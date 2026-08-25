"""The bench task format and its loader (PRD-024 design §1–§2).

A task is a **directory of things this repo already reads**: an AgentCAD
project (`starter/`, `reference/project/`), a PRD-003 `SPECS` block
(`specs/parts/<id>.py`), an exported STEP (`reference/steps/<id>.step`) and one
schema-versioned rubric (`task.json`). Nothing here builds geometry, opens a
service or starts a kernel — the module is OCP-free by contract and pure by
design, because the CI self-test that asserts *every shipped task has zero
problems* must be able to ask the question without constructing anything.

:func:`task_problems` is that question. It is a **pure function returning human
sentences** in the style of `manifest_merge.config_problems`: it never raises,
so a malformed bundle is a list of defects rather than a traceback, and the
list is what :func:`load_task` carries in its ``ValidationError.details``.
"""
from __future__ import annotations

import fnmatch
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .._resources import resource_root
from ..agent.chat import MAX_TOOL_CALLS_PER_TURN
from ..core.checks import _within
from ..core.model import ValidationError
from ..core.specs import declares_specs
from ._json import read_json

#: `task.json`'s schema. Bumped when a *field* changes; the scorer's own
#: version is `bench.HARNESS_VERSION` and the two move independently.
TASK_SCHEMA = 1

#: `reference/metrics.json`'s schema.
METRICS_SCHEMA = 1

#: How many alternative orientations a task may *permit* (design §5.3). Each
#: one is a whole extra IoU boolean, so the ceiling is a cost bound as much as
#: a taste one: a task that accepts nine orientations is not measuring a frame.
MAX_ROTATIONS = 8

#: The five PRD-024 `bench-v1` categories. Each ships exactly five bundles and
#: the loader/report treat them like any other; the constant exists so a reader
#: (and the count-guard test) can name "the original set" apart from
#: :data:`GENERATION_CATEGORY`, which is a different kind of task entirely.
V1_CATEGORIES = ("model_from_drawing", "modify_to_spec", "fix_the_broken_part",
                 "assemble_and_clear", "optimize_under_constraints")

#: PRD-018's category: a part is **generated from a prompt** by the multi-turn
#: generation loop (`agent/generate.run_generation`), not modelled by the
#: single-turn bench runner. The bundle format is identical — a prompt, a frozen
#: rubric SPECS, a reference STEP, metric windows — and the six subscores are
#: measured against the rubric exactly as for every other category; only the way
#: the candidate is *produced* differs (loop vs one-shot), which is what AC8's
#: loop-vs-one-shot delta measures. Kept apart from the `bench-v1` set because it
#: rides its own task_set and grows independently of the 25-task self-test.
GENERATION_CATEGORY = "generate_from_prompt"

CATEGORIES = V1_CATEGORIES + (GENERATION_CATEGORY,)

#: The six subscores, in report order. `weights` has exactly these keys.
SUBSCORES = ("built", "valid", "specs", "geometry", "interference", "metrics")

ALIGN_MODES = ("world", "com", "bbox_center")

#: The closed list of measurable metrics — every one of them computed from
#: `worker._metrics` (`kernel/worker.py:364-415`) with **no new kernel call**.
#: The `bbox_*` and `com_*` keys are derived from `metrics["bbox"]` and
#: `metrics["center_of_mass"]` on the reading side.
METRIC_KEYS = ("volume_mm3", "area_mm2", "mass_g", "n_solids", "n_faces",
               "n_edges", "bbox_x_mm", "bbox_y_mm", "bbox_z_mm",
               "com_x_mm", "com_y_mm", "com_z_mm")

#: What an asset may be. The prompt attaches assets as **text** (design §8.4),
#: so the set is exactly the text-attachable one — a PNG would arrive as
#: mojibake, which is worse than not shipping it.
ASSET_SUFFIXES = (".svg", ".md", ".txt", ".json", ".csv")

