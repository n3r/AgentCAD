# 0356 — PRD-025 renamed workspaces → modes (settling the PRD-005 collision)

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Claude (orchestrator) / Nikita Fedorov

## Summary
Docs-only. PRD-025's Build · Test · Produce · Library · Market tabs are
renamed from "workspaces" to **modes**, resolving the three-way term
collision PRD-005 flagged: tenancy took "workspace" for `org → workspace →
project` (shipped, user-facing) and the PRD-026 shell uses `workspace`
internally as its per-tab layout-memory localStorage key. Decided with the
user (term: "modes"; scope: rename the unbuilt PRD-025 concept only, leave
the shell's internal slot as-is).

## Changes
- `docs/prd/pending/PRD-025-workspaces-ia.md` → `PRD-025-modes-ia.md`; the
  concept, tools (`get_workspace`/`set_workspace` → `get_mode`/`set_mode`),
  event (`workspace_changed` → `mode_changed`), deep links, and prose
  renamed; a Naming note records the ruling. The two Fusion competitive
  references keep "workspace" (that is Fusion's own product term).
- `docs/roadmap.md`: the 025 row + label, the founder-idea cell, the
  PRD-029 forward-ref, and the file link updated.
- Forward-references in sibling PRDs (011, 026, 027, 029, 030, 031, 031a,
  005) that named 025's concept "workspace" now say "mode" — per-line, so
  PRD-026's shipped `per-workspace layout memory` / `agentcad.layout.*`
  mentions (the shell-internal slot, unchanged) and tenancy's "workspace"
  in PRD-005 are preserved. PRD-005's risk note is marked resolved.

## Files
- `docs/prd/pending/PRD-025-modes-ia.md` (renamed + rewritten),
  `docs/roadmap.md`, and PRD-011/026/027/029/030/031/031a/005 forward-refs.

## Notes
No code, no tests — the shell's internal `workspace` identifier and its
`agentcad.layout.<workspace>` localStorage key are deliberately untouched
(never user-facing; renaming them would orphan saved layouts). `make test`
not run for a docs-only change; the last measured tree (PR #35) ran the
full suite green.
