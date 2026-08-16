"""PRD-010 slice 6 — drawing callouts from hole metadata (FR13, AC2, AC5).

Before this slice a hole on a drawing was a diameter and a count, derived from
the projected geometry: a ⌀4.2 tapped hole and a ⌀4.2 drilled hole were the
same thing, and neither one printed a callout at all unless the part carried a
PMI diameter dimension. With the records from slice 4/5 the drawing can say
what the hole *is* — `4× M5×0.8 - 6H ↧12` — and, just as importantly, say
when it is guessing.

The inherited limitation is asserted here rather than discovered later:
`_detect_circles` reads the **top view only**, so a hole on a side face has a
perfect record and no callout. That is PRD-014's job; what this slice owes is
a warning naming the record instead of a silent omission.
"""

import pytest

from agentcad.core.tools import build_registry

from .conftest import make_test_service

# One tapped M5 hole (count 1, below the detector's count >= 3 group
# threshold) plus a bolt circle of 8 clearance holes (above it).
PLATE = '''\
from build123d import Box
from agentcad.toolkit import holes, patterns

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(120, 120, p.t)
    part, _r, _w = holes.tapped(part, [(0, 0)], "M5", depth=12)
    part, _r, _w = holes.clearance(part, patterns.bolt_circle(45, 8), "M6")
    return part
'''

# Slice 8's families on one plate: a counterbore, a countersink, and the same
# pair called out in ASME symbology.
SEATS = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(140, 140, p.t)
    part, _r, _w = holes.counterbore(part, [(-40, 0)], "M8")
    part, _r, _w = holes.countersink(part, [(40, 0)], "M6")
    part, _r, _w = holes.counterbore(part, [(0, 45)], "1/4", std="ansi")
    return part
'''

# The same geometry, hand-cut: no toolkit call, so no records anywhere.
HANDCUT = '''\
from build123d import *

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    with BuildPart() as part:
        Box(120, 120, p.t)
        with PolarLocations(radius=45, count=8):
            Hole(radius=6.6 / 2)
    return part.part
'''

# A tapped hole on a SIDE face: a correct record the top view cannot show.
SIDE = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 40.0, "min": 20.0, "max": 60.0, "unit": "mm",
                "description": "block height"}}

def build(p):
    part = Box(80, 60, p.t)
    part, _r, _w = holes.tapped(part, [(0, 0)], "M6", depth=10, plane="front")
    return part
'''

# Four clearance holes drilled as ONE record, then two of them welded shut and
# the records carried across with `holes.carry` — whose own docstring names
# this exact hazard ("carrying records across a cut that removed one of the
# holes leaves a record for a hole that is no longer there"). The record still
# says `count: 4`; the top view has two circles.
REFILLED = '''\
from build123d import Box, Cylinder, Pos
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(120, 120, p.t)
    part, _r, _w = holes.clearance(
        part, [(-40, -40), (-40, 40), (40, -40), (40, 40)], "M5")
    for x, y in ((40, -40), (40, 40)):
        part = holes.carry(part + Pos(x, y, 0) * Cylinder(5.5 / 2, p.t), part)
    return part
'''

