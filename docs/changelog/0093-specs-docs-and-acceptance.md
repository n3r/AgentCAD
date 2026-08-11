# 0093 — rocketry specs, PRD-003 acceptance tests, docs and close-out

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary
PRD-003 Slice 7, the close-out: `examples/rocketry` now ships real design
intent (a nozzle wall minimum and mass budget, a flange bolt-circle ligament,
and a root `specs.py` with the assembly gaps), one named acceptance test per
criterion (AC1–AC9) walking the real stack, and every documentation surface
brought up to the shipped feature — `docs/agent-api.md`,
`docs/part-authoring.md`, `docs/user-guide.md`, `docs/architecture.md`,
`AGENTS.md`, `README.md`, the `CHEATSHEET` in `core/templates.py` and the
example's own README — with the tool count recounted from a live
`build_registry`. The PRD's `Status:` is now *implemented* with an AC → test
table and sixteen folded-back divergences; it stays in `docs/prd/in-progress/`
(the orchestrator moves it and updates the roadmap row when the branch lands).

**No production code changed in this slice.** The only non-documentation edits
are the example scripts, the `CHEATSHEET` string and the new test module.

## Changes

### The rocketry example ships specs (AC1)
- **`parts/nozzle.py`** — `SPECS = [check_valid, check_wall(min_mm=0.8,
  grid=4), check_mass(max_g=1200.0), check_that(... "fits_fairing")]`, tagged
  `ENG-014` (wall) and `SYS-042` (mass/envelope). The mass budget is picked
  from the shipped default's real `metrics.mass_g` — **1078.1 g** in inconel718
  — with ~11 % headroom, and it stays green as the wall thins (929 g at
  `wall = 2.6`, 709 g at 2.0).
- **`parts/flange.py`** — one `check_wall` named `bolt_circle_ligament`
  (`min_mm=2.0`, `grid=4`, `INT-003`). At the shipped bolt circle the thinnest
  sampled point *is* the 6.5 mm rim ligament (`outer_r - bc_r - bolt_r`), and
  crowding the circle outward (`bolt_circle_d = 130`) drops it to 0.97 mm and
  turns the check red. The build already clamps `bolt_circle_d`; the spec
  measures the result rather than trusting the clamp.
- **New `examples/rocketry/specs.py`** — `check_interference_free` plus the two
  gasket gaps as named clearances: `flange_bore_gap`
  (`flange_1`↔`nozzle_1`, `min_mm=0.3`, measured 0.500 mm as shipped) and
  `injector_gasket_gap` (`injector_plate_1`↔`nozzle_1`, `min_mm=0.15`,
  measured 0.200 mm), all `INT-003`.
- **`parts/injector_plate.py` deliberately declares nothing** — the example
  therefore also demonstrates the spec-less path (no chips, zero added kernel
  work) on a real project.
- `run_specs` on the shipped defaults: **green, 8 checks**, three requirements
  (`ENG-014`, `SYS-042`, `INT-003`) all passing. Cold **0.60 s**, warm
  **0.00 s** (every tier-1 row served from the `.specs.json` sidecars).

### `check_wall` on the shipped geometry — the measurement that shaped the specs
The plan asked for `check_wall(min_mm=2.5)` on a 3.0 mm nozzle wall. That is
not what the check measures. `_min_wall` samples a `grid × grid` UV grid per
face and casts along the inward face normal, so on this part it finds the
**chamfered exit lip** (`0.2 * wall` by construction), not the barrel. Measured
minimum wall (mm) on the shipped nozzle, by `grid` and `wall`:

| `wall` | grid 4 | grid 6 | grid 8 (default) | grid 12 | grid 16 |
|---|---|---|---|---|---|
| 3.0 | **1.020** | 0.857 | 1.259 | 0.321 | 0.269 |
| 2.8 | **0.960** | 0.808 | 0.017 | 0.067 | 0.354 |
| 2.6 | **0.898** | 0.757 | 0.103 | 0.153 | 0.439 |
| 2.5 | **0.867** | 0.732 | 0.146 | 0.196 | 0.036 |
| 2.2 | **0.772** | 0.653 | 0.276 | 0.315 | 0.166 |
| 2.0 | **0.707** | 0.599 | 0.362 | 0.314 | 0.253 |
| 1.5 | **0.540** | 0.053 | 0.419 | 0.378 | 0.041 |
| 1.0 | **0.367** | 0.314 | 0.287 | 0.260 | 0.246 |

