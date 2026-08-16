# Feature toolkit II — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship [PRD-010](../../prd/in-progress/PRD-010-feature-toolkit-ii.md) —
linear/polar/mirror patterns and bolt-circle/grid point helpers; an ISO/ANSI
hole wizard (clearance, tapped, counterbore, countersink) driven by vendored
standards tables; machine-readable hole metadata that survives a rebuild and
reaches drawing callouts; rib/boss/draft helpers under the `safe_*` honest-
warning contract; and sheet-metal v2 (partial-width flanges, automatic bend
relief, hems, corner treatments) — per
[the design spec](../specs/2026-08-13-feature-toolkit-ii-design.md).

**Architecture (one paragraph):** four new kernel-side toolkit modules
(`patterns.py`, `holes.py`, `features.py`) plus one **OCP-free** data module
(`hole_standards.py` + `toolkit/data/*.json`) give part scripts the vocabulary;
the helpers are **thin, byte-faithful wrappers over build123d's own
`Locations`/`Hole`/`mirror` operations** (measured: re-entering a `BuildPart`
reproduces a hand-written script's mesh byte-for-byte — that is what makes AC1
reachable), with `safe_bool` as the fallback rung and an honest per-instance
guard the helper has to *measure* because a misplaced OCCT cut is a silent
no-op, not an error. Hole metadata **rides the built shape as a Python
attribute** rather than a drained global registry — the worker's 16-entry
`_SHAPE_CACHE` skips `build(p)` on a hit, so a registry drains empty and the
records vanish silently — and is harvested by one new handler pack
(`kernel/handlers/holes.py`), persisted as a `.cache/<key>.holes.json` sidecar
on the `.specs.json` precedent, and surfaced through an
`install_rebuild_holes` wrapper on `service._rebuild`/`get_part`. Drawings read
the records in-process from the existing drawing pack. `SheetPart` grows
`start`/`width`/`relief`/`hem`/`corner` in place, and `flat_outline()` stops
being a parallel implementation: it is derived from `unfold()`'s own top face
(measured 1.2 ms), which is what makes FR12's consistency a fact instead of an
invariant to maintain.

**Tech stack:** Python 3.12 + build123d 0.11.1 (pinned) / JSON data files
vendored in the package / pytest with the session-scoped `kernel` fixture /
plain ES modules in `frontend/js/main.js`. **No new runtime dependency. No new
vendored frontend library.**

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** `patterns.py`,
  `holes.py`, `features.py` and `sheetmetal.py` are kernel-side (they import
  `b3d` and run in the worker or in part scripts). **`hole_standards.py` is the
  THIRD OCP-free toolkit module** (with `sketch.py` and `specs.py`) because the
  server's `hole_standards` tool imports it. **Assert it**: import
  `agentcad.toolkit.hole_standards` in a fresh interpreter with `OCP` blocked
  at `sys.meta_path` and assert it loads — the PRD-009 slice-1 pattern.
- **Packs over cores. Do not edit `worker.py`, `tools.py`, `app.py` or
  `service.py`.** New capability arrives through the existing discovery scans:
  one handler pack (`kernel/handlers/holes.py`), one tool pack
  (`core/tools_holes.py`), and edits to the existing `kernel/handlers/
  drawing.py` pack. **No route pack** — `POST /api/tools/{name}` is how
  `push_pull` already reaches the browser.
- **`tools_holes.py` loads at `h`** — before `tools_proposals` (`p`),
  `tools_specs` (`s`) and `tools_versioning` (`v`). Read `service.branches`,
  `service.specs`, `service.gate_providers` **inside methods, never in
  `register()`**, and assert the seam survives a second `build_registry()`.
- **Files this plan may modify, exhaustively.** Anything outside this list is a
  design bug — stop and re-read the spec.
  1. `agentcad/toolkit/patterns.py`, `holes.py`, `features.py`,
     `hole_standards.py` — **new**
  2. `agentcad/toolkit/data/*.json` — **new**
  3. `agentcad/toolkit/sheetmetal.py` — extended in place
  4. `agentcad/toolkit/__init__.py` — re-exports
  5. `agentcad/kernel/handlers/holes.py` — **new**, the only new kernel file
  6. `agentcad/kernel/handlers/drawing.py` — callouts from metadata
  7. `agentcad/core/tools_holes.py` — **new** (tools + the rebuild seam)
  8. `agentcad/kernel/error_doctor.py` — the empty-message draft failure
  9. `frontend/js/main.js`, `frontend/js/api.js`, `frontend/css/app.css`
  10. `examples/construction/parts/*.py` — **slice 7 only**, after slice 1's
      golden proves identity
  11. `agentcad/core/templates.py` (CHEATSHEET), `docs/*.md`, `tests/*`
- **All existing tests keep passing. Baseline: 2018 passed, 1 skipped.** State
  the count in every slice's verification; **re-measure the baseline in slice 1
  rather than trusting this note**. No unexplained skips, ever.
- **Examples tests run on a copy** (`shutil.copytree(..., ignore=
  ignore_patterns(".cache", "exports"))`) — never mutate `examples/` in place.
  Slice 7 is the single exception and it edits the committed scripts
  deliberately, gated on slice 1's golden.
- **`TestClient(base_url="http://127.0.0.1")`**, and
  `create_app(..., extra_allowed_hosts={"testserver"})` for any WebSocket test.
- **Never `uv sync` / `uv pip install` into the shared venv from a parallel
  agent** — use a scratch venv. This plan adds **no dependency**, so there is
  no lock refresh at all; if you find yourself needing one, the design changed.
