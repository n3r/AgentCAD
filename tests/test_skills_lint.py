"""`core/skills_lint.py` — one fixture per code, both profiles, the scaffold.

Pure: the lint reads the paths it is handed and nothing else. The CLI over the
same functions is `tests/test_skills_cli.py`.
"""

import json
from pathlib import Path

import pytest

from agentcad.core import skills_lint as lint

GOOD = """\
---
name: {name}
description: A good test skill for the lint.
version: 1.0.0
triggers: [alpha, beta]
license: Apache-2.0
author: AgentCAD core
requires: [specs]
---
# Title

Preamble.

## One

```python
x = 1
```
"""


def make_skill(root: Path, name: str = "good-skill", text: str | None = None,
               *, flat: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    body = GOOD.format(name=name) if text is None else text
    if flat:
        path = root / f"{name}.md"
        path.write_text(body, encoding="utf-8")
        return path
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(body, encoding="utf-8")
    return path


def codes(findings, level=None):
    return sorted(f.code for f in findings
                  if level is None or f.level == level)


def level_of(findings, code) -> str:
    return next(f.level for f in findings if f.code == code)


# ------------------------------------------------------------------- clean

def test_a_good_skill_lints_clean_in_both_profiles(tmp_path):
    path = make_skill(tmp_path)
    for profile in lint.PROFILES:
        assert lint.lint_skill(path, profile) == []
        assert lint.has_errors(lint.lint_skill(path, profile)) is False


def test_the_flat_form_lints_clean(tmp_path):
    path = make_skill(tmp_path, "flat-skill", flat=True)
    assert lint.lint_skill(path, "library") == []


def test_unknown_profile_is_refused(tmp_path):
    with pytest.raises(ValueError):
        lint.lint_skill(make_skill(tmp_path), "nonsense")


# -------------------------------------------------------------- structure

def test_missing_skill_md(tmp_path):
    (tmp_path / "empty-skill").mkdir()
    findings = lint.lint_skill(tmp_path / "empty-skill")
    assert codes(findings) == ["missing_skill_md"]


def test_frontmatter_parse_failure(tmp_path):
    path = make_skill(tmp_path, "broken-skill", text="no frontmatter here\n")
    assert "frontmatter" in codes(lint.lint_skill(path))


@pytest.mark.parametrize("payload", [b"\xff\xfe\x00garbage", b"z" * 2_000_000])
def test_unreadable_bytes_are_a_frontmatter_error(tmp_path, payload):
    path = tmp_path / "hostile-skill"
    path.mkdir()
    (path / "SKILL.md").write_bytes(payload)
    assert codes(lint.lint_skill(path)) == ["frontmatter"]


def test_missing_keys_are_named(tmp_path):
    path = make_skill(tmp_path, "thin-skill", text="---\nname: thin-skill\n---\nbody\n")
    assert set(codes(lint.lint_skill(path))) >= {"missing_key:description",
                                                 "missing_key:version"}


def test_name_mismatch_and_bad_name(tmp_path):
    path = make_skill(tmp_path, "named-skill", text=GOOD.format(name="other"))
    assert "name_mismatch" in codes(lint.lint_skill(path))
    path = make_skill(tmp_path, "bad-skill", text=GOOD.format(name="Bad Name"))
    assert "bad_name" in codes(lint.lint_skill(path))


def test_bad_version_and_description(tmp_path):
    text = GOOD.format(name="ver-skill").replace("version: 1.0.0", "version: 1.0")
    assert "bad_version" in codes(lint.lint_skill(make_skill(tmp_path, "ver-skill", text=text)))
    text = GOOD.format(name="desc-skill").replace(
        "description: A good test skill for the lint.",
        "description: " + "d" * 201)
    assert "bad_description" in codes(
        lint.lint_skill(make_skill(tmp_path, "desc-skill", text=text)))


def test_bad_triggers(tmp_path):
    text = GOOD.format(name="trig-skill").replace(
        "triggers: [alpha, beta]", "triggers: one-string")
    assert "bad_triggers" in codes(
        lint.lint_skill(make_skill(tmp_path, "trig-skill", text=text)))
    text = GOOD.format(name="many-skill").replace(
        "triggers: [alpha, beta]",
        "triggers: [" + ", ".join(f"t{i}" for i in range(30)) + "]")
    assert "bad_triggers" in codes(
        lint.lint_skill(make_skill(tmp_path, "many-skill", text=text)))


def test_unknown_capability_is_an_error(tmp_path):
    text = GOOD.format(name="cap-skill").replace("requires: [specs]",
                                                 "requires: [specs, nope]")
    findings = lint.lint_skill(make_skill(tmp_path, "cap-skill", text=text))
    assert "unknown_capability" in codes(findings, "error")
    assert "nope" in next(f.message for f in findings
                          if f.code == "unknown_capability")


def test_unknown_key_is_a_warning(tmp_path):
    text = GOOD.format(name="extra-skill").replace(
        "version: 1.0.0", "version: 1.0.0\nmaturity: draft")
    findings = lint.lint_skill(make_skill(tmp_path, "extra-skill", text=text))
    assert codes(findings) == ["unknown_key"]
    assert level_of(findings, "unknown_key") == "warning"


def test_empty_body_is_an_error(tmp_path):
    text = GOOD.format(name="void-skill").split("# Title")[0]
    findings = lint.lint_skill(make_skill(tmp_path, "void-skill", text=text))
    assert "empty_body" in codes(findings, "error")


# --------------------------------------------------------------- profiles

def test_license_and_author_severity_by_profile(tmp_path):
    text = GOOD.format(name="bare-skill")
    text = text.replace("license: Apache-2.0\n", "").replace(
        "author: AgentCAD core\n", "")
    path = make_skill(tmp_path, "bare-skill", text=text)
    library = lint.lint_skill(path, "library")
    user = lint.lint_skill(path, "user")
    assert codes(library, "error") == ["missing_author", "missing_license"]
    assert codes(user, "warning") == ["missing_author", "missing_license"]
    assert codes(user, "error") == []


def test_body_too_long_severity_by_profile(tmp_path):
    text = GOOD.format(name="long-skill") + ("x" * 500)
    path = make_skill(tmp_path, "long-skill", text=text)
    assert codes(lint.lint_skill(path, "library", max_chars=100), "error") \
        == ["body_too_long"]
    assert codes(lint.lint_skill(path, "user", max_chars=100), "warning") \
        == ["body_too_long"]


# ------------------------------------------------------------ code + files

def test_code_fence_syntax_names_the_line(tmp_path):
    text = GOOD.format(name="fence-skill").replace("x = 1", "def (:")
    findings = lint.lint_skill(make_skill(tmp_path, "fence-skill", text=text))
    assert codes(findings) == ["code_fence_syntax"]
    assert "line" in findings[0].message


def test_non_python_fences_are_not_parsed(tmp_path):
    text = GOOD.format(name="json-skill") + "\n```json\n{not python}\n```\n"
    assert lint.lint_skill(make_skill(tmp_path, "json-skill", text=text)) == []


def test_snippet_syntax_and_table_json(tmp_path):
    path = make_skill(tmp_path, "asset-skill")
    (path / "snippets").mkdir()
    (path / "snippets" / "broken.py").write_text("def (:\n", encoding="utf-8")
    (path / "tables").mkdir()
    (path / "tables" / "bad.json").write_text("{oops", encoding="utf-8")
    (path / "tables" / "ok.json").write_text(json.dumps({"a": 1}),
                                             encoding="utf-8")
    assert codes(lint.lint_skill(path)) == ["snippet_syntax", "table_json"]


def test_broken_link(tmp_path):
    text = GOOD.format(name="link-skill") + (
        "\nSee [the snippet](snippets/missing.py) and "
        "[the web](https://example.com/x) and [an anchor](#one).\n")
    path = make_skill(tmp_path, "link-skill", text=text)
    findings = lint.lint_skill(path)
    assert codes(findings) == ["broken_link"]
    assert "snippets/missing.py" in findings[0].message
    (path / "snippets").mkdir()
    (path / "snippets" / "missing.py").write_text("x = 1\n", encoding="utf-8")
    assert lint.lint_skill(path) == []


def test_stray_file_is_a_warning(tmp_path):
    path = make_skill(tmp_path, "stray-skill")
    (path / "data.csv").write_text("a,b\n", encoding="utf-8")
    (path / ".keep").write_text("", encoding="utf-8")
    (path / "notes.md").write_text("# fine\n", encoding="utf-8")
    findings = lint.lint_skill(path)
    assert codes(findings) == ["stray_file"]
    assert level_of(findings, "stray_file") == "warning"
    assert "data.csv" in findings[0].message or "data.csv" in findings[0].path


def test_duplicate_skill_file_is_a_warning(tmp_path):
    make_skill(tmp_path, "twin-skill")
    make_skill(tmp_path, "twin-skill", flat=True)
    findings = lint.lint_skill(tmp_path / "twin-skill")
    assert codes(findings) == ["duplicate_skill_file"]
    assert level_of(findings, "duplicate_skill_file") == "warning"


# --------------------------------------------------------------- lint_dir

def test_lint_dir_covers_every_skill(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "one-skill")
    make_skill(root, "two-skill", flat=True)
    make_skill(root, "bad-skill", text="broken\n")
    (root / "README.md").write_text("# the library\n", encoding="utf-8")
    findings = lint.lint_dir(root, "library")
    assert codes(findings) == ["frontmatter"]
    assert "bad-skill" in findings[0].path


def test_lint_dir_reports_a_stray_entry(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "one-skill")
    (root / "notes.txt").write_text("hi\n", encoding="utf-8")
    (root / "Bad_Name").mkdir()
    findings = lint.lint_dir(root, "user")
    assert codes(findings) == ["stray_file", "stray_file"]
    assert lint.has_errors(findings) is False


def test_lint_dir_lints_the_directory_form_of_a_duplicate_once(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "twin-skill")
    make_skill(root, "twin-skill", flat=True)
    assert codes(lint.lint_dir(root, "user")) == ["duplicate_skill_file"]


def test_lint_dir_on_an_empty_directory(tmp_path):
    (tmp_path / "skills").mkdir()
    assert lint.lint_dir(tmp_path / "skills", "library") == []


# --------------------------------------------------------------- scaffold

def test_scaffold_writes_a_skill_that_lints_clean(tmp_path):
    path = lint.scaffold(tmp_path, "new-skill")
    assert path == tmp_path / "new-skill" / "SKILL.md"
    assert (tmp_path / "new-skill" / "snippets" / ".keep").is_file()
    assert lint.lint_skill(tmp_path / "new-skill", "user") == []


def test_scaffold_refuses_to_overwrite(tmp_path):
    lint.scaffold(tmp_path, "new-skill")
    with pytest.raises(FileExistsError):
        lint.scaffold(tmp_path, "new-skill")


def test_scaffold_refuses_a_bad_name(tmp_path):
    with pytest.raises(ValueError):
        lint.scaffold(tmp_path, "Not A Name")


def test_finding_row_and_dict(tmp_path):
    path = make_skill(tmp_path, "broken-skill", text="nope\n")
    finding = lint.lint_skill(path)[0]
    assert finding.to_dict()["code"] == "frontmatter"
    assert finding.row().startswith(str(path / "SKILL.md") + ": error frontmatter: ")


def test_a_skill_directory_without_skill_md_is_not_an_empty_library(tmp_path):
    # `lint_paths` must not read a broken skill as a `skills/` root and
    # report it clean — "clean" is the one answer that must never be wrong.
    (tmp_path / "empty-skill").mkdir()
    findings = lint.lint_paths([tmp_path / "empty-skill"], "user")
    assert codes(findings) == ["missing_skill_md"]


def test_lint_paths_handles_a_library_root_and_one_skill(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "one-skill")
    assert lint.lint_paths([root, root / "one-skill"], "library") == []
    assert lint.holds_skills(root) is True
    assert lint.holds_skills(root / "one-skill") is False


def test_a_symlink_loop_does_not_hang_the_lint(tmp_path):
    path = make_skill(tmp_path, "loop-skill")
    (path / "snippets").mkdir()
    (path / "snippets" / "back").symlink_to(path, target_is_directory=True)
    assert lint.lint_skill(path, "user") == []
