# 0328 — 2026-08-23 — PRD-027 slice 7: acceptance tests (AC1–AC6), docs, the tool count corrected to the measured 107/110

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

The PRD's acceptance criteria as tests, the documentation for everything
slices 1–6 shipped, a trap block for the next reader, and a correction the
acceptance work surfaced: the docs' tool count had drifted from **85** to a
live registry of **107** before PRD-027 added its three. Closes the build
phase of PRD-027 (the branch review, PR and close-out follow).

## Changes

- **`tests/test_prd027_acceptance.py` (new, 11 tests)** — AC1 on a copy of
  `examples/engine` (33 parts / 65 instances): folders `Block/Pistons/
  Fasteners` and the `fastener` tag written through the tools, the store
  re-opened from disk, `search_parts tag:fastener` equal (not ⊆) to the
  tagged set and `folder:Fasteners` likewise, zero kernel calls; AC2's
  machine half (`state:error` finds the one broken part and nothing after the
  fix; the browser half cites 0327's measurements); AC3 (a built part's
  `thumb_key` is key₁ with `.cache/<key₁>.thumb.png` on disk, a *shape*
  change rebuilds to key₂ with its own file — a uniform scale change renders
  byte-identical fit-to-frame pixels, so the key, not the bytes, is the
  identity, and the test says so); AC4 end-to-end through the registry
  (six parts, `bulk_part_op material` → exactly one `project_changed`, one
  `parts_meta_changed`, one history step labelled `project_changed (bulk
  material ×6)`, one `undo` restores all six); AC5's model half in node
  (1 025 rows / 25 folders → three virtual windows ≤ 60 rows, pads summing
  to the full height, bottom clamped); AC6 (the three tools in
  `build_registry` and in `docs/agent-api.md`, `search.GRAMMAR` quoted
  verbatim there, the four routes in `flatten_routes(app)` and none public,
  the user guide naming the dashboard and the `/` shortcut, the newest
  changelog citing a `make test` count); and a **count guard** that parses
  `docs/agent-api.md`'s sentence and compares it to
  `len(build_registry(service).list())` (+3 only when `skfem` imports; a
  hosted instance adds `whoami`), with the mirrors in `docs/architecture.md`
  (×2), `docs/user-guide.md`, `README.md` (×2), `AGENTS.md`,
  `docs/roadmap.md` and `docs/market_research.md` pinned to the same number.
- **Docs** — `docs/agent-api.md`: a Navigation section (`set_part_meta`,
  `search_parts` with the grammar paragraph and the `matched_on`/`snippet`
  shapes, `bulk_part_op` ops/args/row shapes/partial success/`undo_label`,
  the `parts_meta_changed` event, `rebuild_finished.cache_key`,
  `get_project`'s `folder`/`tags`/`thumb_key`, the four routes with the
  thumb cache-header rule; a refusal carries the grammar in
  `details.grammar`). `docs/user-guide.md`: the Sidebar rewritten (folders,
  tags, the filter box and query syntax, selection rules, the context menu's
  six verbs, the bulk bar under the filter box and its results dialog,
  drag-move, thumbnails and state dots; single-part material is the
  inspector's, multi-part is the bulk bar's) and a Dashboard section.
  `docs/architecture.md`: the three core modules, the tool pack and two
  route packs, the thumb cache layout and the `immutable` precedent, the
  frontend additions. `AGENTS.md` + `CLAUDE.md`: a "Navigation (PRD-027)"
  trap block — one publish per bulk = one undo step; `update_parts_meta`'s
  `manifest_scope` → `service._lock` precondition; thumbnails never build
  and never call the rebound `_resolved_instances`; `.thumb.png` in
  `_TRIMMABLE`; the warmer starts only in `routes_thumbnails.build_router`;
  `AGENTCAD_THUMBNAILS=off`; **six** `InstanceSpec(` sites (four carry
  `folder` forward, two mint new instances at root — 0322 said five);
  `kind:package` via `provenance.parse`; `search.GRAMMAR` as the one source;
  the shared parity fixture; the context menu off the overlay stack;
  `import * as virtual`; 107/110. `README.md` feature line.
  `tests/test_prd012_acceptance.py`'s asserted count string moved with the
  docs.
- `agentcad/server/routes_navigation.py` — module docstring corrected
  (grammar in `details`, not the message); no code change.

## Files

- `tests/test_prd027_acceptance.py` — new
- `docs/agent-api.md`, `docs/user-guide.md`, `docs/architecture.md`, `docs/roadmap.md` (prose count only; the PRD row stays in-progress until close-out), `docs/market_research.md`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `tests/test_prd012_acceptance.py`, `agentcad/server/routes_navigation.py` (docstring) — as above

## Notes

**The tool-count drift:** `docs/agent-api.md` said "85 tools (88 with
`[fem]`)" and `tests/test_prd012_acceptance.py` asserted that *sentence*,
not the registry; PRD-013/014/015/017 added tools without moving it, and the
registry was 104 before this PRD and is 107 after (110 with `[fem]`'s
`fem_static`/`fem_modal`/`fem_thermal`). The new guard compares the
sentence to the live registry so the next pack moves the docs or goes red.
Review (Opus) caught five doc-vs-code drifts in the first draft (grammar in
the message; a Material context-menu row that does not exist; the bulk
bar's position; `asm-` as a key rather than a file prefix; `undo_label`/
`parts_meta_changed` edge cases) — all corrected and re-checked by grep.

`make test` — **6171 passed, 65 skipped** (11m11s on the finished branch with `origin/main` at `f62c064` merged in; the run measured 6159 + the 12 self-referential count-guard tests that were red only on this entry's placeholder).
