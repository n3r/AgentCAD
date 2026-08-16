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
(see DESIGN SPECS below). A failing spec NEVER fails the rebuild -- the
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

PATTERNS  (from agentcad.toolkit import patterns)
-------------------------------------------------
Two kinds of thing. POINT SETS are pure arithmetic and are what you want for
holes; SHAPE PATTERNS copy a solid that is already fused into the part.

    patterns.bolt_circle(r, n, start_deg=0.0)   -> [(x, y), ...]
    patterns.grid(nx, ny, dx, dy, center=True)  -> [(x, y), ...] row-major
        # rounded to 9 decimals so trig noise never reaches a coordinate.
        # NOT PolarLocations: these translate, that also ROTATES each copy,
        # and the two tessellate differently. Build a rotated group in its
        # own frame and rotate the result, or the rounding bites.

    part, warn = patterns.linear(part, seed, direction, count, spacing)
    part, warn = patterns.polar(part, seed, axis=Axis.Z, count=4,
                                radius=None, span_deg=360.0)
    part, warn = patterns.mirror(part, plane=Plane.YZ, seed=None)
        # `seed` is a Shape ALREADY in the part, so `count` is the TOTAL
        # instance count (CAD convention) and instance 0 is skipped;
        # count=1 is a no-op with a warning. span_deg < 360 is inclusive.

WHY EVERY HELPER RETURNS A WARNING: OCCT does not fail on a badly placed
feature. A cut placed entirely off the part succeeds in ~1 ms, leaves the
volume EXACTLY unchanged and reports is_valid True. So the helpers measure
engagement themselves: a bounding-box screen per instance always (0.014 ms),
plus verify="exact" for the real (part & tool) probe (2.1 ms each, roughly
doubles a 50-instance pattern) reporting engaged_mm3. Warnings name the
INSTANCE INDICES. Read patterns.instances(part) for the per-instance report.
Impossible arguments (count < 1, spacing <= 0, span outside (0, 360]) RAISE.

HOLE WIZARD  (from agentcad.toolkit import holes; tool: hole_standards, add_holes)
---------------------------------------------------------------------------------
One call per intent, diameters from vendored ISO 273 / ISO 261-262 / ASME
B18.2.8 / Unified tables (never invented here), and a machine-readable RECORD
that reaches the drawing callouts and get_part.

    part, recs, warn = holes.clearance(part, points, "M5", fit="medium")
    part, recs, warn = holes.tapped(part, points, "M6", depth=12)
    part, recs, warn = holes.counterbore(part, points, "M8")
    part, recs, warn = holes.countersink(part, points, "M6")
    part, recs, warn = holes.drill(part, points, 18.0)     # no table, mm
    part, recs, warn = holes.clearance(part, pts, "1/4", std="ansi")

