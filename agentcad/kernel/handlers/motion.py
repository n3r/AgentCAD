"""Worker handler pack: motion sweep over a mate's driven DOF.

``motion_sweep`` drives one mated instance's ``angle``/``position`` mate
param across a list of values. At each value it re-resolves the whole mate
graph (build123d Joints, via the same resolver the ``resolve_mates`` handler
uses), places every instance, and runs the pairwise ``&`` interference check
among placeable (non-mesh) items. Shapes are built ONCE per distinct part
across the whole sweep — only placements change per sample.

Mesh-kind items (imported STL) are excluded from the boolean check and
reported in ``skipped_mesh`` — booleans on an STL mesh Face segfault OCCT.
"""

from __future__ import annotations

import contextlib
import json
import sys

from .._mates_resolver import resolve_mates

MAX_VALUES = 60
DRIVEN_PARAMS = ("angle", "position")


def register(toolbox: dict) -> dict:
    build_shape_ns = toolbox["build_shape_ns"]
    place = toolbox["place"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def handle_motion_sweep(params: dict) -> dict:
        items = params["items"]
        driven = params.get("driven") or {}
        min_volume = float(params.get("min_volume", 0.001))

        driven_id = driven.get("instance")
        driven_param = driven.get("param")
        values = driven.get("values")
        if driven_param not in DRIVEN_PARAMS:
            raise WorkerError(
                ERROR_CONTRACT,
                f"driven.param must be one of {DRIVEN_PARAMS}",
            )
        if not isinstance(values, list) or not values or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) for v in values
        ):
            raise WorkerError(
                ERROR_CONTRACT, "driven.values must be a non-empty number list"
            )
        if len(values) > MAX_VALUES:
            raise WorkerError(
                ERROR_CONTRACT,
                f"driven.values is capped at {MAX_VALUES} samples "
                f"(got {len(values)})",
            )
        driven_item = next((i for i in items if i.get("id") == driven_id), None)
        if driven_item is None:
            raise WorkerError(
                ERROR_CONTRACT, f"driven.instance {driven_id!r} not in items"
            )
        if not driven_item.get("mate"):
            raise WorkerError(
                ERROR_CONTRACT,
                f"driven.instance {driven_id!r} has no mate to drive",
            )

        # ---- build every distinct shape once for the whole sweep ---------
        build_cache: dict[str, tuple] = {}  # content key -> (shape, values, ns)

        def cached_build(script: str, overrides: dict):
            key = "script:" + script + "\x00" + json.dumps(
                overrides or {}, sort_keys=True
            )
            if key not in build_cache:
                shape, vals, _warnings, ns = build_shape_ns(script, overrides)
                build_cache[key] = (shape, vals, ns)
            return build_cache[key]

        # Resolve each item's placeable shape up front. Mesh items (imported
        # STL) are excluded from the boolean check and reported once.
        placeable: list[tuple[str, object]] = []  # (instance id, unplaced shape)
        skipped_mesh: list[str] = []
        for item in items:
            if item.get("source"):
                from ..refload import load_reference

                shape, kind = load_reference(item["source"])
                if kind == "mesh":
                    skipped_mesh.append(item.get("id", "?"))
                    continue
            else:
                shape, _vals, _ns = cached_build(
                    item["script"], item.get("params", {})
                )
            placeable.append((item.get("id", "?"), shape))

        # ---- sweep -------------------------------------------------------
        samples = []
        frames = []
        first_collision = None
        for value in values:
            mate = dict(driven_item["mate"])
            mparams = dict(mate.get("params") or {})
            mparams[driven_param] = float(value)
            mate["params"] = mparams
            sample_items = [
                {**i, "mate": mate} if i is driven_item else i for i in items
            ]
            transforms = resolve_mates(sample_items, cached_build)
            frames.append({
                iid: {"position": t["position"], "rotation_deg": t["rotation_deg"]}
                for iid, t in transforms.items()
            })
            placed = [
                (iid, place(shape, transforms[iid]["position"],
                            transforms[iid]["rotation_deg"]))
                for iid, shape in placeable
            ]
            pairs = []
            for i in range(len(placed)):
                for j in range(i + 1, len(placed)):
                    name_a, shape_a = placed[i]
                    name_b, shape_b = placed[j]
                    # `&` returns a single Part (Shape.intersect() would
                    # return a ShapeList) — same rule as handle_interference.
                    with contextlib.redirect_stdout(sys.stderr):
                        common = shape_a & shape_b
                    volume = float(common.volume)
                    if volume > min_volume:
                        pairs.append(
                            {"a": name_a, "b": name_b, "volume_mm3": volume}
                        )
            samples.append({"value": value, "pairs": pairs})
            if pairs and first_collision is None:
                first_collision = value

        return {
            "samples": samples,
            "frames": frames,
            "clear": first_collision is None,
            "first_collision": first_collision,
            "skipped_mesh": skipped_mesh,
        }

    return {"motion_sweep": handle_motion_sweep}
