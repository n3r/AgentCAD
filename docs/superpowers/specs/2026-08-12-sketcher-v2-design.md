# Sketcher v2 — design

- **PRD:** [PRD-009](../../prd/in-progress/PRD-009-sketcher-v2.md)
- **Date:** 2026-08-12
- **Phase:** v5 — daily-driver depth (the first v5 feature)
- **Status:** design complete; every load-bearing number below was measured on
  this machine before the decision it supports was taken. The measurement
  appendix names the script for each.

---

## What is different about this feature

PRD-001 through PRD-008 were orchestration problems: where does state live,
who may write it, what is the evidence, how does it degrade. PRD-009 is a
**numerical** problem. Its acceptance criteria are inequalities (`≤ 16 ms
p50`), its failure modes are convergence and rank, and its central risk —
drag-to-solve — is a latency budget nobody in this repo has ever measured.

The PRD-008 face-anchor experience is the precedent this design is written
against: an unmeasured assumption about geometry behaviour ("a lone candidate
is evidence") survived into implementation and cost three review rounds. So
this spec does not open with an architecture. It opens with **what the shipped
solver actually does, measured**, and every subsequent decision cites a number.

---

## Ground truth — what `agentcad/toolkit/sketch.py` actually is

335 lines. Its own docstring calls it a "Prototype of what would become
`agentcad/toolkit/sketch.py`" — it is the prototype, shipped.

**Representation.** Three entity types (`_Point`, `_Line`, `_Circle`). A line
owns no parameters — it is a pair of point *names*. A circle owns one radius
parameter and a centre point *name*. The free-parameter vector is packed at
solve time: `[x, y]` per non-fixed point, then `[r]` per non-fixed circle.

**Constraints are closures, not data.** `Sketch._add(k, fn)` appends a Python
callable to `self.residuals` and adds `k` to a running `n_res` counter.
Nothing records which constraint produced which residual rows, what type it
was, or what entities it touched. `self.residuals` is a list of anonymous
lambdas. This is the single most consequential fact in the file: **the solver
cannot name a constraint, because after ingestion it no longer has any.**

**The solve.** `scipy.optimize.least_squares`, method `lm` when
`n_res >= n_par` else `trf`, `xtol=ftol=gtol=1e-10`, `max_nfev=2000`, and
**no `jac=` argument** — so scipy computes a **2-point finite-difference
Jacobian**. Each Jacobian therefore costs `n_par + 1` full evaluations of the
residual list, and each such evaluation is a Python `for` loop over closures
that call a `_Get.__call__` per point access and `math.hypot` per distance.

**Degrees of freedom.** `dof = n_par - n_res`. That is a count of rows, not a
rank. It is wrong whenever any constraint is redundant, and it is the only
diagnostic the tool returns.

**Convergence verdict.** `ok = res.success and max|f| < 1e-7`. There is no
distinction between "did not converge", "over-constrained but consistent" and
"contradictory".

**`initial` is not implemented.** `tools_sketch.py` declares it in the schema
as `"unused; reserved"` and never reads it; `routes_sketch.py` does not even
forward it (the route whitelists `entities` and `constraints` only).

**The GUI.** `frontend/js/sketcher.js` (797 lines) is an SVG overlay. Every
mutation calls `solveAndRender()`, which POSTs the **whole** spec to
`/api/sketch/solve` and overwrites the local model with the solved
coordinates. There is **no drag interaction at all** — the `select` tool
selects; points cannot be moved with the mouse. Emission is client-side
`buildSnippet()`, which walks the lines into chains and emits `Polyline(...)`
plus `Circle(...)`, rounded to 6 decimals (`fmtNum`), appended to the script
as a `sketch_profile()` function the user must wire into `build(p)` themselves.

### Measured: the shipped solver's cost

`scratchpad/bench_sketch.py`, Apple M1 Max, Python 3.12, scipy 1.18.0, numpy
2.5.1. A "staircase" of `n_seg` alternating horizontal/vertical lines, each
carrying an H/V constraint and a distance — a well-conditioned,
exactly-constrained sketch of the shape a real profile has.

| n_seg | n_par | n_res | cold p50 | warm (seeded at the solution) | warm (one coord nudged 0.4 mm — a drag frame) |
|---:|---:|---:|---:|---:|---:|
| 4   | 8   | 8   | 0.71 ms | 0.32 ms | 0.57 ms |
| 10  | 20  | 20  | 2.57 ms | 1.07 ms | 2.08 ms |
| 25  | 50  | 50  | 13.42 ms | 5.43 ms | 10.65 ms |
| **50** | **100** | **100** | **51.57 ms** | 20.38 ms | **51.09 ms** |
| 100 | 200 | 200 | 203.64 ms | 80.59 ms | 205.10 ms |

Two readings matter.

1. **FR6 is already missed by 3.2×, before any new entity exists.** FR6 asks
   for a warm re-solve of a 50-entity sketch at ≤ 16 ms p50 and a cold solve
   at ≤ 250 ms. The shipped solver takes 51 ms for a drag-sized perturbation
   of a 50-line sketch — measured *in-process*, with no HTTP, no JSON, no
   browser.
2. **Warm-starting the shipped solver buys almost nothing under a real drag.**
   Seeding exactly at the solution costs 20 ms (LM still needs two Jacobians
   to prove it is done); nudging one coordinate by 0.4 mm costs the full
   51 ms. So FR4's "`initial` activated" is *not* by itself a performance
   feature. Anyone who plans the work as "wire up `initial` and the drag gets
   fast" will land a slice that measures 51 ms.

Where the time goes, measured on the same sketches:

| n_seg | one residual evaluation | one 2-point FD Jacobian (`n_par + 1` evals) |
|---:|---:|---:|
| 10 | 18.0 µs | 21 evals = 0.38 ms |
| 50 | 93.2 µs | 101 evals = **9.41 ms** |
| 100 | 184.6 µs | 201 evals = **37.10 ms** |

At `n_seg=50` the solver reports `nfev=5`; five iterations × 9.41 ms of
finite differences ≈ 47 ms of the measured 51 ms. **The solver spends 92% of
its time numerically differentiating a function it could differentiate
analytically.** The residual system is elementary calculus — every residual in
the file is a polynomial, a hypot, a normalized cross/dot product or an
`atan2`. There is no reason for a finite difference anywhere in it.

### Verdict on the shipped solver

It is a correct, small, honest prototype with three structural gaps, in
descending order of consequence:

1. **No analytic Jacobian** — an O(n²) cost per iteration that puts every
   interactive target out of reach.
2. **No constraint identity** — residuals are anonymous closures, so no
   diagnostic can name a constraint (G4 is unreachable without changing this).
3. **No parametrized curves** — points, lines and circles only, and a line is
   not even an entity with its own parameters (G1/G2).

It is *not* numerically unsound. The residual formulations are reasonable
(the `point_on_line` residual is properly normalized by the segment length;
`angle` wraps to `(-π, π]`; `_uvec` guards a zero-length denominator). The
solve strategy — Levenberg–Marquardt least squares over a residual vector —
is the same strategy FreeCAD's PlaneGCS and SolveSpace use. What is missing is
engineering, not mathematics.

---

## Decision 1 — extend the first-party solver; do not adopt a dependency

The PRD's scope (full entity vocabulary, generalized tangency, DOF analysis,
conflict diagnosis, interactive drag) is exactly the feature set of a
production 2D geometric constraint solver. That makes "should we depend on
one?" a real question, not a formality. It is also a **supply-chain and
packaging** question: this repo pins build123d, vendors its frontend
libraries with a `VERSIONS.txt`, and ships a single binary
(`scripts/build_binary.sh`).

