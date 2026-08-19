# 0277 — 2026-08-20 — PRD-015 slice 7: acceptance suite + documentation

## Summary

Slice 7 (final) of BOM & release management — the consolidated acceptance suite
and the docs. This closes the buildable PRD-015 scope and (by giving `get_bom`
to the drawing path) unblocks **PRD-014's deferred FR4/FR5** (assembly balloons +
on-sheet BOM).

## Changes

- **`tests/test_prd015_acceptance.py`** (new) — 9 machine-checked + 1
  deferred-skip:
  - **AC1** a release cut end to end (`release_start` green gate → `proposal_review`
    approve → `release_finalize`): record `released`, `release/<rev>` tag exists,
    approval carries principal + ts, bundle present + zipped.
  - **AC2** a twice-instanced sub-assembly with an 8-member bolt pattern → one
    screw line `qty: 16`, flat == indented totals (through `get_bom`).
  - **AC3** CSV lossless under a strict `csv.reader` (comma+quote label
    round-trips; CSV totals == JSON).
  - **AC4** a failing spec blocks `release_start` (the check named), a waiver
    proceeds and is recorded/attributed in `get_release`.
  - **AC5** a finalized record is immutable (`conflict_error`); a branch off the
    tag edits successfully.
  - **AC6** rebuilding the bundle at the tag is reproducible (deterministic-class
    hashes identical; STEP identical after normalization).
  - **AC7** a three-config flange → three lines with per-config mass (built);
    **deferred-skip**: distinct part numbers per config suffix (slice 1 stores one
    per-part `bom` field).
  - **AC8** a project with no BOM/releases is unchanged; an ordinary proposal is
    still `kind:"change"`.
- **`docs/agent-api.md`** — the 8 new tools (`get_bom`/`export_bom`/
  `set_bom_fields`; `release_start`/`release_finalize`/`release_bundle`/
  `list_releases`/`get_release`), the BOM line/result shape (+ `cost_source`/
  `mass_source`), the release record + state machine, and the proposal `release`
  kind.
- **`docs/user-guide.md`** — the BOM view + the release workflow (cut → gate →
  approve → tag + reproducible bundle; released is locked, branch to evolve).
- **`AGENTS.md`** — a "BOM & release gotchas (PRD-015)" block: zero-kernel
  count-only enumeration + process-lifetime metrics + `cost_source` honesty; the
  tag-capable `materialized_service` vs branch-only `tree_of`; the gate is the
  proposal's gate + `proposal_review` (not `approve_proposal`); the lowercased
  `release/<rev>` tag; structural immutability + `_ensure_mutable`; STEP's two
  normalized fields; the deferrals; the `X-Agent-Id`-string tripwire.

## Notes

The AC1 browser session (zero-terminal UI) and AC8's three-OS-matrix claim are
**evidence-graded** — no Chrome extension was connected this session
(`list_connected_browsers → []`), matching the PRD-012/PRD-014 precedent; every
AC satisfiable by a deterministic test is machine-checked, and each slice's
functionality was independently verified as it landed.

`make test` — **4628 passed, 40 skipped** (clean run; the full suite measured
4619 with the 9 self-referential count guards, green once this count lands; the
extra skip is AC7's deferred per-config-part-number half; suite grew across
slice 7's acceptance tests).
