# 0182 — PRD-011 review fixes: catalog content (geometry and spec strength)

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (Opus 5)

## Summary

An independent review of PRD-011 returned CHANGES-REQUIRED with five findings
against the **content** of the seed catalog (the gate itself is fixed
separately). One was a shipped-green geometry defect: the 2020 and 3030
extrusions built **five disconnected solids**. The other four were specs that
could not fail and prose that did not describe what shipped. Every catalog
package the fixes touch was re-published through the real gate, so
`catalog/index.json` stays a build product.

The reviewer verified every hand-typed standards value in the catalog against
the published standards and against bd_warehouse's own tables and found them
**all correct**. Nothing here changes a table row.

## The findings, measured before and after

### C3 — the extrusions built five loose pieces, gate green

`_profile()` cut each T-channel as two rectangles: the mouth
`Rectangle(LIP_T, SLOT_OPEN)` — the full wall thickness, so it cut every face's
wall clean through — and the channel `Rectangle(SLOT_DEPTH - LIP_T,
SLOT_INNER)`. `SLOT_INNER / 2` is 5.75 and `SIZE / 2 - SLOT_DEPTH` is 3.5, so
adjacent channels intersected and severed every diagonal web. Reproduced with
the reviewer's probe:

| | before | after |
|---|---|---|
| `extrusion_2020` solids | **5** | **1** |
| `extrusion_2020` section | 151.40 mm² | **182.32 mm²** (0.492 kg/m) |
| `extrusion_3030` solids | **5** | **1** |
| `extrusion_3030` section | 382.93 mm² | **404.99 mm²** (1.094 kg/m) |

Both are `is_valid`, `is_manifold`, 47 faces, and the section is unchanged over
`length` 10 / 100 / 1000 mm.

The profile is now what a real T-slot section is: an outer skin, a central
bore boss, four **constant-thickness diagonal webs** (2.0 mm on the 2020,
2.5 mm on the 3030) joining the boss to the four corners, and the four
T-channels as the voids left between them. Each channel is subtracted as one
polygon whose two tapering edges lie **on the web faces** — both endpoints sit
`web_t / 2` from the 45° centreline, so the edge is parallel to it — and the
boss is added back afterwards, which makes the bottom of every channel the
boss's own cylindrical surface at exactly `slot_depth`. The interface numbers
the reviewer verified are unchanged and are now all realised: 6/8 mm mouth,
2.0 mm lip, 11.5/16.5 mm channel width behind it, 6.5/8.5 mm depth, ⌀4.2/⌀6.8
bore.

There is **no dimensional standard** for T-slot framing, so the section-area
window is derived from mass: commodity 20-series slot-6 bar is published at
roughly 0.45–0.55 kg/m and 30-series slot-8 at 0.95–1.25 kg/m, which at 6063
aluminium's 2.70 g/cm³ is 167–204 mm² and 352–463 mm². The windows are
**165–205** and **350–465** mm², and both models land mid-band.

Two new specs, and an honest statement of what each one buys:

- `one_connected_solid` — `len(part.solids()) == 1`. Red on both sabotaged
  profiles.
- `section_area` — volume (summed over `part.solids()`, because a nested
  `Compound.volume` undercounts) over the Z extent, inside the window. Red on
  the 2020 sabotage (151.40 is under the floor). **Green on the 3030 sabotage**
  — 382.93 mm² is inside that profile's band. The window is kept honest to its
  reference rather than narrowed until it would have caught it; the
  connectivity check is the one that saw the 3030 break, and both READMEs say
  so.

A second find while building it, worth the comment it now carries: subtracting
each channel through a nested `BuildSketch(Plane.XY.rotated((0, 0, angle)))`
**silently subtracted nothing** for one of the four faces — 400 → 351.51 →
351.51 mm², no error raised. The shipped code rotates the coordinates by exact
integer quarter turns instead. OCCT succeeding is not evidence.

### M5 — the extrusions' length spec never read `p.length`

