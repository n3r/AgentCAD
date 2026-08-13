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

Units: millimetres for geometry, the standard's own for callouts
---------------------------------------------------------------
The ISO tables are millimetres and the ASME tables are inches, and AgentCAD's
kernel works in millimetres. So **every length a lookup returns is
millimetres** (`d`, `depth`, `head_d`, `tap_drill`), with a `*_native`
companion in the table's own unit. A **designation** is text for a drawing and
prints the standard's own unit — `⌀5.5` for ISO, `⌀0.281` for ASME — so
`designation()` takes its numbers in *that* unit and `in_designation_units()`
is the one conversion between the two. A millimetre value formatted as an inch
callout is a 25.4x error that looks like a plausible number, which is why the
conversion is a named function and not a division in a formatter.

What is a standard here and what is a shop rule
-----------------------------------------------
Clearance diameters, thread pitches and fastener head geometry are standards.
The **tap drill** is a shop number (the stock drill nearest `d - P`), and the
**counterbore diameter/depth** is not standardised at all: the published charts
disagree by up to 0.75 mm on M8 alone. So `cbore()` returns the published head
geometry plus a bore derived by the named constants below, and says so in
`rule`. Never present that bore as a table value.

The same trap has a second form on the inch side: **there is more than one
published inch clearance chart**, and they are not roundings of each other
(ASME B18.2.8 gives a #10 screw 0.206/0.221/0.238 in; the traditional
close-fit/free-fit table gives 0.196/0.201). Each data file therefore names the
standard it transcribes in its header, and nothing here blends two of them.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

SCHEMA = 1

DATA_DIR = Path(__file__).resolve().parent / "data"

STANDARDS = ("iso", "ansi")
FAMILIES = ("clearance", "tapped", "counterbore", "countersink")

#: Exact by definition since 1959, so this is a conversion, not a measurement.
MM_PER_INCH = 25.4

# Which vendored file answers which (standard, family) question. A missing
# entry is "that table does not ship", which is an error naming `std` — never
# a silent fall-through to the other standard's numbers.
_TABLES = {
    ("iso", "clearance"): "iso_clearance",
    ("iso", "tapped"): "iso_thread",
    ("iso", "counterbore"): "iso_cbore_csk",
    ("iso", "countersink"): "iso_cbore_csk",
    ("ansi", "clearance"): "ansi_clearance",
    ("ansi", "tapped"): "ansi_thread",
    ("ansi", "counterbore"): "ansi_cbore_csk",
    ("ansi", "countersink"): "ansi_cbore_csk",
}

# The head table each standard means by "a socket head screw" / "a flat head
# screw" when the caller does not name one.
_DEFAULT_FASTENER = {
    ("iso", "counterbore"): "iso4762", ("iso", "countersink"): "iso10642",
    ("ansi", "counterbore"): "asme_b18_3",
    ("ansi", "countersink"): "asme_b18_3_flat",
}

# The tolerance class a tapped callout carries when the caller does not name
# one: ISO 965's 6H, ASME B1.1's 2B. `M5x0.8 - 2B` and `1/4-20 UNC - 6H` are
# both nonsense, so this is per standard and not a single default.
_THREAD_CLASS = {"iso": "6H", "ansi": "2B"}

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
# The same rule in the inch shop's round numbers: 1/16 in on diameter, 1/32 in
# on depth. Within 0.09 mm of the metric rule, and it lands a 1/4 in socket
# head on a 7/16 counterbore 9/32 deep instead of on 11.03/7.15 mm.
CBORE_DIA_CLEARANCE_IN = 0.0625
CBORE_DEPTH_CLEARANCE_IN = 0.03125
CBORE_RULE = (
    f"counterbore = head diameter + {CBORE_DIA_CLEARANCE} mm "
    f"({CBORE_DIA_CLEARANCE_IN} in), depth = head height + "
    f"{CBORE_DEPTH_CLEARANCE} mm ({CBORE_DEPTH_CLEARANCE_IN} in). A shop "
    "default, not a standard: the published counterbore charts disagree (see "
    "the data file's notes). The head dimensions are the standard part of "
    "this answer."
)

