"""Every residual's analytic `df`, proven against a central difference of `f`.

This is the highest-value test in PRD-009. A wrong analytic derivative does
not crash: it converges slowly, to the wrong branch, or not at all, and it is
very hard to debug from the outside. So every registered residual kind is
evaluated at randomized parameter vectors and compared column-by-column with a
central difference of *its own* `f` — including the columns it did not
declare, which is how an under-declared `params` tuple is caught.

`RESIDUAL_KINDS` is the coverage gate: a kind added to the solver without a
case in *some* derivative harness fails
`test_every_registered_residual_kind_is_covered`. Later slices add their kinds
in their own modules (`test_sketch_arcs.py`, ...); each exports a module-level
`DERIV_BUILDERS` mapping and the gate unions every one it can find, so the
coverage requirement travels with the kind rather than with this file.
"""

import importlib
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from agentcad.toolkit.sketch import (RESIDUAL_KINDS, Residual, Sketch,
                                     SketchError, solve_sketch)

RTOL = 1e-6
ATOL = 1e-8
DRAWS = 5          # randomized parameter vectors per residual
JITTER = 1.5       # mm; every base sketch stays non-degenerate under this


def _lines_sketch() -> Sketch:
    """Line/point vocabulary: 12 of the 18 residual kinds."""
    sk = Sketch()
    sk.point("p0", 0.0, 0.0)
    sk.point("p1", 20.0, 1.0)
    sk.point("p2", 21.0, 18.0)
    sk.point("p3", -1.0, 17.0)
    sk.point("m", 10.0, 0.5)
    sk.point("q", 20.5, 9.0)
    sk.point("z", 4.0, 4.0)
    sk.line("l0", "p0", "p1")
    sk.line("l1", "p1", "p2")
    sk.line("l2", "p2", "p3")
    sk.fixed("p3", -1.0, 17.0)
    sk.coincident("z", "m")
    sk.distance("p0", "p1", 20.0)
    sk.distance_x("p0", "p2", 21.0)
    sk.distance_y("p0", "p2", 18.0)
    sk.horizontal("l0")
    sk.vertical("l1")
    sk.parallel("l0", "l2")
    sk.perpendicular("l0", "l1")
    sk.angle("l0", "l1", 90.0)
    sk.point_on_line("q", "l1")
    sk.midpoint("m", "l0")
    return sk


def _circles_sketch() -> Sketch:
    """Radius/tangency vocabulary, unsigned line-circle tangency included."""
    sk = Sketch()
    sk.point("c1", 0.0, 0.0)
    sk.point("c2", 34.0, 3.0)
    sk.point("t", 8.0, 6.0)
    sk.point("la", -20.0, 14.0)
    sk.point("lb", 25.0, 13.0)
    sk.circle("C1", "c1", 10.0)
    sk.circle("C2", "c2", 6.0)
    sk.line("L", "la", "lb")
    sk.radius("C1", 10.0)
    sk.equal_radius("C1", "C2")
    sk.point_on_circle("t", "C1")
    sk.tangent_line_circle("L", "C1")           # unsigned, 1 row
    sk.tangent_circles("C1", "C2", "external")
    sk.tangent_circles("C1", "C2", "internal")
    return sk


def _tangency_point_sketch() -> Sketch:
    """The 3-row `at` form: on-circle + on-line + centre->at perpendicular."""
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.point("u", 0.0, -30.0)
    sk.point("w", 22.0, 14.0)
    sk.point("t", 9.4, -3.3)
    sk.circle("C", "c", 10.0)
    sk.line("l", "u", "w")
    sk.tangent_line_circle("l", "C", at="t")
    return sk


def _fixed_entity_sketch() -> Sketch:
    """Fixed points and a fixed radius contribute no columns at all."""
    sk = Sketch()
    sk.point("a", 0.0, 0.0, fixed=True)
    sk.point("b", 30.0, 4.0)
    sk.point("c", 12.0, 20.0, fixed=True)
    sk.circle("C", "c", 7.0, fixed_r=True)
    sk.line("ab", "a", "b")
    sk.distance("a", "b", 30.0)
    sk.point_on_circle("b", "C")
    sk.horizontal("ab")
    return sk


