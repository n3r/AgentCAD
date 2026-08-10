# AGENTS.md — working on AgentCAD

Guidance for any AI agent (or human) contributing to this repo. Read this
before making changes. It is the canonical contributor guide; deeper design
docs live in `docs/`.

## What this project is

**AgentCAD** is an agentic-first parametric CAD system for complex engineering
parts (rocketry, construction, prototyping). The core bet: **the model is
code** — every part is a small parametric Python script using
[build123d](https://build123d.readthedocs.io) (OpenCascade/OCCT B-rep), which
is the representation agents read, write, and diff best. The **kernel is the
referee**: every change is validated by real geometry (validity, volume, mass,
interference), and failures return as structured data so agents self-correct.

The full capability surface is exposed three ways from one registry: a browser
UI, an **MCP server** (for Claude Code and other MCP clients), and a built-in
**chat agent**. All three are peers over the same service layer.

Status: working, `v0.1.0`, ~300 tests. macOS today; the architecture is pure
Python + browser UI so Windows/Linux are reachable (packaging only).

## Quick start

```bash
make setup        # uv sync  (installs build123d/OCCT wheels, ~2 GB, one time)
make test-fast    # two-worker suite excluding broad/timeout-driven slow tests
make test-pr      # required PR gate; defers exhaustive bundled-engine coverage
make test         # full two-worker suite; needs the kernel
make test-portability  # OS-sensitive filesystem/process/kernel smoke suite
make run          # start the server AND open the browser UI (port 8630)
make serve        # headless server only
make app          # build dist/AgentCAD.app (macOS launcher)
uv sync --extra fem   # optional: enable structural FEM (gmsh/scikit-fem/meshio)
```

CLI: `agentcad serve|open|mcp|new|export` (see `agentcad/cli.py`). Port is
`8630`, persisted in `~/.agentcad/config.json`; kernel pool size via
`AGENTCAD_KERNEL_POOL_SIZE` (default `min(3, cores//3)`).

MCP registration for Claude Code:
```bash
claude mcp add agentcad -- uv --directory /path/to/cad_claude run agentcad mcp
```

## Architecture (two processes + peer clients)

```
Claude Code (MCP) ─┐         Browser UI ─┐        Chat agent ─┐
                   │ HTTP               │ REST+WS            │ in-process
                   ▼                    ▼                    ▼
        ┌───────────────────────────────────────────────────────┐
        │  FastAPI server (127.0.0.1 only)                       │
        │  ToolRegistry ─► AgentCADService ─► ProjectStore(files)│
        │                        │ line-delimited JSON-RPC       │
        │              Kernel worker subprocess(es)              │
        │              (warm build123d/OCCT, restartable pool)   │
        └───────────────────────────────────────────────────────┘
```

- **`agentcad/kernel/`** — the geometry process. `worker.py` imports build123d
  once (warm, ~3 s) and serves JSON-RPC over stdin/stdout; `client.py` /
  `pool.py` own the subprocess(es) with per-request timeout and
  kill-and-respawn; `mesh.py` tessellates to the `ACM1` binary format
  (`acm.py`); `refload.py` loads imported CAD. **Only this package imports
  `OCP`.** The server process must never import build123d/OCP.
- **`agentcad/core/`** — `service.py` (the application service every client
  goes through: rebuild orchestration, content-hash mesh cache, EventBus),
  `project.py` (filesystem project store, atomic writes), `model.py`
  (dataclasses + `AppError` subclasses), `tools.py` (the ToolRegistry),
  `materials.py`, `templates.py`.
- **`agentcad/server/app.py`** — FastAPI: thin REST wrappers, a WebSocket
  event channel, the generic `/api/tools/{name}` passthrough (what MCP
  proxies), and static frontend hosting.
- **`agentcad/agent/`** — `mcp_server.py` (stdio MCP proxy, auto-starts the
  server) and `chat.py` (Anthropic tool-use loop).
- **`agentcad/toolkit/`** — helpers importable *from part scripts*:
  `safe_fillet`/`safe_shell`/`safe_bool`, `sketch` (constraint solver),
  `threads` (bd_warehouse).
- **`frontend/`** — static ES modules, no bundler; Three.js + CodeMirror
  vendored under `frontend/vendor/`.

Read `docs/architecture.md` for the full picture and a rebuild data-flow walk.

## The extension-point contract — HOW TO ADD A FEATURE

v2 features are vertical slices that plug into pre-wired discovery points. **Do
not edit `worker.py` / `tools.py` / `app.py` / `service.py` cores to add a
feature** — add packs:

1. **Worker handler pack** — `agentcad/kernel/handlers/<name>.py` exporting
   `HANDLERS: dict[str, callable]` or `register(toolbox) -> dict`. The
   `toolbox` (see `worker.WORKER_TOOLBOX`) hands you `b3d`, `build_shape`,
   `build_shape_ns`, `metrics`, `shape_volume`, `place`, `export_shape`,
   `atomic_write`, `tessellate`, `WorkerError`, and the error-type constants.
   Handlers return JSON-able dicts; raise `WorkerError(type, msg, {details})`.
   Merged at worker startup (`worker._load_handler_packs`).
2. **Tool pack** — `agentcad/core/tools_<name>.py` exporting
   `register(registry, service)`. Use `agentcad.core.tools.schema(props,
   required)` and `with_hint(result)`. Register a tool **only if it can run**
   (e.g. FEM tools skip themselves when the extra is absent).
3. **Route pack** — `agentcad/server/routes_<name>.py` exporting an
   `APIRouter` named `router` or `build_router(service, registry)`. Mounted
   under `/api`. Raise `NotFoundError`/`ValidationError`/`ConflictError`
   (mapped to 404/422/409). **Whitelist request-body keys** you forward to a
   tool — don't `**body` (the registry rejects unknown/`null`-typed args).
4. **Toolkit module** — `agentcad/toolkit/<name>.py` for part-script helpers;
   re-export the public name from `toolkit/__init__.py` if it's a top-level
   symbol.

`AgentCADService` also exposes seams you extend rather than fork:
`self.materials` (density resolver), `mates.resolve` (assembly mates), the
kernel `affinity=` kwarg (pool routing), `ProjectStore.write_guard` (turn
locking), and `self.history`/`self.undo_cursor` (git-backed snapshots +
undo/redo — a mutating pack needs NO per-call hook: publishing
`project_changed` after its write is what triggers the snapshot).

## The part-script contract

A part is a plain build123d script defining `PARAMS` (numeric specs with
`default`, optional `min`/`max`/`unit`/`description`) and `build(p)` returning
a `Part`/`Solid`/`Compound`. Optional additions: `connectors(p, part)` for
assembly mates, and `from agentcad.toolkit import …` for robust ops. Full
contract + cheat-sheet: `docs/part-authoring.md` and the `part_template` tool.

## build123d / OCCT gotchas (hard-won — read before touching geometry)

- **Version is pinned** (`build123d>=0.11.1`, via `uv.lock`). The 0.x API
  drifts; the test suite is the compatibility harness. Don't bump casually.
- **Boolean intersection volume**: `Shape.intersect()` returns a `ShapeList`
  (no `.volume`). Use the `&` operator for a single `Part` (empty `Compound`
  when disjoint). See `worker.handle_interference`.
- **Nested `Compound.volume` undercounts** (reports only the first child
  subtree). Sum over `shape.solids()` — `worker._shape_volume` does this.
- **Rotations are intrinsic XYZ Euler degrees** everywhere: `build123d
  Location(pos, rot)`, the manifest `rotation_deg`, and THREE.Euler `'XYZ'`
  all agree. `service._apply_transform` applies the rotations Z→Y→X to the
  point (a round-trip is covered by `tests/test_kernel.py`).
- **Imported STL is one welded mesh face with no surface** (`BRep_Tool.
  Surface_s(face) is None`). It needs **crease-angle normals**, not the smooth
  per-vertex average B-rep faces use, or shading melts (see `mesh.py`).
- **STL booleans segfault OCCT** — mesh-kind references are blocked from
  booleans and excluded from interference (`skipped_mesh`).
- **`is_valid` and `is_manifold` are properties, not methods.**
- Fillet/shell/boolean fail readily; prefer the `toolkit.safe_*` helpers, and
  the Error Doctor (`kernel/error_doctor.py`) turns raw OCCT errors into hints.

## Branching gotchas (PRD-001 — read before touching the store or history)

- **Branch worktrees live at `.history/trees/<branch>/`** — *not*
  `.history/worktrees/`, which is git's own per-worktree admin directory.
  Sidecars (default branch, per-client checkouts, tag referrers, a staged
  merge) live at `.history/agentcad/`. Both are inside GIT_DIR, so they are
  never committed.
- **`ours` = the TARGET branch, `theirs` = the SOURCE** everywhere (payloads,
  tool descriptions, UI labels), exactly like `git merge <source>`. Getting it
  backwards silently discards someone's work.
- **`.cache/` is canonical and shared by every branch** (content-addressed
  keys). Use `store.canonical_path_of` for derived data and `store.path_of`
  for authored state; `store.lock_key` — not the bare project name — for turn
  locks and undo stacks.
- Authored-state access goes through the `ProjectStore.branch_resolver` seam,
  so nothing else needs to know about branches. `BranchManager.pinned()` is the
  only override, and only the merge validation pass uses it.
- **`project.json` never merges line-wise.** It is always re-merged by
  `core/manifest_merge.py` (pure, I/O-free), even when git thought the file
  merged cleanly.
- Every git call goes through `ProjectHistory._run` (hermetic env, 10 s
  timeout, never raises into a save) — never a raw `subprocess`. Merging needs
  **git 2.38+**; branches and tags do not, and with no git at all the
  versioning pack registers nothing.

## Proposal gotchas (PRD-002 — read before touching proposals or the packet)

- **Proposals are canonical and branch-independent**, at
  `.history/agentcad/proposals/<id>/` — they are workflow metadata, not model
  state, so `project_restore` must never rewind them and every branch sees the
  same list. Never write proposal state into a working tree.
- **`audit.jsonl` is appended, never atomically replaced.** FR14 makes it
  append-only; a read-modify-replace cycle breaks that and can truncate the log
  on a crash. Everything else under the proposal directory is an atomic write.
- **The packet degrades, it never raises.** A per-part stage that fails
  (build, metrics, render, geometric diff) writes its structured error into the
  payload — `build.<side>.ok: false`, `geom_diff.available: false`,
  `warnings`/`errors` — and the packet still returns `ok: true`. `ok: false`
  means *no* packet could be produced (unreadable manifest, unknown ref). A
  packet is evidence; "the new side does not build" is evidence.
- **`geom_diff` volumes come from the toolbox's `shape_volume`** (the solids
  sum), not `.volume` — a boolean result is routinely a nested Compound — and a
  **mesh-kind (imported STL) side is never booleaned** (it segfaults OCCT); it
  comes back `skipped: "mesh"`.
