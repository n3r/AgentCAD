# 0270 — PRD-028 Materials database: design spec + implementation plan

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
Opens PRD-028 (materials database expansion) on branch
`prd-028-materials-database`: the PRD moves to `docs/prd/in-progress/`, the
design spec records every ruling with its reason, and the plan splits the work
into six slices (schema v2 + loader + lint → query tools ‖ FEM resolution →
curation fan-out ‖ browser UI → acceptance + docs).

## Changes
- Design spec `docs/superpowers/specs/2026-08-19-materials-database-design.md`:
  card schema v2 (closed property keys, canonical units, `basis` ∈
  typical|minimum|characteristic, per-value `source`, optional `T_c`/`table`),
  `Material` keeps every flat field (points/midpoints) and gains typed property
  access + `Property.at(T)` interpolation with clamping; JSON data files under
  `agentcad/core/materials_data/` with `LIBRARY_VERSION` and an additive
  `materials_library` manifest key plus the editorial immutability rule;
  taxonomy (7 categories, closed subcategories; `masonry` kept); process
  vocabulary; the `find_materials` constraint grammar and nearest-relaxation
  refusal; FEM resolves E/ν/k service-side at an analysis temperature and
  reports `material_basis`; lint profiles `library`/`user` with a
  `disallowed_source` check; browser UI; curation rules; deferrals (community
  repo, package distribution, FreeCAD import, palette entry).
- Plan `docs/superpowers/plans/2026-08-19-materials-database.md`: six slices
  with model assignments and the concurrency shape.
- `docs/prd/in-progress/PRD-028-materials-database.md` (moved from pending,
  status updated); `docs/roadmap.md` row 028 → in progress.

## Files
- `docs/superpowers/specs/2026-08-19-materials-database-design.md` — new
- `docs/superpowers/plans/2026-08-19-materials-database.md` — new
- `docs/prd/in-progress/PRD-028-materials-database.md` — moved
- `docs/roadmap.md` — status row

## Notes
Docs only. Baseline `make test` on this branch point — 4542 passed, 44
skipped, 2 failed, 1181 s: `test_sketch_drag` warm-drag p50 19.7 ms over FR6's
16 ms budget (a timing flake — the run shared the machine with the slice-1
agent) and one `specs.py` subprocess import that hit `materials.py` mid-edit by
that same agent (contamination, not a code fault); both are re-run green in the
slice-1 entry. Rulings that depart from the PRD's letter are named in
the spec so a reviewer can overturn them: `characteristic` basis added,
`allowable-linked` → record-level `links`, density must be a point in the
shipped library, FR9 via immutable ids + pin reporting rather than shipping
historical libraries.