#: STEP only. The reference has to be boolean-capable (FR5) and an imported
#: mesh segfaults OCCT under a boolean, so `.stl` is refused by the loader
#: rather than discovered by the kernel. Mirrors `refload._BREP_EXTS`.
STEP_SUFFIXES = (".step", ".stp")

#: The `<id>` half of a task id. `<category>/<id>` is the id used everywhere.
TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")

#: What a task may *not* ask for: the core suite stays green without the
#: `[fem]` extra (FR3), so the one toolkit constructor that needs gmsh/skfem is
#: refused **by name**, in the loader, before anything spawns.
FORBIDDEN_SPEC_CALL = "check_fem_static"

#: A reviewer-facing HTML comment in a `prompt.md` — see
#: :func:`strip_reviewer_comments`.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

#: Three or more consecutive newlines, i.e. the hole a removed comment leaves.
_BLANK_RUN_RE = re.compile(r"\n{3,}")

#: `SPECS +=` in a rubric block. `specs.declares_specs` accepts it -- it is a
#: legitimate binding for a part script -- but it is exactly wrong here: the
#: block is appended to the CANDIDATE's script, so `+=` **extends** whatever
#: the candidate declared for itself instead of replacing it, and an agent can
#: then inflate the `specs` subscore with trivially-true checks of its own.
#: Line-anchored for `_SPECS_TEXT_RE`'s reasons: a comment or a string literal
#: mentioning it is not a binding.
SPECS_AUGMENTED_RE = re.compile(r"^[ \t]*SPECS[ \t]*\+=", re.MULTILINE)


@dataclass(frozen=True)
class Frame:
    """The orientation contract a task states out loud.

    ``datum`` is prose and is reproduced in the prompt: a geometry score
    against an unstated frame is a coin flip, so the task declares the frame
    and the prompt tells the agent what it is.
    """

    align: str
    rotations_deg: tuple
    datum: str


@dataclass(frozen=True)
class Budgets:
    """Wall clock, tool calls, and the derived API-turn cap.

    ``api_turns`` is **derived, never declared** (design §8.3): every API turn
    may issue at least one tool call, plus slack for text-only turns.
    """

    wall_s: float
    turns: int
    api_turns: int


@dataclass(frozen=True)
class MetricWindow:
    """One inclusive band on one metric of one part."""

    name: str
    part: str
    metric: str
    min: float | None
    max: float | None


@dataclass(frozen=True)
class Task:
    """A fully-resolved task: every path absolute, every field validated."""

    id: str
    schema: int
    task_set: str
    version: int
    category: str
    title: str
    sets: tuple
    authored_against: str
    source: dict
    root: Path
    prompt_path: Path
    asset_paths: tuple
    starter_dir: Path | None
    target_project: str
    target_parts: tuple
    budgets: Budgets
    frame: Frame
    reference_project: Path
    reference_steps: dict
    metrics_path: Path | None
    specs_project_path: Path | None
    specs_part_paths: dict
    weights: dict


def tasks_root() -> Path:
    """``benchmarks/tasks`` — resolved like `examples/` and `catalog/`."""
    return resource_root() / "benchmarks" / "tasks"


# --------------------------------------------------------------- validation

def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _inside(base: Path, rel) -> Path | None:
    """``base/rel`` resolved, or None when it escapes the task directory.

    Resolve-then-recheck (`checks._within`) rather than a textual `..` scan: a
    relative path is authored data, and the only honest question is where it
    actually lands.
    """
    if not isinstance(rel, str) or not rel.strip():
        return None
    try:
        target = (Path(base) / rel).resolve()
    except (OSError, ValueError):
        return None
    try:
        outer = Path(base).resolve()
    except (OSError, ValueError):       # pragma: no cover — defensive
        return None
    return target if _within(target, outer) else None


