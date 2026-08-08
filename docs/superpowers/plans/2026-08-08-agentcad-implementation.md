# AgentCAD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read the spec first: `docs/superpowers/specs/2026-08-08-agentcad-design.md` — every contract there is binding.

**Goal:** A working agentic-first parametric CAD application (Python/build123d kernel, FastAPI service, browser UI, MCP + built-in chat agent) with documentation, examples, and tests, running on macOS.

**Architecture:** Long-lived kernel worker subprocess (warm build123d/OCCT) speaking line-delimited JSON-RPC to a FastAPI server that owns project files and a single ToolRegistry; browser UI (static ES modules + Three.js) and agents (MCP stdio proxy, in-server Anthropic tool loop) are peer clients of the same service layer.

**Tech Stack:** Python 3.12, uv, build123d ≥0.9, FastAPI, uvicorn, anthropic, mcp, pytest, httpx; frontend: vanilla ES modules, Three.js (vendored), CodeMirror 5 (vendored). No bundler.

## Global Constraints

- Python 3.12; env managed by uv (`.venv` in repo, `uv sync`); repo root `/Users/nfedorov/dev/personal/cad_claude`.
- Server binds `127.0.0.1` only. Port persisted in `~/.agentcad/config.json`.
- All vendor JS/CSS committed under `frontend/vendor/` — zero CDN/network references in the frontend.
- Part ids and project names: `[a-z][a-z0-9_]{0,39}`.
- Units: millimeters, grams, degrees. Angles in APIs are degrees.
- Every mutating API path returns post-state (metrics or structured error) — never bare 200.
- Structured error shape everywhere: `{"error": {"type", "message", "details"}}`; script failures embed `traceback` and `line` in `details`.
- Atomic file writes (tmp + `os.replace`) for manifests, scripts, cache files.
- Commit after each task (`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`).
- Tests run with `make test` = `uv run pytest -q`. Kernel-dependent tests share one session-scoped worker (fixture in `tests/conftest.py`) to amortize the 3 s warm import.

## File structure (locked)

```
pyproject.toml  Makefile  .gitignore  README.md
agentcad/__init__.py            # __version__
agentcad/cli.py                 # serve|open|mcp|new|export
agentcad/config.py              # port/config persistence (~/.agentcad/config.json)
agentcad/kernel/protocol.py     # request/response dataclasses + framing constants
agentcad/kernel/worker.py       # subprocess entry: build/export/interference/ping
agentcad/kernel/mesh.py         # OCP per-face tessellation + ACM1 binary writer (worker-side)
agentcad/kernel/client.py       # KernelClient: spawn/timeout/respawn
agentcad/core/model.py          # dataclasses: ParamSpec, PartRecord, InstanceSpec, Metrics, RebuildResult, errors
agentcad/core/materials.py      # MATERIALS table
agentcad/core/project.py        # ProjectStore: manifest/scripts CRUD, validation
agentcad/core/service.py        # AgentCADService + EventBus
agentcad/core/tools.py          # ToolRegistry + build_registry(service)
agentcad/core/templates.py      # default part script template + build123d cheat-sheet text
agentcad/server/app.py          # create_app(service) FastAPI + WS + static
agentcad/agent/chat.py          # Anthropic tool-use loop
agentcad/agent/mcp_server.py    # MCP stdio proxy (auto-start server)
frontend/index.html css/app.css js/{main,api,state,viewport,tree,inspector,editor,chat}.js vendor/*
examples/{rocketry,construction,prototyping}/{project.json,parts/*.py,README.md}
scripts/make_app.sh  scripts/vendor_frontend.sh
tests/conftest.py tests/test_{kernel,mesh,project,service,tools,server,mcp,examples}.py
docs/{architecture,agent-api,part-authoring,user-guide,roadmap}.md
```

---

### Task 1: Scaffold — pyproject, Makefile, package skeleton

**Files:** Create `pyproject.toml`, `.gitignore`, `Makefile`, `agentcad/__init__.py`, `agentcad/config.py`, empty package `__init__.py` files, `tests/test_config.py`.

