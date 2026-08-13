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

@pytest.mark.parametrize("name", ["iso_clearance", "iso_thread", "iso_cbore_csk"])
def test_every_data_file_carries_the_provenance_header(name):
    """Decision 5's header, including TWO named sources. A table with one
    source is a transcription nobody checked."""
    doc = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    assert doc["schema"] == 1
    assert doc["units"] == "mm"
    assert doc["standard"] and doc["revision"]
    assert len(doc["sources"]) >= 2, f"{name}: fewer than two published sources"
    assert doc["rows"]


def test_clearance_rows_are_complete_and_ordered_fine_medium_coarse():
    doc = json.loads((DATA / "iso_clearance.json").read_text(encoding="utf-8"))
    for size, row in doc["rows"].items():
        assert set(row) == {"fine", "medium", "coarse"}, size
        assert row["fine"] < row["medium"] < row["coarse"], size
        nominal = float(size[1:])
        assert row["fine"] > nominal, f"{size}: a clearance hole must clear"


def test_thread_rows_carry_a_coarse_pitch_that_is_present_in_pitches():
    doc = json.loads((DATA / "iso_thread.json").read_text(encoding="utf-8"))
    for size, row in doc["rows"].items():
        coarse = row["coarse_pitch"]
        assert hs._pitch_key(coarse) in row["pitches"], size
        for pitch_key, entry in row["pitches"].items():
            assert entry["series"] in ("coarse", "fine"), size
            drill = entry["tap_drill"]
            nominal = float(size[1:])
            # A tap drill lies between the minor and the major diameter; d - P
            # is the classic ~100%-engagement figure and the published drill is
            # never below it (it would not cut) nor above the nominal.
            assert nominal - float(pitch_key) - 0.35 <= drill < nominal, size


def test_head_rows_are_monotone_in_size():
    doc = json.loads((DATA / "iso_cbore_csk.json").read_text(encoding="utf-8"))
    for family, fasteners in doc["rows"].items():
        for fastener, rows in fasteners.items():
            heads = [row["head_d"] for row in rows.values()]
            assert heads == sorted(heads), f"{family}/{fastener}"
            for size, row in rows.items():
                assert row["head_d"] > float(size[1:]), f"{family}/{fastener}/{size}"


# ------------------------------------------------------------------ lookups

def test_ac3_clearance_for_m5_returns_the_three_published_iso_273_diameters():
    """**AC3** — the tool's own example, at the published values.

    ISO 273:1979 M5: fine (H12) 5.3, medium (H13) 5.5, coarse (H14) 5.8.
    """
    answer = hs.lookup(family="clearance", size="M5")
    assert answer["std"] == "iso"
    assert answer["fits"] == {"fine": 5.3, "medium": 5.5, "coarse": 5.8}
    assert answer["standard"].startswith("ISO 273")
    assert len(answer["sources"]) >= 2


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
    assert row["head_d"] == 11.2          # ISO 10642 dk theoretical = 2.24 x d
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


def test_a_standard_with_no_table_yet_says_so_rather_than_answering_wrong():
    """ANSI tables land in slice 8. Until then an ANSI lookup is an error that
    names the standard — never a silent fallback to the ISO numbers."""
    with pytest.raises(ValueError, match=r"std"):
        hs.clearance("M5", std="ansi")


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
