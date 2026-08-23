---
name: design-specs
description: Executable design intent - SPECS declarations checked on every rebuild, the part/assembly/FEM tiers, requirement traceability, and the spec-first workflow the checks tool and geometry CI consume.
triggers: [spec, specs, design intent, check, checks, check_wall, check_mass, check_that, requirement, traceability, run_specs, verify, wall thickness, interference, clearance, stackup, geometry ci]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

A part that builds is not a part that is right. OCCT will happily hand you a
valid solid with a 0.4 mm wall, twice the mass budget, and a cut that removed
nothing — every one of those is a green build. `SPECS` is where you write the
design intent down as code so the kernel checks it on every rebuild, the
`checks` tool reports it, and geometry CI can gate a pull request on it. Use
this skill on anything with a real requirement behind it: a mass budget, a
minimum wall, a clearance, an envelope it must fit inside. Do not reach for it
to assert something the build already guarantees — a spec that can never fail
is noise in every report forever. For the mechanics of the failures specs
catch, see `selectors-and-occt-failures`; for the FEM tier, see `fem-workflow`.

## Declarations are pure data

`from agentcad.toolkit.specs import ...` (optional)

Write the design intent down as code and the kernel checks it on every rebuild.
Declarations are pure data — no geometry, no measurement here.

```python
from agentcad.toolkit.specs import check_mass, check_that, check_wall

SPECS = [
    check_wall(min_mm=2.5, grid=8, requirement="ENG-014"),
    check_mass(max_g=120.0, requirement="SYS-042"),
    check_that(lambda part, metrics:
               metrics["bbox"]["max"][2] - metrics["bbox"]["min"][2] <= 80,
               name="fits_fairing"),
]
```

**Part scope** (in this script): `check_valid()` | `check_mass(min_g, max_g)` |
`check_volume(min_mm3, max_mm3)` | `check_bbox(within_mm)` |
`check_wall(min_mm, grid=8)` | `check_that(fn, name)` |
`check_fem_static(fixed_face, load_face, load_N, max_vm_mpa=, max_disp_mm=)`
— faces are `{"axis": "z", "side": "max"}`.

**Project scope** (in the project's root `specs.py`, over assembly instance
ids): `check_interference_free()` | `check_clearance(a, b, min_mm)` |
`check_stackup(from_instance, to_instance, axis, within)`.

Every constructor takes `name=` (a default is derived: `wall_min`, `mass_max`,
...) and `requirement=` — an opaque id or URL ("SYS-042") that groups checks in
the `run_specs` report. Arguments are validated EAGERLY, so a bad limit raises
while this module executes and comes back as a script error with a line number.

## What a rebuild measures, and what it defers

A rebuild evaluates the shape tier only (valid/mass/volume/bbox/wall/that);
assembly and FEM checks report `skip` + "deferred" there and are measured by
the `run_specs` tool. `check_wall` is a SAMPLED ray cast along the inward face
normal — it finds chamfers and fillet runouts, so the measured minimum is not
the nominal wall: pick the limit from a measurement and pin `grid` (cost is
quadratic in it). Skips carry a reason and a hint and are not failures; an
"error" status means the check itself broke, which is not "it is fine".

Those last two sentences are the whole discipline of reading a spec report.
`skip` is "we did not measure"; `error` is "the measurement broke"; only `fail`
is "the part is wrong" — and only `pass` is evidence.

## Spec-first workflow

**Write the specs before the geometry.** The requirement is the input to the
design, not a review afterwards. Starting the script with

```python
SPECS = [
    check_wall(min_mm=2.0, requirement="ENG-101"),
    check_mass(max_g=45.0, requirement="ENG-102"),
    check_valid(),
]
```

costs three lines and changes what you build: the wall thickness is now a
number you chose against a limit rather than one you typed. It also makes the
first rebuild *informative* — a red `wall_min` on a part that has no walls yet
is the check telling you it is wired up.

**A failing spec NEVER fails the rebuild.** The geometry lands and the failure
is reported beside the metrics. This is deliberate: you must be able to see the
part that violates the requirement, because that is how you fix it. It also
means a red spec is invisible unless someone reads the rows — so read them.

**Read the check rows, not the build status.** After a rebuild, the spec
results ride beside the metrics; `run_specs` runs the deferred tiers (assembly,
FEM) and returns the full report, grouped by `requirement`. A row carries
`status`, `name`, `requirement`, `measured`, `limit`, `unit`, `scope` and, for
a skip or an error, a `reason` and a `hint`. Quote the measured number when you
report to a human: "wall 1.83 mm against a 2.0 mm limit" is actionable, "the
wall check failed" is not.

**Pick limits from measurements.** `check_wall` finds chamfer runouts and
fillet tangency, so the honest minimum on a nominally 2.4 mm wall may be
1.9 mm. Build the part, measure, then set the limit just under what a correct
part achieves — a limit set at the nominal is a check that is red forever and
will be ignored within a day.

**How `checks` and geometry CI consume them.** `agentcad check` runs a `specs`
stage that is exactly `run_specs`: one row per declared check, preserving
`status`, `requirement`, `reason`, `hint` and `error`, with `measured`,
`limit`, `unit`, `scope`, `part` and `location` in the row's details. The
report's top-level `requirements` map is the traceability — every requirement
id you wrote, and the checks that speak to it. A part that declares nothing
produces `skip`/`not_declared`, and the `specs` gate is **fail-closed**: it
never answers `pending`, so "we could not measure" is not a pass. That is why
`requirement=` is worth filling in even for a one-part project: it is the
string that connects a red row in CI to the thing somebody asked for.

## What makes a good spec

- **Falsifiable.** It must be able to fail on a plausible wrong version of this
  part. `check_volume(min_mm3=1)` is not a spec.
- **Cheap.** Specs run on every rebuild. `check_wall`'s cost is quadratic in
  `grid`; the default 8 is usually right and 20 is rarely worth it.
- **Named.** The derived names (`wall_min`, `mass_max`) are fine for one of
  each; pass `name=` as soon as there are two.
- **Attributed.** `requirement=` groups the report and survives into CI.
- **Structural, where nothing else looks.** `check_valid()` and a solid-count
  `check_that` catch the silent failures — a feature that fused into the air, a
  cut that missed — which no dimension check will ever see.

## Checklist

- [ ] SPECS written before or alongside the geometry, not after.
- [ ] Every requirement the user stated has a check with its id.
- [ ] `check_valid()` and a solid-count assertion are present.
- [ ] Limits came from a measurement, not from the nominal.
- [ ] `grid` pinned on `check_wall`, and its cost accepted.
- [ ] The rows were read after the rebuild, and `skip`/`error` were not
      mistaken for `pass`.
- [ ] Assembly and FEM specs were run through `run_specs`, not assumed from a
      rebuild.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/specs.py` — the constructors,
  eager argument validation, the three tiers and the derived names.
- AgentCAD source: `agentcad/core/specs.py` (`SpecRunner`) and
  `agentcad/core/checks.py` — the report shape, the `requirements`
  traceability map and the fail-closed `specs` gate.
- AgentCAD documentation: `docs/geometry-ci.md` (the `specs` stage, its rows
  and the CI verdict) and `docs/part-authoring.md`.
- build123d documentation, *Topology and properties* (volume, bounding box,
  validity): <https://build123d.readthedocs.io/>