**Interfaces (produces):**
- `agentcad.__version__ = "0.1.0"`.
- `agentcad.config.load_config() -> dict` / `save_config(cfg: dict)` — JSON at `~/.agentcad/config.json` (path overridable via env `AGENTCAD_CONFIG` for tests); `get_port() -> int` returns persisted port or allocates default `8630` and persists.

**Steps:**
- [ ] `pyproject.toml`: project `agentcad` 0.1.0, `requires-python = ">=3.12"`, deps: `build123d>=0.9`, `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `anthropic>=0.40`, `mcp>=1.2`, `httpx>=0.27`; dev group: `pytest>=8`, `pytest-timeout`; `[project.scripts] agentcad = "agentcad.cli:main"` (cli lands in Task 9 — create a stub `main()` printing version now).
- [ ] `.gitignore`: `.venv/ __pycache__/ *.pyc .cache/ dist/ exports/ .pytest_cache/ node_modules/`.
- [ ] `Makefile`: `setup` (`uv sync`), `run` (`uv run agentcad open`), `serve`, `test` (`uv run pytest -q`), `app` (`bash scripts/make_app.sh`).
- [ ] Write `tests/test_config.py`: with `AGENTCAD_CONFIG` pointed at tmp file, `get_port()` returns 8630 and persists; second call reads persisted value after manual edit.
- [ ] `uv sync` (expect several minutes, OCP wheel); run `uv run pytest -q` → config tests pass.
- [ ] Commit.

### Task 2: Kernel worker + mesh + client

**Files:** Create `agentcad/kernel/{protocol,worker,mesh,client}.py`, `tests/conftest.py`, `tests/test_kernel.py`, `tests/test_mesh.py`.

**Interfaces (produces):**
- Framing: one JSON object per line on stdin/stdout (`\n`-delimited, UTF-8). Request `{"id": int, "method": str, "params": {...}}`; response `{"id": int, "result": {...}}` or `{"id": int, "error": {"type": "script_error"|"kernel_error"|"contract_error", "message": str, "details": {"traceback": str, "line": int|null}}}`. Worker logs to stderr only.
- Methods:
  - `ping {} -> {"ok": true, "build123d": version}`
  - `build {script, params: {name: num}, density_g_cm3: num, mesh_path: str, tolerance: num=0.1} -> {"metrics": Metrics, "warnings": [str]}` — writes ACM1 file atomically at `mesh_path`.
  - `export {script, params, density_g_cm3, format: "step"|"stl"|"3mf", out_path, tolerance: num=0.05} -> {"path", "size_bytes"}`
  - `export_assembly {items: [{script, params, position:[3], rotation_deg:[3]}], format: "step"|"stl", out_path} -> {"path","size_bytes"}`
  - `interference {items: [{name, script, params, position:[3], rotation_deg:[3]}], min_volume: 0.001} -> {"pairs": [{"a","b","volume_mm3"}]}`
  - `shutdown {} -> {"ok": true}`
- `Metrics` keys: `volume_mm3, area_mm2, mass_g, bbox {min:[3],max:[3]}, center_of_mass:[3], is_valid: bool, n_faces, n_edges, n_solids`.
- Script execution: `exec(compile(script, "<part>", "exec"))` in fresh dict with `__name__="__agentcad_part__"`; require `PARAMS` dict (numeric `default`, optional `min`/`max`/`unit`/`description`) and callable `build`; resolve params = defaults ⊕ overrides, clamp to min/max collecting warnings `"param X clamped to ..."`; unknown override names → `contract_error`. `build` receives `types.SimpleNamespace`. Return of `BuildPart` context → take `.part`; accept `Part|Solid|Compound`; anything else → `contract_error`. Shape LRU (last 16) keyed by `sha256(script)|sorted(params)` reused by export/interference.
- Transform semantics: shape placed with `build123d.Rotation(rx, ry, rz)` (intrinsic XYZ) then translated by `position` (must match Three.js `Euler(rx,ry,rz,'XYZ')` — verified in Task 6).
- ACM1 mesh binary (little-endian): magic `ACM1`, then u32×4 `nv, nt, ne_points, ne_lines`, f32×3nv positions, f32×3nv normals, u32×3nt indices, u32×ne_lines polyline lengths (sum = ne_points), f32×3ne_points edge points. Per-face triangulation via `BRepMesh_IncrementalMesh(shape, tolerance, False, 0.5, True)` + `BRep_Tool.Triangulation_s(face, loc)`; vertices transformed by `loc`; normals per-vertex from `Poly_Triangulation` UV + surface, or per-triangle geometric normals accumulated; faces with `TopAbs_REVERSED` flip normals and winding. Edges from `BRep_Tool.PolygonOnTriangulation_s` (fallback `Polygon3D`), one polyline per edge.
- `KernelClient(python_exe=sys.executable, timeout_s=60.0)`: `.start()`, `.request(method, params, timeout_s=None) -> dict` (raises `KernelError(type, message, details)` on error response; kills+respawns on timeout/EOF then raises `KernelError("timeout"|"kernel_crash", ...)`), `.stop()`. Thread-safe via lock (one in-flight request).

**Steps:**
- [ ] Write `tests/conftest.py`: session fixture `kernel()` yielding started `KernelClient`; `pytest-timeout` default 120 s.
- [ ] Write failing tests (`tests/test_kernel.py`): ping ok; build of the spike plate script (embed it) → volume ≈ 48438.2 ±1, `is_valid`, mass = volume×2.7/1000 for al6061 (density passed 2.70), mesh file exists with `ACM1` magic; param override changes volume; unknown param → `contract_error`; syntax error → `script_error` with `line`; `while True: pass` script with `timeout_s=3` → `KernelError` type `timeout` and next `ping` works (respawn); missing `PARAMS` → `contract_error`; export step+stl produce files >1 KB and STEP starts with `ISO-10303-21`; interference of two overlapping boxes ≈ expected overlap volume ±1%; determinism: two builds → byte-identical mesh files.
- [ ] `tests/test_mesh.py`: parse ACM1 from a built box: counts>0, all normals unit-length ±1e-3, bbox from positions matches metrics bbox ±tolerance, indices < nv, sum(polyline lengths) = ne_points.
- [ ] Run tests → fail (modules missing).
- [ ] Implement `protocol.py`, `mesh.py`, `worker.py`, `client.py` per interfaces.
- [ ] Run `uv run pytest tests/test_kernel.py tests/test_mesh.py -q` → pass. Commit.

### Task 3: Core model, materials, project store

**Files:** Create `agentcad/core/{model,materials,project,templates}.py`, `tests/test_project.py`.

**Interfaces (produces):**
- `model.py`: dataclasses `ParamSpec(name, default, min, max, unit, description)`; `PartRecord(id, label, material, params: dict[str,float])`; `InstanceSpec(id, part, position: list, rotation_deg: list, color: str|None)`; `AppError(Exception)` subclasses `NotFoundError`, `ValidationError(details: dict)`, `ConflictError`; `ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")`.
- `materials.py`: `MATERIALS: dict[str, Material(id,label,density_g_cm3)]` with the 10 spec materials; `get_material(id)` raises `ValidationError` on unknown.
- `templates.py`: `DEFAULT_PART_SCRIPT` (parametric rounded plate using the contract) and `CHEATSHEET: str` (≈60 lines: contract rules + common build123d idioms: Box/Cylinder/Hole/fillet/chamfer/extrude/revolve/loft/patterns/booleans + edge-selection examples).
- `project.py`: `ProjectStore(root: Path)` — `list_projects()`, `create(name) -> Path`, `open(path) -> Path` (validates manifest, registers external dir), `manifest(proj) -> dict`, `save_manifest(proj, m)` (atomic), `part_ids(proj)`, `get_part(proj, id) -> PartRecord`, `read_script(proj, id) -> str`, `write_script(proj, id, text)`, `add_part(proj, id, label, material, script)`, `remove_part(proj, id)` (raises `ConflictError` if instanced), `set_part_params/label/material`, `instances(proj)`, `set_instances(proj, list[InstanceSpec])` (validates part refs, unique ids), `cache_dir(proj)`, `exports_dir(proj)`. Manifest schema per spec §5; unknown keys preserved on save.

