"""Part search: the query grammar, the pure matcher, and the scanning engine
(PRD-027 FR3, design §2).

**On-demand scan with a stat-validated memo, not an inverted index** (ruling
2). A 1 000-part manifest is ~300 KB of JSON and its scripts are ~2 MB of
text; reading both costs tens of milliseconds cold and nothing warm, while a
maintained index would be a second source of truth that every write path —
`write_script`, `use_part`, a branch checkout, a merge, an undo, a restore —
would have to remember to invalidate. A memo keyed on ``(mtime_ns, size)``
cannot go stale: whatever changed the bytes changed the key, whoever changed
them and however they did it.

Three layers, and the split is deliberate:

* :func:`parse` — the grammar, a pure function of the query string. Every
  refusal lives here, so a caller never gets "no results" for what was really
  a typo.
* :func:`matches` / :func:`rank` — the matcher, pure functions of one row, one
  script text and a parsed query. They are what `frontend/js/query_model.js`
  ports (slice 5) and what `tests/fixtures/search_queries.json` pins: the
  browser answers metadata-only queries itself, the server answers anything
  touching script text, and the two must agree row for row and rank for rank.
* :class:`Engine` — the I/O: manifest rows, script text, the memos, and the
  ranking/limit/snippet assembly.

A result row says what it ``matched_on`` and carries a ``snippet`` when the
script body is the only **content** that matched — never "the only source at
all": a field term (`state:ok`, a filter chip) is in every returned row's
evidence and explains nothing about any one of them (`script_only`).

**Zero kernel calls.** Organizing and finding are not building: nothing here
touches `ensure_mesh`, `_ensure_built` or `_cache_key`, and a search over a
project whose parts have never been built is an ordinary, complete answer
(``state: unbuilt`` is a result, not an error). `tests/test_search.py` asserts
that with a counting kernel rather than trusting the reading.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .model import ValidationError
from .navigation import _safe, folder_matches
from .packages import provenance

#: The fields a ``field:value`` term may name. Order is the order the grammar
#: sentence lists them in, and `parse` refuses anything else by name.
FIELDS = ("tag", "material", "state", "kind", "folder", "id", "label")

#: The build states a part can be in, as the SERVER knows them. There is no
#: "building": that is a client notion (a request is in flight), and admitting
#: it here would let a query ask the manifest a question only the browser can
#: answer.
STATES = ("ok", "error", "unbuilt")

#: What a part *is*. ``package`` is not a manifest kind — it is a script part
#: whose script carries a package provenance header (see `Engine._kind`).
KINDS = ("script", "reference", "package")

#: Where a hit came from, in ranking order. This tuple is also the canonical
#: order of a row's ``matched_on`` list, so two engines that found the same
#: evidence report it identically.
SOURCES = ("id", "label", "tag", "material", "folder", "state", "kind", "script")

#: Rank of a hit by its best source: a name match beats a tag, a tag beats a
#: material, a structured filter beats a body hit, and the body is last. Ties
#: fall back to manifest order (`Engine.search` sorts on `(rank, index)`).
RANKS = {"id": 0, "label": 0, "tag": 1, "material": 2,
         "folder": 3, "state": 3, "kind": 3, "script": 4}

#: The sources a free-text term can hit that are NOT the script body. The
#: snippet attaches when ``script`` is in ``matched_on`` and none of these
#: are — "the script is the only CONTENT match" — never "the only match at
#: all": a field term or a filter chip is in every returned row's
#: ``matched_on``, so testing for ``["script"]`` exactly suppressed the
#: snippet on `state:ok counterbore` and on every free-text query the UI
#: sends alongside a filter chip, which is most of them.
CONTENT_SOURCES = frozenset({"id", "label", "tag", "material"})

#: The rank of a row that matched with no positive evidence — the empty query,
#: or a query of nothing but negations. Everything sorts above it, and within
#: it manifest order decides.
NO_EVIDENCE_RANK = max(RANKS.values()) + 1

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

#: A script-only hit carries this many characters of context, whitespace
#: collapsed so it is one line in a result list.
SNIPPET_CHARS = 120

#: The keys `search`'s structured ``filters`` argument accepts — the subset of
#: :data:`FIELDS` an agent (or the UI's filter chips) would send without
#: wanting to quote a string.
FILTER_KEYS = ("tag", "material", "state", "kind", "folder")

#: One paragraph, one constant. It is quoted into the `search_parts` tool
#: description and into the route's refusals, so the documentation of this
#: grammar cannot drift from the parser (the `materials_query` precedent).
GRAMMAR = (
    "Query grammar: whitespace-separated terms, ANDed together. A bare word is "
    "free text and matches a part's id, label, tags, material id and script "
    "text (case-insensitive substring). 'field:value' restricts one field, "
    "where field is one of tag, material, state, kind, folder, id, label — tag "
    "and material are exact on the id, state is one of ok/error/unbuilt, kind "
    "is one of script/reference/package (package = a part whose script carries "
    "a package provenance header), folder matches that folder and everything "
    "under it (case-insensitive, whole segments, so a/b does not match a/bc), "
    "and id/label are substrings. A leading '-' negates a term (-tag:draft). "
    "Double quotes group a phrase (\"m5 boss\", folder:\"Left side\"), and a "
    "token that starts with a quote is always free text, never a field. "
    "Repeating a field ANDs it: tag:a tag:b needs both. The empty query "
    "matches every part in manifest order. An unknown field, an unknown state "
    "or kind value, an empty value and an unterminated quote are each a "
    "validation_error."
)

#: A ``field:`` prefix is a bare identifier — and once a token HAS one, an
#: unknown name is refused, not quietly demoted to free text: ``http://x``
#: and ``C:/tmp`` are "unknown search field" errors, deliberately (a typo an
#: agent cannot see is worse than an error it can). What stays free text is a
#: head this pattern does not match at all, like ``1:2`` or ``:x``. The escape
#: hatch for searching a literal colon is quoting the token: ``"http://x"``.
_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Cap on the script memo, counted in entries. One project's worth of scripts
#: is nothing; a long-lived server that has searched fifty projects should not
#: hold all of them forever. Overflow clears the whole memo rather than
#: evicting cleverly: the next search re-reads (tens of milliseconds), and an
#: LRU here would be a second cache policy to get wrong.
_MAX_SCRIPT_MEMO = 4096

#: Cap on the row memo, counted in projects. Same policy as the script memo
#: and the same reason: a long-lived server should not hold the parsed rows of
#: every project anyone has ever searched, and re-deriving one is a manifest
#: read.
_MAX_ROWS_MEMO = 256


# ------------------------------------------------------------------ grammar

def _refuse(message: str, details: dict):
    """A grammar refusal, with the grammar attached.

    Every refusal `parse` can raise carries :data:`GRAMMAR` in its details, so
    the route's 422 and the tool's ``validation_error`` both hand the caller
    the rules they just broke instead of a bare "unknown search field". It is
    the one grammar string (the `materials_query` precedent): a client that
    renders ``details.grammar`` cannot show a stale copy of it.
    """
    return ValidationError(message, {**details, "grammar": GRAMMAR})


@dataclass(frozen=True)
class Term:
    """One term. ``field`` is ``None`` for free text; ``value`` is lowercased
    except for ``folder``, which keeps the case a human typed because
    `navigation.folder_matches` case-folds both sides itself."""

    field: str | None
    value: str
    negate: bool = False


@dataclass(frozen=True)
class Query:
    """A parsed query: terms ANDed together. ``Query(())`` — the empty query —
    matches every part, which is what makes an empty filter box a listing
    rather than an error."""

    terms: tuple[Term, ...] = ()

    @property
    def free_text(self) -> tuple[Term, ...]:
        return tuple(t for t in self.terms if t.field is None)


def parse(query: str) -> Query:
    """The query string as a :class:`Query`, or a `ValidationError`.

    Refusals are the point (design §2): an agent that types ``colour:red`` or
    ``state:building`` must be told, not handed a silently different search.
    The five refusals are an unknown field, an unknown ``state``/``kind``
    value, an empty value, an unterminated quote, and a non-string query.
    """
    if query is None:
        return Query(())
    if not isinstance(query, str):
        raise _refuse(f"query must be a string, got {type(query).__name__}",
                      {"query": _safe(query)})
    return Query(tuple(_term(pieces) for pieces in _tokenize(query)))


def _tokenize(query: str) -> list[list[tuple[str, bool]]]:
    """Split on whitespace, honouring double quotes.

    A token is a list of ``(text, quoted)`` pieces rather than a string,
    because ``folder:"left side"`` and ``"folder:left side"`` are different
    queries and only the piece boundaries tell them apart.
    """
    tokens: list[list[tuple[str, bool]]] = []
    pieces: list[tuple[str, bool]] = []
    buf: list[str] = []
    quoted = False
    in_quote = False

    def flush_piece() -> None:
        nonlocal buf, quoted
        if buf or quoted:
            pieces.append(("".join(buf), quoted))
        buf = []
        quoted = False

    def flush_token() -> None:
        nonlocal pieces
        flush_piece()
        if pieces:
            tokens.append(pieces)
        pieces = []

    for char in query:
        if char == '"':
            flush_piece()
            in_quote = not in_quote
            quoted = in_quote
            continue
        if char.isspace() and not in_quote:
            flush_token()
            continue
        buf.append(char)
    if in_quote:
        raise _refuse(
            "unterminated quote in query: every \" needs a closing \"",
            {"query": _safe(query)})
    flush_token()
    return tokens


def _term(pieces: list[tuple[str, bool]]) -> Term:
    """One token's pieces as a :class:`Term`."""
    head, head_quoted = pieces[0]
    tail = "".join(text for text, _ in pieces[1:])
    if head_quoted:
        # A token that opens with a quote is free text whatever it contains —
        # the escape hatch for searching a literal colon.
        return _free_term(head + tail, False, pieces)
    negate = head.startswith("-")
    if negate:
        head = head[1:]
    field, sep, rest = head.partition(":")
    if not sep or not _FIELD_RE.fullmatch(field):
        return _free_term(head + tail, negate, pieces)
    if field.lower() not in FIELDS:
        raise _refuse(f"unknown search field {_safe(field)!r}",
                      {"field": _safe(field), "fields": list(FIELDS)})
    return field_term(field.lower(), rest + tail, negate=negate)


