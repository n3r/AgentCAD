# Provenance of the shipped material library

`library_version` 2.0.0 · **434 records** in eight family files · 3 646 cited
properties · 80 temperature tables · 15 `links` entries · 318 `process` blocks.

This file is the AC7 provenance audit and the AC4 editorial-QA record for
PRD-028. It says where every family's numbers come from, what was deliberately
left out, what a separate QA pass re-derived from the named sources, and what
it changed.

## Purpose

A material card is a *claim about the physical world*, and the only thing that
makes it usable is the citation beside it. This library is meant to be read the
way an engineer reads a handbook page: the number, the basis it was measured or
specified on, and the document it came from — never a number on its own.

Nothing here is a design allowable. Where an allowable exists and is public
(MMPDS for the airframe alloys), the card carries a `links` entry to it —
**linked, never mirrored**.

## Sourcing rules (spec §11, enforced by `materials_lint.py`)

- **Allowed**: standards (EN 338, EN 1992-1-1, EN 1993-1-2, EN 10025/10083/
  10088, EN 206, ASTM/ASME, ISO, IEC, ACI, AMS, SAE); public-domain datasets
  (NIST, USGS/FHWA, FPL *Wood Handbook*); handbooks cited **by volume and
  table** (ASM Handbook Vols. 1/2/4/6/21, Aluminum Association *Aluminum
  Standards and Data*, Copper Development Association alloy data, AISC Manual,
  Gibson & Ashby *Cellular Solids*); manufacturer datasheets cited **by
  product**; community measurements labelled as such.
- **Disallowed by name, in any `source`**: MatWeb, MakeItFrom, UL Prospector,
  Granta/CES. `agentcad materials lint` refuses them (`disallowed_source`,
  every profile).
- **When unsure of a value, omit the property.** A card needs only density plus
  its citation. Ranges beat false precision.
- **`basis` is honest**: a specified minimum is `minimum`, an EN 5 %-fractile
  characteristic value is `characteristic`, a handbook or datasheet typical is
  `typical`. Labelling a characteristic value `minimum` is exactly the
  dishonesty the vocabulary exists to prevent.
- **Editorial figures say so in their own `source`.** Three classes are
  editorial by nature and are labelled everywhere they appear:
  - `max_service_temp_c` (372 of 408) — a rule-of-thumb ceiling, not a rated
    property; several cards name the mechanism that actually sets it
    (oxidation, Tg, binder, thermal-shock differential).
  - `cost_usd_kg` (all 83 in-`properties` rows and every top-level block) —
    "editorial estimate, bulk order-of-magnitude", `as_of` 2025. Regional and
    volume variation dwarfs the figure's precision.
  - `process` blocks — classifications, not measurements; each carries one
    citation naming the practice it follows (Machining Data Handbook, AWS
    D1.1/D11.2, vendor processing guides).
  A further 119 property rows (poisson_ratio 41, E 19, cp 18, CTE 17, k 14,
  density 7, bending/compressive 6) are editorial and labelled; most are
  ranges.
- **The 30 legacy v1 records are re-attributed, not re-valued.** Every number
  is byte-identical to the v1 Python table (pinned by
  `test_v1_ids_and_densities_preserved`), because a changed density is a
  changed mesh cache key. Their `source` strings name the primary document the
  value belongs to. A legacy value that QA found mis-attributed is documented
  in place rather than corrected — see `steel_a36` and `nylon_pa12` below.

## How to check this file

```
agentcad materials lint agentcad/core/materials_data --profile library
```

`0 errors` is the claim this document makes machine-checkable; the test suite
runs the same lint over the same directory, and the loader refuses to import a
card that fails it.

---

## Per file

### `metal_aluminum_light.json` — 47 records
aluminum 37 · magnesium 6 · zinc 4.

- **Densities**: Aluminum Association, *Aluminum Standards and Data* (nominal
  per alloy).
- **Mechanical / physical**: ASM Handbook Vol. 2 (*Nonferrous Alloys and
  Special-Purpose Materials*), per temper — tensile typicals, ν, c_p, and the
  Mg/Zn data.
- **Specified minima**: ASTM B209/B211/B221/B317/B230/B609/B928, B26/B108/B85,
  AMS 4050/4427; EN 485-2, EN 755-2, EN 573-3, EN 1706; ASTM B90/B107/B94/B80
  (Mg); ASTM B86/B791, EN 12844 (Zn).
