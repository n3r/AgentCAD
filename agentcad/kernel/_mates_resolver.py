"""Prototype of the AgentCAD mate-resolution function.

Shaped so it can drop into agentcad/kernel/worker.py as a new handler
(``resolve_mates``). Input mirrors the existing assembly item payload plus an
optional ``mate`` per instance; output is concrete position/rotation_deg per
instance — exactly what the manifest, service, and frontend already consume.

Script contract addition (all optional, backwards compatible):

    def connectors(p, part) -> dict[str, dict]:
        return {
            "hole1": {"type": "rigid", "location": ((x, y, z), (rx, ry, rz))},
            "hinge": {"type": "revolute", "axis": ((px,py,pz), (dx,dy,dz)),
                       "range": (0, 180)},
            "bore":  {"type": "cylindrical", "axis": ..., "linear_range": (a,b)},
        }

``p`` is the resolved-params namespace (same one ``build`` receives) and
``part`` is the built shape, so connectors can be derived from topology
(e.g. ``part.faces().sort_by(Axis.Z)[-1].center()``). Locations are in the
part's local frame. ``location`` accepts a build123d Location, ((pos),(rot)),
or (x, y, z). ``axis`` accepts a build123d Axis or ((point),(direction)).

Manifest addition per instance (optional):

    {"id": "bolt1", "part": "bolt",
     "mate": {"connector": "head_seat",         # on THIS instance; must be rigid
              "to_instance": "plate1",          # anchor instance id
              "to_connector": "hole1",          # rigid | revolute | cylindrical
              "params": {"angle": 45.0,          # revolute/cylindrical
                          "position": 7.5}}}     # cylindrical only

Resolution semantics:
  * Instances without a mate are roots: world = Location(position, rotation_deg).
  * Mate edges form a forest (one mate per instance, cycles rejected up front);
    instances resolve in topological order, child world location is computed by
    build123d Joint.connect_to on location-proxies of the built shapes.
  * Resolved child position/rotation_deg overwrite the stored ones; roots pass
    through untouched. Output feeds the exact same _place()/frontend path.
"""

from __future__ import annotations

import build123d as b3d

from .protocol import ERROR_CONTRACT, WorkerError


VALID_TYPES = ("rigid", "revolute", "cylindrical", "slider", "planar")


# ------------------------------------------------------------ spec coercion

def _to_location(value, ctx: str) -> b3d.Location:
    if isinstance(value, b3d.Location):
        return value
    if isinstance(value, (tuple, list)):
        if len(value) == 3 and all(isinstance(v, (int, float)) for v in value):
            return b3d.Location(tuple(float(v) for v in value))
        if len(value) == 2:
            pos, rot = value
            return b3d.Location(
                tuple(float(v) for v in pos), tuple(float(v) for v in rot)
            )
    if isinstance(value, b3d.Plane):
        return value.location
    raise WorkerError(
        ERROR_CONTRACT,
        f"{ctx}: 'location' must be a Location, Plane, (x,y,z) or ((pos),(rot))",
    )


def _to_axis(value, ctx: str) -> b3d.Axis:
    if isinstance(value, b3d.Axis):
        return value
    if isinstance(value, (tuple, list)) and len(value) == 2:
        pt, d = value
        return b3d.Axis(tuple(float(v) for v in pt), tuple(float(v) for v in d))
    raise WorkerError(
        ERROR_CONTRACT, f"{ctx}: 'axis' must be an Axis or ((point),(direction))"
    )


