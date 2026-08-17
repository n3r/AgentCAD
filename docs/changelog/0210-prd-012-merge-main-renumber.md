# 0210 — PRD-012 branch: merge main (PRD-005a) and renumber the entries

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov (with Claude)

## Summary

`origin/main` moved while PRD-012 was built: PRD-005a (hosted core, PR #17)
landed with changelogs `0188`–`0199`. This commit merges main into
`prd-012-configurations`, resolves seven conflicts, and renumbers the ten
PRD-012 entries from `0188`–`0197` to `0200`–`0209` (sequence order is
authoritative history order, so both PRDs' entries keep their own order and
neither collides).

## Changes

- Changelog renumbering (a `git mv` each, headers and every reference
  updated — the plan's slice map, `tests/test_prd012_acceptance.py`'s evidence
  paths, `CLAUDE.md`/`AGENTS.md`'s PRD-012 lines, cross-references inside the
  entries): `0188-prd-012-design` → `0200`, `0189-configs-model-and-resolution`
  → `0201`, `0190-config-build-path` → `0202`, `0191-configs-merge-granularity`
  → `0203`, `0192-configs-tools-and-routes` → `0204`,
  `0193-config-bound-instances` → `0205`,
  `0194-config-artifacts-drawings-specs-ci` → `0206`, `0195-configs-ui` →
  `0207`, `0196-prd-012-docs-and-acceptance` → `0208`,
  `0197-prd-012-review-fixes` → `0209`.
- Conflict resolutions: `agentcad/server/app.py` (both new imports — the
  strict `_object_body` reader and `security_module`), `frontend/js/main.js`
  (both new module imports), `frontend/css/app.css` (both appended blocks —
  configurations, then identity), `CLAUDE.md` (both trap lines —
  configurations, then hosted core), `AGENTS.md` (both gotcha sections;
  the Determinism bullet keeps PRD-012's "nothing new in the cache-key
  payload" sentence on top of PRD-005a's rewritten Security/trust bullet),
  `docs/agent-api.md` and `docs/architecture.md` (the tool count).
- Tool count re-measured on the merged tree: **85 tools in local mode
  (88 with `[fem]`)**; a hosted instance registers `whoami` on top.
  PRD-005a's "83/86" counted from its own branch; PRD-012's five tools land
  on top of PRD-005a's surface, and `tools_auth` registers nothing in local
  mode, so 85 is what `build_registry` reports here.

## Files

- `docs/changelog/02{00..09}-*.md` — renamed (see above), headers updated
- `docs/superpowers/plans/2026-08-17-configurations.md`, `tests/test_prd012_acceptance.py`,
  `CLAUDE.md`, `AGENTS.md`, `docs/agent-api.md`, `docs/architecture.md`,
  `frontend/js/main.js`, `frontend/css/app.css`, `agentcad/server/app.py` —
  merge resolutions / renumbered references
- `docs/changelog/0210-prd-012-merge-main-renumber.md` — this entry

## Notes

Merged-tree verification: the integration-sensitive modules first
(`test_prd012_acceptance`, `test_prd011_acceptance`, `test_prd005a_acceptance`,
`test_server`, `test_configs_api`, `test_auth_routes`, `test_hosted_surface` —
183 passed), then the full suite: `make test` — 3906 passed, 7 skipped in 8:47 on 8 workers; the one red row was PRD-009's wall-clock analysis-budget assertion `tests/test_sketch_diagnostics.py::test_the_full_budget_completes_the_same_analysis` (untouched by either PRD; passes alone, 37/37).
