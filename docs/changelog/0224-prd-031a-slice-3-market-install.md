# 0224 — PRD-031a slice 3: add-to-library (`market_install`)

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The agent convenience for add-to-library: `market_install` composes PRD-011's
`add_package` + `use_part` into one call, **scoped to the seeded public catalog**
— a package resolvable only from a private index is refused before anything
installs. The browser "Add to library" path reuses the existing authenticated
package routes unchanged.

## Changes
- `core/tools_market.py`: **new** tool pack. `market_install(project, package,
  part, part_id, version_req?, preset?, params?)` reads `service.packages`
  **inside** the function (load order: `mar` < `pac`, so `service.packages` is
  not yet installed at `register` time), resolves the first **public-scoped**
  index carrying the package via the dual-scope filter (`configured_scope ==
  "public"` AND `scope == "public"` — the same rule `routes_public._public_indexes`
  applies), refuses a private-only/nonexistent package with a `not_found_error`,
  then `manager.add(project, package, version_req, index)` + `materialize(...)`.
  Returns `{project, package, index, lock, requirement_change, part}` — the lock
  entry pins `version`+`content_id` (PRD-011 AC3 inherited: byte-identical
  rebuild forever).
- `tests/test_market_install.py`: **new** — on a copy of the bundled catalog:
  the lockfile pin + index-`content_id` match (AC5), byte-identical
  re-materialisation, the provenance header names the package, a private-only and
  a nonexistent package both refused with nothing installed (AC6), and the
  browser package routes asserted private (`security.is_public` False).
- `tests/test_packages_api.py`: `test_the_gate_is_not_reachable_over_http` grows
  its expected package-route set by the five new anonymous PRD-031a GET routes
  (all read/customizer, no write/publish verb).

## Files
- `agentcad/core/tools_market.py` — new tool pack
- `tests/test_market_install.py` — new test module
- `tests/test_packages_api.py` — inventory guard grown

## Notes
- **Verification.** Targeted suites, all green:
  `test_market_api.py` (20), `test_market_customizer.py` (12),
  `test_market_install.py` (8); PRD-007 unaffected (`test_share_customizer.py` +
  `test_share_isolation.py` green, its limits unchanged — the `customizer_guard`
  extraction is behaviour-preserving). Full suite: `make test` →
  **4074 passed, 1 skipped** (prior tree, changelog 0220, was 3974 passed, 1
  skipped).
- The market variant returns a `mesh_key` with no anonymous market mesh-by-key
  route in slices 1–3 (the design's five-route surface stops at
  variant/download); the browser customizer viewport is slice 4.
