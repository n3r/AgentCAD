# 0154 — 2026-08-13 — PRD-010 slice 8: counterbore, countersink, and the ANSI/ASME tables

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 8)

## Summary

The rest of FR4 and FR5: `holes.counterbore` and `holes.countersink` over
build123d's own `CounterBoreHole`/`CounterSinkHole`, and the ASME tables that
make `std="ansi"` a real answer instead of the deliberate `ValueError` slice 2
left there. Three new vendored files, every row transcribed from **two
independent published sources**, and four things refused for want of a second
one — named below rather than quietly rounded in.

Two facts drive most of the design here and neither is a coding decision:

1. **The counterbore diameter is still not a standard, and this slice does not
   pretend otherwise.** Slice 2 measured three published conventions
   disagreeing by 0.75 mm on one M8 (15.0 / 14.25 / 14.5). Nothing about
   shipping the *geometry* helper changes that, so `counterbore` bores what
   `hole_standards.cbore` derives from corroborated **head** geometry by a
   named rule, and the rule travels in the record and in the answer. The inch
   side gets the same treatment with the inch shop's round numbers.
2. **The two standards do not share a unit.** ISO tables are millimetres, ASME
   tables are inches, and AgentCAD drills millimetres. So every length a lookup
   returns is millimetres and every *designation* prints the standard's own
   unit, with exactly one named conversion between them.

## The ANSI sources, and what was checked

