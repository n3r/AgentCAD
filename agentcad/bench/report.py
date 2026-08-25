"""`bench report` -- aggregate a results directory, gate it against a baseline.

Pure over a directory of `score.json` / `run.json` documents (design §8.7): no
service, no kernel, no network, no clock. Everything non-deterministic already
lives in `run.json`; this module reads exactly one field out of it
(`over_budget`) and puts nothing of its own into the report body, so
`report.json` is byte-identical across machines that measured the same thing
(FR6/AC3).

Four rules carry the whole design:

* **A category total is the unweighted mean of its tasks' totals; the overall
  total is the unweighted mean of the *category* totals.** At five tasks per
  category the two coincide -- stating it this way means a v2 that adds tasks
  to one category cannot silently reweight the headline number.
* **A missing task scores `0.0`** and is flagged `missing: true`; against a
  baseline it is a regression twice over -- it depresses its category mean, and
  it is named as a `coverage` regression in its own right. Without that a
  release could be gated green by not running the hard half.
* **The roster is read, never assumed.** `expected` names the ids the report
  must cover; in its absence `aggregate` takes the roster from a
  `tasks_root`, then from `bench.json`'s per-task index (`_index_ids`), and
  only then from the ids that happen to be on disk.
* **The gate is `total`, each category and coverage, never a single task.** One task
  under a stochastic agent is noise, and gating on noise makes the release gate
  a coin flip. Per-task deltas are computed and printed anyway -- this is the
  one place the design deliberately measures more than it enforces
  (design §11).

**The `bench.json` contract this module depends on** (design §8.7, "run header
+ per-task index"): the key is **`tasks`**, and it is canonically an **object
keyed by task id** -- the per-task index, whose values this module does not
read. A plain **list of task ids** is accepted as the equivalent shorthand.
Whatever the runner writes there is the roster the report is measured against,
so a task the runner selected and never scored shows up as `missing: true`
instead of quietly leaving the denominator.
"""
from __future__ import annotations

import math
from pathlib import Path

from ..core.model import ValidationError
from ._json import is_finite_number as _finite
from ._json import read_json, round_floats
from .tasks import load_tasks

#: The report document's own version.
REPORT_SCHEMA = 1
#: `benchmarks/baseline.json`'s version.
BASELINE_SCHEMA = 1
#: `bench.json`'s version (the run header this module reads).
HEADER_SCHEMA = 1
#: The `score.json` version this module knows how to aggregate.
SCORE_SCHEMA = 1

#: Per-task rows rendered into markdown before the `+N more` line. The cap is
#: `checks.MAX_RENDERED_FAILURES`' reasoning at bench scale: a GitHub step
#: summary is capped at 1 MiB, and a truncated table that says it was truncated
#: beats one GitHub silently drops.
MAX_RENDERED_TASKS = 25


# ------------------------------------------------------------- aggregation

def _score_paths(results_dir: Path) -> dict:
    """`{task_id: path}` for every `<results>/tasks/<category>/<id>/score.json`.

    The id comes from the **directory**, not from the document: it is the key
    `expected` is stated in, and it is the one thing a truncated or mislabelled
    score cannot lie about. A document whose `task` disagrees earns a warning
    below rather than a second identity.
    """
    base = Path(results_dir) / "tasks"
    if not base.is_dir():
        return {}
    return {f"{path.parent.parent.name}/{path.parent.name}": path
            for path in sorted(base.glob("*/*/score.json"))}


def _header_doc(results_dir: Path) -> dict:
    """`bench.json` if there is one, `{}` if there is not.

    A hand-assembled results directory (one `bench score` output, a downloaded
    submission) has no `bench.json`, and refusing it would make the reporter
    unusable for exactly the audit FR12 exists to invite. A `bench.json` that
    *is* present and unreadable is a harness error -- the caller asked for a
    document we could not read.
    """
    path = Path(results_dir) / "bench.json"
    if not path.is_file():
        return {}
    head = read_json(path)
    if head.get("schema") != HEADER_SCHEMA:
        raise ValidationError(
            f"{path} declares schema {head.get('schema')!r}, "
            f"this harness reads {HEADER_SCHEMA}",
            {"path": str(path)})
    return head


