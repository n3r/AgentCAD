# 0190 — PRD-005a slice 3: enrolment, login, sessions and the sign-in view

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

A human can now be invited from the CLI and sign in from a browser. Adds the
`routes_auth.py` route pack (login / logout / session / enrol / admin users),
the `agentcad admin` subcommand group that operates directly on the state
files with no service and no kernel, and the frontend sign-in and enrolment
view behind a single 401 funnel. Verified end to end against a **real running
hosted instance**, including that a signed-in human actually holds a per-part
claim and that history attribution reads `user:nikita/browser:7f3a1b2c`.

## Changes

- **`agentcad/server/routes_auth.py` (new).** `POST /api/auth/login`,
  `POST /api/auth/logout`, `GET /api/auth/session`,
  `GET|POST /api/auth/enrol/{token}`, admin-only `GET|POST /api/auth/users`
  and `POST /api/auth/users/{handle}/disable`.
  - Reads its config from `security.current_config()`, never from `service` —
    putting identity on the service is what would drag it into
    `checks._ephemeral_service` and `gate._ephemeral_service`. Every handler
    answers `404` in local mode, so the pack is inert there.
  - **Both rate buckets are taken before the scrypt call** (address first, then
    handle), so a flood cannot buy 63 ms of CPU per request; the refusal is a
    `429` carrying `details.retry_after_s`, derived from the bucket's rate
    because `TokenBucket` exposes no remaining-token API and PRD-008 code is
    not this feature's to edit.
  - One failure message for every reason a sign-in can fail — unknown handle,
    wrong password, never enrolled, disabled — because telling them apart is a
    user-enumeration oracle.
  - Cookie is `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` iff the origin is
    https. Logout **deletes the row**, so a cookie copied beforehand is dead on
    its next use.
  - `GET /api/auth/enrol/{token}` content-negotiates: `Accept: text/html`
    gets `index.html` (the frontend reads the token out of the path), anything
    else gets `{handle, mode}`. Reading it never spends the token.
- **`agentcad/cli.py`** — `agentcad admin user add|list|disable` and
  `agentcad admin enrol`. Builds `AuthStore(state_dir()/"auth")` directly:
  **no service, no kernel, no port**, which is what makes
  `docker compose exec agentcad agentcad admin ...` cheap and what lets it work
  while the server is down. `user add` prints the enrolment URL and the trust
  sentence; `--help` carries it too (two of FR17's four places; the compose
  header and `docs/deployment.md` are slice 6). Errors exit 2 with the message
  on stderr.
- **`agentcad/cli.py` — `_security_config()`.** `cmd_serve` now passes
  `security=` when `AGENTCAD_MODE=hosted`. Nothing in the plan wired this, so
  before it no hosted instance could actually run — see Notes.
- **Frontend.** `frontend/js/auth.js` (session/login/logout/enrol,
  `renderSignIn`, `renderChip`); `api.js` gains the auth calls and dispatches
  `agentcad:unauthenticated` from the single `request()` funnel on a 401;
  `main.js` resolves identity before booting any panel and swaps the workbench
  for the sign-in view; `index.html` gains `#auth-view` and `#auth-chip`;
  `app.css` gains the identity styles, routed through the existing tokens so
  both themes are covered.

## Files

- `agentcad/server/routes_auth.py` — new
- `agentcad/core/authstore.py` — `peek_enrolment` (read without spending)
- `agentcad/cli.py` — `admin` group, `TRUST_SENTENCE`, `_security_config()`
- `frontend/js/auth.js` — new
- `frontend/js/api.js`, `frontend/js/main.js`, `frontend/index.html`,
  `frontend/css/app.css`
- `tests/test_auth_routes.py`, `tests/test_cli_admin.py` — new
- `tests/test_hosted_surface.py` — `NOT_YET_BUILT` shrinks by the three
  `/api/auth` entries, so the enumeration now *proves* they are anonymous and
  the kernel-silence test exercises them

## Notes

