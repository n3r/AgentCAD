# PRD-015 BOM & release management — implementation plan

Design: `docs/superpowers/specs/2026-08-19-bom-release-management-design.md`. TDD
per slice; the controller (not subagents) runs `make test` and commits, one
changelog per commit. Slices are mostly serial (shared `core/bom.py`/
`releases.py`/`manifest_merge.py`); the frontend (Slice 6) is disjoint and can
run alongside a late backend slice.

## Slice 1 — BOM builder + fields (FR1-3)
- `core/bom.py`: `count_leaves(service, proj)` — **zero-kernel** structural walk
  (patterns × count, sub-assembly recursion with cycle guard, origin-project
  keying); `build_bom(service, proj, structure, config?)` → grouped lines with
  all FR2 fields; cached-metrics read via `service._status`/`_config_status`
  directly (never `_ensure_built`) + staleness via `_cache_key_for`, warning on
  `unbuilt`/`stale`; cost (`manual`/`material_estimate`/`none` + `cost_source`);
  package `part_number`/`url` inheritance via the provenance header → package.json.
- `tools_bom.py`: `get_bom {project, ref?, config?, structure?}` (ref deferred to
  slice 2), `set_bom_fields {...}`; manifest per-part `bom` field.
- `manifest_merge.py`: `"bom"` → `_PART_SUBDICTS`.
- Tests: flat roll-ups (patterns count, sub-assembly ×N — AC2), per-config lines
  (AC7), cost fallback + `cost_source`, unbuilt→warning (no rebuild), package
  inheritance, `set_bom_fields` validation. **Opus.**

## Slice 2 — exports + ref-pinned BOM (FR4-5)
- `core/_worktree.py`: `materialized_service(service, ref)` contextmanager
  (tag-capable, from the checks.py `_ephemeral_service` pattern — worktree add,
  `write_guard=None`/`branch_resolver=None`, teardown). Do NOT break the checks
  determinism tests; if lifting from checks.py is risky, build a faithful sibling
  and note it.
- `export_bom {project, format, ref?}` — CSV (RFC-4180, totals row, `cost_source`
  column) + JSON; `get_bom {ref}` resolves branch/tag via the helper.
- Tests: CSV strict re-parse + comma/quote round-trip + totals match JSON (AC3);
  `get_bom {ref=tag}` reproduces a past BOM. **Opus.**

## Slice 3 — release records + gate + start (FR6-8)
- `proposals.py`: additive `kind: str = "change"`; carry through `_summary`/object;
  `tools_proposals`/`routes_proposals` forward it.
- `core/releases.py`: `manifest["releases"]` record + rev auto-sequence + state
  machine; `release_start {project, notes?, waive?}` opens a `release`-kind
  proposal, reads `proposal["gates"]` (specs + checks), adds release checks
  (clean tree, sub-assembly refs pinned, drawings regenerable), returns the gate
  report + proposal id; `waive` records a waiver. `list_releases`/`get_release`.
- `manifest_merge.py`: `"releases"` → `_ENTRY_DICTS` (+ `_write_path` set).
- Tests: a failing spec blocks start with the check named in `details.gate`, a
  waiver proceeds and shows in `get_release` (AC4); rev auto-sequence. **Opus.**

## Slice 4 — finalize + tag + immutability (FR9, FR12)
- `release_finalize {project, rev}` (idempotent; auto on approval): tag
  `release/<rev>`, append `tags.json` referrer, transition `released`, supersede
  prior, record approvals from proposal reviews, emit `release_changed`.
- Immutability: a `released`/`superseded` record is append-only → `conflict_error`
  on mutation; editing a released tag's state is refused (branch instead).
- Tests: mutate-on-released → conflict_error, branch-from-tag edits succeed (AC5);
  approval attribution recorded. **Opus.**

## Slice 5 — reproducible bundle (FR10-11)
- `core/releases.py` bundle job in `materialized_service(release/<rev>)`: STEP
  (part+assembly), drawings via `generate_drawing(version={ref,date})`, bom.csv/
  json, flat patterns, README, `artifacts.json` (sha256); copy out to
  `exports/releases/<rev>/` + zip.
- Tests: two runs → identical artifacts.json hashes for drawings/BOM/flat/README,
  STEP matches after timestamp normalization (AC6). **Opus.**

## Slice 6 — frontend (Experience) — disjoint, can parallel slice 4/5
- BOM view (`bom.js` + `tree.js`): table + totals + CSV/JSON export + inline edits
  via `api.patchBom`; Releases panel (`releases.js`/`versions.js`) with status
  chips + cut-release flow reusing `proposals.js` approve UI.
- Verified in a real browser if the extension is available, else evidence-graded.
  **Sonnet.**

## Slice 7 — acceptance + docs (AC1-8)
- `tests/test_prd015_acceptance.py`: AC1 (release end-to-end — API half machine,
  browser evidence-graded), AC2 (roll-ups flat==indented), AC3 (CSV lossless),
  AC4 (gate block + waiver), AC5 (immutability), AC6 (bundle reproducibility),
  AC7 (per-config), AC8 (no-release projects unchanged).
- Docs: `docs/agent-api.md` (7 new tools + proposal `release` kind), `docs/user-
  guide.md` (BOM + releases), `AGENTS.md` ("BOM/release gotchas": zero-kernel
  count enumeration, tag-capable worktree vs branch-only, gate-via-proposal,
  release-record immutability, cost-honesty column, process-lifetime metrics
  caveat), roadmap on close-out. Also note this unblocks PRD-014 FR3/FR4/FR5.
  **Opus tests + Sonnet docs.**

## Non-negotiables
- `core/bom.py` makes **zero kernel calls** (count-only; cached-metrics peek).
- Only tag-capable materialization for `ref`/bundle (checks.py pattern), never
  the branch-only `tree_of`.
- Determinism: BOM sorted + `fmt`-free JSON; bundle reproducibility tested.
- Cores untouched; proposal `kind` additive; changelog per commit; subagents
  don't git/uv sync.
