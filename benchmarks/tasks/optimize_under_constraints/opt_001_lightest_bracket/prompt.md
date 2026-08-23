<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape, and demanding one would turn it into a copy
     exercise. The weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40.

     WHAT BINDS: the 4 mm wall floor, not the parameter range. `thk` declares
     `min` 3.0 — a thickness this script builds cleanly and this connection may
     not have — and `check_wall(min_mm=4.0, grid=4)` is red below it. Measured
     across the range, grid=4 reads about 0.2 mm over the leg thickness itself:

       thk 3.0 -> 3.176 (red)   thk 3.5 -> 3.686 (red)   thk 3.8 -> 3.992 (red)
       thk 4.0 -> 4.196 (green) thk 4.5 -> 4.706 (green) thk 6.0 -> 6.235 (green)

     So the reference is `thk` 4.0: the lightest bracket whose wall is the
     4 mm the rubric asks for, one whole millimetre inside the range the script
     declares. Typing the end of the slider (`thk` 3.0) is a **0.925** — it
     clears every objective window and loses `leg_thickness`.

     The sampler's 0.2 mm of overread is measurement slack on the row and not a
     second requirement: `thk` 3.9 reads 4.094 and is green, so a candidate can
     sit 0.1 mm under the stated wall. It cannot OUTSCORE the reference by
     doing it — the objective windows are one-sided, so 3.9 and 4.0 are both
     1.0 — which is why the row is left at the requirement rather than lifted
     to chase the sampler.

     Objective: minimise `mass_g`. The reference solution (`thk` 4.0, every
     other parameter unchanged) measures **427.6291 g** and **54475.0446 mm³**.
     The objective is a two-rung one-sided window derived from that measured
     value, in BOTH units:

       objective_mass            max = 1.05 x 427.6291   =   449.01 g   (full)
       objective_mass_relaxed    max = 1.20 x 427.6291   =   513.15 g   (partial)
       objective_volume          max = 1.05 x 54475.0446 = 57198.80 mm³ (full)
       objective_volume_relaxed  max = 1.20 x 54475.0446 = 65370.05 mm³ (partial)

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
     **352.2434 g** and clears both mass rungs on unchanged geometry.

     Measured proof:

       reference (thk 4.0, steel_a36)   427.6291 g /  54475.0446 mm³ -> 1.0
       starter   (thk 10.0, steel_a36) 1024.1152 g / 130460.5317 mm³ -> 0.8
       half-way  (thk 4.5, steel_a36)   479.0633 g /  61027.1686 mm³ -> 0.9
       old range floor (thk 6.0)        631.4818 g /  80443.5403 mm³ -> 0.8
       new range floor (thk 3.0)        323.8188 g /  41250.7968 mm³ -> 0.925
         (every metric window green, `leg_thickness` red at 3.176 mm)
       starter re-materialled to al6061 (no geometry change)         -> 0.825

     One constraint the prompt states but the rubric does not measure: the R6
     inner fillet, which is why it is listed under "Not graded". Spending it is
     spending it DOWNWARD, and R2 at the reference thickness measures
     423.3165 g against R6's 427.6291 g — 4.3126 g, 1.0%, inside the
     objective's own 5% slack — so the unmeasured rule cannot move the score.
     (Growing it costs mass: R12 reads 442.1843 g.)
-->

The project already holds the part `angle_bracket`, an L-shaped erection
bracket in A36 structural steel. The connection it makes is fixed; its weight
is not.

Objective: **make the bracket as light as you can.** You are scored on the
part's mass — lighter is better, and there is no target you have to hit
exactly.

The parameter range is not the limit here: the leg thickness may be driven
thinner than this bracket is allowed to be, and **the wall floor below is what
stops you**, not the end of the slider.

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
