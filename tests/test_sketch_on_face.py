"""Sketch-on-face (PRD-009 slice 12, AC5), and the spike that shaped it.

The design rests on one assumption the plan required to be measured first:
**`Plane(face).x_dir` has to be deterministic**, or every coordinate a
sketch-on-face emits is arbitrary. The PRD-008 precedent is why — an unmeasured
assumption about OCCT face behaviour cost three review rounds there.

Measured through the kernel worker (only `agentcad/kernel/` may import
build123d), on the prototyping enclosure and the rocketry nozzle:

```
part              faces  planar  chosen face  edges                  x_dir
enclosure_base       87      59       37      4 LINE + 4 CIRCLE      (1, 0, 0)
nozzle               10       2        6      2 CIRCLE               (1, 0, 0)
```

- `x_dir` was **bit-identical** (max delta 0.0) across 3 rebuilds in one worker,
  across a fresh worker process, and across the parameter changes `wall: 3.0`
  and `length: 110.0`, which resize that face without renumbering the part's
  faces.
- `corner_r: 6.0` **renumbers** the faces: ordinal 37 goes from the 5989 mm^2
  base plate to a 51 mm^2 sliver. That is the face *index* moving, not the
  basis — the same instability push/pull already documents, and the emitted
  script carries the caveat inline.
- A planar face bounded by a spline and an elliptical arc returns those edges
  as `kind: "other"` with a 25-point polyline and `constrainable: False`.

This file re-runs every one of those measurements.
"""

import math

import pytest

from agentcad.core.sketch_emit import EmitError, emit
from agentcad.core.tools import build_registry
from agentcad.core.tools_sketch import reference_entities
from agentcad.toolkit.sketch import solve_sketch

from .conftest import make_test_service

ENCLOSURE = "examples/prototyping/parts/enclosure_base.py"
NOZZLE = "examples/rocketry/parts/nozzle.py"

# A planar face bounded by curves that are neither lines nor circles.
SPLINE_PART = '''\
from build123d import *

PARAMS = {
    "h": {"default": 6.0, "min": 2.0, "max": 20.0, "unit": "mm"},
    "w": {"default": 40.0, "min": 20.0, "max": 80.0, "unit": "mm"},
}


def build(p):
    with BuildPart() as bp:
        with BuildSketch(Plane.XY) as sk:
            with BuildLine():
                Spline((0, 0), (p.w * 0.4, 14), (p.w, 4))
                Line((p.w, 4), (p.w, -12))
                EllipticalCenterArc((p.w / 2, -12), p.w / 2, 8.0,
                                    start_angle=0.0, arc_size=180.0)
                Line((0, -12), (0, 0))
            make_face()
        extrude(amount=p.h)
    return bp.part
'''

BOX_SCRIPT = '''\
from build123d import *

PARAMS = {"size": {"default": 20.0, "min": 5.0, "max": 100.0, "unit": "mm"}}


def build(p):
    return Solid.make_box(p.size, p.size, p.size)
'''


def _read(path: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / path).read_text()


def _planes(kernel, script: str, params: dict | None = None) -> dict:
    """Every planar face's plane, keyed by mesh-order index."""
    n = kernel.request("face_info", {"script": script, "params": params or {},
                                     "face_index": 0})["n_faces"]
    out = {}
    for i in range(n):
        try:
            out[i] = kernel.request("sketch_plane", {
                "script": script, "params": params or {}, "face_index": i})
        except Exception:
            continue          # not planar; `sketch_plane` says so loudly
    return out, n


def _biggest(planes: dict) -> int:
    return max(planes, key=lambda i: planes[i]["area_mm2"])


