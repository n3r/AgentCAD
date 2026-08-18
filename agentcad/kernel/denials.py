"""Naming what a confinement or a quota refused, from the exception it raised.

A breach that happens *inside* a part script stays what it already is — a
`script_error` with a traceback, a line number and an Error Doctor hint
(design spec, Decision 9). This module adds the one word that tells an agent
whether to fix the script or shrink the job: ``details.denied`` ∈
``{network, filesystem, process_count, memory}``.

String-level on purpose. The worker calls it with the formatted exception, so
the same four answers cover a seatbelt EPERM on macOS, a Landlock EACCES and a
seccomp EPERM on Linux, and a job object's `MemoryError` on Windows, without
this module knowing which one is in force.

Deliberately importable from server code: no ``OCP``/build123d, no OS call.
"""

from __future__ import annotations

#: EAGAIN is 11 on Linux and 35 on macOS — the fork cap answers with whichever
#: the platform uses, and both mean "the process budget is spent".
_EAGAIN = ("[Errno 11]", "[Errno 35]", "Resource temporarily unavailable")


def classify(exc_type: str, message: str, *, active: bool) -> str | None:
    """The denial *exc_type*/*message* represents, or ``None``.

    *active* is whether this worker actually applied a confinement or a quota
    (the preamble's own report — never an intention). With nothing applied the
    answer is always ``None``: an unconfined worker's ``PermissionError`` is a
    plain file-permission bug in the script, and calling it a sandbox denial
    would send the reader looking for a cap that does not exist.
    """
    if not active:
        return None
    if exc_type == "MemoryError":
        return "memory"
    if exc_type == "PermissionError":
        # EPERM is the kernel refusing the *operation* (a socket call under a
        # seccomp filter or the seatbelt); EACCES is it refusing the *path*.
        if "[Errno 1]" in message or "Operation not permitted" in message:
            return "network"
        if "[Errno 13]" in message or "Permission denied" in message:
            return "filesystem"
        return None
    if exc_type in ("BlockingIOError", "OSError"):
        if any(marker in message for marker in _EAGAIN):
            return "process_count"
    return None
