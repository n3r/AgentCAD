# PRD-028 Materials database — design spec

Grounded in a full read of the materials seam (`core/materials.py` 412 LOC — the
30-row `_m(...)` table, the frozen flat `Material`, the three-layer
`MaterialLibrary`; `core/tools_materials.py` — `ProjectMaterialResolver`,
`list_materials`/`set_project_materials`; `server/routes_materials.py`;
`core/tools_analysis.py` — the FEM tools read `E_gpa`/`k_w_m_k` and hard-code
ν = 0.3; `core/specs.py:_fem_material_key`; `frontend/js/inspector.js:materialBlock`;
`manifest_merge.py` — `materials.<id>` is atomic; `service._cache_key` hashes
density). This spec records the decisions and the rejected alternatives; the
slice plan is the sibling `docs/superpowers/plans/2026-08-19-materials-database.md`.

The orchestrator ran the brainstorm autonomously (standing process for roadmap
PRDs); every ruling below is recorded with its reason so a reviewer can overturn
it by name.

## Scope (what this PRD builds now)

The PRD's **MVP + Phase 2** in full; Phase 3 only where it lives in this repo.

- **Build now:** FR1 schema v2 (+ lossless migration of the 30), FR2 taxonomy,
  FR3 sourcing rules (CONTRIBUTING text + lint enforcement), FR4 FEM temperature
  resolution, FR5 mass unchanged, FR6 `find_materials`/`get_material`/extended
  `list_materials`, FR7 browser UI (tree, filters, compare, detail with citations
  and basis badges, linked-out allowables), FR8 `agentcad materials lint`, FR9
  library versioning; G4 process metadata; **≥300 records** (AC4's floor, G1's
  floor) with per-value citations.
- **Defer (recorded, not silently dropped):**
  - the public `agentcad-materials` community repo and its CI — it is a
    *separate* repository; this PRD ships the card format, the lint it would run,
    and the CONTRIBUTING rules verbatim in `docs/materials.md` so the repo is a
    `git init` away;
  - material-card **package** distribution (PRD-011 mechanics) — packages carry
    parts today, not materials; the seam is noted in `docs/materials.md`;
  - FreeCAD `.FCMat` one-way import — a convenience the PRD itself labels
    "not a compatibility promise"; the mapping table is left as a doc note;
  - 600+ records and dated cost refreshes (Phase 3 growth);
  - the ⌘K palette entry — there is no palette yet (PRD-026); the browser is
    reachable from the inspector's material block ("Browse…") and a toolbar
    button, and the hash `#materials` so PRD-026 can deep-link it.

Non-goals unchanged from the PRD: no licensed-aggregator import, no
supplier-grade catalogs, no A/B-basis statistics, no live pricing.

## 1. Schema v2 — the card (Decision 1 — FR1)

A **card** is the on-disk/contribution format; a `Material` is the resolved
in-process object. Cards are JSON:

```json
{
  "label": "Aluminum 6061-T6",
  "category": "metal", "subcategory": "aluminum",
  "condition": "T6",
  "standards": ["ASTM B209", "EN 573-3 (EN AW-6061)"],
  "properties": {
    "density_g_cm3": {"value": 2.70, "unit": "g/cm3", "basis": "typical",
                      "source": "ASM Handbook Vol. 2, 6061 physical properties"},
    "yield_mpa":     {"value": 276, "unit": "MPa", "basis": "typical",
                      "source": "ASM Handbook Vol. 2, 6061-T6 sheet"},
    "k_w_m_k":       {"value": 167, "unit": "W/(m*K)", "basis": "typical",
                      "table": [[25, 167], [100, 172], [200, 177]],
                      "source": "..."}
  },
  "process": {"machinability": "excellent", "weldability": "good",
              "printable": {"dmls": "fair"},
              "sheet": {"k_factor_range": [0.33, 0.5], "min_bend_radius_t": 1.5},
              "source": "editorial classification; Machining Data Handbook"},
  "cost_usd_kg": {"range": [3.0, 5.0], "as_of": "2025", "source": "..."},
  "links": [{"label": "MMPDS (allowables)", "url": "https://..."}],
  "notes": "..."
}
```

