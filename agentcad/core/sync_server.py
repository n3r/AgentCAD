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
import shlex
import shutil
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path

#: Hard timeout for the short plumbing calls this module makes. Deliberately
#: larger than ``history._GIT_TIMEOUT_S`` (10 s): a cold ``checkout -f`` over a
#: large project after a push that changed every file is the one call here that
#: can legitimately take seconds.
GIT_TIMEOUT_S = 120.0

#: Bumped whenever :data:`PRE_RECEIVE_HOOK` changes. The marker is written into
#: the hook, so :func:`prepare_repo` rewrites an out-of-date hook in place
#: instead of leaving an old rule set running on an upgraded server.
HOOK_VERSION = 3
HOOK_MARKER = f"# agentcad pre-receive hook v{HOOK_VERSION}"

#: The name of the fold-check sidecar the hook delegates to, installed beside
#: ``pre-receive`` in the server-owned hooks dir. See :data:`CHECKPATHS_SCRIPT`.
CHECKPATHS_NAME = "checkpaths.py"

#: The interpreter the pre-receive hook runs the fold sidecar with, baked in at
#: import time. ``sys.executable`` is the server's own Python — the one process
#: that is guaranteed present — quoted for the ``/bin/sh`` the hook is written
#: in; the hook falls back to ``python3``/``python`` on PATH if this path has
#: gone stale (a moved venv), and to the in-process :func:`materialize` backstop
#: if none of them run.
_HOOK_PYTHON = shlex.quote(sys.executable or "python3")

#: The exact path-component names that address a project's git internals. The
#: GIT_DIR of a project's history repo is ``<project>/.history`` *inside* the
#: work tree, so a pushed/pulled/merged tree carrying either of these — at any
#: depth — is written straight into the served repo's live git internals when
#: ``checkout -f`` materializes it (a ``post-receive`` hook, a ``config`` with
#: ``core.hooksPath``/``core.fsmonitor``/a filter driver), and the planted code
#: then runs as the unconfined server (or, via clone/pull, the workstation)
#: user. ``git``'s own ``verify_path`` guards only the literal name ``.git``
#: (and even that only when ``core.protectHFS``/``NTFS`` do not object), never
#: ``.history``. Everything is compared AFTER :func:`_fold_component`, never
#: literally, so ``.gitignore``/``.gitattributes`` (which do not fold to
#: ``.git``) stay allowed.
_GIT_INTERNAL_NAMES = frozenset((".git", ".history"))

#: Codepoints Apple's HFS+ ignores when comparing filenames, and which git's
#: own ``core.protectHFS`` strips before testing a component against ``.git``
#: (git's ``utf8.c::is_hfs_dotgit`` / ``next_hfs_char``): the zero-width
#: joiner/non-joiner, the bidi/directional formatting marks, and the BOM. On
#: HFS+ ``.g<U+200C>it`` and ``.git`` are the SAME file, so both must fold to
#: ``.git``.
_HFS_IGNORABLE = frozenset(
    "\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u206a\u206b\u206c\u206d\u206e\u206f\ufeff"
)


def _fold_component(component: str) -> str:
    """A path component reduced to the name a case-insensitive, NTFS or HFS+
    filesystem would actually *create on disk* for it.

    A case-sensitive, byte-exact predicate is the hole the PRD-005 re-check
    drove through: ``.History/config`` passes a literal ``.history`` test, but
    on macOS APFS/HFS+ or Windows NTFS it folds onto the live ``.history``
    GIT_DIR at checkout. This mirrors the evasions git's own
    ``core.protectNTFS``/``core.protectHFS`` defend against, in order:

    * an NTFS **alternate data stream** ``name:stream`` writes ``name`` — so
      ``.git::$DATA`` targets ``.git``; compare the base name only;
    * HFS+ **ignores** the :data:`_HFS_IGNORABLE` codepoints anywhere in a name;
    * a **decomposed** spelling is precomposed (NFC) so it cannot smuggle a
      distinct byte string that names the same file;
    * Windows **strips trailing dots and spaces** from every component, so
      ``.git.`` and ``.git `` both name ``.git``;
    * a case-insensitive filesystem **folds case**, so ``.GIT``/``.History``
      name ``.git``/``.history``.

    ``.gitignore``/``.gitattributes``/``x.history`` survive every fold as
    themselves and are therefore NOT in :data:`_GIT_INTERNAL_NAMES` — only the
    exact ``.git``/``.history`` component is.
    """
    component = component.split(":", 1)[0]
    component = "".join(ch for ch in component if ch not in _HFS_IGNORABLE)
    component = unicodedata.normalize("NFC", component)
    component = component.rstrip(". ")
    return component.casefold()


