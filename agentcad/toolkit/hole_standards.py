"""ISO/ANSI hole standards: the vendored tables, the lookups, the designations.

**This module is OCP-free and must stay that way.** It is the third toolkit
module (with `sketch.py` and `specs.py`) that runs in the *server* process —
`core/tools_holes.py`'s `hole_standards` tool imports it, and the server must
never import build123d/OCP. `tests/test_toolkit_ocp_free.py` asserts it in a
fresh interpreter with `OCP` blocked at `sys.meta_path`.

The data lives in `data/*.json`, one file per family group, each carrying
`{schema, standard, units, sources, revision, rows}`. **Every row is
transcribed from two independent published sources named in `sources`; a row
that could not be corroborated is absent, and the file's `notes` says which
ones and why.** A correction is therefore a reviewable one-line diff, and
`revision` makes it visible in a proposal packet.

Fit spellings
-------------
ISO 273 names the three clearance series **fine / medium / coarse**; ASME
B18.2.8 (and PRD-010 itself) names the same idea **close / normal / loose**.
Both spellings are accepted everywhere a fit is taken. Internally the ISO name
is the key; the answer reports the spelling of the requested `std`
(`canonical_fit`). This is documented rather than picked because an agent will
type both.

What is a standard here and what is a shop rule
-----------------------------------------------
Clearance diameters, thread pitches and fastener head geometry are standards.
The **tap drill** is a shop number (the stock drill nearest `d - P`), and the
**counterbore diameter/depth** is not standardised at all: the published charts
disagree by up to 0.75 mm on M8 alone. So `cbore()` returns the published head
geometry plus a bore derived by the named constants below, and says so in
`rule`. Never present that bore as a table value.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

SCHEMA = 1

DATA_DIR = Path(__file__).resolve().parent / "data"

STANDARDS = ("iso", "ansi")
FAMILIES = ("clearance", "tapped", "counterbore", "countersink")

# Aliases for the counterbore/countersink family names an author may reach for.
_FAMILY_ALIASES = {"cbore": "counterbore", "csk": "countersink",
                   "thread": "tapped", "tapped": "tapped",
                   "clearance": "clearance", "counterbore": "counterbore",
                   "countersink": "countersink"}

# ISO name -> the spelling each standard uses for it.
_FIT_NAMES = {
    "iso": {"fine": "fine", "medium": "medium", "coarse": "coarse"},
    "ansi": {"fine": "close", "medium": "normal", "coarse": "loose"},
}
# every accepted spelling -> the ISO (internal) name
_FIT_ALIASES = {"fine": "fine", "close": "fine",
                "medium": "medium", "normal": "medium",
                "coarse": "coarse", "loose": "coarse"}

# Per-standard countersink angle. build123d's `CounterSinkHole` defaults to 82,
# an ASME default that would otherwise arrive inside an ISO-labelled call, so
# `holes.countersink` passes this explicitly, always.
_CSK_ANGLE = {"iso": 90.0, "ansi": 82.0}

# The counterbore clearance rule. A SHOP DEFAULT, NOT A STANDARD — see the
# module docstring and `data/iso_cbore_csk.json`'s notes. Diameter: the head
# max plus 1.5 mm (0.75 mm radial), which lands on or above every published
# convention from M5 up. Depth: the head max plus 0.8 mm, so the head sits
# below the surface with a visible step.
CBORE_DIA_CLEARANCE = 1.5
CBORE_DEPTH_CLEARANCE = 0.8
CBORE_RULE = (
    f"counterbore = head diameter + {CBORE_DIA_CLEARANCE} mm, depth = head "
    f"height + {CBORE_DEPTH_CLEARANCE} mm. A shop default, not a standard: "
    "the published counterbore charts disagree (see the data file's notes). "
    "The head dimensions are the standard part of this answer."
)

# Glyphs are ISO 129 / ASME Y14.5 and are shared by both symbologies; what
# differs per standard is the numbers and the thread designation.
_DIA, _DEPTH, _CBORE, _CSK = "⌀", "↧", "⌴", "⌵"


# --------------------------------------------------------------- validation

def _check_std(std: str) -> str:
    if not isinstance(std, str) or std.lower() not in STANDARDS:
        raise ValueError(
            f"std must be one of {list(STANDARDS)}, got {std!r}")
    return std.lower()


def _check_size(size) -> str:
    if not isinstance(size, str) or not size:
        raise ValueError(f"size must be a designation string, got {size!r}")
    return size.strip().upper()


def _unknown_size(size: str, std: str, family: str, known) -> ValueError:
    return ValueError(
        f"size {size!r} is not in the {std.upper()} {family} table; "
        f"known sizes: {', '.join(known)}")


def canonical_fit(fit: str, std: str = "iso") -> str:
    """The requested standard's spelling of a fit, from either spelling."""
    std = _check_std(std)
    if not isinstance(fit, str) or fit.lower() not in _FIT_ALIASES:
        raise ValueError(
            f"fit must be one of {sorted(set(_FIT_ALIASES))}, got {fit!r}")
    return _FIT_NAMES[std][_FIT_ALIASES[fit.lower()]]


