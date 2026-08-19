# 0298 — 2026-08-20 — PRD-015 slice 3: release records, the gate, and release_start

## Summary

Slice 3 of BOM & release management — revision records + the state machine and
`release_start` (FR6-8): a release opens a PRD-002 proposal, the specs + CI
gates evaluate for free, `release_start` composes a gate report, and a red gate
leaves the release `draft` (or a recorded waiver proceeds knowingly).

## Changes

- **`agentcad/core/releases.py`** (new, pure Python, no OCP, no kernel calls):
  the record `manifest["releases"][<rev>] = {name, rev, status, tag, proposal,
  notes, approvals, waiver?, gate, bundle}`; `status ∈
  draft|in_review|released|superseded` (this slice reaches `in_review`; finalize
  is slice 4); `rev` auto-sequences spreadsheet-style (`A→…→Z→AA`). `release_start`:
  cuts from a non-default branch, allocates the next rev, opens a `release`-kind
  proposal (whose installed specs+checks gate providers evaluate during
  `create`), then `_gate_report` composes `{status, checks, waiver}` from those
  gates **plus** three release checks — `working_tree_clean` (`git status
  --porcelain`, computed before the record write), `subassembly_refs_pinned`,
  `drawings_regenerable`. Red → record stays `draft` (report returned, not
  raised); green → `in_review`. `list_releases`/`get_release`.
- **`agentcad/core/proposals.py`**: additive `kind: str = "change"` on `create`
  (validated against `("change","release")`), carried into the object and
  `_summary` (default for pre-PRD-015 proposals). Review/approval flow untouched.
- **`agentcad/core/tools_proposals.py`** + **`agentcad/server/routes_proposals.py`**:
  forward `kind` (whitelisted key + schema, never `**body`).
- **`agentcad/core/tools_releases.py`** (new): `release_start {project, notes?,
  waive?}`, `list_releases {project}`, `get_release {project, rev}`; self-disables
  without git; loads after `tools_proposals`; registers no gate provider.
- **`agentcad/core/manifest_merge.py`**: `"releases"` → `_ENTRY_DICTS` (per-rev
  atomic merge — two branches releasing different revs merge clean, a same-rev
  edit conflicts).
- **`tests/test_releases.py`** (new, 15): AC4 (failing spec → red draft with the
  check named; a waiver proceeds and is recorded), rev auto-sequence + Z→AA
  rollover, the `release` proposal kind (+ default `change`), `get_release`
  round-trip, and the per-rev merge.

## Waiver (FR8)

`waive: {reason}` records a durable `{reason, principal (current_client_id),
principal_kind, ts}`; each failing check is marked `waived: true` and stops
blocking, but stays in the report — silent override is impossible. Empty/non-dict
waive → validation_error.

## Notes (documented v1 deviations)

- `subassembly_refs_pinned` **warns, never fails**: PRD-013 reserves `version`
  on a sub-assembly ref for a later phase, so a floating ref can't yet be pinned
  — blocking would make any sub-assembly project un-releasable. It names the
  floating instances; a later phase tightens warn→fail.
- `drawings_regenerable` is a **soft pass**: a real probe needs a `generate_drawing`
  kernel call this zero-kernel path avoids; the bundle regenerates drawings
  deterministically in slice 5 and this check documents that intent.

Verified: 15 release tests + 110 proposals/specs-gate/api tests (the `kind`
addition is additive) + the merge change safe; OCP boundary clean.

`make test` — **4589 passed, 38 skipped** (green total; a contended 15-min run
measured 4580 with the 9 count guards + 3 timing flakes — a sketch-budget test
and the two `test_supervisor.py` RSS-killer tests, all `3 passed` in isolation,
none touched by this slice's pure-Python releases code).
