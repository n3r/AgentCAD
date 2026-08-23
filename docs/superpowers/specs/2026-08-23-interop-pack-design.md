# PRD-017 Interop pack — design spec

Grounded in a full seam map (export/import tools + kernel writers, PMI model,
ACM cache, manifest, extension-point contract) and an executed OCCT 7.9.3
capability spike (scripts + raw output preserved at the session scratchpad,
findings reproduced below where load-bearing). PRD:
`docs/prd/in-progress/PRD-017-interop-pack.md`. Slice plan: sibling
`docs/superpowers/plans/2026-08-23-interop-pack.md`.

## Scope ruling

**Build MVP + Phase 2 + USD:** FR1–FR9, FR11, FR12 in full; FR10 in its v1
form (deep trees flatten to one instance level, transforms composed).
**Deferred, recorded here:** `structured: "nested"` import onto PRD-013
sub-assembly sources (a per-product project/sub-assembly authoring problem —
its own design), and PMI *import* (PRD's own Phase-3 exploration). The spike
showed FR1's coverage is far better than the PRD's risk section assumed, so
FR1 ships dims + datums + **all five** of our FCF types, not a subset.

## Corrections to the PRD's technical approach (from the seam map)

- **`tools_interop.py` "re-registers extended schemas" cannot work** —
  `ToolRegistry.register` raises `ValueError` on a duplicate name and no
  overwrite mechanism exists. The codebase idiom for extending a core verb
  from a pack is the `tools_structure.py` one: capture and **wrap the service
  method**, and mutate the already-registered `Tool.input_schema` in place
  (Decision 2).
- **`service.export_assembly` is already monkeypatched** by
  `tools_structure._install_expansion` (PRD-013), which *replaces* rather than
  delegates. Our pack must therefore load **after** `tools_structure` — the
  pack is **`tools_xchange.py`** (`x` sorts after `structure`/`undo`/
  `versioning`; the module docstring records this, house pattern).
- **No material-category→color mapping exists anywhere.** Instance colors are
  author-set `#rrggbb` on `InstanceSpec` plus a frontend fallback palette.
  FR4/FR6 colors need a new, small, deterministic mapping (Decision 5).
- **3MF is written by lib3mf 2.5.0** (official Consortium library) via
  `build123d.Mesher` — no OPC post-step is needed for metadata, units,
  names, or per-solid colors (spike D). OCP has **no** 3MF writer.
- `part_number` lives at `entry["bom"]["part_number"]` (PRD-015 loose key);
  PMI at `entry["pmi"]`. New provenance fields follow the same
  schema-tolerant loose-key pattern (Decision 7).
- The import handler pack split (`handlers/interop.py` for exports,
  `handlers/interop_import.py` for import) exists so the two kernel slices
  can be built in parallel without sharing a file.

## 1. STEP AP242 + PMI export (FR1, FR3) — Decision 1

A new kernel handler pack `agentcad/kernel/handlers/interop.py` registers
`export_step_pmi` (used by the server when `format=step` and the part has PMI
with `pmi != false`; parts without PMI keep today's `b3d.export_step` path
byte-for-byte). It builds the shape, puts it in an XCAF document, maps our
normalized PMI model (`core/pmi.py`: dims linear/diameter, datums A–Z on six
box faces, FCFs flatness/position/perpendicularity/parallelism/cylindricity)
via `XCAFDoc_DimTolTool`, writes AP242.

The mapping layer (`kernel/handlers/_pmi_map.py`, pure mapping + OCP) **owns
the six spike traps as hard rules**:

1. **De-locate before `AddShape`**: `shape.wrapped.Located(TopLoc_Location())`
   — a located shape yields a reference label and null sub-shape labels
   (silent `SetDatum` failure).
2. **Construct `STEPCAFControl_Writer` first, then set
   `Interface_Static write.step.schema = "AP242DIS"` and assert the setter
   returned True** — set before construction it is a silent no-op and the
   file is AP214 with zero PMI. The handler also re-opens the written file
   and asserts the `FILE_SCHEMA` line contains `AP242` (cheap, honest).
3. **`XCAFDimTolObjects_DatumObject.SetPosition(n)` (1-based) always** —
   without it every datum-referencing FCF is silently dropped.
4. **Always ≥1 dimension in the document** — a dimension-less document mints
   METRE units for tolerance measures (silent ×1000). If the part's PMI has
   FCFs/datums but no dims, emit one untoleranced auxiliary
   `DIMENSIONAL_SIZE` (overall bbox size — true information) and record
   `fidelity.pmi_notes: ["auxiliary overall-size dimension emitted to pin
   millimetre units"]`.
5. **Tolerances are passed as magnitudes** (`SetLowerTolValue(+minus)`) — the
   writer negates; a signed value writes a standards-incorrect file our own
   round-trip would not catch.
6. **Blocklist `Location_WithPath` / `Size_WithPath` / two-target
   `Location_Oriented`** dimension types (segfault the writer, exit 139).
   Our model cannot express them today; the blocklist is a defensive assert
   in the mapping layer so a future model extension fails as a
   `pmi_skipped` row, never a dead worker. Angular dims (radians/degrees
   asymmetry) are likewise refused defensively.

Face targeting (deterministic): a datum's `face` selector (top/bottom/…)
resolves to the largest planar face whose outward normal matches the axis
(the same box-face semantics `core/pmi.py` documents); linear dims map to
`Size_*` (one target face — `Location_*` needs two labels and reads back as
nothing with one); diameter dims and cylindricity target the largest
cylindrical face, and **skip with a `pmi_skipped` reason** when none exists;
an FCF attaches to its first referenced datum's face, else the largest planar
face. Everything not mappable lands in `fidelity.pmi_skipped:
[{id, reason}]` — never silently dropped (FR3).

The round-trip test (AC1) reads the file back with `STEPCAFControl_Reader`
(`SetGDTMode(True)` — the reader has no `SetDimTolMode`) and matches entries
by **(type, value, tolerance, target)** — PMI entry identity does not survive
the writer (labels are overwritten by STEP type keywords), and a two-datum
FCF reads back as three datum labels, so the test asserts on datum *names*.
A unit assertion (values in mm, not ×1000) is part of the round trip.

## 2. Tool surface: the `tools_xchange.py` pack — Decision 2

Loads last alphabetically (after `tools_structure`'s export_assembly
replacement and `tools_versioning`). At `register(registry, service)` it:

- Wraps `service.export_part`: new formats `gltf`/`glb` (+`usd` when
  available) route server-side; `format=step` with part PMI routes to
  `export_step_pmi` (kernel), `pmi: false` opts out; `3mf` gains
  `metadata` stamping (Decision 4); everything else delegates to the
  captured original. `EXPORT_FORMATS` in `service.py` is untouched — the
  wrapper owns the extended format check and raises the same
  `validation_error` shape for unknown formats.
- Wraps `service.export_assembly` (the *final*, PRD-013-expanded version):
  adds `structured` (FR2), `gltf`/`glb`/`3mf` (+`usd`), delegates
  `step`(flat)/`stl` untouched. Guarded by a `_WRAPPED` sentinel for
  idempotent double-registration, same as `tools_structure`.
- **Mutates the registered tools' `input_schema` in place**: `export_part`
  format enum `[step, stl, 3mf, gltf, glb]` (+`usd` iff `usd_available()`)
  plus `pmi?: boolean` and `metadata?: object`; `export_assembly` enum
  likewise plus `structured?: boolean`; `import_cad_file` gains
  `structured?: boolean` and `prefix?: string`, and `part_id` moves out of
  `required` (needed only for flat imports — the runtime check stays). A
  test asserts the mutation is visible in `GET /api/tools` (AC5's
  gating check reads this surface). This is the first in-place schema
  mutation in the codebase — the test is the contract.
- Registers no gate provider; touches no core file.

REST: the two direct `app.py` export routes call the service methods, so the
wrappers cover them for free. Fidelity (Decision 8) is attached by the
wrappers and by `import_cad_file`.

## 3. Structured assembly-STEP import (FR8–FR10) — Decision 3

**Import materializes one `.brep` file per unique product.** The kernel pack
`handlers/interop_import.py` registers:

- `inspect_cad_tree {source_path}` — read-only XCAF walk: unique products
  (names, colors), occurrence tree (names, composed transforms, color
  overrides), counts. Serves the preview endpoint and the auto-detect.
- `import_structured {source_path, out_dir}` — same walk, plus: writes each
  unique product's shape to `<stem>__<n>_<sanitized-product>.brep`
  (atomic tmp+rename, deterministic names), returns
  `{products: [{name, file, color}], occurrences: [{product_index, name,
  position, rotation_deg, color}], tree, warnings}`.

Why `.brep` materialization: each imported part becomes a **plain reference
part** through the existing, tested pipeline (`refload` already reads
`.brep`; content-addressed mesh cache, STL-boolean rules, LOD — all
unchanged). No `refload` cache-key change, no multi-product selector state.
Exact B-rep is preserved. Rejected: a `(file, product_path)` selector on the
reference record — it threads a new axis through `refload`'s cache key,
`_content_signature`, and every reference call site for no fidelity gain.

XCAF walk rules (spike C, verbatim): ask `IsAssembly_s`/`IsSimpleShape_s` of
the **referred** label (a component label answers False to both); instance
name/color-override live on the component label, product name/color on the
referred label; color precedence `ColorSurf → ColorGen → ColorCurv` at each
of (component, referred); colors are read via
`XCAFDoc_ColorTool.GetColor_s` (the label overloads are static-only in OCP)
and converted with `Quantity_Color.Values(Quantity_TOC_sRGB)` — `.Red()` et
al. return **linear** values and would darken every imported color; instance
identity derives from the **component-label path**, never the leaf label
(one product's label is shared by all its occurrences); transforms come out
as composed `gp_Trsf` → position + intrinsic-XYZ Euler degrees via
`gp_Quaternion.GetEulerAngles(gp_Intrinsic_XYZ)` (the house rotation
convention); `xstep.cascade.unit` stays at its `MM` default (process-global
static — never changed per-call).

Server side (`tools_import.py` grows; `routes_import.py` gains the preview):

- `import_cad_file {structured?}`: default **auto** — structured when the
  file contains >1 product occurrence (via `inspect_cad_tree`), flat
  otherwise; `structured: false` forces today's single-blob path (which
  stays byte-for-byte for single-product files either way — FR9);
  `structured: true` on a single-product file is honored (1 part, 1
  instance). STL is always flat (a mesh has no product tree).
- Structured landing: N `create_part(kind="reference")` calls (ids =
  sanitized product names through `ID_RE`, `prefix?` prepended, collisions
  suffixed `_2, _3, …` deterministically; original label kept as
  `entry["source_label"]` loose key), then assembly instances appended with
  transforms/colors (instance ids from occurrence names, same sanitize +
  suffix rules). **One manifest write** (single undo step, one
  `project_changed`).
- Result: `{parts, instances, tree, warnings, fidelity}` (FR9). `part_id`
  remains required for flat imports (`validation_error` otherwise).
- `POST /api/projects/{p}/imports/{name}/preview` → `inspect_cad_tree`
  result for the dialog (no writes; name passes `safe_import_name`).
- The 100 MB and extension guards are untouched. Import scale: N products
  register serially through the existing per-part build path (kernel pool
  affinity by part id) — measured in tests at the fixture scale; the guard
  is not raised.

## 4. 3MF v2 (FR4–FR5) — Decision 4

A new kernel handler `export_3mf_rich` (in `handlers/interop.py`; the
worker core's plain 3MF branch stays untouched for the legacy path)
decomposes to
`shape.solids()` and sets `.label`/`.color` per solid before
`Mesher.add_shape` — the spike proved `add_shape(Part)` silently drops names
and colors while `add_shape(Solid)` emits `name=`, `partnumber=`, and a
conformant per-solid `<basematerials>` group. Metadata
(`Title`/`Designer`/`Description`/`CreationDate` + `PartNumber`) rides the
Mesher API; `CreationDate` comes from the **version date** passed by the
server (determinism — never wall clock). The server passes `metadata` from:
explicit `metadata` arg > `entry["bom"]["part_number"]` + part label +
project name. Assembly 3MF (`export_assembly {format: "3mf"}`) exports one
object per instance with instance colors, placed transforms baked.
Colors: instance/solid explicit color first, else the category map
(Decision 5). Units are already explicit millimeters (lib3mf default,
asserted in tests).

**3MF is not byte-deterministic** (lib3mf mints ~9 random `p:UUID`s per
file; zip timestamps drift) — recorded in docs and `fidelity`, no content
hashing of 3MF anywhere (the DXF precedent). Conformance test: unzip +
XML-assert core-spec structure (unit, metadata, basematerials wiring) and
re-read via the venv's own lib3mf; PrusaSlicer opening stays a
manual-per-release check (AC4).

## 5. Colors: one deterministic category map — Decision 5

`core/interop_colors.py`: `color_for(record, instance) -> "#rrggbb"` —
explicit instance/solid color wins; else a fixed `CATEGORY_COLORS` map from
the part material's category (metal→silver-gray family per subcategory,
polymer→off-white, wood→tan, masonry→gray, other→neutral); else
`#98a2ad` (the viewport default). Pure, closed, tested. Used by glTF, USD,
3MF, and structured STEP export alike — one mapping, four consumers.
sRGB→linear conversion for glTF/USD lives beside it (`srgb_to_linear`),
because glTF `baseColorFactor` is linear (spike E) and storing linear values
as sRGB (or vice versa) is the classic silent-darkening bug.

## 6. glTF/GLB (FR6–FR7) — Decision 6

`agentcad/core/gltf.py` — pure Python, no OCP, no new dependency: reads the
part/instances' **ACM1 cache buffers** (positions/normals/indices; the parse
mirror of `kernel/acm.py` already exists in two places — this is the third,
kept minimal and tested against `acm.pack`), builds glTF 2.0 JSON + one
binary buffer, GLB container preferred. Meshes are **deduplicated by
`mesh_key`** (8 screws = 1 mesh, 8 nodes). Z-up→Y-up is **one root node**
with a fixed −90° X quaternion; `asset.extras` states
`{"source_up_axis": "+Z", "converted_to": "+Y"}` — never per-caller flags.
Nodes: one per instance, sorted by instance id (stable), translation in mm,
rotation quaternion from intrinsic-XYZ Euler. Materials: PBR
`baseColorFactor` from Decision 5 (linear), metallic/roughness by category
(metal 0.9/0.4, else 0.0/0.8). Determinism (FR7): `sort_keys`, fixed float
formatting (`repr`-stable via round to 6 like `score.json`), stable
ordering, no timestamps — **two exports at the same state are
byte-identical** (sha test, the PRD-014 pattern), GLB proven deterministic
in the spike. Export requires built meshes: the wrapper ensures instances
are built (the same path `get_assembly` uses) before conversion.
Validation: structural asserts in tests (magic, chunk alignment, accessor
bounds) + the vendored Three.js loader in the browser check (AC3); no
external validator dependency.