# Glyphs are ISO 129 / ASME Y14.5 and are shared by both symbologies; what
# differs per standard is the numbers and the thread designation.
_DIA, _DEPTH, _CBORE, _CSK = "⌀", "↧", "⌴", "⌵"


# --------------------------------------------------------------- validation

def check_std(std: str) -> str:
    """The canonical lower-case name of a standard, or `ValueError` naming the
    argument. Public because a caller that only wants the *symbology* — a plain
    drilled hole has no table row but still has a callout grammar — needs to
    validate `std` the same way every lookup here does."""
    if not isinstance(std, str) or std.lower() not in STANDARDS:
        raise ValueError(
            f"std must be one of {list(STANDARDS)}, got {std!r}")
    return std.lower()


_check_std = check_std


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


def default_thread_class(std: str = "iso") -> str:
    """ISO 965's 6H, ASME B1.1's 2B — the internal-thread tolerance class the
    standard's own callouts carry."""
    return _THREAD_CLASS[_check_std(std)]


def in_designation_units(value_mm: float, std: str = "iso") -> float:
    """A length in the units the standard's callouts PRINT: millimetres for
    ISO, inches for ASME.

    Every number this module returns for **geometry** is millimetres, because
    that is the unit the kernel works in. Every number a **callout** shows is
    the standard's own, and for ASME that is inches. Those are two different
    quantities, so converting between them is a named function rather than a
    `/ 25.4` somewhere in a formatter.
    """
    return float(value_mm) / (MM_PER_INCH if _check_std(std) == "ansi" else 1.0)


def _pitch_key(pitch: float, std: str = "iso") -> str:
    """The JSON key for a pitch.

    ISO keys are the decimal pitch in millimetres (`'0.8'`, `'1.25'`, `'1.0'`).
    **Unified inch keys are threads per inch** — a whole count, not a length —
    so `'20'`, never `'20.0'`. Formatting a count with a length's formatter is
    how a table lookup silently misses.
    """
    if _check_std(std) == "ansi":
        value = float(pitch)
        if value != int(value) or value <= 0:
            raise ValueError(
                f"pitch {pitch!r} is threads per inch for {std.upper()} "
                f"threads, so it must be a whole count > 0")
        return str(int(value))
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


def _table_for(std: str, family: str) -> dict:
    name = _TABLES.get((std, family))
    if name is None:                                      # pragma: no cover
        raise ValueError(
            f"std {std!r}: no {std.upper()} {family} table ships. A lookup "
            f"that cannot be answered from a vendored table is an error, "
            f"never the other standard's numbers under this label.")
    return table(name)


def _provenance(doc: dict) -> dict:
    return {"standard": doc["standard"], "revision": doc["revision"],
            "units": doc["units"], "sources": list(doc["sources"])}


def _mm(value, doc: dict) -> float:
    """A table value in the kernel's unit. The file says which unit it is in;
    nothing downstream has to know."""
    return float(value) * (MM_PER_INCH if doc["units"] == "in" else 1.0)


def _fastener_for(std: str, family: str, fastener: str | None) -> str:
    return fastener if fastener else _DEFAULT_FASTENER[(std, family)]


# ------------------------------------------------------------------ lookups

def _clearance_cell(row: dict, key: str, std: str, doc: dict) -> tuple:
    """`(d_mm, d_native, drill|None)` for one fit of one clearance row.

    The two files are shaped differently on purpose: the ISO cell is a bare
    millimetre number because a metric clearance hole *is* the drill, while the
    ASME cell carries a number/letter/fraction drill designation which is part
    of the callout and cannot be derived from the decimal.
    """
    cell = row[_FIT_NAMES[std][key]] if _FIT_NAMES[std][key] in row else row[key]
    if isinstance(cell, dict):
        return _mm(cell["d"], doc), float(cell["d"]), cell.get("drill")
    return _mm(cell, doc), float(cell), None


