# PRD-017 Interop pack — implementation plan

Design: `docs/superpowers/specs/2026-08-23-interop-pack-design.md`. Spike
evidence (recipes, traps, raw output):
`docs/superpowers/specs/2026-08-23-interop-pack-spike.md`. TDD per slice; the
controller (not subagents) runs `make test` and commits, one changelog per
commit citing the full-suite count. Subagents never run mutating git or
`uv sync`. Kernel slices split across **two handler files** so they
parallelize: `handlers/interop.py` (exports) and `handlers/interop_import.py`
(import).

## Wave 1 (parallel — disjoint files)

### Slice 1 — AP242 PMI export (FR1, FR3) — **Opus**
- `kernel/handlers/_pmi_map.py`: our PMI model → XCAF mapping owning the six
  spike traps (de-locate; writer-first-then-`AP242DIS`-and-assert;
  `DatumObject.SetPosition`; ≥1 dimension or auxiliary bbox size dim +
  `pmi_notes`; magnitude tolerances; segfault blocklist as refusals).
  Deterministic face targeting per spec §1; unmappable → `pmi_skipped`.
- `kernel/handlers/interop.py`: `export_step_pmi` handler (XCAF doc, write,
  re-open + assert `FILE_SCHEMA` contains AP242) and `read_step_pmi` (the
  round-trip reader used by tests: `SetGDTMode(True)`, enumerate by
  (type, value, tol, target); datum *names*, not label counts).
- Tests (`tests/test_interop_pmi.py`): round-trip dims/datums/all-5 FCFs
  with values + unit assertion (AC1 machine half); `pmi_skipped` exercised
  (diameter dim on a box with no cylindrical face — AC6); no-dims→auxiliary
  dimension + note; blocklist refusal; parts without PMI byte-match today's
  export path.

### Slice 2 — structured import, kernel half (FR8 kernel) — **Opus**
- `kernel/handlers/interop_import.py`: `inspect_cad_tree` + `import_structured`
  per spec §3 — XCAF walk (referred-label rules, color precedence +
  `Values(Quantity_TOC_sRGB)`, component-label-path identity,
  `gp_Quaternion.GetEulerAngles(gp_Intrinsic_XYZ)` transforms), per-product
  `.brep` materialization (atomic, deterministic names).
- Test helper: author a nested/colored multi-product STEP fixture via raw
  XCAF (spike script C is the template) through the kernel — in-suite, no
  binary blobs.
- Tests (`tests/test_interop_import_kernel.py`): tree counts, dedup (N
  occurrences → 1 product), composed-transform spot checks (the spike's
  (0,80,10) case), color override vs product color, sRGB correctness,
  `.brep` files load through `refload`.

## Wave 2 (parallel — disjoint files; blocked by wave 1)

### Slice 3 — structured import, server half (FR8–FR10) — **Opus** (needs S2)
- `tools_import.py`: `structured?` (auto = >1 occurrence)/`prefix?`;
  structured landing — N reference parts (ID_RE sanitize + prefix +
  deterministic `_2` suffixing, `source_label`/`import_source` loose keys),
  instances appended, **one manifest write**; result
  `{parts, instances, tree, warnings, fidelity}`; `part_id` required only
  for flat; STL always flat.
- `routes_import.py`: `POST /projects/{p}/imports/{name}/preview`.
- `manifest_merge` check/tests for the new loose keys.
- Tests (`tests/test_interop_import.py`): AC2 (dedup set + all instances +
  transforms + names), flat default unchanged for single-product, forced
  flat/structured both ways, collision suffixing, hosted-mode host-path
  guard still holds, preview route (+ anonymous-surface non-exposure).

### Slice 4 — glTF/GLB + colors + fidelity plumbing (FR6–FR7, FR12) — **Opus** (needs S1 for the pack file only — creates `tools_xchange.py`)
- `core/interop_colors.py`: `color_for` + `CATEGORY_COLORS` + `srgb_to_linear`.
- `core/gltf.py`: ACM1→glTF/GLB per spec §6 (mesh dedup by `mesh_key`, root
  −90°X node + `asset.extras`, sorted/stable/rounded output, GLB container).
