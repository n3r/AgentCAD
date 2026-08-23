# 0340 — PRD-027 completed: move PRD to completed/, mark roadmap DONE

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary

PRD-027 (project, part & assembly navigation at scale) merged into `main` via
PR #34 (`bf3b41a`): manifest folders/tags, `search_parts` + the `field:value`
grammar, content-hash thumbnails that never build, `bulk_part_op` as one
undo step, the kernel-free dashboard, and the virtualized folder tree with
filter, multi-select, context menu and bulk bar. This commit is the
close-out: the PRD moves from `docs/prd/in-progress/` to
`docs/prd/completed/` with its status line updated, and the roadmap row flips
to **completed (PR #34)**. No code changes.

## Changes

- `docs/prd/completed/PRD-027-project-navigation-scale.md` — moved from
  `in-progress/`; `Status:` now names PR #34, the merge sha, the shipped scope
  (MVP + Phase 2) and the deferred Phase 3 (sub-assembly nesting, pattern
  member rows, 1k-instance certification).
- `docs/roadmap.md` — row 027 → completed with the one-line scope and the
  design/plan links; the "026/027 stay early-v5 movable" sentence now records
  both as DONE (PRs #29 and #34).

## Files

- `docs/prd/completed/PRD-027-project-navigation-scale.md` — moved + status
- `docs/roadmap.md` — index row + prose
- `docs/changelog/0340-prd-027-completed.md` — this entry

## Notes

The build is recorded in changelogs 0331–0339 (design, seven slices, the
three-reviewer fix wave). Follow-ups recorded there and in the PR: a
stat-memoized cold `thumb_key` in `get_project` (a fresh server shows
placeholders for previously built parts until they rebuild — pre-existing
for the state dot), a transactional history seam (publish-after-unlock is
the house pattern), a unified forced-delete flow in the UI, and Phase 3 with
PRD-013's next phase.

Docs only; the suite is unchanged from the merge. `make test` on the merged
tree before PR #34 — **6552 passed, 76 skipped**; PR #34's CI (macOS PR
suite, ubuntu portability, geometry checks, bench self-test) green.
