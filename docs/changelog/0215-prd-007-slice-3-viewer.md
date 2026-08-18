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
→ **34 passed**; broader regression (share + surface + guard + auth + presence +
ratelimit + publications + server + appmode) → **228 passed** (2026-08-18). The
customizer routes `/s/{token}/variant` and `/s/{token}/download/{fmt}` are
enumerated but **not mounted** — slice 4 adds them with param-validation parity,
per-link/per-IP token buckets, a global in-flight semaphore and the export mask.
Prior full-suite baseline: 3689 passed / 1 skipped (changelog 0199, the prior
tree's measurement — PRD-012 merged after).
