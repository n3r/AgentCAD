# Feature toolkit II — design

- **PRD:** [PRD-010](../../prd/in-progress/PRD-010-feature-toolkit-ii.md)
- **Date:** 2026-08-13
- **Phase:** v5 — daily-driver depth (the second v5 feature, after PRD-009)
- **Status:** design complete. Every load-bearing claim below about OCCT
  behaviour was measured on this machine (build123d 0.11.1, Python 3.12)
  before the decision it supports was taken; the measurement appendix names
  the script and prints the number. Four of the PRD's own statements did not
  survive measurement — they are collected in
  [What the PRD asks for that cannot be delivered as written](#what-the-prd-asks-for-that-cannot-be-delivered-as-written).

---

## What is different about this feature

PRD-009 was a numerical problem; PRD-010 is a **topological** one. Its
capabilities do not compute numbers, they *reference geometry* — a face to put
a hole on, an edge to draft, a seed to pattern — and this repo has now measured
twice, in PRD-008 and again in PRD-009, that the thing which references
geometry is the thing that breaks:

> **For a face: the basis is stable, the ordinal is not.** `Plane(face).x_dir`
> is bit-identical across rebuilds, across a fresh worker and across parameter
> changes; the face *index* is a mesh-order ordinal and a topology-changing
> edit renumbers it (`corner_r: 6.0` turned the enclosure's face 37 from a
> 5989 mm² plate into a 51 mm² sliver).
> — AGENTS.md, Sketcher gotchas (changelog 0139)

Every capability in this PRD inherits that. So this spec does not open with an
API. It opens with **what the shipped code actually does**, then takes each
decision against a measurement, and states plainly where a reference cannot be
made stable rather than discovering it in review.

The second inheritance is the boolean one. `safe_fillet`/`safe_shell`/
`safe_bool` exist because fillet, shell and boolean fail readily. Patterns
multiply that by N, and this PRD's most dangerous sentence — FR1's "overlap and
degenerate spacing produce warnings, never silent geometry" — turns out to be
harder than it reads, because **OCCT does not fail on a badly-placed feature.
It silently succeeds and changes nothing** (measured: §M4).

---

## Ground truth — what ships today

### The toolkit

| module | lines | imports build123d? | process |
|---|---:|---|---|
| `boolean.py` (`safe_bool`) | 90 | yes | kernel |
| `fillet.py` (`safe_fillet`) | 79 | yes | kernel |
| `shell.py` (`safe_shell`) | 84 | yes | kernel |
| `facemod.py` (face order, `push_face`) | 91 | yes | kernel |
| `sheetmetal.py` (`SheetPart`) | 242 | yes | kernel |
| `surfacing.py` | 208 | yes | kernel |
| `threads.py` (bd_warehouse) | 80 | yes | kernel |
| `sketch.py` (solver) | 3846 | **no** | **server** |
| `specs.py` (declarations) | 359 | **no** | **server** |

Exactly two modules are OCP-free, and that property is asserted in a fresh
interpreter with `OCP` blocked at `sys.meta_path`. This PRD adds a **third**
(Decision 5) and must assert it the same way.

The `safe_*` contract is uniform and is the thing this PRD extends: *return the
best geometry you can produce, plus a warning naming what you actually did;
raise only when nothing works.* `safe_fillet` binary-searches the radius down;
`safe_bool` escalates the fuzzy tolerance; `safe_shell` walks a fallback
ladder and admits in its warning that the last rung is ~20% thin on curved
walls. None of them swallow anything.

### `SheetPart` v1

A rectangular base plate plus **full-edge** flanges, one per edge, bending +Z,
angle in `(0, 180)` exclusive. `fold()` fuses a per-flange bend sector + leaf
built as a 2D cross-section extruded over the whole edge width;
`unfold()` fuses a `BA + length` tab per edge; `flat_outline()` is a **separate
hand-written walk** of the four base edges bulging outward; `bend_lines()`
places a midline `BA/2` beyond each edge. `sp.warnings` collects fusion
fallbacks.

Two facts matter for v2. First, `fold()` and `unfold()` are already
one-spec-consistent *by shared arithmetic*, but `flat_outline()` is a
**parallel implementation** of the blank's shape — it agrees with `unfold()`
today only because both are trivial. Second, **no bundled example uses
`SheetPart`**; its only consumers are `tests/test_sheetmetal.py` and a fixture
in `tests/test_checks_pipeline.py`. AC4's bracket has to be built.

### Holes, patterns and drawings today

The bundled examples pattern by hand, in three different idioms
(`examples/construction/parts/gusset_plate.py:96-130` builds a flat `holes`
list in two Python loops then drills them in one `Locations` block;
`examples/rocketry/parts/flange.py:55` uses `PolarLocations`;
`examples/engine/*_bolt_set.py` uses `Pos(...)` list comprehensions).
`mirror` appears **nowhere** in `examples/` — mirroring is arithmetic
(`for sgn in (+1, -1)`). `CounterSinkHole` appears once
(`enclosure_lid.py:87`); `CounterBoreHole` never — counterbores are hand-cut
cylinders.

`generate_drawing` derives hole callouts geometrically:
`_detect_circles` (`kernel/handlers/drawing.py:180`) collects closed CIRCLE
edges **from the top view only**, groups them by radius rounded to 2 dp, and
emits a `hole_groups` entry per radius with `count >= 3`. So today a hole is a
diameter and a count, it exists only if it faces up, and a ⌀4.2 tapped hole and
a ⌀4.2 drilled hole are the same thing.

### The build pipeline — and its two caches

This is the single most consequential piece of ground truth in the document,
because the PRD's metadata design does not survive it.

**Cache 1 — the worker's shape cache.** `kernel/worker.py:290` `build_shape_ns`
executes the script module (`_exec_script`, always), then computes
`_shape_key = sha256(script + params)` and, **on a hit, returns the cached
shape without calling `build(p)` at all**:

```python
key = _shape_key(script, values)
if key in _SHAPE_CACHE:            # OrderedDict, max 16 entries
    _SHAPE_CACHE.move_to_end(key)
    return _SHAPE_CACHE[key], values, warnings, ns
```

Module-level side effects re-run on every call; **`build(p)` body side effects
do not.**

**Cache 2 — the service's metrics sidecar.** `core/service.py:635` returns
early when `.cache/<key>.acm` and `.cache/<key>.metrics.json` both exist,
publishing `rebuild_finished` with `cached: true` — **the kernel is not called
at all**. `<key>` is `sha256({content, params, density, tolerance, format})`
truncated to 32 hex chars, where `content` is the **script text**.

Rebuild result keys today: `ok, metrics, warnings, lods, cache_key`.
`get_part` returns `{id, label, material, params, kind, source,
solid_materials, script, params_spec, status{state,error,warnings}, metrics}`.

### The seams a pack may use

- **Worker handler pack** — `kernel/handlers/<name>.py`, `register(toolbox)`.
- **Tool pack** — `core/tools_<name>.py`, `register(registry, service)`, loaded
  **alphabetically** (21 packs today; `tools_proposals` at `p` assigns
  `service.gate_providers = []` unconditionally, and `tools_versioning` at `v`
  replaces `write_guard` — both are documented traps).
- **Rebuild seam** — `tools_specs.install_rebuild_specs` wraps
  `service._rebuild` and `service.get_part`, idempotent by a `_WRAPPED`
  attribute set on the wrapper itself. It wraps `_rebuild`, not the three
  rebuild-returning tools, because the browser's `PATCH .../params` route calls
  `service.set_params` directly.
- **Derived-data sidecar** — `store.cache_dir(proj) / f"{key}.<name>.json"`,
  atomic write, versioned reader that `unlink()`s garbage
  (`core/specs.py:434-485`). Canonical, shared by every branch,
  content-addressed.
- **Script-editing tool** — `core/tools_facemod.py`: validate, append a
  **marked, counter-suffixed block** that rebinds `build`, call
  `service.update_part`, return `{**with_hint(result), …echoes}`. Reached
  through the generic `POST /api/tools/{name}`; no route pack needed. The UI
  side already exists: `frontend/js/main.js:1420-1653` keeps a face selection
  keyed to the mesh cache key, renders a face card, and applies push/pull.

---

## Decision 1 — the helpers are byte-faithful wrappers over build123d's own operations

The obvious implementation of `holes.clearance(part, points, …)` is "build N
cylinders, boolean them out". Measured, that is the wrong implementation, and
AC1 is what proves it.

Four routes to the same 8-hole plate, same tolerance, comparing the tessellated
`.acm` payload (§M1, §M2):

| route | volume | faces | mesh sha256 |
|---|---:|---:|---|
| `BuildPart` + `Locations` + `Hole` (what the examples write today) | 142891.646112 | 14 | `9fbc6288a1bdf13a` |
| same, points in **reverse order** | 142891.646112 | 14 | `9fbc6288a1bdf13a` |
| `plate - Compound(children=[cylinders])` | 142891.646112 | 14 | **`c2b5400dfeda65ae`** |
| **`BuildPart` + `add(existing_part)` + `Locations` + `Hole`** | 142891.646112 | 14 | **`9fbc6288a1bdf13a`** |

Three readings:

1. **Geometrically identical is not byte-identical.** The compound-cut route
   produces the same volume to 6 dp, the same face count, and a *different*
   mesh. Face order comes from the `TopExp_Explorer` walk, which is a function
   of construction history — so "identical geometry" claims in this PRD have to
   say which identity they mean.
2. **Instance order inside one `Locations` block does not matter.** Reversing
   the point list is byte-identical. Patterns may therefore emit points in
   whatever order is convenient.
3. **A helper can be byte-faithful.** Re-entering a `BuildPart`, `add()`-ing
   the caller's part and running the *same* builder objects reproduces the
   hand-written script's mesh exactly — and at N=8 it costs 38 ms, cheaper than
   the compound route.

**Decision: `holes.*` and `patterns.*` are thin wrappers that open a
`BuildPart`, `add()` the incoming part and drive build123d's own `Locations` /
`PolarLocations` / `GridLocations` / `Hole` / `CounterBoreHole` /
`CounterSinkHole` / `mirror`. They do not hand-roll booleans on the happy
path.** `safe_bool` is the *fallback*: when the builder call raises, the helper
retries through `safe_bool` with fuzzy escalation and warns that it did — and
the warning says the geometry may no longer be byte-identical to the primary
route, because it isn't.

Rationale beyond AC1: build123d's hole operators already handle depth,
direction and through-cuts; reimplementing them would be a second
implementation of a thing this repo pins as a compatibility surface.

---

## Decision 2 — where each capability lives

| capability | lands in | why |
|---|---|---|
| `patterns.linear/polar/mirror/bolt_circle` | `agentcad/toolkit/patterns.py` (kernel-side) | part-script vocabulary; needs build123d |
| `holes.clearance/tapped/counterbore/countersink` | `agentcad/toolkit/holes.py` (kernel-side) | same |
| `features.rib/boss/draft` | `agentcad/toolkit/features.py` (kernel-side) | same |
| ISO/ANSI tables + designation grammar | `agentcad/toolkit/hole_standards.py` (**OCP-free**) | the server's `hole_standards` tool reads it; must never pull OCP into the server |
| the standards data itself | `agentcad/toolkit/data/*.json` | versioned with the package, diffable |
| sheet-metal v2 | `agentcad/toolkit/sheetmetal.py` (extended in place) | one declarative model or fold/unfold diverge |
| hole-record extraction from a built shape | `agentcad/kernel/handlers/holes.py` (**new pack**) | needs the built shape; the only new kernel file |
| `hole_standards` tool, `add_holes` script-edit tool, the rebuild seam | `agentcad/core/tools_holes.py` (**new pack**) | alphabetically at `h`: before `tools_proposals` (`p`), `tools_specs` (`s`), `tools_versioning` (`v`) — so it must read `service.branches` / `service.gate_providers` **inside its methods**, never in `register()` |
| hole callouts from metadata | `agentcad/kernel/handlers/drawing.py` (existing pack, extended) | it already builds the shape and draws the callouts |
| hole-on-face UI | `frontend/js/main.js` face card (existing) | Decision 12 |

**No core is edited.** `worker.py`, `tools.py`, `app.py` and `service.py` are
untouched by this PRD. No route pack is needed: the generic
`POST /api/tools/{name}` is how `push_pull` already reaches the UI.

`tools_holes.py` loading at `h` is the one ordering fact to encode: it is
**earlier** than every seam-owning pack it interacts with, so the
`install_rebuild_holes` wrapper must be installed such that a later
`build_registry` cannot disarm it — the `tools_versioning`-replaces-`write_guard`
trap from PRD-008. Concretely: the wrapper is installed on `service._rebuild`
and `service.get_part`, which no later pack replaces (only *wraps*), so
composition is safe; the plan asserts it with a test that builds the registry
twice and checks the `holes` key still appears.

---

## Decision 3 — how a feature references geometry: coordinates, never ordinals

Every capability here needs to name a piece of geometry. The honest options,
and what each costs:

| reference kind | stable across rebuild? | usable by an agent? | usable by the UI? |
|---|---|---|---|
| mesh-order face **index** | **no** — renumbers on any topology change | poorly | yes, at pick time only |
| a `Plane` written as literal origin + basis vectors | **yes** — coordinates are data | yes | yes, resolved at pick time |
| a **named** axis-aligned face (`"top"` = max-Z planar face by area) | yes *for the parts where it is unambiguous* | yes | yes |
| a build123d selector expression (`part.faces().sort_by(Axis.Z)[-1]`) | yes, and it re-evaluates | yes | awkward to emit |
| **points in part coordinates** | trivially yes | yes | yes |

**Decision, per capability:**

- **`holes.*` take `points` in part coordinates plus a `plane`** — either a
  build123d `Plane`, or the string shorthands `"top"|"bottom"|"front"|"back"|
  "left"|"right"` which resolve to *the extreme planar face along that axis,
  chosen by area* (a documented predicate, re-evaluated every rebuild — not an
  ordinal). A `Plane` may also be passed as a literal
  `Plane(origin=…, x_dir=…, z_dir=…)`, which is what the UI emits.
- **The UI resolves the picked ordinal at pick time and emits literals.** This
  is exactly the sketch-on-face precedent: `sketch_plane` returns a stable
  basis and a `face_id` (`area_mm2`, `normal`, `origin`) recorded for a later
  `face_check: ok | moved | unchecked`. Hole-on-face reuses that handler
  verbatim — it already exists — so a face-click emits
  `holes.clearance(part, plane=Plane(origin=(…), x_dir=(…), z_dir=(…)),
  points=[…], size="M5")` with the renumbering caveat inline, and no ordinal
  survives into the script.
- **`features.draft` takes a face *selector*, not indices** — a predicate over
  `part.faces()` (`filter_by`, normal direction, "all faces not parallel to the
  neutral plane"), because a draft that names 8 ordinals is wrong the first
  time a fillet is added.
- **`features.rib` takes a profile (a sketch/wire/points) and a direction**, in
  part coordinates.
- **Pattern seeds are points or shapes**, never face references.

**What cannot be made stable, stated rather than hidden:** a hole placed
"on the face the user clicked" is only reproducible because we copied the
face's *plane* into the script. If a parameter change moves that face, the
script keeps drilling at the old plane. That is the same failure the sketcher
documents, it is not repairable by this PRD, and the emitted block says so in a
comment — the caveat travels with the code, exactly as `sketch_plane`'s does.

---

## Decision 4 — hole metadata rides the shape, not a registry

The PRD's technical approach (§Technical approach, FR6) says:

> helpers append to a per-build registry in `toolkit.holes`; the kernel worker
> drains it into the build result after `build(p)` returns. […] the worker must
> explicitly reset the registry per request

**That design is not fragile, it is wrong**, and its own acceptance criterion
(AC7 — two consecutive builds of *different* parts never cross-contaminate)
cannot detect the failure. Two independent caches skip the code that would fill
the registry:

1. `_SHAPE_CACHE` (worker, 16 entries): a rebuild of an **unchanged** part
   skips `build(p)`. The registry drains empty and the part's records vanish.
   This is not a rare path — `get_part`, `generate_drawing`, `run_specs`,
   proposal packets and geometric diffs all call `build_shape`, and after the
   first one the rest are cache hits.
2. `.cache/<key>.metrics.json` (service): a rebuild with an unchanged script,
   params, density and tolerance never reaches the kernel at all.

A "reset the registry per request" discipline concentrates the risk in one
place, as the PRD's own risk section says — but it is guarding the wrong
failure. The real failure is *absence*, and absence is silent: the drawing
falls back to geometric detection and nobody sees a warning.

### The mechanism

Measured (§M3): a build123d shape is an ordinary Python object with no
`__slots__`. `setattr` works; the attribute survives `.clean()` and `.moved()`;
it does **not** survive a boolean (which returns a new object — that is the
whole point); and both the `Part` and its `TopoDS_Shape` are weak-referenceable.

**Decision: `toolkit/holes.py` attaches the accumulated record list to the
shape object it returns.**

```python
_ATTR = "_agentcad_hole_records"

def _carry(new_shape, from_shape, new_records):
    prior = list(getattr(from_shape, _ATTR, ()))
    setattr(new_shape, _ATTR, prior + list(new_records))
    return new_shape
```

Three properties follow, and they are the whole design:

- **Cache-coherent by construction.** `_SHAPE_CACHE` stores the very object
  `build(p)` returned, so a cache hit hands back the records with the geometry.
  There is no reset point, so AC7 stops being a discipline and becomes
  structural.
- **Warm-worker contamination is impossible.** There is no shared mutable
  state to contaminate. AC7's regression test still ships — it now proves a
  property instead of guarding one.
- **Records compose along the helper chain.** Every `toolkit` helper that
  takes a part and returns a part (`holes.*`, `patterns.*`, `features.*`,
  `safe_fillet`, `safe_shell`, `safe_bool`) carries the attribute forward.

  > **Amended by spike S2, slice 4 (changelog 0150).** This was written as a
  > property and measured as false: through the worker, `safe_fillet`,
  > `safe_bool` (fuse *and* cut), a raw `part - tool` and even the helpers' own
  > re-entered `BuildPart()` + `add(part)` all return a brand-new object
  > carrying **none** of the original's attributes. Only `.clean()`, `.moved()`
  > and `copy`/`deepcopy` preserve it. Composition is therefore something the
  > code has to *do*: `holes.carry()` is called explicitly by every helper, and
  > `fillet.py` / `shell.py` / `boolean.py` gained a two-line
  > `@holes.carries_records` decorator — a deliberate departure from the plan's
  > file list, because the alternative was a warning on every script that
  > drills and then fillets.

**The gap, stated:** a script that performs a *raw* build123d operation after
its last helper call returns a new object with no attribute, and the records
are lost. This is detectable without any resettable global: `toolkit.holes`
keeps a monotonically increasing `_created` counter (never reset, never read
as an absolute), the harvest reads it **before and after** the build, and when
the delta is non-zero but the returned shape carries fewer records than the
delta, the build result gains a warning:

```
holes: 8 hole record(s) were created but did not reach the returned part —
an operation after the last toolkit call dropped them. Return the part the
helper gave you, or route the later operation through a toolkit helper.
```

A delta of zero means `build(p)` did not run (cache hit), and no comparison is
made. This is a *delta*, so it is immune to the warm-worker problem the PRD
worried about.

**The fallback, if the spike fails:** if a future build123d makes shapes
slotted, the same records move to a `weakref.WeakKeyDictionary` keyed on the
shape (measured to work today), and if *that* fails, to an optional
`hole_records(p, part)` script contract function mirroring
`connectors(p, part)` — which is re-invoked on every call, cache or not, at the
cost of the author restating the positions. The plan's slice-4 spike settles
this **through the real worker**, not in-process.

### The record

```json
{"id": "h3", "family": "clearance", "standard": "iso", "designation": "⌀5.5",
 "size": "M5", "fit": "medium", "positions": [[20,0],[0,20],[-20,0],[0,-20]],
 "count": 4, "axis": [0,0,-1], "plane": {"origin": [0,0,10], "z_dir": [0,0,1]},
 "depth_mm": null, "thru": true,
 "tap": {"pitch": 0.8, "class": "6H", "drill_mm": 4.2},
 "cbore": null, "csk": null,
 "pattern": {"kind": "polar", "id": "p1", "count": 4}}
```

> **Amended in slice 4 (changelog 0150):** the record also carries
> **`centers`** — the instance centres in *part* coordinates, rounded to 9
> decimals — plus `removed_mm3` and the per-instance `instances` report.
> `positions` are plane-local, and slice 6 has to match a record to a detected
> circle group **by centre proximity in the top view**, which plane-local
> positions cannot answer. Without `centers` the drawing slice would have had
> to re-derive them from `plane` + `positions`, i.e. re-implement the plane
> transform in the drawing pack.

A **group is the unit**, not an instance: one call with N points is one record
with `count: N` and N `positions`. FR3's "a pattern of a wizard hole replicates
metadata as one hole group" therefore falls out for free on the points path,
which is the MVP path (Decision 6).

### Harvest and persistence

- **`kernel/handlers/holes.py`** exports one handler, `hole_records
  {script, params}`: it calls the toolbox's `build_shape_ns`, reads the
  attribute off the returned shape, runs the delta check, and returns
  `{holes: [...], warnings: [...]}`. It is cheap on a warm worker because the
  shape cache absorbs the build (measurement obligation §S5).
- **`core/tools_holes.install_rebuild_holes`** wraps `service._rebuild` and
  `service.get_part`, `_WRAPPED`-marked, exactly like `install_rebuild_specs`:
  on a successful rebuild it calls `hole_records`, writes
  `.cache/<key>.holes.json`, and adds a `holes` key to the result; on the
  service's cache-hit path it reads the sidecar. `get_part` reads the sidecar
  only — it never triggers a kernel call it did not already need.
- **Persistence is the sidecar, not `project.json`.** The PRD leaves this
  open ("manifest field vs sidecar: decide in design with a size measurement on
  a 50-hole part"). The size measurement is not the deciding argument; the
  *kind* of data is. Hole records are **derived from the script**, exactly like
  metrics — they have no independent authority, they must be invalidated by the
  same content hash, and they must not be merged. Putting them in
  `project.json` would make `manifest_merge.py` responsible for reconciling two
  branches' derived geometry, would rewrite authored state on every rebuild,
  and would put a 50-hole part's ~6 KB (measured shape: ~120 bytes/record ×
  positions) into a file the user reads. The `.specs.json` precedent is exact:
  same directory, same key, same versioned-reader-that-unlinks-garbage
  discipline, free across branches because `.cache/` is canonical.

Failure mode is uniform with specs: a hole harvest that fails leaves the key
**absent** (not `null`); `null` means "the part declares none".

---

## Decision 5 — standards data: vendored, versioned, provenance-bearing

**Data files, not computation.** ISO 273 clearance diameters, ISO 262 coarse
pitches and tap drills, and counterbore/countersink dimensions for standard
head shapes are *tabulated facts with exceptions*; a formula that reproduces
90% of a table and is wrong for M3 and M20 is worse than no table. ANSI/ASME
adds number and letter drills, which are pure lookup.

```
agentcad/toolkit/data/
  iso_clearance.json      # ISO 273: fine / medium / coarse per nominal
  iso_thread.json         # ISO 262/261: pitch (coarse + fines), tap drill
  iso_cbore_csk.json      # per fastener head family (ISO 4762, ISO 7380, ISO 10642)
  ansi_clearance.json     # ASME B18.2.8 close/normal/loose + drill designations
  ansi_thread.json        # UNC/UNF pitch (TPI), tap drill (number/letter/fraction)
  ansi_cbore_csk.json
```

Every file carries a header object:

```json
{"schema": 1, "standard": "ISO 273:1979", "units": "mm",
 "sources": ["<published source A>", "<published source B>"],
 "revision": "2026-08-13", "rows": {...}}
```

**Provenance rule (non-negotiable, and it is the PRD's own risk):** every row
is transcribed from **two independent published sources** and the spot-check
test asserts a documented sample against the published values. The files are
JSON so a correction is a reviewable one-line diff, and `schema`/`revision`
make a correction visible in a proposal packet.

**`agentcad/toolkit/hole_standards.py` is the third OCP-free toolkit module.**
It loads (cached), validates, and answers queries; it also owns the
**designation grammar**, because the PRD's "designation symbology variants"
risk is really a formatting question:

| family | ISO | ASME |
|---|---|---|
| clearance | `⌀5.5` | `⌀0.217` / `#7` |
| tapped | `M5×0.8 - 6H ↧12` | `10-24 UNC-2B ↧0.50` |
| counterbore | `⌀5.5 ⌴⌀9.5↧5.4` | `⌀0.217 ⌴⌀0.375↧0.213` |
| countersink | `⌀5.5 ⌵⌀10.4×90°` | `⌀0.217 ⌵⌀0.410×82°` |

The `↧`/`⌴`/`⌵` glyphs are emitted **per the hole's declared standard**, and
the mapping table above ships in `docs/part-authoring.md`. The ASME default
countersink angle is 82°, the ISO default 90° — build123d's `CounterSinkHole`
defaults to 82, which is an ASME default arriving in an ISO-labelled call, so
`holes.countersink` passes the angle explicitly, always.

**Naming:** ISO 273 calls the three fits *fine / medium / coarse*; the PRD
writes *close / medium / loose* (the ASME names). Both spellings are accepted;
the canonical key follows the requested `std`, and the record stores the
canonical one. This is documented rather than picked, because an agent will
type both.

`hole_standards {family?, size?, std?}` is a **pure-data tool, registered
unconditionally**, and it is what lets a UI dialog and an agent read the same
numbers the geometry used.

---

## Decision 6 — patterns: geometry-level, points-first, with a measured per-instance contract

### What is MVP

| API | MVP | why |
|---|---|---|
| `patterns.bolt_circle(r, n, start_deg=0)` → points | **yes** | pure arithmetic, zero risk, and it is what makes `holes.*` a one-liner |
| `patterns.grid(nx, ny, dx, dy, center=True)` → points | **yes** | same |
| `patterns.linear(part, seed, direction, count, spacing)` | **yes** (seed = a `Shape`) | one `Locations` + fuse |
| `patterns.polar(part, seed, axis, count, span_deg=360)` | **yes** (seed = a `Shape`) | `PolarLocations` |
| `patterns.mirror(part, plane)` / `patterns.mirror(part, seed, plane)` | **yes** | build123d `mirror` |
| seed = a **feature callable** | **phase 2** | see below |
| along-path patterns | **out** — not in the PRD's FR list; named here so nobody adds it quietly |

> **Settled by measurement in slice 3 (changelog 0149):** for a shape pattern
> the seed is **already fused into the part**, so `count` is the total instance
> count (CAD convention), `count=1` is a no-op, and the helper places
> instances 1..count-1. The natural hand-written form
> `with PolarLocations(0, n): add(seed)` re-adds the seed onto itself at
> instance 0; measured, that coincident re-fuse is *safe* (one valid solid,
> identical volume) but **not byte-free** (`85dd9044…` vs `930d1ee7…`). The
> helpers skip instance 0 and are byte-identical to the hand-written form that
> also skips it.

**Points-first, because the PRD's own example is points-first.** A pattern of a
*hole* is spelled `holes.clearance(part, points=patterns.bolt_circle(...))` —
one hole call, one group record, one boolean. That is FR3 satisfied by
construction, and it is what the examples already do by hand.

**Patterns of geometry are geometry-level (a `Shape` seed).** A "feature-level"
pattern — replay an arbitrary modeling operation at N transforms — needs the
operation to be a first-class value. In a script-as-source-of-truth system that
value is *a Python callable*, and a callable that fails at instance 7 has to be
allowed to fail without failing the other 49. That is real, but it is Phase 2,
and its contract is: `patterns.linear(part, feature=fn, …)` where
`fn(part, location) -> (part, warning|None)`, run per instance, each failure
caught and reported as `{"i": 7, "status": "failed", "error": "..."}` with the
part left as it was before that instance.

### The per-instance failure contract — measured, and the surprise

The PRD asks for "overlap and degenerate spacing produce warnings, never silent
geometry". Measured (§M4), OCCT's behaviour is the opposite of alarming:

```
cut a ⌀4.2 cylinder entirely off the part:  1.7 ms, volume UNCHANGED, is_valid True
```

**A misplaced instance is a silent no-op, not an error.** So "never silent
geometry" is not something OCCT gives us; it is something the helper has to
*measure*, and measurement costs (§M5):

| probe | cost at N=50 |
|---|---:|
| bounding-box overlap per instance | **0.73 ms total** (0.015 ms each) |
| exact `(part & tool).volume` per instance | **210 ms total** (4.2 ms each) |
| the pattern's own boolean (`Locations`+`Hole`, N=50) | 109 ms |

An exact probe **triples** the cost of a 50-hole pattern. So the contract is
two-tier and the tier is in the API:

> **Re-measured through the worker (spike S3, changelog 0149):** bbox
> **0.014 ms** per instance and `&` **2.1 ms** (50-hole plate) / **2.43 ms**
> (`gusset_plate`'s real bolt group) — so the exact tier roughly *doubles* a
> 50-instance pattern rather than tripling it. The split below is what shipped;
> only the multiplier moved, and in the kinder direction.

- **Always on (free):** bbox-overlap per instance; pairwise point spacing vs
  the feature's own diameter (overlap detection is arithmetic on the point set,
  not geometry); and a whole-operation volume-delta check — if the total
  removed volume is zero the call warns loudly whatever the tiers say.
- **`verify="exact"` (opt-in, default off):** the `(part & tool)` probe per
  instance, reporting `engaged_mm3` per instance.
- The return is `(part, records, warning|None)` as FR4 requires, and the
  per-instance detail rides in the record's
  `instances: [{"i": 7, "status": "missed"}]` — so an agent that wants the
  detail reads the record, and an agent that wants the headline reads the
  warning. Nothing is swallowed at either level.

`&` on disjoint shapes returns an **empty `Compound` with `.volume == 0`**
(measured) — never `None`, never a raise. That matches the standing AGENTS.md
gotcha and is the reason the probe is written with `&` and not
`Shape.intersect()`.

---

## Decision 7 — ribs and bosses

**`features.boss` is easy and ships in the same slice as ribs.** A boss is a
cylinder (optionally drafted) fused at a point, optionally with a tapped hole —
i.e. it is `safe_bool(part, Cylinder(...), "fuse")` plus a `holes.tapped` call,
and its only real content is the *convention* (bearing face at the seat, height
measured from the seat, `hole="M3"` bores the tap drill and records the thread).

**`features.rib` is the fragile one.** There is no rib operation in OCCT or
build123d; a rib is a construction:

1. take the profile (a 2D wire/points in a plane),
2. thicken it symmetrically by `thickness` (an offset of an open wire — the
   operation `safe_shell` already documents as the shaky one),
3. extrude it toward the part until it is fully buried,
4. **trim it to the part's envelope** — the step that decides the result, and
   the step OCCT has no primitive for,
5. `safe_bool(..., "fuse")`.

The trim is the design question, and there are two honest answers. (a) **Extend
and intersect**: extrude the thickened profile generously in both directions
and `&` it with a "material envelope" (the part's own convex-ish bounding
solid), which is robust but can add material outside the part. (b) **Extrude to
a stated depth** and require the caller to supply it, which is dumb and always
works. MVP is **(b) with (a) available as `to="part"`**, and the warning names
which was used. Anything cleverer is a solid-modeling kernel feature we do not
have.

Draft on a rib (`draft_deg`) is applied by tapering the extrusion, **not** by
calling the draft operation on the finished part — the measurement in
Decision 8 says why (a rib on a shelled part would fail the draft outright).

---

## Decision 8 — draft: measured, and better news than the PRD expects

The PRD puts draft in Phase 3 as "the hardest OCCT surface […] ships when its
warning contract is honest". Measured (§M6), the picture is sharper than that
and it *changes the phasing*.

`build123d 0.11.1` exposes `draft(faces, neutral_plane, angle) -> Part`
(`build123d.operations_part`) over `BRepOffsetAPI_DraftAngle`, plus a
`DraftAngleError`. Sweeping the angle from 0.5° to 60° on four shapes:

| part | 0.5° | 1° | 2° | 3° | 5° | 7° | 10° | 15° | 20° | 30° | 45° | 60° | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---:|
| box 40×30×20, 4 sides | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | **fail** | fail | 4 ms |
| box + R4 vertical fillets (8 faces) | ok | ok | ok | ok | ok | ok | ok | **fail** | fail | fail | fail | 8 ms |
| box + boss (a cylindrical side face) | ok | ok | ok | ok | ok | ok | ok | ok | **fail** | fail | fail | 5 ms |
| **shelled box, t = 2** | ok | ok | ok | **fail** | fail | fail | fail | fail | fail | fail | fail | 8 ms |

Three findings, in order of value:

1. **Failure is monotone in the angle** on every shape measured — a clean
   `ok…ok fail…fail` boundary, no islands. That is exactly the precondition
   `safe_fillet`'s binary search needs, and it means `safe_draft` is a ~40-line
   helper, not a research project. *(The plan's spike widens this sweep over
   the bundled examples before the search is trusted; if an island is found,
   the search degrades to a descending linear sweep with a documented step —
   a slower helper, not a broken one.)*
2. **The practical ceiling is low, and it is lowest exactly where draft
   matters.** A shelled enclosure — the canonical injection-moulded part —
   caps at **2°**. The standard advice for that part is 1–3°, so the helper is
   useful; but "draft this enclosure at 5°" is a request OCCT will refuse, and
   the product answer is the `safe_*` answer: apply 2°, return it, and say so.
   **The fallback is the feature.**
3. **OCCT says nothing about why.** The failure raises `Standard_Failure` with
   an **empty message**. So the warning has to be written entirely from what we
   measured — "requested 5.0°, applied 2.0° (the largest that succeeded on
   these 8 faces)" — because there is no upstream text to pass through. Feed
   the empty-message case to the Error Doctor as a known pattern.

At 4–8 ms per attempt, a binary search over ~8 attempts is ~60 ms. That is
affordable inside a rebuild.

**Decision: `features.draft` moves out of Phase 3 into the main sequence,
behind a spike that widens the monotonicity measurement.** It is cheap, its
contract is the one this repo already ships three times, and shipping it late
would have been a phasing decision taken on an assumption that measurement
contradicts.

---

## Decision 9 — sheet-metal v2

### The model change

`_Flange` gains `start`, `width` and `relief`; the per-edge uniqueness rule
becomes a **non-overlap** rule over `[start, start + width)` on that edge:

```python
def flange(self, edge, angle_deg, length, inner_radius=None,
           start=0.0, width=None, relief="auto"): ...
def hem(self, edge, kind="open", length=6.0, start=0.0, width=None): ...
def corner(self, edge_a, edge_b, treatment="close"): ...   # close | gap | rip
```

`width=None` keeps v1's full-edge meaning, so **every existing script and both
existing tests keep their exact geometry** — the v1 corpus is the gate.

### Relief

Wherever a partial flange's end meets remaining material, a relief cut is made
**through the base plate**, at the end of the bend zone, in *both* `fold()` and
`unfold()`, from one shared computation:

- `rect` — width `max(t, 1.5*t)` × depth `R + t` past the bend line (the
  common shop rule), square-ended;
- `round` — same envelope, semicircular end (the fatigue-friendlier one);
- `tear` — no material removed; the flange simply stops and the sheet tears
  in the brake. Modeled as **no cut**, and the warning says the model shows no
  relief because a tear relief has none.

The sizing rule is a named constant with the rule written next to it, and
`relief=` accepts an explicit `{"kind": ..., "width": ..., "depth": ...}` for a
shop with different rules. **The rule is a default, not a standard** — no ISO
governs it, and the docs must not imply one.

### `flat_outline()` — consistency by construction

Today `flat_outline()` re-derives the blank shape by hand. With partial tabs,
gaps, reliefs and hems, that walker becomes a combinatorial corner-case
machine, and the PRD's own risk list says so.

**Decision: derive the flat outline from `unfold()`'s own top face.** Measured
(§M7): `flat.faces().filter_by(Plane.XY).sort_by(Axis.Z)[-1].outer_wire()`
costs **1.2 ms** on a two-flange bracket and returns the exact edge list. So:

- `flat_outline()` keeps its v1 signature and return type — a CCW list of
  `(x, y)` — but is now a **discretization of the real blank** at a documented
  chord tolerance, not a parallel model. Arcs (round reliefs, hems) become
  polyline runs; the tolerance is a parameter and is stated in the docstring.
- a new `flat_outline_edges()` returns the exact segments and arcs for anyone
  who needs them (DXF export, PRD-014).
- FR12's "one-spec-consistent" stops being an invariant to maintain and
  becomes a fact: the outline *is* the unfolded solid.

Property tests replace golden points, as the PRD's risk section asks: the
outline is closed, is CCW, and its enclosed area equals the unfold's top-face
area within tolerance.

### Hems — the honest scope

A hem is a 180° bend. `SheetPart` today rejects `angle_deg >= 180` and its
profile builder assumes a leaf departing tangentially from a sector, which a
**teardrop** hem (which wraps past 180°) violates by self-intersecting the
leaf. So:

- **`open` and `closed` hems are in scope.** A closed hem is a 180° bend at a
  small inner radius; the minimum radius at which the fold boolean still
  produces one valid solid is a **measured number**, not a chosen one
  (spike §S8) — a true zero-radius closed hem is not representable in a B-rep
  and the docs will say the model shows `R = <measured minimum>`.
- **`teardrop` is out unless the spike says otherwise**, and the design says so
  now rather than after someone has spent a slice on it. If the spike shows
  the >180° profile can be built as a sector + a second sector, it comes back
  in; if not, `kind="teardrop"` raises a `ValueError` naming the reason.

### Corner treatments

`close | gap | rip` where two flanges meet at a corner. `gap` is a relief-like
cut of stated width; `rip` is the no-material case; `close` mitres the two
leaves so they meet — which is a boolean between two flange solids and is the
one with real failure risk. `sp.warnings` records any fusion fallback, as v1
already does.

---

## Decision 10 — what consumes the metadata, exactly

**In scope, and nothing else:**

1. **`generate_drawing` callouts (FR13, AC2).** The drawing handler already
   builds the shape; it reads the attached records directly (no second kernel
   call), matches a record to a detected circle group by diameter and centre
   within a stated tolerance, and prints the record's designation. `detected`
   gains `from_metadata: true` per group plus the designation string; groups
   with no record keep today's geometric text. **Known limitation, inherited:**
   `_detect_circles` reads the **top view only** and groups with `count < 3`
   are not emitted at all, so a hole on a side face gets no callout even with a
   perfect record. Fixing that is drawings work (PRD-014), not this PRD, and
   the limitation is documented rather than partially patched.
2. **`get_part` and the rebuild result gain `holes`** (FR6) — the records, for
   any client.
3. **`hole_standards`** — the table data, for agents and dialogs.

**Explicitly out of scope, with the reason:**

- **PMI** (`entry["pmi"]` in the manifest) is *authored* tolerance intent.
  Hole records are *derived* geometry facts. Merging them would make a rebuild
  rewrite the user's authored state. They stay separate, and the drawing
  handler reads both.
- **Hole tables** — PRD-014.
- **BOM / release** — PRD-015.
- **DFM rules over hole intent** — PRD-021 consumes the record; it does not
  ship here. The record's shape is designed to be enough for it (family,
  designation, depth, tap class) and that is the whole of this PRD's
  obligation.
- **Configurations** — PRD-012.

---

## Decision 11 — the agent surface stays small

- **New tool: `hole_standards {family?, size?, std?}`** — pure data,
  registered unconditionally, no kernel call.
- **New tool: `add_holes {project, part_id, plane|face_index, points, family,
  size, …}`** — the `push_pull` pattern exactly: validate, append a marked,
  counter-suffixed block that rebinds `build`, `service.update_part`, return
  `{**with_hint(result), …}`. This is what the UI dialog calls, and an agent
  may call it too. It is a *script-editing* tool, not a geometry tool: the
  script remains the source of truth and the appended line is ordinary,
  reviewable, editable code.
- **Changed:** rebuild results and `get_part` gain `holes`;
  `generate_drawing`'s `detected` gains `from_metadata` and designations.
- **No new geometry tools.** Warnings ride the existing rebuild `warnings`
  channel. Error types unchanged.

---

## Decision 12 — the UI ships on the existing face card, not on PRD-016

FR14 says the dialogs are "hosted by PRD-016's shell". **PRD-016 is pending and
unbuilt**, so a UI slice that depends on it cannot land. But the host already
exists: `frontend/js/main.js:1420-1653` keeps a face selection keyed to the
mesh cache key, calls `face_info`, renders a face card and applies push/pull.

**Decision: hole-on-face-click extends that card** — a family/standard/size/fit
control group and an Apply that calls `add_holes`, which appends the call and
rebuilds. It is one card, not a dialog system; when PRD-016 lands it inherits
the tool and re-hosts the control. The pattern dialog is **deferred to
PRD-016** (a pattern needs a direction/axis picker, which is the thing PRD-016
is actually for) — the `patterns.*` script vocabulary ships without it, which
is what the agent path needs.

---

## What the PRD asks for that cannot be delivered as written

Four items. Each is a restatement, not a refusal, and each is grounded.

**1. AC1's "the same content-hash mesh-cache entries" is impossible.** The
cache key is `sha256({content, params, density, tolerance, format})` where
`content` **is the script text** (`core/service.py:555-605`). Rewriting the
construction example's bolt patterns changes the script text, therefore changes
the key, therefore changes the `.cache/<key>.acm` filename — by construction,
for any rewrite whatsoever. And "identical geometry" is ambiguous: measured
(§M1), two constructions with identical volume to 6 dp and identical face
counts can tessellate to **different bytes**.

> **Restated AC1:** the rewritten construction parts produce (a) metrics equal
> to the pre-rewrite goldens — `volume_mm3`, `area_mm2`, `mass_g`, `bbox`,
> `center_of_mass` to `rel=1e-9`, and `n_faces`/`n_edges`/`n_solids` exactly —
> and (b) a **byte-identical `.acm` payload** under its new key. (b) is
> achievable and is why Decision 1 exists (measured identical when the helper
> re-enters a `BuildPart`); if the spike finds a case where it is not, (b)
> degrades to "the mesh differs only in face ordering", stated in the changelog
> with the diff, and never silently dropped.

**2. FR6's registry-drain harvest is wrong, not merely fragile.** Two caches
skip `build(p)`; the records vanish silently. Decision 4 replaces it. AC7 is
kept and **AC7b is added**: rebuild the *same* part twice on one warm worker
and assert the records are identical both times — the regression the original
AC7 cannot see.

**3. FR14's UI depends on an unbuilt PRD.** Decision 12 scopes the hole dialog
onto the existing face card and defers the pattern dialog to PRD-016.

**4. FR11's teardrop hem may not be representable** in `SheetPart`'s
sector-plus-tangent-leaf profile model, which self-intersects past 180°. It is
gated behind a spike and will be refused with a reason rather than approximated
silently. Similarly, a *true* closed hem has zero inner radius, which no B-rep
has; the model will show a measured minimum radius and say so.

One more, smaller: **`patterns.*` "along path" appears in the design-task
framing but not in the PRD's FR list.** It is out. Naming it here stops it
arriving as scope creep.

---

## Measurement obligations — the spike that settles each risk

Every claim below that a slice depends on must be **measured before the slice's
code is trusted**, on the bundled examples where one applies. A spike that
contradicts the design is a design change, not a bug to work around.

| # | Claim the design rests on | Spike | Slice | Status now |
|---|---|---|---|---|
| S1 | A helper that re-enters `BuildPart` + `add()` reproduces the hand-written script's mesh byte-for-byte, **on real example parts through the kernel worker** | rebuild `construction/gusset_plate`, `rocketry/flange`, `prototyping/enclosure_lid` both ways; compare `.acm` sha + metrics | 1 | measured in-process on a synthetic plate (§M1/§M2) — **not yet on real parts** |
| S2 | A build123d shape carries a Python attribute through `_SHAPE_CACHE`, a real rebuild, and `handle_build`'s tessellation | attach records in a part script, drive `build` then `hole_records` through the worker, then repeat for a **cache hit** and after 17 other builds (LRU eviction) | 4 | **measured through the worker — holds** (changelog 0150): cache hit returns the same object with the records, LRU eviction + rebuild reproduces them, and they survive a build that writes an `lod1` tier. No fallback rung needed. The service's cached rebuild makes **0 kernel calls**, so the sidecar is mandatory, not an optimisation |
| S3 | Per-instance engagement can be reported without tripling the cost | bbox vs `&` probe cost on a 50-hole plate and on `gusset_plate`'s real bolt groups | 3 | **measured through the worker** (changelog 0149): bbox 0.014 ms/instance, `&` 2.1–2.4 ms/instance vs ~98–111 ms for the whole 50-instance boolean — the exact tier roughly **doubles** the cost rather than tripling it. API split unchanged |
| S4 | A misplaced instance is a silent no-op that only we can catch | cut off-part, cut through air, cut a hole larger than the stock | 3 | **measured through the worker** (changelog 0149): 0.89 ms on a 50-hole plate and 1.01 ms on `gusset_plate`'s blank, volume delta **exactly 0.0**, `is_valid True`, nothing raised. `&` on a disjoint pair: empty `Compound`, `.volume == 0`, 0 solids |
| S5 | `hole_records` after a rebuild is cheap (shape-cache hit) | time `build` + `hole_records` back-to-back on `enclosure_base` (0.86 s cold) and `intake_manifold` (38 s cold) | 5 | unmeasured — this is the one that could make the seam too expensive |
| S6 | Draft failure is **monotone** in the angle | sweep 0.5→60° on ≥ 8 faces sets across `enclosure_base`, `nozzle`, `gusset_plate`, `angle_bracket` | 10 | measured on 4 synthetic shapes, monotone on all (§M6) |
| S7 | A rib's trim-to-part step produces one valid solid on a real part | rib on `enclosure_base`'s floor, both `to=` modes | 9 | unmeasured |
| S8 | A closed hem folds validly, and at what minimum inner radius | sweep `inner_radius` from `t` down to `0.01·t` at 180° | 12 | unmeasured; **teardrop is gated on this** |
| S9 | Partial flanges + relief keep `fold()`/`unfold()` volume-consistent | build the AC4 bracket; assert `fold().volume == unfold().volume` within the bend-allowance model's own tolerance, and outline area == top-face area | 11 | unmeasured |
| S10 | The table values are right | spot-check ≥ 12 rows against two published sources | 2 | obligation, not a measurement |

---

## Measurement appendix

All numbers below were produced on this machine, build123d 0.11.1 / OCCT via
the pinned wheels, Python 3.12. Scripts were written to the session scratchpad;
the plan's slices re-create them under `scratchpad/` and print the numbers into
the changelog.

**§M1 — construction route vs mesh identity** (`spike_ident.py`). 120×120×10
plate, 8 × ⌀4.2 holes on an 80 mm bolt circle, `tessellate(shape, 0.1)`:

```
A builder (Locations+Hole)  vol=142891.646112 faces=14 sha=9fbc6288a1bdf13a
B plate - Compound(cyls)    vol=142891.646112 faces=14 sha=c2b5400dfeda65ae
C builder, points reversed  vol=142891.646112 faces=14 sha=9fbc6288a1bdf13a
```

**§M2 — a helper can be byte-faithful** (`spike_ident2.py`). Same plate, the
helper form `BuildPart() → add(plate) → Locations(...) → Hole(...)`:

```
ref     9fbc6288a1bdf13a  142891.646112
helper  9fbc6288a1bdf13a  142891.646112  38 ms   IDENTICAL
ref2    9fbc6288a1bdf13a  (determinism across two runs: same)
```

**§M3 — the metadata carrier** (`spike_a.py`):

```
type Box, no __slots__ anywhere in the MRO
setattr OK
after a boolean:  attribute absent   (new object — expected)
after .clean():   attribute present
after .moved():   attribute present
weakref(Part) OK ; weakref(TopoDS_Shape) OK
```

**§M4 — pattern boolean strategies, 50 holes in a 200×200×10 plate**
(`spike_pat.py`):

```
(a) BuildPart + Locations + Hole   109.4 ms  vol=393072.8 valid solids=1
(b) fuse 50 tools, then one cut    342.1 ms  vol=393072.8
(b2) Compound(50 tools), one cut   106.1 ms  vol=393072.8
(c) 50 sequential cuts             320.1 ms  vol=393072.8
(d) 50 × safe_bool cut             712.0 ms  vol=393072.8
(e) cut ENTIRELY OFF the part        1.7 ms  vol UNCHANGED, is_valid True
```

Line (e) is the one that shaped Decision 6.

**§M5 — the cost of honesty** (`spike_probe.py`), same 50 instances:

```
50 × (part & tool).volume   210.4 ms   (disjoint → empty Compound, .volume == 0)
50 × bounding_box             0.73 ms
```

**§M6 — draft feasibility and monotonicity** (`spike_draft.py`): the table in
Decision 8. Every failure raised `Standard_Failure` with an **empty message**;
per-attempt cost 4–8 ms.

**§M7 — sheet metal today** (`spike_sm.py`), `SheetPart(2.0).base(100,60)
.flange("front",90,25).flange("right",90,20)`:

```
fold()                        70 ms   vol=20908.0  valid  solids=1
unfold()                      11 ms   vol=20847.6  flat_outline() → 8 points
outline from the unfold solid  1.2 ms  6 edges, all LINE, area=10423.8
a relief cut in the base plate  5.3 ms valid
```

---

## Risks this design does not remove

- **The face-plane caveat** (Decision 3). A parameter change that moves a face
  leaves the emitted plane behind. Documented in the emitted comment; not
  repairable here.
- **`generate_drawing` sees the top view only.** A perfect record on a side
  hole still gets no callout. PRD-014.
- **The bend-allowance model is a k-factor approximation**, unchanged from v1.
  v2 adds features to it, not accuracy.
- **Table transcription.** Two sources and a spot-check test reduce it; they do
  not eliminate it. The `revision` field is what makes a correction visible.
- **`hole_records` adds one kernel round-trip per uncached rebuild.** S5 is the
  spike; if it measures badly on `intake_manifold` (15 s warm), the seam
  becomes lazy — records harvested on demand from `get_part` only, and the
  rebuild result carries `holes: {"deferred": true}` rather than a number
  nobody paid for.
