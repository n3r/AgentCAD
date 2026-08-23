import pytest

from agentcad.kernel.client import KernelClient, KernelError

from .conftest import BOX_SCRIPT, NUMERIC_ENUM_SCRIPT, PLATE_SCRIPT, TYPED_SCRIPT

AL_DENSITY = 2.70
pytestmark = pytest.mark.portability


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


@pytest.mark.integration
@pytest.mark.slow
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


# The bench's coolant elbow: an annular section swept along a filleted
# right-angle centre line, i.e. G1-tangent cylinder/torus/cylinder junctions
# all the way along. That is the operand shape OCCT 7.9 answers wrongly about
# (kernel/handlers/_bop.py) — and the shape half of `examples/engine` is built
# from, which is why this belongs on the product path and not only in the
# bench.
ELBOW_SCRIPT = '''\
from build123d import *

PARAMS = {"tube_d": {"default": 24.0}, "wall": {"default": 3.0},
          "run": {"default": 60.0}, "bend_r": {"default": 24.0}}

def build(p):
    with BuildPart() as part:
        with BuildLine() as path:
            Polyline((0, 0, 0), (p.run, 0, 0), (p.run, 0, p.run))
            fillet(path.vertices().group_by(Axis.X)[-1].sort_by(Axis.Z)[0:1],
                   radius=p.bend_r)
        with BuildSketch(Plane.YZ):
            Circle(p.tube_d / 2)
            Circle(p.tube_d / 2 - p.wall, mode=Mode.SUBTRACT)
        sweep(path=path.line)
    return part.part
'''


def test_interference_reports_a_boolean_it_could_not_compute(kernel):
    """Two coincident elbows differing only in bend radius — the same two legs
    running down the same two axes, overlapping over most of their length. OCCT
    intersects them to **nothing**, with `IsDone()` true and no error raised,
    and `pairwise_interference` used to read that empty result as `0.0` and
    report the assembly **clean**. It now fails closed: the pair is listed with
    `degenerate: True` and a `volume_mm3` that carries no information.

    The measured volume stays 0.0 on purpose. The recheck in
    `handlers/_bop._disagrees` is a detector — its cropped octant sum is not a
    valid intersection volume — so it is never promoted into the measurement.
    """
    items = [
        {"name": "elbow_r24", "script": ELBOW_SCRIPT,
         "params": {"bend_r": 24.0}, "position": [0, 0, 0]},
        {"name": "elbow_r30", "script": ELBOW_SCRIPT,
         "params": {"bend_r": 30.0}, "position": [0, 0, 0]},
    ]
    result = kernel.request("interference", {"items": items}, timeout_s=300.0)

    assert len(result["pairs"]) == 1
    pair = result["pairs"][0]
    assert {pair["a"], pair["b"]} == {"elbow_r24", "elbow_r30"}
    assert pair["degenerate"] is True
    assert pair["volume_mm3"] >= 0.0


def test_interference_does_not_cry_degenerate_on_a_clean_assembly(kernel):
    """The other half of the guard. Three boxes, one real overlap: no pair
    carries the marker, so `_bop`'s recheck is not firing on ordinary
    geometry (a false degenerate is fail-*closed*, but it is still false)."""
    items = [
        {"name": "a", "script": BOX_SCRIPT, "params": {"size": 10.0},
         "position": [0, 0, 0]},
        {"name": "b", "script": BOX_SCRIPT, "params": {"size": 10.0},
         "position": [5, 0, 0]},
        {"name": "c", "script": BOX_SCRIPT, "params": {"size": 10.0},
         "position": [100, 0, 0]},
    ]
    result = kernel.request("interference", {"items": items})
    assert not any("degenerate" in pair for pair in result["pairs"])


ANALYSIS_SCRIPT = '''\
from build123d import *

PARAMS = {"size": {"default": 30.0, "min": 1.0, "max": 100.0}}

def build(p):
    with BuildPart() as part:
        Box(p.size, p.size, p.size)   # display shape: big, would interfere
    return part.part

def analysis(p):
    with BuildPart() as part:
        Box(2, 2, 2)                  # analysis envelope: tiny, clear
    return part.part
'''


