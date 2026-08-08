"""Worker-side tessellation: OCCT shape -> ACM1 buffer.

Faces are triangulated independently (vertices are not shared across faces),
which preserves hard edges without normal splitting. Within a face, normals
are per-vertex averages of adjacent triangle normals, giving smooth curved
surfaces. Edges are discretized with tangential deflection for crisp outlines.

Imports OCP — only the kernel worker process may import this module.
"""

from __future__ import annotations

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_Orientation
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS

from . import acm

ANGULAR_DEFLECTION = 0.35  # radians, for both meshing and edge discretization


def _triangle_indices(tri) -> tuple[int, int, int]:
    try:
        return tri.Get()
    except TypeError:
        return (tri.Value(1), tri.Value(2), tri.Value(3))


def tessellate(ocp_shape, tolerance: float = 0.1) -> bytes:
    """Triangulate *ocp_shape* (a TopoDS_Shape) and return an ACM1 buffer."""
    BRepMesh_IncrementalMesh(ocp_shape, tolerance, False, ANGULAR_DEFLECTION, True)

    all_positions: list[np.ndarray] = []
    all_normals: list[np.ndarray] = []
    all_indices: list[np.ndarray] = []
    base = 0

    exp = TopExp_Explorer(ocp_shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        exp.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        identity = loc.IsIdentity()
        reversed_face = face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED

        n_nodes = tri.NbNodes()
        pts = np.empty((n_nodes, 3), dtype=np.float64)
        for i in range(1, n_nodes + 1):
            p = tri.Node(i)
            if not identity:
                p = p.Transformed(trsf)
            pts[i - 1] = (p.X(), p.Y(), p.Z())

        n_tris = tri.NbTriangles()
        idx = np.empty((n_tris, 3), dtype=np.int64)
        for i in range(1, n_tris + 1):
            a, b, c = _triangle_indices(tri.Triangle(i))
            idx[i - 1] = (a - 1, b - 1, c - 1)
        if reversed_face:
            idx = idx[:, ::-1]

        # Per-triangle geometric normals accumulated onto vertices.
        v0, v1, v2 = pts[idx[:, 0]], pts[idx[:, 1]], pts[idx[:, 2]]
        tri_n = np.cross(v1 - v0, v2 - v0)
        normals = np.zeros_like(pts)
        for corner in range(3):
            np.add.at(normals, idx[:, corner], tri_n)
        lengths = np.linalg.norm(normals, axis=1)
        lengths[lengths == 0] = 1.0
        normals /= lengths[:, None]

        # Drop degenerate triangles (zero-area slivers confuse nothing, keep all).
        all_positions.append(pts)
        all_normals.append(normals)
        all_indices.append(idx + base)
        base += n_nodes

    if base == 0:
        positions = np.zeros((0, 3))
        normals = np.zeros((0, 3))
        indices = np.zeros((0, 3), dtype=np.int64)
    else:
        positions = np.vstack(all_positions)
        normals = np.vstack(all_normals)
        indices = np.vstack(all_indices)

    edge_lengths, edge_points = _discretize_edges(ocp_shape, tolerance)
    return acm.pack(positions, normals, indices, edge_lengths, edge_points)


def _discretize_edges(ocp_shape, tolerance: float) -> tuple[np.ndarray, np.ndarray]:
    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(ocp_shape, TopAbs_EDGE, edge_map)

    lengths: list[int] = []
    points: list[tuple[float, float, float]] = []
    for i in range(1, edge_map.Extent() + 1):
        edge = TopoDS.Edge_s(edge_map.FindKey(i))
        if BRep_Tool.Degenerated_s(edge):
            continue
        try:
            curve = BRepAdaptor_Curve(edge)
            disc = GCPnts_TangentialDeflection(
                curve, ANGULAR_DEFLECTION, max(tolerance, 1e-4), 2
            )
        except Exception:
            continue
        n = disc.NbPoints()
        if n < 2:
            continue
        for j in range(1, n + 1):
            p = disc.Value(j)
            points.append((p.X(), p.Y(), p.Z()))
        lengths.append(n)

    return (
        np.asarray(lengths, dtype=np.int64),
        np.asarray(points, dtype=np.float64).reshape(-1, 3),
    )