def _resolved(base: Path, rel, label: str, out: list) -> Path | None:
    """`_inside`, appending the human sentence when it escapes or is absent."""
    target = _inside(base, rel)
    if target is None:
        out.append(f"{label} {rel!r} does not resolve to a path inside the "
                   f"task directory; a path that lands outside the task "
                   f"directory is refused")
        return None
    return target


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def task_problems(raw, base) -> list[str]:
    """Every defect of the bundle at *base*, as human sentences.

    Cheapest first, and **never raises** — an unreadable file, a wrong type
    and a `..`-escape are all sentences, because a validator that throws on
    malformed input cannot be used to *report* on malformed input.
    """
    out: list[str] = []
    if not isinstance(raw, dict):
        return ["task.json must hold a JSON object"]
    base = Path(base)

    # 1. identity ---------------------------------------------------------
    if raw.get("schema") != TASK_SCHEMA:
        out.append(f"schema must be {TASK_SCHEMA}, got {raw.get('schema')!r}")
    category, name = raw.get("category"), base.name
    if category not in CATEGORIES:
        out.append(f"category must be one of {', '.join(CATEGORIES)}, "
                   f"got {category!r}")
    elif raw.get("id") != f"{category}/{name}":
        out.append(f"id must be '{category}/{name}', got {raw.get('id')!r}")
    if not TASK_ID_RE.match(name):
        out.append(f"the task directory name {name!r} must match "
                   f"{TASK_ID_RE.pattern}")
    if not (_is_int(raw.get("version")) and raw["version"] > 0):
        out.append(f"version must be a positive int, got "
                   f"{raw.get('version')!r}")
    if not _nonempty_str(raw.get("task_set")):
        out.append("task_set must be a non-empty string")
    if not _nonempty_str(raw.get("title")):
        out.append("title must be a non-empty string")
    if not _nonempty_str(raw.get("authored_against")):
        out.append("authored_against must be a non-empty version string")
    sets = raw.get("sets")
    if not isinstance(sets, list) or not sets or \
            not all(_nonempty_str(item) for item in sets):
        out.append("sets must be a non-empty list of non-empty strings")
    source = raw.get("source")
    if not isinstance(source, dict) or source.get("kind") not in (
            "authored", "derived"):
        out.append("source.kind must be 'authored' or 'derived'")

    # 2. weights ----------------------------------------------------------
    weights = raw.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(SUBSCORES):
        out.append(f"weights must have exactly the keys "
                   f"{', '.join(SUBSCORES)}")
        weights = {}
    else:
        bad = [key for key, value in weights.items()
               if not _is_number(value) or value < 0]
        if bad:
            out.append(f"every weight must be a finite number >= 0: "
                       f"{sorted(bad)}")
            weights = {}
        elif abs(sum(weights.values()) - 1.0) > 1e-9:
            out.append(f"weights must sum to 1.0, they sum to "
                       f"{sum(weights.values())}")

    # 3. budgets ----------------------------------------------------------
    budgets = raw.get("budgets")
    if not isinstance(budgets, dict):
        out.append("budgets must be an object with wall_s and turns")
        budgets = {}
    wall_s = budgets.get("wall_s")
    if not _is_number(wall_s) or wall_s <= 0:
        out.append(f"budgets.wall_s must be a finite number > 0, got "
                   f"{wall_s!r}")
    turns = budgets.get("turns")
    if not _is_int(turns) or not 1 <= turns <= MAX_TOOL_CALLS_PER_TURN:
        out.append(f"budgets.turns must be an int in "
                   f"1..{MAX_TOOL_CALLS_PER_TURN} (MAX_TOOL_CALLS_PER_TURN); "
                   f"a task may not declare more tool calls than one chat "
                   f"turn can issue, got {turns!r}")

    # 4. frame ------------------------------------------------------------
    frame = raw.get("frame")
    if not isinstance(frame, dict):
        out.append("frame must be an object with align, rotations_deg "
                   "and datum")
        frame = {}
    if frame.get("align") not in ALIGN_MODES:
        out.append(f"frame.align must be one of {', '.join(ALIGN_MODES)}, "
                   f"got {frame.get('align')!r}")
    rotations = frame.get("rotations_deg")
    if not isinstance(rotations, list):
        out.append("frame.rotations_deg must be a list of intrinsic-XYZ "
                   "degree triples")
    elif len(rotations) > MAX_ROTATIONS:
        out.append(f"frame.rotations_deg permits at most {MAX_ROTATIONS} "
                   f"alternative orientations, got {len(rotations)}; each one "
                   f"costs a whole extra IoU boolean")
    else:
        for index, triple in enumerate(rotations):
            if not isinstance(triple, list) or len(triple) != 3 or \
                    not all(_is_number(item) for item in triple):
                out.append(f"frame.rotations_deg[{index}] must be three "
                           f"finite numbers (intrinsic XYZ Euler degrees)")
    if not _nonempty_str(frame.get("datum")):
        out.append("frame.datum must be a non-empty sentence: an unstated "
                   "frame makes the geometry subscore a coin flip")

    # 5. target -----------------------------------------------------------
    target = raw.get("target")
    if not isinstance(target, dict):
        out.append("target must be an object with project and parts")
        target = {}
    if not _nonempty_str(target.get("project")):
        out.append("target.project must be the scratch project's name")
    parts = target.get("parts")
    if not isinstance(parts, list) or not parts or \
            not all(_nonempty_str(item) for item in parts):
        out.append("target.parts must be a non-empty list of part ids")
        parts = []

    # 6. prompt and assets ------------------------------------------------
    prompt = _resolved(base, raw.get("prompt"), "prompt", out)
    if prompt is not None:
        text = _read_text(prompt) if prompt.is_file() else None
        if text is None:
            out.append(f"prompt {raw.get('prompt')!r} must be a readable "
                       f"UTF-8 file")
        elif not text.strip():
            out.append(f"prompt {raw.get('prompt')!r} is empty")
    assets = raw.get("assets") or []
    if not isinstance(assets, list):
        out.append("assets must be a list of relative paths")
        assets = []
    for asset in assets:
        path = _resolved(base, asset, "asset", out)
        if path is None:
            continue
        if path.suffix.lower() not in ASSET_SUFFIXES:
            out.append(f"asset {asset!r} must be one of "
                       f"{', '.join(ASSET_SUFFIXES)} — assets are attached to "
                       f"the prompt as text")
        elif not path.is_file():
            out.append(f"asset {asset!r} does not exist")

    # 7. starter and reference projects -----------------------------------
    starter = raw.get("starter")
    if starter is not None:
        path = _resolved(base, starter, "starter", out)
        if path is not None and not (path / "project.json").is_file():
            out.append(f"starter {starter!r} must be a project directory "
                       f"holding project.json")
    reference = raw.get("reference")
    if not isinstance(reference, dict):
        out.append("reference must be an object with project, steps "
                   "and metrics")
        reference = {}
    ref_project = _resolved(base, reference.get("project"),
                            "reference.project", out)
    if ref_project is not None and not (ref_project / "project.json").is_file():
        out.append(f"reference.project {reference.get('project')!r} must be a "
                   f"project directory holding project.json")

    # 8. reference STEPs --------------------------------------------------
    steps = reference.get("steps")
    if not isinstance(steps, dict):
        out.append("reference.steps must be an object of part id -> STEP path")
        steps = {}
    if weights.get("geometry", 0.0):
        missing = [part for part in parts if part not in steps]
        if missing:
            out.append(f"reference.steps must name a STEP for every scored "
                       f"part while the geometry weight is above zero; "
                       f"missing {sorted(missing)}")
    for part_id, rel in sorted(steps.items()):
        if parts and part_id not in parts:
            out.append(f"reference.steps names {part_id!r}, which is not in "
                       f"target.parts")
        path = _resolved(base, rel, "reference.steps entry", out)
        if path is None:
            continue
        if path.suffix.lower() not in STEP_SUFFIXES:
            out.append(f"reference.steps[{part_id!r}] must be a STEP "
                       f"(.step/.stp), never a mesh: the datum has to be "
                       f"boolean-capable and a mesh side segfaults OCCT")
        elif not path.is_file():
            out.append(f"reference.steps[{part_id!r}] {rel!r} does not exist")

    # 9. metric windows ---------------------------------------------------
    metrics_rel = reference.get("metrics")
    metrics_path, window_count = None, 0
    if metrics_rel is not None:
        metrics_path = _resolved(base, metrics_rel, "reference.metrics", out)
    if metrics_path is not None:
        problems, window_count = _metrics_problems(metrics_path, metrics_rel,
                                                   parts)
        out.extend(problems)
    if weights.get("metrics", 0.0):
        # Design §2 rule 8: the weight and the windows are two halves of one
        # claim. A document that parses but declares nothing is the same defect
        # as no document at all -- a scored subscore with nothing to measure --
        # and it is the shape an author reaches by deleting a window rather
        # than by forgetting a file, so it has to be refused by count and not
        # only by presence.
        if metrics_path is None:
            out.append("the metrics weight is above zero but reference.metrics "
                       "names no window document")
        elif window_count == 0:
            out.append(f"the metrics weight is above zero but "
                       f"reference.metrics {metrics_rel!r} declares zero "
                       f"windows; a scored subscore must have something to "
                       f"measure")

    # 10. the specs rubric ------------------------------------------------
    specs = raw.get("specs")
    if specs is None:
        specs = {}
    if not isinstance(specs, dict):
        out.append("specs must be an object with project and parts")
        specs = {}
    specs_parts = specs.get("parts") or {}
    if not isinstance(specs_parts, dict):
        out.append("specs.parts must be an object of part id -> rubric path")
        specs_parts = {}
    rubric_paths: list[tuple[str, Path]] = []
    if specs.get("project") is not None:
        path = _resolved(base, specs.get("project"), "specs.project", out)
        if path is not None:
            rubric_paths.append((str(specs.get("project")), path))
    for part_id, rel in sorted(specs_parts.items()):
        if parts and part_id not in parts:
            out.append(f"specs.parts names {part_id!r}, which is not in "
                       f"target.parts")
        path = _resolved(base, rel, "specs.parts entry", out)
        if path is not None:
            rubric_paths.append((str(rel), path))
    for rel, path in rubric_paths:
        text = _read_text(path) if path.is_file() else None
        if text is None:
            out.append(f"specs block {rel!r} must be a readable UTF-8 file")
            continue
        if not declares_specs(text):
            out.append(f"specs block {rel!r} must bind SPECS at module level "
                       f"(re-bind, never '+='): the block is appended to the "
                       f"candidate's script and the last binding wins")
        elif SPECS_AUGMENTED_RE.search(text):
            out.append(f"specs block {rel!r} uses 'SPECS +=', which extends "
                       f"the candidate's own SPECS instead of replacing it: "
                       f"the block must RE-BIND SPECS, or an agent can inflate "
                       f"the specs subscore with checks it wrote itself")
        if FORBIDDEN_SPEC_CALL in text:
            out.append(f"specs block {rel!r} uses {FORBIDDEN_SPEC_CALL}, which "
                       f"is not allowed: a bench task must score without the "
                       f"[fem] extra installed")
    if weights.get("specs", 0.0) and not rubric_paths:
        out.append("the specs weight is above zero but the task declares "
                   "neither specs.project nor a specs.parts entry")
    return out


