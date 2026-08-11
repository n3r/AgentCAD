# 0121 — PRD-008 slice 10: snapshot authorship, author-aware undo, `revert`

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Claude (Opus 5)

## Summary
Every snapshot now records **who** made it, `undo`/`redo` take a `scope`, and a
`"mine"` undo of a commit that is no longer the branch head is a real
`git revert` of exactly that commit instead of a whole-tree restore that would
take everybody else's later work with it. **AC7 holds**
(`tests/test_undo_authors.py`, 15 tests): A edits part X, B edits part Y, A's
`undo {scope: "mine"}` takes back only X and B's edit stands; after B also
edits X, A's undo is a `conflict_error` carrying
`{commit, reason: "overlapping_changes", paths, blocked_by}` with B's commit
named — refused, never half-applied (FR14).

Two design decisions carry the slice, and both are about not breaking something
that already works:

- **The undo stacks are NOT re-keyed per client** (design Decision 16).
  `scope` defaults to `"any"`, which is byte-identical to the behavior that
  predates authorship — a human watching the agent edit and pressing Cmd+Z to
  take it back is this product's flagship loop, and per-client stacks would
  leave that browser's stack empty. `"mine"` is the opt-in, and it *skips*
  other clients' entries rather than discarding them.
- **Authorship is a commit-message trailer, not a git author** (Decision 15).
  `Client: <client id>` goes in the commit *body*; git's own author/committer
  stay the fixed repo-local identity, because the client id is a self-asserted
  header and rewriting the author with it would dress bookkeeping up as a
  cryptographic claim.

## Risk spikes, run first