def _free_term(value: str, negate: bool, pieces) -> Term:
    """A free-text term. Refused when it is empty or nothing but whitespace.

    ``value`` is NOT stripped — a quoted `" boss"` searches for the space too,
    and a caller who typed it meant it. What is refused is a term with no
    content at all (``-``, ``""``), which would otherwise match everything (or,
    negated, nothing) and silently decide the whole query.
    """
    if not value.strip():
        raise _refuse(
            "empty search term: a '-' or a \"\" with nothing in it matches "
            "nothing and hides the rest of the query",
            {"term": _safe("".join(text for text, _ in pieces))})
    return Term(None, value.lower(), negate)


def field_term(field: str, value, *, negate: bool = False) -> Term:
    """Build (and validate) one ``field:value`` term.

    Shared by the parser and by `Engine.search`'s structured ``filters``, so a
    filter object and the equivalent query string refuse identically — an
    empty value is refused in **both**, which matters most for ``folder``:
    `navigation.folder_matches` reads an empty query as the empty prefix and
    matches everything, so a blank ``folder:`` would silently widen a search
    to the whole project instead of narrowing it.
    """
    if field not in FIELDS:
        raise _refuse(f"unknown search field {_safe(field)!r}",
                      {"field": _safe(field), "fields": list(FIELDS)})
    if not isinstance(value, str):
        # `_safe`, never the value itself: a filter object is a caller's JSON,
        # so `{"tag": NaN}` reaches here nested one level below the registry's
        # type check — and a NaN echoed into `details` is not JSON, which made
        # this refusal an HTTP 500 (`navigation._safe`).
        raise _refuse(
            f"{field} must be a string, got {type(value).__name__}",
            {"field": field, "value": _safe(value)})
    value = value.strip()
    if not value:
        raise _refuse(
            f"{field}: has no value — remove the term or give it one",
            {"field": field})
    # Folder keeps its case (the match case-folds); everything else compares
    # against values that are lowercase by construction.
    if field == "folder":
        if not value.strip("/ "):
            # `folder_matches` strips slashes before splitting, so "/" and
            # "//" parse to the EMPTY prefix and match every folder — the same
            # silent widening an empty value would cause, one character later.
            raise _refuse(
                f"folder: has no value — {_safe(value)!r} is only separators",
                {"field": field, "value": _safe(value)})
    else:
        value = value.lower()
    if field == "state" and value not in STATES:
        raise _refuse(f"unknown state {_safe(value)!r}",
                      {"field": field, "values": list(STATES)})
    if field == "kind" and value not in KINDS:
        raise _refuse(f"unknown kind {_safe(value)!r}",
                      {"field": field, "values": list(KINDS)})
    return Term(field, value, negate)


