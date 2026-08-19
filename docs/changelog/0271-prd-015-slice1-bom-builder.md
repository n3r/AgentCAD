# 0271 — 2026-08-19 — PRD-015 slice 1: the zero-kernel BOM builder

## Summary

Slice 1 of BOM & release management — a structured BOM derived from the model,
making **zero kernel calls** (FR1-3). Roll-ups across PRD-013 patterns and
sub-assemblies, per-config identity, cost with honesty flags, and part-number/
source inputs — the foundation the release engine and PRD-014 assembly balloons
both consume.

## Changes

- **`agentcad/core/bom.py`** (new, pure Python, no OCP, zero kernel calls):
  - `count_leaves(service, proj)` — a **count-only** structural walk of
    `manifest["assembly"]["instances"]` that never composes a transform (so it
    never triggers the `resolve_assembly` kernel call `mates.expand` needs): a
    pattern contributes `int(pattern["count"])`, a sub-assembly recurses into the
    source project's instances multiplying multiplicity through (carrying
    `origin_project` so a screw in project B counts as B's part), cross-project
    cycles guarded by a canonical-path stack (→ `ValidationError` w/
    `details.cycle`).
  - `build_bom(service, proj, structure, config?)` → `{lines, totals, warnings,
    structure}`. `flat` groups by `(origin_project, part_id, config)` and sums
    `qty`; `indented` carries `level`. Totals are always summed over the flat
    grouping, so flat and indented are byte-identical (float add isn't
    associative). Line fields (FR2): `item` (stable ordinal), `origin_project`,
    `part_id`, `part_number`, `label`, `config`, `material`, `unit_mass_g`,
    `unit_cost_usd`, `ext_cost_usd`, `qty`, `source`, `cost_source`
    (`manual|material_estimate|none`), `mass_source` (`built|stale|unbuilt`).
  - Mass reads `service._status`/`_config_status` **directly** (like
    `get_project`), never `_ensure_built`/`get_metrics` — staleness via the pure
    `_cache_key_for` hash; `unbuilt`/`stale` warn (naming the part) and never
    rebuild. Cost (FR3): manual wins, else `unit_mass_g × cost_usd_kg / 1000`
    (`material_estimate`), else `none`. Package parts inherit `part_number`/`url`
    from the `# agentcad:package` provenance header → `package.json`'s
    `provenance.vendor` (best-effort, degrades to blank).
- **`agentcad/core/tools_bom.py`** (new): `get_bom {project, config?, structure?,
  ref?}` (`ref` accepted, wired in slice 2) and `set_bom_fields {project,
  part_id, part_number?, unit_cost_usd?, supplier?, url?, config?}` (bounded/
  control-char-free strings, non-negative cost, unknown keys refused; writes
  `parts[i]["bom"]`, publishes `project_changed`).
- **`agentcad/core/manifest_merge.py`**: `"bom"` → `_PART_SUBDICTS` — the per-part
  `bom` field merges per-field like `params` (two branches editing different BOM
  fields of one part merge clean).
- **`tests/test_bom.py`** (new, 18): pattern + sub-assembly roll-ups (AC2, screw
  `qty: 16`), flat==indented totals, per-config lines (AC7), the three cost
  branches, unbuilt→warning-no-rebuild, stale-on-script-change, `set_bom_fields`
  validation + per-field merge, zero-kernel, cycle detection.

## Notes

Verified: 18 BOM tests + 50 merge tests (the `_PART_SUBDICTS` change is safe) +
129 broad `-k "bom or manifest_merge"`; `build_bom` makes zero `kernel.request`
calls; `import agentcad.core.bom` pulls no OCP/build123d. Line dict adds
`origin_project` beyond FR2's list so cross-project/sub-assembly BOMs are
unambiguous. Metrics cache is process-lifetime, so a part unbuilt this process
reads `unbuilt` (a documented warning, not a rebuild).

`make test` — **4565 passed, 38 skipped** (green total; a contended 24-min run
measured 4556 passed with the 9 self-referential count guards plus 3 timing
flakes — a sketch drag-budget test and the two `test_supervisor.py` RSS-killer
tests — all of which pass in isolation, `3 passed`, and none of which this
slice's pure-Python `core/bom.py` touches).
