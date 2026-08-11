"""AgentCAD command-line interface.

Commands:
    agentcad serve [--port N] [--projects-dir P] [--no-open]
    agentcad open                    # serve + open the browser
    agentcad mcp                     # MCP stdio server (proxies the HTTP API)
    agentcad new <name>              # create a project
    agentcad export <project> <part> --format step|stl|3mf [-o OUT]
    agentcad check [--project P] [--ref REF] [--report R] [--md M]
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

import agentcad
from ._resources import resource_root
from ._spawn import worker_argv  # noqa: F401 — re-exported; kernel spawn helper
from .config import get_port

DEFAULT_PROJECTS_DIR = Path.home() / "AgentCAD" / "projects"


def _build_service(projects_dir: Path, extra_writable: list[str] | None = None):
    """The service every command shares: one warm kernel, no server.

    *extra_writable* appends to the sandbox's writable roots and must be known
    **here**, before the workers spawn: the seatbelt profile is fixed at spawn,
    so a `agentcad check --work-dir` outside the system temp dir cannot be
    granted afterwards. The default leaves the roots byte-identical for every
    other caller.
    """
    from .config import get_kernel_pool_size
    from .core.service import AgentCADService, EventBus
    from .kernel.client import KernelClient
    from .kernel.pool import KernelPool

    size = get_kernel_pool_size()
    writable = _writable_roots(projects_dir)
    if extra_writable:
        writable += [str(root) for root in extra_writable]
    if size == 1:
        kernel = KernelClient(writable_dirs=writable)
    else:
        kernel = KernelPool(size=size, writable_dirs=writable)
    kernel.start()
    try:
        service = AgentCADService(projects_dir, kernel, EventBus())
        _register_examples(service)
    except BaseException:
        # The workers are already running: anything that raises between here
        # and the return would leave one process per worker (~0.5 GB each)
        # behind, with nobody holding a reference to stop them.
        try:
            kernel.stop()
        except Exception:  # noqa: BLE001 — the original failure is the answer
            pass
        raise
    return service


def _writable_roots(projects_dir: Path) -> list[str]:
    """Directories the sandboxed kernel workers may write to: the projects
    dir (part .cache meshes, exports/), the user config dir, each registered
    example project, and the system temp dir (added by the profile builder
    too, listed here for status transparency)."""
    import tempfile

    roots = [
        str(projects_dir),
        str(Path.home() / ".agentcad"),
        tempfile.gettempdir(),
    ]
    examples = resource_root() / "examples"
    if examples.is_dir():
        for child in sorted(examples.iterdir()):
            if (child / "project.json").is_file():
                roots.append(str(child))
    return roots


def _register_examples(service) -> None:
    examples = resource_root() / "examples"
    if not examples.is_dir():
        return
    for child in sorted(examples.iterdir()):
        if (child / "project.json").is_file():
            try:
                service.store.open(child)
            except Exception as exc:  # noqa: BLE001 — a broken example must not block startup
                print(f"warning: could not open example {child.name}: {exc}", file=sys.stderr)


def _make_chat_engine(service, registry):
    try:
        from .agent.chat import ChatEngine
    except ImportError:
        return None
    return ChatEngine(registry, service.bus)


def cmd_serve(args, open_browser: bool) -> None:
    import uvicorn

    from .core.tools import build_registry
    from .server.app import create_app

    port = args.port or get_port()
    projects_dir = Path(args.projects_dir or DEFAULT_PROJECTS_DIR)
    service = _build_service(projects_dir)
    registry = build_registry(service)
    chat_engine = _make_chat_engine(service, registry)
    app = create_app(service, registry, chat_engine)

    url = f"http://127.0.0.1:{port}"
    if open_browser and not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"AgentCAD {agentcad.__version__} — {url}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def cmd_mcp(args) -> None:
    from .agent.mcp_server import run_mcp_server

    run_mcp_server()


def cmd_worker(args) -> None:
    # Hidden subcommand: run the kernel worker loop in THIS process. It exists
    # so a frozen (PyInstaller) bundle can re-exec its own executable as the
    # worker (see agentcad._spawn.worker_argv). Imported lazily because the
    # worker module imports build123d/OCP, which the server process must never
    # load — this branch only ever runs in the dedicated worker subprocess.
    from .kernel.worker import main as worker_main

    worker_main()


def cmd_new(args) -> None:
    from .core.project import ProjectStore

    store = ProjectStore(Path(args.projects_dir or DEFAULT_PROJECTS_DIR))
    path = store.create(args.name)
    print(f"created {path}")


def cmd_export(args) -> None:
    service = _build_service(Path(args.projects_dir or DEFAULT_PROJECTS_DIR))
    try:
        project = args.project
        if "/" in project or project.startswith("."):
            project = service.open_project(project)["name"]
        result = service.export_part(project, args.part, args.format)
        out = result["path"]
        if args.output:
            import shutil

            shutil.copy(out, args.output)
            out = args.output
        print(f"exported {out} ({result['size_bytes']} bytes)")
    finally:
        service.kernel.stop()


def _is_path(project: str) -> bool:
    """``cmd_export``'s idiom: a project argument is a path, not a name, when
    it contains a separator or starts with a dot."""
    return "/" in project or project.startswith(".")


def _check_stages(value: str | None) -> tuple[str, ...]:
    """``--stages build,assembly`` as a validated tuple.

    Validated *here*, before the kernel starts: a typo'd stage name should cost
    a millisecond and a usage line, not three seconds of worker spawn. The
    derived ``determinism`` stage is deliberately not selectable — it certifies
    the product guarantee, not the project, and has its own flag.
    """
    from .core.checks import STAGES

    if value is None:
        return STAGES
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    unknown = [name for name in names if name not in STAGES]
    if not names or unknown:
        named = ", ".join(repr(name) for name in unknown) or "an empty list"
        raise ValueError(f"unknown --stages value: {named}; expected a "
                         f"comma-separated subset of {', '.join(STAGES)}")
    return names


def _finite_arg(flag: str, why: str):
    """An ``argparse`` ``type`` for a limit: finite, non-negative, or exit 2.

    ``type=float`` accepts ``nan`` and ``inf``, and a non-finite limit is not a
    loose limit — it is **no limit at all**, silently: every comparison with
    NaN is false, so ``--budget nan`` switches off the deadline it configures
    and ``--min-volume nan`` makes every ``volume > min_volume`` false, which
    reports a genuinely interfering assembly as green (review C9).

    Refused here, before the kernel starts, because an invocation the user can
    still fix should cost a usage line rather than three seconds of worker
    spawn. ``core.checks`` refuses the same values again, for the tool and the
    route.
    """
    import math

    def parse(value: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(
                f"{flag} must be a number; got {value!r}") from None
        if not math.isfinite(number) or number < 0:
            raise argparse.ArgumentTypeError(
                f"{flag} must be a finite, non-negative number; got {value!r} "
                f"({why})")
        return number

    return parse


def _write_check_outputs(args, report: dict) -> list[str] | None:
    """Write ``--report``/``--md``; None means "could not", which is exit 2.

    Atomic, like every other report this codebase writes: a CI job that reads a
    half-written ``report.json`` is worse than one that reads none.
    """
    import json

    from .core.checks import render_markdown
    from .core.project import ProjectStore

    targets: list[tuple[Path, bytes]] = []
    if args.report:
        targets.append((Path(args.report).expanduser(),
                        (json.dumps(report, indent=2) + "\n").encode()))
    if args.md:
        targets.append((Path(args.md).expanduser(),
                        render_markdown(report).encode()))
    written: list[str] = []
    for path, data in targets:
        try:
            ProjectStore._atomic_write(path, data)
        except OSError as exc:
            print(f"agentcad check: could not write {path}: {exc}",
                  file=sys.stderr)
            return None
        written.append(str(path))
    return written


def _can_post(runner) -> bool:
    """Whether this run could post to a proposal at all.

    The runner owns the answer (proposals are a git feature and
    ``tools_proposals`` self-disables without one); the ``getattr`` is for a
    runner that does not know the question — an embedder's stand-in, or a
    ``CheckRunner`` from a version before posting existed.
    """
    ask = getattr(runner, "can_post", None)
    return bool(ask) and bool(ask())


def _post_note(message: str) -> None:
    """Posting notes go to stderr **even under ``--quiet``**: ``--quiet`` says
    the exit code is the answer about the *check*, and "you asked me to post
    this and I did not" is not something an exit code can say."""
    print(f"agentcad check: {message}", file=sys.stderr)


