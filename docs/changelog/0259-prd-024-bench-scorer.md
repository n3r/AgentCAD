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
  - `built` — `passed / len(target.parts)` over `service._ensure_built`;
    `error` only when it **raises** (`CheckRunner._build_item`'s defensive
    edge). A part absent from the copy's manifest is a **failure**, not an
    error, and is detected from `store.part_ids` before the call.
  - `valid` — `metrics["is_valid"] is True` per part, with `_build_item`'s
    imported-geometry escape **deliberately absent**: a bench candidate that
    imports a mesh is measured, not forgiven.
  - `specs` — one `service.specs.run(proj, deadline=…)`, the runner read
    **inside** the method (the `CheckRunner._stage_specs` rule).
    `passed / (passed + failed + errors)`, **`skip` rows out of the
    denominator** — a skip is "we did not measure". The report is **never
    embedded**: `specs._report` stamps a `generated` timestamp and one
    timestamp anywhere in the body would end AC3.
  - `geometry` — one `iou` kernel call per part with a `reference.steps`
    entry, `timeout_s=IOU_TIMEOUT_S` (300 s; **explicitly**, because the
    client's own default is a 60 s build timeout) and `affinity=task.id` so the
    reference STEP stays in `refload`'s LRU and the candidate script in
    `worker._SHAPE_CACHE`. `value = mean(iou)`.
  - `interference` — `check_interference(min_volume=0.001)`;
    `clean / C(n, 2)`. Every pair touching a `skipped_mesh` instance counts as
    **un-clean**: an unmeasurable pair is not a clean pair. Fewer than two
    instances under a non-zero weight is a **zero** (the task asked for an
    assembly and got none), never `not_applicable`.
  - `metrics` — `satisfied / len(windows)` over `reference/metrics.json`,
    inclusive on both bounds with `specs._slack`'s tolerance. `bbox_*_mm` and
    `com_*_mm` are derived from `metrics["bbox"]`/`["center_of_mass"]`; no new
    kernel call exists for them.
- **`error` is the harness failing to measure; `not_applicable` comes only from
  a zero weight in `task.json`.** A candidate that is absent, broken, mesh-only
  or simply wrong is measured and measures **zero**. Nothing at run time may
  promote a subscore to `not_applicable`, because excluded subscores are
  renormalised away and a run-decided exclusion would let a candidate raise its
  total by destroying evidence (delete the part, break the build, export an
  STL). A mesh-only candidate is therefore `skipped_mesh` at **value 0.0 and
  included at its full weight**, and a submission that is not a readable
  AgentCAD project at all scores zero everywhere rather than raising.
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
  - the finished payload is **scrubbed** of this run's cell path (both the
    `mkdtemp` and the resolved spelling, which differ on macOS), replaced by
    `<cell>`. `ProjectStore._read_manifest`'s refusal is literally
    `f"{path} is not a project"`, so an unreadable submission embedded a
    randomly-named temp path and two runs of the same submission differed.
  `round_floats` is applied to the whole payload before it is returned, so the
  dict a caller reads is the document `write_json` emits.

## Files
- `agentcad/bench/scoring.py` — new (the whole scorer).
- `tests/test_bench_scoring.py` — new, 12 tests.
- `docs/changelog/0259-prd-024-bench-scorer.md` — this entry.

## Tests
```
$ uv run pytest tests/test_bench_scoring.py -q
............                                                             [100%]
12 passed
```
```
$ uv run pytest -q tests/test_bench_tasks.py tests/test_bench_kernel_iou.py \
      tests/test_bench_scoring.py tests/test_checks.py -x
99 passed in 7.54s
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
- **A known tension, followed as specified.** Design §4.3 makes a `specs`
  denominator of zero an `error` under a non-zero weight, which reads against
  D5 ("a candidate that is absent measures zero"): a candidate that deletes
  every part gets `specs` *excluded* rather than zeroed. It is not exploitable
  — every other subscore is zero, so the renormalised total is still 0.0 — and
  it is what the spec says, so it is implemented literally and flagged here
  rather than quietly changed.
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
