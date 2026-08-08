import pytest

from agentcad.kernel.client import KernelClient, KernelError

from .conftest import BOX_SCRIPT, PLATE_SCRIPT

AL_DENSITY = 2.70


def build(kernel, tmp_path, script, params=None, **kwargs):
    mesh_path = tmp_path / "mesh.acm"
    result = kernel.request(
        "build",
        {
            "script": script,
            "params": params or {},
            "density_g_cm3": AL_DENSITY,
            "mesh_path": str(mesh_path),
            **kwargs,
        },
    )
    return result, mesh_path


def test_ping(kernel):
    result = kernel.request("ping", {})
    assert result["ok"] is True
    assert result["build123d"]


def test_build_plate_metrics_and_mesh(kernel, tmp_path):
    result, mesh_path = build(kernel, tmp_path, PLATE_SCRIPT)
    metrics = result["metrics"]
    assert metrics["volume_mm3"] == pytest.approx(48438.2, abs=5.0)
    assert metrics["is_valid"] is True
    assert metrics["mass_g"] == pytest.approx(metrics["volume_mm3"] * AL_DENSITY / 1000)
    assert metrics["n_solids"] == 1
    assert metrics["n_faces"] > 10
    assert mesh_path.read_bytes()[:4] == b"ACM1"
    assert result["warnings"] == []


def test_param_override_changes_volume(kernel, tmp_path):
    base, _ = build(kernel, tmp_path, BOX_SCRIPT)
    bigger, _ = build(kernel, tmp_path, BOX_SCRIPT, params={"size": 20.0})
    assert base["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert bigger["metrics"]["volume_mm3"] == pytest.approx(8000.0, rel=1e-6)


def test_param_clamp_warns(kernel, tmp_path):
    result, _ = build(kernel, tmp_path, BOX_SCRIPT, params={"size": 500.0})
    assert result["metrics"]["volume_mm3"] == pytest.approx(1_000_000.0, rel=1e-6)
    assert any("clamped" in w for w in result["warnings"])


def test_unknown_param_rejected(kernel, tmp_path):
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, BOX_SCRIPT, params={"nope": 1.0})
    assert exc_info.value.type == "contract_error"
    assert "nope" in exc_info.value.message


def test_syntax_error_reports_line(kernel, tmp_path):
    script = "from build123d import *\nPARAMS = {}\ndef build(p:\n    pass\n"
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, script)
    assert exc_info.value.type == "script_error"
    assert exc_info.value.details["line"] == 3


def test_runtime_error_reports_line_and_traceback(kernel, tmp_path):
    script = (
        "from build123d import *\n"
        'PARAMS = {"size": {"default": 10.0}}\n'
        "def build(p):\n"
        "    raise RuntimeError('boom')\n"
    )
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, script)
    err = exc_info.value
    assert err.type == "script_error"
    assert "boom" in err.message
    assert err.details["line"] == 4
    assert "RuntimeError" in err.details["traceback"]


def test_missing_params_is_contract_error(kernel, tmp_path):
    script = "def build(p):\n    return None\n"
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, script)
    assert exc_info.value.type == "contract_error"


def test_bad_return_type_is_contract_error(kernel, tmp_path):
    script = 'PARAMS = {"size": {"default": 1.0}}\ndef build(p):\n    return 42\n'
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, script)
    assert exc_info.value.type == "contract_error"
    assert "int" in exc_info.value.message


def test_timeout_kills_and_recovers():
    client = KernelClient()
    client.start()
    try:
        with pytest.raises(KernelError) as exc_info:
            client.request(
                "build",
                {"script": "while True:\n    pass\n", "params": {}, "mesh_path": "/dev/null"},
                timeout_s=3.0,
            )
        assert exc_info.value.type == "timeout"
        assert client.request("ping", {})["ok"] is True  # respawned
    finally:
        client.stop()


