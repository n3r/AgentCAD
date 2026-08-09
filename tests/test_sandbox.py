"""macOS seatbelt confinement of kernel workers (agentcad/kernel/sandbox.py).

Real sandbox-exec runs, darwin-only. A dedicated sandboxed KernelClient is
built here — the shared session ``kernel`` fixture stays unsandboxed.
"""

from __future__ import annotations

import sys
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
    """A KernelClient confined to ``writable`` (+ system temp dir)."""
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


# 2 — writes inside the writable root work (mesh cache path)
def test_build_writes_mesh_inside_root(sboxed, writable):
    mesh_path = writable / "box.acm"
    result = _build(sboxed, BOX_SCRIPT, mesh_path)
    assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert mesh_path.read_bytes()[:4] == b"ACM1"


# 3 — a script writing outside the roots gets a script_error, and no file lands
def test_write_outside_roots_denied(sboxed, writable):
    script = (
        "import os\n"
        "PARAMS = {}\n"
        "def build(p):\n"
        "    open(os.path.expanduser('~/agentcad_sandbox_probe.txt'), 'w').write('x')\n"
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
    assert sboxed.request("ping", {})["ok"] is True  # same worker, still alive


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
    assert data["sandbox"] == "active"