# Two clearance holes drilled as ONE record, then mirrored: four circles in
# the top view, and a record that only ever knew about two. The plate sits
# entirely in +X and butts against the mirror plane, so the union is one solid
# and `patterns.mirror` itself has nothing to warn about.
MIRRORED = '''\
from build123d import Box, Pos
from agentcad.toolkit import holes, patterns

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Pos(55, 0, 0) * Box(110, 120, p.t)
    part, _r, _w = holes.clearance(part, [(60, -40), (60, 40)], "M5")
    part, _w2 = patterns.mirror(part, "YZ")
    return part
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    return service, build_registry(service)


def _draw(demo, part_id, script, **kwargs):
    service, registry = demo
    service.create_part("demo", part_id, script=script)
    result = registry.call("generate_drawing",
                           {"project": "demo", "part_id": part_id, **kwargs})
    assert "error" not in result, result
    svg = (service.store.exports_dir("demo")
           / f"{part_id}_drawing.svg").read_text(encoding="utf-8")
    return result["detected"], svg


def _group(detected, diameter):
    return next(g for g in detected["hole_groups"]
                if g["diameter_mm"] == pytest.approx(diameter, abs=0.01))


# ------------------------------------------------------------------- AC2


@pytest.mark.integration
def test_ac2_a_tapped_hole_prints_its_iso_designation(demo):
    """**AC2** — the designation, not a diameter. A ⌀4.2 hole on the sheet
    could be a drilled hole or an M5 tap; the record is the only thing that
    knows, and the callout now says so."""
    detected, svg = _draw(demo, "plate", PLATE)

    assert "M5×0.8 - 6H ↧12" in svg
    tapped = _group(detected, 4.2)
    assert tapped["from_metadata"] is True
    assert tapped["designation"] == "M5×0.8 - 6H ↧12"
    assert tapped["family"] == "tapped"
    assert tapped["record_id"] == "h0"


@pytest.mark.integration
def test_a_hand_cut_hole_keeps_the_geometric_callout(demo):
    """No record, no claim: the group keeps the measured diameter and says
    `from_metadata: false`, which is the flag that tells a reader whether the
    text came from intent or from a projected circle."""
    detected, svg = _draw(demo, "handcut", HANDCUT)

    group = _group(detected, 6.6)
    assert group["from_metadata"] is False
    assert "designation" not in group
    assert group["count"] == 8
    assert "8× ⌀6.60" in svg                 # measured, and it looks measured
    assert "6H" not in svg


@pytest.mark.integration
def test_a_counterbore_and_a_countersink_print_their_seat(demo):
    """Slice 8's families reach the sheet. The projected circle is only ever
    the *seat* diameter — a top view cannot see how deep a pocket is, or that
    there is a smaller hole under it — so this is exactly the text the
    geometric detector can never produce."""
    detected, svg = _draw(demo, "seats", SEATS)

    assert "⌀9 ⌴⌀14.5↧8.8" in svg               # M8 clearance + ISO 4762 head
    assert "⌀6.6 ⌵⌀13.44×90°" in svg            # M6 clearance + a 90 deg seat
    assert "⌀0.281 ⌴⌀0.4375↧0.2812" in svg      # the same idea in inches

    # A record matches on its `d`, which is the BORE — the through hole the
    # fastener passes through — not the seat. Both circles are projected and
    # they are concentric, so the leader lands in the same place either way;
    # what matters is that the group carrying the callout is the one whose
    # diameter the record actually states.
    cbore = _group(detected, 9.0)
    assert (cbore["from_metadata"], cbore["family"]) == (True, "counterbore")
    assert 14.5 in detected["diameters_mm"]   # the seat is on the sheet, and
    assert not [g for g in detected["hole_groups"]      # carries no callout of
                if g["diameter_mm"] == pytest.approx(14.5, abs=0.01)]  # its own
    csk = _group(detected, 6.6)
    assert (csk["from_metadata"], csk["family"]) == (True, "countersink")
    assert detected["hole_warnings"] == []


# ------------------------------------------------------------------- AC5


@pytest.mark.integration
def test_ac5_a_pattern_renders_one_callout_for_the_whole_group(demo):
    """**AC5** — one call with N points is one record with `count: N`, so a
    bolt circle of 8 is `8× ⌀6.6`, not eight callouts."""
    detected, svg = _draw(demo, "plate", PLATE)

    assert "8× ⌀6.6" in svg
    group = _group(detected, 6.6)
    assert group["from_metadata"] is True
    assert group["count"] == 8
    assert group["designation"] == "⌀6.6"
    assert svg.count("⌀6.6") == 1            # one callout, not eight


@pytest.mark.integration
def test_a_record_below_the_detector_threshold_is_still_drawn(demo):
    """`_detect_circles` only emits a group at `count >= 3`, so the single
    tapped hole has no geometric group at all. Rendering it from the record is
    explicit here, not an accident of the threshold."""
    detected, svg = _draw(demo, "plate", PLATE)

    tapped = _group(detected, 4.2)
    assert tapped["count"] == 1
    assert tapped["from_metadata"] is True
    assert "1×" not in svg                   # a lone hole is not "1× M5"
    assert "M5×0.8 - 6H ↧12" in svg


# ------------------------------------------- the record that cannot be drawn


@pytest.mark.integration
def test_a_record_the_top_view_cannot_show_warns_instead_of_vanishing(demo):
    """The inherited limitation, stated. A hole on a side face has a perfect
    record and no callout — PRD-014's job — and a record that cannot be drawn
    must be named, not dropped."""
    detected, svg = _draw(demo, "side", SIDE)

    assert detected["hole_groups"] == []
    assert len(detected["hole_warnings"]) == 1
    warning = detected["hole_warnings"][0]
    assert "h0" in warning and "M6×1 - 6H ↧10" in warning
    assert "top view" in warning
    assert "6H" not in svg


@pytest.mark.integration
def test_without_the_top_view_every_record_is_named_as_undrawable(demo):
    detected, _svg = _draw(demo, "plate", PLATE, views=["front", "right"])

    assert "hole_groups" not in detected
    assert len(detected["hole_warnings"]) == 2
    assert all("top view" in w for w in detected["hole_warnings"])


@pytest.mark.integration
def test_a_part_with_no_records_reports_no_hole_warnings(demo):
    detected, _svg = _draw(demo, "handcut", HANDCUT)
    assert detected["hole_warnings"] == []


# --------------------------------------------------------------- robustness


@pytest.mark.integration
def test_a_residue_record_is_reported_not_raised(demo):
    """A record is a plain dict and the drawing pack is a *consumer*: the
    handler that validates record shape is `hole_records` (slice 5). Here a
    malformed one must not take the drawing down with it."""
    residue = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 10.0, "min": 5.0, "max": 20.0, "unit": "mm"}}

def build(p):
    part = Box(60, 60, p.t)
    setattr(part, holes.ATTR, [{"id": "junk"}, "not even a dict"])
    return part
'''
    detected, _svg = _draw(demo, "residue", residue)
    assert len(detected["hole_warnings"]) == 2
    assert any("junk" in w for w in detected["hole_warnings"])