## 7. Manifest & provenance — Decision 7

Reference parts gain loose keys (never `PartRecord` fields — the
`pmi`/`bom` pattern, schema-tolerant, old manifests load):
`source_label` (original STEP product name) and `import_source` (the
uploaded filename). Instances imported structurally carry only standard
`InstanceSpec` fields. **No timestamps, no absolute paths** in any of it.
`manifest_merge`: verify scalar part keys merge cleanly (they ride the
part-entry merge); add tests, and if a keyed guard is needed it follows the
`bom` handling.

## 8. Fidelity (FR12) — Decision 8

Every interop result carries `fidelity`, attached at the tool layer:
exports — `{"geometry": "brep"|"mesh", "pmi": "attached"|"none"|"opted_out",
"pmi_skipped": [{id, reason}], "pmi_notes": [...], "colors":
"per_instance"|"per_solid"|"none", "metadata": "attached"|"none",
"parametric": "none"}` (keys present only when the format can carry the
axis; `parametric: "none"` always present — the honesty line). Imports —
`{"geometry": "brep"|"mesh", "structure": "tree"|"flat", "colors": ...,
"pmi": "not_read", "parametric": "none"}`. The docs state the translation
matrix plainly (FR12): exact B-rep — STEP only; PMI — STEP AP242 export
only; tessellation+colors+metadata — 3MF/glTF/USD; parametric intent —
survives in no neutral format.

