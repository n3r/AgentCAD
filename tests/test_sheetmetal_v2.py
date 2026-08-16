"""Sheet-metal v2: partial flanges, bend relief, outline-from-unfold, hems and
corner treatments.

The v1 corpus (``tests/test_sheetmetal.py``) is the gate for everything here:
a full-edge flange must still produce v1's exact geometry. These tests cover
what v1 could not express.

Numbers quoted in the assertions come from the two spikes recorded in
changelogs 0157 (S9) and 0158 (S8).
"""

import math

import pytest

from agentcad.core.tools import build_registry

from .conftest import make_test_service

# BA for the canonical test bend: 90 deg, R=3, t=2, K=0.44
BA_90 = (math.pi / 2) * (3 + 0.44 * 2)


def _sp(**kw):
    from agentcad.toolkit.sheetmetal import SheetPart

    return SheetPart(kw.pop("thickness", 2.0), **kw)


def _ac4():
    """The AC4 bracket: 60 x 40 x 2 plate, ONE partial-width flange spanning
    x in [-15, +15] of the 60 mm front edge, R=3, leaf 30 -> a relief at both
    ends."""
    return (_sp().base(60, 40)
            .flange("front", 90, 30, inner_radius=3, start=15, width=30))


def _shoelace(pts):
    area = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        area += x0 * y1 - x1 * y0
    return area / 2


def _valid_single(part):
    return bool(part.is_valid) and len(part.solids()) == 1


# ---- partial flanges ---------------------------------------------------------

def test_partial_flange_folds_to_one_valid_solid():
    sp = _ac4()
    part = sp.fold()
    assert _valid_single(part)
    # base + sector + leaf over the 30 mm span, minus two rect reliefs.
    # relief = 1.5*t wide x (R + t) deep x t through = 3 * 5 * 2 = 30 mm^3 each
    expected = (60 * 40 * 2
                + (math.pi / 2) * 2 * (3 + 1) * 30
                + 30 * 2 * 30
                - 2 * 30.0)
    assert part.volume == pytest.approx(expected, rel=1e-9)


def test_partial_flange_unfold_matches_fold_within_the_k_factor_gap():
    """Spike S9(b): the flat model puts the neutral fibre at k*t and the solid
    model at t/2, so fold and unfold disagree by exactly
    angle_rad * (0.5 - k) * t^2 * span -- 11.309734 mm^3 here -- and by
    nothing else. That gap IS the bend-allowance model's own tolerance."""
    sp = _ac4()
    fold, flat = sp.fold(), sp.unfold()
    assert _valid_single(fold) and _valid_single(flat)
    gap = math.radians(90) * (0.5 - 0.44) * 2.0 ** 2 * 30
    assert gap == pytest.approx(11.309734, abs=1e-6)
    assert fold.volume - flat.volume == pytest.approx(gap, abs=1e-6)


def test_partial_flange_bend_line_spans_the_flange_only():
    lines = _ac4().bend_lines()
    assert len(lines) == 1
    bl = lines[0]
    mid_y = -20 - BA_90 / 2
    assert bl["a"] == pytest.approx((-15.0, mid_y), abs=1e-9)
    assert bl["b"] == pytest.approx((15.0, mid_y), abs=1e-9)


def test_two_non_overlapping_flanges_on_one_edge():
    sp = (_sp().base(60, 40)
          .flange("front", 90, 20, inner_radius=3, start=4, width=18)
          .flange("front", 90, 20, inner_radius=3, start=38, width=18))
    part = sp.fold()
    assert _valid_single(part)
    assert len(sp.bend_lines()) == 2
    assert _valid_single(sp.unfold())


def test_overlapping_flanges_on_one_edge_raise():
    sp = _sp().base(60, 40).flange("front", 90, 20, start=4, width=20)
    with pytest.raises(ValueError, match="overlap"):
        sp.flange("front", 45, 10, start=20, width=10)


def test_flange_span_must_fit_the_edge():
    sp = _sp().base(60, 40)
    with pytest.raises(ValueError, match="start"):
        sp.flange("front", 90, 10, start=-1, width=10)
    with pytest.raises(ValueError, match="width"):
        sp.flange("front", 90, 10, start=55, width=10)
    with pytest.raises(ValueError, match="width"):
        sp.flange("front", 90, 10, start=0, width=0)


def test_full_edge_flange_is_still_the_v1_geometry():
    """width=None keeps v1's meaning to the last bit."""
    from agentcad.toolkit.sheetmetal import SheetPart

    v1 = SheetPart(2.0).base(60, 40).flange("front", 90, 30, inner_radius=3)
    explicit = (SheetPart(2.0).base(60, 40)
                .flange("front", 90, 30, inner_radius=3, start=0, width=60))
    assert v1.fold().volume == pytest.approx(explicit.fold().volume, rel=1e-12)
    assert v1.flat_outline() == explicit.flat_outline()


# ---- relief ------------------------------------------------------------------

