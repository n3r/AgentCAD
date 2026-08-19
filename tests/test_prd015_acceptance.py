"""PRD-015 — BOM & release management, consolidated acceptance suite (AC1-AC8).

One place that grades the PRD's *acceptance criteria* against the shipped
surface, driving the real ``get_bom``/``export_bom``/``release_*`` tools through
the registry (and the real kernel where a build is required). The slice suites
already grade the mechanics:

* ``tests/test_bom.py`` — roll-ups, per-config lines, cost sourcing, zero-kernel.
* ``tests/test_bom_export.py`` — CSV/JSON lossless, ref-pinned BOM.
* ``tests/test_releases.py`` — ``release_start``, the gate, the waiver.
* ``tests/test_release_finalize.py`` — finalize, tag, record immutability.
* ``tests/test_release_bundle.py`` — the reproducible bundle (FR10-11).
* ``tests/test_bom_release_routes.py`` — the HTTP surface.

This file grades the *criteria*, once each, and is deliberately additive rather
than a re-run of the slice assertions.

What is machine-checked here vs. graded elsewhere:

* **AC1** (machine, API half) — a small two-part assembly cuts a release
  end-to-end: green gate → approve (records principal + ts) → finalize →
  ``released``, the ``release/<rev>`` tag exists, the approval carries a
  principal + timestamp, and the bundle is present + downloadable (a
  ``exports/releases/A/`` directory with artifacts and a zip beside it). The
  zero-terminal browser session half is the controller's (evidence-graded).
* **AC2** (machine) — a sub-assembly instanced twice, each with an 8-member
  bolt pattern, rolls up to one screw line of ``qty: 16`` through ``get_bom``;
  flat and indented structures agree on totals.
* **AC3** (machine) — ``export_bom`` CSV re-parsed by a strict ``csv.reader``:
  the expected header, a label with a comma AND a quote round-trips, totals
  match the JSON export.
* **AC4** (machine) — a failing spec leaves ``release_start`` in ``draft`` with
  the failing check named in the gate report; ``waive: {reason}`` proceeds and
  the waiver appears in ``get_release``.
* **AC5** (machine) — a finalized (terminal) record is append-only: mutating it
  is a ``conflict_error``; branching from the tag and editing a part there
  succeeds.
* **AC6** (machine) — two bundle runs at the same tag produce identical
  ``artifacts.json`` hashes for every ``deterministic``-class artifact, and STEP
  matches after timestamp-line normalization.
* **AC7** (machine, built half) — a three-config flange yields three BOM lines
  carrying the three configs with distinct per-config masses. The
  distinct-part-number-per-config-suffix half is a documented slice-1 deferral
  (one per-part ``bom`` field) and is on the record as a skipped placeholder.
* **AC8** (machine) — a project with no ``bom`` fields and no ``releases``
  behaves exactly as before: ``get_bom`` still enumerates with blank part
  numbers, no ``releases`` section appears in the manifest, and an ordinary
  proposal is still ``kind: "change"``. The "full suite green on the three-OS
  matrix" half is the close-out changelog's count citation (no count guard is
  added here — the house rule keeps a single one per suite family).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from pathlib import Path

import pytest

from agentcad.core import bom as bommod
from agentcad.core import locks, releases
from agentcad.core.branches import pinned_tree_var
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import (BOX_SCRIPT, FLANGE_SCRIPT, THREE_SIZE_CONFIGS,
                       make_test_service)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_HAS_GIT = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not _HAS_GIT, reason="git not found on PATH")


# A hollow box: specs pass at the default wall (green gate); driving ``wall``
# below 2.0 mm turns the specs gate red. Mirrors the slice suites' GATE_BOX.
GATE_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import check_mass, check_wall

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 60.0, "unit": "mm",
                   "description": "outer edge"},
          "wall": {"default": 2.5, "min": 0.5, "max": 5.0, "unit": "mm",
                   "description": "wall thickness"}}

SPECS = [
    check_wall(min_mm=2.0, grid=4, requirement="ENG-014"),
    check_mass(max_g=500.0, requirement="SYS-042"),
]

def build(p):
    inner = p.size - 2 * p.wall
    return Box(p.size, p.size, p.size) - Box(inner, inner, inner)
'''

# A sheet-metal bracket — declares ``flat_pattern(p)`` so the bundle produces a
# flat-pattern artifact (a deterministic-class file the AC6 comparison covers).
BRACKET = '''\
from agentcad.toolkit.sheetmetal import SheetPart

PARAMS = {
    "width":      {"default": 60.0, "min": 10.0, "max": 500.0, "unit": "mm",
                   "description": "base plate width (X)"},
    "depth":      {"default": 40.0, "min": 10.0, "max": 500.0, "unit": "mm",
                   "description": "base plate depth (Y)"},
    "thick":      {"default": 2.0,  "min": 0.5,  "max": 6.0,   "unit": "mm",
                   "description": "sheet thickness"},
    "flange_len": {"default": 30.0, "min": 5.0,  "max": 200.0, "unit": "mm",
                   "description": "flange leaf length beyond the bend"},
    "bend_r":     {"default": 3.0,  "min": 0.5,  "max": 20.0,  "unit": "mm",
                   "description": "inner bend radius"},
}

def _sheet(p):
    return (SheetPart(p.thick)
            .base(p.width, p.depth)
            .flange("front", 90, p.flange_len, inner_radius=p.bend_r))

def build(p):
    return _sheet(p).fold()

def flat_pattern(p):
    sp = _sheet(p)
    return sp.unfold(), sp.bend_lines()
'''


@pytest.fixture(autouse=True)
def _reset_context():
    """A fresh, restorable client identity + tree pin for every test (mirrors
    the slice suites; harmless for the non-git BOM tests too)."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


