"""The worker confining itself, before it imports a geometry kernel.

``worker.py``'s first two statements are ``from ._preamble import
apply_from_env`` and ``apply_from_env()``. Everything downstream — CPython's
own imports of OCP and build123d included — then runs inside whatever this
applied, which is the point: on Linux the process that runs arbitrary
part-script Python is the process that restricted itself, so there is no
window and no helper binary (design spec, Decision 1).

The client writes ``AGENTCAD_CONFINE`` into the child's environment; **with the
variable absent this is a no-op**, so importing ``agentcad.kernel.worker``
in-process (the tests do) changes nothing at all.

Nothing here raises. A worker that cannot confine itself must still start and
answer — it is the *client* that decides what to do about it, from
:data:`REPORT`, which travels back on the ``ping`` result. That is Decision 8's
honesty rule in code: confinement is reported by the process that applied it,
never claimed by the process that intended it.
"""

from __future__ import annotations

import json
import os
import sys

#: The environment variable carrying the JSON payload. Keys: ``posture``,
#: ``rlimits`` (``{"RLIMIT_AS": [soft, hard], ...}``), ``landlock``
#: (``{"read_roots", "write_roots", "extra_files"}``), ``seccomp``
#: (``{"server_pid"}``), ``quotas`` (tiers the parent installed *around* this
#: process, e.g. ``["job_object"]``) and ``confinement`` (facets the *parent*
#: already applied before this process ran, e.g. macOS's
#: ``["filesystem", "network"]`` for the seatbelt wrapped around the argv —
#: this process never touches Landlock/seccomp itself, so those two keys stay
#: unset, and without ``confinement`` a seatbelt denial would have nothing to
#: point ``denials.active_facets`` at).
ENV = "AGENTCAD_CONFINE"

#: What was actually applied, filled in by :func:`apply_from_env`.
#: ``worker.handle_ping`` copies it into its result under ``"sandbox"``, and
#: ``worker._script_error_from_exc`` reads its truthiness to decide whether a
#: ``PermissionError`` is a sandbox denial or an ordinary bug.
REPORT: dict = {}

#: One line, on stderr, naming what happened. The client keeps stderr as the
#: crash tail, so this is the first thing a reader sees when a worker dies.
LOG_PREFIX = "[agentcad-sandbox]"

#: The worker module is imported **twice** in a worker process: once as
#: ``__main__`` (``python -m agentcad.kernel.worker``) and again as
#: ``agentcad.kernel.worker`` the first time a handler pack does
#: ``from ..worker import ...`` (`handlers/specs.py`; `handlers/holes.py`
#: documents the same trap for the shape cache). This module is imported once
#: under one name, so the flag lives here: confining twice would stack a
#: second Landlock ruleset and a second seccomp filter, and print the report
#: line twice.
_APPLIED = False