@pytest.mark.parametrize("kind,removed", [("rect", 60.0), ("round", 56.1372),
                                          ("tear", 0.0)])
def test_relief_kinds_remove_the_measured_volume_from_both(kind, removed):
    """Spike S9(d): the SAME cut is applied to fold() and unfold(), so both
    lose the same material."""
    from agentcad.toolkit.sheetmetal import SheetPart

    def make(relief):
        return (SheetPart(2.0).base(60, 40)
                .flange("front", 90, 30, inner_radius=3, start=15, width=30,
                        relief=relief))

    with_r, without = make(kind), make({"kind": "tear"})
    assert (without.fold().volume - with_r.fold().volume
            == pytest.approx(removed, abs=1e-3))
    assert (without.unfold().volume - with_r.unfold().volume
            == pytest.approx(removed, abs=1e-3))
    assert _valid_single(with_r.fold())


def test_tear_relief_warns_that_the_model_shows_none():
    sp = (_sp().base(60, 40)
          .flange("front", 90, 30, start=15, width=30, relief="tear"))
    assert any("tear" in w and "no material" in w for w in sp.warnings), sp.warnings


def test_relief_only_where_material_remains():
    """A flange flush to a plate corner gets ONE relief, not two."""
    from agentcad.toolkit.sheetmetal import SheetPart

    flush = (SheetPart(2.0).base(60, 40)
             .flange("front", 90, 30, inner_radius=3, start=0, width=30))
    bare = (SheetPart(2.0).base(60, 40)
            .flange("front", 90, 30, inner_radius=3, start=0, width=30,
                    relief="tear"))
    assert bare.fold().volume - flush.fold().volume == pytest.approx(30.0, abs=1e-3)


def test_explicit_relief_dimensions_override_the_shop_rule():
    from agentcad.toolkit.sheetmetal import SheetPart

    sp = (SheetPart(2.0).base(60, 40)
          .flange("front", 90, 30, inner_radius=3, start=15, width=30,
                  relief={"kind": "rect", "width": 4.0, "depth": 6.0}))
    bare = (SheetPart(2.0).base(60, 40)
            .flange("front", 90, 30, inner_radius=3, start=15, width=30,
                    relief="tear"))
    assert (bare.fold().volume - sp.fold().volume
            == pytest.approx(2 * 4.0 * 6.0 * 2.0, abs=1e-3))


def test_unknown_relief_kind_raises():
    sp = _sp().base(60, 40)
    with pytest.raises(ValueError, match="relief"):
        sp.flange("front", 90, 10, start=5, width=10, relief="chamfer")


# ---- outline derived from the unfold ----------------------------------------

def test_outline_is_closed_ccw_and_equals_the_top_face_area():
    sp = _ac4()
    pts = sp.flat_outline()
    assert pts[0] != pts[-1]                       # closure is implicit
    assert len(pts) >= 4
    area = _shoelace(pts)
    assert area > 0                                # CCW
    flat = sp.unfold()
    from build123d import Axis, Plane

    face = flat.faces().filter_by(Plane.XY).sort_by(Axis.Z)[-1]
    assert area == pytest.approx(face.area, rel=1e-9)
    # and independently of which face was picked: the blank is a prism, so its
    # outline area x thickness IS its volume
    assert area * 2.0 == pytest.approx(flat.volume, rel=1e-9)


def test_outline_of_a_round_relief_is_a_polyline_within_the_chord_tolerance():
    from agentcad.toolkit.sheetmetal import SheetPart
    from build123d import Axis, Plane

    sp = (SheetPart(2.0).base(60, 40)
          .flange("front", 90, 30, inner_radius=3, start=15, width=30,
                  relief="round"))
    pts = sp.flat_outline(tolerance=0.01)
    face = sp.unfold().faces().filter_by(Plane.XY).sort_by(Axis.Z)[-1]
    # arcs bulge into removed material, so the chord polygon over-states the
    # blank; the error is bounded by the chord tolerance, not by luck
    assert _shoelace(pts) == pytest.approx(face.area, rel=2e-4)
    edges = sp.flat_outline_edges()
    assert sum(1 for e in edges if e["kind"] == "arc") == 2
    arc = next(e for e in edges if e["kind"] == "arc")
    assert arc["radius"] == pytest.approx(1.5, rel=1e-9)   # 1.5*t / 2


