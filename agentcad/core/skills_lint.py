"""Skill lint (PRD-029 FR1/FR6) — pure: no service, no kernel, no build.

What ``agentcad skill lint`` runs, what the core-library test runs over every
shipped skill, and what a future marketplace gate (PRD-031) will run over a
submission. One function, two profiles, the ``materials_lint`` precedent:

``library``
    what ``agentcad/skills/`` is held to: ``license`` and ``author`` are
    required and a body over the cap is an **error** — a core skill that needs
    truncating at load is content debt, not a runtime problem.
``user``
    what a hand-written project skill is held to: those three are warnings.

Everything else is an error in both profiles, because the alternative is a
skill the loader will refuse at runtime with nobody having been told.
``unknown_key`` is a **warning** on purpose: the frontmatter format has to
stay forward-compatible, so a key from a newer version is noise, not a fault.

**Traps**

* The rules that decide whether a file is *loadable* live in
  ``core/skills.py`` (:func:`~agentcad.core.skills.parse_frontmatter` and its
  validation); this module never re-implements them, it only assigns a code,
  a level and a profile. One parser, one answer.
* A skill file is untrusted data: every read is size-capped and decoded
  strictly, and an unreadable one is a ``frontmatter`` **finding**, never an
  exception. The lint must survive a hostile directory.
* Dotfiles are never ``stray_file`` — the scaffold writes ``snippets/.keep``,
  and a ``.DS_Store`` is not a lint finding. ``README.md`` beside the skills
  is not a skill and not a stray either.
* :func:`lint_dir` lints the **directory** form of a duplicated skill and
  reports ``duplicate_skill_file`` once, matching the loader, where the
  directory wins.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .skills import (CAPABILITIES, KNOWN_KEYS, MAX_DESCRIPTION,
                     MAX_SKILL_FILE_BYTES, MAX_TRIGGER_CHARS, MAX_TRIGGERS,
                     NAME_RE, VERSION_RE, SkillFormatError, is_flat_skill,
                     parse_frontmatter, walk_files)

#: How many files the lint will look at inside one skill directory. The walk
#: is bounded and symlink-safe for the same reason the loader's is: a skill
#: directory is data from somewhere else.
MAX_SKILL_FILES = 500

PROFILES = ("library", "user")

#: Codes whose level depends on the profile: an error for the shipped
#: library, a warning for a hand-written project skill.
_PROFILED = ("missing_license", "missing_author", "body_too_long")

#: Sibling files a skill directory may hold without a ``stray_file`` warning.
#: Anything else is *probably* a mistake, and a warning never blocks.
_ALLOWED_SUFFIXES = {".md"}

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$")

SCAFFOLD = """\
---
name: {name}
description: One sentence an agent can retrieve on — what this teaches and when to use it.
version: 0.1.0
triggers: [{name}]
license: Apache-2.0
author: {name} author
requires: []
---
# {title}

What this skill is for, in a sentence or two. Everything below is reference
material for an agent writing a part script.

## Rules of thumb

- The numbers, tolerances or ratios that matter, with their units.

## Worked example

```python
PARAMS = {{"width": {{"default": 40.0, "min": 10.0, "max": 200.0, "unit": "mm"}}}}


def build(p):
    from build123d import Box

    return Box(p.width, 20, 10)
```

Complete, runnable scripts belong in `snippets/` beside this file; data the
body refers to belongs in `tables/*.json`.

## Sources

- Where these numbers come from.
"""


@dataclass(frozen=True)
class Finding:
    """One lint result. ``path`` is the file the finding is about."""

    path: str
    level: str          # "error" | "warning"
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"path": self.path, "level": self.level, "code": self.code,
                "message": self.message}

    def row(self) -> str:
        """The CLI's one-line form: ``path: level code: message``."""
        return f"{self.path}: {self.level} {self.code}: {self.message}"


def has_errors(findings) -> bool:
    """Whether anything here should fail a build (exit 1)."""
    return any(f.level == "error" for f in findings)


def _sort_key(f: Finding) -> tuple:
    return (f.path, f.code, f.message)


# ------------------------------------------------------------------ one skill

