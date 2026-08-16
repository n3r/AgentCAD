"""PRD-010 slice 2 — the vendored ISO hole-standards tables and their lookup.

Pure data: no geometry, no kernel call, no OCP. That is the point of the slice,
and it is what makes these numbers reviewable against the published sources
named in each JSON file's `sources` header without anyone reading OCCT.

**AC3** is `test_ac3_*`: `{"size": "M5", "family": "clearance"}` answers with
the three ISO 273 diameters, and they are the published ones.

The spot checks below are deliberately written as literals taken from the
sources rather than re-read from the JSON — a test that reads the same file the
code reads proves only that JSON parses.
"""

import json
from pathlib import Path

import pytest

from agentcad.core.tools import build_registry
from agentcad.toolkit import hole_standards as hs

from .conftest import make_test_service

DATA = Path(hs.__file__).resolve().parent / "data"


# ----------------------------------------------------------------- the files

#: **Every** shipped table. The structural invariants below used to name the
#: three ISO files by hand, which left the three ANSI tables with no structural
#: coverage at all — they were spot-checked row by row and never checked as a
#: *shape*. Parametrising over the whole set is what makes a seventh file
#: impossible to add without deciding which invariants it owes.
TABLES = ("iso_clearance", "iso_thread", "iso_cbore_csk",
          "ansi_clearance", "ansi_thread", "ansi_cbore_csk")

#: (file, the three fit keys the file's own standard spells them with)
CLEARANCE_TABLES = [("iso_clearance", ("fine", "medium", "coarse")),
                    ("ansi_clearance", ("close", "normal", "loose"))]
#: (file, the series names that standard's rows may carry)
THREAD_TABLES = [("iso_thread", ("coarse", "fine")),
                 ("ansi_thread", ("UNC", "UNF"))]
HEAD_TABLES = ("iso_cbore_csk", "ansi_cbore_csk")


def _doc(name: str) -> dict:
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def _nominal(size: str) -> float:
    """The nominal fastener diameter a size designation names, in the unit of
    the table it came from.

    `M5` is 5 mm and `1/4` is a quarter inch, both by reading the designation.
    `#6` is the ASME **number-screw series**, whose nominal diameter is
    `0.060 + 0.013 x N` inches — a formula, not a lookup — and having it is what
    lets a structural test ask the inch tables the same question it asks the
    metric ones ("does this hole clear its screw?"). Without it the ANSI half
    could only ever be spot-checked.
    """
    if size.startswith("M"):
        return float(size[1:])
    if size.startswith("#"):
        return 0.060 + 0.013 * int(size[1:])
    if "/" in size:
        numerator, denominator = size.split("/")
        return float(numerator) / float(denominator)
    return float(size)


@pytest.mark.parametrize("name,units", [
    ("iso_clearance", "mm"), ("iso_thread", "mm"), ("iso_cbore_csk", "mm"),
    ("ansi_clearance", "in"), ("ansi_thread", "in"), ("ansi_cbore_csk", "in"),
])
def test_every_data_file_carries_the_provenance_header(name, units):
    """Decision 5's header. The `sources` list is the union of everything the
    FILE was transcribed from and is **not** a claim about any row — which is
    why there is no "at least two" assertion here any more. That assertion,
    and the loader rule behind it, is what made the shipped guarantee ("two
    published sources must agree or the row does not ship") untrue: a row
    transcribed from one publication passed because its neighbours in the same
    file had two. The real rule is per row and lives below.
    """
    doc = _doc(name)
    assert doc["schema"] == 1
    assert doc["units"] == units
    assert doc["standard"] and doc["revision"]
    assert doc["row_shape"] in hs._ROW_SHAPES
    keys = [hs._source_key(text) for text in doc["sources"]]
    assert len(set(keys)) == len(keys) and doc["sources"]
    assert doc["rows"]


def _refuses(tmp_path, doc, match: str):
    """Load `doc` as a throwaway table and return the refusal it raises."""
    (tmp_path / "probe.json").write_text(json.dumps(doc), encoding="utf-8")
    original = hs.DATA_DIR
    try:
        hs.DATA_DIR = tmp_path
        with pytest.raises(ValueError, match=match) as excinfo:
            hs.table("probe")
    finally:
        hs.DATA_DIR = original
        hs.table.cache_clear()
    return str(excinfo.value)


@pytest.mark.parametrize("name", TABLES)
def test_every_cell_names_its_own_sources_with_no_default_at_any_depth(name):
    """**The provenance rule, enforced per CELL.**

    Every data cell — one fit of one clearance size, one pitch of one thread
    size, one size of one head table — is named in a provenance group or scope
    carrying the publications *it* was transcribed from. There is no `default`
    and no row-level fallback: both were the same mistake at different depths.
    `iso_cbore_csk.json` is the file that proves why it matters — four sources,
    of which two back the ISO 4762 column, one backs the whole ISO 10642 column
    alone, and one was consulted and deliberately not transcribed, so it backs
    no cell at all.
    """
    doc = _doc(name)                    # the raw file, before the loader
    block = doc["provenance"]
    assert "default" not in block, f"{name}: a file-wide default is not a claim"

    covered: dict[str, dict] = {}
    for group in block.get("groups") or []:
        for cell in group["cells"]:
            assert cell not in covered, f"{name}: {cell} claimed twice"
            covered[cell] = group
    # A `scopes` entry refines a cell a group already claims (that is how one
    # cell carries a conflict its neighbours do not), so it may overwrite.
    for scope, entry in (block.get("scopes") or {}).items():
        covered[scope] = entry
    for scope, entry in covered.items():
        assert entry["sources"], f"{name}/{scope}: backed by nothing"
        for index in entry["sources"]:
            assert 0 <= index < len(doc["sources"]), f"{name}/{scope}: {index}"
        assert entry.get("conflict") is None or entry["conflict"].strip()

    loaded = hs.table(name)             # `_cell_paths` walks by `row_shape`
    cells = {"/".join(path) for path in hs._cell_paths(loaded)}
    assert cells == set(covered), (
        f"{name}: uncovered {cells - set(covered)}; "
        f"backing nothing {set(covered) - cells}")


