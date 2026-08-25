"""`agentcad bench` — the whole CLI surface, in one OCP-free module.

Design §9. Two things are worth reading before changing anything here.

**`agentcad/cli.py` takes exactly two edits and this module owns the rest.**
`add_bench_parser(sub)` builds the sub-subcommands, `cmd_bench(args)`
dispatches them, and both are imported *lazily* from `main()` so
`agentcad serve` pays nothing for a package it never touches.

**The exit code is the API** (design §9.3, `cmd_check`'s rule one lane over):

| command | 0 | 1 | 2 |
|---|---|---|---|
| `bench run` | every selected task ran and was scored | — | harness |
| `bench score` | a score was produced | — | harness |
| `bench prompt` | the prompt was printed | — | harness |
| `bench report` | no baseline, or the baseline is met | a regression | harness |
| `bench publish` | the page was written | a row was rejected, and NOTHING was written | harness |

`bench run` and `bench score` are deliberately never `1`: a low score or an
over-budget task is a **measurement**, and turning it into a failing exit would
make the runner and the release gate the same thing. FR11's gate is
`bench report --baseline`, and only there. `bench publish`'s `1` is the one
exception in this module, and it is not about a model: a leaderboard is a claim
about other people's work, so a row that does not disclose everything the rules
require refuses the **whole** board rather than being dropped from it.

Everything *after* the measurement — writing the score, printing the table — is
inside the same exit-code mapping as the measurement itself, because a
traceback out of a CLI is process exit 1, the code reserved for "the model is
wrong" (`cmd_check`'s note, `cli.py:1132-1137`).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

#: Identity for every bench command: one lane over from `cmd_check`'s `"ci"`,
#: so a bench run never collides with a human's per-client checkout and never
#: claims a lock a person is holding.
CLIENT_ID = "bench"

#: What `bench run` selects when the caller names neither `--tasks` nor
#: `--set` (design §9.2). Not "everything": a task set grows over time and a
#: bare `bench run` must keep meaning the same suite.
DEFAULT_SET = "core"

_SCORE_ROW = "  {:<13} {:<16} {:>7} {:>7} {:>9}"
_REPORT_ROW = "  {:<30} {:>8} {:>6} {:>8}"
_RUN_ROW = "  {:<30} {:>8} {:>18} {:>7}"


# ------------------------------------------------------------- the service

def bench_service(projects_dir, *, extra_writable=None):
    """The headless service a bench command measures through.

    `cli._build_service` with **`examples=False`**: a task derived from a
    bundled example must not be solvable by opening that example (design §8.2).
    The catalog stays registered — `assemble_and_clear` tasks legitimately reach
    for fasteners, and benching without a shipped product surface would measure
    something other than the product.
    """
    from ..cli import _build_service

    return _build_service(Path(projects_dir), extra_writable=extra_writable,
                          examples=False)


def _tasks_root(args):
    """`--tasks-dir` as a path, or `None` for the shipped `benchmarks/tasks`."""
    raw = getattr(args, "tasks_dir", None)
    return Path(raw).expanduser().resolve() if raw else None


# ------------------------------------------------------------- the parser

def _skills_arg(value: str, library=None) -> dict:
    """``--skills`` as ``{"mode", "names"}`` — argparse's ``type`` (PRD-029 §9).

    Validated **here**, in `cli._finite_arg`'s idiom, so an unknown name costs
    a usage line (exit 2) rather than three seconds of kernel spawn and a run
    that silently measured the whole library instead of the one skill the
    caller named.

    The vocabulary is the **shipped index** — what is loadable in *this*
    installation, which is what a run can actually measure. A name the library
    knows but does not offer (a capability gate, an invalid file) is reported
    as exactly that: "unknown skill" would send its author hunting a typo that
    is not there. A *project* skill cannot be selected either, and for the same
    reason — the selection is read before any project exists.
    """
    from ..core.skills import SkillLibrary

    raw = (value or "").strip()
    if raw in ("all", "none"):
        return {"mode": raw, "names": []}
    names = sorted({part.strip() for part in raw.split(",") if part.strip()})
    library = library if library is not None else SkillLibrary()
    selectable = [entry["name"] for entry in library.index()]
    unknown = [name for name in names if name not in selectable]
    if not names or unknown:
        gated = sorted(set(unknown) & set(library.records()))
        problem = (f"unknown skill {', '.join(repr(n) for n in unknown)}"
                   if unknown else "an empty selection")
        why = (f" ({', '.join(repr(n) for n in gated)} exists but is not "
               f"loadable here — a capability gate or an invalid file hides "
               f"it, so a run could not measure it)" if gated else "")
        raise argparse.ArgumentTypeError(
            f"{problem}{why}; --skills takes 'all', 'none', or a "
            f"comma-separated list of: {', '.join(selectable)}")
    return {"mode": "only", "names": names}


def add_bench_parser(sub) -> None:
    """Add `bench` and its sub-subcommands to *sub*.

    All of them are registered together: `agentcad bench --help` is a promise
    about the surface, and a help text that grows a subcommand per merge
    teaches a reader to re-read it every week.
    """
    from ..cli import _finite_arg

    p = sub.add_parser(
        "bench", help="run, score and report the AgentCAD benchmark (PRD-024)",
        description="AgentCAD-Bench: a kernel-scored agentic-CAD benchmark. "
                    "Scoring is mechanical — six subscores measured by the "
                    "geometry kernel, no LLM judging anywhere. `run` drives "
                    "the built-in chat agent over a task set, `score` measures "
                    "one submission against one task, `prompt` prints the "
                    "exact prompt an agent is handed, `report` aggregates a "
                    "results directory and gates it against a baseline, and "
                    "`publish` renders the leaderboard.")
    bench_sub = p.add_subparsers(
        dest="bench_command", metavar="{run,score,prompt,report,publish}")

    # -------------------------------------------------------------- run
    r = bench_sub.add_parser(
        "run", help="drive an agent over a task set and score every task",
        description="Run the selected tasks end to end: prompt the agent in a "
                    "throwaway cell, score what it produced, and write a "
                    "results directory. Never exit 1 — an over-budget or "
                    "low-scoring task is a measurement, not a failure.")
    r.add_argument("--tasks", default=None, metavar="GLOB",
                   help="glob over task ids ('model_from_drawing/*', "
                        "'*/mfd_001*'); composes (AND) with --set")
    r.add_argument("--set", dest="set", default=None, metavar="NAME",
                   help="select tasks by their task.json 'sets' membership; "
                        "neither this nor --tasks selects the whole core set")
    r.add_argument("--agent", default="builtin", choices=("builtin",),
                   help="which agent to drive (default: the built-in chat "
                        "agent, the shipped product surface)")
    r.add_argument("--report", required=True, metavar="DIR",
                   help="the results directory to write; a previous run's "
                        "tasks/ is cleared first, and a non-empty directory "
                        "that is not a results directory is refused untouched")
    r.add_argument("--model", default=None, metavar="NAME",
                   help="model id for the agent (default: the chat engine's)")
    r.add_argument("--work-dir", default=None, metavar="DIR",
                   help="where a run materializes its throwaway cells, in "
                        "unique subdirectories it creates and removes "
                        "(default: a temp dir). It may not be, hold or sit "
                        "inside the task tree, the results directory or the "
                        "projects root")
    r.add_argument("--budget", default=None, metavar="SECONDS",
                   type=_finite_arg("--budget",
                                    "a NaN deadline is never in the past, so "
                                    "it bounds nothing"),
                   help="wall-clock ceiling per task, overriding task.json's")
    r.add_argument("--skills", default="all", metavar="SEL", type=_skills_arg,
                   help="which agent skills the run may load: 'all' (the "
                        "default, the shipped library the product ships), "
                        "'none' (no index in the system prompt and every "
                        "load_skill refused), or a comma-separated list of "
                        "names from the shipped library. Recorded in "
                        "run.json; score.json is unaffected, so two runs that "
                        "differ only here are comparable with "
                        "`bench report --baseline`")
    _output_group(r, "the per-task table")

    # ------------------------------------------------------------ score
    s = bench_sub.add_parser(
        "score", help="score one submission against one task",
        description="Measure a submission — any AgentCAD project directory — "
                    "against one task's rubric, on a COPY, in a throwaway "
                    "cell. The submission is never written to: no history "
                    "commit, no branch sidecar, and .cache/ lands in the cell. "
                    "Exit 0 a score was produced, 2 harness. There is no exit "
                    "1: a low score is a measurement.")
    s.add_argument("submission",
                   help="the candidate project directory (holding project.json)")
    s.add_argument("--task", required=True, metavar="ID",
                   help="the task id, '<category>/<id>'")
    s.add_argument("--tasks-dir", default=None, metavar="DIR",
                   help="the task tree to load from (default: the shipped "
                        "benchmarks/tasks)")
    s.add_argument("--out", default=None, metavar="DIR",
                   help="write score.json into this directory. It is one "
                        "score, not a results directory: `bench report` reads "
                        "the layout `bench run --report DIR` writes")
    s.add_argument("--work-dir", default=None, metavar="DIR",
                   help="where the scorer materializes its throwaway cell, in "
                        "a unique subdirectory it creates and removes "
                        "(default: a temp dir). It may not be, hold or sit "
                        "inside the submission, the task tree or the projects "
                        "root")
    s.add_argument("--budget", default=None, metavar="SECONDS",
                   type=_finite_arg("--budget",
                                    "a NaN deadline is never in the past, so "
                                    "it bounds nothing"),
                   help="deadline read before every kernel call; a build "
                        "already in flight cannot be preempted, so the worst "
                        "case is one such call")
    _output_group(s, "the subscore table")

    # ----------------------------------------------------------- prompt
    pr = bench_sub.add_parser(
        "prompt", help="print the exact prompt an agent is handed for a task",
        description="Print one task's prompt on stdout, exactly as the "
                    "built-in runner hands it to the agent: reviewer-only "
                    "HTML comments stripped and every asset inlined as text. "
                    "`cat prompt.md` is NOT the same document — the file "
                    "carries the task author's rationale, including reference "
                    "parameters and thresholds, which the runner strips and an "
                    "external evaluator must not paste into a model.")
    pr.add_argument("task", metavar="ID", help="the task id, '<category>/<id>'")
    pr.add_argument("--tasks-dir", default=None, metavar="DIR",
                    help="the task tree to load from (default: the shipped "
                         "benchmarks/tasks)")
    pr.add_argument("--json", action="store_true",
                    help="print {task, prompt, assets} as JSON instead of the "
                         "prompt text")

    # ----------------------------------------------------------- report
    rep = bench_sub.add_parser(
        "report", help="aggregate a results directory and gate it",
        description="Aggregate every score.json under a results directory into "
                    "one report — a category total is the mean of its tasks, "
                    "the headline is the mean of the CATEGORY means — and, "
                    "with --baseline, gate it. Pure: no service, no kernel. "
                    "Exit 0 met or ungated, 1 a regression beyond --epsilon, "
                    "2 harness (an unreadable results directory, an "
                    "incomparable baseline).")
    rep.add_argument("results", help="the results directory `bench run` wrote")
    rep.add_argument("--baseline", default=None, metavar="PATH",
                     help="gate against this baseline (benchmarks/baseline.json)")
    rep.add_argument("--epsilon", default=0.02, metavar="F",
                     type=_finite_arg("--epsilon",
                                      "every comparison with NaN is false, so "
                                      "a non-finite tolerance deletes the gate "
                                      "instead of widening it"),
                     help="drop tolerated on the total and on each category "
                          "before it is a regression (default: 0.02)")
    rep.add_argument("--md", default=None, metavar="PATH",
                     help="write the markdown summary here "
                          "($GITHUB_STEP_SUMMARY, a PR comment)")
    rep.add_argument("--json-out", default=None, metavar="PATH",
                     help="write the JSON report here")
    _output_group(rep, "the category table")

    # ---------------------------------------------------------- publish
    pub = bench_sub.add_parser(
        "publish", help="render the static leaderboard page",
        description="Render a leaderboard from submitted rows. A row that does "
                    "not disclose everything the rules require is rejected, "
                    "and a rejected row refuses the WHOLE board (exit 1) with "
                    "nothing written. The page is self-contained: no script, "
                    "no remote asset, no clock reading and no filesystem path.")
    pub.add_argument("leaderboard",
                     help="the leaderboard directory (holding "
                          "rows/<row-id>/row.json + report.json)")
    pub.add_argument("-o", "--out", default=None, metavar="PATH",
                     help="write the page here (default: docs/bench/index.html)")
    pub.add_argument("--title", default=None, metavar="TEXT",
                     help="page title")


def _output_group(parser, what: str) -> None:
    """`check`'s mutually exclusive `--quiet` / `--json` pair (`cli.py:1541`)."""
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--quiet", action="store_true",
                     help="print nothing; the exit code is the answer")
    out.add_argument("--json", action="store_true",
                     help=f"print the document to stdout instead of {what}")


