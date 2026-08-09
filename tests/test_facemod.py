"""Face identification (triangle->face sidecar) and push/pull.

The ordering contract under test: a "mesh-order face index" is the position
of a B-rep face in the TopExp_Explorer(FACE) walk — the same order
tessellation emits triangles in (agentcad.toolkit.facemod is the single
source of truth). The `.faces.u32` sidecar written by handle_build, the
face_info kernel handler, and the push_face/push_pull edits must all agree.
"""

import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.core.tools_facemod import PUSH_PULL_MARKER
from agentcad.kernel import acm
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

BOX20_SCRIPT = '''\
from build123d import *

PARAMS = {"size": {"default": 20.0, "min": 5.0, "max": 100.0, "unit": "mm"}}

def build(p):
    return Solid.make_box(p.size, p.size, p.size)
'''

CYL_SCRIPT = '''\
from build123d import *

PARAMS = {"r": {"default": 10.0, "min": 1.0, "max": 50.0, "unit": "mm"}}

def build(p):
    return Solid.make_cylinder(p.r, 30)
'''


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


# ------------------------------------------------- sidecar ordering contract


def test_faces_sidecar_matches_mesh_order(kernel, tmp_path):
    mesh_path = tmp_path / "box.acm"
    result = _build(kernel, mesh_path, BOX_SCRIPT)
    sidecar = tmp_path / "box.faces.u32"
    assert sidecar.is_file()

    mesh = acm.read(mesh_path)
    faces = np.frombuffer(sidecar.read_bytes(), dtype="<u4")
    # one u32 per triangle
    assert len(faces) == len(mesh["indices"])
    assert result["metrics"]["n_faces"] == 6

    # every box face ordinal 0..5 appears, with >= 2 triangles each, and the
    # per-face counts sum to the total
    counts = np.bincount(faces, minlength=6)
    assert len(counts) == 6  # no ordinal beyond n_faces
    assert (counts >= 2).all()
    assert counts.sum() == len(faces)

    # THE ordering contract: face 0's outward normal per face_info equals the
    # normal recomputed from the sidecar's face-0 triangles in the ACM buffer
    info = kernel.request(
        "face_info", {"script": BOX_SCRIPT, "params": {}, "face_index": 0}
    )
    assert info["n_faces"] == 6
    assert info["planar"] is True
    tris = mesh["indices"][faces == 0]
    pts = mesh["positions"].astype(np.float64)
    normals = np.cross(
        pts[tris[:, 1]] - pts[tris[:, 0]], pts[tris[:, 2]] - pts[tris[:, 0]]
    )
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    mean = normals.mean(axis=0)
    mean /= np.linalg.norm(mean)
    assert np.allclose(mean, info["normal"], atol=1e-6)


def test_face_info_out_of_range_is_contract_error(kernel):
    from agentcad.kernel.client import KernelError

    with pytest.raises(KernelError) as exc_info:
        kernel.request(
            "face_info", {"script": BOX_SCRIPT, "params": {}, "face_index": 99}
        )
    assert exc_info.value.details.get("n_faces") == 6


def test_tessellate_with_faces_acm_byte_identical():
    """The refactor must not change tessellate's bytes (determinism is
    pinned elsewhere); the sidecar must be exactly one u32 per triangle."""
    import build123d as b3d

    from agentcad.kernel.mesh import tessellate, tessellate_with_faces

    with b3d.BuildPart() as bp:
        b3d.Box(40, 30, 8)
        b3d.Hole(radius=5)
        b3d.fillet(bp.edges().filter_by(b3d.Axis.Z), radius=2)
    shape = bp.part.wrapped
    plain = tessellate(shape, 0.1)
    buffer, face_ids = tessellate_with_faces(shape, 0.1)
    assert buffer == plain
    n_triangles = struct.unpack_from("<I", buffer, 8)[0]
    assert len(face_ids) == 4 * n_triangles


# ---------------------------------------------------------- push_face toolkit


def _volume(shape):
    return float(sum(s.volume for s in shape.solids()))


def _top_face_index(faces):
    """Mesh-order index of the planar face whose outward normal is +Z, at the
    highest Z (the same normals face_info reports)."""
    import build123d as b3d

    best, best_z = None, None
    for i, face in enumerate(faces):
        if face.geom_type != b3d.GeomType.PLANE:
            continue
        if face.normal_at().Z < 0.999:
            continue
        z = face.center().Z
        if best is None or z > best_z:
            best, best_z = i, z
    assert best is not None, "no +Z planar face found"
    return best


def test_push_face_pull_grows_box():
    import build123d as b3d

    from agentcad.toolkit.facemod import faces_in_mesh_order, push_face

    box = b3d.Solid.make_box(20, 20, 20)
    top = _top_face_index(faces_in_mesh_order(box))
    out = push_face(box, top, 5)
    assert _volume(out) == pytest.approx(20 * 20 * 25, abs=1e-6)


def test_push_face_negative_distance_cuts():
    import build123d as b3d

    from agentcad.toolkit.facemod import faces_in_mesh_order, push_face

    box = b3d.Solid.make_box(20, 20, 20)
    top = _top_face_index(faces_in_mesh_order(box))
    out = push_face(box, top, -5)
    assert _volume(out) == pytest.approx(20 * 20 * 15, abs=1e-6)


def test_push_face_non_planar_raises():
    import build123d as b3d

    from agentcad.toolkit.facemod import faces_in_mesh_order, push_face

    cyl = b3d.Solid.make_cylinder(10, 30)
    side = next(
        i for i, f in enumerate(faces_in_mesh_order(cyl))
        if f.geom_type != b3d.GeomType.PLANE
    )
    with pytest.raises(ValueError, match="not planar"):
        push_face(cyl, side, 3)


