# 0159 — 2026-08-16 — PRD-010 slice 13: hole-on-face on the existing face card (FR14)

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (PRD-010 slice 13)

## Summary

FR14's hole half. `add_holes` is the `push_pull`-shaped script-editing tool
that turns "M3 clearance holes here" into a visible `holes.clearance(...)` call
in the part script, and the viewport's existing face card grows the controls
that call it. **PRD-016, which the PRD names as the host, is unbuilt** — design
Decision 12 scoped this onto the card that already exists
(`frontend/js/main.js`, the push/pull host). The pattern dialog stays deferred
to PRD-016, which is where a direction/axis picker belongs.

The interesting part is what reaches the generated source. A picked face is a
**mesh-order ordinal**, which renumbers on any topology change; the tool
resolves it *now* through the `sketch_plane` handler and writes a literal
`Plane(origin=…, x_dir=…, z_dir=…)` with the caveat inline. A *named* plane
(`top`, `left`, …) stays a name, because a name is a predicate re-evaluated on
every rebuild — the stable reference. And nothing a caller types is
interpolated: every string is a key into a table this module owns and every
number goes through `repr(float(...))`, which is PRD-009's last gotcha
(a crafted `part` put `import os` on line 2 of a generated script) encoded as
a rule rather than remembered.

## What shipped

