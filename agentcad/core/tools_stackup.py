"""Tool pack: 1-D tolerance stack-up analysis along an assembly mate chain.

``tolerance_stackup`` walks the unique path between two instances through the
mate forest (each instance's mate is an edge to its anchor; roots have none):
up from ``from_instance`` to the lowest common ancestor, then down to
``to_instance``, endpoints included. Every instance on that path contributes
its part's linear PMI dims (set via ``set_part_pmi``) whose target matches the
requested world axis — x -> "width", y -> "depth", z -> "height". Totals are
accumulated two ways: worst case (linear sum of plus and of minus) and RSS
(root sum of squares over the individual dims). The nominal distance between
the endpoints comes from the mate-resolved assembly transforms.
"""

from __future__ import annotations

import math

from .model import NotFoundError, ValidationError
from .tools import Tool, schema

# world axis -> (position index, matching linear PMI target)
AXIS_TARGETS = {"x": (0, "width"), "y": (1, "depth"), "z": (2, "height")}


def _chain_to_root(by_id: dict, start: str) -> list[str]:
    """Instance ids from ``start`` up its mate chain to the root, inclusive.

    The store rejects cycles and dangling anchors on write; guard anyway so a
    hand-edited manifest yields a clean error instead of an infinite loop.
    """
    path = [start]
    seen = {start}
    current = by_id[start]
    while current.mate:
        parent = current.mate.get("to_instance")
        if parent in seen:
            raise ValidationError(
                f"mate cycle detected at instance {parent!r}", {"chain": path})
        if parent not in by_id:
            raise ValidationError(
                f"instance {current.id!r}: mate.to_instance {parent!r} not found")
        path.append(parent)
        seen.add(parent)
        current = by_id[parent]
    return path


def compute_stackup(service, project: str, axis: str, from_instance: str,
                    to_instance: str) -> dict:
    """The stack-up math, callable without the tool registry.

    Module-level so ``check_stackup`` (PRD-003's project tier) can reach it
    directly: ``tools_specs`` sorts *before* ``tools_stackup`` in the pack
    walk, so ``registry.call("tolerance_stackup", …)`` would not yet resolve —
    and a check has no business depending on a tool's registration order
    anyway. The tool below is a thin call through, so the two can never drift.
    """
    if axis not in AXIS_TARGETS:
        raise ValidationError(
            "axis must be one of: x, y, z",
            {"known": sorted(AXIS_TARGETS)})
    index, target = AXIS_TARGETS[axis]

    instances = service.store.instances(project)
    by_id = {inst.id: inst for inst in instances}
    for iid in (from_instance, to_instance):
        if iid not in by_id:
            raise NotFoundError(
                f"instance {iid!r} not found in project {project!r}")

    from_chain = _chain_to_root(by_id, from_instance)
    to_chain = _chain_to_root(by_id, to_instance)
    to_set = set(to_chain)
    ancestor = next((iid for iid in from_chain if iid in to_set), None)
    if ancestor is None:
        raise ValidationError(
            "instances are not connected by mates",
            {"from_chain": from_chain, "to_chain": to_chain})
    # up to the common ancestor, then down the other branch
    path = (from_chain[: from_chain.index(ancestor) + 1]
            + list(reversed(to_chain[: to_chain.index(ancestor)])))

    parts = {p["id"]: p for p in service.store.manifest(project)["parts"]}
    contributors: list[dict] = []
    warnings: list[str] = []
    worst = {"plus": 0.0, "minus": 0.0}
    squares = {"plus": 0.0, "minus": 0.0}
    for iid in path:
        part_id = by_id[iid].part
        pmi = parts[part_id].get("pmi") or {}
        dims = [
            {"id": d["id"], "plus": d["plus"], "minus": d["minus"]}
            for d in pmi.get("dims", [])
            if d["kind"] == "linear" and d["target"] == target
        ]
        if not dims:
            warnings.append(
                f"instance {iid} (part {part_id}) has no {target} tolerance")
        plus = sum(d["plus"] for d in dims)
        minus = sum(d["minus"] for d in dims)
        worst["plus"] += plus
        worst["minus"] += minus
        for d in dims:
            squares["plus"] += d["plus"] ** 2
            squares["minus"] += d["minus"] ** 2
        contributors.append({"instance": iid, "part": part_id,
                             "dims": dims, "plus": plus, "minus": minus})

    resolved = {inst.id: inst
                for inst in service._resolved_instances(project)}
    nominal = abs(resolved[to_instance].position[index]
                  - resolved[from_instance].position[index])

    return {
        "axis": axis,
        "target": target,
        "nominal_mm": nominal,
        "worst_case": worst,
        "rss": {"plus": math.sqrt(squares["plus"]),
                "minus": math.sqrt(squares["minus"])},
        "contributors": contributors,
        "path": path,
        "warnings": warnings,
    }


def register(registry, service) -> None:
    def tolerance_stackup(project: str, axis: str, from_instance: str,
                          to_instance: str) -> dict:
        return compute_stackup(service, project, axis, from_instance,
                               to_instance)

    registry.register(Tool(
        "tolerance_stackup",
        "1-D tolerance stack-up between two assembly instances along a world "
        "axis. The stack path is the unique route between them through the "
        "mate forest, endpoints included (from == to analyzes just that one "
        "instance's own dims). Each path instance contributes its part's "
        "linear PMI dims whose target matches the axis (x=width, y=depth, "
        "z=height; declared via set_part_pmi). Returns worst-case (linear sum) "
        "and RSS plus/minus totals, the nominal resolved distance along the "
        "axis (mm), per-instance contributors in path order, and a warning for "
        "each path instance with no matching tolerance. Errors if the "
        "instances are not connected by mates.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "axis": {"type": "string", "description": "x | y | z"},
                "from_instance": {"type": "string",
                                  "description": "Stack start instance id"},
                "to_instance": {"type": "string",
                                "description": "Stack end instance id"},
            },
            ["project", "axis", "from_instance", "to_instance"],
        ),
        tolerance_stackup,
    ))
