# Authoring Parts

A part is a plain Python script using [build123d](https://build123d.readthedocs.io).
No AgentCAD imports, no framework — the script is portable and runs anywhere
build123d does. AgentCAD executes it in the kernel worker and renders,
measures, and exports what `build(p)` returns.

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

- `PARAMS` is a dict of numeric parameter specs. `default` is required and
  must be a number; `min`, `max`, `unit`, `description` are optional but
  strongly recommended — `min`+`max` gives the UI a slider, and the bundled
  examples treat all four as mandatory style.
- Project parameter overrides are **clamped** to `[min, max]` with a warning
  (never an error), so agents and sliders can push bounds safely.
- `build(p)` receives an attribute namespace of resolved values (`p.width`)
  and must return a build123d `Part`, `Solid`, or `Compound`. Returning the
  `BuildPart` builder itself also works — AgentCAD takes `.part`.
- Units are millimeters; angles in degrees; mass uses the part's material
  density (see `agentcad/core/materials.py` for the built-in table).

Execution environment: fresh module namespace per rebuild, 120 s build
timeout, stdout redirected (use exceptions, not prints, to signal problems —
tracebacks come back with the failing line number). Same script + same
parameters → identical geometry, always.

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

## Cheat-sheet

Agents get the full contract plus common build123d idioms (builder contexts,
selectors, patterns, sketches, lofts) from the `part_template` tool at
runtime; the same text lives in `agentcad/core/templates.py` (`CHEATSHEET`).
The bundled examples under `examples/` are the best reference for real,
robust parametric parts:

- `examples/rocketry` — revolved profiles, polar patterns (thrust chamber).
- `examples/construction` — sketch polygons, rotated hole groups (gusset node).
- `examples/prototyping` — shells, bosses, slot patterns (snap-fit enclosure).
