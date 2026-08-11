"""The geometry CI action and the dogfood workflow (PRD-004, slice 7).

There is no runner here, so what these tests pin is everything about the action
that can drift *without* a red build telling anyone:

* **The command it runs is the command the CLI has.** Every long flag in the
  check step is asserted against ``agentcad check --help``, because a renamed
  flag would only surface on a runner, and only as exit 2.
* **``--ref`` is never passed** (design Decision 9). ``actions/checkout`` has
  already materialized ``$GITHUB_SHA`` into the working tree and a runner has
  no AgentCAD ``.history/`` repo to resolve it against, so the SHA is
  provenance — ``--sha``/``--ref-label`` — and ``--ref`` would exit 2 on every
  single run.
* **A red check still leaves evidence.** The check step swallows the exit code,
  the summary and the artifact steps carry ``if: always()``, and a later step
  re-raises the saved code. Ordering *and* the conditions are asserted.
* **The OCCT package list is the one ``ci.yml`` proves.** That list is hard-won;
  the two files are compared token for token.
* **The workflow is fork-safe**: ``pull_request``, never
  ``pull_request_target``, and no ``secrets.`` reference anywhere in it.

The shell bodies are executed for real (with ``bash``) rather than pattern
matched, so a quoting bug in the input plumbing fails here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agentcad.core.checks import STAGES

REPO = Path(__file__).resolve().parent.parent
ACTION_DIR = REPO / ".github/actions/agentcad-check"
ACTION = ACTION_DIR / "action.yml"
WORKFLOW = REPO / ".github/workflows/geometry-ci.yml"
CI = REPO / ".github/workflows/ci.yml"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(action: dict) -> list[dict]:
    return action["runs"]["steps"]


def _step(action: dict, key: str) -> dict:
    for step in _steps(action):
        if step.get("id") == key or step.get("name") == key:
            return step
    raise AssertionError(f"no step {key!r} in {[s.get('name') for s in _steps(action)]}")


def _run_body(action: dict, key: str, env: dict[str, str],
              cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Execute one composite step's script the way a runner would."""
    base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "")}
    return subprocess.run(["bash", "-c", _step(action, key)["run"]],
                          cwd=str(cwd or REPO), env={**base, **env},
                          capture_output=True, text=True, timeout=120)


def _outputs(path: Path) -> dict[str, str]:
    got: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            got[key] = value
    return got


# --------------------------------------------------------------------------
# shape


def test_action_is_a_composite_action_with_the_documented_surface():
    action = _load(ACTION)
    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) == {
        "project", "projects-dir", "stages", "strict", "budget",
        "verify-determinism", "proposal", "auto-proposal", "report-json",
        "report-md", "pool-size", "agentcad", "python-version", "fem",
        "upload-artifacts", "artifact-name", "github-token",
    }
    assert set(action["outputs"]) == {
        "status", "exit-code", "report-json", "report-md", "failed-stages",
    }
    for name, spec in action["inputs"].items():
        assert spec.get("description"), name
        assert "default" in spec, name
    readme = (ACTION_DIR / "README.md").read_text(encoding="utf-8")
    for name in {**action["inputs"], **action["outputs"]}:
        assert f"`{name}`" in readme, f"{name} is undocumented"


def test_stages_default_is_the_modules_own_stage_tuple():
    # A stage added to core.checks must reach the action, not be silently
    # excluded by a stale default.
    assert _load(ACTION)["inputs"]["stages"]["default"] == ",".join(STAGES)


def test_workflow_parses_and_dogfoods_the_local_action():
    workflow = _load(WORKFLOW)
    # PyYAML resolves the bare `on:` key to True (the Norway problem's cousin).
    triggers = workflow.get("on") or workflow.get(True)
    assert set(triggers) == {"push", "pull_request", "schedule", "workflow_dispatch"}
    # A fork's part scripts are arbitrary Python on an unconfined Linux runner:
    # no elevated trigger and no secret may reach them.
    code = "\n".join(line for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))
    assert "pull_request_target" not in code
    assert "secrets." not in code
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert set(jobs) == {"examples", "engine"}
    for job in jobs.values():
        assert job["runs-on"] == "ubuntu-latest"  # v1 is Linux-only by design
        uses = [s for s in job["steps"] if s.get("uses", "").startswith("./")]
        assert [s["uses"] for s in uses] == ["./.github/actions/agentcad-check"]
        assert uses[0]["with"]["agentcad"] == "."  # the checked-out source

    examples = jobs["examples"]["strategy"]["matrix"]["example"]
    assert examples == ["construction", "prototyping", "rocketry", "fasteners"]
    for name in [*examples, "engine"]:
        assert (REPO / "examples" / name / "project.json").is_file(), name
    # The engine example is minutes of kernel time: nightly only, like ci.yml.
    assert "schedule" in jobs["engine"]["if"]
    assert "examples/engine" == jobs["engine"]["steps"][-1]["with"]["project"]


