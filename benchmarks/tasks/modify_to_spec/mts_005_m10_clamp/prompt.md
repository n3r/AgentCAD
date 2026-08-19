<!-- Weight override, argued (design §7.6): this is the one v1 modify_to_spec
     task whose project is an ASSEMBLY, and "the re-sized plate still stacks on
     the tapped plate without interfering" is half the requirement. So
     `interference` carries 0.10, taken out of `geometry` (0.30 -> 0.20): the
     shape still has to be right, but a candidate that grows the plate into the
     part below it has not met the spec. -->

The project holds a bolted joint: the part `tapped_plate` (a base plate with a
blind M8 tapped hole) with the part `clamp_plate` stacked on top of it. The
joint is being re-designed for an M10 bolt and a larger clamped footprint.

Requirement: `clamp_plate` becomes a **64 mm square, 10 mm thick** plate with
an **M10 medium-fit clearance hole (Ø11.0)**.

Constraints:

- Change `clamp_plate` only. `tapped_plate` keeps every parameter it has.
- The assembly must stay interference-free: the clamp plate sits on the tapped
  plate's top face and grows upward, never into it.
- Do not move either instance.

Datum: unchanged. The clamp plate's underside lies on Z = 0 and it extends into
+Z, centred on the origin in X and Y; the tapped plate's top face is also Z = 0
and its body hangs into -Z.
