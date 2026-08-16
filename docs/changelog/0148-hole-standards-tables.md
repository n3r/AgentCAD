# 0148 — 2026-08-13 — PRD-010 slice 2: vendored ISO hole-standards tables and the `hole_standards` tool

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 2)

## Summary

FR5 and **AC3**: the ISO hole tables an author, an agent and (later) a UI size
picker all read, as vendored JSON with provenance, behind an **OCP-free**
lookup module and one pure-data tool. No geometry, no kernel call, nothing that
can fail on OCCT. `hole_standards {family?, size?, std?}` answers
`{"size": "M5", "family": "clearance"}` with the three ISO 273 diameters
**5.3 / 5.5 / 5.8**, which is AC3 verbatim.

The slice's real content is the transcription, and the design named it as a
risk. Every row that ships was found identically in **two independent published
sources**, both named in the file's `sources` header. Rows the two sources
disagreed on, or that only one carried, **do not ship** — and each file's
`notes` says which ones and why, so nobody re-derives them by guesswork later.

## Sources used, and what was verified

**`iso_clearance.json` — ISO 273:1979, 19 rows (M1.6…M36).**
Sources: `engineeringhardware.com/fastener/clearance-hole-sizes/` and
`ekinsun.com/custom-fasteners/clearance-hole-chart/` (both read 2026-08-13).
The two agree **exactly, row for row, on every row shipped**. Spot-checked
against the published values in the tests as literals (M3 3.2/3.4/3.6, M5
5.3/5.5/5.8, M6 6.4/6.6/7.0, M8 8.4/9.0/10.0, M12 13.0/13.5/14.5, M20
21.0/22.0/24.0); a web search summary independently reproduced the M3, M5 and
M8 rows.
*Rejected:* a third chart (`fractory.com`) that disagreed on M2.5, M3, M6, M12
and M16 and printed M30 twice — rejected outright rather than averaged or used
as a tie-break.
*Not shipped, for want of a second source:* M1, M1.2, M1.4, M3.5, M7, M33 and
everything above M36.

**`iso_thread.json` — ISO 261/262 pitch + tap drill, 13 coarse rows and 5 fine.**
Sources: `amesweb.info/Screws/metric-tap-drill-chart.aspx` and
`aimsindustrial.com.au/blogs/product-guides/blog-threading-tap-metric-imperial-size-chart`
(both read 2026-08-13). Coarse pitches and drills agree exactly across the
range shipped (M1.6…M24). Spot-checked in the tests: M3 0.5/2.5, M5 0.8/4.2,
M8 1.25/6.8, M10 1.5/8.5, M12 1.75/10.2.
*Not shipped:* M3.5, M14, M18 and M22 coarse (one source only), and the
`M12×1.5`, `M12×1.25` and `M6×0.75` fine rows — the sources printed different
drills for those (e.g. 10.75 vs 10.8 for `M12×1.25`, which is a rounding to a
different stock drill, not a typo). Fine rows that *did* clear both sources:
`M8×1.0`→7.0, `M10×1.0`→9.0, `M16×1.5`→14.5, `M20×1.5`→18.5, `M24×2.0`→22.0.

**`iso_cbore_csk.json` — ISO 4762 and ISO 10642 head geometry.**
Sources: `fasteners.eu/standards/iso/4762/` and
`fullerfasteners.com/tech/din-912-specifications-hex-socket-head-cap-screws/`
for the socket-head table (identical on all 13 rows: M5 dk 8.5 / k 5.0, M8 13.0
/ 8.0, M12 18.0 / 12.0, M24 36.0 / 24.0), and
`fullerfasteners.com/tech/iso-10642-specifications-hex-socket-countersunk-head-screws/`
corroborated by a search-result reproduction of the same theoretical-sharp
figures from two fastener datasheets (M3 6.72, M5 11.2, M8 17.92).

> **Correction (review, 2026-08-16).** This paragraph originally added "the
> series is exactly `2.24 × d`, which is its own internal check". **It is not a
> check on the column**: the ratio holds for M3…M12 and the two largest rows
> are below it — M16 is 33.6 against 35.84, M20 is 40.32 against 44.8. The rows
> were re-read against the source and **the data is right**; the corroborating
> *rule* was the false part, so the claim is now restricted to the range where
> it holds (in the JSON's `notes` and in
> `test_the_iso_10642_head_ratio_is_claimed_only_where_it_holds`). Separately,
> the search-result reproduction quoted above is **not a named source in the
> file**: `iso_cbore_csk.json` names exactly one for the ISO 10642 column, so
> all nine of those rows now answer `corroborated: false`.

