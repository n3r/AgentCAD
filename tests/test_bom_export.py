"""PRD-015 Slice 2 — BOM exports (FR4) and the ref-pinned BOM (FR5).

The load-bearing invariants proven here:

* **CSV is lossless (AC3).** ``export_bom`` CSV re-parsed by ``csv.reader``
  yields the exact header + rows, a label carrying a comma AND a double quote
  round-trips unchanged (RFC-4180 ``QUOTE_MINIMAL``), and the TOTAL row equals
  the JSON export's totals.
* **JSON mirrors FR2 and is deterministic.** Two exports of one BOM are
  byte-identical (no wall-clock, sorted keys).
* **A ref pins the BOM (FR5).** ``get_bom {ref=<tag>}`` reproduces the BOM as of
  that tag — a past assembly, not the mutated working tree — and a branch ref
  resolves too; a bogus ref is a clean error.
* **The ephemeral service never pollutes.** A ref read materializes a throwaway
  detached worktree OUTSIDE the project, leaves not one user byte moved, and
  tears the worktree down.
"""

import csv
import io
import json

import pytest

from agentcad.core import bom as bommod
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service


def _service(tmp_path, kernel):
    """A muzzled service (no synchronous snapshots) — the working-tree path."""
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    return service, registry


@pytest.fixture
def real_stack(kernel, tmp_path):
    """The REAL service with the snapshot hook live — a ref needs git history
    (mirrors tests/test_checks_ref.py's `stack`)."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    return service, registry


# a label that exercises both RFC-4180 quoting rules at once
_TRICKY_LABEL = 'Bracket, "L" type'


def _fingerprint(root):
    """sha256 of every user-owned file under *root* (git admin `.history/`
    excluded)."""
    import hashlib
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".history":
            continue
        if path.is_file():
            out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# --------------------------------------------------------------- CSV (AC3)


def test_export_csv_lossless_and_quotes_round_trip(tmp_path, kernel):
    """The CSV re-parses to the exact header + rows, a comma/quote label
    survives, and the TOTAL row equals the JSON totals (AC3)."""
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT, label=_TRICKY_LABEL,
                        material="al6061")
    service.set_assembly("p", [{"id": "b", "part": "bolt",
        "pattern": {"kind": "linear", "count": 3, "step_mm": 10}}])
    service.get_metrics("p", "bolt")   # warm so mass/cost are numeric

    out = registry.call("export_bom", {"project": "p", "format": "csv"})
    assert "error" not in out
    assert out["path"].endswith("exports/bom.csv")
    assert out["format"] == "csv" and out["lines"] == 1

    raw = open(out["path"], newline="", encoding="utf-8").read()
    assert "\r\n" in raw                       # RFC-4180 terminator
    rows = list(csv.reader(io.StringIO(raw, newline="")))
    assert rows[0] == list(bommod.CSV_HEADER)
    body = rows[1]
    # column 3 is `name` — the tricky label came back byte-for-byte
    assert body[bommod.CSV_HEADER.index("name")] == _TRICKY_LABEL
    assert body[bommod.CSV_HEADER.index("qty")] == "3"
    assert body[bommod.CSV_HEADER.index("cost_source")] == "material_estimate"

    totals_row = rows[-1]
    assert totals_row[0] == "TOTAL"
    # The JSON export reports the same numbers.
    j = registry.call("export_bom", {"project": "p", "format": "json"})
    payload = json.loads(open(j["path"], encoding="utf-8").read())
    mass = float(totals_row[bommod.CSV_HEADER.index("unit_mass_g")])
    cost = float(totals_row[bommod.CSV_HEADER.index("ext_cost_usd")])
    assert mass == payload["totals"]["mass_g"]
    assert cost == payload["totals"]["cost_usd"]


def test_export_csv_none_cells_are_empty(tmp_path, kernel):
    """An unbuilt part with no cost writes empty mass/cost cells (not the
    string 'None'), so a re-parse never mistakes a blank for a value."""
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    service.set_assembly("p", [{"id": "b", "part": "bolt"}])
    # A genuinely unbuilt part (the documented post-restart case).
    service._status.clear()
    service._config_status.clear()

    out = registry.call("export_bom", {"project": "p", "format": "csv"})
    rows = list(csv.reader(io.StringIO(
        open(out["path"], newline="", encoding="utf-8").read(), newline="")))
    body = rows[1]
    assert body[bommod.CSV_HEADER.index("unit_mass_g")] == ""
    assert body[bommod.CSV_HEADER.index("unit_cost_usd")] == ""
    assert body[bommod.CSV_HEADER.index("mass_source")] == "unbuilt"


# --------------------------------------------------------------- JSON (FR2)


def test_export_json_mirrors_fr2_and_is_deterministic(tmp_path, kernel):
    """JSON carries the FR2 keys and two exports are byte-identical."""
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT, material="al6061")
    service.set_assembly("p", [{"id": "b", "part": "bolt",
        "pattern": {"kind": "linear", "count": 4, "step_mm": 10}}])
    service.get_metrics("p", "bolt")

    out = registry.call("export_bom", {"project": "p", "format": "json"})
    first = open(out["path"], "rb").read()
    payload = json.loads(first)
    assert set(payload) == {"structure", "lines", "totals", "warnings",
                            "generated_ref"}
    assert payload["generated_ref"] is None
    assert payload["lines"][0]["qty"] == 4
    assert payload["totals"]["mass_g"] > 0

    registry.call("export_bom", {"project": "p", "format": "json"})
    assert open(out["path"], "rb").read() == first    # byte-identical


def test_export_bad_format_is_validation_error(tmp_path, kernel):
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    out = registry.call("export_bom", {"project": "p", "format": "xml"})
    assert out["error"]["type"] == "validation_error"


# ---------------------------------------------------------- ref-pinned (FR5)


def _gadget(service, count):
    service.create_project("gadget")
    service.create_part("gadget", "bolt", script=BOX_SCRIPT)
    service.set_assembly("gadget", [{"id": "b", "part": "bolt",
        "pattern": {"kind": "linear", "count": count, "step_mm": 10}}])
    return "gadget"


def test_get_bom_at_tag_reproduces_the_past(real_stack):
    """Tag at qty 3, mutate to qty 7 → get_bom {ref=tag} still reports 3, the
    working tree reports 7 (FR5)."""
    service, registry = real_stack
    proj = _gadget(service, 3)
    service.branches.tag(proj, "v1", "first release")
    # Mutate after the tag: the pattern grows to 7.
    service.set_assembly(proj, [{"id": "b", "part": "bolt",
        "pattern": {"kind": "linear", "count": 7, "step_mm": 10}}])

    live = registry.call("get_bom", {"project": proj})
    assert live["lines"][0]["qty"] == 7
    assert live["generated_ref"] is None

    tagged = registry.call("get_bom", {"project": proj, "ref": "v1"})
    assert "error" not in tagged
    assert tagged["lines"][0]["qty"] == 3          # the BOM as of the tag
    assert tagged["generated_ref"] == "v1"


def test_get_bom_at_branch_resolves(real_stack):
    """A branch ref resolves too, to the branch head (the current state)."""
    service, registry = real_stack
    proj = _gadget(service, 5)
    branch = service.branches.default_branch(proj)

    at_branch = registry.call("get_bom", {"project": proj, "ref": branch})
    assert "error" not in at_branch
    assert at_branch["lines"][0]["qty"] == 5
    assert at_branch["generated_ref"] == branch


def test_get_bom_bogus_ref_is_a_clean_error(real_stack):
    service, registry = real_stack
    proj = _gadget(service, 2)
    out = registry.call("get_bom", {"project": proj, "ref": "no-such-ref"})
    assert out["error"]["type"] == "notfound_error"
    assert "no-such-ref" in out["error"]["message"]


def test_export_bom_at_ref_writes_into_the_real_project(real_stack):
    """A ref-pinned export lands in the REAL project's exports/, not the
    throwaway worktree."""
    service, registry = real_stack
    proj = _gadget(service, 3)
    service.branches.tag(proj, "v1", "cut")
    service.set_assembly(proj, [{"id": "b", "part": "bolt",
        "pattern": {"kind": "linear", "count": 9, "step_mm": 10}}])

    out = registry.call("export_bom",
                        {"project": proj, "format": "json", "ref": "v1"})
    assert "error" not in out
    real_exports = service.store.exports_dir(proj) / "bom.json"
    assert out["path"] == str(real_exports)
    payload = json.loads(real_exports.read_text(encoding="utf-8"))
    assert payload["generated_ref"] == "v1"
    assert payload["lines"][0]["qty"] == 3         # the tag's qty, not 9


def test_ref_read_leaves_the_project_byte_identical(real_stack):
    """The whole containment point: a ref read moves not one user byte and
    leaves no worktree behind."""
    service, registry = real_stack
    proj = _gadget(service, 4)
    service.branches.tag(proj, "v1", "cut")
    path = service.store.canonical_path_of(proj)

    before = _fingerprint(path)
    worktrees_before = service.history._run(
        path, "worktree", "list", "--porcelain", check=False).stdout

    out = registry.call("get_bom", {"project": proj, "ref": "v1"})
    assert "error" not in out

    # Not one user-owned byte moved — this fingerprint spans `.cache/` and
    # `exports/` too, so a stray write from the throwaway worktree would show.
    assert _fingerprint(path) == before
    # The detached worktree was torn down (git's registration list is back).
    worktrees_after = service.history._run(
        path, "worktree", "list", "--porcelain", check=False).stdout
    assert worktrees_after == worktrees_before
