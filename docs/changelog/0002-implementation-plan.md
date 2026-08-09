# 0002 — AgentCAD implementation plan

- **Commit:** c0eabda
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Adds the task-by-task implementation plan that turns the design spec into build
instructions: a locked file structure, global constraints, and 12 sequenced
tasks with per-task interfaces, failing-test-first steps, and parallelization
markers.

## Changes
- New plan `docs/superpowers/plans/2026-08-08-agentcad-implementation.md`
  (208 lines); points implementers at superpowers subagent-driven / plan-
  execution sub-skills and declares the spec's contracts binding.
- **Global constraints:** Python 3.12 via uv, server bound to `127.0.0.1`, port
  persisted in `~/.agentcad/config.json`, vendored frontend (no CDN), id regex
  `[a-z][a-z0-9_]{0,39}`, mm/g/deg units, post-state on every mutation, the
  structured error shape, atomic writes, and a session-scoped kernel fixture to
  amortize the ~3 s warm import.
- **Locked file tree:** enumerates every module to create across `kernel/`,
  `core/`, `server/`, `agent/`, `frontend/`, `examples/`, `scripts/`, `tests/`,
  and `docs/`.
- **Tasks 1–12:** scaffold; kernel worker+mesh+client (defines the line-JSON
  protocol methods, `Metrics` keys, script-exec/param-clamp rules, ACM1 binary
  layout, and OCP tessellation approach); core model/materials/store;
  service+EventBus+ToolRegistry (17 tools); FastAPI server+WS+static (adds the
  `/api/tools` passthrough as a recorded spec delta); frontend; agent layer;
  three examples; CLI + macOS app; docs; adversarial review; final verification.
- Records an execution decision: Tasks 1–5 inline (interdependent spine),
  6/7/8/10 via parallel subagents on disjoint file ownership, 11–12 via workflow.

## Files
- `docs/superpowers/plans/2026-08-08-agentcad-implementation.md` — implementation plan

## Notes
Documentation only; no code. Deliberately specifies behavioral contracts and
ACM1/transform semantics in prose rather than literal code, on the stated
assumption that implementers are Fable-class agents with spec+plan+repo access.
