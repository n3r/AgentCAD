# 0053 — v3 close-out: roadmap rewritten, docs reconciled

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

The close-out of the v3 wave (plan 0034): every remaining item from the old
`docs/roadmap.md` has shipped across changelogs 0035–0052. The roadmap is
rewritten to record what shipped and the deliberately-kept residuals, and
the stale tool-count prose is reconciled with reality.

## Changes

- `docs/roadmap.md` rewritten: the v3 shipped list (typed params, per-solid
  semantics, sheet metal, PMI/GD&T + stack-ups, motion from mates, class-A
  surfacing + curvature, modal/thermal FEM, GUI sketcher + push/pull, vision
  feedback, turn locks + multi-agent sessions, git history, mesh LOD,
  sandboxed workers, three-OS CI, single-binary) plus honest deferrals
  (contact/CalculiX FEM, full kinematic solver, surface-sculpting UX,
  bend relief, non-macOS sandboxing, notarization, CRDT collaboration,
  sketcher depth).
- Tool counts reconciled everywhere they were hardcoded: the registry now
  holds **39 tools** without the `[fem]` extra, **42 with it**
  (`docs/agent-api.md` intro, README ×2, the architecture diagram label).
- README capability paragraph extended with the v3 feature list; the
  roadmap pointer no longer names shipped items as future work.

## Files

- `docs/roadmap.md`, `docs/agent-api.md`, `docs/architecture.md`,
  `README.md`

## Notes

Full-suite verification for the wave is recorded in this commit's message;
the browser pass is 0052. Wave total: 19 commits, ~180 new tests
(138 → 320+ incl. FEM-extra runs), zero regressions.