def apply_from_env() -> dict:
    """Apply the confinement in ``AGENTCAD_CONFINE``; return :data:`REPORT`.

    Order is rlimits, then Landlock, then seccomp — caps first because they
    are the only ones that also apply on macOS, filesystem before syscalls
    because the seccomp filter is what makes the remaining steps unrepeatable.
    Every stage failure lands in ``REPORT["failures"]`` as
    ``{"stage", "error"}`` and the next stage still runs: a kernel too old for
    Landlock can still have its network filtered.

    The stage names are read, not decorative: ``landlock`` and ``seccomp`` are
    the two whose failure means *this process is not confined*
    (``client.CONFINEMENT_STAGES``). ``rlimits`` is a cap that did not apply
    and ``landlock_root`` is one grant that was lost from a ruleset that did —
    both belong in health's warnings, neither says anything about confinement.
    """
    global _APPLIED

    raw = os.environ.get(ENV)
    if _APPLIED or not raw or not raw.strip():
        return REPORT
    _APPLIED = True

    # A part script must never be able to write a .pyc into the venv, and
    # under a write-root ruleset every attempt would be a denied open.
    sys.dont_write_bytecode = True

    failures: list[dict] = []
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"{ENV} must be a JSON object")
    except (ValueError, TypeError) as exc:
        payload = {}
        failures.append({"stage": "payload", "error": f"{type(exc).__name__}: {exc}"})

    # `quotas` is the one entry nothing here applies: it names tiers the
    # PARENT installed around this process (a job object on Windows, where
    # there are no rlimits at all). The worker still has to know a cap is in
    # force, or `denials.classify` would call a job object's MemoryError an
    # ordinary out-of-memory and send the reader looking for a leak.
    # `confinement` names facets the PARENT already applied before this
    # process ran (macOS's seatbelt, wrapped around the argv) — copied
    # through verbatim, never computed here, because this process has no way
    # to verify a seatbelt it did not apply to itself. `denials.active_facets`
    # trusts it exactly as it trusts `landlock_abi`/`seccomp`: as a claim from
    # whichever process actually did the confining.
    report: dict = {"posture": payload.get("posture"), "rlimits": [],
                    "quotas": [str(tier) for tier in payload.get("quotas") or []],
                    "confinement": [str(facet) for facet
                                    in payload.get("confinement") or []],
                    "landlock_abi": None, "seccomp": None,
                    "failures": failures}

    rlimits = payload.get("rlimits") or {}
    if rlimits:
        report["rlimits"] = _rlimits(rlimits, failures)
    landlock = payload.get("landlock")
    if isinstance(landlock, dict):
        report["landlock_abi"] = _landlock(landlock, failures)
    seccomp = payload.get("seccomp")
    if isinstance(seccomp, dict):
        report["seccomp"] = _seccomp(seccomp, failures)

    extra = ""
    if sys.platform == "win32":
        # Nothing to apply — the AppContainer was applied to this process
        # before its first instruction — but everything to *verify*: this is
        # the one place in the system that can read the confinement instead of
        # claiming it (design spec, Decision 3).
        report["appcontainer"], report["appcontainer_sid"] = \
            _appcontainer(failures)
        extra = (f" appcontainer={report['appcontainer']}"
                 f" appcontainer_sid={report['appcontainer_sid']}")

    REPORT.clear()
    REPORT.update(report)
    print(f"{LOG_PREFIX} posture={report['posture']} "
          f"rlimits={','.join(report['rlimits']) or '-'} "
          f"quotas={','.join(report['quotas']) or '-'} "
          f"landlock_abi={report['landlock_abi']} "
          f"seccomp={report['seccomp']}{extra} failures={len(failures)}",
          file=sys.stderr, flush=True)
    return REPORT


def _rlimits(rlimits: dict, failures: list[dict]) -> list[str]:
    try:
        from ._confine import apply_rlimits, known_rlimits

        applied = apply_rlimits(rlimits)
    except BaseException as exc:  # noqa: BLE001 - a preamble may not raise
        failures.append({"stage": "rlimits",
                         "error": f"{type(exc).__name__}: {exc}"})
        return []
    # A name this OS does not have is a legitimate absence (RLIMIT_NPROC is not
    # universal); a name it has and refused is a cap that is not in force, and
    # saying so is the whole contract.
    refused = [name for name in known_rlimits(sorted(rlimits))
               if name not in applied]
    if refused:
        failures.append({"stage": "rlimits",
                         "error": f"not applied: {', '.join(refused)}"})
    return applied


def _landlock(landlock: dict, failures: list[dict]) -> int | None:
    try:
        from ._confine import landlock_apply

        report = landlock_apply(landlock.get("read_roots") or [],
                                landlock.get("write_roots") or [],
                                landlock.get("extra_files") or [])
    except BaseException as exc:  # noqa: BLE001 - a preamble may not raise
        failures.append({"stage": "landlock",
                         "error": f"{type(exc).__name__}: {exc}"})
        return None
    if not report.get("applied"):
        failures.append({"stage": "landlock",
                         "error": report.get("reason", "not applied")})
        return None
    for path, reason in report.get("failed", []):
        # A root that does not exist costs a grant, not the ruleset: the rest
        # of it is in force and must not read as absent. Hence its OWN stage
        # (review I2) — `client.CONFINEMENT_STAGES` is `("landlock",
        # "seccomp")`, so a `landlock` entry clears the confinement claim and
        # this one does not. Filing a lost grant under `landlock` said "this
        # worker is not confined" about a worker that demonstrably was, and on
        # a CI job running under `AGENTCAD_EXPECT_SANDBOX=active` that turned
        # one missing directory into a red build.
        #
        # It is still a failure and still travels: it is in `failures`, health
        # shows it in `warnings`, and the write it was meant to permit really
        # will be denied.
        failures.append({"stage": "landlock_root", "error": f"{path}: {reason}"})
    return report.get("abi")


