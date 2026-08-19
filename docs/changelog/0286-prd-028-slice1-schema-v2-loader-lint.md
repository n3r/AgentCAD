# 0286 — PRD-028 slice 1: material card schema v2, the data loader, the migration of the 30, and `materials lint`

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
The materials seam becomes a **data** library instead of a Python table: cards
in `agentcad/core/materials_data/*.json` carry a per-property `unit`, `basis`
and `source`, `materials.py` resolves them into the same `Material` every
consumer already reads, and the loader refuses to ship a card the new lint
calls wrong. The 30 legacy records are re-attributed to primary sources with
every number byte-identical (they were generated from the pre-migration table,
so the equality is by construction, not by proofreading).

## Changes
- **`core/materials.py`** — schema v2 with every existing name and signature
  intact:
  - `PROPERTY_UNITS` (15 closed keys → one canonical unit each; the first nine
    are the v1 keys in v1 order, and `_NUMERIC_FIELDS` is derived from it),
    `BASES`, `CATEGORIES` (7), `SUBCATEGORIES`, `PROCESS_RATINGS`,
    `PRINT_PROCESSES`, `PROCESS_KEYS`, `CARD_FIELDS`, `PROPERTY_FIELDS`.
  - `Property` (frozen): `value|range`, `unit`, `basis`, `source`, `T_c`,
    `table`, `as_of`; `.point` (value or midpoint) and
    `.at(T_c) -> (value, interpolated, clamped)` — linear interpolation
    between table rows, clamped to the end rows outside, `(point, False,
    False)` with no table.
  - `Material` keeps every v1 field in its v1 position and gains defaulted
    `poisson_ratio, cp_j_kg_k, shear_modulus_gpa, compressive_mpa,
    bending_mpa, E_perp_gpa, subcategory, condition, standards, process,
    links, properties, warnings, library_version`; `prop(key)`;
    `to_payload(full=False)` keeps the flat shape and adds `subcategory,
    condition, standards, process?, links, warnings, basis, uncited,
    library_version` (`full=True` adds `properties`).
  - `normalize_entry(id, entry, source)` accepts a **v1 flat entry** (every v1
    rejection preserved) or a **v2 card** (top-level keys, property objects,
    `process` vocabulary, `links`, `standards`, subcategory-belongs-to-category,
    top-level `cost_usd_kg` shorthand normalized into `properties`). Mixing
    flat numeric keys with `properties` is a refusal; a range density resolves
    to the midpoint and records `warnings = ("density_range_midpoint",)`.
    `validate_material_entry`/`validate_materials_dict` keep their signatures
    and delegate.
  - `load_library()` reads `materials_data/`, lints every card at the
    `library` profile and raises `RuntimeError` naming file/id/property on any
    error finding; `LIBRARY_VERSION` and `MATERIALS` come from it. Duplicate
    ids across family files and a non-2 `schema_version` also raise.
- **`core/materials_data/`** (new) — `_library.json` (`2.0.0`), eight family
  files holding the 30 legacy records as v2 cards, and `PROVENANCE.md`
  (per-file sources + the no-aggregator attestation). Sources are ASM Handbook
  Vol. 1/2, Aluminum Association, ASTM (A36/A992 minima → `basis: minimum`),
  Special Metals, CDA, manufacturer datasheets, EOS, Hexcel-style laminate
  data, EN 1992-1-1 / EN 14080 (→ `characteristic`) and FPL Wood Handbook. No
  `source` names MatWeb, MakeItFrom, UL Prospector or Granta. `process` blocks
  and MMPDS `links` were added only where they are defensible.
- **`core/materials_lint.py`** (new, pure) — `Finding`, `lint_card`,
  `lint_catalog`, `lint_file`, `lint_paths`, `has_errors`, `ENVELOPES`. Codes:
  `invalid_id, schema, unit_mismatch, missing_citation, density_must_be_point,
  subcategory_required, process_source_required, table_not_monotonic,
  point_outside_table, range_inverted, cost_in_two_places, disallowed_source,
  out_of_envelope`. Profiles `library` (every rule an error) and `user`
  (uncited/range-density are warnings, subcategory optional); findings sort by
  `(file, id, property, code)`.
