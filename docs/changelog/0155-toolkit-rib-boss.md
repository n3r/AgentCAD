# 0155 — 2026-08-13 — PRD-010 slice 9: `features.rib` / `features.boss` and the measured trim

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 9)

## Summary

Half of FR8: `agentcad/toolkit/features.py` with `rib` and `boss`, both under
the `safe_*` honest-warning contract and both carrying hole records forward.
The slice's real content is the **trim step** — there is no rib operation in
OCCT or build123d, so a rib is a construction whose last step (trim to the
part) OCCT has no primitive for — and the guard, because a rib that misses the
part is the same class of silent failure as slice 3's misplaced cut: measured
here, it fuses without complaint, reports `is_valid True`, and *raises the
volume by the rib's full amount*.

## Spike S7 — the trim step, through the kernel worker

`scratchpad/spike_rib.py`, driven with `KernelClient.request("build", …)` so
every number is the worker's build123d, on a plain plate and on
`prototyping/enclosure_base` at the params `examples/prototyping/project.json`
ships (the real shelled part: 38686.835 mm³, 1 solid, valid, bbox z 0…30).

### (a) `to=<mm>` — extrude to a stated height

| part | rib | build | fuse | volume delta | hand-built | delta − hand | solids | valid |
|---|---|---:|---:|---:|---:|---:|---:|---|
| plate 100×60×5, top face | 80 × 3 × 8 | 3.07 ms | **6.30 ms** | 1920.0 | 1920.0 | **0.0** | 1 | `True` |
| `enclosure_base` cavity floor (z = 2.5) | 60 × 2 × 8 | 2.71 ms | **53.95 ms** | 959.9999999999563 | 960.0 | **−4.4e-11** | 1 | `True` |
| `enclosure_base`, two-segment L profile | (30+20) × 2 × 8 | 83.0 ms | 55.57 ms | 799.9999999998472 | 800.0 | −1.5e-10 | 1 | `True` |

So `to=<mm>` is exact, is one valid solid on a real shelled part, and `trace`
survives a corner. Seating the rib flush on the face is enough: repeating the
plate and enclosure runs with the rib embedded 0.5 mm **into** the material
gave the identical delta (1920.0 / 959.9999999999563), the same 1 solid and
the same `is_valid True`, so the helper has no `embed` parameter — a coincident
face-to-face fuse is fine here.

### (b) `to="part"` — extrude generously and `&` the material envelope

| part | trimmed rib | its bbox z | inside part bbox | already inside material | volume delta after fuse | solids | valid |
|---|---:|---|---|---:|---:|---:|---|
| plate 100×60×5 | 1200.0 mm³ | −2.5 … 2.5 | yes | 1200.0 mm³ | **0.0** | 1 | `True` |
| `enclosure_base` floor | 3600.0 mm³ | 0.0 … 30.0 | yes | 300.0 mm³ | 3300.0 | 1 | `True` |

**This is the finding that shaped the API.** Decision 7(a) says the envelope
"can add material outside the part"; measured, the sharper statement is that
the envelope of a *convex* part **is** the part, so `to="part"` on a plate is a
silent no-op — 0.0 mm³, one valid solid, nothing raised — and on the shelled
enclosure it runs to the top of the **bounding box** (z = 30, the wall rim),
not to the wall the rib meets. It is shipped, because on a shelled part it is
genuinely useful, but it **always warns** and names the trim it used, and the
volume-delta check is what catches the no-op. `to=<mm>` is the default and the
one the docs push.

### (c) a rib that misses the part — the silent failure

The same rib, seated 25 mm above the part:

| part | fuse | raised | volume delta | `is_valid` | solids | `(part & rib).volume` |
|---|---:|---|---:|---|---:|---:|
| plate | 4.92 ms | **none** | **+1920.0** | `True` | **2** | **0** |
| `enclosure_base` | 50.89 ms | **none** | **+960.0** | `True` | **2** | **0** |

The volume delta is *exactly what success looks like*, and `is_valid` is True.
Only the solid count and the intersection probe tell them apart, which is why
the guard measures both, and why the warning escalates to the exact `&` probe
(≈2 ms, changelog 0149) the moment the solid count rises: the number in the
warning is `engages 0 mm^3`, not an adjective.