def _metrics_problems(path: Path, rel, parts) -> tuple[list[str], int]:
    """Defects of one `reference/metrics.json`, and how many windows it holds.

    Never raises. The count comes back with the problems because the *caller*
    is the only place that knows whether zero windows is a defect: it is,
    exactly when the task also weights the `metrics` subscore above zero.
    """
    out: list[str] = []
    try:
        doc = read_json(path)
    except ValidationError as exc:
        return [f"reference.metrics {rel!r}: {exc.message}"], 0
    if doc.get("schema") != METRICS_SCHEMA:
        out.append(f"reference.metrics schema must be {METRICS_SCHEMA}, got "
                   f"{doc.get('schema')!r}")
    windows = doc.get("windows")
    if not isinstance(windows, list):
        return out + ["reference.metrics windows must be a list"], 0
    seen: set = set()
    for index, window in enumerate(windows):
        where = f"reference.metrics windows[{index}]"
        if not isinstance(window, dict):
            out.append(f"{where} must be an object")
            continue
        name = window.get("name")
        if not _nonempty_str(name):
            out.append(f"{where}.name must be a non-empty string")
        elif name in seen:
            out.append(f"{where}.name {name!r} is declared twice")
        else:
            seen.add(name)
        if parts and window.get("part") not in parts:
            out.append(f"{where}.part {window.get('part')!r} is not in "
                       f"target.parts")
        if window.get("metric") not in METRIC_KEYS:
            out.append(f"{where}.metric must be one of "
                       f"{', '.join(METRIC_KEYS)}, got "
                       f"{window.get('metric')!r}")
        low, high = window.get("min"), window.get("max")
        if low is None and high is None:
            out.append(f"{where} must declare at least one of min or max")
            continue
        for label, bound in (("min", low), ("max", high)):
            if bound is not None and not _is_number(bound):
                out.append(f"{where}.{label} must be a finite number, got "
                           f"{bound!r}; a NaN bound is not a loose bound, it "
                           f"is no bound")
        if _is_number(low) and _is_number(high) and low > high:
            out.append(f"{where} has min {low} above max {high}")
    return out, len(windows)