def is_git_internal_path(path: str) -> bool:
    """True when ANY component of *path* folds to ``.git`` or ``.history``.

    The single source of truth every sync/merge belt (:func:`tree_git_internals`
    here, ``sync.git_internals_in_tree`` on the client, ``merge`` via this
    function) shares, and the exact same fold the pre-receive hook applies out
    of process — so a case, trailing-dot/space or unicode variant that a
    case-insensitive filesystem would collapse onto the live GIT_DIR is refused
    everywhere at once. Component-wise (never a single mega-regex) so an
    evasion has to defeat the per-component fold, not a pattern's anchors.
    Backslash is treated as a separator too: on Windows it is one, and a Unix
    repo has no business carrying a ``.git\\x`` component either.
    """
    for component in path.replace("\\", "/").split("/"):
        if component and _fold_component(component) in _GIT_INTERNAL_NAMES:
            return True
    return False

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
#: The fold-check the pre-receive hook delegates its git-internals scan to.
#:
#: A ``/bin/sh`` ``grep`` cannot see the NTFS trailing-dot/space or the HFS
#: zero-width-codepoint evasions the PRD-005 re-check used, so the hook pipes
#: its changed-path list into THIS, run by the server's own interpreter. It is
#: a **self-contained duplicate** of :func:`_fold_component` /
#: :func:`is_git_internal_path` — no ``import agentcad`` (a security hook must
#: not depend on the package being importable, or on its startup cost), stdlib
#: only — and ``tests/test_git_internals_predicate.py`` asserts the two agree
#: over a shared battery so they cannot drift. Exit ``3`` on a hit (the hook
#: rejects the push), ``0`` when clean; any OTHER exit (a missing interpreter,
#: a crash) is deliberately NOT treated as a hit by the hook, because
#: :func:`materialize`'s in-process fold is the authoritative backstop that
#: refuses the checkout regardless — so a misconfigured interpreter can never
#: brick honest pushes, and a poisoned tree can never reach a checkout.
CHECKPATHS_SCRIPT = '''\
import sys
import unicodedata

_IGNORABLE = frozenset(
    "\\u200c\\u200d\\u200e\\u200f\\u202a\\u202b\\u202c\\u202d\\u202e"
    "\\u206a\\u206b\\u206c\\u206d\\u206e\\u206f\\ufeff"
)
_NAMES = frozenset((".git", ".history"))


def _fold(component):
    component = component.split(":", 1)[0]
    component = "".join(ch for ch in component if ch not in _IGNORABLE)
    component = unicodedata.normalize("NFC", component)
    component = component.rstrip(". ")
    return component.casefold()


def _hit(path):
    for component in path.replace("\\\\", "/").split("/"):
        if component and _fold(component) in _NAMES:
            return True
    return False


def main():
    for line in sys.stdin.read().split("\\n"):
        if line and _hit(line):
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

#: The null-oid test is ``case "$x" in *[!0]*)`` — "consists only of zeros" —
#: rather than a literal 40-zero string, so the hook is correct in a sha256
#: repository too. ``/bin/sh``, not bash: Debian's ``sh`` is dash and every
#: construct here is POSIX. The interpreter for the fold sidecar is baked in at
#: write time (:data:`_HOOK_PYTHON`), with a ``python3``/``python`` PATH
#: fallback, so the hook does not have to guess it at receive time.
PRE_RECEIVE_HOOK = f"""#!/bin/sh
{HOOK_MARKER}
# Managed by agentcad/core/sync_server.py — edits are overwritten on upgrade.
#
# FR9: divergence is surfaced, never overwritten.
#   1. no ref deletes (branch or tag)
#   2. no non-fast-forward branch updates
#   3. no tag rewrites (PRD-015 release tags are immutable)
# New branches and new tags, and fast-forward branch updates, pass.
#
# SECURITY: a pushed commit whose tree writes into the repo's own git
#   internals (any '.history'/'.git' path component, AT ANY DEPTH and in ANY
#   case/NTFS/HFS spelling) is refused. The GIT_DIR is '<project>/.history'
#   inside the work tree, so materialization ('checkout -f') would otherwise
#   plant a hook/config straight into the live internals and run it as the
#   server user. A case variant ('.History') folds onto the live GIT_DIR on a
#   case-insensitive filesystem, so the scan below is case-insensitive AND
#   delegates the NTFS/HFS folds to the checkpaths.py sidecar.
AGENTCAD_PY={_HOOK_PYTHON}
if [ ! -x "$AGENTCAD_PY" ]; then
    AGENTCAD_PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")
fi
AGENTCAD_CHECK=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/{CHECKPATHS_NAME}
rc=0
tips=""
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
    # A non-deleting update: remember the new tip for the tree scan below.
    tips="$tips $new"

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

# Reject a push that writes into the repo's git internals. Bounded on cost: a
# COMBINED-diff name scan ('-c') over only the newly-pushed commits ('$tips
# --not --all' excludes everything the server already has), never the whole
# tree or the whole history. '-c' is load-bearing — a plain name-only log
# omits merge commits, and a file introduced only by a merge (present in the
# merge result, in no parent) would slip past. '--diff-filter=d' ignores
# deletions (removing a stray '.history' path is fine); '--root' makes the
# very first push's root commit show its whole tree as additions.
if [ -n "$tips" ]; then
    paths=$(git log -c --name-only --pretty=format: --diff-filter=d --root \\
            $tips --not --all 2>/dev/null)
    hit=no
    # Fast belt: the case-insensitive ('-i') '.git'/'.history' forms, which
    # covers every realistic attack ('.History', '.GIT', mixed case, any
    # depth) loudly at push time and needs no interpreter.
    if printf '%s\\n' "$paths" | grep -iqE '(^|/)\\.(git|history)(/|$)'; then
        hit=yes
    elif [ -n "$AGENTCAD_PY" ] && [ -f "$AGENTCAD_CHECK" ]; then
        # Exhaustive belt: NTFS trailing dot/space, an alternate data stream,
        # and the HFS-ignorable unicode codepoints a '/bin/sh' grep cannot see.
        # Exit 3 == a folded hit; any other exit (missing interpreter, crash)
        # is left to materialize()'s in-process backstop, never treated as a
        # hit, so a stale interpreter cannot reject an honest push.
        printf '%s\\n' "$paths" | "$AGENTCAD_PY" "$AGENTCAD_CHECK"
        if [ "$?" -eq 3 ]; then
            hit=yes
        fi
    fi
    if [ "$hit" = yes ]; then
        echo "agentcad: refusing a commit that writes into .history/ - the project's git internals are not yours to push" >&2
        rc=1
    fi
fi
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
    # A `core.fsmonitor` command runs on almost every git call; a checked-out
    # or injected one would run as the server user. We never want it, so it is
    # pinned off in the repo config (and again per-call in `_SAFETY_PINS`).
    ("core.fsmonitor", "false"),
)

#: Config keys that must be **absent**: an open-ended value is arbitrary code.
#: A ``filter.<name>.smudge``/``clean`` command runs on ``checkout``;
#: ``core.sshCommand`` on any network call. We cannot ``-c``-override an
#: unknown filter name, so :func:`_enforce` unsets every ``filter.*`` key it
#: finds (and these) rather than trusting the repo config a push could have
#: rewritten via a checked-out ``.history/config``.
_FORBIDDEN_CONFIG = ("core.sshcommand", "core.fsmonitorhookversion")

#: Per-call belt for every server-side git invocation (:func:`_run`): no repo
#: or operator config can make a routine plumbing call — a ``checkout`` running
#: ``post-checkout``, any command consulting ``core.fsmonitor`` — execute code.
#: ``receive-pack`` is spawned by the HTTP backend, not ``_run``, so the FR9
#: ``pre-receive`` hook (which lives in the repo's ``core.hooksPath`` dir) is
#: untouched by this.
_SAFETY_PINS: tuple[str, ...] = (
    "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


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
    cmd = [git_executable(), *_SAFETY_PINS, "--git-dir", str(git_dir),
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


def hooks_dir(git_dir: Path) -> Path:
    """The **server-owned** hooks directory — ``core.hooksPath`` points here.

    Deliberately *not* the default ``.history/hooks``. Two things follow. A
    push's checkout can never make an *unmanaged* hook run (``post-receive``,
    ``update``, ``push-to-checkout`` …): git consults only this directory, and
    the only file we ever place in it is the managed ``pre-receive``. And
    because ``core.hooksPath`` is then a value the server writes and re-asserts
    (never one derived from the repo config a push could rewrite), a
    ``.history/config`` that tried to redirect it is reset on the next
    reconcile. It lives under the GIT_DIR — inside the work tree — but that is
    exactly the region the pre-receive rule and the materialize belt keep any
    push out of.
    """
    return git_dir / "agentcad-hooks"


def _stamp(git_dir: Path) -> tuple[int, int]:
    def mtime(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1
    return (mtime(git_dir / "config"),
            mtime(hooks_dir(git_dir) / "pre-receive"))


def prepare_repo(project_path: Path | str, *, force: bool = False) -> dict:
    """Make a project's history repo safe to serve over smart HTTP.

    Idempotent, and cheap on the second call. Sets :data:`REPO_CONFIG` plus a
    server-owned ``core.hooksPath``, unsets the open-ended dangerous keys
    (:data:`_FORBIDDEN_CONFIG`, every ``filter.*``), and installs
    :data:`PRE_RECEIVE_HOOK` — rewriting the hook whenever its
    :data:`HOOK_MARKER` version differs from the one on disk, so a server
    upgrade that tightens a rule actually tightens it and a hand-edited hook is
    replaced rather than trusted.

    Returns ``{"config": [keys changed], "hook": "installed"|"current"}``.
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
        result = _enforce(project_path, git_dir, force=force)
        _prepared[key] = _stamp(git_dir)
    return result


def reconcile_repo(project_path: Path | str) -> dict:
    """Re-assert config + hook **without trusting the mtime cache**.

    Called by :func:`materialize` before every checkout, so a config a prior
    push's checkout might have written (a ``core.hooksPath`` redirect, a
    ``filter.*`` smudge command, ``core.fsmonitor``) is healed on the very next
    push even when its mtime happens to match the cached stamp. Cheap: one
    ``git config --list`` and a write only on genuine drift.
    """
    project_path = Path(project_path)
    git_dir = history_dir(project_path)
    with _prepare_lock:
        result = _enforce(project_path, git_dir, force=False)
        _prepared[str(git_dir)] = _stamp(git_dir)
    return result


def _enforce(project_path: Path, git_dir: Path, *, force: bool) -> dict:
    """The body of :func:`prepare_repo`/:func:`reconcile_repo` (lock held)."""
    existing = _config_map(project_path)
    written: list[str] = []

    desired = list(REPO_CONFIG) + [
        # An ABSOLUTE, server-owned hooks dir — see :func:`hooks_dir`.
        ("core.hooksPath", str(hooks_dir(git_dir).resolve())),
    ]
    for name, value in desired:
        # `git config --list` lower-cases the variable name (the section and
        # key are case-insensitive to git); compare in its spelling, not ours,
        # or every request rewrites the config.
        if existing.get(name.lower()) != value:
            _run(project_path, "config", name, value)
            written.append(name)

    # Unset the open-ended dangerous keys. `filter.<name>.smudge`/`clean` is a
    # command run on checkout; enumerated from the live config because we
    # cannot name an arbitrary filter to `-c`-override it.
    for key in sorted(existing):
        if key.startswith("filter.") or key in _FORBIDDEN_CONFIG:
            _run(project_path, "config", "--unset-all", key, check=False)
            written.append(key)

    hook = _install_hook(git_dir, force=force)
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
    hooks = hooks_dir(git_dir)
    path = hooks / "pre-receive"
    sidecar = hooks / CHECKPATHS_NAME

    def _read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8") if p.is_file() else ""
        except OSError:
            return ""

    # The sidecar the hook delegates its NTFS/HFS folds to is versioned WITH the
    # hook: both must match, and pre-receive must be executable (a hook without
    # the bit is silently not run — the worst failure mode for this file).
    if (not force and _read(path) == PRE_RECEIVE_HOOK
            and _read(sidecar) == CHECKPATHS_SCRIPT
            and os.access(path, os.X_OK)):
        return "current"
    hooks.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(sidecar, CHECKPATHS_SCRIPT, mode=0o644)
    # pre-receive LAST: it references the sidecar, so the sidecar must already
    # be in place the first time the hook fires.
    _atomic_write_text(path, PRE_RECEIVE_HOOK, mode=0o755)
    return "installed"


def _atomic_write_text(path: Path, text: str, *, mode: int) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


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
        # Re-assert the security config on every push (FR: not just on an mtime
        # change). Cheap, idempotent, and it heals a config a prior checkout
        # might have touched before this checkout runs.
        reconcile_repo(project_path)
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
        # Belt for the pre-receive rule (a repo that predates the hook, or was
        # populated out of band): NEVER checkout a tree that carries the repo's
        # own git internals. Refuse the materialization — the refs already
        # landed, so the bytes are safe on the server, but they are not written
        # into the live GIT_DIR where a planted hook/config would run.
        offending = tree_git_internals(project_path, branch)
        if offending:
            return {"materialized": False, "reason": "git_internals_in_tree",
                    "branch": branch, "changed": 0,
                    "offending": offending[:10]}
        changed = _run(project_path, "diff", "--name-only", "HEAD", "--",
                       check=False)
        count = len([line for line in (changed.stdout or "").splitlines()
                     if line])
        # `-c core.hooksPath=/dev/null -c core.fsmonitor=false` (via
        # `_SAFETY_PINS` in `_run`) so a `post-checkout` hook or an
        # `fsmonitor` command in the repo config cannot run on this checkout.
        _run(project_path, "checkout", "-f", branch, "--")
        return {"materialized": True, "reason": None, "branch": branch,
                "changed": count}


def tree_git_internals(project_path: Path | str, ref: str) -> list[str]:
    """Paths in *ref*'s tree that land in the repo's git internals, sorted.

    A component that folds to ``.history`` (the GIT_DIR nested in the work
    tree) or ``.git`` — at any depth, in any case/NTFS/HFS spelling
    (:func:`is_git_internal_path`). ``-r -t`` so a tree/gitlink entry named
    ``.git``/``.history`` is caught even with nothing under it. ``-z`` and a NUL
    split so a path with an embedded newline cannot smuggle a component past a
    line-oriented scan. Empty list = safe to check out.
    """
    result = _run(Path(project_path), "ls-tree", "-r", "-t", "--name-only",
                  "-z", ref, check=False)
    return sorted(path for path in (result.stdout or "").split("\0")
                  if path and is_git_internal_path(path))
