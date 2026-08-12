"""Slots — compiled at ingestion, addressable, never blamed (PRD-009 slice 6).

A `slot {name, c1, c2, width}` expands **before any residual is built** into
two arcs, two lines and their auto-constraints. Two of the design's
"structural, not a row" choices do the heavy lifting:

- **one shared radius parameter** for both caps, so equal-radius cannot appear
  in a conflict report because it is not a constraint;
- **structural junctions** — each side line is built directly on the caps'
  virtual handles — so the four coincidences are not rows either.

What is left is what a slot actually asserts: `radius = width/2` and four
line-arc tangencies. Five rows against the five parameters the slot owns.

The tangency form matters and is measured here: with `dist(centre, line) - r`,
sliding a junction along its cap moves *both* endpoints of the side along the
line, so the residual is second-order flat and the Jacobian is rank-deficient
**at the solution** — a slot reported `rank 1` of 5 and `dof 4` with its own
name in `free_entities`. Because the junction is structurally on both the arc
and the line, the honest residual is the perpendicularity of the radius, which
is first-order exact: `rank 5`, `dof 0`, `max_residual 1.0e-15`.
"""

import math

import pytest

from agentcad.core import tools_sketch
from agentcad.core.model import ValidationError
from agentcad.core.tools import ToolRegistry
from agentcad.toolkit.sketch import Sketch, SketchError, solve_sketch

from .test_sketch_jacobian import (assert_df_matches_central_difference,
                                   assert_df_stays_inside_params)

# Non-round on purpose: a slot on tidy numbers hides nothing and proves less.
C1 = (3.17, -2.43)
C2 = (41.93, 7.61)
WIDTH = 11.34


def _slot_sketch(fixed: bool = True) -> Sketch:
    sk = Sketch()
    sk.point("c1", *C1, fixed=fixed)
    sk.point("c2", *C2, fixed=fixed)
    sk.slot("s1", "c1", "c2", WIDTH)
    return sk


DERIV_BUILDERS = {"slots": lambda: _slot_sketch(fixed=False)}


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_every_df_matches_a_central_difference_of_its_own_f(name):
    assert_df_matches_central_difference(name, DERIV_BUILDERS[name]())


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_df_writes_only_inside_its_declared_params(name):
    assert_df_stays_inside_params(name, DERIV_BUILDERS[name]())


def _spec(**over) -> dict:
    spec = {
        "points": [{"name": "c1", "x": C1[0], "y": C1[1], "fixed": True},
                   {"name": "c2", "x": C2[0], "y": C2[1], "fixed": True}],
        "slots": [{"name": "s1", "c1": "c1", "c2": "c2", "width": WIDTH}],
        "constraints": [],
    }
    spec.update(over)
    return spec


# --------------------------------------------------------------------------
# what a slot compiles to
# --------------------------------------------------------------------------
def test_a_slot_compiles_to_two_arcs_and_two_lines():
    sk = _slot_sketch()
    assert sorted(sk.arcs) == ["s1.arc_a", "s1.arc_b"]
    assert sorted(sk.lines) == ["s1.side_1", "s1.side_2"]
    # each side runs between the caps' virtual handles: the four junctions are
    # structural, so they are not residual rows and cannot conflict
    assert (sk.lines["s1.side_1"].p1, sk.lines["s1.side_1"].p2) == (
        "s1.arc_a.end", "s1.arc_b.start")
    assert (sk.lines["s1.side_2"].p1, sk.lines["s1.side_2"].p2) == (
        "s1.arc_b.end", "s1.arc_a.start")


def test_the_two_caps_share_one_radius_parameter():
    """Equal-radius is structural, so it can never appear in a conflict."""
    sk = _slot_sketch()
    assert sk.n_par == 5                 # r + two angles per cap
    assert sk._rads["s1.arc_a"] is sk._rads["s1.arc_b"]
    assert "equal_radius" not in sk.con_types
    assert sk.slot_owner == ["s1"] * 5   # reported as the slot, not the caps


def test_a_slot_contributes_five_rows_and_they_are_all_its_own():
    sk = _slot_sketch()
    assert sk.n_res == 5
    assert [r.kind for r in sk.residuals] == (
        ["radius"] + ["tangent_point_perp"] * 4)
    assert {r.con_index for r in sk.residuals} == {0}
    assert sk.con_types == ["slot"]
    assert all(r.origin == "slot:s1" for r in sk.residuals)
    # and the slot has no caller-visible index: there is no entry of
    # `constraints` to point at, because the caller wrote an *entity*
    assert sk.con_report == [None]


