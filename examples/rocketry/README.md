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

**Mated, not hand-placed.** The nozzle is the assembly datum (a root
instance at the origin). The flange and injector plate are positioned by
*mates* instead of hardcoded transforms: each part declares named rigid
connectors via `connectors(p, part)` — the nozzle exposes `flange_seat`
(z = -0.2) and `injector_seat` (z = +0.2), the flange exposes `top`
(its z = flange_t interface face), and the plate exposes `bottom` (z = 0).
The manifest then mates `flange_1.top → nozzle_1.flange_seat` and
`injector_plate_1.bottom → nozzle_1.injector_seat`; the service resolves
those to concrete positions ([0, 0, -14.2] and [0, 0, 0.2]) at read time,
so raising `flange_t` re-seats the flange automatically and the two
gasket clearances live in one place (the seats) rather than being baked
into every transform.

## Design specs — the intent, in code

The stated requirements are not in this README only; they are executable
declarations the kernel re-checks on every rebuild:

- `parts/nozzle.py` — `SPECS` carries `check_valid`, a `check_wall` minimum
  (ENG-014), the chamber `check_mass` budget of 1200 g (SYS-042, against
  1078 g as shipped) and a `check_that` fairing-envelope predicate.
- `parts/flange.py` — a `check_wall` named `bolt_circle_ligament` (INT-003):
  the bolt circle must keep real material to both the bore and the rim. The
  build *clamps* `bolt_circle_d` between them; the spec *measures* the result.
- `specs.py` — the assembly intent (INT-003): `check_interference_free`, plus
  the two gasket gaps as `check_clearance` pairs (flange-to-barrel,
  injector-plate-to-rim).
- `parts/injector_plate.py` deliberately declares **nothing** — a spec-less
  part costs zero extra kernel work and shows no chips.

`run_specs {"project": "rocketry"}` is green as shipped. Drag `wall` from
3.0 mm down to 2.0 mm and `nozzle:wall_min` goes red with its measured value
and the thin point's location — while the geometry still rebuilds, because a
failing spec is signal, not an error.

**One honest caveat, and it is the reason both wall checks pin `grid=4`:**
`check_wall` samples a UV grid per face and casts along the inward face
normal, so it finds the chamfered exit lip (0.2 × `wall` here) rather than the
barrel — at the shipped 3.0 mm wall it measures 1.02 mm. The limits above are
therefore stated in *measured* terms, taken from a real measurement. A finer
grid finds different (thinner) points; changing `grid` changes the number.

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

With the specs above, that loop gains a termination condition: every
`set_params` result carries a `specs` block, so "thin `wall` until mass fits"
stops being a judgement call — thin it until `mass_max` is green and
`wall_min` still is, then cite the green `run_specs` report as the evidence
that the change met the requirements it was asked to meet.