def lint_skill(path, profile: str = "user", *,
               max_chars: int = 24_000) -> list[Finding]:
    """Lint one skill: a directory ``<name>/`` or a flat ``<name>.md``."""
    if profile not in PROFILES:
        raise ValueError(f"unknown lint profile {profile!r}")
    path = Path(path)
    library = profile == "library"
    findings: list[Finding] = []

    def add(code: str, message: str, where: Path | None = None,
            level: str | None = None) -> None:
        if level is None:
            level = "error" if (library or code not in _PROFILED) else "warning"
            if code in ("unknown_key", "stray_file", "duplicate_skill_file"):
                level = "warning"
        findings.append(Finding(str(where or skill_md), level, code, message))

    if path.is_dir():
        name = path.name
        skill_md = path / "SKILL.md"
        directory: Path | None = path
        if not skill_md.is_file():
            return [Finding(str(skill_md), "error", "missing_skill_md",
                            f"{path} has no SKILL.md")]
        if (path.parent / f"{name}.md").is_file():
            add("duplicate_skill_file",
                f"both {name}/ and {name}.md exist; the directory wins")
    else:
        name = path.stem
        skill_md = path
        directory = None
        if not path.is_file():
            return [Finding(str(path), "error", "missing_skill_md",
                            f"{path} does not exist")]
        if (path.parent / name).is_dir():
            add("duplicate_skill_file",
                f"both {name}/ and {name}.md exist; the directory wins")

    text = _read_text(skill_md)
    if isinstance(text, str):
        findings.extend(_lint_text(text, name, skill_md, directory, library,
                                   max_chars))
    else:
        add("frontmatter", text.message)

    if directory is not None:
        findings.extend(_lint_directory(directory, skill_md, library))
    return sorted(findings, key=_sort_key)


class _Unreadable:
    def __init__(self, message: str):
        self.message = message


def _read_text(path: Path):
    """The file's text, or an :class:`_Unreadable` explaining why not."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _Unreadable(f"unreadable: {type(exc).__name__}: {exc}")
    if len(raw) > MAX_SKILL_FILE_BYTES:
        return _Unreadable(f"the file is {len(raw)} bytes; the ceiling is "
                           f"{MAX_SKILL_FILE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _Unreadable(f"not valid UTF-8: {exc}")
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def _lint_text(text: str, name: str, skill_md: Path, directory: Path | None,
               library: bool, max_chars: int) -> list[Finding]:
    out: list[Finding] = []

    def add(code: str, message: str, level: str | None = None) -> None:
        if level is None:
            level = "error" if (library or code not in _PROFILED) else "warning"
            if code == "unknown_key":
                level = "warning"
        out.append(Finding(str(skill_md), level, code, message))

    try:
        meta, body = parse_frontmatter(text)
    except SkillFormatError as exc:
        add("frontmatter", str(exc))
        return out

    for key in ("name", "description", "version"):
        if key not in meta:
            add(f"missing_key:{key}", f"the {key!r} key is required")

    declared = meta.get("name")
    if isinstance(declared, str):
        if not NAME_RE.fullmatch(declared):
            add("bad_name", f"{declared!r} is not a slug "
                            f"([a-z][a-z0-9-]{{1,47}})")
        elif declared != name:
            add("name_mismatch", f"frontmatter name {declared!r} must equal "
                                 f"the directory/file name {name!r}")
    elif declared is not None:
        add("bad_name", "'name' must be a scalar, not a list")

    description = meta.get("description")
    if description is not None:
        if not isinstance(description, str):
            add("bad_description", "'description' must be a scalar")
        elif not description.strip() or len(description) > MAX_DESCRIPTION:
            add("bad_description",
                f"description must be 1–{MAX_DESCRIPTION} characters "
                f"(it is {len(description)})")

    version = meta.get("version")
    if version is not None:
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            add("bad_version", f"{version!r} is not MAJOR.MINOR.PATCH")

    triggers = meta.get("triggers", []) or []
    if isinstance(triggers, str):
        add("bad_triggers", "'triggers' must be a list ([a, b] or '- a' lines)")
    elif len(triggers) > MAX_TRIGGERS:
        add("bad_triggers", f"at most {MAX_TRIGGERS} triggers "
                            f"({len(triggers)} given)")
    else:
        long = [t for t in triggers if len(t) > MAX_TRIGGER_CHARS]
        if long:
            add("bad_triggers", f"trigger {long[0]!r} is longer than "
                                f"{MAX_TRIGGER_CHARS} characters")

    requires = meta.get("requires", []) or []
    if isinstance(requires, str):
        add("unknown_capability", "'requires' must be a list of capability "
                                  "names")
    else:
        unknown = [c for c in requires if c not in CAPABILITIES]
        if unknown:
            add("unknown_capability",
                f"{', '.join(repr(c) for c in unknown)} is not a known "
                f"capability; the closed set is "
                f"{', '.join(sorted(CAPABILITIES))}")

    for key in ("license", "author"):
        if not meta.get(key):
            add(f"missing_{key}", f"the {key!r} key is required for a core "
                                  f"skill and recommended for a project one")

    for key in sorted(meta):
        if key not in KNOWN_KEYS:
            add("unknown_key", f"{key!r} is not a key this version knows "
                               f"(kept, but check the spelling)")

    if not body.strip():
        add("empty_body", "the skill has no body — frontmatter alone teaches "
                          "nothing")
    if len(body) > max_chars:
        add("body_too_long", f"the body is {len(body)} characters; the cap is "
                             f"{max_chars} (it would be truncated at load)")

    out.extend(_lint_fences(body, skill_md, len(text.split("\n")) - len(body.split("\n"))))
    out.extend(_lint_links(body, skill_md, directory))
    return out


def _lint_fences(body: str, skill_md: Path, offset: int) -> list[Finding]:
    """``ast.parse`` every ```python fence; the message carries the line."""
    out: list[Finding] = []
    lines = body.split("\n")
    fence: str | None = None
    start = 0
    language = ""
    buffer: list[str] = []
    for i, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                start = i + 1
                buffer = []
                language = match.group(2).lower()
            continue
        if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
            if language in ("python", "py"):
                source = "\n".join(buffer)
                try:
                    ast.parse(source)
                except SyntaxError as exc:
                    out.append(Finding(
                        str(skill_md), "error", "code_fence_syntax",
                        f"the ```python fence opening at line "
                        f"{start + offset} does not parse: {exc.msg} "
                        f"(fence line {exc.lineno})"))
            fence = None
            continue
        buffer.append(line)
    return out


