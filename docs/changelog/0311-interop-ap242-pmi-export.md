# 0311 — PRD-017 slice 1: STEP AP242 PMI export (kernel)

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
New kernel handler pack exporting a part's PMI (`core/pmi.py` model: dims,
datums, FCFs) into STEP AP242 via OCCT XCAF, plus the round-trip reader the
tests and later slices use. FR1/FR3 of PRD-017.

## Changes
- `export_step_pmi {script?|source_path?, params?, pmi, out_path, name?}` →
  `{path, size_bytes, schema, pmi_attached, pmi_skipped, pmi_notes}`; atomic
  write, temp unlinked on failure so an AP214/torn file never lands; STL
  references refused (`contract_error`).
- `read_step_pmi {path}` — STEPCAFControl_Reader (`SetGDTMode`), entries
  matched by (type, value, tolerance, target descriptor) — PMI identity does
  not survive the writer; datums compared by deduplicated **names**.
- `_pmi_map.py` owns the six spike traps, each mutation-verified by a test:
  (1) locations **baked** via `BRepBuilderAPI_Transform` (the spike's
  `.Located(identity)` recipe silently teleports an off-origin part);
  (2) writer constructed before `write.step.schema = AP242DIS`, setter
  asserted, **and the static restored after the write** (process-global — a
  warm worker would otherwise re-schema every later plain STEP export);
  (3) `DatumObject.SetPosition` always set (`min(index+1, 3)` — ASME's three
  slots); (4) a dimension-less document mints METRE units, so FCF-only PMI
  gets one untoleranced auxiliary bbox-size dimension + a `pmi_notes` entry;
  (5) tolerances passed as magnitudes (writer negates; checked against the
  raw STEP text since the reader hides the sign); (6)
  `Location_WithPath`/`Size_WithPath`/`Location_Oriented`(2-target)/angular
  dims blocklisted as `pmi_skipped` refusals — they segfault the writer.
- Deterministic face targeting: datum face selector → largest matching
  planar face; diameter dims prefer the cylindrical face matching the
  declared nominal (±0.05 mm) before falling back to the largest; a
  `position` FCF gets the diameter zone modifier (ASME); an FCF referencing
  a skipped datum still emits, with a `pmi_notes` honesty row.
- All OCCT transfer chatter redirected to stderr (protocol stream safety).

## Files
- `agentcad/kernel/handlers/_pmi_map.py` — new (mapping layer + AP242 writer)
- `agentcad/kernel/handlers/interop.py` — new handler pack
- `tests/test_interop_pmi.py` — new (19 tests)

## Notes
Parts without PMI keep today's `b3d.export_step` path untouched (asserted).
`pmi_skipped` reasons are stable `token: prose` strings. Tool-layer fidelity
wiring is slice 4. `make test` — 5406 passed, 40 skipped in the recorded run; the 23 non-passing items were: a machine-load hang that burned `test_service`'s 120 s timeouts (19/19 pass in 3.6 s in isolation), transient supervisor/count-guard items that re-run green, and the pre-existing local-only `test_prd028_acceptance` AC6 real-solver timeout (fails identically on a tree without this branch's packs; skips on CI where `[fem]` is absent).
