# 0163 — PRD-010 review round 2: a hole a record could not prove, and a provenance rule that was not true

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

Five findings from the independent Codex review of PRD-010, all in the hole
half: the public default `verify="bbox"` let a hole instance cut air while the
record counted it; a drawing asserted a blind depth the geometry no longer had;
the persisted sidecar and the shape attribute were read with a much weaker
contract than the one the harvest enforces; blind `clearance`/`counterbore`/
`countersink` callouts omitted the hole depth entirely; and the "two published
sources must agree or the row does not ship" guarantee was never what the
loader checked. Four are behaviour fixes with a measurement each; the fifth is
a claim narrowed to what is now mechanically enforced.

## Changes

### The default guard measures each instance (finding 3)

`toolkit/holes._guard`'s default tier compared each tool against the **whole
part's** bounding box, and the only other check was the aggregate volume delta
— which one successful cut satisfies however many instances missed. Measured on
a 100×100×10 square frame with a 60×60 void, drilling ⌀10 at `(40, 0)` and
`(0, 0)`:

| | before | after |
|---|---|---|
| solids / validity | 1, valid | 1, valid |
| warning | `None` | names instance `[1]` and the dropped count |
| instance statuses | `engaged`, `engaged` | `engaged`, `missed` |
| removed volume | 785.398163397 mm³ (**one** cylinder) | unchanged |
| record `count` | **2** | **1** |

The tier is now three rungs, cheapest first, and only the last two conclude:
the bbox screen (a proved *miss*, since a box contains its shape), then
`_axis_proof` — a point strictly inside the solid on the bore axis, which is a
proved *engagement* because every tool built here contains the bore cylinder
over `[0, reach]` — and otherwise the exact `(part & tool)` probe for that one
instance. The statuses it produces are therefore identical to
`verify="exact"`'s; what `exact` still buys is `engaged_mm3` / `contact_mm2` on
every instance.

Cost, `holes.clearance` end to end on a 200×200×12 plate — one warm run per
cell, before → after, so the sub-millisecond differences are inside the noise
and only the order of magnitude is a claim:

| | `off` | default | `exact` |
|---|---|---|---|
| 12 holes | 32.2 ms | 29.8 → **31.5 ms** | 93.6 ms |
| 50 holes | 108.3 ms | 117.3 → **114.7 ms** | 372.1 ms |

Per probe: bbox 0.014 ms, axis classification 0.041 ms (0.033 ms to build the
classifier once per part plus 0.010 ms per point, nine points), exact ≈5 ms. So
the default keeps its speed and gains the exact tier's verdict; changing the
default to `exact` would have cost 3.2× on 50 holes for the same answer.

The record now claims only what came off: `count`, `positions` and `centers`
cover the instances the guard proved removed material, the rest are listed
under `dropped: [{i, status, position}]`, and `verify` records the mode that
was **requested** (the tier that answered an instance is
`instances[i].probe`). `verify="off"` drops nothing and says so — it is the one mode whose
count is intent.

**A drop has to reach the user, and the helper's warning does not.** Every
bundled part spells the call `part, _r, _w = holes.clearance(...)`, so the
string naming a no-op instance goes into `_w` and nowhere else. The record is
what survives the script, so the harvest handler now reads the drop back off it
and adds it to the warnings the rebuild result and `get_part` already carry.
(The harvest's own top-level `dropped` is the *other* kind — records lost by an
operation that did not carry them — and the two are deliberately not merged.)

Related, found by the fixture that broke: the "deeper than the stock" guard
tested `depth > stock`, so `depth=t` on a `t` plate — the spelling an author
writes — passed silently and produced a record with `thru: false` and a depth
the geometry does not have. `stock` is a bounding-box extent, so *reaching* it
is as certainly through as exceeding it; the test is now `>=` and the message
says to use `thru=True`.

### A drawing does not assert a depth it has not checked (finding 4)

Round 1 already made the callout print the circles actually **matched** rather
than the record's count, which closes the "8-hole group, 7 destroyed, sheet
says 8×" half of this finding (re-confirmed: the callout reads `3× ⌀5.5` with a
warning naming the divergence). What remained is the depth. Measured: an M8
recorded blind at 6 mm, then drilled through by a later carrying operation,
printed `M8×1.25 - 6H ↧6` with **no warning at all**.

