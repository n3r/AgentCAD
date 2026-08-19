# 0272 — 2026-08-20 — PRD-015 slice 2: BOM exports + ref-pinned BOM via a tag-capable worktree

## Summary

Slice 2 of BOM & release management — CSV/JSON exports (FR4) and a
reproducible-at-a-ref BOM (FR5), on a new tag-capable worktree helper the release
bundle will reuse.

## Changes

- **`agentcad/core/_worktree.py`** (new, no OCP): `materialized_service(service,
  project, ref)` — a contextmanager that resolves a **branch or tag or commit**
  (`resolve_branch` → `resolve_tag` → commit, tags-before-branches like the
  checks resolver), `git worktree add --detach`s the tree into a unique
  `tempfile.mkdtemp` cell, drives a **muzzled** ephemeral `AgentCADService`
  (importing `checks._ephemeral_service` so the three non-negotiable nulls —
  `write_guard`/`branch_resolver`/`bus.on_publish` — have exactly one home), and
  tears the cell down in `finally` (`git worktree remove --force` + `prune` +
  `rmtree`, only ever the dir it created; overlap refused against the cell). The
  kernel is shared, not restarted.
- **`agentcad/core/bom.py`**: `build_bom` gains `generated_ref` provenance;
  pure `to_csv`/`to_json` renderers + `CSV_HEADER`.
- **`agentcad/core/tools_bom.py`**: `get_bom {ref}` computes the BOM inside
  `materialized_service` at the ref (else the working tree); new
  `export_bom {project, format: csv|json, config?, structure?, ref?}` → writes
  `exports/bom.<ext>` in the **real** project (not the throwaway tree).
- **`tests/test_bom_export.py`** (new, 9): CSV lossless + a `Bracket, "L" type`
  label round-trips through `csv.reader` (AC3), `None`→empty cell, JSON mirrors
  FR2 + byte-deterministic, bad format → validation_error, `get_bom {ref=tag}`
  reproduces the past (pattern qty 3-at-tag vs 7-live), a branch ref resolves, a
  bogus ref is a clean error, ref reads leave the project byte-identical + no
  worktree leak.

## Notes

The worktree helper is a **faithful sibling** (option b), not a lift:
`checks._resolve_ref`/`_materialized` are bound to a runner's `warnings`/`source`/
determinism state, so lifting them risked `test_checks_ref`'s exact-`source`
assertions; only the small add/teardown mechanics are re-expressed and
`checks.py` is byte-for-byte untouched (its **26 ref + 10 determinism + 92
checks/gate** tests confirm). CSV `cost_source` is its own column so a
`material_estimate` is never read as a quote (through the export too).

Limitation (carried to slice 5): at a ref the ephemeral cache is cold, so the
zero-kernel BOM reads mass `unbuilt` — `get_bom {ref}` reproduces the
manifest-derived BOM (qty, part numbers, manual costs, materials, configs)
faithfully; real per-tag **mass** needs a warm build, which the release bundle
does.

`make test` — **4577 passed, 38 skipped** (clean run; the full suite measured
4568 with the 9 self-referential count guards, green once this count lands;
suite grew 4565→4577 with slice 2's export/ref tests).