#: ``TOKEN_QUERY``, and the two ``TOKEN_INFORMATION_CLASS`` values that answer
#: "am I in an AppContainer, and which one".
_TOKEN_QUERY = 0x0008
_TokenIsAppContainer = 29
_TokenAppContainerSid = 31


def _appcontainer(failures: list[dict]) -> tuple[bool, str | None]:
    """``(TokenIsAppContainer, TokenAppContainerSid)`` for **this** process.

    The Windows half of the honesty rule: the parent can only ever say what it
    intended, and this is the process that can look. A failure to read the
    token is reported and answers ``False`` — "we could not verify" and "we are
    not confined" have to collapse the same way, or an unverifiable worker
    would keep a claim it cannot support.

    Every HANDLE crosses the ctypes boundary as ``c_void_p``. Without that,
    ``GetCurrentProcess()``'s pseudo-handle (``0xFFFFFFFFFFFFFFFF``) is
    marshalled as a 32-bit ``int`` and raises ``OverflowError: int too long to
    convert`` — measured, in probe round 2, which is the only reason this
    function has argtypes on every call.
    """
    handle = None
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        advapi32.OpenProcessToken.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        advapi32.GetTokenInformation.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32)]
        advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]

        handle = ctypes.c_void_p()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                         _TOKEN_QUERY, ctypes.byref(handle)):
            raise OSError(f"OpenProcessToken: WinError "
                          f"{ctypes.get_last_error()}")
        flag = ctypes.c_uint32(0)
        returned = ctypes.c_uint32(0)
        if not advapi32.GetTokenInformation(
                handle, _TokenIsAppContainer, ctypes.byref(flag), 4,
                ctypes.byref(returned)):
            raise OSError(f"GetTokenInformation(TokenIsAppContainer): WinError "
                          f"{ctypes.get_last_error()}")
        in_container = bool(flag.value)
        return in_container, (_appcontainer_sid(ctypes, kernel32, advapi32,
                                                handle)
                              if in_container else None)
    except BaseException as exc:  # noqa: BLE001 - a preamble may not raise
        failures.append({"stage": "appcontainer",
                         "error": f"{type(exc).__name__}: {exc}"})
        return False, None
    finally:
        if handle is not None and handle.value:
            try:
                import ctypes as _ctypes

                _ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                    _ctypes.c_void_p(handle.value))
            except BaseException:  # noqa: BLE001 - cleanup never raises
                pass


def _appcontainer_sid(ctypes, kernel32, advapi32, handle) -> str | None:
    """``TokenAppContainerSid`` as ``S-1-15-2-...``, or ``None``.

    The documented two-call form: the first ``GetTokenInformation`` is
    *expected* to fail and only fills in the size. The string comes back in
    memory Windows allocated, so it is copied out and ``LocalFree``d.
    """
    size = ctypes.c_uint32(0)
    advapi32.GetTokenInformation(handle, _TokenAppContainerSid, None, 0,
                                 ctypes.byref(size))
    if size.value == 0:
        raise OSError(f"GetTokenInformation(TokenAppContainerSid) size: "
                      f"WinError {ctypes.get_last_error()}")
    buffer = (ctypes.c_char * size.value)()
    if not advapi32.GetTokenInformation(handle, _TokenAppContainerSid, buffer,
                                        size, ctypes.byref(size)):
        raise OSError(f"GetTokenInformation(TokenAppContainerSid): WinError "
                      f"{ctypes.get_last_error()}")
    # TOKEN_APPCONTAINER_INFORMATION is one pointer: the PSID.
    psid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    text = ctypes.c_void_p()
    if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(psid),
                                           ctypes.byref(text)):
        raise OSError(f"ConvertSidToStringSidW: WinError "
                      f"{ctypes.get_last_error()}")
    try:
        return ctypes.wstring_at(text)
    finally:
        kernel32.LocalFree(text)


def _seccomp(seccomp: dict, failures: list[dict]) -> str | None:
    try:
        from ._confine import seccomp_apply

        return seccomp_apply(int(seccomp.get("server_pid") or 0))
    except BaseException as exc:  # noqa: BLE001 - a preamble may not raise
        failures.append({"stage": "seccomp",
                         "error": f"{type(exc).__name__}: {exc}"})
        return None
