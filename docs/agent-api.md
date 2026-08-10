# Agent API Reference

Agents drive AgentCAD through a single tool surface — 52 tools (55 with the
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
- Rebuild results have `{"ok": true, "metrics", "warnings"}` or
  `{"ok": false, "error", "hint"}`. On failure the previous good geometry is
  kept.
- Units: mm, grams, degrees. Instance rotations are intrinsic XYZ Euler.
- One error type is **returned rather than raised**: `merge_conflict`
  (`merge_branch` / `resolve_merge`). It arrives as an ordinary
  `{"error": {"type": "merge_conflict", "details": {"conflicts": […]}}}`
  payload — over REST at HTTP **200**, not 409 — because a conflict is a
  workflow state to render, not a failure. Everything else keeps the usual
  `validation_error` / `notfound_error` / `conflict_error` mapping.

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
| `get_part` | **project, part_id** | Script, `params_spec`, current params, status (state/error/warnings), metrics, plus `kind` (`script`\|`reference`) and `source`. For reference parts `script`/`params_spec` are `null` and `source` is the imported file. |
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
| `project_history` | **project**, limit, ref | List the project's automatic history snapshots, newest first (`{id, message, ts}`); entry [0] is the current state. History is **per branch**: you see your own branch's unless you pass `ref` — a branch or tag name — which reads that ref's history without switching you. `available: false` + empty list when git is missing on the server. |
| `project_restore` | **project, commit** | Restore the project to a snapshot id **or a branch/tag name** (`{commit: "shop-rev-a"}` restores a version) and append a linear "restore" commit on your current branch. Returns refreshed history + `{restored}`; validation_error on unknown commit/no git, conflict_error under someone else's turn lock. A manual restore is itself one undoable step. |
| `undo` | **project** | Undo the last mutation (any client's) by stepping back through the git history: `{undone, history: {available, undo, redo}}`. conflict_error when nothing to undo; after a server restart one step remains available. |
| `redo` | **project** | Redo the most recently undone mutation. The redo stack clears when any new mutation happens. |
| `get_history` | **project** | Undoable/redoable action labels, newest first, plus `available` (false when git is missing). The full durable snapshot log with commit ids is `project_history`. |
| `render_view` | **project**, part_id, view, width, height | Server-side shaded orthographic render of built geometry so the agent can *see* the shape. `part_id` renders one part; omit it to render the whole placed assembly (instance transforms and colors honored; unbuildable instances are listed in `skipped`). `view` is `iso` (default), `front`, `top` or `right`; `width`/`height` are 64..2048 px (default 800×600). Writes `exports/renders/<part|assembly>_<view>.png` and returns `{path, width, height, view, png_base64}`; over MCP and in chat the PNG arrives as actual image content. |
| `analyze_part` | **project, part_id, kind**, plane, axis, min_required | `kind=section` (cross-section area on `plane` XY\|XZ\|YZ), `wall` (min wall thickness; with `min_required` it adds an `ok` flag), `inertia` (mass-properties tensor + centre of mass), `projected_area` (silhouette area along `axis` X\|Y\|Z), `curvature` (per-face gaussian K in 1/mm² and mean H in 1/mm sampled on an 8×8 UV grid: `faces[]` with min/max/mean per face, `worst_gaussian_abs`, `n_faces`, `sampled_points`; H's sign is orientation-dependent — compare magnitudes; a true G2 blend shows no jump in K/H across the seam). Script parts only. |

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
| `resolve_merge` | **project, choices** | Resolves the staged merge. `choices` maps a conflict's `path` (scripts, e.g. `"parts/flange.py"`) or `key` (manifest, e.g. `"parts.flange.params.bolt_d"`) to `{"take": "ours"\|"theirs"\|"base"}`, or `{"content": "<full file text>"}` for a script, or `{"value": …}` for a manifest key. In a manifest conflict a side that has **no value** (it deleted the key, or both branches added it so there is no base) is **omitted** from the payload — `"ours": null` means that side authored a JSON `null`, and taking it writes that null rather than deleting the key. Each manifest conflict also carries `path`, the exact key segments, because an id may contain a `.` (`parts.body.solid_materials.wall.inner`); `key` remains the dotted string you address the choice by. A `kind: "binary"` conflict (anything under `imports/`) carries `sides: {base\|ours\|theirs: {bytes, sha256} \| null}` instead of text and takes a side only — `content` is a `validation_error`. Taking a side where the file is absent (that branch deleted it) **deletes** it; taking `base` when there is none (both branches added the file) is a `validation_error` naming the valid choices. Partial resolution is fine — the reply lists what is still outstanding — and the merge completes (validation pass included) as soon as nothing is. Unknown path/key → `validation_error`, staged merge untouched. |
| `merge_abort` | **project** | Discards the staged merge (its worktree and state); no branch moves. `{aborted: false}` when nothing was staged. |
| `merge_status` | **project** | `{merge: {id, source, target, base, by, created, outstanding, conflicts, resolved} \| null}` — re-enter a merge you or another client staged earlier (e.g. after a reload or a server restart). |

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
← {"ok": true, "metrics": {...}}   # kernel re-validates every change

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