- **Matched renders need the explicit `frame=`.** `render_acm` auto-fits per
  mesh, so two renders of different geometry do not superimpose; the packet
  passes the union of both world bboxes to both sides.
- **`allow_invalid` overrides the kernel validation gate only** — never the
  approvals policy. One field must not mean two unrelated things.
- **`actor_kind` is `human` only for the `browser` identity.** The chat dock is
  a human asking an *agent*, so those actions are the agent's. It is
  bookkeeping, not authentication, until PRD-005.
- A packet is generated from the **branches' existing worktrees**
  (`branches.tree_of` + `branches.pinned`) and refuses a dirty tree, so its
  pinned head SHAs always describe the bytes it measured.

## Conventions (match these)

- **Structured errors**: `{"error": {"type", "message", "details"}}`; script
  failures carry `details.traceback` and `details.line`, and `details.hint`
  from the Error Doctor. Mutating operations return post-state, never bare OK.
- **Atomic writes** (temp + `os.replace`) for every manifest/script/cache file.
- **Determinism**: same script + params ⇒ identical geometry and byte-identical
  meshes. Cache key = `sha256(content, params, density, tolerance)`.
- **Security/trust**: server binds `127.0.0.1` only, with a Host-allowlist +
  same-origin guard (`server/app._browser_request_allowed`) on HTTP and WS.
  Part scripts run with user privileges by design (local single-user tool);
  on macOS the kernel worker is additionally confined by a deny-by-default
  `sandbox-exec` profile (`kernel/sandbox.py`: writes only in project roots,
  no network; `AGENTCAD_NO_SANDBOX=1` opts out). API key from env only,
  never persisted.