The predicate asserted `bbox.min.z ≈ 0` and `10 ≤ height ≤ 1000` — the
parameter's own declared range, true for every legal value. `build` now records
`p.length` on the returned solid (`part.cut_length_mm`, the pattern
bd_warehouse fasteners use with `screw_size`/`length`) and the predicate
measures the Z extent against it. A 900 mm measurement while 250 mm was asked
for was `True` before and is `False` now.

### M4 — `din625`'s specs were circular and could not fail

`_designation_from(metrics)` selected the table row by matching the **built**
`D` and `B`, so those two checks compared the geometry with the row it had just
been used to pick, and the bore check compared the table with itself. The
reviewer's probe sabotaged `_row` to build a 608 whatever the parameter said
and the gate reported **77 of 77 rows green** (56 of them spec rows).

The row now comes from the row the **parameter** names: `build` records
`p.designation` on the solid and `_declared_row(part)` reads it back. Same
probe, after: **red, publishable false, 43 of the 70 spec rows failed** —
every designation except 608, which is the one the sabotaged build really does
produce, and the 628, whose bore is also 8 mm so its bore row alone stays
green. A fifth check, `ring_faces_din625`, reads the four cylindrical
**faces** (bore, the two ring-split walls at 30%/70% of the radial section, the
OD) against the same declared row, sharing no input with the three
bounding-box checks.

### M6 — `iso4014` claimed a partial thread it does not build

Measured on the shipped `m8x30` preset: one cylindrical face, ⌀6.647, spanning
the whole 30 mm, and `thread_length == 30.0`. ISO 4014 M8×30 has b = 22 mm of
thread and an 8.000 mm plain shank.

The pinned **bd_warehouse 0.3.0 cannot build one**, and this was checked rather
than assumed: `Screw.__init__` sets `thread_length = length - length_offset`
unconditionally, no screw class takes a thread-length argument
(`inspect.signature` over every class in `bd_warehouse.fastener`), and the
`iso4014` rows of `HexHeadScrew.fastener_data` carry only `k`, `s`, `short` and
`long` — there is no `b` column. Both `thread` values are affected: `cosmetic`
*is* the root cylinder, `real` adds helical geometry on top of it, and neither
adds a plain shank.

So the package now claims what it builds — the **ISO 4014 head** (`s`, `k`) on
a fully threaded shank, geometrically an ISO 4017-shaped screw with ISO 4014
head heights, which are not the same (`k` for M8 is 5.30 in ISO 4014 and 5.54
in ISO 4017) — and says where that is wrong to use (a bolt in shear, a reamed
fit). ⌀6.647 is the basic minor diameter d1 = d − 1.0825 P, parsed out of the
designation rather than typed into a table.

`shank_full_length_root` pins it: exactly one cylindrical face at d1 spanning
`z = -length` to `z = 0`. It discriminates in both directions — a synthetic
bolt with a real 8 mm plain shank at ⌀8 fails it (no full-length cylinder), and
a full-length shank drawn at the nominal ⌀8 fails it too. If a later
bd_warehouse builds the partial thread, the check goes red, which is the
notification that the docs have to change.

### m13 — prose that contradicted the code

- `iso4762`'s README said `cosmetic` draws the shank "at the nominal diameter"
  while the same README's table and the part docstring say the thread **root**
  (⌀4.134 on M5, and the measurement agrees). The README now says root.
- The `dk` column was labelled "the standard's max column". ISO 4762 has two
  maxima and these are the **knurled-head** ones (M3: 5.50 plain, 5.68
  knurled). The values are right — they are what bd_warehouse models
  (`SocketHeadCapScrew.fastener_data["M3-0.5"]["iso4762:dk "] == "5.68"`) and
  therefore the only column a measurement of this solid can match — so the
  label is corrected, not the number.
- `thread_insert` claimed "M2 through M6" and ships five sizes with **no M5**.
  bd_warehouse 0.3.0's ruthex data has exactly six populated rows (M2-0.4-4,
  M2.5-0.45-5.7, M3-0.5-4.0, M3-0.5-4.0Voron, M4-0.7-4.0, M6-1-6.8); its M5
  designations exist with every ruthex field empty, so there is no M5 to build.
  The M3 and M4 rows are ruthex's **short** 4.0 mm inserts (`GE-M3Sx40-002`,
  `GE-M4Sx04-1`). The claim now names the five sizes and the gap, and the M3
  preset's "the common one" is replaced by the part number.

