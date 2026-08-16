# nema23 — NEMA 23 stepper motor outline

The NEMA 23 motor envelope (56.4 mm frame) as one parametric part:
`motor`. Body length is the only thing that varies between motors of this
frame, so it is the only parameter.

| | |
|---|---|
| part id | `motor` |
| parameters | `body_length` (40–120 mm) |
| connectors | `face_mount` (rigid), `shaft` (cylindrical) |
| specs | validity, frame square, bolt pattern, shaft diameter, pilot-boss diameter |

## What this is, and what it is not

An **interface model**. The frame square, the chamfered corners, the four
tapped mounting holes on their bolt pattern, the pilot boss and the shaft are
what a bracket has to fit and clear. The stator laminations, the wiring gland
and the rear bearing cap are not modelled — **the body is a solid block**, so
this is an envelope for fit and interference, not a mass model. Do not read a
motor mass off it.

## Origin and orientation

The **mounting face is at z = 0**. The body runs back to
`z = -body_length` and the boss and shaft stand up in +z, so mating
`face_mount` onto a bracket face hangs the motor behind it exactly as it hangs
in a machine. `shaft` is the output axis, cylindrical, so a coupling or pulley
slides along it and turns on it.

## The interface

| | mm |
|---|---|
| frame square | 56.4 |
| bolt pattern (square, centre to centre) | 47.14 |
| mounting holes | M5 tapped |
| pilot boss diameter | 38.1 |
| shaft diameter | 6.35 |

Common body lengths: 51, 56 and 76.

The bolt-pattern spec finds the four mounting holes **by their own radius** and
measures them at their own axes, so it fails if a hole moves, if one is
missing, or if a fifth appears.

## Licence and trust

Apache-2.0. "NEMA 23" names a published frame size; no manufacturer's geometry
is reproduced and no affiliation is claimed.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
