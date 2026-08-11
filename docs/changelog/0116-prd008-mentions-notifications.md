# 0116 — PRD-008 slice 5: mentions, `notifications.jsonl` and AC4

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
`@chat:main` in a comment body now delivers: a `notification` event on the
WebSocket, an unread record `list_notifications` returns to that identity, and
a `read` line when the drawer marks it. **AC4 holds**, end to end, in
`tests/test_comments_notifications.py`. The fifth and last tool FR7 freezes —
`list_notifications` — is registered, taking the surface from **72 to 73**
tools (76 with the `[fem]` extra).

The two design decisions this slice is built on are both about honesty. A
mention must name a **plausible identity** or it is prose: `@todo` and
`@nobody` deliver nothing and stay exactly where the author typed them,
because a drawer full of notifications nobody can act on is worse than no
drawer. And the `notification` event is a **broadcast** — every `/ws` client
receives every one and filters on `to` — which is stated in the tool
description and in `docs/agent-api.md` rather than implied, because it is
honest for a single-user 127.0.0.1-only server with no authentication and
becomes real per-principal delivery with PRD-005, with no payload change.

## Changes
- **`agentcad/core/comments.py` — the scanner.** `parse_mentions(body)`
  (`(?<![\w@])@([A-Za-z0-9_.:-]{1,64})`, ordered, deduplicated) and
  `plausible_mention(handle, present=())`, which accepts `browser`,
  `browser:<nonce>`, `chat`, `chat:<session>` — validated with the chat
  engine's own `SESSION_ID_RE`, imported rather than re-written (lazily: the
  dependency only points `agent` → `core`) — or any id in the presence
  registry's roster. The lookbehind is load-bearing: `nobody@example.com` is an
  address and `@@chat` is prose, so neither delivers.
  - `CommentManager._mentions` retries one handle transformation before giving
    up: the character class swallows sentence punctuation, so `@chat:main.`
    is re-checked as `chat:main`. It can only turn a non-identity into an
    identity, never one identity into another.
  - `_present_ids` is a named optional seam: `service.presence.mention_ids
    (project)` when the presence pack (slice 6) is installed, an empty set
    otherwise, and a registry that raises is caught — presence is ephemeral,
    and it must never stop a comment from being written.
- **`notifications.jsonl` — append-only, one log per project.** New
  `CommentStore.notifications_path` / `append_notification` / `notifications`,
  sharing `audit.jsonl`'s `_read_lines` (corrupt lines skipped, the rest is
  still evidence). Two line kinds: `{seq, kind: "mention", to, project,
  thread, comment, from, ts}` and `{seq, kind: "read", to, ids: [...], ts}`.
  **There is deliberately no file per identity** — an identity is an arbitrary
  string from an unvalidated header, and a filename derived from one is a
  path-traversal surface; one shared log with a `to` field needs no
  sanitizing. **Read is a line, not a mutation**, so unread stays *derived*
  (this identity's mention seqs minus every seq a later `read` line names) and
  nothing in the log is ever rewritten.
- **Delivery from the existing `_publish` seam.** `create`, `reply` and
  `edit_comment` record the plausible mentions on the comment
  (`comments[].mentions`, which has existed since slice 1 and was always
  `[]`), append one `mention` line per recipient, append one `mentioned` audit
  entry (the action `ACTIONS` reserved in slice 1), and hand the events to
  `_publish`, which emits them **after** `comment_changed` — so a client that
  reacts to a notification by reading the thread finds the comment that
  mentions it. Self-mentions deliver nothing; a comment mentioning nobody
  records nothing at all, so the log stays a record of deliveries rather than
  of scans.
  - **An edit re-scans and delivers only the newly mentioned.** Leaving
    `mentions` to describe a body that no longer contains them would make the
    field a lie, and a mistyped handle would otherwise be unfixable; nobody is
    notified twice for one comment.
- **The read API.** `CommentManager.list_notifications(project=None,
  unread=False)` answers for `locks.current_client_id()` only, oldest first,
  each record flagged `read`; omitting `project` spans every project, because
  a drawer is a per-person inbox. `mark_read(proj, ids=None)` appends one
  `read` line (and nothing at all when there is nothing unread, so marking
  twice is a clean no-op). `_read_ids` whitelists the ids: a non-integer, or a
  seq addressed to a *different* identity, is a `validation_error` rather than
  a silent no-op — marking somebody else's notification read is not something
  that can succeed, so it must not look like it did.
- **Surface.** `list_notifications {project?, unread?}` in
  `tools_comments.py`; `GET /api/projects/{proj}/notifications?unread=` (a
  registry passthrough) and `POST /api/projects/{proj}/notifications/read
  {ids?}` in `routes_comments.py`. Marking read is a **route, not a sixth
  tool** (design Decision 11): an agent reading its own mentions needs no read
  cursor, and FR7 freezes the agent surface at five. Both routes answer for
  the identity of the *request* — the `X-Agent-Id` the app middleware bound —
  and never take one as an argument.

## Files
- `agentcad/core/comments.py` — `_MENTION_RE`/`_MENTION_TRAILING`/
  `NOTIFICATION_KINDS`, `parse_mentions`, `plausible_mention`, the three store
  methods, `_mentions`/`_present_ids`/`_deliver`, `list_notifications`/
  `notifications`/`mark_read`/`_for`/`_projects`, `_read_ids`, mentions
  threaded through `create`/`reply`/`edit_comment`, `_publish(notices)`,
  docstring
- `agentcad/core/tools_comments.py` — `list_notifications` (the fifth and
  final FR7 tool) and the module docstring
- `agentcad/server/routes_comments.py` — the two notification endpoints
- `tests/test_comments_notifications.py` (new) — 20 cases in four sections
- `tests/test_comments_api.py` — the registration assertions move from four
  tools to five
- `docs/agent-api.md` — 72 → 73 tools, the `list_notifications` row, a
  "Mentions" paragraph, the two routes, and the `notification` event with the
  broadcast stated out loud

## Notes
- **AC4 is one test:** a browser posts `@chat:main …`, a `/ws` client
  (`extra_allowed_hosts={"testserver"}`) receives `comment_changed` then
  `notification`, `GET …/notifications?unread=true` under
  `X-Agent-Id: chat:main` returns one record, and after `POST …/read` it
  returns `{"notifications": [], "unread": 0}`.
- **Verification:** `uv run pytest tests/test_comments_notifications.py -q` →
  20 passed. `uv run pytest tests/test_comments_api.py tests/test_comments.py
  tests/test_comments_notifications.py -q` → 77 passed.
  `uv run pytest tests/test_mcp.py tests/test_tools.py tests/test_server.py -q`
  → 18 passed (the registry counts 73 tools). `make test-fast` →
  **1033 passed, 1 skipped**; `make test` → **1332 passed, 1 skipped**
  (0114's baseline of 1287 plus slice 4's 25 and this slice's 20).
- The module never builds and never touches git: its service fixture runs on a
  kernel client that raises on any request.
- **Left for slice 6:** `PresenceRegistry` must expose `mention_ids(project)`
  for `@<present client>` to become mentionable — the seam is named, called
  defensively and covered by a test that passes `present={"mcp-bot"}`
  explicitly. **Left for slice 9:** the notifications drawer (the
  `#proposals-btn`/`#proposals-count` unread-badge pattern) and its
  click-to-open-thread.