### (d) a tapered extrusion is the rib's draft

3° taper over 8 mm: plate rib 1919.9999999999998 → **1643.4848718733426** mm³,
enclosure rib 960.0 → **753.9209272297493** mm³. Both one valid solid. This is
design Decision 7's reason for tapering the extrusion rather than drafting the
finished part, and slice 10's sweep is the evidence: `enclosure_base` refuses
the draft operation above **0.25°**.

## What shipped

- `features.rib(part, profile, thickness, *, to, plane="top", draft_deg=None,
  verify="bbox") -> (part, warning|None)`. `profile` is a polyline of `(u, v)`
  points in the plane's coordinates, traced to `thickness` (build123d `trace`),
  extruded away from the seat, fused through the builder route with `safe_bool`
  as the fallback rung (`patterns._fuse`). `draft_deg` tapers the extrusion and
  is refused with `to="part"` — a taper needs a stated height.
- `features.boss(part, at, d, h, *, hole=None, hole_depth=None, plane="top",
  draft_deg=None, std="iso", verify="bbox") -> (part, warning|None)`. The
  bearing face is the seat; `draft_deg` narrows it going up and raises rather
  than building a cone with a point; `hole="M3"` bores the tap drill through
  `holes.tapped` (blind at the seat by default) so the screw boss carries a
  record. **`thread_class` is deliberately not passed on** — `holes.tapped`
  defaults it per standard, and a class named by a boss helper would be a
  number this module invented.
- **Which way is "out".** `plane` goes through `holes.resolve_plane`, so the
  six names mean what they mean for holes and the normal points out of the
  material: holes drill along `−z_dir`, ribs and bosses grow along `+z_dir`.
- The guard reuses `patterns.engagement` / `boxes_overlap` / `_fuse_warnings`
  verbatim — one implementation of "this instance did nothing", as the plan
  requires — plus the features-specific exact-probe escalation above.
- Every path calls `holes.carry(new, old)`, because every boolean returns a new
  object with none of the original's attributes (changelog 0150). `boss` does
  it *before* the `holes.tapped` call: `carry` **replaces** the target's record
  list with the source's, so decorating `boss` with `@holes.carries_records`
  would have thrown the boss's own tapped record away.

## End-to-end, through the worker

A part script calling `rib` → `boss(hole="M3")` → `draft` built in the worker
and then harvested by the `hole_records` handler returns the boss's record with
its designation intact: `h0 / tapped / M3×0.5 - 6H ↧6`, `dropped: 0`. The same
run caught a real trap worth recording: **after a rib, `plane="top"` resolves
to the rib's top face**, so a boss placed by name landed in the air — and the
guard reported `features.boss: the result is 2 disjoint solids, not one` plus
`engages 0 mm^3`. The docstring and `docs/part-authoring.md` now say to pass an
explicit `Plane` for a feature that is not on the current outermost face.

## Files

- `agentcad/toolkit/features.py` — **new** (rib, boss, the envelope trim, the
  guard escalation)
- `agentcad/toolkit/__init__.py` — `features` re-export
- `tests/test_features.py` — **new**, 24 tests for this slice
- `docs/part-authoring.md` — "Ribs, bosses and draft" under the toolkit
- `docs/changelog/0155-toolkit-rib-boss.md`

## Verification

- `.venv/bin/python -m pytest -q tests/test_features.py -k "rib or boss or
  floating"` — **24 passed, 13 deselected in 3.27s** (this slice's half of the
  file; the 13 deselected are slice 10's).
- `.venv/bin/python -m pytest -q tests/test_features.py` — **37 passed**.
- Spike numbers above are from `scratchpad/spike_rib.py`, all through
  `KernelClient`.
- Full-suite verification is stated in changelog 0156, which lands with it.

## Notes

- `to="part"`'s envelope is the axis-aligned bounding solid, not a convex hull.
  A convex hull would behave better on an L-shaped part; it is not built here
  because the mode is already the approximate one and a second approximation
  would be harder to explain, not easier. The warning names the envelope.
- The shared `_fuse_warnings` text says "instance(s) unknown did not connect"
  when the bbox screen found nothing to blame (a feature seated *above* the
  part but within its bbox). That is honest — the free tier genuinely cannot
  name it — and the exact-probe escalation is what supplies the number.