- `core/tools_xchange.py`: the pack (docstring records the load-order
  rationale) — wraps `service.export_part`/`export_assembly` (capture the
  *final* methods; `_WRAPPED` sentinel), routes `gltf`/`glb` server-side and
  `step`+PMI to `export_step_pmi`, mutates the registered tools'
  `input_schema` (format enums + `pmi?`/`metadata?`/`structured?`;
  `import_cad_file` `structured?`/`prefix?`, `part_id` un-required),
  attaches `fidelity` on all export paths (spec §8 shape).
- Tests (`tests/test_interop_gltf.py`, `tests/test_xchange_pack.py`): GLB
  structural validation + byte-identical two-export sha (AC3 machine half);
  Z-up→Y-up pose test; color precedence incl. category map + linear
  conversion; schema mutation visible in `GET /api/tools`; fidelity present
  on every export; delegation leaves step/stl/3mf results identical;
  existing suites (PRD-013 export expansion) still green.

## Wave 3 (parallel — disjoint files; blocked by wave 2)

### Slice 5 — 3MF v2 + structured STEP assembly export (FR2, FR4–FR5) — **Opus**
- `kernel/handlers/interop.py` grows `export_3mf_rich` (solids decomposition,
  `.label`/`.color` per solid, Mesher metadata incl. `CreationDate` from the
  version date arg) and `export_step_structured` (XCAF products/components/
  names/colors, AP242, dedup by part identity).
- `tools_xchange.py`: route `3mf` (part + assembly) and
  `export_assembly {structured: true}`; metadata precedence per spec §4.
- Tests (`tests/test_interop_3mf.py`, `tests/test_interop_step_asm.py`):
  3MF OPC/XML conformance + lib3mf re-read + per-solid colors + mm units +
  metadata + part_number from `bom` (AC4 machine half); structured export →
  `inspect_cad_tree` round-trip (products/occurrences/names/transforms);
  `structured: false` byte-preserves today's fused output.

### Slice 6 — frontend (Experience) — **Sonnet**
- Import preview dialog (`dialogs.register`), prefix field, flat toggle;
  single-product flow unchanged. `EXPORTS` + conditional `usd` from tool
  schema; "include PMI" toggle on STEP part export when PMI exists.
- Browser-verify via Playwright + installed Chrome against `agentcad serve`
  (structured import of a fixture → tree renders; GLB export toast).
  Files: `frontend/js/main.js`, new `frontend/js/import_dialog.js` (or
  inline per shell idiom), `frontend/index.html`/`app.css` only if needed.

## Wave 4

### Slice 7 — USD (FR11) — **Opus** (needs S4)
- `pyproject.toml` `usd` extra with the linux-aarch64-excluding marker;
  `uv.lock` regenerated by the **controller** (`uv lock`), and
  `make test-linux`'s sync must stay green.
- `core/usd_export.py`: `usd_available()` + stage writer (spec §10);
  `tools_xchange` conditional enum entry + route behavior.
- Tests (`tests/test_interop_usd.py`): gating both ways (monkeypatch
  pattern from `tests/test_analysis.py`); `importorskip("pxr")` stage
  content checks (mesh counts, metersPerUnit, upAxis, displayColor).

### Slice 8 — acceptance + docs — **Opus tests, Sonnet docs** (needs all)
- `tests/test_prd017_acceptance.py`: AC1–AC7 machine halves (manual
  FreeCAD/PrusaSlicer checks recorded as per-release notes in docs).
- Docs: `docs/agent-api.md` (export/import args, fidelity contract,
  translation matrix), `docs/user-guide.md` (export menu, import preview,
  USD platform matrix), `docs/architecture.md` (interop packs, format-enum
  mutation mechanism, glTF Y-up note beside ACM1/transform sections),
  `AGENTS.md` + `CLAUDE.md` trap list (the six AP242 traps condensed, sRGB
  vs linear, `tools_xchange` load order, 3MF non-determinism, usd wheel
  matrix), changelog.

## Non-negotiables
- Cores untouched (`worker.py`/`tools.py`/`app.py`/`service.py`); all growth
  via the two kernel packs + `tools_xchange.py` + wrappers.
- Only the kernel imports OCP; `core/gltf.py`/`usd_export.py`/OPC step are
  OCP-free (assert like `bench/**`).
- Determinism: GLB byte-identical (test), 3MF explicitly not (documented);
  no timestamps except version-date `CreationDate`.
- Segfault blocklist is a refusal path, never reachable as a crash.
- Fidelity on every interop result; skips reported, never silent.
- Full suite per slice; changelog per commit citing the count; subagents
  never git/uv sync; `uv lock` runs only in slice 7 by the controller.