- **Tables**: `E_gpa(T)` on `al6082_t6`, `al6063_t6`, `al5083_h116` — EN
  1999-1-2 Table 1 `k_E,θ`, labelled on the card as fire-design curves.
- **`links`**: MMPDS on 2014-T6, 2024-T351, 7050-T7451, 7075-T651, 7075-T73.
- **Legacy**: al6061, al7075, al2024 (ASM Vol. 2 + AA density); `alsi10mg` is
  as-built LPBF from the EOS AlSi10Mg datasheet, and its cost is powder.
- **Omissions**: `al7020_t6` k; `al1350_h19`/`al6101_t6` elongation; ZA-alloy
  ν and c_p; temper-dependent k and EN 485-2 R_m bands carried as ranges.

### `metal_steel.json` — 65 records
steel 45 · tool_steel 10 · cast_iron 10.

- **EN minima** (t ≤ 16 mm): EN 10025-2/-3/-4/-5/-6, EN 10083-2/-3, EN 10084;
  elastic constants from EN 1993-1-1 §3.2.6 (E 210 GPa, G 81 GPa, ν 0.3,
  α 12 µm/(m·K), ρ 7850).
- **ASTM minima**: A36, A992, A572, A500, A53, A588, A514, A516 (ASME II-A),
  A29, A108, A682, A228, A295, A681, A600; A48, A536, A897, A47 for the irons,
  with EN 1561/1562/1563/1564 alongside. SAE J403/J404/J1397; AMS
  6345/6415/6265; AISC Manual for E and G.
- **Typicals**: ASM Handbook Vol. 1 (*Irons, Steels, and High-Performance
  Alloys*); Vol. 6 for welding practice.
- **Tables**: `E_gpa(T)` on 16 cards (EN 1993-1-2 Table 3.1 `k_E,θ`);
  `k_w_m_k(T)` and `cp_j_kg_k(T)` on the 8 EN grades (EN 1993-1-2 §3.4.1.3
  λ_a = 54 − 3.33·10⁻² θ, and §3.4.1.2 with the 735 °C phase-change peak).
  `steel_a36`'s E(T) is the same curve **scaled to the card's 200 GPa point**,
  which its `source` states.
- **Omissions**: tool steels carry no tensile row (they are hardness-specified)
  except H13/H11/P20 as ranges; gray irons have no yield or elongation and
  their E is a secant **range** (flake graphite has no straight elastic
  region); A53 elongation; 1075 and A228 yield; Q&T grades as ranges; no E(T)
  on A514/S690QL (the code curve is the carbon-steel one).

### `metal_stainless_ni_ti_cu.json` — 62 records
stainless 20 · nickel 10 · titanium 8 · copper 16 · other_metal 8.

- **Typicals/physicals**: ASM Handbook Vol. 1 (stainless), Vol. 2 (Ti, Ta, Nb,
  Pb, Sn, Zr, Co-Cr-Mo and the Cu elastic constants).
- **Specified minima**: ASTM A240/A276/A564/A582/A666; B265/B348/B338, F136,
  F2924, F3184; B575/B574, B127/B164, B865, B160/B162, B166/B168, B435, B637,
  F1684; the B-series copper specs (B16…B505); B365/B708, B392, B387, B777,
  B29/B749, B339, B551/B493, F75; EN 10088-2/-3; ISO 5832-4; AMS
  4911/4919/4930/5536/5659/5662/5667.
- **Manufacturer**: Special Metals (MONEL 400/K-500, NICKEL 200, INCONEL
  600/X-750), Haynes (HASTELLOY C-276/X), Carpenter Invar 36, CDA alloy data
  (C10100…C95400), Plansee (W, Mo), EOS 316L and Ti64, AK Steel 304/304L and
  316/316L.
- **Tables**: `E_gpa(T)` on `stainless_304l` (EN 1993-1-2 Annex C Table C.1);
  `k_w_m_k(T)` on `stainless_304l`, `stainless_316l` (AK Steel),
  `copper_c101` and `tungsten` (TPRC/Touloukian). Thirteen stainless `k` points
  carry `T_c: 100` because that is the temperature the mills quote them at.