- **Subagents do not run `git`.** Staging, committing and branch work belong to
  the driving session.
- **Every slice whose feasibility rests on OCCT behaviour OPENS with a spike**
  that measures it, on the bundled examples, through the kernel worker. Slices
  1, 3, 4, 5, 9, 10, 11 and 12 do. The spike's numbers go in the changelog. If
  a spike contradicts the design, **stop and amend the design spec** — that is
  the point of the spike, not a detour around it.
- **A measurement, not an adjective.** "It works", "it's fast", "it's stable"
  are not verifications. Print the number.
- **UI slices are verified in a real browser** — screenshot, zero console
  errors, via the `run` skill.
- **Every slice lands with `docs/changelog/NNNN-<slug>.md`** staged in the same
  commit. The highest existing entry at planning time is **0146**; recompute
  the next number from the tree when you land — do not trust this note.

---

## Slice map

| # | Slice | Ships | Feasibility |
|---|---|---|---|
| 1 | Identity harness + example goldens + baseline | the gate AC1 will be judged against, before anything changes | **spike first** (S1) |
| 2 | Standards data + `hole_standards` tool | FR5, **AC3**; no geometry at all | proven (data) |
| 3 | `toolkit/patterns.py` | FR1, FR2; the per-instance guard | **spike first** (S3, S4) |
| 4 | `toolkit/holes.py` — clearance + tapped, records on the shape | FR4 (ISO half), FR7 | **spike first** (S2) |
| 5 | Metadata pipeline: handler pack, sidecar, rebuild seam | FR6, **AC7 + AC7b** | **spike first** (S5) |
| 6 | Drawing callouts from metadata | FR13, **AC2**, **AC5** | proven |
| 7 | AC1: rewrite the construction example | **AC1**, FR15 (partial) | gated on slice 1 |
| 8 | Counterbore + countersink + ANSI tables | FR4 (rest), FR5 (ANSI) | proven |
| 9 | `features.rib` / `features.boss` | FR8 (rib/boss half) | **spike first** (S7) |
| 10 | `features.draft` | FR8 (draft half) | **spike first** (S6) |
| 11 | Sheet-metal v2a: partial flanges, relief, outline-from-unfold | FR9, FR10, FR12, **AC4** | **spike first** (S9) |
| 12 | Sheet-metal v2b: hems + corner treatments | FR11 | **spike first** (S8) |
| 13 | UI: hole-on-face on the existing face card | FR14 (hole half) | proven (host exists) |
| 14 | Docs, cheat-sheet, acceptance tests, PRD close-out | FR15, **AC6, AC8**, close-out | proven |

Slices 1–7 are the PRD's MVP. 8–10 and 12 are its Phase 2 (**with draft
promoted out of Phase 3 — see design Decision 8: failure is monotone in the
angle, so the binary-search helper is 40 lines, not a research project**).
13 is what of Phase 3 can land without PRD-016. Each slice is independently
landable and leaves the suite green.

---

## Slice 1 — the identity harness

**Nothing ships to users. This slice writes down what is true today**, so
slice 7 cannot quietly change the bundled examples' geometry and slice 3/4
cannot quietly change what a hole is.

### Spike (design risk S1: is a byte-faithful helper actually possible on real parts?)

- [ ] `scratchpad/spike_identity.py`, run **through the kernel worker** (never
      by importing build123d outside `agentcad/kernel/`): for
      `construction/gusset_plate`, `rocketry/flange` and
      `prototyping/enclosure_lid`, build the shipped script, then build a
      variant whose hole/pattern block is replaced by the helper form
      (`BuildPart() → add(part) → Locations(...) → Hole(...)`). Compare
      `sha256` of the `.acm` payload, every metric, and `n_faces`/`n_edges`.
- [ ] Record the result in the changelog **as a table**, whichever way it comes
      out. The design predicts byte-identical (measured in-process on a
      synthetic plate: `9fbc6288a1bdf13a` both ways). If a real part differs,
      **amend restated-AC1 (b) in the design spec** before slice 7 and say what
      differs (face ordering? tessellation seed? topology?).

### Files
- `tests/test_examples_golden.py` — **new**
- `tests/test_toolkit_ocp_free.py` — **new**
- `docs/changelog/NNNN-feature-toolkit-ii-baseline.md`

### Tasks
- [ ] `tests/test_examples_golden.py`: for every part of `construction` (and
      `rocketry/flange`, `prototyping/enclosure_lid` as controls), on a
      **copy**, at default params, capture and assert `volume_mm3`,
      `area_mm2`, `mass_g`, `bbox`, `center_of_mass` (`rel=1e-9`) and
      `n_faces`/`n_edges`/`n_solids` (exact), plus the `sha256` of the built
      `.acm`. Goldens live in the test file as literals, generated by running
      it once — **not** in a JSON fixture nobody reads.
- [ ] `tests/test_toolkit_ocp_free.py`: the fresh-interpreter assertion
      scaffold (block `OCP` at `sys.meta_path`, import the module, assert it
      loads and that `"OCP" not in sys.modules`). It covers `sketch` and
      `specs` today and gains `hole_standards` in slice 2 — write it so adding
      a module is one line.
- [ ] Re-measure and record the true full-suite baseline. Do not trust the
      "2018 passed, 1 skipped" note.

### Verification
- [ ] `uv run pytest -q tests/test_examples_golden.py tests/test_toolkit_ocp_free.py` — green; state the count.
- [ ] Paste the spike's identity table into the changelog with the sha values.
- [ ] `make test` — state the measured baseline count.

---

