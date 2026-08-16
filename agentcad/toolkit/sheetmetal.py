"""Declarative sheet metal: one spec yields the folded solid AND the flat pattern.

A ``SheetPart`` is a rectangular base plate plus flanges, hems and corner
treatments. From that single declaration you get ``fold()`` (the bent solid for
modeling/assembly), ``unfold()`` (the flat blank as a solid),
``flat_outline()`` (the blank's 2D polygon), ``flat_outline_edges()`` (the same
outline as exact segments and arcs) and ``bend_lines()`` (where to bend, in
flat coordinates) — so the part on screen and the pattern sent to the
laser/brake can never disagree.

Conventions (mm / degrees):
  * ``base(width, depth)``: footprint centered on the origin, width along X,
    depth along Y, thickness extruded +Z (plate occupies z in [0, t]).
  * Edges: "left" x=-width/2, "right" x=+width/2, "front" y=-depth/2,
    "back" y=+depth/2. Flanges bend UP (+Z). ``angle_deg`` is the bend angle,
    exclusive (0, 180); 90 is the common case. ``inner_radius`` defaults to the
    sheet thickness. 180 is reachable only through ``hem()``.
  * ``start`` / ``width`` place a **partial** flange on an edge. ``start`` is
    measured from the edge's LOW-coordinate end — X- for "front"/"back", Y- for
    "left"/"right" — and ``width=None`` means the whole edge, which is v1's
    meaning and v1's exact geometry. Several flanges may share an edge as long
    as their ``[start, start+width)`` spans do not overlap.
  * Bend allowance BA = radians(angle) * (inner_radius + k_factor * thickness).
    Each flange adds BA + length of flat stock beyond its edge; k_factor 0.44
    suits air-bent mild steel / aluminum — tune it per process.

Guidance for agents: in a part script build the SheetPart from p inside a
helper, return ``sp.fold()`` from build(p), and add the optional contract
function ``flat_pattern(p)`` returning ``(sp.unfold(), sp.bend_lines())`` to
enable the ``flat_pattern`` export tool (SVG/DXF with a BEND layer).

What was measured (changelogs 0157 and 0158)
--------------------------------------------
**Fold and unfold disagree by exactly one number, and it is the k-factor's.**
The solid model puts the neutral fibre at t/2 (a bend sector of volume
``angle * t * (R + t/2) * span``); the flat model puts it at ``k*t``. So

    fold().volume - unfold().volume = angle_rad * (0.5 - k) * t^2 * span

**per bend**, and — *for a part with no mitred corner* — nothing else. Measured
on the AC4 bracket (60x40x2 plate, one 90 deg flange spanning 30 mm of the front
edge, R=3, leaf 30): fold 6916.991118, unfold 6905.681385, difference
11.309734 mm^3 — the predicted gap to within 1e-9.

**It is a sum over bends, so it grows linearly with the bend count.** Measured
(t=2, k=0.44, R=3, leaf 20, 90 deg): one flange on a 60 mm edge gives
22.619467105842887; a second identical flange on the opposite edge gives
45.238934211700325 — exactly twice, residual 7.3e-12; a third on a 40 mm edge
gives 60.318578948928916 against the 60.31857894892403 the closed form predicts
for 160 mm of bend line. So the honest statement is per-bend and per-mm-of-bend-
line: the gap is the model's own tolerance rather than an error, but it
accumulates, and on a part with many bends it is the *sum* you have to judge
against your process, not the 11 mm^3 of one bend.

A ``close`` corner adds a second, larger term, because a mitre is cut through
the sheet's *thickness* in the fold (a 45 deg plane, so the outer skin runs
t further past the corner than the inner one) and at the *neutral fibre* in
the blank, where there is no thickness to cut through. Measured on the corner
bracket (60x40x2, two 90 deg R3 flanges, leaf 20, ``close``): fold
10441.970395, unfold 10376.632742, difference 65.337653 against the 37.699112
the two bends alone would give. Neither number is wrong; they are the same
model measured on two sides of one k-factor. See ``_mitre_cuts`` for the shape
of the blank's mitre, which is the part of this that is an approximation.

**The outline is the unfold.** ``flat_outline()`` is a discretization of
``unfold()``'s own top face (4.4 ms, measured), not a parallel walker. Its
enclosed area equals that face's area exactly when the blank is straight-edged,
and within the chord tolerance when a round relief puts arcs in it.

**Reliefs are one computation applied twice.** The same solids are cut from
``fold()`` and from ``unfold()``, so a relief can never appear in one and not
the other (measured: rect removes 60.0 mm^3 from both; round 56.1372 from both;
tear 0.0 from both). The fold stayed one valid solid at every thickness from
2.0 mm down to 0.05 mm — the relief is sized from the thickness, so it never
becomes a sliver next to its own sheet.

**A hem is a 180 deg bend and the model shows the air gap as 2R.** OCCT holds
far below anything manufacturable: at 180 deg the fold is one valid solid of
exactly the predicted volume down to R/t = 1e-6. At R/t = 1e-7 OCCT quietly
drops a face (10 -> 9) and the volume drifts by 1.8e-8 relative; at R = 0 the
fold is *still* one valid solid of exactly the right volume but has 8 faces
instead of 10 — the seam between the folded leaf and the sheet is gone, and
nothing distinguishes a hem from 2t of solid stock. So the shipped closed-hem
radius is a **shop** number, not an OCCT limit, and ``inner_radius=0`` is
refused rather than approximated.

**A teardrop hem is not representable here, and it is refused.** In this
model the leaf leaves the bend tangentially, so past 180 deg it descends toward
the sheet and enters it after ``L = R*(1 - cos a)/-sin a`` — measured 2.41*R at
225 deg, 1.43*R at 250 deg, 1.00*R at 270 deg. A hem leaf needs >= 4t. At
225 deg with R = t and a 4t leaf the leaf overlaps the sheet by 144.59 mm^3,
and the fuse *succeeds*: one valid solid, `is_valid` True, with 144.59 mm^3 of
declared material silently gone. ``kind="teardrop"`` therefore raises.

**A ``close`` corner is a closed seam exactly when the two CROSS-SECTIONS agree
and each one fits inside its mitre extension.** The mitre is one vertical 45 degree plane
cutting both leaves, so each leaf's cut face is its own profile read as a
function of outward distance ``u`` from the plate edge: the two faces coincide
exactly when the two profiles do. Measured as the face area the two mitred
flange solids actually share — ``(A.area + B.area - (A+B).area)/2``, against the
``sqrt(2) * min(profile area)`` the mitre promises:

===========================================  ==============  =====
corner (60x40 plate)                         seam / promise  note
===========================================  ==============  =====
90/20/R3 vs 90/20/R3, t=2                    1.000000000000  the seam is whole
90/20/R3 vs 90/10/R3, t=2                    1.000000000000  short leaf is fine
90/20/R3 vs 90/40/R3, t=2                    1.000000000000
120/8/R4.5 both, t=3                         1.000000000000
179/6/R2 both, t=2                           1.000000000000
90/20/R3 vs 90/20/R2.9, t=2                  0.958597256259  radius differs
90/20/R3 vs 89/20/R3, t=2                    0.933701732113  angle differs
90/20/R3 vs 45/10/R1, t=2                    0.267448702599  both differ
45/12/R1 both, t=1                           0.190203814953  acute, LONG leaf
30/25/R0.8 both, t=0.8                       0.069572222109  acute, LONG leaf
45/12/R1 both, t=2                           0.281003186702  L_max 1.2426
45/12/R3 both, t=2                           0.410304292791  L_max 2.0711
45/0.5/R3 both, t=2                          1.000000000000  acute, short leaf
30/1/R5 both, t=2                            1.000000000000  acute, short leaf
===========================================  ==============  =====

The worst matched-and-obtuse case is 1.0 to within 8e-15 relative and the best
mismatched one is 0.9586, so the two populations are eight orders apart and the
screen needs no tuned threshold.

The last two rows are a **second, independent defect**, and it is about the
LEAF LENGTH, not the angle alone. ``_effective_span`` runs a close-corner
flange past the corner by ``inner_radius + thickness``, which is the outward
reach of a 90 degree profile and of nothing else. A profile at bend angle ``a``
reaches ``(R + t)*sin(a) + L*cos(a)``, so the extension holds iff

    L <= L_max = (R + t) * tan(45 deg - a/2)          (``_max_mitre_leaf``)

which is *infinite* at and above 90 degrees — a vertical leaf adds no outward
reach, and past 90 the leaf comes back — and a small positive number below.
Matched acute corners inside that bound seam **whole** and are silent; measured
at t=2, all at ``1.000000000000`` with no warning: 60 deg/R3/L0.2 (reach 4.4301
against 5.0), 45/R3/L0.5 (3.8891 of 5.0), 30/R5/L1 (4.3660 of 7.0), 10/R5/L1
(2.2003 of 7.0), 20/R4/L2 (3.9315 of 6.0), 45/R12/L1 (10.6066 of 14.0). It is
the ordinary long leaf that breaks: 45/R1/L12 wants 10.6066 mm of extension and
gets 3.0, seaming 0.2810. So the rule is the reach, and the code tests the
reach; ``L_max`` is closed-form (verified against a bisection on the reach
predicate to 4e-9 mm over six (t, a, R) combinations) and is what the warning
quotes, because it is the number an author can act on.

Feeding the required reach back into ``_effective_span`` takes the failing rows
to ``1.000000000`` exactly, so this is a fixable modelling bug and not a limit
of the construction; it is reported rather than fixed here because the same
extension has to be re-derived for the blank (``_mitre_cuts(flat=True)``'s
chord is derived at 90 degrees too) and fold and unfold may not diverge.

Neither defect moves any volume: both leaves are cut by the same plane, so
neither can cross it and ``_conserved`` is silent by construction. Nor does
``_checked`` see anything — the parts still fuse through the base plate into one
valid solid. The seam is the only thing that is missing, so the seam is what
has to be measured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import (
    Align,
    Axis,
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    CenterArc,
    Cylinder,
    Line,
    Part,
    Plane,
    extrude,
    make_face,
)

from .boolean import safe_bool

_EDGES = ("left", "right", "front", "back")
# rotation about Z taking the canonical front-edge flange to each edge
_ROT_Z = {"front": 0.0, "right": 90.0, "back": 180.0, "left": 270.0}
# sign relating the canonical (front-edge) extrusion axis to the edge's own
# world axis after that rotation: front/right run with it, back/left against it
_SPAN_SIGN = {"front": 1.0, "right": 1.0, "back": -1.0, "left": -1.0}
# outward normal of each edge, in the plate's XY
_OUT_N = {"front": (0.0, -1.0), "back": (0.0, 1.0),
          "left": (-1.0, 0.0), "right": (1.0, 0.0)}
# the four plate corners, as which END of each edge meets there
_CORNER_ENDS = {
    ("front", "right"): {"front": "hi", "right": "lo"},
    ("right", "back"): {"right": "hi", "back": "hi"},
    ("back", "left"): {"back": "lo", "left": "hi"},
    ("left", "front"): {"left": "lo", "front": "lo"},
}

#: Bend-relief sizing. **No standard governs this** — it is the common shop
#: rule: a slot 1.5 x thickness wide, cut (inner_radius + thickness) past the
#: bend line into the parent material. Pass
#: ``relief={"kind": ..., "width": ..., "depth": ...}`` for a shop with its own.
RELIEF_WIDTH_FACTOR = 1.5
RELIEF_DEPTH_EXTRA = 1.0          # depth = inner_radius + this * thickness
RELIEF_KINDS = ("rect", "round", "tear")

#: Hem inner radii as multiples of the thickness. Also shop defaults: an open
#: hem leaves an air gap of 2R = 2t, a closed hem 2R = t. The measurement
#: behind the closed number is in the module docstring — OCCT would accept far
#: less, and far less stops being a hem.
OPEN_HEM_RADIUS_FACTOR = 1.0
CLOSED_HEM_RADIUS_FACTOR = 0.5
HEM_KINDS = ("open", "closed", "teardrop")

#: Corner treatments. ``gap`` opens this much (x thickness) at the corner.
CORNER_TREATMENTS = ("close", "gap", "rip")
CORNER_GAP_FACTOR = 1.0

#: Default chord tolerance for ``flat_outline()``'s discretization of arcs.
OUTLINE_TOLERANCE = 0.05

_TOL = 1e-9


@dataclass(frozen=True)
class _Flange:
    edge: str
    angle_deg: float
    length: float
    inner_radius: float
    start: float
    width: float
    relief: dict | None = None       # None == tear: no material removed
    hem: str | None = None           # "open" | "closed" when made by hem()


@dataclass(frozen=True)
class _Corner:
    edges: tuple[str, str]
    treatment: str


@dataclass
class _Outline:
    points: list[tuple[float, float]] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)


class SheetPart:
    """Declarative sheet-metal part builder (see module docstring)."""

    def __init__(self, thickness: float, k_factor: float = 0.44):
        if thickness <= 0:
            raise ValueError("thickness must be > 0")
        if not 0 < k_factor < 1:
            raise ValueError("k_factor must be in (0, 1)")
        self.thickness = float(thickness)
        self.k_factor = float(k_factor)
        self.warnings: list[str] = []  # collected from fusion fallbacks
        self._base: tuple[float, float] | None = None
        self._flanges: list[_Flange] = []
        self._corners: list[_Corner] = []
        self._outline_cache: dict[float, _Outline] = {}

    # ------------------------------------------------------------- declaration

    def base(self, width: float, depth: float) -> "SheetPart":
        """Rectangular base plate: width along X, depth along Y, z in [0, t]."""
        if width <= 0 or depth <= 0:
            raise ValueError("base width and depth must be > 0")
        if self._base is not None:
            raise ValueError("base() may only be called once")
        self._base = (float(width), float(depth))
        self._outline_cache.clear()
        return self

    def flange(self, edge: str, angle_deg: float, length: float,
               inner_radius: float | None = None, start: float = 0.0,
               width: float | None = None, relief: str | dict = "auto"
               ) -> "SheetPart":
        """A flange bending UP (+Z) over ``[start, start + width)`` of *edge*.

        ``length`` is the flat leaf beyond the bend zone; ``inner_radius``
        defaults to the thickness; ``width=None`` spans the whole edge (v1's
        meaning, and v1's exact geometry). ``relief`` is ``"auto"`` (= rect),
        ``"rect"``, ``"round"``, ``"tear"``, or an explicit
        ``{"kind", "width", "depth"}``; a relief is cut wherever the flange
        ends in the middle of an edge, i.e. where material remains beside it.
        """
        if not 0.0 < angle_deg < 180.0:
            raise ValueError("angle_deg must be in (0, 180) exclusive; a 180 "
                             "degree bend is a hem — use hem()")
        return self._add_flange(edge, angle_deg, length, inner_radius, start,
                                width, relief, hem=None)

    def hem(self, edge: str, kind: str = "open", length: float = 6.0,
            start: float = 0.0, width: float | None = None,
            inner_radius: float | None = None, relief: str | dict = "auto"
            ) -> "SheetPart":
        """A hem: a 180 degree bend folding the leaf back over the sheet.

        ``kind="open"`` uses R = ``OPEN_HEM_RADIUS_FACTOR`` x t (air gap 2t),
        ``kind="closed"`` R = ``CLOSED_HEM_RADIUS_FACTOR`` x t (air gap t);
        ``inner_radius`` overrides either. Both are shop defaults, not
        standards — the measurement that bounds them is in the module
        docstring. ``kind="teardrop"`` raises: it is not representable in this
        model and is not approximated as a closed hem.
        """
        if kind not in HEM_KINDS:
            raise ValueError(f"hem kind must be one of {list(HEM_KINDS)}, "
                             f"got {kind!r}")
        if kind == "teardrop":
            raise ValueError(
                "hem(kind='teardrop') is refused, not approximated: a teardrop "
                "wraps past 180 degrees, and in this model (a bend sector plus "
                "a leaf leaving it tangentially) the leaf then descends into "
                "the sheet after L = R*(1 - cos a)/-sin a — measured 2.41*R at "
                "225 degrees, 1.00*R at 270 — while a hem leaf needs at least "
                "4*t. Measured at 225 degrees with R = t and a 4*t leaf, the "
                "leaf overlaps the sheet by 144.59 mm^3 and the fuse still "
                "reports one valid solid, so the loss would be silent. Use "
                "kind='open' or 'closed'.")
        t = self.thickness
        if inner_radius is None:
            inner_radius = (OPEN_HEM_RADIUS_FACTOR if kind == "open"
                            else CLOSED_HEM_RADIUS_FACTOR) * t
        elif float(inner_radius) <= 0:
            raise ValueError(
                "hem inner_radius must be > 0. A true zero-radius closed hem "
                "is not representable: measured, R = 0 still folds to one "
                "valid solid of exactly the right volume, but with 8 faces "
                "instead of 10 — the seam between the folded leaf and the "
                f"sheet is gone. The closed-hem default is {CLOSED_HEM_RADIUS_FACTOR}"
                " * thickness.")
        return self._add_flange(edge, 180.0, length, inner_radius, start,
                                width, relief, hem=kind)

    def corner(self, edge_a: str, edge_b: str, treatment: str = "close"
               ) -> "SheetPart":
        """Treat the corner where two adjacent flanged edges meet.

        ``close`` mitres the two leaves: each extends past the corner by its
        own (inner_radius + thickness) and is cut by the 45 degree bisector, so
        they meet along it. ``gap`` opens ``CORNER_GAP_FACTOR`` x thickness on
        both flanges. ``rip`` adds and removes nothing — the untreated corner.

        Declare the two flanges first: this validates that both reach the
        corner.
        """
        if treatment not in CORNER_TREATMENTS:
            raise ValueError(f"treatment must be one of "
                             f"{list(CORNER_TREATMENTS)}, got {treatment!r}")
        key = self._corner_key(edge_a, edge_b)
        for existing in self._corners:
            if set(existing.edges) == set(key):
                raise ValueError(f"duplicate corner {key}")
        for edge in key:
            end = _CORNER_ENDS[key][edge]
            if self._flange_at_corner(edge, end) is None:
                raise ValueError(
                    f"corner{key}: no flange on {edge!r} reaches it — declare "
                    "the flanges before the corner")
        self._corners.append(_Corner(key, treatment))
        self._outline_cache.clear()
        return self

    # ---------------------------------------------------------------- geometry

    def bend_allowance(self, flange: _Flange) -> float:
        """BA = radians(angle) * (inner_radius + k_factor * thickness)."""
        return math.radians(flange.angle_deg) * (
            flange.inner_radius + self.k_factor * self.thickness)

    def fold(self) -> Part:
        """The folded solid: base plate + per-flange bend sector and leaf,
        corner treatments and bend reliefs, fused into a single valid solid.
        Exact volume with no reliefs or corners:
        w*d*t + sum(angle_rad*t*(R + t/2)*span + length*t*span).

        That closed form is also the conservation check: every declared piece
        is counted, every cut is credited with what it *measurably* removed,
        and `_conserved` says so when the fuse kept less than the difference.
        """
        width, depth = self._require_base()
        part = self._base_plate()
        declared = width * depth * self.thickness
        mitred: dict[int, object] = {}
        for index, flange in enumerate(self._flanges):
            lo, hi = self._effective_span(flange, flat=False)
            solid = self._flange_solid(flange, lo, hi)
            declared += self._declared_flange_volume(flange, hi - lo)
            for cut in self._mitre_cuts(flange):
                solid, removed = self._cut_measured(solid, cut)
                declared -= removed
            mitred[index] = solid
            part = self._fuse(part, solid)
        for cut in self._relief_cuts():
            part, removed = self._cut_measured(part, cut)
            declared -= removed
        self._conserved(part, declared, "fold")
        self._corner_seams(mitred)
        return self._checked(part, "fold")

    def unfold(self) -> Part:
        """The flat pattern as a solid in the base plane: base plate plus a
        (BA + length) x span tab per flange, with the same reliefs and corner
        treatments cut from it, thickness unchanged.

        A ``close`` corner is **mitred here too** — the blank has to be
        foldable into the model, and two tabs both running past the corner are
        two tabs claiming one piece of sheet. ``_mitre_cuts(flat=True)`` is
        that cut and carries the one number that differs from the fold's.
        """
        width, depth = self._require_base()
        part = self._base_plate()
        declared = width * depth * self.thickness
        for flange in self._flanges:
            lo, hi = self._effective_span(flange, flat=True)
            tab = self._tab_solid(flange, lo, hi)
            declared += ((self.bend_allowance(flange) + flange.length)
                         * (hi - lo) * self.thickness)
            for cut in self._mitre_cuts(flange, flat=True):
                tab, removed = self._cut_measured(tab, cut)
                declared -= removed
            part = self._fuse(part, tab)
        for cut in self._relief_cuts():
            part, removed = self._cut_measured(part, cut)
            declared -= removed
        self._conserved(part, declared, "unfold")
        self._corner_seams(None)
        return self._checked(part, "unfold")

    def flat_outline(self, tolerance: float = OUTLINE_TOLERANCE
                     ) -> list[tuple[float, float]]:
        """The flat pattern's 2D outline polygon (XY, mm), counter-clockwise,
        starting at the outline VERTEX nearest the (-width/2, -depth/2) base
        corner — the same vertex ``flat_outline_edges()`` starts at, because
        the two are one list sampled two ways and pick their start once.
        (A sampled point in the middle of an arc can be nearer that corner
        than any vertex is; when the two decisions were taken separately,
        that is exactly where they came apart.)

        This is a **discretization of ``unfold()``'s own top face**, not a
        parallel model, so it cannot disagree with the blank: its enclosed area
        equals that face's area exactly for a straight-edged blank, and within
        *tolerance* (a chord tolerance, in mm) where a round relief or a hem
        puts arcs in the boundary. Base corners are vertices only where the
        blank actually turns — a full-edge tab merges with the plate into one
        straight run. Use ``flat_outline_edges()`` for the exact segments and
        arcs.
        """
        return list(self._outline(tolerance).points)

    def flat_outline_edges(self, tolerance: float = OUTLINE_TOLERANCE
                           ) -> list[dict]:
        """The same outline as exact geometry, from the same start vertex as
        ``flat_outline()``, in order and end-to-end:
        ``{"kind": "line", "a": (x, y), "b": (x, y)}`` or
        ``{"kind": "arc", "a", "b", "center", "radius", "ccw"}``. For DXF and
        anyone who should not be handed a polyline."""
        return [dict(e) for e in self._outline(tolerance).edges]

    def bend_lines(self) -> list[dict]:
        """Bend midlines in FLAT coordinates: the bend zone spans [edge,
        edge + BA] outward from the base edge, so each midline sits BA/2
        beyond the edge and spans the flange's own extent along the edge.
        Endpoints a -> b run in ascending order along the edge direction.

        A midline stops where the BLANK does, which at a ``close`` corner is
        not where the tab's span ends: the mitre chord crosses the midline
        half a bend allowance out, having reached only ``rho*sin(a)/2`` past
        the corner instead of the tab's full ``rho`` (1.94 mm against 3.88 on
        the 60x40x2 R3 90 deg corner). Drawn to the span, a bend line would
        hang off the edge of the blank it is drawn on.
        """
        width, depth = self._require_base()
        out = []
        for flange in self._flanges:
            m = {"front": depth, "back": depth, "left": width, "right": width
                 }[flange.edge] / 2 + self.bend_allowance(flange) / 2
            lo, hi = self._effective_span(flange, flat=True)
            rho = flange.inner_radius + self.k_factor * self.thickness
            back = rho * (1 - math.sin(math.radians(flange.angle_deg)) / 2)
            for corner, end in self._corners_of(flange):
                if corner.treatment != "close":
                    continue
                if end == "hi":
                    hi -= back
                else:
                    lo += back
            a, b = {
                "front": ((lo, -m), (hi, -m)),
                "back": ((lo, m), (hi, m)),
                "left": ((-m, lo), (-m, hi)),
                "right": ((m, lo), (m, hi)),
            }[flange.edge]
            out.append({"edge": flange.edge, "a": a, "b": b,
                        "angle_deg": flange.angle_deg,
                        "inner_radius": flange.inner_radius})
        return out

    # -------------------------------------------------------- declaration bits

    def _add_flange(self, edge, angle_deg, length, inner_radius, start, width,
                    relief, hem):
        if self._base is None:
            raise ValueError("call base() before flange()")
        if edge not in _EDGES:
            raise ValueError(f"edge must be one of {_EDGES}, got {edge!r}")
        if length <= 0:
            raise ValueError("flange length must be > 0")
        radius = self.thickness if inner_radius is None else float(inner_radius)
        if radius <= 0:
            raise ValueError("inner_radius must be > 0")
        edge_len = self._edge_length(edge)
        start = float(start)
        span = edge_len - start if width is None else float(width)
        if start < -_TOL:
            raise ValueError(f"start must be >= 0, got {start!r}")
        if span <= 0:
            raise ValueError(f"flange width must be > 0, got {width!r}")
        if start + span > edge_len + _TOL:
            raise ValueError(
                f"flange width: [{start:g}, {start + span:g}) runs off the "
                f"{edge!r} edge, which is {edge_len:g} mm long")
        for other in self._flanges:
            if other.edge != edge:
                continue
            if (start < other.start + other.width - _TOL
                    and other.start < start + span - _TOL):
                raise ValueError(
                    f"flanges overlap on edge {edge!r}: "
                    f"[{other.start:g}, {other.start + other.width:g}) and "
                    f"[{start:g}, {start + span:g})")
        flange = _Flange(edge, float(angle_deg), float(length), radius, start,
                         span, self._relief_spec(relief, radius), hem)
        self._flanges.append(flange)
        self._outline_cache.clear()
        if flange.relief is None and self._relief_ends(flange):
            self._warn(
                f"sheetmetal: flange on {edge!r} uses a 'tear' relief, so the "
                "model shows no material removed at its ends — a tear relief "
                "has none. The sheet tears in the brake instead.")
        return self

    def _relief_spec(self, relief, radius) -> dict | None:
        t = self.thickness
        if relief == "auto":
            relief = "rect"
        if isinstance(relief, str):
            if relief not in RELIEF_KINDS:
                raise ValueError(f"relief must be one of {list(RELIEF_KINDS)}, "
                                 f"'auto', or a dict, got {relief!r}")
            if relief == "tear":
                return None
            return {"kind": relief, "width": RELIEF_WIDTH_FACTOR * t,
                    "depth": radius + RELIEF_DEPTH_EXTRA * t}
        if not isinstance(relief, dict):
            raise ValueError(f"relief must be a string or a dict, got {relief!r}")
        kind = relief.get("kind", "rect")
        if kind not in RELIEF_KINDS:
            raise ValueError(f"relief kind must be one of {list(RELIEF_KINDS)}, "
                             f"got {kind!r}")
        if kind == "tear":
            return None
        w = float(relief.get("width", RELIEF_WIDTH_FACTOR * t))
        d = float(relief.get("depth", radius + RELIEF_DEPTH_EXTRA * t))
        if w <= 0 or d <= 0:
            raise ValueError("relief width and depth must be > 0")
        if kind == "round" and d <= w / 2:
            raise ValueError("a round relief needs depth > width/2")
        return {"kind": kind, "width": w, "depth": d}

    def _corner_key(self, edge_a: str, edge_b: str) -> tuple[str, str]:
        for edge in (edge_a, edge_b):
            if edge not in _EDGES:
                raise ValueError(f"edge must be one of {_EDGES}, got {edge!r}")
        for key in _CORNER_ENDS:
            if set(key) == {edge_a, edge_b}:
                return key
        raise ValueError(f"{edge_a!r} and {edge_b!r} are not adjacent edges")

    # ----------------------------------------------------------------- helpers

    def _require_base(self) -> tuple[float, float]:
        if self._base is None:
            raise ValueError("call base() first")
        return self._base

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _edge_length(self, edge: str) -> float:
        width, depth = self._require_base()
        return width if edge in ("front", "back") else depth

    def _edge_distance(self, edge: str) -> float:
        width, depth = self._require_base()
        return depth / 2 if edge in ("front", "back") else width / 2

    def _base_plate(self):
        width, depth = self._require_base()
        return Box(width, depth, self.thickness,
                   align=(Align.CENTER, Align.CENTER, Align.MIN))

    def _fuse(self, a, b):
        out, warning = safe_bool(a, b, "fuse")
        if warning:
            self._warn(warning)
        return out

    def _cut(self, a, b):
        out, warning = safe_bool(a, b, "cut")
        if warning:
            self._warn(warning)
        return out

    def _cut_measured(self, a, b) -> tuple:
        """``_cut``, reporting what the cut *measurably* removed. A tool that
        overhangs the part removes less than its own volume, so conservation
        must credit the measurement and never the assumption."""
        before = _volume(a)
        out = self._cut(a, b)
        return out, before - _volume(out)

    def _declared_flange_volume(self, flange: _Flange, span: float) -> float:
        """The material this flange declares, before any cut: the fold()
        docstring's closed form for one flange, over *span*."""
        t = self.thickness
        return (math.radians(flange.angle_deg) * t
                * (flange.inner_radius + t / 2) + flange.length * t) * span

    def _conserved(self, part, declared: float, label: str) -> None:
        """Material conservation — the failure ``_checked`` structurally
        cannot see.

        Two declared features that occupy the same space fuse into ONE valid
        solid whose volume is simply smaller than the sum of its parts. OCCT
        raises nothing, ``is_valid`` stays True and the count stays 1, so
        every property ``_checked`` looks at is fine. Measured:
        ``base(60, 40).hem("front", "open", length=50).flange("back", 90, 25)``
        declares 15496.4600 mm^3 and folds to 15256.4600 — the hem leaf,
        50 mm of it on a 40 mm plate, lies inside the back flange's leaf and
        240.0 mm^3 of it is gone with no warning of any kind.

        Only the shortfall is a failure mode here: every piece is declared by
        a closed form and every cut is credited with what it measurably
        removed, so nothing in this construction can hand back more material
        than it was given.
        """
        measured = _volume(part)
        lost = declared - measured
        if lost > max(1e-6, 1e-9 * abs(declared)):
            self._warn(
                f"sheetmetal: {label}() declares {declared:.4f} mm^3 of "
                f"material and measures {measured:.4f} — {lost:.6g} mm^3 was "
                "swallowed. Two declared features occupy the same space and "
                "the fuse absorbed the overlap silently (is_valid stays true "
                "and the count does not change, so nothing else can tell "
                "you). Check a hem or flange leaf long enough to reach "
                "another feature.")

    # --------------------------------------------------------- corner seams

    def _profile_reach(self, flange: _Flange) -> float:
        """How far the flange's cross-section reaches OUTWARD past the plate
        edge, in mm — the distance the mitre plane has to be able to see.

        At bend angle ``a`` the outer skin leaves the bend zone at
        ``(R + t)*sin(a)`` and the leaf then travels ``L*cos(a)`` further out
        (negative past 90 degrees, where the leaf comes back). At exactly
        90 degrees this is ``R + t``, which is what `_effective_span` runs the
        flange past the corner by — and the only angle at which that extension
        is right.

        **From 90 degrees up the leaf term is dropped, and that is exact
        arithmetic rather than a tolerance.** ``cos a <= 0`` for every
        ``a >= 90``, so ``L*cos(a)`` can only pull the reach back and the
        maximum is the skin — but ``math.cos(math.radians(90.0))`` is
        ``6.123233995736766e-17``, not 0, so computing the term anyway leaks a
        positive ``L * 6.12e-17`` into the answer. That grows with the leaf and
        crosses ``_TOL`` at **L = 1.63312e7 mm (16.33 km)**, where the seam
        screen would fire on a *correct* 90 degree corner and then quote
        ``_max_mitre_leaf``'s ``inf`` at the author as "the longest leaf that
        still mitres is inf mm". Unreachable on any real part, and false all
        the same: the two functions have to agree at every input, not merely at
        the ones anybody has tried.
        """
        a = math.radians(flange.angle_deg)
        skin = (flange.inner_radius + self.thickness) * math.sin(a)
        if flange.angle_deg >= 90.0:
            return max(0.0, skin)
        return max(0.0, skin, skin + flange.length * math.cos(a))

    def _mitre_extension(self, flange: _Flange) -> float:
        """What `_effective_span` actually runs a `close` flange past the
        corner by (the fold's value)."""
        return flange.inner_radius + self.thickness

    def _max_mitre_leaf(self, flange: _Flange) -> float:
        """The longest leaf whose profile still fits inside `_mitre_extension`,
        i.e. the largest ``L`` with ``(R+t)*sin a + L*cos a <= R + t``:

            L_max = (R + t) * tan(45 deg - a/2)

        **Infinite at and above 90 degrees**, and a real, small number below —
        verified against a bisection on the reach predicate itself to 4e-9 mm
        on six (t, angle, R) combinations. This is the number to quote at an
        author, because it is the one they can act on.

        **90 degrees is a DISCONTINUITY, not a limit — do not read the ``inf``
        as one.** The one-sided limit runs the other way: ``L_max`` falls to 0
        as ``a -> 90`` from below (measured 0.0436 mm at 89 degrees,
        4.36e-05 at 89.999), and then the value AT 90 is unbounded. The
        constraint the formula came from is
        ``L*cos a <= (R + t)*(1 - sin a)``, and at exactly 90 degrees **both
        sides vanish**, so every ``L`` satisfies it — a vertical leaf adds no
        outward reach at all, and ``R + t`` is the profile's reach whatever the
        leaf does. Dividing through by ``cos a`` to get ``L_max`` is the step
        that loses that, because it divides by something that is zero there.
        The jump is real geometry, not an artifact: the reach predicate agrees
        with it at every leaf length (asserted out to 1e9 mm, six orders past
        the 1.63e7 where the old float leak used to break it).
        """
        half = math.radians(45.0 - flange.angle_deg / 2.0)
        if flange.angle_deg >= 90.0:
            return float("inf")
        return (flange.inner_radius + self.thickness) * math.tan(half)

    def _seam_promise(self, a: _Flange, b: _Flange) -> float:
        """The mitre face area a `close` corner promises, in mm^2.

        Each leaf's cut face is its own cross-section read as a function of
        outward distance, on a plane at 45 degrees to the extrusion axis — so
        it is ``sqrt(2)`` x the cross-section area, and the seam can be no
        larger than the smaller of the two. Measured equal to the shared face
        area to within 8e-15 relative on every matched, >=90 degree corner in
        the module docstring's table.
        """
        return math.sqrt(2) * min(self._declared_flange_volume(a, 1.0),
                                  self._declared_flange_volume(b, 1.0))

    def _corner_seams(self, mitred: dict | None) -> None:
        """Warn when a `close` corner cannot form the seam it promises.

        `_conserved` and `_checked` are both structurally blind here: the two
        leaves are cut by the SAME plane so neither can cross it (no volume
        moves) and they still fuse through the base plate (one valid solid).
        The seam is the only casualty, so it is what gets measured.

        The screen is arithmetic and free — two profiles agree iff their bend
        angle and inner radius do, and a profile fits its mitre iff its
        outward reach fits the extension it was given. It runs on every
        `fold()`/`unfold()`. The `sqrt(2)*min(profile)` identity behind it is
        measured to 8e-15 on matched corners and the worst mismatched corner
        measured 0.9586 of it, so the two populations are eight orders apart.
        The **boolean** probe that turns the screen into a number is paid only
        when the screen has already fired (measured 20 ms per corner), and it
        can still call the corner clean — the screen states a criterion, the
        contact area is the evidence, and evidence wins.
        """
        for corner in self._corners:
            if corner.treatment != "close":
                continue
            pair = []
            for edge in corner.edges:
                flange = self._flange_at_corner(
                    edge, _CORNER_ENDS[corner.edges][edge])
                if flange is None:                    # corner() validated this
                    break
                pair.append(flange)
            if len(pair) != 2:
                continue
            fa, fb = pair
            faults, remedies = [], []
            if abs(fa.angle_deg - fb.angle_deg) > _TOL:
                faults.append(
                    f"the bend angles differ ({fa.angle_deg:g} deg on "
                    f"{fa.edge!r}, {fb.angle_deg:g} on {fb.edge!r})")
            if abs(fa.inner_radius - fb.inner_radius) > _TOL:
                faults.append(
                    f"the inner radii differ ({fa.inner_radius:g} mm on "
                    f"{fa.edge!r}, {fb.inner_radius:g} on {fb.edge!r})")
            if faults:
                remedies.append(
                    "give the two flanges the same bend angle and inner radius")
            for flange in pair:
                reach = self._profile_reach(flange)
                ext = self._mitre_extension(flange)
                if reach <= ext + _TOL:
                    continue
                longest = self._max_mitre_leaf(flange)
                faults.append(
                    f"the {flange.edge!r} leaf reaches {reach:.4f} mm past "
                    f"the plate edge but is only run {ext:.4f} mm past the "
                    f"corner, so the mitre plane never reaches its far end "
                    f"(a leaf leans outward below 90 degrees; at "
                    f"{flange.angle_deg:g} deg with R={flange.inner_radius:g} "
                    f"the longest leaf that still mitres is {longest:.4f} mm, "
                    f"against this one's {flange.length:g})")
                remedies.append(
                    f"shorten the {flange.edge!r} leaf to {longest:.4f} mm or "
                    f"raise its inner_radius (the limit is "
                    f"(inner_radius + thickness) * tan(45 - angle/2), so it "
                    f"grows with either)")
            if not faults:
                continue
            promised = self._seam_promise(fa, fb)
            measured = None
            if mitred is not None:
                measured = self._measured_seam(mitred, fa, fb)
            if measured is not None and measured >= promised * (1 - 1e-9):
                continue                    # the evidence overrules the screen
            got = ("" if measured is None else
                   f" Measured, the two leaves share {measured:.6f} mm^2 of "
                   f"face — {measured / promised:.4f} of it.")
            self._warn(
                f"sheetmetal: corner{corner.edges} is 'close', which promises "
                f"the two leaves meet along the 45 degree bisector over "
                f"{promised:.6f} mm^2, but {'; '.join(faults)}.{got} Nothing "
                "else can tell you: both leaves are cut by the same plane so "
                "no material is lost, and they still fuse through the plate "
                f"into one valid solid. To close it, {'; or '.join(remedies)}"
                "; otherwise say what you mean with 'gap' or 'rip'.")

    def _measured_seam(self, mitred: dict, fa: _Flange, fb: _Flange
                       ) -> float | None:
        """The face area the two mitred leaves actually share, in mm^2.

        Arithmetic off three areas rather than a `&` probe, on `patterns`'
        precedent: an intersection is empty for a face-to-face seam exactly as
        it is for two shapes that never meet, so it cannot tell them apart. A
        fusion that welds two solids along a face hides that face from BOTH
        sides, so the area it loses is twice the contact.
        """
        try:
            a = mitred[self._flange_index(fa)]
            b = mitred[self._flange_index(fb)]
            shared = (float(a.area) + float(b.area) - float((a + b).area)) / 2
        except Exception:                                          # noqa: BLE001
            # An OCCT fuse that will not run is not evidence either way; the
            # screen's own statement still goes out, without a number.
            return None
        return max(0.0, shared)

    def _flange_index(self, flange: _Flange) -> int:
        for index, other in enumerate(self._flanges):
            if other is flange:
                return index
        raise KeyError("flange is not declared on this part")

    def _checked(self, part, label: str):
        """OCCT's 'success' is not evidence: check the shape, not the absence
        of a raise."""
        solids = len(part.solids())
        if not part.is_valid or solids != 1:
            self._warn(
                f"sheetmetal: {label}() produced {solids} solid(s), is_valid="
                f"{bool(part.is_valid)} — the declared features do not join "
                "into one body. Check for a flange that misses its edge or a "
                "corner treatment on flanges that do not meet.")
        return part

    # --------------------------------------------------------- spans & corners

    def _declared_span(self, flange: _Flange) -> tuple[float, float]:
        """[lo, hi] along the edge's own world axis (X for front/back, Y for
        left/right), from the low-coordinate end."""
        lo = -self._edge_length(flange.edge) / 2 + flange.start
        return lo, lo + flange.width

    def _flange_at_corner(self, edge: str, end: str) -> _Flange | None:
        half = self._edge_length(edge) / 2
        want = half if end == "hi" else -half
        for flange in self._flanges:
            lo, hi = self._declared_span(flange)
            if flange.edge == edge and abs((hi if end == "hi" else lo) - want) < 1e-6:
                return flange
        return None

    def _corners_of(self, flange: _Flange):
        """Yield (corner, end) for every declared corner this flange reaches."""
        for corner in self._corners:
            if flange.edge not in corner.edges:
                continue
            end = _CORNER_ENDS[corner.edges][flange.edge]
            if self._flange_at_corner(flange.edge, end) is flange:
                yield corner, end

    def _effective_span(self, flange: _Flange, *, flat: bool
                        ) -> tuple[float, float]:
        lo, hi = self._declared_span(flange)
        t = self.thickness
        for corner, end in self._corners_of(flange):
            if corner.treatment == "rip":
                continue
            if corner.treatment == "close":
                # the leaf runs past the corner to its own outer skin; in the
                # flat there is no through-thickness, so the neutral fibre
                # (k*t) is the honest single value
                delta = flange.inner_radius + (self.k_factor * t if flat else t)
            else:                              # gap
                delta = -CORNER_GAP_FACTOR * t
            if end == "hi":
                hi += delta
            else:
                lo -= delta
        return lo, hi

    def _to_canonical(self, edge: str, u: float) -> float:
        return u if _SPAN_SIGN[edge] > 0 else -u

    # ------------------------------------------------------------------ solids

    def _flange_solid(self, flange: _Flange, lo: float, hi: float):
        """Bend sector + leaf, built as a 2D cross-section in the plane
        perpendicular to the edge and extruded along the flange's span.
        Canonical frame is the front edge (profile in YZ, extruded along X);
        other edges rotate the result about Z."""
        t, radius = self.thickness, flange.inner_radius
        angle = math.radians(flange.angle_deg)
        dist = self._edge_distance(flange.edge)
        edge_w = self._edge_length(flange.edge)
        u0, u1 = sorted((self._to_canonical(flange.edge, lo),
                         self._to_canonical(flange.edge, hi)))
        full = abs(u0 + edge_w / 2) < _TOL and abs(u1 - edge_w / 2) < _TOL
        # bend axis (canonical, in (y, z)): the inner surface is tangent to the
        # plate top face at the edge, so the center sits at (-dist, t + R)
        cy, cz = -dist, t + radius

        def pt(r: float, th: float) -> tuple[float, float]:
            return (cy - r * math.sin(th), cz - r * math.cos(th))

        in0, out0 = pt(radius, 0.0), pt(radius + t, 0.0)      # plate end-face
        in1, out1 = pt(radius, angle), pt(radius + t, angle)  # sector end
        tang = (-math.cos(angle), math.sin(angle))            # leaf direction
        leaf_in = (in1[0] + flange.length * tang[0], in1[1] + flange.length * tang[1])
        leaf_out = (out1[0] + flange.length * tang[0], out1[1] + flange.length * tang[1])

        with BuildPart() as bp:
            with BuildSketch(Plane.YZ):
                with BuildLine():
                    CenterArc((cy, cz), radius, 270 - flange.angle_deg,
                              flange.angle_deg)
                    CenterArc((cy, cz), radius + t, 270 - flange.angle_deg,
                              flange.angle_deg)
                    Line(in0, out0)
                    Line(in1, leaf_in)
                    Line(out1, leaf_out)
                    Line(leaf_in, leaf_out)
                make_face()
            if full:                       # v1's call, byte-for-byte
                extrude(amount=edge_w / 2, both=True)
            else:
                extrude(amount=u1 - u0)
        solid = bp.part if full else bp.part.translate((u0, 0, 0))
        rot = _ROT_Z[flange.edge]
        return solid.rotate(Axis.Z, rot) if rot else solid

    def _tab_solid(self, flange: _Flange, lo: float, hi: float):
        """The unfolded tab: (BA + length) of stock beyond the edge, over the
        flange's span."""
        t = self.thickness
        ext = self.bend_allowance(flange) + flange.length
        dist = self._edge_distance(flange.edge)
        span, mid = hi - lo, (lo + hi) / 2
        if flange.edge in ("front", "back"):
            size = (span, ext, t)
            cx = mid
            cy = -(dist + ext / 2) if flange.edge == "front" else dist + ext / 2
        else:
            size = (ext, span, t)
            cx = -(dist + ext / 2) if flange.edge == "left" else dist + ext / 2
            cy = mid
        return Box(*size, align=(Align.CENTER, Align.CENTER, Align.MIN)
                   ).translate((cx, cy, 0))

    def _relief_ends(self, flange: _Flange) -> list[float]:
        """The declared ends (in world edge-axis coordinates) where the flange
        stops in the middle of an edge, so material remains beside it."""
        half = self._edge_length(flange.edge) / 2
        lo, hi = self._declared_span(flange)
        return [u for u, limit in ((lo, -half), (hi, half))
                if abs(u - limit) > 1e-6]

    def _relief_cuts(self) -> list:
        cuts = []
        t = self.thickness
        for flange in self._flanges:
            spec = flange.relief
            if spec is None:
                continue
            dist = self._edge_distance(flange.edge)
            rw, rd = spec["width"], spec["depth"]
            for u in self._relief_ends(flange):
                cu = self._to_canonical(flange.edge, u)
                depth = rd if spec["kind"] == "rect" else rd - rw / 2
                solid = Box(rw, depth, 3 * t,
                            align=(Align.CENTER, Align.MIN, Align.CENTER)
                            ).translate((cu, -dist, t / 2))
                if spec["kind"] == "round":
                    solid = solid.fuse(
                        Cylinder(rw / 2, 3 * t).translate(
                            (cu, -dist + depth, t / 2)))
                rot = _ROT_Z[flange.edge]
                cuts.append(solid.rotate(Axis.Z, rot) if rot else solid)
        return cuts

    def _mitre_cuts(self, flange: _Flange, *, flat: bool = False) -> list:
        """For each `close` corner this flange reaches, the half-space beyond
        the mitre — cut it away and the two leaves meet on it.

        In the FOLD the mitre is the 45 degree bisector through the corner
        (normal ``n_b - n_a``): both leaves stand in that plane, so one plane
        cuts both and they meet on it.

        In the BLANK the same joint is not 45 degrees, because the bisector
        crosses the bend zone while the sheet is still rolled up. At a
        distance ``u`` along the tab the material has travelled only
        ``rho*sin(u/rho)`` past the bend line once folded
        (``rho = R + k*t``, the neutral fibre), so the exact unrolled mitre is
        the curve ``v = rho*sin(u/rho)`` — neither a line nor an arc. A
        spline through it would make the blank an approximation of itself and
        hand ``flat_outline_edges()`` a curve kind it has no word for, so we
        cut the CHORD of that curve instead: the line through its two ends,
        slope ``sin(a)/a``, which is exact at the bend line and again at the
        end of the bend zone. Sine is concave over (0, 90 deg], so the chord
        is the **steepest straight mitre that never over-runs the bisector**:
        the blank comes out a little small and never a little large. Measured
        on the 60x40x2 R3 90 deg corner it is 3.231 mm^2 per tab (12.923 mm^3
        of blank), a mitre gap peaking at 0.815 mm a third of the way through
        the bend zone and closing to zero at both ends of it. Over-running
        would instead fold two leaves into the same space — the silent
        overlap `_conserved` exists to catch.
        """
        width, depth = self._require_base()
        big = 4 * (width + depth)
        out = []
        for corner, _end in self._corners_of(flange):
            if corner.treatment != "close":
                continue
            other = corner.edges[1] if corner.edges[0] == flange.edge else corner.edges[0]
            na, nb = _OUT_N[flange.edge], _OUT_N[other]
            slope = 1.0
            if flat:
                angle = math.radians(flange.angle_deg)
                slope = math.sin(angle) / angle
            nx, ny = nb[0] - slope * na[0], nb[1] - slope * na[1]
            norm = math.hypot(nx, ny)
            nx, ny = nx / norm, ny / norm
            cx = (width / 2 if "right" in corner.edges else -width / 2)
            cy = (-depth / 2 if "front" in corner.edges else depth / 2)
            half = Box(big, big, big,
                       align=(Align.MIN, Align.CENTER, Align.CENTER))
            out.append(Plane(origin=(cx, cy, 0.0), x_dir=(nx, ny, 0.0),
                             z_dir=(0.0, 0.0, 1.0)) * half)
        return out

    # ----------------------------------------------------------------- outline

    def _outline(self, tolerance: float) -> _Outline:
        if tolerance <= 0:
            raise ValueError("outline tolerance must be > 0")
        cached = self._outline_cache.get(tolerance)
        if cached is not None:
            return cached
        width, depth = self._require_base()
        flat = self.unfold()
        face = flat.faces().filter_by(Plane.XY).sort_by(Axis.Z)[-1]
        edges = face.outer_wire().order_edges()
        exact: list[dict] = []
        # the polygon is the edge list, sampled: each edge contributes its
        # start vertex (kept in `exact`) plus its own interior samples, so the
        # two lists stay index-aligned and ONE start decision serves both
        interiors: list[list[tuple[float, float]]] = []
        for edge in edges:
            kind = getattr(edge.geom_type, "name", str(edge.geom_type))
            a, b = edge @ 0, edge @ 1
            if kind == "LINE":
                exact.append({"kind": "line", "a": _xy(a), "b": _xy(b)})
                interiors.append([])
                continue
            radius = float(getattr(edge, "radius", 0.0) or 0.0)
            centre = edge.arc_center
            exact.append({"kind": "arc", "a": _xy(a), "b": _xy(b),
                          "center": _xy(centre), "radius": round(radius, 9),
                          "ccw": _arc_is_ccw(edge)})
            n = _arc_samples(edge, radius, tolerance)
            interiors.append([_xy(edge @ (i / (n - 1)))
                              for i in range(1, n - 1)])
        if _signed_area([p for e, ins in zip(exact, interiors)
                         for p in (e["a"], *ins)]) < 0:
            # walk the loop the other way: each edge keeps its own interior
            # samples, reversed, but starts at what used to be its end
            exact = [{**e, "a": e["b"], "b": e["a"],
                      **({"ccw": not e["ccw"]} if e["kind"] == "arc" else {})}
                     for e in reversed(exact)]
            interiors = [list(reversed(ins)) for ins in reversed(interiors)]
        start = min(range(len(exact)),
                    key=lambda i: math.dist(exact[i]["a"],
                                            (-width / 2, -depth / 2)))
        exact = exact[start:] + exact[:start]
        interiors = interiors[start:] + interiors[:start]
        points = [p for e, ins in zip(exact, interiors)
                  for p in (e["a"], *ins)]
        out = _Outline(points, exact)
        self._outline_cache[tolerance] = out
        return out


# --------------------------------------------------------------- free helpers

def _xy(p) -> tuple[float, float]:
    return (round(p.X, 9), round(p.Y, 9))


def _volume(shape) -> float:
    """A boolean result is routinely a nested Compound, whose ``.volume``
    reports only the first child subtree — sum the solids instead."""
    return sum(s.volume for s in shape.solids())


def _signed_area(points) -> float:
    area = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        area += x0 * y1 - x1 * y0
    return area / 2


def _arc_samples(edge, radius: float, tolerance: float) -> int:
    """Points per arc for a sagitta no larger than *tolerance*."""
    if radius <= tolerance:
        return 4
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - tolerance / radius)))
    return max(3, int(math.ceil((edge.length / radius) / step)) + 1)


def _arc_is_ccw(edge) -> bool:
    centre = edge.arc_center
    a, mid = edge @ 0, edge @ 0.5
    return ((a.X - centre.X) * (mid.Y - centre.Y)
            - (a.Y - centre.Y) * (mid.X - centre.X)) > 0
