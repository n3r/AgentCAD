# 0294 — 2026-08-20 — PRD-028 closed out: the materials database ships (434 cited cards)

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary

Bookkeeping after PR #27 (Materials database) merged to main as 4bd8f44. The
PRD moves to `docs/prd/completed/` and its roadmap row flips to **completed
(PR #27)** — founder idea #2 ("vastly expanded materials"), reframed by the
research to *credible* rather than *all possible*, is delivered as 434 generic
materials with per-value provenance.

## What shipped (MVP + Phase 2)

- **Schema v2 as data** — `agentcad/core/materials_data/*.json`: 434 cards
  across all 30 taxonomy leaves, every property with `unit` + `basis`
  (`typical|minimum|characteristic`) + a primary-source `source`, 80 cited
  temperature tables; the 30 legacy records migrated byte-identically
  (`_cache_key` payload untouched); v1 flat user entries still valid.
- **`materials.py`** — `Property.at(T)` interpolation with clamping,
  `Material` keeps every flat field, `LIBRARY_VERSION` + the additive
  `materials_library` manifest pin, the editorial immutability rule.
- **Query engine + tools** — `find_materials` / `get_material` /
  `list_materials {category, subcategory, filter}` with the conservative
  range bound, missing-property-never-qualifies, and the `nearest_relaxation`
  refusal; routes `GET /api/materials?filter=`, `GET /api/materials/{id}`,
  `POST /api/materials/find` (member-gated).
- **FEM** — E/ν/k resolved from the material at an analysis temperature
  (thermal: the mean of the two fixed temperatures), `material_basis` in every
  result, `temperature_out_of_table_range:` warning on clamping; kernel
  untouched.
- **Lint** — `core/materials_lint.py` + `agentcad materials lint` (profiles
  `library`/`user`, exit 0/1/2, `missing_citation` naming the property,
  `disallowed_source`, `point_disagrees_with_table`); the loader refuses a card
  the lint calls wrong.
- **Materials browser** — modal with tree, filters, sortable table, compare,
  detail with basis badges + citations + tables + https-only outbound links;
  inspector **Browse…** assign mode.
- **Provenance** — `materials_data/PROVENANCE.md`: per-file sources, the
  20-record editorial QA, the library-wide sweep, the AC7 attestation, 45
  kept lint warnings itemized.
- **Docs** — `docs/materials.md`, `docs/agent-api.md`, `docs/user-guide.md`,
  `AGENTS.md` "Materials library gotchas", `CLAUDE.md` trap.

## Deferred (recorded in the PRD, the spec and `docs/materials.md`)

The public `agentcad-materials` community repo + CI (the card format and the
lint are the gate it would run), material-card package distribution (PRD-011
mechanics), FreeCAD `.FCMat` one-way import (mapping note), 600+ records and
dated cost refreshes, the ⌘K palette entry (PRD-026).

## Files
- `docs/prd/completed/PRD-028-materials-database.md` — moved from
  `in-progress/`, `Status: completed — merged to main in PR #27 (4bd8f44)`.
- `docs/roadmap.md` — 028 row → completed (PR #27); the depth-tier note.
- `docs/changelog/0294-prd-028-completed.md` — this entry.

## Notes
Merged-tree verification on the branch before merge: `make test — 5004 passed,
48 skipped, 0 failed in 657 s` (quiet machine; two earlier runs under load ≥ 25
each tripped one sketch timing micro-benchmark, green in isolation). PR #27 CI
fully green (macOS PR suite, Ubuntu + Windows portability, Geometry CI ×4 —
`check (rocketry)` on a re-run after the known apt-mirror stall — and the
bench self-test, also re-run after an apt stall). Review: Opus design/plan
review + Opus adversarial verifier (Codex quota-blocked); all findings fixed
in 3ab02c7 (changelog 0293). Branch history: changelogs 0285–0293 (renumbered
from 0270–0277 after PRD-024 landed first).
