"""macOS seatbelt confinement of kernel workers (agentcad/kernel/sandbox.py).

Real sandbox-exec runs, darwin-only. A dedicated sandboxed KernelClient is
built here — the shared session ``kernel`` fixture stays unsandboxed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from agentcad.kernel import sandbox
from agentcad.kernel.client import KernelClient, KernelError

from .conftest import BOX_SCRIPT, make_test_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        sys.platform != "darwin", reason="sandbox-exec confinement is macOS-only"
    ),
]

AL_DENSITY = 2.70
PROBE = Path.home() / "agentcad_sandbox_probe.txt"


@pytest.fixture(scope="module")
def writable(tmp_path_factory):
    return tmp_path_factory.mktemp("sandboxed")


@pytest.fixture(scope="module")
def sboxed(writable):
    """A KernelClient confined to ``writable`` (+ its own private temp dir).

    Not the *system* temp dir: since PRD-006 the plan grants only the worker's
    own ``agentcad-worker-*`` directory, because a shared ``/tmp`` grant let
    every worker read and overwrite every sibling's scratch.
    """
    mp = pytest.MonkeyPatch()
    # Isolate from the developer's real ~/.agentcad/config.json and env.
    mp.setenv("AGENTCAD_CONFIG", str(writable / "no-such-config.json"))
    mp.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    try:
        client = KernelClient(writable_dirs=[str(writable)])
    finally:
        mp.undo()  # the sandbox decision is made at construction
    client.start()
    yield client
    client.stop()


def _build(client, script, mesh_path, params=None):
    return client.request(
        "build",
        {
            "script": script,
            "params": params or {},
            "density_g_cm3": AL_DENSITY,
            "mesh_path": str(mesh_path),
        },
    )


# 1 — the worker starts and answers under confinement
def test_ping_under_sandbox(sboxed):
    assert sboxed.sandboxed is True
    result = sboxed.request("ping", {})
    assert result["ok"] is True
    assert result["build123d"]


# 1b — the preamble runs INSIDE the seatbelt (PRD-006, Decision 1)
#
# The design claims rlimits are not a seatbelt-governed operation, so the same
# in-process preamble that confines a Linux worker can apply macOS's quota
# tier from inside sandbox-exec. This is that claim's verification point: the
# report comes from the worker's own `setrlimit`, not from the plan's
# intention, and `RLIMIT_NPROC` is the one rlimit Darwin actually honours
# (RLIMIT_AS/DATA/RSS are EINVAL there).
def test_the_preamble_applied_the_rlimits_inside_the_seatbelt(sboxed):
    report = sboxed.request("ping", {})["sandbox"]
    assert report["rlimits"] == ["RLIMIT_NPROC"]
    assert report["failures"] == []
    assert report["landlock_abi"] is None and report["seccomp"] is None
    # The seatbelt is applied by the PARENT, before this process ever runs, so
    # it leaves nothing for the worker's own Landlock/seccomp fields to show —
    # `confinement` is the parent's declaration of the two facets it really
    # enforces, carried through by `_preamble.apply_from_env` (the
    # `details.denied` regression: without it a real seatbelt denial has no
    # evidence to point `denials.active_facets` at).
    assert report["confinement"] == ["filesystem", "network"]
    assert sboxed.sandbox_report == report


# 2 — writes inside the writable root work (mesh cache path)
def test_build_writes_mesh_inside_root(sboxed, writable):
    mesh_path = writable / "box.acm"
    result = _build(sboxed, BOX_SCRIPT, mesh_path)
    assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert mesh_path.read_bytes()[:4] == b"ACM1"


# 3 — a script writing outside the roots gets a script_error, and no file lands
def test_write_outside_roots_denied(sboxed, writable):
    """The probe path is spelled out rather than expanded in the worker: since
    PRD-006 the child's ``HOME`` is its private temp dir, so ``~`` inside a
    script is *already* somewhere it may write. The developer's real home —
    the thing confinement is protecting — is this process's ``Path.home()``.
    """
    script = (
        "PARAMS = {}\n"
        "def build(p):\n"
        f"    open({str(PROBE)!r}, 'w', encoding='utf-8').write('x')\n"
        "    return None\n"
    )
    try:
        with pytest.raises(KernelError) as exc_info:
            _build(sboxed, script, writable / "probe.acm")
        err = exc_info.value
        assert err.type == "script_error"
        # the denial must be the OS refusing the open, not some other failure
        assert "PermissionError" in err.details.get("traceback", "") or (
            "Operation not permitted" in err.message
        )
        # The `details.denied` regression: the seatbelt's EACCES/EPERM must
        # still be labelled `filesystem`, even though the worker's own
        # preamble has no landlock_abi to point at (the seatbelt was applied
        # by the parent, not by this process).
        assert err.details.get("denied") == "filesystem"
        assert not PROBE.exists()
    finally:
        if PROBE.exists():  # belt and braces: never leave an escape artifact
            PROBE.unlink()
            pytest.fail("sandboxed worker wrote outside its writable roots")


# 4 — network is denied, and the denial doesn't kill the worker
def test_network_denied_worker_survives(sboxed, writable):
    script = (
        "import socket\n"
        "PARAMS = {}\n"
        "def build(p):\n"
        "    socket.create_connection(('127.0.0.1', 9), timeout=1)\n"
        "    return None\n"
    )
    with pytest.raises(KernelError) as exc_info:
        _build(sboxed, script, writable / "net.acm")
    err = exc_info.value
    assert err.type == "script_error"
    # seatbelt denies with EPERM; without the sandbox this port would refuse
    # the connection (ConnectionRefusedError), so pin the PermissionError
    assert "PermissionError" in err.details.get("traceback", "")
    # The `details.denied` regression: same parent-declared `network` facet as
    # the write-outside-roots case above.
    assert err.details.get("denied") == "network"
    assert sboxed.request("ping", {})["ok"] is True  # same worker, still alive


# 4b — the worker's temp dir is its own, and the shared one is not granted
#
# PRD-006, Decision 1: the v3 profile granted `tempfile.gettempdir()`
# wholesale, so two workers' scripts shared a scratch directory they could
# both read and overwrite. Each worker now gets a private `agentcad-worker-*`
# dir, exported as $TMPDIR (so `tempfile` in the script finds it), and that
# dir — not the shared parent — is what the profile grants.

TEMP_SCRIPT = """\
import os
import tempfile
from build123d import *

