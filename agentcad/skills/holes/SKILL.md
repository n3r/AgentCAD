---
name: holes
description: The hole wizard - clearance, tapped, counterbore, countersink and drilled holes from ISO/ASME tables, the plane predicate, per-instance verification and the machine-readable hole record.
triggers: [hole, holes, drill, bore, clearance, tapped, tap drill, counterbore, cbore, countersink, csk, fastener hole, screw hole, iso 273, designation, callout, plane, verify]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

A hole is not a cylinder you subtracted. It is a *manufacturing instruction* —
a diameter that came off a published table, a depth that is either through or
stated, a callout a shop can read, and evidence that it removed material where
you said it would. `agentcad.toolkit.holes` gives you one call per intent and
hands back a record carrying all of that, so the drawing, `get_part` and the
BOM cannot disagree with the geometry. Use this skill whenever a part needs a
fastener hole, a bolt pattern, a tapped boss or a countersunk seat. For the
*positions*, use `patterns` (the wizard takes any list of points). For real
thread geometry and the fasteners that go in the holes, use
`threads-and-fasteners`. A plain cosmetic pocket that no fastener enters is
just a `Hole()` and needs none of this.

## One call per intent

`from agentcad.toolkit import holes`; tools: `hole_standards`, `add_holes`.

Diameters come from vendored ISO 273 / ISO 261-262 / ASME B18.2.8 / Unified
tables (never invented here), and every call returns a machine-readable RECORD
that reaches the drawing callouts and `get_part`.

```
part, recs, warn = holes.clearance(part, points, "M5", fit="medium")
part, recs, warn = holes.tapped(part, points, "M6", depth=12)
part, recs, warn = holes.counterbore(part, points, "M8")
part, recs, warn = holes.countersink(part, points, "M6")
part, recs, warn = holes.drill(part, points, 18.0)     # no table, mm
part, recs, warn = holes.clearance(part, pts, "1/4", std="ansi")
```

Common kwargs: `plane=`, `depth=` (omit for through), `thru=`,
`std="iso"|"ansi"`, `fit="fine|medium|coarse"` (or the ASME spellings
`close|normal|loose`), `verify="bbox"|"exact"|"off"`.

## Verification: what each mode measures

`verify="bbox"` (the DEFAULT) is three rungs, cheapest first, and every status
it reports is measured on THAT instance: the bounding-box screen (0.014 ms, and
disjoint boxes prove a MISS because a box contains its shape), then a point on
the bore axis classified inside the solid (0.041 ms, which PROVES engagement),
then the exact `(part & tool)` probe (~5 ms) for the instance nothing cheap
could decide. Its verdicts are therefore identical to `verify="exact"`; `exact`
buys the per-instance `engaged_mm3` / `contact_mm2` numbers on EVERY instance
(372 ms vs 115 ms for 50 holes). `verify="off"` measures nothing at all.

## The plane predicate

`plane=` is a build123d `Plane`, or one of `top|bottom|front|back|left|right` —
a PREDICATE re-evaluated every rebuild (the extreme planar face along that
axis, largest area, then lowest centre), never a face index. `points` are
`(u, v)` in that plane: top -> `(x, y)` drilling -Z; front -> `(x, z)` drilling
+Y; left -> `(-y, z)` drilling +X. A name that resolves to nothing raises.

