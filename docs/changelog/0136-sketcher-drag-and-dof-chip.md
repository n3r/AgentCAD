# 0136 — PRD-009 slice 10: drag-to-solve, the DOF chip, and the browser latency spike

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

The sketcher's `select` tool drags points and arc handles, solving on the
server every frame; the `solved · dof N` string becomes a **DOF chip** with
four states, a staleness marker and click-to-highlight. Both are built on the
plan's mandatory spike, and **the spike changed the design**: a real browser
misses the 16 ms budget on the round-trip path, so the plan's named fallback —
**client-side prediction** — is adopted, with the numbers for both paths in
hand.

This is AC2's UI half, AC4's UI half, and AC7.

## The spike (design risk 5), and what it decided

Every HTTP number in the design spec came from Python httpx: **0.72 ms p50**
with keep-alive, **12.55 ms p50 / 16.47 ms p95 without**. This drove **200
synthetic drag frames per configuration from a real headless Chrome, inside
the real app page** (project open, part selected, Three.js rendering through
SwiftShader, sketcher open) against a real server, reproducing Decision 9e's
frame protocol exactly: coalesce to one `requestAnimationFrame`, one request
in flight, full spec + `initial` from the previous frame + `drag`, then
re-render the SVG node-by-node the way `render()` does.

Paint is timestamped two ways because the difference is exactly one display
frame and that matters at a 16 ms budget: **1 raf** is the frame that will
show the change, **2 raf** the frame that provably has. This browser's rAF
cadence measured **8.30 ms** (≈120 Hz) — *faster* than a 60 Hz display, so
these numbers flatter the round-trip path rather than the reverse.

```
small GUI sketch (20 params, 20 rows, 1.7 kB)      p50      p95      max
  srv   (the solver's own solve_ms)               1.20     1.44     1.89
  net   (fetch issued -> JSON parsed)             4.20     6.00     7.30
  roundtrip e2e, pointermove -> painted (1 raf)  16.90    18.00    19.00
  roundtrip e2e, pointermove -> painted (2 raf)  25.00    26.20    27.00

FR6 size, bench-shaped (100 params, 100 rows, 8.3 kB)
  srv                                             7.01     8.31     8.85
  net                                            11.95    17.00    26.40
  roundtrip e2e (1 raf)                          25.40    32.30    50.40
  roundtrip e2e (2 raf)                          33.30    35.00    58.40

connection reuse: 200 requests per configuration, **0 new TCP connections**,
protocol http/1.1, in every configuration measured (600 requests in the first
run, 400 in the final one). Read from the page's own resource timings
(connectEnd > connectStart), which is what the network panel shows.
```

**Connection reuse is confirmed** — the design's named failure mode (12.55 ms
of connection setup per frame) does not occur in Chrome, and nothing on the
drag path breaks the pool. **The budget is missed anyway**, on frame
quantization plus the solve:

