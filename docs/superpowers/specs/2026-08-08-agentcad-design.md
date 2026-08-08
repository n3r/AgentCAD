# AgentCAD — Design Specification

**Date:** 2026-08-08
**Status:** Approved (autonomous session — approval gates resolved by the build agent under the user's `/goal` directive; every decision and its rationale is recorded here for review)

## 1. Context and goal

Build an **agentic-first CAD system** for complex parts and systems, aimed at rocket
science, construction, and prototyping. Deliverables: documentation and a working
application. Runs on macOS today; Windows and Linux must remain reachable later
(no platform-locked architecture).

Prior art in the sibling directory `~/dev/personal/cad` ("Vela CAD"): an Electron +
TypeScript app with a hand-built typed transaction protocol over `occt-wasm`. It
demonstrates that route's cost — months of protocol work yields three primitives and
whole-body booleans. This project deliberately takes a different architecture and
shares no code with Vela.

**Environment verified on this machine:** Apple Silicon (arm64), Python 3.12.4, uv
0.11.6, Node 24, 10 cores. Kernel spike passed: `build123d` (OpenCascade B-rep via
OCP wheels) builds a filleted, counterbored plate in 50 ms, tessellates in 80 ms,
exports valid STEP/STL. Warm import 3.2 s → the kernel must live in a long-lived
process, not be re-imported per operation.

## 2. What "agentic-first" means here (product thesis)

1. **The model is code.** Each part is a parametric Python script (build123d). Code
   is the representation agents read, write, diff, and review best — far better than
   opaque click-history feature trees. Humans review and steer; agents author.
2. **Agents are first-class clients.** The full tool surface is exposed twice from
   one registry: as an MCP server (so Claude Code / any MCP client can drive the
   system) and as a built-in chat agent in the UI (Anthropic API tool-use loop).
3. **The kernel is the referee.** Every operation is validated by real B-rep
   geometry: validity checks, volume/mass/bounding-box metrics, interference
   checks. Errors return as structured data so agents self-correct.
4. **Determinism.** Same script + same parameters → same geometry, same exports.
   No hidden state between rebuilds.

## 3. Approaches considered

**A. Python service + build123d (OCCT) kernel + web UI + MCP** — *chosen.*
Real B-rep modeling (sketches, lofts, sweeps, fillets, threads, booleans), STEP
export, immediately huge modeling vocabulary; Python scripts are the most
agent-native CAD representation; cross-platform by construction (pure Python +
browser UI). Cost: Python app distribution is heavier than a single binary;
executing generated code requires an explicit trust model (§10).

**B. Electron/TS + occt-wasm typed transactions** (the Vela route). Single
language and tight packaging, but every kernel feature must be hand-plumbed
through a protocol; capability-per-effort is an order of magnitude worse, and it
duplicates existing prior art. Rejected.

**C. Mesh CSG (manifold3d/trimesh).** Simple and fast, but no B-rep, no STEP, no
exact geometry — unusable for rocketry/construction engineering. Rejected.

## 4. System architecture

Product name: **AgentCAD**. Python package: `agentcad`. One repository.

```
┌─────────────┐   ┌──────────────┐   ┌─────────────────────┐
│  Claude Code │   │  Browser UI  │   │ Built-in chat agent │
│  (MCP stdio) │   │ (Three.js)   │   │ (Anthropic tool loop│
└──────┬───────┘   └──────┬───────┘   │  in the server)     │
       │ proxies HTTP     │ REST+WS   └──────────┬──────────┘
       ▼                  ▼                      │ in-process
┌──────────────────────────────────────────────────────────┐
│  FastAPI server (localhost only)                          │
│  ┌────────────────┐  ┌──────────────────────────────┐    │
│  │ ToolRegistry   │→ │ Service layer (projects,      │    │
│  │ (single source │  │ parts, assembly, exports)     │    │
│  │ of tool truth) │  └──────────────┬───────────────┘    │
│  └────────────────┘                 │ JSON-RPC/stdio      │
│                        ┌────────────▼───────────────┐     │
│                        │ Kernel worker (subprocess,  │     │
│                        │ warm build123d, restartable)│     │
│                        └────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
                         │ files
              ~/AgentCAD/projects/<name>/  (or --projects-dir)
```

### Components (one purpose each)

- **`agentcad/kernel/worker.py`** — subprocess entry point. Imports build123d once
  (warm), then serves JSON-RPC over stdin/stdout: `build_part`, `tessellate`,
  `export`, `interference`, `mass_properties`. Each script runs in a fresh module
  namespace. Never imported by the server process.
- **`agentcad/kernel/client.py`** — owns the worker subprocess: spawn, health,
  per-request timeout (default 60 s), kill-and-respawn on hang/crash. The only
  module that talks to the worker.
- **`agentcad/core/project.py`** — project store: manifest load/save, part CRUD,
  assembly instances, schema validation, atomic writes.
- **`agentcad/core/service.py`** — the application service: orchestrates store +
  kernel client, caches meshes by content hash, emits events. All clients (REST,
  MCP, chat agent) call this layer; none touch the kernel client directly.
- **`agentcad/core/tools.py`** — ToolRegistry: every agent-visible tool defined
  once (name, description, JSON Schema, handler → service). MCP and the chat
  agent both render from this registry, so the surfaces cannot drift.
- **`agentcad/server/app.py`** — FastAPI: REST routes (thin wrappers over the
  service), one WebSocket event channel, static frontend hosting, chat endpoint.
- **`agentcad/agent/mcp_server.py`** — MCP stdio server; proxies tool calls to the
  running HTTP server (base URL from `AGENTCAD_URL`); if unreachable, auto-starts
  `agentcad serve` in the background and waits for health.
- **`agentcad/agent/chat.py`** — server-side Anthropic tool-use loop (model
  default `claude-sonnet-5`, key from `ANTHROPIC_API_KEY`), streaming turns and
  tool events to the UI over the WebSocket. Absent key → UI panel shows setup
  instructions and the MCP alternative; everything else still works.
- **`agentcad/cli.py`** — `agentcad serve|open|mcp|export|new`.
- **`frontend/`** — static ES modules, no bundler: Three.js viewport (vendored),
  project tree, parameter inspector, CodeMirror 5 code editor (vendored), chat
  panel, error surfaces.
- **`examples/`** — three real projects: rocketry (regeneratively-thick nozzle or
  injector plate), construction (truss gusset node), prototyping (snap-fit
  electronics enclosure).

## 5. Data model and file formats

Project = directory under `~/AgentCAD/projects/<name>/` (server flag
`--projects-dir` overrides; examples ship in-repo and open by path).

```
project.json          # manifest, schema_version: 1
parts/<part_id>.py    # one parametric script per part
.cache/               # tessellation cache keyed by sha256(script+params)
exports/              # user-requested STEP/STL/3MF output
```

`project.json`:

```json
{
  "schema_version": 1,
  "name": "demo",
  "units": "mm",
  "parts": [
    {"id": "nozzle", "label": "Nozzle", "material": "al6061",
     "params": {"throat_d": 18.0}}          // overrides of script defaults
  ],
  "assembly": {
    "instances": [
      {"id": "nozzle_1", "part": "nozzle",
       "position": [0,0,0], "rotation_deg": [0,0,0], "color": "#8899aa"}
    ]
  }
}
```

Materials: built-in table in `agentcad/core/materials.py` (id, label, density
g/cm³): al6061, steel_a36, stainless_316, ti6al4v, inconel718, abs, pla, nylon_pa12,
concrete, douglas_fir. Part `material` must be a known id; density drives mass.

### Part script contract (the whole authoring API)

Scripts are plain build123d — no framework import, portable outside AgentCAD:

```python
from build123d import *

PARAMS = {
    "width":  {"default": 80.0, "min": 10.0, "max": 300.0, "unit": "mm",
               "description": "Plate width"},
    "hole_d": {"default": 6.0,  "min": 1.0,  "max": 50.0,  "unit": "mm",
               "description": "Center hole diameter"},
}

def build(p):
    with BuildPart() as part:
        Box(p.width, 60, 8)
        Hole(radius=p.hole_d / 2)
    return part.part
```

Rules enforced by the worker: `PARAMS` is a dict of numeric specs (`default`
required; `min`/`max`/`unit`/`description` optional); `build(p)` receives an
attribute namespace of resolved values (project overrides applied, clamped-with-
warning to min/max) and must return a build123d `Part`/`Solid`/`Compound`.
Violations produce structured errors (§8), never crashes.

## 6. API contract (REST, localhost only)

Base: `http://127.0.0.1:<port>/api`. Port from the local port registry at first
run, persisted to `~/.agentcad/config.json`. JSON in/out. Errors: HTTP 4xx/5xx
with `{"error": {"type", "message", "details"}}`.

| Method & path | Purpose |
|---|---|
| `GET /health` | `{status, version, kernel: "ready"\|"starting"}` |
| `GET /projects` | list projects (name, path, part count) |
| `POST /projects` | `{name}` → create |
| `POST /projects/open` | `{path}` → open by path (examples) |
| `GET /projects/{proj}` | manifest + per-part param specs & status |
| `GET /projects/{proj}/parts/{id}` | `{script, params_spec, params, metrics}` |
| `PUT /projects/{proj}/parts/{id}` | `{script?, label?, material?}` → validate + rebuild → metrics or structured error |
| `POST /projects/{proj}/parts` | `{id, label?, script?, material?}` (default script template) |
| `DELETE /projects/{proj}/parts/{id}` | remove part (fails if instantiated in assembly) |
| `PATCH /projects/{proj}/parts/{id}/params` | `{name: value, ...}` → rebuild → metrics |
| `GET /projects/{proj}/parts/{id}/mesh` | binary mesh (custom little-endian buffer: header + f32 positions + f32 normals + u32 indices + edge polylines) |
| `GET /projects/{proj}/parts/{id}/metrics` | volume mm³, mass g, area mm², bbox, center of mass, is_valid, face/edge counts |
| `POST /projects/{proj}/parts/{id}/export` | `{format: step\|stl\|3mf, tolerance?}` → file path under `exports/` |
| `GET /projects/{proj}/assembly` | instances + rolled-up mass/bbox |
| `PUT /projects/{proj}/assembly` | replace instance list (validated part refs) |
| `POST /projects/{proj}/assembly/interference` | pairwise intersection volumes > tolerance |
| `POST /projects/{proj}/export` | whole assembly as STEP (compound) or STL |
| `POST /chat` | user message → agent loop (streams over WS) |
| `GET /chat/history`, `DELETE /chat/history` | per-project chat transcript |
| `WS /ws` | events: `rebuild_started/finished/failed {project, part, metrics?, error?}`, `project_changed`, `chat_delta/tool_call/tool_result/done` |

Any client that edits state does it through these endpoints; the server is the
single writer of project files (external edits are picked up by mtime check on
read, not watched).

## 7. Agent tool surface (ToolRegistry → MCP + chat)

Tools mirror the service, phrased for agent ergonomics. Names and JSON Schemas
live in `core/tools.py`; MCP and chat render the same definitions.

`list_projects`, `create_project`, `open_project`, `get_project`,
`create_part`, `get_part` (script + spec + metrics), `update_part_script`
(returns rebuild result — metrics on success, structured error with traceback on
failure), `set_params`, `delete_part`, `get_mesh_summary` (counts+bbox, not the
buffer), `export_part`, `get_assembly`, `set_assembly`, `check_interference`,
`export_assembly`, `part_template` (returns the contract + a starter script +
build123d cheat-sheet so agents don't guess the API).

Design rule: every mutating tool returns the *post-state the agent needs next*
(metrics, validation, warnings) so loops converge in few turns.

## 8. Error handling

- **Script errors** (syntax, runtime, contract violations): worker returns
  `{type: "script_error", message, traceback, line}`; part keeps its last good
  mesh, UI marks it stale-with-error; agents receive the full traceback.
- **Kernel failures** (OCCT throw, e.g. impossible fillet): same shape,
  `type: "kernel_error"`, message includes the failing operation.
- **Timeout/hang**: worker killed and respawned; `type: "timeout"`; server stays
  up. Crash of worker mid-request → `type: "kernel_crash"`, auto-respawn.
- **Validation**: unknown material / bad param name / assembly ref to missing
  part → 422 with field-level details, nothing written.
- Writes are atomic (temp file + rename); a failed rebuild never corrupts the
  manifest or the previous cache entry.

## 9. Frontend design (v1 scope)

Dark, quiet, macOS-native feel (`-apple-system`, SF Mono for code). Layout:
left sidebar (projects → parts → assembly instances), center Three.js viewport
(orbit/pan/zoom, fit, ground grid, shaded+edges rendering from the mesh buffer,
per-instance transforms/colors, click-to-select syncs tree), right inspector
tabs — **Parameters** (sliders/number fields from spec, debounced live rebuild),
**Code** (CodeMirror 5 Python, Cmd+S saves → rebuild, error banner with
traceback), **Metrics** (volume/mass/bbox/COM/validity) — and a bottom-docked
**Agent** panel (chat with streamed tool-call chips; empty-state explains
API-key setup and the Claude Code MCP alternative). WebSocket keeps all panes
live; rebuild spinner per part. No offline-breaking CDN references — all vendor
libs committed under `frontend/vendor/`.

## 10. Security / trust model

Local, single-user engineering tool. Part scripts execute with user privileges
in the worker subprocess — same trust model as running the code the agent writes
in any coding session. Mitigations, not sandbox theater: server binds
`127.0.0.1` only; per-request kernel timeout; worker isolation keeps the app
alive; scripts live in the project where humans review them; API key only from
env, never persisted. Documented plainly in README. (True OS-level sandboxing:
roadmap.)

## 11. Testing strategy

pytest, run via `make test`:
- **Kernel worker**: contract enforcement, param clamping, error shapes, timeout
  kill/respawn, determinism (same input → same volume hash), export validity
  (STEP re-imports, STL parses).
- **Project store**: manifest round-trip, atomic writes, validation failures.
- **Service**: cache hits by content hash, event emission.
- **REST**: full endpoint pass with FastAPI TestClient over a temp project.
- **Tools/MCP**: registry schemas valid JSON Schema; each tool handler
  exercised; MCP server lists/calls tools over stdio.
- **Examples as integration tests**: each example project rebuilds valid with
  default and perturbed params; assembly interference check runs.
UI is verified by browser automation at the end (screenshot evidence), not unit
tests, in v1.

## 12. Non-goals (v1) → docs/roadmap.md

Constraint/mate solver; 2D drawings; STEP *import*; feature-tree GUI editing
(code is the model); multi-user/collab; Windows/Linux packaging (architecture
keeps them open: pure Python + browser UI + no mac-only deps outside the .app
wrapper script); OS-level script sandboxing; distributed/remote kernels.

## 13. Delivery checklist

Working `make setup && make run` on this machine; UI verified in a real browser;
MCP server usable from Claude Code; three example projects rebuild and export;
pytest green; documentation set (README, architecture, agent-api, part-authoring,
user-guide, roadmap); macOS `AgentCAD.app` wrapper generated by `make app`.

## Spec self-review (performed)

Placeholders: none. Consistency: tool surface (§7) is a strict mirror of service
capabilities used by REST (§6); mesh buffer endpoint is REST-only by design
(agents get `get_mesh_summary`). Scope: one implementation plan is feasible
because contracts (§5–§7) let core, frontend, agent layer, examples, docs
proceed in parallel after the core lands. Ambiguities resolved: parts are
identified by filesystem-safe ids (`[a-z0-9_]{1,40}`); units fixed to mm in v1;
param values are numbers only in v1 (booleans/enums: roadmap).
