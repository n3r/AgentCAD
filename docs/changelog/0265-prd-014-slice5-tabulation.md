# 0265 — 2026-08-19 — PRD-014 slice 5: configuration tabulation with letter variables

## Summary

Slice 5 (the last build slice) of Drawings v2 — FR10 config tabulation. A new
`tabulate: true` renders a configuration table with **render-time letter
variables** for a PRD-012 part: the drawn dims carry `A`, `B`, `C`… and a boxed
table lists each configuration's value for every letter plus its mass.

## Changes

- **`agentcad/kernel/handlers/drawing.py`** — kernel-side tabulation reusing the
  PRD-012 measurement path: `_measure_tabulate` calls `_measure_table` verbatim
  for the per-config overall X/Y/Z extents (resolved params, 8-row cap, the
  one-broken-member em-dash contract), `_tabulate_variables` assigns letters,
  `_config_table` draws the boxed table as typed primitives. The overall
  dimension lines and any matching PMI **diameter** callouts now carry their
  letter on the drawn views. `tabulate` is threaded through the display list and
  both backends; the result echoes `config_table`.
- **`agentcad/core/tools_drawing.py`** — `tabulate` arg + schema; builds the
  request block (per-config mass resolved service-side), scales the timeout by
  config count (`_ROW_TIMEOUT_S`, like `dim_table`), and surfaces `config_table`.
- **`tests/test_drawings_tabulate.py`** (new, 7): letter variables + a config
  table on a three-config part; the table maps every config with values + mass;
  the **active** config drives the drawn views; no-configs is a warning not an
  error; `tabulate` wins over `dim_table` when both are asked; a PMI diameter dim
  becomes a lettered variable; a tabulated sheet is byte-stable in SVG and PDF.

## Design notes

- **Letter rule** (stable, render-time): `A/B/C` = overall X/Y/Z extents (the
  guaranteed core), then one letter per PMI **diameter** dim in declaration order
  (`D, E, …`). PMI ids stay lowercase — the letters are a render layer.
- **`dim_table` vs `tabulate`** share the sheet's one table column, so
  **tabulate wins**: with both requested the letter-variable table is drawn,
  `dim_table` is dropped, and the drop is noted in `config_table.warnings`.
- **`config_table`** = `{variables: [{letter, source}], rows: [{config, label,
  ok, values, mass, error?}], active_config, warnings?}` — machine-readable.
- Deviation (documented): PMI **linear** dims are not separately lettered — their
  width/height/depth targets resolve to the overall X/Z/Y extents A/B/C already
  carry, so lettering them would print a redundant column. Diameter dims (which
  genuinely vary per config) are covered.

## Notes

Determinism reuses `_measure_table`, every value through `fmt()`, stable letter
order — the geometry-CI determinism stage stays green; OCP boundary clean. This
completes the buildable PRD-014 scope (FR1-2, 6-13); FR3 (revision block) and
FR4/FR5 (assembly balloons + BOM) remain deferred to PRD-015.

`make test` — **4503 passed, 30 skipped** (clean run; the full suite measured
4494 with the 9 `*_cites_a_make_test_count` guards reading this entry's own count
before it was filled — green once it lands; suite grew 4496→4503 with slice 5).
