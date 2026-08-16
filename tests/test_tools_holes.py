"""`add_holes` — the hole-on-face script edit (PRD-010 FR14, slice 13).

The contract under test is the `tools_facemod.push_pull` one: the tool
*appends source*, so everything it writes has to be either a validated table
key or a number, the block has to be syntactically valid Python that rebuilds,
and two calls must compose rather than shadow each other.

The security half is not decoration. A crafted identifier interpolated into a
generated script put `import os` on line 2 of one once (PRD-009's last gotcha,
`sketch_emit._plane_header`), so every string that reaches the output here is
a key into a table this module owns, and every number goes through
`repr(float(...))`.
"""

from __future__ import annotations

import pytest

from agentcad.core.tools import build_registry
from agentcad.core.tools_holes import (
    ADD_HOLES_MARKER,
    NAMED_PLANES,
    _FAMILIES,
)

from .conftest import make_test_service

PLATE_SCRIPT = '''\
from build123d import *

PARAMS = {"t": {"default": 10.0, "min": 2.0, "max": 40.0, "unit": "mm"}}

def build(p):
    with BuildPart() as part:
        Box(80, 60, p.t)
    return part.part
'''

CYL_SCRIPT = '''\
from build123d import *

PARAMS = {"r": {"default": 12.0, "min": 1.0, "max": 50.0, "unit": "mm"}}

def build(p):
    return Solid.make_cylinder(p.r, 20)
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "plate", script=PLATE_SCRIPT)
    return service


def _call(service, **params):
    return build_registry(service).call("add_holes",
                                        {"project": "demo", **params})


def _top_face(service, part_id="plate"):
    """The +Z planar face ordinal, found the way the GUI finds it."""
    registry = build_registry(service)
    first = registry.call("face_info", {"project": "demo", "part_id": part_id,
                                        "face_index": 0})
    assert "error" not in first, first
    best, best_z = None, None
    for i in range(first["n_faces"]):
        info = first if i == 0 else registry.call(
            "face_info", {"project": "demo", "part_id": part_id,
                          "face_index": i})
        if not info["planar"] or info["normal"][2] < 0.999:
            continue
        if best is None or info["center"][2] > best_z:
            best, best_z = i, info["center"][2]
    assert best is not None
    return best


# ------------------------------------------------------- the appended block

def test_the_appended_block_is_valid_python_and_rebuilds(demo):
    before = demo.get_metrics("demo", "plate")["volume_mm3"]
    res = _call(demo, part_id="plate", points=[[20, 10], [-20, 10]],
                family="clearance", size="M5")
    assert res.get("ok") is True, res
    assert res["count"] == 2 and res["size"] == "M5" and res["fit"] == "medium"

    script = demo.store.read_script("demo", "plate")
    assert script.count(ADD_HOLES_MARKER) == 1
    compile(script, "plate.py", "exec")          # syntactically valid Python
    assert "holes.clearance(" in script
    assert "_agentcad_prev_build_0" in script

    # ISO 273 medium for M5 is 5.5 mm; two through holes in 10 mm of stock.
    removed = before - res["metrics"]["volume_mm3"]
    assert removed == pytest.approx(2 * 3.14159265 * 2.75 ** 2 * 10, rel=1e-3)


def test_the_build_result_carries_the_hole_records(demo):
    res = _call(demo, part_id="plate", points=[[0, 0]], family="tapped",
                size="M6", depth=8)
    assert res.get("ok") is True, res
    holes = res.get("holes")
    assert isinstance(holes, list) and len(holes) == 1
    assert holes[0]["family"] == "tapped"
    assert holes[0]["designation"].startswith("M6×1")
    assert holes[0]["count"] == 1


def test_two_calls_compose_and_do_not_shadow_each_other(demo):
    assert _call(demo, part_id="plate", points=[[20, 10]],
                 family="clearance", size="M5").get("ok") is True
    second = _call(demo, part_id="plate", points=[[-20, -10]],
                   family="clearance", size="M5")
    assert second.get("ok") is True, second
    script = demo.store.read_script("demo", "plate")
    assert script.count(ADD_HOLES_MARKER) == 2
    assert "_agentcad_prev_build_0" in script
    assert "_agentcad_prev_build_1" in script
    # Both holes survived: a shadowed previous build would lose the first.
    assert len(second["holes"]) == 2


def test_a_named_plane_stays_a_name_in_the_script(demo):
    """A name is a *predicate* re-evaluated every rebuild — the stable
    reference. Freezing it into a literal basis would reintroduce the very
    staleness the face path has to carry a caveat about."""
    res = _call(demo, part_id="plate", points=[[0, 0]], family="clearance",
                size="M5", plane="bottom")
    assert res.get("ok") is True, res
    script = demo.store.read_script("demo", "plate")
    assert "plane='bottom'" in script
    assert "_agentcad_Plane" not in script


def test_the_named_plane_list_matches_the_toolkits(demo):
    """This module cannot import `toolkit.holes` (it imports build123d), so
    the six names are duplicated. This is the assertion that keeps the copy
    honest."""
    from agentcad.toolkit import holes

    assert set(NAMED_PLANES) == set(holes._NAMED_PLANES)


# ---------------------------------------------------------- the picked face

def test_a_picked_face_emits_the_sketch_plane_basis_with_the_caveat(demo):
    top = _top_face(demo)
    registry = build_registry(demo)
    basis = registry.call("sketch_plane", {"project": "demo",
                                           "part_id": "plate",
                                           "face_index": top})
    assert "error" not in basis, basis

    res = _call(demo, part_id="plate", points=[[10, 5]], family="clearance",
                size="M5", face_index=top)
    assert res.get("ok") is True, res
    assert res["face_index"] == top and res["plane"] == "face"

    script = demo.store.read_script("demo", "plate")
    compile(script, "plate.py", "exec")
    assert "_agentcad_Plane(origin=" in script
    # THE emitted basis is `sketch_plane`'s, component for component.
    for key, arg in (("origin", "origin"), ("x_dir", "x_dir"),
                     ("normal", "z_dir")):
        rendered = f"{arg}=({', '.join(repr(float(c)) for c in basis[key])})"
        assert rendered in script, (rendered, script)
    # and the renumbering caveat travels with the code
    assert "mesh-order ordinals" in script


def test_a_non_planar_face_is_a_validation_error_and_touches_nothing(demo):
    demo.create_part("demo", "cyl", script=CYL_SCRIPT)
    registry = build_registry(demo)
    side = next(i for i in range(3)
                if not registry.call("face_info",
                                     {"project": "demo", "part_id": "cyl",
                                      "face_index": i})["planar"])
    res = _call(demo, part_id="cyl", points=[[0, 0]], family="clearance",
                size="M5", face_index=side)
    assert res["error"]["type"] == "validation_error"
    assert ADD_HOLES_MARKER not in demo.store.read_script("demo", "cyl")


def test_an_out_of_range_face_is_a_validation_error(demo):
    res = _call(demo, part_id="plate", points=[[0, 0]], family="clearance",
                size="M5", face_index=999)
    assert res["error"]["type"] == "validation_error"
    assert "out of range" in res["error"]["message"]


def test_plane_and_face_index_together_are_refused(demo):
    res = _call(demo, part_id="plate", points=[[0, 0]], family="clearance",
                size="M5", plane="top", face_index=0)
    assert res["error"]["type"] == "validation_error"


# ------------------------------------------------------------- the refusals

@pytest.mark.parametrize("bad", [
    {"family": "slotted", "size": "M5"},              # not a family
    {"family": "clearance", "size": "M4.5"},          # not in ISO 273
    {"family": "clearance", "size": "M5", "fit": "snug"},
    {"family": "clearance", "size": "M5", "std": "din"},
    {"family": "clearance", "size": "M5", "depth": -3},
    {"family": "drilled", "size": "not-a-number"},
])
def test_a_request_the_tables_cannot_answer_is_refused(demo, bad):
    res = _call(demo, part_id="plate", points=[[0, 0]], **bad)
    assert res["error"]["type"] == "validation_error", res
    assert ADD_HOLES_MARKER not in demo.store.read_script("demo", "plate")


@pytest.mark.parametrize("points", [
    [], "M5", [[0]], [[0, 0, 0]], [[0, "x"]], [[0, 0], [1, float("inf")]],
])
def test_bad_points_are_refused(demo, points):
    """`invalid_arguments` is the registry's schema layer answering first for
    a wholly wrong type; everything it lets through this tool refuses itself.
    Both are structured caller errors, and neither writes a line."""
    res = _call(demo, part_id="plate", points=points, family="clearance",
                size="M5")
    assert res["error"]["type"] in ("validation_error", "invalid_arguments"), res
    assert ADD_HOLES_MARKER not in demo.store.read_script("demo", "plate")


def test_a_crafted_identifier_cannot_reach_the_generated_source(demo):
    """The PRD-009 gotcha, as a test. Every hostile shape a caller can send —
    a size, a fit, a standard, a plane name, a coordinate — is refused before
    the emitter runs, because each one is a key into a table or a float."""
    payload = "M5'); import os; os.system('true'); ('"
    for field in ("size", "fit", "std", "plane"):
        res = _call(demo, part_id="plate", points=[[0, 0]],
                    family="clearance",
                    **{"size": "M5", field: payload})
        assert res["error"]["type"] == "validation_error", (field, res)
    res = _call(demo, part_id="plate", points=[[payload, 0]],
                family="clearance", size="M5")
    assert res["error"]["type"] == "validation_error"
    res = _call(demo, part_id="plate", points=[[0, 0]], family=payload,
                size="M5")
    assert res["error"]["type"] == "validation_error"
    script = demo.store.read_script("demo", "plate")
    assert "import os" not in script and ADD_HOLES_MARKER not in script


def test_an_unknown_part_is_a_structured_error(demo):
    res = _call(demo, part_id="missing", points=[[0, 0]], family="clearance",
                size="M5")
    assert res["error"]["type"] == "notfound_error", res


def test_a_numeric_string_coordinate_is_parsed_not_interpolated(demo):
    """A JSON client that sends `"20"` gets a hole at 20 mm, and the script
    gets `20.0` — the emitter writes `repr(float(...))`, never the caller's
    text, so leniency about the input type costs nothing at the output."""
    res = _call(demo, part_id="plate", points=[["20", "10"]],
                family="clearance", size="M5")
    assert res.get("ok") is True, res
    assert res["points"] == [[20.0, 10.0]]
    assert "[(20.0, 10.0)]" in demo.store.read_script("demo", "plate")


# ----------------------------------------------------------- every family

@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_every_family_emits_a_block_that_rebuilds(demo, family):
    # `drilled` has no table row, so its "size" is a diameter in millimetres —
    # a string here only because the tool's schema types the field once.
    size = "8" if family == "drilled" else "M6"
    depth = 6 if family in ("tapped",) else None
    params = {"part_id": "plate", "points": [[0, 0]], "family": family,
              "size": size}
    if depth is not None:
        params["depth"] = depth
    res = _call(demo, **params)
    assert res.get("ok") is True, (family, res)
    script = demo.store.read_script("demo", "plate")
    compile(script, "plate.py", "exec")
    helper = "drill" if family == "drilled" else family
    assert f"holes.{helper}(" in script
    assert res["metrics"]["volume_mm3"] > 0