**Property object:** exactly one of `value` (number) or `range: [lo, hi]`
(`lo <= hi`); `unit` required and must equal the canonical unit of the key
(self-describing cards; a wrong unit is a lint error, not a silent conversion);
`basis` ∈ `typical | minimum | characteristic` (default `typical`); `source`
required (a non-empty string naming a standard, handbook, datasheet or
"community measurement"); optional `T_c` (the temperature the point holds at,
default 20) and optional `table: [[T_c, v], …]` (≥2 rows, strictly increasing T).
When a table exists the point `value` is still required and lint checks that it
lies within the table's value envelope — the point is what every non-thermal
consumer reads; the table is what FEM interpolates.

**Rulings:**
- `characteristic` is added to the PRD's basis vocabulary: EN 338 / EN 1992
  publish 5 %-fractile *characteristic* values, which are neither "typical" nor a
  US-style spec "minimum"; labelling them `minimum` would be the dishonesty G7
  exists to prevent.
- The PRD's `allowable-linked` basis becomes a **record-level `links` list**
  (FR7: MMPDS/Prospector linked, never mirrored). A "basis" that carries no
  value is not a basis; a link is a link.
- **Property keys are a closed set** (typo safety, the existing
  `_ENTRY_FIELDS` philosophy): the nine v1 keys (`density_g_cm3, E_gpa,
  yield_mpa, ultimate_mpa, elongation_pct, cte_um_m_k, k_w_m_k,
  max_service_temp_c, cost_usd_kg`) plus `poisson_ratio` (FEM ν — today a
  hard-coded 0.3), `cp_j_kg_k` (G3), `shear_modulus_gpa`, `compressive_mpa`
  (concrete f_ck, timber f_c,0,k), `bending_mpa` (timber f_m,k, replacing the
  v1 habit of stuffing bending strength into `ultimate_mpa`), `E_perp_gpa`
  (wood/laminate anisotropy as a separate property — the PRD's MVP rule, no
  tensor). Every key has one canonical unit in `PROPERTY_UNITS`.
- **`cost_usd_kg` may be written beside `properties`** as the PRD writes it
  (`{range, as_of, source}` at the card's top level) **or** inside `properties`
  like any other key (with an optional `as_of`); the loader normalizes both to
  `properties.cost_usd_kg` (carrying `as_of`), lint refuses a card that uses
  both, and the flat `Material.cost_usd_kg` is the midpoint.
- **Density must be a point** in the shipped library (lint error
  `density_must_be_point` at `--profile library`; a warning for user cards).
  A user-card range density resolves to the midpoint and the resolved
  `Material.warnings` carries `density_range_midpoint`, which `list_materials`/
  `get_material` surface. This is the PRD's "midpoint-plus-warning" rule with the
  library held to the stricter "points preferred" side so mass and cache keys
  never depend on a convention.

**Backward compatibility (FR1):** a flat v1 entry (`{"density_g_cm3": 2.7,
"E_gpa": 70, "category": "metal", "notes": "..."}`) remains valid everywhere
(project `materials` section, `~/.agentcad/materials.json`,
`set_project_materials`): it normalizes to v2 points with `basis: typical`,
`unit` canonical, `source: null` (reported as **uncited**, never invented). The
v1 validator's rejections are preserved (unknown keys, density ∈ (0, 25],
non-negative numbers, known category, string label/notes). A card and a v1 entry
are told apart by the presence of a `properties` key; mixing flat numeric keys
with `properties` in one entry is a validation error.

## 2. `Material`, resolved (Decision 2)

`Material` stays a frozen dataclass and **keeps every existing field with its
existing meaning** (`density_g_cm3`, `E_gpa`, …, `cost_usd_kg`, `category`,
`notes`, `source` = provenance layer) — every consumer (`service.material_density`,
`specs._youngs_mpa`, `tools_analysis`, `inspector.js`) keeps reading flat point
values. New fields, all defaulted: `subcategory`, `condition`, `standards`
(tuple), `process` (frozen mapping or `None`), `links` (tuple), `properties`
(a frozen mapping key → `Property`), `warnings` (tuple), the six new numeric
properties, and `library_version`.

