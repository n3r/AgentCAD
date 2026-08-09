"""Frozen-aware kernel-worker spawn command.

Lives in its own tiny module (rather than in ``agentcad.cli``) so that
``agentcad.kernel.client`` — a lower layer than the CLI — never has to import
the CLI module just to learn how to spawn a worker. ``agentcad.cli``
re-exports :func:`worker_argv` for convenience.

Normal (unfrozen) runs spawn ``python -u -m agentcad.kernel.worker``. In a
PyInstaller bundle there is no ``python`` on disk, so the bundle's own
executable re-execs itself with the hidden ``worker`` subcommand
(``agentcad worker``), which runs :func:`agentcad.kernel.worker.main` in the
child process. ``-u`` is unnecessary there: the worker flushes stdout after
every response, and the frozen bootloader accepts no interpreter flags.
"""

from __future__ import annotations

import sys


def worker_argv(python_exe: str | None = None) -> list[str]:
    """Argv that starts a kernel worker subprocess.

    ``python_exe`` overrides the interpreter for the unfrozen path (used by
    tests / KernelClient's ``python_exe`` argument); it defaults to
    ``sys.executable`` and the returned list is byte-identical to the
    historical ``[python, "-u", "-m", "agentcad.kernel.worker"]``.
    """
    exe = python_exe or sys.executable
    if getattr(sys, "frozen", False):
        return [exe, "worker"]
    return [exe, "-u", "-m", "agentcad.kernel.worker"]
