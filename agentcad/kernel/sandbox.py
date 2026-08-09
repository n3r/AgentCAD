"""macOS seatbelt (sandbox-exec) confinement for kernel workers.

Part scripts execute arbitrary Python inside the worker subprocess. This
module wraps the worker's argv in ``/usr/bin/sandbox-exec -p <profile>`` so a
script can still compute anything, but can only *write* inside the project
roots (plus the system temp dir) and cannot use the network.

Deliberately importable from server code: no ``OCP``/build123d imports here.
The profile is deny-by-default; every ``allow`` below is documented with the
observed reason it is needed (see ``build_profile``).

Opt-out: ``AGENTCAD_NO_SANDBOX=1`` (env, wins) or ``{"sandbox": false}`` in
the user config file.
"""

from __future__ import annotations

import os
import sys
import tempfile

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

_TRUTHY = {"1", "true", "yes", "on"}


def supported() -> bool:
    """Platform can sandbox at all: macOS with the seatbelt CLI present."""
    return sys.platform == "darwin" and os.path.isfile(SANDBOX_EXEC)


def _disabled() -> bool:
    """User opt-out. The env var wins over the config file either way:
    AGENTCAD_NO_SANDBOX=1 disables even if config says otherwise, and an
    explicit AGENTCAD_NO_SANDBOX=0 re-enables over ``{"sandbox": false}``."""
    env = os.environ.get("AGENTCAD_NO_SANDBOX")
    if env is not None and env.strip() != "":
        return env.strip().lower() in _TRUTHY
    from ..config import load_config

    return load_config().get("sandbox") is False


def available() -> bool:
    """True when a newly spawned worker would be confined."""
    return supported() and not _disabled()


def build_profile(writable_dirs: list[str]) -> str:
    """Seatbelt profile: deny by default, global read, write only where allowed.

    The allow set was validated on macOS by running the real worker (CPython
    + build123d/OCCT) through ping+build under profile variants with each
    allow removed in turn (and the full profile through build/export/assembly
    flows); per-line comments record which denials were observed and which
    lines are kept deliberately beyond the observed minimum.
    """
    roots: list[str] = []
    for d in [*writable_dirs, tempfile.gettempdir()]:
        real = os.path.realpath(d)
        if real not in roots:
            roots.append(real)
    write_rules = "\n".join(
        f'(allow file-write* (subpath "{_escape(r)}"))' for r in roots
    )
    return f"""(version 1)
(deny default)
; required: sandbox-exec applies the profile then execvp()s the worker python
; (observed: removing it -> "execvp() ... failed: Operation not permitted").
(allow process-exec*)
; not observed as required for ping/build, but forked children inherit the
; sandbox, so allowing fork keeps scripts using multiprocessing/os.fork from
; dying in surprising ways without weakening confinement.
(allow process-fork)
; lets Python raise signals at itself (faulthandler, KeyboardInterrupt);
; target self only — the worker cannot signal other processes.
(allow signal (target self))
; required (dyld + interpreter + site-packages/OCP + /dev/urandom); global
; read is the accepted v1 posture — confinement here is about writes/network.
(allow file-read*)
; write access ONLY inside the configured project roots + the temp dir
; (mesh caches under <project>/.cache, exports/, tessellation scratch):
{write_rules}
; harmless device sink; kept so scripts writing to os.devnull don't trip a
; denial (not load-bearing for ping/build in experiments).
(allow file-write* (literal "/dev/null"))
; required: build123d's import chain calls os.uname()/sysctl (observed:
; removing it -> PermissionError: [Errno 1] in platform machine lookup).
(allow sysctl-read)
; already covered by (deny default); kept explicit as documentation of the
; second confinement goal.
(deny network*)
"""


def _escape(path: str) -> str:
    # Seatbelt string literals use double quotes; escape embedded quotes/backslashes.
    return path.replace("\\", "\\\\").replace('"', '\\"')


def wrap_argv(argv: list[str], writable_dirs: list[str]) -> list[str]:
    """Wrap a worker argv in sandbox-exec when confinement is on; else unchanged."""
    if not writable_dirs or not available():
        return list(argv)
    return [SANDBOX_EXEC, "-p", build_profile(writable_dirs), *argv]


def status(sandboxed: bool | None = None) -> str:
    """Effective sandbox status: "active" | "off" | "unsupported".

    With ``sandboxed`` (the actual state of the running kernel client) the
    answer reflects the live service; without it, it reflects what a NEW
    KernelClient constructed with writable dirs would get.
    """
    if not supported():
        return "unsupported"
    if sandboxed is None:
        sandboxed = available()
    return "active" if sandboxed else "off"