def _post_check(runner, project: str, args, report: dict,
                pid: str | None) -> int | None:
    """Post the report to a proposal. Returns an exit-code override, or None.

    ``--auto-proposal`` matches **active** proposals whose source is the branch
    that was checked: none is a warning (most checks are not about a proposal),
    and more than one is exit 2 — guessing which proposal a verdict belongs to
    is worse than refusing to say.
    """
    from .core.model import AppError

    if pid is None and args.auto_proposal:
        matches = runner.matching_proposals(project, report)
        branch = runner.measured_branch(project, report)
        if not matches:
            _post_note(f"--auto-proposal: no active proposal has source "
                       f"{branch!r}; nothing was posted")
            return None
        if len(matches) > 1:
            ids = ", ".join(str(row.get("id")) for row in matches)
            _post_note(f"--auto-proposal: {len(matches)} active proposals "
                       f"share source {branch!r} ({ids}); refusing to guess — "
                       f"pass --proposal ID")
            return 2
        pid = str(matches[0].get("id"))
    if pid is None:
        return None
    try:
        receipt = runner.post_to_proposal(project, pid, report)
    except AppError as exc:
        _post_note(f"could not post to proposal {pid}: {exc.message}")
        return 2
    _post_note(f"posted to proposal {pid}: {receipt['status']} "
               f"(exit {receipt['exit_code']}) — {receipt['path']}")
    return None


