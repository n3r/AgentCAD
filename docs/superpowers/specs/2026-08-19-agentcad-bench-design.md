# PRD-024 AgentCAD-Bench — design spec (tasks, kernel scorer, runner, leaderboard)

- **PRD:** `docs/prd/in-progress/PRD-024-agentcad-bench.md`
- **Branch:** `prd-024-agentcad-bench`
- **Date:** 2026-08-19
- **Scope of this spec:** the PRD **MVP** (task format + loader + scorer + `bench
  score` + `bench run --agent builtin` + 25 tasks + reference self-tests +
  JSON/markdown reports), **plus** FR11 baseline gating, FR12 `bench publish`
  (the static leaderboard renderer), and the FR10 external-agent walkthrough
  with a checked-in example submission. **Out of scope, seams designed not
  built (§16):** FR13 launch results (needs real paid runs), the `fem/`
  category, task-set v2 rotation policy, PRD-018 wiring, multi-turn
  continuation, voxel-IoU fallback, PNG/image assets.

This is a *wide* PRD with a *narrow* code footprint. Everything below keeps to
the extension-point contract: one OCP-free package (`agentcad/bench/`), one
worker handler pack (`agentcad/kernel/handlers/bench.py`), one CLI subcommand
delegating out of `agentcad/cli.py`, and **no new model-facing tool, route,
event, error type or manifest key**. `worker.py` / `tools.py` / `app.py` /
`service.py` are not edited. `agentcad/cli.py` takes exactly two edits (§9.1).

---

## 0. Load-bearing seams (file:line, today's code)

**The headless-service pattern (PRD-004), which `agentcad bench` reuses verbatim:**

- `cli._build_service` — `agentcad/cli.py:48-136`. Resolves quotas (88), posture
  (89), pool size (90), creates the granted work root
  `mkdtemp(prefix="agentcad-work-")` (94), builds writable roots (97-99), starts
  the kernel (107), constructs `AgentCADService` (114), stamps `work_root` (115)
  / `writable_roots` (120) / `usage` (121), registers examples (123) and the
  catalog (124). Both failure paths remove the work root (111, 134).
- `cli._writable_roots` — `agentcad/cli.py:194-261`. The system temp dir is
  **not** a writable root; the projects dir and `<state>/publications/build`
  are created here because a Landlock rule on a missing path is ENOENT (250-255).
- `cli._register_examples` — `agentcad/cli.py:264-280`; the
  `AGENTCAD_EXAMPLES=0` opt-out at `agentcad/cli.py:270`.
- `cli._remove_work_root` / `_release_work_root` — `agentcad/cli.py:139-158`
  ("never delete a directory it did not create").
- `cli._accept_work_dir` — `agentcad/cli.py:161-191`: resolve → refuse →
  create, **before** `_build_service`, because the seatbelt/Landlock profile is
  fixed at spawn.
- `checks.refuse_work_dir_overlap` — `agentcad/core/checks.py:170-202`, with
  `_within` at `:157-167`.
- `checks._ephemeral_service` — `agentcad/core/checks.py:864-914`. The three
  non-negotiable nullings: `bus.on_publish = None` (894), `branch_resolver =
  None` (903), `write_guard = None` (913); kernel **shared, never restarted**
  (878).
- `checks.default_work_root` — `agentcad/core/checks.py:836-861`.
- `cli.cmd_check` — `agentcad/cli.py:1014-1154`: setup inside the exit-code
  mapping (1056), `locks.set_client_id("ci")` (1089), kernel stopped and work
  root released in `finally` (1120-1130), exit code from the report (1154).
- `cli._finite_arg` — `agentcad/cli.py:658-686` (NaN/inf refusal for a limit).
- `cli._write_check_outputs` — `agentcad/cli.py:689-716` (atomic report writes
  through `ProjectStore._atomic_write`).
- argparse wiring for `check` — `agentcad/cli.py:1484-1545`; subparser metavar
  `agentcad/cli.py:1446-1448`; dispatch `agentcad/cli.py:1688-1708`.

**Report/verdict discipline the bench mirrors:**

- `checks.STAGES` (85), `ITEM_STATUSES` (97), `make_item` (246-293),
  `make_stage` (296-318), `finalize_report` (321-370), `exit_code` (376-398),
  `_MIN_CALL_S = 1.0` (822), `CheckRunner._run_context` (1251-1271),
  `_cannot_afford` (1304-1315), `_budget_item` (1333-1346).
- `checks._METRIC_KEYS` (833) and `_compare_builds` (941-981) — the determinism
  precedent: exact equality, "not deterministic" is not a useful sentence.

**Specs (PRD-003) — the scoring vocabulary:**

- Storage is **code, never manifest**: part scope is a module-level `SPECS` list
  in `parts/<id>.py`; project scope is a root `specs.py`
  (`SpecRunner.specs_path`, `agentcad/core/specs.py:510-513`).
- `specs.declares_specs` (183-213) / `_binds_specs` (137-166) / `_SPECS_TEXT_RE`
  (179-180) — the last accepts `SPECS =`, `SPECS: list =` **and** `SPECS +=`.
- Constructors: `agentcad/toolkit/specs.py` — `SPEC_FORMAT = 1` (48),
  `PART_KINDS` (50), `PROJECT_KINDS` (51), `_declaration` (162-165),
  `check_valid` (170), `check_mass` (177), `check_volume` (186), `check_bbox`
  (196), `check_wall` (204), `check_that` (218), `check_fem_static` (233),
  `check_interference_free` (261), `check_clearance` (271), `check_stackup`
  (283), `declaration_problem` (308), `json_safe` (350).
- `SpecRunner.run` — `agentcad/core/specs.py:1363-1391`; the report shape at
  `_report` (1524-1583); the per-row skeleton `_record` (315-332); `summarize`
  (218-229); `report_status` (231-239); `assign_ids` (269-288, id is
  `"<part>:<name>"` / `"project:<name>"`).
- Kernel: `handlers/specs.py` — `_EVALUATORS` (216-217), `handle_spec_declare`
  (310), `handle_spec_eval` (323), `handle_clearance` (363), registration (420).
- `CheckRunner._stage_specs` — `agentcad/core/checks.py:1857-1902`, reading
  `service.specs` **inside** the method (1873).
- Real on-disk examples: `examples/rocketry/specs.py:18-29` (project scope),
  `examples/rocketry/parts/nozzle.py:45-51` and `parts/flange.py:35-38` (part
  scope).

**Kernel geometry:**

- `worker._shape_volume` — `agentcad/kernel/worker.py:353-361` (sum
  `shape.solids()`; nested `Compound.volume` undercounts).
- `worker._metrics` — `:364-415` (`volume_mm3`, `area_mm2`, `mass_g`, `bbox`,
  `center_of_mass`, `is_valid`, `n_faces`, `n_edges`, `n_solids`, per-solid
  `solids`).
- `worker._place` — `:418-420` (`b3d.Location(position, rotation_deg)`,
  intrinsic XYZ Euler degrees).
- `worker._item_shape` — `:595-628`: an item is `{"script", "params"}` **or**
  `{"source"}`; kind is `"script"` / `"solid"` / `"mesh"`.
- `worker.pairwise_interference` — `:646-724`: decomposes to solids because
  build123d's `&` misbehaves on a multi-solid Compound (650-653); AABB
  prefilter (666-686); the intersection itself is `c = a & b` at `:687-689`.
- `worker.WORKER_TOOLBOX` — `:765-780`; `_load_handler_packs` — `:783-804`
  (a pack shadowing a builtin is refused with a warning, 800-803).
- `handlers/diff.py` — the two-shape-handler template: `register` (29),
  `_side_shape` (37-48), the guarded boolean `_stage` (50-73), the mesh refusal
  (102-109). **The pattern this spec copies.**
- `handlers/reference.py:39-91` — pack shape, and `refload` imported directly
  (`from ..refload import load_reference`, `:15`).
- `refload.load_reference` — `agentcad/kernel/refload.py:41-69`; `_BREP_EXTS`
  (27), `_MESH_EXTS` (28), `is_mesh_kind` (72-73); LRU keyed by
  `(realpath, mtime_ns, size)`, `_CACHE_MAX = 8` (24).
- `protocol.py:21-25` — `ERROR_SCRIPT`/`ERROR_CONTRACT`/`ERROR_KERNEL`/
  `ERROR_TIMEOUT`/`ERROR_CRASH`; `WorkerError` (38-48).
- `KernelClient.request(method, params, timeout_s=, affinity=)` —
  `agentcad/kernel/client.py:233-244`, default timeout 60 s (`:91`);
  `KernelPool._pick` routes by `hash(affinity) % size`
  (`agentcad/kernel/pool.py:86-88`).

**Service:**

- `AgentCADService._ensure_built` — `agentcad/core/service.py:764-778`;
  `_rebuild` (884); `_build_with` (899, kernel request at 990).
- `check_interference` — `:615-641` (kernel request at 633, `timeout_s`
  override for a deadline at 617-620).
- `_shape_item` — `:600-613`: the exact item dict the bench reuses for both
  sides of an IoU call.
- `create_project` (202), `open_project` (207), `get_part` (303),
  `export_part` (496).
- `ProjectStore._atomic_write` — `agentcad/core/project.py:777` (staged through
  a **random** name).

**Chat (the system under test):**

- `chat.DEFAULT_MODEL = "claude-sonnet-5"`, `MAX_TOKENS = 4096`,
  **`MAX_TOOL_CALLS_PER_TURN = 30`**, `DEFAULT_SESSION = "main"` —
  `agentcad/agent/chat.py:48-51`.
- `ChatEngine.__init__(registry, bus, model=, api_key=, client_factory=)` —
  `:142-160`; `available` (164); `_default_client_factory` (168-171);
  `history(project, session)` (175-178).
- `start_turn` — `:185-204`: refuses a non-`str` message (195-196), fire-and-
  forget, task parked in `self._tasks[turn_id]` (202).
- `_run_turn` (216-223, per-`(project, session)` `asyncio.Lock`),
  `_run_turn_locked` (225-336): the API call at 238-244, termination on no
  `tool_use` at 262-263, the tool-limit break at **306-316**, the blanket
  `except Exception` at 317-327, `chat_done` in `finally` (329-335).
- `_call_tool` sets `locks.set_client_id("chat")` — `:338-350`.
- `_repair_history` — `:352-380`.
- **No wall-clock timeout anywhere in the turn loop.**
- The fake-client pattern: `tests/test_chat.py:39-66` (`_text`, `_tool_use`,
  `_response`, `FakeMessages`, `FakeAnthropic`), injected as
  `ChatEngine(registry, bus, api_key="test-key", client_factory=lambda: fake)`
  (e.g. `tests/test_chat.py:100-102`).

**Tests / CI / packaging:**

- `tests/conftest.py`: session-scoped `kernel` (168-173), `make_test_service`
  (120-126), `clone_test_service` (128-131), `flatten_routes` (374-411).
  Examples-on-a-copy lives in the test modules —
  `tests/test_examples.py:70-87`, `tests/test_examples_golden.py:226-243`.
