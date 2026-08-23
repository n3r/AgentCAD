---
name: assemblies-and-mates
description: Declaring named connection frames with a connectors(p, part) function and positioning assembly instances by mates - rigid, revolute and cylindrical joints, the anchor rule, and the mate forest.
triggers: [assembly, assemblies, mate, mates, connector, connectors, joint, revolute, cylindrical, rigid, instance, transform, position, hinge, anchor, set_mate, clear_mate, placement]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

An assembly positioned by hardcoded transforms is a set of numbers that go
stale the moment a part's height changes. Connectors and mates replace them
with a *relationship*: the lid seats on the box's rim, wherever the rim ends up.
A part script declares named frames; the manifest says which frame meets which,
and the service resolves a concrete position and rotation at read time. Use
this skill when an assembly has parts that must stay together through parameter
changes, or when something rotates or slides — a hinge, a piston, a telescoping
leg. Both features are optional and backward compatible: an instance with no
mate keeps its own position, and a part with no `connectors` function still
places fine. For the geometry inside each part, see the part-authoring skills;
this one is about how parts find each other.

## Declaring connectors

Add a second top-level function to a part script to declare named connection
frames; instances can then be positioned by MATES instead of hardcoded
transforms. Both are optional — omit for plain placement.

```python
def connectors(p, part) -> dict:
    # p = the resolved params namespace (as build receives); part = the
    # built shape, so connectors can be derived from topology. Locations
    # are in the part's LOCAL frame.
    top = part.faces().sort_by(Axis.Z)[-1]
    return {
        "seat":  {"type": "rigid", "location": (0, 0, p.height)},
        "hinge": {"type": "revolute", "axis": ((0, 0, 0), (1, 0, 0)),
                  "range": (0, 180)},
        "bore":  {"type": "cylindrical", "axis": ((0, 0, 0), (0, 0, 1))},
    }
```

`location` accepts `(x,y,z)` | `((pos),(rot))` | a `Location` | a `Plane`.
`axis` accepts `((point),(direction))` | an `Axis`.

`connectors` receives the *built shape*, which is the whole point: derive the
frame from topology (`part.faces().sort_by(Axis.Z)[-1]`) and it follows the
geometry through every parameter change. Deriving it from a parameter
(`(0, 0, p.height)`) is equally valid and cheaper — use topology when the
number is not a parameter you already have.

## Declaring a mate on an instance

Manifest mate on an instance (the service resolves it to a concrete
position/rotation_deg at read time):

```python
{"id": "lid", "part": "lid",
 "mate": {"connector": "seat",          # ON THIS instance -- must be RIGID
          "to_instance": "box",          # the anchor instance id
          "to_connector": "rim",         # anchor connector: carries the DOF
          "params": {"angle": 30.0,      # revolute/cylindrical only (deg)
                     "position": 5.0}}}  # cylindrical only (mm)
```

## The rules

The moving side (`connector`) must be rigid; the ANCHOR connector's type
(rigid/revolute/cylindrical) decides the joint. A rigid mate takes no params.
Instances with no mate are roots (world = their `position`/`rotation_deg`).
Mates form a forest resolved in topological order; a cycle is rejected. A
mate-driven instance cannot be nudged with `set_instance_transform` (409 —
clear the mate first). Tools: `set_mate` / `clear_mate`.

Unpacking the one rule people get backwards: **the degrees of freedom live on
the anchor**. The lid's `seat` is a rigid frame — it is just "here is my
mounting point". The box's `rim` is what says whether the lid is bolted on
(`rigid`), swings (`revolute`) or slides and spins (`cylindrical`). So if you
want a hinge, declare `revolute` on the part that *holds* the hinge, and give
the swinging part a plain rigid frame at its pivot.

`params` follow from the anchor's type: nothing for rigid, `angle` (degrees)
for revolute, `angle` and `position` (mm) for cylindrical. A `range` on the
anchor bounds the angle; asking for one outside it is a refusal, not a clamp.

## Working patterns

- **A stack** — base → bracket → motor, each mated to the one below. The forest
  resolves in topological order, so changing the base's height moves everything
  above it, once.
- **A hinge study** — one `revolute` anchor, and the mate's `angle` becomes the
  parameter you sweep to check clearance through the swing.
- **A bore and shaft** — a `cylindrical` anchor gives you both the rotation and
  the insertion depth as numbers you can drive.
- **Mixed** — mate what should follow, and leave genuinely independent
  instances as roots with plain transforms. Not everything needs a mate.

## Traps

- A mate is not a constraint solver. It is a *placement*: one anchor, one
  frame, resolved directly. There is no loop closure, and a cycle is rejected
  rather than solved.
- The moving connector must be **rigid**. A revolute frame on the moving side
  is a refusal, and the fix is to move the joint type to the anchor.
- `set_instance_transform` on a mated instance is a 409. Clear the mate first,
  or change the mate's params — nudging is exactly what mates exist to stop.
- Connectors are resolved from the *built* part, so a connector derived from a
  face that a parameter change deletes will move somewhere surprising. Prefer a
  parameter-derived location where the topology is fragile, and assert the
  result with a `check_stackup` spec (`design-specs`).
- Interference between mated parts is a separate question:
  `check_interference_free()` and `check_clearance(a, b, min_mm)` are the
  project-scope specs that answer it, and a threaded fastener will always fail
  the first one (`threads-and-fasteners`).
- Rotations are intrinsic XYZ Euler degrees everywhere in this system — the
  resolved `rotation_deg` on an instance included.

## Checklist

- [ ] Every connector's `location`/`axis` is in the part's LOCAL frame.
- [ ] The joint type is declared on the **anchor**, and the moving side is
      rigid.
- [ ] `params` match the anchor's type, and any `range` is respected.
- [ ] The mate graph is a forest — no instance is its own ancestor.
- [ ] Instances that should not follow anything are roots on purpose.
- [ ] A stackup or clearance spec proves the resolved assembly, rather than
      the screenshot.

## Sources

- AgentCAD source: `agentcad/core/mates.py` and
  `agentcad/kernel/_mates_resolver.py` — the joint types, the anchor rule,
  the forest resolution and the cycle rejection.
- AgentCAD source: `agentcad/kernel/handlers/connectors.py` — how a part
  script's `connectors(p, part)` function is evaluated.
- AgentCAD source: `agentcad/core/tools_mates.py` — the `set_mate` /
  `clear_mate` tools and the `set_instance_transform` refusal.
- AgentCAD toolkit source: `agentcad/toolkit/specs.py` —
  `check_interference_free`, `check_clearance`, `check_stackup`.
- build123d documentation, *Joints* and *Location arithmetic*:
  <https://build123d.readthedocs.io/>