**`ansi_clearance.json` — ASME B18.2.8, 18 rows (#0…1 in).**
Sources: `amesweb.info/Screws/Clearance-Hole-Chart.aspx` and
`diecasting-mould.com/news/bolt-screw-clearance-hole-size-chart-metric-imperial`
(both read 2026-08-13); `calc-tools.com/formulas/clearance-hole-size-calculator`
independently reproduced the #4 and #10 rows and is recorded as a spot check,
not as a transcription source. Spot-checked as literals in the tests: #6
0.154/0.170/0.185, #10 0.206/0.221/0.238, 1/4 0.266/0.281/0.297, 3/8
0.391/0.406/0.422, 1/2 0.531/0.562/0.609, plus the drill designations
(#2, 17/64, B).

**The finding that made this file name its standard in the header.** There is
more than one published inch clearance chart and they are *not* roundings of
each other:

| chart | #10 close / normal / loose | 1/2 close |
|---|---|---|
| ASME B18.2.8 (shipped) | 0.206 / 0.221 / 0.238 | 0.531 |
| traditional "close fit / free fit" (`engineersbible.com`) | 0.196 / 0.201 / — | 0.5156 |
| a third widely-reprinted chart (`ficientdesign.com`) | 0.196 / 0.204 / 0.221 | 0.516 |

This is the same shape of problem as slice 2's counterbore spread, with one
difference that makes it tractable: the second and third charts do not *claim*
B18.2.8. So the rule applied is the same one — no blending, no tie-breaking —
and the resolution is that the file transcribes one named standard and says so
in `standard` and in `notes`.

*One cell the two sources printed differently:* **#8 normal.** Both print drill
**#9**; one prints ⌀0.196 (drill #9's tabulated diameter) and the other 0.190,
which is not any drill size and is the #10 screw's nominal diameter. The
agreeing datum is the drill designation, and every other row's ⌀ column in both
sources is exactly its drill's diameter, so the row ships as `#9`/0.196 with
the disagreement written into the file's `notes`. That is the one place this
slice resolved a conflict rather than dropping a row, and it is recorded here
so a reviewer can overrule it in a one-line diff.
*Not shipped:* 1-1/8 through 1-1/2 (one source only).

**`ansi_thread.json` — Unified UNC/UNF, 19 sizes (#1…1 in).**
Sources: `premsaindustries.com/en/resources/tap-drill-size-chart` and
`rivcut.com/resources/tap-drill-chart` (both read 2026-08-13). Every shipped
row is identical in both, TPI, drill designation and decimal. Spot-checked in
the tests: #6-32→#36/0.1065, #10-24→#25/0.1495, 1/4-20→#7/0.2010,
5/16-18→F/0.2570, 3/8-16→5/16/0.3125, 1/2-13→27/64/0.4219.
*Not shipped, one source only:* `#0-80 UNF` (there is no #0 UNC, so the size
would have put a fine thread in the first-choice slot), `#5-44`, `#12-28`,
`11/16-24`, `13/16-20`, `7/8-14`, `15/16-16` and `1 1/8`/`1 1/4` UNF, and
`11/16` and `15/16` UNC.

**`ansi_cbore_csk.json` — ASME B18.3 head geometry.**
Socket head cap screws (18 rows, #0…1 in) from
`fullerfasteners.com/tech/ansi-asme-b18-3-specifications-hex-socket-head-cap-screws/`
and `aftfasteners.com/socket-cap-screws-dimensions-and-mechanical-properties/`
— identical on every row, and with an internal check the metric table has too:
`A max = 1.5 d` and `H max = d` throughout. The two sources print H for 5/16 as
0.3125 and 0.312 and for 7/16 as 0.4375 and 0.438; those are the same number at
two precisions (both equal the nominal diameter) and the unrounded value ships.
82° flat head socket cap screws (12 rows, #4…3/4) from MSC Direct's
`FlatSocketHeadCapScrews.pdf` and the Purchase Partners Fastener Reference
Guide's `Flat Head.pdf` — identical on every shipped row, taking the **max
theoretical sharp** head diameter, which is the dimension a countersink callout
names (the same choice slice 2 made for ISO 10642's `dk = 2.24 d`).
*Not shipped:* csk `#2`, `#3`, `7/8` and `1` (one source only), and ASME B18.21
button-head geometry (no corroborated table found at all).

**What was refused outright:** no ANSI counterbore *diameter* table, for the
same reason no ISO one shipped — see below.

## The counterbore rule, in two unit systems

`hole_standards.cbore` still returns the published head (`head_d`, `head_h`)
and derives the bore by a named constant pair. The metric rule is unchanged
(head + 1.5 mm diameter, + 0.8 mm depth). The inch rule is **+1/16 in on
diameter and +1/32 in on depth** — within 0.09 mm of the metric one, and it
lands on the shop's own numbers: a 1/4 in socket head gets a **7/16
counterbore 9/32 deep** instead of 11.03/7.15 mm. Both constants are public
(`CBORE_DIA_CLEARANCE`, `CBORE_DIA_CLEARANCE_IN`, …) and `CBORE_RULE` says "a
shop default, not a standard" in every answer that carries it.

`counterbore(..., cbore_d=…, cbore_depth=…)` takes a shop's own numbers in
millimetres, and the record then carries those.

## The angle, and why it is passed explicitly every time

build123d's `CounterSinkHole` defaults `counter_sink_angle=82`, which is an
**ASME** default that would otherwise arrive inside an ISO-labelled call.
`hole_standards.default_csk_angle` owns the number (ISO 90, ASME 82) and
`holes.countersink` passes it on every call. The test proves it by volume
rather than by reading the argument: at 90° the cone is
`(seat_r − r)/tan(45°)` deep and at 82° it is `/tan(41°)`, and the measured
removal matches the first at `rel=1e-6` and differs from the second by more
than `rel=1e-3`. A silently inherited default cannot pass that.

## Changes

- **`agentcad/toolkit/data/ansi_clearance.json`, `ansi_thread.json`,
  `ansi_cbore_csk.json` (new)** — the tables, with the same
  `{schema, standard, units, sources, revision, notes, rows}` header, `units`
  now `"in"`, and `notes` naming every row that did not ship and why.
- **`agentcad/toolkit/hole_standards.py`**
  - `_TABLES` maps `(std, family)` to a file; a missing entry is an error
    naming `std`, and `std="ansi"` no longer raises for any of the four
    families. A metric size asked of the ANSI table (or the reverse) is a
    **`size`** error that lists what *is* tabulated.
  - **Units.** `MM_PER_INCH`, `_mm()` (a table value in the kernel's unit) and
    `in_designation_units()` (a length in the unit the standard's callouts
    print). Every lookup now returns millimetres in `d`/`depth`/`head_d`/
    `tap_drill` with a `*_native` companion, and `_provenance` reports `units`.
    This is the one place a 25.4x error could hide, so the conversion is a
    named function, not a division inside a formatter.
  - **A Unified "pitch" is threads per inch**, a whole count: `_pitch_key` is
    std-aware (`"20"`, never `"20.0"`) and refuses a non-integer for ASME with
    a message saying why. `thread()` reports `tpi` *and* the derived millimetre
    `pitch`, plus the drill designation and `1/4-20 UNC` as the thread label.
  - `default_thread_class` (ISO `6H`, ASME `2B`) — `1/4-20 UNC - 6H` is
    nonsense, so the class default is per standard and `thread_class=None`
    resolves it. Same for the fastener head table (`_DEFAULT_FASTENER`).
  - `sizes()` returns **table order**, not sorted order: `float(size[1:])`
    reads `1/4` as 1.0 and cannot read `B` at all.
  - `check_std` is public (slice 7).
- **`agentcad/toolkit/holes.py`**
  - `counterbore(part, points, size, *, plane, fit, std, fastener, cbore_d,
    cbore_depth, depth, thru, verify)` and `countersink(..., angle, csk_d,
    ...)`, both returning FR4's `(part, records, warning|None)`. The through
    hole is the standard's clearance hole for the size and fit; the seat is the
    head. A `cbore_d`/`csk_d` that is not larger than the hole it sits over
    raises, and a pocket that is not shallower than the stock below the plane
    warns ("the head has nothing to bear on").
  - `_drill` grew `cut`/`tool`/`envelope_r`, so the primary route stays
    build123d's own operator (Decision 1's byte-faithful `BuildPart` +
    `add(part)` + `Locations`), the `safe_bool` fallback cuts the **right**
    shape (cylinder + pocket, cylinder + frustum) rather than a plain hole, and
    the bbox screen and the spacing/proximity warnings use the *seat* radius,
    which is the part of the tool that can actually clash.
  - Records gain `cbore {d, depth, fastener}` / `csk {d, angle_deg, fastener}`,
    and `tap` gains `tpi` and the tap-drill `drill` designation — a US shop
    reads "#7 drill", and it is not derivable from 0.201.
  - `fastener` defaults to `None` and resolves per standard. **A deviation from
    the plan's literal `fastener="iso4762"`**, taken because that default under
    `std="ansi"` would be a lookup for a metric head table.
- **`agentcad/core/tools_holes.py`** — the `hole_standards` tool description
  and `size` schema now say ASME, the inch vocabulary, and the unit split. No
  new tool, no signature change.
- **`docs/agent-api.md`** — the ANSI half of `hole_standards`, the three-charts
  finding, the mm-vs-designation-units rule, `tpi`, the per-standard thread
  class, and the record's `cbore`/`csk`/`drilled` families.

## Files

- `agentcad/toolkit/data/ansi_clearance.json` — **new**, ASME B18.2.8, 18 rows
- `agentcad/toolkit/data/ansi_thread.json` — **new**, UNC/UNF + tap drills
- `agentcad/toolkit/data/ansi_cbore_csk.json` — **new**, ASME B18.3 head geometry
- `agentcad/toolkit/hole_standards.py` — ANSI tables, units, tpi, defaults
- `agentcad/toolkit/holes.py` — `counterbore`, `countersink`, the `_drill` seam
- `agentcad/core/tools_holes.py` — tool description + schema wording
- `tests/test_hole_standards.py` — 21 new tests (ANSI spot checks, units, tpi)
- `tests/test_holes.py` — counterbore/countersink geometry, the angle proof,
  the shallow-stock warning, the ANSI end-to-end unit test
- `tests/test_drawing_holes.py` — a counterbore and a countersink callout
- `docs/agent-api.md`, `docs/changelog/0154-holes-cbore-csk-ansi.md`

## Notes

- **A record matches its BORE circle in a drawing, not its seat.** Both are
  projected and they are concentric, so the leader lands in the same place; the
  point is that the group carrying `⌀9 ⌴⌀14.5↧8.8` is the group whose diameter
  the record actually states. The seat circle keeps no callout of its own (and
  a single seat is below `_detect_circles`' `count >= 3` threshold anyway).
- **`⌀0.281 ⌴⌀0.4375↧0.2812` is what an ASME sheet gets** while the geometry
  is 7.1374 mm across — the unit split working end to end, asserted as a test
  rather than as a docstring.
- Deliberately not here: ISO 7380 button heads (still one source), the ANSI
  fine/coarse *clearance* distinction beyond the three B18.2.8 fits, and any
  counterbore *diameter* table in either standard.
- Slices 9–10 (`features.rib`/`boss`/`draft`) need two things from here:
  `holes.tapped` for `boss(hole="M3")` — its `thread_class` default is now
  per-standard, so pass nothing unless you mean something — and the `_drill`
  `cut`/`tool` seam, which is how a new hole *shape* is added without touching
  the guard, the records or the byte-faithful route.

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_hole_standards.py tests/test_holes.py tests/test_drawing_holes.py
120 passed in 10.35s
```

```
$ .venv/bin/python -m pytest -q -n 2 --dist loadscope -m "not slow"   # make test-fast
1884 passed, 1 skipped in 305.59s (0:05:05)
```

Full suite (`make test`, split into two chunks because one process exceeds this
sandbox's foreground time cap; `-n 2 --dist loadscope` is what the Makefile
runs):

```
$ .venv/bin/python -m pytest -q -n 4 --dist loadscope tests/ --ignore=tests/test_examples.py
2194 passed, 1 skipped in 336.89s (0:05:36)
$ .venv/bin/python -m pytest -q -n 2 tests/test_examples.py
20 passed in 924.16s (0:15:24)
```

**`make test`: 2214 passed, 1 skipped**, against slice 6's 2178 passed / 1
skipped. No new skips, and no pre-existing test moved.
