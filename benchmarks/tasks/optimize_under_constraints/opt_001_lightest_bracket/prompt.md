<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape, and demanding one would turn it into a copy
     exercise. The weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40.

     Objective: minimise `mass_g`. The reference solution (`thk` 6.0, every
     other parameter unchanged) measures **631.4818 g**. The objective is a
     two-rung one-sided window derived from that measured value:

       objective_mass          max = 1.05 x 631.4818 = 663.06 g   (full credit)
       objective_mass_relaxed  max = 1.20 x 631.4818 = 757.78 g   (partial)

     A two-rung ladder because `metrics` is scored as the fraction of windows
     satisfied, and a single window would make an optimisation objective a
     cliff: every candidate short of the target would score the same as one
     that changed nothing. Measured proof:

       reference (thk 6.0)  631.4818 g  ->  1.0
       starter   (thk 10.0) 1024.1152 g ->  0.866667  (fails both rungs)
       half-way  (thk 7.0)  731.5241 g  ->  0.933333  (fails the tight rung)

     One constraint the rubric states but cannot measure: the R6 inner fillet.
     `fillet_r` spans 4.3 g over its whole declared range (627.1692 g at R2 vs
     631.4818 g at R6, 0.7%), which is inside the objective's own 5% slack, so
     the unmeasured constraint cannot move the score.
-->

The project already holds the part `angle_bracket`, an L-shaped erection
bracket. The connection it makes is fixed; its weight is not.

Objective: **make the bracket as light as you can.** You are scored on the
part's mass — lighter is better, and there is no target you have to hit
exactly.

Constraints, all of them graded:

- The connection does not change: both legs stay 90 mm long, the bracket stays
  80 mm wide, and each leg keeps its **two Ø14 mm bolt holes**.
- The bracket must still fit the same envelope: no larger than
  90.3 x 80.3 x 90.3 mm, and no smaller than 89.5 mm along X, 79.5 mm along Y
  and 89.5 mm along Z.
- No wall thinner than **4 mm**, measured as `check_wall(min_mm=4.0, grid=4)`.
- The result must be one valid solid.
- The inner corner fillet stays at **R6** — it is the stress-raiser control at
  the corner and is not yours to spend.

Datum: unchanged. The inside corner of the L is at the origin, the horizontal
leg runs +X, the vertical leg runs +Z, and the 80 mm width runs along -Y (the
bracket spans y in [-80, 0]).
