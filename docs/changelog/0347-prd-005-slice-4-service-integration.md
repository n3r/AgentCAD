# 0347 — PRD-005 slice 4: tenant resolver, RBAC enforcement, qualified keys

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
Slices 1 and 2 shipped the tenancy model, the authz ladder and the sync
server with their seams open. This wires them into the running service:
a request resolves an `(org, workspace)`, the `ProjectStore` roots under
it, and the ladder is enforced at the read guard, the tool registry and
the write guard. FR5/FR6/FR2. Local mode is byte-identical — every
wrapper's first act is to read the tenant and, on `None`, to be the
identity function.

## Changes
- **`core/project.py` — the one seam** (surgical, sibling of
  `write_guard`/`branch_resolver`): `root_resolver: () -> Path | None`,
  consulted by a new private `_root(create=False)` that the *three*
  root compositions (`list_projects`, `create`, `_locate`) now go
  through — so `.cache/`, `exports/`, `imports/`, `.history/` and every
  branch tree inherit tenant rooting without a line of their own. The
  tenant root is made **only** on `create` (a read that made it would
  materialise a directory for a request about to be refused). A
  resolver that raises or answers `None` falls back to `self.root`.
  `_external` (the `open_project` map, which has no tenant in it) is
  invisible while a resolver answers, in `_locate`, `list_projects` and
  `create` alike.
- **`server/security.py` — resolution + the read floor.** After
  `set_client_id`, `resolve_tenant` picks the tenant: **token scope**
  (read defensively off `resolve_token`'s row — slice 5 adds the field;
  a scope is a credential attribute, so it *places* the request without
  a membership check, because an agent has no org default and requiring
  one would make every token unusable) > **`X-Agentcad-Workspace:
  org/ws`** > the session's active workspace (`Principal.workspace`,
  also defensive — slice 8 writes it) > the principal's own memberships
  when they name one, alphabetically first otherwise > `None`. A
  malformed header is a **400**; a selection the principal holds
  nothing on is a **name-free 404** (`"no such workspace"`, details
  empty — a 403 there would confirm the org exists). Then the read
  floor: a route binding `{proj}`/`{project}` needs `view`, matched by
  a regex list compiled once **from the app's own routes** (walking
  `include_context`, because FastAPI leaves `include_router` opaque and
  a naive walk sees 23 of 83) and cached on `app.state`. Literal routes
  are matched first, which is load-bearing: `POST /api/projects/open`
  is not a project called `open`. `/git/…` is excluded — it carries its
  own tenant and its own floor. `guard_websocket` resolves the same way
  and accepts `?workspace=org/ws` (a browser cannot set a header on a
  WebSocket).
