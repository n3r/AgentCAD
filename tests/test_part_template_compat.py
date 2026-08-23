"""`part_template` after the cheat-sheet migration (PRD-029, FR9 / AC7).

The tool keeps its name and its shape — every existing agent calls it first —
but the nine toolkit sections it used to carry are core skills now. That makes
this a compatibility surface with two halves worth pinning:

* what did **not** change: `template` and `cheatsheet` are still there, and the
  cheat-sheet still opens with the CONTRACT an agent needs to write any script;
* what did: the sheet is the *generic minimum* (under 7 000 characters, down
  from ~30 000), and the sections that left it are reachable by name through
  `skills` — every entry a real name in the shipped library, so a `load_skill`
  built from this payload cannot 404.

`threads-and-fasteners` is the capability-gated member of the promoted set
(`requires: [threads]`). This suite runs in the app venv, where `bd_warehouse`
is installed, so it must be *listed*: an absent entry there would mean the
capability probe had quietly started answering False and every threaded-part
agent had lost its guide with no test going red.
"""

import importlib.util

import pytest

from agentcad.core.skills import SkillLibrary


@pytest.fixture()
def payload(tmp_path, kernel):
    from tests.conftest import make_test_service

    return make_test_service(tmp_path / "projects", kernel).part_template()


def test_the_payload_keeps_its_keys(payload):
    assert set(payload) >= {"template", "cheatsheet", "skills", "hint"}
    assert "PARAMS" in payload["template"]
    assert "def build(p)" in payload["template"]
    assert "load_skill" in payload["hint"]


def test_the_cheatsheet_is_the_generic_minimum(payload):
    sheet = payload["cheatsheet"]
    assert "CONTRACT" in sheet
    assert len(sheet) < 7000, len(sheet)
    # The promoted section headings are gone from the sheet — single source.
    for heading in ("ROBUSTNESS TOOLKIT", "HOLE WIZARD", "SHEET METAL",
                    "DESIGN SPECS", "CONSTRAINT SKETCH SOLVER",
                    "THREADS & FASTENERS", "RIBS, BOSSES & DRAFT",
                    "CONNECTORS & MATES"):
        assert heading not in sheet, heading
    # …and the generic half stayed: selectors and the failure modes are what
    # you need to write *any* script, so they are duplicated in the deeper
    # skill on purpose rather than moved out of the sheet.
    assert "Selectors" in sheet
    assert "Common failure modes" in sheet


def test_every_listed_skill_is_a_real_core_skill(payload):
    listed = payload["skills"]
    assert isinstance(listed, list)
    assert len(listed) >= 10, len(listed)
    library = {entry["name"] for entry in SkillLibrary().index()}
    for entry in listed:
        assert set(entry) == {"name", "description"}, entry
        assert entry["name"] in library, entry["name"]
        assert 1 <= len(entry["description"]) <= 200, entry


def test_the_promoted_sections_are_all_reachable_by_name(payload):
    names = {entry["name"] for entry in payload["skills"]}
    assert names >= {
        "robust-parametrics", "selectors-and-occt-failures", "patterns",
        "holes", "ribs-bosses-draft", "sketch-solver", "sheet-metal",
        "design-specs", "assemblies-and-mates",
    }


@pytest.mark.skipif(importlib.util.find_spec("bd_warehouse") is None,
                    reason="threads capability absent: the skill is hidden by "
                           "design, which is what the gate is for")
def test_the_thread_skill_is_listed_when_bd_warehouse_is_installed(payload):
    names = {entry["name"] for entry in payload["skills"]}
    assert "threads-and-fasteners" in names
