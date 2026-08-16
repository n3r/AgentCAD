# 0162 — PRD-010 review round 2: where a pattern went, and what a mitre promised

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

Second round of fixes against an independent Codex (GPT-5.6) review of PRD-010,
covering the geometry half: `toolkit/patterns`, `toolkit/sheetmetal`, the
hole-form state in `frontend/js/main.js`, and two tests in `tests/test_features.py`
that required an OCCT defect to persist. The review read the pre-round-1 tree,
so two of its five findings here were already closed and are recorded as
re-measured rather than re-fixed.

The through-line is the same one PRD-010 started with, one layer up: this time
the checks that catch silent geometry were themselves blind in a direction.
`patterns.polar` could place every instance in the wrong place with the right
volume, one valid solid and no warning; a `close` sheet-metal corner could
promise a seam and deliver a quarter of one while moving no material at all.

## Changes

### `patterns.polar(..., radius=r)` placed instances wrong, silently (MAJOR)

`polar` built a placement for every point on the requested circle and then
discarded `placed[0]`, on the assumption that index 0 is the seed. That holds
only for `radius=None`, where `PolarLocations(0, n)`'s first location is the
identity. With a radius, *every* placement translates onto the circle, so index
0 is a real instance and skipping it drops it.

Measured before the fix, a centre boss patterned `count=4, radius=20` on a
120×120×10 plate:

```
warning : None
solids  : 1     is_valid: True
added   : ≈904.77868 mm^3   (= 3 x ≈301.59289, exactly the expected 3)
centres : (-20, 0), (0, -20), (0, 0), (0, 20)
```

(The boolean's last digits are OCCT noise — repeat runs land on
`904.7786842338537` and `1206.3715789784692`/`…85201` for the after figure, an
11th-significant-figure difference. The instance *centres* are exact and are
what the claim rests on.)

The `(+20, 0)` instance was never placed and the seed was counted in its stead.
Right volume, right solid count, every instance `engaged` — the two-tier guard
had nothing to say because none of its tiers looks at *where*.

- **The skip is now the transform, not the index** (`_identity_placement`): the
  placement that moves the seed nowhere is the seed, and with a radius there is
  none, so all `count` instances are added and the seed is left where it was
  with a warning saying the part now carries `count + 1` copies. The test is
  the location matrix rather than the resulting position on purpose — a
  rotationally symmetric seed spun about its own axis lands its bounding box
  back where it started at *every* placement, and a position test would turn
  that pattern into a silent no-op.
- **`radius=None` is untouched**: the identity is still at index 0, the kept
  list is still `placed[1:]` in the same order and the same `Locations` block
  runs, so the byte-identity property (changelog 0149, `test_polar_helper_is_
  byte_identical_to_the_handwritten_form`) is preserved by construction.
- **Every report row now carries `center`** — for `linear`, `polar` and
  `mirror` — and two layout assertions check them against what was asked for:
  `_polar_layout_warnings` (all instances equidistant from the axis, evenly
  spaced by `span/count`, and on the requested radius) and
  `_linear_layout_warnings` (consecutive instances exactly `spacing` apart
  along the direction, with no drift across it). Both are arithmetic, so they
  run whatever `verify` says.

After the fix, the same probe:

```
warning : patterns.polar: radius=20 translates every instance onto the circle,
          so none of them is where the seed already sits: all 4 were ADDED …
added   : ≈1206.37158 mm^3   (= 4 x ≈301.59289)
centres : (20, 0), (0, 20), (-20, 0), (0, -20)   [+ the seed at (0, 0)]
```

#### `center` is a rigid image, never the moved shape's bounding box

The first version of that layout assertion measured each instance's
**bounding-box centre after the placement**, and that metric is wrong: a
bounding box is only rotation-invariant for a seed with 180° point symmetry.
Every pattern test in the suite used a box or a cylinder, both of which are
immune, so it went green while firing on geometry that is right. Measured on a
right-triangular gusset boss — one valid solid, added volume exact to 6e-11:

| correct pattern | moved-bbox radii from the axis | spread | verdict |
|---|---|---|---|
| `count=3`, `radius=None` | 32.535898 … 36.055513 | **3.5196 mm** | warned |
| `count=5` | 32.204836 … 36.055513 | **3.8507 mm** | warned |
| `count=8` | 33.015148 … 36.055513 | **3.0404 mm** | warned |

