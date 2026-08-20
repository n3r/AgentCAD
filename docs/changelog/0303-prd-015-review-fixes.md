# 0303 — 2026-08-20 — PRD-015 review fixes: atomic-resume finalize, approved-state tag, race-free BOM downloads

## Summary

An adversarial code review of PRD-015 returned **CHANGES-REQUESTED, no HIGH** —
the zero-kernel discipline, worktree teardown-owns-only-the-cell contract, cycle
guard, immutability seam, route whitelisting, and XSS-safe frontend all held.
This closes its two MED and three LOW/INFO findings.

## Fixes

- **MED-1 — finalize is now atomic-resumable** (`releases.py`). The tag and the
  record transition are not one op, so a crash after `branches.tag` but before
  the record flips left the release wedged forever (retry re-tags →
  `ConflictError`, and there is no tag-delete tool). Finalize now catches that
  `ConflictError` and treats an already-existing tag as an idempotent **resume**
  (keeping the tag that pinned the approved state), completing the record
  transition. Regression test added.
- **MED-2 — finalize tags the APPROVED state, not the drifted branch**
  (`releases.py`). `branches.tag` auto-commits the working tree, so an
  uncommitted edit or a new commit made after approval would be tagged
  (immutably) while carrying the approval. Finalize now **re-gates**: it re-runs
  `working_tree_clean` and refuses a head that is not among the approve reviews'
  `source_head`s — a `conflict_error` directing to re-approve. Regression test
  added.
- **MED-3 — race-free BOM downloads** (`routes_bom.py`). The `.csv`/`.json`
  routes re-read the tool's single shared `exports/bom.<fmt>`, so two concurrent
  downloads with different params (`?structure=` / `?ref=`) could clobber or
  half-read each other. The routes now render **in-memory** from `get_bom` via
  the pure `bom.to_csv`/`to_json` (byte-identical) — no shared file. The
  `export_bom` tool still writes the one canonical file for agents.
- **LOW-4 — the worktree cell can't leak** (`_worktree.py`). `refuse_work_dir_
  overlap` ran BEFORE the `try:` whose `finally` cleans the `mkdtemp` cell; moved
  inside the `try` so a refusal still tears the cell down.
- **LOW-5 — no absolute paths in the committed manifest** (`releases.py`). The
  bundle summary stored absolute `dir`/`zip` into the git-tracked manifest
  (non-portable, merge churn — the packages "no absolute path" trap). Now
  `_persist_bundle` stores **project-relative** paths and `_hydrate_bundle`
  resolves them back to absolute on read (`get_release`/`list_releases`/finalize
  return), so the API still hands back runtime-usable absolutes.

## Not changed (INFO-6, confirmed intent)

The waiver is **wholesale**, not per-check — one `waive: {reason}` clears every
failing gate check at once. It is durable + attributed (never silent), which is
the documented v1 contract; a per-check waiver is a later refinement, not a
defect.

## Notes

Verified: 11 finalize (incl. the two new MED tests) + bundle + acceptance +
bom-routes/export + 26 checks-ref (LOW-4 unbroken) tests green; the SHIP-modulo-
MED verdict stands — these are integrity/robustness fixes with no behavior change
for a clean, approved release.

`make test` — **4630 passed, 40 skipped** on the pre-merge PRD-015 branch. After
merging main (PRD-024 + PRD-028 landed in parallel; changelogs renumbered
0295-0303), the combined tree measured **5087 passed** — the only non-passing
items are contention flakes that pass in isolation (`test_supervisor`,
`test_sketch_diagnostics`, four `test_simplify` setup-timeouts) and one
`[fem]`-extra real-solver test (`test_prd028…real_solver_static`) that CI skips
via `pytest.importorskip('skfem')` — none touch PRD-015 code. CI on the three-OS
matrix (which runs without the `[fem]` extra) is authoritative.
