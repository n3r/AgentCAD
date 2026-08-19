# 0293 — PRD-028 review fixes: JSON-boundary hardening, the citation-lint hole, https-only links, a meaningful standalone `basis`, point-vs-table warning, two basis re-labels

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (orchestrated with Claude; Opus code review + Opus adversarial verifier)

## Summary
Two independent review passes over the finished branch (a design/plan review
and an adversarial "try to break it" pass) found no Critical defect and one
family of real ones — untrusted-input hardening at the JSON boundary, the
trap `CLAUDE.md` already carries a scar for — plus a hole in the lint gate,
an unvalidated `href`, a filter control that did nothing, and a few drifts.
All are closed here with a regression test each.

## Changes
- **JSON boundary** (verifier D1–D4): `routes_materials._parse_filter` refuses
  a `RecursionError` (a 10 000-deep `[[[…]]]` was a 500) and caps the query
  param at 64 KiB; `routes_configs._json` (shared by six routes) turns a
  `RecursionError` into a 422; `PUT /projects/{p}/materials` with a non-object
  body is a 422, not a 500; `materials_query.parse_constraints` refuses
  non-finite bounds (`NaN` was a silent no-op filter and then a 500 on echo);
  `MaterialLibrary._global_layer` degrades on `ValueError` too (a cp1252
  `~/.agentcad/materials.json` raised `UnicodeDecodeError` out of every build);
  `Property.at` refuses a non-finite temperature (NaN fell through to the last
  row with no clamp flag) and the three FEM tools check `temperature_c` /
  `t_hot_c` / `t_cold_c` at the door, before `_quietly` could swallow it.
- **Lint** (review #2, #5): the top-level `cost_usd_kg` shorthand needs a
  `source` like any property (`missing_citation`; it linted clean while
  `to_payload` reported it uncited); new **warning**
  `point_disagrees_with_table` when a point differs from its own table at
  `T_c` by > 2 % (23 shipped cards: the 20 normal-weight concretes' 1.8
  W/(m·K) design point against EN 1992-1-2's 1.95 upper curve, `copper_c101`,
  `ptfe`, `pvdf` — each explained in its notes; the shipped lint is now
  `0 errors, 45 warnings`).
- **Links** (review #3, verifier D5): `links[].url` must be `https://`
  (validated on write — a `javascript:` URL in a user-layer card can no longer
  reach an `href`), and `materials.js` refuses anything else again when
  rendering.
- **Query** (review #4, minor; verifier D7): a standalone `basis` means
  "carries at least one value on that basis" with those properties as the
  evidence (it matched all 434 before); `nearest_relaxation` names the only
  constraint when there is one; a `category`/`subcategory` argument that
  disagrees with the one inside `require` is a refusal, not a silent override.
- **FEM**: E resolving to ≤ 0 (EN 1993-1-2's curve ends at 0 at 1200 °C) is a
  refusal naming the temperature, never a singular stiffness.
- **Frontend** (review #1; verifier D6, D9): the tree reuses the catalog the
  workbench already holds (one `GET /api/materials` per open, not two);
  `refreshProject` re-fetches the ~0.5 MB catalog only when the project's
  materials map actually moved; a null subcategory renders as `unclassified`
  (as the docs said); library warnings toast once per open, not per
  keystroke; `escapeHtml` escapes quotes too.
- **Data** (review minor; orchestrator ruling): `stainless_316`'s
  205/515/40 % are exactly ASTM A240's S31600 minima, so their `basis` is now
  `minimum` with the A240 citation (values unchanged — the a36 precedent).
- **Loader**: a broken/missing shipped data file raises a `RuntimeError`
  naming the file (was a bare `JSONDecodeError`/`FileNotFoundError`).
- `to_payload` tests `Mapping`, not `dict`, for nested process values (the
  `_process_ok` lesson applied to its sibling).
- Docs: `docs/materials.md`, `AGENTS.md`, `docs/agent-api.md`,
  `PROVENANCE.md` (the 45 warnings) updated for every rule above.

## Files
- `agentcad/core/materials.py`, `materials_lint.py`, `materials_query.py`,
  `tools_analysis.py`, `server/routes_materials.py`, `server/routes_configs.py`
- `frontend/js/materials.js`, `frontend/js/main.js`
- `agentcad/core/materials_data/metal_stainless_ni_ti_cu.json` (basis/source
  text on `stainless_316`), `PROVENANCE.md`
- `tests/test_materials.py`, `test_materials_lint.py`, `test_materials_query.py`,
  `test_materials_tools.py`, `test_fem_material_resolution.py` — 12 new tests
- `docs/materials.md`, `docs/agent-api.md`, `AGENTS.md`

## Verification
- `.venv/bin/python -m pytest -q tests/test_fem_material_resolution.py
  tests/test_materials.py tests/test_materials_query.py
  tests/test_materials_tools.py tests/test_materials_lint.py
  tests/test_prd028_acceptance.py tests/test_frontend_materials.py` →
  181 passed, 4 skipped.
- `.venv/bin/agentcad materials lint agentcad/core/materials_data` → `0
  errors, 45 warnings` (22 `out_of_envelope` + 23 `point_disagrees_with_table`,
  all itemized in `PROVENANCE.md`).
- Full `make test` over the merged branch before these fixes: 4994 passed,
  48 skipped, 1 failed (`test_sketch_diagnostics::test_the_full_budget_
  completes_the_same_analysis`, a time-budget test that passed 2/3 in
  isolation at load average 29 while two reviewers ran pytest — unrelated to
  materials); the post-fix full run is cited in the close-out entry.

## Notes
Left for follow-up, recorded: each shipped card is normalized twice at import
(once by the lint, once by the loader — ~96 ms per server/CLI process; a
`lint_card` that returns the `Material` would halve it); `Property.at`
reports `interpolated: true` on a clamped read; no focus trap/restore on the
modal (house-wide); the browser has still not been painted in a real Chrome
(extension unavailable in this session) — the three-pane modal is
evidence-graded against the HTTP contracts and the node-executed `api.js` /
`materials_model.js`.
