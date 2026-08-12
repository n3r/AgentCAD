"""DOF, rank, free entities and the redundant/conflicting split (PRD-009 AC3).

The four-case rectangle table below is the design spec's measured evidence,
turned into tests. Its point is that **the textbook method is wrong here**:
column-pivoted QR of `J.T` blames an innocent *original* `vertical` constraint
on two of the three over-constrained rectangles, because pivoting selects by
column norm — an artifact of residual scaling, not of intent. Declaration-order
greedy forward selection is correct on all four, because it blames the *later*
constraint, which is the one the user just added.

`test_pivoted_qr_blames_an_innocent_constraint_where_greedy_does_not` pins that
difference on purpose: a future "simplification" back to pivoted QR must fail
loudly with an explanation rather than quietly start pointing at the wrong line.
"""

import statistics
import time

import numpy as np
import pytest
from scipy.linalg import qr

from agentcad.core import tools_sketch
from agentcad.core.model import ValidationError
from agentcad.core.tools import ToolRegistry
from agentcad.toolkit.sketch import parse_sketch, solve_sketch

from .test_sketch_bench import staircase

# The rectangle of design Decision 6: `a` pinned at the origin, four H/V
# constraints and two dimensions — exactly constrained at 6 params / 6 rows.
# Any seventh constraint is therefore dependent, and the question the whole
# slice answers is *which* constraint gets named.
BASE_CONSTRAINTS = [
    {"type": "horizontal", "ln": "ab"},      # 0
    {"type": "vertical", "ln": "bc"},        # 1
    {"type": "horizontal", "ln": "cd"},      # 2
    {"type": "vertical", "ln": "da"},        # 3
    {"type": "distance", "p": "a", "q": "b", "d": 50},   # 4
    {"type": "distance", "p": "b", "q": "c", "d": 30},   # 5
]
ADDED = len(BASE_CONSTRAINTS)  # the index every case below must name


def rectangle(*extra: dict) -> dict:
    """The 50 x 30 rectangle, plus whatever constraint the case adds."""
    return {
        "points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
                   {"name": "b", "x": 50, "y": 1},
                   {"name": "c", "x": 51, "y": 30},
                   {"name": "d", "x": 1, "y": 29}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"},
                  {"name": "cd", "p1": "c", "p2": "d"},
                  {"name": "da", "p1": "d", "p2": "a"}],
        "circles": [],
        "constraints": BASE_CONSTRAINTS + list(extra),
    }


# case -> (added constraint, the set it must land in, its declared type)
TABLE = {
    "redundant_parallel": (
        {"type": "parallel", "l1": "ab", "l2": "cd"}, "redundant", "parallel"),
    "duplicate_distance": (
        {"type": "distance", "p": "d", "q": "c", "d": 50}, "redundant", "distance"),
    "contradictory_distance": (
        {"type": "distance", "p": "d", "q": "c", "d": 60}, "conflicting", "distance"),
    "duplicate_horizontal": (
        {"type": "horizontal", "ln": "cd"}, "redundant", "horizontal"),
}


def jacobian_at_solution(spec: dict):
    """`(sketch, J)` with the Jacobian evaluated at the solved coordinates.

    Re-parses the spec seeded at its own solution, which is the public way to
    get the matrix the diagnostics ran on.
    """
    result = solve_sketch(spec)
    seeded = {**spec, "points": [dict(p) for p in spec["points"]]}
    for p in seeded["points"]:
        solved = result["points"][p["name"]]
        p["x"], p["y"] = solved["x"], solved["y"]
    sk = parse_sketch(seeded)
    return sk, sk.make_functions()[1](sk.initial_vector())


# ---------------- AC3: the four-case table ----------------
@pytest.mark.parametrize("case", sorted(TABLE))
def test_the_dependent_set_names_the_constraint_that_was_added(case):
    """AC3. Each case adds one constraint to an exactly-constrained rectangle;
    the diagnostic must name *that* constraint, by declaration index and by
    the type the caller wrote."""
    added, bucket, ctype = TABLE[case]
    diag = solve_sketch(rectangle(added))["diagnostics"]

    assert diag["status"] == "over_constrained"
    assert diag["analysis_complete"] is True
    assert diag[bucket] == [{"index": ADDED, "type": ctype, "origin": None}], diag
    other = "conflicting" if bucket == "redundant" else "redundant"
    assert diag[other] == [], diag


