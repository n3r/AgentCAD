# 0005 — FastAPI server, CLI, and examples integration test harness

- **Commit:** 3ba55c3
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Exposes the core service over HTTP and the command line. Adds the FastAPI app
(REST + WebSocket + generic tool passthrough + static hosting), a full-featured
CLI (`serve`/`open`/`mcp`/`new`/`export`), and integration tests over the server
and any bundled example projects.

## Changes
- **Server** (`agentcad/server/app.py`): `create_app(service, registry,
  chat_engine=None)` wiring all `/api` routes as thin wrappers over the service —
  project CRUD + open-by-path, part CRUD, `PATCH …/params`, `GET …/metrics`,
  `GET …/mesh` (streams ACM1 with `Cache-Control: no-store` + `X-Mesh-Key`
  header), export, and assembly get/set/interference/export.
- **Error mapping:** exception handlers map `NotFoundError`→404,
  `ValidationError`→422, `ConflictError`→409 (default 400) and `KernelError`→502,
  all in the `{"error": {type, message, details}}` shape.
- **Generic tool passthrough:** `GET /api/tools` lists the registry and
  `POST /api/tools/{name}` calls a tool by name — the endpoints the MCP server
  proxies (the spec delta recorded in the plan).
- **WebSocket** `/ws`: subscribes to the `EventBus` and forwards events as JSON,
  emitting a `ping` every 20 s via `run_in_executor` on the queue; unsubscribes on
  disconnect. Chat routes (`/api/chat`, history get/delete) register only when a
  chat engine is provided; `/api/health` reports `kernel` readiness and
  `chat_available`.
- **Static hosting:** serves `frontend/index.html` at `/` and mounts
  `/js`, `/css`, `/vendor` when those directories exist.
- **CLI** (`agentcad/cli.py`): rewritten from the stub into an argparse app.
  `serve`/`open` build the service (starting the kernel, registering
  `examples/*` at startup), build the registry, optionally create the chat
  engine, and run uvicorn on `127.0.0.1:<port>` (port from `--port` or config
  8630), opening the browser for `open`. `mcp` runs the MCP stdio server; `new`
  creates a project; `export <project|path> <part> --format --output` exports and
  optionally copies the file out.
- **Tests:** `tests/test_server.py` (TestClient: health, project/part flow,
  broken-script `ok:false` 200, mesh magic/headers, export, assembly +
  interference, 404/422/409 mapping, `/api/tools` lists 17, generic tool calls,
  WebSocket `rebuild_finished`); `tests/test_examples.py` (parametrized over
  `examples/*` on disk: all parts valid at defaults and at every param min/max,
  every param has min/max/unit/description, ≥2 assembly instances, clean
  interference, STEP export).

## Files
- `agentcad/server/app.py` — FastAPI app: REST, WS, tool passthrough, static, error handlers
- `agentcad/cli.py` — real CLI (serve/open/mcp/new/export), example auto-registration
- `tests/test_server.py` — API/WebSocket coverage via TestClient
- `tests/test_examples.py` — integration harness over bundled example projects

## Notes
The CLI imports the agent layer lazily — `.agent.mcp_server.run_mcp_server` in
`cmd_mcp` and `.agent.chat.ChatEngine` in `_make_chat_engine` (missing import
→ no chat engine). Those modules, the `frontend/` assets, and the example
projects do not exist yet at this commit; they land in 0008, so
`test_examples.py` skips (no examples on disk) and the served `/`,`/js`,`/css`,
`/vendor` mounts stay inactive until then. The mesh route reads
`service._status[...]["cache_key"]` directly to set `X-Mesh-Key`.
