# AgentCAD-Bench Implementation Plan (PRD-024 MVP + FR10–FR12)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first public, kernel-scored agentic-CAD benchmark — 25 tasks, a mechanical scorer with no LLM judging anywhere, a budgeted runner over the built-in chat agent, a deterministic `score.json`, a baseline release gate, and a static leaderboard — without adding a single model-facing tool, route, event or manifest key.

**Architecture:** One OCP-free package `agentcad/bench/` (loader, scorer, runner, report, publish, authoring helper) reusing the PRD-004 headless-service pattern (`cli._build_service` → warm kernel, no server) and the geometry-CI muzzled-copy pattern (`checks._ephemeral_service`: `bus.on_publish = None`, `branch_resolver = None`, `write_guard = None`). One new worker handler pack `agentcad/kernel/handlers/bench.py` exposing a single kernel-internal `iou` method. Tasks are data under `benchmarks/`. `agentcad/cli.py` takes exactly two edits. No edits to `worker.py` / `tools.py` / `app.py` / `service.py`.

**Tech Stack:** Python 3.12 + uv, build123d/OCCT (kernel process only), FastAPI (untouched here), `anthropic` SDK (runner, injected), pytest (parallel, session-scoped `kernel` fixture). No new dependency.

**Design spec:** `docs/superpowers/specs/2026-08-19-agentcad-bench-design.md` — read it first. Every "Decision N" and "D-N" below refers to it.

**Slice count (11) & justification:** the feature spans a data format, a kernel handler, a scorer, a CLI, an agent runner, two reporters and 25 authored artefacts. Tasks 1–7 are code (each a testable vertical slice); Tasks 8–10 are pure authoring, split three ways so a slow AC1 self-test never blocks a code slice; Task 11 is acceptance + CI + docs. **Recommended PR seam:** Tasks 1–4 (format + scorer + `bench score`, fully offline) as PR-A; Tasks 5–11 (runner, reporting, leaderboard, tasks, docs) as PR-B.

## Global Constraints

- **Only `agentcad/kernel/` imports OCP/build123d.** `agentcad/bench/**` must import neither, ever. Task 11 has a test that proves it.
- **Never edit** `worker.py` / `tools.py` / `app.py` / `service.py`. `agentcad/cli.py` gets exactly two changes, both specified in Task 4; nothing else in it moves.
- **No model-facing surface.** No `agentcad/core/tools_bench.py`, no `routes_bench.py`, no new event type, no new error type, no manifest key. The `iou` handler is kernel-internal; a test asserts it is absent from `build_registry(service).list()`.
- **`error` is the harness failing to measure; `not_applicable` is declared by `task.json` (weight 0) and never by a run.** A candidate that is absent, broken, mesh-only or wrong measures **zero** (design D5). Getting this backwards rewards destroying evidence.
- Boolean intersection uses the **`&` operator**; volumes come from `toolbox["shape_volume"]` (`worker._shape_volume`, `worker.py:353`), never `.volume` — nested `Compound.volume` undercounts. **Never `|`** (design D7).
- Rotations are **intrinsic XYZ Euler degrees** everywhere; compose with build123d `Location` in the kernel (`worker._place`, `worker.py:418-420`).
- **An STL/mesh side is never booleaned** — it segfaults OCCT. Short-circuit before any boolean (`handlers/diff.py:102-109`).
- Deadlines use `time.monotonic`, never `time.time` (an NTP step must not move a budget).
- Tests must be **parallel-safe**: session-scoped `kernel` fixture (`tests/conftest.py:168`), own projects root via `tmp_path_factory` (`conftest.py:120` `make_test_service`), **never** mutate `os.environ` globally, never write into `benchmarks/` (it is a read-only input), never touch `~/AgentCAD`. Anything over the global 120 s timeout (`pyproject.toml:50`) carries an explicit `@pytest.mark.timeout`.
- **No network in any test.** `ChatEngine.available` is `False` without a key (`chat.py:164`), and every runner test injects a fake client.
- **The core suite must stay green without the `[fem]` extra.** No bench module may import `gmsh`/`skfem`/`meshio`, and `check_fem_static` is refused by the task loader by name.
- **Do NOT run `uv sync` / `uv pip install`.** No new dependency is needed. Use the existing venv (`uv run …`).
- **Do NOT run mutating `git` commands.** Read-only `git log`/`git diff`/`git status` are fine. Each task's final step *prepares* a commit — writing the changelog file and staging nothing; **the orchestrator commits.**
- **Every task writes `docs/changelog/NNNN-<slug>.md`** with the number given in that task, from the actual diff, following `docs/changelog/README.md`. The implementer **cites the targeted test output** in the changelog and in its final report; the **orchestrator** fills in the full `make test` count before committing.

---

## File map

**New package** (all OCP-free):
- `agentcad/bench/__init__.py` — `HARNESS_VERSION`.
- `agentcad/bench/_json.py` — canonical/deterministic JSON read+write (the `core/packages/_json.py` precedent).
- `agentcad/bench/tasks.py` — schema constants, `Task`/`Frame`/`Budgets`/`MetricWindow`, `task_problems`, `load_task`, `load_tasks`, `prompt_text`.
- `agentcad/bench/scoring.py` — `Scorer`, rubric injection, the six subscores, `score.json`.
- `agentcad/bench/runner.py` — `BudgetedClient`, `RunOutcome`, `run_task`, transcript + `run.json`.
- `agentcad/bench/report.py` — `aggregate`, `compare_baseline`, `render_markdown`.
- `agentcad/bench/publish.py` — `row_problems`, `render_leaderboard`, `publish`.
- `agentcad/bench/cli.py` — `add_bench_parser`, `cmd_bench`.
- `agentcad/bench/author.py` — dev helper: export reference STEPs, seed `metrics.json`, render drawing SVGs. Run as `uv run python -m agentcad.bench.author`; **not** an `agentcad` subcommand.

**New kernel pack:**
- `agentcad/kernel/handlers/bench.py` — the `iou` method.

**Modified:**
- `agentcad/cli.py` — `_build_service` gains keyword-only `examples: bool = True` (:48-136); `main()` gains the `bench` subparser + dispatch (:1446-1448, :1688-1708).

**Data:**
- `benchmarks/tasks/<category>/<id>/…` (25 tasks), `benchmarks/baseline.json`, `benchmarks/examples/submission-mfd-001/`, `benchmarks/leaderboard/rows/…`.

**Docs / CI:**
- `docs/bench.md` (new), `docs/agent-api.md`, `docs/architecture.md`, `docs/geometry-ci.md` (cross-ref), `AGENTS.md`, `CLAUDE.md`, `docs/roadmap.md`, `.github/workflows/bench.yml`, `.dockerignore`.

**Tests:**
- `tests/test_bench_tasks.py`, `test_bench_kernel_iou.py`, `test_bench_scoring.py`, `test_bench_cli.py`, `test_bench_runner.py`, `test_bench_report.py`, `test_bench_publish.py`, `tests/test_prd024_acceptance.py`.

---

## Task 1: Package skeleton, task schema + loader, the authoring helper, and one complete seed task (Decisions 1, 2)

**Files:**
- Create: `agentcad/bench/__init__.py`, `agentcad/bench/_json.py`, `agentcad/bench/tasks.py`, `agentcad/bench/author.py`
- Create: `benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/` (complete bundle)
- Create: `tests/test_bench_tasks.py`
- Reference: `agentcad/core/packages/_json.py` (the read-with-a-size-cap precedent), `agentcad/core/checks.py:157` (`_within`), `agentcad/core/specs.py:183` (`declares_specs`), `agentcad/core/specs.py:306` (`_slack`), `agentcad/agent/chat.py:50` (`MAX_TOOL_CALLS_PER_TURN`), `agentcad/_resources.py` (`resource_root`)

**Interfaces:**
- Produces `agentcad/bench/__init__.py`: `HARNESS_VERSION: int = 1`.
- Produces `agentcad/bench/_json.py`:
  - `round_floats(value, places: int = 6)` — recursive; ints and bools untouched.
  - `canonical_json(payload: dict) -> bytes` — `json.dumps(round_floats(payload), sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"`.
  - `write_json(path: Path, payload: dict) -> None` — `canonical_json` through `ProjectStore._atomic_write`.
  - `read_json(path: Path, *, max_bytes: int = 4 << 20) -> dict` — refuses by size **before** parsing; catches `ValueError` **and `RecursionError`** and re-raises `ValidationError`.
- Produces `agentcad/bench/tasks.py`:
  - `TASK_SCHEMA = 1`; `METRICS_SCHEMA = 1`; `MAX_ROTATIONS = 8`
  - `CATEGORIES = ("model_from_drawing", "modify_to_spec", "fix_the_broken_part", "assemble_and_clear", "optimize_under_constraints")`
  - `SUBSCORES = ("built", "valid", "specs", "geometry", "interference", "metrics")`
  - `ALIGN_MODES = ("world", "com", "bbox_center")`
  - `METRIC_KEYS = ("volume_mm3", "area_mm2", "mass_g", "n_solids", "n_faces", "n_edges", "bbox_x_mm", "bbox_y_mm", "bbox_z_mm", "com_x_mm", "com_y_mm", "com_z_mm")`
  - `ASSET_SUFFIXES = (".svg", ".md", ".txt", ".json", ".csv")`
  - `@dataclass(frozen=True) class Frame: align: str; rotations_deg: tuple; datum: str`
  - `@dataclass(frozen=True) class Budgets: wall_s: float; turns: int; api_turns: int`
  - `@dataclass(frozen=True) class MetricWindow: name: str; part: str; metric: str; min: float | None; max: float | None`
  - `@dataclass(frozen=True) class Task` — fields `id, schema, task_set, version, category, title, sets, authored_against, source, root, prompt_path, asset_paths, starter_dir, target_project, target_parts, budgets, frame, reference_project, reference_steps, metrics_path, specs_project_path, specs_part_paths, weights`
  - `tasks_root() -> Path`
  - `task_problems(raw: dict, base: Path) -> list[str]` — **pure**, never raises, returns human sentences
  - `load_task(task_id: str, root: Path | None = None) -> Task` — raises `ValidationError(msg, {"problems": [...]})`
  - `load_tasks(root=None, *, glob: str | None = None, set_name: str | None = None) -> list[Task]` — sorted by `id`
  - `load_windows(path: Path) -> list[MetricWindow]`
  - `prompt_text(task: Task) -> str`
- Produces `agentcad/bench/author.py`: `export_reference(task_dir: Path, *, service) -> dict`, `seed_metrics(task_dir: Path, *, service, tolerance: float = 0.01) -> Path`, `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Failing test — the loader accepts the seed task and rejects malformed ones**

```python
# tests/test_bench_tasks.py
import json
import pytest
from pathlib import Path

from agentcad.bench import tasks as bench_tasks
from agentcad.core.model import ValidationError

SEED = "model_from_drawing/mfd_001_spacer_plate"


def test_seed_task_loads_and_is_fully_resolved():
    task = bench_tasks.load_task(SEED)
    assert task.id == SEED
    assert task.category == "model_from_drawing"
    assert task.task_set == "bench-v1"
    assert task.target_parts == ("spacer_plate",)
    assert task.prompt_path.is_file()
    assert task.reference_project.joinpath("project.json").is_file()
    assert task.reference_steps["spacer_plate"].suffix.lower() in (".step", ".stp")
    assert task.metrics_path.is_file()
    assert abs(sum(task.weights.values()) - 1.0) < 1e-9
    assert task.frame.align in bench_tasks.ALIGN_MODES


def test_every_shipped_task_has_zero_problems():
    root = bench_tasks.tasks_root()
    found = sorted(p for p in root.glob("*/*/task.json"))
    assert found, "no tasks are shipped"
    for path in found:
        raw = json.loads(path.read_text())
        problems = bench_tasks.task_problems(raw, path.parent)
        assert problems == [], f"{path.parent.name}: {problems}"
        assert raw["id"] == f"{path.parent.parent.name}/{path.parent.name}"


def _seed_raw(tmp_path: Path) -> tuple[dict, Path]:
    """A copy of the seed bundle a test may mutate."""
    import shutil
    src = bench_tasks.tasks_root() / "model_from_drawing" / "mfd_001_spacer_plate"
    dst = tmp_path / "mfd_001_spacer_plate"
    shutil.copytree(src, dst)
    return json.loads((dst / "task.json").read_text()), dst


@pytest.mark.parametrize("mutate, needle", [
    (lambda r: r.__setitem__("schema", 2), "schema"),
    (lambda r: r["weights"].__setitem__("geometry", 0.9), "sum to 1"),
    (lambda r: r["budgets"].__setitem__("turns", 999), "MAX_TOOL_CALLS_PER_TURN"),
    (lambda r: r["frame"].__setitem__("align", "principal_axes"), "align"),
    (lambda r: r.__setitem__("prompt", "../../../etc/passwd"), "outside the task"),
    (lambda r: r["reference"]["steps"].__setitem__("spacer_plate", "reference/steps/x.stl"), "STEP"),
    (lambda r: r["frame"].__setitem__("rotations_deg", [[0, 0, i] for i in range(9)]), "at most 8"),
])
def test_task_problems_names_each_defect(tmp_path, mutate, needle):
    raw, base = _seed_raw(tmp_path)
    mutate(raw)
    problems = bench_tasks.task_problems(raw, base)
    assert any(needle in p for p in problems), problems


def test_specs_block_must_bind_SPECS_and_may_not_use_fem(tmp_path):
    raw, base = _seed_raw(tmp_path)
    block = base / "specs" / "parts" / "spacer_plate.py"
    block.write_text("from agentcad.toolkit.specs import check_wall\nx = 1\n")
    assert any("SPECS" in p for p in bench_tasks.task_problems(raw, base))
    block.write_text(
        "from agentcad.toolkit.specs import check_fem_static as _f\n"
        "SPECS = [_f('a', 'b', 1.0)]\n")
    assert any("check_fem_static" in p for p in bench_tasks.task_problems(raw, base))


def test_load_task_raises_with_the_problem_list():
    with pytest.raises(ValidationError) as exc:
        bench_tasks.load_task("model_from_drawing/does_not_exist")
    assert "does_not_exist" in exc.value.message


def test_load_tasks_filters_by_glob_and_set():
    assert [t.id for t in bench_tasks.load_tasks(glob="model_from_drawing/*")] == [SEED]
    assert bench_tasks.load_tasks(set_name="core")
    assert bench_tasks.load_tasks(set_name="no-such-set") == []


def test_prompt_text_inlines_every_asset_as_text():
    task = bench_tasks.load_task(SEED)
    text = bench_tasks.prompt_text(task)
    assert task.prompt_path.read_text().strip() in text
    assert "attachment: assets/drawing.svg" in text
    assert "<svg" in text


def test_canonical_json_is_byte_identical_and_refuses_nan():
    from agentcad.bench._json import canonical_json
    payload = {"b": 1 / 3, "a": [3.0000000001, 2]}
    assert canonical_json(payload) == canonical_json(dict(payload))
    assert b'"b": 0.333333' in canonical_json(payload)
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: agentcad.bench`).
  `uv run pytest tests/test_bench_tasks.py -x -q`

- [ ] **Step 3: Implement `agentcad/bench/__init__.py` and `_json.py`**

```python
# agentcad/bench/__init__.py
"""AgentCAD-Bench: kernel-scored agentic-CAD evaluations (PRD-024).

OCP-free by contract: nothing in this package may import build123d or OCP.
The only geometry this feature adds lives in agentcad/kernel/handlers/bench.py.
"""
from __future__ import annotations

