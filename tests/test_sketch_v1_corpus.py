"""The v1 sketch-solver compatibility corpus (PRD-009 FR3).

One case per shipped constraint type, plus the three sketches already covered
by ``tests/test_sketch.py``. Every expectation below was **captured from the
shipped prototype solver** (``agentcad/toolkit/sketch.py`` at the head of the
PRD-009 branch, before the slice-2 rewrite) and is asserted to ``abs=1e-9``.

This file is the compatibility harness for the solver rewrite, in the same
spirit as "the test suite is the build123d compat harness": if a residual
formulation, a solve method or a Jacobian changes the answer, it fails here.

Every case is **exactly constrained** on purpose — an under-constrained case
has a solution that depends on the optimizer's path, which would make this
corpus a test of scipy rather than of the solver.

``dof`` is deliberately *not* asserted: PRD-009 replaces the shipped row count
``n_params - n_residuals`` with ``n_params - rank(J)``, which is a bug fix
(``shipped_two_circle_tangent_line`` reports ``dof: -2`` today). ``n_params``
and ``n_residuals`` — the two keys FR3 freezes — are asserted instead.
"""

import pytest

from agentcad.toolkit.sketch import solve_sketch

# The 17 constraint types the shipped solver dispatches. The corpus must cover
# every one of them; `test_corpus_covers_every_v1_constraint_type` is the gate.
V1_TYPES = {
    "fixed", "coincident", "distance", "distance_x", "distance_y",
    "horizontal", "vertical", "parallel", "perpendicular", "angle",
    "point_on_line", "point_on_circle", "radius", "equal_radius", "midpoint",
    "tangent_line_circle", "tangent_circles",
}


def _sk(points=(), lines=(), circles=(), constraints=()):
    return {"points": list(points), "lines": list(lines),
            "circles": list(circles), "constraints": list(constraints)}


def _p(name, x, y, fixed=False):
    return {"name": name, "x": x, "y": y, "fixed": fixed}


def _l(name, p1, p2):
    return {"name": name, "p1": p1, "p2": p2}


def _c(name, center, r, fixed_r=False):
    return {"name": name, "center": center, "r": r, "fixed_r": fixed_r}


