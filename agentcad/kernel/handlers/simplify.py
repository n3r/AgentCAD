"""Worker handler pack: ``simplify_rep`` — proxy meshes for instanced display.

PRD-013 Decision 4. A ``simplified_rep`` is a NEW build kind, not a coarser LOD
tolerance: a convex hull (dozens of triangles, a great instancing proxy) or a
coarse decimation (a large-deflection re-tessellation, for thin/hollow parts a
hull would swallow). It is packed into the same ACM1 buffer as every other tier
and written as a ``<key>.simplified.acm`` sidecar, so it flows through the
existing ``mesh_info(lod=)`` tier mechanism with no route change.

DISPLAY-ONLY: this never touches metrics. Mass, interference and every kernel
measurement still run on the real B-rep — the proxy is only what the viewport
uploads once per (part, tier) for ``THREE.InstancedMesh``.

Separate from ``worker._write_lod_tiers`` (a tolerance re-tessellation in the
worker core we may not edit): a hull is a different construction and rides its
own kernel call, produced lazily and content-addressed (one hull per distinct
(part, config), so a 1000-instance pattern is one hull).
"""

from __future__ import annotations

import numpy as np

from .. import acm


# A coarse deflection for the ``decimated`` mode — large enough to collapse most
# curvature detail while keeping the rough silhouette.
_DECIMATE_TOLERANCE = 2.0


def _convex_hull_buffer(positions: np.ndarray) -> bytes:
    """Pack the convex hull of a vertex cloud as a flat-shaded ACM1 buffer.

    Flat faces → per-triangle normals (no vertex sharing), taken from the hull's
    own outward face equations, with winding oriented to match. Edges are
    omitted (a proxy is display fill, not a wireframe reference)."""
    from scipy.spatial import ConvexHull  # first ConvexHull use (scipy>=1.14)

    pts = np.ascontiguousarray(positions, dtype=np.float64).reshape(-1, 3)
    if len(pts) < 4:
        raise ValueError("convex hull needs at least 4 non-coplanar vertices")
    hull = ConvexHull(pts)

    out_pos = np.empty((len(hull.simplices) * 3, 3), dtype=np.float64)
    out_nrm = np.empty((len(hull.simplices) * 3, 3), dtype=np.float64)
    for t, (simplex, equation) in enumerate(zip(hull.simplices, hull.equations)):
        tri = pts[simplex]
        normal = equation[:3]  # outward unit normal from the hull equation
        # Orient the winding so the triangle's own face normal points outward.
        edge_normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        if float(edge_normal @ normal) < 0.0:
            tri = tri[::-1]
        out_pos[t * 3:t * 3 + 3] = tri
        out_nrm[t * 3:t * 3 + 3] = normal

    indices = np.arange(len(out_pos), dtype=np.int64).reshape(-1, 3)
    empty_lengths = np.zeros(0, dtype="<u4")
    empty_points = np.zeros((0, 3), dtype="<f4")
    return acm.pack(out_pos, out_nrm, indices, empty_lengths, empty_points)


def register(toolbox: dict) -> dict:
    build_shape = toolbox["build_shape"]
    tessellate = toolbox["tessellate"]
    atomic_write = toolbox["atomic_write"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def simplify_rep(params: dict) -> dict:
        shape, _values, _warnings = build_shape(
            params["script"], params.get("params", {}))
        mode = params.get("mode", "convex")
        if mode == "convex":
            # Hull of the full tessellation's vertices — deterministic, and the
            # tessellation is the same one the cache already builds for the part.
            full = acm.parse(tessellate(shape.wrapped, 0.1))
            try:
                buffer = _convex_hull_buffer(full["positions"])
            except Exception as exc:  # degenerate/thin part → fall back coarse
                buffer = tessellate(shape.wrapped, _DECIMATE_TOLERANCE)
                mode = "decimated"
                _ = exc
        elif mode == "decimated":
            buffer = tessellate(shape.wrapped, _DECIMATE_TOLERANCE)
        else:
            raise WorkerError(
                ERROR_CONTRACT,
                f"simplify_rep: mode must be 'convex' or 'decimated' (got "
                f"{mode!r})")
        atomic_write(params["mesh_path"], buffer)
        triangles = int(len(acm.parse(buffer)["indices"]))
        return {"path": params["mesh_path"], "mode": mode,
                "triangles": triangles}

    return {"simplify_rep": simplify_rep}
