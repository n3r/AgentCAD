# 0030 — Fix imported-mesh shading: crease-angle normals for mesh faces

- **Commit:** 9657083
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Fixes "melted" shading with dark spiky halos on imported STL meshes.
`tessellate()` in `agentcad/kernel/mesh.py` averaged per-vertex normals across
an entire face — correct for a single-surface B-rep face, but wrong for an
imported STL, which is one welded triangulation covering the whole part, so the
average smeared across every crease and hole rim.

## Changes
- Splits normal computation in `mesh.py` into two paths, selected per face by
  `BRep_Tool.Surface_s(face) is None` (no underlying geometric surface ⇒
  imported mesh):
  - `_smooth_face_normals(pts, idx)` — the prior behavior, area-weighted
    per-vertex average over the whole face, shared vertices kept. Used for
    B-rep faces (unchanged shading).
  - `_crease_mesh_normals(pts, idx, crease_angle)` — new. Splits every triangle
    corner into its own output vertex; each corner's normal averages only the
    incident triangles whose unit normal is within `crease_angle` of that
    triangle's normal. Curved regions stay smooth; sharp edges/hole rims stay
    crisp. Falls back to the triangle's own normal when the accumulated vector
    is near-zero.
- Adds `CREASE_ANGLE = math.radians(35.0)` constant and an `import math`.
- Updates the `tessellate()` loop to call the selected helper and to advance
  the vertex `base` by `len(f_pos)` (mesh path emits `n_tris * 3` vertices).
- Hardens `tests/test_examples.py`: both example tests now `continue` when
  `detail.get("kind") == "reference"`, skipping the `is_valid`/`params_spec`
  assertions and param sweeps for imported reference parts that have no script.
- Adds `tests/test_mesh.py` regression tests: `test_imported_mesh_uses_crease_
  normals` (a re-imported STL box must have flat, axis-aligned per-face normals
  via a `build_reference` request) and `test_brep_face_stays_smooth` (a B-rep
  cylinder wall must keep >30 distinct normal directions).

## Files
- `agentcad/kernel/mesh.py` — split into `_smooth_face_normals` /
  `_crease_mesh_normals`; per-face mesh-vs-B-rep dispatch; `CREASE_ANGLE`;
  updated module docstring
- `tests/test_examples.py` — skip reference parts (no script/params spec)
- `tests/test_mesh.py` — added crease-normal and B-rep-smoothness regression
  tests

## Notes
Root cause was confirmed by inspecting the reported 46k-triangle STL (winding
consistent, no near-zero normals) — the defect was the cross-crease averaging,
not the file. B-rep rendering is intentionally unchanged. 35° is the crease
threshold matching how other CAD tools (e.g. FreeCAD) display meshes.
