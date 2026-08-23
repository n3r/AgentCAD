"""Tool pack: navigation at scale — part metadata, search, bulk ops (PRD-027).

**Load order.** Tool packs are discovered by `_load_tool_packs` in
``pkgutil.iter_modules`` order, which is alphabetical: this module sorts at
``nav`` — after ``tools_materials`` (so `service.materials` is already the
project-aware resolver by the time anything here reads a material) and before
``tools_packages`` / ``tools_proposals``. That last one matters and is the
``tools_run_checks`` trap in AGENTS.md restated: ``tools_proposals`` sets
``service.gate_providers = []`` unconditionally, so a gate provider registered
from here would be thrown away without a word. **This pack registers none**,
and must not grow one — a navigation concern has nothing to prove about a
package anyway.

**What it does not touch.** Organizing is not building. Nothing in this pack
calls `ensure_mesh` or `_ensure_built`, nothing enters `_cache_key`'s payload,
and `folder`/`tags` are absent from every geometry request — moving a part
between folders must never invalidate its cached mesh.

**Events.** A metadata write publishes `project_changed` (which is what the
history snapshot hook sees — one publish, one snapshot, one undo step) and
then `parts_meta_changed`, the narrow event a client uses to re-file rows
without reloading the whole project. The order is load-bearing: a client that
acts on `parts_meta_changed` must be able to assume the durable write is
already done and already snapshotted.
"""

from __future__ import annotations

from .tools import Tool, schema

#: "this keyword was not passed" — `folder=None` MEANS root, so it cannot
#: double as "leave it alone" (the `active_config` precedent in project.py).
_UNSET = object()

_FOLDER_DOC = (
    "Folder path, '/'-separated, 1-8 segments of "
    "[A-Za-z0-9][A-Za-z0-9 _.-]{0,39} with no leading/trailing space "
    "(e.g. 'Chassis/Left side'). Case is kept as typed and matched "
    "case-insensitively. Send \"\" (or null) to move the part to the root; "
    "OMIT the key to leave the folder unchanged."
)

_TAGS_DOC = (
    "Full replacement tag list. Tags are normalized on write — stripped, "
    "lowercased, de-duplicated keeping first-seen order — and must match "
    "[a-z0-9][a-z0-9_.-]{0,31} afterwards (max 32 per part); a tag that is "
    "still invalid is a validation_error naming it. Send [] to clear all "
    "tags; OMIT the key to leave them unchanged."
)


def register(registry, service) -> None:
    def set_part_meta(project: str, part_id: str, folder=_UNSET,
                      tags: list | None = None) -> dict:
        """Organize one part: its folder and/or its tags (FR1).

        Both fields are independently optional. Omitting one leaves it alone;
        ``folder`` of ``""``/``null`` files the part at the project root, and
        ``tags`` of ``[]`` clears them. Omitting BOTH is a read-back: it
        writes nothing and publishes nothing, because a `project_changed` for
        a change nobody made costs every connected browser a reload and
        (where git is available) asks the history for a snapshot of nothing.
        """
        fields = []
        if folder is not _UNSET:
            fields.append("folder")
        if tags is not None:
            fields.append("tags")
        if not fields:
            record = service.store.get_part(project, part_id)  # 404s honestly
            return {"id": record.id, "folder": record.folder,
                    "tags": list(record.tags)}
        record = service.store.update_part_meta(
            project, part_id,
            **({} if folder is _UNSET else {"folder": folder}),
            tags=tags,
        )
        # project_changed FIRST: it is the publish the history hook snapshots
        # on, so anything reacting to parts_meta_changed can assume the write
        # is durable and already undoable.
        service.bus.publish({"type": "project_changed", "project": project,
                             "part": part_id, "reason": "meta"})
        service.bus.publish({"type": "parts_meta_changed", "project": project,
                             "part_ids": [part_id], "fields": fields})
        return {"id": record.id, "folder": record.folder,
                "tags": list(record.tags)}

    registry.register(Tool(
        "set_part_meta",
        "Set a part's navigation metadata: which folder it is filed under and "
        "its tags. Folders and tags are project organization stored in the "
        "manifest — they never move the part's script (always parts/<id>.py) "
        "and never invalidate its geometry cache. Omit a field to leave it "
        "unchanged; this is one undoable step. "
        f"folder: {_FOLDER_DOC} tags: {_TAGS_DOC}",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                # A plain "string" type, not ["string", "null"]: the registry's
                # validator looks its type up in a dict (`_TYPE_CHECKS.get`),
                # and a list is unhashable — a type list raises TypeError
                # inside validation rather than being accepted. `null` still
                # reaches the handler (the validator skips the type check for
                # None on an optional argument) and means root, and so does "".
                "folder": {"type": "string", "description": _FOLDER_DOC},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": _TAGS_DOC},
            },
            ["project", "part_id"],
        ),
        set_part_meta,
    ))

    # --- search (slice 2) ---

    # --- thumbnails (slice 3) ---

    # --- bulk (slice 4) ---