A flat field is **the point of its property**: `value`, or the midpoint of
`range`. `Material.prop(key) -> Property | None` is the typed access
(`value|range`, `unit`, `basis`, `source`, `T_c`, `table`). `Property.at(T_c)`
returns `(value, interpolated: bool, clamped: bool)` — linear interpolation
between table rows, clamping to the end rows outside the table (the
SolidWorks/Ansys convention) so a consumer always gets a number plus a flag it
must surface as a warning. No table → the point, `interpolated=False`.

`Material.to_payload(full=False)` keeps today's flat shape (the `list_materials`
contract the UI/tests read) plus `subcategory`, `condition`, `standards`,
`process`, `links`, `warnings`, `basis: {key: basis}` and `uncited: [keys]`.
`full=True` adds `properties` (every property object with `source` and `table`)
and `cost` — the `get_material` payload. The summary stays small enough that
`list_materials` over 330 records is a few hundred KB.

## 3. Data files and library versioning (Decision 3 — Technical approach, FR9)

The builtin library moves from the Python table to
`agentcad/core/materials_data/<category>_<subcategory>.json`, each
`{"schema_version": 2, "materials": {"<id>": <card>}}`, plus
`materials_data/_library.json` = `{"library_version": "2.0.0"}` and
`materials_data/PROVENANCE.md` (AC7 — per-file sources and the
no-aggregator attestation). `materials.py` loads the directory at import into
the same module-level `MATERIALS` dict (name kept: `service.py` and
`share_build.py` import it), validates every card with the **library** lint
profile, and fails loudly (import error naming the file/id/property) rather
than shipping a broken card — a test asserts the shipped data lints clean. The
`_m(...)` rows are deleted; the 30 ids and their densities are pinned by an
extended `test_v1_ids_and_densities_preserved` covering all 30 (AC1: densities
unchanged ⇒ `_cache_key` unchanged ⇒ byte-identical meshes, and the suite's
golden/cache tests are the proof).

**Library versioning (FR9).** `LIBRARY_VERSION` is exported; `Material.
library_version` is set on builtin records. The manifest gains one additive,
atomic-merge key `materials_library` (the version string) written by
`create_project` and refreshed by `set_project_materials`; `list_materials`
reports `{library_version, project_library_version}` and adds a warning
`library_version_newer_than_shipped` when a project pins a version the running
server does not know. What actually preserves byte-stable rebuilds is the
**editorial immutability rule**, stated in `docs/materials.md` and enforced by
the pinned-densities test: a builtin id's density is never changed in place —
a corrected or re-based material gets a new id (or a `condition` suffix) and
the old id stays. Rejected: shipping historical library versions and resolving
against the pinned one — the PRD's ask is "re-resolve identically", and
immutable ids give that without a second resolver.

## 4. Taxonomy (Decision 4 — FR2)

`CATEGORIES` keeps the five existing ids and grows: `metal, polymer, composite,
wood, masonry, ceramic, other`. `masonry` is kept (not renamed to `concrete`)
because user files already validate against it and it is the honest parent of
concrete/mortar/brick/stone. `SUBCATEGORIES` (closed, per category):

- metal: `steel, stainless, tool_steel, cast_iron, aluminum, titanium, copper,
  nickel, magnesium, zinc, other_metal`
- polymer: `commodity, engineering, high_performance, thermoset, elastomer, foam`
- composite: `laminate, sandwich, reinforced_polymer`
- wood: `softwood, hardwood, engineered`
- masonry: `concrete, mortar, brick, stone`
- ceramic: `technical_ceramic, glass`
- other: `other`

A card without `subcategory` is valid for user layers (it resolves to
`subcategory: null`, shown as "unclassified"); the library profile requires it. The launch floor per leaf is
≥5 records for every leaf the examples or the integration tests touch
(`aluminum, steel, stainless, titanium, nickel, copper, commodity, engineering,
high_performance, laminate, softwood, engineered, concrete`) and ≥1 for every
other leaf (a leaf with zero records is a lint warning on the library, not an
error — the taxonomy is allowed to lead the data).

## 5. Process metadata (Decision 5 — G4/FR1)

`process` is an optional object with a closed vocabulary, ratings on one
4-step enum `excellent | good | fair | poor` (absent = not applicable / not
rated — never a fifth value):

- `machinability`, `weldability`: rating
- `printable`: `{fdm|sla|sls|mjf|dmls: rating}` (`dmls` added for the metals the
  library already carries as LPBF records)
