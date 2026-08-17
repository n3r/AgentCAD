# 0188 — PRD-012 design: configurations spec and implementation plan

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Nikita Fedorov (with Claude)

## Summary

The design round for PRD-012 (Configurations): an eight-seam reading of the
codebase, a design spec with eleven decisions, an eight-slice implementation
plan, and the PRD moved to `docs/prd/in-progress/` with four amendments
folded back. No code changes.

## Changes

- `docs/superpowers/specs/2026-08-17-configurations-design.md` — the design.
  The decisions the reading forced: configurations are manifest-resident
  fields on `PartRecord`/`InstanceSpec` (Decision 1); the frozen PRD-011
  schema, one validator plus a map-level `validate_configurations`, lowercase
  names, range-strict declaration vs clamp-on-override, values normalized on
  write (Decision 2); resolution is a pure function of the record
  (`config_params` / `effective_params`), the worker fills defaults, every
  geometry consumer switches from `record.params`, and nothing new enters
  `_cache_key`'s payload (Decision 3); `_rebuild`/`get_part` keep their
  pack-wrapped signatures, config builds go through an extracted
  `_build_with` and a separate `_config_status` memo, `build_configs` is
  serial and de-duplicated by cache key (Decision 4); `set_active_config`
  clears explicit overrides unless `keep_overrides`, divergence is semantic
  (Decision 5); five tools and eight routes (Decision 6); the store validates
  instance bindings, `mesh_key` + a content-addressed `/meshes/{key}` route
  replace the one-mesh-per-part assumption (Decision 7); per-config exports,
  renders, a measured drawing dimension table, spec results and CI rows
  (Decision 8); the merge reaches `configs.<name>.params.<param>` and reports
  the dangling-binding hybrid (Decision 9); the browser surfaces (Decision 10).
- `docs/superpowers/plans/2026-08-17-configurations.md` — eight slices with
  changelog numbers 0189–0196, files, shapes, tasks, tests, verification.
- `docs/prd/pending/PRD-012-configurations.md` →
  `docs/prd/in-progress/PRD-012-configurations.md`, status line updated, and
  four fold-backs marked *Amended at design*: config names lowercase
  (`flange_l.step`, AC4 and Experience), `build_configs` serial and
  de-duplicated (FR5, Experience — PRD-011's deleted fan-out is the
  evidence), declared configurations range/enum-strict with normalization on
  write (FR1 note; FR1's pinned needles untouched), and the v1 dimension
  table's columns (FR8).

## Files

- `docs/superpowers/specs/2026-08-17-configurations-design.md` — new
- `docs/superpowers/plans/2026-08-17-configurations.md` — new
- `docs/prd/in-progress/PRD-012-configurations.md` — moved + amended
- `docs/changelog/0188-prd-012-design.md` — this entry

## Notes

Reading was eight parallel readers over: param resolution and the cache key,
assembly resolution, the mesh pipeline and viewport, tool/route conventions
and drawings, the manifest and its merge, the frontend, test conventions and
the CI/spec seams, and the frozen configuration schema plus the docs format.
Three findings changed the PRD's own words and are recorded above; two more
shaped the design without changing the PRD: `_rebuild` and `get_part` are
monkey-patched by three packs with fixed two-positional signatures (so no
`config=` kwarg there), and the browser's `meshBuffers` map plus the server's
`_status` slot were the only two places the one-mesh-per-part assumption was
load-bearing (a livelock, not a perf note, once two bound instances share a
part).

Baseline at the design commit (docs only, run before any slice): `make test`
— 3306 passed, 7 skipped, in 10:35 on 8 workers. The only failures in that
run were the four `test_ac*_the_full_suite_count_is_cited` acceptance tests
(PRD-008/009/010/011), which read the newest changelog entry — this one — and
found no count in it; this sentence is the fix, and the next slice's run is
the first fully green one.