@pytest.mark.parametrize("case", sorted(TABLE))
def test_an_over_constrained_sketch_never_reports_a_negative_dof(case):
    """The shipped `n_params - n_residuals` reported -1 for every row of this
    table; `n_params - rank(J)` reports the truth, which is 0."""
    added, _, _ = TABLE[case]
    result = solve_sketch(rectangle(added))
    assert result["n_params"] == 6 and result["n_residuals"] == 7
    assert result["rank"] == 6
    assert result["dof"] == 0
    assert result["diagnostics"]["dof"] == 0


def test_pivoted_qr_blames_an_innocent_constraint_where_greedy_does_not():
    """The regression that keeps the measured-correct algorithm in place.

    Measured on this machine (and reproduced by this test): on the duplicate
    and contradictory `distance` cases, column-pivoted QR of `J.T` names
    constraint **3, `vertical(da)`** — an original the user drew first and
    considers structural — while declaration-order greedy names constraint 6,
    the one just added. If someone "simplifies" `Sketch.dependent_rows` back to
    pivoted QR, this test fails and says why.
    """
    fooled = []
    for case in ("duplicate_distance", "contradictory_distance"):
        added, _, _ = TABLE[case]
        sk, J = jacobian_at_solution(rectangle(added))
        rank = sk.rank(J)
        owners = sk.row_owners()

        _, _, piv = qr(J.T, mode="economic", pivoting=True)
        qr_blames = sorted({owners[i].con_index for i in piv[rank:]})
        greedy_blames = sorted({owners[i].con_index
                                for i in sk.dependent_rows(J)[0]})

        assert greedy_blames == [ADDED], (case, greedy_blames)
        # The load-bearing claim: QR points at a constraint that was already
        # there. (Measured index: 3, `vertical`. The index itself is LAPACK's
        # pivot order, so only the "not the added one" half is asserted.)
        assert qr_blames and all(i < ADDED for i in qr_blames), (
            f"{case}: pivoted QR blamed {qr_blames}, which no longer "
            "reproduces the design's measurement — re-run the comparison "
            "before trusting either method")
        fooled.append((case, qr_blames, [sk.con_types[i] for i in qr_blames]))

    assert len(fooled) == 2, fooled


def test_greedy_keeps_exactly_rank_many_rows():
    """The greedy pass and the SVD must agree on how much rank there is, or
    one of the two tolerances is wrong."""
    for case in sorted(TABLE):
        added, _, _ = TABLE[case]
        sk, J = jacobian_at_solution(rectangle(added))
        dependent, complete = sk.dependent_rows(J)
        assert complete
        assert J.shape[0] - len(dependent) == sk.rank(J), case


# ---------------- AC4 (payload half): under-constrained ----------------
def test_under_constrained_sketch_names_its_free_entities():
    """AC4's payload half: a bare `dof: 2` is not actionable; "2 DOF, free:
    c, d" is. The rectangle keeps its base pinned but loses both dimensions
    and two of its four H/V constraints."""
    spec = {
        "points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
                   {"name": "b", "x": 50, "y": 0, "fixed": True},
                   {"name": "c", "x": 51, "y": 30},
                   {"name": "d", "x": 1, "y": 29}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"},
                  {"name": "cd", "p1": "c", "p2": "d"},
                  {"name": "da", "p1": "d", "p2": "a"}],
        "circles": [],
        "constraints": [{"type": "vertical", "ln": "bc"},
                        {"type": "horizontal", "ln": "cd"}],
    }
    diag = solve_sketch(spec)["diagnostics"]
    assert diag["status"] == "under_constrained"
    assert diag["dof"] == 2 and diag["rank"] == 2
    assert diag["free_entities"] == ["c", "d"]
    assert diag["redundant"] == [] and diag["conflicting"] == []


