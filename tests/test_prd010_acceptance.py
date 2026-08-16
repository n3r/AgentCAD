"""PRD-010 acceptance criteria — one named test per criterion (slice 14).

The mechanics are covered in depth by the nine modules this PRD grew:
`tests/test_hole_standards.py` (the vendored tables and their provenance),
`tests/test_patterns.py`, `tests/test_holes.py`, `tests/test_features.py`,
`tests/test_hole_metadata.py` (the harvest, the sidecar and the seam),
`tests/test_drawing_holes.py`, `tests/test_sheetmetal.py` (the v1 corpus,
unchanged) and `tests/test_sheetmetal_v2.py`, plus
`tests/test_examples_golden.py` — written **before** any of it existed so
slice 7's rewrite of the bundled examples could not be graded by the person
making it — and `tests/test_tools_holes.py` for the hole-on-face tool.

This file is the **contract** layer: it walks each acceptance criterion of
`docs/prd/completed/PRD-010-feature-toolkit-ii.md` through the surfaces a human
and an agent actually touch — the registered tools, a real service rebuild and
a real kernel build — so a reviewer can map AC → test without reading the unit
suites.

| AC | Test |
|----|------|
| AC1 | ``test_ac1_the_rewritten_construction_parts_are_byte_identical`` +
        ``test_ac1_the_cache_key_necessarily_moved_and_the_prd_says_so`` |
| AC2 | ``test_ac2_a_tapped_hole_reaches_the_drawing_as_its_designation`` |
| AC3 | ``test_ac3_hole_standards_returns_the_published_iso_273_diameters`` |
| AC4 | ``test_ac4_a_partial_flange_bracket_gets_relief_and_a_flat_pattern``
        (the SVG half is
        ``tests/test_sheetmetal_v2.py::test_ac4_partial_flange_bracket_exports_a_flat_pattern_with_reliefs``,
        whose render was rasterised and looked at — changelog 0157) |
| AC5 | ``test_ac5_a_polar_pattern_of_one_tapped_hole_is_one_group`` |
| AC6 | ``test_ac6_a_hole_that_misses_the_part_warns_and_names_the_instance`` +
        ``test_ac6_impossible_geometry_is_a_script_error_with_the_failing_line`` |
| AC7 | ``test_ac7_two_parts_on_one_warm_worker_do_not_cross_contaminate`` |
| AC7b| ``test_ac7b_the_same_part_twice_on_a_warm_worker_is_identical`` |
| AC8 | ``test_ac8_the_examples_tree_is_untouched_by_a_rebuild`` +
        ``test_ac8_the_full_suite_count_is_cited`` |
| FR14| ``test_fr14_the_face_card_hole_controls_are_wired_into_the_shipped_frontend`` |

**AC7b is not in the PRD.** It was added by the design spec because the PRD's
own AC7 cannot observe the failure its harvest design would have had: a rebuild
of an *unchanged* part skips `build(p)` entirely, so a registry-drain harvest
returns nothing and says so to nobody. It is graded here alongside the criteria
the PRD wrote.

**The browser half is an evidence check, deliberately.** FR14's card was driven
for real (headless Chrome on a scratch server, screenshots, zero console
errors) in slice 13 and changelog 0159 is the record; the test above it is a
*structural* gate that fails if the wiring is deleted, because an evidence
check alone is exactly as strong as the prose it reads (the PRD-008 AC1 /
PRD-009 AC7 pattern).
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

import pytest

from agentcad.core.tools import build_registry
from agentcad.toolkit import hole_standards

from .conftest import make_test_service
from .test_examples_golden import (
    EXAMPLES_DIR,
    GOLDENS,
    assert_matches_golden,
    measure_part,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "docs" / "changelog"
FRONTEND = REPO_ROOT / "frontend"
PRD = (REPO_ROOT / "docs" / "prd" / "completed"
       / "PRD-010-feature-toolkit-ii.md")


@pytest.fixture
def demo(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    # Build the registry once here, because that is what installs the packs'
    # seams: `tools_holes.register` wraps `service._rebuild` and
    # `service.get_part`, and a service nobody registered onto has no `holes`
    # key at all. A real server always has them; a test that skipped this
    # would be grading a configuration nobody runs.
    build_registry(service)
    service.create_project("demo")
    return service


def _part(demo, part_id: str, script: str) -> dict:
    demo.create_part("demo", part_id, script=script)
    return demo._rebuild("demo", part_id)


# ------------------------------------------------------------------- AC1

@pytest.mark.integration
def test_ac1_the_rewritten_construction_parts_are_byte_identical(
        kernel, tmp_path):
    """**AC1 (restated)** — "identical geometry" needs saying which identity.

    Measured in slice 1 (changelog 0147): two constructions with the same
    volume to 6 dp and the same face count can tessellate to **different
    bytes**, so the harness asserts both halves. (a) every metric at
    `rel=1e-9`, (b) a byte-identical `.acm` payload. Both hold on all three
    rewritten parts (changelog 0153).
    """
    service = make_test_service(tmp_path / "projects", kernel)
    src = EXAMPLES_DIR / "construction"
    dest = tmp_path / "copy" / "construction"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    name = service.open_project(str(dest))["name"]

    parts = sorted(part for proj, part in GOLDENS if proj == "construction")
    assert parts, "the construction goldens vanished"
    for part_id in parts:
        script = (dest / "parts" / f"{part_id}.py").read_text(encoding="utf-8")
        # (0) the criterion is about the parts the helpers rewrote, so the
        # test says the rewrite is actually there rather than passing a
        # byte-identity check trivially on unchanged source.
        assert "agentcad.toolkit" in script, (
            f"{part_id} no longer uses the toolkit — AC1 grades a rewrite")
        assert_matches_golden(measure_part(service, name, part_id),
                              GOLDENS[("construction", part_id)],
                              f"construction/{part_id}")


def test_ac1_the_cache_key_necessarily_moved_and_the_prd_says_so():
    """**AC1's other half is impossible, and is recorded as such.**

    The PRD asks for "the same content-hash mesh-cache entries". The key is
    `sha256({content, params, density, tolerance, format})` where `content`
    **is the script text**, so *any* rewrite mints a new key by construction.
    This asserts the two things that make that honest rather than quiet: the
    cache key really does hash the script, and the PRD's divergence section
    says so.
    """
    from agentcad.core import service as service_module

    source = Path(service_module.__file__).read_text(encoding="utf-8")
    assert "_cache_key" in source
    text = PRD.read_text(encoding="utf-8")
    assert "cache key" in text.lower()
    assert "script text" in text, (
        "the PRD's divergence section must state why AC1's cache-key half "
        "cannot be delivered")


# ------------------------------------------------------------------- AC2

TAPPED_PLATE = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(120, 120, p.t)
    part, _r, _w = holes.tapped(part, [(0, 0)], "M5", depth=12)
    return part
'''


@pytest.mark.integration
def test_ac2_a_tapped_hole_reaches_the_drawing_as_its_designation(demo):
    """**AC2** — the SVG text and the `from_metadata` flag.

    A ⌀4.2 circle on a sheet could be a drilled hole or an M5 tap. Projection
    cannot tell; the record can, and the drawing prints what the hole *is*.
    """
    demo.create_part("demo", "plate", script=TAPPED_PLATE)
    result = build_registry(demo).call(
        "generate_drawing", {"project": "demo", "part_id": "plate"})
    assert "error" not in result, result

    group = result["detected"]["hole_groups"][0]
    assert group["from_metadata"] is True
    assert group["designation"] == "M5×0.8 - 6H ↧12"
    assert group["family"] == "tapped"
    svg = (demo.store.exports_dir("demo") / "plate_drawing.svg"
           ).read_text(encoding="utf-8")
    assert "M5×0.8 - 6H ↧12" in svg
    assert result["detected"]["hole_warnings"] == []


# ------------------------------------------------------------------- AC3

def test_ac3_hole_standards_returns_the_published_iso_273_diameters(demo):
    """**AC3**, through the registered tool an agent actually calls.

    ISO 273:1979 M5 is 5.3 / 5.5 / 5.8 — transcribed from two independent
    published sources (changelog 0148) and asserted here as literals, because
    a table that only agrees with itself proves nothing.
    """
    registry = build_registry(demo)
    answer = registry.call("hole_standards",
                           {"size": "M5", "family": "clearance"})
    assert "error" not in answer, answer
    assert answer["fits"] == {"fine": 5.3, "medium": 5.5, "coarse": 5.8}
    assert answer["designations"]["medium"] == "⌀5.5"
    assert answer["standard"].startswith("ISO 273")
    assert len(answer["sources"]) >= 2, (
        "every row ships with two independent published sources")

    # one tap-drill row and one counterbore row, as the criterion asks
    tapped = registry.call("hole_standards", {"size": "M5", "family": "tapped"})
    assert tapped["pitch"] == 0.8 and tapped["tap_drill"] == 4.2
    cbore = registry.call("hole_standards",
                          {"size": "M5", "family": "counterbore"})
    assert cbore["head_d"] == 8.5 and cbore["head_h"] == 5.0

    # and a request the tables cannot answer is a caller error, not a 500
    bad = registry.call("hole_standards", {"size": "M4.5",
                                           "family": "clearance"})
    assert bad["error"]["type"] == "validation_error"


# ------------------------------------------------------------------- AC4

AC4_BRACKET = '''\
from agentcad.toolkit.sheetmetal import SheetPart

PARAMS = {"thick": {"default": 2.0, "min": 0.5, "max": 6.0, "unit": "mm",
                    "description": "sheet thickness"}}

def _sheet(p):
    return (SheetPart(p.thick)
            .base(60, 40)
            .flange("front", 90, 30, inner_radius=3.0,
                    start=15.0, width=30.0, relief="round"))

def build(p):
    return _sheet(p).fold()

def flat_pattern(p):
    sp = _sheet(p)
    return sp.unfold(), sp.bend_lines()
'''


@pytest.mark.integration
def test_ac4_a_partial_flange_bracket_gets_relief_and_a_flat_pattern(demo):
    """**AC4** — a partial-width flange earns automatic bend relief; `fold()`
    is valid, `unfold()` round-trips *with the relief cuts*, the bend lines
    span the tab and the `flat_pattern` export renders.

    The visually-verified SVG half of the criterion is
    `tests/test_sheetmetal_v2.py::test_ac4_partial_flange_bracket_exports_a_flat_pattern_with_reliefs`;
    the render was rasterised and inspected in slice 11 (changelog 0157) — two
    keyhole notches straddling the bend line, and a `90° R3` bend line 30 mm
    long, not 60.
    """
    result = _part(demo, "tab_bracket", AC4_BRACKET)
    assert result["ok"], result
    assert result["metrics"]["n_solids"] == 1
    assert result["metrics"]["is_valid"] is True
    # spike S9's `fold_round` on exactly these parameters
    assert result["metrics"]["volume_mm3"] == pytest.approx(6920.854, abs=1e-3)

    flat = build_registry(demo).call(
        "flat_pattern", {"project": "demo", "part_id": "tab_bracket",
                         "format": "svg"})
    assert "error" not in flat, flat
    # one bend line, spanning the 30 mm tab and not the 60 mm edge
    assert flat["n_bend_lines"] == 1
    assert flat["flat_bbox_mm"]["w"] == pytest.approx(60.0, abs=0.1)
    svg = (demo.store.exports_dir("demo") / "tab_bracket_flat.svg"
           ).read_text(encoding="utf-8")
    assert svg.startswith("<svg") and 'id="BEND"' in svg
    assert "90&#176; R3" in svg, "the bend line carries its angle and radius"

    # and the relief is really cut: the same bracket with `relief="tear"`
    # removes nothing, so the difference is the two notches.
    torn = _part(demo, "tab_bracket_tear",
                 AC4_BRACKET.replace('relief="round"', 'relief="tear"'))
    assert torn["ok"], torn
    removed = (torn["metrics"]["volume_mm3"]
               - result["metrics"]["volume_mm3"])
    assert removed == pytest.approx(56.1372, abs=1e-3), (
        "the two round reliefs remove 56.14 mm³ (spike S9, changelog 0157)")


# ------------------------------------------------------------------- AC5

POLAR_TAPPED = '''\
from build123d import Box
from agentcad.toolkit import holes, patterns

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(140, 140, p.t)
    part, _r, _w = holes.tapped(part, patterns.bolt_circle(45, 8), "M5",
                                depth=10)
    return part
'''


@pytest.mark.integration
def test_ac5_a_polar_pattern_of_one_tapped_hole_is_one_group(demo):
    """**AC5** — one record with `count: 8`, and a callout that reads
    `8× …`. FR3 falls out of the group record for free: the points path is one
    call, so it is one boolean and one record whatever the pattern.
    """
    result = _part(demo, "plate", POLAR_TAPPED)
    assert result["ok"], result
    records = result["holes"]
    assert len(records) == 1, "a pattern is ONE group, not N unrelated records"
    record = records[0]
    assert record["count"] == 8
    assert len(record["positions"]) == 8 and len(record["centers"]) == 8
    assert record["designation"] == "M5×0.8 - 6H ↧10"

    drawing = build_registry(demo).call(
        "generate_drawing", {"project": "demo", "part_id": "plate"})
    assert "error" not in drawing, drawing
    svg = (demo.store.exports_dir("demo") / "plate_drawing.svg"
           ).read_text(encoding="utf-8")
    assert "8× M5×0.8 - 6H ↧10" in svg


# ------------------------------------------------------------------- AC6

OVERLAPPING_FLANGES = '''\
from agentcad.toolkit.sheetmetal import SheetPart

PARAMS = {"thick": {"default": 2.0, "min": 0.5, "max": 6.0, "unit": "mm",
                    "description": "sheet thickness"}}

def build(p):
    sp = SheetPart(p.thick).base(60, 40)
    sp.flange("front", 90, 20, start=0.0, width=40.0)
    sp.flange("front", 90, 20, start=30.0, width=20.0)
    return sp.fold()
'''

UNKNOWN_SIZE = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 10.0, "min": 5.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(80, 60, p.t)
    part, _r, _w = holes.clearance(part, [(0, 0)], "M4.5")
    return part
'''


def test_ac6_a_hole_that_misses_the_part_warns_and_names_the_instance():
    """**AC6, the warning half.** OCCT does *not* give us this: a cut placed
    entirely off the part is a silent success — 0.0 volume delta, `is_valid`
    True, nothing raised (measured through the worker, changelog 0149). So the
    helper measures engagement itself, and the warning names the **index**,
    never a count.
    """
    from build123d import Box
    from agentcad.toolkit import holes

    plate = Box(120, 80, 10)
    out, records, warning = holes.clearance(
        plate, [(0, 0), (30, 0), (9999, 9999)], "M5")

    assert warning is not None, "a misplaced instance must not be silent"
    assert "[2]" in warning, f"the warning must name the instance: {warning}"
    # and the part is still returned, with the two holes that did land
    assert plate.volume - out.volume == pytest.approx(
        2 * math.pi * 2.75 ** 2 * 10, rel=1e-9)
    assert records[0]["instances"][2]["status"] == "missed"


@pytest.mark.integration
def test_ac6_impossible_geometry_is_a_script_error_with_the_failing_line(demo):
    """**AC6, the failure half.** A request no geometry can honour is not a
    warning: it raises where it is called, and reaches the client as the
    ordinary structured `script_error` with `details.line` — the
    `toolkit/specs.py` convention, so an agent can jump to the line.
    """
    for part_id, script, needle in (
            ("overlap", OVERLAPPING_FLANGES, "overlap"),
            ("badsize", UNKNOWN_SIZE, "M4.5"),
    ):
        result = _part(demo, part_id, script)
        assert result["ok"] is False, (part_id, result)
        error = result["error"]
        assert error["type"] == "script_error", (part_id, error)
        assert needle in error["message"], (part_id, error["message"])
        assert isinstance(error["details"].get("line"), int), (
            f"{part_id}: a script error must name the failing line")
        assert error["details"]["line"] > 0


# ------------------------------------------------------------- AC7 / AC7b

HOLED_A = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 10.0, "min": 5.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(100, 80, p.t)
    part, _r, _w = holes.clearance(part, [(-20, 0), (20, 0)], "M5")
    return part
'''

HOLED_B = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 14.0, "min": 5.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(90, 90, p.t)
    part, _r, _w = holes.tapped(part, [(0, 0)], "M12", depth=10)
    return part
'''


@pytest.mark.integration
def test_ac7_two_parts_on_one_warm_worker_do_not_cross_contaminate(kernel):
    """**AC7**, and it is now a property rather than a discipline.

    The PRD's technical approach had the helpers append to a module-level
    registry the worker drained after `build(p)`. Records riding the returned
    shape remove the shared mutable state entirely, so there is nothing to
    contaminate — and the third call proves the first part's answer did not
    move when another part was built between.
    """
    first = kernel.request("hole_records", {"script": HOLED_A, "params": {}})
    second = kernel.request("hole_records", {"script": HOLED_B, "params": {}})
    third = kernel.request("hole_records", {"script": HOLED_A, "params": {}})

    assert [r["size"] for r in first["holes"]] == ["M5"]
    assert [r["size"] for r in second["holes"]] == ["M12"]
    assert third["holes"] == first["holes"]
    assert first["warnings"] == second["warnings"] == []


@pytest.mark.integration
def test_ac7b_the_same_part_twice_on_a_warm_worker_is_identical(kernel):
    """**AC7b** — added by the design spec, because AC7 as written cannot see
    it. The second build of an *unchanged* part takes the worker's
    `_SHAPE_CACHE` and never runs `build(p)`; a registry-drain harvest returns
    nothing there, silently. The test says out loud that the second call
    really was the cache-hit path (`measured is False`), or it would be
    grading the wrong thing.
    """
    params = {"t": 11.0}
    first = kernel.request("hole_records", {"script": HOLED_A,
                                            "params": params})
    second = kernel.request("hole_records", {"script": HOLED_A,
                                             "params": params})
    assert first["measured"] is True and second["measured"] is False
    assert first["holes"] == second["holes"] and len(first["holes"]) == 1
    assert first["dropped"] == second["dropped"] == 0
    assert second["warnings"] == []


# ------------------------------------------------------------------- AC8

def _fingerprint(root: Path) -> dict[str, str]:
    """sha256 of every file under *root*, ignoring the derived directories a
    build is allowed to create in a project it opens."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in (".cache", "exports", ".history"):
            continue
        if any(part in (".cache", "exports", "__pycache__")
               for part in rel.parts):
            continue
        if path.is_file():
            out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.mark.integration