- **`links`**: 5 MMPDS entries.
- **Omissions**: `ti_grade9`/`ti_6242` thermal properties; `cocr_f75` CTE and
  k; 15-5PH ν; lead and tin yield (they creep at room temperature); W and Mo
  yield (below the DBTT there isn't one), whose `max_service_temp_c` is the
  oxidation-in-air limit and says so.

### `polymer_commodity_engineering.json` — 48 records
commodity 25 · engineering 23.

- **Test basis**: ISO 527-2 (tensile), ISO 1183 (density), plus the per-polymer
  ISO designation standards (ISO 1872-2, 19069-2, 1622-2, 2897-2, 1163-2,
  2898-2, 8257-2/7823-1, 2580-2, 4894-2, 6402-2, 4613-2, 1874-2, 9988-2,
  7792-2, 15103-2, 7391-2, 11542-2/ASTM D4020, ASTM D1784, UL 94).
- **Minima from pipe standards**: EN 12201-2 / ISO 4427-2 / ISO 12162 (PE 100),
  EN ISO 15874-2, EN ISO 15877-2, EN 1452-2.
- **Manufacturer datasheets by product**: LyondellBasell, Borealis,
  ExxonMobil, Röchling, Celanese, Ineos Styrolution, Simona, Lubrizol, Röhm,
  Indorama, Ensinger, Dow, SABIC, BASF (Ultramid/Ultradur), DuPont
  (Zytel/Delrin/Crastin/Rynite), Evonik, Arkema, Envalior/DSM, Covestro,
  Eastman.
- **Tables**: `E_gpa(T)` on `pp_homopolymer` (LyondellBasell Moplen), `pa66`
  (BASF Ultramid A3K), `pom_homopolymer` (DuPont Delrin design guide),
  `pom_copolymer` (Celanese Hostaform design guide) — all read off published
  curves, which the `source` states.
- **A documented weakness**: 43 of these 48 cards cite a *class* rather than
  one datasheet — `"generic <polymer>, typical of ISO 527-2 datasheet values
  (e.g. <named product>)"`, usually with the value as a range. That is an
  honest label for a generic-grade typical (it names the test standard and a
  real product, and does not claim a single traceable measurement), but it is
  the weakest citation form in the library and a contributor tightening one of
  these cards to a single grade should replace it fact-by-fact.
- **Omissions documented in `notes`**: plasticized-PVC E, PET elongation, no
  yield on brittle grades, the PA dry-as-moulded caveat.
- **Legacy**: abs, pla, petg, nylon_pa12, pc — SABIC Cycolac, NatureWorks
  Ingeo, Eastman PETG, EOS PA 2200, Covestro Makrolon 2405.

### `polymer_high_performance_thermoset_elastomer_foam.json` — 48 records
high_performance 18 · thermoset 9 · elastomer 13 · foam 8.

- **Manufacturer datasheets by product**: Victrex; Ensinger TECAPEEK CF30/GF30;
  Arkema Kepstan 6002 / Kynar 720; Stratasys ULTEM 9085; SABIC Ultem 1000;
  Solvay Ryton / Radel R-5000 / Udel P-1700 / Torlon 4203L / CYCOM 5250-4;
  BASF Ultrason E 2010 and Elastollan 1185A/1195A; Chemours Teflon PTFE
  handbook, PFA 340, FEP 100, Viton A-401C; DuPont Vespel SP-1 and Hytrel
  5556; Celanese Vectra A950; Huntsman Araldite LY 1564; Gurit Ampreg 31;
  Scott Bader Crystic 2-446PA; Ashland Derakane 411-350; Smooth-On TASK 9;
  Formlabs Clear V4 / Tough 2000; Kraiburg THERMOLAST K; Zeon Zetpol; Lanxess
  Adiprene; JSP ARPRO; DIAB Divinycell H80; 3A AIREX T90.100; Evonik ROHACELL
  71 IG-F; Zotefoams Plastazote LD33.
- **Standards**: EN 13163/13164/13165 (+ EN 826), EN ISO 10456 Table 3, ISO
  527-2/178/868/37/844/845/1798/3601-3/12086-2/14526-3, ASTM
  D638/D700/D790/D412/D1708/D2000/D2116/D2240/D3222/D3307/D4894, C273/C365.
- **Tables**: `E_gpa(T)` on `ptfe` (Chemours), `pvdf` (Arkema Kynar),
  `pai_torlon_4203` (Solvay), read off datasheet curves and disclosed as such.
- **Editorial, labelled**: service limits derived from Tg/Tm; ν; cost bands;
  EPS/XPS modulus derived from the EN CS(10) level; `sla_tough_resin` density
  (the grade datasheet does not publish one).
- **Thin by design**: `bmi_resin`, `pe_foam`, `pekk`, `pes`, `lcp`. The
  vulcanized rubbers carry **no E** — they are hyperelastic and a Young's
  modulus would be a fiction.
- **Legacy**: peek (Victrex 450G), ultem (SABIC Ultem 1000). Ultem's break
  strength (105 MPa) below its yield (110 MPa) is real datasheet behaviour for
  a ductile amorphous polymer, not a transcription error.

### `composite_ceramic_other.json` — 55 records
laminate 13 · sandwich 4 · reinforced_polymer 9 · technical_ceramic 16 ·
glass 8 · other 5.

- **Handbooks**: ASM Handbook Vol. 21 (Composites), ASM Engineered Materials
  Handbook Vol. 4 (Ceramics and Glasses), ASM Vol. 2 (cemented carbide);
  Gibson & Ashby *Cellular Solids* (cork); NIST/CRC and Glassbrenner & Slack
  (1964) for silicon.
- **Manufacturer**: Hexcel HexPly 8552/IM7 and HexWeb 5052/HRH-10; Toray M55J;
  Solvay APC-2/AS4; Bcomp ampliTex; Basaltex/Kamenny Vek; Diab Divinycell H80;
  Airex/Baltek SB.100; CoorsTek (AD-96, AD-995, AlN, B4C, ZTA), CeramTec,
  Kyocera, Saint-Gobain Hexoloy, Sandvik/Kennametal WC-6Co, 3M B4C, Corning
  MACOR; SCHOTT BOROFLOAT 33 and ZERODUR; Corning HPFS 7980 and Gorilla Glass
  3; Heraeus fused quartz; SGL SIGRAFINE/SIGRABOND, POCO EDM-3, Element Six
  CVD diamond.
- **Standards**: IEC 60672-3, ISO 513, ISO 13356, EN 572-1/-2, EN 12150-1, ISO
  3585, EN 1748-1, IEC 60893-3-2, NEMA LI 1, ISO 527-2, ISO 178, ISO 2078,
  ASTM D578/D3039/D6641.
- **Tables**: `k_w_m_k(T)` on `alumina_96`, `alumina_995` (CoorsTek),
  `silicon_carbide` (ASM Vol. 4), `silicon_single_crystal` (Glassbrenner &
  Slack via NIST/CRC; its point carries `T_c: 27`).
- **Thin by design**: the four sandwich panels are density-level only — a panel
  is a *section* property, not a material one, and pretending otherwise would
  be the dishonest move. Aluminosilicate glass carries no strength (it is set
  by the ion-exchange depth, not the glass). UD carbon's longitudinal CTE is
  **omitted because it is negative** (≈ −0.5 µm/(m·K)) and the schema's
  properties are non-negative; the card says so. Si and diamond carry no cost.
- **Legacy**: cfrp_qi, gfrp_qi — quasi-isotropic epoxy-prepreg laminate
  typicals at ~55–60 % fibre volume (Hexcel HexPly class). In-plane only;
  through-thickness stiffness and conductivity are far lower and the `notes`
  say so. A composite has no yield point, so `yield_mpa` is absent.
- Note: `peek_gf30` and `pps_gf40` live in the high-performance polymer file,
  not here (id-collision avoided during curation).

### `wood.json` — 60 records
softwood 22 (EN 338 C14–C50 + 9 species) · hardwood 26 (EN 338 D18–D80 + 12
species) · engineered 12.

- **EN 338:2016 Tables 1–2** for the strength classes, with EN 14081-1. Each
  class card carries `bending_mpa` = f_m,k, `compressive_mpa` = f_c,0,k and
  `ultimate_mpa` = f_t,0,k, all `characteristic`; `E_gpa` = E_0,mean,
  `E_perp_gpa` = E_90,mean, `shear_modulus_gpa` = G_mean, all `typical`; and
  density = ρ_mean (the value EN 1995-1-1 uses for self-weight), **not** ρ_k,
  which the `notes` state.
- **Engineered**: EN 14080:2013 Tables 4–5 (glulam), EN 14374/EN 14279 (LVL),
  EN 16351 (CLT), EN 636 / EN 12369-2 + APA PDS (plywood), EN 300 / EN 12369-1
  Table 2 (OSB/3), EN 622-5, EN 622-2, EN 312, EN 310, EN 316, EN 323 (MDF,
  hardboard, particleboard minima).
- **Species**: FPL *Wood Handbook* (2010) Tables 5-3b, 5-5, 5-1 and Ch. 4 —
  clear, straight-grained small specimens at 12 % MC. Every species card's
  `notes` say in capitals that these are **not** design allowables.
- **Tables**: `k_w_m_k(T)` on `c16`, `c24`, `c30`, `c40`, `d30`, `d40`,
  `gl28h`, `lvl`, `clt` — EN 1995-1-2:2004 Annex B. The curve is
  non-monotonic by design (0.12 → 0.15 → 0.07 → 0.09 → 0.35 → 1.50): above
  ~200 °C it lumps the char layer, mass transport and cracking into one
  fire-design effective value and is **not** a material conductivity. Every
  card carrying it says so.
- **Editorial, labelled**: `max_service_temp_c` 60 °C, cost bands,
  machinability; `spruce_norway` and `beech_european` are ranges from European
  literature consensus rather than one namable table (the weakest wood
  citations — both are ranges, and both name the EN 338 class a graded stock
  would fall in).
- **Omissions**: hardboard E; MDF/particleboard in-plane strength; E_perp on
  the FPL species is the handbook's *elastic ratio* applied to E_0 (a derived
  range, said so in the `source`).
