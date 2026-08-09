# 0023 — v2: threads & fasteners toolkit

- **Commit:** 57118fc
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
A thin `agentcad.toolkit.threads` wrapper over bd_warehouse (Apache-2.0) giving
part scripts ISO threads, threaded rods, tapped-hole threads, and cap-screw/
hex-bolt fasteners — with a simple-vs-real switch and guidance that steers
agents away from the slow `ThreadedHole` trap.

## Changes
- **`external_thread` / `internal_thread`**: direct `IsoThread` constructors
  (external=True/False) returning the raw thread solid; callers fuse onto a
  cylinder of `thread.min_radius`.
- **`threaded_rod(d, pitch, length)`**: builds a ready-to-use rod — an external
  thread fused onto a core cylinder at `min_radius`, returned as a build123d
  Part.
- **`tapped_hole_thread(d, pitch, depth)`**: returns the internal thread solid
  for a tapped hole; docstring shows the pattern (bore at `.min_radius`, then
  `add(thr)`) that avoids `bd_warehouse.fastener.ThreadedHole(simple=False)`,
  which takes ~15 s and does not auto-insert the thread.
- **`cap_screw` / `hex_bolt`** (`size="M8-1.25"`, `length`, `simple`): wrap
  `SocketHeadCapScrew` (ISO 4762) and `HexBolt`; `simple=True` is a fast/light
  cosmetic thread, `simple=False` is real geometry.
- Module docstring documents the triangle budget (`~9k` per real thread at
  tolerance 0.1 vs `~1k` cosmetic) and when to pick each; heavy imports
  (`build123d`, `bd_warehouse.fastener`) are deferred into the functions.

## Files
- `agentcad/toolkit/threads.py` — new threads/fasteners wrappers
- `tests/test_threads.py` — thread validity/speed, threaded-rod solidity, tapped-hole avoids the 15 s trap, simple-vs-real face counts

## Notes
Pure toolkit module — no new tool/route/handler; parts import it directly. Tests
build threads standalone (in-context `IsoThread` is ~250x slower) and assert the
tapped-hole path stays under 8 s.