PARAMS = {}

def build(p):
    tmp = tempfile.gettempdir()
    assert os.path.basename(tmp).startswith("agentcad-worker-"), tmp
    with open(os.path.join(tmp, "scratch.txt"), "w", encoding="utf-8") as f:
        f.write(tmp)
    with BuildPart() as part:
        Box(10, 10, 10)
    return part.part
"""


def test_the_scripts_temp_dir_is_the_workers_private_one(sboxed, writable):
    result = _build(sboxed, TEMP_SCRIPT, writable / "temp.acm")
    assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)

    private = Path(sboxed._plan.tmp_dir)
    assert private.name.startswith("agentcad-worker-")
    scratch = private / "scratch.txt"
    # The write succeeded *and* it landed in this client's own directory.
    assert scratch.read_text(encoding="utf-8") == str(private)


@pytest.mark.parametrize("where", ["inside", "beside"])
def test_the_shared_temp_tree_is_not_writable(sboxed, writable, where):
    """`inside` is the regression this closes: a path in the system temp dir
    itself, which the v3 profile granted. `beside` is a sibling of it under
    the same /var/folders tree, which it never did."""
    shared = Path(tempfile.gettempdir())
    probe = (shared if where == "inside" else shared.parent) / "agentcad-probe.txt"
    script = (
        "PARAMS = {}\n"
        "def build(p):\n"
        f"    open({str(probe)!r}, 'w', encoding='utf-8').write('x')\n"
        "    return None\n"
    )
    try:
        with pytest.raises(KernelError) as exc_info:
            _build(sboxed, script, writable / "shared.acm")
        err = exc_info.value
        assert err.type == "script_error"
        assert "PermissionError" in err.details.get("traceback", "")
        assert not probe.exists()
    finally:
        if probe.exists():
            probe.unlink()
            pytest.fail("the sandboxed worker wrote into the shared temp tree")


def test_stop_removes_the_private_temp_dir(writable, monkeypatch):
    monkeypatch.setenv("AGENTCAD_CONFIG", str(writable / "no-such-config.json"))
    monkeypatch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    client = KernelClient(writable_dirs=[str(writable)])
    private = Path(client._plan.tmp_dir)
    client.start()
    assert private.is_dir()
    client.stop()
    assert not private.exists()


# 5 — timeout kill + respawn still works under confinement
def test_timeout_kills_and_recovers_sandboxed(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "no-such-config.json"))
    monkeypatch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    client = KernelClient(writable_dirs=[str(tmp_path)])
    assert client.sandboxed is True
    client.start()
    try:
        with pytest.raises(KernelError) as exc_info:
            client.request(
                "build",
                {"script": "while True:\n    pass\n", "params": {},
                 "mesh_path": str(tmp_path / "hang.acm")},
                timeout_s=3.0,
            )
        assert exc_info.value.type == "timeout"
        assert client.request("ping", {})["ok"] is True  # respawned, sandboxed
        assert client.sandboxed is True
    finally:
        client.stop()


# 6 — AGENTCAD_NO_SANDBOX=1 disables: new client spawns unsandboxed
def test_env_kill_switch_disables(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "1")
    client = KernelClient(writable_dirs=[str(tmp_path)])
    assert client.sandboxed is False
    client.start()
    try:
        assert client.request("ping", {})["ok"] is True
    finally:
        client.stop()
    assert sandbox.status(client.sandboxed) == "off"


# status(): module-level semantics
def test_status_reflects_availability(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    assert sandbox.available() is True
    assert sandbox.status() == "active"  # what a NEW client would get
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "1")
    assert sandbox.status() == "off"
    # config file opt-out (env unset)
    monkeypatch.delenv("AGENTCAD_NO_SANDBOX")
    (tmp_path / "cfg.json").write_text('{"sandbox": false}', encoding="utf-8")
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    assert sandbox.status() == "off"
    # env wins over config in both directions
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "0")
    assert sandbox.status() == "active"


# /api/health reports the ACTUAL kernel state
def test_health_reports_active_for_sandboxed_kernel(sboxed, tmp_path):
    from fastapi.testclient import TestClient

    from agentcad.core.tools import build_registry
    from agentcad.server.app import create_app

    service = make_test_service(tmp_path / "projects", sboxed)
    app = create_app(
        service, build_registry(service), extra_allowed_hosts={"testserver"}
    )
    data = TestClient(app, base_url="http://127.0.0.1").get("/api/health").json()
    # Since PRD-006 the field is the honest per-facet object; its top-level
    # `status` still means the confinement's, which is what it always meant.
    assert data["sandbox"]["status"] == "active"
    assert data["sandbox"]["confinement"]["mechanism"] == "seatbelt"


# 7 — PRD-007 merge: the one state-dir subtree a worker may write
#
# `sboxed` above pins its own roots (`writable_dirs=[str(writable)]`), so it
# proves nothing about `cli._writable_roots`'s new grant. This is a second,
# equally cheap module-scoped client built the way `cmd_serve` actually builds
# one — through `_writable_roots` — with `AGENTCAD_STATE_DIR` pointed at an
# isolated tmp tree, so it can prove both halves under a real seatbelt: a
# script CAN write inside `<state>/publications/build` (PRD-007's
# share-build store) and CANNOT write elsewhere in the state dir.


@pytest.fixture(scope="module")
def state_root(tmp_path_factory):
    return tmp_path_factory.mktemp("prd007-state")


@pytest.fixture(scope="module")
def sboxed_publications(tmp_path_factory, state_root):
    from agentcad import cli

    projects = tmp_path_factory.mktemp("prd007-projects")
    mp = pytest.MonkeyPatch()
    mp.setenv("AGENTCAD_CONFIG", str(state_root / "no-such-config.json"))
    mp.setenv("AGENTCAD_STATE_DIR", str(state_root))
    mp.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    try:
        roots = cli._writable_roots(projects)
        client = KernelClient(writable_dirs=roots)
    finally:
        mp.undo()  # the sandbox decision is made at construction
    client.start()
    yield client
    client.stop()


def test_publications_build_subtree_is_writable_under_the_seatbelt(
        sboxed_publications, state_root):
    target = state_root / "publications" / "build" / "proj" / "part.txt"
    script = (
        "import os\n"
        "from build123d import *\n"
        "PARAMS = {}\n"
        "def build(p):\n"
        f"    os.makedirs({str(target.parent)!r}, exist_ok=True)\n"
        f"    open({str(target)!r}, 'w', encoding='utf-8').write('x')\n"
        "    with BuildPart() as part:\n"
        "        Box(10, 10, 10)\n"
        "    return part.part\n"
    )
    result = _build(sboxed_publications, script,
                     state_root / "publications" / "build" / "mesh.acm")
    assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert target.read_text(encoding="utf-8") == "x"


def test_the_rest_of_the_state_dir_stays_unwritable(sboxed_publications,
                                                     state_root):
    probe = state_root / "secret.probe"
    script = (
        "PARAMS = {}\n"
        "def build(p):\n"
        f"    open({str(probe)!r}, 'w', encoding='utf-8').write('x')\n"
        "    return None\n"
    )
    try:
        with pytest.raises(KernelError) as exc_info:
            _build(sboxed_publications, script,
                   state_root / "publications" / "build" / "mesh2.acm")
        err = exc_info.value
        assert err.type == "script_error"
        assert "PermissionError" in err.details.get("traceback", "") or (
            "Operation not permitted" in err.message
        )
        assert err.details.get("denied") == "filesystem"
        assert not probe.exists()
    finally:
        if probe.exists():
            probe.unlink()
            pytest.fail("sandboxed worker wrote outside publications/build")