#: The scorer's own version. Bump it whenever a subscore's computation changes:
#: two scores are comparable only when (task_set, task_version, harness) agree.
HARNESS_VERSION = 1
```

```python
# agentcad/bench/_json.py
"""Deterministic JSON in, deterministic JSON out.

Every bench artefact goes through here so byte-identity (FR6/AC3) is a property
of one module rather than of every call site. `json.loads` raises
**RecursionError** on deeply nested input, and RecursionError is not a
ValueError -- the trap `core/packages/_json.py` was written for.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.model import ValidationError
from ..core.project import ProjectStore


def round_floats(value, places: int = 6):
    """Recursively round every float. Bools are ints and are left alone."""
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, dict):
        return {key: round_floats(item, places) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_floats(item, places) for item in value]
    return value


def canonical_json(payload: dict) -> bytes:
    """Sorted keys, fixed indent, six decimals, no NaN, trailing newline.

    `allow_nan=False` because a NaN serialises as the bare `NaN` literal, which
    no strict parser accepts -- and a non-finite measurement is a `status:
    "error"` subscore, never a number.
    """
    text = json.dumps(round_floats(payload), sort_keys=True, indent=2,
                      allow_nan=False)
    return (text + "\n").encode()


def write_json(path, payload: dict) -> None:
    ProjectStore._atomic_write(Path(path), canonical_json(payload))


def read_json(path, *, max_bytes: int = 4 << 20) -> dict:
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ValidationError(f"cannot read {target}: {exc}",
                              {"path": str(target)}) from exc
    if size > max_bytes:
        raise ValidationError(
            f"{target} is {size} bytes, above the {max_bytes}-byte limit; "
            f"it is refused before parsing", {"path": str(target)})
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, RecursionError, UnicodeDecodeError) as exc:
        raise ValidationError(f"{target} is not readable JSON: {exc}",
                              {"path": str(target)}) from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{target} must hold a JSON object",
                              {"path": str(target)})
    return value
```

- [ ] **Step 4: Implement `agentcad/bench/tasks.py`** — the dataclasses and constants from the Interfaces block, then `task_problems`, which runs the ten checks of design §2 **in order, cheapest first, never raising**:

```python
def _inside(base: Path, rel: str) -> Path | None:
    """`base/rel` resolved, or None when it escapes the task directory."""
    try:
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    return target if _within(target, base.resolve()) else None   # checks._within


def task_problems(raw, base: Path) -> list[str]:
    out: list[str] = []
    if not isinstance(raw, dict):
        return ["task.json must hold a JSON object"]
    if raw.get("schema") != TASK_SCHEMA:
        out.append(f"schema must be {TASK_SCHEMA}, got {raw.get('schema')!r}")
    category, name = str(raw.get("category")), base.name
    if category not in CATEGORIES:
        out.append(f"category must be one of {', '.join(CATEGORIES)}")
    elif raw.get("id") != f"{category}/{name}":
        out.append(f"id must be {category}/{name!r}, got {raw.get('id')!r}")
    ...
    weights = raw.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(SUBSCORES):
        out.append(f"weights must have exactly the keys {', '.join(SUBSCORES)}")
    else:
        bad = [k for k, v in weights.items()
               if not isinstance(v, (int, float)) or isinstance(v, bool)
               or not math.isfinite(v) or v < 0]
        if bad:
            out.append(f"every weight must be a finite number >= 0: {sorted(bad)}")
        elif abs(sum(weights.values()) - 1.0) > 1e-9:
            out.append(f"weights must sum to 1.0, they sum to {sum(weights.values())}")
    budgets = raw.get("budgets") or {}
    turns = budgets.get("turns")
    if not isinstance(turns, int) or isinstance(turns, bool) \
            or not 1 <= turns <= MAX_TOOL_CALLS_PER_TURN:
        out.append(f"budgets.turns must be an int in 1..{MAX_TOOL_CALLS_PER_TURN} "
                   f"(MAX_TOOL_CALLS_PER_TURN); a task may not declare more tool "
                   f"calls than one chat turn can issue")
    ...
```

  and the rest exactly per design §2, with these message fragments the test pins:
  `"schema"`, `"sum to 1"`, `"MAX_TOOL_CALLS_PER_TURN"`, `"align"`,
  `"outside the task directory"`, `"must be a STEP (.step/.stp), never a mesh"`,
  `"at most 8"`, `"must bind SPECS"`, `"check_fem_static is not allowed"`.
  `MAX_TOOL_CALLS_PER_TURN` is imported from `agentcad.agent.chat` so raising the
  product ceiling raises what a task may declare (design D12).
  `prompt_text(task)` returns the prompt file's text plus, per asset, a block
  `"\n\n--- attachment: assets/<name> ---\n```\n<text>\n```\n"`.

- [ ] **Step 5: Run — expect FAIL** (`benchmarks/` does not exist yet).

- [ ] **Step 6: Author the seed task bundle** at
  `benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/`. Everything below is
  written by hand except the STEP and `metrics.json` (Step 8).

  `task.json` — copy the complete example from design §1.1, verbatim.

  `prompt.md`:
  ```markdown
  Model the spacer plate shown in the attached drawing.

  Create a project part with the id `spacer_plate`. The plate is 80 mm long,
  50 mm wide and 6 mm thick, with R5 corner rounds and four M6 clearance holes
  (Ø6.0) on a 60 x 30 mm rectangular pattern centred on the plate.

  Datum: the plate's bottom face lies on Z = 0 and its centre is at the origin,
  with the 80 mm dimension along +X.
  ```

  `assets/drawing.svg` — a hand-authored three-view SVG (top, front, right) with
  dimension text for 80, 50, 6, R5, 4x Ø6, 60 and 30. Plain `<svg>`/`<line>`/
  `<circle>`/`<text>`, no external font, no script.

  `reference/project/project.json` — a one-part manifest, `id: "spacer_plate"`,
  `material: "aluminum_6061"`, params matching the script's `PARAMS`.

  `reference/project/parts/spacer_plate.py`:
  ```python
  """Reference solution for bench task model_from_drawing/mfd_001_spacer_plate."""
  from build123d import *

  PARAMS = {
      "length": {"default": 80.0, "min": 20.0, "max": 200.0},
      "width": {"default": 50.0, "min": 20.0, "max": 200.0},
      "thickness": {"default": 6.0, "min": 1.0, "max": 30.0},
      "corner_r": {"default": 5.0, "min": 0.0, "max": 20.0},
      "hole_d": {"default": 6.0, "min": 1.0, "max": 20.0},
      "hole_dx": {"default": 60.0, "min": 10.0, "max": 180.0},
      "hole_dy": {"default": 30.0, "min": 10.0, "max": 180.0},
  }


  def build(p):
      with BuildPart() as part:
          with BuildSketch() as plate:
              RectangleRounded(p.length, p.width, p.corner_r)
          extrude(amount=p.thickness)
          with Locations(part.faces().sort_by(Axis.Z)[-1]):
              with GridLocations(p.hole_dx, p.hole_dy, 2, 2):
                  Hole(radius=p.hole_d / 2)
      return part.part
  ```

  `specs/parts/spacer_plate.py` — exactly the shape of design §1.2 (three checks:
  `check_valid`, `check_wall(min_mm=3.0, grid=4)`, `check_bbox(within_mm=(80.2,
  50.2, 6.2))`), every constructor imported under a `_bench_` alias, binding
  `SPECS = [...]` (re-bind, never `+=`).

- [ ] **Step 7: Implement `agentcad/bench/author.py`** — the dev helper, run as
  `uv run python -m agentcad.bench.author <cmd> <task-dir>`:

```python
"""Authoring helper for bench tasks. NOT an `agentcad` subcommand.

    uv run python -m agentcad.bench.author step    benchmarks/tasks/<c>/<id>
    uv run python -m agentcad.bench.author metrics benchmarks/tasks/<c>/<id>

`step` opens `reference/project`, builds every part named in `task.json`'s
`target.parts` and exports each to `reference/steps/<part>.step`. `metrics`
measures the same parts and seeds `reference/metrics.json` with a +/-1% band on
mass and volume and a +/-0.05 mm band on each bbox extent -- a STARTING POINT
the author then hand-edits and argues in the PR.
"""


def export_reference(task_dir: Path, *, service) -> dict:
    """Build + export every reference part. Returns {part_id: step_path}."""
    raw = read_json(task_dir / "task.json")
    proj = service.open_project(str(task_dir / raw["reference"]["project"]))["name"]
    out: dict[str, str] = {}
    steps = task_dir / "reference" / "steps"
    steps.mkdir(parents=True, exist_ok=True)
    for part_id in raw["target"]["parts"]:
        result = service.export_part(proj, part_id, "step")     # service.py:496
        shutil.copyfile(result["path"], steps / f"{part_id}.step")
        out[part_id] = str(steps / f"{part_id}.step")
    return out
```

  `seed_metrics` reads `service._ensure_built(proj, part_id)["metrics"]` and emits
  the `{"schema": 1, "windows": [...]}` document of design §1.3, windows sorted by
  `name`, through `_json.write_json`. `main()` builds its service with
  `agentcad.cli._build_service(tmp_projects_dir, examples=False)` (available after
  Task 4 — until then it passes no `examples` kwarg; **Task 4 Step 9 updates this
  one call site**) and stops the kernel in a `finally`.

- [ ] **Step 8: Generate the seed task's STEP and metrics**
  ```
  uv run python -m agentcad.bench.author step    benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate
  uv run python -m agentcad.bench.author metrics benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate
  ```
  Then hand-edit `reference/metrics.json` down to the four windows of design §1.3
  (`mass`, `height`, `solids`, `material`) and delete the rest.
  **Expected:** `reference/steps/spacer_plate.step` exists, is ISO-10303-21 text
  (`head -1` starts `ISO-10303-21;`) and is 20–200 KB.

- [ ] **Step 9: Run — expect PASS.** `uv run pytest tests/test_bench_tasks.py -q`
  **Expected:** all tests pass; `test_every_shipped_task_has_zero_problems` sees
  exactly one task.

- [ ] **Step 10: Changelog + report.** Write `docs/changelog/0256-bench-task-format-and-loader.md`
  from the diff (what the format is, why the rubric is separate from the reference,
  why `turns` is capped at `MAX_TOOL_CALLS_PER_TURN`). Cite the
  `pytest tests/test_bench_tasks.py -q` output. **The orchestrator commits and
  fills the full `make test` count.**

**Advances:** FR1, FR2 (format half), Decisions 1–2, D1–D3, D10, D12, D15.

---

## Task 2: The `iou` kernel handler (Decision 5, FR5, FR7, AC4)

**Files:**
- Create: `agentcad/kernel/handlers/bench.py`
- Create: `tests/test_bench_kernel_iou.py`
- Reference: `agentcad/kernel/handlers/diff.py:29-117` (the template), `agentcad/kernel/worker.py:646-724` (`pairwise_interference`, the `&` at :687-689 and the multi-solid warning at :650-653), `worker.py:353` (`_shape_volume`), `worker.py:418` (`_place`), `worker.py:595` (`_item_shape`'s grammar), `agentcad/kernel/refload.py:41,64,72`

**Interfaces:**
- Consumes: `WORKER_TOOLBOX` keys `b3d`, `build_shape`, `shape_volume`, `WorkerError`, `ERROR_KERNEL` (`worker.py:765-780`).
- Produces kernel method **`iou`**, params
  `{"candidate": <item>, "reference": <item>, "align": str, "rotations_deg": [[x,y,z], ...]}`
  where `<item>` is `{"script", "params"}` or `{"source"}` plus optional
  `"position"`/`"rotation_deg"` — `worker._item_shape`'s grammar exactly.
  Result: `{"intersection_mm3", "union_mm3", "iou", "candidate_volume_mm3",
  "reference_volume_mm3", "candidate_solids", "reference_solids", "align",
  "rotation_deg", "status"}` plus `"skipped_mesh": [...]` when applicable.

- [ ] **Step 1: Failing test — the analytic cases (AC4)**

```python
# tests/test_bench_kernel_iou.py
import pytest

BOX = """
from build123d import *
PARAMS = {"sx": {"default": 10.0}, "sy": {"default": 10.0}, "sz": {"default": 10.0},
          "dx": {"default": 0.0}}

def build(p):
    with BuildPart() as part:
        with Locations((p.dx, 0, 0)):
            Box(p.sx, p.sy, p.sz)
    return part.part
"""


def _iou(kernel, candidate, reference, **kw):
    params = {"candidate": candidate, "reference": reference,
              "align": kw.pop("align", "world"),
              "rotations_deg": kw.pop("rotations_deg", [[0.0, 0.0, 0.0]])}
    return kernel.request("iou", params, timeout_s=120.0)


def test_identical_scripts_score_one(kernel):
    item = {"script": BOX, "params": {}}
    out = _iou(kernel, item, item)
    assert out["status"] == "ok"
    assert out["iou"] == pytest.approx(1.0, abs=1e-9)
    assert out["intersection_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert out["union_mm3"] == pytest.approx(1000.0, rel=1e-6)


def test_disjoint_shapes_score_zero(kernel):
    a = {"script": BOX, "params": {}}
    b = {"script": BOX, "params": {"dx": 50.0}}
    out = _iou(kernel, a, b)
    assert out["iou"] == 0.0
    assert out["intersection_mm3"] == 0.0
    assert out["union_mm3"] == pytest.approx(2000.0, rel=1e-6)


def test_half_overlap_is_the_analytic_value(kernel):
    a = {"script": BOX, "params": {}}
    b = {"script": BOX, "params": {"dx": 5.0}}
    out = _iou(kernel, a, b)
    # intersection 5x10x10 = 500; union 2000-500 = 1500; iou = 1/3
    assert out["intersection_mm3"] == pytest.approx(500.0, rel=1e-6)
    assert out["iou"] == pytest.approx(1.0 / 3.0, rel=1e-6)


def test_com_alignment_cancels_a_pure_translation(kernel):
    a = {"script": BOX, "params": {}}
    b = {"script": BOX, "params": {"dx": 25.0}}
    assert _iou(kernel, a, b)["iou"] == 0.0
    assert _iou(kernel, a, b, align="com")["iou"] == pytest.approx(1.0, abs=1e-6)


def test_declared_rotation_recovers_a_rotated_reference(kernel):
    slab = BOX.replace('"sx": {"default": 10.0}', '"sx": {"default": 30.0}')
    a = {"script": slab, "params": {}}
    b = {"script": slab, "params": {}, "rotation_deg": [0.0, 0.0, 90.0]}
    assert _iou(kernel, a, b)["iou"] < 0.5
    out = _iou(kernel, a, b, rotations_deg=[[0.0, 0.0, 0.0], [0.0, 0.0, 90.0]])
    assert out["iou"] == pytest.approx(1.0, abs=1e-6)
    assert out["rotation_deg"] == [0.0, 0.0, 90.0]


def test_mesh_candidate_is_skipped_never_booleaned(kernel, tmp_path):
    stl = tmp_path / "cube.stl"
    stl.write_bytes(_write_ascii_cube_stl())          # helper in this module
    out = _iou(kernel, {"source": str(stl)}, {"script": BOX, "params": {}})
    assert out["status"] == "skipped_mesh"
    assert out["skipped_mesh"] == ["candidate"]
    assert out["iou"] == 0.0
    assert out["reference_volume_mm3"] == pytest.approx(1000.0, rel=1e-6)


def test_multi_solid_candidate_sums_and_clamps(kernel):
    two = BOX.replace("Box(p.sx, p.sy, p.sz)",
                      "Box(p.sx, p.sy, p.sz)\n        with Locations((30, 0, 0)):\n"
                      "            Box(p.sx, p.sy, p.sz)")
    out = _iou(kernel, {"script": two, "params": {}}, {"script": BOX, "params": {}})
    assert out["candidate_solids"] == 2
    assert out["intersection_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert 0.0 <= out["iou"] <= 1.0
    assert out["iou"] == pytest.approx(0.5, rel=1e-6)


def test_iou_is_kernel_internal_and_not_a_tool(service_with_kernel):
    from agentcad.core.tools import build_registry
    assert "iou" not in build_registry(service_with_kernel).list_names()
```

  (`list_names` is whatever `ToolRegistry` already exposes — check
  `agentcad/core/tools.py` and use the real accessor; the assertion is that no
  registered tool is named `iou`.)

- [ ] **Step 2: Run — expect FAIL** (`unknown method 'iou'`).
  `uv run pytest tests/test_bench_kernel_iou.py -x -q`

- [ ] **Step 3: Implement `agentcad/kernel/handlers/bench.py`**

```python
"""Worker handler pack for the bench geometry scorer (PRD-024).

