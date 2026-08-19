<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape, and demanding one would turn it into a copy
     exercise. The weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40.

     Objective: minimise `mass_g`. The reference solution (`thk` 6.0, every
     other parameter unchanged) measures **631.4818 g** and **80443.5403 mm³**.
     The objective is a two-rung one-sided window derived from that measured
     value, in BOTH units:

       objective_mass            max = 1.05 x 631.4818   =   663.06 g   (full)
       objective_mass_relaxed    max = 1.20 x 631.4818   =   757.78 g   (partial)
       objective_volume          max = 1.05 x 80443.5403 = 84465.72 mm³ (full)
       objective_volume_relaxed  max = 1.20 x 80443.5403 = 96532.25 mm³ (partial)

     A two-rung ladder because `metrics` is scored as the fraction of windows
     satisfied, and a single window would make an optimisation objective a
     cliff: every candidate short of the target would score the same as one
     that changed nothing.

     The `volume_mm3` pair is the DENSITY-INVARIANT twin of the `mass_g` pair,
     at the same ratios, and `specs/parts/angle_bracket.py` carries a
     `material_density` row beside it. Without them the objective is defeated
     without touching geometry: `mass_g = volume x density`, the density comes
     from the manifest material, and `update_part_script(material=...)` changes
     it in one call. Measured: the starter re-materialled to `al6061` weighs
     **352.2 g** and clears both mass rungs on unchanged geometry.

     Measured proof:

       reference (thk 6.0, steel_a36)   631.4818 g / 80443.5403 mm³ -> 1.0
       starter   (thk 10.0, steel_a36) 1024.1152 g / 130460.5317 mm³ -> 0.8
       half-way  (thk 7.0, steel_a36)   731.5241 g /  93187.7882 mm³ -> 0.9
       starter re-materialled to al6061 (no geometry change)         -> 0.825

     One constraint the prompt states but the rubric does not measure: the R6
     inner fillet, which is why it is listed under "Not graded". `fillet_r`
     spans 4.3 g over its whole declared range (627.1692 g at R2 vs 631.4818 g
     at R6, 0.7%), inside the objective's own 5% slack, so the unmeasured rule
     cannot move the score.
-->

The project already holds the part `angle_bracket`, an L-shaped erection
bracket in A36 structural steel. The connection it makes is fixed; its weight
is not.

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
- The material stays **A36 steel** (`steel_a36`, 0.00785 g/mm³). Mass is
  measured on the part you build, not on a lighter alloy: the same shape in
  aluminium is the same shape.
- The result must be one valid solid.

Not graded, but part of the design: the inner corner fillet stays at **R6** —
it is the stress-raiser control at the corner and is not yours to spend.

Datum: unchanged. The inside corner of the L is at the origin, the horizontal
leg runs +X, the vertical leg runs +Z, and the 80 mm width runs along -Y (the
bracket spans y in [-80, 0]).
