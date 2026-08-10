# 0089 — SpecRunner: tiers, the result sidecar, the report and requirement grouping

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Claude

## Summary
PRD-003 Slice 3: `agentcad/core/specs.py` — the service-side orchestration
between the pure-data declarations (Slice 1) and the kernel primitives
(Slice 2). It partitions declared checks into three tiers, pays for each need
once, caches tier-1 results in a `.cache/<cache_key>.specs.json` sidecar beside
the existing `.metrics.json`, and returns one report with per-check records,
per-part blocks, per-requirement grouping and summary counts. Nothing imports
it yet — Slice 4's tool/route packs and the rebuild seam do.

## Changes
- **`declares_specs(script)`** — an `ast.parse` presence scan for a
  module-level binding of `SPECS`, memoized by `sha256(script)`. It recurses
  into `if`/`for`/`while`/`try`/`with` (a conditionally or loop-built `SPECS`
  still binds the name) and deliberately not into a function or class body (that
  binding is local). A `SyntaxError` is `False`. **Never exec** — this is the
  mechanism that makes a spec-less part cost nothing (FR5/AC9), and executing to
  find out would defeat it.
- **Pure report helpers** — `summarize` (passed/failed/skipped/errors/total),
  `report_status` (`green`/`red`/`skip`; skips never make a report red, and
  `skip` means nothing was declared at all), `group_requirements` (FR12: a
  requirement fails if any of its checks failed *or errored*, passes with at
  least one pass and no failure, skips when all skipped; a requirement with zero
  checks does not exist), and `assign_ids` (`<part>:<name>` / `project:<name>`,
  with `#2`, `#3` … and a `warnings` entry for a duplicate name inside one
  scope — never a silently merged row).
- **`SpecRunner.declarations(proj, part_id?)`** — the `list_specs` payload:
  `{project, declared, parts, project_specs, requirements, errors, warnings}`.
  One `spec_declare` per declaring part plus one for `specs.py`, memoized by
  `sha256(scope, script)`; **zero `build` calls**, so it reads on a project that
  has never been built (AC6). A file that will not execute becomes an `errors[]`
  entry, leaving every other file's declarations readable.
- **`SpecRunner.tier1(proj, part_id, build_result?)`** — the rebuild summary:
  one `spec_eval` with `timeout_s=300.0, affinity=part_id` (a shape-LRU hit on
  the worker that just built the part), joined to *the rebuild's own*
  `cache_key` so a spec result can never be attached to a different build than
  the one that landed. Returns `{status, summary, checks, requirements, cached,
  warnings}`, or **`None`** when the part declares nothing — an explicit "none
  declared", which is not "not evaluated". Assembly and FEM declarations come
  back from the kernel as `skip`/`deferred` and project-scope declarations found
  in a part script as `skip`/`unsupported_scope`: visible, named, never dropped.
- **FR10 caching.** `.cache/<cache_key>.specs.json` (`{version, cache_key,
  checks, declared, warnings, tiers}`) written with
  `ProjectStore._atomic_write`, read before any kernel call. `SPECS` lives in
  the script text, which is what `service._cache_key_for` already hashes, so
  editing a spec invalidates the sidecar for free. A corrupt or stale-version
  sidecar is unlinked and recomputed, never raised (the `metrics.json`
  precedent); an unwritable cache is a slow run, not an error. Tier-3 verdicts
  are appended into the same file under `tiers.fem`; the assembly tier gets its
  own `.cache/<project_key>.projspecs.json` keyed on the `specs.py` text plus
  every instance's id, part cache key and resolved transform (moving one
  instance changes every clearance).
  **Only `pass`/`fail` rows are cached** — a `skip` can be machine-specific (a
  missing `[fem]` extra) and an `error` is usually transient.
- **`SpecRunner.run(proj, part_id?, ref?)`** — the full report (Decision 4):
  `{project, ref, generated, status, summary, checks, parts, project_checks,
  requirements, declared, warnings, errors}`. `checks` is the flat list of
  records and every other section joins to it by `id`. A `part_id` narrows to
  one part and **skips the project tier entirely** (project scope is the
  assembly's, not a part's).
