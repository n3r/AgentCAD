# 0151 — 2026-08-13 — PRD-010 slice 5: the hole-metadata pipeline (FR6, AC7 + AC7b)

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 5)

## Summary

Slice 4 put a machine-readable record on the built shape. This slice carries it
out: a `hole_records` worker handler, a `.cache/<key>.holes.json` sidecar, and a
`holes` key on rebuild results and `get_part`. The feature's failure mode is
**silence** — a lost record is a missing drawing callout and nothing else — so
the work here is mostly about keeping three different answers apart, and the
spike found that the design's own drop-detection could not fire at all in the
seam's normal order. That is written up below rather than quietly reordered.

## Spike S5 — what the extra kernel round-trip costs

All numbers through the real worker (`KernelClient`/`KernelPool`), on the two
parts the plan names plus `construction/gusset_plate`.

**(a) The harvest is a shape-cache hit, and it is free.**

| part | `build` (cold) | `hole_records` after it | again | `build` (warm) | overhead |
|---|---:|---:|---:|---:|---:|
| `prototyping/enclosure_base` | 191 ms | **1 ms** | 1 ms | 38 ms | 0.5% |
| `engine/intake_manifold` | 11 502 ms | **1 ms** | 1 ms | 281 ms | 0.0% |

The design's prediction holds and the lazy `holes: {"deferred": true}` fallback
is not needed.

**(b) …but only if it is routed by affinity. This one is load-bearing.**
`KernelPool._pick` routes a keyed request by `hash(affinity) % size` and
**round-robins an unkeyed one**, while `service._rebuild` builds with
`affinity=part_id`. On a 2-worker pool:

| part | `build` (affinity) | harvest (affinity) | harvest (**unkeyed**) |
|---|---:|---:|---:|
| `prototyping/enclosure_base` | 2 726 ms | **1 ms** | 151 ms / 1 ms |
| `engine/intake_manifold` | 11 472 ms | **1 ms** | 1 ms / **11 354 ms** |

(The 2 726 ms build includes the pool's lazy spawn of its first worker; what
matters is the harvest column.) An unkeyed harvest that lands on the other
worker pays a full cold build — 11 354 ms of it. The seam passes
`affinity=part_id`, and a test asserts it.

**(c) The ordering finding: with the harvest AFTER the build, the drop check
never fires.** The delta that separates "declares none" from "a raw operation
dropped the records" is only meaningful for the call that actually ran
`build(p)`. Measured on three parts, a harvest issued after `build` reports
`measured: false` **every time** — the shape cache absorbs it, its delta is 0,
and the design's drop warning would have been dead code outside its own unit
test. Harvesting *first* makes the harvest the measuring call and leaves
`handle_build` with the cache hit:

| part | build then harvest | harvest then build | delta | measured? |
|---|---:|---:|---:|---|
| `prototyping/enclosure_base` | 191 ms | 197 ms | +3.0% | after=False before=**True** |
| `engine/intake_manifold` | 12 018 ms | 11 999 ms | −0.2% | after=False before=**True** |
| `construction/gusset_plate` | 70 ms | 83 ms | +17.5% | after=False before=**True** |

+17.5% on `gusset_plate` is 13 ms of fixed round-trip on a part small enough
for it to show; on the repo's worst case it is noise. **The seam therefore
harvests before the build** — a deviation from the design's "on a successful
rebuild it calls `hole_records`", taken because the measurement says the
stated order cannot deliver the stated warning.

## The three answers, and the fourth

`holes.records()` returns `[]` both for "no holes" and for "a raw build123d
operation dropped them", so the list alone cannot tell them apart. What ships:

| `holes` | means |
|---|---|
| `[...]` | the records |
| `null` | the part declares none |
| `[]` **+ a warning** | records were created and did not reach the returned part |
| **absent** | not harvested — the build failed, or the harvest could not measure |

`null` comes from the **delta**, never from an empty list. Absent is the
honest answer in two cases: a failed build (there is no shape to describe), and
a harvest that took a shape-cache hit and found nothing — where "no records"
is not evidence of anything. That second case is **not persisted**, so a later
harvest that does run the build can still answer for real.

## What the delta cannot see, stated

On a shape-cache hit `build(p)` did not run, so the number of records this
build *created* is unknowable — the dropped records went with the object that
held them, and no weak registry recovers that without reintroducing exactly the
cross-build contamination AC7 forbids. Two things keep it from mattering:

1. **The seam harvests first**, so in the ordinary rebuild it is the measuring
   call (see (c) above).
