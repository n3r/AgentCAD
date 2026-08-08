# Agent API Reference

Agents drive AgentCAD through a single tool surface defined once in
`agentcad/core/tools.py` and exposed two ways:

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

| Tool | Arguments (required bold) | Returns |
|---|---|---|
| `part_template` | — | The part-script contract, a starter template, and a build123d cheat-sheet. Call this before writing your first script. |
| `list_projects` | — | `{projects: [{name, path, n_parts}]}` |
| `create_project` | **name** | Project detail. Names match `[a-z][a-z0-9_]{0,39}`. |
| `open_project` | **path** | Opens an existing project directory (e.g. a bundled example) by absolute path. |
| `get_project` | **project** | Manifest: parts with build state, assembly instances, material table. |
| `create_part` | **project, part_id**, label, script, material | Part detail with metrics (default template if no script). |
| `get_part` | **project, part_id** | Script, `params_spec`, current params, status (state/error/warnings), metrics. |
| `update_part_script` | **project, part_id**, script, label, material | Rebuild result. On failure: traceback + failing line + hint; previous geometry kept. |
| `set_params` | **project, part_id, values** | Rebuild result. Values merge with existing overrides and clamp to min/max with warnings; unknown names are rejected before anything is written, and a `null` value removes an override. |
| `delete_part` | **project, part_id** | `{deleted}` — fails with a conflict while assembly instances reference the part. |
| `get_metrics` | **project, part_id** | `{volume_mm3, mass_g, area_mm2, bbox, center_of_mass, is_valid, n_faces, n_edges, n_solids}` |
| `get_mesh_summary` | **project, part_id** | `{vertices, triangles, edges, bbox}` — statistics only, no binary buffer. |
| `export_part` | **project, part_id, format**, tolerance | Writes `exports/<part_id>.<format>`; formats `step`, `stl`, `3mf`. |
| `get_assembly` | **project** | Instances with per-instance mass/state plus rolled-up `total_mass_g` and world bbox. |
| `set_assembly` | **project, instances** | Full replacement list: `{id, part, position [x,y,z], rotation_deg [rx,ry,rz], color?}`. |
| `check_interference` | **project**, min_volume | Boolean-intersects every instance pair; `{pairs: [{a, b, volume_mm3}]}` above the threshold. |
| `export_assembly` | **project, format** | Whole placed assembly as `step` or `stl`. |

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
