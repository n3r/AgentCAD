"""Server half of git sync (PRD-005 FR8/FR9): repo preparation + materialization.

This module owns everything the hosted copy of a project's history repo needs
*before* and *after* a push, and nothing about HTTP — the smart-HTTP proxy
lives in ``server/routes_sync.py``.

The layout it works on is ``core/history.py``'s: every project directory holds
a **non-bare** repo whose ``GIT_DIR`` is ``<project>/.history`` and whose work
tree is the project directory itself. Three consequences drove every decision
here (all measured in the PRD-005 spike, `docs/superpowers/specs/
2026-08-24-multi-tenant-cloud-spike.md` §A2–§A5):

1. **``receive.denyCurrentBranch=updateInstead`` cannot work here.**
   ``receive-pack`` derives the main work tree by stripping a trailing
   ``/.git`` from the GIT_DIR and *ignores* ``core.worktree``; our GIT_DIR
   ends in ``/.history``, so it resolves the work tree to the GIT_DIR itself,
   finds none of the tracked files there, and rejects every push as "Working
   directory has unstaged changes" — against a provably clean tree. So the
   setting is ``ignore`` and this module materializes explicitly.
2. **With ``ignore``, refs advance and the work tree does not.**
   :func:`materialize` is therefore an always-run step after a successful
   receive, not an optimization. It is cheap: 0.02–0.14 s measured on a
   305-file/19 MB project.
3. **git's own knobs do not implement FR9.** ``receive.denyNonFastForwards``
   and ``receive.denyDeletes`` are ``refs/heads/``-only, so a client can
   rewrite or delete a PRD-015 release *tag* with both set. The
   :data:`PRE_RECEIVE_HOOK` closes all three holes with humane messages.

``checkout -f`` is the right verb for materialization for two reasons the
spike measured: it does **not** delete untracked derived data (``.cache/``,
``exports/`` survive — FR8's "derived data never syncs"), and it *does*
clobber uncommitted **tracked** edits, which is why it runs inside
:func:`project_write_scope` (the project's write guard — turn lock — plus a
per-tree lock) and refuses a dirty tree by default.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

#: Hard timeout for the short plumbing calls this module makes. Deliberately
#: larger than ``history._GIT_TIMEOUT_S`` (10 s): a cold ``checkout -f`` over a
#: large project after a push that changed every file is the one call here that
#: can legitimately take seconds.
GIT_TIMEOUT_S = 120.0

#: Bumped whenever :data:`PRE_RECEIVE_HOOK` changes. The marker is written into
#: the hook, so :func:`prepare_repo` rewrites an out-of-date hook in place
#: instead of leaving an old rule set running on an upgraded server.
HOOK_VERSION = 1
HOOK_MARKER = f"# agentcad pre-receive hook v{HOOK_VERSION}"

#: The FR9 contract, as three rules and in this order:
#:
#: (a) **any ref delete** — branches *and* tags — is refused;
#: (b) a **branch** update whose old value is not an ancestor of the new one
#:     (a force-push / a diverged history) is refused;
#: (c) a **tag** update with a non-zero old value (a tag rewrite) is refused.
#:
#: Plus a catch-all: only ``refs/heads/*`` and ``refs/tags/*`` may be updated
#: at all, so nothing can push into ``refs/agentcad/*`` or a stray namespace.
#:
#: ``pre-receive`` is **all-or-nothing**: one bad ref rejects the whole push
#: (the spike proved it — a good branch in the same push is rejected too).
#: That is the right semantics for "surface divergence, never overwrite"; an
#: ``update`` hook would accept the good refs and is deliberately not used.
#:
#: The null-oid test is ``case "$x" in *[!0]*)`` — "consists only of zeros" —
#: rather than a literal 40-zero string, so the hook is correct in a sha256
#: repository too. ``/bin/sh``, not bash: Debian's ``sh`` is dash and every
#: construct here is POSIX.
PRE_RECEIVE_HOOK = f"""#!/bin/sh
{HOOK_MARKER}
# Managed by agentcad/core/sync_server.py — edits are overwritten on upgrade.
#
# FR9: divergence is surfaced, never overwritten.
#   1. no ref deletes (branch or tag)
#   2. no non-fast-forward branch updates
#   3. no tag rewrites (PRD-015 release tags are immutable)
# New branches and new tags, and fast-forward branch updates, pass.
rc=0
while read -r old new ref
do
    oldz=no
    newz=no
    case "$old" in *[!0]*) ;; *) oldz=yes ;; esac
    case "$new" in *[!0]*) ;; *) newz=yes ;; esac

    if [ "$newz" = yes ]; then
        echo "agentcad: refusing to delete $ref - deletes are refused on the hosted copy" >&2
        rc=1
        continue
    fi

    case "$ref" in
    refs/heads/*)
        if [ "$oldz" = no ] && ! git merge-base --is-ancestor "$old" "$new"; then
            echo "agentcad: $ref diverged - pull and merge, never force" >&2
            rc=1
        fi
        ;;
    refs/tags/*)
        if [ "$oldz" = no ]; then
            echo "agentcad: $ref already exists - tags are immutable" >&2
            rc=1
        fi
        ;;
    *)
        echo "agentcad: refusing to update $ref - only refs/heads/* and refs/tags/* sync" >&2
        rc=1
        ;;
    esac
done
exit $rc
"""

#: Config the hosted copy must carry. ``receive.denyCurrentBranch=ignore`` is
#: load-bearing (see the module docstring); ``http.receivepack=true`` is what
#: lets ``git http-backend`` advertise and run receive-pack at all; the two
#: ``deny*`` knobs are belt-and-braces behind the hook, which is what actually
#: implements FR9.
REPO_CONFIG: tuple[tuple[str, str], ...] = (
    ("receive.denyCurrentBranch", "ignore"),
    ("http.receivepack", "true"),
    ("receive.denyNonFastForwards", "true"),
    ("receive.denyDeletes", "true"),
)


class SyncError(RuntimeError):
    """A sync-side git operation failed (missing repo, missing git, git error)."""


# --------------------------------------------------------------------- git

_git_path: str | None = None
_backend_path: str | None = None
_probed = False


def git_executable() -> str:
    """``git`` on PATH. Raises :class:`SyncError` when there is none."""
    global _git_path
    if _git_path is None:
        found = shutil.which("git")
        if found is None:
            raise SyncError("git is not installed on this server")
        _git_path = found
    return _git_path


def http_backend() -> str:
    """Absolute path to ``git-http-backend``.

    Probed once and cached. It lives in ``git --exec-path``, not on ``PATH``:
    macOS puts it under Xcode, Debian under ``/usr/lib/git-core``,
    git-for-windows under ``mingw64/libexec/git-core`` with an ``.exe``
    suffix. A missing backend would otherwise present as "sync just 500s", so
    :func:`probe` exists to say it out loud at startup.
    """
    global _backend_path, _probed
    if _backend_path is None:
        try:
            result = subprocess.run(
                [git_executable(), "--exec-path"],
                capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SyncError(f"git --exec-path failed: {exc}") from exc
        exec_path = (result.stdout or "").strip()
        name = "git-http-backend" + (".exe" if os.name == "nt" else "")
        candidate = Path(exec_path) / name if exec_path else None
        if candidate is None or not candidate.is_file():
            raise SyncError(
                f"{name} was not found in git --exec-path ({exec_path!r}); "
                "git sync needs the smart-HTTP backend that ships with git"
            )
        _backend_path = str(candidate)
        _probed = True
    return _backend_path


def probe() -> dict:
    """``{"git": …, "http_backend": …}`` or ``{"error": …}`` — never raises.

    For a startup log line and for CI to assert on the Linux and Windows legs.
    """
    try:
        return {"git": git_executable(), "http_backend": http_backend()}
    except SyncError as exc:
        return {"error": str(exc)}


def git_env(git_dir: Path) -> dict[str, str]:
    """The hermetic environment ``core/history.py`` uses, to the letter.

    ``HOME``/``XDG_CONFIG_HOME`` point *into* the GIT_DIR so a server
    operator's ``~/.gitconfig`` (hooks, ``core.hooksPath``, gpg signing,
    ``init.defaultBranch``) cannot change what the hosted repo does. That
    matters more here than in ``history``: an operator ``core.hooksPath``
    would silently disable the FR9 hook.
    """
    return {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(git_dir),
        "XDG_CONFIG_HOME": str(git_dir / "xdg"),
    }


def history_dir(project_path: Path | str) -> Path:
    """The GIT_DIR of a project's history repo (``<project>/.history``)."""
    return Path(project_path) / ".history"


def has_repo(project_path: Path | str) -> bool:
    return (history_dir(project_path) / "HEAD").is_file()


def _run(project_path: Path, *args: str, check: bool = True
         ) -> subprocess.CompletedProcess:
    """``git --git-dir=<.history> --work-tree=<project> …`` — history's shape.

    Not ``ProjectHistory._run`` itself: that one has a 10 s timeout tuned for
    a snapshot and is the *undo* driver. Same environment, same explicit
    ``--work-tree`` (a hook's inherited env is not enough — spike §A2/§A3).
    """
    git_dir = history_dir(project_path)
    cmd = [git_executable(), "--git-dir", str(git_dir),
           "--work-tree", str(project_path), *args]
    try:
        result = subprocess.run(
            cmd, cwd=str(project_path), env=git_env(git_dir),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError(f"git {args[0]}: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SyncError(f"git {args[0]} failed: {detail}")
    return result


# ------------------------------------------------------------ preparation

#: ``str(git_dir) -> (config mtime_ns, hook mtime_ns)``. Preparation is a
#: per-request precondition (the receive-pack *advertisement* already needs
#: ``http.receivepack``), so the common case must not pay for four
#: ``git config`` calls. Keyed on mtimes so an out-of-band edit — an operator
#: with a shell, ``docker compose exec`` — is picked up on the next request
#: rather than cached away for the life of the process.
_prepared: dict[str, tuple[int, int]] = {}
_prepare_lock = threading.Lock()


def _stamp(git_dir: Path) -> tuple[int, int]:
    def mtime(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1
    return mtime(git_dir / "config"), mtime(git_dir / "hooks" / "pre-receive")


def prepare_repo(project_path: Path | str, *, force: bool = False) -> dict:
    """Make a project's history repo safe to serve over smart HTTP.

    Idempotent, and cheap on the second call. Sets :data:`REPO_CONFIG` and
    installs :data:`PRE_RECEIVE_HOOK`, rewriting the hook whenever its
    :data:`HOOK_MARKER` version differs from the one on disk — so a server
    upgrade that tightens a rule actually tightens it, and a hand-edited hook
    is replaced rather than trusted.

    Returns ``{"config": [keys set], "hook": "installed"|"current"}``.
    """
    project_path = Path(project_path)
    git_dir = history_dir(project_path)
    if not has_repo(project_path):
        raise SyncError(
            f"{project_path.name!r} has no history repo yet "
            "(nothing has been committed in this project)"
        )
    key = str(git_dir)
    stamp = _stamp(git_dir)
    if not force and _prepared.get(key) == stamp:
        return {"config": [], "hook": "current"}

    with _prepare_lock:
        existing = _config_map(project_path)
        written = []
        for name, value in REPO_CONFIG:
            # `git config --list` lower-cases the variable name (the section
            # and key are case-insensitive to git); compare in its spelling,
            # not ours, or every request rewrites the config.
            if existing.get(name.lower()) != value:
                _run(project_path, "config", name, value)
                written.append(name)
        hook = _install_hook(git_dir, force=force)
        _prepared[key] = _stamp(git_dir)
    return {"config": written, "hook": hook}


def _config_map(project_path: Path) -> dict[str, str]:
    """The repo's local config as a dict (one subprocess, not four)."""
    result = _run(project_path, "config", "--local", "--list", "-z",
                  check=False)
    out: dict[str, str] = {}
    if result.returncode != 0:
        return out
    for record in (result.stdout or "").split("\0"):
        if not record:
            continue
        name, _, value = record.partition("\n")
        out[name.strip().lower()] = value
    return out


def _install_hook(git_dir: Path, *, force: bool = False) -> str:
    hooks = git_dir / "hooks"
    path = hooks / "pre-receive"
    try:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        current = ""
    if not force and current == PRE_RECEIVE_HOOK:
        # Executable bit too: a hook without it is silently not run, which is
        # the worst possible failure mode for this particular file.
        if os.access(path, os.X_OK):
            return "current"
    hooks.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"pre-receive.{os.getpid()}.tmp")
    tmp.write_text(PRE_RECEIVE_HOOK, encoding="utf-8")
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return "installed"


# --------------------------------------------------------- materialization

#: One lock per project tree. The ref transaction is git's own serialization
#: point (two racing pushes to one branch: the loser is told, correctly), but
#: *materialization* happens outside it, so two pushes that both win on
#: different branches would otherwise race the checkout.
_tree_locks: dict[str, threading.Lock] = {}
_tree_locks_guard = threading.Lock()


def tree_lock(project_path: Path | str) -> threading.Lock:
    key = str(Path(project_path))
    with _tree_locks_guard:
        lock = _tree_locks.get(key)
        if lock is None:
            lock = _tree_locks[key] = threading.Lock()
        return lock


@contextlib.contextmanager
def project_write_scope(service, proj: str, project_path: Path | str):
    """The write context :func:`materialize` runs inside.

    The smallest possible reach into the service: the per-tree lock above,
    plus ``store.write_guard`` — the seam PRD-008 installed, which is
    ``turnlock.check(proj, current_client_id())`` and raises ``ConflictError``
    when *another* client holds the project's turn. A push that lands while a
    human holds the turn therefore updates the refs and leaves the work tree
    alone rather than clobbering their session; the next materialization (or
    the next open) catches the tree up.

    ``locks.write_scope(None)`` is entered for the same reason every
    whole-project write does it: a claim is a *part* claim, and a push is not
    about one part.
    """
    from . import locks

    with tree_lock(project_path):
        with locks.write_scope(None):
            guard = getattr(getattr(service, "store", None), "write_guard", None)
            if guard is not None:
                guard(proj)
            yield


def default_branch(project_path: Path | str) -> str | None:
    """The branch HEAD points at, or ``None`` for a detached/unreadable HEAD.

    ``symbolic-ref`` and not ``rev-parse --abbrev-ref``: an unborn HEAD (a
    repo whose first push has not landed) still answers here, which is
    exactly the case :func:`materialize` must handle.
    """
    result = _run(Path(project_path), "symbolic-ref", "--short", "HEAD",
                  check=False)
    branch = (result.stdout or "").strip()
    return branch or None


def pending_edits(project_path: Path | str) -> list[str]:
    """Tracked files modified in the work tree but not committed.

    Call this **before** a push lands: afterwards the work tree is stale by
    construction (refs advanced, files did not) and ``status`` cannot tell the
    two apart. AgentCAD commits on every ``project_changed``, so this is
    normally empty — but the window is real, and ``checkout -f`` would eat
    what is in it.
    """
    result = _run(Path(project_path), "status", "--porcelain", "-uno",
                  check=False)
    if result.returncode != 0:
        return []
    return [line[3:] for line in (result.stdout or "").splitlines() if line]


def materialize(project_path: Path | str, write_context=None, *,
                dirty: list[str] | None = None, force: bool = False) -> dict:
    """Check the default branch out into the project directory.

    *write_context* is a zero-argument callable returning a context manager —
    :func:`project_write_scope` bound to the service and project by the route.
    ``None`` means "no write context", which is what a library caller and the
    unit tests use.

    *dirty* is the :func:`pending_edits` list captured **before** the push. A
    non-empty one means a human had uncommitted tracked edits when the push
    arrived, and ``checkout -f`` would silently destroy them, so the checkout
    is skipped (``{"materialized": False, "reason": "uncommitted_edits"}``)
    unless *force*. The refs are already updated either way — a push is never
    failed for this, because the bytes are safely on the server and rejecting
    it would be the lie.

    Returns ``{"materialized": bool, "branch": …, "changed": int, "reason": …}``
    where ``changed`` is the number of tracked files the checkout brought into
    line with HEAD (0 when the work tree was already current).
    """
    project_path = Path(project_path)
    if not has_repo(project_path):
        raise SyncError(f"{project_path} has no history repo")

    context = write_context() if write_context is not None \
        else contextlib.nullcontext()
    with context:
        if dirty and not force:
            return {"materialized": False, "reason": "uncommitted_edits",
                    "branch": default_branch(project_path), "changed": 0,
                    "pending": list(dirty)}
        branch = default_branch(project_path)
        if branch is None:
            return {"materialized": False, "reason": "detached_head",
                    "branch": None, "changed": 0}
        if _run(project_path, "rev-parse", "--verify", "--quiet",
                f"refs/heads/{branch}", check=False).returncode != 0:
            # An unborn branch: the repo has been created but nothing has been
            # pushed to it yet. Nothing to check out, and not an error.
            return {"materialized": False, "reason": "unborn_branch",
                    "branch": branch, "changed": 0}
        changed = _run(project_path, "diff", "--name-only", "HEAD", "--",
                       check=False)
        count = len([line for line in (changed.stdout or "").splitlines()
                     if line])
        _run(project_path, "checkout", "-f", branch, "--")
        return {"materialized": True, "reason": None, "branch": branch,
                "changed": count}
