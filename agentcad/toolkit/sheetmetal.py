"""Declarative sheet metal: one spec yields the folded solid AND the flat pattern.

A ``SheetPart`` is a rectangular base plate plus full-edge flanges. From that
single declaration you get ``fold()`` (the bent solid for modeling/assembly),
``unfold()`` (the flat blank as a solid), ``flat_outline()`` (the blank's 2D
polygon) and ``bend_lines()`` (where to bend, in flat coordinates) — so the
part on screen and the pattern sent to the laser/brake can never disagree.

Conventions (mm / degrees):
  * ``base(width, depth)``: footprint centered on the origin, width along X,
    depth along Y, thickness extruded +Z (plate occupies z in [0, t]).
  * Edges: "left" x=-width/2, "right" x=+width/2, "front" y=-depth/2,
    "back" y=+depth/2. One flange per edge; flanges bend UP (+Z) and span the
    full edge. ``angle_deg`` is the bend angle, exclusive (0, 180); 90 is the
    common case. ``inner_radius`` defaults to the sheet thickness.
  * Bend allowance BA = radians(angle) * (inner_radius + k_factor * thickness).
    Each flange adds BA + length of flat stock beyond its edge; k_factor 0.44
    suits air-bent mild steel / aluminum — tune it per process.

Guidance for agents: in a part script build the SheetPart from p inside a
helper, return ``sp.fold()`` from build(p), and add the optional contract
function ``flat_pattern(p)`` returning ``(sp.unfold(), sp.bend_lines())`` to
enable the ``flat_pattern`` export tool (SVG/DXF with a BEND layer).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from build123d import (
    Align,
    Axis,
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    CenterArc,
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


@dataclass(frozen=True)
class _Flange:
    edge: str
    angle_deg: float
    length: float
    inner_radius: float


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
        self._flanges: dict[str, _Flange] = {}

    # ------------------------------------------------------------- declaration

    def base(self, width: float, depth: float) -> "SheetPart":
        """Rectangular base plate: width along X, depth along Y, z in [0, t]."""
        if width <= 0 or depth <= 0:
            raise ValueError("base width and depth must be > 0")
        if self._base is not None:
            raise ValueError("base() may only be called once")
        self._base = (float(width), float(depth))
        return self

    def flange(self, edge: str, angle_deg: float, length: float,
               inner_radius: float | None = None) -> "SheetPart":
        """Full-edge flange bending UP (+Z). One per edge; length is the flat
        leaf beyond the bend zone; inner_radius defaults to the thickness."""
        if self._base is None:
            raise ValueError("call base() before flange()")
        if edge not in _EDGES:
            raise ValueError(f"edge must be one of {_EDGES}, got {edge!r}")
        if edge in self._flanges:
            raise ValueError(f"duplicate flange on edge {edge!r}")
        if not 0.0 < angle_deg < 180.0:
            raise ValueError("angle_deg must be in (0, 180) exclusive")
        if length <= 0:
            raise ValueError("flange length must be > 0")
        radius = self.thickness if inner_radius is None else float(inner_radius)
        if radius <= 0:
            raise ValueError("inner_radius must be > 0")
        self._flanges[edge] = _Flange(edge, float(angle_deg), float(length), radius)
        return self

    # ---------------------------------------------------------------- geometry

    def bend_allowance(self, flange: _Flange) -> float:
        """BA = radians(angle) * (inner_radius + k_factor * thickness)."""
        return math.radians(flange.angle_deg) * (
            flange.inner_radius + self.k_factor * self.thickness)

    def fold(self) -> Part:
        """The folded solid: base plate + per-flange bend sector and leaf,
        fused into a single valid solid. Exact volume:
        w*d*t + sum(angle_rad*t*(R + t/2)*edge_w + length*t*edge_w)."""
        width, depth = self._require_base()
        part = self._base_plate()
        for flange in self._flanges.values():
            part = self._fuse(part, self._flange_solid(flange))
        return part

    def unfold(self) -> Part:
        """The flat pattern as a solid in the base plane: base plate plus a
        (BA + length) x edge-width tab per flange, thickness unchanged."""
        width, depth = self._require_base()
        t = self.thickness
        part = self._base_plate()
        for flange in self._flanges.values():
            ext = self.bend_allowance(flange) + flange.length
            if flange.edge in ("front", "back"):
                size, sign = (width, ext, t), (0, 1)
            else:
                size, sign = (ext, depth, t), (1, 0)
            cx = {"left": -(width + ext) / 2, "right": (width + ext) / 2}.get(flange.edge, 0.0)
            cy = {"front": -(depth + ext) / 2, "back": (depth + ext) / 2}.get(flange.edge, 0.0)
            tab = Box(*size, align=(Align.CENTER, Align.CENTER, Align.MIN)
                      ).translate((cx, cy, 0))
            part = self._fuse(part, tab)
        return part

    def flat_outline(self) -> list[tuple[float, float]]:
        """The flat pattern's 2D outline polygon (XY, mm), counter-clockwise
        starting at the (-width/2, -depth/2) base corner. Base corners are
        always vertices (adjacent tabs meet there as reflex corners)."""
        width, depth = self._require_base()
        x0, x1, y0, y1 = -width / 2, width / 2, -depth / 2, depth / 2
        ext = {edge: (self.bend_allowance(f) + f.length
                      if (f := self._flanges.get(edge)) else 0.0)
               for edge in _EDGES}
        pts: list[tuple[float, float]] = []
        # walk each base edge CCW; a flanged edge bulges outward by its extent
        for a, b, e, (nx, ny) in (
            ((x0, y0), (x1, y0), ext["front"], (0, -1)),
            ((x1, y0), (x1, y1), ext["right"], (1, 0)),
            ((x1, y1), (x0, y1), ext["back"], (0, 1)),
            ((x0, y1), (x0, y0), ext["left"], (-1, 0)),
        ):
            pts.append(a)
            if e > 0:
                pts.append((a[0] + nx * e, a[1] + ny * e))
                pts.append((b[0] + nx * e, b[1] + ny * e))
        return pts

    def bend_lines(self) -> list[dict]:
        """Bend midlines in FLAT coordinates: the bend zone spans [edge,
        edge + BA] outward from the base edge, so each midline sits BA/2
        beyond the edge and spans the full edge width. Endpoints a -> b run
        in ascending order along the edge direction."""
        width, depth = self._require_base()
        out = []
        for flange in self._flanges.values():
            m = {"front": depth, "back": depth, "left": width, "right": width
                 }[flange.edge] / 2 + self.bend_allowance(flange) / 2
            a, b = {
                "front": ((-width / 2, -m), (width / 2, -m)),
                "back": ((-width / 2, m), (width / 2, m)),
                "left": ((-m, -depth / 2), (-m, depth / 2)),
                "right": ((m, -depth / 2), (m, depth / 2)),
            }[flange.edge]
            out.append({"edge": flange.edge, "a": a, "b": b,
                        "angle_deg": flange.angle_deg,
                        "inner_radius": flange.inner_radius})
        return out

    # ----------------------------------------------------------------- helpers

    def _require_base(self) -> tuple[float, float]:
        if self._base is None:
            raise ValueError("call base() first")
        return self._base

    def _base_plate(self):
        width, depth = self._require_base()
        return Box(width, depth, self.thickness,
                   align=(Align.CENTER, Align.CENTER, Align.MIN))

    def _fuse(self, a, b):
        out, warning = safe_bool(a, b, "fuse")
        if warning:
            self.warnings.append(warning)
        return out

    def _flange_solid(self, flange: _Flange):
        """Bend sector + leaf, built as a 2D cross-section in the plane
        perpendicular to the edge and extruded along the full edge width.
        Canonical frame is the front edge (profile in YZ, extruded along X);
        other edges rotate the result about Z."""
        t, radius = self.thickness, flange.inner_radius
        angle = math.radians(flange.angle_deg)
        width, depth = self._require_base()
        dist, edge_w = ((depth / 2, width) if flange.edge in ("front", "back")
                        else (width / 2, depth))
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
            extrude(amount=edge_w / 2, both=True)
        solid = bp.part
        rot = _ROT_Z[flange.edge]
        return solid.rotate(Axis.Z, rot) if rot else solid
