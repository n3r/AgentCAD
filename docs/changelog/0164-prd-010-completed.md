# 0164 — 2026-08-16 — PRD-010 closed out: the feature toolkit is deep enough to design with

## Summary

Bookkeeping after PR #14 (feature toolkit II) merged to main with all seven
checks green on the first run — macOS, Linux and Windows, plus the four
geometry-CI dogfood jobs. The PRD moves to `docs/prd/completed/` and the
roadmap index reflects it.

This closes the second of the v5 "daily-driver depth" entries and, with
PRD-009's sketcher, the pair that roadmap goal 3 depends on: *powerful manual
CAD with optional AI*. Standard content (PRD-011) is next and is unblocked —
its publish gate runs the PRD-003 specs that shipped in PR #10.

## Changes

- `docs/prd/in-progress/PRD-010-feature-toolkit-ii.md` →
  `docs/prd/completed/`, status "completed — merged to main in PR #14
  (AC1–AC8 + AC7b verified)".
- `docs/roadmap.md`: the PRD-010 row links to `prd/completed/` and reads
  "completed (PR #14, AC1–AC8 + AC7b verified)".

## Files

- `docs/prd/completed/PRD-010-feature-toolkit-ii.md` — moved + status
- `docs/roadmap.md` — index row
- `docs/changelog/0164-prd-010-completed.md` — this entry

## Notes

Feature history: 14 TDD slices (changelog 0144–0159), a close-out (0160), and
two review rounds (0161, 0162–0163). The second round is the one worth reading,
and it is the longest gate any PRD here has had: one independent Opus review
(21 findings), Codex GPT-5.6 xhigh on `9b7095a` (seven major, three minor,
CHANGES-REQUIRED), then **three adversarial verification passes that broke fix
rounds 1, 2 and 5**. Six fix rounds in total.

Two of those are worth carrying forward as method, not trivia.

**A guard can cause the failure it was written to prevent.** `polar(radius=)`
misplaced instances while returning the expected volume, one valid solid and no
warning. The fix added a layout assertion — which then fired on *correct*
patterns of any seed lacking point symmetry, because it measured bounding-box
centres, which are not rigid under rotation. A triangular gusset produced "this
is a placement bug, not a tolerance" on geometry exact to 6e-11. Every test had
used a box or a cylinder. The file's own `_identity_placement` docstring
already warned that bbox positions are not rotation-invariant.

**A verifier retracted its own conclusion, and that was the round's best
finding.** It passed the provenance re-derivation after four steering attacks,
then noticed it had mutated one side of each pair at a time — which
re-derivation naturally catches. Mutating both sides consistently left the
disputed ANSI `#8` callout in place while claiming "corroborated, 0 conflicts",
reopening the exact laundering the fix existed to stop, through the one key
nobody had validated (`size` and `fit` steered provenance but were not in
`RECORD_KEYS`, were never typed, and were never compared against `d`).

The same pass also *disproved* its most promising attack: a fabricated
counterbore pocket validates clean, but the record is byte-identical to what
the documented `cbore_d`/`cbore_depth` author override produces, so a table
check there would fire on a supported API. It reported no finding.

Two gaps ship open, deliberately, with the claim narrowed to what was measured
rather than the tolerance tuned until it looked green: `_seat_present` cannot
see a partially destroyed seat (a 2×2 mm pin at one probe azimuth suffices) or
one filled back in, and a fabricated value inside a *declared* cell still
loads — coverage proves citation, never correctness, at any granularity. Both
are documented **and pinned by tests asserting measured volumes**, so they can
only change deliberately.

Final suite: `make test` — 2527 passed, 1 skipped (1500.46 s, exit 0).
Suite growth across v5 so far: 1441 at the end of PRD-008, 2527 now.

## One test fixed in this commit

`test_the_prd_records_every_divergence_the_design_measured` asserted the literal
`**Status:** implemented`, so moving the PRD to `completed/` on merge turned it
red — the lifecycle working, not a regression. It now accepts either
post-implementation state, which is what the assertion was actually for: a
divergence record only means something once there is work to diverge from.

This is the second time the same trap has fired on this PRD (slice 14
hard-coded `docs/prd/completed/`, which `_find_prd()` replaced). An acceptance
test that pins *where a document is* or *what phase it is in* breaks on the
transition it should be indifferent to. Assert the property, not the phase.
