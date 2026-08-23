# 0334 — 2026-08-23 — PRD-027 slice 3: content-addressed thumbnails, the warmer, `thumb.png` routes, `rebuild_finished.cache_key`

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

Per-part and per-assembly 192² iso thumbnails rendered server-side from the
meshes that already exist (FR4/G3), keyed by the build cache key, pre-warmed
by a bus subscriber and rendered on demand — never building. Design §3.
Landed in one commit with slice 2 (0333): both slices edit their own anchored
block of `tools_navigation.py`.

## Changes

- **`agentcad/core/thumbnails.py` (new)** — `thumb_path` (`.cache/<key>.thumb.png`),
  `mesh_for_key` (prefers `<key>.lod1.acm`), `render_part_thumb` (`render_acm`
  at 192², `MAX_TRIANGLES` guard, atomic write; a truncated/empty/just-trimmed
  mesh reads as "no mesh", never a 500), `part_key` (the `_status` key when the
  service remembers the part, else recomputed purely through
  `_cache_key_for(_record_for(...))` — so a fresh process answers a dashboard),
  `part_thumb`, `assembly_key`/`assembly_thumb` (composite over the instances
  at `asm-<sha256>.thumb.png` — dash, not dot, so the janitor keys it on its
  own — with a first-built-part fallback and an empty-composite fallback),
  `has_thumb` (`is_file` checks only; does not create `.cache/`), and
  `ThumbnailWarmer` (daemon thread on `bus.subscribe()`, reacts to
  `rebuild_finished` carrying `cache_key` and ignores `config`-tagged matrix
  builds; queue 256 coalesced by `(proj, key)`, full → drop oldest; one render
  in flight; `drain()` is an exact barrier; `stop()` releases waiters;
  `AGENTCAD_THUMBNAILS=off` = no thread).
- **The kernel-free invariant, the hard way.** Review reproduced a kernel
  request from the assembly route: `service._resolved_instances` is rebound by
  `tools_structure` to `mates.resolve_project`, which issues `resolve_assembly`
  for every polar-pattern and sub-assembly member. `thumbnails._instances`
  therefore walks `store.instances` directly and expands only **linear**
  patterns, purely, through the same `mates._unit`/`_member` the real
  expansion uses (line-for-line the same math); polar patterns, mates and
  sub-assemblies composite at their **stored** base transform (a thumbnail is
  a hint — `render_view` renders the resolved truth), `origin_project`
  members are never looked up in the parent, and any expansion error degrades
  to the base instance (`except Exception` — two review rounds each found a
  class an enumeration missed; behind an `<img>` the only honest failure is
  to draw what is known). Seven route tests install the `tools_structure`
  rebinding first (asserted) and run against a kernel that raises on any
  request.
- **`agentcad/server/routes_thumbnails.py` (new)** — `GET
  /api/projects/{proj}/parts/{part_id}/thumb.png[?k=]` and `GET
  /api/projects/{proj}/thumb.png`: 404 with no mesh (never a build), `ETag:
  "<key>"`, a 304 decided from the key **before** any render or read,
  `Cache-Control: private, max-age=31536000, immutable` only when a
  well-formed `k` names the served key, else `no-cache`. **This is the
  codebase's first non-`no-store` binary response** — safe exactly because the
  immutable answer is keyed by the content hash the client named. The router
  is also what **starts the warmer** (idempotently, honouring the opt-out):
  route packs are mounted only by the HTTP server, so MCP, the CLI,
  `agentcad check`, the package gate and the bench never spawn the thread —
  review had found a warmer constructed per `build_registry` (an orphaned
  thread + bus subscriber each time, and a late render able to re-create a
  deleted `agentcad-check-*` cell).
- **`agentcad/core/tools_navigation.py`** (thumbnails block) — constructs the
  warmer with the `Engine` reuse rule (a second registry on the same service
  gets the same object; a different service gets its own), never starts it.
- **`agentcad/core/service.py`** — `rebuild_finished` carries `cache_key` on
  both the cached and the fresh branch; `get_project` parts carry `thumb_key`
  (the `_status` key when `state == "ok"`, else `null`).
- **`agentcad/core/project.py`** — `.thumb.png` joins `_TRIMMABLE`.
- **`tests/conftest.py`** — an autouse fixture defaults
  `AGENTCAD_THUMBNAILS=off` so no test leaks a daemon thread; the tests that
  want one set it explicitly.

## Files

- `agentcad/core/thumbnails.py`, `agentcad/server/routes_thumbnails.py`, `tests/test_thumbnails.py` (48 tests) — new
- `agentcad/core/tools_navigation.py`, `agentcad/core/service.py`, `agentcad/core/project.py`, `tests/conftest.py` — as above

## Notes

Measured: **3.3 ms** per 192² render (a cube, warm page cache). Deferred
minors for the final review: the assembly route's `k` has no published source
and its fallback ETag is a part key; a serve-existing-`k` fast path; the key is
resolved twice per 200 after the 304 moved forward; the composite re-parses one
`.acm` per member; an unknown material answers 422 from an `<img>`; `drain()`
after `stop()` renders nothing. Mated/polar/sub-assembly instances in a
thumbnail sit at stored transforms — the deliberate price of zero kernel
calls (engine example: 6 of 65 instances are mated).

`make test` — **5698 passed, 50 skipped** (11m18s on this tree with both
slices; the run measured 5688 + the 10 self-referential count-guard tests,
which were red only on a count placeholder in the draft entry — 209 tests in
`test_search.py` + `test_thumbnails.py` re-run green after the last
one-line fix).
