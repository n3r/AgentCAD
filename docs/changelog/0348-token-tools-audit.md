# 0348 — PRD-005 slice 5: scoped agent tokens as tools + the audit log

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
FR3 and FR12: bearer tokens gain an optional `{org, projects, role}` scope
honored end-to-end, the `tools_cloud` hosted-only pack promotes token/role
management to the agent surface, and every auth event + mutating cloud
tool lands in a per-org SQLite-WAL audit log.

## Changes
- `authstore.py` (additive): `add_token(scope=)`, scope accessors;
  an **unscoped token is byte-identical to 005a** (scope written only
  when present; `resolve_token`'s shape pinned by the existing equality
  tests).
- `core/audit.py`: per-org `<state>/audit/<org>.db` (WAL,
  `synchronous=NORMAL`, `busy_timeout=30000`, spike-derived indexes);
  one connection per (thread, org) with `check_same_thread` kept as a
  guard; **the WAL pragma can return SQLITE_BUSY ignoring the busy
  handler** — setup is lock-serialized, retried, read back, and degrades
  rather than refusing to start (raced once in a fresh-DB batch before
  the fix); two-thread + real-second-process appends proven
  (`integrity_check: ok`); `args_digest` strips secret-named keys
  (tested); `vacuum_into` backup (a raw `cp` of a WAL db loses rows —
  the spike's proof is a regression test); retention pruning;
  `tap_registry(call, log, ...)` built + tested, wiring left to the
  integration layer (no tenant → no row → local mode free).
- `tools_cloud.py` (hosted-only registration): `create_agent_token`
  (admin floor; secret once; refuses a second live token of one name —
  both compose to `agent:<name>` and would union reach),
  `revoke_agent_token`, `grant_role`/`revoke_role` (org-admin floor),
  `list_members`, `sync_status` (slice-6 stub); `whoami` is **wrapped in
  place** (`_WRAPPED`) — the registry refuses duplicate names — and
  grows org/workspace/roles/scope keys **only when an org exists** (no
  tenancy → byte-for-byte 005a's four keys). A named `org`/`workspace`
  argument that disagrees with the request tenant is refused, never an
  override. Derived property, tested: a bearer token cannot mint, grant,
  or revoke — agents have no org default in `role_of`.
- `routes_auth.py`: audit taps (login outcomes, logout, enrol,
  user/token/oidc/passkey events — anonymous ceremony *begins*
  deliberately unrecorded: a row per anonymous attempt is external DB
  growth) + admin-only `GET /api/auth/audit`.
- `cli.py`: `agentcad admin audit query|backup` (house `verb:action`
  style).

## Files
- `agentcad/core/audit.py`, `agentcad/core/tools_cloud.py` — new
- `agentcad/core/authstore.py`, `agentcad/server/routes_auth.py`,
  `agentcad/cli.py` — extended
- `tests/test_audit.py` (62), `tests/test_tools_cloud.py` (53) — new

## Notes
AC5/AC6 machine halves land here (scoped-token 403 + next-request
revocation; user/chat/bearer distinguished on one project).
`make test` — 6982 passed, 51 skipped recorded (13:12); one real 5-test regression in that run (tenancy_wiring.install on cmd_serve's stub services — AttributeError) was fixed before commit (guard honoring the docstring's 'safe on a service with no tenancy'; 183 passed re-verified incl. the integration suite); the rest were the documented flake families (share_publish/sketch_drag green in isolation) and the pre-existing prd028 AC6 local solver timeout (skips on CI). Its `admin audit` CLI block also rides the 0347 commit's cli.py.
