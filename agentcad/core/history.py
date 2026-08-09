"""Per-project undo/redo: in-memory snapshots of the mutable project state.

A snapshot is the byte content of ``project.json`` + every ``parts/*.py`` —
the complete mutable state (caches/exports are derived and content-hashed, so
a restore self-invalidates them). History is in-memory and bounded, like chat
history; the seam exists so a durable (git-backed) store can replace it.
"""

from __future__ import annotations

import hashlib
from collections import deque

from .model import ConflictError
from .project import ProjectStore

HISTORY_LIMIT = 50


class HistoryManager:
    def __init__(self, store, bus, lock, on_restore, limit=HISTORY_LIMIT):
        self.store = store
        self.bus = bus
        self._lock = lock
        self._on_restore = on_restore
        self._limit = limit
        self._undo: dict[str, deque] = {}
        self._redo: dict[str, list] = {}

    def checkpoint(self, proj: str, label: str) -> None:
        """Capture the pre-mutation state; call after validation, right before
        the first store write. ``label`` names the action about to happen."""
        with self._lock:
            entry = self._capture(proj, label)
            stack = self._undo_stack(proj)
            if stack and stack[-1]["hash"] == entry["hash"]:
                stack[-1] = entry  # earlier op never wrote; newest label wins
            else:
                stack.append(entry)
            self._redo.pop(proj, None)

    def undo(self, proj: str) -> dict:
        return self._step(proj, self._undo_stack, self._redo_stack, "undo")

    def redo(self, proj: str) -> dict:
        return self._step(proj, self._redo_stack, self._undo_stack, "redo")

    def status(self, proj: str) -> dict:
        with self._lock:
            return {
                "undo": [e["label"] for e in reversed(self._undo.get(proj, []))],
                "redo": [e["label"] for e in reversed(self._redo.get(proj, []))],
            }

    # ------------------------------------------------------------- internals

    # The undo side is a bounded deque; redo is a plain list (its depth can
    # never exceed what undo produced). _step treats both as stacks.

    def _undo_stack(self, proj):
        return self._undo.setdefault(proj, deque(maxlen=self._limit))

    def _redo_stack(self, proj):
        return self._redo.setdefault(proj, [])

    def _step(self, proj, source, target, verb) -> dict:
        with self._lock:
            stack = source(proj)
            current = self._capture(proj, "")
            while stack and stack[-1]["hash"] == current["hash"]:
                stack.pop()  # checkpoint whose operation never wrote
            if not stack:
                raise ConflictError(f"nothing to {verb}")
            entry = stack.pop()
            current["label"] = entry["label"]
            target(proj).append(current)
            self._restore(proj, entry["files"])
            counts = {
                "undo": len(self._undo.get(proj, [])),
                "redo": len(self._redo.get(proj, [])),
            }
        self.bus.publish({"type": "project_changed", "project": proj})
        return {"label": entry["label"], **counts}

    def _capture(self, proj: str, label: str) -> dict:
        root = self.store.path_of(proj)
        files = {"project.json": (root / "project.json").read_bytes()}
        parts = root / "parts"
        if parts.is_dir():
            for path in sorted(parts.glob("*.py")):
                files[f"parts/{path.name}"] = path.read_bytes()
        digest = hashlib.sha256()
        for name in sorted(files):
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(files[name]).digest())
        return {"label": label, "files": files, "hash": digest.hexdigest()}

    def _restore(self, proj: str, files: dict) -> None:
        root = self.store.path_of(proj)
        for rel, data in files.items():
            ProjectStore._atomic_write(root / rel, data)
        parts = root / "parts"
        if parts.is_dir():
            for path in parts.glob("*.py"):
                if f"parts/{path.name}" not in files:
                    path.unlink()
        self._on_restore(proj)