def default_csk_angle(std: str = "iso") -> float:
    """ISO countersinks are 90 deg, ASME 82 deg. build123d's default is 82."""
    return _CSK_ANGLE[_check_std(std)]


def _pitch_key(pitch: float) -> str:
    """Pitch keys in the JSON are the decimal spelling ('0.8', '1.25', '1.0')."""
    text = f"{float(pitch):.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


# ------------------------------------------------------------------- tables

@functools.lru_cache(maxsize=None)
def table(name: str) -> dict:
    """Load and validate one vendored table. Cached: the files never change
    within a process, and the lookups are on the rebuild path."""
    path = DATA_DIR / f"{name}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:                      # pragma: no cover
        raise ValueError(f"no vendored table named {name!r}") from exc
    if doc.get("schema") != SCHEMA:
        raise ValueError(
            f"{path.name}: schema {doc.get('schema')!r} is not {SCHEMA}")
    for key in ("standard", "units", "sources", "revision", "rows"):
        if not doc.get(key):
            raise ValueError(f"{path.name}: missing header key {key!r}")
    if len(doc["sources"]) < 2:
        raise ValueError(
            f"{path.name}: every table needs two independent published "
            f"sources, got {len(doc['sources'])}")
    return doc


def _table_for(std: str, name: str, family: str) -> dict:
    if std != "iso":
        raise ValueError(
            f"std {std!r}: no {std.upper()} {family} table ships yet "
            f"(ANSI/ASME tables land with counterbore + countersink); "
            f"use std='iso'")
    return table(name)


def _provenance(doc: dict) -> dict:
    return {"standard": doc["standard"], "revision": doc["revision"],
            "sources": list(doc["sources"])}


# ------------------------------------------------------------------ lookups

def clearance(size: str, *, fit: str = "medium", std: str = "iso") -> dict:
    """ISO 273 clearance hole for `size` at `fit`."""
    std = _check_std(std)
    size = _check_size(size)
    doc = _table_for(std, "iso_clearance", "clearance")
    row = doc["rows"].get(size)
    if row is None:
        raise _unknown_size(size, std, "clearance", doc["rows"])
    key = _FIT_ALIASES.get(str(fit).lower())
    if key is None:
        raise ValueError(
            f"fit must be one of {sorted(set(_FIT_ALIASES))}, got {fit!r}")
    d = float(row[key])
    return {"family": "clearance", "std": std, "size": size,
            "fit": _FIT_NAMES[std][key], "d": d,
            "designation": designation("clearance", d=d, std=std),
            **_provenance(doc)}


def clearance_fits(size: str, *, std: str = "iso") -> dict:
    """All three fits at once — what the `hole_standards` tool answers (AC3)."""
    std = _check_std(std)
    size = _check_size(size)
    doc = _table_for(std, "iso_clearance", "clearance")
    row = doc["rows"].get(size)
    if row is None:
        raise _unknown_size(size, std, "clearance", doc["rows"])
    return {name: float(row[key]) for key, name in _FIT_NAMES[std].items()}