def test_the_outline_and_its_edge_list_start_at_the_same_place():
    """One start decision, not two. The polygon used to pick the nearest of
    ALL its points and the edge list the nearest edge START, so an arc whose
    middle is nearer the base corner than any vertex split them: measured on
    this bracket, ``flat_outline()`` began at (1.634902, -6.809857) — a sample
    34.2746 mm from the corner, in the middle of the relief arc — while
    ``flat_outline_edges()`` began 36.0947 mm away at (-30, -56.094690). The
    two describe the same loop from different places, and a reader that walks
    them together (a DXF writer, a bend-line overlay) is off by an edge."""
    sp = (_sp().base(60, 40)
          .flange("front", 90, 30, inner_radius=3)
          .flange("right", 90, 20, inner_radius=3, start=0, width=20,
                  relief={"kind": "round", "width": 30, "depth": 30}))
    pts, edges = sp.flat_outline(), sp.flat_outline_edges()
    assert pts[0] == pytest.approx(edges[0]["a"], abs=1e-9)
    # and every edge start is still a point of the polygon, in order
    assert [e["a"] for e in edges] == [p for p in pts if p in
                                       {e["a"] for e in edges}]


def test_outline_edges_are_exact_and_join_end_to_end():
    edges = _ac4().flat_outline_edges()
    assert edges and all(e["kind"] in ("line", "arc") for e in edges)
    for a, b in zip(edges, edges[1:] + edges[:1]):
        assert a["b"] == pytest.approx(b["a"], abs=1e-6)


# ---- hems --------------------------------------------------------------------

def test_open_hem_folds_and_unfolds_consistently():
    sp = _sp(thickness=1.5).base(60, 40).hem("front", kind="open", length=6)
    fold, flat = sp.fold(), sp.unfold()
    assert _valid_single(fold) and _valid_single(flat)
    t, R = 1.5, 1.5
    expected = (60 * 40 * t + math.pi * t * (R + t / 2) * 60 + 6 * t * 60)
    assert fold.volume == pytest.approx(expected, rel=1e-9)
    gap = math.pi * (0.5 - 0.44) * t * t * 60
    assert fold.volume - flat.volume == pytest.approx(gap, abs=1e-6)
    bl = sp.bend_lines()[0]
    assert bl["angle_deg"] == 180.0
    assert bl["inner_radius"] == pytest.approx(1.5)


def test_closed_hem_is_one_valid_solid_at_the_shipped_radius():
    """Spike S8: OCCT holds down to R/t = 1e-6; the shipped radius is a shop
    number (0.5*t -> an air gap of t), not an OCCT limit."""
    from agentcad.toolkit.sheetmetal import CLOSED_HEM_RADIUS_FACTOR

    sp = _sp(thickness=1.5).base(60, 40).hem("front", kind="closed", length=6)
    part = sp.fold()
    assert _valid_single(part)
    R = CLOSED_HEM_RADIUS_FACTOR * 1.5
    assert sp.bend_lines()[0]["inner_radius"] == pytest.approx(R)
    expected = 60 * 40 * 1.5 + math.pi * 1.5 * (R + 0.75) * 60 + 6 * 1.5 * 60
    assert part.volume == pytest.approx(expected, rel=1e-9)
    # the hem's air gap is 2R and it is still a gap: the folded leaf sits on
    # top of the sheet at t + 2R, so the part is t + 2R + t tall
    assert part.bounding_box().max.Z == pytest.approx(1.5 + 2 * R + 1.5, abs=1e-6)


def test_a_closed_hem_at_a_vanishing_radius_stops_being_a_hem():
    """Spike S8's floor, and the reason a zero-radius hem is refused: at
    R = 0 the fold still returns one valid solid of exactly the right volume,
    but the seam between the folded leaf and the sheet is gone."""
    sp = _sp(thickness=1.5).base(60, 40)
    with pytest.raises(ValueError, match="inner_radius"):
        sp.hem("front", kind="closed", length=6, inner_radius=0.0)


def test_teardrop_hem_is_refused_with_the_measured_reason():
    sp = _sp(thickness=1.5).base(60, 40)
    with pytest.raises(ValueError) as exc:
        sp.hem("front", kind="teardrop", length=6)
    msg = str(exc.value)
    assert "teardrop" in msg
    assert "180" in msg              # names the wrap that breaks the model
    assert "2.41" in msg             # the measured longest non-penetrating leaf


def test_partial_hem_and_relief():
    sp = (_sp(thickness=1.5).base(60, 40)
          .hem("front", kind="open", length=6, start=15, width=30))
    assert _valid_single(sp.fold())
    assert _valid_single(sp.unfold())
    bl = sp.bend_lines()[0]
    assert bl["a"][0] == pytest.approx(-15.0)
    assert bl["b"][0] == pytest.approx(15.0)


def test_hem_and_flange_do_not_collide_on_different_edges():
    sp = (_sp().base(60, 40)
          .flange("front", 90, 20, inner_radius=3)
          .hem("back", kind="open", length=6))
    assert _valid_single(sp.fold())
    assert len(sp.bend_lines()) == 2


# ---- corner treatments -------------------------------------------------------

def _cornered(treatment):
    sp = (_sp().base(60, 40)
          .flange("front", 90, 20, inner_radius=3)
          .flange("right", 90, 20, inner_radius=3))
    if treatment is not None:
        sp.corner("front", "right", treatment)
    return sp


