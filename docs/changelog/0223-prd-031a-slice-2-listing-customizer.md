# 0223 — PRD-031a slice 2: the listing customizer (one shared kernel path)

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The one anonymous kernel path for the marketplace: a logged-out visitor rebuilds
and downloads a bounded variant of a seeded catalog part. It reaches `exec()`
through PRD-007's containment reused **verbatim** — no second set of limits — via
a thin `ShareBuilder.build_catalog_variant` and a per-IP guard **shared** with
`/s/`.

## Changes
- `core/share_build.py`:
  - Extracted the tail of `build_variant`/`export_variant` into spec-taking cores
    `_validate` / `_variant` / `_export`, so the share-link path and the catalog
    path run the SAME validate → clamp → cache-probe → in-flight-slot wall.
    `_validated_params` is now a thin share-link adaptor (behaviour-preserving).
  - Added `ensure_catalog_pin(script_bytes, material)` (content-addresses the
    bytes, registers the build project; **no `_params_spec` kernel call** — the
    spec comes from the index digest), `build_catalog_variant(script_sha,
    material, spec, params)` and `export_catalog_variant(...)`. The catalog
    version's `content_id` is the pin — no `Publication`, no share token.
  - Added `CustomizerGuard` (per-IP `TokenBucket` + `_HourlyCounter` login gate +
    `client_host`) and the rate/gate constants, moved here from
    `routes_share_public`. `ensure_share` now installs `service.customizer_guard`
    once — the ONE per-IP bucket both anonymous customizer surfaces share, so a
    visitor cannot double their per-address allowance (AC4).
- `server/routes_share_public.py`: the per-IP throttle + login gate + client-host
  now delegate to `service.customizer_guard`; the per-**link** bucket stays
  route-local. `/s/` behaviour is byte-identical (PRD-007 suites green).
- `server/routes_market.py`: **new** pack (`PREFIX="/api"`) — the two `K` routes
  `.../parts/{part}/variant` and `.../parts/{part}/download/{fmt}`. Order is the
  containment: dual-scope `_find` (404), confirm the part is customizable before
  the builder, `require_customizer_capacity()` (503), the shared gate + per-listing
  and per-IP buckets, `ensure_catalog_pin`, then the build. Download is gated by
  the fixed export set `{step, stl, 3mf}` (= `service.EXPORT_FORMATS`); a format
  outside it 404s before the builder.
- `tests/test_hosted_surface.py`: the two `K` templates were added to
  `EXPECTED_PUBLIC` in slice 1's edit; they are mounted here, so the surface
  equality test is green. `NOT_YET_BUILT` stays `== set()`.
- `tests/test_market_customizer.py`: **new** — AC1 end-to-end (anonymous
  search→params→variant→STEP), AC4 (cache coalescing, clamp coalescing, the
  single-worker 503, the shared per-IP bucket drained on both surfaces, the
  guard-identity assert), the export-mask 404-before-builder, param-parity kernel
  silence, and the private-listing indistinguishable miss.

## Files
- `agentcad/core/share_build.py` — cores + catalog builds + `CustomizerGuard`
- `agentcad/server/routes_share_public.py` — delegate to the shared guard
- `agentcad/server/routes_market.py` — new kernel pack
- `tests/test_market_customizer.py` — new test module

## Notes
The market variant returns a `mesh_key`; there is deliberately **no** anonymous
market mesh-by-key route in slices 1–3 (the design's five-route surface stops at
variant/download). The browser customizer viewport that needs the rebuilt mesh
bytes is slice 4 (UI) — flagged, not pulled forward. `PIN_MATERIAL` is
`DEFAULT_MATERIAL` (the digest carries no per-part material) — geometry is exact;
a mass metric uses the default density.
