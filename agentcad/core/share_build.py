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
   *after* ``build_registry``). It shares the main kernel pool. Because the
   build store is under the state dir and outside every user project, the
   isolation is doubled: even without the muzzles a build here could not reach a
   user repo, and with them it writes only its own content-addressed ``.cache/``.

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
import os
import re
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path

from .materials import DEFAULT_MATERIAL
from .model import NotFoundError, RateLimitedError, ValidationError
from .publications import PublicationStore

#: The one part every content-addressed build project holds. The owner's part
#: id never becomes a build-project name (two parts with identical bytes are one
#: build), so a fixed id keeps ``read_script`` deterministic.
BUILD_PART = "part"

#: The affinity prefix that routes visitor builds onto a segregated pool slice,
#: so a customizer flood cannot poison a member's worker (design Decision 4).
SHARE_AFFINITY = "share"

# --------------------------------------------------------- the in-flight cap
#
# A **global** ``BoundedSemaphore`` around the *build call only*, sized BELOW
# the kernel pool, is what keeps a customizer flood from starving signed-in
# members of workers (design Decision 4). It is acquired NON-BLOCKING: a
# refusal is a ``quota_exceeded`` the page degrades on, never a queue that ties
# up a request thread. The segregation ``affinity="share:<pub>"`` is the second
# layer — even the ``SHARE_MAX_INFLIGHT`` builds that DO run route onto a
# bounded pool slice, off the members' workers.
#
# The size is read from the environment on every call so an operator (and a
# test) can move it without a restart; the semaphore is rebuilt only when the
# size actually changes, so within one setting it is the one shared object the
# whole process contends on.
ENV_MAX_INFLIGHT = "AGENTCAD_SHARE_MAX_INFLIGHT"

#: Conservative by design (design "Risks"): customizer responsiveness traded
#: against member starvation, with the affinity segregation as the second wall.
DEFAULT_MAX_INFLIGHT = 2

#: A refused build is retryable almost immediately — a slot frees the moment any
#: in-flight build returns; unlike the token buckets this is not a refill delay.
SHARE_BUSY_RETRY_AFTER_S = 1

_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict = {"size": None, "sem": None}

#: A query-string integer, so an enum choice declared ``3`` matches a ``"3"``.
_INT_RE = re.compile(r"^[+-]?\d+$")


def _configured_max_inflight() -> int:
    raw = os.environ.get(ENV_MAX_INFLIGHT)
    if raw is None:
        return DEFAULT_MAX_INFLIGHT
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_INFLIGHT


def inflight_semaphore() -> threading.BoundedSemaphore:
    """The process-global build slot, sized by :data:`ENV_MAX_INFLIGHT`.

    Rebuilt only when the configured size changes, so every concurrent caller
    contends on the same object — which is the whole point of a *global* cap.
    """
    size = _configured_max_inflight()
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
        num = _try_number(value)
        return num if num is not None else value
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


def _proj_name(script_sha: str) -> str:
    """A ``validate_id``-legal project name for a content-addressed build.

    ``[a-z][a-z0-9_]{0,39}`` — so the 64-hex digest cannot be the name. ``s`` +
    38 hex is 39 chars and 152 bits, collision-proof for this purpose."""
    hexpart = script_sha.split(":")[-1]
    return "s" + hexpart[:38]


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

    def _validated_params(self, record: dict, raw: dict) -> tuple[str, str, dict]:
        """``(proj, material, normalized)`` for a variant request, or raise.

        Validation is the AUTHORING path's own logic, reused not forked:
        ``normalize_params`` rejects wrong types, non-member enum choices and
        unknown names BEFORE any build; the out-of-range clamp-with-warning is
        inherited from ``worker._resolve_params`` inside the build itself. The
        visitor supplies DATA, never code (PRD-005a Decision 2)."""
        script_sha = record["script_sha"]
        material = record.get("material") or DEFAULT_MATERIAL
        spec = self.params_spec(script_sha) or {}
        coerced = _coerce_query_params(spec, raw)
        normalized = self._svc().normalize_params(spec, coerced)
        return _proj_name(script_sha), material, normalized

    # --------------------------------------------------------- public builds

    def build_variant(self, pub_id: str, params: dict) -> dict:
        """Build one visitor variant, bounded. Returns ``{mesh_key, metrics,
        warnings, lods, ok, cached}``.

        The order is the containment: **validate** (reject out-of-spec before a
        build), **probe the cache** (a repeat is a free disk read — no slot, no
        kernel), then take a **global in-flight slot** around the build only. A
        failed build is a ``validation_error`` — a param set the author's script
        cannot realise, not a server fault."""
        record = self._store.get(pub_id)
        if record is None:
            raise NotFoundError("no such share link")
        proj, material, normalized = self._validated_params(record, params)
        script_sha = record["script_sha"]

        key = self._variant_cache_key(proj, material, normalized)
        if self.mesh_path(script_sha, key) is not None:
            sidecar = self.metrics_for(script_sha, key) or {}
            return {"mesh_key": key, "metrics": sidecar.get("metrics"),
                    "warnings": sidecar.get("warnings", []),
                    "lods": sidecar.get("lods", []), "ok": True, "cached": True}

        with _inflight_slot():
            result = self._build(proj, script_sha, material, normalized)
        if not result["ok"]:
            raise ValidationError(
                result.get("error", {}).get("message", "variant build failed"),
                {"params": normalized})
        return {"mesh_key": result["cache_key"],
                "metrics": result.get("metrics"),
                "warnings": result.get("warnings", []),
                "lods": result.get("lods", []), "ok": True, "cached": False}

    def export_variant(self, pub_id: str, params: dict, fmt: str) -> Path:
        """Export one visitor variant to a **content-addressed** file, so a
        repeat download is a disk read (no kernel). Same param parity and the
        same in-flight cap as :meth:`build_variant`; the caller checks the
        export mask before this runs."""
        from .service import EXPORT_TOLERANCE

        record = self._store.get(pub_id)
        if record is None:
            raise NotFoundError("no such share link")
        proj, material, normalized = self._validated_params(record, params)

        svc = self._svc()
        key = self._variant_cache_key(proj, material, normalized)
        out = svc.store.exports_dir(proj) / f"{key}.{fmt}"
        if out.is_file():
            return out                     # content-addressed: a cache read

        out.parent.mkdir(parents=True, exist_ok=True)
        script = svc.store.read_script(proj, BUILD_PART)
        with _inflight_slot():
            result = svc.kernel.request(
                "export",
                {"script": script, "params": dict(normalized), "format": fmt,
                 "out_path": str(out), "tolerance": EXPORT_TOLERANCE},
                timeout_s=300.0)
        if not result.get("ok", True):
            raise ValidationError("variant export failed", {"format": fmt})
        return out

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
    """Install ``service.publications`` + ``service.share_builder`` once.

    Called from ``routes_share*.build_router`` — never from
    ``AgentCADService.__init__`` — so the store is constructed only in a server
    process and PRD-004/011 ephemeral services stay unaffected by construction.
    The store lives under ``appmode.state_dir()``, never ``--projects-dir``."""
    from .appmode import state_dir

    found = getattr(service, "share_builder", None)
    if isinstance(found, ShareBuilder):
        return found
    store = PublicationStore(state_dir() / "publications")
    service.publications = store
    service.share_builder = ShareBuilder(store, service.kernel)
    return service.share_builder
