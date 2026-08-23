"""`agentcad skill new|lint` — the author path.

Run as a real subprocess: the exit code is the API (0 clean, 1 errors, 2
usage) and an in-process call cannot prove it. Every rule lives in
`core/skills_lint.py`; the CLI is deliberately thin.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.core.skills import CORE_DIR

BROKEN = "not a skill file at all\n"


def _argv() -> list[str]:
    script = Path(sys.executable).with_name("agentcad")
    if script.exists():
        return [str(script)]
    return [sys.executable, "-c", "from agentcad.cli import main; main()"]


def _cli(*args: str, env=None) -> subprocess.CompletedProcess:
    environ = dict(os.environ)
    environ.update(env or {})
    return subprocess.run(_argv() + ["skill", *args], capture_output=True,
                          text=True, timeout=300, env=environ)


@pytest.mark.integration
def test_new_then_lint_is_clean(tmp_path):
    made = _cli("new", "my-skill", "--dir", str(tmp_path))
    assert made.returncode == 0, made.stderr
    skill_md = tmp_path / "my-skill" / "SKILL.md"
    assert skill_md.is_file()
    assert str(skill_md) in made.stdout

    linted = _cli("lint", str(tmp_path / "my-skill"))
    assert linted.returncode == 0, linted.stdout + linted.stderr
    assert "0 errors" in linted.stdout


@pytest.mark.integration
def test_new_into_a_project(tmp_path):
    projects = tmp_path / "projects"
    (projects / "widget").mkdir(parents=True)
    (projects / "widget" / "project.json").write_text(
        json.dumps({"name": "widget", "parts": []}), encoding="utf-8")
    made = _cli("new", "house-rules", "--project", "widget",
                env={"AGENTCAD_PROJECTS_DIR": str(projects)})
    assert made.returncode == 0, made.stderr
    assert (projects / "widget" / "skills" / "house-rules" / "SKILL.md").is_file()


@pytest.mark.integration
def test_new_refuses_to_overwrite_and_needs_a_target(tmp_path):
    assert _cli("new", "my-skill", "--dir", str(tmp_path)).returncode == 0
    again = _cli("new", "my-skill", "--dir", str(tmp_path))
    assert again.returncode == 2
    assert "exists" in again.stderr
    assert _cli("new", "my-skill").returncode == 2
    assert _cli("new", "Not A Name", "--dir", str(tmp_path)).returncode == 2
    unknown = _cli("new", "my-skill", "--project", "nope",
                   env={"AGENTCAD_PROJECTS_DIR": str(tmp_path / "none")})
    assert unknown.returncode == 2


@pytest.mark.integration
def test_lint_reports_errors_and_the_row_format(tmp_path):
    broken = tmp_path / "broken-skill"
    broken.mkdir()
    (broken / "SKILL.md").write_text(BROKEN, encoding="utf-8")
    result = _cli("lint", str(broken))
    assert result.returncode == 1
    row = result.stdout.splitlines()[0]
    assert row.startswith(str(broken / "SKILL.md") + ": error frontmatter: ")
    assert "1 errors, 0 warnings" in result.stdout


@pytest.mark.integration
def test_lint_json_and_a_skills_directory(tmp_path):
    root = tmp_path / "skills"
    assert _cli("new", "one-skill", "--dir", str(root)).returncode == 0
    (root / "two-skill").mkdir()
    (root / "two-skill" / "SKILL.md").write_text(BROKEN, encoding="utf-8")
    result = _cli("lint", str(root), "--json")
    assert result.returncode == 1
    rows = json.loads(result.stdout)
    assert [r["code"] for r in rows] == ["frontmatter"]
    assert rows[0]["level"] == "error"


@pytest.mark.integration
def test_lint_profile_changes_severity(tmp_path):
    bare = tmp_path / "bare-skill"
    bare.mkdir()
    (bare / "SKILL.md").write_text(
        "---\nname: bare-skill\ndescription: d\nversion: 1.0.0\n---\nbody\n",
        encoding="utf-8")
    assert _cli("lint", str(bare), "--profile", "user").returncode == 0
    assert _cli("lint", str(bare), "--profile", "library").returncode == 1


@pytest.mark.integration
def test_lint_without_paths_is_usage():
    result = _cli("lint")
    assert result.returncode == 2
    assert "path" in result.stderr


@pytest.mark.integration
def test_lint_core_is_green():
    result = _cli("lint", "--core")
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(CORE_DIR) in result.stdout or "0 errors" in result.stdout


@pytest.mark.integration
def test_skill_without_a_subcommand_is_usage():
    assert _cli().returncode == 2
