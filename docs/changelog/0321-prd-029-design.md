# 0321 — PRD-029 Agent skills: design spec + implementation plan

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
Opens PRD-029 (agent skills & knowledge packs) on branch
`prd-029-agent-skills`: the PRD moves to `docs/prd/in-progress/`, the design
spec records every ruling with its reason, and the plan splits the work into
seven slices (library + lint + CLI → tools + chat seam + routes ‖ cheat-sheet
migration ‖ six authored core skills → bench `--skills` ‖ Skills modal + chat
chip → acceptance + docs).

## Changes
- Design spec `docs/superpowers/specs/2026-08-23-agent-skills-design.md`:
  `SKILL.md` with a strict flat-YAML-subset frontmatter parsed by our own
  reader (no PyYAML; every value a string); layers `core < org < project`
  with visible overrides (`org` unpopulated until PRD-005); deterministic
  keyword ranking for `list_skills {query}`; structural truncation at `##`
  boundaries; a per-chat-session budget (4 skills / 40 000 chars) with LRU
  eviction that **rewrites the evicted `tool_result` in the transcript** and
  publishes `skill_unloaded`; capability gating from a closed set (`fem`,
  `threads`, …) that fails closed on unknown names; trust for project skills
  keyed by content digest, stored in `<project>/.history/agentcad/skills/`
  (local, never cloned), granted only through a human-only route — no tool
  can approve agent instructions; `part_template` shrinks to the contract +
  build123d basics + a pointer index while the nine toolkit sections move
  verbatim into core skills; sixteen core skills at launch; `bench run
  --skills all|none|<names>` with the with/without comparison through the
  existing `bench report --baseline`; `agentcad skill new|lint`; the Skills
  modal on the PRD-026 shell and a chat chip per loaded skill.
- Plan `docs/superpowers/plans/2026-08-23-agent-skills.md`: seven slices
  with exact interfaces, model assignments and the concurrency shape.
- `docs/prd/in-progress/PRD-029-agent-skills.md` (moved from pending, status
  updated); `docs/roadmap.md` row 029 → in-progress.

## Files
- `docs/superpowers/specs/2026-08-23-agent-skills-design.md` — new
- `docs/superpowers/plans/2026-08-23-agent-skills.md` — new
- `docs/prd/in-progress/PRD-029-agent-skills.md` — moved
- `docs/roadmap.md` — status row
- `docs/changelog/0321-prd-029-design.md` — this entry

## Notes
Docs only. Baseline `make test` on this branch point (`f5dabf6`, a fresh
worktree) — 5423 passed, 40 skipped, 3 failed in 934 s with seven
implementation agents sharing the machine; the three
(`test_supervisor` memory-cap kill, `test_share_frontend` shell, and the
`test_prd028_acceptance` real-solver FEM run) are timeouts under that load —
the first two re-run green in isolation (2 passed, 30 s) and the FEM one is
the known local `[fem]`-extra timeout that skips on CI. Rulings that depart from the PRD's letter are
named in the spec so a reviewer can overturn them: no `unload_skill` tool
(runtime LRU only), trust is a route rather than a tool, `part_template`
keeps the build123d idioms beside the contract, the org layer and the
CI-published bench deltas are deferred with reasons.
