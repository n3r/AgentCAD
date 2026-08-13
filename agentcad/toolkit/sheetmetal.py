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

per bend, and nothing else. Measured on the AC4 bracket (60x40x2 plate, one
90 deg flange spanning 30 mm of the front edge, R=3, leaf 30): fold 6916.991118,
unfold 6905.681385, difference 11.309734 mm^3 — the predicted gap to within
1e-9. That difference is the model's own tolerance; it is not an error and it
does not grow with the number of features.

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
        w*d*t + sum(angle_rad*t*(R + t/2)*span + length*t*span)."""
        self._require_base()
        part = self._base_plate()
        for flange in self._flanges:
            lo, hi = self._effective_span(flange, flat=False)
            solid = self._flange_solid(flange, lo, hi)
            for cut in self._mitre_cuts(flange):
                solid = self._cut(solid, cut)
            part = self._fuse(part, solid)
        for cut in self._relief_cuts():
            part = self._cut(part, cut)
        return self._checked(part, "fold")

    def unfold(self) -> Part:
        """The flat pattern as a solid in the base plane: base plate plus a
        (BA + length) x span tab per flange, with the same reliefs and corner
        treatments cut from it, thickness unchanged."""
        self._require_base()
        part = self._base_plate()
        for flange in self._flanges:
            lo, hi = self._effective_span(flange, flat=True)
            part = self._fuse(part, self._tab_solid(flange, lo, hi))
        for cut in self._relief_cuts():
            part = self._cut(part, cut)
        return self._checked(part, "unfold")

    def flat_outline(self, tolerance: float = OUTLINE_TOLERANCE
                     ) -> list[tuple[float, float]]:
        """The flat pattern's 2D outline polygon (XY, mm), counter-clockwise,
        starting at the vertex nearest the (-width/2, -depth/2) base corner.

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
        """The same outline as exact geometry, in order and end-to-end:
        ``{"kind": "line", "a": (x, y), "b": (x, y)}`` or
        ``{"kind": "arc", "a", "b", "center", "radius", "ccw"}``. For DXF and
        anyone who should not be handed a polyline."""
        return [dict(e) for e in self._outline(tolerance).edges]

    def bend_lines(self) -> list[dict]:
        """Bend midlines in FLAT coordinates: the bend zone spans [edge,
        edge + BA] outward from the base edge, so each midline sits BA/2
        beyond the edge and spans the flange's own extent along the edge.
        Endpoints a -> b run in ascending order along the edge direction."""
        width, depth = self._require_base()
        out = []
        for flange in self._flanges:
            m = {"front": depth, "back": depth, "left": width, "right": width
                 }[flange.edge] / 2 + self.bend_allowance(flange) / 2
            lo, hi = self._effective_span(flange, flat=True)
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

    def _mitre_cuts(self, flange: _Flange) -> list:
        """For each `close` corner this flange reaches, the half-space beyond
        the 45 degree bisector — cut it away and the two leaves meet on it."""
        width, depth = self._require_base()
        big = 4 * (width + depth)
        out = []
        for corner, _end in self._corners_of(flange):
            if corner.treatment != "close":
                continue
            other = corner.edges[1] if corner.edges[0] == flange.edge else corner.edges[0]
            na, nb = _OUT_N[flange.edge], _OUT_N[other]
            nx, ny = nb[0] - na[0], nb[1] - na[1]
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
        points: list[tuple[float, float]] = []
        exact: list[dict] = []
        for edge in edges:
            kind = getattr(edge.geom_type, "name", str(edge.geom_type))
            a, b = edge @ 0, edge @ 1
            if kind == "LINE":
                exact.append({"kind": "line", "a": _xy(a), "b": _xy(b)})
                points.append(_xy(a))
                continue
            radius = float(getattr(edge, "radius", 0.0) or 0.0)
            centre = edge.arc_center
            exact.append({"kind": "arc", "a": _xy(a), "b": _xy(b),
                          "center": _xy(centre), "radius": round(radius, 9),
                          "ccw": _arc_is_ccw(edge)})
            n = _arc_samples(edge, radius, tolerance)
            for i in range(n - 1):
                points.append(_xy(edge @ (i / (n - 1))))
        if _signed_area(points) < 0:
            points.reverse()
            exact = [{**e, "a": e["b"], "b": e["a"],
                      **({"ccw": not e["ccw"]} if e["kind"] == "arc" else {})}
                     for e in reversed(exact)]
        start = min(range(len(points)),
                    key=lambda i: math.dist(points[i], (-width / 2, -depth / 2)))
        points = points[start:] + points[:start]
        estart = min(range(len(exact)),
                     key=lambda i: math.dist(exact[i]["a"],
                                             (-width / 2, -depth / 2)))
        exact = exact[estart:] + exact[:estart]
        out = _Outline(points, exact)
        self._outline_cache[tolerance] = out
        return out


# --------------------------------------------------------------- free helpers

def _xy(p) -> tuple[float, float]:
    return (round(p.X, 9), round(p.Y, 9))


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
