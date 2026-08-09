"""Assembly mate tests: connectors, rigid/revolute resolution, chains, cycles."""

import pytest

from agentcad.core.model import ConflictError, ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

# A plate with a rigid connector 20mm up at its top center.
PLATE = '''\
from build123d import *

PARAMS = {"t": {"default": 10.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Box(40, 40, p.t)
    return part.part

def connectors(p, part):
    return {"top": {"type": "rigid", "location": ((0, 0, p.t / 2), (0, 0, 0))},
            "hinge": {"type": "revolute", "axis": ((0, 0, p.t / 2), (1, 0, 0))}}
'''

# A pin with a rigid connector at its base.
PIN = '''\
from build123d import *

PARAMS = {"h": {"default": 15.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Cylinder(radius=3, height=p.h, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part

def connectors(p, part):
    return {"base": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "plate", script=PLATE)
    service.create_part("demo", "pin", script=PIN)
    return service


def test_connectors_inspected(demo):
    result = demo.kernel.request("connectors", {"script": PLATE, "params": {}})
    assert set(result["connectors"]) == {"top", "hinge"}
    assert result["connectors"]["top"]["type"] == "rigid"


def test_rigid_mate_resolves_transform(demo):
    demo.set_assembly("demo", [
        {"id": "plate1", "part": "plate", "position": [0, 0, 0]},
        {"id": "pin1", "part": "pin", "position": [99, 99, 99]},  # will be overridden
    ])
    registry = build_registry(demo)
    result = registry.call("set_mate", {
        "project": "demo", "instance": "pin1", "connector": "base",
        "to_instance": "plate1", "to_connector": "top",
    })
    pin = next(i for i in result["instances"] if i["id"] == "pin1")
    # plate default t=10 -> top connector at z=5; pin base mates there
    assert pin["position"][0] == pytest.approx(0, abs=1e-6)
    assert pin["position"][1] == pytest.approx(0, abs=1e-6)
    assert pin["position"][2] == pytest.approx(5.0, abs=1e-6)


def test_mate_chain_resolves(demo):
    demo.create_part("demo", "pin2", script=PIN.replace("base", "base"))
    demo.set_assembly("demo", [
        {"id": "plate1", "part": "plate", "position": [0, 0, 0]},
        {"id": "pin1", "part": "pin"},
        {"id": "pin2", "part": "pin2"},
    ])
    registry = build_registry(demo)
    registry.call("set_mate", {"project": "demo", "instance": "pin1",
                               "connector": "base", "to_instance": "plate1",
                               "to_connector": "top"})
    # chain: pin2 -> pin1 -> plate1 (pin has only "base"; mate pin2.base to pin1.base)
    result = registry.call("set_mate", {"project": "demo", "instance": "pin2",
                                        "connector": "base", "to_instance": "pin1",
                                        "to_connector": "base"})
    assert "error" not in result
    pin2 = next(i for i in result["instances"] if i["id"] == "pin2")
    assert pin2["position"][2] == pytest.approx(5.0, abs=1e-6)  # same as pin1 base


def test_cycle_rejected(demo):
    with pytest.raises(ValidationError) as exc_info:
        # set_assembly resolves to return state, so the cycle is caught on write
        demo.set_assembly("demo", [
            {"id": "plate1", "part": "plate",
             "mate": {"connector": "top", "to_instance": "pin1", "to_connector": "base"}},
            {"id": "pin1", "part": "pin",
             "mate": {"connector": "base", "to_instance": "plate1", "to_connector": "top"}},
        ])
    assert "cycle" in str(exc_info.value).lower()


def test_patch_rejects_mate_driven_instance(demo):
    from fastapi.testclient import TestClient

    from agentcad.core.tools import build_registry as br
    from agentcad.server.app import create_app

    app = create_app(demo, br(demo), extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    demo.set_assembly("demo", [
        {"id": "plate1", "part": "plate", "position": [0, 0, 0]},
        {"id": "pin1", "part": "pin",
         "mate": {"connector": "base", "to_instance": "plate1", "to_connector": "top"}},
    ])
    # mate-driven -> 409
    r = client.patch("/api/projects/demo/assembly/instances/pin1",
                     json={"position": [1, 2, 3]})
    assert r.status_code == 409
    # free instance -> ok
    r = client.patch("/api/projects/demo/assembly/instances/plate1",
                     json={"position": [1, 2, 3]})
    assert r.status_code == 200


def test_mate_to_reference_part_clean_error(demo, kernel, tmp_path):
    # import a STEP reference part, then try to mate a script part to it
    step = tmp_path / "ref.step"
    kernel.request("export", {
        "script": 'PARAMS={"s":{"default":10.0}}\nfrom build123d import *\n'
                  'def build(p):\n    return Solid.make_box(10,10,10)\n',
        "params": {}, "format": "step", "out_path": str(step)})
    demo.create_part("demo", "imported", kind="reference", source="ref.step",
                     label="Imported")
    # copy the file into the project imports dir
    import shutil
    shutil.copy(step, demo.store.imports_dir("demo") / "ref.step")
    with pytest.raises(ValidationError) as exc_info:
        demo.set_assembly("demo", [
            {"id": "imp1", "part": "imported", "position": [0, 0, 0]},
            {"id": "pin1", "part": "pin",
             "mate": {"connector": "base", "to_instance": "imp1", "to_connector": "x"}},
        ])
    assert "connector" in str(exc_info.value).lower() or "reference" in str(exc_info.value).lower()


def test_dangling_mate_rejected_on_write(demo):
    # can't set an instance mated to a non-existent anchor
    with pytest.raises(ValidationError):
        demo.set_assembly("demo", [
            {"id": "pin1", "part": "pin",
             "mate": {"connector": "base", "to_instance": "ghost", "to_connector": "top"}},
        ])
