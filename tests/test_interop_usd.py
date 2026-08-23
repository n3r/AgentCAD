"""PRD-017 slice 7 — USD export (FR11), behind the `agentcad[usd]` extra.

Two halves, deliberately split by what they need:

* **The gate runs everywhere.** `usd_available()` is monkeypatched both ways
  (the `tests/test_analysis.py` FEM idiom) and the assertion is that the format
  enum, the tool API and the route behaviour move *together*: with the extra
  `usd` is offered and writes a stage; without it the enum has no entry and a
  `usd` request is the ordinary unknown-format `validation_error`. An agent is
  never shown a format that cannot run.
* **The stage half needs `pxr`** and takes the `pxr` fixture, so it skips
  cleanly in a venv without the extra (and on linux-aarch64, where no wheel
  exists at all — the marker in `pyproject.toml` is asserted here too, because
  "simplifying" it away breaks `uv sync --extra usd` on exactly the platform
  `make test-linux` runs).

The stage assertions are made on the `.usda` **text** and again through `pxr`'s
own composition, because the two catch different mistakes: the text proves what
we wrote (declared units, one copy of the points, no timestamp), the stage
proves what a consumer *reads* (the reference resolves, the matrix rotates the
way our Euler convention says).
"""

import re
import struct
import subprocess
import sys
import tomllib
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core import tools_xchange, usd_export
from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- fixtures


def make_acm(positions, normals=None, indices=None) -> bytes:
    """An ACM1 buffer, written straight from the layout in `kernel/acm.py`'s
    docstring (the `tests/test_interop_gltf.py` helper, same reason: this test
    must not import the module it checks against)."""
    normals = normals if normals is not None else [(0.0, 0.0, 1.0)] * len(positions)
    indices = indices if indices is not None else [(0, 1, 2)]
    out = bytearray()
    out += struct.pack("<4sIIII", b"ACM1", len(positions), len(indices), 0, 0)
    for vec in positions:
        out += struct.pack("<3f", *vec)
    for vec in normals:
        out += struct.pack("<3f", *vec)
    for tri in indices:
        out += struct.pack("<3I", *tri)
    return bytes(out)


