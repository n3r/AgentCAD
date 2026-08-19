# 0292 — PRD-028 slice 6: acceptance tests + docs

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
Closes out PRD-028 with one acceptance module grading AC1–AC7 against the
shipped surface, a new `docs/materials.md` reference (schema, taxonomy,
resolution, versioning/immutability, the query grammar, FEM resolution, the
lint, sourcing/CONTRIBUTING rules, and the three deferrals), the materials
gotchas sections in `AGENTS.md`/`CLAUDE.md`, and a verification pass over
`docs/agent-api.md`'s existing Materials/FEM rows (already accurate — slices
2/3 wrote them; no changes needed).

## Changes
- **`tests/test_prd028_acceptance.py`** (new) — 21 tests (19 run, 2
  `importorskip`-skipped without the `[fem]` extra) grading AC1–AC7:
  - AC1: the 30 legacy ids/densities (imported from `test_materials.py`'s
    pinned `LEGACY_DENSITIES` rather than re-derived), the flat `to_payload()`
    keys, and a cache-key invariance proof — `service._cache_key`'s signature
    carries no materials-shaped parameter, two services in different
    materials-resolver states (`_DefaultMaterialResolver` vs
    `ProjectMaterialResolver`) mint the identical mesh cache key for the same
    part, and an independently recomputed key matches the one a real build
    minted.
  - AC2: the PRD's exact `find_materials` query — every row's constraining
    evidence clears its bound and cites a source — plus `prefer:
    {cost_usd_kg: "min"}` ordering the costed subset non-decreasing, plus a
    thin tool-wiring check through `registry.call`.
  - AC3: a fake-kernel fixture (`fem_available` patched true before
    `build_registry`, `service.kernel` swapped for a recorder — the
    `test_fem_material_resolution.py` pattern) proving `fem_thermal`
    interpolates a synthetic `k(T)` table inside its span and clamps with a
    `temperature_out_of_table_range:`-prefixed warning outside it, plus an
    `importorskip`-guarded real-`skfem`-solver variant.
  - AC4: `len(MATERIALS) >= 300`, every builtin property cited, all 30
    taxonomy leaves populated (>= 5 on the 13 example-touched leaves), and
    `materials_data/PROVENANCE.md` exists and attests (loose check: file
    exists + contains "Prospector" — a QA agent owns that file's prose in the
    concurrent 0291 slice).
  - AC5: `materials_lint.lint_card` and the real CLI subprocess
    (`[sys.executable, "-c", "from agentcad.cli import main; main()"]`, the
    `test_materials_lint.py` pattern) both reject a card whose `yield_mpa` has
    no `source`, naming the property, exit code 1.
  - AC6: the construction example copied (never the source tree), `base_plate`
    re-materialled to `c24` (EN 338 timber) then `concrete_c30_37`
    (EN 1992-1-1 concrete) via `service.update_part` — mass tracks `volume_mm3
    x density_g_cm3 / 1000` within 5% for each, and a fake-kernel `fem_static`
    call proves the solver received exactly `E_gpa * 1000` (11000 / 33000) with
    `material_basis.E_mpa.basis` present — plus an `importorskip`-guarded real
    `skfem` static solve on the c24 plate.
  - AC7: no builtin `source`/`notes`/`label` names a licensed aggregator
    (`links[].label` is exempt — an outbound reference to MMPDS/Prospector is
    the point); every `links[].url` is `https`.
  - Plus a docs test: `docs/materials.md` exists and names the lint command,
    the immutability rule, and the three deferrals.
