# 0001 — AgentCAD design specification

- **Commit:** c0d6c61
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Adds the approved design spec for AgentCAD, an agentic-first parametric CAD
system for rocketry/construction/prototyping. Establishes the binding contracts
(architecture, data model, APIs, tool surface, error shapes) that every later
implementation commit builds against.

## Changes
- New spec `docs/superpowers/specs/2026-08-08-agentcad-design.md` (305 lines).
- **Product thesis:** the model is code (each part is a parametric build123d
  Python script), agents are first-class clients, the OCCT kernel referees every
  op, and rebuilds are deterministic.
- **Approaches considered:** chooses Python service + build123d (OCCT) kernel +
  browser UI + MCP over the Electron/occt-wasm route (Vela prior art) and mesh
  CSG; records the rationale and the verified kernel spike (build123d builds a
  filleted plate in 50 ms, 3.2 s warm import → long-lived worker).
- **Architecture (§4):** FastAPI server (localhost only) fronting a single
  `ToolRegistry` → service layer → a restartable kernel worker subprocess over
  JSON-RPC/stdio; names the per-module responsibilities (`kernel/worker.py`,
  `kernel/client.py`, `core/{project,service,tools}.py`, `server/app.py`,
  `agent/{mcp_server,chat}.py`, `cli.py`, `frontend/`).
- **Data model (§5):** project directory layout, `project.json` schema v1,
  the 10-material density table, and the part-script contract (`PARAMS` dict +
  `build(p)`).
- **Contracts:** REST route table (§6), the 17-tool agent surface (§7), the
  structured error taxonomy (`script_error`/`kernel_error`/`timeout`/…, §8),
  frontend scope (§9), trust model (§10), testing strategy (§11), v1 non-goals
  (§12), and a delivery checklist (§13).

## Files
- `docs/superpowers/specs/2026-08-08-agentcad-design.md` — full design spec

## Notes
Documentation only; no code. Marked "Approved" under an autonomous `/goal`
session with decisions recorded inline for review. The `/api/tools` generic
passthrough that later powers the MCP proxy is a deliberate delta introduced in
the implementation plan (0002), not in this spec.
