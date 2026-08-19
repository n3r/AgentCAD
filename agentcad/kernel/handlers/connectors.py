"""Worker handlers for assembly mates and connector inspection.

``resolve_mates`` takes assembly items (each with script/params/position/
rotation_deg and an optional mate spec) and returns concrete transforms with
mates resolved via build123d Joints. ``connectors`` reports the named
connectors a part script declares via the optional connectors(p, part)
contract.
"""

from __future__ import annotations

import math

import build123d as b3d

from .._mates_resolver import eval_connectors, resolve_assembly, resolve_mates


def _matrix3(loc: b3d.Location):
    """The 3x3 rotation part of a build123d Location (from its gp_Trsf)."""
    trsf = loc.wrapped.Transformation()
    return [[trsf.Value(i, j) for j in range(1, 4)] for i in range(1, 4)]


def _apply_rot(loc: b3d.Location, d):
    """Rotate a direction (translation ignored) by a Location — one convention,
    done through the transformation matrix so no Vector/Location product API is
    relied on."""
    m = _matrix3(loc)
    return tuple(
        sum(m[i][k] * float(d[k]) for k in range(3)) for i in range(3))


def _rpy_from_location(loc: b3d.Location):
    """URDF roll-pitch-yaw (radians, fixed-axis XYZ: R = Rz·Ry·Rx) extracted
    from a build123d Location's rotation matrix. Kept in the kernel beside
    build123d so no second Euler implementation lives server-side."""
    r = _matrix3(loc)
    sy = -r[2][0]
    cy = math.sqrt(r[0][0] ** 2 + r[1][0] ** 2)
    if cy > 1e-9:
        roll = math.atan2(r[2][1], r[2][2])
        pitch = math.atan2(sy, cy)
        yaw = math.atan2(r[1][0], r[0][0])
    else:  # gimbal lock (pitch = ±90°)
        roll = math.atan2(-r[1][2], r[1][1])
        pitch = math.atan2(sy, cy)
        yaw = 0.0
    return [roll, pitch, yaw]


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

    def handle_mate_subassembly(params: dict) -> dict:
        """Place a whole sub-assembly UNIT by mating its exported interface
        connector to an anchor (PRD-013 FR3/FR4 interface-mate geometry).

        Reuses the ordinary ``resolve_mates`` joint machinery (DOF drivers,
        range clamping, the rigid-moving-side rule): the interface member is
        mated to the anchor as if it were a standalone instance, giving its
        world placement ``member_world``; the unit placement that carries every
        source member is then ``member_world * member_local⁻¹`` (since a member's
        world is ``unit * member_local``). One rotation convention throughout —
        build123d ``Location``.
        """
        anchor = params["anchor"]
        unit = params["unit"]
        mate = params["mate"]
        items = [
            {"id": "__anchor__", "script": anchor["script"],
             "params": anchor.get("params", {}),
             "position": anchor.get("position", [0, 0, 0]),
             "rotation_deg": anchor.get("rotation_deg", [0, 0, 0])},
            {"id": "__member__", "script": unit["script"],
             "params": unit.get("params", {}),
             "position": [0, 0, 0], "rotation_deg": [0, 0, 0],
             "mate": {"connector": unit["connector"],
                      "to_instance": "__anchor__",
                      "to_connector": mate["to_connector"],
                      "params": mate.get("params") or {}}},
        ]
        warnings: list = []
        transforms = resolve_mates(items, _build_with_ns, warnings=warnings)
        mw = transforms["__member__"]
        member_world = b3d.Location(tuple(mw["position"]),
                                    tuple(mw["rotation_deg"]))
        member_local = b3d.Location(tuple(unit["member_position"]),
                                    tuple(unit["member_rotation_deg"]))
        unit_loc = member_world * member_local.inverse()
        p, o = unit_loc.position, unit_loc.orientation
        return {"position": [p.X, p.Y, p.Z],
                "rotation_deg": [o.X, o.Y, o.Z],
                "warnings": warnings}

    def handle_connectors(params: dict) -> dict:
        shape, values, ns = _build_with_ns(params["script"], params.get("params", {}))
        specs = eval_connectors(ns, values, shape)
        return {
            "connectors": {
                name: {"type": spec["type"]} for name, spec in specs.items()
            }
        }

    def handle_urdf_frames(params: dict) -> dict:
        """Per-joint frames for URDF export (PRD-013 §6.2). For each joint,
        build the anchor part, read its named connector, and return the joint
        ``<origin>`` (child relative to parent, mm + URDF rpy) and ``<axis>``
        (the connector axis in the joint/child frame), plus the connector type
        and DOF range. All Euler/Location math stays here (one convention);
        ``core/urdf.py`` receives plain numbers.
        """
        out = []
        for j in params["joints"]:
            _shape, values, ns = _build_with_ns(
                j["anchor_script"], j.get("anchor_params", {}))
            conns = eval_connectors(ns, values, _shape)
            spec = conns.get(j["connector"])
            if spec is None:
                out.append({"name": j["name"], "type": None,
                            "error": f"no connector {j['connector']!r}"})
                continue
            parent = b3d.Location(tuple(j["parent_position"]),
                                  tuple(j["parent_rotation_deg"]))
            child = b3d.Location(tuple(j["child_position"]),
                                 tuple(j["child_rotation_deg"]))
            rel = parent.inverse() * child
            p = rel.position
            entry = {
                "name": j["name"], "type": spec["type"],
                "origin_xyz_mm": [p.X, p.Y, p.Z],
                "origin_rpy": _rpy_from_location(rel),
            }
            axis = spec.get("axis")
            if axis is not None:
                d = axis.direction
                # local axis dir -> world (rotate by the parent/anchor world
                # rotation) -> joint (child) frame (rotate by inverse child).
                parent_rot = b3d.Location((0, 0, 0),
                                          tuple(j["parent_rotation_deg"]))
                world_dir = _apply_rot(parent_rot, (d.X, d.Y, d.Z))
                inv_child = b3d.Location(
                    (0, 0, 0), tuple(j["child_rotation_deg"])).inverse()
                jd = _apply_rot(inv_child, world_dir)
                mag = math.sqrt(sum(c * c for c in jd)) or 1.0
                entry["axis"] = [c / mag for c in jd]
            for k in ("range", "linear_range", "u_range", "v_range"):
                if k in spec:
                    entry[k] = list(spec[k])
            out.append(entry)
        return {"joints": out}

    return {"resolve_mates": handle_resolve_mates,
            "resolve_assembly": handle_resolve_assembly,
            "mate_subassembly": handle_mate_subassembly,
            "urdf_frames": handle_urdf_frames,
            "connectors": handle_connectors}
