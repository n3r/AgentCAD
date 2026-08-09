# AgentCAD v3 — Roadmap Build-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development to implement this plan slice-by-slice.
> Each slice is dispatched with its section below plus the relevant subsystem
> maps. Subagents do NOT run git and do NOT install into the shared venv.
> The orchestrator owns all commits; every commit stages a
> `docs/changelog/NNNN-<slug>.md` entry (next: 0036 — 0034/0035 were taken by the light-ui branch).

**Goal:** Build every remaining item in `docs/roadmap.md` (all sections below
"Shipped since v0.1") as additive vertical slices behind the v2 extension
points, without rewriting the cores.

**Architecture:** Every slice is a pack trio (kernel handler pack + core tool
pack + server route pack) plus, where authoring-facing, a toolkit module and a
CHEATSHEET section; UI slices extend the vanilla-ES-module frontend through the
existing `actions`/state-store wiring. Frozen surfaces stay frozen: the 17 v1
tools, existing routes, manifest fields, and the ACM1 byte format are extended,
never mutated. Manifest growth is additive (the store round-trips unknown
keys).

**Tech Stack:** Python 3.12 + uv, build123d 0.11.1 (pinned), FastAPI, numpy /
scipy (server-safe), scikit-fem/gmsh/meshio behind `[fem]`, Three.js 0.185.1
(vendored), CodeMirror 5 (vendored). New deps must be MIT/BSD/Apache or
subprocess-isolated (GPL firewall precedent: gmsh).

## Global Constraints

- Only `agentcad/kernel/` may import `OCP`/build123d. Server code stays clean.
- build123d pinned `>=0.11.1` via `uv.lock`; the suite is the compat harness.
- Rotations are intrinsic XYZ Euler degrees everywhere; no quaternions in one
  layer only.
- Tool counts in tests are floors (`>= 25`); never rename/remove pinned names.
- Every tool has non-empty description, object schema, dict properties, list
  required. Registry rejects unexpected args — extending a tool = editing its
  schema.
- Suite must stay green **without** `[fem]` (importorskip both halves).
- Mutating operations publish `project_changed` (or a more specific event) and
  return post-state. Structured errors, never bare failures.
- Atomic writes for every persisted file. Byte-determinism of ACM1 buffers is
  a hard test.
- `bool` is not a number anywhere (`isinstance(x, bool)` checked first).
- Every commit: `make test` green (cite count) + changelog entry staged +
  `Co-Authored-By: Claude <noreply@anthropic.com>` trailer.
- Docs to touch when the surface changes: `docs/agent-api.md` (tool tables +
  count prose), `docs/architecture.md` (count label + new seams),
  `docs/part-authoring.md` (script contract), `templates.py` CHEATSHEET,
  `docs/user-guide.md` (UI), README.

## Execution order and waves

| Wave | Slices | Rationale |
|------|--------|-----------|
| 1 | S1 non-numeric PARAMS · S2 per-solid semantics | authoring foundations; touch shared validation sites |
| 2 | S3 sheet metal · S4 PMI/GD&T · S5 stack-ups (after S4) · S6 motion · S7 surfacing+curvature · S8 FEM tiers | engineering depth; disjoint pack files |
| 3 | S9 turn-locking · S10 multi-agent (after S9) · S11 git history · S12 mesh LOD · S13 vision feedback · S14 direct-manipulation gaps · S15 GUI sketcher+push/pull | application layer |
| 4 | S16 sandbox-exec · S17 Win/Linux CI · S18 single binary | platform |
| 5 | S19 final docs/roadmap refresh + full verification | close-out |

---

### S1: Non-numeric PARAMS (bool / enum / string)

**Files:**
- Modify: `agentcad/kernel/worker.py` (`_validate_params_spec`,
  `_resolve_params`, `handle_inspect`)
- Modify: `agentcad/core/service.py:210-243` (`set_params` numeric check)
- Modify: `agentcad/core/project.py:106` (float coercion in `get_part`),
  `:206-213` (`update_part_entry` validation)
