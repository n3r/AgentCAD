"""The bench authoring helper (PRD-024 design §1, §7.1).

`agentcad/bench/author.py` is a developer tool that *writes into the
repository*, so nothing here points it at `benchmarks/` — every test that
exercises it works on a `tmp_path` copy of a shipped bundle, and the pure
functions (`compact_svg`) are tested without a service at all.
"""
import json
import shutil
from pathlib import Path

import pytest

from agentcad.bench import author
from agentcad.bench import tasks as bench_tasks
from agentcad.core.model import ValidationError

from .conftest import make_test_service


def _bundle_copy(tmp_path: Path, task_id: str) -> Path:
    """A writable copy of a shipped task bundle."""
    category, name = task_id.split("/")
    src = bench_tasks.tasks_root() / category / name
    dst = tmp_path / "bundle" / name
    shutil.copytree(src, dst)
    return dst


# ------------------------------------------------------------- compact_svg

def test_compact_svg_collapses_a_straight_run_to_its_endpoints():
    # 200 collinear points along y = 10: everything between the ends is
    # redundant at any epsilon.
    points = " L ".join(f"{x}.000 10.000" for x in range(200))
    svg = f'<path d="M {points}" stroke="#000"/>'
    out = author.compact_svg(svg)
    assert out == '<path d="M 0.000 10.000 L 199.000 10.000" stroke="#000"/>'


def test_compact_svg_keeps_a_curve_within_the_epsilon():
    """A real arc keeps enough vertices that nothing moves more than epsilon."""
    import math

    # 0.25-degree steps on an R40 arc: each chord's sagitta is 9.5e-5, well
    # under PATH_EPSILON, so the arc genuinely simplifies — but only as far as
    # the epsilon allows.
    raw = [(50.0 + 40.0 * math.cos(i / 720 * math.pi),
            50.0 + 40.0 * math.sin(i / 720 * math.pi)) for i in range(721)]
    body = " L ".join(f"{x:.3f} {y:.3f}" for x, y in raw)
    out = author.compact_svg(f'<path d="M {body}"/>')
    kept = [tuple(float(v) for v in chunk.split())
            for chunk in out[len('<path d="M '):-len('"/>')].split(" L ")]
    assert 2 < len(kept) < len(raw)          # simplified, but not to a chord
    # Every original point is still within PATH_EPSILON of the kept polyline.
    for px, py in raw:
        best = min(_point_to_segment(px, py, kept[i], kept[i + 1])
                   for i in range(len(kept) - 1))
        assert best <= author.PATH_EPSILON + 1e-9


def _point_to_segment(px, py, a, b) -> float:
    import math

    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    norm = dx * dx + dy * dy
    if norm < 1e-18:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / norm))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def test_compact_svg_touches_nothing_but_paths():
    svg = ('<svg><circle cx="1.000" cy="2.000" r="3.000"/>'
           '<text x="4" y="5">⌀14</text>'
           '<line x1="0" y1="0" x2="9" y2="9"/></svg>')
    assert author.compact_svg(svg) == svg


def test_compact_svg_leaves_a_two_point_path_alone():
    svg = '<path d="M 1.000 2.000 L 3.000 4.000"/>'
    assert author.compact_svg(svg) == svg


def test_compact_svg_survives_a_very_long_path_without_recursing():
    """The worst case for Douglas-Peucker is a monotone staircase, which
    recurses once per point — 4000 of them would blow the interpreter stack if
    the implementation were recursive."""
    points = " L ".join(f"{i}.000 {i * i / 4000.0:.3f}" for i in range(4000))
    out = author.compact_svg(f'<path d="M {points}"/>')
    assert out.startswith('<path d="M 0.000 0.000 L ')


# ----------------------------------------------------------- render_drawing

