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
`docs/prd/*/PRD-010-feature-toolkit-ii.md` (the PRD moves from `in-progress/`
to `completed/` at merge, so this file locates it) through the surfaces a human
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
import re
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
PRD_NAME = "PRD-010-feature-toolkit-ii.md"


def _find_prd() -> Path:
    """Locate the PRD wherever it currently lives.

    A PRD moves from `in-progress/` to `completed/` at **merge**, not when the
    build finishes, so a test that hard-codes one directory is red for the
    whole review window (and again if anyone ever files it elsewhere). The
    contract this file grades is the PRD's *content*, not its filing cabinet.
    """
    prd_root = REPO_ROOT / "docs" / "prd"
    for stage in ("in-progress", "completed", "pending"):
        candidate = prd_root / stage / PRD_NAME
        if candidate.is_file():
            return candidate
    found = sorted(prd_root.rglob(PRD_NAME))
    assert found, f"{PRD_NAME} is not anywhere under {prd_root}"
    return found[0]


PRD = _find_prd()


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
    part, _r, _w = holes.tapped(part, [(0, 0)], "M5", depth=8)
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
    assert group["designation"] == "M5×0.8 - 6H ↧8"
    assert group["family"] == "tapped"
    svg = (demo.store.exports_dir("demo") / "plate_drawing.svg"
           ).read_text(encoding="utf-8")
    assert "M5×0.8 - 6H ↧8" in svg
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
        "THIS row ships with two independent published sources")
    # "every row does" is the claim the review disproved: the file-level list
    # is a union, and `corroborated` is the per-row answer.
    assert answer["corroborated"] is True

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
#
# The helpers below exist because a plain substring grep over `main.js`
# is not a gate: `"renderHoleControls" in main` is satisfied by the *comment*
# that names it, and the "no hard-coded size" rule was satisfied by needles
# (`M2.5`, `M16`, `M36`) that were absent while a literal `"M5"` sat in the
# defaulting line the rule is about (review 3, R21). So the wiring assertions
# below are made **inside the function that has to carry them**, and the
# behavioural half runs the shipped source in node.

MAIN_JS = FRONTEND / "js" / "main.js"

# `M5`, `M2.5`, `#10`, `1/4`, `1/4-20` — a whole string literal that IS a
# fastener designation. Deliberately a prohibition rather than a needle: a rule
# of the form "this text is absent" cannot be satisfied by unrelated text.
DESIGNATION = re.compile(r"^(?:M\d+(?:\.\d+)?|#\d+|\d+/\d+(?:-\d+)?)$")


def _strip_js_comments(text: str) -> str:
    """`text` with its comments blanked, string and template literals intact.

    Every structural assertion below runs on this, because a comment is not
    wiring: commenting the `renderHoleControls(body, planar)` call out left the
    old grep — and the first draft of this one — perfectly green.
    """
    out: list[str] = []
    i, n, quote = 0, len(text), ""
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
        elif ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            out.append("\n" * text.count("\n", i, n if end < 0 else end))
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _js_function(text: str, name: str) -> str:
    """The source of a top-level `function name(…)`, closer included, with its
    comments blanked.

    Ends at the first line that is exactly `}` — the repo's style for a
    top-level function — rather than counting braces, which a template
    literal or a comment can unbalance.
    """
    text = _strip_js_comments(text)
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", text, re.M)
    assert match, f"main.js has no top-level `function {name}(`"
    end = text.index("\n}\n", match.start())
    return text[match.start():end + 3]


def _hole_section(text: str) -> str:
    """Everything between the `hole on face` banner and the next one.

    Raw — comments and all — because this is what the node harness executes.
    """
    start = re.search(r"^// -+ hole on face.*$", text, re.M)
    assert start, "main.js has lost the `hole on face` section banner"
    end = re.search(r"^// -+ widgets\s*$", text[start.end():], re.M)
    assert end, "the `hole on face` section has no following banner"
    return text[start.end():start.end() + end.start()]


def _hole_form_reset_hook(text: str) -> str:
    """The name of the function the hole section resets its form from.

    Found through the wiring, not by name: whatever `onKeys` hands the
    `selectedPart` change that is *defined in the hole section* is the reset
    hook, so this fails if the reset is never subscribed at all.
    """
    section = _strip_js_comments(_hole_section(text))
    handlers = re.findall(r"onKeys\(\[([^\]]*)\],\s*([A-Za-z_$][\w$]*)\s*\)",
                          _strip_js_comments(text))
    owned = [name for keys, name in handlers
             if '"selectedPart"' in keys
             and re.search(rf"^function {re.escape(name)}\(", section, re.M)]
    assert owned, (
        "nothing in the hole section is subscribed to the `selectedPart` "
        "change — the hole form is module-level, so a depth or a set of "
        "points typed against one part is applied to the next one")
    return owned[0]