- `pyproject.toml`: `[project.scripts] agentcad = "agentcad.cli:main"`;
  `[tool.hatch.build.targets.wheel] packages = ["agentcad"]` (46-47) — a new
  `agentcad/bench/` subpackage needs **no** pyproject change; `testpaths =
  ["tests"]` (51) and global `timeout = 120` (50); markers `exhaustive`,
  `integration`, `portability`, `slow` (52-57).
- `.github/workflows/ci.yml`: `test` job (22), matrix (27-53), PR step
  (106-114), portability step (116-122), `exhaustive` job (132). **No workflow
  uses a secret today.** `geometry-ci.yml:8-9` states the rule: `pull_request`,
  never `pull_request_target`, and no secrets — a part script is arbitrary
  Python.
- **Trap:** `tests/test_prd006_acceptance.py:484-495` asserts
  `ci.count("expect_sandbox: active") == 2` over `ci.yml`. A new job in
  `ci.yml` that sets it would break that test — which is why the bench CI is a
  **separate workflow file** (§13).
- Baseline test count today: `uv run pytest --collect-only -q` → **4474 tests**.

---

## 1. Decision 1 — the task bundle is a directory of things this repo already reads

A task lives at `benchmarks/tasks/<category>/<id>/`. `<category>` is one of the
five PRD names; `<id>` matches `^[a-z][a-z0-9_]{2,47}$`. The **task id** used
everywhere (CLI, `score.json`, baseline, leaderboard) is `"<category>/<id>"`.

```
benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/
  task.json                       # the rubric (schema-versioned)
  prompt.md                       # the prompt handed to the agent, verbatim
  assets/drawing.svg              # optional; attached to the prompt as text
  starter/                        # optional; a COMPLETE AgentCAD project dir
      project.json
      parts/<id>.py
  reference/
      project/                    # a COMPLETE AgentCAD project dir — the solution
          project.json
          parts/spacer_plate.py
      steps/spacer_plate.step     # exported from reference/project; the IoU datum
      metrics.json                # the metric windows
  specs/
      project.py                  # optional: injected as <copy>/specs.py
      parts/spacer_plate.py       # optional: injected into <copy>/parts/spacer_plate.py
```

Three consequences, each of which removes code rather than adding it:

1. **`starter/` and `reference/project/` are ordinary project directories.**
   The runner and the scorer open them with `service.open_project(path)`
   (`service.py:207`) exactly as `test_examples.py:85` opens a copied example.
   No bespoke installer, no manifest synthesis.
2. **AC1 is `bench score` with no special case:** scoring
   `benchmarks/tasks/<c>/<id>/reference/project --task <c>/<id>` must return
   `total == 1.0`. The reference is graded by the machinery it defines.
3. **The rubric (`specs/`, `reference/metrics.json`, `task.json` weights) is
   separate from the reference geometry.** A reference project's part scripts
   carry **no** `SPECS` of their own; the rubric is injected into every
   candidate — reference included — so the reference cannot pass by declaring
   something no other submission is measured against.

### 1.1 `task.json` — complete example

```json
{
  "schema": 1,
  "id": "model_from_drawing/mfd_001_spacer_plate",
  "task_set": "bench-v1",
  "version": 1,
  "category": "model_from_drawing",
  "title": "Spacer plate from a three-view drawing",
  "sets": ["core", "fast"],
  "authored_against": "0.1.0",
  "source": {"kind": "authored"},
  "prompt": "prompt.md",
  "assets": ["assets/drawing.svg"],
  "starter": null,
  "target": {"project": "bench_mfd_001_spacer_plate", "parts": ["spacer_plate"]},
  "budgets": {"wall_s": 600, "turns": 24},
  "frame": {
    "align": "world",
    "rotations_deg": [],
    "datum": "the plate's bottom face lies on Z = 0 and its centre is at the origin"
  },
  "reference": {
    "project": "reference/project",
    "steps": {"spacer_plate": "reference/steps/spacer_plate.step"},
    "metrics": "reference/metrics.json"
  },
  "specs": {
    "project": null,
    "parts": {"spacer_plate": "specs/parts/spacer_plate.py"}
  },
  "weights": {
    "built": 0.15, "valid": 0.10, "specs": 0.10,
    "geometry": 0.50, "interference": 0.00, "metrics": 0.15
  }
}
```

Field notes (all validated by the loader, §2):

- `source` is `{"kind": "authored"}` or
  `{"kind": "derived", "example": "construction", "parts": ["angle_bracket"]}` —
  provenance for the docs and for the contamination story.
- `sets` selects subsets: `core` (all 25) and `fast` (the CI subset). A task
  may declare more; `--set` matches by membership.
- `authored_against` is `agentcad.__version__` at authoring time (the PRD's
  maintenance-drag mitigation). Informational; never gates.
- `target.project` is the scratch project name the runner creates and the
  prompt pins. `target.parts` are the part ids every subscore addresses — the
  frame-declaration discipline extended to **naming**, and it is why the prompt
  must say "create a part called `spacer_plate`".
- `budgets.turns` is the **tool-call** budget. `budgets.wall_s` is wall clock.
  The API-turn cap is derived, not declared (§8.3).
- `weights` keys are exactly the six subscores. **A weight of `0.0` declares the
  subscore `not_applicable`** — the task decides, never the run (§4.7).
  Weights must be non-negative, finite, and sum to `1.0 ± 1e-9`.
- `frame.align ∈ {"world", "com", "bbox_center"}`; `frame.rotations_deg` is a
  list of at most 8 intrinsic-XYZ triples the task *permits* (§5.3).
  `frame.datum` is prose, reproduced in `docs/bench.md` review guidance and in
  the prompt — it is what makes the frame reviewable in PR.

### 1.2 `specs/parts/<part_id>.py` — the SPECS format as it actually exists

The rubric file is **the tail of a part script**: an import and one `SPECS`
binding, in PRD-003's real vocabulary. Verbatim shape, mirroring
`examples/rocketry/parts/nozzle.py:45-51`:

```python
# benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/specs/parts/spacer_plate.py
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)

SPECS = [
    _bench_check_valid(name="valid", requirement="MFD-001"),
    _bench_check_wall(min_mm=3.0, grid=4, name="ligament", requirement="MFD-001"),
    _bench_check_bbox(within_mm=(80.2, 50.2, 6.2), name="envelope",
                      requirement="MFD-001"),
]
```

Two rules the file must obey, checked by the loader:

- **It re-binds `SPECS`, it does not append.** The block is concatenated to the
  end of the candidate's script, so the last module-level binding wins and any
  `SPECS` the candidate authored is discarded. Without this an agent could
  inflate the `specs` subscore by declaring its own trivially-true checks.
- **Every constructor is imported under a `_bench_` alias.** The candidate's
  own module namespace is in scope at that point; an alias makes a
  same-named module-level function in the candidate script irrelevant.

`specs/project.py` is the project-scope rubric and is copied **wholesale** over
`<copy>/specs.py` — the shape of `examples/rocketry/specs.py:18-29`
(`check_interference_free`, `check_clearance`, `check_stackup`).

The vocabulary a task may use is exactly `toolkit/specs.py`'s, minus
`check_fem_static` (FR3: core tasks need no `[fem]`) — refused by the loader.

### 1.3 `reference/metrics.json` — the metric windows

```json
{
  "schema": 1,
  "windows": [
    {"name": "mass",    "part": "spacer_plate", "metric": "mass_g",    "max": 132.0, "min": 118.0},
    {"name": "height",  "part": "spacer_plate", "metric": "bbox_z_mm", "max": 6.05,  "min": 5.95},
    {"name": "solids",  "part": "spacer_plate", "metric": "n_solids",  "max": 1,     "min": 1},
    {"name": "material","part": "spacer_plate", "metric": "volume_mm3","max": 22100.0}
  ]
}
```

- `metric` is drawn from a **closed list** computed entirely from
  `worker._metrics` (`worker.py:364-415`) — **no new kernel call**:
  `volume_mm3`, `area_mm2`, `mass_g`, `n_solids`, `n_faces`, `n_edges`,
  `bbox_x_mm`, `bbox_y_mm`, `bbox_z_mm`, `com_x_mm`, `com_y_mm`, `com_z_mm`.
  The `bbox_*` and `com_*` keys are derived server-side from
  `metrics["bbox"]` / `metrics["center_of_mass"]`.
- `min` and `max` are both optional; at least one is required; both must be
  finite (`_finite`'s reasoning, `checks.py:205-233` — a NaN bound is not a
  loose bound, it is no bound).
- Windows are **inclusive** with the PRD-003 slack rule reused verbatim:
  `_slack(limit) = max(1e-9, abs(limit) * 1e-9)` (`specs.py:306-308`).

### 1.4 `benchmarks/` layout as a whole

```
benchmarks/
  tasks/<category>/<id>/...
  baseline.json                  # FR11 (§11)
  examples/submission-mfd-001/   # FR10/AC6: a checked-in external submission
  leaderboard/                   # FR12 input (§12)
      rows/<row-id>/row.json
      rows/<row-id>/report.json
```

`benchmarks/` is a top-level data directory resolved through
`agentcad._resources.resource_root()` — the same treatment as `examples/` and
`catalog/` (`cli.py:256`, `cli.py:297`), so no `pyproject.toml` change is
needed and it is **not** shipped in the wheel. `.dockerignore` gains
`benchmarks/` (it currently excludes `tests/`, `docs/`, `scripts/`, `.github/`;
without the entry the tasks would land in the multi-GB image).

---

## 2. Decision 2 — the loader validates the whole bundle before anything spawns

`agentcad/bench/tasks.py`, OCP-free, no service, no kernel:

```python
TASK_SCHEMA = 1
CATEGORIES = ("model_from_drawing", "modify_to_spec", "fix_the_broken_part",
              "assemble_and_clear", "optimize_under_constraints")
SUBSCORES = ("built", "valid", "specs", "geometry", "interference", "metrics")
ALIGN_MODES = ("world", "com", "bbox_center")
METRIC_KEYS = (...)                      # the closed list of §1.3

@dataclass(frozen=True)
class Task: ...                          # every field resolved to an absolute path
def tasks_root() -> Path                 # resource_root() / "benchmarks" / "tasks"
def load_task(task_id, root=None) -> Task
def load_tasks(root=None, *, glob=None, set_name=None) -> list[Task]
def task_problems(raw, base: Path) -> list[str]
```

`task_problems` is a **pure** function returning a list of human sentences, in
the style of `manifest_merge.config_problems` — so the CI self-test can assert
"every shipped task has zero problems" without constructing anything. It
checks, in this order (cheapest first, and never raising):

1. `schema == 1`; `id == f"{category}/{dirname}"`; `category` known; `version`
   a positive int; `task_set` a non-empty string.
2. `weights`: exactly `SUBSCORES` keys, each finite and `>= 0`, summing to
   `1.0 ± 1e-9`. (An all-zero weight map is refused: a task that grades nothing
   is not a task.)