def test_export_step_and_stl(kernel, tmp_path):
    for fmt, checker in (
        ("step", lambda b: b.startswith(b"ISO-10303-21")),
        ("stl", lambda b: len(b) > 1000),
        ("3mf", lambda b: b[:2] == b"PK"),  # 3mf is a zip container
    ):
        out = tmp_path / f"part.{fmt}"
        result = kernel.request(
            "export",
            {
                "script": PLATE_SCRIPT,
                "params": {},
                "format": fmt,
                "out_path": str(out),
            },
        )
        assert result["size_bytes"] > 1000
        assert checker(out.read_bytes()), fmt


def test_interference_detects_overlap(kernel):
    items = [
        {"name": "a", "script": BOX_SCRIPT, "params": {"size": 10.0},
         "position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
        {"name": "b", "script": BOX_SCRIPT, "params": {"size": 10.0},
         "position": [5, 0, 0], "rotation_deg": [0, 0, 0]},
        {"name": "c", "script": BOX_SCRIPT, "params": {"size": 10.0},
         "position": [100, 0, 0], "rotation_deg": [0, 0, 0]},
    ]
    result = kernel.request("interference", {"items": items})
    pairs = {(p["a"], p["b"]): p["volume_mm3"] for p in result["pairs"]}
    assert ("a", "b") in pairs
    assert pairs[("a", "b")] == pytest.approx(500.0, rel=0.01)
    assert not any("c" in key for key in pairs)


def test_rotation_semantics(kernel, tmp_path):
    # A 20x10x2 box rotated 90 deg about X: bbox y/z extents swap.
    script = (
        "from build123d import *\n"
        'PARAMS = {"l": {"default": 20.0}}\n'
        "def build(p):\n"
        "    with BuildPart() as part:\n"
        "        Box(p.l, 10, 2)\n"
        "    return part.part\n"
    )
    out = tmp_path / "rot.stl"
    kernel.request(
        "export_assembly",
        {
            "items": [
                {"script": script, "params": {}, "position": [0, 0, 0],
                 "rotation_deg": [90, 0, 0]}
            ],
            "format": "stl",
            "out_path": str(out),
        },
    )
    import struct

    data = out.read_bytes()
    n_tris = struct.unpack_from("<I", data, 80)[0]
    zs, ys = [], []
    for i in range(n_tris):
        off = 84 + i * 50 + 12
        for v in range(3):
            x, y, z = struct.unpack_from("<fff", data, off + v * 12)
            ys.append(y)
            zs.append(z)
    assert max(zs) - min(zs) == pytest.approx(10.0, abs=0.1)
    assert max(ys) - min(ys) == pytest.approx(2.0, abs=0.1)


def test_determinism_identical_mesh_bytes(kernel, tmp_path):
    _, mesh_a = build(kernel, tmp_path / "a", BOX_SCRIPT)
    _, mesh_b = build(kernel, tmp_path / "b", BOX_SCRIPT)
    assert mesh_a.read_bytes() == mesh_b.read_bytes()


def test_nested_compound_volume_sums_solids(kernel, tmp_path):
    # build123d 0.11 Compound.volume undercounts a nested compound; the worker
    # must sum per-solid volumes. Two disjoint 10mm cubes -> 2000 mm^3.
    script = (
        "from build123d import *\n"
        'PARAMS = {"g": {"default": 30.0, "min": 20.0, "max": 60.0}}\n'
        "def build(p):\n"
        "    a = Solid.make_box(10, 10, 10)\n"
        "    b = Solid.make_box(10, 10, 10).moved(Location((p.g, 0, 0)))\n"
        "    inner = Compound(children=[b])\n"
        "    return Compound(children=[a, inner])\n"
    )
    result, _ = build(kernel, tmp_path, script)
    assert result["metrics"]["volume_mm3"] == pytest.approx(2000.0, rel=1e-6)
    assert result["metrics"]["n_solids"] == 2