_CHECK_ROW = "  {:<10} {:<6} {:>5} {:>5} {:>5} {:>6} {:>6}  {:>7}"


def _check_lines(report: dict, written: list[str]) -> list[str]:
    """The stage table and what went wrong, for a human reading stderr."""
    source = report.get("source") or {}
    facts = [str(source.get("kind") or "worktree")]
    for key, label in (("ref", "ref"), ("label", "label")):
        if source.get(key):
            facts.append(f"{label} {source[key]}")
    for key, label in (("sha", "sha"), ("host_sha", "commit")):
        if source.get(key):
            facts.append(f"{label} {str(source[key])[:7]}")
    if source.get("dirty"):
        facts.append("dirty")
    if report.get("strict"):
        facts.append("strict")
    lines = [f"{report.get('project')} — {' · '.join(facts)}",
             _CHECK_ROW.format("stage", "status", "pass", "fail", "skip",
                               "error", "total", "time")]
    for stage in report.get("stages") or []:
        summary = stage.get("summary") or {}
        row = _CHECK_ROW.format(
            str(stage.get("name")), str(stage.get("status")),
            summary.get("passed", 0), summary.get("failed", 0),
            summary.get("skipped", 0), summary.get("errors", 0),
            summary.get("total", 0),
            f"{float(stage.get('duration_s') or 0.0):.1f} s")
        if stage.get("reason"):
            row += f"  ({stage['reason']})"
        lines.append(row)

    broken = [item for stage in report.get("stages") or []
              for item in stage.get("items") or []
              if item.get("status") in ("fail", "error")]
    if broken:
        lines.append("failures:")
        lines += _check_named(f"{item.get('id')} — {item.get('message')}"
                              for item in broken)
    if report.get("strict_failures"):
        lines.append(f"strict: {len(report['strict_failures'])} skipped row(s) "
                     f"count as failures")
    if report.get("warnings"):
        lines.append("warnings:")
        lines += _check_named(report["warnings"])
    if report.get("errors"):
        lines.append("harness errors:")
        lines += _check_named(f"{entry.get('type')}: {entry.get('message')}"
                              for entry in report["errors"])
    lines += [f"wrote {path}" for path in written]
    return lines


