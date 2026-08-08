# Prototyping example — snap-fit electronics enclosure

A two-part 3D-printable electronics enclosure in ABS: an open-top shelled
base that carries a PCB, and a lid whose inner lip seats into the base
cavity with a controlled clearance. It is the kind of bread-and-butter
prototyping part an agent iterates on daily: change a wall thickness or a
board size, rebuild, re-check the fit, export STEP for printing.

## Parts

### `enclosure_base.py`

Outer box (`length` × `width` × `height`), corner-filleted (`corner_r`) and
shelled open at the top with a uniform `wall` (walls + floor). Inside:

- **4 corner screw bosses** (`boss_d`) running from the cavity floor to the
  rim, each with a blind pilot hole (`pilot_d`) for self-tapping lid screws.
- **4 PCB standoffs** (`standoff_d` / `standoff_h`), centers inset
  `pcb_margin` from the inner walls, each with a small PCB-screw pilot hole.
- **Ventilation slots** (`n_vents` slots, 2 mm × 6 mm pitch) cut through the
  front long wall, automatically kept clear of the bosses and corner fillets.

### `enclosure_lid.py`

Flat plate (`lid_t`) matching the base footprint, with:

- an **inner lip** (`lip_h` deep, `lip_t` thick) inset **0.15 mm**
  (`LIP_CLEARANCE`) from the base's inner walls so it self-locates when the
  lid closes;
- **corner notches** in the lip (boss radius + 0.5 mm) so it clears the
  base's full-height screw bosses, plus a 0.2 mm relief pocket over each
  boss top;
- **4 countersunk screw holes** (`screw_d`, 90° countersink) aligned with
  the boss pilot holes — the boss-center formula is shared between the two
  scripts, so matching `length`/`width`/`wall`/`boss_d` keeps them aligned;
- an optional shallow **0.6 mm logo recess** on the top face (`emboss` flag).

Both scripts clamp dependent dimensions inside `build()` (fillet vs. wall,
boss/standoff placement, vent span, hole sizes), so every parameter builds a
valid solid across its whole documented min–max range.

## Assembly

`base_1` sits at the origin; `lid_1` is closed on top at `z = 30.1`, i.e.
the plate underside floats 0.1 mm above the base rim and boss tops while the
lip reaches 2.9 mm into the cavity. All mating clearances at defaults are
real gaps — 0.15 mm radial lip-to-wall, 0.5 mm radial lip-to-boss, 0.1 mm
axial — so the interference check reports zero overlapping pairs.

## Iterating with an agent

This project is built for tight agent loops: to make the lid fit snugger,
edit `LIP_CLEARANCE` in `parts/enclosure_lid.py` (e.g. 0.15 → 0.08 for a
printer with good tolerances), rebuild, and re-run `check_interference` —
the kernel verifies the lip still clears the walls and bosses before you
print anything. The same loop covers board swaps (`pcb_margin`,
`standoff_h`), hotter electronics (`n_vents`), or a sturdier shell (`wall`,
`lid_t`); if a change collides — say the lip now scrapes a boss — the
interference pairs name the offending instances and the overlap volume, so
the fix is one parameter tweak away. Export the assembly as STEP when the
check comes back clean.
