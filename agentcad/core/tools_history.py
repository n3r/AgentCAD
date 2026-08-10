"""Tool pack: git-backed project history (undo/redo for agents and the UI).

Every persistent mutation — service methods and pack tools alike, i.e.
anything that publishes ``project_changed`` — commits a snapshot of the
project directory into a per-project git repo at ``<project>/.history``
(core/history.py, wired via ``AgentCADService._snapshot_on_event``). These
tools expose that history: ``project_history`` lists snapshots newest-first;
``project_restore`` overlays a snapshot's tracked content back onto the
project and appends a fresh "restore" commit, keeping history linear — undo
is "restore history[1]", redo is "restore the commit id you were on before
the undo".

Reentrancy: ``project_restore`` sets ``history.in_restore`` around the
checkout AND its own ``project_changed`` publish, so the service's bus hook
does not stack a second snapshot on top of the internal restore commit.
"""

from __future__ import annotations

from .history import HistoryError, looks_like_commit, valid_ref_name
from .model import NotFoundError, ValidationError
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}
_NO_GIT_NOTE = "git not found on PATH"


def register(registry, service) -> None:
    def project_history(project: str, limit: int = 20,
                        ref: str | None = None) -> dict:
        service.store.manifest(project)  # existence check -> notfound_error
        if not service.history.available():
            return {"available": False, "history": [], "note": _NO_GIT_NOTE}
        path = service.store.path_of(project)
        if ref is not None:
            if not valid_ref_name(ref) and not looks_like_commit(ref):
                raise ValidationError(f"invalid ref {ref!r}")
            if service.history.resolve_ref(path, ref) is None:
                raise NotFoundError(f"unknown branch, tag or commit {ref!r}")
        entries = service.history.log(path, limit, ref=ref)
        return {"available": True, "history": entries}

    def project_restore(project: str, commit: str) -> dict:
        service.store.manifest(project)  # existence check -> notfound_error
        if not service.history.available():
            raise ValidationError(f"cannot restore: {_NO_GIT_NOTE}")
        if service.store.write_guard is not None:
            # Restore rewrites project files outside the store: honor the
            # same turn lock every other persistent write is checked against.
            service.store.write_guard(project)
        path = service.store.path_of(project)
        # A commit id restores verbatim (unchanged behavior); a branch or tag
        # name is resolved to its commit first, so a ref can never reach git
        # as a raw argument.
        target = commit
        if not looks_like_commit(commit):
            if not valid_ref_name(commit):
                raise ValidationError(f"invalid commit id or ref {commit!r}")
            resolved = service.history.resolve_ref(path, commit)
            if resolved is None:
                raise NotFoundError(f"unknown branch, tag or commit {commit!r}")
            target = resolved
        head_before = service.history.head(path)
        service.history.in_restore = True
        try:
            try:
                service.history.restore(path, target)
            except HistoryError as exc:
                raise ValidationError(str(exc)) from exc
            service.bus.publish(
                {"type": "project_changed", "project": project,
                 "reason": "restore"}
            )
        finally:
            service.history.in_restore = False
        # A manual restore is itself an undoable step: its follow-up commit's
        # parent is the pre-restore state, so Cmd+Z can take it back. A no-op
        # restore (tree already at the target) moves nothing and records
        # nothing.
        head = service.history.head(path)
        if head and head != head_before:
            service.undo_cursor.on_snapshot(
                project, head, f"restore {target[:8]}"
            )
        # No cache surgery needed: build cache keys re-derive from the
        # restored content on the next read, so stale in-memory status
        # self-heals into a rebuild (or a cache hit on the old key).
        result = project_history(project)
        result["restored"] = commit
        return result

    registry.register(Tool(
        "project_history",
        "List a project's history snapshots, newest first: {id, message, ts}. "
        "A snapshot is committed automatically after every persistent "
        "mutation (part/script/param/assembly edits, mates, materials, PMI, "
        "imports), so entry [0] is the current state and entry [1] is the "
        "state before the latest change. Pass an id to project_restore to "
        "undo/redo. Derived data (.cache/, exports/) is never snapshotted. "
        "When git is not installed on the server, returns available:false "
        "with an empty list. Pass ref to read another branch's or a tag's "
        "history without switching your own branch.",
        schema(
            {
                "project": _PROJ,
                "limit": {
                    "type": "integer",
                    "description": "Max snapshots to return "
                                   "(default 20, clamped to 1..100)",
                },
                "ref": {
                    "type": "string",
                    "description": "Branch or tag name to read instead of "
                                   "your current branch (default: yours)",
                },
            },
            ["project"],
        ),
        project_history,
    ))
    registry.register(Tool(
        "project_restore",
        "Restore a project to a past snapshot (a commit id from "
        "project_history, or a branch or tag name), then append a new "
        "'restore' commit so history "
        "stays linear — to redo, restore the id you were on before undoing. "
        "Restore OVERLAYS the snapshot's tracked content: files created "
        "after that snapshot are not deleted, but a part added later "
        "disappears from the manifest and its script survives only as an "
        "invisible orphan file. Geometry rebuilds from the restored scripts "
        "on the next read. Returns the refreshed history plus {restored}. "
        "Fails with a validation_error for unknown commits or when git is "
        "missing, and with a conflict_error while someone else holds the "
        "editing turn.",
        schema(
            {
                "project": _PROJ,
                "commit": {
                    "type": "string",
                    "description": "Commit id from project_history (full or "
                                   "abbreviated hex), or a branch or tag name",
                },
            },
            ["project", "commit"],
        ),
        project_restore,
    ))