- Modify: `agentcad/core/model.py` (`ParamSpec` typing, `PartRecord.params`)
- Modify: `agentcad/core/tools.py:167-181` (set_params description only)
- Modify: `agentcad/core/templates.py` CHEATSHEET §1
- Modify: `agentcad/agent/chat.py` SYSTEM_PROMPT params sentence
- Modify: `frontend/js/inspector.js` `buildParamControls`/`syncParamValues`
- Modify: `tests/test_examples.py` (type-aware min/max/extremes branches)
- Test: `tests/test_kernel.py` + `tests/test_service.py` additions
- Docs: `docs/part-authoring.md`, `docs/agent-api.md` set_params row

**Interfaces (binding):** PARAMS entry gains optional `"type"`:
`"number"` (default) | `"int"` | `"bool"` | `"enum"` | `"string"`.
`enum` requires `"choices": [str|number, ...]` (default must be a member).
`bool` default must be a bool; `string` default a str (optional `max_len`,
default 200). min/max valid only for number/int. Overrides type-checked
against the spec (bool for bool, member for enum, str for string, number for
number/int with clamping preserved). `handle_inspect` returns `type` and
`choices` in the spec. Cache key needs no change (params JSON-serialized).
UI: checkbox for bool, `<select>` for enum, text input for string.

**Acceptance:** kernel tests for each type (spec validation, resolve, inspect,
build with a bool-toggled feature); service set_params round-trips non-numeric
values; example tests still sweep numeric extremes and skip min/max for
non-numeric types; suite green.

### S2: Per-solid part semantics

**Files:**
- Modify: `agentcad/kernel/worker.py` `_metrics` (per-solid array)
- Create: none (metrics is core-owned already) — worker change is additive
- Modify: `agentcad/core/service.py` (density map for solids in cache key +
  build params), `agentcad/core/project.py` (part entry `solid_materials`)
- Create: `agentcad/core/tools_solids.py` (`set_solid_materials` tool)
- Modify: `frontend/js/inspector.js` metricsTable (per-solid rows)
- Test: `tests/test_solids.py`
- Docs: part-authoring.md (optional `solid_labels`), agent-api.md

**Interfaces (binding):** `_metrics` gains `"solids": [{"volume_mm3",
"mass_g", "bbox", "center_of_mass"}]` (index-ordered, only when n_solids > 1;
aggregates unchanged — do not regress `test_nested_compound_volume_sums_solids`).
Script contract: optional `SOLID_LABELS = ["body", "lid", ...]` list (by solid
index) surfaced in metrics as `"label"`. Manifest part entry gains optional
`"solid_materials": {"<index-or-label>": material_id}`; service resolves each
solid's density (fallback part material), sends `densities: [float,...]` to
the worker, and hashes the density list into the cache key. `mass_g`
aggregate = sum of per-solid masses.

**Acceptance:** compound part with two solids of different materials reports
correct per-solid masses and correct aggregate; assembly roll-up uses the new
aggregate; suite green.

### S3: Sheet metal toolkit + flat pattern

**Files:**
- Create: `agentcad/toolkit/sheetmetal.py` (+ `__init__` lazy re-export)
- Create: `agentcad/kernel/handlers/sheetmetal.py` (`flat_pattern` handler)
- Create: `agentcad/core/tools_sheetmetal.py` (`flat_pattern` tool)
- Create: `tests/test_sheetmetal.py`
- Modify: `agentcad/core/templates.py` CHEATSHEET (new section)
- Docs: part-authoring.md toolkit table, agent-api.md new category

**Interfaces (binding):** Declarative spec — `SheetPart(thickness, k_factor
=0.44)`; `.base(profile_pts | (w,d))`; `.flange(edge: "left|right|front|back",
angle_deg, length, inner_radius)` (bend relief auto for partial-width flanges
later; v1 full-edge flanges). `.fold() -> Part` (folded solid: base plate +
cylindrical bend zone + flange leaf, fused); `.unfold() -> Part` (flat solid);
`.flat_outline() -> list[(x, y)]` plus `bend_lines() -> [{"axis_pts", "angle",
"radius"}]`. Bend allowance `BA = radians(angle) * (R + K*t)`; flat length of
a 90° flange = leg_a + leg_b + BA - 2*(R + t) verified against a hand-computed
value in tests. Script contract: build(p) uses the toolkit; optional
`flat_pattern(p) -> Part` in the script namespace is what the kernel handler
executes (via `build_shape_ns`), then projects top-view to SVG (reusing
drawing primitives) or DXF into exports/.

