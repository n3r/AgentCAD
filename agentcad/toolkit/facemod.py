"""Direct face modeling: mesh-order face indexing and push/pull.

This module is the ONE source of truth for face indexing: a "mesh-order face
index" is the position of a B-rep face in the plain ``TopExp_Explorer(FACE)``
walk of the shape — exactly the order ``agentcad.kernel.mesh`` tessellates
faces in (it imports :func:`iter_ocp_faces` from here), so the triangle→face
sidecar written next to each mesh, the ``face_info`` kernel handler, and
:func:`push_face` all agree on which face "index 3" is.

Kernel-side module: imports build123d/OCP at top, so it may only be imported
by part scripts and the kernel worker process (like the rest of the toolkit's
geometry helpers), never by the server process.
"""

from __future__ import annotations

import sys

import build123d as b3d
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from .boolean import safe_bool


def iter_ocp_faces(ocp_shape):
    """Yield each ``TopoDS_Face`` of *ocp_shape* in mesh (explorer) order."""
    exp = TopExp_Explorer(ocp_shape, TopAbs_FACE)
    while exp.More():
        yield TopoDS.Face_s(exp.Current())
        exp.Next()


def faces_in_mesh_order(shape) -> list[b3d.Face]:
    """The shape's B-rep faces in the same order tessellation emits them.

    *shape* may be a build123d shape (``.wrapped`` is used) or a raw
    ``TopoDS_Shape``. Index i of the returned list is mesh-order face index i.
    """
    ocp = shape.wrapped if hasattr(shape, "wrapped") else shape
    return [b3d.Face(f) for f in iter_ocp_faces(ocp)]


def push_face(part, face_index: int, distance: float):
    """Push/pull one planar face of *part* by *distance* millimetres.

    The face (by mesh-order index) is extruded along its outward normal by
    ``abs(distance)``; the resulting prism is fused into the part when
    ``distance > 0`` (pull material out) or cut from it when ``distance < 0``
    (push material in). Raises ``ValueError`` for a non-planar face or a bad
    index/distance, and ``RuntimeError`` when the boolean cannot produce a
    single valid solid. Returns the new shape.
    """
    faces = faces_in_mesh_order(part)
    face_index = int(face_index)
    if not 0 <= face_index < len(faces):
        raise ValueError(
            f"face_index {face_index} out of range (part has {len(faces)} faces)"
        )
    distance = float(distance)
    if distance == 0:
        raise ValueError("distance must be nonzero")
    face = faces[face_index]
    if face.geom_type != b3d.GeomType.PLANE:
        raise ValueError(
            f"face {face_index} is not planar "
            f"(geom_type {face.geom_type.name}); push/pull needs a planar face"
        )

    normal = face.normal_at()
    # A pull extrudes outward and fuses; a push extrudes INTO the material and
    # cuts (extruding outward and cutting would remove nothing).
    direction = normal if distance > 0 else -normal
    tool = b3d.extrude(face, amount=abs(distance), dir=direction)
    op = "fuse" if distance > 0 else "cut"
    out, warning = safe_bool(part, tool, op)
    if warning:
        print(warning, file=sys.stderr)
    solids = out.solids()
    if len(solids) != 1:
        raise RuntimeError(
            f"push_face: {op} of face {face_index} by {distance} left "
            f"{len(solids)} solids (expected a single solid)"
        )
    if not out.is_valid:
        raise RuntimeError(
            f"push_face: {op} of face {face_index} by {distance} produced an "
            "invalid solid"
        )
    return out