def clearance(size: str, *, fit: str = "medium", std: str = "iso") -> dict:
    """Clearance hole for `size` at `fit` — ISO 273, or ASME B18.2.8.

    `d` is **millimetres**, always: it is a number for the geometry. `d_native`
    and `designation` are in the standard's own units (inches for ASME), which
    is what a callout prints.
    """
    std = _check_std(std)
    size = _check_size(size)
    doc = _table_for(std, "clearance")
    row = doc["rows"].get(size)
    if row is None:
        raise _unknown_size(size, std, "clearance", doc["rows"])
    key = _FIT_ALIASES.get(str(fit).lower())
    if key is None:
        raise ValueError(
            f"fit must be one of {sorted(set(_FIT_ALIASES))}, got {fit!r}")
    d_mm, native, drill = _clearance_cell(row, key, std, doc)
    return {"family": "clearance", "std": std, "size": size,
            "fit": _FIT_NAMES[std][key], "d": d_mm, "d_native": native,
            "drill": drill,
            "designation": designation("clearance", d=native, std=std),
            **_provenance(doc)}


def clearance_fits(size: str, *, std: str = "iso") -> dict:
    """All three fits at once, in the standard's own units — what the
    `hole_standards` tool answers (AC3)."""
    std = _check_std(std)
    size = _check_size(size)
    doc = _table_for(std, "clearance")
    row = doc["rows"].get(size)
    if row is None:
        raise _unknown_size(size, std, "clearance", doc["rows"])
    return {name: _clearance_cell(row, key, std, doc)[1]
            for key, name in _FIT_NAMES[std].items()}


def thread(size: str, *, pitch: float | None = None, depth: float | None = None,
           thread_class: str | None = None, std: str = "iso") -> dict:
    """Thread pitch and the shop tap drill for `size` — ISO 261/262, or the
    Unified inch UNC/UNF series.

    `pitch=None` means the first-choice pitch (ISO coarse, or UNC). For a
    Unified thread the "pitch" argument is **threads per inch**, a whole count:
    `thread("1/4", pitch=28, std="ansi")` is the UNF row. The answer reports
    both — `tpi` and `pitch` in millimetres — so a caller that only speaks one
    of them is never handed the other silently.

    `depth` only shapes the designation; it is the caller's geometry, not a
    table value, and it is given in **millimetres** like every other length
    that crosses this boundary.
    """
    std = _check_std(std)
    size = _check_size(size)
    thread_class = thread_class or default_thread_class(std)
    doc = _table_for(std, "tapped")
    row = doc["rows"].get(size)
    if row is None:
        raise _unknown_size(size, std, "thread", doc["rows"])
    key = _pitch_key(row["coarse_pitch"] if pitch is None else pitch, std)
    entry = row["pitches"].get(key)
    if entry is None:
        raise ValueError(
            f"pitch {pitch!r} is not tabulated for {size} in the "
            f"{std.upper()} thread table; known pitches: "
            f"{', '.join(sorted(row['pitches']))}")
    if std == "ansi":
        tpi = float(key)
        pitch_mm = MM_PER_INCH / tpi
        label = f"{size}-{int(tpi)} {entry['series']}"
    else:
        tpi = None
        pitch_mm = float(key)
        label = f"{size}×{_num(pitch_mm, std)}"
    return {"family": "tapped", "std": std, "size": size, "pitch": pitch_mm,
            "tpi": tpi, "tap_drill": _mm(entry["tap_drill"], doc),
            "tap_drill_native": float(entry["tap_drill"]),
            "drill": entry.get("drill"), "series": entry["series"],
            "thread": label, "thread_class": thread_class,
            "designation": designation(
                "tapped", thread=label, thread_class=thread_class,
                depth=None if depth is None
                else in_designation_units(depth, std), std=std),
            **_provenance(doc)}


