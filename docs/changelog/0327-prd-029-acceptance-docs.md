# 0327 — PRD-029 slice 7: acceptance tests (AC1–AC7) and the skills docs

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
Closes the build of PRD-029: one test per acceptance criterion, the
branching round-trip, `docs/skills.md`, and every doc that still described
the old cheat-sheet or lacked the skill tools.

## Changes
- `tests/test_prd029_acceptance.py`: AC1 — a scripted client asks for a
  snap-fit lid, really calls `load_skill` then `create_part` with the
  skill's own `snippets/cantilever_lid.py`; asserts `skill_loaded {layer:
  core, client: chat}`, "Loaded this session: snap-fits" in the second
  request's system prompt, and a kernel-built part with `volume_mm3 > 0`.
  AC2 — `list_skills {query: "sheet"}` ranks `sheet-metal` first; an
  oversized project skill loads truncated with a byte-exact prefix and the
  omitted headings named. AC3 — a project `enclosures` shadows the core one
  identically on the tool and the route. AC4 — with the `fem` probe false
  `fem-workflow` is hidden and refused `skill_unavailable`; present with it
  true. AC5 — points at `tests/test_bench_skills.py`. AC7 — `part_template`
  compat plus the house count-guard.
- `tests/test_skills_branching.py`: a project skill written on branch
  `feature` is absent on main, present on `feature`, present on main after
  `merge_branch` (fast-forward); and trust does **not** travel with the
  branch (`.history/agentcad/` is branch-free).
- `docs/skills.md` (new): format, layers, the agent loop (index, tools,
  budget/eviction), trust and provenance, the modal, authoring and every lint
  code, bench measurement, the sixteen core skills, the traps.
- `docs/agent-api.md`: `### Skills` table (`list_skills`, `load_skill`), the
  three events, the `part_template` row and worked loop;
  `docs/user-guide.md`: the Skills modal, badges, trust, teach flow;
  `docs/part-authoring.md`: the cheat-sheet section now points at the core
  skills; `AGENTS.md` (two stale pointers + a Skills trap paragraph),
  `CLAUDE.md` (the condensed trap bullet + deeper-docs pointer),
  `README.md` (one feature line); `agentcad/toolkit/holes.py`: two comments
  cite the `holes` skill instead of the cheat-sheet.
- `docs/superpowers/specs/2026-08-23-agent-skills-design.md` §9 records
  Slice 5's `only`-refusal and `--baseline` rulings.

## Files
- `tests/test_prd029_acceptance.py`, `tests/test_skills_branching.py`, `docs/skills.md` — new
- `docs/agent-api.md`, `docs/user-guide.md`, `docs/part-authoring.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `agentcad/toolkit/holes.py`, `docs/superpowers/specs/2026-08-23-agent-skills-design.md`

## Notes
Pre-existing drift noticed, not fixed here: README, `agent-api.md`,
`architecture.md` and `user-guide.md` say "85 tools" while `build_registry`
registers 109 (107 before this PRD) — a separate docs fix. `make test` on the
whole branch (all seven slices, quiet machine) — 5967 passed, 66 skipped, 4 failed in 765 s on the tree merged with
`origin/main` (PRD-017 landed underneath; the merge kept both trap blocks in
`AGENTS.md`/`CLAUDE.md` and ported main's one cheat-sheet edit —
`check_clearance(a, b, min_mm, max_mm=)` — into the `design-specs` skill).
The four: the known local `[fem]` real-solver timeout that skips on CI, and
three 120 s kernel-wait timeouts under the suite's own load
(`test_supervisor` ×2, `test_share_viewer`) that re-run green in isolation
(3 passed, 28 s).
