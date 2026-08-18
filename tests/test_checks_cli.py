"""``agentcad check`` — the headless CLI (PRD-004, slice 4).

Slices 1–3 built the report, the pipeline and the ref containment; this is the
surface a human and a CI runner actually touch, so what is pinned here is the
*contract at the process boundary*, not the measurement:

* **The exit code is the API** (AC5). ``0`` green · ``1`` red — the model is
  wrong · ``2`` harness — we could not produce a verdict. All three are
  asserted through a real subprocess, because an exit code is the one thing a
  unit test cannot honestly stand in for.
* **A partial report is evidence.** A blown ``--budget`` exits 2 *with*
  ``report.json`` and ``report.md`` on disk and ``complete: false`` inside.
* **stdout is a contract, stderr is for humans.** ``--json`` puts the report
  alone on stdout (so ``agentcad check --json | jq`` works), ``--quiet`` puts
  nothing there, and neither changes the exit code.
* **``_build_service`` grew one optional parameter and nothing else.** The
  writable roots a default call computes must stay byte-identical, because the
  seatbelt profile is fixed when the kernel spawns and every other command
  depends on that list.

The examples are copied and *renamed* before they are checked: the CLI
registers every bundled example at startup, so opening an unrenamed copy by
path would collide with the original by name. Nothing here touches
``examples/`` — the copies drop ``.cache`` and ``exports`` like
``tests/test_examples.py`` does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentcad import cli
from agentcad.core.checks import validate_report

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

# The cheapest bundled example that still exercises every stage: two script
# parts (build + drawings), no instances (assembly skips with a reason).
EXAMPLE = "prototyping"


# --------------------------------------------------------- the subprocess

def _argv() -> list[str]:
    """The real entry point: the console script when the venv has one,
    otherwise ``main()`` through this interpreter (``agentcad.cli`` has no
    ``__main__`` guard, and slice 4 is not allowed to add one)."""
    script = Path(sys.executable).with_name("agentcad")
    if script.exists():
        return [str(script)]
    return [sys.executable, "-c", "from agentcad.cli import main; main()"]


def _cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    child = dict(os.environ)
    child["AGENTCAD_KERNEL_POOL_SIZE"] = "1"  # one worker: CI memory, not cores
    child.update(env or {})
    return subprocess.run(_argv() + list(args), capture_output=True, text=True,
                          timeout=600, env=child)


def _copy_example(dest: Path, name: str, example: str = EXAMPLE) -> Path:
    """A copy of a bundled example, renamed so it cannot collide with the
    original the CLI registers at startup."""
    shutil.copytree(EXAMPLES / example, dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    manifest = json.loads((dest / "project.json").read_text())
    manifest["name"] = name
    (dest / "project.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return dest


def _stage(report: dict, name: str) -> dict:
    return next(s for s in report["stages"] if s["name"] == name)


# ------------------------------------------------------------- the fixtures

@pytest.fixture(scope="module")
def green(tmp_path_factory) -> SimpleNamespace:
    """One green run over a renamed copy, with both output files.

    Module-scoped on purpose: the project's ``.cache/`` stays warm, so the
    later runs against the same copy pay one kernel start and no rebuilds.
    """
    root = tmp_path_factory.mktemp("check_cli_green")
    proj = _copy_example(root / "green", "cli_green")
    report, md = root / "report.json", root / "report.md"
    res = _cli("check", "--project", str(proj),
               "--projects-dir", str(root / "projects"),
               "--report", str(report), "--md", str(md))
    return SimpleNamespace(res=res, report=report, md=md, proj=proj, root=root)


@pytest.fixture(scope="module")
def broken(tmp_path_factory) -> SimpleNamespace:
    """The same example with one part script broken at a known line."""
    root = tmp_path_factory.mktemp("check_cli_broken")
    proj = _copy_example(root / "broken", "cli_broken")
    script = proj / "parts" / "enclosure_lid.py"
    lines = script.read_text().splitlines()
    lines.insert(0, "this is not python")
    script.write_text("\n".join(lines) + "\n")
    report, md = root / "report.json", root / "report.md"
    res = _cli("check", "--project", str(proj),
               "--projects-dir", str(root / "projects"),
               "--report", str(report), "--md", str(md))
    return SimpleNamespace(res=res, report=report, md=md, proj=proj, root=root)


# ------------------------------------------------- AC5: the three exit codes

@pytest.mark.slow
def test_green_project_exits_zero(green):
    assert green.res.returncode == 0, green.res.stderr
    report = json.loads(green.report.read_text())
    assert report["status"] == "green"
    assert report["exit_code"] == 0
    assert report["complete"] is True


@pytest.mark.slow
def test_broken_project_exits_one(broken):
    assert broken.res.returncode == 1, broken.res.stderr
    report = json.loads(broken.report.read_text())
    assert report["status"] == "red"
    assert report["exit_code"] == 1
    # Exit 1 is "the model is wrong", so the run is complete: we produced a
    # verdict, and it is red.
    assert report["complete"] is True


@pytest.mark.slow
def test_unknown_project_exits_two(tmp_path):
    res = _cli("check", "--project", "no_such_project",
               "--projects-dir", str(tmp_path / "projects"))
    assert res.returncode == 2, res.stdout + res.stderr
    assert "no_such_project" in res.stderr


# ------------------------------------------------------- the written outputs

@pytest.mark.slow
def test_report_and_markdown_are_written_and_valid(green):
    report = json.loads(green.report.read_text())
    assert validate_report(report) == []
    md = green.md.read_text()
    assert md.startswith("# Geometry CI")
    assert "cli_green" in md
    assert "| Stage |" in md


@pytest.mark.slow
def test_markdown_names_the_failing_item(broken):
    md = broken.md.read_text()
    assert "## Failures" in md
    assert "enclosure_lid" in md
    report = json.loads(broken.report.read_text())
    item = next(i for i in _stage(report, "build")["items"]
                if i["status"] in ("fail", "error"))
    assert item["error"]["details"].get("line")
    # The human summary names what failed, not just how many.
    assert "enclosure_lid" in broken.res.stderr


# ------------------------------------------------------------ output modes

@pytest.mark.slow
def test_json_mode_puts_the_report_alone_on_stdout(green):
    res = _cli("check", "--project", str(green.proj),
               "--projects-dir", str(green.root / "projects"),
               "--stages", "build", "--json")
    assert res.returncode == 0, res.stderr
    report = json.loads(res.stdout)          # stdout is JSON and nothing else
    assert validate_report(report) == []
    assert _stage(report, "build")["status"] == "green"
    for name in ("assembly", "specs", "drawings"):
        assert _stage(report, name)["status"] == "skip"
        assert _stage(report, name)["reason"] == "not_selected"


@pytest.mark.slow
def test_quiet_mode_prints_nothing_and_keeps_the_exit_code(green):
    res = _cli("check", "--project", str(green.proj),
               "--projects-dir", str(green.root / "projects"),
               "--stages", "build", "--quiet")
    assert res.returncode == 0, res.stderr
    assert res.stdout == ""
    assert res.stderr == ""


@pytest.mark.slow
def test_human_summary_names_the_stages_and_the_verdict(green):
    assert "build" in green.res.stderr
    assert "green" in green.res.stdout


# ------------------------------------------------------------- the harness

def test_unknown_stage_exits_two_naming_the_valid_stages(tmp_path):
    res = _cli("check", "--project", "whatever", "--stages", "build,bogus",
               "--projects-dir", str(tmp_path / "projects"))
    assert res.returncode == 2, res.stdout + res.stderr
    assert "bogus" in res.stderr
    for name in ("build", "assembly", "specs", "drawings"):
        assert name in res.stderr
    # Refused before anything expensive: no kernel, so no project lookup.
    assert "whatever" not in res.stderr


@pytest.mark.slow
def test_blown_budget_exits_two_with_a_partial_report_on_disk(green):
    report = green.root / "partial.json"
    md = green.root / "partial.md"
    res = _cli("check", "--project", str(green.proj),
               "--projects-dir", str(green.root / "projects"),
               "--budget", "0.001", "--report", str(report), "--md", str(md))
    assert res.returncode == 2, res.stdout + res.stderr
    assert report.is_file() and md.is_file()
    partial = json.loads(report.read_text())
    assert partial["complete"] is False
    assert partial["exit_code"] == 2
    assert validate_report(partial) == []
    assert any(item["reason"] == "budget_exceeded"
               for stage in partial["stages"] for item in stage["items"])


@pytest.mark.slow
def test_a_refused_work_dir_is_never_created(tmp_path):
    """The overlap refusal (review W1) lives in ``CheckRunner._work_dir``, but
    the CLI used to ``mkdir`` the path *before* the runner ever saw it — so
    ``--work-dir <project>/scratch`` created ``scratch`` inside the user's
    project and only then exited 2. Nothing was deleted, but the promise the
    changelog made ("a refused path leaves nothing behind") was false on the
    surface most people use.

    Creating the directory is the runner's job, after it has accepted it.
    """
    proj = _copy_example(tmp_path / "refused", "cli_refused")
    inside = proj / "scratch"

    res = _cli("check", "--project", str(proj), "--stages", "build",
               "--projects-dir", str(tmp_path / "projects"),
               "--work-dir", str(inside), "--quiet")

    assert res.returncode == 2, res.stdout + res.stderr
    assert "overlaps" in res.stderr and str(inside) in res.stderr
    assert not inside.exists(), "the refused work dir was created anyway"


@pytest.mark.slow
@pytest.mark.portability
def test_a_project_outside_the_usual_roots_is_still_writable(tmp_path):
    """`--project .` on a checkout is *the* CI shape, and the checkout is
    nowhere `_writable_roots` guessed. The seatbelt profile is fixed when the
    workers spawn, so the project path has to be granted before that — or every
    part fails to build with a PermissionError writing `.cache/` instead of
    producing a verdict. TMPDIR is redirected here so the project is genuinely
    outside the system temp dir the profile always allows.
    """
    proj = _copy_example(tmp_path / "outside", "cli_outside")
    fake_tmp = tmp_path / "tmpdir"
    fake_tmp.mkdir()
    res = _cli("check", "--project", str(proj), "--stages", "build",
               "--projects-dir", str(tmp_path / "projects"),
               env={"TMPDIR": str(fake_tmp)})
    assert res.returncode == 0, res.stdout + res.stderr
    assert "PermissionError" not in res.stderr
    assert (proj / ".cache").is_dir()


# ------------------------------- the wiring, without paying for a kernel

def _report(**overrides) -> dict:
    """A minimal, valid report — enough to exercise the CLI's own plumbing."""
    from agentcad.core.checks import finalize_report, make_item, make_stage

    items = [make_item("build", "part", "widget", overrides.pop("row", "pass"),
                       "…")]
    report = finalize_report(
        "p", [make_stage("build", items)], source={"kind": "worktree"},
        host={"platform": "test"}, started="2026-01-01T00:00:00Z",
        complete=overrides.pop("complete", True))
    report.update(overrides)
    return report