- **Legacy**: glulam (GL24h, EN 14080 Table 5), douglas_fir (FPL Table 5-3,
  Coast Douglas-fir, clear wood at 12 % MC — modulus of rupture, not a graded
  allowable).
- AC6 note: the EN class ids are bare (`c24`, `d40`), not `timber_c24`.

### `masonry.json` — 49 records
concrete 24 · mortar 8 · brick 9 · stone 8.

- **Concrete**: EN 1992-1-1 Table 3.1 and §3.1.3 for the 14 EN classes
  (f_ck as `compressive_mpa`, `characteristic`; E_cm; f_ctm as `ultimate_mpa`,
  `typical` — f_ctm is a *mean*, not a fractile; ν 0.2; α 10 µm/(m·K)); ACI
  318-19 §19.2.2.1(b) E_c = 4700·√f'c and §19.2.3.1 f_r = 0.62·λ·√f'c for the
  5 US grades (`minimum`, ACI specifies f'c rather than a fractile, and the
  `notes` say so); EN 206, EN 14487-1, ACI 213R, ACI 239R, ASTM C39/C1856,
  FHWA-HRT-13-060 for lightweight, UHPC and shotcrete.
- **Tables**: `k_w_m_k(T)` on all 20 concrete cards — EN 1992-1-2 §3.3.3
  eq. (3.1), the **upper limit** curve, stated in the `source`; c_p from
  §3.3.2; densities from EN 1991-1-1 Table A.1.
