<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape. Weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40.

     Objective: maximise `n_faces`, which on this part IS the bolt count.
     Measured across five builds, n_faces = 8 + n_bolts exactly (n=8 -> 16,
     n=16 -> 24, n=18 -> 26, n=21 -> 29, n=24 -> 32): the ring, bore, two
     chamfers and the two end faces are a constant eight, and every bolt hole
     adds exactly one cylindrical face. The reference solution (`n_bolts` 24 —
     the parameter's own declared maximum, on the shipped Ø118 bolt circle)
     measures **32 faces**. The objective is a two-rung one-sided window
     derived from that measured value:

       objective_bolt_faces          min = 32 / 1.05 = 30.48  (>= 31 faces,
                                                               >= 23 bolts)
       objective_bolt_faces_relaxed  min = 32 / 1.20 = 26.67  (>= 27 faces,
                                                               >= 19 bolts)

     A two-rung ladder because `metrics` is scored as the fraction of windows
     satisfied, and a single window would make the objective a cliff. Measured
     proof:

       reference (n_bolts 24)  32 faces -> 1.0
       starter   (n_bolts 8)   16 faces -> 0.866667  (fails both rungs)
       half-way  (n_bolts 21)  29 faces -> 0.933333  (fails the tight rung)

     `n_faces` is a proxy and is honest about being one: it counts bolt holes
     only while the candidate keeps the part's topology. That is why the
     ligament, bore and envelope constraints are rubric-owned spec rows rather
     than more face counting — they are what stops a candidate buying faces
     with geometry that is not a bolt hole.

     The ligament row is the trap that makes this an optimisation rather than
     "type the maximum": at n_bolts = 24 on the shipped Ø118 circle the
     grid-4 sampler reads 6.500 mm, but pushing the bolt circle out to Ø130
     (which the script clamps to a Ø127 circle) reads **0.972 mm** and is red.
-->

The project already holds the part `flange`, the chamber-head interface ring.
The joint leaks at the seal, and the fix is more bolts on the same flange.

Objective: **fit as many Ø9 mm bolt holes as you can.** You are scored on the
number of bolt holes — more is better, and there is no target you have to hit
exactly. The bolt circle is yours to move.

Constraints, all of them graded:

- The flange keeps its Ø140 mm outer diameter and its 14 mm thickness (the
  envelope is 140.3 x 140.3 x 14.2 mm and it may not read under 139.85 mm
  across or under 13.9 mm thick).
- The Ø87 mm bore stays — the ring still slips over the chamber barrel.
- Every bolt hole keeps at least **3 mm** of material to the bore, to the rim
  and to its neighbours, measured as `check_wall(min_mm=3.0, grid=4)`. This is
  the flange's own INT-003 requirement: crowding the bolt circle outward loses
  it long before the holes themselves touch.
- The result must be one valid solid.

Datum: unchanged. The flange's bottom face lies on Z = 0 and the ring rises
into +Z; the bore axis is the Z axis and the ring is centred on the origin in
X and Y.