def _lint_links(body: str, skill_md: Path,
                directory: Path | None) -> list[Finding]:
    """Relative link targets must exist inside the skill's own directory."""
    base = directory or skill_md.parent
    out: list[Finding] = []
    seen: set[str] = set()
    for target in _LINK_RE.findall(body):
        if target in seen:
            continue
        seen.add(target)
        if target.startswith("#") or "://" in target or ":" in target.split("/")[0]:
            continue
        relative = target.split("#")[0].split("?")[0]
        if not relative:
            continue
        candidate = base / relative
        try:
            inside = candidate.resolve().is_relative_to(base.resolve())
        except OSError:
            inside = False
        if not inside or not candidate.exists():
            out.append(Finding(str(skill_md), "error", "broken_link",
                               f"the link target {target!r} does not resolve "
                               f"inside the skill directory"))
    return out


def _lint_directory(directory: Path, skill_md: Path,
                    library: bool) -> list[Finding]:
    """Siblings: snippets parse, tables are JSON, everything else is named."""
    out: list[Finding] = []
    for child in walk_files(directory, MAX_SKILL_FILES):
        if child == skill_md:
            continue
        try:
            relative = child.relative_to(directory)
        except ValueError:
            continue
        parts = relative.parts
        if parts[0] == "snippets" and child.suffix == ".py":
            out.extend(_parse_python(child))
            continue
        if parts[0] == "tables" and child.suffix == ".json":
            out.extend(_parse_json(child))
            continue
        if child.suffix in _ALLOWED_SUFFIXES:
            continue
        out.append(Finding(str(child), "warning", "stray_file",
                           f"{relative.as_posix()} is not SKILL.md, "
                           f"snippets/*.py, tables/*.json or *.md"))
    return out


def _parse_python(path: Path) -> list[Finding]:
    text = _read_text(path)
    if not isinstance(text, str):
        return [Finding(str(path), "error", "snippet_syntax", text.message)]
    try:
        ast.parse(text)
    except SyntaxError as exc:
        return [Finding(str(path), "error", "snippet_syntax",
                        f"{exc.msg} (line {exc.lineno})")]
    return []