# --------------------------------------------------------------------------
# the spike, as tests
# --------------------------------------------------------------------------
@pytest.mark.slow
@pytest.mark.parametrize("path,faces,planar,edges", [
    (ENCLOSURE, 87, 59, {"LINE": 4, "CIRCLE": 4}),
    (NOZZLE, 10, 2, {"CIRCLE": 2}),
])
def test_the_spike_inventory_of_the_two_example_parts(kernel, path, faces,
                                                      planar, edges):
    """What actually comes back — counted, not assumed."""
    script = _read(path)
    planes, n_faces = _planes(kernel, script)
    assert n_faces == faces
    assert len(planes) == planar
    top = _biggest(planes)
    assert planes[top]["ref_kinds"] == edges
    assert all(r["constrainable"] for r in planes[top]["refs"])


@pytest.mark.slow
@pytest.mark.parametrize("path", [ENCLOSURE, NOZZLE])
def test_x_dir_is_bit_identical_across_rebuilds(kernel, path):
    """**The gating measurement.** An unstable basis would make every emitted
    sketch-on-face coordinate arbitrary and would change the design."""
    script = _read(path)
    planes, _ = _planes(kernel, script)
    face = _biggest(planes)
    runs = [kernel.request("sketch_plane", {"script": script, "params": {},
                                            "face_index": face})
            for _ in range(3)]
    for run in runs[1:]:
        assert run["x_dir"] == runs[0]["x_dir"]
        assert run["y_dir"] == runs[0]["y_dir"]
        assert run["origin"] == runs[0]["origin"]
    # orthonormal, right-handed: x cross y == normal
    x, y, z = runs[0]["x_dir"], runs[0]["y_dir"], runs[0]["normal"]
    cross = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2],
             x[0] * y[1] - x[1] * y[0])
    assert cross == pytest.approx(tuple(z), abs=1e-12)


@pytest.mark.slow
def test_x_dir_survives_a_parameter_change_that_keeps_the_face(kernel):
    """A resize is not a renumber: `wall` and `length` move the geometry and
    leave the basis alone."""
    script = _read(ENCLOSURE)
    base = kernel.request("sketch_plane", {"script": script, "params": {},
                                           "face_index": 37})
    for change in ({"wall": 3.0}, {"length": 110.0}):
        moved = kernel.request("sketch_plane", {"script": script,
                                                "params": change,
                                                "face_index": 37})
        assert moved["x_dir"] == base["x_dir"], change
        assert moved["ref_kinds"] == base["ref_kinds"], change
        assert moved["area_mm2"] != pytest.approx(base["area_mm2"])


@pytest.mark.slow
def test_a_topology_changing_parameter_renumbers_the_faces(kernel):
    """The instability that is real, measured so the caveat is not folklore:
    `corner_r: 6.0` turns face 37 from the 5989 mm^2 base plate into a 51 mm^2
    sliver. It is the *ordinal* that moved, which is exactly what the emitted
    NOTE warns about."""
    script = _read(ENCLOSURE)
    base = kernel.request("sketch_plane", {"script": script, "params": {},
                                           "face_index": 37})
    moved = kernel.request("sketch_plane", {"script": script,
                                            "params": {"corner_r": 6.0},
                                            "face_index": 37})
    assert base["area_mm2"] > 5000.0
    assert moved["area_mm2"] < 100.0


@pytest.mark.slow
def test_a_non_line_non_circle_edge_is_other_and_not_a_constraint_target(kernel):
    """A documented gap, not a silent one: the edge comes back, drawable, and
    flagged unusable rather than approximated into something constrainable."""
    planes, _ = _planes(kernel, SPLINE_PART)
    face = _biggest(planes)
    kinds = planes[face]["ref_kinds"]
    assert kinds.get("BSPLINE") == 1 and kinds.get("ELLIPSE") == 1
    others = [r for r in planes[face]["refs"] if r["kind"] == "other"]
    assert len(others) == 2
    for ref in others:
        assert ref["constrainable"] is False
        assert len(ref["points"]) == 25
    # and they are dropped from the entities a sketch would ingest
    ents = reference_entities(planes[face]["refs"])
    assert len(ents["lines"]) == 2
    assert not ents["circles"] and not ents["arcs"]


def test_a_non_planar_face_is_refused_by_geom_type(kernel):
    from agentcad.kernel.client import KernelError
    with pytest.raises(KernelError, match="needs a planar one"):
        kernel.request("sketch_plane", {"script": SPLINE_PART, "params": {},
                                        "face_index": 0})


