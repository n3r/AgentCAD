"""2D sketch constraint solver — a typed residual IR with analytic Jacobians.

Runs in the **server** process (`core/tools_sketch.py` imports it) as well as
in part scripts, so it must never import build123d/OCP. numpy and scipy are
declared dependencies of this package for exactly that reason.

Entities:
  point  (x, y)            -- free or fixed
  line   (p1, p2)          -- reference to two points (no own params)
  circle (center, r)       -- center point ref + radius param (free or fixed)

Constraint vocabulary v1:
  fixed, coincident, distance, distance_x, distance_y,
  horizontal, vertical, parallel, perpendicular, angle,
  point_on_line, point_on_circle, radius, equal_radius,
  tangent (line-circle, with optional tangency point; circle-circle
  external/internal), midpoint.

## The residual IR

Every constraint compiles to one or more `Residual` records carrying the spec
index that produced them, the parameter slots they can touch, their value
function `f` and their **analytic derivative** `df`. That buys three things:

- `df` makes the Jacobian one pass instead of `n_params + 1` finite-difference
  passes. Measured on a 50-segment staircase: 50.5 ms -> 0.5 ms warm.
  **A residual without a `df` reintroduces the O(n^2) cost, so `Residual`
  refuses to be constructed without one**, and every `df` is proven against a
  central difference of its own `f` in `tests/test_sketch_jacobian.py`.
- `con_index` lets a diagnostic name the constraint the caller actually wrote.
- `PointRef` indirection (a name -> value/gradient/param slots) is what will
  let arcs, ellipses and splines reuse this vocabulary through virtual handles
  (`arc1.end`) instead of multiplying constraint types.

Solve: `scipy.optimize.least_squares(..., jac=..., method="trf")`. `trf` is
used uniformly — MINPACK's `lm` requires `m >= n`, which an under-constrained
sketch violates, and the measurement shows the method is noise next to the
Jacobian. Residuals are scaled so lengths and unit-vector cross products mix
reasonably.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

# Singular values below `max(m, n) * s0 * RANK_TOL_REL` are treated as zero
# when ranking the Jacobian (design Decision 6).
RANK_TOL_REL = 1e-10

# Every residual kind this module can emit. `tests/test_sketch_jacobian.py`
# asserts it has a central-difference case for each one, so adding a kind
# without a derivative test fails loudly.
RESIDUAL_KINDS = frozenset({
    "fixed", "coincident", "distance", "distance_x", "distance_y",
    "horizontal", "vertical", "parallel", "perpendicular", "angle",
    "point_on_line", "point_on_circle", "radius", "equal_radius", "midpoint",
    "tangent_line_circle", "tangent_point_perp", "tangent_circles",
})


class SketchError(ValueError):
    pass


# ---------------- entities ----------------
@dataclass
class _Point:
    name: str
    x0: float
    y0: float
    fixed: bool = False
    ix: int = -1  # index into free-parameter vector (x at ix, y at ix+1)


@dataclass
class _Circle:
    name: str
    center: str
    r0: float
    fixed_r: bool = False
    ir: int = -1


@dataclass
class _Line:
    name: str
    p1: str
    p2: str


# ---------------- references ----------------
class PointRef:
    """Resolves a handle to a point value, its gradient and its param slots.

    Two implementations today (free and fixed); arcs/ellipses will add derived
    handles (``arc1.end``) whose ``accum`` chain-rules through
    ``{cx, cy, r, theta}`` — which is the whole reason constraints are written
    against this indirection rather than against point names.
    """

    __slots__ = ("name", "params")

    def value(self, v: np.ndarray) -> tuple[float, float]:
        raise NotImplementedError

    def accum(self, v: np.ndarray, J: np.ndarray, row: int,
              dfdx: float, dfdy: float) -> None:
        """Add d(residual)/d(param) for a residual with these x/y partials."""
        raise NotImplementedError


class _FreePoint(PointRef):
    __slots__ = ("ix",)

    def __init__(self, name: str, ix: int) -> None:
        self.name, self.ix, self.params = name, ix, (ix, ix + 1)

    def value(self, v):
        return v[self.ix], v[self.ix + 1]

    def accum(self, v, J, row, dfdx, dfdy):
        J[row, self.ix] += dfdx
        J[row, self.ix + 1] += dfdy


class _FixedPoint(PointRef):
    __slots__ = ("xy",)

    def __init__(self, name: str, x: float, y: float) -> None:
        self.name, self.xy, self.params = name, (x, y), ()

    def value(self, v):
        return self.xy

    def accum(self, v, J, row, dfdx, dfdy):
        return  # contributes no columns


class ScalarRef:
    """The radius half of the same idea."""

    __slots__ = ("name", "params")

    def value(self, v: np.ndarray) -> float:
        raise NotImplementedError

    def accum(self, v: np.ndarray, J: np.ndarray, row: int, d: float) -> None:
        raise NotImplementedError


class _FreeScalar(ScalarRef):
    __slots__ = ("ix",)

    def __init__(self, name: str, ix: int) -> None:
        self.name, self.ix, self.params = name, ix, (ix,)

    def value(self, v):
        return v[self.ix]

    def accum(self, v, J, row, d):
        J[row, self.ix] += d


class _FixedScalar(ScalarRef):
    __slots__ = ("val",)

    def __init__(self, name: str, val: float) -> None:
        self.name, self.val, self.params = name, val, ()

    def value(self, v):
        return self.val

    def accum(self, v, J, row, d):
        return


# ---------------- the residual IR ----------------
@dataclass(frozen=True, slots=True)
class Residual:
    """One compiled block of residual rows.

    `f(v)` returns a sequence of `rows` floats; `df(v, J, row0)` **adds** its
    partial derivatives into `J[row0:row0+rows, params]` and must touch no
    other column (`J` is zeroed before each assembly, and `df` accumulates
    with `+=` so a residual that names the same point twice is correct).
    """

    con_index: int
    kind: str
    rows: int
    params: tuple[int, ...]
    f: Callable[[np.ndarray], Sequence[float]]
    df: Callable[[np.ndarray, np.ndarray, int], None]
    origin: str | None = field(default=None)

    def __post_init__(self) -> None:
        if self.rows < 1:
            raise SketchError(f"residual {self.kind!r} must contribute >= 1 row")
        if not callable(self.f) or not callable(self.df):
            raise SketchError(
                f"residual {self.kind!r} must ship both a value function and an "
                "analytic derivative: a missing df silently reintroduces the "
                "finite-difference Jacobian (92% of the v1 solve time)")


def _unit(ax: float, ay: float, bx: float, by: float):
    """Unit direction a->b and the segment length (guarded)."""
    dx, dy = bx - ax, by - ay
    n = math.hypot(dx, dy) or 1e-12
    return dx / n, dy / n, n


def _accum_dir(v, J, row, ra: PointRef, rb: PointRef,
               ux: float, uy: float, n: float, dfdux: float, dfduy: float):
    """Chain d(residual)/d(unit direction) back onto the line's endpoints.

    For ``u = (b - a) / |b - a|`` the Jacobian of the normalization is
    ``(I - u u^T) / n``, so the endpoint partials are that matrix applied to
    ``(dfdux, dfduy)``, negated for `a`.
    """
    t = dfdux * ux + dfduy * uy
    gx = (dfdux - ux * t) / n
    gy = (dfduy - uy * t) / n
    rb.accum(v, J, row, gx, gy)
    ra.accum(v, J, row, -gx, -gy)


class Sketch:
    """Entities and constraints, compiled to a residual IR at declaration time.

    Parameter slots are assigned when an entity is declared (2 per free point,
    1 per free radius). `solve_sketch` declares all points before all circles,
    so the packing matches the v1 solver's; nothing observable depends on the
    order.
    """

    def __init__(self) -> None:
        self.points: dict[str, _Point] = {}
        self.lines: dict[str, _Line] = {}
        self.circles: dict[str, _Circle] = {}
        self.residuals: list[Residual] = []
        self.n_res = 0
        self.n_par = 0
        self.con_types: list[str] = []   # spec-order constraint types
        self._refs: dict[str, PointRef] = {}
        self._rads: dict[str, ScalarRef] = {}
        self._con_index = -1

    # ---------------- entities ----------------
    def point(self, name: str, x0: float, y0: float, fixed: bool = False) -> str:
        if name in self.points:
            raise SketchError(f"duplicate point {name}")
        p = _Point(name, float(x0), float(y0), bool(fixed))
        if p.fixed:
            self._refs[name] = _FixedPoint(name, p.x0, p.y0)
        else:
            p.ix = self.n_par
            self.n_par += 2
            self._refs[name] = _FreePoint(name, p.ix)
        self.points[name] = p
        return name

    def line(self, name: str, p1: str, p2: str) -> str:
        self._need_point(p1), self._need_point(p2)
        self.lines[name] = _Line(name, p1, p2)
        return name

    def circle(self, name: str, center: str, r0: float, fixed_r: bool = False) -> str:
        self._need_point(center)
        c = _Circle(name, center, float(r0), bool(fixed_r))
        if c.fixed_r:
            self._rads[name] = _FixedScalar(name, c.r0)
        else:
            c.ir = self.n_par
            self.n_par += 1
            self._rads[name] = _FreeScalar(name, c.ir)
        self.circles[name] = c
        return name

    def _need_point(self, n: str) -> None:
        if n not in self.points:
            raise SketchError(f"unknown point {n}")

    def _need_line(self, n: str) -> None:
        if n not in self.lines:
            raise SketchError(f"unknown line {n}")

    def _need_circle(self, n: str) -> None:
        if n not in self.circles:
            raise SketchError(f"unknown circle {n}")

    # ---------------- compilation helpers ----------------
    def _begin(self, ctype: str) -> int:
        """Open the next spec-order constraint; returns its `con_index`."""
        self._con_index += 1
        self.con_types.append(ctype)
        return self._con_index

    def _add(self, res: Residual) -> None:
        self.residuals.append(res)
        self.n_res += res.rows

    def _line_refs(self, ln: str) -> tuple[PointRef, PointRef]:
        line = self.lines[ln]
        return self._refs[line.p1], self._refs[line.p2]

    def _circle_refs(self, c: str) -> tuple[PointRef, ScalarRef]:
        return self._refs[self.circles[c].center], self._rads[c]

    @staticmethod
    def _params(*refs) -> tuple[int, ...]:
        seen: dict[int, None] = {}
        for ref in refs:
            for ix in ref.params:
                seen[ix] = None
        return tuple(seen)

    # ---------------- constraints ----------------
    def fixed(self, p: str, x: float, y: float) -> None:
        self._need_point(p)
        rp = self._refs[p]
        x, y = float(x), float(y)

        def f(v):
            px, py = rp.value(v)
            return (px - x, py - y)

        def df(v, J, r):
            rp.accum(v, J, r, 1.0, 0.0)
            rp.accum(v, J, r + 1, 0.0, 1.0)

        self._add(Residual(self._begin("fixed"), "fixed", 2,
                           self._params(rp), f, df))

    def coincident(self, p: str, q: str) -> None:
        self._need_point(p), self._need_point(q)
        rp, rq = self._refs[p], self._refs[q]

        def f(v):
            px, py = rp.value(v)
            qx, qy = rq.value(v)
            return (px - qx, py - qy)

        def df(v, J, r):
            rp.accum(v, J, r, 1.0, 0.0)
            rq.accum(v, J, r, -1.0, 0.0)
            rp.accum(v, J, r + 1, 0.0, 1.0)
            rq.accum(v, J, r + 1, 0.0, -1.0)

        self._add(Residual(self._begin("coincident"), "coincident", 2,
                           self._params(rp, rq), f, df))

    def distance(self, p: str, q: str, d: float) -> None:
        self._need_point(p), self._need_point(q)
        rp, rq = self._refs[p], self._refs[q]
        d = float(d)

        def f(v):
            px, py = rp.value(v)
            qx, qy = rq.value(v)
            return (math.hypot(px - qx, py - qy) - d,)

        def df(v, J, r):
            px, py = rp.value(v)
            qx, qy = rq.value(v)
            ux, uy, _ = _unit(qx, qy, px, py)
            rp.accum(v, J, r, ux, uy)
            rq.accum(v, J, r, -ux, -uy)

        self._add(Residual(self._begin("distance"), "distance", 1,
                           self._params(rp, rq), f, df))

    def distance_x(self, p: str, q: str, d: float) -> None:
        self._need_point(p), self._need_point(q)
        rp, rq = self._refs[p], self._refs[q]
        d = float(d)

        def f(v):
            return (rq.value(v)[0] - rp.value(v)[0] - d,)

        def df(v, J, r):
            rq.accum(v, J, r, 1.0, 0.0)
            rp.accum(v, J, r, -1.0, 0.0)

        self._add(Residual(self._begin("distance_x"), "distance_x", 1,
                           self._params(rp, rq), f, df))

    def distance_y(self, p: str, q: str, d: float) -> None:
        self._need_point(p), self._need_point(q)
        rp, rq = self._refs[p], self._refs[q]
        d = float(d)

        def f(v):
            return (rq.value(v)[1] - rp.value(v)[1] - d,)

        def df(v, J, r):
            rq.accum(v, J, r, 0.0, 1.0)
            rp.accum(v, J, r, 0.0, -1.0)

        self._add(Residual(self._begin("distance_y"), "distance_y", 1,
                           self._params(rp, rq), f, df))

    def horizontal(self, ln: str) -> None:
        self._need_line(ln)
        ra, rb = self._line_refs(ln)

        def f(v):
            return (rb.value(v)[1] - ra.value(v)[1],)

        def df(v, J, r):
            rb.accum(v, J, r, 0.0, 1.0)
            ra.accum(v, J, r, 0.0, -1.0)

        self._add(Residual(self._begin("horizontal"), "horizontal", 1,
                           self._params(ra, rb), f, df))

    def vertical(self, ln: str) -> None:
        self._need_line(ln)
        ra, rb = self._line_refs(ln)

        def f(v):
            return (rb.value(v)[0] - ra.value(v)[0],)

        def df(v, J, r):
            rb.accum(v, J, r, 1.0, 0.0)
            ra.accum(v, J, r, -1.0, 0.0)

        self._add(Residual(self._begin("vertical"), "vertical", 1,
                           self._params(ra, rb), f, df))

    def parallel(self, l1: str, l2: str) -> None:
        self._need_line(l1), self._need_line(l2)
        ra, rb = self._line_refs(l1)
        rc, rd = self._line_refs(l2)

        def f(v):
            ux, uy, _ = _unit(*ra.value(v), *rb.value(v))
            vx, vy, _ = _unit(*rc.value(v), *rd.value(v))
            return (ux * vy - uy * vx,)

        def df(v, J, r):
            ux, uy, n1 = _unit(*ra.value(v), *rb.value(v))
            vx, vy, n2 = _unit(*rc.value(v), *rd.value(v))
            _accum_dir(v, J, r, ra, rb, ux, uy, n1, vy, -vx)
            _accum_dir(v, J, r, rc, rd, vx, vy, n2, -uy, ux)

        self._add(Residual(self._begin("parallel"), "parallel", 1,
                           self._params(ra, rb, rc, rd), f, df))

    def perpendicular(self, l1: str, l2: str) -> None:
        self._need_line(l1), self._need_line(l2)
        ra, rb = self._line_refs(l1)
        rc, rd = self._line_refs(l2)

        def f(v):
            ux, uy, _ = _unit(*ra.value(v), *rb.value(v))
            vx, vy, _ = _unit(*rc.value(v), *rd.value(v))
            return (ux * vx + uy * vy,)

        def df(v, J, r):
            ux, uy, n1 = _unit(*ra.value(v), *rb.value(v))
            vx, vy, n2 = _unit(*rc.value(v), *rd.value(v))
            _accum_dir(v, J, r, ra, rb, ux, uy, n1, vx, vy)
            _accum_dir(v, J, r, rc, rd, vx, vy, n2, ux, uy)

        self._add(Residual(self._begin("perpendicular"), "perpendicular", 1,
                           self._params(ra, rb, rc, rd), f, df))

    def angle(self, l1: str, l2: str, deg: float) -> None:
        """Angle from l1 direction to l2 direction, CCW degrees."""
        self._need_line(l1), self._need_line(l2)
        ra, rb = self._line_refs(l1)
        rc, rd = self._line_refs(l2)
        want = math.radians(float(deg))

        def f(v):
            ux, uy, _ = _unit(*ra.value(v), *rb.value(v))
            vx, vy, _ = _unit(*rc.value(v), *rd.value(v))
            err = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy) - want
            # wrap to (-pi, pi]
            return ((err + math.pi) % (2 * math.pi) - math.pi,)

        def df(v, J, r):
            # theta = atan2(v) - atan2(u); the wrap is piecewise constant.
            ux, uy, n1 = _unit(*ra.value(v), *rb.value(v))
            vx, vy, n2 = _unit(*rc.value(v), *rd.value(v))
            _accum_dir(v, J, r, ra, rb, ux, uy, n1, uy, -ux)
            _accum_dir(v, J, r, rc, rd, vx, vy, n2, -vy, vx)

        self._add(Residual(self._begin("angle"), "angle", 1,
                           self._params(ra, rb, rc, rd), f, df))

    def point_on_line(self, p: str, ln: str) -> None:
        self._need_point(p), self._need_line(ln)
        self._on_line(self._begin("point_on_line"), p, ln)

    def _on_line(self, ci: int, p: str, ln: str) -> None:
        rp = self._refs[p]
        ra, rb = self._line_refs(ln)

        def f(v):
            ax, ay = ra.value(v)
            ux, uy, _ = _unit(ax, ay, *rb.value(v))
            px, py = rp.value(v)
            return ((px - ax) * uy - (py - ay) * ux,)

        def df(v, J, r):
            ax, ay = ra.value(v)
            ux, uy, n = _unit(ax, ay, *rb.value(v))
            px, py = rp.value(v)
            wx, wy = px - ax, py - ay
            rp.accum(v, J, r, uy, -ux)
            ra.accum(v, J, r, -uy, ux)
            _accum_dir(v, J, r, ra, rb, ux, uy, n, -wy, wx)

        self._add(Residual(ci, "point_on_line", 1,
                           self._params(rp, ra, rb), f, df))

    def point_on_circle(self, p: str, c: str) -> None:
        self._need_point(p), self._need_circle(c)
        self._on_circle(self._begin("point_on_circle"), p, c)

    def _on_circle(self, ci: int, p: str, c: str) -> None:
        rp = self._refs[p]
        rc, rr = self._circle_refs(c)

        def f(v):
            px, py = rp.value(v)
            cx, cy = rc.value(v)
            return (math.hypot(px - cx, py - cy) - rr.value(v),)

        def df(v, J, r):
            ux, uy, _ = _unit(*rc.value(v), *rp.value(v))
            rp.accum(v, J, r, ux, uy)
            rc.accum(v, J, r, -ux, -uy)
            rr.accum(v, J, r, -1.0)

        self._add(Residual(ci, "point_on_circle", 1,
                           self._params(rp, rc, rr), f, df))

    def radius(self, c: str, r: float) -> None:
        self._need_circle(c)
        _, rr = self._circle_refs(c)
        want = float(r)

        def f(v):
            return (rr.value(v) - want,)

        def df(v, J, row):
            rr.accum(v, J, row, 1.0)

        self._add(Residual(self._begin("radius"), "radius", 1,
                           self._params(rr), f, df))

    def equal_radius(self, c1: str, c2: str) -> None:
        self._need_circle(c1), self._need_circle(c2)
        _, r1 = self._circle_refs(c1)
        _, r2 = self._circle_refs(c2)

        def f(v):
            return (r1.value(v) - r2.value(v),)

        def df(v, J, row):
            r1.accum(v, J, row, 1.0)
            r2.accum(v, J, row, -1.0)

        self._add(Residual(self._begin("equal_radius"), "equal_radius", 1,
                           self._params(r1, r2), f, df))

    def midpoint(self, p: str, ln: str) -> None:
        self._need_point(p), self._need_line(ln)
        rp = self._refs[p]
        ra, rb = self._line_refs(ln)

        def f(v):
            px, py = rp.value(v)
            ax, ay = ra.value(v)
            bx, by = rb.value(v)
            return (px - (ax + bx) / 2, py - (ay + by) / 2)

        def df(v, J, r):
            rp.accum(v, J, r, 1.0, 0.0)
            ra.accum(v, J, r, -0.5, 0.0)
            rb.accum(v, J, r, -0.5, 0.0)
            rp.accum(v, J, r + 1, 0.0, 1.0)
            ra.accum(v, J, r + 1, 0.0, -0.5)
            rb.accum(v, J, r + 1, 0.0, -0.5)

        self._add(Residual(self._begin("midpoint"), "midpoint", 2,
                           self._params(rp, ra, rb), f, df))

    def tangent_line_circle(self, ln: str, c: str, at: str | None = None) -> None:
        """Line tangent to circle. If `at` is given, that point is the tangency
        point: it lies on the circle, on the line, and center->at is
        perpendicular to the line (3 residuals). Otherwise just
        dist(center, line) == r (1 residual, unsigned)."""
        self._need_line(ln), self._need_circle(c)
        ci = self._begin("tangent_line_circle")
        ra, rb = self._line_refs(ln)
        rc, rr = self._circle_refs(c)
        if at is None:
            def f(v):
                ax, ay = ra.value(v)
                ux, uy, _ = _unit(ax, ay, *rb.value(v))
                cx, cy = rc.value(v)
                return (abs((cx - ax) * uy - (cy - ay) * ux) - rr.value(v),)

            def df(v, J, r):
                ax, ay = ra.value(v)
                ux, uy, n = _unit(ax, ay, *rb.value(v))
                cx, cy = rc.value(v)
                wx, wy = cx - ax, cy - ay
                s = 1.0 if wx * uy - wy * ux >= 0.0 else -1.0
                rc.accum(v, J, r, s * uy, -s * ux)
                ra.accum(v, J, r, -s * uy, s * ux)
                _accum_dir(v, J, r, ra, rb, ux, uy, n, -s * wy, s * wx)
                rr.accum(v, J, r, -1.0)

            self._add(Residual(ci, "tangent_line_circle", 1,
                               self._params(ra, rb, rc, rr), f, df))
            return

        self._need_point(at)
        self._on_circle(ci, at, c)
        self._on_line(ci, at, ln)
        rt = self._refs[at]

        def f(v):
            ux, uy, _ = _unit(*ra.value(v), *rb.value(v))
            tx, ty = rt.value(v)
            cx, cy = rc.value(v)
            return ((tx - cx) * ux + (ty - cy) * uy,)

        def df(v, J, r):
            ux, uy, n = _unit(*ra.value(v), *rb.value(v))
            tx, ty = rt.value(v)
            cx, cy = rc.value(v)
            rt.accum(v, J, r, ux, uy)
            rc.accum(v, J, r, -ux, -uy)
            _accum_dir(v, J, r, ra, rb, ux, uy, n, tx - cx, ty - cy)

        self._add(Residual(ci, "tangent_point_perp", 1,
                           self._params(ra, rb, rc, rt), f, df))

    def tangent_circles(self, c1: str, c2: str, kind: str = "external") -> None:
        self._need_circle(c1), self._need_circle(c2)
        ra, r1 = self._circle_refs(c1)
        rb, r2 = self._circle_refs(c2)
        sign = 1.0 if kind == "external" else -1.0

        def f(v):
            ax, ay = ra.value(v)
            bx, by = rb.value(v)
            return (math.hypot(ax - bx, ay - by)
                    - (r1.value(v) + sign * r2.value(v)),)

        def df(v, J, r):
            ux, uy, _ = _unit(*rb.value(v), *ra.value(v))
            ra.accum(v, J, r, ux, uy)
            rb.accum(v, J, r, -ux, -uy)
            r1.accum(v, J, r, -1.0)
            r2.accum(v, J, r, -sign)

        self._add(Residual(self._begin("tangent_circles"), "tangent_circles", 1,
                           self._params(ra, rb, r1, r2), f, df))

    # ---------------- assembly ----------------
    def initial_vector(self) -> np.ndarray:
        """The starting parameter vector, in slot order."""
        x0 = np.zeros(self.n_par)
        for p in self.points.values():
            if not p.fixed:
                x0[p.ix], x0[p.ix + 1] = p.x0, p.y0
        for c in self.circles.values():
            if not c.fixed_r:
                x0[c.ir] = c.r0
        return x0

    def _row_offsets(self) -> list[int]:
        offsets, row = [], 0
        for res in self.residuals:
            offsets.append(row)
            row += res.rows
        return offsets

    def make_functions(self):
        """`(fun, jac)` over the compiled residuals.

        `jac` fills one preallocated dense array — dense is measured fast
        enough through 400 parameters, and it keeps `numpy.linalg` (and the
        SVD the rank analysis needs) in play with no `scipy.sparse` plumbing.
        """
        residuals = self.residuals
        offsets = self._row_offsets()
        n_res, n_par = self.n_res, self.n_par
        jac_buf = np.zeros((n_res, n_par))

        def fun(v):
            # A fresh array every call: least_squares holds on to the previous
            # residual vector across an iteration and would compare it against
            # itself if we handed out one buffer.
            out = np.empty(n_res)
            for res, row in zip(residuals, offsets):
                out[row:row + res.rows] = res.f(v)
            return out

        def jac(v):
            jac_buf.fill(0.0)
            for res, row in zip(residuals, offsets):
                res.df(v, jac_buf, row)
            return jac_buf

        return fun, jac

    def rank(self, J: np.ndarray) -> int:
        """Numerical rank of the Jacobian (design Decision 6)."""
        if J.size == 0:
            return 0
        s = np.linalg.svd(J, compute_uv=False)
        if s.size == 0 or s[0] <= 0.0:
            return 0
        return int((s > max(J.shape) * s[0] * RANK_TOL_REL).sum())

    # ---------------- solve ----------------
    def solve(self, tol: float = 1e-10, max_nfev: int = 2000) -> dict:
        t0 = time.perf_counter()
        n_par, n_res = self.n_par, self.n_res
        x0 = self.initial_vector()
        fun, jac = self.make_functions()

        if n_par == 0 or n_res == 0:
            # Nothing to solve: least_squares rejects an empty problem, and
            # "no free parameters" is a legitimate (fully fixed) sketch.
            xs = x0
            max_err = float(np.max(np.abs(fun(xs)))) if n_res else 0.0
            success, nfev = True, 0
        else:
            res = least_squares(fun, x0, jac=jac, method="trf",
                                xtol=tol, ftol=tol, gtol=tol, max_nfev=max_nfev)
            xs = res.x
            max_err = float(np.max(np.abs(res.fun))) if n_res else 0.0
            success, nfev = bool(res.success), int(res.nfev)

        rank = self.rank(jac(xs)) if (n_par and n_res) else 0
        t1 = time.perf_counter()
        out_pts = {}
        for name in self.points:
            x, y = self._refs[name].value(xs)
            out_pts[name] = {"x": float(x), "y": float(y)}
        out_circ = {}
        for name, c in self.circles.items():
            cx, cy = self._refs[c.center].value(xs)
            out_circ[name] = {"cx": float(cx), "cy": float(cy),
                              "r": float(self._rads[name].value(xs))}
        return {
            "ok": success and max_err < 1e-7,
            "max_residual": max_err,
            "n_params": n_par,
            "n_residuals": n_res,
            "rank": rank,
            # n_params - rank(J), never n_params - n_residuals: the row count
            # reports a NEGATIVE dof for any redundant constraint.
            "dof": n_par - rank,
            "nfev": nfev,
            "solve_ms": (t1 - t0) * 1e3,
            "points": out_pts,
            "circles": out_circ,
        }


# ---------------- JSON front-end (agent tool shape) ----------------
def solve_sketch(spec: dict) -> dict:
    """Solve a sketch from a JSON-shaped spec.

    spec = {
      "points":  [{"name","x","y","fixed"?}, ...],
      "lines":   [{"name","p1","p2"}, ...],
      "circles": [{"name","center","r","fixed_r"?}, ...],
      "constraints": [{"type": <name>, ...kwargs}, ...]
    }
    """
    sk = Sketch()
    for p in spec.get("points", []):
        sk.point(p["name"], p["x"], p["y"], p.get("fixed", False))
    for l in spec.get("lines", []):
        sk.line(l["name"], l["p1"], l["p2"])
    for c in spec.get("circles", []):
        sk.circle(c["name"], c["center"], c["r"], c.get("fixed_r", False))
    dispatch = {
        "fixed": sk.fixed, "coincident": sk.coincident, "distance": sk.distance,
        "distance_x": sk.distance_x, "distance_y": sk.distance_y,
        "horizontal": sk.horizontal, "vertical": sk.vertical,
        "parallel": sk.parallel, "perpendicular": sk.perpendicular,
        "angle": sk.angle, "point_on_line": sk.point_on_line,
        "point_on_circle": sk.point_on_circle, "radius": sk.radius,
        "equal_radius": sk.equal_radius, "midpoint": sk.midpoint,
        "tangent_line_circle": sk.tangent_line_circle,
        "tangent_circles": sk.tangent_circles,
    }
    for c in spec.get("constraints", []):
        kw = {k: v for k, v in c.items() if k != "type"}
        try:
            fn = dispatch[c["type"]]
        except KeyError:
            raise SketchError(f"unknown constraint type {c.get('type')!r}; "
                              f"known: {sorted(dispatch)}")
        fn(**kw)
    return sk.solve()
