"""geom_diff kernel handler: added/removed volume between two part versions.

Volume assertions are analytic (a drilled hole is pi*r^2*h), never taken from
``.volume`` on the boolean result — a difference is routinely a nested
Compound, whose ``.volume`` undercounts. Mesh-kind (STL) sides are asserted to
be skipped rather than booleaned: an OCCT boolean on a welded mesh Face
segfaults the worker, so the last assertion of that test is that the kernel is
still answering.
"""

from __future__ import annotations

import math

import pytest

from agentcad.kernel import acm
from agentcad.kernel.client import KernelError

from .conftest import BOX_SCRIPT

pytestmark = pytest.mark.portability

CUBE = '''\
from build123d import *
PARAMS = {"s": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm",
                "description": "cube edge"}}
def build(p):
    return Box(p.s, p.s, p.s)
'''

CUBE_HOLE = '''\
from build123d import *
PARAMS = {"s": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm",
                "description": "cube edge"},
          "d": {"default": 6.0, "min": 1.0, "max": 10.0, "unit": "mm",
                "description": "hole diameter"}}
def build(p):
    return Box(p.s, p.s, p.s) - Cylinder(p.d / 2, p.s * 2)
'''

# Two disjoint 10 mm cubes; ``hole`` drills a 4 mm bore through the second.
TWO_CUBES = '''\
from build123d import *
PARAMS = {"hole": {"default": False, "type": "bool", "description": "bore the second cube"}}
def build(p):
    a = Solid.make_box(10, 10, 10)
    b = Solid.make_box(10, 10, 10).moved(Location((20, 0, 0)))
    if p.hole:
        b = b - Solid.make_cylinder(2, 30).moved(Location((25, 5, -10)))
    return Compound(children=[a, b])
'''

# Builds (an empty Compound is a legal build(p) return) but cannot be
# subtracted from: OCCT has nothing to cut.
EMPTY = '''\
from build123d import *
PARAMS = {}
def build(p):
    return Compound(children=[])
'''

HOLE_MM3 = math.pi * 3.0 ** 2 * 20.0


def diff(kernel, old, new, **params):
    return kernel.request("geom_diff", {"old": old, "new": new, **params})


def test_drilled_hole_reports_removed_volume(kernel):
    result = diff(kernel, {"script": CUBE, "params": {}},
                  {"script": CUBE_HOLE, "params": {}})
    assert result["removed_mm3"] == pytest.approx(HOLE_MM3, rel=0.01)
    assert result["added_mm3"] == 0.0
    assert result["old_volume_mm3"] == pytest.approx(8000.0, rel=1e-6)
    assert result["new_volume_mm3"] == pytest.approx(8000.0 - HOLE_MM3, rel=0.01)
    assert "skipped_mesh" not in result


def test_filled_hole_reports_added_volume(kernel):
    result = diff(kernel, {"script": CUBE_HOLE, "params": {}},
                  {"script": CUBE, "params": {}})
    assert result["added_mm3"] == pytest.approx(HOLE_MM3, rel=0.01)
    assert result["removed_mm3"] == 0.0


def test_identical_inputs_are_zero_and_write_no_mesh(kernel, tmp_path):
    added, removed = tmp_path / "a.acm", tmp_path / "r.acm"
    result = diff(kernel, {"script": CUBE, "params": {}},
                  {"script": CUBE, "params": {}},
                  added_path=str(added), removed_path=str(removed))
    assert (result["added_mm3"], result["removed_mm3"]) == (0.0, 0.0)
    assert (result["added_triangles"], result["removed_triangles"]) == (0, 0)
    assert not added.exists() and not removed.exists()


def test_multi_solid_diff_sums_solid_volumes(kernel):
    result = diff(kernel, {"script": TWO_CUBES, "params": {"hole": False}},
                  {"script": TWO_CUBES, "params": {"hole": True}})
    bore = math.pi * 2.0 ** 2 * 10.0
    assert result["old_volume_mm3"] == pytest.approx(2000.0, rel=1e-6)
    assert result["new_volume_mm3"] == pytest.approx(2000.0 - bore, rel=0.01)
    assert result["removed_mm3"] == pytest.approx(bore, rel=0.01)
    assert result["added_mm3"] == 0.0


def test_absent_side_counts_as_whole_volume(kernel):
    added = diff(kernel, None, {"script": CUBE, "params": {}})
    assert added["added_mm3"] == pytest.approx(8000.0, rel=1e-6)
    assert added["removed_mm3"] == 0.0
    assert added["old_volume_mm3"] == 0.0

    removed = diff(kernel, {"script": CUBE, "params": {}}, None)
    assert removed["removed_mm3"] == pytest.approx(8000.0, rel=1e-6)
    assert removed["added_mm3"] == 0.0

    assert diff(kernel, None, None) == {
        "added_mm3": 0.0, "removed_mm3": 0.0,
        "old_volume_mm3": 0.0, "new_volume_mm3": 0.0,
        "added_triangles": 0, "removed_triangles": 0,
    }


def test_mesh_side_is_skipped_not_booleaned(kernel, tmp_path):
    stl = tmp_path / "blob.stl"
    kernel.request("export", {"script": BOX_SCRIPT, "params": {}, "format": "stl",
                              "out_path": str(stl)})
    added, removed = tmp_path / "a.acm", tmp_path / "r.acm"
    result = diff(kernel, {"source": str(stl)}, {"script": CUBE, "params": {}},
                  added_path=str(added), removed_path=str(removed))
    assert result["skipped_mesh"] == ["old"]
    assert (result["added_mm3"], result["removed_mm3"]) == (0.0, 0.0)
    assert not added.exists() and not removed.exists()

    other = diff(kernel, {"script": CUBE, "params": {}}, {"source": str(stl)})
    assert other["skipped_mesh"] == ["new"]

    # the STL never reached a boolean, so the worker is still alive
    assert kernel.request("ping", {})["ok"] is True


def test_boolean_failure_is_a_structured_kernel_error(kernel):
    with pytest.raises(KernelError) as excinfo:
        diff(kernel, {"script": EMPTY, "params": {}},
             {"script": CUBE, "params": {}})
    assert excinfo.value.details["stage"] in {"added", "removed"}
    assert "geometric diff unavailable" in excinfo.value.message
    assert kernel.request("ping", {})["ok"] is True


def test_diff_meshes_are_written_and_parse(kernel, tmp_path):
    added, removed = tmp_path / "a.acm", tmp_path / "r.acm"
    result = diff(kernel, {"script": CUBE, "params": {}},
                  {"script": CUBE_HOLE, "params": {}},
                  added_path=str(added), removed_path=str(removed))
    assert not added.exists()
    assert result["added_triangles"] == 0
    assert removed.read_bytes()[:4] == b"ACM1"
    mesh = acm.read(removed)
    assert len(mesh["indices"]) == result["removed_triangles"] > 0
