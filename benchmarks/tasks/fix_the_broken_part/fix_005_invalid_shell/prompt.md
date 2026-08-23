<!-- Weight override, argued (design §7.6). `geometry` is 0.00 here and its
     0.15 moves to `metrics` (0.10 -> 0.25). The reason is measured, not
     stylistic: this part's tube has G1-tangent face junctions at every bend,
     and OCCT 7.9's `BRepAlgoAPI_Common` can silently mis-answer a boolean
     between two such solids — usually empty, sometimes a negative volume —
     whatever their serialization (changelog 0308). It is not a STEP round
     trip defect: script-vs-script and STEP-vs-STEP "intersect cleanly" at
     21711.685 mm3, but that is operand sameness letting OCCT take a
     shortcut, not proof the boolean works. The candidate-vs-datum boolean
     the IoU handler has to take — a script solid against the checked-in
     STEP, two genuinely distinct operands — comes back `None`, i.e. an
     intersection of 0.0 mm3 between two solids of identical volume, and the
     kernel now detects and refuses this rather than banking it as a
     measurement (`agentcad/kernel/handlers/_bop.py`). A geometry weight here
     would therefore score EVERY submission zero on shape, the reference
     included, which is a broken rubric and not a hard task. `metrics`
     measures the same fact through the windows on mass, volume and the
     three bbox extents, and carries the weight instead. -->

The project holds one part, `coolant_elbow` — a thin-wall swept elbow. It
builds without an error, but the model is **not a valid solid**: the part's
own metrics come back with `is_valid: false`, and in the viewer the tube
visibly folds through itself on the inside of the bend. Downstream, the STEP
export is unusable.

**Fixed** means: `coolant_elbow` builds and comes back as **one valid solid**.
Keep the part id `coolant_elbow`.

The elbow, as it should be:

- an **Ø24 mm** tube with a uniform **3 mm** wall (Ø18 bore);
- two straight legs of **60 mm** centre-line each, meeting at a right angle;
- a **24 mm centre-line bend radius** at the corner — one tube diameter, the
  shop's minimum for this section.

Material: 6061 aluminium (unchanged).

Datum: the inlet is centred at the **origin** with the duct axis along **+X**;
the elbow turns upward and the outlet axis runs along **+Z** at X = 60. The
starter already sits on this datum and the fix must not move it.