- **`docs/materials.md`** (new) — the materials reference: the card schema
  (all 15 property keys with canonical units, `value`/`range`, the
  `typical|minimum|characteristic` basis vocabulary, `source`, `T_c`, `table`,
  `cost_usd_kg`'s two legal spellings, the `process` vocabulary, `links`, v1
  flat-entry compatibility and what it reads as — uncited); the 7-category/
  30-leaf taxonomy; the three-layer resolution; library versioning
  (`LIBRARY_VERSION`, the `materials_library` manifest key) and the
  editorial immutability rule stated as its own callout; the `find_materials`/
  `filter` constraint grammar; FEM resolution (mean-temperature thermal
  evaluation, `temperature_c`, `material_basis`, the clamping warning); the
  lint (`agentcad materials lint <path>… [--profile library|user] [--json]`,
  exit codes 0/1/2, all 13 rule codes); a CONTRIBUTING section (spec §11's
  sourcing rules, restated for a contributor rather than a reviewer) plus the
  editorial QA process (linking `materials_data/PROVENANCE.md`); and a
  Deferred section covering the `agentcad-materials` community repo + CI
  (PRD-031), material-card package distribution (PRD-011 mechanics), the
  FreeCAD `.FCMat` one-way import mapping table, 600+ records/dated cost
  refreshes, and the ⌘K palette entry (PRD-026).
- **`AGENTS.md`** — `materials_query.py`/`materials_lint.py`/`materials_data/`
  added to the `agentcad/core/` module listing; a new "Materials library
  gotchas (PRD-028)" section (13 bullets) covering closed property keys,
  density-must-be-a-point, the loader's raise-at-import (`_`-prefixed drafts
  as the escape hatch), the immutability rule, the two lint profiles, `masonry`
  kept, `characteristic`/`links` replacing `allowable-linked`, service-side
  FEM resolution (mean temperature, the clamp-warning prefix, the two
  `fallback_default` values), the `find_materials` conservative-bound/
  missing-property/`nearest_relaxation` rules, the `routes_materials.py` 422
  convention, the aggregator-name lint, the `materials_library` manifest key,
  and the CLI invocation (`.venv/bin/agentcad materials lint`, not `python -m
  agentcad.cli`, which has no `__main__` guard).
- **`CLAUDE.md`** — one condensed "Materials library" bullet in "Traps that
  will bite you" (same style as the other PRDs' bullets); `docs/materials.md`
  added to "Deeper docs".
- **`docs/agent-api.md`** — verified, not changed: the Materials section
  (`list_materials`/`find_materials`/`get_material`/`set_project_materials`/
  `set_solid_materials`) and the FEM rows (`temperature_c`, `material_basis`,
  the clamping warning) already describe the landed slices 2/3 behaviour
  exactly. `get_project` does not carry a `materials_library` key today
  (`service._materials_map` returns `{id: {label, density_g_cm3}}` only, no
  version string), so the existing `get_project` row is already accurate and
  was left as-is. No dedicated CLI section exists in this doc, so no
  `agentcad materials lint` mention was added there (the CLI lives in
  `docs/materials.md` and `AGENTS.md`).
- **`docs/roadmap.md`** — unchanged: PRD-028's row is already "in progress",
  which is correct until the controller merges the branch.

## Files
- `tests/test_prd028_acceptance.py` — new, 21 tests.
- `docs/materials.md` — new.
- `AGENTS.md` — module listing + the new gotchas section.
- `CLAUDE.md` — one condensed trap bullet + a Deeper-docs entry.

## Verification
- `.venv/bin/python -m pytest -q tests/test_prd028_acceptance.py
  tests/test_materials.py tests/test_materials_lint.py` → 82 passed, 2
  skipped.
- `.venv/bin/python -m pytest -q -n 4 --dist loadscope
  tests/test_prd028_acceptance.py tests/test_prd007_acceptance.py
  tests/test_prd004_acceptance.py tests/test_prd011_acceptance.py
  tests/test_prd012_acceptance.py tests/test_examples.py -k "not exhaustive"`
  → 107 passed, 2 skipped.
- Last full `make test`-equivalent run on this branch (entry 0286): 4565
  passed, 44 skipped; the controller's full-suite run over the finished
  branch is cited in the close-out entry.

## Notes
`docs/user-guide.md`'s "Materials browser" subsection and `frontend/**` are a
concurrent agent's slice (changelog 0290) and were not touched here.
`materials_data/*.json` and `materials_data/PROVENANCE.md` are a concurrent
QA agent's slice (changelog 0291) and were not touched here either — AC4's
`PROVENANCE.md` assertion is deliberately loose (existence + "Prospector")
for exactly that reason. No product code changed in this slice; every test
here graded the surface slices 1–4 already shipped, and none of them exposed
a product bug.
