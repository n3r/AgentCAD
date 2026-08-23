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
│   (109 tools,      (cache, events,     ~/AgentCAD/projects  │
│    single source    orchestration)     or --projects-dir    │
│    of truth)             │                                  │
│                          │ line-delimited JSON-RPC (stdio)  │
│                          ▼                                  │
│            Kernel worker pool (N warm subprocesses)         │
│        (build123d/OCCT, restartable, affinity-routed)       │
└─────────────────────────────────────────────────────────────┘
```

The registry registers **109 tools** (112 with the optional `[fem]` extra
installed — `fem_static`, `fem_modal`, `fem_thermal` register only when their
deps are importable).

Two tiers of process: the **server** (FastAPI + service + store) and one or
more **kernel workers** (`agentcad/kernel/worker.py`), each of which imports
build123d once (~3 s) and then executes part scripts in ~10–100 ms per
rebuild. The server never imports OCCT; if a script hangs or crashes a
kernel, that worker is killed and respawned (`agentcad/kernel/client.py`) and
the server keeps running.

**Hosted mode (PRD-005a)** is the same two tiers with one thing added in front
and nothing rearranged behind:

```
   internet ──► [ reverse proxy / TLS ] ──► ┌──────────────────────────────┐
                                            │ FastAPI — 0.0.0.0:8630       │
   anonymous ─────────────────────────────► │  security.guard (default     │
     nine public entries only               │  deny; 9 public entries)     │
                                            │        │ principal           │
   member (session cookie) ────────────────►│        ▼                     │
   agent   (Bearer acad_…) ────────────────►│  set_client_id("user:nikita/ │
                                            │   browser:7f3a1b2c")         │
                                            │  ToolRegistry ─► Service ─►  │
                                            │  ProjectStore  /data/projects│
                                            └──────────────┬───────────────┘
                                        AuthStore /data/state/auth/*.json
                                        (4 atomic JSON docs, flock-guarded)
```

`AGENTCAD_MODE=hosted` is explicit and never inferred; with it unset the
middleware runs the pre-005a local path unchanged (`create_app(security=None)`
is the same code path, not a disabled feature). The resolved principal is set
through the existing `locks.set_client_id`, so turn locks, per-part claims,
presence, comments, notifications and history attribution became
principal-aware with **zero** changes to PRD-008 code. Identity state lives
beside the config file (`core/appmode.state_dir()`), never inside a project
and never under `--projects-dir`. See `docs/deployment.md`.

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
| `agentcad/kernel/handlers/` | Worker handler packs (reference, drawing, analysis, fem, connectors, diff, specs, sketchplane, bench, interop, interop_import) merged into the worker at startup — see [extension points](#v2-extension-points). |
| `agentcad/kernel/refload.py` | Reference-CAD loader (STEP/BREP → solid, STL → mesh-only Face) with an LRU keyed by (realpath, mtime, size). Kernel-side only (imports OCP). |
| `agentcad/kernel/error_doctor.py` | Catalog of real OCCT/build123d failure signatures → plain-language diagnosis + fix; enriches every worker error's `details.hint`. |
| `agentcad/kernel/_mates_resolver.py` | Connector evaluation + mate-graph ordering (cycle rejection) + Joint-based resolution to concrete transforms. |
| `agentcad/toolkit/` | Part-authoring helpers importable from scripts: `safe_fillet`/`safe_shell`/`safe_bool`, the scipy sketch solver, `bd_warehouse` threads, and `specs` — the ten pure-data `check_*` constructors a script's `SPECS` list is built from (zero kernel imports, so a `check_fem_static` declares without the `[fem]` extra). |
| `agentcad/toolkit/sketch.py` | The 2D constraint solver, and — with `specs.py` — one of exactly **two OCP-free toolkit modules**: it is imported by `core/tools_sketch.py` and therefore runs in the *server* process (`facemod`, `fillet`, `shell`, `surfacing`, `sheetmetal`, `threads` all import build123d and run kernel-side). Every constraint compiles to a typed `Residual` carrying its spec index, its parameter slots, its value function and its **analytic derivative**, which buys the Jacobian in one pass instead of `n_par + 1` (measured 104× at 50 entities), a rank analysis that can name the constraint a user wrote, and a `PointRef` indirection under which arcs, ellipses, splines and slots reuse the existing constraint vocabulary. Diagnostics are a pure-numpy post-pass (SVD for rank/DOF/free entities, declaration-order greedy selection for the dependent set). |
| `agentcad/core/sketch_emit.py` | The **single** sketch emitter, for the GUI and for agents alike: solved sketch → idiomatic `BuildLine`/`BuildSketch` source at 9 decimals with shared junction literals, behind a 1e-8 mm closure gate that refuses `make_face()` on a wire that would not close. Also owns the FR10 round-trip block (`persist_spec`, `block_hash`, `parse_blocks`, `next_name`). Emitting build123d *source* is not importing build123d: OCP-free, asserted in a fresh interpreter. |
| `agentcad/core/project.py` | Filesystem project store: `project.json` manifest (schema v2, reads v1), `parts/<id>.py` scripts, `imports/` references, atomic writes, validation. |
| `agentcad/core/service.py` | The application service all clients share: rebuild orchestration (script and reference parts), content-hash mesh/metrics cache, EventBus, assembly rollups, three v2 seams (material resolver, mate resolution, kernel pool). |
| `agentcad/core/materials.py` | Materials v2: frozen `Material` schema + 30-entry builtin library + `MaterialLibrary` (builtin < global file < project overrides). |
| `agentcad/core/mates.py` | Server-side seam that marshals instances to the worker's `resolve_mates` and writes concrete transforms back. |
| `agentcad/core/imports.py` | Reference-file ingest helpers: safe basename, 100 MB cap, supported-extension gate. |
| `agentcad/core/history.py` | The per-project git engine: snapshot-on-mutation, log/restore, ref primitives (`resolve_ref`, `branches`, `tags`), and the `UndoCursor`. Works in the project directory *or* a linked worktree; every git call is hermetic (`GIT_CONFIG_NOSYSTEM`, scratch `HOME`, 10 s timeout) and never raises into a save. |
| `agentcad/core/branches.py` | `BranchManager`: branch/tag operations and the per-client branch resolver installed into `ProjectStore.branch_resolver`. Owns `.history/trees/<branch>/` worktrees and the `.history/agentcad/` sidecars. |
| `agentcad/core/manifest_merge.py` | Pure, I/O-free three-way merge of `project.json` at CAD key granularity: `merge_manifests(base, ours, theirs) -> (merged, conflicts)` and `apply_choices`. No git, no kernel, no service imports. |
| `agentcad/core/merge.py` | `MergeOrchestrator`: `git merge-tree` for scripts, the manifest driver for the manifest, a staged detached worktree, the kernel validation pass, and the two-parent `commit-tree` + compare-and-swap `update-ref` that lands it. |
| `agentcad/core/proposals.py` | `ProposalStore` (JSON documents + an append-only `audit.jsonl` in the `.history/agentcad/` sidecar, atomic writes, id allocation) and `ProposalManager`: the state machine, attribution, the approvals policy, the gate list, and the gated merge that delegates to `MergeOrchestrator` unchanged. No kernel, no packet work. |
| `agentcad/core/packet.py` | `PacketBuilder`: the review packet. Four pure delta functions (changed parts, PARAMS, assembly, metrics) plus generation over the two branch worktrees — git diffs, per-part metrics through the ordinary service path, frame-matched renders, and `geom_diff` kernel calls — persisted with both branch heads. Talks to the kernel only through `service.kernel.request`. |
| `agentcad/core/specs.py` | `SpecRunner`: design-spec orchestration — the `ast`-only `declares_specs` presence scan, the three evaluation tiers, the `.cache/*.specs.json` result sidecars, the report (flat check records, per-part blocks, per-requirement grouping), the `specs.py` reader/writer, and `evaluate_specs`/`gate_provider` for the proposal gate. Talks to the kernel only through `service.kernel.request`; imports no OCP. |
| `agentcad/core/checks.py` | Geometry CI: the `schema: 1` report (rows, stages, verdict), its markdown rendering, a hand-rolled `validate_report`, and `CheckRunner` — the *sequencer* that drives four existing surfaces (`_ensure_built`, `_resolved_instances` + `check_interference`, `SpecRunner.run`, the drawing tools), materializes a `--ref` into a throwaway worktree behind a second ephemeral service, verifies determinism, and posts a verdict to a proposal (`CheckStore` + the `checks` gate provider). Composes; never measures. Imports no OCP. |
| `agentcad/core/comments.py` | `CommentStore` (thread documents + an append-only `audit.jsonl` per thread and one `notifications.jsonl` per project, atomic writes, a persisted `next_id` high-water mark, a rebuildable `index.json`) and `CommentManager`: the thread lifecycle, anchor validation, attachment rules, mentions, and the `comment_changed`/`notification` events. Lives in the `.history/agentcad/comments/` sidecar — canonical, branch-free, outside model state. No kernel, no git. |
| `agentcad/core/anchors.py` | Read-time anchor **resolution**: the four states (`ok`/`moved`/`orphaned`/`unverified`) built in one place by `make_resolution`, `face_table`/`signature_table` (per-face area, area-weighted centroid and normal, `bbox_uvw`, `area_frac` — pure NumPy over the `.acm` mesh plus the `<key>.faces.u32` sidecar, no kernel call and no rebuild), the face matcher, the two-tier `script_range` remap (exact snippet search, then a `difflib` line map over the blob at the anchor's stored head), and the `proposal_hunk` header re-match. Imports no OCP. |
| `agentcad/core/presence.py` | `PresenceRegistry`: an in-memory, TTL'd (45 s) roster keyed `(lock_key, client_id)`, fed by an HTTP heartbeat and expired lazily on read — never persisted, no background thread. Also `install_claim_guard`/`ensure_*`, the lazy wrapper that teaches `ProjectStore.write_guard` about per-part claims without changing its signature. |
| `agentcad/core/locks.py` | `TurnLock` (project-wide, explicit) and `ClaimRegistry` (per-part, implicit, human-vs-human, 90 s), plus the client-identity and `write_scope` contextvars that carry "who" and "which part" to the write guard. |
| `agentcad/core/tools.py` | ToolRegistry — the 17 core tools defined once; discovers and loads `tools_*.py` packs. MCP and chat render from the merged registry. |
| `agentcad/core/tools_*.py` | Feature tool packs (import, materials, mates, drawing, analysis, sketch, locks, history, versioning, proposals, specs, run_checks, comments, packages, configs, ui, xchange), each exporting `register(registry, service)`. `tools_specs` additionally *wraps* `service._rebuild` and `service.get_part` (the `install_write_guard` precedent) and appends the `specs` gate provider; `tools_run_checks` installs `service.checks` and appends the `checks` gate — and is named for **load order**, since packs load alphabetically and `tools_proposals` resets `gate_providers`. |
| `agentcad/core/tools_ui.py` | The `ui_open` tool pack (PRD-026): loads at `ui`, registers unconditionally (no gate provider). Validates `view`/`args`, rate-limits to 10 opens/10 s per process, and publishes `{"type": "ui_open", "view", "args", "by": "agent"}` on `service.bus` — `delivered_to` is `EventBus.subscriber_count()`, read *before* the publish. |
| `agentcad/core/navigation.py` | Navigation at scale (PRD-027): the folder/tag grammar (`normalize_folder`, `normalize_tags`, `folder_matches` — a case-insensitive **whole-segment** prefix, so `a/b` is under `a` and not under `a/bc`), `BulkExecutor` (the six ops over a multi-selection as **one** manifest write, one `project_changed` and one undo entry), and `dashboard(service)` (every project as one card's worth of facts, from manifests and in-memory `_status` alone). No kernel call anywhere in it. |
| `agentcad/core/search.py` | The part search: the `field:value` query grammar (`parse`, and the single `GRAMMAR` constant quoted into the tool description, every refusal and `docs/agent-api.md`), a pure matcher/ranker (`matches`, `rank`, `snippet`) the frontend ports byte-for-byte, and `Engine` — an on-demand scan with a stat-validated memo (manifest rows keyed on `(mtime_ns, size)`, script text per path), **no** inverted index and no bus plumbing. `state`/`kind` are deliberately outside the memo: a build moves `_status` without touching a manifest byte. |
| `agentcad/core/thumbnails.py` | Content-addressed 192² iso previews rendered from meshes that already exist: `part_key`/`part_thumb`, `assembly_key`/`assembly_thumb` (a composite over the placed instances), `has_thumb`, and `ThumbnailWarmer` (a daemon thread on `bus.subscribe()` reacting to `rebuild_finished.cache_key`, queue 256 coalesced by `(proj, key)`, `drain()` as an exact barrier). **Never builds** — and never calls the rebound `service._resolved_instances`, which would issue kernel requests for every polar-pattern and sub-assembly member. |
| `agentcad/core/tools_navigation.py` | The navigation tool pack (PRD-027): `set_part_meta`, `search_parts`, `bulk_part_op`. Loads at `nav` — after `tools_materials`, before `tools_proposals`, which resets `gate_providers` — and registers **no** gate provider. It *constructs* `service.search` and `service.thumbnails` (reusing one already bound to this service) and starts neither. |
| `agentcad/server/routes_navigation.py` | `GET /api/projects/{proj}/search` (a passthrough to the `search_parts` tool, so the filter box and an agent get one answer to one question) and `GET /api/dashboard`. Member-only by default-deny. |
| `agentcad/server/routes_thumbnails.py` | `GET /api/projects/{proj}/parts/{part_id}/thumb.png` and `GET /api/projects/{proj}/thumb.png` — the codebase's first non-`no-store` binary responses, safe because they are addressed by content hash (below). This pack is also the **only** place the thumbnail warmer's thread is started: route packs are mounted by `create_app` alone, so the MCP server, the CLI, `agentcad check`, the package gate and the bench never spawn it. |
| `agentcad/server/app.py` | Core REST routes (thin), `/api/tools` passthrough, WebSocket channel, static hosting; mounts `routes_*.py` packs under `/api`. |
| `agentcad/server/routes_*.py` | Route packs (import upload + structured-tree preview, materials, single-instance PATCH, drawing + SVG preview, analyze + fem, sketch solve + sketch blocks, history, branches/versions/merge, proposals, specs, checks, comments, presence, packages, configs, ui + the content-addressed mesh route). |
| `agentcad/server/routes_ui.py` | `POST /api/ui/events` (PRD-026): the browser's fire-and-forget UX telemetry (`dialog_opened`/`dialog_submitted`/`palette_executed`), allow-listed and re-published on `service.bus` with `by: "browser"` and the `X-Agent-Id` client id. Member-only by default (not in `PUBLIC_PATHS`). |
| `agentcad/bench/` | AgentCAD-Bench (PRD-024): the task-bundle loader, the six-subscore kernel scorer over a muzzled copy, the budgeted runner, the report/baseline gate, the leaderboard and the authoring helper. CLI-only — no tool, route or event. OCP-free, asserted by a test. |
| `agentcad/agent/mcp_server.py` | MCP stdio server proxying `/api/tools`; auto-starts the HTTP server when unreachable. |
| `agentcad/agent/chat.py` | Server-side Anthropic tool-use loop streaming to the UI over the WebSocket. |
| `frontend/` | Static ES modules (no bundler): Three.js viewport, tree, parameter inspector, CodeMirror editor, chat panel. PRD-027 adds `query_model.js` (the byte-equivalent port of `core/search.py`'s grammar and matcher, driven through the same `tests/fixtures/search_queries.json` parity fixture as the Python half), `virtual_model.js` (the pure scroll window — import it namespaced, `import * as virtual`, because a named `window` import shadows the browser global), the `tree_model.js` folder/filter/selection models, and the DOM halves `tree.js` (rewritten on those models), `bulk.js` and `dashboard.js`. |
| `frontend/js/shell/` | The workbench shell (PRD-026): `actions.js` (the one action registry menus/palette/shortcuts read), `dialogs.js`/`dialogs_model.js` (the modal/non-modal primitive, the overlay stack, the dialog-view registry `ui_open` resolves through), `palette.js`/`palette_model.js` (⌘K: fuzzy ranking, JSON-Schema→form fields, result routing), `menu.js`/`menu_model.js` (the File/Edit/View/Model/Help bar), `layout.js`/`layout_model.js` (resizable/collapsible sidebar/inspector/chat-dock panels, per-workspace `localStorage`), `shortcuts.js`/`shortcuts_model.js` (chord registration, conflict detection, the "?" cheat-sheet), `toast.js`, `events.js` (`POST /api/ui/events`). Every DOM module is paired with a DOM-free `*_model.js` importable in node — the `tree_model.js`/`tree.js` split, tested from `tests/test_frontend_shell.py`. |

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
   REST and agent surfaces cannot drift. **The export routes are the known
   exception** (PRD-017): `POST .../parts/{id}/export` and `POST
   .../export` call `service.export_part`/`export_assembly` directly and
   forward only `format`/`tolerance`/`config` from the body — `pmi`,
   `metadata` and `structured` are tool-surface-only (agent/MCP `callTool`,
   which the frontend's Export▾ menu itself falls back to for exactly this
   reason — see `docs/agent-api.md`'s `export_part`/`export_assembly` rows),
   the same kind of gap `generate_drawing`'s `tabulate` argument documents
   for itself.

**Growing an *existing* verb from a pack** (the interop pack, PRD-017, is the
worked example). `ToolRegistry.register` raises on a duplicate name and has no
overwrite seam, so a pack cannot re-register `export_part` with a wider schema
— the house idiom is two halves that always move together:

- **wrap the service method** (`service.export_part` / `export_assembly` are
  captured and replaced behind a `_WRAPPED` sentinel, so a second
  `build_registry` is idempotent), and
- **mutate the already-registered `Tool` in place** — its `input_schema`
  (`format` enum, `pmi?`, `metadata?`, `structured?`), its description, **and
  its `handler`**. Rebinding the handler is not optional: the registered
  lambda took the old argument list, and a schema advertising `pmi` over a
  handler that cannot take the keyword is a `TypeError` at call time. A test
  asserts the mutation is visible through `build_registry` *and*
  `GET /api/tools`.

The pack is called **`tools_xchange.py` for its load order**: packs load
alphabetically, and `tools_structure` (PRD-013) does not delegate to
`export_assembly` — it *replaces* it. A pack sorting before it would wrap a
method that is then thrown away, silently taking assembly expansion or interop
with it; `xchange` sorts after `structure`, `undo` and `versioning`, so what it
captures is the **final** method, expansion included. (`tools_interop.py` would
have sorted before — `i` < `s`.) The same file is where a format becomes
*conditionally* available: `usd` joins the enums, the descriptions and the
routing only when `usd_available()`, all from one live function, so an agent is
never shown a format that cannot run (the FEM gating rule).

The kernel side is two packs, `handlers/interop.py` (exports: `export_step_pmi`
/ `read_step_pmi`, `export_3mf_rich`, `export_step_structured`) and
`handlers/interop_import.py` (`inspect_cad_tree`, `import_structured`) — split
so the two halves could be built in parallel, and because only the kernel
process may import OCP. glTF/GLB and USD are written **server-side** from the
cached ACM1 meshes (`core/gltf.py`, `core/usd_export.py` — both OCP-free,
asserted in a fresh interpreter), so they cost no kernel round trip at all.

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

## Branches, versions and merges

A fifth seam, and the same shape as the fourth: `ProjectStore.branch_resolver`
is a post-init hook `(proj, canonical_path) -> working_tree_path`. Every read
and write of *authored* state already funnels through `ProjectStore._resolve`,
so installing one function makes the manifest, scripts, exports, imports, the
snapshot hook, the turn lock and the undo cursor branch-aware with no service
or pack edits. `agentcad/core/tools_versioning.py` installs it (plus
`service.branches` and `service.merges`) and **declines to register anything
when `git` is absent**, so the product degrades to linear history rather than
offering tools that cannot run.

**Layout.** The default branch keeps the project directory as its working
tree, so an unbranched project is byte-identical on disk to a pre-branching
one and the migration is a no-op. Everything else lives inside the existing
git dir:

```
<project>/                     # the DEFAULT branch's working tree
├── project.json, parts/, imports/
├── .cache/                    # canonical + content-addressed: SHARED by all branches
└── .history/                  # the git repository (already excluded from itself)
    ├── trees/<branch>/        # one linked worktree per non-default branch
    │                          #  ('/' → '-'; not .history/worktrees/, which is git's own)
    └── agentcad/              # sidecars, inside GIT_DIR so never committed
        ├── config.json        # the discovered default branch
        ├── checkouts.json     # client → branch, branch → tree dirname
        ├── tags.json          # version referrers (PRD-015 forward-compat)
        ├── merge.json         # the staged merge, if any
        └── merge-<id>/        # its detached staging worktree
```

**Resolution order** in `BranchManager.resolve_path`: an explicit
`pinned_tree_var` (used only by the merge validation pass) → the calling
client's checked-out branch (`locks.current_client_id()`, the same ContextVar
turn-locking stamps) → the canonical directory. It never raises: an unreadable
sidecar or a missing worktree degrades to the project directory, which is
always a valid project. `cache_dir` stays canonical, so a mesh built on one
branch is a cache hit on every other (byte-determinism across branches);
`lock_key` returns the project name on the default branch and the resolved
tree path elsewhere, which is what makes per-branch turn locks and undo stacks
fall out for free while unbranched behavior stays bit-identical.

**A merge, end to end** (`MergeOrchestrator.merge`):

1. Resolve both branches, snapshot and require both trees clean, take the
   *target's* turn lock, and compute the merge base. Base == source ⇒ no-op;
   base == target ⇒ fast-forward (ref + tree move, no validation pass).
2. `git merge-tree --write-tree -z <target> <source>` produces the merged tree
   oid and per-path stages `{1: base, 2: ours, 3: theirs}`. `ours` is always
   the **target**.
3. `project.json` is re-merged by `manifest_merge` **regardless** of what git
   thought — a line-wise merge of a manifest is either garbage or a clean
   result nobody authored. Non-text content (`imports/*.stl|step`) is read and
   staged as raw **bytes**: a binary conflict reports sizes and digests, takes
   a side verbatim, and never becomes conflict-marked text. Its key space (the
   full table is in the module docstring):

   | Key | Granularity |
   |---|---|
   | `parts.<id>.params.<name>` · `.solid_materials.<key>` | per key |
   | `parts.<id>.configs.<name>` | per name; then field-wise |
   | `parts.<id>.configs.<name>.params.<param>` | per parameter |
   | `parts.<id>.active_config` · `assembly.instances.<id>.config` | whole value (a selection) |
   | `parts.<id>.pmi` · `materials.<id>` · `packages[_lock].<name>` | atomic per entry |
4. The result is staged in a detached worktree under `.history/agentcad/`.
   Nothing outside that directory is written while conflicts are outstanding,
   and no ref moves, so a conflicted merge is never partially applied.
   Conflicts are **returned** as a `{"error": {"type": "merge_conflict", …}}`
   payload (the registry derives error types from exception class names, so
   raising would rename it), and `resolve_merge` re-runs the same pipeline
   with accumulated choices.
5. The validation pass runs inside `branches.pinned(proj, staged_dir)` and
   calls the *ordinary* service methods (`_ensure_built`, `_resolved_instances`,
   `check_interference`), so the kernel pool, the mesh cache and the mates
   resolver are reused verbatim and already-built parts are cache hits. It
   reports built parts, build failures, referential integrity (dangling
   instances/mates a clean key-wise merge can still produce, plus
   `manifest_merge.package_problems`' requirement/lock hybrid and
   `config_problems`' instance bound to a configuration the merge removed) and
   interference diffed against the pre-merge target — only **new** pairs block.
   A part whose `active_config` the merge removed resolves as base, so it is a
   `warnings` string and does not block.
6. Landing is `commit-tree` with both parents plus a compare-and-swap
   `update-ref`: a commit that arrived on the target while the merge was
   staged fails the swap and surfaces as a `conflict_error` instead of
   silently clobbering it. Then the target's working tree is reset to the
   merge commit, the undo cursor records it, and `project_changed` +
   `merge_completed` go out on the bus.

**New events:** `branch_changed {project, client, branch}` (a switch is
per-client, so the UI resets its context only for its own client id) and
`merge_completed {project, source, target, commit, validation}`.

**Requirements.** Branches and tags work on any git; merging needs **git
2.38+** for `merge-tree --write-tree` and says so by name in a
`validation_error` on older versions.

## Change proposals (CAD pull requests)

A proposal is a durable, attributed object over a branch pair, with an
auto-generated **review packet** and a merge that only happens through a gate.
It adds no seam of its own to `ProjectStore` — it is a tool pack
(`tools_proposals.py`) plus a route pack (`routes_proposals.py`) over PRD-001's
ref layer, and it installs `service.proposals`, `service.packets` and
`service.gate_providers` (the list PRD-003's `specs` gate already appends to
and PRD-004's `checks` will, so neither has to touch `proposals.py`). Like the branch tools, the
whole pack **declines to register when `git` is absent**.

**State lives in the sidecar, not in the project.** FR3 requires that
`project_restore` never rewind workflow state, so proposals are written inside
`GIT_DIR`, where no working tree and no snapshot can see them:

```
<project>/.history/agentcad/proposals/
├── index.json                 # a rebuildable cache of the per-directory truth
├── policy.json                # approvals_required, self_approve
└── <id>/
    ├── proposal.json          # the object (atomic write)
    ├── audit.jsonl            # append-only: {seq, ts, actor, actor_kind, action, details}
    ├── packet.json            # the generated evidence, pinned to both heads
    ├── renders/<part>.<side>.<view>.png
    └── diff/<generation>/<part>.<added|removed>.acm
```

Diff meshes are namespaced by the **build** that wrote them (`packet.generation`,
which the asset URLs carry): a packet is published together with its assets, so
a build the merge overtook is discarded *with its directory* and a frozen
packet's URLs keep naming the geometry it was persisted with. A build that
persists collects the older generations under its slot.

`audit.jsonl` is the one file that is **appended**, never atomically replaced:
FR14 makes it append-only, and a read-modify-replace cycle would both break
that and risk truncating the log on a crash.

**A packet, end to end** (`PacketBuilder.build`):

1. Resolve `source`/`target` through `branches.tree_of`, checkpoint each tree
   and **refuse a dirty one** (`conflict_error`), then pin both head SHAs — a
   packet whose pinned heads do not describe the measured bytes is a lie.
2. Changed parts = the union of part ids whose `parts/<id>.py` bytes differ
   (`git diff --name-only`) and whose manifest entry differs, classified
   `added`/`removed`/`modified` with `changed_by ∈ script | params | manifest`.
3. Script diffs come from one `git diff --unified=3 <target> <source> --
   parts/`, split per path, with hunk anchors PRD-008 will hang threads off.
   PARAMS and assembly deltas come from the two manifests (read with merge's
   *strict* loader) and from `get_assembly` at **resolved** transforms.
4. Every measurement runs through the **ordinary service methods** under
   `branches.pinned(proj, tree)` — the same mechanism the merge validation pass
   uses — so the canonical, content-addressed `.cache/` makes unchanged parts
   free on both sides. Packet cost scales with the change, not the project.
5. Geometry: a part whose cache key matches on both sides short-circuits to
   `unchanged` with **no kernel call at all**; otherwise one `geom_diff`
   request per part returns `added_mm3`/`removed_mm3` and writes the two ACM1
   diff solids for the viewport overlay.
6. Renders: both sides at 640×480 through `render_acm(..., frame=…)` with
   **one** frame — the union of both world bboxes, inflated 2 % — so the pair
   is literally superimposable. They are written as PNG assets and published as
   URLs, because MCP and chat lift only one `png_base64` per result.
7. Every per-part stage is individually wrapped: a failure lands in
   `warnings`/`errors` and the packet still returns `ok: true`. `ok: false`
   means no packet could be produced at all.

**The `geom_diff` handler** is a sibling handler pack
(`agentcad/kernel/handlers/diff.py`), not a new `kind` on `analysis.py`:
`analyze` takes one script, a diff takes two shapes and writes two meshes.
It builds both sides through the worker toolbox, computes `new - old` (added)
and `old - new` (removed) with the **`-` operator** — correct on multi-solid
Compound operands, unlike the `&` interference uses — measures them with the
toolbox's `shape_volume` (the solids sum; `Compound.volume` undercounts a
nested result), and tessellates each to ACM. A mesh-only reference part is `skipped: "mesh"` rather than
booleaned — the `check_interference` rule — and a boolean failure degrades to
`available: false` with the metrics still present.

**The merge is PRD-001's, unchanged.** `ProposalManager.merge` evaluates gates
first (`state`, `approvals`, `validation`, the provider-supplied `specs` gate
below, and `checks` once PRD-004 supplies it) and refuses a red one with a `conflict_error` naming it; then it
calls `MergeOrchestrator.merge` and forwards its payloads verbatim, including
`merge_conflict`. `allow_invalid` is passed straight through to the kernel
validation gate and never touches the approvals policy.

**New event:** `proposal_changed {project, id, state, reason}` for every
state or packet transition.

## Design specs (executable intent)

Design intent lives in the tree as code: a `SPECS` list in `parts/<id>.py`
(part scope) and in a root `specs.py` (project scope), built from
`agentcad/toolkit/specs.py`'s pure-data constructors. **There is no storage
layer** — specs are tracked files, so PRD-001 versions, branches, diffs,
merges, restores and undoes them for free. Declaration is data; measurement is
the kernel's.

**Three components, one per process boundary.**

- `agentcad/toolkit/specs.py` — the ten constructors. Stdlib only, no kernel
  import at all, validating **eagerly** so a bad argument raises while the
  script executes and arrives as an ordinary `script_error` with
  `details.line`, exactly like a malformed `PARAMS`.
- `agentcad/kernel/handlers/specs.py` — the worker pack: `spec_declare` (exec
  the module, read `SPECS`, **never build**), `spec_eval` (build through the
  shape LRU, then evaluate the shape tier against the built part and its
  metrics, predicates included) and `clearance` — the one genuinely new
  geometry op, `BRepExtrema_DistShapeShape` over two world-placed items,
  measured through the conservative `analysis(p)` envelope so a reported gap
  is never larger than the real one.
- `agentcad/core/specs.py` — `SpecRunner`, the orchestration, `service.specs`.

**Three tiers, so cost lands where it belongs.** Tier 1 (shape: `valid`,
`mass`, `volume`, `bbox`, `wall`, `that`) runs on **every rebuild**, as one
`spec_eval` with `affinity=part_id` onto the worker that just built the part.
Tier 2 (assembly: `interference_free`, `clearance`, `stackup`) and tier 3
(`fem_static`, 600 s budget) are **deferred** — reported at rebuild time as
`skip` with `reason: "deferred"`, evaluated by `run_specs` and by the gate.
A failing check never fails a rebuild: geometry lands, `ok` stays `true`.

**Zero cost when nothing is declared.** `declares_specs(script)` is an
`ast.parse` presence scan for a module-level `SPECS` binding, memoized by
`sha256(script)` and **never executed** — a spec-less part reaches no kernel
call at all, and its rebuild payload carries `specs: null` ("none declared",
which is not "not evaluated").

**Caching rides the existing content hash.** Tier-1 results are stored in
`.cache/<cache_key>.specs.json` beside the existing `.metrics.json` (atomic
write, versioned, a corrupt or stale sidecar is discarded and recomputed,
never raised); the assembly tier gets `.cache/<project_key>.projspecs.json`
keyed on the `specs.py` text plus every instance's id, part cache key and
resolved transform. Because `SPECS` lives *in the script text*, the existing
cache key already covers it — editing a spec invalidates the sidecar for free,
at the cost of one kernel rebuild of geometry that did not change (deliberate:
a second content signature that split geometry identity from spec identity
could serve a stale mesh). Only `pass`/`fail` rows are cached — a `skip` can
be machine-specific and an `error` is usually transient.

**The proposal gate is one appended provider.** `tools_specs.install_specs_gate`
appends a closure named `specs` to PRD-002's `service.gate_providers`; it
evaluates the proposal's **source branch** under `branches.pinned(...)`,
re-reads the head afterwards (a head that moved is `pending`, never a verdict
wearing a commit it did not measure), and is bounded by `GATE_BUDGET_S = 30`.
It is **fail-closed**: failed, errored *or unevaluated* is red, and
`allow_invalid` does not waive it. Reverting the enforcement is deleting that
one call — `proposals.py`, `merge.py` and `packet.py` are untouched by the
feature.

**Trust.** Both a part script's `SPECS` and a project's `specs.py` — including
`check_that` predicates — execute **inside the confined kernel worker**, never
in the server process, under exactly the same sandbox as any part script (see
[Trust model](#trust-model)). A predicate's callable never crosses the JSON-RPC
boundary: it is reported to the service as `"predicate": true`.

## Geometry CI

`agentcad/core/checks.py` certifies a **whole project** in one bounded run:
every part rebuilds, the assembly re-resolves and is interference-checked, the
declared specs are evaluated and every drawing regenerates — headless, with no
server process. Full reference: [geometry-ci.md](geometry-ci.md).

**It is a sequencer, not a measurement.** `CheckRunner` drives four surfaces
that already exist and are reviewed, and shapes one report out of what they
return:

| Stage | Surface |
|---|---|
| `build` | `service._ensure_built` per manifest part, plus `service._ensure_config_built` per declared configuration (subject `part@config`) |
| `assembly` | `service._resolved_instances`, then `service.check_interference` (the **service** method — the tool's schema has no `timeout_s`) |
| `specs` | `service.specs.run` (PRD-003, all three tiers), embedded whole |
| `drawings` | the registered `generate_drawing` (SVG) and `flat_pattern` tools |

Row statuses, summary counts and stage statuses are literally PRD-003's —
`summarize`, `report_status`, `group_requirements` and `assign_ids` are
**imported** from `core/specs.py`, not restated — so one product has one status
vocabulary. A failing row's `error` is the driving surface's payload verbatim,
which is what makes a red check a task an agent can act on. Rows are called
`items`: `checks` already means the gate name, a spec report's rows and the
proposals UI tab.

**Four surfaces, one runner.** `tools_run_checks.register` constructs
`service.checks = CheckRunner(service, registry)` **once**, so the CLI
(`cmd_check`), the `run_checks` tool, `POST /api/projects/{p}/checks` and the
GitHub Action all share one runner, one last-report cache and one publisher —
and therefore produce identical reports. The pack is named for load order:
packs load alphabetically and `tools_proposals` (`p`) assigns
`gate_providers = []` unconditionally, so at `r` this pack's `checks` gate
survives while a `tools_checks.py` (`c`) would have been silently discarded —
and, for the same reason, `service.specs` (`s`) and `service.branches` (`v`) do
not exist yet at registration and are read lazily inside the runner's methods.

**Checking a ref never mutates the project** (the containment rule). The
resolved *commit* is materialized into a throwaway detached `git worktree` and
measured through a **second, ephemeral `AgentCADService`** rooted in the work
dir:

```
  your project                    throwaway <work-dir>/agentcad-check-<pid>-<rand>/
  ┌──────────────────────────┐   worktree add   ┌──────────────────────────────┐
  │ parts/ project.json      │  --detach <sha>  │ <project>/ at <sha>          │
  │ .cache/  exports/        │ ───────────────► │ a cold .cache/               │
  │ .history/ (git repo)     │                  │                              │
  └──────────────────────────┘                  │ ephemeral AgentCADService    │
        byte-untouched                          │   bus.on_publish   = None    │
                                                │   branch_resolver  = None    │
                                                │   write_guard      = None    │
                                                │   the SAME kernel object     │
                                                └──────────────────────────────┘
                                       removed in a `finally`, then `worktree prune`
```

All three muzzles are load-bearing. A live event bus would publish
`project_changed`, and `service._snapshot_on_event` would commit a history
snapshot **into the linked worktree** — the user's real repository — from a
command whose contract is "never mutates". A live `branch_resolver` would route
every read and write through a `.history/agentcad/` sidecar that does not exist
there, and create one. A live `write_guard` (the versioning pack installs one)
would call `branches.ensure_checkout` and materialize a branch tree in that
same repository. The kernel object is *shared*: a second pool would cost
another ~3 s per worker and ~0.5 GB. The price of the containment is stated
rather than hidden — a ref check runs on a **cold cache**.

The work dir itself is never written to directly: a run materializes into a
unique subdirectory it creates (`agentcad-check-<pid>-<rand>/`) and deletes
only that, and a `--work-dir` that is, contains or sits inside the project (or
the projects root) is refused. The throwaway tree is named after the project,
so without that rule `--work-dir .` from the projects root resolves onto the
live one.

**Determinism is verified, not assumed.** `--verify-determinism` adds a derived
`determinism` stage that builds every part a second time against a copy of the
measured tree with no `.cache`, and compares the cache key, the `.acm` mesh
bytes, the metrics and the SVG drawing for exact equality. DXF is excluded by
name (ezdxf stamps `$TDCREATE` and fresh GUIDs), as one `skip` row with a hint
— the report's only `strict_exempt` row, because a skip nothing can fix is not
a `--strict` candidate.

**The verdict lands where decisions are made.** `--proposal <id>` writes the
report to `.history/agentcad/proposals/<id>/checks.json` beside PRD-002's
packet, appends one `audit.jsonl` line and publishes `proposal_changed
{reason: "checks"}`; a gate provider named `checks` reads it back and replaces
PRD-002's placeholder of the same name. Like the `specs` gate it never answers
`pending` — `ProposalManager.merge` blocks `fail` and nothing else — so a
report certifying a head the branch has moved past is a `fail` saying *re-run*.
`proposals.py`, `packet.py`, `merge.py` and `specs.py` are untouched by the
feature: the whole enforcement surface is one new file beside the packet and
one `append` to `service.gate_providers`.

**Trust.** A check executes the project's part scripts — inside the same
confined kernel worker as every other build (see [Trust model](#trust-model)).
Since PRD-006 that worker confines itself on Linux too (Landlock + seccomp: no
network, writes only in the checkout's project roots and a private temp dir),
but the runner's kernel decides whether it can: an image without Landlock in
its `lsm=` list confines nothing and says so. So the GitHub Action still
documents `pull_request` (never `pull_request_target`) and no secrets — the
confinement is a second line, not the reason the workflow is safe.

## AgentCAD-Bench

`agentcad check` certifies a *project*; **`agentcad bench` scores an *agent***
(PRD-024, [`docs/bench.md`](bench.md)). One OCP-free package, `agentcad/bench/`
(`tasks.py` the bundle loader, `scoring.py` the six subscores, `runner.py` the
budgeted `ChatEngine` client, `report.py` the aggregation and the baseline gate,
`publish.py` the leaderboard, `cli.py` the five subcommands, `author.py` the
task-authoring helper), plus one kernel handler pack —
`agentcad/kernel/handlers/bench.py`, exposing a single `iou` method that is
**never registered as a model-facing tool**, because a bench-only tool would
contaminate the measurement it exists to take. Tasks are data under
`benchmarks/tasks/<category>/<id>/`, resolved through `resource_root()` like
`examples/` and `catalog/`.

The scorer is `checks._ephemeral_service`'s **muzzled copy**, reused rather than
re-derived: a submission is copied into a throwaway cell, the task's rubric is
appended to the copy's part scripts (re-binding `SPECS`, discarding whatever the
candidate declared), and a service with `bus.on_publish`, `branch_resolver` and
`write_guard` all `None` measures it — so scoring writes no history snapshot, no
branch sidecar and nothing at all into the submission. `cli._build_service`
gained exactly one keyword-only parameter for it (`examples=False`): a task
derived from a bundled example must not be solvable by opening that example,
and an env var would be process-global under xdist. No route pack, no tool
pack, no new event type and no manifest key.

## Review threads, anchors and presence

Feedback that points at something. A **thread** is a root comment plus replies
with state `open`/`resolved`, anchored to a part, a face, a param, a script
line range, an assembly instance or a proposal diff hunk.

**Storage — deliberately not model state.**

```
<project>/.history/agentcad/comments/
  next_id · index.json · notifications.jsonl
  <id>/thread.json · <id>/audit.jsonl
```

Inside GIT_DIR, so it rides no branch, appears in no `git status`, is never
merged, and is structurally beyond `project_restore`'s reach — the same
sidecar pattern as `proposals/` (PRD-002), resolved through
`store.canonical_path_of` so a client working in a branch worktree writes to
the one canonical list. `thread.json`, `index.json` and `next_id` are atomic
writes; `audit.jsonl` and `notifications.jsonl` are appends and are never
rewritten (unread = mention seqs minus every seq a later `read` line names).

**Anchor resolution is a read-time computation, never stored.** The anchor is
immutable evidence stamped by the server at creation; what it *means now* is
recomputed on every list. It issues no kernel call, forces no rebuild,
regenerates no review packet and writes to no thread, anchor, manifest or
proposal — the one thing it does write is a `<key>.facesig.json` memo of the
face table beside the mesh in the project's `.cache`, derived data keyed by the
same content hash and versioned (`{"v": 2, "faces": [...]}`, changelog `0125`)
so a memo written by an older build is recomputed from the mesh rather than
read short of a feature:

```
list_comments
  └─ for each anchor
       part/param/instance → manifest lookup                → ok | orphaned
       face                → service._cache_key_for(part)    (NO build)
                             .cache/<key>.acm + <key>.faces.u32
                             → face_table (area, centroid, normal,
                                           bbox_uvw, area_frac)
                             → same mesh key?     → ok
                             → matcher (normal dot, bbox_uvw distance,
                                        area-share gate, ambiguity margin)
                                                  → moved | orphaned
                             → never built        → unverified/part_not_built
       script_range        → exact snippet at the stored range      → ok
                             exact snippet elsewhere, corroborated
                             by the stored context (a LONE copy the
                             context contradicts is not a match)    → moved
                             difflib map over the blob at anchor.head
                                                  → moved | orphaned
                             no git / head gone   → unverified
       proposal_hunk       → persisted packet.json, header identity → ok |
                             moved | orphaned | unverified(packet_frozen)
```

Four states, three of which are not "fine": `ok`, `moved`, `orphaned`,
`unverified` (*we did not look*). The contract is **orphan rather than guess** —
an ambiguous match is an orphan, a lone candidate must clear an absolute area
bar of its own (`LONE_AREA_REL`, the code review's finding) **and still touch
the same number of faces** (`0125` — the area bar alone did not close the class
it was introduced for), a lone *snippet* must be contradicted by neither side
of its stored context (changelog `0124`, tightened in `0125` — the same
"nothing left to compare against" mistake, in the script matcher), and the
tolerances were set by measurement
(`docs/changelog/0113-prd008-anchor-resolution.md`, re-measured in `0123` and
`0125`). Two classes, measured separately because the first says nothing about
the second: across a **parameter change**, 53.9% resolved and 2 mis-pins in
2 693 known-truth faces; across a **deleted feature**, 98.8% correctly orphaned
and 4 mis-pins in 327 destroyed faces (27 before the adjacency gate). A strong
bias, not a guarantee: a cut-away face can still re-pin. Resolution issues
**zero kernel calls**: it reads the manifest, meshes a build already wrote, and
at most one git blob per anchor.

Absence is classified **only after** deciding whether we are looking at the
anchor's own branch: a parameter, a line range or a face missing from a part
that exists on both branches is `unverified`/`other_branch`, not an orphan,
because "it was removed" would be a claim about a branch the thread was never
about (design Decision 7, widened in changelog `0124`).

**Presence** is an in-memory registry keyed `(lock_key, client_id)` with a 45 s
TTL, fed by a 15 s HTTP heartbeat (`POST /api/projects/{p}/presence`) rather
than by the WebSocket: `/ws` lives in `app.py`, carries no client identity, and
its Host guard is HTTP middleware. The heartbeat *response* carries the whole
roster, so a client that misses every `presence_changed` converges within one
beat; an over-rate heartbeat is HTTP 200 with `throttled: true`, never an
error. Everything about it is bounded, because the identity is a header anyone
can rotate: `MAX_ID_CHARS` (refused, never truncated — a truncation would merge
two identities into one key; the check lives in `locks.check_client_id` and is
called by the claim registry too, because a part write reaches it without
passing a presence route), `MAX_CLIENTS` (a **process-wide** ceiling, and a
full roster refuses a *new* row rather than evicting an incumbent) and
`MAX_BUCKETS` on the rate limiter, which bounds memory first: a rotating
identity is granted its first beat exactly like a real newcomer, so what it is
held to is one grant per bucket per refill window (512 per 5 s) rather than the
1/s a single client gets. Eviction may drop only a bucket that has refilled to
its full burst — dropping a spent one would hand its owner a new one, which is
a payout to the flood it is supposed to bound.

**Two coordination mechanisms, one precedence rule**, evaluated on every
persistent write at `ProjectStore.write_guard`:

| # | Condition | Outcome |
|---|---|---|
| 1 | Another client holds the project **turn** | today's `ConflictError`, unchanged (PRD-001 path, message and details) |
| 2 | The caller holds the turn | proceed — **never** claim-checked (FR12) |
| 3 | The part is **claimed** by another client and both are `human` | `ConflictError` with `{claim, overridable: true}` — the browser arms a single-use 30 s override and retries |
| 4 | otherwise | proceed, and refresh the caller's claim on that part |

Claims are per-part, implicit (taken by *editing*, never by viewing), 90 s, and
**human-vs-human only** — an agent blocked by a human's open editor would 409
on the first write of the human→agent review loop. The part dimension reaches
the guard through the `locks.write_scope` contextvar, so `write_guard`'s
signature is unchanged and only `write_script`/`update_part_entry` are covered;
whole-manifest writes are turn-locked only.

**Events** all ride the existing bus: `comment_changed`, `notification`,
`presence_changed`, `claim_changed`. None of them is `project_changed` — a
comment is not a model change, so it triggers no snapshot and no rebuild.

## Packages, indexes and the publish gate

`agentcad/core/packages/` is an eleven-module subpackage that touches no
geometry — every module is asserted OCP-free in a fresh interpreter — plus one
worker handler pack:

| module | contents |
|---|---|
| `content.py` | the content id (a canonical tree digest), the inventory, the ceilings, path containment |
| `format.py` | `package.json` / `index.json` / configuration schemas, the hand-rolled version grammar |
| `cache.py` | `~/.agentcad/packages/<name>/<version>/`, sibling receipts, atomic install, verification |
| `lockfile.py` | the two additive manifest maps, and the rule that neither holds a machine fact |
| `indexes.py` | the client protocol, `LocalIndex`, `GitIndex`, and the publisher |
| `_git.py` | a second, small git runner — *not* `history._run` |
| `search.py` | structured filters, the deterministic ranking, the semantic seam |
| `provenance.py` | header emit/parse and status-on-read |
| `gate.py` | the nine-stage pipeline over an ephemeral service |
| `from_step.py` | the vendor-STEP scaffold (FR13) |
| `manager.py` | `PackageManager` — the façade at `service.packages` |

Reached through the ordinary extension points: the tool pack
`core/tools_packages.py` (seven tools, and **no gate provider** — `pac` sorts
before `tools_proposals`' unconditional `gate_providers = []`), the route pack
`server/routes_packages.py`, `frontend/js/library.js`, the CLI's `package` and
`publish` subcommands, and the worker handler `kernel/handlers/reffaces.py`
(the faces of an imported solid, which `face_info` cannot read because it
takes a script).

```
~/.agentcad/packages/<name>/<version>/     the content-verified cache
~/.agentcad/packages/<name>/.receipts/     machine facts, never committed
~/.agentcad/indexes/<name>/                a git index's checkout, at a pinned ref
catalog/                                   the bundled `agentcad-core` index
<project>/project.json  packages / packages_lock   what you asked for / what you got
```

**The gate is orchestration, not measurement.** It materialises the package
into a scratch project inside a throwaway cell, drives a *second, ephemeral*
`AgentCADService` over the **same warm kernel** with `bus.on_publish`,
`store.branch_resolver` and `store.write_guard` all nulled, and sequences
`inspect` → `set_params` → `_rebuild` (optionally fanned out across the pool)
→ `SpecRunner.run` → `connectors` + `resolve_mates` → `render_view`, shaping
PRD-004's report. Unlike PRD-004's runner this one really writes, so nulling
the write guard is load-bearing rather than prophylactic. The report is a
PRD-004 document plus `package`, `note` and the verdict.

**`use_part` copies in.** A materialised package part is an *ordinary part* at
every seam — rebuild, cache key, git history, proposals packet, `checks --ref`,
anchors, comments — which reference resolution would have touched all of. The
price is that consumers do not get fixes automatically; `list_packages`
reports `latest` and `stale`.

Full reference: [`docs/packages.md`](packages.md).

## Configurations

A **configuration** is a named parameter set on a part, living in the manifest
at `parts.<id>.configs.<name>` in the schema the package format froze
(`{params, label?, description?}`, validated by
`packages.format.validate_configuration` — one object, one validator, no
second copy of the rules). `parts.<id>.active_config` names the one the
working state is showing; `assembly.instances.<id>.config` binds an instance
to one. All three are written **only when set**, so a project without a family
serializes byte-identically to a pre-configuration one and needs no schema
bump or migration.

**The kernel never sees a configuration.** Resolution is two pure members on
`PartRecord` — `config_params(name)` (a copy of one configuration's map) and
`effective_params` (`{**config_params(active), **params}`) — and every
geometry consumer reads `effective_params` where it used to read `params`.
Resolution happens on the way *into* a request, never inside
`ProjectStore.get_part`: a store that resolved would let the next `set_params`
bake the active configuration into the overrides. "PARAMS defaults <" needs no
code at all, because `worker._resolve_params` already fills every unset name —
which is exactly what keeps every pre-existing cache key byte-identical.

**Nothing new entered the cache key.** `_cache_key_for` hashes
`record.effective_params`, so configuration-awareness is a property of the
record rather than a new field in the hashed payload: two configurations with
the same map share one entry, a declared family nobody activated changes no
key, and every cache entry written before the feature still hits.

**One build path, two entry points.** `service._rebuild(proj, part_id)` keeps
its signature byte-for-byte — three tool packs monkey-patch it and
`get_part` with two-positional wrappers, so it cannot grow a `config=` kwarg —
and its body lives in
`_build_with(proj, record, *, affinity, status_key, config=None)`. The
working state calls it with the stored record and the existing 2-tuple
`_status` slot; a pure-configuration build goes through
`_ensure_config_built`, which memoizes in a **separate**
`_config_status[(lock_key, part, config)]` and passes `status_key=None`. That
separation is why `get_project.parts[].state`, `get_part.status` and the tree
badge still mean *the working state* — and it is a livelock guard, not
bookkeeping: with one slot per part, two instances bound to different
configurations would miss the memo on alternate `get_assembly` reads,
republish `rebuild_finished` and drive the browser's refresh loop forever.
`service._record_for(proj, part_id, config)` returns the stored record or a
derived one whose `params` are the pure configuration map, so
`_cache_key_for`, `_content_signature`, `_solid_densities` and `_shape_item`
all work on a configuration build unchanged.

`build_configs` is **serial and de-duplicated by cache key** (compute every
requested member's pure key, build each distinct key once, fan the row back
out) with `affinity=part_id` throughout. There is no fan-out and no `--jobs`:
the same many-variants-of-one-part parallelism was measured at 1.08×/1.40×/1.17×
against a pre-registered 1.5× bar and deleted in PRD-011, and two members
sharing a key would race the worker's fixed-name staging file.

**Assembly geometry is content-addressed.** The one-mesh-per-part assumption
lived in exactly two places — the server's `_status` (above) and the browser's
`meshBuffers` map — and it is removed rather than patched: `get_assembly`
publishes each built instance's cache key as `mesh_key`, and the browser
fetches through `GET /api/projects/{proj}/meshes/{key}?lod=`, a pack route
that serves `.cache/<key>.acm` behind a `^[0-9a-f]{32}$` gate and **never
builds** (an unbuilt key is a 404, so a browser cannot storm the kernel
through it). There is no `?config=` query parameter — one identity for a mesh,
not two. Part mode needed no mesh work at all: `active_config` resolves in the
manifest path, so the part-keyed route already serves the active
configuration's geometry.

Binding validation lives in `ProjectStore.set_instances`, beside the existing
unknown-part and dangling-mate refusals, because three writers reach the store
and only the store sees all three. The merge reaches
`parts.<id>.configs.<name>.params.<param>` (the key-space table in [Branches,
versions and merges](#branches-versions-and-merges) above), and
`manifest_merge.config_problems` reports the hybrid a clean key-wise merge can
still produce — a binding whose configuration the other branch removed.

Reached through the ordinary extension points: `core/tools_configs.py` (five
tools, sorting at `con` — before `drawing`, `holes`, `packages`, `proposals`,
`specs` and `vision`, so it reads `service.specs` and `service.packages`
inside handlers behind `getattr` and never touches `gate_providers`),
`server/routes_configs.py`, and `frontend/js/configs.js` plus the config bar
in `inspector.js`.

Full reference: [`docs/agent-api.md`](agent-api.md#configurations) and the
user-facing [`docs/user-guide.md`](user-guide.md#configurations).

## Navigation at scale (PRD-027)

Three core modules, two route packs and one tool pack, all of them **outside
the geometry path**: `folder` and `tags` are additive manifest fields that
never enter `_cache_key`'s payload, and search, the dashboard and every
thumbnail read only what is already on disk or already in memory.

**Metadata.** `PartRecord.folder`/`tags` and `InstanceSpec.folder` are written
by `to_manifest` **only when set**, so a project nobody has organized
serializes byte-identically to before the feature existed. The store's
`update_part_meta` writes one part; `update_parts_meta(proj, edits)` validates
every id and every edit key first, then does one mutation pass and **one**
`save_manifest` — which is what makes a bulk gesture one git snapshot and one
undo entry. Its lock precondition is the caller's: `manifest_scope(store,
proj)` **then** `service._lock`, outer to inner (taking `manifest_scope` inside
the store would invert the order `tools_configs.set_instance_config` already
established).

**Search.** No index is maintained and none is invalidated. `Engine` scans the
manifest on demand behind a memo validated by `(mtime_ns, size)` on
`project.json` (and per script path for the text), which measured cold 59–120 ms
and warm 6–13 ms over a 1 000-part project against a 500/100 ms budget. Both
the tool and `GET …/search` go through the same call, and `frontend/js/query_model.js`
is a port of the same grammar and ranker — the agreement between the two
languages is written down once, in `tests/fixtures/search_queries.json`, and
driven through both.

**The thumbnail cache.** A part thumbnail is a 192² PNG at
`.cache/<key>.thumb.png`, where `<key>` is the part's build cache key — the
same key `get_project` reports as `thumb_key` and `rebuild_finished` now
carries as `cache_key`. A project (assembly) thumbnail is a composite over the
placed instances at `.cache/asm-<hash>.thumb.png` — a **dash**, not a dot, so
the cache janitor keys it on its own; `.thumb.png` is in the store's
`_TRIMMABLE` set, so a thumbnail is derived data the janitor may sweep at any
time.

Because the address is a content hash, these two routes are the codebase's
**first `immutable` responses**. Every other mesh/render route answers
`no-store` for a good reason: it is addressed by part id, so a cached copy
goes stale the instant the part rebuilds. Here, a request that names the key
being served (`?k=<thumb_key>`) gets `Cache-Control: private, max-age=31536000,
immutable` — a rebuild mints a *different* key and therefore a different URL,
so nothing can be stale. Anything else gets `no-cache` plus `ETag: "<key>"`,
and the 304 is decided from the key **before** any render or file read.

Nothing on this path builds. `thumbnails._instances` walks
`store.instances` directly and expands only *linear* patterns, purely, rather
than calling `service._resolved_instances` — which `tools_structure` rebinds to
`mates.resolve_project`, and which issues a kernel `resolve_assembly` for every
polar-pattern and sub-assembly member. Mated, polar and sub-assembly instances
therefore composite at their **stored** transform: a thumbnail is a hint, and
`render_view` renders the resolved truth. The warmer is constructed in the tool
pack and **started only by `routes_thumbnails.build_router`**, so exactly the
process that serves `thumb.png` runs the thread; `AGENTCAD_THUMBNAILS=off`
opts out.

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
export. **Structured STEP import reads the same convention back**:
`gp_Quaternion.GetEulerAngles(gp_Intrinsic_XYZ)` on the composed `gp_Trsf`,
which is byte-for-byte what `build123d.Location.orientation` does, so an
imported occurrence re-places exactly.

**Up axis, at the two export boundaries.** AgentCAD is Z-up everywhere.
**glTF is Y-up by fiat**, so `core/gltf.py` converts with **one root node**
carrying a fixed −90° X quaternion — never a per-caller flag — and states it in
the file (`asset.extras: {"source_up_axis": "+Z", "converted_to": "+Y"}`);
instance translations and rotations underneath it stay in the authored Z-up
frame, so a node's numbers still match the manifest. **USD carries both
natively**, so `core/usd_export.py` *declares* rather than converts
(`upAxis = "Z"`, `metersPerUnit = 0.001`) — and writes the pose as a single
`xformOp:transform` matrix, because pxr's `rotateXYZ` composes in the reverse
order of our intrinsic XYZ and the two agree only when at most one angle is
non-zero.

## Interop formats: what is deterministic, and how an import lands

**Deterministic:** glTF/GLB and USD are byte-identical across two exports of
one state (sorted keys and node/mesh ordering, floats rounded to 6 decimals, no
timestamp, no generator version), so a share link or a CI job may cache them by
content hash. **3MF is not, and never will be:** lib3mf mints a fresh
production-extension `p:UUID` per object per write, so two exports of one state
differ in bytes by construction — nothing content-hashes a 3MF (the same rule
DXF already lives under). Its one date, `CreationDate`, is PRD-014's resolved
*version* date rather than a wall clock, which is what keeps the rest of the
package stable; a project with no history gets no date at all rather than a
placeholder. The determinism test therefore compares the model XML with the
production-namespace UUIDs stripped.

**Structured import materializes one `.brep` per unique product.** The kernel
walks the file's XCAF document, writes each unique product's shape to
`imports/<stem>__<n>_<product>.brep` (atomic, deterministic names), and the
server registers each one as an ordinary **reference part** through the
existing, tested path — so `refload`'s content-addressed cache, the mesh
cache, the STL-boolean rules and LOD all apply unchanged, and exact B-rep is
preserved. The rejected alternative was a `(file, product_path)` selector on
the reference record: it threads a new axis through `refload`'s cache key,
`_content_signature` and every reference call site for no fidelity gain.
Occurrences then land as ordinary assembly instances — one `set_instances`
write for the whole batch, so a 41-occurrence import is one undo step on top of
the per-part ones, not 41.

## Trust model

A local, single-user engineering tool. Part scripts execute with user
privileges inside the worker subprocess — the same trust model as running any
code an agent writes in your working directory. **Design specs change nothing
here**: a part's `SPECS`, a project's `specs.py` and every `check_that`
predicate run in that same confined worker and never in the server process.
What is new is only that a *project-level* file now executes too, under
identical confinement.

**The per-OS contract** (PRD-006). One promise, three mechanisms, and health
names which one is in force:

| | confinement | quota tiers |
|---|---|---|
| **macOS** | `sandbox-exec` seatbelt profile (`kernel/sandbox_macos.py`): deny-by-default, global read, writes only in the roots below, no network, signals to self only | `rlimit` (`RLIMIT_NPROC` only — `RLIMIT_AS`/`DATA`/`RSS` are `EINVAL` on Darwin) + `supervisor` |
| **Linux** | Landlock + seccomp applied by the worker **to itself** through `ctypes` before `import build123d` (`kernel/_confine.py`, `kernel/_preamble.py`): writes only in the roots below, no socket family but `AF_UNIX`, no `ptrace`/`process_vm_*`/`io_uring`, no `pidfd_*` at all (a `/proc/<pid>` directory fd is a valid pidfd, so `pidfd_send_signal` would route around the pid-argument signal rules) and no `process_madvise`, no signal at pid ≤ 0 or at the server. Needs Landlock ABI ≥ 3 in the boot `lsm=` list; below that it reports `off` rather than shipping a false-denying profile | `cgroup` (only with an operator-delegated v2 subtree) + `rlimit` (`RLIMIT_AS`, `RLIMIT_NPROC`) + `supervisor` |
| **Windows** | an **AppContainer** (PRD-006b, `kernel/sandbox_windows.py`): a per-installation package SID with **no capabilities** (the absence of `INTERNET_CLIENT` *is* the network denial — Winsock answers `WSAEACCES` / `[WinError 10013]`), the roots below granted to that SID with `icacls` (`(OI)(CI)M`; the interpreter, the venv and the app tree get `(OI)(CI)RX`), and the worker spawned through `CreateProcessW` + `STARTUPINFOEX` because `subprocess` cannot pass a lowbox token. The worker proves it from its own `TokenIsAppContainer` **and** a matching SID; a failed profile/ACL/spawn is `off` + warnings, and below Windows 8 it is `unsupported`. Two residuals, stated: the ACEs are **permanent and inheritable** (removal recipe in `docs/deployment.md`), and the profile name mixes a random per-installation salt (`<state-dir>/appcontainer.salt`) because a package SID is a *hash of the name* — unsalted, another local account could derive the SID and hold it. The SID is per installation, not per worker, so two live workers are separated by DAC alone | `job_object` (commit limit, active processes, CPU rate) + `supervisor` |

The writable roots are the same everywhere: the projects dir, registered
examples, an accepted `--work-dir`, the worker's **private**
`agentcad-worker-*` temp dir, the server's one `agentcad-work-*` root that
`agentcad check` and the package gate materialize their cells under, and
`<state-dir>/publications/build` — PRD-007's share-link/customizer variant
builds go through the SAME confined kernel pool
(`core/share_build.py`'s `self._store.build_root()`, which is exactly
`appmode.state_dir() / "publications" / "build"`). The shared system temp dir
is deliberately **not** granted: it let every worker read and write every
other worker's scratch. Neither is the rest of `~/.agentcad` — nothing in
`kernel/` or `toolkit/` reads or writes the config dir, every `load_config()`
caller is server-side, and the worker's `HOME` is its own private temp dir, so
a blanket grant would buy nothing and cost the sentence below; `secret.key`
and `auth/` sit beside `publications/`, not beneath it, and stay ungranted.
Reads follow a *posture*: `local` reads anywhere (the v1
stance), and `hosted` — Linux only, selected by `AGENTCAD_MODE=hosted` —
narrows reads to an allow-list that excludes `AGENTCAD_STATE_DIR` (except that
one `publications/build` subtree, which is both a write root and therefore
readable) and leaves **nothing else under the server user's home** reachable,
so a member's script can no longer read the session signing key. Note that the
allow-list is the read roots **plus the write roots**, which is why a write
root is always readable — and why a hosted `agentcad serve` **refuses to
start** when `AGENTCAD_STATE_DIR` itself lies inside a write root (exit 2,
naming both paths; compose's `/data/state` is a sibling of `/data/projects`,
not a child, and `<state-dir>/publications/build` is a *child* of the state
dir, not a container of it, so it never trips this guard).
The worker's own `HOME` is its private temp dir, so `~` inside a part script
is never the server user's home. Forks and
`exec`s inherit the confinement.

A script can still compute anything, and under `local` read world-readable
files, but it cannot modify files outside its projects and cannot reach the
network from inside the worker. `/api/health` reports the effective state as
an object — `sandbox: {status, mechanism, posture, confinement, quotas,
warnings}`, where the top-level `status` is the confinement's (`active` |
`off` | `unsupported`) and is set from **the worker's own ping report**, never
from intent: a preamble that failed a stage is `off` with the failure in
`warnings`. Opt out with `AGENTCAD_NO_SANDBOX=1` or `{"sandbox": false}` in
`~/.agentcad/config.json` (env wins) — which opts out of *confinement*, not of
the caps. Per-worker caps (`kernel/quotas.py`, layered defaults < config file <
`AGENTCAD_QUOTA_*` env < per-caller overrides) bound memory, address space,
process count and CPU; a breach rides the existing error contract
(`script_error` + `details.denied`, or `kernel_crash` + `details.reason`), and
every response carries its `usage` (CPU ms, wall ms, peak RSS) for the meter in
`core/usage.py`. Per-project disk budgets
(`.cache/`, `exports/`, `imports/`; `AGENTCAD_QUOTA_DISK_MB`) refuse a build
or an export before the worker writes, and a cache janitor deletes the oldest
unreferenced meshes once the cache passes 75 % of the budget — never one
younger than ten minutes, because the keep-set is this *process's* memory and
that is empty after a restart. Unchanged mitigations: the server
binds `127.0.0.1` only; kernel requests time out; the worker is isolated so
kernel crashes never take down the app; scripts live in the project directory
where humans review them; the Anthropic API key is read from the environment
and never persisted.

**Installed packages change nothing about that, and the publish gate is not a
security boundary.** A package is Python; `use_part` copies it into the
project and the next rebuild executes it in the same confined worker with the
same privileges. The gate proves that geometry builds, that specs pass and
that connectors mate — never intent. What *is* enforced is content integrity:
an index declares a content id, the cache verifies every fetched tree against
that declaration before installing, and `use_part` re-verifies the whole tree
on every materialisation, so a tampered download, checkout or cache is
refused. An index that lies about both the tree and the id is a compromised
index; the `signatures` slot is reserved and empty for that, and the
confinement above is the backstop — a package's script gets no network and no
write outside the project either.

**Hosted mode (PRD-005a) changes who can reach that trust model, and says so
plainly.** With `AGENTCAD_MODE=hosted` the server binds a public interface and
every request resolves to an authenticated principal or to *anonymous*. The
authentication is real — invite-only accounts, server-side sessions, revocable
bearer tokens, default-deny on every route — but **it is not isolation between
the people using the instance**. Since PRD-006 the Linux worker *is* confined
(the table above, `hosted` posture: no network, no writes outside the projects
tree, no reads of the state dir and nothing under the server user's home but
the config dir, capped memory/pids/CPU), which is
what makes the session key unreachable from a part script. What confinement
does not do is separate one member from another: the script runs as the server
user and the **whole projects tree** is readable and writable to it.
Therefore:

- **An account on a hosted instance is still for someone you trust.** Give one
  only to someone you would give a shell to. Registration is closed by
  construction — there is no self-registration route; an admin mints a
  single-use enrolment link.
- **`member` and `admin` are not a security boundary between each other.**
  Admins manage users and tokens; both can run code and both can read and
  write every project. Authorization is deliberately flat because a
  per-project ACL would be a *label* and not a boundary until per-project
  isolation exists; PRD-005 is where it does.
- **The anonymous surface is nine entries and executes nothing**: `/`, the
  static `/js`, `/css`, `/vendor` mounts, a `/api/health` trimmed to
  `{status, mode}`, `POST /api/auth/login`, `GET|POST /api/auth/enrol/{token}`,
  and four read-only `/api/public/packages…` routes that serve pre-generated
  `scope: "public"` index JSON and shipped preview PNGs. Every one of them is a
  file read; a test exercises the whole surface with the kernel instrumented
  and asserts **zero** kernel requests, with a positive control on the counter.
  A package carried only by a `scope: "private"` index answers the same 404 as
  one that does not exist.
- **What hosted mode refuses rather than confines**: `POST /api/projects/open`
  and the absolute-path form of `import_cad_file` (both are host-filesystem
  reach), a presence beacon naming an identity outside the caller's own
  namespace, and the full health body without a principal.
- **Residuals it does not close, named in the PRD**: cross-project reach for
  every member (per-project ACLs are PRD-005), an authenticated DoS — the caps
  bound each kernel job, not how many a member queues — no queryable audit log
  (history trailers and the proposal/comment `audit.jsonl` carry attribution),
  no envelope encryption of the state files (`0600` in a volume), and an
  unmetered — but cacheable and static — anonymous read.
