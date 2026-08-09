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


VALID_TYPES = ("rigid", "revolute", "cylindrical")


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
        else:
            if "axis" not in spec:
                raise WorkerError(
                    ERROR_CONTRACT, f"connector {name!r}: {ctype} needs an 'axis'"
                )
            norm["axis"] = _to_axis(spec["axis"], f"connector {name!r}")
            if "range" in spec:
                norm["range"] = tuple(spec["range"])
            if "linear_range" in spec:
                norm["linear_range"] = tuple(spec["linear_range"])
        out[name] = norm
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

def _make_joint(label: str, shape, spec: dict):
    if spec["type"] == "rigid":
        return b3d.RigidJoint(label, shape, spec["location"])
    if spec["type"] == "revolute":
        kw = {}
        if "range" in spec:
            kw["angular_range"] = spec["range"]
        return b3d.RevoluteJoint(label, shape, axis=spec["axis"], **kw)
    kw = {}
    if "range" in spec:
        kw["angular_range"] = spec["range"]
    if "linear_range" in spec:
        kw["linear_range"] = spec["linear_range"]
    return b3d.CylindricalJoint(label, shape, axis=spec["axis"], **kw)


def resolve_mates(items: list[dict], build_shape_fn) -> dict[str, dict]:
    """items: [{id, script, params, position, rotation_deg, mate?}]
    build_shape_fn(script, params) -> (shape, values, ns)  # worker.build_shape+ns

    Returns {instance_id: {"position": [x,y,z], "rotation_deg": [rx,ry,rz]}}
    with mates resolved and roots passed through.
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
        anchor_joint = _make_joint(
            "anchor", anchor_proxy, anchor_conns[mate["to_connector"]]
        )
        child_joint = _make_joint("child", child_proxy, child_spec)
        anchor_proxy.location = world[mate["to_instance"]]

        mparams = dict(mate.get("params") or {})
        atype = anchor_conns[mate["to_connector"]]["type"]
        try:
            if atype == "rigid":
                if mparams:
                    raise WorkerError(
                        ERROR_CONTRACT,
                        f"instance {iid!r}: rigid mate takes no params",
                    )
                anchor_joint.connect_to(child_joint)
            elif atype == "revolute":
                anchor_joint.connect_to(
                    child_joint, angle=float(mparams.pop("angle", 0.0))
                )
                if mparams:
                    raise WorkerError(
                        ERROR_CONTRACT,
                        f"instance {iid!r}: unknown mate params {sorted(mparams)}",
                    )
            else:  # cylindrical
                anchor_joint.connect_to(
                    child_joint,
                    position=float(mparams.pop("position", 0.0)),
                    angle=float(mparams.pop("angle", 0.0)),
                )
                if mparams:
                    raise WorkerError(
                        ERROR_CONTRACT,
                        f"instance {iid!r}: unknown mate params {sorted(mparams)}",
                    )
        except WorkerError:
            raise
        except Exception as exc:  # joint range violations etc.
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
