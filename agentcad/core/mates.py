"""Server-side mate resolution — the seam AgentCADService._resolved_instances
calls when any instance carries a ``mate`` spec.

Geometry (build123d Joints) lives in the worker; this module marshals the
instance list to the worker's ``resolve_mates`` handler and writes the
resulting concrete transforms back onto copies of the instances, so the rest
of the service and the frontend see ordinary position/rotation_deg.

The one request it makes takes an optional ``timeout_s`` for the same reason
``check_interference`` does: a caller under a wall-clock deadline must not have
this pass outlive its whole budget.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

from ..kernel.client import KernelError
from .model import InstanceSpec, ValidationError


#: Ceiling for one ``resolve_mates`` round trip. A *caller working under a
#: deadline* (PRD-003's spec gate budget) passes a smaller ``timeout_s``; it is
#: clamped here so no caller can ask for more than the flat ceiling.
RESOLVE_TIMEOUT_S = 120.0


# ============================================================ expansion
#
# PRD-013 Assembly v2. `expand` is the SINGLE point where a pattern (repeat a
# part N times) or a sub-assembly (instance another project) is flattened into
# concrete instances. `_resolved_instances` (via the tools_structure wrapper)
# runs `expand` THEN the mate pass, so every consumer — mass roll-up,
# interference, export, stackup, specs, checks, the packet — reads N members
# from one place and can never double- or under-count.
#
# The load-bearing invariant: expansion REPLACES a patterned base id `<id>`
# with `<id>[0..count-1]` and NEVER emits the base alongside its members; a
# member is never itself re-expanded. Geometry (polar re-aim, sub-assembly
# rigid placement) is composed in the kernel via build123d `Location`, the one
# rotation convention — the server never implements a second Euler.


def _member(base: InstanceSpec, index: int, position, rotation_deg):
    """One concrete pattern/sub-assembly member: id `<base>[i]`, inheriting the
    base's part/color/config/folder/mate template, at a composed transform.
    `pattern` and `assembly` are cleared — a member is a leaf, never
    re-expanded."""
    return InstanceSpec(
        id=f"{base.id}[{index}]",
        part=base.part,
        position=[float(v) for v in position],
        rotation_deg=[float(v) for v in rotation_deg],
        color=base.color,
        mate=copy.deepcopy(base.mate) if base.mate else None,
        config=base.config,
        # PRD-027: a member is filed where its base is, so the assembly tree
        # shows an expanded pattern under the folder the author chose.
        folder=base.folder,
    )


def _unit(vec):
    length = math.sqrt(sum(float(c) ** 2 for c in vec))
    if length < 1e-12:
        return (1.0, 0.0, 0.0)
    return tuple(float(c) / length for c in vec)


def resolve_project(service, proj: str, timeout_s: float | None = None,
                    _stack: list | None = None):
    """Fully resolve a project's assembly into a flat, world-placed instance
    list: expand patterns + sub-assemblies, then run the mate pass over the
    project's OWN instances. Returns ``(flat_instances, warnings)``.

    This is the recursion primitive: a sub-assembly resolves its source with
    exactly this call, one stack level deeper (cross-project cycle detection
    threads the ``_stack``). The ``tools_structure`` wrapper on
    ``_resolved_instances`` is a thin adapter over it.
    """
    stack = _stack or [(proj, str(service.store.canonical_path_of(proj)))]
    instances = service.store.instances(proj)
    if not any((i.mate or i.pattern or i.assembly) for i in instances):
        return instances, []
    warnings: list[dict] = []
    flat = expand(service, proj, instances, timeout_s, stack)
    warnings.extend(flat[1])
    flat = flat[0]
    # The mate pass runs only over this project's OWN (native) instances — a
    # sub-assembly member is already world-final in its parent's frame and
    # carries no parent-level mate. Skipping it when nothing is mated preserves
    # the "an unmated 1000-member pattern builds no shapes" property.
    native = [m for m in flat if m.origin_project is None]
    if any(m.mate for m in native):
        resolved = {m.id: m for m in resolve(service, proj, native, timeout_s,
                                             warnings_out=warnings)}
        flat = [resolved.get(m.id, m) for m in flat]
    return flat, warnings


def expand(service, proj: str, instances: list, timeout_s: float | None = None,
           stack: list | None = None):
    """Flatten patterns and sub-assemblies into a concrete instance list.

    Returns ``(flat_instances, warnings)``. Linear patterns compose entirely
    server-side (pure translation); polar patterns (a per-member rotation about
    an axis) and sub-assembly rigid placement are composed in the kernel via
    ``Location`` in ONE ``resolve_assembly`` round trip.
    """
    if stack is None:
        stack = [(proj, str(service.store.canonical_path_of(proj)))]
    flat: list[InstanceSpec] = []
    warnings: list[dict] = []
    # Members whose transform needs kernel Location composition, collected so a
    # whole assembly's polar / sub-assembly members ride ONE round trip.
    ops: list[dict] = []
    op_targets: dict[str, InstanceSpec] = {}

    for inst in instances:
        if inst.assembly is not None:
            _expand_subassembly(service, proj, inst, timeout_s,
                                 flat, warnings, ops, op_targets, stack)
            continue
        pattern = inst.pattern
        if pattern is None:
            flat.append(inst)
            continue
        kind = pattern["kind"]
        count = int(pattern["count"])
        if kind == "linear":
            unit = _unit(pattern.get("axis", [[0, 0, 0], [1, 0, 0]])[1]
                         if pattern.get("axis") else [1, 0, 0])
            step = float(pattern["step_mm"])
            for i in range(count):
                pos = [inst.position[a] + i * step * unit[a] for a in range(3)]
                flat.append(_member(inst, i, pos, inst.rotation_deg))
        elif kind == "polar":
            axis = pattern.get("axis") or [[0, 0, 0], [0, 0, 1]]
            center = pattern.get("center") or axis[0]
            angle_step = float(pattern["angle_step_deg"])
            if inst.mate:
                # An anchored polar base has ONE anchor connector; we cannot
                # re-solve the mate per member, so members fall back to the
                # rigid polar image and we say so (spec §2.4).
                warnings.append({"kind": "pattern_polar_offaxis",
                                 "instance": inst.id})
            for i in range(count):
                member = _member(inst, i, inst.position, inst.rotation_deg)
                flat.append(member)
                ops.append({
                    "id": member.id, "kind": "polar",
                    "base_position": list(inst.position),
                    "base_rotation_deg": list(inst.rotation_deg),
                    "angle_deg": i * angle_step,
                    "axis": [list(axis[0]), list(axis[1])],
                    "center": list(center),
                })
                op_targets[member.id] = member
        else:  # pragma: no cover — set_instances validates kind first
            raise ValidationError(f"unknown pattern kind {kind!r}")

    if ops:
        result = service.kernel.request(
            "resolve_assembly", {"operators": ops},
            timeout_s=RESOLVE_TIMEOUT_S if timeout_s is None
            else min(RESOLVE_TIMEOUT_S, timeout_s),
        )
        for oid, t in result["transforms"].items():
            member = op_targets[oid]
            member.position = [float(v) for v in t["position"]]
            member.rotation_deg = [float(v) for v in t["rotation_deg"]]
    return flat, warnings


def _source_name(service, ref: str) -> str:
    """Resolve a sub-assembly reference (a known project NAME or an absolute
    path) to a project name, opening an external directory READ-ONLY. ``open``
    installs no write hook — only read accessors are ever used on a source."""
    from ..kernel.client import KernelError  # noqa: F401 (parity import)
    from .model import NotFoundError

    try:
        service.store.manifest(ref)          # a known name resolves directly
        return ref
    except NotFoundError:
        return service.store.open(ref)       # register an external path, read-only


def _interface_mate_placement(service, proj, inst, source, iface, sub_flat,
                              timeout_s, warnings):
    """Compute a sub-assembly unit's world placement by mating its exported
    interface connector to the anchor (PRD-013 FR3/FR4).

    The exported connector resolves to an internal ``(instance, connector)`` in
    the source; that member's source-local placement (``member_local``, already
    resolved incl. the source's own mates) plus its connector frame define the
    interface frame. The kernel mates the interface member to the anchor part
    (reusing ``resolve_mates`` — DOF drivers, clamping, the rigid rule) and
    returns the unit placement that carries every source member. Returns
    ``(position, rotation_deg)``.
    """
    member = next((m for m in sub_flat if m.id == iface["instance"]), None)
    if member is None:
        raise ValidationError(
            f"instance {inst.id!r}: interface names source instance "
            f"{iface['instance']!r}, which is not a resolvable member of "
            f"{source!r} (patterned/sub-assembly interface members are Phase 2)",
            {"interface": inst.mate.get("connector")},
        )
    member_owner = getattr(member, "origin_project", None) or source
    member_record = service._record_for(member_owner, member.part, member.config)
    if member_record.kind != "script":
        raise ValidationError(
            f"instance {inst.id!r}: interface member {iface['instance']!r} is a "
            "reference/imported part and has no connectors")

    to_id = inst.mate.get("to_instance")
    anchor_inst = next(
        (i for i in service.store.instances(proj) if i.id == to_id), None)
    if anchor_inst is None:
        raise ValidationError(
            f"instance {inst.id!r}: mate.to_instance {to_id!r} not found")
    if (anchor_inst.assembly is not None or anchor_inst.pattern is not None
            or not anchor_inst.part):
        raise ValidationError(
            f"instance {inst.id!r}: an interface mate anchor must be a plain "
            f"part instance (got {to_id!r}); mating to a pattern or "
            "sub-assembly is Phase 2")
    anchor_record = service._record_for(proj, anchor_inst.part,
                                        anchor_inst.config)
    if anchor_record.kind != "script":
        raise ValidationError(
            f"instance {inst.id!r}: anchor {to_id!r} is a reference/imported "
            "part and has no connectors")

    payload = {
        "anchor": {
            "script": service.store.read_script(proj, anchor_inst.part),
            "params": anchor_record.effective_params,
            "position": list(anchor_inst.position),
            "rotation_deg": list(anchor_inst.rotation_deg),
        },
        "unit": {
            "script": service.store.read_script(member_owner, member.part),
            "params": member_record.effective_params,
            "connector": iface["connector"],
            "member_position": list(member.position),
            "member_rotation_deg": list(member.rotation_deg),
        },
        "mate": {
            "to_connector": inst.mate.get("to_connector"),
            "params": inst.mate.get("params") or {},
        },
    }
    try:
        result = service.kernel.request(
            "mate_subassembly", payload,
            timeout_s=RESOLVE_TIMEOUT_S if timeout_s is None
            else min(RESOLVE_TIMEOUT_S, timeout_s),
        )
    except KernelError as exc:
        raise ValidationError(
            f"instance {inst.id!r}: interface mate failed: {exc.message}",
            exc.details) from exc
    warnings.extend(result.get("warnings") or [])
    return result["position"], result["rotation_deg"]


def _expand_subassembly(service, proj, inst, timeout_s, flat, warnings, ops,
                        op_targets, stack):
    """Depth-first, READ-ONLY sub-assembly resolution (PRD-013 Decision 3).

    Opens the source read-only, recurses to resolve its own structure into
    source-local members, rigid-places each at ``parent * member_local`` (kernel
    ``Location``), and namespaces ids ``<parent>/<child>`` so two nesting levels
    read ``stand/engine/piston[0]``. ``write_guard`` is structurally unreachable
    — only read accessors touch the source (a store-spy asserts zero authored
    writes).
    """
    ref = inst.assembly.get("project")
    source = _source_name(service, ref)
    spath = str(service.store.canonical_path_of(source))

    # Cross-project cycle: identity is the canonical path; the payload names the
    # readable chain (mirrors the intra-project mate-cycle payload).
    if any(p == spath for _, p in stack):
        names = [n for n, _ in stack] + [source]
        raise ValidationError("assembly cycle: " + " -> ".join(names),
                              {"cycle": names})

    # Interface: only exported connectors are matable from outside. A mate on a
    # sub-assembly instance must name one, or it is unreachable by construction.
    iface = None
    if inst.mate:
        exported = service.store.assembly_interface(source)
        cname = inst.mate.get("connector")
        if cname not in exported:
            raise ValidationError(
                f"instance {inst.id!r}: connector {cname!r} is not an exported "
                f"interface of sub-assembly {source!r} "
                f"(exports: {sorted(exported) or 'none'})",
                {"interface": cname},
            )
        iface = exported[cname]

    # Recurse: the source's members in ITS OWN local frame (read-only). Done
    # BEFORE the interface mate — the mate needs the exported connector's frame
    # in the resolved source's local coordinates.
    sub_flat, sub_warns = resolve_project(
        service, source, timeout_s, stack + [(source, spath)])
    warnings.extend(sub_warns)

    # Parent placement of the whole unit. An interface mate resolves the unit's
    # pose geometrically (the exported connector mated to the anchor, FR3/FR4);
    # otherwise the instance's explicit transform.
    if iface is not None:
        parent_pos, parent_rot = _interface_mate_placement(
            service, proj, inst, source, iface, sub_flat, timeout_s, warnings)
    else:
        parent_pos = list(inst.position)
        parent_rot = list(inst.rotation_deg)

    for m in sub_flat:
        child = copy.copy(m)
        child.id = f"{inst.id}/{m.id}"
        # A member built from a deeper source keeps that source; a leaf member
        # of THIS source is built from `source`.
        child.origin_project = m.origin_project or source
        flat.append(child)
        ops.append({
            "id": child.id, "kind": "rigid",
            "base_position": list(m.position),
            "base_rotation_deg": list(m.rotation_deg),
            "parent_position": parent_pos,
            "parent_rotation_deg": parent_rot,
        })
        op_targets[child.id] = child


def resolve(service, proj: str, instances: list,
            timeout_s: float | None = None, warnings_out: list | None = None):
    ids = {inst.id for inst in instances}
    kinds = {inst.id: service.store.get_part(proj, inst.part).kind for inst in instances}
    items = []
    for inst in instances:
        # An instance bound to a configuration resolves its connectors from
        # THAT configuration's geometry (a connector position routinely rides a
        # parameter), so the derived record is what feeds the item below. The
        # kernel's `conn_cache` is keyed by instance id, so two instances of one
        # part at two sizes never share a cached connector frame.
        record = service._record_for(proj, inst.part, inst.config)
        # Reference/imported parts declare no connectors and cannot mate; give a
        # clear error instead of a cryptic KeyError deep in the resolver.
        if inst.mate:
            if record.kind != "script":
                raise ValidationError(
                    f"instance {inst.id!r}: reference/imported parts have no "
                    "connectors and cannot be mated"
                )
            target = inst.mate.get("to_instance")
            if target not in ids:
                raise ValidationError(
                    f"instance {inst.id!r}: mate.to_instance {target!r} not found"
                )
            if kinds.get(target) != "script":
                raise ValidationError(
                    f"instance {inst.id!r}: cannot mate to reference/imported "
                    f"instance {target!r} (it has no connectors)"
                )
        item = {
            "id": inst.id,
            "position": list(inst.position),
            "rotation_deg": list(inst.rotation_deg),
        }
        if inst.mate:
            item["mate"] = inst.mate
        # Scripts carry connectors; reference parts have none (script omitted).
        if record.kind == "script":
            item["script"] = service.store.read_script(proj, inst.part)
            item["params"] = record.effective_params
        items.append(item)

    try:
        result = service.kernel.request(
            "resolve_mates", {"items": items},
            timeout_s=RESOLVE_TIMEOUT_S if timeout_s is None
            else min(RESOLVE_TIMEOUT_S, timeout_s),
        )
    except KernelError as exc:
        # Surface mate errors (unknown connector, cycle, range) as validation.
        raise ValidationError(f"mate resolution failed: {exc.message}",
                              exc.details) from exc

    transforms = result["transforms"]
    if warnings_out is not None:
        warnings_out.extend(result.get("warnings") or [])
    resolved = []
    for inst in instances:
        t = transforms.get(inst.id)
        new = copy.copy(inst)
        if t:
            new.position = [float(v) for v in t["position"]]
            new.rotation_deg = [float(v) for v in t["rotation_deg"]]
        resolved.append(new)
    return resolved