- `sheet`: `{k_factor_range: [lo, hi] in (0, 1], min_bend_radius_t: number ≥ 0}`
- `im`: rating (injection moulding; polymers)
- `casting`: rating
- `source`: required when `process` is present — one citation for the block
  (ratings are classifications, not measurements; one honest citation beats ten
  decorative ones).

Consumers: `find_materials` `process` constraint (§6), the browser's process
chips, and PRD-021 later.

## 6. The query engine (Decision 6 — FR6/G5)

`core/materials_query.py` — pure functions over a resolved catalog, used by the
tools, the routes and (through them) the browser. No kernel, no I/O.

**Constraint grammar** (`require` in `find_materials`, `filter` in
`list_materials`): an object whose keys are
- `<property>_min` / `<property>_max` for any known property key (e.g.
  `yield_mpa_min: 240`, `cost_usd_kg_max: 8`, `max_service_temp_c_min: 150`) —
  a range satisfies `_min` by its **lower** bound and `_max` by its **upper**
  bound (conservative: a material qualifies only if its whole range does); a
  material lacking the property **does not qualify** (a missing value is not a
  pass);
- `category`, `subcategory`: exact;
- `process`: one of `cnc | weld | fdm | sla | sls | mjf | dmls | im | sheet |
  casting` — qualifies when the matching rating is `excellent|good|fair`
  (`poor` or absent fails);
- `basis`: restrict to records whose *constraining* properties have this basis
  (e.g. `minimum` — "only spec minima, please").
Unknown keys/props → `validation_error` listing the known grammar.

**Ranking** (`prefer`): an object `{<property>: "min" | "max"}` (e.g.
`{cost_usd_kg: "min", yield_mpa: "max"}`); score = sum of normalized ranks over
the qualifying set, materials missing a preferred property rank last; stable
tie-break by `(category, subcategory, id)`. No `prefer` → the stable order.

**Result row:** `{id, label, category, subcategory, condition, score?,
constraining: {<key>: {value|range, unit, basis, source}}}` — the constraining
values are the cited evidence the PRD's agent path reads back ("yield 240 MPa
(minimum, EN 755-2)").

**Impossible sets:** zero qualifying records → `ValidationError("no material
satisfies the constraints", {nearest_relaxation: {drop: "<key>", count: N},
tried: {...}})` — the single constraint whose removal admits the most records,
computed by leave-one-out (≤ 20 constraints × 330 records; trivial). The
PRD asks for an error with the nearest relaxation named; a tool refusal is a
200 `{"error": …}` envelope, so an agent reads it as data.

**Tools** (in `tools_materials.py`, the existing pack):
- `find_materials {require?, prefer?, category?, limit? (default 10, ≤ 50), project?}`
- `get_material {id, project?}` → `to_payload(full=True)`
- `list_materials {project?, category?, subcategory?, filter?}` — unchanged
  payload shape, plus `library_version`, `project_library_version`, `count`.