- **Tier 2, the assembly tier.** `check_interference_free` →
  `service.check_interference` (fails naming the offending pairs in
  `details.pairs`, `measured` is the largest overlap volume; fewer than two
  instances is `skip`/`no_instances`). `check_clearance` → the Slice-2
  `clearance` kernel method with `timeout_s=300.0, affinity=project` (`measured`
  is the distance, `location` the witness point on side `a`; a mesh side comes
  back as `skip`/`mesh_only`). `check_stackup` → the extracted
  `compute_stackup`, comparing the worst-case accumulation against `within_mm`.
  An unknown instance id in any of them is `status: "error"` **naming the id**
  (the PRD's rename risk) — honest, and red at a boundary.
- **Tier 3, FEM (AC3).** `check_fem_static` runs one `fem_static` request with a
  600 s budget, passing the part material's Young's modulus when the catalog has
  one (`fem_modal`'s convention, minus its hard error — a missing `E` falls back
  to the solver default rather than failing the check). Without the extra it is
  `{"status": "skip", "reason": "fem_extra_missing", "hint": …}`. `measured` is
  `{max_disp_mm, max_vm_mpa}` and the message names whichever bound broke.
- **Degradation, everywhere.** A `KernelError` from `spec_eval` becomes
  `{"status": "error", "error": <payload>}` with **one error record per declared
  check** (the declarations are still readable without building), or a single
  named record when even the declaration cannot be read — which is the
  structural-`SPECS` residue path (`SPECS = "hello"` → `contract_error`), data on
  an otherwise successful rebuild rather than a failed build. Nothing in the
  evaluation path raises: only `NotFoundError` (unknown project/part/branch) and
  `ValidationError` (a ref that is a tag, or a ref with no git) do.
- **`ref` resolution.** `run(proj, ref=…)` evaluates under
  `branches.pinned(proj, branches.tree_of(proj, ref))`. The ref is resolved with
  `history.resolve_branch` — `rev-parse` searches tags before branches, so a tag
  named like a branch would otherwise answer for it (PRD-001 X1): a tag is a
  `validation_error`, an unknown branch a `notfound_error`, and a `ref` on a
  project with no git a `validation_error` naming git. `service.branches` is
  read **inside** the method, never in `__init__` (pack load order).
- **`agentcad/core/tools_stackup.py`** — the `tolerance_stackup` handler body is
  lifted verbatim to a module-level `compute_stackup(service, project, axis,
  from_instance, to_instance)`; the tool is now a one-line call through. **Pure
  refactor, no behaviour change** — `tests/test_stackup.py` passes unedited (8
  passed). `check_stackup` calls it directly rather than
  `registry.call("tolerance_stackup", …)`, because `tools_specs` sorts *before*
  `tools_stackup` in the pack walk and a check must not depend on a tool's
  registration order.

## Files
- `agentcad/core/specs.py` — new module: `declares_specs`, `summarize`,
  `report_status`, `group_requirements`, `assign_ids`, `SpecRunner`,
  `SPEC_RESULT_VERSION`, `GATE_BUDGET_S`.
- `agentcad/core/tools_stackup.py` — handler body lifted to `compute_stackup`.
- `tests/test_specs.py` — new test module (46 tests).
- `docs/changelog/0089-spec-runner.md` — this entry.

## Measurements (the plan's Slice-3 obligation: what `check_wall` costs)

`check_wall` is the one tier-1 check with real cost, and it runs on **every**
rebuild. Measured the way the runner actually pays it: `SPECS` in the script, a
`build` first, then `spec_eval` on the warm shape LRU — so the number below is
the ray casts alone, not a rebuild. Apple Silicon, one warm worker, on copies.

| part | faces | build | `grid=4` | `grid=8` (default) | `grid=16` |
|---|---|---|---|---|---|
| `examples/rocketry` nozzle | 10 | 68 ms | **33 ms** | **60 ms** | 141 ms |
| `examples/engine` exhaust_manifold (swept runners) | 42 | 2.93 s | 139 ms | **312 ms** | 1.09 s |
| `examples/engine` intake_manifold (lofted plenum) | 93 | 12.26 s | 767 ms | **2.40 s** | 8.88 s |
| `examples/engine` cylinder_head (the heaviest casting) | 244 | 2.04 s | 298 ms | **1.04 s** | 3.77 s |

**Verdict: it ships at the default `grid=8`.** On the PRD's headline part (the
rocketry nozzle) the marginal cost of `check_wall` is **60 ms**, and it is paid
once per `(script, params, density)` because the sidecar is keyed by the same
content hash as the mesh — a slider drag back to a value you already visited
costs zero. The worst part in the tree (a lofted intake manifold, 12 s to build)
adds 2.4 s, i.e. **20% on top of its own build**, which is the correct
proportion for a check that is cheap relative to the geometry it measures.

