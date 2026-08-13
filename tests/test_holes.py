"""PRD-010 slice 4 — `toolkit.holes`: ISO geometry and the records that ride it.

Two things are under test and they fail differently. The **geometry** is
build123d's `Hole` driven from the vendored ISO tables, so a wrong number here
is a wrong hole. The **records** are metadata riding on the returned shape,
where the failure mode is silence: the design's original registry design would
have drained empty on a worker shape-cache hit and nobody would have seen a
thing (changelog 0150 measures it through the worker). So the tests below
assert what the records survive as carefully as what the holes measure.
"""

import math

import pytest


def _plate(w=120.0, d=80.0, t=10.0):
    from build123d import Box

    return Box(w, d, t)


def _stepped_plate():
    """A plate with a raised pad: two candidate planar faces normal to Z."""
    from build123d import Box, Pos

    return Box(100, 100, 10) + Pos(0, 0, 10) * Box(40, 40, 10)


def _cyl_volume(diameter: float, length: float) -> float:
    return math.pi * (diameter / 2) ** 2 * length


# ------------------------------------------------------------ the geometry

def test_clearance_uses_the_iso_table_diameter():
    """AC3's numbers reaching the geometry: an M5 medium clearance hole is
    ISO 273's 5.5 mm, and the material removed says so to 6 decimals."""
    from agentcad.toolkit import holes

    plate = _plate()
    out, records, warning = holes.clearance(plate, [(0, 0), (30, 0)], "M5")
    assert warning is None
    assert records[0]["d"] == 5.5
    assert plate.volume - out.volume == pytest.approx(
        2 * _cyl_volume(5.5, 10.0), rel=1e-9)


def test_clearance_fit_changes_the_diameter_per_iso_273():
    from agentcad.toolkit import holes

    plate = _plate()
    fine = holes.clearance(plate, [(0, 0)], "M5", fit="fine")[1][0]["d"]
    coarse = holes.clearance(plate, [(0, 0)], "M5", fit="loose")[1][0]["d"]
    assert (fine, coarse) == (5.3, 5.8)


def test_blind_depth_removes_only_that_much_stock():
    from agentcad.toolkit import holes

    plate = _plate(t=20.0)
    out, records, warning = holes.clearance(plate, [(0, 0)], "M8", depth=6)
    assert warning is None
    assert records[0]["thru"] is False
    assert records[0]["depth_mm"] == 6.0
    assert plate.volume - out.volume == pytest.approx(
        _cyl_volume(9.0, 6.0), rel=1e-9)


def test_tapped_bores_the_tap_drill_and_records_the_thread():
    from agentcad.toolkit import holes

    plate = _plate(t=20.0)
    out, records, warning = holes.tapped(plate, [(0, 0)], "M5", depth=12)
    record = records[0]
    assert warning is None
    assert record["d"] == 4.2                      # the tap drill, not 5.0
    assert record["tap"] == {"pitch": 0.8, "class": "6H", "drill_mm": 4.2,
                             "thread": "M5×0.8", "series": "coarse",
                             "geometry": "none"}
    assert record["designation"] == "M5×0.8 - 6H ↧12"
    assert plate.volume - out.volume == pytest.approx(
        _cyl_volume(4.2, 12.0), rel=1e-9)


def test_tapped_with_real_thread_bores_at_the_root_radius():
    """The CHEATSHEET's hard-won rule, encoded: bore at the ROOT radius when
    real thread geometry is fused (boring at the physical tap drill buries the
    ridges in the wall — valid, fast and invisible), and say what it costs."""
    from agentcad.toolkit import holes

    plate = _plate(t=15.0)
    out, records, warning = holes.tapped(plate, [(0, 0)], "M6", depth=8,
                                         thread="real")
    assert records[0]["tap"]["bore_mm"] == 6.0     # M6 major, not the 5.0 drill
    assert records[0]["tap"]["geometry"] == "real"
    assert "9k triangles" in warning
    assert out.is_valid and len(out.solids()) == 1
    # the thread put material back into the bore
    assert plate.volume - out.volume < _cyl_volume(6.0, 8.0)