BUILDERS = {
    "lines": _lines_sketch,
    "circles": _circles_sketch,
    "tangency_point": _tangency_point_sketch,
    "fixed_entities": _fixed_entity_sketch,
}

# The coverage gate below unions this mapping across every `test_sketch_*.py`
# module that exports one.
DERIV_BUILDERS = BUILDERS


def _draws(sk: Sketch, seed: int) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    x0 = sk.initial_vector()
    return [x0] + [x0 + rng.uniform(-JITTER, JITTER, x0.shape)
                   for _ in range(DRAWS - 1)]


def _central(res: Residual, v: np.ndarray, ix: int) -> np.ndarray:
    h = 1e-6 * (1.0 + abs(float(v[ix])))
    vp, vm = v.copy(), v.copy()
    vp[ix] += h
    vm[ix] -= h
    return (np.asarray(res.f(vp), float) - np.asarray(res.f(vm), float)) / (2 * h)


def assert_df_matches_central_difference(name, sk):
    """The gate on every analytic derivative in the solver.

    Public so the slices that add residual kinds can reuse the harness from
    their own test modules instead of copying it.
    """
    n_par = sk.n_par
    for v in _draws(sk, seed=sum(bytearray(name.encode()))):
        for k, res in enumerate(sk.residuals):
            J = np.zeros((res.rows, n_par))
            res.df(v, J, 0)
            for ix in range(n_par):
                want = _central(res, v, ix)
                got = J[:, ix]
                assert got == pytest.approx(want, rel=RTOL, abs=ATOL), (
                    f"{name}: residual {k} ({res.kind}) d/dparam[{ix}] "
                    f"analytic={got} central-difference={want}")


def assert_df_stays_inside_params(name, sk):
    """`params` is what the rank analysis and future sparsity rely on."""
    for k, res in enumerate(sk.residuals):
        assert len(set(res.params)) == len(res.params), f"{res.kind} params dupes"
        assert all(0 <= ix < sk.n_par for ix in res.params), res.kind
        for v in _draws(sk, seed=7 + k):
            J = np.zeros((res.rows, sk.n_par))
            res.df(v, J, 0)
            outside = [ix for ix in range(sk.n_par) if ix not in set(res.params)]
            assert not J[:, outside].any(), (
                f"{name}: residual {k} ({res.kind}) wrote outside its params "
                f"{res.params}")


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_every_df_matches_a_central_difference_of_its_own_f(name):
    assert_df_matches_central_difference(name, BUILDERS[name]())


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_df_writes_only_inside_its_declared_params(name):
    assert_df_stays_inside_params(name, BUILDERS[name]())


def _covered_kinds() -> set[str]:
    """Every residual kind built by any derivative harness in the suite."""
    seen: set[str] = set()
    for path in sorted(Path(__file__).parent.glob("test_sketch_*.py")):
        mod = importlib.import_module(f"{__package__}.{path.stem}")
        for build in getattr(mod, "DERIV_BUILDERS", {}).values():
            seen |= {res.kind for res in build().residuals}
    return seen


def test_every_registered_residual_kind_is_covered():
    """A new residual kind without a derivative test is the failure mode this
    whole file exists to prevent."""
    assert _covered_kinds() == set(RESIDUAL_KINDS)


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_assembled_jacobian_matches_a_finite_difference_of_the_whole_system(name):
    """Catches a wrong row offset, which a per-residual check cannot see."""
    sk = BUILDERS[name]()
    fun, jac = sk.make_functions()
    v = _draws(sk, seed=99)[1]
    analytic = jac(v).copy()
    numeric = np.empty_like(analytic)
    for ix in range(sk.n_par):
        h = 1e-6 * (1.0 + abs(float(v[ix])))
        vp, vm = v.copy(), v.copy()
        vp[ix] += h
        vm[ix] -= h
        numeric[:, ix] = (fun(vp) - fun(vm)) / (2 * h)
    assert analytic == pytest.approx(numeric, rel=RTOL, abs=ATOL)


