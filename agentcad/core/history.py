"""ProjectHistory: git-backed per-project snapshot history (undo/redo).

Each project directory gets its own git repository whose GIT_DIR lives at
``<project>/.history`` and whose work tree is the project directory itself,
so no ``.git/`` ever appears inside a project. Every persistent mutation
(anything that publishes ``project_changed`` — see
``AgentCADService._snapshot_on_event``) commits a snapshot; ``restore``
overlays a past commit's tracked content back onto the work tree and appends
a fresh "restore" commit, so history stays strictly linear: undo is "restore
the previous entry", redo is "restore the commit id you were on before the
undo".

Only authored state is tracked — ``project.json``, ``parts/*.py``,
``imports/`` — while derived data (``.cache/``, ``exports/``) and the
history repo itself are excluded via ``.history/info/exclude``.

Restore semantics (v1, deliberate): ``git checkout <commit> -- .`` overlays
the target's tracked content but does not delete files absent from the
target. A part script added after the target commit therefore survives on
disk as an orphan — invisible, because the restored manifest no longer
references it — until it is garbage or re-adopted by a later state.

Design constraints: stdlib only, subprocess git with a hard timeout, and
``snapshot`` NEVER raises into the caller — a broken or missing git must
never break a CAD save.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_GIT_TIMEOUT_S = 10.0
_EXCLUDES = ".cache/\nexports/\n.history/\n*.tmp\n"
# Commit ids as handed out by log(): hex only, so an id can never be parsed
# as a git option or a ref expression.
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")


class HistoryError(RuntimeError):
    """A history operation failed (git missing, unknown commit, git error)."""


class ProjectHistory:
    """Stateless-per-project history driver; methods take the project path.

    ``in_restore`` is a reentrancy flag for the service's bus hook: the
    ``project_restore`` tool sets it around restore + its ``project_changed``
    publish so the hook does not stack a second snapshot on top of the
    internal "restore" commit.
    """

    def __init__(self) -> None:
        self._git: str | None = None
        self._checked = False
        self.in_restore = False

    # ------------------------------------------------------------- plumbing

    def available(self) -> bool:
        """True when a git executable is on PATH (resolved once, cached)."""
        if not self._checked:
            self._git = shutil.which("git")
            self._checked = True
        return self._git is not None

    def _run(
        self, project_path: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess:
        git_dir = project_path / ".history"
        cmd = [
            self._git or "git",
            "--git-dir", str(git_dir),
            "--work-tree", str(project_path),
            *args,
        ]
        # Hermetic git: no system/user config (identity is set locally in the
        # repo), no prompts, hard timeout. HOME/XDG point into .history so a
        # user's ~/.gitconfig (hooks, gpg signing, ...) cannot interfere.
        env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(git_dir),
            "XDG_CONFIG_HOME": str(git_dir / "xdg"),
        }
        try:
            result = subprocess.run(
                cmd,
                cwd=str(project_path),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",  # git output is UTF-8; never the cp1252 locale
                errors="replace",
                timeout=_GIT_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HistoryError(f"git {args[0]}: {exc}") from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise HistoryError(f"git {args[0]} failed: {detail}")
        return result

    @staticmethod
    def _has_repo(project_path: Path) -> bool:
        return (project_path / ".history" / "HEAD").is_file()

    def _ensure_repo(self, project_path: Path) -> None:
        if self._has_repo(project_path):
            return
        self._run(project_path, "init")
        self._run(project_path, "config", "user.name", "AgentCAD")
        self._run(project_path, "config", "user.email", "agentcad@local")
        self._run(project_path, "config", "commit.gpgsign", "false")
        exclude = project_path / ".history" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(_EXCLUDES, encoding="utf-8")

    # ------------------------------------------------------------------ api

    def snapshot(self, project_path: Path | str, message: str) -> str | None:
        """Commit the project's current authored state; return the new commit
        hash, or None when there is nothing to commit or git is unusable.

        Fast-failing and exception-free by contract: any git problem is
        logged to stderr and swallowed so a broken git can never break the
        CAD mutation that triggered the snapshot.
        """
        if not self.available():
            return None
        path = Path(project_path)
        try:
            self._ensure_repo(path)
            self._run(path, "add", "-A")
            staged = self._run(path, "diff", "--cached", "--quiet", check=False)
            if staged.returncode == 0:
                return None  # nothing changed since the last snapshot
            self._run(path, "commit", "-m", message or "change")
            return self._run(path, "rev-parse", "HEAD").stdout.strip()
        except Exception as exc:  # noqa: BLE001 — never raise into a CAD save
            print(f"[history] snapshot of {path.name!r} failed: {exc}",
                  file=sys.stderr)
            return None

    def log(self, project_path: Path | str, limit: int = 20) -> list[dict]:
        """Snapshots newest-first: [{"id", "message", "ts"}] (ts is the ISO
        commit time). Empty when git is missing or no snapshot exists yet."""
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return []
        limit = max(1, min(int(limit), 100))
        try:
            result = self._run(
                path, "log", "-n", str(limit),
                "--pretty=format:%H%x1f%cI%x1f%s", check=False,
            )
            if result.returncode != 0:
                return []  # e.g. unborn branch: repo exists, no commits yet
            entries = []
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                commit, ts, message = line.split("\x1f", 2)
                entries.append({"id": commit, "message": message, "ts": ts})
            return entries
        except Exception as exc:  # noqa: BLE001 — reads must never raise
            print(f"[history] log of {path.name!r} failed: {exc}",
                  file=sys.stderr)
            return []

    def restore(self, project_path: Path | str, commit: str) -> None:
        """Overlay ``commit``'s tracked content onto the project, then append
        a follow-up "restore <id>" snapshot so history stays linear.

        Raises HistoryError on a missing git, a project without history, or
        an unknown/malformed commit id. Untracked-in-target files are NOT
        deleted (see the module docstring for the orphan semantics).
        """
        if not self.available():
            raise HistoryError("git not found on PATH")
        path = Path(project_path)
        if not self._has_repo(path):
            raise HistoryError("project has no history yet")
        if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
            raise HistoryError(f"invalid commit id {commit!r}")
        probe = self._run(path, "cat-file", "-e", f"{commit}^{{commit}}",
                          check=False)
        if probe.returncode != 0:
            raise HistoryError(f"unknown commit {commit!r}")
        self._run(path, "checkout", commit, "--", ".")
        self.snapshot(path, f"restore {commit[:8]}")

    # ---------------------------------------------------- cursor primitives

    def head(self, project_path: Path | str) -> str | None:
        """Current head commit id, or None (no repo / unborn / no git)."""
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return None
        result = self._run(path, "rev-parse", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def parent_of(self, project_path: Path | str, commit: str) -> str | None:
        """First parent of ``commit``, or None (root commit / unknown id)."""
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return None
        if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
            return None
        result = self._run(path, "rev-parse", f"{commit}^", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def has_commit(self, project_path: Path | str, commit: str) -> bool:
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return False
        if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
            return False
        probe = self._run(path, "cat-file", "-e", f"{commit}^{{commit}}",
                          check=False)
        return probe.returncode == 0


class UndoCursor:
    """One-keystroke undo/redo over a project's linear git history.

    The git history (ProjectHistory) is the durable record; this cursor is
    the in-memory two-stack UX on top of it. Each real mutation snapshot
    pushes its commit id + label onto the undo stack (and clears redo, the
    standard semantics); ``undo`` restores the top mutation's PARENT tree,
    moving that state's id to the redo stack; ``redo`` restores it back.
    Every restore rides ProjectHistory.restore, so each step is itself a
    linear "restore" commit and the durable history never rewrites.

    Stacks are process-memory (like the chat history): after a server
    restart ``undo`` degrades to a single step back through the latest
    snapshot via the git log, and ``redo`` is empty. Bounded to
    ``UNDO_LIMIT`` entries per project.
    """

    UNDO_LIMIT = 100

    def __init__(self, history: ProjectHistory, store, bus) -> None:
        import threading

        self.history = history
        self.store = store
        self.bus = bus
        self._lock = threading.Lock()
        self._undo: dict[str, list[dict]] = {}
        self._redo: dict[str, list[dict]] = {}

    def on_snapshot(self, proj: str, commit_id: str, label: str) -> None:
        """Record a real mutation snapshot (called from the service's bus
        hook). Clears the redo stack — a new edit forks away from it."""
        with self._lock:
            stack = self._undo.setdefault(proj, [])
            stack.append({"id": commit_id, "label": label})
            del stack[: -self.UNDO_LIMIT]
            self._redo.pop(proj, None)

    def undo(self, proj: str) -> dict:
        return self._step(proj, redo=False)

    def redo(self, proj: str) -> dict:
        return self._step(proj, redo=True)

    def status(self, proj: str) -> dict:
        """Undoable/redoable labels, newest first (no git calls)."""
        with self._lock:
            return {
                "available": self.history.available(),
                "undo": [e["label"] for e in reversed(self._undo.get(proj, []))],
                "redo": [e["label"] for e in reversed(self._redo.get(proj, []))],
            }

    # ------------------------------------------------------------- internals

    def _step(self, proj: str, *, redo: bool) -> dict:
        from .model import ConflictError, ValidationError

        verb = "redo" if redo else "undo"
        if not self.history.available():
            raise ValidationError("undo/redo unavailable: git not found on PATH")
        path = self.store.path_of(proj)
        # Turn-locking: undo/redo rewrites project files outside the store
        # choke point, so it must invoke the same write guard explicitly.
        if getattr(self.store, "write_guard", None):
            self.store.write_guard(proj)
        with self._lock:
            source = (self._redo if redo else self._undo).setdefault(proj, [])
            while source and not self.history.has_commit(path, source[-1]["id"]):
                source.pop()  # history repo was pruned/replaced under us
            entry = source.pop() if source else None
            if entry is None and not redo:
                # Post-restart fallback: one step back through the latest
                # snapshot. Refused when that snapshot is itself a restore —
                # undoing it would act as a redo, and repeated fallback undos
                # would oscillate between two states.
                log = self.history.log(path, limit=1)
                if log and not log[0]["message"].startswith("restore "):
                    entry = {"id": log[0]["id"], "message_from_log": True,
                             "label": log[0]["message"]}
            if entry is None:
                raise ConflictError(f"nothing to {verb}")
            if redo:
                # entry["id"] captures the state to return to; going back is
                # "undo the redo": restore its parent again later.
                target = entry["id"]
            else:
                target = self.history.parent_of(path, entry["id"])
                if target is None:
                    # The root snapshot has no parent: nothing before it to
                    # return to. Keep the stack entry — it wasn't consumed.
                    if not entry.get("message_from_log"):
                        source.append(entry)
                    raise ConflictError(f"nothing to {verb}")
            opposite = (self._undo if redo else self._redo).setdefault(proj, [])
            opposite.append(entry)
            del opposite[: -self.UNDO_LIMIT]
            self.history.in_restore = True
            try:
                self.history.restore(path, target)
                self.bus.publish(
                    {"type": "project_changed", "project": proj, "reason": verb}
                )
            except HistoryError as exc:
                opposite.pop()  # the step never happened; don't fake a redo
                raise ValidationError(f"{verb} failed: {exc}") from exc
            finally:
                self.history.in_restore = False
            counts = {
                "undo": len(self._undo.get(proj, [])),
                "redo": len(self._redo.get(proj, [])),
            }
        return {"label": entry["label"], **counts}