## Slice 2 — standards data and the `hole_standards` tool

Pure data. No geometry, no kernel call, no OCP. This is the slice that can be
reviewed against published sources without anyone reading OCCT.

### Files
- `agentcad/toolkit/hole_standards.py` — **new**, OCP-free
- `agentcad/toolkit/data/iso_clearance.json`, `iso_thread.json`,
  `iso_cbore_csk.json` — **new** (ANSI files land in slice 8)
- `agentcad/core/tools_holes.py` — **new** (only `hole_standards` for now)
- `tests/test_hole_standards.py` — **new**
- `tests/test_toolkit_ocp_free.py` — add the module
- `docs/changelog/NNNN-hole-standards-tables.md`

### Tasks
- [ ] Data files carry the header from design Decision 5:
      `{"schema": 1, "standard": "...", "units": "mm", "sources": [A, B],
      "revision": "YYYY-MM-DD", "rows": {...}}`. **Every row transcribed from
      two independent published sources**; put both in `sources` and name them
      in the changelog. A row you cannot corroborate does not ship.
- [ ] `hole_standards.py`: cached loaders (`functools.lru_cache`), a `lookup`
      API (`clearance(size, fit, std)`, `thread(size, pitch=None, std)`,
      `cbore(size, fastener, std)`, `csk(size, angle, std)`), eager validation
      raising `ValueError` naming the argument (the `toolkit/specs.py`
      convention — a bad call in a part script must surface as a `script_error`
      with `details.line`), and the **designation grammar** per standard.
- [ ] Accept both fit spellings (`fine|medium|coarse` per ISO 273 and
      `close|medium|loose` per the PRD/ASME); canonicalize per `std`; store the
      canonical one. Document the mapping in the module docstring.
- [ ] `holes.countersink` will pass the angle **explicitly** — build123d's
      `CounterSinkHole` defaults to 82° (an ASME default). Encode the per-
      standard default here (ISO 90°, ASME 82°) so the geometry slice cannot
      inherit the wrong one.
- [ ] `core/tools_holes.py`: `register(registry, service)` adding
      `hole_standards {family?, size?, std?}` via `tools.schema(...)`.
      Registered **unconditionally** (pure data). No `service` seams touched
      yet — do not read `service.branches`/`gate_providers` anywhere.
- [ ] `docs/agent-api.md`: the new tool.

### Tests
`tests/test_hole_standards.py`: **AC3** — `{size: "M5", family: "clearance"}`
returns the three ISO 273 diameters matching published values; one tap-drill
row and one counterbore row likewise; designation strings for all four families
in both symbologies; unknown size / unknown fit / unknown standard raise
`ValueError` naming the argument; the data files parse and every row has the
required keys; `schema == 1`.

### Verification
- [ ] `uv run pytest -q tests/test_hole_standards.py tests/test_toolkit_ocp_free.py` — green; state the count.
- [ ] `uv run python -c "from agentcad.core.tools import build_registry; ..."` — the tool appears in the registry and answers `{"size":"M5","family":"clearance"}`; paste the output.
- [ ] `make test` — baseline + new.

---

## Slice 3 — `toolkit/patterns.py`

### Spike (design risks S3, S4: the per-instance contract)

- [ ] `scratchpad/spike_pattern_guard.py`, through the worker, on
      `construction/gusset_plate`'s real bolt groups and a 50-hole plate:
      (a) confirm a tool placed entirely off the part is a **silent no-op**
      (measured in-process: 1.7 ms, volume unchanged, `is_valid True`);
      (b) time the bbox probe vs the exact `(part & tool).volume` probe per
      instance (measured in-process: 0.015 ms vs 4.2 ms each);
      (c) confirm `&` on disjoint shapes returns an **empty `Compound` with
      `.volume == 0`**, never `None`, never a raise.
- [ ] If (a) turns out to raise on some real part, the guard gets simpler and
      the design's Decision 6 is amended to say so.

### Files
- `agentcad/toolkit/patterns.py` — **new**
- `agentcad/toolkit/__init__.py` — re-export
- `tests/test_patterns.py` — **new**
- `docs/changelog/NNNN-toolkit-patterns.md`

### Tasks
- [ ] Point helpers (pure arithmetic, no geometry): `bolt_circle(r, n,
      start_deg=0.0)`, `grid(nx, ny, dx, dy, center=True)`. Deterministic
      order; documented as usable by `holes.*` **and** by plain
      `Locations(*pts)`.
- [ ] `linear(part, seed, direction, count, spacing, *, verify="bbox")`,
      `polar(part, seed, axis, count, radius=None, span_deg=360.0)`,
      `mirror(part, plane, *, seed=None)`. `seed` is a `Shape`; each returns
      `(part, warning|None)`.
- [ ] **Route through build123d** (`Locations`/`PolarLocations`/`GridLocations`
      + `add`, or `mirror`), inside a `BuildPart` the helper opens itself —
      design Decision 1. `safe_bool` is the fallback rung only, and its warning
      says the result may not be byte-identical to the primary route.
- [ ] The guard, two-tier (design Decision 6): always-on bbox overlap per
      instance + pairwise point-spacing check + whole-operation volume delta;
      `verify="exact"` adds the `&` probe and reports `engaged_mm3`.
      Degenerate spacing (`spacing <= 0`, `count < 1`, span that wraps onto
      itself) is a `ValueError` at the call, not a warning — an impossible
      request is not geometry.
- [ ] Warnings name the instance indices, never a count alone.

