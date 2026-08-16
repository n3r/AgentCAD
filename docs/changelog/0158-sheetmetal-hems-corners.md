# 0158 — 2026-08-13 — PRD-010 slice 12: sheet-metal v2b — hems, corner treatments, and a refused teardrop

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 12)

## Summary

The rest of FR11. `SheetPart.hem(edge, kind="open"|"closed", …)` is a 180° bend
folding the leaf back over the sheet; `SheetPart.corner(edge_a, edge_b,
treatment="close"|"gap"|"rip")` treats the corner where two flanged edges meet.
Both ride slice 11's structure, so they appear in `unfold()` and in the derived
outline by construction.

> **Correction (review, 2026-08-16).** Not the corner they don't. Riding the
> structure carried the corner's *span extension* into `unfold()` and nothing
> else: the mitre itself was cut from `fold()` only, so the blank had a square
> corner where the model has a joint and **could not be bent into the model**.
> Measured on the corner bracket: `fold − unfold` 48.151660 mm³, and the two
> tabs declared 10423.927534 mm³ against a blank of 10393.818734 — 30.108800
> mm³ of sheet claimed twice and swallowed by the fuse in silence. The mitre is
> now cut from the blank too (0161), and because a mitre crosses the bend zone
> while the sheet is still rolled up, the blank's mitre is the **chord of the
> unrolled bisector** (slope `sin(a)/a`), not a 45° line. Blank 10376.632742,
> `fold − unfold` 65.337653.

**`kind="teardrop"` raises.** The design gated it on spike S8 and said it would
be refused with a reason rather than approximated if the spike said so. The
spike says so, and the measurement is specific enough to put in the error
message.

## Spike S8 — is a hem representable?

`scratchpad/spike_hem.py` and `spike_hem2.py`, through the kernel worker.
(The first pass of `spike_hem.py` had a real bug worth recording: it built the
cross-section with `BuildSketch(Plane.YZ)` and then `add()`-ed the resulting
*world*-placed sketch into a second `BuildSketch(Plane.YZ)`, which transforms it
twice. The flange landed somewhere else entirely — and the symptom was
`solids == 2` with **`is_valid True` and exactly the expected volume**. Another
instance of the standing fact: OCCT reporting success is not evidence.)

### (A) The 180° radius sweep — OCCT is not the floor

At 180° the returned leaf sits `2R` above the sheet, so the hem's air gap *is*
`2R` and a "closed" hem is a small-`R` one. Swept `inner_radius` from `t` down
to `0.01·t` at t = 1.5, 3.0 and 0.8, checking `is_valid`, the solid count **and**
the volume against `w·d·t + π·t·(R + t/2)·span + L·t·span`:

| R/t | 1.0 | 0.5 | 0.25 | 0.1 | 0.05 | 0.025 | 0.01 |
|---|---|---|---|---|---|---|---|
| t = 1.5 | ok | ok | ok | ok | ok | ok | ok |
| t = 3.0 | ok | ok | ok | ok | ok | ok | ok |
| t = 0.8 | ok | ok | ok | ok | ok | ok | ok |

`ok` = one valid solid **and** volume error 0.0. Every intermediate ratio
(0.75, 0.35, 0.15, 0.075, 0.035, 0.015) is `ok` too, and so is the same sweep
with `clean()` skipped. So the second pass pushed to the actual floor:

| R/t (t = 1.5) | 1e-2 | 3e-3 | 1e-3 | 3e-4 | 1e-4 | 3e-5 | 1e-5 | 1e-6 | **1e-7** | **0** |
|---|---|---|---|---|---|---|---|---|---|---|
| valid / solids | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| faces | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | **9** | **8** |
| relative volume error | 0 | 0 | 0 | 0 | 0 | 2e-16 | 0 | 0 | **1.8e-8** | 4e-16 |