def test_corner_treatments_order_by_volume():
    close = _cornered("close").fold()
    rip = _cornered("rip").fold()
    gap = _cornered("gap").fold()
    for part in (close, rip, gap):
        assert _valid_single(part)
    assert close.volume > rip.volume > gap.volume
    # rip is v1's untreated corner, to the last bit
    assert rip.volume == pytest.approx(_cornered(None).fold().volume, rel=1e-12)


def test_close_corner_mitre_fills_the_notch():
    """The mitre adds material on BOTH flanges: each extends past the corner
    by its own (R + t) and is cut by the 45 deg bisector."""
    close, rip = _cornered("close").fold(), _cornered("rip").fold()
    added = close.volume - rip.volume
    # two mitre wedges, each (R + t) long x t thick x (R + t + leaf) tall,
    # halved by the bisector -> order of magnitude, not a golden number
    assert 100.0 < added < 1000.0
    # the notch itself: a probe sitting in the open corner of the rip version.
    # The front leaf ends at x = 30 and the right leaf starts at x = 33, so
    # (31, -24, 11) is air in `rip` and mitred material in `close`.
    from build123d import Box, Pos

    probe = Pos(31.0, -24.0, 11.0) * Box(1.0, 0.5, 2.0)
    assert (close & probe).volume == pytest.approx(1.0, rel=1e-6)
    # a disjoint `&` is None here, not an empty Compound — either way, no
    # material (see the patterns module on why the probe is worth paying for)
    empty = rip & probe
    assert empty is None or empty.volume == pytest.approx(0.0, abs=1e-9)


def test_close_corner_appears_in_the_flat_pattern_too():
    """The mitre is cut from the BLANK as well as from the fold, so the blank
    can be bent into the model.

    Measured while the mitre was cut from ``fold()`` only: both tabs ran the
    full ``rho = R + k*t`` past the corner and both claimed the ``rho x rho``
    square there, so the fuse swallowed 30.1088 mm^3 of declared material in
    silence — one valid solid of 10393.8187 mm^3 where the two tabs declare
    10423.9275. The blank was un-foldable and every total-volume assertion
    passed anyway, because the 45 degree bisector cuts the square in two and
    the two halves tile it exactly: the swallowed overlap and the missing
    mitre are the same 30.1088 mm^3. Only the closed form below, which pays
    for the mitre wedge the CHORD of the unrolled bisector removes, tells
    them apart."""
    close, rip = _cornered("close"), _cornered("rip")
    flat = close.unfold()
    assert _valid_single(flat)
    assert flat.volume > rip.unfold().volume
    # each tab is (BA + leaf) long and its span runs rho past the corner; the
    # mitre chord (slope sin(a)/a) then takes rho^2 * a / (2 sin a) back off
    # each mitred end -- see SheetPart._mitre_cuts for why it is the chord
    rho = 3 + 0.44 * 2
    ext = BA_90 + 20
    wedge = rho ** 2 * math.radians(90) / (2 * math.sin(math.radians(90)))
    expected = 2.0 * (60 * 40 + ext * (60 + rho) + ext * (40 + rho) - 2 * wedge)
    assert expected == pytest.approx(10376.6327, abs=1e-3)
    assert flat.volume == pytest.approx(expected, rel=1e-9)
    # nothing is claimed twice: the two tabs meet on the mitre, they do not
    # overlap on it (this is the assertion the square-cornered blank failed)
    assert not any("swallow" in w for w in close.warnings), close.warnings
    a_close = _shoelace(close.flat_outline())
    a_rip = _shoelace(rip.flat_outline())
    assert a_close > a_rip
    # the mitred corner does not leave the blank in two coplanar top faces:
    # outline area x thickness is the whole blank's volume
    assert a_close * 2.0 == pytest.approx(close.unfold().volume, rel=1e-9)


def test_the_flat_blank_carries_the_mitre_notch_not_a_square_corner():
    """The shape, not just the number: the blank's corner is a V-notch whose
    apex is the plate corner itself, because that is where the mitre plane
    meets the bend line. Measured with the mitre missing from ``unfold()``,
    the outline ran straight through the square corner at (33.88, -23.88)."""
    close = _cornered("close")
    pts = [(round(x, 6), round(y, 6)) for x, y in close.flat_outline()]
    rho = 3 + 0.44 * 2
    # the apex: both mitres start at the plate corner
    assert (30.0, -20.0) in pts, pts
    # and the square corner both tabs used to claim is gone
    assert (round(30 + rho, 6), round(-20 - rho, 6)) not in pts, pts
    # the two chord ends: the front tab reaches rho across at BA out, the
    # right tab rho out at BA across
    assert (round(30 + rho, 6), round(-20 - BA_90, 6)) in pts, pts
    assert (round(30 + BA_90, 6), round(-20 - rho, 6)) in pts, pts