def _index_ids(head: dict) -> list | None:
    """The roster `bench.json`'s per-task index declares, or `None`.

    The key is `tasks` (module docstring): canonically an object keyed by task
    id, with a list of ids accepted as the shorthand. Anything else is ignored
    rather than guessed at -- a malformed index must not silently shrink the
    denominator, which is the whole failure this roster exists to prevent.
    """
    index = head.get("tasks")
    if isinstance(index, dict):
        return sorted(str(key) for key in index)
    if isinstance(index, list):
        return sorted(str(item) for item in index if isinstance(item, str))
    return None


def _header(head: dict, scores: dict) -> dict:
    """The report's identity fields: the header's, else the scores' own."""
    first = next(iter(scores.values()), {})
    return {
        "task_set": head.get("task_set", first.get("task_set")),
        "harness": head.get("harness", first.get("harness")),
        "agentcad": head.get("agentcad", first.get("agentcad")),
        "agent": head.get("agent"),
        "model": head.get("model"),
    }


def _read_scores(paths: dict) -> dict:
    """Read every `score.json`, refusing an unreadable or foreign-schema one.

    This is the `bench report` half of exit code 2: "unreadable results, schema
    mismatch" is the harness failing to measure, and it must not be answered
    with a number.
    """
    out = {}
    for task_id, path in paths.items():
        score = read_json(path)
        if score.get("schema") != SCORE_SCHEMA:
            raise ValidationError(
                f"{path} declares schema {score.get('schema')!r}, "
                f"this harness reads {SCORE_SCHEMA}",
                {"path": str(path), "task": task_id})
        total = score.get("total")
        if not _finite(total):
            raise ValidationError(
                f"{path} has no finite numeric total (got {total!r})",
                {"path": str(path), "task": task_id})
        for name, entry in (score.get("subscores") or {}).items():
            if isinstance(entry, dict) and "value" in entry \
                    and not _finite(entry["value"]):
                raise ValidationError(
                    f"{path}: subscore {name!r} has a non-finite value "
                    f"({entry['value']!r}); a non-finite measurement is a "
                    f"status: \"error\" subscore, never a number",
                    {"path": str(path), "task": task_id, "subscore": name})
        out[task_id] = score
    return out


def _over_budget(path: Path, task_id: str, warnings: list) -> bool:
    """`run.json`'s `over_budget`, or `False` with a warning when it will not read.

    An absent `run.json` is not a defect: `bench score` on a submission someone
    else produced writes a score and no run. A *present* one that will not parse
    is worth a sentence, but not the run's verdict -- it holds provenance, not
    measurement.
    """
    run = path.parent / "run.json"
    if not run.is_file():
        return False
    try:
        doc = read_json(run)
    except ValidationError as exc:
        warnings.append(f"{task_id}: run.json is unreadable ({exc.message}); "
                        f"over_budget reported as false")
        return False
    return bool(doc.get("over_budget"))


def _generation(path: Path, task_id: str, warnings: list) -> dict | None:
    """`generation.json`'s AC8 delta beside a score, or ``None`` when absent.

    A `generate_from_prompt` task writes a `generation.json` (loop vs one-shot)
    beside its `score.json`; every other category writes none. A *present* one
    that will not parse is a sentence, not the report's verdict — it holds the
    loop-vs-one-shot comparison, not the task's own measurement (which is the
    loop's `score.json`, read the same way as any other).
    """
    doc_path = path.parent / "generation.json"
    if not doc_path.is_file():
        return None
    try:
        doc = read_json(doc_path)
    except ValidationError as exc:
        warnings.append(f"{task_id}: generation.json is unreadable "
                        f"({exc.message}); the loop-vs-one-shot delta is omitted")
        return None
    delta = doc.get("delta")
    if not _finite(doc.get("loop_total")) or not _finite(doc.get("oneshot_total")) \
            or not _finite(delta):
        warnings.append(f"{task_id}: generation.json has no finite totals; the "
                        f"loop-vs-one-shot delta is omitted")
        return None
    subscores = {}
    for name, entry in (doc.get("subscores") or {}).items():
        if isinstance(entry, dict):
            subscores[name] = {"loop": entry.get("loop"),
                               "oneshot": entry.get("oneshot"),
                               "delta": entry.get("delta")}
    return {"loop_total": float(doc["loop_total"]),
            "oneshot_total": float(doc["oneshot_total"]),
            "delta": float(delta), "subscores": subscores}


