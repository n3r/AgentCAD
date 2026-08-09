"""Per-solid part semantics: SOLID_LABELS, metrics.solids, solid_materials."""

import hashlib
import json

import pytest

from agentcad.core.materials import MATERIALS
from agentcad.core.service import MESH_TOLERANCE, AgentCADService, EventBus
from agentcad.core.tools import build_registry

AL = MATERIALS["al6061"].density_g_cm3
STEEL = MATERIALS["steel_a36"].density_g_cm3

TWO_BOX = '''\
import build123d as b3d
PARAMS = {"size": {"default": 10.0, "min": 5.0, "max": 20.0, "unit": "mm", "description": "box edge"}}
SOLID_LABELS = ["body", "lid"]
def build(p):
    a = b3d.Box(p.size, p.size, p.size)
    b = b3d.Box(p.size, p.size, p.size).moved(b3d.Location((p.size * 2, 0, 0)))
    return b3d.Compound(children=[a, b])
'''

ONE_BOX = '''\
import build123d as b3d
PARAMS = {"size": {"default": 10.0, "min": 5.0, "max": 20.0, "unit": "mm"}}
def build(p):
    return b3d.Box(p.size, p.size, p.size)
'''

BAD_LABELS = '''\
import build123d as b3d
PARAMS = {"size": {"default": 10.0}}
SOLID_LABELS = "body"
def build(p):
    return b3d.Box(p.size, p.size, p.size)
'''

EXTRA_LABELS = TWO_BOX.replace(
    'SOLID_LABELS = ["body", "lid"]',
    'SOLID_LABELS = ["body", "lid", "hinge"]',
)


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "duo", script=TWO_BOX)
    return service


def test_per_solid_metrics_with_labels(demo):
    metrics = demo.get_metrics("demo", "duo")
    solids = metrics["solids"]
    assert [s["label"] for s in solids] == ["body", "lid"]
    for s in solids:
        assert s["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
        assert s["mass_g"] == pytest.approx(1000.0 * AL / 1000.0)
        assert s["bbox"]["min"] and s["bbox"]["max"] and s["center_of_mass"]
    assert solids[0]["mass_g"] == pytest.approx(solids[1]["mass_g"])
    assert metrics["mass_g"] == pytest.approx(sum(s["mass_g"] for s in solids))
    # the two boxes are disjoint along X, so their bboxes must differ
    assert solids[0]["bbox"]["min"][0] != solids[1]["bbox"]["min"][0]


def test_single_solid_has_no_solids_key(demo):
    demo.create_part("demo", "mono", script=ONE_BOX)
    metrics = demo.get_metrics("demo", "mono")
    assert "solids" not in metrics
    assert metrics["mass_g"] == pytest.approx(1000.0 * AL / 1000.0)


def test_set_solid_materials_changes_mass_and_manifest(demo, tmp_path):
    registry = build_registry(demo)
    result = registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo",
        "materials": {"lid": "steel_a36"},
    })
    assert "error" not in result, result
    assert result["ok"] is True
    assert result["solid_materials"] == {"lid": "steel_a36"}
    solids = {s["label"]: s for s in result["metrics"]["solids"]}
    assert solids["lid"]["mass_g"] == pytest.approx(1000.0 * STEEL / 1000.0)
    assert solids["body"]["mass_g"] == pytest.approx(1000.0 * AL / 1000.0)
    assert result["metrics"]["mass_g"] == pytest.approx(
        solids["lid"]["mass_g"] + solids["body"]["mass_g"]
    )
    # persisted on disk
    manifest = json.loads(
        (tmp_path / "projects" / "demo" / "project.json").read_text()
    )
    entry = next(p for p in manifest["parts"] if p["id"] == "duo")
    assert entry["solid_materials"] == {"lid": "steel_a36"}
    # surfaced by get_part
    assert demo.get_part("demo", "duo")["solid_materials"] == {"lid": "steel_a36"}


def test_set_solid_materials_by_index(demo):
    registry = build_registry(demo)
    result = registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo",
        "materials": {"0": "steel_a36"},
    })
    assert "error" not in result, result
    solids = {s["label"]: s for s in result["metrics"]["solids"]}
    assert solids["body"]["mass_g"] == pytest.approx(1000.0 * STEEL / 1000.0)
    assert solids["lid"]["mass_g"] == pytest.approx(1000.0 * AL / 1000.0)