class _Runner:
    def __init__(self, report=None, raises=None):
        self.report, self.raises, self.seen = report, raises, {}

    def run(self, proj, **kwargs):
        self.seen = dict(kwargs, project=proj)
        # The real `CheckRunner._work_dir` creates the work dir itself, once
        # `_refuse_overlap` has accepted it — the CLI no longer pre-creates it
        # (F3). A fake that skipped the mkdir would make an unwritable
        # `--work-dir` look like it worked.
        if kwargs.get("work_dir"):
            Path(kwargs["work_dir"]).mkdir(parents=True, exist_ok=True)
        if self.raises is not None:
            raise self.raises
        return self.report


@pytest.fixture
def wired(monkeypatch):
    """`_build_service` and the registry stubbed out: this exercises the CLI,
    not the pipeline, so it costs no kernel and no build."""
    state = SimpleNamespace(runner=_Runner(_report()), stopped=False,
                            extra_writable=None, projects_dir=None)
    service = SimpleNamespace(
        kernel=SimpleNamespace(
            stop=lambda: setattr(state, "stopped", True)),
        checks=state.runner,
        open_project=lambda path: {"name": "wired"})

    def _build(projects_dir, extra_writable=None):
        state.projects_dir = projects_dir
        state.extra_writable = extra_writable
        return service

    monkeypatch.setattr(cli, "_build_service", _build)
    monkeypatch.setattr("agentcad.core.tools.build_registry",
                        lambda svc: object())
    return state


