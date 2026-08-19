"""A tag-capable, reusable ``materialized_service`` (PRD-015 FR5, FR10-11):
check a project's ``.history`` tree out at a **branch, tag or commit** into a
throwaway detached worktree and drive an *ephemeral* :class:`AgentCADService`
rooted there — the mechanism that makes a released tag's BOM (and, later, its
release bundle) reproducible after the fact.

**Pure Python, no OCP/build123d import.** The kernel is *shared*, never
re-started (a second pool costs another pool's RAM and startup to run the same
builds).

Why a sibling of ``checks.py`` rather than a lift (design Decision 5, option
b): the dangerous atom — the muzzled service with its three non-negotiable
nulls — already lives at module scope as :func:`checks._ephemeral_service`, so
we import it directly and there is **no** third copy of those nulls. What
``checks.py`` keeps to itself, :meth:`CheckRunner._resolve_ref` /
``_materialized``, are methods bound to a runner's ``warnings`` list, its
``source`` provenance block and its determinism stage; lifting them would
thread that state through a standalone function and risk perturbing the
``test_checks_ref`` determinism/containment assertions (they compare exact
``source`` dicts and repo/worktree state). So only the worktree add/teardown
mechanics are re-expressed here — small, self-contained, and it leaves
``checks.py`` byte-for-byte untouched.

Containment is by construction, exactly as in ``checks.py``: the ephemeral
service is rooted at the throwaway cell, so its ``canonical_path_of`` — and
therefore ``.cache/`` and ``exports/`` — lands inside the cell and the user's
project is never written. The worktree is a *linked* checkout of the user's
``.history`` repo, so ``_ephemeral_service`` nulls ``bus.on_publish`` (no
snapshot commit into the user's repo), ``branch_resolver`` and ``write_guard``.
Teardown removes only the cell this call created with ``mkdtemp`` (0700, unique)
— it never deletes a directory it did not make.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

from .checks import _ephemeral_service, default_work_root, refuse_work_dir_overlap
from .history import HistoryError, looks_like_commit
from .model import NotFoundError, ValidationError


def _resolve_ref(history, canonical: Path, proj: str, ref: str) -> tuple[str, str]:
    """``(kind, sha)`` for *ref*: branch, then tag, then commit id.

    **Never** ``git rev-parse <ref>`` directly: it searches ``refs/tags``
    before ``refs/heads``, so a tag named like a branch would answer for it
    (the same trap ``CheckRunner._resolve_ref`` documents). A bare name that is
    *both* resolves as the BRANCH; ``refs/heads/<x>`` / ``refs/tags/<x>`` force
    the disambiguation. A ref that does not resolve raises
    ``NotFoundError``/``ValidationError`` naming it — never a crash.
    """
    if not history.available():
        raise ValidationError(
            "resolving a ref needs git on PATH (the ref is materialized from "
            "the project's .history repository); omit ref to read the working "
            "tree instead", {"ref": ref})
    if not history._has_repo(canonical):
        raise ValidationError(
            f"project {proj!r} has no .history repository yet — nothing has "
            f"been snapshotted; omit ref to read the working tree instead",
            {"ref": ref, "project": proj})

    name = str(ref)
    for prefix, kind, resolve in (("refs/heads/", "branch", history.resolve_branch),
                                  ("refs/tags/", "tag", history.resolve_tag)):
        if name.startswith(prefix):
            found = resolve(canonical, name[len(prefix):])
            if found:
                return kind, found
            raise NotFoundError(
                f"{kind} {name[len(prefix):]!r} not found in project {proj!r}",
                {"ref": ref})

    branch = history.resolve_branch(canonical, name)
    if branch:
        return "branch", branch
    tag = history.resolve_tag(canonical, name)
    if tag:
        return "tag", tag
    if looks_like_commit(name) and history.has_commit(canonical, name):
        return "commit", history.resolve_ref(canonical, name) or name

    raise NotFoundError(
        f"ref {ref!r} not found in project {proj!r}: searched "
        f"refs/heads/{name}, refs/tags/{name} and the project's commit ids",
        {"ref": ref, "searched": ["refs/heads", "refs/tags", "commit"]})


@contextlib.contextmanager
def materialized_service(service, project: str, ref: str):
    """Yield ``(ephemeral_service, registry, project_name)`` for *project*
    checked out at *ref* (a branch, tag or commit), then tear the worktree down.

    Load-bearing order (design Decision 5): resolve the ref explicitly →
    ``git worktree add --detach <sha>`` into a unique cell we own → an
    ``_ephemeral_service`` with the three non-negotiable nulls → teardown in a
    ``finally`` that removes only the cell this call created.
    """
    history = service.history
    canonical = Path(service.store.canonical_path_of(project)).resolve()
    _kind, sha = _resolve_ref(history, canonical, project, ref)

    # A cell we own outright: `mkdtemp` creates it 0700 and unique, so a
    # pre-existing collision is impossible and teardown never touches a
    # directory this call did not make.
    root = default_work_root(service)
    cell = Path(tempfile.mkdtemp(prefix=f"agentcad-bom-{os.getpid()}-",
                                 dir=root)).resolve()
    # Belt and braces (checks.py's W1 bug): the CELL — not the broad work root,
    # which may legitimately contain the projects tree — must not be, hold or
    # sit inside the project it materializes.
    refuse_work_dir_overlap(cell, canonical, service.store.root)
    tree = cell / canonical.name
    try:
        history._run(canonical, "worktree", "prune", check=False)
        added = history._run(canonical, "worktree", "add", "--detach",
                             str(tree), sha, check=False)
        if added.returncode != 0:
            detail = (added.stderr or "").strip() or (added.stdout or "").strip()
            raise ValidationError(
                f"could not materialize {sha[:8]} for a ref-pinned read: {detail}",
                {"ref": ref, "sha": sha, "path": str(tree)})
        ephemeral, registry, name = _ephemeral_service(cell, tree, service.kernel)
        yield ephemeral, registry, name
    finally:
        with contextlib.suppress(HistoryError):
            history._run(canonical, "worktree", "remove", "--force", str(tree),
                         check=False)
            history._run(canonical, "worktree", "prune", check=False)
        # The cell is ours (mkdtemp) — remove whatever survived the worktree
        # remove (a `.cache/` the ephemeral build wrote, the empty cell).
        shutil.rmtree(cell, ignore_errors=True)