def test_the_close_corner_bend_line_stays_inside_the_blank():
    """A bend line is drawn on the blank, so it stops where the blank does.
    The tab runs rho past the corner but the mitre crosses the bend MIDLINE
    half a bend allowance out, where the chord has only reached
    rho*sin(a)/2 — 1.94 mm here, not 3.88."""
    rho = 3 + 0.44 * 2
    lines = {b["edge"]: b for b in _cornered("close").bend_lines()}
    assert lines["front"]["b"][0] == pytest.approx(30 + rho / 2, abs=1e-9)
    assert lines["right"]["a"][1] == pytest.approx(-20 - rho / 2, abs=1e-9)
    # and the untreated corner is untouched
    rip = {b["edge"]: b for b in _cornered("rip").bend_lines()}
    assert rip["front"]["b"][0] == pytest.approx(30.0, abs=1e-9)


def test_gap_corner_shortens_both_bend_lines():
    gap = _cornered("gap")
    rip = _cornered("rip")
    lg = {b["edge"]: b for b in gap.bend_lines()}
    lr = {b["edge"]: b for b in rip.bend_lines()}
    def _len(b):
        return math.dist(b["a"], b["b"])
    assert _len(lg["front"]) == pytest.approx(_len(lr["front"]) - 2.0)
    assert _len(lg["right"]) == pytest.approx(_len(lr["right"]) - 2.0)


def test_corner_validation():
    sp = (_sp().base(60, 40)
          .flange("front", 90, 20)
          .flange("right", 90, 20))
    with pytest.raises(ValueError, match="adjacent"):
        sp.corner("front", "back", "close")
    with pytest.raises(ValueError, match="treatment"):
        sp.corner("front", "right", "weld")
    sp.corner("front", "right", "close")
    with pytest.raises(ValueError, match="duplicate"):
        sp.corner("right", "front", "gap")


def test_corner_needs_flanges_that_reach_it():
    sp = (_sp().base(60, 40)
          .flange("front", 90, 20, start=0, width=30)
          .flange("right", 90, 20))
    with pytest.raises(ValueError, match="reach"):
        sp.corner("front", "right", "close")


# ---- material conservation ---------------------------------------------------

def test_a_hem_leaf_that_lands_on_a_flange_warns_about_what_it_swallowed():
    """A 50 mm open hem on a 40 mm deep plate folds back over the sheet and
    straight through the back flange's leaf. OCCT reports nothing: one solid,
    ``is_valid`` True, and a volume that is simply 240.0 mm^3 short of the
    declaration (15496.4600 declared, 15256.4600 measured) -- 60 x 2 x 2 mm of
    hem leaf inside the back flange. ``_checked`` cannot see this, because
    every property it looks at is fine."""
    sp = (_sp().base(60, 40)
          .hem("front", "open", length=50)
          .flange("back", 90, 25))
    part = sp.fold()
    assert _valid_single(part), "the failure this warns about is a SILENT one"
    assert part.volume == pytest.approx(15256.460033, abs=1e-3)
    swallowed = [w for w in sp.warnings if "swallow" in w]
    assert swallowed, sp.warnings
    assert "240" in swallowed[0], swallowed[0]


@pytest.mark.parametrize("make", [
    _ac4,
    lambda: _cornered("close"),
    lambda: _cornered("gap"),
    lambda: _sp(thickness=1.5).base(60, 40).hem("front", "open", length=6),
    lambda: (_sp().base(60, 40).flange("front", 90, 20, inner_radius=3)
             .hem("back", "open", length=6)),
])
def test_features_that_do_not_overlap_lose_no_material(make):
    """The conservation check must be quiet on every shape this module is for:
    the mitre extension, the gap corner, the reliefs and a hem all land where
    the declaration says, to OCCT's own precision."""
    sp = make()
    sp.fold()
    sp.unfold()
    assert not any("swallow" in w for w in sp.warnings), sp.warnings


def test_two_hems_that_meet_in_the_middle_report_the_volume_they_swallowed():
    """Two 30 mm hems folded back onto a 40 mm deep plate overlap by 20 mm of
    leaf, over the full 60 mm width and the full 1 mm thickness: 1200 mm^3
    exactly. The fold still reports ONE VALID SOLID and raises nothing --
    declared 6565.486678, measured 5365.486678 -- so conservation is the only
    thing that sees it. (An independent review's probe; the numbers here are
    that probe's, re-measured.)"""
    sp = (_sp(thickness=1.0).base(60, 40)
          .hem("front", length=30)
          .hem("back", length=30))
    part = sp.fold()
    assert _valid_single(part), "the failure this warns about is a SILENT one"
    assert part.volume == pytest.approx(5365.486678, abs=1e-5)
    swallowed = [w for w in sp.warnings if "swallow" in w]
    assert swallowed, sp.warnings
    assert "6565.4867" in swallowed[0] and "5365.4867" in swallowed[0]
    assert "1200" in swallowed[0], swallowed[0]


# ---- close-corner seams ------------------------------------------------------

def _corner_seam_warnings(sp):
    return [w for w in sp.warnings if "'close'" in w]


