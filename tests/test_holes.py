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
    assert record["tap"] == {"pitch": 0.8, "tpi": None, "class": "6H",
                             "drill_mm": 4.2, "drill": None,
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
    assert "reaches the far side of the stock" in warning
    assert "breaks through" in warning


def test_a_depth_that_exactly_equals_the_stock_is_not_blind_and_says_so():
    """**Regression.** The guard tested `depth > stock`, so `depth=t` on a `t`
    plate — the spelling an author actually writes — passed silently, and the
    record then carried `thru: false` with a depth the geometry does not have.
    The drawing printed `↧12` on a hole open at both ends. `stock` is a
    bounding-box extent, so reaching it is as certainly through as exceeding
    it, and the equality case is the common one."""
    from agentcad.toolkit import holes

    _out, records, warning = holes.clearance(_plate(t=10.0), [(0, 0)], "M5",
                                             depth=10.0)
    assert "reaches the far side of the stock" in warning
    assert "Use thru=True" in warning
    assert records[0]["thru"] is False      # the record still reports what was
    assert records[0]["depth_mm"] == 10.0   # asked for; the warning is the fact


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


# ------------------------------------------------- counterbore / countersink

def _frustum_volume(r_big: float, r_small: float, h: float) -> float:
    return math.pi * h / 3.0 * (r_big ** 2 + r_big * r_small + r_small ** 2)


def test_counterbore_removes_the_clearance_hole_plus_the_head_pocket():
    """The through hole is the ISO 273 clearance hole for the fastener; the
    pocket above it is the head, plus this repo's named clearance rule (the
    published counterbore charts disagree with each other — changelog 0148)."""
    from agentcad.toolkit import holes

    plate = _plate(t=20.0)
    out, records, warning = holes.counterbore(plate, [(0, 0)], "M5")
    record = records[0]
    assert warning is None
    assert record["family"] == "counterbore"
    assert record["d"] == 5.5                       # ISO 273 medium
    assert record["cbore"] == {"d": 10.0, "depth": 5.8,
                               "fastener": "iso4762"}
    assert record["designation"] == "⌀5.5 ⌴⌀10↧5.8"
    expected = (_cyl_volume(5.5, 20.0)
                + math.pi * (5.0 ** 2 - 2.75 ** 2) * 5.8)
    assert plate.volume - out.volume == pytest.approx(expected, rel=1e-9)


def test_countersink_uses_the_standards_angle_not_build123ds_default():
    """build123d's `CounterSinkHole` defaults to 82 deg — an ASME default that
    would otherwise arrive inside an ISO-labelled call. `holes.countersink`
    passes the angle explicitly, always, and the removed volume is the proof:
    at 90 deg the cone is shallower than at 82 and the two differ by a
    measurable amount, so a silently-inherited default cannot pass this."""
    from agentcad.toolkit import holes

    plate = _plate(t=20.0)
    out, records, warning = holes.countersink(plate, [(0, 0)], "M5")
    record = records[0]
    assert warning is None
    assert record["csk"] == {"d": 11.2, "angle_deg": 90.0,
                             "fastener": "iso10642"}
    assert record["designation"] == "⌀5.5 ⌵⌀11.2×90°"

    def removed(angle_deg: float) -> float:
        cone_h = (5.6 - 2.75) / math.tan(math.radians(angle_deg / 2.0))
        return (_cyl_volume(5.5, 20.0)
                + _frustum_volume(5.6, 2.75, cone_h)
                - math.pi * 2.75 ** 2 * cone_h)

    measured = plate.volume - out.volume
    assert measured == pytest.approx(removed(90.0), rel=1e-6)
    assert measured != pytest.approx(removed(82.0), rel=1e-3)


def test_an_explicit_countersink_angle_is_honoured():
    from agentcad.toolkit import holes

    _out, records, _warning = holes.countersink(
        _plate(t=20.0), [(0, 0)], "M5", angle=82.0)
    assert records[0]["csk"]["angle_deg"] == 82.0
    assert records[0]["designation"] == "⌀5.5 ⌵⌀11.2×82°"


def test_a_counterbore_deeper_than_the_stock_warns():
    from agentcad.toolkit import holes

    _out, _records, warning = holes.counterbore(
        _plate(t=4.0), [(0, 0)], "M8", verify="off")
    assert "counterbore" in warning and "4" in warning


def test_ansi_holes_measure_in_millimetres_and_call_out_in_inches():
    """The unit split, end to end: the geometry is a 0.281 in hole in
    millimetres, and the callout is what a US drawing prints."""
    from agentcad.toolkit import holes

    plate = _plate(w=60.0, d=60.0, t=25.0)
    out, records, _warning = holes.counterbore(
        plate, [(0, 0)], "1/4", std="ansi")
    record = records[0]
    assert record["d"] == pytest.approx(0.281 * 25.4)
    assert record["designation"] == "⌀0.281 ⌴⌀0.4375↧0.2812"
    assert plate.volume - out.volume == pytest.approx(
        _cyl_volume(0.281 * 25.4, 25.0)
        + math.pi * ((0.4375 * 25.4 / 2) ** 2 - (0.281 * 25.4 / 2) ** 2)
        * 0.28125 * 25.4, rel=1e-9)

    _o, tapped, _w = holes.tapped(out, [(20, 20)], "1/4", std="ansi", depth=12.7)
    assert tapped[0]["d"] == pytest.approx(0.201 * 25.4)
    assert tapped[0]["designation"] == "1/4-20 UNC - 2B ↧0.5"


# --------------------------------------------------------- the drilled hole

def test_drill_cuts_the_stated_diameter_and_records_no_standard_size():
    """A structural bolt hole is millimetres, not an ISO row.

    `construction/gusset_plate` drills 18 mm for an M16 bolt — EN 1090's 2 mm
    clearance, which is not any of ISO 273's M16 values (17.0/17.5/18.5). The
    diameter is the design's own number, so `drill` takes it directly and the
    record claims no `size` and no table provenance. Inventing one would be a
    false provenance claim on a surface whose whole point is provenance.
    """
    from agentcad.toolkit import holes

    plate = _plate(t=10.0)
    out, records, warning = holes.drill(plate, [(0, 0), (30, 0)], 18.0)
    record = records[0]
    assert warning is None
    assert (record["family"], record["d"]) == ("drilled", 18.0)
    assert record["size"] is None and record["fit"] is None
    assert record["designation"] == "⌀18"
    assert plate.volume - out.volume == pytest.approx(
        2 * _cyl_volume(18.0, 10.0), rel=1e-9)


def test_drill_blind_records_the_depth_in_the_designation():
    from agentcad.toolkit import holes

    plate = _plate(t=20.0)
    _out, records, _warning = holes.drill(plate, [(0, 0)], 12.5, depth=8.0)
    assert records[0]["designation"] == "⌀12.5 ↧8"
    assert records[0]["thru"] is False


def test_a_drilled_hole_under_an_asme_label_calls_out_in_inches():
    """The diameter is millimetres either way — it is what gets drilled — but
    `⌀12.7` printed under an ASME label would read as a 12.7 INCH hole."""
    from agentcad.toolkit import holes

    _out, records, _warning = holes.drill(
        _plate(t=20.0), [(0, 0)], 12.7, depth=6.35, std="ansi")
    assert records[0]["d"] == 12.7
    assert records[0]["designation"] == "⌀0.5 ↧0.25"


def test_drill_shares_the_guard_with_the_table_driven_helpers():
    """One `_drill`, one guard: an off-part instance is named here too."""
    from agentcad.toolkit import holes

    plate = _plate()
    _out, _records, warning = holes.drill(plate, [(0, 0), (400, 0)], 10.0)
    assert "instance(s) [1]" in warning


# ---------------------------------------------------------------- the raises

@pytest.mark.parametrize("call,message", [
    (lambda h, part: h.drill(part, [(0, 0)], 0.0), "diameter"),
    (lambda h, part: h.drill(part, [(0, 0)], -3.0), "diameter"),
    (lambda h, part: h.drill(part, [(0, 0)], float("nan")), "diameter"),
    (lambda h, part: h.drill(part, [(0, 0)], "M5"), "diameter"),
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


# ------------------------------- R6: a normal that points INTO the material

def _inward_normal_slab():
    """A 40x60x20 slab spanning x in [0, 40], and a plane on its x=0 face
    whose z_dir is +X — pointing **into** the stock.

    This is the frame the bundled `angle_bracket` deliberately uses for its
    vertical leg (`Plane(origin=(0, 0, hz), z_dir=(1, 0, 0))`), chosen there
    because sliding a workplane along the hole axis is free while a named face
    would rotate the tool and re-tessellate the part.
    """
    from build123d import Box, Plane, Pos

    return Pos(20, 0, 0) * Box(40, 60, 20), Plane(origin=(0, 0, 0),
                                                  z_dir=(1, 0, 0))


def test_stock_is_measured_on_the_side_the_material_is_actually_on():
    """**Regression.** `_extent` measured only along `-z_dir`, so a plane whose
    normal points into the material reported **0 mm of stock** — for the
    repo's own flagship rewrite. Everything derived from it was then wrong."""
    from agentcad.toolkit import holes

    part, plane = _inward_normal_slab()
    assert holes._extent(part, plane) == pytest.approx(40.0)


def test_exact_verify_does_not_cry_wolf_on_an_inward_pointing_normal():
    """The hole is really cut — `Hole` drills a thru hole in both directions,
    which is why the primary route always worked — but the `exact` guard built
    its probe tool on the conventional `-z_dir` frame, i.e. in fresh air, and
    reported `flush, engaged 0.0` with a warning, on correct geometry."""
    from agentcad.toolkit import holes

    part, plane = _inward_normal_slab()
    out, records, warning = holes.drill(part, [(0, 0)], 10.0, plane=plane,
                                        verify="exact")

    removed = part.volume - out.volume
    assert removed == pytest.approx(_cyl_volume(10.0, 40.0), rel=1e-6)
    instance = records[0]["instances"][0]
    assert instance["status"] == "engaged", records[0]["instances"]
    assert instance["engaged_mm3"] == pytest.approx(removed, rel=1e-6)
    assert warning is None, warning
    # the recorded axis is the direction the tool actually travelled
    assert records[0]["axis"] == [1.0, 0.0, 0.0]


def test_the_safe_bool_fallback_cuts_through_an_inward_pointing_normal(
        monkeypatch):
    """`_bore`'s fallback builds the tool solid itself, so on this frame it
    built a **zero-height** cylinder (`reach = stock = 0`) and cut nothing —
    a silent no-op on the rung that exists to rescue the primary route."""
    from build123d import BuildPart

    from agentcad.toolkit import holes

    def explode(*_args, **_kwargs):
        raise RuntimeError("primary route down")

    monkeypatch.setattr(BuildPart, "__enter__", explode)

    part, plane = _inward_normal_slab()
    out, _records, warning = holes.drill(part, [(0, 0)], 10.0, plane=plane)
    assert "fell back to a safe_bool cut" in (warning or "")
    assert part.volume - out.volume == pytest.approx(
        _cyl_volume(10.0, 40.0), rel=1e-6)


# ------------------------------- the record may only claim what it removed


def _frame(outer=100.0, void=60.0, t=10.0):
    """A square frame: a plate with its middle cut out. Its bounding box is the
    whole outer square, so a hole drilled into the void is inside the box and
    outside the material — which is the shape that broke the default guard."""
    from build123d import Box, BuildPart, Mode

    with BuildPart() as builder:
        Box(outer, outer, t)
        Box(void, void, t, mode=Mode.SUBTRACT)
    return builder.part


@pytest.mark.parametrize("verify", ["bbox", "exact"])
def test_an_instance_that_cuts_air_is_not_in_the_record(verify):
    """**Regression, and the reason the default tier was rewritten.**

    Measured on this exact frame before the fix, with the public default
    `verify="bbox"`: one valid solid, `warning=None`, BOTH instances reported
    `engaged`, the removed volume exactly one cylinder (785.398163397 mm^3 for
    a ⌀10 x 10) and the record claimed `count: 2`. The bounding-box screen
    compares each tool against the WHOLE part's box, which the void is inside,
    and the volume check is aggregate — so one successful cut hid the no-op.

    Both tiers now answer per instance, and the record may claim only what
    demonstrably removed material.
    """
    from agentcad.toolkit import holes

    part = _frame()
    out, records, warning = holes.drill(part, [(40, 0), (0, 0)], 10.0,
                                        verify=verify)
    record = records[0]
    one_cylinder = _cyl_volume(10.0, 10.0)

    assert part.volume - out.volume == pytest.approx(one_cylinder, rel=1e-9)
    assert [row["status"] for row in record["instances"]] == ["engaged",
                                                              "missed"]
    assert record["count"] == 1
    assert record["positions"] == [[40.0, 0.0]]
    assert len(record["centers"]) == 1
    assert record["dropped"] == [{"i": 1, "status": "missed",
                                  "position": [0.0, 0.0]}]
    assert record["verify"] == verify
    assert "instance(s) [1] do not reach the part" in warning
    assert "the record claims 1 of 2 instance(s)" in warning


def test_the_default_tier_proves_engagement_on_the_axis_not_on_a_box():
    """The rungs are visible in the report: a hole through material is proved
    by an interior point on its own axis (`probe: "axis"`, no boolean), and
    only an instance nothing cheap could decide pays for the exact probe."""
    from agentcad.toolkit import holes

    _out, records, _warning = holes.drill(_frame(), [(40, 0), (0, 0)], 10.0)
    report = records[0]["instances"]
    assert (report[0]["status"], report[0]["probe"]) == ("engaged", "axis")
    assert (report[1]["status"], report[1]["probe"]) == ("missed", "exact")


def test_verify_off_records_intent_and_says_that_is_what_it_is():
    """`verify="off"` is the one mode whose count is intent rather than
    measurement — the caller asked for no per-instance question. The record
    says so in `verify` instead of presenting it as a measurement."""
    from agentcad.toolkit import holes

    _out, records, _warning = holes.drill(_frame(), [(40, 0), (0, 0)], 10.0,
                                          verify="off")
    record = records[0]
    assert (record["verify"], record["count"]) == ("off", 2)
    assert record["dropped"] == []


# ------------------------------------------------- the blind-depth callouts


@pytest.mark.parametrize("family,size,expected", [
    ("clearance", "M8", "⌀9 ↧6"),
    ("counterbore", "M8", "⌀9 ↧6 ⌴⌀14.5↧8.8"),
    ("countersink", "M8", "⌀9 ↧6 ⌵⌀17.92×90°"),
])
def test_a_blind_seat_callout_states_the_hole_depth(family, size, expected):
    """**Regression.** `clearance`, `counterbore` and `countersink` recorded
    `depth_mm` and `thru: false` and then printed a designation with no depth
    in it at all — `⌀9` for an M8 blind to 6 mm, which a shop manufactures as a
    through hole. `drill` and `tapped` always did include it, so this was an
    inconsistent omission, not a convention.

    The counterbore is the one with two depths, and they are disambiguated by
    position: each `↧` qualifies the `⌀` group it follows.
    """
    from agentcad.toolkit import holes

    helper = getattr(holes, family)
    _out, records, _warning = helper(_plate(t=20.0), [(0, 0)], size, depth=6.0,
                                     thru=False)
    assert records[0]["designation"] == expected
    assert records[0]["depth_mm"] == 6.0 and records[0]["thru"] is False


@pytest.mark.parametrize("family,size,expected", [
    ("clearance", "M8", "⌀9"),
    ("counterbore", "M8", "⌀9 ⌴⌀14.5↧8.8"),
    ("countersink", "M8", "⌀9 ⌵⌀17.92×90°"),
])
def test_a_through_seat_callout_is_unchanged(family, size, expected):
    """A through hole is not "depth 0": the depth glyph stays absent, and these
    strings are byte-identical to what shipped before the blind fix."""
    from agentcad.toolkit import holes

    helper = getattr(holes, family)
    _out, records, _warning = helper(_plate(t=20.0), [(0, 0)], size)
    assert records[0]["designation"] == expected


def test_every_record_carries_a_depth_free_spelling_of_its_own_callout():
    """`designation_base` is what a reader prints once it has MEASURED that the
    geometry no longer supports the recorded depth. It is built by the same
    function from the same record, so the two spellings cannot drift."""
    from agentcad.toolkit import holes

    _out, records, _warning = holes.tapped(_plate(t=20.0), [(0, 0)], "M8",
                                           depth=6.0)
    assert records[0]["designation"] == "M8×1.25 - 6H ↧6"
    assert records[0]["designation_base"] == "M8×1.25 - 6H"


@pytest.mark.parametrize("family,kwargs", [
    ("drill", {"diameter": 9.0}),
    ("clearance", {"size": "M8"}),
    ("tapped", {"size": "M8", "depth": 6.0}),
    ("counterbore", {"size": "M8"}),
    ("countersink", {"size": "M8"}),
])
def test_every_helper_produces_a_record_the_shared_validator_accepts(
        family, kwargs):
    """One contract, three readers (the harvest raises, the drawing skips, the
    sidecar discards). If a helper could produce a record that contract rejects,
    the contract would be quietly unenforceable — so every helper is checked
    against it here, where the failure is cheap."""
    from agentcad.toolkit import hole_standards, holes

    helper = getattr(holes, family)
    argument = kwargs.pop("diameter", None) or kwargs.pop("size")
    _out, records, _warning = helper(_plate(t=20.0), [(0, 0)], argument,
                                     **kwargs)
    assert hole_standards.validate_record(records[0]) is None


def test_a_hole_in_the_gap_between_two_solids_is_a_miss():
    """The same defect in a second shape, and the one that proves the probe
    survives a Compound: two disjoint solids in one part, with a hole placed in
    the empty space between them. That space is inside the part's bounding box,
    so the box screen calls it `engaged`; the classifier is built from
    `part.wrapped` — a Compound here, not a Solid — and answers per point."""
    from build123d import Box, Pos

    from agentcad.toolkit import holes

    part = Box(40, 40, 10) + Pos(100, 0, 0) * Box(40, 40, 10)
    assert len(part.solids()) == 2
    _out, records, warning = holes.drill(part, [(0, 0), (100, 0), (50, 0)], 6.0)
    record = records[0]
    assert [row["status"] for row in record["instances"]] == [
        "engaged", "engaged", "missed"]
    assert record["count"] == 2
    assert record["positions"] == [[0.0, 0.0], [100.0, 0.0]]
    assert "instance(s) [2] do not reach the part" in warning


# ------------------ the CHEATSHEET is a guide agents receive, so it is tested


def test_the_cheatsheet_names_every_key_a_hole_record_actually_carries():
    """**Regression, and a standing guard.**

    `core/templates.CHEATSHEET` is what `part_template` hands an agent, and it
    is the one hole-authoring doc with no reader to notice when it goes stale:
    `docs/part-authoring.md` and `AGENTS.md` were updated for the measured
    `count`, `verify`/`dropped`, `designation_base` and the blind-depth
    callouts and the CHEATSHEET got none of them, so the guide an agent is
    handed described the behaviour of two rounds earlier.

    Asserting it against a REAL record is what makes it stay true: a key added
    to the record without a word in the sheet fails here.
    """
    import re

    from agentcad.core.templates import CHEATSHEET
    from agentcad.toolkit import holes

    _out, records, _warning = holes.counterbore(_plate(t=20.0), [(0, 0)], "M8",
                                                depth=6.0, thru=False)
    record = records[0]
    # THE RECORD-KEY BLOCK, not the whole sheet. `key not in CHEATSHEET` was a
    # substring search over ~400 lines, so 21 of these 24 keys survived being
    # deleted from the block (`d` alone occurs 821 times elsewhere) and only
    # `family`, `depth_mm` and `removed_mm3` were genuinely pinned. Plausible
    # future keys — `status`, `origin`, `depth`, `warnings`, `label`, `seat` —
    # all passed while entirely absent.
    block = re.search(r"ONE GROUP record -- \{(.*?)\}", CHEATSHEET, re.S)
    assert block is not None, "the CHEATSHEET's record-key block is gone"
    declared = {token.strip()
                for token in block.group(1).replace("\n", " ").split(",")}
    assert declared == set(record), (
        f"CHEATSHEET record keys disagree with a real record: "
        f"missing {sorted(set(record) - declared)}, "
        f"stale {sorted(declared - set(record))}")

    # …and the specific claims the last two rounds repealed.
    assert "instance 0 is skipped" not in CHEATSHEET
    assert "a bounding-box screen per instance" not in CHEATSHEET
    assert 'instances[i]["probe"]' in CHEATSHEET      # the tier that answered
    assert "clearance, blind" in CHEATSHEET           # the blind designations


# --------------- provenance reaches the thing that becomes a callout


def test_a_single_sourced_diameter_says_so_on_the_record():
    """**Regression.** `corroborated` reached nothing that manufactures.

    Grepping the tree for it returned exactly two hits — the tool description
    and the line that computes it — so the honest work of labelling the
    single-sourced ISO 10642 column was invisible at the one place the number
    turns into a shop instruction. A countersink's seat diameter comes off that
    column; the record now carries the label with it.
    """
    from agentcad.toolkit import hole_standards, holes

    _out, records, warning = holes.countersink(_plate(t=20.0), [(0, 0)], "M8")
    provenance = records[0]["provenance"]
    # The clearance row has two sources and the ISO 10642 seat has one, so the
    # union is three and the CONJUNCTION is false. That is the point: a hole is
    # only as corroborated as the least-corroborated row that shaped it.
    assert provenance["corroborated"] is False
    assert any("10642" in text for text in provenance["sources"])
    assert hole_standards.csk("M8")["corroborated"] is False
    # Single-sourced is carried, not shouted: it is a permanent property of
    # every metric countersink this repo can ship, and a warning nothing can
    # ever clear teaches readers to ignore warnings.
    assert warning is None


def test_a_counterbore_claims_what_backs_BOTH_of_its_rows():
    """A counterbore is two published rows — the clearance hole and the
    fastener head — so its record's provenance is their union and
    `corroborated` is their conjunction, never one row's answer for both."""
    from agentcad.toolkit import hole_standards, holes

    _out, records, _warning = holes.counterbore(_plate(t=30.0), [(0, 0)], "M8")
    provenance = records[0]["provenance"]
    clearance = hole_standards.clearance("M8")
    head = hole_standards.cbore("M8")
    assert set(provenance["sources"]) == set(clearance["sources"]) | set(
        head["sources"])
    assert provenance["corroborated"] is (clearance["corroborated"]
                                          and head["corroborated"])


def test_a_disputed_table_cell_warns_as_well_as_recording_itself():
    """The ANSI `#8 normal` cell is two sources that DISAGREED and an
    adjudication shipped in their place. That is a decision someone made, not a
    permanent property of the literature, so it interrupts — unlike a merely
    single-sourced row."""
    from agentcad.toolkit import holes

    _out, records, warning = holes.clearance(
        _plate(t=20.0), [(0, 0)], "#8", std="ansi", fit="normal")
    provenance = records[0]["provenance"]
    assert provenance["corroborated"] is False
    assert provenance["conflicts"] and "0.190" in provenance["conflicts"][0]
    assert "DISPUTED" in warning


def test_a_drilled_hole_carries_no_provenance_because_no_table_gave_it_one():
    """`drilled` takes millimetres from the designer. Claiming a source there
    would put a standard's name on a number the standard did not supply — and
    `validate_record` refuses a drilled record that carries one."""
    from agentcad.toolkit import hole_standards, holes

    _out, records, _warning = holes.drill(_plate(), [(0, 0)], 18.0)
    assert records[0]["provenance"] is None
    forged = {**records[0], "provenance": {"sources": ["ISO 273"],
                                           "corroborated": True,
                                           "conflicts": []}}
    assert "may not carry provenance" in hole_standards.validate_record(forged)


def test_provenance_is_re_derived_and_compared_not_merely_self_consistent():
    """**Regression.** `validate_record` re-derived the *designation* and
    compared, so a fabricated callout could not survive — but it checked
    provenance only for internal consistency. The genuine disputed ANSI
    `#8 normal` counterbore record, with its conflict note deleted and
    `corroborated` flipped to `true`, is perfectly self-consistent and
    **validated clean**, while re-deriving from its own `size`/`fit`/`standard`
    gives `corroborated: False` over one conflict.

    So provenance is now re-derived from the record's own fields and compared,
    exactly like the designation. Every laundering the verifier tried is below;
    each one used to return `None`.
    """
    from agentcad.toolkit import hole_standards, holes

    _out, records, _warning = holes.counterbore(
        _plate(t=30.0), [(0, 0)], "#8", std="ansi", fit="normal")
    genuine = records[0]
    assert hole_standards.validate_record(genuine) is None
    real = genuine["provenance"]
    assert (real["corroborated"], len(real["conflicts"])) == (False, 1)

    for name, provenance in (
        ("the conflict deleted and the flag flipped",
         {**real, "corroborated": True, "conflicts": []}),
        ("citations naming publications in no table",
         {**real, "sources": ["Bob's Big Book of Holes", "ibid."],
          "corroborated": True, "conflicts": []}),
        ("one citation listed twice",
         {**real, "sources": ["a chart", "a chart "], "corroborated": True,
          "conflicts": []}),
    ):
        problem = hole_standards.validate_record(
            {**genuine, "provenance": provenance})
        assert problem is not None and "entitle it to" in problem, name

    # A wrong, mistyped or absent `standard` is refused on the way in.
    for standard in ("ISO 9001", 7, None):
        problem = hole_standards.validate_record(
            {**genuine, "provenance": {**real, "standard": standard}})
        assert problem is not None, standard


def test_provenance_standard_is_always_a_list():
    """It was a bare string for a one-row answer and a list for a
    counterbore's two — an undocumented, untyped polymorphism a reader had to
    branch on, and one that hands a caller indexing `[0]` a character."""
    from agentcad.toolkit import holes

    for family, kwargs in (("clearance", {}), ("tapped", {}),
                           ("counterbore", {}), ("countersink", {})):
        _out, records, _warning = getattr(holes, family)(
            _plate(t=30.0), [(0, 0)], "M8", **kwargs)
        standard = records[0]["provenance"]["standard"]
        assert isinstance(standard, list) and all(
            isinstance(text, str) for text in standard), family


def test_size_and_fit_are_tied_to_the_diameter_they_select():
    """**Regression, and the one that reopened the flagship laundering.**

    `size` and `fit` select the published row every other check re-derives
    from, but they were not in `RECORD_KEYS`, were never typed, and were never
    compared with `d` — while `designation_for_record` spells the callout from
    `d`. So the number that gets manufactured and the label that chooses its
    provenance were never tied together, and mutating **both sides
    consistently** laundered the disputed ANSI `#8 normal` cell clean:
    `size: "#10"` with the `#10` provenance left `d` at 4.9784 and the callout
    at `⌀0.196` while the record claimed `corroborated: True` over 0 conflicts,
    and validated. Earlier attacks mutated one side only, which is why it
    passed four of them.

    The fix is one already-cached lookup, and it is why `size`/`fit` are now
    typed record keys: a key that steers validation and is itself unvalidated
    is the shape of this whole defect class.
    """
    from agentcad.toolkit import hole_standards, holes

    _out, records, _warning = holes.clearance(
        _plate(t=30.0), [(0, 0)], "#8", std="ansi", fit="normal")
    genuine = records[0]
    assert hole_standards.validate_record(genuine) is None
    assert genuine["designation"] == "⌀0.196"
    assert genuine["provenance"]["corroborated"] is False

    for label, patch in (("size #8 -> #10", {"size": "#10"}),
                         ("fit normal -> close", {"fit": "close"}),
                         ("fit normal -> loose", {"fit": "loose"})):
        forged = {**genuine, **patch}
        # both sides mutated consistently: the provenance the new row entitles
        forged["provenance"] = hole_standards.provenance_for_record(forged)
        assert forged["provenance"]["corroborated"] is True, label
        problem = hole_standards.validate_record(forged)
        assert problem is not None and "name one row" in problem, label

    assert {"size", "fit"} <= set(hole_standards.RECORD_KEYS)


@pytest.mark.parametrize("family,kwargs,size,fit", [
    ("drill", {"diameter": 9.0}, None, None),
    ("clearance", {"size": "M8"}, "M8", "medium"),
    ("tapped", {"size": "M8"}, "M8", None),
    ("counterbore", {"size": "M8"}, "M8", "medium"),
    ("countersink", {"size": "M8"}, "M8", "medium"),
])
def test_each_family_carries_exactly_the_names_its_row_needs(
        family, kwargs, size, fit):
    """`drilled` has no row so it may claim no size and no fit; a tapped hole
    has a size and no fit; the clearance-based families have both. Enforced,
    because these are the fields that select the row."""
    from agentcad.toolkit import hole_standards, holes

    helper = getattr(holes, family)
    argument = kwargs.pop("diameter", None) or kwargs.pop("size")
    _out, records, _warning = helper(_plate(t=30.0), [(0, 0)], argument,
                                     **kwargs)
    record = records[0]
    assert (record["size"], record["fit"]) == (size, fit)
    assert hole_standards.validate_record(record) is None
    for key in ("size", "fit"):
        flipped = {**record, key: None if record[key] else "M8"}
        assert hole_standards.validate_record(flipped) is not None, key