- **`core/tenancy_wiring.py` (new) — six wrappers, no core edits.**
  `root_resolver`; `store.lock_key` qualified via `tenancy.qualified`
  (**broader than the design's "the write-guard re-keys the turnlock"**
  on purpose: `lock_key` is the single funnel for turn locks, claims,
  presence, undo stacks, build badges, the search index and navigation,
  so one wrapper is what makes a cross-tenant name collision impossible
  everywhere instead of in one of seven places); `store.create`
  (`edit` at workspace level — the one write `write_guard` cannot see —
  plus `TenancyStore.add_project` after the directory lands); the
  write guard in the `ensure_claim_guard` shape with the authz check
  **before** the wrapped guard (a caller who may never write should not
  be told to wait for a turn), re-installed by wrapping
  `tools_versioning.install_write_guard` (which *replaces* the guard —
  PRD-008's lesson) bound to one service by **weakref**;
  `ToolRegistry.call` (one wrapper covers HTTP, chat and MCP, and
  answers the `{"error": {"type": "permission_error"}}` envelope
  `call` itself would, because a raise is a 500 on the chat and MCP
  surfaces); `EventBus.publish`/`subscribe`; and slice 2's
  `routes_sync.require_role`/`resolve_project`.
- **The WS filter design.** `bus.subscribe` is wrapped to read the
  ContextVar **at subscribe time** — `/ws` subscribes inside the
  connection's own context, right after `guard_websocket` resolved it,
  and a socket has no per-message request to re-read a tenant from. The
  queue the bus created must stay the object the bus holds and
  `unsubscribe` is called with, so the wrapper replaces the queue's
  **bound `put_nowait`** rather than returning a proxy: `publish` calls
  it, identity/`get`/`get_nowait`/`queue.Full` are untouched, and the
  non-dict `_WS_STOP` sentinel is never filtered. Delivery rule: an
  event reaches a subscriber when it carries **no** tenant or exactly
  that subscriber's — so an untenanted subscriber on a hosted instance
  hears no tenant's events.
- **Tool floors.** `READ_ONLY_TOOLS` (curated, `view`), `COMMENT_TOOLS`
  (the review surface — `authz`'s ladder says comment "adds review
  threads and proposals"; `proposal_merge` is `edit`), `ADMIN_TOOLS`
  (`grant_role`, `revoke_role`, the token tools), `NO_FLOOR_TOOLS`
  (`whoami` only — refusing it to a principal who holds nothing would
  be a riddle with the answer inside), and **everything else defaults
  to `edit`**. `TENANT_FORBIDDEN = {"open_project"}` closes the FR19
  gap CLAUDE.md records: the tool registers an absolute path in a
  tenant-free process-global map, and a registry wrapper is the
  unregister seam `core/tools.py` lacks.
- **`cli.py`**: one call, `tenancy_wiring.install(service, registry)` in
  `cmd_serve` after `build_registry` (the one place holding the
  service, the registry and the security config at once), plus its
  import in the same function's import block.

## Files
- `agentcad/core/project.py` — `root_resolver` + `_root()`; three call sites
- `agentcad/server/security.py` — `resolve_tenant`, `parse_workspace`,
  `project_routes`/`project_of`, `SecurityConfig.tenancy()`, two new
  `Principal` fields, the guard's tenancy block and the WS half
- `agentcad/core/tenancy_wiring.py` — new
- `agentcad/cli.py` — two lines in `cmd_serve`
- `tests/test_tenancy_integration.py` — new (43 tests)

## Notes
Enforcement gap, stated rather than discovered: the read floor is
`view` for **every** method on a project route, so a viewer can still
reach the mutating HTTP routes that do not write through `write_guard`
(the comment/thread routes). The tool surface has the right floor for
those (`comment`), and geometry is covered by the write guard; closing
the REST half would need either a per-route table (which
`security.py`'s docstring argues against) or a blanket `comment` floor
on unsafe methods, which would refuse a viewer `POST …/render` and
`POST …/export`. Recorded for slice 10.

Also deferred: PRD-007 share links and publications key on a project
name in the state directory, so two orgs' same-named projects share
that namespace; not reachable cross-tenant through any route wired
here, but not yet qualified either.

`make test` (the full suite, and the count AC9 wants) is the controller's
run at landing; this slice's evidence is the subsets below.

`uv run pytest -q tests/test_tenancy_integration.py tests/test_tenancy.py
tests/test_authz.py tests/test_sync_server.py tests/test_hosted_surface.py
tests/test_hosted_hardening.py tests/test_security_guard.py
tests/test_prd005a_acceptance.py tests/test_service.py tests/test_presence.py
tests/test_locks.py tests/test_claims.py tests/test_project.py` — **431
passed, 1 failed**: `test_ac9_the_full_suite_count_is_cited`, which reads
the *newest* changelog entry and found slice 6's `0348` (written minutes
earlier by a concurrent subagent, count not yet filled in). Not this
slice's, and green with `0347` newest. Regression sweeps: server/project/
branches/claims/disk_budget/route_prefix/prd004/prd008/cli_admin/mcp — 235
passed with one failure in `tests/test_audit.py` (slice 5's in-flight,
untracked file: `OperationalError('database is locked')`); history/search/
navigation — 311 passed; checks/proposals/packet/publications/share — 211
passed. The controller runs the full suite for the count.

`make test` — 6982 passed, 51 skipped recorded (13:12); one real 5-test regression in that run (tenancy_wiring.install on cmd_serve's stub services — AttributeError) was fixed before commit (guard honoring the docstring's 'safe on a service with no tenancy'; 183 passed re-verified incl. the integration suite); the rest were the documented flake families (share_publish/sketch_drag green in isolation) and the pre-existing prd028 AC6 local solver timeout (skips on CI).


Note: this slice's two cmd_serve wiring lines in `cli.py` ride the 0348 commit (three slices shared that file this wave). 