@pytest.mark.parametrize("front,right,thickness", [
    ((90.0, 20.0, 3.0), (90.0, 20.0, 3.0), 2.0),      # the canonical corner
    ((90.0, 20.0, 3.0), (90.0, 10.0, 3.0), 2.0),      # a shorter leaf is fine
    ((90.0, 20.0, 3.0), (90.0, 40.0, 3.0), 2.0),      # ...and a longer one
    ((120.0, 8.0, 4.5), (120.0, 8.0, 4.5), 3.0),      # obtuse, matched
    ((179.0, 6.0, 2.0), (179.0, 6.0, 2.0), 2.0),
    # ACUTE and matched is fine too, as long as the leaf fits the extension:
    # L <= (R + t) * tan(45 - a/2). These all measure a whole seam (1.0).
    ((60.0, 0.2, 3.0), (60.0, 0.2, 3.0), 2.0),        # L_max 1.3397
    ((45.0, 0.5, 3.0), (45.0, 0.5, 3.0), 2.0),        # L_max 2.0711
    ((30.0, 1.0, 5.0), (30.0, 1.0, 5.0), 2.0),        # L_max 4.0415
    ((10.0, 1.0, 5.0), (10.0, 1.0, 5.0), 2.0),        # L_max 5.8737
    ((20.0, 2.0, 4.0), (20.0, 2.0, 4.0), 3.0),
])
def test_a_close_corner_whose_profiles_agree_is_quiet(front, right, thickness):
    """The screen must not fire on the shapes this feature is for. Measured,
    every one of these shares its full ``sqrt(2)*min(profile)`` mitre face to
    within 8e-15 relative, so the check costs them nothing but the arithmetic.
    """
    sp = _sp(thickness=thickness).base(60, 40)
    sp.flange("front", front[0], front[1], inner_radius=front[2])
    sp.flange("right", right[0], right[1], inner_radius=right[2])
    sp.corner("front", "right", "close")
    sp.fold()
    sp.unfold()
    assert not _corner_seam_warnings(sp), sp.warnings


@pytest.mark.parametrize("front,right,thickness,fraction,cause", [
    # profiles that disagree: one plane cuts both leaves, so each leaf's cut
    # face is its own cross-section and the two only coincide where the
    # profiles do.
    ((90.0, 20.0, 3.0), (45.0, 10.0, 1.0), 2.0, 0.2674, "bend angles differ"),
    ((90.0, 20.0, 3.0), (90.0, 20.0, 1.0), 2.0, 0.1296, "inner radii differ"),
    ((90.0, 20.0, 3.0), (85.0, 20.0, 3.0), 2.0, 0.6700, "bend angles differ"),
    # matched and acute, but the LEAF IS TOO LONG for the extension:
    # `_effective_span` runs it `R + t` past the corner, which is the outward
    # reach of a 90 degree profile and no other. Not the angle on its own —
    # the quiet cases above are acute too.
    ((45.0, 12.0, 1.0), (45.0, 12.0, 1.0), 1.0, 0.1902, "never reaches"),
    ((30.0, 25.0, 0.8), (30.0, 25.0, 0.8), 0.8, 0.0696, "never reaches"),
    ((45.0, 12.0, 1.0), (45.0, 12.0, 1.0), 2.0, 0.2810, "longest leaf"),
    # the same overrun with more room: L_max scales with (R + t), so a bigger
    # radius seams more of the same 12 mm leaf (1.2426 -> 2.0711 mm of limit,
    # 0.2810 -> 0.4103 of seam). The leaf length is not the whole story either;
    # what matters is how far past the limit it is.
    ((45.0, 12.0, 3.0), (45.0, 12.0, 3.0), 2.0, 0.4103, "longest leaf"),
])
def test_a_close_corner_that_cannot_seam_says_so_with_the_measurement(
        front, right, thickness, fraction, cause):
    """Nothing else in the module can see this: both leaves are cut by the SAME
    plane so no material moves (``_conserved`` is silent by construction) and
    they still fuse through the base plate into one valid solid (``_checked``
    is happy). Only the shared face area is short."""
    sp = _sp(thickness=thickness).base(60, 40)
    sp.flange("front", front[0], front[1], inner_radius=front[2])
    sp.flange("right", right[0], right[1], inner_radius=right[2])
    sp.corner("front", "right", "close")
    part = sp.fold()
    assert _valid_single(part), "the failure this warns about is a SILENT one"
    assert not any("swallow" in w for w in sp.warnings), sp.warnings
    found = _corner_seam_warnings(sp)
    assert found, sp.warnings
    assert cause in found[0], found[0]
    # the warning carries what was measured, not just that something is wrong
    got = float(found[0].split("share ")[1].split(" mm^2")[0])
    promised = float(found[0].split("bisector over ")[1].split(" mm^2")[0])
    assert got / promised == pytest.approx(fraction, abs=5e-5)
    # and the remedy names the fault that actually fired, not a boilerplate
    # pair of them: telling an author to match angles they already matched is
    # advice that cannot be followed.
    remedy = found[0].split("To close it, ")[1]
    profiles_differ = "differ" in found[0]
    assert ("same bend angle and inner radius" in remedy) is profiles_differ
    assert ("shorten the" in remedy) is ("never reaches" in found[0])