- **`agentcad/core/tools_holes.py` gains `add_holes {project, part_id, points,
  family, size, plane?, face_index?, fit?, std?, depth?}`.** It appends a
  marked, counter-suffixed block that rebinds `build` and calls the matching
  `toolkit.holes` helper, then persists through `service.update_part` so the
  rebuild, validation, event stream and history snapshot all ride the normal
  path. Composable: `_agentcad_prev_build_0`, `_1`, … so a second call cannot
  shadow the first.
  - `family` is a key into `_FAMILIES`, so only one of five names can reach the
    output; `size`/`fit`/`std` are validated by **looking the row up** in
    `hole_standards`, so an `M4.5` is a `validation_error` naming the tabulated
    sizes rather than a `script_error` on the next rebuild.
  - `family="drilled"` maps to `holes.drill` and takes a diameter in
    millimetres — the record then claims no table provenance, which is the
    whole point of that helper.
  - A non-planar or out-of-range face comes back as a `validation_error`
    (`sketch_plane`'s `contract_error`, converted at the tool surface the way
    `push_pull` converts `face_info`'s answer) and **the script is not
    touched**. A crash or a timeout is left exactly as it came.
  - The pack still imports no build123d: `..kernel.protocol` for the error
    constant is OCP-free, asserted by importing the module in a fresh
    interpreter with `OCP` and `build123d` blocked at `sys.meta_path`.
- **`frontend/js/main.js`** — `renderHoleControls` adds a *Hole* section to the
  face card (family / std / size / fit / depth / positions / **Drill**) and
  `applyAddHoles` calls the tool with the picked `face_index`, then clears the
  selection because the rebuild renumbers the ordinals. `holeStandardsFor(std)`
  fetches the size list from the **`hole_standards` tool**, once per standard,
  and caches it: there is no size list in JS, so the picker cannot offer a row
  the tables do not have. The form state lives outside the card so a re-render
  (the `face_info` answer landing) does not discard half-typed input.
- **`frontend/css/app.css`** — `.facecard-holes` / `.facecard-grid` /
  `.facecard-field`, reusing the existing `.param-select` / `.placement-num`
  controls.
- **`tests/test_tools_holes.py` (new, 29 tests)** — the appended block compiles
  and rebuilds; two calls compose and both records survive; the emitted basis
  equals `sketch_plane`'s component for component and carries the caveat; a
  named plane stays a name; every family emits a block that rebuilds; and the
  refusals, including a crafted identifier in each of `size`/`fit`/`std`/
  `plane`/`family`/a coordinate, after which `import os` is nowhere in the
  script.

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_tools_holes.py
29 passed in 7.40s
$ node --check frontend/js/main.js && node --check frontend/js/api.js
(no output — both parse)
```

**Real browser** — headless Chrome for Testing via Playwright with SwiftShader
WebGL, against a **scratch server on port 52095 with a scratch projects dir**
(a copy of `examples/prototyping`; the user's 8630 and `~/AgentCAD/projects`
were never touched). Every flow driven through the real pointer handlers:
click a face in the viewport, fill the card, press Drill.

```
A  clearance   4 x M3 at (±20, ±10) on face 66 (the cavity floor, 5006.6 mm²)
   toast  "Drilled 4 × M3 clearance on face 66"
   script holes.clearance(_agentcad_part, [(20.0, 10.0), (-20.0, 10.0),
                          (20.0, -10.0), (-20.0, -10.0)], 'M3',
                          plane=_agentcad_Plane(origin=(-2.44e-15, 8.19e-16,
                          2.5), x_dir=(1.0, 0.0, -0.0), z_dir=(0.0, 0.0, 1.0)),
                          fit='medium', std='iso')
   metrics 38,596 mm³ · 91 faces · 1 solid · valid

B  tapped      2 x M4 at (0, ±20) — a SECOND call on the same part
   toast  "Drilled 2 × M4 tapped on face 66"
   script both blocks present, _agentcad_prev_build_0 AND _1
   metrics 38,553 mm³ · 93 faces · 1 solid · valid

C  drilled     3 x ⌀4.5 — the number input, and the fit control correctly gone
   toast  "Drilled 3 × ⌀4.5 drilled on face 66"
   metrics 38,568 mm³ · 90 faces · 1 solid · valid

size picker options (from the tool, not from JS):
   M1.6 M2 M2.5 M3 M4 M5 M6 M8 M10 M12 M14 M16 M18 M20 M22 M24 M27 M30 M36

CONSOLE ERRORS: NONE  (all three sessions)
```

Screenshots: `shots13/ha1-configured.png`, `ha2-drilled.png`, `ha3-script.png`,
`ha4-metrics.png` and the `hb*` / `hc*` series for flows B and C. The `hb3`
shot shows both generated blocks in the CodeMirror editor and the four floor
holes plus two tapped holes in the viewport.

Full suite: see changelog 0160, which lands with it. `make test` totals are
stated there.

## Files

- `agentcad/core/tools_holes.py` — `add_holes`, `_FAMILIES`, `NAMED_PLANES`,
  `_points_literal`, `_plane_literal`, `_hole_call_args`, `_number`
- `frontend/js/main.js` — `HOLE_FAMILIES`, `holeStandardsFor`,
  `parseHolePoints`, `renderHoleControls`, `applyAddHoles`
- `frontend/css/app.css` — the hole section, plus a `min-width` on the face
  card's push/pull field
- `tests/test_tools_holes.py` — **new**
- `docs/agent-api.md` — the `add_holes` tool
- `docs/user-guide.md` — drilling from the face card
- `docs/changelog/0159-hole-on-face-ui.md` — this entry

## Notes

- **A one-line CSS fix outside the feature, and it is visible in the
  screenshot.** `.facecard-row .placement-num` had `flex: 1; width: 0` with
  three sibling buttons on a 236 px card, which shrank the push/pull distance
  field to a ~2 px sliver. `min-width: 46px`. Pre-existing, found by looking at
  the render rather than at the diff.
- **A numeric string coordinate is parsed, not interpolated.** `"20"` from a
  JSON client becomes `20.0` in the script, because the emitter writes
  `repr(float(...))` and never the caller's text — so being lenient about the
  input type costs nothing at the output. Asserted.
- **`plane` and `face_index` together are refused.** They are two different
  answers to "which face", and picking one silently would make the other look
  honoured.
- **A known gap, stated: the generated block drops the helper's warning
  string**, exactly as every bundled example does (`_warn`), because a part
  script has no channel to the rebuild's `warnings` list — `build_shape_ns`
  only ever emits parameter warnings. What is *not* lost is the machine-
  readable half: a missed instance still comes back in the record's
  `instances: [{"i": 2, "status": "missed"}]` on the rebuild result's `holes`
  key, and the harvest's own delta check still warns if the records go
  missing. A script-to-rebuild warning channel is a worker change and belongs
  with whoever needs it for all the `safe_*` helpers, not to this slice.
- Deliberately not here: the pattern dialog (PRD-016 — it needs a direction/
  axis picker), a point picker in the viewport (`hit.point` is not plumbed
  through `viewport.js`'s pick result, and `viewport.js` is outside this
  slice's file list), and previewing the hole before Apply.