At the default `grid=8` the number is **not monotone in `wall`** (1.259 at 3.0,
0.017 at 2.8, 0.362 at 2.0): the sampler either lands on the lip chamfer or it
does not, and a threshold separating "3.0 mm is fine" from "2.0 mm is not"
cannot exist there. At `grid=4` the measurement is monotone across the whole
range and tracks `wall` almost linearly (≈ 0.34 × `wall`), so the shipped
declaration is `check_wall(min_mm=0.8, grid=4)`: green at 3.0 (1.020) and at
2.6 (0.898, the `wall` the PRD-002 acceptance suite thins to), red at 2.0
(0.707). 0.8 mm sampled ≈ a 2.35 mm barrel wall.

**This is a real property of the shipped measurement, not a workaround**, and
it is now documented in three places (`docs/part-authoring.md`'s gotcha,
`AGENTS.md`'s spec gotchas, the example's own README and the script comment):
the wall check is a sampled ray cast, the measured minimum is not the nominal
wall, **pick the limit from a measurement and pin `grid`**. A medial-axis wall
measurement is a different measurement and therefore a different PRD.

The flange is the mirror image: at `grid=4` it measures the true 6.5 mm
bolt-circle ligament, and only a finer grid drifts onto the bore/rim chamfers
(0.44 mm at 8, 0.02 mm at 16). Same rule, same knob.

### Acceptance tests — `tests/test_prd003_acceptance.py` (new, 9 tests)
One named test per criterion with the `| AC | Test |` table in the module
docstring, mirroring `tests/test_prd002_acceptance.py`. The module carries
`integration` + `portability` and skips without git; the geometry cases are
`slow`; the two rocketry cases run **on a copy** and are skipped when the
example is absent.

- `test_ac1_rocketry_ships_green_specs_and_thinning_turns_red` — green as
  shipped (all six ids asserted individually, all three requirements `pass`,
  the spec-less `injector_plate` absent from `report["parts"]`), then
  `set_params {"wall": 2.0}` → red with **exactly one** failing id,
  `nozzle:wall_min`, carrying `kind`, `requirement`, `measured < limit.min_mm`,
  `unit` and a three-component `location`; the mass budget stays green.
- `test_ac2_failing_spec_still_lands_geometry` — `ok: true`,
  `volume_mm3 > 0`, `ensure_mesh(...).is_file()`, the failure in the
  post-state's `specs`, and the same verdict on `get_part` (the chip path).
- `test_ac3_fem_check_skips_without_extra_and_evaluates_with_it` — one test,
  both halves: the skip half is *forced* (`_fem_available → False`) so it runs
  on every machine and asserts `reason: "fem_extra_missing"`, a hint, and a
  report that is **green** with a skip in it; the evaluation half runs only
  when the solver stack imports. Verified green in both configurations.
- `test_ac4_project_specs_measure_clearance_and_name_interference` —
  `set_project_specs` writes the file and returns declarations; a 0.4 mm gap is
  reported as `measured` against a 1.0 mm limit (a number, not just a verdict),
  and the overlapping pair is named in `details.pairs`.
- `test_ac5_raising_predicate_is_an_error_not_a_crash` — `status: "error"` with
  `details.traceback`, `ok: true`, the sibling `check_valid` still `pass`, the
  report red with `{"passed": 1, "errors": 1}`, and the worker still answering.
- `test_ac6_requirements_group_and_list_specs_does_not_build` — zero `build`
  and zero `spec_eval` calls under the counting monkeypatch (one
  `spec_declare`, for the one declaring part), and the declared requirement map
  asserted **equal** to the evaluated one.
- `test_ac7_evaluate_specs_green_for_a_good_branch_red_for_a_broken_one` —
  green for the default branch, red for a branch whose `wall` was thinned, each
  verdict carrying the head it measured; reading another ref leaves the caller
  on their own branch; and a **tag** ref raises a `ValidationError` (the
  reworded half — see Notes).
- `test_ac8_spec_chips_verified_in_browser` — the evidence check over
  `docs/changelog/0092-spec-chips-ui.md` (phrases, the clean-console line, the
  `spec-fail`/`spec-skip` classes), not a re-driven browser.
- `test_ac9_specless_parts_add_no_kernel_work` — `service._status.clear()` to
  force the full cache-key path, then zero `spec_declare` / `spec_eval` /
  `clearance` calls across a rebuild, a `run_specs` (`status: "skip"`, no
  parts) and a `get_part`; the rebuild payload is `specs: null`.