@pytest.mark.parametrize("thickness,angle,radius", [
    (2.0, 45.0, 3.0), (2.0, 30.0, 5.0), (2.0, 60.0, 3.0),
    (2.0, 10.0, 5.0), (1.0, 75.0, 2.0), (3.0, 20.0, 4.0),
])
def test_the_quoted_leaf_limit_is_the_reach_predicate_itself(
        thickness, angle, radius):
    """The warning quotes `L_max = (R + t) * tan(45 - a/2)` as the longest leaf
    that still mitres. Bisect the *actual* predicate (`_profile_reach` against
    `_mitre_extension`) and check the closed form is it — an author acting on
    that number must land on the right side of the check."""
    def fits(leaf):
        sp = _sp(thickness=thickness).base(60, 40)
        sp.flange("front", angle, leaf, inner_radius=radius)
        flange = sp._flanges[0]
        return sp._profile_reach(flange) <= sp._mitre_extension(flange) + 1e-9

    lo, hi = 1e-6, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if fits(mid) else (lo, mid)

    sp = _sp(thickness=thickness).base(60, 40)
    sp.flange("front", angle, lo, inner_radius=radius)
    assert sp._max_mitre_leaf(sp._flanges[0]) == pytest.approx(lo, abs=1e-8)


@pytest.mark.parametrize("angle", [90.0, 90.0001, 120.0, 179.0])
@pytest.mark.parametrize("leaf", [
    0.5, 500.0, 1.0e4,
    1.63312e7,     # where the float leak used to cross _TOL at 90 degrees
    1.0e8,         # where it used to print "the longest leaf ... is inf mm"
    1.0e9,
])
def test_from_90_degrees_up_every_leaf_fits_the_mitre(angle, leaf):
    """A leaf at or past 90 degrees adds no outward reach, so the shipped
    `R + t` extension is exact there for **any** leaf length — which is why
    `L_max` is infinite rather than the 0 that `tan(45 - a/2)` gives.

    Pinned across six orders of leaf length because the previous version of
    this test pinned L=500 while claiming "any leaf whatsoever", and the gap
    hid a real defect: `cos(radians(90.0))` is 6.123e-17, not 0, so the reach
    accumulated `L * 6.12e-17` and crossed `_TOL` at L = 1.63312e7 mm. A
    correct 90 degree corner then warned, and the warning quoted `inf` as the
    longest leaf that still mitres. `_profile_reach` now drops the leaf term
    from 90 degrees up, where `cos a <= 0` makes it exactly redundant.
    """
    sp = _sp().base(60, 40)
    sp.flange("front", angle, leaf, inner_radius=3.0)
    flange = sp._flanges[0]
    from agentcad.toolkit.sheetmetal import _TOL

    assert sp._max_mitre_leaf(flange) == math.inf
    assert sp._profile_reach(flange) <= sp._mitre_extension(flange) + _TOL


def test_a_90_degree_corner_never_quotes_an_infinite_leaf_limit():
    """The end-to-end statement: at the leaf lengths that used to trip it, a
    matched 90 degree `close` corner is silent.

    The samples below are a check on an argument, not the argument itself —
    "never" is not something three leaf lengths can establish. From 90 degrees
    up `_profile_reach` returns `max(0, (R + t) * sin a)`, which does not
    mention the leaf at all and is bounded by `R + t = _mitre_extension` for
    every angle, since `sin a <= 1`. So the reach fault cannot fire there for
    ANY leaf, and `_max_mitre_leaf` is `inf` only from 90 degrees up: the two
    can never meet. What the samples defend is that the code still says what
    the argument says — the previous version of it was true of the geometry
    and false of the floating point.
    """
    for leaf in (1.63312e7, 1.0e8, 1.0e9):
        sp = _sp().base(60, 40)
        sp.flange("front", 90.0, leaf, inner_radius=3.0)
        sp.flange("right", 90.0, leaf, inner_radius=3.0)
        sp.corner("front", "right", "close")
        sp._corner_seams(None)             # the screen, without the boolean
        assert not [w for w in sp.warnings if "'close'" in w], (leaf, sp.warnings)


def test_the_close_corner_seam_check_is_free_when_it_passes():
    """The boolean probe is paid only where the arithmetic screen has already
    fired, so a correct corner never buys one."""
    sp = _cornered("close")
    calls = []
    original = type(sp)._measured_seam
    type(sp)._measured_seam = lambda self, *a: (calls.append(a), None)[1]
    try:
        sp.fold()
    finally:
        type(sp)._measured_seam = original
    assert calls == []


# ---- the k-factor gap accumulates --------------------------------------------

