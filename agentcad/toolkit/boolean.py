"""Robust booleans with automatic fuzzy-tolerance escalation.

The classic OCCT failure is two faces that should touch but sit a sub-tolerance
gap apart (~1e-5 mm): the plain fuse silently leaves two disjoint solids, or a
cut/common produces an invalid shape. Retrying via BRepAlgoAPI with a fuzzy
value rescues these. Validated against build123d 0.11.1 / OCCT.
"""

from __future__ import annotations

from typing import Literal

from build123d import Compound, Shape, Solid

from .holes import carries_records


def _occt_bool(a: Shape, b: Shape, op: str, fuzzy: float):
    from OCP.BRepAlgoAPI import (
        BRepAlgoAPI_Common,
        BRepAlgoAPI_Cut,
        BRepAlgoAPI_Fuse,
    )
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopTools import TopTools_ListOfShape

    cls = {"fuse": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut,
           "common": BRepAlgoAPI_Common}[op]
    builder = cls()
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(a.wrapped)
    tools.Append(b.wrapped)
    builder.SetArguments(args)
    builder.SetTools(tools)
    if fuzzy > 0:
        builder.SetFuzzyValue(fuzzy)
    builder.SetRunParallel(True)
    builder.Build()
    if not builder.IsDone():
        return None
    shape = builder.Shape()
    if shape.ShapeType() == TopAbs_ShapeEnum.TopAbs_COMPOUND:
        return Compound(shape)
    return Solid(shape)


@carries_records  # the result keeps `a`'s hole records (PRD-010)
def safe_bool(a, b, op: Literal["fuse", "cut", "common"] = "fuse", *,
              fuzzy: float = 1e-4, clean: bool = True, expect_single: bool = True):
    """Boolean ``a <op> b`` with fuzzy-tolerance escalation. Returns
    ``(shape, warning|None)``. Tries the plain build123d operator, then raw
    BRepAlgoAPI at ``fuzzy`` and ``10*fuzzy`` when the plain op raises, yields
    an invalid/empty shape, or (fuse) leaves >1 disjoint solid."""

    def n_solids(shape) -> int:
        try:
            return len(shape.solids())
        except Exception:  # noqa: BLE001
            return 1

    def acceptable(out) -> bool:
        if out is None or not out.is_valid:
            return False
        if op != "common" and hasattr(out, "volume") and out.volume <= 1e-9:
            return False
        if op == "fuse" and expect_single and n_solids(out) > 1:
            return False
        return True

    def plain():
        try:
            out = {"fuse": a.fuse, "cut": a.cut, "common": a.intersect}[op](b)
            if clean and hasattr(out, "clean"):
                out = out.clean()
            return out if acceptable(out) else None
        except Exception:  # noqa: BLE001
            return None

    out = plain()
    if out is not None:
        return out, None

    for f in (fuzzy, fuzzy * 10):
        raw = _occt_bool(a, b, op, f)
        if raw is not None and acceptable(raw):
            if clean and hasattr(raw, "clean"):
                raw = raw.clean()
            return raw, f"safe_bool: plain {op} failed; succeeded with fuzzy tolerance {f}."

    raise RuntimeError(
        f"safe_bool: {op} failed even with fuzzy tolerance {fuzzy * 10}. Shapes "
        "may be non-manifold, self-intersecting, or genuinely disjoint."
    )
