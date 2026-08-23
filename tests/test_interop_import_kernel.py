"""Structured CAD import, kernel half (PRD-017 FR8) — the XCAF walk.

The fixture is a nested, coloured, multi-product assembly STEP authored
**in-suite through the real kernel** (the `test_reference.py::_make_step`
idiom, extended to raw XCAF because build123d cannot author a product tree):
no binary blobs in the repo, and — like every other test module here — this
process never imports OCP or build123d. The authoring script runs inside the
worker, which is the only process allowed to.

Fixture shape (the spike's section-C assembly):

    TopAssembly
      bracket_1                     Bracket  (0,0,0)
      pinpair_1                     PinPair  (0,0,10)
        pin_1                       Pin      -> (0,0,10)
        pin_2                       Pin      -> (30,0,10)  rot [0,90,0]
      pinpair_2                     PinPair  (0,50,10) rotated 90 deg about Z
        pin_1                       Pin      -> (0,50,10)  rot [0,0,90]
        pin_2                       Pin      -> (0,80,10)  rot [-90,0,90]
      ball_1                        Ball     (5,5,40)
      ball_2                        Ball     (-5,-5,40)    per-occurrence colour

3 unique products, 7 leaf occurrences, one nested level, product colours, one
occurrence colour override, and the spike's (0,80,10) composed-transform case.

Two deliberate details make the rotation half of this suite load-bearing:
`Pin` is a MIN-aligned box (a centred or cylindrical part places identically
under +90 and -90, so a sign error would be invisible in a bounding box), and
`pin_2` carries a *local* 90 deg Y rotation, so its composed orientation under
`pinpair_2` is a two-axis product Rz(90)·Ry(90). Intrinsic XYZ decomposes that
to [-90,0,90]; extrinsic XYZ would answer [0,90,90]. A pure Z rotation cannot
tell the two sequences apart.
"""

import pytest

from agentcad.kernel.client import KernelError

pytestmark = pytest.mark.portability


# --------------------------------------------------------------- the fixture

_AUTHOR_SCRIPT = '''\
"""Authors a nested multi-product assembly STEP via raw XCAF (spike C)."""
import math

from build123d import Align, Box, Sphere, Solid
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

PARAMS = {}

OUT = __OUT__

MIN = (Align.MIN, Align.MIN, Align.MIN)


def _loc(dx=0.0, dy=0.0, dz=0.0, rot_deg=0.0, axis=(0, 0, 1)):
    trsf = gp_Trsf()
    if rot_deg:
        trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis)),
                         math.radians(rot_deg))
    trsf.SetTranslationPart(gp_Vec(dx, dy, dz))
    return TopLoc_Location(trsf)


def _name(label, text):
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(text))


def build(p):
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    products = {}
    for label_text, shape, rgb in (
        ("Bracket", Box(20, 10, 4).solid().wrapped, (0.85, 0.20, 0.15)),
        ("Pin", Box(4, 2, 12, align=MIN).solid().wrapped, (0.15, 0.45, 0.90)),
        ("Ball", Sphere(3).solid().wrapped, (0.10, 0.70, 0.30)),
    ):
        # Spike trap 1: AddShape on a *located* shape makes a reference label.
        label = shape_tool.AddShape(shape.Located(TopLoc_Location()), False)
        _name(label, label_text)
        color_tool.SetColor(label, Quantity_Color(*rgb, Quantity_TOC_sRGB),
                            XCAFDoc_ColorSurf)
        products[label_text] = label

    sub = shape_tool.NewShape()
    _name(sub, "PinPair")
    # pin_2's own 90 deg Y rotation makes the composed orientation under the
    # Z-rotated pinpair_2 a genuine two-axis product (see the module docstring).
    for i, loc in enumerate((_loc(), _loc(30, 0, 0, rot_deg=90, axis=(0, 1, 0)))):
        _name(shape_tool.AddComponent(sub, products["Pin"], loc),
              "pin_" + str(i + 1))

    top = shape_tool.NewShape()
    _name(top, "TopAssembly")
    _name(shape_tool.AddComponent(top, products["Bracket"], _loc()), "bracket_1")
    for i, loc in enumerate((_loc(0, 0, 10), _loc(0, 50, 10, rot_deg=90))):
        _name(shape_tool.AddComponent(top, sub, loc), "pinpair_" + str(i + 1))
    for i, loc in enumerate((_loc(5, 5, 40), _loc(-5, -5, 40))):
        component = shape_tool.AddComponent(top, products["Ball"], loc)
        _name(component, "ball_" + str(i + 1))
        if i == 1:
            color_tool.SetColor(
                component, Quantity_Color(1.0, 0.85, 0.0, Quantity_TOC_sRGB),
                XCAFDoc_ColorSurf)
    shape_tool.UpdateAssemblies()

    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    writer.SetLayerMode(True)
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(OUT)
    # The export handler needs a shape back; it is written to a throwaway path.
    return Solid.make_box(1, 1, 1)
'''