`_blind_depth_holds` classifies one point per matched centre just past the
recorded bottom. Gone ⇒ the callout prints the record's `designation_base`
(the same string built by the same function, without the depth qualifier), the
group reports `bottom_present: false`, and the recorded number travels in
`hole_warnings` where it cannot be read as a dimension. Still there ⇒ the depth
is printed unchanged and `bottom_present: true`. The claim is *degraded*, never
guessed: measuring the hole's new depth would be a sampled ray cast.

### One record contract, three readers (finding 5)

`hole_standards.validate_record` is now the only record check in the system.
It was three: the worker's harvest checked eight keys and their types, the
drawing checked **five key names**, and the sidecar checked the version and
that `holes` was list-or-null.

It is structural *and* self-consistent — it re-derives the record's
`designation` from the record's own diameter, depth and thread
(`designation_for_record`, which `toolkit.holes` also uses to *build* it) and
requires a match, plus `count == len(positions) == len(centers)`, `thru`
against `depth_mm`, and the family/seat pairing. Measured before: a
structurally plausible dict `setattr`-ed onto the shape with a fabricated
designation beside one real diameter and centre printed
`M8×1.25 - 6H ↧12 (INSPECT 100%)` on the sheet. After: skipped, with a warning
naming the contradiction.

`_read_sidecar` now also compares the **cache key the file itself stores** —
written since the sidecar existed and never read, so a `.holes.json` describing
a different build of the part was served as if it described this one — and
enforces the four-state invariant the module docstring declares. The
`{"holes": [], "dropped": 0, "warnings": []}` document Codex constructed is an
impossible fifth state and was accepted and reported as "records were created
and did not arrive", silently; it is now discarded and recomputed.
`HOLES_SIDECAR_VERSION` is 2, so stored v1 documents (whose `count` is the
weaker claim) are discarded on read.

**The trust model, stated rather than overclaimed:** none of this is an
authentication boundary. A part script runs arbitrary code in the kernel
process and one that wants an M8 callout can drill an M8. What is closed is the
*stale or inconsistent carrier* — a record whose text and numbers have come
apart, a sidecar written for other bytes — which is what a hand edit, a partial
write or a copied `.cache/` actually produces.

### Blind seat callouts state the hole depth (finding 6)

`holes.clearance(part, [(0, 0)], "M8", depth=6)` recorded `depth_mm: 6.0`,
`thru: false` — and a designation of `⌀9`, which a shop manufactures as a
through hole. `counterbore` was worse: the visible `↧` was the *pocket* depth.
`drill` and `tapped` always did include it, so this was an inconsistent
omission and not a convention.

| | before | after |
|---|---|---|
| `clearance` blind | `⌀9` | `⌀9 ↧6` |
| `counterbore` blind | `⌀9 ⌴⌀14.5↧8.8` | `⌀9 ↧6 ⌴⌀14.5↧8.8` |
| `countersink` blind | `⌀9 ⌵⌀17.92×90°` | `⌀9 ↧6 ⌵⌀17.92×90°` |

The two depths are disambiguated the way ISO 129 and ASME Y14.5 both do it:
**each `↧` qualifies the `⌀` group it follows**. Those standards do not
disagree about that ordering; where drafting practice varies is only whether
the groups are stacked on two lines of a note, which is not available in one
string. Through-hole callouts are byte-identical to what shipped.

### Provenance is per row, and `corroborated` means the sources agreed (finding 7)

The shipped guarantee — "every row is transcribed from two independent
published sources; a row that could not be corroborated is absent" — was not
what the code checked. `table()` and `tests/test_hole_standards.py` required
two **file-level** source strings, so a row copied from one publication passed
because the file already contained unrelated citations, and
`ansi_clearance.json` shipped `#8 NORMAL` as `corroborated: true` while its own
notes recorded the two sources printing it differently. No wrong numeric value
has been found in any table; the defect is that the provenance claim could not
establish transcription accuracy.

Both halves of the repo's precedent (narrow the claim, enforce what remains):

- **Per-row provenance, enforced on load.** Each file declares `row_shape` and
  a `provenance` block with `groups` (a source set plus the rows it covers, by
  name) and `scopes` (a single row). **There is no `default`** — a default is
  exactly what let a row inherit its neighbours' citations. Adding a row
  without a declaration fails to load naming it, and a scope naming a row that
  is not there fails too.

  *(Rounds 3 and 4 took this further: the unit is the **cell**, a row-level
  entry is refused outright, and the refusal reads `cell(s) [...] declare no
  sources (N in total)`. The paragraphs below are the record of how it got
  there; where they disagree with this parenthesis, this parenthesis is what
  ships.)*
