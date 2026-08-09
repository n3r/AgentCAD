# Engine example — an assemblable 90° V4

A complete SOHC V4 engine built **assembly-first**: 33 parametric parts, 65
instances, and every joint modeled the way a real engine bolts together —
mating faces, alignment dowels, gaskets, matched hole patterns, and the
fasteners themselves (72 screws and nuts, grouped into per-joint compound
parts). Print the set and it goes together in the order below; nothing is
fused that a wrench would need to separate. Zero interference at the posed
20° crank angle across all 1953 instance pairs.

## The layout

Crank axis = **Y**, 90° V opening up in XZ, bore 66 / stroke 60 / rod 110,
pins at y = ∓40 (180° apart), rods offset ∓9 per bank, cylinders at y =
−49, −31, +31, +49. Deck at `stroke/2 + rod_length + 28`; heads sit on a
real 0.8 mm gasket part (deck + 0.9). The layout is exactly symmetric under
a 180° turn about Z: one head, one exhaust manifold, and one nut-set script
each serve both banks.

Running fits are engineered throughout: 0.3 mm piston/bore, 0.25 mm
big-end/pin, 0.1 mm wrist-pin/piston and 0.25 wrist-pin/rod, 0.3 mm
main-journal/bore, 0.2 mm cam-journal/saddle, 0.05–0.5 mm on every static
seat.

## Threads are real; analysis uses their envelope

Every screw, and all 32 manifold studs, carry **real ISO thread geometry**
(`bd_warehouse` via the toolkit). Tapped holes run nominal + 0.4 and nut
bores nominal + 0.4 — mating internal threads are deliberately not modeled
in contact, per the rule the `fasteners` example demonstrates: engaged
thread pairs always interpenetrate in solid geometry. For
`check_interference`, each fastener script defines the optional
``analysis(p)`` contract hook: the same hardware with cosmetic
nominal-diameter shanks — a strict *superset* of the real thread, so a
clear check proves the real geometry clear, at a fraction of the boolean
cost (exactly how production CAD suppresses threads in analysis).

## Hollow where gas flows

The intake manifold is a casting with a real gas path: a shelled plenum,
annulus-swept runner tubes, and solid channel sweeps subtracted through
flange, runner, and plenum wall — look into a port and you see the plenum.
Runner/plenum junctions are fillet-blended, not butt-intersected. The
exhaust manifolds are shelled the same way (tubular primaries into a hollow
collector, open tail), and the throttle body's bore, butterfly, and shaft
sit in a genuinely open passage bolted over the plenum's spigot with
counterbored screws.

## The SOHC head — why the cam is offset

A cam directly over the chamber centers is geometrically impossible with
central spark plugs: the plug tubes would pass through the shaft. The
example uses the textbook SOHC answer — the cam sits **offset over the
exhaust side** (x = +24) in three saddles closed by bolted **cam caps**
(main-bearing style: an enclosed tunnel could never swallow the lobes), and
**finger-follower rockers** on a shaft at x = −16 carry the motion back to
the vertical valves at ratio 16/40. The valvetrain is posed mid-cycle,
consistently: crank 20° → cam 10°; each lobe's phase decides its rocker's
tilt and its valve's lift (two valves per head are caught open, springs
compressed), with running gaps preserved lobe→beam→valve-tip. The heads
even carry the machined reliefs real castings need where the open-valve
rocker tips dip below the deck line.

## Assembly order (the README is the build manual)

1. **Short block** — block inverted: lay the crankshaft into the three open
   saddles from below; fit the three `main_cap`s into their register
   windows (75.5 in 76); run the six M8 `main_bolt_set` screws up into the
   tapped bulkheads.
2. **Rotating assembly** — per cylinder: slide `rod_body` over a `piston`'s
   boss gap, push the `wrist_pin` through boss–rod–boss; feed the assembly
   down its bore from the deck; hang the big end on the crank pin; fit
   `rod_cap` (split faces meet, 0.05 modeled gap) and its two M5
   `rod_bolt_pair` screws from below.
3. **Top end** — per bank: drop the `head_gasket` over the two deck dowels;
   the head follows onto the same dowels; four M10 `head_bolt_set` screws
   through head + gasket into the tapped deck bosses. Lay the `camshaft`
   into its saddles, bolt the `cam_cap_set` over the journals; slide the
   `rocker_set` shaft through its pedestals; the `valve_set` stands in its
   guides and pockets.
4. **Closures** — `oil_pan` under the drilled rail (eight M6 up through the
   flange); `timing_cover` onto the two front dowels (eight M5 into the
   tapped front pattern); `cam_cover`s onto their tapped rails (six M5
   each), plug tubes landing over the wells; coils onto the tubes.
5. **Induction & exhaust** — `intake_manifold` flange plates slide over the
   heads' 17 mm-pitch studs (eight `intake_nut_set` nuts); each
   `exhaust_manifold` likewise (eight nuts per bank); `throttle_body` spigot
   into the plenum bore, four M6 into the tapped flange circle.
6. **Flywheel end** — flywheel over the crank's 30 mm pilot, six M8 through
   its counterbores into the flange circle (`flywheel_bolt_set` rides the
   same mate as the flywheel, so both spin with the crank); keyed
   `crank_pulley` onto the snout, washer and center bolt.

## Mates do the posing

`crankshaft_1` sits in a **revolute** mate on the block's `crank_axis`
(`angle = 90 + crank angle`; 110 → 20°). The flywheel *and its bolt set*
chain-mate to the crank's `flange` connector — drive the crank angle and
both rotate. Heads mate onto seats the block computes from its own
parameters (bank B's seat carries the 180° symmetry turn); pan on the rail
seat. Pistons, rods, caps, and pins are posed by slider-crank kinematics at
20° (`a + sqrt(L² − b²)` along the bank axis; the caps ride each rod's
frame). The valvetrain's poses are baked into the part scripts from the
shared cam-phase table.

## What's deliberately deferred

Timing drive (chain/sprockets, enclosed by the cover), oil pump and pickup,
water passages, bearing shells, fuel rail and injectors, piston rings as
separate parts. Each is listed in the plan
(`docs/superpowers/plans/2026-08-09-engine-assembly-first.md`) — none is
silently faked.

## How an agent iterates on this project

`set_mate` on `crankshaft_1` turns the crank, flywheel, and flywheel bolts;
re-pose pistons/rods/caps/pins via `set_assembly` with the slider-crank
formula, then `check_interference` (an AABB prefilter keeps 63 instances
fast). Unbolt visually by hiding instances — hide a `cam_cover` and the
whole valvetrain is on display. Couplings to keep in step when
re-dimensioning: bore ↔ piston diameter + 0.6 ↔ gasket rings; stroke shared
by block and crank; pin_d ↔ big_end_bore − 0.5; wrist pin ↔ piston/rod
bores; camshaft lift/phases ↔ valve_set ↔ rocker_set (the three scripts
share a constants table).