# ------------------------------------------------------------------ loading

def _task_dir(task_id: str, root: Path | None) -> Path:
    base = Path(root) if root is not None else tasks_root()
    return base.joinpath(*task_id.split("/"))


def load_task(task_id: str, root: Path | None = None) -> Task:
    """The fully-resolved task, or `ValidationError` carrying every problem.

    The problem list lives in ``details["problems"]`` so the CLI can print all
    of them at once: an author fixing one defect per run is a bad afternoon.
    """
    if not isinstance(task_id, str) or task_id.count("/") != 1:
        raise ValidationError(
            f"a task id is '<category>/<id>', got {task_id!r}",
            {"problems": [f"malformed task id {task_id!r}"]})
    category, name = task_id.split("/")
    if category not in CATEGORIES or not TASK_ID_RE.match(name):
        raise ValidationError(
            f"no such bench task: {task_id}",
            {"problems": [f"{task_id} is not a well-formed task id"]})
    base = _task_dir(task_id, root)
    manifest = base / "task.json"
    if not manifest.is_file():
        raise ValidationError(
            f"no such bench task: {task_id} (expected {manifest})",
            {"problems": [f"{manifest} does not exist"], "task": task_id})
    try:
        raw = read_json(manifest)
    except ValidationError as exc:
        raise ValidationError(f"{task_id}: {exc.message}",
                              {"problems": [exc.message],
                               "task": task_id}) from exc
    problems = task_problems(raw, base)
    if problems:
        raise ValidationError(
            f"{task_id} is not a valid bench task: {problems[0]}",
            {"problems": problems, "task": task_id})
    return _build_task(raw, base)


