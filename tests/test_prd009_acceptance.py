"""PRD-009 acceptance criteria — one named test per AC (slice 14).

The mechanics are covered in depth by the eleven `tests/test_sketch_*.py`
modules: `test_sketch_v1_corpus.py` (the FR3 compatibility corpus),
`test_sketch_jacobian.py` (every analytic derivative against a central
difference), `test_sketch_diagnostics.py` (rank, DOF, the dependent set),
`test_sketch_initial.py`, `test_sketch_arcs.py`,
`test_sketch_tangent_direction.py`, `test_sketch_splines.py`,
`test_sketch_slots.py`, `test_sketch_ellipses.py`, `test_sketch_emit.py`,
`test_sketch_drag.py`, `test_sketch_on_face.py`, `test_sketch_roundtrip.py`
and the `slow` benchmark in `test_sketch_bench.py`.

This file is the **contract** layer: it walks each acceptance criterion of
`docs/prd/in-progress/PRD-009-sketcher-v2.md` through the surfaces a human and
an agent actually touch — the registered tools, the REST routes and a real
kernel build — so a reviewer can map AC → test without reading the unit
suites.

| AC | Test |
|----|------|
| AC1 | ``test_ac1_a_slotted_cam_emits_code_that_rebuilds_to_the_solved_metrics`` |
| AC2 | ``test_ac2_a_hundred_step_drag_over_the_route_never_flips_branch`` +
        ``test_ac2_browser_half_evidence_is_recorded`` |
| AC3 | ``test_ac3_a_redundant_constraint_names_the_constraint_that_was_added`` |
| AC4 | ``test_ac4_an_under_constrained_sketch_reports_dof_and_free_entities`` +
        ``test_ac4_the_dof_chip_is_wired_into_the_shipped_frontend`` |
| AC5 | ``test_ac5_a_sketch_on_the_enclosures_top_face_rebuilds_green`` |
| AC6 | ``test_ac6_the_v1_corpus_is_identical_through_the_tool_surface`` +
        ``test_ac6_solve_ms_is_a_duration_even_when_the_sketch_has_arcs`` +
        ``test_ac6_the_full_suite_count_is_cited`` |
| AC7 | ``test_ac7_browser_half_evidence_is_recorded`` +
        ``test_ac7_the_sketcher_surfaces_the_changelogs_claim_exist`` |

**The browser halves are evidence checks, deliberately.** AC2's UI half, AC4's
chip and AC7 are claims about a real browser session; they were driven for
real (headless Chrome, screenshots, zero console errors) in slices 9, 10, 11,
12 and 13 and the changelogs are the record. The pattern — an evidence check
that fails if the record is removed, plus a *structural* gate that fails if
the feature is deleted — is PRD-001 AC6 / PRD-002 AC1 / PRD-008 AC1, and the
structural gate exists because the evidence check alone is exactly as strong
as the prose it reads.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core.model import ValidationError
from agentcad.core.sketch_emit import parse_blocks
from agentcad.core.tools import ToolRegistry, build_registry
from agentcad.core.tools_sketch import register as register_sketch
from agentcad.server.app import create_app
from agentcad.toolkit.sketch import solve_sketch

from .conftest import make_test_service
# The geometry oracles live with the tests that measured them; re-deriving
# Green's-theorem areas here would be a second implementation to keep true.
from .test_sketch_emit import (
    build_metrics, cam_area_and_bbox, slot_area_and_bbox, slotted_cam_spec,
)
from .test_sketch_on_face import ENCLOSURE, _biggest, _planes, _read
from agentcad.core.tools_sketch import reference_entities
from .test_sketch_v1_corpus import CASES, EXPECTED

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "docs" / "changelog"
FRONTEND = REPO_ROOT / "frontend" / "js"

ENTITY_KINDS = ("points", "lines", "circles", "arcs", "ellipses", "splines",
                "slots")
FR6_WARM_MS = 16.0


def entities_of(spec: dict) -> dict:
    return {k: spec.get(k, []) for k in ENTITY_KINDS}


@pytest.fixture
def tools() -> ToolRegistry:
    """The sketch pack alone — `solve_sketch` needs no service."""
    registry = ToolRegistry()
    register_sketch(registry, None)
    return registry


@pytest.fixture
def http(tmp_path):
    service = make_test_service(tmp_path / "projects", None)
    return TestClient(create_app(service, build_registry(service)),
                      base_url="http://127.0.0.1")


def call(registry: ToolRegistry, spec: dict, **kwargs) -> dict:
    return registry.get("solve_sketch").handler(
        entities=entities_of(spec), constraints=spec["constraints"], **kwargs)


# ------------------------------------------------------------------- AC1
@pytest.mark.slow
def test_ac1_a_slotted_cam_emits_code_that_rebuilds_to_the_solved_metrics(
        tools, kernel, tmp_path):
    """**AC1** — the roadmap's done-when case, end to end through the *tool*.

    A slotted cam profile (two tangent arcs of different radii, two lines on
    their virtual handles, and a slot) solves; the emitted `BuildLine` is
    written into a scratch part and rebuilt through the real kernel; the
    rebuilt metrics match the geometry computed from the **solved
    coordinates** — Green's theorem for the area, the arcs' true extremes for
    the bbox — to `rel=1e-6`.

    Every coordinate in the spec is non-round on purpose: the emission bug
    this gate exists for does not reproduce on tidy numbers.
    """
    spec = slotted_cam_spec()
    out = call(tools, spec, emit="function", persist="cam")
    assert out["ok"] is True
    code = out["emit"]["code"]

    # the round-trip block rides along, and the code inside it is what builds
    block = parse_blocks(code)[0]
    assert block["status"] == "ok"
    assert "def sketch_cam():" in block["code"]

    metrics = build_metrics(kernel, tmp_path, block["code"].replace(
        "def sketch_cam():", "def sketch_profile():"))
    cam_area, cam_bb = cam_area_and_bbox(out)
    slot_area, slot_bb = slot_area_and_bbox(out)
    # extruded 1 mm by the harness, so the volume *is* the profile's area
    assert metrics["volume_mm3"] == pytest.approx(cam_area + slot_area,
                                                  rel=1e-6)
    bb = metrics["bbox"]
    for got, want in ((bb["min"][0], min(cam_bb[0], slot_bb[0])),
                      (bb["min"][1], min(cam_bb[1], slot_bb[1])),
                      (bb["max"][0], max(cam_bb[2], slot_bb[2])),
                      (bb["max"][1], max(cam_bb[3], slot_bb[3]))):
        assert got == pytest.approx(want, rel=1e-6, abs=1e-6)


# ------------------------------------------------------------------- AC2
def cam_lobe() -> dict:
    """Two arcs joined by two tangent lines — the drag subject of AC2."""
    return {
        "points": [{"name": "cL", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "cR", "x": 38.0, "y": 0.0}],
        "arcs": [{"name": "L", "center": "cL", "r": 18.0,
                  "start_deg": 90.0, "end_deg": 270.0},
                 {"name": "R", "center": "cR", "r": 7.0,
                  "start_deg": 270.0, "end_deg": 450.0}],
        "lines": [{"name": "top", "p1": "L.start", "p2": "R.end"},
                  {"name": "bot", "p1": "R.start", "p2": "L.end"}],
        "constraints": [{"type": "tangent", "a": "top", "b": "L"},
                        {"type": "tangent", "a": "top", "b": "R"},
                        {"type": "tangent", "a": "bot", "b": "L"},
                        {"type": "tangent", "a": "bot", "b": "R"},
                        {"type": "radius", "c": "L", "r": 18.0},
                        {"type": "radius", "c": "R", "r": 7.0}],
    }


def orientation(result: dict) -> tuple:
    """The branch invariant: the sign of every arc's signed sweep.

    A mirror flip *is* an arc taking the other way round, so a sweep that
    changes sign between two frames of one drag is a flip.
    """
    return tuple(1 if a["end_deg"] >= a["start_deg"] else -1
                 for _, a in sorted(result["arcs"].items()))


def seed_of(result: dict) -> dict:
    """`initial` from the previous frame — never from the cursor."""
    seed = {"points": {n: {"x": p["x"], "y": p["y"]}
                       for n, p in result["points"].items()}}
    if result.get("arcs"):
        seed["arcs"] = {n: {"r": a["r"], "start_deg": a["start_deg"],
                            "end_deg": a["end_deg"]}
                        for n, a in result["arcs"].items()}
    return seed


def test_ac2_a_hundred_step_drag_over_the_route_never_flips_branch(http):
    """**AC2 (solver half), over the real route.**

    100 scripted drag frames of a cam lobe through `POST /api/sketch/solve`,
    each warm-started from the previous frame's solution with the cursor
    entering as a weighted soft objective — the frame protocol the browser
    runs, minus the browser. Zero branch flips, and the solver's own
    `solve_ms` p50 inside FR6's 16 ms budget.

    The end-to-end browser latency is a different quantity and was measured
    separately (changelog 0136); the route can only speak for the server half.
    """
    spec = cam_lobe()
    body = {"entities": entities_of(spec), "constraints": spec["constraints"]}
    base = http.post("/api/sketch/solve", json=body).json()
    assert base["ok"] is True, base
    ref = orientation(base)
    home = base["points"]["cR"]

    prev, flips, solve_ms, wall_ms = base, 0, [], []
    for i in range(100):
        ang = 2 * math.pi * i / 100
        frame = {**body, "initial": seed_of(prev),
                 "drag": {"point": "cR",
                          "x": home["x"] + 12.0 * math.sin(ang),
                          "y": home["y"] + 12.0 * math.cos(ang)}}
        t0 = time.perf_counter()
        res = http.post("/api/sketch/solve", json=frame).json()
        wall_ms.append((time.perf_counter() - t0) * 1e3)
        assert res["ok"] is True, (i, res)
        assert res["warm_started"] is True, (i, res.get("warnings"))
        if orientation(res) != ref:
            flips += 1
        solve_ms.append(res["solve_ms"])
        prev = res

    solve_ms.sort()
    wall_ms.sort()
    print(f"\nAC2: 100 drag frames over the route — solve p50 "
          f"{solve_ms[50]:.2f} ms, route wall p50 {wall_ms[50]:.2f} ms, "
          f"flips {flips}")
    assert flips == 0, (
        f"{flips} branch flip(s) over 100 frames: an arc took the other way "
        "round mid-drag, which is the failure the weak-pull objective and "
        "previous-frame seeding exist to prevent")
    # bounded on BOTH sides: `solve_ms` was a large negative number for any
    # sketch containing an arc until this slice (see the regression below), and
    # a one-sided budget assertion passes happily on a negative
    assert 0.0 < solve_ms[50] <= FR6_WARM_MS, solve_ms[50]
    # the drag is an objective, not a constraint: it never poisons the verdict
    assert prev["max_residual"] < 1e-7
    assert prev["diagnostics_source"] == "cached"


def test_ac2_browser_half_evidence_is_recorded():
    """AC2's UI half — "the profile deforms continuously with no visible flip"
    is a claim about a browser, driven for real in slice 10 with screenshots.
    This asserts the record exists, so removing it fails a test."""
    entry = (CHANGELOG / "0136-sketcher-drag-and-dof-chip.md").read_text(
        encoding="utf-8")
    assert "AC2" in entry
    for phrase in ("flips 0", "console", "screenshot", "prediction"):
        assert phrase in entry.lower(), \
            f"the slice-10 evidence does not mention {phrase!r}"


# ------------------------------------------------------------------- AC3
def rectangle() -> dict:
    """A fully-constrained rectangle: six constraints, indices 0-5."""
    return {
        "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "b", "x": 50.0, "y": 1.0},
                   {"name": "c", "x": 51.0, "y": 30.0},
                   {"name": "d", "x": 1.0, "y": 29.0}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"},
                  {"name": "cd", "p1": "c", "p2": "d"},
                  {"name": "da", "p1": "d", "p2": "a"}],
        "constraints": [{"type": "horizontal", "ln": "ab"},
                        {"type": "vertical", "ln": "bc"},
                        {"type": "horizontal", "ln": "cd"},
                        {"type": "vertical", "ln": "da"},
                        {"type": "distance", "p": "a", "q": "b", "d": 50.0},
                        {"type": "distance", "p": "a", "q": "d", "d": 30.0}],
    }


def test_ac3_a_redundant_constraint_names_the_constraint_that_was_added(tools):
    """**AC3** — adding a redundant constraint to a fully-constrained
    rectangle returns `over_constrained` with the dependent set naming
    **constraint #6, the one that was just added**.

    That is only true with declaration-order greedy selection: column-pivoted
    QR, the textbook method, blamed `#3 vertical` — an innocent original — in
    two of three measured cases (changelog 0129). `dof` is 0, never negative.
    And redundant-but-consistent is **not** an error.
    """
    spec = rectangle()
    spec["constraints"].append({"type": "parallel", "l1": "ab", "l2": "cd"})
    out = call(tools, spec)

    assert out["ok"] is True                      # still solves, still fine
    diag = out["diagnostics"]
    assert diag["status"] == "over_constrained"
    assert diag["dof"] == 0
    assert [c["index"] for c in diag["redundant"]] == [6]
    assert diag["redundant"][0]["type"] == "parallel"
    assert diag["conflicting"] == []

    # …and a contradictory one is an error, carrying the same block
    bad = rectangle()
    bad["constraints"].append({"type": "distance", "p": "a", "q": "b",
                               "d": 60.0})
    with pytest.raises(ValidationError) as exc:
        call(tools, bad)
    conflicting = exc.value.details["diagnostics"]["conflicting"]
    assert [c["index"] for c in conflicting] == [6]
    assert "not necessarily the unique culprit" in str(exc.value)


# ------------------------------------------------------------------- AC4
def test_ac4_an_under_constrained_sketch_reports_dof_and_free_entities(http):
    """**AC4 (payload half)** — over the route, because that is what the chip
    reads: `dof > 0`, `status: under_constrained`, and `free_entities` naming
    the corners that can still move."""
    spec = rectangle()
    spec["constraints"] = spec["constraints"][:4]      # drop both distances
    out = http.post("/api/sketch/solve", json={
        "entities": entities_of(spec), "constraints": spec["constraints"],
    }).json()

    assert out["ok"] is True
    diag = out["diagnostics"]
    assert diag["status"] == "under_constrained"
    assert out["dof"] == diag["dof"] == 2
    assert diag["dof"] == diag["n_params"] - diag["rank"]
    assert set(diag["free_entities"]), "an under-constrained sketch names none"
    assert set(diag["free_entities"]) <= {"b", "c", "d"}


def test_ac4_the_dof_chip_is_wired_into_the_shipped_frontend():
    """The structural half of AC4 and AC7, and its honest limit.

    The evidence checks below assert that a changelog *says* a browser session
    happened, which is exactly as strong as the prose: deleting the chip would
    have left them green. This reads the shipped module and asserts the
    surfaces those entries claim are still defined and still wired.

    What it does **not** prove: that any of it renders, or that the console is
    clean. Those need a browser; they were driven for real in slices 9-13 and
    the changelogs are the record.
    """
    sketcher = (FRONTEND / "sketcher.js").read_text(encoding="utf-8")
    for surface in ("function chipState(", "function onDofClick(",
                    "function highlightSet(", "fully constrained",
                    "over-constrained", "conflicting"):
        assert surface in sketcher, f"sketcher.js no longer has {surface!r}"
    # the chip's honesty rules: a dependent set is not the unique culprit, and
    # a cached verdict says so
    assert "not necessarily the unique culprit" in sketcher
    assert 'diagnostics_source === "cached"' in sketcher


# ------------------------------------------------------------------- AC5
@pytest.mark.slow
@pytest.mark.timeout(900)
def test_ac5_a_sketch_on_the_enclosures_top_face_rebuilds_green(tools, kernel,
                                                                tmp_path):
    """**AC5** — sketch on the prototyping enclosure's largest planar face,
    anchored to one of that face's **own projected edges**, emitted through the
    tool, and rebuilt as a pad on the real part; the face reference and its
    caveat are in the script, and the block round-trips.

    The example is read and rebuilt from a **copy**: `examples/` is never
    mutated. The seed sits next to the edge it references — the solver
    converges to the *nearest* solution, and a seed 90 deg from the answer is
    how a chain of perpendiculars diverges.
    """
    script = _read(ENCLOSURE)
    planes, _ = _planes(kernel, script)
    face = _biggest(planes)
    info = planes[face]
    ents = reference_entities(info["refs"])
    edge = ents["lines"][0]["name"]
    plane = {**info, "part": "build(p)"}
    spec = {
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
            {"type": "point_on_line", "p": "a", "ln": edge},
            {"type": "distance", "p": "a", "q": f"{edge}_a", "d": 20.0},
            {"type": "parallel", "l1": "n1", "l2": edge},
            {"type": "distance", "p": "a", "q": "b", "d": 12.0},
            {"type": "distance", "p": "b", "q": "c", "d": 8.0},
            {"type": "perpendicular", "l1": "n1", "l2": "n2"},
            {"type": "perpendicular", "l1": "n2", "l2": "n3"},
            {"type": "perpendicular", "l1": "n3", "l2": "n4"},
        ],
    }
    out = call(tools, spec, plane=plane, emit="function", persist="face_pad")
    assert out["ok"] is True, out["diagnostics"]
    assert out["dof"] == 0
    assert out["emit"]["warnings"] == []
    code = out["emit"]["code"]

    # the provenance the design demands: the basis, the face, and the caveat
    assert f"agentcad sketch on face {face} of build(p)" in code
    assert "Re-pick the face" in code and "mesh-order ordinals" in code
    assert "BuildSketch(Plane(origin=" in code and "Plane.XY" not in code
    # …and the block round-trips, plane included
    block = parse_blocks(code)[0]
    assert block["status"] == "ok"
    assert block["spec"]["plane"]["face_index"] == face
    # projected references constrain but are never emitted as geometry
    for name in [line["name"] for line in ents["lines"]]:
        assert name not in block["code"]

    body = block["code"].replace("def sketch_face_pad():",
                                 "def sketch_profile():")
    pad = (script.rstrip("\n") + "\n\n" + body + "\n\n"
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


# ------------------------------------------------------------------- AC6
@pytest.mark.parametrize("name", sorted(CASES))
def test_ac6_the_v1_corpus_is_identical_through_the_tool_surface(tools, name):
    """**AC6 (compatibility half)** — every v1 case, through the registered
    tool rather than through `solve_sketch` directly, still returns the
    coordinates captured from the *shipped* solver on 2026-08-12 to 1e-9.

    The corpus module asserts the library call; this asserts the surface an
    agent actually reaches, including the tool's own error policy — which is
    the layer where "over-constrained is not an error" could have broken
    compatibility without the corpus noticing.
    """
    spec = CASES[name]
    want = EXPECTED[name]
    out = call(tools, spec)

    assert out["ok"] is True
    assert out["n_params"] == want["n_params"]
    assert out["n_residuals"] == want["n_residuals"]
    assert out["max_residual"] < 1e-9
    for pname, (x, y) in want["points"].items():
        assert out["points"][pname]["x"] == pytest.approx(x, abs=1e-9)
        assert out["points"][pname]["y"] == pytest.approx(y, abs=1e-9)
    for cname, (cx, cy, r) in want["circles"].items():
        got = out["circles"][cname]
        assert got["cx"] == pytest.approx(cx, abs=1e-9)
        assert got["cy"] == pytest.approx(cy, abs=1e-9)
        assert got["r"] == pytest.approx(r, abs=1e-9)
    # FR3's frozen keys are still a subset — PRD-009 may only add
    assert {"ok", "max_residual", "n_params", "n_residuals", "dof", "nfev",
            "solve_ms", "points", "circles"} <= set(out)


def test_ac6_solve_ms_is_a_duration_even_when_the_sketch_has_arcs():
    """**AC6, and a bug this slice found.** `solve_ms` is one of FR3's nine
    frozen result keys, and it was **wrong for every sketch containing an arc**
    from slice 5 until slice 14: the arc-output loop reused the local name
    `t1`, which is also the solve's own end timestamp, so the reported duration
    was `(an angle in radians − t0)` — about −183 000 000 ms on this machine.

    FR3 freezes the keys' *meanings*, not just their presence, so the corpus
    could not catch it (it asserts coordinates) and neither could a one-sided
    budget assertion (a negative clears any ceiling). Both bounds are checked
    here, with and without arcs.
    """
    for spec in (rectangle(), cam_lobe()):
        out = solve_sketch(spec)
        assert out["ok"] is True
        assert 0.0 < out["solve_ms"] < 5_000.0, out["solve_ms"]
        assert out["diagnostics"]["analysis_ms"] >= 0.0


def test_ac6_the_full_suite_count_is_cited():
    """**AC6 (suite half)** — "full suite green, count cited" is a claim about
    a run, so this is the evidence check that the count is on the record in
    the close-out changelog (the PRD-004 AC10 / PRD-008 AC9 precedent).

    It stays an evidence check deliberately: recomputing the number here would
    mean running the full suite from inside the full suite, and
    `--collect-only` counts *cases*, which is not what `make test` reports.
    """
    entry = CHANGELOG / "0141-prd-009-completed.md"
    assert entry.is_file(), "the PRD-009 close-out changelog entry is missing"
    text = entry.read_text(encoding="utf-8")
    assert "make test" in text and "passed" in text
    assert any(token.isdigit() and len(token) >= 4
               for token in text.replace(",", " ").split()), \
        "the close-out entry does not cite a suite count"

    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    if latest != entry:
        recent = latest.read_text(encoding="utf-8")
        assert "make test" in recent and "passed" in recent, (
            f"{latest.name} is the newest changelog entry and cites no suite "
            "count; every entry that lands work must cite one")


# ------------------------------------------------------------------- AC7
def test_ac7_browser_half_evidence_is_recorded():
    """**AC7** — "draw a profile, constrain it, drag it, finish; script diff
    visible, rebuild green, zero console errors" was driven for real across
    slices 9-13 (headless Chrome, screenshots, a real rebuild each time).
    These are the records; this test fails if one is removed."""
    records = {
        "0135-sketcher-ui-entities.md": ("arc", "spline", "slot", "console"),
        "0136-sketcher-drag-and-dof-chip.md": ("drag", "rebuild", "console"),
        "0138-sketch-ellipses.md": ("ellipse", "console"),
        "0139-sketch-on-face.md": ("sketch on face", "rebuild", "console"),
        "0140-sketch-roundtrip-spec.md": ("diverg", "rebuild", "console"),
    }
    for name, phrases in records.items():
        text = (CHANGELOG / name).read_text(encoding="utf-8").lower()
        for phrase in phrases:
            assert phrase in text, f"{name} does not mention {phrase!r}"
        assert ("console errors: none" in text
                or "zero console errors" in text), \
            f"{name} does not record a clean console"


def test_ac7_the_sketcher_surfaces_the_changelogs_claim_exist():
    """The structural gate under AC7: the drag protocol, the emitter call, the
    sketch-on-face entry point and the round-trip banner are all still there,
    and emission is still the server's."""
    sketcher = (FRONTEND / "sketcher.js").read_text(encoding="utf-8")
    api = (FRONTEND / "api.js").read_text(encoding="utf-8")
    main = (FRONTEND / "main.js").read_text(encoding="utf-8")

    for surface in ("function scheduleDragFrame(", "function sendDragFrame(",
                    "function endDrag(", "function openBlock(",
                    "function specToModel(", "function refreshBlocks(",
                    "export function openOnFace(",
                    "async function insertSnippet("):
        assert surface in sketcher, f"sketcher.js no longer defines {surface!r}"
    # emission is the server's, and there is no second emitter in the browser
    assert 'emit: "function"' in sketcher
    for gone in ("function buildSnippet(", "function findChains(",
                 "function fmtNum("):
        assert gone not in sketcher, \
            f"{gone!r} is back in the browser — there must be one emitter"
    assert "solveSketch:" in api and "sketchBlocks:" in api
    assert "sketcher.openOnFace(" in main


