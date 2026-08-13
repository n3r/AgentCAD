# 0149 — 2026-08-13 — PRD-010 slice 3: `toolkit.patterns` and the measured per-instance guard

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 3)

## Summary

FR1 and FR2: `patterns.bolt_circle` / `patterns.grid` (pure arithmetic point
sets) and `patterns.linear` / `patterns.polar` / `patterns.mirror` (thin,
byte-faithful wrappers over build123d's own `Locations` / `PolarLocations` /
`mirror`). The slice's real content is the sentence FR1 makes in one line —
"overlap and degenerate spacing produce warnings, never silent geometry" —
because **OCCT does not give us that**. It was re-measured through the kernel
worker here, on `construction/gusset_plate`'s real bolt group and on a 50-hole
plate, and the numbers below are what the two-tier guard is priced against.

## Spike S3/S4 — the guard, through the worker

The design measured these in-process on a synthetic plate. Re-measured through
`KernelClient.request("build", …)`, i.e. inside the real worker, on the real
part at the params `examples/construction/project.json` ships (12 bolt
positions), and on a 200×200×10 plate with 50 holes. Each probe set gets one
**deliberately misplaced** extra instance at (9999, 9999).

### (a) a tool placed entirely off the part is a silent no-op

| part | cut time | volume before | volume after | delta | `is_valid` | raised |
|---|---:|---:|---:|---:|---|---|
| 50-hole plate | **0.89 ms** | 399999.9999999999 | 399999.9999999999 | **0.0** | `True` | none |
| `gusset_plate` blank | **1.01 ms** | 351107.3754449809 | 351107.3754449809 | **0.0** | `True` | none |

A tool passed *through the air above* the part is likewise a 0.0 delta. **The
design's Decision 6 stands unchanged**: a misplaced instance is a success that
changes nothing, so the helper has to measure engagement itself. Nothing in the
kernel — not `is_valid`, not an exception, not a warning — reports it.

### (b) what honesty costs, per instance

| probe | `gusset_plate` (13 instances) | 50-hole plate (51 instances) |
|---|---:|---:|
| bounding-box overlap | 0.188 ms total — **0.0145 ms each** | 0.714 ms total — **0.0140 ms each** |
| `(part & tool).volume` | 31.54 ms total — **2.43 ms each** | 107.59 ms total — **2.11 ms each** |
| the pattern's own boolean | — | **97.7–111.0 ms** (50 holes) |

The exact probe roughly **doubles** the cost of a 50-instance pattern (the
design predicted a tripling from its in-process 4.2 ms/instance; through the
worker the `&` probe is about twice as fast as it measured, so the ratio is
kinder — the design's conclusion is unchanged and its API split is what
shipped). Both probes found the planted miss. The exact probe's first
instance reported 138.544 mm³ on the plate (π·2.1²·10 = 138.544) and 2544.690
mm³ on the gusset (π·9²·10 = 2544.690) — it measures material, not adjacency.

### (c) `&` on disjoint shapes

Both parts, identical answer: **`Compound`, `.volume == 0`, `len(solids()) == 0`,
not `None`, no raise**, in 0.44 ms. That is why `engagement()` is written with
`&` and not `Shape.intersect()` (which returns a `ShapeList` — the standing
AGENTS.md gotcha), and it is asserted as a live test
(`test_and_on_disjoint_shapes_is_an_empty_compound_not_none`) so a future OCCT
cannot change it under the guard silently.

### (d) which pattern route is byte-faithful — an unplanned measurement that changed the API

The plan's identity test asks the helper to equal the hand-written form byte
for byte. There are two hand-written forms, because the seed of a shape pattern
is *already fused into the part*:

| route | volume | solids | `.acm` sha256[:16] |
|---|---:|---:|---|
| A `with PolarLocations(0, 6): add(seed)` — re-adds the seed at instance 0 | 149428.67210540315 | 1 | `85dd9044d54a9ae6` |
| B `with Locations(*locs[1:]): add(seed)` — skips instance 0 | 149428.67210540315 | 1 | **`930d1ee7547367a7`** |
| D the same skip, with hand-made rotation `Location`s | 149428.67210540315 | 1 | **`930d1ee7547367a7`** |
| E linear, instance 0 re-added | 147619.11473693544 | 1 | `72bfff06fb6423e5` |
| F linear, instance 0 skipped | 147619.11473693547 | 1 | `5ae463f8791d6c00` |

Three findings:

1. **A coincident re-fuse of the seed onto itself is safe** — one valid solid,
   the same volume to the last bit — so the natural hand-written form is not a
   bug. It is simply **not byte-free**: it tessellates differently.
2. **B == D.** Locations taken out of a `PolarLocations` context are bit-equal
   to hand-made `Location(Vector(0,0,0), Vector(0,0,1), angle)` rotations, so
   the helper can skip instance 0 and still be a wrapper over build123d's own
   arithmetic.
