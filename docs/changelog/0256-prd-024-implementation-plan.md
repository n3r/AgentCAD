# 0256 — PRD-024 AgentCAD-Bench: implementation plan

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (orchestrator) with an Opus planning subagent

## Summary
Adds the 11-task implementation plan for PRD-024, derived from the design spec
(changelog 0255). Docs only.

## Changes
- `docs/superpowers/plans/2026-08-19-agentcad-bench.md` — tasks: (1) bench
  package skeleton + task loader + authoring helper + seed task; (2) kernel
  `iou` handler pack; (3) scorer; (4) `bench score` CLI + the two `cli.py`
  edits; (5) runner + budgeted client; (6) `bench report` + baseline gate;
  (7) `bench publish`; (8–10) the remaining 24 tasks in three authoring
  slices; (11) acceptance tests, `bench.yml` workflow, docs.

## Files
- `docs/superpowers/plans/2026-08-19-agentcad-bench.md` — new

## Notes
Changelog numbers quoted inside the plan (0256–0266) are one behind after
this entry; the orchestrator assigns the real next number at each commit.
`make test` baseline on the branch: 4438 passed, 36 skipped.
