# 0150 — 2026-08-13 — PRD-010 slice 4: `toolkit.holes` — ISO clearance and tapped holes, records on the shape

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 4)

## Summary

FR4's ISO half and FR7: `holes.clearance` and `holes.tapped`, each returning
`(part, records, warning|None)`, with the diameters taken from slice 2's
vendored tables and a **machine-readable record per call** riding on the shape
the helper returns. Nothing about the record mechanism was taken on faith: the
design's carrier was measured in-process only, and the plan asked for it to be
proven *through the worker*, across the shape cache, the LRU, tessellation and
the service's cached-rebuild path. It was — and the measurement also
contradicted one of the design's stated properties, which is written up below
and cost three files a two-line change.

## Spike S2 — the carrier, through the worker

All of the below is `KernelClient.request("build", …)` against the real worker,
with a probe script that reads the worker's own `_SHAPE_CACHE` (the stand-in
for slice 5's handler pack) and reports what it finds.

| # | question | result |
|---|---|---|
| (a) | after a real build, does the shape the worker cached carry the attribute? | **yes** — records intact, object id 4739556768 |
| (b) | build the same script again (a `_SHAPE_CACHE` hit) | `build(p)` **did not run** (no build event) and the probe found the records on **the very same object** |
| (c) | 17 other builds (cache max 16), then the first again | after the fillers: **0 carriers** in a 16-entry cache — evicted; the rebuild ran `build(p)` again, produced a **new** object id, and the records were identical |
| (d) | `handle_build`'s tessellation and `_write_lod_tiers` | a part heavy enough to cross the 150 000-triangle LOD threshold (`lods: ['lod1']`) came back with both records intact (`h0` ⌀9, `h1` ⌀5.5) |
| (f) | the **service's** `.metrics.json` fast path | rebuild with an empty cache dir: **1 kernel call** (`build`). Rebuild with the sidecar present: **0 kernel calls**. `get_part` with the sidecar present: **0 kernel calls** |

**Verdict: the attribute rung holds. No fallback was needed** — not the
`WeakKeyDictionary`, not the `hole_records(p, part)` script contract — and
design Decision 4 needs no amendment on this point. (b) and (c) together are
the exact regression AC7b was invented for, and they pass by construction
rather than by discipline.

**What (f) tells slice 5, and it is not a detail.** On the service's cached
path the kernel is **not called at all** — so there is no shape to read an
attribute off, at any price. The records must be persisted in a
`.cache/<key>.holes.json` sidecar and read from there, exactly as the design
says. A "harvest on rebuild" seam that assumed a kernel round-trip would return
nothing for every part that did not change, which is most of them.

### (e) what the attribute does *not* survive — the finding that changed the code

The same spike measured every operation a part might pass through after the
holes are drilled:

| operation | attribute survives? |
|---|---|
| `.clean()` | **yes** |
| `.moved()` | **yes** |
| `copy.copy` / `copy.deepcopy` | **yes** |
| `safe_fillet` | **no** |
| `safe_bool(..., "fuse")` | **no** |
| `safe_bool(..., "cut")` | **no** |
| a raw `part - tool` | **no** |
| **a re-entered `BuildPart()` + `add(part)` + `Hole`** | **no** |

The design states (Decision 4) that "records compose along the helper chain —
every `toolkit` helper that takes a part and returns a part (`holes.*`,
`patterns.*`, `features.*`, `safe_fillet`, `safe_shell`, `safe_bool`) carries
the attribute forward". Measured, **that was not true of any of the three
existing `safe_*` helpers**, and it is not true of the helpers' own
`BuildPart`+`add` route either: build123d returns a brand-new object with none
of the original's attributes.

Two consequences, both shipped:

1. Every helper here calls `carry()` explicitly (that was always the plan —
   `_carry` is in the design).
2. **`fillet.py`, `shell.py` and `boolean.py` gained a two-line
   `@carries_records` decorator.** Without it a script that drills holes and
   then fillets an edge loses every record and every drawing callout with it —
   and the plan's own slice-4 test list ("records survive `safe_fillet` applied
   after the holes") could not have passed. **This is a deviation from the
   plan's exhaustive file list**, taken because the design's stated property
   required it and the measurement showed the property was false; it is
   recorded here rather than buried. The decorator is inert for any part with
   no records.

## Changes

