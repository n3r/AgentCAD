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


def test_the_shipped_set_is_five_per_category():
    """The v1 task set is 25 tasks, five in each of the five categories.

    Equality, not membership, and deliberately so: this lands with the last
    authored bundle, so from here on a task added, removed or filed under the
    wrong category is a red test rather than a quiet change to what
    `bench-v1` means. `load_tasks` is used rather than a glob because it is
    the loader every consumer goes through, so a bundle that globs but does
    not LOAD fails here too.
    """
    from collections import Counter
    counts = Counter(task.category for task in bench_tasks.load_tasks())
    assert dict(counts) == {name: 5 for name in bench_tasks.CATEGORIES}
    assert sum(counts.values()) == 25


def test_exactly_one_task_per_category_is_in_the_fast_set():
    """`--set fast` is the CI subset and the paid `builtin` job's whole input.

    One per category, five in total: fewer leaves a category unmeasured on the
    schedule, more multiplies the API spend of every scheduled run. Membership
    is declared per bundle, so nothing but a test keeps the shape.
    """
    from collections import Counter

    fast = [task for task in bench_tasks.load_tasks(set_name="fast")]
    counts = Counter(task.category for task in fast)
    assert dict(counts) == {name: 1 for name in bench_tasks.CATEGORIES}
    assert len(fast) == 5
    # `core` is every task, so `fast` is a strict subset and never a lane of
    # its own: a task in `fast` but not `core` would be scored by the schedule
    # and left out of the roster the report's denominator comes from.
    for task in fast:
        assert "core" in task.sets, task.id


#: The design §7.6 category default weight vectors, written out rather than
#: read from a bundle: the point of the test below is that a bundle agrees
#: with the design, and a fixture derived from the bundles would agree with
#: itself no matter what anyone edited.
CATEGORY_DEFAULT_WEIGHTS = {
    "model_from_drawing": {"built": 0.15, "valid": 0.10, "specs": 0.10,
                           "geometry": 0.50, "interference": 0.00,
                           "metrics": 0.15},
    "modify_to_spec": {"built": 0.10, "valid": 0.05, "specs": 0.40,
                       "geometry": 0.30, "interference": 0.00,
                       "metrics": 0.15},
    "fix_the_broken_part": {"built": 0.25, "valid": 0.15, "specs": 0.35,
                            "geometry": 0.15, "interference": 0.00,
                            "metrics": 0.10},
    "assemble_and_clear": {"built": 0.10, "valid": 0.05, "specs": 0.35,
                           "geometry": 0.00, "interference": 0.40,
                           "metrics": 0.10},
    "optimize_under_constraints": {"built": 0.10, "valid": 0.05, "specs": 0.45,
                                   "geometry": 0.00, "interference": 0.00,
                                   "metrics": 0.40},
}

#: The two overrides the v1 set ships, each argued in an HTML comment at the
#: top of its own `prompt.md` (design §7.6's rule: an override is allowed, an
#: *unargued* one is not).
#:
#: * `mts_005_m10_clamp` moves 0.10 of `geometry` to `interference`: the task
#:   is a two-plate bolted joint, so non-interference is part of the answer.
#: * `fix_005_invalid_shell` moves its whole 0.15 `geometry` weight onto
#:   `metrics` (0.10 -> 0.25): the part is a swept pipe and a swept surface
#:   does not survive the STEP round trip as a boolean operand, so `geometry`
#:   would score every submission zero.
WEIGHT_OVERRIDES = {
    "modify_to_spec/mts_005_m10_clamp": {"geometry": 0.20,
                                         "interference": 0.10},
    "fix_the_broken_part/fix_005_invalid_shell": {"geometry": 0.00,
                                                  "metrics": 0.25},
}


def test_every_task_carries_its_category_default_weights_or_an_override():
    overridden = set()
    for task in bench_tasks.load_tasks():
        expected = dict(CATEGORY_DEFAULT_WEIGHTS[task.category])
        override = WEIGHT_OVERRIDES.get(task.id)
        if override:
            overridden.add(task.id)
            expected.update(override)
        assert task.weights == pytest.approx(expected), task.id
        # An override still has to be argued where a reviewer reads it, and
        # `prompt_text` strips the block before the agent ever sees it.
        if override:
            body = task.prompt_path.read_text(encoding="utf-8")
            assert "<!--" in body, task.id
            assert "<!--" not in bench_tasks.prompt_text(task), task.id
    assert overridden == set(WEIGHT_OVERRIDES), sorted(overridden)


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


def test_a_scored_metrics_weight_needs_at_least_one_window(tmp_path):
    """Design §2 rule 8: the weight and the windows are two halves of one claim.

    The document parses, the schema is right and every declared window is
    well-formed -- there are just none of them. Before this was checked the
    task validated clean while carrying a 0.15 `metrics` weight.
    """
    raw, base = _seed_raw(tmp_path)
    metrics = base / "reference" / "metrics.json"
    metrics.write_text(json.dumps({"schema": 1, "windows": []}))
    problems = bench_tasks.task_problems(raw, base)
    assert any("declares zero windows" in p for p in problems), problems
    # ... and it is only a defect while the subscore is actually weighted.
    raw["weights"]["metrics"] = 0.0
    raw["weights"]["geometry"] = 0.65
    assert bench_tasks.task_problems(raw, base) == []


