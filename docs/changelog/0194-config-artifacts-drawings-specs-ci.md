# 0194 — PRD-012 slice 6: per-configuration drawings, spec results and CI rows

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude

## Summary
Design Decision 8's remaining three artifacts. `generate_drawing` grows
`config` (a sheet per member, `<part>_<config>_drawing.<ext>`) and `dim_table`
(a boxed table of the whole family, every number **measured in the handler**
from that member's own built shape). `SpecRunner._shape_tier` takes a
keyword-only `record=`, so `build_configs` can fill `spec_results` per
configuration without the sidecar key and the measured params ever coming from
different records. The CI build stage emits one extra `build` row per
configuration, subject `part@config`, with no new stage and no new item kind.

## Changes
- **`tools_drawing.generate_drawing(…, config=None, dim_table=False)`.**
  `service._record_for(project, part_id, config)` is the one validator (a
  reference part, a non-string name and an undeclared name all refuse there),
  and the derived record's `effective_params` is the pure configuration map the
  kernel receives — the worker still never learns the word. The output path
  gains `_<config>` before `_drawing`; the result echoes `config` and, when the
  handler drew one, `dim_table` (the same object as `detected["dim_table"]`).
  With `dim_table` and a configured part the request carries
  `{rows: [{config, label, params}] in family order, columns: [configured
  params, union, first-seen]}` and the timeout scales `120 + 60·rows`, because
  every row is one more `build_shape` inside the same call. `dim_table: true`
  on a part with **no** configurations is a question, not an error: no table
  reaches the request and the sheet is byte-identical to a plain call.
- **`kernel/handlers/drawing.py`.** New `_measure_table(build_shape, script,
  table)` builds each row and records `values = {**params, X, Y, Z}` from the
  **world** bounding box (`shape.bounding_box().size`) — the same quantity the
  front/top overall dimensions print, which are that box projected. A row that
  will not build is `ok: false` with its error and prints em dashes; beyond
  eight rows the rest are dropped with a warning (nine rows plus a header is
  45 mm in a 42 mm rectangle). New `_dim_table(rows, columns, x=264.0,
  y_top=18.0, row_h=4.5)` renders the header (`config`, the columns, `X`, `Y`,
  `Z`) and one boxed cell per value (`<rect … {_BOX}/>` + `_text`), column
  width `max(14, 2.2·len + 4)` (`_fcf_frame`'s rule), dropping **trailing
  parameter** columns with a warning until the table fits the sheet's 150 mm
  right column — `config` and the measured extents are never dropped.
  **Every string goes through `_esc`**: a label is author-supplied and one `&`
  would make the whole sheet unparseable. `_build_svg` gains `dim_table=None`
  and echoes `detected["dim_table"] = {columns, rows, placement:
  "right-column", warnings}`. `drawing()` measures only for SVG, so DXF ignores
  the table exactly as it ignores PMI (v1).
- **`server/routes_drawing.py`.** The POST forwards `config` and `dim_table`
  from the body; the SVG GET takes `?config=` and reads the **suffixed** file,
  so it serves that configuration's sheet rather than whatever the base call
  last wrote.
- **`specs.SpecRunner._shape_tier(…, *, record=None)`.** `record or
  store.get_part(...)`, and everything downstream already flows from `record`:
  the sidecar key is `_cache_key_for(proj, record)` and the `spec_eval` params
  are `record.effective_params`. Both halves of the identity move together on
  purpose — a `record=` that keyed the sidecar but not the measurement would
  write the base numbers into a config-keyed file and every key assertion would
  still pass.
- **`tools_configs`: `spec_results` per matrix row.** `_rows` reads
  `getattr(service, "specs", None)` at CALL time (the pack sorts at `con`,
  before `specs`) and, when `declares_specs(script)`, adds
  `row["spec_results"] = {"checks": …, "cached": …}` from
  `specs._shape_tier(project, pid, record=derived)`. A `KernelError` becomes
  `{"error": payload}` — data, like a failed build. Shape tier only: an
  assembly is not per configuration. The script is read once per part, not once
  per member.
- **`checks._stage_build` + `_config_item` + `_config_cache`.** The stage now
  walks `[(part_id, None), *((f"{part}@{name}", name) for name in
  entry.get("configs") or {})]`, so an unconfigured project produces exactly
  the rows it produced before. `_cannot_afford` is checked before **every** row
  (a budget that dies mid-family names the members it never reached rather than
  dropping them), and what it stops is a `skip`/`budget_exceeded`, never a red.
  `_config_item` mirrors `_build_item`'s branches through
  `service._ensure_config_built` and carries `details = {config, cache_key,
  cached, volume_mm3, mass_g, n_solids, is_valid}`; `_config_cache` is
  `_is_cached`'s question asked of a derived record, returning the key as well
  so a **failed** build's row can still name the key its parameters hash to.
  `STAGES` and `ITEM_KINDS` are untouched.

## Files
- `agentcad/core/tools_drawing.py` — `config`/`dim_table`, the suffixed output
  path, the family-order table request, the scaled timeout, the schema.
- `agentcad/kernel/handlers/drawing.py` — `_measure_table`, `_dim_table`,
  `_cell`, `_build_svg(dim_table=)`, the `drawing()` wiring, module docstring.
- `agentcad/server/routes_drawing.py` — `config`/`dim_table` on the POST,
  `?config=` and the suffixed read on the SVG GET.
- `agentcad/core/specs.py` — `_shape_tier(…, *, record=None)` and its docstring
  (this slice touched nothing else in the file).
- `agentcad/core/tools_configs.py` — `_spec_results`, the `_rows` hook, the
  `KernelError`/`declares_specs` imports, the `build_configs` description.
- `agentcad/core/checks.py` — `_stage_build`'s inner loop, `_config_item`,
  `_config_cache`.
- `docs/geometry-ci.md` — the `build` stage row now documents `part@config`.
- `tests/test_configs_drawing.py` (new) — 11 tests: the suffixed file and L's
  measured geometry, the refusal, the three-row table (family order, labels
  once each, X/Y/Z from the built shapes), an `&` in a label parsed back with
  `ElementTree`, the unconfigured part's byte-identical sheet, DXF ignoring the
  table (compared by entity list — ezdxf stamps a fresh timestamp and GUIDs, so
  DXF bytes are not comparable), both routes, and three renderer unit tests
  (truncation past 8, the em-dash row, the trailing-column drop) driven with a
  fake `build_shape`.
- `tests/test_configs_checks.py` (new) — 7 tests: the four `build` rows and
  their details, the warm run, a member that fails, the budget floor before
  every row, a budget that dies mid-family, a blown budget's exit 2, and
  row-set equality for a configuration-free project.
- `tests/test_configs.py` — `TestConfigSpecResults` (4 tests): every row's
  shape tier, the config-keyed sidecar that cannot hold the base measurement,
  the second read as a sidecar hit, and no `spec_results` key at all for a
  script that declares nothing.

## Fix round 1 (review: spec PASS, quality PASS — 2 Important, 7 minor)
- **I1 — the table's cells now echo the RESOLVED parameter map**
  (`build_shape`'s second return value), not the request's override map. A
  family is routinely ragged, and echoing overrides printed an em dash wherever
  a member did not override a column while the geometry beside it had the
  script's default (and printed enums un-canonicalized). The renderer prints
  only the requested `columns`, so the extra resolved keys are inert in the SVG
  and a bonus in `detected`. Covered by a ragged fourth member (`xl` overrides
  only `thick`) in `test_the_dimension_table_measures_every_configuration`;
  reverting the one line fails it with `KeyError: 'outer_d'`.
- **I2 — `if dim_table and declared and format == "svg"`.** A DXF request used
  to carry the table payload and inflate its timeout by 60 s per configuration
  for a path that discards it. `test_a_dxf_request_carries_no_table_and_keeps_
  the_flat_timeout` captures the kernel call's params and `timeout_s`;
  reverting the guard fails it.
- **Route** — `GET …/drawing.svg` gains `dim_table: bool = False` (FastAPI
  parses `?dim_table=1` and `?dim_table=true`), so the browser preview can ask
  for the tabulated sheet without a POST.
- **M1** — the `config` cell prints the NAME, with the label beside it when one
  exists and differs: `Small (s)`. The name is the identity every other surface
  uses (manifest key, `part@config`, `?config=`), and a sheet reading only
  `Small` could not be traced back to it. New `_row_label`.
- **M2** — `cut = columns.pop()` on its own line before the message is built.
- **M3** — `str(entry.get("label") or name)`: a non-string label from a hand
  edit or a merge can no longer `TypeError` the whole sheet inside `_esc`.
- **M4** — `_config_item`'s pass message reuses `_build_item`'s conditional
  `is_valid` clause verbatim instead of hardcoding `", valid"`.
- **M5** — `if declares and row["ok"]:`; a member whose build just failed is
  not measured again (`spec_eval` would fail for the same reason, at the same
  cost, to restate the error the row already carries). Covered by a
  SPECS-declaring fragile family in `TestConfigSpecResults`.
- **M6** — `detected.dim_table.columns` stays the list that was *requested*;
  `dropped` names what did not fit beside it, so a caller comparing the echo to
  its own request does not have to diff two lists.
- **M7** — the harness `report.errors[]` entry carries `config` beside `part`.
  New `test_a_harness_failure_is_one_error_row_naming_the_configuration`.
- Re-run: `uv run pytest tests/test_configs_drawing.py
  tests/test_configs_checks.py tests/test_configs.py tests/test_drawings.py
  tests/test_checks_pipeline.py -q` — **122 passed** in 261 s; plus
  `tests/test_drawing_holes.py tests/test_pmi.py tests/test_checks.py
  tests/test_checks_api.py tests/test_specs.py tests/test_configs_api.py
  tests/test_mcp.py -q` — **223 passed, 2 skipped**.

## Notes
- **The table is a measurement, not a printout.** Every number in it comes from
  `build_shape` inside the handler; a table of the parameters the caller
  already had would assert nothing, and that is the module's standing contract.
  It is also why the timeout scales with the row count.
- **`m`'s key is not the base key**, even though `THREE_SIZE_CONFIGS["m"]`
  *is* the script's defaults: the service hashes the override map (`{}` versus
  an explicit map) while the worker resolves defaults. Making the two agree
  would move every pre-PRD-012 cache key (Decision 3), so the CI test asserts
  four distinct keys and says why.
- **DXF is not byte-stable** (ezdxf writes a fresh timestamp and GUIDs), so the
  "DXF ignores the table" test compares the modelspace entity list. SVG *is*
  byte-stable, which is what makes the unconfigured-part control a real byte
  comparison.
- The imported-geometry escape in `_build_item` (`is_valid: false` reported,
  never enforced) is deliberately **absent** from `_config_item`:
  `_record_for` refuses a reference part outright, so the branch would be dead
  code that reads like a policy.
- `render_view {config?}` moved to slice 4 and is not in this diff.
  `docs/agent-api.md` is slice 8's.
- Test counts below are round 0's; fix round 1's re-run is in its own section
  above (the two new drawing tests, one spec-results test and one checks test
  take `test_configs_drawing.py` to 13, `test_configs.py`'s
  `TestConfigSpecResults` to 5 and `test_configs_checks.py` to 8).
- Focused suite: `uv run pytest tests/test_configs_drawing.py
  tests/test_configs_checks.py tests/test_configs.py tests/test_drawings.py
  tests/test_drawing_holes.py tests/test_checks.py
  tests/test_checks_pipeline.py tests/test_specs.py -q` — **257 passed, 2
  skipped**; plus `tests/test_mcp.py tests/test_tools.py tests/test_pmi.py
  tests/test_checks_api.py tests/test_checks_cli.py tests/test_checks_gate.py
  tests/test_specs_api.py tests/test_specs_gate.py tests/test_configs_api.py
  tests/test_prd011_acceptance.py -q` — **231 passed**; plus the slow
  regression band `tests/test_examples_golden.py tests/test_examples.py
  tests/test_checks_ref.py tests/test_geometry_ci_action.py -q` — **170
  passed** (the examples declare no configurations, so the build stage's row
  set there is unchanged).
- Full suite: `make test` — 3472 passed, 7 skipped (run by the controller over slices 4 and 6 together, with slice 7's frontend edits already in the tree; the one red row was `tests/test_presence.py::test_the_browser_mints_and_sends_a_per_profile_identity`, the hand-rolled-fetch `X-Agent-Id` count pin that slice 7's `getMeshByKey` bumps from 5 to 6 — slice 7 updates the pin).
