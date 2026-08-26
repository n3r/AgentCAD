# 0357 — PRD-018 task-to-part generation: design spec + slice plan; PRD to in-progress

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Claude (orchestrator) / Nikita Fedorov

## Summary
Design groundwork for PRD-018 (kernel-grounded LLM loop generating
parametric parts from a prompt/image/PDF, iterating draft→build→render→
measure until kernel-green AND spec-green, with candidates, provenance,
proposal acceptance, and bench scoring). No code — spec, executed-spike
evidence, slice plan, PRD moved to in-progress.

## Changes
- Design spec with scope ruling (MVP + most of Phase 2 — all deps shipped;
  background jobs/model-tiering/marketplace packs deferred as the PRD's own
  Phase 3) and the seam-map decisions: the orchestrator is a NEW
  `agent/generate.py` loop that reuses ChatEngine's seams by import (not a
  subclass — chat is single-turn/30-call/no budget), with mechanical
  look-and-measure enforced in CODE; budget/termination via a
  BudgetedClient-style wrapper (spec_green / budget_exhausted / abandoned,
  all results never exceptions); half-write integrity via scratch part ids
  + delete_part (no live orphan on any exit); frozen intent-specs diffed at
  terminate (a weakened spec fails, measured like the bench specs
  denominator); provenance as a manifest loose key surfaced on get_part via
  the install_rebuild_specs wrapper pattern; proposal/direct acceptance
  under a gen:<id> identity with the PRD-005 tenant-capture-across-threads
  lesson applied.
- Executed spike (proofs preserved): the FakeMessages client drives a
  multi-tool loop deterministically against the REAL kernel (FR14 keystone
  proven — create_part→render_view→get_metrics); render_view vision
  re-entry already works (chat's image-block rewrite); **pypdfium2** is the
  clean PDF dep (BSD/Apache, all-platform py3 wheels, native text
  extraction, reuses the repo's dependency-free `encode_png` — no Pillow,
  one dep; pymupdf-AGPL and poppler-GPL rejected); standards grounding
  already ships as `skills/brackets-and-mounts/tables/nema.json` read
  server-side via `SkillLibrary.load(asset=)` — the exact AC2 numbers, no
  new mechanism to build.
- Slice plan: 7 slices in 4 waves.

## Files
- `docs/prd/in-progress/PRD-018-task-to-part-generation.md` — moved from pending/
- `docs/superpowers/specs/2026-08-25-task-to-part-generation-design.md` — new
- `docs/superpowers/specs/2026-08-25-task-to-part-generation-spike.md` — new
- `docs/superpowers/plans/2026-08-25-task-to-part-generation.md` — new

## Notes
The security invariant the review will attack: an uploaded datasheet/PDF's
text is reference DATA, never instructions. `make test` not run for a
docs-only commit; last measured tree (PR #35) green.