def test_a_free_radius_shows_up_as_a_free_entity():
    """Null-space reporting maps radius slots back to their circle, not to the
    centre point that happens to be next to them in the vector."""
    spec = {
        "points": [{"name": "o", "x": 0, "y": 0, "fixed": True}],
        "lines": [],
        "circles": [{"name": "C", "center": "o", "r": 8}],
        "constraints": [],
    }
    diag = solve_sketch(spec)["diagnostics"]
    assert diag["dof"] == 1
    assert diag["free_entities"] == ["C"]
    assert diag["status"] == "under_constrained"


def test_a_well_constrained_sketch_says_so():
    diag = solve_sketch(rectangle())["diagnostics"]
    assert diag["status"] == "well_constrained"
    assert diag["dof"] == 0 and diag["rank"] == 6
    assert diag["free_entities"] == []
    assert diag["redundant"] == [] and diag["conflicting"] == []


def test_diagnostics_are_returned_on_every_solve():
    """FR5: the block is present whatever the verdict, with the keys the
    design fixes."""
    for spec in (rectangle(), rectangle(TABLE["contradictory_distance"][0])):
        diag = solve_sketch(spec)["diagnostics"]
        assert set(diag) == {"status", "dof", "rank", "n_params", "n_residuals",
                             "redundant", "conflicting", "free_entities",
                             "analysis_ms", "analysis_complete"}
        assert diag["analysis_ms"] > 0.0


def test_a_compiled_sub_row_is_blamed_on_the_constraint_the_caller_wrote():
    """The two tangency rows of a `tangent_line_circle(at=...)` compile into
    `point_on_circle` / `point_on_line` / `tangent_point_perp` rows. A
    diagnostic must report the caller's `tangent_line_circle`, never a row
    they never wrote."""
    spec = {
        "points": [{"name": "c1", "x": 0, "y": 0, "fixed": True},
                   {"name": "c2", "x": 60, "y": 0, "fixed": True},
                   {"name": "t1", "x": 0, "y": 10},
                   {"name": "t2", "x": 60, "y": 5}],
        "lines": [{"name": "tan", "p1": "t1", "p2": "t2"}],
        "circles": [{"name": "C1", "center": "c1", "r": 10, "fixed_r": True},
                    {"name": "C2", "center": "c2", "r": 6, "fixed_r": True}],
        "constraints": [
            {"type": "tangent_line_circle", "ln": "tan", "c": "C1", "at": "t1"},
            {"type": "tangent_line_circle", "ln": "tan", "c": "C2", "at": "t2"},
        ],
    }
    result = solve_sketch(spec)
    diag = result["diagnostics"]
    assert result["ok"] is True                 # redundant, not conflicting
    assert diag["status"] == "over_constrained"
    assert diag["conflicting"] == []
    assert {e["type"] for e in diag["redundant"]} == {"tangent_line_circle"}
    assert {e["index"] for e in diag["redundant"]} <= {0, 1}


def test_a_fully_fixed_sketch_reports_its_constraints_as_dependent():
    """With zero free parameters every row is structurally dependent — and the
    split still tells the truth about which ones hold."""
    def fixed_pair(d):
        return {"points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
                           {"name": "b", "x": 10, "y": 0, "fixed": True}],
                "lines": [], "circles": [],
                "constraints": [{"type": "distance", "p": "a", "q": "b", "d": d}]}

    holds = solve_sketch(fixed_pair(10))["diagnostics"]
    assert holds["n_params"] == 0 and holds["dof"] == 0
    assert holds["redundant"] == [{"index": 0, "type": "distance", "origin": None}]
    assert holds["conflicting"] == []

    breaks = solve_sketch(fixed_pair(12))["diagnostics"]
    assert breaks["conflicting"] == [{"index": 0, "type": "distance",
                                      "origin": None}]


# ---------------- the tool contract ----------------
def sketch_tool():
    registry = ToolRegistry()
    tools_sketch.register(registry, None)   # the pack ignores the service
    return registry.get("solve_sketch")


def test_redundant_but_consistent_returns_ok_through_the_tool():
    """`over_constrained` alone is NOT an error: adding a harmless duplicate
    must not break a working sketch."""
    added = TABLE["duplicate_distance"][0]
    spec = rectangle(added)
    result = sketch_tool().handler(
        entities={k: spec[k] for k in ("points", "lines", "circles")},
        constraints=spec["constraints"])
    assert result["ok"] is True
    assert result["diagnostics"]["status"] == "over_constrained"
    assert result["diagnostics"]["redundant"][0]["index"] == ADDED
    assert result["points"]["c"]["x"] == pytest.approx(50.0, abs=1e-9)


