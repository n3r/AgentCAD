# 0189 — PRD-005a slice 2: the default-deny security seam and composed principals

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

The authorization seam for hosted mode: a new `agentcad/server/security.py`
holding the `PUBLIC_PATHS` enumeration, principal resolution and the
default-deny guard; the one sanctioned `create_app` edit (`security=`, the
middleware body, the WebSocket guard call); three new `AppError` subclasses;
and the two-line `actor_kind` change without which PRD-008's per-part claims
would have silently stopped protecting anybody the day hosting turned on.
Local mode is the *same code path*, not a disabled feature.

## Changes

- **`agentcad/core/proposals.py` — `actor_kind` (FR10).** Two prefix tests in
  front of a byte-identical `browser`/else branch: `user:` → `human`,
  `agent:` → `agent`. `user:nikita/browser:7f3a1b2c` does not start with
  `browser:`, so before this every signed-in human classified as an *agent* —
  and `ClaimRegistry.acquire` returns `None` for a non-human holder while
  `_blocking` never blocks an agent, so no hosted person could hold a claim
  and nobody was protected from anybody. The docstring now records what it
  used to promise.
- **`agentcad/server/security.py` (new).**
  - `PUBLIC_PATHS` (3 exact) + `PUBLIC_PREFIXES` (`/api/public/`,
    `/api/auth/enrol/`, `/js/`, `/css/`, `/vendor/`) are the **entire**
    anonymous surface, as a literal in one file. There is deliberately no
    per-route `@public` decorator: a pack author must not be able to open the
    surface from their own module.
  - `Principal(kind, name, role, device, via)` composes
    `user:<handle>/<device>` or `agent:<name>`. `via` is load-bearing — the
    CSRF rule exempts bearers because a browser cannot attach one cross-site.
  - `resolve_principal` tries `Authorization: Bearer` first, then the session
    cookie, and **never falls back** from a present-but-invalid bearer to a
    valid cookie (a revoked token quietly becoming a session is a confused
    deputy). It never raises; a store that cannot be read yields no principal,
    which fails closed.
  - `guard` implements design Decision 7's ordered steps and always returns a
    structured `JSONResponse` rather than raising, so an auth failure cannot
    become a 500. `guard_websocket` does the same before `accept()`.
  - `current_config()` / `current_principal()` are per-request ContextVars
    with a module-level fallback that `create_app` installs, so a process
    hosting two apps cannot answer with the other one's configuration.
- **`agentcad/server/app.py` — the flagged core edit.** One `security=None`
  parameter; the middleware branches once at the top and everything after that
  branch is byte-identical; the WebSocket handler makes the same call; the
  health body is trimmed to `{status, mode}` without a principal.
- **`agentcad/core/model.py`** — `AuthError` (401), `AuthzError` (403; named
  to avoid shadowing the builtin `PermissionError`, which this codebase
  catches around filesystem work) and `RateLimitedError` (429), wired into
  `_ERROR_STATUS`.
- **`tests/conftest.py`** — the shared `hosted` / `hosted_client` /
  `hosted_app` / `kernel_counter` fixtures, a `login()` helper, a
  `CountingKernel` proxy, and `flatten_routes()`.

## Files

- `agentcad/server/security.py` — new
- `agentcad/server/app.py` — `security=`, middleware branch, WS guard, trimmed health
- `agentcad/core/proposals.py` — `actor_kind`
- `agentcad/core/model.py` — three `AppError` subclasses
- `tests/conftest.py` — hosted fixtures + `flatten_routes`
- `tests/test_actor_kind.py`, `tests/test_security_guard.py`,
  `tests/test_hosted_surface.py` — new
- `tests/test_claims.py` — two appended AC10 parity tests

## Notes

- **The enumeration test would have been blind.** The plan's version walks
  `[r.path for r in app.routes]`, and in FastAPI 0.141 that is **not** the
  route list: each `include_router` lands as one opaque `_IncludedRouter` with
  `path = None`, so the naive walk sees the 23 routes declared in `app.py` and
  **none of the ~60 in the sixteen route packs** — exactly the population the
  test exists to police. It would have passed, green, while a pack went
  public. `conftest.flatten_routes` recurses through `include_context`, and
  `test_the_route_walk_actually_sees_the_route_packs` cross-checks the result
  against `app.openapi()` (FastAPI's own independent traversal) so the trap
  cannot return silently. Walk: 83 routes; naive walk: 23.
- **Two real findings from my own adversarial tests**, both fixed:
  `X-Agent-Id: user:anya` was accepted as a *device* and composed to
  `user:nikita/user:anya` — not an impersonation (the principal stayed
  nikita), but a string the presence roster, claim map and comment author line
  all render, so an identity that reads as two people. `DEVICE_RE` now allows
  at most one colon, bans the `user:`/`agent:` prefixes and caps at 24
  characters (`user:` + 32 + `/` + 24 = 62 ≤ `MAX_CLIENT_ID_CHARS`).
- **The CSRF rule is applied to anonymous state-changing routes too**, not
  only to authenticated ones as Decision 7 literally says. A cross-site POST
  to `/api/auth/login` that signs a victim into the *attacker's* account is a
  real if quiet attack, and exempting it would have been an accident rather
  than a decision.
- **Divergence from the plan, forced:** the trimmed health body is assigned to
  slice 5, but slice 2's own `test_health_is_public_but_trimmed` requires it
  and `/api/health` joins `PUBLIC_PATHS` here — leaving it would have shipped
  an anonymous version/kernel/sandbox reconnaissance leak for three slices.
  Implemented here.
- **Every negation verified by breaking the code, not by assertion.** Removing
  the `user:`/`agent:` prefixes fails four tests including the two that drive
  the real `ClaimRegistry`; `test_the_kernel_counter_actually_counts` is the
  positive control without which AC7's `calls == 0` would pass just as happily
  with a broken counter.
- `test_every_other_route_answers_401_anonymously` sweeps all 60+ private
  routes with their templates filled, because the guard is middleware and
  answers **before** routing — which is also why a non-existent path is 401
  rather than a 404 that would map which paths exist. `/openapi.json`,
  `/docs` and `/redoc` are covered by default-deny with no action by anybody.
- Verification: `.venv/bin/python -m pytest tests/test_security_guard.py
  tests/test_hosted_surface.py tests/test_actor_kind.py tests/test_claims.py
  -q` → **104 passed**. Regression on the modules this touches
  (`test_server`, `test_proposals`, `test_comments`, `test_presence`,
  `test_locks`, `test_history`, `test_comments_api`, `test_packages_ocp_free`)
  → **199 passed**.
