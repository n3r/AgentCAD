"""Minimal 2D sketch constraint solver on scipy.optimize.least_squares.

Prototype of what would become agentcad/toolkit/sketch.py.

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

Solve: least_squares (default 'lm' for small dense systems). Residuals are
scaled so lengths and unit-vector cross products mix reasonably.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares


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


class SketchError(ValueError):
    pass


class Sketch:
    def __init__(self) -> None:
        self.points: dict[str, _Point] = {}
        self.lines: dict[str, _Line] = {}
        self.circles: dict[str, _Circle] = {}
        self.residuals: list = []  # list of (callable(get) -> [float, ...])
        self.n_res = 0

    # ---------------- entities ----------------
    def point(self, name: str, x0: float, y0: float, fixed: bool = False) -> str:
        if name in self.points:
            raise SketchError(f"duplicate point {name}")
        self.points[name] = _Point(name, float(x0), float(y0), fixed)
        return name

    def line(self, name: str, p1: str, p2: str) -> str:
        self._need_point(p1), self._need_point(p2)
        self.lines[name] = _Line(name, p1, p2)
        return name

    def circle(self, name: str, center: str, r0: float, fixed_r: bool = False) -> str:
        self._need_point(center)
        self.circles[name] = _Circle(name, center, float(r0), fixed_r)
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

    # ---------------- constraint helpers ----------------
    def _add(self, k: int, fn) -> None:
        self.residuals.append(fn)
        self.n_res += k

    def _line_pts(self, ln: str):
        l = self.lines[ln]
        return l.p1, l.p2

    # ---------------- constraints ----------------
    def fixed(self, p: str, x: float, y: float) -> None:
        self._need_point(p)
        self._add(2, lambda g: (g(p)[0] - x, g(p)[1] - y))

    def coincident(self, p: str, q: str) -> None:
        self._need_point(p), self._need_point(q)
        self._add(2, lambda g: (g(p)[0] - g(q)[0], g(p)[1] - g(q)[1]))

    def distance(self, p: str, q: str, d: float) -> None:
        self._need_point(p), self._need_point(q)
        self._add(1, lambda g: (math.hypot(g(p)[0] - g(q)[0], g(p)[1] - g(q)[1]) - d,))

    def distance_x(self, p: str, q: str, d: float) -> None:
        self._add(1, lambda g: (g(q)[0] - g(p)[0] - d,))

    def distance_y(self, p: str, q: str, d: float) -> None:
        self._add(1, lambda g: (g(q)[1] - g(p)[1] - d,))

    def horizontal(self, ln: str) -> None:
        self._need_line(ln)
        a, b = self._line_pts(ln)
        self._add(1, lambda g: (g(b)[1] - g(a)[1],))

    def vertical(self, ln: str) -> None:
        self._need_line(ln)
        a, b = self._line_pts(ln)
        self._add(1, lambda g: (g(b)[0] - g(a)[0],))

    @staticmethod
    def _uvec(g, a, b):
        dx, dy = g(b)[0] - g(a)[0], g(b)[1] - g(a)[1]
        n = math.hypot(dx, dy) or 1e-12
        return dx / n, dy / n

    def parallel(self, l1: str, l2: str) -> None:
        self._need_line(l1), self._need_line(l2)
        a, b = self._line_pts(l1)
        c, d = self._line_pts(l2)
        def f(g):
            u, v = self._uvec(g, a, b), self._uvec(g, c, d)
            return (u[0] * v[1] - u[1] * v[0],)
        self._add(1, f)

    def perpendicular(self, l1: str, l2: str) -> None:
        self._need_line(l1), self._need_line(l2)
        a, b = self._line_pts(l1)
        c, d = self._line_pts(l2)
        def f(g):
            u, v = self._uvec(g, a, b), self._uvec(g, c, d)
            return (u[0] * v[0] + u[1] * v[1],)
        self._add(1, f)

    def angle(self, l1: str, l2: str, deg: float) -> None:
        """Angle from l1 direction to l2 direction, CCW degrees."""
        self._need_line(l1), self._need_line(l2)
        a, b = self._line_pts(l1)
        c, d = self._line_pts(l2)
        def f(g):
            u, v = self._uvec(g, a, b), self._uvec(g, c, d)
            cross = u[0] * v[1] - u[1] * v[0]
            dot = u[0] * v[0] + u[1] * v[1]
            err = math.atan2(cross, dot) - math.radians(deg)
            # wrap to (-pi, pi]
            err = (err + math.pi) % (2 * math.pi) - math.pi
            return (err,)
        self._add(1, f)

    def point_on_line(self, p: str, ln: str) -> None:
        self._need_point(p), self._need_line(ln)
        a, b = self._line_pts(ln)
        def f(g):
            ax, ay = g(a); bx, by = g(b); px, py = g(p)
            dx, dy = bx - ax, by - ay
            n = math.hypot(dx, dy) or 1e-12
            return (((px - ax) * dy - (py - ay) * dx) / n,)
        self._add(1, f)

    def point_on_circle(self, p: str, c: str) -> None:
        self._need_point(p), self._need_circle(c)
        ctr = self.circles[c].center
        def f(g):
            px, py = g(p); cx, cy = g(ctr)
            return (math.hypot(px - cx, py - cy) - g.radius(c),)
        self._add(1, f)

    def radius(self, c: str, r: float) -> None:
        self._need_circle(c)
        self._add(1, lambda g: (g.radius(c) - r,))

    def equal_radius(self, c1: str, c2: str) -> None:
        self._need_circle(c1), self._need_circle(c2)
        self._add(1, lambda g: (g.radius(c1) - g.radius(c2),))

    def midpoint(self, p: str, ln: str) -> None:
        self._need_point(p), self._need_line(ln)
        a, b = self._line_pts(ln)
        self._add(2, lambda g: (g(p)[0] - (g(a)[0] + g(b)[0]) / 2,
                                g(p)[1] - (g(a)[1] + g(b)[1]) / 2))

    def tangent_line_circle(self, ln: str, c: str, at: str | None = None) -> None:
        """Line tangent to circle. If `at` is given, that point is the tangency
        point: it lies on the circle, on the line, and center->at is
        perpendicular to the line (3 residuals). Otherwise just
        dist(center, line) == r (1 residual, unsigned)."""
        self._need_line(ln), self._need_circle(c)
        a, b = self._line_pts(ln)
        ctr = self.circles[c].center
        if at is None:
            def f(g):
                ax, ay = g(a); bx, by = g(b); cx, cy = g(ctr)
                dx, dy = bx - ax, by - ay
                n = math.hypot(dx, dy) or 1e-12
                dist = abs((cx - ax) * dy - (cy - ay) * dx) / n
                return (dist - g.radius(c),)
            self._add(1, f)
        else:
            self._need_point(at)
            self.point_on_circle(at, c)
            self.point_on_line(at, ln)
            def f(g):
                ax, ay = g(a); bx, by = g(b); tx, ty = g(at); cx, cy = g(ctr)
                dx, dy = bx - ax, by - ay
                n = math.hypot(dx, dy) or 1e-12
                return (((tx - cx) * dx + (ty - cy) * dy) / n,)
            self._add(1, f)

    def tangent_circles(self, c1: str, c2: str, kind: str = "external") -> None:
        self._need_circle(c1), self._need_circle(c2)
        a, b = self.circles[c1].center, self.circles[c2].center
        sign = 1.0 if kind == "external" else -1.0
        def f(g):
            d = math.hypot(g(a)[0] - g(b)[0], g(a)[1] - g(b)[1])
            return (d - (g.radius(c1) + sign * g.radius(c2)),)
        self._add(1, f)

    # ---------------- solve ----------------
    def solve(self, tol: float = 1e-10, max_nfev: int = 2000) -> dict:
        t0 = time.perf_counter()
        # pack free parameters
        x0: list[float] = []
        for p in self.points.values():
            if not p.fixed:
                p.ix = len(x0)
                x0 += [p.x0, p.y0]
        for c in self.circles.values():
            if not c.fixed_r:
                c.ir = len(x0)
                x0.append(c.r0)
        x0 = np.array(x0, dtype=float)

        pts, circs = self.points, self.circles

        class _Get:
            __slots__ = ("v",)
            def __init__(self, v): self.v = v
            def __call__(self, name):
                p = pts[name]
                return (p.x0, p.y0) if p.fixed else (self.v[p.ix], self.v[p.ix + 1])
            def radius(self, name):
                c = circs[name]
                return c.r0 if c.fixed_r else self.v[c.ir]

        def fun(v):
            g = _Get(v)
            out = []
            for r in self.residuals:
                out.extend(r(g))
            return out

        n_par, n_res = len(x0), self.n_res
        method = "lm" if n_res >= n_par else "trf"
        res = least_squares(fun, x0, method=method, xtol=tol, ftol=tol, gtol=tol,
                            max_nfev=max_nfev)
        t1 = time.perf_counter()
        max_err = float(np.max(np.abs(res.fun))) if n_res else 0.0
        g = _Get(res.x)
        out_pts = {n: {"x": g(n)[0], "y": g(n)[1]} for n in pts}
        out_circ = {n: {"cx": g(circs[n].center)[0], "cy": g(circs[n].center)[1],
                        "r": g.radius(n)} for n in circs}
        return {
            "ok": bool(res.success) and max_err < 1e-7,
            "max_residual": max_err,
            "n_params": n_par,
            "n_residuals": n_res,
            "dof": n_par - n_res,
            "nfev": int(res.nfev),
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
