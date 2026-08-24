"""The last RCE vector in a divergent `agentcad pull` (PRD-005 security wave).

Fixer 1 hardened clone/fetch/checkout/fast-forward against a pushed tree that
writes into this clone's own GIT_DIR (`<project>/.history`) — a planted
`post-merge` hook or a `config` with an `fsmonitor` command that would run as
the user. But a **divergent** pull does not fast-forward: it drives PRD-001's
`MergeOrchestrator.merge`, whose staged worktree materializes the incoming
branch and whose `reset --hard` lands the merge RESULT into a live work tree —
bypassing the checkout/ff belts.

Two belts close it, and both are proven here against a REAL git binary:

* `sync.merge_diverged` refuses the fetched tip before it is even parked as
  `incoming/<branch>` (nothing is created, the local tree is untouched);
* `merge.MergeOrchestrator._assert_no_git_internals` scans the merge RESULT —
  which `git merge-tree` can SYNTHESIZE a `.history` path into, present in
  neither parent tip (directory-rename detection) — before it is staged or a
  ref moves.

A normal clean or conflicting divergence must behave exactly as before: the
belts fire only on a tree that writes into git internals.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentcad.core import sync
from agentcad.core.history import ProjectHistory
from agentcad.core.model import ValidationError

# Reuse Fixer 1's committed harness: the served-instance and clone fixtures, the
# real-git plumbing helper, and the product's own committer.
from .test_sync_cli import (  # noqa: F401 — imported fixtures are used by name
    _server_git,
    commit,
    diverged,
    local_instance,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """Never the developer's real `~/.agentcad/` — `sync.json` holds a token and
    the credential helper reads it back from a subprocess, so isolation rides
    the environment (Fixer 1's `test_sync_cli` reason)."""
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    for name in ("AGENTCAD_TOKEN", "AGENTCAD_URL", "AGENTCAD_SYNC_CONFIG"):
        monkeypatch.delenv(name, raising=False)


def _poison_incoming(project: Path, subpath: str = "hooks/post-merge") -> str:
    """Advance the SERVED project's master to a commit whose tree writes an
    executable into `.history/<subpath>` — a compromised/hostile server, the
    incoming side of a divergence."""
    def g(*args, inp=None):
        return _server_git(project, *args, inp=inp)
    blob = g("hash-object", "-w", "--stdin",
             inp="#!/bin/sh\ntouch /tmp/PWNED_merge\n").stdout.strip()
    parts = subpath.split("/")
    tree = g("mktree", inp=f"100755 blob {blob}\t{parts[-1]}\n").stdout.strip()
    for comp in reversed(parts[:-1]):
        tree = g("mktree", inp=f"040000 tree {tree}\t{comp}\n").stdout.strip()
    base_tree = g("ls-tree", "HEAD").stdout
    root = g("mktree",
             inp=base_tree + f"040000 tree {tree}\t.history\n").stdout.strip()
    head = g("rev-parse", "HEAD").stdout.strip()
    commit_oid = g("commit-tree", root, "-p", head, inp="poison\n").stdout.strip()
    g("update-ref", "refs/heads/master", commit_oid)
    return commit_oid


def _build_dirrename_branches(dest: Path) -> tuple[str, str]:
    """Two branches in *dest*'s repo whose MERGE synthesizes `.history/b.py`,
    a path present in NEITHER tip, via directory-rename detection: `ours` adds
    `d/b.py`; `theirs` renames the whole `d/` directory to `.history/`. Returns
    `(ours_head, theirs_head)`.

    Built with raw plumbing (mktree/commit-tree) so the illegal `.history` path
    lands in a committed tree without a working-tree checkout fighting the
    clone's `.history` exclude."""
    def g(*args, inp=None):
        return _server_git(dest, *args, inp=inp)
    ablob = g("hash-object", "-w", "--stdin", inp="# d/a\n").stdout.strip()
    bblob = g("hash-object", "-w", "--stdin", inp="# d/b\n").stdout.strip()
    base_ls = g("ls-tree", "HEAD").stdout.rstrip("\n")
    head = g("rev-parse", "HEAD").stdout.strip()

    d_base = g("mktree", inp=f"100644 blob {ablob}\ta.py\n").stdout.strip()
    base_root = g("mktree",
                  inp=base_ls + f"\n040000 tree {d_base}\td\n").stdout.strip()
    c_base = g("commit-tree", base_root, "-p", head, inp="base\n").stdout.strip()

    d_ours = g("mktree",
               inp=f"100644 blob {ablob}\ta.py\n"
                   f"100644 blob {bblob}\tb.py\n").stdout.strip()
    ours_root = g("mktree",
                  inp=base_ls + f"\n040000 tree {d_ours}\td\n").stdout.strip()
    c_ours = g("commit-tree", ours_root, "-p", c_base,
               inp="ours adds d/b.py\n").stdout.strip()

    hist = g("mktree", inp=f"100644 blob {ablob}\ta.py\n").stdout.strip()
    theirs_root = g("mktree",
                    inp=base_ls + f"\n040000 tree {hist}\t.history\n").stdout.strip()
    c_theirs = g("commit-tree", theirs_root, "-p", c_base,
                 inp="theirs renames d/ -> .history/\n").stdout.strip()

    g("update-ref", "refs/heads/ours", c_ours)
    g("update-ref", "refs/heads/theirs", c_theirs)
    return c_ours, c_theirs


def _poison_incoming_named(project: Path, topdir: str) -> str:
    """Like :func:`_poison_incoming`, but the internals dir is a case/spelling
    VARIANT (`.History`) — the PRD-005 re-check bypass at the merge layer."""
    def g(*args, inp=None):
        return _server_git(project, *args, inp=inp)
    cfg = g("hash-object", "-w", "--stdin",
            inp='[core]\n\tfsmonitor = "touch /tmp/PWNED_merge"\n').stdout.strip()
    tree = g("mktree", inp=f"100644 blob {cfg}\tconfig\n").stdout.strip()
    base_tree = g("ls-tree", "HEAD").stdout
    root = g("mktree",
             inp=base_tree + f"040000 tree {tree}\t{topdir}\n").stdout.strip()
    head = g("rev-parse", "HEAD").stdout.strip()
    commit_oid = g("commit-tree", root, "-p", head,
                   inp="casefold poison\n").stdout.strip()
    g("update-ref", "refs/heads/master", commit_oid)
    return commit_oid


# ---------------------------------------------------- belt 1: the incoming tip

def test_a_divergent_pull_refuses_a_case_folded_incoming_branch(diverged):
    """The case-fold bypass at the merge layer: the server diverges with a tip
    that writes `.History/config`, the clone diverges honestly. The old
    case-sensitive belt would have parked and staged it; the folded belt refuses
    the incoming tip first, before anything is created or a byte moves."""
    base, project, dest, merger, client, name = diverged
    _poison_incoming_named(project, ".History")   # server (incoming) side
    commit(dest, "parts/mine.py", "# mine\n")     # local side
    my_head = ProjectHistory().head(dest)

    with pytest.raises(sync.SyncError) as exc:
        sync.pull(dest, merger=merger)

    assert "not yours to pull" in str(exc.value)
    assert ".History/config" in str(exc.value)
    assert ProjectHistory().head(dest) == my_head
    assert not any(b["name"].startswith(sync.INTERNAL_BRANCH_PREFIX)
                   for b in client.history.branches(dest))




def test_a_divergent_pull_refuses_a_poisoned_incoming_branch(diverged):
    """The server diverges with a commit that writes `.history/hooks/post-merge`
    while the clone diverges with an honest one. The divergence would drive the
    staged merge — but `merge_diverged` refuses the incoming tip first, before
    it parks `incoming/*` or touches a byte."""
    base, project, dest, merger, client, name = diverged
    _poison_incoming(project)                     # server (incoming) side
    commit(dest, "parts/mine.py", "# mine\n")     # local side
    my_head = ProjectHistory().head(dest)

    with pytest.raises(sync.SyncError) as exc:
        sync.pull(dest, merger=merger)

    assert "not yours to pull" in str(exc.value)
    assert ".history/hooks/post-merge" in str(exc.value)
    # Nothing landed: the local ref is where it was, no hook was planted into
    # the workstation's live GIT_DIR, and no scratch branch dangles.
    assert ProjectHistory().head(dest) == my_head
    assert not (dest / ".history" / "hooks" / "post-merge").is_file()
    assert not any(b["name"].startswith(sync.INTERNAL_BRANCH_PREFIX)
                   for b in client.history.branches(dest))
    # The server's ref is untouched too — a refusal, never a counter-write.
    assert ProjectHistory().head(project) is not None


# --------------------------------------------- belt 2: the synthesized result

def test_the_merge_result_belt_refuses_a_synthesized_history_path(diverged):
    """A directory-rename merge whose RESULT carries `.history/b.py` — a path in
    NEITHER parent tip, so scanning either tip would miss it. The staged-merge
    belt scans the tree that actually lands and refuses. Driven at the merge
    layer directly, where the belt lives and where no incoming-tip check exists
    to mask it."""
    _base, _project, dest, _merger, client, name = diverged
    ours_head, theirs_head = _build_dirrename_branches(dest)

    with pytest.raises(ValidationError) as exc:
        client.merges.merge(name, "theirs", "ours")

    # The belt names the SYNTHESIZED path, proving it scanned the result tree
    # (neither `ours` nor `theirs` carries `.history/b.py`).
    planted = (exc.value.details or {}).get("git_internals") or []
    assert ".history/b.py" in planted, planted
    # Nothing staged, nothing moved, nothing planted into the live GIT_DIR.
    assert client.merges.status(name)["merge"] is None
    assert client.history.resolve_branch(dest, "ours") == ours_head
    assert client.history.resolve_branch(dest, "theirs") == theirs_head
    assert not (dest / ".history" / "b.py").exists()
    assert not (dest / ".history" / "a.py").exists()


def test_merge_diverged_cleans_up_the_scratch_branch_when_the_result_is_refused(
        diverged, monkeypatch):
    """When the staged-merge belt fires (a poisoned RESULT that the incoming tip
    does not betray), `merge_diverged` must abort the whole pull with the clear
    message AND remove the `incoming/*` branch it parked — leaving the tree as a
    failed merge does. The `git_internals` ValidationError is injected so this
    isolates `merge_diverged`'s cleanup from the merge construction proven
    above."""
    _base, project, dest, merger, client, name = diverged
    commit(project, "parts/theirs.py", "# theirs\n")   # a real, clean
    commit(dest, "parts/mine.py", "# mine\n")          # divergence
    my_head = ProjectHistory().head(dest)

    def poisoned_merge(proj, source, target=None, **kwargs):
        # The scratch branch must exist by now — the belt fires INSIDE the merge.
        assert any(b["name"] == source
                   for b in client.history.branches(dest)), source
        raise ValidationError(
            "merge result writes into .history",
            {"git_internals": [".history/hooks/post-merge"],
             "source": source, "target": target},
        )

    monkeypatch.setattr(client.merges, "merge", poisoned_merge)

    with pytest.raises(sync.SyncError) as exc:
        sync.pull(dest, merger=merger)

    assert "not yours to pull" in str(exc.value)
    assert ".history/hooks/post-merge" in str(exc.value)
    # Cleaned up like a failed merge: the local ref is unchanged and no scratch
    # branch is left dangling.
    assert ProjectHistory().head(dest) == my_head
    assert not any(b["name"].startswith(sync.INTERNAL_BRANCH_PREFIX)
                   for b in client.history.branches(dest))


# ------------------------------------------------ regression: honest merges

def test_a_real_content_conflict_still_surfaces_the_prd001_conflict(diverged):
    """A genuine content conflict on `parts/a.py` is NOT a false refusal: it
    comes back as PRD-001's `merge_conflict` payload, staged, nothing
    overwritten — exactly as before the belts."""
    _base, project, dest, merger, client, name = diverged
    commit(project, "parts/a.py", "# part a, their way\n")
    commit(dest, "parts/a.py", "# part a, my way\n")
    my_head = ProjectHistory().head(dest)

    result = sync.pull(dest, merger=merger)

    assert len(result["conflicts"]) == 1
    error = result["conflicts"][0]["merge"]["error"]
    assert error["type"] == "merge_conflict"
    assert [c["path"] for c in error["details"]["conflicts"]] == ["parts/a.py"]
    # Never reset, never overwrite — and the conflict, not a git-internals
    # refusal, is what surfaced.
    assert ProjectHistory().head(dest) == my_head
    assert (dest / "parts" / "a.py").read_text() == "# part a, my way\n"
    assert client.merges.status(name)["merge"]["outstanding"] == 1


def test_a_clean_divergent_pull_still_merges(diverged):
    """No `.history` anywhere: a clean two-parent merge lands, both sides'
    work is present, and the scratch branch does not outlive it."""
    _base, project, dest, merger, client, name = diverged
    commit(project, "parts/theirs.py", "# theirs\n")
    commit(dest, "parts/mine.py", "# mine\n")

    result = sync.pull(dest, merger=merger)

    entry = next(b for b in result["branches"] if b["branch"] == "master")
    assert entry["state"] == "diverged" and entry["action"] == "merged"
    assert "error" not in entry["merge"], entry["merge"]
    assert (dest / "parts" / "mine.py").is_file()
    assert (dest / "parts" / "theirs.py").is_file()
    parents = sync.local(dest, "rev-list", "--parents", "-n", "1",
                         "HEAD").stdout.split()
    assert len(parents) == 3, parents
    assert not any(b["name"].startswith(sync.INTERNAL_BRANCH_PREFIX)
                   for b in client.history.branches(dest))
