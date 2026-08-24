# 0346 — PRD-005 slice 3: OIDC sign-in and WebAuthn passkeys (FR1)

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Nikita Fedorov

## Summary
FR1's two modern sign-in doors, on top of PRD-005a's identity store and
without touching it: an OpenID Connect relying party (authorization code +
PKCE, hand-rolled on `httpx` + `pyjwt`) and WebAuthn passkeys (behind a new
`agentcad[cloud]` extra, imported lazily, 501 without it). Both end at the
**same session cookie** `POST /api/auth/login` already mints. Registration
stays closed: an OIDC identity signs in a handle it is *linked* to and never
creates one.

## Changes
- **`agentcad/core/oidc.py` (new).** `OidcConfig` read from a new
  `<state>/auth/oidc.json` document (issuer, client id/secret, scopes, label,
  `allowed_email_domains`, `email_handles`); `OidcClient` with cached
  discovery (issuer asserted), `begin()` / `authorization_url()` /
  `complete()`, HTTP **Basic** client authentication (falling back to
  `client_secret_post` only when the provider advertises no basic), and
  ID-token validation with an algorithm **allowlist that cannot contain
  `none`**, `iss`/`aud`/`exp`/`iat`/`sub` required, `azp` checked on a
  multi-audience token, and `nonce` compared with `hmac.compare_digest`.
  JWKS is fetched with **httpx** (never `jwt.PyJWKClient`, which uses
  `urllib` and ignores a hosted instance's proxy/CA configuration), cached by
  `kid`, and refetched on a miss at most once per 30 s (key rollover without
  handing a stranger a load generator).
- **Linking policy** (`sign_in_handle` / `link_identity`): an already-linked
  identity signs its handle in; otherwise a **verified** email that the
  instance's own `email_handles` map names is auto-linked to an existing
  handle; otherwise one refusal message for every reason. An invited handle
  (disabled **and never enrolled**) is opened by its first SSO sign-in; a
  *revoked* one (disabled and enrolled) is not. Unverified emails never link.
- **`routes_auth.py`.** `GET /api/auth/oidc/login` (302, 404 unconfigured),
  `GET /api/auth/oidc/callback` (JSON, or 303 into the app for a browser —
  the `enrol_page` precedent), `POST`/`DELETE /api/auth/oidc/link`
  (authenticated); passkeys: `POST /api/auth/passkey/register/begin|complete`
  (signed-in person only — a bearer gets 403, Decision 14's shape),
  `POST /api/auth/passkey/login/begin|complete` (usernameless via discoverable
  credentials, with an optional handle hint), `GET /api/auth/passkeys` and
  `DELETE /api/auth/passkeys/{id}`. `webauthn` is imported inside the handlers
  (~105 ms) and `passkeys_available()` is a `find_spec`, so the absent extra is
  a 501 `PasskeysUnavailable` with the FEM wording.
- **Login-CSRF stop.** `oidc.FLOW_COOKIE` (`HttpOnly`, `SameSite=Lax`,
  `Path=/api/auth/oidc`) binds an authorization request to the browser that
  started it. `state` alone proves the flow started *here*, not *in this
  browser*: without the cookie an attacker's `code`+`state` fed to a victim's
  browser signs the victim in as the attacker. The link flow additionally
  re-reads the session at the callback rather than trusting the pending record.
- **`authstore.py` (additive only).** Per-user `passkeys: [...]` and
  `oidc: {...}` fields with `add_passkey` / `get_passkeys` / `find_by_passkey`
  / `update_sign_count` / `remove_passkey` / `link_oidc` / `find_oidc` /
  `find_by_oidc` / `unlink_oidc`, all schema-tolerant (absent or malformed
  reads as "none"), all atomic RMW under `_scope`. `link_oidc` is write-free
  when nothing changed, so an SSO sign-in does not fsync `users.json` every
  time. `oidc.json` joins `DOCUMENTS` with `read_oidc`/`write_oidc`. No
  existing field is reshaped and `list_users` is untouched.
- **`security.py`** — the anonymous surface grows by **four exact paths**
  (`/api/auth/oidc/login`, `/api/auth/oidc/callback`,
  `/api/auth/passkey/login/begin`, `/api/auth/passkey/login/complete`).
  Deliberately not prefixes: `/api/auth/oidc/` would have opened the link
  route and `/api/auth/passkey/` the register ceremony.
- **`pyproject.toml`** — `pyjwt[crypto]>=2.10` promoted to an explicit
  dependency (it was an undeclared transitive of `mcp`); new
  `cloud = ["webauthn>=3.0", "cbor2>=5.6"]` extra.

## Files
- `agentcad/core/oidc.py` — new: config, pending flows, the RP, linking policy
- `agentcad/server/routes_auth.py` — the OIDC and passkey routes, the
  challenge store, `passkeys_available`, `_set_session` widened to `Response`
- `agentcad/core/authstore.py` — passkey/OIDC accessors, the `oidc.json` document
- `agentcad/server/security.py` — four `PUBLIC_PATHS` entries
- `pyproject.toml` / `uv.lock` — the explicit pyjwt dep and the `cloud` extra
- `tests/test_oidc.py` — new: in-process mock IdP (RS256, rotatable key)
  behind an `httpx.MockTransport`; 30 tests
- `tests/test_passkeys.py` — new: the spike's virtual ES256 authenticator
  driven through the real routes; 27 tests
- `tests/test_hosted_surface.py` — `EXPECTED_PUBLIC` grown by four, with nine
  new negation params for the near misses (`NOT_YET_BUILT` stays `== set()`)

## Verification
`make test` (uv run pytest -q -n auto) on this tree: **6782 passed**, 51
skipped, 18 failed in 978 s — and every one of those eighteen is accounted
for, none of them in this slice's surface:

- **Twelve** are the `..._the_full_suite_count_is_cited` guards across
  PRD-005a/006/007/008/009/010/011/012/017/026/027/029/031a. They read the
  *newest* changelog entry, which is this one, so they are red until this
  section exists — the chicken-and-egg every slice's first full run meets.
- **Six** are environmental, on a box running two `-n auto` suites at once
  (a second agent's slice): `test_share_publish`, both `test_supervisor`
  kill-path tests and `test_sketch_diagnostics`'s timing assertion all pass on
  re-run in isolation (48 passed in 114 s), and
  `test_prd028_acceptance::test_ac6_real_solver_static_on_c24_base_plate` is a
  120 s `pytest-timeout` on a real gmsh + scikit-fem solve, reproducible on
  this machine with the slice reverted and untouched by anything here (this
  change adds no kernel call, no FEM path and no import to the worker).

The slice's own gates: `tests/test_oidc.py` (30), `tests/test_passkeys.py`
(24), `tests/test_hosted_surface.py`, `tests/test_hosted_hardening.py`,
`tests/test_prd005a_acceptance.py`, `tests/test_security_guard.py`,
`tests/test_authstore.py`, `tests/test_prd007_acceptance.py` — 262 passed.

## Notes
- **Where the in-flight state lives:** in memory, per app, TTL'd and capped
  (`oidc.PendingFlows`, `routes_auth._ChallengeStore`). A `state`/`nonce`/PKCE
  verifier and a WebAuthn challenge are nonces, not identity, and a hosted
  AgentCAD is one uvicorn process (`cli.cmd_serve` passes no `workers=`; the
  kernel pool, event bus and turn locks are already in-process singletons).
  Putting them in `authstore` would add an flock + fsync to the sign-in path
  and a fifth thing to prune, to protect a record that lives minutes and whose
  loss costs one retry. If the server ever becomes multi-process, these two
  classes are what move — nothing else.
- The provider is a **config document**, not environment variables: an admin
  edits `oidc.json` (0600, atomic) and the change is live on the next request
  (the route pack keys its client on a config fingerprint), where an env var
  needs a restart and puts a client secret in `docker inspect`.
- OIDC works on a plain install; only passkeys need `agentcad[cloud]`.
- No UI yet (slice 8) — a browser needs a way to *discover* that SSO is
  configured, which will want a small public `GET /api/auth/oidc` returning
  `{configured, label}` and a fifth `PUBLIC_PATHS` entry, deliberately left for
  that slice rather than added unused here.