def thread(size: str, *, pitch: float | None = None, depth: float | None = None,
           thread_class: str = "6H", std: str = "iso") -> dict:
    """ISO 261/262 pitch and the shop tap drill for `size`.

    `pitch=None` means the coarse (first-choice) pitch. `depth` only shapes the
    designation; it is the caller's geometry, not a table value.
    """
    std = _check_std(std)
    size = _check_size(size)
    doc = _table_for(std, "iso_thread", "thread")
    row = doc["rows"].get(size)
    if row is None:
        raise _unknown_size(size, std, "thread", doc["rows"])
    key = _pitch_key(row["coarse_pitch"] if pitch is None else pitch)
    entry = row["pitches"].get(key)
    if entry is None:
        raise ValueError(
            f"pitch {pitch!r} is not tabulated for {size} in the "
            f"{std.upper()} thread table; known pitches: "
            f"{', '.join(sorted(row['pitches']))}")
    label = f"{size}×{_num(float(key), std)}"
    return {"family": "tapped", "std": std, "size": size, "pitch": float(key),
            "tap_drill": float(entry["tap_drill"]), "series": entry["series"],
            "thread": label, "thread_class": thread_class,
            "designation": designation("tapped", thread=label,
                                       thread_class=thread_class, depth=depth,
                                       std=std),
            **_provenance(doc)}


def cbore(size: str, *, fastener: str = "iso4762", std: str = "iso") -> dict:
    """Counterbore for a socket-head fastener.

    Returns the **published head geometry** (`head_d`, `head_h`) and the bore
    derived from it by `CBORE_RULE`. The rule travels in the answer because the
    published counterbore charts disagree; nothing here pretends otherwise.
    """
    std = _check_std(std)
    size = _check_size(size)
    doc = _table_for(std, "iso_cbore_csk", "counterbore")
    rows = doc["rows"]["cbore"].get(fastener)
    if rows is None:
        raise ValueError(
            f"fastener {fastener!r} has no head table; known: "
            f"{', '.join(sorted(doc['rows']['cbore']))}")
    row = rows.get(size)
    if row is None:
        raise _unknown_size(size, std, f"counterbore ({fastener})", rows)
    head_d, head_h = float(row["head_d"]), float(row["head_h"])
    return {"family": "counterbore", "std": std, "size": size,
            "fastener": fastener, "head_d": head_d, "head_h": head_h,
            "d": head_d + CBORE_DIA_CLEARANCE,
            "depth": head_h + CBORE_DEPTH_CLEARANCE,
            "rule": CBORE_RULE, **_provenance(doc)}


def csk(size: str, *, angle: float | None = None, fastener: str = "iso10642",
        std: str = "iso") -> dict:
    """Countersink for a flat-head fastener.

    `d` is the fastener's **theoretical sharp** head diameter — the dimension a
    countersink callout names — so no clearance is added: it already stands off
    the machined head max.
    """
    std = _check_std(std)
    size = _check_size(size)
    doc = _table_for(std, "iso_cbore_csk", "countersink")
    rows = doc["rows"]["csk"].get(fastener)
    if rows is None:
        raise ValueError(
            f"fastener {fastener!r} has no head table; known: "
            f"{', '.join(sorted(doc['rows']['csk']))}")
    row = rows.get(size)
    if row is None:
        raise _unknown_size(size, std, f"countersink ({fastener})", rows)
    head_d = float(row["head_d"])
    resolved = float(row.get("angle_deg", default_csk_angle(std))) \
        if angle is None else float(angle)
    return {"family": "countersink", "std": std, "size": size,
            "fastener": fastener, "head_d": head_d, "d": head_d,
            "angle_deg": resolved, **_provenance(doc)}


def sizes(family: str, *, std: str = "iso") -> list[str]:
    """The sizes tabulated for a family, in table order."""
    std = _check_std(std)
    family = _check_family(family)
    if family == "clearance":
        return list(_table_for(std, "iso_clearance", family)["rows"])
    if family == "tapped":
        return list(_table_for(std, "iso_thread", family)["rows"])
    doc = _table_for(std, "iso_cbore_csk", family)
    group = "cbore" if family == "counterbore" else "csk"
    return sorted({size for rows in doc["rows"][group].values() for size in rows},
                  key=lambda s: float(s[1:]))


def _check_family(family: str) -> str:
    key = _FAMILY_ALIASES.get(str(family).lower())
    if key is None:
        raise ValueError(
            f"family must be one of {list(FAMILIES)}, got {family!r}")
    return key


