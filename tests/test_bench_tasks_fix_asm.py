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


#: Text a `fix_the_broken_part` **starter** may never contain, per task.
#:
#: The runner copies `starter/` verbatim into the agent's scratch project, so
#: every byte of it is prompt. A starter that names its own defect — or that
#: still carries the reference's docstring — is not a diagnosis task, it is a
#: typing exercise, and the failure is silent: the bundle still loads, the
#: reference still scores 1.0 and the starter still scores low.
#:
#: The entries are prose that NAMES the defect, never a number or an
#: identifier the script legitimately declares: `enclosure_base` really does
#: default `wall` to 2.5 and `coolant_elbow` really does default `bend_r` to
#: 24, because in both tasks the defect is the value STORED in the manifest,
#: and noticing that the stored value disagrees with the script's own default
#: is the diagnosis rather than a leak of it. Nor is a parameter's own
#: `description` ("Centre-line bend radius at the corner") a leak: it names the
#: parameter, which the reference declares identically, and not the defect.
FIX_STARTER_FORBIDDEN = {
    "fix_001_contract": ("misspel", "AttributeError", "SimpleNamespace"),
    "fix_002_fillet": ("safe_fillet", "max_fillet", "exceeds"),
    "fix_003_wall_red": ("breakage", "stored parameter", "SAME script",
                         "too thin"),
    "fix_004_hole_pattern": ("edited line", "off-by-one", "grid(2, 2"),
    "fix_005_invalid_shell": ("self-intersect", "crosses itself",
                              "folds through", "one tube diameter",
                              "tightest bend"),
}

#: Text NO starter may contain, whatever the task or the category.
#:
#: Two classes, and the second one is why this list grew. **An answer key** —
#: "Reference solution", "the reference" — is the obvious leak. **Harness meta
#: and a strategy hint** are the quiet one: a starter header explaining that
#: "the starter and the reference are the SAME script at different parameters"
#: tells the agent the task is a parameter edit and never a rewrite, and one
#: naming `specs/parts/` tells it a rubric it cannot see is about to be
#: injected. Both were written for a maintainer reading the bundle; a
#: maintainer reads the **reference** side, which keeps the full note. Every
#: starter header is trimmed to the one-line provenance form
#: `# Copied from examples/<...>.py into this project.`
STARTER_FORBIDDEN_ANYWHERE = ("Reference solution", "rubric", "Rubric",
                              "specs/parts", "consequence 3",
                              "the reference", "SAME script", "SAME scripts",
                              "registers no examples", "_reference",
                              "bench task", "design §1")


def test_no_fix_starter_script_leaks_its_own_defect():
    tasks = {task.id.split("/")[1]: task for task in _tasks(FIX)}
    assert set(tasks) == set(FIX_STARTER_FORBIDDEN), sorted(tasks)
    for name, task in sorted(tasks.items()):
        for script in sorted((task.starter_dir / "parts").glob("*.py")):
            text = script.read_text(encoding="utf-8")
            forbidden = (FIX_STARTER_FORBIDDEN[name]
                         + STARTER_FORBIDDEN_ANYWHERE + (name, task.id))
            hits = [token for token in forbidden if token in text]
            assert not hits, f"{task.id}: {script.name} leaks {hits}"


def test_no_starter_in_any_category_carries_harness_meta_or_a_hint():
    """The whole shipped starter set, not just `fix_*`.

    `starter/` is copied verbatim into the agent's scratch project, so every
    byte of every starter is prompt — including the provenance header. The
    `fix_*` starters already carried the one-line form; `mts_*`, `asm_*` and
    `opt_*` carried a six-to-nine-line block explaining the harness to a
    maintainer who reads the reference side anyway.
    """
    seen = 0
    for task in bench_tasks.load_tasks():
        if task.starter_dir is None:
            continue
        for script in sorted((task.starter_dir / "parts").glob("*.py")):
            seen += 1
            text = script.read_text(encoding="utf-8")
            hits = [token for token in STARTER_FORBIDDEN_ANYWHERE
                    if token in text]
            assert not hits, f"{task.id}: {script.name} leaks {hits}"
    assert seen >= 20, seen


def test_every_derived_starter_keeps_its_one_line_provenance_header():
    """Trimmed, not deleted: a reader of a scratch project still has to be able
    to tell a copied example from something the agent wrote."""
    for task in bench_tasks.load_tasks():
        if task.starter_dir is None:
            continue
        for script in sorted((task.starter_dir / "parts").glob("*.py")):
            first = script.read_text(encoding="utf-8").splitlines()[0]
            if not first.startswith("#"):
                continue          # a hand-authored broken part (`fix_001`)
            assert first.startswith("# Copied from examples/"), \
                f"{task.id}: {script.name}: {first}"
            assert first.endswith(" into this project."), \
                f"{task.id}: {script.name}: {first}"


def test_no_starter_script_is_byte_identical_to_its_reference():
    # The narrow, load-bearing half of `..._differs_from_its_reference`: a
    # starter copied wholesale from the reference carries the reference's
    # docstring, which is written for a reviewer and reads as an answer key.
    for category in (FIX, ASM):
        for task in _tasks(category):
            for part in task.target_parts:
                starter = (task.starter_dir / "parts" / f"{part}.py")
                reference = (task.reference_project / "parts" / f"{part}.py")
                if category == ASM:
                    # An `asm` task changes no script at all, so its two sides
                    # are meant to match; what must not match is a REFERENCE
                    # docstring, and these carry the provenance header instead.
                    assert "Reference solution" not in \
                        starter.read_text(encoding="utf-8"), task.id
                    continue
                assert starter.read_text(encoding="utf-8") != \
                    reference.read_text(encoding="utf-8"), \
                    f"{task.id}: {part}.py is the reference verbatim"