3. `budgets.wall_s` finite, `> 0`; `budgets.turns` an int in
   `1 .. MAX_TOOL_CALLS_PER_TURN` — **imported from
   `agentcad.agent.chat`**, so raising the product's per-turn ceiling
   automatically raises what a task may declare (§8.3).
4. `frame.align` known; `len(rotations_deg) <= 8`; each a 3-list of finite
   floats; `frame.datum` a non-empty string.
5. Every declared path exists and lies **inside the task directory** (a
   `..`-escape is refused by resolving and re-checking with
   `checks._within`, `checks.py:157`).
6. `starter`, when present, and `reference/project` both hold a readable
   `project.json`.
7. `reference.steps` covers **every** `target.parts` entry whose `geometry`
   weight is non-zero, and each names an existing `.step`/`.stp`
   (`refload._BREP_EXTS`, `refload.py:27`) — never `.stl`, because the
   reference must be boolean-capable (FR5).
8. `reference.metrics` parses, `schema == 1`, every window names a part in
   `target.parts`, a `metric` in `METRIC_KEYS`, at least one finite bound, and
   `min <= max` when both are given. A `metrics` weight above zero with zero
   windows is refused.
9. `specs.parts` keys are all in `target.parts`; each file **binds `SPECS`**
   (`specs.declares_specs`, `specs.py:183` — AST, never exec) and contains no
   `check_fem_static`. A `specs` weight above zero with neither
   `specs.project` nor a non-empty `specs.parts` is refused.
10. `prompt` resolves to a non-empty UTF-8 file; every `assets` entry exists
    and has a suffix in `{".svg", ".md", ".txt", ".json", ".csv"}` — the
    text-attachable set (§8.4).

`load_task` raises `ValidationError` carrying the problem list in `details`;
the CLI maps that to exit 2 like every other `AppError` (`cmd_check`'s idiom,
`cli.py:1114-1116`).

---

## 3. Decision 3 — scoring runs on a copy, in a work cell, through a muzzled service

`agentcad/bench/scoring.py` owns one class:

```python
class Scorer:
    def __init__(self, service, registry=None): ...
    def score(self, task: Task, submission: Path, *,
              budget_s: float | None = None,
              work_dir: str | None = None) -> dict     # score.json
```

The lifecycle, per submission — deliberately `CheckRunner._run_ref`'s
(`checks.py:2033-2091`) shape:

1. **Refuse an overlapping work dir.** `refuse_work_dir_overlap(root,
   submission, projects_root)` (`checks.py:170`), *plus* the same refusal
   against the task directory and against `benchmarks/` — the packages gate's
   `_refuse_overlap` precedent, which also covers the package directory.
2. **Materialise a cell:** `mkdtemp(prefix="agentcad-bench-",
   dir=default_work_root(service))` (`checks.py:836`). The parent is the
   granted `agentcad-work-*` root, because since PRD-006 the shared temp dir is
   not a writable root and a confined worker cannot write `.cache/` under it.
   The run **removes only the cell it made** and never a directory handed to it.
3. **Copy the submission** into `<cell>/candidate/<project-name>` with
   `shutil.copytree(..., ignore=shutil.ignore_patterns(".cache", "exports",
   ".history"))` — `test_examples_golden.py:238-240`'s discipline, plus
   `.history` so a submission's git sidecar never travels.
4. **Inject the rubric** (§3.1).
5. **Open a muzzled ephemeral service** over the cell:
   `checks._ephemeral_service(cell, tree, self.service.kernel)`
   (`checks.py:864`). All three nullings apply and are load-bearing here for
   the same reasons: a `project_changed` publish would commit a snapshot, a
   live `branch_resolver` would write a `.history/agentcad/` sidecar into the
   copy, and `write_guard` would materialise a branch tree.
6. **Measure** the six subscores (§4).
7. **`shutil.rmtree(cell)`** in a `finally`. The caller's `--work-dir`, the
   submission and the task directory are untouched.

### 3.1 Rubric injection — the one textual mutation, and only in the copy

- `specs.project` → written over `<copy>/specs.py` (created if absent, replaced
  if present).
- Each `specs.parts[part_id]` → the candidate's `parts/<part_id>.py` becomes
  `candidate_text + "\n\n" + BLOCK_HEADER + "\n" + block_text + "\n"`, where
  `BLOCK_HEADER` is a fixed comment line naming the bench. The appended block
  re-binds `SPECS` (§1.2), so the candidate's own declarations are dropped.
- A `specs.parts` entry naming a part that does not exist in the copy is **not**
  an error: it is a missing part, which the `built` subscore already reports as
  zero (§4.8).

Injection happens **before** any build, so exactly one build per part serves
every subscore. Appending `SPECS` does not change `build(p)`, so the geometry
measured is the geometry the candidate authored; it does change the content
signature (`service._content_signature`, `service.py:673`) and therefore the
cache key, which is correct and costs nothing in a cold cell.

**The honest limitation, stated in `docs/bench.md`:** a candidate script could
monkeypatch `agentcad.toolkit.specs` in `sys.modules` before our import line
executes and fake the `specs` subscore. The bench is a **measurement, not a
security boundary** — the same sentence the publish gate carries. Two things
make it uninteresting: the ceiling is one subscore (geometry, metrics,
interference are measured from the built shape in the kernel and cannot be
faked without building the right shape), and every published row ships its
transcript and a reproducible submission, so a shortcut is inspectable. That is
the PRD's own contamination stance applied one level down.

---

## 4. Decision 4 — six subscores, each in `[0,1]`, each with an honest status

Every subscore is `{"value": float, "weight": float, "status": str,
"detail": {...}}`. `status ∈ {"ok", "skipped_mesh", "error", "not_applicable"}`.

### 4.1 `built`

`value = passed / len(target.parts)` where a part counts as passed when
`service._ensure_built(proj, part_id)` (`service.py:764`) returns `ok: true`.
A part absent from the copy's manifest counts as failed (never an error).
`detail = {"parts": n, "failed": [...sorted ids...]}`.
`status` is `"error"` only when `_ensure_built` **raises** — the defensive edge
`CheckRunner._build_item` guards at `checks.py:1591-1600`.

### 4.2 `valid`

Per-part, over the same build result's metrics:
`ok = metrics["is_valid"] is True`. `value = valid_parts / len(target.parts)`;
a part that did not build counts as invalid. This is `CheckRunner._build_item`'s
rule (`checks.py:1618-1627`) with its imported-geometry escape (1628-1638)
**deliberately absent**: a bench candidate that imports a mesh is measured, not
forgiven.
`detail = {"invalid": [...sorted...], "n_solids": {part: n}}`.

### 4.3 `specs`

One `SpecRunner.run(proj, deadline=…)` call on the ephemeral service
(`specs.py:1363`), reading `service.specs` **inside** the method exactly as
`CheckRunner._stage_specs` does (`checks.py:1873`).

`value = passed / (passed + failed + errors)` over the report's `checks` rows.
**`skip` rows are excluded from the denominator** — a skip is "we did not
measure", and a machine-specific skip must not silently score as a pass or a
fail (PRD-003's own rule, and `checks.exit_code`'s split at `checks.py:376-398`
reversed for a fraction). A denominator of zero with a non-zero `specs` weight
is `status: "error"` — the rubric declared checks and none were measured.

`detail = {"passed": n, "failed": [...sorted ids...], "skipped": [...],
"errors": [...], "total": n}`. Row ids are `assign_ids`' `"<part>:<name>"` /
`"project:<name>"` (`specs.py:269-288`).

**The specs report is never embedded.** `_report` stamps a `generated`
timestamp (`specs.py:1574`); embedding it would break byte-identity (FR6/AC3).

### 4.4 `geometry` — IoU through the new kernel handler

Applies per part in `target.parts` that has a `reference.steps` entry. Per part:

```python
result = kernel.request("iou", {
    "candidate": {"script": <copy's script>, "params": <effective params>},
    "reference": {"source": <abs path to reference STEP>},
    "align": task.frame.align,
    "rotations_deg": task.frame.rotations_deg or [[0.0, 0.0, 0.0]],
}, timeout_s=IOU_TIMEOUT_S, affinity=task.id)
```

The candidate side is built exactly as `service._shape_item` builds an assembly
item (`service.py:600-613`): `{"script", "params"}` for a script part,
`{"source"}` for a reference part. `affinity=task.id` routes every call for one
task to one worker (`pool._pick`, `pool.py:86-88`), so the reference STEP stays
in `refload`'s LRU (`refload.py:23-24`) and the candidate script stays in
`worker._SHAPE_CACHE` — the second call is a cache hit, not a rebuild.

`value = mean(iou per part)`, rounded to 6 dp.
`detail = {part: {"iou", "intersection_mm3", "union_mm3",
"candidate_volume_mm3", "reference_volume_mm3", "align", "rotation_deg"}}`.

Statuses:
- Candidate resolves mesh-only → `value = 0.0`, `status = "skipped_mesh"`
  (FR5, AC4). **Zero and included**, not excluded: being handed a mesh when a
  model was asked for is a fact about the candidate.
- Candidate did not build → `value = 0.0`, `status = "ok"` (§4.8).
- The worker raises (`ERROR_KERNEL`/`ERROR_TIMEOUT`) → `value = 0.0`,
  `status = "error"`, excluded from the weighted total (FR7).
  `detail.error = {"type", "message", "stage"}` from the worker payload.

### 4.5 `interference`

`service.check_interference(proj, min_volume=0.001,
timeout_s=<remaining budget>)` (`service.py:615`).
`value = clean_pairs / checked_pairs` where `checked_pairs = C(n, 2)` over the
resolved instances and `clean_pairs = checked_pairs - len(result["pairs"])`.
With fewer than two instances the call short-circuits to
`{"pairs": [], "checked": n}` (`service.py:630-631`) — with a non-zero
`interference` weight that is `value = 0.0` (the task asked for an assembly and
got none), not `not_applicable`.
`result["skipped_mesh"]` instances are named in `detail` and **counted as
un-clean**: an unmeasurable pair is not a clean pair.
`detail = {"checked": n, "pairs": [{"a","b","volume_mm3"}...sorted...],
"skipped_mesh": [...]}`.

### 4.6 `metrics`

`value = satisfied / len(windows)` over `reference/metrics.json`. Each window
reads its metric from the part's build result (`_ensure_built(...)["metrics"]`)
and compares inclusively with `_slack` (§1.3). A window whose part did not
build is unsatisfied. `detail = {"passed": n, "total": n,
"failed": [{"name", "measured", "min", "max"}...sorted by name...]}`.
`status = "error"` only when the build result raised.

### 4.7 `not_applicable` is a property of the task, never of the run

A subscore whose `task.json` weight is `0.0` is emitted as
`{"value": 0.0, "weight": 0.0, "status": "not_applicable",
"detail": {"reason": "weight_zero"}}` and is never measured (no kernel call, no
`check_interference`). Nothing at run time may *promote* a subscore to
`not_applicable`.

This is the single most important scoring rule and it exists to close an
exploit: since excluded subscores are renormalised away (FR4), a run-decided
exclusion would mean **a candidate improves its total by making a subscore
unmeasurable** — delete the part, break the build, hand back a mesh. So:

