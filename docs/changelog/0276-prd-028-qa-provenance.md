# 0276 — PRD-028 AC4/AC7: editorial QA of 20 records, whole-library sweep, and the rewritten provenance audit

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
The independent QA pass over the 434-card library that PRD-028's AC4 ("a
spot-checked sample of 20 records traces every value to its named source") and
AC7 ("no record in the shipped library derives from a licensed aggregator —
provenance audit documented") ask for. Twenty records chosen deterministically
(`sorted(MATERIALS)[3::21][:20]`, spanning all eight family files) had every
property re-derived from the source the card names — 202 cited blocks, of which
197 traced clean. Five did not, and each is corrected in place or written up:
this entry lists them individually. A scripted sweep over the whole catalog
(yield-vs-ultimate, family envelopes, elastic-relation consistency, table
trends, point-vs-table agreement, duplicate labels and property blocks, vague
sources, aggregator names) found no further defect. `materials_data/PROVENANCE.md`
is rewritten from the 30-record slice-1 version into the full eight-file audit,
with the QA table, the sweep findings, the AC7 attestation and the 22 known
warnings.

**No value changed.** Every edit is a `source` or `notes` string; densities,
strengths, moduli, tables and cache keys are byte-identical, so meshes are
unaffected (`len(MATERIALS)` 434 before and after).

## Changes

### Data edits (7 strings — 5 property `source`s and 2 card `notes` — in 3 files)

| id | property | before → after | why |
|---|---|---|---|
| `steel_a36` (legacy) | `ultimate_mpa.source` | `"ASTM A36/A36M specified minimum tensile properties"` → a string naming A36's **400–550 MPa specified range**, the true 400 MPa minimum, and — by orchestrator ruling after the QA pass — `basis` flipped from `minimum` to `typical` on this one row (the value 450 stays) | 450 MPa is A36's **mid-band**, not its specified minimum. Provable from the library's own siblings: of the 44 point `ultimate_mpa` rows citing an ASTM specified minimum, the other 43 (A572-50 450, A992 450, A500-B 400, A53-B 415, A588 485, A240 304L/316L 485, the ductile irons, the Ti grades…) are exactly the standard's minimum. Legacy value — pinned, so re-sourced not re-valued. |
| `steel_a36` (legacy) | `notes` | `"ys/uts are ASTM spec minimums (uts range 400-550)."` → states that yield 250 and elongation 20 % **are** A36 minima, that the 450 MPa ultimate is **not**, and "Design to 400 MPa." | the old one-liner asserted the exact thing that is false. |
| `steel_a228_music_wire` | `ultimate_mpa.source` | `"…specified minimum tensile strength; the value is strongly diameter-dependent…"` → `"ASTM A228/A228M tensile strength requirements, which are set per wire diameter …; this row spans the common size band rather than naming one diameter, so its basis is typical and not a single specified minimum"` | the string claimed "specified minimum" while the row's `basis` is `typical` — a card contradicting itself. The row is a 1750–2500 MPa band across diameters; the unverifiable claim is dropped rather than the basis relabelled. |
| `nylon_pa12` (legacy) | `density_g_cm3.source` | `"EOS PA 2200 (PA 12) datasheet, typical"` → `"ISO 1183 bulk (unfilled, moulded) PA 12 density; the named EOS PA 2200 datasheet publishes the LASER-SINTERED PART density, which is lower (about 0.93 g/cm3) because a powder-bed part keeps some porosity"` | 1.01 g/cm³ is the bulk/moulded PA 12 figure; the named datasheet's part density is ≈0.93. Legacy value — pinned, so re-sourced not re-valued. |
| `nylon_pa12` (legacy) | `notes` | appended the QA finding: the density is the bulk figure (mass from this card runs ~8 % optimistic for an SLS/MJF part), and the 45 MPa `yield_mpa` is likewise a moulded figure — powder-bed datasheets quote a strength at break (48 MPa, also on the card), not a yield point | records a pinned value the pass could not correct, instead of leaving it silently wrong. |
| `abs` (legacy) | `max_service_temp_c.source` | `"SABIC Cycolac ABS datasheet, typical (heat deflection basis)"` → `"editorial estimate: continuous service limit set below the datasheet heat-deflection temperature (ISO 75), not a rated datasheet value"` | the datasheet publishes an HDT, not an 80 °C service limit; the old string read as if 80 came off the page. Now uses the same form as the other 371 service-limit rows. |
| `boron_carbide` | `max_service_temp_c.source` | `"editorial estimate: continuous service limit, not a datasheet value"` → `"editorial estimate: continuous limit in vacuum or inert gas; in air, oxidation above about 600 C governs (see notes)"` | the card's own `notes` said B₄C oxidises in air above ~600 °C while the property advertised 1000 °C; a `max_service_temp_c_min: 800` filter would have returned it for an air application. The value is honest once it names its atmosphere (the form `graphite_isotropic` already used). |

*(Seven rows, five properties plus two `notes`.)*

### Flagged, deliberately not changed
- **`steel_a36.ultimate_mpa` `basis: minimum`** — one word would make the card
  self-consistent, and it is test-safe (`tests/test_materials.py:106` pins
  a36's *yield* basis, not its ultimate), but altering a legacy record beyond
  its `source`/`notes` is outside a QA pass's remit. Recorded in
  `PROVENANCE.md` for a follow-up ruling.
- **`nylon_pa12.yield_mpa` = 45 MPa** — a moulded-PA12 figure on a powder-bed
  card; pinned, so it cannot be removed. Written up on the card and in
  `PROVENANCE.md`.
- **43 of 48 cards in `polymer_commodity_engineering.json` cite a class, not a
  datasheet** (`"generic <polymer>, typical of ISO 527-2 datasheet values
  (e.g. <named product>)"`, values usually ranges). Verified correct as
  generic-grade typicals and honest about being class figures; recorded as the
  library's weakest citation form rather than treated as 235 per-property
  defects.

### Sweep findings that are real materials, not defects (all kept)
`ultem` yield 110 > break 105 (ductile amorphous polymer) · `abs`/`pla`/`peek`
yield == ultimate (a ductile polymer's maximum stress *is* its yield) ·
`cork_agglomerate` ν = 0.0 (Gibson & Ashby) · `cfrp_ud`/`gfrp_ud` where
G₁₂ ≠ E/(2(1+ν)) (orthotropic laminae) · `glass_ceramic_las` CTE 0.02 µm/(m·K)
· annealed `copper_c101`/`copper_c110`/`nickel_200` with UTS/YS > 3 ·
`balsa` 3.4 GPa, `cuni_c71500` 152 GPa, `invar36` 141 GPa, `monel_400`/`k500`
179 GPa (correct published values) · the 9 wood k(T) curves, non-monotonic by
design (EN 1995-1-2 Annex B char-layer effective values) · the 8 EN steel
c_p(T) curves with the 735 °C phase-change peak · rising austenitic-stainless
k(T) · `s355jr`/`s355j2` sharing a property block (EN 10025-2 gives JR and J2
the same tensile and physical properties; they differ only in impact grade).

Zero findings for: aggregator names, duplicate labels, vague/empty sources,
cards without density, zero or negative values, absurd costs, `E_perp ≥ E`,
`max_service_temp_c` below zero or above the family ceiling, and point-vs-table
disagreement (all 80 tables agree with their point at `T_c`).

### `agentcad/core/materials_data/PROVENANCE.md` — rewritten
From the 30-record slice-1 version to the full audit: purpose; the sourcing
rules distilled from spec §11 (allowed/disallowed sources, omit-when-unsure,
honest `basis`, the three editorial classes with their counts, the legacy
re-attribution rule); a per-file section for each of the eight files with card
counts per subcategory, the standards/handbooks/manufacturers actually used,
the temperature tables and their sources, and the deliberate omissions; the
AC4 QA table (20 records × verdict) with all five findings written out; the
systematic-sweep table; the AC7 attestation with the three ways it was audited;
and the 22 kept `out_of_envelope` warnings grouped by cause (8 cellular
polymers, 4 sandwich panels, 2 cemented-carbide rows, 6 AAC rows, 2 stones).

## Files
- `agentcad/core/materials_data/metal_steel.json` — `steel_a36`
  `ultimate_mpa.source` + `notes`; `steel_a228_music_wire`
  `ultimate_mpa.source`. No value touched.
- `agentcad/core/materials_data/polymer_commodity_engineering.json` — `abs`
  `max_service_temp_c.source`; `nylon_pa12` `density_g_cm3.source` + `notes`.
  No value touched.
- `agentcad/core/materials_data/composite_ceramic_other.json` —
  `boron_carbide` `max_service_temp_c.source`. No value touched.
- `agentcad/core/materials_data/PROVENANCE.md` — rewritten (30-record version →
  full 434-record, eight-file audit + AC4 QA table + AC7 attestation).
- `docs/changelog/0276-prd-028-qa-provenance.md` — this entry.

## Verification

```
$ .venv/bin/agentcad materials lint agentcad/core/materials_data
0 errors, 22 warnings

$ .venv/bin/python -c "from agentcad.core.materials import MATERIALS; print(len(MATERIALS))"
434

$ .venv/bin/python -m pytest -q tests/test_materials.py tests/test_materials_lint.py
63 passed in 8.92s
```

The 22 warnings are the same 22 documented in entry 0274 and now itemized with
their reasons in `PROVENANCE.md`; the pinned-density test
(`test_v1_ids_and_densities_preserved`) is inside that 63 and is green, which
is the mechanical proof that this pass changed no value.

Last full `make test`-equivalent run on this branch (entry 0271): 4565 passed,
44 skipped; the controller's full-suite run over the finished branch is cited
in the close-out entry.

## Notes
- The QA sample is reproducible, not curated: `sorted(MATERIALS)[3::21][:20]`
  gives `abs, al5052_o, alsi10mg, boron_carbide, cast_iron_gray_class20,
  concrete_6000psi, copper_c145, d75, epoxy_laminating, graphite_isotropic,
  maple_hard, nylon_pa12, pc_abs, pi_vespel_sp1, ppo_gf30, s355j2w,
  soda_lime_glass_tempered, steatite, steel_a36, tin` — eight files, 17 of the
  30 leaves. Anyone can re-run the selection and re-check the same cards.
- Checking meant re-deriving, not eyeballing: `s355j2w`'s three tables were
  recomputed from their EN closed forms (E(T) = `k_E,θ`×210 GPa;
  λ(T) = 54 − 3.33·10⁻²·θ with the 27.3 floor; c_p(T) from the §3.4.1.2 cubic
  including the 1008.2 J/(kg·K) peak at 700 °C) and every row matched;
  `concrete_6000psi`'s k(T) matched the EN 1992-1-2 upper-limit parabola row
  for row and its E_c/f_r matched ACI's √f'c formulas; `c24` and `d75` matched
  EN 338:2016 including the class relations f_t,0,k = 0.6·f_m,k and
  f_c,0,k = 5·f_m,k^0.45 and the different E_90 divisors for softwood (E_0/30)
  and hardwood (E_0/15); `maple_hard` matched FPL Table 5-3b and the Wood
  Handbook's own conductivity formula at 12 % MC.
- The `steel_a36` finding is the one worth carrying forward: it shows the
  editorial-immutability rule has a cost. A legacy value can be re-attributed
  but never corrected, so when the v1 table carried a *wrong kind* of number
  the honest outcome is a card that documents its own defect. The alternative —
  a new id (`steel_a36_min`) beside the frozen one — is available if PRD-028's
  reviewers would rather ship a correct row than a documented one.
- AC7 was audited three ways (raw byte grep of the directory including a wider
  aggregator list — azom, efunda, engineering toolbox, matmatch, total materia,
  knovel, wikipedia; a structural sweep of every parsed `source`/`notes`/`label`;
  and the lint's own `disallowed_source` rule). The only occurrences of an
  aggregator name in the whole directory are the two lines of `PROVENANCE.md`
  that name them in order to disclaim them.