#: sRGB colours the fixture authors, as 0..1 triples.
BRACKET_RGB = (0.85, 0.20, 0.15)
PIN_RGB = (0.15, 0.45, 0.90)
BALL_RGB = (0.10, 0.70, 0.30)
BALL_2_OVERRIDE_RGB = (1.0, 0.85, 0.0)


def make_assembly_step(kernel, tmp_path, name="assembly"):
    """Author the fixture STEP inside the worker; return its path."""
    target = tmp_path / f"{name}.step"
    script = _AUTHOR_SCRIPT.replace("__OUT__", repr(str(target)))
    kernel.request("export", {
        "script": script, "params": {}, "format": "step",
        "out_path": str(tmp_path / "_scratch.step"),
    })
    assert target.is_file(), "fixture STEP was not written"
    return target


def _rgb(hex_color):
    assert isinstance(hex_color, str) and hex_color.startswith("#"), hex_color
    assert len(hex_color) == 7, hex_color
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _by_name(rows):
    return {row["name"]: row for row in rows}


@pytest.fixture(scope="module")
def tree(kernel, tmp_path_factory):
    """One inspect result shared by the read-only assertions."""
    tmp_path = tmp_path_factory.mktemp("interop_inspect")
    step = make_assembly_step(kernel, tmp_path)
    return kernel.request("inspect_cad_tree", {"source_path": str(step)})


# ------------------------------------------------------------ inspect_cad_tree


def test_counts_and_product_dedup(tree):
    """7 occurrences of 3 products — a product's label is shared by all of its
    occurrences, so `Pin` must appear exactly once in `products`."""
    assert tree["counts"] == {"products": 3, "occurrences": 7}
    assert len(tree["products"]) == 3
    assert len(tree["occurrences"]) == 7
    names = sorted(p["name"] for p in tree["products"])
    assert names == ["Ball", "Bracket", "Pin"]
    assert [p["index"] for p in tree["products"]] == [0, 1, 2]
    pin = next(p for p in tree["products"] if p["name"] == "Pin")
    # 4 pin occurrences (2 sub-assemblies x 2 pins) all point at that one product
    pin_occurrences = [o for o in tree["occurrences"]
                       if o["product_index"] == pin["index"]]
    assert len(pin_occurrences) == 4


def test_occurrence_names_are_distinct_across_parents(tree):
    """Pitfall 5: `pin_1` under `pinpair_1` and under `pinpair_2` are the same
    XCAF label. Path-derived qualification keeps them apart."""
    names = [o["name"] for o in tree["occurrences"]]
    assert len(set(names)) == 7, names
    assert set(names) == {
        "bracket_1", "ball_1", "ball_2",
        "pinpair_1_pin_1", "pinpair_1_pin_2",
        "pinpair_2_pin_1", "pinpair_2_pin_2",
    }
    # the path is the component-label chain, root assembly excluded
    occ = _by_name(tree["occurrences"])["pinpair_2_pin_2"]
    assert occ["path"] == ["pinpair_2", "pin_2"]
    assert any("unique" in w for w in tree["warnings"]), tree["warnings"]


def test_composed_transforms(tree):
    """Occurrences are flattened to one level with COMPOSED transforms — the
    spike's (0,80,10) case is `pin_2`'s local (30,0,0) through a sub-assembly
    placed at (0,50,10) with a 90 deg Z rotation."""
    occ = _by_name(tree["occurrences"])
    assert occ["bracket_1"]["position"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)
    assert occ["pinpair_1_pin_1"]["position"] == pytest.approx([0, 0, 10], abs=1e-6)
    assert occ["pinpair_1_pin_2"]["position"] == pytest.approx([30, 0, 10], abs=1e-6)
    assert occ["pinpair_2_pin_1"]["position"] == pytest.approx([0, 50, 10], abs=1e-6)
    assert occ["pinpair_2_pin_2"]["position"] == pytest.approx([0, 80, 10], abs=1e-6)
    assert occ["ball_1"]["position"] == pytest.approx([5, 5, 40], abs=1e-6)
    assert occ["ball_2"]["position"] == pytest.approx([-5, -5, 40], abs=1e-6)


