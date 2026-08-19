# 0225 — PRD-031a slice 4: the kernel-free market mesh route (closing the gap)

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
Closes the one functional gap slices 1–3 flagged: the market listing customizer
returns a `mesh_key`, but there was **no anonymous route to fetch those `.acm`
bytes** — the browser viewport needs them. Adds `GET .../parts/{part}/mesh/{key}`,
a **kernel-free** read that serves a variant already in the build cache and 404s
an absent one, **never building** — the exact `/s/{token}/mesh/{key}` /
`routes_configs.get_mesh_by_key` discipline, scoped to the listing.

## Changes
- `core/share_build.py`: extracted `script_sha_for(script_bytes)` — the pin
  identity (`"sha256:" + sha256(bytes)`) computed **without** registering a build
  project, so the mesh route can locate a variant's cache path from the sha alone
  and never build. `ensure_catalog_pin` now uses it (behaviour-preserving).
- `server/routes_market.py`: new `GET
  /api/public/packages/{name}/versions/{version}/parts/{part}/mesh/{key}`. It
  resolves the listing through the SAME dual-`scope: public` `_resolve_catalog_part`
  the variant route uses (a private/nonexistent/non-customizable part is one
  indistinguishable `_miss`), computes the pinned `script_sha` with
  `script_sha_for`, and returns `ShareBuilder.mesh_path(...)` — which hex-gates
  `key` (`_is_cache_key`) against traversal and 404s an absent file. It is
  **kernel-free**, so it is NOT guarded or throttled (a pure disk read), and
  lives in `routes_market.py` beside `/variant` exactly as `/s/`'s kernel-free
  mesh read sits beside `/s/`'s customizer routes.
- `tests/test_hosted_surface.py`: the mesh template joins `EXPECTED_PUBLIC` in
  this change (`NOT_YET_BUILT` stays `== set()`); it is swept by the
  kernel-silence positive-control test and stays silent.
- `tests/test_packages_api.py`: the package-route inventory grows by the mesh
  route (still a GET, no write verb).
- `tests/test_market_customizer.py`: five new tests — the mesh serves a built
  variant's bytes (`X-Mesh-Key`, `no-store`), never builds (404 + kernel counter
  unmoved), makes no kernel call even on a hit, is hex-gated against traversal,
  and a private listing's mesh is byte-identical to a nonexistent one.

## Files
- `agentcad/core/share_build.py` — `script_sha_for` helper
- `agentcad/server/routes_market.py` — the mesh route
- `tests/test_hosted_surface.py` — `EXPECTED_PUBLIC` + one row
- `tests/test_packages_api.py` — inventory guard + one row
- `tests/test_market_customizer.py` — five mesh tests

## Notes
- **Positive control.** The mesh route is kernel-free — proven by the existing
  `test_public_surface_makes_no_kernel_calls` sweep (which now includes it) plus
  the positive control that a variant *does* build. `NOT_YET_BUILT == set()`
  stays true.
- **Verification.** Targeted, green: `test_market_customizer.py`,
  `test_hosted_surface.py`, `test_market_api.py`, the packages-api guard;
  PRD-007 unaffected (`test_share_customizer.py`, `test_share_isolation.py`,
  `test_prd007_acceptance.py` green — the `script_sha_for` extraction is
  behaviour-preserving). The full-suite count is cited in the slice-6 changelog
  (0227), the close-out.
