"""Class-A surfacing: continuity-controlled freeform helpers.

``smooth_loft`` lofts a single solid through 2+ planar profiles, falling back
to a ruled loft when the smooth (spline) interpolation fails. ``blend_surface``
builds a transition surface between the nearest edges of two faces using
OCCT's plate-filling algorithm (``BRepOffsetAPI_MakeFilling``) with G0/G1/G2
boundary continuity against the source faces — the class-A move that plain
lofting cannot do. House style: ``(result, warning | None)`` tuples with
honest warnings; RuntimeError when every strategy fails. Validated against
build123d 0.11.1 / OCCT 7.x.

Guidance for agents:
  * Aesthetic/aero bodies: loft the main body with ``smooth_loft``, then close
    gaps between adjacent faces with ``blend_surface(..., continuity="G1")``
    (tangent — no visible crease). Use ``"G2"`` for reflective class-A skins
    (curvature-continuous highlights); it degrades to G1 with a warning when
    OCCT cannot satisfy the constraint.
  * Verify the result with ``analyze_part(kind="curvature")`` — a G2 blend
    should show no jump in mean curvature across the seam.
"""

from __future__ import annotations

from build123d import Face, Part, Wire, loft
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeFilling
from OCP.GeomAbs import GeomAbs_Shape
from OCP.gp import gp_Pnt
from OCP.TopoDS import TopoDS

# GeomAbs_Shape values handed to BRepOffsetAPI_MakeFilling per continuity.
# LOAD-BEARING QUIRK (OCCT 7.x): BRepFill_Filling::AddConstraints passes the
# GeomAbs_Shape enum value straight through as BRepFill_CurveConstraint's
# integer Tang order (valid 0..2). GeomAbs_G2 has enum value 3, so a literal
# G2 request ALWAYS throws "BRepFill : The continuity is not G0 G1 or G2" —
# while GeomAbs_C1 (enum value 2) lands as Tang=2, i.e. a true
# curvature-continuity constraint. Verified against OCCT V7_7_2 sources
# (BRepFill_Filling.cxx / BRepFill_CurveConstraint.cxx).
_CONTINUITY_ORDERS = {
    "G0": GeomAbs_Shape.GeomAbs_C0,
    "G1": GeomAbs_Shape.GeomAbs_G1,
    "G2": GeomAbs_Shape.GeomAbs_C1,  # Tang=2 = curvature (see note above)
}
# The plate algorithm with curvature constraints is numerically fragile: on
# non-coplanar supports the surface can "balloon" (measured 3x-338x the G1
# area on offset/tilted plates) while still reporting IsDone. Accept a G2
# result only when its area stays comparable to the stable G1 reference.
_G2_BLOWUP_RATIO = 1.5


def smooth_loft(profiles, *, ruled: bool = False) -> tuple[Part, str | None]:
    """Loft a single solid through 2+ planar profiles (Sketch/Face/Wire).

    Tries a smooth (spline-interpolated) loft first; on failure retries with
    ``ruled=True`` (straight-line sections) and says so in the warning.
    Returns ``(part, warning | None)``; raises RuntimeError when both fail.
    """
    sections = list(profiles)
    if len(sections) < 2:
        raise ValueError(
            f"smooth_loft: need at least 2 profiles, got {len(sections)}")
    sections = [Face(s) if isinstance(s, Wire) else s for s in sections]

    def try_loft(use_ruled: bool):
        try:
            out = loft(sections, ruled=use_ruled)
        except Exception:  # noqa: BLE001 — OCCT throws on twisted/misaligned sections
            return None
        if out is None:
            return None
        solids = out.solids()
        if len(solids) == 1 and out.is_valid and out.volume > 0:
            return out
        return None

    out = try_loft(ruled)
    if out is not None:
        return out, None
    if not ruled:
        out = try_loft(True)
        if out is not None:
            return out, (
                "smooth_loft: smooth (spline) loft failed; fell back to a "
                "ruled loft — sections are joined by straight lines, not a "
                "smooth blend."
            )
    raise RuntimeError(
        "smooth_loft: loft failed (smooth and ruled) for these profiles. "
        "Check that profiles are planar, closed, similarly oriented, and "
        "do not self-intersect."
    )