def test_a_row_added_without_declaring_its_sources_does_not_load(tmp_path):
    """The rule is only worth what the loader refuses.

    Copying a row out of one publication into a file that already cites two
    used to ship it as `corroborated: true` with no declaration anywhere. Now
    the file does not load, and the error names it.
    """
    doc = json.loads((DATA / "ansi_clearance.json").read_text(encoding="utf-8"))
    doc["rows"]["1-1/8"] = {"close": {"d": 1.16, "drill": "1-11/64"},
                            "normal": {"d": 1.22, "drill": "1-7/32"},
                            "loose": {"d": 1.28, "drill": "1-9/32"}}
    message = _refuses(tmp_path, doc, "declare no sources")
    assert "1-1/8" in message


def test_a_CELL_added_to_a_covered_row_does_not_load(tmp_path):
    """**Regression, and the hole the row-level rule left.**

    Proving coverage over row NAMES meant a new *cell* on an existing row
    inherited that row's citations. Measured: an `M12×1.5` pitch with a
    fabricated `tap_drill: 99.9` added to `iso_thread`'s group-covered `M12`
    row LOADED, and `thread("M12", pitch=1.5)` answered `tap_drill: 99.9`,
    `corroborated: True` over two named publications — while the control (a
    whole new row) was correctly refused the whole time, which is exactly what
    made the gap easy to miss. `size/pitch` tables are where the data
    legitimately grows a cell, so this was the likely real path.
    """
    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    doc["rows"]["M12"]["pitches"]["1.5"] = {"tap_drill": 99.9, "series": "fine"}
    message = _refuses(tmp_path, doc, "declare no sources")
    assert "M12/1.5" in message


def test_a_provenance_entry_that_backs_no_cell_does_not_load(tmp_path):
    """The other direction: a citation attached to a cell that is not there is
    a claim about nothing, and a typo would otherwise leave a real cell
    silently uncovered. A ROW-level key is refused for the same reason — it
    would be a fallback for cells added later."""
    doc = json.loads((DATA / "iso_clearance.json").read_text(encoding="utf-8"))
    doc["provenance"]["scopes"] = {"M99/fine": {"sources": [0]}}
    _refuses(tmp_path, doc, "names no data cell")

    doc = json.loads((DATA / "iso_clearance.json").read_text(encoding="utf-8"))
    doc["provenance"]["scopes"] = {"M5": {"sources": [0]}}   # a row, not a cell
    _refuses(tmp_path, doc, "names no data cell")


def test_one_citation_listed_twice_is_not_two_independent_sources(tmp_path):
    """**Regression.** Distinctness was exact-string, so the same publication
    pasted twice with a trailing space loaded as two sources and every cell it
    backed answered `corroborated: True` — the untrue claim the whole rule
    exists to stop, arriving through a copy-paste."""
    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    doc["sources"] = [doc["sources"][0], doc["sources"][0] + " "]
    _refuses(tmp_path, doc, "same citation once whitespace and case")

    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    doc["sources"] = [doc["sources"][0], doc["sources"][0].upper()]
    _refuses(tmp_path, doc, "same citation once whitespace and case")


def test_importing_the_module_proves_every_shipped_table():
    """`validate_all` runs at import, so a malformed vendored file is one error
    naming the file and the cell — not a kernel error found mid-build by
    whichever process happened to ask first. Six files, 0.58 ms.

    Asserted in a **fresh interpreter**, because the property is about import
    and this one's cache has been cleared by other tests. The honest limit,
    recorded rather than implied: this module is itself imported lazily by its
    callers, so "eager" means eager *within* the module, not at process start.
    """
    import subprocess
    import sys

    assert set(hs.SHIPPED_TABLES) == set(TABLES)
    proof = subprocess.run(
        [sys.executable, "-c",
         "from agentcad.toolkit import hole_standards as hs;"
         "print(hs.table.cache_info().currsize)"],
        capture_output=True, text=True, check=True)
    assert proof.stdout.strip() == str(len(TABLES)), proof.stderr


def test_corroborated_means_two_sources_that_agree_not_two_sources():
    """`ansi_clearance` documents its two sources DISAGREEING on `#8 normal`
    and shipped that cell as corroborated anyway — the provenance flag said the
    transcription had been checked when what happened is that a disagreement
    was adjudicated. Both facts now travel: the row ships, `conflicts` names
    the rejected value, and `corroborated` is false.
    """
    disputed = hs.clearance("#8", fit="normal", std="ansi")
    assert len(disputed["sources"]) == 2
    assert disputed["corroborated"] is False
    assert "0.190" in disputed["conflicts"][0]
    assert disputed["d_native"] == 0.196 and disputed["drill"] == "#9"

    # Its neighbours in the same row are corroborated: this is a cell, not a
    # row, and not a file.
    assert hs.clearance("#8", fit="close", std="ansi")["corroborated"] is True
    # A single-sourced row is false for the other reason, and says which.
    single = hs.csk("M5")
    assert (single["corroborated"], single["conflicts"]) == (False, [])


@pytest.mark.parametrize("name,fits", CLEARANCE_TABLES)
def test_every_clearance_row_is_complete_ordered_and_clears_its_screw(name, fits):
    doc = _doc(name)
    for size, row in doc["rows"].items():
        assert set(row) == set(fits), f"{name}/{size}"
        values = []
        for fit in fits:
            cell = row[fit]
            if isinstance(cell, dict):
                # The inch cell carries the drill DESIGNATION, which is part of
                # the callout and cannot be derived from the decimal.
                assert cell["drill"], f"{name}/{size}/{fit}"
                values.append(cell["d"])
            else:
                values.append(cell)
        assert values[0] < values[1] < values[2], f"{name}/{size}"
        assert values[0] > _nominal(size), \
            f"{name}/{size}: a clearance hole must clear"