**Steps:**
- [ ] Write failing tests: create→list round-trip; add_part writes script file + manifest entry; invalid id rejected; unknown material rejected; set_instances with missing part ref → ValidationError; remove instanced part → ConflictError; atomic save (manifest still valid JSON after simulated crash = write to tmp verified by monkeypatching `os.replace` to raise after tmp write, original intact); open() of a dir missing project.json → ValidationError.
- [ ] Run → fail. Implement. Run → pass. Commit.

### Task 4: Service layer + EventBus + ToolRegistry

**Files:** Create `agentcad/core/{service,tools}.py`, `tests/test_service.py`, `tests/test_tools.py`.

**Interfaces (produces):**
- `EventBus`: `subscribe() -> asyncio.Queue` / `unsubscribe(q)` / `publish(event: dict)` (sync-safe; drops if queue full at 256).
- `AgentCADService(projects_dir: Path, kernel: KernelClient, bus: EventBus)` — methods exactly as spec'd (§4 component list + §6 route needs): `list_projects, create_project, open_project, get_project, create_part, get_part, update_part, set_params, delete_part, ensure_mesh(proj, part) -> Path, get_metrics, export_part, get_assembly, set_assembly, check_interference, export_assembly, part_template()`. Rebuild flow: cache key `sha256(script + json(sorted params) + density + tolerance)`; hit → reuse `.cache/<key>.acm` + `<key>.metrics.json`; miss → kernel `build`, store both. `RebuildResult = {"ok": true, "metrics", "warnings"} | {"ok": false, "error": {...}}` — update_part/set_params persist the change *then* rebuild (a broken script is saved, marked with error; last good mesh retained). Publishes `rebuild_started/finished/failed` with `{project, part}` payloads.
- `tools.py`: `Tool(name, description, input_schema, handler)`; `ToolRegistry.list() -> [Tool]`, `.call(name, args: dict) -> dict` (validates args against schema minimally: required keys + types; returns JSON-able dict; converts `AppError`/`KernelError` to `{"error": {...}}` payloads rather than raising). `build_registry(service) -> ToolRegistry` registering the 17 spec §7 tools. Every tool result includes `"hint"` only when actionable (e.g. rebuild failed → hint to fetch `part_template`).