- **`agentcad/toolkit/holes.py` (new).**
  - `clearance(part, points, size, *, plane="top", fit="medium", std="iso",
    depth=None, thru=True, verify="bbox")` and `tapped(part, points, size, *,
    pitch=None, depth=None, thread_class="6H", plane="top", std="iso",
    thread="none", verify="bbox")`, both returning `(part, records,
    warning|None)`.
  - Diameters come from `hole_standards` — an M5 medium clearance hole removes
    exactly π·(5.5/2)²·t of material, asserted to `rel=1e-9`, and `tapped`
    bores the **tap drill** (4.2 for M5) while the record carries
    `M5×0.8 - 6H ↧12`.
  - `thread="real"` fuses `threads.tapped_hole_thread(...)` and bores at
    **`root_radius`**, not the tap drill — the CHEATSHEET's hard-won rule
    (boring at the physical drill buries the ridges: valid, fast, invisible).
    It requires a `depth`, and the warning states the ~9k triangles per hole.
  - **`plane` is a predicate, never an ordinal.** A `Plane`, or one of
    `top|bottom|front|back|left|right` resolved every rebuild to the extreme
    planar face along that axis (largest area among coplanar candidates, then
    lowest centre — a documented tie-break). The plane's origin is the part
    origin projected onto that face, so `points` stay part coordinates, and the
    module docstring carries the full `(u, v)` mapping table for all six names.
    A name that resolves to nothing raises `ValueError` naming the reason.
  - **The record** is one **group** per call — `id` (`h0`, `h1`, … stable
    within a build), `family`, `standard`, `size`, `fit`, `d`, `designation`,
    `count`, `positions` (plane-local), **`centers` (global, 9 dp)**, `axis`,
    `plane {origin, z_dir, x_dir}`, `depth_mm`, `thru`, `removed_mm3`, `tap`,
    `cbore`, `csk`, `pattern`, and the per-instance `instances` report. FR3
    ("a pattern of a wizard hole is one hole group") falls out for free.
    `centers` is an addition to the design's record shape, and a load-bearing
    one: slice 6 matches a record to a detected circle group **by centre
    proximity**, which plane-local positions cannot answer.
  - **The carrier, public:** `ATTR`, `records(part)`, `carry(new, prior,
    new_records=())`, `created()`, `carries_records` (the decorator), and
    `dropped_records_warning(part, created_before)` — the delta check slice 5's
    handler calls, with the design's warning text. A delta of **zero** means
    `build(p)` never ran (a cache hit) and the check stays silent, which is
    what makes it immune to warm-worker contamination.
  - **`safe_bool` is the fallback rung here too**: if build123d's `Hole` block
    raises, the helper cuts all the tools at once through `safe_bool` and warns
    that the result may not be byte-identical to the primary route. Tested with
    an injected failure.
  - **FR7's guards**, reusing slice 3's `engagement` rather than growing a
    second one: off-part instances (named by index), instances that touch but
    remove nothing (`exact` tier), a whole-call "nothing was removed" warning,
    holes closer than one diameter to each other **or to an existing record's
    centre** (named by index and by record id), and a depth deeper than the
    stock below the plane. The free tier builds no geometry at all — a hole's
    tool is a cylinder, so its envelope is arithmetic.
- **`agentcad/toolkit/fillet.py`, `shell.py`, `boolean.py`** — the
  `@carries_records` decorator (see (e) above).
- **`agentcad/toolkit/__init__.py`** — lazy re-export of `holes`.

## Files

- `agentcad/toolkit/holes.py` — **new**
- `agentcad/toolkit/fillet.py`, `shell.py`, `boolean.py` — carry the records
- `agentcad/toolkit/__init__.py` — lazy re-export
- `tests/test_holes.py` — **new**, 41 tests
- `docs/changelog/0150-toolkit-holes-iso.md` — this entry

## Notes

- **`plane="top"` costs nothing in bytes, measured.** A through hole drilled on
  the resolved top plane and the same hole drilled on `Plane.XY` produce the
  same volume, the same face count **and the same `.acm`** — asserted as a
  live test through the worker. That was not obvious (slice 1 measured two
  routes with identical metrics and different bytes), and it is what lets slice
  7 write `holes.clearance(part, pts, "M16", plane="top")` in the construction
  example without moving restated-AC1 (b). A *blind* hole measures its depth
  from the plane, so there the two planes are different geometry, not different
  bytes — the test says so.
- **The stock-depth check is a bounding-box measure** and the docstring says
  so: it catches a depth that cannot fit in the part at all, not one that
  misses a local pocket. An honest cheap check beats a dishonest exact-looking
  one.
- **`carry()` is bookkeeping, not a proof.** Carrying records across a cut that
  removed one of the holes leaves a record for a hole that is gone. Documented
  in the function, because the alternative — re-verifying every record against
  the geometry on every helper call — is slice 6/PRD-021 work priced at 2.1 ms
  per instance.
- Deliberately not here: counterbore and countersink and the ANSI tables
  (slice 8), the metadata pipeline that persists these records (slice 5), and
  the CHEATSHEET/`part-authoring` documentation (slice 14 by the plan's file
  list).

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_holes.py
41 passed in 8.92s
```

Full suite (`make test`, split into two chunks because one process exceeds this
sandbox's foreground time cap; `-n 2 --dist loadscope` is what the Makefile
runs):

```
$ .venv/bin/python -m pytest -q -n 4 --dist loadscope tests/ --ignore=tests/test_examples.py
2125 passed, 1 skipped in 313.16s (0:05:13)
$ .venv/bin/python -m pytest -q -n 2 tests/test_examples.py
20 passed in 919.94s (0:15:19)
```

**`make test`: 2145 passed, 1 skipped**, against slice 2's 2064 passed / 1
skipped — +81 for the two slices' 40 + 41 tests. No new skips, and none of the
2064 pre-existing tests moved despite `fillet.py` / `shell.py` / `boolean.py`
gaining the carry decorator.