CASES = {
    "fixed": _sk(
        points=[_p("p", 5, 5)],
        constraints=[{"type": "fixed", "p": "p", "x": 10, "y": 20}],
    ),
    "coincident": _sk(
        points=[_p("anchor", 10, 20, True), _p("p", 0, 0)],
        constraints=[{"type": "coincident", "p": "p", "q": "anchor"}],
    ),
    "distance": _sk(
        points=[_p("a", 0, 0, True), _p("b", 30, 1)],
        lines=[_l("ab", "a", "b")],
        constraints=[{"type": "horizontal", "ln": "ab"},
                     {"type": "distance", "p": "a", "q": "b", "d": 50}],
    ),
    "distance_x": _sk(
        points=[_p("a", 0, 0, True), _p("b", 30, 4)],
        lines=[_l("ab", "a", "b")],
        constraints=[{"type": "horizontal", "ln": "ab"},
                     {"type": "distance_x", "p": "a", "q": "b", "d": 50}],
    ),
    "distance_y": _sk(
        points=[_p("a", 0, 0, True), _p("b", 4, 30)],
        lines=[_l("ab", "a", "b")],
        constraints=[{"type": "vertical", "ln": "ab"},
                     {"type": "distance_y", "p": "a", "q": "b", "d": 25}],
    ),
    "horizontal": _sk(
        points=[_p("a", 0, 0, True), _p("b", 30, 4)],
        lines=[_l("ab", "a", "b")],
        constraints=[{"type": "horizontal", "ln": "ab"},
                     {"type": "distance", "p": "a", "q": "b", "d": 25}],
    ),
    "vertical": _sk(
        points=[_p("a", 0, 0, True), _p("b", 4, 30)],
        lines=[_l("ab", "a", "b")],
        constraints=[{"type": "vertical", "ln": "ab"},
                     {"type": "distance", "p": "a", "q": "b", "d": 25}],
    ),
    "parallel": _sk(
        points=[_p("a", 0, 0, True), _p("b", 10, 0, True),
                _p("c", 0, 10, True), _p("d", 9, 12)],
        lines=[_l("l1", "a", "b"), _l("l2", "c", "d")],
        constraints=[{"type": "parallel", "l1": "l1", "l2": "l2"},
                     {"type": "distance", "p": "c", "q": "d", "d": 20}],
    ),
    "perpendicular": _sk(
        points=[_p("a", 0, 0, True), _p("b", 10, 0, True),
                _p("c", 5, 0, True), _p("d", 6, 9)],
        lines=[_l("l1", "a", "b"), _l("l2", "c", "d")],
        constraints=[{"type": "perpendicular", "l1": "l1", "l2": "l2"},
                     {"type": "distance", "p": "c", "q": "d", "d": 12}],
    ),
    "angle": _sk(
        points=[_p("a", 0, 0, True), _p("b", 10, 0, True), _p("d", 9, 4)],
        lines=[_l("l1", "a", "b"), _l("l2", "a", "d")],
        constraints=[{"type": "angle", "l1": "l1", "l2": "l2", "deg": 30},
                     {"type": "distance", "p": "a", "q": "d", "d": 20}],
    ),
    "point_on_line": _sk(
        points=[_p("a", 0, 0, True), _p("b", 100, 0, True), _p("p", 30, 5)],
        lines=[_l("ab", "a", "b")],
        constraints=[{"type": "point_on_line", "p": "p", "ln": "ab"},
                     {"type": "distance", "p": "a", "q": "p", "d": 40}],
    ),
    "point_on_circle": _sk(
        points=[_p("c", 0, 0, True), _p("p", 7, 7)],
        circles=[_c("C", "c", 10, True)],
        constraints=[{"type": "point_on_circle", "p": "p", "c": "C"},
                     {"type": "distance_x", "p": "c", "q": "p", "d": 6}],
    ),
    "radius": _sk(
        points=[_p("c", 0, 0, True)],
        circles=[_c("C", "c", 5)],
        constraints=[{"type": "radius", "c": "C", "r": 12}],
    ),
    "equal_radius": _sk(
        points=[_p("c1", 0, 0, True), _p("c2", 40, 0, True)],
        circles=[_c("C1", "c1", 5), _c("C2", "c2", 9)],
        constraints=[{"type": "equal_radius", "c1": "C1", "c2": "C2"},
                     {"type": "radius", "c": "C1", "r": 12}],
    ),
    "midpoint": _sk(
        points=[_p("a", 0, 0, True), _p("b", 10, 40, True), _p("m", 1, 1)],
        lines=[_l("ab", "a", "b")],
        constraints=[{"type": "midpoint", "p": "m", "ln": "ab"}],
    ),
    # Unsigned form: distance(centre, line) == r, 1 residual.
    "tangent_line_circle": _sk(
        points=[_p("c", 0, 0, True), _p("a", -20, 10, True), _p("b", 20, 11)],
        lines=[_l("l", "a", "b")],
        circles=[_c("C", "c", 10, True)],
        constraints=[{"type": "tangent_line_circle", "ln": "l", "c": "C"},
                     {"type": "distance", "p": "a", "q": "b", "d": 40}],
    ),
    # `at` form: point_on_circle + point_on_line + centre->at perpendicular
    # to the line, 3 residuals.
    "tangent_line_circle_at": _sk(
        points=[_p("c", 0, 0, True), _p("u", 0, -30, True),
                _p("w", 40, -25), _p("t", 9, -4)],
        lines=[_l("l", "u", "w")],
        circles=[_c("C", "c", 10, True)],
        constraints=[{"type": "tangent_line_circle", "ln": "l", "c": "C", "at": "t"},
                     {"type": "distance", "p": "u", "q": "w", "d": 50}],
    ),
    "tangent_circles_external": _sk(
        points=[_p("c1", 0, 0, True), _p("c2", 30, 2)],
        circles=[_c("C1", "c1", 10, True), _c("C2", "c2", 6, True)],
        constraints=[{"type": "tangent_circles", "c1": "C1", "c2": "C2",
                      "kind": "external"},
                     {"type": "distance_y", "p": "c1", "q": "c2", "d": 0}],
    ),
    "tangent_circles_internal": _sk(
        points=[_p("c1", 0, 0, True), _p("c2", 5, 1)],
        circles=[_c("C1", "c1", 10, True), _c("C2", "c2", 6, True)],
        constraints=[{"type": "tangent_circles", "c1": "C1", "c2": "C2",
                      "kind": "internal"},
                     {"type": "distance_y", "p": "c1", "q": "c2", "d": 0}],
    ),

    # --- the three sketches shipped in tests/test_sketch.py, to 1e-9 --------
    "shipped_rectangle": _sk(
        points=[_p("o", 0, 0, True), _p("bx", 30, 1), _p("ty", 1, 20)],
        lines=[_l("bottom", "o", "bx"), _l("left", "o", "ty")],
        constraints=[{"type": "horizontal", "ln": "bottom"},
                     {"type": "vertical", "ln": "left"},
                     {"type": "distance", "p": "o", "q": "bx", "d": 80},
                     {"type": "distance", "p": "o", "q": "ty", "d": 40}],
    ),
    "shipped_two_circle_tangent_line": _sk(
        points=[_p("c1", 0, 0, True), _p("c2", 60, 0, True),
                _p("t1", 0, 10), _p("t2", 60, 5)],
        lines=[_l("tan", "t1", "t2")],
        circles=[_c("C1", "c1", 10, True), _c("C2", "c2", 6, True)],
        constraints=[{"type": "tangent_line_circle", "ln": "tan", "c": "C1", "at": "t1"},
                     {"type": "tangent_line_circle", "ln": "tan", "c": "C2", "at": "t2"}],
    ),
    "shipped_build123d_feed": _sk(
        points=[_p("a", 0, 0, True), _p("b", 40, 1),
                _p("c", 41, 25), _p("d", 1, 24)],
        lines=[_l("ab", "a", "b"), _l("ad", "a", "d")],
        constraints=[{"type": "horizontal", "ln": "ab"},
                     {"type": "vertical", "ln": "ad"},
                     {"type": "distance", "p": "a", "q": "b", "d": 50},
                     {"type": "distance", "p": "a", "q": "d", "d": 30},
                     {"type": "distance_x", "p": "a", "q": "c", "d": 50},
                     {"type": "distance_y", "p": "a", "q": "c", "d": 30}],
    ),
}

