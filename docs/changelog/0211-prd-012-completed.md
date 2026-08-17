# 0211 — PRD-012 completed: configurations are first-class, and the family builds as one call

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov (with Claude)

## Summary

Bookkeeping after PR #18 (PRD-012, Configurations) merged to main. The PRD
moves to `docs/prd/completed/` and the roadmap index row flips to completed.
A part record can now carry a named, validated family
(`parts.<id>.configs.<name> = {params, label?, description?}` — the schema
PRD-011 froze — plus `active_config`), every derived artifact has a per-config
identity, an assembly instance binds to a configuration, and a project without
configurations is byte-identical to before.

## Changes

- `docs/prd/in-progress/PRD-012-configurations.md` →
  `docs/prd/completed/PRD-012-configurations.md`, status "completed — merged to
  main in PR #18 (AC1–AC9 verified; AC9 graded as evidence — a real headless
  Chrome session driven with Playwright, `ERROR COUNT: 0` /
  `FAILED REQUESTS: 0`, changelog 0207)".
- `docs/roadmap.md`: index row 012 links `prd/completed/` and reads completed
  (PR #18); PRD-014 and PRD-019's `012` dependency is now satisfied.

## Files

- `docs/prd/completed/PRD-012-configurations.md` — moved + status
- `docs/roadmap.md` — index row
- `docs/changelog/0211-prd-012-completed.md` — this entry

## Notes

Feature history: a design round (0200; eight parallel codebase readers, an
eleven-decision spec, four PRD fold-backs — lowercase names, serial
de-duplicated matrix, strict declaration vs clamp-on-override, v1
dimension-table columns), eight TDD slices (0201–0208) each with an Opus task
review and a fix round, and a three-seat whole-branch review (0209): an Opus
final review (0 Critical / 3 Important / 5 Minor), a Codex review
(`gpt-5.3-codex-spark` — the `gpt-5.6-sol xhigh` seat hit the account's usage
limit until 20 Aug 2026; a re-run post-merge is worth doing), and an
independent verifier that reproduced every finding (6 confirmed, 4
downgraded, 1 refuted, 4 new). One fix wave, one scoped re-review that caught
its own regression (an empty-body `PUT /assembly` wiping the assembly), and
the merge of PRD-005a's main with the entries renumbered (0210).

Three decisions worth remembering: `_rebuild`/`get_part` keep their
pack-wrapped signatures and configuration builds take a separate memoized
path (a shared status slot was a browser livelock, not a perf note); nothing
entered the cache-key payload — config-awareness is `record.effective_params`;
and assembly meshes are content-addressed by `mesh_key`, which removed the
one-mesh-per-part assumption instead of patching it.

Suite at merge: PR #18 CI green (all seven checks green on the first run: three pytest matrices, four geometry checks); the merged tree locally
`make test` — 3906 passed, 7 skipped (one PRD-009 wall-clock flake that
passes alone).