| where the frame goes | small | FR6 |
|---|---:|---:|
| coalesce (pointermove → the issuing rAF) | 8.30 ms p50 | 8.30 ms |
| net (Chrome `fetch` + FastAPI + solve) | 4.20 | 11.95 |
| paint (the response's own frame) | ~8.3 | ~8.3 |
| **e2e p95** | **18.0 ms** | **32.3 ms** |

`p95 > 16 ms` in both → **the plan's decision rule selects client-side
prediction**, and it was selected here, not pre-built.

**Chrome's `fetch` tax, isolated.** The same 200 frames driven by httpx
against the same running server, same sketches, same drag point:

```
                     httpx net / srv        browser net / srv     browser adds
10-seg,  1 DOF        6.63 / 4.75 ms         8.70 / 5.00 ms          ~2.1 ms
50-seg,  1 DOF       21.93 / 19.42 ms       24.80 / 19.79 ms         ~2.9 ms
```

So Chrome's `fetch` costs **2–3 ms more than httpx** on a pooled connection —
real, worth knowing, and small next to one display frame. The same sketches
solved **in-process** (no HTTP at all) cost 4.43 ms and 18.65 ms p50, matching
`srv` to within noise: the threadpool hop and the ASGI stack add nothing
measurable, and at 50 segments **the solve is the budget**.

**The fallback, measured rather than assumed.** With prediction the dragged
handle is written to the DOM *inside* the coalescing rAF callback, so it is
committed to that frame's paint. Pointermove → that callback measured **8.30
ms p50 / 9.20 ms p95 / 16.60 ms max** — one display frame, which is the floor
for any browser UI and is inside the budget at p95. The solved geometry
reconciles behind it at the `settle` rate (33.6 / 42.0 ms p50), which is
exactly the latency prediction exists to hide.

Spikes: `scratchpad/spike_browser_drag.py`, `spike_final.py`,
`spike_prediction.py`, `control_http.py`, `control_inproc.py`.

## Changes

- **The frame protocol** (`startDrag`, `scheduleDragFrame`, `sendDragFrame`,
  `endDrag`). `pointermove` stores the cursor and requests one animation
  frame; the frame paints the prediction and, **only if no request is in
  flight**, sends one; the existing `solveSeq` guard discards stale responses.
  The payload is the full spec plus `initial` **seeded from the previous
  frame's solution** (never the cursor — that is what flips the mirror branch)
  plus `drag {point, x, y}`, with `diagnostics` left at its default so the
  frame serves the cached block.
- **A press is not a click.** `onEntityPointerDown` records the press; more
  than `DRAG_PX` (3 px) of movement turns it into a drag, otherwise
  `pointerup` selects. Points, spline control points, arc and slot centres,
  and arc `.start`/`.end` handles are all draggable, because `drag` takes any
  ref the solver's `PointRef` can resolve.
- **The prediction, and the honest affordance it needs.** During a drag the
  canvas shows the solved geometry, a **ring at the cursor** (the predicted
  handle, painted with zero round trips) and a **dotted hairline between
  them** whenever the constraints are holding the point back. Drawing the
  handle at the cursor and calling it the geometry would be a lie the moment
  the sketch is fully constrained — and that case is not rare, it is the
  correct behaviour the design spent a measurement on. The hairline is
  `drag.gap` made visible.
- **On `pointerup`, one final non-drag solve with `diagnostics: "full"`**, so
  the chip describes the settled geometry rather than a cached block.
- **On error the drag ends and the model reverts** to the snapshot taken at
  `pointerdown`, `solveSeq` is bumped to discard anything still in flight, and
  the message is toasted.
- **The DOF chip** (`chipState`, `onDofClick`, `highlightSet`) replaces
  `solved · dof N`:
  - `fully constrained` (green) · `N DOF` (neutral; the tooltip names
    `free_entities`, clicking pulses them) · `over-constrained (n)` (amber,
    `redundant`) · `conflicting (n)` (red, from the error payload's
    `details.diagnostics`) · `unsolved` (red, with `max_residual`) ·
    `dof N · not analysed` when `analysis_complete` is false — "we did not
    look" never renders as "nothing found".
  - Clicking a conflict highlights **every** member: the constraint chips whose
    index is in the set gain `.flagged`, and a member compiled from an entity
    (`index: null`) is named by its `origin` in a toast, because there is no
    chip to point at.
  - The tooltip states, on every over-constrained state, that the set is *a*
    dependent set and not necessarily the unique culprit.
  - **`stale`.** While a drag is in flight the block is served from the
    solver's cache (`diagnostics_source === "cached"`), and the chip goes
    italic and dimmed to say so. A cached verdict presented as a fresh one is
    the quiet lie the `unverified` rule exists to prevent.
- **`seedFromModel` covers slots**, and that is not a detail. `initial` is
  all-or-nothing: a slot owns a radius parameter, so an `initial` that omits
  it comes back `warm_started: false` with an `initial_incomplete` warning and
  **the whole frame falls back to a cold start** — silently re-opening the
  mirror-flip door the soft pull exists to close. Reproduced against the route
  before the fix, and verified after it in a browser: 38 drag frames on a slot,
  `warm_started` true on every one, zero warnings, diagnostics cached
  throughout (`s10-m-slot-drag.png`).
- **The error path was broken and is now fixed.** `/api/sketch/solve` answers
  **HTTP 200 with an `{error: {type, message, details}}` envelope** for a
  tool-level failure and only *throws* for transport/HTTP failures. The first
  cut of this slice handled the thrown shape alone, so a contradictory sketch
  kept reading `fully constrained` while the server was returning
  `conflicting: [{index: 2, type: "distance"}]`. `errorOf(res, err)` +
  `failureState(error)` now normalise both shapes, and all three call sites —
  `solveAndRender`, `sendDragFrame` and `insertSnippet` — go through them.
  This is why the conflicting chip, the drag revert and the emission
  `validation_error` are each verified in a browser below rather than reasoned
  about.

## Files

- `frontend/js/sketcher.js` — the drag protocol, the prediction, the DOF chip,
  the highlight, the normalised error path
- `frontend/css/app.css` — `.sk-dof` and its five states, `.sk-chip.flagged`,
  the `sk-pulse` keyframes (with the existing `prefers-reduced-motion` opt-out)
- `docs/changelog/0136-sketcher-drag-and-dof-chip.md` — this entry

## Verification

Real browser, headless Chrome for Testing 1228 via Playwright, SwiftShader
WebGL, against a scratch server on port 52328 with a scratch projects dir. The
user's 8630 was never touched, and the server was stopped afterwards.

**AC2 (UI half) — the mirror triangle, 100 `pointermove` frames dragged
straight through `y = 0`, the branch boundary:**

```
frames sent 99   flips 0   y first 22.000003   y last 22.000003
net p50 2.70 ms   p95 3.80 ms   max 4.50 ms
new TCP connections 0
chip during the drag  'fully constrained'  (sk-dof ok stale)
chip after pointerup  'fully constrained'  (sk-dof ok)
diagnostics served    cached, every frame
```

The point does not move, and that is **correct** — it is fully determined by
its two distances, and the design's measurement is that the old "responsive"
behaviour was a teleport to the other solution. The screenshot
`s10-b-mid-drag.png` is the affordance working: the solved point stays put, a
dotted hairline runs 40 mm down to the cursor, and the amber ring at the cursor
is the prediction.

**A drag that really deforms** — a 3-DOF chain, 60 frames: the dragged tip
tracks the cursor from `(25, 20)` to `(73.0, 50.0)` monotonically in x, the
chain deforms continuously, no flip (`s10-i-deform-mid.png`).

**AC4 (UI half) and AC7 — the chip:**

```
under-constrained + solved   '3 DOF'                 title "still free: p2, p3"   2 nodes pulsing
fully constrained            'fully constrained'     sk-dof ok
redundant (duplicate dist)   'over-constrained (1)'  sk-dof warn   flagged chip index 2
conflicting (dist = 11)      'conflicting (1)'       sk-dof err    flagged chip index 2 ('dist p1–p3 = 11')
```

Both over-constrained tooltips carry "*a* dependent set, not necessarily the
unique culprit", and both flag the **later** constraint — the declaration-order
heuristic, visible in the UI.

**The emission round trip** — a closed 4-line profile with a circle, drawn in
the GUI, inserted with the button, saved through **Save & Rebuild**:

```
toast    'sketch inserted — call sketch_profile() from build(p)'
script   contains `def sketch_profile():`
rebuild  error None, volume 7200.0 mm^3  (a 60x40 profile extruded 3 mm)
```

**An emission warning reaching the user** — a full-turn arc (the `Arc` tool
with the end click on the start), whose endpoints coincide:

```
toast  'sketch inserted with 1 warning(s): an arc sweeping a full turn has
        coincident endpoints and cannot be written as a RadiusArc; emitted as
        CenterArc, which the reader derives from the rounded centre, radius
        and angles'
```

**Zero console errors in every session above.** (Chrome logs four
`GPU stall due to ReadPixels` *warnings* — SwiftShader software rasterisation,
not the app.)

Screenshots: `s10-a-dof-under`, `s10-b-mid-drag`, `s10-c-redundant`,
`s10-d-conflicting`, `s10-e-profile`, `s10-f-inserted`, `s10-g-saved`,
`s10-h-dof-pulse`, `s10-i-deform-mid`, `s10-j-deform-end`,
`s10-k-full-turn-arc`, `s10-l-emit-warning`.

`node --check` on both changed JS files. `make test-fast` → **1328 passed, 1
skipped** (259.20 s). **No Python changed in slices 9–10**, so the `slow`
chunks are untouched and the full-suite total stands at the **1646 passed, 1
skipped** recorded with its per-chunk table in
`docs/changelog/0134-sketch-drag-protocol.md`.

## Notes

- **The 2–3 ms of browser `fetch` tax is not the reason prediction was
  chosen** — the round trip fits. What does not fit is the round trip *plus*
  two display-frame boundaries, and on a 60 Hz monitor those cost 33 ms
  between them rather than the 16.6 ms measured here. Prediction removes both
  boundaries from the path the eye tracks and leaves the reconcile where it
  always was.
- **At the FR6 size the solve is the budget, not the transport.** 7.0 ms of
  the 11.95 ms `net` is `solve_ms`, and it is 19 ms on a 50-segment chain with
  a free DOF where the drag actually moves geometry (in-process: 18.65 ms —
  nothing to do with HTTP). Slice 8's bench measures the cheap case (a
  fully-constrained staircase dragged at `p9`, where the drag is nearly a
  no-op) at 6.72 ms and this run reproduces it at 7.01 ms. **A drag that moves
  a long chain by 12 mm is roughly 3× that**, and no browser-side change
  addresses it — it belongs to the solver.
- **The single-flight rule means frames are dropped, by design.** At 8.30 ms
  per rAF and a 12 ms round trip, roughly every second frame is skipped; the
  next one carries the newer cursor. The prediction is what makes that
  invisible.
- **Nothing on the drag path may break connection reuse**, and the comment
  saying so now lives on `api.solveSketch` next to the measurement. No
  `Connection: close`, no per-frame `AbortController`, no `sendBeacon`.
- **The `stale` chip state is not in the design spec** — it was added because
  the drag path serves cached diagnostics by construction, and the chip is the
  one place a user reads that verdict. It is the same rule as PRD-008's
  `unverified`, applied to a cache.
- A tangent chain junction reads `over-constrained (1)`; the measurement
  showing that this is a rank-tolerance artifact at a tangential solution, and
  the fix it points at, are in `docs/changelog/0135-sketcher-ui-entities.md`.
