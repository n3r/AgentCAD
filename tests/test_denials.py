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


#: A frame that names the call, as `traceback.format_exc()` renders it.
NET_FRAME = ('  File "<part>", line 4, in build\n'
             "    socket.create_connection(('1.1.1.1', 80))\n")
KILL_FRAME = ('  File "<part>", line 3, in build\n'
              "    os.kill(-1, signal.SIGKILL)\n")


@pytest.mark.parametrize("exc_type,message,expected", [
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


@pytest.mark.parametrize("frames", [
    NET_FRAME,                                          # create_connection
    '    s = socket.socket()\n',
    '    urllib.request.urlopen("http://1.1.1.1/")\n',
    '    socket.getaddrinfo("example.com", 80)\n',
    '    sock.connect((host, port))\n',
])
def test_an_eperm_from_a_socket_call_is_the_network_denial(frames):
    """EPERM plus a frame that names the call. `create_connection` matches on
    `connect`, which is why the marker list does not repeat it."""
    assert classify("PermissionError", "[Errno 1] Operation not permitted",
                    active=True, traceback=frames) == "network"


def test_an_eperm_with_no_socket_frame_is_not_a_denial_at_all():
    """The seccomp filter answers EPERM for a refused `kill` too. Labelling
    that `network` would send an agent to fix a networking bug that is not
    there — so an unattributed EPERM is `None`, not a guess."""
    assert classify("PermissionError", "[Errno 1] Operation not permitted",
                    active=True, traceback=KILL_FRAME) is None
    assert classify("PermissionError", "[Errno 1] Operation not permitted",
                    active=True) is None


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
                    active=True, traceback=NET_FRAME) == "network"
