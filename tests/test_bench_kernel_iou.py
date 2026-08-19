"""The bench ``iou`` kernel handler (PRD-024, Decision 5, AC4).

Every volume assertion here is analytic — a 10 mm cube is 1000 mm3, a half
overlap of two of them is 500 mm3, so IoU is 500/1500 = 1/3 exactly. Nothing
is read back off ``.volume`` on a boolean result (a nested Compound
undercounts there); the handler answers with ``shape_volume``'s solids sum and
these numbers check it.

Three properties beyond the arithmetic are pinned here because they are the
ones that would rot silently: a mesh (STL) side is *skipped*, never booleaned
(an OCCT boolean on a welded mesh Face segfaults the worker, so the test's last
act is to prove the kernel is still answering); the candidate-side alignment
and rotation compose in one specific order (``translate(anchor_ref) .
rotate(r) . translate(-anchor_cand)``), which only a test combining a non-zero
anchor *and* a non-zero rotation can arbitrate; and ``iou`` is kernel-internal,
so no model-facing tool may ever be named after it.
"""

from __future__ import annotations

import pytest

from agentcad.core.tools import build_registry
from agentcad.kernel.client import KernelError

from .conftest import make_test_service

pytestmark = pytest.mark.portability

BOX = """
from build123d import *
PARAMS = {"sx": {"default": 10.0}, "sy": {"default": 10.0}, "sz": {"default": 10.0},
          "dx": {"default": 0.0}}

def build(p):
    with BuildPart() as part:
        with Locations((p.dx, 0, 0)):
            Box(p.sx, p.sy, p.sz)
    return part.part
"""


# Two 10 mm cubes overlapping each other by 5 mm in X. `shape_volume` sums
# `shape.solids()`, so this shape's reported volume is 2000 mm3 while the
# region it actually occupies is 1500 — the exact double-count the handler's
# clamp exists for.
OVERLAPPING_PAIR = """
from build123d import *
PARAMS = {}

def build(p):
    return Compound(children=[Box(10, 10, 10), Pos(5, 0, 0) * Box(10, 10, 10)])
"""


@pytest.fixture
def service(kernel, tmp_path):
    return make_test_service(tmp_path / "projects", kernel)


def _iou(kernel, candidate, reference, **kw):
    params = {"candidate": candidate, "reference": reference,
              "align": kw.pop("align", "world"),
              "rotations_deg": kw.pop("rotations_deg", [[0.0, 0.0, 0.0]])}
    params.update(kw)
    return kernel.request("iou", params, timeout_s=120.0)


def _export_stl(kernel, out_path):
    """A 10 mm cube as a real STL, written by the kernel's own exporter."""
    kernel.request("export", {"script": BOX, "params": {}, "format": "stl",
                              "out_path": str(out_path)})
    return str(out_path)


