# 0259 — PRD-024: the bench scorer — six subscores, rubric injection, a byte-stable `score.json`

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary
Adds `agentcad/bench/scoring.py`: the mechanical scorer of AgentCAD-Bench
(design spec Decisions 3, 4 and 6; FR4, FR6, FR7; AC2, AC3). It copies a
submission into a work cell, injects the task's `SPECS` rubric into the copy,
opens a **muzzled** ephemeral service over it, measures six subscores, and
returns one deterministic, timestamp-free, path-free `score.json` payload.
No model-facing surface is added: there is no `tools_bench.py`, no route, no
event, no manifest key.

## Changes
- **`Scorer.score(task, submission, *, budget_s=None, work_dir=None)`** —
  `CheckRunner._run_ref`'s lifecycle: refuse an overlapping work dir → cut a
  cell with `mkdtemp(prefix="agentcad-bench-", dir=default_work_root(service))`
  → `copytree` ignoring `.cache`/`exports`/`.history` → inject the rubric →
  `checks._ephemeral_service(cell, tree, kernel)` → measure → `rmtree` **the
  cell we created** in a `finally`. The submission, the task bundle and any
  caller-supplied `--work-dir` are never written to; the copy is proven
  untouched by a test that byte-compares the whole tree before and after.
  The kernel is **shared**, never restarted.
- **All three `_ephemeral_service` nullings are load-bearing here** for their
  own reasons, restated at the call site: a `project_changed` publish would
  commit a history snapshot, a live `branch_resolver` would write a
  `.history/agentcad/` sidecar *into the copy*, and `write_guard` would
  materialise a branch tree.
- **`refuse_scoring_overlap(root, submission, task_root, projects_root)`** —
  `checks.refuse_work_dir_overlap` for the submission and the projects root,
  plus the two read-only inputs a bench run also has: the **task directory**
  and the shipped **`benchmarks/` tree** (the packages gate's `_refuse_overlap`
  precedent, which likewise covers the package directory and not only the
  project). `root=None` means "no `--work-dir`" and refuses nothing.
- **`inject_rubric(task, copy_root) -> [part ids]`** — `specs.project` replaces
  `<copy>/specs.py`; each `specs.parts[id]` is appended to the candidate's
  script behind `BLOCK_HEADER`. The block **re-binds `SPECS`**, so the
  candidate's own declarations are discarded (the last module-level binding
  wins) — that is what stops an agent inflating its `specs` subscore with
  checks it wrote itself, and it is why the loader refuses a rubric using
  `SPECS +=`. A `specs.parts` entry naming a part the copy does not have is
  skipped, not an error: a missing part is already a `built` zero.