def test_empty_dict_clears_solid_materials(demo, tmp_path):
    registry = build_registry(demo)
    registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo", "materials": {"lid": "steel_a36"}})
    result = registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo", "materials": {}})
    assert "error" not in result, result
    assert result["solid_materials"] is None
    assert result["metrics"]["mass_g"] == pytest.approx(2 * 1000.0 * AL / 1000.0)
    manifest = json.loads(
        (tmp_path / "projects" / "demo" / "project.json").read_text()
    )
    entry = next(p for p in manifest["parts"] if p["id"] == "duo")
    assert "solid_materials" not in entry


def test_unknown_material_rejected(demo):
    registry = build_registry(demo)
    result = registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo",
        "materials": {"lid": "unobtainium"},
    })
    assert result["error"]["type"] == "validation_error"
    assert "unobtainium" in result["error"]["message"]
    # nothing was persisted
    assert demo.store.get_part("demo", "duo").solid_materials is None


def test_unknown_part_rejected(demo):
    registry = build_registry(demo)
    result = registry.call("set_solid_materials", {
        "project": "demo", "part_id": "nope", "materials": {}})
    assert result["error"]["type"] == "notfound_error"


def test_unmatched_key_builds_with_warning(demo):
    registry = build_registry(demo)
    result = registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo",
        "materials": {"hinge": "steel_a36"},
    })
    assert "error" not in result, result
    assert result["ok"] is True
    assert any("hinge" in w for w in result["warnings"])
    # unmatched key changes no mass
    assert result["metrics"]["mass_g"] == pytest.approx(2 * 1000.0 * AL / 1000.0)


def test_cache_key_changes_with_solid_materials(demo):
    registry = build_registry(demo)
    demo.get_metrics("demo", "duo")
    key_before = demo._status[("demo", "duo")]["cache_key"]
    result = registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo",
        "materials": {"lid": "steel_a36"},
    })
    assert "error" not in result, result
    key_after = demo._status[("demo", "duo")]["cache_key"]
    assert key_before != key_after
    # not served from the stale cache: mass reflects the new density
    metrics = demo.get_metrics("demo", "duo")
    assert metrics["mass_g"] == pytest.approx(1000.0 * (AL + STEEL) / 1000.0)


def test_cache_key_unchanged_without_solid_materials(demo):
    # Pin the exact pre-feature cache-key bytes: a part with no solid_materials
    # must keep the identical key, so existing on-disk caches stay valid.
    record = demo.store.get_part("demo", "duo")
    content = demo._content_signature("demo", record)
    density = demo.material_density("demo", record.material)
    payload = json.dumps(
        {
            "content": content,
            "params": {},
            "density": density,
            "tolerance": MESH_TOLERANCE,
            "format": "acm1",
        },
        sort_keys=True,
    )
    expected = hashlib.sha256(payload.encode()).hexdigest()[:32]
    assert demo._cache_key_for("demo", record) == expected


def test_bad_solid_labels_is_contract_error(demo):
    detail = demo.create_part("demo", "bad", script=BAD_LABELS)
    assert detail["status"]["state"] == "error"
    assert detail["status"]["error"]["type"] == "contract_error"
    assert "SOLID_LABELS" in detail["status"]["error"]["message"]


def test_extra_labels_ignored_with_warning(demo):
    detail = demo.create_part("demo", "extra", script=EXTRA_LABELS)
    assert detail["status"]["state"] == "ok"
    assert any("SOLID_LABELS" in w for w in detail["status"]["warnings"])
    labels = [s["label"] for s in detail["metrics"]["solids"]]
    assert labels == ["body", "lid"]


def test_unlabeled_multi_solid_gets_fallback_labels(demo):
    script = TWO_BOX.replace('SOLID_LABELS = ["body", "lid"]\n', "")
    demo.create_part("demo", "plain", script=script)
    metrics = demo.get_metrics("demo", "plain")
    assert [s["label"] for s in metrics["solids"]] == ["solid_0", "solid_1"]


def test_reference_part_rejected(demo, kernel, tmp_path):
    # set_solid_materials targets script parts; a reference part is rejected.
    step = tmp_path / "widget.step"
    kernel.request("export", {"script": ONE_BOX, "params": {}, "format": "step",
                              "out_path": str(step)})
    registry = build_registry(demo)
    imported = registry.call("import_cad_file", {
        "project": "demo", "source": str(step), "part_id": "widget"})
    assert "error" not in imported, imported
    result = registry.call("set_solid_materials", {
        "project": "demo", "part_id": "widget", "materials": {"0": "steel_a36"}})
    assert result["error"]["type"] == "validation_error"
