# Authoring Parts

A part is a plain Python script using [build123d](https://build123d.readthedocs.io).
The base contract needs no AgentCAD imports — such a script is portable and
runs anywhere build123d does. AgentCAD executes it in the kernel worker and
renders, measures, and exports what `build(p)` returns.

Two *optional* extensions build on that contract: the
[part-authoring toolkit](#the-part-authoring-toolkit) (`safe_fillet`,
`safe_shell`, `safe_bool`, the sketch solver, threads) and the
[`connectors(p, part)`](#declaring-connectors-for-mates) hook for assembly
mates. Both are backward-compatible — a script that uses neither behaves
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
from agentcad.toolkit import threads                             # ISO threads / fasteners
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

### Constraint-solved sketches

`agentcad.toolkit.sketch.solve_sketch(spec)` runs a first-party scipy
least-squares solver over points/lines/circles and a constraint list, and
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
rough shape you actually want — a mirrored guess yields a mirrored result.
Constraint types: `fixed, coincident, distance, distance_x, distance_y,
horizontal, vertical, parallel, perpendicular, angle, point_on_line,
point_on_circle, radius, equal_radius, midpoint, tangent_line_circle,
tangent_circles`.

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

### Sheet metal

`agentcad.toolkit.sheetmetal.SheetPart` is a declarative builder: one spec
yields BOTH the folded solid and the manufacturing flat pattern, so they can
never disagree. `base(width, depth)` is a plate centered on the origin (width
along X, depth along Y, z in `[0, t]`); `flange(edge, angle_deg, length,
inner_radius=None)` adds a full-edge flange bending up (+Z) on `left`/`right`/
`front`/`back` (one per edge; angle exclusive `(0, 180)`; `inner_radius`
defaults to the thickness). Bend allowance is
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
polygon, and `bend_lines()` the bend midlines (`BA/2` beyond each edge, in
flat coordinates). Declaring `flat_pattern(p)` enables the `flat_pattern`
export tool (SVG, or DXF with `OUTLINE`/`BEND` layers). Duplicate edges,
angle 0/180, or `flange()` before `base()` raise `ValueError`; read
`sp.warnings` after `fold()` if fusion needed a fallback.

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
