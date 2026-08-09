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

1. PARAMS: dict of numeric parameter specs.
   PARAMS = {"name": {"default": 10.0, "min": 1.0, "max": 100.0,
                      "unit": "mm", "description": "..."}}
   - "default" is required and must be a number. min/max/unit/description
     are optional but strongly recommended (min+max gives the UI a slider).
   - Values passed by the project are clamped to [min, max] with a warning.

2. build(p): receives an attribute namespace of resolved values (p.name)
   and must return a build123d Part, Solid, or Compound.

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

ROBUSTNESS TOOLKIT  (from agentcad.toolkit import safe_fillet, safe_shell, safe_bool)
------------------------------------------------------------------------------------
The blessed way to write parts that survive OCCT's sharp edges. Each returns a
tuple ending in a warning (None when nothing went wrong) -- read it and, if the
part is user-facing, surface it. Importing these needs the agentcad package
(part scripts run in the app venv, so it is available; plain build123d scripts
that must stay portable should not import them).

    part, r, warn = safe_fillet(part, edges, radius, *, min_radius=0.05)
        # fillets at `radius`; on OCCT failure binary-searches DOWN to the
        # largest radius that works (uses .max_fillet as a hint). `r` is what
        # was actually applied; if even min_radius fails you get the part back
        # unchanged with a warning. Use instead of bare fillet() on any radius
        # that might be too big for the edge.

    part, warn = safe_shell(part, thickness, opening_faces=None, *, kind=Kind.ARC)
        # hollows the solid, opening `opening_faces` (a list of faces). Falls
        # back through Kind.INTERSECTION, then fewer opened faces, then an
        # APPROXIMATE boolean-subtract shell. That last fallback is NOT uniform
        # on curved/slanted walls (can be ~20% thin on dome mid-sections) --
        # the warning says so; pass it on. Raises only if every strategy fails.

    shape, warn = safe_bool(a, b, op="fuse"|"cut"|"common", *, fuzzy=1e-4)
        # boolean with automatic fuzzy-tolerance escalation for faces that
        # should touch but sit a sub-tolerance gap apart (the classic "fuse
        # leaves two disjoint solids" / invalid-cut failure). Tries the plain
        # operator, then raw OCCT at fuzzy and 10x fuzzy. Raises if all fail.

CONSTRAINT SKETCH SOLVER  (agentcad.toolkit.sketch; tool: solve_sketch)
----------------------------------------------------------------------
A first-party 2D constraint solver (scipy least-squares, ms-scale, machine
precision). Use it to COMPUTE exact coordinates from geometric constraints,
then draw the result with ordinary build123d lines/arcs -- it does not emit
geometry itself. JSON front-end:

    from agentcad.toolkit.sketch import solve_sketch
    r = solve_sketch({
        "points":  [{"name": "a", "x": 0, "y": 0, "fixed": True},
                    {"name": "b", "x": 30, "y": 5}],
        "lines":   [{"name": "ab", "p1": "a", "p2": "b"}],
        "circles": [{"name": "c", "center": "b", "r": 4}],
        "constraints": [
            {"type": "distance", "p": "a", "q": "b", "d": 32},
            {"type": "horizontal", "ln": "ab"},
            {"type": "radius", "c": "c", "r": 5},
        ],
    })
    # r -> {"ok", "max_residual", "dof", "n_residuals",
    #       "points": {"b": {"x": .., "y": ..}}, "circles": {"c": {"cx","cy","r"}}}

Constraint types: fixed, coincident, distance, distance_x, distance_y,
horizontal, vertical, parallel, perpendicular, angle(l1,l2,deg),
point_on_line, point_on_circle, radius, equal_radius, midpoint,
tangent_line_circle(ln,c,at=None), tangent_circles(c1,c2,kind="external").
Reading the result: ok=False or a large max_residual means the system is
unsatisfiable/over-constrained; dof>0 means UNDER-constrained (the answer is
not unique). Give points a good initial (x,y) -- tangent/mirror problems have
several valid branches and the solver walks to the nearest one. An object API
exists too: sk = sketch.Sketch(); sk.point(...); sk.distance(...); sk.solve().

