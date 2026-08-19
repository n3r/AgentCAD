<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape. Weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40. `interference` is 0.00 for the
     category, so the assembly requirement is carried by specs/project.py
     (`check_interference_free` + `check_clearance`), which is rubric-owned and
     scores into `specs`.

     Objective: minimise the cap screw's `bbox_z_mm` — the overall height of
     head plus shank, which is `length` + the M8 socket head's measured 8.0 mm.
     The reference solution (`length` 11.0, the shortest screw whose shank
     still projects 3 mm past the clamp plate) measures **19.0 mm**. The
     objective is a two-rung one-sided window derived from that measured value:

       objective_screw_height          max = 1.05 x 19.0 = 19.95 mm  (full)
       objective_screw_height_relaxed  max = 1.20 x 19.0 = 22.80 mm  (partial)

     A two-rung ladder because `metrics` is scored as the fraction of windows
     satisfied, and a single window would make the objective a cliff. Measured
     proof:

       reference (length 11)  19.0 mm -> 1.0
       starter   (length 20)  28.0 mm -> 0.641667  (fails both rungs AND both
                                                    project rows: the shank
                                                    overlaps the tapped thread
                                                    by 0.00549 mm³ and the
                                                    clearance reads 0.0 mm)
       half-way  (length 13)  21.0 mm -> 0.933333  (the example's own screw:
                                                    clear of the thread,
                                                    still 2 mm too long)

     DEVIATION from "the starter is the example at its shipped parameters",
     argued: the fasteners example ships `length` 13.0, which is 2 mm off the
     constrained optimum and passes every constraint. A starter that is already
     the answer measures nothing. 20.0 mm is the plausible wrong state the
     constraint exists to catch — a screw off the shelf that bottoms into the
     tapped thread — and it is inside the parameter's declared range (8..40).
     The half-way project above is the example's shipped 13.0 mm.
-->

The project already holds a bolted joint: the part `tapped_plate` (a base plate
with a blind M8x1.25 tapped hole), the part `clamp_plate` stacked on top of it,
and the part `cap_screw` dropped through the clamp plate's clearance hole.

The screw currently fitted is too long. This build is the fit-up stack: the
screw locates the clamp plate in the tapped plate's plain counterbore and must
stop short of the tapped thread, and the one fitted reaches into it.

Objective: **make the cap screw as short as you can.** You are scored on the
screw's overall height (head plus shank) — shorter is better, and there is no
target you have to hit exactly.

Constraints, all of them graded:

- The screw's shank must still project **at least 3 mm past the clamp plate's
  underside**. The clamp plate spans Z = 0 to Z = 8 and the screw's under-head
  bearing face sits on its top face at Z = 8, so measure it on the built screw:
  head plus shank must be long enough to reach Z = -3.
- The assembly must be interference-free.
- The screw must stay at least **1.0 mm** clear of `tapped_plate` at every
  point — it may enter the plain counterbore, never the thread.
- Change `cap_screw` only. `clamp_plate` and `tapped_plate` keep every
  parameter they have, and no instance moves.
- Keep the three instance ids exactly as they are — `tapped_plate_1`,
  `clamp_plate_1` and `cap_screw_1`. The clearance requirement above is
  measured between the named instances `cap_screw_1` and `tapped_plate_1`, so
  renaming or re-creating one is the same as deleting the check.
- It stays an M8 socket-head cap screw: the head keeps its Ø13.27 mm outside
  diameter, and the screw is one valid solid.

Datum: unchanged. The tapped plate's top face lies on Z = 0 and its body hangs
into -Z; the clamp plate sits on it spanning Z = 0 to Z = 8; the cap screw's
under-head bearing face is on the clamp plate's top face at Z = 8, its head
rises into +Z and its shank runs down -Z.
