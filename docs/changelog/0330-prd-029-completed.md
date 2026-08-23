# 0330 — PRD-029 completed: move PRD to completed/, mark roadmap DONE

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
PRD-029 (agent skills & knowledge packs) merged into main via PR #33: the
`SKILL.md` format, the core < project library, `list_skills`/`load_skill`
on every agent surface, the budgeted chat seam, digest-keyed trust with a
human-only grant, sixteen core skills, the Skills modal and chat chips,
`agentcad skill new|lint`, and `bench run --skills`. This commit is the
docs-only close-out.

## Changes
- `docs/prd/in-progress/PRD-029-agent-skills.md` → `docs/prd/completed/`,
  header status → completed (PR #33).
- `docs/roadmap.md`: PRD-029 row → completed, link updated to `completed/`,
  with the shipped scope and the four recorded deferrals (org layer,
  workspace-aware suggestion, CI-published bench deltas, marketplace
  distribution).

## Files
- `docs/prd/completed/PRD-029-agent-skills.md` — moved, status line
- `docs/roadmap.md` — status + link

## Notes
Evidence: PR #33 CI green first pass on every leg (`pytest (macos, pr)`,
`pytest (ubuntu, portability)`, bench self-test, the four geometry-CI
checks); merged-tree local run in 0329 — `make test` — 5967 passed, 66
skipped (the four non-green rows there are the known local `[fem]` timeout
and three load timeouts that re-run green). Review trail: Opus code review +
Opus adversarial verifier + Codex xhigh → fix wave (0328/0329) → static
re-review with three follow-ups landed in the same commit. Known follow-ups
(LOW, non-gating, named in 0325/0329): `skills.js` issues its four requests
through a local helper rather than `api.js` methods; `load()` reads
`trust.json` twice; `test_the_three_skill_events_reach_the_right_handlers`
asserts source text rather than DOM behaviour. The spec's deferrals are the
seams for PRD-005 (org layer), PRD-025 (workspace envelope) and PRD-031
(marketplace); `bench.yml` can publish per-skill deltas once the task roster
is skill-tagged.
