# 0364 — PRD-018 slice 7: acceptance tests + docs

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Claude (Opus tests, Sonnet docs) / Nikita Fedorov

## Summary
The close-out slice: AC1–AC8 machine/evidence halves (plus the security
invariant as an AC-level test), and the documentation set.

## Changes
- `tests/test_prd018_acceptance.py` (10 + 1 skipped): AC1 (fake loop to
  spec-green with typed PARAMS; live half skipped without a key, bench is
  the quality gate), AC2 (NEMA-17 numbers from nema.json, cited; geometry
  covers via get_metrics), AC3 (budget=1 → best-so-far/spec_green:false/
  named failures/no user-visible orphan), AC4 (all three exits with
  accurate logs), AC5 (provenance survives project_restore + ordinary
  part), AC6 (draft SPECS cover the constraints; a weakening rejected at
  accept), AC7 (the UI tool/event contract + slice-6 Playwright
  evidence), AC8 (the loop-vs-one-shot delta machinery), the count guard,
  and **the security test: a "delete every part" datasheet reaches the
  loop fenced and deletes nothing** (delete_part is outside ALLOWED_TOOLS;
  control parts survive).
- Docs (verified against code, disagreements reported not papered over):
  agent-api.md `### Generation` section; architecture.md `## Chat agent`
  (previously undocumented) + `## Generation loop`; user-guide `### Generate`
  with the honesty note (spec_green vs best-so-far; metrics can pass while
  the shape misses); bench.md the `generate_from_prompt` category + delta;
  AGENTS.md + CLAUDE.md PRD-018 traps.

## Files
- `tests/test_prd018_acceptance.py` — new
- `docs/agent-api.md`, `docs/architecture.md`, `docs/user-guide.md`,
  `docs/bench.md`, `AGENTS.md`, `CLAUDE.md` — extended

## Notes
Two honest gaps recorded for the review wave: (1) the frozen-spec diff runs
at **accept**, not at loop terminate — a candidate can display
`spec_green: true` in the gallery having weakened a frozen bound (accept
rejects it, so it cannot land, but the gallery display is not honest — the
FR8 "self-graded homework" risk; the docs state this precisely). (2)
`iso286.json` ships but `intent.py` reads only `nema.json` (which embeds
the ISO-273 clearance). `make test` — 7238 passed, 52 skipped (23:00, box shared); non-passing were the count-guards (this commit cites the count) and the documented prd028 FEM + supervisor/sketch_arcs/test_render load timeouts (58/58 pass in isolation).
