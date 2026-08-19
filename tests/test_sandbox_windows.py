"""PRD-006 slice 3 — the Windows battery: a real worker under a real job object.

Windows CI only (`ci.yml` runs `-m portability` on `windows-latest`); the
*shape* of the same plan is asserted on every OS in
`tests/test_sandbox_plan.py`, with the five Win32 entry points stubbed. What
cannot be faked, and is what this file exists for, is the kernel actually
refusing the commit — a job object caps *committed memory*, so a breach is an
allocation that fails inside the script rather than a process that dies, and
the warm worker survives it.

Confinement is `unsupported` here and is asserted as such: AppContainer with
CPython and OCCT is carved out as PRD-006b (design spec, Decision 7/12), and
"folder as status" means health says so rather than implying a switch.
"""

from __future__ import annotations

import sys

import pytest

from agentcad.kernel import sandbox
from agentcad.kernel.client import KernelClient, KernelError
from agentcad.kernel.quotas import resolve

pytestmark = [
    pytest.mark.portability,
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "win32",
                       reason="job-object quotas are Windows-only"),
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


@pytest.fixture(scope="module")
def capped(tmp_path_factory):
    root = tmp_path_factory.mktemp("windows-quotas")
    client = KernelClient(writable_dirs=[str(root)],
                          quotas=resolve(QUOTAS, env={}, config={}))
    client.start()
    yield client, root
    client.stop()


def _build(client, script, mesh_path):
    return client.request("build", {"script": script, "params": {},
                                    "mesh_path": str(mesh_path)})


def test_the_plan_caps_with_a_job_object_and_confines_with_nothing(capped):
    client, _root = capped
    plan = client._plan

    assert plan.confinement["status"] == "unsupported"
    assert plan.confinement["detail"]["note"] == (
        "AppContainer confinement is PRD-006b")
    assert plan.quotas["status"] == "active"
    assert plan.quotas["mechanism"] == "job_object+supervisor"
    assert plan.quotas["limits"]["memory_mb"] == 1024
    assert plan.backend.job is not None
    assert client.sandboxed is False           # nothing claimed, nothing held


def test_the_supervisor_can_sample_a_windows_worker(capped):
    """psapi's working set, through the same `Backend.rss_bytes` seam the
    supervisor calls on every sample.

    The bound is 100 MB and it is the point of the test: a venv `python.exe`
    (uv-managed ones included) is a **launcher** that starts the real
    interpreter as a child, so `GetProcessMemoryInfo` on the `Popen` handle
    measures a ~3.9 MB stub — which is exactly what this asserted before
    (changelog 0238). The worker here has build123d imported and is several
    hundred MB; sampling the job's processes is what makes that visible, and a
    stub-only sample now fails loudly instead of passing a sanity bound.
    """
    client, _root = capped
    rss = client._plan.backend.rss_bytes(client._proc)
    assert isinstance(rss, int)
    assert 100 * 1024 * 1024 < rss < 8 * 1024 ** 3    # a CPython with OCCT in it


def test_a_balloon_over_the_commit_limit_is_a_recoverable_script_error(capped):
    """The job object's best property, the mirror of Linux's `RLIMIT_AS`: the
    allocation fails, so the breach is a `MemoryError` with a line number and
    the worker — a warm one, seven seconds of OCCT import — is still there."""
    client, root = capped
    with pytest.raises(KernelError) as exc_info:
        _build(client, BALLOON, root / "balloon.acm")
    err = exc_info.value

    assert err.type == "script_error"
    assert "MemoryError" in err.message
    # The parent installed the cap, so the worker was told a quota is in force
    # — otherwise this would read as the machine running out of memory.
    assert err.details["denied"] == "memory"
    assert client.alive
    assert _build(client, BOX, root / "after.acm")["metrics"]


def test_report_names_the_tier_and_the_missing_confinement(capped):
    client, _root = capped
    body = sandbox.report(client)

    assert body["status"] == "unsupported"     # the confinement's, as always
    assert body["mechanism"] is None
    assert body["posture"] == "local"
    assert body["quotas"]["mechanism"] == "job_object+supervisor"
    assert body["quotas"]["limits"]["memory_mb"] == 1024
    assert isinstance(body["warnings"], list)
