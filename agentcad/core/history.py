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

Authorship (PRD-008, design Decision 15): every snapshot carries a
``Client: <client id>`` TRAILER — a body line, never the subject, so every
subject-prefix contract in the tree (``"restore "`` below, the proposals
reconciler's scans, and every exact-message assertion in the suite, all of
which read ``%s``) is unaffected. Git's own author/committer stay the fixed
repo-local identity on purpose: the client id is a self-asserted header, and
rewriting a commit's author with it would dress bookkeeping up as a
cryptographic claim about who wrote the change. Reading authorship back
(:func:`author_of`) accepts ``Merged-by:`` as well, because that is the
trailer ``core/merge.py`` has written on two-parent merge commits since
PRD-001 and its exact message is pinned by that feature's tests.

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

from . import locks

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
# The authorship trailer (Decision 15). Matched anywhere in the BODY, so a
# caller that already wrote one (the merge orchestrator's style) keeps it.
_CLIENT_TRAILER_RE = re.compile(r"^Client:[ \t]*(.+?)[ \t]*$", re.M)
# The merge orchestrator's own authorship trailer. ``merge.py`` (PRD-001) has
# written ``Merged-by:`` since before ``Client:`` existed and its exact commit
# message is pinned by that feature's tests, so authorship is read from BOTH
# spellings here rather than by rewriting what a merge says. Without this a
# two-parent merge — the one commit that always has a person behind it —
# reported ``author: null`` and could never be selected by a post-restart
# ``scope: "mine"`` undo.
_MERGED_BY_TRAILER_RE = re.compile(r"^Merged-by:[ \t]*(.+?)[ \t]*$", re.M)
# One record per commit in log(): %B is multi-line, so commits are separated
# by \x1e and fields within a record by \x1f. Deliberately NOT git's
# %(trailers:...) placeholder — that one needs git >= 2.22 and degrades by
# emitting itself literally, which would put junk in every author field.
_LOG_FORMAT = "%H%x1f%cI%x1f%s%x1f%B%x1e"


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


def with_client_trailer(message: str) -> str:
    """``message`` plus a ``Client:`` trailer, unless it already carries one.

    The subject line is never touched — the trailer is appended after a blank
    line, which is where git looks for trailers and where nothing in this repo
    parses.
    """
    body = message or "change"
    if _CLIENT_TRAILER_RE.search(body):
        return body
    return f"{body.rstrip()}\n\nClient: {locks.current_client_id()}\n"