**Acceptance:** folded bracket is valid with correct volume (plate + flange +
bend zone analytic volume within 1%); unfold flat length matches analytic BA;
`flat_pattern` tool writes a parseable DXF and an SVG with bend lines; suite
green.

### S4: PMI / GD&T tolerance model + drawing callouts

**Files:**
- Create: `agentcad/core/pmi.py` (validation), `agentcad/core/tools_pmi.py`
  (`set_part_pmi`, `get_part_pmi` tools)
- Modify: `agentcad/kernel/handlers/drawing.py` (callout rendering layer)
- Modify: `agentcad/core/tools_drawing.py` (forward pmi into drawing call)
- Create: `tests/test_pmi.py`
- Docs: agent-api.md (new category), part-authoring.md note

**Interfaces (binding):** manifest part entry gains optional `"pmi"`:
```json
{"dims": [{"id": "d1", "kind": "linear|diameter", "nominal": null,
           "plus": 0.1, "minus": 0.1, "note": "hole pattern"}],
 "datums": [{"id": "A", "face": "bottom|top|left|right|front|back"}],
 "fcf": [{"id": "f1", "type": "flatness|position|perpendicularity|
          parallelism|cylindricity", "tol_mm": 0.05,
          "datums": ["A"], "note": "mounting face"}]}
```
Validation copies the materials explicit-field-tuple pattern (unknown keys
rejected, ValidationError with known/unknown lists). `nominal: null` means
"driven" — the drawing keeps its measured value and appends `±plus/minus`.
Drawing SVG renders: datum flags (boxed letter + leader on the named face's
silhouette edge), FCF boxes (type symbol, tolerance, datum refs) in a callout
column, and ± suffixes on the overall dims when `dims` entries exist. The
`detected` payload gains `"pmi_rendered": {"dims": n, "datums": n, "fcf": n}`.

**Acceptance:** set_part_pmi validates and persists; drawing SVG contains the
datum letter, FCF text and ± strings (structural asserts, not byte-golden);
get_part_pmi round-trips; suite green.

### S5: Tolerance stack-ups (worst-case + RSS)

**Files:**
- Create: `agentcad/core/tools_stackup.py` (`tolerance_stackup` tool)
- Create: `tests/test_stackup.py`
- Docs: agent-api.md row

**Interfaces (binding):** `tolerance_stackup(project, axis: "x|y|z",
from_instance, to_instance)` walks the resolved assembly (mates applied):
contributors = every instance on the mate-chain path between the two
instances; each contributes its PMI linear dims projected on the axis (or a
zero-tolerance note when it has none). Returns `{"axis", "nominal_mm"
(distance between instance origins), "worst_case": {"plus", "minus"},
"rss": {"plus", "minus"}, "contributors": [{"instance", "part", "dims":
[...], "plus", "minus"}], "warnings"}`. Pure server-side math; mate chain
from `InstanceSpec.mate` links (root = anchor).

**Acceptance:** two-plate stack with ±0.1 each reports worst case ±0.2 and
RSS ±0.141; unrelated instances rejected with ValidationError; suite green.

### S6: Motion from mates (drive DOFs + sweep interference)

**Files:**
- Create: `agentcad/kernel/handlers/motion.py` (`motion_sweep` handler)
- Create: `agentcad/core/tools_motion.py` (`sweep_motion` tool)
- Create: `tests/test_motion.py`
- Modify: `frontend/js/placement.js` (DOF drive slider for mated instances) +
  `frontend/js/main.js` (WS case) — animation via direct group mutation
- Docs: agent-api.md row, user-guide note

**Interfaces (binding):** `sweep_motion(project, instance, angle_range?:
[start, end] | offset_range?: [start, end], samples: int=12, min_volume=0.001)`
— instance must carry a revolute (angle) or cylindrical (angle or offset)
mate. Kernel `motion_sweep` params: `{items, driven: {instance, param:
"angle"|"position", values: [..]}, min_volume}` — one kernel round-trip;
resolves mates per sample (reusing `_mates_resolver.resolve_mates`) and runs
the pairwise `&` interference (mesh-kind items skipped, reported once).
Returns `{"samples": [{"value", "pairs": [...]}, ...], "clear": bool,
"first_collision": value|null, "skipped_mesh": [...]}` plus
`"frames": [{instance_id: {position, rotation_deg}}]` for UI animation.