def test_an_out_of_range_face_index_says_how_many_there_are(kernel):
    from agentcad.kernel.client import KernelError
    with pytest.raises(KernelError, match="out of range"):
        kernel.request("sketch_plane", {"script": BOX_SCRIPT, "params": {},
                                        "face_index": 99})


# --------------------------------------------------------------------------
# reference entities: fixed, construction, zero parameters
# --------------------------------------------------------------------------
def test_reference_entities_contribute_zero_free_parameters(kernel):
    """Design Decision 12, asserted in the one number that proves it."""
    planes, _ = _planes(kernel, BOX_SCRIPT)
    face = _biggest(planes)
    ents = reference_entities(planes[face]["refs"])
    spec = {**ents, "constraints": []}
    res = solve_sketch(spec)
    assert res["n_params"] == 0
    assert res["n_residuals"] == 0
    assert res["dof"] == 0
    assert res["diagnostics"]["free_entities"] == []
    assert res["ok"] is True


def test_a_fixed_reference_arc_owns_no_parameters_at_all():
    """Not even its two angles — an arc normally allocates them whatever its
    radius does, and a reference that could be re-swept is not a reference."""
    spec = {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True}],
        "arcs": [{"name": "ref0", "center": "c", "r": 5.0, "start_deg": 0.0,
                  "end_deg": 90.0, "fixed": True, "construction": True}],
        "constraints": [],
    }
    res = solve_sketch(spec)
    assert res["n_params"] == 0
    arc = res["arcs"]["ref0"]
    assert (arc["start_deg"], arc["end_deg"]) == pytest.approx((0.0, 90.0))
    assert (arc["start"]["x"], arc["start"]["y"]) == pytest.approx((5.0, 0.0))
    assert res["construction"] == ["ref0"]


