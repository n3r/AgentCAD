# 0044 — Multi-user turn-locking at the store choke point

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Per-project advisory-but-enforced editing turns (roadmap "Multi-user /
collaboration", the turn-locking half): an agent acquires the turn and every
other client's writes fail with a conflict error naming the holder until
release or TTL expiry. No lock held → byte-identical behavior to before.

## Changes

- **`agentcad/core/locks.py`** (new): `client_id_var` ContextVar +
  `current_client_id()`/`set_client_id()`; thread-safe `TurnLock` (acquire
  with refresh-by-holder and TTL clamp 5–3600 s, release, get, check;
  wall-clock `expires_at`; expiry means free).
- **Enforcement seam**: `ProjectStore.write_guard` — called before
  `save_manifest` (which every pack mutation funnels through) and
  `write_script`. The service installs `turnlock.check(proj,
  current_client_id())`, covering mates/materials/PMI/solids mutations with
  zero per-pack edits. Project creation and derived-data writes stay
  unguarded.
- **Identity threading**: HTTP middleware stamps the ContextVar from
  `X-Agent-Id` (default `browser`; Starlette's threadpool copies context into
  sync routes — verified by test); the chat engine sets `chat` inside the
  executor on every call (empirically load-bearing: executor threads REUSE
  contexts across work items, they do not get fresh ones); the MCP proxy
  sends `AGENTCAD_AGENT_ID` or `mcp` on every proxied call.
- **Tools** `acquire_turn` / `release_turn` / `get_turn` (`tools_locks.py`
  pack) with etiquette in the descriptions; `lock_changed` events on the bus.
- **UI**: toolbar lock chip (`🔒 <holder>`) driven by `lock_changed`,
  hidden for browser/none, reset on project switch.
- Docs: agent-api rows, user-guide section, architecture fourth-seam note.

## Files

- `agentcad/core/locks.py`, `agentcad/core/tools_locks.py`
- `agentcad/core/project.py`, `agentcad/core/service.py`
- `agentcad/server/app.py`, `agentcad/agent/chat.py`,
  `agentcad/agent/mcp_server.py`
- `frontend/js/main.js`
- `tests/test_locks.py` — 8 scenarios incl. both conflict shapes (raw REST
  409 vs tool-passthrough 200 error payload), pack-mutation coverage via the
  store choke, TTL expiry steal, chat identity via FakeAnthropic
- `docs/agent-api.md`, `docs/user-guide.md`, `docs/architecture.md`

## Notes

Known quirk: `create_part` under someone else's lock writes the script file
before the manifest write raises, leaving a harmless orphan `.py` that a
retry overwrites — fixing it means reordering `add_part`; deferred.
