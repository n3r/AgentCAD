# 0036 — Engine: real thread geometry, hollow manifolds, analysis() contract hook

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Nikita Fedorov / Claude

## Summary
Answers the "cosmetic cylinders and solid blocks" critique: every fastener
and stud now carries real ISO thread geometry; the intake/exhaust manifolds
and throttle body are hollow parts with continuous gas paths and blended
junctions. To keep `check_interference` tractable with helical B-reps, the
part contract gains an optional ``analysis(p)`` hook (a conservative
envelope used only for interference), and the kernel's pairwise check is
rebuilt on per-solid decomposition — which also fixes a real correctness
bug: build123d 0.9's ``&`` misbehaves on multi-solid Compound operands.

## Changes
- **Kernel** (`agentcad/kernel/worker.py`):
  - `_item_shape(..., analysis=True)`: scripts may define ``analysis(p)``;
    `handle_interference` prefers it (cached alongside the build shape).
    Display/export/metrics always use ``build(p)``.
  - `handle_interference` decomposes instances into solids, prefilters
    solid pairs by AABB, and crops the larger solid to the AABB overlap
    before the exact common when the overlap is small. Every boolean is
    strictly Solid-vs-Solid: compound-`&` in build123d 0.9 returned bogus
    volumes (observed: identical values across differently-posed
    instances) and `Compound & Box` returned empty.
- **Contract docs** (`docs/part-authoring.md`): new "Analysis stand-ins"
  section — the stand-in must be a superset of the real shape.
- **Tests** (`tests/test_kernel.py`): `test_interference_prefers_analysis_shape`.
- **Engine parts**:
  - All 9 fastener sets + throttle body refactored to
    `_build(p, simple)` with `build` (real threads) and `analysis`
    (nominal envelope); hardware sets are now true Compounds, not fuses.
  - New `stud_set` (16 fully threaded M8 rods per head, 2 instances);
    the head's fake stud cylinders replaced by tapped Ø8.4 holes.
  - `intake_manifold`: hollow rebuild — shelled plenum, annulus-swept
    runner tubes, solid channel sweeps subtracted through flange/runner/
    wall, fillet-blended junctions (no collar discs), tapped TB flange.
  - `exhaust_manifold`: shelled collector + tail cone with open end,
    tubular primaries, channel sweeps, blended junctions.
  - `throttle_body`: counterbored recessed screws, filleted flange/trumpet
    steps.

## Files
- `agentcad/kernel/worker.py`, `docs/part-authoring.md`,
  `tests/test_kernel.py`
- `examples/engine/parts/*` (12 edited, 1 new), `examples/engine/project.json`,
  `examples/engine/README.md`

## Notes
Assembly STEP export grows to ~210 MB with real threads — the honest cost
of exact helical geometry. Nut/tapped internal threads are deliberately not
modeled in contact with the male threads (see the fasteners example's
"mating threads always interpenetrate" rule); the README documents this.

## Addendum
- `tests/test_examples.py` gets a module-level `pytest.mark.timeout(900)`
  and `service.check_interference` a 600 s kernel budget — the threaded
  63-instance engine legitimately needs minutes where 120 s once sufficed.
- `exhaust_manifold` skips its junction blend below Ø30 primaries: the
  OCCT fillet on that junction crashes the worker (a segfault, not a
  catchable failure) at small tube sizes.