# Captured from the shipped solver on 2026-08-12 (M1 Max, scipy 1.18.0).
EXPECTED = {
    "fixed": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "p": (10.0, 20.0),
        },
        "circles": {},
    },
    "coincident": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "anchor": (10.0, 20.0),
            "p": (10.0, 20.0),
        },
        "circles": {},
    },
    "distance": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (50.0, 0.0),
        },
        "circles": {},
    },
    "distance_x": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (50.0, 0.0),
        },
        "circles": {},
    },
    "distance_y": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (0.0, 25.0),
        },
        "circles": {},
    },
    "horizontal": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (25.0, 0.0),
        },
        "circles": {},
    },
    "vertical": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (0.0, 25.0),
        },
        "circles": {},
    },
    "parallel": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (10.0, 0.0),
            "c": (0.0, 10.0),
            "d": (20.0, 10.0),
        },
        "circles": {},
    },
    "perpendicular": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (10.0, 0.0),
            "c": (5.0, 0.0),
            "d": (5.0, 12.0),
        },
        "circles": {},
    },
    "angle": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (10.0, 0.0),
            "d": (17.320508075688775, 10.0),
        },
        "circles": {},
    },
    "point_on_line": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (100.0, 0.0),
            "p": (40.0, 0.0),
        },
        "circles": {},
    },
    "point_on_circle": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "c": (0.0, 0.0),
            "p": (6.0, 7.999999999999999),
        },
        "circles": {
            "C": (0.0, 0.0, 10.0),
        },
    },
    "radius": {
        "n_params": 1, "n_residuals": 1,
        "points": {
            "c": (0.0, 0.0),
        },
        "circles": {
            "C": (0.0, 0.0, 12.0),
        },
    },
    "equal_radius": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "c1": (0.0, 0.0),
            "c2": (40.0, 0.0),
        },
        "circles": {
            "C1": (0.0, 0.0, 12.0),
            "C2": (40.0, 0.0, 12.0),
        },
    },
    "midpoint": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "a": (0.0, 0.0),
            "b": (10.0, 40.0),
            "m": (5.0, 20.0),
        },
        "circles": {},
    },
    "tangent_line_circle": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "c": (0.0, 0.0),
            "a": (-20.0, 10.0),
            "b": (20.0, 10.0),
        },
        "circles": {
            "C": (0.0, 0.0, 10.0),
        },
    },
    "tangent_line_circle_at": {
        "n_params": 4, "n_residuals": 4,
        "points": {
            "c": (0.0, 0.0),
            "u": (0.0, -30.0),
            "w": (16.666666666666664, 17.140452079103166),
            "t": (9.428090415820632, -3.333333333333333),
        },
        "circles": {
            "C": (0.0, 0.0, 10.0),
        },
    },
    "tangent_circles_external": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "c1": (0.0, 0.0),
            "c2": (16.0, 0.0),
        },
        "circles": {
            "C1": (0.0, 0.0, 10.0),
            "C2": (16.0, 0.0, 6.0),
        },
    },
    "tangent_circles_internal": {
        "n_params": 2, "n_residuals": 2,
        "points": {
            "c1": (0.0, 0.0),
            "c2": (4.0, 0.0),
        },
        "circles": {
            "C1": (0.0, 0.0, 10.0),
            "C2": (4.0, 0.0, 6.0),
        },
    },
    "shipped_rectangle": {
        "n_params": 4, "n_residuals": 4,
        "points": {
            "o": (0.0, 0.0),
            "bx": (80.0, 0.0),
            "ty": (0.0, 40.0),
        },
        "circles": {},
    },
    "shipped_two_circle_tangent_line": {
        "n_params": 4, "n_residuals": 6,
        "points": {
            "c1": (0.0, 0.0),
            "c2": (60.0, 0.0),
            "t1": (0.6666666666666666, 9.977753031397176),
            "t2": (60.4, 5.986651818838307),
        },
        "circles": {
            "C1": (0.0, 0.0, 10.0),
            "C2": (60.0, 0.0, 6.0),
        },
    },
    "shipped_build123d_feed": {
        "n_params": 6, "n_residuals": 6,
        "points": {
            "a": (0.0, 0.0),
            "b": (50.0, 0.0),
            "c": (50.0, 30.0),
            "d": (0.0, 30.0),
        },
        "circles": {},
    },
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_v1_case_solves_to_the_captured_coordinates(name):
    """FR3: the v1 vocabulary keeps its answers to 1e-9."""
    result = solve_sketch(CASES[name])
    want = EXPECTED[name]

    assert result["ok"] is True
    assert result["n_params"] == want["n_params"]
    assert result["n_residuals"] == want["n_residuals"]
    assert result["max_residual"] < 1e-9

    assert set(result["points"]) == set(want["points"])
    for pname, (x, y) in want["points"].items():
        got = result["points"][pname]
        assert got["x"] == pytest.approx(x, abs=1e-9), f"{name}.{pname}.x"
        assert got["y"] == pytest.approx(y, abs=1e-9), f"{name}.{pname}.y"

    assert set(result["circles"]) == set(want["circles"])
    for cname, (cx, cy, r) in want["circles"].items():
        got = result["circles"][cname]
        assert got["cx"] == pytest.approx(cx, abs=1e-9), f"{name}.{cname}.cx"
        assert got["cy"] == pytest.approx(cy, abs=1e-9), f"{name}.{cname}.cy"
        assert got["r"] == pytest.approx(r, abs=1e-9), f"{name}.{cname}.r"


def test_corpus_covers_every_v1_constraint_type():
    """A shipped constraint type without a corpus case is an unguarded type."""
    seen = {c["type"] for spec in CASES.values() for c in spec["constraints"]}
    assert seen == V1_TYPES


def test_result_keys_are_the_frozen_v1_set():
    """FR3 freezes these keys; PRD-009 may only add to them."""
    result = solve_sketch(CASES["shipped_rectangle"])
    frozen = {"ok", "max_residual", "n_params", "n_residuals", "dof", "nfev",
              "solve_ms", "points", "circles"}
    assert frozen <= set(result)