### Documentation
- **`docs/agent-api.md`** — a new **Design specs** section beside Drawings and
  analysis: the five rules (a failing spec is data; the four statuses and what
  separates them; a rebuild runs the shape tier only; requirement strings are
  opaque; results ride the existing content hash), the four tools with their
  full payload shapes, the rebuild/`get_part` `specs` key, the fail-closed gate
  (including that `allow_invalid` does not waive it), and the four routes. The
  header count is **64 tools (67 with the `[fem]` extra)**, recounted from a
  live `build_registry` (was 60/63). The `proposal_get` gate list no longer
  says `specs` is a placeholder, the `allow_invalid` paragraph names the specs
  gate, and **the worked loop now writes a spec and iterates to green** —
  `set_params` → `specs` → adjust → `run_specs`.
- **`docs/part-authoring.md`** — a `## Design specs (SPECS)` section: the ten
  constructors in two tables (part vs project scope), the `name`/`requirement`
  conventions, part-scope-vs-project-scope guidance, eager validation as *the*
  error contract, what a rebuild evaluates, and the two gotchas that cost real
  time — the sampled-ray-cast wall caveat with the rocketry numbers, and
  `check_fem_static`'s `{"axis", "side"}` face selectors with its
  at-least-one-limit rule. The intro now names `SPECS` as the third optional
  extension.
- **`docs/user-guide.md`** — the chip strip in the Parameters pane (four
  states, the tooltip's content, the live green → red → green flip, and that a
  spec-less part shows nothing at all), where project specs live and how to
  edit them, the `specs` gate in the proposals Checks tab and its fail-closed
  rule, and a rocketry line in the bundled-examples tour.
- **`docs/architecture.md`** — a `## Design specs (executable intent)` section
  (three components across the process boundaries, the three tiers, the
  `ast`-only presence scan, both cache sidecars, the appended gate provider,
  and the trust statement), plus component-table rows for
  `agentcad/core/specs.py`, the `specs` handler pack, the toolkit module and
  the tool/route packs; the process diagram's tool count; and a trust-model
  paragraph stating that `SPECS`, `specs.py` and every predicate execute in the
  confined worker and never in the server process.
- **`AGENTS.md`** — a `## Spec gotchas (PRD-003)` section (eleven traps: specs
  are code in the tree, a failing spec never fails a rebuild, the four statuses,
  `SPECS` is in the cache key, "declares nothing" ≠ "not evaluated", the
  fail-closed gate, the three live name collisions, the `_min_wall` sampling
  caveat, the load-order rules, and why the rebuild seam is a wrapper), and
  `SPECS` added to the part-script contract summary.
- **`README.md`** — an "Executable design specs" bullet in the v4 list, the
  recounted tool surface, and the rocketry example line.
- **`agentcad/core/templates.py`** — the `CHEATSHEET` gains an
  `Optional: SPECS = [...]` line in the numbered contract block and a
  `DESIGN SPECS` section (constructors by scope, the requirement/name
  convention, eager validation, the tier rule and the `check_wall` caveat).
  `DEFAULT_PART_SCRIPT` is deliberately **unchanged** — every new part would
  otherwise be born carrying a spec.
- **`examples/rocketry/README.md`** — what each file declares, the green → red
  demo, the `grid` caveat, and a paragraph on the spec as the agent loop's
  termination condition.