## 9. Structured STEP assembly export (FR2)

`export_assembly {format: "step", structured: true}` sends the **expanded
per-instance item list** (the PRD-013 wrapper's own graph: part sources,
names, transforms, colors) to a new kernel handler `export_step_structured`
(in `handlers/interop.py`): XCAF document, one product per unique part
(deduplicated by part identity), one component per instance
(`XCAFDoc_ShapeTool` components with locations), instance names, colors via
`XCAFDoc_ColorTool` (sRGB set, per Decision 5), AP242 schema (same
writer-first-then-static rule). `structured: false` (default) preserves
today's fused-compound behavior byte-for-byte. Round-trip test: export
structured → re-read with our own `inspect_cad_tree` → product/occurrence
counts, names, transforms match.

## 10. USD (FR11) — Decision 9

`pyproject.toml` gains `[project.optional-dependencies] usd = ["usd-core>=26.8"]`
**with an environment marker excluding linux-aarch64** (usd-core ships no
such wheel; without the marker `uv sync` — and `make test-linux`, which runs
arm64 — breaks). `core/usd_export.py`: `usd_available()` via
`importlib.util.find_spec("pxr")` (the FEM twin); a pure-pxr, server-side
stage writer from the same ACM buffers + Decision 5 colors: one stage,
`metersPerUnit = 0.001` and `upAxis = "Z"` **declared** (USD natively
supports both — no conversion, the declaration is the honesty), Xform per
instance, Mesh per unique part, displayColor per instance. Registration:
the format enum entry and route behavior appear **only when
`usd_available()`** (AC5, the FEM gating pattern — agents never see a tool
that cannot run). Tests `importorskip("pxr")` for the positive half; the
gating test runs everywhere. Docs state the platform matrix (no
linux-aarch64) so nobody discovers it at pip-install time.

