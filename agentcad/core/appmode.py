"""Deployment mode, the identity state directory, and the bind interlock.

Two modes, and **never a third**. ``local`` is what the product has always
been: a loopback tool with no credential, where the Host allowlist and the
same-origin check in :func:`agentcad.server.app._browser_request_allowed` are
the whole of access control. ``hosted`` is an instance on a network, where
every request resolves to an authenticated principal or to an enumerated
anonymous surface.

**Mode is explicit, never inferred.** Deriving it from "is auth configured"
fails *open* on a typo — an operator misspells a variable, the server decides
it is local, and a public interface serves an unauthenticated API. That is the
one failure direction that must be impossible, so an unrecognised
``AGENTCAD_MODE`` is an error rather than a default (design spec, Decision 3).

**The interlock** is the other half of the same idea:

===================  ==================  ======================
                     loopback bind       non-loopback bind
===================  ==================  ======================
``local``            today's behaviour   **refused**
``hosted``           allowed             allowed
===================  ==================  ======================

You cannot expose an interface without turning authentication on.

A "trusted LAN" mode was considered and rejected: a LAN is a network, not a
trust boundary, and this feature's whole thesis is that an account on a
005-lite instance is a shell (design spec, Decision 1).
"""

from __future__ import annotations

import ipaddress
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..config import config_path

LOCAL = "local"
HOSTED = "hosted"
MODES = (LOCAL, HOSTED)

#: Every spelling of "loopback only" a `--host` argument can carry. Matches
#: ``server.app.LOCAL_HOSTNAMES`` by construction: the same set decides "this
#: Host header is local" there and "this bind is invisible" here, and two
#: copies that drifted would let one say yes while the other said no.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: An *origin* is scheme + authority and nothing else. A path here would be
#: silently dropped from every comparison the guard makes (Host and Origin are
#: both path-free), so it is refused at the door instead.
_ORIGIN_RE = re.compile(r"^https?://[^/?#\s]+$")

#: A session secret shorter than this is a configuration mistake, not a
#: choice. 32 characters is the length of the key this module generates
#: (``secrets.token_bytes(32)`` — 256 bits, the floor for anything that is not
#: allowed to be guessed) so the hand-set and the generated key state the same
#: strength.
MIN_SECRET_CHARS = 32

SECRET_FILE = "secret.key"


class ModeError(Exception):
    """A deployment-configuration refusal. Names the setting at fault.

    Deliberately not an :class:`~agentcad.core.model.AppError`: this is raised
    before an app exists, by ``resolve_mode`` at startup and by ``check_bind``
    in the CLI, and it becomes an exit status rather than an HTTP response.
    """


def state_dir() -> Path:
    """Where identity state lives: ``$AGENTCAD_STATE_DIR`` or
    ``<config-dir>/state``.

    The fallback is ``config.config_path().parent``, which is the derivation
    ``AGENTCAD_PACKAGES_DIR`` and ``AGENTCAD_INDEXES_DIR`` already use
    (``core/packages/cache.py``, ``core/packages/_git.py``) and for the same
    stated reason: the ``AGENTCAD_CONFIG`` override every test already sets
    keeps this out of a real home directory too, so an isolated identity store
    is free in any test that sets it.

    It is deliberately **not** derived from the projects dir. Identity is never
    inside a project, a ``.history`` repo or a manifest, which is why
    ``--projects-dir`` cannot reach it, ``git add -A`` cannot commit it, and
    PRD-004/011's ephemeral services are unaffected by construction.

    Read at call time, not import time, so ``monkeypatch.setenv`` works.
    """
    override = os.environ.get("AGENTCAD_STATE_DIR")
    if override:
        return Path(override)
    return config_path().parent / "state"


def ensure_state_dir() -> Path:
    """:func:`state_dir`, created and **0700**, returned.

    ``mkdir(mode=0o700)`` sets the mode of the *final* component only, and
    only when it actually creates it. Both escapes were real: ``agentcad admin
    user add`` builds ``AuthStore(state_dir()/"auth")`` first, so ``state``
    was born as an intermediate parent at 0755 and the later
    ``mkdir(exist_ok=True)`` in :func:`_secret` left it there — measured 0755
    on a first boot in that order (review finding m2). The directory holds the
    session secret and the password hashes; on a multi-account host 0755 means
    every local account can list it.

    The chmod is best-effort: a bind-mounted volume owned by another uid can
    refuse, and refusing to start over a directory mode would turn a hardening
    step into an outage. The secret file's own check in :func:`_secret` is the
    one that is fatal, and it is unchanged.
    """
    path = state_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            path.chmod(0o700)
    except OSError:
        pass
    return path