`iou` measures how much of the candidate is the reference, and vice versa, as
one number. Three traps this handler is written around:

* **Only the intersection is booleaned.** `union = volA + volB - inter` is
  arithmetic; a `|` on multi-solid Compound operands is exactly the operand
  shape `worker.pairwise_interference` warns about, and it would double the
  OCCT failure surface for a number we already have.
* **Both sides are decomposed into solids**, for `pairwise_interference`'s two
  reasons: build123d's `&` misbehaves when an operand is a multi-solid
  Compound, and an AABB prefilter skips almost all of the N*M work.
* **A mesh side is never booleaned** -- an imported STL is one welded Face and
  an OCCT boolean on it segfaults the worker. Such a side short-circuits to
  `status: "skipped_mesh"` with both per-side volumes still reported, exactly
  as `handlers/diff.py` does.

Volumes come from `shape_volume` (sum over `shape.solids()`), never `.volume`:
a boolean result is routinely a nested Compound and `Compound.volume` reports
only the first child subtree. This handler is NEVER registered as a
model-facing tool -- a bench-only tool would contaminate the measurement.
"""
from __future__ import annotations

ALIGN_MODES = ("world", "com", "bbox_center")


def register(toolbox: dict) -> dict:
    b3d = toolbox["b3d"]
    build_shape = toolbox["build_shape"]
    shape_volume = toolbox["shape_volume"]
    WorkerError = toolbox["WorkerError"]
    ERROR_KERNEL = toolbox["ERROR_KERNEL"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def _side(item, label):
        """(shape, kind) for one side -- worker._item_shape's grammar."""
        if not isinstance(item, dict):
            raise WorkerError(ERROR_CONTRACT, f"{label} must be an item object")
        if item.get("source"):
            from ..refload import load_reference
            shape, kind = load_reference(item["source"])
        else:
            shape, _values, _warnings = build_shape(
                item["script"], item.get("params") or {})
            kind = "script"
        placed = b3d.Location(tuple(item.get("position") or (0, 0, 0)),
                              tuple(item.get("rotation_deg") or (0, 0, 0)))
        return shape.moved(placed), kind

    def _anchor(shape, align):
        if align == "com":
            point = shape.center(b3d.CenterOf.MASS)
            return (point.X, point.Y, point.Z)
        if align == "bbox_center":
            point = shape.bounding_box().center()
            return (point.X, point.Y, point.Z)
        return (0.0, 0.0, 0.0)

    def _guarded(fn, stage):
        try:
            return fn()
        except Exception as exc:      # noqa: BLE001 -- any OCCT failure degrades
            raise WorkerError(ERROR_KERNEL, f"iou unavailable: {exc}",
                              {"stage": stage}) from exc

    def _boxes_touch(a, b, tol=1e-6):
        return not (a.max.X < b.min.X - tol or b.max.X < a.min.X - tol
                    or a.max.Y < b.min.Y - tol or b.max.Y < a.min.Y - tol
                    or a.max.Z < b.min.Z - tol or b.max.Z < a.min.Z - tol)

    def _intersection(cand, ref):
        """Sum of pairwise solid intersections, AABB-prefiltered."""
        left = [(s, s.bounding_box()) for s in (cand.solids() or [cand])]
        right = [(s, s.bounding_box()) for s in (ref.solids() or [ref])]
        total = 0.0
        for solid_a, box_a in left:
            for solid_b, box_b in right:
                if not _boxes_touch(box_a, box_b):
                    continue
                common = solid_a & solid_b
                if common is not None:
                    total += max(shape_volume(common), 0.0)
        return total, len(left), len(right)

    def handle_iou(params: dict) -> dict:
        align = params.get("align") or "world"
        if align not in ALIGN_MODES:
            raise WorkerError(ERROR_CONTRACT, f"unknown align mode {align!r}")
        cand, cand_kind = _side(params.get("candidate"), "candidate")
        ref, ref_kind = _side(params.get("reference"), "reference")
        vol_a = _guarded(lambda: shape_volume(cand), "candidate_volume")
        vol_b = _guarded(lambda: shape_volume(ref), "reference_volume")
        base = {"candidate_volume_mm3": vol_a, "reference_volume_mm3": vol_b,
                "candidate_solids": len(cand.solids()),
                "reference_solids": len(ref.solids()),
                "align": align, "rotation_deg": [0.0, 0.0, 0.0]}
        skipped = [name for name, kind in (("candidate", cand_kind),
                                           ("reference", ref_kind))
                   if kind == "mesh"]
        if skipped:
            # No boolean is attempted: an STL operand segfaults OCCT.
            return {**base, "intersection_mm3": 0.0,
                    "union_mm3": vol_a + vol_b, "iou": 0.0,
                    "status": "skipped_mesh", "skipped_mesh": skipped}

        anchor_c, anchor_r = _anchor(cand, align), _anchor(ref, align)
        best = None
        for rotation in (params.get("rotations_deg") or [[0.0, 0.0, 0.0]]):
            rot = [float(v) for v in rotation]
            # translate(anchor_ref) . rotate(r) . translate(-anchor_cand)
            placement = (b3d.Location(tuple(anchor_r), tuple(rot))
                         * b3d.Location(tuple(-v for v in anchor_c)))
            moved = cand.moved(placement)
            inter, n_a, n_b = _guarded(
                lambda: _intersection(moved, ref), "intersect")
            # The pairwise sum equals vol(A n B) only when each side's solids
            # are mutually disjoint; a self-overlapping candidate over-counts.
            inter = min(inter, vol_a, vol_b)
            union = vol_a + vol_b - inter
            score = 0.0 if union <= 0.0 else max(0.0, min(1.0, inter / union))
            if best is None or score > best[0]:
                best = (score, inter, union, rot, n_a, n_b)
        score, inter, union, rot, n_a, n_b = best
        return {**base, "candidate_solids": n_a, "reference_solids": n_b,
                "intersection_mm3": inter, "union_mm3": union, "iou": score,
                "rotation_deg": rot, "status": "ok"}

    return {"iou": handle_iou}
```

- [ ] **Step 4: Run — expect PASS.**
  `uv run pytest tests/test_bench_kernel_iou.py -q`
  **Expected:** 8 passed. If `test_declared_rotation_recovers_a_rotated_reference`
  fails, the `Location` composition order is inverted — swap the two operands and
  re-run; the test is the arbiter, not the comment.

- [ ] **Step 5: Failing test — an OCCT failure degrades honestly (FR7)**

```python
def test_a_boolean_failure_is_a_kernel_error_with_a_stage(kernel):
    from agentcad.kernel.client import KernelError
    bad = {"script": "def build(p):\n    raise RuntimeError('boom')\n", "params": {}}
    with pytest.raises(KernelError) as exc:
        _iou(kernel, bad, {"script": BOX, "params": {}})
    assert exc.value.type in ("script_error", "kernel_error")
```

- [ ] **Step 6: Run — expect PASS already** (`worker._dispatch` at `worker.py:826-841`
  converts any handler exception into a typed `WorkerError`). If it does not,
  the `_guarded` wrapper is missing a stage — add it.

- [ ] **Step 7: Confirm the pack does not shadow a builtin.**
  `uv run python -c "from agentcad.kernel import worker; worker._load_handler_packs(); print('iou' in worker.HANDLERS)"`
  **Expected:** `True`, and **no** `warning: handler … shadows a builtin` line on
  stderr (`worker.py:800-803`).

- [ ] **Step 8: Changelog + report.** `docs/changelog/0257-bench-iou-kernel-handler.md`
  — why only the intersection is booleaned, the clamp, the mesh short-circuit, and
  that the handler is deliberately not a tool. Cite the
  `pytest tests/test_bench_kernel_iou.py -q` output. **Orchestrator commits.**

**Advances:** FR5, FR7, AC4, Decision 5, D7, D8.

---

## Task 3: The scorer — six subscores, rubric injection, deterministic `score.json` (Decisions 3, 4, 6)

**Files:**
- Create: `agentcad/bench/scoring.py`
- Create: `tests/test_bench_scoring.py`
- Reference: `agentcad/core/checks.py:864` (`_ephemeral_service`), `:836` (`default_work_root`), `:170` (`refuse_work_dir_overlap`), `:1317` (`_budget_broke`); `agentcad/core/service.py:764` (`_ensure_built`), `:615` (`check_interference`), `:600` (`_shape_item`); `agentcad/core/specs.py:1363` (`SpecRunner.run`), `:218` (`summarize`), `:306` (`_slack`); `agentcad/kernel/refload.py:72` (`is_mesh_kind`)

**Interfaces:**
- Consumes: `bench.tasks.{Task, MetricWindow, load_windows}` (Task 1), `bench._json.{write_json, canonical_json}` (Task 1), kernel method `iou` (Task 2).
- Produces `agentcad/bench/scoring.py`:
  - `SCORE_SCHEMA = 1`; `IOU_TIMEOUT_S = 300.0`;
    `SUBSCORE_STATUSES = ("ok", "skipped_mesh", "error", "not_applicable")`
  - `class Scorer:`
    - `__init__(self, service, registry=None)`
    - `score(self, task: Task, submission, *, budget_s: float | None = None, work_dir: str | None = None) -> dict`
  - `inject_rubric(task: Task, copy_root: Path) -> list[str]` — returns the part ids whose scripts were rewritten
  - `refuse_scoring_overlap(root, submission, task_root, projects_root) -> None`
  - `total_of(subscores: dict) -> tuple[float, dict]` — `(total, weights_effective)`

- [ ] **Step 1: Failing test — the reference scores 1.0 and the score is byte-stable (AC3)**

```python
# tests/test_bench_scoring.py
import shutil
import pytest

from agentcad.bench import tasks as bench_tasks
from agentcad.bench._json import canonical_json
from agentcad.bench.scoring import Scorer

SEED = "model_from_drawing/mfd_001_spacer_plate"
pytestmark = pytest.mark.timeout(600)


@pytest.fixture
def scorer(service_with_kernel):
    return Scorer(service_with_kernel)


def test_the_reference_solution_scores_one(scorer):
    task = bench_tasks.load_task(SEED)
    score = scorer.score(task, task.reference_project)
    assert score["schema"] == 1
    assert score["task"] == SEED
    assert score["total"] == pytest.approx(1.0, abs=1e-9)
    for name in ("built", "valid", "specs", "geometry", "metrics"):
        assert score["subscores"][name]["status"] == "ok"
        assert score["subscores"][name]["value"] == pytest.approx(1.0, abs=1e-6)
    assert score["subscores"]["interference"]["status"] == "not_applicable"


def test_scoring_twice_is_byte_identical(scorer):
    task = bench_tasks.load_task(SEED)
    first = canonical_json(scorer.score(task, task.reference_project))
    second = canonical_json(scorer.score(task, task.reference_project))
    assert first == second
    assert b"generated" not in first and b"started" not in first
    assert b"/private" not in first and b"/tmp" not in first
```

- [ ] **Step 2: Run — expect FAIL** (`agentcad.bench.scoring` does not exist).
  `uv run pytest tests/test_bench_scoring.py -x -q`

- [ ] **Step 3: Implement `Scorer.score`'s lifecycle** (design §3), in this order:

```python
def score(self, task, submission, *, budget_s=None, work_dir=None) -> dict:
    submission = Path(submission).expanduser().resolve()
    projects_root = Path(self.service.store.root).resolve()
    refuse_scoring_overlap(work_dir, submission, task.root, projects_root)
    parent = work_dir or default_work_root(self.service)          # checks.py:836
    cell = Path(tempfile.mkdtemp(prefix="agentcad-bench-", dir=parent))
    deadline = None if budget_s is None else time.monotonic() + budget_s
    try:
        tree = cell / "candidate" / task.target_project
        shutil.copytree(submission, tree, ignore=shutil.ignore_patterns(
            ".cache", "exports", ".history"))
        inject_rubric(task, tree)
        # NON-NEGOTIABLE, all three: a `project_changed` publish would commit a
        # history snapshot, a live branch_resolver would write a
        # `.history/agentcad/` sidecar into the copy, and write_guard would
        # materialise a branch tree. See checks._ephemeral_service.
        service, registry, proj = _ephemeral_service(cell, tree, self.service.kernel)
        subscores = {
            "built": self._built(service, task, proj),
            ...
        }
        total, effective = total_of(subscores)
        return {...}
    finally:
        shutil.rmtree(cell, ignore_errors=True)      # only the cell WE created
```

  `inject_rubric(task, copy_root)`:
  ```python
  BLOCK_HEADER = "# --- agentcad-bench: task rubric (appended by the scorer) ---"

  def inject_rubric(task, copy_root: Path) -> list[str]:
      if task.specs_project_path is not None:
          (copy_root / "specs.py").write_text(
              task.specs_project_path.read_text(encoding="utf-8"), encoding="utf-8")
      touched = []
      for part_id, block in sorted(task.specs_part_paths.items()):
          script = copy_root / "parts" / f"{part_id}.py"
          if not script.is_file():
              continue          # a missing part is a `built` zero, not an error
          script.write_text(
              f"{script.read_text(encoding='utf-8')}\n\n{BLOCK_HEADER}\n"
              f"{block.read_text(encoding='utf-8')}\n", encoding="utf-8")
          touched.append(part_id)
      return touched
  ```
  The block **re-binds** `SPECS`, so the candidate's own declarations are
  discarded — that is what stops an agent inflating its `specs` subscore.

- [ ] **Step 4: Implement the six subscore methods** exactly per design §4.1–4.6.
  Each returns `{"value": float, "weight": float, "status": str, "detail": dict}`.
  A subscore whose `task.weights[name] == 0.0` short-circuits **before any
  measurement** to `{"value": 0.0, "weight": 0.0, "status": "not_applicable",
  "detail": {"reason": "weight_zero"}}` — the task decides, never the run.

```python
def _built(self, service, task, proj):
    failed, errors = [], []
    for part_id in task.target_parts:
        try:
            result = service._ensure_built(proj, part_id)
        except Exception as exc:      # noqa: BLE001 -- checks._build_item's edge
            errors.append({"part": part_id, "message": f"{type(exc).__name__}: {exc}"})
            continue
        if not result.get("ok"):
            failed.append(part_id)
    if errors:
        return _subscore(0.0, task, "built", "error", {"errors": errors})
    value = 1.0 - len(failed) / len(task.target_parts)
    return _subscore(value, task, "built", "ok",
                     {"parts": len(task.target_parts), "failed": sorted(failed)})