def eval_connectors(ns: dict, values: dict, shape) -> dict[str, dict]:
    """Run the optional connectors(p, part) contract; validate the specs."""
    import types

    fn = ns.get("connectors")
    if fn is None:
        return {}
    if not callable(fn):
        raise WorkerError(ERROR_CONTRACT, "connectors must be a function(p, part)")
    specs = fn(types.SimpleNamespace(**values), shape)
    if not isinstance(specs, dict):
        raise WorkerError(ERROR_CONTRACT, "connectors(p, part) must return a dict")
    out = {}
    for name, spec in specs.items():
        if not isinstance(name, str) or not name:
            raise WorkerError(ERROR_CONTRACT, f"invalid connector name {name!r}")
        if not isinstance(spec, dict):
            raise WorkerError(ERROR_CONTRACT, f"connector {name!r} must be a dict")
        ctype = spec.get("type", "rigid")
        if ctype not in VALID_TYPES:
            raise WorkerError(
                ERROR_CONTRACT,
                f"connector {name!r}: type must be one of {VALID_TYPES}",
            )
        norm: dict = {"type": ctype}
        if ctype == "rigid":
            norm["location"] = _to_location(
                spec.get("location", ((0, 0, 0), (0, 0, 0))), f"connector {name!r}"
            )
        elif ctype == "planar":
            # No native build123d PlanarJoint — the DOF is composed as a
            # Location post-multiply on a rigid frame (see resolve_mates). The
            # connector's `location` defines the plane frame (local x/y in the
            # plane, local z the normal).
            norm["location"] = _to_location(
                spec.get("location", ((0, 0, 0), (0, 0, 0))), f"connector {name!r}"
            )
            if "u_range" in spec:
                norm["u_range"] = tuple(spec["u_range"])
            if "v_range" in spec:
                norm["v_range"] = tuple(spec["v_range"])
            norm["spin"] = bool(spec.get("spin", True))
        else:  # revolute / cylindrical / slider — axis-carried DOF
            if "axis" not in spec:
                raise WorkerError(
                    ERROR_CONTRACT, f"connector {name!r}: {ctype} needs an 'axis'"
                )
            norm["axis"] = _to_axis(spec["axis"], f"connector {name!r}")
            if "range" in spec:
                norm["range"] = tuple(spec["range"])
            if "linear_range" in spec:
                norm["linear_range"] = tuple(spec["linear_range"])
            if ctype == "slider" and "linear_range" not in norm:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"connector {name!r}: slider needs a 'linear_range'",
                )
        out[name] = norm
    return out


# -------------------------------------------------- pattern / sub-assembly

def _rigid_place(op) -> b3d.Location:
    """Compose ONE member's world Location from a pattern/sub-assembly operator.

    All in the intrinsic-XYZ-Euler build123d convention (the one AgentCAD uses
    everywhere), so orientation round-trips through ``service._apply_transform``.
    ``linear`` is a pure world translation of the base placement; ``polar`` is a
    rotation of ``angle_deg`` about ``axis`` through ``center`` applied to the
    base placement (so a member re-aims — a bolt keeps pointing radially); a
    ``rigid`` operator (sub-assembly placement) left-multiplies the parent
    placement onto the member's local placement.
    """
    base = b3d.Location(
        tuple(float(v) for v in op.get("base_position", [0, 0, 0])),
        tuple(float(v) for v in op.get("base_rotation_deg", [0, 0, 0])),
    )
    kind = op["kind"]
    if kind == "linear":
        offset = op.get("offset", [0, 0, 0])
        return b3d.Location(tuple(float(v) for v in offset)) * base
    if kind == "polar":
        (cx, cy, cz) = (float(v) for v in op["center"])
        direction = tuple(float(v) for v in op["axis"][1])
        angle = float(op["angle_deg"])
        rot = (
            b3d.Location((cx, cy, cz))
            * b3d.Location((0, 0, 0), direction, angle)
            * b3d.Location((-cx, -cy, -cz))
        )
        return rot * base
    if kind == "rigid":
        parent = b3d.Location(
            tuple(float(v) for v in op.get("parent_position", [0, 0, 0])),
            tuple(float(v) for v in op.get("parent_rotation_deg", [0, 0, 0])),
        )
        return parent * base
    raise WorkerError(ERROR_CONTRACT, f"unknown placement operator {kind!r}")


def resolve_assembly(operators: list[dict]) -> dict[str, dict]:
    """Place pattern/sub-assembly members by pure ``Location`` composition —
    no shapes built. Returns ``{id: {position, rotation_deg}}``."""
    out: dict[str, dict] = {}
    for op in operators:
        loc = _rigid_place(op)
        p, o = loc.position, loc.orientation
        out[op["id"]] = {"position": [p.X, p.Y, p.Z],
                         "rotation_deg": [o.X, o.Y, o.Z]}
    return out


# ------------------------------------------------------------- mate graph