Routes (`routes_materials.py`): `GET /api/materials` gains `category`,
`subcategory`, `filter` (JSON-encoded) query params; `GET /api/materials/{id}`;
`POST /api/materials/find` (body = the tool args). Anonymous surface unchanged
(hosted: these stay behind the member gate like today's `GET /api/materials`).

## 7. FEM integration (Decision 7 — FR4/G3)

No kernel change. The **service side** resolves the scalar the solver already
takes, and says how it got it:

- `fem_thermal`: when `k_w_m_k` is not passed, k is `material.prop("k_w_m_k")
  .at(T_eval)` with `T_eval = (t_hot_c + t_cold_c) / 2` (ruling: the mean of the
  two fixed temperatures is the one defensible single temperature for a linear
  steady-state conduction model; documented; an explicit `k_w_m_k` bypasses it
  as today).
- `fem_static`/`fem_modal`: gain `temperature_c` (default 20). `E_mpa` /
  `nu` default to the material's `E_gpa` (at `temperature_c`) and
  `poisson_ratio`; when the material has no E the tool keeps its historical
  fallback (`fem_static`: 210000; `fem_modal`: the existing `validation_error`)
  and no ν → 0.3, each recorded in the result.
- Every FEM result gains `material_basis: {E_mpa?: {value, basis, source,
  T_c, interpolated, clamped}, nu?: {...}, k_w_m_k?: {...}}` and, when a value
  was clamped outside its table, a `warnings` entry
  `temperature_out_of_table_range` naming the property and the table's span.
  `specs._fem_material_key` keys on E (already) — it now reads E through the same
  resolver at 20 °C, so a material whose E *table* changes but whose 20 °C point
  does not leaves the memo untouched (the key is what the solver consumed).

AC3 is tested two ways: a fake-kernel test (the pack registered with
`fem_available` patched true, `service.kernel.request` captured) that proves the
interpolated k, the clamping and the warning without the `[fem]` extra; and an
`importorskip` test that runs the real solver when the extra is present.

## 8. Mass, metrics, cache keys (Decision 8 — FR5/AC1)

`service.material_density` is unchanged (flat point). Ranges never reach mass
in the shipped library (§1); a user-layer range density contributes its
midpoint and `list_materials`/`get_material` show the warning. `_cache_key`'s
payload is untouched. The 30 migrated densities are byte-identical (test).

## 9. The lint (Decision 9 — FR8/FR3/AC5)

`core/materials_lint.py` — pure: `lint_card(id, card, profile) -> [Finding]`,
`lint_file(path, profile)`, `lint_catalog(dict, profile)`. `Finding = {code,
level: error|warning, id, property?, message, file?}`. Profiles:
- `library` (default for the CLI and for loading `materials_data/`): every
  rule an error — schema, closed keys, unit mismatch, **missing citation per
  property** (`missing_citation`, naming the property — AC5), density must be a
  point, subcategory required, `process.source` required, table monotonic,
  point inside table envelope, range `lo <= hi`, **envelope** checks per
  category (density, E, yield, max service temp bands in `ENVELOPES`) are
  warnings, and an **aggregator** check: a `source` mentioning MatWeb,
  MakeItFrom, UL Prospector or Granta is an error (`disallowed_source`, AC7 —
  a primary source must be cited instead).
- `user`: v1 flat entries accepted (uncited = warning `missing_citation`),
  range density a warning, subcategory optional.

CLI: `agentcad materials lint <path>… [--profile library|user] [--json]` —
a path is a card file, a directory of card files, or a `project.json` (lints
its `materials` section with the `user` profile). Exit 0 clean, 1 errors, 2
usage. The shipped library is linted in a test; the community repo's CI would
run the same command.

## 10. Browser UI (Decision 10 — FR7)

`frontend/js/materials.js` (+ CSS in `app.css`): a full-page overlay view in
the `market.js` style, opened by a `Materials` toolbar button, by the inspector
material block's **Browse…** button (assign mode: the chosen material is
written to the current part via `api.updatePart`, the existing path), and by
the `#materials` hash.

