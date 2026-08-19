# Materials — the property library

The materials library is a **curated set of cited generic engineering
materials** (metals, polymers, composites, wood, masonry, ceramics) that every
mass, FEM, thermal and cost calculation in AgentCAD reads from. Every shipped
value names its primary source and its basis (typical, minimum, or a
standard's characteristic value) — this is not a design-allowables database,
and every surface that shows a number says so.

This is the reference doc: the card schema, the taxonomy, the three-layer
resolution, versioning, the query grammar, FEM resolution, the lint, the
sourcing/CONTRIBUTING rules, and what is deliberately deferred. For the agent
tool surface (`find_materials`, `get_material`, `list_materials`,
`set_project_materials`, `set_solid_materials`) see the Materials section of
`docs/agent-api.md`.

---

## Quick start

```bash
# every builtin material, as JSON
list_materials {}

# search by engineering requirement
find_materials {"require": {"yield_mpa_min": 240, "max_service_temp_c_min": 150},
                "prefer": {"cost_usd_kg": "min"}, "limit": 5}

# one material's full record — every property with its unit/basis/source
get_material {"id": "al6061"}

# lint a card (or a whole directory of them) before you ship it
.venv/bin/agentcad materials lint agentcad/core/materials_data
```

---

## 1. The card — schema v2

A **card** is the on-disk/contribution format (JSON); a resolved `Material`
(`agentcad/core/materials.py`) is the in-process object every consumer
(`service.material_density`, the FEM tools, `inspector.js`) actually reads.

```json
{
  "label": "Aluminum 6061-T6",
  "category": "metal", "subcategory": "aluminum", "condition": "T6",
  "standards": ["ASTM B209", "EN 573-3 (EN AW-6061)"],
  "properties": {
    "density_g_cm3": {"value": 2.70, "unit": "g/cm3", "basis": "typical",
                      "source": "ASM Handbook Vol. 2, 6061 physical properties"},
    "yield_mpa":     {"value": 276, "unit": "MPa", "basis": "typical",
                      "source": "ASM Handbook Vol. 2, 6061-T6 sheet"},
    "k_w_m_k":       {"value": 167, "unit": "W/(m*K)", "basis": "typical",
                      "table": [[25, 167], [100, 172], [200, 177]],
                      "source": "ASM Handbook Vol. 2, 6061-T6 thermal conductivity"}
  },
  "process": {"machinability": "excellent", "weldability": "good",
              "printable": {"dmls": "fair"},
              "sheet": {"k_factor_range": [0.33, 0.5], "min_bend_radius_t": 1.5},
              "source": "editorial classification; Machining Data Handbook"},
  "cost_usd_kg": {"range": [3.0, 5.0], "as_of": "2025", "source": "market estimate"},
  "links": [{"label": "MMPDS (allowables)", "url": "https://…"}],
  "notes": "free-form editorial note"
}
```

### Top-level fields

| Field | Required | Meaning |
|---|---|---|
| `label` | no (defaults to the id) | display name |
| `category` | no (defaults `metal`) | one of the 7 top-level categories (§2) |
| `subcategory` | required for a **library** card | one of that category's closed leaves (§2) |
| `condition` | no | temper/grade string, e.g. `T6`, `annealed`, `C24` |
| `standards` | no | list of standard reference strings |
| `properties` | required | `{key -> property object}` (below) |
| `process` | no | machinability/weldability/printability/sheet-bend metadata (§6) |
| `cost_usd_kg` | no | shorthand for the `cost_usd_kg` property — see below |
| `links` | no | `[{label, url}]` — outbound references, e.g. MMPDS, UL Prospector |
| `notes` | no | free-form string |

A card and a **v1 flat entry** (the pre-PRD-028 shape, still valid everywhere
a user writes materials — project files, `~/.agentcad/materials.json`) are
told apart by the presence of `properties`: mixing flat numeric keys with
`properties` in one entry is a validation error. See §5 for what a v1 entry
resolves to.

### Property keys (closed set, one canonical unit each)

`PROPERTY_UNITS` in `agentcad/core/materials.py` is the closed set — an
unknown key, or a key written with any other unit, is a lint/validation
error, never a silent conversion:

| Key | Unit | Meaning |
|---|---|---|
| `density_g_cm3` | g/cm3 | the **only required** property — the kernel computes `mass_g = volume_mm3 * density_g_cm3 / 1000` |
| `E_gpa` | GPa | Young's modulus |
| `yield_mpa` | MPa | yield strength |
| `ultimate_mpa` | MPa | ultimate strength (deliberately **not** enforced `>= yield_mpa`: ductile polymers legitimately break below yield) |
| `elongation_pct` | % | elongation at break |
| `cte_um_m_k` | um/(m·K) | coefficient of thermal expansion |
| `k_w_m_k` | W/(m·K) | thermal conductivity |
| `max_service_temp_c` | C | maximum continuous service temperature |
| `cost_usd_kg` | USD/kg | material cost |
| `poisson_ratio` | – | Poisson's ratio (FEM ν; historically hard-coded 0.3) |
| `cp_j_kg_k` | J/(kg·K) | specific heat |
| `shear_modulus_gpa` | GPa | shear modulus |
| `compressive_mpa` | MPa | compressive strength (concrete f_ck, timber f_c,0,k) |
| `bending_mpa` | MPa | bending strength (timber f_m,k) |
| `E_perp_gpa` | GPa | modulus perpendicular to grain/fiber (wood/laminate anisotropy — a separate property, no tensor) |

The first nine are the v1 keys, in v1 order; the last six are v2 additions.

### The property object

Exactly one of:

- `value` (a number) — a single point, or
- `range: [lo, hi]` (`lo <= hi`) — resolves to its **midpoint** everywhere a
  flat number is read (`Material.E_gpa`, mass calculations, …).

Plus:

| Field | Required | Meaning |
|---|---|---|
| `unit` | yes | must equal the key's canonical unit exactly |
| `basis` | no (default `typical`) | see below |
| `source` | no in general, **required in the `library` lint profile** | a citation string; absent = *uncited*, never invented |
| `T_c` | no (default 20) | the temperature the point value holds at |
| `table` | no | `[[T_c, value], …]`, >= 2 rows, strictly increasing `T_c` — linear interpolation, clamped to the end rows outside the span (§7) |
| `as_of` | no | a date/year string, mainly for `cost_usd_kg` |

When a card carries both a point and a `table`, the point still has to lie
within the table's value envelope (lint: `point_outside_table`) — the point is
what every non-thermal consumer reads, the table is what FEM interpolates.

### Basis vocabulary

| Basis | Meaning |
|---|---|
| `typical` | a representative datasheet/handbook figure |
| `minimum` | a standard-defined spec minimum (e.g. ASTM A36/A992 yield) |
| `characteristic` | a standard's 5%-fractile characteristic value (EN 338 timber, EN 1992 concrete) — deliberately **not** called `minimum`: a characteristic value is a statistical fractile, a US-style spec minimum is a guaranteed floor, and conflating them is exactly the dishonesty basis labels exist to prevent |

There is no fourth "allowable-linked" basis: an aerospace allowable (MMPDS)
that AgentCAD does not carry a value for is a record-level `links` entry, not
a basis on a property that has no value.

### `cost_usd_kg` placement

`cost_usd_kg` may be written **either** beside `properties` (the PRD's
top-level shorthand, `{value|range, as_of?, source?}`) **or** inside
`properties` like any other key — never both (lint/validation:
`cost_in_two_places`). The loader normalizes either spelling into
`properties.cost_usd_kg`.

### `process` vocabulary

Optional, one closed 4-step rating enum: `excellent | good | fair | poor`
(absent = not applicable/not rated — never a fifth value).

| Key | Shape |
|---|---|
| `machinability`, `weldability` | one rating |
| `printable` | `{fdm\|sla\|sls\|mjf\|dmls: rating}` |
| `sheet` | `{k_factor_range: [lo, hi] in (0, 1], min_bend_radius_t: number >= 0}` |
| `im` | one rating (injection moulding) |
| `casting` | one rating |
| `source` | **required whenever `process` is present** — one citation for the whole block (ratings are classifications, not measurements) |

### `links`

A list of `{label, url}` — outbound references only (MMPDS, UL Prospector,
manufacturer pages). AgentCAD never mirrors licensed data; a `links` entry
points a reader at it instead. `url` must be `https`.

---

## 2. Taxonomy

7 categories, 30 leaves total (`CATEGORIES`/`SUBCATEGORIES` in
`materials.py`):

| Category | Leaves |
|---|---|
| `metal` | `steel, stainless, tool_steel, cast_iron, aluminum, titanium, copper, nickel, magnesium, zinc, other_metal` |
| `polymer` | `commodity, engineering, high_performance, thermoset, elastomer, foam` |
| `composite` | `laminate, sandwich, reinforced_polymer` |
| `wood` | `softwood, hardwood, engineered` |
| `masonry` | `concrete, mortar, brick, stone` |
| `ceramic` | `technical_ceramic, glass` |
| `other` | `other` |

`masonry` is kept (not renamed to `concrete`): user files already validate
against it, and it is the honest parent of concrete/mortar/brick/stone.

`subcategory` is required for a card loaded under the `library` lint profile
(the shipped catalog); a user layer (project/global) may omit it — it reads
back as `subcategory: null`, shown as "unclassified".

---

## 3. Three-layer resolution

Same precedence as before PRD-028, unchanged:

1. **project** — the `materials` object in `project.json` (per-project)
2. **global** — `~/.agentcad/materials.json` (per-machine)
3. **builtin** — the shipped `materials_data/` library

Highest layer wins per id; an override REPLACES the whole lower entry (no
field merging — density is always required, so a half-specified override can
never silently inherit the wrong one). A builtin id can be overridden but
never deleted.

**A project/global v1 (flat) entry always reads as uncited.** `{"density_g_cm3":
2.7, "E_gpa": 70, "category": "metal"}` normalizes to v2 points with
`basis: "typical"` and `source: null` on every property — reported in the
resolved payload's `uncited: [...]` list, never invented. This is
deliberate: a hand-typed number in a project file has no citation to report,
and pretending otherwise would be worse than saying so.

---

## 4. Library versioning and the immutability rule

`LIBRARY_VERSION` (`agentcad/core/materials.py`, currently read from
`materials_data/_library.json`) is exported, and every builtin `Material`
carries it as `library_version`. `create_project` writes the additive
manifest key `materials_library` = the running server's version;
`set_project_materials` refreshes it (the entries were just validated against
*this* schema). `list_materials` reports both `library_version` (what the
server ships) and `project_library_version` (what the project pinned), with a
`library_version_newer_than_shipped` warning when the project's pin is ahead
of the running server (or `library_version_unreadable` if the pin does not
parse as dotted integers).

> ### The immutability rule
>
> **A builtin id's density — and every other property value — is never
> changed in place.** A corrected or re-based material gets a **new id** (or
> a `condition` suffix), and the old id keeps its old numbers forever.
>
> This is what actually preserves byte-stable rebuilds: `service._cache_key`
> hashes the resolved density (and, via `_solid_densities`, every per-solid
> one), so silently editing `al6061`'s density would silently invalidate
> every mesh cached against it. Version pinning tells a reader which library a
> project was authored against; the immutability rule is what makes that pin
> mean anything.

---

## 5. The query grammar

`core/materials_query.py` — pure functions over a resolved catalog, shared by
`find_materials`, `list_materials`'s `filter`, and (through the routes) the
browser.

**Constraint object** (`require` in `find_materials`, `filter` in
`list_materials`):

- `<property>_min` / `<property>_max` — for any key in the table above. A
  **range** qualifies `_min` by its **lower** bound and `_max` by its
  **upper** bound (conservative: the whole range has to clear the bar). A
  material **missing** the property never qualifies — a missing value is not
  a pass.
- `category`, `subcategory` — exact match.
- `process` — one of `cnc | weld | fdm | sla | sls | mjf | dmls | im | sheet |
  casting`; qualifies when the matching rating is `excellent|good|fair`
  (`poor` or absent fails; `sheet` qualifies on the block's presence).
- `basis` — restrict to records whose *constraining* properties (the ones
  this call actually tests) carry this basis, e.g. `"minimum"` for "only spec
  minima, please".

An unknown key raises `validation_error` listing the whole known grammar.

**`prefer`** (ranking, `find_materials` only): `{<property>: "min"|"max"}`.
Score = sum of per-property normalized rank over the qualifying set (0 best …
1 worst); a material missing a preferred property ranks last on that key. No
`prefer` → stable `(category, subcategory, id)` order.

**Result row**: `{id, label, category, subcategory, condition, constraining:
{<key>: {value|range, unit, basis, source}}, score?}` — `constraining` is the
cited evidence for exactly the properties this call tested, so an agent's
margin report can quote "yield 240 MPa (minimum, EN 755-2)" verbatim.

**Zero qualifying records** is a `validation_error`
(`"no material satisfies the constraints"`) carrying
`details.nearest_relaxation: {drop, count} | null` — the single constraint
whose removal would admit the most records, by leave-one-out — and
`details.tried` (the normalized constraints), so an agent can retry a
narrower ask instead of guessing.

See `docs/agent-api.md`'s Materials section for the full tool argument/return
shapes.

---

## 6. FEM resolution

No kernel change: the solver still takes plain scalars (`E_mpa`, `nu`,
`k_w_m_k`). Resolution happens **service-side**, in `core/tools_analysis.py`:

- `fem_thermal`: with no explicit `k_w_m_k`, k is the part material's
  conductivity at `T_eval = (t_hot_c + t_cold_c) / 2` — the mean of the two
  fixed faces is the one defensible single temperature for a linear
  steady-state conduction model.
- `fem_static` / `fem_modal`: gain a `temperature_c` argument (default 20).
  `E_mpa`/`nu` default from the material at that temperature; a material
  with no E falls back to `fem_static`'s historical 210000 MPa (`fem_modal`
  keeps its hard refusal — a modal frequency scales with √E, so a silent
  steel default would be a wrong answer, not a rough one); no
  `poisson_ratio` falls back to 0.3.
- Every FEM result carries **`material_basis`**: one entry per scalar the
  solver actually consumed — `{value, basis, source, T_c, interpolated,
  clamped, table_range, unit}` when read from the material, `{value, basis:
  "explicit"}` when the caller passed it, `{value, basis:
  "fallback_default"}` for the two historical fallbacks above.
- A property with a `table` is linearly interpolated at the evaluation
  temperature and **clamped** to its end row outside the table's span — and a
  clamped read appends a `warnings` entry:

  ```
  temperature_out_of_table_range: k_w_m_k evaluated at 400.0 C, table covers
  20.0..300.0 C; end value used
  ```

  (appended to whatever the solver's own warnings were — never overwriting
  them).

`core/specs.py`'s `_youngs_mpa` reads E through this same resolver at 20 °C,
so a spec's FEM memo key (`_fem_material_key`) is keyed on the number the
solver actually consumed, and stays byte-identical for a point-only material.

---

## 7. The lint

`core/materials_lint.py` — pure: no service, no kernel, no I/O beyond reading
the paths it is handed. `lint_card`/`lint_file`/`lint_catalog`/`lint_paths`
return a sorted list of `Finding {code, level, id, property?, message,
file?}`.

### Profiles

| Profile | Used by | Rules |
|---|---|---|
| `library` | the shipped `materials_data/` at import, and the CLI's default | every structural rule is an **error**: unknown/wrong-unit property, `missing_citation` (naming the property), `density_must_be_point`, `subcategory_required`, `process_source_required`, non-monotonic table, point outside its table's envelope, inverted range, `cost_in_two_places`, `disallowed_source` |
| `user` | a hand-written project/global entry | v1 flat entries are fine; an uncited property and a range density are **warnings**, not errors; `subcategory` is optional |

Per-category sanity bands (`ENVELOPES`: density/E/yield/max-service-temp) are
**always warnings** in every profile — they catch a typo'd exponent or a
kg/m³-written-as-g/cm³ slip, not a claim about materials science, and never
block a publish.

### Rule codes

`invalid_id, schema, unit_mismatch, missing_citation, density_must_be_point,
subcategory_required, process_source_required, table_not_monotonic,
point_outside_table, range_inverted, cost_in_two_places, disallowed_source,
out_of_envelope`.

`disallowed_source` fires whenever a `source` (on a property, on `process`,
or on `cost_usd_kg`) names MatWeb, MakeItFrom, UL Prospector or Granta —
case-insensitive, in every profile. This is the machine half of §8's sourcing
rule.

### The CLI

```
agentcad materials lint <path>… [--profile library|user] [--json]
```

A `<path>` is a card file, a directory of card files (`*.json`, skipping
`_`-prefixed marker files like `_library.json`), or a `project.json` (whose
`materials` section is linted at the `user` profile regardless of
`--profile`, because it is hand-written, not shipped).

Exit codes: **0** clean · **1** at least one error · **2** usage (no paths, or
a path that cannot be read — broken JSON in a file that *does* exist is an
error, not a usage failure).

`--json` prints the finding list as JSON (for CI / tooling); the default is
one line per finding, `<level> <file>:<id>[.<property>] <code>: <message>`.

This is exactly what the (not-yet-created) public `agentcad-materials`
community repo's CI would run — see §9.

---

## 8. CONTRIBUTING — sourcing rules

Every value in the library exists because a human or an agent could name
where it came from. These rules are editorial *and* mechanically enforced (the
`library` lint profile refuses a card that breaks them):

- **Allowed sources**: standards-defined values (EN 338, EN 1992-1-1 Table
  3.1 / EN 206, EN 10025/10088/10083, EN 485/755, ASTM grade minima —
  A36/A572/A992/B209/B221/B265/B348, ISO 527 datasheets, …), NIST/public-domain
  data, ASM Handbook / Aluminum Association / CDA / FPL Wood Handbook values
  cited by volume/table, manufacturer datasheets cited fact-by-fact, and
  community measurements labelled as such.
- **Disallowed (lint error, `disallowed_source`)**: MatWeb, MakeItFrom, UL
  Prospector, Granta — named in any `source`. These are licensed aggregators;
  ingesting their data is legally excluded (`docs/prd/…/PRD-028-materials-database.md`,
  Non-goals). A `links` entry pointing a reader *at* one of them is fine — the
  library never mirrors their numbers.
- **When unsure of a value, omit the property.** A card needs only a cited
  density. Ranges over false precision: if a number varies by grade/supplier,
  ship a `range`, not a made-up point.
- **Basis honesty**: a standard-defined minimum → `basis: "minimum"`; a
  European standard's 5%-fractile characteristic value → `"characteristic"`;
  a handbook/datasheet typical → `"typical"`. Never round a characteristic
  value up to "typical" to make it sound stronger, and never call a typical
  value a minimum.
- **The 30 legacy records are re-attributed, not re-valued.** Every number
  carried over from the pre-PRD-028 table is byte-identical (the immutability
  rule, §4, applies retroactively to the migration itself) — only the
  `source` field changed, from the old table's generic "typical datasheet
  figures" comment to a named primary source.

### Editorial QA

A QA pass (a second agent, independent of whoever authored a family file)
spot-checks a sample of records against their named sources and records the
result — per-file source lists and the "no aggregator" attestation — in
[`agentcad/core/materials_data/PROVENANCE.md`](../agentcad/core/materials_data/PROVENANCE.md).
Read that file for the current library's actual provenance; this doc
describes the *process*, not a frozen snapshot of its findings.

---

## 9. Deferred (recorded, not silently dropped)

These are in the PRD's Phase 3 or explicitly out of this build's scope. Each
is deferred *on the record* so a later PRD has a clean seam to build against,
not a surprise.

- **The public `agentcad-materials` community repository and its CI.** A
  separate repository of card files (CC-BY-SA-4.0 data licence per the PRD),
  whose CI runs exactly `agentcad materials lint <changed files> --profile
  library`. This build ships the card format, the lint, and this doc's §8 so
  that repository is a `git init` away — see PRD-031 (community material
  cards).
- **Material-card package distribution.** Packages (PRD-011) carry parametric
  *parts* today, not material cards. Distributing a curated card set as an
  installable package — so a project can `add_package`/pin a materials
  bundle the same way it pins a parts library — reuses PRD-011's index/cache/
  content-id machinery; the seam is the same `materials_data/*.json` shape,
  just not wired to `packages_lock` yet.
- **FreeCAD `.FCMat` one-way import.** A convenience, explicitly not a
  compatibility promise (FreeCAD's schema and this one will drift). The clean
  mappings, when this lands:

  | FreeCAD `.FCMat` key | AgentCAD property | Note |
  |---|---|---|
  | `Density` | `density_g_cm3` | FreeCAD stores kg/m³; divide by 1000 |
  | `YoungsModulus` | `E_gpa` | FreeCAD stores an `MPa`/`kPa`-suffixed string; parse the unit, convert to GPa |
  | `PoissonRatio` | `poisson_ratio` | unitless, direct |
  | `ThermalConductivity` | `k_w_m_k` | direct (W/(m·K)) |
  | `ThermalExpansionCoefficient` | `cte_um_m_k` | unit-string parse, convert to µm/(m·K) |
  | `UltimateTensileStrength` | `ultimate_mpa` | unit-string parse |
  | `YieldStrength` | `yield_mpa` | unit-string parse |

  Every imported card still needs a `source` to pass the `library` lint
  profile — a `.FCMat` file's own provenance (if any) becomes that citation,
  or the import is rejected pending one.
- **600+ records and dated cost refreshes.** This build ships 300+ (the PRD's
  G1/AC4 floor); growing past it and refreshing `cost_usd_kg` ranges against a
  living `as_of` date is ongoing curation, not a schema change.
- **The ⌘K palette entry.** There is no command palette yet (PRD-026). The
  browser is reachable from the inspector's material block ("Browse…") and a
  toolbar button today, at the `#materials` hash so PRD-026 can deep-link it
  once it exists.

---

## See also

- `docs/agent-api.md` — the Materials tool section (arguments/returns) and
  the FEM tools' `material_basis`/`temperature_c` rows.
- `docs/user-guide.md` — the Materials browser (human path).
- `agentcad/core/materials_data/PROVENANCE.md` — the current library's
  per-file sourcing and QA attestation.
- `docs/prd/in-progress/PRD-028-materials-database.md` — the PRD this doc
  implements.