TRI = make_acm([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 20.0, 5.0)])
QUAD = make_acm(
    [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
    indices=[(0, 1, 2), (0, 2, 3)],
)


def item(instance_id="a", mesh_key="k1", acm=TRI, position=(0, 0, 0),
         rotation=(0, 0, 0), color="#b0b6bd", category="metal") -> dict:
    return {
        "instance_id": instance_id, "mesh_key": mesh_key, "acm_bytes": acm,
        "position": list(position), "rotation_deg": list(rotation),
        "color_hex": color, "material_category": category,
    }


#: One part, two instances, the second rotated and moved: the whole contract
#: (dedup, pose, per-instance colour) in two prims.
TWO_INSTANCES = [
    item(instance_id="a", color="#808080"),
    item(instance_id="b", position=(20, 0, 0), rotation=(0, 0, 90),
         color="#ff0000"),
]


@pytest.fixture
def pxr():
    """The extra, or a clean skip. Everything under this fixture is the half
    that cannot run without `agentcad[usd]`."""
    return pytest.importorskip("pxr")


@pytest.fixture
def bare(kernel, tmp_path):
    """A service with a project and no part: the gate refuses on the format
    before it ever looks for geometry."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    return service


@pytest.fixture
def svc(bare):
    bare.create_part("demo", "box", script=BOX_SCRIPT)
    return bare


def two_boxes(service):
    service.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "b", "part": "box", "position": [20, 0, 0],
         "rotation_deg": [0, 0, 90], "color": "#ff0000"},
    ])


def usd(monkeypatch, available: bool) -> None:
    """The FEM gating idiom: patch availability on the module the pack reads
    it from (`tools_xchange` calls `usd_export.usd_available()` at call time
    precisely so this works)."""
    monkeypatch.setattr(usd_export, "usd_available", lambda: available)


def stage_text(items) -> str:
    return usd_export.build_usd(items).decode("utf-8")


def written(tmp_path, items) -> Path:
    path = tmp_path / "stage.usda"
    path.write_bytes(usd_export.build_usd(items))
    return path


# ------------------------------------------------------------------- gate


def test_the_format_enum_gains_usd_only_with_the_extra(bare, monkeypatch):
    usd(monkeypatch, False)
    off = build_registry(bare)
    assert off.get("export_part").input_schema["properties"]["format"][
        "enum"] == list(tools_xchange.BASE_PART_FORMATS)
    assert off.get("export_assembly").input_schema["properties"]["format"][
        "enum"] == list(tools_xchange.BASE_ASSEMBLY_FORMATS)
    assert "usd" not in off.get("export_part").description

    usd(monkeypatch, True)
    on = build_registry(bare)
    assert on.get("export_part").input_schema["properties"]["format"][
        "enum"] == list(tools_xchange.BASE_PART_FORMATS) + ["usd"]
    assert on.get("export_assembly").input_schema["properties"]["format"][
        "enum"] == list(tools_xchange.BASE_ASSEMBLY_FORMATS) + ["usd"]
    # The description says what the stage declares, so a caller never has to
    # guess whether we converted their units or their up axis.
    assert "metersPerUnit 0.001" in on.get("export_part").description
    assert "usd" in on.get("export_assembly").description


@pytest.mark.parametrize("available", [True, False])
def test_the_gate_is_what_the_tool_api_serves(bare, monkeypatch, available):
    usd(monkeypatch, available)
    registry = build_registry(bare)
    app = create_app(bare, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    tools = {t["name"]: t for t in client.get("/api/tools").json()["tools"]}
    for name in ("export_part", "export_assembly"):
        enum = tools[name]["input_schema"]["properties"]["format"]["enum"]
        assert ("usd" in enum) is available, name


def test_a_usd_request_without_the_extra_is_the_ordinary_refusal(bare,
                                                                 monkeypatch):
    """No special "install the extra" path: `usd` is simply not a format, and
    the refusal names the ones that are (the `_check_format` shape)."""
    registry = build_registry(bare)
    usd(monkeypatch, False)
    with pytest.raises(ValidationError) as exc:
        bare.export_part("demo", "box", "usd")
    assert exc.value.details["known"] == list(tools_xchange.BASE_PART_FORMATS)
    with pytest.raises(ValidationError) as exc:
        bare.export_assembly("demo", "usd")
    assert "usd" not in str(exc.value)
    payload = registry.call("export_part", {"project": "demo",
                                            "part_id": "box", "format": "usd"})
    assert payload["error"]["type"] == "validation_error"


def test_the_writer_itself_refuses_without_pxr(monkeypatch):
    """Belt and braces: the pack's enum is the gate, but the writer is a public
    function and says why rather than raising ImportError from three frames in."""
    usd(monkeypatch, False)
    with pytest.raises(usd_export.UsdError) as exc:
        usd_export.build_usd([item()])
    assert "agentcad[usd]" in str(exc.value)


def test_prim_names_are_legal_usd_identifiers_and_unique():
    """Instance ids are authored strings and mesh keys are hex digests; USD
    identifiers are `[A-Za-z_][A-Za-z0-9_]*`, and two ids can sanitize to one
    name."""
    used: set[str] = set()
    assert usd_export._prim_name("bolt-1", used) == "bolt_1"
    assert usd_export._prim_name("bolt.1", used) == "bolt_1_2"
    assert usd_export._prim_name("3abc", used) == "_3abc"
    assert usd_export._prim_name("0f3a9", used, prefix="Mesh_") == "Mesh_0f3a9"


# ------------------------------------------------------- the pyproject pin


def test_the_usd_extra_keeps_its_platform_marker():
    """usd-core ships no linux-aarch64 wheel and `make test-linux` runs arm64,
    so an unmarked requirement breaks `uv sync --extra usd` on exactly the
    platform CI uses. With the marker that platform gets no package,
    `usd_available()` stays False, and the format never appears."""
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    requirements = data["project"]["optional-dependencies"]["usd"]
    assert len(requirements) == 1
    requirement, _, marker = requirements[0].partition(";")
    assert requirement.strip().startswith("usd-core>=26.8")
    assert marker, "the usd extra MUST carry its platform marker"
    assert "platform_machine" in marker and "aarch64" in marker
    assert "sys_platform" in marker and "linux" in marker


# ------------------------------------------------------------ stage: text


def test_the_stage_declares_millimetres_and_z_up(pxr):
    """FR11's honesty line: USD carries both natively, so nothing is converted
    — the numbers in the file are the numbers in the manifest."""
    text = stage_text(TWO_INSTANCES)
    assert text.startswith("#usda 1.0")
    assert "metersPerUnit = 0.001" in text
    assert 'upAxis = "Z"' in text
    assert 'defaultPrim = "AgentCAD"' in text
    assert 'string creator = "AgentCAD"' in text
    assert text.count("matrix4d xformOp:transform") == 2


def test_the_file_carries_no_timestamp(pxr):
    """The stage is a function of the model state and nothing else (FR7) —
    the provenance we do stamp is one constant string."""
    text = stage_text(TWO_INSTANCES)
    assert re.search(r"\b20\d\d-\d\d-\d\d", text) is None
    assert text.count("customLayerData") == 1
    assert "documentation" not in text


def test_the_points_are_written_once_for_two_instances(pxr):
    """Eight screws are one mesh's worth of points and eight prims (spec §10).
    The dedup is composition: the library holds the arrays, the instances hold
    a reference, a pose and a colour."""
    text = stage_text(TWO_INSTANCES)
    assert text.count("point3f[] points") == 1
    assert text.count("int[] faceVertexIndices") == 1
    assert text.count("references = ") == 2
    assert text.count("matrix4d xformOp:transform") == 2
    assert text.count("color3f[] primvars:displayColor") == 2
    # Two mesh keys are two library meshes.
    two = stage_text([item(instance_id="a", mesh_key="k1", acm=TRI),
                      item(instance_id="b", mesh_key="k2", acm=QUAD)])
    assert two.count("point3f[] points") == 2


def test_two_exports_of_one_state_are_byte_identical(pxr):
    """AC3's rule for USD too — and the reason the stage is `.usda` text: a
    crate file cannot be diffed, and `sort_keys`-style determinism is only
    meaningful if you can see it."""
    first = usd_export.build_usd(TWO_INSTANCES)
    second = usd_export.build_usd(TWO_INSTANCES)
    assert sha256(first).hexdigest() == sha256(second).hexdigest()
    assert first == usd_export.build_usd(list(reversed(TWO_INSTANCES))), \
        "item order must not reach the file (prims sort by instance id)"


def test_display_color_is_linear_not_srgb(pxr):
    """The same conversion the glTF writer makes: USD's displayColor feeds a
    linear rendering pipeline, and writing our sRGB hex straight through is the
    classic silent-darkening bug."""
    text = stage_text([item(color="#808080")])
    assert "0.215861" in text          # 0x80/255 = 0.50196 sRGB -> linear
    assert "0.501961" not in text


# ----------------------------------------------------------- stage: pxr


def test_the_stage_opens_and_composes(pxr, tmp_path):
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(written(tmp_path, TWO_INSTANCES)))
    assert UsdGeom.GetStageMetersPerUnit(stage) == pytest.approx(0.001)
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert stage.GetDefaultPrim().GetName() == "AgentCAD"

    # The library is abstract, so a traversal (and a renderer) sees exactly the
    # two instances — never a prototype sitting at the origin.
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert sorted(p.GetName() for p in meshes) == ["a", "b"]
    library = stage.GetPrimAtPath("/AgentCAD/Meshes")
    assert library.IsAbstract()
    prototypes = [p for p in library.GetAllChildren() if p.IsA(UsdGeom.Mesh)]
    assert len(prototypes) == 1

    # ... and the reference composes: the instance reads the library's points.
    for prim in meshes:
        mesh = UsdGeom.Mesh(prim)
        assert len(mesh.GetPointsAttr().Get()) == 3
        assert list(mesh.GetFaceVertexCountsAttr().Get()) == [3]
        assert len(mesh.GetNormalsAttr().Get()) == 3
        assert mesh.GetNormalsInterpolation() == UsdGeom.Tokens.vertex
        # Triangles, not a subdivision cage: without this a renderer smooths
        # the model into something the kernel never built.
        assert mesh.GetSubdivisionSchemeAttr().Get() == UsdGeom.Tokens.none
        assert mesh.GetDoubleSidedAttr().Get()
        assert len(mesh.GetExtentAttr().Get()) == 2


def test_each_instance_carries_its_own_colour(pxr, tmp_path):
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(written(tmp_path, TWO_INSTANCES)))
    colors = {}
    for name in ("a", "b"):
        primvar = UsdGeom.Gprim(
            stage.GetPrimAtPath(f"/AgentCAD/{name}")).GetDisplayColorPrimvar()
        assert primvar.GetInterpolation() == UsdGeom.Tokens.constant
        colors[name] = tuple(round(c, 6) for c in primvar.Get()[0])
    assert colors["a"] == (0.215861, 0.215861, 0.215861)     # #808080, linear
    assert colors["b"] == (1.0, 0.0, 0.0)                    # #ff0000, linear


def euler_matrix_xyz(rotation_deg):
    """R = Rx · Ry · Rz on COLUMN vectors — the house convention
    (`service._apply_transform`), multiplied out by hand here so the assertion
    does not borrow the implementation's own arithmetic."""
    import math

    rx, ry, rz = (math.radians(a) for a in rotation_deg)

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]

    x = [[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]]
    y = [[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]]
    z = [[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]]
    return mul(mul(x, y), z)