- **The six subscores** (`{"value", "weight", "status", "detail"}`, status in
  `("ok", "skipped_mesh", "error", "not_applicable")`):
  - `built` — `passed / len(target.parts)` over `service._ensure_built`. A
    part absent from the copy's manifest is a **failure**, detected from
    `store.part_ids` before the call; so is every exception
    `_blames_harness` blames on the candidate, `detail.reasons` naming which.
  - `valid` — `metrics["is_valid"] is True` per part, with `_build_item`'s
    imported-geometry escape **deliberately absent**: a bench candidate that
    imports a mesh is measured, not forgiven.
  - `specs` — one `service.specs.run(proj, deadline=…)`, the runner read
    **inside** the method (the `CheckRunner._stage_specs` rule).
    `passed / (passed + failed + errors)` over **the injected rubric's rows
    only**, **`skip` rows out of the denominator** — a skip is "we did not
    measure". Ownership is read off `report["parts"][pid]["checks"]` for the
    parts `inject_rubric` returned and `report["project_checks"]["checks"]`
    only when the task ships `specs/project.py`, never parsed out of an id
    prefix (a part legitimately named `project` would collide). A `skip` whose
    reason is in `CANDIDATE_SKIP_REASONS` — `mesh_only`, `no_instances` — is
    counted as a **failure** rather than left out of the denominator. The
    report is **never embedded**: `specs._report` stamps a `generated`
    timestamp and one timestamp anywhere in the body would end AC3.
  - `geometry` — **per part**, `value = mean` over every `target.parts` entry,
    one `iou` kernel call each with `timeout_s=IOU_TIMEOUT_S` (300 s;
    **explicitly**, because the client's own default is a 60 s build timeout)
    and `affinity=task.id` so the reference STEP stays in `refload`'s LRU and
    the candidate script in `worker._SHAPE_CACHE`. A mesh-only part
    contributes 0.0 like any other unmeasurable part and is named in
    `detail.skipped_mesh`; `status` is `skipped_mesh` only when **every**
    target part is mesh-only. `detail` is `{"parts": {...}, "error": {...}}`,
    nested so a part named `error` cannot collide with the error key.
  - `interference` — `check_interference(min_volume=0.001)`;
    `interference_fraction(checked, pairs, skipped)` = `clean / C(n, 2)` with
    `C(n,2)` computed as `n*(n-1)//2`. Every pair touching a `skipped_mesh`
    instance counts as **un-clean**: an unmeasurable pair is not a clean pair.
    Fewer than two instances under a non-zero weight is a **zero** (the task
    asked for an assembly and got none), never `not_applicable`. An exception
    resolving the candidate's own assembly is `value 0.0, status "ok",
    reason "assembly_unresolved"`.
  - `metrics` — `satisfied / len(windows)` over `reference/metrics.json`,
    inclusive on both bounds with `specs._slack`'s tolerance. `bbox_*_mm` and
    `com_*_mm` are derived from `metrics["bbox"]`/`["center_of_mass"]`; no new
    kernel call exists for them.
- **`error` is the harness failing to measure; everything the candidate caused
  is a measured zero (`status: "ok"`, a `reason` in `detail`).**
  `not_applicable` comes only from a zero weight in `task.json`. Excluded
  subscores are renormalised away, so a run-decided exclusion lets a candidate
  **raise its total by destroying evidence** — delete the part script, break
  the assembly, export an STL. Every `error` arm is therefore guarded by
  `_blames_harness(exc)`: a `KernelError` is ours only when it is `timeout` or
  `kernel_crash`; a script error, a contract error and an OCCT failure over the
  candidate's own geometry are the candidate's, as is every `AppError`
  (`NotFoundError` for a deleted script, `ValidationError` for a manifest that
  does not agree with itself); an unanticipated exception class is ours. See
  the orchestrator rulings below.
- **A zero weight short-circuits before any measurement.** No kernel call, no
  `check_interference`, no spec run — and no *build* either: `_measure` builds
  a part only when one of `built`/`valid`/`geometry`/`metrics` is actually
  scored.
- **`total_of(subscores) -> (total, weights_effective)`** — excludes `error`
  and `not_applicable`, renormalises the rest, and publishes
  `weights_effective` so a reader can reproduce the arithmetic without knowing
  the rule. `W == 0` answers `(0.0, {})` and adds a note; turning that into
  exit 2 is `bench score`'s job (Task 4).
- **Determinism (FR6/AC3).** The payload carries `{schema, agentcad, harness,
  task, task_set, task_version, category, total, weights_effective, subscores,
  notes}` and **no timestamp, host, path, duration or client id**. Two
  additional defences, both new to this diff and both load-bearing:
  - error details are `{"type", "message"}` and never `_payload`'s `details`,
    which is where a `KernelError` keeps its **traceback** — and a traceback
    names files;
  - the finished payload is **scrubbed** of four roots — the cell (both the
    `mkdtemp` and the resolved spelling, which differ on macOS), the task
    tree, the submission and the projects root — replaced by `<cell>`,
    `<task>`, `<submission>`, `<projects>`, longest needle first so a
    submission nested inside the projects root is labelled as the submission.
    `ProjectStore._read_manifest`'s refusal is literally
    `f"{path} is not a project"`, `load_windows` names the task tree in an
    `OSError`, and the `iou` handler's `iou unavailable: …` can wrap a
    `refload` failure naming the reference STEP.
  `round_floats` is applied to the whole payload before it is returned, so the
  dict a caller reads is the document `write_json` emits.

## Files
- `agentcad/bench/scoring.py` — new (the whole scorer).
- `tests/test_bench_scoring.py` — new, 35 tests.
- `docs/changelog/0259-prd-024-bench-scorer.md` — this entry.

## Tests
```
$ uv run pytest -q tests/test_bench_scoring.py tests/test_bench_kernel_iou.py
....................................................                     [100%]
52 passed in 4.21s
```
```
$ uv run ruff check agentcad/bench/scoring.py tests/test_bench_scoring.py
All checks passed!
```
`make test` — <orchestrator fills>

AC3 is two tests: `score` twice over the same submission is byte-identical
(and contains no `generated`, no `started`, no `/tmp`, no `/private`), and the
same for an unreadable submission, which is the case that actually leaked a
path. AC2 is four: a missing hole costs `geometry` and not `specs`, a
too-thick plate names `spacer_plate:envelope` in `specs.detail.failed`, a
candidate's own `SPECS` are discarded (3 rows, not 4), and a missing target
part is zero everywhere and never `not_applicable`.

## Notes
- **Plan defect corrected.** The plan's AC2 test mutated
  `GridLocations(p.hole_dx, p.hole_dy, 2, 2)` to `…, 2, 1)` and asserted the
  IoU drop was `2 * hole_volume / union`. That mutation does not remove two
  holes: it also **moves** the two survivors onto `y = 0`, so the candidate is
  missing four reference holes *and* carries two the reference does not have,
  and the true drop is `6 * hole_volume / union` — 3× the asserted value, well
  outside its `rel=0.05`. The test now replaces the grid with an explicit
  two-position `Locations(…)` at two of the four grid points, which removes
  exactly two holes and adds none; the candidate then strictly contains the
  reference and the plan's arithmetic is correct as written.
- **Orchestrator rulings of record (review round 1), applied throughout.**
  - **R1 — §4.7/D5 governs over a literal reading of §4.1/§4.3.** *Any* failure
    the candidate caused is a measured zero (`status: "ok"`, `value 0.0`, a
    `reason` in `detail`); `error` is reserved for harness failures — budget
    truncation, a kernel that is gone, an unexpected exception class. This is
    `_blames_harness` and it now guards `_build_all`, `_specs`, `_geometry`
    and `_interference`. The first draft's literal reading of §4.3 (a
    zero-denominator `specs` is `error`) was *candidate-reachable* and is
    reversed.
  - **R2 — `geometry` is per-part.** Value is the mean over `target.parts`
    with a mesh-only part contributing 0.0; `status: "skipped_mesh"` only when
    every target part is mesh-only, otherwise `"ok"` with the skipped parts
    named. `detail` is split into `{"parts": {...}, "error": {...}}` so a part
    named `error` cannot collide.
  - **R3 — the `specs` denominator is rubric-owned rows only**: `<part>:*` for
    the parts `inject_rubric` returned, `project:*` only when the task ships
    `specs/project.py`. `<copy>/specs.py` is replaced *or deleted*
    unconditionally, so a candidate-authored one never scores.
- The honest limitation from design §3.1 stands and belongs in `docs/bench.md`
  (Task 11): a candidate script could monkeypatch `agentcad.toolkit.specs` in
  `sys.modules` before the injected import line runs and fake the `specs`
  subscore. The bench is a **measurement, not a security boundary** — the
  publish gate's own sentence. The ceiling is one subscore (geometry,
  interference and metrics are measured from the built shape in the kernel),
  and every published row ships its transcript and a reproducible submission.
- `agentcad/bench/**` remains OCP-free: the one mesh question the scorer asks
  is answered by suffix (`MESH_SUFFIXES`, mirroring `refload._MESH_EXTS`)
  rather than by importing `kernel.refload`, which does import build123d. A
  fresh interpreter importing `agentcad.bench.scoring` loads neither `OCP` nor
  `build123d`.
- No edits to `worker.py` / `tools.py` / `app.py` / `service.py` / `cli.py`.

## Review round 1 (fixes in this same entry)

Reviewed against the seed task; every finding was *measured*, not argued. Six
of the seven were the same bug wearing different hats — a candidate-caused
failure classified as `error`, therefore **excluded**, therefore
**renormalised away**, therefore a higher total for a worse submission.

| # | Finding (measured) | Fix |
|---|---|---|
| C1 | a broken candidate assembly excluded `interference`; total 0.8 → 1.0 | `_interference` classifies with `_blames_harness`; the candidate's own unresolvable assembly is `0.0 / "ok" / "assembly_unresolved"` |
| C2 | a **deleted part script** made `built`/`valid`/`specs`/`metrics` `error` and left `weights_effective == {"geometry": 1.0}` | `_build_all` maps `AppError` (and every non-harness `KernelError`) to `state: "failed"` with a reason; only a timeout, a dead worker or an unanticipated class is `error` |
| C3 | a mesh-only candidate attached no rubric → zero rows → `specs` excluded; total 0.1875 → 0.2083 | `_specs` uses `inject_rubric`'s returned list: nothing attached, or nothing measured, is `0.0 / "ok"` with `reason` `no_rubric_attached` / `nothing_measured` / `spec_run_refused` |
| C4 | the `specs` denominator counted every check in the project — nine filler parts moved 0.667 → 0.917 — and a candidate's own `specs.py` scored | R3: rubric-owned rows only, and `<copy>/specs.py` is deleted when the task ships none |
| I5 | a non-UTF-8 byte in a part script (or a `parts/<id>.py` that is a directory) crashed `score()` | the copy **and** the injection moved inside the guard; `_PREPARE_FAILURES` adds `UnicodeDecodeError` (a `ValueError`, not an `OSError`) and `shutil.Error` |
| I6 | only the cell path was scrubbed; the task tree, submission and projects root could still reach `detail.error.message` | four labelled needles, longest first |
| I7 | no tests for arithmetic with no reference task yet | 13 kernel-free unit tests over `interference_fraction`, `metric_of`, `window_satisfied`, `_metrics`, `_timeout`/`_budget_broke`, `_scrub`, and the zero-weight/zero-build short-circuits |

Also taken: `C(n,2)` is `n*(n-1)//2` rather than `len(list(combinations(…)))`,
and `_assert_statuses` checks every emitted status against
`SUBSCORE_STATUSES` (and every value into `[0, 1]`) in five tests.

**One expectation the fixes changed.** `SpecRunner.run` refuses over the
*whole* project when one part's script file is missing, so the two-part
deleted-script test measures `specs` at `0.0 / "ok" / "spec_run_refused"`
rather than 1.0 for the intact part's rubric. That is the candidate's doing
and it costs them the subscore at **full weight** — no exclusion, no
renormalisation — which is exactly R1's point.

## Review round 2 (fixes in this same entry)

**One new Important, from the round-1 diff.** A candidate-authored
`project.json` that is *valid JSON but structurally wrong* escaped `score()`
entirely, and would have been classified as a harness failure if it had been
caught — the C2 exploit in a new spelling. `ProjectStore` validates that the
document parses and names a project; it validates nothing under that. Measured:
`parts: [{"label": "a"}]` is a `KeyError 'id'` at `_build_all`'s
`store.part_ids`, which sat **outside** every guard; `assembly: "nope"` is an
`AttributeError` (`'str' object has no attribute 'setdefault'`) inside
`store.open`, i.e. inside `_prepare` but past a `_PREPARE_FAILURES` tuple that
caught neither.

- `_PREPARE_FAILURES` is now
  `(AppError, OSError, shutil.Error, KeyError, TypeError, AttributeError,
  ValueError)`. Deliberately wide, and the docstring says why: it costs the
  ability to tell a harness bug *in `_prepare`* from a malformed submission,
  and it buys the guarantee that **no manifest a candidate can author is worth
  an `error`** — which, renormalised away, is worth points. The net covers
  `_prepare` only; the measurement still classifies with `_blames_harness`.
- `_prepare` **probes the manifest's shape once**, inside the guard
  (`service.store.part_ids(proj)` right after `_ephemeral_service`), so a
  structurally wrong document is an unopenable submission — a zeroed score with
  a note — instead of a traceback out of a reader that trusted it.
- `_build_all` guards its own `part_ids` too (belt and braces behind the
  probe): a manifest we cannot enumerate is a candidate with no parts.

**Orchestrator ruling — a `skip` the candidate induced is a failure.** New
module constant `CANDIDATE_SKIP_REASONS = ("mesh_only", "no_instances")`,
enumerated from `core/specs.py:1170` (a `clearance` check whose side is an
imported mesh) and `:1120` (a project-scope `interference_free`/`clearance`
check with fewer than two instances placed). Both are choices the candidate
made, and leaving them out of the denominator **pays** a candidate for making a
declared check unmeasurable — `_blames_harness`'s exploit one level down. They
now count, and count as failures, named separately in
`detail["skipped_as_failed"]` so a reader can still tell a measured failure
from an induced skip. Everything else stays out, because it is not the
candidate's: `fem_extra_missing` is this machine without the `[fem]` extra,
`unsupported_scope` is the *rubric author* declaring a part-scope check at
project scope, and `deferred` is a rebuild-tier row a full run never emits. (A
budget row is an `error`, not a skip — `specs._budget_row`.)

Minors taken: `metric_of`'s no-op ternary (`else value` → `else None`, so a
non-finite measurement really is "no number"); the changelog's I7 count
corrected 14 → 13; and two tests that were missing branch coverage —
`inject_rubric`'s project-block **write** branch and `_owned_rows`'
project-scope branch, exercised together by a tmp task fixture that ships
`specs/project.py`.
