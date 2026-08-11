# 0117 — PRD-008 slice 6: presence, the HTTP heartbeat and per-browser identity

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
Two browsers on one project can now see each other. `agentcad/core/presence.py`
holds an in-memory, TTL'd `PresenceRegistry`; `agentcad/server/routes_presence.py`
feeds it from an HTTP heartbeat and answers with the whole roster; and
`frontend/js/api.js` finally gives each browser profile an identity of its own —
`browser:<8 hex>`, minted once and kept in `localStorage`.

That last change is the first **behavioral** one in PRD-008. Until now every
browser tab in the world was literally the identity `browser`, which made AC1
("browser A" vs "browser B") untestable and would have made slice 7's soft
claims inert by construction. It also means an existing user's next request
arrives under a client id the project has never seen — verified below (risk R6).

The transport is a deliberate divergence from FR9's wording, per design
Decision 13: presence arrives over **HTTP, not the WebSocket**. `/ws` lives in
`server/app.py`, a core the extension-point contract forbids editing for a
feature; it carries no client identity, because `set_client_id` is HTTP
middleware only; and its origin/Host guard is HTTP middleware too — a route
pack cannot see `create_app`'s `allowed_hosts`, so it could not reproduce that
guard under test. Over HTTP presence inherits the reviewed guard, the identity
plumbing, the error mapping and the rate limiting for free, and opens no new
inbound channel.

## Changes
- **`agentcad/core/presence.py` (new).** `PresenceRegistry`, keyed
  `(store.lock_key(proj), client_id)` — the same branch-aware key turn locks
  and undo stacks use, so two clients on two branches do not appear to be
  looking at the same thing.
  - `touch()` returns `(entry, changed)`. `changed` is the point: it is true
    only when the roster *others can see* differs (a join, a focus or label
    change, a pruned straggler), so `presence_changed` does not fire for the
    idle 15-second heartbeat. Five idle clients would otherwise be 20 events a
    minute.
  - `leave()`, `roster()` (oldest arrival first), `payload()` and `publish()`.
  - `mention_ids(project)` — the one method `CommentManager._present_ids` calls
    (slice 5 wrote it defensively against exactly this seam). It deliberately
    spans lock keys: a mention of a teammate is about the project, not the
    branch.
  - `claims(key)` reads `service.claims` lazily and returns `{}` when the
    claims pack is absent, so slice 7 drops in without touching this file.
  - **No reaper thread.** Entries carry a wall-clock `expires` and are dropped
    by the read that notices them (`_prune`, under the lock, in every
    operation). `test_expiry_is_lazy_and_starts_no_thread` asserts both halves:
    `threading.active_count()` is unchanged, and the entry survives in the dict
    until something reads it.
  - **Never persisted** (FR9): no store access anywhere in the module. A
    restart empties it, which is the honest answer to "who is here *now*".
  - `TokenBucket` — per-identity, 1/s with a burst of 5, injected clock.
  - `kind` is *derived* from the identity with `proposals.actor_kind` and never
    taken from the client; `label` is display data — capped at 40 printable
    characters, defaulted to the identity, never written into a thread, an
    audit line or a lock.
  - `ensure_presence(service)` installs `service.presence` idempotently.
- **`agentcad/server/routes_presence.py` (new).** `POST` and `GET`
  `/api/projects/{proj}/presence` → `{you, clients, claims, ttl_s,
  heartbeat_s}`. `GET` registers nobody. An unknown project is a 404 from
  `store.canonical_path_of` before any roster work; an unknown `surface` is a
  422 naming the four known ones (`viewport`, `editor`, `inspector`,
  `proposals` — FR9's set plus where reviewers actually are).
  - **An over-rate heartbeat is HTTP 200 with `throttled: true`**, never a 429:
    a heartbeat that surfaced as a red toast would teach users to distrust a
    status indicator. The response still carries the full roster, because the
    response *is* the mechanism — a client that misses every `presence_changed`
    converges within one beat.
  - The pack calls `ensure_presence(service)` at `build_router` time. Presence
    gets **no tools**: an agent's presence is its writes.
  - One documented exception to "identity is never an argument": a `pagehide`
    beacon may name `client_id` in its body, because `navigator.sendBeacon`
    cannot set headers. It grants nobody anything — the header it stands in for
    is itself unvalidated, and the worst a forged leave does is drop a roster
    row that the victim's next heartbeat restores (and the TTL would have
    dropped within 45 s anyway).
- **`frontend/js/api.js` — the identity.** `clientId` is minted from
  `crypto.getRandomValues` as `browser:<8 hex>`, validated against
  `/^browser:[0-9a-f]{8}$/` on read, and persisted in
  `localStorage["agentcad.client_id"]` (both accesses wrapped in `try`, so
  private mode degrades to a per-page identity rather than a crash).
  `localStorage`, not `sessionStorage`, on purpose: two *tabs* of one profile
  stay one client — which is what keeps the per-client branch checkout behaving
  as it does today — while two browsers, or a normal and an incognito window,
  are two clients. The header goes on `request()` **and on all four hand-rolled
  `fetch` helpers** (`getMesh`, `getMeshFaces`, `getDiffMesh`, `uploadImport`),
  which bypass it; `tests/test_presence.py` pins the count at five so a fifth
  hand-rolled fetch cannot quietly speak under a different identity. New
  `api.heartbeat()` / `api.presence()`.
