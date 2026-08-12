"""2D sketch constraint solver — a typed residual IR with analytic Jacobians.

Runs in the **server** process (`core/tools_sketch.py` imports it) as well as
in part scripts, so it must never import build123d/OCP. numpy and scipy are
declared dependencies of this package for exactly that reason.

Entities:
  point  (x, y)            -- free or fixed
  line   (p1, p2)          -- reference to two points (no own params)
  circle (center, r)       -- center point ref + radius param (free or fixed)
  arc    (center, r, t1, t2) -- centre point ref + 3 own params; its endpoints
                             are the **virtual handles** `<name>.start` and
                             `<name>.end`
  spline (points)          -- an ordered list of named points, degree 3,
                             non-periodic; no params of its own
  slot   (c1, c2, width)   -- compiled at ingestion into two arcs + two lines
                             sharing one radius param

Constraint vocabulary v1:
  fixed, coincident, distance, distance_x, distance_y,
  horizontal, vertical, parallel, perpendicular, angle,
  point_on_line, point_on_circle, radius, equal_radius,
  tangent (line-circle, with optional tangency point; circle-circle
  external/internal), midpoint.

Added in PRD-009 slice 5:
  tangent (one name, dispatched over the pair's kinds), symmetric,
  equal_length, concentric — and `radius`, `equal_radius`, `point_on_circle`,
  `tangent_line_circle`, `tangent_circles` now accept arcs, because an arc's
  radius is a radius.

## Arcs and virtual handles

An arc owns exactly three parameters (`r`, `theta1`, `theta2`; two when
`fixed_r`), plus the two its centre point owns if that point is new. Its
endpoints are **derived**: `arc1.start` and `arc1.end` are names that resolve
through `PointRef` to `(cx + r cos t, cy + r sin t)` and to a gradient
chain-ruled over `{cx, cy, r, theta}`. So `coincident {p: "arc1.end",
q: "p3"}` is the same two rows as any other coincidence, and the whole v1
vocabulary applies to arc endpoints with **no extra parameters and no extra
residuals** (design Decision 3b). The rejected alternative — free endpoint
points tied back by residuals — costs 7 parameters per arc and puts machinery
the user never wrote into every conflict report.

Angles are **degrees in the spec, radians in the parameter vector, and never
wrapped mid-solve**: the sweep is `theta2 - theta1` however many turns that
is, and normalization happens on output only. Wrapping a parameter is a
discontinuity in the Jacobian and is how an arc jumps the long way round
during a drag.

A dot in a name is the solver's namespace, not the caller's: virtual handles
(`arc1.end`) and compiled sub-entities (`slot1.arc_a`, an authored 3-point
arc's `a1.center`) live behind it, so a user entity containing a dot is a
`SketchError` rather than a silent rebinding. Sub-entities may be *referenced*
from a constraint; they may not be *declared*.

**Tangency has two residual forms, and the choice is not cosmetic.** When the
tangency point is a point of its own, tangency is `dist(centre, line) - r`.
When the line's endpoint *is* the arc's own virtual handle — a closed chain,
or a slot's side — that point is already on both curves structurally, and the
remaining condition is that the radius meets the line square. Measured: with
the distance form in that position, sliding the junction moves *both* line
endpoints along the line, so the row is second-order flat and the Jacobian is
**rank-deficient at the solution** (a slot reported rank 1 of 5, `dof 4`, and
its own name in `free_entities`). `_shared_endpoint` detects the case and uses
the perpendicular form: same row count, `dof 0`, and on a 50-entity ring of
arcs and lines the warm solve went 11.5 ms -> 6.1 ms (nfev 7 -> 4) with
`max_residual` 3.6e-8 -> 2.8e-14.

## Splines and slots (PRD-009 slice 6)

A **spline** is an ordered list of named `point` entities, so every point
constraint applies to its control points for free. Measured (slice 6 spike):
build123d's `Spline` interpolates that point list to **7.1e-15 mm**, far
inside the 1e-8 mm emission tolerance, so the solver's through-point model is
the emitted curve — `Bezier`, the documented fallback, misses by up to 9.8 mm
and was not needed. Its **end tangent** is a different matter: a free-end
`Spline` sits up to **44.6 deg** away from the first control-polygon leg, so
`tangent {a: "sp1.start", b: "ln4"}` holds on the emitted curve only if the
emitter passes `tangents=` (measured to pin the direction to 7.1e-15 deg while
still interpolating). The result payload carries `end_tangent` and the solved
directions for exactly that. **On-curve point constraints are out of scope.**

A **slot** compiles at ingestion into two arcs and two lines. Its two caps
share **one radius parameter** (equal-radius is structural, never a row) and
its four junctions are structural too (each side line is built on the caps'
handles), so it contributes exactly five rows: `radius = width/2` and four
tangencies. Every one of them carries the slot's own `con_index` and
`origin: "slot:<name>"`, and the slot's *caller-visible* index is `None` —
there is no entry of `spec["constraints"]` to point at, and a diagnostic never
blames a constraint the user did not write.

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

## Diagnostics

Every solve returns a `diagnostics` block (design Decision 6/7):

- `rank` comes from the SVD of the Jacobian and **`dof = n_params - rank`**,
  never `n_params - n_residuals` (the row count reports a *negative* dof for
  any redundant constraint).
- `free_entities` is read off the null space, so an under-constrained sketch
  says *which* entities can still move rather than only how many DOF remain.
- The dependent set is found by **declaration-order greedy forward
  selection**, not by column-pivoted QR. Measured (design spec, Decision 6):
  pivoted QR blamed an innocent *original* `vertical` constraint in 2 of 3
  cases, because column pivoting selects by column norm — an artifact of
  residual scaling, not of intent. Greedy in declaration order was correct 4
  of 4, because it blames the *later* constraint, which is the one the user
  just added. `tests/test_sketch_diagnostics.py` pins both behaviours so a
  "simplification" back to QR fails loudly.
- A dependent row satisfied at the solution is **redundant**; a violated one
  is **conflicting**. `over_constrained` alone is *not* an error — only a
  non-empty `conflicting` set is (`core/tools_sketch.py` holds that contract).
- The greedy pass is bounded by `ANALYSIS_BUDGET_MS`; exhausting it yields
  `analysis_complete: false` with the two sets **omitted**. "We did not look"
  is never rendered as "nothing found".

## `initial`

`spec["initial"]` seeds the starting parameter vector — it **selects the
solution branch**, and it is not the speed mechanism (measured: the v1 solver
cost 20 ms seeded exactly at the solution and 51 ms seeded 0.4 mm away; the
Jacobian was always the cost). It can never change the spec: it cannot fix a
point, cannot override `fixed_r` and cannot introduce an entity. An unknown
name is an error; a stale or partial `initial` degrades to a cold start with
`warm_started: false` and an `initial_incomplete` warning.

## `drag` (PRD-009 slice 8)

`spec["drag"] = {point, x, y, weight?}` compiles to a **weighted soft residual
block appended after the constraint rows**, and it is an **objective, not a
constraint**: excluded from `ok`, `max_residual`, `n_residuals`, `rank`, `dof`
and `diagnostics`, every one of which is computed over `[:n_res]`. Measured,
counting it makes every drag of a fully-constrained entity report `ok: false`
with `max_residual` 2.43 over a 48 mm drag — a verdict about the cursor.

The two halves are separate and both are needed. Seeding is `initial`, from the
**previous frame's solution**; the cursor enters only through the objective.
Measured on the mirror triangle (a, b pinned, c held by two distances, two
solutions at `(23.4375, +-18.7265)`): seeding c *at the cursor* flips the branch
the moment the cursor crosses the boundary, while the weak pull seeded from the
previous frame holds `+18.7265` through a sweep to `y = -30`.

The soft pull is a compromise, so a frame ends with one **constraint-only
re-solve seeded at the drag's answer** (`_settle`). Without it a
fully-constrained point lands `w^2` of the drag distance off its constraints
(measured 0.170 mm, `max_residual` 0.104) and `ok` would be false for the
honest reason that the coordinates really are off. With it the point returns to
where its constraints put it — which is what dragging a fully-constrained
entity should do — and it costs nothing when the drag moved only free DOF,
because the constraint rows are already satisfied there.

Diagnostics stay **off the drag path**: `analyze` is cached against a hash of
the compiled residual structure *and the constraint targets*, and
`spec["diagnostics"] in {"auto", "full", "cached"}` chooses. `auto` recomputes
except on a drag frame. `diagnostics_source` reports which block you got, so a
cached measurement is never presented as a fresh one.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import least_squares

# Singular values below `max(m, n) * s0 * RANK_TOL_REL` are treated as zero
# when ranking the Jacobian (design Decision 6).
RANK_TOL_REL = 1e-10

# A residual row is kept by the greedy forward selection when the part of it
# orthogonal to the rows declared *before* it is this large relative to the
# row's own norm; below it, the row adds no rank and is dependent.
GREEDY_TOL_REL = 1e-8

# A dependent row whose residual is this small at the solution is redundant
# (measured on a duplicate `distance`: max|f| = 3.6e-18); a larger one is
# conflicting (measured on a contradictory `distance`: 2.50).
SATISFIED_TOL = 1e-7

# FR5's documented time budget for the dependent-set analysis. Measured cost
# is well under it below ~300 constraints; exhausting it degrades to
# `analysis_complete: false` with the sets omitted, never to a silent "none".
ANALYSIS_BUDGET_MS = 50.0

# A parameter slot counts as free when its column of the null-space basis is
# this large relative to the largest such column.
NULLSPACE_TOL_REL = 1e-6

# Every residual kind this module can emit. `tests/test_sketch_jacobian.py`
# asserts it has a central-difference case for each one, so adding a kind
# without a derivative test fails loudly.
RESIDUAL_KINDS = frozenset({
    "fixed", "coincident", "distance", "distance_x", "distance_y",
    "horizontal", "vertical", "parallel", "perpendicular", "angle",
    "point_on_line", "point_on_circle", "radius", "equal_radius", "midpoint",
    "tangent_line_circle", "tangent_point_perp", "tangent_circles",
    "symmetric", "equal_length",
})

# Entity names are the user's namespace; dotted names are the solver's. A
# virtual handle (`arc1.end`) and a compiled sub-entity (`slot1.arc_a`) both
# live behind a dot, so a user entity may not contain one — a collision there
# would silently rebind a handle rather than fail.
RESERVED_NAME_CHAR = "."

# Sentinel for "this constraint's caller-visible index is its declaration
# index" — distinct from an explicit `None`, which means "compiled, the caller
# never wrote it".
_AUTO_INDEX = object()

# The weight of a drag's soft pull, relative to constraint rows scaled to
# millimetres. Measured (design Decision 9d, the mirror-flip probe): at 0.05 a
# 48 mm drag of a fully-constrained point never flips the branch and leaves the
# point where its constraints put it, while seeding the point *at the cursor* —
# the naive "warm start from the on-screen state" — flips it the moment the
# cursor crosses the branch boundary.
DRAG_WEIGHT = 0.05

# What `diagnostics` may ask for. `auto` recomputes, except on a drag frame,
# where the constraint set cannot have changed.
DIAGNOSTICS_MODES = ("auto", "full", "cached")

# Diagnostics are a function of the compiled residual *structure*, and a drag
# frame changes no constraints, so a frame can serve the previous block instead
# of paying ~6.4 ms for the greedy dependent-set pass (design Decision 9c: this
# is what turns an ~8 ms frame into ~1.5 ms). The cache is module-level because
# the route is stateless — every frame compiles a fresh `Sketch`.
DIAG_CACHE_MAX = 32
_DIAG_CACHE: dict[str, dict] = {}


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


@dataclass
class _Arc:
    """Centre point + `r`, `theta1`, `theta2` — three own parameters, never 7.

    Angles are **degrees in the spec and radians in `t1_0`/`t2_0`**, and they
    are never wrapped: the sweep is `t2 - t1` however many turns that is, and
    normalization happens on output only.
    """

    name: str
    center: str
    r0: float
    t1_0: float
    t2_0: float
    fixed_r: bool = False
    ir: int = -1
    i1: int = -1
    i2: int = -1
    # how the caller wrote it, so slice 7's emitter can pick ThreePointArc
    authored: str = "center"
    three_point: tuple | None = None
    # the slot that compiled this arc, if any: it owns the parameter slots for
    # reporting, and it is what `initial` seeds instead of the arc
    owner: str | None = None


@dataclass
class _Spline:
    """An ordered list of named `point` entities. Degree 3, non-periodic.

    It owns **no parameters**: its control points are ordinary points, so
    every existing point constraint works on them for free. `<name>.start`
    and `<name>.end` alias the first and last point.

    Measured (slice 6 spike): build123d's `Spline` interpolates this point
    list to 7.1e-15 mm, far inside the 1e-8 mm emission tolerance, so the
    solver's through-point model **is** the emitted curve's geometry. Its
    free-end *tangent*, however, is up to 44.6 deg away from the first
    control-polygon leg, so `end_tangent` records which ends a `tangent`
    constraint pinned; the emitter must pass `tangents=` for those (measured
    to hold the direction to 7.1e-15 deg and still interpolate).
    """

    name: str
    points: tuple[str, ...]
    end_tangent: dict[str, bool] = field(
        default_factory=lambda: {"start": False, "end": False})


@dataclass
class _Slot:
    """A slot, compiled at ingestion into two arcs and two lines.

    The two arcs share **one radius parameter**, so equal-radius is
    structural: it is not a residual row and can never appear in a conflict
    report. The four junctions are structural too — each side line is built
    directly on the arcs' virtual handles — so the only rows a slot
    contributes are `radius = width/2` and the four line-arc tangencies.
    """

    name: str
    c1: str
    c2: str
    width: float
    ir: int = -1                       # the shared radius parameter slot
    con_index: int = -1
    r_seed: float | None = None        # from `initial`; else width / 2


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


class _ArcEndPoint(PointRef):
    """A **virtual handle**: `arc1.start` / `arc1.end` (design Decision 3b).

    ``(x, y) = (cx + r cos t, cy + r sin t)``, so it owns **no parameters of
    its own** — it chain-rules a residual's `(dfdx, dfdy)` back onto the
    centre's slots, the radius slot and the angle slot. That is what lets
    `coincident {p: "arc1.end", q: "p3"}` be exactly the same two rows as any
    other coincidence, instead of 4 extra parameters and 4 extra residuals
    that would show up in every conflict report as machinery the user never
    wrote.
    """

    __slots__ = ("center", "radius", "it")

    def __init__(self, name: str, center: PointRef, radius: ScalarRef,
                 it: int) -> None:
        self.name, self.center, self.radius, self.it = name, center, radius, it
        self.params = tuple(dict.fromkeys(center.params + radius.params + (it,)))

    def value(self, v):
        cx, cy = self.center.value(v)
        r, t = self.radius.value(v), v[self.it]
        return cx + r * math.cos(t), cy + r * math.sin(t)

    def accum(self, v, J, row, dfdx, dfdy):
        r, t = self.radius.value(v), v[self.it]
        ct, st = math.cos(t), math.sin(t)
        self.center.accum(v, J, row, dfdx, dfdy)
        self.radius.accum(v, J, row, dfdx * ct + dfdy * st)
        J[row, self.it] += r * (dfdy * ct - dfdx * st)


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


def _circumcircle(a, b, c) -> tuple[float, float, float]:
    """Centre and radius through three points; collinear points are an error."""
    (ax, ay), (bx, by), (cx, cy) = a, b, c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    scale = max(abs(ax), abs(ay), abs(bx), abs(by), abs(cx), abs(cy), 1.0)
    if abs(d) <= 1e-12 * scale * scale:
        raise SketchError(
            "three-point arc needs three non-collinear points; "
            f"{a}, {b}, {c} are collinear")
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


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
        self.arcs: dict[str, _Arc] = {}
        self.splines: dict[str, _Spline] = {}
        self.slots: dict[str, _Slot] = {}
        self.residuals: list[Residual] = []
        self.n_res = 0
        self.n_par = 0
        self.con_types: list[str] = []   # spec-order constraint types
        # The index a *caller* can look up for each constraint. It is the
        # declaration index for anything the caller wrote, and **None** for a
        # constraint compiled from an entity (a slot's internal machinery),
        # because there is no entry of `spec["constraints"]` to point at.
        # `parse_sketch` sets it explicitly so entity-compiled constraints —
        # which are declared first — cannot shift the user's indices.
        self.con_report: list[int | None] = []
        # parameter slot -> the entity that owns it, so a null-space vector can
        # be reported as "p7, c3" rather than as a list of column indices.
        self.slot_owner: list[str] = []
        self.warm_started = False
        self.warnings: list[dict] = []
        # The drag objective (slice 8). It is **not** in `self.residuals`: it
        # is excluded from `ok`, `max_residual`, `n_residuals`, the rank, the
        # DOF and the diagnostics, so it must not be in the structure every
        # one of those is computed from.
        self.drag_res: Residual | None = None
        self.n_drag = 0
        self.drag_info: dict | None = None
        self.diagnostics_mode = "auto"
        # The constraints as the caller declared them. `parse_sketch` fills
        # this in; it is what makes the diagnostics cache key distinguish a
        # duplicate `distance 50` (redundant) from a contradictory `distance
        # 60` (conflicting) — two specs with an identical residual structure
        # and opposite verdicts. A `Sketch` built by hand leaves it None, and
        # then nothing is cached.
        self.con_args: list[dict] | None = None
        self._refs: dict[str, PointRef] = {}
        self._rads: dict[str, ScalarRef] = {}
        self._con_index = -1
        self._spec_index: object = _AUTO_INDEX
        self._origin: str | None = None

    # ---------------- entities ----------------
    def _claim(self, name: str, *, internal: bool = False) -> str:
        """Reserve an entity name across the one shared namespace.

        Circles and arcs share `_rads`, so they share a namespace; and a
        dotted name is the solver's, not the caller's (`arc1.end`,
        `slot1.arc_a`), so a user entity may never contain a dot.
        """
        if not isinstance(name, str) or not name:
            raise SketchError("entity name must be a non-empty string")
        if not internal and RESERVED_NAME_CHAR in name:
            raise SketchError(
                f"entity name {name!r} contains {RESERVED_NAME_CHAR!r}, which "
                "is reserved for virtual handles (arc1.end) and compiled "
                "sub-entities (slot1.arc_a)")
        if (name in self.points or name in self.lines or name in self.circles
                or name in self.arcs or name in self.splines
                or name in self.slots):
            raise SketchError(f"duplicate entity {name}")
        return name

    def point(self, name: str, x0: float, y0: float, fixed: bool = False, *,
              internal: bool = False) -> str:
        self._claim(name, internal=internal)
        p = _Point(name, float(x0), float(y0), bool(fixed))
        if p.fixed:
            self._refs[name] = _FixedPoint(name, p.x0, p.y0)
        else:
            p.ix = self.n_par
            self.n_par += 2
            self.slot_owner += [name, name]
            self._refs[name] = _FreePoint(name, p.ix)
        self.points[name] = p
        return name

    def line(self, name: str, p1: str, p2: str, *, internal: bool = False) -> str:
        self._claim(name, internal=internal)
        self._pref(p1), self._pref(p2)
        self.lines[name] = _Line(name, p1, p2)
        return name

    def circle(self, name: str, center: str, r0: float, fixed_r: bool = False, *,
               internal: bool = False) -> str:
        self._claim(name, internal=internal)
        self._need_point(center)
        c = _Circle(name, center, float(r0), bool(fixed_r))
        if c.fixed_r:
            self._rads[name] = _FixedScalar(name, c.r0)
        else:
            c.ir = self.n_par
            self.n_par += 1
            self.slot_owner.append(name)
            self._rads[name] = _FreeScalar(name, c.ir)
        self.circles[name] = c
        self._refs[f"{name}.center"] = self._refs[center]
        return name

    def arc(self, name: str, center: str, r0: float, start_deg: float,
            end_deg: float, fixed_r: bool = False, *, internal: bool = False,
            radius_ref: ScalarRef | None = None, authored: str = "center",
            three_point: tuple | None = None, owner: str | None = None) -> str:
        """An arc: a centre point, a radius and two angles (**degrees here**).

        Costs exactly 3 parameters — 2 when `fixed_r`, 0 extra when the caller
        supplies a shared `radius_ref` (which is how a slot's two arcs share
        one radius, making equal-radius structural rather than a constraint
        row). Its endpoints are the virtual handles `<name>.start` /
        `<name>.end`; `<name>.center` resolves to the centre point.
        """
        self._claim(name, internal=internal)
        self._need_point(center)
        a = _Arc(name, center, float(r0), math.radians(float(start_deg)),
                 math.radians(float(end_deg)), bool(fixed_r),
                 authored=authored, three_point=three_point)
        a.owner = owner
        slot_name = owner or name     # what `free_entities` should call it
        if radius_ref is not None:
            self._rads[name] = radius_ref
            a.fixed_r = True          # not this arc's parameter to seed
        elif a.fixed_r:
            self._rads[name] = _FixedScalar(name, a.r0)
        else:
            a.ir = self.n_par
            self.n_par += 1
            self.slot_owner.append(slot_name)
            self._rads[name] = _FreeScalar(name, a.ir)
        a.i1, a.i2 = self.n_par, self.n_par + 1
        self.n_par += 2
        self.slot_owner += [slot_name, slot_name]
        self.arcs[name] = a
        centre_ref, radius = self._refs[center], self._rads[name]
        self._refs[f"{name}.center"] = centre_ref
        self._refs[f"{name}.start"] = _ArcEndPoint(
            f"{name}.start", centre_ref, radius, a.i1)
        self._refs[f"{name}.end"] = _ArcEndPoint(
            f"{name}.end", centre_ref, radius, a.i2)
        return name

    def arc_three_point(self, name: str, start, mid, end, *,
                        internal: bool = False) -> str:
        """The 3-point authoring form, compiled to the centre form at ingestion.

        The circumcentre becomes a compiled point `<name>.center` and the
        sweep is unwrapped so it passes through `mid`; `authored` records the
        form so slice 7's emitter can write `ThreePointArc`.
        """
        self._claim(name, internal=internal)
        pts = tuple((float(p[0]), float(p[1])) for p in (start, mid, end))
        cx, cy, r = _circumcircle(*pts)
        t1 = math.atan2(pts[0][1] - cy, pts[0][0] - cx)
        tm = math.atan2(pts[1][1] - cy, pts[1][0] - cx)
        t2 = math.atan2(pts[2][1] - cy, pts[2][0] - cx)
        two_pi = 2 * math.pi
        dm, de = (tm - t1) % two_pi, (t2 - t1) % two_pi
        t2 = t1 + (de if dm < de else de - two_pi)
        centre = self.point(f"{name}.center", cx, cy, internal=True)
        return self.arc(name, centre, r, math.degrees(t1), math.degrees(t2),
                        internal=True, authored="three_point", three_point=pts)

    def spline(self, name: str, points: Sequence[str], *,
               internal: bool = False) -> str:
        """An ordered list of named points, degree 3, non-periodic.

        The points are ordinary `point` entities, so every point constraint
        works on them for free; the spline itself owns no parameters and
        contributes no residuals. **On-curve point constraints are out of
        scope** (design Decision 3 / the PRD's own risk entry) — a point is
        either one of the spline's own control points or it is unconstrained
        by the curve.
        """
        self._claim(name, internal=internal)
        pts = tuple(points)
        if len(pts) < 2:
            raise SketchError(
                f"spline {name!r} needs at least 2 points, got {len(pts)}")
        for pt in pts:
            self._need_point(pt)
        self.splines[name] = _Spline(name, pts)
        self._refs[f"{name}.start"] = self._refs[pts[0]]
        self._refs[f"{name}.end"] = self._refs[pts[-1]]
        return name

    def slot(self, name: str, c1: str, c2: str, width: float, *,
             internal: bool = False) -> str:
        """Compile a slot into two arcs, two lines and their auto-constraints.

        **One shared radius parameter** for both arcs (equal-radius is
        structural, never a row) and **structural junctions** (each side line
        is built on the arcs' virtual handles, so the four coincidences are
        not rows either). What is left is what a slot actually asserts:
        `radius = width/2` and four line-arc tangencies — 5 rows against the
        5 parameters the slot owns, so a slot with free centres has exactly
        the 4 DOF a hand count gives it (position 2, orientation 1, length 1).

        Every compiled residual carries the slot's own `con_index` and
        `origin: "slot:<name>"`, and the slot's caller-visible index is
        `None`: **a diagnostic never blames a constraint the user did not
        write**, and there is no entry of `constraints` to point at.
        """
        self._claim(name, internal=internal)
        self._need_point(c1), self._need_point(c2)
        width = float(width)
        if width <= 0.0:
            raise SketchError(f"slot {name!r} needs a positive width, got {width}")
        p1, p2 = self.points[c1], self.points[c2]
        span = math.hypot(p2.x0 - p1.x0, p2.y0 - p1.y0)
        if span <= 1e-9:
            raise SketchError(
                f"slot {name!r} needs two distinct centres; {c1!r} and {c2!r} "
                "start at the same coordinates")

        slot = _Slot(name, c1, c2, width)
        slot.ir = self.n_par
        self.n_par += 1
        self.slot_owner.append(name)
        shared_r = _FreeScalar(f"{name}.r", slot.ir)
        self._rads[f"{name}.r"] = shared_r

        a_deg, b_deg = self._slot_arc_angles(p1, p2)
        self.arc(f"{name}.arc_a", c1, width / 2, *a_deg, internal=True,
                 radius_ref=shared_r, owner=name)
        self.arc(f"{name}.arc_b", c2, width / 2, *b_deg, internal=True,
                 radius_ref=shared_r, owner=name)
        self.line(f"{name}.side_1", f"{name}.arc_a.end", f"{name}.arc_b.start",
                  internal=True)
        self.line(f"{name}.side_2", f"{name}.arc_b.end", f"{name}.arc_a.start",
                  internal=True)

        prev_origin, self._origin = self._origin, f"slot:{name}"
        prev_index, self._spec_index = self._spec_index, None
        try:
            ci = self._begin("slot")
            slot.con_index = ci
            self._radius_ref(ci, shared_r, width / 2)
            for side in (f"{name}.side_1", f"{name}.side_2"):
                for cap in (f"{name}.arc_a", f"{name}.arc_b"):
                    self._tangent_line_curve(ci, side, cap)
        finally:
            self._origin, self._spec_index = prev_origin, prev_index
        self.slots[name] = slot
        return name

    @staticmethod
    def _slot_arc_angles(p1: _Point, p2: _Point):
        """Starting angles for the two caps, from the centre-line direction.

        Derived rather than authored, and **re-derived after `initial` seeds
        the centres**, so a client never has to send a slot's internal angles.
        """
        phi = math.atan2(p2.y0 - p1.y0, p2.x0 - p1.x0)
        d = math.degrees(phi)
        return (d + 90.0, d + 270.0), (d - 90.0, d + 90.0)

    def _pref(self, n: str) -> PointRef:
        """Resolve a point handle: a point name, or a virtual handle."""
        ref = self._refs.get(n)
        if ref is None:
            known = sorted(self._refs)
            shown = known[:12] + (["..."] if len(known) > 12 else [])
            raise SketchError(
                f"unknown point handle {n!r}; known: {shown}")
        return ref

    def _need_point(self, n: str) -> None:
        if n not in self.points:
            raise SketchError(f"unknown point {n}")

    def _need_line(self, n: str) -> None:
        if n not in self.lines:
            raise SketchError(f"unknown line {n}")

    def _need_circle(self, n: str) -> None:
        """A radius-carrying curve: a circle or an arc (a radius is a radius)."""
        if n not in self.circles and n not in self.arcs:
            raise SketchError(f"unknown circle or arc {n}")

    # ---------------- compilation helpers ----------------
    def _begin(self, ctype: str) -> int:
        """Open the next spec-order constraint; returns its `con_index`."""
        self._con_index += 1
        self.con_types.append(ctype)
        self.con_report.append(
            self._con_index if self._spec_index is _AUTO_INDEX
            else self._spec_index)
        return self._con_index

    def _add(self, res: Residual) -> None:
        if self._origin is not None and res.origin is None:
            # Stamped here rather than threaded through every helper, so a
            # compiled entity's rows carry their provenance even when they are
            # built by the same code path a user constraint uses.
            res = replace(res, origin=self._origin)
        self.residuals.append(res)
        self.n_res += res.rows

    def _line_refs(self, ln: str) -> tuple[PointRef, PointRef]:
        line = self.lines[ln]
        return self._refs[line.p1], self._refs[line.p2]

    def _circle_refs(self, c: str) -> tuple[PointRef, ScalarRef]:
        """Centre and radius of a circle **or an arc** — the tangency,
        radius, equal-radius and concentric residuals are identical for both.
        """
        curve = self.circles.get(c) or self.arcs.get(c)
        if curve is None:
            raise SketchError(f"unknown circle or arc {c}")
        return self._refs[curve.center], self._rads[c]

    def _kind_of(self, name: str) -> str:
        """What a `tangent` argument refers to (design Decision 4's dispatch)."""
        if name in self.lines:
            return "line"
        if name in self.circles:
            return "circle"
        if name in self.arcs:
            return "arc"
        base, _, attr = name.rpartition(RESERVED_NAME_CHAR)
        if attr in ("start", "end") and base in self.splines:
            return "spline_end"
        if name in self.splines:
            raise SketchError(
                f"a spline is tangent at its ends, not as a whole: write "
                f"{name + '.start'!r} or {name + '.end'!r}")
        raise SketchError(
            f"unknown curve {name!r}; known lines {sorted(self.lines)}, "
            f"circles {sorted(self.circles)}, arcs {sorted(self.arcs)}, "
            f"splines {sorted(self.splines)}")

    @staticmethod
    def _params(*refs) -> tuple[int, ...]:
        seen: dict[int, None] = {}
        for ref in refs:
            for ix in ref.params:
                seen[ix] = None
        return tuple(seen)

    # ---------------- constraints ----------------
    def fixed(self, p: str, x: float, y: float) -> None:
        rp = self._pref(p)
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
        self._coincident(self._begin("coincident"), self._pref(p),
                         self._pref(q))

    def _coincident(self, ci: int, rp: PointRef, rq: PointRef) -> None:
        """Two rows, whatever the handles are: `concentric` and every arc
        junction reuse this rather than growing a residual kind each."""

        def f(v):
            px, py = rp.value(v)
            qx, qy = rq.value(v)
            return (px - qx, py - qy)

        def df(v, J, r):
            rp.accum(v, J, r, 1.0, 0.0)
            rq.accum(v, J, r, -1.0, 0.0)
            rp.accum(v, J, r + 1, 0.0, 1.0)
            rq.accum(v, J, r + 1, 0.0, -1.0)

        self._add(Residual(ci, "coincident", 2, self._params(rp, rq), f, df))

    def distance(self, p: str, q: str, d: float) -> None:
        rp, rq = self._pref(p), self._pref(q)
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
        rp, rq = self._pref(p), self._pref(q)
        d = float(d)

        def f(v):
            return (rq.value(v)[0] - rp.value(v)[0] - d,)

        def df(v, J, r):
            rq.accum(v, J, r, 1.0, 0.0)
            rp.accum(v, J, r, -1.0, 0.0)

        self._add(Residual(self._begin("distance_x"), "distance_x", 1,
                           self._params(rp, rq), f, df))

    def distance_y(self, p: str, q: str, d: float) -> None:
        rp, rq = self._pref(p), self._pref(q)
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
        self._parallel_refs(self._begin("parallel"), ra, rb, rc, rd)

    def _parallel_refs(self, ci: int, ra: PointRef, rb: PointRef,
                       rc: PointRef, rd: PointRef) -> None:
        """Zero cross product of two unit directions — the residual behind
        `parallel` and behind a spline's end tangency."""

        def f(v):
            ux, uy, _ = _unit(*ra.value(v), *rb.value(v))
            vx, vy, _ = _unit(*rc.value(v), *rd.value(v))
            return (ux * vy - uy * vx,)

        def df(v, J, r):
            ux, uy, n1 = _unit(*ra.value(v), *rb.value(v))
            vx, vy, n2 = _unit(*rc.value(v), *rd.value(v))
            _accum_dir(v, J, r, ra, rb, ux, uy, n1, vy, -vx)
            _accum_dir(v, J, r, rc, rd, vx, vy, n2, -uy, ux)

        self._add(Residual(ci, "parallel", 1,
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
        self._need_line(ln)
        self._on_line(self._begin("point_on_line"), p, ln)

    def _on_line(self, ci: int, p: str, ln: str) -> None:
        rp = self._pref(p)
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
        self._need_circle(c)
        self._on_circle(self._begin("point_on_circle"), p, c)

    def _on_circle(self, ci: int, p: str, c: str) -> None:
        rp = self._pref(p)
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
        self._radius_ref(self._begin("radius"), rr, float(r))

    def _radius_ref(self, ci: int, rr: ScalarRef, want: float) -> None:

        def f(v):
            return (rr.value(v) - want,)

        def df(v, J, row):
            rr.accum(v, J, row, 1.0)

        self._add(Residual(ci, "radius", 1, self._params(rr), f, df))

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
        self._need_line(ln)
        rp = self._pref(p)
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
        """Line tangent to a circle **or an arc** (v1 name, kept for ever).

        If `at` is given, that point is the tangency point: it lies on the
        circle, on the line, and centre->at is perpendicular to the line (3
        residuals). Otherwise just dist(centre, line) == r (1 residual,
        unsigned).
        """
        self._tangent_line_curve(self._begin("tangent_line_circle"), ln, c, at)

    def _tangent_line_curve(self, ci: int, ln: str, c: str,
                            at: str | None = None) -> None:
        self._need_line(ln), self._need_circle(c)
        ra, rb = self._line_refs(ln)
        rc, rr = self._circle_refs(c)
        if at is None:
            at = self._shared_endpoint(ln, c)
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

        if self._shared_endpoint(ln, c) != at:
            # The tangency point is a point of its own: say so, in rows.
            self._on_circle(ci, at, c)
            self._on_line(ci, at, ln)
        self._tangent_perp(ci, ln, c, at)

    def _shared_endpoint(self, ln: str, c: str) -> str | None:
        """The line endpoint that *is* one of the arc's virtual handles.

        When a chain closes on `arc1.end`, that point is already on the arc
        and already on the line **structurally**, and the honest residual for
        the remaining condition is the perpendicularity of the radius — not
        `dist(centre, line) - r`.

        This is not a nicety. Measured: with the distance form, sliding the
        junction along the arc moves *both* line endpoints along the line, so
        the residual is second-order flat in every angle it touches and the
        Jacobian is **rank-deficient at the solution**. A slot built that way
        reports `rank 1` out of 5 and `dof 4` with its own name in
        `free_entities`; the perpendicular form reports `dof 0`, which is the
        truth. Same row count either way.
        """
        line = self.lines[ln]
        for handle in (line.p1, line.p2):
            base, _, attr = handle.rpartition(RESERVED_NAME_CHAR)
            if base == c and attr in ("start", "end"):
                return handle
        return None

    def _tangent_perp(self, ci: int, ln: str, c: str, at: str) -> None:
        """`(at - centre) . u_line == 0` — the radius meets the line square."""
        ra, rb = self._line_refs(ln)
        rc, _ = self._circle_refs(c)
        rt = self._pref(at)

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
        """Circle/arc tangent to circle/arc (v1 name, kept for ever)."""
        self._tangent_curves(self._begin("tangent_circles"), c1, c2, kind)

    def _tangent_curves(self, ci: int, c1: str, c2: str,
                        kind: str = "external") -> None:
        self._need_circle(c1), self._need_circle(c2)
        if kind not in ("external", "internal"):
            raise SketchError(
                f"tangent kind must be 'external' or 'internal', not {kind!r}")
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

        self._add(Residual(ci, "tangent_circles", 1,
                           self._params(ra, rb, r1, r2), f, df))

    # ---------------- generalized constraints (slice 5) ----------------
    def tangent(self, a: str, b: str, at: str | None = None,
                kind: str = "external") -> None:
        """One constraint with a dispatch table over the pair's kinds.

        `tangent_line_circle` and `tangent_circles` stay registered under their
        own names for ever — this is a new front door onto the same residuals,
        not a rename — but it is the one an agent or the GUI should write,
        because it does not have to know which of the two curves is the line.
        """
        ci = self._begin("tangent")
        ka, kb = self._kind_of(a), self._kind_of(b)
        radial = ("circle", "arc")
        if ka == "spline_end" and kb == "line":
            self._tangent_spline_end(ci, a, b)
        elif kb == "spline_end" and ka == "line":
            self._tangent_spline_end(ci, b, a)
        elif ka == "line" and kb in radial:
            self._tangent_line_curve(ci, a, b, at)
        elif kb == "line" and ka in radial:
            self._tangent_line_curve(ci, b, a, at)
        elif ka in radial and kb in radial:
            if at is not None:
                raise SketchError(
                    "`at` names the tangency point of a line-curve tangency; "
                    f"{a!r} and {b!r} are both curves")
            self._tangent_curves(ci, a, b, kind)
        else:
            raise SketchError(
                f"tangent cannot constrain {ka} {a!r} to {kb} {b!r}; supported "
                "pairs are line+circle, line+arc and circle/arc+circle/arc")

    def _tangent_spline_end(self, ci: int, handle: str, ln: str) -> None:
        """A spline's end tangent, as a direction residual against the first
        (or last) leg of its control polygon.

        Measured (slice 6 spike): a build123d `Spline` left to its own end
        conditions leaves that direction up to **44.6 deg** away from the leg,
        so this constraint means what it says only if the emitter passes
        `tangents=` — which pins it to 7.1e-15 deg while still interpolating
        every point to 7.3e-15 mm. `end_tangent` records the ends that need
        it. **On-curve tangency anywhere else on the spline is out of scope.**
        """
        self._need_line(ln)
        base, _, which = handle.rpartition(RESERVED_NAME_CHAR)
        sp = self.splines[base]
        leg = (sp.points[0], sp.points[1]) if which == "start" \
            else (sp.points[-2], sp.points[-1])
        sp.end_tangent[which] = True
        ra, rb = self._line_refs(ln)
        self._parallel_refs(ci, ra, rb, self._pref(leg[0]), self._pref(leg[1]))

    def symmetric(self, a: str, b: str, about: str) -> None:
        """Mirror symmetry of two points, or of two lines, about a line.

        **Two rows per point pair, not one**: the midpoint of `ab` lies on the
        axis *and* `ab` is perpendicular to it. The midpoint row alone looks
        right on a rectangle and is wrong on everything else.

        For a line pair the endpoints are paired **in declaration order**
        (`a.p1` with `b.p1`, `a.p2` with `b.p2`), so a line drawn in the
        opposite direction mirrors its ends the way it was written.
        """
        self._need_line(about)
        ci = self._begin("symmetric")
        if a in self.lines and b in self.lines:
            la, lb = self.lines[a], self.lines[b]
            self._symmetric_points(ci, la.p1, lb.p1, about)
            self._symmetric_points(ci, la.p2, lb.p2, about)
        elif a in self.lines or b in self.lines:
            raise SketchError(
                f"symmetric needs two points or two lines, not {a!r} and {b!r}")
        else:
            self._symmetric_points(ci, a, b, about)

    def _symmetric_points(self, ci: int, p: str, q: str, about: str) -> None:
        rp, rq = self._pref(p), self._pref(q)
        rc, rd = self._line_refs(about)

        def f(v):
            px, py = rp.value(v)
            qx, qy = rq.value(v)
            cx, cy = rc.value(v)
            ux, uy, _ = _unit(cx, cy, *rd.value(v))
            mx, my = (px + qx) / 2, (py + qy) / 2
            sx, sy, _ = _unit(px, py, qx, qy)
            return ((mx - cx) * uy - (my - cy) * ux,   # midpoint on the axis
                    sx * ux + sy * uy)                 # and perpendicular to it

        def df(v, J, r):
            px, py = rp.value(v)
            qx, qy = rq.value(v)
            cx, cy = rc.value(v)
            ux, uy, n = _unit(cx, cy, *rd.value(v))
            wx, wy = (px + qx) / 2 - cx, (py + qy) / 2 - cy
            rp.accum(v, J, r, 0.5 * uy, -0.5 * ux)
            rq.accum(v, J, r, 0.5 * uy, -0.5 * ux)
            rc.accum(v, J, r, -uy, ux)
            _accum_dir(v, J, r, rc, rd, ux, uy, n, -wy, wx)
            sx, sy, ns = _unit(px, py, qx, qy)
            _accum_dir(v, J, r + 1, rp, rq, sx, sy, ns, ux, uy)
            _accum_dir(v, J, r + 1, rc, rd, ux, uy, n, sx, sy)

        self._add(Residual(ci, "symmetric", 2,
                           self._params(rp, rq, rc, rd), f, df))

    def equal_length(self, l1: str, l2: str) -> None:
        self._need_line(l1), self._need_line(l2)
        ra, rb = self._line_refs(l1)
        rc, rd = self._line_refs(l2)

        def f(v):
            ax, ay = ra.value(v)
            bx, by = rb.value(v)
            cx, cy = rc.value(v)
            dx, dy = rd.value(v)
            return (math.hypot(bx - ax, by - ay) - math.hypot(dx - cx, dy - cy),)

        def df(v, J, r):
            ux, uy, _ = _unit(*ra.value(v), *rb.value(v))
            wx, wy, _ = _unit(*rc.value(v), *rd.value(v))
            rb.accum(v, J, r, ux, uy)
            ra.accum(v, J, r, -ux, -uy)
            rd.accum(v, J, r, -wx, -wy)
            rc.accum(v, J, r, wx, wy)

        self._add(Residual(self._begin("equal_length"), "equal_length", 1,
                           self._params(ra, rb, rc, rd), f, df))

    def concentric(self, a: str, b: str) -> None:
        """Centre coincidence of two circles/arcs — 2 rows, the `coincident`
        residual on the two centre handles."""
        self._need_circle(a), self._need_circle(b)
        ra, _ = self._circle_refs(a)
        rb, _ = self._circle_refs(b)
        self._coincident(self._begin("concentric"), ra, rb)

    # ---------------- the drag objective ----------------
    def drag(self, point: str, x: float, y: float,
             weight: float | None = None) -> None:
        """A weighted soft pull of `point` toward the cursor (design 9d).

        **This is an objective, not a constraint.** It occupies its own
        weighted block *after* the constraint rows and is excluded from every
        reported quantity: `ok`, `max_residual`, `n_residuals`, `rank`, `dof`
        and the whole `diagnostics` block. Measured, including it makes every
        drag of a fully-constrained entity report `ok: false` with
        `max_residual` climbing to 2.43 (= weight x a 48 mm drag) — a verdict
        about the cursor, not about the sketch.

        Seeding is the other half and it belongs to `initial`: every parameter
        starts at the **previous frame's solution**, never at the cursor.
        Measured, seeding the dragged point at the cursor is not a warm start
        that happens to flip the branch — it is what *causes* the flip, because
        the on-screen state includes the cursor and the cursor crossed the
        branch boundary.
        """
        if self.drag_res is not None:
            raise SketchError("only one drag block per solve")
        rp = self._pref(point)
        if not rp.params:
            raise SketchError(
                f"cannot drag {point!r}: it has no free parameters (it is "
                "fixed, or derived entirely from fixed entities)")
        x, y = float(x), float(y)
        w = DRAG_WEIGHT if weight is None else float(weight)
        if not (w > 0.0) or not math.isfinite(w):
            raise SketchError(f"drag weight must be finite and positive, got {w}")

        def f(v):
            px, py = rp.value(v)
            return (w * (px - x), w * (py - y))

        def df(v, J, r):
            rp.accum(v, J, r, w, 0.0)
            rp.accum(v, J, r + 1, 0.0, w)

        self.drag_res = Residual(-1, "drag", 2, self._params(rp), f, df)
        self.n_drag = 2
        self.drag_info = {"point": point, "x": x, "y": y, "weight": w}

    # ---------------- diagnostics cache ----------------
    def structure_key(self) -> str | None:
        """A hash of the compiled residual structure and the constraint targets.

        Two frames of one drag hash the same: a drag changes `initial` and the
        cursor, and neither is in here. A changed constraint — including one
        whose *target* moved, which is the difference between a redundant and a
        conflicting duplicate — does not. Coordinates are deliberately absent:
        the GUI resends the whole spec every frame with its points at the last
        solution, so keying on them would miss every time.
        """
        if self.con_args is None:
            return None
        payload = {
            "n_par": self.n_par,
            "n_res": self.n_res,
            "rows": [[r.con_index, r.kind, r.rows, list(r.params), r.origin]
                     for r in self.residuals],
            "types": self.con_types,
            "report": self.con_report,
            "owners": self.slot_owner,
            "cons": self.con_args,
            # A fixed entity's value is baked into its residuals, so it is part
            # of the structure even though it is a coordinate.
            "fixed_points": sorted((n, p.x0, p.y0)
                                   for n, p in self.points.items() if p.fixed),
            "fixed_radii": sorted(
                [(n, c.r0) for n, c in self.circles.items() if c.fixed_r]
                + [(n, a.r0) for n, a in self.arcs.items()
                   if a.fixed_r and a.owner is None]),
            "slots": sorted((n, s.width) for n, s in self.slots.items()),
        }
        blob = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()

    # ---------------- warm start ----------------
    def seed(self, initial: dict | None) -> bool:
        """Seed the starting parameter vector from an `initial` block (FR4).

        `initial` **selects the solution branch** — it is not the speed
        mechanism (measured: the v1 solver cost 20 ms seeded exactly at the
        solution and 51 ms seeded 0.4 mm away; the Jacobian was the cost). It
        overrides starting values only: it cannot fix a point, cannot override
        `fixed_r` and cannot introduce an entity, so a value given for a fixed
        entity is accepted and has no effect.

        An unknown name raises — a silent ignore turns a client desync into a
        sketch that mysteriously stops warm-starting. A stale or partial
        `initial` (an entity it does not cover, or one it covers only
        halfway) **degrades to a cold start** with an `initial_incomplete`
        warning; it never raises and never seeds half a sketch.
        """
        self.warm_started = False
        if not initial:
            return False
        if not isinstance(initial, dict):
            raise SketchError("initial must be an object with 'points', "
                              "'circles', 'arcs' and/or 'slots' entries")
        known = ("arcs", "circles", "points", "slots")
        unknown_sections = set(initial) - set(known)
        if unknown_sections:
            raise SketchError(
                f"initial has unknown section(s) {sorted(unknown_sections)}; "
                f"known: {list(known)}")
        pts = initial.get("points") or {}
        circs = initial.get("circles") or {}
        arcs = initial.get("arcs") or {}
        slots = initial.get("slots") or {}
        for name in pts:
            if name not in self.points:
                raise SketchError(f"initial names unknown point {name!r}")
        for name in circs:
            if name not in self.circles:
                raise SketchError(f"initial names unknown circle {name!r}")
        for name in arcs:
            if name not in self.arcs:
                raise SketchError(f"initial names unknown arc {name!r}")
        for name in slots:
            if name not in self.slots:
                raise SketchError(f"initial names unknown slot {name!r}")

        missing: list[str] = []
        seeds: list[tuple[object, dict[str, float]]] = []
        for name, p in self.points.items():
            if p.fixed:
                continue            # not a parameter; `initial` cannot un-fix it
            given = pts.get(name)
            if not isinstance(given, dict) or given.get("x") is None \
                    or given.get("y") is None:
                missing.append(name)
                continue
            seeds.append((p, {"x0": float(given["x"]), "y0": float(given["y"])}))
        for name, c in self.circles.items():
            if c.fixed_r:
                continue            # `initial` cannot override a fixed radius
            given = circs.get(name)
            if not isinstance(given, dict) or given.get("r") is None:
                missing.append(name)
                continue
            seeds.append((c, {"r0": float(given["r"])}))
        for name, slot in self.slots.items():
            given = slots.get(name)
            if not isinstance(given, dict) or given.get("r") is None:
                missing.append(name)
                continue
            seeds.append((slot, {"r_seed": float(given["r"])}))
        for name, a in self.arcs.items():
            if a.owner is not None:
                # A slot's caps are derived: their angles are re-derived from
                # the seeded centres below, so a client never has to send a
                # compiled sub-entity's parameters.
                continue
            given = arcs.get(name)
            if not isinstance(given, dict):
                missing.append(name)
                continue
            # angles are degrees in `initial`, as they are in the spec
            want = {"t1_0": given.get("start_deg"), "t2_0": given.get("end_deg")}
            if not a.fixed_r:
                want["r0"] = given.get("r")
            if any(val is None for val in want.values()):
                missing.append(name)
                continue
            seeds.append((a, {k: (float(val) if k == "r0"
                                  else math.radians(float(val)))
                              for k, val in want.items()}))

        if missing:
            self.warnings.append({
                "code": "initial_incomplete",
                "message": ("initial does not cover " + ", ".join(sorted(missing))
                            + "; solving from the spec's own coordinates instead"),
                "entities": sorted(missing),
            })
            return False
        for entity, attrs in seeds:
            for attr, val in attrs.items():
                setattr(entity, attr, val)
        self._reseed_slots()
        self.warm_started = True
        return True

    def _reseed_slots(self) -> None:
        """Re-derive every slot's compiled geometry from the seeded centres."""
        for name, slot in self.slots.items():
            p1, p2 = self.points[slot.c1], self.points[slot.c2]
            a_deg, b_deg = self._slot_arc_angles(p1, p2)
            r = slot.width / 2 if slot.r_seed is None else slot.r_seed
            for arc_name, (d1, d2) in ((f"{name}.arc_a", a_deg),
                                       (f"{name}.arc_b", b_deg)):
                arc = self.arcs[arc_name]
                arc.t1_0, arc.t2_0 = math.radians(d1), math.radians(d2)
                arc.r0 = r

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
        for a in self.arcs.values():
            if not a.fixed_r:
                x0[a.ir] = a.r0
            x0[a.i1], x0[a.i2] = a.t1_0, a.t2_0
        for slot in self.slots.values():
            x0[slot.ir] = slot.width / 2 if slot.r_seed is None else slot.r_seed
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
        residuals = list(self.residuals)
        offsets = self._row_offsets()
        n_res, n_par = self.n_res, self.n_par
        # The drag block is appended **after** the constraint rows, so every
        # reported quantity is `[:n_res]` of what these functions return.
        if self.drag_res is not None:
            residuals.append(self.drag_res)
            offsets.append(n_res)
        m = n_res + self.n_drag
        jac_buf = np.zeros((m, n_par))

        def fun(v):
            # A fresh array every call: least_squares holds on to the previous
            # residual vector across an iteration and would compare it against
            # itself if we handed out one buffer.
            out = np.empty(m)
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

    # ---------------- diagnostics ----------------
    def row_owners(self) -> list[Residual]:
        """The `Residual` record behind each assembled row."""
        return [res for res in self.residuals for _ in range(res.rows)]

    def free_entities(self, J: np.ndarray, rank: int) -> list[str]:
        """The entities the null space can still move (design Decision 7).

        Read off the *column norms* of the null-space basis rather than off one
        singular vector at a time: any orthonormal basis of the null space
        spans the same subspace, so a per-vector reading depends on an
        arbitrary rotation while a column norm does not.
        """
        if self.n_par == 0 or rank >= self.n_par:
            return []
        if J.size == 0:
            slots = range(self.n_par)          # nothing constrains anything
        else:
            # `full_matrices` only matters when the system has fewer rows than
            # parameters; otherwise Vt is already the full n x n basis.
            vt = np.linalg.svd(J, full_matrices=J.shape[0] < J.shape[1])[2]
            null = vt[rank:]
            if null.size == 0:
                return []
            col = np.linalg.norm(null, axis=0)
            thresh = max(NULLSPACE_TOL_REL * float(col.max()), 1e-12)
            slots = [i for i in range(self.n_par) if col[i] > thresh]
        out: list[str] = []
        for i in slots:
            owner = self.slot_owner[i]
            if owner not in out:
                out.append(owner)
        return out

    def dependent_rows(self, J: np.ndarray,
                       budget_ms: float = ANALYSIS_BUDGET_MS
                       ) -> tuple[list[int], bool]:
        """Rows that add no rank to the rows declared **before** them.

        Greedy forward selection in declaration order, so the blame lands on
        the later constraint — the one the user just added. **Do not replace
        this with column-pivoted QR**: measured, QR blamed an innocent original
        constraint in 2 of 3 cases (design Decision 6).

        Orthogonalization is classical Gram-Schmidt run twice, which is as
        stable as modified Gram-Schmidt and lets each row cost two
        matrix-vector products instead of a Python loop over the basis.

        Returns `(rows, complete)`; on an exhausted budget the row list is
        empty and `complete` is False — a partial answer is never reported as
        a whole one.
        """
        m, n = J.shape
        basis = np.empty((min(m, n), n))
        kept = 0
        dependent: list[int] = []
        deadline = time.monotonic() + budget_ms / 1e3
        for i in range(m):
            if time.monotonic() > deadline:
                return [], False
            w = np.array(J[i], dtype=float)
            n0 = float(np.linalg.norm(w))
            if n0 <= 0.0:
                dependent.append(i)      # an all-zero row constrains nothing
                continue
            if kept:
                b = basis[:kept]
                w -= b.T @ (b @ w)
                w -= b.T @ (b @ w)
            nw = float(np.linalg.norm(w))
            if nw > n0 * GREEDY_TOL_REL and kept < basis.shape[0]:
                basis[kept] = w / nw
                kept += 1
            else:
                dependent.append(i)
        return dependent, True

    def analyze(self, J: np.ndarray, f: np.ndarray, *, ok: bool,
                budget_ms: float = ANALYSIS_BUDGET_MS) -> dict:
        """The `diagnostics` block returned on every solve (FR5)."""
        t0 = time.perf_counter()
        n_par, n_res = self.n_par, self.n_res
        rank = self.rank(J) if (n_par and n_res) else 0
        dof = n_par - rank
        free = self.free_entities(J, rank) if dof > 0 else []

        # Only a rank-deficient system has dependent rows at all, so a
        # well-constrained sketch — the drag-path case — never pays for the
        # greedy pass.
        dependent: list[int] = []
        complete = True
        if rank < n_res:
            dependent, complete = self.dependent_rows(J, budget_ms)

        redundant, conflicting = [], []
        if complete and dependent:
            owners = self.row_owners()
            entries: dict[tuple[int, str | None], dict] = {}
            for row in dependent:
                res = owners[row]
                key = (res.con_index, res.origin)
                entry = entries.get(key)
                if entry is None:
                    entry = entries[key] = {
                        # the index the *caller* can look up: None when the
                        # constraint was compiled from an entity
                        "index": self.con_report[res.con_index],
                        # the type the caller *wrote*, not the compiled row's
                        # kind: a diagnostic never names a constraint the user
                        # did not write.
                        "type": self.con_types[res.con_index],
                        "origin": res.origin,
                        "violated": False,
                    }
                if abs(float(f[row])) > SATISFIED_TOL:
                    entry["violated"] = True
            for entry in entries.values():
                violated = entry.pop("violated")
                (conflicting if violated else redundant).append(entry)

        if rank < n_res:
            status = "over_constrained"
        elif not ok:
            status = "did_not_converge"
        elif dof > 0:
            status = "under_constrained"
        else:
            status = "well_constrained"

        diag = {
            "status": status,
            "dof": dof,
            "rank": rank,
            "n_params": n_par,
            "n_residuals": n_res,
            "free_entities": free,
            "analysis_ms": (time.perf_counter() - t0) * 1e3,
            "analysis_complete": complete,
        }
        if complete:
            # `conflicting` is *a* dependent set, not the unique culprit:
            # removing any one member resolves the dependency.
            diag["redundant"] = redundant
            diag["conflicting"] = conflicting
        return diag

    # ---------------- solve ----------------
    # A note for whoever owns the drag budget next: `tr_solver="lsmr"` was
    # measured on the drag path and **rejected**. Over a 100-frame scripted
    # drag with warm caches it is faster on an arc-heavy sketch (50-entity ring
    # + slot, 132 parameters: p50 9.09 -> 6.28 ms) and much slower on a
    # line-heavy one (50-segment staircase, 100 parameters: p50 6.27 -> 11.63
    # ms, max 6.77 -> 23.69 — over the FR6 budget). It is not a free win; the
    # default `exact` clears the budget on both.
    def _settle(self, fun, jac, xs, max_err, success, tol, max_nfev):
        """Project a dragged solution back onto the constraint manifold.

        The soft pull is a *compromise*: minimizing ``|f|^2 + w^2 |p - cursor|^2``
        leaves a fully-constrained point about ``w^2`` of the drag distance off
        its own constraints. Measured on the mirror triangle over a 48.7 mm
        drag: the point lands **0.170 mm** away with `max_residual` **0.104**,
        so `ok` is false for the honest reason that the coordinates really are
        off. Reporting the cursor's opinion as geometry is not the answer, so
        the frame ends with one constraint-only re-solve **seeded at the drag's
        answer** — which keeps the branch the drag chose (it starts 0.17 mm
        from it) and returns the coordinates the constraints imply: exactly
        `(23.4375, 18.7265)` again, `max_residual` 3.6e-15.

        It costs nothing in the case that matters: when the drag moved only
        free DOF the constraint rows are already satisfied and this returns
        immediately. Measured on the same triangle, for a frame that does need
        it: **0.24 ms -> 0.42 ms**, against a 16 ms budget.
        """
        n_res = self.n_res
        if max_err <= SATISFIED_TOL:
            return xs, np.asarray(fun(xs)[:n_res], dtype=float), max_err, success

        def cfun(v):
            return fun(v)[:n_res]

        def cjac(v):
            return jac(v)[:n_res]

        res = least_squares(cfun, xs, jac=cjac, method="trf",
                            xtol=tol, ftol=tol, gtol=tol, max_nfev=max_nfev)
        fs = np.asarray(res.fun, dtype=float)
        return (res.x, fs, float(np.max(np.abs(fs))) if n_res else 0.0,
                success and bool(res.success))

    def _diagnostics(self, J: np.ndarray, f: np.ndarray, *, ok: bool,
                     budget_ms: float) -> tuple[dict, str]:
        """`analyze`, behind the structure cache (design Decision 9c).

        A drag frame changes no constraints, so `auto` serves the block the
        previous solve computed rather than paying for an SVD and the greedy
        dependent-set pass on every frame. `full` always recomputes; `cached`
        prefers the cache whatever the frame is. The served block is the one
        that was computed — including its `analysis_ms` — and
        `diagnostics_source` says which it is, because a cached measurement
        presented as a fresh one is exactly the kind of quiet lie this
        codebase's `unverified` rule exists to prevent.
        """
        mode = self.diagnostics_mode
        key = self.structure_key()
        prefer_cache = key is not None and (
            mode == "cached" or (mode == "auto" and self.drag_res is not None))
        if prefer_cache:
            cached = _DIAG_CACHE.get(key)
            if cached is not None:
                return dict(cached), "cached"
        diag = self.analyze(J, f, ok=ok, budget_ms=budget_ms)
        if key is not None:
            _DIAG_CACHE[key] = dict(diag)
            while len(_DIAG_CACHE) > DIAG_CACHE_MAX:
                _DIAG_CACHE.pop(next(iter(_DIAG_CACHE)))
        return diag, "computed"

    def solve(self, tol: float = 1e-10, max_nfev: int = 2000, *,
              analysis_budget_ms: float = ANALYSIS_BUDGET_MS) -> dict:
        t0 = time.perf_counter()
        n_par, n_res = self.n_par, self.n_res
        m = n_res + self.n_drag
        x0 = self.initial_vector()
        fun, jac = self.make_functions()

        if n_par == 0 or m == 0:
            # Nothing to solve: least_squares rejects an empty problem, and
            # "no free parameters" is a legitimate (fully fixed) sketch.
            xs = x0
            fs = fun(xs)[:n_res] if n_res else np.zeros(0)
            max_err = float(np.max(np.abs(fs))) if n_res else 0.0
            success, nfev = True, 0
        else:
            res = least_squares(fun, x0, jac=jac, method="trf",
                                xtol=tol, ftol=tol, gtol=tol,
                                max_nfev=max_nfev)
            xs = res.x
            # `[:n_res]`, everywhere: the drag block is an objective, and a
            # verdict computed over it is a verdict about the cursor.
            fs = np.asarray(res.fun, dtype=float)[:n_res]
            max_err = float(np.max(np.abs(fs))) if n_res else 0.0
            success, nfev = bool(res.success), int(res.nfev)
            if self.drag_res is not None and n_res:
                xs, fs, max_err, success = self._settle(
                    fun, jac, xs, max_err, success, tol, max_nfev)

        ok = success and max_err < 1e-7
        J = (jac(xs)[:n_res] if (n_par and n_res)
             else np.zeros((n_res, n_par)))
        diag, source = self._diagnostics(J, fs, ok=ok,
                                         budget_ms=analysis_budget_ms)
        rank = diag["rank"]
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
        out_arcs = {}
        for name, a in self.arcs.items():
            cx, cy = self._refs[a.center].value(xs)
            sx, sy = self._refs[f"{name}.start"].value(xs)
            ex, ey = self._refs[f"{name}.end"].value(xs)
            # Normalize on OUTPUT only: the parameter itself is never wrapped
            # (a wrapped parameter is a Jacobian discontinuity, and is how an
            # arc jumps the long way round during a drag). The reported end
            # keeps the full sweep relative to the reported start.
            start_deg = math.degrees(float(xs[a.i1])) % 360.0
            sweep_deg = math.degrees(float(xs[a.i2]) - float(xs[a.i1]))
            out_arcs[name] = {
                "center": a.center,
                "cx": float(cx), "cy": float(cy),
                "r": float(self._rads[name].value(xs)),
                "start_deg": start_deg,
                "end_deg": start_deg + sweep_deg,
                "start": {"x": float(sx), "y": float(sy)},
                "end": {"x": float(ex), "y": float(ey)},
                "authored": a.authored,
            }
        out_splines = {}
        for name, sp in self.splines.items():
            coords = [self._refs[pt].value(xs) for pt in sp.points]
            tangents = {}
            for which, pinned in sp.end_tangent.items():
                if not pinned:
                    tangents[which] = None
                    continue
                leg = (sp.points[0], sp.points[1]) if which == "start" \
                    else (sp.points[-2], sp.points[-1])
                ax, ay = self._refs[leg[0]].value(xs)
                bx, by = self._refs[leg[1]].value(xs)
                ux, uy, _ = _unit(ax, ay, bx, by)
                tangents[which] = {"x": float(ux), "y": float(uy)}
            out_splines[name] = {
                "points": list(sp.points),
                "coords": [{"x": float(x), "y": float(y)} for x, y in coords],
                "degree": 3, "periodic": False,
                # The emitter must pass `tangents=` for a pinned end, or the
                # constraint does not hold on the emitted curve (measured:
                # up to 44.6 deg of free-end drift).
                "end_tangent": dict(sp.end_tangent),
                "tangents": tangents,
            }
        out_slots = {}
        for name, slot in self.slots.items():
            c1x, c1y = self._refs[slot.c1].value(xs)
            c2x, c2y = self._refs[slot.c2].value(xs)
            out_slots[name] = {
                "c1": slot.c1, "c2": slot.c2,
                "center1": {"x": float(c1x), "y": float(c1y)},
                "center2": {"x": float(c2x), "y": float(c2y)},
                "width": slot.width,
                "r": float(xs[slot.ir]),
                "arcs": [f"{name}.arc_a", f"{name}.arc_b"],
                "sides": [f"{name}.side_1", f"{name}.side_2"],
            }
        out = {
            "ok": ok,
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
            "arcs": out_arcs,
            "splines": out_splines,
            "slots": out_slots,
            "diagnostics": diag,
            # "computed" or "cached": a drag frame serves the block the
            # constraint set already produced (Decision 9c).
            "diagnostics_source": source,
            "warm_started": self.warm_started,
            "warnings": list(self.warnings),
        }
        if self.drag_info is not None:
            px, py = self._refs[self.drag_info["point"]].value(xs)
            out["drag"] = {
                **self.drag_info,
                # How far the constraints kept the point from the cursor. It is
                # reported here and **nowhere else**: it is not a residual of
                # this sketch.
                "gap": float(math.hypot(px - self.drag_info["x"],
                                        py - self.drag_info["y"])),
            }
        return out


# ---------------- JSON front-end (agent tool shape) ----------------
def parse_sketch(spec: dict) -> Sketch:
    """Compile a JSON-shaped spec into a `Sketch` (no solve).

    spec = {
      "points":  [{"name","x","y","fixed"?}, ...],
      "lines":   [{"name","p1","p2"}, ...],
      "circles": [{"name","center","r","fixed_r"?}, ...],
      "arcs":    [{"name","center","r","start_deg","end_deg","fixed_r"?}, ...]
                 or [{"name","start":[x,y],"mid":[x,y],"end":[x,y]}, ...],
      "splines": [{"name","points":[<point names>]}, ...],
      "slots":   [{"name","c1","c2","width"}, ...],
      "constraints": [{"type": <name>, ...kwargs}, ...],
      "initial": {"points": {name: {"x","y"}}, "circles": {name: {"r"}},
                  "arcs": {name: {"r","start_deg","end_deg"}},
                  "slots": {name: {"r"}}},
      "drag":    {"point": <handle>, "x": .., "y": .., "weight"?: ..},
      "diagnostics": "auto" | "full" | "cached"
    }

    Split out from `solve_sketch` so callers that need the compiled residuals
    or the Jacobian — the diagnostics tests, and the drag path — do not have to
    re-implement ingestion.
    """
    sk = Sketch()
    for p in spec.get("points", []):
        sk.point(p["name"], p["x"], p["y"], p.get("fixed", False))
    for c in spec.get("circles", []):
        sk.circle(c["name"], c["center"], c["r"], c.get("fixed_r", False))
    for a in spec.get("arcs", []):
        if "center" in a and "start_deg" in a:
            sk.arc(a["name"], a["center"], a["r"], a["start_deg"], a["end_deg"],
                   a.get("fixed_r", False))
        elif "start" in a and "mid" in a and "end" in a:
            sk.arc_three_point(a["name"], a["start"], a["mid"], a["end"])
        else:
            raise SketchError(
                f"arc {a.get('name')!r} must be authored either centre-form "
                "({center, r, start_deg, end_deg}) or 3-point "
                "({start, mid, end})")
    for sl in spec.get("slots", []):
        sk.slot(sl["name"], sl["c1"], sl["c2"], sl["width"])
    for sp in spec.get("splines", []):
        sk.spline(sp["name"], sp["points"])
    # lines last: their endpoints may be virtual handles (`a1.end`), which
    # only exist once the arc that owns them has been declared
    for l in spec.get("lines", []):
        sk.line(l["name"], l["p1"], l["p2"])
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
        "tangent": sk.tangent, "symmetric": sk.symmetric,
        "equal_length": sk.equal_length, "concentric": sk.concentric,
    }
    for i, c in enumerate(spec.get("constraints", [])):
        kw = {k: v for k, v in c.items() if k != "type"}
        try:
            fn = dispatch[c["type"]]
        except KeyError:
            raise SketchError(f"unknown constraint type {c.get('type')!r}; "
                              f"known: {sorted(dispatch)}")
        # The caller-visible index is this constraint's position in the spec,
        # so entity-compiled constraints (a slot's, declared first) cannot
        # shift what a diagnostic points at.
        sk._spec_index = i
        try:
            fn(**kw)
        finally:
            sk._spec_index = _AUTO_INDEX
    sk.con_args = [dict(c) for c in spec.get("constraints", [])]
    mode = spec.get("diagnostics") or "auto"
    if mode not in DIAGNOSTICS_MODES:
        raise SketchError(f"diagnostics must be one of "
                          f"{list(DIAGNOSTICS_MODES)}, not {mode!r}")
    sk.diagnostics_mode = mode
    sk.seed(spec.get("initial"))
    drag = spec.get("drag")
    if drag:
        if not isinstance(drag, dict):
            raise SketchError("drag must be {point, x, y, weight?}")
        unknown = set(drag) - {"point", "x", "y", "weight"}
        if unknown:
            raise SketchError(
                f"drag has unknown key(s) {sorted(unknown)}; "
                "known: ['point', 'x', 'y', 'weight']")
        for key in ("point", "x", "y"):
            if drag.get(key) is None:
                raise SketchError(f"drag needs {key!r}")
        sk.drag(drag["point"], drag["x"], drag["y"], drag.get("weight"))
    return sk


def solve_sketch(spec: dict, *,
                 analysis_budget_ms: float = ANALYSIS_BUDGET_MS) -> dict:
    """Compile and solve a sketch from a JSON-shaped spec (see `parse_sketch`)."""
    return parse_sketch(spec).solve(analysis_budget_ms=analysis_budget_ms)