**The boolean never fails.** What happens instead is quieter and worse: at
`R/t = 1e-7` (R = 1.5e-7 mm, OCCT's own modelling tolerance) OCCT drops a face
and the volume drifts; **at `R = 0` the fold is still one valid solid of
exactly the right volume, but with 8 faces instead of 10** — the seam between
the folded leaf and the sheet is gone, and nothing in the model distinguishes a
closed hem from 2t of solid stock.

So the shipped closed-hem radius is **a shop number, not an OCCT limit**, and
the docstring says exactly that. `OPEN_HEM_RADIUS_FACTOR = 1.0` (gap 2t),
`CLOSED_HEM_RADIUS_FACTOR = 0.5` (gap t), both overridable per hem with
`inner_radius=`. `inner_radius=0` is **refused** with the face-count reason
rather than accepted as "a true closed hem", because the shape it returns is
not one.

The hem also unfolds under the same law as every other bend — no new terms:
at R = 0.25·t, fold 4458.086256, unfold 4432.639356, difference **25.4469**,
predicted by `π·(0.5 − k)·t²·span` **25.4469**, residual −0.0.

### (B) The teardrop — it builds, and that is the problem

A teardrop wraps past 180°. In this model the leaf leaves the bend
tangentially, so past 180° it descends toward the sheet and enters it after
`L_touch = R·(1 − cos a)/−sin a`. Measured at t = R = 1.5 with a hem-sized
4t leaf:

| wrap | L_touch (predicted) | profile face builds? | face area vs analytic | leaf ∩ sheet | fuse `is_valid` | fuse solids | material silently lost |
|---:|---:|---|---|---:|---|---:|---:|
| 185° | 34.36 | yes | exact | 0 | True | 1 | 0.0 |
| 200° | 8.51 | yes | exact | 0 | True | 1 | 0.0 |
| **225°** | **3.62** (= 2.41·R) | yes | exact | **144.59 mm³** | **True** | **1** | **144.59 mm³** |
| 250° | 2.14 (= 1.43·R) | yes | exact | 143.66 mm³ | True | 1 | 143.66 mm³ |
| 270° | 1.50 (= 1.00·R) | yes | exact | 135.00 mm³ | True | 1 | 135.00 mm³ |

and the leaf-length sweep at 225° puts the penetration threshold exactly where
the formula does:

| leaf L | 0.5 | 1.0 | 1.5 | 2.0 | 2.4 | 2.5 | 3.0 | **4.0** | **6.0** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| leaf ∩ sheet (mm³) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **4.302** | **144.594** |

**The verdict.** The profile face itself is fine — `make_face()` never raised
and the face area matched `angle·t·(R + t/2) + L·t` exactly at every wrap, so
the leaf does not self-intersect the sector. The self-intersection is with the
**base plate**, and it is silent: at 225° with a 4t leaf the fuse returns one
valid solid and 144.59 mm³ of declared material is simply gone. The longest
non-penetrating leaf is **2.41·R at 225°, 1.43·R at 250°, 1.00·R at 270°** —
and a teardrop is a *closed* hem, so R is small, which makes the usable leaf
smaller still (0.24·t at R = 0.1·t and 225°), while a hem leaf needs ≥ 4t.

`kind="teardrop"` therefore raises a `ValueError` carrying those numbers. It is
not approximated as a closed hem.

## What shipped

- `hem(edge, kind="open", length=6.0, start=0.0, width=None,
  inner_radius=None, relief="auto")` — a 180° flange. `flange()` still refuses
  180° (v1's rule, and its test, unchanged) and now says *why*: a 180° bend is
  a hem, use `hem()`. Hems take `start`/`width` and reliefs like any flange.
- `OPEN_HEM_RADIUS_FACTOR`, `CLOSED_HEM_RADIUS_FACTOR`, `HEM_KINDS` as named
  constants with the measurement written beside them.
- `corner(edge_a, edge_b, treatment="close")`:
  - **`close`** mitres: each flange's span is extended past the corner by its
    own `inner_radius + t` and cut by the half-space beyond the 45° bisector
    through the corner (normal `n_b − n_a`), so the two leaves meet on it. In
    the flat pattern the extension is `inner_radius + k·t` — a blank has no
    through-thickness, so the neutral fibre is the one honest value, and the
    two extended tabs simply fuse where they overlap.

    > **Correction (review, 2026-08-16).** "Simply fuse where they overlap" is
    > the bug, stated as if it were the design: one piece of sheet cannot fold
    > two ways, so an overlap there is material claimed twice (30.1088 mm³
    > measured) and a blank that will not fold. Both tabs are now mitred
    > (0161) — see `SheetPart._mitre_cuts`, and `_conserved`, which is the
    > check that would have caught this at the time.
  - **`gap`** shortens both flanges by `CORNER_GAP_FACTOR × t` at that corner;
    **`rip`** adds and removes nothing (v1's untreated corner, asserted equal
    to the no-corner fold at `rel=1e-12`).
  - Validation: non-adjacent edges, an unknown treatment, a duplicate corner
    and a flange that does not reach the corner each raise a `ValueError`
    naming the problem.
- Measured on a 60 × 40 × 2 plate with two 90° R3 flanges: `close` 10441.9704 >
  `rip` 10056.6371 > `gap` 9846.3716 mm³. The mitre is verified by an `&` probe
  in the notch itself — `(close & probe).volume == 1.0`, and the same probe on
  `rip` is empty — not by a bounding box, which does not move (the front
  flange's mitre stops exactly at the right flange's outer skin).

## Files

- `agentcad/toolkit/sheetmetal.py` — `hem`, `corner`, `_corner_key`,
  `_flange_at_corner`, `_corners_of`, `_mitre_cuts`, `_Corner`, and the hem /
  corner constants
- `tests/test_sheetmetal_v2.py` — the hem, corner and refusal tests
- `agentcad/core/templates.py` — the CHEATSHEET's sheet-metal block. Slice 14
  owns the wider cheat-sheet work, but this block asserted "span the full edge,
  one per edge", which slices 11 and 12 made **false**; a correction now beats a
  wrong instruction the agent reads on every `part_template` call. Partial
  flanges, relief, hems, corners and the teardrop refusal are in.
- `docs/part-authoring.md` — hems and corners, including the refusal
- `docs/changelog/0158-sheetmetal-hems-corners.md`

## Verification

- `.venv/bin/python -m pytest -q tests/test_sheetmetal.py
  tests/test_sheetmetal_v2.py` — **44 passed in 7.38s** (12 in the v1 corpus,
  unchanged; 32 new).
- `make test` (the full suite, run in the two chunks this machine uses so the
  examples build does not time out), against the slice-10 baseline of
  **2251 passed, 1 skipped**:
  - chunk A — `.venv/bin/python -m pytest -q -n 4 --dist loadscope tests/
    --ignore=tests/test_examples.py` → **2263 passed, 1 skipped in 332.99s**
  - chunk B — `.venv/bin/python -m pytest -q -n 2 tests/test_examples.py` →
    **20 passed in 923.21s**
  - total **2283 passed, 1 skipped** = the 2251 baseline + the 32 new tests in
    `tests/test_sheetmetal_v2.py`. No new skips, no example's geometry moved.
- `make test-fast` — **1952 passed, 1 skipped in 149.99s** (`1921 + 32 = 1953`
  collected; one of them, `test_ac9_the_full_suite_count_is_cited`, failed on
  that run because 0157 was the newest changelog entry at the moment the run
  reached it and its numbers live here in 0158 — it passes on the final tree:
  `.venv/bin/python -m pytest -q
  tests/test_prd008_acceptance.py::test_ac9_the_full_suite_count_is_cited` →
  **1 passed in 0.20s**).
- The spikes are `scratchpad/spike_sheet_v2.py`, `spike_hem.py` and
  `spike_hem2.py`, all through `KernelClient`.

## Notes

- **A mitre between flanges of different radii is a step, not a joint.** The
  bisector is the same plane for both, but each leaf sits at its own distance
  from the corner, so unequal `inner_radius` leaves a ledge. Equal radii — the
  normal case — give a clean mitre.
- The corner's flat-pattern extension (`R + k·t`) and its folded extension
  (`R + t`) differ by `(1 − k)·t`. That is the same k-factor gap slice 11
  measured, appearing in a second place; it is not a new approximation.
- `corner()` must follow the two `flange()` calls — it validates that both
  reach the corner, which it can only do once they exist. The error says so.
- Not done here (slice 14 owns them): the CHEATSHEET section and the
  `AGENTS.md` gotcha lines. The two facts that belong there are "a 180° hem's
  air gap is `2R`, and `R = 0` builds but stops being a hem (8 faces, not 10)"
  and "a >180° leaf penetrates the sheet and the fuse swallows it silently".