def test_fr14_the_face_card_hole_controls_are_wired_into_the_shipped_frontend():
    """**FR14's structural gate.** The browser session is evidence
    (changelog 0159); this fails if the wiring is deleted, which the prose
    alone cannot.

    Every assertion here is positional — it is made against the body of the
    function that must contain it — so no comment, string or unrelated literal
    elsewhere in `main.js` can stand in for the wiring.

    It also pins the rule the card must not lose: **a size the picker offers is
    a row of the `hole_standards` answer**, never a literal in JS. That is
    graded as a prohibition on designation-shaped string literals anywhere in
    the file, which is what the previous three-needle spelling only pretended
    to do.
    """
    main = MAIN_JS.read_text(encoding="utf-8")
    css = (FRONTEND / "css" / "app.css").read_text(encoding="utf-8")

    card = _js_function(main, "renderFaceCard")
    controls = _js_function(main, "renderHoleControls")
    drill = _js_function(main, "applyAddHoles")
    standards = _js_function(main, "holeStandardsFor")

    # the card renders the controls — not an orphaned function nobody calls
    assert re.search(r"\brenderHoleControls\(\s*body\b", card), (
        "renderFaceCard does not render the hole controls")
    assert re.search(r'className = "facecard-holes"', controls)
    assert ".facecard-holes" in css

    # the button is wired to the tool call, and the tool call is in the button's
    # function — not merely somewhere in the file
    assert re.search(r'addEventListener\(\s*"click",[^\n]*applyAddHoles\(',
                     controls), "the Drill button does not call applyAddHoles"
    assert re.search(r'callTool\(\s*"add_holes"', drill), (
        "applyAddHoles does not call the add_holes tool")
    assert re.search(r'callTool\(\s*"hole_standards"', standards), (
        "the size tables do not come from the hole_standards tool")

    # the picker's options are the fetched table's rows
    assert re.search(r"tables\.sizes\[", controls), (
        "renderHoleControls does not read its sizes from the fetched tables")
    assert re.search(r"\bsizes\.map\(", controls), (
        "the size options are not built from the fetched sizes")

    # …and no size is written in this file, in any spelling. (Prose may name
    # one — `M4.5` in the comment above `holeTables` is the rule being stated —
    # so this reads the code, not the comments.)
    code = _strip_js_comments(main)
    literals = (re.findall(r'"([^"\\\n]*)"', code)
                + re.findall(r"'([^'\\\n]*)'", code))
    offenders = sorted({s for s in literals if DESIGNATION.match(s)})
    assert not offenders, (
        f"{offenders} are size designations written into main.js — every size "
        "the picker offers must be a row `hole_standards` returned")

    # the form is reset on a part switch (R16); the *behaviour* is graded in
    # node below, this is the subscription itself
    assert _hole_form_reset_hook(main)


