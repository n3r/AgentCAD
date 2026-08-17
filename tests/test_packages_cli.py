"""``agentcad package validate`` — the headless CLI (PRD-011, slice 6).

Slices 4–5 built the gate; this is the surface a human and a CI runner touch,
so what is pinned here is the *contract at the process boundary*:

* **The exit code is the API.** ``0`` green · ``1`` red — **the package is
  wrong** · ``2`` harness — we could not produce a verdict. All three are
  asserted through a real subprocess, because an exit code is the one thing a
  unit test cannot honestly stand in for.
* **The report is a document somebody else can read.**
  ``gate.validate_gate_report`` accepts it, and `checks.validate_report` — the
  PRD-004 schema — complains about nothing but the stage-name vocabulary.
* **The non-claim is printed, once, above the verdict.** The gate is a
  correctness gate and not a security boundary, and a CLI that only said so in
  the docs would be a CLI nobody read it in.
* **A `--work-dir` that overlaps the projects root is exit 2 with both paths
  named**, and it is never created on the way there.

The gate itself is stubbed for the plumbing tests (the flags, an unwritable
report, a raising gate): those exercise the CLI, not the pipeline, and cost no
kernel.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentcad import cli
from agentcad.core import checks
from agentcad.core.packages import gate

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "packages"


def _argv() -> list[str]:
    """The real entry point: the console script when the venv has one,
    otherwise `main()` through this interpreter."""
    script = Path(sys.executable).with_name("agentcad")
    if script.exists():
        return [str(script)]
    return [sys.executable, "-c", "from agentcad.cli import main; main()"]


def _cli(*args: str) -> subprocess.CompletedProcess:
    child = dict(os.environ)
    child["AGENTCAD_KERNEL_POOL_SIZE"] = "1"   # one worker: CI memory, not cores
    return subprocess.run(_argv() + list(args), capture_output=True, text=True,
                          timeout=600, env=child)


@pytest.fixture(scope="module")
def green(tmp_path_factory) -> SimpleNamespace:
    root = tmp_path_factory.mktemp("package_cli_green")
    report = root / "report.json"
    res = _cli("package", "validate", str(FIXTURES / "widget_good"),
               "--projects-dir", str(root / "projects"),
               "--report", str(report))
    return SimpleNamespace(res=res, report=report, root=root)


# ------------------------------------------------- the three exit codes


@pytest.mark.slow
def test_the_green_fixture_exits_zero_and_is_publishable(green):
    assert green.res.returncode == 0, green.res.stderr
    report = json.loads(green.report.read_text())
    assert report["status"] == "green"
    assert report["publishable"] is True
    assert report["complete"] is True


@pytest.mark.slow
@pytest.mark.parametrize("fixture,named", [
    ("break_at_extreme", "build:strut@length=max"),
    ("broken_connector", "connectors:bracket"),
])
def test_a_broken_fixture_exits_one_with_the_failing_item_on_stderr(
        tmp_path, fixture, named):
    res = _cli("package", "validate", str(FIXTURES / fixture),
               "--projects-dir", str(tmp_path / "projects"))
    assert res.returncode == 1, res.stdout + res.stderr
    assert "failures:" in res.stderr
    assert named in res.stderr
    assert "publishable: no" in res.stdout


@pytest.mark.slow
def test_a_directory_that_is_not_a_package_exits_two(tmp_path):
    res = _cli("package", "validate", str(tmp_path / "nowhere"),
               "--projects-dir", str(tmp_path / "projects"))
    assert res.returncode == 2, res.stdout + res.stderr
    assert "no package directory" in res.stderr
    assert str(tmp_path / "nowhere") in res.stderr


@pytest.mark.slow
def test_a_work_dir_inside_the_projects_root_exits_two_naming_both_paths(
        tmp_path):
    projects = tmp_path / "projects"
    res = _cli("package", "validate", str(FIXTURES / "widget_good"),
               "--projects-dir", str(projects),
               "--work-dir", str(projects / "cell"))
    assert res.returncode == 2, res.stdout + res.stderr
    assert str(projects.resolve()) in res.stderr
    assert str((projects / "cell").resolve()) in res.stderr
    assert not (projects / "cell").exists()


@pytest.mark.slow
def test_package_with_no_subcommand_exits_two(tmp_path):
    res = _cli("package")
    assert res.returncode == 2
    assert "expected a subcommand" in res.stderr


# ------------------------------------------------------- what it wrote


@pytest.mark.slow
def test_the_written_report_validates(green):
    report = json.loads(green.report.read_text())
    assert gate.validate_gate_report(report) == []
    # …and it is a PRD-004 report but for the stage names, which is the whole
    # claim `validate_gate_report` makes.
    assert all("unknown stage name" in problem
               for problem in checks.validate_report(report))


@pytest.mark.slow
def test_the_human_summary_names_every_stage_and_the_verdict(green):
    err, out = green.res.stderr, green.res.stdout
    for name in gate.GATE_STAGES:
        assert name in err
    assert "widget_good@1.0.0" in err
    assert "not measured (exempt from the publish verdict)" in err
    assert "no_policy_configured" in err
    assert out.strip().startswith("package validate: green")
    assert "publishable: yes" in out


@pytest.mark.slow
def test_the_security_non_claim_is_printed_once_above_the_verdict(green):
    err, out = green.res.stderr, green.res.stdout
    assert (err + out).count("not a security boundary") == 1
    # Last on stderr — immediately above the verdict, which is stdout's only
    # line — rather than buried in the stage table.
    assert err.strip().splitlines()[-1] == gate.SECURITY_NOTE
    assert out.strip().splitlines() == [out.strip()]
    # …and it travels with the evidence, too.
    assert json.loads(green.report.read_text())["note"] == gate.SECURITY_NOTE


# ---------------------------------- the wiring, without paying for a kernel


def _report(**overrides) -> dict:
    from agentcad.core.checks import finalize_report, make_item, make_stage

    items = [make_item("format", "check", "package.json",
                       overrides.pop("row", "pass"), "…")]
    report = finalize_report(
        "widget", [make_stage("format", items)], source={"kind": "worktree"},
        host={"platform": "test"}, started="2026-01-01T00:00:00Z",
        complete=overrides.pop("complete", True))
    report.update({"package": {"name": "widget", "version": "1.0.0",
                               "content_id": "sha256:" + "ab" * 32},
                   "note": gate.SECURITY_NOTE, "publishable": True,
                   "exempt_skips": [], "blockers": []})
    report.update(overrides)
    return report


class _Gate:
    def __init__(self, report=None, raises=None):
        self.report, self.raises, self.seen = report, raises, {}

    def __call__(self, service):
        self.service = service
        return self

    def run(self, path, **kwargs):
        self.seen = dict(kwargs, path=path)
        if kwargs.get("work_dir"):
            # The real `_work_root` creates the dir once `_refuse_overlap` has
            # accepted it; a fake that skipped the mkdir would make an
            # unwritable --work-dir look like it worked.
            Path(kwargs["work_dir"]).mkdir(parents=True, exist_ok=True)
        if self.raises is not None:
            raise self.raises
        return self.report


@pytest.fixture
def wired(monkeypatch):
    state = SimpleNamespace(gate=_Gate(_report()), stopped=False,
                            extra_writable=None, projects_dir=None)
    service = SimpleNamespace(
        kernel=SimpleNamespace(stop=lambda: setattr(state, "stopped", True)))

    def _build(projects_dir, extra_writable=None):
        state.projects_dir = projects_dir
        state.extra_writable = extra_writable
        return service

    monkeypatch.setattr(cli, "_build_service", _build)
    monkeypatch.setattr("agentcad.core.packages.gate.PackageGate", state.gate)
    return state


def _args(**overrides):
    from argparse import Namespace

    defaults = dict(path="pkg", projects_dir=None, strict=False, report=None,
                    work_dir=None, budget=None,
                    package_command="validate")
    defaults.update(overrides)
    return Namespace(**defaults)


def test_main_wires_package_validate_and_passes_every_flag(wired, monkeypatch,
                                                           tmp_path):
    argv = ["agentcad", "package", "validate", str(tmp_path / "pkg"),
            "--projects-dir", str(tmp_path / "projects"),
            "--strict", "--budget", "12.5",
            "--work-dir", str(tmp_path / "wd"),
            "--report", str(tmp_path / "r.json")]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    seen = wired.gate.seen
    assert exit_info.value.code == 0
    assert seen["path"] == str(tmp_path / "pkg")
    assert seen["strict"] is True and "jobs" not in seen
    assert seen["budget_s"] == 12.5
    # Absolute, and granted to the sandbox before the workers spawn.
    work_dir = str((tmp_path / "wd").resolve())
    assert seen["work_dir"] == work_dir
    assert wired.extra_writable == [work_dir]
    assert wired.stopped is True
    assert json.loads((tmp_path / "r.json").read_text())["package"]["name"] \
        == "widget"


def test_without_a_work_dir_the_writable_roots_are_untouched(wired):
    assert cli.cmd_package_validate(_args()) == 0
    assert wired.extra_writable is None


@pytest.mark.parametrize("report,expected", [
    (_report(), 0),
    (_report(row="fail"), 1),
    (_report(row="error"), 1),
    (_report(complete=False), 2),
])
def test_the_exit_code_is_the_reports_verdict(wired, report, expected):
    wired.gate.report = report
    assert cli.cmd_package_validate(_args()) == expected


def test_a_harness_exception_is_exit_two_not_a_traceback(wired, capsys):
    from agentcad.core.model import NotFoundError

    wired.gate.raises = NotFoundError("no package directory at /ghost")
    assert cli.cmd_package_validate(_args(path="/ghost")) == 2
    assert "no package directory" in capsys.readouterr().err


def test_an_unexpected_exception_is_exit_two_and_the_kernel_still_stops(
        wired, capsys):
    wired.gate.raises = RuntimeError("the gate exploded")
    assert cli.cmd_package_validate(_args()) == 2
    assert wired.stopped is True
    assert "RuntimeError" in capsys.readouterr().err


def test_an_unwritable_report_path_is_exit_two(wired, tmp_path):
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        assert cli.cmd_package_validate(
            _args(report=str(blocked / "report.json"))) == 2
    finally:
        blocked.chmod(0o700)


def test_a_partial_report_is_still_written_before_exit_two(wired, tmp_path):
    """A blown budget is exit 2 **with** the evidence on disk."""
    wired.gate.report = _report(complete=False)
    report = tmp_path / "r.json"
    assert cli.cmd_package_validate(_args(report=str(report))) == 2
    assert json.loads(report.read_text())["complete"] is False


@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_a_budget_that_is_not_a_limit_is_refused_at_the_parser(tmp_path, value):
    res = _cli("package", "validate", str(FIXTURES / "widget_good"),
               "--budget", value)
    assert res.returncode == 2
    assert "--budget" in res.stderr


def test_the_jobs_flag_is_gone_from_both_commands(tmp_path):
    """`--jobs` was DELETED, not deprecated (changelog 0181): the fan-out it
    drove missed its pre-registered 1.5x bar three times and broke
    determinism under `--budget`. A flag argparse still accepts is a flag a
    script keeps passing, so both commands must refuse it outright."""
    for command in (("package", "validate", str(FIXTURES / "widget_good")),
                    ("publish", str(FIXTURES / "widget_good"),
                     "--index", "x")):
        res = _cli(*command, "--projects-dir", str(tmp_path / "projects"),
                   "--jobs", "2")
        assert res.returncode == 2
        assert "unrecognized arguments: --jobs" in res.stderr
        assert "Traceback" not in res.stderr


def test_help_lists_package_beside_the_other_commands():
    """The subparser metavar is what `agentcad --help` prints; a command
    missing from it is a command nobody finds."""
    res = _cli("--help")
    assert res.returncode == 0
    # `admin` joined the list in PRD-005a; the assertion is per-command rather
    # than one literal string so the next command to land fails on its own
    # merits (missing from the metavar) instead of on the punctuation.
    for command in ("serve", "open", "mcp", "new", "export", "check",
                    "package", "publish", "admin"):
        assert command in res.stdout, command
    assert "{serve,open,mcp,new,export,check,package,publish,admin}" in res.stdout
