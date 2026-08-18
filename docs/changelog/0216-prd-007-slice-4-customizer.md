# 0216 — PRD-007 slice 4: the customizer rebuild and its caps

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The customizer: `GET /s/<token>/variant` and `GET /s/<token>/download/{fmt}` —
the **first anonymous requests that legitimately reach `exec()`** in the kernel.
Both are gated (the owner-written `customizer` flag / the export mask), capped
(per-link + per-IP token buckets, a global in-flight semaphore, pool-affinity
segregation), param-validated to the authoring path's own parity, and served
from a content-addressed cache so a repeat slider stop is free.

## Changes
- **`agentcad/core/share_build.py`** — the caps and the public build path:
  - A **global `BoundedSemaphore`** (`inflight_semaphore()`), sized by
    `AGENTCAD_SHARE_MAX_INFLIGHT` (default 2), acquired **non-blocking** around
    the build call only — over-limit is a `RateLimitedError`
    (`retry_after_s`), never a queued request thread. The size is re-read from
    the env each call and the semaphore rebuilt only when it changes, so within
    one setting it is the single shared object the whole process contends on.
  - `build_variant(pub_id, params)`: **validate → probe cache → cap**. Params
    are coerced from query strings to their declared types
    (`_coerce_query_params`) and refused with the authoring path's own
    `service.normalize_params` (wrong type, non-member enum, unknown name → a
    `validation_error` **before** any build); the out-of-range clamp-with-warning
    is inherited from `worker._resolve_params` inside the build. A repeat param
    set is a pure disk read (`_variant_cache_key` computes the key without
    building) — **no slot, no kernel**. A fresh build takes the semaphore and
    runs through the existing uncapped `_build` (`_build_with`,
    `affinity="share:<pub>"`, `status_key=None`).
  - `export_variant(pub_id, params, fmt)`: same param parity and in-flight cap;
    the export is **content-addressed** (`exports/<cache_key>.<fmt>`), so a
    repeat download is a disk read with zero kernel calls.
- **`agentcad/server/routes_share_public.py`** — the two customizer routes,
  mounted (the anonymous surface is now the full eight of design Decision 2):
  - `/variant`: resolve token (404) → `customizer:false` **404 before the
    builder** (the escalation boundary, structural) → login-gate knob →
    per-link + per-IP buckets → `build_variant` → bump `rebuilds`.
  - `/download/{fmt}`: 404 unless **customizer AND `fmt` in the mask** (a
    download carries params, so it is a rebuild — this closes a
    params-via-download escalation) → same caps → streamed as
    `<part>_<hash8>.<fmt>` → bump `downloads`.
  - Per-**app** `TokenBucket`s (`0.5/s`, burst 15) keyed `share:<pub_id>` and
    `addr:<client_host>`; the address is `request.client.host` (the
    proxy-resolved value), **never** a visitor-controlled `X-Forwarded-For` —
    the PRD-005a M3 lesson.
  - `_HourlyCounter` backing `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE` (off unless
    set): above N anonymous rebuilds/hour from one address, `/variant` requires
    a session (401). A signed-in member is never gated.
- **`tests/test_hosted_surface.py`** — `EXPECTED_PUBLIC` grows by the two
  customizer templates, mounted in the **same change** (`NOT_YET_BUILT` stays
  `== set()`, which `test_prd005a_acceptance.py` hard-asserts). The
  kernel-silence sweep now covers all eight `/s/`+`/embed/` routes.

## Files
- `agentcad/core/share_build.py` — in-flight semaphore, param coercion,
  `build_variant`, `export_variant`, `_variant_cache_key`
- `agentcad/server/routes_share_public.py` — `/variant` + `/download` handlers,
  per-app buckets, the login-gate counter, proxy-aware client host
- `tests/test_hosted_surface.py` — the two customizer routes join `EXPECTED_PUBLIC`
- `tests/test_share_customizer.py` — new: the AC battery with a negation per wall

## Notes
Verification: `pytest tests/test_share_customizer.py tests/test_hosted_surface.py`
→ **44 passed**; `pytest tests/test_prd005a_acceptance.py tests/test_share_viewer.py
tests/test_share_publish.py tests/test_share_isolation.py` → **45 passed**
(2026-08-18). Each containment property has a negation test: the cap is
consulted (a held slot forces 429; releasing it lets the same request build —
positive control), an out-of-spec param never reaches build (422, counter
unchanged), a repeat variant/download rebuilds nothing (positive control: a
DISTINCT param set does build), a `customizer:false` link 404s `/variant` before
the builder, and the per-IP bucket ignores a forged `X-Forwarded-For`.

**What a malicious visitor can STILL do within the caps:** CPU burn — distinct
param sets, each a bounded build, at the token-bucket rate, at most
`SHARE_MAX_INFLIGHT` at once, on a pool slice segregated from members'
(`affinity="share:<pub>"`). That bounded compute cost is accepted. The named
residual is **peak memory: a params-driven mesh can balloon RSS and OOM the
host — unbounded until PRD-006** (which also owes pid caps, variant-cache disk
budget, and worker egress denial). Until then the extreme is bounded by operator
posture and the `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE` backstop (off by default).