- **Verified against a real hosted server**, not only `TestClient`
  (`agentcad serve` on port 8641, scratch config and projects dir, no browser
  extension available — see the gap below). The whole invitation flow:
  `admin user add` printed a link; `GET /` served the app with `#auth-view`;
  anonymous `/api/auth/session` → 401; the enrol URL returned `index.html` to
  `Accept: text/html` and `{"handle":"nikita","mode":"hosted"}` to `*/*`;
  `POST` returned `{"principal":"user:nikita",...}` with a `Set-Cookie`; and
  the cookie plus `X-Agent-Id: browser:7f3a1b2c` composed
  `user:nikita/browser:7f3a1b2c`.
- **The payoff evidence, on that same live instance:** taking a claim returned
  `{"holder": "user:nikita/browser:7f3a1b2c", "holder_kind": "human"}` and the
  presence roster said `human`, and the history entry's author is
  `user:nikita/browser:7f3a1b2c`. That is AC3's attribution claim and slice
  2's `actor_kind` fix demonstrated in the running product rather than in a
  unit test — before the fix `holder_kind` could not have been `human` and
  there would have been no claim at all.
- **Gap, stated rather than papered over:** the Chrome extension was not
  connected in this session, so the plan's step-11 *screenshots* (sign-in
  view, workbench chip, lock chip during an edit) were **not** taken. What is
  verified is every HTTP contract the JavaScript depends on, plus JS parse
  checks; the visual pass is outstanding and AC3's "browser session" half is
  not yet evidence.
- **Two plan gaps found and closed.** (1) Nothing wired `security=` into
  `cmd_serve`, so hosted mode was unreachable outside tests; `_security_config()`
  does it, refusing to start on a `ModeError` rather than falling back to
  local — a server that quietly served an unauthenticated API because a
  variable was misspelled is the one failure this design will not have.
  (2) The enrolment URL the CLI prints is an *API* path a human pastes into a
  browser; without content negotiation that flow ends at a page of JSON.
- **Deliberate tightening beyond the plan:** admin routes require a signed-in
  **person** (`kind == "user"`), so an `admin`-role bearer token is `403`.
  Minting credentials from the same authenticated HTTP surface those
  credentials unlock is the privilege-escalation shape design Decision 14
  avoids while there is no audit log.
- **Behaviour pinned honestly where my first test was wrong:** `POST
  /api/auth/logout` with no session is **401**, not 200 — logout is not on the
  nine-entry public allowlist, and widening the allowlist to make a test pass
  would have been the wrong fix. The frontend treats 401 on logout as "already
  signed out".
- **Latent cross-app trap, found and closed.** `routes_auth` originally read
  `security.current_config()` — a process-global slot — on every request, so
  two apps in one process could cross-wire: a *local* app built before a
  hosted one would find the hosted config still installed and quietly grow
  working auth routes backed by the other app's identity store. The router now
  binds its configuration **at mount time** (`create_app` calls `install()`
  before `_mount_route_packs`, so the captured value is exactly this app's),
  and `test_a_local_app_built_after_a_hosted_one_has_no_auth_routes` pins it
  in both directions. One app per process in production, but the test suite
  builds both constantly and a future embedder would too.
- `agentcad admin enrol` is included with user management (the design lists it
  in the same breath); `admin token` remains slice 4.
- **One existing test edited, deliberately.** `test_packages_cli.py::
  test_help_lists_package_beside_the_other_commands` pinned the subparser
  metavar as one literal string, and `admin` now belongs in it — the test's own
  docstring says "a command missing from it is a command nobody finds". It now
  asserts per-command *and* keeps the full literal, so the next command to land
  fails on its own merits rather than on punctuation. This is the only
  pre-existing test any of slices 1-3 touched.
- Verification: `.venv/bin/python -m pytest tests/test_auth_routes.py
  tests/test_cli_admin.py tests/test_hosted_surface.py -q` → **74 passed**;
  `tests/test_security_guard.py tests/test_auth_routes.py` → **84 passed**.
  Full gate for slices 1-3: `make test` → **3532 passed, 1 skipped in
  550.95 s** (8 workers, this machine). The prior tree's measurement, taken on
  this branch at `e8645f4` before slice 1: **3316 passed, 1 skipped in
  528.05 s** — so the three slices add 216 tests and no regressions. (The
  0187 entry's 3310/7 was a different tree and is not this baseline.)