def lookup(family: str | None = None, size: str | None = None,
           std: str = "iso") -> dict:
    """The `hole_standards` tool's answer. JSON-able, no geometry.

    No `family` lists what is tabulated. `family` alone returns that family's
    sizes. `family` + `size` returns the row — for clearance, all three fits at
    once (AC3), because a caller asking "what hole for an M5" wants the choice.
    """
    std = _check_std(std)
    if family is None:
        return {"std": std, "families": list(FAMILIES),
                "sizes": {name: sizes(name, std=std) for name in FAMILIES},
                "fits": {name: _FIT_NAMES[std][name]
                         for name in ("fine", "medium", "coarse")},
                "csk_angle_deg": default_csk_angle(std)}
    family = _check_family(family)
    if size is None:
        return {"std": std, "family": family, "sizes": sizes(family, std=std)}
    if family == "clearance":
        doc = _table_for(std, "iso_clearance", family)
        fits = clearance_fits(size, std=std)
        return {"std": std, "family": family, "size": _check_size(size),
                "fits": fits,
                "designations": {name: designation("clearance", d=d, std=std)
                                 for name, d in fits.items()},
                **_provenance(doc)}
    if family == "tapped":
        row = thread(size, std=std)
        doc = _table_for(std, "iso_thread", family)
        # A list, not a dict keyed by pitch: JSON has no numeric keys, and an
        # agent reading "which pitches exist for M8" wants them in order.
        row["pitches"] = sorted(
            ({"pitch": float(key), **entry}
             for key, entry in doc["rows"][_check_size(size)]["pitches"].items()),
            key=lambda entry: entry["pitch"], reverse=True)
        return row
    if family == "counterbore":
        return cbore(size, std=std)
    return csk(size, std=std)


# -------------------------------------------------------------- designation

def _num(value: float, std: str = "iso") -> str:
    """Format a length for a callout: millimetres to 3 dp, inches to 4, both
    with trailing zeros trimmed. Never a display formatter over data — this
    output is *text for a drawing*, and the numeric value travels separately."""
    text = f"{float(value):.{3 if std == 'iso' else 4}f}".rstrip("0").rstrip(".")
    return text or "0"


def designation(family: str, *, std: str = "iso", d: float | None = None,
                depth: float | None = None, thread: str | None = None,
                thread_class: str | None = None, cbore_d: float | None = None,
                cbore_depth: float | None = None, csk_d: float | None = None,
                angle: float | None = None) -> str:
    """The callout string for a hole, per the standard's symbology.

    | family | ISO | ASME |
    |---|---|---|
    | clearance | `⌀5.5` | `⌀0.217` |
    | tapped | `M5×0.8 - 6H ↧12` | `10-24 UNC - 2B ↧0.5` |
    | counterbore | `⌀5.5 ⌴⌀9.5↧5.4` | `⌀0.217 ⌴⌀0.375↧0.213` |
    | countersink | `⌀5.5 ⌵⌀10.4×90°` | `⌀0.217 ⌵⌀0.41×82°` |

    The glyphs are shared; the numbers and the thread designation are what the
    standard changes. A `depth` of `None` means a through hole and the depth
    glyph is omitted — a through hole is not "depth 0".
    """
    std = _check_std(std)
    family = _check_family(family)
    if family == "tapped":
        if not thread:
            raise ValueError("thread is required for a tapped designation")
        text = thread if not thread_class else f"{thread} - {thread_class}"
        return text if depth is None else f"{text} {_DEPTH}{_num(depth, std)}"
    if d is None:
        raise ValueError(f"d is required for a {family} designation")
    head = f"{_DIA}{_num(d, std)}"
    if family == "clearance":
        return head if depth is None else f"{head} {_DEPTH}{_num(depth, std)}"
    if family == "counterbore":
        if cbore_d is None or cbore_depth is None:
            raise ValueError(
                "cbore_d and cbore_depth are required for a counterbore "
                "designation")
        return (f"{head} {_CBORE}{_DIA}{_num(cbore_d, std)}"
                f"{_DEPTH}{_num(cbore_depth, std)}")
    if csk_d is None:
        raise ValueError("csk_d is required for a countersink designation")
    deg = default_csk_angle(std) if angle is None else float(angle)
    return f"{head} {_CSK}{_DIA}{_num(csk_d, std)}×{_num(deg, std)}°"