> **`error` is reserved for the harness failing to measure. A candidate that is
> absent, broken, mesh-only or simply wrong is measured, and it measures zero.**

### 4.8 The total

```
included = [s for s in subscores if s.status not in ("error", "not_applicable")]
W        = sum(s.weight for s in included)
total    = round(sum(s.value * s.weight for s in included) / W, 6)   if W > 0
```

The renormalised weights are published as `weights_effective` so a reader can
reproduce the arithmetic without knowing the rule. If `W == 0` — every subscore
excluded — `total` is `0.0`, a note says so, and **`bench score` exits 2**: we
could not produce a verdict, which is `checks.exit_code`'s meaning of 2 exactly
(`checks.py:376-398`).

---

## 5. Decision 5 — the `iou` handler: one boolean, solids-decomposed, guarded

New pack `agentcad/kernel/handlers/bench.py`, discovered by
`worker._load_handler_packs` (`worker.py:783-804`), registering exactly one
method: `iou`. **It is never registered as a model-facing tool** — there is no
`agentcad/core/tools_bench.py`, and that absence is the whole of the PRD's
"agent surface: deliberately none".

Written to `handlers/diff.py`'s template (`diff.py:29-117`): `register(toolbox)`
closure, sides resolved by a private `_side` mirroring `worker._item_shape`'s
grammar, every boolean guarded into a `WorkerError(ERROR_KERNEL, …,
{"stage": …})`.

### 5.1 Union without a union boolean

```
intersection = Σ_{i,j} volume(cand_solid_i & ref_solid_j)      # AABB-prefiltered
union        = volume(candidate) + volume(reference) - intersection
iou          = intersection / union
```

Only the **intersection** is ever booleaned. `|` on multi-solid Compounds is
exactly the operand shape `worker.py:650-653` warns about, and a union boolean
would double the OCCT failure surface for a number that arithmetic gives for
free.

Both sides are decomposed with `shape.solids() or [shape]` and prefiltered by
bounding box — `pairwise_interference`'s two reasons verbatim
(`worker.py:649-653`). Volumes come from `toolbox["shape_volume"]`
(`worker._shape_volume`, `worker.py:353`), never `.volume`.

**The pairwise sum equals `volume(A ∩ B)` exactly when A's solids are mutually
disjoint and B's are** — true of well-formed parts, false of a candidate whose
own solids overlap, where it over-counts. So the handler clamps:
`intersection = min(pairwise_sum, vol_a, vol_b)`, and `iou` is clamped to
`[0.0, 1.0]`. Both `candidate_solids` and `reference_solids` ride in the result
so a reader can see when the clamp could have bitten.

### 5.2 Alignment

`align` is applied to the **candidate** only; the reference is the datum.

| mode | candidate anchor | reference anchor |
|---|---|---|
| `world` | origin | origin |
| `com` | `shape.center(b3d.CenterOf.MASS)` (`worker._metrics`, `worker.py:374`) | same |
| `bbox_center` | `shape.bounding_box().center()` | same |

The transform is `translate(anchor_ref) ∘ rotate(r) ∘ translate(-anchor_cand)`,
composed as build123d `Location`s and applied with `shape.moved(loc)` —
`worker._place`'s mechanism (`worker.py:418-420`), intrinsic XYZ Euler degrees
everywhere. With `align="world"` and `r = [0,0,0]` this is the identity, so the
default (FR5: "as-built world coordinates") costs nothing.

**Scale is never normalised.** A part of the wrong size is a wrong part.

### 5.3 Permitted rotations

`rotations_deg` is the task's finite, declared list (default `[[0,0,0]]`). The
handler evaluates each, keeps the **maximum** IoU, and reports which one won.
It is the mechanical answer to the PRD's frame-alignment risk for genuinely
symmetric parts, and it is deterministic because the list is finite, ordered
and checked in order (ties keep the first). Capped at 8 by the loader: each
entry is a full boolean.

### 5.4 Result and errors

```json
{"intersection_mm3": 21344.216, "union_mm3": 23378.104, "iou": 0.913,
 "candidate_volume_mm3": 22012.0, "reference_volume_mm3": 22710.32,
 "candidate_solids": 1, "reference_solids": 1,
 "align": "com", "rotation_deg": [0.0, 0.0, 0.0],
 "status": "ok"}
```

- A mesh-kind side (`refload.load_reference` returns kind `"mesh"`,
  `refload.py:64`) short-circuits **before any boolean** — STL booleans
  segfault OCCT — returning `{"status": "skipped_mesh", "skipped_mesh":
  ["candidate"], "iou": 0.0, …}` with both per-side volumes still reported, the
  exact contract `handlers/diff.py:102-109` uses.
- Any OCCT failure is `WorkerError(ERROR_KERNEL, "iou unavailable: …",
  {"stage": "intersect" | "candidate_volume" | "reference_volume"})`
  (`diff.py:68-73`). The scorer turns it into `status: "error"` (FR7) — never a
  harness crash, never a silent zero.
- A reference file with a `.stl` suffix is refused by the **loader**
  (§2 rule 7), so the handler never has to explain a mesh datum.

`IOU_TIMEOUT_S = 300.0`, in `agentcad/bench/scoring.py`; when the scorer holds a
budget it passes `min(IOU_TIMEOUT_S, remaining)` and treats a resulting
`ERROR_TIMEOUT` below the ceiling as budget truncation, not as a red —
`CheckRunner._budget_broke`'s rule (`checks.py:1317-1331`).

---

## 6. Decision 6 — `score.json` is versioned, byte-identical, and timestamp-free

```json
{
  "schema": 1,
  "agentcad": "0.1.0",
  "harness": 1,
  "task": "model_from_drawing/mfd_001_spacer_plate",
  "task_set": "bench-v1",
  "task_version": 1,
  "category": "model_from_drawing",
  "total": 0.8421,
  "weights_effective": {
    "built": 0.15, "valid": 0.1, "geometry": 0.5, "metrics": 0.15, "specs": 0.1
  },
  "subscores": {
    "built": {"value": 1.0, "weight": 0.15, "status": "ok",
              "detail": {"parts": 1, "failed": []}},
    "geometry": {"value": 0.913, "weight": 0.5, "status": "ok",
                 "detail": {"spacer_plate": {"align": "world", "intersection_mm3": 21344.216,
                            "iou": 0.913, "rotation_deg": [0.0, 0.0, 0.0],
                            "union_mm3": 23378.104}}},
    "interference": {"value": 0.0, "weight": 0.0, "status": "not_applicable",
                     "detail": {"reason": "weight_zero"}},
    "metrics": {"value": 0.75, "weight": 0.15, "status": "ok",
                "detail": {"failed": [{"max": 6.05, "measured": 6.4, "min": 5.95,
                                       "name": "height"}], "passed": 3, "total": 4}},
    "specs": {"value": 1.0, "weight": 0.1, "status": "ok",
              "detail": {"errors": [], "failed": [], "passed": 3, "skipped": [],
                         "total": 3}},
    "valid": {"value": 1.0, "weight": 0.1, "status": "ok",
              "detail": {"invalid": [], "n_solids": {"spacer_plate": 1}}}
  },
  "notes": []
}
```

**Determinism rules (FR6/AC3), all enforced in one writer,
`agentcad/bench/report.write_json`:**

1. `json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"`,
   written through `ProjectStore._atomic_write` (`project.py:777`).
2. Every float is `round(x, 6)` **before** serialisation, applied recursively.
   OCCT is bit-deterministic on identical bytes (that is what
   `check --verify-determinism` relies on, `checks.py:830-833`), so this is not
   papering over jitter — it is making the published number stable across
   machines and readable by a human.
