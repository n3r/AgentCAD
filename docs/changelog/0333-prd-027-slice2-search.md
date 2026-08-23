# 0333 — 2026-08-23 — PRD-027 slice 2: search engine, the `field:value` query language, `search_parts`, `GET …/search`

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

Server-side part search (FR3/G2): a small query grammar, a pure matcher and
ranker the frontend can port byte-for-byte, an on-demand scanning engine with
a stat-validated memo (no inverted index, no bus plumbing), the
`search_parts` tool and `GET /api/projects/{p}/search`. Design §2.

## Changes

- **`agentcad/core/search.py` (new)** — three layers:
  - *Grammar* (`parse`, `Term`, `Query`, `FIELDS = (tag, material, state, kind,
    folder, id, label)`, `STATES`, `KINDS`): whitespace terms, `field:value`,
    leading `-` negates, double quotes group a phrase (`"m5 boss"`,
    `folder:"left side"`), everything else is free text. Refused with a
    `validation_error` carrying the grammar: an unknown field, an unknown
    `state`/`kind` value, an unterminated quote, an **empty field value**
    (including `folder:/`, which `folder_matches` would have widened to
    everything). Only a non-identifier head (`1:2`) stays free text; quoting is
    the escape hatch for a literal `http:`. `GRAMMAR` is one constant quoted
    into the tool description and every refusal's `details`.
  - *Matcher/ranker* (`matches`, `rank`, `script_only`, `snippet`): `tag`
    exact (normalized), `material` exact id, `state ∈ ok|error|unbuilt` from
    `_status`, `kind ∈ script|reference|package` (package ⇔
    `provenance.parse(script) is not None` — a `MARKER in script` gate), `folder`
    through `navigation.folder_matches`, `id`/`label` substring, free text over
    id/label/tags/material **and script text**; `matched_on` in a canonical
    order; ranking id/label › tag › material › folder/state/kind › script with
    manifest order breaking ties. A `snippet` (≤ 120 chars) is attached when the
    script is the only **content** source — a `state:` or `folder:` term beside
    a text hit does not suppress it (review ruling).
  - *Engine* (`Engine.rows/script_text/search`): rows memoized per `lock_key`
    on the manifest's `(mtime_ns, size)` (capped at 256 entries); script text
    (raw + lowered) memoized per path on `(mtime_ns, size)`; `state`/`kind`
    deliberately **not** memoized with the rows (a build moves `_status`
    without touching a manifest byte). Plain dicts, GIL-atomic operations, the
    worst race is a recompute — documented, no lock. **Zero kernel calls**,
    asserted with `CountingKernel`. `limit` 1..500 (default 50), `total` in
    the payload, `filters {tag?, material?, state?, kind?, folder?}` ANDed
    through the same `field_term` validation as the query string.
- **`agentcad/core/tools_navigation.py`** (search block) — `service.search =
  Engine(service)` (reused when already bound to this service — an ephemeral
  check service never inherits another tree's memo) and
  `search_parts {project, query, filters?, limit?}`.
- **`agentcad/server/routes_navigation.py` (new)** — `GET
  /api/projects/{proj}/search?q=&limit=` → the same payload; 422 with the
  grammar on a bad query, 404 unknown project; member-only (nothing added to
  the public allowlist — asserted).
- **`tests/fixtures/search_queries.json` (new)** — 15 parts (script text
  inline, one with a real provenance header) × 42 cases (`expect` in rank
  order, `expect_matched_on`, `error` cases). It is the parity contract Slice 5's
  `query_model.js` is tested against. Review found it pinned almost no
  ranking (every pairwise `RANKS` swap stayed green); it now carries cases
  where one query returns rows at different ranks, and a direct rank-table test
  covers the three swaps that are provably invisible to result order
  (`min(3, 4) == min(4, 3)` when every returned row shares the field terms).
  Mutation sweep: 13/13 non-degenerate swaps now fail at least one test.

## Files

- `agentcad/core/search.py`, `agentcad/server/routes_navigation.py`, `tests/fixtures/search_queries.json`, `tests/test_search.py` (161 tests) — new
- `agentcad/core/tools_navigation.py` — search block only

## Notes

**Measured** (1 000-part project, never built, MacBook): cold **59–120 ms**,
warm **6–13 ms**, metadata-only 5–7 ms (AC budgets 500 / 100 ms). Under a
concurrent full-suite load the cold number reached 388 ms — the AC keeps its
headroom but it is the assertion most likely to flake on a shared CI runner;
the warm bound (10× headroom) is the one the UI depends on. Two profiled fixes
made the warm number: `os.stat` on a `str` instead of `Path.stat` (79 % of a
warm search), and one `_status_key` per scan instead of one per part.

`make test` — see 0334 (slices 2 and 3 landed in one commit; the count is cited there).