Common kwargs: plane=, depth= (omit for through), thru=, std="iso"|"ansi",
fit="fine|medium|coarse" (or the ASME spellings close|normal|loose),
verify="bbox"|"exact"|"off".

    plane= is a build123d Plane, or one of top|bottom|front|back|left|right --
    a PREDICATE re-evaluated every rebuild (the extreme planar face along that
    axis, largest area, then lowest centre), never a face index. `points` are
    (u, v) in that plane: top -> (x, y) drilling -Z; front -> (x, z) drilling
    +Y; left -> (-y, z) drilling +X. A name that resolves to nothing raises.

    holes.drill is the one with no table behind it: a structural bolt hole has
    no fastener row (18 mm for an M16 is none of ISO 273's values), so it takes
    millimetres and its record carries NO size and NO provenance.

    tapped() bores the TAP DRILL and records the thread -- a tapped hole on a
    drawing is a callout, not a helix. thread="real" additionally fuses real
    thread geometry, needs a depth, costs ~9k triangles per hole, and bores at
    root_radius instead (boring at the tap drill buries the ridges: valid,
    fast, invisible). countersink() always passes its angle explicitly --
    build123d's default is 82 deg, which is ASME's; ISO is 90.

Designations, per the hole's own standard (ISO | ASME):
    clearance    (/)5.5              | (/)0.217
    tapped       M5x0.8 - 6H depth12 | 10-24 UNC - 2B depth0.5
    counterbore  (/)5.5 cbore(/)9.5  | countersink (/)5.5 csk(/)10.4x90deg
(the real strings use the ISO 129 glyphs; ask the hole_standards tool for one.)

THE RECORDS. Each call makes ONE GROUP record -- {id, family, standard,
designation, size, fit, d, count, positions, centers, axis, plane, depth_mm,
thru, tap, cbore, csk, instances} -- so a pattern of a wizard hole is one hole
group, not N unrelated records. They ride on the shape the helper RETURNS:

    holes.records(part)        -> the records carried by this shape
    holes.carry(new, old)      -> after a RAW build123d op of your own

Every operation that returns a new object (safe_fillet, safe_bool, a raw
part - tool, a re-entered BuildPart) drops the attribute; the safe_* helpers
carry it for you, a raw op does not, and the build result warns when records
went missing. Rebuild results and get_part gain `holes`: [...] the records,
null "declares none", [] + a warning "they were dropped", key absent "not
harvested".

Guards you will actually see: an instance off the part or off the face
(named by index), a depth deeper than the stock below the plane, a new hole
within one diameter of another or of an existing record. An unknown size or a
negative depth RAISES, and comes back as a normal script_error with the line.

    from agentcad.toolkit import holes, patterns
    def build(p):
        part = Box(120, 120, p.t)
        part, recs, warn = holes.tapped(part, patterns.bolt_circle(45, 8),
                                        "M5", depth=10)
        return part                     # drawing prints: 8x M5x0.8 - 6H depth10

RIBS, BOSSES & DRAFT  (from agentcad.toolkit import features)
-------------------------------------------------------------
    part, warn = features.rib(part, profile, thickness, to=8.0, plane="top",
                              draft_deg=None)
        # `profile` is a polyline of (u, v) points in the plane's coordinates,
        # traced to `thickness` and extruded away from the seat. to=<mm> is
        # exact. to="part" intersects the part's BOUNDING SOLID -- an envelope,
        # not the part: on a convex part it adds 0 mm3, on a shelled one it
        # runs to the bbox top. It always warns. draft_deg TAPERS the
        # extrusion; it never calls the draft operation (see below).
    part, warn = features.boss(part, at, d, h, hole="M3", draft_deg=None)
        # a cylinder standing h above the seat; hole="M3" bores the tap drill
        # through holes.tapped so the screw boss carries a RECORD.
    part, achieved_deg, warn = features.draft(part, faces, angle_deg,
                                              neutral_plane, min_angle=0.25)
        # `faces` is a list of Face objects or a selector callable f(part) ->
        # faces. NEVER indices. On failure it binary-searches DOWN.

`plane` is holes' predicate and the normal points OUT of the material: holes
drill along -z_dir, ribs and bosses grow along +z_dir. After you add a rib,
"top" resolves to the RIB's top face -- pass an explicit Plane for anything
inside a cavity.

DRAFT'S CEILINGS ARE LOW AND ITS FAILURE IS QUIET. Measured through the
worker: box 35 deg, box+boss 15, box+fillets 10, shelled box 2.5,
gusset_plate 17.5, prototyping/enclosure_base 0.25, and rocketry/nozzle and
construction/angle_bracket refuse EVERY angle. Failure is monotone in the
angle (no islands). Only the extreme angles raise -- most failing angles
RETURN a shape with is_valid False and a plausible volume, so a hand-written
draft() must check is_valid itself. Draft before you shell or fillet; when
nothing works features.draft returns the part unchanged and says so.

Both a rib that misses the part and a boss placed in the air FUSE HAPPILY:
volume rises by exactly the right amount, is_valid True, nothing raised. The
only tells are the solid count and an intersection probe, which is what the
helpers' warnings report.

CONSTRAINT SKETCH SOLVER  (agentcad.toolkit.sketch; tool: solve_sketch)
----------------------------------------------------------------------
A first-party 2D constraint solver (scipy least-squares, ms-scale, machine
precision). Use it to COMPUTE exact coordinates from geometric constraints;
it can also EMIT the build123d source for the solved profile. JSON front-end:

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
    # r -> {"ok", "max_residual", "rank", "dof", "n_residuals", "diagnostics",
    #       "points": {"b": {"x": .., "y": ..}}, "circles": {"c": {"cx","cy","r"}}}

Entities: points, lines, circles,
  arcs     {name, center, r, start_deg, end_deg} or {name, start, mid, end}
  ellipses {name, center, a, b, rotation} (+ start_deg/end_deg = elliptical arc)
  splines  {name, points: [<point names>]}          (degree 3, through-points)
  slots    {name, c1, c2, width}                    (compiled: 2 arcs + 2 lines)
Any entity may carry "construction": True -- it constrains but never emits.
VIRTUAL HANDLES cost nothing and take the whole point vocabulary: <arc>.start,
<arc>.end, <ellipse>.center/.major/.minor/.start/.end, <spline>.start/.end,
<slot>.arc_a/.side_1..., plus the scalar handles <ellipse>.a / <ellipse>.b for
radius/equal_radius (an ellipse has two radii; name the one you mean).

Constraint types: fixed, coincident, distance, distance_x, distance_y,
horizontal, vertical, parallel, perpendicular, angle(l1,l2,deg),
point_on_line, point_on_circle, radius, equal_radius, midpoint,
tangent_line_circle(ln,c,at=None), tangent_circles(c1,c2,kind="external"),
tangent(a,b,at?,kind?), symmetric(a,b,about), equal_length(l1,l2),
concentric(a,b).
Reading the result: ok=False or a large max_residual means the system is
unsatisfiable; "diagnostics" carries status/dof/rank/free_entities and, when
over-constrained, redundant/conflicting naming the dependent constraints by
their index (a redundant-but-consistent one is NOT an error; a conflicting one
raises). dof = n_params - rank(J) and is never negative; dof>0 means
UNDER-constrained (the answer is not unique). Give points a good initial (x,y),
or pass "initial" -- it selects the solution BRANCH (tangent/mirror problems
have several), it is not the speed knob.
Emission: "emit": "function"|"buildline" returns {code, warnings} -- the same
emitter the GUI uses, at 9 decimals behind a 1e-8 mm closure gate. Add
"persist": "<name>" and the code is wrapped in a round-trip block carrying the
spec and a hash, so the sketch can be reopened; "plane": {origin, x_dir,
normal, ...} from the sketch_plane tool emits onto a picked face's plane.
An object API exists too: sk = sketch.Sketch(); sk.point(...); sk.distance(...);
sk.solve().

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

SHEET METAL  (from agentcad.toolkit.sheetmetal import SheetPart; tool: flat_pattern)
------------------------------------------------------------------------------------
One declarative spec yields BOTH the folded solid and the manufacturing flat
pattern, so they can never disagree. Base plate centered on the origin, width
along X, depth along Y, z in [0, t]; edges left/right/front/back (x=-w/2,
x=+w/2, y=-d/2, y=+d/2). Flanges bend UP (+Z); angle in (0, 180) exclusive;
inner_radius defaults to the thickness. start/width place a PARTIAL flange
(start from the edge's low-coordinate end, X- or Y-; width=None = whole edge);
several per edge as long as their spans do not overlap. Bend allowance
BA = radians(angle) * (R + K*t); each flange adds BA + length of flat stock
beyond its edge (K=0.44 default suits air-bent steel/aluminum).

    def _sheet(p):
        return (SheetPart(p.thick, k_factor=0.44)
                .base(p.width, p.depth)
                .flange("front", 90, p.flange_len, inner_radius=p.bend_r))
    def build(p):
        return _sheet(p).fold()          # single valid folded solid
    def flat_pattern(p):                 # optional contract -> enables the
        sp = _sheet(p)                   # flat_pattern export tool
        return sp.unfold(), sp.bend_lines()

    sp.unfold()       -> flat blank as a solid (base + BA+length tab per edge)
    sp.flat_outline() -> [(x, y), ...] CCW outline polygon of the blank; it is
                         a discretization of unfold()'s OWN top face, not a
                         second model, so it cannot disagree with the blank
                         (it therefore COSTS an unfold(), 11-70 ms, not free)
    sp.flat_outline_edges() -> the same outline as exact lines and arcs
    sp.bend_lines()   -> [{"edge","a","b","angle_deg","inner_radius"}, ...]
                         midlines spanning each flange's own extent, flat coords

fold() and unfold() differ in volume by EXACTLY radians(angle)*(0.5-K)*t^2*span
per bend -- the solid puts the neutral fibre at t/2, the flat model at K*t --
and by nothing else. That is the bend-allowance model's own tolerance, it does
not accumulate with feature count, and it is not a bug to report.

Bend relief is cut automatically wherever a partial flange stops in the middle
of an edge, in BOTH fold() and unfold(): relief="auto"|"rect"|"round"|"tear"
or {"kind","width","depth"}. The default size (1.5*t wide, R+t past the bend
line) is a SHOP RULE, not a standard. "tear" removes nothing and warns.

    sp.hem(edge, kind="open"|"closed", length, start=0.0, width=None)
        a 180 deg bend folding the leaf back over the sheet; the air gap is
        2R -- open R=t (gap 2t), closed R=t/2 (gap t), both shop defaults.
        kind="teardrop" RAISES: past 180 deg the leaf descends into the sheet
        after R*(1-cos a)/-sin a (2.41*R at 225 deg) while a hem leaf needs
        >= 4t, and the fuse swallows the overlap silently. Not approximated.
    sp.corner(edge_a, edge_b, "close"|"gap"|"rip")
        close mitres the two leaves on the 45 deg bisector; gap opens one
        thickness; rip is the untreated corner. Declare the flanges first.

The flat_pattern tool renders the unfolded blank to SVG (outline + dashed
bend lines with angle/radius callouts) or DXF (layers OUTLINE and BEND) at
exports/<part>_flat.<ext>. Overlapping spans on one edge, angle 0/180 in
flange(), or flange() before base() raise ValueError; read sp.warnings after
fold() -- it records fusion fallbacks AND a fold that did not come out as one
valid solid, because OCCT reporting success is not evidence that it did.

DESIGN SPECS  (optional; from agentcad.toolkit.specs import ...)
----------------------------------------------------------------
Write the design intent down as code and the kernel checks it on every
rebuild. Declarations are pure data -- no geometry, no measurement here.

    from agentcad.toolkit.specs import check_mass, check_that, check_wall

    SPECS = [
        check_wall(min_mm=2.5, grid=8, requirement="ENG-014"),
        check_mass(max_g=120.0, requirement="SYS-042"),
        check_that(lambda part, metrics:
                   metrics["bbox"]["max"][2] - metrics["bbox"]["min"][2] <= 80,
                   name="fits_fairing"),
    ]

Part scope (in this script):  check_valid() | check_mass(min_g, max_g) |
check_volume(min_mm3, max_mm3) | check_bbox(within_mm) | check_wall(min_mm,
grid=8) | check_that(fn, name) | check_fem_static(fixed_face, load_face,
load_N, max_vm_mpa=, max_disp_mm=)   # faces are {"axis": "z", "side": "max"}

Project scope (in the project's root specs.py, over assembly instance ids):
check_interference_free() | check_clearance(a, b, min_mm) |
check_stackup(from_instance, to_instance, axis, within)

Every constructor takes name= (a default is derived: wall_min, mass_max, ...)
and requirement= -- an opaque id or URL ("SYS-042") that groups checks in the
run_specs report. Arguments are validated EAGERLY, so a bad limit raises while
this module executes and comes back as a script error with a line number.

A rebuild evaluates the shape tier only (valid/mass/volume/bbox/wall/that);
assembly and FEM checks report skip + "deferred" there and are measured by the
run_specs tool. check_wall is a SAMPLED ray cast along the inward face normal
-- it finds chamfers and fillet runouts, so the measured minimum is not the
nominal wall: pick the limit from a measurement and pin grid (cost is
quadratic in it). Skips carry a reason and a hint and are not failures; an
"error" status means the check itself broke, which is not "it is fine".

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