def _check_named(messages, limit: int = 20) -> list[str]:
    """Bullet lines, capped — a 33-part project with a broken shared import
    would otherwise bury the verdict under its own failures."""
    rows = [str(message).splitlines()[0][:200] if str(message) else ""
            for message in messages]
    shown, extra = rows[:limit], max(0, len(rows) - limit)
    lines = [f"  - {row}" for row in shown]
    if extra:
        lines.append(f"  - (+{extra} more — see the report)")
    return lines


def _check_verdict(report: dict) -> str:
    summary = report.get("summary") or {}
    counts = (f"{summary.get('passed', 0)} passed, "
              f"{summary.get('failed', 0)} failed, "
              f"{summary.get('skipped', 0)} skipped, "
              f"{summary.get('errors', 0)} errors "
              f"of {summary.get('total', 0)}")
    tail = "" if report.get("complete", True) else " — INCOMPLETE (budget)"
    return (f"check: {report.get('status')} — {report.get('project')} · "
            f"{counts} in {float(report.get('duration_s') or 0.0):.1f} s "
            f"(exit {report.get('exit_code')}){tail}")


def _print_check(args, report: dict, written: list[str]) -> None:
    import json

    if args.quiet:
        return
    for line in _check_lines(report, written):
        print(line, file=sys.stderr)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_check_verdict(report))