# --------------------------------------------------------------- helpers ------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check(gate: dict, name: str) -> dict:
    return next(c for c in gate["checks"] if c["name"] == name)


def _on(service, project: str, client: str, branch: str) -> None:
    locks.set_client_id(client)
    if service.branches.current(project) != branch:
        service.branches.switch(project, branch)


def _approve(registry, project: str, pid: str, reviewer: str = "agent_b") -> None:
    """Approve a release proposal as a DIFFERENT principal (self-approval does
    not count under the default policy)."""
    prev = locks.current_client_id()
    locks.set_client_id(reviewer)
    try:
        res = registry.call("proposal_review",
                            {"project": project, "id": pid,
                             "verdict": "approve"})
        assert "error" not in res, res
    finally:
        locks.set_client_id(prev)


def _gate_demo(kernel, root: Path):
    """A REAL service (snapshot hook live, so the tree is clean after each write
    — the state the release gate's clean-tree check expects) with a single
    spec-declaring box on master and a 'rel' release branch off it."""
    service = AgentCADService(root, kernel, EventBus())
    registry = build_registry(service)
    assert registry.get("release_start") is not None
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": GATE_BOX})
    service.branches.create("demo", "rel")
    return service, registry


def _start_green(service, registry) -> dict:
    _on(service, "demo", "agent_a", "rel")
    started = releases.release_start(service, "demo")
    assert started["gate"]["status"] == "green", started["gate"]
    assert started["status"] == "in_review"
    return started


# ================================================================= AC1 ========


