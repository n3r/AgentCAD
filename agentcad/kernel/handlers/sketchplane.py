"""Worker handler: the sketch plane of a face, and its boundary as references.

``face_info`` (``handlers/facemod.py``) already reports a face's normal and
centre — a plane, but **no basis**. Without a deterministic in-plane X axis
every sketch-on-face coordinate an emitter writes is arbitrary, so this pack
adds ``sketch_plane``, which returns build123d's own ``Plane(face)`` basis plus
the face's **own boundary edges** expressed in that basis.

Why the boundary and not a whole-part section: it is what a user means by
"sketch on this face", it is bounded, and it cannot produce the degenerate
near-tangential intersections a section can (design Decision 12).

**Measured before this was built** (the slice-12 spike, both the prototyping
enclosure's top face and the rocketry nozzle):

- ``Plane(face).x_dir`` is **bit-identical across rebuilds** of the same script
  in the same worker *and* across a fresh worker process — 0.0 difference in
  every component over 3 rebuilds, on both parts. It also survives a
  *parameter* change that does not alter the face's topology.
- It is **not** stable against arbitrary edits: it is derived from the face's
  underlying surface parametrization, so a change that re-cuts the face can
  turn it. That is the same class of instability as the face *index* itself,
  and it is surfaced the same way — the emitted script records the basis it was
  taken with, and the caveat says a topology change can move it.
- Edge inventory: the enclosure's top face returns 4 LINE edges; the nozzle's
  chosen planar face returns 1 CIRCLE edge; a fillet-adjacent face returns
  LINE + CIRCLE mixtures, and a lofted side returns ``BSPLINE``/``ELLIPSE``,
  which come back ``kind: "other"`` with a polyline approximation and are
  **not** constraint targets.

Face indices are ``toolkit.facemod.faces_in_mesh_order`` ordinals, the single
source of truth this codebase already uses for picking and push/pull.
"""

from __future__ import annotations

import math

import build123d as b3d

from ...toolkit.facemod import faces_in_mesh_order

# How many points a curve that is neither a line nor a circle is approximated
# with. It is a *display* aid: `kind: "other"` references are never constraint
# targets, and the docs say so rather than letting a user constrain to a
# polyline that is not the geometry.
POLYLINE_SAMPLES = 24

# Two plane coordinates closer than this are the same point, for the purpose of
# deciding whether an edge is degenerate. OCCT's own vertex tolerance is ~1e-7.
DEGENERATE_MM = 1e-7


def register(toolbox: dict):
    build_shape = toolbox["build_shape"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def sketch_plane(params: dict) -> dict:
        shape, _v, _w = build_shape(params["script"], params.get("params", {}))
        faces = faces_in_mesh_order(shape)
        index = int(params["face_index"])
        if not 0 <= index < len(faces):
            raise WorkerError(
                ERROR_CONTRACT,
                f"face_index {index} out of range (part has {len(faces)} faces)",
                {"n_faces": len(faces)},
            )
        face = faces[index]
        if face.geom_type != b3d.GeomType.PLANE:
            raise WorkerError(
                ERROR_CONTRACT,
                f"face {index} is a {face.geom_type.name} face, and a sketch "
                "needs a planar one; pick a flat face",
                {"geom_type": face.geom_type.name, "n_faces": len(faces)},
            )
        plane = b3d.Plane(face)
        refs, kinds = _references(face, plane)
        origin, x_dir, y_dir, normal = (plane.origin, plane.x_dir, plane.y_dir,
                                        plane.z_dir)
        return {
            "face_index": index,
            "n_faces": len(faces),
            "planar": True,
            "origin": [origin.X, origin.Y, origin.Z],
            "x_dir": [x_dir.X, x_dir.Y, x_dir.Z],
            "y_dir": [y_dir.X, y_dir.Y, y_dir.Z],
            "normal": [normal.X, normal.Y, normal.Z],
            "area_mm2": float(face.area),
            "refs": refs,
            # what came back, so a caller can say "3 of 5 edges are usable"
            # instead of silently offering fewer targets than the face has
            "ref_kinds": kinds,
        }

    return {"sketch_plane": sketch_plane}


def _to_plane(plane, pnt) -> tuple[float, float]:
    """A 3D point in the plane's own 2D coordinates."""
    local = plane.to_local_coords(pnt)
    return float(local.X), float(local.Y)


def _references(face, plane) -> tuple[list[dict], dict]:
    """The face's boundary edges, in plane coordinates.

    Lines and circles come back as themselves; everything else comes back
    `kind: "other"` with a polyline approximation, flagged unusable as a
    constraint target. A silent omission would leave a user wondering why the
    edge they can see is not there.
    """
    refs: list[dict] = []
    kinds: dict[str, int] = {}
    for i, edge in enumerate(face.edges()):
        gt = edge.geom_type.name
        kinds[gt] = kinds.get(gt, 0) + 1
        start = _to_plane(plane, edge @ 0)
        end = _to_plane(plane, edge @ 1)
        entry = {"name": f"ref{i}", "geom_type": gt,
                 "length_mm": float(edge.length)}
        if edge.geom_type == b3d.GeomType.LINE:
            entry.update({"kind": "line", "p1": list(start), "p2": list(end),
                          "constrainable": True})
        elif edge.geom_type == b3d.GeomType.CIRCLE:
            centre = _to_plane(plane, edge.arc_center)
            radius = float(edge.radius)
            closed = math.dist(start, end) <= DEGENERATE_MM
            entry.update({
                "kind": "circle" if closed else "arc",
                "center": list(centre), "r": radius, "constrainable": True,
            })
            if not closed:
                entry.update({
                    "start_deg": _angle(centre, start),
                    "end_deg": _sweep_end(centre, start, end, edge, plane),
                    "p1": list(start), "p2": list(end),
                })
        else:
            # Neither a line nor a circle: a polyline the GUI can *draw* and
            # nothing can be constrained to. Documented, not hidden.
            entry.update({
                "kind": "other", "constrainable": False,
                "points": [list(_to_plane(plane, edge @ (k / POLYLINE_SAMPLES)))
                           for k in range(POLYLINE_SAMPLES + 1)],
            })
        refs.append(entry)
    return refs, kinds


def _angle(centre, pnt) -> float:
    return math.degrees(math.atan2(pnt[1] - centre[1], pnt[0] - centre[0]))


def _sweep_end(centre, start, end, edge, plane) -> float:
    """The end angle carrying the full signed sweep (the solver's convention).

    The direction is read from the edge's own midpoint rather than assumed:
    an arc that goes the long way round is exactly the mistake the solver's
    "never wrap a parameter" rule exists to prevent.
    """
    a0, a1 = _angle(centre, start), _angle(centre, end)
    mid = _angle(centre, _to_plane(plane, edge @ 0.5))
    ccw_mid = (mid - a0) % 360.0
    ccw_end = (a1 - a0) % 360.0
    return a0 + (ccw_end if ccw_mid < ccw_end else ccw_end - 360.0)
