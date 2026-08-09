# 0053 — GUI sketching & push/pull (script-as-source-of-truth)

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

The roadmap's biggest UX item: an interactive 2D sketcher over the
first-party constraint solver, and face push/pull — both emitting *code*, so
direct manipulation never forks the model away from its script.

## Changes

- **Face identity**: `tessellate_with_faces` returns the byte-identical ACM1
  buffer plus a u32-per-triangle B-rep face map; `handle_build` writes it as
  a `<key>.faces.u32` sidecar; served at `GET .../mesh/faces`. Byte identity
  of `tessellate` proven against pre-change baselines and pinned by a test.
- **Viewport picking**: `pick()` returns `faceIndex` (mapped inside the
  viewport from the sidecar stored on the geometry-cache entry);
  `highlightFace` renders an amber overlay in the scene root. Picking is
  live once the full-resolution mesh is on stage (LOD tier and reference
  parts excluded).
- **Push/pull**: `toolkit/facemod.py` (`faces_in_mesh_order` — the single
  source of truth for face indexing — and `push_face(part, i, distance)`;
  planar faces only, negative distances cut inward), a `face_info` kernel
  handler + tool, and a `push_pull` tool that APPENDS a marker-commented
  wrapper (`_agentcad_prev_build_N` + new `build`) to the script and
  rebuilds through the normal path — composable, visible, revertible (and
  snapshotted by project history like any edit). Verified: a 5 mm push of a
  20-box face → volume exactly 10 000 mm³; chained edits compose.
- **Sketcher** (`frontend/js/sketcher.js` + toolbar ✏️): draw
  points/lines/circles, constraint palette (distance, horizontal, vertical,
  parallel, perpendicular, radius, coincident, fix), live solve through
  `/api/sketch/solve` with solved/DOF status, constraint chips, and "Insert
  into script" emitting a `sketch_profile()` build123d function (Polyline
  chains closed within 1e-6; circles via `Locations`) into the code editor.
- Fixed the long-standing `docs/part-authoring.md` sketch example bug (the
  constraint kwargs are `ln`/`p`/`q`, not `line`/`p1`/`p2`).

## Files

- `agentcad/kernel/mesh.py`, `agentcad/kernel/worker.py`,
  `agentcad/kernel/handlers/facemod.py`, `agentcad/toolkit/facemod.py`,
  `agentcad/toolkit/__init__.py`, `agentcad/core/tools_facemod.py`,
  `agentcad/server/app.py`
- `frontend/js/sketcher.js`, `frontend/js/{main,viewport,api,editor}.js`,
  `frontend/index.html`, `frontend/css/app.css`
- `tests/test_facemod.py` — ordering contract (sidecar normals vs face_info),
  push/pull volumes ±, composition, route bytes
- `docs/agent-api.md`, `docs/user-guide.md`, `docs/part-authoring.md`

## Notes

Parts built before this change have no faces sidecar until their next real
rebuild (cache-key format deliberately unchanged) — picking is silently off
for them. The push/pull wrapper resolves the roadmap's scale-stays-parametric
tension explicitly: the direct edit *is* a script edit.