### Tests
Deterministic point sets; a pattern whose 3rd instance misses the part warns
and names index 2; overlapping spacing warns; `count=1` is a no-op with a
warning; `verify="exact"` reports per-instance `engaged_mm3`;
`mirror` about `Plane.YZ` doubles the volume of an asymmetric part;
degenerate args raise `ValueError`; and one **identity** test: a polar pattern
of holes equals the hand-written `PolarLocations` form byte-for-byte (the
slice-1 harness).

### Verification
- [ ] `uv run pytest -q tests/test_patterns.py` — green; state the count.
- [ ] Paste the spike's probe-cost table into the changelog.
- [ ] `make test`.

---

## Slice 4 — `toolkit/holes.py`: clearance and tapped, records on the shape

### Spike (design risk S2: does the metadata carrier survive the real pipeline?)

- [ ] `scratchpad/spike_carrier.py`, **through the kernel worker**: a part
      script that attaches an attribute to the shape it returns; then
      (a) `build` → read it back via a throwaway handler;
      (b) build the **same** script again (a `_SHAPE_CACHE` hit) and confirm
      the attribute is still there;
      (c) build 17 other parts, then the first again (LRU eviction → a real
      rebuild) and confirm it is still correct;
      (d) confirm `handle_build`'s tessellation and `_write_lod_tiers` do not
      disturb it.
- [ ] Measured in-process already (design §M3): no `__slots__`, `setattr` works,
      survives `.clean()`/`.moved()`, both `Part` and `TopoDS_Shape` are
      weak-referenceable. **If (b) or (c) fails through the worker**, fall back
      in order: `WeakKeyDictionary` keyed on the shape → the
      `hole_records(p, part)` script contract function (design Decision 4).
      Amend the design spec with which rung you landed on.

### Files
- `agentcad/toolkit/holes.py` — **new**
- `agentcad/toolkit/__init__.py` — re-export
- `tests/test_holes.py` — **new**
- `docs/changelog/NNNN-toolkit-holes-iso.md`

### Tasks
- [ ] `clearance(part, points, size, *, plane="top", fit="medium", std="iso",
      depth=None, thru=True)` and `tapped(part, points, size, *, pitch=None,
      depth=None, thread_class="6H", plane="top", std="iso", thread="none")`.
      Each returns `(part, records, warning|None)` — FR4's exact shape.
- [ ] **`plane` resolution is a predicate, never an ordinal** (design
      Decision 3): a `Plane`; or `"top"|"bottom"|"front"|"back"|"left"|
      "right"` → the extreme planar face along that axis chosen by area,
      re-evaluated every rebuild. Document the tie-break. A `plane` that
      resolves to nothing is a `ValueError` naming the reason.
- [ ] `tapped` bores the **tap drill** and records the thread; it does **not**
      build thread geometry by default. `thread="real"` fuses
      `threads.tapped_hole_thread(...)` and the docstring repeats the
      CHEATSHEET's hard-won rule: bore at `root_radius` for real threads
      (boring at `min_radius` buries the ridges), tap-drill diameter for
      cosmetic. Real threads cost ~9k triangles each — say it in the warning.
- [ ] Records per design Decision 4, one **group** record per call
      (`count`, `positions`) — this is FR3 for free. `id` is stable within a
      build (`h0`, `h1`, …), positions rounded to 9 decimals (the sketcher's
      lesson: never format a coordinate for data with a display formatter).
- [ ] The carrier: `_ATTR` on the returned shape, `_carry()` copying the
      incoming part's records forward, and the never-reset monotonic
      `_created` counter used **only as a delta** by the harvest.
