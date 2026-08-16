"""Robust shell: offset() with graduated fallbacks.

The boolean-subtract fallback is APPROXIMATE — wall thickness is not uniform on
curved/slanted faces (measured up to ~20% thin on dome mid-sections in the
validation spike). The returned warning states this plainly; callers must
surface it. Validated against build123d 0.11.1 / OCCT.
"""

from __future__ import annotations

from build123d import Compound, Face, Kind, offset, scale

from .holes import carries_records


def _inner_subtract_shell(part, thickness: float, opening_faces: list[Face]):
    bb = part.bounding_box()
    size = bb.size
    if min(size.X, size.Y, size.Z) <= 2 * thickness:
        return None
    fx = (size.X - 2 * thickness) / size.X
    fy = (size.Y - 2 * thickness) / size.Y
    fz = (size.Z - 2 * thickness) / size.Z
    center = bb.center()
    inner = scale(part, by=(fx, fy, fz))
    inner = inner.translate(center - inner.bounding_box().center())
    for f in opening_faces:
        n = f.normal_at(f.center())
        inner = inner.fuse(inner.translate(n * (2 * thickness)))
    out = part.cut(inner)
    if isinstance(out, Compound):
        solids = out.solids()
        if not solids:
            return None
        out = max(solids, key=lambda s: s.volume)
    return out if out.is_valid and out.volume > 0 else None


@carries_records  # a shelled part keeps its hole records (PRD-010)
def safe_shell(part, thickness: float, opening_faces: list[Face] | None = None,
               *, kind: Kind = Kind.ARC):
    """Shell ``part`` to ``thickness``, opening ``opening_faces``. Returns
    ``(part, warning|None)``. Falls back through Kind.INTERSECTION, fewer
    opened faces, and finally an approximate boolean-subtract shell."""
    opening_faces = opening_faces or []

    def try_offset(faces, k):
        try:
            out = offset(part, amount=-thickness, openings=faces or None, kind=k)
            if isinstance(out, Compound):
                solids = out.solids()
                out = max(solids, key=lambda s: s.volume) if solids else None
            if out is not None and out.is_valid and 0 < out.volume < part.volume:
                return out
        except Exception:  # noqa: BLE001 — offset throws on tight curvature
            pass
        return None

    out = try_offset(opening_faces, kind)
    if out is not None:
        return out, None

    out = try_offset(opening_faces, Kind.INTERSECTION)
    if out is not None:
        return out, "safe_shell: offset() needed Kind.INTERSECTION (sharp corners)."

    if len(opening_faces) > 1:
        biggest = max(opening_faces, key=lambda f: f.area)
        out = try_offset([biggest], kind) or try_offset([biggest], Kind.INTERSECTION)
        if out is not None:
            return out, (
                f"safe_shell: could only open 1 of {len(opening_faces)} requested "
                "faces (largest); others left closed."
            )

    out = _inner_subtract_shell(part, thickness, opening_faces)
    if out is not None:
        return out, (
            "safe_shell: offset() failed; used approximate boolean-subtract "
            "fallback. Wall thickness is only approximate on curved/slanted "
            "faces (can be ~20% thin on dome mid-sections)."
        )

    raise RuntimeError(
        f"safe_shell: all strategies failed for thickness {thickness}. "
        "Try a thicker wall, simpler geometry, or shell before filleting."
    )
