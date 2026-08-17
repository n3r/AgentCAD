"""PRD-005a slice 6: a route pack may declare its own mount prefix.

`_mount_route_packs` hardcodes `prefix="/api"`, so a pack cannot serve anything
at the root. PRD-007 needs exactly that (`/s/<token>` share links), and the
two-line seam is cheaper to land here — with its test — than as a core edit
inside a feature that is really about sharing.

The probe pack is injected through `sys.modules` + a filtered
`pkgutil.iter_modules`, **not** by writing a file into `agentcad/server/`: the
suite runs eight workers against one checkout, and a real file would be visible
to every app every other worker builds.
"""

from __future__ import annotations

import pkgutil
import sys
import types

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from .conftest import make_test_service

PROBE = "routes_zzprefixprobe"


def _client(kernel, tmp_path):
    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module
    from agentcad.server.app import create_app

    security_module.install(None)
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def probe_pack(monkeypatch):
    """Register a fake `agentcad.server.routes_*` module for this test only."""
    import agentcad.server as server_pkg

    module = types.ModuleType(f"agentcad.server.{PROBE}")
    router = APIRouter()

    @router.get("/probe")
    def probe():
        return {"ok": True}

    module.router = router
    monkeypatch.setitem(sys.modules, module.__name__, module)

    server_path = list(server_pkg.__path__)
    real = pkgutil.iter_modules

    def fake(path=None, prefix=""):
        yield from real(path, prefix)
        # Only when the SERVER package is being walked: `_load_tool_packs`
        # walks `agentcad.core` through the same function.
        if path is not None and list(path) == server_path:
            yield pkgutil.ModuleInfo(None, PROBE, False)

    monkeypatch.setattr(pkgutil, "iter_modules", fake)
    return module


def test_a_pack_may_declare_its_own_prefix(kernel, tmp_path, probe_pack):
    """PRD-007 needs `/s/<token>` at the root; a pack cannot express that
    today."""
    probe_pack.PREFIX = ""
    client = _client(kernel, tmp_path)
    assert client.get("/probe").json() == {"ok": True}
    assert client.get("/api/probe").status_code == 404


def test_a_pack_without_a_prefix_still_mounts_under_api(kernel, tmp_path,
                                                        probe_pack):
    """The default is unchanged, which is what makes this a seam rather than a
    migration: sixteen existing packs declare nothing and must not move."""
    client = _client(kernel, tmp_path)
    assert client.get("/api/probe").json() == {"ok": True}
    assert client.get("/probe").status_code == 404


def test_packs_without_a_prefix_still_mount_under_api(kernel, tmp_path):
    """And the real ones, with no probe in sight."""
    client = _client(kernel, tmp_path)
    assert client.get("/api/materials").status_code == 200


def test_a_prefixed_pack_is_still_default_deny_in_hosted_mode(hosted_client):
    """The seam must not become a way around the allowlist. `is_public` is
    consulted with the FULL path, so a pack that mounted itself at `/` would
    still be private — the enumeration test in `test_hosted_surface.py` is what
    would catch a widened surface, and this is the runtime half."""
    from agentcad.server import security

    assert security.is_public("/probe") is False
    assert hosted_client.get("/probe").status_code == 401