def blend_surface(face_a, face_b, *, continuity: str = "G1") -> tuple[Face, str | None]:
    """A transition surface between the nearest edges of two faces.

    ``continuity`` is the boundary condition against each source face:
    ``"G0"`` positional, ``"G1"`` tangent, ``"G2"`` tangent + curvature.
    Built with ``BRepOffsetAPI_MakeFilling`` (plate algorithm): each nearest
    edge is a bound constraint supported by its face; open edge endpoints are
    joined by straight G0 rails to close the boundary.

    Returns ``(face, warning | None)``. G2 results are additionally gated
    against the G1 reference surface — when the curvature-constrained plate
    balloons (a known OCCT 7.x instability) the blend degrades to G1 and the
    warning says so. Failed levels degrade down the G2 → G1 → G0 ladder;
    raises RuntimeError when even G0 fails.
    """
    requested = str(continuity).upper()
    if requested not in _CONTINUITY_ORDERS:
        raise ValueError(
            f"blend_surface: continuity must be one of "
            f"{sorted(_CONTINUITY_ORDERS)}, got {continuity!r}")

    edge_a = _nearest_edge(face_a, face_b)
    edge_b = _nearest_edge(face_b, face_a)
    rails = _rail_edges(edge_a, edge_b)

    def try_filling(name: str):
        order = _CONTINUITY_ORDERS[name]
        fill = BRepOffsetAPI_MakeFilling()
        try:
            if name == "G0":
                fill.Add(edge_a.wrapped, order, True)
                fill.Add(edge_b.wrapped, order, True)
            else:
                fill.Add(edge_a.wrapped, face_a.wrapped, order, True)
                fill.Add(edge_b.wrapped, face_b.wrapped, order, True)
            for rail in rails:
                fill.Add(rail, GeomAbs_Shape.GeomAbs_C0, True)
            fill.Build()
            if not fill.IsDone():
                return None
            out = Face(TopoDS.Face_s(fill.Shape()))
        except Exception:  # noqa: BLE001 — MakeFilling throws Standard_Failure
            return None
        return out if out.is_valid and out.area > 0 else None

    warning = None
    if requested == "G2":
        g2 = try_filling("G2")
        g1 = try_filling("G1")
        # no G1 reference to gate against (rare: G1 failed where G2 built) —
        # accept the G2 result ungated rather than fail
        if g2 is not None and (g1 is None
                               or g2.area <= _G2_BLOWUP_RATIO * g1.area):
            return g2, None
        if g2 is not None:
            warning = (
                "blend_surface: G2 (curvature) filling was numerically "
                "unstable on this geometry (plate surface ballooned); "
                "degraded to G1 (tangency only)."
            )
        else:
            warning = (
                "blend_surface: G2 filling failed on this geometry; "
                "degraded to G1 (tangency only)."
            )
        if g1 is not None:
            return g1, warning
        warning = warning.rstrip(".") + ", which also failed; "
    elif requested == "G1":
        g1 = try_filling("G1")
        if g1 is not None:
            return g1, None
        warning = "blend_surface: G1 filling failed on this geometry; "

    if requested != "G0" and warning is not None:
        g0 = try_filling("G0")
        if g0 is not None:
            return g0, warning + "degraded to G0 (positional only, no tangency)."
    else:
        g0 = try_filling("G0")
        if g0 is not None:
            return g0, None
    raise RuntimeError(
        "blend_surface: plate filling failed at every continuity level. "
        "Check that the faces have open edges roughly facing each other "
        "and are not intersecting."
    )


def _nearest_edge(face, other_face):
    """The edge of ``face`` that faces ``other_face`` overall: minimal mean
    distance sampled along the edge. (Plain min-distance ties on rectangles —
    side edges touch the facing edge's corners at the same distance.)"""
    ts = (0.1, 0.3, 0.5, 0.7, 0.9)
    return min(
        face.edges(),
        key=lambda e: sum(other_face.distance_to(e.position_at(t)) for t in ts),
    )


def _rail_edges(edge_a, edge_b) -> list:
    """Straight edges joining matched endpoints of two open edges, closing the
    filling boundary. Closed edges (e.g. full circles) need no rails."""
    if edge_a.is_closed or edge_b.is_closed:
        return []
    a0, a1 = edge_a.position_at(0), edge_a.position_at(1)
    b0, b1 = edge_b.position_at(0), edge_b.position_at(1)
    # pair endpoints so the boundary does not twist
    if (a0 - b0).length + (a1 - b1).length > (a0 - b1).length + (a1 - b0).length:
        b0, b1 = b1, b0
    return [
        BRepBuilderAPI_MakeEdge(
            gp_Pnt(p.X, p.Y, p.Z), gp_Pnt(q.X, q.Y, q.Z)).Edge()
        for p, q in ((a0, b0), (a1, b1))
    ]