## 11. Frontend — Decision 10

- **Import preview dialog** (`dialogs.register("import-preview", ...)`): on
  upload, call the preview endpoint; if >1 occurrence, show the product
  tree with counts ("14 products, 41 occurrences"), a prefix field, and an
  "import flat" toggle (flat falls back to today's part-id prompt); confirm
  → `import_cad_file {structured, prefix}`. Single-product files keep
  today's prompt exactly.
- **Export menu**: `EXPORTS` array in `main.js` gains part `gltf`/`glb`/
  assembly `gltf`/`glb`/`3mf`; `usd` entries are added conditionally from
  the tool schema (capability-honest — absent when the extra is absent,
  zero special-casing beyond reading the enum). STEP part export exposes an
  "include PMI" default-on toggle only when the part has PMI.
- Browser verification via Playwright + installed Chrome against
  `agentcad serve` (the PRD-026 pattern) if the extension is unavailable.

## 12. Testing strategy

Golden fixtures authored **via build123d through the real kernel** in-suite
(the `test_reference.py::_make_step` idiom — no binary blobs in-repo): a
multi-product/nested/colored assembly STEP (built by our own structured
export once it exists; bootstrap fixture written via raw XCAF in a helper),
a toleranced part (dims + datum + flatness + position FCFs). Tests: AP242
round-trip by (type, value, tol, target) + unit assertion + `pmi_skipped`
path (AC1, AC6); structured import counts/dedup/composed transforms/names +
flat-mode unchanged + collision suffixing (AC2); GLB structural validation +
byte-identical determinism cross-state (AC3); 3MF XML/OPC conformance +
lib3mf re-read + per-solid colors + mm units + metadata (AC4); usd gating
both ways + importorskip'd stage validation (AC5); fidelity presence on
every path (AC6); full-suite regression (AC7). Kernel-crash tests (the
segfault blocklist) assert the refusal, never invoke the crash.

## 13. What does not change

`worker.py`/`tools.py`/`app.py`/`service.py` cores (all growth via packs and
wrappers); `EXPORT_FORMATS`; the flat import path for single-product files;
`refload`'s cache key; STL boolean blocking; the 100 MB/extension guards;
ACM1; the drawing PMI renderer; `set_part_pmi` validation.
