"""`generate_from_prompt` — the PRD-018 loop as a bench candidate, plus AC8.

Design §10. A `generate_from_prompt` task is scored exactly like every other
category — the six subscores, the injected rubric, the specs denominator, the
IoU geometry, all of `scoring.Scorer` unchanged — but the candidate is
**produced by the multi-turn generation loop** (`agent/generate.run_generation`)
rather than by bench's single-turn `runner.run_task`. AC8 is the honest
comparison of the two on one prompt+rubric:

* the **loop** iterates until spec-green / budget / abandon, and its best
  candidate's script becomes the task's scored submission (`score.json`);
* the **one-shot baseline** is bench's existing single-turn runner over the
  same prompt (`runner.run_task`), scored the same way (`oneshot_score.json`);
* the **delta** (`generation.json`) is ``loop − one-shot``, per subscore and
  total, and it is what `bench report` surfaces as "beats one-shot".

Three invariants carry the module and every one is a bench trap:

1. **Fail-honest, like the scorer.** This module never *scores* — it produces
   two submissions and hands them to `scoring.Scorer`, whose rule 2
   (`error` is the harness failing to measure; a candidate-caused failure is a
   measured zero with ``status: "ok"``) is the whole point. A loop that
   produced nothing writes a submission with no part file, which the scorer
   measures at zero — never an `error`. The delta likewise refuses to subtract
   across an excluded subscore: a `not_applicable`/`error` side yields
   ``delta: null`` (not comparable), never a number.

2. **Offline-deterministic.** The two client factories are the test seams
   :data:`LOOP_CLIENT_FACTORY` / :data:`ONESHOT_CLIENT_FACTORY` (the
   `runner.CLIENT_FACTORY` precedent): with them set the whole path runs against
   scripted fakes and touches no network, and `score.json` /
   `generation.json` stay byte-identical across two runs of the same fake (the
   scorer already rounds and strips every non-deterministic field, and the delta
   is a pure function of two such scores).

3. **`generation.json` carries no clock, host or path** — it is derived purely
   from the two `score.json` documents, so it inherits their determinism
   (design §6 rule 5). Everything non-deterministic already lives in the loop's
   `run.json`.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..agent.generate import Budget, run_generation
from .runner import require_agent
from .scoring import COPY_IGNORE
from .tasks import SUBSCORES, Task, prompt_text

#: `generation.json`'s schema — the AC8 delta document `bench report` reads.
GENERATION_SCHEMA = 1

#: The loop client factory test seam (a ``() -> client`` or ``(n) -> client``).
#: With it set, the loop runs against a scripted fake and no key is read; with
#: it unset the live factory is built from ``ANTHROPIC_API_KEY`` and a run with
#: no key is refused before the kernel spawns (`runner.require_agent`).
LOOP_CLIENT_FACTORY: Callable[..., Any] | None = None

#: The one-shot baseline client factory test seam. ``None`` lets `run_task`
#: build the real client from the key, exactly as an ordinary `bench run` does.
ONESHOT_CLIENT_FACTORY: Callable[[], Any] | None = None


# ----------------------------------------------------------- the delta

def _subscore(score: dict, name: str) -> dict:
    return (score.get("subscores") or {}).get(name) or {}


def _comparable_value(row: dict):
    """The subscore's value when it was measured, else ``None``.

    Only ``status: "ok"`` is a measurement to subtract: `not_applicable` (the
    task zeroed the weight) and `error` (the harness could not measure) are not
    numbers a delta may pretend to compare.
    """
    if row.get("status") == "ok" and isinstance(row.get("value"), (int, float)):
        return float(row["value"])
    return None


def generation_delta(loop_score: dict, oneshot_score: dict) -> dict:
    """``loop − one-shot`` per subscore and total — AC8's "beats one-shot".

    Pure and total over its two inputs. A subscore delta is ``null`` unless
    **both** sides measured it (rule 1): subtracting a measured zero from an
    excluded subscore would invent a comparison the two runs never made. The
    total delta is always a number — both totals are finite by `score.json`'s
    contract (`report._read_scores` refuses a non-finite one upstream).
    """
    loop_total = float(loop_score.get("total") or 0.0)
    oneshot_total = float(oneshot_score.get("total") or 0.0)
    subscores: dict[str, dict] = {}
    for name in SUBSCORES:
        loop_row, oneshot_row = _subscore(loop_score, name), \
            _subscore(oneshot_score, name)
        loop_value = _comparable_value(loop_row)
        oneshot_value = _comparable_value(oneshot_row)
        delta = (loop_value - oneshot_value
                 if loop_value is not None and oneshot_value is not None
                 else None)
        subscores[name] = {"loop": loop_value, "oneshot": oneshot_value,
                           "delta": delta}
    return {
        "schema": GENERATION_SCHEMA,
        "loop_total": loop_total,
        "oneshot_total": oneshot_total,
        "delta": loop_total - oneshot_total,
        "subscores": subscores,
    }


# ------------------------------------------------------- the loop outcome

@dataclass(frozen=True)
class LoopOutcome:
    """What the loop produced, apart from the submission written to disk."""

    summary: dict
    best: dict | None
    over_budget: bool
    stopped: str


def _loop_budget(task: Task, budget: Budget | dict | None) -> Budget:
    """The loop's `Budget`, derived from the task unless one is passed.

    The task's wall clock and tool-call ceiling map onto the loop's
    ``wall_clock_s`` and ``max_iterations`` — the same two budgets `bench run`
    already enforces on the single-turn runner, so the loop and the one-shot are
    bounded by the *same* task declaration and the delta measures the mode, not
    a budget mismatch.
    """
    if budget is not None:
        return Budget.coerce(budget)
    return Budget(max_iterations=int(task.budgets.turns),
                  wall_clock_s=float(task.budgets.wall_s))


def run_loop_submission(task: Task, *, service, registry, cell, model,
                        api_key: str | None = None, client_factory=None,
                        budget: Budget | dict | None = None,
                        quiet: bool = False) -> LoopOutcome:
    """Drive the PRD-018 loop over *task*'s prompt and return its best candidate.

    *service*/*registry* write into a project under *cell*; the loop creates its
    scratch parts there and this returns the best candidate. The caller turns
    that candidate into a scored submission with :func:`write_loop_submission`.
    """
    require_agent(api_key, client_factory)
    service.create_project(task.target_project)
    project = task.target_project
    summary = asyncio.run(run_generation(
        service, registry, project=project, prompt=prompt_text(task),
        budget=_loop_budget(task, budget), candidates=1,
        client_factory=client_factory, model=model,
        api_key=api_key or "bench-injected-client"))
    candidates = summary.get("candidates") or []
    best_index = summary.get("best")
    best = (candidates[best_index]
            if isinstance(best_index, int) and 0 <= best_index < len(candidates)
            else None)
    stopped = (best or {}).get("terminal_state") or "budget_exhausted"
    return LoopOutcome(summary=summary, best=best,
                       over_budget=stopped != "spec_green", stopped=stopped)


def write_loop_submission(dest: Path, task: Task, best: dict | None) -> Path:
    """Write the best candidate's script as *task*'s target part — the submission.

    The bench "accept": the loop iterates on a scratch part id, and the scored
    submission is a plain project whose one part carries the accepted script at
    the task's own ``target.parts[0]`` id. A candidate that produced no script
    (the loop returned nothing usable) writes a manifest naming the part with no
    file behind it — which the scorer measures as a zero, honestly, never an
    error.
    """
    from ._json import write_json

    dest = Path(dest)
    shutil.rmtree(dest, ignore_errors=True)
    part_id = task.target_parts[0]
    (dest / "parts").mkdir(parents=True)
    script = (best or {}).get("script")
    params = (best or {}).get("params") or {}
    if isinstance(script, str) and script.strip():
        (dest / "parts" / f"{part_id}.py").write_text(script, encoding="utf-8")
    manifest = {"schema_version": 1, "name": task.target_project, "units": "mm",
                "parts": [{"id": part_id, "label": part_id,
                           "params": params}],
                "assembly": {"instances": []}}
    write_json(dest / "project.json", manifest)
    return dest


# -------------------------------------------------- the CLI orchestrator

def run_one_generation_task(task, *, service, scorer, report_dir, work_dir,
                            model, api_key, agent, loop_client_factory,
                            oneshot_client_factory, failures, quiet=False,
                            skills=None, budget=None) -> dict:
    """Run the loop AND the one-shot, score both, write the AC8 delta. Returns
    the `bench.json` row (`cli._run_one_task`'s contract, one comparison wider).

    Never raises: a task that dies takes its own row down and nothing else. The
    cell is a directory this process created with `mkdtemp`, removed in a
    ``finally``; the caller's ``--work-dir`` is its parent and is left untouched.
    """
    from ..core.checks import default_work_root
    from ..core.tools import build_registry
    from . import runner as bench_runner
    from ._json import write_json
    from .cli import _derive_task_service, _install_skills

    category, name = task.id.split("/")
    out = report_dir / "tasks" / category / name
    row = {"category": category, "total": None, "over_budget": True,
           "stopped": "error"}
    cell = Path(tempfile.mkdtemp(prefix="agentcad-bench-gen-",
                                 dir=work_dir or default_work_root(service)))
    try:
        # --- one-shot baseline: bench's single-turn runner, unchanged --------
        oneshot_cell = cell / "oneshot"
        (oneshot_cell / "projects").mkdir(parents=True)
        oneshot_service = _derive_task_service(service, oneshot_cell / "projects")
        engine_skills = _install_skills(oneshot_service, skills)
        os_started = bench_runner._now()
        oneshot_outcome = bench_runner.run_task(
            task, service=oneshot_service,
            registry=build_registry(oneshot_service), cell=oneshot_cell,
            model=model, api_key=api_key, client_factory=oneshot_client_factory,
            quiet=quiet, skills=engine_skills)
        os_finished = bench_runner._now()
        oneshot_submission = out / "oneshot_submission"
        shutil.rmtree(oneshot_submission, ignore_errors=True)
        shutil.copytree(oneshot_cell / "projects" / task.target_project,
                        oneshot_submission,
                        ignore=shutil.ignore_patterns(*COPY_IGNORE))
        oneshot_score = scorer.score(task, oneshot_submission, work_dir=work_dir)

        # --- the loop: the PRD-018 multi-turn generation over the same prompt -
        loop_cell = cell / "loop"
        (loop_cell / "projects").mkdir(parents=True)
        loop_service = _derive_task_service(service, loop_cell / "projects")
        _install_skills(loop_service, skills)
        loop_started = bench_runner._now()
        loop_outcome = run_loop_submission(
            task, service=loop_service, registry=build_registry(loop_service),
            cell=loop_cell, model=model, api_key=api_key,
            client_factory=loop_client_factory, budget=budget, quiet=quiet)
        loop_finished = bench_runner._now()
        loop_submission = out / "submission"
        write_loop_submission(loop_submission, task, loop_outcome.best)
        loop_score = scorer.score(task, loop_submission, work_dir=work_dir)

        # --- the AC8 delta ---------------------------------------------------
        delta = generation_delta(loop_score, oneshot_score)

        write_json(out / "score.json", loop_score)
        write_json(out / "oneshot_score.json", oneshot_score)
        write_json(out / "generation.json", {"task": task.id, **delta})
        write_json(out / "run.json", _gen_run_json(
            task, loop_outcome, oneshot_outcome, agent=agent, model=model,
            started=loop_started, finished=loop_finished, skills=skills))
        write_json(out / "transcript.json", bench_runner.transcript_payload(
            task, oneshot_outcome.transcript, cell=oneshot_cell,
            projects_root=oneshot_cell / "projects"))

        row.update(total=loop_score.get("total"),
                   over_budget=loop_outcome.over_budget,
                   stopped=loop_outcome.stopped)
        if not loop_score.get("weights_effective"):
            failures.append(f"{task.id}: every subscore was excluded")
    except Exception as exc:  # noqa: BLE001 — one task's failure is its own
        row["error"] = f"{type(exc).__name__}: {exc}"
        failures.append(f"{task.id}: {row['error']}")
    finally:
        shutil.rmtree(cell, ignore_errors=True)
    return row


def _gen_run_json(task, loop_outcome, oneshot_outcome, *, agent, model,
                  started, finished, skills):
    """A `run.json` for a generation task: the loop is the canonical run.

    `report._over_budget` reads one field (`over_budget`); the loop-vs-one-shot
    detail lives in `generation.json`. The loop's terminal state stands in for
    `stopped`, and the one-shot's is recorded beside it for provenance.
    """
    from . import HARNESS_VERSION
    from .runner import skills_block

    import agentcad

    return {
        "schema": 1,
        "task": task.id,
        "task_set": task.task_set,
        "task_version": task.version,
        "category": task.category,
        "agent": agent,
        "model": model,
        "agentcad": agentcad.__version__,
        "harness": HARNESS_VERSION,
        "started": started,
        "finished": finished,
        "budgets": {"wall_s": float(task.budgets.wall_s),
                    "turns": int(task.budgets.turns),
                    "api_turns": int(task.budgets.api_turns)},
        "skills": skills_block(skills),
        "over_budget": bool(loop_outcome.over_budget),
        "stopped": loop_outcome.stopped,
        "oneshot_stopped": oneshot_outcome.stopped,
        "generation": "generation.json",
        "transcript": "transcript.json",
    }


# ------------------------------------------------------- CLI factories

def loop_client_factory(api_key: str | None):
    """The loop's client factory: the seam if set, else a live one, else None.

    `run_generation` **requires** a factory, so the live path builds an
    `AsyncAnthropic` here rather than leaning on an engine default the loop does
    not have. ``None`` (no seam, no key) reaches `require_agent`, which refuses.
    """
    if LOOP_CLIENT_FACTORY is not None:
        return LOOP_CLIENT_FACTORY
    if api_key:
        import anthropic

        return lambda: anthropic.AsyncAnthropic(api_key=api_key)
    return None


def oneshot_client_factory():
    """The one-shot's client factory: its own seam, else the runner's seam, else
    ``None`` so `run_task` builds the real client from the key.

    The one-shot baseline *is* bench's single-turn runner, so it honours
    `runner.CLIENT_FACTORY` — the seam an offline `bench run` already sets —
    when its own is unset. ``None`` (neither seam) reaches `run_task`, which
    builds the real client from the key.
    """
    from . import runner as bench_runner

    return (ONESHOT_CLIENT_FACTORY if ONESHOT_CLIENT_FACTORY is not None
            else bench_runner.CLIENT_FACTORY)


def require_generation_agents(api_key) -> None:
    """Refuse a generation run that can drive neither the loop nor the one-shot.

    Both halves need a client, and both are refused *before the kernel spawns*
    (the `runner.require_agent` rule) so a misconfigured run fails with the fix
    in the sentence rather than as a 401 three minutes in. The *effective*
    factories are checked, so a run wired only through the seams is accepted.
    """
    require_agent(api_key, loop_client_factory(api_key))
    require_agent(api_key, oneshot_client_factory())


__all__ = ["GENERATION_SCHEMA", "LOOP_CLIENT_FACTORY", "ONESHOT_CLIENT_FACTORY",
           "LoopOutcome", "generation_delta", "loop_client_factory",
           "oneshot_client_factory", "require_generation_agents",
           "run_loop_submission", "run_one_generation_task",
           "write_loop_submission"]
