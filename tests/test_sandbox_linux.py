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

import os
import sys
from pathlib import Path

import pytest

from agentcad.kernel.client import KernelClient, KernelError
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


def _client(tmp_path_factory, name, **kwargs):
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
                              quotas=resolve(QUOTAS, env={}, config={}),
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


def test_a_root_that_does_not_exist_is_a_lost_grant_and_a_reported_failure(
        tmp_path_factory):
    """Why `cli._writable_roots` creates the projects dir and `~/.agentcad`
    before a worker spawns: on Linux a Landlock rule on a missing path is
    ENOENT, and this is what that costs — the failure lands in the worker's own
    report (so the client reports `off`) AND the root is never granted, so a
    write into it is denied the moment the directory does appear.

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
        assert any(str(absent) in f["error"] and f["stage"] == "landlock"
                   for f in failures), failures
        assert client.sandboxed is False       # a landlock-stage failure
        absent.mkdir()                          # ...and now it exists
        err = _denied(client, root,
                      f"open({str(absent / 'x')!r}, 'w', encoding='utf-8').write('x')",
                      "absent")
        assert err.details["denied"] == "filesystem"
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