- Comment density and style: match the surrounding file. Comments state
  constraints the code can't, not narration.

## Testing (`make test`)

- Two xdist workers run by module scope, each with one **session-scoped
  `kernel` fixture** (`tests/conftest.py`) that amortizes the warm import.
- Ordinary service tests use `make_test_service`, which disables synchronous
  git snapshots; `tests/test_history.py` and MCP integration keep real history.
- Mark broad/process-heavy coverage `slow`; `make test-fast` excludes it while
  `make test-pr` can still include it when it is required for merge confidence.
- Mark only unusually broad stress coverage `exhaustive`; PR CI excludes it,
  while scheduled/manual exhaustive CI and local `make test` always run it.
  The marker is not a shortcut for an ordinarily slow regression test.
- Mark tests `portability` when they exercise behavior that can vary by host OS
  (filesystem/encoding, Git, subprocesses, local sockets, OCCT wheels, or CAD
  import/export). Linux and Windows CI run this focused group; do not mark pure
  domain logic merely to increase the cross-platform test count.
- **Examples tests run on a copy** — never mutate `examples/` in place.
- FastAPI `TestClient` must pass `base_url="http://127.0.0.1"` and, for
  WebSocket tests, `create_app(..., extra_allowed_hosts={"testserver"})` (the
  host guard).