```

  `_specs` calls `service.specs.run(proj, deadline=deadline)` — reading
  `service.specs` **inside** the method, never captured in `__init__` (the
  `CheckRunner` rule) — and computes
  `passed / (passed + failed + errors)`, **excluding `skip` rows from the
  denominator**; a zero denominator with a non-zero weight is `status: "error"`.
  It embeds counts and sorted row ids, **never the report** (it carries a
  `generated` timestamp at `specs.py:1574`).

  `_geometry` issues one `iou` request per part with a `reference.steps` entry:
  ```python
  item = self._candidate_item(service, proj, part_id)    # service._shape_item's shape
  if item.get("source") and refload_is_mesh(item["source"]):
      return _subscore(0.0, task, "geometry", "skipped_mesh", {...})
  result = service.kernel.request("iou", {
      "candidate": item,
      "reference": {"source": str(task.reference_steps[part_id])},
      "align": task.frame.align,
      "rotations_deg": [list(r) for r in task.frame.rotations_deg] or [[0.0, 0.0, 0.0]],
  }, timeout_s=min(IOU_TIMEOUT_S, remaining) if remaining else IOU_TIMEOUT_S,
     affinity=task.id)
  ```
  A `KernelError` becomes `status: "error"` with
  `detail["error"] = {"type", "message", "stage"}`; a part that did not build
  becomes `value 0.0, status "ok"` with `detail["reason"] = "build_failed"`.

  `_interference` and `_metrics` per design §4.5–4.6.

  `total_of`:
  ```python
  def total_of(subscores: dict) -> tuple[float, dict]:
      included = {k: s for k, s in subscores.items()
                  if s["status"] not in ("error", "not_applicable")}
      weight = sum(s["weight"] for s in included.values())
      if weight <= 0.0:
          return 0.0, {}
      effective = {k: s["weight"] / weight for k, s in included.items()}
      return sum(s["value"] * effective[k] for k, s in included.items()), effective
  ```

- [ ] **Step 5: Run — expect PASS.** `uv run pytest tests/test_bench_scoring.py -q`

- [ ] **Step 6: Failing test — a flawed solution loses exactly the right subscores (AC2)**

```python
def _reference_copy(task, tmp_path):
    dst = tmp_path / "flawed"
    shutil.copytree(task.reference_project, dst)
    return dst