2. **A script that never mentions `agentcad` cannot hold a record**, so it
   answers `null` definitively with **no kernel call at all**. Without this the
   ordinary hole-less part — most parts — would report "not harvested"
   whenever any other surface had already built it.

## A trap found on the way: the worker runs as `__main__`

The handler needs to know whether its `build_shape_ns` call took a cache hit.
The obvious `from ..worker import _SHAPE_CACHE` **silently returns a second,
always-empty copy**: the worker is spawned as `python -m agentcad.kernel.worker`,
so the running module is `__main__`, and importing `agentcad.kernel.worker`
executes the module *again* with its own module-level state. Measured — every
call reported itself as a fresh build. Importing a worker *function* is fine
(several packs do it); importing worker **state** is not. The handler reaches
the live cache through `build_shape_ns.__globals__` instead, which is by
construction the module dict of the copy that is actually running.

## Changes

- **`agentcad/kernel/handlers/holes.py` (new).** One handler, `hole_records
  {script, params}` → `{holes, warnings, dropped, measured}`. It brackets
  `build_shape_ns` with `holes.created()`, reads `holes.records()` off the
  returned shape, and runs the toolkit's own `dropped_records_warning`.
  `dropped` and `measured` are additions to the design's `{holes, warnings}`
  shape: the seam has to choose `null` vs `[]` on the far side of a JSON pipe,
  and parsing that out of the warning *prose* would be worse.
  - **Record shape is validated here** (`contract_error`, naming the offending
    keys): a record is a plain dict, anything can `setattr` the carrier
    attribute, and residue must fail loudly rather than become a `KeyError` in
    the server or a missing callout in a drawing. Required: `id`, `family`,
    `standard`, `designation`, `d`, `count`, `positions`, `centers`, each
    type-checked (a `count` of `True` is residue), plus a `json.dumps` probe so
    a record holding a build123d object cannot reach the pipe.
- **`agentcad/core/tools_holes.py`** gains `install_rebuild_holes(service)`,
  `_WRAPPED`-marked exactly like `install_rebuild_specs`, wrapping
  `service._rebuild` (harvest + sidecar + the `holes` key) and
  `service.get_part` (sidecar only — a read never triggers a kernel call it did
  not already need).
  - Sidecar `.cache/<key>.holes.json`, `{"version": 1, "cache_key", "holes",
    "warnings", "dropped"}`, atomic write via `ProjectStore._atomic_write`, and
    a versioned reader that `unlink()`s anything corrupt, stale or shaped
    wrong (`core/specs.py:434-485`). An `OSError` on write is swallowed: an
    unwritable cache is a slow read, not a bug.
  - **Nothing raises out of either wrapper.** A harvest that fails leaves the
    key absent *and* appends a warning naming the failure — geometry that
    landed is not reported as broken by a metadata problem, and a metadata
    problem is not silent either.
  - A part whose last rebuild **errored** is harvested afterwards instead of
    before, so a script that fails — or hangs — is not built twice per
    rebuild. It costs one cache hit and degrades only the drop check, only on
    the first rebuild after the script is fixed.
  - A **reference** part answers `null` with no kernel call: a mesh has no
    script, so it cannot declare records, and that is a fact rather than an
    absence.
- **`docs/agent-api.md`** — the `holes` key on rebuild results and `get_part`,
  its four states, and a new "Hole metadata" section including the two limits
  (a record describes the call, not the current geometry; a record is not
  automatically a callout).

## Files

- `agentcad/kernel/handlers/holes.py` — **new**
- `agentcad/core/tools_holes.py` — the seam
- `tests/test_hole_metadata.py` — **new**, 23 tests
- `docs/agent-api.md`
- `docs/changelog/0151-hole-metadata-pipeline.md` — this entry

## Notes

- **AC7 is now a property, not a discipline.** Two different parts on one warm
  worker cannot mix because there is no shared mutable state to mix. **AC7b**
  — the same part twice on one warm worker — is the criterion the original
  registry design could not observe, and it passes on the `_SHAPE_CACHE`-hit
  path by construction.
- `get_part` on a built part whose sidecar is missing reports the key
  **absent**, not `null`: it will not run a build to answer a read. Any
  rebuild fixes it.
- Deliberately not here: the drawing callouts that consume these records
  (slice 6, changelog 0152) and the UI.

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_hole_metadata.py
23 passed in 5.46s
```

Full suite: see changelog 0152, which lands in the same series and states the
measured total.
