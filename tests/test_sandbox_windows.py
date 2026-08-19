"""PRD-006/006b — the Windows battery: a real worker in a real AppContainer.

Windows CI only (`ci.yml` runs `-m portability` on `windows-latest`); the
*shape* of the same plan is asserted on every OS in
`tests/test_sandbox_plan.py`, with the Win32 entry points stubbed. What cannot
be faked, and is what this file exists for, is the OS actually refusing:

* the **job object** capping *committed* memory, so a breach is an allocation
  that fails inside the script rather than a process that dies, and the warm
  worker survives it (PRD-006);
* the **lowbox token** refusing the network (no ``INTERNET_CLIENT`` capability
  -> ``WSAEACCES``) and every path that carries no ACE for the package SID
  (PRD-006b), while a normal build and a STEP export still succeed — the
  load-bearing negative, because OCCT resolves DLLs from directories nobody
  enumerated.

Honesty (design spec, Decision 3/8): `confinement.status == "active"` is only
ever asserted from the worker's **own** ``TokenIsAppContainer`` self-report,
and under ``AGENTCAD_EXPECT_SANDBOX=active`` (which `ci.yml` sets on this row)
anything less is a red test rather than a skipped one.

**A residual this file does not paper over.** The package SID is *per
installation*, not per worker (design spec, Decision 2 — per-worker profiles
are explicitly out of scope), so the ACE one plan puts on its private temp dir
is an ACE for every worker of the same installation. The isolation proven
below is therefore "a directory this installation granted to nobody", which is
what protects the machine; two concurrent workers are isolated from each other
by DAC and by nothing else, unlike macOS's per-worker seatbelt profile and
Linux's per-worker Landlock ruleset.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

from agentcad.kernel import client as client_module
from agentcad.kernel import sandbox
from agentcad.kernel.client import KernelClient, KernelError
from agentcad.kernel.quotas import resolve

pytestmark = [
    pytest.mark.portability,
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "win32",
                       reason="the job object and the AppContainer are "
                              "Windows-only"),
]

BOX = """\
from build123d import *

PARAMS = {}

def build(p):
    with BuildPart() as part:
        Box(10, 10, 10)
    return part.part
"""

BALLOON = """\
from build123d import *

PARAMS = {}

def build(p):
    b = bytearray(3 << 30)
    with BuildPart() as part:
        Box(10, 10, 10)
    return part.part
