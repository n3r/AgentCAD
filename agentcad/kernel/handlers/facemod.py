"""Worker handler: B-rep face identification for GUI picking and push/pull.

``face_info`` reports one face of a built script part, addressed by its
mesh-order index (the ``TopExp_Explorer(FACE)`` order tessellation uses — see
``agentcad.toolkit.facemod``, the single source of truth for that order).

Only inspection lives here: the push/pull edit itself happens through script
rewriting (``agentcad.core.tools_facemod``), so geometry stays
script-as-source-of-truth.
"""

from __future__ import annotations

import build123d as b3d

from ...toolkit.facemod import faces_in_mesh_order


def register(toolbox: dict):
    build_shape = toolbox["build_shape"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def face_info(params: dict) -> dict:
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
        center = face.center()
        normal = face.normal_at()
        return {
            "planar": face.geom_type == b3d.GeomType.PLANE,
            "normal": [normal.X, normal.Y, normal.Z],
            "area_mm2": float(face.area),
            "center": [center.X, center.Y, center.Z],
            "n_faces": len(faces),
        }

    return {"face_info": face_info}