def _build_task(raw: dict, base: Path) -> Task:
    """Turn a *validated* document into a `Task`. Assumes zero problems."""
    reference = raw["reference"]
    specs = raw.get("specs") or {}
    specs_parts = specs.get("parts") or {}
    turns = int(raw["budgets"]["turns"])
    metrics_rel = reference.get("metrics")
    return Task(
        id=raw["id"],
        schema=int(raw["schema"]),
        task_set=raw["task_set"],
        version=int(raw["version"]),
        category=raw["category"],
        title=raw["title"],
        sets=tuple(raw["sets"]),
        authored_against=raw["authored_against"],
        source=dict(raw["source"]),
        root=base.resolve(),
        prompt_path=(base / raw["prompt"]).resolve(),
        asset_paths=tuple((base / item).resolve()
                          for item in (raw.get("assets") or [])),
        starter_dir=((base / raw["starter"]).resolve()
                     if raw.get("starter") else None),
        target_project=raw["target"]["project"],
        target_parts=tuple(raw["target"]["parts"]),
        budgets=Budgets(wall_s=float(raw["budgets"]["wall_s"]), turns=turns,
                        api_turns=turns + 4),
        frame=Frame(align=raw["frame"]["align"],
                    rotations_deg=tuple(tuple(float(item) for item in triple)
                                        for triple in
                                        raw["frame"]["rotations_deg"]),
                    datum=raw["frame"]["datum"]),
        reference_project=(base / reference["project"]).resolve(),
        reference_steps={part: (base / rel).resolve()
                         for part, rel in reference.get("steps", {}).items()},
        metrics_path=((base / metrics_rel).resolve()
                      if metrics_rel is not None else None),
        specs_project_path=((base / specs["project"]).resolve()
                            if specs.get("project") else None),
        specs_part_paths={part: (base / rel).resolve()
                          for part, rel in specs_parts.items()},
        weights=dict(raw["weights"]),
    )


