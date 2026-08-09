# 0007 — README

- **Commit:** 7551e0a
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Adds the top-level `README.md`: the project pitch, the agentic-first rationale, a
quickstart, agent-driver setup, the part-authoring primer, and the trust model —
the front door tying the docs set together.

## Changes
- New `README.md` (118 lines).
- **Why agentic-first:** frames the two core bets — the model is code (parts are
  parametric build123d scripts) and the kernel is the referee (every change
  validated by real B-rep geometry, failures returned as structured data) — and
  the one-registry/two-surface design (MCP server + built-in chat as peers of the
  browser UI).
- **Quickstart:** prerequisites (macOS, uv, Python 3.12), `make setup` / `make
  run` (server + UI at `http://127.0.0.1:8630`), the three bundled examples
  (rocketry/construction/prototyping), and the `test`/`app`/`serve` targets.
- **Drive it from Claude Code:** the `claude mcp add agentcad -- uv --directory
  … run agentcad mcp` registration and a natural-language usage example.
- **Built-in chat:** enabling the UI Agent panel via `ANTHROPIC_API_KEY`, with
  graceful degradation to the MCP path when absent.
- **Writing parts:** the minimal `PARAMS` + `build(p)` example and a pointer to
  the `part_template` tool.
- **Trust model** and a **project layout** map; links out to the architecture /
  agent-api / part-authoring / user-guide / roadmap docs.

## Files
- `README.md` — project overview, quickstart, agent setup, trust model

## Notes
Documentation only. References `docs/assets/workbench.png` (a screenshot
placeholder) and links `docs/user-guide.md`, `frontend/`, and the agent layer —
none of which exist in the tree at this commit; they are filled in by later
commits (0008 onward).