# ------------------------------------------------------------------ matcher

def matches(row: dict, script_text: str, query: Query) -> list[str] | None:
    """``matched_on`` for a row, or ``None`` when the row is out.

    **Pure**, and total over ``row``: the values come off a manifest a hand
    edit or a merge can shape, so a non-string label or a tag list that is not
    a list simply does not match — a corrupt entry must not raise in the
    middle of a scan over a thousand parts (the `folder_matches` discipline).

    ``script_text`` is lowered **lazily**: a metadata-only query never touches
    it, which is what keeps a filter over 1 000 parts off the ~2 MB of script
    text those parts add up to.

    An empty query answers ``[]`` — matched, with no evidence — not ``None``.
    """
    lowered: list[str] = []

    def script() -> str:
        if not lowered:
            lowered.append(script_text.lower()
                           if isinstance(script_text, str) else "")
        return lowered[0]

    return _match(row, script, query)


def _match(row: dict, script, query: Query) -> list[str] | None:
    """`matches`, with the lowered script text behind a **getter**.

    The getter is the whole reason this is split out: `matches` takes the raw
    text and lowers it lazily (which is the pure signature the fixture and the
    JS port use), while `Engine` hands over its memoized lowered copy and
    lowers nothing per query — ~2 MB per call over a 1 000-part project.
    """
    found: set[str] = set()
    for term in query.terms:
        hit = _sources(row, script, term)
        if term.negate:
            if hit:
                return None
        elif not hit:
            return None
        else:
            found |= hit
    return [source for source in SOURCES if source in found]


