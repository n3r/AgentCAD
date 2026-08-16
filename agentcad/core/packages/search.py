"""Structured package search: deterministic, explainable, honestly degraded.

Design Decision 8. Everything here runs in the server process over parsed
`index.json` documents — **no kernel call, no download, no network** beyond an
index `refresh()`.

Ranking, in points, highest wins::

    exact name          100
    name prefix          80
    standards match      70
    keyword match        60
    summary substring    40
    part or param name   30

Ties break on **name ascending, then version descending**, so two indexes
carrying the same package name order the same way on every machine. Every hit
carries ``why`` — a search an agent cannot explain is a search it cannot
correct.

**Semantic search is optional and never a hard dependency** (FR8). This build
registers no embedding provider, so every result carries
``semantic: false, semantic_reason: "no_embedding_provider"``.
Present-and-false beats absent: an agent can tell that keyword search is what
it got, which is exactly the "degrades honestly" clause. The chat agent
already needs an Anthropic key; embeddings must not become a second hard
dependency for a catalog to be searchable.

**A yanked version is never a hit.** `yank` says "do not start here"; a
package whose only versions are yanked is omitted entirely, and an existing
lock entry naming one keeps resolving through `manager.resolve` — which is
where that exception belongs, not here.
"""

from __future__ import annotations

from ..model import NotFoundError, ValidationError
from . import format as pkgformat

SCORE_NAME_EXACT = 100
SCORE_NAME_PREFIX = 80
SCORE_STANDARD = 70
SCORE_KEYWORD = 60
SCORE_TEXT = 40
SCORE_PART = 30

DEFAULT_LIMIT = 20

#: What this build did instead of a semantic search, and why.
NO_SEMANTIC = "no_embedding_provider"


def search(indexes, *, query=None, index=None, keywords=None, standards=None,
           param=None, limit=DEFAULT_LIMIT) -> dict:
    """``{hits, semantic, semantic_reason, indexes, warnings}``.

    ``index`` pins one configured index by name; ``keywords`` and
    ``standards`` are **AND** filters (case-insensitive); ``param`` is
    ``{name, min?, max?}`` and matches a part whose declared range for that
    parameter *overlaps* the interval — the facet that makes the published
    parts digest worth carrying.

    A broken or unreachable index is a **warning**, never an exception: one
    bad index must not make the others unsearchable, which is the same rule
    `load_indexes` and `manager.resolve` already follow.
    """
    warnings: list[str] = []
    names: list[str] = []
    hits: list[dict] = []
    wanted_keywords = _lower(keywords)
    wanted_standards = _lower(standards)
    param = _param(param)
    for candidate in indexes or []:
        if index is not None and candidate.name != index:
            continue
        names.append(candidate.name)
        try:
            candidate.refresh()
            doc = candidate.entries()
        except (NotFoundError, ValidationError) as exc:
            warnings.append(f"index {candidate.name!r}: {exc}")
            continue
        stale = bool(getattr(candidate, "stale", False))
        reason = getattr(candidate, "stale_reason", None)
        if stale and reason:
            warnings.append(f"index {candidate.name!r} is stale: {reason}")
        for name, record in sorted((doc.get("packages") or {}).items()):
            versions = record.get("versions") if isinstance(record, dict) else None
            if not isinstance(versions, dict):
                continue
            version = pkgformat.resolve(versions, "*")
            if version is None:      # every version is yanked, or none parses
                continue
            hit = _score(candidate, name, version, versions[version], query,
                         wanted_keywords, wanted_standards, param)
            if hit is not None:
                hit["stale"] = stale
                hits.append(hit)
    hits.sort(key=lambda h: (-h["score"], h["name"],
                             _version_key(h["version"])))
    if limit is not None:
        hits = hits[:max(0, int(limit))]
    return {"hits": hits, "semantic": False, "semantic_reason": NO_SEMANTIC,
            "indexes": names, "warnings": warnings}


# ---------------------------------------------------------------- scoring


