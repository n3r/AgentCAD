# Rocketry example — liquid engine thrust chamber study

A small liquid rocket engine thrust chamber assembly, modeled as three
parametric parts stacked along the engine axis (Z):

- **`nozzle.py` — Thrust Chamber & Nozzle** (Inconel 718). One revolved
  closed profile: a cylindrical combustion chamber (`chamber_d`,
  `chamber_l`), a tangent-arc converging section blending into the throat
  (`throat_d`, internally kept below 90% of the chamber diameter), and a
  15 deg conical near-bell diverging section whose exit area is
  `throat area x expansion_ratio`. The wall is a constant `wall` radial
  offset of the inner contour; the exit lip is chamfered.
- **`injector_plate.py` — Injector Plate** (Stainless 316). Circular plate
  (`plate_d`, `plate_t`) with a polar pattern of `n_orifices` propellant
  orifices (`orifice_d`) on a `pattern_r` pitch circle, and a filleted
  center igniter boss with an `igniter_d` through-port. The orifice ring
  is auto-clamped so it always clears the boss fillet and rim chamfer.
- **`flange.py` — Chamber Head Flange** (Stainless 316). Annular ring
  (`outer_d`, `inner_d`, `flange_t`) with `n_bolts` clearance holes
  (`bolt_d`) on `bolt_circle_d`; bore and rim edges are chamfered. The
  bolt circle is auto-clamped to stay between bore and rim.

## Assembly

The nozzle hangs below Z=0 with its injector interface rim at the origin;
the flange slips over the chamber barrel just below the rim (0.5 mm radial
clearance to the 87 mm bore); the injector plate caps the stack 0.2 mm
above the rim. Bolts and seals are not modeled. All stacked faces keep a
deliberate 0.2-0.4 mm clearance so the interference check is exactly clean;
in a fastened build those gaps would be gasket/seal allowances.

## How an agent iterates on this project

A typical loop: call `set_params` on `nozzle` to raise `expansion_ratio`
(say 4.9 -> 12), read the returned metrics — mass, bounding box, and
center of mass shift as the bell grows — then re-run `check_interference`
to confirm the longer bell still clears everything, and `export_assembly`
for a STEP snapshot. Because every mutation returns post-state metrics,
the agent can bisect toward a target (e.g. "keep nozzle mass under 2 kg
while maximizing expansion ratio, then thin `wall` until mass fits") in a
handful of tool calls, letting the kernel's validity and interference
checks referee each step.