- **Mortar**: ASTM C270 M/S/N/O/K and EN 998-2 M5/M10/M20 (EN 1015-11).
- **Brick / units**: EN 771-1 / EN 772-13, BS 3921 Table 2, ASTM C90 + TMS 402,
  EN 771-4 / EN 12602 (AAC), EN 771-2 / ASTM C73; masonry E from EN 1996-1-1
  §3.7.2 (E = 1000·f_k) and EN 1996-2 Annex D; EN ISO 10456 Table 3 for
  thermal.
- **Stone**: ASTM C615/C568/C616/C503/C629/C120 (C170/C99 as the test
  methods) plus public USGS/FHWA/NIST rock-property compilations. Every stone
  elastic and thermal figure is an **editorial range** and says so — rock is a
  population, not a grade.
- **Legacy**: concrete (C30/37 per EN 1992-1-1 Table 3.1; thermal figures an
  editorial reading of EN 1992-1-2).

---

## AC4 — editorial QA spot check (20 records)

The sample is deterministic, not hand-picked: the sorted list of all 434 ids,
every 21st entry starting at index 3 (`ids[3::21]`, trimmed to 20). It spans
all eight files and 17 of the 30 taxonomy leaves. Every property of every
record was re-derived from the source the card names — 172 property rows plus
30 `cost`/`process` blocks, 202 cited blocks in total.

