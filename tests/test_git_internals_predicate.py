"""The git-internals predicate — the single source of truth every sync/merge
belt shares (PRD-005 RCE re-check, the case-folding bypass).

The prior fix tested a path *byte-exactly* (a case-sensitive regex, and a
`grep` without `-i`). On a case-insensitive filesystem — macOS APFS/HFS+,
Windows NTFS, the machines this feature is *for* — a pushed/pulled/merged tree
spelling `.History/config` or `.Git/hooks/post-checkout` passed every belt and
then folded onto the live `<project>/.history` GIT_DIR at checkout, planting a
`config`/hook that AgentCAD's own `history._exec` then ran as the server (or
workstation) user.

`sync_server.is_git_internal_path` closes it component-wise and fold-aware
(case, NTFS trailing dot/space + alternate data streams, HFS-ignorable
unicode). These tests pin:

* every case/NTFS/HFS spelling of `.git`/`.history` is REFUSED, at any depth;
* the neighbours that merely *start* with `.git`/*end* with `history`
  (`.gitignore`, `.gitattributes`, `x.history`, `history`) stay ALLOWED —
  an over-block would break every real project;
* the `checkpaths.py` sidecar the pre-receive hook delegates to agrees with the
  in-process predicate over the whole battery, so the two can never drift;
* client, server and merge all resolve to the *same object* — one predicate,
  not three that can be hardened one at a time.

Pure and fast: no git, no socket. The real-git end-to-end proofs live in
`test_sync_server`/`test_sync_cli`/`test_sync_merge_rce`.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from agentcad.core import sync, sync_server
from agentcad.core.sync_server import _fold_component, is_git_internal_path

# A zero-width non-joiner (U+200C): HFS+ ignores it, so `.g<ZWNJ>it` and `.git`
# are the same file on disk. git's own core.protectHFS strips exactly this set.
ZWNJ = "‌"
BOM = "﻿"

# Every one of these MUST be refused: they are the ways a tree path spells
# `.git`/`.history` such that a case-insensitive / NTFS / HFS filesystem
# collapses it onto the live GIT_DIR.
REFUSED = [
    ".git",
    ".history",
    ".History/config",                       # the re-check's exact bypass
    ".HISTORY/hooks/post-checkout",
    ".Git/config",
    ".GIT",
    ".Git",
    ".HiStOrY",
    ".git ",                                  # NTFS strips a trailing space
    ".git.",                                  # NTFS strips a trailing dot
    ".git. . ",                               # ...any run of them
    ".history.",
    ".history ",
    "sub/.History/x",                         # mixed case at depth
    "a/b/.GIT/c",
    f".g{ZWNJ}it/x",                          # HFS-ignorable in the middle
    f".history{ZWNJ}",                        # ...and trailing
    f"{BOM}".join([".hi", "story"]),          # BOM inside `.history`
    ".git::$DATA",                            # NTFS alternate data stream
    ".git:whatever",
    ".GIT./hooks/post-checkout",              # case + trailing dot at depth
]

# Every one of these MUST be allowed: they only resemble the internals. An
# over-block here breaks real projects — `.gitignore`/`.gitattributes` ship in
# every clone.
ALLOWED = [
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    ".gitkeep",
    "x.history",
    "history",
    "history/log.txt",
    "githooks",
    "gitconfig",
    ".githubconfig",
    ".history_notes",
    "my.git.txt",
    "parts/bracket.py",
    "project.json",
    "imports/model.stl",
    ".git-credentials",                       # a file NAMED with a `.git-` stem
]


@pytest.mark.parametrize("path", REFUSED)
def test_a_git_internals_variant_is_refused(path):
    assert is_git_internal_path(path) is True, path


@pytest.mark.parametrize("path", ALLOWED)
def test_a_lookalike_path_is_allowed(path):
    assert is_git_internal_path(path) is False, path


def test_the_exact_bypass_the_recheck_used_is_closed():
    """The regression, named: a byte-exact `.history` test missed `.History`,
    which is the live GIT_DIR on a case-insensitive filesystem."""
    assert is_git_internal_path(".History/config") is True
    assert is_git_internal_path(".HISTORY/hooks/post-checkout") is True
    assert is_git_internal_path(".Git/config") is True


def test_fold_reduces_every_spelling_to_the_on_disk_name():
    """`_fold_component` is the whole game: it must land every variant on the
    exact `.git`/`.history` a case-insensitive/NTFS/HFS filesystem creates."""
    assert _fold_component(".History") == ".history"
    assert _fold_component(".GIT") == ".git"
    assert _fold_component(".git.") == ".git"
    assert _fold_component(".git ") == ".git"
    assert _fold_component(".git. . ") == ".git"
    assert _fold_component(".git::$DATA") == ".git"
    assert _fold_component(f".g{ZWNJ}it") == ".git"
    # ...and it must NOT collapse the lookalikes onto an internal name.
    assert _fold_component(".gitignore") == ".gitignore"
    assert _fold_component("x.history") == "x.history"
    assert _fold_component("history") == "history"


def test_an_embedded_newline_is_one_component_not_a_git_dir():
    """A path with a newline INSIDE a component is a single file (git stores it
    as one entry; `tree_git_internals` reads it back NUL-delimited), literally
    named `a.py<LF>.git` under `parts/` — never a `.git` directory. The
    predicate must not be fooled into blocking it by splitting on the newline;
    `.git` is only ever a `/`-delimited component."""
    assert is_git_internal_path("parts/a.py\n.git") is False
    # But a genuine `.git` component with a newline elsewhere is still caught.
    assert is_git_internal_path("parts/a.py\n/.git/x") is True


def test_the_predicate_is_one_object_across_client_server_and_merge():
    """Single source of truth: `sync` imports the server's function rather than
    keeping its own regex (the shape that let one belt be hardened and the
    others rot). `merge._assert_no_git_internals` reaches it through
    `tree_git_internals`, which calls it too."""
    assert sync.is_git_internal_path is sync_server.is_git_internal_path
    assert sync.git_internals_in_tree.__module__ == "agentcad.core.sync"


# ----------------------------------------------------- the hook's sidecar

def _run_sidecar(script_path, stdin_text):
    return subprocess.run([sys.executable, str(script_path)], input=stdin_text,
                          capture_output=True, text=True, timeout=30)


@pytest.fixture
def sidecar(tmp_path):
    """The exact `checkpaths.py` the pre-receive hook installs, on disk."""
    path = tmp_path / sync_server.CHECKPATHS_NAME
    path.write_text(sync_server.CHECKPATHS_SCRIPT, encoding="utf-8")
    return path


@pytest.mark.parametrize("path", REFUSED)
def test_the_sidecar_agrees_with_the_predicate_on_a_hit(sidecar, path):
    """The hook delegates the NTFS/HFS folds a `/bin/sh` grep cannot see to this
    sidecar, which duplicates the fold. Exit 3 == a folded hit, so the two
    implementations cannot drift into disagreement on a REFUSED path."""
    result = _run_sidecar(sidecar, path + "\n")
    assert result.returncode == 3, (path, result.returncode, result.stderr)


@pytest.mark.parametrize("path", ALLOWED)
def test_the_sidecar_agrees_with_the_predicate_on_a_miss(sidecar, path):
    result = _run_sidecar(sidecar, path + "\n")
    assert result.returncode == 0, (path, result.returncode, result.stderr)


def test_the_sidecar_scans_a_whole_mixed_batch(sidecar):
    """A real hook feeds the sidecar the whole changed-path list at once: a
    single poisoned line among many clean ones still trips it, and an all-clean
    batch passes."""
    clean = _run_sidecar(sidecar, "\n".join(ALLOWED) + "\n")
    assert clean.returncode == 0, clean.stderr
    poisoned = _run_sidecar(sidecar, "\n".join(ALLOWED + [".History/config"]))
    assert poisoned.returncode == 3


def test_the_sidecar_is_self_contained():
    """It must not `import agentcad`: a security hook cannot depend on the
    package being importable in the receive-pack environment, or pay its
    startup cost. Only the stdlib it names."""
    assert "import agentcad" not in sync_server.CHECKPATHS_SCRIPT
    assert "import sys" in sync_server.CHECKPATHS_SCRIPT
    assert "import unicodedata" in sync_server.CHECKPATHS_SCRIPT
