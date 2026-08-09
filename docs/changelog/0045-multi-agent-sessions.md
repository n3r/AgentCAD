# 0045 — Multi-agent chat sessions

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Concurrent agents on one project (roadmap "Multi-agent sessions"), gated by
the turn locks that landed in 0044: chat history and turn serialization are
now keyed by `(project, session)`, sessions interleave concurrently while
same-session turns still queue, and every session's tool calls carry a
distinct client identity (`chat:<session>`).

## Changes

- **ChatEngine**: `start_turn`/`history`/`clear_history` gain
  `session="main"` (ids `[a-z0-9_-]{1,32}`, validated at one choke point);
  per-(project, session) asyncio locks; all chat_* events (including
  error/limit/finally paths) carry `"session"`; tool identity is `chat` for
  "main" (backward compat with 0044's lock tests) and `chat:<session>`
  otherwise. Image tool-result and executor-identity behavior preserved.
- **Routes**: `POST /api/chat` takes optional `session` (explicit null →
  422); history GET/DELETE take `?session=`; payloads carry `"session"`.
  The WS relay forwards events verbatim, so sessions flow through untouched.
- **Chat dock** stays a single-session UI pinned to "main": foreign-session
  events are filtered (they can no longer unlock the composer or corrupt the
  stream), with a one-line muted notice per active foreign session.

## Files

- `agentcad/agent/chat.py`, `agentcad/server/app.py`, `frontend/js/chat.js`
- `tests/test_chat.py` — five new tests: independent histories + tagged
  events, true cross-session concurrency (rendezvous fake), same-session
  serialization, scoped `chat:reviewer` lock identity, route validation
- `docs/agent-api.md`, `docs/user-guide.md`

## Notes

`_locks` entries for (project, session) pairs are never pruned — same
lifecycle as the old per-project dict, bounded by ids actually used.