| # | id | file | blocks | verdict |
|---|----|------|--------|---------|
| 1 | `abs` | polymer_commodity | 9+1 | 9 ok · **1 fixed** (`max_service_temp_c` source) |
| 2 | `al5052_o` | metal_aluminum_light | 10+2 | all ok |
| 3 | `alsi10mg` | metal_aluminum_light | 9+1 | all ok |
| 4 | `boron_carbide` | composite_ceramic_other | 9+1 | 9 ok · **1 fixed** (`max_service_temp_c` source) |
| 5 | `cast_iron_gray_class20` | metal_steel | 9+2 | all ok |
| 6 | `concrete_6000psi` | masonry | 9+2 | all ok |
| 7 | `copper_c145` | metal_stainless_ni_ti_cu | 10+1 | all ok |
| 8 | `d75` | wood | 11+2 | all ok |
| 9 | `epoxy_laminating` | polymer_hp_thermoset… | 6+2 | all ok |
| 10 | `graphite_isotropic` | composite_ceramic_other | 8+2 | all ok |
| 11 | `maple_hard` | wood | 9+2 | all ok |
| 12 | `nylon_pa12` | polymer_commodity | 9+1 | 8 ok · **1 fixed** (density source + notes) · **1 flagged** (`yield_mpa`) |
| 13 | `pc_abs` | polymer_commodity | 7+2 | all ok (class citation, see below) |
| 14 | `pi_vespel_sp1` | polymer_hp_thermoset… | 7+2 | all ok |
| 15 | `ppo_gf30` | polymer_commodity | 6+2 | all ok (class citation, see below) |
| 16 | `s355j2w` | metal_steel | 11+2 | all ok |
| 17 | `soda_lime_glass_tempered` | composite_ceramic_other | 8+1 | all ok |
| 18 | `steatite` | composite_ceramic_other | 8+1 | all ok |
| 19 | `steel_a36` | metal_steel | 9+1 | 9 ok · **1 fixed text + flagged basis** (`ultimate_mpa`) |
| 20 | `tin` | metal_stainless_ni_ti_cu | 8+0 | all ok |

**197 of 202 blocks traced clean.** The five that did not — plus one
citation pattern worth recording — and what happened to each:

1. **`steel_a36.ultimate_mpa` = 450 MPa, `basis: minimum` — the value is not a
   minimum.** ASTM A36 specifies a tensile **range** of 400–550 MPa; the
   specified minimum is 400 MPa and 450 is the mid-band. This is provable from
   the library's own siblings: of the 44 point `ultimate_mpa` rows citing an
   ASTM specified minimum, the other 43 (A572-50 = 450, A992 = 450, A500-B =
   400, A53-B = 415, A588 = 485, A240 304L/316L = 485, the ductile-iron and
   Ti grades, …) are exactly the standard's minimum. A36's is the only
   mid-band one. It is a **legacy v1 value and cannot change** (mesh cache
   keys), so the fix is textual: the `source` and `notes` now state the range
   and name 400 MPa as the true minimum. **Orchestrator ruling (2026-08-20):**
   this single row's `basis` is changed to `typical` (the metadata now matches
   the number; the value stays 450, the mesh cache keys are untouched —
   `tests/test_materials.py` pins a36's *yield* basis, not its ultimate).
2. **`nylon_pa12.density_g_cm3` = 1.01 g/cm³ attributed to the EOS PA 2200
   datasheet.** That datasheet publishes the *laser-sintered part* density,
   ≈ 0.93 g/cm³ — a powder-bed part keeps porosity. 1.01 is the bulk/moulded
   PA 12 figure (ISO 1183). Legacy value, so **re-sourced, not re-valued**: the
   `source` now names ISO 1183 bulk PA 12 and states the datasheet's 0.93, and
   the `notes` warn that mass from this card runs ~8 % optimistic for an
   SLS/MJF part.
3. **`nylon_pa12.yield_mpa` = 45 MPa — flagged, not changed.** Powder-bed PA 12
   datasheets quote a strength at break (48 MPa, also on this card), not a
   yield point; 45 MPa is a moulded-PA12 figure. It is a pinned legacy value
   and cannot be removed, so the `notes` record it.
4. **`abs.max_service_temp_c` = 80 °C sourced as "SABIC Cycolac datasheet,
   typical (heat deflection basis)".** The datasheet publishes a heat-deflection
   temperature, not an 80 °C service limit; the old string read as if 80 came
   off the page. **Fixed**: relabelled "editorial estimate: continuous service
   limit set below the datasheet heat-deflection temperature (ISO 75), not a
   rated datasheet value" — the form used by the other 371 service-limit rows.
5. **`boron_carbide.max_service_temp_c` = 1000 °C** while the card's own
   `notes` said B₄C oxidises in air above ~600 °C. A filter for
   `max_service_temp_c_min: 800` would have returned it for an air
   application. **Fixed**: the `source` now reads "continuous limit in vacuum
   or inert gas; in air, oxidation above about 600 C governs" — the value is
   honest once it names its atmosphere, and `graphite_isotropic` in the same
   file already used exactly that form.
