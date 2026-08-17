"""Tool pack: direct face modeling (push/pull) on script parts.

Geometry stays script-as-source-of-truth: ``push_pull`` validates the picked
face via the kernel's ``face_info`` handler, then APPENDS an auto-generated
block to the part script that wraps the existing ``build`` in a
``push_face`` call, and persists it through ``service.update_part`` — so the
rebuild, validation, event stream, and history snapshot all ride the normal
path, and the edit is composable (a second push/pull appends a second block)
and visible/removable in the script.

Face indexing is "mesh order" — the order ``agentcad.toolkit.facemod``
defines and the ``.faces.u32`` mesh sidecar exposes to the GUI.
"""

from __future__ import annotations

from .model import ValidationError
from .script_blocks import apply_generated_block, next_build_alias
from .tools import Tool, schema, with_hint

PUSH_PULL_MARKER = (
    "# --- agentcad push/pull (auto-generated; edit or remove freely) ---"
)

# `alias` is allocated by `script_blocks.next_build_alias` against the names
# ALREADY IN THE SCRIPT — not off this marker's count — so a chained push/pull
# never shadows its own saved previous build, and never collides with a block
# another pack (`add_holes`) appended or with the numbering left behind when a
# middle block is deleted. A collision here is not a shadow: the alias resolves
# as a global at call time, so the loser calls itself (`RecursionError`).
_PUSH_PULL_BLOCK = """

{marker}
from agentcad.toolkit.facemod import push_face as _agentcad_push_face
{alias} = build
def build(p):
    return _agentcad_push_face({alias}(p), {face_index}, {distance})
"""


def register(registry, service) -> None:
    def _script_part(project: str, part_id: str):
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError(
                "push/pull works on script parts only (imported references "
                "have no editable script)"
            )
        return record, service.store.read_script(project, part_id)

    def _face_info(script: str, params: dict, face_index: int) -> dict:
        return service.kernel.request(
            "face_info",
            {"script": script, "params": params, "face_index": face_index},
            timeout_s=300.0,  # may rebuild the shape from scratch
        )

    def face_info(project: str, part_id: str, face_index: int) -> dict:
        record, script = _script_part(project, part_id)
        info = _face_info(script, record.effective_params, int(face_index))
        return {"face_index": int(face_index), **info}

    def push_pull(
        project: str, part_id: str, face_index: int, distance_mm: float
    ) -> dict:
        record, script = _script_part(project, part_id)
        face_index = int(face_index)
        distance = float(distance_mm)
        if distance == 0:
            raise ValidationError("distance_mm must be nonzero")
        info = _face_info(script, record.effective_params, face_index)
        if not info["planar"]:
            raise ValidationError(
                f"face {face_index} is not planar — push/pull needs a planar "
                "face",
                info,
            )
        new_script = script.rstrip("\n") + _PUSH_PULL_BLOCK.format(
            marker=PUSH_PULL_MARKER,
            alias=next_build_alias(script),
            face_index=face_index,
            distance=repr(distance),
        )
        result = apply_generated_block(
            service, project, part_id, script, new_script)
        return {
            **with_hint(result),
            "face_index": face_index,
            "distance_mm": distance,
        }

    registry.register(Tool(
        "face_info",
        "Inspect one B-rep face of a script part by mesh-order index (the "
        "order of the mesh's triangle->face sidecar; also n_faces in "
        "metrics). Returns planar, outward normal, area_mm2, center, and "
        "n_faces — use it to find a face before push_pull.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                "face_index": {"type": "integer",
                               "description": "Mesh-order B-rep face index"},
            },
            ["project", "part_id", "face_index"],
        ),
        face_info,
    ))
    registry.register(Tool(
        "push_pull",
        "Push/pull a planar face of a script part by distance_mm (positive "
        "pulls material out along the face's outward normal; negative cuts "
        "in). Appends a marked, editable block to the part script wrapping "
        "build(p) in a push_face call, then rebuilds through the normal "
        "path. Composable: repeated calls append further blocks. Note that "
        "face indices are re-derived from the NEW geometry after each edit.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                "face_index": {"type": "integer",
                               "description": "Mesh-order B-rep face index "
                                              "(must be planar)"},
                "distance_mm": {"type": "number",
                                "description": "Distance in mm; > 0 adds "
                                               "material, < 0 removes"},
            },
            ["project", "part_id", "face_index", "distance_mm"],
        ),
        push_pull,
    ))
