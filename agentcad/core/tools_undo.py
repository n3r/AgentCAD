"""Tool pack: undo/redo the last project mutation (shared with the UI)."""

from __future__ import annotations

from .tools import Tool, schema


def register(registry, service) -> None:
    def undo(project: str) -> dict:
        info = service.history.undo(project)
        return {"undone": info["label"],
                "history": {"undo": info["undo"], "redo": info["redo"]}}

    def redo(project: str) -> dict:
        info = service.history.redo(project)
        return {"redone": info["label"],
                "history": {"undo": info["undo"], "redo": info["redo"]}}

    def get_history(project: str) -> dict:
        return service.history.status(project)

    registry.register(Tool(
        "undo",
        "Undo the last mutation to this project (param change, move, script "
        "edit, part add/delete, mate, materials) regardless of which client "
        "made it. Returns what was undone; error when nothing to undo.",
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
        "List undoable/redoable action labels for a project, newest first.",
        schema({"project": {"type": "string"}}, ["project"]),
        get_history,
    ))
