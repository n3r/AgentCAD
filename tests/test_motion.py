"""Motion-from-mates tests: sweep a revolute DOF, detect the collision angle.

Scenario (all analytic, verified against build123d joint math):

  * ``base``  — Box(40, 40, 10) centered at the origin with a revolute
    connector "hinge" whose axis is ((0, 0, 6), (0, 0, 1)): a vertical hinge
    1 mm above the plate top (plate top is z = 5).
  * ``flap``  — Box(30, 4, 4) with a rigid connector "root" at its -X end
    bottom, ((-15, 0, -2), (0, 0, 0)). Mated root->hinge, at angle 0 the flap
    occupies x in [0, 30], y in [-2, 2], z in [6, 10] (build123d derives the
    zero-angle reference from Plane(z_dir=axis).x_dir, which for a +Z axis is
    world +X) and sweeps in the XY plane like a clock hand.
  * ``wall``  — Box(80, 10, 20) placed at (0, 27, 0): x in [-40, 40],
    y in [22, 32], z in [-10, 10]. Its near face is the plane y = 22, wide
    enough that first contact is always on that face.

  First contact: the flap corner (u=30, v=2) reaches y = 22 when
  30*sin(t) + 2*cos(t) = 22, i.e. t* = asin(22/sqrt(904)) - atan2(2, 30)
  = 43.216 deg. Sweeping [0, 90] with 10 samples (step 10 deg): samples
  0..40 are clear, 50..90 collide, first_collision == 50.
"""

import math

import pytest

from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry
from agentcad.kernel.client import KernelError

from .conftest import clone_test_service, make_test_service

BASE = '''\
from build123d import *

PARAMS = {"t": {"default": 10.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Box(40, 40, p.t)
    return part.part

def connectors(p, part):
    return {"hinge": {"type": "revolute", "axis": ((0, 0, p.t / 2 + 1), (0, 0, 1))}}
'''

FLAP = '''\
from build123d import *

PARAMS = {"l": {"default": 30.0, "min": 5.0, "max": 100.0}}

def build(p):
    with BuildPart() as part:
        Box(p.l, 4, 4)
    return part.part

def connectors(p, part):
    return {"root": {"type": "rigid", "location": ((-p.l / 2, 0, -2), (0, 0, 0))}}
'''

WALL = '''\
from build123d import *

PARAMS = {"w": {"default": 80.0, "min": 10.0, "max": 200.0}}

def build(p):
    with BuildPart() as part:
        Box(p.w, 10, 20)
    return part.part
'''

# analytic first-contact angle (see module docstring)
THETA_STAR = math.degrees(math.asin(22 / math.hypot(30, 2))) - math.degrees(
    math.atan2(2, 30)
)


@pytest.fixture(scope="module")
def motion_projects(kernel, tmp_path_factory):
    projects = tmp_path_factory.mktemp("motion_projects")
    service = make_test_service(projects, kernel)
    service.create_project("motion")
    service.create_part("motion", "base", script=BASE)
    service.create_part("motion", "flap", script=FLAP)
    service.create_part("motion", "wall", script=WALL)
    service.set_assembly("motion", [
        {"id": "base1", "part": "base", "position": [0, 0, 0]},
        {"id": "flap1", "part": "flap",
         "mate": {"connector": "root", "to_instance": "base1",
                  "to_connector": "hinge", "params": {"angle": 0.0}}},
        {"id": "wall1", "part": "wall", "position": [0, 27, 0]},
    ])
    return projects


@pytest.fixture
def demo(kernel, tmp_path, motion_projects):
    return clone_test_service(motion_projects, tmp_path / "projects", kernel)


@pytest.fixture
def registry(demo):
    return build_registry(demo)


def test_theta_star_is_where_we_think():
    # the sweep grid must bracket theta* with real margin on both sides
    assert 40 < THETA_STAR < 50
    assert min(THETA_STAR - 40, 50 - THETA_STAR) > 2.0


def test_sweep_finds_collision_angle(registry):
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "flap1",
        "angle_range": [0, 90], "samples": 10,
    })
    assert "error" not in result
    assert result["instance"] == "flap1"
    assert result["param"] == "angle"
    assert result["values"] == pytest.approx([10.0 * i for i in range(10)])
    assert len(result["samples"]) == 10
    for sample in result["samples"]:
        v = sample["value"]
        if v < THETA_STAR:
            assert sample["pairs"] == [], f"unexpected overlap at {v}"
        else:
            assert sample["pairs"], f"expected overlap at {v}"
            pair = sample["pairs"][0]
            assert {pair["a"], pair["b"]} == {"flap1", "wall1"}
            assert pair["volume_mm3"] > 0.001
    assert result["clear"] is False
    assert result["first_collision"] == pytest.approx(50.0)
    assert result["skipped_mesh"] == []


