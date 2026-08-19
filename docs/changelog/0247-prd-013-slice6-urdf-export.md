# 0247 — 2026-08-19 — PRD-013 slice 6: URDF export + inertia-frame correction

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

Sixth slice of Assembly v2: `export_urdf` turns the resolved assembly into a
URDF robot description + one mesh per link under `exports/urdf/<name>/`. The
builder (`core/urdf.py`) is OCP-free — mass, inertia, connector frames and
transforms come from the kernel; the XML assembly, the hand-rolled
`validate_urdf` (stdlib XML + structural asserts, no new dep), and the
parallel-axis inertia shift live here. Mapping: rigid→`fixed`,
revolute→`revolute` w/ limits (`continuous` when unbounded), slider→`prismatic`,
planar→`planar`; cylindrical/ball degrade to `fixed` + a named warning; an
unmated instance becomes a `fixed` child of `world` + a warning (FR15).

**Correctness finding (prominent divergence):** OCCT's `MatrixOfInertia` is
expressed about the **centre of mass**, not the global origin — so the analysis
handler's long-standing "tensor about the global origin" note was FALSE for
off-origin parts. Fixed `handlers/analysis.py` to parallel-axis the tensor
forward to the origin (making the contract true), and the URDF export shifts it
back to each link's COM — the round trip that makes the reference frame matter,
and which URDF requires. Without this an off-origin link's inertia is a
positive-but-WRONG tensor about the COM (its eigenvalues go negative — caught by
`validate_urdf`'s SPD check).

## Changes

- `core/urdf.py` (**new**, OCP-free): `inertia_kg_m2_about_com` (the reverse
  parallel-axis shift + g·mm²→kg·m²), `build_urdf` (deterministic, byte-stable
  formatting), `validate_urdf` (well-formed + positive mass + SPD inertia +
  known joint types + a single tree rooted at `world`, no cycles).
- `core/tools_urdf.py` (**new** pack): `export_urdf {project, name?,
  mesh_format?} -> {path, links, joints, warnings}`. Links = resolved instances
  (mass + COM-shifted inertia + one STL each); joints from native mates, frames
  from the kernel. Sorts before `tools_versioning`; reads no cross-pack seam; no
  gate.
- `kernel/handlers/connectors.py`: new `urdf_frames` handler — per-joint
  `<origin>` (child-relative-to-parent, mm + URDF fixed-XYZ rpy from the
  rotation matrix) and `<axis>` (connector axis in the joint frame), plus type +
  DOF range. Euler/Location math stays in the kernel (one convention).
- `kernel/handlers/analysis.py`: `_inertia` now returns the tensor about the
  global origin (was COM — the note lied), via a forward parallel-axis add.

## Files

- `agentcad/core/urdf.py` (new), `agentcad/core/tools_urdf.py` (new),
  `agentcad/kernel/handlers/connectors.py`,
  `agentcad/kernel/handlers/analysis.py`
- `tests/test_urdf.py` (new), `tests/fixtures/urdf/rocketry_stack.urdf` (golden)

## Notes

- **Parallel-axis shift, hand-checked (negation-tested):**
  `test_inertia_parallel_axis_shift_offorigin` builds an origin tensor by the
  forward add from a known cube COM tensor, shifts it back, and asserts it
  equals the cube's analytic COM tensor and is SPD;
  `test_unshifted_tensor_is_wrong_negation` asserts the un-shifted origin tensor
  about the COM is NOT close (orders of magnitude off on the diagonal).
- **AC6 machine half:** the golden `rocketry_stack.urdf` (relative mesh paths, so
  machine-independent) parses under `validate_urdf` and is byte-stable; link
  masses match `get_metrics` within 0.1%; joint types map correctly (fixed /
  revolute+limit / prismatic+limit). urdf-viz / `check_urdf` is evidence-graded
  (no checker on the machine).
- **planar→planar** follows this task's mapping (the spec table degraded planar
  to fixed in MVP); URDF `planar` is a valid type and `validate_urdf` accepts it.
- The unmated-link `<origin>` carries world position only (rpy 0) — the base is
  rarely world-rotated; exact world rpy for a rotated root is a follow-up.
- The `_inertia` frame change affects only `analyze_part`'s off-origin output;
  the one existing inertia test uses an origin-centred box (COM≡origin), so it is
  unaffected, and no internal code consumes that tensor.
- Measured: `tests/test_urdf.py` 8 passed; `tests/test_analysis.py` green with
  the frame change. Prior tree 4135 passed, 1 skipped after slices 1–3
  (changelog 0244).
