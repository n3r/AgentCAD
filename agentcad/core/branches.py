"""Per-project branches, worktrees and immutable versions (tags).

Branches are native git refs in the project's existing ``.history`` repo, so
``git log``/``diff``/``clone`` on a project see true branches and tags. The
**default branch keeps the project directory** as its working tree — an
unbranched project is byte-identical on disk to a pre-branching one, and the
migration is a no-op — while every *other* branch gets a linked git worktree
at ``<project>/.history/trees/<branch>/``. (Not ``.history/worktrees/``: that
path is git's own per-worktree admin directory.)

Switching is a pointer update, not a checkout: ``BranchManager.resolve_path``
is installed as ``ProjectStore.branch_resolver``, so every authored-state read
and write already funnelling through ``ProjectStore._resolve`` lands in the
*calling client's* branch tree. Client identity is the ContextVar turn-locking
already stamps (``locks.current_client_id()``), so the browser, each MCP agent
and each chat lane can sit on different branches of one project concurrently.
Derived data does not move: ``.cache/`` stays canonical and content-addressed,
so a mesh built on one branch is a cache hit on every other (FR13).

Sidecar state lives under ``.history/agentcad/`` (inside GIT_DIR, therefore
never committed): ``config.json`` (the discovered default branch),
``checkouts.json`` (per-client branch + branch → worktree directory names) and
``tags.json`` (version referrers, for PRD-015).

All git goes through ``ProjectHistory._run`` so the hermetic environment, the
10 s timeout and the single git-executable probe are inherited.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from . import locks
from .history import HistoryError, valid_ref_name
from .model import ConflictError, NotFoundError, ValidationError
from .project import ProjectStore

# Branch names (FR1): lowercase, never starting with '-' (which git would read
# as an option), no dots (tags may have them, branches may not).
_BRANCH_RE = re.compile(r"^[a-z0-9][a-z0-9_/-]{0,63}$")

# Highest-precedence override of branch resolution, used by the merge
# validation pass (slice 3) to run ordinary service calls against a staged
# worktree. Explicit and short-lived: set only inside ``pinned()``.
pinned_tree_var: ContextVar[Path | None] = ContextVar(
    "agentcad_pinned_tree", default=None
)


def _resolved(path: Path) -> Path:
    """``resolve()`` that survives a missing path (and macOS's /var symlink)."""
    try:
        return path.resolve()
    except OSError:
        return path


def _inside(path: Path, root: Path) -> bool:
    root = _resolved(root)
    return root == _resolved(path) or root in _resolved(path).parents


def _registers(porcelain: str, target: Path, name: str) -> bool:
    """True when ``git worktree list --porcelain`` names ``target`` as the
    checkout of ``refs/heads/<name>``."""
    want = _resolved(target)
    for block in porcelain.split("\n\n"):
        path = branch = None
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):].strip()
            elif line.startswith("branch "):
                branch = line[len("branch "):].strip()
        if branch != f"refs/heads/{name}" or not path:
            continue
        if _resolved(Path(path)) == want:
            return True
    return False


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class BranchManager:
    """Branch/tag operations plus the per-client branch resolver.

    Constructing one installs ``store.branch_resolver`` — that is the whole
    integration: no service method changes, because the snapshot hook, the
    turn lock and the undo cursor all key off the store's resolved path.
    """

    def __init__(self, service) -> None:
        self.service = service
        self.store: ProjectStore = service.store
        self.history = service.history
        self._lock = threading.RLock()
        self._state: dict[str, dict] = {}
        self.store.branch_resolver = self.resolve_path

    # ------------------------------------------------------------ resolution

    def resolve_path(self, proj: str, canonical: Path) -> Path:
        """Working tree for ``proj`` in the calling context. Never raises —
        an unreadable sidecar or a missing worktree degrades to the canonical
        project directory, which is always a valid project.

        That degradation is for READS only. A write that fell back this way
        would land one branch's edits on the default branch, so the write path
        goes through :meth:`ensure_checkout` first."""
        pinned = pinned_tree_var.get()
        if pinned is not None:
            return Path(pinned)
        try:
            state = self._state_for(proj)
            branch = state["clients"].get(locks.current_client_id())
            dirname = state["trees"].get(branch) if branch else None
            if not dirname:
                return canonical  # default branch (or not materialized)
            tree = canonical / ".history" / "trees" / dirname
            return tree if (tree / "project.json").is_file() else canonical
        except Exception:  # noqa: BLE001 — resolution must never break a read
            return canonical

    def ensure_checkout(self, proj: str) -> Path:
        """Guarantee the calling client's branch tree exists — the WRITE-path
        counterpart of :meth:`resolve_path`, called from the store's write
        guard before any persistent mutation.

        A client checked out on branch X whose tree is missing or unreadable
        used to be redirected to the canonical directory silently, so its
        writes landed on the DEFAULT branch. Here the tree is re-materialized
        from the branch ref instead (the same content the branch always had),
        and if that is impossible the write is refused rather than misfiled.

        Cheap by construction: the fast path is the same ``project.json``
        stat ``resolve_path`` already does, with no git call at all.
        """
        pinned = pinned_tree_var.get()
        if pinned is not None:
            return Path(pinned)
        canonical = self.store.canonical_path_of(proj)
        state = self._state_for(proj)
        branch = state["clients"].get(locks.current_client_id())
        if not branch or branch == self.default_branch(proj):
            return canonical
        dirname = state["trees"].get(branch)
        if dirname:
            tree = canonical / ".history" / "trees" / dirname
            if (tree / "project.json").is_file():
                return tree
        with self._lock:
            try:
                dirname = self._materialize(canonical, state, branch)
            except (ValidationError, ConflictError, OSError) as exc:
                raise ConflictError(
                    f"the working tree for branch {branch!r} is missing and "
                    f"could not be restored ({exc}); switch branches or "
                    "re-create it before writing — this write would otherwise "
                    "land on another branch",
                    {"project": proj, "branch": branch},
                ) from exc
            state["trees"][branch] = dirname
            self._save(proj, state)
        tree = canonical / ".history" / "trees" / dirname
        if not (tree / "project.json").is_file():
            raise ConflictError(
                f"the working tree for branch {branch!r} is unreadable; "
                "switch branches or re-create it before writing",
                {"project": proj, "branch": branch},
            )
        return tree

    @contextmanager
    def pinned(self, proj: str, path: Path) -> Iterator[None]:
        """Force every store path in this context to ``path`` (all projects —
        the merge validation pass works on one project at a time)."""
        token = pinned_tree_var.set(Path(path))
        try:
            yield
        finally:
            pinned_tree_var.reset(token)

    # ---------------------------------------------------------------- state

    def _agentcad_dir(self, canonical: Path) -> Path:
        return canonical / ".history" / "agentcad"

    def _state_for(self, proj: str) -> dict:
        with self._lock:
            state = self._state.get(proj)
            if state is None:
                state = self._load(proj)
                self._state[proj] = state
            return state

    def _load(self, proj: str) -> dict:
        canonical = self.store.canonical_path_of(proj)
        base = self._agentcad_dir(canonical)
        checkouts = _read_json(base / "checkouts.json")
        state = {
            "default": _read_json(base / "config.json").get("default_branch"),
            "clients": {
                str(k): str(v)
                for k, v in (checkouts.get("clients") or {}).items()
            },
            "trees": {
                str(k): str(v)
                for k, v in (checkouts.get("trees") or {}).items()
            },
        }
        if state["clients"] or state["trees"]:
            # Drop entries naming branches that no longer exist (deleted by
            # this app or by a power user running raw git).
            known = {b["name"] for b in self.history.branches(canonical)}
            state["clients"] = {
                c: b for c, b in state["clients"].items() if b in known
            }
            state["trees"] = {
                b: d for b, d in state["trees"].items() if b in known
            }
        return state

    def _save(self, proj: str, state: dict) -> None:
        base = self._agentcad_dir(self.store.canonical_path_of(proj))
        ProjectStore._atomic_write(
            base / "checkouts.json",
            json.dumps({"clients": state["clients"], "trees": state["trees"]},
                       indent=2).encode(),
        )

    # -------------------------------------------------------------- plumbing

    def _run(self, path: Path, *args: str, check: bool = True):
        try:
            return self.history._run(path, *args, check=check)
        except HistoryError as exc:
            raise ValidationError(str(exc)) from exc

    def _ensure_history(self, proj: str) -> Path:
        """Canonical project path, guaranteed to have a repo with a commit.

        Decision 8's migration: the repo (and the first snapshot for a project
        that was never mutated) is created lazily on the first branching call;
        whatever linear history exists simply *is* the default branch's.
        """
        if not self.history.available():
            raise ValidationError("branching unavailable: git not found on PATH")
        canonical = self.store.canonical_path_of(proj)
        self.history._ensure_repo(canonical)
        if self.history.head(canonical) is None:
            self.history.snapshot(canonical, "initial snapshot")
            if self.history.head(canonical) is None:
                raise ValidationError(
                    f"could not initialize history for project {proj!r}"
                )
        return canonical

    @staticmethod
    def _validate_branch_name(name: object) -> str:
        if not isinstance(name, str) or not _BRANCH_RE.match(name) \
                or not valid_ref_name(name):
            raise ValidationError(
                f"invalid branch name {name!r}",
                {"pattern": _BRANCH_RE.pattern,
                 "note": "lowercase; no '..', trailing '/', '.lock' or '@{'"},
            )
        return name

    @staticmethod
    def _validate_tag_name(name: object) -> str:
        # Tags additionally allow dots, so 'v1.2' is a legal version name.
        if not isinstance(name, str) or not valid_ref_name(name):
            raise ValidationError(
                f"invalid version name {name!r}",
                {"pattern": r"^[a-z0-9][a-z0-9._/-]{0,63}$"},
            )
        return name

    def _branch_names(self, canonical: Path) -> set[str]:
        return {b["name"] for b in self.history.branches(canonical)}

    def _checkpoint(self, tree: Path, message: str, what: str) -> None:
        """Snapshot ``tree``, and REFUSE when a dirty tree could not be
        committed.

        ``ProjectHistory.snapshot`` is exception-free by contract and returns
        None for two very different outcomes: "nothing to commit" (a clean
        tree — fine) and "git failed" (not fine). The difference is whether
        the tree is still dirty afterwards. Ignoring it let a branch switch,
        a version tag or a delete walk away from uncommitted work.
        """
        self.history.snapshot(tree, message)
        result = self._run(tree, "status", "--porcelain", check=False)
        if result.returncode != 0 or result.stdout.strip():
            raise ConflictError(
                f"could not snapshot the working tree before {what}; its "
                "uncommitted changes would be lost",
                {"tree": str(tree),
                 "status": result.stdout.strip()[:2000]},
            )

    # -------------------------------------------------------------- branches

    def default_branch(self, proj: str) -> str:
        """The branch whose working tree is the project directory. Discovered
        from the repo (never assumed) and pinned in config.json, so a later
        change to git's ``init.defaultBranch`` cannot re-point old projects."""
        state = self._state_for(proj)
        if state["default"]:
            return state["default"]
        canonical = self.store.canonical_path_of(proj)
        if not self.history.available() or not self.history._has_repo(canonical):
            return "master"  # not yet a repo: nothing to persist
        result = self._run(canonical, "symbolic-ref", "--short", "HEAD",
                           check=False)
        name = result.stdout.strip() if result.returncode == 0 else ""
        name = name or "master"
        state["default"] = name
        ProjectStore._atomic_write(
            self._agentcad_dir(canonical) / "config.json",
            json.dumps({"default_branch": name}, indent=2).encode(),
        )
        return name

    def current(self, proj: str, client: str | None = None) -> str:
        client = client or locks.current_client_id()
        branch = self._state_for(proj)["clients"].get(client)
        return branch or self.default_branch(proj)

    def list(self, proj: str) -> dict:
        canonical = self._ensure_history(proj)
        state = self._state_for(proj)
        default = self.default_branch(proj)
        current = self.current(proj)
        branches = []
        for entry in self.history.branches(canonical):
            name = entry["name"]
            branches.append({
                **entry,
                "is_default": name == default,
                "is_current": name == current,
                "checked_out_by": sorted(
                    c for c, b in state["clients"].items() if b == name
                ),
            })
        return {
            "branches": branches,
            "current": current,
            "default": default,
            "you": locks.current_client_id(),
        }

    def create(self, proj: str, name: str, from_ref: str | None = None) -> dict:
        """Create a branch and materialize its working tree. Does NOT switch
        the caller (branch_switch does that, so an agent can prepare a branch
        for someone else)."""
        self._validate_branch_name(name)
        canonical = self._ensure_history(proj)
        with self._lock:
            state = self._state_for(proj)
            if name in self._branch_names(canonical):
                raise ConflictError(f"branch {name!r} already exists")
            source = from_ref or self.current(proj)
            # An explicit 'from' documents itself as "branch, tag or commit"
            # and keeps git's precedence; the DEFAULT is the caller's branch,
            # which must resolve as a branch even if a tag shadows its name.
            commit = (
                self.history.resolve_ref(canonical, source)
                if from_ref
                else self.history.resolve_branch(canonical, source)
            )
            if commit is None:
                raise NotFoundError(f"unknown branch, tag or commit {source!r}")
            result = self._run(canonical, "branch", name, commit, check=False)
            if result.returncode != 0:
                # e.g. 'feat' when 'feat/x' exists — git's own ref rules.
                raise ValidationError(
                    f"git refused branch {name!r}: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
            state["trees"][name] = self._materialize(canonical, state, name)
            self._save(proj, state)
        payload = self.list(proj)
        payload["created"] = name
        return payload

    def switch(self, proj: str, name: str) -> str:
        """Point the calling client at ``name``. O(1): the tree already
        exists. Snapshots the tree being left first, so a branch boundary is
        always a clean, restorable state (FR3)."""
        canonical = self._ensure_history(proj)
        if name not in self._branch_names(canonical):
            raise NotFoundError(f"branch {name!r} not found")
        client = locks.current_client_id()
        with self._lock:
            state = self._state_for(proj)
            self._checkpoint(
                self.store.path_of(proj),
                f"checkpoint before switch to {name}",
                f"switching to {name!r}",
            )
            if name == self.default_branch(proj):
                state["trees"].pop(name, None)
            else:
                state["trees"][name] = self._materialize(canonical, state, name)
            state["clients"][client] = name
            self._save(proj, state)
        self.service.bus.publish(
            {"type": "branch_changed", "project": proj, "client": client,
             "branch": name}
        )
        return name

    def delete(self, proj: str, name: str) -> dict:
        # Whitelist before anything else: the name reaches here from a REST
        # path segment as well as from a tool argument.
        self._validate_branch_name(name)
        canonical = self._ensure_history(proj)
        if name == self.default_branch(proj):
            raise ValidationError(f"cannot delete the default branch {name!r}")
        with self._lock:
            state = self._state_for(proj)
            holders = sorted(
                c for c, b in state["clients"].items() if b == name
            )
            if holders:
                raise ValidationError(
                    f"branch {name!r} is checked out by "
                    f"{', '.join(holders)}; switch away first",
                    {"checked_out_by": holders},
                )
            if name not in self._branch_names(canonical):
                raise NotFoundError(f"branch {name!r} not found")
            dirname = state["trees"].get(name)
            if dirname:
                tree = canonical / ".history" / "trees" / dirname
                if (tree / "project.json").is_file():
                    # '--force' below discards whatever is in the tree, so the
                    # tree has to be committed FIRST. A dirty tree that will
                    # not commit is a refusal, not a silent data loss.
                    try:
                        self._checkpoint(
                            tree, f"checkpoint before deleting {name}",
                            f"deleting branch {name!r}",
                        )
                    except ConflictError as exc:
                        raise ValidationError(
                            f"branch {name!r} has uncommitted changes that "
                            "could not be snapshotted; it will not be deleted",
                            {"branch": name,
                             "status": (exc.details or {}).get("status")},
                        ) from exc
                self._run(canonical, "worktree", "remove", "--force",
                          str(tree), check=False)
            self._run(canonical, "worktree", "prune", check=False)
            result = self._run(canonical, "branch", "-D", name, check=False)
            if result.returncode != 0:
                raise ConflictError(
                    f"could not delete branch {name!r}: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
            state["trees"].pop(name, None)
            self._save(proj, state)
        payload = self.list(proj)
        payload["deleted"] = name
        return payload

    def tree_of(self, proj: str, branch: str) -> Path:
        """Working tree of ``branch`` (materializing it if needed)."""
        canonical = self._ensure_history(proj)
        if branch == self.default_branch(proj):
            return canonical
        if branch not in self._branch_names(canonical):
            raise NotFoundError(f"branch {branch!r} not found")
        with self._lock:
            state = self._state_for(proj)
            dirname = self._materialize(canonical, state, branch)
            state["trees"][branch] = dirname
            self._save(proj, state)
        return canonical / ".history" / "trees" / dirname

    # ------------------------------------------------------------- worktrees

    def _materialize(self, canonical: Path, state: dict, name: str) -> str:
        """Ensure a linked worktree exists for ``name``; return its directory
        name under ``.history/trees/``.

        A directory that merely *looks* like a checkout is not adopted: a
        copied project brings its predecessor's trees along, whose ``.git``
        files still point at the ORIGINAL project's repo (so commits would
        land there), and a tree that lost its ``.git`` would be ``git init``-ed
        into an invisible throwaway repo. Either way the tree is discarded and
        re-materialized from the branch ref.
        """
        dirname = state["trees"].get(name) or self._tree_dirname(state, name)
        target = canonical / ".history" / "trees" / dirname
        if (target / "project.json").is_file() \
                and self._is_linked_worktree(canonical, target, name):
            return dirname
        # A tree deleted out from under git stays registered and 'prunable',
        # and blocks re-adding the same path — prune before every add.
        self._run(canonical, "worktree", "prune", check=False)
        self._drop_foreign_registrations(canonical)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(canonical, "worktree", "add", str(target), name,
                           check=False)
        if result.returncode != 0:
            raise ValidationError(
                f"could not create a working tree for branch {name!r}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return dirname

    def _is_linked_worktree(self, canonical: Path, target: Path,
                            name: str) -> bool:
        """True when ``target`` is THIS repo's registered linked worktree for
        branch ``name`` — not a copy, not a bare directory."""
        dotgit = target / ".git"
        if not dotgit.is_file():
            return False
        try:
            text = dotgit.read_text(encoding="utf-8")
        except OSError:
            return False
        if "gitdir:" not in text:
            return False
        admin = Path(text.split("gitdir:", 1)[1].strip())
        if not _inside(admin, canonical / ".history" / "worktrees"):
            return False  # points at another project's repo
        listing = self._run(canonical, "worktree", "list", "--porcelain",
                            check=False)
        if listing.returncode != 0:
            return False
        return _registers(listing.stdout, target, name)

    @staticmethod
    def _drop_foreign_registrations(canonical: Path) -> None:
        """Forget worktree registrations pointing outside this project.

        A copied project inherits the original's ``.history/worktrees/*``
        admin directories; git still considers those branches checked out (at
        paths in the *other* project, which exist, so ``prune`` keeps them)
        and refuses to add a worktree here. Registrations inside this project
        — including the staged merge worktrees — are left alone.
        """
        admin_root = canonical / ".history" / "worktrees"
        if not admin_root.is_dir():
            return
        for admin in sorted(admin_root.iterdir()):
            try:
                recorded = (admin / "gitdir").read_text(encoding="utf-8")
            except OSError:
                continue
            if not _inside(Path(recorded.strip()), canonical):
                shutil.rmtree(admin, ignore_errors=True)

    @staticmethod
    def _tree_dirname(state: dict, name: str) -> str:
        """Directory name for a branch: '/' is not a directory separator here,
        so 'feat/x' becomes 'feat-x', disambiguated on collision."""
        base = name.replace("/", "-") or "branch"
        taken = set(state["trees"].values())
        if base not in taken:
            return base
        for suffix in range(2, 100):
            candidate = f"{base}-{suffix}"
            if candidate not in taken:
                return candidate
        raise ConflictError(f"too many branch directories named like {base!r}")

    # ------------------------------------------------------------------ tags

    def tag(self, proj: str, name: str, message: str | None = None) -> dict:
        """Create an immutable named version at the caller's branch head.

        Annotated (so it carries author, date and message). Moving or
        re-pointing a version is refused — there is deliberately no delete
        tool, which is what makes FR5 hold.
        """
        self._validate_tag_name(name)
        canonical = self._ensure_history(proj)
        if name in {t["name"] for t in self.history.tags(canonical)}:
            raise ConflictError(
                f"version {name!r} already exists; versions are immutable"
            )
        tree = self.store.path_of(proj)
        # A failed snapshot here would tag a STALE head — a version that does
        # not describe the state the caller was looking at (FR5 is only worth
        # anything if the name is pinned to the right commit).
        self._checkpoint(tree, f"checkpoint before version {name}",
                         f"tagging version {name!r}")
        result = self._run(tree, "tag", "-a", name, "-m", message or name,
                           check=False)
        if result.returncode != 0:
            raise ValidationError(
                f"could not create version {name!r}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        records = _read_json(self._agentcad_dir(canonical) / "tags.json")
        records[name] = {"referrers": []}
        ProjectStore._atomic_write(
            self._agentcad_dir(canonical) / "tags.json",
            json.dumps(records, indent=2).encode(),
        )
        return {
            "tag": name,
            "commit": self.history.resolve_tag(tree, name),
            "versions": self.versions(proj),
        }

    def versions(self, proj: str) -> list[dict]:
        """Tags newest-first: {name, commit, ts, author, message, referrers}.

        Tag timestamps have one-second resolution, so ties break on the name
        (descending) to keep the order deterministic.
        """
        canonical = self.store.canonical_path_of(proj)
        records = _read_json(self._agentcad_dir(canonical) / "tags.json")
        rows = self.history.tags(canonical)
        rows.sort(key=lambda r: (r["ts"], r["name"]), reverse=True)
        for row in rows:
            entry = records.get(row["name"]) or {}
            row["referrers"] = list(entry.get("referrers") or [])
        return rows