def test_sweep_frames_move_the_flap_only(registry):
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "flap1",
        "angle_range": [0, 90], "samples": 10,
    })
    assert "error" not in result
    frames = result["frames"]
    assert len(frames) == 10
    # every frame carries every instance
    for frame in frames:
        assert set(frame) == {"base1", "flap1", "wall1"}
        assert set(frame["flap1"]) == {"position", "rotation_deg"}
    # flap rotation about Z follows the driven angle, strictly increasing
    rz = [frame["flap1"]["rotation_deg"][2] for frame in frames]
    for i, angle in enumerate(result["values"]):
        assert rz[i] == pytest.approx(angle, abs=1e-6)
    assert all(b > a for a, b in zip(rz, rz[1:]))
    # the flap's position moves too (it orbits the hinge)
    assert frames[0]["flap1"]["position"] != pytest.approx(
        frames[-1]["flap1"]["position"]
    )
    # static instances are identical across all frames
    for iid in ("base1", "wall1"):
        first = frames[0][iid]
        for frame in frames[1:]:
            assert frame[iid]["position"] == pytest.approx(first["position"])
            assert frame[iid]["rotation_deg"] == pytest.approx(
                first["rotation_deg"]
            )


def test_sweep_entirely_below_theta_star_is_clear(registry):
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "flap1",
        "angle_range": [0, 30], "samples": 4,
    })
    assert "error" not in result
    assert result["clear"] is True
    assert result["first_collision"] is None
    assert all(s["pairs"] == [] for s in result["samples"])


def test_unmated_instance_rejected(registry):
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "base1",
        "angle_range": [0, 90], "samples": 4,
    })
    assert result["error"]["type"] == "validation_error"
    assert "mate" in result["error"]["message"]


def test_unknown_instance_rejected(registry):
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "ghost",
        "angle_range": [0, 90], "samples": 4,
    })
    assert result["error"]["type"] == "notfound_error"


def test_samples_out_of_range_rejected(registry):
    for samples in (1, 61):
        result = registry.call("sweep_motion", {
            "project": "motion", "instance": "flap1",
            "angle_range": [0, 90], "samples": samples,
        })
        assert result["error"]["type"] == "validation_error"
        assert "samples" in result["error"]["message"]


def test_exactly_one_range_required(registry):
    # neither
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "flap1",
    })
    assert result["error"]["type"] == "validation_error"
    # both
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "flap1",
        "angle_range": [0, 90], "offset_range": [0, 5],
    })
    assert result["error"]["type"] == "validation_error"
    # malformed range
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "flap1", "angle_range": [0],
    })
    assert result["error"]["type"] == "validation_error"


def test_kernel_rejects_unmated_driven_and_value_flood(demo):
    items = [
        {"id": "base1", "script": BASE, "params": {},
         "position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
    ]
    with pytest.raises(KernelError) as exc_info:
        demo.kernel.request("motion_sweep", {
            "items": items,
            "driven": {"instance": "base1", "param": "angle", "values": [0, 10]},
        })
    assert exc_info.value.type == "contract_error"
    assert "mate" in exc_info.value.message

    with pytest.raises(KernelError) as exc_info:
        demo.kernel.request("motion_sweep", {
            "items": items,
            "driven": {"instance": "base1", "param": "angle",
                       "values": list(range(61))},
        })
    assert exc_info.value.type == "contract_error"
    assert "60" in exc_info.value.message


def test_mesh_reference_instance_lands_in_skipped_mesh(demo, kernel, tmp_path):
    # export a small STL, import it as a reference part, park it far away
    stl = tmp_path / "ref.stl"
    kernel.request("export", {
        "script": 'PARAMS={"s":{"default":10.0}}\nfrom build123d import *\n'
                  'def build(p):\n    return Solid.make_box(10,10,10)\n',
        "params": {}, "format": "stl", "out_path": str(stl)})
    demo.create_part("motion", "imported", kind="reference", source="ref.stl",
                     label="Imported")
    import shutil
    shutil.copy(stl, demo.store.imports_dir("motion") / "ref.stl")
    instances = [i.to_manifest() for i in demo.store.instances("motion")]
    instances.append({"id": "ref1", "part": "imported",
                      "position": [200, 200, 200]})
    demo.set_assembly("motion", instances)

    registry = build_registry(demo)
    result = registry.call("sweep_motion", {
        "project": "motion", "instance": "flap1",
        "angle_range": [0, 90], "samples": 4,
    })
    assert "error" not in result
    assert result["skipped_mesh"] == ["ref1"]
    # the mesh instance still appears in every frame (it is placeable)
    for frame in result["frames"]:
        assert frame["ref1"]["position"] == pytest.approx([200, 200, 200])