@pytest.mark.parametrize("name,series", THREAD_TABLES)
def test_every_thread_row_carries_a_coarse_pitch_and_a_believable_drill(
        name, series):
    doc = _doc(name)
    std = "ansi" if name.startswith("ansi") else "iso"
    to_mm = hs._unit_factor(doc["units"])
    for size, row in doc["rows"].items():
        assert hs._pitch_key(row["coarse_pitch"], std) in row["pitches"], size
        nominal = _nominal(size) * to_mm
        for key, entry in row["pitches"].items():
            assert entry["series"] in series, f"{name}/{size}"
            # A Unified key is a COUNT of threads per inch, an ISO key is a
            # length; both become a millimetre pitch before they are compared.
            pitch = hs.MM_PER_INCH / float(key) if std == "ansi" else float(key)
            drill = float(entry["tap_drill"]) * to_mm
            # A tap drill lies between the minor and the major diameter; d - P
            # is the classic ~100%-engagement figure and the published drill is
            # never far below it (it would not cut) nor above the nominal.
            assert nominal - pitch - 0.35 <= drill < nominal, \
                f"{name}/{size}/{key}"
            if std == "ansi":
                assert entry["drill"], f"{name}/{size}/{key}"


@pytest.mark.parametrize("name", HEAD_TABLES)
def test_every_head_row_is_monotone_and_larger_than_its_fastener(name):
    doc = _doc(name)
    for family, fasteners in doc["rows"].items():
        for fastener, rows in fasteners.items():
            heads = [row["head_d"] for row in rows.values()]
            assert heads == sorted(heads), f"{name}/{family}/{fastener}"
            for size, row in rows.items():
                where = f"{name}/{family}/{fastener}/{size}"
                assert row["head_d"] > _nominal(size), where
                if family == "cbore":
                    assert 0 < row["head_h"] < row["head_d"], where
                else:
                    assert row["angle_deg"] in (82.0, 90.0), where


# ------------------------------------------------------------------ lookups

def test_ac3_clearance_for_m5_returns_the_three_published_iso_273_diameters():
    """**AC3** — the tool's own example, at the published values.

    ISO 273:1979 M5: fine (H12) 5.3, medium (H13) 5.5, coarse (H14) 5.8.
    """
    answer = hs.lookup(family="clearance", size="M5")
    assert answer["std"] == "iso"
    assert answer["fits"] == {"fine": 5.3, "medium": 5.5, "coarse": 5.8}
    assert answer["standard"].startswith("ISO 273")
    # Two sources because THIS row has two, not because the file lists two.
    assert len(answer["sources"]) == 2 and answer["corroborated"] is True
    assert answer["conflicts"] == []


@pytest.mark.parametrize("size,fine,medium,coarse", [
    ("M3", 3.2, 3.4, 3.6),
    ("M6", 6.4, 6.6, 7.0),
    ("M8", 8.4, 9.0, 10.0),
    ("M12", 13.0, 13.5, 14.5),
    ("M20", 21.0, 22.0, 24.0),
])
def test_clearance_spot_checks_against_the_published_table(size, fine, medium, coarse):
    assert hs.clearance(size, fit="fine")["d"] == fine
    assert hs.clearance(size, fit="medium")["d"] == medium
    assert hs.clearance(size, fit="coarse")["d"] == coarse


def test_clearance_accepts_both_fit_spellings_and_canonicalizes_per_standard():
    """ISO 273 says fine/medium/coarse; ASME B18.2.8 (and the PRD) says
    close/normal/loose. An agent will type either, so both are accepted and the
    ANSWER carries the spelling of the requested standard."""
    assert hs.clearance("M5", fit="close")["d"] == 5.5 - 0.2
    assert hs.clearance("M5", fit="close")["fit"] == "fine"
    assert hs.clearance("M5", fit="normal")["fit"] == "medium"
    assert hs.clearance("M5", fit="loose")["fit"] == "coarse"
    # The same lookup labelled for ASME reports the ASME spelling. (ANSI hole
    # TABLES land in slice 8; the naming convention does not need them.)
    assert hs.canonical_fit("fine", "ansi") == "close"
    assert hs.canonical_fit("coarse", "ansi") == "loose"
    assert hs.canonical_fit("normal", "iso") == "medium"


@pytest.mark.parametrize("size,pitch,drill", [
    ("M3", 0.5, 2.5),
    ("M5", 0.8, 4.2),
    ("M8", 1.25, 6.8),
    ("M10", 1.5, 8.5),
    ("M12", 1.75, 10.2),
])
def test_tap_drill_spot_checks_against_the_published_table(size, pitch, drill):
    row = hs.thread(size)
    assert row["pitch"] == pitch
    assert row["tap_drill"] == drill
    assert row["series"] == "coarse"


def test_thread_accepts_an_explicit_fine_pitch():
    fine = hs.thread("M8", pitch=1.0)
    assert (fine["pitch"], fine["tap_drill"], fine["series"]) == (1.0, 7.0, "fine")
    assert hs.thread("M8")["tap_drill"] == 6.8   # default is still coarse


def test_a_tapped_lookup_lists_every_tabulated_pitch_coarsest_first():
    answer = hs.lookup(family="tapped", size="M8")
    assert [entry["pitch"] for entry in answer["pitches"]] == [1.25, 1.0]
    assert answer["pitches"][0]["series"] == "coarse"


def test_counterbore_spot_check_reports_the_published_head_and_a_named_rule():
    """The published fact is the ISO 4762 head (M5: dk 8.5, k 5.0). The bore is
    this repo's documented clearance rule, and the answer says which is which —
    because the published counterbore charts disagree with each other."""
    row = hs.cbore("M5")
    assert (row["head_d"], row["head_h"]) == (8.5, 5.0)
    assert row["d"] == pytest.approx(8.5 + hs.CBORE_DIA_CLEARANCE)
    assert row["depth"] == pytest.approx(5.0 + hs.CBORE_DEPTH_CLEARANCE)
    assert "not a standard" in row["rule"].lower()
    assert row["fastener"] == "iso4762"


def test_countersink_spot_check_uses_the_theoretical_sharp_head_diameter():
    row = hs.csk("M5")
    assert row["head_d"] == 11.2          # ISO 10642 theoretical sharp dk
    assert row["d"] == 11.2               # no clearance added; see the JSON note
    assert row["angle_deg"] == 90.0


