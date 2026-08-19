# 0246 — 2026-08-19 — PRD-013 slice 5: instanced rendering + rep-mode + scale

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

Fifth slice of Assembly v2: the viewport can draw an assembly at scale. A new
`THREE.InstancedMesh` path uploads ONE geometry per (part, rep-tier) group and N
per-member transforms, an instanceId-aware raycast maps a hit back to the
expanded assembly id (the click-select contract does not regress — machine-
checked in node), a Full/Simplified rep-mode toggle + HUD instance/geometry
counts, and the kernel-side scale assertion that a generated 1000-instance
project resolves — through the one expansion point — to exactly 1000 flat
members that share one mesh_key (one instanced upload).

AC3's fps (>=30 orbiting 1000 instances) is a browser/manual criterion and is
**evidence-graded, extension-gated** (the Chrome extension has been unavailable
for many sessions); it is NOT claimed here. The machine-checkable halves are the
id round-trip and the `len(flat)==1000` resolution.

## Changes

- `frontend/js/instancing.js` (**new**, pure — no THREE): `groupInstances`
  (group by (part, key), first-seen order), `buildInstanceIndex`
  (`"<groupIndex>:<localInstanceId>"` -> expanded id table), `instanceCounts`.
  Exported as `__instanceIndex__` for the node round-trip.
- `frontend/js/viewport.js`: `showAssemblyInstanced(items)` builds one
  `InstancedMesh` per group (per-instance matrix from intrinsic-XYZ Euler,
  per-instance color), tagging each mesh with its id table; `pick()` reads
  `hit.instanceId` and maps it back via that table (singleton
  `userData.instanceId` path unchanged); `assemblyCounts()` for the HUD.
- `frontend/js/main.js`: rep-mode branch in `renderAssemblyFromCache`
  (Simplified -> instanced proxy render, Full -> per-mesh editable);
  `loadSimplifiedProxies` fetches one convex-hull proxy per distinct part
  through the per-part route (producing the tier lazily); `setRepMode` toggle;
  HUD shows instance + geometry counts in assembly mode.
- `frontend/index.html`, `frontend/css/app.css`: the Full/Simplified toggle
  (`#repmode`), shown in assembly mode.
- `frontend/js/state.js`: `repMode: "full"`.

## Files

- `frontend/js/instancing.js` (new), `frontend/js/viewport.js`,
  `frontend/js/main.js`, `frontend/js/state.js`, `frontend/index.html`,
  `frontend/css/app.css`
- `tests/test_frontend_instancing.py`, `tests/test_structure_scale.py` (new)

## Notes

- **Click-select contract, machine-checked:** the InstancedMesh id mapping is
  the one property invisible to a screenshot and unreachable from Python, so
  `instancing.js` is pure and the round-trip runs in node exactly as in the
  browser (`idForInstance["0:3"] == "bolt[3]"`; namespaced
  `stand/engine/piston[0]` round-trips too).
- Per-instance selection highlight and gizmo editing stay in the Full (per-mesh)
  path; Simplified mode is display + pick. A selected instance is meant to
  re-render full-resolution (the design's "exact geometry for a selected
  instance").
- The simplified proxy is fetched per part (config-agnostic display proxy),
  produced lazily by slice 4's `mesh_info` wrapper via the per-part route.
- Measured: `tests/test_frontend_instancing.py` 3 passed,
  `tests/test_structure_scale.py` 2 passed; the combined slice-4/5 structure set
  (`test_frontend_instancing test_structure_scale test_structure_patterns
  test_structure_subassembly test_structure_interface_mate test_simplify`)
  29 passed. `node --check` clean on the four edited JS files. Prior tree 4135
  passed, 1 skipped after slices 1–3 (changelog 0244).