def order_mates(items: list[dict]) -> list[dict]:
    """Validate the mate graph and return items in resolution order.

    Pure graph work — no geometry. Raises WorkerError on: duplicate ids,
    unknown to_instance, self-mates, and cycles (with the cycle path).
    """
    by_id: dict[str, dict] = {}
    for item in items:
        iid = item["id"]
        if iid in by_id:
            raise WorkerError(ERROR_CONTRACT, f"duplicate instance id {iid!r}")
        by_id[iid] = item

    dep: dict[str, str] = {}  # instance -> anchor it depends on
    for item in items:
        mate = item.get("mate")
        if not mate:
            continue
        for key in ("connector", "to_instance", "to_connector"):
            if not isinstance(mate.get(key), str) or not mate[key]:
                raise WorkerError(
                    ERROR_CONTRACT, f"instance {item['id']!r}: mate.{key} required"
                )
        target = mate["to_instance"]
        if target == item["id"]:
            raise WorkerError(
                ERROR_CONTRACT, f"instance {item['id']!r}: mate to itself"
            )
        if target not in by_id:
            raise WorkerError(
                ERROR_CONTRACT,
                f"instance {item['id']!r}: mate.to_instance {target!r} not found",
            )
        dep[item["id"]] = target

    # cycle detection: follow the single-parent chain from each node
    state: dict[str, int] = {}  # 0 unseen / 1 on-stack / 2 done
    order: list[str] = []

    def visit(node: str, stack: list[str]):
        state[node] = 1
        stack.append(node)
        parent = dep.get(node)
        if parent is not None:
            s = state.get(parent, 0)
            if s == 1:
                cycle = stack[stack.index(parent):] + [parent]
                raise WorkerError(
                    ERROR_CONTRACT,
                    "mate cycle: " + " -> ".join(cycle),
                    {"cycle": cycle},
                )
            if s == 0:
                visit(parent, stack)
        state[node] = 2
        stack.pop()
        order.append(node)  # parents land before children

    for iid in by_id:
        if state.get(iid, 0) == 0:
            visit(iid, [])
    return [by_id[i] for i in order]


# --------------------------------------------------------------- resolution

def _clamp(value: float, rng) -> float:
    lo, hi = min(rng), max(rng)
    return max(lo, min(hi, value))


def _make_joint(label: str, shape, spec: dict, location=None):
    ctype = spec["type"]
    if ctype == "rigid":
        return b3d.RigidJoint(label, shape, spec["location"])
    if ctype == "planar":
        # Composed, not native: the effective anchor frame (base location
        # post-multiplied by the DOF) is passed in as `location`.
        return b3d.RigidJoint(label, shape,
                              location if location is not None
                              else spec["location"])
    if ctype == "revolute":
        kw = {}
        if "range" in spec:
            kw["angular_range"] = spec["range"]
        return b3d.RevoluteJoint(label, shape, axis=spec["axis"], **kw)
    if ctype == "slider":
        # A pure prismatic DOF along the axis (build123d LinearJoint).
        return b3d.LinearJoint(label, shape, axis=spec["axis"],
                               linear_range=spec["linear_range"])
    kw = {}
    if "range" in spec:
        kw["angular_range"] = spec["range"]
    if "linear_range" in spec:
        kw["linear_range"] = spec["linear_range"]
    return b3d.CylindricalJoint(label, shape, axis=spec["axis"], **kw)