@pytest.mark.parametrize("rotation", [
    [0, 0, 90], [90, 0, 0], [30, 45, 60], [-15, 0, 120],
])
def test_the_pose_is_our_intrinsic_xyz_euler_convention(pxr, tmp_path,
                                                        rotation):
    """The reason the pose is ONE `xformOp:transform` matrix and not
    `xformOp:rotateXYZ`: USD's rotate ops name the *application* order on row
    vectors, which is the transpose-composition of our intrinsic XYZ, and the
    two agree only when at most one angle is non-zero — a silent, 3-axis-only
    error. The matrix is built from the glTF writer's quaternion, and this
    test compares what USD *computes* against a hand-multiplied matrix."""
    from pxr import Gf, Usd, UsdGeom

    position = (7.0, -3.0, 11.0)
    path = written(tmp_path, [item(instance_id="a", position=position,
                                   rotation=rotation)])
    stage = Usd.Stage.Open(str(path))
    prim = stage.GetPrimAtPath("/AgentCAD/a")
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
        Usd.TimeCode.Default())
    assert list(matrix.ExtractTranslation()) == pytest.approx(list(position))

    expected_rotation = euler_matrix_xyz(rotation)
    for vector in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
                   (2.0, -5.0, 3.0)):
        expected = [sum(expected_rotation[r][c] * vector[c] for c in range(3))
                    + position[r] for r in range(3)]
        assert list(matrix.Transform(Gf.Vec3d(*vector))) == \
            pytest.approx(expected, abs=1e-9)


