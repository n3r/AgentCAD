"""Worker handler pack for reference (imported) CAD parts.

STEP/BREP load as real B-reps (exact metrics, boolean-capable). STL loads as a
triangulation-only Face: it is tessellated and measured (volume from the mesh)
but flagged mesh-only, since booleans on a mesh Face segfault OCCT.
"""

from __future__ import annotations

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

from ..refload import load_reference


def _stl_mesh_volume(shape) -> float:
    """Signed volume of an STL triangulation via the divergence theorem."""
    face = TopoDS.Face_s(shape.wrapped)
    tri = BRep_Tool.Triangulation_s(face, TopLoc_Location())
    if tri is None:
        return 0.0
    pts = np.array([[tri.Node(i).X(), tri.Node(i).Y(), tri.Node(i).Z()]
                    for i in range(1, tri.NbNodes() + 1)])
    idx = []
    for i in range(1, tri.NbTriangles() + 1):
        t = tri.Triangle(i)
        try:
            a, b, c = t.Get()
        except TypeError:
            a, b, c = t.Value(1), t.Value(2), t.Value(3)
        idx.append((a - 1, b - 1, c - 1))
    idx = np.array(idx)
    v0, v1, v2 = pts[idx[:, 0]], pts[idx[:, 1]], pts[idx[:, 2]]
    return float(np.abs(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum()) / 6.0)


def register(toolbox: dict) -> dict:
    metrics = toolbox["metrics"]
    tessellate = toolbox["tessellate"]
    atomic_write = toolbox["atomic_write"]
    write_lod_tiers = toolbox["write_lod_tiers"]

    def build_reference(params: dict) -> dict:
        source = params["source_path"]
        density = float(params.get("density_g_cm3", 1.0))
        tolerance = float(params.get("tolerance", 0.1))
        shape, kind = load_reference(source)
        warnings: list[str] = []

        if kind == "mesh":
            volume = _stl_mesh_volume(shape)
            bb = shape.bounding_box()
            m = {
                "volume_mm3": volume,
                "area_mm2": 0.0,
                "mass_g": volume * density / 1000.0,
                "bbox": {"min": [bb.min.X, bb.min.Y, bb.min.Z],
                         "max": [bb.max.X, bb.max.Y, bb.max.Z]},
                "center_of_mass": [bb.center().X, bb.center().Y, bb.center().Z],
                "is_valid": False,
                "n_faces": 1,
                "n_edges": 0,
                "n_solids": 0,
                "mesh": True,
            }
            warnings.append(
                "STL reference: mesh-only. Volume is estimated from the "
                "triangulation and this body cannot take part in booleans."
            )
        else:
            m = metrics(shape, density)
            m["mesh"] = False

        buffer = tessellate(shape.wrapped, tolerance)
        atomic_write(params["mesh_path"], buffer)
        # An STL mesh face's triangulation IS its geometry — re-tessellating
        # cannot coarsen it (and cleaning it would destroy the part), so LOD
        # tiers are only produced for B-rep imports (STEP/BREP).
        lod_params = params if kind != "mesh" else {**params, "lod_tolerances": None}
        triangles, lods = write_lod_tiers(shape.wrapped, lod_params, buffer)
        return {
            "metrics": m,
            "warnings": warnings,
            "kind": kind,
            "triangles": triangles,
            "lods": lods,
        }

    return {"build_reference": build_reference}