- [ ] FR7 guards: off-face / off-part instances (slice 3's guard, reused),
      `depth` beyond the local stock thickness, and a new-hole position within
      one diameter of an existing **record's** position → warnings naming the
      indices. Impossible geometry (negative depth, unknown size) raises, and
      surfaces as a normal structured `script_error` with `details.line`.

### Tests
Volume math for a known plate/hole set; ISO table values reach the geometry
(a ⌀5.5 medium M5 clearance measures 5.5); `plane="top"` picks the right face
on a part with two candidate planar faces; records carry designation, positions
and count; records survive `safe_fillet` applied after the holes; a raw
build123d op after the last helper **drops** them and the delta check catches it
(the warning text is asserted); off-part and overlapping placements warn; bad
size raises.

### Verification
- [ ] `uv run pytest -q tests/test_holes.py` — green; state the count.
- [ ] Paste the spike's (a)–(d) results into the changelog, including which
      carrier rung landed.
- [ ] `make test`.

---

## Slice 5 — the metadata pipeline

### Spike (design risk S5: what does the extra kernel round-trip cost?)

- [ ] `scratchpad/spike_harvest_cost.py`: time `build` then `hole_records`
      back-to-back on `prototyping/enclosure_base` (0.86 s cold / 0.47 s warm
      per changelog 0079) and on `engine/intake_manifold` (38.57 s cold /
      15.03 s warm — the worst case in the repo). The design predicts
      `hole_records` is nearly free because it hits `_SHAPE_CACHE`.
- [ ] **If it is not** (e.g. LRU eviction between the two calls on a busy
      pool), switch the seam to lazy per the design's stated fallback:
      `get_part` harvests on demand and the rebuild result carries
      `holes: {"deferred": true}`. Do not ship a rebuild that silently doubled.

### Files
- `agentcad/kernel/handlers/holes.py` — **new**
- `agentcad/core/tools_holes.py` — the seam
- `tests/test_hole_metadata.py` — **new**
- `docs/changelog/NNNN-hole-metadata-pipeline.md`

### Tasks
- [ ] Handler pack: `register(toolbox)` exporting `hole_records
      {script, params}` → `{"holes": [...], "warnings": [...]}`. Uses the
      toolbox's `build_shape_ns`; reads the attribute; runs the delta check.
      Raise `WorkerError(ERROR_CONTRACT, ...)` for a malformed record — a
      hand-written record that is not a dict with the required keys is residue,
      not a `KeyError` in the server (the `toolkit/specs.declaration_problem`
      precedent: validate the **shape**, name the offending key).
- [ ] `install_rebuild_holes(service)` in `tools_holes.py`, `_WRAPPED`-marked
      exactly like `install_rebuild_specs`: wrap `service._rebuild` (adds
      `holes` on success, writes the sidecar) and `service.get_part` (reads the
      sidecar). **Key absent** on failure; `null` means "declares none". Never
      raise out of the wrapper — an exception becomes
      `{"status": "error", "error": payload}`.
- [ ] Sidecar `.cache/<key>.holes.json`, `{"version": 1, "cache_key": key,
      "holes": [...], "warnings": [...]}`, atomic write via
      `ProjectStore._atomic_write`, versioned reader that `unlink()`s a corrupt
      or version-mismatched file (the `core/specs.py:434-485` pattern). An
      `OSError` on write is swallowed — an unwritable cache is a slow read, not
      a bug.
- [ ] Read the seam's cross-pack dependencies **inside methods**, and assert
      the wrapper survives a second `build_registry()`.
- [ ] `docs/agent-api.md`: the `holes` key on rebuild results and `get_part`.

### Tests
**AC7** — two consecutive builds of *different* parts on one warm worker do not
cross-contaminate. **AC7b (new, and the one the original AC7 cannot see)** —
building the **same** part twice on one warm worker returns identical records
both times (the `_SHAPE_CACHE`-hit regression). Plus: a service-level cache hit
(`.metrics.json` present, kernel not called) still returns `holes` from the
sidecar; a corrupt sidecar is discarded and re-harvested; a part with no holes
gets `holes: null`; a failed rebuild has no `holes` key at all; the seam is
idempotent across two `build_registry()` calls.

### Verification
- [ ] `uv run pytest -q tests/test_hole_metadata.py` — green; state the count.
- [ ] Paste the spike's cost table (cold/warm, both parts) into the changelog.
- [ ] `make test`.

---

## Slice 6 — drawing callouts from metadata

### Files
- `agentcad/kernel/handlers/drawing.py`
- `tests/test_drawing_holes.py` — **new**
- `docs/changelog/NNNN-drawing-hole-designations.md`

### Tasks
- [ ] The drawing handler already builds the shape — read the records off it
      **in-process**; no second kernel call, no service round trip.
- [ ] Match a record to a detected circle group by diameter (existing 0.05 mm
      tolerance) **and** centre proximity in the top view; on a match print the
      record's designation and set `from_metadata: true` on that group; on no
      match keep today's geometric text and `from_metadata: false`.
- [ ] **AC5**: a group record with `count: n` renders `n× <designation>` — and
      note that today's `_detect_circles` only emits groups with `count >= 3`,
      so a record with `count < 3` is rendered from metadata **without**
      needing the detector's group. Make that explicit rather than inheriting
      the threshold by accident.
- [ ] Document the inherited limitation in the handler docstring and in
      `docs/agent-api.md`: **top view only**. A hole on a side face has a
      record and no callout. That is PRD-014's job.

### Tests
**AC2** — a tapped M5×0.8 ×12 hole yields SVG text containing the ISO
designation and `detected.hole_groups[0].from_metadata is True`; a hand-cut
`Hole()` with no record keeps the geometric text and `from_metadata is False`;
**AC5** — a polar pattern of one tapped hole renders `8× M5×0.8 - 6H ↧12`;
a record whose diameter does not match any detected circle produces a warning
naming it (a record that cannot be drawn is not silently dropped).

### Verification
- [ ] `uv run pytest -q tests/test_drawing_holes.py` — green; state the count.
- [ ] Generate one drawing for the fixture part and **look at the SVG**;
      attach the callout line to the changelog.
- [ ] `make test`.

---

## Slice 7 — AC1: rewrite the construction example

Gated on slice 1's golden. This slice edits **committed** example scripts —
the one place this plan does.

### Files
- `examples/construction/parts/gusset_plate.py`, `base_plate.py`,
  `angle_bracket.py`
- `tests/test_examples_golden.py`
- `docs/changelog/NNNN-construction-example-helpers.md`

### Tasks
- [ ] Rewrite `gusset_plate.py:96-130`'s two hand-rolled loops and
      `angle_bracket.py:59-64`'s pairs with `patterns.*` + `holes.*`. Keep the
      params and the part's meaning identical.
- [ ] Run slice 1's golden **before** committing the rewrite. It must pass
      unchanged on metrics and on the `.acm` sha.
- [ ] **If the sha moves**: do not adjust the golden. Report the exact
      difference (metrics identical? face count identical? which face order
      changed?), record it in the changelog, and land restated-AC1 (b) in its
      degraded form — "the mesh differs only in face ordering" — with the
      evidence. Silently re-baselining a golden is the failure mode this slice
      exists to prevent.
- [ ] Note in the changelog that the **cache key necessarily changes** (the key
      hashes the script text) — that half of the PRD's AC1 was never
      achievable, per the design spec.

### Verification
- [ ] `uv run pytest -q tests/test_examples_golden.py tests/test_examples.py -k construction` — green; state the count.
- [ ] `uv run agentcad check --project examples/construction` (or the
      equivalent geometry-CI invocation) — green; paste the summary.
- [ ] `make test`.

---

## Slice 8 — counterbore, countersink, ANSI

### Files
- `agentcad/toolkit/data/ansi_clearance.json`, `ansi_thread.json`,
  `ansi_cbore_csk.json` — **new**
- `agentcad/toolkit/hole_standards.py`, `agentcad/toolkit/holes.py`
- `tests/test_hole_standards.py`, `tests/test_holes.py`
- `docs/changelog/NNNN-holes-cbore-csk-ansi.md`

### Tasks
- [ ] ANSI/ASME tables under the same two-source provenance rule, including
      number/letter/fraction drill designations, which are part of the
      designation string, not a rounding of a millimetre value.
- [ ] `counterbore(part, points, size, *, fastener="iso4762", ...)` and
      `countersink(part, points, size, *, angle=None, ...)` over build123d's
      `CounterBoreHole` / `CounterSinkHole`. **Pass the angle explicitly**
      (ISO 90°, ASME 82°; the build123d default is 82).
- [ ] Records gain `cbore {d, depth}` / `csk {d, angle}`; designations follow
      the per-standard grammar from slice 2.

### Tests
Published-value spot checks for ANSI rows; the countersink angle default is
per-standard, not per-build123d; a counterbore deeper than the stock warns;
designation strings in both symbologies; drawings render a counterbore callout
(extends slice 6's tests).

### Verification
- [ ] `uv run pytest -q tests/test_hole_standards.py tests/test_holes.py tests/test_drawing_holes.py` — green; state the count.
- [ ] `make test`.

---

## Slice 9 — `features.rib` and `features.boss`

### Spike (design risk S7: the trim step)

- [ ] `scratchpad/spike_rib.py`, through the worker: build a rib on
      `prototyping/enclosure_base`'s floor and on a plain plate, in both
      `to=` modes (explicit depth; `to="part"` extend-and-intersect). Measure:
      does the result contain **one** valid solid, what does it cost, and does
      `to="part"` add material outside the part's envelope? Print the volume
      delta against a hand-built rib.
- [ ] If `to="part"` is unreliable, ship `to=<depth>` only and say so — the
      design already names that as the MVP.

### Files
- `agentcad/toolkit/features.py` — **new**
- `agentcad/toolkit/__init__.py`
- `tests/test_features.py` — **new**
- `docs/changelog/NNNN-toolkit-rib-boss.md`

### Tasks
- [ ] `rib(part, profile, thickness, *, to=..., draft_deg=None)` per design
      Decision 7: thicken the profile, extrude, trim, `safe_bool` fuse. Returns
      `(part, warning|None)`; the warning names which trim mode was used.
- [ ] `boss(part, at, d, h, *, hole=None, draft_deg=None)` — cylinder fused at
      a point, `hole="M3"` bores the tap drill through `holes.tapped` and
      **records it** (so a screw boss shows up in the metadata and the
      drawing).
- [ ] Rib draft is a **tapered extrusion**, not a call to the draft operation
      (design Decision 7 — a shelled part caps at 2°, so drafting a finished
      shelled part would fail where a tapered extrusion cannot).

### Tests
Rib volume against hand-built geometry; a rib that misses the part warns rather
than silently adding a floating solid; a boss with `hole="M3"` produces a
record; both leave one valid solid; `draft_deg` changes the volume in the right
direction.

### Verification
- [ ] `uv run pytest -q tests/test_features.py` — green; state the count.
- [ ] Paste the spike's trim-mode table into the changelog.
- [ ] `make test`.

---

## Slice 10 — `features.draft`

### Spike (design risk S6: is failure monotone in the angle?)

- [ ] `scratchpad/spike_draft_sweep.py`, through the worker, on **≥ 4 bundled
      example parts** (`prototyping/enclosure_base`, `rocketry/nozzle`,
      `construction/angle_bracket`, `construction/gusset_plate`) plus the four
      synthetic shapes from the design's §M6: sweep 0.5 → 60° at fine steps and
      print the ok/fail pattern per part.
- [ ] The design measured **monotone on all four synthetic shapes**
      (box 30°→45° boundary; box+fillets 10→15; box+boss 15→20; **shelled box
      2→3**). Binary search is only valid if that holds. **If an island
      appears**, replace the search with a descending linear sweep at a
      documented step and amend design Decision 8.
- [ ] Also confirm the failure raises `Standard_Failure` with an **empty
      message** — the warning text has to be written entirely from our own
      measurements, and the Error Doctor needs the pattern.

### Files
- `agentcad/toolkit/features.py`
- `agentcad/kernel/error_doctor.py`
- `tests/test_features.py`
- `docs/changelog/NNNN-toolkit-draft.md`

### Tasks
- [ ] `draft(part, faces, angle_deg, neutral_plane, *, min_angle=0.25,
      rel_tol=0.02)` over build123d's `draft(faces, neutral_plane, angle)`,
      binary-searching **down** on failure exactly as `safe_fillet` does.
      Returns `(part, achieved_deg, warning|None)`.
- [ ] `faces` is a **selector or a list of `Face` objects**, never indices
      (design Decision 3).
- [ ] The warning names the achieved angle and the face count — because OCCT
      says nothing. Add an Error Doctor hint for the empty-message
      `Standard_Failure` out of `BRepOffsetAPI_DraftAngle`: "a draft angle too
      large for this geometry; `features.draft` searches down automatically".
- [ ] Document the measured ceilings in the docstring and in
      `docs/part-authoring.md`: **a shelled enclosure caps around 2°** — the
      fallback is the feature, not a consolation.

### Tests
A plain box drafts at 10°; a shelled box asked for 5° comes back at the
measured achievable angle with a warning naming it; a request below
`min_angle` that still fails returns the part unchanged with a warning; the
achieved angle is monotone-consistent with the spike's table for one fixture.

### Verification
- [ ] `uv run pytest -q tests/test_features.py` — green; state the count.
- [ ] Paste the full monotonicity sweep into the changelog — it is the
      evidence the binary search rests on.
- [ ] `make test`.

---

## Slice 11 — sheet-metal v2a: partial flanges, relief, outline-from-unfold

### Spike (design risk S9: does the fold/unfold model survive partial tabs?)

- [ ] `scratchpad/spike_sheet_v2.py`: build the AC4 bracket (a base plate with
      one **partial-width** flange) three ways and measure —
      (a) `fold()` is one valid solid;
      (b) `unfold()` volume equals `fold()` volume within the bend-allowance
      model's own tolerance (state the tolerance and where it comes from);
      (c) the outline derived from `unfold()`'s top face (measured 1.2 ms on a
      two-flange bracket) is closed, CCW, and its area equals the top-face
      area;
      (d) relief cuts appear in **both** fold and unfold.
- [ ] Also measure the relief's effect on `fold()` validity at small
      thicknesses — a relief narrower than the mesh tolerance is a sliver.

### Files
- `agentcad/toolkit/sheetmetal.py`
- `tests/test_sheetmetal.py`, `tests/test_sheetmetal_v2.py` — **new**
- `docs/changelog/NNNN-sheetmetal-partial-flanges-relief.md`

### Tasks
- [ ] `_Flange` gains `start`, `width`, `relief`; per-edge uniqueness becomes a
      **non-overlap** rule over `[start, start+width)`. `width=None` keeps v1's
      full-edge meaning — **the v1 tests are the gate and must pass unchanged
      and byte-identical**.
- [ ] Relief kinds `rect | round | tear` from one shared computation applied to
      **both** `fold()` and `unfold()`; `relief="auto"` picks `rect`; explicit
      `{"kind","width","depth"}` overrides. The sizing rule is a named constant
      with the rule written beside it, documented as **a shop default, not a
      standard**. `tear` removes no material and the warning says so.
- [ ] `flat_outline()` is **derived from `unfold()`'s top face** (design
      Decision 9): same signature and return type, now a discretization at a
      documented chord tolerance. Add `flat_outline_edges()` for the exact
      segments/arcs.