def test_ac8_the_examples_tree_is_untouched_by_a_rebuild(kernel, tmp_path):
    """**AC8's second half** — "examples repo state untouched by default".

    Slice 7's rewrite is the single deliberate exception and it is *committed*
    source; what must never happen is a test or a rebuild mutating
    `examples/` in place. Every examples test copies first, and this asserts
    the tree is byte-identical across a full rebuild of a copy.
    """
    before = _fingerprint(EXAMPLES_DIR)
    assert before, "the examples tree is empty?"

    service = make_test_service(tmp_path / "projects", kernel)
    dest = tmp_path / "copy" / "construction"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(EXAMPLES_DIR / "construction", dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    name = service.open_project(str(dest))["name"]
    for part in sorted(p for proj, p in GOLDENS if proj == "construction"):
        assert service._rebuild(name, part)["ok"]

    assert _fingerprint(EXAMPLES_DIR) == before, (
        "a rebuild mutated examples/ — every examples test must run on a copy")

    # …and the deliberate exception is visible, so "untouched" cannot be
    # satisfied by never having done the rewrite.
    gusset = (EXAMPLES_DIR / "construction" / "parts" / "gusset_plate.py"
              ).read_text(encoding="utf-8")
    assert "patterns.grid" in gusset and "holes.drill" in gusset


def test_ac8_the_full_suite_count_is_cited():
    """**AC8's first half** — "full suite green" is a claim about a run, so
    this is the evidence check that the count is on the record in the
    close-out changelog (the PRD-004 AC10 / PRD-008 AC9 / PRD-009 AC6
    precedent).

    It stays an evidence check deliberately: recomputing the number would mean
    running the full suite from inside the full suite, and `--collect-only`
    counts *cases*, which is not what `make test` reports.
    """
    entry = CHANGELOG / "0160-prd-010-completed.md"
    assert entry.is_file(), "the PRD-010 close-out changelog entry is missing"
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


# ------------------------------------------------------------------- FR14

def test_fr14_the_face_card_hole_controls_are_wired_into_the_shipped_frontend():
    """**FR14's structural gate.** The browser session is evidence
    (changelog 0159); this fails if the wiring is deleted, which the prose
    alone cannot.

    It also pins the one rule the card must not lose: the size list comes from
    the `hole_standards` tool, never from a literal in JS, so the picker
    cannot offer a size the tables do not have.
    """
    main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")
    css = (FRONTEND / "css" / "app.css").read_text(encoding="utf-8")

    assert "renderHoleControls" in main and "applyAddHoles" in main
    assert 'callTool("add_holes"' in main
    assert 'callTool("hole_standards"' in main
    assert "renderHoleControls(body" in main, (
        "the controls must be rendered by the face card, not orphaned")
    assert ".facecard-holes" in css

    # No hard-coded size list: the only sizes in the file come from the tool.
    for size in ("M2.5", "M16", "M36"):
        assert size not in main, (
            f"{size} is a literal in main.js — the picker must read "
            "`hole_standards`, not embed a table")


def test_fr14_the_add_holes_tool_is_registered_with_its_schema(demo):
    """The agent half of FR14: `add_holes` is an ordinary registered tool, so
    an agent may make the same edit the card makes."""
    tool = build_registry(demo).get("add_holes")
    assert tool is not None, "add_holes is not registered"
    assert set(tool.input_schema["required"]) == {
        "project", "part_id", "points", "family", "size"}
    for optional in ("plane", "face_index", "fit", "std", "depth"):
        assert optional in tool.input_schema["properties"]


# --------------------------------------------------- the PRD's own record

def test_the_prd_records_every_divergence_the_design_measured():
    """The four items the design spec named as "cannot be delivered as
    written", plus the two the slices found, have to be **in the PRD** — the
    document a reader reaches first — not only in a changelog nobody opens.
    """
    text = PRD.read_text(encoding="utf-8")
    assert "**Status:** implemented" in text
    for needle in (
            "cache key",          # AC1's impossible half
            "registry",           # FR6's drain, replaced by the carrier
            "PRD-016",            # FR14's unbuilt host
            "teardrop",           # FR11's refused hem
            "AC7b",               # the criterion the design added
            "top view",           # the inherited drawing limitation
    ):
        assert needle in text, f"the PRD does not record {needle!r}"
    # every AC is named in the verification table with the test that proves it
    for ac in ("AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7", "AC8"):
        assert ac in text
    assert "test_ac4_partial_flange_bracket_exports_a_flat_pattern_with_reliefs" \
        in text, "AC4's row must name the test that renders the SVG"


def test_the_roadmap_points_at_the_moved_prd():
    """The roadmap's link is the one a reader follows. It pointed at
    `prd/pending/` while the file lived in `prd/in-progress/` for the whole of
    this PRD's build."""
    roadmap = (REPO_ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
    row = next(line for line in roadmap.splitlines()
               if line.startswith("| [010]"))
    assert "prd/completed/PRD-010-feature-toolkit-ii.md" in row, row
    assert "completed" in row
    assert PRD.is_file()
    assert not (REPO_ROOT / "docs" / "prd" / "in-progress"
                / "PRD-010-feature-toolkit-ii.md").exists()


def test_the_toolkit_module_that_must_stay_ocp_free_is_declared():
    """`hole_standards` is the THIRD OCP-free toolkit module (with `sketch`
    and `specs`) because the server's `hole_standards` tool imports it. The
    property is asserted in a fresh interpreter by
    `tests/test_toolkit_ocp_free.py`; this is the one-line reminder that the
    list is a contract."""
    probes = (REPO_ROOT / "tests" / "test_toolkit_ocp_free.py"
              ).read_text(encoding="utf-8")
    assert "hole_standards" in probes
    # and the data really is data: every shipped table parses and carries its
    # provenance header.
    data_dir = Path(hole_standards.__file__).parent / "data"
    files = sorted(data_dir.glob("*.json"))
    assert len(files) == 6, [f.name for f in files]
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["schema"] == 1
        assert len(doc["sources"]) >= 2, path.name
        assert doc["revision"] and doc["standard"] and doc["rows"]