# ------------------------------------------------------------- dispatch

def cmd_bench(args) -> int:
    """`agentcad bench` — dispatch to one of the five handlers."""
    handler = {"run": _cmd_run, "score": _cmd_score, "prompt": _cmd_prompt,
               "report": _cmd_report, "publish": _cmd_publish}.get(
        getattr(args, "bench_command", None))
    if handler is None:
        print("agentcad bench: pick a subcommand: run, score, prompt, report, "
              "publish", file=sys.stderr)
        return 2
    return handler(args)


def _cmd_run(args) -> int:
    """`agentcad bench run` — drive the agent over a task set and score it all.

    `_cmd_score`'s skeleton, one loop wider. The shape worth knowing:

    **One kernel, one cell per task.** Starting a kernel pool per task would
    cost seconds and half a gigabyte 25 times over to run the same builds, so
    the run builds **one** service (`bench_service`, examples off) and every
    task gets a throwaway *cell* under the work root, holding its own
    `projects/` and its own `AgentCADService` over the **shared** kernel —
    `checks._ephemeral_service`'s trick, unmuzzled. Unmuzzled is the point: a
    run measures the product surface as it ships, history snapshots included,
    and the tree it writes into is one this process made and removes.

    **A task's failure is that task's.** Anything a single task raises is
    caught, recorded as its row and printed; the loop goes on. The exit code
    is 0 only when **every selected task produced a score** — and it is never
    1, because a low score or an over-budget task is a *measurement* (§9.3).

    **`bench.json` is written from the selection, not from the survivors.**
    `bench report` takes its roster from that index (`report._index_ids`), so
    a task that was selected and never scored has to appear there or it would
    quietly leave the denominator — which is the one arithmetic a benchmark
    may not get wrong.
    """
    import agentcad

    from ..cli import _accept_work_dir, _release_work_root
    from ..core import locks
    from ..core.model import AppError
    from ..core.tools import build_registry
    from . import HARNESS_VERSION
    from . import generation
    from . import runner as bench_runner
    from ._json import write_json
    from .scoring import Scorer, refuse_scoring_overlap
    from .tasks import load_tasks, tasks_root

    service = None
    projects_root = None
    rows: dict = {}
    failures: list = []
    try:
        tasks_base = _tasks_root(args) or tasks_root()
        set_name = args.set
        if args.tasks is None and set_name is None:
            set_name = DEFAULT_SET
        selected = [_budgeted(task, args.budget)
                    for task in load_tasks(tasks_base, glob=args.tasks,
                                           set_name=set_name)]
        if not selected:
            print(f"agentcad bench run: no task matched "
                  f"--tasks {args.tasks!r} / --set {set_name!r} under "
                  f"{tasks_base}", file=sys.stderr)
            return 2
        model, api_key, client_factory = _agent_config(args, bench_runner)
        report_dir = Path(args.report).expanduser().resolve()
        _accept_report_dir(report_dir)
        projects_root = Path(tempfile.mkdtemp(prefix="agentcad-bench-projects-"))
        # Accepted, refused and created BEFORE the kernel spawns (review I1):
        # the work dir is a writable root and a Landlock rule on a missing
        # path is ENOENT, which loses the grant silently. The results
        # directory stands in for `bench score`'s submission here — it is the
        # tree a run writes and must never materialize a cell inside.
        work_dir = _accept_work_dir(
            args.work_dir,
            lambda root: refuse_scoring_overlap(root, report_dir, tasks_base,
                                                projects_root))
        # **Only the work dir.** `extra_writable` is a WRITE grant
        # (`_build_service` → `KernelClient(writable_dirs)` → the Landlock /
        # seatbelt write rules) handed to the worker that executes the
        # candidate's own Python, so granting the task tree here would let a
        # candidate script overwrite `reference/steps/<part>.step` before
        # `_geometry` measures against it — a 1.0 geometry subscore it wrote
        # itself — and, on `bench run`, corrupt the maintainer's checked-in
        # `benchmarks/` tree for good. It buys nothing either way: reads are
        # unrestricted in the `local` posture and `resource_root()` (the repo
        # root, which holds `benchmarks/`) is read-granted in `hosted`
        # (`sandbox_linux._read_roots`). The results directory was never
        # granted for the same reason — a submission is copied out by *this*
        # process, never written by a worker.
        extra = [work_dir] if work_dir else []
        service = bench_service(projects_root, extra_writable=extra)
        locks.set_client_id(CLIENT_ID)
        scorer = Scorer(service, build_registry(service))
        # Parsed by argparse (`_skills_arg`); `getattr` because a caller
        # building an `args` by hand predates the flag and must keep meaning
        # "the library the product ships".
        skills = bench_runner.skills_block(getattr(args, "skills", None))
        started = bench_runner._now()
        # A `generate_from_prompt` task is produced by the PRD-018 loop, not the
        # single-turn runner (design §10), so its factories are refused up front
        # too — but only when the selection actually contains one, so a plain
        # `bench run` over the v1 set never needs the loop's key.
        from .tasks import GENERATION_CATEGORY
        if any(task.category == GENERATION_CATEGORY for task in selected):
            generation.require_generation_agents(api_key)
        loop_factory = generation.loop_client_factory(api_key)
        oneshot_factory = generation.oneshot_client_factory()
        for task in selected:
            if task.category == GENERATION_CATEGORY:
                rows[task.id] = generation.run_one_generation_task(
                    task, service=service, scorer=scorer, report_dir=report_dir,
                    work_dir=work_dir, model=model, api_key=api_key,
                    agent=args.agent, loop_client_factory=loop_factory,
                    oneshot_client_factory=oneshot_factory,
                    budget=None, failures=failures, quiet=args.quiet,
                    skills=skills)
                continue
            rows[task.id] = _run_one_task(
                task, service=service, scorer=scorer, report_dir=report_dir,
                work_dir=work_dir, model=model, api_key=api_key,
                agent=args.agent, client_factory=client_factory,
                failures=failures, quiet=args.quiet, skills=skills)
        header = {
            "schema": bench_runner.BENCH_SCHEMA,
            "task_set": selected[0].task_set,
            "harness": HARNESS_VERSION,
            "agentcad": agentcad.__version__,
            "agent": args.agent,
            "model": model,
            # The selection is a property of the RUN, so it is stated once for
            # the whole results directory as well as on every `run.json`.
            "skills": dict(skills),
            "started": started,
            "finished": bench_runner._now(),
            "n": len(selected),
            "tasks": rows,
        }
        write_json(report_dir / "bench.json", header)
    except AppError as exc:
        print(f"agentcad bench run: {exc.message}", file=sys.stderr)
        _print_problems(exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad bench run: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    finally:
        # Tolerant of a partial construction, exactly as `_cmd_score` is: setup
        # may have failed before there was a kernel to stop, and a kernel that
        # will not stop must not replace the verdict with its own traceback.
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"agentcad bench run: the kernel did not stop cleanly: "
                      f"{exc}", file=sys.stderr)
            _release_work_root(service)
        if projects_root is not None:
            shutil.rmtree(projects_root, ignore_errors=True)

    # Printing is under the SAME mapping as the run: a traceback out of here
    # would be exit 1, the code reserved for "the model is wrong".
    try:
        _print_run(args, header, failures)
    except Exception as exc:  # noqa: BLE001
        print(f"agentcad bench run: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    return 2 if failures else 0


#: What makes a directory recognisable as a results directory rather than
#: someone's Documents folder. `bench run` writes the first two and
#: `bench report` the last two (design §8.7).
_RESULTS_ENTRIES = frozenset({"bench.json", "tasks", "report.json",
                              "report.md"})


def _accept_report_dir(report_dir: Path) -> None:
    """Refuse a `--report` that is not ours; clear the part of it that is.

    **Why clear.** `report.aggregate` reads **every** `score.json` under
    `<report>/tasks/` (`report._score_paths`), while `bench.json` carries one
    agent, one model and one roster. A second run into the same directory with
    a narrower `--tasks` would therefore publish the *union* of two runs under
    one header — two models reported as one number — so the previous `tasks/`
    tree goes before the loop starts.

    **Why refuse first.** Removing anything is only defensible once the
    directory is known to be a results directory: absent, empty, or already
    carrying one of :data:`_RESULTS_ENTRIES`. Anything else keeps its contents
    and the run stops (exit 2), because `--report ~/Documents` is a typo and
    must never be a delete command. This is `refuse_scoring_overlap`'s rule one
    argument over: refusing beats every cleverer answer.
    """
    from ..core.model import ValidationError

    if not report_dir.exists():
        return
    if not report_dir.is_dir():
        raise ValidationError(
            f"--report {report_dir} is not a directory",
            {"path": str(report_dir)})
    entries = {entry.name for entry in report_dir.iterdir()}
    if entries and not (entries & _RESULTS_ENTRIES):
        raise ValidationError(
            f"--report {report_dir} is not empty and does not look like a "
            f"results directory (no {', '.join(sorted(_RESULTS_ENTRIES))}); "
            f"nothing was written or removed — pass a new directory",
            {"path": str(report_dir), "entries": sorted(entries)[:20]})
    # Ours, so the stale half goes. `report.json`/`report.md` are `bench
    # report`'s to overwrite and are left alone.
    shutil.rmtree(report_dir / "tasks", ignore_errors=True)


def _budgeted(task, budget_s):
    """*task* with `--budget` substituted for its declared wall clock.

    Only the **wall** budget moves: the tool-call ceiling is what keeps a run
    inside one engine turn (`MAX_TOOL_CALLS_PER_TURN`, §8.3) and a flag that
    could raise it would let one invocation measure something the task set does
    not describe. The override lands in the `Task`, so `run.json`'s `budgets`
    block reports the budget that was actually enforced rather than the one
    `task.json` declares.
    """
    import dataclasses

    from .tasks import Budgets

    if budget_s is None:
        return task
    return dataclasses.replace(task, budgets=Budgets(
        wall_s=float(budget_s), turns=task.budgets.turns,
        api_turns=task.budgets.api_turns))


def _agent_config(args, bench_runner):
    """`(model, api_key, client_factory)` for the agent `--agent` names.

    The factory is `runner.CLIENT_FACTORY`, the one test seam: with it set,
    the whole `main()` → argparse → `cmd_bench` → `_cmd_run` path runs offline
    against a scripted client, and with it unset a run with no
    `ANTHROPIC_API_KEY` is **refused before the kernel spawns** rather than
    discovered as a 401 three minutes in.
    """
    from ..agent.chat import DEFAULT_MODEL

    client_factory = bench_runner.CLIENT_FACTORY
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    bench_runner.require_agent(api_key, client_factory)
    return (args.model or DEFAULT_MODEL), api_key, client_factory


def _derive_task_service(parent, projects_dir):
    """A second `AgentCADService` over *projects_dir*, wired like the parent's.

    `checks._ephemeral_service`'s trick — a service over a throwaway tree
    sharing the parent's **warm kernel**, because a kernel pool per task would
    cost seconds and ~0.5 GB, 25 times over, to run the same builds. Unlike
    that one it is deliberately **not muzzled**: it owns the tree it writes
    into and a bench measures the shipped surface, history snapshots included.

    What is easy to miss, and what this function exists for: a bare
    `AgentCADService(...)` is **not** the shipped surface. `cli._build_service`
    attaches five things after construction, and every one of them changes what
    an agent can do or how it is judged:

    * ``work_root`` — without it `checks.default_work_root` answers ``None``
      and `run_checks` / `from_step` cut their cells in
      `tempfile.gettempdir()`, which PRD-006 Decision 1 removed from the
      writable roots. On Linux the confined worker then gets a
      ``PermissionError`` and **the agent is scored down for a harness defect**.
    * ``bundled_indexes`` — the shipped `catalog/` (`cli._register_catalog`).
      `PackageManager` reads it as ``getattr(service, "bundled_indexes",
      None)``, so without it `use_part` finds no fasteners and every
      `assemble_and_clear` task is unsolvable. §8.2 keeps the catalog on for
      exactly this reason.
    * ``writable_roots`` — the sandbox grant, kept so a caller can *check* it.
    * ``store.disk_budget_mb`` — the resolved quota. A bench agent running
      unbudgeted is measuring a product nobody ships.
    * ``usage`` — the `UsageMeter`, so a run's kernel cost is accounted the way
      the product accounts it.

    The **examples are not** carried over: `_register_examples` is the one
    thing `bench_service` switches off (§8.2), and a task derived from a
    bundled example must not be solvable by opening that example.
    """
    from ..core.service import AgentCADService, EventBus

    service = AgentCADService(Path(projects_dir), parent.kernel, EventBus())
    service.work_root = getattr(parent, "work_root", None)
    service.writable_roots = list(getattr(parent, "writable_roots", None) or ())
    service.store.disk_budget_mb = getattr(parent.store, "disk_budget_mb", None)
    # Both set only when the parent has one: `AgentCADService` leaves `usage`
    # at `None` and never defines `bundled_indexes`, and writing `None` over an
    # absent attribute would turn "no catalog was registered" into "a catalog
    # was registered and is empty".
    for name in ("usage", "bundled_indexes"):
        value = getattr(parent, name, None)
        if value is not None:
            setattr(service, name, value)
    return service


def _install_skills(task_service, selection: dict):
    """Apply a `--skills` selection to *task_service*; return the engine's library.

    Two surfaces, one selection, and they are **not** the same edit — which is
    the whole reason this function exists rather than one keyword on
    `run_task`:

    * the **engine** takes a library or ``None``. ``None`` is the historical
      `ChatEngine` byte-for-byte (no skills block in the system prompt), which
      is what ``--skills none`` has to mean;
    * the **tools** read ``service.skills``, and `load_skill` is on the
      registry whatever the engine holds. So a run that told the agent nothing
      about skills but still answered `load_skill` with the whole library would
      be measuring the library it claims to have switched off.

    ``none`` is therefore ``only`` over the empty set on the service side, and
    the refusal an out-of-selection name gets is the library's own —
    `NotFoundError` / ``skill_not_found``, hinted with ``bench --skills``
    (`core/skills.py`). The bench does not spell that refusal a second time.
    """
    from ..core.skills import SkillBudget, SkillLibrary

    mode = (selection or {}).get("mode") or "all"
    if mode == "all":
        # The service's own library, built by `AgentCADService.__init__` over
        # this cell's store: the shipped surface, not a second construction of
        # it that could drift from what the product does. Its budget is
        # `SkillBudget.from_config()`, which is the same object shape
        # `bench_runner.run_task` hands the engine.
        return task_service.skills
    # The **same** budget the engine gets. `run_task` derives the engine's
    # from `SkillBudget.from_config()`; deriving this one the same way means
    # one config read cannot produce a library that truncates at one cap and
    # an engine that accounts at another — and `SkillBudget` normalizes
    # `max_skill_chars` to `max_loaded_chars` on construction, so both sides
    # normalize identically.
    library = SkillLibrary(task_service.store,
                           budget=SkillBudget.from_config(),
                           only=frozenset((selection or {}).get("names") or ()))
    task_service.skills = library
    return None if mode == "none" else library


def _run_one_task(task, *, service, scorer, report_dir, work_dir, model,
                  api_key, agent, client_factory, failures, quiet=False,
                  skills=None) -> dict:
    """Run, copy out, score and write one task. Returns its `bench.json` row.

    Never raises: a task that dies takes its own row down and nothing else.
    The cell is removed in a `finally` — and it is a directory **this process
    created** with `mkdtemp`, so removing it breaks nobody's "never delete a
    directory it did not create" contract; the caller's `--work-dir` is its
    parent and is left exactly as it was.
    """
    from ..core.checks import default_work_root
    from ..core.tools import build_registry
    from . import runner as bench_runner
    from ._json import write_json
    from .scoring import COPY_IGNORE

    # One source for both halves: `task.id` is the key `bench.json`'s roster is
    # stated in and the key `report._score_paths` reconstructs from these two
    # directory names, so deriving the path from anything else invites a run
    # that files a score where the reporter will not look for it.
    category, name = task.id.split("/")
    out = report_dir / "tasks" / category / name
    # `stopped: "error"` and `over_budget: True` are the same statement — the
    # invariant is `over_budget == (stopped != "model_ended_turn")`, and it has
    # to hold on the row a task that never returned an outcome leaves behind.
    row = {"category": category, "total": None, "over_budget": True,
           "stopped": "error"}
    cell = Path(tempfile.mkdtemp(prefix="agentcad-bench-run-",
                                 dir=work_dir or default_work_root(service)))
    try:
        (cell / "projects").mkdir()
        task_service = _derive_task_service(service, cell / "projects")
        # BEFORE `build_registry`: the pack reads `service.skills` inside its
        # handlers, but a selection installed after the agent had already been
        # handed its tools would be a race waiting to be written.
        engine_skills = _install_skills(task_service, skills)
        started = bench_runner._now()
        outcome = bench_runner.run_task(
            task, service=task_service, registry=build_registry(task_service),
            cell=cell, model=model, api_key=api_key,
            client_factory=client_factory, quiet=quiet, skills=engine_skills)
        finished = bench_runner._now()
        row.update(over_budget=outcome.over_budget, stopped=outcome.stopped)

        submission = out / "submission"
        shutil.rmtree(submission, ignore_errors=True)
        shutil.copytree(cell / "projects" / task.target_project, submission,
                        ignore=shutil.ignore_patterns(*COPY_IGNORE))
        write_json(out / "transcript.json", bench_runner.transcript_payload(
            task, outcome.transcript, cell=cell,
            projects_root=cell / "projects"))
        write_json(out / "run.json", bench_runner.run_json(
            task, outcome, agent=agent, model=model, started=started,
            finished=finished, skills=skills))

        score = scorer.score(task, submission, work_dir=work_dir)
        write_json(out / "score.json", score)
        row["total"] = score.get("total")
        if not score.get("weights_effective"):
            # §4.8's harness lane: every subscore excluded is no verdict at
            # all, which is a failure of the run and not a zero.
            failures.append(f"{task.id}: every subscore was excluded")
    except Exception as exc:  # noqa: BLE001 — one task's failure is its own
        row["error"] = f"{type(exc).__name__}: {exc}"
        failures.append(f"{task.id}: {row['error']}")
    finally:
        shutil.rmtree(cell, ignore_errors=True)
    return row


def _run_lines(header: dict, failures: list) -> list:
    """The per-task table, then whatever went wrong, for a human on stderr."""
    lines = [f"task set {header.get('task_set')} · agent "
             f"{header.get('agent')} · model {header.get('model')} · "
             f"{header.get('n', 0)} task(s)",
             _RUN_ROW.format("task", "score", "stopped", "budget")]
    for task_id, row in (header.get("tasks") or {}).items():
        total = row.get("total")
        lines.append(_RUN_ROW.format(
            str(task_id)[:30], "—" if total is None else f"{float(total):.4f}",
            str(row.get("stopped")), "over" if row.get("over_budget") else "ok"))
    if failures:
        lines.append("not scored:")
        lines += [f"  - {str(line).splitlines()[0][:200]}"
                  for line in failures[:20]]
    return lines


def _print_run(args, header: dict, failures: list) -> None:
    """stdout is a contract, stderr is for humans (`_print_score`'s rule)."""
    from ._json import canonical_json

    if args.quiet:
        return
    if args.json:
        sys.stdout.write(canonical_json(header).decode())
        return
    for line in _run_lines(header, failures):
        print(line, file=sys.stderr)
    scored = sum(1 for row in (header.get("tasks") or {}).values()
                 if row.get("total") is not None)
    print(f"bench run: {scored}/{header.get('n', 0)} task(s) scored → "
          f"{Path(args.report).expanduser()}")


#: Where the leaderboard lands when `-o` is not given (design §12). Relative,
#: so it is the repo's published page when the command is run from a checkout
#: and never an absolute path baked into a shipped binary.
DEFAULT_PAGE = "docs/bench/index.html"


def _cmd_publish(args) -> int:
    """`agentcad bench publish` — render the board, or refuse it whole.

    Three exit codes and the middle one is the point: **1 is a row rejected for
    incomplete disclosure, and nothing was written**. It is the one command in
    the bench where 1 means "the input is wrong" rather than "the model is
    wrong", because a leaderboard is a claim about other people's work and a
    board that published the disclosed rows and quietly dropped the rest would
    make the disclosure rule decorative.

    The two steps are deliberate rather than one `publish()` call: `load_rows`
    never raises for a bad row, so the exit-1 lane is separated from the
    harness lane *before* anything can be written, and every problem of every
    row is printed at once — an author fixing one per run is a bad afternoon.

    **No service and no kernel**: this command is pure over a directory.
    """
    from ..core.model import AppError
    from . import publish as bench_publish
    from .tasks import load_tasks

    try:
        # The roster is the shipped task set: rule 5 (`_coverage_problems`) is
        # what stops a row buying a place by running the easy half, and it can
        # only ask that question against the tasks this harness ships.
        #
        # Deliberately NOT per row: every row is measured against the roster
        # this checkout ships, whatever `task_set` the row declares. That is
        # right while one set exists (a row naming another set is measured
        # against the only tasks anyone can reproduce) and is the line to
        # revisit when a second one does — a v2 board wants
        # `load_tasks(set_name=row["task_set"])` and a per-set section, not one
        # roster spanning both.
        expected = [task.id for task in load_tasks()]
        board = Path(args.leaderboard).expanduser()
        rows, problems = bench_publish.load_rows(board, expected)
        if problems:
            print(f"agentcad bench publish: the leaderboard was not written; "
                  f"{len(problems)} disclosure problem"
                  f"{'' if len(problems) == 1 else 's'}:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        out = Path(args.out or DEFAULT_PAGE).expanduser()
        result = bench_publish.publish(
            board, out, title=args.title or "AgentCAD-Bench",
            expected_tasks=expected)
    except AppError as exc:
        print(f"agentcad bench publish: {exc.message}", file=sys.stderr)
        _print_problems(exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad bench publish: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    print(f"bench publish: {result['rows']} row(s) over "
          f"{len(result['categories'])} category(ies) → {result['path']}")
    return 0


# ---------------------------------------------------------------- score

def _cmd_score(args) -> int:
    """`agentcad bench score` — measure one submission, `cmd_check`'s skeleton.

    Setup is **inside** the mapping: loading the task, accepting the work dir
    and starting the kernel are as able to fail as the measurement itself, and
    a traceback out of here would be exit 1.

    The projects root is a throwaway `mkdtemp`. The scorer never opens the
    submission through it — it copies into a cell and opens a muzzled ephemeral
    service there — but `_build_service` needs *a* root, and pointing one at the
    user's tree would put every project of theirs in the confined worker's
    writable set for a run that has no business reading any of them.
    """
    from ..cli import _accept_work_dir, _release_work_root
    from ..core import locks
    from ..core.model import AppError
    from ..core.tools import build_registry
    from ._json import write_json
    from .scoring import Scorer, refuse_scoring_overlap
    from .tasks import load_task

    service = None
    projects_root = None
    try:
        task = load_task(args.task, _tasks_root(args))
        submission = Path(args.submission).expanduser().resolve()
        projects_root = Path(tempfile.mkdtemp(prefix="agentcad-bench-projects-"))
        # Accepted, refused and created BEFORE the kernel spawns (review I1,
        # `cli._accept_work_dir`): a `--work-dir` is a writable root, a Landlock
        # rule on a path that does not exist is ENOENT, and the grant is lost
        # with it. `Scorer.score` asks the same question again with the
        # authoritative canonical path, and a refused path leaves nothing behind.
        work_dir = _accept_work_dir(
            args.work_dir,
            lambda root: refuse_scoring_overlap(root, submission, task.root,
                                                projects_root))
        # **Only the work dir**, and never the submission or the task bundle:
        # `extra_writable` is a WRITE grant handed to the worker that executes
        # the candidate's own Python (`_cmd_run`'s note), so granting the task
        # bundle would let a candidate script rewrite the reference STEP it is
        # about to be measured against, and granting the submission would put
        # the caller's own directory — the one thing this command promises
        # never to write — inside the confined worker's write set. Neither
        # grant buys a read: reads are unrestricted in the `local` posture, and
        # in `hosted` the read roots are their own list
        # (`sandbox_linux._read_roots`).
        extra = [work_dir] if work_dir else []
        service = bench_service(projects_root, extra_writable=extra)
        locks.set_client_id(CLIENT_ID)
        scorer = Scorer(service, build_registry(service))
        score = scorer.score(task, submission, budget_s=args.budget,
                             work_dir=work_dir)
    except AppError as exc:
        print(f"agentcad bench score: {exc.message}", file=sys.stderr)
        _print_problems(exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad bench score: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    finally:
        # Tolerant of a partial construction: setup may have failed before
        # there was a kernel to stop, and a kernel that will not stop must not
        # replace the verdict with its own traceback.
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"agentcad bench score: the kernel did not stop "
                      f"cleanly: {exc}", file=sys.stderr)
            _release_work_root(service)
        # A directory this process made with `mkdtemp`, so removing it breaks
        # nobody's "never delete a directory it did not create" contract — the
        # caller's `--work-dir` is a different path and is never touched.
        if projects_root is not None:
            shutil.rmtree(projects_root, ignore_errors=True)

    # Writing and printing are under the SAME mapping as the run: a traceback
    # out of here would be exit 1, the code reserved for "the model is wrong".
    try:
        written = []
        if args.out:
            target = Path(args.out).expanduser() / "score.json"
            write_json(target, score)
            written.append(str(target))
        _print_score(args, score, written)
    except Exception as exc:  # noqa: BLE001
        print(f"agentcad bench score: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    # Every subscore excluded means there is no verdict to report — design
    # §4.8's harness lane, not a zero. A zero is a measurement; this is not one.
    return 0 if score.get("weights_effective") else 2


def _print_problems(exc) -> None:
    """An `AppError`'s `details["problems"]`, capped.

    The task loader and the publish gate both collect **every** defect before
    they raise: an author fixing one per run is a bad afternoon.
    """
    problems = (getattr(exc, "details", None) or {}).get("problems") or []
    for line in problems[:20]:
        print(f"  - {line}", file=sys.stderr)
    if len(problems) > 20:
        print(f"  - (+{len(problems) - 20} more)", file=sys.stderr)


def _score_lines(score: dict, written: list) -> list:
    """The subscore table, for a human reading stderr."""
    lines = [f"{score.get('task')} — {score.get('category')} · task set "
             f"{score.get('task_set')} v{score.get('task_version')} · harness "
             f"{score.get('harness')} · agentcad {score.get('agentcad')}",
             _SCORE_ROW.format("subscore", "status", "value", "weight",
                               "contrib")]
    effective = score.get("weights_effective") or {}
    for name, entry in sorted((score.get("subscores") or {}).items()):
        share = effective.get(name)
        # The contribution is computed from `weights_effective`, never from the
        # declared weight: excluded subscores are renormalised away, and a table
        # whose column does not sum to the total is a table that lies.
        contrib = ("—" if share is None
                   else f"{float(entry.get('value', 0.0)) * float(share):.4f}")
        lines.append(_SCORE_ROW.format(
            str(name), str(entry.get("status")),
            f"{float(entry.get('value', 0.0)):.4f}",
            f"{float(entry.get('weight', 0.0)):.2f}", contrib))
    if score.get("notes"):
        lines.append("notes:")
        lines += [f"  - {str(note).splitlines()[0][:200]}"
                  for note in score["notes"][:20]]
    lines += [f"wrote {path}" for path in written]
    return lines


def _print_score(args, score: dict, written: list) -> None:
    """stdout is a contract, stderr is for humans (`_print_check`, `cli.py:853`).

    `--json` **replaces** the table with the canonical document on stdout, so
    `agentcad bench score --json | jq` sees exactly the bytes `score.json`
    holds — the same rounding, the same key order. `--quiet` prints nothing
    anywhere and the exit code is the whole answer.
    """
    from ._json import canonical_json

    if args.quiet:
        return
    if args.json:
        sys.stdout.write(canonical_json(score).decode())
        return
    for line in _score_lines(score, written):
        print(line, file=sys.stderr)
    total = score.get("total")
    print(f"bench score: {score.get('task')} — "
          f"{'—' if total is None else f'{float(total):.4f}'} "
          f"over {len(score.get('weights_effective') or {})} subscore(s)")


# --------------------------------------------------------------- prompt

def _cmd_prompt(args) -> int:
    """`agentcad bench prompt` — the prompt, byte-for-byte as an agent gets it.

    **Why this is a command and not `cat prompt.md`.** `prompt.md` is authored
    for two audiences: the agent, and the reviewer who has to judge whether the
    task is fair. The reviewer's half lives in HTML comments — the rationale
    for a weight override, the reference parameters a threshold was derived
    from — and `tasks.prompt_text` strips it (`strip_reviewer_comments`) before
    the built-in runner hands the prompt over. An external evaluator reading
    the file and pasting it "verbatim" would therefore hand their model a
    document the built-in agent never sees, containing part of the answer. One
    command that emits exactly `prompt_text` is the whole fix.

    **No service and no kernel**: this command is pure over a task bundle.
    """
    from ..core.model import AppError
    from ._json import canonical_json
    from .tasks import load_task, prompt_text

    try:
        task = load_task(args.task, _tasks_root(args))
        text = prompt_text(task)
        if args.json:
            sys.stdout.write(canonical_json({
                "task": task.id,
                "prompt": text,
                "assets": [asset.relative_to(task.root).as_posix()
                           for asset in task.asset_paths]}).decode())
        else:
            # `prompt_text` already ends in a newline; `write` rather than
            # `print` so the bytes on stdout are the bytes the agent is handed.
            sys.stdout.write(text)
    except AppError as exc:
        print(f"agentcad bench prompt: {exc.message}", file=sys.stderr)
        _print_problems(exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad bench prompt: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    return 0


# --------------------------------------------------------------- report

def _cmd_report(args) -> int:
    """`agentcad bench report` — aggregate, optionally gate, write, print.

    **No service and no kernel**: this command is pure over a results
    directory, which is why a CI job can run it on a machine that has never
    built a solid.
    """
    from ..core.model import AppError
    from ..core.project import ProjectStore
    from ._json import read_json, write_json
    from .report import (aggregate, compare_baseline, render_markdown,
                         report_exit_code)

    try:
        report = aggregate(Path(args.results).expanduser())
        if args.baseline:
            baseline_path = Path(args.baseline).expanduser()
            report["baseline"] = compare_baseline(
                report, read_json(baseline_path), args.epsilon,
                path=baseline_path)
        written = []
        if args.json_out:
            target = Path(args.json_out).expanduser()
            write_json(target, report)
            written.append(str(target))
        if args.md:
            target = Path(args.md).expanduser()
            # Atomic, like every other report this codebase writes: a CI job
            # that reads a half-written summary is worse than one that reads
            # none.
            ProjectStore._atomic_write(target, render_markdown(report).encode())
            written.append(str(target))
        _print_report(args, report, written)
    except AppError as exc:
        print(f"agentcad bench report: {exc.message}", file=sys.stderr)
        _print_problems(exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad bench report: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    return report_exit_code(report)


def _report_lines(report: dict, written: list) -> list:
    """The category table, the offending scopes, and what was written."""
    baseline = report.get("baseline") or {}
    facts = [f"task set {report.get('task_set')}",
             f"harness {report.get('harness')}",
             f"agent {report.get('agent')}",
             f"model {report.get('model')}",
             f"{report.get('n', 0)} task(s)"]
    lines = [" · ".join(facts),
             _REPORT_ROW.format("category", "score", "n", "missing")]
    for name, row in (report.get("categories") or {}).items():
        lines.append(_REPORT_ROW.format(
            str(name), f"{float(row.get('total') or 0.0):.4f}",
            row.get("n", 0), row.get("missing", 0)))
    if baseline.get("regressions"):
        lines.append(f"regressions (epsilon "
                     f"{float(baseline.get('epsilon') or 0.0):.4f}):")
        for row in baseline["regressions"][:20]:
            if row.get("scope") == "coverage":
                missing = row.get("missing") or []
                lines.append(f"  - coverage — {len(missing)} baseline task(s) "
                             f"were not scored and count as 0.0: "
                             f"{', '.join(missing[:5])}"
                             + (" …" if len(missing) > 5 else ""))
                continue
            lines.append(f"  - {row.get('scope')}: "
                         f"{float(row.get('baseline') or 0.0):.4f} → "
                         f"{float(row.get('measured') or 0.0):.4f} "
                         f"({float(row.get('delta') or 0.0):+.4f})")
    for line in (baseline.get("warnings") or [])[:20]:
        lines.append(f"  ! {line}")
    if report.get("warnings"):
        lines.append("warnings:")
        lines += [f"  - {str(line).splitlines()[0][:200]}"
                  for line in report["warnings"][:20]]
    lines += [f"wrote {path}" for path in written]
    return lines


def _print_report(args, report: dict, written: list) -> None:
    from ._json import canonical_json

    if args.quiet:
        return
    if args.json:
        sys.stdout.write(canonical_json(report).decode())
        return
    for line in _report_lines(report, written):
        print(line, file=sys.stderr)
    baseline = report.get("baseline") or {}
    total = report.get("total")
    verdict = (f"bench report: "
               f"{'—' if total is None else f'{float(total):.4f}'} "
               f"over {report.get('n', 0)} task(s)")
    if baseline.get("status"):
        verdict += f" — baseline {baseline['status']}"
        if baseline.get("reason"):
            verdict += f" ({baseline['reason']})"
    print(verdict)


__all__ = ["add_bench_parser", "bench_service", "cmd_bench", "CLIENT_ID"]