def test_interference_prefers_analysis_shape(kernel):
    # The display shapes overlap massively; the analysis() stand-ins do not.
    # check_interference must judge by analysis(), display by build().
    items = [
        {"name": "a", "script": ANALYSIS_SCRIPT, "params": {},
         "position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
        {"name": "b", "script": ANALYSIS_SCRIPT, "params": {},
         "position": [5, 0, 0], "rotation_deg": [0, 0, 0]},
    ]
    result = kernel.request("interference", {"items": items})
    assert result["pairs"] == []


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


# ------------------------------------------------------------- typed params


def test_typed_params_inspect_normalizes_spec(kernel):
    spec = kernel.request("inspect", {"script": TYPED_SCRIPT})["params_spec"]
    assert spec["size"]["type"] == "number"
    assert "choices" not in spec["size"] and "max_len" not in spec["size"]
    assert spec["holes"]["type"] == "bool"
    assert spec["holes"]["default"] is True
    assert spec["grade"]["type"] == "enum"
    assert spec["grade"]["choices"] == ["std", "wide"]
    assert spec["label"]["type"] == "string"
    assert spec["label"]["max_len"] == 10
    assert spec["n"]["type"] == "int"
    assert spec["n"]["min"] == 1 and spec["n"]["max"] == 4


def test_typed_params_drive_geometry(kernel, tmp_path):
    withholes, _ = build(kernel, tmp_path, TYPED_SCRIPT)
    solid, _ = build(kernel, tmp_path, TYPED_SCRIPT, params={"holes": False})
    assert solid["metrics"]["volume_mm3"] > withholes["metrics"]["volume_mm3"]

    wide, _ = build(kernel, tmp_path, TYPED_SCRIPT, params={"grade": "wide"})
    bbox = wide["metrics"]["bbox"]
    assert bbox["max"][0] - bbox["min"][0] == pytest.approx(40.0, abs=0.01)
    assert bbox["max"][1] - bbox["min"][1] == pytest.approx(20.0, abs=0.01)


def test_typed_params_int_coerces_integral_float(kernel, tmp_path):
    result, _ = build(kernel, tmp_path, TYPED_SCRIPT, params={"n": 3.0})
    assert result["metrics"]["is_valid"] is True
    assert result["warnings"] == []


def test_typed_params_int_clamp_warns(kernel, tmp_path):
    result, _ = build(kernel, tmp_path, TYPED_SCRIPT, params={"n": 9})
    assert any("clamped" in w for w in result["warnings"])
    assert result["metrics"]["is_valid"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"n": 3.5},          # int must be integral
        {"holes": "yes"},    # bool takes only a real bool
        {"label": "x" * 11}, # string over max_len
    ],
    ids=["int-fractional", "bool-string", "string-too-long"],
)
def test_typed_params_bad_override_is_contract_error(kernel, tmp_path, params):
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, TYPED_SCRIPT, params=params)
    assert exc_info.value.type == "contract_error"


def test_typed_params_numeric_enum_canonicalizes_to_declared_choice(kernel, tmp_path):
    # 3.0 == the declared int choice 3, so it must resolve — and it must reach
    # build(p) as the declared int (range(p.n) rejects a float).
    exact, _ = build(kernel, tmp_path / "a", NUMERIC_ENUM_SCRIPT, params={"n": 3})
    floaty, _ = build(kernel, tmp_path / "b", NUMERIC_ENUM_SCRIPT, params={"n": 3.0})
    assert floaty["metrics"]["is_valid"] is True
    assert floaty["metrics"]["volume_mm3"] == pytest.approx(
        exact["metrics"]["volume_mm3"], rel=1e-9
    )


def test_typed_params_numeric_enum_still_rejects_bool(kernel, tmp_path):
    # True == 1 numerically, but bools are never valid enum members.
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, NUMERIC_ENUM_SCRIPT, params={"n": True})
    assert exc_info.value.type == "contract_error"


def test_typed_params_inspect_output_round_trips_as_params(kernel, tmp_path):
    # handle_inspect emits explicit None for absent min/max/unit; feeding its
    # own normalized spec back in as PARAMS must be legal (None == absent).
    spec = kernel.request("inspect", {"script": TYPED_SCRIPT})["params_spec"]
    script = (
        "import build123d as b3d\n"
        f"PARAMS = {spec!r}\n"
        "def build(p):\n"
        "    return b3d.Box(10, 10, 10)\n"
    )
    respec = kernel.request("inspect", {"script": script})["params_spec"]
    assert respec == spec
    result, _ = build(kernel, tmp_path, script, params={"label": "hi"})
    assert result["metrics"]["is_valid"] is True


def test_typed_params_none_spec_fields_treated_as_absent(kernel, tmp_path):
    script = (
        "import build123d as b3d\n"
        "PARAMS = {\n"
        '    "flip": {"default": True, "type": "bool", "min": None, "max": None,'
        ' "unit": None, "description": "x"},\n'
        '    "tag": {"default": "a", "type": "string", "max_len": None,'
        ' "description": "x"},\n'
        "}\n"
        "def build(p):\n"
        "    return b3d.Box(10, 10, 10)\n"
    )
    spec = kernel.request("inspect", {"script": script})["params_spec"]
    assert spec["tag"]["max_len"] == 200  # None means "use the default"
    result, _ = build(kernel, tmp_path, script, params={"tag": "hello"})
    assert result["metrics"]["is_valid"] is True


def test_typed_params_enum_nonmember_reports_choices(kernel, tmp_path):
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, TYPED_SCRIPT, params={"grade": "narrow"})
    err = exc_info.value
    assert err.type == "contract_error"
    assert err.details.get("choices") == ["std", "wide"] or "std" in err.message


def _one_param_script(params_entry: str) -> str:
    return (
        "import build123d as b3d\n"
        f"PARAMS = {{{params_entry}}}\n"
        "def build(p):\n"
        "    return b3d.Box(10, 10, 10)\n"
    )


@pytest.mark.parametrize(
    "entry",
    [
        '"g": {"default": "a", "type": "enum"}',                    # enum w/o choices
        '"g": {"default": "a", "type": "enum", "choices": []}',     # empty choices
        '"f": {"default": 1, "type": "bool"}',                      # bool default 1
        '"f": {"default": True, "type": "bool", "min": 0}',         # min on a bool
        '"s": {"default": 5, "type": "string"}',                    # string default 5
        '"x": {"default": 1.0, "type": "flag"}',                    # unknown type
    ],
    ids=["enum-no-choices", "enum-empty", "bool-int-default",
         "bool-with-min", "string-num-default", "unknown-type"],
)
def test_typed_params_bad_spec_is_contract_error(kernel, tmp_path, entry):
    with pytest.raises(KernelError) as exc_info:
        build(kernel, tmp_path, _one_param_script(entry))
    assert exc_info.value.type == "contract_error"


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