- **`corroborated` = two or more independent sources **that agree**.** A cell
  with a recorded `conflict` is now `corroborated: false` whatever the source
  count. `#8 NORMAL` ships adjudicated, not agreed: drill #9 / 0.196,
  `corroborated: false`, with the rejected 0.190 and the reasoning in
  `conflicts`; its two neighbours in the same row stay `corroborated: true`.
- **The ISO countersink citations** were already corrected in round 1
  (`csk/iso10642` cites only the ISO 10642 source, and all nine rows answer
  `corroborated: false`). That is the "ships labelled single-sourced" branch of
  the corrected rule, and the rule now says so out loud: a one-source or
  disputed row may ship *labelled*; dropping the ISO 10642 column would remove
  every metric countersink, and shipping it silently is what this rule exists
  to prevent.
- The file-level `len(sources) >= 2` rule is gone from the loader and from the
  test, replaced by "distinct, non-empty citations" — the union speaks for no
  row.

### Round 3: what an adversarial verifier found in round 2's own fixes

An adversarial pass confirmed the three-rung verify tiers (twelve adversarial
geometries plus all five helper families: the default's statuses were identical
to `verify="exact"` in every case) and spot-checked ~90 table values against
ISO 273 / 261-262 / 4762 / 10642 and ASME B18.2.8 / B18.3 with no wrong value
and no unit error. Five things it broke:

