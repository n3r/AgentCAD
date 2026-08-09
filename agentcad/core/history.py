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
