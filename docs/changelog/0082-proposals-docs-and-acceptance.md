# 0082 — PRD-002 acceptance tests, docs, and the timestamp fold-back

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
Slice 6 of PRD-002, the close-out: one named acceptance test per criterion
(AC1–AC9) walking the real stack, the two slice-4/5 fold-backs decided and
implemented (zone-aware UTC timestamps; the as-built packet/UI shape recorded
in the design spec), and the documentation surfaces — `docs/agent-api.md`,
`docs/user-guide.md`, `docs/architecture.md`, `AGENTS.md`, `README.md` — brought
up to the shipped feature, with the tool count recounted from a live
`build_registry`. The PRD's `Status:` is now *implemented* with an AC → test
table; it stays in `docs/prd/in-progress/` (the orchestrator moves it and
updates the roadmap row when the branch lands).

## Changes
- **New `tests/test_prd002_acceptance.py`** — 11 tests, the contract layer over
  the unit suites, mirroring `tests/test_prd001_acceptance.py` (the `| AC |
  Test |` table lives in the module docstring). Everything carries
  `integration` + `portability` and skips without git; the geometry cases are
  `slow`.
  - `test_ac1_roundtrip_agent_proposes_human_merges` — on a **copy** of
    `examples/rocketry`: the agent identity (`chat:main`) branches, edits the
    nozzle and opens a proposal *through tools*; the human half runs entirely
    *through the HTTP routes* (list, detail, packet, a PNG render, approve,
    merge) with no service or manager call; then the audit is asserted —
    `created`/`packet_generated` as `agent`, `reviewed`/`merged` as `human`.
  - `test_ac2_packet_generates_warm_under_10s` — timed warm regeneration on a
    rocketry copy, asserting all five kinds of evidence including the shared
    render frame and the `{"field": "value"}` PARAMS row.
  - `test_ac3_drilled_hole_reports_removed_volume` — `removed_mm3` within 1 %
    of π·3²·20, `added_mm3` exactly 0, a parseable ACM1 solid on disk **and**
    over the asset route.
  - `test_ac4_instance_move_does_no_per_part_kernel_work` — respects slice 4's
    caveat: an instance move alone produces no part rows, so the change also
    carries a manifest-only relabel, and the assertion is made on that real
    part row (`changed_by == ["manifest"]`, `geom_diff.unchanged is True`)
    *plus* a kernel-call counter containing no `build`/`geom_diff`.
  - `test_ac5_failed_validation_blocks_then_overrides` — the target also moves,
    so the merge is a real two-parent merge and the override is asserted in all
    three places (audit `override` entry, `proposal.merge.allow_invalid`, the
    commit message).
  - `test_ac6_self_approval_does_not_satisfy_policy` — zero approvals and the
    author's own approval are both `conflict_error`s carrying the failing gate
    with its policy details; `allow_invalid` does not bypass it; a second
    identity's approval does.
  - `test_ac7_unbuildable_side_degrades_honestly`, 
    `test_ac8_second_client_sees_proposal_changed_live` (one WS connection,
    create → review → merge, each transition asserted whole — the proposal is
    opened with an `X-Agent-Id` header so the browser's approval is a second
    party's), `test_ac9_project_restore_does_not_rewind_proposals` (both
    sidecar files byte-identical, audit still valid JSON lines).
  - `test_ac1_browser_half_evidence_is_recorded` /
    `test_ac3_browser_overlay_evidence_is_recorded` — the browser halves of AC1
    and AC3 were driven for real in slice 5; these assert that record is
    present in `docs/changelog/0081-proposals-ui.md` rather than re-driving a
    browser (the PRD-001 AC6 pattern).
- **Fold-back 1 — timestamps are zone-aware UTC** (slice 5 raised it, slice 6
  decided it). `proposals._now()` now returns `%Y-%m-%dT%H:%M:%SZ`, and
  `packet.py` uses that same helper for the packet's `generated` instead of its
  own naive `time.strftime`. Everything a client reads —
  `created`/`updated`, every audit and review `ts`, `generated` — is now
  unambiguous to `Date.parse` and `datetime.fromisoformat`. The JSON shape is
  otherwise identical, nothing in the codebase parses these stamps, and the
  UI's `ago()` drops the compensating `Z`-tagging it needed. Written test-first:
  both new tests failed on the naive format before the change.
- **Fold-back 2 — the design spec records the as-built packet and UI.** An
  "As built" paragraph in Decision 4 (the `params_diff` `"field": "value"` row
  and the per-spec-field case, `changed_by: "manifest"`, `assembly.renders:
  null`, zone-aware stamps), five tabs and the real action list in Decision 9,
  and four more entries (9–12) in "PRD divergences to fold back".
- **`docs/agent-api.md`** — the tool count recounted from a live
  `build_registry` (**60**, 63 with the `[fem]` extra; was 52/55), a new
  **Change proposals** section next to "Branches, versions and merges" with all
  eight tools and their arguments, the packet shape, the four consumption rules
  (renders are URLs; packet-internal failures are payload fields; unchanged
  parts cost nothing; `stale` is honest), gating/policy, attribution (and that
  it is bookkeeping, not authentication, until PRD-005), the event and the
  routes — plus a second v4 worked loop, **branch → edit → propose → packet →
  review → merge**, with the traps that matter to an agent (your own approval
  does not count; never fake a state; address feedback then reopen).
