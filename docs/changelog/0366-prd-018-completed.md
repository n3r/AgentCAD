# 0366 — 2026-08-26 — PRD-018 closed out: task-to-part generation ships

- **Commit:** (this commit)
- **Date:** 2026-08-26
- **Author:** Claude (orchestrated) / Nikita Fedorov

## Summary
PRD-018 (task-to-part generation) merged to main in **PR #37** (squash `470b224`).
This close-out moves the PRD to `docs/prd/completed/`, flips its Status, and marks
the roadmap row completed. No code change.

## What shipped (MVP + most of Phase 2)
A kernel-grounded generation loop: a task prompt (plus optional images / PDFs)
drives draft → build → render → measure → revise until the geometry is both
kernel-valid and spec-green, reusing `ChatEngine`'s `client_factory` seam (chat
core imported, never edited). N candidates on their own scratch ids; NEMA
grounding from a shipped `tables/*.json`; FR7 connectors; provenance; accept as a
direct write or a proposal; a `generate_from_prompt` bench category with a
loop-vs-one-shot delta. Generation tools register only when `ANTHROPIC_API_KEY`
is set at startup; the provenance wrapper installs unconditionally.

## The integrity centerpiece (the review's contribution)
A four-way review (Opus lenses + an adversarial verifier + Codex xhigh) found the
loop and `accept_candidate` trusting the candidate's **self-reported** specs. The
fix: the frozen intent contract is enforced by **re-measuring it server-side
against built geometry**, never by diffing re-declared `SPECS` and never through
anything `build()` can observe. The `frozen_measure` kernel op builds the
candidate's UNMODIFIED recorded bytes (byte-for-byte what `create_part` builds,
nothing appended) and returns raw kernel metrics; the server evaluates every
frozen bound itself, fail-closed, at loop terminate and at accept (bound to the
immutable recorded snapshot). This defeated a proven landing exploit (a `build()`
that detects an injected measurement probe and fakes compliance).

## Deferred (recorded honestly)
NEMA hole-pattern feature verification (needs a kernel circle-inventory
measurement; a garbage part already cannot land because the footprint check fails
un-forgeably), branch coherence of scratch parts on a non-default branch,
background jobs (PRD-020), and model-tiering.

## Changes
- `docs/prd/in-progress/PRD-018-task-to-part-generation.md` →
  `docs/prd/completed/`, Status → "completed — merged to main in PR #37".
- `docs/roadmap.md` — row 018 relinked to `completed/` and marked completed.

## Notes
`make test`: **7288 passed, 52 skipped** on the merged tree with the optional
extras installed locally (run in two parallel halves and summed — the full
~7.3k-case suite exceeds this environment's single-run memory ceiling). CI is
green on PR #37: the macOS/ubuntu `pytest` legs, the four geometry `check` packs,
and the bench self-test all pass; the PDF-integration tests skip on a CI leg that
does not install the `[pdf]` extra (the FEM `importorskip` idiom). Zero
regressions — the PR diff touches no FEM / supervisor / sandbox / pool code, and
the one kernel change (`frozen_measure`) is purely additive.
