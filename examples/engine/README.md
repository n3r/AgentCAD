# Engine example — a fully dressed 90° V4

A complete, dressed V4 engine: block, crank train, heads, intake with swept
runners, tubular exhaust manifolds, timing cover with water pump, harmonic
damper, flywheel, oil pan, oil filter, and coil-on-plug ignition — **13
parametric parts, 24 instances, interference-free** at a 20° crank angle.
It is the showcase for **declarative mates** (the heads, pan, crank, and
flywheel carry no hand-placed transforms) and for casting-level detail from
plain build123d: ribs, bosses, freeze plugs, stud patterns, bolt circles.

## Layout — one crankshaft of numbers

Global frame: the crank axis is **Y** through the origin, the V opens upward
in XZ, each bank tilts `bank_angle / 2` (default 45°) off Z. The two crank
pins sit at y = ∓40 (80 mm pin spacing), 180° apart, each carrying two rods
side by side — bank A's rod 9 mm ahead of the pin center, bank B's 9 mm
behind — so the banks are offset 18 mm and the cylinders land at y = −49
(A1), −31 (B1), +31 (A2), +49 (B2).

**The layout is exactly symmetric under a 180° turn about Z.** That single
fact dresses the whole engine: one `cylinder_head` script serves both banks
(bank B's seat connector carries the extra 180° so both intakes face the
valley), and one `exhaust_manifold` script serves both sides (instance B is
just `rotation_deg: [0, 0, 180]`).

Deck height is `stroke/2 + rod_length + 28` (28 mm = piston compression
height), so crowns sweep flush with the decks at TDC. Fits are engineered,
not decorative: 0.3 mm piston/bore, 0.25 mm big-end/pin, 0.3 mm
small-end/wrist-pin and main-journal/bulkhead, 0.4 mm on every gasket
(heads, pan, timing cover), 0.5 mm on every dress-part seat, and the
manifold flanges slide over the heads' real 17 mm-pitch stud patterns with
0.75 mm hole clearance.

## Mates do the assembly

- **`crankshaft_1`** mates its rigid `hub` into the block's **revolute**
  `crank_axis`. The mate's `angle` parameter *is* the crank pose: pin 1
  points `angle − 90` degrees past the vertical toward bank A, so
  `angle = 110` is a 20° crank angle — every piston mid-bore.
- **`flywheel_1`** rigid-mates to the *crankshaft's* `flange` connector — a
  mate chain, so driving the crank angle spins the flywheel (watch the ring
  gear).
- **`head_a` / `head_b`** rigid-mate onto seats the block derives from
  `bank_angle`/`stroke`/`rod_length`; re-angle or de-stroke the block and
  both heads follow. Bank B's seat encodes the 180° symmetry turn.
- **`oil_pan_1`** rigid-mates under the block's `pan_rail`.

The pistons and rods are the honest exception: their poses follow
slider-crank kinematics (a *function* of crank angle no rigid mate can
express) and are placed explicitly at the same 20°. For a bank whose axis
makes angle α with the pin direction, the pin-center distance along the
bore is `a + sqrt(L² − b²)` with `a = r·cos α`, `b = r·sin α`,
`r = stroke/2`, `L = rod_length`. The dress parts (intake, exhausts, timing
cover, damper, filter, coils) are modeled in the engine frame and sit at
identity — their scripts derive the mounting geometry from the same shared
constants (see each header).

## Parts

Core: **`engine_block`** (ribbed crankcase, three line-bored bulkheads,
freeze plugs, head-bolt holes, mount pads, bellhousing flange, drilled pan
rail), **`crankshaft`** (counterweighted webs, keyed snout, flanged rear
with pilot), **`piston`** ×4 (three ring grooves, valve pockets, integral
wrist pin), **`connecting_rod`** ×4 (I-beam blade, cap split line, rod
bolts kept inside the bay swing envelope), **`cylinder_head`** ×2 (port
pads with studs, plug tubes, ribbed cam cover, cam-drive humps),
**`flywheel`** (36-tooth ring gear, relief ring), **`oil_pan`**.

Dress: **`intake_manifold`** (four planar-swept runners into a log plenum,
throttle body), **`exhaust_manifold`** ×2 (swept primaries into a
collector + tail), **`timing_cover`** (silhouette-matched plate, seal boss,
water pump + pulley, cast bolt bosses), **`crank_pulley`** (damper with
V-grooves on the keyed snout), **`oil_filter`** (spin-on can on the block
boss, sized to the pocket between the bank-B slab and the pan rail),
**`ignition_coil`** ×4 (on the plug tubes).

## How an agent iterates on this project

Drive the crank: `set_mate` on `crankshaft_1` with a new `angle_deg` turns
the crank *and* the flywheel; re-pose the four pistons/rods via
`set_assembly` with the formula above, then `check_interference` — the
running clearances hold through the full revolution. Re-angle the V:
`set_params` on `engine_block` (`bank_angle`) carries both heads along
automatically (the manifolds are dimensioned for the 90° default — expect
`check_interference` to referee your redesign, that is its job).
Displacement study: sweep `bore`/`stroke` on the block, mirror them on
piston/crank (couplings: bore ↔ piston diameter + 0.6, pin_d ↔
big_end_bore − 0.5, stroke shared by block and crank), read `mass_g` from
`get_part`, and `export_assembly` STEP snapshots as you go.