def test_the_jacobian_buffer_is_allocated_once_per_solve():
    """A Jacobian reallocated per call is the documented way to lose the 66x."""
    sk = _lines_sketch()
    _, jac = sk.make_functions()
    v = sk.initial_vector()
    assert jac(v) is jac(v + 0.1)


def test_the_residual_vector_is_not_a_shared_buffer():
    """least_squares holds the previous residual vector across an iteration."""
    sk = _lines_sketch()
    fun, _ = sk.make_functions()
    v = sk.initial_vector()
    first = fun(v)
    second = fun(v + 1.0)
    assert first is not second
    assert not np.array_equal(first, second)


def test_a_residual_without_a_df_is_refused_at_construction():
    """Not at solve time, when the cost would be silent instead of loud."""
    with pytest.raises(SketchError, match="analytic derivative"):
        Residual(0, "bogus", 1, (0,), lambda v: (0.0,), None)
    with pytest.raises(SketchError, match="analytic derivative"):
        Residual(0, "bogus", 1, (0,), None, lambda v, J, r: None)


def test_residual_records_carry_the_spec_index_that_produced_them():
    """`con_index` is what lets slice 3 name a constraint the user wrote."""
    spec = {
        "points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
                   {"name": "b", "x": 30, "y": 1}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"}],
        "circles": [{"name": "C", "center": "a", "r": 10, "fixed_r": True}],
        "constraints": [
            {"type": "horizontal", "ln": "ab"},
            {"type": "distance", "p": "a", "q": "b", "d": 50},
            {"type": "tangent_line_circle", "ln": "ab", "c": "C", "at": "b"},
        ],
    }
    sk = Sketch()
    for p in spec["points"]:
        sk.point(p["name"], p["x"], p["y"], p.get("fixed", False))
    for line in spec["lines"]:
        sk.line(line["name"], line["p1"], line["p2"])
    for c in spec["circles"]:
        sk.circle(c["name"], c["center"], c["r"], c.get("fixed_r", False))
    sk.horizontal("ab")
    sk.distance("a", "b", 50)
    sk.tangent_line_circle("ab", "C", at="b")

    assert [r.con_index for r in sk.residuals] == [0, 1, 2, 2, 2]
    assert sk.con_types == ["horizontal", "distance", "tangent_line_circle"]
    # The compiled sub-residuals of the `at` form all report constraint 2 --
    # a diagnostic must never blame a row the caller did not write.
    assert [r.kind for r in sk.residuals if r.con_index == 2] == [
        "point_on_circle", "point_on_line", "tangent_point_perp"]


def test_dof_is_rank_based_not_a_row_count():
    """The shipped `n_params - n_residuals` reports a negative dof for any
    redundant constraint; `n_params - rank(J)` is the real answer."""
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
    r = solve_sketch(spec)
    assert r["ok"]
    assert r["n_params"] == 4 and r["n_residuals"] == 6
    assert r["rank"] == 4
    assert r["dof"] == 0            # the shipped solver reported -2 here


_NO_KERNEL_PROBE = """
import importlib
import sys


class _Blocked:
    \"\"\"Refuse OCP/build123d so an accidental kernel import is a hard error.\"\"\"

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("OCP", "build123d"):
            raise ImportError("blocked kernel import: " + name)
        return None


sys.meta_path.insert(0, _Blocked())
mod = importlib.import_module("agentcad.toolkit.sketch")
res = mod.solve_sketch({"points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
                                   {"name": "b", "x": 3, "y": 1}],
                        "lines": [{"name": "ab", "p1": "a", "p2": "b"}],
                        "circles": [],
                        "constraints": [{"type": "horizontal", "ln": "ab"},
                                        {"type": "distance", "p": "a", "q": "b",
                                         "d": 5}]})
assert res["ok"] and abs(res["points"]["b"]["x"] - 5.0) < 1e-9, res
assert "OCP" not in sys.modules and "build123d" not in sys.modules
print("ok")
"""


@pytest.mark.integration
@pytest.mark.portability
def test_solver_imports_and_solves_with_no_kernel_available():
    """The solver runs in the SERVER process, which may not import build123d."""
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, "-c", _NO_KERNEL_PROBE],
                          cwd=repo, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")
