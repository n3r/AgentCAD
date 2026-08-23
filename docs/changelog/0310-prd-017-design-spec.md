# 0310 — PRD-017 interop pack: design spec + slice plan; PRD moved to in-progress

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (orchestrator) / Nikita Fedorov

## Summary
Design groundwork for PRD-017 (neutral-format interop: STEP AP242 PMI,
structured assembly-STEP import, glTF/GLB, 3MF v2, USD-behind-extra,
fidelity reporting). No code changes — spec, executed-spike evidence, and
the slice plan, plus the PRD moved to in-progress.

## Changes
- Design spec recording scope (MVP + Phase 2 + USD; FR10 v1 flatten; nested
  import and PMI import deferred) and the corrections to the PRD's technical
  approach found by the seam map: `tools_interop.py` re-registration cannot
  work (duplicate-name `ValueError`) — the pack is **`tools_xchange.py`**
  (wraps service methods, mutates registered tool schemas in place, loads
  after `tools_structure`'s `export_assembly` replacement); no
  material→color mapping exists today (new `core/interop_colors.py`);
  3MF is lib3mf-backed and needs no OPC step for metadata/colors.
- OCCT 7.9.3 capability spike (executed, output preserved): AP242 PMI
  round-trips all 15 FCF types and 27/30 dimension types **iff** six traps
  are handled (writer-first schema static, `DatumObject.SetPosition`,
  ≥1 dimension or METRE corruption, magnitude tolerances, de-located shapes,
  a 3-type segfault blocklist); structured XCAF import works (referred-label
  rules, sRGB extraction, component-path identity); GLB is
  byte-deterministic; `usd-core` has no linux-aarch64 wheel (extra needs an
  environment marker).
- Slice plan: 8 slices in 4 waves, kernel import/export packs split across
  two files for parallelism.

## Files
- `docs/prd/in-progress/PRD-017-interop-pack.md` — moved from `pending/`
- `docs/superpowers/specs/2026-08-23-interop-pack-design.md` — new
- `docs/superpowers/specs/2026-08-23-interop-pack-spike.md` — new (spike report)
- `docs/superpowers/plans/2026-08-23-interop-pack.md` — new

## Notes
The spike is OCCT-vs-OCCT; FreeCAD/commercial-viewer readability stays a
manual per-release check (AC1). Changelog numbers may need renumbering above
main's highest at merge time (parallel-branch convention).
