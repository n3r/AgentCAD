"""PRD-006 slice 2 — the Linux battery: a real worker, really confined.

Everything here drives the actual kernel worker with an actual part script
and asserts what came back. It runs on Linux only: locally through
`make test-linux` (which copies the tree into `agentcad:local` — Landlock is
not coherent over Docker Desktop's `fakeowner` bind mounts, so the tree is
COPIED, never mounted) and on the ubuntu CI job.

Decision 13's honesty gate: each test asserts containment *when the live
status is active*, and `AGENTCAD_EXPECT_SANDBOX=active` turns "not active"
from a skip into a failure. `make test-linux` and CI both set it, so a silent
degradation to `off` is red rather than quietly green.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agentcad.kernel import sandbox
from agentcad.kernel.client import KernelClient, KernelError
from agentcad.kernel.pool import KernelPool
from agentcad.kernel.quotas import resolve

pytestmark = [
    pytest.mark.portability,
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "linux",
                       reason="Landlock + seccomp confinement is Linux-only"),
]

BOX = """\
from build123d import *

PARAMS = {}

def build(p):
    with BuildPart() as part:
        Box(10, 10, 10)
    return part.part
"""

#: memory_mb is well above the 451-482 MB a warm worker occupies, and
#: address_space_mb resolves to 3x it — the spike measured RLIMIT_AS at
#: 1.25 GiB failing during `import build123d`, 1.5 GiB as the floor.
QUOTAS = {"memory_mb": 1024, "pids_headroom": 64}


def _client(tmp_path_factory, name, quotas=None, **kwargs):
    """A confined client whose project root is a fresh tmp dir.

    `pytest.MonkeyPatch` rather than the fixture: these are module-scoped, and
    the sandbox decision is made at construction, so the environment only has
    to be right for that one call.
    """
    root = tmp_path_factory.mktemp(name)
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CONFIG", str(root / "no-such-config.json"))
    patch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    try:
        client = KernelClient(writable_dirs=[str(root)],
                              quotas=resolve(quotas or QUOTAS, env={}, config={}),
                              **kwargs)
    finally:
        patch.undo()
    client.start()
    return client, root


@pytest.fixture(scope="module")
def confined(tmp_path_factory):
    client, root = _client(tmp_path_factory, "confined")
    yield client, root
    client.stop()


@pytest.fixture
def battery(confined):
    """`(client, root)`, but only where the confinement is really in force.

    Without this a degraded worker would fail every test in the file with a
    misleading message; with `AGENTCAD_EXPECT_SANDBOX=active` set it does not
    skip at all, which is the point of the gate.
    """
    client, root = confined
    if not client.sandboxed and os.environ.get("AGENTCAD_EXPECT_SANDBOX") != "active":
        pytest.skip(f"the worker is not confined; its own report is "
                    f"{client.sandbox_report!r}")
    return client, root


def _build(client, script, mesh_path, params=None):
    return client.request("build", {"script": script, "params": params or {},
                                    "mesh_path": str(mesh_path)})


def _script(body: str) -> str:
    """A part script whose `build()` runs *body* and then returns a box."""
    indented = "\n".join("    " + line for line in body.strip("\n").splitlines())
    return ("from build123d import *\n\nPARAMS = {}\n\ndef build(p):\n"
            f"{indented}\n"
            "    with BuildPart() as part:\n        Box(10, 10, 10)\n"
            "    return part.part\n")


def _denied(client, root, body, name):
    """Run *body* in a part script; return the KernelError it raised."""
    with pytest.raises(KernelError) as exc_info:
        _build(client, _script(body), root / ".cache" / f"{name}.acm")
    return exc_info.value


# ------------------------------------------------------- what the worker says

def test_ping_reports_landlock_and_seccomp(confined):
    """What the worker says it applied to itself. These assertions are
    unconditional on Linux — a kernel that cannot confine must fail here, not
    quietly skip. Only the *claim* (`sandboxed`) is gated, because Decision 13
    makes `AGENTCAD_EXPECT_SANDBOX=active` the thing that turns a degradation
    into a red rather than a shrug."""
    client, _root = confined
    report = client.sandbox_report
    assert report["landlock_abi"] >= 3
    assert report["seccomp"] in ("seccomp(2)", "prctl")
    assert report["rlimits"] == ["RLIMIT_AS", "RLIMIT_NPROC"]
    assert report["posture"] == "local"
    assert report["failures"] == []
    expect = os.environ.get("AGENTCAD_EXPECT_SANDBOX")
    if expect != "active":
        pytest.skip(f"AGENTCAD_EXPECT_SANDBOX={expect!r}; the live report "
                    f"above passed and `sandboxed` is {client.sandboxed!r}")
    # `sandboxed` is the WORKER's answer, not the plan's intention.
    assert client.sandboxed is True


def test_a_root_that_does_not_exist_is_a_lost_grant_but_still_confined(
        tmp_path_factory):
    """Why the CLI creates the roots it owns — the projects dir, and an
    **accepted** `--work-dir` — before a worker spawns: on Linux a Landlock
    rule on a missing path is ENOENT, and this is what that costs. The root is
    never granted, so a write into it is denied the moment the directory does
    appear, and every part under it fails with a `PermissionError` instead of
    producing a verdict.

    What it does **not** cost, since review I2: the confinement claim. The
    ruleset landed; one path out of it did not. So the failure is reported
    under its own stage, `landlock_root`, and `sandboxed` stays True — filing
    it under `landlock` said "this worker is unconfined" about a worker that
    demonstrably was, and turned one missing directory into a red CI job under
    `AGENTCAD_EXPECT_SANDBOX=active`.

    Asserted rather than fixed here on purpose: `plan()` must NOT create the
    roots it is handed, because it also receives `--work-dir` paths that may
    still be refused (`tests/test_sandbox_plan.py` pins both halves).
    """
    root = tmp_path_factory.mktemp("missing-root")
    # OUTSIDE `root`: a Landlock grant is path-beneath, so a subdirectory of an
    # existing root would be writable through the parent's rule and prove
    # nothing about its own.
    absent = root.parent / "never-created-root"
    assert not absent.exists()
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CONFIG", str(root / "no-such-config.json"))
    patch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    try:
        client = KernelClient(writable_dirs=[str(root), str(absent)],
                              quotas=resolve(QUOTAS, env={}, config={}))
    finally:
        patch.undo()
    client.start()
    try:
        failures = client.sandbox_report["failures"]
        assert any(str(absent) in f["error"] and f["stage"] == "landlock_root"
                   for f in failures), failures
        # The ruleset is in force, so the claim holds — and health shows the
        # lost grant as a warning rather than as a failed sandbox.
        assert client.sandboxed is True
        warnings = sandbox.report(client)["warnings"]
        assert any("lost a Landlock grant" in w and str(absent) in w
                   for w in warnings), warnings

        absent.mkdir()                          # ...and now it exists
        err = _denied(client, root,
                      f"open({str(absent / 'x')!r}, 'w', encoding='utf-8').write('x')",
                      "absent")
        assert err.details["denied"] == "filesystem"

        # The other half (review I1): a root that DOES exist when the worker
        # spawns keeps its grant, which is the state the CLI now guarantees for
        # an accepted `--work-dir`.
        assert _build(client, BOX, root / ".cache" / "granted.acm")["metrics"]
    finally:
        client.stop()


def test_a_work_dir_that_exists_at_spawn_is_writable(tmp_path_factory):
    """Review I1, from the worker's side. The CLI accepts a `--work-dir`,
    creates it, and only then spawns — so the grant lands and the run can
    write its cell. This is the same shape with the directory made first.
    """
    root = tmp_path_factory.mktemp("granted-work-dir")
    work = root.parent / "accepted-work-dir"
    work.mkdir(exist_ok=True)          # what `cli._accept_work_dir` does
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CONFIG", str(root / "no-such-config.json"))
    patch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    try:
        client = KernelClient(writable_dirs=[str(root), str(work)],
                              quotas=resolve(QUOTAS, env={}, config={}))
    finally:
        patch.undo()
    client.start()
    try:
        assert client.sandbox_report["failures"] == []
        assert client.sandboxed is True
        target = work / "cell.txt"
        result = _build(client, _script(
            f"open({str(target)!r}, 'w', encoding='utf-8').write('ok')"
        ), root / ".cache" / "workdir.acm")
        assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0,
                                                                rel=1e-6)
        assert target.read_text(encoding="utf-8") == "ok"
    finally:
        client.stop()


def test_io_uring_is_denied(battery):
    """io_uring would be a way around the socket rule entirely: the ring's
    entries ask the kernel to open and use a socket, and the only syscall the
    filter sees is `io_uring_enter`.

    Read this as "the interface is closed in the shipped posture", not as
    attribution: **Docker's own default profile also denies io_uring here**
    (measured in `agentcad:local`: EPERM for 425/426/427 with no filter of
    ours installed). It is not redundant, though — with that profile off the
    syscall is live in the same kernel (`io_uring_setup(8, NULL)` answers
    EFAULT, not ENOSYS), so on a host without Docker's profile our filter is
    the only thing closing it. `tests/test_confine_unit.py` is the proof that
    OUR program denies it, by interpreting the bytes.
    """
    client, root = battery
    err = _denied(client, root,
                  "import ctypes\n"
                  "libc = ctypes.CDLL(None, use_errno=True)\n"
                  "libc.syscall.restype = ctypes.c_long\n"
                  "ctypes.set_errno(0)\n"
                  "rc = libc.syscall(425, 8, 0)   # io_uring_setup(8, NULL)\n"
                  "raise RuntimeError(f'io_uring rc={rc} errno={ctypes.get_errno()}')",
                  "iouring")
    assert err.type == "script_error"
    # EPERM (1). Unfiltered, `io_uring_setup(8, NULL)` would answer EFAULT (14)
    # — a ring it could then fill — or hand back a real ring fd (rc >= 0).
    assert "errno=1" in err.message, err.message
    assert "rc=-1" in err.message, err.message


def test_a_normal_build_still_works_confined(battery):
    """The load-bearing negative: none of this may cost a real build."""
    client, root = battery
    result = _build(client, BOX, root / ".cache" / "box.acm")
    assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert (root / ".cache" / "box.acm").read_bytes()[:4] == b"ACM1"


@pytest.mark.timeout(600)
def test_a_three_worker_pool_all_start_under_their_fork_budget(
        tmp_path_factory):
    """Review C2, the regression that motivated the whole fix.

    `RLIMIT_NPROC` is a **per-uid** ceiling that the kernel checks against the
    *calling* process's own limit, and a warm worker runs 15-22 threads. With
    the budget computed once — `live count + pids_headroom` — and handed
    identically to every pool slot, worker 0 and worker 1 spent the headroom
    just by existing and worker 2 died inside `import build123d` with a
    `pthread_create` EAGAIN. Measured, in this image.

    The fix is two things at once, and this needs both: the headroom is scaled
    by the pool size, and it is **re-measured at every spawn** rather than once
    at construction. So: three workers, each reached by its own affinity key
    (`hash()` is salted per process, so the keys are found at run time), each
    answering `ping`.
    """
    root = tmp_path_factory.mktemp("pool-of-three")
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CONFIG", str(root / "no-such-config.json"))
    patch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    try:
        pool = KernelPool(size=3, writable_dirs=[str(root)],
                          quotas=resolve(QUOTAS, env={}, config={}),
                          timeout_s=300)
    finally:
        patch.undo()

    keys = [f"part-{index}" for index in range(64)]
    routed = {slot: next(k for k in keys if hash(k) % 3 == slot)
              for slot in range(3)}
    assert len(set(routed.values())) == 3

    try:
        for slot, key in sorted(routed.items()):
            result = pool.request("ping", {}, affinity=key)
            assert result["ok"] is True, (slot, key, result)
        # Every slot really did spawn — the failure this guards against was a
        # worker that never came up, not one that answered wrongly.
        assert all(worker.alive for worker in pool._workers)
        # ...and each got its OWN fork budget, measured when it spawned.
        caps = {json.loads(worker._plan.spawn_env()["AGENTCAD_CONFINE"])
                ["rlimits"]["RLIMIT_NPROC"][0] for worker in pool._workers}
        assert all(cap > 3 * QUOTAS["pids_headroom"] for cap in caps), caps
        # A real build still lands, on the worker the affinity key names.
        built = pool.request(
            "build", {"script": BOX, "params": {},
                      "mesh_path": str(root / ".cache" / "pool.acm")},
            affinity=routed[0])
        assert built["metrics"]["volume_mm3"] == pytest.approx(1000.0,
                                                               rel=1e-6)
    finally:
        pool.stop()


# --------------------------------------------------------------- the battery

def test_network_is_denied(battery):
    client, root = battery
    err = _denied(client, root,
                  "import socket\n"
                  "socket.create_connection(('1.1.1.1', 80), timeout=2)",
                  "net")
    assert err.type == "script_error"
    assert err.details["denied"] == "network"
    assert "sandbox" in err.details.get("hint", "")
    # ...and the worker is the same warm process afterwards.
    assert _build(client, BOX, root / ".cache" / "after-net.acm")["metrics"]


@pytest.mark.parametrize("where", ["/app/pwned", "/usr/pwned", "home"])
def test_write_outside_roots_is_denied(battery, where):
    client, root = battery
    target = str(Path.home() / "pwned") if where == "home" else where
    err = _denied(client, root,
                  f"open({target!r}, 'w', encoding='utf-8').write('x')",
                  "write")
    assert err.type == "script_error"
    assert err.details["denied"] == "filesystem"
    assert not Path(target).exists()


def test_private_tmp_is_the_only_temp(battery, tmp_path):
    """`/tmp` is shared on Linux, so granting it wholesale would let one
    worker's script read and overwrite a sibling's scratch — the leak the
    private per-worker dir closes (Decision 1)."""
    client, root = battery
    result = _build(client, _script(
        "import os, tempfile\n"
        "tmp = tempfile.gettempdir()\n"
        "assert os.path.basename(tmp).startswith('agentcad-worker-'), tmp\n"
        "open(os.path.join(tmp, 'mine.txt'), 'w', encoding='utf-8').write(tmp)"
    ), root / ".cache" / "tmp.acm")
    assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)

    other = Path("/tmp/agentcad-other")
    other.mkdir(exist_ok=True)
    err = _denied(client, root,
                  f"open({str(other / 'pwned')!r}, 'w', encoding='utf-8').write('x')",
                  "other-tmp")
    assert err.details["denied"] == "filesystem"
    assert not (other / "pwned").exists()


def test_kill_broadcast_is_denied(battery):
    """`kill(-1, SIGKILL)` would take every process this uid owns — the server
    included. The seatbelt has always refused it; seccomp is Linux's parity
    (Decision 11)."""
    client, root = battery
    err = _denied(client, root,
                  "import os, signal\nos.kill(-1, signal.SIGKILL)", "kill")
    assert err.type == "script_error"
    assert "PermissionError" in err.message
    # EPERM, but NOT a network denial: the four categories describe what the
    # script was reaching for, and a refused broadcast signal is none of them.
    # Labelling it `network` would send an agent to fix a socket that is not
    # in the script (`denials.classify` requires a socket frame).
    assert err.details.get("denied") is None
    assert client.request("ping", {})["ok"] is True


def test_signals_to_the_server_are_denied(battery):
    client, root = battery
    err = _denied(client, root,
                  "import os, signal\n"
                  f"os.kill({os.getpid()}, signal.SIGTERM)", "kill-server")
    assert err.type == "script_error"
    assert "PermissionError" in err.message


def test_pidfd_send_signal_at_the_server_is_denied(battery):
    """Review C1, live. `pidfd_send_signal(pidfd, sig, ...)` names its target
    by a **file descriptor**, so `args[0]` is not a `pid_t` and the filter's
    negative-pid / server-pid analysis never runs on it. Denying `pidfd_open`
    was not enough: **a `/proc/<pid>` directory fd is a valid pidfd**, and
    `/proc` is readable in both postures — so before this rule a part script
    could open `/proc/<server pid>` and SIGKILL the server through it
    (verified in the image: the victim died with -9).

    The syscall number is 424 on x86_64 and on aarch64 alike.
    """
    client, root = battery
    err = _denied(client, root,
                  "import ctypes, os, signal\n"
                  f"fd = os.open('/proc/{os.getpid()}', "
                  "os.O_RDONLY | os.O_DIRECTORY)\n"
                  "libc = ctypes.CDLL(None, use_errno=True)\n"
                  "libc.syscall.restype = ctypes.c_long\n"
                  "ctypes.set_errno(0)\n"
                  "rc = libc.syscall(424, fd, signal.SIGKILL, 0, 0)\n"
                  "err = ctypes.get_errno()\n"
                  "os.close(fd)\n"
                  "raise RuntimeError(f'pidfd rc={rc} errno={err}')",
                  "pidfd")

    assert err.type == "script_error"
    # EPERM (1) from the filter. Unfiltered this returns 0 and the test
    # process — which IS the server here — is gone.
    assert "rc=-1" in err.message, err.message
    assert "errno=1" in err.message, err.message
    # The clinching assertion: this process is still running and answering.
    assert client.request("ping", {})["ok"] is True


def test_fork_child_inherits(battery):
    """Landlock and seccomp are inherited across fork AND exec — a script
    cannot escape by delegating."""
    client, root = battery
    err = _denied(client, root,
                  "import os\n"
                  "pid = os.fork()\n"
                  "if pid == 0:\n"
                  "    try:\n"
                  "        open('/usr/pwned', 'w', encoding='utf-8').write('x')\n"
                  "        os._exit(0)\n"
                  "    except BaseException:\n"
                  "        os._exit(9)\n"
                  "status = os.waitpid(pid, 0)[1]\n"
                  "raise RuntimeError(f'child {status}')", "fork")
    assert err.type == "script_error"
    assert "child 0" not in err.message   # a non-zero wait status: it was denied
    assert not Path("/usr/pwned").exists()

    err = _denied(client, root,
                  "import subprocess, sys\n"
                  "done = subprocess.run([sys.executable, '-c',\n"
                  "                       'import socket; socket.socket()'])\n"
                  "raise RuntimeError(f'exec rc {done.returncode}')", "exec")
    assert "exec rc 0" not in err.message


# ---------------------------------------------------------- the two postures

@pytest.fixture(scope="module")
def hosted(tmp_path_factory):
    """A second worker under the `hosted` read posture, plus a fake state dir
    OUTSIDE its project root — the file FR5 exists to hide."""
    client, root = _client(tmp_path_factory, "hosted", posture="hosted")
    state = root.parent / "state"
    state.mkdir(exist_ok=True)
    secret = state / "secret.key"
    secret.write_text("the session signing key", encoding="utf-8")
    yield client, root, secret
    client.stop()


def test_hosted_posture_hides_state_dir(hosted):
    client, root, secret = hosted
    if not client.sandboxed and os.environ.get("AGENTCAD_EXPECT_SANDBOX") != "active":
        pytest.skip(f"not confined; report {client.sandbox_report!r}")

    assert client.sandbox_report["posture"] == "hosted"
    # No failures means every entry of the allow-list that survived the
    # existence filter was actually granted — a root the kernel refused would
    # be a read the worker silently lost.
    assert client.sandbox_report["failures"] == []
    # A read denial, not a write one: with global read, this file is how a
    # member forges any session (the same uid runs the server and the worker).
    err = _denied(client, root,
                  f"open({str(secret)!r}, encoding='utf-8').read()", "secret")
    assert err.type == "script_error"
    assert err.details["denied"] == "filesystem"

    # ...while everything a part script legitimately needs still works.
    result = _build(client, _script(
        "open('/etc/hostname', encoding='utf-8').read()"
    ), root / ".cache" / "hosted.acm")
    assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)


# ------------------------------------------------------------- the opt-out

@pytest.fixture(scope="module")
def unconfined(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_NO_SANDBOX", "1")
    root = tmp_path_factory.mktemp("unconfined")
    try:
        client = KernelClient(writable_dirs=[str(root)],
                              quotas=resolve(QUOTAS, env={}, config={}))
    finally:
        patch.undo()
    client.start()
    yield client, root
    client.stop()


def test_no_sandbox_env_reports_off(unconfined):
    """The kill switch drops confinement — and says so — while the quota
    payload still travels: opting out of the sandbox is not opting out of the
    caps."""
    client, root = unconfined
    assert client.sandboxed is False
    assert client.sandbox_report.get("landlock_abi") is None
    assert client.sandbox_report.get("seccomp") is None
    assert client.sandbox_report["rlimits"] == ["RLIMIT_AS", "RLIMIT_NPROC"]

    # Creating a socket is not a network round trip; it is the syscall the
    # filter denies, so its success here is what proves the filter is absent.
    result = _build(client, _script("import socket\nsocket.socket().close()"),
                    root / ".cache" / "sock.acm")
    assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)


# ------------------------------------------------------------ the quota tiers

@pytest.fixture(scope="module")
def rlimited(tmp_path_factory):
    """A worker whose address space is capped well below the supervisor's RSS
    cap, so `RLIMIT_AS` is the tier that answers.

    2048 MiB, not the 1536 the spike measured as the floor: 1.25 GiB failed
    during `import build123d` and 1.5 GiB passed *by settling* (VmSize adapted
    to 1 543 272 kB), both on arm64 in this image. A test pinned to a measured
    floor is a test that flakes on the next architecture, and nothing here
    needs the tightest possible cap — it needs one below the 4 GiB balloon and
    above a real build.
    """
    client, root = _client(tmp_path_factory, "rlimit-as",
                           quotas={"address_space_mb": 2048, "memory_mb": 8192,
                                   "pids_headroom": 64})
    yield client, root
    client.stop()


@pytest.mark.timeout(300)
def test_rlimit_as_makes_a_balloon_recoverable(rlimited):
    """The single best property of the rlimit tier: a memory-hungry script
    becomes an ordinary `script_error` with a line number, and the warm worker
    — seven seconds of OCCT import — is not lost. That is why `RLIMIT_AS` is
    deliberately loose (3x the memory cap by default) rather than being the
    cap: it exists to turn a runaway *reservation* into a `MemoryError`.
    """
    client, root = rlimited
    err = _denied(client, root, "b = bytearray(4 << 30)", "balloon")

    assert err.type == "script_error"
    assert err.details["denied"] == "memory"
    assert "MemoryError" in err.message
    assert client.alive
    assert _build(client, BOX, root / ".cache" / "after-balloon.acm")["metrics"]


@pytest.fixture(scope="module")
def fork_limited(tmp_path_factory):
    client, root = _client(tmp_path_factory, "rlimit-nproc",
                           quotas={"memory_mb": 1024, "pids_headroom": 32})
    yield client, root
    client.stop()


@pytest.mark.timeout(600)
def test_rlimit_nproc_stops_a_fork_loop(fork_limited):
    """`RLIMIT_NPROC` is per-*uid* and counts threads, so the number is the
    live uid count measured at spawn plus the headroom — a fixed 32 killed the
    worker during `import build123d` in the spike. A script that forks past it
    gets EAGAIN in its own frame and the worker survives.
    """
    client, root = fork_limited
    err = _denied(client, root,
                  "import os, signal, time\n"
                  "kids = []\n"
                  "try:\n"
                  "    for _ in range(200):\n"
                  "        pid = os.fork()\n"
                  "        if pid == 0:\n"
                  "            time.sleep(30)\n"
                  "            os._exit(0)\n"
                  "        kids.append(pid)\n"
                  "finally:\n"
                  # The seccomp filter allows a signal at a script's own
                  # children (a positive pid that is not the server's).
                  "    for pid in kids:\n"
                  "        try:\n"
                  "            os.kill(pid, signal.SIGKILL)\n"
                  "            os.waitpid(pid, 0)\n"
                  "        except OSError:\n"
                  "            pass\n",
                  "forkloop")

    assert err.type == "script_error"
    assert err.details["denied"] == "process_count"
    assert client.alive
    assert _build(client, BOX, root / ".cache" / "after-forks.acm")["metrics"]


def _delegated_cgroup() -> str | None:
    """The operator-delegated cgroup directory, if there is a usable one."""
    path = os.environ.get("AGENTCAD_CGROUP_DIR", "").strip()
    if not path or path.lower() == "off":
        return None
    return path if (os.path.isdir(path)
                    and os.access(path, os.W_OK | os.X_OK)) else None


@pytest.mark.timeout(600)
@pytest.mark.skipif(_delegated_cgroup() is None,
                    reason="no delegated cgroup v2 subtree "
                           "(set AGENTCAD_CGROUP_DIR; see the docstring)")
def test_cgroup_tier_when_delegated(tmp_path_factory):
    """The one tier that OOM-kills, exercised for real. Needs Decision 4's
    Model 2 — a host-delegated subtree, no capabilities added to the container:

        # on the host, once
        sudo mkdir -p /sys/fs/cgroup/agentcad
        echo "+memory +pids +cpu" | sudo tee /sys/fs/cgroup/cgroup.subtree_control
        echo "+memory +pids +cpu" | sudo tee /sys/fs/cgroup/agentcad/cgroup.subtree_control
        sudo chown -R 10001:10001 /sys/fs/cgroup/agentcad

        docker run --rm --cgroup-parent=/agentcad \\
          -v /sys/fs/cgroup/agentcad:/cg:rw -e AGENTCAD_CGROUP_DIR=/cg \\
          -e AGENTCAD_EXPECT_SANDBOX=active -v "$PWD":/src:ro -w /tmp \\
          agentcad:local sh -c '...scripts/linux-test.sh body...'

    It skips in the default container on purpose: `/sys/fs/cgroup` is mounted
    read-only and root-owned there, and the alternative route to a writable
    subtree is `--cap-add SYS_ADMIN`, which is a near-root capability this
    project refuses to ask for. The *detection and fallback* path is covered
    unconditionally by `test_cgroup_probe_falls_back_honestly` and by
    `tests/test_sandbox_plan.py`'s cgroup section.

    `memory_mb` is 1024, not the 512 the plan sketched: a warm worker is
    451-482 MB RSS and 499 MB after a build, so a 512 MB cgroup would OOM-kill
    it during `import build123d` and prove nothing about a balloon. The
    address-space tier is switched off so `RLIMIT_AS` cannot answer first.
    """
    client, root = _client(tmp_path_factory, "cgroup",
                           quotas={"memory_mb": 1024, "address_space_mb": "off",
                                   "pids": 64, "pids_headroom": 64})
    try:
        assert client._plan.quotas["mechanism"].startswith("cgroup")
        cg_dir = Path(client._plan.backend.cg_dir)
        assert cg_dir.parent == Path(_delegated_cgroup())
        assert (cg_dir / "memory.max").read_text(
            encoding="utf-8").strip() == str(1024 * 1024 * 1024)
        assert (cg_dir / "memory.swap.max").read_text(
            encoding="utf-8").strip() == "0"
        assert _build(client, BOX, root / ".cache" / "box.acm")["metrics"]

        # 4 GiB, every page touched: an untouched allocation charges nothing.
        with pytest.raises(KernelError) as exc_info:
            _build(client, _script(
                "b = bytearray(4 << 30)\n"
                "b[::4096] = b'\\x01' * (len(b) // 4096 + 1)\n"
            ), root / ".cache" / "balloon.acm")
        err = exc_info.value

        assert err.type == "kernel_crash"
        # The kernel kills at the charge, so RSS never reaches the supervisor's
        # sample — with a cgroup in force it is always the cgroup that answers.
        assert err.details["reason"] == "memory_cap"
        assert err.details["tier"] == "cgroup"
        assert err.details["usage"]["wall_ms"] > 0
        assert _build(client, BOX, root / ".cache" / "after.acm")["metrics"]
    finally:
        client.stop()


def test_cgroup_probe_falls_back_honestly(tmp_path):
    """The operator asked for the tier and did not get it. Falling back is
    right; falling back quietly is not — the warning names the directory, and
    `mechanism` never says `cgroup` for a cgroup that was not made.
    """
    from agentcad.kernel import sandbox_linux

    refused = tmp_path / "not-a-cgroup"
    refused.mkdir()
    refused.chmod(0o500)
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CGROUP_DIR", str(refused))
    try:
        warnings: list[str] = []
        assert sandbox_linux.CgroupTier.probe(warnings) is None
        assert any(str(refused) in warning for warning in warnings), warnings

        client = KernelClient(writable_dirs=[str(tmp_path)],
                              quotas=resolve(QUOTAS, env={}, config={}))
    finally:
        patch.undo()
        refused.chmod(0o700)
    try:
        assert client._plan.quotas["mechanism"] == "rlimit+supervisor"
        assert client._plan.backend.cg_dir is None
        assert any(str(refused) in warning for warning in client._plan.warnings)
    finally:
        client.stop()


# ------------------------------------------------------------- the metering

def test_every_response_carries_usage_from_a_confined_worker(battery):
    """`/proc/self/clear_refs` needs its own Landlock file rule, so this is
    also the assertion that the extra-file grants landed: without them the
    peak would silently fall back to the lifetime high-water mark."""
    client, root = battery
    _build(client, BOX, root / ".cache" / "usage.acm")
    usage = client.last_usage
    assert usage["cpu_ms"] > 0 and usage["wall_ms"] > 0
    assert usage["peak_rss_mb"] > 0
    assert usage["peak_rss_is_lifetime"] is False
