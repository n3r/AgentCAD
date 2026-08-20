# 0306 — 2026-08-20 — PRD-015 follow-up: adopt the BOM/Releases modals onto the PRD-026 shell

## Summary

The PRD-015 Slice 6 frontend (bom.js / releases.js) predated the PRD-026 shell
contract landing on `main`; the merge exposed two real regressions the PR CI
couldn't see (PRD-026's tests weren't on `main` at PR time). Both are fixed here.

## Fixes

- **Native dialog removed** (`releases.js`): "Cut release…" used a native
  `prompt("Release notes (optional):")`, which `test_no_native_dialogs_remain`
  (PRD-026 AC1) forbids. Replaced with `dialogs.prompt({...})` (a textarea in the
  shell modal); **cancel now aborts the cut** rather than silently proceeding
  with no notes — a UX improvement over the old `|| undefined`.
- **Modals adopted onto the dialog stack** (`bom.js`, `releases.js`): the
  `bom-modal` and `releases-modal` overlays were on no dialog stack, so
  `isModalOpen()` read false behind them and `F`/`G`/`R`/`?` would fire through
  them. Each now calls `dialogs.attachLegacy(overlay, {view, …})` in `init`,
  `notifyOpen()`/`notifyClose()` on open/close, and **drops its own
  `document`-level Escape listener** (the shell owns Esc + the focus trap now) —
  the exact three-part adoption the other nine modals use.
- **`tests/test_frontend_shell.py`**: `ADOPTED_MODALS` gains `"bom": "bom.js"`
  and `"releases": "releases.js"`, so the overlay-closure invariant covers them
  and the parametrized adoption test verifies all three parts of each.

## Notes

Frontend + one test-list edit; no Python behavior changes. Verified:
`test_frontend_shell.py` + `test_prd026_acceptance.py` — **257 passed**;
`node --check` clean on both modules. This is the macOS-CI failure that surfaced
after PRD-015 (PR #28) merged; the Windows leg that also flagged it is now
dropped (0305).

`make test` — **5089 passed** (the merged tree's 5087 + the two parametrized
`test_every_legacy_overlay_is_adopted_and_registered[bom|releases]` cases this
adds; frontend + test-list only, no product behavior change). CI on ubuntu +
macOS is authoritative.