def test_main_wires_check_and_passes_every_flag(wired, monkeypatch, tmp_path):
    argv = ["agentcad", "check",
            "--project", str(tmp_path / "proj"),
            "--projects-dir", str(tmp_path / "projects"),
            "--ref", "feat/nozzle", "--stages", "build,specs", "--strict",
            "--verify-determinism", "--budget", "12.5", "--min-volume", "0.5",
            "--work-dir", str(tmp_path / "wd"), "--sha", "deadbeef",
            "--ref-label", "refs/heads/feat/nozzle",
            "--report", str(tmp_path / "r.json"), "--md", str(tmp_path / "r.md"),
            "--quiet"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    seen = wired.runner.seen
    assert exit_info.value.code == 0
    assert seen["project"] == "wired"          # the path went through open_project
    assert seen["ref"] == "feat/nozzle"
    assert seen["stages"] == ("build", "specs")
    assert seen["strict"] is True and seen["verify_determinism"] is True
    assert seen["budget_s"] == 12.5 and seen["min_volume"] == 0.5
    assert seen["sha"] == "deadbeef"
    assert seen["ref_label"] == "refs/heads/feat/nozzle"
    # The work dir is absolute (a relative one would land inside the project
    # when git materializes a ref) and granted to the sandbox before the spawn.
    work_dir = str((tmp_path / "wd").resolve())
    assert seen["work_dir"] == work_dir
    assert work_dir in wired.extra_writable
    assert str((tmp_path / "proj").resolve()) in wired.extra_writable
    assert wired.stopped is True
    assert (tmp_path / "r.json").is_file() and (tmp_path / "r.md").is_file()


def _args(**overrides):
    from argparse import Namespace

    defaults = dict(project="p", projects_dir=None, ref=None, stages=None,
                    report=None, md=None, strict=False,
                    verify_determinism=False, budget=None, min_volume=0.001,
                    work_dir=None, proposal=None, auto_proposal=False,
                    sha=None, ref_label=None, quiet=True, json=False)
    defaults.update(overrides)
    return Namespace(**defaults)


@pytest.mark.parametrize("report,expected", [
    (_report(), 0),
    (_report(row="fail"), 1),
    (_report(row="error"), 1),
    (_report(complete=False), 2),
])
def test_exit_code_is_the_reports_verdict(wired, report, expected):
    wired.runner.report = report
    assert cli.cmd_check(_args()) == expected


def test_a_harness_exception_is_exit_two_not_a_traceback(wired):
    from agentcad.core.model import NotFoundError

    wired.runner.raises = NotFoundError("no project 'ghost'")
    assert cli.cmd_check(_args(project="ghost")) == 2
    assert wired.stopped is True


def test_an_unwritable_report_path_is_exit_two(wired, tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    assert cli.cmd_check(_args(report=str(blocked / "report.json"))) == 2


def test_a_partial_report_is_still_written_before_exit_two(wired, tmp_path):
    wired.runner.report = _report(complete=False)
    report = tmp_path / "partial.json"
    assert cli.cmd_check(_args(report=str(report))) == 2
    assert json.loads(report.read_text())["complete"] is False


@pytest.mark.parametrize("failure", ["app", "bare"])
def test_a_post_run_failure_is_exit_two_with_a_message_not_a_traceback(
        wired, capsys, failure):
    """Review W5: ``_write_check_outputs``/``_post_check``/``_print_check`` ran
    *after* the try/except that maps a harness failure to exit 2, so anything
    they raised — ``matching_proposals`` over a mangled proposals index, an
    audit append that will not write — left a traceback and exit **1**, which
    is reserved for "the model is wrong"."""
    from agentcad.core.model import ValidationError

    exc = (ValidationError("the proposals index.json could not be read")
           if failure == "app"
           else OSError("proposals/index.json is not a file"))

    def boom(project, report):
        raise exc

    wired.runner.can_post = lambda: True
    wired.runner.matching_proposals = boom

    assert cli.cmd_check(_args(auto_proposal=True)) == 2

    err = capsys.readouterr().err
    assert "index.json" in err and "agentcad check" in err


@pytest.mark.portability
@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="needs an unwritable directory")
def test_an_unwritable_work_dir_is_exit_two_not_a_traceback(wired, tmp_path,
                                                             capsys):
    """Review C8: the work-dir ``mkdir`` and ``_build_service`` ran **before**
    the try/except that maps a harness failure to exit 2, so a ``--work-dir``
    the user cannot create escaped as a traceback and process exit **1** — the
    code reserved for "the model is wrong", which automation reads as red
    geometry.

    Since F3 the ``mkdir`` is the runner's, inside ``run()`` — later, but under
    the same mapping, so the contract is unchanged: exit 2 and a named message.
    The kernel is up by then, and the ``finally`` stops it.
    """
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        assert cli.cmd_check(_args(work_dir=str(blocked / "wd"))) == 2
    finally:
        blocked.chmod(0o700)

    err = capsys.readouterr().err
    assert "agentcad check" in err and "PermissionError" in err
    # The failure is now inside the run, so the kernel was up — and stopped.
    assert wired.stopped is True


def test_a_failure_after_the_kernel_starts_does_not_leak_workers(fake_kernel,
                                                                  tmp_path,
                                                                  monkeypatch):
    """The other half of C8: ``_build_service`` starts the pool and *then*
    constructs the service and registers the examples. Anything that raises in
    between used to leave the workers running — every one of them a process
    holding ~0.5 GB."""
    def boom(*args, **kwargs):
        raise RuntimeError("the projects dir is not a directory")

    monkeypatch.setattr("agentcad.core.service.AgentCADService", boom)

    with pytest.raises(RuntimeError):
        cli._build_service(tmp_path / "projects")

    assert fake_kernel.stopped == [True], "the kernel pool was left running"


@pytest.mark.parametrize("flag,value", [
    ("--budget", "nan"), ("--budget", "inf"), ("--budget", "-1"),
    ("--min-volume", "nan"), ("--min-volume", "-inf"),
])
def test_a_non_finite_budget_or_min_volume_exits_two_at_the_parser(tmp_path,
                                                                    flag,
                                                                    value):
    """Review C9: ``argparse``'s ``type=float`` happily returns ``nan``, and
    every comparison with NaN is false — a NaN ``--budget`` switches the
    deadline off and a NaN ``--min-volume`` makes a real overlap report green.
    Refused at the parser, before the kernel spawns."""
    # `--flag=value`, because argparse reads a bare `-inf` as an option.
    res = _cli("check", "--project", "whatever", f"{flag}={value}",
               "--projects-dir", str(tmp_path / "projects"))

    assert res.returncode == 2, res.stdout + res.stderr
    assert flag in res.stderr and "finite" in res.stderr
    assert "Traceback" not in res.stderr


def test_proposal_flags_are_accepted_and_warn_until_slice_six(wired, capsys):
    assert cli.cmd_check(_args(proposal="pr-1")) == 0
    assert "--proposal" in capsys.readouterr().err


# ------------------------------------------ the one sanctioned service change

class _FakeKernel:
    """Stands in for KernelClient/KernelPool: records what it was told it may
    write to, whether it was ever stopped, and starts nothing."""

    seen: list[list[str]] = []
    stopped: list[bool] = []

    def __init__(self, *, writable_dirs=None, **kwargs):
        self.writable_dirs = list(writable_dirs or [])
        _FakeKernel.seen.append(self.writable_dirs)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        _FakeKernel.stopped.append(True)


@pytest.fixture
def fake_kernel(monkeypatch):
    import agentcad.kernel.client as client_mod
    import agentcad.kernel.pool as pool_mod

    _FakeKernel.seen = []
    _FakeKernel.stopped = []
    monkeypatch.setattr(client_mod, "KernelClient", _FakeKernel)
    monkeypatch.setattr(pool_mod, "KernelPool", _FakeKernel)
    return _FakeKernel


def test_build_service_without_extra_writable_is_unchanged(fake_kernel,
                                                           tmp_path):
    projects = tmp_path / "projects"
    service = cli._build_service(projects)
    try:
        assert service.kernel.writable_dirs == (
            cli._writable_roots(projects) + [str(service.work_root)])
    finally:
        cli._release_work_root(service)


def test_build_service_appends_extra_writable_roots(fake_kernel, tmp_path):
    projects = tmp_path / "projects"
    work = tmp_path / "work"
    service = cli._build_service(projects, extra_writable=[str(work)])
    try:
        assert service.kernel.writable_dirs == (
            cli._writable_roots(projects)
            + [str(service.work_root), str(work)])
    finally:
        cli._release_work_root(service)


# ------------------------------------- PRD-006: the work root, not bare temp


def test_the_granted_roots_are_the_work_root_and_never_the_shared_temp_dir(
        fake_kernel, tmp_path):
    """Decision 1. Granting `tempfile.gettempdir()` gave every worker read and
    write access to every other worker's scratch — the exact thing the private
    per-worker dir exists to prevent. The one shared scratch a *run* still
    needs is this server's own `agentcad-work-*` directory, granted by name.
    """
    service = cli._build_service(tmp_path / "projects")
    try:
        roots = service.kernel.writable_dirs
        assert tempfile.gettempdir() not in roots
        assert str(service.work_root) in roots
        assert Path(service.work_root).is_dir()
        assert Path(service.work_root).name.startswith("agentcad-work-")
    finally:
        cli._release_work_root(service)


def test_the_work_root_is_removed_with_the_service(fake_kernel, tmp_path):
    service = cli._build_service(tmp_path / "projects")
    root = Path(service.work_root)

    cli._release_work_root(service)

    assert not root.exists()
    cli._release_work_root(service)          # idempotent
    # ...and a service that never had one is not an AttributeError.
    cli._release_work_root(SimpleNamespace())


def test_a_named_work_dir_is_still_the_callers_and_is_granted(wired, tmp_path):
    """The work root is the *default*, never an override: an explicit
    `--work-dir` is still resolved, still granted to the sandbox before the
    workers spawn, and still handed to the runner untouched."""
    work_dir = tmp_path / "wd"
    assert cli.cmd_check(_args(work_dir=str(work_dir))) == 0

    assert wired.extra_writable == [str(work_dir.resolve())]
    assert wired.runner.seen["work_dir"] == str(work_dir.resolve())