def test_a_reference_is_a_real_constraint_target(kernel):
    """The point of projecting them: a new line can be made collinear with a
    projected edge, and the solve moves the *new* geometry only."""
    planes, _ = _planes(kernel, BOX_SCRIPT)
    face = _biggest(planes)
    ents = reference_entities(planes[face]["refs"])
    edge = ents["lines"][0]["name"]
    spec = {
        "points": ents["points"] + [{"name": "a", "x": 3.0, "y": 4.0},
                                    {"name": "b", "x": 9.0, "y": 5.0}],
        "lines": ents["lines"] + [{"name": "new", "p1": "a", "p2": "b"}],
        "circles": ents["circles"], "arcs": ents["arcs"],
        "constraints": [
            {"type": "parallel", "l1": "new", "l2": edge},
            {"type": "point_on_line", "p": "a", "ln": edge},
            {"type": "distance", "p": "a", "q": "b", "d": 8.0},
        ],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True, res["diagnostics"]
    assert res["n_params"] == 4          # only the two new points
    a, b = res["points"]["a"], res["points"]["b"]
    p1 = res["points"][ents["lines"][0]["p1"]]
    p2 = res["points"][ents["lines"][0]["p2"]]
    ux, uy = p2["x"] - p1["x"], p2["y"] - p1["y"]
    cross = (a["x"] - p1["x"]) * uy - (a["y"] - p1["y"]) * ux
    assert cross == pytest.approx(0.0, abs=1e-9)
    assert math.dist((a["x"], a["y"]), (b["x"], b["y"])) == \
        pytest.approx(8.0, abs=1e-9)


def test_references_do_not_emit_as_geometry(kernel):
    """A projected edge belongs to the part, not to the new profile. Emitting
    it would draw the face's own boundary a second time."""
    planes, _ = _planes(kernel, BOX_SCRIPT)
    face = _biggest(planes)
    ents = reference_entities(planes[face]["refs"])
    spec = {
        "points": ents["points"] + [
            {"name": "a", "x": 3.0, "y": 3.0}, {"name": "b", "x": 12.0, "y": 3.0},
            {"name": "cc", "x": 12.0, "y": 11.0}, {"name": "d", "x": 3.0, "y": 11.0}],
        "lines": ents["lines"] + [
            {"name": "n1", "p1": "a", "p2": "b"}, {"name": "n2", "p1": "b", "p2": "cc"},
            {"name": "n3", "p1": "cc", "p2": "d"}, {"name": "n4", "p1": "d", "p2": "a"}],
        "circles": ents["circles"], "arcs": ents["arcs"],
        "constraints": [],
    }
    res = solve_sketch(spec)
    code = emit(res, spec, style="buildline")["code"]
    for name in [line["name"] for line in ents["lines"]]:
        assert name not in code
    assert code.count("Polyline(") == 1
    assert "make_face()" in code


# --------------------------------------------------------------------------
# emission onto the face's plane (FR8)
# --------------------------------------------------------------------------
def _profile_on(plane: dict) -> tuple[dict, dict]:
    spec = {
        "points": [{"name": "a", "x": 3.0, "y": 3.0},
                   {"name": "b", "x": 13.0, "y": 3.0},
                   {"name": "c", "x": 13.0, "y": 9.0},
                   {"name": "d", "x": 3.0, "y": 9.0}],
        "lines": [{"name": "l1", "p1": "a", "p2": "b"},
                  {"name": "l2", "p1": "b", "p2": "c"},
                  {"name": "l3", "p1": "c", "p2": "d"},
                  {"name": "l4", "p1": "d", "p2": "a"}],
        "constraints": [],
        "plane": plane,
    }
    return spec, solve_sketch(spec)


def test_emission_writes_the_basis_and_the_caveat(kernel):
    planes, _ = _planes(kernel, BOX_SCRIPT)
    face = _biggest(planes)
    plane = {**planes[face], "part": "build(p)"}
    spec, res = _profile_on(plane)
    out = emit(res, spec, style="function")
    code = out["code"]
    assert f"agentcad sketch on face {face}" in code
    assert "mesh-order ordinals" in code
    assert "BuildSketch(Plane(origin=" in code
    assert "x_dir=(" in code and "z_dir=(" in code
    assert "Plane.XY" not in code
    assert out["plane"]["face_index"] == face


def test_without_a_plane_the_emission_is_unchanged():
    """FR3-adjacent: every existing caller passes no plane and must keep
    getting `Plane.XY` and the old header."""
    spec, res = _profile_on(None)
    spec.pop("plane")
    code = emit(res, spec, style="function")["code"]
    assert "BuildSketch(Plane.XY)" in code
    assert "agentcad sketch (auto-generated)" in code


@pytest.mark.slow
def test_ac5_a_sketch_on_the_enclosures_top_face_rebuilds_green(kernel,
                                                                tmp_path):
    """**AC5.** Sketch on the prototyping enclosure's top face, constrained to
    a projected edge, emitted, and rebuilt through the kernel — with the face
    reference and the caveat in the script.

    The example is read and rebuilt from a **copy**: `examples/` is never
    mutated.
    """
    script = _read(ENCLOSURE)
    planes, _ = _planes(kernel, script)
    face = _biggest(planes)
    info = planes[face]
    ents = reference_entities(info["refs"])
    edge = ents["lines"][0]["name"]
    spec = {
        # seeded roughly where it should land, next to the edge it references
        # — the solver converges to the *nearest* solution, and a seed 90 deg
        # from the answer is how a chain of perpendiculars diverges
        "points": ents["points"] + [{"name": "a", "x": -50.0, "y": 5.0},
                                    {"name": "b", "x": -50.0, "y": -7.0},
                                    {"name": "c", "x": -42.0, "y": -7.0},
                                    {"name": "d", "x": -42.0, "y": 5.0}],
        "lines": ents["lines"] + [{"name": "n1", "p1": "a", "p2": "b"},
                                  {"name": "n2", "p1": "b", "p2": "c"},
                                  {"name": "n3", "p1": "c", "p2": "d"},
                                  {"name": "n4", "p1": "d", "p2": "a"}],
        "circles": ents["circles"], "arcs": ents["arcs"],
        "constraints": [
            # anchored to the *projected edge*: that is what a reference is for
            {"type": "point_on_line", "p": "a", "ln": edge},
            {"type": "distance", "p": "a", "q": f"{edge}_a", "d": 20.0},
            {"type": "parallel", "l1": "n1", "l2": edge},
            {"type": "distance", "p": "a", "q": "b", "d": 12.0},
            {"type": "distance", "p": "b", "q": "c", "d": 8.0},
            {"type": "perpendicular", "l1": "n1", "l2": "n2"},
            {"type": "perpendicular", "l1": "n2", "l2": "n3"},
            {"type": "perpendicular", "l1": "n3", "l2": "n4"},
        ],
        "plane": {**info, "part": "build(p)"},
    }
    res = solve_sketch(spec)
    assert res["ok"] is True, res["diagnostics"]
    assert res["dof"] == 0
    # anchored to the projected edge: 20 mm along it from its first endpoint
    a = res["points"]["a"]
    ref_a = res["points"][f"{edge}_a"]
    assert math.dist((a["x"], a["y"]), (ref_a["x"], ref_a["y"])) == \
        pytest.approx(20.0, abs=1e-9)
    out = emit(res, spec, style="function")
    assert out["warnings"] == []
    # the profile pad, built on the picked face's plane, through the kernel
    pad = (script.rstrip("\n") + "\n\n" + out["code"] + "\n\n"
           "_agentcad_prev_build = build\n"
           "def build(p):\n"
           "    return _agentcad_prev_build(p) + extrude(sketch_profile(), "
           "amount=2.0)\n")
    before = kernel.request("build", {
        "script": script, "params": {}, "mesh_path": str(tmp_path / "a.acm"),
    })["metrics"]
    after = kernel.request("build", {
        "script": pad, "params": {}, "mesh_path": str(tmp_path / "b.acm"),
    })["metrics"]
    assert after["volume_mm3"] == pytest.approx(
        before["volume_mm3"] + 12.0 * 8.0 * 2.0, rel=1e-6)
    assert f"face {face}" in out["code"]
    assert "Re-pick the face" in out["code"]


# --------------------------------------------------------------------------
# the tool surface
# --------------------------------------------------------------------------
@pytest.fixture
def demo(tmp_path, kernel):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX_SCRIPT)
    return service


