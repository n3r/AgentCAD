<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape. Weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40.

     Objective: maximise `bbox_z_mm` (the plate thickness). The reference
     solution — the outline trimmed to `chord_w` 50, `diag_w` 40,
     `edge_dist` 27, then thickened until the mass budget binds — measures
     **17.0 mm** at 2917.0270 g / 371595.7903 mm³ (the next whole millimetre,
     18 mm, reads 3088.6168 g / 393454.3662 mm³ and breaks the budget). The
     objective is a two-rung one-sided window derived from that measured value:

       objective_thickness          min = 17.0 / 1.05 = 16.19 mm  (full credit)
       objective_thickness_relaxed  min = 17.0 / 1.20 = 14.17 mm  (partial)

     A two-rung ladder because `metrics` is scored as the fraction of windows
     satisfied, and a single window would make the objective a cliff. Measured
     proof:

       reference (t 17, 50/40/27, steel_a36)  17.0 mm -> 1.0
       starter   (t 10, 80/60/30, steel_a36)  10.0 mm -> 0.765
       half-way  (t 15, 50/40/27, steel_a36)  15.0 mm -> 0.92
       starter re-materialled to al6061 (no geometry change) -> 0.765
       reference re-materialled to al6061 and taken to t 25 -> 0.85
         (the exploit the material rows exist for: it clears every
          objective window and loses `material_budget` +
          `material_density`)

     TWO DEVIATIONS from the design table (section 7.5), both argued.

     (1) The table's objective is "maximise the throat section area (check_that
     on metrics; window on `volume_mm3`)". Shipped: a window on `bbox_z_mm`,
     and no area predicate. Reasons, in order. The throat section at the work
     point is `chord_w x plate_t`, and NOTHING a spec predicate can reach
     measures it: `check_that` is handed the built shape and the metrics dict,
     and `chord_w` is not recoverable from either — the plate's Y extent is the
     convex hull of the chord band AND both diagonal strips, so it moves when
     the diagonals move. Sectioning the solid to measure the area would need a
     boolean inside a spec predicate, which is exactly what the kernel's spec
     tier does not do. `plate_t`, the other factor and the one that actually
     drives buckling stiffness, IS directly measurable as `bbox_z_mm`, so the
     objective is the half of the product that can be measured honestly.
     A `volume_mm3` objective was rejected on a second ground: the table pairs
     it with a `mass_g <= Y` constraint, and on a single material those are the
     same axis — "maximise volume" and "cap the mass" are a contradiction, not
     a trade. `volume_mm3` is still measured here, as `material_budget`, in the
     role that works: a density-invariant restatement of the budget.

     (2) The table's `plate_t <= 12`. With a starter at 10 mm and a ceiling at
     12 the objective has a 1.2x dynamic range and NO multiplicative window can
     separate the starter from the reference — the relaxed rung would sit at
     11.43 mm and the task would be a cliff with a two-millimetre answer. So
     the thickness ceiling is the parameter's own declared maximum (25 mm) and
     the binding constraint is the 3000 g mass budget, which is the other half
     of the table's row. The trade the task measures is unchanged: thickness is
     bought by trimming the outline.

     Why `material_budget` and `material_density` ride beside `mass_budget`:
     `mass_g = volume x density`, the density comes from the manifest material,
     and `update_part_script(material=...)` changes it in one call with no
     geometry touched. In `al6061` the plate could go to the parameter's 25 mm
     maximum and still weigh under 3000 g, which would delete the only
     constraint the objective trades against. 382165.6 mm³ is the same budget
     in the density-invariant unit (3000 / 0.00785).
-->

The project already holds the part `gusset_plate`, the A36 steel plate at a
truss panel point. It buckles before it yields, so the fabricator has been
asked for the stiffest plate the connection's weight budget will buy.

Objective: **make the plate as thick as you can.** You are scored on the
plate's thickness — thicker is better, and there is no target you have to hit
exactly. The material you save by trimming the plate's outline is what pays
for it.

Constraints, all of them graded:

- The plate must weigh **3000 g or less**. It weighs about 2516 g today.
- The material stays **A36 steel** (`steel_a36`, 0.00785 g/mm³), so the same
  budget also reads **382165.6 mm³ or less** of material. A lighter alloy is
  not a lighter design.
- The outer holes keep the code end distance of 1.5 × d = 27 mm, so the plate
  must still measure **at least 234.5 mm along X and at least 142.2 mm
  along Y**. (Trimming past that is what a smaller end distance looks like
  from the outside, and it is rejected.)
- The plate must stay inside a 273.2 x 176.8 x 25.2 mm envelope.
- The result must be one valid solid.

Not graded, but part of the design: the bolted connection itself does not
change — Ø18 mm holes, two rows per member at 45 mm pitch, and the two
diagonals still rising at 45°. What you may re-cut is the plate outline
(`chord_w`, `diag_w`, `edge_dist`) and its thickness.

Datum: unchanged. The plate lies flat with its back face on Z = 0 and its
material in +Z; the bottom chord runs along X, the chord band starts at Y = 0
and the two diagonals rise into +Y at 45°.
