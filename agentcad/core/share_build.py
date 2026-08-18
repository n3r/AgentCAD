"""The publish pin and the muzzled build service behind a share link.

Two jobs, both about isolation:

1. **Pin a part at a ref by copying its bytes out of the project.** At publish
   the ref is resolved to an immutable commit, the part's script (and its
   material, from the manifest at that commit) is read with a ``cat-file blob``
   — no worktree — content-addressed (PRD-011 sha256), and written into
   ``<state-dir>/publications/``. A later ``write_script`` on the owner's
   working part changes neither the stored commit nor the copied bytes, so a
   live link never drifts (design Decision 3, PRD-007 AC8).

2. **Build variants against that copy, never against a user project.** A single
   long-lived, **muzzled** :class:`AgentCADService` is rooted at
   ``<state-dir>/publications/build/`` and built with the PRD-004
   ``checks._ephemeral_service`` recipe (``bus.on_publish=None``,
   ``store.branch_resolver=None``, ``store.write_guard=None`` — the last two
   *after* ``build_registry``). It shares the main kernel pool, but the in-flight
   cap reserves at least one worker for members (see ``effective_max_inflight``).
   Because the build store is under the state dir and outside every user project,
   the isolation is doubled: even without the muzzles a build here could not
   reach a user repo, and with them it writes only its own content-addressed
   ``.cache/``.

The build project is content-addressed: one project named for the script sha,
one part called ``part``. Every build runs a *derived* record
(``dataclasses.replace`` with the visitor's params and the pinned material), so
the stored part's own material never matters and the ``_build_with`` cache key
carries params + density exactly as the authoring path's does. The viewer's
``mesh``/``model``/``params`` reads are pure file reads of the sidecars this
module writes — **no kernel** — which is what makes "a viewer link reaches zero
kernel" true (design Decision 7).

The capped, rate-limited public ``build_variant``/``export_variant`` and the
in-flight semaphore are a LATER slice; the object model here is shaped for them
(the record's ``customizer`` flag, the content-addressed build) but this slice
ships only the pin, the default-variant warm, and the kernel-free read helpers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ..kernel import paramclamp
from .materials import DEFAULT_MATERIAL
from .model import (
    AuthError,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    ValidationError,
)
from .publications import PublicationStore
from .ratelimit import TokenBucket

#: The one part every content-addressed build project holds. The owner's part
#: id never becomes a build-project name (two parts with identical bytes are one
#: build), so a fixed id keeps ``read_script`` deterministic.
BUILD_PART = "part"

#: The affinity prefix that routes visitor builds by consistent hash onto a
#: warm worker (``kernel/pool.py`` ``_pick`` — ``hash(affinity) % size``). This
#: is cache-warmth routing, NOT isolation: it does not fence anonymous builds
#: off members' workers (two affinities can hash to the same worker, and on a
#: small pool they collide often). The real containment wall is the
#: ``pool_size - 1`` worker reservation in :func:`effective_max_inflight`.
SHARE_AFFINITY = "share"

# --------------------------------------------------------- the in-flight cap
#
# A **global** ``BoundedSemaphore`` around the *build call only* is what keeps a
# customizer flood from starving signed-in members of workers (design Decision
# 4). Two rules make it real containment rather than a slogan:
#
#   1. It is acquired NON-BLOCKING: a refusal is a ``quota_exceeded`` the page
#      degrades on, never a queue that ties up a request thread.
#   2. Its effective size is CLAMPED to leave at least one worker free for
#      members: ``min(SHARE_MAX_INFLIGHT, max(0, pool_size - 1))``. On a
#      single-worker pool the clamp is 0 and the customizer refuses cleanly
#      (a 503 naming ``AGENTCAD_KERNEL_POOL_SIZE``) rather than occupying the
#      members' sole worker. This — not the ``affinity=`` routing, which is
#      consistent-hash cache-warmth and does NOT segregate — is the wall.
#
# The operator ceiling is read from the environment on every call so it (and a
# test) can move without a restart; the semaphore is rebuilt only when the
# effective size actually changes, so within one setting it is the one shared
# object the whole process contends on.
ENV_MAX_INFLIGHT = "AGENTCAD_SHARE_MAX_INFLIGHT"

#: Conservative by design (design "Risks"): customizer responsiveness traded
#: against member starvation. The operator's ceiling; the effective cap is this
#: clamped by the ``pool_size - 1`` worker reservation.
DEFAULT_MAX_INFLIGHT = 2

#: A refused build is retryable almost immediately — a slot frees the moment any
#: in-flight build returns; unlike the token buckets this is not a refill delay.
SHARE_BUSY_RETRY_AFTER_S = 1

#: The customizer needs at least this many kernel workers: one to build on and
#: one left free for members. Below it, a variant/download request refuses.
MIN_CUSTOMIZER_POOL_SIZE = 2

_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict = {"size": None, "sem": None}

#: A query-string integer, so an enum choice declared ``3`` matches a ``"3"``.
_INT_RE = re.compile(r"^[+-]?\d+$")


def _configured_max_inflight() -> int:
    """The operator's requested ceiling (``ENV_MAX_INFLIGHT`` or the default),
    floored at 1 — before the pool reservation clamps it."""
    raw = os.environ.get(ENV_MAX_INFLIGHT)
    if raw is None:
        return DEFAULT_MAX_INFLIGHT
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_INFLIGHT


def _pool_size() -> int:
    """The kernel pool size, read from the SAME source the pool is built from
    (``config.get_kernel_pool_size`` — env ``AGENTCAD_KERNEL_POOL_SIZE`` /
    config / a cores-derived default), so the reservation below matches the
    real worker count in a server process."""
    from ..config import get_kernel_pool_size
    try:
        return max(1, int(get_kernel_pool_size()))
    except Exception:                            # noqa: BLE001 — never crash a build
        return 1


def effective_max_inflight() -> int:
    """Concurrent anonymous builds actually permitted: the operator ceiling
    clamped to leave one worker free for members (``pool_size - 1``).

    Zero on a single-worker pool — the customizer cannot run there without
    starving members, so :meth:`ShareBuilder.build_variant` /
    :meth:`ShareBuilder.export_variant` refuse with a 503 instead of building.
    """
    return min(_configured_max_inflight(), max(0, _pool_size() - 1))


def require_customizer_capacity() -> None:
    """Refuse (503) a variant/download build when no worker can be spared.

    The containment wall for finding M-1: on a single-worker pool an anonymous
    build would occupy the members' only worker, so it is refused with a clear,
    structured message naming the knob to fix — never a build."""
    if effective_max_inflight() <= 0:
        raise ServiceUnavailableError(
            f"the customizer needs a kernel pool of at least "
            f"{MIN_CUSTOMIZER_POOL_SIZE}; this instance has {_pool_size()} "
            f"worker(s). Set AGENTCAD_KERNEL_POOL_SIZE>="
            f"{MIN_CUSTOMIZER_POOL_SIZE} to enable variant builds "
            f"(viewer links are unaffected).",
            {"kernel_pool_size": _pool_size(),
             "required_kernel_pool_size": MIN_CUSTOMIZER_POOL_SIZE})


def inflight_semaphore() -> threading.BoundedSemaphore:
    """The process-global build slot, sized by :func:`effective_max_inflight`
    (the operator ceiling clamped by the ``pool_size - 1`` reservation).

    Rebuilt only when the effective size changes, so every concurrent caller
    contends on the same object — which is the whole point of a *global* cap.
    A size of 0 is legal (a single-worker pool); callers gate on
    :func:`require_customizer_capacity` before ever acquiring it.
    """
    size = effective_max_inflight()
    with _INFLIGHT_LOCK:
        if _INFLIGHT["sem"] is None or _INFLIGHT["size"] != size:
            _INFLIGHT["sem"] = threading.BoundedSemaphore(size)
            _INFLIGHT["size"] = size
        return _INFLIGHT["sem"]


@contextmanager
def _inflight_slot():
    """Hold a global build slot for the duration of one kernel-reaching build,
    or refuse with ``quota_exceeded`` when the cap is already full."""
    sem = inflight_semaphore()
    if not sem.acquire(blocking=False):
        raise RateLimitedError(
            "the customizer is busy; please retry shortly",
            {"retry_after_s": SHARE_BUSY_RETRY_AFTER_S})
    try:
        yield
    finally:
        sem.release()


def _try_number(value: str):
    text = value.strip()
    try:
        return int(text) if _INT_RE.match(text) else float(text)
    except ValueError:
        return None


def _coerce_query_value(entry: dict, value):
    """A query string is all text; coerce one value to the type its spec entry
    declares so the authoring-path validator can judge it. A value that will
    not coerce is passed through UNCHANGED — ``normalize_params`` then refuses
    it, so a bad value is one refusal, never a swallowed coercion."""
    if not isinstance(value, str):
        return value
    ptype = entry.get("type") or "number"
    if ptype == "string":
        return value
    if ptype == "bool":
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        return value                       # normalize_params rejects a non-bool
    if ptype == "enum":
        # An enum choice may be declared as a string OR a number. A raw string
        # that IS a declared choice must win over its numeric coercion, or a
        # choice like "1"/"2" (strings that look numeric) becomes unselectable
        # (m-3): the query "1" would coerce to int 1 and fail to match "1".
        choices = entry.get("choices") or []
        if value in choices:
            return value
        num = _try_number(value)
        if num is not None and num in choices:
            return num
        return value                       # normalize_params refuses a non-member
    num = _try_number(value)               # number / int
    return num if num is not None else value


def _coerce_query_params(spec: dict, raw: dict) -> dict:
    """Coerce a raw query dict against *spec*. An unknown name is left as-is so
    ``normalize_params`` raises the same ``unknown parameter(s)`` it does for
    the authoring path — the negation ``an out-of-spec param never reaches
    build`` proves."""
    return {name: (_coerce_query_value(spec[name], value) if name in spec
                   else value)
            for name, value in raw.items()}


def _clamp_params(spec: dict, values: dict) -> tuple[dict, list[str]]:
    """Range-clamp already-normalized numeric params against *spec*, returning
    ``(clamped, warnings)`` — the worker's clamp, applied server-side BEFORE the
    variant cache key is computed so clamp-equal requests coalesce (M-2).

    Uses the shared pure ``paramclamp`` helper, so parity with the worker is
    structural, not copied. A non-finite ``NaN`` (which the clamp comparisons
    silently pass) is refused here as a ``validation_error`` rather than reaching
    the kernel (m-2); ``inf`` is left to clamp to max, as the worker does."""
    clamped = dict(values)
    warnings: list[str] = []
    for name, value in values.items():
        entry = spec.get(name)
        if not entry:
            continue
        ptype = entry.get("type") or "number"
        if ptype not in paramclamp.NUMERIC_TYPES:
            continue
        if paramclamp.is_nan(value):
            raise ValidationError(
                f"parameter {name!r} must be a finite number",
                {"param": name})
        clamped[name] = paramclamp.clamp_numeric(entry, value, name, ptype,
                                                 warnings)
    return clamped, warnings


def script_sha_for(script_bytes: bytes) -> str:
    """The content-address of a script's raw bytes — the pin identity, computed
    WITHOUT registering a build project.

    A pure read helper for the market mesh route (PRD-031a slice 4): that route
    must resolve the cached ``.acm`` for a variant *already built*, keyed by the
    same ``script_sha`` :meth:`ensure_catalog_pin` derives, but it must **never**
    build or even register a project — so it needs the sha alone, not a pin. The
    raw bytes are hashed newline-preserving, exactly as :meth:`pin` and
    :meth:`ensure_catalog_pin` do, so the three agree byte-for-byte."""
    return "sha256:" + hashlib.sha256(script_bytes).hexdigest()


def _proj_name(script_sha: str) -> str:
    """A ``validate_id``-legal project name for a content-addressed build.

    ``[a-z][a-z0-9_]{0,39}`` — so the 64-hex digest cannot be the name. ``s`` +
    38 hex is 39 chars and 152 bits, collision-proof for this purpose."""
    hexpart = script_sha.split(":")[-1]
    return "s" + hexpart[:38]


# ---------------------------------------------- the shared per-IP customizer guard
#
# PRD-031a opens a SECOND anonymous kernel path (the market listing customizer)
# beside PRD-007's `/s/`. The process-global in-flight semaphore above is the
# real wall and is shared for free. The two per-*app* token buckets in
# `routes_share_public.py` were not: if `routes_market.py` minted its own per-IP
# bucket, one visitor would get DOUBLE the per-address allowance (one bucket per
# surface). The fix is to move the per-IP bucket + the hourly login gate into one
# `CustomizerGuard` installed once on `service.customizer_guard` (by
# `ensure_share`) and read by BOTH surfaces — so a visitor's per-address rate is
# one shared object, asserted by an identity test (PRD-031a AC4). The per-*link*
# / per-*listing* bucket stays route-local (keyed differently), because a popular
# listing and a popular share link are genuinely different subjects.

#: 0.5/s with a burst of 15: the page feels live while a slider is dragged, then a
#: sustained hammer throttles to one rebuild every two seconds (PRD-007 Decision 4).
SHARE_RATE_PER_S = 0.5
SHARE_BURST = 15.0
SHARE_RETRY_AFTER_S = max(1, math.ceil(1.0 / SHARE_RATE_PER_S))

#: The pre-006 backstop, OFF by default (founder decision): above N anonymous
#: rebuilds/hour from one address, a customizer rebuild requires a session. The
#: SAME knob and counter for `/s/` and `/market` (design Decision, founder
#: recommendation 3) — a visitor cannot get two hourly budgets.
ENV_REQUIRE_LOGIN_ABOVE = "AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE"
_GATE_WINDOW_S = 3600
_GATE_MAX_ADDRS = 8192


class _HourlyCounter:
    """Best-effort per-address hits in a sliding hour, for the login gate.

    Bounded: an address whose window has fully drained carries no information,
    so it is pruned; a runaway id space is capped at :data:`_GATE_MAX_ADDRS`.
    Not a correctness invariant — a gate that occasionally under-counts under a
    process restart is the accepted cost of keeping it in memory, not the store.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def hit(self, addr: str, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            times = [t for t in self._hits.get(addr, ())
                     if now - t < _GATE_WINDOW_S]
            times.append(now)
            self._hits[addr] = times
            if len(self._hits) > _GATE_MAX_ADDRS:
                self._hits = {a: ts for a, ts in self._hits.items()
                              if ts and now - ts[-1] < _GATE_WINDOW_S}
            return len(times)


class CustomizerGuard:
    """The per-IP rate bucket + hourly login gate every anonymous customizer
    surface shares. Installed once on ``service.customizer_guard`` by
    :func:`ensure_share`; both ``routes_share_public`` (``/s/``) and
    ``routes_market`` (``/market``) call this ONE object, so a visitor's per-IP
    allowance and hourly count are not doubled across the two surfaces.

    Only the parts that must not be duplicated per surface live here. The
    per-*link* / per-*listing* bucket stays route-local (keyed differently:
    ``share:<pub_id>`` vs ``catalog:<name>@<version>/<part>``), because that
    bucket shapes ONE subject's rate and there is no double-allowance to close.
    """

    def __init__(self) -> None:
        #: The per-ADDRESS bucket, shared across surfaces (the whole point).
        self.addr_rate = TokenBucket(rate=SHARE_RATE_PER_S, burst=SHARE_BURST)
        #: The hourly per-address counter behind the login gate, also shared.
        self.login_gate = _HourlyCounter()

    @staticmethod
    def client_host(request) -> str:
        """The address the proxy layer resolved (``request.client.host``), NOT a
        header the visitor controls — the PRD-005a M3 discipline (a forged
        ``X-Forwarded-For`` must not mint a fresh bucket per request)."""
        return (request.client.host if request.client else "?") or "?"

    def throttle(self, addr: str) -> None:
        """Take the shared per-IP bucket; over-limit is a ``quota_exceeded`` the
        page degrades to view-only on — never a red error."""
        if not self.addr_rate.take(f"addr:{addr}"):
            raise RateLimitedError(
                "too many customizer rebuilds; the page will retry shortly",
                {"retry_after_s": SHARE_RETRY_AFTER_S})

    def gate(self, addr: str, *, authenticated: bool) -> None:
        """The optional login-above-N backstop. Off unless
        ``AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE`` is set; then an anonymous address
        past the hourly threshold must sign in (401). A signed-in member is
        never gated. ``authenticated`` is the caller's principal check, passed in
        so this stays server-import-free (core does not read the request
        principal itself)."""
        raw = os.environ.get(ENV_REQUIRE_LOGIN_ABOVE)
        if not raw:
            return
        try:
            threshold = int(raw)
        except (TypeError, ValueError):
            return                              # a nonsense knob is no gate
        if authenticated:
            return
        if self.login_gate.hit(addr) > threshold:
            raise AuthError(
                "sign in to keep customizing this link (this link is under a "
                "high rebuild rate from your network)")


class ShareBuilder:
    """Pins parts into the publication store and builds variants against a
    muzzled service rooted there. Constructed once per process, lazily."""

    def __init__(self, store: PublicationStore, kernel) -> None:
        self._store = store
        self._kernel = kernel
        self._service = None
        self._lock = threading.Lock()

    # --------------------------------------------------------- muzzled service

    def _svc(self):
        """The muzzled build service, constructed on first use.

        The three nullings are the ``_ephemeral_service`` recipe, and the last
        two are only meaningful *after* ``build_registry`` (the versioning pack
        is what installs a ``branch_resolver`` and a ``write_guard``)."""
        with self._lock:
            if self._service is None:
                from .service import AgentCADService, EventBus
                from .tools import build_registry

                root = self._store.build_root()
                root.mkdir(parents=True, exist_ok=True)
                svc = AgentCADService(Path(root).resolve(), self._kernel, EventBus())
                svc.bus.on_publish = None            # no history snapshots
                build_registry(svc)
                svc.store.branch_resolver = None     # after build_registry
                svc.store.write_guard = None         # after build_registry
                self._service = svc
            return self._service

    def _ensure_project(self, script_sha: str, script_text: str,
                        material: str) -> str:
        """Register the content-addressed build project + part, idempotently.

        Returns the build-project name. Safe to call for a sha already pinned
        (a second publication of identical bytes shares the build)."""
        from .model import ConflictError

        svc = self._svc()
        proj = _proj_name(script_sha)
        try:
            svc.store.create(proj)
        except ConflictError:
            pass
        try:
            svc.store.add_part(proj, BUILD_PART, BUILD_PART, material,
                               script_text, kind="script")
        except ConflictError:
            pass                                     # already added
        except ValidationError:
            # A project-custom material is not defined in the content-addressed
            # build project (Phase 2 copies the materials section); fall back to
            # the default so the pin still succeeds. Density then differs from
            # the owner's custom material — noted as a Phase-2 residual.
            try:
                svc.store.add_part(proj, BUILD_PART, BUILD_PART,
                                   DEFAULT_MATERIAL, script_text, kind="script")
            except ConflictError:
                pass
        return proj

    # ----------------------------------------------------------------- pin

    def pin(self, service, project: str, part_id: str,
            ref: str | None) -> dict:
        """Resolve *ref* to a commit, copy the part's bytes out, warm the
        default variant. Returns ``{script_sha, ref, material, part_id,
        default_variant_key}``. **Never writes ``service``'s store.**"""
        canonical = service.store.canonical_path_of(project)
        ref_dict = self._resolve_pin(service, project, canonical, ref)
        commit = ref_dict["commit"]

        script_bytes = self._blob(service, canonical, commit,
                                  f"parts/{part_id}.py")
        if script_bytes is None:
            raise NotFoundError(
                f"part {part_id!r} not found at {ref_dict['name']!r} in "
                f"project {project!r}", {"part_id": part_id, "ref": ref})
        try:
            script_text = script_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                f"part {part_id!r} script is not valid UTF-8", {}) from exc

        material, params = self._pinned_material_and_params(
            service, canonical, commit, part_id)

        # Content-address the RAW bytes (newline-preserving), so the pin is
        # byte-exact and two identical parts share a build.
        script_sha = "sha256:" + hashlib.sha256(script_bytes).hexdigest()
        script_file = self._store.script_path(script_sha)
        script_file.parent.mkdir(parents=True, exist_ok=True)
        if not script_file.exists():
            script_file.write_bytes(script_bytes)

        proj = self._ensure_project(script_sha, script_text, material)
        svc = self._svc()

        # Cache the inspected params spec ONCE (a kernel call here, on the
        # authenticated publish path) so the viewer's /params is a file read.
        self._write_spec(script_sha, svc._params_spec(script_text))

        # Warm the default variant at the pinned params/material.
        result = self._build(proj, script_sha, material, params)
        if not result["ok"]:
            raise ValidationError(
                f"the pinned part failed to build: "
                f"{result.get('error', {}).get('message', 'build failed')}",
                {"ref": ref_dict["name"]})
        return {
            "script_sha": script_sha,
            "ref": ref_dict,
            "material": material,
            "part_id": part_id,
            "default_variant_key": result["cache_key"],
        }

    def _resolve_pin(self, service, project: str, canonical: Path,
                     ref: str | None) -> dict:
        """``{kind, name, commit}`` for the pin.

        The PRD-001 discipline (``checks._resolve_ref``): a tag resolves as a
        tag, a branch is auto-tagged into an immutable version, and ``git
        rev-parse``'s tags-before-heads order is never trusted. A ref that is
        neither is a ``not_found``."""
        history = service.history
        if not history.available() or not history._has_repo(canonical):
            # No history yet: tag the current head so the pin is immutable.
            return self._autotag(service, project, canonical)
        if ref:
            tag_commit = history.resolve_tag(canonical, ref)
            if tag_commit:
                return {"kind": "tag", "name": ref, "commit": tag_commit}
            if history.resolve_branch(canonical, ref):
                # A branch ref without a tag: auto-tag the current head and pin
                # the version (design Decision 3).
                return self._autotag(service, project, canonical)
            raise NotFoundError(
                f"ref {ref!r} is neither a version nor a branch in project "
                f"{project!r}", {"ref": ref})
        return self._autotag(service, project, canonical)

    def _autotag(self, service, project: str, canonical: Path) -> dict:
        """Tag the current head with a generated version name and return it."""
        branches = getattr(service, "branches", None)
        if branches is None:
            raise ValidationError(
                "publishing needs the versioning layer (git on PATH)", {})
        name = "share-" + secrets.token_hex(4)
        branches.tag(project, name)
        commit = service.history.resolve_tag(canonical, name)
        return {"kind": "tag", "name": name, "commit": commit}

    # ------------------------------------------------------------- blob reads

    @staticmethod
    def _blob(service, canonical: Path, commit: str, path: str) -> bytes | None:
        """The bytes of one tracked file at a commit — no worktree, the
        ``packet._blob`` primitive."""
        result = service.history._run_bytes(
            canonical, "cat-file", "blob", f"{commit}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    def _pinned_material_and_params(self, service, canonical: Path,
                                    commit: str, part_id: str
                                    ) -> tuple[str, dict]:
        """The part's material and stored params from the manifest AT the
        commit — so an owner who later edits either does not move a live link."""
        raw = self._blob(service, canonical, commit, "project.json")
        if raw is None:
            return DEFAULT_MATERIAL, {}
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return DEFAULT_MATERIAL, {}
        for entry in manifest.get("parts", []):
            if entry.get("id") == part_id:
                return (entry.get("material", DEFAULT_MATERIAL),
                        dict(entry.get("params") or {}))
        return DEFAULT_MATERIAL, {}

    # --------------------------------------------------------------- building

    def _build(self, proj: str, script_sha: str, material: str,
               params: dict) -> dict:
        """Build one variant on the muzzled service — the ``_build_with`` path
        with a derived record, ``status_key=None`` (no badge, no state).

        The base part's stored material is irrelevant: the record is derived
        with the pinned material, so the density (and therefore the cache key)
        is the pinned one."""
        svc = self._svc()
        base = svc.store.get_part(proj, BUILD_PART)
        record = dataclasses.replace(base, params=dict(params),
                                     material=material)
        return svc._build_with(
            proj, record, affinity=f"{SHARE_AFFINITY}:{_proj_name(script_sha)}",
            status_key=None)

    def _variant_cache_key(self, proj: str, material: str,
                           params: dict) -> str:
        """The content-addressed key a build of *params* WOULD produce, computed
        without building — so a repeat visitor is a pure disk read that takes no
        in-flight slot and makes zero kernel calls (AC2)."""
        svc = self._svc()
        base = svc.store.get_part(proj, BUILD_PART)
        derived = dataclasses.replace(base, params=dict(params),
                                      material=material)
        return svc._cache_key_for(proj, derived)

    def _validate(self, script_sha: str, material: str | None, spec: dict | None,
                  raw: dict) -> tuple[str, str, dict, list[str]]:
        """``(proj, material, clamped, warnings)`` for a variant request, or raise.

        The spec-taking core shared by the share-link path (spec from the pin's
        cached sidecar) and the catalog path (spec from the pre-generated index
        digest). Validation is the AUTHORING path's own logic, reused not forked:
        ``normalize_params`` rejects wrong types, non-member enum choices and
        unknown names BEFORE any build. The out-of-range clamp is then applied
        **here, server-side**, using the SAME pure ``paramclamp`` helper the
        worker uses — so two out-of-range values that clamp to identical
        geometry produce identical params, one cache key and one build (PRD-007
        review finding M-2). The visitor supplies DATA, never code
        (PRD-005a Decision 2)."""
        spec = spec or {}
        coerced = _coerce_query_params(spec, raw)
        normalized = self._svc().normalize_params(spec, coerced)
        clamped, warnings = _clamp_params(spec, normalized)
        return (_proj_name(script_sha), material or DEFAULT_MATERIAL,
                clamped, warnings)

    def _validated_params(self, record: dict,
                          raw: dict) -> tuple[str, str, dict, list[str]]:
        """The share-link adaptor over :meth:`_validate`: the pin's cached spec
        sidecar supplies the spec. Behaviour-preserving for PRD-007."""
        script_sha = record["script_sha"]
        return self._validate(script_sha, record.get("material"),
                              self.params_spec(script_sha), raw)

    # ----------------------------------------------------- the shared build core

    def _variant(self, script_sha: str, material: str | None, spec: dict | None,
                 raw: dict) -> dict:
        """Validate → clamp → cache-probe → in-flight build, for ANY pinned
        script. The single containment tail both :meth:`build_variant` (share
        links) and :meth:`build_catalog_variant` (the market listing customizer)
        run through — one wall, not two.

        **Probe the cache** (a repeat is a free disk read — no slot, no kernel),
        then take a **global in-flight slot** around the build only. A failed
        build is a ``validation_error`` — a param set the author's script cannot
        realise. Server-side clamp warnings ride back on both paths."""
        proj, material, clamped, warnings = self._validate(
            script_sha, material, spec, raw)
        key = self._variant_cache_key(proj, material, clamped)
        if self.mesh_path(script_sha, key) is not None:
            sidecar = self.metrics_for(script_sha, key) or {}
            return {"mesh_key": key, "metrics": sidecar.get("metrics"),
                    "warnings": warnings + list(sidecar.get("warnings", [])),
                    "lods": sidecar.get("lods", []), "ok": True, "cached": True}

        with _inflight_slot():
            result = self._build(proj, script_sha, material, clamped)
        if not result["ok"]:
            raise ValidationError(
                result.get("error", {}).get("message", "variant build failed"),
                {"params": clamped})
        return {"mesh_key": result["cache_key"],
                "metrics": result.get("metrics"),
                "warnings": warnings + list(result.get("warnings", [])),
                "lods": result.get("lods", []), "ok": True, "cached": False}

    def _export(self, script_sha: str, material: str | None, spec: dict | None,
                raw: dict, fmt: str) -> Path:
        """The shared export tail: same param parity and in-flight cap as
        :meth:`_variant`, content-addressed so a repeat is a disk read. The
        caller checks the export mask before this runs."""
        from .service import EXPORT_TOLERANCE

        proj, material, clamped, _warnings = self._validate(
            script_sha, material, spec, raw)
        svc = self._svc()
        key = self._variant_cache_key(proj, material, clamped)
        out = svc.store.exports_dir(proj) / f"{key}.{fmt}"
        if out.is_file():
            return out                     # content-addressed: a cache read

        out.parent.mkdir(parents=True, exist_ok=True)
        script = svc.store.read_script(proj, BUILD_PART)
        with _inflight_slot():
            result = svc.kernel.request(
                "export",
                {"script": script, "params": dict(clamped), "format": fmt,
                 "out_path": str(out), "tolerance": EXPORT_TOLERANCE},
                timeout_s=300.0)
        if not result.get("ok", True):
            raise ValidationError("variant export failed", {"format": fmt})
        return out

    # --------------------------------------------------- public builds (/s/)

    def build_variant(self, pub_id: str, params: dict) -> dict:
        """Build one share-link visitor variant, bounded. Returns ``{mesh_key,
        metrics, warnings, lods, ok, cached}``.

        The order is the containment: **refuse if no worker can be spared**
        (single-worker pool → 503, finding M-1), then the shared
        :meth:`_variant` tail (validate + clamp + cache + global in-flight slot).
        """
        require_customizer_capacity()
        record = self._store.get(pub_id)
        if record is None:
            raise NotFoundError("no such share link")
        return self._variant(record["script_sha"], record.get("material"),
                             self.params_spec(record["script_sha"]), params)

    def export_variant(self, pub_id: str, params: dict, fmt: str) -> Path:
        """Export one share-link visitor variant. Same containment as
        :meth:`build_variant`; the caller checks the export mask first."""
        require_customizer_capacity()
        record = self._store.get(pub_id)
        if record is None:
            raise NotFoundError("no such share link")
        return self._export(record["script_sha"], record.get("material"),
                            self.params_spec(record["script_sha"]), params, fmt)

    # ------------------------------------- catalog builds (/market, PRD-031a)

    def ensure_catalog_pin(self, script_bytes: bytes,
                           material: str | None) -> dict:
        """Pin a catalog part's script bytes into the content-addressed build
        project, idempotently. Returns ``{script_sha}``.

        The catalog version's ``content_id`` (in ``index.json``) is already the
        immutable pin, so — unlike :meth:`pin` — this mints **no** ``Publication``
        and makes **no** ``_params_spec`` kernel call: the param spec comes from
        the pre-generated index digest, supplied to :meth:`build_catalog_variant`
        by the route. Two listings with byte-identical scripts share one build.
        """
        try:
            script_text = script_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                "catalog part script is not valid UTF-8", {}) from exc
        script_sha = script_sha_for(script_bytes)
        script_file = self._store.script_path(script_sha)
        script_file.parent.mkdir(parents=True, exist_ok=True)
        if not script_file.exists():
            script_file.write_bytes(script_bytes)
        self._ensure_project(script_sha, script_text,
                             material or DEFAULT_MATERIAL)
        return {"script_sha": script_sha}

    def build_catalog_variant(self, script_sha: str, material: str | None,
                              spec: dict | None, params: dict) -> dict:
        """The market listing customizer's ONE kernel path. Same containment as
        :meth:`build_variant` — the pool-reservation 503, the shared in-flight
        semaphore, param parity, the clamp-before-cache and the content-addressed
        variant cache — reached through the SAME :meth:`_variant` tail. The
        caller must :meth:`ensure_catalog_pin` first; ``spec`` is the index
        digest's param list keyed by name."""
        require_customizer_capacity()
        return self._variant(script_sha, material, spec, params)

    def export_catalog_variant(self, script_sha: str, material: str | None,
                               spec: dict | None, params: dict,
                               fmt: str) -> Path:
        """A market variant export. Same caps as :meth:`build_catalog_variant`;
        the caller checks the fixed export mask before this runs."""
        require_customizer_capacity()
        return self._export(script_sha, material, spec, params, fmt)

    # ------------------------------------------------- kernel-free read helpers

    def mesh_path(self, script_sha: str, cache_key: str) -> Path | None:
        """The ``.acm`` bytes for a key **already in the cache**, or ``None``.

        Never builds — the ``routes_configs.get_mesh_by_key`` 404-if-absent
        discipline. Computed straight from the store layout, so it answers with
        no service registration (a restart-safe, kernel-free read)."""
        if not _is_cache_key(cache_key):
            return None
        path = (self._store.build_root() / _proj_name(script_sha) / ".cache"
                / f"{cache_key}.acm")
        return path if path.is_file() else None

    def metrics_for(self, script_sha: str, cache_key: str) -> dict | None:
        """The ``{metrics, warnings, lods}`` sidecar for a cached key, or
        ``None``. A pure JSON file read — no kernel."""
        if not _is_cache_key(cache_key):
            return None
        path = (self._store.build_root() / _proj_name(script_sha) / ".cache"
                / f"{cache_key}.metrics.json")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def params_spec(self, script_sha: str) -> dict | None:
        """The inspected slider spec, from the sidecar cached at pin. No kernel."""
        try:
            return json.loads(self._spec_path(script_sha).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def script_text(self, script_sha: str) -> str | None:
        """The pinned script bytes as text, for a ``show_script`` link."""
        try:
            return self._store.script_path(script_sha).read_text("utf-8")
        except (OSError, ValueError):
            return None

    # ----------------------------------------------------------------- spec io

    def _spec_path(self, script_sha: str) -> Path:
        return (self._store.build_root() / _proj_name(script_sha)
                / "params_spec.json")

    def _write_spec(self, script_sha: str, spec: dict | None) -> None:
        path = self._spec_path(script_sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        tmp.write_text(json.dumps(spec, indent=2, sort_keys=True), "utf-8")
        tmp.replace(path)


def _is_cache_key(value: object) -> bool:
    """A cache key is a hex sha with no path separators — a hard gate before it
    is ever joined to a directory (no ``..``, no absolute path can slip in)."""
    return (isinstance(value, str) and 0 < len(value) <= 128
            and all(c in "0123456789abcdef" for c in value))


def ensure_share(service):
    """Install ``service.publications`` + ``service.share_builder`` +
    ``service.customizer_guard`` once.

    Called from ``routes_share*.build_router`` and ``routes_market`` — never
    from ``AgentCADService.__init__`` — so the store is constructed only in a
    server process and PRD-004/011 ephemeral services stay unaffected by
    construction. The store lives under ``appmode.state_dir()``, never
    ``--projects-dir``.

    ``customizer_guard`` is the ONE per-IP bucket + hourly login gate every
    anonymous customizer surface shares (PRD-031a AC4): both ``/s/`` and
    ``/market`` read ``service.customizer_guard``, so a visitor cannot double
    their per-address allowance by hitting both. Installed independently of the
    builder's early-return so a service that somehow has one but not the other
    still gets the guard."""
    from .appmode import state_dir

    if not isinstance(getattr(service, "customizer_guard", None),
                      CustomizerGuard):
        service.customizer_guard = CustomizerGuard()
    found = getattr(service, "share_builder", None)
    if isinstance(found, ShareBuilder):
        return found
    store = PublicationStore(state_dir() / "publications")
    service.publications = store
    service.share_builder = ShareBuilder(store, service.kernel)
    return service.share_builder
