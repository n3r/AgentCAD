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
│   (60 tools,       (cache, events,     ~/AgentCAD/projects  │
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
| `agentcad/kernel/handlers/` | Worker handler packs (reference, drawing, analysis, fem, connectors, diff) merged into the worker at startup — see [extension points](#v2-extension-points). |
| `agentcad/kernel/refload.py` | Reference-CAD loader (STEP/BREP → solid, STL → mesh-only Face) with an LRU keyed by (realpath, mtime, size). Kernel-side only (imports OCP). |
| `agentcad/kernel/error_doctor.py` | Catalog of real OCCT/build123d failure signatures → plain-language diagnosis + fix; enriches every worker error's `details.hint`. |
| `agentcad/kernel/_mates_resolver.py` | Connector evaluation + mate-graph ordering (cycle rejection) + Joint-based resolution to concrete transforms. |
| `agentcad/toolkit/` | Part-authoring helpers importable from scripts: `safe_fillet`/`safe_shell`/`safe_bool`, the scipy sketch solver, `bd_warehouse` threads. |
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
| `agentcad/core/tools.py` | ToolRegistry — the 17 core tools defined once; discovers and loads `tools_*.py` packs. MCP and chat render from the merged registry. |
| `agentcad/core/tools_*.py` | Feature tool packs (import, materials, mates, drawing, analysis, sketch, locks, history, versioning, proposals), each exporting `register(registry, service)`. |
| `agentcad/server/app.py` | Core REST routes (thin), `/api/tools` passthrough, WebSocket channel, static hosting; mounts `routes_*.py` packs under `/api`. |
| `agentcad/server/routes_*.py` | Route packs (import upload, materials, single-instance PATCH, drawing + SVG preview, analyze + fem, sketch solve, history, branches/versions/merge, proposals). |
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
   a side verbatim, and never becomes conflict-marked text.
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
   instances/mates a clean key-wise merge can still produce) and interference
   diffed against the pre-merge target — only **new** pairs block.
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
`service.gate_providers` (the empty list PRD-003's specs and PRD-004's checks
append to, so neither has to touch `proposals.py`). Like the branch tools, the
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
first (`state`, `approvals`, `validation`, plus any provider-supplied `specs`
and `checks`) and refuses a red one with a `conflict_error` naming it; then it
calls `MergeOrchestrator.merge` and forwards its payloads verbatim, including
`merge_conflict`. `allow_invalid` is passed straight through to the kernel
validation gate and never touches the approvals policy.

**New event:** `proposal_changed {project, id, state, reason}` for every
state or packet transition.

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