def test_countersink_angle_default_is_per_standard_not_per_build123d():
    """build123d's `CounterSinkHole` defaults to 82 deg, an ASME default that
    would otherwise arrive inside an ISO-labelled call. The default lives here
    so the geometry slice cannot inherit the wrong one."""
    assert hs.default_csk_angle("iso") == 90.0
    assert hs.default_csk_angle("ansi") == 82.0
    assert hs.csk("M5", std="iso")["angle_deg"] == 90.0
    assert hs.csk("M5", angle=100.0)["angle_deg"] == 100.0


# ------------------------------------------------------- ANSI (slice 8)

@pytest.mark.parametrize("size,close,normal,loose", [
    ("#6", 0.154, 0.170, 0.185),
    ("#10", 0.206, 0.221, 0.238),
    ("1/4", 0.266, 0.281, 0.297),
    ("3/8", 0.391, 0.406, 0.422),
    ("1/2", 0.531, 0.562, 0.609),
])
def test_ansi_clearance_spot_checks_against_the_published_table(
        size, close, normal, loose):
    """ASME B18.2.8's own numbers, as literals from the two named sources.

    Note these are NOT the traditional Machinery's-Handbook 'close/free fit'
    inch chart, which gives a #10 screw 0.196/0.201 — a different published
    convention, not a rounding of this one. The file names the standard it
    transcribes; see its `notes`.
    """
    for fit, expected in (("close", close), ("normal", normal),
                          ("loose", loose)):
        row = hs.clearance(size, fit=fit, std="ansi")
        assert row["d_native"] == expected
        assert row["d"] == pytest.approx(expected * 25.4)
        assert row["designation"] == f"⌀{expected:g}"


def test_ansi_clearance_carries_the_drill_designation():
    """A number/letter/fraction drill is part of the callout an operator reads
    and cannot be derived from the decimal — 0.221 in is drill #2, and no
    amount of arithmetic gets you from one to the other."""
    assert hs.clearance("#10", fit="normal", std="ansi")["drill"] == "#2"
    assert hs.clearance("1/4", fit="close", std="ansi")["drill"] == "17/64"
    assert hs.clearance("#10", fit="loose", std="ansi")["drill"] == "B"
    assert hs.clearance("M5")["drill"] is None      # ISO has no drill column


def test_ansi_fit_spellings_answer_in_the_asme_names():
    assert hs.clearance("1/4", fit="medium", std="ansi")["fit"] == "normal"
    assert hs.clearance("1/4", fit="fine", std="ansi")["fit"] == "close"
    # `clearance_fits` is millimetres like every other geometric length here;
    # the table's own inches are `clearance_fits_native`.
    assert hs.clearance_fits("1/4", std="ansi") == pytest.approx(
        {"close": 0.266 * 25.4, "normal": 0.281 * 25.4, "loose": 0.297 * 25.4})
    assert hs.clearance_fits_native("1/4", std="ansi") == {
        "close": 0.266, "normal": 0.281, "loose": 0.297}


@pytest.mark.parametrize("size,tpi,drill,decimal,series", [
    ("#6", 32, "#36", 0.1065, "UNC"),
    ("#10", 24, "#25", 0.1495, "UNC"),
    ("1/4", 20, "#7", 0.2010, "UNC"),
    ("5/16", 18, "F", 0.2570, "UNC"),
    ("3/8", 16, "5/16", 0.3125, "UNC"),
    ("1/2", 13, "27/64", 0.4219, "UNC"),
])
def test_ansi_tap_drill_spot_checks_against_the_published_table(
        size, tpi, drill, decimal, series):
    row = hs.thread(size, std="ansi")
    assert (row["tpi"], row["drill"], row["series"]) == (tpi, drill, series)
    assert row["tap_drill_native"] == decimal
    assert row["tap_drill"] == pytest.approx(decimal * 25.4)
    # tpi is a COUNT; the millimetre pitch is derived from it, and both are
    # reported so neither can be mistaken for the other.
    assert row["pitch"] == pytest.approx(25.4 / tpi)


def test_a_unified_thread_designation_is_not_a_metric_one():
    assert hs.thread("1/4", std="ansi")["designation"] == "1/4-20 UNC - 2B"
    assert hs.thread("1/4", pitch=28, std="ansi")["designation"] == \
        "1/4-28 UNF - 2B"
    # depth crosses the boundary in millimetres and prints in inches
    assert hs.thread("1/4", depth=12.7, std="ansi")["designation"] == \
        "1/4-20 UNC - 2B ↧0.5"
    assert hs.default_thread_class("ansi") == "2B"
    assert hs.default_thread_class("iso") == "6H"


def test_a_unified_pitch_is_a_whole_count_of_threads():
    with pytest.raises(ValueError, match=r"pitch"):
        hs.thread("1/4", pitch=1.25, std="ansi")     # a millimetre pitch
    with pytest.raises(ValueError, match=r"pitch"):
        hs.thread("1/4", pitch=19, std="ansi")       # not tabulated


def test_ansi_cbore_and_csk_report_the_published_head_geometry():
    """ASME B18.3 head geometry, spot-checked as literals. (`H max = d` holds
    across the whole socket-head table; `A max = 1.5 d` holds on the fractional
    sizes only — see `test_the_asme_b18_3_head_ratio_*`.) The bore is still the
    named shop rule — in the inch shop's round numbers, 1/16 on diameter and
    1/32 on depth, which puts a 1/4 in head in a 7/16 counterbore 9/32 deep."""
    bore = hs.cbore("1/4", std="ansi")
    assert (bore["head_d_native"], bore["head_h_native"]) == (0.375, 0.250)
    assert bore["head_d"] == pytest.approx(0.375 * 25.4)
    assert bore["d_native"] == pytest.approx(0.4375)      # 7/16
    assert bore["depth_native"] == pytest.approx(0.28125)  # 9/32
    assert bore["fastener"] == "asme_b18_3"
    assert "not a standard" in bore["rule"].lower()

    sink = hs.csk("1/4", std="ansi")
    assert sink["d_native"] == 0.531        # max theoretical sharp
    assert sink["angle_deg"] == 82.0        # ASME's angle, not ISO's 90
    assert hs.csk("M5")["angle_deg"] == 90.0