**Steps:**
- [ ] Failing tests (`test_service.py`, real kernel fixture, tmp projects dir): create project → create part (default template) → metrics valid; set_params changes volume + second identical call hits cache (assert kernel `build` called once via counter monkeypatch on `KernelClient.request`); update with broken script → `ok:false` + error persisted in `get_part().status`, mesh file still previous; export creates file; assembly set + interference on two overlapping instances; events observed on a subscribed queue.
- [ ] Failing tests (`test_tools.py`): registry has 17 tools with valid JSON Schemas (`jsonschema` not required — assert dict shape: `type: object`, `properties`); `call("create_part", ...)` then `call("update_part_script", ...)` with bad script returns `error` payload (not raise); `part_template` includes `PARAMS` and cheat-sheet text.
- [ ] Run → fail. Implement. Run → pass. Commit.

### Task 5: FastAPI server + WebSocket + static hosting

**Files:** Create `agentcad/server/app.py`, `tests/test_server.py`. Modify `agentcad/config.py` if port helpers need async access.

**Interfaces (produces):**
- `create_app(service: AgentCADService, registry: ToolRegistry, chat_factory=None) -> FastAPI` — all §6 routes under `/api`, plus `GET /api/tools` (registry schemas) and `POST /api/tools/{name}` (generic call → result or error payload; this is what MCP proxies). Static: `/` serves `frontend/index.html`, `/js /css /vendor` static dirs. WS `/ws`: on connect subscribe bus, forward events as JSON; ping every 20 s. Mesh route streams ACM1 with `Cache-Control: no-store`, header `X-Mesh-Key: <cache key>`. Error handler maps `NotFoundError`→404, `ValidationError`→422, `ConflictError`→409, `KernelError`→502 with the structured error shape.
- Chat routes registered only when `chat_factory` provided (Task 7 wires it): `POST /api/chat {project, message}` → `{"turn_id"}` (runs loop in background task, streams over WS), `GET/DELETE /api/chat/history?project=`.

