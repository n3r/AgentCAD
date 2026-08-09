import numpy as np
import pytest

from agentcad.kernel import acm

from .conftest import PLATE_SCRIPT

pytestmark = pytest.mark.portability


@pytest.fixture(scope="module")
def plate_mesh(kernel, tmp_path_factory):
    mesh_path = tmp_path_factory.mktemp("mesh") / "plate.acm"
    result = kernel.request(
        "build",
        {
            "script": PLATE_SCRIPT,
            "params": {},
            "density_g_cm3": 2.70,
            "mesh_path": str(mesh_path),
        },
    )
    return acm.read(mesh_path), result["metrics"]


def test_counts_consistent(plate_mesh):
    mesh, _ = plate_mesh
    assert len(mesh["positions"]) > 100
    assert len(mesh["indices"]) > 100
    assert len(mesh["normals"]) == len(mesh["positions"])
    assert int(mesh["edge_lengths"].sum()) == len(mesh["edge_points"])
    assert len(mesh["edge_lengths"]) > 4


def test_indices_in_range(plate_mesh):
    mesh, _ = plate_mesh
    assert mesh["indices"].max() < len(mesh["positions"])


def test_normals_unit_length(plate_mesh):
    mesh, _ = plate_mesh
    lengths = np.linalg.norm(mesh["normals"], axis=1)
    assert np.allclose(lengths, 1.0, atol=1e-3)


def test_bbox_matches_metrics(plate_mesh):
    mesh, metrics = plate_mesh
    pos_min = mesh["positions"].min(axis=0)
    pos_max = mesh["positions"].max(axis=0)
    assert np.allclose(pos_min, metrics["bbox"]["min"], atol=0.2)
    assert np.allclose(pos_max, metrics["bbox"]["max"], atol=0.2)


def test_acm_roundtrip():
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    normals = np.array([[0, 0, 1]] * 3, dtype=np.float32)
    indices = np.array([[0, 1, 2]], dtype=np.uint32)
    edge_lengths = np.array([2], dtype=np.uint32)
    edge_points = np.array([[0, 0, 0], [1, 0, 0]], dtype=np.float32)
    data = acm.pack(positions, normals, indices, edge_lengths, edge_points)
    parsed = acm.parse(data)
    assert np.array_equal(parsed["positions"], positions)
    assert np.array_equal(parsed["indices"], indices)
    assert np.array_equal(parsed["edge_points"], edge_points)


def test_imported_mesh_uses_crease_normals(kernel, tmp_path):
    """An STL box re-imported must render with flat per-face normals (every
    normal axis-aligned), not the diagonal averages the old smooth path
    produced at each shared corner — that averaging was the source of the
    'melted' shading artifacts on imported meshes."""
    import numpy as np

    from agentcad.kernel import acm

    stl = tmp_path / "box.stl"
    kernel.request("export", {
        "script": 'from build123d import *\nPARAMS={"s":{"default":20.0}}\n'
                  'def build(p):\n    return Solid.make_box(p.s,p.s,p.s)\n',
        "params": {}, "format": "stl", "out_path": str(stl)})
    # tessellate the imported mesh directly via the worker's mesh path
    mesh_path = tmp_path / "box.acm"
    res = kernel.request("build_reference", {
        "source_path": str(stl), "density_g_cm3": 1.0,
        "mesh_path": str(mesh_path), "tolerance": 0.1})
    assert res["kind"] == "mesh"
    m = acm.read(mesh_path)
    normals = m["normals"]
    # a box's faces are axis-aligned: each normal's largest |component| ~ 1.0
    dominant = np.abs(normals).max(axis=1)
    assert (dominant > 0.999).all(), (
        "imported box normals are not flat/axis-aligned — crease normals "
        f"not applied (min dominant {dominant.min():.3f})")


def test_brep_face_stays_smooth(kernel, tmp_path):
    """A B-rep cylinder must keep smoothly-varying normals on its curved wall
    (the fix must not turn B-rep shading flat)."""
    import numpy as np

    from agentcad.kernel import acm

    mesh_path = tmp_path / "cyl.acm"
    kernel.request("build", {
        "script": 'from build123d import *\nPARAMS={"r":{"default":10.0}}\n'
                  'def build(p):\n    return Solid.make_cylinder(p.r, 30)\n',
        "params": {}, "density_g_cm3": 1.0, "mesh_path": str(mesh_path)})
    m = acm.read(mesh_path)
    # the curved wall produces many distinct normal directions (smooth), far
    # more than the handful a faceted render would give
    normals = np.round(m["normals"], 2)
    unique = np.unique(normals, axis=0)
    assert len(unique) > 30, f"cylinder wall not smooth ({len(unique)} unique normals)"