def test_identical_scripts_score_one(kernel):
    item = {"script": BOX, "params": {}}
    out = _iou(kernel, item, item)
    assert out["status"] == "ok"
    assert out["iou"] == pytest.approx(1.0, abs=1e-9)
    assert out["intersection_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert out["union_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert out["candidate_solids"] == 1
    assert out["reference_solids"] == 1
    assert out["align"] == "world"
    assert out["rotation_deg"] == [0.0, 0.0, 0.0]


def test_disjoint_shapes_score_zero(kernel):
    a = {"script": BOX, "params": {}}
    b = {"script": BOX, "params": {"dx": 50.0}}
    out = _iou(kernel, a, b)
    assert out["iou"] == 0.0
    assert out["intersection_mm3"] == 0.0
    assert out["union_mm3"] == pytest.approx(2000.0, rel=1e-6)


def test_half_overlap_is_the_analytic_value(kernel):
    a = {"script": BOX, "params": {}}
    b = {"script": BOX, "params": {"dx": 5.0}}
    out = _iou(kernel, a, b)
    # intersection 5x10x10 = 500; union 2000-500 = 1500; iou = 1/3
    assert out["intersection_mm3"] == pytest.approx(500.0, rel=1e-6)
    assert out["union_mm3"] == pytest.approx(1500.0, rel=1e-6)
    assert out["iou"] == pytest.approx(1.0 / 3.0, rel=1e-6)


def test_com_alignment_cancels_a_pure_translation(kernel):
    a = {"script": BOX, "params": {}}
    b = {"script": BOX, "params": {"dx": 25.0}}
    assert _iou(kernel, a, b)["iou"] == 0.0
    assert _iou(kernel, a, b, align="com")["iou"] == pytest.approx(1.0, abs=1e-6)


def test_bbox_center_alignment_cancels_a_pure_translation(kernel):
    a = {"script": BOX, "params": {}}
    b = {"script": BOX, "params": {"dx": 25.0}}
    out = _iou(kernel, a, b, align="bbox_center")
    assert out["align"] == "bbox_center"
    assert out["iou"] == pytest.approx(1.0, abs=1e-6)


def test_declared_rotation_recovers_a_rotated_reference(kernel):
    slab = BOX.replace('"sx": {"default": 10.0}', '"sx": {"default": 30.0}')
    a = {"script": slab, "params": {}}
    b = {"script": slab, "params": {}, "rotation_deg": [0.0, 0.0, 90.0]}
    assert _iou(kernel, a, b)["iou"] < 0.5
    out = _iou(kernel, a, b, rotations_deg=[[0.0, 0.0, 0.0], [0.0, 0.0, 90.0]])
    assert out["iou"] == pytest.approx(1.0, abs=1e-6)
    assert out["rotation_deg"] == [0.0, 0.0, 90.0]


def test_alignment_and_rotation_compose_in_that_order(kernel):
    """The one case that arbitrates the ``Location`` composition order.

    The candidate is a 30x10x10 slab centred at (25,0,0); the reference is the
    same slab spun 90 deg about Z and parked at (40,0,0). Only
    ``translate(anchor_ref) . rotate(90) . translate(-anchor_cand)`` lands them
    on each other. Both anchors are non-zero and the reference anchor is off
    the rotation axis, so all three ways of getting it wrong are caught:
    rotating before the de-centring sends the candidate to (0,25,0); applying
    ``translate(anchor_ref)`` *before* the rotation sends it to (0,40,0); and
    composing the two ``Location``s the other way round sends it to (15,25,0).
    An anchor *on* the Z axis would be invariant under this rotation and would
    silently pass the middle case — measured, not assumed.
    """
    slab = BOX.replace('"sx": {"default": 10.0}', '"sx": {"default": 30.0}')
    a = {"script": slab, "params": {"dx": 25.0}}
    b = {"script": slab, "params": {}, "rotation_deg": [0.0, 0.0, 90.0],
         "position": [40.0, 0.0, 0.0]}
    out = _iou(kernel, a, b, align="com", rotations_deg=[[0.0, 0.0, 90.0]])
    assert out["iou"] == pytest.approx(1.0, abs=1e-6)
    assert out["rotation_deg"] == [0.0, 0.0, 90.0]


def test_mesh_candidate_is_skipped_never_booleaned(kernel, tmp_path):
    stl = _export_stl(kernel, tmp_path / "cube.stl")
    out = _iou(kernel, {"source": stl}, {"script": BOX, "params": {}})
    assert out["status"] == "skipped_mesh"
    assert out["skipped_mesh"] == ["candidate"]
    assert out["iou"] == 0.0
    assert out["intersection_mm3"] == 0.0
    assert out["reference_volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    # The kernel is still alive: no boolean was ever attempted on the mesh.
    assert _iou(kernel, {"script": BOX, "params": {}},
                {"script": BOX, "params": {}})["iou"] == pytest.approx(1.0)


def test_multi_solid_candidate_sums_over_its_solids(kernel):
    """Two *disjoint* candidate solids: the pairwise sum is exact and the clamp
    sits harmlessly on its bound. The clamp itself is the next test."""
    two = BOX.replace("Box(p.sx, p.sy, p.sz)",
                      "Box(p.sx, p.sy, p.sz)\n        with Locations((30, 0, 0)):\n"
                      "            Box(p.sx, p.sy, p.sz)")
    out = _iou(kernel, {"script": two, "params": {}}, {"script": BOX, "params": {}})
    assert out["candidate_solids"] == 2
    assert out["candidate_volume_mm3"] == pytest.approx(2000.0, rel=1e-6)
    assert out["intersection_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert 0.0 <= out["iou"] <= 1.0
    assert out["iou"] == pytest.approx(0.5, rel=1e-6)


def test_a_self_overlapping_candidate_is_clamped(kernel):
    """The clamp doing real work.

    The candidate is two 10 mm cubes overlapping each other by 5 mm; the
    reference is one 10 mm cube coincident with the first. The pairwise sum
    double-counts the shared 5x10x10 slab and comes to 1000 + 500 = 1500 mm3 —
    more than the whole reference. Unclamped that is union 1500 and a perfect
    1.0 for a candidate that is visibly not the reference; clamped to
    ``min(sum, vol_a, vol_b) = vol_b = 1000`` it is union 2000 and 0.5.
    """
    out = _iou(kernel, {"script": OVERLAPPING_PAIR, "params": {}},
               {"script": BOX, "params": {}})
    assert out["candidate_solids"] == 2
    assert out["candidate_volume_mm3"] == pytest.approx(2000.0, rel=1e-6)
    assert out["reference_volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert out["intersection_mm3"] == pytest.approx(
        min(out["candidate_volume_mm3"], out["reference_volume_mm3"]), rel=1e-6)
    assert out["intersection_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert out["union_mm3"] == pytest.approx(2000.0, rel=1e-6)
    assert out["iou"] <= 1.0
    assert out["iou"] == pytest.approx(0.5, rel=1e-6)


def test_a_build_failure_degrades_to_a_typed_kernel_error(kernel):
    """FR7: the harness must be able to record `error`, never crash."""
    bad = {"script": "PARAMS = {}\ndef build(p):\n    raise RuntimeError('boom')\n",
           "params": {}}
    with pytest.raises(KernelError) as exc:
        _iou(kernel, bad, {"script": BOX, "params": {}})
    assert exc.value.type in ("script_error", "kernel_error")


def test_a_missing_reference_file_degrades_rather_than_crashing(kernel, tmp_path):
    missing = str(tmp_path / "absent.step")
    with pytest.raises(KernelError) as exc:
        _iou(kernel, {"source": missing}, {"script": BOX, "params": {}})
    assert exc.value.type in ("contract_error", "kernel_error")
    # ...and the worker is still answering.
    assert _iou(kernel, {"script": BOX, "params": {}},
                {"script": BOX, "params": {}})["status"] == "ok"


def test_an_unknown_align_mode_is_a_contract_error(kernel):
    item = {"script": BOX, "params": {}}
    with pytest.raises(KernelError) as exc:
        _iou(kernel, item, item, align="centroid")
    assert exc.value.type == "contract_error"


def test_an_empty_rotation_list_is_a_contract_error_not_the_identity(kernel):
    """An explicit `[]` must be refused, never quietly defaulted to [[0,0,0]]:
    a task that declares no permitted rotation and a task that omits the key
    are different claims, and only one of them is answerable."""
    item = {"script": BOX, "params": {}}
    with pytest.raises(KernelError) as exc:
        _iou(kernel, item, item, rotations_deg=[])
    assert exc.value.type == "contract_error"
    with pytest.raises(KernelError) as exc:
        _iou(kernel, item, item, rotations_deg={"z": 90})
    assert exc.value.type == "contract_error"


def test_an_absent_rotation_list_defaults_to_the_identity(kernel):
    """...while an omitted (or null) key is the documented default."""
    item = {"script": BOX, "params": {}}
    out = kernel.request("iou", {"candidate": item, "reference": item},
                         timeout_s=120.0)
    assert out["status"] == "ok"
    assert out["rotation_deg"] == [0.0, 0.0, 0.0]
    assert out["iou"] == pytest.approx(1.0, abs=1e-9)


def test_a_side_that_is_neither_script_nor_source_is_a_contract_error(kernel):
    with pytest.raises(KernelError) as exc:
        _iou(kernel, {}, {"script": BOX, "params": {}})
    assert exc.value.type == "contract_error"


def test_iou_is_kernel_internal_and_not_a_tool(service):
    names = {tool.name for tool in build_registry(service).list()}
    assert "iou" not in names