@pytest.mark.parametrize("edges,bend_line", [
    (("front",), 60.0),
    (("front", "back"), 120.0),
    (("front", "back", "left"), 160.0),
])
def test_the_fold_unfold_gap_is_per_bend_and_sums_over_them(edges, bend_line):
    """``fold() - unfold() = angle_rad*(0.5 - k)*t^2*span`` is a statement about
    ONE bend, and the model's total is the sum. Measured: 22.619467105842887
    for one 60 mm bend, 45.238934211700325 for two (exactly twice), and
    60.318578948928916 for 160 mm of bend line. AGENTS.md and the module
    docstring used to say the gap does not grow with the number of features;
    it grows linearly with the length of bend line, which is what this pins."""
    sp = _sp(thickness=2.0).base(60, 40)
    for edge in edges:
        sp.flange(edge, 90.0, 20.0, inner_radius=3.0)
    gap = sp.fold().volume - sp.unfold().volume
    predicted = math.radians(90.0) * (0.5 - 0.44) * 2.0 ** 2 * bend_line
    assert gap == pytest.approx(predicted, abs=1e-9)
    assert not sp.warnings, sp.warnings


# ---- warnings ----------------------------------------------------------------

def test_warnings_is_the_sink_and_the_fold_stays_single():
    sp = _cornered("close")
    part = sp.fold()
    assert _valid_single(part)
    assert not any("solid" in w for w in sp.warnings), sp.warnings


# ---- AC4: the partial-flange bracket, end to end -----------------------------

AC4_BRACKET = '''\
from agentcad.toolkit.sheetmetal import SheetPart

PARAMS = {
    "width":      {"default": 60.0, "min": 10.0, "max": 500.0, "unit": "mm",
                   "description": "base plate width (X)"},
    "depth":      {"default": 40.0, "min": 10.0, "max": 500.0, "unit": "mm",
                   "description": "base plate depth (Y)"},
    "thick":      {"default": 2.0,  "min": 0.5,  "max": 6.0,   "unit": "mm",
                   "description": "sheet thickness"},
    "flange_len": {"default": 30.0, "min": 5.0,  "max": 200.0, "unit": "mm",
                   "description": "flange leaf length beyond the bend"},
    "bend_r":     {"default": 3.0,  "min": 0.5,  "max": 20.0,  "unit": "mm",
                   "description": "inner bend radius"},
    "tab_start":  {"default": 15.0, "min": 0.0,  "max": 400.0, "unit": "mm",
                   "description": "flange start along the front edge"},
    "tab_width":  {"default": 30.0, "min": 2.0,  "max": 400.0, "unit": "mm",
                   "description": "flange width along the front edge"},
}

def _sheet(p):
    return (SheetPart(p.thick)
            .base(p.width, p.depth)
            .flange("front", 90, p.flange_len, inner_radius=p.bend_r,
                    start=p.tab_start, width=p.tab_width, relief="round"))

def build(p):
    return _sheet(p).fold()

def flat_pattern(p):
    sp = _sheet(p)
    return sp.unfold(), sp.bend_lines()
'''


@pytest.fixture
def ac4_service(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "tab_bracket", script=AC4_BRACKET)
    return service


def test_ac4_partial_flange_bracket_exports_a_flat_pattern_with_reliefs(ac4_service):
    """AC4: partial-width flange -> automatic relief, valid fold, round-tripped
    unfold, correct bend lines, and an SVG that shows the relief cuts."""
    registry = build_registry(ac4_service)
    result = registry.call("flat_pattern", {
        "project": "demo", "part_id": "tab_bracket", "format": "svg"})
    assert "error" not in result, result
    assert result["n_bend_lines"] == 1
    # the blank is the plate plus one 30 mm tab, so it is still 60 wide and
    # (40 + BA + 30) deep
    assert result["flat_bbox_mm"]["w"] == pytest.approx(60, abs=0.1)
    assert result["flat_bbox_mm"]["h"] == pytest.approx(40 + BA_90 + 30, abs=0.1)
    svg = (ac4_service.store.exports_dir("demo") / "tab_bracket_flat.svg"
           ).read_text(encoding="utf-8")
    assert svg.startswith("<svg") and 'id="BEND"' in svg
    # the two round reliefs are arcs in the projected outline: HLR emits them
    # as path arcs, so the outline is no longer four straight lines
    assert svg.count("<path") >= 2
    # and the bend line is the 30 mm tab, not the whole 60 mm edge
    assert "90&#176; R3" in svg


def test_ac4_build_result_is_a_valid_single_solid(ac4_service):
    registry = build_registry(ac4_service)
    result = registry.call("get_part", {"project": "demo",
                                        "part_id": "tab_bracket"})
    assert "error" not in result, result
    # spike S9's `fold_round` on exactly these parameters: 6920.854 mm^3
    assert result["metrics"]["volume_mm3"] == pytest.approx(6920.854, abs=1e-3)
