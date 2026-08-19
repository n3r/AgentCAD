"""The bench task format and its loader (PRD-024, design §1–§2).

Nothing here builds geometry or starts a kernel: the loader is a pure reader
over `benchmarks/`, and `benchmarks/` is a read-only input — every test that
mutates a bundle does it on a `tmp_path` copy.
"""
import json
from pathlib import Path

import pytest

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