def _comparability(task_id: str, score: dict, header: dict) -> list:
    """Sentences naming every way *score* is not comparable with the header.

    Design §6: two scores are comparable iff `(task_set, task_version, harness)`
    agree. `task_version` is a property of the task and the report header
    carries no single value for it, so the pair the header *does* declare is
    what is compared -- and the score's `task_version` rides along in the
    sentence so a human reading the warning can see it.
    """
    out = []
    for field in ("task_set", "harness"):
        want, got = header.get(field), score.get(field)
        if want is not None and got != want:
            out.append(
                f"{task_id}: {field} is {got!r}, the report's is {want!r} "
                f"(task_version {score.get('task_version')!r}); the row is "
                f"included and was not silently averaged away")
    named = score.get("task")
    if isinstance(named, str) and named and named != task_id:
        out.append(f"{task_id}: score.json calls itself {named!r}; "
                   f"the directory name wins")
    category = score.get("category")
    if isinstance(category, str) and category and category != task_id.split("/")[0]:
        out.append(f"{task_id}: score.json's category is {category!r}, the "
                   f"directory says {task_id.split('/')[0]!r}; "
                   f"the directory wins")
    return out


def aggregate(results_dir, *, tasks_root=None, expected: list | None = None) -> dict:
    """The report document of design §10 over a results directory.

    *expected* names the task ids the report must cover -- the honest
    denominator, because a task that was never run must not vanish from it.
    In its absence the roster is taken, in order, from `tasks_root`
    (`[t.id for t in load_tasks(root=tasks_root)]`), then from `bench.json`'s
    per-task index (`tasks`, see the module docstring), then from the ids found
    on disk. That last fallback is a hand-assembled directory: it has no roster
    to be measured against, and inventing one out of the shipped
    `benchmarks/tasks` would turn "score this submission" into "score this
    submission against 25 tasks it never claimed".

    A results directory with no rows at all -- no score and no roster -- raises
    `ValidationError` rather than answering `total: 0.0`. "We measured nothing"
    is the harness lane (exit 2), and a zero is a measurement.
    """
    results_dir = Path(results_dir)
    paths = _score_paths(results_dir)
    scores = _read_scores(paths)
    head = _header_doc(results_dir)
    header = _header(head, scores)

    if expected is None and tasks_root is not None:
        expected = [task.id for task in load_tasks(root=tasks_root)]
    if expected is None:
        expected = _index_ids(head)
    ids = sorted(set(paths) | set(expected or ()))

    warnings: list = []
    tasks: dict = {}
    generation: dict = {}
    for task_id in ids:
        score = scores.get(task_id)
        if score is None:
            tasks[task_id] = {"total": 0.0, "over_budget": False,
                              "missing": True, "subscores": {}}
            continue
        warnings += _comparability(task_id, score, header)
        subscores = {}
        for name, entry in (score.get("subscores") or {}).items():
            if isinstance(entry, dict) and isinstance(entry.get("value"),
                                                      (int, float)):
                subscores[name] = float(entry["value"])
        row = {
            "total": float(score["total"]),
            "over_budget": _over_budget(paths[task_id], task_id, warnings),
            "missing": False,
            "subscores": subscores,
        }
        gen = _generation(paths[task_id], task_id, warnings)
        if gen is not None:
            row["generation"] = gen
            generation[task_id] = gen
        tasks[task_id] = row

    categories: dict = {}
    for task_id, row in tasks.items():
        bucket = categories.setdefault(task_id.split("/")[0],
                                       {"totals": [], "missing": 0})
        bucket["totals"].append(row["total"])
        bucket["missing"] += 1 if row["missing"] else 0
    categories = {name: {"total": sum(bucket["totals"]) / len(bucket["totals"]),
                         "n": len(bucket["totals"]),
                         "missing": bucket["missing"]}
                  for name, bucket in sorted(categories.items())}

    if not tasks:
        raise ValidationError(
            f"{results_dir} holds no score.json and names no expected task; "
            f"there is nothing to report",
            {"path": str(results_dir)})

    # The mean of the *category* means, never the mean of the tasks: adding a
    # sixth task to one category must not reweight the headline number.
    total = (sum(row["total"] for row in categories.values()) / len(categories)
             if categories else None)

    report = {
        "schema": REPORT_SCHEMA,
        "task_set": header["task_set"],
        "harness": header["harness"],
        "agentcad": header["agentcad"],
        "agent": header["agent"],
        "model": header["model"],
        "n": len(tasks),
        "total": total,
        "categories": categories,
        "tasks": dict(sorted(tasks.items())),
        "warnings": sorted(warnings),
    }
    # Additive and guarded: a run with no `generate_from_prompt` task writes no
    # `generation.json`, so the key never appears and every other report is
    # byte-for-byte unchanged (AC3 for the non-generation suite).
    if generation:
        report["generation"] = dict(sorted(generation.items()))
    return round_floats(report)


