---
name: selectors-and-occt-failures
description: Picking edges and faces with ShapeList selectors, and reading OCCT failures - fillet radius, degenerate booleans, shell openings, loft order, Hole with no material, sweep self-intersection.
triggers: [selector, edges, faces, filter_by, group_by, sort_by, occt, fillet fails, boolean, degenerate, shell, offset, loft, sweep, hole, invalid, failure, error, topology, brep]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

Two thirds of the time a part script fails, the geometry was fine and the
*selection* was wrong: a `filter_by(Axis.Z)` that swept up the ribs as well as
the corners, a `sort_by(Axis.Z)[-1]` that picked the boss's cap instead of the
lid. The remaining third is OCCT refusing an operation, usually with an error
string that names the algorithm and not your mistake. This skill is the deeper
version of the selector and failure-mode notes in the part-script cheat-sheet:
how to address topology so the address survives a rebuild, and what each of
OCCT's common refusals actually means. Reach for it when a build goes red on a
fillet, a shell, a loft or a boolean, or when a feature lands on the wrong
edge. It is not the robustness-helper guide (`robust-parametrics` — the
`safe_*` wrappers and the parameter guards that prevent the failure), and it
does not cover hole, thread or sheet-metal geometry, which have their own
skills.

## Selectors

ShapeList methods, chainable:

```python
part.edges().filter_by(Axis.Z)              # parallel to Z
part.edges().filter_by(GeomType.CIRCLE)     # circular edges
part.faces().sort_by(Axis.Z)[-1]            # topmost face
part.edges().group_by(Axis.Z)[-1]           # edges at max Z level
part.faces().filter_by(Plane.XY)            # faces parallel to XY
```

The three verbs do different things and mixing them up is the usual bug:

- **`filter_by`** keeps every shape matching a predicate — an `Axis` (parallel
  to it), a `Plane` (parallel to it), a `GeomType`, or a callable. It returns
  *all* of them, in no meaningful order, and it does not care where they are.
- **`sort_by`** orders the whole list along an axis (or by `SortBy.AREA`,
  `SortBy.LENGTH`, `SortBy.VOLUME`, `SortBy.DISTANCE`) and you index into it.
  `[-1]` is one shape, and it is the extreme *by centre*, so a large slanted
  face can outrank a small higher one.
- **`group_by`** buckets shapes that share a coordinate along the axis and
  returns a list of lists. `group_by(Axis.Z)[-1]` is *every* edge at the top
  level; `sort_by(Axis.Z)[-1]` is the single topmost. Use `group_by` for "the
  top face's perimeter", `sort_by` for "the top face".

Chain them to narrow, and prefer a positional band over an index:

```python
top = part.faces().filter_by(Plane.XY).sort_by(Axis.Z)[-1]
corners = (part.edges()
           .filter_by(Axis.Z)
           .filter_by(lambda e: abs(e.center().X) > 20))
bores = part.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[-1]
```

Rules that keep a selection true across a rebuild:

- **Never index a raw `edges()` list.** `part.edges()[3]` is a lottery ticket
  on the next parameter change; OCCT's ordering is an implementation detail.
- **Select before you build the thing that pollutes the selection.** Fillet
  the plate's corners *before* the ribs exist, or `filter_by(Axis.Z)` will
  catch every rib's vertical edges too. Ordering is the cheapest selector.
- **Bound the region explicitly.** A band in *both* X and Y beats
  `group_by(Axis.Z)[0]`, which hands you a whole underside perimeter.
- **A selection can be empty**, and an empty `ShapeList` handed to `fillet()`
  raises somewhere unhelpful. `if edges:` before the operation, or assert the
  count you expect.
- **Filleted and chamfered edges are gone.** The edge you rounded is now two
  edges and a face, parallel to nothing in particular. Anything that selects
  by axis must run before the rounding.
- **Faces come back from a `Plane`, not a face index.** For holes, ribs and
  bosses, `plane="top"` is a *predicate* re-evaluated on every rebuild (see
  `holes` and `ribs-bosses-draft`), which is exactly why it is safer than a
  face you picked once.

## Common failure modes

- "Failed creating a fillet": radius too large for the edge — reduce it or
  fillet fewer edges.
- `Hole()` needs existing material to cut; depth defaults to through-all.
- `BuildSketch` profiles must be closed before `make_face()`.
- `offset()`/shell with openings: pick the face to remove via selectors.

## OCCT failure playbook

**Fillet radius vs edge length.** OCCT's fillet fails when the radius does not
fit the *local* geometry: it exceeds half the shortest adjacent edge, or two
fillets on converging edges would overlap, or the rounded surface would run
off the end of its face. The message is always the same
("Failed creating a fillet") whichever it was. Fix in this order: clamp the
radius against the dimension it must fit inside (`robust-parametrics`), fillet
fewer edges per call so a failure names a smaller set, then use `safe_fillet`,
which binary-searches down and tells you what it actually applied. `.max_fillet`
on the shape gives you the largest radius the selected edges accept, and is
worth asserting against rather than discovering.