- **`frontend/js/presence.js` (new).** A 15 s heartbeat, plus an immediate
  (coalesced to ≥1.2 s, inside the server's bucket) beat on a project, part,
  branch or mode change, on tab re-focus, and on a surface change tracked by
  one capturing `pointerdown` listener (`#proposals-modal` → `proposals`,
  `#pane-code` → `editor`, `#inspector` → `inspector`, `#viewport` →
  `viewport`). `pagehide` sends `{leave, client_id}` via `navigator.sendBeacon`
  (falling back to `fetch(..., {keepalive: true})`, which *can* carry the
  header). The roster lands in `state.presence`; the avatar strip is created
  lazily before `#conn-dot` with `renderLockIndicator`'s exact pattern — no
  `index.html` and no CSS churn — coloured from `tree.js`'s exported
  `INSTANCE_PALETTE` by an id hash, square-ish for agents, ringed for you, and
  hidden until somebody else is here. The tooltip carries the **label**, never
  the raw nonce. `setClaiming(on)` is exported for slice 9's editor wiring.
- **`frontend/js/main.js`** — the import, `presence.init()` in `boot()`, and one
  `case "presence_changed":` in `handleEvent`, project-guarded like every other
  case and commented as an optimization rather than the mechanism.
- **`frontend/js/state.js`** — one documented key, `presence`.
- **`tests/test_presence.py` (new, 21 tests).** Four sections: the registry
  (join/leave/TTL with an injected clock, lazy expiry + no thread, per-lock-key
  isolation, derived `kind`, capped `label`, the `changed` flag, `mention_ids`,
  the bucket), the routes (POST registers / GET does not, leave, throttling,
  422/404, nothing persisted), the event (fires on join, focus and leave but
  **not** on a no-op beat; a comment mutation still publishes only
  `comment_changed`), and the seams (R6, a present id becoming a plausible
  mention and ceasing to be one on leave, idempotent installation, and the
  served `api.js`/`presence.js`/`main.js` assertions in `test_server.py`'s
  style).

## Files
- `agentcad/core/presence.py` — new: `PresenceRegistry`, `TokenBucket`,
  `ensure_presence`
- `agentcad/server/routes_presence.py` — new: the heartbeat route pack
- `frontend/js/api.js` — the minted `clientId` and the `X-Agent-Id` header on
  all five request paths; `heartbeat`/`presence`
- `frontend/js/presence.js` — new: the heartbeat loop and the avatar strip
- `frontend/js/main.js` — import, `presence.init()`, `presence_changed`
- `frontend/js/state.js` — the `presence` key
- `tests/test_presence.py` — new

## Notes
- **Risk R6 verified.** `test_a_freshly_minted_browser_identity_lands_on_the_
  default_branch` drives `GET /branches` and a `PUT …/parts/box` under a
  never-before-seen `browser:0badc0de`: `BranchManager.current` falls back to
  `default_branch(proj)` when `checkouts.json` has no row for a client, so the
  first run is a clean default-branch checkout and the write lands there. The
  visible consequence for an existing user is cosmetic and was accepted in the
  plan's rollback notes: the turn-lock badge in `main.js` hides a holder called
  exactly `"browser"`, and a holder called `browser:7f3a1b2c` is no longer that
  string. The browser UI never takes a turn lock itself, so this only shows if
  a user drives `acquire_turn` through the tool passthrough. Not fixed here —
  it is a one-line comparison against `state.clientId` that belongs with slice
  9's claim/lock UX.
- **Verification.** `uv run pytest tests/test_presence.py -q` → **21 passed**.
  `uv run pytest tests/test_server.py tests/test_comments_api.py
  tests/test_comments_notifications.py tests/test_locks.py tests/test_mcp.py -q`
  → **70 passed** (the new route pack changes no tool count: presence has no
  tools). `node --check` on all four touched/added JS files. The full-suite
  citation for slices 6 and 7 together is in changelog 0118: **1371 passed,
  1 skipped**.
- **Verified against a real server** as well as `TestClient`: two identities
  heartbeating `agentcad serve` see each other in the roster, with focus and
  labels, and the payload carries `ttl_s: 45.0` / `heartbeat_s: 15.0`.
- **Not verified visually.** The two-browser screenshot pass the plan asks for
  belongs with slices 8–9, which give presence its real UI (tree chips, the
  claim chip, the notifications drawer); the strip added here is the minimum
  that makes the heartbeat observable.
- `presence_changed` carries `claims: {}` until slice 7 installs
  `service.claims`; the payload shape does not change when it does.
