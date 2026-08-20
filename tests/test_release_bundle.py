"""The reproducible release bundle (PRD-015 slice 5, FR10-11).

``release_finalize`` — after it tags ``release/<rev>`` and transitions the
record — builds a reproducible **bundle** at the tag: STEP per part + the
assembly, PDF+SVG drawings per script part (title block pinned via the PRD-014
``version`` override), ``bom.csv``/``bom.json``, a flat pattern for every
sheet-metal part, a ``README.md`` and an ``artifacts.json`` (sha256 per file).
The files are produced in a throwaway worktree materialized at the tag and
copied out into the real project's ``exports/releases/<rev>/`` (plus a
``<rev>.zip`` beside it) before the worktree is torn down.

**Reproducibility (FR11):** rebuilding the bundle at the same tag
(``release_bundle`` — idempotent) yields identical sha256 for every
``deterministic``-class artifact; STEP files match after their ``FILE_NAME``
timestamp header line is normalized (the one normalized-comparison class, named
in the README).

Real kernel work (STEP/drawing/flat builds against a cold cache), so this is a
slow integration test; skips without git.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from agentcad.core import locks, releases
from agentcad.core.branches import pinned_tree_var
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]
pytestmark = _GIT + [pytest.mark.slow]


# A solid box with specs that pass at the defaults (green gate). Not sheet
# metal, so its flat pattern is skipped by the bundle (and noted).
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

# A sheet-metal bracket — defines flat_pattern(p), so the bundle produces its
# flat pattern.
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
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def demo(kernel, tmp_path):
    """'demo' with a solid part + a sheet-metal part + a 2-instance assembly on
    master, and a 'rel' release branch checked out."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
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
                      {"id": "k1", "part": "bracket",
                       "position": [80, 0, 0]}]})
    service.branches.create("demo", "rel")
    return service, registry


def _on_rel(service):
    """Switch the acting client (agent_a) onto the release branch — branch
    'current' is per-client, so this mirrors the finalize suite's helper."""
    locks.set_client_id("agent_a")
    if service.branches.current("demo") != "rel":
        service.branches.switch("demo", "rel")


def _approve(registry, pid, reviewer="agent_b"):
    prev = locks.current_client_id()
    locks.set_client_id(reviewer)
    try:
        res = registry.call("proposal_review",
                            {"project": "demo", "id": pid, "verdict": "approve"})
        assert "error" not in res, res
    finally:
        locks.set_client_id(prev)


def _finalize(service, registry):
    """Cut → approve → finalize release A; return the finalized record."""
    _on_rel(service)
    started = releases.release_start(service, "demo")
    assert started["gate"]["status"] == "green", started["gate"]
    _approve(registry, started["proposal"])
    _on_rel(service)
    return releases.release_finalize(service, "demo", "A")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifacts(bundle_dir: Path) -> dict:
    return json.loads((bundle_dir / "artifacts.json").read_text())


def _fingerprint(bundle_dir: Path) -> tuple[dict, dict]:
    """(deterministic-class sha256 by path, step-class NORMALIZED sha256 by
    path) for a bundle directory — the FR11 comparison basis."""
    art = _artifacts(bundle_dir)
    det, step = {}, {}
    for entry in art["files"]:
        raw = (bundle_dir / entry["path"]).read_bytes()
        if entry["class"] == "deterministic":
            det[entry["path"]] = _sha256(raw)
        elif entry["class"] == "step":
            step[entry["path"]] = _sha256(releases._normalize_step_bytes(raw))
    return det, step


# ------------------------------------------------------------- 1. the bundle