**Acceptance:** hinged flap over a base collides only beyond the analytic
contact angle; frames move monotonically; STL exclusion preserved; suite
green.

### S7: Class-A surfacing toolkit + curvature analysis

**Files:**
- Create: `agentcad/toolkit/surfacing.py` (+ lazy re-export)
- Modify: `agentcad/kernel/handlers/analysis.py` (new kind `curvature`)
- Modify: `agentcad/core/tools_analysis.py` (kind enum text)
- Modify: `frontend/js/inspector.js` (curvature analysis row)
- Create: `tests/test_surfacing.py`
- Docs: CHEATSHEET section, agent-api analyze_part row

**Interfaces (binding):** toolkit: `smooth_loft(profiles: list[Sketch|Face],
*, ruled=False) -> Part` (loft with OCCT continuity defaults),
`network_surface(u_curves, v_curves) -> Face` (GeomPlate/BRepOffsetAPI through
build123d where possible, raw OCP inside the function otherwise), returning
the safe_* tuple contract where fallible. Analysis kind `curvature`: samples
each B-rep face on a UV grid (BRepAdaptor_Surface + BRepLProp_SLProps),
returns `{"faces": [{"index", "area_mm2", "gaussian": {"min","max","mean"},
"mean_curvature": {...}}], "worst_gaussian_mm2", "sampled_points"}`.
Mesh-kind references rejected with contract_error.

**Acceptance:** cylinder face reports gaussian ≈ 0 and mean ≈ 1/(2r) within
2%; sphere gaussian ≈ 1/r²; loft produces a valid solid; suite green.

### S8: Higher-fidelity FEM tiers — modal + thermal

**Files:**
- Modify: `agentcad/kernel/_fem_impl.py` (mesh reuse + two new solvers)
- Modify: `agentcad/kernel/handlers/fem.py` (`fem_modal`, `fem_thermal`)
- Modify: `agentcad/core/tools_analysis.py` (register both when available)
- Modify: `agentcad/server/routes_analysis.py` (501 fallbacks)
- Test: `tests/test_analysis.py` additions (importorskip triple)
- Docs: agent-api FEM section, roadmap CalculiX note stays

**Interfaces (binding):** `fem_modal(project, part_id, n_modes=6, fixed_face?)`
→ `{"frequencies_hz": [...], "n_dof", "mesh": {...}}` (P2 tets, K/M
generalized eigenproblem via scipy.sparse.linalg.eigsh, shift-invert; free-free
when no fixed_face — report rigid modes ≈ 0 filtered with a note).
`fem_thermal(project, part_id, hot_face, cold_face, t_hot_c, t_cold_c,
k_w_m_k?)` → `{"t_min_c", "t_max_c", "flux_w", "mesh"}` (steady conduction,
Dirichlet BCs; conductivity default from the part material's `k_w_m_k`, error
if absent and not supplied). Same gmsh-subprocess meshing path as fem_static.

**Acceptance:** cantilever first natural frequency within 5% of the analytic
Euler-Bernoulli value; bar conduction flux within 2% of kAΔT/L; both tools
absent without `[fem]` and the suite green either way.

### S9: Multi-user turn-locking

**Files:**
- Create: `agentcad/core/locks.py` (`TurnLock` manager + contextvar identity)
- Create: `agentcad/core/tools_locks.py` (`acquire_turn`, `release_turn`,
  `get_turn` tools)
- Modify: `agentcad/core/service.py` — mutating methods call
  `self.turnlock.check(proj)` (seam installed by the pack, default no-op)
- Modify: `agentcad/server/app.py` middleware sets client id contextvar from
  `X-Agent-Id` header (default "anon-<ip-port-ish>"); chat sets "chat";
  MCP proxy forwards `AGENTCAD_AGENT_ID` env as the header
- Modify: `frontend/js/main.js` lock indicator + `lock_changed` WS case
- Create: `tests/test_locks.py`
- Docs: agent-api category, user-guide, architecture seam list

**Interfaces (binding):** `acquire_turn(project, ttl_s=120)` → `{"holder",
"expires_at"}` (refresh if already holder; ConflictError with holder+expiry if
held by another and unexpired); `release_turn(project)`; `get_turn(project)`.
Enforcement: when a lock is held by someone else and unexpired, mutating
service methods raise ConflictError("project is locked by <holder>").
No lock held → writes allowed (backward compatible). Events:
`{"type": "lock_changed", "project", "holder": str|null}`. Identity via
`contextvars.ContextVar("agentcad_client_id", default="local")`.