- **`docs/user-guide.md`** — the Proposals toolbar entry, and a **Change
  proposals** section surface by surface: the list and its filter chips, the
  create form, the header actions, the five tabs (including the viewport
  overlay and its legend), degradation-as-evidence, the approvals rule, the
  conflict hand-off, and slice 5's **staged-merge-left-behind** note (decline
  "Merge anyway" ⇒ complete or abort the staged merge). Plus a
  `.history/agentcad/proposals/<id>/` row in "Where files live".
- **`docs/architecture.md`** — `proposals.py` and `packet.py` rows in the
  component table, `diff` in the handler-pack list, `proposals` in the tool- and
  route-pack lists, the diagram's tool count, and a **Change proposals** section
  after the branching one: the sidecar layout, why `audit.jsonl` is appended
  rather than replaced, the seven-step packet data flow, the `geom_diff` handler
  (the `-` operator, the solids-sum volume, the mesh skip), and that the merge
  is PRD-001's unchanged.
- **`AGENTS.md`** — a "Proposal gotchas" section beside the branching one:
  proposals are canonical and branch-independent; `audit.jsonl` is appended;
  the packet degrades and never raises; `geom_diff` volume and mesh rules;
  renders need the explicit frame; `allow_invalid` is the kernel gate only;
  `actor_kind` is `human` only for `browser`; packets refuse a dirty tree.
- **`README.md`** — a "Change proposals (CAD pull requests)" bullet in the
  feature list and the two tool counts.
- **PRD** (`docs/prd/in-progress/PRD-002-…md`) — `Status:` → *implemented*, a
  **Verification** subsection with the AC → proving-test table and the browser-
  half evidence note, an **As built** list of all twelve divergences, and the
  divergences folded into the body (the sidecar path, the branch worktrees, the
  eight tools, five tabs, plain-DOM diffs).
- **Plan** (`docs/superpowers/plans/2026-08-10-change-proposals.md`) — Slice 6's
  five steps checked, with the deviation recorded: the PRD stays in
  `in-progress/` on the branch.

## Files
- `tests/test_prd002_acceptance.py` — new: the 11 acceptance tests
- `tests/test_proposals.py` — `test_every_timestamp_is_zone_aware_utc` (new)
- `tests/test_packet.py` — `test_the_packet_stamp_is_zone_aware_utc` (new)
- `agentcad/core/proposals.py` — `_now()` emits the `Z` designator
- `agentcad/core/packet.py` — imports that helper for `generated`
- `frontend/js/proposals.js` — `ago()` no longer tags naive stamps
- `docs/agent-api.md` · `docs/user-guide.md` · `docs/architecture.md` ·
  `AGENTS.md` · `README.md` — the documentation surfaces above
- `docs/prd/in-progress/PRD-002-change-proposals-geometric-diff.md` — status,
  verification, as-built divergences
- `docs/superpowers/specs/2026-08-10-change-proposals-design.md` — as-built
  packet/UI shape, divergences 9–12
- `docs/superpowers/plans/2026-08-10-change-proposals.md` — Slice 6 checked

## Verification
- `uv run pytest -q tests/test_prd002_acceptance.py` → **11 passed in 26.20 s**
  (slow cases included; the rocketry pair is 12 s of that).
- `uv run pytest -q -m portability tests/test_prd002_acceptance.py` → **11
  passed** — every test in the file carries the marker, so the Linux/Windows
  CI group runs all of it.
- Full `make test` → **628 passed, 1 skipped** in 18:52 (615 passed, 1 skipped
  before this slice: +11 acceptance, +2 timestamp).
- `git diff --name-status main -- tests/` → only `A` rows: no pre-existing test
  file was edited. The two extended files (`tests/test_proposals.py`,
  `tests/test_packet.py`) were themselves created on this branch, by slices 1
  and 4.
- Both new timestamp tests were confirmed **failing** on the naive format
  before `_now()` changed (`AssertionError: 2026-08-10T09:52:36`), then passing
  after. `node --check frontend/js/proposals.js` clean.

## Notes
- **Why the timestamp fix and not a UI-side patch.** The UI already
  compensated (`ago()` appended a `Z`), but every *other* consumer — an agent
  reading the audit log, a future CI check, `datetime.fromisoformat` — would
  have had to make the same guess. A stamp that does not say what zone it is in
  is a bug in the record, not in the reader. Second resolution and the ISO
  shape are unchanged, so no stored packet or audit line is invalidated: old
  entries simply lack the `Z`.
- **The PRD is not moved to `docs/prd/completed/` here**, and the roadmap row
  is untouched, deliberately: PRD-001 did that in its own commit after the
  branch merged (`0076`), and doing it on the branch invites a conflict with
  whatever else lands in `docs/roadmap.md`.
- **AC4 is asserted twice on purpose.** `tests/test_packet.py` already had the
  zero-kernel-work case; the acceptance copy exists because the criterion is a
  contract, and it repeats the manifest-only-edit trick so the short circuit is
  proved on a real part row rather than on an empty list.
- **AC2 measures the same budget as slice 4** (0.97 s warm there); the
  acceptance run reproduces it end to end through the tool surface rather than
  through `PacketBuilder` directly, which is the only difference.
- No `uv sync`; no changes to `worker.py`, `tools.py`, `app.py`, `service.py`,
  `merge.py`, `branches.py` or `history.py`.
