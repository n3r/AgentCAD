# Engine example v2 — assembly-first rebuild

**Branch:** `engine-example`. **Baseline:** commit e14eb5e (v1, display-level
solids). **Driver:** the v1 parts are fused primitive compositions — no
split lines, no fastener joints, no way to physically assemble a printed
set. This plan rebuilds the example the way an engine is actually built:
**every joint gets mating faces, an alignment feature, a bolt pattern, and
the fasteners themselves.**

## Research summary (what real construction demands)

Short-block order (King Engine Builders, ChevyDIY): bearings into saddles →
crank laid in from below → **main caps** bolted over the journals
(center-out) → piston+rod assemblies inserted from the deck top → **rod
caps** bolted from below. Head joint (Clark's Garage): gasket over **dowel
pins**, head bolts torqued in sequence into tapped bosses. Manifolds
(Summit): gasket per flange, manifold guided onto **studs/dowels**, nuts
torqued in sequence. SOHC head anatomy (engineassy.com): cam journaled in
**towers**, valves closed by springs held by **retainers/keepers**, cover
bolted over a rail. Printable engine kits (3DSets, EngineDIY, Thingiverse
LS3) split exactly these joints and fasten with M3-class screws — the
benchmark for "printable and assemblable".

Three v1 parts are physically *unassemblable* and must be redesigned, not
decorated: one-piece rods (can't get onto a one-piece crank), the integral
wrist pin (a closed small end can't slide onto a pin fixed at both ends),
and bulkheads with closed crank bores (the crank can't be installed at all).

## Architecture invariants (kept from v1 — verified working)

Global frame (crank axis = Y, 90° V opening up in XZ), bore 66 / stroke 60 /
rod 110, pin spacing 80, rod offset 9, deck at 168, R_z(180) bank symmetry,
slider-crank pose math, the mates showcase (revolute crank + chained
flywheel + seated heads/pan), and all running clearances (0.3 piston/bore,
0.25 big-end/pin, 0.3 mains).

Head-stack change: the head now sits on a real **0.8 mm gasket part**:
seat_s moves from deck+0.4 to **deck+0.9** (0.05 clearance each side of the
gasket). All scripts that hard-code SEAT_S follow.

## Joint table (the contract every part must honor)

| # | Joint | Interface | Fasteners (modeled) | Alignment |
|---|---|---|---|---|
| J1 | crank → block | 3 open saddles, half-bores at z=0 | — (captured by J2) | saddle bore |
| J2 | main caps ×3 → block | flat faces + half-bores | 6× M8 SHCS (1 bolt-set part) | register tongue/notch |
| J3 | rod cap ×4 → rod body | split faces through big-end center | 2× M6 SHCS per rod (4 bolt-pair parts) | bore halves + bosses |
| J4 | wrist pin ×4 → piston+rod | pin slides through boss–rod–boss | retaining-ring grooves (cosmetic) | bores 18.1/18.15 over Ø18 pin |
| J5 | head gasket ×2 → deck | flat, bore + bolt + dowel holes | — | 2 deck dowels |
| J6 | head ×2 → block | gasket sandwich | 6× M10 bolts per head (2 sets) | same dowels |
| J7 | camshaft ×2 → head | tunnel bores in 3 towers, slides in from front | front thrust plate screws (cosmetic holes) | journal Ø25.8 / Ø26 |
| J8 | valves+springs ×8/head → head | guide bores, spring seats | retainers + keeper cones (modeled) | guides |
| J9 | cam cover ×2 → head | rail face | 6× M5 per cover (2 sets) | plug tubes over wells |
| J10 | intake → both heads | per-bank flange, port bores | 8 nuts over the heads' studs (1 set) | studs |
| J11 | throttle body → plenum | round flange | 4× M6 (holes both sides) | spigot register |
| J12 | exhaust ×2 → head | per-port flanges | 8 nuts over studs (2 sets) | studs |
| J13 | pan → block rail | flat + gasket gap | 8× M6 (1 set) | matched drilled rails (v1 ✓) |
| J14 | timing cover → block front | flat + gasket gap | 8× M6 (1 set) | 2 front dowels |
| J15 | flywheel → crank flange | pilot register (v1 ✓) | 6× M8 (1 set) through both drilled patterns (v1 ✓) | pilot |
| J16 | damper → snout | keyed bore (v1 ✓) | center bolt + washer (v1 ✓) | key |

## Part list (≈30 scripts, ≈60 instances)

Reworked: `engine_block` (open saddles, cap seats+tapped holes, deck & front
dowels, tapped front pattern, head-bolt bosses), `connecting_rod` →
**rod_body**, new **rod_cap**, `piston` (open pin bores), new **wrist_pin**,
`cylinder_head` (valve seats/guides, spring seats, cam towers + tunnel,
tapped cover rail, no fused cover), new **cam_cover** (plug tubes live
here), new **camshaft**, new **valve_set** (8 valves+springs+retainers as
one compound), new **head_gasket**, `intake_manifold` (rounded-rect plenum,
tangent runners with port bores, one 4-hole flange per bank), new
**throttle_body**, `exhaust_manifold` (real flanges, lofted collector),
`timing_cover` (+dowel holes), unchanged: `crankshaft`, `flywheel`,
`oil_pan`, `crank_pulley`, `oil_filter`, `ignition_coil`.

Hardware (each a small script, one compound instance per joint):
`main_cap_set` (3 caps as separate instances + 1 bolt set), `head_bolt_set`,
`cover_bolt_set`, `pan_bolt_set`, `timing_bolt_set`, `flywheel_bolt_set`,
`rod_bolt_pair`, `intake_nut_set`, `exhaust_nut_set`. Cosmetic `simple=True`
cap screws from `agentcad.toolkit.threads`; nuts are hex prisms with bores.

## Kernel change

`handle_interference` gets an **AABB prefilter** (skip pairs whose bounding
boxes don't overlap, small tolerance). Pure speedup — bbox-disjoint solids
cannot intersect — needed because ~60 instances is ~1700 pairs.

## Phases (verify after each: part builds valid + targeted interference)

1. Plan + AABB prefilter (+ tests stay green).
2. Short block: block rework, main caps, cap bolts. Crank must drop into
   saddles; caps close the bores to 45.6 with 0.3 clearance on journals.
3. Rotating assembly: rod_body/rod_cap/rod_bolts, piston pin bores,
   wrist_pin.
4. Top end: head rework, camshaft, valve_set, cam_cover, head_gasket,
   head/cover bolt sets, deck dowels.
5. Induction/exhaust: intake rebuild, throttle_body, intake nuts, exhaust
   flange rework + nuts.
6. Closures: pan/timing/flywheel bolt sets, block front tapped pattern +
   dowels.
7. Integration: pose generator script → project.json (≈60 instances), full
   interference, param-extremes sweep, pytest, render, README as an
   assembly guide, changelog + commits.

Deferred (documented, not silently dropped): timing chain/sprocket drive
(enclosed by the cover), oil pump & pickup, water passages/jackets, fuel
rail + injectors, distributor/wiring, bearings shells as separate parts.

## Definition of done

Every joint in the table exists in geometry (faces, holes, fasteners,
alignment), every part is one printable solid, the assembly sequence in the
README is physically executable in order, all parts valid at all param
extremes, zero interference at defaults, `tests/test_examples.py` green.