def test_finalize_builds_the_bundle_with_every_expected_artifact(demo):
    service, registry = demo
    record = _finalize(service, registry)

    bundle = record.get("bundle")
    assert bundle, "finalize did not build a bundle inline"
    bundle_dir = Path(bundle["dir"])
    assert bundle_dir.is_dir()
    assert bundle_dir.name == "A"
    assert bundle_dir.parent.name == "releases"

    names = {p.name for p in bundle_dir.iterdir()}
    # STEP per part + assembly.
    assert {"box.step", "bracket.step", "assembly.step"} <= names
    # Drawings (pdf + svg) per script part.
    assert {"box_drawing.pdf", "box_drawing.svg",
            "bracket_drawing.pdf", "bracket_drawing.svg"} <= names
    # BOM both formats.
    assert {"bom.csv", "bom.json"} <= names
    # README + artifacts manifest.
    assert {"README.md", "artifacts.json"} <= names

    # The zip sits beside the directory.
    zip_path = Path(bundle["zip"])
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        assert any(n.endswith("README.md") for n in zf.namelist())


# --------------------------------------------- 2. artifacts.json is honest


def test_artifacts_json_lists_every_file_with_a_matching_sha256(demo):
    service, registry = demo
    record = _finalize(service, registry)
    bundle_dir = Path(record["bundle"]["dir"])

    art = _artifacts(bundle_dir)
    assert art["files"], "no files recorded"
    # artifacts.json never lists itself.
    listed = {e["path"] for e in art["files"]}
    assert "artifacts.json" not in listed

    for entry in art["files"]:
        target = bundle_dir / entry["path"]
        assert target.is_file(), entry["path"]
        raw = target.read_bytes()
        assert entry["sha256"] == _sha256(raw), entry["path"]
        assert entry["bytes"] == len(raw), entry["path"]
        assert entry["class"] in ("deterministic", "step")

    # STEP files are the step class; drawings/bom/README are deterministic.
    by_path = {e["path"]: e["class"] for e in art["files"]}
    assert by_path["box.step"] == "step"
    assert by_path["assembly.step"] == "step"
    assert by_path["bom.csv"] == "deterministic"
    assert by_path["README.md"] == "deterministic"


# ------------------------------------------ 3. flat pattern only for sheetmetal


def test_flat_pattern_only_for_sheetmetal_parts_and_solids_noted(demo):
    service, registry = demo
    record = _finalize(service, registry)
    bundle_dir = Path(record["bundle"]["dir"])

    names = {p.name for p in bundle_dir.iterdir()}
    assert "bracket_flat.svg" in names          # the sheet-metal part
    assert "box_flat.svg" not in names          # the solid part is skipped

    # The skip is noted (honesty), in the README and the bundle summary.
    readme = (bundle_dir / "README.md").read_text()
    assert "box" in readme
    assert any("box" in str(s) for s in (record["bundle"].get("skipped") or []))


# ------------------------------------------------- 4. README carries the facts


def test_readme_contains_release_name_and_gate_report(demo):
    service, registry = demo
    record = _finalize(service, registry)
    bundle_dir = Path(record["bundle"]["dir"])
    readme = (bundle_dir / "README.md").read_text()

    assert "Release A" in readme                # the release name
    # The gate report (its checks) is embedded.
    assert "working_tree_clean" in readme or "specs" in readme
    assert "green" in readme.lower()
    # The STEP normalization is documented (the one normalized class).
    assert "FILE_NAME" in readme or "normaliz" in readme.lower()


# ----------------------------------------------- 5. AC6 reproducibility (FR11)


def test_rebuilding_the_bundle_at_the_tag_is_reproducible(demo):
    service, registry = demo
    record = _finalize(service, registry)
    bundle_dir = Path(record["bundle"]["dir"])

    det1, step1 = _fingerprint(bundle_dir)
    assert det1, "no deterministic artifacts to compare"
    assert step1, "no step artifacts to compare"

    # Rebuild the bundle at the same tag (idempotent) — it overwrites the dir.
    out = registry.call("release_bundle", {"project": "demo", "rev": "A"})
    assert "error" not in out, out

    det2, step2 = _fingerprint(bundle_dir)

    # Every deterministic-class artifact is byte-identical across the two runs.
    assert det1 == det2
    # STEP files match after timestamp-line normalization.
    assert step1 == step2

    # release_bundle returns a summary naming the dir/zip/artifacts.
    assert Path(out["dir"]) == bundle_dir
    assert Path(out["zip"]).is_file()