def cbore(size: str, *, fastener: str | None = None, std: str = "iso") -> dict:
    """Counterbore for a socket-head fastener.

    Returns the **published head geometry** (`head_d`, `head_h`) and the bore
    derived from it by `CBORE_RULE`. The rule travels in the answer because the
    published counterbore charts disagree; nothing here pretends otherwise.

    Lengths are millimetres; `*_native` repeats them in the table's own unit,
    which for ASME is inches and is what the callout prints.
    """
    std = _check_std(std)
    size = _check_size(size)
    fastener = _fastener_for(std, "counterbore", fastener)
    doc = _table_for(std, "counterbore")
    rows = doc["rows"]["cbore"].get(fastener)
    if rows is None:
        raise ValueError(
            f"fastener {fastener!r} has no head table; known: "
            f"{', '.join(sorted(doc['rows']['cbore']))}")
    row = rows.get(size)
    if row is None:
        raise _unknown_size(size, std, f"counterbore ({fastener})", rows)
    inch = doc["units"] == "in"
    dia_clear = CBORE_DIA_CLEARANCE_IN if inch else CBORE_DIA_CLEARANCE
    depth_clear = CBORE_DEPTH_CLEARANCE_IN if inch else CBORE_DEPTH_CLEARANCE
    head_d, head_h = float(row["head_d"]), float(row["head_h"])
    return {"family": "counterbore", "std": std, "size": size,
            "fastener": fastener,
            "head_d": _mm(head_d, doc), "head_h": _mm(head_h, doc),
            "head_d_native": head_d, "head_h_native": head_h,
            "d": _mm(head_d + dia_clear, doc),
            "depth": _mm(head_h + depth_clear, doc),
            "d_native": head_d + dia_clear,
            "depth_native": head_h + depth_clear,
            "rule": CBORE_RULE, **_provenance(doc)}


def csk(size: str, *, angle: float | None = None, fastener: str | None = None,
        std: str = "iso") -> dict:
    """Countersink for a flat-head fastener.

    `d` is the fastener's **theoretical sharp** head diameter — the dimension a
    countersink callout names — so no clearance is added: it already stands off
    the machined head max. Lengths are millimetres, `*_native` the table's own.
    """
    std = _check_std(std)
    size = _check_size(size)
    fastener = _fastener_for(std, "countersink", fastener)
    doc = _table_for(std, "countersink")
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
            "fastener": fastener, "head_d": _mm(head_d, doc),
            "head_d_native": head_d, "d": _mm(head_d, doc),
            "d_native": head_d, "angle_deg": resolved, **_provenance(doc)}


def sizes(family: str, *, std: str = "iso") -> list[str]:
    """The sizes tabulated for a family, **in table order**.

    Table order, not sorted order: `#8 < #10 < 1/4` is an ordering only the
    table knows (`float(size[1:])` reads `1/4` as 1.0 and puts it last, and
    reads `B` not at all). The files are written in ascending size, so
    preserving their order is both correct and free.
    """
    std = _check_std(std)
    family = _check_family(family)
    if family in ("clearance", "tapped"):
        return list(_table_for(std, family)["rows"])
    doc = _table_for(std, family)
    group = "cbore" if family == "counterbore" else "csk"
    seen: dict[str, None] = {}
    for rows in doc["rows"][group].values():
        for size in rows:
            seen.setdefault(size, None)
    return list(seen)


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
        doc = _table_for(std, family)
        fits = clearance_fits(size, std=std)
        return {"std": std, "family": family, "size": _check_size(size),
                "fits": fits,
                "designations": {name: designation("clearance", d=d, std=std)
                                 for name, d in fits.items()},
                **_provenance(doc)}
    if family == "tapped":
        row = thread(size, std=std)
        doc = _table_for(std, family)
        # A list, not a dict keyed by pitch: JSON has no numeric keys, and an
        # agent reading "which pitches exist for M8" wants them in order.
        # The key is a millimetre pitch for ISO and a thread count for ASME,
        # so it is reported under the name it actually has.
        label = "tpi" if std == "ansi" else "pitch"
        row["pitches"] = sorted(
            ({label: float(key), **entry}
             for key, entry in doc["rows"][_check_size(size)]["pitches"].items()),
            key=lambda entry: entry[label], reverse=std != "ansi")
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

    **Every length here is in the standard's own unit** — millimetres for ISO,
    inches for ASME — because this is text for a drawing. A caller holding
    millimetres (which is everything geometric in this system) converts with
    `in_designation_units` first; the lookups above already have.
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