def test_one_missing_hole_costs_geometry_and_metrics_but_not_specs(scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    script = flawed / "parts" / "spacer_plate.py"
    script.write_text(script.read_text().replace(
        "GridLocations(p.hole_dx, p.hole_dy, 2, 2)",
        "GridLocations(p.hole_dx, p.hole_dy, 2, 1)"))
    score = scorer.score(task, flawed)
    geometry = score["subscores"]["geometry"]
    assert geometry["status"] == "ok"
    # two of four holes are gone; the drop is 2*hole_volume / union
    hole = 3.1415926535 * 3.0 ** 2 * 6.0
    detail = geometry["detail"]["spacer_plate"]
    assert 1.0 - geometry["value"] == pytest.approx(
        2 * hole / detail["union_mm3"], rel=0.05)
    assert score["subscores"]["specs"]["value"] == pytest.approx(1.0)
    assert score["total"] < 1.0


def test_a_violated_spec_names_the_failing_check(scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    manifest = flawed / "project.json"
    manifest.write_text(manifest.read_text().replace('"thickness": 6.0',
                                                     '"thickness": 12.0'))
    score = scorer.score(task, flawed)
    specs = score["subscores"]["specs"]
    assert specs["status"] == "ok"
    assert specs["value"] < 1.0
    assert "spacer_plate:envelope" in specs["detail"]["failed"]


def test_a_candidates_own_SPECS_are_discarded(scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    script = flawed / "parts" / "spacer_plate.py"
    script.write_text(script.read_text() + (
        "\nfrom agentcad.toolkit.specs import check_valid\n"
        "SPECS = [check_valid(name='free_point')]\n"))
    score = scorer.score(task, flawed)
    assert score["subscores"]["specs"]["detail"]["total"] == 3
    assert "spacer_plate:free_point" not in score["subscores"]["specs"]["detail"]["failed"]


def test_a_missing_target_part_is_zero_everywhere_and_never_not_applicable(scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "project.json").write_text(
        '{"schema_version": 1, "name": "empty", "units": "mm", "parts": []}')
    score = scorer.score(task, empty)
    for name in ("built", "valid", "geometry", "metrics"):
        assert score["subscores"][name]["value"] == 0.0
        assert score["subscores"][name]["status"] == "ok", name
    assert score["total"] == 0.0


def test_the_submission_directory_is_never_mutated(scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    copy = _reference_copy(task, tmp_path)
    before = {p.relative_to(copy): p.read_bytes()
              for p in copy.rglob("*") if p.is_file()}
    scorer.score(task, copy)
    after = {p.relative_to(copy): p.read_bytes()
             for p in copy.rglob("*") if p.is_file()}
    assert before == after
```

- [ ] **Step 7: Run — expect FAIL, then fix until PASS.** The likely first red is
  `test_a_missing_target_part_is_zero_everywhere_and_never_not_applicable` if any
  subscore promotes itself to `not_applicable` on a missing part — it must not
  (design D5).

- [ ] **Step 8: Failing test — an errored subscore is excluded and renormalised**

```python
def test_an_errored_geometry_subscore_is_excluded_and_weights_renormalise(scorer, tmp_path, monkeypatch):
    task = bench_tasks.load_task(SEED)
    from agentcad.kernel.client import KernelError
    real = scorer.service.kernel.request

    def boom(method, params, **kw):
        if method == "iou":
            raise KernelError("kernel_error", "iou unavailable: boom",
                              {"stage": "intersect"})
        return real(method, params, **kw)

    monkeypatch.setattr(scorer.service.kernel, "request", boom)
    score = scorer.score(task, task.reference_project)
    assert score["subscores"]["geometry"]["status"] == "error"
    assert "geometry" not in score["weights_effective"]
    assert score["weights_effective"]["built"] == pytest.approx(0.15 / 0.5, rel=1e-9)
    assert score["total"] == pytest.approx(1.0, abs=1e-9)   # the rest are perfect
```

- [ ] **Step 9: Run — expect PASS.** Then the whole module:
  `uv run pytest tests/test_bench_scoring.py -q`
  **Expected:** 9 passed.

- [ ] **Step 10: Changelog + report.** `docs/changelog/0258-bench-scorer.md` —
  the six subscores, the `error`-vs-zero rule and why it exists, rubric
  injection and the re-bind, and the determinism contract. Cite
  `pytest tests/test_bench_scoring.py -q`. **Orchestrator commits.**

**Advances:** FR4, FR6, FR7, AC2, AC3, Decisions 3, 4, 6, D3–D6, D15.

---

## Task 4: `agentcad bench score` — the CLI, and the two `cli.py` edits (Decision 9)

**Files:**
- Create: `agentcad/bench/cli.py`
- Modify: `agentcad/cli.py:48-136` (`_build_service` gains `examples`), `:1446-1448` (metavar), `:1688-1708` (dispatch)
- Modify: `agentcad/bench/author.py` (Step 9)
- Create: `tests/test_bench_cli.py`
- Reference: `agentcad/cli.py:1014-1154` (`cmd_check`'s exact skeleton), `:161` (`_accept_work_dir`), `:658` (`_finite_arg`), `:689` (`_write_check_outputs`), `:1089` (`locks.set_client_id`), `:1541-1545` (the `--quiet`/`--json` group)

**Interfaces:**
- Consumes: `bench.tasks.load_task/load_tasks` (Task 1), `bench.scoring.Scorer` (Task 3).
- Produces `agentcad/bench/cli.py`:
  - `add_bench_parser(sub) -> None` — adds the `bench` subparser and its four sub-subcommands (`run`, `score`, `report`, `publish`); `run`/`report`/`publish` are wired in Tasks 5–7 and until then their handlers return exit 2 with `"not implemented in this slice"`.
  - `cmd_bench(args) -> int`
  - `bench_service(projects_dir, *, extra_writable=None)` — thin wrapper over `agentcad.cli._build_service(projects_dir, extra_writable=extra_writable, examples=False)`
- Produces `agentcad/cli.py`: `_build_service(..., *, posture=None, examples: bool = True)`.

- [ ] **Step 1: Failing test — `examples=False` hides the bundled examples**

```python
# tests/test_bench_cli.py
import json
import pytest
from pathlib import Path

from agentcad import cli as agentcad_cli


def test_build_service_examples_flag_defaults_to_registering_them(tmp_path):
    service = agentcad_cli._build_service(tmp_path / "projects")
    try:
        assert any(p["name"] == "prototyping" for p in service.list_projects())
    finally:
        service.kernel.stop()
        agentcad_cli._release_work_root(service)


def test_build_service_examples_false_registers_none(tmp_path):
    service = agentcad_cli._build_service(tmp_path / "projects", examples=False)
    try:
        assert service.list_projects() == []
    finally:
        service.kernel.stop()
        agentcad_cli._release_work_root(service)
```

- [ ] **Step 2: Run — expect FAIL** (`unexpected keyword argument 'examples'`).
  `uv run pytest tests/test_bench_cli.py -x -q`

- [ ] **Step 3: Edit `agentcad/cli.py` — edit 1 of 2.** In `_build_service`
  (`cli.py:48-49`) add the keyword-only parameter and guard the one call:

```python
def _build_service(projects_dir: Path, extra_writable: list[str] | None = None,
                   *, posture: str | None = None, examples: bool = True):
```
```python
        if examples:
            _register_examples(service)          # was cli.py:123, unconditional
        _register_catalog(service)
```

  Extend the docstring with one paragraph: *"`examples=False` is the bench's
  (PRD-024): a task derived from a bundled example must not be solvable by
  opening that example. The default keeps `check`, `serve`, `export` and
  `package` byte-identical, and it is a parameter rather than
  `AGENTCAD_EXAMPLES=0` because that variable is process-global and a bench run
  inside a pytest worker would clobber a neighbour."*

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Failing test — `agentcad bench score` end to end**

```python
def _run(argv):
    """Drive main() the way a shell would; returns the SystemExit code."""
    import sys
    from unittest.mock import patch
    with patch.object(sys, "argv", ["agentcad", *argv]):
        with pytest.raises(SystemExit) as exc:
            agentcad_cli.main()
    return exc.value.code


SEED = "model_from_drawing/mfd_001_spacer_plate"


@pytest.mark.timeout(600)
def test_bench_score_writes_a_score_and_exits_zero(tmp_path):
    from agentcad.bench import tasks as bench_tasks
    task = bench_tasks.load_task(SEED)
    out = tmp_path / "out"
    code = _run(["bench", "score", str(task.reference_project), "--task", SEED,
                 "--out", str(out), "--quiet"])
    assert code == 0
    score = json.loads((out / "score.json").read_text())
    assert score["task"] == SEED
    assert score["total"] == pytest.approx(1.0, abs=1e-9)


def test_bench_score_unknown_task_is_exit_two(tmp_path, capsys):
    code = _run(["bench", "score", str(tmp_path), "--task", "nope/nope", "--quiet"])
    assert code == 2
    assert "nope/nope" in capsys.readouterr().err


def test_bench_score_refuses_a_work_dir_inside_the_submission(tmp_path, capsys):
    from agentcad.bench import tasks as bench_tasks
    task = bench_tasks.load_task(SEED)
    inside = tmp_path / "sub"
    __import__("shutil").copytree(task.reference_project, inside)
    code = _run(["bench", "score", str(inside), "--task", SEED,
                 "--work-dir", str(inside / "cell"), "--quiet"])
    assert code == 2
    assert "overlaps" in capsys.readouterr().err
    assert not (inside / "cell").exists()      # a refused path leaves nothing behind


def test_bench_help_lists_the_four_subcommands(capsys):
    with pytest.raises(SystemExit):
        _run(["bench", "--help"])
    text = capsys.readouterr().out
    for name in ("run", "score", "report", "publish"):
        assert name in text
```

- [ ] **Step 6: Run — expect FAIL** (`invalid choice: 'bench'`).

- [ ] **Step 7: Implement `agentcad/bench/cli.py`.** `add_bench_parser` builds the
  full argparse surface of design §9.2 (all four sub-subcommands, so `--help` is
  honest from this slice on). `cmd_bench` dispatches on
  `args.bench_command` and follows `cmd_check`'s skeleton **exactly**:

```python
def cmd_bench(args) -> int:
    from ..core import locks
    from ..core.model import AppError

    handler = {"run": _cmd_run, "score": _cmd_score,
               "report": _cmd_report, "publish": _cmd_publish}.get(
        getattr(args, "bench_command", None))
    if handler is None:
        print("agentcad bench: pick a subcommand: run, score, report, publish",
              file=sys.stderr)
        return 2
    return handler(args)


def _cmd_score(args) -> int:
    from ..cli import _accept_work_dir, _release_work_root
    from ..core import locks
    from ..core.model import AppError
    from ..core.tools import build_registry
    from .scoring import Scorer, refuse_scoring_overlap
    from .tasks import load_task

    service = None
    try:
        task = load_task(args.task, _tasks_root(args))
        submission = Path(args.submission).expanduser().resolve()
        projects_root = Path(tempfile.mkdtemp(prefix="agentcad-bench-projects-"))
        work_dir = _accept_work_dir(
            args.work_dir,
            lambda root: refuse_scoring_overlap(root, submission, task.root,
                                                projects_root))
        extra = [str(submission), str(task.root)] + ([work_dir] if work_dir else [])
        service = bench_service(projects_root, extra_writable=extra)
        locks.set_client_id("bench")
        scorer = Scorer(service, build_registry(service))
        score = scorer.score(task, submission, budget_s=args.budget,
                             work_dir=work_dir)
    except AppError as exc:
        print(f"agentcad bench score: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:      # noqa: BLE001 -- any harness failure is exit 2
        print(f"agentcad bench score: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:      # noqa: BLE001
                print(f"agentcad bench score: the kernel did not stop cleanly: "
                      f"{exc}", file=sys.stderr)
            _release_work_root(service)
    # Writing and printing are under the SAME mapping as the run: a traceback
    # out of here would be exit 1, the code reserved for "the model is wrong".
    try:
        if args.out:
            write_json(Path(args.out) / "score.json", score)
        _print_score(args, score)
    except Exception as exc:      # noqa: BLE001
        print(f"agentcad bench score: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 2 if not score.get("weights_effective") else 0
```

  `_print_score` renders one aligned table (subscore · status · value · weight ·
  contribution) plus the total, to **stderr**, suppressed by `--quiet`, replaced
  by `canonical_json(score)` on stdout under `--json` — `_print_check`'s
  conventions (`cli.py:853-876`).

- [ ] **Step 8: Edit `agentcad/cli.py` — edit 2 of 2.** In `main()`: the metavar at
  `cli.py:1446-1448` becomes
  `"{serve,open,mcp,new,export,check,bench,package,publish,admin}"`; after the
  `check` subparser block add
  ```python
  from .bench.cli import add_bench_parser      # lazy: `serve` pays nothing
  add_bench_parser(sub)
  ```
  and in the dispatch chain (`cli.py:1701-1706`) add
  ```python
      elif args.command == "bench":
          from .bench.cli import cmd_bench
          raise SystemExit(cmd_bench(args))
  ```

- [ ] **Step 9: Point `author.py` at the new parameter.** In
  `agentcad/bench/author.py`'s `main()`, change the service construction to
  `_build_service(projects_dir, examples=False)`.

- [ ] **Step 10: Run — expect PASS.**
  `uv run pytest tests/test_bench_cli.py -q`
  **Expected:** 6 passed.

- [ ] **Step 11: Smoke the real CLI (evidence).**
  ```
  uv run agentcad bench --help
  uv run agentcad bench score benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/reference/project \
      --task model_from_drawing/mfd_001_spacer_plate --out /tmp/bench-smoke
  echo "exit=$?"
  ```
  **Expected:** the table prints, `exit=0`, `/tmp/bench-smoke/score.json` has
  `"total": 1.0`.

- [ ] **Step 12: Changelog + report.** `docs/changelog/0259-bench-cli-score.md` —
  the two `cli.py` edits and why `examples` is a parameter, the exit-code table,
  the work-dir refusal. Cite `pytest tests/test_bench_cli.py -q` and the smoke
  output. **Orchestrator commits.**

**Advances:** FR9 (score half), Decision 9, D14, D16, D23.

---

## Task 5: The runner — budgeted `ChatEngine`, transcript, `run.json`, `bench run` (Decision 8, AC8)

**Files:**
- Create: `agentcad/bench/runner.py`
- Modify: `agentcad/bench/cli.py` (`_cmd_run`)
- Create: `tests/test_bench_runner.py`
- Reference: `agentcad/agent/chat.py:48-51` (constants), `:142-160` (`__init__`), `:164` (`available`), `:175` (`history`), `:185-204` (`start_turn`), `:238-244` (the API call), `:306-316` (the tool-call break), `:317-336` (the blanket except + `chat_done`), `:352` (`_repair_history`), `:101-129` (`_render_tool_result`); `tests/test_chat.py:39-66` (the fake client), `:100-102` (injection)

**Interfaces:**
- Consumes: `bench.tasks.{Task, prompt_text}` (Task 1), `bench.scoring.Scorer` (Task 3), `bench.cli.bench_service` (Task 4), `bench._json.write_json` (Task 1).
- Produces `agentcad/bench/runner.py`:
  - `RUN_SCHEMA = 1`; `BENCH_SCHEMA = 1`; `WALL_GRACE_S = 30.0`
  - `class BudgetExhausted(Exception)` with `.reason: str`
  - `class BudgetedClient` — exposes only `.messages.create(**kwargs)`
  - `budgeted_client_factory(inner_factory, *, deadline, max_tool_calls, max_api_turns) -> Callable[[], BudgetedClient]`
  - `@dataclass(frozen=True) class RunOutcome: over_budget: bool; stopped: str; usage: dict; transcript: list[dict]`
  - `run_task(task, *, service, registry, cell, model, api_key=None, client_factory=None) -> RunOutcome`
  - `transcript_payload(task, messages, *, cell, projects_root) -> dict`
  - `run_json(task, outcome, *, agent, model, started, finished) -> dict`
  - `STOPPED = ("model_ended_turn", "wall_clock", "tool_calls", "api_turns", "error")`

- [ ] **Step 1: Failing test — a scripted agent's work is scored (offline)**

```python
# tests/test_bench_runner.py
import json
import pytest
from pathlib import Path

from agentcad.bench import runner as bench_runner
from agentcad.bench import tasks as bench_tasks

SEED = "model_from_drawing/mfd_001_spacer_plate"


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _text(t):
    return _Block(type="text", text=t)


def _tool_use(tid, name, args):
    return _Block(type="tool_use", id=tid, name=name, input=args)


def _response(blocks, stop_reason="end_turn"):
    return _Block(content=blocks, stop_reason=stop_reason)


class FakeMessages:
    """Replays a scripted list of responses; records every request."""

    def __init__(self, script):
        self._script = list(script)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self._script:
            return _response([_text("done")])
        return self._script.pop(0)


class FakeAnthropic:
    def __init__(self, script):
        self.messages = FakeMessages(script)


REF_SCRIPT = (bench_tasks.load_task(SEED).reference_project
              / "parts" / "spacer_plate.py").read_text()


@pytest.fixture
def bench_cell(tmp_path):
    cell = tmp_path / "cell"
    (cell / "projects").mkdir(parents=True)
    return cell


def _service(bench_cell):
    from agentcad import cli as agentcad_cli
    return agentcad_cli._build_service(bench_cell / "projects", examples=False)


@pytest.mark.timeout(600)
def test_a_scripted_agent_produces_a_scoreable_project(bench_cell):
    from agentcad.core.tools import build_registry
    from agentcad.bench.scoring import Scorer

    task = bench_tasks.load_task(SEED)
    service = _service(bench_cell)
    try:
        registry = build_registry(service)
        script = [
            _response([_tool_use("t1", "create_part", {
                "project": task.target_project, "part_id": "spacer_plate",
                "script": REF_SCRIPT})], stop_reason="tool_use"),
            _response([_text("Created the spacer plate.")]),
        ]
        fake = FakeAnthropic(script)
        outcome = bench_runner.run_task(
            task, service=service, registry=registry, cell=bench_cell,
            model="fake-model", api_key="test-key",
            client_factory=lambda: fake)
        assert outcome.stopped == "model_ended_turn"
        assert outcome.over_budget is False
        assert outcome.usage["tool_calls"] == 1
        score = Scorer(service, registry).score(
            task, bench_cell / "projects" / task.target_project)
        assert score["subscores"]["built"]["value"] == 1.0
        assert score["total"] > 0.9
    finally:
        service.kernel.stop()
```

- [ ] **Step 2: Run — expect FAIL** (`agentcad.bench.runner` does not exist).
  `uv run pytest tests/test_bench_runner.py -x -q`

- [ ] **Step 3: Implement `BudgetedClient` and `run_task`**

```python
class BudgetExhausted(Exception):
    """The next API call is refused because a budget is spent.

    Raised INSIDE `messages.create`, so `ChatEngine._run_turn_locked`'s blanket
    handler (chat.py:317-327) catches it: the history is repaired, one
    `chat_delta` is published and `chat_done` fires in the `finally`. The turn
    ends cleanly and whatever the agent already wrote to disk is scoreable --
    which is AC8.
    """

    def __init__(self, reason: str):
        super().__init__(f"bench budget exhausted: {reason}")
        self.reason = reason


class BudgetedClient:
    def __init__(self, inner, *, deadline, max_tool_calls, max_api_turns):
        self._inner = inner
        self._deadline = deadline           # time.monotonic, never time.time
        self._max_tool_calls = max_tool_calls
        self._max_api_turns = max_api_turns
        self.api_turns = 0
        self.tool_calls = 0
        self.stopped: str | None = None
        self.messages = _BudgetedMessages(self)

    def _check(self, request_messages) -> None:
        # Counted from the request itself: every tool_use block the engine has
        # already sent is in the history it hands us, so no bus subscription
        # and no chat.py change is needed.
        self.tool_calls = sum(
            1 for message in request_messages
            for block in (message.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "tool_use")
        if self._deadline is not None and time.monotonic() > self._deadline:
            self.stopped = "wall_clock"
        elif self.tool_calls >= self._max_tool_calls:
            self.stopped = "tool_calls"
        elif self.api_turns >= self._max_api_turns:
            self.stopped = "api_turns"
        if self.stopped:
            raise BudgetExhausted(self.stopped)
        self.api_turns += 1
```

```python
def run_task(task, *, service, registry, cell, model, api_key=None,
             client_factory=None) -> RunOutcome:
    from ..agent.chat import ChatEngine

    project = _prepare_project(task, service, cell)   # create or copy `starter/`
    deadline = time.monotonic() + task.budgets.wall_s
    inner = client_factory
    tracker: dict = {}

    def factory():
        client = BudgetedClient(
            inner() if inner else None, deadline=deadline,
            max_tool_calls=task.budgets.turns,
            max_api_turns=task.budgets.api_turns)
        tracker["client"] = client
        return client

    engine = ChatEngine(registry, service.bus, model=model,
                        api_key=api_key or "bench", client_factory=factory)

    async def drive():
        turn = await engine.start_turn(project, prompt_text(task))
        await asyncio.wait_for(engine._tasks[turn["turn_id"]],
                               timeout=task.budgets.wall_s + WALL_GRACE_S)

    started = time.monotonic()
    stopped = "model_ended_turn"
    try:
        asyncio.run(drive())
    except asyncio.TimeoutError:
        # The client wrapper cannot preempt a TOOL already in flight; this is
        # the backstop for one hung call, and it is the same honest limitation
        # `agentcad check --budget` documents.
        stopped = "wall_clock"
    client = tracker.get("client")
    if client is not None and client.stopped:
        stopped = client.stopped
    return RunOutcome(
        over_budget=stopped != "model_ended_turn", stopped=stopped,
        usage={"wall_s": round(time.monotonic() - started, 3),
               "tool_calls": getattr(client, "tool_calls", 0),
               "api_turns": getattr(client, "api_turns", 0)},
        transcript=engine.history(project))
```

  `_prepare_project` creates `service.create_project(task.target_project)` when
  `task.starter_dir is None`, else `shutil.copytree(task.starter_dir, projects /
  name)` followed by `service.open_project(str(path))`.

  `budgets.api_turns` is derived in `tasks.Budgets` as `turns + 4`
  — never declared in `task.json` (design D11/§1.1).

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Failing test — over-budget still scores (AC8)**

```python
@pytest.mark.timeout(600)
def test_a_runaway_agent_is_stopped_and_still_scored(bench_cell):
    from agentcad.core.tools import build_registry
    from agentcad.bench.scoring import Scorer

    task = bench_tasks.load_task(SEED)
    service = _service(bench_cell)
    try:
        registry = build_registry(service)
        good = _response([_tool_use("t1", "create_part", {
            "project": task.target_project, "part_id": "spacer_plate",
            "script": REF_SCRIPT})], stop_reason="tool_use")
        spin = _response([_tool_use("tN", "get_project", {
            "project": task.target_project})], stop_reason="tool_use")
        fake = FakeAnthropic([good] + [spin] * 200)
        outcome = bench_runner.run_task(
            task, service=service, registry=registry, cell=bench_cell,
            model="fake-model", api_key="test-key", client_factory=lambda: fake)
        assert outcome.over_budget is True
        assert outcome.stopped in ("tool_calls", "api_turns")
        assert outcome.usage["tool_calls"] >= task.budgets.turns
        score = Scorer(service, registry).score(
            task, bench_cell / "projects" / task.target_project)
        assert score["subscores"]["built"]["value"] == 1.0   # the on-disk state
    finally:
        service.kernel.stop()


@pytest.mark.timeout(300)
def test_a_zero_wall_budget_stops_before_the_first_call(bench_cell):
    import dataclasses
    task = bench_tasks.load_task(SEED)
    task = dataclasses.replace(task, budgets=bench_tasks.Budgets(
        wall_s=0.0, turns=task.budgets.turns, api_turns=task.budgets.api_turns))
    from agentcad.core.tools import build_registry
    service = _service(bench_cell)
    try:
        outcome = bench_runner.run_task(
            task, service=service, registry=build_registry(service),
            cell=bench_cell, model="fake-model", api_key="test-key",
            client_factory=lambda: FakeAnthropic([]))
        assert outcome.stopped == "wall_clock"
        assert outcome.over_budget is True
    finally:
        service.kernel.stop()
```

- [ ] **Step 6: Run — expect PASS.**

- [ ] **Step 7: Failing test — the transcript is written, redacted and elided**

```python
def test_transcript_is_redacted_and_image_free(tmp_path):
    payload = bench_runner.transcript_payload(
        bench_tasks.load_task(SEED),
        [{"role": "user", "content": [{"type": "text",
                                       "text": f"wrote {tmp_path}/projects/x.py"}]},
         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                       "content": [{"type": "text",
                                                    "text": '{"png_base64": "AAAA"}'}]}]}],
        cell=tmp_path, projects_root=tmp_path / "projects")
    blob = json.dumps(payload)
    assert str(tmp_path) not in blob
    assert "<cell>" in blob
    assert "AAAA" not in blob and "<image omitted>" in blob
    assert payload["schema"] == 1


def test_run_json_carries_the_timestamps_score_json_does_not():
    task = bench_tasks.load_task(SEED)
    outcome = bench_runner.RunOutcome(
        over_budget=True, stopped="tool_calls",
        usage={"wall_s": 12.5, "tool_calls": 24, "api_turns": 13},
        transcript=[])
    doc = bench_runner.run_json(task, outcome, agent="builtin", model="m",
                                started="2026-08-19T10:00:00Z",
                                finished="2026-08-19T10:00:12Z")
    assert doc["over_budget"] is True and doc["stopped"] == "tool_calls"
    assert doc["budgets"]["turns"] == task.budgets.turns
    assert doc["transcript"] == "transcript.json"
    assert doc["started"].endswith("Z")
```

- [ ] **Step 8: Run — expect FAIL, implement, expect PASS.** `transcript_payload`
  replaces the cell root then the projects root (longest prefix first) and walks
  every string value replacing a `"png_base64"` value with `"<image omitted>"` —
  the string the bus event already uses (`chat.py:113-115`).

- [ ] **Step 9: Implement `_cmd_run` in `agentcad/bench/cli.py`.** For each
  selected task: a per-task cell under the run's work root, `run_task`, copy the
  project out to `<report>/tasks/<id>/submission/`, score it with `Scorer`, write
  `score.json`, `run.json` and `transcript.json`, then remove the cell. Write the
  run header `<report>/bench.json`. **Serial** — no `--jobs`, ever (design D21).
  Exit 0 when every selected task produced a score, 2 otherwise.

- [ ] **Step 10: Failing test — `bench run` refuses to touch the user's projects dir**

```python
def test_bench_run_requires_an_api_key_and_names_the_fix(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = _run(["bench", "run", "--tasks", SEED, "--report", str(tmp_path / "out")])
    assert code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
```

  (`_run` is the helper from `tests/test_bench_cli.py`; import it or repeat it.)

- [ ] **Step 11: Run — expect PASS.**
  `uv run pytest tests/test_bench_runner.py -q`
  **Expected:** 6 passed.

- [ ] **Step 12: Changelog + report.** `docs/changelog/0260-bench-runner.md` — the
  budgeted client factory and why it needs no `chat.py` change, the one-turn rule,
  over-budget semantics, transcript redaction. Cite
  `pytest tests/test_bench_runner.py -q`. **Orchestrator commits.**

**Advances:** FR8, AC8, Decision 8, D11, D12, D21.

---

## Task 6: `bench report` + the baseline gate (Decisions 10, 11, FR11)

**Files:**
- Create: `agentcad/bench/report.py`, `benchmarks/baseline.json`
- Modify: `agentcad/bench/cli.py` (`_cmd_report`)
- Create: `tests/test_bench_report.py`
- Reference: `agentcad/core/checks.py:602-671` (`render_markdown`), `:594-600` (`_capped`/`_more`), `:376` (`exit_code`)

**Interfaces:**
- Consumes: `bench._json.{read_json, write_json}` (Task 1), `bench.tasks.load_tasks` (Task 1).
- Produces `agentcad/bench/report.py`:
  - `REPORT_SCHEMA = 1`; `BASELINE_SCHEMA = 1`
  - `aggregate(results_dir: Path, *, tasks_root=None, expected: list[str] | None = None) -> dict`
    — `expected` names the task ids the report must cover; it defaults to
    `[t.id for t in load_tasks(root=tasks_root)]`, and an id in `expected`
    with no `score.json` is a `missing` row scoring `0.0`.
  - `compare_baseline(report: dict, baseline: dict, epsilon: float) -> dict`
  - `render_markdown(report: dict) -> str`
  - `report_exit_code(report: dict) -> int`

- [ ] **Step 1: Failing test — aggregation, missing tasks, and the gate**

```python
# tests/test_bench_report.py
import pytest
from pathlib import Path

from agentcad.bench import report as bench_report
from agentcad.bench._json import write_json


def _score(task_id, total, category):
    return {"schema": 1, "agentcad": "0.1.0", "harness": 1, "task": task_id,
            "task_set": "bench-v1", "task_version": 1, "category": category,
            "total": total, "weights_effective": {"built": 1.0},
            "subscores": {"built": {"value": total, "weight": 1.0,
                                    "status": "ok", "detail": {}}},
            "notes": []}


def _results(tmp_path, rows):
    for task_id, total, category in rows:
        out = tmp_path / "tasks" / task_id
        write_json(out / "score.json", _score(task_id, total, category))
        write_json(out / "run.json", {"schema": 1, "task": task_id,
                                      "over_budget": False, "agent": "builtin",
                                      "model": "m", "stopped": "model_ended_turn"})
    write_json(tmp_path / "bench.json", {"schema": 1, "agent": "builtin",
                                         "model": "m", "task_set": "bench-v1",
                                         "harness": 1, "agentcad": "0.1.0"})
    return tmp_path


def test_overall_total_is_the_mean_of_category_means(tmp_path):
    results = _results(tmp_path, [
        ("model_from_drawing/a", 1.0, "model_from_drawing"),
        ("model_from_drawing/b", 0.0, "model_from_drawing"),
        ("modify_to_spec/c", 1.0, "modify_to_spec"),
    ])
    report = bench_report.aggregate(results)
    assert report["categories"]["model_from_drawing"]["total"] == pytest.approx(0.5)
    assert report["total"] == pytest.approx(0.75)      # mean of {0.5, 1.0}
    assert report["n"] == 3


def test_a_missing_task_is_zero_and_flagged(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    report = bench_report.aggregate(results, tasks_root=None,
                                    expected=["model_from_drawing/a",
                                              "model_from_drawing/b"])
    assert report["tasks"]["model_from_drawing/b"]["missing"] is True
    assert report["tasks"]["model_from_drawing/b"]["total"] == 0.0
    assert report["categories"]["model_from_drawing"]["missing"] == 1


def test_a_regression_beyond_epsilon_is_exit_one(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 0.5, "model_from_drawing")])
    report = bench_report.aggregate(results)
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 1,
                "total": 0.9, "categories": {"model_from_drawing": 0.9},
                "tasks": {"model_from_drawing/a": 0.9}}
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02)
    assert report["baseline"]["status"] == "regressed"
    scopes = {r["scope"] for r in report["baseline"]["regressions"]}
    assert "total" in scopes and "category:model_from_drawing" in scopes
    assert bench_report.report_exit_code(report) == 1


def test_a_per_task_drop_alone_never_gates(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing"),
                                  ("model_from_drawing/b", 0.0, "model_from_drawing")])
    report = bench_report.aggregate(results)
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 1,
                "total": 0.5, "categories": {"model_from_drawing": 0.5},
                "tasks": {"model_from_drawing/a": 0.0,
                          "model_from_drawing/b": 1.0}}
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02)
    assert report["baseline"]["status"] == "ok"
    assert report["baseline"]["task_deltas"]           # printed, not gated
    assert bench_report.report_exit_code(report) == 0


