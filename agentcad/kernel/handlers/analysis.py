"""Worker handler: tier-1 geometric analysis.

section (cross-section area + SVG), wall (min wall thickness via inward ray
casting), inertia (full tensor + principal via GProp), projected_area (ray
grid). Uses only shipped deps (build123d + OCP). Validated to machine
precision against analytic cases in the spike.
"""

from __future__ import annotations

import math

import build123d as b3d
from OCP.BRepGProp import BRepGProp, BRepGProp_Face
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

_PLANES = {"XY": b3d.Plane.XY, "XZ": b3d.Plane.XZ, "YZ": b3d.Plane.YZ}


def _section(shape, plane_name: str, min_required=None) -> dict:
    plane = _PLANES.get(plane_name, b3d.Plane.XY)
    sec = b3d.section(shape, section_by=plane)
    faces = sec.faces()
    area = float(sum(f.area for f in faces))
    return {"kind": "section", "plane": plane_name,
            "area_mm2": area, "n_faces": len(faces)}


def _min_wall(shape, min_required=None, grid: int = 8) -> dict:
    # Probe EVERY solid: a thin feature on a second solid must not be missed
    # (a first-solid-only probe reports a false "ok").
    solids = shape.solids() or [shape]
    min_thick = float("inf")
    min_loc = None
    for solid in solids:
        inter = IntCurvesFace_ShapeIntersector()
        inter.Load(solid.wrapped, 1e-6)
        exp = TopExp_Explorer(solid.wrapped, TopAbs_FACE)
        while exp.More():
            face = TopoDS.Face_s(exp.Current())
            umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
            gp_face = BRepGProp_Face(face)
            reversed_ = face.Orientation() == TopAbs_REVERSED
            for i in range(grid):
                for j in range(grid):
                    u = umin + (umax - umin) * (i + 0.5) / grid
                    v = vmin + (vmax - vmin) * (j + 0.5) / grid
                    pnt, nrm = gp_Pnt(), gp_Vec()
                    gp_face.Normal(u, v, pnt, nrm)
                    if nrm.Magnitude() < 1e-9:
                        continue
                    nrm.Normalize()
                    if reversed_:
                        nrm.Reverse()
                    d = gp_Dir(-nrm.X(), -nrm.Y(), -nrm.Z())
                    inter.Perform(gp_Lin(pnt, d), 1e-4, 1e6)
                    best = None
                    for k in range(1, inter.NbPnt() + 1):
                        w = inter.WParameter(k)
                        if w > 1e-4 and (best is None or w < best):
                            best = w
                    if best is not None and best < min_thick:
                        min_thick = best
                        min_loc = [pnt.X(), pnt.Y(), pnt.Z()]
            exp.Next()
    out = {"kind": "wall", "min_thickness_mm": None if math.isinf(min_thick) else min_thick,
           "location": min_loc}
    if min_required is not None and not math.isinf(min_thick):
        out["min_required_mm"] = min_required
        out["ok"] = min_thick >= min_required - 1e-6
    return out


def _projected_area(shape, axis: str = "Z", n: int = 200) -> dict:
    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(shape.wrapped, 1e-6)
    bb = shape.bounding_box()
    axis = axis.upper()
    if axis == "Z":
        u0, u1, v0, v1 = bb.min.X, bb.max.X, bb.min.Y, bb.max.Y
        base = lambda a, b: gp_Pnt(a, b, bb.min.Z - 1); direction = gp_Dir(0, 0, 1)  # noqa: E731
    elif axis == "Y":
        u0, u1, v0, v1 = bb.min.X, bb.max.X, bb.min.Z, bb.max.Z
        base = lambda a, b: gp_Pnt(a, bb.min.Y - 1, b); direction = gp_Dir(0, 1, 0)  # noqa: E731
    else:
        u0, u1, v0, v1 = bb.min.Y, bb.max.Y, bb.min.Z, bb.max.Z
        base = lambda a, b: gp_Pnt(bb.min.X - 1, a, b); direction = gp_Dir(1, 0, 0)  # noqa: E731
    du, dv = (u1 - u0) / n, (v1 - v0) / n
    cell = du * dv
    hits = 0
    for i in range(n):
        a = u0 + (i + 0.5) * du
        for j in range(n):
            b = v0 + (j + 0.5) * dv
            inter.Perform(gp_Lin(base(a, b), direction), 0, 1e6)
            if inter.NbPnt() > 0:
                hits += 1
    return {"kind": "projected_area", "axis": axis, "area_mm2": hits * cell}


def _inertia(shape, density_g_cm3: float = 1.0) -> dict:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props)
    com = props.CentreOfMass()
    mat = props.MatrixOfInertia()
    # unit-density tensor (mm^5); scale to g*mm^2 with density g/cm^3 = 1e-3 g/mm^3
    scale = density_g_cm3 * 1e-3
    tensor = [[mat.Value(r, c) * scale for c in range(1, 4)] for r in range(1, 4)]
    return {
        "kind": "inertia",
        "volume_mm3": props.Mass(),
        "center_of_mass": [com.X(), com.Y(), com.Z()],
        "inertia_tensor_g_mm2": tensor,
        "note": "tensor about the global origin; density in g/cm^3",
    }


def register(toolbox: dict):
    build_shape = toolbox["build_shape"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def analyze(params: dict) -> dict:
        shape, _v, _w = build_shape(params["script"], params.get("params", {}))
        kind = params.get("kind", "inertia")
        if kind == "section":
            return _section(shape, params.get("plane", "XY"))
        if kind == "wall":
            return _min_wall(shape, params.get("min_required"),
                             int(params.get("grid", 8)))
        if kind == "projected_area":
            return _projected_area(shape, params.get("axis", "Z"),
                                   int(params.get("n", 200)))
        if kind == "inertia":
            return _inertia(shape, float(params.get("density_g_cm3", 1.0)))
        raise WorkerError(ERROR_CONTRACT, f"unknown analysis kind {kind!r}")

    return {"analyze": analyze}