def resolve_mates(items: list[dict], build_shape_fn,
                  warnings: list | None = None) -> dict[str, dict]:
    """items: [{id, script, params, position, rotation_deg, mate?}]
    build_shape_fn(script, params) -> (shape, values, ns)  # worker.build_shape+ns

    Returns {instance_id: {"position": [x,y,z], "rotation_deg": [rx,ry,rz]}}
    with mates resolved and roots passed through. An out-of-range DOF value is
    **clamped** to the connector's declared range (not raised) and a
    ``dof_clamped`` record is appended to ``warnings`` when provided
    (PRD-013 FR11).
    """
    ordered = order_mates(items)

    world: dict[str, b3d.Location] = {}
    conn_cache: dict[int, dict] = {}  # id(item) is fine here; keyed per instance
    out: dict[str, dict] = {}

    def connectors_for(item):
        key = item["id"]
        if key not in conn_cache:
            shape, values, ns = build_shape_fn(item["script"], item.get("params", {}))
            conn_cache[key] = (shape, eval_connectors(ns, values, shape))
        return conn_cache[key]

    for item in ordered:
        iid = item["id"]
        mate = item.get("mate")
        if not mate:
            world[iid] = b3d.Location(
                tuple(item.get("position", [0, 0, 0])),
                tuple(item.get("rotation_deg", [0, 0, 0])),
            )
            out[iid] = {
                "position": list(item.get("position", [0, 0, 0])),
                "rotation_deg": list(item.get("rotation_deg", [0, 0, 0])),
                "mated": False,
            }
            continue

        anchor_item = next(i for i in ordered if i["id"] == mate["to_instance"])
        anchor_shape, anchor_conns = connectors_for(anchor_item)
        child_shape, child_conns = connectors_for(item)

        for cname, conns, owner in (
            (mate["to_connector"], anchor_conns, mate["to_instance"]),
            (mate["connector"], child_conns, iid),
        ):
            if cname not in conns:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"instance {owner!r}: no connector {cname!r} "
                    f"(has: {sorted(conns) or 'none'})",
                )
        child_spec = child_conns[mate["connector"]]
        if child_spec["type"] != "rigid":
            raise WorkerError(
                ERROR_CONTRACT,
                f"instance {iid!r}: moving-side connector {mate['connector']!r} "
                "must be rigid (the anchor connector carries the DOF)",
            )

        # location-proxies: fresh copies so cached shapes are never mutated
        anchor_proxy = anchor_shape.located(b3d.Location())
        child_proxy = child_shape.located(b3d.Location())
        child_joint = _make_joint("child", child_proxy, child_spec)

        mparams = dict(mate.get("params") or {})
        anchor_spec = anchor_conns[mate["to_connector"]]
        atype = anchor_spec["type"]

        def _clamped(dof: str, requested: float, rng) -> float:
            value = _clamp(requested, rng)
            if warnings is not None and abs(requested - value) > 1e-12:
                warnings.append({
                    "kind": "dof_clamped", "instance": iid, "dof": dof,
                    "requested": requested, "clamped": value,
                })
            return value

        # Planar composes its DOF into the anchor frame (no native joint);
        # every other type carries the DOF through connect_to.
        if atype == "planar":
            u = float(mparams.pop("u", 0.0))
            v = float(mparams.pop("v", 0.0))
            spin = (float(mparams.pop("spin", 0.0))
                    if anchor_spec.get("spin", True) else 0.0)
            if "u_range" in anchor_spec:
                u = _clamped("u", u, anchor_spec["u_range"])
            if "v_range" in anchor_spec:
                v = _clamped("v", v, anchor_spec["v_range"])
            eff = anchor_spec["location"] * b3d.Location(
                (u, v, 0.0), (0.0, 0.0, spin))
            anchor_joint = _make_joint("anchor", anchor_proxy, anchor_spec,
                                       location=eff)
        else:
            anchor_joint = _make_joint("anchor", anchor_proxy, anchor_spec)
        anchor_proxy.location = world[mate["to_instance"]]

        try:
            if atype in ("rigid", "planar"):
                anchor_joint.connect_to(child_joint)
            elif atype == "revolute":
                angle = float(mparams.pop("angle", 0.0))
                if "range" in anchor_spec:
                    angle = _clamped("angle", angle, anchor_spec["range"])
                anchor_joint.connect_to(child_joint, angle=angle)
            elif atype == "slider":
                pos = _clamped("position", float(mparams.pop("position", 0.0)),
                               anchor_spec["linear_range"])
                anchor_joint.connect_to(child_joint, position=pos)
            else:  # cylindrical
                pos = float(mparams.pop("position", 0.0))
                angle = float(mparams.pop("angle", 0.0))
                if "linear_range" in anchor_spec:
                    pos = _clamped("position", pos, anchor_spec["linear_range"])
                if "range" in anchor_spec:
                    angle = _clamped("angle", angle, anchor_spec["range"])
                anchor_joint.connect_to(child_joint, position=pos, angle=angle)
            if mparams:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"instance {iid!r}: unknown mate params {sorted(mparams)}",
                )
        except WorkerError:
            raise
        except Exception as exc:  # unexpected joint failures
            raise WorkerError(
                ERROR_CONTRACT, f"instance {iid!r}: mate failed: {exc}"
            ) from exc

        loc = child_proxy.location
        world[iid] = loc
        p, o = loc.position, loc.orientation
        out[iid] = {
            "position": [p.X, p.Y, p.Z],
            "rotation_deg": [o.X, o.Y, o.Z],
            "mated": True,
        }
    return out
