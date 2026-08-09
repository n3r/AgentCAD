"""Worker handlers for assembly mates and connector inspection.

``resolve_mates`` takes assembly items (each with script/params/position/
rotation_deg and an optional mate spec) and returns concrete transforms with
mates resolved via build123d Joints. ``connectors`` reports the named
connectors a part script declares via the optional connectors(p, part)
contract.
"""

from __future__ import annotations

from .._mates_resolver import eval_connectors, resolve_mates


def register(toolbox: dict) -> dict:
    build_shape_ns = toolbox["build_shape_ns"]

    def _build_with_ns(script, params):
        shape, values, _warnings, ns = build_shape_ns(script, params)
        return shape, values, ns

    def handle_resolve_mates(params: dict) -> dict:
        items = params["items"]
        return {"transforms": resolve_mates(items, _build_with_ns)}

    def handle_connectors(params: dict) -> dict:
        shape, values, ns = _build_with_ns(params["script"], params.get("params", {}))
        specs = eval_connectors(ns, values, shape)
        return {
            "connectors": {
                name: {"type": spec["type"]} for name, spec in specs.items()
            }
        }

    return {"resolve_mates": handle_resolve_mates, "connectors": handle_connectors}