— every one reported as "a placement bug, not a tolerance". The fix is the
metric, not the tolerance: `center` is now the **rigid image of one reference
point** (the seed's own bounding-box centre, measured once on the *unmoved*
seed) under that instance's placement, which is exact under rotation by
construction. The same three patterns then spread `0.0` before rounding and
2.9e-10 … 8.7e-10 after `POSITION_DECIMALS`, and are silent; the placement bug
still separates the seed from the circle by the full 20 mm. `_identity_placement`
already tested the *transform* rather than the resulting position, for exactly
this reason — the assertion was built on the wrong quantity next door to the
docstring saying so. `mirror`'s centre is likewise computed (reflecting the
reference point in the plane) rather than measured off the image, so `center`
means one thing everywhere in the report.

`_PLACE_TOL`/`_ANGLE_TOL` stay 1e-6 mm / 1e-6 deg, but the honest argument for
them is the metric, not headroom: on rigid images a correct pattern's radius
residual is exactly zero, and what the assertion actually reads is the
9-decimal rounding. Widening a tolerance to cover the 3.5 mm the old metric
produced would have had to swallow the 20 mm bug with it.

#### The radius warning's advice had to be followable

The warning told the caller to "author the seed at the axis and pass `radius`"
— which *guarantees* the leftover centre feature it is complaining about. There
is no argument to `polar` that consumes a seed it was handed, so `radius > 0`
always leaves one over. The warning now leads with the route that does work
(build the seed where instance 0 goes, call with `radius=None`, and the seed
*is* instance 0 — a clean circle of exactly `count`, asserted by a new test),
says plainly that no argument removes a seed, and reframes `radius` as being
for a seed authored at the axis where the leftover is deliberate (a hub with
its bolt circle). It also no longer asserts a total the helper cannot verify;
it reports what it added.

### A `close` sheet-metal corner promised a seam it could not form (MAJOR)

`_conserved` (round 1) already catches two features fusing into one smaller
solid — the review's own probe reproduces with the warning intact, see
"already closed" below. A mitred corner is the *other* half of the same class
and `_conserved` is structurally blind to it: both leaves are cut by the **same**
plane, so neither can cross it, no volume moves, and they still fuse through
the base plate into one valid solid. Only the seam is missing.

So the seam is what is measured — the face area the two mitred flange solids
actually share, from `(A.area + B.area − (A+B).area)/2` (the `patterns`
precedent: an intersection is empty for a face-to-face seam exactly as it is
for two shapes that never meet), against the `√2 · min(profile area)` a 45°
mitre promises. Measured on a 60×40 plate:

| corner | seam / promise | |
|---|---|---|
| 90/20/R3 vs 90/20/R3, t=2 | 1.000000000000 | the whole seam |
| 90/20/R3 vs 90/10/R3, t=2 | 1.000000000000 | a shorter leaf is fine |
| 90/20/R3 vs 90/40/R3, t=2 | 1.000000000000 | |
| 120/8/R4.5 both, t=3 | 1.000000000000 | |
| 179/6/R2 both, t=2 | 1.000000000000 | |
| 90/20/R3 vs 90/20/**R2.9**, t=2 | 0.958597256259 | radius differs |
| 90/20/R3 vs **89**/20/R3, t=2 | 0.933701732113 | angle differs |
| 90/20/R3 vs **45**/10/**R1**, t=2 | 0.267448702599 | the review's probe |
| **45**/12/R1 both, t=1 | 0.190203814953 | acute, and *matched* |
| **30**/25/R0.8 both, t=0.8 | 0.069572222109 | acute, and *matched* |

Two independent causes, and the second was not in the review:

1. **The profiles must agree.** One plane cuts both leaves, so each leaf's cut
   face is its own cross-section read as a function of outward distance from
   the plate edge; the two faces coincide exactly when the profiles do. A
   different leaf *length* is fine — the shorter face is seamed whole — a
   different bend angle or inner radius is not.