Layout: category/subcategory tree (counts) on the left; filter bar (density,
E, yield, max service temp, cost max, process chips — all `filter` grammar,
one `GET /api/materials?filter=…` call per change, debounced); a sortable
table; **Compare** pins 2–4 rows into a side-by-side column view; **Detail**
lists every property with a basis badge (`typical`/`minimum`/`characteristic`),
the source text, the temperature table if any, the process block, the `links`
rendered as outbound anchors (MMPDS/Prospector — never mirrored), the
`uncited` properties marked as such, and the record's `warnings`. The caveat
text stays (it is now the per-value basis's sibling, not its substitute).
Verified in a real browser (screenshot) when the extension is available, else
evidence-graded against the HTTP contracts with tests.

## 11. Curation (Decision 11 — G1/FR3/AC4/AC7)

≥300 cards authored by parallel curation agents, **one file per family**,
standards-first, under these rules (also the CONTRIBUTING text in
`docs/materials.md`):

- Allowed sources: standards values (EN 338, EN 1992-1-1 Table 3.1 / EN 206,
  EN 10025/10088/10083, EN 485/755, ASTM A36/A572/A992/B209/B221/B265/B348,
  ISO 527 datasheets …), NIST/public-domain data, ASM Handbook / Aluminum
  Association / CDA / Wood Handbook (FPL) values cited by volume/table,
  manufacturer datasheets cited fact-by-fact, community measurements labelled
  as such. **Disallowed** (lint error): MatWeb, MakeItFrom, UL Prospector,
  Granta — by name in any `source`.
- **When unsure of a value, omit the property** — a card needs only density
  (+ citation). Ranges over false precision. `basis` honest: standard minima
  → `minimum`; EN characteristic values → `characteristic`; handbook/datasheet
  typicals → `typical`.
- The 30 legacy records are **re-attributed, not re-valued**: every number
  stays byte-identical (the pinned test), the `source` names the primary source
  the value comes from (the v1 table's "MatWeb typical" family comments are
  replaced by ASM/ASTM/EN/manufacturer citations).
- Each family file is linted by its author at `--profile library`; the
  orchestrator runs the suite. A **QA pass** by a separate agent spot-checks
  20 random records against their named sources and writes
  `materials_data/PROVENANCE.md` (per-file source list + the AC7 attestation)
  and `docs/materials.md`'s QA checklist section (AC4).

Target split (≈340 cards): aluminum/magnesium/zinc ~45 · carbon/alloy/tool
steels + cast iron ~50 · stainless/nickel/titanium/copper ~50 · commodity +
engineering polymers ~45 · high-performance polymers + thermosets + elastomers +
foams ~40 · composites + ceramics/glass + other ~35 · wood (EN 338 C/D classes,
species per Wood Handbook, engineered) ~40 · masonry (EN 206 / EN 1992 classes,
ACI, mortar/brick/stone) ~35.

## 12. Errors, docs, acceptance (Decision 12)

- Tool refusals are `validation_error` payloads (the registry's envelope);
  routes map `AppError` to 404/422 as today.
- Docs: `docs/materials.md` (new — schema, taxonomy, basis, sourcing rules /
  CONTRIBUTING, lint, versioning rule, deferrals incl. community repo, package
  distribution, FreeCAD mapping note), `docs/agent-api.md` (Materials section +
  FEM args/results), `docs/user-guide.md` (browser), `AGENTS.md` (materials-v2
  traps: closed keys, density-is-a-point, immutability rule, `library` vs
  `user` profile, `masonry` kept, FEM resolves service-side, no aggregator
  names), `CLAUDE.md` condensed traps, `docs/roadmap.md`.
- Acceptance tests (`tests/test_prd028_acceptance.py`): AC1 30 ids + densities
  + payload compat + a mesh cache-key equality across the migration; AC2 the
  PRD's exact query with every member checked and cited; AC3 fake-kernel +
  importorskip; AC4 `len(MATERIALS) >= 300` and every builtin property cited;
  AC5 lint rejects a missing citation naming the property; AC6 the construction
  example copied, a part re-materialled to `timber_c24` and `concrete_c30_37`,
  mass within ±5 % of density × volume, `fem_static` through the fake-kernel
  path (+ importorskip real); AC7 no aggregator name in any shipped `source`.

## 13. Pack boundaries (Decision 13)

`materials.py` (schema, loader, resolver, interpolation) · `materials_query.py`
(pure query) · `materials_lint.py` (pure lint) · `materials_data/` (cards) ·
`tools_materials.py` (the pack: resolver install + 4 tools) · `tools_analysis.py`
(FEM resolution through `service.materials`) · `routes_materials.py` ·
`cli.py` (`materials lint` sub-command, thin) · `frontend/js/materials.js`.
`core/tools.py`, `service.py` (beyond `create_project` writing the version key
and `_materials_map` tolerating the new fields), `worker.py` untouched.

## 14. Approaches considered and rejected (summary)

- *Keep the Python table and bolt citations onto `_m(...)`* — 340 rows of
  Python is not a contribution format and the PRD's community path needs data
  files; rejected.
- *YAML cards* — a new dependency for no gain; JSON with a JSON Schema in
  `docs/materials.md` is enough.
- *A fourth "allowable_linked" basis* — replaced by record-level `links` (§1).
- *Rename `masonry` → `concrete`* — breaks user files; kept (§4).
- *Ship historical library versions for FR9* — replaced by immutable ids + pin
  reporting (§3).
- *Kernel-side temperature interpolation* — the solver takes scalars; resolving
  service-side keeps "no kernel changes" and gives the tool result the basis
  record (§7).
- *Zero-result `find_materials` as an empty 200* — the PRD specifies a
  `validation_error` with the nearest relaxation; kept as specified (§6).