def cmd_check(args) -> int:
    """`agentcad check` — certify a project and answer with an exit code.

    Headless by construction: one service, one warm kernel, the tool registry —
    **no FastAPI app, no port, no chat engine and no API key**. The kernel is
    stopped in a ``finally``, because a crashed run must not leave workers (or,
    with ``--ref``, a registered git worktree) behind.

    The exit code is the API (design Decision 6): ``0`` green · ``1`` red, the
    model is wrong · ``2`` harness, we could not produce a verdict. A blown
    ``--budget`` is 2 **with the partial report written** — evidence beats
    silence — and so is an unwritable report path, an unknown project and any
    unexpected exception. Everything a check *measured* is payload: a failing
    part is a red report, never a traceback.

    Identity is ``ci`` so a run never collides with a human's per-client
    checkout, and so a proposal post classifies as an agent action.

    ``--proposal``/``--auto-proposal`` attach the report to a change proposal,
    where it becomes that proposal's ``checks`` gate. The target is resolved
    *before* the run (a mistyped id must not cost a full rebuild) and the post
    happens *after* the report files are written, from the report exactly as it
    was measured. Refusing to post is exit 2 — except when the project has no
    proposals at all (no git), which is a warning: the check itself still ran.
    """
    from .core import locks
    from .core.checks import CheckRunner
    from .core.model import AppError
    from .core.tools import build_registry

    try:
        stages = _check_stages(args.stages)
    except ValueError as exc:
        print(f"agentcad check: {exc}", file=sys.stderr)
        return 2

    # Setup is INSIDE the mapping: creating the work dir and starting the kernel
    # are as able to fail as the run itself (an unwritable --work-dir, a
    # projects dir that is a file), and a traceback out of here would be process
    # exit 1 — the code reserved for "the model is wrong".
    service = None
    post_to = args.proposal
    try:
        # Absolute, and known before the kernel spawns: `history._run` runs git
        # with cwd set to the project, so a relative work dir would materialize
        # a `--ref` worktree *inside* the project — and the seatbelt profile is
        # fixed at spawn. Resolved here, but NOT created here: `CheckRunner`
        # creates it after `_refuse_overlap` has accepted it, so a work dir
        # inside the project no longer gets made on the way to exit 2. The
        # sandbox grant is a path, not a directory, and does not need it to
        # exist yet.
        work_dir = None
        extra_writable: list[str] = []
        if args.work_dir:
            work_dir = str(Path(args.work_dir).expanduser().resolve())
            extra_writable.append(work_dir)
        # A project given as a path is the CI case (`--project .` on a checkout)
        # and it lives nowhere `_writable_roots` guessed, so the kernel could not
        # write its `.cache/` — every part would fail to build with a
        # PermissionError instead of a verdict. It is known here, before the
        # workers spawn, which is the only moment the seatbelt profile can still
        # be widened.
        if _is_path(args.project):
            extra_writable.append(str(Path(args.project).expanduser().resolve()))

        service = _build_service(
            Path(args.projects_dir or DEFAULT_PROJECTS_DIR),
            extra_writable=extra_writable or None)
        locks.set_client_id("ci")
        registry = build_registry(service)
        project = args.project
        if _is_path(project):
            project = service.open_project(project)["name"]
        # `service.checks` is the tool pack's runner once slice 5 lands; until
        # then (and for a bare service) build one over the same registry.
        runner = getattr(service, "checks", None) or CheckRunner(service, registry)
        if (post_to or args.auto_proposal) and not _can_post(runner):
            # No git, no proposals, nothing to post to. A warning rather than
            # exit 2: the check itself ran and its verdict is honest, and this
            # is exactly the CI-runner case (a checkout has no .history repo).
            _post_note("--proposal/--auto-proposal: this project has no "
                       "proposals (they need git history); the report will "
                       "not be posted")
            post_to, args.auto_proposal = None, False
        elif post_to:
            # Resolved BEFORE the kernel measures anything: a mistyped id or a
            # merged proposal should cost a millisecond, not a full rebuild.
            runner.post_target(project, post_to)
        report = runner.run(
            project, ref=args.ref, stages=stages, strict=args.strict,
            budget_s=args.budget, min_volume=args.min_volume,
            verify_determinism=args.verify_determinism, sha=args.sha,
            ref_label=args.ref_label, work_dir=work_dir)
    except AppError as exc:
        print(f"agentcad check: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        # Tolerant of a partial construction: setup may have failed before
        # there was a kernel to stop, and a kernel that will not stop must not
        # replace the verdict with its own traceback.
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"agentcad check: the kernel did not stop cleanly: "
                      f"{exc}", file=sys.stderr)

    # Everything after the run is under the SAME exit-code mapping as the run
    # itself: writing the report, posting it and printing it can all fail (an
    # unreadable proposals index, an audit append that will not write), and a
    # traceback out of here would exit 1 — the code reserved for "the model is
    # wrong". The report is written first, so a post that fails still leaves
    # the evidence on disk.
    try:
        written = _write_check_outputs(args, report)
        if written is None:
            return 2
        # Posted AFTER the files are written, and from the report exactly as it
        # was measured: the copy on disk and the copy in the proposal are the
        # same document, because a check never edits a verdict it has already
        # produced.
        override = _post_check(runner, project, args, report, post_to)
        _print_check(args, report, written)
    except AppError as exc:
        print(f"agentcad check: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return override if override is not None else int(report.get("exit_code", 2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentcad", description="Agentic-first CAD")
    parser.add_argument("--version", action="version", version=agentcad.__version__)
    # metavar hides the internal `worker` subcommand from usage/help.
    sub = parser.add_subparsers(
        dest="command", metavar="{serve,open,mcp,new,export,check}")

    for name in ("serve", "open"):
        p = sub.add_parser(name, help=f"{name} the AgentCAD server")
        p.add_argument("--port", type=int, default=None)
        p.add_argument("--projects-dir", default=None)
        p.add_argument("--no-open", action="store_true")

    sub.add_parser("mcp", help="run the MCP stdio server")

    # Hidden: kernel worker loop (used by frozen bundles to re-exec themselves).
    sub.add_parser("worker")

    p = sub.add_parser("new", help="create a new project")
    p.add_argument("name")
    p.add_argument("--projects-dir", default=None)

    p = sub.add_parser("export", help="export a part")
    p.add_argument("project", help="project name or path")
    p.add_argument("part")
    p.add_argument("--format", default="step", choices=["step", "stl", "3mf"])
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--projects-dir", default=None)

    p = sub.add_parser(
        "check", help="certify a project: build, assembly, specs, drawings",
        description="Rebuild every part, re-resolve the assembly, evaluate the "
                    "design specs and regenerate the drawings — headless, with "
                    "no server. Exit 0 green, 1 red, 2 harness.")
    p.add_argument("--project", default=".",
                   help="project name or path (default: the current directory)")
    p.add_argument("--projects-dir", default=None)
    p.add_argument("--ref", default=None,
                   help="check this branch/tag/commit instead of the working "
                        "tree (materialized into a throwaway worktree)")
    p.add_argument("--stages", default=None,
                   metavar="build,assembly,specs,drawings",
                   help="comma-separated subset of the stages to run")
    p.add_argument("--report", default=None, metavar="PATH",
                   help="write the JSON report here")
    p.add_argument("--md", default=None, metavar="PATH",
                   help="write the markdown summary here "
                        "($GITHUB_STEP_SUMMARY, a PR comment)")
    p.add_argument("--strict", action="store_true",
                   help="count skipped rows as failures (rows keep their "
                        "status; only the verdict moves). A row marked "
                        "strict_exempt — an unconditional skip, today only the "
                        "DXF determinism row — is never counted")
    p.add_argument("--verify-determinism", action="store_true",
                   help="build every part a second time on a cold cache and "
                        "compare the artefacts byte for byte")
    p.add_argument("--budget", default=None, metavar="SECONDS",
                   type=_finite_arg("--budget",
                                    "a NaN deadline is never in the past, so "
                                    "it bounds nothing"),
                   help="deadline read before every item and every kernel "
                        "call; a build (300 s) or drawing (120 s) already in "
                        "flight cannot be preempted, so the worst case is one "
                        "such call")
    p.add_argument("--min-volume", default=0.001, metavar="MM3",
                   type=_finite_arg("--min-volume",
                                    "every comparison with NaN is false, so a "
                                    "real overlap would report green"),
                   help="interference volume below which an overlap is noise")
    p.add_argument("--work-dir", default=None, metavar="DIR",
                   help="where --ref materializes its worktree, in a unique "
                        "subdirectory it creates and cleans up (default: a "
                        "temp dir, deleted afterwards). It may not be, hold or "
                        "sit inside the project")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--proposal", default=None, metavar="ID",
                       help="post the report to this proposal; it becomes that "
                            "proposal's checks gate")
    group.add_argument("--auto-proposal", action="store_true",
                       help="post to the one active proposal whose source is "
                            "the branch that was checked (none: a warning; "
                            "more than one: exit 2)")
    p.add_argument("--sha", default=None,
                   help="provenance: the host VCS commit this run measured")
    p.add_argument("--ref-label", default=None,
                   help="provenance: the host VCS ref name (e.g. $GITHUB_REF_NAME)")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--quiet", action="store_true",
                     help="print nothing; the exit code is the answer")
    out.add_argument("--json", action="store_true",
                     help="print the report to stdout instead of the summary")

    args = parser.parse_args()
    if args.command in ("serve", "open"):
        cmd_serve(args, open_browser=args.command == "open")
    elif args.command == "mcp":
        cmd_mcp(args)
    elif args.command == "worker":
        cmd_worker(args)
    elif args.command == "new":
        cmd_new(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "check":
        raise SystemExit(cmd_check(args))
    else:
        parser.print_help()