**Degenerate booleans.** OCCT 7.9's `BRepAlgoAPI_Common` (build123d's `&`)
silently returns a *wrong* answer — usually empty, sometimes negative volume —
for solids carrying G1-tangent face junctions, which means any filleted or
swept solid. `IsDone()` is `True`, nothing raises, and the failure is
order-dependent inside one process: an earlier boolean changes whether the next
one tells the truth. Two genuinely distinct tangent-jointed swept solids that
plainly overlap can intersect to nothing. AgentCAD's kernel now **detects this
and fails closed** — interference checks list the pair and mark it
`degenerate: true`, the bench raises a kernel error rather than banking `0.0`
(changelog `0308`). What that means for a script: a boolean answering "no
overlap" between two swept, filleted solids is not evidence of clearance. Two
practical counter-measures: **overlap, never touch** — start a feature 0.5–1 mm
inside the body it fuses to, because two exactly coplanar faces are the
degenerate case and an overlap always fuses; and when you must measure an
intersection, use the `&` operator on solids you decomposed yourself
(`shape.solids()`) and treat an exactly-zero result on overlapping bounding
boxes as suspect, not as clean.

**Shell and offset with openings.** `offset(amount=-t, openings=…)` needs the
opening faces named through selectors, and it fails when the wall would
self-intersect: `t` larger than the smallest local radius of curvature, a
fillet whose radius is under the wall thickness, or a sharp internal corner the
offset surface cannot round. Shell **before** you fillet, keep `t` below every
internal fillet radius, and open faces one at a time to find the culprit.
`safe_shell` walks the fallback ladder for you (`Kind.ARC` →
`Kind.INTERSECTION` → fewer openings → an approximate boolean subtract) and
warns that the last rung is not a uniform wall — a `check_wall` spec is the
way to know what you really got.

**Loft section ordering.** `loft()` takes the sketches in the order they were
added and connects vertex `i` to vertex `i`. Sections whose start vertices sit
on opposite sides produce a twisted, self-intersecting solid that is often
still `is_valid`. Keep every section's profile the same winding and the same
starting corner, keep the vertex counts equal (a 4-point rectangle to a circle
is a lottery), do not stack two sections at the same Z, and check the volume
against the prism you expect. `ruled=True` gives a straight-sided loft that
fails loudly instead of twisting quietly.

**`Hole` without material.** `Hole()` is a subtraction; there must already be
solid on the drill axis at that location, and the depth defaults to
through-all. A hole placed off the part **succeeds** — in about a millisecond,
with the volume exactly unchanged and `is_valid True`. That is why the hole
wizard measures engagement per instance and reports `dropped` (see `holes`),
and why a bare `Hole()` inside `with Locations(...)` deserves a volume or
solid-count assertion. Inside `BuildPart`, `Hole` also needs to be in the same
builder context as the material, not a fresh one.

**Sweep self-intersection.** `sweep()` fails, or produces an invalid solid,
when the path's radius of curvature is smaller than the profile's half-width —
the inner side of the bend folds through itself. The rule of thumb is
`bend_radius > profile_half_width` with margin; for a tube, centreline radius
greater than the outer diameter. Also: the profile must be perpendicular to
the path at its start (build the sketch on a plane derived from the path),
`is_frenet=True` stabilises a path with an inflection, and a path made of
tangent-continuous segments sweeps far more reliably than one with kinks. A
swept solid is exactly the tangent-junction shape the degenerate-boolean note
above is about, so validate it with volume and `is_valid`, not with a boolean.

**Reading any of them.** An OCCT failure that raises comes back as a normal
script error with your line number; an OCCT failure that does *not* raise is
the dangerous one. The three tells are the solid count (a feature that missed
fuses as an extra solid), the volume delta (a cut that removed exactly nothing),
and `is_valid` (a draft or offset that "succeeded" into a self-intersecting
face). Assert all three with `design-specs` rather than trusting a green build.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/fillet.py`,
  `agentcad/toolkit/shell.py`, `agentcad/toolkit/boolean.py` — the
  failure ladders the playbook above describes.
- AgentCAD changelog `docs/changelog/0308-degenerate-boolean-detection.md`:
  the measured degenerate-`Common` table, the tangent-junction root cause and
  the fail-closed detector.
- build123d documentation, *Selectors and ShapeList*, *Objects and
  operations*: <https://build123d.readthedocs.io/>
- Open CASCADE Technology documentation, *Modeling Algorithms* — fillets and
  chamfers, offsets and shelling, sweeping (`BRepOffsetAPI_MakePipeShell`),
  and *Boolean Operations* (`BRepAlgoAPI_Common`, fuzzy tolerance).
