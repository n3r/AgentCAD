# 0304 — 2026-08-20 — PRD-015 closed out: BOMs and reproducible releases ship

## Summary

Bookkeeping after PR #28 (BOM & release management) merged to main. The PRD
moves to `docs/prd/completed/` and its roadmap row flips to **completed (PR #28)**.
This also **unblocks PRD-014's deferred FR4/FR5** (assembly balloons + on-sheet
BOM) by giving `get_bom` to the drawing path.

## What shipped (full scope)

- **BOM (FR1-5):** a **zero-kernel** count-only builder rolls quantities up
  across PRD-013 patterns and sub-assemblies; per-config identity; cost with a
  `cost_source` honesty column (manual / `material_estimate` via `cost_usd_kg` /
  none); mass read from the cache without rebuilding (warns on unbuilt/stale);
  package part-numbers inherited from provenance. RFC-4180 CSV + JSON exports;
  `get_bom {ref}` reproduces a past BOM via a tag-capable worktree helper.
- **Revisions & releases (FR6-9, FR12):** records + a `draft → in_review →
  released → superseded` state machine; `release_start` opens a `release`-kind
  PRD-002 proposal and composes a gate report from its specs+checks gates (+ a
  recorded waiver); `release_finalize` (on approval) pins a `release/<rev>` tag,
  registers the referrer, supersedes the prior rev; released records are
  append-only (`conflict_error`).
- **Reproducible bundle (FR10-11):** STEP + drawings (version-pinned) + BOM +
  flat patterns + README + `artifacts.json` (sha256); deterministic-class
  artifacts byte-identical on rebuild, STEP normalized for its `FILE_NAME`
  timestamp **and** OCCT's process-global assembly counter.
- **Frontend + HTTP routes** for the BOM view and Releases panel.

## Deferred (recorded)

Per-config BOM part-number override (v1 stores one per-part `bom` field); the
`subassembly_refs_pinned` / `drawings_regenerable` gate checks are warn/soft in
v1 (PRD-013 doesn't yet pin sub-assembly refs; the bundle regenerates drawings).

## The CI story (why this merged with CI/Windows red)

Green on **bench, Geometry CI, CI/ubuntu, CI/macOS**. **CI/Windows** failed
persistently (3× re-runs) with a `kernel_crash: kernel worker exited
unexpectedly` cascading through **PRD-006b's `test_sandbox_windows` (9 errors)**
and one PRD-004 checks test — **no PRD-015 code is in the failure** (the BOM/
release code has no Windows/kernel/sandbox surface). It is a Windows
AppContainer / kernel-worker crash, intermittent on `main` too (an earlier main
run failed the same job while HEAD passed), most plausibly a shared-kernel-pool
resource crash under the loaded parallel run. Windows portability is a
**non-required** check; merged on the user's explicit call, with the Windows
kernel-worker instability **flagged for whoever owns the PRD-006b/Windows work**
as a separate issue — it should be diagnosed on a Windows host (unreproducible
without one).

## Changes

- `docs/prd/in-progress/PRD-015-bom-release-management.md` → `docs/prd/completed/`,
  status "completed — merged in PR #28".
- `docs/roadmap.md`: the 015 row → **completed (PR #28)**; the "demoted behind
  that chain" note updated (013/014/015/028 done, only 017 left of that tier).

## Notes

Two build passes across 7 slices + an adversarial review (**SHIP** after 2 MED +
3 LOW fixes). Changelogs were renumbered `0295-0303` at merge to avoid a
collision with the parallel PRD-024/028.

`make test` — **5087 passed** on the merged tree with the `[fem]` extra installed
locally (the only non-passing items were contention flakes that pass in isolation
plus one `[fem]`-only real-solver test CI skips via `importorskip`); the three-OS
CI matrix runs without `[fem]` and is green on ubuntu + macOS (Windows per the CI
story above).