# -------------------------------------------------------- plane resolution

def test_named_plane_picks_the_extreme_face_and_keeps_part_coordinates():
    from agentcad.toolkit import holes

    plane = holes.resolve_plane(_stepped_plate(), "top")
    # the pad's face at z=15, not the plate's at z=5: `top` is the extreme
    # face along the axis, and only then the largest of the coplanar ones
    assert tuple(plane.origin) == (0, 0, 15)
    assert tuple(plane.z_dir) == (0, 0, 1)
    assert tuple(plane.x_dir) == (1, 0, 0)         # a point stays (x, y)


@pytest.mark.parametrize("name,origin,z_dir", [
    ("top", (0, 0, 5), (0, 0, 1)),
    ("bottom", (0, 0, -5), (0, 0, -1)),
    ("front", (0, -20, 0), (0, -1, 0)),
    ("back", (0, 20, 0), (0, 1, 0)),
    ("right", (30, 0, 0), (1, 0, 0)),
    ("left", (-30, 0, 0), (-1, 0, 0)),
])
def test_every_named_plane_resolves_to_its_outward_face(name, origin, z_dir):
    from build123d import Box

    from agentcad.toolkit import holes

    plane = holes.resolve_plane(Box(60, 40, 10), name)
    assert tuple(plane.origin) == origin
    assert tuple(plane.z_dir) == z_dir


def test_a_hole_on_a_named_side_face_lands_where_the_table_says():
    """The docstring's mapping table is a contract: on `front`, `(u, v)` is
    `(x, z)`."""
    from build123d import Box

    from agentcad.toolkit import holes

    block = Box(60, 40, 20)
    out, records, _warning = holes.clearance(
        block, [(10, 5)], "M6", plane="front", depth=8)
    assert records[0]["centers"] == [[10.0, -20.0, 5.0]]
    assert records[0]["axis"] == [0.0, 1.0, 0.0]
    assert block.volume - out.volume == pytest.approx(
        _cyl_volume(6.6, 8.0), rel=1e-9)


def test_a_plane_that_resolves_to_nothing_raises_naming_the_reason():
    from build123d import Sphere

    from agentcad.toolkit import holes

    with pytest.raises(ValueError, match="no planar face"):
        holes.resolve_plane(Sphere(20), "top")


def test_an_explicit_plane_is_used_verbatim():
    from build123d import Plane

    from agentcad.toolkit import holes

    plate = _plate()
    out, records, _warning = holes.clearance(plate, [(0, 0)], "M5",
                                             plane=Plane.XY)
    assert records[0]["plane"]["origin"] == [0.0, 0.0, 0.0]
    assert plate.volume - out.volume == pytest.approx(
        _cyl_volume(5.5, 10.0), rel=1e-9)


# ------------------------------------------------------------- the records

def test_the_record_is_one_group_per_call():
    """FR3 for free: N points is one record with `count: N`, not N records."""
    from agentcad.toolkit import patterns
    from agentcad.toolkit import holes

    points = patterns.bolt_circle(30, 4)
    _out, records, _warning = holes.clearance(_plate(), points, "M5")
    assert len(records) == 1
    record = records[0]
    assert record["count"] == 4
    assert record["positions"] == [list(point) for point in points]
    assert record["designation"] == "⌀5.5"
    assert record["family"] == "clearance"
    assert record["standard"] == "iso"
    assert record["id"] == "h0"


def test_records_accumulate_and_ids_are_stable_within_a_build():
    from agentcad.toolkit import holes

    part = _plate()
    part, _r1, _w1 = holes.clearance(part, [(0, 0)], "M5")
    part, _r2, _w2 = holes.tapped(part, [(40, 0)], "M6", depth=6)
    assert [record["id"] for record in holes.records(part)] == ["h0", "h1"]
    assert [record["family"] for record in holes.records(part)] == [
        "clearance", "tapped"]


