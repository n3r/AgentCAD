<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape. Weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40.

     WHAT BINDS: two ligaments, not the parameter range. `n_bolts` declares
     `max` 48 — 48 holes is a slot, not a bolt circle, and the script builds it
     — and the answer is 32, sixteen short of the end of the slider, because
     the bolt circle is squeezed from both sides:

       * outward, by `bolt_circle_ligament` (`check_wall(min_mm=3.0, grid=4)`),
         which on this part reads the RIM. Measured at n_bolts = 32:
         Ø122.5 -> 4.154, Ø123.5 -> 3.447, Ø124 -> 3.094, Ø124.5 -> 2.740 (red),
         Ø125 -> 2.387 (red). The sampler walks out through the 1.5 mm rim
         chamfer, so it reads about 0.4 mm under the nominal rim ligament,
         and Ø124.2-ish is where the row turns.
       * inward, by `bolt_spacing`, which is the neighbour ligament: Ø9 holes
         3 mm apart is 12 mm centre to centre, so a circle of diameter D
         carries floor(pi*D/12) bolts. Ø123.5 carries 32 (12.125 mm pitch);
         a 33rd needs Ø126.05 or more, which the rim row reads at 1.998 and
         refuses.

     Reference: `n_bolts` 32 on a Ø123.5 circle — 40 faces, rim row 3.447,
     pitch 12.125. Both rows green with margin, and 33 bolts unreachable in
     either direction (Ø123.5/33 loses `bolt_spacing`; Ø126.1/33 loses
     `bolt_circle_ligament`; both score 0.9357).

     `bolt_spacing` is a `check_that` and not more `check_wall` because the
     wall sampler does not see the neighbour ligament AT ALL: measured, 42
     holes on a Ø124 circle leave 0.27 mm between neighbours and
     `check_wall(3.0, grid=4)` still reports 4.064 and passes it. Nor does a
     finer grid fix it — grid=16 reads 0.022 mm on EVERY variant, reference
     included, because it samples the chamfer. So the neighbour requirement is
     measured directly on the built part's hole centres (`arc_center`, never
     `center()`: a merged hole is a trimmed arc and its centre of mass is a
     point on the arc).

     Objective: maximise `n_faces`, which on this part IS the bolt count.
     Measured across builds, n_faces = 8 + n_bolts exactly (n=8 -> 16,
     n=24 -> 32, n=32 -> 40, n=42 -> 50): the ring, bore, two rim chamfers, two
     bore chamfers and the two end faces are a constant eight, and every bolt
     hole adds exactly one cylindrical face. The reference measures **40
     faces**. The objective is a two-rung one-sided window derived from that
     measured value:

       objective_bolt_faces          min = 40 / 1.05 = 38.10  (>= 39 faces,
                                                               >= 31 bolts)
       objective_bolt_faces_relaxed  min = 40 / 1.20 = 33.33  (>= 34 faces,
                                                               >= 26 bolts)

     A two-rung ladder because `metrics` is scored as the fraction of windows
     satisfied, and a single window would make the objective a cliff. Measured
     proof:

       reference (32 @ Ø123.5)  40 faces -> 1.0
       starter   (8 @ Ø118)     16 faces -> 0.866667  (fails both rungs)
       half-way  (28 @ Ø118)    36 faces -> 0.933333  (fails the tight rung)
       old range ceiling (24 @ Ø118) 32 faces -> 0.866667  (every rubric row
                                                  green, both rungs missed)
       new range ceiling (48 @ Ø118) 106 faces -> 0.804762  (the holes merge
                                                  into a slot: two solids,
                                                  `bolt_spacing` and
                                                  `bolt_pattern` both red)

     `n_faces` is a proxy and `bolt_pattern` is what keeps it honest: it
     requires at least four Ø9 holes and that the face count is exactly those
     holes plus the ring's own eight, so a candidate cannot buy faces with
     geometry that is not a bolt hole. Measured, that closes the cheapest
     rewrite of all — 41 holes at Ø5 on the shipped circle reads 49 faces and
     would otherwise be a 1.0; it scores 0.871429, because Ø5 is not Ø9.
-->

The project already holds the part `flange`, the chamber-head interface ring.
The joint leaks at the seal, and the fix is more bolts on the same flange.

Objective: **fit as many Ø9 mm bolt holes as you can.** You are scored on the
number of bolt holes — more is better, and there is no target you have to hit
exactly. The bolt circle is yours to move.

The parameter range is not the limit here: the bolt count can be driven far
past what this ring can carry, and **the ligaments below are what stop you**,
not the end of the slider.

Constraints, all of them graded:

- The flange keeps its Ø140 mm outer diameter and its 14 mm thickness (the
  envelope is 140.3 x 140.3 x 14.2 mm and it may not read under 139.85 mm
  across or under 13.9 mm thick).
- The Ø87 mm bore stays — the ring still slips over the chamber barrel.
- Every bolt hole keeps at least **3 mm** of material to the bore and to the
  rim, measured as `check_wall(min_mm=3.0, grid=4)`. This is the flange's own
  INT-003 requirement: crowding the bolt circle outward loses it long before
  the holes themselves touch the rim.
- Every bolt hole keeps at least **3 mm** of material to its neighbours,
  measured on the built part as centre-to-centre spacing: Ø9 holes 3 mm apart
  are 12 mm between centres.
- Every hole is **Ø9 mm**, and the ring carries nothing else: the score counts
  faces, so the part must read as the ring's own eight faces (top, bottom, rim
  and bore cylinders, and the four edge chamfers) plus exactly one face per
  bolt hole. A pocket, a counterbore, a smaller hole or a pair of holes merged
  into a slot is not a bolt.
- The result must be one valid solid.

Datum: unchanged. The flange's bottom face lies on Z = 0 and the ring rises
into +Z; the bore axis is the Z axis and the ring is centred on the origin in
X and Y.
