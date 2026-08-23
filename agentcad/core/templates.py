"""Default part script and the build123d cheat-sheet served to agents."""

DEFAULT_PART_SCRIPT = '''\
from build123d import *

PARAMS = {
    "length":    {"default": 60.0, "min": 5.0, "max": 500.0, "unit": "mm",
                  "description": "Plate length (X)"},
    "width":     {"default": 40.0, "min": 5.0, "max": 500.0, "unit": "mm",
                  "description": "Plate width (Y)"},
    "thickness": {"default": 6.0,  "min": 0.5, "max": 100.0, "unit": "mm",
                  "description": "Plate thickness (Z)"},
    "corner_r":  {"default": 5.0,  "min": 0.0, "max": 50.0,  "unit": "mm",
                  "description": "Corner fillet radius"},
}

def build(p):
    with BuildPart() as part:
        Box(p.length, p.width, p.thickness)
        if p.corner_r > 0:
            radius = min(p.corner_r, min(p.length, p.width) / 2 - 0.1)
            fillet(part.edges().filter_by(Axis.Z), radius=radius)
    return part.part
'''

CHEATSHEET = """\
AGENTCAD PART SCRIPT CONTRACT
=============================
A part is a plain build123d Python script defining exactly two things:

1. PARAMS: dict of typed parameter specs.
   PARAMS = {"name": {"default": 10.0, "min": 1.0, "max": 100.0,
                      "unit": "mm", "description": "..."}}
   - "default" is required. Optional "type": "number" (default) | "int" |
     "bool" | "enum" | "string". min/max/unit apply to number/int only
     (min+max gives the UI a slider); enum requires "choices" (strings
     and/or numbers); string takes "max_len" (default 200).
   - Numeric values passed by the project are clamped to [min, max] with a
     warning; bool/enum/string values must match their spec (an error if not).
   - e.g. "ribbed": {"default": True, "type": "bool", "description": "..."},
          "finish": {"default": "raw", "type": "enum",
                     "choices": ["raw", "anodized"], "description": "..."}

2. build(p): receives an attribute namespace of resolved values (p.name)
   and must return a build123d Part, Solid, or Compound.

Optional: SOLID_LABELS = ["body", "lid"]  # names a multi-solid Compound's
solids by index; metrics.solids and set_solid_materials address solids by
these labels (fallback solid_0, solid_1, ...).

Optional: SPECS = [...]  # executable design intent, checked on every rebuild
(load_skill design-specs). A failing spec NEVER fails the rebuild -- the
geometry lands and the failure is reported beside the metrics.

Surfacing: agentcad.toolkit.surfacing.smooth_loft / blend_surface --
continuity-controlled lofts + G0/G1/G2 face blends; always propagate the
returned warning. Verify with analyze_part(kind="curvature").

Units are millimeters; angles in degrees. Scripts run in a kernel worker
with a 60 s timeout. Do not read files, loop forever, or print protocol
noise — stdout is redirected. Raise exceptions freely: tracebacks are
returned to you with the failing line number.

BUILD123D IDIOMS (builder style)
--------------------------------
from build123d import *

with BuildPart() as part:                 # solids accumulate by fusion
    Box(20, 20, 5)                        # centered at origin by default
    Cylinder(radius=5, height=20)         # fuses with existing material
    with Locations((5, 5, 0), (-5, -5, 0)):
        Hole(radius=2)                    # through-hole (subtracts)
    Cylinder(radius=8, height=4, mode=Mode.SUBTRACT)   # explicit subtract
    fillet(part.edges().filter_by(Axis.Z), radius=2)   # vertical edges
    chamfer(part.edges().group_by(Axis.Z)[-1], length=1)  # topmost edges

Sketch + extrude / revolve:
with BuildPart() as part:
    with BuildSketch(Plane.XZ) as profile:     # sketch on a plane
        with BuildLine() as outline:
            Polyline((0, 0), (30, 0), (30, 40), (20, 40), (0, 10), (0, 0))
        make_face()
    revolve(axis=Axis.Z)                       # or: extrude(amount=10)

Lofts, sweeps, shells, patterns:
    loft()                                      # between stacked sketches
    sweep(path=path_line)                       # profile along path
    offset(amount=-2, openings=part.faces().sort_by(Axis.Z)[-1])  # shell
    with PolarLocations(radius=25, count=8):    # circular pattern
        Hole(radius=3)
    with GridLocations(10, 10, 4, 3):           # rectangular pattern
        Hole(radius=1.5)

Selectors (ShapeList methods, chainable):
    part.edges().filter_by(Axis.Z)              # parallel to Z
    part.edges().filter_by(GeomType.CIRCLE)     # circular edges
    part.faces().sort_by(Axis.Z)[-1]            # topmost face
    part.edges().group_by(Axis.Z)[-1]           # edges at max Z level
    part.faces().filter_by(Plane.XY)            # faces parallel to XY

Common failure modes:
- "Failed creating a fillet": radius too large for the edge — reduce it or
  fillet fewer edges.
- Hole() needs existing material to cut; depth defaults to through-all.
- BuildSketch profiles must be closed before make_face().
- offset()/shell with openings: pick the face to remove via selectors.

Algebra style also works: part = Box(1,2,3) + Cylinder(2,5) - Hole ...; any
expression returning a Part/Solid/Compound from build(p) is accepted.

Everything past the basics -- the robustness helpers, patterns, the hole
wizard, threads, ribs/bosses/draft, the sketch solver, sheet metal, design
specs, connectors and mates, and the craft guides (enclosures, snap-fits,
fits, FDM rules, FEM) -- is a loadable SKILL. The `skills` list beside this
sheet names them; call load_skill {name} for the one that matches the task.
"""