def _sources(row: dict, script, term: Term) -> set[str]:
    """Which fields of ``row`` this one term hits (empty set = no hit)."""
    value = term.value
    if term.field is None:
        hit = set()
        if value in _text(row, "id"):
            hit.add("id")
        if value in _text(row, "label"):
            hit.add("label")
        if any(value in tag for tag in _tags(row)):
            hit.add("tag")
        if value in _text(row, "material"):
            hit.add("material")
        if value in script():
            hit.add("script")
        return hit
    if term.field == "tag":
        return {"tag"} if value in _tags(row) else set()
    if term.field == "material":
        return {"material"} if _text(row, "material") == value else set()
    if term.field == "state":
        return {"state"} if _text(row, "state") == value else set()
    if term.field == "kind":
        return {"kind"} if _text(row, "kind") == value else set()
    if term.field == "folder":
        return {"folder"} if folder_matches(row.get("folder"), value) else set()
    return ({term.field} if value in _text(row, term.field) else set())


def _text(row: dict, key: str) -> str:
    value = row.get(key)
    return value.lower() if isinstance(value, str) else ""


def _tags(row: dict) -> list[str]:
    tags = row.get("tags")
    if not isinstance(tags, (list, tuple)):
        return []
    return [tag.lower() for tag in tags if isinstance(tag, str)]


def script_only(matched_on) -> bool:
    """Is the script body the only **content** source in ``matched_on``?

    The snippet rule, as a pure predicate because slice 5's `query_model.js`
    ports it: the row is in the list because a word was found in its script,
    and nothing about its name, tags or material says why. Field terms
    (``folder``/``state``/``kind``) do not count — they are the same for every
    returned row and explain nothing about *this* one.
    """
    found = set(matched_on)
    return "script" in found and not (found & CONTENT_SOURCES)


