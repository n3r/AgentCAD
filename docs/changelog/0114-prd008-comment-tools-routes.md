# 0114 — PRD-008 slice 3: the comment tool pack, the route pack and `comment_changed`

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
Slices 1 and 2 built the review-thread document and its read-time anchor
resolution as library code that nothing called. This slice makes it reachable:
a tool pack (`agentcad/core/tools_comments.py`) installing `service.comments`
and four of FR7's five frozen tools, a route pack
(`agentcad/server/routes_comments.py`) with eight endpoints, and the
`comment_changed` event published from `CommentManager` on every mutation —
and on no no-op. **AC9 now holds at the API layer** as well as in the library:
an attachment outside `exports/` is a 422.

The tool descriptions carry the honest anchor contract rather than a summary of
it: the four resolution states with `unverified` spelled out as *we did not
look*, the measured face-match odds from changelog 0113's R1 spike (about two
in three, zero mis-pins), the instruction to address a face through
`resolution.face_index` and never the stored ordinal, and the statement that
`actor_kind` is bookkeeping, not authentication.

## Changes
- **New tool pack `agentcad/core/tools_comments.py`.** `register()` installs
  `service.comments = CommentManager(service)` and registers `list_comments`,
  `add_comment`, `resolve_thread` and `reopen_thread`. `list_notifications` is
  slice 5; the registry moves **68 → 72** (71 → 75 with the `[fem]` extra).
  - `add_comment` takes **exactly one** of `anchor` (opens a thread) or
    `thread` (replies to one); both or neither is a `validation_error` whose
    `details.required` names the rule. Every mutating tool returns the
    post-state thread under a `thread` key.
  - `list_comments` maps `resolve_anchors: null` to **true**, not false: the
    registry's convention is that `null` on an optional argument means
    "omitted", and a client sending a uniform payload of nulls must not get a
    silently resolution-free list.
  - The pack **does not self-disable without git** (unlike `tools_proposals`
    and `tools_versioning`) — a comment is not a commit, and the two features
    that do need git degrade by answering `unverified` and saying why.
  - It adds **no gate provider** and assigns nothing a later pack assigns:
    threads inform, verdicts decide (design Decision 20).
- **New route pack `agentcad/server/routes_comments.py`** — eight endpoints:
  `GET|POST /api/projects/{proj}/comments`, `GET …/comments/{id}`,
  `POST …/comments/{id}/resolve`, `POST …/comments/{id}/reopen`,
  `PATCH|DELETE …/comments/{id}/comments/{cid}`, `GET …/comments/{id}/audit`.
  The first four verbs are registry passthroughs (one implementation serves
  agents and the browser); the last four call `service.comments` directly
  because FR7 freezes the agent surface at five tools and reading one thread,
  editing your own comment and reading an audit log are panel affordances.
  Body keys are whitelisted per route — never `**body`, which is also what
  stops a client posting `state`/`author`/`id` into a thread document.
  `_BODY_ERRORS` is empty: an `orphaned` or `unverified` anchor is payload, so
  the only HTTP errors are 404/422 for "no such thing" and "that is invalid".
- **`comment_changed` in `agentcad/core/comments.py`.** A new
  `CommentManager._publish` called from the single return point of each of the
  five mutators, publishing `{type, project, thread, state, action, part}` with
  `action` ∈ `created | replied | resolved | reopened | comment_edited |
  comment_deleted`. The two idempotent early returns (`resolve` on a resolved
  thread, `delete` on a tombstone) return *before* it, so a no-op publishes
  nothing. It is deliberately not `project_changed`: that event's `on_publish`
  hook snapshots history, and a comment is not a model change. `bus.on_publish`
  is not touched.
- **`docs/agent-api.md`**: the header count, a four-status bullet in the
  conventions list, and a new "Review threads" section (the four tools with
  their payloads, the six anchor shapes, the immutable-anchor rule with the
  measured 2-in-3 face odds, the "listing never builds" cost rule, the
  `exports/` attachment rule, the identity paragraph, the routes and the
  event).

## Files
- `agentcad/core/tools_comments.py` — new (261 lines; the pack's own docstring
  carries the load-order argument, and three shared description constants carry
  the resolution contract, the measured odds and the identity caveat)
- `agentcad/server/routes_comments.py` — new (147 lines)
- `agentcad/core/comments.py` — `_publish` added, the five mutators' return
  points wired through it, module docstring's events paragraph