def test_records_survive_the_safe_helpers():
    """Design Decision 4's "records compose along the helper chain", which is
    only true because `safe_fillet`/`safe_shell`/`safe_bool` were taught to
    carry them: measured through the worker, every one of them returns a brand
    new object with none of the original's attributes (changelog 0150)."""
    from build123d import Axis, Box, Pos

    from agentcad.toolkit import holes, safe_bool, safe_fillet

    part, _records, _warning = holes.clearance(_plate(), [(0, 0)], "M5")
    filleted, _radius, _w = safe_fillet(part, part.edges().filter_by(Axis.Z),
                                        3.0)
    assert [r["id"] for r in holes.records(filleted)] == ["h0"]
    fused, _w2 = safe_bool(part, Pos(0, 0, 10) * Box(20, 20, 10), "fuse")
    assert [r["id"] for r in holes.records(fused)] == ["h0"]


def test_a_raw_operation_drops_the_records_and_the_delta_check_says_so():
    """The gap the design states plainly, and the warning slice 5's harvest
    prints. The delta is the whole mechanism: it needs no resettable global, so
    it cannot be contaminated by another build on the same warm worker."""
    from build123d import Cylinder

    from agentcad.toolkit import holes

    before = holes.created()
    part, _records, _warning = holes.clearance(_plate(), [(0, 0)], "M5")
    raw = part - Cylinder(2, 40)                   # a new object, no attribute
    assert holes.records(raw) == []
    warning = holes.dropped_records_warning(raw, before)
    assert "1 hole record(s) were created but did not reach the returned part" \
        in warning
    assert "holes.carry(new_part, old_part)" in warning
    # and the escape hatch works
    assert holes.records(holes.carry(raw, part)) == holes.records(part)
    assert holes.dropped_records_warning(holes.carry(raw, part), before) is None


def test_no_records_created_means_no_delta_warning():
    """A zero delta means `build(p)` never ran — the worker served the shape
    from `_SHAPE_CACHE` — and the check must stay silent rather than accuse a
    cache hit of dropping records."""
    from agentcad.toolkit import holes

    assert holes.dropped_records_warning(_plate(), holes.created()) is None


def test_created_is_monotonic_and_never_reset():
    from agentcad.toolkit import holes

    first = holes.created()
    holes.clearance(_plate(), [(0, 0)], "M5")
    holes.clearance(_plate(), [(0, 0)], "M5")
    assert holes.created() == first + 2


# ------------------------------------------------------------- the warnings

def test_an_off_part_instance_warns_and_names_its_index():
    """OCCT's silent success, caught. Measured through the worker: the cut
    below takes ~1 ms, changes the volume by exactly 0.0 and reports
    `is_valid True` (changelog 0149) — nothing but this guard notices."""
    from agentcad.toolkit import holes

    plate = _plate()
    out, records, warning = holes.clearance(plate, [(0, 0), (500, 0)], "M5")
    assert "instance(s) [1]" in warning
    assert "silent no-op" in warning
    assert records[0]["instances"][1]["status"] == "missed"
    # ... and the geometry really is unchanged by that instance
    assert plate.volume - out.volume == pytest.approx(
        _cyl_volume(5.5, 10.0), rel=1e-9)


def test_every_instance_missing_warns_that_nothing_was_removed():
    from agentcad.toolkit import holes

    _out, _records, warning = holes.clearance(_plate(), [(500, 0)], "M5")
    assert "nothing was removed" in warning
    assert "OCCT does not fail on a misplaced cut" in warning