def test_occt_system_libraries_match_the_pytest_workflow():
    def packages(text: str) -> set[str]:
        body = text.split("apt-get install -y --no-install-recommends", 1)[1]
        body = body.split("\n\n", 1)[0].replace("\\", " ")
        return {tok for tok in body.split() if tok.startswith("lib")}

    assert packages(ACTION.read_text(encoding="utf-8")) == \
        packages(CI.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# the command, and the exit-code contract


def _check_step_flags() -> set[str]:
    body = _step(_load(ACTION), "check")["run"]
    args = "\n".join(line for line in body.splitlines() if "args" in line)
    return set(re.findall(r"--[a-z][a-z0-9-]*", args))


@pytest.mark.integration
def test_every_flag_the_action_passes_exists_on_the_cli():
    script = Path(sys.executable).with_name("agentcad")
    argv = ([str(script)] if script.exists()
            else [sys.executable, "-c", "from agentcad.cli import main; main()"])
    help_text = subprocess.run(argv + ["check", "--help"], capture_output=True,
                               text=True, timeout=120).stdout
    flags = _check_step_flags()
    assert flags, "the check step passes no flags at all"
    for flag in flags:
        assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text), \
            f"{flag} is not a flag of `agentcad check`"


def test_the_action_passes_the_sha_as_provenance_and_never_as_a_ref():
    flags = _check_step_flags()
    assert {"--sha", "--ref-label"} <= flags
    # Decision 9: a runner checkout has no .history repo to resolve a ref in.
    assert "--ref" not in flags


def test_a_failing_check_still_writes_its_summary_and_artifact():
    action = _load(ACTION)
    names = [s.get("id") or s.get("name") for s in _steps(action)]
    check = names.index("check")
    summary = names.index("Write the job summary")
    upload = names.index("Upload the report")
    reraise = names.index("Re-raise the check's exit code")
    assert check < summary < upload < reraise

    assert "continue-on-error" not in _step(action, "check")
    assert "exit-code=$code" in _step(action, "check")["run"]
    for key in ("Write the job summary", "Upload the report",
                "Re-raise the check's exit code"):
        assert _step(action, key)["if"].startswith("always()"), key
    # The job's verdict comes from the saved code, not from the check step.
    assert "steps.check.outputs.exit-code != '0'" in \
        _step(action, "Re-raise the check's exit code")["if"]


@needs_bash
def test_the_shell_bodies_are_syntactically_valid():
    for step in _steps(_load(ACTION)):
        if "run" not in step:
            continue
        proc = subprocess.run(["bash", "-n"], input=step["run"], text=True,
                              capture_output=True, timeout=60)
        assert proc.returncode == 0, (step.get("name"), proc.stderr)


# --------------------------------------------------------------------------
# the input plumbing, executed


@needs_bash
@pytest.mark.integration
def test_the_plan_step_resolves_paths_the_requirement_and_the_artifact_name(tmp_path):
    out = tmp_path / "out"
    out.write_text("")
    proc = _run_body(_load(ACTION), "plan", {
        "GITHUB_OUTPUT": str(out), "RUNNER_TEMP": str(tmp_path),
        "RUNNER_OS_LOWER": "Linux", "INPUT_PROJECT": "examples/construction",
        "INPUT_REPORT_JSON": "", "INPUT_REPORT_MD": "", "INPUT_ARTIFACT_NAME": "",
        "INPUT_AGENTCAD": ".", "INPUT_FEM": "false", "INPUT_PROPOSAL": "",
        "INPUT_AUTO_PROPOSAL": "false", "INPUT_GITHUB_TOKEN": "",
    })
    assert proc.returncode == 0, proc.stderr
    got = _outputs(out)
    assert got["report-json"] == str(tmp_path / "agentcad-check/report.json")
    assert got["report-md"] == str(tmp_path / "agentcad-check/report.md")
    assert got["requirement"] == "."
    # Unique per OS and project, so two matrix jobs never collide on
    # upload-artifact@v4's "an artifact with this name already exists".
    assert got["artifact-name"] == "agentcad-check-linux-examples-construction"