2. **The leaf must fit its mitre extension**, which is about the leaf length
   and not the angle alone. `_effective_span` runs a close-corner flange past
   the corner by `inner_radius + thickness`, the outward reach of a **90°
   profile and of nothing else**; a profile at bend angle `a` reaches
   `(R + t)·sin a + L·cos a`, so the extension holds iff

   ```
   L  ≤  L_max = (R + t) · tan(45° − a/2)        (`_max_mitre_leaf`)
   ```

   which is *infinite* at and above 90° (a vertical leaf adds no outward reach;
   past 90° the leaf comes back) and a small positive number below. **90° is a
   discontinuity, not a limit**: `L_max` falls to 0 as `a → 90⁻` (0.0436 mm at
   89°, 4.36e-05 at 89.999°, 4.36e-09 at 89.9999999°) and the value *at* 90° is
   unbounded, because the constraint it comes from is
   `L·cos a ≤ (R + t)(1 − sin a)` and **both sides vanish** there — dividing
   through by `cos a` to reach `L_max` is the step that loses it. Read the
   `inf` as a one-sided limit and you conclude the exact opposite of the truth.
   Matched
   **acute** corners inside that bound seam whole and stay quiet — measured at
   t=2, all `1.000000000000` with no warning: 60°/R3/L0.2 (reach 4.4301 of
   5.0), 45°/R3/L0.5 (3.8891 of 5.0), 30°/R5/L1 (4.3660 of 7.0), 10°/R5/L1
   (2.2003 of 7.0), 20°/R4/L2 (3.9315 of 6.0), 45°/R12/L1 (10.6066 of 14.0).
   It is the ordinary *long* leaf that breaks: 45°/R1/L12 wants 10.6066 mm and
   gets 3.0, seaming 0.2810. How *far* past the limit is what matters, not the
   leaf length alone — `L_max` scales with `R + t`, so the same 12 mm leaf at
   45°/R3 has a 2.0711 mm limit instead of 1.2426 and seams 0.4103. Feeding the
   required reach back in takes the failing rows to `1.000000000` exactly.

   An earlier draft of this entry said "`close` is only sound from 90° up".
   That was more pessimistic than the code, which has always tested the reach;
   `L_max` is closed-form, verified against a bisection on the reach predicate
   itself to 4e-9 mm over six `(t, a, R)` combinations, and is now what the
   warning quotes — it is the number an author can act on.

   **`_profile_reach` drops the leaf term from 90° up**, which is exact
   arithmetic and not a tolerance: `cos a ≤ 0` for every `a ≥ 90`, so the term
   can only pull the reach back. Computing it anyway leaked a positive
   `L · 6.123233995736766e-17` — the float value of `cos(radians(90.0))` —
   which grows with the leaf and crossed `_TOL` at **L = 1.63312e7 mm
   (16.33 km)**. Past that a *correct* 90° corner warned, and quoted
   `_max_mitre_leaf`'s `inf` at the author as "the longest leaf that still
   mitres is inf mm". The old test asserted "any leaf whatsoever" while
   pinning `L = 500`; it now runs six leaf lengths out to 1e9 mm across four
   angles, and a second test drives the whole screen at the three lengths that
   used to trip it. Sub-90° behaviour is unchanged and still continuous into
   the jump (reach 5.000209439 at 89.999°, 5.000000021 at 89.9999999°,
   5.000000000 at 90°).

The warning's **remedy is built from the faults that actually fired**, not
appended wholesale: a corner whose flanges already share an angle and radius
and fails only on leaf length is told to shorten the named leaf to its
`L_max` (or raise its `inner_radius`, since the limit grows with both), and is
not told to match angles it has already matched. Asserted per case.

The check (`_corner_seams`) is two-tier on the module's own precedent: the
screen is arithmetic and free (angles equal, radii equal, reach within the
extension) and runs on every `fold()`/`unfold()`; the boolean that turns it
into a number is paid **only where the screen has already fired** (~20 ms per
corner, against a 68–90 ms fold), and can still call the corner clean — the
screen states a criterion, the contact area is the evidence, and evidence wins.
The matched population sits at 1.0 to within 8e-15 and the worst mismatched one
at 0.9586, eight orders apart, so no threshold was tuned to make anything pass.

Cause 2 is **reported, not fixed**: the extension is straightforward on the
fold, but `_mitre_cuts(flat=True)`'s chord — slope `sin(a)/a` — is derived at
90° as well, and fold and unfold may not diverge (FR12). The required reach and
the measurement that closes it are recorded in the module docstring and
`AGENTS.md` so the fix is a derivation away rather than a rediscovery.

