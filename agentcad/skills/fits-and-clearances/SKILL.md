---
name: fits-and-clearances
description: Sizing mating features — ISO 286 hole-basis fits (H7 with g6/h6/k6/p6), printed-part clearances, bearing seats, dowel pins and heat-set insert holes.
triggers: [fit, clearance, tolerance, h7, g6, k6, p6, press, slip, sliding, bore, pin, dowel, bearing, insert, heat-set, interference, shaft, iso 286, reamed]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

Use this when two features have to mate — a pin in a bore, a bearing in a housing, a tongue in a groove, an insert in a boss — and you need the actual numbers: ISO 286 hole-basis fits for machined parts, the very different clearances printed parts need, and the common hardware seats. It does **not** cover fastener clearance holes (that is `holes` and the hole wizard's `fine/medium/coarse`, a different table), thread fits (`threads-and-fasteners`), snap-fit interference (`snap-fits`), or FDM process rules beyond fit (`fdm-design-rules`).

## Pick the fit first

| Function | Hole basis | Character | Typical use |
| --- | --- | --- | --- |
| Turns or slides freely, lubricated | **H7/g6** | clearance 0.005–0.029 mm at Ø10 | shaft in a plain bearing, sliding pin, hinge |
| Assembles by hand, locates, comes apart | **H7/h6** | clearance 0–0.024 mm at Ø10 | spigots, locating diameters, removable pins |
| Snug, no play, light force to fit | **H7/k6** | transition, −0.010…+0.014 mm at Ø10 | dowels, clamped bearing seats |
| Stays put, carries load through the joint | **H7/p6** | interference 0–0.024 mm at Ø10 | pressed bushings, gear/pulley hubs |
| Loose, dirty, painted, welded | H11/c11 | clearance 0.080–0.260 mm at Ø10 | brackets, sheet parts, agricultural |

**Hole basis** (the hole is always `H`, EI = 0) because a hole comes out at the fixed size of a reamer, drill or broach while a shaft is easy to turn or grind to any size. Use shaft basis (`h` shaft, lettered hole) only when the shaft is bought stock — drawn bar, a bearing journal, a linear rail.

## ISO 286 in one page

The **basic size** is the number both parts share. Each part gets a *fundamental deviation* (the letter — which side of basic its band sits on) and a *standard tolerance grade* (the IT number — how wide the band is).

- Hole `H`: `EI = 0`, `ES = +IT`. So `H7` at Ø10 is `10.000 / 10.015`.
- Shaft `a…h`: the letter gives `es` (upper), `ei = es − IT`. Shaft `j…zc`: it gives `ei`, `es = ei + IT`.
- Max clearance `= ES − ei`; min clearance `= EI − es`. A negative min clearance is interference.

Standard tolerance grades, µm (ISO 286-1:2010):

| Nominal, mm | IT6 | IT7 | IT8 | IT9 | IT10 | IT11 |
| --- | --- | --- | --- | --- | --- | --- |
| over 3 to 6 | 8 | 12 | 18 | 30 | 48 | 75 |
| over 6 to 10 | 9 | 15 | 22 | 36 | 58 | 90 |
| over 10 to 18 | 11 | 18 | 27 | 43 | 70 | 110 |
| over 18 to 30 | 13 | 21 | 33 | 52 | 84 | 130 |
| over 30 to 50 | 16 | 25 | 39 | 62 | 100 | 160 |

Fundamental deviations, µm (ISO 286-1:2010):

| Nominal, mm | H (EI) | g (es) | h (es) | k (ei) | p (ei) |
| --- | --- | --- | --- | --- | --- |
| over 3 to 6 | 0 | −4 | 0 | +1 | +12 |
| over 6 to 10 | 0 | −5 | 0 | +1 | +15 |
| over 10 to 18 | 0 | −6 | 0 | +1 | +18 |
| over 18 to 30 | 0 | −7 | 0 | +2 | +22 |
| over 30 to 50 | 0 | −9 | 0 | +2 | +26 |

`k`'s deviation above holds for grades IT4–IT7; at IT3 and finer or IT8 and coarser, `k` has `ei = 0`. The machine-readable copy of both tables, plus the resulting clearance range of every H7 fit in every band, is `tables/iso286.json`.

**The range trap:** bands are *over … up to and including*. Ø10 is in `over 6 to 10`, Ø30 in `over 18 to 30`. Reading Ø10 one band up turns IT7 from 15 µm into 18 µm — 20 % of the whole fit.

### Worked example — Ø10 H7/g6

1. Band: `over 6 to 10`. IT7 = 15 µm, IT6 = 9 µm, `g` es = −5 µm.
2. Hole H7: `10.000 / 10.015`.
3. Shaft g6: `es = −5` → `9.995`; `ei = −5 − 9 = −14` → `9.986`. So `9.986 / 9.995`.
4. Min clearance `= 10.000 − 9.995 = 0.005 mm`; max `= 10.015 − 9.986 = 0.029 mm`.

CAD carries **one** number per feature: model the basic size (or the mid-band, as the snippet does) and put the limits in the callout or a `SPECS` entry — geometry is not where a tolerance lives.

## Printed parts: the process is the tolerance

Diametral values in mm — the gap you must model between the two nominal diameters. **Diametral, not radial:** half of it appears on each side, and halving it twice is the single most common printed-fit bug.

| Process | Sliding | Locational | Light press | As-printed hole |
| --- | --- | --- | --- | --- |
| FDM, 0.4 mm nozzle | 0.20–0.40 (start 0.30) | 0.20 | 0.10–0.15 interference | 0.1–0.3 mm **undersize** |
| SLA / DLP | 0.10–0.20 | 0.10 | 0.05 interference | ~0.05 undersize |
| SLS, PA12 | 0.30 | 0.20 | 0.10 interference | ~0.10 undersize, plus trapped powder |
| Machined | ISO 286 above | — | — | reamed to size |

- **Compensate the hole.** An FDM Ø5.0 bore measures ~4.7–4.9 (extrusion width, corner overshoot, shrink). Model it 0.1–0.3 mm oversize — or 0.5 mm undersize and drill/ream it after printing when it must truly be round.
- **Orientation decides roundness.** A bore printed axis-vertical is round; printed on its side it sags at the top and has an elephant-foot lip at the bed. Real bearing and shaft seats print axis-vertical.
- **An H7 callout on an FDM part is fiction.** IT7 at Ø20 is 21 µm; FDM spread is ±0.2 mm — ten times wider. Tolerance the function, not the drawing: decide what the feature must *do*, print one fit coupon (bores stepping 0.05 mm), set the parameter from what fits.
- **Printed press fits creep.** Plastic relaxes over days under interference; if the joint must hold torque, add a screw, key or flat.

## Bearings, pins, inserts

**608 bearing** (8 × 22 × 7 mm; ISO 15 boundary dimensions, ISO 492 Normal class rings are minus-toleranced, bore and OD both `0 / −0.008`):

| Seat | Machined | Printed |
| --- | --- | --- |
| Housing Ø22 | **H7** = `22.000 / 22.021`, stationary outer ring, light load, non-split housing | model Ø22.0, measure, aim 0–0.10 mm interference; lead-in chamfer + clamp slot |
| Shaft Ø8 | **h6** = `7.991 / 8.000` stationary inner ring; **j6/k6** when it rotates under load | use a steel shaft or an M8 bolt — printed shafts flex and wear |
| Shoulder | contacts the ring face only, never the shield or seal | same |

**Dowel pins.** A hardened parallel pin (ISO 2338, `m6`) in a reamed `H7` hole is a transition fit: it locates to a few µm and still presses out; use `H7/p6` for a permanent one. Two pins locate a joint, but the second hole must be a **slot** (or a diamond pin) or hole-spacing tolerance over-constrains the pair.

**Heat-set inserts** for thermoplastics — typical brass knurled inserts; the vendor datasheet always wins:

| Thread | Insert OD × length | Hole Ø | Min boss OD |
| --- | --- | --- | --- |
| M2 | 3.2 × 4.0 | 3.2 | 6.5 |
| M2.5 | 3.5 × 5.7 | 3.5 | 7.0 |
| M3 | 4.0 × 5.7 | **4.0** | 8.0 |
| M4 | 5.6 × 8.1 | 5.6 | 11.0 |
| M5 | 6.4 × 9.5 | 6.4 | 13.0 |

The hole is straight (no taper), ~1 mm deeper than the insert so displaced plastic has somewhere to go; boss OD ≥ 2 × insert OD with ≥ 1.5 mm of wall. **Do not add the FDM shrink compensation on top** — the vendor figure is already an as-printed diameter, and that undersize is the interference the melting insert consumes.

## Express the fit in PARAMS

Never hard-code a diameter pair. Take the basic size plus a `fit` enum and compute both members in `build(p)`:

```python
_BANDS = ((3.0, 6.0), (6.0, 10.0), (10.0, 18.0), (18.0, 30.0), (30.0, 50.0))
_IT6 = (8, 9, 11, 13, 16)
_IT7 = (12, 15, 18, 21, 25)
_DEV = {"sliding":    ("es", (-4, -5, -6, -7, -9)),     # g6
        "locational": ("es", (0, 0, 0, 0, 0)),          # h6
        "transition": ("ei", (1, 1, 1, 2, 2)),          # k6
        "press":      ("ei", (12, 15, 18, 22, 26))}     # p6

PARAMS = {
    "bore": {"default": 10.0, "min": 3.0, "max": 50.0, "unit": "mm",
             "description": "Basic diameter shared by bore and shaft"},
    "fit": {"default": "sliding", "type": "enum",
            "choices": ["sliding", "locational", "transition", "press"],
            "description": "H7/g6, H7/h6, H7/k6, H7/p6"},
}

def _band(nominal):
    for i, (lo, hi) in enumerate(_BANDS):
        if lo < nominal <= hi:              # over ... up to and including
            return i
    return 0 if nominal <= _BANDS[0][1] else len(_BANDS) - 1

def _iso_sizes(nominal, fit):
    i = _band(nominal)
    it6, it7 = _IT6[i] / 1000.0, _IT7[i] / 1000.0
    which, values = _DEV[fit]
    dev = values[i] / 1000.0
    ei = dev if which == "ei" else dev - it6
    return nominal + it7 / 2.0, nominal + ei + it6 / 2.0   # bore, shaft (mid-band)

def build(p):
    bore_d, shaft_d = _iso_sizes(p.bore, p.fit)
    ...
```

The printed branch is the same shape with a flat table instead of ISO 286:

```python
_FDM = {"sliding": 0.30, "locational": 0.20, "transition": 0.05, "press": -0.12}

def _printed_sizes(nominal, fit, hole_shrink=0.20):
    return nominal + hole_shrink, nominal - _FDM[fit]      # bore, shaft
```

`snippets/pin_and_bore.py` is the complete part: block, bore, mating pin, `fit` and `process` enums, `SOLID_LABELS = ["block", "pin"]`.

## Checklist and failure modes

- [ ] Named the *function* (turns / locates / stays), then picked the designation — not the reverse.
- [ ] Nominal read from the right band (over … up to and **including**).
- [ ] Clearance stated diametral, applied once.
- [ ] Lead-in chamfer 0.5–1 mm at 30–45° on the entering member — an assembly aid, never part of the fit.
- [ ] Printed: hole compensated, bore axis vertical, one fit coupon measured.
- [ ] Two locating features → one hole plus one slot.

Recovering in build123d:

- **A fit modelled as zero clearance is a degenerate boolean** — coincident cylindrical faces are exactly what OCCT fuses unpredictably or refuses. Model each member at its own size and record the press in a spec; `selectors-and-occt-failures` has the playbook and `safe_bool`.
- **Use `holes.drill(part, points, diameter)` for a fit bore** — millimetres, no fastener table behind it (right: a Ø22 bearing seat is not an ISO 273 row), and its record reaches the drawing callout. `holes.clearance(..., "M5", fit="medium")` is fastener clearance, a different standard — not a way to size a bearing seat.
- **A mouth chamfer that fails** is usually eating past the wall: reduce the length (`min(0.6, d / 12)`) and wrap the call, or fillet with `safe_fillet`, which searches down to a radius that works.
- **A bore that vanished** was cut before the material existed — drill after the last fuse, then re-check `metrics.n_solids`.

## Sources

- ISO 286-1:2010, *ISO code system for tolerances on linear sizes — Part 1: Basis of tolerances, deviations and fits* — IT grades and fundamental deviations.
- ISO 286-2:2010, *Part 2: Tables of standard tolerance classes and limit deviations for holes and shafts* — the limit tables above and in `tables/iso286.json`.
- ISO 2338:1997, *Parallel pins, of unhardened steel and austenitic stainless steel* — m6 / h8 pin tolerances.
- ISO 15:2017 (rolling-bearing boundary dimensions) and ISO 492:2014 (radial-bearing GPS and tolerance values).
- SKF *Rolling Bearings* catalogue, "Bearing seats — tolerances and fits" — shaft/housing class by load condition and which ring rotates.
- *Machinery's Handbook*, 31st ed., Industrial Press — "Preferred Metric Fits".
- Heat-set core-hole diameters: ruthex brass insert datasheets (M2–M6) and Böllhoff QUICKSERT technical data for thermoplastics.
- Printed clearances are measured practice on 0.4 mm-nozzle FDM, SLA/DLP and SLS PA12 — starting values, confirmed with a fit coupon.
