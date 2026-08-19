# 0240 — 2026-08-19 — PRD-013 review fixes: planar URDF degrades, not a wrong-normal joint

## Summary

Two independent reviewers on split axes both returned **SHIP** for PRD-013
Assembly v2. The assembly-math reviewer re-derived the sharpest change — the
inertia reference-frame round trip — from scratch and confirmed it exact
(OCCT's `matrix_of_inertia` is about the COM, verified by probe; `analyze_part`
forward-shifts to the origin; URDF back-shifts to the COM; round-trip max abs
diff 3.4e-21, SPD holds, golden inertias re-derived by hand) with no consumer
regression (only two readers of `inertia_tensor_g_mm2` repo-wide, the test one
origin-centered). Pattern replace-not-add, two-level sub-assembly mass, the
interface-mate geometry, DOF clamping, and simplified-is-display-only all
re-derived correctly. The isolation/honesty reviewer confirmed the four
never-edit cores are untouched, AC8 no-regression holds (flat assemblies
resolve identically; the clamp change is in-range-identical to 1e-12), the
schema bump is additive (zero key drift across five example projects), every
evidence/Phase-2 AC is honestly graded, and no confinement code was touched.

One LOW finding is fixed here.

## Fix — a planar mate no longer exports a URDF joint with a missing axis

The URDF `planar` joint type requires the plane normal as its `<axis>`, but our
planar connector stores a `location`, not an axis — so the build emitted
`<joint type="planar">` with **no `<axis>`**, which a real URDF loader reads as
the default normal `(1,0,0)`: a valid-looking wrong plane orientation, and
untested (the assembly-math reviewer's LOW-1). This is also out of MVP scope —
the PRD's URDF core is fixed/revolute/prismatic (FR14), and AGENTS.md already
reserves a correct planar URDF for Phase 2. So `planar` now **degrades to
`fixed` + a `joint_degraded` warning**, exactly like `cylindrical`/`ball`,
until the Phase-2 export that carries the plane normal. The planar joint's full
DOF still resolves in the assembly (motion, interference); only its URDF
representation degrades.

Added `test_planar_mate_degrades_to_fixed_with_a_warning` (the untested case the
review flagged): a planar-mated arm exports a `fixed` joint, a `joint_degraded
{from: planar}` warning, and **no `<axis>`**. `docs/agent-api.md` corrected
(it still said `planar→planar`).

## Recorded, not fixed (reviewer INFO/LOW, non-blocking)

- **`get_assembly` double-resolves a same-project assembly** (both reviewers):
  the wrapper computes `_resolved_instances` to pick the cross-project path,
  then delegates to `type(service).get_assembly` which resolves again. Correct
  (idempotent), only redundant compute — and the delegation is a deliberate
  trade for the class-monkeypatch dispatch property. A perf follow-up.
- **URDF joint name equals its child link name** — unconventional but
  `validate_urdf` keeps the namespaces separate and ROS tolerates it.

## Files

- `agentcad/core/tools_urdf.py` — planar → fixed + warning (the `_JOINT_MAP`)
- `tests/test_urdf.py` — the planar-degrade test
- `docs/agent-api.md` — the corrected `export_urdf` mapping line
- `docs/changelog/0240-prd-013-review-fixes.md` — this entry

## Notes

`make test` — **4182 passed, 1 skipped** (clean run on the committed tree
after this fix: 4155 in the parallel phase + 27 in the serial bench/drag tail,
both exit 0). This is 0239's pre-fix 4181 plus the one planar-degrade test
added here. Nothing here touches the inertia round trip, pattern expansion,
sub-assembly resolution, or the AC8 no-regression path the reviewers cleared.