**R4 — `git revert` inside GIT_DIR=`.history`.** Spiked before any code was
written, in a throwaway project, through `history._run` in **both** layouts
(the main tree, and a linked worktree at `.history/trees/feat/`). Result in
both: `revert --no-commit <sha>` + `commit` lands; the reverted file goes back
while a later unrelated commit's file stands; `parent_of(revert)` is the old
head (so the cursor's parent walk is unaffected); the subject is
`revert <sha8> (...)`, which does **not** collide with `UndoCursor`'s
`"restore "`-prefix guard; a conflicting revert exits 1, and
`revert --abort` + `reset --hard HEAD` leaves `git status --porcelain` empty,
HEAD unmoved and no `sequencer/` directory behind. Nothing was re-scoped.

**R5 — the `Client:` trailer changes commit messages.** Grepped the suite
before landing. The exact-message assertions are
`tests/test_branches.py:224,225,237,261,265,725`, `tests/test_history.py:142`
and the `%B` substring reads in `tests/test_merge.py`,
`tests/test_proposals.py`, `tests/test_prd001_acceptance.py` and
`tests/test_prd002_acceptance.py`. **None of them needed a change**, and no
pre-PRD-008 test file was edited: the equality assertions read `log()`'s
`message`, which is `%s` (the *subject*), and a trailer is a body line; the
`%B` reads are `in` substring checks on merge commits, which
`MergeOrchestrator` builds with its own `Merged-by:` trailer and which
`snapshot()` therefore leaves alone. `proposals._VALIDATION_RE` is anchored at
`^Validation:` under `re.M` and cannot match a `Client:` line.

## Changes
- **`agentcad/core/history.py`**
  - `with_client_trailer(message)` / `author_of(body)` module helpers plus
    `_CLIENT_TRAILER_RE`. The trailer is appended only when the message does
    not already carry one, so `MergeOrchestrator`-style pre-built messages and
    a re-snapshot of the same text stay single-trailered.
  - `snapshot()` commits `with_client_trailer(message)`. The *label* handed to
    `UndoCursor.on_snapshot` by `service.py` is still the caller's plain
    message — stack labels are unchanged.
  - `log()` reads `%H%x1f%cI%x1f%s%x1f%B%x1e` (record-separated, because `%B`
    is multi-line) and adds `author` to every row: the trailer's value, or
    `None` for a commit written before authorship existed — never
    `"unknown"`, which is a different fact. Deliberately **not** git's
    `%(trailers:…)` placeholder: it needs git ≥ 2.22 and degrades by emitting
    itself literally, which would put junk in every author field.
  - `ProjectHistory.revert(path, commit, message=None)` — the one new git verb
    in PRD-008. Validates like `restore` (hex-only commit id, existence
    probe), reverts a two-parent commit against its **first** parent (`-m 1`,
    the branch that was merged *into*), and on failure reads the unmerged
    paths **before** aborting, computes `blocked_by` as the commits in
    `<commit>..HEAD` touching those paths, then `revert --abort` +
    `reset --hard HEAD` and raises `ConflictError`. A revert with nothing to
    apply raises the same error with `reason: "already_reverted"` rather than
    an empty commit. Helpers `_unmerged_paths` / `_commits_touching`.
  - `UndoCursor.on_snapshot` records `author` from `locks.current_client_id()`
    (read there, not at step time: it runs synchronously inside the mutating
    call). `status()` gains `mine: {undo, redo}` counts for the calling
    client, alongside the untouched `available`/`undo`/`redo` keys.
  - `undo(proj, scope="any")` / `redo(proj, scope="any")`; `_step` gains
    `scope`, rejecting anything but `any`/`mine` with a `ValidationError`.
    `"mine"` scans the stack from the top for the caller's entry and pops it
    from its position; the post-restart single-step fallback also honors it
    (the log row's `author` must be the caller). A `"mine"` step whose entry is
    the branch head takes today's restore path unchanged; otherwise it reverts.
    Redo of a revert reverts the revert — the entry carries `undone_by` /
    `applied_by` so a re-applied change is never redone by a tree restore.
  - Refusal semantics: a `ConflictError` out of `revert` puts the entry back at
    its original index on the caller's stack, and the opposite stack is only
    appended to **after** git succeeded. `bus.publish` still happens inside
    `in_restore`, so the service's snapshot hook does not stack a commit on the
    step's own commit.
- **`agentcad/core/tools_undo.py`** — `undo`/`redo` take `scope` (enum
  `any|mine`, default `any`). Descriptions state that `"mine"` is best-effort
  over one shared linear history and that an overlapping later change is a
  refusal, not a merge. `get_history`'s description documents `mine`.
- **`agentcad/core/tools_history.py`** — `project_history`'s description
  documents the new `author` field and that it is self-asserted bookkeeping,
  not authentication.
- **`agentcad/server/routes_undo.py`** — both routes read an optional
  `{scope}` from the body (whitelisted, bytes-checked so a body-less POST still
  means `"any"`; the browser's existing `api.undo(proj)` sends no body and is
  unchanged). An unknown scope is left for the cursor to reject, so the tool
  and the route refuse identically.

## Files
- `agentcad/core/history.py` — trailer helpers, `log().author`, `revert()`,
  `UndoCursor` authorship + `scope` + the revert step
- `agentcad/core/tools_undo.py` — `scope` on `undo`/`redo`
- `agentcad/core/tools_history.py` — `author` in the `project_history` docs
- `agentcad/server/routes_undo.py` — optional `{scope}` body
- `tests/test_undo_authors.py` — new, 15 tests, `portability`

## Notes
- **Deliberate gap: no browser affordance for `scope: "mine"`.** The toolbar's
  `#undo-btn` and Cmd+Z still send no scope, i.e. `"any"` — the shared stack —
  which is the behavior the flagship loop needs. Per-client undo is an
  API/agent surface in this slice; giving it a hidden modifier gesture in the
  toolbar was not worth the discoverability cost, and it is named as a gap in
  the user guide rather than half-shipped.
- Merge commits reach the undo stack (`merge.py` calls `on_snapshot`), which is
  why `revert` handles the two-parent case explicitly instead of letting git
  refuse with "is a merge but no -m option was given".
- `git revert` needs a clean work tree; every mutation in this system
  snapshots, so it is. If it is dirty for some other reason, git refuses, no
  unmerged paths exist, and the caller gets a `HistoryError`-derived
  `validation_error` rather than a fabricated conflict.
- Verification: `uv run pytest tests/test_undo_authors.py -q` → **15 passed**;
  `tests/test_history.py tests/test_branches.py tests/test_versioning_api.py
  tests/test_merge.py` → **130 passed**; `tests/test_proposals.py
  tests/test_locks.py tests/test_claims.py tests/test_prd001_acceptance.py
  tests/test_prd002_acceptance.py` → **105 passed**. No pre-PRD-008 test file
  was modified — the only test change in this slice is the new
  `tests/test_undo_authors.py`.