### The k-factor claim was false (MINOR)

`AGENTS.md` and the `sheetmetal` module docstring both said the fold/unfold
difference "does not accumulate" / "does not grow with the number of features".
Re-measured (t=2, k=0.44, R=3, leaf 20, 90°):

| bends | bend line | `fold() − unfold()` | closed form | residual |
|---|---|---|---|---|
| 1 | 60 mm | 22.619467105842887 | 22.61946710584651 | −3.6e-12 |
| 2 | 120 mm | 45.238934211700325 | 45.23893421169302 | +7.3e-12 |
| 3 | 160 mm | 60.318578948928916 | 60.31857894892403 | +4.9e-12 |

`angle_rad·(0.5 − k)·t²·span` is a statement about **one bend**; the model's
total is the sum, exactly linear in the bend line. Both texts now say so and
say to judge the sum against the process, not the 11 mm³ of one bend.

### Two tests required an OCCT defect to persist (MINOR)

`test_draft_never_returns_the_invalid_shape_occt_hands_back` asserted the raw
build123d draft returns `not raw.is_valid`, and
`test_error_doctor_diagnoses_the_empty_message_draft_failure` required a raise
whose message is empty. Both would fail the day build123d/OCCT is *fixed* —
rewarding the kernel for getting worse — even though `features.draft` would
have got safer. Both now interrogate the raw operation and assert the helper's
contract against whichever answer comes back (`out.is_valid` always; `achieved
== 5.0` if the kernel can now do it, `achieved < 5.0` if it still fails
silently; the Error Doctor rule matched against the live exception when one is
offered and against the documented traceback when it is not). The observation
each was written for is kept as the docstring, which is where its value was.

### The hole form's remaining scope hole (MINOR)

Round 1 reset the form on part change and defaulted the size per family; the
part id alone is not an identity across projects, though. `loadProject` *keeps*
the current selection when the incoming project has a part of the same name,
and every project made from the template has a `part1`, so the reset depended
on `selectedPart` incidentally transiting `null` in `openProject`'s `setState`.
The scope key is now `"<project>::<part>"` on PRD-009's precedent and
`syncHoleFormPart` listens to `projectName` as well.

## Files

- `agentcad/toolkit/patterns.py` — `_seed_reference`, `_centre`,
  `_mirror_centre`, `_identity_placement`, `_polar_layout_warnings`,
  `_linear_layout_warnings`, `_PLACE_TOL`/`_ANGLE_TOL`; `polar` skips by
  transform and warns (with followable advice) when the seed is off the circle;
  `linear`/`polar`/`mirror` report a rigid-image `center` per instance; module
  docstring.
- `agentcad/toolkit/sheetmetal.py` — `_profile_reach` (leaf term dropped from
  90° up), `_mitre_extension`,
  `_max_mitre_leaf`, `_seam_promise`, `_corner_seams`, `_measured_seam`,
  `_flange_index`; `fold()` keeps its mitred solids and checks the seams,
  `unfold()` runs the screen; module docstring gains the seam table, the leaf
  limit and the corrected k-factor claim.
- `frontend/js/main.js` — `holeFormScope` / `holeFormScopeOf()` replace
  `holeFormPart`; `syncHoleFormPart` is wired to `projectName` too.
- `tests/test_patterns.py` — `_centres` and `_asymmetric_seeded_plate` helpers;
  the recorded centres for `polar` and `linear`, the `radius=` placement
  regression (centres, not volume), the layout assertion against a sabotaged
  placement list, the followable-advice test, and **eight parametrized cases
  patterning a right-triangular seed** (counts 3/5/7/8 and three partial spans)
  that must stay silent — the coverage whose absence let the bbox metric
  through.
- `tests/test_sheetmetal_v2.py` — the two-hem 1200 mm³ conservation probe, ten
  quiet close corners (five of them **acute**, inside the leaf limit), seven
  that warn with the measured seam fraction, the closed-form leaf limit bisected
  against the reach predicate on six shapes, **24 angle × leaf-length
  combinations from 90° up (leaves to 1e9 mm)** plus the end-to-end screen at
  the three lengths the float leak used to trip, the "screen is free when it
  passes" test, and the k-factor accumulation table.
- `tests/test_features.py` — the two draft tests rewritten to hold under both
  kernel outcomes.
