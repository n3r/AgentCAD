# 0227 — PRD-031a slice 6: docs, acceptance sweep and close-out

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
Closes PRD-031a: the AC1–AC9 acceptance sweep, the docs (AGENTS gotchas, agent
API, user guide, packages), and the PRD/roadmap carve reconciliation. The
marketplace ships as six anonymous routes (five kernel-free + one shared kernel
path) plus the seeded add-to-library, all over the completed 011 · 005a · 007
substrate — no new dependency, no core edit.

## Changes
- `tests/test_prd031a_acceptance.py`: **new** — one test per criterion, graded
  against the real anonymous surface and the bundled catalog. `_find_prd()` +
  property-based status (the 0164 close-out trap); AC1 (anonymous
  search → params → variant → mesh → STEP, no cookie); AC2 (browse/search/listing/
  script/params/preview/mesh reach **zero kernel** with a **positive control**);
  AC3 (surface equality including the six market templates + private-index
  indistinguishability across listing/variant/mesh); AC4 (cache coalescing =
  one build for two requests + the shared `service.customizer_guard` identity +
  the single-worker 503); AC5 (add-to-library pins version+`content_id` via the
  lockfile, on a **copy**); AC6 (`market_install` seeded-catalog scope); AC7
  (provenance surfaces read-only, no remix affordance); AC8 (OCP-free in a fresh
  interpreter); AC9 (the market view is wired and calls the routes it claims —
  the browser half **graded as evidence**). Every build-path test pins
  `AGENTCAD_KERNEL_POOL_SIZE >= 2` (the PR #20 CI trap).
- `AGENTS.md`: a "Marketplace gotchas (PRD-031a)" section — the one-shared-kernel-
  path invariant, the shared `customizer_guard`, `scope: public` dual filter, the
  digest-as-param-spec, the kernel-free mesh route, the fixed export set,
  `market_install` load order, the `search`-before-`{name}` order, and
  `routes_public` zero-kernel vs `routes_market` as the K pack.
- `docs/agent-api.md`: the `market_install` tool row and a "Marketplace catalog
  (PRD-031a)" route section (the six anonymous routes + the authenticated
  add-to-library).
- `docs/user-guide.md`: "Browsing the catalog (the Marketplace)".
- `docs/packages.md`: "The public marketplace read (PRD-031a)".
- `docs/prd/in-progress/PRD-031a-marketplace-catalog.md`: status → **implemented**;
  FR6a (the mesh read) folded in; the six-route surface and the browser posture
  (`list_connected_browsers` → `[]`, "never rendered by a browser") recorded.
- `docs/prd/pending/PRD-031-marketplace.md`, `docs/roadmap.md`: the carve marked
  implemented for chain step 4 (031b remainder unchanged).

## Files
- `tests/test_prd031a_acceptance.py` — the AC1–AC9 sweep
- `AGENTS.md`, `docs/agent-api.md`, `docs/user-guide.md`, `docs/packages.md`
- `docs/prd/in-progress/PRD-031a-marketplace-catalog.md`
- `docs/prd/pending/PRD-031-marketplace.md`, `docs/roadmap.md`

## Notes
- **Browser posture.** AC9 (and AC1's visual half) is graded as evidence — the
  pages were **never rendered by a browser** (`list_connected_browsers` → `[]`).
  The API criteria AC1–AC8 are the machine-checked backstop.
- **Verification.** `make test` → **PLACEHOLDER_COUNT** (the prior tree, changelog
  0224, was 4074 passed, 1 skipped; slice 4 added 5 mesh tests and slice 6 the
  AC1–AC9 sweep). Targeted suites all green: `test_market_api.py`,
  `test_market_customizer.py`, `test_market_install.py`, `test_prd031a_acceptance.py`,
  `test_hosted_surface.py`, `test_packages_api.py`; PRD-007/005a unaffected
  (`test_share_customizer.py`, `test_share_isolation.py`, `test_prd007_acceptance.py`,
  `test_prd005a_acceptance.py` green).
