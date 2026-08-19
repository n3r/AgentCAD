# 0274 — PRD-028 slice 4: the curated library — 434 cited material cards across every leaf

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (orchestrated with Claude; eight parallel Opus curation agents, one data file each)

## Summary
The builtin library grows from the 30 migrated legacy records to **434 generic
material cards** (PRD-028 G1/AC4 floor: 300), every property carrying a
`unit`, a `basis` and a primary-source `source`, across all 30 taxonomy leaves
(spec §4). Standards-first (EN 338, EN 1992-1-1 Table 3.1, EN 10025/10083/10088,
EN 485/755, ASTM grade minima), handbook typicals (ASM Handbook volumes,
Aluminum Association, CDA, FPL Wood Handbook) and manufacturer datasheets cited
by product. No aggregator (MatWeb, MakeItFrom, UL Prospector, Granta) is named
anywhere — the lint's `disallowed_source` rule is clean over the whole tree.

## Changes
- `metal_aluminum_light.json` 4 → 47 (aluminum 37, magnesium 6, zinc 4; E(T)
  tables on 6082-T6/6063-T6/5083-H116 from EN 1999-1-2 Table 1, labelled
  fire-design curves; MMPDS links on the aerospace alloys).
- `metal_steel.json` 5 → 65 (steel 45, tool_steel 10, cast_iron 10; EN 1993-1-2
  Table 3.1 E(T) tables on 16 structural grades including the legacy `steel_a36`
  — its 200 GPa point and every other legacy value unchanged — and λ(T)/c_p(T)
  on the 8 EN grades from §3.4.1; tool steels carry hardness bands, not invented
  tensile points; gray irons carry a secant-E range and no yield).
- `metal_stainless_ni_ti_cu.json` 9 → 62 (stainless 20, nickel 10, titanium 8,
  copper 16, other_metal 8 incl. W/Mo/Ta/Nb/Pb/Sn/CoCr/Zr; 304L E(T) from
  EN 1993-1-2 Annex C, Cu/W k(T) from TPRC compilations).
- `polymer_commodity_engineering.json` 5 → 48 (commodity 25, engineering 23;
  four E(T) curves read from named design guides; polyamides dry-as-moulded).
- `polymer_high_performance_thermoset_elastomer_foam.json` 2 → 48
  (high_performance 18, thermoset 9, elastomer 13, foam 8; vulcanized rubbers
  carry no E; three E(T) read-offs labelled as such).
- `composite_ceramic_other.json` 2 → 55 (laminate 13, sandwich 4,
  reinforced_polymer 9, technical_ceramic 16, glass 8, other 5; sandwich panels
  are density-only by design; UD carbon omits its negative CTE because the
  schema is ≥ 0 — said in the notes; alumina/SiC/Si k(T) tables).
- `wood.json` 2 → 60 (EN 338:2016 C14–C50 and D18–D80 with ρ_mean/E_0,mean/
  E_90,mean/G_mean/f_m,k/f_c,0,k/f_t,0,k, FPL Wood Handbook species, GL/LVL/CLT/
  panel products from EN 14080/14374/16351/636/300/622/312; EN 1995-1-2 Annex B
  k(T) on nine cards).
- `masonry.json` 1 → 49 (the full EN 1992-1-1 Table 3.1 series incl.
  `concrete_c30_37` beside the legacy `concrete`, ACI 318 grades, lightweight,
  UHPC, shotcrete; ASTM C270 / EN 998-2 mortars; EN 771 / ASTM C90 units incl.
  AAC; ASTM C615/C568/C616/C503/C629 stone ranges; EN 1992-1-2 eq. (3.1) k(T) on
  20 concrete cards).
- Spec/plan: AC6 names `c24` (the EN 338 id), not `timber_c24`; the plan's lint
  command is `.venv/bin/agentcad materials lint`.

## Files
- `agentcad/core/materials_data/*.json` — 404 new cards; the 30 legacy cards
  byte-identical in every value (the pinned-density test and each agent's
  per-record diff).
- `docs/superpowers/specs/2026-08-19-materials-database-design.md`,
  `docs/superpowers/plans/2026-08-19-materials-database.md` — the two
  corrections above.

## Verification
- `.venv/bin/agentcad materials lint agentcad/core/materials_data` →
  `0 errors, 22 warnings`. Every warning is `out_of_envelope` and deliberately
  kept with the reason in the card's `notes`: AAC block density/E (a 0.4–0.6
  g/cm3 masonry unit), quartzite/basalt E above the masonry band, the eight
  foams below the polymer density band, four sandwich panels below the composite
  band, WC-6Co density and E above the ceramic band (a cermet).
- `len(MATERIALS)` = 434; 30/30 taxonomy leaves populated (≥ 4 each); 80
  temperature tables, each with a cited source.
- `.venv/bin/python -m pytest -q -n 4 --dist loadscope tests/test_materials.py
  tests/test_materials_lint.py tests/test_materials_query.py
  tests/test_materials_tools.py tests/test_fem_material_resolution.py
  tests/test_analysis.py` → 139 passed, 7 skipped.
- The last full `make test`-equivalent run on this branch (slice 1, entry 0271)
  was 4565 passed, 44 skipped; the controller's full-suite run over the
  finished branch is cited in the close-out entry.

## Notes
The QA pass (20-record spot check against named sources), the per-file
`PROVENANCE.md` and the AC7 attestation land in the next entry. Deliberate
omissions are the rule, not the exception: when an agent could not name the
source of a number it left the property out (e.g. hardboard E, 7020-T6 k,
lead's yield, Ti Gr 9 thermal properties) rather than fill the card.