The cost is quadratic in `grid`, as designed: 4 → 8 → 16 multiplies by roughly
3.2× and 3.7× per step on the manifold. So **the lever, if a specific part is
too slow interactively, is the declared `grid` option** (`check_wall(min_mm=…,
grid=4)` cuts the manifold to 767 ms), then moving `wall` to tier 2. Dropping
the check is not a lever.

One honest caveat the measurement makes visible: the reported minimum *changes*
with `grid` (nozzle: 1.02 mm at 4, 1.26 mm at 8, 0.27 mm at 16). `_min_wall`
samples a UV grid and casts along the inward face normal, so a finer grid finds
thinner points — it is a sampled ray cast, not a medial-axis measurement, and a
coarser `grid` trades honesty for speed. Slice 7 documents this in
`docs/part-authoring.md` and `AGENTS.md`; it is the reason `grid` is a declared
option rather than a hidden constant.

`examples/surfacing`, named in the plan, does not exist in the tree — the
surfacing-heavy stand-ins above are the `examples/engine` swept/lofted manifolds
and the cylinder head, chosen by face count.

## Notes
- **Deliberate readings of the design spec, both narrower than its example JSON:**
  1. **A part that declares nothing does not appear in `report["parts"]`.** The
     design's example shows a spec-less `flange` row with `"checks": []` and
     `"status": "green"`; calling a part with no stated intent *green* asserts
     something we did not measure. Spec-less parts are absent, exactly as a
     requirement with zero checks is absent.
  2. **A part-scope check declared in `specs.py` is `skip`/`unsupported_scope`,
     not `error`.** The design says `error` for a project-scope `check_that`;
     since `check_that` always constructs `scope: "part"`, that case arrives as
     a part-scope declaration in a project module — which the Slice-2 kernel
     pack already reports as a *skip* in the mirror-image direction
     (project-scope in a part script). Two names for one structural mismatch
     would be worse than one; both directions are `skip`/`unsupported_scope`
     with a hint, and both are counted, never dropped.
- The gate budget constant is named **`GATE_BUDGET_S`** (the implementation
  plan's name) rather than the design spec's `SPEC_GATE_BUDGET_S`. It is
  declared here and applied by Slice 5.
- `evaluate_specs`, `gate_provider` and `write_project_specs` are **not** in
  this slice: the plan assigns the first two to Slice 5 and the writer to
  Slice 4. `specs_path` and `project_script` land here because `declarations`
  and `run` need them.
- Declaration rows returned by `declarations()` carry an added `id` (the join
  key of the `requirements` map) beside the constructor's own keys.
- **No OCP anywhere in `core/specs.py`** — pinned by a subprocess test that
  installs a `sys.meta_path` finder refusing `OCP`/`build123d` and then imports
  the module (the `test_specs_toolkit.py` probe, applied to the runner).
- `service.py` is **not** edited; the rebuild seam is Slice 4's two installed
  method wrappers.
- Test suite: `make test-fast` → **711 passed, 1 skipped** (3:02);
  `uv run pytest tests/test_specs.py -q` → **46 passed** in ~9 s;
  `uv run pytest tests/test_stackup.py -q` → 8 passed, unedited. Full
  `make test` → **822 passed, 1 skipped** (~20:45) against a baseline of 776
  passed, 1 skipped — exactly the 46 new tests, with **zero edits to any
  pre-existing test file**.