def test_rotations_are_intrinsic_xyz_degrees(tree):
    """The house convention: degrees, intrinsic XYZ (`R = Rx.Ry.Rz`).

    `pinpair_2_pin_2` composes Rz(90) (the sub-assembly) onto Ry(90) (the
    component's own placement). Intrinsic XYZ decomposes that product to
    [-90,0,90]; OCCT's *extrinsic* XYZ sequence would answer [0,90,90], so
    this row — and only this row — pins the sequence itself.
    """
    occ = _by_name(tree["occurrences"])
    for name in ("bracket_1", "pinpair_1_pin_1", "ball_1", "ball_2"):
        assert occ[name]["rotation_deg"] == pytest.approx([0, 0, 0], abs=1e-6)
    assert occ["pinpair_1_pin_2"]["rotation_deg"] == pytest.approx(
        [0, 90, 0], abs=1e-6)
    assert occ["pinpair_2_pin_1"]["rotation_deg"] == pytest.approx(
        [0, 0, 90], abs=1e-6)
    assert occ["pinpair_2_pin_2"]["rotation_deg"] == pytest.approx(
        [-90, 0, 90], abs=1e-6)


def test_colors_are_srgb_hex_with_occurrence_override(tree):
    """Pitfall 4: `Quantity_Color.Red()` is LINEAR — 0.85 sRGB reads back as
    0.692 and every imported colour would darken. The tolerance here is far
    tighter than that gap, so this test fails on the linear read."""
    products = {p["name"]: p for p in tree["products"]}
    assert _rgb(products["Bracket"]["color"]) == pytest.approx(BRACKET_RGB, abs=0.01)
    assert _rgb(products["Pin"]["color"]) == pytest.approx(PIN_RGB, abs=0.01)
    assert _rgb(products["Ball"]["color"]) == pytest.approx(BALL_RGB, abs=0.01)

    occ = _by_name(tree["occurrences"])
    # ball_1 inherits the product colour; ball_2 carries the override that
    # lives on its COMPONENT label.
    assert _rgb(occ["ball_1"]["color"]) == pytest.approx(BALL_RGB, abs=0.01)
    assert _rgb(occ["ball_2"]["color"]) == pytest.approx(
        BALL_2_OVERRIDE_RGB, abs=0.01)
    assert occ["ball_2"]["color"] != occ["ball_1"]["color"]
    assert _rgb(occ["pinpair_2_pin_2"]["color"]) == pytest.approx(PIN_RGB, abs=0.01)


def test_tree_is_the_nested_original_structure(tree):
    """`occurrences` is flat; `tree` keeps the authored nesting."""
    assert len(tree["tree"]) == 1
    root = tree["tree"][0]
    assert root["name"] == "TopAssembly"
    children = {c["name"]: c for c in root["children"]}
    assert set(children) == {"bracket_1", "pinpair_1", "pinpair_2",
                             "ball_1", "ball_2"}
    assert children["bracket_1"]["children"] == []
    nested = children["pinpair_2"]
    assert [c["name"] for c in nested["children"]] == ["pin_1", "pin_2"]
    # leaves reference a product; the sub-assembly node does not
    assert "product_index" in children["ball_1"]
    assert "product_index" not in nested