#: The immediate peer a hosted instance trusts to have set `X-Forwarded-For`.
#: The docs put the reverse proxy on the same host, so the default is loopback.
DEFAULT_TRUSTED_PROXY = "127.0.0.1"


def trusted_proxy(env: Mapping[str, str] | None = None) -> str:
    """Which immediate peers may set the client's forwarded address.

    Returned verbatim to uvicorn's ``forwarded_allow_ips`` (its
    ``ProxyHeadersMiddleware``): a comma-separated list of IPs / CIDRs, defaulting
    to :data:`DEFAULT_TRUSTED_PROXY` because the deployment guide runs the TLS
    proxy on the same host as the app. uvicorn walks ``X-Forwarded-For`` from the
    right and returns the first entry whose immediate hop is *not* trusted, so
    with exactly one trusted local proxy the resolved address is the real client
    as that proxy saw it, and a client that prepends its own value has it
    overwritten by the proxy's append — it cannot forge the address across a
    single correctly configured hop.

    Trust-everyone is **refused**, and refused by *meaning*, not spelling:
    ``*`` and any prefix-length-zero network (``0.0.0.0/0``, ``::/0``) all tell
    uvicorn to trust every peer, which turns ``X-Forwarded-For`` back into
    attacker-controlled input: any client could then set its own rate-limit
    bucket key (or, later, its audit principal). uvicorn 0.52.1 treats
    ``0.0.0.0/0`` as functionally identical to ``*`` (measured in review m11),
    so catching only the literal ``*`` would leave the footgun loaded. The
    rate-limit key is only as honest as this value, so a config that silently
    re-opens review finding M3 is a refusal, not a warning.
    """
    env = os.environ if env is None else env
    value = (env.get("AGENTCAD_TRUSTED_PROXY") or "").strip()
    if not value:
        return DEFAULT_TRUSTED_PROXY
    for item in (part.strip() for part in value.split(",")):
        if item == "*" or _is_trust_everyone_cidr(item):
            raise ModeError(
                "AGENTCAD_TRUSTED_PROXY must not trust every peer "
                f"(got {item!r}): it lets any client forge its own "
                "X-Forwarded-For, and the login rate limit is keyed on that "
                "address. Name the reverse proxy's address explicitly (an IP "
                "or a bounded CIDR, e.g. 127.0.0.1 or 10.0.0.0/8); leave it "
                "unset to trust the local proxy the deployment guide describes."
            )
    return value


def _is_trust_everyone_cidr(item: str) -> bool:
    """True for a network that covers every address (prefix length 0), the
    CIDR spelling of ``*`` — ``0.0.0.0/0`` and ``::/0``. Anything uvicorn would
    not read as a network (a bare IP, a bounded CIDR, junk) is not our concern
    here: a bare IP trusts one peer, and uvicorn ignores what it cannot parse."""
    if "/" not in item:
        return False
    try:
        return ipaddress.ip_network(item, strict=False).prefixlen == 0
    except ValueError:
        return False


@dataclass(frozen=True)
class AppMode:
    """The resolved deployment mode. Frozen: it is read on every request, and
    a mutable one would be a way to turn hosting off at runtime."""

    name: str
    #: ``https://cad.example.com`` — no trailing slash, no path. ``None`` in
    #: local mode.
    public_origin: str | None
    #: Session secret. Present in hosted mode; ``None`` in local mode.
    secret: bytes | None

    @property
    def hosted(self) -> bool:
        return self.name == HOSTED

    @property
    def origin_host(self) -> str | None:
        """The origin's host with no port — what a ``Host`` header carries.

        A reverse proxy on 443 forwards ``Host: cad.example.com`` for an origin
        written ``https://cad.example.com``, and a container published on 8630
        forwards ``Host: 127.0.0.1:8630`` for ``http://127.0.0.1:8630``. Both
        must compare equal, so the port is dropped on both sides.
        """
        if not self.public_origin:
            return None
        return _hostname(self.public_origin.split("://", 1)[1])

    @property
    def secure_cookies(self) -> bool:
        """``Secure`` on the session cookie iff the public origin is https.

        Not "always true": a ``Secure`` cookie set over plain http is never
        sent back, and the operator sees "login silently does nothing" with no
        error anywhere — which is how a staging box or an air-gapped install
        would first meet this feature.
        """
        return bool(self.public_origin and self.public_origin.startswith("https://"))


