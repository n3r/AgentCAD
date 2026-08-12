# 0112 — PRD-008 slice 1: the comment store and the thread lifecycle

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
First slice of PRD-008 (anchored review threads): `agentcad/core/comments.py`
adds a `CommentStore` (files) and a `CommentManager` (anchors, replies,
resolve/reopen, edits) storing threads at `.history/agentcad/comments/` —
inside GIT_DIR, branch-free, and therefore structurally beyond
`project_restore`'s reach, which is AC8 by construction rather than by
vigilance. Additive only: nothing existing calls it yet (the tool pack, the
route pack and the events are slice 3).

## Changes
- **New module `agentcad/core/comments.py`.** Modelled line for line on
  PRD-002's `proposals.py`: atomic `thread.json`/`index.json`/`next_id`
  writes, an append-only `audit.jsonl`, a rebuildable index, and ids from a
  persisted high-water mark so a hand-deleted directory cannot hand its id to
  the next thread. `actor_kind` is **imported** from `proposals.py`, not
  re-implemented.
- **Storage layout** `.history/agentcad/comments/{next_id, index.json,
  <id>/thread.json, <id>/audit.jsonl}`, always via `store.canonical_path_of`.
- **Lifecycle.** `create` (root comment + anchor) · `reply` (per-thread
  sequential comment id, bumps `updated`; a resolved thread still takes
  replies) · `resolve`/`reopen` (idempotent — a no-op records nothing — and
  each records actor, `actor_kind` and ts) · `edit_comment`/`delete_comment`
  (author-only; the root comment cannot be deleted; a delete leaves
  `deleted: true, body: null`; an edit audits `previous_sha256`, never the
  previous text) · `get` · `list(state, kind, part_id, branch)` returning
  `{threads, counts}`. Every mutating call returns the post-state thread.
- **Anchor validation (FR1), immutable once written.** `part`, `param` and
  `instance` validate against the manifest / the part's PARAMS spec / the
  assembly, and an unknown target is a `validation_error` carrying the known
  set. All six kinds are registered in one table; `face`, `script_range` and
  `proposal_hunk` raise `NotImplementedError` *inside* the private table,
  which the public path converts into a `validation_error` naming the
  supported set — so no partial anchor surface leaks before slices 2 and 4.
  Anchor keys are whitelisted per kind (`part_id`/`instance_id` accepted as
  aliases), and `branch`/`head` provenance is stamped by the module, never
  taken from the caller.
- **Attachments (FR8/AC9).** Accepted as an absolute path (what `render_view`
  returns) or as `exports/…`; both sides `resolve()`d before comparison, so
  `..`, an absolute path outside the tree and a symlink inside `exports/`
  pointing out of it are all refused, as is a file that does not exist at
  creation. Stored as one project-relative POSIX path, capped at 8. At read
  time a missing file renders as `{path, available: false}` — never an error,
  because `exports/` is branch-scoped.
- **Param anchors use `service._params_spec`, never `get_part`** — `get_part`
  calls `_ensure_built`, which would turn opening a comment into a 300 s
  build; `inspect` only imports the script and is content-hash cached.
- **New test module `tests/test_comments.py`** (30 cases, 8 sections): store
  layout and round-trip, id monotonicity across a hand-deleted directory and a
  lost index, index rebuild from the directories (missing and corrupt), the
  audit log's byte-prefix stability across three mutations, attribution,
  anchor validation, attachment refusals (**AC9**), the lifecycle rules,
  listing/counts, and — marked `integration`/`portability` — that a thread
  survives `project_restore` unchanged (**AC8**) and that
  `git status --porcelain` stays clean after creating one.

## Files
- `agentcad/core/comments.py` — new: `CommentStore`, `CommentManager`,
  `STATES`/`ANCHOR_KINDS`/`ACTIONS`/`MAX_*`
- `tests/test_comments.py` — new: 30 cases covering the above
- `docs/changelog/0112-prd008-comment-store.md` — this entry

## Notes
- **Deliberately not in this slice** (they belong to the plan's later slices,
  and landing them early would spread the surface before its tests exist):
  `comment_changed` publishing from the manager (slice 3, where the tool pack
  and the WS assertions live), anchor *resolution* into
  `ok`/`moved`/`orphaned`/`unverified` (slice 2), `notifications.jsonl` and
  mentions (slice 5). `list`'s `counts` is therefore `{open, resolved}` only —
  `orphaned` joins it when resolution exists, rather than being reported as a
  hardcoded `0` that would be a lie.
- `MAX_SNIPPET_LINES`/`MAX_SNIPPET_BYTES` are defined here (they cap evidence
  stored *in the anchor envelope* this module writes) but are first read by
  slice 2's `core/anchors.py`.
- No core edits: `service.py`, `app.py`, `tools.py` and `worker.py` are
  untouched, and so are `proposals.py` and every other completed PRD's module.
- The manager reads `service.branches` inside its methods, never in
  `__init__` — `tools_comments` (`c`) will load before `tools_versioning`
  (`v`). Without git or without the versioning pack the anchor's `branch` and
  `head` are `""` and everything else works unchanged.
- Verification: `uv run pytest tests/test_comments.py -q` → **30 passed**;
  `uv run pytest -q -x tests/test_proposals.py tests/test_history.py` → **78
  passed**; `make test-fast` → **924 passed, 1 skipped**; `make test` →
  **1213 passed, 1 skipped** (the 1183/1 baseline from 0111 plus this slice's
  30 cases, with no pre-existing test file modified).
