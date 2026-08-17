# 0192 — PRD-005a slice 4: agent bearer tokens, `whoami`, remote MCP

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

An agent can now hold a revocable credential and drive a hosted instance from
outside it. Adds the `whoami` tool (hosted mode only), the admin-only token
routes, `agentcad admin token add|list|revoke`, and the two MCP-proxy changes
that make `AGENTCAD_URL` + `AGENTCAD_TOKEN` a real remote configuration —
including a refusal to auto-spawn a *local* server because a *remote* one is
unreachable.

## Changes

- **`agentcad/core/tools_auth.py` (new).** `whoami` → `{principal, kind, role,
  mode}`, registered only when this process is serving a hosted app — the
  FEM-tools precedent ("register a tool only if it can run"). `principal` is
  the **composed** identity (`user:nikita/browser:7f3a1b2c`,`agent:ci`), the
  same string `locks.current_client_id()` carries into claims, the roster and
  history trailers; a bare handle would be a second spelling of identity.
- **`agentcad/server/routes_auth.py`** — `GET /api/auth/tokens`,
  `POST /api/auth/tokens` (201), `DELETE /api/auth/tokens/{id}`. All three
  require a signed-in **admin person**, matching slice 3's tightening: an
  `admin`-role *token* is `403`, so a credential cannot mint another
  credential (design Decision 14, no audit log yet). The plaintext secret is
  in the create response and nowhere else — `list_tokens` cannot return it,
  because only a SHA-256 digest is stored.
- **`agentcad/cli.py`** — `agentcad admin token add|list|revoke`, same shape as
  `admin user`: direct `AuthStore`, no service, no kernel, no port. `add`
  prints the secret once with the "only time it is shown" line; `list` prints
  id/name/role/state (`active`/`expired`/`revoked`) and never a digest;
  `revoke` marks rather than deletes so the record survives.
- **`agentcad/cli.py` — `security_module.install(security)` in `cmd_serve`.**
  See Notes: without it `whoami` would have existed in every test and in no
  real server.
- **`agentcad/agent/mcp_server.py`** — `_client_headers()` (adds
  `Authorization: Bearer` when `AGENTCAD_TOKEN` is set; a blank value is
  treated as absent) and `_may_autostart()` (loopback-only, decided on the
  parsed **host**, reusing `appmode.LOOPBACK_HOSTS`). `_ensure_server` refuses
  to spawn for a non-loopback base and says so on stderr.

## Files

- `agentcad/core/tools_auth.py` — new
- `agentcad/server/routes_auth.py` — the three token routes
- `agentcad/cli.py` — `admin token` group, `import time`, the `install()` call
- `agentcad/agent/mcp_server.py` — `_client_headers`, `_may_autostart`, the
  spawn gate, and `_serve` now builds its client from `_client_headers()`
- `tests/conftest.py` — the `hosted` fixture installs the config **before**
  `build_registry`
- `tests/test_tokens.py`, `tests/test_mcp_remote.py` — new
- `tests/test_cli_admin.py` — nine token tests appended

## Notes

- **Plan gap found and closed: registration order.** The plan says `whoami`
  registers "only when `security.current_config()` is not None", but
  `build_registry(service)` is evaluated in the *caller*, before `create_app`
  calls `security.install()`. As written, the config is never installed in time
  and `whoami` registers nowhere. Both callers now install first —
  `cli.cmd_serve` explicitly, and the `hosted` fixture in `tests/conftest.py`
  — each with a comment saying why. This is the same class of gap slice 3
  found with `_security_config()`: the feature passing its own tests while
  being unreachable in the running product.
- **The layering, stated.** `core/tools_auth.py` is the one `core/` module that
  reads from `server/`, because identity is app-layer state by design
  (Decision 10) and this pack is its agent-facing projection. It is kept honest
  by skipping entirely when `agentcad.server.security` is not in `sys.modules`
  — exactly the headless case (`agentcad check`, the publish gate), which then
  pays nothing, not even the FastAPI import.
- **`_may_autostart` is a host comparison, not a prefix one**, and
  `http://127.0.0.1.evil.example` is a parametrised negative for it. It reuses
  `appmode.LOOPBACK_HOSTS` rather than re-spelling the set — two copies that
  drifted would let one say yes while the other said no.
- **Rate limits and lockouts, both directions.** The token suite pins that a
  revoked token fails on the *next* call, that an expired one stops
  authenticating (clock moved via `authstore._now`), that a forged bearer never
  falls back to a cookie riding the same request, and — the legitimate-traffic
  half — that a live token is `200`, is `Origin`-exempt on a write, and carries
  the role it was minted with.
- `tests/test_mcp_remote.py` is marked `portability`: it touches no kernel and
  the autostart decision is exactly the kind of platform-shaped behaviour that
  job exists for. `test_ensure_server_refuses_to_spawn_for_a_remote_url`
  monkeypatches `subprocess.Popen` to raise, so a regression fails loudly
  instead of leaving a stray server behind.
- Verification: `.venv/bin/python -m pytest tests/test_tokens.py
  tests/test_mcp_remote.py tests/test_cli_admin.py -q` → **55 passed**; with
  the slices 1–3 files (`test_auth_routes`, `test_security_guard`,
  `test_hosted_surface`, `test_packages_cli`, `test_appmode`, `test_authstore`,
  `test_actor_kind`) → **279 passed**. The prior tree's full-suite measurement
  is 0190's `make test` → **3532 passed, 1 skipped in 550.95 s**; the count
  after slices 4 and 5 is **3597 passed, 1 skipped in 515.69 s** (8 workers,
  this machine) — exactly baseline + 41 + 24, no regressions. Slice 6's count
  is in 0194.
- **Verified against a real hosted `agentcad serve`** (port 8643, scratch
  config and projects dir): `admin token add ci --ttl-days 30` printed the
  secret once; `POST /api/tools/whoami` with that bearer answered
  `{"principal":"agent:ci","kind":"agent","role":"member","mode":"hosted"}` and
  with the browser cookie plus `X-Agent-Id: browser:7f3a1b2c` answered
  `{"principal":"user:nikita/browser:7f3a1b2c",...}`; `admin token revoke` then
  turned the same bearer's `GET /api/projects` from `200` into `401`.