**Steps:**
- [ ] Failing tests with `TestClient`: health; project CRUD; part CRUD incl. broken-script 200-with-`ok:false` semantics; params patch; mesh endpoint returns bytes starting `ACM1`; export; assembly + interference; `/api/tools` lists 17; generic tool call works; 404/422/409 mapping; WS receives `rebuild_finished` after a params patch (TestClient websocket context).
- [ ] Run → fail. Implement. Run → pass. Commit.
- [ ] Smoke: `uv run uvicorn --factory` not needed — add `agentcad/server/__main__.py` minimal runner used by CLI later; `curl` health OK. Commit.

### Task 6: Frontend UI  *(parallel-safe: only touches `frontend/` + `scripts/vendor_frontend.sh`)*

**Files:** Create everything under `frontend/` per file structure, `scripts/vendor_frontend.sh`.

**Interfaces (consumes):** REST/WS exactly as Task 5; ACM1 format as Task 2; rotation semantics `THREE.Euler(rx,ry,rz,'XYZ')` degrees→radians.

**Requirements (binding):**
- `vendor_frontend.sh`: `npm i three@latest codemirror@5` into a temp dir, copy `three.module.min.js`, `OrbitControls.js` (rewrite its `from 'three'` to work via import map `{"three": "/vendor/three.module.min.js"}`), CodeMirror `codemirror.js/.css`, `mode/python/python.js`; commit vendored files.
- Layout per spec §9: left sidebar (project switcher + parts list + assembly instances), center viewport, right inspector (Parameters/Code/Metrics tabs), bottom-dock Agent chat, top toolbar (project name, fit view, export menu, rebuild indicator). Dark theme, `-apple-system` / `SF Mono`, restrained color, no gradients-for-decoration; selection states legible.
- Viewport: parse ACM1 into `BufferGeometry` (positions/normals/index) + `LineSegments` for edges (expand polylines); `MeshStandardMaterial` (metalness .1, roughness .8, per-instance color), hemisphere + directional light, ground grid, orbit controls, fit-to-bbox on load and on `F`; raycast click → select instance/part (syncs tree + inspector); assembly mode renders all instances with transforms; single-part mode renders active part at origin.
- Parameters tab: controls generated from spec (slider when min&max present, else number input), debounce 250 ms → `PATCH params` → apply returned metrics; warnings surfaced; rebuild errors → red banner with traceback (monospace, scrollable).
- Code tab: CodeMirror python; Cmd+S / button "Save & Rebuild" → `PUT part {script}`; on `ok:false` show banner, keep editing.
- Chat: `POST /api/chat`, render streamed WS `chat_delta/tool_call/tool_result/done` as bubbles + collapsible tool chips; no key → render setup empty-state (text from `GET /api/health` field `chat_available: bool` — Task 7 adds it; treat missing as false).
- All fetches through `js/api.js`; state changes through `js/state.js` (tiny pub/sub); no framework.
- Acceptance: with server running and `examples/prototyping` opened, a human sees the enclosure, edits `wall` param, watches the mesh update, breaks the script and sees the traceback banner, saves a fix, exports STEP.

**Steps:**
- [ ] Run vendor script; commit vendor files.
- [ ] Implement modules; manual smoke against running server with an example project; fix; commit.