def test_a_conflicting_set_raises_with_the_diagnostics_attached():
    """Unsatisfiability is the error, and the agent path gets the whole block."""
    spec = rectangle(TABLE["contradictory_distance"][0])
    with pytest.raises(ValidationError) as excinfo:
        sketch_tool().handler(
            entities={k: spec[k] for k in ("points", "lines", "circles")},
            constraints=spec["constraints"])
    details = excinfo.value.details
    assert details["diagnostics"]["conflicting"] == [
        {"index": ADDED, "type": "distance", "origin": None}]
    assert details["diagnostics"]["status"] == "over_constrained"
    assert "distance" in excinfo.value.message


def test_non_convergence_without_a_conflicting_set_says_which_failure_it_is():
    """A full-rank but unsatisfiable system (a negative target distance) is
    `did_not_converge`, and the message must not blame a constraint set the
    analysis did not find."""
    spec = {
        "points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
                   {"name": "b", "x": 3, "y": 4}],
        "lines": [], "circles": [],
        "constraints": [{"type": "distance", "p": "a", "q": "b", "d": -5}],
    }
    result = solve_sketch(spec)
    assert result["ok"] is False
    diag = result["diagnostics"]
    assert diag["status"] == "did_not_converge"
    assert diag["rank"] == diag["n_residuals"] == 1
    assert diag["conflicting"] == []

    with pytest.raises(ValidationError) as excinfo:
        sketch_tool().handler(
            entities={k: spec[k] for k in ("points", "lines", "circles")},
            constraints=spec["constraints"])
    assert "did not converge" in excinfo.value.message
    assert excinfo.value.details["diagnostics"]["status"] == "did_not_converge"


def test_a_contradictory_pair_blames_the_later_constraint():
    """Two `distance` constraints on the same pair: the second one is the
    dependent — and violated — row."""
    spec = {
        "points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
                   {"name": "b", "x": 3, "y": 4}],
        "lines": [], "circles": [],
        "constraints": [{"type": "distance", "p": "a", "q": "b", "d": 10},
                        {"type": "distance", "p": "a", "q": "b", "d": 20}],
    }
    diag = solve_sketch(spec)["diagnostics"]
    assert diag["status"] == "over_constrained"
    assert diag["conflicting"] == [{"index": 1, "type": "distance",
                                    "origin": None}]


def test_the_tool_description_never_claims_the_set_is_unique():
    """The honesty rule of design Decision 6, asserted rather than intended."""
    text = sketch_tool().description.lower()
    assert "dependent set" in text
    assert "declaration order" in text
    assert "not necessarily" in text or "not the unique" in text


# ---------------- FR5: the time budget ----------------
def test_an_exhausted_analysis_budget_omits_the_sets_it_did_not_compute():
    """"We did not look" is never rendered as "nothing found" (the PRD-008
    `unverified` rule, applied to numerics)."""
    spec = staircase(200)
    # one duplicate makes the system rank-deficient, so the greedy pass runs
    spec["constraints"].append({"type": "horizontal", "ln": "l0"})
    result = solve_sketch(spec, analysis_budget_ms=1.0)

    diag = result["diagnostics"]
    assert diag["analysis_complete"] is False
    assert "redundant" not in diag and "conflicting" not in diag
    # what *was* measured is still reported
    assert diag["rank"] == 400 and diag["dof"] == 0
    assert diag["status"] == "over_constrained"
    assert result["ok"] is True


def test_the_full_budget_completes_the_same_analysis():
    spec = staircase(200)
    spec["constraints"].append({"type": "horizontal", "ln": "l0"})
    diag = solve_sketch(spec)["diagnostics"]
    assert diag["analysis_complete"] is True
    assert diag["conflicting"] == []
    assert [e["index"] for e in diag["redundant"]] == [len(spec["constraints"]) - 1]