@pytest.mark.timeout(300)
def test_render_drawing_writes_a_three_view_svg(tmp_path, kernel):
    bundle = _bundle_copy(tmp_path, "model_from_drawing/mfd_001_spacer_plate")
    service = make_test_service(tmp_path / "projects", kernel)
    from agentcad.core.tools import build_registry

    build_registry(service)
    target = author.render_drawing(bundle, "spacer_plate", service=service)

    assert target == bundle / "assets" / "drawing.svg"
    text = target.read_text(encoding="utf-8")
    assert text.startswith("<svg")
    for view in ("TOP", "FRONT", "RIGHT"):
        assert f">{view}<" in text
    assert "ISO" not in text                 # `DEFAULT_VIEWS` is three views
    # The compaction runs on the way out: the raw handler output tessellates
    # every straight edge into up to 256 points, so a compacted sheet has no
    # path anywhere near that long.
    longest = max((len(chunk.split(" L "))
                   for chunk in text.split('d="M ')[1:]), default=0)
    assert 0 < longest < 64
    # It is an authoring helper, so it OVERWROTE the shipped asset in the copy
    # rather than writing beside it — and the shipped bundle is untouched.
    shipped = (bench_tasks.tasks_root() / "model_from_drawing"
               / "mfd_001_spacer_plate" / "assets" / "drawing.svg")
    assert shipped.read_text(encoding="utf-8") != text


@pytest.mark.timeout(300)
def test_render_drawing_refuses_a_part_the_task_does_not_score(tmp_path, kernel):
    bundle = _bundle_copy(tmp_path, "model_from_drawing/mfd_001_spacer_plate")
    service = make_test_service(tmp_path / "projects", kernel)
    with pytest.raises(ValidationError) as excinfo:
        author.render_drawing(bundle, "not_a_part", service=service)
    assert "target.parts" in str(excinfo.value)
    assert not (bundle / "assets" / "nothing.svg").exists()


@pytest.mark.timeout(300)
def test_render_drawing_honours_out_and_views(tmp_path, kernel):
    bundle = _bundle_copy(tmp_path, "model_from_drawing/mfd_001_spacer_plate")
    service = make_test_service(tmp_path / "projects", kernel)
    from agentcad.core.tools import build_registry

    build_registry(service)
    out = tmp_path / "elsewhere" / "sheet.svg"
    target = author.render_drawing(bundle, "spacer_plate", service=service,
                                   views=["front"], out=out)
    assert target == out
    text = out.read_text(encoding="utf-8")
    assert ">FRONT<" in text and ">TOP<" not in text


def test_drawing_subcommand_requires_a_part(tmp_path, capsys):
    bundle = _bundle_copy(tmp_path, "model_from_drawing/mfd_001_spacer_plate")
    with pytest.raises(SystemExit):
        author.main(["drawing", str(bundle)])
    assert "--part" in capsys.readouterr().err


def test_helper_refuses_a_directory_with_no_task_json(tmp_path, capsys):
    assert author.main(["step", str(tmp_path)]) == 2
    assert "task.json" in capsys.readouterr().err


# ------------------------------------------------------ every shipped bundle

def test_every_shipped_svg_asset_is_small_enough_to_attach():
    """An asset is attached to the prompt as text, verbatim (design §8.4), so
    its bytes are the agent's context window. 40 KB is roughly 12 000 tokens —
    a real drawing, not a tessellation dump."""
    root = bench_tasks.tasks_root()
    assets = sorted(root.glob("*/*/assets/*.svg"))
    assert assets, "no drawing assets are shipped"
    for path in assets:
        assert path.stat().st_size < 40_000, f"{path} is {path.stat().st_size} B"


def test_every_derived_task_copies_its_scripts_into_the_bundle():
    """A derived task may never reach into `examples/` at run time: the runner
    registers no examples (design §8.2), so a reference that imported one would
    be a task nobody can score.

    Every script in the bundle is checked, not only `target.parts`: a project
    holds parts the task does not *score* but does have to *build* — `mts_005`
    scores `clamp_plate` and builds `tapped_plate` beside it for the
    interference subscore — and a missing copy there is the same defect.
    `source.parts` is cross-checked against what is on disk so a bundle cannot
    quietly under-declare its provenance either.
    """
    root = bench_tasks.tasks_root()
    seen = 0
    for path in sorted(root.glob("*/*/task.json")):
        raw = json.loads(path.read_text())
        if raw["source"]["kind"] != "derived":
            continue
        seen += 1
        roots = [path.parent / raw["reference"]["project"]]
        if raw.get("starter"):
            roots.append(path.parent / raw["starter"])
        for project in roots:
            scripts = sorted((project / "parts").glob("*.py"))
            assert scripts, f"{path.parent.name}: {project} holds no parts"
            declared = set(raw["source"].get("parts") or ())
            assert {s.stem for s in scripts} <= declared or not declared, (
                f"{path.parent.name}: {sorted(s.stem for s in scripts)} is not "
                f"covered by source.parts {sorted(declared)}")
            for part_id in raw["target"]["parts"]:
                assert (project / "parts" / f"{part_id}.py").is_file(), (
                    f"{path.parent.name}: {part_id} has no script in {project}")
            for script in scripts:
                body = script.read_text().replace("# Copied from examples/", "")
                assert "examples/" not in body, f"{script} reaches examples/"
    assert seen, "no derived task is shipped"


