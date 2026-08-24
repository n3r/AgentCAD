# 0344 — PRD-005 slice 1: tenancy model + authz core

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
`core/tenancy.py` (orgs → workspaces → per-project roles in
`<state>/auth/orgs.json`, plus the ambient tenant ContextVar) and
`core/authz.py` (the view<comment<edit<admin ladder, `role_of`,
`require`, `permission_error`). FR5's model + FR6's core, no service
wiring yet (slice 4).

## Changes
- `orgs.json` rides the authstore idioms — and **shares authstore's
  guard by identity** (`_guard_for` import): a private registry on the
  same `<state>/auth/.lock` would be a self-deadlock, not a duplicate
  (flock is per open file description; measured — a second fd blocks
  forever). Tests pin guard identity and bound the deadlock case.
- `PermissionError(AuthzError)` — the wire type is derived from the
  class name (`model.error_type`), the 403 inherited from `_ERROR_STATUS`
  via isinstance: **zero core edits**. The builtin-name collision is
  contained (no filesystem work in authz.py; `PermissionDeniedError`
  alias exported for other modules; `not issubclass(..., OSError)`
  pinned). Note: HTTP bodies spell `"PermissionError"`, the tool surface
  `"permission_error"` — the pre-existing house split; docs quote the
  tool spelling.
- Precedence: org admin (a floor an override cannot lower — a demotion
  the admin can undo in one call would be a rendered fiction) >
  per-project override (raises or lowers) > org default > None; agent
  principals have **no org default** — explicit grants only. Malformed
  rows fail closed to no-role; an unparsable orgs.json raises rather
  than reading as "no orgs". `members` keys on bare handles, per-project
  `roles` on full principals (device suffix stripped).
- `tenant_var`/`current_tenant`/`qualified(proj)`/`tenant_root` — the
  ContextVar seam slice 4 wires into security/store/locks/events.
- Additive shape deviation: optional workspace `label` (readers fall
  back to the id).

## Files
- `agentcad/core/tenancy.py`, `agentcad/core/authz.py` — new
- `tests/test_tenancy.py` (82), `tests/test_authz.py` (54) — new

## Notes
`make test` — 6798 passed, 51 skipped (13:10); non-passing: the pre-existing prd028 AC6 local solver timeout (skips on CI; unchanged since PRD-017's proof it predates these branches) and supervisor/share_frontend load flakes (green in isolation).