- [ ] `bend_lines()` spans `[start, start+width)`, not the whole edge.

### Tests
The v1 corpus unchanged (byte-identical `fold()`); property tests on the
outline (closed, CCW, area == top-face area); two non-overlapping flanges on
one edge; an overlapping pair raises; **AC4** — the bracket's `fold()` is
valid, `unfold()` round-trips with relief cuts, `bend_lines()` are correct, and
the `flat_pattern` export produces an SVG.

### Verification
- [ ] `uv run pytest -q tests/test_sheetmetal.py tests/test_sheetmetal_v2.py` — green; state the count.
- [ ] **Look at the AC4 SVG** (AC4 requires one visually verified export) and
      attach it / describe the relief cuts in the changelog.
- [ ] Paste the spike's (a)–(d) numbers.
- [ ] `make test`.

---

## Slice 12 — sheet-metal v2b: hems and corner treatments

### Spike (design risk S8: is a hem representable?)

- [ ] `scratchpad/spike_hem.py`: at 180°, sweep `inner_radius` from `t` down to
      `0.01·t` and find the **minimum radius at which `fold()` still yields one
      valid solid**. Then attempt a **teardrop** (>180°) profile in the current
      sector-plus-tangent-leaf model and record whether it self-intersects.
- [ ] The design gates `teardrop` on this. If it cannot be built, `kind=
      "teardrop"` **raises a `ValueError` naming the reason** — it is not
      approximated as a closed hem.

