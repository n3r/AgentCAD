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
│   (25 tools,       (cache, events,     ~/AgentCAD/projects  │
│    single source    orchestration)     or --projects-dir    │
│    of truth)             │                                  │
│                          │ line-delimited JSON-RPC (stdio)  │
│                          ▼                                  │
│            Kernel worker pool (N warm subprocesses)         │
│        (build123d/OCCT, restartable, affinity-routed)       │
└─────────────────────────────────────────────────────────────┘
```

Two tiers of process: the **server** (FastAPI + service + store) and one or
more **kernel workers** (`agentcad/kernel/worker.py`), each of which imports
build123d once (~3 s) and then executes part scripts in ~10–100 ms per
rebuild. The server never imports OCCT; if a script hangs or crashes a
kernel, that worker is killed and respawned (`agentcad/kernel/client.py`) and
the server keeps running.

The service talks to a **`KernelPool`** (`agentcad/kernel/pool.py`) rather
than a bare client: it fans requests across N warm workers so multi-part
rebuilds run concurrently (measured 2.4–3.6× on batches). The pool is a
drop-in for a single `KernelClient` — same `request(method, params,
timeout_s=, affinity=)` surface — so nothing upstream changed. Requests carry
an `affinity` (the part id) that hashes to a fixed worker, keeping a part on
its warm shape-LRU; unkeyed work round-robins. Workers spawn lazily, and
**pool size 1 is byte-for-byte identical to v1**. Size auto-picks
`max(1, min(3, cores//3))` — memory (~0.5 GB/worker), not cores, is the
constraint — and is overridable via `kernel_pool_size` in the config file or
`AGENTCAD_KERNEL_POOL_SIZE`.

## Components

| Module | Responsibility |
|---|---|
| `agentcad/kernel/worker.py` | Executes part scripts in a fresh namespace; enforces the PARAMS/build contract; builds, exports, interference checks. Stdout of user scripts is redirected to stderr so prints cannot corrupt the protocol. |
| `agentcad/kernel/mesh.py` | OCCT → ACM1 tessellation: per-face triangulation (hard edges preserved), per-vertex normals, tangential-deflection edge polylines. |
| `agentcad/kernel/acm.py` | ACM1 binary mesh codec (no OCP dependency; the frontend has a JS parser). |
| `agentcad/kernel/client.py` | Worker lifecycle: spawn, one-request-at-a-time, per-request timeout, kill-and-respawn, stderr tail capture for crash reports. |
| `agentcad/kernel/pool.py` | `KernelPool`: N `KernelClient`s behind the same `request()` surface; affinity routing + round-robin, lazy spawn. Size 1 ≡ single client. |
| `agentcad/kernel/handlers/` | Worker handler packs (reference, drawing, analysis, fem, connectors) merged into the worker at startup — see [extension points](#v2-extension-points). |
| `agentcad/kernel/refload.py` | Reference-CAD loader (STEP/BREP → solid, STL → mesh-only Face) with an LRU keyed by (realpath, mtime, size). Kernel-side only (imports OCP). |
| `agentcad/kernel/error_doctor.py` | Catalog of real OCCT/build123d failure signatures → plain-language diagnosis + fix; enriches every worker error's `details.hint`. |
| `agentcad/kernel/_mates_resolver.py` | Connector evaluation + mate-graph ordering (cycle rejection) + Joint-based resolution to concrete transforms. |
| `agentcad/toolkit/` | Part-authoring helpers importable from scripts: `safe_fillet`/`safe_shell`/`safe_bool`, the scipy sketch solver, `bd_warehouse` threads. |
| `agentcad/core/project.py` | Filesystem project store: `project.json` manifest (schema v2, reads v1), `parts/<id>.py` scripts, `imports/` references, atomic writes, validation. |
| `agentcad/core/service.py` | The application service all clients share: rebuild orchestration (script and reference parts), content-hash mesh/metrics cache, EventBus, assembly rollups, three v2 seams (material resolver, mate resolution, kernel pool). |
| `agentcad/core/materials.py` | Materials v2: frozen `Material` schema + 30-entry builtin library + `MaterialLibrary` (builtin < global file < project overrides). |
| `agentcad/core/mates.py` | Server-side seam that marshals instances to the worker's `resolve_mates` and writes concrete transforms back. |
| `agentcad/core/imports.py` | Reference-file ingest helpers: safe basename, 100 MB cap, supported-extension gate. |
| `agentcad/core/tools.py` | ToolRegistry — the 17 core tools defined once; discovers and loads `tools_*.py` packs. MCP and chat render from the merged registry. |
| `agentcad/core/tools_*.py` | v2 tool packs (import, materials, mates, drawing, analysis, sketch), each exporting `register(registry, service)`. |
| `agentcad/server/app.py` | Core REST routes (thin), `/api/tools` passthrough, WebSocket channel, static hosting; mounts `routes_*.py` packs under `/api`. |
| `agentcad/server/routes_*.py` | v2 route packs (import upload, materials, single-instance PATCH, drawing + SVG preview, analyze + fem, sketch solve). |
| `agentcad/agent/mcp_server.py` | MCP stdio server proxying `/api/tools`; auto-starts the HTTP server when unreachable. |
| `agentcad/agent/chat.py` | Server-side Anthropic tool-use loop streaming to the UI over the WebSocket. |
| `frontend/` | Static ES modules (no bundler): Three.js viewport, tree, parameter inspector, CodeMirror editor, chat panel. |

## v2 extension points

The v1 spine above is untouched. Every v2 capability lands as a **vertical
module** behind one of three pre-wired, auto-discovered extension points, so
features compose without editing the core files:

1. **Worker handler packs** — `agentcad/kernel/handlers/<feature>.py`. Each
   exports either a `HANDLERS: dict[str, callable]` or a
   `register(toolbox) -> dict`; `worker.py` discovers them with `pkgutil` at
   startup and merges them into its method table (a pack may not shadow a
   builtin). The **worker toolbox** (`WORKER_TOOLBOX`) hands packs the exact
   `build_shape` / `metrics` / `place` / `export_shape` / `tessellate`
   primitives the core uses, so reference imports, drawings, analysis, FEM,
   and connector inspection reuse one build path instead of re-deriving it.
2. **Tool packs** — `agentcad/core/tools_<feature>.py`, each exporting
   `register(registry, service)`; `build_registry` calls every pack after
   registering the 17 core tools. A pack may **decline** to register (the FEM
   pack registers `fem_static` only when its extra imports) so agents never
   see a tool that cannot run.
3. **Route packs** — `agentcad/server/routes_<feature>.py`, each exporting
   `build_router(service, registry) -> APIRouter`; `app.py` mounts them all
   under `/api`. Most just call the matching tool through the registry, so the
   REST and agent surfaces cannot drift.

`AgentCADService` exposes exactly **three seams** the packs fill in; each
defaults to v1 behavior, so the core runs unchanged if a pack is absent:

- **Material resolution** — `service.materials` starts as a builtin-only
  resolver; importing the materials pack swaps in the project-aware
  `MaterialLibrary`, so mass metrics honor user-defined alloys. Density feeds
  the rebuild cache key, so a material change re-keys the mesh cache.
- **Mate resolution** — `get_assembly` / `check_interference` /
  `export_assembly` route instances through `_resolved_instances`, which
  no-ops unless some instance carries a `mate`; then `core/mates.py` calls the
  worker's `resolve_mates` handler and substitutes concrete transforms. The
  rest of the service and the whole frontend keep seeing plain
  `position`/`rotation_deg`.
- **Kernel pool** — the single `KernelClient` is replaced by a `KernelPool`
  of the same shape (see [above](#process-model)).

A fourth, v3 seam handles **turn locking**: `ProjectStore.write_guard` is a
post-init hook invoked with the project name before every persistent mutation
(`save_manifest` — which all pack mutations funnel through — and
`write_script`). `AgentCADService` installs `TurnLock.check(proj,
current_client_id())` there, so per-project advisory locks are enforced at
one store choke point with zero per-pack edits. Client identity rides a
ContextVar (`agentcad/core/locks.py`): HTTP middleware stamps it from
`X-Agent-Id` (default `browser`), the chat engine stamps `chat` inside its
tool executor, and the MCP proxy sends `AGENTCAD_AGENT_ID`/`mcp`. Project
creation and derived-data writes (cache, exports, metrics sidecars) are
intentionally unguarded.

**LOD tiers.** A kernel build request may carry `lod_tolerances`
(`{suffix: tolerance}`) and `lod_min_triangles`; after writing the
full-resolution `<key>.acm` the worker re-tessellates and atomically writes
one `<key>.<suffix>.acm` sidecar per entry — but only when the full mesh's
triangle count (word 2 of the ACM1 header) exceeds the threshold, so small
parts never pay for the extra pass. The service requests a single `lod1`
tier at tolerance 0.8 above 150 000 triangles (`MESH_LOD_TOLERANCE` /
`LOD_TRIANGLE_THRESHOLD`) on every build; the result's `triangles` and
`lods` round-trip through the `<key>.metrics.json` sidecar. Tiers use the
same frozen ACM1 format and leave the full-resolution bytes pinned. The mesh
route serves a tier for `?lod=lod1` with `X-Mesh-Lod: lod1`, falling back to
the full buffer (`X-Mesh-Lod: full`) when none exists, which lets the
browser show a coarse preview instantly and swap in the full mesh in the
background. STL reference imports never get tiers — their triangulation is
their geometry.

Two cross-cutting pieces ride these seams. The **Error Doctor**
(`error_doctor.py`) is invoked from one hook in the worker's `_dispatch`: it
matches a failure's type + message + traceback against a catalog of real OCCT
signatures and attaches a plain-language `details.hint` — every kernel error,
core or pack, gets diagnosed. The **reference loader** (`refload.py`) is the
single place that turns an imported file into a shape, with the content-keyed
LRU and the STL-is-mesh-only rule (STL Faces are tessellated and measured but
blocked from booleans, which segfault OCCT) enforced once for every caller.

Two upstream build123d bugs are corrected in this layer: nested-`Compound`
`.volume` undercounts (worker metrics sum over `shape.solids()`), and the
manifest is **schema_version 2** (adds `kind`/`source` per part, an optional
per-instance `mate`, and a project `materials` section) while still reading v1
files.

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
code an agent writes in your working directory. On macOS the worker is
additionally confined by a seatbelt profile (`agentcad/kernel/sandbox.py`,
via `/usr/bin/sandbox-exec`): deny-by-default, global read, writes allowed
only inside the project roots (projects dir, registered examples,
`~/.agentcad`, the system temp dir), and no network. A script can still
compute anything and read world-readable files, but it cannot modify files
outside its projects and cannot reach the network from inside the worker.
`/api/health` reports the effective state (`"sandbox": "active" | "off" |
"unsupported"`); opt out with `AGENTCAD_NO_SANDBOX=1` or `{"sandbox": false}`
in `~/.agentcad/config.json` (env wins). Unchanged mitigations: the server
binds `127.0.0.1` only; kernel requests time out; the worker is isolated so
kernel crashes never take down the app; scripts live in the project directory
where humans review them; the Anthropic API key is read from the environment
and never persisted. Windows/Linux confinement is on the roadmap.
