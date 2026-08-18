# 0215 — PRD-007 slice 3: the kernel-free anonymous viewer

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The anonymous viewer surface for share links: a logged-out visitor renders a
pinned part with **zero** kernel calls. The guard's allowlist grows by exactly
two prefixes (`/s/`, `/embed/`), the surface enumeration grows by the eight
share templates, and the main app now sends `frame-ancestors 'none'` while the
embed page opts into cross-origin framing.

## Changes
- **New `agentcad/server/routes_share_public.py`** (`PREFIX = ""`, root-mounted)
  — six viewer routes, each resolving the path token against the store and
  answering **one indistinguishable 404** (revoked/expired/unknown/wrong-secret)
  with the `routes_public._miss` cache header:
  - `GET /s/{token}` / `GET /embed/{token}` — the self-contained HTML shell, no
    cookie; the page-load bumps only the `views` counter (not asset fetches).
    Embed sets `Content-Security-Policy: frame-ancestors *` + `Referrer-Policy:
    no-referrer`; `/s/` sets `Referrer-Policy: no-referrer`.
  - `GET /s/{token}/model` — attribution + metrics + `default_variant_key`, read
    from the pin's cached sidecar. No kernel.
  - `GET /s/{token}/mesh/{key}` — the `.acm` bytes for a key already in the
    variant cache; **404-if-absent, never builds** (the `get_mesh_by_key`
    discipline).
  - `GET /s/{token}/params` — the typed slider spec, a cached JSON read.
  - `GET /s/{token}/script` — the pinned script iff `show_script`; else the same
    404 as any miss.
- **`agentcad/server/security.py`** — `PUBLIC_PREFIXES` gains `"/s/"` and
  `"/embed/"` (trailing slash load-bearing). New `response_headers(path)` +
  `FRAME_ANCESTORS_NONE`/`EMBED_PREFIX`: the founder-decision hardening header,
  `frame-ancestors 'none'` on every hosted response except `/embed/`.
- **`agentcad/server/app.py`** — the hosted middleware branch stamps
  `security.response_headers(path)` onto the response with `setdefault` (so the
  embed page's own policy wins). A header, not a route — the anonymous-surface
  equality test is untouched. Local mode is byte-identical.
- **`tests/test_hosted_surface.py`** — `EXPECTED_PUBLIC` grew by the eight
  `/s/`+`/embed/` templates; `NOT_YET_BUILT` holds the two customizer templates
  (`/variant`, `/download`) so slice 4 removes them; negation params `/s`,
  `/status`, `/svg`, `/embed`, `/embedding` added (the trailing-slash gotcha).

## Files
- `agentcad/server/routes_share_public.py` — new: the viewer pack
- `agentcad/server/security.py` — two prefixes + the CSP header seam
- `agentcad/server/app.py` — middleware stamps the hardening header
- `tests/test_hosted_surface.py` — enumeration grown + negation params
- `tests/test_share_viewer.py` — new: page opens (no cookie), model/params/mesh,
  mesh 404-without-build, script gated on `show_script`, revoked==expired==unknown
  404 bodies, kernel-silence sweep with a positive control, embed framable /
  main app not, view-counter bumps only on page load

## Notes
Verification: `pytest tests/test_share_viewer.py tests/test_hosted_surface.py`
→ **34 passed**; the slice-1–3 targeted suites (`test_ratelimit`,
`test_publications`, `test_share_publish`, `test_share_isolation`,
`test_share_viewer`) → **38 passed**; broader regression (share + surface +
guard + auth + presence + ratelimit + publications + server + appmode) → **228
passed** (2026-08-18). A full `make test` (uv run pytest -n 8) over these three
slices showed **no functional failures attributable to slices 1–3** (3947
passed; the failures observed were changelog count-citation *evidence* checks,
not code). The prior tree measured 3689 passed / 1 skipped (changelog 0199,
before PRD-012 merged) — cited as the prior tree's number, not this one's.

**Divergence from the PRD-007 plan (reported):** the plan (slice 3, step 2) said
to stage the two customizer templates in `NOT_YET_BUILT` and empty it in slice
4. That is incompatible with `tests/test_prd005a_acceptance.py::test_ac2...`,
which hard-asserts `NOT_YET_BUILT == set()` (the 005a surface is "finished"), so
a non-empty subtrahend turns a 005a acceptance test red for a whole slice.
Instead, slice 3 enumerates only the six viewer templates; PRD-007 slice 4 adds
`/s/{token}/variant` and `/s/{token}/download/{fmt}` to `EXPECTED_PUBLIC` **and**
mounts them in the same change (param-validation parity, per-link/per-IP token
buckets, a global in-flight semaphore, the export mask). This keeps every tree
green and stays reviewable — spec wins over the plan where they disagree.