@pytest.mark.slow
def test_the_greedy_analysis_cost_is_measured_and_off_the_drag_path():
    """The number slice 8 depends on: diagnostics must not run per drag frame.

    Prints the measured cost of the greedy pass at 50/100/200 residual rows.
    A well-constrained sketch skips it entirely (there are no dependent rows
    when `rank == n_residuals`), which is what keeps a drag frame cheap even
    before slice 8's cache.
    """
    print("\n=== dependent-set analysis cost (p50 of 9) ===")
    rows = []
    for n_seg in (25, 50, 100):
        sk, J = jacobian_at_solution(staircase(n_seg))
        f = sk.make_functions()[0](sk.initial_vector())
        greedy, full = [], []
        for _ in range(9):
            t0 = time.perf_counter()
            sk.dependent_rows(J)
            greedy.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter()
            sk.analyze(J, f, ok=True)
            full.append((time.perf_counter() - t0) * 1e3)
        rows.append((J.shape[0], statistics.median(greedy), statistics.median(full)))
    print(f"{'rows':>6} {'greedy':>10} {'analyze (rank-only)':>22}")
    for n_rows, g, a in rows:
        print(f"{n_rows:>6} {g:>9.2f}ms {a:>21.2f}ms")

    # The FR5 budget is not reached below ~300 constraints (design Decision 9c).
    assert all(g < 50.0 for _, g, _ in rows), rows
    # A well-constrained sketch pays only for the rank SVD.
    assert all(a < g for _, g, a in rows), rows


def test_the_analysis_never_touches_the_solved_coordinates():
    """Diagnostics are a post-pass: turning the budget down must not change a
    single solved number."""
    spec = rectangle(TABLE["redundant_parallel"][0])
    full = solve_sketch(spec)
    starved = solve_sketch(spec, analysis_budget_ms=0.0)
    assert starved["diagnostics"]["analysis_complete"] is False
    assert full["points"] == starved["points"]
    assert np.isclose(full["max_residual"], starved["max_residual"])


# --------------------------------------------------------------------------
# row scale must not decide the rank (review P3)
# --------------------------------------------------------------------------
# Every residual that normalizes a direction (`parallel`, `perpendicular`,
# `angle`, `point_on_line`, the line-circle tangency, `_LineTangent`) divides
# its derivative by the segment's length, so a **1e-9 mm line** — one GUI
# double-click on the same spot — writes 1.4e+09 into the Jacobian. The rank
# threshold was `max(m, n) * s0 * RANK_TOL_REL` with `s0` the largest singular
# value of the whole matrix, so that one row raised the threshold to ~0.28 and
# every honest row fell under it:
#
#     rank 3 of 7, dof 7 (true 3), status over_constrained,
#     redundant [] conflicting [], free_entities ['b','c','d','z','z2']
#
# — a pinned rectangle reported as free, and a status contradicting its own
# blame set. The greedy pass never agreed, because it measures each row
# against that row's own norm. The rank is now read off the same row-scaled
# matrix, and where the greedy pass runs to completion its own count *is* the
# rank, so the two halves of the block cannot disagree.

def rect_with_degenerate_line(extra: dict) -> dict:
    spec = rectangle()
    spec["points"] += [{"name": "z", "x": 100.0, "y": 100.0},
                       {"name": "z2", "x": 100.0 + 1e-9, "y": 100.0}]
    spec["lines"] += [{"name": "zz", "p1": "z", "p2": "z2"}]
    spec["constraints"] = list(spec["constraints"]) + [extra]
    return spec


def test_one_degenerate_row_does_not_destroy_the_rank_analysis():
    """**The regression.** The rectangle is pinned either way; the 1e-9 line
    is the only thing that can still move."""
    spec = rect_with_degenerate_line({"type": "parallel", "l1": "zz",
                                      "l2": "ab"})
    diag = solve_sketch(spec)["diagnostics"]
    assert diag["n_params"] == 10 and diag["n_residuals"] == 7
    assert diag["rank"] == 7, diag
    assert diag["dof"] == 3, diag
    assert diag["status"] == "under_constrained", diag
    # and the pinned rectangle is not reported as free geometry
    assert set(diag["free_entities"]) == {"z", "z2"}, diag