The roadmap's non-goals forbid an in-house **geometry kernel** and an in-house
**CAD language**. A 2D constraint solver is neither — it produces numbers, not
B-rep, and it has no surface syntax. So building one is *allowed*. The
question is whether it is *wise*.

### The candidates

| Option | What it is | License | Packaging | Verdict |
|---|---|---|---|---|
| **Extend `toolkit/sketch.py`** | numpy + `scipy.optimize`, both already installed | ours | none — no new wheel | **chosen** |
| `python-solvespace` / `py-slvs` | SolveSpace's SLVS solver as a Python C extension | **GPL-3.0** ([SolveSpace](https://en.wikipedia.org/wiki/SolveSpace), [py-slvs](https://pypi.org/project/py-slvs/1.0.4/)) | native wheel per platform | **rejected — license** |
| `planegcs` (FreeCAD's) | the reference OSS 2D GCS; DogLeg/LM/BFGS/SQP | LGPL-2.1 ([FreeCAD/planegcs](https://github.com/FreeCAD/FreeCAD/tree/main/src/Mod/Sketcher/App/planegcs)) | C++; no first-party maintained PyPI wheel; a [WASM wrapper](https://github.com/Salusoft89/planegcs) exists | **rejected — packaging + split brain** |
| `planegcs` WASM in the browser | solve client-side at 60 fps, no round trip | LGPL-2.1 | vendored binary blob in `frontend/vendor/` | **rejected — two solvers** |

### Why the dependencies lose

**License.** The repo has **no `LICENSE` file and no `license` field in
`pyproject.toml`**. Its licensing posture is undecided. Linking a GPL-3.0
solver into a shipped single binary decides it — permanently, silently, inside
a sketcher PRD. That is not a decision this feature gets to make. `py-slvs` is
out on that ground alone.

**Packaging.** PlaneGCS is LGPL (usable), but it is C++ with no maintained
first-party Python wheel. Adopting it means building or vendoring a native
extension for macOS-arm64, macOS-x86_64, Linux and Windows, inside a binary
build that today has exactly one native surface (the OCCT wheels) and treats
it as a known cost. It would double that surface for a component whose pure-
Python replacement measures **0.78 ms** (below).

**The split brain — the decisive one.** The WASM option is genuinely
attractive for latency: a browser-side solver has no round trip at all. But
PRD-009's whole thesis is stated in its own competitive section: *"the
sketcher is a thin GUI over a solver that is also an agent tool, so completing
the vocabulary upgrades humans and agents in the same commit."* A WASM solver
in the browser plus a Python solver for `solve_sketch` is **two solvers with
two convergence behaviours, two diagnostics implementations and two sets of
rounding**. The PRD's "Handoff" scenario — *"a human drags an agent-authored
sketch in the GUI; both drive the same solver over the same spec"* — becomes
false by construction, and every divergence between them is a bug class that
did not previously exist. One solver is a product invariant here, not a
preference.

### Why extending wins — measured

`scratchpad/spike_analytic.py` implements the same staircase system with a
**hand-written analytic Jacobian** and a damped Gauss–Newton (LM) loop over
`numpy.linalg`, in pure Python. Same machine, same sketches:

| n_seg | n_par | cold p50 | **warm drag-frame p50** | one residual eval | one **analytic** Jacobian | SVD rank analysis |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 20 | 0.19 ms | 0.17 ms | 11.5 µs | 16.8 µs | 0.10 ms |
| 25 | 50 | 0.35 ms | 0.29 ms | 20.5 µs | 30.2 µs | 0.08 ms |
| **50** | **100** | **0.78 ms** | **0.71 ms** | 57.0 µs | 67.4 µs | 0.30 ms |
| 100 | 200 | 2.11 ms | 1.67 ms | 114.9 µs | 171.3 µs | 2.16 ms |
| 200 | 400 | 6.47 ms | 4.93 ms | 164.9 µs | 260.3 µs | 11.13 ms |

**66× faster at the FR6 size** (51.09 → 0.78 ms), from one change: the
Jacobian. The analytic Jacobian costs 67 µs where the finite-difference one
cost 9410 µs — 140×, because it is one pass instead of `n_par + 1` passes.
The drag budget stops being a research question and becomes 4% of itself.

At 200 segments (400 parameters, twice the PRD's Phase-3 "100+ entity" bar)
the warm drag frame is still 4.93 ms. **Sparse Jacobians are not needed** for
anything this PRD asks for; the PRD's own hedge ("sparse Jacobian if the
benchmark demands") is answered *no* by measurement, and the benchmark test
is what keeps that answer honest.

**Decision: extend `agentcad/toolkit/sketch.py` in place. No new runtime
dependency. The engineering that buys the PRD's targets is an analytic
Jacobian and a typed residual IR, not a third-party solver.**

### One honest correction to make while we are here

`agentcad/toolkit/sketch.py` imports `numpy` and `scipy`, and **neither is
declared in `pyproject.toml`**. They resolve only because `build123d` happens
to require scipy (verified against `uv.lock`: scipy 1.18.0 is pulled in by
`build123d`, `ocp-gordon`, `scikit-fem`, `scikit-learn`, `svgpathtools`). The
*server* process — which must never import build123d — imports
`toolkit.sketch` through `tools_sketch.py`, so the server's sketch capability
currently depends on a transitive dependency of a package it is forbidden to
import. A build123d release that drops scipy silently breaks `solve_sketch`.
**Declare `numpy` and `scipy` as direct dependencies.** This is a one-line
correction with no behaviour change, and it belongs to the feature that makes
those libraries load-bearing.

---

## Decision 2 — the shape of the rewrite: a typed residual IR

The three gaps in Ground Truth share one root: residuals are anonymous
closures. Fix that once and all three become tractable.

Every constraint compiles to one or more **`Residual` records**:

```python
@dataclass(frozen=True, slots=True)
class Residual:
    con_index: int          # which entry of spec["constraints"] produced this row
    kind: str               # "distance", "tangent_arc_arc", ... (for messages)
    rows: int               # how many rows this record contributes
    params: tuple[int, ...] # the free-parameter slots this row can touch
    f: Callable             # (v: np.ndarray) -> np.ndarray of length `rows`
    df: Callable            # (v, J, row0) -> None; writes into J[row0:row0+rows, params]
```

Three properties follow directly, and they are the whole design:

- **`con_index` gives diagnostics a name.** A dependent residual row maps back
  to the exact `spec["constraints"][i]` the caller wrote, including the
  compiled sub-entities of a slot (Decision 5).
- **`df` gives the analytic Jacobian.** `J` is allocated once per solve and
  filled by index assignment. Rows are sparse by construction — no residual in
  the entire vocabulary touches more than 8 parameters — but `J` stays a
  **dense** `numpy` array, because the measurement says dense is fast enough
  through 400 parameters and dense keeps `numpy.linalg` (and the SVD the
  diagnostics need) in play with no `scipy.sparse` plumbing.
- **`params` gives a cheap correctness net.** `df` must write only inside
  `params`; a debug-mode assertion compares each `df` against a central
  difference of its own `f`. Every new residual kind added by this PRD gets
  that test, mechanically. This is the single highest-value test in the plan:
  an analytic Jacobian that is subtly wrong does not crash — it converges
  slowly, or to the wrong branch, or not at all, and it is very hard to debug
  from the outside.

The solve loop stays `scipy.optimize.least_squares`, now with
`jac=<the assembled Jacobian>` and `method="trf"` uniformly (LM in MINPACK
requires `m >= n`, which an under-constrained sketch violates; `trf` handles
both and the measurement shows the method choice is noise next to the
Jacobian). The public `solve_sketch(spec) -> dict` signature and every v1 key
in its result are unchanged (Decision 13).

---

## Decision 3 — entity parametrization

| Entity | Free parameters | Derived | Notes |
|---|---|---|---|
| `point` | `x, y` (unless `fixed`) | — | unchanged from v1 |
| `line` | none | direction, length | a name pair, unchanged from v1 |
| `circle` | `r` (unless `fixed_r`) + its centre point | — | unchanged from v1 |
| `arc` | centre point + `r`, `θ₁`, `θ₂` (radians internally, **degrees in the spec**) | `start`, `end` points | see below |
| `ellipse` | centre point + `a`, `b`, `φ` (+ `θ₁`, `θ₂` when bounded) | `start`, `end` | Phase 2 |
| `spline` | its control points (ordinary `point` entities) | the curve | degree fixed at 3 |
| `slot` | none — compiled | two arcs + two lines | Decision 5 |

### Arcs: parametrized *and* endpoint-addressable

An arc is `(centre_point, r, θ₁, θ₂)`. Its endpoints are **derived**, not
free:

```
start = (cx + r·cos θ₁,  cy + r·sin θ₁)
end   = (cx + r·cos θ₂,  cy + r·sin θ₂)
```

FR2 requires arc endpoints to participate in `coincident` so chains close.
Two ways to give them a name, and the choice matters:

- **(a) Free endpoint points + residuals tying them to the centre/radius/angle.**
  Adds 4 parameters and 4 residuals per arc, all redundant. It inflates
  `n_par` and `n_res` symmetrically, so `dof` is unaffected — but every one of
  those rows shows up in the rank analysis as machinery the user never wrote,
  and the conflicting-set report would name them. Rejected.
- **(b) Virtual point handles.** `arc1.start` and `arc1.end` are *names* that
  resolve to a derived `(x, y)` and a derived Jacobian row (chain rule through
  `cx, cy, r, θ`). No extra parameters, no extra residuals, and `coincident
  {p: "arc1.end", q: "ln3.p1"}` is exactly 2 rows — the same as any other
  coincidence. **Chosen.**

Option (b) is why the residual IR carries `params` as an explicit tuple: a
residual on `arc1.start` touches `{cx, cy, r, θ₁}`, not a point slot. A
`PointRef` abstraction resolves a name — `"p3"`, `"arc1.start"`,
`"ellipse2.center"`, `"slot1.arc_a.start"` — to `(value_fn, grad_fn, params)`,
and **every existing constraint is rewritten against `PointRef` instead of
against point names**. That single indirection is what makes "generalized
tangency" and "arc endpoints participate in coincident" fall out of the v1
vocabulary rather than needing a combinatorial matrix of new constraint types.

`θ₁`/`θ₂` are unwrapped continuously across a drag: the parameter vector
carries whatever real number the previous frame ended on, and the *reported*
angles are normalized only on output. Wrapping a parameter mid-solve is a
discontinuity in the Jacobian and is precisely how an arc "jumps the long way
round" during a drag.

### Splines: control points, fixed degree, MVP restrictions

A spline is an ordered list of named `point` entities (its control points),
degree 3, non-periodic, emitted as build123d `Spline(*pts)` — which
**interpolates** its points. That is the important semantic to fix now: the
solver's spline points are *through* points, matching `Spline`'s behaviour, so
the emitted curve passes through exactly the coordinates the solver reports.

MVP constraint surface on splines, per the PRD's own risk entry: constraints
apply to **control points** (any point constraint works, free) and to **end
tangents** (`tangent {a: "sp1.start", b: "ln4"}`, implemented as a direction
residual between the line and the first control polygon leg — an approximation
of the true end tangent that is exact for a cubic interpolating spline's end
condition only under `tangents=` emission). **On-curve point constraints are
out**, and the docs say so plainly rather than shipping something ambiguous.

This is the one place in the design where the emitted geometry and the solved
model can disagree, so the plan carries a **spike** before the spline slice:
build the same control polygon through build123d `Spline` and measure the
deviation between the solver's polyline model and the real curve at the
midpoints. If the deviation exceeds the emission tolerance the design falls
back to `Bezier` (control-point semantics, no interpolation) and says so.

---

## Decision 4 — the constraint vocabulary

Every v1 constraint keeps its name, its keyword arguments and its residual
count (FR3). New ones:

| Type | Args | Rows | Residual |
|---|---|---|---|
| `tangent` | `a`, `b`, `at?` | 1 (or 3 with `at`) | dispatched on the pair's kinds |
| `symmetric` | `a`, `b`, `about` | 2 | midpoint on the line **and** `ab ⟂ about` |
| `equal_length` | `l1`, `l2` | 1 | `|l1| − |l2|` |
| `equal_radius` | `c1`, `c2` | 1 | extended to arcs (radius is a radius) |
| `concentric` | `a`, `b` | 2 | coincidence of the two centres |
| `coincident` | `p`, `q` | 2 | now accepts virtual handles (`arc1.end`) |

**`tangent` is one name with a dispatch table**, not five constraint types.
The PRD's agent surface says `tangent` *generalized*; a `PointRef`-style
`CurveRef` resolves `a` and `b` to `(kind, centre_ref, radius_ref)` and the
dispatcher picks:

- line–circle / line–arc → distance(centre, line) − r, or the 3-row form when
  `at` is given (the v1 formulation, unchanged and reused)
- circle–circle / arc–arc / arc–circle → `d(c₁,c₂) − (r₁ ± r₂)`, sign from
  `kind: "external" | "internal"` (v1's `tangent_circles`, unchanged)
- spline-end–line → the end-tangent direction residual above

**`tangent_line_circle` and `tangent_circles` remain registered** as their own
names for ever. They are in the shipped tool description, in
`docs/part-authoring.md`, in `docs/agent-api.md` and in the v1 test corpus.
`tangent` is a new front door onto the same residuals, not a rename.

**`symmetric` is two rows, not one.** Symmetry of `a` and `b` about line `L`
means the midpoint of `ab` lies on `L` *and* `ab` is perpendicular to `L`.
Shipping only the midpoint row is the kind of half-constraint that looks
right on a rectangle and is wrong on everything else.

---

## Decision 5 — slots compile at ingestion, and stay addressable

FR1 says a slot compiles "at spec ingestion into the two-arc/two-line
composite with internal tangency and equal-radius auto-applied", and the PRD's
risk list flags the naming problem. Both halves are settled here.

A `slot {name, c1, c2, width}` expands, **before any residual is built**, into:

- two arcs `<name>.arc_a`, `<name>.arc_b` centred on `c1`, `c2` with radius
  `<name>.r` (one shared radius parameter — equal-radius is structural, not a
  constraint row, so it can never appear in a conflict report)
- two lines `<name>.side_1`, `<name>.side_2` joining the arc endpoints
- auto-constraints: `coincident` on each of the four junctions, `tangent`
  line–arc at each of the four, and `radius(<name>.r) = width/2`

Every generated constraint carries `con_index` pointing at the **slot's own
spec index** and an `origin: "slot:<name>"` marker. Consequence, stated as a
rule: **a diagnostic never blames a constraint the user did not write.** A
conflict inside a slot's internal machinery reports the slot, with
`details.origin` naming the sub-entity (`slot1.arc_a`) for anyone who wants
it. Grouped and addressable, exactly as the PRD's risk entry asks.

Sub-entity names are reserved: a spec containing a user entity named
`slot1.arc_a` is a `validation_error` at ingestion, not a silent collision.

---

## Decision 6 — DOF diagnostics: rank from SVD, dependent set from *declaration order*

This is the decision the PRD's own risk list is most nervous about ("rank
analysis finds *a* dependent set, not necessarily the constraint the user
considers the culprit"), and it is the one where the obvious implementation is
measurably wrong.

### Rank and DOF

At the solution, assemble `J` (n_res × n_par) and take
`numpy.linalg.svd(J, compute_uv=False)`. `rank = #{σ > max(m,n)·σ₀·1e-10}`,
`dof = n_par − rank`.

Measured against the shipped `dof = n_par − n_res` on six sketches
(`scratchpad/spike_diag.py`):

| Sketch | `n_par − n_res` (shipped) | `n_par − rank` (true) |
|---|---:|---:|
| Rectangle, exactly constrained | 0 | 0 |
| … + redundant `parallel(ab, cd)` | **−1** | **0** |
| … + duplicate `distance(d,c)=50` | **−1** | **0** |
| … + contradictory `distance(d,c)=60` | **−1** | **0** |
| Rectangle without dimensions | 2 | 2 |
| Two tangent circles | 0 | 0 |

The shipped number reports a **negative DOF** for every over-constrained
sketch — a quantity with no meaning that the GUI renders today as
`solved · dof -1`. Rank analysis gives the right answer (0 DOF, plus a
redundancy) in every case. Cost: 0.10 ms at 50×50, 0.31 ms at 100×100, 2.18 ms
at 200×200 — affordable, but see the drag budget in Decision 9.

### The dependent set — where the obvious method fails

The textbook method is **column-pivoted QR of `Jᵀ`**: the first `rank` pivots
are an independent row set, the trailing pivots are dependent. Measured on the
same rectangles:

| Sketch | pivoted-QR blames | correct? |
|---|---|---|
| + redundant `parallel(ab,cd)` (constraint #6) | `#6 parallel` | ✅ |
| + duplicate `distance(d,c)=50` (#6) | **`#3 vertical`** | ❌ |
| + contradictory `distance(d,c)=60` (#6) | **`#3 vertical`** | ❌ |

In two of three cases pivoted QR blames an **original, innocent** constraint —
`vertical(da)`, which the user drew first and considers structural — because
column pivoting selects by *column norm*, which is an artifact of residual
scaling, not of intent. AC3 ("the conflicting set naming the added
constraint") **fails** with pivoted QR. Shipping it would produce a diagnostic
that confidently points at the wrong line, which is worse than the shrug it
replaces.

### The method that works: greedy forward selection in declaration order

Walk the residual rows **in the order the user declared them**. Keep a row iff
it raises the rank of the kept set (incremental modified Gram–Schmidt against
an orthonormal basis, with a relative tolerance). A row that does not raise
the rank is dependent **on rows declared earlier** — so the blame lands on the
*later* constraint, which is the one the user just added. Measured:

| Sketch | greedy blames | correct? |
|---|---|---|
| + redundant `parallel(ab,cd)` | `#6 parallel` — redundant | ✅ |
| + duplicate `distance(d,c)=50` | `#6 distance` — redundant | ✅ |
| + contradictory `distance(d,c)=60` | `#6 distance` — **conflicting** | ✅ |
| + duplicate `horizontal(cd)` | `#6 horizontal` — redundant | ✅ |
| rectangle, exactly constrained | — | ✅ |

Four for four, where pivoted QR was one for three. **Declaration order is the
signal.** It costs 0.06 ms on the rectangle; at scale it is a Python loop:
1.82 ms at 50×50, 6.35 ms at 100×100, 25.78 ms at 200×200 — an order of
magnitude more than the SVD, which is why Decision 9 keeps it off the drag
path.

Two honesty rules the PRD demands and this design keeps:

- **Never claim uniqueness.** The payload field is `conflicting`, the docs and
  the tool description say "a dependent set — removing any one member resolves
  the dependency", and the GUI highlights **all** members.
- **Declaration order is a heuristic, not a proof.** It works because a
  sketch is built incrementally and the newest constraint is the usual
  culprit. An agent that submits a spec in arbitrary order gets an arbitrary
  (but still correct) member of the dependent set. Say so in the tool
  description.

---

## Decision 7 — `redundant` vs `conflicting`, and when the tool errors

The PRD asks for both sets. They are distinguished by one measurement at the
solution: a dependent row whose residual is **satisfied** (`|f| ≤ 1e-7`) is
**redundant**; one that is **violated** is **conflicting**. Measured: the
duplicate `distance=50` case ends with `max|f| = 3.6e-18` (redundant); the
contradictory `distance=60` case ends with `max|f| = 2.50` (conflicting).

The consequence for the tool contract needs stating explicitly, because the
PRD's Experience section (*"An over-constrained sketch returns a
`validation_error`"*) reads more aggressively than the design should be:

> **`over_constrained` alone is not an error. Unsatisfiability is.**

- `status: "over_constrained"`, `conflicting: []`, all residuals satisfied →
  **`ok: true`**, solved coordinates returned, `redundant: [...]` reported.
  Adding a harmless duplicate constraint must not break a working sketch;
  every incumbent sketcher tells you about it and carries on.
- `conflicting` non-empty (equivalently `max_residual > 1e-7`) → the tool
  raises `ValidationError` with **`details.diagnostics`** carrying the full
  block including `conflicting`, exactly as the PRD's agent path describes.
- Non-convergence with an empty `conflicting` set (the solver gave up: bad
  initial guess, a genuinely inconsistent non-linear system with full rank) →
  also `ValidationError`, `status: "did_not_converge"`, and the message says
  which of the two it is rather than blaming the constraint set.

`diagnostics` is returned on **every** solve, success or failure (FR5):

```json
{"status": "well_constrained|under_constrained|over_constrained|did_not_converge",
 "dof": 0, "rank": 6, "n_params": 6, "n_residuals": 7,
 "redundant": [{"index": 6, "type": "parallel", "origin": null}],
 "conflicting": [],
 "free_entities": [],
 "analysis_ms": 0.4, "analysis_complete": true}
```

`free_entities` answers the second half of the PRD's DOF ask — *"here are the
free entities"*. It is read off the null space: for each right-singular vector
beyond `rank`, the parameter slots with non-negligible components map back to
their owning entity. Under-constrained by 2 DOF with `free_entities:
["p7", "c3"]` is a diagnostic a human can act on; a bare `dof: 2` is not.

`analysis_complete: false` with `redundant`/`conflicting` omitted is the
documented degradation when the FR5 time budget (Decision 9) is exhausted.
**"We did not look" is never rendered as "nothing found"** — the PRD-008
`unverified` rule, applied to numerics.

---

## Decision 8 — `initial`, and what it is actually for

FR4 activates `initial`. Its shape:

```json
{"points":  {"p3": {"x": 12.5, "y": 4.0}},
 "circles": {"c1": {"r": 6.0}},
 "arcs":    {"a1": {"cx": 0, "cy": 0, "r": 8, "start_deg": 0, "end_deg": 90}},
 "ellipses":{"e1": {"a": 10, "b": 6, "rot_deg": 15}}}
```

Semantics, in order of how easy they are to get wrong:

1. **`initial` overrides the starting point, never the spec.** It cannot make
   a point fixed, cannot change a radius that the spec pinned with `fixed_r`,
   and cannot introduce an entity. It seeds `x₀`.
2. **An unknown name is a `validation_error`** (FR4, verbatim). A silent
   ignore turns a client-side desync into a sketch that mysteriously stops
   warm-starting.
3. **A `initial` that no longer matches the spec degrades to a cold start with
   a warning, never a crash** (the PRD's risk entry). "No longer matches"
   means: the spec gained an entity `initial` does not mention, or `initial`
   partially covers an entity's parameters. The result carries
   `warnings: [{"code": "initial_incomplete", ...}]` and
   `warm_started: false`. Tested by adding an entity mid-drag.

And the correction from Ground Truth, which must be in the tool description
so nobody plans against the wrong model: **`initial` is a *branch selection*
mechanism, not a speed mechanism.** Measured, the shipped solver takes 20 ms
seeded exactly at the solution and 51 ms seeded 0.4 mm away. Warm starting
saves iterations, and iterations were never the cost — the Jacobian was.
Decision 2 is what makes drag fast; `initial` is what makes it *stable*.

---

## Decision 9 — drag-to-solve: where it runs, and the proof it fits

This is the design's central risk. Four sub-questions, each answered with a
measurement.

### 9a — Where does the solve run? **On the server, over HTTP.**

- **Browser JS** — rejected by Decision 1 (two solvers).
- **Kernel worker** — rejected outright. The solver has no OCP content; routing
  it through the JSON-RPC worker adds a process hop and burns a pool slot that
  a rebuild needs. `toolkit/sketch.py` must stay importable by the *server*
  and must never import build123d (the file's existing property; the plan
  asserts it in a fresh interpreter, per the PRD-003/004 pattern).
- **Client→server WebSocket** — rejected on the extension-point contract.
  `/ws` lives in `agentcad/server/app.py`, a core this feature may not edit;
  it is server→client only (its receive loop discards everything that is not a
  disconnect) and carries no client identity. This is the same wall PRD-008
  hit with presence, and the same answer: **HTTP**.
- **Server, through the existing `routes_sketch.py` pack** — chosen.

### 9b — Does the round trip fit? **Yes, with keep-alive. Without it, no.**

`scratchpad/spike_http.py`: uvicorn + FastAPI on 127.0.0.1, one POST route
that parses a realistic sketch payload and returns solved-shaped JSON, driven
by an httpx client — transport, ASGI and JSON only, no solver.

| payload | p50 | p95 | max |
|---|---:|---:|---:|
| 10 points, 1.5 kB | 0.45 ms | 0.55 ms | 1.11 ms |
| 51 points, 8.1 kB | 0.72 ms | 0.85 ms | 1.07 ms |
| 101 points, 16 kB | 1.03 ms | 1.20 ms | 1.31 ms |
| 201 points, 33 kB | 1.67 ms | 1.77 ms | 7.27 ms |
| **51 points, new connection every frame** | **12.55 ms** | **16.47 ms** | — |

Loopback HTTP costs **under 1 ms** at the FR6 size when the connection is
reused, and **12.5 ms — 78% of the entire budget — when it is not.**
Connection reuse is therefore a **hard requirement with a named failure
mode**, not an optimization. Browser `fetch` pools HTTP/1.1 connections by
default, so the requirement is met by *not breaking it*: no `Connection:
close`, no per-frame `AbortController` that tears the socket down, no
switching the drag path to `sendBeacon`.

### 9c — The budget, assembled

| Component | Measured | Source |
|---|---:|---|
| Solve (50 entities, warm, analytic Jacobian) | **0.78 ms** | `spike_analytic.py` |
| HTTP round trip (8 kB, keep-alive) | **0.72 ms** | `spike_http.py` |
| JSON encode/decode both ways | included above | — |
| SVD rank + DOF | 0.30 ms | `spike_analytic.py` |
| Greedy dependent-set analysis | 6.35 ms | `spike_diag2.py` |
| **Drag frame, diagnostics off the path** | **≈ 1.5 ms** | sum |
| Drag frame with full diagnostics every frame | ≈ 8 ms | sum |
| **FR6 budget** | **16 ms** | PRD |

Comfortable — with one rule that follows straight from the table:
**diagnostics do not run on the drag path.** Rank and the dependent set are
functions of the *constraint set*, and a drag changes no constraints. The
solver caches the diagnostics block against a hash of the compiled residual
structure and returns the cached block on a drag frame; it recomputes on any
constraint or entity change. This is what turns 8 ms into 1.5 ms and it costs
nothing in correctness. The route takes `diagnostics: "auto" | "full" | "cached"`
so an agent can force a recomputation.

The FR5 "documented time budget" is **50 ms** for the greedy analysis, checked
against `time.monotonic` between rows; exceeding it yields
`analysis_complete: false` (Decision 7). By measurement that budget is not
reached below ~300 constraints.

### 9d — Branch stability: the mechanism, and why `initial` alone is not it

Measured (`scratchpad/`, mirror-flip probe). A triangle: `a`,`b` fixed on the
x-axis, `c` held by two distance constraints — two mirror solutions. Drag `c`
downward by seeding it at the cursor each frame (the naive "warm start from
the on-screen state" the PRD describes):

```
cursor y=  18 -> c=(23.4375,  18.7265)
cursor y=   1 -> c=(23.4375,  18.7265)
cursor y=  -1 -> c=(23.4375, -18.7265)   <-- flipped
cursor y= -18 -> c=(23.4375, -18.7265)
```

**Warm-starting from the on-screen state does not prevent the mirror flip** —
it *causes* it, because the on-screen state includes the cursor, and the
cursor crossed the branch boundary. AC2 is not satisfiable by FR4 alone. This
is the design's second measured correction to the PRD's technical approach.

The mechanism that does work: during a drag, the dragged handle is expressed
as a **weighted soft residual pulling it toward the cursor**, and every
parameter is seeded from the **previous frame's solution** — not from the
cursor. Measured with `w = 0.05` on the same triangle:

```
cursor y=  18 -> c=(23.4412, 18.7247)
cursor y=  -1 -> c=(23.4408, 18.6747)
cursor y= -30 -> c=(23.4400, 18.5981)   <-- never flips, through the whole drag
```

Zero flips across the full sweep. The point barely moves, which is **correct**:
`c` is fully determined by its two distance constraints, and dragging a
fully-constrained entity should do nothing. The naive version's "responsive"
behaviour was it teleporting to a different solution.

The soft residual forces one contract rule, and it is easy to get wrong: in
the same measurement `ok` came back **`false`** with `max_residual` climbing
to 2.43, because the drag row is an unsatisfiable "constraint". Therefore:

> **The drag residual is an objective, not a constraint. It is excluded from
> `ok`, from `max_residual`, from `n_residuals`, from the rank, from the DOF
> and from the diagnostics.** It occupies its own weighted block appended
> after the constraint rows, and every reported quantity is computed over the
> constraint rows only.

Drag weight `w = 0.05` relative to constraint rows scaled to millimetres —
recorded as a constant with the measurement beside it, and swept in the
benchmark so a future change to residual scaling cannot silently detune it.

### 9e — The frame protocol

Client side, in `sketcher.js`:

- **One request in flight at a time.** On `pointermove`, store the cursor and
  request an animation frame. On the frame, if a request is already in flight,
  do nothing (the next frame will pick up the newest cursor). Coalescing by
  frame plus single-flight is what bounds the queue; the existing
  `solveSeq` monotonic-sequence guard stays and discards stale responses.
- **The drag payload is the full spec plus `initial` plus `drag`.** No
  incremental/session protocol — the route stays stateless and
  project-independent, exactly as it is today. At 8 kB and 0.72 ms this costs
  nothing, and a stateless route has no session to leak, expire or invalidate.
- **On `pointerup`, one final non-drag solve** with `diagnostics: "full"`, so
  the DOF chip and any conflicts are refreshed against the settled geometry.
- **On error, the drag ends and the model reverts to the last good solution.**
  A drag that silently keeps a divergent frame on screen is how a sketch gets
  corrupted.

Server side, one change inside the route pack: the handler becomes a **sync
`def`**, so FastAPI runs it in the threadpool instead of on the event loop.
Today's `async def` calls a synchronous solver directly on the loop; at 51 ms
per call that blocks the WebSocket event channel and every other request. At
0.78 ms it would be survivable, but a 300-constraint sketch with `diagnostics:
"full"` is not, and the fix is one keyword.

---

## Decision 10 — emission: one server-side emitter, endpoint-anchored, with a closure gate

The PRD leaves the emitter's location open ("a design-spec decision with a
fallback of keeping emission in JS"). **Decision: move it server-side**, to a
new `agentcad/core/sketch_emit.py`, exposed as `emit` on the sketch route and
returned alongside the solution.

Reasons, in order: the GUI and agents must produce byte-identical code for the
same spec (the PRD's whole "one solver, both layers" thesis, applied to the
second layer); an emitter in Python is testable by the pytest suite that
already rebuilds emitted code through the kernel (AC1 is a golden test, and a
golden test against a JS emitter would need a browser); and the emitter needs
the exact solved values, which live on the server.

### The rounding rule — measured, and today's rule is unsafe

`scratchpad/spike_emit.py`. A three-segment profile (one arc, two lines) with
non-round solved coordinates, emitted with the arc as a centre-parametrized
`CenterArc(center, r, θ₁, sweep)` and the lines as explicit endpoints, at
various decimal places, then `make_face()`:

| decimals | endpoint gap between the arc's derived end and the line's start | `make_face()` |
|---:|---:|---|
| 6 (**what `fmtNum` does today**) | 7.58e-7 mm | **ValueError: Face can only be created with closed wires** |
| 7 | 3.71e-8 mm | OK |
| 8 | 6.23e-9 mm | OK |
| 9 | 3.15e-10 mm | OK |
| full precision | 0 | OK |

The threshold is OCCT's ~1e-7 mm vertex tolerance, and the current emitter's
6-decimal rounding sits **just** on the wrong side of it. The failure does
**not** reproduce on a tidy profile: the same test with round numbers
(a 40 × 16 slot) closes at 3 decimals with zero area error, which is exactly
how this bug reaches a reviewer's machine only after it reaches a user's.

A second measurement isolates the cause. Emitting the same arc as an
**endpoint-anchored** `RadiusArc(start, end, r)` — where the arc and both
adjacent lines are literally the same rounded coordinate pair — closes at
**every** precision tested, including 3 decimals. But at 3 decimals the
resulting area is off by 0.3%, so endpoint anchoring fixes *closure*, not
*fidelity*.

**Three rules, each traceable to a row above:**

1. **Chain vertices are emitted once and shared.** A vertex where two curves
   meet is a single formatted literal, referenced by both. Derived endpoints
   (an arc's `start`/`end`) are formatted *from the solved point*, never
   recomputed by the reader from a rounded centre/radius/angle.
2. **Emission precision is 9 decimals** (`%.9g`-equivalent), up from 6. It is
   below every measured failure threshold with two orders of margin and still
   reads as a number a human will edit.
3. **The emitter runs a closure gate before it returns.** For every chain it
   emits, it computes the maximum vertex-to-vertex gap *from the formatted
   literals* and refuses to emit a `make_face()` when that gap exceeds
   **1e-8 mm**, returning a `validation_error` naming the junction. Emitting
   code that will not rebuild is the one failure this feature must not
   produce, and it is now impossible to produce without tripping an assertion
   in the emitter's own tests.

### The entity → build123d mapping (FR9, FR11)

| Solver entity | Emitted | Note |
|---|---|---|
| line chain | `Polyline(...)` or per-segment `Line(a, b)` | shared vertex literals |
| arc (in a chain) | `RadiusArc(start, end, r, short_sagitta=…)` | endpoint-anchored; `short_sagitta` from the solved sweep |
| arc (3-point) | `ThreePointArc(a, m, b)` | when the spec was authored 3-point |
| arc tangent to the previous segment | `TangentArc(start, end, tangent=…)` | only when a `tangent` constraint pins it |
| full circle | `Circle(radius=…)` under `Locations(...)` | v1 behaviour, unchanged |
| ellipse | `Ellipse(x_radius, y_radius)` under `Locations` + `rotation` | Phase 2 |
| elliptical arc | `EllipticalCenterArc(center, x_radius, y_radius, start_angle, end_angle, rotation=…)` | Phase 2 |
| spline | `Spline(p0, p1, …)` | interpolating; see Decision 3 |
| slot | `SlotCenterToCenter(center_separation, height, rotation=…)` under `Locations` | **not a `BuildLine` curve** |

Verified against the pinned build123d **0.11.1**: all of `Line`, `CenterArc`,
`RadiusArc`, `ThreePointArc`, `TangentArc`, `EllipticalCenterArc`, `Ellipse`,
`Spline`, `SlotCenterToCenter`, `SlotOverall`, `SlotArc` exist with the
signatures above.

The slot row is a trap worth naming: `SlotCenterToCenter` is a **BuildSketch
face object centred at the origin**, not a curve that can join a `BuildLine`
chain. A standalone slot emits as a sketch object under `Locations`; a slot
that participates in a larger closed profile emits as its **compiled
primitives** — two `Line`s and two `RadiusArc`s — which the compilation in
Decision 5 already produced. The emitter picks by whether the slot's
sub-entities carry constraints tying them to anything outside the slot.

---

## Decision 11 — round-trip persistence: a structured block in the script

FR10 wants a sketch to reopen with its constraint spec intact. The PRD leaves
the shape open (structured comment vs sidecar file) and requires divergence
detection either way.

**Decision: a structured block in the script**, following the `push_pull`
precedent exactly (`core/tools_facemod.py`'s `PUSH_PULL_MARKER` — an
auto-generated, human-visible, freely-editable block appended to the script).

```python
# --- agentcad sketch "profile1" (auto-generated; edit or remove freely) ---
# agentcad-sketch-spec: {"v":1,"entities":{...},"constraints":[...],"plane":{...}}
# agentcad-sketch-hash: 3f2a…   # sha256 of the emitted code block below
def _agentcad_sketch_profile1():
    with BuildSketch(Plane.XY) as _sk:
        with BuildLine():
            ...
    return _sk.sketch
```

Why in the script rather than a sidecar:

- **Single-file portability.** The PRD's non-goals say "the part script remains
  the only artifact". A sidecar makes the script non-self-describing and adds
  a file that branching, restore, undo and merge would each need to reason
  about — four features that currently get sketches for free.
- **It rides every existing mechanism.** `git add -A` tracks it, so branch,
  merge, restore, undo and the proposal packet's script diff all work with no
  new code, exactly as PRD-003 argued for `SPECS`.

Why the hash, and what divergence means:

- **The code is the source of truth for geometry. The spec block is
  provenance.** If the hash of the emitted block no longer matches
  `agentcad-sketch-hash`, the user hand-edited the geometry, and the sketcher
  **opens read-only with a divergence banner** offering two explicit choices:
  *re-solve from the spec* (discards the hand edit) or *discard the spec*
  (keeps the hand edit, drops the constraints). It never silently overwrites —
  the PRD says so, and it is the same "orphan rather than guess" bias PRD-008
  established for anchors.
- A block whose spec fails to parse is `unverified`, not "no sketch": the
  banner says the spec is unreadable, and the code is left alone.

---

## Decision 12 — sketch-on-face

FR7/FR8. Two new pieces and one reused one.

**Reused:** the viewport already picks faces (`viewport.js` `pickAt` →
`faceIndex` via the `.faces.u32` sidecar; `main.js` `selectFace` → the
`face_info` tool). Face-index semantics are already defined by
`toolkit/facemod.faces_in_mesh_order` and already documented as shiftable.

**New worker handler pack** — `agentcad/kernel/handlers/sketchplane.py`,
exporting `sketch_plane`. `face_info` today returns `planar`, `normal`,
`center`, `area_mm2`, `n_faces` — a normal and a centre define a plane but
**not a basis**, and without a deterministic in-plane X axis every emitted
sketch-on-face coordinate is arbitrary. `sketch_plane` returns
`{origin, x_dir, y_dir, normal, refs: [...]}` where `x_dir` comes from
build123d's `Plane(face)` (deterministic for a given face) and `refs` are the
face's own boundary edges expressed in plane coordinates as
`{kind: "line"|"circle"|"arc"|"other", ...}`.

Projecting the face's **boundary** rather than intersecting the whole part
with the plane is deliberate: it is what a user means by "sketch on this
face", it is what FreeCAD's external geometry gives, it is bounded in size,
and it cannot produce the degenerate near-tangential intersections a
whole-part section can. Curves that are neither line nor circle come back
`kind: "other"` with a polyline approximation and are **not** constraint
targets — a documented gap, not a silent one.

**Reference entities are fixed.** They enter the spec with `fixed: true` /
`fixed_r: true` and contribute **zero parameters** — so they add no DOF, can
never be dragged, and can never appear in a conflict report as something the
user could change.

**Emission (FR8)** records the face reference the way `push_pull` does — a
visible, editable, annotated call — plus the caveat inline:

```python
# --- agentcad sketch "top_profile" on face 12 of build(p) ---
# NOTE: face indices are mesh-order ordinals; a parameter change that alters
# the part's topology can renumber them. Re-pick the face if the rebuild moves.
```

Not hidden, not silently repaired. The same bias, and the same wording style,
as PRD-008's anchor caveats.

---

## Decision 13 — backward compatibility (FR3)

The v1 contract is frozen, and the plan's first slice makes it mechanical:

- **A v1 corpus test file** capturing every one of the 17 constraint types plus
  the three shipped tests, each asserting solved coordinates to `1e-9`. It is
  written **before** the rewrite, against the shipped solver, and the rewrite
  must keep it green. This is the compatibility harness, in the same spirit as
  "the test suite is the build123d compat harness".
- **Result keys are a superset.** `ok`, `max_residual`, `n_params`,
  `n_residuals`, `dof`, `nfev`, `solve_ms`, `points`, `circles` all keep their
  names and meanings. `dof` changes *value* for over-constrained sketches
  (from a meaningless negative to `n_par − rank`) — that is a bug fix, it is
  called out in the changelog, and `n_params − n_residuals` remains derivable
  from the two fields that are still there.
- **New keys are additive**: `arcs`, `ellipses`, `splines`, `slots`,
  `diagnostics`, `warnings`, `warm_started`, `emit`.
- **`tangent_line_circle` / `tangent_circles` keep working for ever**
  (Decision 4).

---

## Decision 14 — agent surface and routes

**No new tools** (the PRD is explicit). `solve_sketch` grows:

- `entities` gains `arcs`, `ellipses`, `splines`, `slots`
- `constraints` gains `tangent`, `symmetric`, `equal_length`, `concentric`
- `initial` becomes real
- `drag` (optional, `{point, x, y, weight?}`) — the soft-pull block
- `diagnostics` (optional, `"auto" | "full" | "cached"`)
- `emit` (optional, `false | "buildline" | "function"`) — returns the code
- the result gains per-entity solved geometry, `diagnostics`, `warnings`,
  `warm_started` and `emit`

`routes_sketch.py` whitelists the new keys explicitly (the route-pack contract
forbids `**body`; the registry rejects unknown args) and its handler becomes a
sync `def` (Decision 9). No new routes, no new events — sketching is
client-side until emission, and emission rides the normal
`rebuild_started`/`rebuild_finished` flow.

---

## Decision 15 — UI surfaces

All inside `frontend/js/sketcher.js` and `frontend/css/app.css`:

- **Toolbar**: arc (centre, 3-point, tangent), spline, ellipse, slot, plus a
  construction/reference toggle.
- **Constraint palette**: tangent, symmetric, equal, concentric, alongside the
  existing eight.
- **Drag**: `select` tool gains pointer-drag on points and on curve handles
  (arc radius/endpoints, ellipse axes, spline control points), wired to the
  frame protocol in Decision 9e.
- **The DOF chip** replaces today's `solved · dof N` string:
  `fully constrained` (green) · `3 DOF` (neutral, click → pulse the free
  entities) · `over-constrained (2)` (amber for redundant, red for
  conflicting; click → highlight the whole set).
- **Sketch on face**: an entry on the existing face context action, opening
  the sketcher on that plane with reference geometry ghosted.

Every UI slice carries real-browser verification (screenshot, zero console
errors), per the definition of done.

---

## Decision 16 — explicitly out of scope

Parabola/hyperbola primitives (PRD non-goal — elliptical arcs cover the
CAD-practical cases). 3D sketching. Auto-constrain inference (PRD-029).
On-curve spline point constraints (Decision 3). A sketch file format
(Decision 11). Sparse Jacobians — **measured unnecessary** through 400
parameters (Decision 1), and the benchmark is what keeps that true. Constraint
decomposition into independent clusters — the same measurement says a single
dense system is fast enough; revisit only if the benchmark reddens.

---

## Risks the implementer must verify empirically

Six. The first four are settled by the measurements above and are listed so a
reviewer can re-run them; the last two are **open** and each begins its slice
with a spike.

1. ~~Can a Python solver hit 16 ms at 50 entities?~~ **Settled: 0.78 ms** with
   an analytic Jacobian (`spike_analytic.py`). Re-measure in the benchmark
   test on every change.
2. ~~Does the HTTP round trip fit?~~ **Settled: 0.72 ms** with keep-alive,
   **12.55 ms without** (`spike_http.py`). The no-keep-alive number is the one
   to watch.
3. ~~Does rank analysis name the right constraint?~~ **Settled: only with
   declaration-order greedy selection.** Pivoted QR blamed an innocent
   constraint in 2 of 3 cases (`spike_diag.py` vs `spike_diag2.py`).
4. ~~Does warm-starting prevent mirror flips?~~ **Settled: no.** Seeding the
   dragged point at the cursor *causes* the flip; a weak-pull residual seeded
   from the previous frame prevents it, at the cost of excluding that row from
   every reported quantity.
5. **OPEN — browser `fetch` latency at drag rate.** Every HTTP number above
   was measured from Python httpx. Chrome's `fetch` has its own scheduling,
   and `sketcher.js` runs inside a page that also renders Three.js. **Spike
   before the drag slice:** instrument a real browser session, 200 synthetic
   drag frames against the real server, record p50/p95/max end-to-end from
   `pointermove` to re-render, and record whether the connection was reused.
   If p95 exceeds 16 ms the fallback is client-side prediction (render the
   dragged handle immediately, reconcile on the response) — and that fallback
   must be *chosen with the number in hand*, not pre-built.
6. **OPEN — spline fidelity.** The solver models a spline as its through
   points; build123d's `Spline` interpolates them with its own end conditions.
   **Spike before the spline slice:** build the same point list through
   `Spline`, sample the real curve between control points, and measure the
   deviation from the solver's model. If it exceeds the emission tolerance,
   fall back to `Bezier` and document the semantics change.
7. **OPEN — elliptical tangency conditioning.** Tangency to an ellipse has no
   closed-form point-to-curve distance; it needs an auxiliary parameter (the
   tangency point's eccentric anomaly) and an extra residual. **Spike before
   the ellipse slice:** implement it, and measure convergence from 20
   randomized starts on a line–ellipse tangency. If it converges below 90%,
   ship ellipses *without* tangency in Phase 2 and say so, rather than
   shipping a constraint that fails one time in five.

---

## Gotchas this feature adds to AGENTS.md

- **The solver's cost is the Jacobian, not the iterations.** A finite-
  difference Jacobian costs `n_par + 1` full residual evaluations; the shipped
  prototype spent 92% of a 51 ms solve there. Every residual must ship its
  analytic derivative (`df`) with a central-difference test beside it. Adding
  a residual without `df` silently reintroduces the O(n²) cost.
- **`dof` is `n_params − rank(J)`, never `n_params − n_residuals`.** The row
  count reports a negative DOF for any redundant constraint — a quantity with
  no meaning that the old GUI rendered as `dof -1`.
- **Blame the *later* constraint: dependent-set analysis walks residual rows in
  declaration order.** Column-pivoted QR is the textbook method and it blamed
  an innocent original constraint in 2 of 3 measured cases, because pivoting
  selects by column norm, an artifact of residual scaling. Never claim the set
  is unique; `conflicting` is *a* dependent set and the docs say so.
- **`over_constrained` is not an error; unsatisfiable is.** A redundant but
  consistent constraint solves fine (measured `max|f| = 3.6e-18`) and must
  return `ok: true` with `redundant: [...]`. Only a non-empty `conflicting`
  (equivalently `max_residual > 1e-7`) raises `ValidationError`.
- **The drag residual is an objective, not a constraint.** It is excluded from
  `ok`, `max_residual`, `n_residuals`, the rank, the DOF and the diagnostics.
  Including it makes every drag of a fully-constrained entity report
  `ok: false` with a growing residual (measured: 2.43 after a 48 mm drag).
- **Warm-starting from the on-screen state causes mirror flips, it does not
  prevent them.** Seeding the dragged point at the cursor flips the branch the
  moment the cursor crosses it (measured: `+18.7265 → −18.7265`). Seed from
  the *previous solution* and pull toward the cursor with a weak residual.
- **HTTP keep-alive is load-bearing on the drag path.** A reused loopback
  connection costs 0.72 ms; a fresh one costs 12.55 ms p50 / 16.47 ms p95 —
  78% of the entire 16 ms budget, before any solving.
- **Emitted arc chains must share vertex literals and round to 9 decimals.**
  A centre-parametrized arc emitted at the old 6 decimals leaves a 7.6e-7 mm
  gap and `make_face()` raises "Face can only be created with closed wires" —
  and it only reproduces on non-round coordinates, so a tidy test profile
  passes. The emitter gates on a measured 1e-8 mm closure tolerance.
- **`SlotCenterToCenter` is a BuildSketch face at the origin, not a BuildLine
  curve.** A slot inside a larger closed profile emits as its compiled
  primitives (two lines, two arcs), never as the sketch object.
- **`toolkit/sketch.py` runs in the SERVER process and must never import
  build123d.** It is the only toolkit module besides `specs.py` with that
  property (`facemod`, `fillet`, `shell`, `threads`, `surfacing` and
  `sheetmetal` all import `b3d` and run kernel-side). Asserted in a fresh
  interpreter.
- **`numpy`/`scipy` are direct dependencies now.** They were previously only
  transitive through build123d — a package the server process is forbidden to
  import.
- **A compiled sub-entity never gets blamed.** Slot machinery carries the
  slot's own `con_index`; a conflict inside it reports the slot, with
  `details.origin` naming `slot1.arc_a`.

---

## Acceptance criteria → design

| AC | Where it is satisfied | Measurement obligation |
|---|---|---|
| AC1 slotted cam profile solves; emitted code rebuilds to matching metrics | Decisions 3, 5, 10 | golden test: emitted script rebuilds through the kernel; area/bbox within 1e-6 rel of the solved profile |
| AC2 100-step drag, zero mirror flips; FR6 thresholds | Decisions 9d, 2 | flip count == 0; benchmark asserts warm ≤ 16 ms p50, cold ≤ 250 ms at 50 entities, with the measured number printed |
| AC3 redundant constraint → `over_constrained` naming the added one | Decision 6 | the four-case table above, as tests |
| AC4 under-constrained reports `dof > 0` + GUI chip | Decisions 6, 15 | rank-based DOF test + browser session |
| AC5 sketch-on-face references a projected edge, rebuilds green | Decision 12 | test on the prototyping enclosure + browser session |
| AC6 v1 corpus identical; suite green | Decision 13 | v1 corpus written first, to 1e-9; full suite ≥ 1441 passed |
| AC7 browser: draw, constrain, drag, finish; zero console errors | Decisions 9e, 15 | real-browser session with screenshots |

---

## Measurement appendix

Every number in this document is reproducible. Machine: Apple M1 Max, macOS
Darwin 25.6.0, Python 3.12, numpy 2.5.1, scipy 1.18.0, build123d 0.11.1.

| Script | Establishes |
|---|---|
| `bench_sketch.py` | shipped solver cold/warm cost 4→100 segments; residual and FD-Jacobian unit costs |
| `spike_analytic.py` | analytic-Jacobian LM cost 10→200 segments; SVD diagnostics cost |
| `spike_http.py` | loopback HTTP round trip with and without keep-alive |
| `spike_diag.py` | rank vs row-count DOF; pivoted-QR blame failures |
| `spike_diag2.py` | declaration-order greedy blame; greedy vs SVD cost |
| `spike_emit.py` | emission closure threshold; endpoint-anchored vs centre-parametrized arcs |
| mirror-flip probe | branch flip under cursor seeding; stability under weak-pull + previous-frame seeding |

These live in the design session's scratchpad. **Slice 1 of the plan promotes
them into `tests/test_sketch_bench.py`**, so the numbers become regressions
rather than folklore.