@needs_bash
@pytest.mark.integration
@pytest.mark.parametrize("requirement,expected", [
    (".", ".[fem]"),
    ("agentcad", "agentcad[fem]"),
    ("agentcad==1.2", "agentcad[fem]==1.2"),
    ("agentcad[all]", "agentcad[all]"),  # the caller already named its extras
])
def test_the_fem_extra_is_spliced_before_any_version_specifier(
        tmp_path, requirement, expected):
    out = tmp_path / "out"
    out.write_text("")
    proc = _run_body(_load(ACTION), "plan", {
        "GITHUB_OUTPUT": str(out), "RUNNER_TEMP": str(tmp_path),
        "RUNNER_OS_LOWER": "Linux", "INPUT_PROJECT": ".",
        "INPUT_REPORT_JSON": "", "INPUT_REPORT_MD": "", "INPUT_ARTIFACT_NAME": "",
        "INPUT_AGENTCAD": requirement, "INPUT_FEM": "true", "INPUT_PROPOSAL": "",
        "INPUT_AUTO_PROPOSAL": "false", "INPUT_GITHUB_TOKEN": "",
    })
    assert proc.returncode == 0, proc.stderr
    assert _outputs(out)["requirement"] == expected


@needs_bash
@pytest.mark.integration
def test_proposal_and_auto_proposal_are_refused_before_the_kernel_spawns(tmp_path):
    out = tmp_path / "out"
    out.write_text("")
    proc = _run_body(_load(ACTION), "plan", {
        "GITHUB_OUTPUT": str(out), "RUNNER_TEMP": str(tmp_path),
        "RUNNER_OS_LOWER": "Linux", "INPUT_PROJECT": ".",
        "INPUT_REPORT_JSON": "", "INPUT_REPORT_MD": "", "INPUT_ARTIFACT_NAME": "",
        "INPUT_AGENTCAD": "agentcad", "INPUT_FEM": "false",
        "INPUT_PROPOSAL": "3", "INPUT_AUTO_PROPOSAL": "true",
        "INPUT_GITHUB_TOKEN": "",
    })
    assert proc.returncode == 2
    assert "mutually exclusive" in proc.stderr


@needs_bash
@pytest.mark.integration
def test_the_summary_step_says_so_when_there_is_no_report(tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("")
    proc = _run_body(_load(ACTION), "Write the job summary", {
        "GITHUB_STEP_SUMMARY": str(summary),
        "REPORT_MD": str(tmp_path / "absent.md"), "EXIT_CODE": "2",
    })
    assert proc.returncode == 0, proc.stderr
    assert "No markdown summary" in summary.read_text(encoding="utf-8")

    written = tmp_path / "report.md"
    written.write_text("# Geometry CI — `demo` — **red**\n")
    summary.write_text("")
    proc = _run_body(_load(ACTION), "Write the job summary", {
        "GITHUB_STEP_SUMMARY": str(summary), "REPORT_MD": str(written),
        "EXIT_CODE": "1",
    })
    assert proc.returncode == 0, proc.stderr
    assert summary.read_text(encoding="utf-8") == written.read_text(encoding="utf-8")


@needs_bash
@pytest.mark.integration
@pytest.mark.parametrize("code", ["1", "2"])
def test_the_saved_exit_code_is_re_raised(tmp_path, code):
    proc = _run_body(_load(ACTION), "Re-raise the check's exit code", {
        "EXIT_CODE": code, "STATUS": "red", "FAILED_STAGES": "build,specs",
    })
    assert proc.returncode == int(code)
    assert "::error::" in proc.stdout


# --------------------------------------------------------------------------
# report -> step outputs


def _report_outputs(report_path: Path, out: Path) -> dict[str, str]:
    proc = subprocess.run([sys.executable,
                           str(ACTION_DIR / "report_outputs.py"), str(report_path)],
                          env={**os.environ, "GITHUB_OUTPUT": str(out)},
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return _outputs(out)


def test_report_outputs_names_the_red_stages(tmp_path):
    report = tmp_path / "report.json"
    report.write_text('{"status": "red", "stages": ['
                      '{"name": "build", "status": "red"},'
                      '{"name": "assembly", "status": "green"},'
                      '{"name": "specs", "status": "skip"},'
                      '{"name": "drawings", "status": "red"}]}')
    out = tmp_path / "out"
    out.write_text("")
    assert _report_outputs(report, out) == {
        "status": "red", "failed-stages": "build,drawings"}


@pytest.mark.parametrize("body", ["", "not json at all", "[]"])
def test_report_outputs_is_silent_about_an_unreadable_report(tmp_path, body):
    # A missing verdict is information, not a second failure: the check's own
    # exit code was saved before this ran and stays the answer.
    report = tmp_path / "report.json"
    report.write_text(body)
    out = tmp_path / "out"
    out.write_text("")
    assert _report_outputs(report, out) == {"status": "", "failed-stages": ""}


def test_report_outputs_survives_a_missing_report(tmp_path):
    out = tmp_path / "out"
    out.write_text("")
    assert _report_outputs(tmp_path / "nope.json", out) == {
        "status": "", "failed-stages": ""}