def test_exact_verify_reports_engaged_volume_per_instance():
    from agentcad.toolkit import holes

    _out, records, _warning = holes.clearance(
        _plate(), [(0, 0), (500, 0)], "M8", verify="exact")
    report = records[0]["instances"]
    assert report[0]["probe"] == "exact"
    assert report[0]["engaged_mm3"] == pytest.approx(
        _cyl_volume(9.0, 10.0), rel=1e-6)
    assert report[1]["status"] == "missed"


def test_holes_closer_than_one_diameter_warn_naming_the_pair():
    from agentcad.toolkit import holes

    _out, _records, warning = holes.clearance(_plate(), [(0, 0), (3, 0)], "M5")
    assert "instance pair(s) 0&1" in warning
    assert "merge into a slot" in warning


def test_a_new_hole_near_an_existing_record_warns_naming_it():
    from agentcad.toolkit import holes

    part, _records, _warning = holes.clearance(_plate(), [(0, 0)], "M5")
    _out, _r2, warning = holes.clearance(part, [(2, 0)], "M5")
    assert "0 near h0" in warning
    assert "within one diameter" in warning


def test_a_depth_deeper_than_the_stock_warns():
    from agentcad.toolkit import holes

    _out, _records, warning = holes.clearance(_plate(t=10.0), [(0, 0)], "M5",
                                              depth=25)
    assert "deeper than the stock" in warning
    assert "breaks through" in warning


def test_verify_off_skips_the_guard_entirely():
    from agentcad.toolkit import holes

    _out, records, warning = holes.clearance(
        _plate(), [(500, 0)], "M5", verify="off")
    assert records[0]["instances"] == []
    # the whole-operation volume delta is free, so it still speaks up
    assert "nothing was removed" in warning


def test_the_hole_route_falls_back_to_safe_bool_and_says_so(monkeypatch):
    """The fallback rung of design Decision 1: when build123d's `Hole` block
    refuses, one `safe_bool` cut of all the tools still produces the geometry,
    and the warning admits the bytes may have moved (a compound cut measured
    the same volume and a different mesh in slice 1). The failure is injected
    because a genuine one is not reproducible on demand — and an untested rung
    is a rung that does not work.
    """
    from agentcad.toolkit import holes

    def explode(*args, **kwargs):
        raise RuntimeError("BRep_API: command not done")

    monkeypatch.setattr(holes, "BuildPart", explode)
    plate = _plate()
    out, records, warning = holes.clearance(plate, [(0, 0), (30, 0)], "M5")
    assert "fell back to a safe_bool cut" in warning
    assert "may not be byte-identical" in warning
    assert plate.volume - out.volume == pytest.approx(
        2 * _cyl_volume(5.5, 10.0), rel=1e-6)
    # the record still describes the holes that were actually cut
    assert records[0]["count"] == 2
    assert holes.records(out) == records


# ---------------------------------------------------------------- the raises

@pytest.mark.parametrize("call,message", [
    (lambda h, part: h.clearance(part, [(0, 0)], "M4.5"), "size"),
    (lambda h, part: h.clearance(part, [(0, 0)], "M5", fit="snug"), "fit"),
    (lambda h, part: h.clearance(part, [(0, 0)], "M5", std="jis"), "std"),
    (lambda h, part: h.clearance(part, [], "M5"), "points is empty"),
    (lambda h, part: h.clearance(part, [(0, 0)], "M5", depth=-2), "depth"),
    (lambda h, part: h.clearance(part, [(0, 0)], "M5", thru=False), "depth"),
    (lambda h, part: h.clearance(part, [(0, 0)], "M5", plane="topmost"),
     "plane"),
    (lambda h, part: h.tapped(part, [(0, 0)], "M5", pitch=0.9), "pitch"),
    (lambda h, part: h.tapped(part, [(0, 0)], "M5", thread="cosmetic"),
     "thread"),
    (lambda h, part: h.tapped(part, [(0, 0)], "M5", thread="real"), "depth"),
])
def test_impossible_requests_raise_naming_the_argument(call, message):
    """Every one of these surfaces as a normal structured `script_error` with a
    line number when it happens inside a part script — the `toolkit/specs.py`
    convention."""
    from agentcad.toolkit import holes

    with pytest.raises(ValueError, match=message):
        call(holes, _plate())


