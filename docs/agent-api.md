# Agent API Reference

Agents drive AgentCAD through a single tool surface — 70 tools (73 with the
`[fem]` extra), assembled once in `agentcad/core/tools.py` (the 17 core
tools) plus the v2/v3/v4 feature packs in `agentcad/core/tools_*.py` — and
exposed two ways:

1. **MCP** (any MCP client, e.g. Claude Code): a stdio server that proxies
   the running HTTP API — and auto-starts the server if it isn't running.

   ```bash
   claude mcp add agentcad -- uv --directory /path/to/cad_claude run agentcad mcp
   ```

2. **Built-in chat** (the UI's Agent panel): a server-side Anthropic
   tool-use loop over the same registry. Set `ANTHROPIC_API_KEY` before
   `agentcad serve` to enable it.

Raw HTTP works too: `GET /api/tools` lists the registry;
`POST /api/tools/{name}` calls a tool with a JSON body of arguments.

## Conventions

- Expected failures return an `{"error": {"type", "message", "details"}}`
  **payload**, not a protocol error — read it and react. Script failures
  carry `details.traceback` and `details.line`.
- Mutating tools return the post-state you need next (metrics, warnings,
  status), so a create → inspect → fix loop converges in few turns.
- Rebuild results have `{"ok": true, "metrics", "warnings", "specs"}` or
  `{"ok": false, "error", "hint"}`. On failure the previous good geometry is
  kept. `specs` is the design-spec verdict for that part (below) — `null` when
  the part declares none, and absent entirely on a failed build.
- Units: mm, grams, degrees. Instance rotations are intrinsic XYZ Euler.
- One error type is **returned rather than raised**: `merge_conflict`
  (`merge_branch` / `resolve_merge`). It arrives as an ordinary
  `{"error": {"type": "merge_conflict", "details": {"conflicts": […]}}}`
  payload — over REST at HTTP **200**, not 409 — because a conflict is a
  workflow state to render, not a failure. Everything else keeps the usual
  `validation_error` / `notfound_error` / `conflict_error` mapping.
- A review thread's anchor resolves into **four** statuses, and they are not
  interchangeable: `ok` (it still points at what it pointed at), `moved`
  (re-matched at a **new** address, which the block carries), `orphaned` (the
  target is gone or no candidate cleared the tolerance — the contract, not a
  bug) and `unverified` (*we did not look*: the part is unbuilt, git is
  absent, the packet is frozen, the anchor belongs to another branch).
  `unverified` is never a synonym for "fine", and **`other_branch` wins over
  `orphaned` at every level** — a parameter, a line range or a face missing
  from a part that exists on both branches reads `unverified`/`other_branch`,
  because "it was removed" would be a claim about a branch the thread was
  never about. See [Review threads](#review-threads).

## Tools

Required arguments are **bold**; the rest are optional. Discover the live set
and exact JSON Schemas at runtime with `GET /api/tools` — that is the source
of truth, and it omits the FEM tools unless the `[fem]` extra is installed.

### Projects and parts

| Tool | Arguments | Returns |
|---|---|---|
| `part_template` | — | The part-script contract, a starter template, and a build123d cheat-sheet. Call this before writing your first script. |
| `list_projects` | — | `{projects: [{name, path, n_parts}]}` |
| `create_project` | **name** | Project detail. Names match `[a-z][a-z0-9_]{0,39}`. |
| `open_project` | **path** | Opens an existing project directory (e.g. a bundled example) by absolute path. |
| `get_project` | **project** | Manifest: parts (with build state), assembly instances, and a `materials` map (`id → {label, density_g_cm3}`). |
| `create_part` | **project, part_id**, label, script, material | Part detail with metrics (default template if no `script`; `material` defaults to `al6061`). |
| `get_part` | **project, part_id** | Script, `params_spec`, current params, status (state/error/warnings), metrics, `specs` (the part's design-spec verdict, from cache — `null` when it declares none, and absent when the part does not build), plus `kind` (`script`\|`reference`) and `source`. For reference parts `script`/`params_spec` are `null` and `source` is the imported file. |
| `update_part_script` | **project, part_id**, script, label, material | Rebuild result. On failure: traceback + failing line + hint; previous geometry kept. |
| `set_params` | **project, part_id, values** | Rebuild result. Values (numbers, booleans, enum choices, or strings, per each param's `type` in `params_spec`) merge with existing overrides; numeric values clamp to min/max with warnings, while a wrong-typed value or non-member enum choice is rejected. Unknown names are rejected before anything is written, and a `null` value removes an override. |
| `delete_part` | **project, part_id** | `{deleted}` — fails with a conflict while assembly instances reference the part. |

### Turn locking and chat sessions

| Tool | Arguments | Returns |
|---|---|---|
| `acquire_turn` | **project**, ttl_s | Take (or refresh) the per-project editing turn: `{holder, expires_at, you}`. While held, writes by every other client fail with `conflict_error` naming the holder. TTL default 120 s, clamped 5–3600; re-acquire to refresh. Identity = `X-Agent-Id` header (the MCP proxy sends `AGENTCAD_AGENT_ID` or `mcp`; built-in chat is `chat`; no header = `browser`). |
| `release_turn` | **project** | Release your turn: `{released}` (`false` when nothing was held). Releasing a turn held by someone else is a `conflict_error`. |
| `get_turn` | **project** | `{lock: {holder, expires_at} \| null, you}` — who holds the turn plus your own client identity. |

**Chat sessions.** Chat history and turn serialization are keyed by
`(project, session)`. `session` is an id matching `[a-z0-9_-]{1,32}`, default
`"main"` (the browser dock's lane). `POST /api/chat` accepts an optional
`"session"`; `GET`/`DELETE /api/chat/history` accept `?session=`; bad ids are
422. All `chat_*` WebSocket events and history payloads carry `"session"`.
Turns in the same session queue; turns in different sessions run concurrently
— cross-session write consistency is the per-project turn lock's job. A
session's tool calls run under client identity `chat:<session>` (`chat` for
`"main"`), so `get_turn` names the lane holding a lock.

### Metrics, mesh, and export

| Tool | Arguments | Returns |
|---|---|---|
| `get_metrics` | **project, part_id** | `{volume_mm3, mass_g, area_mm2, bbox, center_of_mass, is_valid, n_faces, n_edges, n_solids, solids?}` — `solids` (multi-solid parts only) is an index-ordered `[{label, volume_mm3, mass_g, bbox, center_of_mass}]`. |
| `get_mesh_summary` | **project, part_id** | `{vertices, triangles, edges, bbox}` — statistics only, no binary buffer. |
| `export_part` | **project, part_id, format**, tolerance | Writes `exports/<part_id>.<format>`; formats `step`, `stl`, `3mf`. |

### Assembly and mates

| Tool | Arguments | Returns |
|---|---|---|
| `get_assembly` | **project** | Instances with per-instance mass/state plus rolled-up `total_mass_g` and world bbox. Mate-driven instances are returned with their **resolved** concrete `position`/`rotation_deg`. |
| `set_assembly` | **project, instances** | Full replacement list: `{id, part, position [x,y,z], rotation_deg [rx,ry,rz], color?, mate?}`. |
| `check_interference` | **project**, min_volume | Boolean-intersects every instance pair; `{pairs: [{a, b, volume_mm3}], checked, skipped_mesh?}` above the threshold. STL references are `skipped_mesh` (booleans on a mesh segfault OCCT). |
| `export_assembly` | **project, format** | Whole placed assembly as `step` or `stl`. |
| `set_mate` | **project, instance, connector, to_instance, to_connector**, angle_deg, offset_mm | Constrain `instance` to `to_instance` via named connectors declared by a part's `connectors(p, part)`. The moving-side `connector` must be *rigid*; the anchor `to_connector` may be rigid/revolute/cylindrical, with `angle_deg`/`offset_mm` driving its DOF. Returns the updated assembly. |
| `clear_mate` | **project, instance** | Removes the instance's mate; it reverts to its explicit position/rotation. Returns the updated assembly. |
| `sweep_motion` | **project, instance**, angle_range, offset_range, samples, min_volume | Sweep a mated instance's driven DOF across `[start, end]` (exactly one of `angle_range` deg / `offset_range` mm; `samples` 2–60, default 12), re-resolving mates and boolean-checking every instance pair at each sample. Returns `{samples: [{value, pairs}], frames, clear, first_collision, skipped_mesh}` plus an `{instance, param, values}` echo — `frames[i]` maps every instance id to its resolved `{position, rotation_deg}` for animation; `first_collision` is the first swept value that overlaps (null when `clear`). STL references are skipped like `check_interference`. |
| `tolerance_stackup` | **project, axis, from_instance, to_instance** | 1-D tolerance stack-up along a world axis (`x`\|`y`\|`z`) over the unique mate-forest path between the two instances (endpoints included; `from == to` analyzes that one instance's own dims). Each path instance contributes its part's linear PMI dims matching the axis (x=`width`, y=`depth`, z=`height`, via `set_part_pmi`). Returns `{axis, target, nominal_mm, worst_case: {plus, minus}, rss: {plus, minus}, contributors: [{instance, part, dims, plus, minus}], path, warnings}` — worst case is the linear sum, RSS the root-sum-of-squares per dim, `nominal_mm` the resolved axis distance; instances not connected by mates are a validation error. |

### Materials

| Tool | Arguments | Returns |
|---|---|---|
| `list_materials` | project | Resolved catalog (builtin < `~/.agentcad/materials.json` < project overrides): `{materials: [{id, label, category, density_g_cm3, source, E_gpa?, yield_mpa?, ultimate_mpa?, elongation_pct?, cte_um_m_k?, k_w_m_k?, max_service_temp_c?, cost_usd_kg?, notes?}], caveat, global_error}`. Values are typical datasheet figures, **not design allowables**. |
| `set_project_materials` | **project, materials** | Replaces the project's `materials` section (a map `id → {density_g_cm3 required, …, category, notes}`); validates every entry, then returns the resolved list. |
| `set_solid_materials` | **project, part_id, materials** | Assign a material per solid of a multi-solid *script* part. Read `get_metrics(...).solids[].label` first (labels come from the script's `SOLID_LABELS`, else `solid_0`, `solid_1`, …). `materials` maps a solid label or index string to a material id; unmatched keys build with a warning; `{}` clears. Per-solid and aggregate mass then use those densities (unmapped solids keep the part material). Returns the rebuild result plus `solid_materials`. |

### Import (reference parts)

| Tool | Arguments | Returns |
|---|---|---|
| `import_cad_file` | **project, source, part_id**, label, material | Imports `.step`/`.stp`/`.brep`/`.stl` as a *reference* part (no script; placeable in assemblies, and — STEP/BREP only — usable in booleans). `source` is an absolute path to ingest, or the basename of a file already uploaded via `POST /api/projects/{proj}/imports`. Returns `{part: <detail>, imported: {source, n_solids, is_valid, mesh_only, warnings}}`. |

### Drawings and analysis

| Tool | Arguments | Returns |
|---|---|---|
| `generate_drawing` | **project, part_id**, views, format | Projected front/top/right/iso views with overall dimensions and hole callouts detected from the geometry. `views` is a subset of `[top, front, right, iso]` (default all); `format` is `svg` (default) or `dxf`. Writes `exports/<part_id>_drawing.<ext>` and returns `{path, size_bytes, detected: {diameters_mm, hole_groups, label}}`. When the part has PMI (`set_part_pmi`), the SVG gains tolerance suffixes on the overall/diameter dimensions, boxed datum flags, and feature control frames; `detected` then also carries `pmi_rendered: {dims, datums, fcf}` and `pmi_warnings`. DXF output ignores PMI (v1). Script parts only. |
| `flat_pattern` | **project, part_id**, format | Sheet-metal flat pattern: the unfolded blank's outline plus dashed bend lines with angle/radius callouts. Requires the script to define `flat_pattern(p)` returning a flat part or `(part, bend_lines)` — `SheetPart` from `agentcad.toolkit.sheetmetal` provides both. `format` is `svg` (default) or `dxf` (layers `OUTLINE`/`BEND`). Writes `exports/<part_id>_flat.<ext>` and returns `{path, size_bytes, flat_bbox_mm: {w, h}, n_bend_lines}`. Script parts only. |
| `set_part_pmi` | **project, part_id, pmi** | Replaces the part's PMI / GD&T section (`{}` clears it): `dims` (`{id, kind: linear\|diameter, target: width\|height\|depth or nominal hole ⌀ mm, plus, minus, note?}`), `datums` (`{id: "A".."Z", face: top\|bottom\|left\|right\|front\|back}`), `fcf` (`{id, type: flatness\|position\|perpendicularity\|parallelism\|cylindricity, tol_mm, datums: [letters], note?}`). Validated before writing; works for script and reference parts. Returns `{part_id, pmi}`. |
| `get_part_pmi` | **project, part_id** | The part's stored PMI section, with empty `dims`/`datums`/`fcf` when unset. |
| `face_info` | **project, part_id, face_index** | Inspect one B-rep face by its mesh-order index (the same ordinal the viewport's face picking and the `mesh/faces` sidecar use): `{planar, normal, area_mm2, center, n_faces}`. |
| `push_pull` | **project, part_id, face_index, distance_mm** | Direct-manipulation face offset recorded as code: validates the face is planar, then APPENDS an auto-generated wrapper to the script (`push_face(build(p), i, d)` — visible, editable, composable) and rebuilds. Positive distance grows the solid along the outward normal; negative cuts inward. The script stays the source of truth. |
| `project_history` | **project**, limit, ref | List the project's automatic history snapshots, newest first (`{id, message, ts, author}` — `author` is the client id that made it, read from the commit's `Client:` trailer, and `null` for a snapshot taken before authorship was recorded); entry [0] is the current state. History is **per branch**: you see your own branch's unless you pass `ref` — a branch or tag name — which reads that ref's history without switching you. `available: false` + empty list when git is missing on the server. |
| `project_restore` | **project, commit** | Restore the project to a snapshot id **or a branch/tag name** (`{commit: "shop-rev-a"}` restores a version) and append a linear "restore" commit on your current branch. Returns refreshed history + `{restored}`; validation_error on unknown commit/no git, conflict_error under someone else's turn lock. A manual restore is itself one undoable step. |
| `undo` | **project**, scope | Undo the last mutation by stepping back through the git history: `{undone, history: {available, undo, redo, mine}}`. `scope` is `any` (**default** — one shared stack, so you may take back another client's edit, which is the point of Cmd+Z next to a working agent) or `mine` (skip other clients' entries and take back your own most recent one). A `mine` undo of an entry that is no longer the branch head is a **`git revert` of exactly that commit**, so nobody else's later work moves; a later change that overlaps it is a `conflict_error` with `details: {commit, reason: "overlapping_changes", paths, blocked_by}` — never a merge, never a partial apply. Every other refusal has the same shape: `uncommitted_changes`, `already_reverted`, and `merge_in_range` when the range an undo would invert contains a merge commit. conflict_error when there is nothing to undo; after a server restart one step remains available. |
| `redo` | **project**, scope | Redo the most recently undone mutation. The redo stack clears when any new mutation happens. A step that was undone by a revert is redone by reverting that revert. |
| `get_history` | **project** | Undoable/redoable action labels, newest first, plus `available` (false when git is missing) and `mine: {undo, redo}` — how many entries on each stack are yours. The full durable snapshot log with commit ids is `project_history`. |
| `render_view` | **project**, part_id, view, width, height | Server-side shaded orthographic render of built geometry so the agent can *see* the shape. `part_id` renders one part; omit it to render the whole placed assembly (instance transforms and colors honored; unbuildable instances are listed in `skipped`). `view` is `iso` (default), `front`, `top` or `right`; `width`/`height` are 64..2048 px (default 800×600). Writes `exports/renders/<part|assembly>_<view>.png` and returns `{path, width, height, view, png_base64}`; over MCP and in chat the PNG arrives as actual image content. |
| `analyze_part` | **project, part_id, kind**, plane, axis, min_required | `kind=section` (cross-section area on `plane` XY\|XZ\|YZ), `wall` (min wall thickness; with `min_required` it adds an `ok` flag), `inertia` (mass-properties tensor + centre of mass), `projected_area` (silhouette area along `axis` X\|Y\|Z), `curvature` (per-face gaussian K in 1/mm² and mean H in 1/mm sampled on an 8×8 UV grid: `faces[]` with min/max/mean per face, `worst_gaussian_abs`, `n_faces`, `sampled_points`; H's sign is orientation-dependent — compare magnitudes; a true G2 blend shows no jump in K/H across the seam). Script parts only. |

### Design specs

Design intent as executable assertions over built geometry: a module-level
`SPECS` list in a part script (part scope) and in a root `specs.py` (project
scope), built from `agentcad.toolkit.specs`'s ten `check_*` constructors. Specs
are **code in the tree**, so branching, diffing, merging, restore and undo come
from PRD-001 for free — there is no spec database. Authoring is documented in
[part-authoring.md](part-authoring.md#design-specs-specs); this section is the
tool surface.

Five rules govern every payload here:

- **A failing spec is data, never an error.** It never fails a rebuild: the
  geometry lands, `ok` stays `true`, and the failure is signal. Nothing in this
  section raises for a red check.
- **Four statuses, and they are not interchangeable.** `pass`/`fail` were
  *measured* and carry `measured`, `limit` (a dict, e.g. `{"min_mm": 2.5}`),
  `unit` and a message; `skip` is a named structural inability to measure and
  **always** carries a `reason` (`fem_extra_missing` | `mesh_only` | `deferred`
  | `unsupported_scope` | `no_instances`) and a `hint` — a skip is not a
  failure; `error` means the check itself broke (a predicate raised, an
  instance id no longer exists), i.e. *we do not know*, which is not *it is
  fine*.
- **A rebuild evaluates the shape tier only** (`valid`, `mass`, `volume`,
  `bbox`, `wall`, `that`). The assembly tier (`interference_free`,
  `clearance`, `stackup`) and the expensive tier (`fem_static`) come back
  `skip`/`deferred` there — a 600 s solve inside a slider drag is not "without
  friction". `run_specs` evaluates all three.
- **Requirement strings are opaque.** We store and group by them; we never
  parse or resolve them. A requirement with zero checks does not exist.
- **Results are cached under the same content hash as the mesh** (`SPECS` lives
  in the script, so editing a spec invalidates it for free), so re-running
  after no change costs no kernel work. **A failed evaluation is cached too** —
  a `SPECS` that will not declare, a script that will not build and a predicate
  that hangs are properties of that script and those params, and every
  `get_part` would otherwise re-pay them. `run_specs` is the one surface that
  ignores a cached failure and measures again. The keys cover every input a
  check reads: the assembly sidecar also hashes the **mate graph** and each
  referenced part's **PMI dims** (what `check_stackup` sums), and a cached
  `fem_static` row is additionally keyed by the material's **E** — the part
  cache key covers density only, and displacement scales with 1/E.
- **A declaration is a shape.** A hand-written `SPECS` entry is accepted only
  if it carries every key a constructor emits (`spec`, `kind`, `scope`, `name`,
  `limit`, `options`, `requirement`); anything else is a `contract_error`
  naming the key. Limits must be finite: `nan`/`inf` are rejected at
  construction, because every ordered comparison against NaN is false and such
  a check would report `pass` without measuring anything.

| Tool | Arguments | Returns |
|---|---|---|
| `run_specs` | **project**, part_id, ref | Evaluate and report — all three tiers (`part_id` narrows to one part and skips the project scope). `{project, ref, generated, status: green\|red\|skip, summary: {passed, failed, skipped, errors, total}, checks: [{id, name, kind, scope, part, status, measured, limit, unit, requirement, location, message, details, reason?, hint?}], parts: {<id>: {status, summary, cached, checks: [id]}}, project_checks, requirements: {<req>: {status, checks: [id]}}, declared, warnings, errors}`. `id` is `"<part>:<name>"` or `"project:<name>"` and every section joins to `checks` by it; `location` is a world point where the measurement yields one (the wall check's thin point, the clearance witness point). `status` is `red` when anything failed **or errored**, `green` when nothing did (skips are allowed and named), `skip` when nothing was declared at all. `ref` evaluates another **branch**'s state without switching yours — a tag is a `validation_error` (a tag must never answer for a branch), and a `ref` on a project with no git is a `validation_error` naming git. |
| `list_specs` | **project**, part_id | Declared intent with **no evaluation and no build** — it works on a project whose parts have never been built and on a part whose script does not build at all. `{project, declared, parts: {<id>: {specs: [<declaration>]}}, project_specs: {path, exists, specs}, requirements: {<req>: [id]}, errors, warnings}`. A declaration is the constructor's own dict — `{spec, kind, scope, name, limit, requirement, options}` — with a `check_that` predicate reported as `"predicate": true` (the callable never leaves the kernel worker). A file that will not execute is an `errors[]` entry, so one broken `specs.py` never hides the part specs. |
| `get_project_specs` | **project** | `{path, exists, script, declared, specs, declaration_error, warnings}`. A project with no `specs.py` answers `{"script": null, "specs": []}` — not a 404. `declaration_error` is the script error when the file will not execute: reported, not raised, so you can read a broken file in order to fix it. |
| `set_project_specs` | **project, script** | Writes `specs.py` and returns the same shape as post-state. The file is written **unconditionally** and reported afterwards — a broken script is saved and its error returned, because you must be able to save one in order to fix it (the `update_part_script` rule). `""` deletes the file. Refused with a `conflict_error` under another client's turn lock; the write is snapshotted into git like any other edit. |

**Part scope rides the rebuild.** `update_part_script`, `set_params` and
`set_solid_materials` — and `get_part` — carry
`specs: {status, summary: {passed, failed, skipped, errors, total}, checks,
requirements, cached, warnings}` for that part, or `null` when the part
declares none ("none declared" is not "not evaluated"). A failed build carries
no `specs` key at all. This is the loop an agent iterates in: `set_params` →
read `specs` → adjust → green.

**The proposal gate (fail-closed).** A proposal's `specs` gate is this same
evaluation over its **source branch**, and it is a hard block: `proposal_merge`
raises a `conflict_error` naming `specs` when the gate is red, before anything
is merged — and a declared check that could not be evaluated at all (a kernel
error, a source branch that will not build, an evaluation that blew the 30 s
gate budget) is *also* red, because an unmeasured spec is not evidence of
green. **`allow_invalid` does not waive it**: that flag is about the kernel's
verdict on geometry and nothing else. The gate's `details` carries
`{status, summary, failures, skips, errors, ref, source_head,
specs_py_changed, reason}` — `specs_py_changed` flags a proposal that edits the
*spec* rather than the geometry, which the review packet's part rows cannot
show (measured from the merge base, so a target that moved never sets it). Run
`run_specs {project, ref: "<source>"}` to see and fix what the gate is red
about.

Divergences between the gate and a `run_specs` report, all deliberate and all
fail-closed:

- **Every `skip` is a `fail` in the gate**, whatever its reason —
  `fem_extra_missing` (no `[fem]` extra on the reviewing machine), `mesh_only`
  (an STL side has no B-rep to measure against), `unsupported_scope`,
  `no_instances`, and any reason added later. A report is read by an engineer,
  who is better served by the named skip and its hint; a gate decides a merge,
  and "declared but not measured" is exactly the hole it exists to close —
  otherwise swapping a STEP reference for an STL, or reviewing without the FEM
  extra, silently satisfies a declared check. Each gate failure keeps
  `details.reason`, `details.hint` and `details.skipped_in_report: true`, and
  names the reason in its message.
- **The gate never answers `pending`.** `proposal_merge` blocks a `fail` and
  nothing else, so a source head that moved during evaluation is a `fail`
  saying to retry, not a `pending` that would let unevaluated content merge.
  That verdict is not memoized, so reading the proposal again re-measures.
- **A spec module that will not read or declare is a red `declaration` check
  row** named after the file (`project:specs`), not merely an `errors[]` entry:
  status and the gate are computed from the check rows alone, so a `specs.py`
  that raised while it executed used to leave the report green.
- **The 30 s budget is a deadline, and its verdict is remembered.** Every kernel
  call under the gate — the measurements, and the mate resolution an assembly
  check needs first — asks for what the budget has left rather than its own
  120 s/300 s/600 s ceiling, and the deadline is re-checked between checks. A
  `budget_exceeded` verdict is memoized for that source head — it is red with a
  stable reason, and re-paying an exhausted budget on every `proposal_get` is
  worse than answering from the memo — so the gate stays red until the head
  moves or `run_specs` (unbounded by design) warms the caches, which also drops
  the memoized verdict.

**Routes** (all under `/api`): `GET /projects/{proj}/specs?part_id=` →
`list_specs`, `POST /projects/{proj}/specs/run` → `run_specs`,
`GET|PUT /projects/{proj}/specs/file` → `get_project_specs` /
`set_project_specs`.

### Branches, versions and merges

Registered **only when `git` is on the server's PATH** (no git ⇒ no branch
tools, no `/branches` routes, and the product degrades to linear history).
Convention, repeated in every tool description because it is the one thing
agents get backwards: **`ours` = the target branch** (what you merge into),
**`theirs` = the source**, exactly like `git merge <source>`.

| Tool | Arguments | Returns |
|---|---|---|
| `branch_create` | **project, name**, from | Creates a branch and materializes its working tree at `<project>/.history/trees/<name>/`. Names match `[a-z0-9][a-z0-9_/-]{0,63}`. `from` defaults to *your* current branch and also accepts a tag or commit id. Does **not** switch you. Returns the `branch_list` payload plus `{created}`. |
| `branch_list` | **project** | `{branches: [{name, head, ts, message, is_default, is_current, checked_out_by: [client…]}], current, default, you}` (`head` is the branch's commit id, `message` its subject; `you` is your client identity). Branches are **per client identity**: two agents can sit on two branches of one project at once, each with its own turn lock and undo stack. |
| `branch_switch` | **project, name** | Points *your* client at `name` (nobody else moves). O(1) — the tree already exists — and it snapshots the tree you are leaving first, so a switch is always a clean, restorable boundary. Returns `{branch, project}` (the post-switch project state). Publishes `branch_changed`. |
| `branch_delete` | **project, name** | Deletes the branch and its working tree. `validation_error` for the default branch, for a branch any client has checked out, and for one whose working tree has uncommitted changes that cannot be snapshotted (the tree is committed first, then removed — `--force` never discards live work). Versions (tags) made on it survive. Returns the `branch_list` payload plus `{deleted}`. |
| `version_tag` | **project, name**, message | Names the current state of your branch as an immutable version (an annotated git tag): `{tag, commit, versions}`. Re-using a name is a `conflict_error` — versions never move, and there is deliberately no delete tool. Restore one with `project_restore {commit: "<name>"}`. |
| `list_versions` | **project** | `{versions: [{name, commit, ts, author, message, referrers}]}`, newest first. `referrers` is the forward-compatibility hook for releases (PRD-015). |
| `merge_branch` | **project, source**, target, allow_invalid | Merges `source` (theirs) into `target` (ours; default: your current branch). Fast-forwards when the target has nothing of its own (`{fast_forward: true}`) — **still validated**, because an edit persists before its rebuild fails, so a branch can carry a script that does not build; merging an ancestor returns `{already_up_to_date: true}` with `validation: null`. Otherwise a real three-way merge: part scripts via git's textual merge, `project.json` **always** re-merged key-wise (per part, param, instance, material, PMI section). Conflicts come back as `merge_conflict` with the merge **staged** — nothing outside `.history/agentcad/` is written and no ref moves until you resolve or abort. On success: one merge commit with **two parents**, plus `{commit, parents, conflicts_resolved, validation, project}`. Re-running it on a staged merge whose branches have moved is a `conflict_error` — the recorded resolutions no longer apply, so discard it with `merge_abort` and merge again rather than losing them silently. |
| `resolve_merge` | **project, choices** | Resolves the staged merge. `choices` maps a conflict's `path` (scripts, e.g. `"parts/flange.py"`) or `key` (manifest, e.g. `"parts.flange.params.bolt_d"`) to `{"take": "ours"\|"theirs"\|"base"}`, or `{"content": "<full file text>"}` for a script, or `{"value": …}` for a manifest key. In a manifest conflict a side that has **no value** (it deleted the key, or both branches added it so there is no base) is **omitted** from the payload — `"ours": null` means that side authored a JSON `null`, and taking it writes that null rather than deleting the key. Each manifest conflict also carries `path`, the exact key segments, because an id may contain a `.` (`parts.body.solid_materials.wall.inner`); `key` remains the dotted string you address the choice by. A `kind: "binary"` conflict (anything under `imports/`) carries `sides: {base\|ours\|theirs: {bytes, sha256} \| null}` instead of text and takes a side only — `content` is a `validation_error`. Taking a side where the file is absent (that branch deleted it) **deletes** it; taking `base` when there is none (both branches added the file) is a `validation_error` naming the valid choices. Partial resolution is fine — the reply lists what is still outstanding — and the merge completes (validation pass included) as soon as nothing is, **unless the staged merge is held**: a merge staged by `proposal_merge` carries `held_by: "proposal:<id>"`, and at zero outstanding this returns `{held: true, merged: false, outstanding: 0, held_by, hint}` having landed nothing — completing it is `proposal_merge`'s, which re-checks that proposal's gates first. `merge_abort` still discards it. Unknown path/key → `validation_error`, staged merge untouched. |
| `merge_abort` | **project** | Discards the staged merge (its worktree and state); no branch moves. `{aborted: false}` when nothing was staged. |
| `merge_status` | **project** | `{merge: {id, source, target, base, by, created, outstanding, conflicts, resolved, held, held_by} \| null}` — re-enter a merge you or another client staged earlier (e.g. after a reload or a server restart). |

**The validation pass (FR9).** Before **any** merge lands — fast-forward
included — the merged tree is rebuilt by the real kernel: changed parts build,
mates re-resolve, referential integrity is checked, and interference is re-run.
`validation` is
`{ok, blocked, warnings: [str], built: [{part, cached}], failures: [{part, error}], integrity: [{kind, instance, …}], interference: {checked, new_pairs: [{a, b, volume_mm3}], skipped}}`.
Only **newly introduced** interference pairs block, so a project that already
overlaps stays mergeable; `skipped: "instances"` means the assembly was above
the pair-check cap (40 instances) or had fewer than two — above the cap it also
appears in `warnings`, so an `ok: true` report never hides a check it did not
run. `integrity` also carries `kind: "manifest_invalid"` when the merged
`project.json` would not load (no `name`, a malformed `parts` list, …).
Failures block the merge with a `validation_error` carrying the same report
under `details.validation`; `allow_invalid: true` lands it anyway, with the
failures recorded in the merge commit message and returned to the caller. Parts
already built on either branch are cache hits — the mesh cache is shared across
branches (byte-determinism, FR13).

A `project.json` that **exists but does not parse** on any of base/ours/theirs
is a `validation_error` naming the ref and the file, refused before the merge
starts: an unreadable manifest is not the same statement as a deleted one, and
reading it as `{}` would merge as "this side deleted everything".

**Branch names are resolved as branches.** `git rev-parse <name>` searches
`refs/tags` before `refs/heads`, so a tag named like a branch can answer for
it. Every branch operation (create-from-current, switch, delete, merge, tag)
resolves `refs/heads/<name>` explicitly; only surfaces documented to take *any*
ref — `project_history {ref}`, `project_restore {commit}` — keep git's
precedence.

**Events.** `branch_changed {project, client, branch}` and
`merge_completed {project, source, target, commit, validation}`, alongside the
usual `project_changed`.

**Routes** (all under `/api`): `GET|POST /projects/{proj}/branches`,
`POST /projects/{proj}/branches/switch`,
`DELETE /projects/{proj}/branches/{name}` (the name may contain `/`),
`GET|POST /projects/{proj}/versions`,
`GET|POST /projects/{proj}/merge`, `POST /projects/{proj}/merge/resolve`,
`POST /projects/{proj}/merge/abort`.

### Change proposals

A proposal is a CAD pull request: a durable, attributed object over a branch
pair, with an auto-generated **review packet** of kernel-computed evidence and
a merge that only happens through a gate. Registered under the same condition
as the branch tools — **only when `git` is on the server's PATH**.

Convention, repeated in every description because it is the one thing agents
get backwards: read the pair like `git merge <source>` — the **target branch is
`old`** (ours, what the change lands in) and the **source branch is `new`**
(theirs, the proposed work).

| Tool | Arguments | Returns |
|---|---|---|
| `proposal_create` | **project, source, title**, target, description, draft | `{proposal, gates, packet}`. `target` defaults to the project's **default** branch, not your current one (a proposal is read by other clients). A second *active* proposal for the same pair is a `conflict_error` naming the existing id; an unknown branch is a `notfound_error` (a version tag does not answer for a branch); `source == target` is a `validation_error`. `draft: true` opens it unreviewable until you update it to `open`. |
| `proposal_list` | **project**, state | `{proposals: [{id, source, target, title, state, author, author_kind, created, updated, reviews, merge_commit}], counts: {<state>: n}}`, oldest id first. |
| `proposal_get` | **project, id** | `{proposal, gates, audit, packet}`. `gates` is the merge checklist — `[{name, state: pass\|fail\|pending\|skipped, summary, details}]` over `state`, `approvals`, `validation` (pending until the merge runs it), `specs` (the fail-closed design-spec gate over the source branch — see [Design specs](#design-specs)) and `checks` (the geometry-CI verdict posted to this proposal — see [Geometry CI](#geometry-ci); `skipped` until one is). `audit` is the append-only log. `packet` here is only a status summary (`{generated, stale, ok, frozen}`, or `null` before the first view). |
| `proposal_update` | **project, id**, title, description, state | Edits the title/description, or moves state: `draft → open`, anything active → `closed`, `closed → open`, `changes_requested → open`. Approving is `proposal_review` and merging is `proposal_merge`; neither can be faked by writing a state — any other move is a `validation_error` carrying `{from, to, allowed}`. |
| `proposal_packet` | **project, id**, regenerate | The review packet (below). Generated on first view, re-served while both branch heads hold, regenerated when either moved or on `regenerate: true`. A packet frozen by a merge refuses `regenerate` with a `conflict_error`. A **terminal** (merged/closed) proposal is never measured again — a packet built then would describe the branches as they are now, under this proposal's name. Merging freezes the packet, or, when none was ever generated, freezes the *absence* as `{frozen: true, generated: null, ok: false, parts: [], note: "…"}`; a closed proposal keeps whatever it had, and a terminal proposal with no packet at all is a `conflict_error`. |
| `proposal_render` | **project, id, side**, part, view | One image you can actually look at: `{path, width, height, view, side, part, png_base64}`. `side` is `old` (target) or `new` (source); omit `part` for the whole assembly. Framed by the union of both sides' bounding boxes, so old and new superimpose. Views: `iso`, `front`, `top`, `right`. Every render is **written to `path`** and served from there afterwards, so the path names a file that exists. A **frozen** packet serves only the renders stored with it: any other view is a `conflict_error`, because drawing one now would draw today's branches under the decision's date. |
| `proposal_review` | **project, id, verdict**, summary | `approve` → `approved`, `request_changes` → `changes_requested` (blocks the merge until the author reopens it), `comment` (recorded, state unchanged). The latest **approve/request_changes** *per actor* counts; a `comment` is recorded and audited but never changes the approvals count — it changes no state, so it retracts nothing. `summary` goes into the permanent audit log with your identity. |
| `proposal_merge` | **project, id**, allow_invalid | Gates first, then PRD-001's `merge_branch` unchanged. Success returns that payload plus `{proposal, gates}` with the proposal `merged` and its packet frozen. A `merge_conflict` records the staged merge on the proposal, so a merge finished by `resolve_merge` is recognised and recorded on the next read (see below). |

**The packet.** One JSON document — `{ok, stale, frozen, generated,
generated_by, elapsed_ms, source, target, source_head, target_head, base,
summary, parts, assembly, manifest, binary, warnings, errors}` — pinned to both
branch heads. Per changed part: `script_diff` (unified text plus `hunks`
anchors), `params_diff` (`added`/`removed`/`changed` rows; a scalar override is
one row with `"field": "value"`, a full parameter spec one row per changed
field — and the scripts' own `PARAMS` **declarations** are diffed too, as rows
carrying `"source": "spec"` and a `"spec.<field>"` field name, because changing
a `default`, a `max` or a `type` in the script changes no override at all),
`build` per side, `metrics` (`{old, new, delta, pct}` for
`volume_mm3`/`mass_g`/`area_mm2`, a per-axis `center_of_mass` delta, both
bounding boxes plus `size_delta_mm`), `geom_diff` (`added_mm3`/`removed_mm3`
computed by kernel booleans, with ACM1 overlay meshes), and `renders` — before
and after **URLs** sharing one camera `frame`. `assembly` carries instances
added/removed/moved (at *resolved* transforms), mate changes and the total-mass
delta; its `renders` are `null` (assembly renders are the expensive kind — ask
for one with `proposal_render`).

Four things to know before you consume it:

- **Renders are URLs, not images.** MCP and chat lift exactly one top-level
  `png_base64` per tool result, so a packet with N pairs cannot carry them.
  Call `proposal_render` to *see* a side.
- **Packet-internal failures are payload fields, never errors** (FR8). An
  unbuildable side is `build.<side>.ok: false` with the structured script error
  and `metrics.<x>.<side>: null`; a failed or impossible boolean is
  `geom_diff.available: false` with a reason (`skipped: "mesh"` for an imported
  reference part — the `check_interference` rule); anything unexpected lands in
  `warnings`/`errors`. `ok` is `false` only when a piece of **evidence** could
  not be read: a git command that failed (`cat-file` on a manifest, a `diff`)
  is an `errors[]` entry carrying `fatal: true`, the `command` and the `ref`,
  and it forces `ok: false` rather than passing an empty result off as "nothing
  changed" or "the whole project was deleted".
- **Unchanged parts cost nothing.** A part whose content hash matches on both
  sides short-circuits to `geom_diff: {available: true, unchanged: true}` with
  zero kernel work — packet cost scales with the change, not the project.
- **`stale` is honest, not fatal.** A moved head marks the packet stale and it
  regenerates on the next view; reviews made against an older source head stay
  counted but are marked `stale`. A head that moves *during* a build discards
  that build and takes it again; a head that moves twice persists the packet
  marked `stale` rather than labelling mixed evidence with a head it no longer
  describes. A **frozen** packet is never `stale` (it is pinned) but carries
  `stale_at_merge: true` when the commits it describes are not the commits the
  merge landed.

**Gating and policy.** `proposal_merge` checks gates **first**: a red one is a
`conflict_error` naming it in `details.failing` with the full `details.gates`,
and nothing is merged. Policy v1 is two per-project fields in
`<project>/.history/agentcad/proposals/policy.json` — `approvals_required`
(default 1) and `self_approve` (default false, so the author's own approval
does not count). Then PRD-001's merge runs: a `merge_conflict` comes back at
HTTP 200 with the merge staged and the proposal still open (resolve with
`resolve_merge`, or discard with `merge_abort`, then call `proposal_merge`
again). That staged merge is **held** by the proposal: `resolve_merge` records
the resolutions and answers `{held: true, held_by: "proposal:<id>",
outstanding: 0}` **without landing anything**, because landing it there would
walk straight past the gates — only `proposal_merge` completes it, after
re-evaluating them. A failing kernel validation pass is a `validation_error`
carrying
`details.validation`. **`allow_invalid: true` overrides the kernel validation
gate only** — it never waives the approvals policy, and it never waives the
fail-closed `specs` gate — and it is recorded in the
audit log, on the proposal and in the merge commit message.

**Attribution.** Every action is stamped `{seq, ts, actor, actor_kind, action,
details}` in an append-only `audit.jsonl`. `actor` is the client identity the
turn-lock plumbing already carries (`browser`, `chat:<session>`, an MCP agent
id via `X-Agent-Id`); `actor_kind` is `human` **only** for the browser UI —
the chat dock is a human asking an *agent*, so its actions are the agent's.
This is honest bookkeeping, **not authentication**: the identity header is
unvalidated until PRD-005 replaces it with an authenticated principal (no
schema change). Timestamps are zone-aware UTC (`…Z`).

Proposals are workflow metadata, not model state: they live in PRD-001's
sidecar at `<project>/.history/agentcad/proposals/<id>/`, outside every working
tree, so `project_restore` never rewinds them and every branch sees the same
proposals.

**Events.** `proposal_changed {project, id, state, reason}` for every
state/packet transition — `reason` is one of `created`, `updated`, `review`,
`packet`, `checks` (a geometry-CI report was posted), `merged`.

**Routes** (all under `/api`): `GET|POST /projects/{proj}/proposals`,
`GET|PATCH /projects/{proj}/proposals/{id}`,
`GET /projects/{proj}/proposals/{id}/packet?regenerate=1`,
`POST /projects/{proj}/proposals/{id}/review`,
`POST /projects/{proj}/proposals/{id}/merge`,
`GET /projects/{proj}/proposals/{id}/render/{side}[/{part}]?view=iso`
(`image/png`),
`GET /projects/{proj}/proposals/{id}/diff/{gen}/{part}/{kind}.acm`
(the overlay mesh, `application/octet-stream`). `{gen}` is the packet's
`generation` — the build its assets were published with — so a packet only ever
serves the geometry it was persisted with; a generation that has been collected
(or discarded) is a 404. Read the URLs off the packet rather than composing
them.

### Geometry CI

One call certifies a whole project: rebuild every part, re-resolve the
assembly and look for interference, evaluate the declared design specs (all
three tiers) and regenerate the drawings. It is exactly what `agentcad check`
runs — the same `CheckRunner` over the same project — so the report is
identical on both surfaces. Full reference: [geometry-ci.md](geometry-ci.md).

| Tool | Arguments | Returns |
|---|---|---|
| `run_checks` | **project**, ref, stages, strict, budget, proposal | The whole `schema: 1` report (below). `stages` is a subset of `build`, `assembly`, `specs`, `drawings`; `ref` certifies a branch/tag/commit instead of the working tree; `strict` counts skipped rows as failures *in the verdict only*; `budget` is a soft deadline in seconds; `proposal` posts the report to that change proposal. |

**It measures nothing new.** Every row comes from a surface you already have —
`update_part_script`'s rebuild, the mate resolver, `check_interference`,
`run_specs`, `generate_drawing`/`flat_pattern` — so a failing row's `error` is
that tool's payload **verbatim**, including `details.line` and the Error Doctor
`details.hint`. A red stage is a structured task you can pick up and fix
without re-deriving anything.

**A red check is data, never an error.** The call returns normally; only the
harness raises — an unknown project is a `notfound_error`; an unknown stage, an
**empty** `stages` list (it is refused, never read as "all four"), a
non-finite `budget` and a `ref` on a project with no git are all
`validation_error`; and an unknown or already-merged proposal is a
`notfound_error` / `conflict_error` raised **before** anything is measured. A
refused **post** does not raise here at all — it is a receipt on the returned
report (see `posted.ok` below).

Four things about the payload:

- **All four stages always appear**, in order, whatever you selected: an
  unselected one is `skip`/`not_selected`, so you never have to guess whether a
  stage was green or never ran. Per-stage rows are called `items` — `checks`
  already means the gate, a spec report's rows and the proposals UI tab.
- **Four row statuses, and they are not interchangeable.** `pass`/`fail` were
  *measured*; `skip` is a named structural inability to measure and always
  carries a `reason` **and** a `hint` (`not_selected`, `budget_exceeded`,
  `mesh_only`, `fem_extra_missing`, `not_declared`, `no_instances`,
  `not_script`, …); `error` means the check itself broke — "we do not know",
  which is not "it is fine".
- **`exit_code` is the verdict as one integer**: `0` green · `1` red, the model
  is wrong · `2` harness, no verdict at all (`complete: false` — a budget ran
  out mid-run). `strict: true` rewrites **no row**: it lists the skipped ids in
  `strict_failures` and lets only the derived `status`/`exit_code` move, so a
  reader can always tell what was measured from what was demanded.
- **`ref` never mutates the project.** The commit is materialized into a
  throwaway detached git worktree and measured through a second, ephemeral
  service, so your files and `.cache/` are byte-identical afterwards — at the
  price of a cold cache, which makes it much slower than checking the tree you
  are in.

```jsonc
{"schema": 1, "agentcad": "0.1.0", "project": "rocketry",
 "source": {"kind": "worktree|branch|tag|commit", "ref": null, "sha": null,
            "label": null, "host_sha": null, "dirty": false},
 "started": "…Z", "finished": "…Z", "duration_s": 46.3,
 "status": "green|red|skip", "complete": true, "strict": false,
 "strict_failures": [], "exit_code": 0,
 "summary": {"passed": 18, "failed": 0, "skipped": 1, "errors": 0, "total": 19},
 "stages": [{"name": "build", "status": "green|red|skip", "reason": null,
             "duration_s": 44.8, "summary": {…},
             "items": [{"id": "build:nozzle", "kind": "part",
                        "subject": "nozzle", "status": "pass",
                        "message": "built — …", "reason": null, "hint": null,
                        "requirement": null, "error": null,
                        "details": {"cache_key": "…", "volume_mm3": 1.0,
                                    "mass_g": 1.0, "n_solids": 1,
                                    "is_valid": true, "cached": false}}]}],
 "requirements": {"ENG-014": {"status": "pass",
                              "checks": ["specs:nozzle:wall_min"]}},
 "warnings": [], "errors": [],
 "host": {"platform": "darwin", "python": "3.12…", "agentcad": "0.1.0",
          "fem": true, "sandbox": true, "pool_size": 1,
          "kernel_pool": "KernelPool"}}
```

The specs stage additionally embeds its `run_specs` document whole as
`stage["report"]`, and the top-level `requirements` map is that report's
traceability re-keyed to **this** report's item ids.

**Posting to a proposal.** `proposal: "<id>"` stores the report durably as that
proposal's `checks.json`, appends one audit line, publishes `proposal_changed
{reason: "checks"}` and returns a `posted` receipt on the report. It is then
read by the proposal's `checks` gate: nothing **ever** posted (no record *and*
no `checks_posted` audit line) → `skipped` (blocks nothing); a **complete,
green** report against the source branch's **current** head → `pass`; anything
else — red, incomplete, unreadable, not a valid record, *deleted after being
posted*, or certifying a head the branch has moved past — → `fail`, which
**does** block `proposal_merge`. A stale green is a `fail` saying to re-run,
never a soft `pending`: a merge blocks on `fail` and nothing else, so a
`pending` would wave through commits nobody measured. Posting is how a proposal
opts in — this gate is **evidence**, while the `specs` and `validation` gates
are enforcement and re-measure on every merge.

A report that measured a **dirty working tree** cannot be posted at all: its
`head` is the *committed* sha, so the gate would read it as certifying bytes the
run never measured. Commit or stash, then re-run.

**Read `posted.ok`.** A post that was refused — a dirty tree, or a proposal that
went terminal while you were measuring — is a **receipt, not an error**:
`run_checks` returns the report it just measured, at HTTP 200 with no top-level
`error`, carrying `posted: {id, ok: false, error: {...}}` and a `NOT posted`
line in `warnings`. Minutes of kernel work are not thrown away to report a
delivery failure. `status` and `exit_code` are about the **geometry** — a
green, complete report whose `posted.ok` is `false` certified nothing, and that
proposal's `checks` gate is still `skipped`. (On the CLI the same refusal is a
message on stderr and exit `2`.)

**Routes** (under `/api`): `POST /projects/{proj}/checks` (body whitelisted to
`{ref, stages, strict, budget, proposal}`; a red project is an ordinary **200**
— only "no verdict at all" is an HTTP error), `GET /projects/{proj}/checks`
(the last report *this process* produced, 404 when there is none) and
`GET /projects/{proj}/checks?proposal=<id>` (the durable posted record).

**Events.** `check_finished {project, ref, status, exit_code, summary,
duration_s}` after **every** completed run — including a red one and a
budget-truncated one, and from the CLI as well as from the tool and the route.
It is deliberately not `project_changed`: measuring a project is not changing
it, so it triggers no history snapshot.

### Review threads

Feedback that points at something and can be marked done: a thread is a root
comment plus replies, anchored to a part, a face, a parameter, a script line
range, an assembly instance or a proposal diff hunk, with state `open` or
`resolved`. Threads live at `.history/agentcad/comments/` — **canonical,
branch-free, and outside model state**: every branch sees the same list,
`project_restore` cannot rewind one, no merge ever touches one, and a comment
never appears in `git status`.

| Tool | Arguments | Returns |
|---|---|---|
| `list_comments` | **project**, part_id, state, kind, branch, proposal, anchor_status, resolve_anchors | `{threads: [{id, state, anchor, resolution, branch, author, author_kind, created, updated, resolved, comments: [{id, author, author_kind, ts, body, attachments: [{path, available}], mentions, edited, deleted}]}], counts: {open, resolved, orphaned}}`. `counts` describes the whole project, never the filtered page. `resolve_anchors: false` is the cheapest listing — no `resolution` block and no `orphaned` count, because nothing was looked at. |
| `add_comment` | **project, body**, anchor, thread, attachments | The post-state `{thread}`. Exactly **one** of `anchor` (open a new thread) or `thread` (reply to that id) — both or neither is a `validation_error`. |
| `resolve_thread` | **project, thread** | The post-state `{thread}`. Idempotent: resolving a resolved thread records nothing and publishes nothing. |
| `reopen_thread` | **project, thread** | The post-state `{thread}`. |
| `list_notifications` | project, unread | `{notifications: [{seq, kind: "mention", to, project, thread, comment, from, ts, read}], unread: n}` for **the calling identity only**, oldest first. Omit `project` for every project on this server. |

**The six anchors, validated at creation** (a bad anchor is a
`validation_error`, never a stored orphan):
`{kind: "part", part}` · `{kind: "face", part, face_index}` (the part must have
been built; validated against the mesh's face count, and an imported reference
part has no faces to anchor to) · `{kind: "param", part, param}` ·
`{kind: "script_range", part, start, end}` (1-based, **inclusive**) ·
`{kind: "instance", instance}` · `{kind: "proposal_hunk", proposal, file,
hunk}`. Branch, head and every piece of evidence (a face's signature, a line
range's snippet) are stamped **by the server** and refused from the caller: a
signature a client can assert is not evidence of anything.

**An anchor is immutable; its status is computed on every read** into the four
statuses in the conventions above, so `resolution` is *view* data and the
stored `anchor` never changes under you. Address a face through
`resolution.face_index` and lines through `resolution.start`/`end` — **never**
the stored `anchor.face_index`, which is the ordinal at creation time. Face
ordinals are not stable across a parameter change (measured: 87–93% hold; one
bundled part renumbered 20 of its 44 faces for a **1%** tweak), so a face
anchor is re-matched from its stored mesh signature: measured over 2 693 faces
whose identity is known it resolves about **half** the time and comes back
honestly `orphaned` otherwise, with **2 mis-pins in 2 693** (both on a body of
revolution). That sweep only ever changes a *parameter*, so it says nothing
about the class you hit when you **delete** a feature; that one was measured
separately over 327 faces that no longer exist, and **4 of them re-pinned onto
the face that was underneath** (98.8% correctly orphaned). Orphan rather than
guess — a strong bias, not a guarantee: **a cut-away face can still re-pin**,
so treat a resolved face as strong evidence, and confirm with `face_info` when
the answer decides something expensive. Two ceilings are worth knowing before you
read an `orphaned` as a bug: a parameter change that moves a face's
position *relative to the shape's bounds* orphans it even though the face still
exists (`bbox_uvw` is measured against those bounds — which is exactly what
makes a pure scale survivable), and a **closed curved face** such as a
cylinder's side orphans on any edit, because its area-weighted normal nearly
cancels and no candidate clears the normal gate.

**A script range is re-found by its text plus its context, never by its text
alone.** Tier 1 looks for the stored snippet verbatim; a lone copy must be
contradicted by neither side of the stored surrounding lines — deleting the
anchored one of two identical lines used to re-pin the thread onto the
unrelated survivor and report `moved` at confidence 1.0, and one *agreeing*
side is not enough to rule that out, because duplicated blocks routinely end
with the same line. The same rule guards the address the anchor already has: a
range that still holds its exact text but whose context says a different block
now sits there is put to the diff rather than answered `ok` (with no diff to
read — no git, no head — the address still wins, so an ordinary edit near a
thread never costs it its pin). With two or more copies the context is a
tie-break, as before. A refused hit falls through to tier 2, a `difflib` map
over the blob at the anchor's own head, which answers from the real diff or
`orphaned`s.

**Listing never builds.** Resolution reads the manifest, the meshes a build
already wrote and at most one git blob per anchor — so a face anchor on a part
that has never been built is `unverified`/`part_not_built` rather than a 300 s
rebuild, and `list_comments` on a 40-part project stays a cheap read.

**Hunk threads re-map by header, and never regenerate a packet.** A
`{kind: "proposal_hunk", proposal, file, hunk}` anchor is validated against the
proposal's **already-built** review packet (`file` is a path in its script
diffs, `hunk` a 0-based index into that file's hunks; no packet on disk is a
`validation_error` telling you to call `proposal_packet`, never a build), and
it stores that hunk's header byte-for-byte plus the packet's `generation`.
Because a regeneration renumbers hunks freely, the header is the identity:
same generation → `ok`; a new generation carrying that exact header exactly
once → `moved` to its new index (**never** `ok`, even at the same index — a new
generation measured different commits); the header rewritten or now
non-unique → `orphaned`/`hunk_regenerated`; a packet frozen by a merge →
`unverified`/`packet_frozen`, because the diff it describes is history and the
thread is the record of a review of exactly that. Reading a thread only ever
reads the persisted `packet.json` — never `proposal_packet`'s regeneration
path, which rebuilds geometry on both sides and can move the proposal's state.
`list_comments {kind: "proposal_hunk", proposal: "3"}` fetches one proposal's
threads in a single call.

**Attachments** must live under the project's `exports/` (pass what
`render_view` returned, or `"exports/renders/iso.png"`); anything resolving
outside that tree, symlinks included, is a `validation_error`, and there are at
most 8 per comment. A file that is missing at read time is reported as
`{path, available: false}`, never an error — `exports/` is branch-scoped.

**Attribution is bookkeeping, not authentication.** `author`/`actor` is the
client identity (`browser`, `browser:<nonce>`, `chat:<session>`, an MCP agent's
`X-Agent-Id`) and `author_kind` is `human` iff it is the browser; the header is
unvalidated. Anyone may resolve or reopen anything; only a comment's own author
may edit or delete it, and the root comment cannot be deleted at all (retire a
thread by resolving it). Every action appends to a per-thread audit log.

**Mentions.** `@<identity>` in a body notifies that identity — but only when it
names a **plausible** one: `browser`, `browser:<nonce>`, `chat`,
`chat:<session>` (the chat engine's own `[a-z0-9_-]{1,32}` session rule), or a
client the presence registry currently knows. `@todo` and `@nobody` stay plain
text and deliver nothing, and mentioning yourself delivers nothing. Deliveries
land in one append-only `notifications.jsonl` per project with a `to` field —
never a file per identity, which would make an unvalidated header into a
path — and *read* is another line in the same log, so unread is derived
(mentions minus every seq a later `read` line names) and nothing is rewritten.
Editing a comment re-scans it and delivers only the **newly** mentioned.

Routes: `GET|POST /api/projects/{proj}/comments`,
`GET /api/projects/{proj}/comments/{id}`,
`POST /api/projects/{proj}/comments/{id}/resolve`,
`.../reopen`, `PATCH|DELETE /api/projects/{proj}/comments/{id}/comments/{cid}`
(edit or tombstone one comment — panel affordances, deliberately not tools),
`GET /api/projects/{proj}/comments/{id}/audit`,
`GET /api/projects/{proj}/notifications?unread=`,
`POST /api/projects/{proj}/notifications/read {ids?}` (omit `ids` to mark all
of yours; another identity's seq is a 422). Both notification routes answer for
the identity of the *request* and never take one as an argument.

**Events.** `comment_changed {project, thread, state, action, part}` on every
mutation (`created`, `replied`, `resolved`, `reopened`, `comment_edited`,
`comment_deleted`) — and on **no** no-op. It is deliberately not
`project_changed`: a comment is not a model change, so it triggers no history
snapshot and no rebuild. Each mention adds `notification {to, project, thread,
comment, from, ts}`, published straight after it. **The bus is a broadcast**:
every `/ws` client receives every `notification` and filters on `to` itself.
That is honest for a single-user, 127.0.0.1-only server with no authentication
— it discloses nothing a `GET` on the same box would not — and per-principal
delivery arrives with real identity (PRD-005), with no payload change.

### Presence and per-part claims

Who else is in this project, and who is currently editing which part. **There
are no tools here on purpose** — an agent coordinates through `acquire_turn`
and branches, and a claim is a *human*-vs-human courtesy that an agent must
never be blocked by (see the precedence table below). The surface is three
routes:

```
GET  /api/projects/{proj}/presence            # read the roster, register nobody
POST /api/projects/{proj}/presence            # heartbeat  {part_id?, surface?,
                                              #             label?, claim?, leave?}
POST /api/projects/{proj}/claims/override     # {part} -> {part, armed_until, claim}
```

Both presence calls answer with the whole roster:
`{you, clients: [{id, kind, label, focus: {part_id, surface}, since}],
claims: {<part>: {part, holder, holder_kind, expires_at}}, ttl_s,
heartbeat_s}`. `surface` is one of `viewport | editor | inspector | proposals`
(anything else is a `validation_error`); `kind` is `human` iff the identity is
a browser. The registry is **in-memory and never persisted**, entries expire
45 s after the last beat (the browser beats every 15 s), and `{leave: true}` is
what a closing tab sends. An over-rate heartbeat is **HTTP 200 with
`throttled: true`**, never an error — the response is the mechanism, so a
client that misses every event still converges within one beat.

A **claim** names one part, is taken by *editing* (a heartbeat with
`claim: true`, or any successful part-scoped write — viewing never claims),
lasts 90 s, and is enforced at the one seam every persistent write passes
through:

| # | Condition | Outcome |
|---|---|---|
| 1 | another client holds the project **turn** | today's `conflict_error`, unchanged |
| 2 | you hold the turn | proceed — never claim-checked |
| 3 | the part is claimed by another client and **both are `human`** | `conflict_error` with `details: {claim: {part, holder, holder_kind, expires_at}, overridable: true}` |
| 4 | otherwise | proceed, and refresh your claim on that part |

Only `update_part_script` and per-part manifest edits are claim-covered;
whole-manifest writes (`add_part`, assembly, project materials) are turn-locked
only, on purpose. To take a part anyway, `POST …/claims/override {part}` arms a
**single-use, 30-second** override for your identity and retries the write; a
library or tool caller uses `with locks.claim_override():` instead. Arming
publishes `claim_changed` with `overridden_by`, so taking somebody's part is on
the record before the write lands.

**Events.** `presence_changed {project, clients, claims}` when the roster
actually differs (never on a no-op heartbeat) and `claim_changed {project,
part, holder, holder_kind, expires_at, overridden_by?}`. With
`comment_changed` and `notification` these are the four events PRD-008 adds;
all four ride the existing `/ws` broadcast and none of them is
`project_changed`.

**Identity is self-asserted.** `X-Agent-Id` is an unvalidated header, so
presence, claims, authorship and mentions are bookkeeping and coordination, not
authentication or access control. That is honest for a single-node,
127.0.0.1-only server; PRD-005 is what makes it a principal.

### Sketch solving

| Tool | Arguments | Returns |
|---|---|---|
| `solve_sketch` | **entities, constraints** | Solve a 2D constrained sketch to exact coordinates you can feed into a build123d `BuildLine`/`BuildSketch`. `entities = {points:[{name,x,y,fixed?}], lines:[{name,p1,p2}], circles:[{name,center,r,fixed_r?}]}`; `constraints = [{type, …}]`. Returns `{ok, points, circles, dof, max_residual, …}`; a sketch that does not converge comes back as a validation error (the solver homes to the *nearest* solution, so a mirrored initial guess yields a mirrored result). |

Constraint types: `fixed, coincident, distance, distance_x, distance_y,
horizontal, vertical, parallel, perpendicular, angle, point_on_line,
point_on_circle, radius, equal_radius, midpoint, tangent_line_circle,
tangent_circles`.

### FEM — present only with the `[fem]` extra

The FEM tools are registered **only** when `agentcad[fem]` is installed, so
they never appear in `GET /api/tools` (or to agents) otherwise — the
philosophy is that agents must not see a tool that cannot run. Without the
extra, the routes answer 501 with an install hint.

| Tool | Arguments | Returns |
|---|---|---|
| `fem_static` | **project, part_id, fixed_face, load_face**, load_N, load_dir, E_mpa, nu, mesh_size_mm | Linear-static FEM: clamp one axis-aligned face, load another, return max displacement and max von Mises. `fixed_face`/`load_face` = `{axis: x\|y\|z, side: min\|max}`. |
| `fem_modal` | **project, part_id**, n_modes, fixed_face, E_mpa, nu | Modal FEM: natural `frequencies_hz` (ascending) from a consistent-mass eigensolve; E and density default from the part material. `fixed_face` = `{axis: x\|y\|z, side: min\|max}`; omit it for free-free (rigid-body modes are dropped and noted). |
| `fem_thermal` | **project, part_id, hot_face, cold_face, t_hot_c, t_cold_c**, k_w_m_k | Thermal FEM: steady-state conduction with fixed temperatures on two faces; returns `t_min_c`/`t_max_c` and `flux_w` (total heat flow through the hot face, W). k defaults from the part material's `k_w_m_k`. |

Routes: `POST /api/projects/{proj}/parts/{id}/fem`, `.../fem/modal`,
`.../fem/thermal`.

## A worked loop

The canonical agent workflow — create, hit an error, read it, fix, verify,
export:

```
→ part_template {}
← {template: "...", cheatsheet: "AGENTCAD PART SCRIPT CONTRACT ..."}

→ create_project {"name": "bracket_study"}
→ create_part {"project": "bracket_study", "part_id": "bracket",
               "script": "<L-bracket with fillet radius 12>"}
← {"status": {"state": "error", ...},
   "metrics": null, ...}          # part was created; build failed

→ get_part {"project": "bracket_study", "part_id": "bracket"}
← status.error.message: "ValueError: Failed creating a fillet with radius
   of 12, try a smaller value..."  details.line: 14

→ update_part_script {"project": "bracket_study", "part_id": "bracket",
                      "script": "<same, fillet scaled to thickness/3>"}
← {"ok": true, "metrics": {"volume_mm3": 8412.6, "mass_g": 22.7,
   "is_valid": true, ...}, "warnings": []}

→ set_params {"project": "bracket_study", "part_id": "bracket",
              "values": {"thickness": 8}}
← {"ok": true, "metrics": {...}, "specs": null}   # kernel re-validates every
                                                 # change; no SPECS declared yet

# Write the intent down as code, so it is checked from now on (TDD for
# hardware: the spec comes first, the geometry converges onto it).
→ update_part_script {"project": "bracket_study", "part_id": "bracket",
                      "script": "<same script + from agentcad.toolkit.specs
                                 import check_mass, check_wall; SPECS = [
                                 check_wall(min_mm=2.5, requirement='ENG-014'),
                                 check_mass(max_g=120, requirement='SYS-042')]>"}
← {"ok": true, "metrics": {...},
   "specs": {"status": "red",
             "summary": {"passed": 1, "failed": 1, "skipped": 0,
                         "errors": 0, "total": 2},
             "checks": [{"name": "wall_min", "status": "fail",
                         "measured": 2.1, "limit": {"min_mm": 2.5},
                         "unit": "mm", "requirement": "ENG-014",
                         "location": [12.0, -8.0, 3.5],
                         "message": "min wall 2.1 mm is below the 2.5 mm minimum"},
                        {"name": "mass_max", "status": "pass",
                         "measured": 22.7, "limit": {"max_g": 120.0}}]}}

→ set_params {"project": "bracket_study", "part_id": "bracket",
              "values": {"thickness": 10}}
← {"ok": true, "metrics": {...},
   "specs": {"status": "green", "summary": {"passed": 2, "failed": 0, ...}}}

→ run_specs {"project": "bracket_study"}      # all three tiers, on demand
← {"status": "green", "summary": {"passed": 2, "failed": 0, "skipped": 0,
   "errors": 0, "total": 2},
   "requirements": {"ENG-014": {"status": "pass", "checks": ["bracket:wall_min"]},
                    "SYS-042": {"status": "pass", "checks": ["bracket:mass_max"]}}}

→ export_part {"project": "bracket_study", "part_id": "bracket",
               "format": "step"}
← {"path": ".../exports/bracket.step", "size_bytes": 48231}
```

Guidance that makes agents effective here:

- **Fetch `part_template` first.** The cheat-sheet encodes the contract and
  the common OCCT failure modes.
- **Trust the kernel, not your mental model.** After every mutation, read
  the returned metrics (volume, mass, validity) and sanity-check them
  against intent; use `check_interference` after assembly changes.
- **Errors are data.** `details.line` points into your script;
  `details.traceback` usually names the failing OCCT operation.
- **Write the budget down as a spec, not in prose.** A stated constraint
  ("under 120 g", "walls never below 2.5 mm", "0.5 mm to the chamber") belongs
  in `SPECS`, where every later rebuild re-checks it and the green `run_specs`
  report is the evidence you cite as done. A red spec is not an error to work
  around — it is the termination condition you have not reached yet.

## A v2 example: import a vendor part, measure it, mate it

Bring in a purchased bracket, sanity-check its wall thickness against the
kernel, and fasten it to a plate — no script authoring, just tools:

```
# 1. Upload the file (raw body; the tools can't read your local disk unless
#    you pass an absolute path). REST, since there is no upload tool:
→ POST /api/projects/rig/imports?filename=bracket.step   (STEP bytes as body)
← {"source": "bracket.step", "size_bytes": 412300}

→ import_cad_file {"project": "rig", "source": "bracket.step",
                   "part_id": "bracket", "material": "al6061"}
← {"part": {"kind": "reference", "source": "bracket.step",
            "metrics": {"n_solids": 1, "is_valid": true, ...}},
   "imported": {"n_solids": 1, "is_valid": true, "mesh_only": false,
                "warnings": []}}          # STEP → a real B-rep, boolean-capable

# 2. Measure a script part's thinnest wall against a requirement:
→ analyze_part {"project": "rig", "part_id": "housing",
                "kind": "wall", "min_required": 2.5}
← {"kind": "wall", "min_thickness_mm": 2.38, "location": [12.0, 4.0, 9.5],
   "ok": false, "min_required_mm": 2.5}   # too thin — thicken and re-run

# 3. Place instances, then constrain one with a mate (the anchor part's
#    script declared connectors(p, part) with a "hole1" revolute connector):
→ set_assembly {"project": "rig", "instances": [
     {"id": "plate1", "part": "plate"},
     {"id": "bracket1", "part": "bracket"}]}
→ set_mate {"project": "rig", "instance": "bracket1", "connector": "seat",
            "to_instance": "plate1", "to_connector": "hole1",
            "angle_deg": 30}
← {"instances": [..., {"id": "bracket1", "part": "bracket",
       "position": [...], "rotation_deg": [...],   # derived from the mate
       "mate": {"connector": "seat", "to_instance": "plate1",
                "to_connector": "hole1", "params": {"angle": 30.0}},
       "mass_g": 62.1, "state": "ok"}],
   "total_mass_g": 187.4}
```

Notes that keep this loop tight:

- **`import_cad_file` reports what survived.** `mesh_only: true` (STL) means
  the body measures and places but cannot take part in booleans or
  interference — `check_interference` will list it under `skipped_mesh`.
- **Analysis and drawings are script-part only.** Reference parts have no
  script to rebuild; call them on your parametric parts.
- **A mate is authoritative.** While `bracket1` is mate-driven, editing its
  transform directly (`PATCH …/assembly/instances/bracket1`) returns `409`;
  `clear_mate` first if you want to pose it by hand.

## A v4 example: branch, edit, merge

Try a risky change in isolation, then land it — the loop every agent should
use instead of editing the mainline in place:

```
→ branch_create {"project": "rig", "name": "flange-weld"}
← {"created": "flange-weld", "current": "master", "default": "master",
   "you": "mcp", "branches": [...]}      # created, NOT switched

→ branch_switch {"project": "rig", "name": "flange-weld"}
← {"branch": "flange-weld", "project": {...}}   # your client only

# Work normally: every existing tool now reads and writes this branch.
→ update_part_script {"project": "rig", "part_id": "flange", "script": "..."}
→ set_params {"project": "rig", "part_id": "flange", "values": {"bolt_d": 8}}

→ version_tag {"project": "rig", "name": "weld-study-a",
               "message": "welded flange, 8 mm bolts"}
← {"tag": "weld-study-a", "commit": "9f2c…", "versions": [...]}

# Land it. Meanwhile someone edited the same script on master:
→ merge_branch {"project": "rig", "source": "flange-weld", "target": "master"}
← {"error": {"type": "merge_conflict", "details": {
     "source": "flange-weld", "target": "master", "outstanding": 2,
     "conflicts": [
       {"kind": "script", "path": "parts/flange.py", "part": "flange",
        "ours": "<master's text>", "theirs": "<yours>", "base": "<...>",
        "merged": "<<<<<<< master … ||||||| base … ======= … >>>>>>> flange-weld"},
       {"kind": "manifest", "key": "parts.flange.params.bolt_d",
        "base": 6.0, "ours": 10.0, "theirs": 8.0}],
     "hint": "Resolve with resolve_merge …"}}}
   # nothing was applied; the merge is staged until you resolve or abort

→ resolve_merge {"project": "rig", "choices": {
     "parts/flange.py": {"content": "<the merge you authored, by hand>"},
     "parts.flange.params.bolt_d": {"value": 8.0}}}
← {"merged": true, "commit": "1a4b…", "parents": ["<master>", "<flange-weld>"],
   "conflicts_resolved": 2,
   "validation": {"ok": true, "built": [{"part": "flange", "cached": false}],
                  "failures": [], "integrity": [],
                  "interference": {"checked": 3, "new_pairs": [], "skipped": null}},
   "project": {...}}
```

What keeps this loop cheap and safe:

- **Branch first, always.** A branch costs one checkout of scripts and the
  manifest; `.cache/` is shared, so nothing rebuilds when you switch.
- **`ours` is the target.** In every conflict payload `ours` is the branch you
  are merging *into*. Getting this backwards silently discards someone's work.
- **A conflict is staged, not applied.** Re-read it any time with
  `merge_status`, resolve it in pieces, or `merge_abort` to walk away — the
  target branch never moves until the merge completes.
- **Read the validation report.** `ok: false` with `blocked: true` means the
  merge would break a build, strand an instance, or introduce interference.
  Fix the source branch and merge again; `allow_invalid: true` is for when you
  intend to land the failure (it is recorded in the commit message).

## A v4 example: propose the change instead of landing it

The loop above merges your own work. The **mandated end state of an agent
task** is one step short of that: branch → edit → *propose*, and let a human
(or a reviewer agent) decide. Same branch, same merge — with a reviewable
argument and machine-gathered evidence in between:

```
→ branch_create {"project": "rig", "name": "nozzle-thinner"}
→ branch_switch {"project": "rig", "name": "nozzle-thinner"}
→ update_part_script {"project": "rig", "part_id": "nozzle", "script": "..."}
→ set_params {"project": "rig", "part_id": "nozzle", "values": {"wall": 2.6}}

→ proposal_create {"project": "rig", "source": "nozzle-thinner",
                   "title": "Thin the nozzle wall to 2.6 mm",
                   "description": "3.0 mm was a placeholder; 2.6 keeps the
                                   hoop stress margin and saves 12 g."}
← {"proposal": {"id": "3", "state": "open", "target": "master",
                "author": "mcp", "author_kind": "agent"},
   "gates": [{"name": "approvals", "state": "fail",
              "summary": "1 approval required, 0 recorded …"}, …],
   "packet": null}                       # generated lazily, on first view

→ proposal_packet {"project": "rig", "id": "3"}
← {"ok": true, "stale": false, "elapsed_ms": 970, "generation": "6f1c…",
   "summary": {"parts_changed": 1, "mass_delta_g": -12.4},
   "parts": [{"part": "nozzle", "changed_by": ["script", "params"],
              "script_diff": {"unified": "@@ -12,6 +12,8 @@ …"},
              "params_diff": {"changed": [{"name": "wall", "field": "value",
                                           "old": 3.0, "new": 2.6}]},
              "metrics": {"mass_g": {"old": 111.3, "new": 98.9,
                                     "delta": -12.4, "pct": -11.1}, …},
              "geom_diff": {"available": true, "added_mm3": 0.0,
                            "removed_mm3": 4593.2,
                            "removed_mesh":
                                "/api/…/diff/6f1c…/nozzle/removed.acm"},
              "renders": {"view": "iso", "frame": {…},
                          "old": "/api/…/render/old/nozzle",
                          "new": "/api/…/render/new/nozzle"}}],
   "assembly": {"changed": false, …}, "warnings": [], "errors": []}

# A reviewer — human in the browser, or another agent holding these tools —
# looks at the evidence and rules on it:
→ proposal_render {"project": "rig", "id": "3", "side": "new",
                   "part": "nozzle"}     # the pair superimposes: same frame
→ proposal_review {"project": "rig", "id": "3", "verdict": "approve",
                   "summary": "margin checks out against the hoop-stress calc"}
← {"proposal": {"state": "approved"}, "gates": [{"name": "approvals",
                                                 "state": "pass"}, …]}

→ proposal_merge {"project": "rig", "id": "3"}
← {"merged": true, "commit": "5c31…", "parents": ["<master>", "<source>"],
   "validation": {"ok": true, …},
   "proposal": {"state": "merged", "merge": {"commit": "5c31…"}}}
```

What this buys, and the traps:

- **Write the description for the reviewer, not for the log.** The packet
  supplies *what* changed; you supply *why it is right*. That is the whole
  argument a reviewer judges.
- **Your own approval does not count.** Under the default policy a merge with
  zero non-author approvals is a `conflict_error` naming the gate — an agent
  cannot land its own work, which is the point.
- **Never `proposal_update {state: …}` to fake a decision.** Only `open` and
  `closed` are writable; approving and merging have their own tools.
- **Address feedback on the branch, then reopen.** `request_changes` blocks the
  merge; push new commits to the source branch (which marks the packet stale),
  then `proposal_update {state: "open"}` to re-request review.
- **A `merge_conflict` is the same object PRD-001 defines.** Resolve it with
  `resolve_merge` and call `proposal_merge` again — the proposal stays open
  until the merge actually lands. But `resolve_merge` *is* what lands it, and
  it knows nothing about proposals: so the proposal remembers the merge it
  staged and recognises it in the commit that appears, marking itself `merged`
  on the next read with the real commit, its real parents and **the
  `allow_invalid` the staged merge actually ran under** (with the `override`
  audit entry that goes with it). Calling `proposal_merge` again then reports
  that merge (`already_landed: true`) instead of merging an ancestor; a merge
  you discarded with `merge_abort` is audited `merge_discarded` and the
  proposal stays exactly where it was.
