# 0021 — v2: CAD file import as reference parts (STEP/BREP/STL)

- **Commit:** 07289c5
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Implements importing external CAD files as `reference` parts: a worker
`build_reference` handler, an `import_cad_file` tool, and an upload route. STEP/
BREP round-trip as real B-reps (exact metrics, boolean-capable); STL is
display/measure-only (booleans blocked, excluded from interference without
crashing).

## Changes
- **Worker handler `kernel/handlers/reference.py`:** registers `build_reference`
  via the Wave-0 toolbox. Loads through `refload.load_reference`; STEP/BREP get
  full `metrics(...)` with `mesh=False`; STL computes signed mesh volume from
  the triangulation via the divergence theorem (`_stl_mesh_volume`), reports
  `is_valid=False`, `n_solids=0`, `mesh=True`, and a mesh-only warning. Always
  tessellates and writes the mesh so the reference renders.
- **Tool `core/tools_import.py`:** `import_cad_file(project, source, part_id,
  label?, material?)` — ingests an absolute path (or a path-like `source`) into
  the project's `imports/`, or resolves a bare filename already uploaded there
  (else `ValidationError`), then creates a `kind="reference"` part. Returns the
  part detail plus an `imported` summary (`source`, `n_solids`, `is_valid`,
  `mesh_only`, `warnings`).
- **Ingest helpers `core/imports.py`:** `safe_import_name` reduces any filename
  to its basename (the traversal security boundary) and enforces the
  `.step/.stp/.brep/.stl` allowlist; `ingest_file` copies into `imports/` with
  a 100 MB cap. Shared `SUPPORTED_EXTS`/`MAX_IMPORT_BYTES` constants.
- **Route `server/routes_import.py`:** `POST /projects/{proj}/imports?filename=`
  — raw-body upload, basename-sanitized, 100 MB cap, empty-body rejected,
  returns `{source, size_bytes}`.

## Files
- `agentcad/kernel/handlers/reference.py` — `build_reference` handler, STL divergence-theorem volume
- `agentcad/core/imports.py` — `safe_import_name` (traversal/extension guard), `ingest_file` (100 MB cap)
- `agentcad/core/tools_import.py` — `import_cad_file` tool → reference part
- `agentcad/server/routes_import.py` — `POST /projects/{proj}/imports` upload route
- `tests/test_reference.py` — STEP round-trip (2000 mm³, 2 solids), reference-in-boolean, STL mesh-only, STL excluded-from-interference-no-crash, upload traversal/extension rejection

## Notes
STL boolean participation is banned end-to-end: `refload` marks it mesh-only,
the handler flags it, and `check_interference` skips it (reported via
`skipped_mesh`) because cut/intersect on a triangulation-only Face segfaults
OCCT. The STEP two-cube round-trip relies on the Wave-0 nested-Compound volume
fix to report 2000 mm³ correctly.