def test_the_rank_is_free_of_row_scale():
    """Scaling a row changes nothing about which rows are independent, so it
    must change nothing about the rank — the property the threshold broke."""
    spec = rectangle({"type": "parallel", "l1": "ab", "l2": "cd"})
    sk, J = jacobian_at_solution(spec)
    base = sk.rank(J)
    scaled = np.array(J, dtype=float)
    scaled[0] *= 1e9
    scaled[3] *= 1e-9
    assert sk.rank(scaled) == base


def test_the_status_and_the_blame_set_can_never_disagree():
    """`over_constrained` with an empty blame set is a bug, not a display
    problem: the status is derived from the same dependent-row count the sets
    are. Asserted over the whole diagnostics corpus, including the degenerate
    row that produced the contradiction."""
    specs = [rectangle(), *(rectangle(TABLE[k][0]) for k in sorted(TABLE)),
             rect_with_degenerate_line({"type": "parallel", "l1": "zz",
                                        "l2": "ab"}),
             rect_with_degenerate_line({"type": "perpendicular", "l1": "zz",
                                        "l2": "ab"}),
             rect_with_degenerate_line({"type": "point_on_line", "p": "a",
                                        "ln": "zz"})]
    for spec in specs:
        diag = solve_sketch(spec)["diagnostics"]
        if not diag.get("analysis_complete", True):
            continue
        blamed = len(diag["redundant"]) + len(diag["conflicting"])
        assert (diag["status"] == "over_constrained") == (blamed > 0), diag
        assert (diag["rank"] < diag["n_residuals"]) == (blamed > 0), diag


# --------------------------------------------------------------------------
# the audit: no residual is stationary where the sketch pins its arguments
# --------------------------------------------------------------------------
# The tangency degeneracy was found three times because it was hunted one
# instance at a time. The property behind it is general and testable: a
# residual whose value sits at an extremum of the manifold the *other*
# constraints cut out has a gradient in their span, so it reports itself
# redundant while removing a real degree of freedom.
#
# `dof` is `n_params - rank(J)` and removing a constraint cannot change
# `n_params`, so "this constraint removes a DOF" is exactly "dropping it
# raises `dof`" — measurable without knowing anything about the residual's
# algebra. Every constraint in the corpus below removes a DOF, so none of them
# may be blamed. Audited by hand at the same time, and the two agree: the only
# residuals in the vocabulary whose value is a *distance at an extremum* are
# `tangent_line_circle` and `tangent_circles`, and both are now unreachable at
# a pinned junction. Everything else is linear in its arguments (`fixed`,
# `coincident`, `distance_x/y`, `horizontal`, `vertical`, `radius`,
# `equal_radius`, `midpoint`), a unit-vector product (`parallel`,
# `perpendicular`, `tangent_point_perp`, `tangent_dir`, `symmetric`) or an
# angle (`angle`) — all first-order where they hold.