1. **Provenance coverage proved row NAMES, not cells.** `_check_provenance`
   built its `uncovered` set from `_row_paths`; `_cell_paths` only validated
   scopes. So an `M12×1.5` pitch cell with a fabricated `tap_drill: 99.9` added
   to `iso_thread`'s group-covered `M12` row **loaded**, and
   `thread("M12", pitch=1.5)` answered `{'tap_drill': 99.9, 'corroborated':
   True, 'n_sources': 2, 'conflicts': []}` — a 99.9 mm tap drill for an M12,
   shipped as corroborated by two named publications. The control (a whole new
   row) was correctly refused throughout, which is what made the gap easy to
   believe closed. Coverage is now over `_cell_paths`, the JSON groups name
   every cell, and a row-level entry is refused outright (it would be a
   fallback for cells added later). Both probes now refuse, naming `M12/1.5`.
   `size/pitch` tables are exactly where the data legitimately grows a cell, so
   this was the likely real-world path.
   Also: `sources` distinctness was exact-string, so one citation pasted twice
   with a trailing space loaded as two independent publications and answered
   `corroborated: True`. Comparison is now on whitespace-collapsed, case-folded
   text. And all six files are validated at **import** (`validate_all`, 0.58 ms
   against the module's own 8.6 ms import) rather than one at a time on first
   lookup — with the honest limit recorded: the module is itself imported
   lazily by its callers, so that is eager *within the module*, not at process
   start.

2. **`corroborated` reached nothing that manufactures.** Grepping the tree
   returned exactly two hits — the tool description and the line computing it.
   The record carried no provenance at all and `add_holes` discarded everything
   but `size`, so `holes.countersink(part, [(0,0)], "M8")` produced
   `⌀9 ⌵⌀17.92×90°` off the single-sourced ISO 10642 column with
   `provenance keys []`, and the *adjudicated* ANSI `#8 normal` produced
   `⌀0.196` the same way. Every non-`drilled` record now carries
   `provenance: {standard, sources, corroborated, conflicts}`, unioned over
   every published row that fed it (`merge_provenance`; a counterbore has two,
   and `corroborated` is their conjunction), `add_holes` echoes it, and
   `validate_record` refuses a record that calls itself corroborated on one
   source or over a recorded conflict. A **disputed** cell also warns; a merely
   single-sourced one deliberately does not — "ISO 10642 has one source" is
   permanent and unfixable, and a warning nothing can ever clear teaches
   readers to ignore warnings (PRD-004's `strict_exempt` lesson). `drilled`
   carries `null`, and a drilled record that carries provenance is refused.

3. **`depth_verified` overclaimed, and so did the surrounding prose.** The
   field measures one point past the recorded bottom, so it catches a hole made
   deeper and cannot catch one made shallower. Measured: an M8 blind at 6 mm on
   a 12 mm plate with 3 mm milled off the **top** printed `M8×1.25 - 6H ↧6`
   over a **3 mm** hole, `depth_verified: true`, no warnings, byte-identical to
   the control. Renamed **`bottom_present`**, and `drawing.py`'s "re-measures
   everything it asserts" is now the explicit list of three things it does
   measure plus a paragraph naming what it does not (a shallowed hole, the
   diameter beyond `_HOLE_DIA_TOL`, anything off the top view). The gap itself
   is documented rather than closed: measuring a hole's real depth means
   finding where its wall begins, a ray cast this handler does not do.

4. **`verify` does not name the tier that answered.** It echoes the requested
   mode — `"verify": "bbox"` while the probes were `axis` and `exact` — and it
   has to, because one default-mode call routinely uses two rungs. The tier is
   per instance in `instances[i].probe`. `toolkit/holes.py`, `AGENTS.md`,
   `docs/part-authoring.md`, `docs/agent-api.md` and this entry all said
   otherwise.

5. **`core/templates.CHEATSHEET` — the guide agents actually receive — got
   none of round 2.** It still said `count` is the total with "instance 0 is
   skipped" (repealed by the polar fix), described the guard as "a bounding-box
   screen per instance", omitted `verify`/`dropped`/`designation_base`/
   `provenance`/`removed_mm3`/`pattern` from its record key list, and had no
   blind rows in its designation table. All updated from the live
   `patterns.polar` docstring and the live record, and
   `tests/test_holes.py::test_the_cheatsheet_names_every_key_a_hole_record_actually_carries`
   now asserts the sheet against a **real record**, so a key added without a
   word in the sheet fails.

### Round 4: six more claims that outran their measurements

The adversarial pass re-attacked round 3. Confirmed intact: the fabricated-cell
refusal, the duplicate-citation refusal, the 216-cell count (independently
recounted from raw JSON), `merge_provenance` as a true conjunction/union,
sidecar v3, and — proved rather than assumed — that `validate_all()` really is
eager in the **kernel worker** (`worker→mesh→facemod→boolean→holes→
hole_standards`, six cache misses at worker import; a corrupted package kills
the process at import) while the server stays lazy and OCP-free. No numeric
value moved in round 3. Six things it broke:

1. **`validate_record` accepted laundered provenance.** It re-derived the
   *designation* and compared, but checked provenance only for **internal**
   consistency — so the genuine disputed ANSI `#8 normal` counterbore record,
   with its conflict note deleted and `corroborated` flipped to `true`, is
   perfectly self-consistent and **validated clean** (`None`), while the tables
   give `corroborated: False` over one conflict. Also accepted: citations
   naming publications in no table; `standard` set to a wrong standard, a
   random string, an int, or absent; and `corroborated: true` backed by one
   citation listed twice (round 3's normalisation lived in the loader and
   nowhere else). New `provenance_for_record` re-derives the whole block from
   the record's own `family`/`standard`/`size`/`fit`/seat `fastener` — a
   lookup, because the record carries everything the lookup needs — and
   `validate_record` compares. All six launderings now refuse, naming which
   keys diverged and both sides' numbers.

2. **The `add_holes` echo contradicted the record and its own comment.**
   `_hole_call_args` echoed a single `lookup()`, which for a seat family is the
   fastener **head** row. Measured: `counterbore #8 (ansi)` echoed
   `corroborated: True, conflicts: 0` while the record said
   `corroborated: False, conflicts: 1` — the two surfaces disagreeing about the
   flagship disputed cell. The inline comment claimed the echo covered "the
   clearance row only", the opposite of what the code did. The echo now goes
   through `merge_provenance` over the same rows the record merges; measured
   after, echo and record are equal dicts for `clearance`, `counterbore` and
   `countersink`.

3. **The drawing asserted counterbore/countersink seats without measuring
   them.** `grep -n "cbore\|csk\|seat"` over the handler returned **0 hits**:
   a seat travels inside `designation` and printed verbatim. Measured — 30 mm
   plate, M8 counterbore (8.8 deep) + M6 countersink, 10 mm milled off the top
   so both seats are entirely gone (bbox z −15..5 against the control's
   −15..15): `⌀9 ⌴⌀14.5↧8.8` and `⌀6.6 ⌵⌀13.44×90°`, four numbers for features
   that do not exist, `hole_warnings: []`, byte-identical to the control. It is
   the same "material removed from the top" trigger the blind-depth check
   already handles. **Measured, not documented away** (the coordinator's
   preference, and it is not a ray cast): `_seat_present` classifies four
   points around the seat's outer radius at its own mid-depth — the pocket
   depth for a counterbore, half the cone height for a countersink, derived
   from the record's own numbers. After: both callouts read `⌀9` and `⌀6.6`,
   `seat_present: false`, two warnings naming the machined-away seat; the
   control is unchanged with `seat_present: true`. The probe asks **`any` of
   four azimuths, not `all`** — a counterbore at x=12.6 on a 40 mm plate puts
   its probe ring 0.1 mm past the edge and still reads `true`, because
   degrading a *correct* callout is a worse failure than missing a false one.
   Degradation is spelled by `designation_for_record` from a modified copy of
   the record, so a degraded callout uses the same grammar as an honest one.

4. **The CHEATSHEET guard was weaker than it read.** `key not in CHEATSHEET`
   was a substring search over ~400 lines: **21 of 24 record keys survived
   being deleted from the key block** (only `family`, `depth_mm` and
   `removed_mm3` were genuinely pinned; `d` occurs 821 times elsewhere), and
   `status`, `origin`, `depth`, `warnings`, `label` and `seat` all passed while
   entirely absent. It now regex-parses the `{...}` block and compares sets in
   both directions, so a missing key **and** a stale one both fail.

5. **`provenance["standard"]` was `str` for one row and `list` for two** —
   undocumented, untyped, and a caller indexing `[0]` got a character.
   Normalised to a list always, typed in `validate_record`, documented in the
   CHEATSHEET, `part-authoring.md` and `agent-api.md`.

6. **`coarse_pitch` was data outside the coverage set** — the same defect class
   as the fabricated cell, one field along. It names which tabulated pitch the
   standard calls first choice and `thread(size)` answers from it: flipping
   `iso_thread`'s M8 from 1.25 to 1.0 made `thread("M8")` answer tap drill
   **7.0** instead of 6.8, and the file loaded. The 32 values across the two
   thread tables are now cells (`iso_thread` 18 → 31, `ansi_thread` 35 → 54,
   248 cells in total), so removing one's declaration refuses naming
   `M8/coarse_pitch`. Citation normalisation also folds zero-width characters
   and `http`/`https`.

Also: the PRD's divergence text said `provenance.scopes` covers "a single row
or cell" — row-level entries are refused, and it now says so. And the
`/`-collision refusal lost its `# pragma: no cover` and gained the test that
fires it (a `size/fit` file where row `1/4` cell `close` and row `1` cell
`4/close` both spell `1/4/close`).

### Round 5: the laundering reopened, and two prose claims retired

The verifier **corrected its own round-4 conclusion**: it had passed the
provenance re-derivation after four steering attacks, then realised each had
mutated only one side of the pair.

1. **`size`/`fit` were not tied to `d`, which reopened the flagship
   laundering.** They select the published row every other check re-derives
   from, but were absent from `RECORD_KEYS`, never typed, and never compared
   with `d` — while `designation_for_record` spells the callout from `d`. So
   the number that gets manufactured and the label that chooses its provenance
   were never connected. Mutating **both sides consistently** — `size` `#8` →
   `#10` plus the `#10` provenance — left `d` at 4.9784 and the callout at the
   disputed `⌀0.196` while the record claimed `corroborated: True` over 0
   conflicts, and `validate_record` returned `None`; same for
   `fit normal → close` and `→ loose`. All three now refuse. `size` and `fit`
   are typed `RECORD_KEYS`, each family declares which of them it must and must
   not carry, and one already-cached lookup (`rows_for_record`, now the single
   place that decides which rows back a record) ties the bore diameter to them.

2. **`_seat_present` was under-sampled and its paragraph overclaimed a third
   time.** Three genuinely destroyed seats read `true` with no warning: the
   seat region milled off with a 2×2 mm pin left at one probe azimuth (volume
   303 967 against the control's 429 198); a 14 mm slot milled across it
   leaving 0.25 mm crescents (415 856); and the pocket **filled solid**
   (430 091 — *above* the control). The filled case is invisible at any
   sampling density, because the probe asked "is there material around the
   seat" and never "is the seat a void". Added: one point inside the seat
   required to be OUT — the coordinator's suggestion, and the reasoning checks
   out, since an interior point is inside the part's footprint wherever the
   seat is and so cannot degrade the edge case. The filled case now degrades to
   `⌀9` with a warning.

   The pin and the slot are **not** fixed, and that is now a measured choice
   rather than an oversight. A bounding-box-filtered `all` catches both and
   keeps the x=12.6 edge case *and* the edge-breaking seat — but reads `false`
   on a **correct** counterbore beside an ordinary pocket touching or
   overlapping its probe ring, i.e. it degrades a true drawing on a routine
   layout. Measured, all six cases, so the trade is on the record:

   | case | `any` (shipped) | bbox-filtered `all` |
   |---|---|---|
   | control | true | true |
   | seat gone, one 2×2 pin | true | **false** (caught) |
   | 14 mm slot across it | true | **false** (caught) |
   | edge seat x=12.6 on a 40 plate | true | true |
   | seat breaking the part edge | true | true |
   | **correct** seat beside a pocket touching its ring | true | **false** (wrong) |

   The prose in `drawing.py`, `AGENTS.md` and `docs/agent-api.md` now reads
   "nothing surrounds it at any of four azimuths at its mid-depth, or its space
   is not empty", names the two classes it cannot see, and both are pinned by
   tests so the gap cannot change without someone deciding to change it. Also
   kept, because it is worth not re-deriving: a countersink cone's radius at
   mid-depth is `(seat_r + bore_r)/2` **independent of the included angle**,
   which is why the outer probe is outside it for every angle and why the void
   radius can be stated without one.

3. **`_cell_paths` was an allowlist, not a complement.** `size/pitch` listed
   `(*row["pitches"], "coarse_pitch")` by name, so any *other* scalar on a
   thread row was undeclared — measured, `M8/preferred_pitch` loaded clean. It
   is now every key that is not the `pitches` container. **The seat tables stay
   at row granularity, and that is now stated instead of implied**: a cell is
   the path `row_shape` names and no finer — one fit, one pitch or other
   scalar, one head size — and the fields inside a cell (`{d, drill}`,
   `{head_d, head_h}`) are covered by that cell's citation. 248 declarations
   against 442 scalar leaves, said in the refusal message itself, in
   `AGENTS.md`, and in `_cell_paths`'s docstring. The reasons: a cell is what
   one line of the published table prints, `_prov_scope` resolves at exactly
   that depth (declaring below it would make a cell's provenance a union over
   its fields — a redesign), and a fabricated field no lookup reads is inert.
   A fabricated `fabricated_mm` on a cbore row therefore still loads, by a
   decision rather than by accident.

4. **This entry contradicted itself**, and `_INVISIBLE` was three codepoints
   short of its own docstring. The round-2 paragraph described `scopes` as "a
   single row or a single cell" — the sentence it says it corrected in the PRD
   — and quoted a refusal (`"row(s) ['1-1/8'] declare no sources"`) the loader
   no longer emits. Both fixed, with a note marking the round-2/3 paragraphs as
   the record of how the rule got here rather than what ships. `_INVISIBLE`
   gained U+00AD, U+200E and U+180E (eight codepoints, now named one by one
   rather than described as "zero-width characters", which is a wider claim
   than a finite set can honour) and a parametrised test walks the same list.

### Round 6: a justification that failed a two-line probe

The verifier returned SHIP with no finding against the system — `rows_for_record`
survived every steering key it could reach, the diameter tolerance accepts one
ulp and refuses +2e-9 with 0 legitimate records refused across a 20-record
sweep, and it disproved its own best remaining attack (a fabricated counterbore
pocket validates clean, but that is byte-identical to what the documented
`cbore_d`/`cbore_depth` author override produces, so a table check there would
be a false positive on a supported API). One wrong sentence, and one test named
for more than it built:

1. **`_cell_paths`' justification was checkably false.** It said the finer
   design's *"only gain is refusing a fabricated field that no lookup reads"* —
   but two lookups read an **optional** in-cell field with a default
   (`_clearance_cell`'s `cell.get("drill")` and `lookup`'s
   `entry.get("drill")`), and ISO thread pitch cells carry no `drill`, so one
   is addable. Measured: `drill: "FAKE-99"` inside the *declared* `M8/1.25`
   cell loaded and `thread("M8")` served it with `corroborated: True` — a
   fabricated machinist drill designation presented as corroborated data.

   The decision stands and the sentence is replaced by the true one: **a value
   added to an optional field is exactly as uncatchable as a value edited in a
   required one** — anyone who can add `drill` can change `tap_drill`, and
   coverage proves citation, never correctness, at any granularity. Going finer
   would not change that.

   The other half of the offer was taken as well, because it closes something
   real: `_check_cell_fields` is a closed per-`row_shape` schema of the field
   names each cell may and must carry. It refuses a fabricated `fabricated_mm`
   (was ACCEPTED) and a typo like `head_dd` (was ACCEPTED — and a typo is worse
   than a fabrication, because it leaves `cbore()` to raise `KeyError` halfway
   through an answer instead of failing at load naming the file). It does
   **not** refuse `drill`, because `drill` is a legitimate field of a pitch
   cell, and the docstring says so rather than implying the check is wider than
   it is. It cannot touch the `cbore_d`/`csk_d` author overrides: those are
   arguments to `holes.counterbore`/`countersink`, not table fields.

   `_cell_items` is now the single walker `_cell_paths` and `_check_cell_fields`
   both come off, so the set the coverage proof is over and the set whose field
   names are checked cannot drift apart — two walkers over one structure is how
   `coarse_pitch` came to be outside the coverage set in the first place.
   `_row_paths` fell out unused and is gone.

2. **The two-class seat test built only one class.** It was named for the pin
   *and* the slot and constructed the pin, with the slot's volume carried in
   prose — the same "a documented miss that is only prose can drift" argument
   that motivated pinning the pin. Both are now parametrised cases, and each
   asserts the part's **measured** volume (303 967 and 415 856 against the
   control's 429 198) through `get_metrics`, so the test cannot pass by echoing
   the record.

Also recorded rather than changed: the void probe samples one azimuth against
the outer ring's four. The two need opposite quantifiers — the ring asks `any`
because a missing azimuth may be a legitimate edge, so more samples only add
ways to say yes; the void probe refuses on any filled sample, so more samples
only add ways to say no, and a partly filled seat is still a seat whose callout
is right about its diameter.

## Files

- `agentcad/toolkit/hole_standards.py` — per-CELL provenance loader
  (`_ROW_SHAPES`, `_row_paths`, `_cell_paths`, rewritten `_check_provenance`
  with group expansion and a coverage proof over cells, plus a `/`-collision
  guard); `_prov_scope` raises instead of falling back to a default;
  `_source_key` normalises citations for the distinctness rule;
  `SHIPPED_TABLES` + `validate_all()` called at import; `_source_key` folds
  whitespace, case, zero-width characters and `http`/`https`; `coarse_pitch`
  is a cell; `corroborated` requires no conflicts; `designation()` prints a
  blind hole's depth for every seat family; new `RECORD_FAMILIES`,
  `RECORD_KEYS`, `merge_provenance` (whose `standard` is always a list),
  `_cell_items` (the one cell walker) + `_check_cell_fields` (a closed
  per-shape field-name schema); `rows_for_record` / `provenance_for_record`,
  `designation_for_record`,
  `validate_record` (which re-derives the designation, the provenance AND the
  bore diameter from the record's own fields and compares all three);
  `_NAMES_BY_FAMILY`; `size`/`fit` are typed `RECORD_KEYS`
- `agentcad/toolkit/data/{iso,ansi}_{clearance,thread,cbore_csk}.json` —
  `row_shape` header, `provenance.groups` naming every **cell** (216 across the
  six files, 248 after `coarse_pitch` joined them), `default` removed;
  `ansi_clearance` records the `#8 NORMAL` dispute on the cell and revises its
  notes
- `agentcad/toolkit/holes.py` — `_axis_proof` and the three-rung default tier
  in `_guard`; `>=` on the through-depth guard; `_drill` filters the record to
  the instances that removed material and adds `verify`/`dropped`; every
  helper's designation is derived by `designation_for_record`, plus
  `designation_base`
- `agentcad/kernel/handlers/holes.py` — the harvest raises
  `hole_standards.validate_record`'s verdict; its private `_REQUIRED` list is
  gone; a record carrying `dropped` instances becomes a rebuild warning
- `agentcad/kernel/handlers/drawing.py` — `_record_problem` runs the shared
  validator; `_bottom_present` / `_matched_world_centers`; the callout text
  degrades to `designation_base` and the group reports `bottom_present`; the
  module docstring's "re-measures everything it asserts" is now the list of
  four things it measures and a paragraph on what it does not; `_seat_present`
  / `_seat_geometry` / `_without` degrade a counterbore or countersink whose
  seat has been machined away or filled back in
- `agentcad/core/tools_holes.py` — `_sidecar_problem` (cache key, records,
  four-state invariant); `_read_sidecar(path, key)`; `add_holes` echoes the
  row's `provenance` through `merge_provenance` over the same rows the record
  uses; sidecar version 3 (records gained `provenance`)
- `agentcad/core/templates.py` — the CHEATSHEET's polar rule, verify tiers,
  designation table, record key list and guard list, none of which had round 2
- `tests/test_hole_standards.py` — the per-row rule, an undeclared row refused,
  a scope backing nothing refused, corroboration vs agreement
- `tests/test_holes.py` — the frame that cuts air (both tiers), the gap
  between two solids of one Compound part, the probe rungs, `verify="off"`,
  blind and through seat callouts, `designation_base`, every helper against the
  shared validator, `depth == stock`
- `tests/test_drawing_holes.py` — the stale blind depth degraded and the live
  one kept, a forged designation refused; `PLATE`'s tapped hole is now blind at
  8 mm on a 12 mm plate (at `depth=12` it was through, so the fixture asserted
  the very defect)
- `tests/test_tools_holes.py` — sidecar acceptance and every rejection; the
  echo and the record agreeing about provenance
- `tests/test_hole_metadata.py` — a no-op instance warned about on the harvest,
  a drifted designation as a `contract_error`; the sidecar version assertions
  read `HOLES_SIDECAR_VERSION` instead of the literal `1`
- `tests/test_prd010_acceptance.py` — same fixture correction as `PLATE`
- `docs/prd/in-progress/PRD-010-feature-toolkit-ii.md`, `docs/agent-api.md`,
  `docs/part-authoring.md` — the shipped guarantee equals what the loader
  enforces; the record's measured `count`; the blind-depth symbology

## Notes

- **The axis probe is a proof in one direction only.** A point strictly inside
  the solid on the bore axis proves the intersection has volume; *not* finding
  one proves nothing (a thin wall between samples, a tangency), which is why
  that branch escalates to the boolean rather than concluding. More samples can
  therefore only save time, never change an answer — the sample count is a
  performance knob and not a tolerance.
- **`_bottom_present` answers one question and not the interesting one.** It
  detects that the bottom is *gone*; it does not measure how deep the hole now
  is, and must not be described as doing so — which is exactly what its old
  name `depth_verified` did, and why a hole made shallower from the top printed
  `↧6` over 3 mm of stock with `true` beside it.
- **Round 3's theme, worth carrying forward: every one of the five findings was
  a CLAIM that outran its measurement, and three were introduced by round 2's
  own fixes.** Coverage said "per row" and the row was not the unit;
  `corroborated` was computed honestly and carried nowhere; `depth_verified`
  named a check that verifies a bottom, not a depth; `verify` named a tier it
  does not know. The code was right in each case and the label was not. Prefer
  a field name that under-claims.
- **Whole suite on the committed tree: `make test` — 2527 passed, 1 skipped**
  (`uv run pytest -q -n 2 --dist loadscope`, 1500.46 s, exit 0), run after
  round 6 with no edit landing during it. `test_prd010_acceptance.py::
  test_ac8_the_full_suite_count_is_cited` requires the newest changelog entry
  to cite a count, and for most of this entry's life it passed on prose rather
  than on a number that had been measured — precisely what that check exists
  to stop. The intermediate runs are kept here as provenance and **not** as
  this entry's count, because each measured different bytes: 2439 on the
  round-2 tree, 2473 after round 3, 2482 mid-round-5. Round 3 alone changed
  the record shape, the data files and the test set.
- Measured here after round 6 (`uv run pytest -q tests/test_holes.py
  tests/test_hole_standards.py tests/test_hole_metadata.py
  tests/test_drawing_holes.py tests/test_drawings.py tests/test_tools_holes.py
  tests/test_toolkit_ocp_free.py tests/test_prd010_acceptance.py`): **314
  passed**, 15.85 s. `tests/test_examples.py`: **20 passed**, 1095.20 s — the
  geometry is untouched by any of this (only the measurement, the record and
  the annotation layer changed), and that run is the check on the claim. Every
  bundled part that drills through `toolkit.holes` — `construction/
  angle_bracket` (2 records, 4 instances) and `construction/gusset_plate`
  (1 record, 18 instances) — reports every instance `engaged` on the `axis`
  rung, drops nothing, and validates against the shared record contract.