# The pickers, the Enter key and the part switch are DOM behaviour, so they are
# graded by running the shipped source. `main.js` is the app entry — importing
# it boots the whole UI — so the harness evaluates the `hole on face` section
# with a stub for each of the seven names it reaches outside itself. That makes
# the section boundary load-bearing, which is why `_hole_section` fails loudly
# rather than silently matching less.
_HOLE_HARNESS = r"""
const CALLS = [];
const TOASTS = [];
const STANDARDS = JSON.parse(process.env.AGENTCAD_STANDARDS);
let pendingDrill = null;
let cleared = 0;
let card = null;

function makeEl(tag) {
  return {
    tagName: tag, kids: [], attrs: {}, handlers: {},
    className: "", textContent: "", value: "", title: "", type: "",
    step: "", min: "", placeholder: "", disabled: false, selected: false,
    setAttribute(key, value) { this.attrs[key] = value; },
    appendChild(kid) { this.kids.push(kid); return kid; },
    append(...kids) { for (const kid of kids) this.kids.push(kid); },
    addEventListener(name, fn) {
      (this.handlers[name] = this.handlers[name] || []).push(fn);
    },
    fire(name, event) {
      for (const fn of this.handlers[name] || []) fn(event || {});
    },
  };
}

const document = { createElement: makeEl, getElementById: () => null };
const api = {
  callTool(name, args) {
    CALLS.push({ name, args });
    if (name === "hole_standards") return Promise.resolve(STANDARDS);
    return new Promise((resolve) => { pendingDrill = resolve; });
  },
};
const state = { projectName: "demo", selectedPart: null, mode: "part" };
let faceSel = { partId: "plate", faceIndex: 3, key: "k", info: { planar: true } };
function toast(message, kind) { TOASTS.push([message, kind || ""]); }
function clearFaceSelection() { cleared += 1; }
function renderFaceCard() { card = makeEl("div"); renderHoleControls(card, true); }

/*REGION*/

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const walk = (node, out = []) => {
  out.push(node);
  for (const kid of node.kids) walk(kid, out);
  return out;
};
const nodes = () => walk(card);
const numbers = () =>
  nodes().filter((n) => n.tagName === "input" && n.type === "number");
const familySelect = () => nodes().find(
  (n) => n.tagName === "select" && n.kids.some((o) => o.value === "drilled"));
const drills = () => CALLS.filter((c) => c.name === "add_holes").length;
const enter = (input) => input.fire("keydown", {
  key: "Enter", preventDefault() {},
});

const out = { toolsCalled: [] };
renderFaceCard();
await tick();                       // the hole_standards answer lands
out.tableSizes = STANDARDS.sizes.clearance;
out.sizeFromTables = holeForm.size;

// R15 — a designation must not survive into the millimetre control
holeForm.size = STANDARDS.sizes.clearance[2];
renderFaceCard();
out.designationPicked = holeForm.size;
const family = familySelect();
family.value = "drilled";
family.fire("change");
out.sizeAfterDrilled = holeForm.size;
out.numbersAfterDrilled = numbers().map((n) => n.value);

// …and a diameter must not survive back into the picker
holeForm.size = "7.5";
renderFaceCard();
const back = familySelect();
back.value = "clearance";
back.fire("change");
out.sizeBackFromDrilled = holeForm.size;

// R17 — Enter honours the guard the click path gets from the attribute
renderFaceCard();
const points = nodes().find(
  (n) => n.tagName === "input" && n.type === "text" && n.handlers.keydown);
const button = nodes().find((n) => n.tagName === "button");
out.foundPointsInput = !!points;
out.foundButton = !!button;
const before = drills();
button.disabled = true;
enter(points);
await tick();
out.drillsWhileDisabled = drills() - before;
button.disabled = false;
enter(points);
await tick();
out.drillsAfterEnter = drills() - before;
out.disabledInFlight = button.disabled;
enter(points);                      // a second Enter while the first is out
await tick();
out.drillsAfterSecondEnter = drills() - before;
if (pendingDrill) pendingDrill({ ok: true });
await tick();
out.drillArgs = CALLS.filter((c) => c.name === "add_holes").map((c) => c.args);

// R16 — the form belongs to the part it was typed against
state.selectedPart = "plate";
/*RESET*/();
holeForm.depth = "12";
holeForm.points = "5, 5";
/*RESET*/();                        // the same part, announced again
out.depthSameSelection = holeForm.depth;
out.pointsSameSelection = holeForm.points;
state.selectedPart = "bracket";
/*RESET*/();
out.depthAfterSwitch = holeForm.depth;
out.pointsAfterSwitch = holeForm.points;

out.toolsCalled = CALLS.map((c) => c.name);
process.stdout.write(JSON.stringify(out));
"""


def _drive_hole_controls() -> dict:
    """Run the shipped hole-on-face section against a stub DOM, in node."""
    import os
    import subprocess

    main = MAIN_JS.read_text(encoding="utf-8")
    script = (_HOLE_HARNESS
              .replace("/*REGION*/", _hole_section(main))
              .replace("/*RESET*/", _hole_form_reset_hook(main)))
    # The picker is fed exactly what the tool answers — a shape the UI is not
    # free to invent. A failure here is the tables', not the card's.
    try:
        standards = hole_standards.lookup(std="iso")
    except Exception as err:  # pragma: no cover - the tables have their own suite
        pytest.fail(f"hole_standards.lookup(std='iso') raised {err!r}; that is "
                    "a table failure, not a frontend one")
    out = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "AGENTCAD_STANDARDS": json.dumps(standards)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")