# ------------------------------------------------------------------ identity

HELPER = '''
from build123d import *
from agentcad.toolkit import holes

PARAMS = {"d": {"default": 5.5, "min": 1.0, "max": 20.0, "unit": "mm"}}

def build(p):
    with BuildPart() as bp:
        Box(120, 80, 10)
    part, records, warning = holes.clearance(
        bp.part, [(30.0, 20.0), (-30.0, 20.0), (30.0, -20.0), (-30.0, -20.0)],
        "M5", plane=%s)
    return part
'''

HANDWRITTEN = '''
from build123d import *

PARAMS = {"d": {"default": 5.5, "min": 1.0, "max": 20.0, "unit": "mm"}}

def build(p):
    with BuildPart() as bp:
        Box(120, 80, 10)
    with BuildPart() as out:
        add(bp.part)
        with Locations((30.0, 20.0), (-30.0, 20.0), (30.0, -20.0),
                       (-30.0, -20.0)):
            Hole(radius=5.5 / 2)
    return out.part
'''


@pytest.mark.integration
def test_clearance_on_plane_xy_is_byte_identical_to_the_handwritten_form(
        kernel, tmp_path):
    """Design Decision 1 for `holes`: on the same plane the helper *is* the
    hand-written `Locations` + `Hole`, down to the tessellated bytes. This is
    what makes slice 7's rewrite of the bundled construction parts a safe
    edit rather than a re-baselining exercise."""
    from .test_examples_golden import REL, script_acm_sha256

    sha_helper, metrics_helper = script_acm_sha256(
        kernel, HELPER % "Plane.XY", mesh_path=tmp_path / "helper.acm")
    sha_hand, metrics_hand = script_acm_sha256(
        kernel, HANDWRITTEN, mesh_path=tmp_path / "hand.acm")
    assert metrics_helper["volume_mm3"] == pytest.approx(
        metrics_hand["volume_mm3"], rel=REL)
    assert sha_helper == sha_hand, (
        "holes.clearance no longer reproduces the hand-written form byte for "
        "byte — the helper has stopped being a wrapper")


@pytest.mark.integration
def test_the_named_top_plane_is_byte_identical_to_plane_xy_for_a_through_hole(
        kernel, tmp_path):
    """A measured fact slice 7 needs, asserted so it cannot rot.

    `plane="top"` puts the workplane on the top face (z=5 here); `Plane.XY`
    puts it at the part origin. The through-hole tool therefore starts in a
    different place — and the tessellated result is **identical**, measured
    (this test). That was not obvious: slice 1 measured that two constructions
    with the same volume and face count can still tessellate differently
    (changelog 0147), so a rewrite that swaps `Locations(...)`/`Hole` for
    `holes.clearance(..., plane="top")` keeps its `.acm` bytes, which is
    exactly what restated-AC1 (b) asks of slice 7.

    It is a claim about a THROUGH hole. A blind hole measures its depth from
    the plane, so there the two planes are different geometry, not different
    bytes.
    """
    from .test_examples_golden import REL, script_acm_sha256

    sha_top, metrics_top = script_acm_sha256(
        kernel, HELPER % '"top"', mesh_path=tmp_path / "top.acm")
    sha_xy, metrics_xy = script_acm_sha256(
        kernel, HELPER % "Plane.XY", mesh_path=tmp_path / "xy.acm")
    assert metrics_top["volume_mm3"] == pytest.approx(
        metrics_xy["volume_mm3"], rel=REL)
    assert metrics_top["n_faces"] == metrics_xy["n_faces"]
    assert sha_top == sha_xy
