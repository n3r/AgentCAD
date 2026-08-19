<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape. Weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40.

     Objective: maximise `bbox_z_mm` (the plate thickness). The reference
     solution — the outline trimmed to `chord_w` 50, `diag_w` 40,
     `edge_dist` 27, then thickened until the mass budget binds — measures
     **17.0 mm** and 2917.0270 g (the next whole millimetre, 18 mm, weighs
     3088.6168 g and breaks the 3000 g budget). The objective is a two-rung
     one-sided window derived from that measured value:

       objective_thickness          min = 17.0 / 1.05 = 16.19 mm  (full credit)
       objective_thickness_relaxed  min = 17.0 / 1.20 = 14.17 mm  (partial)

     A two-rung ladder because `metrics` is scored as the fraction of windows
     satisfied, and a single window would make the objective a cliff. Measured
     proof:

       reference (t 17, 50/40/27)  17.0 mm  ->  1.0
       starter   (t 10, 80/60/30)  10.0 mm  ->  0.84      (fails both rungs)
       half-way  (t 15, 50/40/27)  15.0 mm  ->  0.92      (fails the tight rung)

     DEVIATION from the design table, argued: the table lists `plate_t <= 12`
     as a constraint. With a starter at 10 mm and a ceiling at 12 the objective
     has a 1.2x dynamic range and NO multiplicative window can separate the
     starter from the reference — the relaxed rung would sit at 11.43 mm and
     the task would be a cliff with a two-millimetre answer. So the thickness
     ceiling is the parameter's own declared maximum (25 mm) and the binding
     constraint is the 3000 g mass budget, which is the other half of the
     table's row. The trade the task measures is unchanged: thickness is
     bought by trimming the outline.
-->

The project already holds the part `gusset_plate`, the steel plate at a truss
panel point. It buckles before it yields, so the fabricator has been asked for
the stiffest plate the connection's weight budget will buy.

Objective: **make the plate as thick as you can.** You are scored on the
plate's thickness — thicker is better, and there is no target you have to hit
exactly. The material you save by trimming the plate's outline is what pays
for it.

Constraints, all of them graded:

- The plate must weigh **3000 g or less**. It weighs about 2516 g today.
- The bolted connection does not change: Ø18 mm holes, two rows per member at
  45 mm pitch, and the two diagonals still rise at 45°.
- The outer holes keep the code end distance of 1.5 × d = 27 mm, so the plate
  must still measure **at least 234.5 mm along X and at least 142.2 mm
  along Y**. (Trimming past that is what a smaller end distance looks like
  from the outside, and it is rejected.)
- The plate must stay inside a 273.2 x 176.8 x 25.2 mm envelope.
- The result must be one valid solid.

Datum: unchanged. The plate lies flat with its back face on Z = 0 and its
material in +Z; the bottom chord runs along X, the chord band starts at Y = 0
and the two diagonals rise into +Y at 45°.
