# 0222 — PRD-031a slice 1: public browse/search API (kernel-free)

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The anonymous marketplace data surface: public, refresh-free search with a
licence facet, the read-only part script, and the pre-generated param spec —
three new routes on the existing `/api/public/packages` family, all reading only
the pre-generated `index.json` digest and shipped assets. Zero kernel, zero
network, `scope: public` only.

## Changes
- `core/packages/search.py`: `search()` gains `refresh: bool = True` (the
  anonymous caller passes `False`, so no `index.refresh()` git fetch on the
  no-credential path — PRD-005a's M2 rule) and a `license` single AND filter
  (the PRD-031a licence facet, case-insensitive, adds `why: license:<x>`). The
  default preserves every existing caller byte-for-byte. `_score` grows the
  matching `license` argument.
- `server/routes_public.py`: three new routes, all `CACHE_CONTROL` + `_miss`,
  all via `_public_indexes` (dual `scope: public`) / `_find`:
  - `GET /api/public/packages/search` — declared **before** `/{name}` so
    Starlette does not bind `{name} == "search"`; calls
    `search.search(_public_indexes(service), …, refresh=False)`.
  - `GET .../versions/{version}/script/{part}` — the read-only `.py` text, via
    `index.fetch` (local) + `content.resolve_within` with a fixed `.py` suffix.
  - `GET .../versions/{version}/params/{part}` — the digest `parts[part].params`
    list (Decision 3: no `inspect`, so browse stays zero-kernel).
- `tests/test_hosted_surface.py`: `EXPECTED_PUBLIC` grows the three kernel-free
  templates (and the two slice-2 kernel templates, mounted in slice 2's change);
  `NOT_YET_BUILT` stays `== set()`.
- `tests/test_market_api.py`: new — the search refresh/license unit tests, the
  three routes, the private-index-never-surfaces + byte-identical-miss checks,
  kernel silence at the pack door, and an OCP-free import assertion.

## Files
- `agentcad/core/packages/search.py` — `refresh`/`license` parameters
- `agentcad/server/routes_public.py` — search/script/params routes
- `tests/test_hosted_surface.py` — `EXPECTED_PUBLIC` growth
- `tests/test_market_api.py` — new test module

## Notes
The two `K` customizer routes are listed in `EXPECTED_PUBLIC` here but mounted in
slice 2's `routes_market.py` — the surface-equality test only goes green once
slice 2 lands (both in this branch). Route order is the one gotcha: `search`
before `{name}`.