**Acceptance:** two registries over one service: A acquires, B's set_params
gets conflict_error payload, B acquires after A releases/expires; existing
tests unaffected (no locks held); suite green.

### S10: Multi-agent sessions

**Files:**
- Modify: `agentcad/agent/chat.py` (history/locks keyed `(project, session)`,
  events carry `session`; default session "main" keeps API compat)
- Modify: `agentcad/server/app.py` chat routes accept optional `session`
- Modify: `frontend/js/chat.js` (filter events on session "main")
- Test: `tests/test_chat.py` additions
- Docs: agent-api/user-guide multi-agent section

**Interfaces (binding):** `POST /api/chat {project, message, session?}`;
chat_* events gain `"session"`. Two sessions on one project interleave turns
gated by the S9 turn lock (chat acquires the turn for its mutating tool calls
as client id `chat:<session>`; releases at turn end). History pairing
invariants preserved per session.

**Acceptance:** two concurrent scripted sessions keep independent histories,
events tagged, pairing invariant holds; suite green.

### S11: Git-backed undo/redo & history

**Files:**
- Create: `agentcad/core/history.py` (per-project git via subprocess;
  graceful no-git fallback), `agentcad/core/tools_history.py`
  (`project_history`, `project_restore` tools)
- Modify: `agentcad/core/service.py` — after successful mutations, call
  `self.history.snapshot(proj, message)` (seam, default no-op)
- Create: `agentcad/server/routes_history.py` (GET history, POST restore)
- Modify: `frontend/js/main.js` toolbar Undo/Redo + Cmd+Z
- Create: `tests/test_history.py`
- Docs: user-guide + agent-api

**Interfaces (binding):** repo at `<project>/.history/` (`GIT_DIR` outside the
worktree pattern: `git --git-dir=<proj>/.history --work-tree=<proj>`), with
`.cache/`, `exports/`, `.history/` excluded via that repo's info/exclude.
`snapshot(proj, message)` commits manifest/parts/imports when dirty (author
"AgentCAD <agentcad@local>"). `project_history(project, limit=20)` →
`[{"id", "message", "ts"}]`; `project_restore(project, commit)` checks the
tree out into the working dir and snapshots "restore <id>" (history stays
linear; undo = restore parent, redo = restore the pre-undo commit).
Service caches self-heal (cache keys re-derived from restored content);
`project_changed` published. If `git` is not on PATH, history tools report
`{"error": {"type": "validation_error", "message": "git not available"}}` and
snapshots no-op.

**Acceptance:** script edit → snapshot → param change → snapshot → restore
first commit brings the old script back and rebuild reflects it; suite green
with and without git on PATH (mock).

### S12: Mesh streaming — LOD + chunked delivery

**Files:**
- Modify: `agentcad/kernel/worker.py` handle_build (optional `lod_tolerances`)
  and `agentcad/kernel/handlers/reference.py` (same for imports)
- Modify: `agentcad/core/service.py` (LOD paths in mesh_info; thresholds)
- Modify: `agentcad/server/app.py` mesh route: `?lod=` param (404-safe
  fallback to full) — full-resolution route byte-identical to today
- Modify: `frontend/js/api.js` + `frontend/js/main.js`/`viewport.js`:
  progressive load (coarse LOD first paint for large meshes, then full)
- Test: `tests/test_mesh_lod.py`
- Docs: architecture ACM section

**Interfaces (binding):** ACM1 format untouched. When a build's full mesh
exceeds `LOD_TRIANGLE_THRESHOLD = 150_000` triangles, the worker also writes
`<key>.lod1.acm` (tolerance ×8). `mesh_info(proj, part, lod=None)` returns
the requested tier when present. `GET .../mesh?lod=1` serves it with
`X-Mesh-Lod: 1`; absent tier → full buffer with `X-Mesh-Lod: 0`. Frontend
requests lod=1 first when `mesh_summary.triangles > threshold` (or on
`content-length` heuristic), swaps to full when it arrives. Determinism tests
untouched (full buffer path identical).

**Acceptance:** a >threshold part produces both files, lod buffer parses as
ACM1 with fewer triangles, byte-determinism holds per tier; small parts write
no LOD files and the route falls back; suite green.

