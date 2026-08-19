# 0295 — PRD-026 slice 5: the `ui_open` tool pack and `POST /api/ui/events`

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (with Claude)

## Summary

The agent side of the workbench shell (PRD-026, design spec
`docs/superpowers/specs/2026-08-19-workbench-shell-design.md` §7): an agent can
ask the browser to open a named dialog/panel (`ui_open`), and the browser can
report three UX events (`dialog_opened`, `dialog_submitted`,
`palette_executed`) through one member-only route, so an agent can narrate or
react to what a human is doing in the shell. Also the PRD's design spec + slice
plan (committed one step earlier) and `.superpowers/` added to `.gitignore`.

## Changes

- **`agentcad/core/tools_ui.py` (new pack, loads at `ui`, registers
  unconditionally, touches no gate seam).** `ui_open {view, args?}`:
  `view` must match `^[a-z][a-z0-9-]{0,39}$`, `args` must be a JSON object of
  ≤ 4096 bytes; validation runs **before** the rate limiter, so a malformed
  call costs no token. A per-process token bucket (10 opens / 10 s, continuous
  refill, `threading.Lock`, monotonic clock through an injectable module
  global `_now`, `_reset_bucket()` for tests) refuses with
  `validation_error` "ui_open rate limit: 10 per 10 s" + `retry_after_s`.
  Publishes `{"type": "ui_open", "view", "args", "by": "agent"}` on
  `service.bus` and answers `{"ok": true, "view", "args", "delivered_to": n,
  "note"}` — `delivered_to` is the live WS subscriber count, and `0` carries
  the note "no browser is connected; nothing will open" (capability-honest,
  the `project_history` `available: false` precedent). It is a **broadcast**:
  every connected client receives it (the bus has no per-client routing).
- **`EventBus.subscriber_count()`** (`core/service.py`), read under the lock.
- **`agentcad/server/routes_ui.py` (new route pack).** `POST /api/ui/events`
  — strict object body (`routes_configs._json`), `type` ∈ {`dialog_opened`,
  `dialog_submitted`, `palette_executed`}, only `view`/`action`/`tool` string
  keys ≤ 80 chars; anything else is a 422. The server sets `"by": "browser"`
  and `"client": <X-Agent-Id or null>` — a body cannot forge either (an
  attempt is an unexpected key → 422). Member-only by default-deny;
  `PUBLIC_PATHS` untouched.
- **`docs/agent-api.md`**: new "Workbench shell — `ui_open` and UX events"
  section (tool row, the broadcast/honesty/rate-limit notes, the four event
  shapes, the route contract). MCP exposes `ui_open` with no change
  (`mcp_server.py` mirrors `GET /api/tools`).

## Files

- `agentcad/core/tools_ui.py` — new
- `agentcad/server/routes_ui.py` — new
- `agentcad/core/service.py` — `EventBus.subscriber_count()`
- `tests/test_tools_ui.py` (21), `tests/test_routes_ui.py` (23) — new
- `docs/agent-api.md` — the new section
- `.gitignore` — `.superpowers/` (SDD scratch workspace)

## Notes

Reviewed (Sonnet task review, Approved, three deferred minors): the absent-body
422 is reached through the `type` check rather than an explicit guard; the
subscriber count and the publish are two lock acquisitions (a subscribe in the
gap can make `delivered_to` off by one — telemetry, not a contract); a
syntactically invalid JSON body is a 500 like every other body-reading route
in the house (pre-existing, not fixed in one pack). `client` is the raw header:
attribution, never identity.

`make test` — see below (the count is the one measured on the combined slice
1 + slice 5 tree, since the two slices were built concurrently):
**4676 passed, 44 skipped** (11 reported: the 9 `*_count_is_cited` guards read this very entry before its count was filled, `test_checks_pipeline` asserts a clean tree while the slice was uncommitted, and `test_checks_cli`'s 1 ms `--budget` race lost to a machine running two other suites — see 0296).