### The finding that changed what this file contains

**The counterbore diameter is not a standard, and three published conventions
disagree materially.** Measured while transcribing, for the same M5/M8 socket
head cap screw:

| source family | M5 ⌀ / depth | M8 ⌀ / depth |
|---|---|---|
| DIN 974-1-style charts (`engineersbible.com`) | 10.0 / 5.8 | 15.0 / 8.8 |
| Machinery's-Handbook-derived (`amesweb.info`) | 9.75 / 5.0 | 14.25 / 8.0 |
| a third machinist chart | 9.75 / 5.0 | 14.5 / 8.0 |

That is a 0.75 mm spread on M8 alone. The design's provenance rule ("a row you
cannot corroborate does not ship") therefore forbids transcribing any of them
as *the* ISO value. So **no counterbore diameter is transcribed at all**. What
ships is the fastener **head geometry**, which the standards do fix and which
two independent sources print identically, and `cbore()` derives the bore from
it by a named constant pair — `CBORE_DIA_CLEARANCE = 1.5 mm`,
`CBORE_DEPTH_CLEARANCE = 0.8 mm` — returning `head_d`/`head_h` (the standard
part) *and* `d`/`depth` *and* a `rule` string that says in the answer itself
that the bore is a shop default, not a standard. For M5 that lands on 10.0 /
5.8 and for M8 on 14.5 / 8.8; the rule was chosen to sit on or above every
published convention from M5 up, because a counterbore that is too big fails
visibly and one that is too small traps the head.

> **Correction (review, 2026-08-16).** "From M5 up" was the range the rule was
> *chosen* over and the code applied it **unguarded** below that: measured,
> `cbore("M2")` bored **5.3** where DIN 974-1 gives 4.3, because a flat 1.5 mm
> on a 3.8 mm head is a third of the whole hole. Below the head the flat number
> was set on (`CBORE_DIA_FLAT_MIN_HEAD` = the 8.5 mm ISO 4762 M5 head, and
> 0.375 in on the inch side), the diameter clearance is now applied as the same
> **proportion** of the head it has there. Continuous at the threshold, so M5
> is still 10.0 and a 1/4 in head still gets 7/16; M2 becomes 4.47, still large
> rather than small. The depth clearance is deliberately left flat — no
> published small-size depth was measured, and inventing a second guard would
> be the very thing this section refuses to do.

For countersinks the equivalent trap is avoided differently: `csk()` returns the
**theoretical sharp** head diameter (ISO 10642's `dk`, which is `2.24 d` for
M3…M12 and is transcribed rather than derived above M12 — see the correction
above), which is the dimension a countersink callout names and already stands
off the machined head max, so no invented clearance is added.

## Changes

- **`agentcad/toolkit/hole_standards.py` (new)** — the **third OCP-free toolkit
  module** (with `sketch.py` and `specs.py`), because the server's tool imports
  it. `lru_cache`d loaders that validate the header (`schema == 1`, units,
  revision, **two sources**) on first read; `clearance`, `clearance_fits`,
  `thread`, `cbore`, `csk`, `sizes`, `lookup`, `canonical_fit`,
  `default_csk_angle` and `designation`.
- **Both fit spellings are accepted and canonicalized per standard.** ISO 273
  says fine/medium/coarse, ASME B18.2.8 (and the PRD) says close/normal/loose;
  either spelling resolves, and the answer reports the spelling of the requested
  `std`. Documented in the module docstring rather than picked silently,
  because an agent will type both.
- **The countersink angle default is per-standard here (ISO 90°, ASME 82°)**,
  precisely so the geometry slice cannot inherit build123d's
  `CounterSinkHole` default of 82 — an ASME default that would otherwise arrive
  inside an ISO-labelled call.