def test_the_sketch_plane_tool_returns_a_basis_and_ready_made_entities(demo):
    registry = build_registry(demo)
    n = registry.call("face_info", {"project": "demo", "part_id": "box",
                                    "face_index": 0})["n_faces"]
    found = None
    for i in range(n):
        info = registry.call("sketch_plane", {"project": "demo",
                                              "part_id": "box",
                                              "face_index": i})
        if info["normal"] == [0.0, 0.0, 1.0]:
            found = info
            break
    assert found is not None
    assert len(found["refs"]) == 4
    assert found["entities"]["lines"][0]["construction"] is True
    assert found["entities"]["points"][0]["fixed"] is True
    assert "mesh-order ordinals" in found["caveat"]
    assert found["n_faces"] == n


def test_the_tool_refuses_an_imported_reference_part(demo, tmp_path, kernel):
    """An imported reference has no script to emit a sketch into.

    `ToolRegistry.call` turns an `AppError` into an `{error: ...}` envelope —
    the same shape the route returns — so that is what is asserted."""
    import shutil

    step = tmp_path / "ref.step"
    kernel.request("export", {"script": BOX_SCRIPT, "params": {},
                              "format": "step", "out_path": str(step)})
    demo.create_part("demo", "imported", kind="reference", source="ref.step",
                     label="Imported")
    shutil.copy(step, demo.store.imports_dir("demo") / "ref.step")
    registry = build_registry(demo)
    res = registry.call("sketch_plane", {"project": "demo",
                                         "part_id": "imported",
                                         "face_index": 0})
    assert res["error"]["type"] == "validation_error"
    assert "script parts only" in res["error"]["message"]


