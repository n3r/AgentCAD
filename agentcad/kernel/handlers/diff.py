"""Worker handler pack for the geometric diff between two part versions.

``geom_diff`` builds both sides, differences them both ways (``new - old`` is
the added material, ``old - new`` the removed material) and reports the two
volumes plus, optionally, an ACM1 mesh of each so a viewer can overlay them.

Three traps this handler is written around:

* **Volumes come from ``shape_volume``, never ``.volume``** — a boolean result
  is routinely a *nested* Compound, and ``Compound.volume`` reports only the
  first child subtree.
* **Mesh-kind references (imported STL) are never booleaned** — an STL loads as
  one welded Face and an OCCT boolean on it segfaults the worker. Such a side
  is named in ``skipped_mesh`` and both volumes come back ``0.0``.
* **Every boolean is guarded** — an OCCT failure becomes a structured
  ``kernel_error`` carrying ``details.stage``, so the caller can degrade to
  "geometric diff unavailable" with its other evidence intact.

Unlike ``&`` (see ``worker.pairwise_interference``), the ``-`` operator is
correct on multi-solid Compound operands; the measurement is in
``docs/changelog/0079-geometric-diff-kernel-handler.md``.
"""

from __future__ import annotations

import struct


def register(toolbox: dict) -> dict:
    build_shape = toolbox["build_shape"]
    shape_volume = toolbox["shape_volume"]
    tessellate = toolbox["tessellate"]
    atomic_write = toolbox["atomic_write"]
    WorkerError = toolbox["WorkerError"]
    ERROR_KERNEL = toolbox["ERROR_KERNEL"]

    def _side_shape(item) -> tuple[object | None, str]:
        """Resolve one side to (shape, kind), mirroring worker._item_shape's
        script/reference split. A null side means the part is absent there."""
        if not item:
            return None, "empty"
        if item.get("source"):
            from ..refload import load_reference

            return load_reference(item["source"])
        shape, _values, _warnings = build_shape(
            item["script"], item.get("params", {}))
        return shape, "script"

    def _stage(minuend, subtrahend, path, tolerance, stage) -> tuple[float, int]:
        """Volume and optional mesh of ``minuend - subtrahend``.

        Guarded as a unit: a boolean that OCCT cannot complete must degrade the
        diff, not kill the request. A zero-volume side writes no file, so the
        caller knows there is nothing to overlay."""
        if minuend is None:
            return 0.0, 0
        try:
            result = minuend if subtrahend is None else minuend - subtrahend
            volume = shape_volume(result)
            if volume <= 0.0 or not path:
                return max(volume, 0.0), 0
            buffer = tessellate(result.wrapped, tolerance)
            atomic_write(path, buffer)
            # ACM1 header: magic(4) | nv u32 | nt u32 | ...
            (triangles,) = struct.unpack_from("<I", buffer, 8)
            return volume, int(triangles)
        except Exception as exc:  # noqa: BLE001 - any OCCT failure degrades
            raise WorkerError(
                ERROR_KERNEL,
                f"geometric diff unavailable: {exc}",
                {"stage": stage},
            ) from exc

    def handle_geom_diff(params: dict) -> dict:
        tolerance = float(params.get("tolerance", 0.1))
        old, old_kind = _side_shape(params.get("old"))
        new, new_kind = _side_shape(params.get("new"))

        result = {
            "added_mm3": 0.0,
            "removed_mm3": 0.0,
            "old_volume_mm3": shape_volume(old) if old is not None else 0.0,
            "new_volume_mm3": shape_volume(new) if new is not None else 0.0,
            "added_triangles": 0,
            "removed_triangles": 0,
        }
        skipped = [name for name, kind in (("old", old_kind), ("new", new_kind))
                   if kind == "mesh"]
        if skipped:
            # No boolean is attempted: an STL operand segfaults OCCT. The
            # added/removed volumes stay 0.0 and the caller marks the diff
            # unavailable; the per-side volumes are still reported.
            result["skipped_mesh"] = skipped
            return result

        result["added_mm3"], result["added_triangles"] = _stage(
            new, old, params.get("added_path"), tolerance, "added")
        result["removed_mm3"], result["removed_triangles"] = _stage(
            old, new, params.get("removed_path"), tolerance, "removed")
        return result

    return {"geom_diff": handle_geom_diff}
