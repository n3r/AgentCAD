# 0071 — Branching: acceptance criteria verified, docs updated, PRD-001 closed out

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Claude (PRD-001 slice 5)

## Summary
Slice 5, the last of PRD-001: one named acceptance test per criterion
(AC1–AC7) over the real service/registry/kernel, and every documentation
surface the feature changed — the agent API (10 new tools, the `ref` argument,
the `merge_conflict` error type, a worked branch→edit→merge loop), the user
guide (branch switcher, versions dialog, merge flow and conflict view), the
architecture doc (the `.history/` layout, the branch-resolver seam, the merge
pipeline, the new events), AGENTS.md's traps, the README, and the PRD itself
(status + the five divergences folded back from the design spec). No
production code changed in this slice.

## Changes
- **`tests/test_prd001_acceptance.py` (new, 7 tests)** — the contract layer
  over the unit suites, with a module docstring mapping AC → test:
  - `test_ac1_disjoint_parts_merge_clean` (`slow`) — the **rocketry example on
    a copy** (`examples/` never mutated): a branch edits `parts/flange.py`
    while the default branch edits `parts/nozzle.py`; the merge takes zero
    conflicts, `validation.ok` is true, the merge commit's parents are
    `[target_head, source_head]` (checked both in the payload and via
    `git rev-list --parents`), both edits are on disk, and **every** part of
    the merged project builds green with positive volume.
  - `test_ac2_script_conflict_resolved_by_tools_only` — both branches rewrite
    the same `Box(...)` line: `merge_conflict` names the part with
    ours(target)/theirs(source)/base and diff3-marked text, the target's script
    is untouched while the merge is staged, and the conflict is resolved
    **only** through `resolve_merge {"content": …}` (no filesystem writes by
    the test), completing the merge with a validation report.
  - `test_ac3_param_vs_script_merges_clean` — script edit vs `set_params` on
    one part: zero conflicts, both land, and the rebuilt geometry reflects both
    (12 × 12 × 24 mm).
  - `test_ac4_interference_blocks_then_lands_with_allow_invalid` — hand-built
    two-cube assembly where each branch's move is innocent alone; the merged
    positions overlap, so the merge is blocked with `box_1`/`pin_1` named in
    `validation.interference.new_pairs`, the target ref does not move, the
    merge stays staged, and `allow_invalid: true` then lands it with the same
    pair named in the result **and** in the commit message
    (`Validation: FAILED … allow_invalid`).
  - `test_ac5_tag_round_trip_and_survives_branch_delete` — `version_tag` →
    mutate a script, a param, and a second branch → `project_restore` **by tag
    name**: `project.json` and every `parts/*.py` come back byte-identical
    (whole-directory comparison, not one file); after `branch_delete` the
    version is still listed at the same commit and still restores.
  - `test_ac6_browser_session_evidence_is_recorded` — AC6 was driven for real
    in slice 4; this asserts the session's evidence is on the record in
    `docs/changelog/0070-branching-ui.md` (a named check that fails if the
    evidence is removed) rather than re-driving a browser from the suite.
  - `test_ac7_history_and_undo_are_unchanged_on_the_default_branch` — with the
    versioning pack installed but nobody branched: `lock_key` is still the bare
    project name, `path_of == canonical_path_of`, one snapshot per mutation,
    and `undo`/`redo`/`project_restore` behave exactly as before.
  - All tests carry `integration` + `portability` and skip without git (AC1 is
    additionally `slow` with a 600 s timeout), matching `tests/test_merge.py`.
- **`docs/agent-api.md`** — tool count 42 → **52** (55 with `[fem]`, recounted
  from the live registry); a Conventions bullet for the returned-not-raised
  `merge_conflict` type (HTTP 200); a new **"Branches, versions and merges"**
  section with all ten tools, their arguments and returns, the ours=target /
  theirs=source convention, the validation-report shape, the two new events and
  the seven routes; `project_history` gains its `ref` argument and
  `project_restore` its branch/tag support in the table; and a new worked **v4
  example** (branch → switch → edit → tag → merge → conflict → `resolve_merge`)
  with the four rules that keep the loop safe.
