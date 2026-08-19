# 0270 — PRD-024 AgentCAD-Bench: design spec, PRD moved to in-progress

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (orchestrator) with an Opus design subagent

## Summary
Opens the PRD-024 (AgentCAD-Bench) build on branch `prd-024-agentcad-bench`:
the PRD moves to `docs/prd/in-progress/`, the roadmap row flips to
in-progress, and the full design spec lands under `docs/superpowers/specs/`.
No code changes.

## Changes
- `docs/superpowers/specs/2026-08-19-agentcad-bench-design.md` — 18-section
  design: task bundle = a directory of complete AgentCAD projects
  (`starter/`, `reference/project/`) + rubric (`specs/`, `reference/metrics.json`,
  weights); six subscores with honest `status` (`error` = harness failed to
  measure; `not_applicable` only ever declared by `task.json`); `iou` as a new
  kernel handler pack (`handlers/bench.py`) doing one `&` boolean, solids-sum
  volumes, `union = a + b − inter`, `world|com|bbox_center` alignment + a finite
  rotation list; timestamp-free `score.json` (sorted keys, round 6, NaN refused)
  with `run.json` holding the non-deterministic bits; the built-in runner drives
  `ChatEngine` through a budgeting client-factory wrapper (zero `chat.py`
  edits); CLI `agentcad bench run|score|report|publish` on the PRD-004 headless
  pattern with exit codes 0/1/2 (`report --baseline` is the FR11 gate); 25
  tasks (5 × 5) listed with sources and weights; reference STEP checked in
  **and** regenerable with a CI drift self-test; bench CI as a separate
  workflow with a secret-gated `builtin` job; SDD ledger of rulings.
- `docs/prd/in-progress/PRD-024-agentcad-bench.md` — moved from `pending/`,
  `Status: in-progress`.
- `docs/roadmap.md` — PRD-024 row → in-progress.

## Files
- `docs/superpowers/specs/2026-08-19-agentcad-bench-design.md` — new
- `docs/prd/in-progress/PRD-024-agentcad-bench.md` — moved
- `docs/roadmap.md` — row updated

## Notes
Rulings of record are in the spec's §17 ledger. Out of scope for this PR
(seams designed, not built): FR13 launch results, the `fem/` category, task-set
v2 rotation, PRD-018 wiring, image (PNG) assets in the builtin runner's first
message. `make test` on the branch before this change — 4438 passed, 36 skipped.
