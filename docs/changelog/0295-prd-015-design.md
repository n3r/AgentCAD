# 0295 — 2026-08-19 — PRD-015 BOM & release management: design spec + implementation plan

## Summary

Opens PRD-015 (BOM & release management). The PRD moves to
`docs/prd/in-progress/` and this commit lands the design spec + 7-slice plan
under `docs/superpowers/`, grounded in a full seam map (manifest/
`_resolved_instances`/`mates`, the metrics cache, `materials.cost_usd_kg`,
`SpecRunner.evaluate_specs`, `CheckRunner` gates, `branches.tag`/`tags.json`
referrers, `proposals`/`proposal_review`, `checks.py`'s tag-capable ephemeral
service, the export paths, `manifest_merge`).

## Scope — full, and it unblocks PRD-014's deferred half

Every dependency is completed on main (PRD-001/002/003 hard; 004/011/012/013/014
soft), so the whole PRD is buildable: BOM (FR1-5), revisions/releases (FR6-9,
FR12), reproducible bundles (FR10-11). Giving `get_bom` to the drawing path also
lands **PRD-014's deferred FR4/FR5** (assembly balloons + on-sheet BOM).

## Key decisions (with the seam-map corrections to the PRD's own approach)

- **Zero-kernel BOM via a count-only enumeration.** The existing `mates.expand`
  makes `kernel.request("resolve_assembly")` calls for polar patterns and
  sub-assemblies (transform composition); the BOM needs only counts, so
  `core/bom.py` walks the manifest structurally (patterns × count, sub-assembly
  recursion with a cycle guard) and makes **no** kernel calls.
- **Tag-capable ref materialization.** The PRD names the merge-validation
  worktree, but `branches.tree_of`/`SpecRunner._pinned` are **branch-only and
  refuse a tag**. `get_bom {ref}` and the release bundle must use `checks.py`'s
  tag-capable `_ephemeral_service` pattern — lifted into a shared
  `core/_worktree.py: materialized_service(service, ref)`.
- **The gate is nearly free.** `release_start` opens a PRD-002 proposal; the
  specs gate and CI gate are already in `service.gate_providers` and evaluate
  against every proposal, so releases read `proposal["gates"]` rather than
  re-invoking `evaluate_specs`/`CheckRunner.run`.
- **Materials already carry `cost_usd_kg`** — FR3's material-estimate fallback
  needs no schema change; a `cost_source` column keeps an estimate from being
  read as a quote (through CSV too).
- **Metrics-without-rebuild** reads `service._status` directly (like
  `get_project`), staleness via the pure `_cache_key_for` hash — but the cache is
  process-lifetime, so post-restart a part reads `unbuilt` (a documented warning,
  not a rebuild).
- **Immutability is mostly structural** — no write path can land on a tag's tree
  (you can only `branch_create(from_ref=tag)`), so the real guard is that a
  `released` record is append-only (`conflict_error` on mutation).
- **Approval is `proposal_review`** (the PRD prose's `approve_proposal` does not
  exist); the proposal gains an additive `kind: "release"`.

## Slices (mostly serial; frontend is disjoint)

1. BOM builder + `get_bom`/`set_bom_fields` + `bom` manifest field (FR1-3).
2. CSV/JSON exports + ref-pinned BOM + the shared tag-worktree helper (FR4-5).
3. Release records + state machine + `release_start` + proposal `release` kind +
   gate report + waive (FR6-8).
4. `release_finalize` + tag pin + referrer + immutability (FR9, FR12).
5. Reproducible bundle + `artifacts.json`/sha256 + README (FR10-11).
6. Frontend BOM view + Releases panel (Experience).
7. Acceptance (AC1-8) + docs.

## Notes

Docs-only commit (design spec, plan, PRD move) — no product code changed, so the
suite is unchanged from `main`. `make test` — **4550 passed, 38 skipped** (the
committed `main` tree this branch forked from; PRD-014 + PRD-006b). CI on the
three-OS matrix is authoritative as slices land.
