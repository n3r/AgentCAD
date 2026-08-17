# Authoring Parts

A part is a plain Python script using [build123d](https://build123d.readthedocs.io).
The base contract needs no AgentCAD imports — such a script is portable and
runs anywhere build123d does. AgentCAD executes it in the kernel worker and
renders, measures, and exports what `build(p)` returns.

Three *optional* extensions build on that contract: the
[part-authoring toolkit](#the-part-authoring-toolkit) (`safe_fillet`,
`safe_shell`, `safe_bool`, the sketch solver, threads), the
[`connectors(p, part)`](#declaring-connectors-for-mates) hook for assembly
mates, and a [`SPECS`](#design-specs-specs) list of executable design
assertions. All are backward-compatible — a script that uses neither behaves
exactly as before — but a script that imports the toolkit is **no longer
plain-build123d portable**: `from agentcad.toolkit import …` requires the
`agentcad` package on the path (it is present in the app venv, so scripts run
fine in AgentCAD; they just won't run in a bare build123d environment).

## The contract

Every part script defines exactly two things:

```python
from build123d import *

PARAMS = {
    "width":  {"default": 80.0, "min": 10.0, "max": 300.0, "unit": "mm",
               "description": "Plate width"},
    "hole_d": {"default": 6.0,  "min": 1.0,  "max": 50.0,  "unit": "mm",
               "description": "Center hole diameter"},
}

def build(p):
    with BuildPart() as part:
        Box(p.width, 60, 8)
        Hole(radius=p.hole_d / 2)
    return part.part
```

Rules (enforced by the kernel — violations return `contract_error`):

- `PARAMS` is a dict of typed parameter specs. `default` is required;
  `description` is optional but strongly recommended. An optional `"type"`
  selects the kind of value — `"number"` (the default), `"int"`, `"bool"`,
  `"enum"`, or `"string"`:
  - **number / int**: `min`, `max`, `unit` are optional but recommended —
    `min`+`max` gives the UI a slider, and the bundled examples treat
    min/max/unit/description as mandatory style. `int` accepts only integral
    values (`3.0` coerces to `3`; `3.5` is rejected).
  - **bool**: `default` must be a real `True`/`False` (the UI shows a checkbox).
  - **enum**: requires `choices`, a non-empty list of strings and/or numbers;
    `default` and overrides must be members (the UI shows a dropdown).
  - **string**: `default` must be a string; optional `max_len` (default 200).
  - `min`/`max` are only legal on number/int specs, `choices` only on enum,
    `max_len` only on string.

  ```python
  PARAMS = {
      "size":   {"default": 20.0, "min": 5.0, "max": 80.0, "unit": "mm",
                 "description": "Cube edge"},
      "ribbed": {"default": True, "type": "bool", "description": "Add ribs"},
      "finish": {"default": "raw", "type": "enum",
                 "choices": ["raw", "anodized"], "description": "Surface finish"},
      "label":  {"default": "acme", "type": "string", "max_len": 12,
                 "description": "Engraving text"},
  }
  ```
- Numeric parameter overrides are **clamped** to `[min, max]` with a warning
  (never an error), so agents and sliders can push bounds safely. Non-numeric
  overrides must match their spec exactly — a wrong-typed value or a
  non-member enum choice is a `contract_error`.
- `build(p)` receives an attribute namespace of resolved values (`p.width`)
  and must return a build123d `Part`, `Solid`, or `Compound`. Returning the
  `BuildPart` builder itself also works — AgentCAD takes `.part`.
- Units are millimeters; angles in degrees; mass uses the part's material
  density (see `agentcad/core/materials.py` for the built-in table).

Execution environment: fresh module namespace per rebuild, 120 s build
timeout, stdout redirected (use exceptions, not prints, to signal problems —
tracebacks come back with the failing line number). Same script + same
parameters → identical geometry, always.

## Multi-solid parts and SOLID_LABELS

A part whose `build(p)` returns a multi-solid `Compound` reports per-solid
metrics: `metrics.solids` is an index-ordered list of
`{label, volume_mm3, mass_g, bbox, center_of_mass}` alongside the whole-part
aggregates. Optionally name the solids with a module-level

```python
SOLID_LABELS = ["body", "lid"]   # applied by index; must be a list of strings
```

Unnamed solids fall back to `solid_0`, `solid_1`, …; extra labels beyond the
actual solid count are ignored with a warning (anything but a list of strings
is a `contract_error`). Labels exist so agents and the `set_solid_materials`
tool can address individual solids: assigning `{"lid": "steel_a36"}` gives
that solid its own density, and the part's aggregate `mass_g` becomes the sum
of per-solid masses. Single-solid parts are unchanged.

## Design for parameter robustness

Your part must stay **manifold at every parameter extreme**, because sliders
and agents will go there (the example test suite rebuilds every part at every
parameter's min and max). The reliable pattern is to derive dependent
dimensions defensively inside `build`:

```python
def build(p):
    corner = min(p.corner_r, min(p.length, p.width) / 2 - 0.1)  # can't exceed half-width
    wall = min(p.wall, p.height / 2 - 0.5)                       # shell must stay open
    ...
```

Clamping in `build()` is invisible to callers, so prefer tightening
`min`/`max` in `PARAMS` when the constraint is expressible there; use inline
guards for constraints that couple two parameters.

**Named sizes are not a script concept.** A part's family — S/M/L, left/right,
three bolt counts — lives in the *manifest* as
[configurations](user-guide.md#configurations), declared with
`set_part_configs` and validated against this `PARAMS` spec. A script never
mentions them: it just has to build at every value they can name, which is the
same robustness this section asks for. Feature variation is expressible the
ordinary way, as script logic branching on an enum parameter a configuration
sets.

## Common OCCT failure modes

| Symptom | Cause and fix |
|---|---|
| `Failed creating a fillet with radius X` | Radius exceeds what the adjacent faces allow. Reduce it, fillet fewer edges, or scale the radius from the smallest coupled dimension. |
| `There are no suitable edges for chamfer or fillet` | Your selector matched no edges (or the wrong ones). Print-free debugging: tighten selectors like `.filter_by(Axis.Z)`, `.group_by(Axis.Z)[-1]`. |
| Boolean produces zero-volume/invalid result | Tool doesn't actually intersect the base, or surfaces are exactly coincident. Offset by ≥0.01 mm or overlap tools slightly. |
| `Hole` cuts nothing | `Hole` needs existing material at its location; check the active `Locations` context. |
| Shell/`offset` fails | Wall thickness too large for the cavity, or the opening face selector matched nothing — pick the face with `part.faces().sort_by(Axis.Z)[-1]`. |

## The part-authoring toolkit

`agentcad.toolkit` collects the helpers that survive the failure modes above
so a script keeps producing geometry instead of raising. Import only what you
need:

```python
from agentcad.toolkit import safe_fillet, safe_shell, safe_bool  # robustness
from agentcad.toolkit import sketch                              # constraint solver
from agentcad.toolkit import patterns                            # point sets + shape patterns
from agentcad.toolkit import holes                               # ISO/ASME hole wizard
from agentcad.toolkit import features                            # rib / boss / draft
from agentcad.toolkit import threads                             # ISO threads / fasteners
from agentcad.toolkit.sheetmetal import SheetPart                # folded + flat, one spec
```

Each robustness helper returns a **warning string** (or `None`) alongside its
result — the warning is the honest record of any fallback it took, and you
should let it reach the user rather than swallow it.

| Helper | Signature → returns | What it does |
|---|---|---|
| `safe_fillet` | `safe_fillet(part, edges, radius) → (part, achieved_radius, warning?)` | Applies `radius`; on OCCT failure it consults `max_fillet` and binary-searches down to the largest radius that actually succeeds. The warning names the radius it fell back to. |
| `safe_shell` | `safe_shell(part, thickness, opening_faces=None, kind=Kind.ARC) → (part, warning?)` | Tries `offset()` with `Kind.ARC`, then `Kind.INTERSECTION`, then fewer opened faces, then an **approximate** boolean-subtract shell. The fallback warning states plainly that wall thickness can be ~20% thin on curved/slanted faces — surface it. |
| `safe_bool` | `safe_bool(a, b, op="fuse", fuzzy=1e-4) → (shape, warning?)` | The plain build123d operator first; on a raise, an invalid/empty result, or a `fuse` that leaves disjoint solids, it retries via OCCT `BRepAlgoAPI` with a fuzzy tolerance (`fuzzy`, then `10·fuzzy`) to close sub-tolerance gaps. `op` is `fuse`\|`cut`\|`common`. |

```python
from agentcad.toolkit import safe_fillet

def build(p):
    with BuildPart() as part:
        Box(p.length, p.width, p.thickness)
    solid, r, warn = safe_fillet(part.part, part.part.edges().filter_by(Axis.Z),
                                 radius=p.corner_r)
    if warn:
        print(warn)            # redirected to stderr; shows in kernel logs
    return solid
```

### Patterns

`agentcad.toolkit.patterns` covers two different things, and the difference
matters.

**Point sets are pure arithmetic** and are what you want for holes. They are
rounded to 9 decimals so trig noise (`cos(90°) = 6.1e-17`) never reaches a
stored coordinate:

```python
patterns.bolt_circle(r=45, n=8, start_deg=0.0)   # -> [(x, y), ...]
patterns.grid(nx=4, ny=2, dx=50, dy=70)          # -> row-major, centred
```

These are **not** `PolarLocations`: a point set translates copies, a
`PolarLocations` block also *rotates* each copy, and the two tessellate to
different bytes. If your group needs rotating, build it in its own frame and
rotate the result — asking `grid` for rotated coordinates rounds an irrational
number (`construction/gusset_plate`'s diagonals do exactly this).

**Shape patterns copy a solid that is already in the part**, which is why
`count` is the *total* instance count the way every CAD package counts it:

```python
part, warn = patterns.linear(part, seed, direction=(1, 0, 0), count=5, spacing=20)
part, warn = patterns.polar(part, seed, axis=Axis.Z, count=6, span_deg=360)
part, warn = patterns.mirror(part, plane=Plane.YZ)
```

The instance that leaves the seed where it already is gets skipped — that is
the seed. Re-adding it is safe (one valid solid, identical volume) but not
byte-free, and `count=1` is a genuine no-op with a warning. `span_deg < 360` is
inclusive — three instances over 180° sit at 0/90/180, which is what a CAD user
means and not build123d's `PolarLocations` default.

**`polar(..., radius=r)` is the exception, and it warns.** A radius translates
*every* instance onto the circle, so no placement leaves the seed alone: all
`count` are added and the seed stays where you built it, giving `count + 1`
copies. Author the seed **at the axis** and pass `radius`, or build it on the
circle and use `radius=None`. (Skipping index 0 there used to drop the instance
at angle 0 while leaving the seed off the circle — with the right volume, one
valid solid and no warning at all.)

Every row of `patterns.instances(part)` carries the instance's `center`, and
the helpers assert those centres against the pattern that was asked for —
equidistant from the axis and evenly spaced for `polar`, one step along one
line for `linear`. A pattern can be one valid solid with exactly the right
added volume and every instance in the wrong place; the volume checks cannot
see that and the centres can.

#### Why every one of them returns a warning

**OCCT does not fail on a badly placed feature.** Measured through the kernel
worker (changelog 0149): a ⌀4.2 tool placed entirely off the part cuts in
0.89 ms, leaves the volume **exactly** unchanged, reports `is_valid True` and
raises nothing. "Overlap and degenerate spacing produce warnings, never silent
geometry" is therefore not something the kernel gives you — the helper has to
measure engagement, and measuring costs:

| probe | cost per instance | always on? |
|---|---:|---|
| bounding-box overlap | **0.014 ms** | yes |
| `(part & tool).volume` | **2.1–2.4 ms** | only `verify="exact"` |

So the tier is in the API. The free tier also checks the solid count, the
whole-operation volume delta and the seed extent against the spacing;
`verify="exact"` adds the intersection probe and reports `engaged_mm3` per
instance. **Warnings name instance indices, never a count** — `instance(s) [2]
do not reach the part` — and `patterns.instances(part)` reads the full
per-instance report back off the returned shape.

The bbox tier is a *screen*, not a verdict: it can call a near miss "engaged",
but it never calls a real hit "missed". The direction of that error is the
load-bearing part.

An **impossible** request raises rather than warning: `count < 1`,
`spacing <= 0`, a zero-length direction, a span outside `(0, 360]`. Inside a
part script that surfaces as a normal `script_error` with `details.line`.

### Holes — the wizard

`agentcad.toolkit.holes` places holes **by intent**. The diameter comes from a
vendored standards table (never from arithmetic in the helper), and every call
leaves a machine-readable **record** that reaches `get_part`, the rebuild
result and the drawing callouts.

```python
part, records, warn = holes.clearance(part, points, "M5", fit="medium")
part, records, warn = holes.tapped(part, points, "M6", depth=12)
part, records, warn = holes.counterbore(part, points, "M8")
part, records, warn = holes.countersink(part, points, "M6")
part, records, warn = holes.drill(part, points, 18.0)          # no table
part, records, warn = holes.clearance(part, points, "1/4", std="ansi")
```

Shared keywords: `plane=`, `depth=` (omit for a through hole), `thru=`,
`std="iso"|"ansi"`, `fit=` and `verify=` (the pattern tiers above).

**`plane` is a predicate, never an ordinal.** It takes a build123d `Plane`, or
one of six names resolved *on every rebuild* to the extreme planar face along
that axis (largest area among coplanar candidates, then lowest centre — a
documented tie-break). A face **index** would renumber on any topology change;
a name and a literal basis do not. `points` are `(u, v)` in the resolved
plane, and the plane's origin is the part origin projected onto it, so they
stay part coordinates:

| name | drills along | `(u, v)` means |
|---|---|---|
| `top` | −Z | `(x, y)` at z = max |
| `bottom` | +Z | `(x, −y)` at z = min |
| `front` | +Y | `(x, z)` at y = min |
| `back` | −Y | `(−x, z)` at y = max |
| `right` | −X | `(y, z)` at x = max |
| `left` | +X | `(−y, z)` at x = min |

A name that resolves to nothing (a lathe part with no planar top) raises,
naming the reason — honouring it approximately would drill into a curve.

**`holes.drill` is the one with no table behind it.** A structural bolt hole
often has no fastener row: `construction/gusset_plate` drills 18 mm for an M16
(EN 1090 clearance — none of ISO 273's 17.0/17.5/18.5) and the diameter is a
swept parameter. `drill` takes millimetres and its record carries **no `size`
and no provenance**, because printing a standard's name on a number the
standard did not supply is worse than printing none.

**`tapped` bores the tap drill and records the thread.** A tapped hole on a
drawing is a callout, not a helix. `thread="real"` additionally fuses real ISO
thread geometry — it needs a `depth`, costs ~9k triangles per hole, and bores
at `root_radius` rather than the tap drill (boring at the physical tap drill
buries the ridges: valid, fast, and invisible). **`countersink` always passes
its angle explicitly**: build123d's `CounterSinkHole` defaults to 82°, an ASME
default that would otherwise arrive inside an ISO-labelled call.

#### Designation symbology

Emitted per the hole's **declared standard**; the glyphs are ISO 129 /
ASME Y14.5 and are shared, the numbers and the thread designation are what
changes. Every ISO length prints millimetres, every ASME length prints inches
— one named conversion (`hole_standards.in_designation_units`) sits between
them, because everything geometric in AgentCAD is millimetres.

| family | ISO | ASME |
|---|---|---|
| clearance | `⌀5.5` | `⌀0.217` |
| clearance, blind | `⌀5.5 ↧6` | `⌀0.217 ↧0.25` |
| tapped | `M5×0.8 - 6H ↧12` | `10-24 UNC - 2B ↧0.5` |
| counterbore | `⌀5.5 ⌴⌀9.5↧5.4` | `⌀0.217 ⌴⌀0.375↧0.213` |
| counterbore, blind | `⌀5.5 ↧6 ⌴⌀9.5↧5.4` | `⌀0.217 ↧0.25 ⌴⌀0.375↧0.213` |
| countersink | `⌀5.5 ⌵⌀10.4×90°` | `⌀0.217 ⌵⌀0.41×82°` |
| countersink, blind | `⌀5.5 ↧6 ⌵⌀10.4×90°` | `⌀0.217 ↧0.25 ⌵⌀0.41×82°` |

**A blind hole always states its depth, and each `↧` qualifies the `⌀` group
it follows.** A counterbore has two of them: `⌀5.5 ↧6 ⌴⌀9.5↧5.4` is a 6 mm
deep ⌀5.5 hole under a 5.4 mm deep ⌀9.5 pocket. `clearance`, `counterbore`
and `countersink` used to omit the hole depth entirely — a blind ⌀9 printed
`⌀9`, which a shop makes as a through hole.

ISO 273 names its three fits *fine / medium / coarse*; ASME B18.2.8 names them
*close / normal / loose*. **Both spellings are accepted** and canonicalized to
the requested `std`. The tables themselves are queryable without building
anything — `hole_standards {family?, size?, std?}` (see `docs/agent-api.md`)
answers out of the same files the geometry read, with the sources **that row**
was transcribed from and `corroborated`, which is true only when two or more
independent sources back it *and agree*.

**The counterbore diameter is a shop rule, not a standard.** The published
counterbore charts disagree by up to 0.75 mm on one M8, so
`hole_standards.cbore` returns corroborated *head* geometry — the standard part
of the answer — and applies a named clearance rule to it for the bore. Pass
`cbore_d=` / `cbore_depth=` to use your shop's numbers; the record then carries
yours.

#### The records, and where they live

One call makes **one group record**, so a pattern of a wizard hole is one hole
group with `count: n` and `n` positions, not `n` unrelated records:

```python
{"id": "h0", "family": "tapped", "standard": "iso",
 "designation": "M5×0.8 - 6H ↧12", "designation_base": "M5×0.8 - 6H",
 "size": "M5", "d": 4.2,
 "count": 8, "positions": [...], "centers": [...],   # plane-local, then global
 "axis": [0, 0, -1], "plane": {...}, "depth_mm": 12.0, "thru": False,
 "tap": {"pitch": 0.8, "class": "6H", "drill_mm": 4.2},
 "provenance": {"standard": ["ISO 261/262"], "sources": [...],
                "corroborated": True, "conflicts": []},   # standard is a LIST
 "instances": [{"i": 0, "status": "engaged", "probe": "axis", ...}],
 "verify": "bbox", "dropped": []}
```

**`count` is what was measured to come off, never what was asked for.** The
guard proves per instance whether material was removed; anything it proves was
a no-op is dropped from `count`, `positions` and `centers` and listed under
`dropped` with its status, with a warning naming the indices —
`verify="off"` is the one value under which `count` is intent, because the
caller asked for no measurement.

**`verify` is the mode you REQUESTED; `instances[i].probe` is the tier that
answered.** The default runs up to three probes and one call routinely uses
two, so `"verify": "bbox"` beside `"probe": "axis"` is the request beside the
answer, not a contradiction. Read `probe` when the question is "how do we
know".

**`provenance` is the published backing for the DIAMETER**, unioned over every
table row that fed the hole (a counterbore has two: the clearance hole and the
fastener head). `corroborated` is true only for two or more independent sources
that **agree**, so a single-sourced seat (every ISO 10642 countersink) or an
adjudicated one (ASME `#8` normal) says so on the record itself — a disputed
row additionally warns. `drilled` holes carry `None`: no table supplied the
number. `standard` is always a **list**, even of one.

It is not merely carried, it is **checkable**: `hole_standards.validate_record`
re-derives the whole block from the record's own size, fit, standard and
fastener and compares, exactly as it does the designation — and it ties `size`
and `fit` to `d`, so the number that gets cut and the label that selects its
provenance name one published row or neither is checkable. A record cannot
claim a corroboration, a citation, a standard or a diameter its own fields do
not earn.

`designation` is **derived from the record** by
`hole_standards.designation_for_record`, and `designation_base` is the same
callout with no depth qualifier. Both readers that hold geometry re-derive the
first to check a carrier and print the second when they have measured that the
recorded depth no longer holds.

The records **ride on the shape the helper returns** rather than in a registry
the worker drains, and that is not a stylistic choice: the worker's 16-entry
`_SHAPE_CACHE` returns a cached shape *without calling `build(p)`*, and the
service's metrics fast path makes no kernel call at all — so a registry would
drain empty on the second and every later build of an unchanged part, silently
(changelog 0150).

What that costs you: **any operation returning a new object drops the
attribute**. `safe_fillet`, `safe_shell` and `safe_bool` carry it for you; a
raw build123d operation of your own does not:

```python
part, records, warn = holes.clearance(part, pts, "M5")
part = part - Cylinder(3, 40)              # a raw op: records are gone
part = holes.carry(part, previous_part)    # carry them across yourself
holes.records(part)                        # what this shape is carrying
```

You do not have to notice: the rebuild compares `holes.created()` before and
after the build and warns when records went missing, naming what to do.

The build result and `get_part` carry a `holes` key with four distinct
answers — the records, `null` ("declares none"), `[]` plus a warning ("they
were dropped") and **absent** ("not harvested") — described in
`docs/agent-api.md`.

#### Guards you will actually see

Off-part and off-face instances (named by index), an instance that touches but
removes nothing (`verify="exact"`), a whole call that removed nothing at all, a
depth deeper than the stock below the plane (a bounding-box measure — it
catches a depth that cannot fit in the part, not one that misses a local
pocket), and a new hole within one diameter of another or of an existing
record's centre. An unknown size, a negative depth or a counterbore smaller
than its own clearance hole **raise**.

One honest limit, from the function's own docstring: `carry()` is bookkeeping,
not a proof. Carrying records across a cut that removed one of the holes leaves
a record for a hole that is gone — re-verifying every record against the
geometry on every call is priced at 2.1 ms per instance and is PRD-021's job.

```python
from build123d import Box
from agentcad.toolkit import holes, patterns

def build(p):
    part = Box(120, 120, p.t)
    part, recs, warn = holes.tapped(part, patterns.bolt_circle(45, 8),
                                    "M5", depth=10)
    return part          # the drawing prints: 8× M5×0.8 - 6H ↧10
```

### Constraint-solved sketches

`agentcad.toolkit.sketch.solve_sketch(spec)` runs a first-party scipy
least-squares solver over points/lines/circles/arcs/ellipses/splines/slots and
a constraint list, and
returns exact coordinates you can feed straight into a `BuildLine`/`BuildSketch`.
The same solver is exposed to agents as the `solve_sketch` tool (see
[agent-api.md](agent-api.md)); the spec shape and constraint vocabulary are
identical.

```python
from agentcad.toolkit import sketch

spec = {
    "points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
               {"name": "b", "x": 40, "y": 0},
               {"name": "c", "x": 40, "y": 25}],
    "lines":  [{"name": "ab", "p1": "a", "p2": "b"}],
    "circles": [],
    "constraints": [
        {"type": "horizontal", "ln": "ab"},
        {"type": "distance", "p": "a", "q": "b", "d": 40},
        {"type": "distance_y", "p": "b", "q": "c", "d": 25},
    ],
}
sol = sketch.solve_sketch(spec)      # {"ok": True, "points": {"c": {"x": 40.0, "y": 25.0}, ...}, ...}
```

The solver converges to the solution *nearest the initial guess*, so seed the
rough shape you actually want — a mirrored guess yields a mirrored result. Pass
`"initial": {"points": {"c": {"x": …, "y": …}}, "circles": {"C": {"r": …}}}` to
seed it explicitly (branch selection, not speed); it never edits the spec, and
an `initial` that does not cover every free entity falls back to a cold start
with `warm_started: False` and an `initial_incomplete` warning.

Every result carries a `diagnostics` block: `status`
(`well_constrained`/`under_constrained`/`over_constrained`/`did_not_converge`),
`dof` (= `n_params − rank(J)`, never negative), `free_entities` for an
under-constrained sketch, and `redundant`/`conflicting` naming the dependent
constraints by their index in `constraints`. A redundant but consistent
constraint still solves; only a `conflicting` one is an error.

Constraint types: `fixed, coincident, distance, distance_x, distance_y,
horizontal, vertical, parallel, perpendicular, angle, point_on_line,
point_on_circle, radius, equal_radius, midpoint, tangent_line_circle,
tangent_circles, tangent, symmetric, equal_length, concentric`.

**Splines** (`"splines": [{name, points:[<point names>]}]`) are ordered lists
of named points, degree 3, non-periodic; the points are ordinary points, so
every point constraint applies to them, and the emitted build123d `Spline`
interpolates them (measured to 7.1e-15 mm). Constraints reach the curve only
through its control points and its **end tangents** (`{"type": "tangent", "a":
"sp1.start", "b": "ln4"}`); on-curve point constraints are out of scope. A
pinned end tangent needs `tangents=` at emission — a free-end `Spline` drifts
up to 44.6 deg from the control-polygon leg — and the result reports
`splines[name]["end_tangent"]` and the solved directions to say so.

**Slots** (`"slots": [{name, c1, c2, width}]`) compile at ingestion into
`<name>.arc_a`, `<name>.arc_b`, `<name>.side_1`, `<name>.side_2` with **one
shared radius** and structural junctions, contributing five rows in total
(`radius = width/2` plus four tangencies). Sub-entities may be referenced in
constraints but not declared; a diagnostic reports the slot with `origin:
"slot:<name>"` and `index: null` rather than a constraint you did not write.

**Arcs** (`"arcs": [{name, center, r, start_deg, end_deg, fixed_r?}]`, or the
3-point form `{name, start:[x,y], mid:[x,y], end:[x,y]}`) add three parameters
each — radius and the two angles, counter-clockwise degrees — and expose their
endpoints as the **virtual handles** `<name>.start` / `<name>.end`, which can
be written wherever a point name is accepted, so `{"type": "coincident", "p":
"arc1.end", "q": "p3"}` closes a chain with no extra entities. `radius`,
`equal_radius`, `point_on_circle` and both tangency constraints accept an arc
wherever they accept a circle. Entity names may not contain a `.` — that
namespace belongs to handles and compiled sub-entities. `tangent {a, b, at?,
kind?}` dispatches on what `a` and `b` are (line+circle/arc, or curve+curve);
`symmetric {a, b, about}` mirrors two points or two lines about a line in two
rows (midpoint on the axis **and** perpendicular to it).

**Tangency at a junction is a *direction* residual, and that matters.** When
the two curves already meet — the line is built on `arc1.end`, or a
`coincident` ties a junction point to it — `tangent` compiles to "the two unit
tangents are parallel" instead of "centre-to-line distance equals r". Same row
count; the distance form is second-order flat exactly at the solution, so it
reports itself as redundant while doing real work (measured singular value
1.8e-16 against a 8.5e-9 rank tolerance) and costs four times the iterations.
At such a junction `tangent`'s `kind` has no meaning and is accepted unused —
the coincidence already chose where the curves touch.

**Ellipses** (`"ellipses": [{name, center, a, b, rotation?}]`, plus
`start_deg`/`end_deg` for an elliptical arc) cost 3 parameters (+2 when
bounded). Angles are the **eccentric anomaly** in degrees — the point at `t` is
`center + R(rotation)·(a·cos t, b·sin t)`, which is exactly build123d's
`EllipticalCenterArc` parametrization (measured to 8.9e-16 mm), so the solved
angles are the emitted ones. Handles: `<name>.center`, `<name>.major` /
`<name>.minor` (the semi-axis ends, ordinary point handles, so `distance` and
friends pin an ellipse's size with no new constraint type), `<name>.start` /
`<name>.end` when bounded, and the scalar handles `<name>.a` / `<name>.b` that
`radius`/`equal_radius` take — an ellipse has two radii and neither is *the*
radius, so you name the one you mean. `tangent` accepts ellipse+line and
ellipse+circle/arc, carrying the tangency point's anomaly as an auxiliary
parameter (`<name>.tangency`). **Out of scope, deliberately:**
ellipse-to-ellipse tangency, on-ellipse point constraints, and
parabolas/hyperbolas (a PRD non-goal).

#### Sketching on a face

`sketch_plane {project, part_id, face_index}` (agent tool) returns the picked
planar face's **basis** — `{origin, x_dir, y_dir, normal}` from build123d's
`Plane(face)`, measured bit-identical across rebuilds, across a fresh worker
and across parameter changes that do not renumber the faces — plus `refs`, the
face's own boundary edges in that plane's 2D coordinates, and `entities`, the
same references already in `solve_sketch`'s entity shape.

Reference entities arrive **fixed and construction-marked**: fixed means they
contribute zero parameters (no DOF, undraggable, and they can never appear in
a conflict report as something you could change), construction means they
constrain but are never emitted as geometry. Boundary edges that are neither
lines nor circles come back `kind: "other"` with a polyline approximation and
are **not** constraint targets — a documented gap, not a silent one.

Pass the plane back as `"plane": {...}` on `solve_sketch` and emission writes
`BuildSketch(Plane(origin=…, x_dir=…, z_dir=…))` instead of `Plane.XY`, under
a header naming the face. **Face indices are mesh-order ordinals and a
topology-changing parameter edit can renumber them** (measured: `corner_r: 6.0`
turned the enclosure's face 37 from a 5989 mm² base plate into a 51 mm²
sliver) — the caveat is written into the script rather than hidden.

#### Emitting build123d source

`"emit": "function" | "buildline"` (or `agentcad.core.sketch_emit.emit(solution,
spec)`) returns `{code, warnings, style}` — the **one** emitter the GUI and
agents share, so the same spec produces byte-identical code either way.

**The entity → build123d mapping (FR11):**

| entity | emitted |
|---|---|
| line chain | `Polyline(v0, v1, …)`, or `Line(a, b)` per segment in a mixed chain |
| arc (centre-authored, in a chain) | `RadiusArc(start, end, ±r, short_sagitta=…)` — endpoint-anchored |
| arc (3-point authored) | `ThreePointArc(start, mid, end)` |
| arc sweeping a full turn | `CenterArc(…)`, with a warning (a `RadiusArc` cannot express it) |
| circle | `Circle(radius=…)` under `Locations(…)` |
| ellipse (full) | `Ellipse(x_radius=…, y_radius=…, rotation=…)` under `Locations(…)` |
| elliptical arc | `EllipticalCenterArc(centre, a, b, start_angle=…, arc_size=…, rotation=…)` |
| spline | `Spline(p0, …, pn)`, with `tangents=` when an end tangent is pinned |
| slot, standalone | `SlotCenterToCenter(sep, 2r, rotation=…)` under `Locations` |
| slot, tied to the sketch | its compiled primitives — two `Line`s, two `RadiusArc`s |
| construction / projected reference | **nothing** — it constrains, it never emits |
| closed chain | `make_face()`, behind the closure gate |

`arc_size`, **not** `end_angle`, on `EllipticalCenterArc`: passing `end_angle`
raises `UnboundLocalError` in the pinned build123d 0.11.1 (its deprecation
branch reads a name only the other deprecated parameter binds). And
`SlotCenterToCenter` is a BuildSketch **face**, not a curve that can join a
`BuildLine` chain, which is why a slot that carries constraints of its own
emits as its primitives instead.

Every junction is emitted **once**, as a shared `v<n>` literal at **9
decimals**, and a closure gate refuses to emit `make_face()` when a junction's
shared literal is more than **1e-8 mm** from any endpoint it stands for.
Measured: a centre-parametrized arc chain at 6 decimals — what the GUI used to
write — leaves a 7.58e-7 mm gap and `make_face()` raises *"Face can only be
created with closed wires"*, and the failure only appears on non-round
coordinates.

#### Dragging

`"drag": {point, x, y, weight?}` pulls a point (or a virtual handle) toward a
cursor as a **weighted soft objective, not a constraint**: it is excluded from
`ok`, `max_residual`, `n_residuals`, `rank`, `dof` and `diagnostics`, and its
own slack comes back as `drag.gap`. Send it with `initial` seeded from the
**previous frame's solution** — seeding the dragged point at the cursor is what
causes a mirror-branch flip, not what prevents one. Dragging a fully
constrained entity moves it (almost) not at all; that is correct.
`"diagnostics": "auto" | "full" | "cached"` controls the diagnostics cache
(`auto` serves the cached block on a drag frame, since a drag changes no
constraints), and `diagnostics_source` in the result says which you got.

#### Round-trip persistence

`"persist": "<name>"` (alongside `emit`) wraps the emitted code in a block that
carries the whole spec, so the sketch can be **reopened and re-solved** from
the script it was written into:

```python
# --- agentcad sketch "profile" (auto-generated; edit or remove freely) ---
# agentcad-sketch-spec: {"v": 2, "entities": {...}, "constraints": [...], "initial": {...}}
# agentcad-sketch-hash: sha256:33353fb1…
def sketch_profile():
    ...
# --- end agentcad sketch "profile" ---
```

In the script, not a sidecar: the part script is the only artifact this
project keeps, and a script-resident block gets branching, restore, undo,
merge and the proposal diff for free. **The block name becomes the function's
name, so two blocks of one name define `sketch_<name>()` twice and the second
silently wins** — nothing prevents it. `sketch_emit.next_name(script)` returns
the next name that shadows nothing (it counts pre-block `def sketch_*(`
definitions too), the `/api/sketch/blocks` route returns it as `next_name`,
and the sketcher's Insert asks for it rather than guessing.

`agentcad.core.sketch_emit.parse_blocks(script)` reads them back —
`{name, status, spec, code, hash, computed_hash, start_line, end_line,
message}` — and the GUI does the same through `POST /api/sketch/blocks`.
**The code is the source of truth for geometry; the spec block is
provenance:** the hash covers **the spec line and the code together**, so
`status: "ok"` means *this spec produced this code*. Edit either and `status`
is `diverged`, and nothing is repaired (the sketcher opens read-only and asks
the user to choose). A spec that will not parse, is not shaped like a sketch
spec, is of another version, or a block with no hash or no end marker, is
`unverified` — "we cannot tell", never rendered as "there is no sketch".

Entities and constraints are stored **as submitted**; `initial` is stored from
the **solution**, which is what makes reopening land on the branch the code was
emitted from. (Without it, a sketch solved on the branch an `initial` selected
reopened on the other one and still reported `ok`.)

### Threads and fasteners

`agentcad.toolkit.threads` wraps `bd_warehouse` (Apache-2.0) with real ISO
thread geometry: `external_thread`, `internal_thread`, `threaded_rod`,
`tapped_hole_thread`, plus `cap_screw` / `hex_bolt`. Real threads are exact
but heavy (~9k triangles per M8 thread at mesh tolerance 0.1), so:

- **Assembly previews / fit checks** — use cosmetic threads (`simple=True` on
  `cap_screw`/`hex_bolt`): fast and light.
- **Manufacturing drawings / real mating** — use real threads.
- **To tap a hole** — bore a hole at `tapped_hole_thread(...).min_radius` (the
  tap-drill size) then `add()` the returned thread solid. The wrappers exist
  specifically to bypass `bd_warehouse`'s `ThreadedHole(simple=False)` trap
  (~15 s and inserts no thread).

### Surfacing (class-A)

`from agentcad.toolkit import surfacing`:

- `surfacing.smooth_loft(profiles, ruled=False) -> (part, warning|None)` —
  loft one solid through 2+ planar profiles (Sketch/Face/Wire); falls back to
  a ruled loft with a warning; RuntimeError when both fail.
- `surfacing.blend_surface(face_a, face_b, continuity="G1") ->
  (face, warning|None)` — transition surface between the two faces' nearest
  edges with G0/G1/G2 continuity against the source faces (OCCT plate
  filling). G2 is gated for numerical stability and degrades to G1 with a
  warning when the plate balloons (an OCCT 7.x instability); surface the
  warnings — they are honest. Verify blends with
  `analyze_part(kind="curvature")`: a true G2 blend shows no jump in
  curvature across the seam.

### Ribs, bosses and draft

`from agentcad.toolkit import features` — three shape features under the same
honest-warning contract. `plane` is resolved by the same predicate `holes.*`
uses (a `Plane`, or `top|bottom|front|back|left|right` → the extreme planar
face along that axis, by area), and the normal points **out of the material**:
holes drill along `-z_dir`, ribs and bosses grow along `+z_dir`. For a feature
inside a cavity pass an explicit `Plane(origin=(0, 0, floor_t), z_dir=(0,0,1))`
— a *name* can only ever resolve to an outer face, and after you add a rib
`"top"` resolves to the **rib's** top face.

- `features.rib(part, profile, thickness, *, to, plane="top", draft_deg=None)
  -> (part, warning|None)` — the `profile` polyline (points in plane
  coordinates) traced to `thickness` and extruded away from the seat.
  `to=<mm>` is the rib height and is exact (measured equal to a hand-built rib
  to the last bit). `to="part"` extrudes generously and intersects the part's
  **bounding solid**, which is an envelope and not the part: on a convex part
  the rib lands inside existing material and adds **0 mm³** (measured), on a
  shelled part it runs to the top of the bounding box. That mode always warns.
  `draft_deg` tapers the extrusion — it never calls the draft operation,
  because a finished shelled part refuses draft (see below).
- `features.boss(part, at, d, h, *, hole=None, hole_depth=None, draft_deg=None)
  -> (part, warning|None)` — a cylinder standing `h` above the seat at `at`.
  `hole="M3"` bores the tap drill with `holes.tapped` (blind at the seat by
  default) so the screw boss carries a **record** and reaches the drawing
  callouts; read it back with `holes.records(part)`.
- `features.draft(part, faces, angle_deg, neutral_plane, *, min_angle=0.25)
  -> (part, achieved_deg, warning|None)` — `faces` is a list of `Face`s **or a
  selector callable** `f(part) -> faces`, never indices. On failure it binary-
  searches down to the largest angle that yields a *valid* solid and names what
  it applied.

**Draft's ceilings are low, and they are lowest exactly where draft matters.**
Swept 0.25 → 60° through the kernel worker (changelog 0156); failure was
**monotone in the angle on all eight shapes** — no islands, which is what makes
the search sound:

| part | largest angle that produced a valid solid |
|---|---|
| box 40×30×20 (4 side faces) | 35° |
| box + boss (5 faces) | 15° |
| box + R4 vertical fillets (8 faces) | 10° |
| shelled box t=2 (8 faces) | 2.5° |
| `construction/gusset_plate` (18 faces) | 17.5° |
| `prototyping/enclosure_base` (56 faces) | **0.25°** |
| `rocketry/nozzle`, `construction/angle_bracket` | **none** — every angle fails |

Draft before you shell or fillet. And note the failure mode: only the extreme
angles raise (`Standard_Failure` with an **empty** message, or build123d's
`DraftAngleError`); most failing angles **return a shape** with
`is_valid False` and a plausible volume, so a hand-written `draft()` call must
check `is_valid` itself. When nothing works down to `min_angle`,
`features.draft` returns the part **unchanged** with a warning naming the
failing angle and what OCCT said — never a silently undrafted part.

### Sheet metal

`agentcad.toolkit.sheetmetal.SheetPart` is a declarative builder: one spec
yields BOTH the folded solid and the manufacturing flat pattern, so they can
never disagree. `base(width, depth)` is a plate centered on the origin (width
along X, depth along Y, z in `[0, t]`); `flange(edge, angle_deg, length,
inner_radius=None, start=0.0, width=None, relief="auto")` adds a flange bending
up (+Z) on `left`/`right`/`front`/`back` (angle exclusive `(0, 180)`;
`inner_radius` defaults to the thickness). `start` is measured from the edge's
low-coordinate end (X− for `front`/`back`, Y− for `left`/`right`) and
`width=None` spans the whole edge; several flanges may share an edge as long as
their spans do not overlap. Bend allowance is
`BA = radians(angle) * (inner_radius + k_factor * thickness)` — each flange
adds `BA + length` of flat stock beyond its edge (`k_factor=0.44` suits
air-bent steel/aluminum).

```python
from agentcad.toolkit.sheetmetal import SheetPart

def _sheet(p):
    return (SheetPart(p.thick)
            .base(p.width, p.depth)
            .flange("front", 90, p.flange_len, inner_radius=p.bend_r))

def build(p):
    return _sheet(p).fold()          # single valid folded solid

def flat_pattern(p):                 # optional contract → flat_pattern tool
    sp = _sheet(p)
    return sp.unfold(), sp.bend_lines()
```

`unfold()` returns the flat blank as a solid, `flat_outline()` its CCW outline
polygon, `flat_outline_edges()` the same outline as exact lines and arcs, and
`bend_lines()` the bend midlines (`BA/2` beyond each edge, in flat
coordinates). Declaring `flat_pattern(p)` enables the `flat_pattern` export
tool (SVG, or DXF with `OUTLINE`/`BEND` layers). Overlapping spans on one edge,
angle 0/180, or `flange()` before `base()` raise `ValueError`; read
`sp.warnings` after `fold()` if fusion needed a fallback.

**The outline is the unfold.** `flat_outline()` is a discretization of
`unfold()`'s own top face at a chord tolerance, not a second model of the
blank — so consistency is a fact rather than an invariant to maintain. For a
straight-edged blank its enclosed area equals that face's area exactly; where a
round relief or a hem puts arcs in the boundary it is within the tolerance.
Base corners are vertices only where the blank actually turns.

**Bend relief.** Wherever a partial flange stops in the middle of an edge, a
relief is cut **through the base plate**, in both `fold()` and `unfold()`, from
one computation. `relief="auto"` (= `"rect"`), `"round"`, or `"tear"`; or an
explicit `{"kind", "width", "depth"}`. The default sizing —
`1.5 × thickness` wide, `inner_radius + thickness` past the bend line — is a
**common shop rule, not a standard**; no ISO governs it. `"tear"` removes no
material and says so in `sp.warnings`.

**Hems and corners.** `hem(edge, kind="open"|"closed", length, start, width)`
is a 180° bend folding the leaf back over the sheet, with an air gap of `2R`:
`open` uses `R = t` (gap `2t`), `closed` `R = t/2` (gap `t`). Both are shop
defaults, overridable with `inner_radius=`. **`kind="teardrop"` raises** — it
wraps past 180°, where this model's tangential leaf descends into the sheet
after `R·(1−cos a)/−sin a` (2.41·R at 225°) while a hem leaf needs ≥ 4t; the
overlap would be swallowed silently by the fuse. `corner(edge_a, edge_b,
treatment)` treats the corner where two flanged edges meet: `close` mitres the
two leaves on the 45° bisector, `gap` opens one thickness on both, `rip` is the
untreated corner. Declare the two flanges before the corner.

**A `close` corner is a whole seam when the two flanges' cross-sections agree
and each leaf fits its mitre extension**, and it warns with the measurement
when they do not. One plane cuts both leaves, so each leaf's cut face is its
own profile: the faces coincide exactly when the profiles do (a different
*leaf length* is fine — the shorter face is seamed whole — a different angle or
inner radius is not). The second condition is the leaf's outward reach, which
holds iff `L ≤ (R + t)·tan(45° − a/2)` — unbounded at and above 90°, where the
leaf is vertical and adds no reach, and a small number below. Measured shares
of the promised seam: 1.000000 for matched corners inside that bound, *acute
ones included* (60°/R3/L0.2, 45°/R3/L0.5, 30°/R5/L1); 0.9586 for a 0.1 mm
radius mismatch; 0.2674 for 90°/R3 against 45°/R1; and — for a *matched* 45°
pair at t=2 whose leaf is 12 mm — 0.2810 at R1 (limit 1.2426 mm) and 0.4103 at
R3 (limit 2.0711 mm), the seam growing with the limit. Nothing else sees it —
both leaves are cut by the same plane so no material is lost, and they still
fuse through the plate into one valid solid.

Every `fold()` and `unfold()` checks `is_valid` **and** the solid count **and**
material conservation, and warns if the declared features did not join into one
body or if the fuse swallowed declared material — OCCT reporting success is not
evidence that it did neither.

## Analysis stand-ins for interference checking

A script may define an optional ``analysis(p)`` alongside ``build(p)``: a
simplified, **conservative** stand-in that ``check_interference`` uses in
place of the display shape. The canonical case is real ISO thread geometry —
exact helical B-reps make pairwise booleans hundreds of times slower, and
production CAD suppresses threads in analysis for the same reason. A
cosmetic shank at the thread's nominal (major) diameter strictly *contains*
the real thread, so a clear envelope proves the real part clear:

```python
def _build(p, simple):
    return threads.cap_screw("M8-1.25", p.length, simple=simple)

def build(p):        # display / export / metrics: real thread
    return _build(p, simple=False)

def analysis(p):     # interference: nominal-diameter envelope (superset)
    return _build(p, simple=True)
```

Keep the stand-in a superset of the real shape — an undersized analysis
shape would hide genuine collisions. Display, export, drawings, and metrics
always use ``build(p)``.

## Declaring connectors for mates

A part script may declare named **connectors** so its instances can be posed
by [assembly mates](agent-api.md#assembly-and-mates) (`set_mate`) instead of
explicit transforms. This is a single optional function alongside `PARAMS`
and `build`:

```python
def connectors(p, part) -> dict:
    top = part.faces().sort_by(Axis.Z)[-1]
    return {
        "seat": {"type": "rigid",
                 "location": (top.center().to_tuple(), (0, 0, 0))},
        "hinge": {"type": "revolute",
                  "axis": ((0, 0, 0), (0, 0, 1)), "range": (0, 180)},
        "bore":  {"type": "cylindrical",
                  "axis": ((0, 0, 0), (0, 0, 1)), "linear_range": (0, 20)},
    }
```

- `p` is the resolved-parameter namespace `build` received; `part` is the
  built shape — so connectors can be *derived from topology*.
- Locations are in the part's **local frame**. `location` accepts a build123d
  `Location`, `((pos), (rot))`, or `(x, y, z)`; `axis` accepts an `Axis` or
  `((point), (direction))`.
- Connector `type` is `rigid` (needs `location`), `revolute`, or
  `cylindrical` (both need `axis`; optional `range` / `linear_range`).
- When mating, the **moving** instance's connector must be `rigid`; the
  anchor connector carries the degree of freedom that `set_mate`'s
  `angle_deg`/`offset_mm` drive. The service resolves mate chains to concrete
  transforms at assembly read (topological order; cycles are rejected).

## Design specs (SPECS)

A part script may declare **design intent as executable assertions** — a
module-level `SPECS` list beside `PARAMS`, built from pure-data constructors.
Every rebuild evaluates them and reports pass/fail beside the metrics; a
failing spec never fails the rebuild.

```python
from agentcad.toolkit.specs import check_mass, check_that, check_wall

SPECS = [
    check_wall(min_mm=2.5, requirement="ENG-014"),
    check_mass(max_g=120.0, requirement="SYS-042"),
    check_that(lambda part, metrics:
               metrics["bbox"]["max"][2] - metrics["bbox"]["min"][2] <= 80.0,
               name="fits_fairing", requirement="SYS-042"),
]
```

**The vocabulary is ten constructors**, seven part-scope and three
project-scope. Nine reuse a measurement the kernel already had; a constructor
lands only when its measurement exists (`clearance` was the one new one).

| Part scope (in `parts/<id>.py`) | Asserts |
|---|---|
| `check_valid()` | the part builds into at least one valid solid |
| `check_mass(min_g=None, max_g=None)` | mass budget in grams (material-dependent by design) |
| `check_volume(min_mm3=None, max_mm3=None)` | the solids-sum volume |
| `check_bbox(within_mm)` | the bounding-box size fits — a scalar or `[x, y, z]` |
| `check_wall(min_mm, grid=8)` | minimum wall thickness (see the caveat below) |
| `check_that(fn, name)` | any predicate `fn(part, metrics) -> bool` over the built part |
| `check_fem_static(fixed_face, load_face, load_N, max_vm_mpa=…, max_disp_mm=…)` | a linear-static FEM budget |

| Project scope (in a root `specs.py`) | Asserts |
|---|---|
| `check_interference_free(min_volume_mm3=0.001)` | no two placed instances overlap |
| `check_clearance(a, b, min_mm)` | minimum distance between two placed instances |
| `check_stackup(from_instance, to_instance, axis, within)` | the 1-D worst-case tolerance stack-up (PMI dims) along a mate chain |

Every constructor takes `name=` (a default is derived: `wall_min`, `mass_max`,
`clearance_<a>_<b>`, …; a name may not contain `:`) and `requirement=` — an
opaque id or URL (`"SYS-042"`, a link) that we store and group by and never
parse or resolve.

**Part scope vs project scope.** Anything measurable from one built part goes
in that part's `SPECS`; anything that needs the *placed assembly* goes in the
project's root `specs.py`, which is an ordinary module with its own `SPECS`
list and no `PARAMS`/`build`. Both are tracked files, so branching, diffing,
merging, restore and undo apply to intent exactly as they do to geometry —
there is no spec database. Write `specs.py` with `set_project_specs` (or just
edit the file). A project-scope check found in a part script (or vice versa)
is reported as `skip`/`unsupported_scope` — visible, never silently dropped.

**Constructors validate eagerly, and that is the error contract.** `SPECS` is
built while the module executes, so `check_wall(min_mm="thick")` raises *there*
and surfaces as a `script_error` with `details.line`, byte-identically to a
malformed `PARAMS`. There is no new error type: bad arguments are script
errors, and everything about a *check* — pass, fail, skip, error — is payload.

**What a rebuild evaluates.** The shape tier only (`valid`, `mass`, `volume`,
`bbox`, `wall`, `that`). Assembly-scope and FEM checks are reported there as
`skip` with `reason: "deferred"` and are evaluated by `run_specs` and by the
proposal gate — a 600 s solve inside a slider drag is not a design tool.
`check_fem_static` declares cleanly on a machine with no `[fem]` extra and
evaluates to `skip`/`fem_extra_missing` there; skips are data, never hidden.

**Two gotchas worth the ink:**

- **`check_wall` is a sampled ray cast, not a medial-axis measurement.** It
  samples a `grid × grid` UV grid per face and casts along the inward face
  normal, so it over-estimates on non-parallel walls, can miss a feature finer
  than the sample spacing, and *will* find chamfered edges and fillet runouts
  that are genuinely thinner than the nominal wall. The measured value
  therefore is not the wall parameter: on `examples/rocketry`'s nozzle a 3.0 mm
  wall measures 1.02 mm at `grid=4`, because the thinnest sampled point is the
  chamfered exit lip. **Pick the limit from a measurement** (`analyze_part
  {kind: "wall"}` or one `run_specs`), and **pin `grid`** — changing it changes
  the measurement, and cost is quadratic in it (60 ms on that nozzle at the
  default 8, 2.4 s on the heaviest lofted part in `examples/engine`).
- **`check_fem_static`'s faces are selectors, not indices**:
  `{"axis": "x"|"y"|"z", "side": "min"|"max"}`, the same shape `fem_static`
  takes. At least one of `max_vm_mpa` / `max_disp_mm` is required — a check
  with no limit can neither pass nor fail.
- **A `check_that` predicate must not write to the `metrics` dict it is
  handed** (it gets a copy, and predicates are evaluated after the built-in
  checks precisely so a mutating one cannot change their verdicts) and must not
  depend on evaluation order.
- **`check_clearance` against an imported STL is not measured.** An STL is one
  welded mesh face with no B-rep to measure a distance against, so the check is
  a `skip`/`mesh_only` in a report — and a **fail** in a proposal's `specs`
  gate, because an unmeasured clearance must not pass a merge. Import the part
  as STEP if a clearance depends on it. The same rule covers *every* skip: a
  `check_fem_static` on a reviewing machine without the `[fem]` extra, a
  project-scope check declared in a part script, an `interference_free` with
  nothing placed — a report names the reason, and the gate is red.
- **Limits must be finite numbers.** `check_mass(max_g=float("nan"))` raises
  where you wrote it: every ordered comparison against NaN is false, so a NaN
  limit would report `pass` while measuring nothing.

`"spec": 1` is the declaration marker *and* the format version: a dict without
it is not a spec, and a future format bump is a version change, not a new key.
`examples/rocketry` ships the whole loop — a nozzle wall minimum and mass
budget (`parts/nozzle.py`), a flange bolt-circle ligament (`parts/flange.py`)
and the assembly gaps (`specs.py`), with `injector_plate.py` deliberately
declaring nothing.

## Tolerances and GD&T (PMI)

Parts can carry a PMI section — annotation, not geometry — rendered by
`generate_drawing` (SVG) as toleranced dimensions, datum flags, and feature
control frames:

```json
{"dims":   [{"id": "d1", "kind": "linear",   "target": "width", "plus": 0.1, "minus": 0.1},
            {"id": "d2", "kind": "diameter", "target": 9.0,     "plus": 0.05, "minus": 0.1}],
 "datums": [{"id": "A", "face": "bottom"}],
 "fcf":    [{"id": "f1", "type": "flatness", "tol_mm": 0.05, "datums": [], "note": "mounting face"}]}
```

Linear targets tolerance the overall extents (`width` = X, `height` = Z,
`depth` = Y); diameter targets attach to circles detected in the top view
within 0.05 mm of the nominal. Set with `set_part_pmi` (an empty object
clears), read with `get_part_pmi`. Applies to script and reference parts;
DXF drawings ignore PMI (v1).

## Cheat-sheet

Agents get the full contract plus common build123d idioms (builder contexts,
selectors, patterns, sketches, lofts) from the `part_template` tool at
runtime; the same text lives in `agentcad/core/templates.py` (`CHEATSHEET`).
The bundled examples under `examples/` are the best reference for real,
robust parametric parts:

- `examples/rocketry` — revolved profiles, polar patterns (thrust chamber).
- `examples/construction` — sketch polygons, rotated hole groups (gusset node).
- `examples/prototyping` — shells, bosses, slot patterns (snap-fit enclosure).
- `examples/fasteners` — real ISO threads, `safe_fillet` (M8 bolted joint).
- `examples/engine` — algebra-mode booleans, connectors + revolute/chained
  mates, engineered running clearances (90° V4 engine).