3. `allow_nan=False`: a NaN would serialise as the bare `NaN` literal no strict
   parser accepts (`checks.py:216-218`'s reasoning). A non-finite measurement
   is a `status: "error"` subscore instead.
4. Every list is sorted by a stated key (`failed` by id, `pairs` by
   `(a, b)`, `notes` lexically).
5. **No timestamp, no host, no path, no duration, no client id anywhere in the
   body** — the packages-provenance rule (`no timestamp/client id/absolute
   path`). Those live in the sibling `run.json` (§8.6).
6. `harness` is the scorer's own version, bumped whenever a subscore's
   computation changes. `task_version` is the task's. Two scores are comparable
   iff `(task_set, task_version, harness)` agree; `bench report` warns when
   they do not.

---

## 7. Decision 7 — the 25 tasks

Five per category. "Derived" tasks copy a bundled example's part scripts into
`starter/` or `reference/project/`; **the bundled examples are not registered in
the runner's service** (§8.2), so a derived task cannot be solved by reading
the answer.

Column *subscores* lists the non-zero weights; the weight sets are the category
defaults in §7.6 unless noted.

### 7.1 `model_from_drawing` (mfd) — empty project + an SVG drawing

| id | prompt gist | source | reference | subscores |
|---|---|---|---|---|
| `mfd_001_spacer_plate` | 80×50×6 plate, R5 corners, 4×Ø6 on a 60×30 grid; bottom face on Z=0, centred | authored | authored script + STEP | built·valid·specs·geometry·metrics |
| `mfd_002_angle_bracket` | 90×90 L-bracket, 80 wide, 10 thick, R6 inner fillet, 2×Ø14 per leg | derived: `construction/angle_bracket` (defaults) | example script + STEP | same |
| `mfd_003_head_flange` | Ø140 ring, Ø87 bore, 14 thick, 8×Ø9 on a Ø118 bolt circle, chamfered | derived: `rocketry/flange` (defaults) | example script + STEP | same |
| `mfd_004_shaft_collar` | Ø40/Ø20×15 collar, Ø5 cross hole, 3 mm clamp slit | authored (revolve + cut) | authored script + STEP | same |
| `mfd_005_vee_block` | 60×60×40 block, 90° V groove full length, 2×Ø8 through | authored | authored script + STEP | same |

Assets: a 3-view SVG per task, produced offline by our own drawing path
(`generate_drawing`, `agentcad/core/tools_drawing.py:30-52`, `format="svg"`) for
the derived tasks, hand-authored for the authored ones. Checked in as text.

### 7.2 `modify_to_spec` (mts) — a starter project, a stated target

| id | prompt gist | source | reference | notes |
|---|---|---|---|---|
| `mts_001_thin_the_nozzle` | get the chamber under 900 g without breaching the 0.8 mm wall minimum or changing the expansion ratio | derived: `rocketry/nozzle` | `wall = 2.0` variant | the README's known-good regression, inverted |
| `mts_002_bigger_pcb` | enclosure base for a 128×78 PCB: keep wall 2.5, bosses and standoffs must follow | derived: `prototyping/enclosure_base` | length 140 / width 90 variant | |
| `mts_003_gusset_pattern` | 4 rows at 60 pitch, Ø22 holes, edge distance ≥ 1.5·d | derived: `construction/gusset_plate` | param variant | manifest params differ from script defaults here — deliberate |
| `mts_004_lighter_flywheel` | ≤ 4.2 kg, keep Ø200 OD and the 6-bolt Ø56 circle | derived: `engine/flywheel` | thinned variant | |
| `mts_005_m10_clamp` | 64 mm clamp plate, M10 clearance, 10 thick, still clear of the tapped plate | derived: `fasteners/clamp_plate` | param variant | `interference` weight 0.10, geometry 0.20 |

### 7.3 `fix_the_broken_part` (fix) — a starter that is wrong

| id | what is broken | source | reference |
|---|---|---|---|
| `fix_001_contract` | `build(p)` returns `None`; one `PARAMS` key misspelt → `script_error` | authored | corrected script |
| `fix_002_fillet` | a fillet radius exceeds the local wall; OCCT fails the build | authored (bracket-shaped) | `safe_fillet` + reduced radius |
| `fix_003_wall_red` | builds fine; `check_wall(min_mm=2.0)` red because `wall` was cut to 1.2 | derived: `prototyping/enclosure_base` | `wall = 2.5` |
| `fix_004_hole_pattern` | a `GridLocations` off-by-one puts two holes off the plate — valid but wrong | derived: `construction/base_plate` | corrected layout |
| `fix_005_invalid_shell` | a `shell` thickness leaves a self-intersecting solid; `check_valid` red | authored | corrected thickness |

`fix` weights load `built`/`valid` heavily (§7.6) — the category is about
getting back to green.

### 7.4 `assemble_and_clear` (asm) — parts present, no instances

| id | prompt gist | source | rubric |
|---|---|---|---|
| `asm_001_thrust_chamber` | stack nozzle, flange and injector plate to the stated gaps | derived: `rocketry` (3 parts, instances stripped) | `specs/project.py` is `examples/rocketry/specs.py:20-29` almost verbatim |
| `asm_002_lid_on_base` | seat the lid on the base with ≥ 0.05 mm clearance | derived: `prototyping` | `check_interference_free` + `check_clearance` |
| `asm_003_bolted_joint` | stack tapped plate, clamp plate and cap screw; screw tip must clear the tapped thread | derived: `fasteners` | as above |
| `asm_004_truss_node` | place base plate, gusset (rotated) and two angle brackets clear of each other | derived: `construction` | `check_interference_free` |
| `asm_005_rod_and_piston` | assemble piston, wrist pin, rod body, rod cap and rod bolts | derived: `engine` (5 small parts, **not** the block) | `check_interference_free` + two clearances |

`asm_005` deliberately avoids `engine_block.py` (214 lines, the slowest example
part) — the category needs multiple instances, not the biggest ones.

### 7.5 `optimize_under_constraints` (opt) — an objective, mechanically graded

Geometry (IoU) is **`not_applicable`** for the whole category: there is no
unique correct shape, and demanding one would turn an optimisation into a copy
exercise. The objective is a **one-sided metric window** derived from the
reference solution's achieved value with a stated slack, so the score is
mechanical and the reference still scores 1.0.

| id | objective | constraints | source |
|---|---|---|---|
| `opt_001_lightest_bracket` | minimise `mass_g` (window: ≤ 1.05 × reference) | `check_wall(min_mm=4)`, hole pattern preserved, bbox within envelope | derived: `construction/angle_bracket` |
| `opt_002_stiffest_gusset` | maximise the throat section area (`check_that` on metrics; window on `volume_mm3`) | `plate_t ≤ 12`, `mass_g ≤ Y` | derived: `construction/gusset_plate` |
| `opt_003_thinnest_lid` | minimise `volume_mm3` (≤ 1.05 × reference) | `check_bbox(within_mm=[100,60,10])`, `check_wall(min_mm=1.6)` | derived: `prototyping/enclosure_lid` |
| `opt_004_most_bolts` | maximise `n_bolts` (window on `n_faces` ≥ reference) | the flange's own `check_wall(min_mm=2.0, name="bolt_circle_ligament")`, outer Ø ≤ 140 | derived: `rocketry/flange` |
| `opt_005_shortest_screw` | minimise `cap_screw.length` (window ≤ 1.05 × reference) | interference-free, `check_clearance ≥ 1.0 mm` engagement | derived: `fasteners` |

### 7.6 Category default weights (each row sums to 1.0)

| category | built | valid | specs | geometry | interference | metrics |
|---|---|---|---|---|---|---|
| `model_from_drawing` | 0.15 | 0.10 | 0.10 | 0.50 | 0.00 | 0.15 |
| `modify_to_spec` | 0.10 | 0.05 | 0.40 | 0.30 | 0.00 | 0.15 |
| `fix_the_broken_part` | 0.25 | 0.15 | 0.35 | 0.15 | 0.00 | 0.10 |
| `assemble_and_clear` | 0.10 | 0.05 | 0.35 | 0.00 | 0.40 | 0.10 |
| `optimize_under_constraints` | 0.10 | 0.05 | 0.45 | 0.00 | 0.00 | 0.40 |

Per-task overrides are allowed and must be argued in the task's `prompt.md`
front-matter comment; `mts_005` is the one v1 override (§7.2).

### 7.7 Reference STEP: checked in **and** regenerable

The ruling the orchestrator asked to be argued. **Both artefacts ship**, with
different jobs:

- `reference/project/` (the build123d script) is the **solution**. It is
  reviewable in a PR, it is what AC1 scores, and it is the existence proof that
  a task is solvable with the ordinary tool surface.
- `reference/steps/<part>.step` is the **datum**. It is what IoU measures
  against.

The datum is checked in rather than regenerated at scoring time because a
regenerated datum makes every published number a function of the installed
build123d. build123d is pinned and the test suite is the compat harness — but a
pin moves, and when it does, a regenerated reference would silently shift every
score with no version bump, which is precisely what FR6's
`{task_set, harness, agentcad}` stamping exists to prevent. STEP is ISO-10303-21
**text**, so it diffs and greps like the rest of the tree; a part is 20–200 KB
and 25 tasks are a few MB. Its header carries a timestamp, so STEP bytes are
never compared (the DXF lesson, `checks.py:796-800`).

The cost — drift between script and datum — is bought back by a CI self-test
(`AC1`, §14): re-export every `reference/project` part and compare against the
checked-in STEP with `iou ≥ 0.9999`, `|Δvolume|/volume ≤ 1e-6` and
`|Δbbox| ≤ 1e-3 mm`. Drift is then **loud and named**, never silent.

---

## 8. Decision 8 — the built-in runner drives `ChatEngine` from a budgeted client

`agentcad/bench/runner.py`:

```python
@dataclass(frozen=True)
class RunOutcome:
    over_budget: bool; stopped: str; usage: dict; transcript: list[dict]

def run_task(task: Task, *, service, registry, cell: Path,
             model: str, api_key: str | None = None,
             client_factory=None) -> RunOutcome
```

### 8.1 Scratch project lifecycle

One cell per task under the run's work root:
`<work-root>/agentcad-bench-<rand>/<category>__<id>/`, holding
`projects/` (the service's root), `submission/` (copied out after the turn) and
`transcript.json`. The **project name is `task.target.project`**, created either
by `service.create_project(name)` (`service.py:202`) when the task has no
starter, or by copying `starter/` in and `service.open_project(path)`
(`service.py:207`) when it has one.

The user's projects dir is never involved: `cmd_bench` calls `_build_service`
with the cell's `projects/` as the projects root, so `_writable_roots`
(`cli.py:194`) grants exactly that and the granted `agentcad-work-*` root, and
nothing else.

### 8.2 The bundled examples are switched off

`AGENTCAD_EXAMPLES=0` (`cli.py:270`) would work, but it is a process-global env
mutation and a bench run inside a pytest process would clobber a neighbour. So
`_build_service` gains **one keyword-only parameter**:

```python
def _build_service(projects_dir, extra_writable=None, *, posture=None,
                   examples: bool = True):
    ...
    if examples:
        _register_examples(service)
    _register_catalog(service)
```

The default preserves `cmd_check`, `cmd_serve`, `cmd_export` and `cmd_package`
byte-for-byte. The bench passes `examples=False`, so a task derived from
`examples/prototyping` cannot be solved by opening `examples/prototyping`.

**The catalog stays registered.** `assemble_and_clear` tasks legitimately reach
for fasteners, and the catalog is a shipped product surface — benching without
it would measure something other than the product.

### 8.3 Budgets: enforced in the client factory, not in the engine

`ChatEngine` has no wall-clock timeout and a hard-coded
`MAX_TOOL_CALLS_PER_TURN = 30` (`chat.py:50`, enforced at `chat.py:306-316`).
The runner adds budgets **without touching `chat.py`**, by wrapping the client:

```python
class _BudgetedClient:                 # exposes only `.messages.create(**kw)`
    """Refuses the next API call once any budget is spent."""
```

Before each `messages.create` it checks three things and raises
`BudgetExhausted` when any is spent:

1. **wall clock** — `time.monotonic() > deadline` (never `time.time()`: an NTP
   step must not move a budget, `checks.py:1208-1210`);
2. **tool calls** — counted from the request itself, as the number of
   `tool_use` blocks in `kwargs["messages"]`, so the count is derived from what
   the engine actually sent and needs no bus subscription;
3. **API turns** — its own call count, capped at `turns + 4` (derived, not
   declared: every API turn may issue at least one tool call, plus slack for
   text-only turns).

`BudgetExhausted` is caught by `ChatEngine`'s blanket handler
(`chat.py:317-327`), which repairs the history (`_repair_history`,
`chat.py:352`), publishes one `chat_delta` and fires `chat_done`. **The turn
ends cleanly, the transcript stays Messages-API-valid, and the on-disk state is
whatever the agent reached** — which is exactly AC8.

An outer `asyncio.wait_for(engine._tasks[turn_id], timeout=wall_s + grace)` is
the backstop for a *tool* that hangs (the client wrapper cannot preempt a call
already in flight — `agentcad check`'s honest limitation, `checks.py:1216-1221`,
restated here). On that timeout the runner cancels the task, records
`stopped = "wall_clock"` and scores anyway.

**One engine turn per task.** `task.json` may not declare `turns >
MAX_TOOL_CALLS_PER_TURN` (§2 rule 3), so the engine's own limit is never the
thing that stops a run and no continuation logic exists. This is not a
workaround, it is the PRD's design: *"the ordinary tool surface… the benchmark
measures the product surface as it ships"* (PRD lines 91-94). If 30 tool calls
turns out to be too tight, that is a **product** finding, raised in `chat.py`,
and the bench then measures the change. Multi-turn continuation is a Phase-2
seam (§16).

### 8.4 The prompt

The first (and only) user message is:

```
<prompt.md, verbatim>

--- attachment: assets/drawing.svg ---
<the SVG source, verbatim, in a fenced block>
```

`start_turn` refuses a non-`str` message (`chat.py:195-196`), and no request
path anywhere accepts an image block from a human. **v1 assets are therefore
text: SVG, Markdown, plain text, JSON, CSV** (§2 rule 10) — which is also
ruling 3's "drawing assets are SVG… no binary PNGs required". An SVG *is* the
drawing, in a form a language model reads better than a raster. Image-block
attachments are a Phase-2 seam requiring a `chat.py` signature change (§16), and
this is a **deliberate divergence from orchestrator ruling 6** (§17, D-13).

### 8.5 The transcript

`transcript.json`:

```json
{"schema": 1, "session": "main", "project": "bench_mfd_001_spacer_plate",
 "messages": [ ... engine.history(project) ... ]}
```

`ChatEngine.history` (`chat.py:175-178`) already returns plain JSON-safe dicts —
there is no serialisation helper and none is added. Two transforms before
writing:

- **Path redaction:** every occurrence of the cell root is replaced with
  `<cell>` and of the projects root with `<projects>`, so a published
  transcript carries no machine paths.
- **Image elision:** a `png_base64` value inside a tool result (produced by
  `render_view`, `chat.py:101-129`) is replaced with `"<image omitted>"` —
  the same string the bus event already uses (`chat.py:113-115`). A transcript
  is for reading, and a 2 MB base64 blob in a published artefact is not.

### 8.6 `run.json` — where everything non-deterministic lives

```json
{"schema": 1, "task": "model_from_drawing/mfd_001_spacer_plate",
 "agent": "builtin", "model": "claude-sonnet-5",
 "agentcad": "0.1.0", "harness": 1, "task_set": "bench-v1",
 "started": "2026-08-19T10:04:11Z", "finished": "2026-08-19T10:07:15Z",
 "duration_s": 184.2,
 "budgets": {"wall_s": 600, "turns": 24, "api_turns": 28},
 "usage": {"wall_s": 184.2, "tool_calls": 19, "api_turns": 11},
 "over_budget": false, "stopped": "model_ended_turn",
 "host": {"platform": "darwin", "python": "3.12.4"},
 "transcript": "transcript.json"}
```

`stopped ∈ {model_ended_turn, wall_clock, tool_calls, api_turns, error}`;
`over_budget` is `stopped != "model_ended_turn"` — which is AC8's flag.
Timestamps use `checks._now`'s formatter (`checks.py:236-240`): UTC, ISO-8601,
trailing `Z`.

### 8.7 Results directory

```
out/
  bench.json                    # run header + per-task index
  tasks/<category>/<id>/score.json | run.json | transcript.json | submission/
  report.json                   # written by `bench report --json-out`
  report.md                     # written by `bench report --md`
```

`submission/` is the final project directory copied out of the cell before it
is removed — the thing FR12 requires a leaderboard row to link, and the thing
`bench score` can be re-run against by anyone.

---

## 9. Decision 9 — the CLI

`agentcad/bench/cli.py` owns the whole surface and exports two functions;
`agentcad/cli.py` takes **two** edits.

### 9.1 The two edits to `agentcad/cli.py`

1. `_build_service` gains the keyword-only `examples: bool = True` (§8.2).
2. In `main()`: the metavar at `cli.py:1446-1448` gains `bench`, and

```python
from .bench.cli import add_bench_parser          # lazy, inside main()
add_bench_parser(sub)
...
elif args.command == "bench":
    raise SystemExit(cmd_bench(args))
```

Imported lazily so `agentcad serve` pays nothing for a package it does not use.

### 9.2 The argparse surface

```
agentcad bench run [--tasks GLOB] [--set NAME] [--agent builtin]
                   --report DIR [--model NAME] [--work-dir DIR]
                   [--budget SECONDS] [--quiet | --json]

agentcad bench score SUBMISSION --task ID [--tasks-dir DIR] [--out DIR]
                     [--work-dir DIR] [--budget SECONDS] [--quiet | --json]

agentcad bench report RESULTS [--baseline PATH] [--epsilon F]
                      [--md PATH] [--json-out PATH] [--quiet | --json]

agentcad bench publish LEADERBOARD [-o PATH] [--title TEXT]
```

Shared conventions, all borrowed rather than invented:

- `--tasks` is a glob over task ids (`"model_from_drawing/*"`, `"*/mfd_001*"`);
  `--set` selects by `task.json`'s `sets` membership. They compose (AND).
  Neither given → the whole `core` set.
- `--budget` uses `_finite_arg` (`cli.py:658`) — a NaN deadline bounds nothing.
- `--epsilon` uses `_finite_arg` for the same reason.
- `--work-dir` is resolved, refused and created by `_accept_work_dir`
  (`cli.py:161`) **before** `_build_service`, with the overlap guard bound to
  the submission, the task root and the projects root.
- `--quiet` / `--json` are the mutually exclusive pair `check` already has
  (`cli.py:1541-1545`).
- Identity is `locks.set_client_id("bench")` — `cmd_check`'s `"ci"` at
  `cli.py:1089`, one lane over, so a bench run never collides with a human's.

### 9.3 Exit codes

`check`'s table (`checks.exit_code`, `checks.py:376-398`), specialised:

| command | 0 | 1 | 2 |
|---|---|---|---|
| `bench run` | every selected task ran and was scored | — | harness: unknown task, service would not start, results unwritable |
| `bench score` | a score was produced | — | harness: unscoreable submission, unknown task, every subscore excluded (§4.8) |
| `bench report` | no baseline, or the baseline is met | **a regression beyond `--epsilon`** | harness: unreadable results, schema mismatch |
| `bench publish` | the page was written | **a row was rejected for incomplete disclosure** | harness: unreadable input, unwritable output |

`bench run` is deliberately never 1: an over-budget or low-scoring task is a
*measurement*, and turning it into a failing exit would make the runner and the
gate the same thing. FR11's gate is `bench report --baseline`, and only there.

Everything after the run — writing reports, printing the table — is inside the
same exit-code mapping as the run itself, because a traceback out of a CLI is
process exit 1, the code reserved for "the model is wrong"
(`cmd_check`'s note, `cli.py:1132-1137`).

### 9.4 `cmd_bench`'s skeleton (the PRD-004 pattern, verbatim)

```python
def cmd_bench(args) -> int:
    service = None
    try:
        work_dir = _accept_work_dir(args.work_dir, refuse)     # before the spawn
        service = _build_service(cell / "projects",
                                 extra_writable=[str(cell)] + ([work_dir] if work_dir else None),
                                 examples=False)
        locks.set_client_id("bench")
        registry = build_registry(service)
        ...
    except AppError as exc:  print(...); return 2
    except Exception as exc: print(...); return 2
    finally:
        if service is not None:
            try: service.kernel.stop()
            except Exception as exc: print(...)
            _release_work_root(service)
```

---

## 10. Decision 10 — `bench report`

`agentcad/bench/report.py`, pure over a results directory:

```json
{"schema": 1, "task_set": "bench-v1", "harness": 1, "agentcad": "0.1.0",
 "agent": "builtin", "model": "claude-sonnet-5",
 "n": 25, "total": 0.6124,
 "categories": {"model_from_drawing": {"total": 0.7412, "n": 5, "missing": 0}},
 "tasks": {"model_from_drawing/mfd_001_spacer_plate":
             {"total": 0.8421, "over_budget": false, "missing": false,
              "subscores": {"built": 1.0, "geometry": 0.913, "metrics": 0.75,
                            "specs": 1.0, "valid": 1.0}}},
 "warnings": [],
 "baseline": {"path": "benchmarks/baseline.json", "epsilon": 0.02,
              "status": "regressed",
              "regressions": [{"scope": "category:modify_to_spec",
                               "baseline": 0.71, "measured": 0.62, "delta": -0.09}]}}
```

**Aggregation rule:** a category total is the unweighted mean of its tasks'
totals; the overall total is the unweighted mean of the **category** totals. At
5-per-category the two coincide; stating it this way means a v2 that adds tasks
to one category does not silently reweight the headline number.

**A missing task scores 0.0 and is flagged `missing: true`,** and with
`--baseline` a missing task is a regression. Without that rule a release could
be gated green by not running the hard half.

`--md` renders the category table plus the worst rows, in
`checks.render_markdown`'s idiom (`checks.py:602-671`) — capped, with a
"…and N more" line (`checks._more`, `:598`).

Comparability: if any `score.json`'s `(task_set, task_version, harness)` differs
from the report header's, the row is included and a `warnings` sentence names
it. Scores from different harness versions are never silently averaged.

---

## 11. Decision 11 — `benchmarks/baseline.json` and the FR11 gate

```json
{"schema": 1, "task_set": "bench-v1", "harness": 1,
 "agent": "builtin", "model": "claude-sonnet-5", "agentcad": "0.1.0",
 "recorded": "2026-08-19", "source": "https://github.com/…/actions/runs/…",
 "total": 0.6124,
 "categories": {"model_from_drawing": 0.7412, "modify_to_spec": 0.7100,
                "fix_the_broken_part": 0.6800, "assemble_and_clear": 0.4900,
                "optimize_under_constraints": 0.4400},
 "tasks": {"model_from_drawing/mfd_001_spacer_plate": 0.8421}}
```

`bench report --baseline benchmarks/baseline.json --epsilon 0.02`:

- **Gates on `total` and on each category** — a drop greater than `epsilon`
  exits 1, naming every offending scope with baseline, measured and delta.
- **Per-task deltas are printed, never gated.** A single task under a
  stochastic agent is noise; gating on it would make the release gate a coin
  flip. This is the one place the design deliberately measures more than it
  enforces, and `docs/bench.md` says so.
- A baseline whose `(task_set, harness)` differs from the measured report is
  **exit 2**, not a pass: comparing across harness versions is not a comparison.
- A baseline naming tasks the results do not contain → those tasks are
  `missing` (§10) and count as full regressions.

The v1 `baseline.json` ships with `total: null` and a comment field until the
first real run records one; `bench report --baseline` on a null baseline is a
no-op that exits 0 and warns. (FR13's launch numbers are out of scope for this
PR — §16.)

---

## 12. Decision 12 — `bench publish`, and the full-disclosure rule as code

Input:

```
benchmarks/leaderboard/
  rows/<row-id>/row.json
  rows/<row-id>/report.json          # produced by `bench report --json-out`
```

`row.json`:

```json
{"schema": 1, "id": "builtin-sonnet5-2026-08-19",
 "agent": "AgentCAD built-in chat agent", "harness_command": "agentcad bench run --set core",
 "model": "claude-sonnet-5", "agentcad": "0.1.0", "harness": 1,
 "task_set": "bench-v1", "date": "2026-08-19",
 "config": {"kernel_pool_size": 1, "budget_scale": 1.0},
 "submission": "https://github.com/…/releases/download/…/submissions.tar.gz",
 "transcript": "https://github.com/…/releases/download/…/transcripts.tar.gz",
 "notes": ""}
```

**The full-disclosure rule is fail-closed and mechanical.** A row is rejected —
and `publish` exits 1 having written nothing — unless *all* of:

1. every key above is present, and every string one is non-empty (`notes` may
   be empty; `config` may be `{}` but must be present);
2. `report.json` validates against §10's schema;
3. `row.json`'s `task_set`/`harness`/`agentcad` equal `report.json`'s;
4. `submission` and `transcript` are absolute `https://` URLs or repo-relative
   paths that exist;
5. `report.json` covers **every** task in the declared `task_set` (a partial
   run is not a leaderboard row).

Rejection names the row and the failing rule. Nothing partial is written: the
page is rendered whole and written through `ProjectStore._atomic_write`.

Output: one **self-contained** HTML file (default `docs/bench/index.html`),
inline `<style>`, no script, no CDN, no web font — the constraint
`routes_share_public.py`'s public page already lives under. Content: a title, a
one-paragraph statement of what is measured and what is not, a sortable-by-eye
table (agent · model · agentcad · task set · per-category · total · date ·
submission · transcript), and a footer naming the exact command that reproduces
any row. Rows are ordered by `total` descending, ties by `id` ascending — a
stable order, so republishing the same input gives the same bytes.

---

## 13. Decision 13 — CI

A **new** workflow file, `.github/workflows/bench.yml`. It must not be a job in
`ci.yml`: `tests/test_prd006_acceptance.py:484-495` asserts
`ci.count("expect_sandbox: active") == 2` over that file, and
`tests/test_geometry_ci_action.py:151-157` asserts its apt block matches the
composite action's.

**Job `selftest`** — `pull_request` + `push`, `ubuntu-latest`, 45 min, **no
secrets** (`geometry-ci.yml:8-9`'s rule: a bench candidate script is arbitrary
Python and Linux has no seatbelt). Installs the OCCT libs the same way, then:

```bash
uv run pytest -q -n 2 --dist loadscope tests/test_bench_*.py tests/test_prd024_acceptance.py
```

This is where AC1 (every reference scores 1.0), AC2, AC3, AC4, AC7 and AC8 run.

**Job `builtin`** — `schedule` + `workflow_dispatch` + `push` to `main` only,
**never `pull_request`**, `macos-latest`, 60 min. It is the only thing in the
repo that touches a secret, and a fork PR must never reach it.

```yaml
  guard:
    runs-on: ubuntu-latest
    outputs: {has_key: "${{ steps.k.outputs.has_key }}"}
    steps:
      - id: k
        run: |
          if [ -n "${{ secrets.ANTHROPIC_API_KEY }}" ]; then echo "has_key=true" >> "$GITHUB_OUTPUT"
          else echo "has_key=false" >> "$GITHUB_OUTPUT"
               echo "::notice title=AgentCAD-Bench::ANTHROPIC_API_KEY is not configured; the built-in agent run was skipped."
          fi
  builtin:
    needs: guard
    if: needs.guard.outputs.has_key == 'true'
```

(The guard job exists because the `secrets` context is not available in a
job-level `if:`. When the key is absent the job is skipped **with a visible
notice**, not silently — ruling 1.)

Steps: `agentcad bench run --set fast --agent builtin --report bench-out`, then
`agentcad bench report bench-out --baseline benchmarks/baseline.json
--epsilon 0.05 --md "$GITHUB_STEP_SUMMARY" --json-out bench-out/report.json`,
then `actions/upload-artifact` for `bench-out`. The second command's exit code
is AC5's gate.

`.dockerignore` gains `benchmarks/`.

---

## 14. Testing strategy (parallel-safe, no network, green without `[fem]`)

All new tests live in `tests/` (`testpaths = ["tests"]`, `pyproject.toml:51`).
Anything exceeding the global 120 s timeout (`pyproject.toml:50`) carries an
explicit `@pytest.mark.timeout`. Every test taking the kernel uses the
session-scoped `kernel` fixture (`conftest.py:168`) and its own
`tmp_path_factory` projects root via `make_test_service` (`conftest.py:120`).

| file | covers |
|---|---|
| `tests/test_bench_tasks.py` | loader: every shipped task has zero `task_problems`; ids match directories; weights sum to 1; `turns <= MAX_TOOL_CALLS_PER_TURN`; a `..`-escape, a `.stl` reference, a `check_fem_static` and a missing window are each refused with a named sentence |
| `tests/test_bench_kernel_iou.py` | **AC4**: reference vs itself → `1.0`; two disjoint boxes → `0.0`; half-overlapping boxes → the analytic value; a mesh (STL) candidate → `status: "skipped_mesh"`, `iou 0.0`; a multi-solid compound; a rotated reference with `rotations_deg=[[0,0,90]]` → `1.0`, and without it → below 1.0; `align="com"` on a translated copy → `1.0`; the handler does **not** shadow a builtin and is **not** in `build_registry(service).list()` |
| `tests/test_bench_scoring.py` | **AC2**: a reference minus one hole scores below 1.0, with the geometry drop within tolerance of `hole_volume / union` and `specs.detail.failed` naming the exact check; **AC3**: two scorings of one submission are byte-identical; `error`/`not_applicable` exclusion and `weights_effective` arithmetic; a missing target part scores zero everywhere and is **never** `not_applicable` (§4.7); rubric injection discards a candidate's own `SPECS` |
| `tests/test_bench_runner.py` | **AC8** and the offline proof of the runner, on `tests/test_chat.py:39-66`'s fake-client pattern: a scripted client that creates a part → the on-disk state is scored; a client that never stops → `stopped: "tool_calls"`, `over_budget: true`, a score is still produced; a wall-clock budget of 0 → `stopped: "wall_clock"`; the transcript is written, path-redacted and image-elided; `examples=False` means `list_projects` shows only the scratch project |
| `tests/test_bench_report.py` | aggregation (mean of category means); a missing task is 0.0 and `missing`; `--baseline` exit codes 0/1/2; a harness-version mismatch is exit 2; markdown renders and caps |
| `tests/test_bench_publish.py` | **AC7**: three rows (built-in + two external) render; each disclosure rule rejects with a named message and exit 1 and writes nothing; the page is self-contained (no `http://`/`https://` asset URL, no `<script>`) and byte-identical on republish |
| `tests/test_bench_cli.py` | `bench score` / `report` / `publish` end to end through `cmd_bench`, exit codes, `--quiet`/`--json`, a refused `--work-dir` leaves nothing behind, the kernel is stopped and the work root released on every path |
| `tests/test_prd024_acceptance.py` | **AC1** (`@pytest.mark.slow`): every shipped task's `reference/project` scores exactly `1.0`; **the STEP-drift check** (re-export each reference part, compare volume/bbox/IoU to the checked-in datum); **AC6**: `benchmarks/examples/submission-mfd-001` is accepted and scored by `bench score`; **AC9**: no bench module imports `OCP`/`build123d` outside `agentcad/kernel/`; the workflow file asserts (`bench.yml` has the guard job, has no `pull_request` trigger on the `builtin` job) |

Parallel safety: every test builds its own service over `tmp_path_factory`;
nothing writes to `benchmarks/` (tasks are read-only inputs); no test mutates
`os.environ` globally (that is what `examples=False` bought). No test needs
network — the runner tests all inject a fake client, and `available` is `False`
without a key so a stray real call is impossible (`chat.py:164`, `:189-194`).
No test imports `gmsh`/`skfem`/`meshio`, so the suite is green without `[fem]`
(FR3/AC9).

---

## 15. Docs

- **`docs/bench.md`** (new, the FR10 home): what is measured and what is not;
  the task bundle format with the `task.json` of §1.1; the SPECS format as it
  actually exists; every subscore's computation; `score.json`'s schema; the
  four commands with their exit codes; the baseline and the release gate; the
  leaderboard and the full-disclosure rule; **the external-agent walkthrough** —
  `claude mcp add agentcad …`, hand the agent `prompt.md` and the SVG, let it
  work in a scratch project, then `agentcad bench score <dir> --task <id>` —
  with the checked-in example submission as the worked case; **task-authoring
  guidance** (declare the frame, pin the datums *and the part name* in the
  prompt, keep it solvable with the ordinary surface and without `[fem]` or
  network, argue any weight override); and the honest non-guarantees (§3.1's
  monkeypatch note, per-task deltas not gated, contamination stance).
- **`docs/roadmap.md`**: PRD-024 row → in-progress with the PR link on merge.
- **`docs/agent-api.md`**: one paragraph — the bench adds **no** tool, route or
  event, and the `iou` handler is kernel-internal by design, because a
  bench-only tool would contaminate the measurement.
- **`AGENTS.md`** (extension-point section) and **`CLAUDE.md`** (traps): a new
  condensed bullet, drafted in §17's ledger terms — `error` is the harness only;
  `not_applicable` is the task only; `weights_effective`; one boolean, never a
  union; the reference STEP is the datum and the script is the solution; the
  bench CI job never runs on `pull_request`; `benchmarks/` is not in the wheel
  and is in `.dockerignore`.
- **`docs/changelog/NNNN-*.md`** per commit, citing the `make test` count.

---

## 16. Phase 2 / 3 — designed, not built

| item | the seam that already exists for it | why not now |
|---|---|---|
| FR13 launch results (built-in + 2 external rows) | `benchmarks/leaderboard/rows/` and `bench publish`, which already accept and validate them | needs real paid runs and two external setups; the *renderer* is in scope, the *numbers* are not |
| `fem/` category | `check_fem_static` (`toolkit/specs.py:233`) is already refused by the loader by name; lifting the refusal plus an `importorskip` guard is the whole change | FR3 keeps core tasks extra-free; a skipped category in every published row is worse than no category |
| Task-set v2 rotation | `task_set` is stamped in `task.json`, `score.json`, `run.json`, the report, the baseline and every leaderboard row; a v2 set is a new directory and a new stamp | contamination policy needs at least one release of real data first |
| PRD-018 wiring | `bench report --json-out` is a stable machine artefact and `--baseline` is the objective function | PRD-018 is not built |
| Image (PNG) assets | `chat.py:195` refuses a non-`str` message; the change is a `start_turn(message: str \| list[dict])` widening plus a route field | a product change with its own review; SVG-as-text covers every v1 task (§8.4) |
| Multi-turn continuation | the runner already knows *why* a turn stopped (`RunOutcome.stopped`) | `turns <= MAX_TOOL_CALLS_PER_TURN` makes it unreachable in v1 (§8.3) |
| Voxel-IoU fallback | the handler's `status: "error"` path is already the honest degradation (FR7) | the PRD defers it until real boolean failures appear; adding it now would be a second scorer nobody has calibrated |
| Parallel task execution | none, deliberately | the packages lesson: the build fan-out and `--jobs` were **deleted** after failing a pre-registered bar and flipping a verdict. `bench run` is serial. |

---

## 17. SDD ledger — rulings of record

**R — rulings handed down by the orchestrator (recorded, not reopened):**

| # | ruling |
|---|---|
| R1 | Scope = PRD MVP + FR11 baseline gating + `bench publish` + the FR10 walkthrough with a checked-in example submission. FR13 launch results, `fem/`, task-set v2 rotation and PRD-018 wiring are seams only. AC5's real CI run is a secret-gated job that is skipped with a visible notice when the key is absent; the offline proof is a scripted fake client. |
| R2 | Package layout: OCP-free `agentcad/bench/` (`tasks.py`, `scoring.py`, `runner.py`, `report.py`, `publish.py`, `cli.py`); kernel scorer as a new pack `agentcad/kernel/handlers/bench.py` exposing `iou`, never a model-facing tool; tasks in-repo under `benchmarks/tasks/<category>/<id>/`; `benchmarks/baseline.json`. |
| R3 | 25 tasks, exactly 5 per category, mixing authored and example-derived; every reference scores 1.0 (AC1); solvable with the ordinary surface, no `[fem]`, no network; SVG drawings; authoring guidance in `docs/bench.md`. |
| R4 | Score schema per FR4: weights from `task.json`, subscores in `[0,1]`, per-subscore status `ok\|skipped_mesh\|error\|not_applicable`, `error`/`not_applicable` excluded with renormalised weights, byte-identical `score.json` on repeat, timestamps only in `run.json`. |
| R5 | No new model-facing tools, routes, events or manifest changes; only `agentcad/kernel/` imports OCP; `worker.py`/`tools.py`/`app.py`/`service.py` untouched; reuse `cli._build_service` without changing `check`'s behaviour. |
| R6 | Runner: fresh scratch project per task under a private work root, in-process headless service, `ChatEngine` with per-task budgets, prompt + asset as the first user message, transcript from `ChatEngine.history`, score on-disk state, no HTTP server. |
| R7 | Tests in `tests/test_bench_*.py` + `tests/test_prd024_acceptance.py`, parallel-safe, no network, green without `[fem]`; every changelog cites the `make test` count. |

**D — rulings made in this spec:**

| # | ruling | why |
|---|---|---|
| D1 | `starter/` and `reference/project/` are **complete AgentCAD project directories**; AC1 is literally `bench score reference/project`. | Removes a bespoke installer and makes the reference graded by the machinery it defines (§1). |
| D2 | The rubric (`specs/`, `reference/metrics.json`, weights) is **separate** from the reference geometry; reference part scripts carry no `SPECS`. | The reference must not be able to pass by declaring something no other submission is measured against (§1). |
| D3 | The task's part-scope SPECS are **appended to the candidate's script in the copy and re-bind `SPECS`**, discarding whatever the candidate declared; constructors are imported under `_bench_` aliases. | `_SPECS_TEXT_RE` already contemplates re-binding (`specs.py:179`); the packages gate's precedent is "make a manifest, don't bypass one". Without re-binding an agent inflates the `specs` subscore with its own trivial checks (§1.2, §3.1). |
| D4 | The bench is a **correctness measurement, not a security boundary** — a candidate could monkeypatch `agentcad.toolkit.specs`. Stated in `docs/bench.md`. | The publish gate's exact posture. The ceiling is one subscore; geometry/metrics/interference are kernel-measured and unfakeable; transcripts are published (§3.1). |
| D5 | **`error` is the harness failing to measure; `not_applicable` is declared by `task.json` (weight 0) and never by the run.** A candidate that is absent, broken, mesh-only or wrong measures **zero**. | Renormalisation (FR4) otherwise rewards destroying evidence: delete the part, break the build, hand back a mesh (§4.7). |
| D6 | `skip` rows are excluded from the `specs` denominator; a zero denominator with a non-zero weight is `error`. | A machine-specific skip must score as neither a pass nor a fail (§4.3). |
| D7 | IoU computes **only the intersection** as a boolean; `union = volA + volB − inter`. Both sides are solids-decomposed and AABB-prefiltered; `inter` is clamped to `min(Σ, volA, volB)` and `iou` to `[0,1]`. | `\|` on multi-solid Compounds doubles the OCCT failure surface for arithmetic that is free; the clamp bounds a candidate whose own solids overlap (§5.1). |
| D8 | Alignment modes are `world` (default) / `com` / `bbox_center`, applied to the candidate only, as `translate(anchor_ref) ∘ rotate(r) ∘ translate(−anchor_cand)`. **Scale is never normalised.** Rotations are a task-declared finite list (≤ 8); the max IoU wins, ties keep the first. | Deterministic, cheap, and the mechanical answer to the PRD's frame-alignment risk (§5.2–5.3). |
| D9 | **Both** the reference script and the checked-in reference STEP ship; the STEP is the scoring datum, the script is the solution, and a CI self-test re-exports and compares (IoU ≥ 0.9999, Δvolume ≤ 1e-6 rel, Δbbox ≤ 1e-3 mm). | A regenerated datum makes every published number a function of the build123d pin, and a pin move would shift scores with no version bump. STEP is ISO-10303-21 text, so it still reviews (§7.7). |
| D10 | Metric windows live in `reference/metrics.json` over a **closed list** of keys derived from `worker._metrics` — no new kernel call. | Keeps `task.json` the hand-authored rubric and the windows a regenerable fact about the reference (§1.3). |
| D11 | Budgets are enforced by a **client-factory wrapper** that refuses the next `messages.create` on wall clock, cumulative tool calls (counted from the request's own `tool_use` blocks) or API turns; `BudgetExhausted` is caught by `ChatEngine`'s blanket handler, so the turn ends cleanly with a repaired history. An outer `asyncio.wait_for` backstops a hung tool. | `chat.py` needs no change, the transcript stays valid, and the on-disk state is scoreable — AC8 exactly (§8.3). |
| D12 | `task.json` may not declare `turns > MAX_TOOL_CALLS_PER_TURN` (imported from `agentcad.agent.chat`), so there is **exactly one engine turn per task** and no continuation logic. | The PRD's own design: the benchmark measures the product surface as it ships. Raising the ceiling is a product change the bench then measures (§8.3). |
| D13 | **Divergence from R6:** v1 assets are text only (SVG/MD/TXT/JSON/CSV); no PNG image block. | `chat.py:195` refuses a non-`str` message and no request path accepts an image from a human; adding one is a product change with its own review. SVG *is* the drawing, and R3 already says no binary PNGs are required. Flagged for the orchestrator (§8.4, §16). |
| D14 | `_build_service` gains **one keyword-only `examples: bool = True`**; the bench passes `False`. The catalog stays registered. | Env mutation (`AGENTCAD_EXAMPLES=0`) is process-global and would clobber a neighbour under xdist. A derived task must not be solvable by opening the example; a fastener task legitimately needs the catalog (§8.2). |
| D15 | Determinism: `sort_keys`, `indent=2`, `allow_nan=False`, recursive `round(x, 6)`, every list sorted by a stated key, **no timestamp/host/path/duration in `score.json`** — those live in `run.json`. | FR6/AC3, and the packages-provenance rule (§6). |
| D16 | Exit codes: `run` is 0/2 only; `score` is 0/2; `report` is 0/**1**/2 (1 = regression, the FR11 gate); `publish` is 0/**1**/2 (1 = a rejected row). All-subscores-excluded is exit 2. | `check`'s table, specialised. The runner and the gate must not be the same thing (§9.3). |
| D17 | `bench report --baseline` gates on **total and per-category only**; per-task deltas are printed, never gated. A **missing task is 0.0 and a regression**. | A single task under a stochastic agent is noise; but gating green by not running the hard half must be impossible (§10, §11). |
| D18 | The overall total is the **mean of category means**, not the mean of tasks. | A v2 that adds tasks to one category must not silently reweight the headline number (§10). |
| D19 | `bench publish` is **fail-closed and atomic**: five disclosure rules, a rejected row is exit 1 with nothing written, rows ordered `total` desc then `id` asc so republishing is byte-stable; output is self-contained HTML, no script, no CDN, no web font. | FR12's "a row without full disclosure is not accepted", made mechanical (§12). |
| D20 | Bench CI is a **separate workflow file**; the secret-using job never runs on `pull_request`, and the "is the key set" question is answered by a `guard` job whose output feeds `if:`. | `ci.yml` is asserted byte-wise by `test_prd006_acceptance.py:484-495`; and `geometry-ci.yml:8-9`'s rule — a candidate script is arbitrary Python and Linux has no seatbelt (§13). |
| D21 | **No parallel task execution and no `--jobs`.** | The packages lesson: the build fan-out was deleted after failing a pre-registered bar and flipping a verdict under `--budget` (§16). |
| D22 | `benchmarks/` is resolved through `resource_root()` like `examples/`/`catalog/` (no `pyproject.toml` change, not in the wheel) and is added to `.dockerignore`. | Matches the shipped precedent; without the `.dockerignore` entry the tasks land in the multi-GB image (§1.4). |
| D23 | Scoring identity is `locks.set_client_id("bench")`. | One lane over from `cmd_check`'s `"ci"` (`cli.py:1089`), so a bench run never collides with a human's checkout (§9.2). |
| D24 | **`publish` rule 4, narrowed:** a non-`https://` link is read as a path **relative to the row's own directory** and must stay inside it (textually — no `..` component, no absolute path, no scheme — *and* after `resolve()`, so a symlink cannot walk out either) and must exist there. Only an `https://` link renders as an anchor; a relative one renders as `<code>` text. | §12 says "repo-relative paths that exist", which leaves `../../../etc/passwd` publishable and makes the rendered link depend on where the page was written. The row directory is the only base that is the same fact for the validator and for the reader (Task 7 review, round 1; `docs/bench.md` states it for submitters). |

---

## 18. Risks

1. **Frame ambiguity is mitigated, not solved.** `frame.datum` is prose in the
   prompt, `align` and `rotations_deg` are mechanical, and task review is PR
   review. A correct part in an undeclared pose still scores badly — and that
   is the *task author's* bug, which AC1 does not catch (the reference is in
   the right pose by construction). The mitigation is the authoring checklist
   in `docs/bench.md` and a reviewer who reads `frame.datum` against
   `prompt.md`.
2. **The `specs` subscore is the softest number.** D3/D4 bound the exposure to
   one subscore; the transcript is the audit trail. If a real shortcut ever
   appears, the answer is a `format`-style red row, not a sandbox.
3. **25 references × build + IoU + specs is the CI cost of AC1.** Estimated
   5–20 s per task on `ubuntu-latest`; the `selftest` job is budgeted 45 min
   with the whole thing marked `slow`. If it grows past that, the honest lever
   is `--set fast` in CI and the full set on the schedule — the split
   `ci.yml`'s `test`/`exhaustive` jobs already use.
4. **One engine turn per task may prove too tight** for `assemble_and_clear`
   and `optimize_under_constraints`. That is a *finding*, not a defect: it is
   reported as `stopped: "tool_calls"` in every affected `run.json`, and the
   fix is a product change to `MAX_TOOL_CALLS_PER_TURN` that the bench then
   measures (D12).
5. **A stochastic agent against a per-category gate.** `--epsilon` is the only
   knob, and 0.05 on a 5-task category mean is loose. The mitigation is that
   the gate is advisory on the schedule and blocking only on a release branch;
   the alternative — many samples per task — is unaffordable and is not
   proposed.
6. **Contamination.** The PRD's stance stands and is repeated in `docs/bench.md`:
   versioned task sets, published transcripts, a rotation policy in Phase 3.
   Public-and-reproducible beats secret-and-unverifiable for this purpose, and
   the docs say so out loud rather than implying a secrecy we do not have.
