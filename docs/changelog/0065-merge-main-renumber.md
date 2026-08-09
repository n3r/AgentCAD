# 0065 — Merge main into world-cad; renumber branch changelogs to 0063/0064

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Brings `world-cad` current with main before the PR: merges main's three
waves that landed after this branch was cut — undo/redo (PR #3), the
assembly-first engine example (PR #4), and the faster test suite (PR #5) —
and resolves the changelog-number collision the same way the v3 branch did:
this branch's entries yield to main's.

## Changes

- **Merge `origin/main` (8b1e008)** into `world-cad`. `AGENTS.md` and
  `README.md` were touched on both sides and auto-merged cleanly (main:
  test targets, tool counts 42/45, five examples, history/undo seams; this
  branch: doc pointers to the PRD index and market research).
- **Renumbered** this branch's changelog entries — `0059-competitive-
  analysis-v4-roadmap` → `0063`, `0060-prd-system-founder-ideas` → `0064`
  (headers updated) — because main claimed 0059–0062 (engine example,
  undo-redo, engine v2/v3, faster suite) in parallel. Note: main carries a
  pre-existing duplicate (`0059-engine-example-v1` and `0059-undo-redo`)
  from its own parallel branches; left untouched here as historical record.
- **Stale-count fixes** post-merge: the tool surface is now 42 (45 with
  `[fem]`) — updated in `docs/roadmap.md` (shipped summary, changelog range
  0001–0062), `docs/market_research.md` (intro + standing section), and
  drift-prone phrasing in `PRD-018`/`PRD-024`.

## Files

- Merge of main's changes (workflows, Makefile, core/undo modules, engine
  example, docs) — see the merge diff.
- `docs/changelog/0063-competitive-analysis-v4-roadmap.md`,
  `docs/changelog/0064-prd-system-founder-ideas.md` — renamed from
  0059/0060, headers updated.
- `docs/roadmap.md`, `docs/market_research.md`,
  `docs/prd/pending/PRD-018…`, `docs/prd/pending/PRD-024…` — count fixes.

## Notes

Docs-only on this branch's side; the merged code (undo/redo, engine
example, test-speedup) is main's already-reviewed work. The 3-OS CI runs on
push and covers the merged tree.