@pytest.mark.integration
def test_a_pmi_toleranced_callout_wins_the_slot_and_is_not_doubled(demo):
    """PMI is *authored* tolerance intent and hole records are *derived*
    geometry facts (design Decision 10) — they stay separate, and where both
    describe the same circles the toleranced callout is the one that belongs on
    the sheet. The record still marks the group, so a reader can tell where the
    designation came from."""
    service, registry = demo
    service.create_part("demo", "plate", script=PLATE)
    registry.call("set_part_pmi", {
        "project": "demo", "part_id": "plate",
        "pmi": {"dims": [{"id": "d1", "kind": "diameter", "target": 6.6,
                          "plus": 0.1, "minus": 0.05}]}})
    result = registry.call("generate_drawing",
                           {"project": "demo", "part_id": "plate"})
    svg = (service.store.exports_dir("demo")
           / "plate_drawing.svg").read_text(encoding="utf-8")

    assert "8x ⌀6.60 +0.10/-0.05" in svg      # the PMI callout, drawn once
    assert "8× ⌀6.6<" not in svg              # and not repeated from metadata
    group = _group(result["detected"], 6.6)
    assert group["from_metadata"] is True
    assert group["designation"] == "⌀6.6"


@pytest.mark.integration
def test_the_dxf_path_is_unaffected(demo):
    """DXF carries no annotation layer at all (v1), so the records change
    nothing there — and must not break it."""
    service, registry = demo
    service.create_part("demo", "plate", script=PLATE)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "plate", "format": "dxf"})
    assert "error" not in result, result
    assert result["detected"].get("hole_groups") is None


# ------------------------- R3: the callout counts what it is actually over

@pytest.mark.integration
def test_a_callout_counts_the_circles_it_is_drawn_over_not_the_intent(demo):
    """**Regression.** The callout printed `record["count"]` — what the drill
    call *asked for* — while the leader was drawn over whatever circles
    actually matched. `_match_record` computes exactly that set (its `hit`)
    and used to throw it away.

    Here four holes are drilled as one record and two are welded shut. The
    record still says four; the top view has two circles. A sheet that prints
    `4× ⌀5.5` over two circles is a drawing that lies to a machinist, and it
    did so silently.
    """
    detected, svg = _draw(demo, "refilled", REFILLED)

    group = _group(detected, 5.5)
    assert group["from_metadata"] is True
    assert "2× ⌀5.5" in svg
    assert "4× ⌀5.5" not in svg
    # and the divergence is named, not swallowed
    warning = next(w for w in detected["hole_warnings"] if "h0" in w)
    assert "4" in warning and "2" in warning


@pytest.mark.integration
def test_a_callout_over_more_circles_than_the_record_claims_warns(demo):
    """The other direction. Two holes are drilled as one record and the plate
    is then mirrored, so the top view has four ⌀5.5 circles. The detected
    group counts four and the record claims two — the sheet printed
    `2× ⌀5.5` beside a `count: 4` group **in the same result**, with nothing
    saying which to believe.

    The callout still reads `2×`, and deliberately so: the two unmatched
    circles are not swept into it, because a second feature that merely shared
    a diameter would then be mislabelled — the mistake `_match_record` demands
    centre agreement to avoid. What changes is that the shortfall is now
    *stated* instead of leaving the reader to spot it.
    """
    detected, svg = _draw(demo, "mirrored", MIRRORED)

    group = _group(detected, 5.5)
    assert group["count"] == 4, detected["hole_groups"]
    assert "2× ⌀5.5" in svg
    warning = next(w for w in detected["hole_warnings"] if "h0" in w)
    assert "2 of the 4" in warning
    assert "no record claims them" in warning


@pytest.mark.integration
def test_a_record_that_matches_exactly_still_warns_about_nothing(demo):
    """The guard must not cry wolf: the bolt circle's record says 8 and the
    top view has 8, so nothing is reported and the callout is unchanged."""
    detected, svg = _draw(demo, "plate", PLATE)

    assert "8× ⌀6.6" in svg
    assert detected["hole_warnings"] == []