### Files
- `agentcad/toolkit/sheetmetal.py`
- `tests/test_sheetmetal_v2.py`
- `docs/changelog/NNNN-sheetmetal-hems-corners.md`

### Tasks
- [ ] Relax the `(0, 180)` exclusive rule to admit exactly 180° for hems, at
      the **measured** minimum inner radius, with a docstring that says a true
      zero-radius closed hem is not representable in a B-rep and the model
      shows `R = <measured>`.
- [ ] `hem(edge, kind="open"|"closed", length, start=0.0, width=None)`.
- [ ] `corner(edge_a, edge_b, treatment="close"|"gap"|"rip")`; `close` mitres
      the two leaves (a boolean with real failure risk → `sp.warnings` records
      any `safe_bool` fallback, as v1 already does).
- [ ] Hems and corners appear in `unfold()` and in the derived outline by
      construction (slice 11 made that structural).

### Tests
An open hem folds and unfolds consistently; a closed hem at the measured
minimum radius is one valid solid; `teardrop` raises with the recorded reason
(or ships, if the spike said it can); the three corner treatments differ in
volume in the expected direction; the outline properties still hold.

### Verification
- [ ] `uv run pytest -q tests/test_sheetmetal_v2.py` — green; state the count.
- [ ] Paste the hem radius sweep into the changelog.
- [ ] `make test`.

---

## Slice 13 — UI: hole-on-face on the existing face card

**PRD-016 is unbuilt; the host already exists** (`frontend/js/main.js:1420-1653`
— face selection keyed to the mesh cache key, `face_info`, the face card,
push/pull Apply). This slice extends that card. The pattern dialog stays
deferred to PRD-016 (design Decision 12).

