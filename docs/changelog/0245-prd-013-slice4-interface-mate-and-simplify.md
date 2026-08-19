# 0245 — 2026-08-19 — PRD-013 slice 4: interface-mate geometry + simplified_rep

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

Fourth slice of Assembly v2. Two pieces:

1. **Interface-mate geometry** — closes the MVP gap flagged in slice 2. A
   `set_mate` on a sub-assembly instance whose connector names an exported
   interface now GEOMETRICALLY resolves: the exported connector's frame is
   computed through the resolved source (the interface member's source-local
   placement composed with its connector location), the whole unit is mated to
   the anchor by reusing the ordinary `resolve_mates` joint machinery (DOF
   drivers, range clamping, the rigid-moving-side rule), and every source
   member is carried by the resulting unit placement. The engine now sits at the
   MATED pose, not its explicit transform, and `check_interference`/`sweep_motion`
   see the mated geometry.
2. **`simplified_rep`** (FR7) — a NEW proxy-mesh tier (a convex hull via
   `scipy.spatial.ConvexHull`, first use; or a coarse `decimated` re-tessellation),
   packed into the same ACM1 buffer as the LOD tiers, written as a
   `<key>.simplified.acm` sidecar, produced LAZILY on a `?lod=simplified` miss
   and served through the existing `mesh_info(lod=)` tier probe unchanged.
   DISPLAY-ONLY: it is never a metrics input — mass and interference still
   measure the real B-rep.

## Changes

- `kernel/handlers/connectors.py`: new `mate_subassembly` handler — mates the
  interface member to the anchor via `resolve_mates`, then returns
  `member_world * member_local⁻¹` as the unit placement (one `Location`
  convention).
- `core/mates.py`: `_expand_subassembly` resolves the source BEFORE placing the
  unit (the interface mate needs the resolved source's connector frame); new
  `_interface_mate_placement` marshals the anchor part + interface member to the
  kernel. Non-plain-part anchors and patterned/sub-assembly interface members
  raise a clear `ValidationError` (Phase 2).
- `kernel/handlers/simplify.py` (**new** pack): `simplify_rep {script, params,
  mode, mesh_path}` — convex hull (flat per-triangle normals from the hull's
  outward face equations, winding oriented to match; degenerate parts fall back
  to `decimated`) or coarse decimation; `atomic_write`s the sidecar.
- `core/tools_structure.py`: `_install_expansion` wraps `service.mesh_info` — on
  a `lod="simplified"` miss it issues one `simplify_rep` keyed by the part's
  cache key, writes the sidecar and serves it; content-addressed so it is
  produced once per (part, config). The sanctioned wrapper idiom, no
  `service.py`/`worker.py` edit.

## Files

- `agentcad/kernel/handlers/connectors.py`, `agentcad/core/mates.py`,
  `agentcad/kernel/handlers/simplify.py` (new), `agentcad/core/tools_structure.py`
- `tests/test_structure_interface_mate.py`, `tests/test_simplify.py` (new)

## Notes

- **Interface-mate MVP scope:** the anchor's world is its stored transform (a
  root/plain-part anchor); mating to a mated anchor, a pattern, or a
  sub-assembly is Phase 2 (raised with a clear error). The rocketry two-level
  fixture's engine mates to the stand via an interface connector and sits at the
  mated pose (the crown connector lands on the anchor face).
- **Display-only proof (negation-tested):** `test_simplified_is_display_only_...`
  asserts mass_g and the full mesh's triangle count are byte-identical before
  and after the proxy is produced, and that the full mesh path still serves the
  full mesh (`lod: None`).
- The convex hull omits edges (a proxy is display fill, not a wireframe
  reference); the `decimated` mode keeps the coarse tessellation's edges.
- Measured: `tests/test_structure_interface_mate.py` 3 passed,
  `tests/test_simplify.py` 4 passed; the mesh/config/lod regression
  (`test_mesh_lod test_mesh test_configs test_configs_assembly`) 93 passed; the
  structure/mates set (`test_structure_subassembly test_structure_patterns
  test_mates_joints test_mates`) 32 passed. Prior tree measured 4135 passed,
  1 skipped after slices 1–3 (changelog 0244).