3. **`PolarLocations` instantiates outside a builder** (6 locations, no raise),
   which is what lets the helper read its locations, run the guard on them, and
   then place only the ones it means to.

**Decision, recorded because it is an API-visible one:** the helpers skip
instance 0. `count` is the total instance count the way every CAD package counts
it, `count=1` is a genuine no-op with a warning, and the byte-identity claim is
against the skip-0 hand-written form (route D) — asserted in
`test_polar_helper_is_byte_identical_to_the_handwritten_form`, through the real
worker at the service's mesh tolerance.

Also measured: `seed.moved(loc)` puts the seed exactly where
`with Locations(loc): add(seed)` does (centres equal to 1e-12, volume delta
0.0). That is what makes the guard's probe tools the real instances rather than
an approximation of them.

## Changes

- **`agentcad/toolkit/patterns.py` (new).**
  - `bolt_circle(r, n, start_deg=0)` and `grid(nx, ny, dx, dy, center=True)` —
    arithmetic, deterministic, rounded to 9 decimals so trig noise
    (`cos(90°) = 6.1e-17`) never reaches a stored coordinate. The docstring
    says plainly that these are **not** `PolarLocations`: these translate, that
    also rotates, and the two tessellate differently.
  - `linear(part, seed, direction, count, spacing, *, verify="bbox")`,
    `polar(part, seed, axis, count, radius=None, span_deg=360, *, verify=…)`,
    `mirror(part, plane, *, seed=None, verify=…)`, each returning
    `(part, warning|None)` — the `safe_*` contract.
  - **The guard, two-tier and public**: `engagement(part, tools, verify=…)`
    returns `[{"i", "status", "probe", "engaged_mm3"}]` with four honest
    statuses — `missed` (boxes disjoint: it *cannot* touch), `engaged`,
    `flush` (`exact` only: boxes overlap, engaged volume 0 — a valid face-to-face
    join for a fuse, and nothing at all for a cut), `unchecked` (`verify="off"`).
    `boxes_overlap`/`bbox_of`/`spacing_conflicts` are exported alongside so
    `holes` reuses the same code rather than growing a second guard.
  - Free tier always on: bbox screen, `len(result.solids())` (the definitive
    disjointness check for a fuse), the whole-operation volume delta, and the
    seed-extent-vs-spacing check. `verify="exact"` adds the `&` probe.
  - **Every warning names indices**, never a count: `instance(s) [2] do not
    reach the part`, `instance(s) 0&1, 1&2 overlap`.
  - `patterns.instances(part)` reads the per-instance report back off the
    returned shape (the same carrier mechanism as the hole records, for the
    same reason: a module-level registry drains empty on a worker cache hit).
  - **`safe_bool` is the fallback rung, and it says so.** The primary route is
    build123d's own builder because that is the byte-faithful one; when it
    raises, the helper retries the whole pattern as one `safe_bool` fuse with
    fuzzy escalation and warns that *the result may not be byte-identical to
    the primary route* — which is true, and measured (slice 1: a compound
    boolean gives the same volume and a different mesh). The rung is tested
    with an injected failure, because a real one is not reproducible on demand
    and an untested rung does not work.
- **`agentcad/toolkit/__init__.py`** — lazy re-export of `patterns`.

## Files

- `agentcad/toolkit/patterns.py` — **new**
- `agentcad/toolkit/__init__.py` — lazy re-export
- `tests/test_patterns.py` — **new**, 40 tests
- `docs/changelog/0149-toolkit-patterns.md` — this entry

## Notes

- **An impossible request raises; a doubtful one warns.** `count < 1`,
  `spacing <= 0`, a zero-length direction, `span_deg` outside `(0, 360]`, a
  negative radius, an unknown `verify` and an unknown plane name all raise
  `ValueError` naming the argument, so inside a part script they surface as a
  structured `script_error` with `details.line` (the `toolkit/specs.py`
  convention). Geometry that merely looks wrong — a missed instance, an
  overlap, a mirror that adds nothing — warns and still returns the part.
- **The bbox tier is a screen, not a verdict**, and the docstring says so: it
  can call a near miss "engaged", but it never calls a real hit "missed". The
  direction of the error is the load-bearing part.
- **`span_deg < 360` is inclusive** (3 instances over 180° sit at 0/90/180),
  which is what a CAD user means and *not* build123d's `PolarLocations`
  default; the helper passes `endpoint` explicitly rather than inheriting it.
- Deliberately not here: patterns along a path (named in the design as out of
  scope so it cannot arrive as scope creep) and feature-callable seeds (design
  Phase 2). Docs for the new vocabulary — CHEATSHEET and
  `docs/part-authoring.md` — belong to slice 14 by the plan's own file list.

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_patterns.py
40 passed in 9.61s
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
skipped — +81, which is exactly the 40 tests in `tests/test_patterns.py` plus
the 41 in `tests/test_holes.py` (slice 4, landing alongside). No new skips.