def test_a_harness_mismatch_is_exit_two(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    report = bench_report.aggregate(results)
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 99, "total": 1.0,
                "categories": {}, "tasks": {}}
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02)
    assert report["baseline"]["status"] == "incomparable"
    assert bench_report.report_exit_code(report) == 2


def test_a_null_baseline_total_is_a_no_op(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 0.1, "model_from_drawing")])
    report = bench_report.aggregate(results)
    report["baseline"] = bench_report.compare_baseline(
        report, {"schema": 1, "task_set": "bench-v1", "harness": 1,
                 "total": None, "categories": {}, "tasks": {}}, 0.02)
    assert report["baseline"]["status"] == "unrecorded"
    assert bench_report.report_exit_code(report) == 0


def test_markdown_renders_the_category_table(tmp_path):
    report = bench_report.aggregate(_results(
        tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")]))
    text = bench_report.render_markdown(report)
    assert "model_from_drawing" in text and "1.0" in text
```

- [ ] **Step 2: Run — expect FAIL.** `uv run pytest tests/test_bench_report.py -x -q`

- [ ] **Step 3: Implement `agentcad/bench/report.py`.** `aggregate` walks
  `<results>/tasks/*/*/score.json`, reads the sibling `run.json` for
  `over_budget`, groups by `category`, and produces the document of design §10.
  `expected` falls back to the ids found on disk only when neither `expected`
  nor a readable tasks root is available. A score whose
  `(task_set, task_version, harness)` differs from the header's is included with
  a `warnings` sentence naming it.
  `compare_baseline` returns
  `{"path", "epsilon", "status", "regressions", "task_deltas"}` with
  `status ∈ {"ok", "regressed", "incomparable", "unrecorded"}`; it gates on
  `total` and each category only.
  `report_exit_code(report)` → `2` for `incomparable`, `1` for `regressed`, else `0`.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Implement `_cmd_report`** in `agentcad/bench/cli.py`: aggregate,
  optionally compare, write `--json-out` / `--md` atomically via `write_json` and
  `ProjectStore._atomic_write`, print the table unless `--quiet`, and return
  `report_exit_code`. **No service, no kernel** — this command is pure.

- [ ] **Step 6: Write `benchmarks/baseline.json`** with `total: null`:

```json
{
  "schema": 1,
  "task_set": "bench-v1",
  "harness": 1,
  "agent": "builtin",
  "model": "claude-sonnet-5",
  "agentcad": "0.1.0",
  "recorded": null,
  "source": null,
  "note": "No number is recorded yet. `bench report --baseline` on an unrecorded baseline exits 0 with a warning; the first scheduled run of .github/workflows/bench.yml records one (PRD-024 FR13, out of MVP scope).",
  "total": null,
  "categories": {},
  "tasks": {}
}
```

- [ ] **Step 7: Smoke the real command (evidence).**
  ```
  uv run agentcad bench report /tmp/bench-smoke-results --baseline benchmarks/baseline.json --epsilon 0.02
  echo "exit=$?"
  ```
  (Build `/tmp/bench-smoke-results` by hand from one `score.json` — the shape the
  test fixture uses.) **Expected:** the table prints, `exit=0`, with a
  "baseline is unrecorded" warning.

- [ ] **Step 8: Run** `uv run pytest tests/test_bench_report.py -q`.
  **Expected:** 7 passed.

- [ ] **Step 9: Changelog + report.** `docs/changelog/0261-bench-report-and-baseline.md`
  — mean-of-category-means, missing-is-a-regression, why per-task deltas are
  printed and not gated, the incomparable-is-exit-2 rule. Cite the test output.
  **Orchestrator commits.**

**Advances:** FR9 (report half), FR11, Decisions 10–11, D17, D18.

---

## Task 7: `bench publish` — the leaderboard and the full-disclosure rule (Decision 12, FR12, AC7)

**Files:**
- Create: `agentcad/bench/publish.py`, `benchmarks/leaderboard/rows/.gitkeep`
- Modify: `agentcad/bench/cli.py` (`_cmd_publish`)
- Create: `tests/test_bench_publish.py`
- Reference: `agentcad/server/routes_share_public.py` (the self-contained-page precedent), `agentcad/core/project.py:777` (`_atomic_write`)

**Interfaces:**
- Consumes: `bench._json.read_json` (Task 1), `bench.report.REPORT_SCHEMA` (Task 6), `bench.tasks.load_tasks` (Task 1).
- Produces `agentcad/bench/publish.py`:
  - `ROW_SCHEMA = 1`
  - `REQUIRED_ROW_KEYS = ("agent", "model", "agentcad", "harness", "task_set", "date", "submission", "transcript", "config", "harness_command")`
  - `row_problems(row: dict, report: dict, base: Path, expected_tasks: list[str]) -> list[str]`
  - `load_rows(leaderboard_dir: Path, expected_tasks: list[str]) -> tuple[list[dict], list[str]]`
  - `render_leaderboard(rows: list[dict], *, title: str) -> str`
  - `publish(leaderboard_dir: Path, out_path: Path, *, title: str, expected_tasks: list[str]) -> dict`

- [ ] **Step 1: Failing test — three rows render, and every rule rejects (AC7)**

```python
# tests/test_bench_publish.py
import pytest
from pathlib import Path

from agentcad.bench import publish as bench_publish
from agentcad.bench._json import write_json

TASKS = ["model_from_drawing/a", "modify_to_spec/b"]


def _report(total=0.6):
    return {"schema": 1, "task_set": "bench-v1", "harness": 1,
            "agentcad": "0.1.0", "agent": "builtin", "model": "m",
            "n": 2, "total": total,
            "categories": {"model_from_drawing": {"total": total, "n": 1,
                                                  "missing": 0},
                           "modify_to_spec": {"total": total, "n": 1,
                                              "missing": 0}},
            "tasks": {t: {"total": total, "over_budget": False,
                          "missing": False, "subscores": {}} for t in TASKS},
            "warnings": []}


def _row(row_id, **over):
    row = {"schema": 1, "id": row_id, "agent": "AgentCAD built-in chat agent",
           "harness_command": "agentcad bench run --set core",
           "model": "claude-sonnet-5", "agentcad": "0.1.0", "harness": 1,
           "task_set": "bench-v1", "date": "2026-08-19",
           "config": {"kernel_pool_size": 1},
           "submission": "https://example.invalid/s.tar.gz",
           "transcript": "https://example.invalid/t.tar.gz", "notes": ""}
    row.update(over)
    return row


def _board(tmp_path, rows):
    for row in rows:
        out = tmp_path / "rows" / row["id"]
        write_json(out / "row.json", row)
        write_json(out / "report.json", _report(row.pop("_total", 0.6)))
    return tmp_path


def test_three_rows_render_a_self_contained_page(tmp_path):
    board = _board(tmp_path, [_row("builtin"), _row("claude-mcp"), _row("kcl")])
    out = tmp_path / "index.html"
    result = bench_publish.publish(board, out, title="AgentCAD-Bench",
                                   expected_tasks=TASKS)
    html = out.read_text()
    assert result["rows"] == 3
    assert "<script" not in html
    assert "https://fonts." not in html and "cdn" not in html.lower()
    assert html.count("<tr") >= 4                      # header + 3 rows
    for row_id in ("builtin", "claude-mcp", "kcl"):
        assert row_id in html


def test_republishing_the_same_input_is_byte_identical(tmp_path):
    board = _board(tmp_path, [_row("builtin")])
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    bench_publish.publish(board, a, title="T", expected_tasks=TASKS)
    bench_publish.publish(board, b, title="T", expected_tasks=TASKS)
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.parametrize("mutate, needle", [
    (lambda r: r.pop("submission"), "submission"),
    (lambda r: r.__setitem__("transcript", ""), "transcript"),
    (lambda r: r.pop("config"), "config"),
    (lambda r: r.__setitem__("model", ""), "model"),
    (lambda r: r.__setitem__("task_set", "bench-v0"), "task_set"),
    (lambda r: r.__setitem__("harness", 99), "harness"),
])
def test_each_disclosure_rule_rejects_and_names_itself(tmp_path, mutate, needle):
    row = _row("bad")
    mutate(row)
    board = _board(tmp_path, [row])
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert needle in str(exc.value)
    assert not (tmp_path / "out.html").exists()        # nothing partial is written


def test_a_partial_run_is_not_a_row(tmp_path):
    board = _board(tmp_path, [_row("builtin")])
    partial = _report()
    partial["tasks"].pop("modify_to_spec/b")
    write_json(board / "rows" / "builtin" / "report.json", partial)
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "modify_to_spec/b" in str(exc.value)


def test_rows_are_ordered_by_total_then_id(tmp_path):
    board = _board(tmp_path, [_row("zzz", _total=0.9), _row("aaa", _total=0.9),
                              _row("mmm", _total=0.5)])
    out = tmp_path / "index.html"
    bench_publish.publish(board, out, title="T", expected_tasks=TASKS)
    html = out.read_text()
    assert html.index("aaa") < html.index("zzz") < html.index("mmm")
```

- [ ] **Step 2: Run — expect FAIL.** `uv run pytest tests/test_bench_publish.py -x -q`

- [ ] **Step 3: Implement `row_problems`** — the five rules of design §12, each
  producing a sentence containing the failing key's name.

- [ ] **Step 4: Implement `render_leaderboard`** — one f-string page: `<style>`
  inline (a light/dark-agnostic neutral palette, no web font), an `<h1>` title, a
  paragraph stating *what is measured (build validity, per-solid validity, PRD-003
  specs, geometry IoU against a checked-in reference, interference, metric
  windows) and what is not (no LLM judging anywhere, no human panel)*, the table,
  and a footer naming `agentcad bench score <submission> --task <id>` as the
  command that reproduces any row. No `<script>`, no remote asset.
  `publish()` validates **every** row first, raises `ValidationError` on the first
  problem list (nothing written), and only then writes through
  `ProjectStore._atomic_write`.

- [ ] **Step 5: Run — expect PASS.**

- [ ] **Step 6: Implement `_cmd_publish`** — exit 0 written, **1** a row rejected
  (print each problem to stderr), 2 harness. Default `-o docs/bench/index.html`.

- [ ] **Step 7: Smoke (evidence).** Build a two-row board under `/tmp`, run
  `uv run agentcad bench publish /tmp/board -o /tmp/board.html; echo "exit=$?"`,
  then open it: `uv run python -c "import webbrowser; webbrowser.open('file:///tmp/board.html')"`.
  **Expected:** `exit=0`, the page renders with no console-visible missing asset.

- [ ] **Step 8: Run** `uv run pytest tests/test_bench_publish.py -q`.
  **Expected:** 12 passed.

- [ ] **Step 9: Changelog + report.** `docs/changelog/0262-bench-publish-leaderboard.md`
  — the five disclosure rules, fail-closed and atomic, the stable ordering, the
  self-contained-page constraint. Cite the test output. **Orchestrator commits.**

**Advances:** FR12, AC7, Decision 12, D19.

---

## Task 8: Author the remaining `model_from_drawing` and all `modify_to_spec` tasks (9 tasks)

**Files:**
- Create: `benchmarks/tasks/model_from_drawing/{mfd_002_angle_bracket, mfd_003_head_flange, mfd_004_shaft_collar, mfd_005_vee_block}/`
- Create: `benchmarks/tasks/modify_to_spec/{mts_001_thin_the_nozzle, mts_002_bigger_pcb, mts_003_gusset_pattern, mts_004_lighter_flywheel, mts_005_m10_clamp}/`
- Modify: `agentcad/bench/author.py` (add the `drawing` subcommand)
- Modify: `tests/test_bench_tasks.py` (no change needed — it globs)
- Reference: `examples/construction/parts/angle_bracket.py`, `examples/rocketry/parts/{flange,nozzle}.py`, `examples/prototyping/parts/enclosure_base.py`, `examples/construction/parts/gusset_plate.py`, `examples/engine/parts/flywheel.py`, `examples/fasteners/parts/clamp_plate.py`; `agentcad/core/tools_drawing.py:30-52` (`generate_drawing`, `format="svg"`)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces `agentcad/bench/author.py`: `render_drawing(task_dir: Path, part_id: str, *, service, views=None) -> Path` and a `drawing` subcommand.

**The authoring checklist — every task in Tasks 8, 9 and 10 must satisfy all of it:**

1. `benchmarks/tasks/<category>/<id>/` with `id` matching `^[a-z][a-z0-9_]{2,47}$`.
2. `task.json` per design §1.1. `source` is `{"kind": "authored"}` or
   `{"kind": "derived", "example": "<dir>", "parts": [...]}`. `sets` is
   `["core"]`, plus `"fast"` for the CI subset (one task per category).
   `authored_against` is today's `agentcad.__version__`.
3. `prompt.md` **names the part id the agent must create** and **states the datum
   in words** (which face is on Z = 0, which axis the long dimension runs along,
   where the origin is). This is the frame-alignment mitigation; a reviewer reads
   `prompt.md` against `frame.datum` and they must agree.
4. `weights` are the category defaults (design §7.6) unless the task argues an
   override in a comment at the top of `prompt.md`.
5. `budgets.turns <= 30`; `budgets.wall_s` = 600 for single-part tasks, 900 for
   assembly and optimisation tasks.
6. `reference/project/` is a complete project directory whose part scripts carry
   **no** `SPECS` — the rubric lives in `specs/`.
7. `specs/parts/<part>.py` re-binds `SPECS` and imports every constructor under a
   `_bench_` alias. `specs/project.py` for assembly-scope checks. No
   `check_fem_static`.
8. `reference/steps/<part>.step` generated by the helper, never hand-edited.
9. `reference/metrics.json` seeded by the helper, then **hand-tightened**: a
   window must be tight enough to fail a wrong answer and loose enough that the
   reference passes with margin.
10. **The reference must score exactly 1.0** (Step 5 below). If it does not, the
    rubric is wrong, not the reference.
11. A derived task copies the example's script **into the bundle**; it never
    references `examples/` at run time.

**The authoring commands:**

```
# 1. write task.json, prompt.md, reference/project/, specs/
# 2. generate the datum and seed the windows
uv run python -m agentcad.bench.author step    benchmarks/tasks/<c>/<id>
uv run python -m agentcad.bench.author metrics benchmarks/tasks/<c>/<id>
# 3. (derived model-from-drawing tasks only) render the drawing asset
uv run python -m agentcad.bench.author drawing benchmarks/tasks/<c>/<id> --part <part_id>
# 4. hand-tighten reference/metrics.json, then prove it
uv run agentcad bench score benchmarks/tasks/<c>/<id>/reference/project --task <c>/<id>
```

- [ ] **Step 1: Implement `author.py drawing`**

```python
def render_drawing(task_dir: Path, part_id: str, *, service, views=None) -> Path:
    """Render `reference/project`'s part as a three-view SVG asset.

    Uses the product's own drawing path (`generate_drawing`, format="svg"), so a
    bench drawing is exactly the drawing the product produces -- the task cannot
    be easier or harder than the tool it measures.
    """
    from ..core.tools import build_registry

    raw = read_json(task_dir / "task.json")
    proj = service.open_project(str(task_dir / raw["reference"]["project"]))["name"]
    registry = build_registry(service)
    result = registry.call("generate_drawing", {
        "project": proj, "part_id": part_id, "format": "svg",
        "views": list(views or ["top", "front", "right"])})
    target = task_dir / "assets" / "drawing.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result["path"], target)
    return target
```

  (Use the registry's real call signature — check `agentcad/core/tools.py` for
  whether it is `registry.call(name, args)` or `registry.invoke(...)` and match it.)

- [ ] **Step 2: Author the four remaining `model_from_drawing` tasks** per design
  §7.1 and the checklist. `mfd_002`/`mfd_003` are derived (copy
  `examples/construction/parts/angle_bracket.py` and
  `examples/rocketry/parts/flange.py` into `reference/project/parts/`, strip their
  `SPECS`, keep the defaults) and get generated drawings; `mfd_004`/`mfd_005` are
  authored with hand-written SVGs.

- [ ] **Step 3: Author the five `modify_to_spec` tasks** per design §7.2. Each has
  a `starter/` project (the example at its shipped parameters) and a
  `reference/project/` (the same script at the target parameters). The rubric is
  `specs/parts/<part>.py` (mass/wall/bbox) plus `reference/metrics.json` windows
  on the changed dimensions.

- [ ] **Step 4: Generate every datum**
  ```
  for d in benchmarks/tasks/model_from_drawing/* benchmarks/tasks/modify_to_spec/*; do
    uv run python -m agentcad.bench.author step "$d" && \
    uv run python -m agentcad.bench.author metrics "$d" || echo "FAILED $d"
  done
  ```
  **Expected:** no `FAILED` lines; every `reference/steps/*.step` starts
  `ISO-10303-21;`.

- [ ] **Step 5: Prove every reference scores 1.0 (AC1 for these tasks)**
  ```
  for d in benchmarks/tasks/model_from_drawing/* benchmarks/tasks/modify_to_spec/*; do
    id="$(basename "$(dirname "$d")")/$(basename "$d")"
    uv run agentcad bench score "$d/reference/project" --task "$id" --json \
      | python3 -c "import json,sys; s=json.load(sys.stdin); \
        print(s['task'], s['total']); assert s['total'] == 1.0, s['subscores']"
  done
  ```
  **Expected:** nine lines, each ending `1.0`. A total below 1.0 means the rubric
  is wrong — tighten or loosen the failing window/spec, never the reference.

- [ ] **Step 6: Run the loader suite** `uv run pytest tests/test_bench_tasks.py -q`.
  **Expected:** `test_every_shipped_task_has_zero_problems` now covers 10 tasks.

- [ ] **Step 7: Changelog + report.** `docs/changelog/0263-bench-tasks-drawing-and-modify.md`
  — the nine tasks, their sources, and the datum each declares. Cite the Step 5
  output verbatim. **Orchestrator commits.**

**Advances:** FR2 (10 of 25), AC1 (partial), design §7.1–7.2.

---

## Task 9: Author all `fix_the_broken_part` and `assemble_and_clear` tasks (10 tasks)

**Files:**
- Create: `benchmarks/tasks/fix_the_broken_part/{fix_001_contract, fix_002_fillet, fix_003_wall_red, fix_004_hole_pattern, fix_005_invalid_shell}/`
- Create: `benchmarks/tasks/assemble_and_clear/{asm_001_thrust_chamber, asm_002_lid_on_base, asm_003_bolted_joint, asm_004_truss_node, asm_005_rod_and_piston}/`
- Reference: `examples/rocketry/specs.py:18-29` (the project-scope rubric to copy for `asm_001`), `examples/prototyping/`, `examples/fasteners/`, `examples/construction/`, `examples/engine/parts/{piston,wrist_pin,rod_body,rod_cap,rod_bolt_pair}.py`; `agentcad/toolkit/specs.py:261,271,283` (`check_interference_free`, `check_clearance`, `check_stackup`)

**Interfaces:** consumes Tasks 1–4 and the checklist in Task 8. Produces ten task bundles. No code.

- [ ] **Step 1: Author the five `fix_the_broken_part` tasks** per design §7.3.
  Each has a `starter/` whose part is broken in exactly one named way and a
  `reference/project/` that is the corrected script. `weights` are the `fix` row
  of design §7.6 (`built` 0.25, `valid` 0.15). `prompt.md` describes the
  **symptom**, never the fix — the agent must diagnose.
  For `fix_001_contract` the starter script must raise at build time; verify with
  `uv run agentcad bench score benchmarks/tasks/fix_the_broken_part/fix_001_contract/starter --task fix_the_broken_part/fix_001_contract`
  and confirm `built` is `0.0` with `status: "ok"` (**not** `error`).

- [ ] **Step 2: Author the five `assemble_and_clear` tasks** per design §7.4.
  Each `starter/` holds the parts with `assembly.instances` **empty**; the rubric
  is `specs/project.py` (`check_interference_free` + the task's
  `check_clearance` calls) and `weights` are the `asm` row (`interference` 0.40,
  `geometry` 0.00). `reference/project/` is the same project with the instances
  placed. `asm_001_thrust_chamber`'s `specs/project.py` is
  `examples/rocketry/specs.py:20-29` with the requirement ids kept.
  **`asm_005_rod_and_piston` must not include `engine_block`** — it is the
  slowest example part and the category needs many instances, not the biggest.

- [ ] **Step 3: Generate every datum**
  ```
  for d in benchmarks/tasks/fix_the_broken_part/* benchmarks/tasks/assemble_and_clear/*; do
    uv run python -m agentcad.bench.author step "$d" && \
    uv run python -m agentcad.bench.author metrics "$d" || echo "FAILED $d"
  done
  ```
  **Expected:** no `FAILED` lines.

- [ ] **Step 4: Prove every reference scores 1.0.** Same loop as Task 8 Step 5,
  over these ten directories. **Expected:** ten lines each ending `1.0`.
  For the `asm` tasks the `interference` subscore must read
  `{"checked": C(n,2), "pairs": []}` — an assembly with a clean pair list, not a
  short-circuit; if `checked` is 0 the reference has no instances.

- [ ] **Step 5: Prove the starters are NOT 1.0.** For each of the ten:
  ```
  uv run agentcad bench score benchmarks/tasks/<c>/<id>/starter --task <c>/<id> --json \
    | python3 -c "import json,sys; s=json.load(sys.stdin); \
      print(s['task'], s['total']); assert s['total'] < 0.95, 'starter already solves it'"
  ```
  **Expected:** ten lines, every total below 0.95. A starter that already scores
  high is not a task.

- [ ] **Step 6: Run** `uv run pytest tests/test_bench_tasks.py -q`.
  **Expected:** 20 tasks with zero problems.

- [ ] **Step 7: Changelog + report.** `docs/changelog/0264-bench-tasks-fix-and-assemble.md`
  — the ten tasks, what each starter breaks, and the starter-is-not-a-solution
  evidence. Cite Steps 4 and 5. **Orchestrator commits.**

**Advances:** FR2 (20 of 25), AC1 (partial), design §7.3–7.4.

---

## Task 10: Author all `optimize_under_constraints` tasks (5 tasks)

**Files:**
- Create: `benchmarks/tasks/optimize_under_constraints/{opt_001_lightest_bracket, opt_002_stiffest_gusset, opt_003_thinnest_lid, opt_004_most_bolts, opt_005_shortest_screw}/`
- Reference: design §7.5; `agentcad/toolkit/specs.py:218` (`check_that`)

**Interfaces:** consumes Tasks 1–4 and the Task 8 checklist. Produces five task bundles. No code.

- [ ] **Step 1: Author the five tasks** per design §7.5, with the `opt` weight row
  (`geometry` **0.00** — `not_applicable`; `metrics` 0.40; `specs` 0.45).
  Each objective is a **one-sided metric window** derived from the reference
  solution's achieved value with a stated slack, e.g. for `opt_001`:
  ```json
  {"name": "objective_mass", "part": "angle_bracket", "metric": "mass_g",
   "max": 214.7}
  ```
  where `214.7 = 1.05 * <the reference's measured mass>`. Write the multiplier and
  the measured value in a comment at the top of `prompt.md` so a reviewer can
  reproduce the number.

- [ ] **Step 2: Generate the datum.** `author.py metrics` for each, then replace
  the seeded windows with the objective window plus the constraint windows.
  `author.py step` is still run: `reference/steps/` is required by the loader only
  when the `geometry` weight is non-zero, and it is zero here — so **omit
  `reference.steps` from `task.json` entirely** and confirm the loader accepts it.
  **Expected:** `task_problems` returns `[]` with no `steps` key.

- [ ] **Step 3: Prove every reference scores 1.0.** Same loop as Task 8 Step 5.
  **Expected:** five lines each ending `1.0`, and `score["subscores"]["geometry"]["status"] == "not_applicable"`.

- [ ] **Step 4: Prove the starters do not.** Same loop as Task 9 Step 5.
  **Expected:** five totals below 0.95.

- [ ] **Step 5: Prove the objective is reachable but not free.** For each task,
  score a hand-made "half-way" project (the starter with the obvious first
  improvement applied) and record the total in `prompt.md`'s comment block.
  **Expected:** a total strictly between the starter's and 1.0 — evidence the
  score is graded, not a cliff.

- [ ] **Step 6: Run** `uv run pytest tests/test_bench_tasks.py -q`.
  **Expected:** **25 tasks**, five per category, all with zero problems. Add an
  assertion to `tests/test_bench_tasks.py`:
  ```python
  def test_the_shipped_set_is_five_per_category():
      from collections import Counter
      counts = Counter(t.category for t in bench_tasks.load_tasks())
      assert dict(counts) == {c: 5 for c in bench_tasks.CATEGORIES}
  ```

- [ ] **Step 7: Changelog + report.** `docs/changelog/0265-bench-tasks-optimize.md`
  — the five tasks, why `geometry` is `not_applicable` for the category, and how
  each objective window was derived. Cite Steps 3–6. **Orchestrator commits.**

**Advances:** FR2 (25 of 25), AC1 (all tasks authored), design §7.5.

---

## Task 11: Acceptance suite, CI workflow, the example submission, and docs (AC1, AC6, AC9)

**Files:**
- Create: `tests/test_prd024_acceptance.py`, `.github/workflows/bench.yml`, `docs/bench.md`, `benchmarks/examples/submission-mfd-001/`
- Modify: `docs/agent-api.md`, `docs/architecture.md`, `docs/geometry-ci.md`, `AGENTS.md`, `CLAUDE.md`, `docs/roadmap.md`, `.dockerignore`
- Reference: `.github/workflows/ci.yml` (job shape, the OCCT apt block), `.github/workflows/geometry-ci.yml:8-9` (the no-secrets rule), `tests/test_prd006_acceptance.py:484-495` (the `ci.yml` byte assertion this must not break), `docs/geometry-ci.md` (the doc shape to mirror)

**Interfaces:** consumes everything above. Produces no new module.

- [ ] **Step 1: Failing test — the acceptance suite**

```python
# tests/test_prd024_acceptance.py
"""PRD-024 acceptance. AC1/AC6 are slow (every reference is built and scored)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.bench import tasks as bench_tasks
from agentcad.bench.scoring import Scorer

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.slow
@pytest.mark.timeout(3600)
@pytest.mark.parametrize("task_id", [t.id for t in bench_tasks.load_tasks()])
def test_ac1_every_reference_scores_one(service_with_kernel, task_id):
    task = bench_tasks.load_task(task_id)
    score = Scorer(service_with_kernel).score(task, task.reference_project)
    assert score["total"] == pytest.approx(1.0, abs=1e-9), score["subscores"]


@pytest.mark.slow
@pytest.mark.timeout(1800)
@pytest.mark.parametrize("task_id", [
    t.id for t in bench_tasks.load_tasks() if t.reference_steps])
def test_the_checked_in_step_still_matches_its_script(service_with_kernel, task_id,
                                                      tmp_path):
    """STEP-drift guard (design D9): re-export and compare to the datum."""
    from agentcad.bench.author import export_reference
    task = bench_tasks.load_task(task_id)
    service = service_with_kernel
    proj = service.open_project(str(task.reference_project))["name"]
    for part_id, datum in task.reference_steps.items():
        fresh = tmp_path / f"{task_id.replace('/', '_')}_{part_id}.step"
        result = service.export_part(proj, part_id, "step")
        Path(fresh).write_bytes(Path(result["path"]).read_bytes())
        out = service.kernel.request("iou", {
            "candidate": {"source": str(fresh)},
            "reference": {"source": str(datum)},
            "align": "world", "rotations_deg": [[0.0, 0.0, 0.0]]},
            timeout_s=300.0)
        assert out["iou"] >= 0.9999, (task_id, part_id, out)
        assert abs(out["candidate_volume_mm3"] - out["reference_volume_mm3"]) \
            <= 1e-6 * out["reference_volume_mm3"]


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_ac6_the_example_external_submission_is_accepted_and_scored(service_with_kernel):
    task = bench_tasks.load_task("model_from_drawing/mfd_001_spacer_plate")
    submission = REPO / "benchmarks" / "examples" / "submission-mfd-001"
    score = Scorer(service_with_kernel).score(task, submission)
    assert score["schema"] == 1
    assert 0.0 <= score["total"] <= 1.0
    assert score["subscores"]["built"]["status"] == "ok"


def test_ac9_no_bench_module_imports_ocp_or_build123d():
    offenders = []
    for path in (REPO / "agentcad" / "bench").rglob("*.py"):
        text = path.read_text()
        if "import build123d" in text or "import OCP" in text or "from OCP" in text:
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_ac9_the_bench_adds_no_model_facing_tool(service_with_kernel):
    from agentcad.core.tools import build_registry
    names = set(build_registry(service_with_kernel).list_names())
    assert "iou" not in names
    assert not any(name.startswith("bench_") for name in names)


def test_the_bench_workflow_never_runs_the_secret_job_on_a_pull_request():
    text = (REPO / ".github" / "workflows" / "bench.yml").read_text()
    assert "ANTHROPIC_API_KEY" in text
    assert "pull_request_target" not in text
    guard, builtin = text.index("  guard:"), text.index("  builtin:")
    assert guard < builtin
    assert "needs: guard" in text[builtin:]
    assert "::notice" in text
    selftest = text[text.index("  selftest:"):]
    assert "secrets." not in selftest.split("  guard:")[0]


def test_ci_yml_is_untouched_by_the_bench():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "bench" not in ci
    assert ci.count("expect_sandbox: active") == 2      # test_prd006_acceptance
```

- [ ] **Step 2: Run — expect FAIL** (no `bench.yml`, no example submission).
  `uv run pytest tests/test_prd024_acceptance.py -q -m "not slow"`

- [ ] **Step 3: Create `benchmarks/examples/submission-mfd-001/`** — a project
  directory a real external agent could have produced for
  `model_from_drawing/mfd_001_spacer_plate`: `project.json` plus
  `parts/spacer_plate.py`, deliberately **imperfect** (e.g. R4 corners instead of
  R5) so the doc can show a real, non-trivial score. It must **not** be a copy of
  the reference. Add a `README.md` naming the task and the command that scores it.

- [ ] **Step 4: Create `.github/workflows/bench.yml`** — exactly the shape of
  design §13:

```yaml
name: bench
on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "0 6 * * 1"
  workflow_dispatch:

jobs:
  selftest:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    steps:
      # ... uv setup + the same OCCT apt block ci.yml uses ...
      - run: uv run pytest -q -n 2 --dist loadscope tests/test_bench_*.py tests/test_prd024_acceptance.py

  guard:
    runs-on: ubuntu-latest
    outputs:
      has_key: ${{ steps.k.outputs.has_key }}
    steps:
      - id: k
        run: |
          if [ -n "${{ secrets.ANTHROPIC_API_KEY }}" ]; then
            echo "has_key=true" >> "$GITHUB_OUTPUT"
          else
            echo "has_key=false" >> "$GITHUB_OUTPUT"
            echo "::notice title=AgentCAD-Bench::ANTHROPIC_API_KEY is not configured; the built-in agent run was skipped."
          fi

  builtin:
    needs: guard
    if: ${{ needs.guard.outputs.has_key == 'true' && github.event_name != 'pull_request' }}
    runs-on: macos-latest
    timeout-minutes: 60
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    steps:
      # ... uv setup ...
      - run: uv run agentcad bench run --set fast --agent builtin --report bench-out
      - run: |
          uv run agentcad bench report bench-out \
            --baseline benchmarks/baseline.json --epsilon 0.05 \
            --md "$GITHUB_STEP_SUMMARY" --json-out bench-out/report.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: bench-out
          path: bench-out
```

  The `builtin` job's `if:` carries **both** conditions — a fork PR must never
  reach a job that holds a secret and runs arbitrary agent-authored Python
  (`geometry-ci.yml:8-9`'s rule).

- [ ] **Step 5: Add `benchmarks/` to `.dockerignore`** beside the existing
  `tests/`, `docs/`, `scripts/`, `.github/` entries.

- [ ] **Step 6: Run — expect PASS** for the non-slow tests, then the slow ones.
  ```
  uv run pytest tests/test_prd024_acceptance.py -q -m "not slow"
  uv run pytest tests/test_prd024_acceptance.py -q -m slow
  ```
  **Expected:** the fast set passes; the slow set is 25 AC1 params + ~20 drift
  params + AC6, all green.

- [ ] **Step 7: Write `docs/bench.md`** — mirror `docs/geometry-ci.md`'s shape
  (`## Two rules to read the whole feature by` → `## The command` → …), covering:
  what is measured and what is not (no LLM judging anywhere); the task bundle
  format with the `task.json` of design §1.1; the SPECS format as it actually
  exists (part `SPECS` in the script, project `specs.py`); each subscore's exact
  computation; `score.json`'s schema and the determinism rules; the four commands
  and their exit codes; the baseline and the release gate; the leaderboard and the
  five disclosure rules; **the external-agent walkthrough** (`claude mcp add
  agentcad …`, hand the agent `prompt.md` and `assets/drawing.svg`, let it work in
  a scratch project, then `agentcad bench score <dir> --task <id>`) worked through
  `benchmarks/examples/submission-mfd-001` with its real score pasted in; the
  **authoring checklist** (Task 8, verbatim) and the `author.py` commands; and the
  honest non-guarantees — the monkeypatch note (design §3.1), per-task deltas not
  gated, and the contamination stance.

- [ ] **Step 8: Update the surrounding docs**
  - `docs/agent-api.md`: one paragraph — the bench adds **no** tool, route or
    event, and `iou` is kernel-internal by design, because a bench-only tool
    would contaminate the measurement.
  - `docs/architecture.md`: `agentcad/bench/` in the package list, and one
    sentence on the muzzled-copy scorer reusing `checks._ephemeral_service`.
  - `docs/geometry-ci.md`: a cross-reference line — `agentcad check` certifies a
    *project*, `agentcad bench` scores an *agent*, and both reuse the same
    headless-service pattern.
  - `docs/roadmap.md`: the PRD-024 row.
  - `AGENTS.md`: a "AgentCAD-Bench gotchas (PRD-024)" section with the traps.
  - `CLAUDE.md`: one condensed bullet in the traps list:

    > - Bench (`agentcad/bench/`, `kernel/handlers/bench.py`, `benchmarks/`):
    >   **`error` means the harness could not measure; `not_applicable` is
    >   declared by `task.json` (weight 0) and never by a run** — an absent,
    >   broken or mesh-only candidate measures **zero**, because excluded
    >   subscores renormalise and the alternative rewards destroying evidence ·
    >   IoU booleans **only the intersection** (`union = volA + volB − inter`,
    >   never `|`), both sides solids-decomposed, `inter` clamped to
    >   `min(Σ, volA, volB)` · a mesh side short-circuits before any boolean ·
    >   the rubric is **injected into a copy and re-binds `SPECS`**, discarding
    >   the candidate's own · `score.json` carries **no timestamp/host/path**
    >   (they live in `run.json`) and is `sort_keys` + `round(x, 6)` +
    >   `allow_nan=False` · `task.json` may not declare `turns >
    >   MAX_TOOL_CALLS_PER_TURN`, so a task is **one chat turn** · budgets are
    >   enforced in a **client-factory wrapper**, never in `chat.py` ·
    >   `_build_service(examples=False)` is load-bearing (a derived task must not
    >   be solvable by opening the example) · the bench CI job **never runs on
    >   `pull_request`** and lives in its own workflow file (`ci.yml` is
    >   byte-asserted) · the reference **script** is the solution, the reference
    >   **STEP** is the datum · no fan-out, no `--jobs`.

- [ ] **Step 9: Full targeted run**
  ```
  uv run pytest tests/test_bench_tasks.py tests/test_bench_kernel_iou.py \
      tests/test_bench_scoring.py tests/test_bench_cli.py \
      tests/test_bench_runner.py tests/test_bench_report.py \
      tests/test_bench_publish.py tests/test_prd024_acceptance.py -q
  ```
  **Expected:** all green. Cite the count.

- [ ] **Step 10: Changelog + report.** `docs/changelog/0266-bench-acceptance-ci-docs.md`
  — the acceptance suite, the workflow and why it is separate and secret-gated,
  the example submission, and every doc touched. Cite Step 9's output. **The
  orchestrator runs `make test`, fills the full count, and commits.**

**Advances:** AC1, AC6, AC9, FR10, FR3, Decision 13, D20, D22, and the DoD's docs
requirement.

---

## Self-review (spec coverage)

**Every FR → task.** FR1 (bundle format) → 1. FR2 (25 tasks, references validated) → 1, 8, 9, 10, 11. FR3 (no `[fem]`, no network) → 1 (loader refuses `check_fem_static`), Global Constraints, 11. FR4 (`score.json`) → 3. FR5 (IoU, alignment, mesh) → 2, 3. FR6 (byte-identical, versions embedded) → 1 (`_json`), 3. FR7 (honest degradation) → 2, 3. FR8 (`bench run`, budgets, transcripts) → 5. FR9 (`bench score`, `bench report`) → 4, 6. FR10 (external walkthrough + example submission) → 11. FR11 (baseline gate) → 6. FR12 (`bench publish`, full disclosure) → 7. FR13 (launch results) → **Phase 2, not built** (the renderer and the row format exist; the numbers need paid runs).

**Every AC → task.** AC1 → 11 (`test_ac1_every_reference_scores_one`, parametrised over all 25), with per-slice proof in 8/9/10. AC2 → 3. AC3 → 3. AC4 → 2. AC5 → 11 (`.github/workflows/bench.yml` `builtin` job, secret-gated) + 5 (the offline fake-client proof). AC6 → 11. AC7 → 7. AC8 → 5. AC9 → 11 (import guard + no-tool guard) and the Global Constraints.

**Every Decision → task.** D1 (Decision 1, bundle) → 1. D2 (Decision 2, loader) → 1. D3 (Decision 3, muzzled copy) → 3. D4 (Decision 4, subscores) → 3. D5 (Decision 5, `iou`) → 2. D6 (Decision 6, determinism) → 1 + 3. D7 (Decision 7, the 25 tasks) → 8, 9, 10. D8 (Decision 8, runner) → 5. D9 (Decision 9, CLI) → 4 + 5 + 6 + 7. D10 (Decision 10, report) → 6. D11 (Decision 11, baseline) → 6. D12 (Decision 12, publish) → 7. D13 (Decision 13, CI) → 11. Ledger rulings D1–D23 all land: D1–D3 → 1/3; D4 → 11 (docs); D5 → 3 (with its own test); D6 → 3; D7–D8 → 2; D9 → 8/9/10 + 11 (drift test); D10 → 1; D11–D12 → 5; D13 → 5 (text-only assets) + 1 (`ASSET_SUFFIXES`); D14 → 4; D15 → 1; D16 → 4/6/7; D17–D18 → 6; D19 → 7; D20 → 11; D21 → 5 (serial, stated); D22 → 1 + 11; D23 → 4.

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Every code block is either complete or an explicitly-marked skeleton whose remaining branches are enumerated by section reference (design §2 rules 1–10, §4.1–4.6, §7.1–7.5, §12 rules 1–5) rather than by "etc.". The two places a reader must consult the real code rather than this plan are named as such: `ToolRegistry`'s call/list accessor names (Task 2 Step 1, Task 8 Step 1) and the `Location` composition order (Task 2 Step 4), each with the test that arbitrates.

**Name consistency.** `HARNESS_VERSION` (1) → used in 3, 6, 7. `canonical_json`/`write_json`/`read_json`/`round_floats` (1) → 3, 5, 6, 7. `Task`/`Frame`/`Budgets`/`MetricWindow`/`load_task`/`load_tasks`/`task_problems`/`prompt_text`/`tasks_root`/`METRIC_KEYS`/`ALIGN_MODES`/`SUBSCORES`/`CATEGORIES` (1) → 3, 4, 5, 8–11. `iou` handler params/result keys (2) → consumed verbatim by `Scorer._geometry` (3) and by the drift test (11). `Scorer`/`inject_rubric`/`total_of`/`refuse_scoring_overlap`/`IOU_TIMEOUT_S` (3) → 4, 5, 11. `add_bench_parser`/`cmd_bench`/`bench_service` (4) → 5, 6, 7; `_build_service(..., examples=)` (4) → 5, and Task 1's `author.py` is retrofitted in Task 4 Step 9. `BudgetedClient`/`BudgetExhausted`/`RunOutcome`/`run_task`/`transcript_payload`/`run_json`/`STOPPED` (5) → 6 reads the `run.json` it writes. `aggregate`/`compare_baseline`/`render_markdown`/`report_exit_code`/`REPORT_SCHEMA` (6) → 7 validates the `report.json` shape 6 produces. `row_problems`/`render_leaderboard`/`publish`/`REQUIRED_ROW_KEYS` (7) → 11 (docs). `export_reference`/`seed_metrics`/`render_drawing` (1, 8) → 8, 9, 10, 11.

**Changelog numbers.** 0256 (T1), 0257 (T2), 0258 (T3), 0259 (T4), 0260 (T5), 0261 (T6), 0262 (T7), 0263 (T8), 0264 (T9), 0265 (T10), 0266 (T11) — contiguous, one per task, each written from the actual diff with the targeted test output cited by the implementer and the full `make test` count filled in by the orchestrator.