def _score(index, name, version, entry, query, keywords, standards, param):
    """One hit, or ``None`` when a filter excludes the package.

    Filters are absolute (a package that does not carry the keyword is not a
    low-scoring hit, it is not a hit); the query only *scores*, so an empty
    query lists the index in name order — which is what the Library dialog
    opens on.
    """
    entry_keywords = _lower(entry.get("keywords"))
    entry_standards = _lower(entry.get("standards"))
    why: list[str] = []
    if keywords:
        if not all(word in entry_keywords for word in keywords):
            return None
        why += [f"keyword:{word}" for word in keywords]
    if standards:
        if not all(word in entry_standards for word in standards):
            return None
        why += [f"standard:{word}" for word in standards]
    if param is not None:
        matched = _param_match(entry, param)
        if not matched:
            return None
        why += matched

    score = 0
    if query:
        needle = str(query).strip().lower()
        if needle:
            score, query_why = _query_score(name, entry, entry_keywords,
                                            entry_standards, needle)
            if score == 0:
                return None
            why += query_why
    return {
        "name": name,
        "version": version,
        "index": index.name,
        "kind": index.kind,
        "summary": entry.get("summary"),
        "keywords": list(entry.get("keywords") or []),
        "standards": list(entry.get("standards") or []),
        "license": entry.get("license"),
        "disclosure": entry.get("disclosure"),
        "parts": entry.get("parts") or {},
        "presets": list(entry.get("presets") or []),
        "previews": list(entry.get("previews") or []),
        "min_agentcad": entry.get("min_agentcad"),
        "gate": entry.get("gate"),
        "score": score,
        "why": why,
    }


def _query_score(name, entry, keywords, standards, needle):
    """The highest band the query matches, and why. One band, not a sum: a
    package is not more relevant for matching the same word twice."""
    lowered = str(name).lower()
    if lowered == needle:
        return SCORE_NAME_EXACT, [f"name:{name}"]
    if lowered.startswith(needle):
        return SCORE_NAME_PREFIX, [f"name_prefix:{name}"]
    hit = next((s for s in standards if needle in s), None)
    if hit is not None:
        return SCORE_STANDARD, [f"standard:{hit}"]
    hit = next((k for k in keywords if needle in k), None)
    if hit is not None:
        return SCORE_KEYWORD, [f"keyword:{hit}"]
    summary = str(entry.get("summary") or "").lower()
    if needle in summary:
        return SCORE_TEXT, ["text:summary"]
    for part_id, digest in sorted((entry.get("parts") or {}).items()):
        if needle in str(part_id).lower():
            return SCORE_PART, [f"part:{part_id}"]
        for spec in (digest or {}).get("params") or []:
            if needle in str((spec or {}).get("name") or "").lower():
                return SCORE_PART, [f"param:{part_id}.{spec['name']}"]
    return 0, []


# --------------------------------------------------------- the param facet


def _param(value):
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    if not isinstance(name, str) or not name:
        return None
    return {"name": name, "min": _number(value.get("min")),
            "max": _number(value.get("max"))}


def _param_match(entry, param) -> list[str]:
    """``why`` entries for every part whose declared range **overlaps** the
    requested interval; empty when none does.

    An unbounded declared range matches anything, which is the honest reading:
    a parameter with no `min` does not exclude a value.
    """
    out = []
    for part_id, digest in sorted((entry.get("parts") or {}).items()):
        for spec in (digest or {}).get("params") or []:
            if not isinstance(spec, dict) or spec.get("name") != param["name"]:
                continue
            low, high = _number(spec.get("min")), _number(spec.get("max"))
            if param["min"] is not None and high is not None \
                    and high < param["min"]:
                continue
            if param["max"] is not None and low is not None \
                    and low > param["max"]:
                continue
            out.append(f"param:{part_id}.{param['name']}")
    return out


# ---------------------------------------------------------------- helpers


def _lower(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value).lower() for value in values]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _version_key(version):
    """Descending version, as a sort key that never raises on a version the
    index carries but this build cannot parse."""
    try:
        return tuple(-part for part in pkgformat.parse_version(version))
    except ValidationError:
        return (0, 0, 0)