- `AGENTS.md` — corrected k-factor claim; three new PRD-010 gotchas (the
  swallowed-volume class, the close-corner seam and its two causes, and
  `polar`'s skip-by-transform rule).
- `docs/part-authoring.md` — `polar(radius=)`'s semantics and the per-instance
  `center`; the `close` corner's measured limits; `fold`/`unfold` now named as
  checking conservation too.

## Notes

**Two of the five findings were already closed by round 1 (`2e63c76`) and are
recorded here as re-measurements, not fixes.** The review read `9b7095a`.

- *Sheet-metal cross-feature collisions are invisible.* The review's probe,
  `SheetPart(1).base(60,40).hem("front", length=30).hem("back", length=30)`,
  reproduces on the current tree with its numbers intact — one valid solid,
  volume `5365.486677646162` against a declared `6565.486677646162`, 1200 mm³
  swallowed — but `_conserved` now warns, naming both numbers and the loss. The
  requested "declared-volume conservation check for every feature that adds
  material" already exists and already covers both entry points: `fold()` and
  `unfold()` are the only two shape-producing calls and each declares every
  flange by its closed form and credits every cut with what it *measurably*
  removed. It is pinned by a new test rather than re-implemented. What was
  genuinely open was the corner half, above.
- *Hole form state is global.* Family switching already reset the size to a
  family-appropriate default (`HOLE_DRILL_DEFAULT_MM` for `drilled`, the
  table's first row otherwise), and the form already reset on part change. No
  reproducible carry-over remained; the scope key removes the dependence on
  `openProject` happening to null the selection, which was incidental rather
  than guaranteed. Confirming the review's own note: there was no XSS or
  injection here, and none is claimed.

**What is not closed.** A `close` corner whose leaf overruns
`(R + t)·tan(45° − a/2)` is warned about, not fixed. The fold-side extension is
known exactly (`(R+t)·sin a + L·cos a`, measured to take the seam to
`1.000000000`), but applying it means re-deriving the blank's mitre chord —
currently `sin(a)/a`, a 90° derivation — and fold and unfold may not disagree.
Left as a warning carrying the numbers and the achievable leaf limit, on the
teardrop-refusal precedent: an approximation that cannot be shown correct is
refused or reported, never quietly shipped.

**A channel this does not close, and does not claim to.** The close-corner
warning lands in `sp.warnings`, the same list `_conserved` and `_checked`
already use, and nothing harvests that into the rebuild's warnings — it reaches
a user only if the part author reads it. Wiring it is **not** small, and the
reason is instructive: rebuild warnings come from `_resolve_params` (parameter
clamping) and nothing else, so a toolkit object has no channel at all. The
holes pipeline works because its records ride the *shape* and are picked up by
a dedicated harvest, which needs a `.cache/<key>.holes.json` sidecar (the
worker's `_SHAPE_CACHE` returns the cached shape **without calling `build(p)`**,
so a per-build list drains empty on the second build — changelog 0150), a
`carry()` call in every helper that returns a new object, and `affinity=part_id`
on the harvest call. Giving `SheetPart` the same treatment is a second metadata
pipeline across `kernel/` and `core/`, not an edit. Recorded as a follow-up.

**Suite.** `uv run pytest -q tests/test_sheetmetal_v2.py tests/test_sheetmetal.py
tests/test_patterns.py tests/test_features.py` — **202 passed**;
`tests/test_prd010_acceptance.py tests/test_toolkit_ocp_free.py` — **24
passed**. The examples/golden sweep (`test_examples`, `test_examples_golden`,
`test_toolkit_ocp_free`, `test_holes`, `test_drawing_holes`,
`test_prd010_acceptance`, 19 min) ran green apart from one `test_holes` row
belonging to the concurrent hole-standards work, not to this change. Whole-suite
count in the commit message.

**Browser.** Verified against a real Chrome (headless, CDP) on a scratch
projects dir: opening the face card on `alpha/part1`, typing `depth 7.5` and
`at (u, v) = 12, 34`, switching to project `beta` — whose part is *also* called
`part1` — and re-opening the face card gives back `depth ""` and `0, 0`, with
zero console errors or warnings throughout. The family switch was confirmed in
the same session: `clearance`/`M5` → `drilled` relabels the control to `⌀ mm`
and shows `6`, never the designation.
