# Provenance of the shipped material library

`library_version` 2.0.0 · 30 records (the v1 Python table, re-attributed).

**No record in this directory derives from MatWeb, MakeItFrom, UL Prospector
or Granta.** Those are licensed aggregators; re-publishing their tables is the
PRD's explicit non-goal, and `agentcad materials lint` refuses a `source`
naming any of them (`disallowed_source`). Where a value came to us through an
aggregator historically, the card names the **primary** source the number
actually belongs to — the standard, handbook or manufacturer datasheet — and
values that could not be attributed that way were left as they are with an
honest editorial label.

Every property carries its own `source` and a `basis`:

- `typical` — a handbook/datasheet typical figure,
- `minimum` — a specified minimum (US-style spec: ASTM A36/A992 here),
- `characteristic` — a 5 %-fractile value (EN 1992 `f_ck`, EN 14080 `f_m,k`).

Two source strings are editorial rather than measured, and say so in the card:

- `max_service_temp_c` — "editorial estimate: continuous service limit, not a
  datasheet value". It is a rule-of-thumb ceiling, not a rated property.
- `cost_usd_kg` — "editorial estimate, bulk order-of-magnitude", `as_of`
  2025. Regional and volume variation is far larger than the figure's
  precision; it exists for order-of-magnitude comparison only.
- `process` blocks are **classifications**, not measurements; each carries one
  citation naming the practice it follows.

None of these numbers are design allowables. For aluminium and titanium
airframe alloys the cards carry a `links` entry to MMPDS — linked, never
mirrored.

## Per file

### `metal_aluminum_light.json` — al6061, al7075, al2024, alsi10mg
- Densities: Aluminum Association, *Aluminum Standards and Data* (nominal).
- Mechanical and physical: ASM Handbook Vol. 2 (*Properties and Selection:
  Nonferrous Alloys and Special-Purpose Materials*), per temper.
- Standards named on the cards: ASTM B209, EN 573-3.
- `alsi10mg` is additive (laser powder-bed fusion), **as-built**: EOS AlSi10Mg
  datasheet. Its cost is powder, not stock.

### `metal_steel.json` — steel_4130, steel_4340, maraging300, steel_a36, steel_a992
- 4130/4340/18Ni-300: ASM Handbook Vol. 1 (*Properties and Selection: Irons,
  Steels, and High-Performance Alloys*), in the stated condition. Heat
  treatment dominates 4130/4340 strength; the rows are normalized.
- A36 and A992: **ASTM A36/A36M** and **ASTM A992/A992M** specified minimum
  tensile properties, `basis: minimum`. Physicals are ASM Vol. 1 carbon-steel
  values.

### `metal_stainless_ni_ti_cu.json` — ti6al4v, inconel718, inconel625, stainless_304, stainless_316, ss17_4ph, copper_c110, brass_c260, bronze_c932
- Ti-6Al-4V: ASM Handbook Vol. 2, annealed bar (ASTM B265 Grade 5 / AMS 4911).
- Inconel 718 / 625: Special Metals alloy datasheets (AMS 5662 aged;
  ASTM B443 annealed).
- 304 / 316 / 17-4PH: ASM Handbook Vol. 1 annealed sheet (17-4PH in H900),
  with ASTM A240/A564 and EN 10088-2 designations on the cards.
- C11000 / C26000 / C93200: Copper Development Association alloy data.

### `polymer_commodity_engineering.json` — abs, pla, petg, nylon_pa12, pc
- Manufacturer datasheets, cited fact by fact: SABIC Cycolac (ABS),
  NatureWorks Ingeo (PLA), Eastman PETG copolyester, EOS PA 2200 (PA 12,
  powder-bed printed — molded PA12 elongates far more), Covestro Makrolon 2405
  (PC). Test basis ISO 527-2 where the datasheet states it.

### `polymer_high_performance_thermoset_elastomer_foam.json` — peek, ultem
- Victrex PEEK 450G and SABIC Ultem 1000 datasheets, unfilled, injection
  moulded. Ultem's break strength below yield is real datasheet behaviour, not
  a transcription error.

### `composite_ceramic_other.json` — cfrp_qi, gfrp_qi
- Typical epoxy-prepreg laminate data for a **quasi-isotropic** layup at
  ~55–60 % fibre volume (e.g. Hexcel HexPly datasheets). In-plane values only:
  through-thickness stiffness and conductivity are far lower, and the cards say
  so in `notes`. Composites have no yield point, so `yield_mpa` is absent.

### `wood.json` — glulam, douglas_fir
- `glulam` (GL24h): **EN 14080 Table 5** — mean density and mean E parallel to
  grain (`typical`), characteristic bending strength `f_m,k` = 24 MPa
  (`characteristic`, carried both as `bending_mpa` and, for v1 compatibility,
  as `ultimate_mpa`).
- `douglas_fir`: **FPL Wood Handbook (2010) Table 5-3**, Douglas-fir (Coast),
  clear wood at 12 % MC — modulus of rupture, not a graded-lumber allowable.
- Thermal values: FPL Wood Handbook Ch. 4, softwood at 12 % MC.

### `masonry.json` — concrete
- C30/37 per **EN 1992-1-1 Table 3.1**: `E_cm` = 33 GPa, mean tensile `f_ctm` =
  2.9 MPa (carried as `ultimate_mpa`, `typical` — `f_ctm` is a mean, not a
  characteristic value), characteristic cylinder strength `f_ck` = 30 MPa as
  `compressive_mpa` (`characteristic`). Thermal figures are an editorial
  reading of EN 1992-1-2 for normal-weight concrete.

## How to check this file

```
agentcad materials lint agentcad/core/materials_data --profile library
```

Exit 0 and "0 errors" is the claim this document makes machine-checkable; the
test suite runs the same lint over the same directory.
