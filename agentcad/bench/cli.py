"""`agentcad bench` — the whole CLI surface, in one OCP-free module.

Design §9. Two things are worth reading before changing anything here.

**`agentcad/cli.py` takes exactly two edits and this module owns the rest.**
`add_bench_parser(sub)` builds the four sub-subcommands, `cmd_bench(args)`
dispatches them, and both are imported *lazily* from `main()` so
`agentcad serve` pays nothing for a package it never touches.

**The exit code is the API** (design §9.3, `cmd_check`'s rule one lane over):

| command | 0 | 1 | 2 |
|---|---|---|---|
| `bench run` | every selected task ran and was scored | — | harness |
| `bench score` | a score was produced | — | harness |
| `bench report` | no baseline, or the baseline is met | a regression | harness |
| `bench publish` | the page was written | a row was rejected | harness |

`bench run` and `bench score` are deliberately never `1`: a low score or an
over-budget task is a **measurement**, and turning it into a failing exit would
make the runner and the release gate the same thing. FR11's gate is
`bench report --baseline`, and only there.

Everything *after* the measurement — writing the score, printing the table — is
inside the same exit-code mapping as the measurement itself, because a
traceback out of a CLI is process exit 1, the code reserved for "the model is
wrong" (`cmd_check`'s note, `cli.py:1132-1137`).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

#: Identity for every bench command: one lane over from `cmd_check`'s `"ci"`,
#: so a bench run never collides with a human's per-client checkout and never
#: claims a lock a person is holding.
CLIENT_ID = "bench"

#: What `run`/`publish` answer until Tasks 5 and 7 wire them. A stub that
#: printed nothing and exited 0 would be worse than absent: a CI job would read
#: it as "the suite ran".
_NOT_IMPLEMENTED = "not implemented in this slice"

_SCORE_ROW = "  {:<13} {:<16} {:>7} {:>7} {:>9}"
_REPORT_ROW = "  {:<30} {:>8} {:>6} {:>8}"


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

def add_bench_parser(sub) -> None:
    """Add `bench` and its four sub-subcommands to *sub*.

    All four are registered from this slice on, `run` and `publish` included:
    `agentcad bench --help` is a promise about the surface, and a help text that
    grows a subcommand per merge teaches a reader to re-read it every week.
    Their handlers refuse honestly (exit 2) until Tasks 5 and 7 land.
    """
    from ..cli import _finite_arg

    p = sub.add_parser(
        "bench", help="run, score and report the AgentCAD benchmark (PRD-024)",
        description="AgentCAD-Bench: a kernel-scored agentic-CAD benchmark. "
                    "Scoring is mechanical — six subscores measured by the "
                    "geometry kernel, no LLM judging anywhere. `run` drives "
                    "the built-in chat agent over a task set, `score` measures "
                    "one submission against one task, `report` aggregates a "
                    "results directory and gates it against a baseline, and "
                    "`publish` renders the leaderboard.")
    bench_sub = p.add_subparsers(dest="bench_command",
                                 metavar="{run,score,report,publish}")

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
                   help="the results directory to write")
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
                   help="write score.json into this directory")
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
                    "and a rejected row is exit 1.")
    pub.add_argument("leaderboard", help="the leaderboard JSON to render")
    pub.add_argument("-o", "--out", default=None, metavar="PATH",
                     help="write the page here (default: stdout)")
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
    """`agentcad bench` — dispatch to one of the four handlers."""
    handler = {"run": _cmd_run, "score": _cmd_score,
               "report": _cmd_report, "publish": _cmd_publish}.get(
        getattr(args, "bench_command", None))
    if handler is None:
        print("agentcad bench: pick a subcommand: run, score, report, publish",
              file=sys.stderr)
        return 2
    return handler(args)


def _cmd_run(args) -> int:
    print(f"agentcad bench run: {_NOT_IMPLEMENTED}", file=sys.stderr)
    return 2


def _cmd_publish(args) -> int:
    print(f"agentcad bench publish: {_NOT_IMPLEMENTED}", file=sys.stderr)
    return 2


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
        # Known here, the one moment the seatbelt profile can still be widened:
        # the submission and the task bundle live nowhere `_writable_roots`
        # guessed, and on Linux the worker's read of a reference STEP is
        # governed by the same rule set as its writes.
        extra = [str(submission), str(task.root)] + ([work_dir] if work_dir else [])
        service = bench_service(projects_root, extra_writable=extra)
        locks.set_client_id(CLIENT_ID)
        scorer = Scorer(service, build_registry(service))
        score = scorer.score(task, submission, budget_s=args.budget,
                             work_dir=work_dir)
    except AppError as exc:
        print(f"agentcad bench score: {exc.message}", file=sys.stderr)
        _print_problems("score", exc)
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


def _print_problems(command: str, exc) -> None:
    """An `AppError`'s `details["problems"]`, capped.

    The task loader collects **every** defect before it raises: an author
    fixing one per run is a bad afternoon.
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