- **`docs/user-guide.md`** — the branch switcher in the Toolbar section, and a
  new surface-by-surface **"Branches, versions and merges"** section: per-client
  switching, New branch…, the versions dialog (tag, restore, immutability), the
  merge modal's three outcomes (clean report / conflict view with
  ours-theirs-base-edit and partial resolution / blocked validation with "Land
  anyway"), what merges how (text scripts vs key-wise manifest), and the
  plain-git escape hatch. `Where files live` gains a `.history/` row (with
  `trees/` and `agentcad/`) and notes that `.cache/` is shared across branches.
- **`docs/architecture.md`** — `52 tools` in the process diagram; component
  rows for `core/history.py`, `core/branches.py`, `core/manifest_merge.py` and
  `core/merge.py`; the tool/route-pack rows updated; and a new **"Branches,
  versions and merges"** section covering the fifth seam
  (`ProjectStore.branch_resolver`), the on-disk layout, the resolution order,
  why `.cache/` and `lock_key` behave as they do, the six-step merge pipeline
  (merge-tree → manifest driver → staged worktree → pinned validation pass →
  two-parent `commit-tree` + CAS `update-ref`), the new events, and the git
  2.38 requirement.
- **`AGENTS.md`** — a "Branching gotchas" section: `.history/trees/` vs git's
  own `worktrees/`, ours = target, `.cache/` canonical and shared,
  `store.lock_key` instead of the project name, the resolver seam, the manifest
  never merging line-wise, and all-git-through-`ProjectHistory._run`.
- **`README.md`** — a branching capability bullet; tool count 42 → 52 (55 with
  `[fem]`) in the capabilities paragraph and the docs link.
- **`docs/prd/in-progress/PRD-001-branching-version-control.md`** — Status is
  now "implemented — MVP complete, AC1–AC7 verified"; a Verification paragraph
  naming the test file and the AC6/AC7 evidence; and an **"As built —
  divergences from this PRD"** section folding back the five design-spec
  divergences (worktree path, kernel affinity untouched, new-pairs-only
  interference, the additive `merge_status`, the content-hashed reference cache
  signature) plus the returned-not-raised `merge_conflict`, the git 2.38 floor
  and the MVP conflict-list scope. The file stays in `in-progress/` — it moves
  to `shipped/` when the branch merges.
- **`docs/roadmap.md`** — the PRD-001 row's link now points at `in-progress/`
  (it still said `pending/`) and its status reads "in progress (MVP
  implemented, AC1–AC7 verified)".

## Files
- `tests/test_prd001_acceptance.py` — new: one named test per AC1–AC7
- `docs/agent-api.md` — tool counts, `merge_conflict` convention, the branching
  tool section, `ref` on the history tools, the v4 worked loop
- `docs/user-guide.md` — branch switcher, the branching/versions/merge section,
  `.history/` in "Where files live"
- `docs/architecture.md` — components, the branching section, tool count
- `AGENTS.md` — branching gotchas
- `README.md` — capability bullet, tool counts
- `docs/prd/in-progress/PRD-001-branching-version-control.md` — status,
  verification, divergences folded back
- `docs/roadmap.md` — PRD-001 row
- `docs/changelog/0071-branching-docs-and-acceptance.md` — this entry

## Notes
- **Verification.** `uv run pytest -q tests/test_prd001_acceptance.py` →
  `7 passed in 64.32s` (slow tests included). Full suite `make test` →
  `458 passed, 1 skipped in 1086.18s` (451 + 1 before this slice, i.e. the 7
  new tests and nothing else moved). `make test-portability` →
  `185 passed in 116.21s`.
- **AC7 evidence.** `git diff --name-status main -- tests/` across slices 1–5
  lists **only additions**: `A tests/test_branches.py`,
  `A tests/test_manifest_merge.py`, `A tests/test_merge.py`,
  `A tests/test_versioning_api.py` (plus this slice's untracked
  `tests/test_prd001_acceptance.py`). No pre-existing test file was edited by
  any slice of this feature.
- **AC1's validation report lists one rebuilt part, not two.** The pass
  rebuilds what the merge changes *relative to the target*, and the nozzle edit
  was already the target's — so `built == ["flange"]`. The "both edits landed"
  claim is asserted separately, on the working tree and by rebuilding every
  part of the merged project.
- `agentcad/core/templates.py`'s CHEATSHEET was deliberately left alone:
  branching changes nothing about the part-script authoring contract.
- Tool counts were recounted from a live `build_registry` (52 registered
  without the FEM extra, 55 with it), not from the previous doc text.
