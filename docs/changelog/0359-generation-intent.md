# 0359 — PRD-018 slice 2: intent normalization + frozen specs + standards grounding

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
Deterministic, server-side (no model) intent normalization: an `Intent`
record, a draft SPECS block over the real `check_*` vocabulary, the
freeze/violation diff, and table-backed standards grounding. FR2/FR8/FR10.

## Changes
- `agent/intent.py`: `Intent` (envelope/interfaces/material/quantities/
  constraints/sources/standards_cited/free_text; `to_dict`/`from_dict`
  round-trip — FR2's returned form). `normalize_intent(prompt, images?,
  pdf_text?, skills?)` grounds table-backed standards deterministically
  and structures the obvious constraints (mass/wall/envelope/screw/
  material/quantity); everything else stays in `free_text` for the loop's
  model (the boundary is documented). `draft_specs(intent)` emits real
  `toolkit.specs` check dicts (bbox/mass/wall/volume/clearance/check_that)
  — deliberately NOT `check_valid` (that meta-spec stays the loop's, out
  of the frozen set). `freeze`/`frozen_spec_violation` — bench-style,
  frozen rows only, direction-aware weakening (looser `max_`/`min_`/
  `within_mm` bound or a deletion → violation; strengthening + additions
  allowed). `STANDARDS_RULE`/`DOCUMENT_RULE` constants for the system
  prompt.
- Grounding: `SkillLibrary().load("brackets-and-mounts",
  asset="tables/nema.json")` → `json.loads` → the NEMA-17 row copied
  verbatim into the interface + `standards_cited {pack, table, row}`.
  An absent frame (NEMA 42) grounds nothing — no invented dimension.
- **No new standards pack**: `nema.json` already carries the ISO-273
  clearance (`clearance_d_mm` 3.4 for M3), so every AC2 number is
  table-sourced; adding a pack would only risk breaking skill loading.

## Files
- `agent/intent.py`, `tests/test_generation_intent.py` (18 tests) — new

## Notes
A test greps the source to prove the standard numbers
(31.0/22.0/3.4/42.3) are not literals in `intent.py`. `check_that`-derived
specs carry an empty limit, so the frozen diff detects their deletion (not
weakening) — inherent to a predicate check, documented. `make test` — 7192 passed, 51 skipped (14:29); the non-passing were the count-guards reading the pre-commit newest changelog (this commit adds the count), one PRD-017 AC7 set-equality assertion updated to a subset check (S3's intake extensions are a legitimate addition; the guard still refuses an unsupported ext), and the documented supervisor/navigation load flakes + prd028 FEM timeout (34/34 pass in isolation).
