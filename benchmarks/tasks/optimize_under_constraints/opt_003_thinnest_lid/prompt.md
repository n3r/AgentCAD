<!-- Grading provenance (design section 7.5), so a reviewer can reproduce every
     number in reference/metrics.json.

     Geometry (IoU) is `not_applicable` for this whole category: an optimisation
     has no unique correct shape. Weights are the category defaults
     0.10 / 0.05 / 0.45 / 0.00 / 0.00 / 0.40.

     WHAT BINDS: the fit, not the parameter range. `lid_t` declares `min` 1.0
     and `lip_h` declares `min` 0.8 — two layers and four layers of a 0.4 mm
     nozzle, which is what this script can print — and the rubric's own
     `plate_thickness` (2.0 mm above the underside) and `lip_depth` (1.5 mm
     below it) are red a whole millimetre inside that. Measured, all three
     rows the plate carries:

       lid_t/lip_h 1.0/0.8 -> plate_thickness RED, lip_depth RED, screw_holes RED
       lid_t/lip_h 1.2/1.0 -> plate_thickness RED, lip_depth RED, screw_holes RED
       lid_t/lip_h 1.5/1.2 -> plate_thickness RED, lip_depth RED, screw_holes RED
       lid_t/lip_h 2.0/1.5 -> all green (the reference)

     `screw_holes` goes red with them for a real reason and not a coincidence:
     the countersink is 90° at csk_r 3.0, so it needs 1.5 mm of depth, and
     under a 2 mm plate it breaks through — the Ø3 cylindrical hole is gone
     (48 faces at the reference, 44 below it) and the row that counts Ø3 hole
     edges reads zero. A lid thinner than its own screw seat is not a lid.

     So the reference stays `lid_t` 2.0 / `lip_h` 1.5 / `lip_t` 1.6 and the
     cheap play is gone: typing the end of both sliders (1.0 / 0.8) is a
     **0.775**, clearing every objective window and losing three of the six
     rubric rows.

     Objective: minimise `volume_mm3` (printed material). The reference
     measures **12207.5183 mm³**. The objective is a two-rung one-sided window
     derived from that measured value:

       objective_volume          max = 1.05 x 12207.5183 = 12817.89 mm³ (full)
       objective_volume_relaxed  max = 1.20 x 12207.5183 = 14649.02 mm³ (partial)

     A two-rung ladder because `metrics` is scored as the fraction of windows
     satisfied, and a single window would make the objective a cliff. Measured
     proof:

       reference (2.0 / 1.5 / 1.6)  12207.5183 mm³ -> 1.0
       starter   (3.0 / 3.0 / 2.0)  19086.9343 mm³ -> 0.84   (fails both rungs)
       half-way  (2.0 / 3.0 / 2.0)  13122.9343 mm³ -> 0.92   (fails the tight rung)
       range floor (1.0 / 0.8 / 1.6) 5968.3209 mm³ -> 0.775  (every window green,
                                                              three rubric rows red)

     The 0.05 mm of slack under each floor (1.95 / -1.45, not 2.00 / -1.50) is
     measurement slack on the requirement, not a different requirement:
     1.95/1.45 measures 11888.8990 mm³ and is green. It cannot outscore the
     reference — the objective window is one-sided, so both are 1.0.

     THREE DEVIATIONS from the design table (section 7.5), all argued.

     (1) The table's envelope is `check_bbox(within_mm=[100, 60, 10])`;
     shipped is `(100.3, 60.3, 6.3)`. X and Y are the same 100 x 60 footprint
     with 0.3 mm of measurement slack — `within_mm` is a ceiling and a bbox
     that reads exactly 100.000 against a 100.0 limit is a row decided by
     floating point. Z is tightened from 10 to 6.3 because 10 mm is not an
     envelope this part can reach: the starter, the thickest lid in the
     declared parameter range that still matches the base, reads 6.0 mm
     (`lid_t` 3.0 + `lip_h` 3.0), so a 10 mm ceiling could not fail any
     candidate and would be a row that neither passes nor discriminates. 6.3
     admits the starter with 0.3 mm to spare and refuses a lid grown past it.

     (2) The table lists `check_wall(min_mm=1.6)`. Measured on this part at
     grid=4 the sampler reports **0.200 mm on every variant in range** — it
     lands on the 0.2 mm BOSS_RELIEF recess in the plate underside, not on a
     wall — so a 1.6 mm
     floor would be red on the reference itself and a row that can neither
     pass nor discriminate. It is not shipped. The 1.6 mm lip wall is stated
     in the prompt as a design rule and is worth 228 mm³ over its whole range
     (12207.5183 at lip_t 1.6 vs 11979.5220 at lip_t 1.0, 1.9%), which is
     inside the objective's own 5% slack, so the unmeasured rule cannot move
     the score. That is why `lip_t` keeps its declared `min` of 1.0: widening
     a range nothing measures would only widen an ungraded rule.

     (3) `lid_t` and `lip_h` are shipped with `min` 1.0 and 0.8 rather than
     the example's 2.0 and 1.5. The example's minima were the fit requirement
     written into the slider, which made the answer to this task "read PARAMS,
     type the minimum" — the graded rows and the range floor were the same
     number. The script builds cleanly and valid below both (measured above),
     so the range now says what the geometry supports and the rubric says what
     THIS enclosure requires, which is the split the category is about.
-->

The project already holds the part `enclosure_lid`, the snap-fit lid of a
printed ABS enclosure. It is going into volume production and every gram of
filament is being counted.

Objective: **use as little material as you can.** You are scored on the lid's
material volume — less is better, and there is no target you have to hit
exactly.

The parameter range is not the limit here: the plate and the lip can both be
driven thinner than this lid is allowed to be, and **the fit constraints below
are what stop you**, not the end of a slider.

Constraints, all of them graded:

- The lid still fits the base: the footprint stays 100 x 60 mm (no smaller than
  99.5 x 59.5 mm, no larger than the 100.3 x 60.3 x 6.3 mm envelope).
- The seating lip must still reach at least **1.5 mm** below the plate's
  underside.
- The lid plate must stay at least **2.0 mm** thick above its underside.
- The four Ø3 mm countersunk screw holes stay — and they stay *holes*: the
  countersink needs 1.5 mm of depth, so a plate thinned past this is one the
  screw seat breaks through, and the row counts the Ø3 hole edges on the part
  you build.
- The result must be one valid solid.

Not graded, but part of the design: the screw holes stay on the base's boss
centres (only their count and diameter are measured), and the lip wall must not
go below 1.6 mm — below that it will not survive a 0.4 mm nozzle.

Datum: unchanged. The lid plate's underside lies on Z = 0 and the plate rises
into +Z; the seating lip hangs into -Z; the 100 mm length runs along X, the
60 mm width along Y, and the lid is centred on the origin in X and Y.
