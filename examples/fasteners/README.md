# Fasteners example — an M8 bolted joint

A minimal bolted joint that shows off `agentcad.toolkit.threads`: a clamp
plate is fastened to a tapped base plate by a socket-head cap screw.

- **`tapped_plate.py` — Tapped Base Plate** (Steel 4340). A square plate
  (`size`, `thickness`) with a blind M8x1.25 tapped hole down its top face.
  The hole is a plain clearance **counterbore** near the top (`counterbore`)
  opening into a *real* ISO internal thread (`thread_engage`) built with
  `threads.tapped_hole_thread`. The thread and counterbore depths are
  auto-clamped to fit inside the plate at any extreme. Corners are broken
  with the robust `safe_fillet` helper.
- **`clamp_plate.py` — Clamp Plate** (Aluminum 6061-T6). A square plate with
  a plain bolt **clearance** hole (`clearance_d`, default 9 mm — an M8 shank
  slips through). No thread: it is the clamped member.
- **`cap_screw.py` — M8 Socket-Head Cap Screw** (Steel 4340). Built with
  `threads.cap_screw("M8-1.25", length, simple=True)`. The size is a fixed
  catalog value; only `length` (under the head) is parametric. A *simple*
  (cosmetic) thread is used — fast and light, the right choice for an
  assembly / fit view.

## Real vs simple threads, and why the joint is interference-clean

The cap screw uses a **simple** thread and the base plate uses a **real**
internal thread — a deliberate mix. Two rules govern it:

1. **Mating threads always interpenetrate.** A male thread driven into a
   female thread of the same nominal size overlaps in solid geometry — that
   is *how threads grip*. So a fully-engaged bolt would always fail the
   interference check. Real CAD assemblies avoid this by not modeling both
   mating threads in contact.
2. **Bore a real internal thread at the ROOT (major) radius.** The thread
   ridges then protrude inward to the minor (crest) radius, adding visible
   material. Boring at the minor radius — the physical tap-drill size —
   buries the ridges in the wall (zero added volume, invisible thread).

This joint stays clean by construction: the cap screw's threaded shank sits
inside the plain **clearance counterbore** (radius 4.5 mm vs the M8 shank's
4.0 mm), and its tip stops ~1 mm **above** the tapped thread (which begins
6 mm down). The screw is seated and captured but not torqued into the
thread, so no two solids overlap. Raise the tapped plate's `counterbore`, or
shorten the screw's `length`, to keep that clearance if you retune the stack.

## Assembly

Three instances stacked on the Z axis:

- `tapped_plate_1` at the origin, its top face at z = 0, body below.
- `clamp_plate_1` resting on that top face (z = 0 .. 8).
- `cap_screw_1` at z = 8: bearing face on the clamp plate, head above,
  threaded shank down through the clearance hole into the counterbore.

## How an agent iterates on this project

Call `set_params` on `cap_screw` to change `length`, or on `tapped_plate` to
change `thread_engage`, then re-run `check_interference` to confirm the shank
still clears the thread, and `export_assembly` for a STEP snapshot. For a
manufacturing drawing of the screw, rebuild it with a real thread
(`simple=False`) and `generate_drawing`; for everyday fit checks keep the
cosmetic thread so rebuilds stay fast.