def test_inspect_writes_nothing(kernel, tmp_path):
    step = make_assembly_step(kernel, tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    kernel.request("inspect_cad_tree", {"source_path": str(step)})
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_single_product_file_is_still_valid_output(kernel, tmp_path):
    """FR9's floor: one product, one occurrence, no assembly at all."""
    plain = tmp_path / "solo.step"
    kernel.request("export", {
        "script": ("import build123d as b3d\n"
                   "PARAMS = {}\n"
                   "def build(p):\n"
                   "    return b3d.Solid.make_box(10, 10, 10)\n"),
        "params": {}, "format": "step", "out_path": str(plain),
    })
    out = kernel.request("inspect_cad_tree", {"source_path": str(plain)})
    assert out["counts"] == {"products": 1, "occurrences": 1}
    occ = out["occurrences"][0]
    assert occ["product_index"] == 0
    assert occ["position"] == pytest.approx([0, 0, 0], abs=1e-9)
    assert occ["rotation_deg"] == pytest.approx([0, 0, 0], abs=1e-9)
    assert occ["name"]


# ----------------------------------------------------------- import_structured


@pytest.fixture
def imported(kernel, tmp_path):
    step = make_assembly_step(kernel, tmp_path)
    out_dir = tmp_path / "refs"
    result = kernel.request("import_structured", {
        "source_path": str(step), "out_dir": str(out_dir)})
    return step, out_dir, result


def test_import_writes_one_brep_per_unique_product(imported):
    step, out_dir, result = imported
    assert result["counts"] == {"products": 3, "occurrences": 7}
    files = sorted(p.name for p in out_dir.iterdir())
    assert len(files) == 3, files
    assert files == sorted(p["file"] for p in result["products"])
    # deterministic, sanitized, basename-only (never an absolute path)
    for product in result["products"]:
        name = product["file"]
        assert name == f"assembly__{product['index']}_{product['name'].lower()}.brep"
        assert "/" not in name
        assert (out_dir / name).is_file()
    # no torn temporaries left behind
    assert not [f for f in files if f.endswith(".tmp")]


def test_import_filenames_are_deterministic(kernel, tmp_path, imported):
    step, out_dir, result = imported
    again = kernel.request("import_structured", {
        "source_path": str(step), "out_dir": str(tmp_path / "refs2")})
    assert [p["file"] for p in again["products"]] == \
        [p["file"] for p in result["products"]]
    for product in result["products"]:
        a = (out_dir / product["file"]).read_bytes()
        b = (tmp_path / "refs2" / product["file"]).read_bytes()
        assert a == b


def test_products_load_through_refload_as_solids(kernel, tmp_path, imported):
    """The whole point of materializing `.brep`: each product lands as a plain
    reference part through the existing, tested pipeline."""
    _step, out_dir, result = imported
    expected = {"Bracket": 20 * 10 * 4, "Pin": 4 * 2 * 12, "Ball": 4 / 3 * 3.14159265 * 27}
    for product in result["products"]:
        out = kernel.request("build_reference", {
            "source_path": str(out_dir / product["file"]),
            "mesh_path": str(tmp_path / f"{product['file']}.acm"),
            "density_g_cm3": 1.0, "tolerance": 0.1,
        })
        assert out["kind"] == "solid"
        metrics = out["metrics"]
        assert metrics["is_valid"] is True
        assert metrics["n_solids"] == 1
        assert metrics["mesh"] is False
        assert metrics["volume_mm3"] == pytest.approx(
            expected[product["name"]], rel=1e-3)


def test_replacing_a_brep_reproduces_the_composed_placement(kernel, tmp_path,
                                                            imported):
    """The Euler decomposition must match the house convention: re-placing the
    product's `.brep` with `b3d.Location(position, rotation_deg)` reproduces
    the composed placement. `Pin` is MIN-aligned and its composed orientation
    is two-axis, so a sign error or an extrinsic ordering moves the box into a
    different octant and the bounding box below fails."""
    _step, out_dir, result = imported
    pin = next(p for p in result["products"] if p["name"] == "Pin")
    occ = _by_name(result["occurrences"])["pinpair_2_pin_2"]
    assert occ["product_index"] == pin["index"]

    brep = str(out_dir / pin["file"])
    script = (
        "import build123d as b3d\n"
        "PARAMS = {}\n"
        "def build(p):\n"
        f"    shape = b3d.import_brep({brep!r})\n"
        f"    return shape.moved(b3d.Location({tuple(occ['position'])!r}, "
        f"{tuple(occ['rotation_deg'])!r}))\n"
    )
    out = kernel.request("build", {
        "script": script, "params": {},
        "mesh_path": str(tmp_path / "placed.acm"), "tolerance": 0.1,
    })
    bbox = out["metrics"]["bbox"]
    # Pin occupies local 0..4 x 0..2 x 0..12; Rz(90).Ry(90) maps (x,y,z) to
    # (-y, z, -x), so around (0,80,10) it lands at -2..0 x 80..92 x 6..10.
    assert bbox["min"] == pytest.approx([-2.0, 80.0, 6.0], abs=1e-6)
    assert bbox["max"] == pytest.approx([0.0, 92.0, 10.0], abs=1e-6)


# ------------------------------------------------------------------- refusals


def test_garbage_file_is_a_clean_error(kernel, tmp_path):
    junk = tmp_path / "junk.step"
    junk.write_bytes(b"this is not a STEP file\n" * 40)
    with pytest.raises(KernelError) as exc:
        kernel.request("inspect_cad_tree", {"source_path": str(junk)})
    assert exc.value.type in ("contract_error", "kernel_error")
    # ...and the worker is still answering.
    assert kernel.request("ping", {})["ok"] is True


def test_missing_and_unsupported_sources_are_refused(kernel, tmp_path):
    with pytest.raises(KernelError) as exc:
        kernel.request("inspect_cad_tree",
                       {"source_path": str(tmp_path / "absent.step")})
    assert exc.value.type == "contract_error"

    stl = tmp_path / "blob.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    with pytest.raises(KernelError) as exc:
        kernel.request("inspect_cad_tree", {"source_path": str(stl)})
    assert exc.value.type == "contract_error"
    assert "STEP" in exc.value.message


def test_import_structured_needs_an_out_dir(kernel, tmp_path):
    step = make_assembly_step(kernel, tmp_path)
    with pytest.raises(KernelError) as exc:
        kernel.request("import_structured", {"source_path": str(step)})
    assert exc.value.type == "contract_error"
