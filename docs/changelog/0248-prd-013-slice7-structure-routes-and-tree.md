# 0248 — 2026-08-19 — PRD-013 slice 7: structure route pack + grouped tree rows

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

Seventh slice of Assembly v2: an HTTP surface for the structure tools and the
sidebar's grouped rows. `routes_structure.py` (a route pack, no `app.py` edit)
exposes `set_pattern` / `add_subassembly` / `set_assembly_interface` /
`export_urdf` over REST; `tree.js` renders a pattern as ONE row with a `×N`
badge (expandable to its members) and a sub-assembly as one read-only row naming
its source, driven by a pure, node-tested `tree_model.js`.

## Changes

- `agentcad/server/routes_structure.py` (**new** route pack): thin, whitelisted
  wrappers over the structure tools. Reuses `routes_configs`' `_result` /
  `_body_keys` / `_json`, so the refusal-raises / build-post-state-is-200
  discipline is one implementation. The `pattern` key is REQUIRED and forwarded
  verbatim (`null` clears — `_body_keys` would strip it). The name
  `routes_assembly2` is taken (the single-instance transform PATCH), so this
  pack owns the new structure verbs.
- `frontend/js/tree_model.js` (**new**, pure — no DOM/imports): `instanceRows`
  (raw instances → row descriptors; a pattern collapses to one `×N` row, a
  sub-assembly to one read-only row), `memberIdsOf` (a base id → its expanded
  members from the flattened view), `rowsHtml` (the node test's badge assertion).
- `frontend/js/tree.js`: `renderInstances` now groups via `tree_model` —
  a disclosure twist expands a pattern's / sub-assembly's members (from
  `state.assembly.instances`), a sub-assembly row carries a `sub` badge + an
  "open source project" affordance (`actions.loadProject`). Selecting a group
  highlights its first member. Leaf/member rows keep the existing click-select.
- `frontend/css/app.css`: `.row-group` / `.row-twist` / `.row-member` /
  `.row-open-src`.

## Files

- `agentcad/server/routes_structure.py` (new), `frontend/js/tree_model.js`
  (new), `frontend/js/tree.js`, `frontend/css/app.css`
- `tests/test_routes_structure.py` (new), `tests/test_frontend_tree.py` (new)

## Notes

- **Grouping reads the RAW instances** (`state.project.assembly.instances`,
  which carry `pattern`/`assembly` verbatim); member rows read the FLATTENED
  `get_assembly` view. The two never cross — the split that keeps the sidebar
  and the mass rollup from disagreeing.
- Over HTTP the app serializes an `AppError` as its class name
  (`"ValidationError"`), not the tool-layer `"validation_error"` — the route
  test asserts the class name (a bad pattern kind is a 422, an unknown instance
  a 404).
- Measured: `tests/test_routes_structure.py` 7 passed,
  `tests/test_frontend_tree.py` 5 passed. `node --check` clean on the two edited
  JS files.