### Files
- `agentcad/core/tools_holes.py` — the `add_holes` script-edit tool
- `frontend/js/main.js`, `frontend/js/api.js`, `frontend/css/app.css`
- `tests/test_tools_holes.py` — **new**
- `docs/changelog/NNNN-hole-on-face-ui.md`

### Tasks
- [ ] `add_holes {project, part_id, plane|face_index, points, family, size,
      fit?, depth?, std?}` following `tools_facemod.push_pull` **exactly**:
      validate (script part only, planar face only), append a **marked,
      counter-suffixed** block that rebinds `build`, call
      `service.update_part`, return `{**with_hint(result), …echoes}`.
- [ ] When the caller passes a `face_index`, resolve it **at call time** via
      the existing `sketch_plane` handler and emit a literal
      `Plane(origin=…, x_dir=…, z_dir=…)` into the script, with the
      renumbering caveat as an inline comment — design Decision 3, and the
      `sketch_plane` emitted-header precedent. **Validate `face_index` and any
      caller-supplied identifier before it reaches generated source** (a
      crafted `part` name put `import os` on line 2 of a generated script once
      — PRD-009's last gotcha).
- [ ] Face card gains family / standard / size / fit-or-depth controls and an
      Apply that calls `add_holes`; the appended line is visible in the editor
      and the normal rebuild events fire.
- [ ] Sizes in the picker come from `hole_standards` — never a hard-coded list
      in JS.

### Tests
`tests/test_tools_holes.py`: the appended block is syntactically valid and
rebuilds; two consecutive calls do not shadow each other (the counter);
a non-planar face is a `ValidationError`; a crafted identifier is refused;
the emitted plane matches `sketch_plane`'s basis.

### Verification
- [ ] `uv run pytest -q tests/test_tools_holes.py` — green; state the count.
- [ ] **Real browser** via the `run` skill: open `prototyping/enclosure_base`,
      click the top face, add 4 × M3 clearance holes, screenshot the result and
      the appended script line. **Zero console errors.**
- [ ] `make test`.

---

## Slice 14 — docs, cheat-sheet, acceptance tests, close-out

### Files
- `agentcad/core/templates.py` (CHEATSHEET)
- `docs/part-authoring.md`, `docs/agent-api.md`, `docs/user-guide.md`,
  `docs/roadmap.md`
- `docs/prd/in-progress/PRD-010-feature-toolkit-ii.md` → `docs/prd/completed/`
- `AGENTS.md` — a **PRD-010 gotchas** section
- `tests/test_prd010_acceptance.py` — **new**
- `docs/changelog/NNNN-prd-010-completed.md`

### Tasks
- [ ] CHEATSHEET sections for `patterns`, `holes`, `features`, and the extended
      sheet metal — matching the density of the existing sections, and
      carrying the measured facts an author needs: draft's real ceilings, the
      tap-drill vs root-radius rule, the top-view-only drawing limitation, the
      face-plane caveat.
- [ ] `docs/part-authoring.md`: a section per module under
      "The part-authoring toolkit", plus the designation symbology table.
- [ ] `AGENTS.md` gotchas, one line each, every one traceable to a changelog:
      a misplaced OCCT cut is a **silent no-op**; `&` on disjoint shapes is an
      empty `Compound` with `.volume == 0`; **hole records ride the shape, not
      a registry, because `_SHAPE_CACHE` skips `build(p)`**; the cache key
      hashes the **script text**, so any rewrite mints a new key; draft fails
      **monotonically** and caps at ~2° on a shelled part with an **empty**
      OCCT message; `flat_outline()` is derived from `unfold()`, not a parallel
      model; `hole_standards.py` is the third OCP-free toolkit module;
      `tools_holes` loads at `h`, before every seam-owner it touches.
- [ ] `tests/test_prd010_acceptance.py`: **AC1–AC8** (with restated AC1 and the
      added AC7b), each named after its criterion and each asserting the thing
      the criterion actually says. **AC8** re-states the suite count and that
      `examples/` is untouched except by slice 7's deliberate rewrite
      (`_fingerprint` on a copy, the PRD-004 pattern).
- [ ] `docs/roadmap.md`: PRD-010 → completed; move the PRD file and update the
      link (the roadmap currently points at `prd/pending/`).

### Verification
- [ ] `uv run pytest -q tests/test_prd010_acceptance.py` — green; state the count and name each AC.
- [ ] `make test` — full suite; state the final count against slice 1's
      re-measured baseline; no unexplained skips.
- [ ] One real-browser pass over the hole card (screenshot, zero console
      errors).

---

## Notes for whoever executes this

- **The four PRD corrections are load-bearing, not editorial.** AC1's cache-key
  half is impossible; FR6's registry is wrong under the shape cache; FR14's
  host is unbuilt; FR11's teardrop may be unrepresentable. All four are
  restated in the design spec's "What the PRD asks for that cannot be delivered
  as written". If you find yourself implementing the PRD's literal words for
  any of them, stop.
- **A spike that contradicts the design is a success.** Amend the spec, record
  the measurement in the changelog, and continue. The PRD-008 face-anchor
  experience — an unmeasured assumption about OCCT behaviour costing three
  review rounds — is why every risky slice opens with one.
- **Slices 2, 3, 4 are independent** and can run in parallel by different
  agents (2 touches no geometry, 3 touches no metadata, 4 depends on 2's
  tables only). Slices 5–7 are strictly sequential. 8–13 are independent of
  each other.
