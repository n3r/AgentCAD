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

from agentcad.kernel.denials import active_facets, classify


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


def test_the_facets_are_claimed_one_by_one_from_the_workers_own_report():
    """Review M3. "Something is confining this process" is not evidence for all
    four answers. A worker whose rlimits applied and whose Landlock did not —
    an `AGENTCAD_NO_SANDBOX` Linux worker, which still gets its caps — has real
    evidence for `process_count` and `memory` and none at all for the
    filesystem, and the blanket boolean made it label an ordinary DAC `EACCES`
    a sandbox denial.
    """
    no_sandbox = {"posture": "local", "rlimits": ["RLIMIT_AS", "RLIMIT_NPROC"],
                  "quotas": [], "landlock_abi": None, "seccomp": None,
                  "failures": []}
    assert active_facets(no_sandbox) == {"process_count", "memory"}

    confined = {"posture": "local", "rlimits": ["RLIMIT_AS"], "quotas": [],
                "landlock_abi": 6, "seccomp": "seccomp(2)", "failures": []}
    assert active_facets(confined) == {"filesystem", "network",
                                       "process_count", "memory"}

    # A tier the PARENT installed around the worker (a Windows job object, a
    # cgroup's pids.max): no rlimit of its own, and still a real cap.
    job = {"posture": "local", "rlimits": [], "quotas": ["job_object"],
           "landlock_abi": None, "seccomp": None, "failures": []}
    assert active_facets(job) == {"process_count", "memory"}

    # Nothing applied at all — and a report that is not a dict.
    assert active_facets({"rlimits": [], "quotas": [], "landlock_abi": None,
                          "seccomp": None}) == frozenset()
    assert active_facets(None) == frozenset()

    # ...and that is what `classify` then honours, facet by facet.
    assert classify("PermissionError", "[Errno 13] Permission denied: '/x'",
                    active=active_facets(no_sandbox)) is None
    assert classify("PermissionError", "[Errno 13] Permission denied: '/x'",
                    active=active_facets(confined)) == "filesystem"
    assert classify("PermissionError", "[Errno 1] Operation not permitted",
                    active=active_facets(no_sandbox),
                    traceback=NET_FRAME) is None
    assert classify("PermissionError", "[Errno 1] Operation not permitted",
                    active=active_facets(confined),
                    traceback=NET_FRAME) == "network"
    assert classify("MemoryError", "", active=active_facets(no_sandbox)) \
        == "memory"
    assert classify("BlockingIOError", "[Errno 11] Resource temporarily "
                    "unavailable", active=active_facets(job)) == "process_count"


def test_a_parent_declared_facet_is_active_even_with_no_worker_evidence():
    """The macOS `details.denied` regression. The seatbelt wraps the argv in
    the PARENT process, before the worker ever runs — so a seatbelt
    EACCES/EPERM leaves `landlock_abi: None` and `seccomp: None` in the
    worker's own preamble report, exactly like an unconfined worker's. Without
    the parent's declaration a real denial would go unlabelled; with it
    (`sandbox_macos.build`'s `confinement: ["filesystem", "network"]`, copied
    through by `_preamble.apply_from_env`) the two facets it genuinely
    enforces are still claimable.
    """
    macos_confined = {"posture": "local", "rlimits": ["RLIMIT_NPROC"],
                      "quotas": [], "landlock_abi": None, "seccomp": None,
                      "confinement": ["filesystem", "network"], "failures": []}
    assert active_facets(macos_confined) == {"filesystem", "network",
                                              "process_count", "memory"}
    assert classify("PermissionError", "[Errno 13] Permission denied: '/x'",
                    active=active_facets(macos_confined)) == "filesystem"
    assert classify("PermissionError", "[Errno 1] Operation not permitted",
                    active=active_facets(macos_confined),
                    traceback=NET_FRAME) == "network"

    # A report with neither the worker's own evidence NOR a parent
    # declaration still claims nothing — `confinement` is additive, not a
    # blanket override.
    undeclared = {"posture": "local", "rlimits": [], "quotas": [],
                 "landlock_abi": None, "seccomp": None, "confinement": [],
                 "failures": []}
    assert active_facets(undeclared) == frozenset()


def test_a_linux_worker_with_a_failed_stage_claims_nothing_for_that_facet():
    """Independent re-review, post-F1: `sandbox_linux.build()` no longer
    declares `payload["confinement"]` at all (it used to, "for symmetry with
    `sandbox_macos`"). Linux is not macOS's case — the Linux worker CAN and
    DOES self-report `landlock_abi`/`seccomp` from actually applying them, so
    a parent-declared facet list there was a second, unconditional claim: if
    `landlock`/`seccomp` failed *inside* the worker after the parent had
    already decided (at plan time) that it intended to apply them, the
    self-report went `landlock_abi: None`/`seccomp: None` but the declared
    list still said `["filesystem", "network"]`, so `active_facets` would
    have relabelled a plain DAC `EACCES` a sandbox denial — exactly what
    review M3 exists to prevent. This is the shape `_preamble.apply_from_env`
    now produces for a Linux worker with no `confinement` key in its payload
    (``payload.get("confinement") or []``): the key is present as `[]`, never
    omitted, and both spellings must claim nothing.
    """
    failed_landlock = {"posture": "hosted", "rlimits": ["RLIMIT_NPROC"],
                       "quotas": [], "landlock_abi": None, "seccomp": None,
                       "confinement": [], "failures": [
                           {"stage": "landlock", "error": "EINVAL"}]}
    assert active_facets(failed_landlock) == {"process_count", "memory"}
    assert classify("PermissionError", "[Errno 13] Permission denied: '/etc/shadow'",
                    active=active_facets(failed_landlock)) is None

    # The key omitted entirely behaves identically — `.get(...) or []`.
    omitted = {"posture": "hosted", "rlimits": [], "quotas": [],
              "landlock_abi": None, "seccomp": None, "failures": []}
    assert active_facets(omitted) == frozenset()