THREADS & FASTENERS  (from agentcad.toolkit import threads; needs bd_warehouse)
------------------------------------------------------------------------------
Simple vs real: cosmetic threads (simple=True) are fast and light -- the right
choice for assembly and fit views. Real ISO threads (simple=False / IsoThread)
are exact but heavy (~9k triangles each) -- use them for manufacturing drawings
and genuine mating only. Never call bd_warehouse ThreadedHole(simple=False)
directly (a ~15 s no-op trap); use the wrappers below.

    threads.threaded_rod(d, pitch, length)        -> Part (thread on its core)
    threads.cap_screw(size="M8-1.25", length=20, simple=False)  -> fastener
    threads.hex_bolt(size="M8-1.25", length=20, simple=False)   -> fastener
        # bearing face at local z=0, head at +z, threaded shank down to -length.

    thr = threads.tapped_hole_thread(d, pitch, depth)   # internal thread solid
    with BuildPart() as part:
        Box(40, 40, 15)
        with Locations(part.faces().sort_by(Axis.Z)[-1]):
            Hole(radius=thr.root_radius, depth=depth)   # bore at ROOT (major/2)
        add(thr)                                        # ridges protrude inward
    # Bore at thr.root_radius so the thread crests reach in to thr.min_radius
    # and add real material. Boring at thr.min_radius (the physical tap-drill)
    # instead buries the ridges in the wall: valid, fast, but NO visible thread.

Interference gotcha: a male thread and a female thread of the same size ALWAYS
interpenetrate as solids (that is how they grip), so a fully-driven bolt fails
check_interference. Keep the bolt's threaded shank inside a clearance
counterbore, or leave a standoff above the tapped thread, or use cosmetic
threads -- see the `fasteners` example.

CONNECTORS & MATES  (optional; backward compatible)
---------------------------------------------------
Add a second top-level function to a part script to declare named connection
frames; instances can then be positioned by MATES instead of hardcoded
transforms. Both are optional -- omit for plain placement.

    def connectors(p, part) -> dict:
        # p = the resolved params namespace (as build receives); part = the
        # built shape, so connectors can be derived from topology. Locations
        # are in the part's LOCAL frame.
        top = part.faces().sort_by(Axis.Z)[-1]
        return {
            "seat":  {"type": "rigid", "location": (0, 0, p.height)},
            "hinge": {"type": "revolute", "axis": ((0, 0, 0), (1, 0, 0)),
                      "range": (0, 180)},
            "bore":  {"type": "cylindrical", "axis": ((0, 0, 0), (0, 0, 1))},
        }
    # location accepts (x,y,z) | ((pos),(rot)) | a Location | a Plane.
    # axis accepts ((point),(direction)) | an Axis.

Manifest mate on an instance (the service resolves it to a concrete
position/rotation_deg at read time):

    {"id": "lid", "part": "lid",
     "mate": {"connector": "seat",          # ON THIS instance -- must be RIGID
              "to_instance": "box",          # the anchor instance id
              "to_connector": "rim",         # anchor connector: carries the DOF
              "params": {"angle": 30.0,      # revolute/cylindrical only (deg)
                          "position": 5.0}}} # cylindrical only (mm)

Rules: the moving side (`connector`) must be rigid; the ANCHOR connector's type
(rigid/revolute/cylindrical) decides the joint. A rigid mate takes no params.
Instances with no mate are roots (world = their position/rotation_deg). Mates
form a forest resolved in topological order; a cycle is rejected. A mate-driven
instance cannot be nudged with set_instance_transform (409 -- clear the mate
first). Tools: set_mate / clear_mate.
"""
