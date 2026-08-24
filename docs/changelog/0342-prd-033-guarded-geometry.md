# 0342 — PRD-033 added: Guarded geometry & OCCT stewardship

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (orchestrator) with an Opus drafter and three independent reviewers

## Summary
Adds `docs/prd/pending/PRD-033-guarded-geometry-occt-stewardship.md` and its
roadmap row (end of the v6 index). Docs only. The PRD answers "we rely heavily
on OCCT — can we build a better version for ourselves?" with three phased
investments: a guarded-op contract behind an enumerated registry (measurement,
structured degradation, or honest refusal — generalising
`kernel/handlers/_bop.py` and the bench's D5 rule), a defect corpus with a
per-build/platform expectation matrix (OCCT#1496 is test zero), differential
fuzzing with metamorphic probes feeding an upstream-contribution programme,
and — only on a written entry trigger (a validated patch upstream will not
land) — owning the OCCT+OCP wheel build, starting from CadQuery's
ocp-build-system, with LGPL compliance counsel-gated. A from-scratch kernel
and kernel-portability claims are explicit non-goals.

## Review before publishing (user-mandated)
Draft reviewed by three independents: an Opus rigor/feasibility reviewer
(22 findings, 4 Critical — e.g. the draft's fuzzer oracle reinstated the
STEP-round-trip framing changelog 0309 retracted; a byte-identity parity bar
no compiler contract can honour), **Codex GPT-5.6 Sol at xhigh** (19 findings;
its three structural objections — the wheel pipeline had no first customer,
"verified" over-promised without an independent oracle, "kernel independence"
contradicted build123d being the model language — drove the rename, the MVP
inversion, and the deletion of the portability claim), and an Opus
strategy/roadmap reviewer (14 findings — MVP inversion, missing false-refusal
and runtime-cost risks, three roadmap prose edits). All consolidated into one
revision (521 → 438 lines); the orchestrator ruled one restructured PRD over
Codex's split-into-four preference.

## Files
- `docs/prd/pending/PRD-033-guarded-geometry-occt-stewardship.md` — new
- `docs/roadmap.md` — 033 row appended to the v6 index; the standing
  kernel non-goal gains the "owning the build ≠ writing a kernel" clause;
  the sequencing prose records 033's MVP as the v6 exception
- `docs/changelog/0313-prd-033-guarded-geometry.md` — this entry

## Notes
Docs-only; no code, no tests, no surface change. Known follow-up recorded in
the PRD: CLAUDE.md's condensed bench trap still carries the retracted STEP
phrasing in one clause — fix with the first PRD-033 implementation commit.
