"""PRD-013 Assembly v2 — slice 5: the machine-checkable half of the 1k-scale AC.

AC3 (>=30 fps orbiting 1000 instances) is a browser/manual criterion, graded as
evidence (the Chrome extension has been unavailable for many sessions). What IS
machine-checked here: a generated 1000-instance synthetic project resolves,
through the ONE expansion point, to exactly 1000 flat members — and the whole
pattern uploads as ONE instanced geometry (`len(groups)==1`), the property the
InstancedMesh path relies on.
"""

import pytest

from agentcad.core import mates
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service


@pytest.fixture
def thousand(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    build_registry(service)
    service.create_project("grid")
    service.create_part("grid", "cube", script=BOX_SCRIPT)
    # A linear pattern of 1000 — one part, 1000 members, no mates (so no shape
    # is built during expansion; the mesh is one cached build).
    service.set_assembly("grid", [{"id": "c", "part": "cube",
        "position": [0, 0, 0],
        "pattern": {"kind": "linear", "count": 1000, "step_mm": 12}}])
    return service


def test_thousand_instance_pattern_flattens_to_1000(thousand):
    flat, warns = mates.expand(
        thousand, "grid", thousand.store.instances("grid"))
    assert len(flat) == 1000                       # exactly N, replace-not-add
    assert flat[0].id == "c[0]" and flat[-1].id == "c[999]"
    assert "c" not in {i.id for i in flat}         # base absent
    assert warns == []


def test_thousand_instances_are_one_instanced_group(thousand):
    a = thousand.get_assembly("grid")
    assert len(a["instances"]) == 1000
    # every member shares one part + one mesh_key -> one geometry upload
    keys = {i["mesh_key"] for i in a["instances"]}
    assert len(keys) == 1
    parts = {i["part"] for i in a["instances"]}
    assert parts == {"cube"}