## Changes

- `catalog/extrusion_2020/1.0.0/**`, `catalog/extrusion_3030/1.0.0/**` — new
  connected profile, `WEB_T` and the derived `BOSS_R`, the section-area band,
  two new specs (`one_connected_solid`, `section_area`), a length spec that
  reads the parameter, regenerated 640×480 previews, rewritten README section.
- `catalog/din625/1.0.0/**` — parameter-named table row, `ring_faces_din625`,
  README rewritten around what the checks can now fail on.
- `catalog/iso4014/1.0.0/**` — honest full-thread claim in the docstring,
  README, `package.json` summaries and preset descriptions;
  `shank_full_length_root` spec and `_thread_root_diameter`.
- `catalog/iso4762/1.0.0/**` — README shank diameter corrected to the root; the
  `dk` column relabelled in the README and in the part's table comment.
- `catalog/thread_insert/1.0.0/**` — size claim corrected in the docstring,
  README, `package.json` summaries and preset descriptions.
- `catalog/index.json` — regenerated by `agentcad publish` for the six affected
  packages (see "Republishing").
- `tests/test_catalog.py` — four new negation tests, 63 → 67.

## Files

- `catalog/extrusion_2020/1.0.0/parts/extrusion.py`,
  `catalog/extrusion_3030/1.0.0/parts/extrusion.py` — profile, specs, `build`
- `catalog/extrusion_*/1.0.0/docs/README.md` — the section and why it is one
  solid, with the numbers
- `catalog/extrusion_*/1.0.0/previews/extrusion_iso.png` — re-rendered
- `catalog/din625/1.0.0/parts/ball_bearing.py`, `docs/README.md`
- `catalog/iso4014/1.0.0/parts/hex_bolt.py`, `docs/README.md`, `package.json`,
  `presets.json`
- `catalog/iso4762/1.0.0/parts/cap_screw.py`, `docs/README.md`
- `catalog/thread_insert/1.0.0/parts/heat_set_insert.py`, `docs/README.md`,
  `package.json`, `presets.json`
- `catalog/index.json`
- `tests/test_catalog.py`

## Republishing — pre-release regeneration, not a version bump

A published version is **immutable**: `LocalIndex.publish` refuses an existing
`name@version` in the document *or* on disk, even when the content id matches.
The six changed packages were therefore removed from the index tree — entry and
directory — and published fresh **at the same 1.0.0**.

That is honest here and only here: this branch is unmerged, `catalog/` has
never been served from anywhere else, and nothing outside this repository has
ever resolved these packages, so no consumer can hold a 1.0.0 whose bytes
differ from these. Had one existed, the answer would have been 1.0.1. The three
untouched packages (`iso7380`, `nema17`, `nema23`) were left alone and keep
their original content ids and `report_id`s.

Every entry in `index.json` is still written by `agentcad publish` from the
gate's own measurements; the only hand-written part remains the four-line empty
document.

```
$ agentcad publish <scratch>/pkgsrc2/<pkg> --index agentcad-core \
      --projects-dir <scratch>/pubprojects --work-dir <scratch>/pubwork
```

| package | content id | stages | rows | verdict |
|---|---|---|---|---|
| `din625` | `sha256:f0fd451b…` | 9 green | 70 spec rows | gate green, exit 0 |
| `extrusion_2020` | `sha256:d64f92f2…` | 9 green | 42 spec rows | gate green, exit 0 |
| `extrusion_3030` | `sha256:433284cd…` | 9 green | 42 spec rows | gate green, exit 0 |
| `iso4014` | `sha256:ece0f695…` | 9 green | 85 spec rows | gate green, exit 0 |
| `iso4762` | `sha256:a6bf9ae0…` | 9 green | 80 spec rows | gate green, exit 0 |
| `thread_insert` | `sha256:3b68c8b5…` | 9 green | 52 spec rows | gate green, exit 0 |