def test_the_route_carries_the_plane_through_to_emission(demo):
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    app = create_app(demo, build_registry(demo),
                     extra_allowed_hosts={"testserver"})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        body = {
            "entities": {"points": [{"name": "a", "x": 0.0, "y": 0.0},
                                    {"name": "b", "x": 10.0, "y": 0.0},
                                    {"name": "c", "x": 10.0, "y": 6.0},
                                    {"name": "d", "x": 0.0, "y": 6.0}],
                         "lines": [{"name": "l1", "p1": "a", "p2": "b"},
                                   {"name": "l2", "p1": "b", "p2": "c"},
                                   {"name": "l3", "p1": "c", "p2": "d"},
                                   {"name": "l4", "p1": "d", "p2": "a"}]},
            "constraints": [],
            "emit": "function",
            "plane": {"origin": [0.0, 0.0, 20.0], "x_dir": [1.0, 0.0, 0.0],
                      "normal": [0.0, 0.0, 1.0], "face_index": 5},
        }
        res = client.post("/api/sketch/solve", json=body).json()
    assert "error" not in res, res
    assert "BuildSketch(Plane(origin=(0.0, 0.0, 20.0)" in res["emit"]["code"]
    assert "agentcad sketch on face 5" in res["emit"]["code"]


# --------------------------------------------------------------------------
# the plane is caller data, not source (review P5/P7)
# --------------------------------------------------------------------------
# `plane["part"]` and `plane["face_index"]` were interpolated raw into the
# generated header, and `plane`'s vectors went through a bare `float()`. The
# emitter's contract is that it produces only what it *formatted*: a crafted
# `part` put `import os` on line 2 of a generated script, reachable from any
# agent/MCP/HTTP caller, and defeating
# `test_emitted_code_never_imports_agentcad`'s `assert "import" not in code`.

INJECTED_PART = ('build(p) ---\nimport os\nos.system("id")\n'
                 '# --- agentcad sketch on face 0 of build(p)')


def _plane(**over) -> dict:
    return {"origin": [0.0, 0.0, 20.0], "x_dir": [1.0, 0.0, 0.0],
            "normal": [0.0, 0.0, 1.0], "face_index": 5, **over}


@pytest.mark.parametrize("plane", [
    _plane(part=INJECTED_PART),
    _plane(part='x"""\nimport os'),
    _plane(face_index="0 ---\nimport os\n# ---"),
    _plane(face_index=1.5),
    _plane(origin=["0.0); import os; Plane(origin=(0.0", 0.0, 0.0]),
    _plane(x_dir="not a vector"),
], ids=["part_newline", "part_quotes", "index_newline", "index_float",
        "origin_expression", "x_dir_string"])
def test_a_crafted_plane_is_refused_rather_than_written_into_the_script(plane):
    """Strict validation, not escaping: an int is an int and a part reference
    is an identifier-ish expression, so anything else is refused with an
    `EmitError` (which the tool layer renders as `validation_error`)."""
    spec, res = _profile_on(plane)
    with pytest.raises(EmitError) as exc:
        emit(res, spec, style="function")
    assert "plane" in str(exc.value)


def test_the_route_refuses_a_crafted_plane_with_the_validation_contract(demo):
    """The same payload through the HTTP surface an agent uses: a
    `validation_error` envelope, never a 500 and never a script with `import`
    in it."""
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    app = create_app(demo, build_registry(demo),
                     extra_allowed_hosts={"testserver"})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        body = {
            "entities": {"points": [{"name": "a", "x": 0.0, "y": 0.0},
                                    {"name": "b", "x": 10.0, "y": 0.0}],
                         "lines": [{"name": "l1", "p1": "a", "p2": "b"}]},
            "constraints": [], "emit": "function",
            "plane": _plane(part=INJECTED_PART),
        }
        response = client.post("/api/sketch/solve", json=body)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["error"]["type"] == "validation_error", res
    assert "emit" not in res, res


