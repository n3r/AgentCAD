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

**Trust is keyed by the tree digest**, not by the name: a ``git pull``
that rewrites a trusted skill is the attack this exists for, so editing a
trusted skill makes it untrusted again — and the digest covers the SKILL.md
**plus every asset** (:func:`tree_digest`), because "copy
``snippets/mount.py``" is the sentence a human approves and the snippet is
what the agent actually runs. Core skills are trusted by construction; an
untrusted project skill is *listed* (so a human can see it) and refused on
load with ``skill_untrusted`` — with its **description and triggers
redacted** on every agent surface (:data:`UNREVIEWED_DESCRIPTION`), because
those two fields are text somebody else wrote and they used to reach a system
prompt verbatim.

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
* Every read is **size-checked before it happens** (:func:`_read_capped`) and
  never allocates more than ``limit + 1`` bytes. A 600 MB sparse ``SKILL.md``
  is a file any project directory can plant, and ``read_bytes()`` on it cost
  630 MB of RSS *per index call*.
* A **symlink is never followed** — not the skill directory, not the flat
  file, not an asset (:func:`scan_layer`, :func:`walk_files`) — and a record
  whose ``resolve()`` leaves the layer root is skipped. A skill directory
  linked outside the project made its *neighbours* readable as assets.
* Search matches **token sets, not substrings** (:meth:`SkillLibrary.search`):
  one-letter tokens matched everything, so ``"make a snap fit lid"`` answered
  ``robust-parametrics``.
* The three trust writers are serialized per project
  (:meth:`SkillLibrary._trust_scope`, ``RLock`` + ``fcntl.flock``, the
  ``packages._index_scope`` shape): they are read-modify-write over one JSON
  document and the server is threaded.
* Parsed frontmatter is memoised per ``(path, mtime_ns, size)``, and so is
  each file's hash inside the tree digest. A rewrite that keeps both the size
  and the nanosecond timestamp would be served from the memo; that is the
  usual stat-based-cache bargain.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Callable

from .model import NotFoundError, ValidationError
from .project import ProjectStore

try:                                    # POSIX only; Windows keeps the RLock
    import fcntl
except ImportError:                     # pragma: no cover - platform-dependent
    fcntl = None

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

#: What an **untrusted project skill's** description reads as on every agent
#: surface. A project skill's frontmatter is text somebody else wrote, and
#: ``description``/``triggers`` are the two fields that flow verbatim into a
#: system prompt (:meth:`SkillLibrary.compact_index`) and into a tool result
#: (``list_skills``). Until a human has approved the file, an agent gets the
#: *name* and this sentence — enough to ask for a review, not enough to be
#: instructed by. The human surfaces (the Skills panel's list, the review
#: read) pass ``redact_untrusted=False`` and see the real metadata.
UNREVIEWED_DESCRIPTION = (
    "unreviewed project skill — a human must approve it in the Skills panel "
    "before an agent can load it")

#: What an unreviewed project skill's parse problem reads as on an agent
#: surface. ``invalid`` embeds the offending source line and the declared
#: value (``parse_frontmatter``/``_meta_from``), so a deliberately broken
#: file would otherwise ship arbitrary author prose past the redaction
#: through ``list_skills`` and through ``resolve``'s ``skill_invalid`` refusal
#: — which fires BEFORE ``load``'s trust check.
UNREVIEWED_PROBLEM = (
    "the file does not parse (details withheld until a human reviews it in "
    "the Skills panel)")

#: The same fact on one compact-index line, where the format is
#: ``- name (…)`` rather than ``- name — description``.
UNREVIEWED_COMPACT = (
    "unreviewed project skill — not loadable until a human approves it in "
    "the Skills panel")

#: How many omitted headings a ``load`` payload names before it summarises the
#: rest. A hostile body of 6 000 ``## `` lines turned a capped 24 kB load into
#: a 768 kB tool result — the cap on the *content* is not a cap on the
#: *report*, so this is the report's own.
MAX_OMITTED_SECTIONS = 40


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


