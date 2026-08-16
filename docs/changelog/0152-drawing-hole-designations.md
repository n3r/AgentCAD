# 0152 — 2026-08-13 — PRD-010 slice 6: drawing callouts from hole metadata (FR13, AC2, AC5)

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 6)

## Summary

`generate_drawing` now prints what a hole **is** rather than what it measures.
A hole drilled through `agentcad.toolkit.holes` carries a record; the drawing
handler already builds the shape, so it reads the records off it in-process
(no second kernel call, no service round trip), matches each one to a detected
circle group by diameter **and** centre, and prints the designation —
`M5×0.8 - 6H ↧12` where the projection could only ever have said `⌀4.20`.
Groups with no record keep the measured text and say `from_metadata: false`,
and a record the top view cannot show is **named** instead of dropped.

## What a callout looks like

Generated from a 120×120×12 plate with one M5 tapped hole, an 8-hole M6 bolt
circle and two M12 coarse-fit clearance holes (`scratchpad/slice6_drawing.py`,
rendered and viewed):

```
detected.hole_groups:
  {'diameter_mm': 4.2,  'count': 1, 'from_metadata': True,
   'designation': 'M5×0.8 - 6H ↧12', 'family': 'tapped',    'record_id': 'h0'}
  {'diameter_mm': 6.6,  'count': 8, 'from_metadata': True,
   'designation': '⌀6.6',            'family': 'clearance', 'record_id': 'h1'}
  {'diameter_mm': 14.5, 'count': 2, 'from_metadata': True,
   'designation': '⌀14.5',           'family': 'clearance', 'record_id': 'h2'}
detected.hole_warnings: []

SVG callout text, each on a leader to its own circle:
  M5×0.8 - 6H ↧12
  8× ⌀6.6
  2× ⌀14.5
```

The same plate hand-cut with `PolarLocations` + `Hole` renders `8× ⌀6.60`,
`from_metadata: false`, and no thread class anywhere — which is the whole
point: the drawing distinguishes intent from inference.

## Decisions worth naming

- **A match needs the diameter *and* the centre.** Diameter alone would let one
  of two same-diameter groups on a plate claim the other's circles and print
  the wrong designation — the exact silent mislabelling a drawing must not do.
  Tolerances: 0.05 mm on diameter (the PMI convention already in the file) and
  0.05 mm on centre. This is what slice 4's `centers` (global coordinates,
  9 dp) exist for; plane-local `positions` cannot answer it.
- **The `count >= 3` threshold applies to guessing, not to intent.**
  `_detect_circles` only emits a *geometric* group at three or more circles, so
  the single tapped hole above has no geometric group at all. A record is drawn
  whatever its count and creates its own group entry — made explicit rather
  than inherited by accident.
- **`n× <designation>`, and a lone hole is just the designation.** `1×` reads
  as a quantity someone forgot to finish, so the prefix appears only where it
  carries information.
- **PMI wins the slot where both describe the same circles.** PMI is *authored*
  tolerance intent and a record is a *derived* geometry fact (design Decision
  10); where a PMI diameter dim already annotated a group, the metadata callout
  is not drawn a second time. The group still reports `from_metadata` and the
  designation, so a reader can see where the text came from.
- **When the record and the geometry disagree, the record does not win
  silently.** No matching circle → no callout and a `hole_warnings` entry
  naming the record, its designation, its diameter and its instance count. Two
  records claiming one group with different designations → the group reports
  the first and warns about the collision. A malformed record → a warning, not
  an exception: shape is *enforced* in the `hole_records` harvest (slice 5), and
  a consumer that raised on residue would take a whole drawing down over one
  bad dict.
- **Top view only, and it is documented in three places** (handler docstring,
  `docs/agent-api.md`, and the warning text itself). A hole on a side face has
  a perfect record and no callout; a drawing whose `views` omits `top` names
  every record as undrawable. Fixing that is PRD-014's job, and partially
  patching it here would have been worse than stating it.

## Changes

- **`agentcad/kernel/handlers/drawing.py`**
  - `_records_on(shape)` reads the records in-process; `_record_problem`,
    `_match_record`, `_top_xy`, `_callout_text` are the new helpers, with
    `_HOLE_DIA_TOL` / `_HOLE_CENTER_TOL` named beside the reason for their
    values.
  - `_build_svg(..., hole_records=())` renders the callout column: PMI
    diameter dims first (unchanged), then one leadered callout per matched
    record, then the measured callouts for whatever is left. Leader targets use
    the same deterministic extreme-circle pick the PMI callouts already use, so
    two runs of one part put the leader on the same hole.
  - `detected["hole_groups"]` entries gain `from_metadata`, and when matched
    `designation`, `family` and `record_id`. A group's `count` stays the
    *detected* circle count where there is a geometric group (it is what is on
    the sheet); a metadata-only group carries the record's count.
  - `detected["hole_warnings"]` is new and always present on the SVG path.
  - **New behaviour for parts with no records:** a geometric group now renders
    a measured callout (`8× ⌀6.60`) where it previously rendered none. Before
    this slice the only diameter text on a sheet came from PMI.
- **`docs/agent-api.md`** — `generate_drawing`'s new `detected` fields, the
  designation behaviour, and the top-view limitation.

## Files

- `agentcad/kernel/handlers/drawing.py` — callouts from metadata
- `tests/test_drawing_holes.py` — **new**, 10 tests
- `docs/agent-api.md`
- `docs/changelog/0152-drawing-hole-designations.md` — this entry

## Notes

- DXF is untouched: it carries no annotation layer at all (v1), and a test
  pins that the records change nothing there.
- The drawing does **not** run the harvest's delta check. It reads whatever the
  shape carries; whether records went missing is the rebuild seam's question
  (slice 5), and asking it here would be a second, mostly-inert code path — the
  drawing's own build is usually a shape-cache hit.

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_drawing_holes.py tests/test_hole_metadata.py
33 passed in 6.67s
```

```
$ .venv/bin/python -m pytest -q -n 2 --dist loadscope -m "not slow"   # make test-fast
1848 passed, 1 skipped in 292.87s (0:04:52)
```

Full suite (`make test`, split into two chunks because one process exceeds this
sandbox's foreground time cap; `-n 2 --dist loadscope` is what the Makefile
runs):

```
$ .venv/bin/python -m pytest -q -n 4 --dist loadscope tests/ --ignore=tests/test_examples.py
2158 passed, 1 skipped in 344.49s (0:05:44)
$ .venv/bin/python -m pytest -q -n 2 tests/test_examples.py
20 passed in 920.97s (0:15:20)
```

**`make test`: 2178 passed, 1 skipped**, against slice 4's 2145 passed / 1
skipped — +33 for the two slices' 23 + 10 tests. No new skips, and no
pre-existing test moved.

The examples chunk is the one that would show the seam's cost: 21 of the 44
bundled part scripts mention `agentcad`, so each of their uncached rebuilds now
pays a `hole_records` round trip and the harvest-first ordering. Measured
**920.97 s against slice 4's 919.94 s** — inside the run-to-run noise, which is
what the per-part spike predicted.
