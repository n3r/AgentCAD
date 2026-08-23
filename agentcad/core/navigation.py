"""Navigation metadata grammar: part/instance folders and part tags (PRD-027).

Folders and tags are **manifest metadata**, not directories — a part's script
stays at ``parts/<id>.py`` whatever folder it is filed under, so organizing a
project never rewrites a path, breaks a package materialisation, or moves a
byte in git that a human did not ask to move (design §1, Risk 1).

The two grammars are deliberately asymmetric, and the asymmetry is the whole
of the rule (ruling 9):

* a **folder** is a *display name*. It is stored verbatim — case, spaces and
  all — and validated strictly, because a segment with a stray trailing space
  is a folder that looks identical to another one in the tree and sorts apart
  from it. Matching is case-insensitive, per segment (see `folder_matches`).
* a **tag** is an *identifier*. It is normalized on write — stripped,
  lowercased, de-duplicated preserving first-seen order — because a tag is
  typed over and over and ``M5``/``m5``/`` m5 `` must be one tag, not three.
  What is still invalid *after* normalization (an inner space, ``#``, ``:``)
  is a `ValidationError` naming it, never a silent drop.

This module imports nothing but `model` and the stdlib: the store, the tool
pack, the routes and (from slice 2) the search engine all validate through it,
so it must sit below every one of them.
"""

from __future__ import annotations

import re

from .model import ValidationError

#: One folder segment: starts alphanumeric, then up to 39 more of
#: ``[A-Za-z0-9 _.-]``. Applied with ``fullmatch`` (never ``match``): with
#: ``match`` a trailing newline slips past the ``$``.
FOLDER_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")

#: One tag, AFTER stripping and lowercasing.
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")

#: Maximum ``/``-separated segments in a folder path.
MAX_FOLDER_DEPTH = 8

#: Maximum tags on one part (counted after de-duplication).
MAX_TAGS = 32


def normalize_folder(value) -> str | None:
    """Validate a folder path and return it verbatim, or ``None`` for root.

    ``None`` and ``""`` both mean root — the tree has no separate "no folder"
    and "empty folder" state, and a client that clears a text input sends the
    empty string. Everything else must be a ``/``-joined path of 1..8
    segments matching :data:`FOLDER_SEGMENT_RE` with no leading or trailing
    whitespace in any segment.

    Nothing is stripped or case-folded: a folder is a name a human typed and
    will read back. That is exactly why `" Pistons"` is refused rather than
    quietly repaired — the repaired value would differ from what the caller
    believes it wrote, and two spellings of one folder would coexist in the
    tree.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValidationError(
            f"invalid folder {value!r}: must be a string or null",
            {"folder": value},
        )
    if value == "":
        return None
    segments = value.split("/")
    if len(segments) > MAX_FOLDER_DEPTH:
        raise ValidationError(
            f"invalid folder {value!r}: at most {MAX_FOLDER_DEPTH} segments "
            f"(got {len(segments)})",
            {"folder": value},
        )
    for segment in segments:
        if segment != segment.strip():
            raise ValidationError(
                f"invalid folder {value!r}: segment {segment!r} has leading "
                "or trailing whitespace",
                {"folder": value, "segment": segment},
            )
        if not FOLDER_SEGMENT_RE.fullmatch(segment):
            raise ValidationError(
                f"invalid folder {value!r}: segment {segment!r} must match "
                r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}",
                {"folder": value, "segment": segment},
            )
    return value


def normalize_tags(value) -> list[str]:
    """Normalize a list of tags: strip, lowercase, de-duplicate, validate.

    Order is first-seen (a tag list is something a human reads, not a set),
    and the de-duplication happens *before* the :data:`MAX_TAGS` count so
    pasting the same tag twice never costs a slot.

    ``None`` is NOT accepted. Every caller in this PRD uses ``None`` to mean
    "leave the tags alone" and ``[]`` to mean "clear them"; making this
    function turn ``None`` into ``[]`` would let one missing guard silently
    erase a part's tags.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValidationError(
            f"invalid tags {value!r}: must be an array of strings",
            {"tags": value},
        )
    out: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if isinstance(raw, bool) or not isinstance(raw, str):
            raise ValidationError(
                f"invalid tag {raw!r}: must be a string", {"tag": raw})
        tag = raw.strip().lower()
        if not TAG_RE.fullmatch(tag):
            raise ValidationError(
                f"invalid tag {raw!r}: must match [a-z0-9][a-z0-9_.-]{{0,31}} "
                "after stripping and lowercasing",
                {"tag": raw},
            )
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValidationError(
            f"too many tags: {len(out)} (max {MAX_TAGS})",
            {"count": len(out), "max": MAX_TAGS},
        )
    return out


def folder_matches(folder: str | None, query: str) -> bool:
    """Does ``folder`` sit at, or under, ``query``? (case-insensitive)

    The comparison is **segment-wise**, never a string prefix: ``"a/b"`` is
    under ``"a"`` but not under ``"a/bc"``. An empty query is the empty
    prefix and matches every folder including root — search's ``folder:``
    term inherits that, so a blank value never silently means "root only".

    **Total over the stored value, strict about the query** (the
    `PartRecord.config_params` discipline). ``folder`` comes off a manifest a
    hand edit or a merge can shape, so a non-string one is read as root and
    simply does not match — a corrupt entry must not 500 the search that is
    scanning a thousand parts. ``query`` comes from a caller, so a non-string
    one is a `ValidationError`: returning ``False`` for it would silently drop
    every row from a result set and read as "no matches" rather than "you
    passed the wrong type".
    """
    if not isinstance(query, str):
        raise ValidationError(
            f"folder query must be a string, got {type(query).__name__}",
            {"query": query},
        )
    wanted = [s.lower() for s in query.strip("/").split("/") if s != ""]
    if not wanted:
        return True
    stored = folder if isinstance(folder, str) else ""
    have = [s.lower() for s in stored.split("/") if s != ""]
    return have[:len(wanted)] == wanted