class SkillTooLarge(Exception):
    """A file over the ceiling — raised *before* its bytes are allocated.

    Deliberately not an ``OSError``: every caller that reads a skill file
    already handles ``OSError`` as "unreadable", and "the file is 600 MB" is a
    different answer from "permission denied".
    """

    def __init__(self, path: Path, size: int, limit: int):
        self.path = path
        self.size = size
        self.limit = limit
        super().__init__(f"the file is {size} bytes; the ceiling is "
                         f"{limit} bytes")


def _read_capped(path: Path, limit: int = MAX_SKILL_FILE_BYTES) -> bytes:
    """The file's bytes, or :class:`SkillTooLarge` — never more than
    ``limit + 1`` bytes in memory.

    Two checks, because one is not enough. ``stat`` first, so a 600 MB file
    (sparse or not) costs a ``stat`` and nothing else — ``read_bytes()`` on it
    cost 630 MB of RSS *per index call*, which is a memory bomb any project
    directory could plant. Then ``read(limit + 1)``, because a file can grow
    between the ``stat`` and the read, and because the extra byte is what
    tells "exactly at the ceiling" from "over it".
    """
    st = path.stat()
    if st.st_size > limit:
        raise SkillTooLarge(path, st.st_size, limit)
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise SkillTooLarge(path, len(raw), limit)
    return raw


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
    invalid: str | None

    @cached_property
    def digest(self) -> str:
        """The **tree** digest (:func:`tree_digest`), computed on first use.

        Lazy because most reads never ask: a core skill is trusted by
        construction, so ``index()`` over the shipped library short-circuits
        before the digest and pays one ``stat`` per SKILL.md instead of one
        per file of every skill (measured: 5.2 ms → 1.6 ms per call over 16
        skills). A record is rebuilt on every ``records()`` call, so the cache
        is per read and never stale.
        """
        return tree_digest(self.path, self.dir)

    @property
    def requires(self) -> tuple[str, ...]:
        return () if self.meta is None else self.meta.requires


@dataclass(frozen=True)
class SkillBudget:
    """Caps on what a skill costs an agent's context.

    ``max_loaded``/``max_loaded_chars`` are the chat engine's LRU bounds
    (PRD-029 §3); ``max_skill_chars`` is this module's truncation cap.

    **Normalized on construction**: ``max_skill_chars`` is clamped to
    ``max_loaded_chars``, so one skill capped at the load cap always fits the
    session budget. Unclamped, ``max_loaded_chars=10_000`` with the default
    ``max_skill_chars=24_000`` produced a skill the engine could never keep —
    it evicted everything, then kept the one over-budget entry anyway (the
    never-evict-the-keep rule), so the budget was silently exceeded by a
    config that looked stricter. Every construction path goes through here,
    :meth:`from_config` included.
    """

    max_loaded: int = 4
    max_loaded_chars: int = 40_000
    max_skill_chars: int = 24_000

    #: The share of ``max_loaded_chars`` one skill's *content* may take. The
    #: engine books the whole serialized tool result — JSON escaping, the
    #: capped ``omitted_sections`` list, the asset list — which runs up to
    #: ~15 % over the content, so a content cap equal to the session cap
    #: would leave a single just-loaded skill above the bound with nothing to
    #: evict (re-review finding C). 0.8 keeps the defaults (24 000 / 40 000)
    #: untouched and makes "one capped skill always fits" true.
    ENVELOPE_SHARE = 0.8

    def __post_init__(self) -> None:
        ceiling = max(1, int(self.max_loaded_chars * self.ENVELOPE_SHARE))
        if self.max_skill_chars > ceiling:
            object.__setattr__(self, "max_skill_chars", ceiling)

    @classmethod
    def from_config(cls) -> "SkillBudget":
        """Env > ``~/.agentcad/config.json`` > the defaults above."""
        from ..config import get_skills_budget

        return cls(**get_skills_budget())


# ------------------------------------------------------------- file reading

#: ``(path, mtime_ns, size) -> (meta, body, invalid)``. Bounded and cleared
#: wholesale — it is a speed-up, never a source of truth.
_PARSE_CACHE: dict[tuple[str, int, int], tuple] = {}
_PARSE_CACHE_MAX = 512