@pytest.fixture
def bundle_demo(kernel, tmp_path):
    """'demo' with a solid box + a sheet-metal bracket + a two-instance assembly
    on master, and a 'rel' release branch checked out — the small assembly AC1
    cuts a real release of (and AC6 rebuilds)."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert registry.get("release_bundle") is not None, "slice 5 tool missing"
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": GATE_BOX})
    assert "error" not in registry.call(
        "create_part",
        {"project": "demo", "part_id": "bracket", "script": BRACKET})
    assert "error" not in registry.call("set_assembly", {
        "project": "demo",
        "instances": [{"id": "b1", "part": "box"},
                      {"id": "k1", "part": "bracket", "position": [80, 0, 0]}]})
    service.branches.create("demo", "rel")
    return service, registry


def _finalize_A(service, registry) -> dict:
    started = _start_green(service, registry)
    _approve(registry, "demo", started["proposal"])
    _on(service, "demo", "agent_a", "rel")
    return releases.release_finalize(service, "demo", "A")


@requires_git
def test_ac1_a_release_is_cut_end_to_end_with_tag_approval_and_bundle(bundle_demo):
    """**AC1 (API half)** — the two-part assembly cuts a release end-to-end:
    green gate → approve (principal + ts recorded) → finalize → ``released``,
    the immutable ``release/a`` tag exists, the approval carries a principal and
    a timestamp, and the bundle is present + downloadable (a
    ``exports/releases/A/`` directory of artifacts with a zip beside it).

    The zero-terminal browser session that drives this in the UI is the
    controller's (evidence-graded); this asserts the tool/HTTP machine half.
    """
    service, registry = bundle_demo
    record = _finalize_A(service, registry)

    # The record reached the terminal released state.
    assert record["status"] == "released"
    assert record["rev"] == "A"
    assert record["tag"] == "release/a"

    # The immutable tag exists in the repo and points at a real commit.
    canonical = service.store.canonical_path_of("demo")
    assert service.history.resolve_tag(canonical, "release/a") is not None
    # And it is registered as a referenced version (PRD-001 FR5: cannot be moved).
    version = next(v for v in service.branches.versions("demo")
                   if v["name"] == "release/a")
    assert {"release": "A"} in version["referrers"]

    # The approval carries a principal AND a timestamp (the audit answer to
    # "who released this"), attributed to the reviewer, not the author.
    assert record["approvals"], "no approval recorded"
    approval = record["approvals"][0]
    assert approval["principal"] == "agent_b"
    assert approval["ts"].endswith("Z")

    # The bundle is present and downloadable: a directory of artifacts under
    # exports/releases/A with a zip beside it.
    bundle = record.get("bundle")
    assert bundle, "finalize did not build a bundle inline"
    bundle_dir = Path(bundle["dir"])
    assert bundle_dir.is_dir()
    assert bundle_dir.name == "A"
    assert bundle_dir.parent.name == "releases"
    assert bundle_dir.parent.parent.name == "exports"
    art = json.loads((bundle_dir / "artifacts.json").read_text())
    assert art["files"], "the bundle manifest lists no artifacts"
    # The STEP/BOM/README/drawings all landed.
    names = {p.name for p in bundle_dir.iterdir()}
    assert {"assembly.step", "bom.csv", "bom.json", "README.md",
            "artifacts.json"} <= names
    # The downloadable zip sits beside the directory.
    zip_path = Path(bundle["zip"])
    assert zip_path.is_file()


# ================================================================= AC2 ========


def test_ac2_a_twice_instanced_subassembly_rolls_bolts_up_to_sixteen(
        kernel, tmp_path):
    """**AC2** — a sub-assembly instanced twice, each carrying an 8-member bolt
    pattern, rolls up through ``get_bom`` to one screw line of ``qty: 16``, and
    the flat and indented structures agree on totals.

    Graded end-to-end through the ``get_bom`` tool (the slice test calls
    ``build_bom`` directly), so the criterion is exercised over the registry.
    """
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)

    service.create_project("widget")
    service.create_part("widget", "bolt", script=BOX_SCRIPT)
    service.set_assembly("widget", [{"id": "bolts", "part": "bolt",
        "pattern": {"kind": "linear", "count": 8, "step_mm": 5}}])

    service.create_project("top")
    service.set_assembly("top", [
        {"id": "w1", "assembly": {"project": "widget"}, "position": [0, 0, 0]},
        {"id": "w2", "assembly": {"project": "widget"}, "position": [50, 0, 0]},
    ])

    flat = registry.call("get_bom", {"project": "top", "structure": "flat"})
    assert "error" not in flat, flat
    assert len(flat["lines"]) == 1
    line = flat["lines"][0]
    assert line["part_id"] == "bolt"
    assert line["qty"] == 16                      # 8 bolts * 2 sub-assemblies

    indented = registry.call(
        "get_bom", {"project": "top", "structure": "indented"})
    assert "error" not in indented, indented
    # Flat collapses the two occurrences; indented keeps each with a level.
    assert all("level" in ln for ln in indented["lines"])
    # The two structures roll to the same totals (mass + cost).
    assert flat["totals"] == indented["totals"]


# ================================================================= AC3 ========


# A label that exercises both RFC-4180 quoting rules at once (a comma AND a
# double quote inside the field).
_TRICKY_LABEL = 'Bracket, "L" type'


def test_ac3_the_csv_export_is_lossless_under_a_strict_reader(kernel, tmp_path):
    """**AC3** — ``export_bom`` CSV re-parsed by a strict ``csv.reader`` yields
    the expected header, a label carrying a comma AND a double quote round-trips
    byte-for-byte, and the TOTAL row equals the JSON export's totals.
    """
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT, label=_TRICKY_LABEL,
                        material="al6061")
    service.set_assembly("p", [{"id": "b", "part": "bolt",
        "pattern": {"kind": "linear", "count": 3, "step_mm": 10}}])
    service.get_metrics("p", "bolt")              # warm so mass/cost are numeric

    out = registry.call("export_bom", {"project": "p", "format": "csv"})
    assert "error" not in out, out
    assert out["path"].endswith("exports/bom.csv")

    raw = open(out["path"], newline="", encoding="utf-8").read()
    rows = list(csv.reader(io.StringIO(raw, newline="")))

    # The header is exactly the canonical BOM header.
    assert rows[0] == list(bommod.CSV_HEADER)
    # The tricky label survived RFC-4180 quoting unchanged.
    body = rows[1]
    assert body[bommod.CSV_HEADER.index("name")] == _TRICKY_LABEL
    assert body[bommod.CSV_HEADER.index("qty")] == "3"

    # The TOTAL row equals the JSON export's totals (the two formats agree).
    totals_row = rows[-1]
    assert totals_row[0] == "TOTAL"
    j = registry.call("export_bom", {"project": "p", "format": "json"})
    assert "error" not in j, j
    payload = json.loads(Path(j["path"]).read_text(encoding="utf-8"))
    csv_mass = float(totals_row[bommod.CSV_HEADER.index("unit_mass_g")])
    csv_cost = float(totals_row[bommod.CSV_HEADER.index("ext_cost_usd")])
    assert csv_mass == payload["totals"]["mass_g"]
    assert csv_cost == payload["totals"]["cost_usd"]


# ================================================================= AC4 ========


@requires_git
def test_ac4_a_failing_spec_blocks_the_release_and_a_waiver_proceeds(
        kernel, tmp_path):
    """**AC4** — a failing spec leaves ``release_start`` in ``draft`` with the
    failing check named in the gate report, and ``waive: {reason}`` proceeds and
    the waiver (reason + attributed principal) appears in ``get_release``.

    Two independent projects so the red-gate and the waiver are graded from the
    same failing state without a rev interfering with the other.
    """
    # --- the red gate blocks and names the failing check ----------------------
    service, registry = _gate_demo(kernel, tmp_path / "red")
    _on(service, "demo", "agent_a", "rel")
    registry.call("set_params", {"project": "demo", "part_id": "box",
                                 "values": {"wall": 1.0}})     # spec goes red

    _on(service, "demo", "agent_a", "rel")
    blocked = releases.release_start(service, "demo")
    assert blocked["gate"]["status"] == "red"
    assert blocked["status"] == "draft"
    specs = _check(blocked["gate"], "specs")
    assert specs["status"] == "fail"
    failing_ids = [f["id"] for f in specs["gate"]["details"]["failures"]]
    assert "box:wall_min" in failing_ids           # the failing check is named
    # The record stays draft (not released) with the red report on it.
    rec = releases.get_release(service, "demo", "A")["release"]
    assert rec["status"] == "draft"

    # --- a waiver proceeds past the red gate and is recorded ------------------
    service2, registry2 = _gate_demo(kernel, tmp_path / "waive")
    _on(service2, "demo", "agent_a", "rel")
    registry2.call("set_params", {"project": "demo", "part_id": "box",
                                  "values": {"wall": 1.0}})
    _on(service2, "demo", "agent_a", "rel")
    waived = releases.release_start(
        service2, "demo", notes="ship it", waive={"reason": "cosmetic only"})
    assert waived["gate"]["status"] == "green"     # the waiver unblocks it
    assert waived["status"] == "in_review"
    waived_specs = _check(waived["gate"], "specs")
    assert waived_specs["status"] == "fail" and waived_specs["waived"] is True
    # The waiver is durable + attributed and visible from get_release.
    release = releases.get_release(service2, "demo", "A")["release"]
    assert release["waiver"]["reason"] == "cosmetic only"
    assert release["waiver"]["principal"] == "agent_a"
    assert release["waiver"]["ts"].endswith("Z")


# ================================================================= AC5 ========


@requires_git
def test_ac5_a_finalized_record_is_immutable_and_a_tag_branch_edits(
        kernel, tmp_path, monkeypatch):
    """**AC5** — a finalized (terminal) release record is append-only: a tool
    that would rewrite it is a ``conflict_error``; you cannot switch onto the
    tag, but branching from ``release/<rev>`` and editing a part there succeeds.

    The bundle is stubbed to a fast no-op — the finalize/immutability paths, not
    the bundle, are under test here (the bundle has its own AC6 coverage).
    """
    def _noop(service, project, rev):
        releases._persist_bundle(service, project, rev, {"stubbed": True})
        return {"stubbed": True}
    monkeypatch.setattr(releases, "build_bundle", _noop)

    service, registry = _gate_demo(kernel, tmp_path / "projects")

    # Release A, then release B — which supersedes A, making A terminal.
    started_a = _start_green(service, registry)
    _approve(registry, "demo", started_a["proposal"])
    _on(service, "demo", "agent_a", "rel")
    releases.release_finalize(service, "demo", "A")
    service.proposals.update("demo", started_a["proposal"], state="closed")

    started_b = _start_green(service, registry)
    _approve(registry, "demo", started_b["proposal"])
    _on(service, "demo", "agent_a", "rel")
    releases.release_finalize(service, "demo", "B")

    assert releases.get_release(service, "demo", "A")["release"]["status"] \
        == "superseded"

    # A is terminal: any tool that would rewrite the record is a conflict_error.
    conflict = registry.call("release_finalize", {"project": "demo", "rev": "A"})
    assert conflict["error"]["type"] == "conflict_error"

    # You cannot switch onto a tag (it is not a branch) — the only way to evolve
    # a released state is to branch off the tag, then edit on that branch.
    with pytest.raises(Exception):
        service.branches.switch("demo", "release/a")

    service.branches.create("demo", "hotfix", from_ref="release/a")
    _on(service, "demo", "agent_a", "hotfix")
    edited = registry.call("set_params", {"project": "demo", "part_id": "box",
                                          "values": {"wall": 3.0}})
    assert "error" not in edited, edited


# ================================================================= AC6 ========


def _fingerprint(bundle_dir: Path) -> tuple[dict, dict]:
    """(deterministic-class sha256 by path, step-class NORMALIZED sha256 by
    path) for a bundle directory — the FR11 comparison basis."""
    art = json.loads((bundle_dir / "artifacts.json").read_text())
    det, step = {}, {}
    for entry in art["files"]:
        raw = (bundle_dir / entry["path"]).read_bytes()
        if entry["class"] == "deterministic":
            det[entry["path"]] = _sha256(raw)
        elif entry["class"] == "step":
            step[entry["path"]] = _sha256(releases._normalize_step_bytes(raw))
    return det, step


@requires_git
def test_ac6_rebuilding_the_bundle_at_the_tag_is_reproducible(bundle_demo):
    """**AC6** — two bundle runs at the same tag produce identical
    ``artifacts.json`` hashes for every ``deterministic``-class artifact
    (drawings/BOM/flat patterns/README), and STEP files match after
    timestamp-line normalization.

    Cross-references ``tests/test_release_bundle.py`` (which grades the bundle
    mechanics); this grades the reproducibility criterion once, at the
    acceptance level, over the ``release_bundle`` tool.
    """
    service, registry = bundle_demo
    record = _finalize_A(service, registry)
    bundle_dir = Path(record["bundle"]["dir"])

    det1, step1 = _fingerprint(bundle_dir)
    assert det1, "no deterministic artifacts to compare"
    assert step1, "no step artifacts to compare"

    # Rebuild the bundle at the same tag (idempotent) — it overwrites the dir.
    out = registry.call("release_bundle", {"project": "demo", "rev": "A"})
    assert "error" not in out, out

    det2, step2 = _fingerprint(bundle_dir)
    assert det1 == det2                            # deterministic: byte-identical
    assert step1 == step2                          # STEP: identical after normalize


# ================================================================= AC7 ========


def test_ac7_a_three_config_flange_yields_three_lines_with_per_config_mass(
        kernel, tmp_path):
    """**AC7 (built half)** — a three-config flange yields three BOM lines
    carrying the three configs, each with a distinct per-config mass measured
    from that configuration's built shape.

    The distinct-part-number-per-config-suffix half is deferred (see the skipped
    placeholder below): slice 1 stores a single per-part ``bom`` field, so
    config-specific part numbers are not modelled yet.
    """
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("p")
    service.create_part("p", "flange", script=FLANGE_SCRIPT)
    registry.call("set_part_configs",
                  {"project": "p", "part_id": "flange",
                   "configs": THREE_SIZE_CONFIGS})
    service.set_assembly("p", [
        {"id": "fs", "part": "flange", "config": "s"},
        {"id": "fm", "part": "flange", "config": "m"},
        {"id": "fl", "part": "flange", "config": "l"},
    ])
    # Warm each configuration's build so the BOM peek finds per-config mass.
    for cfg in ("s", "m", "l"):
        service._ensure_config_built("p", "flange", cfg)

    result = registry.call("get_bom", {"project": "p"})
    assert "error" not in result, result
    lines = result["lines"]
    assert len(lines) == 3
    assert {ln["config"] for ln in lines} == {"s", "m", "l"}
    assert all(ln["mass_source"] == "built" for ln in lines)

    masses = {ln["config"]: ln["unit_mass_g"] for ln in lines}
    assert all(m is not None for m in masses.values())
    # A bigger flange masses more: s < m < l (per-config identity, not echoed).
    assert masses["s"] < masses["m"] < masses["l"]


@pytest.mark.skip(reason="per-config BOM part-number override deferred — "
                         "slice 1 stores one per-part bom field")
def test_ac7_distinct_part_numbers_per_config_suffix():
    """**AC7 (deferred half)** — distinct part numbers per the config suffix
    rule are on the record but not yet modelled: slice 1 stored a single
    per-part ``bom`` field (config-specific part numbers were a documented
    deferral). The per-config *line* identity + per-config mass is graded
    above; this placeholder keeps the deferral honest."""


# ================================================================= AC8 ========


def test_ac8_a_project_without_bom_or_releases_is_unchanged(kernel, tmp_path):
    """**AC8 (BOM half)** — a project that never set a ``bom`` field and never
    cut a release behaves exactly as before: ``get_bom`` still enumerates the
    assembly with blank part numbers, and no ``releases`` section appears in the
    manifest.
    """
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("plain")
    service.create_part("plain", "box", script=BOX_SCRIPT)
    service.set_assembly("plain", [{"id": "b", "part": "box"}])

    result = registry.call("get_bom", {"project": "plain"})
    assert "error" not in result, result
    assert len(result["lines"]) == 1
    line = result["lines"][0]
    assert line["part_id"] == "box"
    # No BOM field was ever set → a blank part number (None), not a fabrication.
    assert line["part_number"] in (None, "")

    # No release was cut → the manifest carries no releases section at all.
    manifest = service.store.manifest("plain")
    assert "releases" not in manifest


@requires_git
def test_ac8_an_ordinary_proposal_is_still_a_change(kernel, tmp_path):
    """**AC8 (proposal half)** — the ``release`` proposal kind is purely
    additive: an ordinary proposal on a project that cut no release is still
    ``kind: "change"``, and the manifest still carries no ``releases`` section.
    """
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    build_registry(service)
    service.create_project("plain")
    service.create_part("plain", "box", script=BOX_SCRIPT)
    service.branches.create("plain", "feat")

    _on(service, "plain", "chat:main", "master")
    created = service.proposals.create("plain", "feat", title="ordinary work")
    assert created["proposal"]["kind"] == "change"

    assert "releases" not in service.store.manifest("plain")
