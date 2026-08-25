# 0357 — Roadmap: six new PRDs + PRD-025 amendment from the Aug-2026 UX study

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Nikita Fedorov (with Claude)

## Summary

Folds the outcome of the four-round UX study (Aug 24–25 2026: interactive
mockups, founder-validated "familiar workstation" direction, two-reviewer
adversarial pass) into the roadmap: PRD-025 is amended with the study's
resolutions, and six new PRDs capture the capabilities the study surfaced
that no pending PRD covered.

## Changes

- **PRD-025 (amended, still pending):** tab set fixed to four in the
  founder's nouns — Design · Testing · Production · Marketplace — with
  Library folded into Marketplace ("My Library"), resolving the PRD's own
  "five tabs" IA risk; a "Direction validated" callout records the study,
  the still-open container-word rename owed to the PRD-005 "mode" ruling,
  and two review-derived invariants now binding every tab (state honesty:
  hash-keyed derived results render an explicit out-of-date state;
  affordance honesty: no dead control styled live). Body updated
  throughout (goals, experience per tab, FR1/2/9/10, MVP, AC1–AC4,
  risks); per-tab content wired to the new PRDs.
- **PRD-034 (new):** Feature timeline & model⇄code sync — tiered feature
  trees parsed from scripts (curated-style AST / annotations / honest
  opaque node), Fusion-style timeline, three-way selection sync, modeless
  live-preview feature edits emitting script, GUI feature creation.
- **PRD-035 (new):** Simulation studio — persisted studies over the
  existing `[fem]` tier, face-anchored fixtures/loads, hash-keyed results
  with structural staleness, real per-kind result displays, study
  criteria as PRD-003 spec rows (CI-gradable), assembly joint margins;
  flow only via PRD-022 cloud burst.
- **PRD-036 (new):** Production planning & routing — per-part routes
  (print/CNC/order/provided) over the PRD-015 BOM with basis-labeled
  costs/leads and hash-honest readiness; route artifacts (CNC handoff
  pack, print job, supplier order lines); records the CNC-programs
  founder request against the standing CAM non-goal instead of smuggling
  scope.
- **PRD-037 (new):** Print studio — in-app slicing in the Bambu grammar;
  external open slicer engines run as confined subprocesses over file
  exchange (AGPL process boundary; capability-gated like `[fem]`);
  previews parsed from real engine output; stale jobs gate print/export.
- **PRD-038 (new):** Generative shape studies — objective +
  keep-constraints + load case over parametric shape families (toolkit
  packs) driven by PRD-019 optimizers, analytic screen + FEM
  verification, applied as reviewable script features with provenance;
  topology optimization deferred to a cloud-burst tier.
- **PRD-031c (new):** Marketplace community layer — galleries, comments
  with publisher/agent badges, honest ratings (never seeded or
  defaulted), remix-with-lineage into the user's own project, personal
  collections; safety inversion unchanged.
- **Roadmap:** new section "How the August 2026 UX study shaped this
  roadmap" (outcome → PRD mapping, the recorded CNC tension); index rows
  added for 031c/034/035/036/037/038; PRD-025's row updated; the CAM
  non-goal annotated (slicing-via-external-engines is not CAM-in-house;
  the founder request is on record, amendment required to act on it).

## Files

- `docs/prd/pending/PRD-025-modes-ia.md` — amended (see above)
- `docs/prd/pending/PRD-034-feature-timeline-model-code-sync.md` — new
- `docs/prd/pending/PRD-035-simulation-studio.md` — new
- `docs/prd/pending/PRD-036-production-planning.md` — new
- `docs/prd/pending/PRD-037-print-studio.md` — new
- `docs/prd/pending/PRD-038-generative-shape-studies.md` — new
- `docs/prd/pending/PRD-031c-marketplace-community.md` — new
- `docs/roadmap.md` — study section, index rows, non-goal annotation

## Notes

Docs-only change; no code, no tests affected. The UX-study mockups and
review live outside the repo (internal artifacts). Number allocation:
033 was already taken by OCCT stewardship, so the study's PRDs start at
034; 031c follows the 005a/031a/031b letter-suffix precedent. The two
honesty invariants in PRD-025 originate from the study's adversarial
review (stale-results and dead-control defect classes) and are inherited
by 034–038 explicitly.