def _parse_json(path: Path) -> list[Finding]:
    text = _read_text(path)
    if not isinstance(text, str):
        return [Finding(str(path), "error", "table_json", text.message)]
    try:
        json.loads(text)
    except (ValueError, RecursionError) as exc:
        return [Finding(str(path), "error", "table_json", str(exc))]
    return []


# ----------------------------------------------------------- a whole library

def lint_dir(root, profile: str = "user", *,
             max_chars: int = 24_000) -> list[Finding]:
    """Lint every skill directly under ``root`` (a ``skills/`` directory)."""
    if profile not in PROFILES:
        raise ValueError(f"unknown lint profile {profile!r}")
    root = Path(root)
    findings: list[Finding] = []
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        return [Finding(str(root), "error", "missing_skill_md",
                        f"unreadable directory: {exc}")]
    skills: set[str] = set()
    strays: list[Path] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            if NAME_RE.fullmatch(entry.name):
                skills.add(entry.name)
                findings.extend(lint_skill(entry, profile, max_chars=max_chars))
            else:
                strays.append(entry)
        elif is_flat_skill(entry):
            if entry.stem not in skills:
                findings.extend(lint_skill(entry, profile, max_chars=max_chars))
        elif entry.name != "README.md":
            strays.append(entry)
    # A flat file shadowed by its directory is not linted twice: the directory
    # is what the loader serves, and its `duplicate_skill_file` already says so.
    for stray in strays:
        if stray.is_dir() or stray.stem not in skills:
            findings.append(Finding(
                str(stray), "warning", "stray_file",
                f"{stray.name} is not a skill (a skill is <name>/SKILL.md or "
                f"a <name>.md opening with '---')"))
    return sorted(findings, key=_sort_key)


def holds_skills(path: Path) -> bool:
    """Whether ``path`` looks like a ``skills/`` directory rather than one
    skill: it holds at least one skill directory or flat skill."""
    try:
        entries = sorted(path.iterdir())
    except OSError:
        return False
    return any(
        (e.is_dir() and NAME_RE.fullmatch(e.name) and (e / "SKILL.md").is_file())
        or (e.is_file() and is_flat_skill(e))
        for e in entries)


def lint_paths(paths, profile: str = "user", *,
               max_chars: int = 24_000) -> list[Finding]:
    """Lint each path: a skill directory, a flat ``.md``, or a ``skills/``
    directory holding many. The three are told apart by what is on disk.

    A directory with no ``SKILL.md`` and nothing skill-shaped inside is read
    as a **broken skill**, not as an empty library — otherwise pointing the
    lint at a skill whose ``SKILL.md`` is missing reports "clean", which is
    the one answer that must never be wrong.
    """
    findings: list[Finding] = []
    for raw in paths:
        path = Path(raw)
        if (path.is_dir() and not (path / "SKILL.md").is_file()
                and (holds_skills(path) or path.name == "skills")):
            findings.extend(lint_dir(path, profile, max_chars=max_chars))
        else:
            findings.extend(lint_skill(path, profile, max_chars=max_chars))
    return sorted(findings, key=_sort_key)


# ------------------------------------------------------------------ scaffold

def scaffold(target_dir, name: str) -> Path:
    """Write ``<target_dir>/<name>/SKILL.md`` + ``snippets/.keep``.

    Refuses an existing directory (``FileExistsError``) and a name that is not
    a slug (``ValueError``) — the CLI turns both into exit 2. The output lints
    clean under the ``user`` profile, which is the only way an author can tell
    the scaffold from a trap.
    """
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError(f"{name!r} is not a skill name "
                         f"([a-z][a-z0-9-]{{1,47}})")
    target = Path(target_dir) / name
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    (target / "snippets").mkdir(parents=True)
    (target / "snippets" / ".keep").write_text("", encoding="utf-8")
    skill_md = target / "SKILL.md"
    title = name.replace("-", " ").capitalize()
    skill_md.write_text(SCAFFOLD.format(name=name, title=title),
                        encoding="utf-8")
    return skill_md