@needs_node
def test_fr14_a_size_is_never_carried_into_a_control_that_cannot_mean_it():
    """A `drilled` hole's size is a **diameter in millimetres**; every other
    family's is a **table designation**. Switching family used to carry `M5`
    straight into the numeric input (review 3, R15) — the browser shows an
    empty number field and the next Drill sends `size: "M5"` for a diameter.

    Graded by driving the real controls: pick a designation, switch to
    Drilled, and no number input may be holding it.
    """
    out = _drive_hole_controls()

    assert out["sizeFromTables"] in out["tableSizes"], (
        "the default size is not a row of the hole_standards answer")
    assert DESIGNATION.match(out["designationPicked"]), out["designationPicked"]

    assert not DESIGNATION.match(out["sizeAfterDrilled"]), (
        f"switching to Drilled kept {out['sizeAfterDrilled']!r} — a "
        "designation is not a diameter")
    assert float(out["sizeAfterDrilled"]) > 0, out["sizeAfterDrilled"]
    for value in out["numbersAfterDrilled"]:
        assert value == "" or float(value) > 0, (
            f"a numeric input holds {value!r}")

    assert out["sizeBackFromDrilled"] in out["tableSizes"], (
        f"switching back to a table family kept {out['sizeBackFromDrilled']!r},"
        " which is a diameter, not a row the picker can offer")


@needs_node
def test_fr14_enter_cannot_fire_a_drill_the_button_would_refuse():
    """Enter in the positions field called `applyAddHoles` with no guard
    (review 3, R17). A disabled `<button>` never fires `click`, so the
    attribute *is* the click path's guard — and `applyAddHoles` sets it for
    the round trip, which makes it the in-flight guard too. Two `add_holes`
    calls appending to one script clobber each other.

    Also grades R16 at the far end: the module-level form is reset when the
    selected part changes, and **only** when it changes — `setState`
    re-announces a key whether or not its value moved, so a reset that does
    not compare would throw away half-typed input on every re-selection.
    """
    out = _drive_hole_controls()
    assert out["foundPointsInput"] and out["foundButton"]

    assert out["drillsWhileDisabled"] == 0, (
        "Enter fired a drill while the button was disabled")
    assert out["drillsAfterEnter"] == 1, "Enter did not fire the drill"
    assert out["disabledInFlight"], (
        "applyAddHoles left the button enabled during the round trip")
    assert out["drillsAfterSecondEnter"] == 1, (
        "a second Enter fired a concurrent add_holes call")
    assert out["drillArgs"][0]["part_id"] == "plate"
    assert out["drillArgs"][0]["face_index"] == 3

    # R16 — same part: keep what was typed; different part: forget it.
    assert out["depthSameSelection"] == "12"
    assert out["pointsSameSelection"] == "5, 5"
    assert out["depthAfterSwitch"] == "", (
        "a depth typed against one part survived onto the next")
    assert out["pointsAfterSwitch"] != "5, 5", (
        "hole positions typed against one part survived onto the next")


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
    # The divergence record only means something once the work exists to
    # diverge from, so the status has to be past "pending"/"in progress" — but
    # it must not pin ONE post-implementation word: this asserted
    # "implemented" and went red the moment the PRD moved to `completed` on
    # merge, which is the lifecycle working, not a regression. Same trap as the
    # hard-coded `docs/prd/completed/` path `_find_prd()` replaced.
    assert any(f"**Status:** {state}" in text
               for state in ("implemented", "completed")), \
        "the PRD's status is not a post-implementation one"
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


def test_the_roadmap_link_resolves_to_the_prd_where_it_actually_lives():
    """The roadmap's link is the one a reader follows, so it must resolve.

    It pointed at `prd/pending/` while the file lived in `prd/in-progress/`
    for the whole of this PRD's build — a dead link. What is gradeable *now*
    is that the link resolves to a real file and names the stage the PRD is
    filed under; the move to `completed/` happens at merge, so asserting that
    stage here would only make this test pass on the far side of an event the
    tree cannot observe.
    """
    roadmap = (REPO_ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
    row = next(line for line in roadmap.splitlines()
               if line.startswith("| [010]"))
    match = re.search(r"\((prd/[^)]+\.md)\)", row)
    assert match, f"the roadmap's 010 row has no PRD link: {row}"
    linked = REPO_ROOT / "docs" / match.group(1)
    assert linked.is_file(), f"the roadmap's 010 link is dead: {match.group(1)}"
    assert linked.resolve() == PRD.resolve(), (
        f"the roadmap links {linked} but the PRD lives at {PRD}")
    # the PRD is filed exactly once
    duplicates = sorted((REPO_ROOT / "docs" / "prd").rglob(PRD_NAME))
    assert len(duplicates) == 1, f"the PRD is filed more than once: {duplicates}"


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