def test_a_slot_solves_exactly_with_zero_dof_when_its_centres_are_fixed():
    res = solve_sketch(_spec())
    assert res["ok"] is True
    assert res["n_params"] == 5 and res["n_residuals"] == 5
    assert res["rank"] == 5 and res["dof"] == 0
    assert res["diagnostics"]["status"] == "well_constrained"
    assert res["max_residual"] < 1e-12


def test_free_centres_leave_exactly_the_four_dof_a_hand_count_gives():
    """Position 2, orientation 1, length 1 — the width is pinned, nothing else."""
    spec = _spec(points=[{"name": "c1", "x": C1[0], "y": C1[1]},
                         {"name": "c2", "x": C2[0], "y": C2[1]}])
    res = solve_sketch(spec)
    assert res["ok"] is True
    assert res["n_params"] == 9          # 4 centre + 5 slot
    assert res["dof"] == 4
    assert set(res["diagnostics"]["free_entities"]) == {"c1", "c2", "s1"}


def test_the_four_junctions_are_tangent_to_1e_9():
    res = solve_sketch(_spec())
    slot = res["slots"]["s1"]
    r = slot["r"]
    assert r == pytest.approx(WIDTH / 2, abs=1e-9)
    a, b = res["arcs"]["s1.arc_a"], res["arcs"]["s1.arc_b"]
    assert a["r"] == pytest.approx(r, abs=1e-12)
    assert b["r"] == pytest.approx(r, abs=1e-12)

    for side, ends in (("side_1", (("s1.arc_a", "end"), ("s1.arc_b", "start"))),
                       ("side_2", (("s1.arc_b", "end"), ("s1.arc_a", "start")))):
        (n0, e0), (n1, e1) = ends
        p0 = res["arcs"][n0][e0]
        p1 = res["arcs"][n1][e1]
        ux, uy = p1["x"] - p0["x"], p1["y"] - p0["y"]
        length = math.hypot(ux, uy)
        ux, uy = ux / length, uy / length
        for name in ("s1.arc_a", "s1.arc_b"):
            arc = res["arcs"][name]
            # perpendicular distance from the cap's centre to the side == r
            wx, wy = arc["cx"] - p0["x"], arc["cy"] - p0["y"]
            assert abs(wx * uy - wy * ux) == pytest.approx(r, abs=1e-9), (
                f"{side} is not tangent to {name}")
        assert side  # the loop variable documents which side failed


def test_the_sides_are_parallel_to_the_centre_line_and_a_width_apart():
    res = solve_sketch(_spec())
    a, b = res["arcs"]["s1.arc_a"], res["arcs"]["s1.arc_b"]
    axis = math.atan2(C2[1] - C1[1], C2[0] - C1[0])
    side1 = math.atan2(b["start"]["y"] - a["end"]["y"],
                       b["start"]["x"] - a["end"]["x"])
    assert math.sin(side1 - axis) == pytest.approx(0.0, abs=1e-9)
    gap = math.hypot(a["end"]["x"] - a["start"]["x"],
                     a["end"]["y"] - a["start"]["y"])
    assert gap == pytest.approx(WIDTH, abs=1e-9)


# --------------------------------------------------------------------------
# naming and blame
# --------------------------------------------------------------------------
def test_a_user_entity_may_not_collide_with_a_compiled_sub_entity():
    sk = Sketch()
    sk.point("c1", *C1)
    sk.point("c2", *C2)
    sk.slot("s1", "c1", "c2", WIDTH)
    with pytest.raises(SketchError, match="reserved"):
        sk.point("s1.arc_a", 0.0, 0.0)
    with pytest.raises(SketchError, match="duplicate"):
        sk.slot("s1", "c1", "c2", WIDTH)


def test_compiled_sub_entities_are_addressable_from_a_constraint():
    """Reserved for *declaration*, referenceable for constraints — that is
    what makes `details.origin` naming `s1.arc_a` useful."""
    res = solve_sketch(_spec(constraints=[
        {"type": "radius", "c": "s1.arc_a", "r": WIDTH / 2}]))
    assert res["ok"] is True                     # consistent, so it still solves
    assert res["diagnostics"]["status"] == "over_constrained"
    # and the redundancy is the caller's row, not the slot's
    assert res["diagnostics"]["redundant"] == [
        {"index": 0, "type": "radius", "origin": None}]