### Task 7: Agent layer — chat loop + MCP server  *(parallel-safe: touches `agentcad/agent/`, small wiring diffs in `server/app.py`, `cli.py`)*

**Files:** Create `agentcad/agent/{chat,mcp_server}.py`, `tests/test_mcp.py`; modify `agentcad/server/app.py` (chat_factory wiring + `chat_available` in health), `agentcad/cli.py` (`mcp` subcommand).

**Interfaces:**
- `chat.py`: `ChatEngine(registry, bus, model="claude-sonnet-5", api_key=env)`; `available` property; `async run_turn(project: str, message: str, history: list) -> list` — Anthropic Messages API tool loop: tools rendered from registry (`name/description/input_schema`), system prompt states the part-script contract summary + "always fetch part_template before writing a first script"; max 30 tool calls/turn; publishes `chat_delta {text}`, `chat_tool_call {name,args}`, `chat_tool_result {name, ok}`, `chat_done` on bus tagged `{project}`. History kept in-memory per project (server lifetime) + `GET/DELETE` endpoints.
- `mcp_server.py`: stdio MCP server (`mcp` package). On start: `GET {AGENTCAD_URL|http://127.0.0.1:<config port>}/api/health`; on failure spawn `uv run agentcad serve --no-open` detached (cwd = repo root via `AGENTCAD_HOME` env or installed script), poll health ≤30 s. Tools = `GET /api/tools` mirrored 1:1; call = `POST /api/tools/{name}`; result returned as JSON text content. Errors returned as tool results (not protocol errors) so agents read them.
- Claude Code registration snippet (goes in docs + README): `claude mcp add agentcad -- uv --directory /path/to/cad_claude run agentcad mcp`.

**Steps:**
- [ ] Failing `tests/test_mcp.py`: spin real server on a free port (uvicorn thread) → run MCP server as subprocess with `AGENTCAD_URL` → MCP client handshake (`mcp` package client) lists 17 tools; `call_tool("list_projects")` returns JSON; `call_tool("update_part_script", bad script)` returns error payload in content, exit clean. Chat: unit-test loop with a `FakeAnthropicClient` injecting a scripted tool-use conversation (no network): asserts tool executed via registry, events published, history recorded; `available=False` when no key → `run_turn` raises `ChatUnavailable`.
- [ ] Run → fail. Implement. Run → pass. Commit.

### Task 8: Example projects ×3  *(three parallel-safe subtasks: each touches only its `examples/<domain>/` + one test file)*

**Files:** `examples/rocketry/…`, `examples/construction/…`, `examples/prototyping/…`, `tests/test_examples.py` (shared, written once by the first to land — parametrized over example dirs found on disk).

**Common requirements:** Real engineering intent, `README.md` explaining the part + params; parts follow the script contract, are valid, non-trivial (revolves/lofts/patterns/fillets, not just boxes); assembly with ≥2 instances positioned sensibly; interference clean (< 0.001 mm³ overlap) at defaults; every param has min/max/unit/description; scripts build in <10 s each.
- **rocketry:** thrust chamber: `nozzle.py` (revolved bell contour: chamber Ø/length, throat Ø, expansion ratio, wall thickness — 80% bell approximated by two tangent arcs + parabola or conical frustum with blend fillets), `injector_plate.py` (plate + polar orifice pattern + center igniter boss), `flange.py` (bolt-circle flange). Assembly stacks them on Z. Materials: inconel718/stainless_316.
- **construction:** steel truss gusset node: `gusset_plate.py` (polygonal plate, bolt hole groups along 3 member axes at parametric angles), `angle_bracket.py`, `base_plate.py` (anchor slots). Material steel_a36.
- **prototyping:** snap-fit electronics enclosure: `enclosure_base.py` (shelled box, screw bosses, side vents pattern, PCB standoffs), `enclosure_lid.py` (lip fit, logo emboss optional param), material abs. Lid sits closed in assembly.
- `tests/test_examples.py`: for each example dir: open via service, rebuild every part at defaults (valid, volume>0), rebuild at min and max of every param (valid or *documented* clamp warning — no exceptions), interference check passes, STEP export of assembly succeeds.