def test_ansi_sizes_come_back_in_table_order_not_sorted():
    """`#8 < #10 < 1/4` is an ordering only the table knows: `float(size[1:])`
    reads `1/4` as 1.0 and cannot read `B` at all."""
    sizes = hs.sizes("clearance", std="ansi")
    assert sizes[:4] == ["#0", "#1", "#2", "#3"]
    assert sizes.index("#8") < sizes.index("#10") < sizes.index("1/4")
    assert hs.sizes("countersink", std="ansi")[0] == "#4"


def test_the_ansi_lookup_answers_the_tool_the_same_shape_as_iso():
    answer = hs.lookup(family="clearance", size="1/4", std="ansi")
    assert answer["fits"] == pytest.approx(
        {"close": 0.266 * 25.4, "normal": 0.281 * 25.4, "loose": 0.297 * 25.4})
    assert answer["fits_native"] == {"close": 0.266, "normal": 0.281,
                                     "loose": 0.297}
    assert answer["designations"]["normal"] == "⌀0.281"
    assert answer["units"] == "in"
    tapped = hs.lookup(family="tapped", size="1/4", std="ansi")
    assert [entry["tpi"] for entry in tapped["pitches"]] == [20.0, 28.0]
    index = hs.lookup(std="ansi")
    assert index["fits"] == {"fine": "close", "medium": "normal",
                             "coarse": "loose"}
    assert index["csk_angle_deg"] == 82.0


# ---------------------------------------------- units, provenance, the rule
#
# Six review findings, every one of them in the MACHINERY around the data
# rather than in a number: the data was re-checked row by row against the
# published standards and no transcription error was found.

def test_an_ansi_clearance_lookup_answers_in_millimetres_under_the_bare_key():
    """The module docstring and the tool description both say every length a
    lookup returns is millimetres. `lookup` returned the ASME table's INCHES
    under `fits` — 0.281 where the very same row's `clearance()["d"]` is
    7.1374 mm — so the tool's headline answer was out by 25.4x under the key
    its own documentation defines as millimetres. The native inches are still
    there, under a key that says which unit it is.
    """
    answer = hs.lookup(family="clearance", size="1/4", std="ansi")
    assert answer["fits"]["normal"] == pytest.approx(0.281 * 25.4)
    assert answer["fits"]["normal"] == \
        hs.clearance("1/4", fit="normal", std="ansi")["d"]
    assert answer["fits_native"] == {"close": 0.266, "normal": 0.281,
                                     "loose": 0.297}
    # A designation is text for a drawing and still prints the standard's unit.
    assert answer["designations"]["normal"] == "⌀0.281"
    # ISO is millimetres either way, and the answer's shape does not change.
    iso = hs.lookup(family="clearance", size="M5")
    assert iso["fits"] == iso["fits_native"] == {"fine": 5.3, "medium": 5.5,
                                                 "coarse": 5.8}


def test_a_tapped_lookup_reports_every_pitch_row_in_millimetres_too():
    """The same 25.4x error, one level down. `lookup(family="tapped")` spliced
    the table entry in raw, so the DOCUMENTED way to pick UNF over UNC —
    reading `pitches[...]["tap_drill"]` — answered 0.213 *inches* under the
    same key whose top level answers 5.1054 *millimetres*. One key, one row,
    two units."""
    answer = hs.lookup(family="tapped", size="1/4", std="ansi")
    unf = next(e for e in answer["pitches"] if e["tpi"] == 28.0)
    assert unf["tap_drill"] == pytest.approx(0.213 * 25.4)
    assert unf["tap_drill_native"] == 0.213
    assert unf["pitch"] == pytest.approx(25.4 / 28)
    assert unf["drill"] == "#3"
    unc = next(e for e in answer["pitches"] if e["tpi"] == 20.0)
    assert unc["tap_drill"] == answer["tap_drill"]   # the top level IS the UNC row
    iso = hs.lookup(family="tapped", size="M8")
    assert [e["pitch"] for e in iso["pitches"]] == [1.25, 1.0]
    assert [e["tap_drill"] for e in iso["pitches"]] == [6.8, 7.0]
    assert iso["pitches"][0]["tpi"] is None      # a metric row has no count


def test_provenance_names_the_sources_that_back_this_row_not_the_whole_file():
    """`_provenance` stapled the file's entire `sources` list onto every row.

    `iso_cbore_csk.json` names four: two for the ISO 4762 socket head, one for
    the ISO 10642 countersunk head, and one **consulted for the counterbore
    convention and deliberately not transcribed**. A countersink answer that
    claimed all four claimed corroboration it does not have — and named a
    source that backs no row in the file at all.
    """
    doc = _doc("iso_cbore_csk")
    assert len(doc["sources"]) == 4
    bore, sink = hs.cbore("M5"), hs.csk("M5")
    assert len(bore["sources"]) == 2 and bore["corroborated"] is True
    assert all("4762" in s or "912" in s for s in bore["sources"])
    assert bore["standard"].startswith("ISO 4762")
    # Nine ISO 10642 rows, one named source. Said out loud, not averaged away.
    assert len(sink["sources"]) == 1 and sink["corroborated"] is False
    assert "10642" in sink["sources"][0]
    assert sink["standard"].startswith("ISO 10642")
    # The consulted-not-transcribed source now backs nothing.
    assert not any("engineersbible" in s
                   for s in bore["sources"] + sink["sources"])


def test_the_recorded_resolved_conflict_travels_on_the_row_that_carries_it():
    """`ansi_clearance.json`'s **#8 normal** cell is the one place slice 8
    resolved a source disagreement instead of dropping the row. That fact
    belongs to that cell, so it rides on it — and on nothing else."""
    row = hs.clearance("#8", fit="normal", std="ansi")
    assert row["conflicts"] and "0.190" in row["conflicts"][0]
    assert hs.clearance("#8", fit="close", std="ansi")["conflicts"] == []
    assert hs.clearance("#10", fit="normal", std="ansi")["conflicts"] == []
    # A whole-row lookup unions its three cells, so it still surfaces it.
    assert hs.lookup("clearance", "#8", std="ansi")["conflicts"]
    assert hs.lookup("clearance", "#10", std="ansi")["conflicts"] == []
    # #4 and #10 carry a third source that reproduced them as a spot check.
    assert len(hs.lookup("clearance", "#10", std="ansi")["sources"]) == 3
    assert len(hs.lookup("clearance", "#6", std="ansi")["sources"]) == 2


