# 0316 — PRD-017 slice 6: frontend import preview + export menu

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Sonnet subagent) / Nikita Fedorov

## Summary
The PRD-017 Experience surface: a structured-import preview dialog, a
schema-driven Export menu (gltf/glb now; future formats appear with zero
frontend changes), an include-PMI toggle on STEP part export, and fidelity
suffixes in toasts.

## Changes
- Import: a `.step`/`.stp` upload calls the preview route; >1 occurrence
  opens a dialog (tree-row styling, per-product swatch + ×N badge, summary
  line, `prefix` field) with Cancel / "Import flat instead" (today's
  part-id prompt, `structured: false`) / "Import N parts"
  (`import_cad_file {structured: true, prefix}`). Single-product, non-STEP,
  or a failed preview fall through silently to today's exact prompt.
  Not `dialogs.register`ed — the `import-part-id` precedent (an in-flight
  upload payload can't be conjured from `ui_open`).
- Export: all entries funnel through `registerExportAction`;
  `syncDynamicExportFormats()` reads the `export_part`/`export_assembly`
  schemas at boot and registers any enum entry the static table lacks
  (verified live: slice 5's `assembly:3mf` appeared with no frontend
  change; `usd` will too). The toolbar Export▾ dropdown is rebuilt from
  `actions.list()` — one list drives menu + toolbar.
- STEP part export routes through `callTool` with `pmi`; a part with PMI
  gets a default-checked "Include GD&T (AP242)" checkbox dialog, no dialog
  otherwise; toasts report `PMI attached (N dims, M datums, K FCFs)` /
  opted out / skipped counts, and import toasts surface warnings — only
  when notable.
- Browser-verified with Playwright + installed Chrome against a scratch
  projects dir: preview render, structured landing (3 parts/7 instances,
  colour override visible), flat fallback, GLB export toast, PMI dialog
  both ways (screenshots in the session scratchpad, referenced in the PR).

## Files
- `frontend/js/main.js` — dialog, PMI toggle, dynamic formats, toast suffix
- `frontend/js/api.js` — `previewImport`
- `frontend/index.html` — Export▾ rows now JS-built containers
- `frontend/css/app.css` — `.import-tree*` styles

## Notes
`make test` — 5546 passed, 40 skipped (14:45); 3 non-passing: the pre-existing local-only prd028 AC6 solver timeout (skips on CI), the supervisor ballooning-kill load flake, and a share-isolation setup timeout that was collateral of the ballooning test's worker restart on the same pool (memory-cap crash visible in the log directly above it; 3/3 pass in 23 s in isolation).