6. **`pc_abs` and `ppo_gf30` cite a class, not a datasheet** — `"generic
   blend, typical of ISO 527-2 datasheet values (e.g. SABIC Cycoloy C1200 /
   Covestro Bayblend T65)"`, values carried as ranges. Verified as correct
   generic-grade typicals and **kept**: the string is honest about being a
   class figure. Recorded as a known weakness of that file (43 of 48 cards)
   rather than a per-card defect.

Everything else reproduced exactly. Some of it reproduced *impressively*
exactly, and it is worth naming what was checked rather than assumed:
`s355j2w`'s three tables were recomputed from their EN closed forms — E(T)
from EN 1993-1-2 Table 3.1 `k_E,θ` × 210 GPa, λ(T) from 54 − 3.33·10⁻²·θ
(53.3 at 20 °C, floor 27.3), c_p(T) from the §3.4.1.2 cubic including the
1008.2 J/(kg·K) peak at 700 °C — and every row matched. `concrete_6000psi`'s
k(T) matched the EN 1992-1-2 upper-limit parabola row for row, and its E_c
(30.2 GPa) and f_r (3.99 MPa) matched ACI's √f'c formulas. `c24` and `d75`
matched EN 338:2016 including the standard's own class relations
(f_t,0,k = 0.6·f_m,k, f_c,0,k = 5·f_m,k^0.45) and the different E_90 divisors
for softwood (E_0/30) and hardwood (E_0/15). `maple_hard` matched FPL Table
5-3b (MOR 109, MOE 12.6, c∥ 54.0) and its k came out of the Wood Handbook's
own conductivity formula at 12 % MC. `cast_iron_gray_class20` carries ASM's
secant-modulus **range** (66–97 GPa) rather than inventing a modulus, and
omits yield and elongation entirely because flake-graphite iron has neither.

## Systematic sweep (whole library, 434 records)

Run over the resolved catalog, not the files, so it sees what a consumer sees.