### PRD close-out
`docs/prd/in-progress/PRD-003-design-specs-executable.md`: `Status:` →
*implemented — AC1–AC9 verified*, a **Verification (slice 7)** section with the
AC → proving-test table and the browser-evidence note, and an **As built**
section folding back **sixteen** divergences — the design spec's eleven plus
five discovered while building (a spec-less part is absent and `specs: null`;
`declaration_error` rather than `error`; the FEM face-selector shape; AC1's
wall limit expressed in measured terms; the packet's missing `specs.py` row).
The file stays in `docs/prd/in-progress/` and the roadmap row stays
`in progress` — moving both is the merge's job, per the PRD-002 convention.

## Files
- `examples/rocketry/parts/nozzle.py` — the `agentcad.toolkit.specs` import and
  a four-check `SPECS` block with the measurement rationale in comments
- `examples/rocketry/parts/flange.py` — the import and the
  `bolt_circle_ligament` check
- `examples/rocketry/specs.py` — **new**: the project-scope specs
- `examples/rocketry/README.md` — the "Design specs" section and the agent-loop
  paragraph
- `tests/test_prd003_acceptance.py` — **new**: 9 acceptance tests
- `docs/agent-api.md`, `docs/part-authoring.md`, `docs/user-guide.md`,
  `docs/architecture.md`, `AGENTS.md`, `README.md` — the documentation surfaces
- `agentcad/core/templates.py` — the `CHEATSHEET` SPECS section
- `docs/prd/in-progress/PRD-003-design-specs-executable.md` — status,
  verification, as-built divergences
- `docs/changelog/0093-specs-docs-and-acceptance.md` — this entry

## Verification

- `uv run pytest -q tests/test_prd003_acceptance.py` → **9 passed** (10.7 s;
  slow cases included). The `[fem]` extra is installed here, so AC3's second
  half really ran; re-run with `fem_available` masked to `False` by a scratch
  pytest plugin (the machine-without-the-extra configuration) → **9 passed**
  again, which is AC3's actual claim.
- `uv run pytest -q tests/test_examples.py -k rocketry` → **4 passed**,
  **unedited**: the example carries specs and its three contracts (build valid
  at defaults, build at every param extreme, interference clean) are untouched.
  The suite uses `make_test_service`, which builds no registry, so spec
  evaluation does not run there at all — adding `SPECS` can only affect it as
  script text, which is exactly the guarantee the run proves.
- `uv run pytest -q tests/test_prd002_acceptance.py` → **11 passed**,
  **unedited** — the load-bearing one: PRD-002's AC1 thins the rocketry nozzle
  to `wall = 2.6` and *merges* it through the routes, so the shipped specs must
  stay green there. They do, which is why the wall limit is 0.8 and not the
  0.85 that `wall = 2.5` would measure.
- `make test` → **880 passed, 1 skipped** against a baseline of 871 passed, 1
  skipped — exactly the 9 new tests, with **zero edits to any pre-existing test
  file** (`git status`: the only test-tree change is the new
  `tests/test_prd003_acceptance.py`).
- `make test-fast` → **718 passed, 1 skipped** (717 + 1 before this slice: the
  evidence-only AC8 test is the one new case that is neither `slow` nor git-
  gated).
- `make test-portability` → **424 passed** (5:29), the group the new module
  joins with its `portability` marker.

## Notes

- **AC7's tag half is not implementable as the PRD words it**, and the test
  says so instead of pretending. `git rev-parse` searches tags before branches
  (PRD-001's X1), so every spec ref is resolved with `history.resolve_branch`
  and a tag is a `validation_error` — a tag must never answer for a branch.
  Slice 5 already shipped and tested that; the acceptance test asserts green
  for a good *branch*, red for a broken one, and that the tag raises. Recorded
  in the PRD's Verification section as well as here.
- **There is no `evaluate_specs` tool** and the docs never promise one. FR11 is
  a service seam consumed by the gate; `run_specs {ref}` is the agent-facing
  way to ask the same question. `docs/agent-api.md` lists exactly the four
  tools that exist.
- **`tests/test_examples.py` did not need the plan's contemplated edit.** The
  plan warned that its min/max sweep would run "with rocketry specs declared";
  it uses `make_test_service` (no registry ⇒ no rebuild seam), so no spec is
  ever evaluated there and the sweep is unaffected. The specs are therefore not
  required to be green at every param extreme — and could not be: a mass budget
  that survives `chamber_l = 300` would not be a budget.
- **Two example parts pin `grid=4`.** That is a *stability* choice, not a cost
  choice, and it is the first time this repo has had to make one: at the
  default grid the nozzle's measured minimum is dominated by whether a sample
  lands on the lip chamfer. A coarser grid samples less, so it can also miss a
  genuinely thin feature — the honest framing, now in the docs, is that the
  grid is part of the declaration and changing it changes the number.
- **The rocketry scripts are no longer plain-build123d portable.** Declaring
  `SPECS` means `from agentcad.toolkit.specs import …`, which needs the
  `agentcad` package on the path — the same trade `docs/part-authoring.md`
  already documents for the authoring toolkit, now taken by a bundled example.
  `injector_plate.py` is untouched and still runs anywhere build123d does.
- **Known gap, unchanged from slice 5:** a proposal that weakens a spec still
  produces no packet row; `details.specs_py_changed` flags it. A `specs`
  section in the packet touches `packet.py` and stays a separate slice.
- **Phase 2 remains Phase 2**: the requirement-grouped project Specs panel and
  the viewport thin-point marker are deliberately absent, as the PRD's own MVP
  section says.
