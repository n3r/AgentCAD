"""Mesh streaming LOD tiers.

Worker: builds may request coarse sidecar meshes (``<key>.lod1.acm``, same
ACM1 format) written only when the full mesh's triangle count exceeds a
threshold. Service: always requests the tier, records ``lods`` in the metrics
sidecar, and ``mesh_info(lod=...)`` falls back to the full mesh when no tier
exists. Route: ``?lod=lod1`` serves the tier with an ``X-Mesh-Lod`` header;
full-resolution serving stays byte-identical to a no-param GET.
"""

import json

import pytest
from fastapi.testclient import TestClient

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.kernel import acm
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

# Dense enough at tolerance 0.05 (thousands of triangles) that a small
# lod_min_triangles lets the worker-level tests stay fast.
SPHERE_SCRIPT = '''\
from build123d import *

PARAMS = {"r": {"default": 30.0, "min": 1.0, "max": 100.0, "unit": "mm"}}

def build(p):
    return Sphere(p.r)
'''

LOD_KWARGS = {"lod_tolerances": {"lod1": 0.8}, "lod_min_triangles": 1000}


def _build(kernel, mesh_path, script, **extra):
    return kernel.request(
        "build",
        {
            "script": script,
            "params": {},
            "density_g_cm3": 1.0,
            "mesh_path": str(mesh_path),
            **extra,
        },
    )


# ------------------------------------------------------------------- worker


def test_worker_writes_lod_tier_above_threshold(kernel, tmp_path):
    mesh_path = tmp_path / "sphere.acm"
    result = _build(kernel, mesh_path, SPHERE_SCRIPT, tolerance=0.05, **LOD_KWARGS)
    lod_path = tmp_path / "sphere.lod1.acm"
    assert mesh_path.is_file()
    assert lod_path.is_file()

    full = acm.read(mesh_path)  # raises unless valid ACM1
    lod = acm.read(lod_path)
    assert result["lods"] == ["lod1"]
    assert result["triangles"] == len(full["indices"])
    assert result["triangles"] > 1000
    assert len(lod["indices"]) < len(full["indices"])
    assert len(lod["normals"]) == len(lod["positions"])


def test_worker_skips_lod_below_threshold(kernel, tmp_path):
    mesh_path = tmp_path / "box.acm"
    result = _build(
        kernel, mesh_path, BOX_SCRIPT,
        lod_tolerances={"lod1": 0.8}, lod_min_triangles=10**9,
    )
    assert mesh_path.is_file()
    assert not (tmp_path / "box.lod1.acm").exists()
    assert result["lods"] == []
    assert result["triangles"] > 0


def test_worker_without_lod_params_unchanged(kernel, tmp_path):
    mesh_path = tmp_path / "box.acm"
    result = _build(kernel, mesh_path, BOX_SCRIPT)
    assert mesh_path.is_file()
    assert result["lods"] == []
    assert not list(tmp_path.glob("*.lod1.acm"))


def test_lod_and_full_bytes_deterministic(kernel, tmp_path):
    _build(kernel, tmp_path / "a" / "s.acm", SPHERE_SCRIPT,
           tolerance=0.05, **LOD_KWARGS)
    _build(kernel, tmp_path / "b" / "s.acm", SPHERE_SCRIPT,
           tolerance=0.05, **LOD_KWARGS)
    assert (tmp_path / "a" / "s.lod1.acm").read_bytes() == \
        (tmp_path / "b" / "s.lod1.acm").read_bytes()
    # The coarse re-tessellation must not perturb full-resolution determinism.
    assert (tmp_path / "a" / "s.acm").read_bytes() == \
        (tmp_path / "b" / "s.acm").read_bytes()


def test_reference_step_writes_lod_tier(kernel, tmp_path):
    step = tmp_path / "widget.step"
    kernel.request("export", {"script": BOX_SCRIPT, "params": {},
                              "format": "step", "out_path": str(step)})
    mesh_path = tmp_path / "ref.acm"
    result = kernel.request("build_reference", {
        "source_path": str(step),
        "mesh_path": str(mesh_path),
        "lod_tolerances": {"lod1": 0.8},
        "lod_min_triangles": 4,  # a box tessellates to 12 triangles
    })
    assert result["lods"] == ["lod1"]
    assert result["triangles"] > 4
    assert (tmp_path / "ref.lod1.acm").read_bytes()[:4] == b"ACM1"


def test_reference_stl_never_writes_lod(kernel, tmp_path):
    """An STL's triangulation IS its geometry: no coarser tier can exist."""
    stl = tmp_path / "sphere.stl"
    kernel.request("export", {"script": SPHERE_SCRIPT, "params": {},
                              "format": "stl", "out_path": str(stl),
                              "tolerance": 0.5})
    mesh_path = tmp_path / "ref.acm"
    result = kernel.request("build_reference", {
        "source_path": str(stl),
        "mesh_path": str(mesh_path),
        "lod_tolerances": {"lod1": 0.8},
        "lod_min_triangles": 1,
    })
    assert result["lods"] == []
    assert result["triangles"] > 1
    assert not (tmp_path / "ref.lod1.acm").exists()


