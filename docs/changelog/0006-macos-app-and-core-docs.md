# 0006 — macOS app wrapper and core documentation set

- **Commit:** 50c7441
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Adds the macOS `.app` bundle build script and the core prose documentation set
(architecture, agent API reference, part authoring, roadmap), written against the
code as built.

## Changes
- **macOS app** (`scripts/make_app.sh`): builds `dist/AgentCAD.app` — writes
  `Contents/Info.plist` (bundle id `dev.agentcad.app`, version 0.1.0, min system
  13.0) and a `Contents/MacOS/AgentCAD` launcher that `cd`s to the repo and
  `exec`s `uv run agentcad open`, logging to `~/Library/Logs/AgentCAD.log`; then
  ad-hoc `codesign`s the bundle. Requires `uv` on PATH; this is the `make app`
  target.
- **architecture.md**: process model (two processes — FastAPI server + kernel
  worker), per-module responsibility table, the anatomy of one rebuild (cache key
  → kernel build → `rebuild_finished`/`failed` events), the ACM1 format, the
  intrinsic-XYZ Euler transform equivalence between kernel `Location` and
  `THREE.Euler(...,'XYZ')`, and the trust model.
- **agent-api.md**: documents the dual agent surface (MCP stdio proxy with the
  `claude mcp add` snippet, plus the built-in chat), the error/post-state/units
  conventions, a table of all 17 tools with arguments and returns, and a worked
  create→error→read→fix→export loop.
- **part-authoring.md**: the `PARAMS`/`build(p)` contract, param-robustness
  guidance (parts must stay manifold at every param extreme), and an OCCT
  failure-mode table (fillet radius, empty selectors, coincident booleans, Hole
  with no material, shell/offset).
- **roadmap.md**: v0.1 non-goals ordered by value — Windows/Linux packaging,
  single-binary distribution, OS-level sandboxing, STEP import, non-numeric
  params, mates/constraints, 2D drawings, richer agent feedback tools, undo/redo.

## Files
- `scripts/make_app.sh` — builds and ad-hoc-signs `dist/AgentCAD.app`
- `docs/architecture.md` — processes, components, rebuild data flow, ACM1, trust model
- `docs/agent-api.md` — the 17-tool surface, MCP/chat setup, worked loop
- `docs/part-authoring.md` — script contract, robustness, OCCT failure modes
- `docs/roadmap.md` — post-v0.1 non-goals

## Notes
The docs describe modules that do not yet exist in the tree at this commit — the
agent layer (`agent/mcp_server.py`, `agent/chat.py`) and `frontend/` land in
0008 — so the MCP/chat and viewport sections document intended, not-yet-committed
behavior. `docs/user-guide.md` is referenced by the doc set but is not added
here.