Each carries the one exempt skip the whole catalog carries,
`policy:no_policy_configured`. `din625` gains 14 spec rows (a fifth check over
14 build variants) and `iso4014` 17, which is where the row counts moved.

## Verification

Targeted:

```
$ .venv/bin/python -m pytest -q tests/test_catalog.py \
    tests/test_prd011_acceptance.py
82 passed in 53.27s
```

(67 in `test_catalog.py`, up from 63, plus the 15 acceptance tests.)

`test_every_catalog_package_passes_the_gate` (nine packages, all nine stages)
and `test_the_catalog_serves_byte_identically_through_a_git_index` — the
dogfood byte-identity test — are both in that first run.

Full suite (`make test` is `test-full` is this command), on the working tree
this entry describes — which also carries the concurrent gate/trust-chain
change, so the count is not attributable to this entry alone:

```
$ .venv/bin/python -m pytest -q -n 2 --dist loadscope
4 failed, 3193 passed, 1 skipped in 1555.00s (0:25:54)
```

**3193 passed**, against 3151 in changelog 0180 — +42. That attribution has
since been **recounted from the diff** rather than estimated, and it was wrong
in both directions: `tests/test_catalog.py` goes **23 → 67 collected cases
(+44)**, all of them this entry's, and the concurrent gate/trust-chain change
(0181) accounts for **+51** across its own twelve files. The +42 measured here
therefore predates part of both changes; the combined tree is **+95** against
0180's 3151, and the number to trust is the one measured on the committed
tree. The one skip is the pre-existing `tests/test_analysis.py:166` FEM
fallback.

The four failures were all the same evidence check, in its four copies
(`test_prd008_acceptance.py::test_ac9`, `009::test_ac6`, `010::test_ac8`,
`011::test_ac8`): *"the newest changelog entry cites no suite count"* — this
entry, before this section existed. That is the rule working, and it is
circular by construction: the count cannot be written down until the run that
produces it has finished. Re-run after writing it:

```
$ .venv/bin/python -m pytest -q tests/test_prd008_acceptance.py \
    tests/test_prd009_acceptance.py tests/test_prd010_acceptance.py \
    tests/test_prd011_acceptance.py -k the_full_suite_count_is_cited
4 passed, 78 deselected in 1.17s
```

No other test failed.

One honest caveat on the full-suite run: it predates the last `din625` prose
correction (the row counts in its docstring and README) and that package's
re-publish, so it measured content id `sha256:2b17bc58…` where the tree now
holds `sha256:f0fd451b…`. What that change can touch — the content-id match,
the gate run and the byte-identical git-index dogfood — is exactly what
`tests/test_catalog.py` covers, and the 82-passed line above is *after* it.

## Notes

- **The area window does not replace the connectivity check, and the 3030 is
  the proof.** A defect that reddens on one package's plausibility band and
  hides inside another's is exactly why `n_solids == 1` is written as its own
  check rather than inferred from a number.
- **A spec that selects its own reference from the measurement it is about to
  make measures nothing.** `din625` did that with the table row; the extrusions
  did it with the parameter range. Both are now anchored to something the
  caller supplied — the parameter, carried on the built solid — which is the
  same pattern the ISO screw packages already used through bd_warehouse's
  `screw_size`.
- ~~**`docs/packages.md:387` still reads "heat-set inserts, M2–M6"**~~ —
  **withdrawn, this was already fixed.** In the combined tree that table reads
  "heat-set inserts — the five ruthex sizes the pinned bd_warehouse ships (M2,
  M2.5, M3 short, M4 short, M6; no M5)", and the `iso4014` row carries the
  fully-threaded note. The claim was written against a stale read of a file the
  concurrent change was editing at the same time; recorded as withdrawn rather
  than deleted, because a follow-up nobody needs is worse than none.
- The bd_warehouse limitation behind M6 is a **library** limitation, not a
  modelling choice; if `bd_warehouse` gains a thread-length argument, the fix
  is to build the real partial thread and let `shank_full_length_root` redden
  until the docs follow it.