AUDIT: dict[str, dict] = {
    # line/point vocabulary
    "lines": {
        "points": [{"name": "a", "x": 0.0, "y": 0.0},
                   {"name": "b", "x": 50.0, "y": 1.0},
                   {"name": "c", "x": 51.0, "y": 30.0},
                   {"name": "m", "x": 25.0, "y": 0.5},
                   {"name": "q", "x": 50.5, "y": 15.0}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"}],
        "constraints": [
            {"type": "fixed", "p": "a", "x": 0.0, "y": 0.0},
            {"type": "horizontal", "ln": "ab"},
            {"type": "distance", "p": "a", "q": "b", "d": 50.0},
            {"type": "perpendicular", "l1": "ab", "l2": "bc"},
            {"type": "distance_y", "p": "b", "q": "c", "d": 30.0},
            {"type": "midpoint", "p": "m", "ln": "ab"},
            {"type": "point_on_line", "p": "q", "ln": "bc"},
            {"type": "distance", "p": "b", "q": "q", "d": 15.0},
        ],
    },
    "angles_and_mirrors": {
        "points": [{"name": "o", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "p", "x": 30.0, "y": 0.0},
                   {"name": "r", "x": 0.0, "y": 20.0},
                   {"name": "s", "x": 18.0, "y": 9.0},
                   {"name": "t", "x": 18.0, "y": -9.0}],
        "lines": [{"name": "op", "p1": "o", "p2": "p"},
                  {"name": "or_", "p1": "o", "p2": "r"},
                  {"name": "axis", "p1": "o", "p2": "p"}],
        "constraints": [
            {"type": "horizontal", "ln": "op"},
            {"type": "distance", "p": "o", "q": "p", "d": 30.0},
            {"type": "angle", "l1": "op", "l2": "or_", "deg": 70.0},
            {"type": "equal_length", "l1": "op", "l2": "or_"},
            {"type": "symmetric", "a": "s", "b": "t", "about": "axis"},
            {"type": "distance_x", "p": "o", "q": "s", "d": 18.0},
        ],
    },
    "radial": {
        "points": [{"name": "c1", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "c2", "x": 40.0, "y": 3.0},
                   {"name": "c3", "x": 40.0, "y": 3.0},
                   {"name": "p", "x": 0.0, "y": 12.0},
                   {"name": "u", "x": -30.0, "y": 12.0},
                   {"name": "w", "x": 30.0, "y": 12.0}],
        "circles": [{"name": "C1", "center": "c1", "r": 12.0},
                    {"name": "C2", "center": "c2", "r": 12.0},
                    {"name": "C3", "center": "c3", "r": 5.0}],
        "lines": [{"name": "top", "p1": "u", "p2": "w"}],
        "constraints": [
            {"type": "radius", "c": "C1", "r": 12.0},
            {"type": "equal_radius", "c1": "C1", "c2": "C2"},
            {"type": "concentric", "a": "C2", "b": "C3"},
            {"type": "point_on_circle", "p": "p", "c": "C1"},
            {"type": "vertical", "ln": "top"},
            {"type": "distance_x", "p": "c1", "q": "c2", "d": 40.0},
            {"type": "distance_y", "p": "c1", "q": "c2", "d": 3.0},
            {"type": "radius", "c": "C3", "r": 5.0},
        ],
    },
    # every tangency form, each at a junction the sketch pins a different way
    "tangencies": {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "j", "x": 12.0, "y": 0.0},
                   {"name": "k", "x": 12.0, "y": 25.0},
                   {"name": "e1", "x": -40.0, "y": 6.0},
                   {"name": "e2", "x": -20.0, "y": 6.0},
                   {"name": "ac", "x": -30.0, "y": 18.0}],
        "circles": [{"name": "C", "center": "c", "r": 12.0}],
        "arcs": [{"name": "A", "center": "ac", "r": 12.0,
                  "start_deg": 180.0, "end_deg": 300.0}],
        "lines": [{"name": "L", "p1": "j", "p2": "k"},
                  {"name": "M", "p1": "e1", "p2": "e2"}],
        "constraints": [
            {"type": "radius", "c": "C", "r": 12.0},
            {"type": "point_on_circle", "p": "j", "c": "C"},
            {"type": "tangent", "a": "L", "b": "C"},
            {"type": "coincident", "p": "A.start", "q": "e1"},
            {"type": "tangent", "a": "M", "b": "A"},
            {"type": "radius", "c": "A", "r": 12.0},
            {"type": "distance", "p": "j", "q": "k", "d": 25.0},
            {"type": "distance", "p": "e1", "q": "e2", "d": 20.0},
        ],
    },
}


@pytest.mark.parametrize("case", sorted(AUDIT))
def test_no_constraint_that_removes_a_dof_is_ever_blamed(case):
    """The audit, run rather than asserted. Every constraint in the corpus
    removes at least one degree of freedom, so nothing may be reported
    redundant — and each removal is *checked* by dropping the constraint and
    watching `dof` rise, so the corpus cannot rot into one of over-constrained
    sketches that pass vacuously."""
    spec = AUDIT[case]
    full = solve_sketch(spec)
    diag = full["diagnostics"]
    assert diag["redundant"] == [], diag
    assert diag["conflicting"] == [], diag
    assert diag["rank"] == diag["n_residuals"], diag
    cons = spec["constraints"]
    for i, con in enumerate(cons):
        without = solve_sketch({**spec, "constraints": cons[:i] + cons[i + 1:]})
        assert without["dof"] > full["dof"], (
            f"{case}[{i}] {con['type']} removes no DOF, so it is genuinely "
            f"dependent and does not belong in the audit corpus")
