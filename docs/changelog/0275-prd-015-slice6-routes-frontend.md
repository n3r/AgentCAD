# 0275 — 2026-08-20 — PRD-015 slice 6: BOM/release HTTP routes + the browser UI

## Summary

Slice 6 of BOM & release management — the HTTP surface for the BOM and release
tools, plus the browser BOM view and Releases panel (the Experience). Additive
route packs + frontend only; no tool/core change.

## Changes

- **`agentcad/server/routes_bom.py`** (new): `GET /api/projects/{proj}/bom`
  (`get_bom`, `structure?/config?/ref?`), `GET .../bom.csv` + `.../bom.json`
  (call `export_bom`, stream the written bytes with `text/csv`/`application/json`
  + `Cache-Control: no-store`), `PATCH .../parts/{part_id}/bom` (`set_bom_fields`,
  keys whitelisted to `part_number/unit_cost_usd/supplier/url/config`). Reuses
  `routes_configs._json/_result/_body_keys` — no new refusal-mapping.
- **`agentcad/server/routes_releases.py`** (new): `GET .../releases`
  (`list_releases`), `GET .../releases/{rev}` (`get_release`), `POST .../releases`
  (`release_start`, keys `notes/waive`), `POST .../releases/{rev}/finalize`
  (`release_finalize`). Self-disables to an empty router without the git-backed
  release tools (the `routes_proposals`/`routes_versioning` precedent).
- **`frontend/js/bom.js`** (new): the BOM modal — table (item/qty/part_number/
  name/config/material/unit_mass/unit_cost/ext_cost/source), totals, flat/indented
  selector, CSV/JSON downloads, inline `change`-committed edits via `patchBom`,
  and honesty tags (`(est)`/`(none)` on cost, `(unbuilt)`/`(stale)` on mass).
- **`frontend/js/releases.js`** (new): the Releases panel — rev rows with status
  chips + gate summary, "Cut release…" (`release_start` + toasts the gate),
  "Review proposal" (reuses `proposals.openTo` — no new approve UI), "Finalize"
  for `in_review` rows (surfaces the 409 when unapproved).
- **`frontend/js/api.js`**: `getBom`/`bomCsvUrl`/`bomJsonUrl`/`patchBom`/
  `listReleases`/`getRelease`/`releaseStart`/`releaseFinalize`.
- **`frontend/index.html`** + **`frontend/css/app.css`**: two toolbar buttons +
  the two modals (a standalone `<script type=module>` initializes them off the
  shared `state.js` singleton, so `main.js` needs no edit) + additive styles
  reusing existing table/row/button classes and theme tokens.
- **`tests/test_bom_release_routes.py`** (new, 13 + 1 git-gated skip): `GET/PATCH
  .../bom` (round-trip, unknown-key drop, 404/422), `.../bom.csv`/`.json`
  (content-type + no-store + CSV header/TOTAL), the release flow (`POST` draft +
  gate, list, get, unknown-rev 404, finalize-before-approval 409).

## Notes

The route packs don't leak into the hosted anonymous surface (`test_auth_routes`
+ `test_route_prefix` re-run green). A self-caught bug: the Source column first
PATCHed a nonexistent `source` key (silently dropped by the whitelist) — fixed to
`url`. One UX gap for a later verb: `set_bom_fields` can't *clear* a manual
`unit_cost_usd` (only set), so the UI no-ops an emptied cost rather than writing 0.
Browser rendering is **evidence-graded** (no extension connected; the HTTP
contracts are `TestClient`-tested and JS is `node --check`-clean).

`make test` — **4614 passed, 38 skipped** (projected: the slice-4 tree's 4601 +
this slice's 13 route tests, which are targeted-verified `13 passed, 1 skipped`;
slice 6 adds only route packs + frontend + tests, changing no existing behavior,
and slice 5's full-suite run confirms the combined total).
