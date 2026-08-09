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

Every method takes a *working tree* path, which may be the project directory
(the default branch) or a linked git worktree under ``.history/trees/<branch>/``
created by ``core/branches.py`` — ``_locate`` finds the right GIT_DIR either
way, so snapshots, restores and undo all land on the branch whose tree they
were handed.

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
# Managed exclude lines, appended to info/exclude when missing — never a
# rewrite: the file is user-editable and may carry lines we know nothing about.
_EXCLUDE_LINES = (".cache/", "exports/", ".history/", "*.tmp")
_EXCLUDES = "".join(f"{line}\n" for line in _EXCLUDE_LINES)
# Commit ids as handed out by log(): hex only, so an id can never be parsed
# as a git option or a ref expression.
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
# Branch/tag names accepted from callers. Same defense as _COMMIT_RE: a ref
# name can never start with '-' (an option), contain a revision expression, or
# name a path traversal. The extra rejects below are shapes the regex allows
# but git refuses or reads specially.
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,63}$")
_REF_REJECT = ("..", "@{", ".lock", "//")


def looks_like_commit(value: object) -> bool:
    """True for a raw commit id (what log() hands out)."""
    return isinstance(value, str) and _COMMIT_RE.match(value) is not None


def valid_ref_name(value: object) -> bool:
    """True for a branch/tag name safe to pass to git as an argument."""
    if not isinstance(value, str) or not _REF_RE.match(value):
        return False
    if value.endswith(("/", ".")) or any(bad in value for bad in _REF_REJECT):
        return False
    return True


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

    @staticmethod
    def _locate(project_path: Path) -> Path:
        """The GIT_DIR driving ``project_path``.

        The main working tree (the project directory) is driven by
        ``<project>/.history``; a linked worktree — one branch's checkout
        under ``.history/trees/<name>/`` — carries a ``.git`` *file* pointing
        at its admin directory under ``.history/worktrees/<name>``, which is
        what git wants as ``--git-dir`` for that tree.
        """
        dotgit = project_path / ".git"
        if dotgit.is_file():
            try:
                text = dotgit.read_text(encoding="utf-8")
            except OSError:
                return project_path / ".history"
            if "gitdir:" in text:
                return Path(text.split("gitdir:", 1)[1].strip())
        return project_path / ".history"

    def _run(
        self, project_path: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess:
        return self._exec(project_path, args, check=check, binary=False)

    def _run_bytes(
        self, project_path: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess:
        """Same call, undecoded stdout/stderr.

        Content that is not text — anything tracked under ``imports/`` — must
        never round-trip through ``str``: the text path decodes with
        ``errors="replace"``, which silently rewrites every non-UTF-8 byte.
        """
        return self._exec(project_path, args, check=check, binary=True)

    def _exec(
        self, project_path: Path, args: tuple[str, ...], *,
        check: bool, binary: bool,
    ) -> subprocess.CompletedProcess:
        git_dir = self._locate(project_path)
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
        text_kwargs = (
            {}
            if binary
            else {
                "text": True,
                # git output is UTF-8; never the cp1252 locale
                "encoding": "utf-8",
                "errors": "replace",
            }
        )
        try:
            result = subprocess.run(
                cmd,
                cwd=str(project_path),
                env=env,
                capture_output=True,
                timeout=_GIT_TIMEOUT_S,
                **text_kwargs,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HistoryError(f"git {args[0]}: {exc}") from exc
        if check and result.returncode != 0:
            stderr = result.stderr
            stdout = result.stdout
            if binary:
                stderr = stderr.decode("utf-8", "replace")
                stdout = stdout.decode("utf-8", "replace")
            detail = stderr.strip() or stdout.strip()
            raise HistoryError(f"git {args[0]} failed: {detail}")
        return result

    @staticmethod
    def _has_repo(project_path: Path) -> bool:
        return (
            (project_path / ".history" / "HEAD").is_file()
            or (project_path / ".git").is_file()  # linked worktree
        )

    def _ensure_repo(self, project_path: Path) -> None:
        if self._has_repo(project_path):
            self._refresh_excludes(project_path)
            return
        self._run(project_path, "init")
        self._run(project_path, "config", "user.name", "AgentCAD")
        self._run(project_path, "config", "user.email", "agentcad@local")
        self._run(project_path, "config", "commit.gpgsign", "false")
        self._refresh_excludes(project_path)

    @staticmethod
    def _refresh_excludes(project_path: Path) -> None:
        """Keep ``info/exclude`` current by APPENDING the managed lines it is
        missing — never by rewriting it.

        Projects created by earlier versions keep whatever list they were
        initialized with; new entries (branch worktrees live under
        ``.history/``) would otherwise never reach them. The file is also a
        legitimate place for a user to add their own patterns, so anything
        already there is preserved. Linked worktrees share the main repo's
        copy, so only the main tree writes it.
        """
        git_dir = project_path / ".history"
        if not git_dir.is_dir():
            return
        exclude = git_dir / "info" / "exclude"
        try:
            current = (
                exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
            )
            present = {line.strip() for line in current.splitlines()}
            missing = [line for line in _EXCLUDE_LINES if line not in present]
            if not missing:
                return
            if current and not current.endswith("\n"):
                current += "\n"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(
                current + "".join(f"{line}\n" for line in missing),
                encoding="utf-8",
            )
        except OSError:  # a read-only project must not break the snapshot
            pass

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

    def log(self, project_path: Path | str, limit: int = 20,
            ref: str | None = None) -> list[dict]:
        """Snapshots newest-first: [{"id", "message", "ts"}] (ts is the ISO
        commit time). Empty when git is missing or no snapshot exists yet.

        ``ref`` (a branch or tag name) reads another line of history without
        touching the working tree; an unknown or malformed ref reads as empty.
        """
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return []
        limit = max(1, min(int(limit), 100))
        extra: list[str] = []
        if ref is not None:
            if not valid_ref_name(ref) and not looks_like_commit(ref):
                return []
            extra = [ref]
        try:
            result = self._run(
                path, "log", "-n", str(limit),
                "--pretty=format:%H%x1f%cI%x1f%s", *extra, check=False,
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

    # -------------------------------------------------------- ref primitives

    def resolve_ref(self, project_path: Path | str, ref: str) -> str | None:
        """Commit id for a branch name, tag name or commit id; None when the
        ref is unknown or malformed (never raises, never shells a bad name)."""
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return None
        if not valid_ref_name(ref) and not looks_like_commit(ref):
            return None
        result = self._run(path, "rev-parse", "--verify", "--quiet",
                           f"{ref}^{{commit}}", check=False)
        commit = result.stdout.strip()
        return commit if result.returncode == 0 and commit else None

    def branches(self, project_path: Path | str) -> list[dict]:
        """[{"name", "head", "ts", "message"}] for every local branch."""
        return self._for_each_ref(
            project_path, "refs/heads",
            "%(refname:short)%1f%(objectname)%1f%(committerdate:iso-strict)"
            "%1f%(contents:subject)",
            ("name", "head", "ts", "message"),
        )

    def tags(self, project_path: Path | str) -> list[dict]:
        """[{"name", "commit", "ts", "author", "message"}] for every tag.

        ``commit`` is the tagged commit even for annotated tags (whose own
        object id is the tag object, not the commit).
        """
        rows = self._for_each_ref(
            project_path, "refs/tags",
            "%(refname:short)%1f%(objectname)%1f%(*objectname)"
            "%1f%(creatordate:iso-strict)%1f%(taggername)%1f%(contents:subject)",
            ("name", "object", "peeled", "ts", "author", "message"),
        )
        out = []
        for row in rows:
            out.append({
                "name": row["name"],
                "commit": row["peeled"] or row["object"],
                "ts": row["ts"],
                "author": row["author"],
                "message": row["message"],
            })
        return out

    def _for_each_ref(self, project_path: Path | str, namespace: str,
                      fmt: str, fields: tuple[str, ...]) -> list[dict]:
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return []
        try:
            result = self._run(path, "for-each-ref", f"--format={fmt}",
                               namespace, check=False)
            if result.returncode != 0:
                return []
            rows = []
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                values = line.split("\x1f")
                values += [""] * (len(fields) - len(values))
                rows.append(dict(zip(fields, values)))
            return rows
        except Exception as exc:  # noqa: BLE001 — reads must never raise
            print(f"[history] for-each-ref in {path.name!r} failed: {exc}",
                  file=sys.stderr)
            return []

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
    ``UNDO_LIMIT`` entries per stack, and keyed by ``store.lock_key(proj)``
    so each branch's working tree gets its own undo/redo history.
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

    def _key(self, proj: str) -> str:
        """Stack key: the project name normally, the caller's working tree
        when branching is active — one undo/redo stack per branch (FR11)."""
        lock_key = getattr(self.store, "lock_key", None)
        return lock_key(proj) if callable(lock_key) else proj

    def on_snapshot(self, proj: str, commit_id: str, label: str,
                    undo_to: str | None = None) -> None:
        """Record a real mutation snapshot (called from the service's bus
        hook). Clears the redo stack — a new edit forks away from it.

        ``undo_to`` names the state to go back to when the entry's first
        parent is NOT it: a fast-forward merge moves the target branch onto a
        commit whose first parent belongs to the *source*, so undoing it would
        land on a state the target never had.
        """
        key = self._key(proj)
        with self._lock:
            stack = self._undo.setdefault(key, [])
            entry = {"id": commit_id, "label": label}
            if undo_to:
                entry["undo_to"] = undo_to
            stack.append(entry)
            del stack[: -self.UNDO_LIMIT]
            self._redo.pop(key, None)

    def undo(self, proj: str) -> dict:
        return self._step(proj, redo=False)

    def redo(self, proj: str) -> dict:
        return self._step(proj, redo=True)

    def status(self, proj: str) -> dict:
        """Undoable/redoable labels, newest first (no git calls)."""
        key = self._key(proj)
        with self._lock:
            return {
                "available": self.history.available(),
                "undo": [e["label"] for e in reversed(self._undo.get(key, []))],
                "redo": [e["label"] for e in reversed(self._redo.get(key, []))],
            }

    # ------------------------------------------------------------- internals

    def _step(self, proj: str, *, redo: bool) -> dict:
        from .model import ConflictError, ValidationError

        verb = "redo" if redo else "undo"
        if not self.history.available():
            raise ValidationError("undo/redo unavailable: git not found on PATH")
        path = self.store.path_of(proj)
        key = self._key(proj)
        # Turn-locking: undo/redo rewrites project files outside the store
        # choke point, so it must invoke the same write guard explicitly.
        if getattr(self.store, "write_guard", None):
            self.store.write_guard(proj)
        with self._lock:
            source = (self._redo if redo else self._undo).setdefault(key, [])
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
                target = entry.get("undo_to") or self.history.parent_of(
                    path, entry["id"]
                )
                if target is None:
                    # The root snapshot has no parent: nothing before it to
                    # return to. Keep the stack entry — it wasn't consumed.
                    if not entry.get("message_from_log"):
                        source.append(entry)
                    raise ConflictError(f"nothing to {verb}")
            opposite = (self._undo if redo else self._redo).setdefault(key, [])
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
                "undo": len(self._undo.get(key, [])),
                "redo": len(self._redo.get(key, [])),
            }
        return {"label": entry["label"], **counts}
