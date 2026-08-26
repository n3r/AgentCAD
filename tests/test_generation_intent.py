"""PRD-018 Slice 2 — intent normalization, frozen specs, standards grounding.

The load-bearing invariant: a standard's numbers come out of the shipped
``tables/*.json`` read server-side, never out of this module's source. These
tests prove it by (a) comparing the intent's numbers to the real table and
(b) grepping the module to show the numbers are not literals in it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcad.agent import intent as intent_mod
from agentcad.agent.intent import (
    FEATURE_GEOMETRY_DEFERRED,
    Intent,
    STANDARDS_RULE,
    draft_specs,
    frozen_needs_wall,
    frozen_specs,
    frozen_verdict,
    interface_dims_parameterized,
    named_interfaces,
    normalize_intent,
)
from agentcad.core.skills import SkillLibrary
from agentcad.toolkit.specs import (
    is_declaration,
)

NEMA_TABLE = (Path(intent_mod.__file__).resolve().parent.parent
              / "skills" / "brackets-and-mounts" / "tables" / "nema.json")


def _nema_row(frame: str) -> dict:
    data = json.loads(NEMA_TABLE.read_text())
    return next(f for f in data["frames"] if f["frame"] == frame)


# --------------------------------------------------------------- grounding

def test_nema17_numbers_come_from_the_table_not_the_model():
    row = _nema_row("NEMA 17")
    intent = normalize_intent("Design a bracket to mount a NEMA 17 stepper")

    assert intent.standards_cited == [
        {"pack": "brackets-and-mounts", "table": "tables/nema.json",
         "row": "NEMA 17"}
    ]
    mount = next(i for i in intent.interfaces
                 if i.get("standard") == "NEMA 17")
    # Each number equals the table's, byte-for-byte — proving it was read.
    assert mount["bolt_square_mm"] == row["bolt_square_mm"] == 31.0
    assert mount["pilot_d_mm"] == row["pilot_d_mm"] == 22.0
    assert mount["screw"] == row["screw"] == "M3"
    assert mount["clearance_d_mm"] == row["clearance_d_mm"] == 3.4
    assert mount["source"] == intent.standards_cited[0]


def test_nema17_numbers_are_not_literals_in_the_source():
    source = Path(intent_mod.__file__).read_text()
    # The dimensions the intent grounded must appear nowhere as literals.
    for magic in ("31.0", "22.0", "3.4", "42.3", "43.84"):
        assert magic not in source, f"{magic!r} is a literal in intent.py"


def test_ungrounded_standard_invents_nothing():
    # NEMA 42 is not in the shipped table — grounding must decline, not guess.
    intent = normalize_intent("A mount for a NEMA 42 servo motor")
    assert intent.standards_cited == []
    assert all("bolt_square_mm" not in i for i in intent.interfaces)
    # The request survives verbatim for the loop's model to handle.
    assert "NEMA 42" in intent.free_text


def test_grounding_reads_through_the_injected_skill_library():
    # The read path is SkillLibrary.load(asset=...); an injected library works.
    intent = normalize_intent("mount a NEMA 14 motor", skills=SkillLibrary())
    row = _nema_row("NEMA 14")
    mount = next(i for i in intent.interfaces if i.get("standard") == "NEMA 14")
    assert mount["bolt_square_mm"] == row["bolt_square_mm"]


# --------------------------------------------------------------- draft specs

def test_draft_specs_cover_every_stated_constraint():
    intent = normalize_intent(
        "A housing with 2 mm walls, under 50 g, envelope 60x40x20 mm")
    specs = draft_specs(intent)

    assert all(is_declaration(s) for s in specs)
    by_kind = {s["kind"]: s for s in specs}
    assert set(by_kind) == {"wall", "mass", "bbox"}

    assert by_kind["wall"]["limit"] == {"min_mm": 2.0}
    assert by_kind["mass"]["limit"] == {"max_g": 50.0}
    assert by_kind["bbox"]["limit"] == {"within_mm": [60.0, 40.0, 20.0]}


def test_mass_direction_lower_bound():
    intent = normalize_intent("must weigh at least 20 g")
    spec = next(s for s in draft_specs(intent) if s["kind"] == "mass")
    assert spec["limit"] == {"min_g": 20.0}


def test_grounded_interface_yields_a_footprint_spec():
    intent = normalize_intent("bracket for a NEMA 17 motor")
    specs = draft_specs(intent)
    that = [s for s in specs if s["kind"] == "that"]
    assert any("covers_bolt_square" in s["name"] for s in that)


# ---------------------------------------- server-owned frozen-spec measurement
#
# The freeze contract is enforced by re-deriving the frozen specs from the
# intent and MEASURING them against geometry (see test_generation_integrity for
# the end-to-end kernel proof). The geometry is measured by building the
# UNMODIFIED recorded script through the kernel ``frozen_measure`` op — no SPECS
# is appended, so build() cannot detect being measured — and the server
# evaluates the frozen bounds/predicates ITSELF over those metrics. A raw
# ``frozen_measure`` result dict is ``{size, mass_g, volume_mm3, min_wall}``.

def _measured(**metrics):
    """A stand-in ``frozen_measure`` kernel result dict."""
    return dict(metrics)


def test_frozen_specs_cover_the_needed_measurements():
    intent = normalize_intent("2 mm wall, under 50 g, envelope 60x40x20 mm")
    specs = frozen_specs(intent)
    kinds = {s["kind"] for s in specs}
    assert {"bbox", "mass", "wall"} <= kinds
    # A wall constraint asks for the (expensive) min-wall probe; mass/volume
    # bounds do not.
    assert frozen_needs_wall(specs) is True
    assert frozen_needs_wall(frozen_specs(normalize_intent("under 50 g"))) is False


def test_frozen_specs_empty_without_a_machine_checkable_constraint():
    assert frozen_specs(normalize_intent("a small bracket")) == []


def test_verdict_measures_a_mass_bound_server_side():
    intent = normalize_intent("under 50 g")
    # A 30 g measurement passes the 50 g budget; 60 g fails; a MISSING metric is
    # fail-closed (not measured is not a pass).
    assert frozen_verdict(intent, _measured(mass_g=30.0))["frozen_ok"]
    bad = frozen_verdict(intent, _measured(mass_g=60.0))
    assert bad["frozen_ok"] is False and "mass" in bad["frozen_violations"][0]
    closed = frozen_verdict(intent, _measured())
    assert closed["frozen_ok"] is False   # no metric -> fail-closed


def test_verdict_runs_the_bolt_square_predicate_over_the_measured_bbox():
    intent = normalize_intent("bracket for a NEMA 17 motor")
    # A 42 mm plate spans the 31 mm square; a 10 mm cube does not.
    assert frozen_verdict(
        intent, _measured(size=[42.0, 42.0, 5.0]))["frozen_ok"]
    small = frozen_verdict(intent, _measured(size=[10.0, 10.0, 10.0]))
    assert small["frozen_ok"] is False
    assert "covers_bolt_square" in small["frozen_violations"][0]


def test_verdict_is_empty_ok_without_a_constraint():
    assert frozen_verdict(normalize_intent("a bracket"), {})["frozen_ok"] is True


def test_verdict_no_standard_number_is_a_literal_in_intent_py():
    # The grounding stays table-sourced: no NEMA dimension in intent.py's source.
    from agentcad.agent import intent as intent_mod
    for magic in ("31.0", "22.0", "3.4"):
        assert magic not in Path(intent_mod.__file__).read_text()


# --------------------------------------------------------------- misc + I/O

def test_material_and_quantity_parsed():
    intent = normalize_intent("A PLA clip, batch of 100")
    assert intent.material == "pla"
    assert intent.quantities == {"count": 100}


def test_screw_interface_parsed():
    intent = normalize_intent("a plate with an M3 clearance hole")
    assert any(i.get("screw") == "M3" for i in intent.interfaces)


def test_sources_recorded_without_bytes():
    intent = normalize_intent("from the datasheet", images=["/tmp/a.png"],
                              pdf_text="Bolt square 31 mm, M3")
    kinds = {s["kind"] for s in intent.sources}
    assert kinds == {"image", "pdf_text"}
    pdf = next(s for s in intent.sources if s["kind"] == "pdf_text")
    # The pdf_text digest is honestly named a TEXT digest — it is NOT a digest
    # of the PDF's bytes (Codex9).
    assert "text_sha256" in pdf and "chars" in pdf
    assert "sha256" not in pdf

def test_intent_serialization_round_trips():
    intent = normalize_intent(
        "NEMA 17 bracket, 2 mm wall, under 50 g, aluminium")
    data = intent.to_dict()
    # Genuinely JSON-serializable (the FR2 "returned with the result" form).
    encoded = json.dumps(data)
    rebuilt = Intent.from_dict(json.loads(encoded))
    assert rebuilt.to_dict() == data


def test_standards_rule_text_is_exact():
    assert STANDARDS_RULE == "never invent a standard dimension — cite the pack or ask"


# ---------------------------------------- Codex11: word-boundary parsing bugs

def test_aluminum_does_not_reverse_a_mass_budget():
    # "aluminum" CONTAINS the substring "min" — the old substring match flipped
    # "under 50 g" into a 50 g FLOOR. Word boundaries fix it.
    intent = normalize_intent("An aluminum bracket under 50 g")
    mass = next(c for c in intent.constraints if c["kind"] == "mass")
    assert mass == {"kind": "mass", "max_g": 50.0}
    assert intent.material == "aluminium"


def test_aluminium_spelling_also_does_not_reverse():
    intent = normalize_intent("an aluminium clip, under 30 g")
    mass = next(c for c in intent.constraints if c["kind"] == "mass")
    assert mass == {"kind": "mass", "max_g": 30.0}


def test_minimum_and_maximum_words_still_parse():
    lo = normalize_intent("must weigh a minimum of 20 g")
    assert next(c for c in lo.constraints if c["kind"] == "mass") == \
        {"kind": "mass", "min_g": 20.0}
    hi = normalize_intent("a maximum of 40 g")
    assert next(c for c in hi.constraints if c["kind"] == "mass") == \
        {"kind": "mass", "max_g": 40.0}


def test_pcb_envelope_is_an_interface_not_a_part_size_ceiling():
    # "50x70 mm PCB" is the PCB's footprint the bracket must SPAN, not a
    # within-envelope on the bracket itself (the Codex11 freeze bug).
    intent = normalize_intent("A bracket to mount a 50x70 mm PCB")
    assert intent.envelope is None, "the PCB size must not be the part envelope"
    pcb = next(i for i in intent.interfaces if i["name"] == "pcb_edge")
    assert pcb["footprint_mm"] == [50.0, 70.0]
    # The drafted spec is a COVERS (lower-bound) check, never a within ceiling.
    specs = draft_specs(intent)
    assert any("pcb_edge_covers_footprint" in s.get("name", "") for s in specs)
    assert not any(s["kind"] == "bbox" for s in specs)


def test_plain_envelope_without_a_mounted_noun_is_still_the_part_size():
    intent = normalize_intent("a housing, envelope 60x40x20 mm")
    assert intent.envelope == {"within_mm": [60.0, 40.0, 20.0]}


# ------------------------------------------------ FR7: named interfaces surfaced

def test_named_interfaces_surface_for_the_prompt():
    intent = normalize_intent("A bracket for a NEMA 17 stepper")
    assert "NEMA 17 face" in named_interfaces(intent)


def test_named_interfaces_surface_a_standalone_screw():
    # A screw not already carried by a grounded mount surfaces on its own.
    intent = normalize_intent("a plate with M3 clearance holes")
    assert "M3 fastener" in named_interfaces(intent)


def test_named_interfaces_include_a_pcb_edge():
    intent = normalize_intent("carrier for a 50x70 mm PCB")
    assert "PCB edge" in named_interfaces(intent)


# ------------------------------------- FR6 meta-spec: interface dims are PARAMS

_NEMA_MAGIC = '''\
from build123d import Box
PARAMS = {}
def build(p):
    return Box(31.0, 31.0, 5.0)   # 31.0 = NEMA 17 bolt square, hardcoded
'''

_NEMA_PARAM = '''\
from build123d import Box
PARAMS = {"bolt_sq": {"default": 31.0, "min": 16.0, "max": 70.0, "unit": "mm"}}
def build(p):
    return Box(p.bolt_sq, p.bolt_sq, 5.0)
'''


def test_meta_spec_flags_a_hardcoded_interface_dimension():
    intent = normalize_intent("Mount for a NEMA 17")
    violations = interface_dims_parameterized(intent, _NEMA_MAGIC, {})
    assert violations, "31.0 hardcoded, not a PARAM -> a violation"
    assert any("bolt_square_mm" in v for v in violations)


def test_meta_spec_passes_when_the_dimension_is_a_param():
    intent = normalize_intent("Mount for a NEMA 17")
    # The candidate exposes bolt_sq as a PARAM whose value equals the dim.
    assert interface_dims_parameterized(
        intent, _NEMA_PARAM, {"bolt_sq": 31.0}) == []


def test_meta_spec_ignores_a_dimension_absent_from_the_script():
    # A plain plate that never uses the bolt square is not a magic-number part.
    intent = normalize_intent("Mount for a NEMA 17")
    plate = "from build123d import Box\nPARAMS = {}\ndef build(p):\n    return Box(42.0, 42.0, 5.0)\n"
    assert interface_dims_parameterized(intent, plate, {}) == []


# ------------------------------- Codex8: feature-geometry deferral is recorded

def test_feature_geometry_deferral_is_documented():
    # The mounting-HOLE pattern (pitch/diameter/pilot) is not yet re-measured
    # server-side; the reason is recorded, not silently skipped (FR8 honesty).
    assert "circle-inventory" in FEATURE_GEOMETRY_DEFERRED
    assert "forge" in FEATURE_GEOMETRY_DEFERRED
    # …and it is cited in the module so a reader finds it.
    assert "FEATURE_GEOMETRY_DEFERRED" in Path(intent_mod.__file__).read_text()


# --------------------------------------------- Codex9: attachment byte digests

def test_prepared_attachment_records_a_byte_digest():
    import base64
    import hashlib

    raw = b"\x89PNG\r\n\x1a\n" + b"fake image bytes"
    b64 = base64.b64encode(raw).decode("ascii")
    intent = normalize_intent(
        "from the render",
        images=[{"png_base64": b64, "media_type": "image/png",
                 "source_name": "shot.png", "kind": "image"}])
    src = intent.sources[0]
    # The digest is of the ACTUAL bytes, not of any text.
    assert src["sha256"] == hashlib.sha256(raw).hexdigest()
    assert src["bytes"] == len(raw)
    assert src["name"] == "shot.png" and src["media_type"] == "image/png"


def test_pdf_page_bytes_are_hashed_and_source_sha256_flows_through():
    import base64
    import hashlib

    raw = b"rasterized page png bytes"
    b64 = base64.b64encode(raw).decode("ascii")
    intent = normalize_intent(
        "from the datasheet",
        images=[{"png_base64": b64, "media_type": "image/png",
                 "source_name": "sheet.pdf", "kind": "pdf_page",
                 "source_sha256": "deadbeef"}],
        pdf_text="Bolt square 31 mm")
    page = next(s for s in intent.sources if s["kind"] == "pdf_page")
    assert page["sha256"] == hashlib.sha256(raw).hexdigest()
    # The ORIGINAL file's byte digest (supplied by intake) rides through.
    assert page["source_sha256"] == "deadbeef"
