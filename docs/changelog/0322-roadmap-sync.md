# 0322 — Roadmap: sync the sequencing narrative with the parallel landings

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (orchestrator) / Nikita Fedorov

## Summary
Docs-only. The "Demoted behind that chain" paragraph still described
PRD-017 as unshipped depth-tier work; 017 (PR #31) and 029 (PR #33) landed
in parallel with 027 (PR #34) and each close-out updated only its own row.
The paragraph now lists the shipped depth tier (013/014/015/028/017), the
shell/navigation pair (026/027), and 029.

## Changes
- One paragraph in the "Resulting order" section rewritten; no status-model
  or index-row changes (those were already correct, verified row-by-row
  against `docs/prd/completed/`). The bottom-of-file tool count (109/112)
  was re-measured against `build_registry` and is accurate — untouched.

## Files
- `docs/roadmap.md` — the sequencing narrative paragraph
- `docs/changelog/0322-roadmap-sync.md` — this entry

## Notes
No code, no tests. `make test` not run for a docs-only narrative edit; the
newest code-bearing entry (0321) carries the last measured count — this
note exists so the count-guards read an explicit statement: prior merged
tree measured 5638 passed (0319).
