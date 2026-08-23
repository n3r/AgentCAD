"""The shipped library (`agentcad/skills/`) is held to its own gate.

Three claims, one parametrised case per skill so a failure names the skill:
it lints clean at the `library` profile, every ```python fence in the body
parses, and every `snippets/*.py` **builds green** through the real kernel —
a snippet an agent is told to copy must be a script that runs.

The parametrisation survives an empty library (the core skills land in
Slice 4): an empty param list would make pytest error on collection, so the
absence is one skipped case instead.
"""

import ast
import re

import pytest

from agentcad.core.skills import CORE_DIR, SkillLibrary, is_flat_skill
from agentcad.core.skills_lint import has_errors, lint_skill
from tests.conftest import make_test_service

_PY_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*(python|py)\s*$", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _core_skill_paths():
    try:
        entries = sorted(CORE_DIR.iterdir())
    except OSError:
        entries = []
    paths = [e for e in entries
             if (e.is_dir() and (e / "SKILL.md").is_file())
             or (e.is_file() and is_flat_skill(e))]
    if not paths:
        return [pytest.param(None, id="no-core-skills-yet")]
    return [pytest.param(p, id=p.name) for p in paths]


CORE_SKILLS = _core_skill_paths()


def _skip_if_absent(path):
    if path is None:
        pytest.skip("the core library lands in Slice 4 (PRD-029)")


def _python_fences(body: str) -> list[tuple[int, str]]:
    """`[(line, source)]` for every ```python fence in `body`."""
    out = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        opening = _PY_FENCE_RE.match(lines[i])
        if not opening:
            i += 1
            continue
        marker = opening.group(1)
        start = i + 1
        i += 1
        buffer = []
        while i < len(lines):
            closing = _FENCE_RE.match(lines[i])
            if closing and closing.group(1)[0] == marker[0] \
                    and len(closing.group(1)) >= len(marker):
                break
            buffer.append(lines[i])
            i += 1
        out.append((start + 1, "\n".join(buffer)))
        i += 1
    return out


@pytest.mark.parametrize("path", CORE_SKILLS)
def test_core_skill_lints_clean(path):
    _skip_if_absent(path)
    findings = lint_skill(path, "library")
    assert not has_errors(findings), [f.row() for f in findings]


@pytest.mark.parametrize("path", CORE_SKILLS)
def test_core_skill_python_fences_parse(path):
    _skip_if_absent(path)
    body = (path / "SKILL.md" if path.is_dir() else path).read_text(
        encoding="utf-8")
    for line, source in _python_fences(body):
        try:
            ast.parse(source)
        except SyntaxError as exc:
            pytest.fail(f"{path.name}: the ```python fence at line {line} "
                        f"does not parse: {exc}")


@pytest.mark.parametrize("path", CORE_SKILLS)
def test_core_skill_snippets_build_green(path, tmp_path, kernel):
    _skip_if_absent(path)
    if not path.is_dir():
        pytest.skip("a flat skill has no snippets")
    snippets = sorted((path / "snippets").glob("*.py"))
    if not snippets:
        pytest.skip(f"{path.name} ships no snippets")
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("snippets")
    for snippet in snippets:
        part_id = re.sub(r"[^a-z0-9_]", "_", snippet.stem.lower())[:40]
        part = service.create_part("snippets", part_id,
                                   script=snippet.read_text(encoding="utf-8"))
        assert part["status"]["state"] == "ok", \
            f"{snippet}: {part['status']['error']}"
        volume = (part["metrics"] or {}).get("volume_mm3")
        assert volume and volume > 0, f"{snippet} builds to nothing"


def test_the_shipped_library_has_at_least_twelve_skills():
    assert len(SkillLibrary().index()) >= 12