# ------------------------------------------------------------------ service


@pytest.fixture
def service(kernel, tmp_path):
    svc = AgentCADService(tmp_path / "projects", kernel, EventBus())
    svc.create_project("demo")
    svc.create_part("demo", "box", script=BOX_SCRIPT)
    return svc


def test_mesh_info_falls_back_without_tier(service):
    # Real 150k threshold: a small box writes no tier — fallback is the norm.
    info = service.mesh_info("demo", "box", lod="lod1")
    assert info["lod"] is None
    assert info["path"].name == f"{info['key']}.acm"
    assert service.mesh_info("demo", "box")["path"] == info["path"]
    result = service._ensure_built("demo", "box")
    assert result["lods"] == []
    assert not (service.store.cache_dir("demo") / f"{info['key']}.lod1.acm").exists()


def test_mesh_info_returns_tier_when_written(kernel, tmp_path, monkeypatch):
    monkeypatch.setattr("agentcad.core.service.LOD_TRIANGLE_THRESHOLD", 4)
    svc = AgentCADService(tmp_path / "projects", kernel, EventBus())
    svc.create_project("demo")
    svc.create_part("demo", "box", script=BOX_SCRIPT)

    result = svc._ensure_built("demo", "box")
    assert result["lods"] == ["lod1"]
    info = svc.mesh_info("demo", "box", lod="lod1")
    assert info["lod"] == "lod1"
    assert info["path"].name == f"{info['key']}.lod1.acm"
    assert info["path"].read_bytes()[:4] == b"ACM1"
    # existing callers (no lod argument) still get the full mesh
    assert svc.mesh_info("demo", "box")["path"].name == f"{info['key']}.acm"


def test_metrics_sidecar_roundtrips_lods(kernel, tmp_path, monkeypatch):
    monkeypatch.setattr("agentcad.core.service.LOD_TRIANGLE_THRESHOLD", 4)
    svc = AgentCADService(tmp_path / "projects", kernel, EventBus())
    svc.create_project("demo")
    svc.create_part("demo", "box", script=BOX_SCRIPT)

    key = svc._ensure_built("demo", "box")["cache_key"]
    sidecar = json.loads(
        (svc.store.cache_dir("demo") / f"{key}.metrics.json").read_text(encoding="utf-8")
    )
    assert sidecar["lods"] == ["lod1"]
    # Forget in-memory status: the cached-rebuild path must keep the list.
    svc._status.clear()
    result = svc._ensure_built("demo", "box")
    assert result["ok"] is True
    assert result["lods"] == ["lod1"]
    assert result["cache_key"] == key


# -------------------------------------------------------------------- route


@pytest.fixture
def client(kernel, tmp_path):
    svc = AgentCADService(tmp_path / "projects", kernel, EventBus())
    app = create_app(
        svc, build_registry(svc), extra_allowed_hosts={"testserver"}
    )
    return TestClient(app, base_url="http://127.0.0.1")


def _make_demo(client):
    assert client.post("/api/projects", json={"name": "demo"}).status_code == 201
    response = client.post(
        "/api/projects/demo/parts", json={"id": "box", "script": BOX_SCRIPT}
    )
    assert response.status_code == 201


def test_route_absent_tier_serves_full(client):
    _make_demo(client)
    plain = client.get("/api/projects/demo/parts/box/mesh")
    tier = client.get("/api/projects/demo/parts/box/mesh?lod=lod1")
    assert plain.status_code == 200 and tier.status_code == 200
    assert plain.headers["x-mesh-lod"] == "full"
    assert tier.headers["x-mesh-lod"] == "full"
    assert tier.headers["x-mesh-key"] == plain.headers["x-mesh-key"]
    assert tier.content == plain.content  # fallback is byte-identical
    assert plain.content[:4] == b"ACM1"


def test_route_serves_tier_when_present(client, monkeypatch):
    monkeypatch.setattr("agentcad.core.service.LOD_TRIANGLE_THRESHOLD", 4)
    _make_demo(client)
    tier = client.get("/api/projects/demo/parts/box/mesh?lod=lod1")
    assert tier.status_code == 200
    assert tier.headers["x-mesh-lod"] == "lod1"
    assert tier.headers["x-mesh-key"]
    assert tier.content[:4] == b"ACM1"
    full = client.get("/api/projects/demo/parts/box/mesh")
    assert full.headers["x-mesh-lod"] == "full"
    assert full.headers["x-mesh-key"] == tier.headers["x-mesh-key"]


def test_route_pathy_lod_suffix_falls_back(client):
    _make_demo(client)
    response = client.get(
        "/api/projects/demo/parts/box/mesh",
        params={"lod": "../../secrets"},
    )
    assert response.status_code == 200
    assert response.headers["x-mesh-lod"] == "full"
    assert response.content[:4] == b"ACM1"
