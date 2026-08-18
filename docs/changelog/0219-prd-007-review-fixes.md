# 0219 — PRD-007 share-customizer security review fixes (M-1, M-2, m-2, m-3)

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
An independent security review of the share-links customizer returned
CHANGES-REQUIRED. The core invariant held (anonymous cannot reach *unbounded*
kernel work — the semaphore, per-link/per-IP buckets on the proxy-resolved
address, timeout, and param parity are all intact and untouched). The two
blocking findings were places where a doc/claim asserted a containment the code
did not provide. Both are fixed with real code, not just narrowed prose; two
cheap non-blocking edges are fixed too.

## Findings and what shipped

### M-1 — "pool-affinity segregation" was not segregation; default cap could exceed the pool (REAL FIX)
`affinity="share:<pub>"` is `KernelPool._pick`'s consistent-hash routing
(`hash(affinity) % size`) — cache warmth, **not** isolation. With
`DEFAULT_MAX_INFLIGHT=2` on the Compose-default single-worker pool, two
anonymous builds could occupy members' sole worker.

- **Reproduce (before):** on `AGENTCAD_KERNEL_POOL_SIZE=1` the anonymous
  in-flight cap was **2 > pool size 1** — anonymous builds could take the only
  worker.
- **Fix:** the effective in-flight cap is now
  `min(SHARE_MAX_INFLIGHT, max(0, pool_size - 1))` (`effective_max_inflight`),
  reserving ≥1 worker for members. `pool_size` is read from the same source the
  pool is built from (`config.get_kernel_pool_size`). On a single-worker pool
  the effective cap is **0**, and `/variant`/`/download` return a structured
  **`503 ServiceUnavailableError`** naming `AGENTCAD_KERNEL_POOL_SIZE`
  (`require_customizer_capacity`) — never a build. Viewer links (kernel-free)
  are unaffected and still work on a 1-worker pool.
- **Compose default bumped `1 → 2`** so the customizer works out of the box AND
  a member worker stays free (chosen over doc-only guidance: friendlier, and it
  makes the reservation meaningful by default).
- **Every "segregat*" claim about `affinity` corrected** to "consistent-hash
  cache-warmth routing" in `share_build.py`, `docs/deployment.md`, `AGENTS.md`,
  the design spec threat table, and the PRD.
- **Verify (after):** `effective_max_inflight` returns
  `0/1/1/2/3/2` for `(pool,ceiling)` of `(1,-)(2,-)(2,5)(3,2)(4,10)(8,-)` and is
  always `≤ pool-1`; on pool=1 `/variant` and `/download` → `503` naming the
  knob with **0** kernel calls, while `/model` and `/params` → `200`.

### M-2 — variant cache key computed pre-clamp, so clamp-equal floods didn't coalesce (REAL FIX)
`_variant_cache_key` used the normalized (un-clamped) params; the worker clamps
only *inside* the build and `_cache_key_for` hashes the record's pre-clamp
params, so `size=100000..100004` (all clamp to max=40) minted 5 distinct keys
and 5 builds.

