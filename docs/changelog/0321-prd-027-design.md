# 0307 — 2026-08-23 — PRD-027 Navigation at scale: design spec + slice plan, PRD moved to in-progress

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

Opens PRD-027 (project/part/assembly navigation at scale). Records the design
decisions and the orchestrator's rulings in a spec, breaks the build into
seven subagent slices in a plan, and moves the PRD to `docs/prd/in-progress/`.
No code changes.

## Changes

- `docs/superpowers/specs/2026-08-23-project-navigation-scale-design.md` —
  scope (MVP + Phase 2; sub-assembly nesting / pattern member rows / 1k-instance
  certification deferred to Phase 3), nine decisions and an eleven-entry rulings
  ledger. Load-bearing rulings: folders/tags are **manifest metadata on the part
  entry** (never directories, never a sidecar — the manifest is what every seam
  snapshots); instance `folder` is a five-writer change because `set_instances`
  is a full replace from `to_manifest()`; a **bulk op is one store RMW + one
  `project_changed` publish = one undo step** (no history grouping API, no
  `in_restore` abuse); search is an **on-demand scan with a stat-validated memo**
  (no inverted index, no bus plumbing) with a 1k-part latency AC; thumbnails are
  keyed by the **build cache key** in `.cache/<key>.thumb.png`, pre-warmed by a
  bus subscriber and rendered on demand from an existing mesh — **never
  building**; `rebuild_finished` gains `cache_key`; the thumb route is the
  codebase's first non-`no-store` binary response (immutable only when the
  client names the matching key); the dashboard is kernel-free and `mass_g` is
  `null` when any part is unbuilt; `tags` merge atomically (whole-list
  conflict) — stated, not papered over; tool count moves 85 → 88.
- `docs/superpowers/plans/2026-08-23-project-navigation-scale.md` — seven
  slices with exact interfaces: S1 metadata + `set_part_meta`; S2 search engine
  + `search_parts` + the shared `tests/fixtures/search_queries.json` parity
  fixture; S3 thumbnails + `routes_thumbnails`; S4 `bulk_part_op` + dashboard;
  S5 frontend pure models (`query_model`, `virtual_model`, `tree_model`
  additions, `shell/contextmenu`); S6 tree rewrite / bulk bar / dashboard /
  wiring with a Playwright evidence pass; S7 acceptance + docs. Dependency map:
  S1 → (S2 ∥ S3) → (S4 ∥ S5) → S6 → S7.
- `docs/prd/in-progress/PRD-027-project-navigation-scale.md` — moved from
  `pending/`, `Status:` line updated; `docs/roadmap.md` row 027 → in-progress
  with design/plan links.

## Files

- `docs/superpowers/specs/2026-08-23-project-navigation-scale-design.md` — new
- `docs/superpowers/plans/2026-08-23-project-navigation-scale.md` — new
- `docs/prd/in-progress/PRD-027-project-navigation-scale.md` — moved + status
- `docs/roadmap.md` — index row
- `docs/changelog/0307-prd-027-design.md` — this entry

## Notes

Docs only; the suite is unchanged from `main`. `make test` — **5089 passed**
(the count cited by 0306 on the base commit `95fdcc3`; no test was added or
changed here). The engine example is 33 parts / 65 instances on `main`, not
the PRD's 32/63 — the acceptance test uses the real ids. PRD-017 is being
built in parallel on another worktree; changelog numbers will be renumbered
at merge time if they collide.
