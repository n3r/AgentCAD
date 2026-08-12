"""Tool pack: one-keystroke undo/redo over the git-backed project history.

The durable record is ProjectHistory (see tools_history's project_history /
project_restore); this pack is the two-stack cursor UX on top of it, shared
by the UI's Cmd+Z and every agent surface.

``scope`` (PRD-008) selects WHOSE entry a step consumes. It defaults to
``"any"`` — one shared stack, exactly as before authorship existed, because
a human taking back the agent's edit with Cmd+Z is the point of the product.
``"mine"`` is the opt-in for a shared session.
"""

from __future__ import annotations

from .tools import Tool, schema

_SCOPE = {
    "type": "string",
    "enum": ["any", "mine"],
    "description": "Whose entry to step through: 'any' (default) is the "
                   "shared stack — you may undo another client's edit; "
                   "'mine' skips other clients' entries and takes back only "
                   "your own most recent one.",
}


def register(registry, service) -> None:
    def undo(project: str, scope: str = "any") -> dict:
        info = service.undo_cursor.undo(project, scope)
        return {"undone": info["label"],
                "history": service.undo_cursor.status(project)}

    def redo(project: str, scope: str = "any") -> dict:
        info = service.undo_cursor.redo(project, scope)
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
        "restart only one step back is available until new edits are made. "
        "With scope='mine' this takes back your own most recent edit instead, "
        "skipping other clients' — best-effort over one shared linear "
        "history: if your edit is no longer the latest commit it is undone "
        "with a git revert, and a later change that overlaps it makes the "
        "undo a conflict_error naming the blocking commits, never a merge and "
        "never a partial apply.",
        schema({"project": {"type": "string"}, "scope": _SCOPE}, ["project"]),
        undo,
    ))
    registry.register(Tool(
        "redo",
        "Redo the most recently undone mutation. The redo stack clears when "
        "any new mutation happens. scope='mine' redoes your own most recent "
        "undo; a step that was undone by a revert is redone by reverting that "
        "revert, so other clients' later work stays put.",
        schema({"project": {"type": "string"}, "scope": _SCOPE}, ["project"]),
        redo,
    ))
    registry.register(Tool(
        "get_history",
        "List undoable/redoable action labels for a project, newest first "
        "(plus `available: false` when git is missing, and `mine` counting "
        "how many entries on each stack are yours). For the full durable "
        "snapshot log with commit ids, use project_history.",
        schema({"project": {"type": "string"}}, ["project"]),
        get_history,
    ))
