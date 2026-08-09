# 0010 — UI screenshots captured from the running app

- **Commit:** 3bbb52e
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Adds two PNG screenshots of the running AgentCAD app so the docs can show the
actual UI (workbench layout and the part-editing flow) rather than describe it.

## Changes
- Introduces `docs/assets/` as the home for documentation imagery.
- Adds `workbench.png` (~355 KB) — the full workbench: sidebar parts/assembly
  tree, Three.js viewport, and the right inspector.
- Adds `part-editing.png` (~334 KB) — the part-editing view (parameters/code/
  metrics inspector against a selected part).

## Files
- `docs/assets/workbench.png` — new, full workbench screenshot
- `docs/assets/part-editing.png` — new, part-editing screenshot

## Notes
Binary assets only; no code or text changes. Later refreshed in commit 3cc9871
(changelog 0013) once the final build's UI settled.