def _hostname(authority: str) -> str:
    """``cad.example.com:8443`` -> ``cad.example.com``; ``[::1]:8630`` -> ``[::1]``.

    The bracketed-IPv6 case is why this is not ``split(":")[0]``.
    """
    authority = authority.strip()
    if authority.startswith("["):
        return authority.split("]", 1)[0] + "]"
    return authority.rsplit(":", 1)[0] if ":" in authority else authority


def resolve_mode(env: Mapping[str, str] | None = None) -> AppMode:
    """Read the deployment mode from the environment, or refuse and say why.

    Every refusal names the setting at fault, because the reader is an
    operator staring at a container that will not start.
    """
    env = os.environ if env is None else env
    name = (env.get("AGENTCAD_MODE") or LOCAL).strip().lower()
    if name not in MODES:
        raise ModeError(
            f"AGENTCAD_MODE must be {LOCAL!r} or {HOSTED!r}, not {name!r}. "
            f"It is never inferred: an unrecognised value is refused rather "
            f"than defaulted, because defaulting would fail open."
        )
    if name == LOCAL:
        return AppMode(LOCAL, None, None)

    origin = (env.get("AGENTCAD_PUBLIC_ORIGIN") or "").strip()
    if not origin:
        raise ModeError(
            "AGENTCAD_MODE=hosted requires AGENTCAD_PUBLIC_ORIGIN "
            "(for example https://cad.example.com). It is what the Host and "
            "Origin checks compare against and what enrolment URLs are built "
            "from, so there is no safe default."
        )
    origin = origin.rstrip("/")
    if not _ORIGIN_RE.match(origin):
        raise ModeError(
            f"AGENTCAD_PUBLIC_ORIGIN must be an absolute http(s) origin with "
            f"no path, e.g. https://cad.example.com — got {origin!r}"
        )
    return AppMode(HOSTED, origin, _resolve_secret(env))


def _resolve_secret(env: Mapping[str, str]) -> bytes:
    """``AGENTCAD_SECRET_KEY``, else a generated key persisted 0600.

    005-lite's sessions are opaque store-backed rows rather than signed
    cookies, so nothing derives from this key *yet*; it is required
    configuration because the alternative — introducing a secret later, on the
    day something needs to be signed — is a silent single-instance-only
    upgrade. Recorded here rather than left as a puzzle for the next reader.

    Never echoed: the error paths below name the setting, never its value.
    """
    given = (env.get("AGENTCAD_SECRET_KEY") or "").strip()
    if given:
        if len(given) < MIN_SECRET_CHARS:
            raise ModeError(
                f"AGENTCAD_SECRET_KEY must be at least {MIN_SECRET_CHARS} "
                f"characters (it was shorter). Leave it unset to have one "
                f"generated and persisted 0600 in the state directory."
            )
        return given.encode("utf-8")

    path = ensure_state_dir() / SECRET_FILE
    try:
        # O_EXCL, so two processes racing at first boot cannot each generate a
        # key and have the loser's sessions silently belong to a dead secret.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        try:
            os.write(fd, secrets.token_bytes(32))
        finally:
            os.close(fd)
    data = path.read_bytes()
    if len(data) < 32:
        raise ModeError(
            f"the session secret at {path} is truncated. Delete it to have a "
            f"new one generated — every existing session becomes invalid — or "
            f"set AGENTCAD_SECRET_KEY."
        )
    # A key readable by another account on the host is not a secret. Refuse
    # rather than repair: a permission we silently widened back is a promise
    # we cannot keep about who read it in between.
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ModeError(
            f"the session secret at {path} is mode {mode:04o}; it must not be "
            f"readable by group or other. Run: chmod 600 {path}"
        )
    return data


def check_bind(mode: AppMode, host: str) -> None:
    """The interlock: refuse a non-loopback bind that nothing authenticates.

    Raised in the CLI before ``uvicorn.run``, where it becomes an exit status.
    """
    if mode.hosted or (host or "").strip() in LOOPBACK_HOSTS:
        return
    raise ModeError(
        f"refusing to bind {host!r}: binding a non-loopback interface "
        f"requires AGENTCAD_MODE=hosted. In local mode the server has no "
        f"authentication at all — the Host allowlist and same-origin check "
        f"are defences against DNS rebinding and CSRF *against a loopback "
        f"server*, not a substitute for auth. Set AGENTCAD_MODE=hosted (and "
        f"AGENTCAD_PUBLIC_ORIGIN) to expose an interface."
    )
