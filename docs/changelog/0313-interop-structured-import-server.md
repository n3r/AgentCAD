# 0313 — PRD-017 slice 3: structured STEP import, server half

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
`import_cad_file` learns `structured`/`prefix`: a multi-product STEP lands as
N deduplicated reference parts plus placed assembly instances with names,
transforms, and colors; a preview route feeds the import dialog. FR8–FR10
(v1 flatten) of PRD-017.

## Changes
- `import_cad_file {project, source, part_id?, label?, material?,
  structured?, prefix?}` — `required` is now `[project, source]`; flat still
  requires `part_id` (`validation_error` naming it). Schema extended in its
  own pack (ruling: `tools_xchange` mutation is only for core-registered
  tools).
- Auto-detect is **name-aware, not count-only**: structured iff >1
  occurrence AND (an authored occurrence name OR >1 distinct product name).
  AgentCAD's own `export_step` of a multi-solid part reads back as N
  occurrences of OCCT-placeholder `SOLID` products — a raw count would have
  exploded every re-imported widget (and broke
  `test_step_reference_roundtrip`). `structured: true/false` overrides.
- Structured landing: `import_structured` into the project's imports dir
  (write-guarded), N `create_part(kind="reference")` with ids slugged
  through `ID_RE` (+`prefix`, deterministic `_2/_3` suffixes against
  existing AND newly-minted ids — re-import lands beside the first set),
  `source_label`/`import_source` loose keys (merge-verified, no
  `manifest_merge` guard needed), then **one** `set_instances` batch write +
  one trailing `project_changed` — the instance batch is a single undo step
  (pinned by test).
- Refusals: `structured: true` on `.stl`/`.brep` → `validation_error` (a
  mesh has no product tree); unreadable STEP in auto mode falls back to
  flat with the reason in `warnings`; `part_id`/`label` on a structured
  import are reported ignored (the browser always sends `part_id`).
- Result `{parts, instances, tree, warnings, fidelity}`; flat/STL results
  keep every existing key and gain `fidelity` (spec §8 shapes).
- `POST /projects/{proj}/imports/{name}/preview` → `inspect_cad_tree`
  payload; 422 non-STEP, 404 missing, 502 with the worker's error type
  (the `routes_drawing` rule); default-deny covers hosted mode (asserted
  anonymously + `is_public` false).
- Hosted host-path guard still fires before any kernel call
  (`CountingKernel`-asserted).

## Files
- `agentcad/core/tools_import.py` — extended (structured landing, fidelity)
- `agentcad/server/routes_import.py` — preview route
- `tests/test_interop_import.py` — new (30 tests)

## Notes
`make test` — 5507 passed, 40 skipped in the recorded run (19:31, box shared with a concurrent session); the 7 non-passing items are the same known set as 0311/0312 — sheetmetal/supervisor load timeouts (22/22 pass in 57 s in isolation) and the pre-existing local-only prd028 AC6 real-solver timeout (skips on CI).