- **The designation grammar** (design Decision 5's table) for all four families
  in both symbologies: `⌀5.5`, `M5×0.8 - 6H ↧12`, `⌀5.5 ⌴⌀9.5↧5.4`,
  `⌀5.5 ⌵⌀10.4×90°` and the ASME-formatted equivalents. A `depth` of `None`
  means a through hole and the `↧` glyph is omitted — a through hole is not
  "depth 0".
- **Every bad argument raises `ValueError` naming the argument** (`size`, `fit`,
  `std`, `pitch`, `fastener`, `family`) — the `toolkit/specs.py` convention, so
  a bad call inside a part script surfaces as a normal structured
  `script_error` with `details.line`.
- **`std="ansi"` raises**, naming `std`, until the ANSI tables land in slice 8.
  A silent fallback to the ISO numbers under an ANSI label is the one failure
  this module must not have.
- **`agentcad/core/tools_holes.py` (new)** — the tool pack, registered
  **unconditionally** (pure data). It loads at `h`, before `tools_proposals`
  (`p`), `tools_specs` (`s`) and `tools_versioning` (`v`), so it reads nothing
  off `service` at all today; the test suite pins that and pins that the tool
  survives a second `build_registry()`. `ValueError` is converted to
  `ValidationError` at the boundary so a bad argument is a structured payload,
  not a 500.
- `agentcad/toolkit/__init__.py` re-exports `hole_standards` lazily, like the
  other submodules.
- `tests/test_toolkit_ocp_free.py` gains the module — one line, as slice 1
  designed it — and the fresh-interpreter probe now evaluates
  `clearance("M5")["d"] == 5.5` with `OCP` blocked.

## Files

- `agentcad/toolkit/hole_standards.py` — **new**, OCP-free lookups + designations
- `agentcad/toolkit/data/iso_clearance.json` — **new**, ISO 273, 19 rows
- `agentcad/toolkit/data/iso_thread.json` — **new**, ISO 261/262 + tap drills
- `agentcad/toolkit/data/iso_cbore_csk.json` — **new**, ISO 4762 / ISO 10642 head geometry
- `agentcad/core/tools_holes.py` — **new**, the `hole_standards` tool pack
- `agentcad/toolkit/__init__.py` — lazy re-export
- `tests/test_hole_standards.py` — **new**, 34 tests including AC3
- `tests/test_toolkit_ocp_free.py` — `hole_standards` added to the OCP-free list
- `docs/agent-api.md` — a "Hole standards" section; tool count 71 → 72 (75 with `[fem]`)
- `docs/architecture.md`, `README.md` — the same tool count
- `docs/changelog/0148-hole-standards-tables.md` — this entry

## Notes

- **The tap drill is a shop number, not a standard**, and the file says so: it
  is the stock drill nearest `d − P` for ~70–75% engagement, and published
  tables round it differently. It is also **not** the radius to bore when
  fusing real thread geometry — the CHEATSHEET's `root_radius` rule still
  governs there, and slice 4's `holes.tapped` docstring repeats it.
- **The data files are validated on load, not trusted.** A hand-edited file
  with one source, a missing `revision`, or `schema != 1` raises a `ValueError`
  naming the file rather than answering with half a table.
- **No new dependency, no lock change**, and the JSON ships inside the package
  (hatchling's `packages = ["agentcad"]` takes the whole tree, so no
  `pyproject` edit was needed).
- Follow-ups this slice deliberately leaves open: ANSI/ASME tables including
  number/letter/fraction drill designations (slice 8), ISO 7380 button-head
  geometry (one source found, so it did not clear the rule), and the fine-pitch
  and small-size rows listed above as not corroborated. Each is a one-line
  diff plus a `revision` bump when a second source is found.

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_hole_standards.py tests/test_toolkit_ocp_free.py
39 passed in 0.86s
```

The tool in a real registry:

```
$ .venv/bin/python -c "…build_registry(service); registry.call('hole_standards', {'size':'M5','family':'clearance'})"
registered: True
{"std": "iso", "family": "clearance", "size": "M5",
 "fits": {"fine": 5.3, "medium": 5.5, "coarse": 5.8},
 "designations": {"fine": "⌀5.3", "medium": "⌀5.5", "coarse": "⌀5.8"},
 "standard": "ISO 273:1979", "revision": "2026-08-13", "sources": [ …two… ]}
```

Full suite (`make test`, split into two chunks because one process exceeds this
sandbox's foreground time cap):

```
$ .venv/bin/python -m pytest -q -n 4 --dist loadscope tests/ --ignore=tests/test_examples.py
2044 passed, 1 skipped in 317.04s
$ .venv/bin/python -m pytest -q -n 2 tests/test_examples.py
20 passed in 938.12s
```

**`make test`: 2064 passed, 1 skipped.** Against slice 1's re-measured baseline
of 2016 passed / 2 failed / 1 skipped on `f684717`, that is +11 from slice 1,
+35 from this slice (34 in `tests/test_hole_standards.py` plus one more
OCP-free probe), and the two changelog-citation failures turned green by
slices 1 and 2 citing a suite count. No new skips.