# ------------------------------------------------------------ end to end


def test_a_part_and_an_assembly_export_through_the_pack(pxr, svc):
    """The kernel-backed half: the real registry, the real mesh cache, the
    file on disk — and `fidelity` on both, which is the only thing that tells
    a caller a USD carries tessellation and no parametric intent."""
    from pxr import Usd, UsdGeom

    registry = build_registry(svc)
    result = svc.export_part("demo", "box", "usd")
    assert Path(result["path"]).name == "box.usda"
    assert result["fidelity"] == {"geometry": "mesh", "colors": "per_instance",
                                  "parametric": "none"}

    two_boxes(svc)
    result = registry.call("export_assembly", {"project": "demo",
                                               "format": "usd"})
    assert "error" not in result, result
    out = Path(result["path"])
    assert out.name == "assembly.usda"
    assert result["fidelity"]["colors"] == "per_instance"
    assert result["fidelity"]["geometry"] == "mesh"

    stage = Usd.Stage.Open(str(out))
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert sorted(p.GetName() for p in meshes) == ["a", "b"]
    # Two instances of ONE part: one prototype, one copy of the points.
    library = stage.GetPrimAtPath("/AgentCAD/Meshes")
    assert len([p for p in library.GetAllChildren() if p.IsA(UsdGeom.Mesh)]) == 1
    assert out.read_text().count("point3f[] points") == 1

    # A second export of the same state is the same file (FR7).
    first = out.read_bytes()
    registry.call("export_assembly", {"project": "demo", "format": "usd"})
    assert out.read_bytes() == first


def test_the_usd_route_is_the_wrapped_one(pxr, svc):
    registry = build_registry(svc)
    app = create_app(svc, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post("/api/projects/demo/parts/box/export",
                           json={"format": "usd"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert Path(body["path"]).name == "box.usda"
    assert body["fidelity"]["parametric"] == "none"


# --------------------------------------------------------------- OCP-free

_PROBE = '''
import importlib
import sys


class _Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("OCP", "build123d"):
            raise ImportError("blocked kernel import: " + name)
        return None


sys.meta_path.insert(0, _Blocked())
mod = importlib.import_module("agentcad.core.usd_export")
assert mod.METERS_PER_UNIT == 0.001 and mod.UP_AXIS == "Z"
assert "OCP" not in sys.modules and "build123d" not in sys.modules
# The whole gating scheme rests on this: importing the writer must NOT import
# pxr, or every server pays 40 MB to answer a yes/no question.
assert "pxr" not in sys.modules
print("ok")
'''


@pytest.mark.integration
@pytest.mark.portability
def test_the_usd_writer_is_ocp_free_and_imports_without_pxr():
    proc = subprocess.run([sys.executable, "-c", _PROBE], cwd=REPO,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")
