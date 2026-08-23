# 0312 — PRD-017 slice 5: 3MF v2 + structured STEP assembly export

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
Two new kernel export handlers and the routing that reaches them: 3MF with
per-solid names/colours and stamped model metadata (FR4/FR5), and STEP
assemblies written as a real product tree instead of one fused compound
(FR2). Both land through `tools_xchange`; no core file changed.

## Changes
- `kernel/handlers/interop.py` — `export_3mf_rich {script?|source_path?,
  params?, items?, out_path, tolerance, name?, metadata{title, designer,
  description, creation_date, part_number}, solid_colors?, default_color?}`
  → `{path, size_bytes, objects, colors: "per_solid"|"none",
  metadata_stamped: [...]}`. Decomposes to `shape.solids()` and stamps
  `.label`/`.color` **before** `Mesher.add_shape` — the spike's D.1 trap
  (`add_shape(Part)` silently drops both, which is why today's 3MF has
  neither). Solid labels are the *same* vocabulary `get_metrics` reports
  (the script's `SOLID_LABELS`, else `solid_<i>`, 0-based; a lone solid
  takes the part's label), so a `solid_colors` map is keyed exactly like
  `solid_materials` — label match > index match > `default_color` > no
  colour at all. `items` is the assembly mode: one placed, named, coloured
  object per instance. Metadata rides the lib3mf API (`PartNumber` in a
  custom namespace + the core `partnumber=` attribute); it is stamped
  **after** the shapes, because `add_meta_data` mints a components object
  when the model has none yet. Unknown metadata keys are refused.
- `kernel/handlers/interop.py` — `export_step_structured {items:
  [{part_id, part_name?, part_color?, source_kind, script?|source_path?,
  params?, name, position, rotation_deg, color?}], out_path, name?}` →
  `{path, size_bytes, schema, products, occurrences}`. One XCAF product per
  unique `part_id` (each shape built once), one component per instance with
  its `b3d.Location(position, rotation_deg)` (intrinsic-XYZ, the house
  convention `worker._place` applies and the importer reads back), names via
  `TDataStd_Name`, colours via `XCAFDoc_ColorTool` in **sRGB**
  (`Quantity_TOC_sRGB`, the mirror of the import side's `Values` read).
  Atomic tmp+`os.replace`, AP242 asserted from the written header.
- Two traps this export owns, both measured and both now tests:
  **(1)** a single-solid product must be added as a `TopoDS_Solid` — as the
  single-solid `TopoDS_Compound` every build123d part is, OCCT's writer
  silently drops every *per-occurrence* colour override; `_product_shape`
  unwraps (and re-delocates) it. **(2)** a genuinely multi-solid product
  keeps its compound, and its colour is then written **per solid**, which
  our own `inspect_cad_tree` reports as `color: None` on the product and the
  occurrence — recorded rather than worked around, since the fix (a product
  per (part, colour) pair) would inflate the product count.
- `_write_assembly_ap242` repeats `_pmi_map.write_ap242`'s trap 2
  (construct the writer, THEN set the static, assert the setter, restore in
  `finally`) because it also needs `write.step.assembly = 1`. Both statics
  are process-wide and both are restored.
- `core/tools_xchange.py` — part `3mf` now routes to `export_3mf_rich`
  (metadata precedence: explicit `metadata` per key > part label / BOM
  `part_number` / `designer: "AgentCAD"` / version date); assembly gains
  `3mf` and `export_assembly {format: "step", structured: true}`, both fed
  by `_structured_items` (`_resolved_instances` + `_record_for` +
  `_shape_item` — the PRD-013-expanded seam, NOT `get_assembly`, which
  carries meshes where a product needs a script). `structured` defaults to
  **false**: the fused export is untouched unless asked for, and
  `structured` on a non-STEP format is a refusal. Fidelity:
  `{"geometry": "mesh", "colors": …, "metadata": "attached"}` for 3MF and
  `{"geometry": "brep", "structure": "tree", "colors": "per_instance"}` for
  the structured STEP (no `pmi` axis — the PMI writer is the single-part
  path, and `pmi: "none"` there would read as "your PMI was dropped").
- **`CreationDate` is PRD-014's version date**, resolved through
  `tools_drawing._drawing_version` (a tag or the HEAD sha, and HEAD's commit
  date) — the same string a drawing's title block prints for that state.
  Never `datetime.now()`; a project with no history resolves to `"-"`, which
  is not a date, so the field is **omitted** rather than stamped.
- Schemas mutated in place: `export_assembly` gains the `3mf` enum entry and
  `structured?`, **and its handler is rebound** (the registered lambda took
  two arguments); `export_part`'s `metadata` description now names the keys.

## Files
- `agentcad/kernel/handlers/interop.py`, `agentcad/core/tools_xchange.py` —
  extended
- `tests/test_interop_3mf.py` (21), `tests/test_interop_step_asm.py` (15) — new
- `tests/test_xchange_pack.py` — three slice-4 tests updated where slice 5
  deliberately changes the contract (assembly `3mf`/`structured` are now
  advertised and run; a part `3mf` is no longer a delegated format)

## Notes
3MF is **not** byte-deterministic and never will be — lib3mf mints a fresh
`p:UUID` per object per write. The determinism test asserts the model XML is
identical once production-namespace UUIDs are stripped, which is what makes
the version-date `CreationDate` meaningful. A fused STEP export already
carries NAUOs, so what `structured: true` actually buys is *identity*: the
fused file's products are called `COMPOUND` and its occurrences are XCAF
label paths, with no colours — pinned by a test that reads both files back.

`make test` — 5546 passed, 40 skipped (14:45); 3 non-passing: the pre-existing local-only prd028 AC6 solver timeout (skips on CI), the supervisor ballooning-kill load flake, and a share-isolation setup timeout that was collateral of the ballooning test's worker restart on the same pool (memory-cap crash visible in the log directly above it; 3/3 pass in 23 s in isolation).