def test_a_bare_boolean_still_means_all_four_or_none():
    """The parameter kept its old shape so a caller with one honest answer for
    the whole worker (and every existing test above) needs no change."""
    assert classify("MemoryError", "", active=True) == "memory"
    assert classify("MemoryError", "", active=False) is None
    assert classify("PermissionError", "Permission denied", active=True) \
        == "filesystem"
    assert classify("PermissionError", "Permission denied", active=False) is None


#: What Winsock answers inside an AppContainer with no `INTERNET_CLIENT`
#: capability, verbatim from the probe run on windows-latest. Note what is NOT
#: in it: no `[Errno 13]`, no "Permission denied" — so before PRD-006b every
#: rule in the classifier missed it and the answer was `None`.
WSAEACCES = ("[WinError 10013] An attempt was made to access a socket in a way "
             "forbidden by its access permissions")


def test_a_winsock_10013_is_the_windows_network_denial():
    """PRD-006b: the AppContainer denies the network by *not having* the
    capability, and Winsock says so with `WSAEACCES` rather than an errno the
    POSIX rules recognise."""
    assert classify("PermissionError", WSAEACCES,
                    active=frozenset({"network"}),
                    traceback=NET_FRAME) == "network"
    # The parent has to have declared the facet — an unconfined Windows box
    # answers 10013 for a bind to a reserved port too, and that is a bug in the
    # script, not a denial.
    assert classify("PermissionError", WSAEACCES, active=frozenset(),
                    traceback=NET_FRAME) is None
    assert classify("PermissionError", WSAEACCES,
                    active=frozenset({"filesystem"}),
                    traceback=NET_FRAME) is None
    # ...and it is never read as a filesystem denial on its way past.
    assert classify("PermissionError", WSAEACCES,
                    active=frozenset({"filesystem", "network"})) == "network"


def test_a_windows_worker_reads_a_file_denial_the_ordinary_way():
    """The other half of the container: a write outside the granted roots
    surfaces as an ordinary `EACCES` with the path in it (measured — the probe
    saw `[Errno 13]` for `C:\\Users\\Public`), so no new rule is needed."""
    assert classify("PermissionError",
                    "[Errno 13] Permission denied: "
                    "'C:\\\\Users\\\\Public\\\\agentcad-pwned.txt'",
                    active=frozenset({"filesystem", "network"})) == "filesystem"


def test_a_windows_worker_that_is_not_in_its_container_claims_nothing():
    """The lowbox spawn can fail and fall back to a plain `Popen` — and the
    payload still says `confinement: [filesystem, network]`, because the parent
    wrote it before it knew.

    An unconfined worker holding that declaration labelled every ordinary
    `[Errno 13]` a sandbox denial, which is the exact overstatement Decision 8
    forbids: the reader goes looking for a cap that is not there. The worker's
    own token is the tiebreak, the same rule `confinement_holds` applies.
    """
    declared = {"posture": "local", "quotas": ["job_object"],
                "confinement": ["filesystem", "network"]}

    assert active_facets({**declared, "appcontainer": True}) == {
        "filesystem", "network", "process_count", "memory"}
    # ...and with the token saying otherwise, only what the JOB OBJECT attests
    # survives: a `MemoryError` is still a cap being enforced.
    for report in ({**declared, "appcontainer": False},
                   {**declared, "appcontainer": None}):
        assert active_facets(report) == {"process_count", "memory"}
        assert classify("PermissionError",
                        "[Errno 13] Permission denied: 'C:\\x'",
                        active=active_facets(report)) is None
        assert classify("PermissionError", WSAEACCES,
                        active=active_facets(report),
                        traceback=NET_FRAME) is None
        assert classify("MemoryError", "",
                        active=active_facets(report)) == "memory"

    # The macOS seatbelt path has no `appcontainer` key at all and is untouched.
    assert active_facets(declared) == {"filesystem", "network",
                                       "process_count", "memory"}


def test_network_wins_over_filesystem_when_both_read():
    """Both are `PermissionError`; the errno is what separates them, and the
    network check has to come first or every EPERM would read as a write."""
    assert classify("PermissionError",
                    "[Errno 1] Operation not permitted: '/etc/hosts'",
                    active=True, traceback=NET_FRAME) == "network"