def test_a_valid_plane_still_writes_its_header():
    """The guard must not cost the feature: the shapes the GUI and the
    `sketch_plane` tool actually send keep working."""
    for part in (None, "build(p)", "build(p).part", "p", "_sk"):
        plane = _plane(part=part) if part else _plane()
        spec, res = _profile_on(plane)
        code = emit(res, spec, style="function")["code"]
        assert "agentcad sketch on face 5 of " in code
        assert "import" not in code


# --------------------------------------------------------------------------
# a stale face ordinal is surfaced on reopen, not silently accepted (P11)
# --------------------------------------------------------------------------
# `test_a_topology_changing_parameter_renumbers_the_faces` measures the
# instability; nothing *checked* it. A persisted sketch-on-face block reopened
# after a renumber re-solved on the old basis, emitted "on face 37" naming a
# different face, and its hash still matched — so the block read `ok` while
# standing on a 51 mm^2 sliver instead of the 5989 mm^2 plate it was drawn on.
# The face's identity (area and normal) is already returned; it is now recorded
# in the plane and re-checked.

def test_the_plane_carries_the_face_identity_it_was_taken_with(demo):
    registry = build_registry(demo)
    info = registry.call("sketch_plane", {"project": "demo", "part_id": "box",
                                          "face_index": 0})
    assert info["face_check"]["status"] == "unchecked"
    assert set(info["face_id"]) == {"area_mm2", "normal", "origin"}


def test_reopening_on_the_same_face_checks_out(demo):
    registry = build_registry(demo)
    info = registry.call("sketch_plane", {"project": "demo", "part_id": "box",
                                          "face_index": 0})
    again = registry.call("sketch_plane", {"project": "demo",
                                           "part_id": "box", "face_index": 0,
                                           "expect": info["face_id"]})
    assert again["face_check"]["status"] == "ok", again["face_check"]


def test_a_renumbered_face_is_reported_moved_with_both_measurements(demo):
    """The honest verdict names both numbers: a caller can see *how far* the
    ordinal moved rather than being told a boolean."""
    registry = build_registry(demo)
    faces = registry.call("face_info", {"project": "demo", "part_id": "box",
                                        "face_index": 0})["n_faces"]
    infos = [registry.call("sketch_plane", {"project": "demo",
                                            "part_id": "box",
                                            "face_index": i})
             for i in range(faces)]
    top = next(i for i in infos if i["normal"] == [0.0, 0.0, 1.0])
    side = next(i for i in infos if i["normal"] != [0.0, 0.0, 1.0]
                and i["normal"] != [0.0, 0.0, -1.0])
    check = registry.call("sketch_plane", {
        "project": "demo", "part_id": "box",
        "face_index": side["face_index"], "expect": top["face_id"]})["face_check"]
    assert check["status"] == "moved", check
    assert check["expected"]["area_mm2"] == top["face_id"]["area_mm2"]
    assert check["actual"]["area_mm2"] == side["area_mm2"]
    assert "face" in check["message"] and "re-pick" in check["message"].lower()


@pytest.mark.slow
def test_the_measured_enclosure_renumber_is_caught(kernel, tmp_path):
    """The exact instability the caveat is about, end to end: `corner_r: 6.0`
    turns face 37 from the 5989 mm^2 base plate into a 51 mm^2 sliver, and
    reopening a sketch recorded on the plate now says so."""
    from agentcad.core.tools_sketch import face_check
    script = _read(ENCLOSURE)
    base = kernel.request("sketch_plane", {"script": script, "params": {},
                                           "face_index": 37})
    moved = kernel.request("sketch_plane", {"script": script,
                                            "params": {"corner_r": 6.0},
                                            "face_index": 37})
    expect = {k: base[k] for k in ("area_mm2", "normal", "origin")}
    assert face_check(base, expect)["status"] == "ok"
    check = face_check(moved, expect)
    assert check["status"] == "moved", check
    assert f"{base['area_mm2']:.4g}" in check["message"]
    assert f"{moved['area_mm2']:.4g}" in check["message"]
