"""ISO/ANSI hole standards: the vendored tables, the lookups, the designations.

**This module is OCP-free and must stay that way.** It is the third toolkit
module (with `sketch.py` and `specs.py`) that runs in the *server* process —
`core/tools_holes.py`'s `hole_standards` tool imports it, and the server must
never import build123d/OCP. `tests/test_toolkit_ocp_free.py` asserts it in a
fresh interpreter with `OCP` blocked at `sys.meta_path`.

The data lives in `data/*.json`, one file per family group, each carrying
`{schema, standard, units, sources, row_shape, provenance, revision, rows}`.

What this module guarantees about provenance, exactly
-----------------------------------------------------
**The shipped guarantee is the one the loader enforces, and it is not "two
sources agree or the row does not ship".** That sentence was written in the
PRD and was never true of the code: the loader only ever checked that the
*file* named two source strings, so a row transcribed from one publication
shipped as corroborated because its neighbours in the same file had two, and
`ansi_clearance.json` shipped a cell whose two sources it documents as
DISAGREEING while reporting `corroborated: true`. Neither is a wrong number —
no wrong numeric value has been found — but the provenance claim could not
establish transcription accuracy, which is the only thing it exists for.

What is enforced now, mechanically, on every load:

1. **Every data CELL is covered by an explicit provenance entry naming its own
   sources** — one fit of one clearance size, one pitch of one thread size, one
   size of one head table. `provenance.groups` lists those cells by name (one
   entry per source set); `provenance.scopes` overrides a single cell.
   There is no file-wide default **and no row-level fallback**: both were the
   same mistake at different depths. Covering by row let a new *cell* on an
   existing row inherit the row's citations — measured, an `M12×1.5` pitch with
   a fabricated `tap_drill: 99.9` loaded and answered `corroborated: true` over
   two named publications, while a whole new row was correctly refused. A cell
   with no entry is a load error naming the cell.
2. **Every provenance entry names a cell that exists**, so a typo cannot leave
   a real cell silently uncovered.
3. **`sources` are distinct after normalising whitespace and case**, or one
   citation pasted twice with a trailing space would read as two independent
   publications and answer `corroborated: true`.
4. **`corroborated` means "at least two independent sources that AGREE".** A
   cell with one source is `corroborated: false`; so is a cell whose sources are
   recorded as having disagreed (`conflicts` non-empty), whatever the number of
   them. `corroborated: false` is a fact about the cell, not an error: the
   honest thing to do with a one-source or disputed cell is to ship it saying
   so, in the answer, every time it is asked.

All six shipped files are validated at **import** (`validate_all`), so a
malformed one is one error naming the file and the cell rather than a kernel
error discovered halfway through a build.

The file-level `sources` is the **union** over the file's cells and speaks for
none of them. `iso_cbore_csk.json` names four: two back the ISO 4762
socket-head column, one backs the whole ISO 10642 countersunk column on its own
(so all nine of those rows answer `corroborated: false`), and one was
*consulted for the counterbore convention and deliberately not transcribed*, so
it backs no row at all.

**Where this flag is and is not visible.** `corroborated`/`conflicts`/`sources`
travel on every lookup answer, on the `hole_standards` tool, **and on every
hole record** (`record["provenance"]`, from `toolkit.holes`) and out through
`add_holes`. They were record-invisible until round 3, which meant the
single-sourced ISO 10642 ⌀17.92 and the adjudicated ANSI ⌀0.196 became
manufacturing callouts with the label left behind in the table.

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

#: Every unit spelling a vendored table may legitimately use, and its factor to
#: millimetres. The lookup is **total**: an unrecognised spelling raises rather
#: than passing the number through unconverted. `_mm` used to convert iff the
#: string was exactly `"in"` and `table()` only checked that the field was
#: truthy, so a file that spelled it `"inch"` would have shipped inches under
#: this module's millimetre contract with no symptom whatsoever — the same
#: 25.4x error the `*_native` split exists to prevent, arriving through the
#: header instead of through a formatter.
_UNIT_FACTORS = {
    "mm": 1.0, "millimetre": 1.0, "millimetres": 1.0,
    "millimeter": 1.0, "millimeters": 1.0,
    "in": MM_PER_INCH, "inch": MM_PER_INCH, "inches": MM_PER_INCH,
}

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

# THE FLAT DIAMETER RULE IS GUARDED BELOW THE HEAD IT WAS SET ON.
#
# The 1.5 mm was chosen against the published charts **from M5 up** (DIN
# 974-1's M5 counterbore is 10.0 = the 8.5 head + 1.5), and the 1/16 in against
# the 1/4 in socket head (0.375 + 0.0625 = 7/16, the shop's own number). Below
# those heads a flat absolute clearance is a third of the whole hole: measured,
# `cbore("M2")` bored **5.3** where DIN 974-1 gives 4.3, on a 3.8 mm head.
#
# So below the head the rule was set on, the clearance is the same PROPORTION
# of the head that it is at that threshold. That makes it continuous — the two
# branches agree exactly at M5 and at 1/4 in, so no published value this repo
# has ever quoted moves — and it keeps the direction the rule was chosen for:
# M2 comes out 4.47 against DIN 974-1's 4.3, i.e. still large rather than
# small, because a counterbore that is too big fails visibly and one that is
# too small traps the head. The threshold is stated as a HEAD diameter, not a
# nominal size, because the rule is a statement about the head and because the
# inch tables index sizes (`#0`, `1/4`) whose nominal diameter is not in them.
CBORE_DIA_FLAT_MIN_HEAD = 8.5          # the ISO 4762 M5 head
CBORE_DIA_FLAT_MIN_HEAD_IN = 0.375     # the ASME B18.3 1/4 in head
# The DEPTH clearance is deliberately left flat in both units: no published
# small-size depth was measured against it during the review that found the
# diameter defect, and inventing a second guard from nothing would be exactly
# the "derived number presented as a table value" this module exists to avoid.
CBORE_RULE = (
    f"counterbore = head diameter + {CBORE_DIA_CLEARANCE} mm "
    f"({CBORE_DIA_CLEARANCE_IN} in), depth = head height + "
    f"{CBORE_DEPTH_CLEARANCE} mm ({CBORE_DEPTH_CLEARANCE_IN} in). Below a "
    f"{CBORE_DIA_FLAT_MIN_HEAD} mm ({CBORE_DIA_FLAT_MIN_HEAD_IN} in) head the "
    "diameter clearance is applied as the same PROPORTION of the head it has "
    "there, because the flat number was only ever checked from that head up "
    "(on an M2 head the flat rule bores 5.3 where DIN 974-1 gives 4.3). A "
    "shop default, not a standard: the published counterbore charts disagree "
    "(see the data file's notes). The head dimensions are the standard part "
    "of this answer."
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

#: Every table that ships, so `validate_all` can prove the whole set at once.
SHIPPED_TABLES = ("iso_clearance", "iso_thread", "iso_cbore_csk",
                  "ansi_clearance", "ansi_thread", "ansi_cbore_csk")


def validate_all() -> None:
    """Load and validate every shipped table, raising the first problem.

    Called at the bottom of this module, so **importing it is a proof that all
    six vendored files are well formed** rather than a promise to find out
    later, one file at a time, in whichever process happens to ask first —
    which for a hole lookup is the kernel worker in the middle of a build.
    Measured at 0.58 ms for all six against the module's own 8.6 ms import, and
    it warms `table`'s cache, so it costs nothing anyone will notice.

    The honest limit: this module is itself imported lazily by its callers
    (`core/tools_holes` imports it inside its functions, so the server can stay
    OCP- and cost-free until someone asks about a hole). "Eager" therefore
    means eager *within the module*, not at process start — a malformed
    vendored file still surfaces on the first hole-standards call of the
    process, but it surfaces as one error naming the file and the cell, before
    any lookup has half-answered.
    """
    for name in SHIPPED_TABLES:
        table(name)


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
    for key in ("standard", "units", "sources", "row_shape", "provenance",
                "revision", "rows"):
        if not doc.get(key):
            raise ValueError(f"{path.name}: missing header key {key!r}")
    try:
        _unit_factor(doc["units"])
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    if doc["row_shape"] not in _ROW_SHAPES:
        raise ValueError(
            f"{path.name}: row_shape {doc['row_shape']!r} is not one of "
            f"{list(_ROW_SHAPES)}; the loader walks `rows` by it to check that "
            f"every row declares its own sources")
    sources = doc["sources"]
    if (not isinstance(sources, list)
            or not all(isinstance(text, str) and text.strip()
                       for text in sources)):
        raise ValueError(
            f"{path.name}: `sources` must be a list of non-empty citations. It "
            f"is the UNION over this file's cells and is not a claim about any "
            f"of them — the two-independent-sources rule is per cell, in "
            f"`provenance`, and is reported as `corroborated`")
    # Distinctness is on the NORMALISED text, not the exact string. `n_sources
    # == 2` is the whole substance of `corroborated`, so one citation listed
    # twice with a trailing space (or a capital) used to load as two
    # independent publications and answer `corroborated: true` — the same
    # untrue claim this rule exists to stop, arriving through a copy-paste.
    seen: dict[str, int] = {}
    for index, text in enumerate(sources):
        key = _source_key(text)
        if key in seen:
            raise ValueError(
                f"{path.name}: sources {seen[key]} and {index} are the same "
                f"citation once whitespace and case are normalised, so this "
                f"file names fewer independent publications than it appears "
                f"to. Two entries that differ only in spacing corroborate "
                f"nothing")
        seen[key] = index
    _check_cell_fields(path.name, doc)
    _check_provenance(path.name, doc)
    return doc


#: How the loader walks `rows` to find the data rows a provenance entry must
#: cover, and the cell paths a lookup will ask about. Declared in the file
#: header rather than sniffed, so a table whose shape this module does not know
#: is refused instead of being walked wrongly and passing vacuously.
_ROW_SHAPES = ("size/fit", "size/pitch", "group/fastener/size")


#: Characters that render as nothing in a citation and would otherwise make one
#: publication read as two. `str.split()` already handles every *visible*
#: whitespace and NBSP (`'\xa0'.isspace()` is True); these are the ones it does
#: not, because Python does not class them as space. Named rather than
#: described as "zero-width characters", which is a wider claim than any finite
#: set can honour: U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ, U+200E LRM, U+2060
#: WORD JOINER, U+FEFF BOM, U+00AD SOFT HYPHEN and U+180E MONGOLIAN VOWEL
#: SEPARATOR. The last three were added in round 5 after they were found
#: outside the set while the docstring claimed to cover them.
_INVISIBLE = "​‌‍‎⁠﻿­᠎"


def _source_key(text: str) -> str:
    """A citation reduced to what makes it a distinct publication.

    The eight codepoints in `_INVISIBLE` dropped — named, not "zero-width
    characters", which is a wider claim than a finite set can honour —
    whitespace runs collapsed (which covers NBSP), ends trimmed, `http://`
    folded onto `https://`, case folded. Every one of those is a way to paste
    the same URL twice and have the file claim two independent publications
    behind one number.
    """
    cleaned = str(text)
    for char in _INVISIBLE:
        cleaned = cleaned.replace(char, "")
    cleaned = " ".join(cleaned.split()).casefold()
    return cleaned.replace("http://", "https://")


def _cell_paths(doc: dict) -> list[tuple]:
    """Every path a LOOKUP will ask `_prov_scope` about — one per data cell,
    and **the set the coverage proof is over**.

    **What a cell is, exactly, because "every cell" has to mean something.** It
    is the path `row_shape` names and no finer: one *fit* of one clearance
    size, one *pitch* (or other scalar) of one thread size, one *size* of one
    fastener head table. The fields inside a cell — ASME clearance's
    `{d, drill}`, a head row's `{head_d, head_h}` — are covered by that cell's
    citation and are **not declared one by one**, uniformly across all three
    shapes. That is a deliberate stopping point: a cell is what one line of the
    published table prints, and `_prov_scope` resolves at exactly this depth,
    so declaring below it would make a cell's provenance a union over its
    fields — a redesign. 248 declarations against 442 scalar leaves is the gap,
    and it is stated rather than papered over: an added field inside a declared
    cell loads, an added *cell* does not.

    **What that gap is, stated correctly.** It is NOT "a field no lookup
    reads" — that clause was checkably false and is the reason this paragraph
    was rewritten. `_clearance_cell` and `lookup`'s tapped branch both read an
    *optional* in-cell field with a default (`cell.get("drill")`,
    `entry.get("drill")`), and ISO thread pitch cells carry no `drill`, so one
    is addable: measured, `drill: "FAKE-99"` inside the declared `M8/1.25` cell
    loaded and `thread("M8")` served it with `corroborated: True`. The honest
    justification is the one below it: **a value added to an optional field is
    exactly as uncatchable as a value edited in a required one**, and anyone
    who can add `drill` can equally change `tap_drill`. Coverage proves
    citation, never correctness — that is true at every granularity, and going
    finer would not change it.

    What `_check_cell_fields` *does* close is the other half: a field name no
    cell of this shape may carry at all. That refuses a fabricated
    `fabricated_mm`, and it turns a typo like `head_dd` — which leaves
    `cbore()` to raise `KeyError` halfway through an answer — into a load error
    naming the file. It does **not** refuse `drill`, because `drill` is a
    legitimate field of a pitch cell; saying otherwise would be the same kind
    of over-claim.

    A cell is finer than a row (one fit of one clearance size, one pitch of one
    thread size), and the difference is not cosmetic: proving coverage at the
    ROW level let a new *cell* on an existing row inherit that row's citations.
    Measured — an `M12×1.5` pitch with a fabricated `tap_drill: 99.9` added to
    the group-covered `M12` row of `iso_thread` loaded, and
    `thread("M12", pitch=1.5)` answered `corroborated: True` over two named
    publications. A whole new row was correctly refused the whole time, which
    is what made it easy to believe the cell case was covered too. And
    `size/pitch` tables are exactly where the data legitimately grows: a size
    gains a fine pitch far more often than a table gains a size.
    """
    return [path for path, _value in _cell_items(doc)]


def _cell_items(doc: dict) -> list[tuple]:
    """`(path, value)` for every data cell — the ONE walker.

    `_cell_paths` and `_check_cell_fields` both come off this, so the set the
    coverage proof is over and the set whose field names are checked cannot
    drift apart. Two walkers over one structure is how `coarse_pitch` came to
    be outside the coverage set in the first place.
    """
    rows = doc["rows"]
    shape = doc["row_shape"]
    if shape == "size/fit":
        return [((size, key), cell)
                for size, row in rows.items() for key, cell in row.items()]
    if shape == "size/pitch":
        # THE COMPLEMENT, not a list of names. `pitches` is the one container
        # on a thread row; every other key on it is a transcribed datum, and
        # `coarse_pitch` — which names the pitch `thread(size)` answers from —
        # is the one that ships. Enumerating names instead left any *other*
        # scalar undeclared: measured, an added `M8/preferred_pitch` loaded
        # clean. (Flipping `coarse_pitch` 1.25 -> 1.0 makes `thread("M8")`
        # answer tap drill 7.0 instead of 6.8, which is why the row's scalars
        # are data and not decoration.)
        return [((size, key), value)
                for size, row in rows.items()
                for key, value in (*((k, v) for k, v in row.items()
                                     if k != "pitches"),
                                   *row["pitches"].items())]
    return [((group, fastener, size), cell)
            for group, families in rows.items()
            for fastener, sizes in families.items()
            for size, cell in sizes.items()]


#: The field names a data cell may carry, per `row_shape`, and the ones it
#: must. A closed schema over what the six shipped files actually use, so a
#: name outside it is either a fabrication or a typo — and a typo is the one
#: that otherwise surfaces as a `KeyError` from inside a lookup rather than as
#: a load error naming the file.
_CELL_FIELDS = {
    "size/fit": ({"d", "drill"}, {"d"}),
    "size/pitch": ({"tap_drill", "drill", "series"}, {"tap_drill", "series"}),
    "group/fastener/size": ({"head_d", "head_h", "angle_deg"}, {"head_d"}),
}


def _check_cell_fields(name: str, doc: dict) -> None:
    """Every cell is a number or an object whose field names this shape knows.

    Deliberately narrow, and the docstring on `_cell_paths` says what it does
    not do: it cannot see a *value* — neither one edited in a required field
    nor one added to a legitimate optional one — because coverage proves
    citation and never correctness.
    """
    allowed, required = _CELL_FIELDS[doc["row_shape"]]
    for path, cell in _cell_items(doc):
        where = f"{name}: cell {'/'.join(path)!r}"
        if not isinstance(cell, dict):
            if isinstance(cell, bool) or not isinstance(cell, (int, float)):
                raise ValueError(
                    f"{where} must be a number or an object, got "
                    f"{type(cell).__name__}")
            continue
        extra = sorted(set(cell) - allowed)
        if extra:
            raise ValueError(
                f"{where} carries field(s) {extra}, which no {doc['row_shape']}"
                f" cell may have (known: {sorted(allowed)}). A name this "
                f"module does not read is either a fabrication or a typo for "
                f"one it does")
        missing = sorted(required - set(cell))
        if missing:
            raise ValueError(
                f"{where} is missing required field(s) {missing}; a lookup "
                f"would raise KeyError halfway through an answer")


def _check_provenance(name: str, doc: dict) -> None:
    """Expand `provenance.groups` into per-cell entries and prove the result
    covers **every data cell** exactly once.

    **There is no file-wide default, and there is no row-level fallback
    either.** Both were the same mistake at different depths: a default let a
    row inherit the file's citations, and a row entry let a new *cell* inherit
    the row's. Every entry names one cell — `_prov_scope`'s prefix walk
    therefore always matches at full depth — so a cell added without a
    declaration is a load error naming the cell, which is the only place the
    mistake is cheap.

    `groups` exists so that stating this costs one entry per *source set*
    rather than one per cell; the cells a group covers are still named one by
    one, so nothing can be added to the file without declaring where it came
    from.
    """
    block = doc["provenance"]
    if not isinstance(block, dict):
        raise ValueError(f"{name}: provenance must be an object")
    if "default" in block:
        raise ValueError(
            f"{name}: provenance has a `default` entry. A file-wide default "
            f"cannot speak for a row — use `groups` to name the rows each "
            f"source set covers")
    scopes = block.get("scopes") or {}
    groups = block.get("groups") or []
    if not isinstance(scopes, dict) or not isinstance(groups, list):
        raise ValueError(
            f"{name}: provenance `scopes` must be an object and `groups` a "
            f"list")

    def _check_entry(scope: str, entry) -> None:
        indices = entry.get("sources") if isinstance(entry, dict) else None
        if not indices:
            raise ValueError(
                f"{name}: provenance scope {scope!r} is backed by nothing")
        for index in indices:
            if not isinstance(index, int) or not 0 <= index < len(doc["sources"]):
                raise ValueError(
                    f"{name}: provenance scope {scope!r} names source "
                    f"{index!r}, which is not an index into this file's "
                    f"{len(doc['sources'])} sources")
        note = entry.get("conflict")
        if note is not None and not (isinstance(note, str) and note.strip()):
            raise ValueError(
                f"{name}: provenance scope {scope!r} has an empty `conflict`; "
                f"a recorded disagreement has to say what disagreed")

    # `groups` expand into ordinary scopes, so `_prov_scope` never has to know
    # they existed and a group and a scope cannot silently claim one cell twice.
    expanded: dict[str, dict] = {}
    for position, group in enumerate(groups):
        cells = group.get("cells") if isinstance(group, dict) else None
        if not isinstance(cells, list):
            raise ValueError(
                f"{name}: provenance group {position} must name the `cells` it "
                f"covers")
        entry = {key: value for key, value in group.items() if key != "cells"}
        _check_entry(f"groups[{position}]", entry)
        for cell in cells:
            if cell in expanded:
                raise ValueError(
                    f"{name}: cell {cell!r} is claimed by two provenance "
                    f"groups")
            expanded[cell] = entry
    for scope, entry in scopes.items():
        # A `scopes` entry deliberately OVERRIDES a group's claim on the same
        # cell rather than colliding with it: that is how one cell carries a
        # recorded disagreement its neighbours in the same row do not.
        _check_entry(scope, entry)
        expanded[scope] = entry

    paths = _cell_paths(doc)
    known = {"/".join(path) for path in paths}
    if len(known) != len(paths):
        # Two different cells whose `/`-joined names collide. It does not
        # happen in the shipped files, but the inch tables make it reachable:
        # a size `1` with a pitch `4` and a size `1/4` both spell `1/4`, and
        # one provenance entry would then silently answer for two cells — the
        # coverage proof would pass with one of them undeclared. Exercised by
        # `test_two_cells_that_spell_one_provenance_path_do_not_load`.
        collided = sorted({"/".join(p) for p in paths
                           if [q for q in paths if q != p
                               and "/".join(q) == "/".join(p)]})
        raise ValueError(
            f"{name}: data cells {collided} share one '/'-joined provenance "
            f"path, so one entry would answer for both. Rename the key")
    for scope in expanded:
        if scope not in known:
            raise ValueError(
                f"{name}: provenance scope {scope!r} names no data cell in "
                f"this file, so it backs nothing. Entries are per CELL — a "
                f"row-level entry would let a cell added later inherit it")
    uncovered = ["/".join(path) for path in _cell_paths(doc)
                 if "/".join(path) not in expanded]
    if uncovered:
        raise ValueError(
            f"{name}: cell(s) {uncovered[:5]} declare no sources "
            f"({len(uncovered)} in total). Every cell — one fit of one "
            f"clearance size, one pitch or other scalar of one thread size, "
            f"one size of one head table; the fields INSIDE a cell are "
            f"covered by its citation and are not declared separately — names "
            f"the publications it was transcribed from. There is no row-level "
            f"or file-wide default to fall back on, because a default at "
            f"either depth is what let an undeclared cell claim its "
            f"neighbours' citations")
    # The expansion is what `_prov_scope` reads, and it is cached with the doc.
    block["scopes"] = expanded


def _table_for(std: str, family: str) -> dict:
    name = _TABLES.get((std, family))
    if name is None:                                      # pragma: no cover
        raise ValueError(
            f"std {std!r}: no {std.upper()} {family} table ships. A lookup "
            f"that cannot be answered from a vendored table is an error, "
            f"never the other standard's numbers under this label.")
    return table(name)


def _prov_scope(doc: dict, path: tuple) -> dict:
    """The provenance entry governing one row path.

    Most specific wins: `("#8", "normal")` tries `"#8/normal"`, then `"#8"`.
    That is what lets one *cell* of one row carry a recorded source conflict
    while its neighbours in the same row carry none. There is no file-wide
    fallback — `_check_provenance` proved on load that every row has an entry,
    so reaching the end of this loop is a bug here, never a missing citation
    quietly answered from the file header.
    """
    scopes = doc["provenance"].get("scopes") or {}
    for stop in range(len(path), 0, -1):
        entry = scopes.get("/".join(path[:stop]))
        if entry is not None:
            return entry
    raise ValueError(                                         # pragma: no cover
        f"no provenance covers row path {'/'.join(path)!r}; the loader's "
        f"coverage check should have refused this file")


def _provenance(doc: dict, *paths: tuple) -> dict:
    """What actually backs THESE rows — never the whole file's source list.

    Several paths union (a clearance answer covers three cells of one row, a
    tapped answer every pitch of one size), because an answer that spans cells
    must claim what backs all of them and no more.

    **`corroborated` is "two independent sources that AGREE"**, evaluated on
    *these* rows: two or more citations **and** no recorded disagreement. The
    conjunction is the point. `ansi_clearance`'s `#8 normal` has two sources
    that printed the cell differently; reporting that as corroborated said the
    transcription was checked when what actually happened is that a
    disagreement was adjudicated, which is a weaker fact and has to read as
    one. `conflicts` carries the adjudication.
    """
    entries = [_prov_scope(doc, tuple(path)) for path in paths]
    if not entries:                                           # pragma: no cover
        raise ValueError("a provenance answer must name at least one row")
    indices: list[int] = []
    conflicts: list[str] = []
    standards: list[str] = []
    for entry in entries:
        for index in entry["sources"]:
            if index not in indices:
                indices.append(index)
        note = entry.get("conflict")
        if note and note not in conflicts:
            conflicts.append(note)
        name = entry.get("standard") or doc["standard"]
        if name not in standards:
            standards.append(name)
    return {
        # A file may transcribe two standards (ISO 4762 *and* ISO 10642); a row
        # is under exactly one, so a scope may narrow the header's `standard`.
        "standard": standards[0] if len(standards) == 1 else doc["standard"],
        "revision": doc["revision"], "units": doc["units"],
        "sources": [doc["sources"][index] for index in indices],
        "corroborated": len(indices) >= 2 and not conflicts,
        "conflicts": conflicts,
    }


def _unit_factor(units) -> float:
    """Millimetres per `units`, or `ValueError` naming the field.

    Total by construction — see `_UNIT_FACTORS`. A unit this module cannot read
    is refused, never passed through as if it were already millimetres.
    """
    factor = (_UNIT_FACTORS.get(units.strip().lower())
              if isinstance(units, str) else None)
    if factor is None:
        raise ValueError(
            f"units must be one of {sorted(_UNIT_FACTORS)}, got {units!r}; a "
            f"table whose unit this module cannot read is refused, never "
            f"passed through unconverted")
    return factor


def _mm(value, doc: dict) -> float:
    """A table value in the kernel's unit. The file says which unit it is in;
    nothing downstream has to know."""
    return float(value) * _unit_factor(doc["units"])


def _is_inch(doc: dict) -> bool:
    """Whether this table's own unit is the inch — asked through the same total
    lookup `_mm` uses, so the two can never disagree about a spelling."""
    return _unit_factor(doc["units"]) != 1.0


def _fastener_for(std: str, family: str, fastener: str | None) -> str:
    return fastener if fastener else _DEFAULT_FASTENER[(std, family)]


# ------------------------------------------------------------------ lookups

def _cell_key(row: dict, key: str, std: str) -> str:
    """The JSON key one fit has in one row: the standard's own spelling when
    the file uses it, the internal ISO name otherwise. Provenance is per row
    *cell*, so the key the file actually uses is what a provenance path names.
    """
    named = _FIT_NAMES[std][key]
    return named if named in row else key


def _clearance_cell(row: dict, key: str, std: str, doc: dict) -> tuple:
    """`(d_mm, d_native, drill|None)` for one fit of one clearance row.

    The two files are shaped differently on purpose: the ISO cell is a bare
    millimetre number because a metric clearance hole *is* the drill, while the
    ASME cell carries a number/letter/fraction drill designation which is part
    of the callout and cannot be derived from the decimal.
    """
    cell = row[_cell_key(row, key, std)]
    if isinstance(cell, dict):
        return _mm(cell["d"], doc), float(cell["d"]), cell.get("drill")
    return _mm(cell, doc), float(cell), None


def _clearance_row(size: str, std: str) -> tuple:
    """`(doc, size, row)` for a clearance size, or the `size` error."""
    doc = _table_for(std, "clearance")
    row = doc["rows"].get(size)
    if row is None:
        raise _unknown_size(size, std, "clearance", doc["rows"])
    return doc, row


def clearance(size: str, *, fit: str = "medium", std: str = "iso") -> dict:
    """Clearance hole for `size` at `fit` — ISO 273, or ASME B18.2.8.

    `d` is **millimetres**, always: it is a number for the geometry. `d_native`
    and `designation` are in the standard's own units (inches for ASME), which
    is what a callout prints.
    """
    std = _check_std(std)
    size = _check_size(size)
    doc, row = _clearance_row(size, std)
    key = _FIT_ALIASES.get(str(fit).lower())
    if key is None:
        raise ValueError(
            f"fit must be one of {sorted(set(_FIT_ALIASES))}, got {fit!r}")
    d_mm, native, drill = _clearance_cell(row, key, std, doc)
    return {"family": "clearance", "std": std, "size": size,
            "fit": _FIT_NAMES[std][key], "d": d_mm, "d_native": native,
            "drill": drill,
            "designation": designation("clearance", d=native, std=std),
            **_provenance(doc, (size, _cell_key(row, key, std)))}


def clearance_fits(size: str, *, std: str = "iso") -> dict:
    """All three fits at once, in **millimetres** — what the `hole_standards`
    tool answers (AC3).

    Millimetres, like every other geometric length this module returns, and
    named `clearance_fits` with no unit suffix precisely because that is the
    module-wide default. The table's own unit is `clearance_fits_native`. The
    two are the same numbers for ISO and differ by 25.4x for ASME, which is
    exactly why the inch answer may not sit under the bare name.
    """
    std = _check_std(std)
    size = _check_size(size)
    doc, row = _clearance_row(size, std)
    return {name: _clearance_cell(row, key, std, doc)[0]
            for key, name in _FIT_NAMES[std].items()}


def clearance_fits_native(size: str, *, std: str = "iso") -> dict:
    """All three fits at once in the **table's own unit** — inches for ASME.

    This is what a callout prints, so it is what `designation()` takes.
    """
    std = _check_std(std)
    size = _check_size(size)
    doc, row = _clearance_row(size, std)
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
            **_provenance(doc, (size, key))}


def _cbore_dia_clearance(head_d: float, inch: bool) -> float:
    """The counterbore's diameter clearance for one head, in the table's unit.

    Flat at or above the head the flat number was checked on, and the same
    proportion of the head below it — see `CBORE_DIA_FLAT_MIN_HEAD` for the
    measurement that forced the guard (`cbore("M2")` bored 5.3 against DIN
    974-1's 4.3). Continuous at the threshold by construction, so no value this
    repo has quoted for M5 and up, or for 1/4 in and up, moves.
    """
    flat = CBORE_DIA_CLEARANCE_IN if inch else CBORE_DIA_CLEARANCE
    floor_head = CBORE_DIA_FLAT_MIN_HEAD_IN if inch else CBORE_DIA_FLAT_MIN_HEAD
    if head_d >= floor_head:
        return flat
    return flat * head_d / floor_head


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
    inch = _is_inch(doc)
    depth_clear = CBORE_DEPTH_CLEARANCE_IN if inch else CBORE_DEPTH_CLEARANCE
    head_d, head_h = float(row["head_d"]), float(row["head_h"])
    dia_clear = _cbore_dia_clearance(head_d, inch)
    return {"family": "counterbore", "std": std, "size": size,
            "fastener": fastener,
            "head_d": _mm(head_d, doc), "head_h": _mm(head_h, doc),
            "head_d_native": head_d, "head_h_native": head_h,
            "d": _mm(head_d + dia_clear, doc),
            "depth": _mm(head_h + depth_clear, doc),
            "d_native": head_d + dia_clear,
            "depth_native": head_h + depth_clear,
            "rule": CBORE_RULE,
            **_provenance(doc, ("cbore", fastener, size))}


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
            "d_native": head_d, "angle_deg": resolved,
            **_provenance(doc, ("csk", fastener, size))}


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

    **Every length in the answer is millimetres**, including `fits` and every
    `pitches[...]["tap_drill"]`, with the table's own unit alongside under
    `fits_native` / `tap_drill_native`. Both of those used to hand back the
    ASME table's inches under the bare, documented-as-millimetres key.
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
        size = _check_size(size)
        doc, row = _clearance_row(size, std)
        # `fits` is MILLIMETRES under the bare name, like every other geometric
        # length here — it used to be the ASME table's inches, which made the
        # tool's headline answer 25.4x wrong under the one key its own
        # description defines as millimetres. `fits_native` is the callout's.
        fits = clearance_fits(size, std=std)
        native = clearance_fits_native(size, std=std)
        cells = [_cell_key(row, key, std) for key in _FIT_NAMES[std]]
        return {"std": std, "family": family, "size": size,
                "fits": fits, "fits_native": native,
                "designations": {name: designation("clearance", d=d, std=std)
                                 for name, d in native.items()},
                **_provenance(doc, *((size, cell) for cell in cells))}
    if family == "tapped":
        size = _check_size(size)
        row = thread(size, std=std)
        doc = _table_for(std, family)
        pitches = doc["rows"][size]["pitches"]
        # A list, not a dict keyed by pitch: JSON has no numeric keys, and an
        # agent reading "which pitches exist for M8" wants them in order.
        # The key is a millimetre pitch for ISO and a thread count for ASME,
        # so it is reported under the name it actually has.
        #
        # Every entry is REBUILT rather than spliced in from the table. Raw,
        # `tap_drill` arrived in the file's own unit — 0.201 inches under the
        # same key whose top level says 5.1054 millimetres — so the documented
        # way to pick UNF over UNC answered in the wrong unit.
        label = "tpi" if std == "ansi" else "pitch"
        row["pitches"] = sorted(
            ({"pitch": MM_PER_INCH / float(key) if std == "ansi"
              else float(key),
              "tpi": float(key) if std == "ansi" else None,
              "series": entry["series"],
              "tap_drill": _mm(entry["tap_drill"], doc),
              "tap_drill_native": float(entry["tap_drill"]),
              "drill": entry.get("drill")}
             for key, entry in pitches.items()),
            key=lambda entry: entry[label], reverse=std != "ansi")
        # The answer spans every pitch of the size, so its provenance does too.
        row.update(_provenance(doc, *((size, key) for key in pitches)))
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
    | clearance, blind | `⌀5.5 ↧6` | `⌀0.217 ↧0.25` |
    | tapped | `M5×0.8 - 6H ↧12` | `10-24 UNC - 2B ↧0.5` |
    | counterbore | `⌀5.5 ⌴⌀9.5↧5.4` | `⌀0.217 ⌴⌀0.375↧0.213` |
    | counterbore, blind | `⌀5.5 ↧6 ⌴⌀9.5↧5.4` | `⌀0.217 ↧0.25 ⌴⌀0.375↧0.213` |
    | countersink | `⌀5.5 ⌵⌀10.4×90°` | `⌀0.217 ⌵⌀0.41×82°` |
    | countersink, blind | `⌀5.5 ↧6 ⌵⌀10.4×90°` | `⌀0.217 ↧0.25 ⌵⌀0.41×82°` |

    The glyphs are shared; the numbers and the thread designation are what the
    standard changes. A `depth` of `None` means a through hole and the depth
    glyph is omitted — a through hole is not "depth 0".

    **`depth` is the HOLE's depth, and a blind hole always prints it.** A
    counterbore has two depths and they are disambiguated the way ISO 129 and
    ASME Y14.5 both disambiguate them: **each `↧` qualifies the `⌀` group it
    follows**, so `⌀5.5 ↧6 ⌴⌀9.5↧5.4` is a 6 mm deep ⌀5.5 hole under a 5.4 mm
    deep ⌀9.5 pocket, and the pocket's depth can never be read as the hole's.
    The two standards do not disagree about that; where drafting practice
    varies is only whether the two lines are stacked in a note (they cannot be
    here — this is one string), so the ordering rule is what carries it.
    Omitting the hole depth entirely, which is what `clearance`, `counterbore`
    and `countersink` used to do, is the one spelling that IS misread: a
    counterbored ⌀5.5 blind to 6 mm read as a through hole.

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
    # The hole's own diameter, then its own depth if it has one. Every family
    # below appends its seat to THIS, so the hole depth is never omitted by one
    # of them and never confusable with the seat's.
    head = f"{_DIA}{_num(d, std)}"
    if depth is not None:
        head = f"{head} {_DEPTH}{_num(depth, std)}"
    if family == "clearance":
        return head
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


# ------------------------------------------------------- the record contract

#: The `family` values a `toolkit.holes` record may carry. `drilled` is not a
#: table family (it has no row — see `holes.drill`) but it is a record family,
#: and it prints with the clearance grammar.
RECORD_FAMILIES = ("drilled", "clearance", "tapped", "counterbore",
                   "countersink")

#: Every key a record must carry, with the types a reader may assume. This is
#: the ONE list: the worker's harvest raises on it, the drawing pack skips a
#: record that fails it, and the sidecar reader discards a stored document that
#: does. A five-field spot-check in one of those places is how a record that no
#: helper produced became a manufacturing callout.
RECORD_KEYS: dict[str, tuple] = {
    "id": (str,),
    "family": (str,),
    "standard": (str,),
    # `size` and `fit` SELECT the published row every other check re-derives
    # from, so they belong here and are typed and cross-checked against `d`
    # below. They were neither, and a key that steers validation while being
    # itself unvalidated is the shape of this whole defect class: changing
    # `size` from `#8` to `#10` (and the provenance with it, consistently) left
    # `d` and the callout on the disputed ⌀0.196 while the record claimed
    # corroboration over agreeing publications.
    "size": (str, type(None)),
    "fit": (str, type(None)),
    "designation": (str,),
    "d": (int, float),
    "count": (int,),
    "positions": (list,),
    "centers": (list,),
    "thru": (bool,),
}

#: Which of `size`/`fit` each record family must carry, and which it must not.
#: `drilled` takes millimetres and has no row; a tapped hole has a size and no
#: fit; the three clearance-based families have both.
_NAMES_BY_FAMILY = {
    "drilled": (False, False),
    "tapped": (True, False),
    "clearance": (True, True),
    "counterbore": (True, True),
    "countersink": (True, True),
}


def merge_provenance(*answers: dict) -> dict:
    """The provenance a record carries, unioned over the lookups behind it.

    A counterbore answer is TWO published rows — the clearance hole's and the
    fastener head's — and a record that names one of them is claiming more than
    it has. So the sources union, `corroborated` is the **conjunction** (every
    row that fed this hole is corroborated) and `conflicts` unions.

    This exists because `corroborated` used to reach nothing that manufactures.
    Every `hole_standards` answer carried it and the hole record dropped it, so
    the single-sourced ISO 10642 ⌀17.92 seat and the *adjudicated* ANSI ⌀0.196
    clearance hole became callouts with nothing said. Labelling a row and then
    not carrying the label is the same silence the label was written to break.

    **`standard` is ALWAYS a list**, even of one. It used to be a bare string
    for a one-row answer and a list for a counterbore's two, which is a shape a
    reader has to branch on and nothing documented or type-checked; a caller
    that indexed `[0]` got a character.
    """
    sources: list[str] = []
    conflicts: list[str] = []
    standards: list[str] = []
    for answer in answers:
        for text in answer.get("sources") or ():
            if text not in sources:
                sources.append(text)
        for note in answer.get("conflicts") or ():
            if note not in conflicts:
                conflicts.append(note)
        name = answer.get("standard")
        if name and name not in standards:
            standards.append(name)
    return {
        "standard": standards,
        "sources": sources,
        "corroborated": bool(answers) and all(
            bool(answer.get("corroborated")) for answer in answers),
        "conflicts": conflicts,
    }


def provenance_for_record(record: dict) -> dict | None:
    """The provenance a record's own fields ENTITLE it to — re-derived from the
    tables, never read off the record.

    The same move `designation_for_record` makes, for the same reason and after
    the same failure. `validate_record` re-derived the callout and compared, so
    a fabricated designation could not survive; it checked provenance only for
    *internal* consistency, so the genuine disputed ANSI `#8 normal` record with
    its conflict note deleted and `corroborated` flipped to `true` validated
    clean — as did citations naming publications in no table, a `standard` set
    to a wrong standard or an int or nothing at all, and `corroborated: true`
    backed by one citation listed twice.

    Everything needed is on the record: `family`, `standard`, `size`, `fit`,
    and the seat's `fastener`. So the answer is a lookup, and a record's
    provenance is checkable exactly as far as its diameter is.
    """
    rows = rows_for_record(record)
    return merge_provenance(*rows) if rows else None


def rows_for_record(record: dict) -> list[dict]:
    """Every published row this record's own fields select, in order — the
    bore's first.

    One place decides which rows back a record, so the provenance check and the
    diameter check below cannot disagree about it. That mattered: `size` and
    `fit` choose the row, and until they were tied to `d` a record could point
    at one row and carry another's diameter.
    """
    family = record["family"]
    if family == "drilled":
        return []                        # no table row, so nothing to claim
    std = _check_std(record["standard"])
    size = _check_size(record["size"])
    if family == "tapped":
        tap = record.get("tap") or {}
        # ISO keys on the millimetre pitch, Unified on threads per inch: the
        # record carries both, and `thread` takes the one its standard uses.
        pitch = tap.get("tpi") if std == "ansi" else tap.get("pitch")
        return [thread(size, pitch=pitch, std=std)]
    row = clearance(size, fit=record.get("fit") or "medium", std=std)
    if family == "clearance":
        return [row]
    seat = record.get("cbore" if family == "counterbore" else "csk") or {}
    lookup = cbore if family == "counterbore" else csk
    return [row, lookup(size, fastener=seat.get("fastener"), std=std)]


def designation_for_record(record: dict) -> str:
    """The callout a record's own numbers spell — the single place the text is
    derived, so it can be **re-derived and compared**.

    `toolkit.holes` builds every record's `designation` by calling this on the
    finished record, and `validate_record` calls it again and requires the
    stored string to match. That is what turns "a script can `setattr` anything
    onto the shape" from an unbounded claim into a bounded one: a carrier can
    still hold a record for a hole that was later destroyed (geometry is
    checked by the reader that has the geometry), but it can no longer hold a
    designation that contradicts the record's own diameter, depth or thread.
    """
    family = record["family"]
    if family not in RECORD_FAMILIES:
        raise ValueError(
            f"family must be one of {list(RECORD_FAMILIES)}, got {family!r}")
    std = _check_std(record["standard"])
    depth_mm = None if record.get("thru", True) else record.get("depth_mm")
    depth = None if depth_mm is None else in_designation_units(depth_mm, std)
    d = in_designation_units(record["d"], std)
    if family == "tapped":
        tap = record.get("tap") or {}
        return designation("tapped", std=std, thread=tap.get("thread"),
                           thread_class=tap.get("class"), depth=depth)
    if family == "counterbore":
        bore = record.get("cbore") or {}
        return designation(
            "counterbore", std=std, d=d, depth=depth,
            cbore_d=None if bore.get("d") is None
            else in_designation_units(bore["d"], std),
            cbore_depth=None if bore.get("depth") is None
            else in_designation_units(bore["depth"], std))
    if family == "countersink":
        seat = record.get("csk") or {}
        return designation(
            "countersink", std=std, d=d, depth=depth,
            csk_d=None if seat.get("d") is None
            else in_designation_units(seat["d"], std),
            angle=seat.get("angle_deg"))
    # `drilled` and `clearance` are the same grammar; the difference between
    # them is provenance (a table row or a stated millimetre), not symbology.
    return designation("clearance", std=std, d=d, depth=depth)


def _finite(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value == value and value not in (float("inf"), float("-inf")))


def _point_problem(value, length: int, where: str) -> str | None:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return f"{where} must be a list of {length} numbers, got {value!r}"
    if not all(_finite(coord) for coord in value):
        return f"{where} must be finite numbers, got {value!r}"
    return None


def validate_record(record, where: str = "hole record") -> str | None:
    """The one structural + self-consistency check on a hole record.

    Returns the first problem as a sentence, or `None`. It answers *shape* and
    *internal agreement*, never geometry: whether the hole a record describes
    still exists is a question only a reader holding the built shape can ask,
    and both readers that hold one (the drawing pack, the harvest) ask it
    separately.

    **This is not an authentication boundary and must never be described as
    one.** A part script is trusted to run arbitrary code in the kernel
    process; a script that wants to fabricate a callout can simply drill the
    hole. What this closes is the *stale or inconsistent carrier*: a record
    whose designation does not match its own diameter, a `count` that does not
    match the positions it lists, a blind hole with no depth, a sidecar written
    for another cache key. Those are the shapes a hand-edited file, a partial
    write or a half-updated script produces, and each of them used to become a
    manufacturing callout with nothing said.
    """
    if not isinstance(record, dict):
        return (f"{where} is a {type(record).__name__}, not a dict — it was "
                f"not produced by a toolkit.holes helper")
    rid = record.get("id")
    if isinstance(rid, str) and rid:
        where = f"{where} ({rid!r})"
    missing = [key for key in RECORD_KEYS if key not in record]
    if missing:
        return (f"{where} is missing required key(s) {missing} — every record "
                f"must be one a toolkit.holes helper produced")
    for key, kinds in RECORD_KEYS.items():
        value = record[key]
        # bools are ints in Python and a count of True is residue.
        if bool not in kinds and isinstance(value, bool):
            return (f"{where}: key {key!r} must be "
                    f"{' or '.join(k.__name__ for k in kinds)}, got bool")
        if not isinstance(value, kinds):
            return (f"{where}: key {key!r} must be "
                    f"{' or '.join(k.__name__ for k in kinds)}, got "
                    f"{type(value).__name__}")
    if not record["id"] or not record["designation"].strip():
        return f"{where}: `id` and `designation` must be non-empty"
    if record["family"] not in RECORD_FAMILIES:
        return (f"{where}: family {record['family']!r} is not one of "
                f"{list(RECORD_FAMILIES)}")
    if record["standard"] not in STANDARDS:
        return (f"{where}: standard {record['standard']!r} is not one of "
                f"{list(STANDARDS)}")
    if not _finite(record["d"]) or record["d"] <= 0:
        return f"{where}: d must be a finite diameter > 0, got {record['d']!r}"
    wants_size, wants_fit = _NAMES_BY_FAMILY[record["family"]]
    for key, wanted in (("size", wants_size), ("fit", wants_fit)):
        present = record[key] is not None
        if present != wanted:
            return (f"{where}: a {record['family']} record must "
                    f"{'carry' if wanted else 'not carry'} a {key!r}, got "
                    f"{record[key]!r}")
    # THE NUMBER THAT GETS MANUFACTURED, TIED TO THE LABEL THAT SELECTS ITS
    # PROVENANCE. `size` and `fit` choose the published row every other check
    # re-derives from, while `designation_for_record` spells the callout from
    # `d` — so until this comparison existed the two were never connected.
    # Mutating `size` `#8` -> `#10` and the provenance with it, consistently,
    # left `d` at 4.9784 and the callout at the disputed `⌀0.196` while the
    # record claimed corroboration over agreeing publications with 0 conflicts,
    # and validated clean. It is one already-cached lookup.
    try:
        rows = rows_for_record(record)
    except (ValueError, KeyError, TypeError) as exc:
        return (f"{where}: its own fields do not identify a published row "
                f"({exc}), so the diameter it carries cannot be checked")
    if rows:
        bore = rows[0]
        expected_d = bore["tap_drill"] if record["family"] == "tapped" \
            else bore["d"]
        if abs(float(expected_d) - float(record["d"])) > 1e-9:
            return (f"{where}: d is {record['d']!r} but "
                    f"{record['size']}"
                    f"{'' if record['fit'] is None else ' ' + record['fit']}"
                    f" in the {record['standard'].upper()} table is "
                    f"{float(expected_d)!r}. The diameter that gets cut and "
                    f"the size that selects its provenance name one row or "
                    f"neither is checkable")
    count = record["count"]
    if count < 0:
        return f"{where}: count must be >= 0, got {count}"
    for name, length in (("positions", 2), ("centers", 3)):
        if len(record[name]) != count:
            return (f"{where}: count is {count} but {name} lists "
                    f"{len(record[name])} — a record's count is the number of "
                    f"instances it can point at, never a separate claim")
        for index, point in enumerate(record[name]):
            problem = _point_problem(point, length, f"{where}: {name}[{index}]")
            if problem is not None:
                return problem
    provenance = record.get("provenance")
    if record["family"] == "drilled":
        if provenance is not None:
            return (f"{where}: a drilled hole's diameter comes from no "
                    f"published table, so it may not carry provenance")
    elif not isinstance(provenance, dict):
        return (f"{where}: a {record['family']} record's diameter comes from a "
                f"table, so it must carry that row's `provenance`; got "
                f"{provenance!r}")
    else:
        for key, kinds in (("standard", list), ("sources", list),
                           ("conflicts", list)):
            value = provenance.get(key)
            if (not isinstance(value, kinds)
                    or not all(isinstance(text, str) for text in value)):
                return (f"{where}: provenance.{key} must be a list of "
                        f"strings, got {value!r}")
        if not isinstance(provenance.get("corroborated"), bool):
            return f"{where}: provenance.corroborated must be a bool"
        # RE-DERIVED AND COMPARED, exactly like the designation above. An
        # internal-consistency check cannot see a laundered claim: the genuine
        # disputed ANSI `#8 normal` record with its conflict deleted and
        # `corroborated` flipped to true is perfectly self-consistent, and it
        # validated clean until this comparison existed.
        try:
            expected = provenance_for_record(record)
        except (ValueError, KeyError, TypeError) as exc:
            return (f"{where}: its own fields do not identify a published row "
                    f"({exc}), so the provenance it carries cannot be checked")
        if expected != provenance:
            differ = [key for key in expected
                      if expected[key] != provenance.get(key)]
            return (f"{where}: provenance {differ} is not what this record's "
                    f"own size, fit, standard and fastener entitle it to "
                    f"(corroborated {provenance['corroborated']} over "
                    f"{len(provenance['sources'])} source(s) and "
                    f"{len(provenance['conflicts'])} conflict(s); the tables "
                    f"give {expected['corroborated']} over "
                    f"{len(expected['sources'])} and "
                    f"{len(expected['conflicts'])}). Provenance is derived "
                    f"from the record, never carried beside it")
    depth = record.get("depth_mm")
    if record["thru"]:
        if depth is not None:
            return (f"{where}: thru is true but depth_mm is {depth!r}; a "
                    f"through hole has no depth")
    elif not _finite(depth) or depth <= 0:
        return (f"{where}: thru is false but depth_mm is {depth!r}; a blind "
                f"hole of no stated depth is not geometry")
    for key, family in (("tap", "tapped"), ("cbore", "counterbore"),
                        ("csk", "countersink")):
        seat = record.get(key)
        if record["family"] == family and not isinstance(seat, dict):
            return (f"{where}: a {family} record must carry a {key!r} object, "
                    f"got {seat!r}")
        if record["family"] != family and seat is not None:
            return (f"{where}: {key!r} is set on a {record['family']} record, "
                    f"which no toolkit.holes helper produces")
    try:
        expected = designation_for_record(record)
    except (ValueError, KeyError, TypeError) as exc:
        return (f"{where}: its own fields do not spell a callout ({exc}) — "
                f"the record is not one a toolkit.holes helper produced")
    if expected != record["designation"]:
        return (f"{where}: designation {record['designation']!r} is not what "
                f"this record's own numbers spell ({expected!r}). A callout "
                f"is derived from the record, never carried beside it")
    try:
        json.dumps(record)
    except (TypeError, ValueError) as exc:
        return (f"{where} is not JSON-safe ({exc}) — a record holding a "
                f"build123d object cannot cross the kernel pipe; round "
                f"coordinates into plain floats")
    return None


# Every shipped table is proved well formed at import — see `validate_all`.
# Deliberately the last statement in the module: everything it calls is
# defined above it, and a data file this module cannot vouch for must not be
# discovered halfway through answering a question about a hole.
validate_all()