def test_specs_block_may_not_augment_SPECS(tmp_path):
    """`SPECS +=` extends the candidate's own list instead of replacing it."""
    raw, base = _seed_raw(tmp_path)
    block = base / "specs" / "parts" / "spacer_plate.py"
    block.write_text(block.read_text().replace("SPECS = [", "SPECS += ["))
    problems = bench_tasks.task_problems(raw, base)
    assert any("SPECS +=" in p for p in problems), problems


def test_load_task_raises_with_the_problem_list():
    with pytest.raises(ValidationError) as exc:
        bench_tasks.load_task("model_from_drawing/does_not_exist")
    assert "does_not_exist" in exc.value.message


def test_load_tasks_filters_by_glob_and_set():
    # Membership, not equality: mfd_002..005 land in a later slice and must not
    # turn this into a failing test about how many tasks are shipped.
    found = [t.id for t in bench_tasks.load_tasks(glob="model_from_drawing/*")]
    assert SEED in found and len(found) >= 1
    assert all(t.startswith("model_from_drawing/") for t in found)
    assert SEED not in [t.id for t in bench_tasks.load_tasks(glob="fix_*/*")]
    assert bench_tasks.load_tasks(set_name="core")
    assert bench_tasks.load_tasks(set_name="no-such-set") == []


def test_prompt_text_inlines_every_asset_as_text():
    task = bench_tasks.load_task(SEED)
    text = bench_tasks.prompt_text(task)
    assert task.prompt_path.read_text().strip() in text
    assert "attachment: assets/drawing.svg" in text
    assert "<svg" in text


def test_prompt_text_strips_the_reviewer_html_comment():
    """A weight-override argument is grading rationale, not prompt.

    Design §7.6 tells a task that overrides its category weights to argue it
    "in a comment at the top of its `prompt.md`", and two shipped bundles do.
    That text names which subscore carries which weight, and an agent that
    reads "geometry is 0.00 here" spends its budget differently — so the
    comment stays in the file for the reviewer and is stripped on the way to
    the model. The stripping is the prompt BODY's only mutation: an asset is
    attached verbatim, because an SVG's own comments are part of the drawing.
    """
    task = bench_tasks.load_task("fix_the_broken_part/fix_005_invalid_shell")
    raw = task.prompt_path.read_text(encoding="utf-8")
    assert raw.lstrip().startswith("<!--") and "Weight override" in raw

    text = bench_tasks.prompt_text(task)
    assert "<!--" not in text and "-->" not in text
    assert "Weight override" not in text and "geometry` is 0.00" not in text
    # ... and the prompt itself is intact, first line to last.
    assert text.startswith("The project holds one part, `coolant_elbow`")
    assert "24 mm centre-line bend radius" in text
    assert text.rstrip().endswith("the fix must not move it.")


def test_strip_reviewer_comments_is_non_greedy_and_closes_the_hole():
    # Two comments, not one span from the first `<!--` to the last `-->`, and
    # no three-newline crater where they were.
    text = bench_tasks.strip_reviewer_comments(
        "<!-- one\nspans lines -->\n\nkeep me\n\n<!-- two -->\n\nand me\n")
    assert text == "keep me\n\nand me"
    assert "spans lines" not in text and "\n\n\n" not in text
    # A prompt with no comment is returned stripped and otherwise untouched.
    assert bench_tasks.strip_reviewer_comments("a\n\nb\n") == "a\n\nb"


def test_canonical_json_is_byte_identical_and_refuses_nan():
    from agentcad.bench._json import canonical_json
    payload = {"b": 1 / 3, "a": [3.0000000001, 2]}
    assert canonical_json(payload) == canonical_json(dict(payload))
    assert b'"b": 0.333333' in canonical_json(payload)
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_round_floats_leaves_bools_alone():
    from agentcad.bench._json import round_floats
    out = round_floats({"flag": True, "count": 3, "ratio": 1 / 3})
    assert out["flag"] is True and isinstance(out["flag"], bool)
    assert out["count"] == 3 and isinstance(out["count"], int)
    assert out["ratio"] == 0.333333


def test_read_json_refuses_by_size_before_parsing(tmp_path):
    from agentcad.bench._json import read_json
    path = tmp_path / "big.json"
    path.write_text(json.dumps({"pad": "x" * 4096}))
    with pytest.raises(ValidationError) as exc:
        read_json(path, max_bytes=64)
    assert "refused before parsing" in exc.value.message


def test_read_json_catches_recursion_error(tmp_path):
    """`json.loads` raises RecursionError, which is NOT a ValueError."""
    from agentcad.bench._json import read_json
    path = tmp_path / "deep.json"
    path.write_text("[" * 100_000 + "]" * 100_000)
    with pytest.raises(ValidationError) as exc:
        read_json(path)
    # Pinned to the recursion path: the document is 200 kB, far under the size
    # ceiling, so a pass here cannot come from the cheaper refusal.
    assert "not readable JSON" in exc.value.message
    assert "recursion" in exc.value.message