**Steps per domain:** write parts → open in service via a short script or test run → iterate until valid → README → tests pass → commit.

### Task 9: CLI + macOS app wrapper  *(after 5; small)*

**Files:** Rewrite `agentcad/cli.py`; create `scripts/make_app.sh`; modify `Makefile` (`app` target already points there).

**Interfaces:** `agentcad serve [--port N] [--projects-dir P] [--no-open]` (uvicorn, opens browser unless suppressed; also registers `examples/` projects via `open_project` at startup); `agentcad open` = serve with browser; `agentcad mcp`; `agentcad new <name>`; `agentcad export <project> <part> --format step|stl|3mf -o OUT`. `make_app.sh`: builds `dist/AgentCAD.app` (Contents/Info.plist + `Contents/MacOS/AgentCAD` shell launcher `cd <repo>; exec uv run agentcad open`); `codesign --force --deep -s - dist/AgentCAD.app` ad-hoc so Gatekeeper allows local run.

**Steps:** implement → `uv run agentcad serve --no-open` smoke + `agentcad export` on an example → `make app` → `open dist/AgentCAD.app` launches browser UI → commit.

### Task 10: Documentation set  *(parallel-safe after 7/8: touches `README.md`, `docs/*.md` only)*

**Files:** `README.md`, `docs/architecture.md`, `docs/agent-api.md`, `docs/part-authoring.md`, `docs/user-guide.md`, `docs/roadmap.md`.

**Requirements:** Every command/path/tool name verified against the code as built (writers must read the code, not the spec, for truth). README: what/why (agentic-first thesis in 3 paragraphs), quickstart (`make setup && make run`), MCP registration for Claude Code, screenshot placeholder replaced in final verification, trust model note. agent-api.md: all tools with schemas + a worked transcript (create part → fix error → export). part-authoring.md: the contract, cheat-sheet, worked example, common OCCT failure modes (fillet too large, zero-thickness). user-guide.md: every UI surface. roadmap.md: spec §12 items with rationale. architecture.md: spec §4 diagram updated to as-built + data flow of one rebuild.

### Task 11: Adversarial review + fixes

Workflow (find→verify→fix) over dimensions: correctness (kernel/cache/concurrency), API/contract drift (docs vs code vs tools), security (bind address, path traversal in project open/export paths, script trust docs), UX gaps, test gaps. Confirmed findings fixed + committed.

### Task 12: Final verification

`make test` green (full suite, no skips); fresh `make setup` from clean checkout in temp dir works; server run; Chrome automation: open UI, load example, edit param, observe rebuild, screenshot into `docs/assets/`; MCP end-to-end from a real MCP client call; `make app` bundle launches. superpowers:verification-before-completion checklist before reporting done.

## Self-review (performed)

**Spec coverage:** §4 components → Tasks 2–7,9; §5 formats → Tasks 2–3; §6 routes → Task 5 (+`/api/tools` addition, recorded here as spec delta: generic tool passthrough powers the MCP proxy); §7 tools → Task 4; §8 errors → Tasks 2–5; §9 UI → Task 6; §10 → Tasks 5,10,11; §11 → distributed test steps; §12–13 → Tasks 10,12. Gap check: config/port (§6 preamble) → Task 1. **Placeholders:** none — behavioral contracts are exhaustive where literal code is impractical; implementers are Fable-class agents with spec+plan+repo access (deliberate deviation from literal-code-in-plan, recorded). **Type consistency:** `RebuildResult`, `Metrics`, error shape, ACM1, transform semantics identical across Tasks 2/4/5/6; tool count 17 consistent in Tasks 4/5/7.

**Execution decision (autonomous):** Tasks 1–5 inline in the orchestrating session (interdependent spine); Tasks 6/7/8/10 via parallel subagents (disjoint file ownership as marked); Tasks 11–12 via workflow + inline verification.
