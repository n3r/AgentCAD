"""Worker-side tessellation: OCCT shape -> ACM1 buffer.

Faces are triangulated independently (vertices are not shared across faces),
which preserves hard edges without normal splitting. Within a B-rep face,
normals are per-vertex averages of adjacent triangle normals, giving smooth
curved surfaces.

A mesh face (an imported STL: one welded triangulation, no underlying
geometric surface) covers the WHOLE part, so averaging normals across it would
smooth over every crease and hole rim — the "melted" look with dark halos.
Such faces instead get crease-angle-limited normals: adjacent triangles are
averaged only when their normals are within CREASE_ANGLE, and vertices on a
sharp edge are split so each side keeps its own normal. This matches how other
CAD tools display meshes (smooth on curved regions, crisp at edges/holes).

Edges are discretized with tangential deflection for crisp outlines.

Imports OCP — only the kernel worker process may import this module.
"""

from __future__ import annotations

import math

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.TopAbs import TopAbs_EDGE, TopAbs_Orientation
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS

# Face iteration order is the contract picking/push-pull relies on; the ONE
# source of truth lives in the toolkit (agentcad.toolkit.facemod) so the
# triangle->face sidecar, face_info, and push_face can never disagree.
from ..toolkit.facemod import iter_ocp_faces
from . import acm

ANGULAR_DEFLECTION = 0.35  # radians, for both meshing and edge discretization
CREASE_ANGLE = math.radians(35.0)  # mesh faces: smooth within, split beyond


def _triangle_indices(tri) -> tuple[int, int, int]:
    try:
        return tri.Get()
    except TypeError:
        return (tri.Value(1), tri.Value(2), tri.Value(3))


def _smooth_face_normals(pts, idx):
    """B-rep face: per-vertex area-weighted average over the whole (smooth)
    face, keeping shared vertices. Returns (pts, normals, idx) unchanged in
    topology."""
    v0, v1, v2 = pts[idx[:, 0]], pts[idx[:, 1]], pts[idx[:, 2]]
    tri_n = np.cross(v1 - v0, v2 - v0)
    normals = np.zeros_like(pts)
    for corner in range(3):
        np.add.at(normals, idx[:, corner], tri_n)
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1.0
    normals /= lengths[:, None]
    return pts, normals, idx


def _crease_mesh_normals(pts, idx, crease_angle=CREASE_ANGLE):
    """Imported mesh face: crease-angle-limited normals with vertex splitting.

    Each triangle corner becomes its own output vertex; its normal is the
    area-weighted average of the triangles sharing that original vertex whose
    unit normal is within ``crease_angle`` of this triangle's normal. Curved
    regions stay smooth; hole rims and sharp edges stay crisp.
    """
    n_tris = len(idx)
    v0, v1, v2 = pts[idx[:, 0]], pts[idx[:, 1]], pts[idx[:, 2]]
    tri_area_n = np.cross(v1 - v0, v2 - v0)  # length == 2*area
    tri_len = np.linalg.norm(tri_area_n, axis=1)
    safe = np.where(tri_len == 0, 1.0, tri_len)
    tri_unit = tri_area_n / safe[:, None]
    cos_thresh = math.cos(crease_angle)

    # original vertex -> list of incident triangle indices
    incident: list[list[int]] = [[] for _ in range(len(pts))]
    for t in range(n_tris):
        a, b, c = idx[t]
        incident[a].append(t)
        incident[b].append(t)
        incident[c].append(t)
    incident_arr = [np.asarray(lst, dtype=np.int64) for lst in incident]

    out_pos = np.empty((n_tris * 3, 3), dtype=np.float64)
    out_nrm = np.empty((n_tris * 3, 3), dtype=np.float64)
    for t in range(n_tris):
        un = tri_unit[t]
        for corner in range(3):
            v = idx[t, corner]
            cand = incident_arr[v]
            dots = tri_unit[cand] @ un
            keep = cand[dots >= cos_thresh]
            acc = tri_area_n[keep].sum(axis=0)
            mag = np.linalg.norm(acc)
            out = acc / mag if mag > 1e-12 else un
            o = t * 3 + corner
            out_pos[o] = pts[v]
            out_nrm[o] = out
    out_idx = np.arange(n_tris * 3, dtype=np.int64).reshape(-1, 3)
    return out_pos, out_nrm, out_idx


def _tessellate_impl(ocp_shape, tolerance: float) -> tuple[bytes, bytes]:
    """Shared triangulation core: (ACM1 buffer, triangle->face-index buffer).

    The second buffer holds one little-endian u32 per triangle: the ordinal of
    the B-rep face the triangle belongs to, counted in the SAME
    ``TopExp_Explorer(FACE)`` order the tessellation loop below walks (see
    ``agentcad.toolkit.facemod`` — the source of truth for that order). Faces
    that yield no triangulation still consume an ordinal, so indices always
    line up with ``faces_in_mesh_order``.
    """
    BRepMesh_IncrementalMesh(ocp_shape, tolerance, False, ANGULAR_DEFLECTION, True)

    all_positions: list[np.ndarray] = []
    all_normals: list[np.ndarray] = []
    all_indices: list[np.ndarray] = []
    all_face_ids: list[np.ndarray] = []
    base = 0

    for face_ordinal, face in enumerate(iter_ocp_faces(ocp_shape)):
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

        # A face with no underlying geometric surface is an imported mesh (STL):
        # smooth-averaging across it would melt every crease, so use crease-angle
        # normals with vertex splitting. B-rep faces keep the smooth average.
        is_mesh_face = BRep_Tool.Surface_s(face) is None
        if is_mesh_face:
            f_pos, f_nrm, f_idx = _crease_mesh_normals(pts, idx)
        else:
            f_pos, f_nrm, f_idx = _smooth_face_normals(pts, idx)

        all_positions.append(f_pos)
        all_normals.append(f_nrm)
        all_indices.append(f_idx + base)
        all_face_ids.append(np.full(len(f_idx), face_ordinal, dtype="<u4"))
        base += len(f_pos)

    if base == 0:
        positions = np.zeros((0, 3))
        normals = np.zeros((0, 3))
        indices = np.zeros((0, 3), dtype=np.int64)
        face_ids = np.zeros(0, dtype="<u4")
    else:
        positions = np.vstack(all_positions)
        normals = np.vstack(all_normals)
        indices = np.vstack(all_indices)
        face_ids = np.concatenate(all_face_ids)

    edge_lengths, edge_points = _discretize_edges(ocp_shape, tolerance)
    buffer = acm.pack(positions, normals, indices, edge_lengths, edge_points)
    return buffer, np.ascontiguousarray(face_ids, dtype="<u4").tobytes()


def tessellate(ocp_shape, tolerance: float = 0.1) -> bytes:
    """Triangulate *ocp_shape* (a TopoDS_Shape) and return an ACM1 buffer."""
    buffer, _face_ids = _tessellate_impl(ocp_shape, tolerance)
    return buffer


def tessellate_with_faces(ocp_shape, tolerance: float = 0.1) -> tuple[bytes, bytes]:
    """Like :func:`tessellate` (byte-identical ACM1 buffer) plus a sidecar
    buffer of one little-endian u32 per triangle: the mesh-order B-rep face
    index that triangle belongs to."""
    return _tessellate_impl(ocp_shape, tolerance)


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
