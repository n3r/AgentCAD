# 0048 — Mesh streaming: LOD tiers + progressive viewport loading

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Large meshes now stream progressively (roadmap "Mesh streaming for huge
parts"): builds above 150k triangles write an additional coarse ACM1 sidecar
(`<key>.lod1.acm`, tolerance 0.8), the mesh route serves it on `?lod=lod1`,
and the viewport shows the coarse tier instantly while the full mesh loads
in the background. ACM1 itself stays frozen and full-resolution bytes stay
pinned byte-deterministic.

## Changes

- **Worker**: `_write_lod_tiers` — reads the full mesh's triangle count from
  the ACM1 header, and when `lod_tolerances`/`lod_min_triangles` warrant it,
  re-tessellates per tier (with `BRepTools.Clean_s` between tiers — OCCT
  otherwise reuses the finer triangulation and would emit a byte-copy) and
  writes each sidecar atomically. Build results gain `"triangles"` and
  `"lods"`. Exposed via `WORKER_TOOLBOX["write_lod_tiers"]`; `build_reference`
  uses it too — STL (mesh-kind) imports are excluded (their triangulation IS
  their geometry).
- **Service**: `MESH_LOD_TOLERANCE = 0.8`, `LOD_TRIANGLE_THRESHOLD =
  150_000`; every build requests the tier; `lods` round-trips through the
  metrics sidecar; `mesh_info(..., lod=)` returns the tier path when present
  (suffix validated `^[a-z][a-z0-9_]{0,15}$` — the param lands in a filename,
  traversal-tested).
- **Route**: `?lod=` param + `X-Mesh-Lod` header; full-resolution serving
  byte-identical to before.
- **Frontend**: `reloadMesh` requests lod1 first, renders immediately, and
  swaps in the full buffer in the background under the existing staleness
  guards; geometry cache keys become `${key}:${lod}` composites (passed from
  main.js — viewport internals unchanged).

## Files

- `agentcad/kernel/worker.py`, `agentcad/kernel/handlers/reference.py`
- `agentcad/core/service.py`, `agentcad/server/app.py`
- `frontend/js/api.js`, `frontend/js/main.js`, `frontend/js/viewport.js`
- `tests/test_mesh_lod.py` — 12 tests: tier writing/threshold/determinism,
  STEP-reference tier, STL skip, sidecar round-trip, route fallbacks,
  traversal guard
- `docs/architecture.md`, `docs/user-guide.md`

## Notes

LOD params are deliberately not in the cache key: pre-existing cached builds
serve `lods: []` (full-mesh fallback) until content changes — correct by
fallback, no invalidation churn. Tier files share the key prefix and age out
with it.