def test_push_face_bad_index_and_zero_distance():
    import build123d as b3d

    from agentcad.toolkit.facemod import faces_in_mesh_order, push_face

    box = b3d.Solid.make_box(20, 20, 20)
    with pytest.raises(ValueError, match="out of range"):
        push_face(box, 6, 5)
    top = _top_face_index(faces_in_mesh_order(box))
    with pytest.raises(ValueError, match="nonzero"):
        push_face(box, top, 0)


# ------------------------------------------------------ push_pull tool (e2e)


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX20_SCRIPT)
    return service


def _find_top_face(registry, project, part_id):
    """+Z planar face index via the face_info tool (as the GUI would)."""
    info0 = registry.call(
        "face_info", {"project": project, "part_id": part_id, "face_index": 0}
    )
    assert "error" not in info0, info0
    best, best_z = None, None
    for i in range(info0["n_faces"]):
        info = (
            info0
            if i == 0
            else registry.call(
                "face_info",
                {"project": project, "part_id": part_id, "face_index": i},
            )
        )
        assert "error" not in info, info
        if not info["planar"] or info["normal"][2] < 0.999:
            continue
        z = info["center"][2]
        if best is None or z > best_z:
            best, best_z = i, z
    assert best is not None
    return best


def test_push_pull_tool_composes(demo):
    registry = build_registry(demo)
    base_volume = demo.get_metrics("demo", "box")["volume_mm3"]
    assert base_volume == pytest.approx(8000.0, rel=1e-6)

    top = _find_top_face(registry, "demo", "box")
    res = registry.call(
        "push_pull",
        {"project": "demo", "part_id": "box", "face_index": top,
         "distance_mm": 5.0},
    )
    assert res.get("ok") is True, res
    assert res["face_index"] == top
    assert res["distance_mm"] == 5.0
    # volume grows by area x distance (400 mm2 x 5 mm) within 1%
    assert res["metrics"]["volume_mm3"] == pytest.approx(
        base_volume + 400 * 5, rel=0.01
    )
    script = demo.store.read_script("demo", "box")
    assert script.count(PUSH_PULL_MARKER) == 1
    assert "_agentcad_prev_build_0" in script

    # a SECOND push/pull composes: indices re-derived from the NEW geometry
    top2 = _find_top_face(registry, "demo", "box")
    res2 = registry.call(
        "push_pull",
        {"project": "demo", "part_id": "box", "face_index": top2,
         "distance_mm": 5.0},
    )
    assert res2.get("ok") is True, res2
    assert res2["metrics"]["volume_mm3"] == pytest.approx(
        base_volume + 400 * 10, rel=0.01
    )
    script = demo.store.read_script("demo", "box")
    assert script.count(PUSH_PULL_MARKER) == 2
    assert "_agentcad_prev_build_0" in script
    assert "_agentcad_prev_build_1" in script


def test_push_pull_non_planar_is_validation_error(demo):
    demo.create_part("demo", "cyl", script=CYL_SCRIPT)
    registry = build_registry(demo)
    side = None
    for i in range(3):
        info = registry.call(
            "face_info", {"project": "demo", "part_id": "cyl", "face_index": i}
        )
        if not info["planar"]:
            side = i
            break
    assert side is not None
    res = registry.call(
        "push_pull",
        {"project": "demo", "part_id": "cyl", "face_index": side,
         "distance_mm": 5.0},
    )
    assert res["error"]["type"] == "validation_error"
    assert res["error"]["details"]["planar"] is False
    # the script was NOT touched
    assert PUSH_PULL_MARKER not in demo.store.read_script("demo", "cyl")


def test_push_pull_zero_distance_rejected(demo):
    registry = build_registry(demo)
    res = registry.call(
        "push_pull",
        {"project": "demo", "part_id": "box", "face_index": 0,
         "distance_mm": 0},
    )
    assert res["error"]["type"] == "validation_error"


# ---------------------------------------------------------------- faces route


@pytest.fixture
def client(kernel, tmp_path):
    svc = AgentCADService(tmp_path / "projects", kernel, EventBus())
    app = create_app(svc, build_registry(svc), extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1"), svc


def test_mesh_faces_route(client):
    client, svc = client
    assert client.post("/api/projects", json={"name": "demo"}).status_code == 201
    assert client.post(
        "/api/projects/demo/parts", json={"id": "box", "script": BOX_SCRIPT}
    ).status_code == 201

    mesh = client.get("/api/projects/demo/parts/box/mesh")
    faces = client.get("/api/projects/demo/parts/box/mesh/faces")
    assert faces.status_code == 200
    assert faces.headers["content-type"].startswith("application/octet-stream")
    assert faces.headers["x-mesh-key"] == mesh.headers["x-mesh-key"]
    n_triangles = struct.unpack_from("<I", mesh.content, 8)[0]
    assert len(faces.content) == 4 * n_triangles
    ids = np.frombuffer(faces.content, dtype="<u4")
    assert ids.max() == 5  # box: faces 0..5

    # absent sidecar (a cache entry from before this feature): 404, mesh 200
    key = mesh.headers["x-mesh-key"]
    (svc.store.cache_dir("demo") / f"{key}.faces.u32").unlink()
    gone = client.get("/api/projects/demo/parts/box/mesh/faces")
    assert gone.status_code == 404
    assert client.get("/api/projects/demo/parts/box/mesh").status_code == 200
