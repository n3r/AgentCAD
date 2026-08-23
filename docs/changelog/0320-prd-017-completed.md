# 0320 — PRD-017 completed: move PRD to completed/, mark roadmap DONE

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (orchestrator) / Nikita Fedorov

## Summary
PRD-017 (interop pack) merged into main via PR #31: STEP AP242 PMI export,
structured assembly-STEP import, deterministic glTF/GLB, 3MF v2
(metadata + per-solid colors), structured STEP assembly export, USD behind
`agentcad[usd]`, and fidelity reporting on every interop result. This
commit is the docs-only close-out.

## Changes
- `docs/prd/in-progress/PRD-017-interop-pack.md` → `docs/prd/completed/`,
  header status → completed.
- `docs/roadmap.md`: PRD-017 row → DONE, link updated to `completed/`.

## Files
- `docs/prd/completed/PRD-017-interop-pack.md` — moved, status line
- `docs/roadmap.md` — status + link

## Notes
Deferred by recorded ruling (design spec): `structured: "nested"` import
onto PRD-013 sub-assembly sources, and PMI import. Known follow-ups from
review (LOW, non-gating): frontend JS unit tests for the new dialog
helpers, CLI export-surface parity (`cmd_export` bypasses the wrapped
service), preview `tree` rendering as a nested tree (flat list ships).
Evidence: PR #31 CI green (`pytest (macos, pr)` passed on rerun after the
documented `test_sketch_drag` timing flake; ubuntu portability + all
geometry-CI checks green first pass); merged-tree local run in 0319 —
`make test` — 5638 passed, 40 skipped.