- FEM tests use `pytest.importorskip` — the suite is green **without** the
  `[fem]` extra. **Never `uv sync`/`uv pip install` into the shared venv from a
  parallel agent** — use a scratch venv.
- Reproduce a bug as a failing test before fixing (see the mesh-normals fix and
  its `tests/test_mesh.py` regression tests for the pattern).

## Changelog — REQUIRED for every commit

**Every commit must include a detailed changelog entry under
`docs/changelog/`.** Stage it *with* the change so the entry lands in the same
commit. One file per commit, named `NNNN-<slug>.md` where `NNNN` is the next
zero-padded sequence number (highest existing + 1) and `<slug>` is a short
kebab-case summary. Follow the template in `docs/changelog/README.md`:
a header (sequence, date, one-line summary), a **Summary** paragraph, a
**Changes** list (grounded in what the diff actually does, not just the commit
subject), a **Files** list, and **Notes** (rationale, gotchas, follow-ups).
Write the changelog from the real diff, not from memory.

## Definition of done for a change

1. `make test` green (state the count; no unexplained skips).
2. New/changed behavior has a test; a bug fix has a regression test.
3. Docs updated if the surface changed (README, `docs/*.md`, and the
   `CHEATSHEET` in `templates.py` for authoring-facing changes).
4. For UI changes: verify in a real browser (screenshot), zero console errors.
5. **A `docs/changelog/NNNN-<slug>.md` entry is written and staged with the
   change** (see the Changelog section above).
6. Commit messages end with the `Co-Authored-By` trailer. Don't commit
   server-generated manifest reformatting churn or the shared venv.

## Where to read more

- `docs/architecture.md` — processes, components, ACM1 format, rebuild flow
- `docs/agent-api.md` — the 42/45 agent tools with schemas + a worked loop
- `docs/part-authoring.md` — the script contract, toolkit, mates, sketch solver
- `docs/user-guide.md` — the UI surface by surface
- `docs/roadmap.md` — the PRD index with statuses (what we're building and why)
- `docs/prd/` — one detailed PRD per roadmap feature (see `docs/prd/README.md`)
- `docs/market_research.md` — the competitive/market evidence behind the roadmap
- `docs/superpowers/specs|plans/` — the design specs and implementation plans