def test_an_unrecognised_unit_string_is_an_error_not_a_pass_through():
    """`_mm` converted iff the units string was exactly `"in"`, and `table()`
    only checked that the field was truthy. A file that spelled it `"inch"`
    would therefore have shipped inches under the millimetre contract with no
    symptom at all. The unit handling is total: every legitimate spelling
    converts, and anything else raises naming the field."""
    assert hs._mm(1.0, {"units": "mm"}) == 1.0
    assert hs._mm(1.0, {"units": "in"}) == pytest.approx(25.4)
    assert hs._mm(1.0, {"units": "inch"}) == pytest.approx(25.4)
    assert hs._mm(1.0, {"units": "Inches"}) == pytest.approx(25.4)
    for bad in ("furlong", "", "mils", None, 25.4):
        with pytest.raises(ValueError, match=r"units"):
            hs._mm(1.0, {"units": bad})


def test_a_table_whose_unit_string_is_unknown_is_refused_at_load(
        tmp_path, monkeypatch):
    doc = _doc("iso_clearance") | {"units": "inch (ish)"}
    (tmp_path / "unit_probe.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(hs, "DATA_DIR", tmp_path)
    hs.table.cache_clear()
    try:
        with pytest.raises(ValueError, match=r"units"):
            hs.table("unit_probe")
    finally:
        hs.table.cache_clear()


def test_the_flat_counterbore_clearance_is_guarded_below_the_head_it_was_set_on():
    """The +1.5 mm rule was chosen to sit on or above every published chart
    **from M5 up**, and was then applied to every size. On an M2 head that flat
    number is a third of the whole hole: `cbore("M2")` bored **5.3** where
    DIN 974-1 gives 4.3. Below the head the rule was set on, the same clearance
    is applied as the PROPORTION it has there, so the two agree exactly at the
    threshold and the small sizes still err large, never small."""
    assert hs.cbore("M5")["d"] == pytest.approx(8.5 + 1.5)          # unchanged
    assert hs.cbore("M6")["d"] == pytest.approx(10.0 + 1.5)         # unchanged
    small = hs.cbore("M2")
    assert small["d"] == pytest.approx(3.8 * (1 + 1.5 / 8.5))
    assert 4.3 < small["d"] < 4.5, "DIN 974-1 gives 4.3; the flat rule gave 5.3"
    assert hs.cbore("M3")["d"] > 5.5 + 0.5   # still clears the M3 head, largely
    # The inch table has the same defect in the same place, and the same guard.
    assert hs.cbore("1/4", std="ansi")["d_native"] == pytest.approx(0.4375)
    assert hs.cbore("#0", std="ansi")["d_native"] == pytest.approx(
        0.096 * (1 + 0.0625 / 0.375))
    assert "proportion" in hs.CBORE_RULE.lower()


def test_the_iso_10642_head_ratio_is_claimed_only_where_it_holds():
    """The file called `dk = 2.24 x d` "its own internal check" for the whole
    ISO 10642 column. It is not one: M16 and M20 are below it (33.6 against
    35.84, 40.32 against 44.8). **The data is right and the rule was the false
    part**, so the claim is restricted to the range where it holds and the two
    rows outside it are named as transcribed, not derived."""
    rows = _doc("iso_cbore_csk")["rows"]["csk"]["iso10642"]
    for size in ("M3", "M4", "M5", "M6", "M8", "M10", "M12"):
        assert rows[size]["head_d"] == pytest.approx(2.24 * float(size[1:]))
    for size in ("M16", "M20"):
        assert rows[size]["head_d"] < 2.24 * float(size[1:])
    notes = " ".join(_doc("iso_cbore_csk")["notes"])
    assert "M16" in notes and "M20" in notes


def test_the_asme_b18_3_head_ratio_is_claimed_only_on_the_fractional_sizes():
    """Same shape of false claim on the inch side. `H max = d` really does hold
    across the whole socket-head table; `A max = 1.5 d` holds on the fractional
    sizes only — all nine numbered sizes are ABOVE it (#0 is 0.096 against
    0.090, #10 is 0.312 against 0.285)."""
    rows = _doc("ansi_cbore_csk")["rows"]["cbore"]["asme_b18_3"]
    for size, row in rows.items():
        nominal = _nominal(size)
        assert row["head_h"] == pytest.approx(nominal), size
        if size.startswith("#"):
            assert row["head_d"] > 1.5 * nominal, size
        else:
            assert row["head_d"] == pytest.approx(1.5 * nominal, abs=0.001), size
    notes = " ".join(_doc("ansi_cbore_csk")["notes"]).lower()
    assert "numbered" in notes


# ------------------------------------------------------------- designations

def test_designations_for_all_four_families_in_both_symbologies():
    """Decision 5's grammar table. The glyphs are shared; what differs per
    standard is the numeric formatting and the thread designation itself."""
    assert hs.designation("clearance", d=5.5) == "⌀5.5"
    assert hs.designation("clearance", d=0.217, std="ansi") == "⌀0.217"

    assert hs.designation("tapped", thread="M5×0.8", thread_class="6H",
                          depth=12.0) == "M5×0.8 - 6H ↧12"
    assert hs.designation("tapped", thread="10-24 UNC", thread_class="2B",
                          depth=0.5, std="ansi") == "10-24 UNC - 2B ↧0.5"

    assert hs.designation("counterbore", d=5.5, cbore_d=9.5,
                          cbore_depth=5.4) == "⌀5.5 ⌴⌀9.5↧5.4"
    assert hs.designation("counterbore", d=0.217, cbore_d=0.375,
                          cbore_depth=0.213, std="ansi") == "⌀0.217 ⌴⌀0.375↧0.213"

    assert hs.designation("countersink", d=5.5, csk_d=10.4,
                          angle=90.0) == "⌀5.5 ⌵⌀10.4×90°"
    assert hs.designation("countersink", d=0.217, csk_d=0.41, angle=82.0,
                          std="ansi") == "⌀0.217 ⌵⌀0.41×82°"


def test_a_thru_hole_designation_omits_the_depth_glyph():
    assert hs.designation("tapped", thread="M5×0.8", thread_class="6H") == \
        "M5×0.8 - 6H"
    assert hs.clearance("M5")["designation"] == "⌀5.5"
    assert hs.thread("M5", depth=12.0)["designation"] == "M5×0.8 - 6H ↧12"


# ------------------------------------------------------------------- errors

def test_unknown_size_raises_value_error_naming_the_argument():
    with pytest.raises(ValueError, match=r"size"):
        hs.clearance("M9")
    with pytest.raises(ValueError, match=r"size"):
        hs.thread("M9")
    with pytest.raises(ValueError, match=r"size"):
        hs.cbore("M9")


def test_unknown_fit_raises_value_error_naming_the_argument():
    with pytest.raises(ValueError, match=r"fit"):
        hs.clearance("M5", fit="snug")


def test_unknown_standard_raises_value_error_naming_the_argument():
    with pytest.raises(ValueError, match=r"std"):
        hs.clearance("M5", std="jis")
    with pytest.raises(ValueError, match=r"std"):
        hs.default_csk_angle("jis")


def test_a_metric_size_is_not_in_the_ansi_table_and_says_so():
    """The two standards' size vocabularies do not overlap, and asking one for
    the other's designation is a `size` error naming what IS tabulated — never
    a silent fallback to the other standard's numbers under this label."""
    with pytest.raises(ValueError, match=r"size 'M5' is not in the ANSI"):
        hs.clearance("M5", std="ansi")
    with pytest.raises(ValueError, match=r"size '1/4' is not in the ISO"):
        hs.clearance("1/4", std="iso")


def test_unknown_pitch_and_unknown_fastener_name_their_argument():
    with pytest.raises(ValueError, match=r"pitch"):
        hs.thread("M5", pitch=0.9)
    with pytest.raises(ValueError, match=r"fastener"):
        hs.cbore("M5", fastener="iso7380")
    with pytest.raises(ValueError, match=r"family"):
        hs.lookup(family="chamfer")


# --------------------------------------------------------------- the tool

def test_the_tool_is_registered_unconditionally_and_answers_ac3(tmp_path):
    service = make_test_service(tmp_path / "projects", kernel=None)
    registry = build_registry(service)
    assert registry.get("hole_standards") is not None
    answer = registry.call("hole_standards", {"size": "M5", "family": "clearance"})
    assert "error" not in answer, answer
    assert answer["fits"] == {"fine": 5.3, "medium": 5.5, "coarse": 5.8}


def test_the_tool_lists_the_families_and_sizes_when_asked_for_nothing(tmp_path):
    service = make_test_service(tmp_path / "projects", kernel=None)
    registry = build_registry(service)
    answer = registry.call("hole_standards", {})
    assert set(answer["families"]) == {"clearance", "tapped", "counterbore",
                                       "countersink"}
    assert "M5" in answer["sizes"]["clearance"]


def test_the_tool_reports_a_bad_argument_as_a_validation_error(tmp_path):
    service = make_test_service(tmp_path / "projects", kernel=None)
    registry = build_registry(service)
    answer = registry.call("hole_standards", {"size": "M9", "family": "clearance"})
    assert answer["error"]["type"] == "validation_error"
    assert "size" in answer["error"]["message"]


def test_the_seam_survives_a_second_build_registry(tmp_path):
    """`tools_holes` loads at `h`, before every pack whose seams it will later
    touch. Slice 2 touches none — assert that, so a later slice adding one has
    to notice it is changing the contract."""
    service = make_test_service(tmp_path / "projects", kernel=None)
    build_registry(service)
    second = build_registry(service)
    assert second.get("hole_standards") is not None


def test_two_cells_that_spell_one_provenance_path_do_not_load(tmp_path):
    """The `/`-joined path is the provenance key, and the inch tables make a
    collision reachable: a size `1` with a pitch `4` and a size `1/4` both
    spell `1/4`, so one entry would answer for two cells and the coverage proof
    would pass with one of them undeclared.

    This branch carried a `# pragma: no cover` and nothing exercised it, which
    is not a state a refusal should ship in.
    """
    doc = json.loads((DATA / "ansi_clearance.json").read_text(encoding="utf-8"))
    # row "1/4" cell "close" and row "1" cell "4/close" both spell 1/4/close
    doc["rows"]["1"]["4/close"] = {"d": 0.281, "drill": "9/32"}
    message = _refuses(tmp_path, doc, "share one '/'-joined provenance path")
    assert "1/4/close" in message


def test_a_citation_repeated_with_an_invisible_difference_is_refused(tmp_path):
    """Zero-width characters and an http/https swap are two more ways to paste
    one publication twice and have the file claim two behind one number."""
    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    first = doc["sources"][0]
    doc["sources"] = [first, first[:20] + "​" + first[20:]]
    _refuses(tmp_path, doc, "same citation once whitespace and case")

    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    doc["sources"] = [first, first.replace("https://", "http://")]
    _refuses(tmp_path, doc, "same citation once whitespace and case")


def test_every_scalar_on_a_thread_row_must_be_declared_not_just_named_ones(
        tmp_path):
    """**Regression.** `_cell_paths` listed `(*row["pitches"], "coarse_pitch")`
    — an allowlist, not a complement — so any *other* scalar added to a thread
    row was undeclared and loaded clean: measured, `M8/preferred_pitch`. It is
    now every key that is not the `pitches` container, so a scalar this module
    has never heard of still has to declare where it came from.
    """
    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    doc["rows"]["M8"]["preferred_pitch"] = 1.0
    message = _refuses(tmp_path, doc, "declare no sources")
    assert "M8/preferred_pitch" in message


def test_a_field_name_no_cell_of_this_shape_may_carry_does_not_load(tmp_path):
    """A closed schema over the field names each `row_shape` actually uses.

    It refuses a fabricated `fabricated_mm`, and — the reason it is worth
    having beyond provenance — it turns a typo like `head_dd` into a load error
    naming the file instead of a `KeyError` raised from inside `cbore()`
    halfway through an answer.
    """
    for field in ("fabricated_mm", "head_dd"):
        doc = json.loads(
            (DATA / "iso_cbore_csk.json").read_text(encoding="utf-8"))
        doc["rows"]["cbore"]["iso4762"]["M3"][field] = 99.9
        message = _refuses(tmp_path, doc, "which no group/fastener/size cell")
        assert field in message and "typo" in message

    doc = json.loads((DATA / "iso_cbore_csk.json").read_text(encoding="utf-8"))
    del doc["rows"]["cbore"]["iso4762"]["M3"]["head_d"]
    _refuses(tmp_path, doc, "missing required field")


def test_an_optional_in_cell_field_is_addable_and_the_justification_says_so(
        tmp_path):
    """**The clause this replaces was checkably false.** `_cell_paths` used to
    justify its stopping point with "a redesign whose only gain is refusing a
    fabricated field that no lookup reads" — but two lookups read an *optional*
    in-cell field with a default (`cell.get("drill")`, `entry.get("drill")`),
    and ISO thread pitch cells carry no `drill`, so one is addable. Measured:
    `drill: "FAKE-99"` inside the declared `M8/1.25` cell loaded and
    `thread("M8")` served it with `corroborated: True`.

    The behaviour is kept — `drill` is a legitimate field of a pitch cell, and
    refusing it would be a different over-claim — and the justification is now
    the true one: a value added to an optional field is exactly as uncatchable
    as a value edited in a required one. This test pins both halves, so the
    sentence cannot drift back.
    """
    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    assert "drill" not in doc["rows"]["M8"]["pitches"]["1.25"]
    doc["rows"]["M8"]["pitches"]["1.25"]["drill"] = "FAKE-99"
    (tmp_path / "probe.json").write_text(json.dumps(doc), encoding="utf-8")
    original, original_table = hs.DATA_DIR, hs._TABLES[("iso", "tapped")]
    try:
        hs.DATA_DIR = tmp_path
        hs.table.cache_clear()
        hs.table("probe")                       # loads: `drill` is legitimate
        hs._TABLES[("iso", "tapped")] = "probe"
        assert hs.thread("M8")["drill"] == "FAKE-99"
    finally:
        hs._TABLES[("iso", "tapped")] = original_table
        hs.DATA_DIR = original
        hs.table.cache_clear()

    # …and the justification in the source says that, not the false clause.
    source = (Path(hs.__file__)).read_text(encoding="utf-8")
    assert "only gain is refusing a fabricated" not in source
    assert "as uncatchable as a value edited in a required one" in source


def test_a_cell_is_the_unit_and_its_fields_are_not_declared_separately():
    """**The stopping point, asserted so it is a decision and not a gap.**

    A cell is the path `row_shape` names — one fit, one pitch or other scalar,
    one head size — and the fields *inside* it (`{d, drill}`,
    `{head_d, head_h}`) are covered by that cell's citation. So 248
    declarations stand against 442 scalar leaves, and a fabricated field added
    inside a declared cell loads. That is deliberate: a cell is what one line
    of the published table prints, `_prov_scope` resolves at exactly this
    depth, and an added field no lookup reads is inert. The refusal message
    says it in as many words, which is the part that was missing.
    """
    declared = sum(len(hs._cell_paths(hs.table(name)))
                   for name in hs.SHIPPED_TABLES)
    assert declared == 248

    def leaves(node):
        if isinstance(node, dict):
            return sum(leaves(value) for value in node.values())
        return 1

    scalars = sum(leaves(hs.table(name)["rows"]) for name in hs.SHIPPED_TABLES)
    assert scalars == 442                       # the gap, named not hidden

    for name in hs.SHIPPED_TABLES:
        try:
            hs.table(name)
        except ValueError as exc:               # pragma: no cover
            raise AssertionError(name) from exc
    # and the refusal explains the granularity rather than claiming more
    doc = json.loads((DATA / "iso_clearance.json").read_text(encoding="utf-8"))
    doc["rows"]["M5"]["extra"] = 1.0
    import tempfile
    import pathlib as _pathlib
    tmp = _pathlib.Path(tempfile.mkdtemp())
    message = _refuses(tmp, doc, "declare no sources")
    assert "the fields INSIDE a cell are covered by its citation" in message


def test_coarse_pitch_is_inside_the_coverage_set(tmp_path):
    """**Promoted from "record it" to "fix it".** `coarse_pitch` names which
    tabulated pitch the standard calls first choice, and `thread(size)` answers
    from it — flipping `iso_thread`'s M8 from 1.25 to 1.0 makes `thread("M8")`
    answer tap drill 7.0 instead of 6.8. It sat outside `_cell_paths`, so the
    32 values across the two thread tables were the one part of this data that
    provenance made no statement about at all: the same defect class as the
    fabricated pitch cell, one field along.
    """
    for name in ("iso_thread", "ansi_thread"):
        loaded = hs.table(name)
        cells = {"/".join(path) for path in hs._cell_paths(loaded)}
        for size, row in loaded["rows"].items():
            if "coarse_pitch" in row:
                assert f"{size}/coarse_pitch" in cells

    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    doc["provenance"]["groups"][0]["cells"].remove("M8/coarse_pitch")
    message = _refuses(tmp_path, doc, "declare no sources")
    assert "M8/coarse_pitch" in message


@pytest.mark.parametrize("codepoint,name", [
    ("​", "ZWSP"), ("‌", "ZWNJ"), ("‍", "ZWJ"),
    ("‎", "LRM"), ("⁠", "WORD JOINER"), ("﻿", "BOM"),
    ("­", "SOFT HYPHEN"), ("᠎", "MONGOLIAN VOWEL SEPARATOR"),
])
def test_every_invisible_codepoint_the_docstring_names_is_actually_folded(
        tmp_path, codepoint, name):
    """The docstring said "zero-width characters dropped" over a set of five,
    which is a wider claim than a finite set can honour — U+00AD, U+200E and
    U+180E render as nothing and were outside it. The set is now named
    codepoint by codepoint and this test walks the same list, so the sentence
    and the code cannot drift apart again."""
    assert codepoint in hs._INVISIBLE, name
    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    first = doc["sources"][0]
    doc["sources"] = [first, first[:20] + codepoint + first[20:]]
    _refuses(tmp_path, doc, "same citation once whitespace and case")
