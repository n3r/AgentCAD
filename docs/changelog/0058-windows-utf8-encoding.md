# 0058 — Explicit UTF-8 on all text I/O (Windows CI fix)

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

The three-OS CI matrix on PR #2 caught exactly one Windows failure out of
292 tests: `test_drawing_renders_pmi_callouts` read the generated SVG with
`Path.read_text()` and no encoding, and Windows' cp1252 default choked on
the multi-byte UTF-8 GD&T glyphs (⏥, ⌀). Every naked text read/write in the
repo now names `encoding="utf-8"` so behavior is identical on all three
platforms.

## Changes

- **Production**: `service._rebuild`'s metrics-sidecar read gains
  `encoding="utf-8"`; the history git driver's `subprocess.run(text=True)`
  gains `encoding="utf-8", errors="replace"` (git output is UTF-8 — commit
  messages would mojibake under a cp1252 locale). All other production text
  I/O already named its encoding (store, config, materials, sandbox).
- **Tests**: `encoding="utf-8"` added to every `read_text`/`write_text` in
  test_pmi (the failing site), test_drawings, test_sheetmetal, test_config,
  test_project, test_materials, test_solids, test_service, test_mesh_lod,
  test_sandbox — the SVG-reading ones were latent traps (cp1252 mis-decodes
  UTF-8 "°" as "Â°" silently), the JSON ones are consistency.

## Files

- `agentcad/core/service.py`, `agentcad/core/history.py`
- `tests/test_pmi.py`, `tests/test_drawings.py`, `tests/test_sheetmetal.py`,
  `tests/test_config.py`, `tests/test_project.py`, `tests/test_materials.py`,
  `tests/test_solids.py`, `tests/test_service.py`, `tests/test_mesh_lod.py`,
  `tests/test_sandbox.py`

## Notes

Windows CI otherwise ran the full suite green (291 passed, 13 skipped — the
extra 8 skips are the darwin-only sandbox tests), which is the roadmap's
"CI to prove portability" doing its job on the first try.