"""

QUOTAS = {"memory_mb": 1024, "pids": 32}

#: The first ping pays for `import build123d` **inside the container**, where
#: every DLL open is an access check the ordinary path does not make. The
#: probe measured a warm worker at ~7 s and 418 MB, but this is deliberately
#: generous: a first-ping timeout is a red build that says nothing.
FIRST_PING_TIMEOUT_S = 300.0


def _hostile(body: str) -> str:
    """A part script whose `build(p)` does one forbidden thing.

    Nothing is caught: the denial has to reach the worker's own
    `_script_error_from_exc`, which is what attaches `details.denied`. Where
    the operation does *not* raise, the body raises with the outcome, so a
    silent success can never be mistaken for a pass.
    """
    indented = "\n".join("    " + line if line.strip() else ""
                         for line in body.strip("\n").splitlines())
    return "PARAMS = {}\n\n\ndef build(p):\n" + indented + "\n"


def _expect_sandbox() -> str | None:
    value = os.environ.get("AGENTCAD_EXPECT_SANDBOX", "").strip()
    return value or None


def _requires_container(client) -> None:
    """Skip a containment assertion on a box that is not confining anything.

    Under ``AGENTCAD_EXPECT_SANDBOX=active`` (what `ci.yml` sets on this row)
    nothing is skipped: a worker that failed to confine itself has to be a red
    test, which is the whole point of the gate (Decision 13). Without it — a
    contributor's machine where `icacls` is missing, a profile could not be
    created, or `AGENTCAD_NO_SANDBOX` is set in the ambient environment — a
    denial that does not happen is not a failure to report, because there is
    nothing denying anything.
    """
    if _expect_sandbox() == "active":
        return
    status = sandbox.report(client)["status"]
    if status != "active":
        pytest.skip(f"this worker is not confined (sandbox status {status!r}), "
                    f"so there is no denial to assert; set "
                    f"AGENTCAD_EXPECT_SANDBOX=active to make that a failure")


@pytest.fixture(scope="module")
def capped(tmp_path_factory):
    """One confined, capped worker for the whole file (an OCCT import each).

    `pytest.MonkeyPatch` rather than the fixture: confinement is decided at
    construction, so the environment only has to be right for that one call —
    and the first-ping deadline only for the start.
    """
    root = tmp_path_factory.mktemp("windows-sandbox")
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CONFIG", str(root / "no-such-config.json"))
    patch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    patch.setattr(client_module, "STARTUP_TIMEOUT_S", FIRST_PING_TIMEOUT_S)
    try:
        client = KernelClient(writable_dirs=[str(root)],
                              quotas=resolve(QUOTAS, env={}, config={}))
        client.start()
    finally:
        patch.undo()
    yield client, root
    client.stop()
    _uninstall(client._plan)


def _uninstall(plan) -> None:
    """Undo what the plan installed on this machine: the ACEs, then the profile.

    The documented uninstall recipe (`docs/deployment.md`), run for real — so
    this suite leaves a dev box (and the runner) as it found it instead of
    accumulating one profile and one dead-SID ACE on `sys.prefix` and the
    checkout per run, and so the recipe itself is exercised somewhere. The
    *product* never does this: the profile is per installation and shared by
    concurrent clients (Decision 2).

    Best-effort by construction — a teardown that fails a green suite would be
    the worst of both.
    """
    from agentcad.kernel import sandbox_windows

    profile = getattr(plan.backend, "profile", None)
    if profile is None:
        return
    for path in sandbox_windows._read_roots():
        sandbox_windows.acl_revoke(path, profile.sid_str)
    try:
        sandbox_windows.AppContainerProfile.delete(profile.name)
    except OSError:
        pass


@pytest.fixture(scope="module")
def unconfined(tmp_path_factory):
    """`AGENTCAD_NO_SANDBOX=1`: no AppContainer, and the quotas still on."""
    root = tmp_path_factory.mktemp("windows-no-sandbox")
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CONFIG", str(root / "no-such-config.json"))
    patch.setenv("AGENTCAD_NO_SANDBOX", "1")
    patch.setattr(client_module, "STARTUP_TIMEOUT_S", FIRST_PING_TIMEOUT_S)
    try:
        client = KernelClient(writable_dirs=[str(root)],
                              quotas=resolve(QUOTAS, env={}, config={}))
        client.start()
    finally:
        patch.undo()
    yield client, root
    client.stop()


def _build(client, script, mesh_path, timeout_s=None):
    return client.request("build", {"script": script, "params": {},
                                    "mesh_path": str(mesh_path)},
                          timeout_s=timeout_s)


def _denial(client, body, mesh_path, timeout_s=120.0) -> KernelError:
    """Run a hostile script and return the `script_error` it raised."""
    with pytest.raises(KernelError) as exc_info:
        _build(client, _hostile(body), mesh_path, timeout_s=timeout_s)
    error = exc_info.value
    assert error.type == "script_error", error.to_payload()
    return error


# ------------------------------------------------------------ the plan itself

def test_the_plan_confines_with_an_appcontainer_and_caps_with_a_job_object(
        capped):
    """AC3's Windows clause, from the worker's own token.

    `client.sandboxed` is only true because the worker read
    `TokenIsAppContainer` off itself and said so on `ping` — the plan's intent
    alone can never produce it (Decision 3).
    """
    client, _root = capped
    plan = client._plan
    report = sandbox.report(client)

    assert plan.quotas["status"] == "active"
    assert plan.quotas["mechanism"] == "job_object+supervisor"
    assert plan.quotas["limits"]["memory_mb"] == 1024
    assert plan.backend.job is not None
    assert report["quotas"] == plan.quotas

    if _expect_sandbox() == "active":
        assert plan.confinement["status"] == "active", plan.confinement
        assert plan.confinement["mechanism"] == "appcontainer"
        assert plan.confinement["detail"]["sid"].startswith("S-1-15-2-")
        assert client.sandbox_report["appcontainer"] is True, \
            client.sandbox_report
        # The SID the worker measured is the SID the parent granted.
        assert (client.sandbox_report["appcontainer_sid"]
                == plan.confinement["detail"]["sid"])
        assert client.sandboxed is True
        assert report["status"] == "active", report
        assert report["mechanism"] == "appcontainer"
        assert report["confinement"]["detail"]["appcontainer"] is True
        assert report["warnings"] == [], report["warnings"]
    else:
        # Still honest when it is not active: a mechanism beside `off` would
        # claim something is in force, and a status with no reason reads as a
        # bug in the sandbox rather than a machine that could not do it.
        assert report["status"] in ("active", "off", "unsupported"), report
        if report["status"] != "active":
            assert report["mechanism"] is None, report
            assert report["confinement"]["detail"]["reason"], report


def test_the_supervisor_can_sample_a_windows_worker(capped):
    """psapi's working set, through the same `Backend.rss_bytes` seam the
    supervisor calls on every sample.

    The bound is 100 MB and it is the point of the test: a venv `python.exe`
    (uv-managed ones included) is a **launcher** that starts the real
    interpreter as a child, so `GetProcessMemoryInfo` on the process handle
    measures a ~3.9 MB stub — which is exactly what this asserted before
    (changelog 0238). The worker here has build123d imported and is several
    hundred MB; sampling the job's processes is what makes that visible, and a
    stub-only sample now fails loudly instead of passing a sanity bound.
    """
    client, _root = capped
    rss = client._plan.backend.rss_bytes(client._proc)
    assert isinstance(rss, int)
    assert 100 * 1024 * 1024 < rss < 8 * 1024 ** 3    # a CPython with OCCT in it


# ------------------------------------------------------------- AC2, the build

def test_a_normal_build_and_a_step_export_work_inside_the_container(capped):
    """AC2, and the load-bearing negative of the whole PRD: OCCT imports,
    tessellates and writes a STEP file with nothing but the granted roots —
    a DLL resolved from a directory nobody granted would fail *here*."""
    client, root = capped
    result = _build(client, BOX, root / "box.acm", timeout_s=180.0)
    assert result["metrics"]
    assert (root / "box.acm").stat().st_size > 0

    out = root / "exports" / "box.step"
    out.parent.mkdir(parents=True, exist_ok=True)
    exported = client.request("export", {"script": BOX, "params": {},
                                         "format": "step", "out_path": str(out),
                                         "tolerance": 0.05}, timeout_s=180.0)
    assert exported["size_bytes"] > 0
    assert out.stat().st_size > 0


# ----------------------------------------------------------- AC1, the battery

def test_the_network_is_denied(capped):
    """No ``INTERNET_CLIENT`` capability, so Winsock answers ``WSAEACCES``
    (`[WinError 10013]`) — which is neither `[Errno 1]` nor `[Errno 13]`, and
    is why `denials.classify` grew a rule for it."""
    client, root = capped
    _requires_container(client)
    error = _denial(client,
                    "import socket\n"
                    "socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
                    "raise RuntimeError('NO DENIAL: the connect succeeded')\n",
                    root / "network.acm")
    assert "PermissionError" in error.message, error.message
    assert "10013" in error.message, error.message
    if _expect_sandbox() == "active":
        assert error.details["denied"] == "network", error.details
    assert client.alive


def test_a_write_outside_the_granted_roots_is_denied(capped):
    """`C:\\Users\\Public` is world-writable for an ordinary user and carries
    no ACE for the package SID, so the container cannot touch it."""
    client, root = capped
    _requires_container(client)
    target = "C:\\Users\\Public\\agentcad-pwned.txt"
    error = _denial(client,
                    f"open({target!r}, 'w', encoding='utf-8').write('x')\n"
                    "raise RuntimeError('NO DENIAL: the write succeeded')\n",
                    root / "public.acm")
    assert "PermissionError" in error.message, error.message
    if _expect_sandbox() == "active":
        assert error.details["denied"] == "filesystem", error.details
    assert not os.path.exists(target)
    assert client.alive


def test_a_write_into_another_workers_scratch_is_denied(capped):
    """A directory named like a worker's private temp dir but granted to
    nobody: the machine's scratch space is not the container's.

    See the module docstring for what this does *not* prove — the package SID
    is per installation, so a directory another plan of the **same** install
    granted is reachable, and only DAC separates two live workers.
    """
    client, root = capped
    _requires_container(client)
    other = tempfile.mkdtemp(prefix=sandbox.TMP_PREFIX)
    try:
        error = _denial(client,
                        f"import os\n"
                        f"open(os.path.join({other!r}, 'x'), 'w', "
                        f"encoding='utf-8').write('x')\n"
                        "raise RuntimeError('NO DENIAL: the write succeeded')\n",
                        root / "other-tmp.acm")
        assert "PermissionError" in error.message, error.message
        if _expect_sandbox() == "active":
            assert error.details["denied"] == "filesystem", error.details
        assert os.listdir(other) == []
    finally:
        for name in os.listdir(other):
            os.unlink(os.path.join(other, name))
        os.rmdir(other)
    assert client.alive


def test_a_child_process_inherits_the_container(capped):
    """The token is inherited, so a script that shells out gains nothing: the
    child's own connect fails and its exit code is non-zero. A `rc=0` here
    would mean the confinement stops at the process boundary."""
    client, root = capped
    _requires_container(client)
    error = _denial(
        client,
        "import subprocess, sys\n"
        "completed = subprocess.run(\n"
        "    [sys.executable, '-c',\n"
        "     \"import socket;socket.create_connection(('1.1.1.1',80),"
        "timeout=3)\"],\n"
        "    capture_output=True, text=True, encoding='utf-8',\n"
        "    errors='replace', timeout=90)\n"
        "raise RuntimeError('CHILD rc=%r' % completed.returncode)\n",
        root / "child.acm", timeout_s=180.0)
    assert "CHILD rc=" in error.message, error.message
    assert "rc=0" not in error.message, error.message
    assert client.alive


def test_the_private_temp_dir_is_writable_and_is_where_tempfile_lands(capped):
    """The other side of the same coin: everything the worker legitimately
    needs a scratch file for must work. `%TEMP%` is redirected by the lowbox
    token into `<private tmp>\\Packages\\<name>\\AC\\Temp`, which the plan's
    `prepare_tmp` creates and grants."""
    client, root = capped
    tmp_dir = client._plan.tmp_dir
    error = _denial(client,
                    "import os, tempfile\n"
                    "handle = tempfile.NamedTemporaryFile(mode='w', "
                    "encoding='utf-8', suffix='.probe', delete=False)\n"
                    "handle.write('x')\n"
                    "handle.close()\n"
                    "raise RuntimeError('WROTE %s' % handle.name)\n",
                    root / "tmp.acm")
    assert "WROTE" in error.message, error.message
    assert tmp_dir.lower() in error.message.lower(), error.message


def test_a_balloon_over_the_commit_limit_is_a_recoverable_script_error(capped):
    """The job object's best property, the mirror of Linux's `RLIMIT_AS`: the
    allocation fails, so the breach is a `MemoryError` with a line number and
    the worker — a warm one, seconds of OCCT import — is still there."""
    client, root = capped
    with pytest.raises(KernelError) as exc_info:
        _build(client, BALLOON, root / "balloon.acm", timeout_s=180.0)
    err = exc_info.value

    assert err.type == "script_error"
    assert "MemoryError" in err.message
    # The parent installed the cap, so the worker was told a quota is in force
    # — otherwise this would read as the machine running out of memory.
    assert err.details["denied"] == "memory"
    assert client.alive
    assert _build(client, BOX, root / "after.acm", timeout_s=180.0)["metrics"]


# --------------------------------------------------------------- AC4, opt-out

def test_the_opt_out_drops_the_container_and_keeps_the_job_object(unconfined):
    """`AGENTCAD_NO_SANDBOX=1` opts out of the **confinement**. The job object
    stays — a runaway script may not take the machine down whether or not the
    operator trusts it with the filesystem — and the balloon still comes back
    as a named `memory` denial."""
    client, root = unconfined
    report = sandbox.report(client)

    assert report["status"] == "off", report
    assert report["mechanism"] is None
    assert report["confinement"]["detail"]["reason"] == "AGENTCAD_NO_SANDBOX"
    assert client.sandboxed is False
    assert report["quotas"]["mechanism"] == "job_object+supervisor"
    assert client._plan.backend.job is not None
    assert client._plan.backend.profile is None

    with pytest.raises(KernelError) as exc_info:
        _build(client, BALLOON, root / "balloon.acm", timeout_s=180.0)
    assert exc_info.value.details["denied"] == "memory"
    assert client.alive

    # ...and with no container, an ordinary write outside the roots is an
    # ordinary success: the opt-out really did opt out.
    assert _build(client, BOX, root / "box.acm", timeout_s=180.0)["metrics"]