`holes.drill` is the one with no table behind it: a structural bolt hole has no
fastener row (18 mm for an M16 is none of ISO 273's values), so it takes
millimetres and its record carries NO size and NO provenance.

`tapped()` bores the TAP DRILL and records the thread — a tapped hole on a
drawing is a callout, not a helix. `thread="real"` additionally fuses real
thread geometry, needs a depth, costs ~9k triangles per hole, and bores at
`root_radius` instead (boring at the tap drill buries the ridges: valid, fast,
invisible). `countersink()` always passes its angle explicitly — build123d's
default is 82 deg, which is ASME's; ISO is 90.

## Designations

Per the hole's own standard (ISO | ASME):

```
clearance          (/)5.5                    | (/)0.217
clearance, blind   (/)5.5 depth6             | (/)0.217 depth0.25
tapped             M5x0.8 - 6H depth12       | 10-24 UNC - 2B depth0.5
counterbore        (/)5.5 cbore(/)9.5depth5.4
counterbore, blind (/)5.5 depth6 cbore(/)9.5depth5.4
countersink        (/)5.5 csk(/)10.4x90deg
countersink, blind (/)5.5 depth6 csk(/)10.4x90deg
```

A BLIND HOLE ALWAYS STATES ITS DEPTH, and each depth symbol qualifies the
diameter group it FOLLOWS — that is what keeps a counterbore's two depths
apart. Omitting the hole depth (which clearance/counterbore/countersink used to
do) is the one spelling a shop reads as a through hole. (The real strings use
the ISO 129 glyphs; ask the `hole_standards` tool for one.)

## The records

Each call makes ONE GROUP record -- {id, family, standard, designation,
designation_base, size, fit, d, count, positions, centers, axis, plane,
depth_mm, thru, removed_mm3, tap, cbore, csk, provenance, instances, verify,
dropped, pattern} -- so a pattern of a wizard hole is one hole group, not N
unrelated records. They ride on the shape the helper RETURNS:

```
holes.records(part)        -> the records carried by this shape
holes.carry(new, old)      -> after a RAW build123d op of your own
```

Every operation that returns a new object (`safe_fillet`, `safe_bool`, a raw
`part - tool`, a re-entered `BuildPart`) drops the attribute; the `safe_*`
helpers carry it for you, a raw op does not, and the build result warns when
records went missing. Rebuild results and `get_part` gain `holes`: `[...]` the
records, `null` "declares none", `[]` + a warning "they were dropped", key
absent "not harvested".

`count`, `positions` and `centers` COVER ONLY THE INSTANCES THAT DEMONSTRABLY
REMOVED MATERIAL -- anything the guard proved was a no-op is excluded and
listed under `dropped` `[{i, status, position}]`, with a warning naming the
indices. `verify` echoes the MODE YOU ASKED FOR; the tier that actually decided
an instance is instances[i]["probe"] (`"bbox"|"axis"|"exact"|"off"`), and one
default-mode call routinely uses two of them. `designation` is DERIVED from the
record's own numbers (so a reader can re-derive it and refuse a record whose
text and numbers have drifted apart) and `designation_base` is the same callout
with no depth qualifier. `provenance` is `{standard (a LIST), sources,
corroborated, conflicts}` for the DIAMETER, unioned over every published row
that fed the hole (a counterbore has two); `corroborated` is true only for two
or more independent sources that AGREE, so a single-sourced seat (every ISO
10642 countersink) or an adjudicated one (ASME #8 normal) says so on the
record. `drilled` carries `provenance: None` -- no table supplied its number.
Both `designation` and `provenance` are RE-DERIVED from the record's own fields
and compared when it is validated, so neither can carry a claim the record's
size, fit, standard and fastener do not earn.

## Guards you will actually see

An instance off the part or off the face (named by index, and excluded from the
count), a depth that REACHES the far side of the stock below the plane (so the
hole is not blind), a new hole within one diameter of another or of an existing
record, and a DISPUTED table cell. An unknown size or a negative depth RAISES,
and comes back as a normal `script_error` with the line.

```python
from agentcad.toolkit import holes, patterns

def build(p):
    part = Box(120, 120, p.t)
    part, recs, warn = holes.tapped(part, patterns.bolt_circle(45, 8),
                                    "M5", depth=10)
    return part                     # drawing prints: 8x M5x0.8 - 6H depth10
```

## Checklist

- [ ] One call per intent, over all its points — not one call per hole.
- [ ] `plane=` is a name or a `Plane`, never a face index.
- [ ] Every blind hole passes an explicit `depth=`; through holes omit it.
- [ ] The returned `part` is the one you keep building on, and any raw op
      after it is followed by `holes.carry(new, old)`.
- [ ] The warning is read: dropped instances mean the pattern missed.
- [ ] `holes.drill` only where no fastener table applies, and its record's
      missing size/provenance is expected, not a bug.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/holes.py` — the five entry
  points, the plane predicate, the three-rung verification and the group
  record.
- AgentCAD toolkit source: `agentcad/toolkit/hole_standards.py` — the
  vendored tables, their per-row sources, `corroborated` and `conflicts`.
- ISO 273 *Fasteners — Clearance holes for bolts and screws*; ISO 261 and
  ISO 262 *ISO general purpose metric screw threads*; ISO 10642 *Hexagon
  socket countersunk head screws*; ISO 129 *Technical drawings — Indication
  of dimensions and tolerances* (the callout glyphs).
- ASME B18.2.8 *Clearance Holes for Bolts, Screws, and Studs*; ASME B1.1
  *Unified Inch Screw Threads*.
- build123d documentation, *Objects and operations* (`Hole`, `CounterBoreHole`,
  `CounterSinkHole`): <https://build123d.readthedocs.io/>