def author_of(body: str) -> str | None:
    """The commit's client identity, or None for a commit written before
    authorship existed. Never ``"unknown"``: absent is a different fact.

    Two trailers, checked in that order: ``Client:``, which every snapshot
    carries, and ``Merged-by:``, which is what ``core/merge.py`` has always
    written on a two-parent merge commit. ``Client:`` wins when both are
    present, because it is the one this module puts there.
    """
    text = body or ""
    match = (_CLIENT_TRAILER_RE.search(text)
             or _MERGED_BY_TRAILER_RE.search(text))
    return match.group(1) if match else None


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
            self._run(path, "commit", "-m", with_client_trailer(message))
            return self._run(path, "rev-parse", "HEAD").stdout.strip()
        except Exception as exc:  # noqa: BLE001 — never raise into a CAD save
            print(f"[history] snapshot of {path.name!r} failed: {exc}",
                  file=sys.stderr)
            return None

    def log(self, project_path: Path | str, limit: int = 20,
            ref: str | None = None) -> list[dict]:
        """Snapshots newest-first: [{"id", "message", "ts", "author"}] (ts is
        the ISO commit time, ``author`` the ``Client:`` trailer's value or
        None). Empty when git is missing or no snapshot exists yet.

        ``message`` is the SUBJECT (``%s``) exactly as before the authorship
        trailer existed — callers that match on it are unaffected.

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
                f"--pretty=format:{_LOG_FORMAT}", *extra, check=False,
            )
            if result.returncode != 0:
                return []  # e.g. unborn branch: repo exists, no commits yet
            entries = []
            for record in result.stdout.split("\x1e"):
                record = record.lstrip("\n")
                if not record.strip():
                    continue
                commit, ts, message, body = record.split("\x1f", 3)
                entries.append({"id": commit, "message": message, "ts": ts,
                                "author": author_of(body)})
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

    def revert(self, project_path: Path | str, commit: str,
               message: str | None = None, since: str | None = None) -> str:
        """Undo ONE commit's changes as a new commit, leaving every later
        commit standing; returns the new commit id (PRD-008, Decision 16).

        ``since`` widens that to the RANGE ``since..commit`` — every commit
        reachable from ``commit`` but not from ``since``, inverted newest
        first into a single commit. It exists for one caller: undoing a
        **fast-forward merge**, where the entry's ``undo_to`` names the state
        the target branch was on and everything between it and the entry
        arrived in that one merge. Reverting only the tip there leaves the
        merge half undone. A ``since`` that is not an ancestor of ``commit``
        is ignored (the single-commit revert is the honest fallback), and a
        range that contains a merge commit is refused *before* git is asked —
        a mainline cannot be meaningful for every commit of a range at once —
        as a ``ConflictError`` with ``reason: "merge_in_range"`` naming the
        merge, in the same shape as every other refusal here.

        This is what a ``scope: "mine"`` undo does when the caller's edit is no
        longer the branch head: ``restore`` would overlay a whole past tree and
        silently take somebody else's later work with it, so a targeted revert
        is the only honest step. A two-parent commit (a merge) is reverted
        against its FIRST parent — the branch that was merged *into*, i.e. the
        one that keeps.

        Never a partial apply (FR14), **in both directions**: a conflict is
        rolled back and raised as a ``ConflictError`` carrying
        ``{commit, reason, paths, blocked_by}``, and so is a failure *after*
        the patch applied cleanly — a repository hook rejecting the commit
        leaves the inverse patch applied and staged, which is a mutated project
        behind an error saying nothing happened. A commit whose changes are
        already gone from the tree raises the same error with
        ``reason: "already_reverted"`` rather than an empty commit, and a
        range containing a merge with ``reason: "merge_in_range"``.

        **A dirty tree is refused before git is asked to start** (``reason:
        "uncommitted_changes"``, with the tracked paths). Two reasons, and the
        second is the one that bit: git itself refuses to begin a revert that
        would overwrite a local modification, so there was nothing to roll
        back — but the rollback ran anyway, and a blanket ``reset --hard``
        deleted the user's own unsaved edit (the user guide documents editing
        ``parts/<id>.py`` in an external editor, so any other client's
        ``undo {scope: "mine"}`` could reach it). And even where git *would*
        have started, rolling a failure back cannot distinguish the revert's
        changes from the ones that were already there. From a clean tree it
        can: :meth:`_rollback_revert` aborts only a sequencer that actually
        started and resets only a tree the revert actually dirtied. Untracked
        files are not "uncommitted work" here — ``reset --hard`` never removes
        them, and the next snapshot adds them.
        """
        from .model import ConflictError

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

        dirty = self._dirty_paths(path)
        if dirty:
            raise ConflictError(
                f"cannot undo {commit[:8]}: the project has uncommitted "
                "changes; save or discard them and try again",
                {"commit": commit, "reason": "uncommitted_changes",
                 "paths": dirty, "blocked_by": []},
            )

        base = self._range_base(path, commit, since)
        if base is None:
            parents = self._run(
                path, "rev-list", "--parents", "-n", "1", commit
            ).stdout.split()[1:]
            # git refuses a merge revert without a mainline; 1st parent=target.
            mainline = ["-m", "1"] if len(parents) > 1 else []
            rev = [commit]
        else:
            # A range is inverted commit by commit, so a mainline cannot be
            # meaningful for all of them at once. git says so *after* it has
            # started, and it used to be discovered by finding no unmerged
            # paths in the wreckage and raising a bare HistoryError — the one
            # refusal here that did not say why in the shape the others do.
            # Ask first instead: it is one rev-list, it names the commit in the
            # way, and nothing has to be rolled back.
            merges = self._run(path, "rev-list", "--merges",
                               f"{base}..{commit}", check=False)
            found = merges.stdout.split() if merges.returncode == 0 else []
            if found:
                raise ConflictError(
                    f"cannot undo {commit[:8]}: the range back to "
                    f"{base[:8]} contains a merge, which cannot be inverted "
                    "commit by commit",
                    {"commit": commit, "reason": "merge_in_range",
                     "paths": [], "blocked_by": found},
                )
            mainline = []
            rev = [f"{base}..{commit}"]

        before = self.head(path)
        attempt = self._run(path, "revert", "--no-commit", *mainline, *rev,
                            check=False)
        if attempt.returncode != 0:
            paths = self._unmerged_paths(path)
            blocked_by = self._commits_touching(path, commit, paths)
            self._rollback_revert(path, before)
            if not paths:
                detail = (attempt.stderr or attempt.stdout).strip()
                raise HistoryError(f"git revert failed: {detail}")
            raise ConflictError(
                f"cannot undo {commit[:8]}: later changes overlap it",
                {"commit": commit, "reason": "overlapping_changes",
                 "paths": paths, "blocked_by": blocked_by},
            )
        staged = self._run(path, "diff", "--cached", "--quiet", check=False)
        if staged.returncode == 0:
            self._rollback_revert(path, before)
            raise ConflictError(
                f"commit {commit[:8]} has already been undone",
                {"commit": commit, "reason": "already_reverted",
                 "paths": [], "blocked_by": []},
            )
        # From here the inverse patch IS applied and staged, so "never a
        # partial apply" is now a statement about the way OUT: anything that
        # fails between this point and the returned id has to put the tree,
        # the index and HEAD back before the error leaves.
        try:
            self._run(path, "commit", "-m", with_client_trailer(
                message or f"revert {commit[:8]}"))
            return self._run(path, "rev-parse", "HEAD").stdout.strip()
        except Exception:
            self._rollback_revert(path, before)
            raise

    def _range_base(self, path: Path, commit: str,
                    since: str | None) -> str | None:
        """``since`` when it is a usable exclusive base for ``commit``, else
        None (revert the single commit).

        Three ways to be unusable, and all three degrade to the single-commit
        revert rather than raising: a malformed or unknown id, an id that is
        not an ancestor of ``commit`` (the range would then mean something
        nobody asked for), and ``since == commit`` (an empty range).
        """
        if not isinstance(since, str) or not _COMMIT_RE.match(since):
            return None
        if not self.has_commit(path, since):
            return None
        resolved = self._run(path, "rev-parse", since, check=False).stdout.strip()
        head = self._run(path, "rev-parse", commit, check=False).stdout.strip()
        if not resolved or resolved == head:
            return None
        ancestry = self._run(path, "merge-base", "--is-ancestor", since, commit,
                             check=False)
        return since if ancestry.returncode == 0 else None

    def _dirty_paths(self, path: Path) -> list[str]:
        """Tracked paths with staged or unstaged modifications.

        Untracked (``??``) and ignored (``!!``) entries are deliberately
        excluded: a revert neither stages nor deletes them, so they are not
        work a rollback could destroy, and treating them as dirty would block
        undo in any project a user has dropped a scratch file into.
        """
        result = self._run(path, "status", "--porcelain", check=False)
        if result.returncode != 0:
            return []
        paths = []
        for line in result.stdout.splitlines():
            if len(line) < 4 or line[:2] in ("??", "!!"):
                continue
            entry = line[3:].strip()
            # "R  old -> new": the new name is the one on disk.
            if " -> " in entry:
                entry = entry.split(" -> ", 1)[1]
            paths.append(entry.strip('"'))
        return sorted(set(paths))

    def _rollback_revert(self, path: Path, before: str | None = None) -> None:
        """Undo what *this* revert started, and nothing else.

        Both steps are conditional because both are destructive and neither is
        always needed: ``revert --abort`` only when git actually opened a
        sequencer (``REVERT_HEAD``), ``reset --hard`` only when something is
        still dirty afterwards. :meth:`revert` refuses a dirty tree up front,
        so anything dirty here is the revert's own doing.

        ``before`` is the commit the tree started on, and it matters for
        exactly one case: a failure *after* the revert commit already landed
        (a ``rev-parse`` that failed behind it). "Reset to HEAD" would then
        keep the very commit this rollback exists to remove, so the reset
        names the starting commit instead and HEAD moves back with the tree.
        """
        probe = self._run(path, "rev-parse", "--git-path", "REVERT_HEAD",
                          check=False)
        marker = probe.stdout.strip()
        if marker and (path / marker).is_file():
            self._run(path, "revert", "--abort", check=False)
        moved = before is not None and self.head(path) not in (None, before)
        if moved or self._dirty_paths(path):
            self._run(path, "reset", "--hard", before or "HEAD", check=False)

    def _unmerged_paths(self, path: Path) -> list[str]:
        """Paths git left conflicted — read BEFORE the abort clears them."""
        result = self._run(path, "diff", "--name-only", "--diff-filter=U",
                           check=False)
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _commits_touching(self, path: Path, commit: str,
                          paths: list[str]) -> list[str]:
        """Commits after ``commit`` (up to HEAD) that touched ``paths`` — the
        honest answer to "who is blocking this undo"."""
        args = ["log", "--format=%H", f"{commit}..HEAD"]
        if paths:
            args += ["--", *paths]
        result = self._run(path, *args, check=False)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines()
                if line.strip()]

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
        ref is unknown or malformed (never raises, never shells a bad name).

        Deliberately ambiguous — git's own precedence (tags before branches)
        applies. Only surfaces that genuinely accept *any* ref may use it
        (``project_history {ref}``, ``project_restore``); anything that means
        "a branch" must use :meth:`resolve_branch`.
        """
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return None
        if not valid_ref_name(ref) and not looks_like_commit(ref):
            return None
        result = self._run(path, "rev-parse", "--verify", "--quiet",
                           f"{ref}^{{commit}}", check=False)
        commit = result.stdout.strip()
        return commit if result.returncode == 0 and commit else None

    def resolve_branch(self, project_path: Path | str, name: str) -> str | None:
        """Commit id of the BRANCH ``name``, unambiguously.

        ``git rev-parse <name>`` searches ``refs/tags`` BEFORE ``refs/heads``,
        so a tag named like a branch answers for it — enough to make a merge
        of branch 'feat' merge the tag's commit instead. Every operation that
        means a branch resolves through here.
        """
        return self._resolve_in(project_path, "refs/heads", name)

    def resolve_tag(self, project_path: Path | str, name: str) -> str | None:
        """Commit id of the TAG ``name`` (the tagged commit for annotated
        tags), unambiguously — the mirror of :meth:`resolve_branch`."""
        return self._resolve_in(project_path, "refs/tags", name)

    def _resolve_in(self, project_path: Path | str, namespace: str,
                    name: str) -> str | None:
        path = Path(project_path)
        if not self.available() or not self._has_repo(path):
            return None
        if not valid_ref_name(name):
            return None
        result = self._run(path, "rev-parse", "--verify", "--quiet",
                           f"{namespace}/{name}^{{commit}}", check=False)
        commit = result.stdout.strip()
        return commit if result.returncode == 0 and commit else None

    @staticmethod
    def branch_ref(name: str) -> str:
        """``refs/heads/<name>`` — what to hand git when a bare branch name
        would be ambiguous with a tag."""
        return f"refs/heads/{name}"

    def branches(self, project_path: Path | str) -> list[dict]:
        """[{"name", "head", "ts", "message"}] for every local branch.

        ``refname:lstrip=2``, never ``refname:short``: the short form is the
        shortest UNAMBIGUOUS name, so a tag called 'feat' renames the branch
        'feat' to 'heads/feat' in this listing — and every name comparison
        against it (is it checked out? does it exist?) then misses.
        """
        return self._for_each_ref(
            project_path, "refs/heads",
            "%(refname:lstrip=2)%1f%(objectname)%1f%(committerdate:iso-strict)"
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
            "%(refname:lstrip=2)%1f%(objectname)%1f%(*objectname)"
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
    restart ``undo`` degrades to a step back through the git log — the latest
    snapshot under ``scope: "any"``, the caller's most recent one within
    ``FALLBACK_SEARCH`` commits under ``"mine"`` — and ``redo`` is empty.
    A fallback entry carries no ``undo_to`` (that fact lives only in the
    stack), so a fast-forward merge undone after a restart goes back through
    its first parent. Bounded to
    ``UNDO_LIMIT`` entries per stack, and keyed by ``store.lock_key(proj)``
    so each branch's working tree gets its own undo/redo history.

    **Authorship, not ownership (PRD-008, Decision 16).** Entries record the
    client that made the edit, and ``scope`` selects which of them a step may
    consume — but the stacks are deliberately NOT re-keyed per client. A human
    watching an agent edit and pressing Cmd+Z to take it back is this product's
    flagship loop; per-client stacks would leave that browser's stack empty.
    So ``scope="any"`` is the default and is byte-identical to the behavior
    that predates authorship, and ``scope="mine"`` is the opt-in that skips
    other clients' entries. When a ``"mine"`` step's entry is no longer the
    branch head, it becomes a ``git revert`` of exactly that commit instead of
    a whole-tree restore, which would silently take later work with it.
    """

    UNDO_LIMIT = 100
    #: How far back the post-restart ``scope: "mine"`` fallback searches the
    #: durable log for the caller's most recent commit. Bounded on purpose and
    #: at ``UNDO_LIMIT``'s order: it is one git call, and a client whose last
    #: edit is more than a hundred snapshots old is asking for history, not
    #: for undo.
    FALLBACK_SEARCH = 100
    #: Selectors for a step. "any" = today's behavior; "mine" = the caller's
    #: own most recent entry, skipping (never discarding) everyone else's.
    SCOPES = ("any", "mine")

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
            # The client id is read HERE, not at step time: on_snapshot runs
            # synchronously inside the mutating call, so this contextvar still
            # carries the identity that made the edit.
            entry = {"id": commit_id, "label": label,
                     "author": locks.current_client_id()}
            if undo_to:
                entry["undo_to"] = undo_to
            stack.append(entry)
            del stack[: -self.UNDO_LIMIT]
            self._redo.pop(key, None)

    def undo(self, proj: str, scope: str = "any") -> dict:
        return self._step(proj, redo=False, scope=scope)

    def redo(self, proj: str, scope: str = "any") -> dict:
        return self._step(proj, redo=True, scope=scope)

    def status(self, proj: str) -> dict:
        """Undoable/redoable labels, newest first (no git calls), plus how
        many of each belong to the calling client (``mine``) so a UI can
        label the button without guessing."""
        key = self._key(proj)
        caller = locks.current_client_id()
        with self._lock:
            undo = self._undo.get(key, [])
            redo = self._redo.get(key, [])
            return {
                "available": self.history.available(),
                "undo": [e["label"] for e in reversed(undo)],
                "redo": [e["label"] for e in reversed(redo)],
                "mine": {
                    "undo": sum(1 for e in undo if e.get("author") == caller),
                    "redo": sum(1 for e in redo if e.get("author") == caller),
                },
            }

    # ------------------------------------------------------------- internals

    def _from_log(self, path, scope: str, caller: str) -> dict | None:
        """The post-restart fallback: a stack entry rebuilt from git.

        A restart empties the in-memory stacks, and the durable history is all
        that is left. ``"any"`` steps back through the LATEST snapshot, which
        is the whole of that scope's question. ``"mine"`` asks a different one
        — "have *I* got anything to undo?" — and reading one commit answered it
        with "is the newest commit mine?", so an edit by anybody else on top of
        the caller's was enough to hear "nothing of yours to undo" about a
        commit still sitting reachable in history. It therefore searches back
        through :data:`FALLBACK_SEARCH` commits for the caller's most recent
        one; that bound is the same order as ``UNDO_LIMIT`` and is what
        ``log()`` itself will hand out, so this is one git call either way.

        A ``restore`` snapshot is refused in both scopes: undoing it would act
        as a redo, and repeated fallback undos would then oscillate between two
        states. Authorship comes from the commit trailer here — the one place
        it is read from git rather than from the stack.
        """
        limit = 1 if scope == "any" else self.FALLBACK_SEARCH
        for row in self.history.log(path, limit=limit):
            if row["message"].startswith("restore "):
                # For "any" this is the single candidate; for "mine" an
                # intervening restore is somebody's undo, not the caller's edit.
                continue
            if scope != "any" and row.get("author") != caller:
                continue
            return {"id": row["id"], "message_from_log": True,
                    "label": row["message"], "author": row.get("author")}
        return None

    def _step(self, proj: str, *, redo: bool, scope: str = "any") -> dict:
        from .model import ConflictError, ValidationError

        verb = "redo" if redo else "undo"
        if scope not in self.SCOPES:
            raise ValidationError(
                f"invalid scope {scope!r}: expected "
                + " or ".join(repr(s) for s in self.SCOPES)
            )
        if not self.history.available():
            raise ValidationError("undo/redo unavailable: git not found on PATH")
        # Turn-locking: undo/redo rewrites project files outside the store
        # choke point, so it must invoke the same write guard explicitly —
        # BEFORE resolving the path, because the guard is also what
        # re-materializes a branch working tree that went missing.
        if getattr(self.store, "write_guard", None):
            self.store.write_guard(proj)
        path = self.store.path_of(proj)
        key = self._key(proj)
        caller = locks.current_client_id()
        with self._lock:
            source = (self._redo if redo else self._undo).setdefault(key, [])
            while source and not self.history.has_commit(path, source[-1]["id"]):
                source.pop()  # history repo was pruned/replaced under us
            index = len(source) - 1
            if scope == "mine":
                # Skip, never discard, other clients' entries: their undo is
                # still theirs to take.
                index = next(
                    (i for i in range(len(source) - 1, -1, -1)
                     if source[i].get("author") == caller),
                    -1,
                )
            entry = source.pop(index) if index >= 0 else None
            if entry is None and not redo:
                found = self._from_log(path, scope, caller)
                if found is not None:
                    entry = found
            if entry is None:
                raise ConflictError(
                    f"nothing to {verb}" if scope == "any"
                    else f"nothing of yours to {verb}"
                )
            # A "mine" step whose commit is no longer the branch head cannot
            # restore a tree — that would take everyone's later work with it.
            # It reverts exactly its own commit instead (Decision 16 step 4).
            head = self.history.head(path)
            # The exclusive base of a RANGE revert — set only where ``undo_to``
            # is (a fast-forward merge), because everything between it and the
            # entry arrived in that one step. See ProjectHistory.revert.
            revert_since = None
            if redo:
                # entry["id"] captures the state to return to; going back is
                # "undo the redo": restore its parent again later.
                revert_target = entry.get("undone_by")
                target = None if revert_target else entry["id"]
            else:
                revert_target = entry.get("applied_by")
                if (revert_target is None and scope == "mine"
                        and entry["id"] != head):
                    revert_target = entry["id"]
                    # ``undo_to`` is what the unscoped path restores to; the
                    # scoped path cannot restore a whole tree (that would take
                    # later work with it), so it inverts exactly the range the
                    # entry brought in. Reverting only the tip, as this used
                    # to, leaves a fast-forward merge half undone.
                    revert_since = entry.get("undo_to")
                target = None
                if revert_target is None:
                    target = entry.get("undo_to") or self.history.parent_of(
                        path, entry["id"]
                    )
                    if target is None:
                        # The root snapshot has no parent: nothing before it to
                        # return to. Keep the stack entry — it wasn't consumed.
                        if not entry.get("message_from_log"):
                            source.insert(max(index, 0), entry)
                        raise ConflictError(f"nothing to {verb}")
            opposite = (self._undo if redo else self._redo).setdefault(key, [])
            self.history.in_restore = True
            try:
                if revert_target is not None:
                    commit = self.history.revert(
                        path, revert_target,
                        f"revert {revert_target[:8]} ({verb} by {caller})",
                        since=revert_since,
                    )
                    moved = {k: v for k, v in entry.items()
                             if k not in ("applied_by", "undone_by")}
                    moved["applied_by" if redo else "undone_by"] = commit
                else:
                    self.history.restore(path, target)
                    moved = entry
                opposite.append(moved)
                del opposite[: -self.UNDO_LIMIT]
                # Published while in_restore is still set, so the service's bus
                # hook does not stack a snapshot on our own restore/revert.
                self.bus.publish(
                    {"type": "project_changed", "project": proj,
                     "reason": verb}
                )
            except ConflictError:
                # Refused, not half-applied: the entry is still the caller's.
                source.insert(max(index, 0), entry)
                raise
            except HistoryError as exc:
                if revert_target is not None:
                    source.insert(max(index, 0), entry)
                raise ValidationError(f"{verb} failed: {exc}") from exc
            finally:
                self.history.in_restore = False
            counts = {
                "undo": len(self._undo.get(key, [])),
                "redo": len(self._redo.get(key, [])),
            }
        return {"label": entry["label"], **counts}