def rank(matched_on) -> int:
    """The sort rank of one row's evidence — lower sorts first."""
    return min((RANKS[source] for source in matched_on if source in RANKS),
               default=NO_EVIDENCE_RANK)


def snippet(text: str, needle: str, width: int = SNIPPET_CHARS) -> str:
    """``width`` characters of ``text`` around the first ``needle``, one line.

    Whitespace is collapsed so a result row shows a sentence rather than four
    lines of indentation, and the window is clamped to the ends of the text so
    a hit in the first line still yields ``width`` characters of context.
    """
    if not isinstance(text, str) or not text or not needle:
        return ""
    index = text.lower().find(needle.lower())
    if index < 0:
        return ""
    start = max(0, index - max(0, (width - len(needle)) // 2))
    end = min(len(text), start + width)
    start = max(0, end - width)
    return " ".join(text[start:end].split())


# ------------------------------------------------------------------- engine

def _stamp(path: str):
    """``(mtime_ns, size)`` for a file, or ``None`` when it is not there.

    The whole invalidation story: whatever rewrote the bytes moved one of the
    two, whoever did it and by whatever route — a tool, a git checkout, a
    merge, an undo, a human with an editor.

    It takes a **string** and calls `os.stat` rather than taking a `Path`,
    which is not a style choice: a scan of 1 000 parts stats every script, and
    `Path(str)` costs more than the stat it wraps (measured — the two
    together were 79% of a warm search before this).
    """
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _entry_tags(entry: dict) -> list[str]:
    """One manifest entry's tags, total over the value: a `tags` that is not a
    list of strings reads as no tags rather than raising mid-scan (the
    `folder_matches` discipline — a merge or a hand edit can shape this)."""
    tags = entry.get("tags")
    if not isinstance(tags, list):
        return []
    return [tag for tag in tags if isinstance(tag, str)]


def _state(status) -> str:
    """One part's build state from its `service._status` entry.

    Reported as found, never coerced into :data:`STATES`: a state this module
    has not heard of should read back as itself and simply match no ``state:``
    term, not be relabelled ``unbuilt``. No entry at all IS ``unbuilt`` — a
    part nobody has built yet, which is a result and not an error.
    """
    state = status.get("state") if isinstance(status, dict) else None
    return state if isinstance(state, str) else "unbuilt"


def _kind(base: dict, text: str) -> str:
    """``script`` | ``reference`` | ``package`` for one base row.

    A package part is a *script* part whose script carries the provenance
    header — `packages_lock` says which packages the project uses and cannot
    say which **part** came from where. `provenance.parse` gates on
    ``MARKER in script`` before it tokenizes anything, so this costs a
    substring scan for the parts that are not from a package.
    """
    if base.get("kind") == "reference":
        return "reference"
    return "package" if provenance.parse(text) is not None else "script"


class Engine:
    """The scan: manifest rows, script text, and the two memos over them.

    One instance per service (installed by `tools_navigation.register` as
    ``service.search``), so the memos survive for the life of the process and
    a registry rebuild does not throw a warm cache away.

    **Both memos are capped** (:data:`_MAX_ROWS_MEMO`,
    :data:`_MAX_SCRIPT_MEMO`) and clear wholesale on overflow: a server that
    has searched hundreds of projects should not hold every manifest it has
    ever seen, and the cost of being wrong is one re-read.

    **No lock, deliberately.** The server is threaded (two browsers can search
    at once), but the state is two plain dicts holding immutable tuples: a
    `dict` get/set is atomic under the GIL, and every entry is validated by a
    stat before it is used. The worst a race can do is have two threads read
    the same file and store equal values, or drop an entry that is about to be
    re-derived — a wasted read, never a wrong answer. A lock here would
    serialize a 1 000-part scan behind whoever got there first.
    """

    def __init__(self, service) -> None:
        self.service = service
        #: lock_key -> (manifest stamp, base rows). Keyed by `store.lock_key`
        #: because two branches of one project are two different manifests
        #: with the same name.
        self._rows: dict[str, tuple] = {}
        #: script path -> (stamp, raw text, lowered text). The lowered copy is
        #: memoized beside the raw one because every free-text query needs it
        #: and lowering ~2 MB of scripts per call was the single largest CPU
        #: cost left in a warm search; the raw copy is what a snippet quotes.
        self._scripts: dict[str, tuple] = {}

    # ------------------------------------------------------------ the rows

    def rows(self, proj: str) -> list[dict]:
        """Every part of ``proj`` as a search row, freshly stated.

        ``state`` and ``kind`` are deliberately **not** memoized with the rest:
        a build changes `service._status` without touching a manifest byte, and
        a script gaining (or losing) a provenance header changes ``kind``
        without touching one either. Only what the manifest itself says is
        cached — the rest is recomputed per call, off a stat-validated memo.
        """
        return [row for row, _, _ in self._scan(proj)]

    def _scan(self, proj: str):
        """Yield ``(row, raw script, lowered script)`` — **one stat each**.

        `search` needs both, and so does `rows` (``kind`` is a fact about the
        script bytes), so they share one pass: fetching the text twice per row
        doubled the stat count of every query.

        `service._status_key` is called **once**, not once per part. It is a
        2-tuple ``(store.lock_key(proj), part_id)`` — an invariant CLAUDE.md
        pins — and with a branch resolver installed (which is the default)
        `lock_key` resolves the project directory, costing two `is_file`
        stats. A thousand of those was 60% of a warm search over 1 000 parts.
        """
        status = self.service._status
        lock_key = self.service._status_key(proj, "")[0]
        for base in self._base_rows(proj):
            text, lowered = self._texts(base["script_path"])
            yield {**base,
                   # A COPY: the base row (and its list) belongs to the memo,
                   # and a caller that sorted or appended to `row["tags"]`
                   # would be editing the cache every later search reads.
                   "tags": list(base["tags"]),
                   "state": _state(status.get((lock_key, base["id"]))),
                   "kind": _kind(base, text)}, text, lowered

    def _base_rows(self, proj: str) -> list[dict]:
        store = self.service.store
        # Raises NotFoundError for an unknown project — before any stat, so an
        # unknown project is a 404 and never an empty result set.
        path = store.path_of(proj) / "project.json"
        key = store.lock_key(proj)
        stamp = _stamp(path)
        hit = self._rows.get(key)
        if hit is not None and stamp is not None and hit[0] == stamp:
            return hit[1]
        rows = []
        for entry in store.manifest(proj).get("parts", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            part_id = entry["id"]
            label = entry.get("label")
            rows.append({
                "id": part_id,
                "label": label if isinstance(label, str) else part_id,
                "material": entry.get("material"),
                "folder": entry.get("folder"),
                "tags": _entry_tags(entry),
                "kind": entry.get("kind") or "script",
                "script_path": str(store.script_path(proj, part_id)),
            })
        if len(self._rows) >= _MAX_ROWS_MEMO:
            self._rows.clear()
        self._rows[key] = (stamp, rows)
        return rows

    # --------------------------------------------------------- the scripts

    def script_text(self, proj: str, part_id: str) -> str:
        """One part's script, memoized on ``(path, mtime_ns, size)``.

        ``""`` for a reference part and for a script file that is missing: a
        search over a project with a broken part must answer, not raise. This
        deliberately does not go through `store.read_script`, which re-reads
        and re-parses the whole manifest for its existence check — once per
        part, that is quadratic over a 1 000-part project.
        """
        return self._text(str(self.service.store.script_path(proj, part_id)))

    def _text(self, path: str) -> str:
        return self._texts(path)[0]

    def _texts(self, path: str) -> tuple[str, str]:
        """``(raw, lowered)`` for one script path, memoized on its stamp.

        Both, because both are needed on every free-text query and neither can
        be derived from the other for free: the matcher compares against the
        lowered copy, the snippet quotes the raw one.
        """
        stamp = _stamp(path)
        if stamp is None:
            self._scripts.pop(path, None)
            return ("", "")
        hit = self._scripts.get(path)
        if hit is not None and hit[0] == stamp:
            return (hit[1], hit[2])
        try:
            # errors="replace": a script that is not valid UTF-8 is searchable
            # rather than fatal. It cannot build either way, and a scan of a
            # thousand parts must not stop on one of them.
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ("", "")
        if len(self._scripts) >= _MAX_SCRIPT_MEMO:
            self._scripts.clear()
        self._scripts[path] = (stamp, text, text.lower())
        return (text, text.lower())

    # ---------------------------------------------------------- the search

    def search(self, proj: str, query: str, *, filters: dict | None = None,
               limit: int = DEFAULT_LIMIT) -> dict:
        """``{query, total, parts}`` — the tool's and the route's one payload.

        ``total`` is the number of matching parts, ``parts`` the first
        ``limit`` of them in rank order, so a UI can say "50 of 312" without
        asking twice.
        """
        limit = _limit(limit)
        parsed = _with_filters(parse(query), filters)
        hits = []
        for index, (row, text, lowered) in enumerate(self._scan(proj)):
            # `_match`, not `matches`: the lowered copy is already in the memo,
            # so the scan lowers nothing per query.
            found = _match(row, lambda lowered=lowered: lowered, parsed)
            if found is not None:
                hits.append((rank(found), index, row, found, text))
        hits.sort(key=lambda hit: (hit[0], hit[1]))
        return {
            "query": query if isinstance(query, str) else "",
            "total": len(hits),
            "parts": [_result_row(row, found, text, parsed)
                      for _, _, row, found, text in hits[:limit]],
        }


def _result_row(row: dict, found: list[str], text: str, parsed: Query) -> dict:
    """One result row: the listing shape, plus the evidence.

    The snippet is attached when the script is the only **content** match
    (`script_only`) — when the part's name, tags or material matched, the row
    already shows the reason and 120 characters of somebody's script would be
    noise. Field terms are not content: `state:ok counterbore` still gets a
    snippet, which is the query shape the UI sends most (filter chips plus a
    word). It is absent rather than empty, so a key that is there always means
    "here is the only reason this row is in the list".
    """
    item = {
        "id": row["id"],
        "label": row["label"],
        "material": row["material"],
        "folder": row["folder"],
        "tags": list(row["tags"]),
        "state": row["state"],
        "kind": row["kind"],
        "matched_on": found,
    }
    if script_only(found):
        needle = next((t.value for t in parsed.free_text if not t.negate), "")
        item["snippet"] = snippet(text, needle)
    return item


def _limit(limit) -> int:
    """``None`` is the default; anything outside 1..500 is a refusal.

    ``bool`` is excluded explicitly — it is an ``int`` in Python, and
    ``limit=True`` meaning "one result" is a coincidence, not an intention.
    """
    if limit is None:
        return DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValidationError(
            f"limit must be an integer between 1 and {MAX_LIMIT}",
            {"limit": _safe(limit), "max": MAX_LIMIT})
    if not 1 <= limit <= MAX_LIMIT:
        raise ValidationError(
            f"limit must be between 1 and {MAX_LIMIT} (got {_safe(limit)})",
            {"limit": _safe(limit), "max": MAX_LIMIT})
    return limit


def _with_filters(parsed: Query, filters) -> Query:
    """AND the structured ``filters`` object onto a parsed query.

    Same terms, same validation, no second grammar — ``{"tag": ["a", "b"]}``
    is ``tag:a tag:b``, which is why a list ANDs rather than ORs. An agent
    passing a filter object and an agent passing the query string get
    identical results and identical refusals.
    """
    if filters is None:
        return parsed
    if not isinstance(filters, dict):
        raise ValidationError("filters must be an object",
                              {"keys": list(FILTER_KEYS)})
    for key in filters:
        if key not in FILTER_KEYS:
            raise ValidationError(f"unknown filter {_safe(key)!r}",
                                  {"filter": _safe(key),
                                   "keys": list(FILTER_KEYS)})
    terms = list(parsed.terms)
    for key in FILTER_KEYS:  # a fixed order, so two equal filter objects
        if key not in filters:  # produce the same Query
            continue
        value = filters[key]
        values = value if isinstance(value, list) else [value]
        if not values:
            raise ValidationError(
                f"filter {_safe(key)!r} is an empty list — omit it instead",
                {"filter": _safe(key)})
        terms += [field_term(key, item) for item in values]
    return Query(tuple(terms))
