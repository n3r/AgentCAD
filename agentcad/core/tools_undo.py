"""Tool pack: one-keystroke undo/redo over the git-backed project history.

The durable record is ProjectHistory (see tools_history's project_history /
project_restore); this pack is the two-stack cursor UX on top of it, shared
by the UI's Cmd+Z and every agent surface.
"""

from __future__ import annotations

from .tools import Tool, schema


def register(registry, service) -> None:
    def undo(project: str) -> dict:
        info = service.undo_cursor.undo(project)
        return {"undone": info["label"],
                "history": service.undo_cursor.status(project)}

    def redo(project: str) -> dict:
        info = service.undo_cursor.redo(project)
        return {"redone": info["label"],
                "history": service.undo_cursor.status(project)}

    def get_history(project: str) -> dict:
        service.store.path_of(project)  # existence check -> notfound_error
        return service.undo_cursor.status(project)

    registry.register(Tool(
        "undo",
        "Undo the last mutation to this project (param change, move, script "
        "edit, part add/delete, mate, materials, PMI) regardless of which "
        "client made it. Steps back through the git-backed project history; "
        "returns what was undone. Error when nothing to undo. After a server "
        "restart only one step back is available until new edits are made.",
        schema({"project": {"type": "string"}}, ["project"]),
        undo,
    ))
    registry.register(Tool(
        "redo",
        "Redo the most recently undone mutation. The redo stack clears when "
        "any new mutation happens.",
        schema({"project": {"type": "string"}}, ["project"]),
        redo,
    ))
    registry.register(Tool(
        "get_history",
        "List undoable/redoable action labels for a project, newest first "
        "(plus `available: false` when git is missing). For the full durable "
        "snapshot log with commit ids, use project_history.",
        schema({"project": {"type": "string"}}, ["project"]),
        get_history,
    ))