### S13: Vision feedback — render_view tool

**Files:**
- Create: `agentcad/core/render.py` (numpy software rasterizer over ACM
  buffers → PNG via zlib, no new deps)
- Create: `agentcad/core/tools_vision.py` (`render_view` tool)
- Create: `agentcad/server/routes_vision.py` (GET rendered PNG)
- Modify: `agentcad/agent/mcp_server.py` (result with `"png_base64"` →
  ImageContent + text), `agentcad/agent/chat.py` (image block in tool_result)
- Create: `tests/test_render.py`; modify `tests/test_mcp.py` content handling
- Docs: agent-api Agents section

**Interfaces (binding):** `render_view(project, part_id?, view="iso|front|
top|right", width=800, height=600)` — part_id omitted renders the assembly
(instances placed with intrinsic-XYZ transforms). Orthographic camera fitted
to bbox, Z-up, Lambert shading with two lights, z-buffered triangle
rasterization in numpy, background transparent-dark. Returns `{"path"
(exports/renders/<name>.png), "width", "height", "png_base64"}`. MCP wraps
`png_base64` as ImageContent (dropping it from the JSON text); chat appends
`{"type": "image", "source": {"type": "base64", ...}}` to the tool_result
content list.

**Acceptance:** box render: PNG magic, nonzero pixel variance, silhouette
wider in iso than front for a flat plate; assembly render places two
instances apart; MCP round-trip returns an image content block; suite green.

### S14: Direct-manipulation UI — close the v2 gaps

**Files:**
- Audit: `frontend/**` against the roadmap list (gizmo, transform panel,
  material picker, Import button, drawing preview, analysis buttons)
- Fix (known): wire Shift-to-snap (`viewport.setGizmoSnap` on shiftKey;
  pass `opts.snap` from main.js) — placement.js:119 advertises it unwired
- Fix: anything else the audit finds; delete the stale user-guide blockquote
  (user-guide.md:273-281 "not in this build")
- Verify: real browser via the `run` skill; screenshots into docs/assets

**Acceptance:** every roadmap-listed control demonstrably works in the
browser (screenshots), zero console errors, user-guide matches reality.

### S15: GUI sketching & push/pull (MVP)

**Files:**
- Create: `frontend/js/sketcher.js` (2D sketch editor overlay: points/lines/
  circles, constraint palette, live solve via POST /api/sketch/solve, emits a
  build123d snippet into the editor)
- Modify: `frontend/js/main.js` (Sketch mode toggle), `frontend/index.html`,
  `frontend/css/app.css`
- Create: `agentcad/toolkit/facemod.py` — `push_face(part, face_index,
  distance) -> Part` (offset-extrude the picked planar face, fuse/cut)
- Create: `agentcad/kernel/handlers/facemod.py` (`face_info` handler: face
  index → normal/area/planarity for pick feedback; validates pushability)
- Create: `agentcad/core/tools_facemod.py` (`push_pull` tool: appends the
  `push_face` call to the script's build return — script stays the source of
  truth) + route
- Modify: `frontend/js/viewport.js` pick() returns faceIndex via a
  `<key>.faces.u32` sidecar (triangle→B-rep-face map written at tessellation;
  additive file, ACM1 untouched); Alt-drag on a face in part mode → push_pull
- Create: `tests/test_facemod.py`; fix `docs/part-authoring.md:139` sketch
  constraint-key doc bug
- Docs: user-guide sketcher section

