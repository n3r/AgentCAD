# 0008 — Frontend UI, agent layer (MCP + chat), and three example projects

- **Commit:** 25b15ab
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Adds the three remaining product layers on top of the existing kernel/service/HTTP
core: a static browser UI, an agent-facing surface (MCP stdio proxy + a built-in
Anthropic chat agent), and three worked example projects. Built by parallel
subagents against the spec/plan contracts; full suite reports 70 passing.

## Changes
- **Agent chat** (`agentcad/agent/chat.py`): `ChatEngine` runs an Anthropic Messages
  API tool-use loop. Tools are rendered from the shared `ToolRegistry` (same source
  as MCP, so the two surfaces can't drift); each turn runs as an asyncio background
  task and streams `chat_delta` / `chat_tool_call` / `chat_tool_result` / `chat_done`
  events on the `EventBus`. Per-project in-memory history, `DEFAULT_MODEL =
  claude-sonnet-5`, `MAX_TOOL_CALLS_PER_TURN = 30`, and a `ChatUnavailable`
  (ValidationError→422) when no `ANTHROPIC_API_KEY` is set. Includes the CAD system
  prompt describing the part-script contract.
- **MCP server** (`agentcad/agent/mcp_server.py`): stdio MCP server that proxies the
  HTTP API — `list_tools` mirrors `GET /api/tools` 1:1, `call_tool` becomes
  `POST /api/tools/{name}`. Transport/HTTP failures come back as tool results
  (`transport_error`), not MCP protocol errors. Auto-starts `agentcad serve
  --no-open` if no server answers `/api/health`, polling up to 30s; honors
  `AGENTCAD_URL`.
- **Frontend** (`frontend/`): offline, same-origin ES-module UI — `main.js` (boot,
  WebSocket stream, mesh routing), `viewport.js` (Three.js, ACM1 binary mesh parser,
  Z-up, orbit/fit/pick), `inspector.js` (Parameters/Code/Metrics tabs + rebuild
  error banner), `editor.js` (CodeMirror 5 Python), `tree.js` (parts + assembly
  instances), `chat.js` (agent dock), `api.js` (structured `ApiError`), `state.js`.
- **Vendoring**: `scripts/vendor_frontend.sh` npm-installs three@latest and
  codemirror@5 into a throwaway dir and copies the exact files into `frontend/vendor/`
  (recorded in `VERSIONS.txt`: three 0.185.1, codemirror 5.65.21); no CDN references.
- **Examples**: `rocketry` (nozzle/injector_plate/flange thrust chamber),
  `construction` (gusset_plate/base_plate/angle_bracket truss node),
  `prototyping` (enclosure_base/enclosure_lid snap-fit) — each a `project.json`
  (schema_version 1) plus parametric build123d part scripts and a README.

## Files
- `agentcad/agent/chat.py` — new `ChatEngine` tool-use loop + event streaming
- `agentcad/agent/mcp_server.py` — new MCP stdio proxy with server auto-start
- `frontend/index.html`, `frontend/css/app.css`, `frontend/js/*.js` — UI
- `frontend/vendor/*`, `frontend/vendor/VERSIONS.txt` — vendored three/codemirror
- `scripts/vendor_frontend.sh` — reproducible vendoring
- `examples/{rocketry,construction,prototyping}/` — three example projects
- `tests/test_chat.py`, `tests/test_mcp.py` — coverage for the agent layer

## Notes
Chat history lives only for the server's lifetime and is per-project. The chat
backend degrades gracefully when no API key is present — the UI surfaces the MCP
setup snippet so agents can drive AgentCAD from Claude Code instead.
