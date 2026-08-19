# 0284 — 2026-08-19 — PRD-024 closed out: AgentCAD-Bench ships, the first v6 "moat" PRD

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (orchestrator)

## Summary

Bookkeeping after PR #26 (AgentCAD-Bench) merged to main as a36d973. The PRD
moves to `docs/prd/completed/` and its roadmap row flips to **completed
(PR #26)** — 024 is the **first of the v6 "moats" tier to ship**, and the
first public, kernel-scored agentic-CAD benchmark anyone publishes.

## What shipped (MVP + FR10–FR12)

- **`agentcad/bench/`** (OCP-free): task loader/schema, the six-subscore scorer
  (built · valid · specs · geometry · interference · metrics), the budgeted
  `ChatEngine` runner, `bench report` + the baseline gate, the leaderboard
  renderer with five fail-closed disclosure rules, the CLI, the authoring helper.
- **`agentcad/kernel/handlers/bench.py`** — the `iou` kernel handler pack (never
  a tool).
- **`agentcad bench run | score | report | publish | prompt`** on the PRD-004
  headless pattern; `report --baseline` exit 1 is the release gate.
- **25 tasks (5 × 5)** under `benchmarks/tasks/`; every reference scores exactly
  1.0 (AC1), every starter well below 0.95; reference STEP checked in and
  re-export-drift-tested.
- **Credibility rules:** `error` = the harness could not measure; a candidate
  that is absent, broken, slow, crashing, mesh-only or wrong measures **zero**;
  rubric injected into a copy, rubric-owned rows only, candidate-inducible
  skips are fails; `score.json` is timestamp/path/host-free and byte-stable.
- **CI:** `.github/workflows/bench.yml` — `selftest` on PRs (all references +
  the bench suite under Linux confinement), a `guard` notice for the missing
  key, the paid `builtin` job only on `main` pushes with the secret.
- **Docs:** `docs/bench.md` + cross-refs in `agent-api`, `architecture`,
  `geometry-ci`, `AGENTS.md`, `CLAUDE.md`.

## Deferred (recorded in the PRD and `docs/bench.md` §seams)

FR13 launch results (real paid runs; `benchmarks/leaderboard/rows/` is empty,
`baseline.json` `total: null`), the `fem/` category, the task-set v2 rotation
policy, PRD-018 wiring, PNG image assets, multi-turn continuation, a voxel-IoU
fallback. Disclosed v1 limits: `assemble_and_clear` grades non-interference,
not placement; opt_001/003/004 bind on the parameter's declared range.

## Product findings raised by the bench (follow-up issues, fenced not fixed)

`handlers/drawing._view_bounds` undersizes curved silhouettes (six points per
edge); a swept pipe surface does not survive the STEP round trip as a boolean
operand; `_edge_svg` discretises a straight line into 256 points.

## Files
- `docs/prd/completed/PRD-024-agentcad-bench.md` — moved from `in-progress/`,
  `Status: completed — merged to main in PR #26 (…)`.
- `docs/roadmap.md` — 024 row → completed (PR #26).
- `docs/changelog/0284-prd-024-completed.md` — this entry.

## Notes
Merged-tree verification on the branch before merge: `make test — 4831 passed,
44 skipped`; PR #26 CI fully green (macOS PR suite, Ubuntu + Windows
portability, Geometry CI ×4, bench self-test). Branch history: changelogs
0270–0283 (renumbered from 0255–0268 after PRD-006b/PRD-014 landed first).
