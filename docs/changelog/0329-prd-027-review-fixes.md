# 0329 — 2026-08-23 — PRD-027 final review fixes: serialized `set_part_meta`, NaN-safe refusals, claim-aware bulk delete, authoritative server search, and 20 smaller findings

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

The whole-branch review wave for PRD-027: an Opus reviewer over the full
diff, an Opus adversarial verifier with a ten-class attack list, and Codex
(GPT-5.6, xhigh). Twenty-six findings closed in one fix wave; three parked
with rulings (below). The two that mattered most were real data-integrity
bugs the per-slice reviews could not see: the singular `set_part_meta` was
an unserialized manifest read-modify-write (concurrent `set_params`/bulk
writes silently lost — **28 of 250 dropped** in the verifier's probe, 0 after),
and a `NaN`/`Infinity` nested in a tool argument was echoed into a refusal's
`details`, which Starlette's `allow_nan=False` turned into a **500**.

## Changes

- **Serialization** — `set_part_meta` now runs its store call under
  `manifest_scope(store, proj)` → `service._lock` (outer → inner, the
  `BulkExecutor` pattern), publishes outside; `ProjectStore.update_part_meta`
  documents the precondition. `PATCH …/assembly/instances/{id}` takes
  `service._lock` around its read-modify-write (a full-list replace like
  `set_assembly`), and the browser issues instance-folder PATCHes
  sequentially. A two-thread test (`set_part_meta` ×200 vs `set_params` ×50)
  pins zero lost writes.
- **Refusals never 500** — `navigation._safe` renders floats as `repr`, caps
  scalars at 200 chars and containers at 20 entries (+ a remainder count),
  nesting at depth 3; applied at ~30 echo sites in `navigation.py` and
  `search.py`; `json.dumps(result, allow_nan=False)` is asserted on the
  refusal payloads. `bulk_part_op export` refuses a non-finite or ≤ 0
  `tolerance`.
- **Claims** — bulk `delete` preflights `write_guard` per existing id inside
  its `write_scope`; a part another human holds refuses the whole gesture
  (spec ruling 5 now holds for delete too — `remove_parts` saves outside any
  scope, so it did not before).
- **Bulk material rows** — `ok` is about the write (which landed and is
  covered by undo); a failed post-write rebuild is `rebuilt: false` + the
  build error, the results dialog shows "written, rebuild failed", and
  `bulk.js` reads the top-level `applied`.
- **Thumbnails** — the assembly route walks the instances once (was twice);
  `If-None-Match: *` no longer answers 304 for a part with no representation
  (RFC 9110 §13.1.2); nothing is written into `.cache/` while the project is
  over its disk budget (the route serves an in-memory render, the warmer
  counts `skipped_budget`); `stop()` sets `_stop` before dropping `_thread`
  (a `start()` racing a slow `stop()` could leave two renderers), `drain()`
  after `stop()` drains inline, the dead `_active` field is gone; the
  dashboard's `thumb` URL is `quote(name, safe="")`-encoded (a project
  directory name is never validated).
- **Tree / filter (Codex)** — a `kind:` term needs the server
  (`query_model.needsServer`: `get_project` reports the manifest `kind`, so
  `kind:package` is only answerable where provenance is read), and a server
  answer for the *current* query is **authoritative** — it replaces the
  provisional client set instead of being unioned (equivalent for free text,
  correct for `kind`); `serverIds`/snippets are cleared on every query change
  and on a failed search; a truncated answer says "showing the first 500 of
  N — narrow the query" (the cap stays); folder context-menu / drag membership
  comes from `state.project.parts` by folder-path containment, not from the
  visible rows (a collapsed folder had zero members); a project switch clears
  `state.selection`/`selectionAnchor` with the scalars (overlapping ids could
  otherwise drive a bulk action on the new project); stored empty folders are
  pruned on adoption (session-only, as the docs promise); the dashboard's
  New/Open cards close once the project exists; the context menu's focus
  restore uses `{preventScroll: true}`.
- **Docs** — `force` does not override a surviving mate/interface reference
  (`details.referenced_by`); every `filters` key accepts a list; the delete
  copy in the user guide, the bulk-delete confirm and the single-delete note
  say "refused while instances use it — clear them first" (the browser never
  sends `force`); the bulk `material` row semantics. `tests/conftest.py`'s
  thumbnail fixture docstring names the route pack as the starter.
- **Tests** — `test_the_route_is_registered_under_api` uses
  `flatten_routes` (the naive `app.routes` walk rescued by a tautological
  `or`); a `manifest_merge` case pins that a both-sides `tags` edit is a
  whole-list conflict and `folder` merges atomically; ~44 tests added across
  `test_navigation_meta`, `test_tools_navigation`, `test_thumbnails`,
  `test_routes_navigation`, `test_search`, `test_manifest_merge`,
  `test_frontend_navigation`.

## Parked (controller rulings)

- **Publish-after-unlock** (Codex): every mutator in this codebase publishes
  `project_changed` after releasing `service._lock`, and the history hook
  runs `git` synchronously inside `publish` — so a second writer landing
  between a save and its publish merges two gestures into one commit/undo
  step. Pre-existing and shared with `update_part`/`set_params`/
  `delete_part`; a transactional history seam tied to the saved bytes is a
  future PRD, not a fix-wave change.
- **A forced, dependency-aware delete flow in the UI** (Opus + Codex): this
  PR documents the refuse-and-clear behaviour; the second-confirm `force`
  flow is a product follow-up.
- **The "newest changelog cites a count" coupling** (Codex minor): the house
  convention every entry follows.

## Files

- `agentcad/core/{navigation,search,thumbnails,tools_navigation,project}.py`, `agentcad/server/{routes_thumbnails,routes_assembly2}.py`
- `frontend/js/{tree,tree_model,query_model,main,bulk,dashboard,shell/contextmenu}.js`
- `docs/agent-api.md`, `docs/user-guide.md`
- `tests/{conftest,test_navigation_meta,test_tools_navigation,test_thumbnails,test_routes_navigation,test_search,test_manifest_merge,test_frontend_navigation}.py`

## Notes

Verified sound by all three reviewers and left alone: the kernel-free paths
(polar/mate/sub-assembly under a raising kernel), conditional serialization,
thumb-key gating (`k` never selects a file), hosted default-deny on every new
route and tool, the warmer lifecycle, no native dialogs / no `innerHTML` with
server strings, and Python/JS grammar parity over 953 fuzzed queries.

`make test` — **6214 passed, 65 skipped** (10m07s on the fixed tree; the run measured 6202 + the 12 self-referential count-guard tests that were red only on this entry's placeholder; the two nits the re-review handed back — an orphaned JSDoc and a trailing space — landed after the run, covered by `node --check` and `test_frontend_navigation.py` 157 passed). Browser smoke (Chrome via Playwright, scratch copies): 6/6 checks pass — filter, multi-select + context menu, `kind:` queries through the server, dashboard + selection cleared on switch, 1 000-row scroll with no focus tug at 43 rendered rows, no unexplained console errors.
