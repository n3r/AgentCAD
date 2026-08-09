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

Status: working, `v0.1.0`, ~122 tests. macOS today; the architecture is pure
Python + browser UI so Windows/Linux are reachable (packaging only).

## Quick start

```bash
make setup        # uv sync  (installs build123d/OCCT wheels, ~2 GB, one time)
make test         # uv run pytest -q   (full suite; needs the kernel)
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

`AgentCADService` also exposes three seams you extend rather than fork:
`self.materials` (density resolver), `mates.resolve` (assembly mates), and the
kernel `affinity=` kwarg (pool routing).

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

## Conventions (match these)

- **Structured errors**: `{"error": {"type", "message", "details"}}`; script
  failures carry `details.traceback` and `details.line`, and `details.hint`
  from the Error Doctor. Mutating operations return post-state, never bare OK.
- **Atomic writes** (temp + `os.replace`) for every manifest/script/cache file.
- **Determinism**: same script + params ⇒ identical geometry and byte-identical
  meshes. Cache key = `sha256(content, params, density, tolerance)`.
- **Security/trust**: server binds `127.0.0.1` only, with a Host-allowlist +
  same-origin guard (`server/app._browser_request_allowed`) on HTTP and WS.
  Part scripts run with user privileges by design (local single-user tool) —
  document, don't sandbox-theater. API key from env only, never persisted.
- Comment density and style: match the surrounding file. Comments state
  constraints the code can't, not narration.

## Testing (`make test`)

- One **session-scoped `kernel` fixture** (`tests/conftest.py`) amortizes the
  warm import; kernel-dependent tests share it.
- **Examples tests run on a copy** — never mutate `examples/` in place.
- FastAPI `TestClient` must pass `base_url="http://127.0.0.1"` and, for
  WebSocket tests, `create_app(..., extra_allowed_hosts={"testserver"})` (the
  host guard).
- FEM tests use `pytest.importorskip` — the suite is green **without** the
  `[fem]` extra. **Never `uv sync`/`uv pip install` into the shared venv from a
  parallel agent** — use a scratch venv.
- Reproduce a bug as a failing test before fixing (see the mesh-normals fix and
  its `tests/test_mesh.py` regression tests for the pattern).

## Definition of done for a change

1. `make test` green (state the count; no unexplained skips).
2. New/changed behavior has a test; a bug fix has a regression test.
3. Docs updated if the surface changed (README, `docs/*.md`, and the
   `CHEATSHEET` in `templates.py` for authoring-facing changes).
4. For UI changes: verify in a real browser (screenshot), zero console errors.
5. Commit messages end with the `Co-Authored-By` trailer. Don't commit
   server-generated manifest reformatting churn or the shared venv.

## Where to read more

- `docs/architecture.md` — processes, components, ACM1 format, rebuild flow
- `docs/agent-api.md` — the 25/26 agent tools with schemas + a worked loop
- `docs/part-authoring.md` — the script contract, toolkit, mates, sketch solver
- `docs/user-guide.md` — the UI surface by surface
- `docs/roadmap.md` — what's intentionally not built yet, and why
- `docs/superpowers/specs|plans/` — the design specs and implementation plans
