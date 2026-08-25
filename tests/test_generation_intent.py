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
    Intent,
    STANDARDS_RULE,
    draft_specs,
    freeze,
    frozen_spec_violation,
    normalize_intent,
)
from agentcad.core.skills import SkillLibrary
from agentcad.toolkit.specs import (
    check_mass,
    check_wall,
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


# --------------------------------------------------------------- freeze/diff

def test_frozen_mass_weakened_is_a_violation():
    frozen = freeze([check_mass(max_g=50.0)])
    weakened = [check_mass(max_g=80.0)]
    violations = frozen_spec_violation(frozen, weakened)
    assert violations and "mass" in violations[0]


def test_frozen_mass_strengthened_is_allowed():
    frozen = freeze([check_mass(max_g=50.0)])
    stronger = [check_mass(max_g=40.0)]
    assert frozen_spec_violation(frozen, stronger) == []


def test_loop_may_add_specs():
    frozen = freeze([check_mass(max_g=50.0)])
    added = [check_mass(max_g=50.0), check_wall(min_mm=2.0)]
    assert frozen_spec_violation(frozen, added) == []


def test_frozen_spec_deleted_is_a_violation():
    frozen = freeze([check_mass(max_g=50.0), check_wall(min_mm=2.0)])
    # The candidate dropped the wall check entirely.
    remaining = [check_mass(max_g=50.0)]
    violations = frozen_spec_violation(frozen, remaining)
    assert len(violations) == 1
    assert "wall_min" in violations[0] and "deleted" in violations[0]


def test_frozen_wall_weakened_is_a_violation():
    frozen = freeze([check_wall(min_mm=2.5)])
    weakened = [check_wall(min_mm=1.0)]
    assert frozen_spec_violation(frozen, weakened)


def test_draft_specs_freeze_round_trips_over_intent():
    intent = normalize_intent("2 mm wall, under 50 g")
    frozen = freeze(draft_specs(intent))
    # The very specs it froze do not violate their own freeze set.
    assert frozen_spec_violation(frozen, draft_specs(intent)) == []


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
    assert "sha256" in pdf and "chars" in pdf

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