- `tests/test_comments_api.py` — new, 27 cases in five sections: registration
  and load order, the tools, `comment_changed`, the routes, the MCP passthrough
- `docs/agent-api.md` — tool count, conventions bullet, "Review threads"
- `docs/architecture.md` — `comments` added to the tool-pack and route-pack
  lists (the module's own architecture paragraph belongs to slice 11)

## Verification
```
uv run pytest tests/test_comments_api.py -q            -> 27 passed in 16.56s
uv run pytest tests/test_comments.py tests/test_anchors.py tests/test_tools.py \
              tests/test_mcp.py tests/test_checks_api.py tests/test_server.py -q
                                                       -> 113 passed in 42.29s
make test-fast                                         -> 989 passed, 1 skipped in 226.90s
make test                                              -> 1287 passed, 1 skipped in 1432.40s (0:23:52)
```
Baseline was 1260 passed, 1 skipped (changelog 0113); +27 is exactly this
slice's new module, so nothing existing was lost. **No pre-existing test file
was edited** — no suite asserts an exact tool count (`tests/test_tools.py`
asserts `>= 25`), so the four new tools needed no test change.

Live, against a real server (`uv run agentcad serve --port 8637`):
`GET /api/tools` returns 72 including the four; `POST …/comments` with a
`face` anchor stores the mesh-derived signature and returns the thread;
`?resolve_anchors=false` returns `{open: 1, resolved: 0}` and no `resolution`
block; `POST …/1/resolve` returns the post-state with its actor; the audit
route returns `["created", "resolved"]`; an unknown project is a 404.

## Notes
- **Load order, verified empirically rather than assumed.**
  `pkgutil.iter_modules(agentcad.core)` now hands the packs over as
  `[tools_analysis, tools_comments, tools_drawing, …]` — `tools_comments` is
  **index 1**, before `tools_proposals` (`p`), `tools_run_checks` (`r`) and
  `tools_versioning` (`v`). So `service.proposals`, `service.branches`,
  `service.merges` and `service.gate_providers` do **not** exist when
  `register()` runs; `CommentManager` already read them lazily (slice 1), and
  the pack captures none of them. Two tests pin it: the ordinal one against the
  real package, and one that calls `register()` on a bare service with those
  four seams asserted absent. This is the `tools_run_checks` trap from PRD-004
  in its other direction — that pack had to be *renamed* to sort after `p`
  because it appends a gate provider; this one may sit at `c` precisely because
  it appends nothing.
- **The tool count in `docs/agent-api.md` was stale by three.** It read "65
  tools (68 with the `[fem]` extra)"; the measured registry before this slice
  was **68** without the extra (`sfepy` absent here, so the three FEM tools do
  not register). The header now reads 72/75, measured with
  `len(build_registry(service).list())` and confirmed against a live
  `GET /api/tools`.
- **Deviation from the parent task's tool list.** The task named
  create/reply/resolve/reopen/edit/delete/get/list/audit as tools. FR7 and
  design Decision 17 freeze the agent surface at *five* tool names, so edit,
  delete, get and audit are **routes** instead, and create/reply are the two
  halves of `add_comment`. That is also what makes the plan's "eight endpoints"
  and its "four thread tools" add up.
- **Deviation from the parent task's event shape.** The task described
  `{project, id, state, reason, …}` (the `proposal_changed` shape); the plan
  (Task 5) and design Decision 12 both say `{project, thread, state, action,
  part}`, and Decision 1 freezes the word `thread` in this payload. The plan's
  shape shipped.
- **`slow` markers:** none. Every case here is a surface assertion over one
  small box part; the module's slowest run is the WebSocket round-trip. The
  one git-dependent case (a mutation creates no history commit) carries
  `integration` + `portability` + a `skipif`, matching `tests/test_comments.py`.
- Slice 4 inherits a `proposal_hunk` kind that is registered in the vocabulary,
  described in `add_comment`'s text and still refused by the public path with
  `"not supported yet"`; when its validator lands, the tool, the route and the
  event need no change. Slice 5 adds `list_notifications` to this pack and two
  notification endpoints to this router (the count then moves 72 → 73), and
  will publish `notification` alongside `comment_changed` from the same
  manager. Slice 8's UI reads `comment_changed` and re-renders from
  `list_comments`, never from the event payload, which is deliberately a
  pointer and carries no body.