def test_ac7_the_round_trip_survives_the_whole_stack(http):
    """AC7's "finish" step, as data: solve → persist → parse → re-solve →
    re-emit, over the route the browser uses, byte for byte."""
    spec = rectangle()
    body = {"entities": entities_of(spec), "constraints": spec["constraints"]}
    first = http.post("/api/sketch/solve",
                      json={**body, "emit": "function",
                            "persist": "profile"}).json()["emit"]["code"]

    parsed = http.post("/api/sketch/blocks",
                       json={"script": "import build123d\n" + first}).json()
    assert parsed["next_name"] == "profile2"
    block = parsed["blocks"][0]
    assert block["status"] == "ok"

    again = http.post("/api/sketch/solve", json={
        "entities": block["spec"]["entities"],
        "constraints": block["spec"]["constraints"],
        "emit": "function", "persist": block["name"],
    }).json()["emit"]["code"]
    assert again == first

    # a hand edit to the code is divergence, and the spec is not overwritten
    # edit the *code*, not the spec line: `make_face()` appears only there
    edited = http.post("/api/sketch/blocks", json={
        "script": first.replace("make_face()", "make_face()  # by hand"),
    }).json()["blocks"][0]
    assert edited["status"] == "diverged"
    assert edited["spec"]["constraints"] == spec["constraints"]


def test_the_solver_and_the_emitter_run_in_the_server_process():
    """Not an AC, but the constraint every one of them is built on: the sketch
    stack runs in the **server** process, which may not import build123d."""
    import subprocess
    import sys
    code = ("import sys;"
            "import agentcad.toolkit.sketch as s, agentcad.core.sketch_emit as e;"
            "r = s.solve_sketch({'points': [{'name': 'a', 'x': 0, 'y': 0,"
            " 'fixed': True}, {'name': 'b', 'x': 9, 'y': 0}],"
            " 'constraints': [{'type': 'distance', 'p': 'a', 'q': 'b',"
            " 'd': 10}]});"
            "assert r['ok'];"
            "e.emit(r, {'points': [], 'constraints': []});"
            "print('OCP' in sys.modules or 'build123d' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "False", out.stdout
