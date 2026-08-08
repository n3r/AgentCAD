import queue

import pytest

from agentcad.core.service import AgentCADService, EventBus

from .conftest import BOX_SCRIPT

BROKEN_SCRIPT = 'PARAMS = {"size": {"default": 1.0}}\ndef build(p):\n    raise RuntimeError("nope")\n'


@pytest.fixture
def service(kernel, tmp_path):
    return AgentCADService(tmp_path / "projects", kernel, EventBus())


@pytest.fixture
def demo(service):
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX_SCRIPT)
    return service


def test_create_project_and_part(demo):
    project = demo.get_project("demo")
    assert project["name"] == "demo"
    assert project["parts"][0]["id"] == "box"
    part = demo.get_part("demo", "box")
    assert part["status"]["state"] == "ok"
    assert part["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert part["params_spec"]["size"]["default"] == 10.0


def test_set_params_rebuilds_and_caches(demo, monkeypatch):
    calls = {"build": 0}
    original = demo.kernel.request

    def counting(method, params, timeout_s=None):
        if method == "build":
            calls["build"] += 1
        return original(method, params, timeout_s=timeout_s)

    monkeypatch.setattr(demo.kernel, "request", counting)

    result = demo.set_params("demo", "box", {"size": 20.0})
    assert result["ok"] is True
    assert result["metrics"]["volume_mm3"] == pytest.approx(8000.0, rel=1e-6)
    first_builds = calls["build"]

    result2 = demo.set_params("demo", "box", {"size": 20.0})
    assert result2["ok"] is True
    assert calls["build"] == first_builds  # cache hit, no extra kernel build


def test_broken_script_keeps_previous_mesh(demo):
    good_mesh = demo.ensure_mesh("demo", "box")
    assert good_mesh.is_file()

    result = demo.update_part("demo", "box", script=BROKEN_SCRIPT)
    assert result["ok"] is False
    assert result["error"]["type"] == "script_error"
    assert "nope" in result["error"]["message"]

    part = demo.get_part("demo", "box")
    assert part["status"]["state"] == "error"
    assert part["script"] == BROKEN_SCRIPT  # broken script is persisted
    assert good_mesh.is_file()  # previous cache entry untouched


def test_export_part(demo):
    result = demo.export_part("demo", "box", "step")
    assert result["size_bytes"] > 500
    assert result["path"].endswith("box.step")


def test_assembly_and_interference(demo):
    demo.set_assembly(
        "demo",
        [
            {"id": "a", "part": "box", "position": [0, 0, 0]},
            {"id": "b", "part": "box", "position": [5, 0, 0]},
        ],
    )
    assembly = demo.get_assembly("demo")
    assert assembly["total_mass_g"] == pytest.approx(2 * 1000 * 2.7 / 1000, rel=1e-3)
    assert assembly["bbox"]["max"][0] == pytest.approx(10.0, abs=0.01)

    result = demo.check_interference("demo")
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["volume_mm3"] == pytest.approx(500.0, rel=0.01)


def test_events_published(demo):
    q = demo.bus.subscribe()
    demo.set_params("demo", "box", {"size": 15.0})
    types = []
    while True:
        try:
            types.append(q.get_nowait()["type"])
        except queue.Empty:
            break
    assert "rebuild_started" in types
    assert "rebuild_finished" in types


LONG_BOX_SCRIPT = (
    "from build123d import *\n"
    'PARAMS = {"l": {"default": 20.0, "min": 1.0, "max": 100.0}}\n'
    "def build(p):\n"
    "    with BuildPart() as part:\n"
    "        Box(p.l, 4, 2)\n"
    "    return part.part\n"
)


def test_assembly_rollup_multi_axis_rotation_intrinsic_xyz(demo):
    # build123d Location((0,0,0),(90,0,90)) maps an X-elongated box onto Z
    # (intrinsic XYZ Euler); the bbox rollup must agree with the kernel.
    demo.create_part("demo", "beam", script=LONG_BOX_SCRIPT)
    demo.set_assembly(
        "demo",
        [{"id": "a", "part": "beam", "position": [0, 0, 0],
          "rotation_deg": [90, 0, 90]}],
    )
    assembly = demo.get_assembly("demo")
    extents = [
        assembly["bbox"]["max"][axis] - assembly["bbox"]["min"][axis]
        for axis in range(3)
    ]
    assert extents[2] == pytest.approx(20.0, abs=0.05)
    assert extents[0] == pytest.approx(4.0, abs=0.05)
    assert extents[1] == pytest.approx(2.0, abs=0.05)


def test_assembly_rollup_with_rotation(demo):
    # 10mm cube rotated 45 deg about Z: xy extent grows to 10*sqrt(2)
    demo.set_assembly(
        "demo",
        [{"id": "a", "part": "box", "position": [0, 0, 0], "rotation_deg": [0, 0, 45]}],
    )
    assembly = demo.get_assembly("demo")
    extent_x = assembly["bbox"]["max"][0] - assembly["bbox"]["min"][0]
    assert extent_x == pytest.approx(14.142, abs=0.05)