# --------------------------------------------- overall-dimension honesty

#: What a generated sheet's title block says. The hand-authored assets do not
#: carry it, which is exactly how the roster test tells the two apart.
GENERATED_MARKER = "AgentCAD · mm · third angle"


def _declared_extents(task_dir: Path) -> list[float]:
    """The part's three bbox extents, from the task's own metric windows."""
    doc = json.loads((task_dir / "reference" / "metrics.json").read_text())
    by_metric = {w["metric"]: w for w in doc["windows"]}
    out = []
    for key in ("bbox_x_mm", "bbox_y_mm", "bbox_z_mm"):
        window = by_metric.get(key)
        if window is None or window.get("min") is None or \
                window.get("max") is None:
            return []
        out.append((float(window["min"]) + float(window["max"])) / 2.0)
    return out


def test_overall_dim_problems_flags_a_sheet_that_contradicts_the_part():
    svg = ('<text x="1" y="2" fill="#1a56db">132.64</text>'
           '<text x="3" y="4" fill="#1a56db">14.00</text>')
    problems = author.overall_dim_problems(svg, [140.0, 140.0, 14.0])
    assert len(problems) == 1
    assert "132.64" in problems[0] and "140" in problems[0]


def test_overall_dim_problems_ignores_callouts_and_toleranced_dims():
    svg = ('<text fill="#1a56db">8× ⌀9.00</text>'
           '<text fill="#1a56db">140.00 +0.10/-0.10</text>'
           '<text fill="#111">FRONT</text>'
           '<text fill="#1a56db">14.00</text>')
    assert author.overall_dim_problems(svg, [140.0, 140.0, 14.0]) == []


def test_every_generated_sheet_dimensions_the_part_it_draws():
    """A sheet that dimensions a Ø140 flange `132.64` is worse than no sheet.

    Only *generated* sheets are checked — a hand-authored drawing is full of
    legitimate numbers that are not overall extents (a hole pitch, a groove
    depth) and is reviewed by reading it, not by this rule.
    """
    root = bench_tasks.tasks_root()
    checked = 0
    for path in sorted(root.glob("*/*/assets/*.svg")):
        text = path.read_text(encoding="utf-8")
        if GENERATED_MARKER not in text:
            continue
        extents = _declared_extents(path.parent.parent)
        if not extents:                  # the task pins no bbox windows
            continue
        checked += 1
        assert author.overall_dim_problems(text, extents) == [], str(path)
    assert checked, "no generated sheet was checked"


@pytest.mark.timeout(300)
def test_render_drawing_refuses_a_sheet_that_contradicts_the_part(tmp_path,
                                                                  kernel):
    """The tripwire, end to end, on the part that actually trips it.

    `handlers/drawing._view_bounds` samples each edge at six points — exact for
    a line, wrong for a circle — so the flange's plan view is dimensioned
    132.64 for a Ø140 part while the front view on the same sheet says 140.00.
    `mfd_003`'s shipped asset carries the corrected annotation; this pins the
    guard that stops a re-render putting the lie back.

    **If this test ever fails because the render succeeded, the product bug is
    fixed:** delete the test and re-render `mfd_003`'s asset from the helper.
    """
    bundle = _bundle_copy(tmp_path, "model_from_drawing/mfd_003_head_flange")
    service = make_test_service(tmp_path / "projects", kernel)
    from agentcad.core.tools import build_registry

    build_registry(service)
    before = (bundle / "assets" / "drawing.svg").read_text(encoding="utf-8")
    with pytest.raises(ValidationError) as excinfo:
        author.render_drawing(bundle, "flange", service=service)
    assert "contradicts the part" in str(excinfo.value)
    # Refused BEFORE the write: the good asset is still there.
    assert (bundle / "assets" / "drawing.svg").read_text(
        encoding="utf-8") == before
    # And `check_dims=False` is the deliberate way to look at the bad sheet.
    author.render_drawing(bundle, "flange", service=service, check_dims=False,
                          out=tmp_path / "bad.svg")
    assert author.overall_dim_problems(
        (tmp_path / "bad.svg").read_text(encoding="utf-8"),
        [140.0, 140.0, 14.0])
