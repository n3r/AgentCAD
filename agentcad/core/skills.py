"""Agent skills: the format, the layers, retrieval, truncation and trust.

A **skill** is a versioned markdown guide an agent can load on demand instead
of carrying in every system prompt (PRD-029). It is a directory
``<name>/SKILL.md`` with optional siblings (``snippets/*.py``,
``tables/*.json``), or — for the one-file teach flow — a bare ``<name>.md``.
The file is a frontmatter block plus a markdown body.

**Where the files live.** Two layers, precedence low → high in
:data:`LAYER_ORDER`:

``core``
    :data:`CORE_DIR` = ``agentcad/skills/`` — shipped in the wheel (hatchling
    packages the whole ``agentcad`` tree), trusted by construction.
``project``
    ``<project>/skills/`` — plain files under the project's **working tree**
    (``store.path_of``, not ``canonical_path_of``), so a skill is git-tracked,
    branched, merged and restored by PRD-001 with no code here.

``org`` sits between them in :data:`LAYER_ORDER` and is deliberately
unpopulated: PRD-005's org store is the only thing it waits for, and an
ordered tuple is the whole seam it needs. A higher layer **shadows** a
lower one by name — including when the higher file is broken, because a
project skill that shadows a core one and fails to parse must be *visible*
(``invalid`` set), never a silent hole.

**Where the local state lives.** ``<project>/.history/agentcad/skills/
trust.json`` — inside GIT_DIR, via ``store.canonical_path_of``, exactly like
PRD-008's comments: never versioned, never cloned, restore-proof, and the same
for every branch. Shape ``{"version": 1, "trusted": {name: sha256},
"disabled": [name]}``. It is read through a size-capped loader and a corrupt
file reads as **empty** — nothing is trusted, nothing is lost, the next
approval rebuilds it.

**Trust is keyed by the content digest**, not by the name: a ``git pull``
that rewrites a trusted skill is the attack this exists for, so editing a
trusted skill makes it untrusted again. Core skills are trusted by
construction; an untrusted project skill is *listed* (so a human can see it)
and refused on load with ``skill_untrusted``.

**Traps**

* This module is OCP-free and must stay that way. The only ``agentcad.kernel``
  import is ``handlers.fem.fem_available`` — a pure probe, imported lazily
  inside :func:`_has_fem` the way ``core/specs.py`` does it.
* A skill file is **data from somewhere else**. Every read is size-capped
  (:data:`MAX_SKILL_FILE_BYTES`), decoded ``utf-8`` strictly and turned into
  an ``invalid`` record — a malformed, huge, non-UTF-8, CRLF or BOM-prefixed
  file never raises out of :meth:`SkillLibrary.records`.
* Frontmatter is **our own strict subset**, not YAML: every value is a string
  and nothing is coerced (``version: 1.0`` stays ``"1.0"``, ``no`` stays
  ``"no"``). YAML's implicit typing is exactly the silent corruption a
  diffable hand-edited format must not have.
* An unknown capability in ``requires`` fails **closed** (hidden + refused),
  so a typo cannot leak a skill past the gate.
* Truncation is structural — whole ``## `` sections, fences respected — and
  the cap is a **hard guarantee**: a preamble that alone exceeds it is cut at
  a line boundary with any open fence closed, reported as
  ``(preamble cut)``. A heading-less body must not be a way around the budget.
  The one slack is :data:`PREAMBLE_CUT_SLACK` (4) for that closing fence.
* Parsed frontmatter is memoised per ``(path, mtime_ns, size)``. A rewrite
  that keeps both the size and the nanosecond timestamp would be served from
  the memo; that is the usual stat-based-cache bargain and it is why the
  digest is taken from the same read as the parse.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .model import NotFoundError, ValidationError
from .project import ProjectStore

#: Layer precedence, low → high. ``org`` is PRD-005's and is never populated
#: here; the ordered tuple is the whole seam it needs.
LAYER_ORDER = ("core", "org", "project")

#: The shipped library. A directory inside the package, so the wheel carries
#: it; nothing here imports it as a Python package.
CORE_DIR = Path(__file__).resolve().parent.parent / "skills"

NAME_RE = re.compile(r"[a-z][a-z0-9-]{1,47}")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
KEY_RE = re.compile(r"[a-z_]+")

MAX_DESCRIPTION = 200
MAX_TRIGGERS = 24
MAX_TRIGGER_CHARS = 32

#: One skill file — prose, not a payload. A megabyte is three orders of
#: magnitude above any real skill and still refuses a memory bomb; a bigger
#: file is an ``invalid`` record, never an exception.
MAX_SKILL_FILE_BYTES = 1024 * 1024

#: How many sibling files ``load`` will enumerate. A hostile skill directory
#: must not turn one tool call into an unbounded walk.
MAX_ASSETS = 200

#: Frontmatter keys this version understands. Anything else is kept in
#: ``SkillMeta.extra`` and is a lint *warning*, never an error — the format
#: has to stay forward-compatible.
KNOWN_KEYS = ("name", "description", "version", "triggers", "license",
              "author", "requires")


def empty_trust_state() -> dict:
    """A fresh empty trust document.

    A **function**, not a module constant: callers mutate what they are given
    (``state["trusted"][name] = digest``), and a shared constant — even copied
    shallowly — hands every project the same nested ``trusted`` dict. That is
    exactly how a corrupt trust file kept answering "trusted" during
    development.
    """
    return {"version": 1, "trusted": {}, "disabled": []}


class SkillFormatError(ValueError):
    """The frontmatter block is not the subset this module accepts."""


# --------------------------------------------------------------- capabilities

def _has_fem() -> bool:
    """Whether the optional FEM extra can run here.

    Lazy on purpose — ``core`` must import without the extra, and this is the
    same probe ``tools_analysis`` and ``core/specs.py`` use.
    """
    from ..kernel.handlers.fem import fem_available

    return fem_available()


def _has_module(name: str) -> Callable[[], bool]:
    def probe() -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    return probe


#: The closed set a skill may name in ``requires``. Closed because the gate
#: fails closed: an unknown name is refused, so a typo hides a skill instead
#: of leaking it.
CAPABILITIES: dict[str, Callable[[], bool]] = {
    "fem": _has_fem,
    "threads": _has_module("bd_warehouse"),
    "sheetmetal": _has_module("agentcad.toolkit.sheetmetal"),
    "sketch": _has_module("agentcad.toolkit.sketch"),
    "holes": _has_module("agentcad.toolkit.holes"),
    # Always present: named so a skill can declare what it is about, and so a
    # future split of the toolkit has a hook.
    "specs": lambda: True,
}


def available_capabilities() -> frozenset[str]:
    """The subset of :data:`CAPABILITIES` this installation can run."""
    out = set()
    for name, probe in CAPABILITIES.items():
        try:
            if probe():
                out.add(name)
        except Exception:  # a probe is best-effort; absent beats raising
            continue
    return frozenset(out)


# --------------------------------------------------------------- frontmatter

def parse_frontmatter(text: str) -> tuple[dict[str, str | list[str]], str]:
    """``(meta, body)`` for a ``---``-delimited frontmatter block.

    A strict flat subset of YAML and nothing more: ``key: value`` scalars
    (bare, ``"double"`` or ``'single'`` quoted), ``[a, b]`` inline lists and
    ``- item`` block lists, ``#`` comments, blank lines. **Every value is a
    string** — nothing is coerced. Raises :class:`SkillFormatError` on
    anything else.
    """
    if not isinstance(text, str):
        raise SkillFormatError("a skill file must be text")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise SkillFormatError(
            "a skill file must open with '---' on line 1 (frontmatter)")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise SkillFormatError("the frontmatter block is never closed by '---'")

    meta: dict[str, str | list[str]] = {}
    pending: str | None = None       # key currently collecting block items
    blockless: set[str] = set()      # keys written as `key:` with nothing yet
    for lineno, raw in enumerate(lines[1:end], start=2):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if pending is None:
                raise SkillFormatError(
                    f"line {lineno}: a '- item' line with no key above it")
            meta[pending].append(_scalar(stripped[2:].strip(), lineno))
            blockless.discard(pending)
            continue
        if line[:1].isspace() and not stripped.startswith("- "):
            raise SkillFormatError(
                f"line {lineno}: unexpected indentation ({line.strip()!r})")
        key, sep, value = line.partition(":")
        if not sep:
            raise SkillFormatError(
                f"line {lineno}: expected 'key: value' ({line.strip()!r})")
        key = key.strip()
        if not KEY_RE.fullmatch(key):
            raise SkillFormatError(
                f"line {lineno}: {key!r} is not a valid key ([a-z_]+)")
        if key in meta:
            raise SkillFormatError(f"line {lineno}: duplicate key {key!r}")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = _inline_list(value[1:-1], lineno)
            pending = None
        elif value == "":
            meta[key] = []          # may collect '- item' lines below
            blockless.add(key)
            pending = key
        else:
            meta[key] = _scalar(value, lineno)
            pending = None

    # `key:` that never collected a '- item' is an empty SCALAR, not a list —
    # every value in this format is a string unless it is written as one of
    # the two list forms. The readers that expect a list treat "" as absent.
    for key in blockless:
        meta[key] = ""
    body = "\n".join(lines[end + 1:])
    return meta, body.lstrip("\n")


def _scalar(value: str, lineno: int) -> str:
    for quote in ('"', "'"):
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1]
    if value.startswith(("[", "{")):
        raise SkillFormatError(
            f"line {lineno}: only inline lists ([a, b]) are supported")
    return value


def _inline_list(inner: str, lineno: int) -> list[str]:
    if not inner.strip():
        return []
    return [_scalar(item.strip(), lineno) for item in inner.split(",")]


# ------------------------------------------------------------------- records

@dataclass(frozen=True)
class SkillMeta:
    """A skill's validated frontmatter. Every field is a string."""

    name: str
    description: str
    version: str
    triggers: tuple[str, ...] = ()
    license: str | None = None
    author: str | None = None
    requires: tuple[str, ...] = ()
    #: Unknown keys, kept verbatim so a forward-compatible file round-trips.
    extra: tuple[tuple[str, str | tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class SkillRecord:
    """One skill file as it is on disk, valid or not.

    ``name`` is the directory/file name — the identity even when the file is
    broken, which is what lets a broken skill still shadow and still be listed.
    """

    meta: SkillMeta | None
    name: str
    layer: str
    path: Path
    dir: Path | None
    body: str
    digest: str
    invalid: str | None

    @property
    def requires(self) -> tuple[str, ...]:
        return () if self.meta is None else self.meta.requires


@dataclass(frozen=True)
class SkillBudget:
    """Caps on what a skill costs an agent's context.

    ``max_loaded``/``max_loaded_chars`` are the chat engine's LRU bounds
    (PRD-029 §3); ``max_skill_chars`` is this module's truncation cap.
    """

    max_loaded: int = 4
    max_loaded_chars: int = 40_000
    max_skill_chars: int = 24_000

    @classmethod
    def from_config(cls) -> "SkillBudget":
        """Env > ``~/.agentcad/config.json`` > the defaults above."""
        from ..config import get_skills_budget

        return cls(**get_skills_budget())


# ------------------------------------------------------------- file reading

#: ``(path, mtime_ns, size) -> (meta, body, digest, invalid)``. Bounded and
#: cleared wholesale — it is a speed-up, never a source of truth.
_PARSE_CACHE: dict[tuple[str, int, int], tuple] = {}
_PARSE_CACHE_MAX = 512


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_file(path: Path, name: str) -> tuple:
    """Read and validate one skill file. Never raises."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return (None, "", "", f"unreadable: {type(exc).__name__}: {exc}")
    digest = _digest(raw)
    if len(raw) > MAX_SKILL_FILE_BYTES:
        return (None, "", digest,
                f"the file is {len(raw)} bytes; the ceiling is "
                f"{MAX_SKILL_FILE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return (None, "", digest, f"not valid UTF-8: {exc}")
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    try:
        meta_map, body = parse_frontmatter(text)
    except SkillFormatError as exc:
        return (None, "", digest, str(exc))
    meta, problem = _meta_from(meta_map, name)
    return (meta, body, digest, problem)


def _meta_from(meta_map: dict, name: str) -> tuple[SkillMeta | None, str | None]:
    """``(meta, problem)`` — the minimum a record needs to be usable.

    Deliberately *not* the lint: this decides whether the library can index
    and serve the file at all. ``core/skills_lint.py`` is where a finding gets
    a code, a level and a profile.
    """
    def scalar(key: str) -> tuple[str | None, str | None]:
        value = meta_map.get(key)
        if value is None:
            return None, f"missing required key {key!r}"
        if not isinstance(value, str):
            return None, f"{key!r} must be a scalar, not a list"
        return value, None

    declared, problem = scalar("name")
    if problem:
        return None, problem
    if not NAME_RE.fullmatch(declared):
        return None, (f"name {declared!r} is not a slug "
                      f"([a-z][a-z0-9-]{{1,47}})")
    if declared != name:
        return None, (f"name {declared!r} does not match the "
                      f"directory/file name {name!r}")
    description, problem = scalar("description")
    if problem:
        return None, problem
    if not description.strip() or len(description) > MAX_DESCRIPTION:
        return None, (f"description must be 1–{MAX_DESCRIPTION} characters "
                      f"(it is {len(description)})")
    version, problem = scalar("version")
    if problem:
        return None, problem
    if not VERSION_RE.fullmatch(version):
        return None, f"version {version!r} is not MAJOR.MINOR.PATCH"

    lists: dict[str, tuple[str, ...]] = {}
    for key, cap in (("triggers", MAX_TRIGGER_CHARS), ("requires", None)):
        value = meta_map.get(key, [])
        if value == "":
            value = []              # `triggers:` with nothing under it
        if isinstance(value, str):
            return None, f"{key!r} must be a list"
        items = [str(v) for v in value]
        if key == "triggers":
            if len(items) > MAX_TRIGGERS:
                return None, (f"triggers: at most {MAX_TRIGGERS} entries "
                              f"({len(items)} given)")
            too_long = [t for t in items if len(t) > cap]
            if too_long:
                return None, (f"trigger {too_long[0]!r} is longer than "
                              f"{cap} characters")
        lists[key] = tuple(items)

    optional: dict[str, str | None] = {}
    for key in ("license", "author"):
        value = meta_map.get(key)
        if value is None:
            optional[key] = None
        elif isinstance(value, str):
            optional[key] = value
        else:
            return None, f"{key!r} must be a scalar, not a list"

    extra = tuple(
        (k, tuple(v) if isinstance(v, list) else v)
        for k, v in sorted(meta_map.items()) if k not in KNOWN_KEYS
    )
    return SkillMeta(name=declared, description=description, version=version,
                     triggers=lists["triggers"], license=optional["license"],
                     author=optional["author"], requires=lists["requires"],
                     extra=extra), None


def read_record(path: Path, name: str, layer: str,
                dir_: Path | None) -> SkillRecord:
    """One :class:`SkillRecord`, memoised on ``(path, mtime_ns, size)``."""
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    parsed = _PARSE_CACHE.get(key) if key is not None else None
    if parsed is None:
        parsed = _parse_file(path, name)
        if key is not None:
            if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
                _PARSE_CACHE.clear()
            _PARSE_CACHE[key] = parsed
    meta, body, digest, invalid = parsed
    return SkillRecord(meta=meta, name=name, layer=layer, path=path, dir=dir_,
                       body=body, digest=digest, invalid=invalid)


def is_flat_skill(path: Path) -> bool:
    """Whether ``<name>.md`` is the flat form of a skill.

    The rule is explicit so a ``README.md`` beside the skills can never be
    mistaken for one: the stem must be a valid name **and** the file must
    open with ``---``.
    """
    if path.suffix != ".md" or not NAME_RE.fullmatch(path.stem):
        return False
    try:
        with open(path, "rb") as f:
            return f.read(3) == b"---"
    except OSError:
        return False


def walk_files(directory: Path, limit: int = MAX_ASSETS) -> list[Path]:
    """Regular files under ``directory``, sorted, bounded, symlink-safe.

    Deliberately **not** ``rglob``: it follows directory symlinks, so a
    self-referential link inside a skill directory is an unbounded walk (and
    ``sorted(rglob(...))`` materialises the whole thing before any cap can
    apply). Dotfiles are skipped — the scaffold writes ``snippets/.keep`` and
    a ``.DS_Store`` is nobody's finding.
    """
    base = directory.resolve()
    out: list[Path] = []
    stack = [directory]
    seen: set[Path] = set()
    while stack and len(out) < limit:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if len(out) >= limit:
                break
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_symlink() and entry.is_dir():
                    continue          # never recurse through a link
                resolved = entry.resolve()
                if not resolved.is_relative_to(base):
                    continue          # a link out of the skill directory
                if entry.is_dir():
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    stack.append(entry)
                elif entry.is_file():
                    out.append(entry)
            except OSError:
                continue
    return sorted(out)


def scan_layer(root: Path, layer: str) -> dict[str, SkillRecord]:
    """Every skill directly under ``root``, by name. Non-skills are ignored.

    The directory form wins over the flat form when both exist (the lint
    reports ``duplicate_skill_file``).
    """
    out: dict[str, SkillRecord] = {}
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    flat: dict[str, Path] = {}
    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue
        if entry.is_dir():
            if not NAME_RE.fullmatch(name):
                continue
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                out[name] = read_record(skill_md, name, layer, entry)
        elif entry.is_file() and is_flat_skill(entry):
            flat[entry.stem] = entry
    for name, path in flat.items():
        if name not in out:
            out[name] = read_record(path, name, layer, None)
    return out


# ------------------------------------------------------------------ sections

def split_sections(body: str) -> list[tuple[str, str]]:
    """``[(heading, text)]`` — the preamble first, with an empty heading.

    ``text`` **includes** the heading line, so ``"".join(texts) == body``;
    that is what makes :func:`truncate_sections` a pure prefix operation.
    Only ``## `` splits (a ``### `` belongs to its parent), and a heading
    inside a fenced code block is not a heading.
    """
    sections: list[tuple[str, list[str]]] = [("", [])]
    fence: str | None = None
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if fence is None:
            marker = _fence_marker(stripped)
            if marker:
                fence = marker
            elif stripped.startswith("## ") and not line[:1].isspace():
                sections.append((stripped[3:].strip(), []))
        else:
            if stripped.startswith(fence) and set(stripped) <= {fence[0]}:
                fence = None
        sections[-1][1].append(line)
    return [(heading, "".join(chunk)) for heading, chunk in sections]


def _fence_marker(stripped: str) -> str | None:
    for char in ("`", "~"):
        if stripped.startswith(char * 3):
            run = len(stripped) - len(stripped.lstrip(char))
            return char * run
    return None


#: What closing an open fence may add on top of ``max_chars``: a newline plus
#: a three-character marker. The cut is pulled back far enough that a longer
#: or ``~~~`` marker still fits inside this, so the cap is a HARD guarantee
#: of ``max_chars + PREAMBLE_CUT_SLACK`` and never more.
PREAMBLE_CUT_SLACK = 4

#: Reported in ``omitted_sections`` when the preamble itself had to be cut.
PREAMBLE_CUT = "(preamble cut)"


def _open_fence(text: str) -> str | None:
    """The marker of the code fence left open at the end of ``text``."""
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is None:
            marker = _fence_marker(stripped)
            if marker:
                fence = marker
        elif stripped.startswith(fence) and set(stripped) <= {fence[0]}:
            fence = None
    return fence


def _cut_at_line(text: str, limit: int) -> str:
    """``text`` cut at the last line boundary at or before ``limit``."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    head = text[:limit]
    boundary = head.rfind("\n")
    return head if boundary < 0 else head[:boundary + 1]


def _cut_preamble(preamble: str, max_chars: int) -> str:
    """The preamble cut to fit, with any open fence closed.

    Cut at a line boundary so no line is halved, then — because the cut can
    land inside a ```python block — close the fence, or an agent receives a
    document whose last section is an unterminated code block. The closer is
    budgeted: the returned string is never longer than
    ``max_chars + PREAMBLE_CUT_SLACK``.
    """
    limit = max_chars
    for _ in range(4):
        cut = _cut_at_line(preamble, limit)
        marker = _open_fence(cut)
        if marker is None:
            return cut
        suffix = ("" if cut.endswith("\n") else "\n") + marker
        if len(cut) + len(suffix) <= max_chars + PREAMBLE_CUT_SLACK:
            return cut + suffix
        limit -= len(cut) + len(suffix) - (max_chars + PREAMBLE_CUT_SLACK)
    return _cut_at_line(preamble, max(0, max_chars - PREAMBLE_CUT_SLACK))


def truncate_sections(body: str, max_chars: int) -> tuple[str, bool, list[str]]:
    """``(text, truncated, omitted_headings)`` — structural truncation.

    Whole ``## `` sections in order while the running total stays within
    ``max_chars``. The first section that does not fit stops the walk, and
    every remaining heading is reported — "the next one happens to be small"
    is not worth an out-of-order body.

    **The cap is a hard guarantee.** The preamble comes first and is kept
    whole *when it fits*; when it alone is over the cap it is cut at a line
    boundary (closing an open fence, see :func:`_cut_preamble`), ``truncated``
    is True and ``omitted_sections`` opens with :data:`PREAMBLE_CUT`. A
    heading-less 900 kB project skill flowing uncapped into an agent's context
    is precisely what this exists to stop, so "never cut mid-fence" is served
    by *closing* the fence rather than by abandoning the cap.
    """
    sections = split_sections(body)
    preamble = sections[0][1]
    headings = [heading for heading, _ in sections[1:]]
    if len(preamble) > max_chars:
        # Nothing after an over-long preamble can fit either: every section
        # is omitted, and the marker says the preamble is short of bytes too.
        return _cut_preamble(preamble, max_chars), True, [PREAMBLE_CUT] + headings
    kept = [preamble]
    total = len(preamble)
    omitted: list[str] = []
    for heading, text in sections[1:]:
        if omitted or total + len(text) > max_chars:
            omitted.append(heading)
            continue
        kept.append(text)
        total += len(text)
    return "".join(kept), bool(omitted), omitted


# ------------------------------------------------------------------ library

class SkillLibrary:
    """Index, search, load and trust over the skill layers.

    ``store`` is the :class:`~agentcad.core.project.ProjectStore` (``None`` →
    the core layer only, which is what an MCP agent browsing before it opens a
    project sees). ``only`` restricts the whole surface to those names — the
    bench's ``--skills`` selection. ``capabilities`` is injected so a test can
    pin the gate without monkeypatching a probe.
    """

    def __init__(self, store: ProjectStore | None = None, *,
                 core_dir: Path = CORE_DIR,
                 budget: SkillBudget | None = None,
                 only: frozenset[str] | None = None,
                 capabilities: Callable[[], frozenset[str]] =
                 available_capabilities):
        self.store = store
        self.core_dir = Path(core_dir)
        self.budget = budget or SkillBudget()
        self.only = None if only is None else frozenset(only)
        self._capabilities = capabilities

    # ------------------------------------------------------------ discovery

    def _layer_dirs(self, project: str | None) -> list[tuple[str, Path]]:
        dirs: list[tuple[str, Path]] = [("core", self.core_dir)]
        if project is not None and self.store is not None:
            # The project's WORKING TREE: a skill is authored state, so it is
            # branched and merged like a part script (PRD-001, AC6).
            dirs.append(("project", self.store.path_of(project) / "skills"))
        return dirs

    def _effective(self, project: str | None
                   ) -> tuple[dict[str, SkillRecord], dict[str, str]]:
        """``(records, overrides)`` after layering, before any filtering."""
        records: dict[str, SkillRecord] = {}
        overrides: dict[str, str] = {}
        for layer, root in self._layer_dirs(project):
            for name, record in scan_layer(root, layer).items():
                if name in records:
                    overrides[name] = records[name].layer
                records[name] = record
        return dict(sorted(records.items())), overrides

    def records(self, project: str | None = None) -> dict[str, SkillRecord]:
        """Effective records by name, name-sorted. Invalid ones included."""
        return self._effective(project)[0]

    # --------------------------------------------------------------- views

    def _entry(self, record: SkillRecord, overrides: str | None,
               project: str | None) -> dict:
        meta = record.meta
        return {
            "name": record.name,
            "description": "" if meta is None else meta.description,
            "layer": record.layer,
            "version": "" if meta is None else meta.version,
            "triggers": [] if meta is None else list(meta.triggers),
            "requires": [] if meta is None else list(meta.requires),
            "overrides": overrides,
            "trusted": self.is_trusted(record, project),
            "enabled": True,
            "invalid": record.invalid,
        }

    def _partition(self, project: str | None
                   ) -> tuple[list[dict], list[dict]]:
        """``(index, hidden)`` in one pass.

        Filter order is layering → ``only`` → enabled → capability, so a
        disabled skill reports ``disabled`` even when it also needs a
        capability this installation lacks.
        """
        records, overrides = self._effective(project)
        state = self.trust_state(project) if project else empty_trust_state()
        disabled = set(state.get("disabled") or ())
        caps = self._capabilities()
        index: list[dict] = []
        hidden: list[dict] = []
        for name, record in records.items():
            if self.only is not None and name not in self.only:
                continue
            entry = self._entry(record, overrides.get(name), project)
            if name in disabled:
                hidden.append({"name": name, "layer": record.layer,
                               "requires": entry["requires"],
                               "reason": "disabled"})
                continue
            if not set(record.requires) <= caps:
                hidden.append({"name": name, "layer": record.layer,
                               "requires": entry["requires"],
                               "reason": "capability"})
                continue
            index.append(entry)
        return index, hidden

    def index(self, project: str | None = None) -> list[dict]:
        """Visible entries, name-sorted. Capability-hidden and disabled
        skills are **not** here — :meth:`hidden` reports those."""
        return self._partition(project)[0]

    def hidden(self, project: str | None = None) -> list[dict]:
        """Why a skill an agent read about is not in the index."""
        return self._partition(project)[1]

    def search(self, query: str, project: str | None = None
               ) -> tuple[list[dict], bool]:
        """``(entries, matched)`` — the deterministic ranking of spec §2.

        100 an exact name, 60 a name substring, 40 per matching trigger, 10
        per query token in the description; ties break by layer (project
        first) then name. **No hit returns the full index** with
        ``matched=False``, so an agent still sees what exists.
        """
        entries = self.index(project)
        q = (query or "").strip().lower()
        tokens = _tokens(q)
        if not q:
            return entries, False
        scored: list[tuple[int, int, str, dict]] = []
        for entry in entries:
            name = entry["name"]
            score = 0
            if q == name:
                score += 100
            elif (q and q in name) or any(t in name for t in tokens):
                score += 60
            for trigger in entry["triggers"]:
                trig = str(trigger).lower()
                if trig and (trig in q or any(t in trig for t in tokens)):
                    score += 40
            described = set(_tokens(entry["description"].lower()))
            score += 10 * len(described & set(tokens))
            if score:
                rank = LAYER_ORDER.index(entry["layer"])
                scored.append((-score, -rank, name, entry))
        if not scored:
            return entries, False
        scored.sort(key=lambda row: (row[0], row[1], row[2]))
        return [row[3] for row in scored], True

    def compact_index(self, project: str | None = None, limit: int = 40) -> str:
        """The system-context block: ``- name — description`` per line."""
        entries = self.index(project)
        if not entries:
            return ""
        lines = [f"- {e['name']} — {e['description'][:120]}"
                 for e in entries[:limit]]
        if len(entries) > limit:
            lines.append(f"…and {len(entries) - limit} more: "
                         f"call list_skills {{query}}")
        return "\n".join(lines)

    # ------------------------------------------------------------ resolution

    def resolve(self, name: str, project: str | None = None) -> SkillRecord:
        """The record behind ``name``, or the refusal that explains why not."""
        records = self.records(project)
        if not isinstance(name, str) or name not in records:
            raise NotFoundError(
                f"skill {name!r} not found",
                {"reason": "skill_not_found",
                 "hint": "call list_skills to see what this project offers"})
        if self.only is not None and name not in self.only:
            raise NotFoundError(
                f"skill {name!r} not found",
                {"reason": "skill_not_found",
                 "hint": "this run was started with a restricted skill "
                         "selection (bench --skills)"})
        record = records[name]
        state = self.trust_state(project) if project else empty_trust_state()
        if name in set(state.get("disabled") or ()):
            raise ValidationError(
                f"skill {name!r} is disabled for this project",
                {"reason": "skill_disabled", "name": name,
                 "hint": "a human can re-enable it in the Skills panel"})
        if record.invalid:
            raise ValidationError(
                f"skill {name!r} cannot be read: {record.invalid}",
                {"reason": "skill_invalid", "name": name,
                 "problem": record.invalid,
                 "hint": "run `agentcad skill lint` on the file"})
        missing = sorted(set(record.requires) - self._capabilities())
        if missing:
            raise ValidationError(
                f"skill {name!r} requires {', '.join(missing)}, which this "
                f"installation does not have",
                {"reason": "skill_unavailable", "name": name,
                 "requires": list(record.requires), "missing": missing,
                 "hint": "an unknown capability is refused the same way as a "
                         "missing one — check the skill's `requires`"})
        return record

    def load(self, name: str, project: str | None = None,
             asset: str | None = None) -> dict:
        """The ``load_skill`` payload: capped content plus provenance."""
        record = self.resolve(name, project)
        if not self.is_trusted(record, project):
            raise ValidationError(
                f"skill {name!r} has not been reviewed by a human yet",
                {"reason": "skill_untrusted", "name": name,
                 "layer": record.layer,
                 "hint": "a human can approve it in the Skills panel; skills "
                         "are agent instructions, so no agent surface can "
                         "approve them"})
        cap = self.budget.max_skill_chars
        if asset is None:
            content, truncated, omitted = truncate_sections(record.body, cap)
        else:
            content = self._read_asset(record, asset)
            truncated = len(content) > cap
            content = content[:cap]
            omitted = []
        return {
            "name": record.name,
            "layer": record.layer,
            "version": "" if record.meta is None else record.meta.version,
            "content": content,
            "chars": len(content),
            "truncated": truncated,
            "omitted_sections": omitted,
            "assets": self._assets(record),
            "provenance": {
                "layer": record.layer,
                "author": None if record.meta is None else record.meta.author,
                "license": None if record.meta is None else record.meta.license,
                "path": self._relative_path(record, project),
                "digest": record.digest,
            },
        }

    def _relative_path(self, record: SkillRecord,
                       project: str | None) -> str | None:
        """Project-relative path for a project skill, ``None`` for a core one
        (whose absolute path inside the wheel tells a reader nothing)."""
        if record.layer != "project" or project is None or self.store is None:
            return None
        try:
            return record.path.relative_to(
                self.store.path_of(project)).as_posix()
        except (ValueError, NotFoundError):
            return record.path.name

    def _assets(self, record: SkillRecord) -> list[dict]:
        """Sibling files, relative-posix, bounded and never outside the dir."""
        if record.dir is None:
            return []
        out: list[dict] = []
        for child in walk_files(record.dir):
            if child == record.path:
                continue
            try:
                out.append({"path": child.relative_to(record.dir).as_posix(),
                            "bytes": child.stat().st_size})
            except OSError:
                continue
        return out

    def _read_asset(self, record: SkillRecord, asset: str) -> str:
        """One sibling file, verbatim. Refuses everything but a plain
        relative path resolving inside the skill directory (symlinks
        included — the check compares ``resolve()``d paths)."""
        hint = ("asset paths are relative to the skill directory; absolute "
                "paths, '..' and links leaving the directory are refused")
        if not isinstance(asset, str) or not asset.strip():
            raise ValidationError("asset must be a relative path inside the "
                                  "skill directory",
                                  {"reason": "skill_not_found", "hint": hint})
        candidate = Path(asset)
        if (candidate.is_absolute() or candidate.drive
                or asset.startswith(("/", "\\"))
                or any(part in ("..", "") for part in candidate.parts)):
            raise ValidationError(f"asset {asset!r} is not a relative path "
                                  f"inside the skill directory",
                                  {"reason": "skill_not_found", "asset": asset,
                                   "hint": hint})
        if record.dir is None:
            raise NotFoundError(f"skill {record.name!r} has no assets "
                                f"(it is a single file)",
                                {"reason": "skill_not_found", "asset": asset,
                                 "hint": hint})
        base = record.dir.resolve()
        target = (record.dir / candidate)
        try:
            resolved = target.resolve()
        except OSError as exc:
            raise NotFoundError(f"asset {asset!r} not found",
                                {"reason": "skill_not_found", "asset": asset,
                                 "hint": str(exc)}) from exc
        if not resolved.is_relative_to(base):
            raise ValidationError(f"asset {asset!r} resolves outside the "
                                  f"skill directory",
                                  {"reason": "skill_not_found", "asset": asset,
                                   "hint": hint})
        if not resolved.is_file():
            raise NotFoundError(f"asset {asset!r} not found in skill "
                                f"{record.name!r}",
                                {"reason": "skill_not_found", "asset": asset,
                                 "hint": "the `assets` list names what exists"})
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise NotFoundError(f"asset {asset!r} is unreadable",
                                {"reason": "skill_not_found", "asset": asset,
                                 "hint": str(exc)}) from exc
        if len(raw) > MAX_SKILL_FILE_BYTES:
            raw = raw[:MAX_SKILL_FILE_BYTES]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"asset {asset!r} is not UTF-8 text",
                                  {"reason": "skill_invalid", "asset": asset,
                                   "hint": str(exc)}) from exc

    # ---------------------------------------------------------------- trust

    def trust_path(self, project: str) -> Path:
        """``<project>/.history/agentcad/skills/trust.json`` — canonical, so
        it is branch-free, never versioned and restore-proof."""
        if self.store is None:
            raise NotFoundError("no project store is configured",
                                {"reason": "skill_not_found"})
        return (self.store.canonical_path_of(project) / ".history"
                / "agentcad" / "skills" / "trust.json")

    def trust_state(self, project: str) -> dict:
        """The local trust document. A corrupt or absent file reads empty."""
        try:
            path = self.trust_path(project)
            raw = path.read_bytes()
        except (NotFoundError, OSError):
            return empty_trust_state()
        if len(raw) > MAX_SKILL_FILE_BYTES:
            return empty_trust_state()
        try:
            doc = json.loads(raw)
        except (ValueError, RecursionError, UnicodeDecodeError):
            return empty_trust_state()
        if not isinstance(doc, dict):
            return empty_trust_state()
        trusted = doc.get("trusted")
        disabled = doc.get("disabled")
        return {
            "version": 1,
            "trusted": {k: v for k, v in trusted.items()
                        if isinstance(k, str) and isinstance(v, str)}
            if isinstance(trusted, dict) else {},
            "disabled": sorted({d for d in disabled if isinstance(d, str)})
            if isinstance(disabled, list) else [],
        }

    def _write_state(self, project: str, state: dict) -> None:
        ProjectStore._atomic_write(
            self.trust_path(project),
            json.dumps(state, indent=2, sort_keys=True).encode(),
        )

    def is_trusted(self, record: SkillRecord, project: str | None) -> bool:
        """Core skills are trusted by construction; a project skill is
        trusted only while its **current digest** is the approved one."""
        if record.layer == "core":
            return True
        if project is None or self.store is None:
            return False
        return self.trust_state(project).get("trusted", {}).get(
            record.name) == record.digest

    def _record_for_state(self, project: str, name: str) -> SkillRecord:
        records = self.records(project)
        if name not in records:
            raise NotFoundError(f"skill {name!r} not found",
                                {"reason": "skill_not_found", "name": name})
        return records[name]

    def _state_entry(self, project: str, name: str) -> dict:
        records, overrides = self._effective(project)
        return self._entry(records[name], overrides.get(name), project)

    def trust(self, project: str, name: str) -> dict:
        """Record the skill's current digest as approved."""
        record = self._record_for_state(project, name)
        state = self.trust_state(project)
        state["trusted"][name] = record.digest
        self._write_state(project, state)
        return self._state_entry(project, name)

    def untrust(self, project: str, name: str) -> dict:
        """Withdraw approval; the skill stays listed and refuses to load."""
        self._record_for_state(project, name)
        state = self.trust_state(project)
        state["trusted"].pop(name, None)
        self._write_state(project, state)
        return self._state_entry(project, name)

    def set_enabled(self, project: str, name: str, enabled: bool) -> dict:
        """Hide (or restore) a skill for this project, whatever its layer."""
        self._record_for_state(project, name)
        state = self.trust_state(project)
        disabled = set(state["disabled"])
        disabled.discard(name) if enabled else disabled.add(name)
        state["disabled"] = sorted(disabled)
        self._write_state(project, state)
        entry = self._state_entry(project, name)
        entry["enabled"] = bool(enabled)
        return entry


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())
