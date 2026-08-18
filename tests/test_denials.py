"""PRD-006 slice 2 — `kernel/denials.py`: naming what the sandbox refused.

A denial is not a new error type (design spec, Decision 9): it is the ordinary
`script_error` the agent already knows how to read, plus one word saying which
promise the script walked into. The classifier is deliberately string-level —
it runs in the worker on an exception that has already been formatted, and it
must answer the same way for a `PermissionError` raised by the seatbelt, by
Landlock and by a seccomp filter.
"""

from __future__ import annotations

import pytest

from agentcad.kernel.denials import classify


@pytest.mark.parametrize("exc_type,message,expected", [
    # network: EPERM at the socket call (seccomp on Linux, seatbelt on macOS)
    ("PermissionError", "[Errno 1] Operation not permitted", "network"),
    ("PermissionError", "Operation not permitted", "network"),
    # filesystem: EACCES on a path outside the write roots
    ("PermissionError", "[Errno 13] Permission denied: '/usr/pwned'",
     "filesystem"),
    ("PermissionError", "Permission denied: '/app/x'", "filesystem"),
    # process count: RLIMIT_NPROC / pids.max, EAGAIN (11 on Linux, 35 on macOS)
    ("BlockingIOError", "[Errno 11] Resource temporarily unavailable",
     "process_count"),
    ("BlockingIOError", "[Errno 35] Resource temporarily unavailable",
     "process_count"),
    ("OSError", "[Errno 11] Resource temporarily unavailable", "process_count"),
    ("OSError", "Resource temporarily unavailable", "process_count"),
    # memory: RLIMIT_AS, or a job object's commit limit
    ("MemoryError", "", "memory"),
])
def test_the_four_denials_are_named(exc_type, message, expected):
    assert classify(exc_type, message, active=True) == expected


@pytest.mark.parametrize("exc_type,message", [
    ("ValueError", "not a denial at all"),
    ("FileNotFoundError", "[Errno 2] No such file or directory: '/nope'"),
    ("OSError", "[Errno 28] No space left on device"),
    ("RuntimeError", "Operation not permitted"),   # the type has to agree too
])
def test_an_ordinary_failure_is_not_a_denial(exc_type, message):
    assert classify(exc_type, message, active=True) is None


def test_nothing_is_a_denial_when_nothing_is_confining():
    """The honesty rule in one line: with no sandbox applied, a
    `PermissionError` is a plain file-permission bug in the script and calling
    it a sandbox denial would send the agent chasing a cap that is not there."""
    assert classify("PermissionError", "[Errno 13] Permission denied",
                    active=False) is None
    assert classify("MemoryError", "", active=False) is None


def test_network_wins_over_filesystem_when_both_read():
    """Both are `PermissionError`; the errno is what separates them, and the
    network check has to come first or every EPERM would read as a write."""
    assert classify("PermissionError",
                    "[Errno 1] Operation not permitted: '/etc/hosts'",
                    active=True) == "network"
