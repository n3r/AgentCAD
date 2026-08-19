"""Worker handlers for assembly mates and connector inspection.

``resolve_mates`` takes assembly items (each with script/params/position/
rotation_deg and an optional mate spec) and returns concrete transforms with
mates resolved via build123d Joints. ``connectors`` reports the named
connectors a part script declares via the optional connectors(p, part)
contract.
"""

from __future__ import annotations

import build123d as b3d

from .._mates_resolver import eval_connectors, resolve_assembly, resolve_mates


def register(toolbox: dict) -> dict:
    build_shape_ns = toolbox["build_shape_ns"]

    def _build_with_ns(script, params):
        shape, values, _warnings, ns = build_shape_ns(script, params)
        return shape, values, ns

    def handle_resolve_mates(params: dict) -> dict:
        items = params["items"]
        warnings: list = []
        transforms = resolve_mates(items, _build_with_ns, warnings=warnings)
        return {"transforms": transforms, "warnings": warnings}

    def handle_resolve_assembly(params: dict) -> dict:
        # Shape-free: pattern/sub-assembly members are placed by composing
        # build123d ``Location``s, never by building geometry (a 1000-member
        # bolt strip is 1000 µs-cheap Locations, one mesh via the cache path).
        return {"transforms": resolve_assembly(params["operators"])}

    def handle_connectors(params: dict) -> dict:
        shape, values, ns = _build_with_ns(params["script"], params.get("params", {}))
        specs = eval_connectors(ns, values, shape)
        return {
            "connectors": {
                name: {"type": spec["type"]} for name, spec in specs.items()
            }
        }

    return {"resolve_mates": handle_resolve_mates,
            "resolve_assembly": handle_resolve_assembly,
            "connectors": handle_connectors}