#: ``(path, mtime_ns, size) -> sha256 hex`` for one file of a skill tree, on
#: the same stat-keyed bargain as :data:`_PARSE_CACHE`. Without it the tree
#: digest would re-hash every asset of every skill on every ``index()``.
_FILE_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}


def _parse_file(path: Path, name: str) -> tuple:
    """Read and validate one skill file. Never raises."""
    try:
        raw = _read_capped(path)
    except SkillTooLarge as exc:
        return (None, "", str(exc))
    except OSError as exc:
        return (None, "", f"unreadable: {type(exc).__name__}: {exc}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return (None, "", f"not valid UTF-8: {exc}")
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    try:
        meta_map, body = parse_frontmatter(text)
    except SkillFormatError as exc:
        return (None, "", str(exc))
    meta, problem = _meta_from(meta_map, name)
    return (meta, body, problem)


def _file_digest(path: Path) -> str:
    """One file's contribution to a tree digest, memoised on its stat.

    An **oversize** file gets a size marker rather than a hash: reading a
    600 MB asset to hash it is the very allocation :func:`_read_capped`
    exists to refuse, and an oversize file cannot be served either — the
    SKILL.md is ``invalid`` and :meth:`SkillLibrary._read_asset` refuses the
    asset — so two oversize files of the same length being indistinguishable
    here delivers no bytes to any agent.
    """
    try:
        st = path.stat()
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"
    key = (str(path), st.st_mtime_ns, st.st_size)
    cached = _FILE_DIGEST_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        value = hashlib.sha256(_read_capped(path)).hexdigest()
    except SkillTooLarge as exc:
        value = f"oversize:{exc.size}"
    except OSError as exc:
        value = f"unreadable:{type(exc).__name__}"
    if len(_FILE_DIGEST_CACHE) >= _PARSE_CACHE_MAX:
        _FILE_DIGEST_CACHE.clear()
    _FILE_DIGEST_CACHE[key] = value
    return value


def tree_digest(path: Path, dir_: Path | None) -> str:
    """The digest trust is keyed by: **the whole skill, not its SKILL.md**.

    ``sha256`` over ``relpath + "\\0" + sha256(bytes)`` for every file in
    :func:`walk_files` order (sorted relative paths) — the SKILL.md plus every
    asset. A flat ``<name>.md`` is a one-file tree.

    Why the tree and not the file: ``SKILL.md`` says "copy
    ``snippets/mount.py``", the human approves *that*, and a ``git pull``
    then rewrites the snippet. With a file digest the skill still read
    ``trusted: True`` and the agent still copied the new snippet — the
    approval covered the sentence describing the payload and not the payload.
    Adding or removing a file moves the digest too, because the relative path
    is hashed beside the bytes.
    """
    hasher = hashlib.sha256()
    if dir_ is None:
        files = [(path.name, path)]
    else:
        files = []
        for child in walk_files(dir_):
            try:
                files.append((child.relative_to(dir_).as_posix(), child))
            except ValueError:          # pragma: no cover - walk_files bounds it
                continue
        files.sort()
    for relative, child in files:
        hasher.update(relative.encode("utf-8", "surrogatepass"))
        hasher.update(b"\0")
        hasher.update(_file_digest(child).encode("ascii"))
        hasher.update(b"\n")
    return hasher.hexdigest()


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
    """One :class:`SkillRecord`, memoised on ``(path, mtime_ns, size)``.

    The parse is memoised per file; the digest is the **tree** digest and is
    recomputed here on every call — it is a ``stat`` per file of the skill
    plus a hash only for the files that changed (:func:`_file_digest`).
    """
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
    meta, body, invalid = parsed
    return SkillRecord(meta=meta, name=name, layer=layer, path=path, dir=dir_,
                       body=body, invalid=invalid)


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

    **A symlink is skipped, file or directory.** Not "a link that resolves
    outside": a link that happens to resolve inside is still a second name for
    a file the walk already has, so it would be hashed twice into
    :func:`tree_digest` and listed twice as an asset — and the day the link
    target moves, the same relative path serves different bytes.
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
                if entry.is_symlink():
                    continue          # never a link, dir or file
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

    **A layer is the files under its root and nothing else.** A symlinked
    entry is skipped and so is anything whose ``resolve()`` leaves the
    resolved root: ``skills/x -> ~/.ssh`` indexed a skill whose *directory*
    was somebody else's, so ``load(asset=…)`` read its neighbours — the asset
    guard compares against ``record.dir``, which was the link's target and
    therefore "inside". The containment check stays beside the symlink check
    rather than replacing it: a bind mount is not a symlink.
    """
    out: dict[str, SkillRecord] = {}
    try:
        base = root.resolve()
        entries = sorted(root.iterdir())
    except OSError:
        return out

    def inside(path: Path) -> bool:
        try:
            return path.resolve().is_relative_to(base)
        except OSError:
            return False

    flat: dict[str, Path] = {}
    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue
        if entry.is_symlink() or not inside(entry):
            continue
        if entry.is_dir():
            if not NAME_RE.fullmatch(name):
                continue
            skill_md = entry / "SKILL.md"
            if skill_md.is_file() and not skill_md.is_symlink():
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

#: One ``RLock`` per ``trust.json`` path, shared by every ``SkillLibrary``
#: instance in this process (the server builds more than one).
_TRUST_LOCKS: dict[str, threading.RLock] = {}
_TRUST_REGISTRY_LOCK = threading.Lock()


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
               project: str | None, state: dict | None = None) -> dict:
        meta = record.meta
        state = state if state is not None else self._state_for(project)
        return {
            "name": record.name,
            "description": "" if meta is None else meta.description,
            "layer": record.layer,
            "version": "" if meta is None else meta.version,
            "triggers": [] if meta is None else list(meta.triggers),
            "requires": [] if meta is None else list(meta.requires),
            "overrides": overrides,
            "trusted": self.is_trusted(record, project, state),
            "enabled": record.name not in set(state.get("disabled") or ()),
            "invalid": record.invalid,
        }

    def _state_for(self, project: str | None) -> dict:
        """The trust document, or an empty one when there is no project.

        Read **once per call** by everything that builds entries: it used to
        be re-read per record inside ``is_trusted``, so one ``index()`` over a
        40-skill project opened and parsed ``trust.json`` 40 times — and two
        of those reads could disagree with each other.
        """
        return self.trust_state(project) if project else empty_trust_state()

    def _partition(self, project: str | None
                   ) -> tuple[list[dict], list[dict]]:
        """``(index, hidden)`` in one pass.

        Filter order is layering → ``only`` → enabled → capability, so a
        disabled skill reports ``disabled`` even when it also needs a
        capability this installation lacks.
        """
        records, overrides = self._effective(project)
        state = self._state_for(project)
        disabled = set(state.get("disabled") or ())
        caps = self._capabilities()
        index: list[dict] = []
        hidden: list[dict] = []
        for name, record in records.items():
            if self.only is not None and name not in self.only:
                continue
            entry = self._entry(record, overrides.get(name), project, state)
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

    def index(self, project: str | None = None, *,
              redact_untrusted: bool = False) -> list[dict]:
        """Visible entries, name-sorted. Capability-hidden and disabled
        skills are **not** here — :meth:`hidden` reports those.

        ``redact_untrusted`` is the **agent** surface: an unapproved project
        skill's ``description`` and ``triggers`` are text somebody else wrote,
        and they reach a model verbatim (the system prompt's compact index,
        ``list_skills``' result). Redacted, the agent still sees that the
        skill exists and that a human has to approve it — which is the only
        part of it that is ours. The default is False because the human
        surfaces (the Skills panel, the review read) need the real metadata to
        review *at all*.
        """
        return _redact(self._partition(project)[0], redact_untrusted)

    def hidden(self, project: str | None = None) -> list[dict]:
        """Why a skill an agent read about is not in the index."""
        return self._partition(project)[1]

    def search(self, query: str, project: str | None = None, *,
               redact_untrusted: bool = False) -> tuple[list[dict], bool]:
        """``(entries, matched)`` — the deterministic ranking of spec §2.

        **Token sets, not substrings.** The query is split into ``[a-z0-9]+``
        tokens of three characters or more (a query that is *all* short tokens
        keeps them, so ``m8`` still searches); then

        * the name scores **100** when the query is exactly the name, else
          **60** when a token equals one of its hyphen-parts or a token of
          four characters or more appears in it;
        * every trigger whose own token set meets the query's scores **40**;
        * every description token the query also has scores **10**.

        Ties break by layer (project first) then name, so the answer is
        deterministic. **No hit returns the full index** with
        ``matched=False``, so an agent still sees what exists.

        Substring matching is what this replaced, and it was not a detail:
        ``"make a snap fit lid"`` tokenised to include ``"a"``, ``"a"`` is
        inside ``clamp``, ``safe_fillet`` and ten other of its triggers, and
        over the shipped library ``robust-parametrics`` beat ``snap-fits``
        **550 to 360** (measured). The same query now scores ``snap-fits``
        200 and ``robust-parametrics`` 0: a one-letter token cannot match
        anything at all.

        ``redact_untrusted`` is :meth:`index`'s, applied to the *result*: the
        ranking still reads the real metadata (an unreviewed skill has to stay
        findable by the words its author chose, or a human cannot be pointed
        at it), and only what is handed back is redacted.
        """
        entries = self.index(project)
        q = (query or "").strip().lower()
        tokens = _query_tokens(q)
        if not q or not tokens:
            return _redact(entries, redact_untrusted), False
        token_set = set(tokens)
        long_tokens = [t for t in token_set if len(t) >= 4]
        scored: list[tuple[int, int, str, dict]] = []
        for entry in entries:
            name = entry["name"]
            score = 0
            if q == name:
                score += 100
            elif (token_set & set(name.split("-"))
                    or any(t in name for t in long_tokens)):
                score += 60
            for trigger in entry["triggers"]:
                if token_set & set(_tokens(str(trigger))):
                    score += 40
            score += 10 * len(set(_tokens(entry["description"])) & token_set)
            if score:
                rank = LAYER_ORDER.index(entry["layer"])
                scored.append((-score, -rank, name, entry))
        if not scored:
            return _redact(entries, redact_untrusted), False
        scored.sort(key=lambda row: (row[0], row[1], row[2]))
        return _redact([row[3] for row in scored], redact_untrusted), True

    def compact_index(self, project: str | None = None, limit: int = 40) -> str:
        """The system-context block: ``- name — description`` per line.

        This string goes into a **system prompt**, so an unreviewed project
        skill contributes its name and :data:`UNREVIEWED_COMPACT` and nothing
        else. Its description is the one field an attacker controls and the
        one field that used to be copied here verbatim.
        """
        entries = self.index(project, redact_untrusted=True)
        if not entries:
            return ""
        lines = []
        for entry in entries[:limit]:
            if _is_unreviewed(entry):
                lines.append(f"- {entry['name']} ({UNREVIEWED_COMPACT})")
            else:
                lines.append(f"- {entry['name']} — {entry['description'][:120]}")
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
            # An unreviewed project skill's problem text is author-controlled
            # (it quotes the offending line), and this refusal fires before
            # the trust check in ``load`` — so it is withheld the same way the
            # description is.
            problem = record.invalid
            if not self.is_trusted(record, project, state=state):
                problem = UNREVIEWED_PROBLEM
            raise ValidationError(
                f"skill {name!r} cannot be read: {problem}",
                {"reason": "skill_invalid", "name": name,
                 "problem": problem,
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
             asset: str | None = None, *, enforce_trust: bool = True) -> dict:
        """The ``load_skill`` payload: capped content plus provenance.

        ``enforce_trust=False`` skips **only** the trust check, and exists for
        exactly one caller: the human review read behind
        ``GET /projects/{p}/skills/{name}``. A person cannot approve a skill
        they are not allowed to read, and "trust it to see it" is the wrong
        order. Everything else still applies — a disabled, invalid or
        capability-gated skill is refused the same way, because those are not
        about who is asking.
        """
        record = self.resolve(name, project)
        if enforce_trust and not self.is_trusted(record, project):
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
            omitted = _cap_omitted(omitted)
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
        included — the check compares ``resolve()``d paths), naming a file the
        bounded walk actually lists, and not the skill's own file.

        **Not the skill's own file**: ``asset="SKILL.md"`` returned the whole
        body with no section truncation and no ``truncated`` flag, so the one
        cap on what a skill costs an agent's context had a bypass spelled
        eight characters long.

        **In the walk**: what :func:`walk_files` lists is what
        :func:`tree_digest` covers, so serving a file outside it would serve
        bytes a human's approval never saw.
        """
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
        if resolved == record.path.resolve():
            raise NotFoundError(
                f"asset {asset!r} is the skill's own file",
                {"reason": "skill_not_found", "asset": asset,
                 "hint": "load the skill without `asset` to read its body — "
                         "that path applies the section budget, this one "
                         "would not"})
        if not resolved.is_file():
            raise NotFoundError(f"asset {asset!r} not found in skill "
                                f"{record.name!r}",
                                {"reason": "skill_not_found", "asset": asset,
                                 "hint": "the `assets` list names what exists"})
        if resolved not in {child.resolve()
                            for child in walk_files(record.dir)}:
            raise NotFoundError(
                f"asset {asset!r} is not one of skill {record.name!r}'s "
                f"listed files",
                {"reason": "skill_not_found", "asset": asset,
                 "hint": "the `assets` list names what exists; the walk is "
                         "bounded, and only what it lists is covered by the "
                         "trust digest"})
        try:
            raw = _read_capped(resolved)
        except SkillTooLarge as exc:
            raise ValidationError(
                f"asset {asset!r} is too large to serve: {exc}",
                {"reason": "skill_invalid", "asset": asset,
                 "hint": f"the ceiling is {MAX_SKILL_FILE_BYTES} bytes"}
            ) from exc
        except OSError as exc:
            raise NotFoundError(f"asset {asset!r} is unreadable",
                                {"reason": "skill_not_found", "asset": asset,
                                 "hint": str(exc)}) from exc
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

    def trust_lock_path(self, project: str) -> Path:
        """The advisory lock file, beside ``trust.json``.

        Beside it, not outside it: unlike a package index (someone else's git
        repo, which `_index_scope` refuses to litter), ``.history/agentcad/
        skills/`` is ours and is never versioned.
        """
        return self.trust_path(project).with_name("trust.lock")

    @contextmanager
    def _trust_scope(self, project: str):
        """Serialize a read-modify-write of one project's ``trust.json``.

        ``trust`` / ``untrust`` / ``set_enabled`` were read-modify-write with
        nothing between the read and the write: forty concurrent approvals
        (two browser tabs is enough, and the server is threaded) each read the
        same document and each wrote its own single change over the others'.
        The user was told forty times that the skill was trusted and the file
        held one of them.

        Two layers, `LocalIndex._index_scope`'s shape and for its reasons: a
        ``threading.RLock`` for two threads in one process, an ``fcntl.flock``
        for two processes (``docker compose exec`` beside a running server).
        The flock is advisory and best-effort — no ``fcntl``, or a filesystem
        without it, degrades to the in-process lock, which is stated rather
        than hidden.
        """
        path = self.trust_path(project)
        try:
            key = str(path.resolve())
        except OSError:                 # pragma: no cover - resolve is lenient
            key = str(path)
        with _TRUST_REGISTRY_LOCK:
            lock = _TRUST_LOCKS.get(key)
            if lock is None:
                lock = _TRUST_LOCKS[key] = threading.RLock()
        with lock:
            if fcntl is None:           # pragma: no cover - POSIX in CI
                yield
                return
            handle = None
            try:
                lock_path = self.trust_lock_path(project)
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = open(lock_path, "a+b")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError:
                if handle is not None:
                    handle.close()
                yield
                return
            try:
                yield
            finally:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()

    def trust_state(self, project: str) -> dict:
        """The local trust document. A corrupt, absent or oversize file reads
        empty — and an oversize one is never allocated."""
        try:
            path = self.trust_path(project)
            raw = _read_capped(path)
        except (NotFoundError, SkillTooLarge, OSError):
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

    def is_trusted(self, record: SkillRecord, project: str | None,
                   state: dict | None = None) -> bool:
        """Core skills are trusted by construction; a project skill is
        trusted only while its **current tree digest** is the approved one.

        ``state`` is the already-read trust document — the index reads it once
        per call and hands it down, rather than re-reading it per record.
        """
        if record.layer == "core":
            return True
        if project is None or self.store is None:
            return False
        state = state if state is not None else self.trust_state(project)
        return state.get("trusted", {}).get(record.name) == record.digest

    def _record_for_state(self, project: str, name: str) -> SkillRecord:
        records = self.records(project)
        if name not in records:
            raise NotFoundError(f"skill {name!r} not found",
                                {"reason": "skill_not_found", "name": name})
        return records[name]

    def _state_entry(self, project: str, name: str, state: dict) -> dict:
        records, overrides = self._effective(project)
        return self._entry(records[name], overrides.get(name), project, state)

    def trust(self, project: str, name: str) -> dict:
        """Record the skill's current digest as approved."""
        record = self._record_for_state(project, name)
        with self._trust_scope(project):
            state = self.trust_state(project)
            state["trusted"][name] = record.digest
            self._write_state(project, state)
            return self._state_entry(project, name, state)

    def untrust(self, project: str, name: str) -> dict:
        """Withdraw approval; the skill stays listed and refuses to load."""
        self._record_for_state(project, name)
        with self._trust_scope(project):
            state = self.trust_state(project)
            state["trusted"].pop(name, None)
            self._write_state(project, state)
            return self._state_entry(project, name, state)

    def set_enabled(self, project: str, name: str, enabled: bool) -> dict:
        """Hide (or restore) a skill for this project, whatever its layer."""
        self._record_for_state(project, name)
        with self._trust_scope(project):
            state = self.trust_state(project)
            disabled = set(state["disabled"])
            disabled.discard(name) if enabled else disabled.add(name)
            state["disabled"] = sorted(disabled)
            self._write_state(project, state)
            return self._state_entry(project, name, state)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


#: Below this a token carries no signal — ``a``, ``of``, ``17`` — and it is
#: what turned substring matching into noise.
MIN_TOKEN_CHARS = 3


def _query_tokens(text: str) -> list[str]:
    """The query's tokens, short ones dropped — unless they are all short.

    The fallback matters: ``m8``, ``h7`` and ``r5`` are real searches, and
    dropping every token would answer "no hit, here is everything" for them.
    """
    tokens = _tokens(text)
    long = [t for t in tokens if len(t) >= MIN_TOKEN_CHARS]
    return long or tokens


def _is_unreviewed(entry: dict) -> bool:
    """An entry a human has not approved: a project skill, not yet trusted."""
    return entry.get("layer") == "project" and not entry.get("trusted")


def _redact(entries: list[dict], redact: bool) -> list[dict]:
    """Copies with every unreviewed entry's author-controlled text removed.

    Copies, never in-place: the same entry dicts back the human surface, and
    a mutation here would redact it too.
    """
    if not redact:
        return entries
    out = []
    for entry in entries:
        if _is_unreviewed(entry):
            entry = dict(entry, description=UNREVIEWED_DESCRIPTION,
                         triggers=[])
            if entry.get("invalid"):
                entry["invalid"] = UNREVIEWED_PROBLEM
        out.append(entry)
    return out


def _cap_omitted(omitted: list[str]) -> list[str]:
    """``omitted_sections``, capped at :data:`MAX_OMITTED_SECTIONS`."""
    if len(omitted) <= MAX_OMITTED_SECTIONS:
        return omitted
    rest = len(omitted) - MAX_OMITTED_SECTIONS
    return omitted[:MAX_OMITTED_SECTIONS] + [f"…and {rest} more sections"]