def load_tasks(root: Path | None = None, *, glob: str | None = None,
               set_name: str | None = None) -> list[Task]:
    """Every task under *root*, sorted by id, optionally filtered.

    *glob* matches the **task id** (`fnmatch`, so `model_from_drawing/*` and
    `*/mfd_00*` both work); *set_name* matches membership in `sets`.
    """
    base = Path(root) if root is not None else tasks_root()
    out: list[Task] = []
    for manifest in sorted(base.glob("*/*/task.json")):
        task_id = f"{manifest.parent.parent.name}/{manifest.parent.name}"
        if glob is not None and not fnmatch.fnmatch(task_id, glob):
            continue
        task = load_task(task_id, root=base)
        if set_name is not None and set_name not in task.sets:
            continue
        out.append(task)
    return sorted(out, key=lambda task: task.id)


def load_windows(path) -> list[MetricWindow]:
    """The metric windows at *path*, sorted by name.

    Reads a document `task_problems` has already accepted; a bound that is
    absent stays `None`, because "no ceiling" and "an infinite ceiling" are
    different claims and only the first one is honest.
    """
    doc = read_json(path)
    windows = doc.get("windows") or []
    out = [MetricWindow(name=window["name"], part=window["part"],
                        metric=window["metric"],
                        min=(float(window["min"])
                             if window.get("min") is not None else None),
                        max=(float(window["max"])
                             if window.get("max") is not None else None))
           for window in windows]
    return sorted(out, key=lambda window: window.name)


def strip_reviewer_comments(text: str) -> str:
    """*text* with every HTML comment removed and the blank it leaves closed.

    Design §7.6 asks a task that overrides its category weights to argue the
    override "in a comment at the top of its `prompt.md`", and `mts_005` and
    `fix_005` do. That argument is written for a REVIEWER: it says which
    subscore carries which weight and why, and handing an agent "geometry is
    not scored on this task" changes what the agent spends its budget on —
    the prompt would be telling it how it is marked. So the comment stays in
    the file, where a reviewer reads it in the diff, and never reaches the
    model.

    Non-greedy and DOTALL: the comments are multi-line and a prompt may carry
    more than one, and a greedy match would swallow everything between the
    first `<!--` and the last `-->`.
    """
    return _BLANK_RUN_RE.sub("\n\n", _HTML_COMMENT_RE.sub("", text)).strip()


def prompt_text(task: Task) -> str:
    """The prompt handed to the agent: `prompt.md`, then every asset inline.

    Assets are attached as **text**, fenced and named by their path relative to
    the task directory — the model reads an SVG drawing as markup, which is
    the whole reason the asset set is text-only.

    Reviewer-facing HTML comments are stripped from the prompt body
    (:func:`strip_reviewer_comments`); assets are attached verbatim, because an
    SVG's own comments are part of the drawing.
    """
    parts = [strip_reviewer_comments(
        task.prompt_path.read_text(encoding="utf-8"))]
    for asset in task.asset_paths:
        rel = asset.relative_to(task.root).as_posix()
        body = asset.read_text(encoding="utf-8").strip()
        parts.append(f"--- attachment: {rel} ---\n```\n{body}\n```")
    return "\n\n".join(parts) + "\n"