| check | result |
|---|---|
| `yield_mpa > ultimate_mpa` | 1: `ultem` (110 > 105). Real — a ductile amorphous polymer breaks below its yield stress; the datasheet says so and PROVENANCE said so before this pass. Kept. |
| `yield == ultimate` | 3: `abs`, `pla`, `peek` (all legacy). Correct for a ductile polymer whose maximum stress *is* the yield point. Kept. |
| density out of the lint envelope | 22 warnings, all real and individually explained — see the list below. |
| E out of family | 5 apparent: `balsa` 3.4 GPa (a botanical hardwood), `cuni_c71500` 152 GPa, `invar36` 141 GPa, `monel_400`/`monel_k500` 179 GPa. All are the correct published values; the crude family bands were wrong, not the cards. |
| `poisson_ratio` outside (0, 0.5) | 1: `cork_agglomerate` ν = 0.0. Real, famous, and cited to Gibson & Ashby. Kept. |
| `shear_modulus` vs E/(2(1+ν)) | 2 mismatches: `cfrp_ud`, `gfrp_ud`. Both are **orthotropic laminae** where the isotropic relation does not hold (G₁₂ ≠ E₁/2(1+ν₁₂)); both `notes` already name the components. Kept. |
| CTE / k / c_p out of category band | 1: `glass_ceramic_las` CTE 0.02 µm/(m·K) — the whole point of a LAS glass-ceramic. Kept. |
| `max_service_temp_c` < 0, or above the family ceiling | none. |
| metals with ultimate/yield > 3 | 3: `copper_c101`, `copper_c110`, `nickel_200` — all fully annealed, where a ratio above 3 is exactly right. Kept. |
| `E_perp_gpa` ≥ `E_gpa` | none. |
| temperature tables with a non-physical trend | 22 flagged, 0 wrong: the 9 wood k(T) curves (EN 1995-1-2's char-layer effective values, non-monotonic by design), the 8 EN steel c_p(T) curves (the 735 °C phase-change peak), and the rising austenitic-stainless k(T) pairs. Each is disclosed in its own `source`. |
| point value vs its own table at `T_c` | 0 disagreements over 10 %; all 80 tables agree with their point. |
| duplicate labels | none. |
| identical property blocks between two ids | 1 pair: `s355jr` / `s355j2`. Correct — EN 10025-2 gives JR and J2 the same tensile and physical properties; they differ only in impact grade, which the `condition` records. |
| vague `source` strings (bare "handbook", "datasheet", empty, < 12 chars) | none. |
| cards with no density | none. |
| zero or negative property values | none (outside ν = 0 above). |
| `cost_usd_kg` ≤ 0 or absurd | none; every cost block carries `as_of: 2025`. |

## AC7 — provenance attestation

**No record in the shipped library derives from MatWeb, MakeItFrom, UL
Prospector, Granta/CES or any other licensed aggregator; every value cites a
standard, a public-domain dataset, a handbook or a manufacturer datasheet, and
the lint refuses an aggregator name in any source.**

Audited three ways, over all 434 records:

1. a raw byte grep of the whole directory for `matweb|makeitfrom|make it
   from|prospector|granta|ces|azom|efunda|engineering toolbox|matmatch|total
   materia|knovel|wikipedia` — **zero hits in any card**; the only matches in
   the directory are the two lines of this document that name the aggregators
   in order to disclaim them;
2. a structural sweep of every `source`, `notes` and `label` string after JSON
   parsing (so a name split across the file's formatting could not hide) —
   zero hits;
3. the lint itself, which is what keeps it true going forward:

```
agentcad materials lint agentcad/core/materials_data --profile library
→ 0 errors, 22 warnings
```

`materials_lint.DISALLOWED_SOURCE_RE` (`matweb|makeitfrom|prospector|granta`,
case-insensitive) is an **error in every profile**, including the `user`
profile, and it fires on a property `source`, a `process.source` and a
`cost_usd_kg.source` alike. The loader lints the shipped directory at import
and refuses to build `MATERIALS` if anything errors, so an aggregator citation
cannot reach a running server.

Where a value historically reached this project through an aggregator, the card
names the **primary** document the number actually belongs to. Where that could
not be done honestly, the value carries an editorial label saying what it is,
and the ones QA could not reconcile at all are written up above rather than
quietly deleted.

## Known warnings (45 = 22 `out_of_envelope` + 23 `point_disagrees_with_table`, all kept deliberately)

The 23 `point_disagrees_with_table` warnings (added after the review) name the
cards whose point differs from their own table at `T_c` by more than 2 %:
the 20 normal-weight concrete cards (point 1.8 W/(m·K), the usual design
value between EN 1992-1-2's upper and lower curves, against the upper-limit
table's 1.95 at 20 °C — said in each card's notes), `copper_c101` (CDA 391
against the TPRC row), `ptfe` and `pvdf` (datasheet point against a curve
read-off). Each is a deliberate editorial choice the notes explain; FEM at
the point's temperature uses the table.


`out_of_envelope` is a warning, never an error: the bands in
`materials_lint.ENVELOPES` are rails for a typo'd exponent, not a claim about
materials science. These 22 are real materials living outside a band, each
explained on its own card.

**Cellular polymers below the 0.8 g/cm³ polymer floor (8)** — a foam is mostly
air, and the density is the whole point: `eps_foam` 0.02, `pu_rigid_foam` 0.035,
`xps_foam` 0.033, `pe_foam` 0.033, `epp_foam` 0.06, `pmi_foam` 0.075,
`pvc_foam_core` 0.08, `pet_foam_core` 0.10.

**Sandwich panels below the 0.9 g/cm³ composite floor (4)** — a panel is a
skin/core section, not a solid: `nomex_cfrp_panel` 0.30, `pvc_foam_gfrp_panel`
0.31, `balsa_gfrp_panel` 0.43, `al_honeycomb_panel` 0.24.

**Cemented carbide above the ceramic bands (2)** — `tungsten_carbide_wc6co`
density 14.9 g/cm³ and E 620 GPa. It is filed as a ceramic and behaves like
one, but it is half tungsten by volume; both figures are the standard
Sandvik/Kennametal grade values.

**Autoclaved aerated concrete below the masonry bands (6)** — `aac_400`,
`aac_500`, `aac_600` at 0.4/0.5/0.6 g/cm³ and 1.25/1.75/2.25 GPa. AAC is a
foamed masonry unit; EN 771-4 / EN 12602 put it exactly here.

**Two stones above the masonry E ceiling (2)** — `basalt` and `quartzite` at
70 GPa (both carried as 50–90 GPa ranges). Igneous and metamorphic rock is
stiffer than the concrete-shaped band the category was drawn around.