# ------------------------------------------------------------- the gate

def _delta(measured, baseline) -> float:
    return float(measured) - float(baseline)


def _with_coverage(report: dict, baseline: dict) -> tuple:
    """`(total, {category: total}, uncovered_ids)` with the baseline's tasks in.

    Every baseline task id the report does not carry is folded in as a `0.0`
    row before the means are recomputed, so a half-run suite cannot pass by
    shrinking its own denominator. The recomputation repeats `aggregate`'s
    rule exactly: a category mean over its tasks, the total over the category
    means.
    """
    rows = {task_id: float(row.get("total") or 0.0)
            for task_id, row in (report.get("tasks") or {}).items()}
    uncovered = sorted(set(baseline.get("tasks") or {}) - set(rows))
    rows.update({task_id: 0.0 for task_id in uncovered})

    buckets: dict = {}
    for task_id, total in rows.items():
        buckets.setdefault(task_id.split("/")[0], []).append(total)
    categories = {name: sum(totals) / len(totals)
                  for name, totals in buckets.items()}
    total = (sum(categories.values()) / len(categories)
             if categories else None)
    return total, categories, uncovered


def compare_baseline(report: dict, baseline: dict, epsilon: float,
                     *, path=None) -> dict:
    """Gate *report* against *baseline*: total and each category, nothing else.

    Status is one of `ok` / `regressed` / `incomparable` / `unrecorded`, and
    `report_exit_code` maps it to `0` / `1` / `2` / `0`.

    **A baseline task id the report does not contain is scored `0.0` before
    anything is gated** (design §11: "a baseline naming tasks the results do
    not contain -- those tasks are `missing` and count as full regressions").
    Its category mean and the overall total are recomputed with those zeros in
    them, and the gap is *also* named on its own as a `coverage` regression --
    otherwise the cheapest way to pass the gate would be to run half the suite,
    which is the one failure mode a release gate exists to catch. Note that
    `coverage` gates regardless of `epsilon`: not measuring a task is not a
    small drop in a number, it is the absence of one.

    The order of the tests is deliberate. A baseline of a foreign **schema** is
    incomparable -- we cannot even read what it claims. A baseline whose
    `total` is `null` is *unrecorded*, and unrecorded outranks a
    `(task_set, harness)` mismatch: there is no number, so there is nothing to
    be incomparable about, and the shipped `benchmarks/baseline.json` must keep
    exiting 0 across a harness bump instead of turning every CI run red before
    a single number exists. Once a number *is* recorded, a mismatch in
    `(task_set, harness)` is exit 2 and never a pass -- comparing across
    harness versions is not a comparison (design §11).
    """
    epsilon = float(epsilon)
    if not math.isfinite(epsilon):
        # `NaN < -NaN` is False, so a non-finite tolerance does not widen the
        # gate -- it deletes it, silently and green. The CLI screens this with
        # `_finite_arg`; the gate refuses it too, because a gate that can be
        # switched off by a typo is not a gate.
        raise ValidationError(
            f"--epsilon must be finite, got {epsilon!r}: a non-finite "
            f"tolerance disables the gate instead of widening it",
            {"epsilon": str(epsilon)})
    out = {"path": str(path) if path is not None else None,
           "epsilon": epsilon, "status": "ok",
           "regressions": [], "task_deltas": [], "warnings": []}

    if baseline.get("schema") != BASELINE_SCHEMA:
        out["status"] = "incomparable"
        out["reason"] = (f"baseline schema {baseline.get('schema')!r}, "
                         f"this harness reads {BASELINE_SCHEMA}")
        return out
    if baseline.get("total") is None:
        out["status"] = "unrecorded"
        out["reason"] = ("the baseline records no total yet; the gate is a "
                         "no-op until a run records one")
        return out
    for field in ("task_set", "harness"):
        if baseline.get(field) != report.get(field):
            out["status"] = "incomparable"
            out["reason"] = (
                f"baseline {field} is {baseline.get(field)!r}, the measured "
                f"report's is {report.get(field)!r}; comparing across "
                f"{field} versions is not a comparison")
            return out

    covered_total, covered_categories, uncovered = _with_coverage(report, baseline)
    if uncovered:
        out["regressions"].append(
            {"scope": "coverage", "baseline": None, "measured": None,
             "delta": None, "missing": uncovered})

    scopes = []
    if covered_total is not None:
        scopes.append(("total", covered_total, baseline.get("total")))
    for name, was in sorted((baseline.get("categories") or {}).items()):
        # A category the baseline names and the run never produced measures
        # zero -- the missing-task rule, one level up.
        scopes.append((f"category:{name}", covered_categories.get(name, 0.0), was))
    for name in sorted(covered_categories):
        if name not in (baseline.get("categories") or {}):
            out["warnings"].append(
                f"category {name!r} is measured here but the baseline does "
                f"not record it; it is ungated until a baseline does")

    for scope, measured, was in scopes:
        if was is None:
            continue
        delta = _delta(measured, was)
        # Gate on the report's own precision. Raw float arithmetic makes a
        # drop of exactly `epsilon` come out at -0.020000000000000018, which
        # would fail a gate while the table prints "-0.0200 vs epsilon
        # 0.0200" -- a verdict contradicted by its own evidence.
        if round(delta, 6) < -epsilon:
            out["regressions"].append({"scope": scope, "baseline": float(was),
                                       "measured": float(measured),
                                       "delta": delta})
    if out["regressions"]:
        out["status"] = "regressed"

    # Printed, never gated (design §11). Worst first, so `--md`'s cap keeps the
    # rows a human would have looked for.
    measured_tasks = report.get("tasks") or {}
    base_tasks = baseline.get("tasks") or {}
    for task_id in sorted(set(measured_tasks) | set(base_tasks)):
        was = base_tasks.get(task_id)
        now = (measured_tasks.get(task_id) or {}).get("total", 0.0)
        out["task_deltas"].append(
            {"task": task_id, "baseline": None if was is None else float(was),
             "measured": float(now),
             "delta": None if was is None else _delta(now, was)})
    out["task_deltas"].sort(key=lambda row: (row["delta"] is None,
                                             row["delta"] or 0.0, row["task"]))
    return round_floats(out)


