# 0031 — Add imported STL reference part (11.stl) to the construction example

- **Commit:** 922ec7c
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Adds an imported-mesh reference part to the `construction` example so the
example set exercises the `kind: "reference"` path (imported STL with no
build script) end to end.

## Changes
- Adds `examples/construction/imports/11.stl` (~2.3 MB binary) — the imported
  mesh source geometry.
- Appends a new part entry to the `parts` array in
  `examples/construction/project.json`: `id`/`label` `ref_11`, `material`
  `al6061`, empty `params`, `kind: "reference"`, and `source: "11.stl"`. This
  is the first part in the example carrying `kind: "reference"` and a `source`
  file rather than a parametric script.

## Files
- `examples/construction/imports/11.stl` — new imported STL geometry
- `examples/construction/project.json` — added the `ref_11` reference part
  (`kind: "reference"`, `source: "11.stl"`, no params)

## Notes
Reference parts have no script and no `PARAMS`, so the example tests skip their
`is_valid`/`params_spec` assertions and param sweeps (see the guard added in
0030's `tests/test_examples.py` change). This makes the construction example a
live fixture for the imported-mesh shading/reference-loading paths.
