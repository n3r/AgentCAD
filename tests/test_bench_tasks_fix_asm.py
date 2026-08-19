"""Authoring invariants of the `fix_the_broken_part` and `assemble_and_clear`
bundles (PRD-024, design §7.3–§7.4).

Nothing here builds geometry or starts a kernel, and nothing writes into
`benchmarks/` — every check is a read over the shipped bundles, so the file is
parallel-safe and costs milliseconds. The expensive halves of these tasks'
correctness (the reference scores 1.0, the starter does not) are proved by
`agentcad bench score` and cited in the changelog; what is pinned here is the
class of authoring mistake that is silent otherwise:

* a `fix` task whose starter is not actually broken — identical to the
  reference — which would make the task a no-op nobody notices;
* an assembly rubric naming an instance id the reference never places, which
  reads as a failing check rather than as a typo;
* an `asm` starter that already ships the instances it is meant to ask for;
* a reference or starter part script that declares its own `SPECS`, which
  design §1 consequence 3 forbids so that the reference is measured against
  exactly what every other submission is measured against.
"""
import ast
import json
from pathlib import Path

import pytest

from agentcad.bench import tasks as bench_tasks
from agentcad.core.specs import declares_specs

FIX = "fix_the_broken_part"
ASM = "assemble_and_clear"


def _tasks(category):
    return [task for task in bench_tasks.load_tasks(glob=f"{category}/*")]


def _manifest(path: Path) -> dict:
    return json.loads((path / "project.json").read_text(encoding="utf-8"))


def _instance_ids(manifest: dict) -> set:
    return {inst["id"]
            for inst in (manifest.get("assembly") or {}).get("instances") or []}


def _clearance_instances(text: str) -> set:
    """Every instance id a `check_clearance(...)` call names, by AST.

    Read structurally rather than by regex: the rubric is Python, and the two
    positional arguments of `check_clearance` are the pair it measures.
    """
    named: set = set()
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else \
            getattr(func, "id", None)
        if name != "check_clearance":
            continue
        for arg in node.args[:2]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                named.add(arg.value)
    return named


@pytest.mark.parametrize("category", [FIX, ASM])
def test_the_category_ships_five_tasks_each_with_a_starter(category):
    found = _tasks(category)
    assert len(found) == 5, [task.id for task in found]
    for task in found:
        assert task.starter_dir is not None, task.id
        assert (task.starter_dir / "project.json").is_file(), task.id


def test_every_fix_starter_differs_from_its_reference():
    for task in _tasks(FIX):
        starter, reference = task.starter_dir, task.reference_project
        scripts_differ = any(
            (starter / "parts" / f"{part}.py").read_text(encoding="utf-8")
            != (reference / "parts" / f"{part}.py").read_text(encoding="utf-8")
            for part in task.target_parts)
        params_differ = _params(starter) != _params(reference)
        assert scripts_differ or params_differ, (
            f"{task.id}: the starter is identical to the reference, so the "
            f"task asks for nothing")


def _params(project: Path) -> dict:
    return {part["id"]: part.get("params") or {}
            for part in _manifest(project)["parts"]}


def test_every_assembly_starter_places_no_instances():
    for task in _tasks(ASM):
        assert _instance_ids(_manifest(task.starter_dir)) == set(), task.id


def test_every_assembly_reference_places_at_least_two_instances():
    # Fewer than two and `check_interference_free` skips `no_instances`, which
    # the scorer counts as a FAILURE — the reference could not score 1.0.
    for task in _tasks(ASM):
        placed = _instance_ids(_manifest(task.reference_project))
        assert len(placed) >= 2, (task.id, placed)


def test_every_assembly_rubric_names_instances_its_reference_places():
    for task in _tasks(ASM):
        assert task.specs_project_path is not None, task.id
        named = _clearance_instances(
            task.specs_project_path.read_text(encoding="utf-8"))
        assert named, f"{task.id}: the project rubric measures no clearance"
        placed = _instance_ids(_manifest(task.reference_project))
        assert named <= placed, (task.id, sorted(named - placed))


def test_every_assembly_prompt_names_every_instance_id_the_rubric_uses():
    # The fairness bar: an agent that does exactly what the prompt says cannot
    # fail a check it was never told about, and a `check_clearance` row is
    # addressed to instance ids by name.
    for task in _tasks(ASM):
        prompt = bench_tasks.prompt_text(task)
        for instance in sorted(_clearance_instances(
                task.specs_project_path.read_text(encoding="utf-8"))):
            assert f"`{instance}`" in prompt, (task.id, instance)


@pytest.mark.parametrize("category", [FIX, ASM])
def test_no_shipped_project_script_declares_its_own_specs(category):
    for task in _tasks(category):
        projects = [task.reference_project]
        if task.starter_dir is not None:
            projects.append(task.starter_dir)
        for project in projects:
            for script in sorted((project / "parts").glob("*.py")):
                assert not declares_specs(
                    script.read_text(encoding="utf-8")), \
                    f"{task.id}: {script.name} declares SPECS"


def test_every_fix_task_scores_geometry_or_says_why_in_its_prompt():
    # A `fix` task may drop the geometry weight, but design §7.6 requires the
    # override to be argued where a reviewer will read it.
    for task in _tasks(FIX):
        if task.weights["geometry"] > 0.0:
            continue
        prompt = task.prompt_path.read_text(encoding="utf-8")
        assert prompt.lstrip().startswith("<!--"), task.id
        assert "geometry" in prompt.split("-->")[0], task.id