def report_exit_code(report: dict) -> int:
    """`0` no baseline or met · `1` a regression · `2` harness.

    `2` is the geometry-CI meaning, unchanged: we could not produce a verdict.
    An incomparable baseline is exactly that -- not a pass, not a failure of
    the model.
    """
    status = (report.get("baseline") or {}).get("status")
    if status == "incomparable":
        return 2
    if status == "regressed":
        return 1
    return 0


# ------------------------------------------------------------- rendering

def _num(value) -> str:
    return "—" if value is None else f"{float(value):.4f}"


def _signed(value) -> str:
    return "—" if value is None else f"{float(value):+.4f}"


def _capped(rows: list, limit: int = MAX_RENDERED_TASKS) -> tuple:
    return rows[:limit], max(0, len(rows) - limit)


def _more(extra: int) -> list:
    """The `+N more` line. It does not promise a file that may not exist --
    `report.json` is only written when the caller passes `--json-out`."""
    return [f"_+{extra} more — the full list is in the report document "
            f"(`--json-out`)_", ""] if extra else []


def render_markdown(report: dict) -> str:
    """The human rendering: the category table, then the worst task rows.

    Valid GitHub-flavoured markdown and valid as a PR comment body, in
    `checks.render_markdown`'s idiom -- capped, with a `+N more` line.
    """
    baseline = report.get("baseline") or {}
    status = baseline.get("status")
    headline = f"**{_num(report.get('total'))}**"
    lines = [f"# AgentCAD-Bench — {headline}"
             + (f" — **{status}**" if status else ""), ""]

    facts = [f"task set `{report.get('task_set')}`",
             f"harness {report.get('harness')}",
             f"agentcad {report.get('agentcad')}",
             f"agent `{report.get('agent')}`",
             f"model `{report.get('model')}`",
             f"{report.get('n', 0)} task(s)"]
    lines += [" · ".join(facts), ""]

    lines += ["| Category | Score | Tasks | Missing |", "|---|---:|---:|---:|"]
    for name, row in (report.get("categories") or {}).items():
        lines.append(f"| {name} | {_num(row.get('total'))} | "
                     f"{row.get('n', 0)} | {row.get('missing', 0)} |")
    lines += ["", f"**Total (mean of category means): {_num(report.get('total'))}**",
              ""]

    generation = report.get("generation") or {}
    if generation:
        # AC8: the loop-vs-one-shot delta, per task and total. A positive delta
        # is the multi-turn generation loop beating the single-turn baseline on
        # the same prompt and rubric.
        lines += ["## Generation vs one-shot (AC8)", "",
                  "| Task | Loop | One-shot | Δ |", "|---|---:|---:|---:|"]
        for task_id, gen in generation.items():
            lines.append(
                f"| `{task_id}` | {_num(gen.get('loop_total'))} | "
                f"{_num(gen.get('oneshot_total'))} | "
                f"{_signed(gen.get('delta'))} |")
        lines.append("")

    if status:
        facts = []
        if baseline.get("path"):
            facts.append(f"`{baseline['path']}`")
        facts.append(f"epsilon {_num(baseline.get('epsilon'))}")
        if baseline.get("reason"):
            facts.append(str(baseline["reason"]))
        lines += [f"## Baseline — {status}", "", " · ".join(facts), ""]
        regressions = baseline.get("regressions") or []
        if regressions:
            lines += ["| Scope | Baseline | Measured | Delta |",
                      "|---|---:|---:|---:|"]
            lines += [f"| {row['scope']} | {_num(row.get('baseline'))} | "
                      f"{_num(row.get('measured'))} | "
                      f"{_signed(row.get('delta'))} |"
                      for row in regressions]
            lines.append("")
        for row in regressions:
            # A coverage gap has no two numbers to subtract -- it is the
            # absence of a measurement, so it is named rather than tabulated.
            if row.get("scope") == "coverage":
                shown, extra = _capped(row.get("missing") or [])
                named = ", ".join(f"`{task_id}`" for task_id in shown)
                tail = f" (+{extra} more)" if extra else ""
                lines += [f"**coverage** — {len(row.get('missing') or [])} "
                          f"baseline task(s) were not scored by this run and "
                          f"count as 0.0: {named}{tail}", ""]
        for line in baseline.get("warnings") or []:
            lines += [f"> {line}", ""]

    rows = sorted((report.get("tasks") or {}).items(),
                  key=lambda item: (item[1].get("total", 0.0), item[0]))
    shown, extra = _capped(rows)
    if shown:
        deltas = {row["task"]: row["delta"]
                  for row in baseline.get("task_deltas") or []}
        lines += ["## Tasks (worst first)", "",
                  "| Task | Score | Δ baseline | Over budget | Missing |",
                  "|---|---:|---:|---|---|"]
        for task_id, row in shown:
            lines.append(
                f"| `{task_id}` | {_num(row.get('total'))} | "
                f"{_signed(deltas.get(task_id))} | "
                f"{'yes' if row.get('over_budget') else 'no'} | "
                f"{'**yes**' if row.get('missing') else 'no'} |")
        lines.append("")
        lines += _more(extra)

    warnings = report.get("warnings") or []
    if warnings:
        shown, extra = _capped(warnings)
        lines += ["## Warnings", ""] + [f"- {line}" for line in shown] + [""]
        lines += _more(extra)
    return "\n".join(lines).rstrip() + "\n"
