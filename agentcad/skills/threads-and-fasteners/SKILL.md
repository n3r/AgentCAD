---
name: threads-and-fasteners
description: Simple vs real ISO threads, threaded rods, cap screws and hex bolts, boring a tapped hole at root_radius, and the male/female interference every assembly check trips over.
triggers: [thread, threads, threaded, screw, bolt, cap screw, hex bolt, fastener, tapped hole, iso thread, pitch, m8, bd_warehouse, root radius, interference, cosmetic]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: [threads]
---

Threads are the one feature where "model it properly" is usually the wrong
answer. A real ISO helix is ~9 000 triangles per hole, and a fully-driven bolt
modelled truthfully *fails* an interference check by design — because that is
how threads grip. This skill covers when to spend the geometry and when a
cosmetic thread is the honest choice, the four `agentcad.toolkit.threads`
wrappers, and the one radius that decides whether a tapped hole shows its
ridges or buries them. Use it whenever a part carries a threaded rod, a tapped
boss, or a fastener you want to see in an assembly. For the *hole* under the
thread — the tap drill, the callout, the record a drawing prints — use `holes`,
which is the right tool for almost every tapped feature. This skill requires
`bd_warehouse`; if the capability is absent the skill is hidden and the
wrappers will not import.

## Simple vs real

Cosmetic threads (`simple=True`) are fast and light — the right choice for
assembly and fit views. Real ISO threads (`simple=False` / `IsoThread`) are
exact but heavy (~9k triangles each) — use them for manufacturing drawings and
genuine mating only. Never call bd_warehouse `ThreadedHole(simple=False)`
directly (a ~15 s no-op trap); use the wrappers below.

```
threads.threaded_rod(d, pitch, length)        -> Part (thread on its core)
threads.cap_screw(size="M8-1.25", length=20, simple=False)  -> fastener
threads.hex_bolt(size="M8-1.25", length=20, simple=False)   -> fastener
    # bearing face at local z=0, head at +z, threaded shank down to -length.
```

`from agentcad.toolkit import threads` (needs bd_warehouse).

The bearing face at local `z=0` is the useful convention: place a fastener by
putting its origin on the face it seats against, and the head stands proud
while the shank goes down into the hole. No offset arithmetic, and the same
placement works for a screw of any length.

## A tapped hole with real thread geometry

```python
thr = threads.tapped_hole_thread(d, pitch, depth)   # internal thread solid
with BuildPart() as part:
    Box(40, 40, 15)
    with Locations(part.faces().sort_by(Axis.Z)[-1]):
        Hole(radius=thr.root_radius, depth=depth)   # bore at ROOT (major/2)
    add(thr)                                        # ridges protrude inward
```

Bore at `thr.root_radius` so the thread crests reach in to `thr.min_radius` and
add real material. Boring at `thr.min_radius` (the physical tap-drill) instead
buries the ridges in the wall: valid, fast, but NO visible thread. This is the
single most common "my thread disappeared" bug, and it is invisible in the
metrics — the volume is plausible, the shape is valid, and the helix is inside
the material.

For anything that is going on a drawing rather than into a render, prefer
`holes.tapped(part, points, "M6", depth=12)`: it bores the tap drill, carries
the thread on a record, and prints `M6x1 - 6H depth12` as a callout. A tapped
hole on a drawing is a callout, not a helix.

## The interference gotcha

A male thread and a female thread of the same size ALWAYS interpenetrate as
solids (that is how they grip), so a fully-driven bolt fails
`check_interference`. Keep the bolt's threaded shank inside a clearance
counterbore, or leave a standoff above the tapped thread, or use cosmetic
threads — see the `fasteners` example.

Which of the three you pick is a modelling decision, so make it deliberately:

- **Clearance counterbore** — physically what a real joint looks like, and the
  interference disappears for the right reason. Best when the assembly is the
  deliverable.
- **Standoff** — leave the bolt a millimetre or two short of fully driven. Fast,
  obviously artificial, fine for a fit study.
- **Cosmetic threads** — `simple=True` on both sides. The check passes because
  neither side has ridges. Best for anything with more than a handful of
  fasteners, where the triangle count matters more than the helix.

A `check_interference_free()` spec on an assembly full of real threads will be
red forever; that is not a bug to fix in the checker.

## Cost, and when to pay it

| choice | triangles per feature | use it for |
| --- | --- | --- |
| cosmetic (`simple=True`) | tens | assemblies, fit views, anything repeated |
| real ISO (`simple=False`) | ~9 000 | a manufacturing drawing, a genuine mate study, a render close-up |
| `holes.tapped` (callout only) | zero extra | every tapped hole that will be manufactured |

A 24-bolt flange with real threads on both sides is over 400 000 triangles
before the flange itself. Mesh size reaches the browser, so this is a
user-visible cost, not a purity argument.

## Traps

- Sizes are strings with the pitch: `"M8-1.25"`, not `"M8"`. The wrappers
  raise on an unknown designation rather than guessing a coarse pitch.
- `threaded_rod(d, pitch, length)` puts the thread *on its core*, so the rod's
  outer diameter is the major diameter — do not add a cylinder underneath it.
- A real thread is a tangent-junction solid, which is exactly the shape OCCT's
  `Common` boolean gets wrong (`selectors-and-occt-failures`). Do not conclude
  "no interference" from a boolean between two threaded parts.
- `bd_warehouse` is an optional dependency. Guard the import at module level
  only if the script must run without it; part scripts in this app can assume
  the app venv.
- Real threads are slow to *build*, not just to render. A part with a dozen of
  them can approach the kernel's 60 s script timeout.

## Checklist

- [ ] The default is cosmetic; real geometry is a decision with a reason.
- [ ] Manufactured tapped holes go through `holes.tapped`, not a modelled
      helix.
- [ ] A modelled internal thread bores at `root_radius`, never `min_radius`.
- [ ] Fasteners are placed by their bearing face at `z=0`.
- [ ] The interference story is chosen (counterbore, standoff or cosmetic),
      not discovered when the assembly check goes red.
- [ ] Triangle budget considered before repeating a real thread N times.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/threads.py` — `threaded_rod`,
  `cap_screw`, `hex_bolt`, `tapped_hole_thread`, the `root_radius` /
  `min_radius` distinction and the `ThreadedHole(simple=False)` wrapper.
- AgentCAD toolkit source: `agentcad/toolkit/holes.py` — `tapped()`, the tap
  drill and the thread callout.
- bd_warehouse documentation, *Thread* and *Fastener* modules:
  <https://bd-warehouse.readthedocs.io/>
- build123d documentation, *Objects and operations*:
  <https://build123d.readthedocs.io/>
- ISO 261 / ISO 262 *ISO general purpose metric screw threads*; ISO 965
  tolerance classes (the `6H`/`6g` designations the callouts use).
