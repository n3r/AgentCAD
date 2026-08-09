# Agent API Reference

Agents drive AgentCAD through a single tool surface — 25 tools, assembled
once in `agentcad/core/tools.py` (the 17 core tools) plus the v2 feature
packs in `agentcad/core/tools_*.py` — and exposed two ways:

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

## Tools

Required arguments are **bold**; the rest are optional. Discover the live set
and exact JSON Schemas at runtime with `GET /api/tools` — that is the source
of truth, and it omits `fem_static` unless the `[fem]` extra is installed.

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
| `set_params` | **project, part_id, values** | Rebuild result. Values merge with existing overrides and clamp to min/max with warnings; unknown names are rejected before anything is written, and a `null` value removes an override. |
| `delete_part` | **project, part_id** | `{deleted}` — fails with a conflict while assembly instances reference the part. |

### Metrics, mesh, and export

| Tool | Arguments | Returns |
|---|---|---|
| `get_metrics` | **project, part_id** | `{volume_mm3, mass_g, area_mm2, bbox, center_of_mass, is_valid, n_faces, n_edges, n_solids}` |
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

### Materials

| Tool | Arguments | Returns |
|---|---|---|
| `list_materials` | project | Resolved catalog (builtin < `~/.agentcad/materials.json` < project overrides): `{materials: [{id, label, category, density_g_cm3, source, E_gpa?, yield_mpa?, ultimate_mpa?, elongation_pct?, cte_um_m_k?, k_w_m_k?, max_service_temp_c?, cost_usd_kg?, notes?}], caveat, global_error}`. Values are typical datasheet figures, **not design allowables**. |
| `set_project_materials` | **project, materials** | Replaces the project's `materials` section (a map `id → {density_g_cm3 required, …, category, notes}`); validates every entry, then returns the resolved list. |

### Import (reference parts)

| Tool | Arguments | Returns |
|---|---|---|
| `import_cad_file` | **project, source, part_id**, label, material | Imports `.step`/`.stp`/`.brep`/`.stl` as a *reference* part (no script; placeable in assemblies, and — STEP/BREP only — usable in booleans). `source` is an absolute path to ingest, or the basename of a file already uploaded via `POST /api/projects/{proj}/imports`. Returns `{part: <detail>, imported: {source, n_solids, is_valid, mesh_only, warnings}}`. |

### Drawings and analysis

| Tool | Arguments | Returns |
|---|---|---|
| `generate_drawing` | **project, part_id**, views, format | Projected front/top/right/iso views with overall dimensions and hole callouts detected from the geometry. `views` is a subset of `[top, front, right, iso]` (default all); `format` is `svg` (default) or `dxf`. Writes `exports/<part_id>_drawing.<ext>` and returns `{path, size_bytes, detected: {diameters_mm, hole_groups, label}}`. Script parts only. |
| `analyze_part` | **project, part_id, kind**, plane, axis, min_required | `kind=section` (cross-section area on `plane` XY\|XZ\|YZ), `wall` (min wall thickness; with `min_required` it adds an `ok` flag), `inertia` (mass-properties tensor + centre of mass), `projected_area` (silhouette area along `axis` X\|Y\|Z). Script parts only. |

### Sketch solving

| Tool | Arguments | Returns |
|---|---|---|
| `solve_sketch` | **entities, constraints** | Solve a 2D constrained sketch to exact coordinates you can feed into a build123d `BuildLine`/`BuildSketch`. `entities = {points:[{name,x,y,fixed?}], lines:[{name,p1,p2}], circles:[{name,center,r,fixed_r?}]}`; `constraints = [{type, …}]`. Returns `{ok, points, circles, dof, max_residual, …}`; a sketch that does not converge comes back as a validation error (the solver homes to the *nearest* solution, so a mirrored initial guess yields a mirrored result). |

Constraint types: `fixed, coincident, distance, distance_x, distance_y,
horizontal, vertical, parallel, perpendicular, angle, point_on_line,
point_on_circle, radius, equal_radius, midpoint, tangent_line_circle,
tangent_circles`.

### FEM — present only with the `[fem]` extra

`fem_static` is registered **only** when `agentcad[fem]` is installed, so it
never appears in `GET /api/tools` (or to agents) otherwise — the philosophy is
that agents must not see a tool that cannot run.

| Tool | Arguments | Returns |
|---|---|---|
| `fem_static` | **project, part_id, fixed_face, load_face**, load_N, load_dir, E_mpa, nu, mesh_size_mm | Linear-static FEM: clamp one axis-aligned face, load another, return max displacement and max von Mises. `fixed_face`/`load_face` = `{axis: x\|y\|z, side: min\|max}`. |

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