**Interfaces (binding):** sketcher generates
`with BuildSketch() as s: Polyline(...); make_face()` style snippets from
solved coordinates (the JSON spec is exactly `solve_sketch`'s). push_pull
tool: `push_pull(project, part_id, face_index, distance_mm)` — rewrites the
script by wrapping the return expression: `return push_face(<expr>,
face_index, distance)` with the toolkit import added; rebuild returns the
standard result. Tessellation writes `<key>.faces.u32` (one u32 per triangle
= B-rep face index) alongside every `.acm`; mesh route gains
`.../mesh/faces`; pick() raycasts, maps triangle→face.

**Acceptance:** solve round-trip drawn-rectangle → constraints → snippet
compiles and builds; push_pull on a box face grows volume by area×distance
within 1%; browser-verified sketch → part flow with screenshots; suite green.

### S16: macOS sandbox-exec worker confinement

**Files:**
- Create: `agentcad/kernel/sandbox.py` (profile builder + argv wrapper)
- Modify: `agentcad/kernel/client.py` spawn (wrap argv when enabled),
  `agentcad/kernel/pool.py` (pass-through), `agentcad/cli.py`
  (`_build_service` passes writable roots), `agentcad/config.py` knob
- Create: `tests/test_sandbox.py` (darwin-only)
- Docs: architecture Trust model section update

**Interfaces (binding):** darwin + `sandbox-exec` present + not
`AGENTCAD_NO_SANDBOX=1` → worker argv becomes `["sandbox-exec", "-p",
profile, python, "-u", "-m", "agentcad.kernel.worker"]`. Profile: `(version 1)
(allow default)` is NOT used — profile is `(deny default)` with allows:
process-exec/fork, read everywhere (site-packages, /System, etc.), write only
to the projects roots (service projects dir + registered example dirs +
tempdir + `~/.agentcad`), mach/sysctl basics; `(deny network*)`. Sandbox
state surfaced in `/api/health` as `"sandbox": "active"|"off"|"unsupported"`.
Respawn path unchanged (wrapper is part of argv).

**Acceptance:** on macOS: a part script writing to `$HOME/pwned.txt` fails
under sandbox while normal builds/exports succeed; timeout-kill-respawn tests
still pass; suite green (non-darwin: module no-ops, tests skip).

### S17: Windows/Linux portability + CI

**Files:**
- Create: `.github/workflows/ci.yml` (matrix: ubuntu-latest, macos-latest,
  windows-latest; astral-sh/setup-uv; `uv sync`; `uv run pytest -q`;
  uv cache enabled; 40 min timeout)
- Audit/fix: POSIX-only assumptions (`os.replace` ok; check signal use,
  path handling, `start_new_session` in mcp_server on Windows, portctl
  fallback in test_mcp)
- Docs: README platform note, roadmap

**Acceptance:** suite green locally; ci.yml lints (actionlint if available);
platform-conditional code paths covered by unit tests where feasible
(sandbox gating, spawn flags). Honest note: CI proof completes when pushed to
GitHub; the workflow is written to be correct on all three OSes.

### S18: Single-binary distribution

**Files:**
- Create: `packaging/pyinstaller/agentcad.spec` (+ hooks listing hidden
  imports: all pack modules, OCP/vtk binaries collection, frontend +
  examples data files)
- Create: `agentcad/cli.py` `worker` subcommand (frozen worker re-exec:
  `KernelClient` spawns `[sys.executable, "worker"]` when frozen)
- Modify: `agentcad/agent/mcp_server.py` `_ensure_server` frozen-aware
- Create: `make dist` target + `scripts/build_binary.sh`
- Create: `tests/test_frozen_helpers.py` (arg-building logic unit tests)
- Docs: README install story

**Acceptance:** local `make dist` produces a runnable bundle whose
`/api/health` responds and which serves the UI (smoke script); helpers
unit-tested; known size documented (~2 GB wheels reality noted honestly).

### S19: Final sweep

- Rewrite `docs/roadmap.md`: move shipped items into "Shipped", keep honest
  deferrals (CalculiX tier, full kinematic solver, class-A *modeling UX*,
  Tauri shell) with rationale.
- Reconcile tool counts everywhere (agent-api.md prose, architecture.md
  label); update README feature list; refresh user-guide.
- `make test` full run (cite count); browser verification pass; final
  changelog entry.

## Self-review

- Every roadmap section maps to ≥1 slice: Modeling (S1, S2, S3, S7, S15),
  PMI (S4, S5), Kinematics (S6), Analysis (S8), Platform (S16, S17, S18),
  Application (S11, S12, S14, S15, S9), Agents (S13, S10). ✓
- Frozen-surface rule respected: ACM1 untouched (S12/S15 add sidecar files),
  tool renames none, manifest growth additive (S2 solid_materials, S4 pmi). ✓
- Type consistency: `solve_sketch` spec reused verbatim by S15; metrics
  extension additive (S2); interference return shape reused by S6. ✓
- Suite-stays-green-without-extras: S8 gated twice (register + importorskip);
  S16 darwin-gated; S11 git-absent fallback. ✓