def test_a_conflict_with_slot_machinery_blames_the_constraint_the_user_wrote():
    """A slot compiles **first**, so a conflicting user constraint is always
    the later row and always the one named. That is the design's rule — a
    diagnostic never blames a constraint the user did not write — and it means
    the slot-origin branch of the report is unreachable from the outside; the
    structural assertions above are what pin it.
    """
    registry = ToolRegistry()
    tools_sketch.register(registry, None)
    spec = _spec(constraints=[{"type": "radius", "c": "s1.arc_b", "r": 3.0}])
    with pytest.raises(ValidationError) as excinfo:
        registry.get("solve_sketch").handler(
            entities={"points": spec["points"], "slots": spec["slots"]},
            constraints=spec["constraints"])
    conflicting = excinfo.value.details["diagnostics"]["conflicting"]
    assert [c["index"] for c in conflicting] == [0]      # the user's radius
    assert [c["type"] for c in conflicting] == ["radius"]
    assert [c["origin"] for c in conflicting] == [None]


def test_a_slot_shifts_no_user_constraint_index():
    """The slot's rows are declared first but its caller-visible index is
    None, so `constraints[i]` still means what a diagnostic says it means."""
    res = solve_sketch(_spec(constraints=[
        {"type": "distance", "p": "c1", "q": "c2", "d": 40.0},
        {"type": "distance", "p": "c1", "q": "c2", "d": 40.0},
    ], points=[{"name": "c1", "x": C1[0], "y": C1[1], "fixed": True},
               {"name": "c2", "x": C2[0], "y": C2[1]}]))
    redundant = res["diagnostics"]["redundant"]
    assert [c["index"] for c in redundant] == [1]        # the duplicate
    assert [c["origin"] for c in redundant] == [None]


def test_a_slot_needs_a_positive_width_and_two_distinct_centres():
    sk = Sketch()
    sk.point("c1", *C1)
    sk.point("c2", *C2)
    sk.point("same", *C1)
    with pytest.raises(SketchError, match="positive width"):
        sk.slot("bad", "c1", "c2", 0.0)
    with pytest.raises(SketchError, match="distinct centres"):
        sk.slot("bad2", "c1", "same", WIDTH)


# --------------------------------------------------------------------------
# warm start
# --------------------------------------------------------------------------
def test_initial_seeds_a_slot_by_its_radius_alone():
    """A client never has to send a compiled sub-entity's parameters: the
    caps' angles are re-derived from the seeded centres."""
    spec = _spec(points=[{"name": "c1", "x": C1[0], "y": C1[1], "fixed": True},
                         {"name": "c2", "x": C2[0], "y": C2[1]}],
                 constraints=[{"type": "distance", "p": "c1", "q": "c2",
                               "d": 40.0},
                              {"type": "distance_y", "p": "c1", "q": "c2",
                               "d": 10.04}])
    res = solve_sketch({**spec, "initial": {
        "points": {"c2": {"x": 43.0, "y": 7.5}},
        "slots": {"s1": {"r": WIDTH / 2}}}})
    assert res["ok"] is True
    assert res["warm_started"] is True
    assert res["warnings"] == []
    assert res["slots"]["s1"]["r"] == pytest.approx(WIDTH / 2, abs=1e-9)


def test_an_initial_that_forgets_the_slot_degrades_to_a_cold_start():
    res = solve_sketch({**_spec(), "initial": {"points": {}}})
    assert res["ok"] is True
    assert res["warm_started"] is False
    assert res["warnings"][0]["code"] == "initial_incomplete"
    assert res["warnings"][0]["entities"] == ["s1"]


def test_initial_naming_an_unknown_slot_raises():
    with pytest.raises(SketchError, match="nope"):
        solve_sketch({**_spec(), "initial": {"slots": {"nope": {"r": 1.0}}}})


# --------------------------------------------------------------------------
# the payload
# --------------------------------------------------------------------------
def test_the_result_reports_the_slot_and_its_compiled_primitives():
    """Slice 7 emits a slot inside a closed profile as its compiled
    primitives — `SlotCenterToCenter` is a BuildSketch face at the origin, not
    a BuildLine curve — so both views are on the payload."""
    res = solve_sketch(_spec())
    slot = res["slots"]["s1"]
    assert slot["c1"] == "c1" and slot["c2"] == "c2"
    assert slot["width"] == WIDTH
    assert slot["arcs"] == ["s1.arc_a", "s1.arc_b"]
    assert slot["sides"] == ["s1.side_1", "s1.side_2"]
    assert slot["center1"]["x"] == pytest.approx(C1[0], abs=1e-12)
    assert slot["center2"]["y"] == pytest.approx(C2[1], abs=1e-12)
    for name in slot["arcs"]:
        assert name in res["arcs"]


def test_the_tool_accepts_slots_and_splines():
    registry = ToolRegistry()
    tools_sketch.register(registry, None)
    spec = _spec()
    res = registry.get("solve_sketch").handler(
        entities={"points": spec["points"], "slots": spec["slots"]},
        constraints=[])
    assert res["ok"] is True
    assert res["slots"]["s1"]["r"] == pytest.approx(WIDTH / 2, abs=1e-9)
