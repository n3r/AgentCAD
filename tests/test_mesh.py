import numpy as np
import pytest

from agentcad.kernel import acm

from .conftest import PLATE_SCRIPT


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