- **Reproduce (before):** 5 clamp-equal requests → **5 distinct keys, 5 kernel
  builds, 6 .acm files** (5 + the pin's default warm).
- **Fix:** the numeric range clamp is factored into a shared pure helper
  `agentcad/kernel/paramclamp.py` (no build123d import, so the server may import
  it). `worker._resolve_numeric` calls it; `share_build._clamp_params` calls it
  **server-side before the cache key** and passes the clamped params to both the
  key computation and the build, so parity is structural, not copied. Clamp
  warnings are captured server-side and merged into the response on both the
  cached and the fresh path, so a clamped request still warns.
- **Verify (after):** 5 clamp-equal requests → **1 distinct key, 1 kernel build**,
  each still warns; positive control: 3 distinct *in-range* values → 3 keys, 3
  builds. The `.acm` file content for two clamp-equal requests is identical (same
  geometry, one file).
- **Honest residual (narrowed claim):** a genuinely-distinct **in-range** flood
  STILL builds each variant, and the on-disk variant cache is **unbounded** until
  PRD-006 adds a disk budget. "popular = cheap" now reads "popular *or
  out-of-range* = cheap; distinct in-range still builds"; the login gate is the
  interim backstop. Stated in `docs/deployment.md`, the spec, and the PRD.

### m-2 — a NaN numeric bypassed the clamp and returned a degenerate 200 (FIX)
`value < mn` and `value > mx` are both False for NaN, so `size=nan` slipped past
the clamp to `build(p)`. Added a `paramclamp.is_nan` guard in
`worker._resolve_numeric` (raises `WorkerError`/contract) and in
`share_build._clamp_params` (raises `ValidationError` before any kernel call).
`inf` is deliberately NOT rejected — it clamps to max, as before.
- **Verify:** `size=nan` → `422` with **0** kernel calls; `size=inf` → `200`
  with a clamp warning.

### m-3 — numeric-string enum choices were unselectable (FIX)
`_coerce_query_value` coerced any enum query value to a number when parseable, so
a choice declared as the string `"1"` could never be selected (the query `"1"`
became int `1`, which `!= "1"`). Now the raw string wins if it is a declared
choice, falling back to the numeric coercion only when *that* is a member.
- **Verify:** a script with `choices: ["1","2"]` accepts `mode="2"` → `200`.

### m-4 — mid-flight env change transiently exceeding the cap (KNOWN EDGE, not fixed)
The in-flight semaphore is rebuilt when its effective size changes; a change
between an in-flight holder acquiring the old object and the rebuild is not
retroactively counted. This is inherent to the read-env-every-call design and
bounded (the next steady state is correct); draining is not worth the
complexity. Recorded as a known minor edge.

## Changes
- `agentcad/kernel/paramclamp.py` — **new** pure module: `clamp_numeric`,
  `is_nan`, `NUMERIC_TYPES`. No build123d/OCP import (server-importable).
- `agentcad/kernel/worker.py` — `_resolve_numeric` delegates the clamp to
  `paramclamp` and rejects NaN.
- `agentcad/core/share_build.py` — `effective_max_inflight` /
  `require_customizer_capacity` / `_pool_size` (M-1 reservation + 503);
  `inflight_semaphore` sized by the effective cap; `_clamp_params` (M-2
  server-side clamp + NaN guard); `build_variant`/`export_variant` gate on
  capacity and merge clamp warnings; `_coerce_query_value` enum fix (m-3);
  comments corrected (affinity is not segregation).
- `agentcad/core/model.py` — new `ServiceUnavailableError` (503).
- `agentcad/server/app.py` — map `ServiceUnavailableError → 503`.
- `agentcad/server/routes_share_public.py` — header prose corrected: the path
  token is kept out of `Referer` (`no-referrer`) but IS logged by a reverse
  proxy; revocation/expiry are the real mitigation.
- `compose.yaml` — `AGENTCAD_KERNEL_POOL_SIZE 1 → 2`.
- `docs/deployment.md`, `AGENTS.md`, the design spec, the PRD — segregation
  claim corrected; the reservation, the 503, the log caveat, and the unbounded
  disk residual documented.
- `tests/test_share_customizer.py` — autouse `_customizer_pool` fixture pins the
  declared pool size (the single-client kernel fixture makes the customizer
  host-independent); new tests for the reservation, the pool-of-one 503, M-2
  coalescing + positive control, the NaN guard + inf control, and the
  numeric-string enum.

## Files
- `agentcad/kernel/paramclamp.py` — new shared clamp helper
- `agentcad/kernel/worker.py` — clamp via helper + NaN guard
- `agentcad/core/share_build.py` — reservation, 503, server-side clamp, enum fix
- `agentcad/core/model.py` — `ServiceUnavailableError`
- `agentcad/server/app.py` — 503 mapping
- `agentcad/server/routes_share_public.py` — token-log claim corrected
- `compose.yaml` — pool default 2
- `docs/deployment.md`, `AGENTS.md`,
  `docs/superpowers/specs/2026-08-18-share-links-customizer-design.md`,
  `docs/prd/in-progress/PRD-007-share-links-customizer.md` — claim corrections
- `tests/test_share_customizer.py` — new + adjusted tests

## Notes
- **Not weakened:** the global in-flight semaphore, the per-IP key
  (`request.client.host`, never a hand-parsed `X-Forwarded-For`), the param
  parity (`normalize_params`), and the kernel boundary. Verified
  `import agentcad.core.share_build` pulls neither `build123d` nor `OCP`.
- The clamp helper is the single source of range logic — worker and share_build
  both call it, so they cannot drift.
- Targeted suites green: `test_share_customizer` (26), `test_share_viewer`,
  `test_share_publish`, `test_share_isolation`, `test_publications`,
  `test_ratelimit`, `test_hosted_surface`, `test_prd007_acceptance`, and
  `test_kernel` (worker clamp) — **140 passed** together. Full suite not run
  here (contended machine); prior tree measured ~3994 (0218).
