# 0035 — Engine example v2: assembly-first rebuild + interference AABB prefilter

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Nikita Fedorov / Claude

## Summary
Rebuilds the `engine` example (branch `engine-example`) from display-level
solids into an assemblable machine: 32 parts, 63 instances, every joint with
mating faces, alignment features, matched fastener patterns, and the
fasteners themselves. Also adds an AABB prefilter to the kernel's pairwise
interference check (63 instances = 1953 pairs; bbox-disjoint pairs skip the
boolean).

## Changes
- **Kernel** (`agentcad/kernel/worker.py`): `handle_interference` computes
  each placed shape's bounding box once and skips non-overlapping pairs
  (0.05 mm tolerance). Pure speedup; results unchanged.
- **Plan**: `docs/superpowers/plans/2026-08-09-engine-assembly-first.md` —
  research summary (short-block order, SOHC anatomy, printable-kit
  benchmark), the 16-row joint table, part list, phases, deferred items.
- **Short block**: block gets open crank saddles (cap windows with 0.25 mm
  side registers), tapped cap-bolt columns, deck dowel pins, a tapped front
  pattern + dowels for the timing cover, drilled pan rail (bolt line moved
  outboard of the pan tub), wider bays (R64) for the rod-bolt swing. New
  `main_cap` ×3 and `main_bolt_set` (6× M8).
- **Rotating assembly**: one-piece rod replaced by `rod_body` + `rod_cap`
  (split faces, tapped bosses, spot-faced cap) + `rod_bolt_pair` (2× M5,
  sized so the swing envelope clears the bays); piston's integral pin
  replaced by bored bosses + floating `wrist_pin` (an integral pin cannot
  accept a one-piece small end).
- **SOHC top end**: head rebuilt with valve seats/guides/spring pockets
  (+machined spring counterbores and rocker-relief pockets), cam saddles
  with bolted `cam_cap_set`, offset camshaft (a cam over central plug tubes
  is impossible), `rocker_set` finger followers (ratio 16/40), `valve_set`
  posed mid-cycle from the shared cam-phase table (two valves caught open),
  separate bolted `cam_cover`, real `head_gasket` on deck dowels,
  `head_bolt_set`, `cover_bolt_set`.
- **Induction/exhaust**: intake rebuilt — one full flange plate per bank
  over the heads' stud patterns, port bores through flange into runners,
  socket collars at the plenum, tapped front flange for a separate
  `throttle_body` (spigot register, butterfly, 4× M6); exhaust flanges get
  gas-path bores and a cone collector; `intake_nut_set`/`exhaust_nut_set`
  (M8 hex nuts on the studs).
- **Closures**: `pan_bolt_set`, `timing_bolt_set`, `flywheel_bolt_set` (the
  latter mate-chained to the crank flange so it spins with the crank);
  flywheel counterbores; crank front journal shortened + longer keyed snout
  clear of the cover's seal boss.
- project.json regenerated from a pose-generator script (slider-crank at
  20° crank + head-frame transforms + mates).

## Files
- `agentcad/kernel/worker.py` — AABB prefilter in handle_interference
- `examples/engine/parts/*` — 18 new part scripts, 10 reworked, 1 removed
  (`connecting_rod.py`)
- `examples/engine/project.json`, `examples/engine/README.md` (now an
  assembly manual), `README.md`
- `docs/superpowers/plans/2026-08-09-engine-assembly-first.md`

## Notes
The interference checker drove the design: ~60 real collisions were found
and fixed across five iterations, including three architectural ones (bulk-
heads that could never admit a crank, plug tubes through the cam, head-bolt
holes breaking into the bores). Deferred, documented in the plan: timing
drive, oil pump, water passages, bearing shells, fuel system.