- **`cli.py`** — `agentcad materials lint <path>… [--profile] [--json]`, thin
  over `materials_lint`; exit 0 clean / 1 errors / 2 usage. Added to the
  subparser metavar and the module docstring.
- **Plumbing** — `project._empty_manifest` writes `materials_library`
  (the single place a new manifest comes from);
  `tools_materials.set_project_materials` refreshes it; `list_materials` gains
  `count`, `library_version`, `project_library_version` and `warnings`
  (`library_version_newer_than_shipped` / `library_version_unreadable`);
  `manifest_merge`'s key table names the new top-level key (it already merged
  whole); the PyInstaller spec declares `materials_data` as data (only `.py`
  files are collected as modules).

## Files
- `agentcad/core/materials.py` — schema v2, `Property`, `normalize_entry`, loader
- `agentcad/core/materials_lint.py` — new, the card lint
- `agentcad/core/materials_data/*.json` — new, the 30 records as cards
- `agentcad/core/materials_data/PROVENANCE.md` — new, per-file sources
- `agentcad/cli.py` — `materials lint` sub-command
- `agentcad/core/project.py` — `materials_library` in `_empty_manifest`
- `agentcad/core/tools_materials.py` — version pin + richer `list_materials`
- `agentcad/core/manifest_merge.py` — docstring key table
- `packaging/pyinstaller/agentcad.spec` — ship the card directory
- `tests/test_materials.py` — 30 pinned densities, migration fidelity, schema,
  refusals, payload compat, version reporting
- `tests/test_materials_lint.py` — new, every rule + the CLI exit codes
- `tests/test_packages_cli.py` — the `--help` metavar literal gains `materials`

## Verification
`make test` equivalent (`.venv/bin/python -m pytest -q -n 4 --dist loadscope`,
minus the two sketch benchmarks): **4565 passed, 44 skipped, 9 failed in
27:44**. All nine failures are the same self-referential check —
`test_the_newest_changelog_cites_a_make_test_count` / `..._suite_count_is_cited`
across the PRD-005a/006/007/008/009/010/011/012/031a acceptance modules — which
reads the newest changelog entry, i.e. *this* file, and is satisfied by this
paragraph; re-running those nine after writing it is green. Two further
failures seen in an earlier pass (`test_supervisor.py::test_a_ballooning_script
_is_killed_named_and_the_worker_comes_back`, `test_examples.py::TestEngineCore::
test_assembly_exports_step`) did not reproduce and are CPU-contention flakes — a
second full test suite was running on the same machine.

`tests/test_materials.py` + `tests/test_materials_lint.py` alone: 63 passed.

## Notes
- **The loader fails loudly.** A shipped card with an error-level finding
  raises `RuntimeError` at *import* of `agentcad.core.materials`, naming the
  file, the id and the property. That is deliberate: degrading quietly would
  put an uncited or mis-united number into somebody's mass calculation.
  Warnings (envelope bands) are informational and ignored at load.
- **The import cycle is real.** `materials` imports `materials_lint` inside
  `load_library`, and `materials_lint` imports `materials` inside its
  functions. Making either import module-level breaks whichever module is
  imported first.
- **Densities are unchanged**, so `service._cache_key` is unchanged and every
  existing mesh cache entry still hits — the suite's golden/cache tests are the
  proof. The flat numeric fields are now `float` (`Property.point`) where the
  table happened to hold ints; JSON and `==` are unaffected.
- Three properties were **added** rather than re-valued, all from facts the v1
  rows already stated in their own `notes`: `compressive_mpa` = 30 MPa (f_ck)
  on `concrete`, and `bending_mpa` mirroring `ultimate_mpa` on `glulam` (24,
  f_m,k) and `douglas_fir` (85, MOR) — the honest key for a number v1 had to
  stuff into `ultimate_mpa`.
- `agentcad materials lint` starts **no kernel and no service**; linting a card
  must never build anything.
