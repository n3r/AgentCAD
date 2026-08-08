# AgentCAD Architecture

AgentCAD is an agentic-first parametric CAD system: parts are plain
[build123d](https://build123d.readthedocs.io) Python scripts, the OpenCascade
(OCCT) kernel referees every change, and AI agents are first-class clients of
the same service humans use through the browser UI.

## Process model

```
┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐
│ Claude Code  │   │  Browser UI  │   │ Built-in chat agent  │
│ (MCP stdio)  │   │  (Three.js)  │   │ (Anthropic tool loop)│
└──────┬───────┘   └──────┬───────┘   └──────────┬───────────┘
       │ HTTP proxy       │ REST + WS            │ in-process
       ▼                  ▼                      ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI server — 127.0.0.1:<port>   (agentcad serve)        │
│                                                             │
│   ToolRegistry ──► AgentCADService ──► ProjectStore (files) │
│   (17 tools,       (cache, events,     ~/AgentCAD/projects  │
│    single source    orchestration)     or --projects-dir    │
│    of truth)             │                                  │
│                          │ line-delimited JSON-RPC (stdio)  │
│                          ▼                                  │
│               Kernel worker subprocess                      │
│               (warm build123d/OCCT, restartable)            │
└─────────────────────────────────────────────────────────────┘
```

Two processes: the **server** (FastAPI + service + store) and the **kernel
worker** (`agentcad/kernel/worker.py`), which imports build123d once (~3 s)
and then executes part scripts in ~10–100 ms per rebuild. The server never
imports OCCT; if a script hangs or crashes the kernel, the worker is killed
and respawned (`agentcad/kernel/client.py`) and the server keeps running.

## Components

| Module | Responsibility |
|---|---|
| `agentcad/kernel/worker.py` | Executes part scripts in a fresh namespace; enforces the PARAMS/build contract; builds, exports, interference checks. Stdout of user scripts is redirected to stderr so prints cannot corrupt the protocol. |
| `agentcad/kernel/mesh.py` | OCCT → ACM1 tessellation: per-face triangulation (hard edges preserved), per-vertex normals, tangential-deflection edge polylines. |
| `agentcad/kernel/acm.py` | ACM1 binary mesh codec (no OCP dependency; the frontend has a JS parser). |
| `agentcad/kernel/client.py` | Worker lifecycle: spawn, one-request-at-a-time, per-request timeout, kill-and-respawn, stderr tail capture for crash reports. |
| `agentcad/core/project.py` | Filesystem project store: `project.json` manifest, `parts/<id>.py` scripts, atomic writes, validation. |
| `agentcad/core/service.py` | The application service all clients share: rebuild orchestration, content-hash mesh/metrics cache, EventBus, assembly rollups. |
| `agentcad/core/tools.py` | ToolRegistry — every agent-visible tool (name, description, JSON Schema, handler) defined once; MCP and chat render from it. |
| `agentcad/server/app.py` | REST routes (thin), `/api/tools` generic passthrough, WebSocket event channel, static frontend hosting. |
| `agentcad/agent/mcp_server.py` | MCP stdio server proxying `/api/tools`; auto-starts the HTTP server when unreachable. |
| `agentcad/agent/chat.py` | Server-side Anthropic tool-use loop streaming to the UI over the WebSocket. |
| `frontend/` | Static ES modules (no bundler): Three.js viewport, tree, parameter inspector, CodeMirror editor, chat panel. |

## Anatomy of one rebuild

1. A client changes a parameter (`PATCH .../params`, `set_params` tool, or a
   chat-agent tool call) — all paths converge on
   `AgentCADService.set_params`.
2. The service persists the change to `project.json` (atomic write), computes
   the cache key `sha256(script, params, density, tolerance)`, and publishes
   `rebuild_started`.
3. Cache hit → cached metrics return immediately. Miss → the kernel worker
   gets a `build` request: it executes the script contract, resolves and
   clamps parameters, builds the B-rep, writes the ACM1 mesh atomically to
   `.cache/<key>.acm`, and returns metrics (volume, mass, area, bbox, center
   of mass, validity, face/edge/solid counts).
4. Success → `rebuild_finished` (with metrics) goes out on the WebSocket; the
   UI refreshes the mesh and metrics. Failure → the structured error
   (`script_error` / `kernel_error` / `contract_error` / `timeout`, with
   traceback and failing line) is persisted as the part's status, published
   as `rebuild_failed`, and returned to the caller — the previous good mesh
   is kept on screen, and agents receive the traceback to self-correct.

Determinism: the same script + parameters always produce the same geometry
and byte-identical meshes (verified by test); no state leaks between rebuilds
beyond an explicit shape LRU keyed by content hash.

## The ACM1 mesh format

Little-endian binary: magic `ACM1`; u32 counts (vertices, triangles, edge
points, edge polylines); f32 positions; f32 unit normals; u32 triangle
indices; u32 polyline lengths; f32 edge points. Faces are tessellated
independently so hard edges stay crisp without normal splitting; edge
polylines give the CAD-style outline rendering.

## Transform semantics

Assembly instances carry `position` (mm) and `rotation_deg` (intrinsic XYZ
Euler, degrees). The kernel applies `build123d.Location(position, rotation)`;
the frontend applies `new THREE.Euler(rx, ry, rz, 'XYZ')` — equivalence is
covered by a kernel test that round-trips a 90° rotation through an STL
export.

## Trust model

A local, single-user engineering tool. Part scripts execute with user
privileges inside the worker subprocess — the same trust model as running any
code an agent writes in your working directory. Mitigations: the server binds
`127.0.0.1` only; kernel requests time out; the worker is isolated so kernel
crashes never take down the app; scripts live in the project directory where
humans review them; the Anthropic API key is read from the environment and
never persisted. OS-level sandboxing is on the roadmap.
