"""Authoring helper for bench tasks. NOT an ``agentcad`` subcommand.

    uv run python -m agentcad.bench.author step    benchmarks/tasks/<c>/<id>
    uv run python -m agentcad.bench.author metrics benchmarks/tasks/<c>/<id>
    uv run python -m agentcad.bench.author drawing benchmarks/tasks/<c>/<id> \
        --part <part_id>

``step`` copies ``reference/project`` into a throwaway projects root, builds
every part named in ``task.json``'s ``target.parts`` and exports each to
``reference/steps/<part>.step``.
``drawing`` renders one of those parts through the product's **own** drawing
path and drops the SVG into ``assets/`` — a bench drawing is exactly the
drawing AgentCAD produces, so the task can be neither easier nor harder than
the tool it measures.
``metrics`` measures the same parts and seeds ``reference/metrics.json`` with a
+/-1% band on mass and volume and a +/-0.05 mm band on each bbox extent — a
**starting point** the author then hand-edits and argues in the PR, never a
generated rubric nobody read.

It is a developer tool rather than a subcommand on purpose: it *writes into the
repository*, and every `agentcad` subcommand that writes writes into a user's
project. Nothing in it imports build123d — the geometry happens in the kernel
worker, on the far side of the service, exactly as it does for the product.
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import sys
import tempfile
from pathlib import Path

from ..core.model import ValidationError
from ._json import read_json, write_json
from .tasks import METRICS_SCHEMA

#: The +/-1% band `seed_metrics` opens on mass and volume.
DEFAULT_TOLERANCE = 0.01

#: The +/-0.05 mm band it opens on each bbox extent. Absolute rather than
#: relative because a 6 mm plate and a 300 mm barrel want the same *machining*
#: slack, not the same percentage.
BBOX_SLACK_MM = 0.05


def _reference_project(task_dir: Path, raw: dict) -> Path:
    return (task_dir / raw["reference"]["project"]).resolve()


def _stage_reference(task_dir: Path, raw: dict, service) -> str:
    """Copy ``reference/project`` under the service's projects root and open it.

    Never opened **in place**: a build writes ``.cache/`` and an export writes
    ``exports/`` into the project directory, so opening the checked-in
    reference would scatter derived files through `benchmarks/` — and the
    confined worker cannot write there anyway (the repo is not a writable
    root, PRD-006 Decision 1), which is how the in-place version announced
    itself: ``RuntimeError: Failed to write STEP file``.
    """
    source = _reference_project(task_dir, raw)
    # Named after the manifest and placed under the store's own root, so the
    # copy is an ORDINARY project rather than an external registration: nothing
    # has to `open` it, and the temp-dir symlink (`/var` -> `/private/var` on
    # macOS) that makes a resolved path differ from `root / name` — and made
    # `open_project` refuse it as "a different project" — never comes up.
    name = read_json(source / "project.json")["name"]
    dest = Path(service.store.root) / name
    if not dest.exists():
        shutil.copytree(source, dest)
    return name


def export_reference(task_dir, *, service) -> dict:
    """Build + export every reference part. Returns ``{part_id: step_path}``."""
    task_dir = Path(task_dir).resolve()
    raw = read_json(task_dir / "task.json")
    proj = _stage_reference(task_dir, raw, service)
    steps = task_dir / "reference" / "steps"
    steps.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    for part_id in raw["target"]["parts"]:
        result = service.export_part(proj, part_id, "step")
        target = steps / f"{part_id}.step"
        shutil.copyfile(result["path"], target)
        out[part_id] = str(target)
    return out


#: The views a bench drawing carries. Three orthographic views and no `iso`:
#: the isometric adds no dimension an agent can read, and it is the one view
#: whose projection is not a plane a draughtsman would dimension against.
DEFAULT_VIEWS = ("top", "front", "right")


#: How far a dropped path vertex may sit off the segment that replaces it, in
#: SVG user units. The drawing handler prints every coordinate with **three**
#: decimals on a 420 x 297 sheet, so 0.002 is twice the printed resolution and
#: below anything a renderer can show: the compaction is lossless *at the
#: precision the file itself carries*.
PATH_EPSILON = 0.002

_PATH_RE = re.compile(r'd="M ([-0-9. L]+)"')


def _compact_path(points, epsilon: float) -> list:
    """The INDICES of the vertices Douglas-Peucker keeps, in order.

    Indices rather than points so the caller can emit each survivor's own
    input substring. Iterative: a 700-point path recurses ~10 deep on average
    and 700 deep in the worst case, and a RecursionError inside an authoring
    helper is not a useful sentence."""
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        (x0, y0), (x1, y1) = points[lo], points[hi]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy)
        worst, index = -1.0, lo
        for i in range(lo + 1, hi):
            px, py = points[i]
            if norm < 1e-12:
                dist = math.hypot(px - x0, py - y0)
            else:
                dist = abs(dy * (px - x0) - dx * (py - y0)) / norm
            if dist > worst:
                worst, index = dist, i
        if worst > epsilon:
            keep[index] = True
            stack.append((lo, index))
            stack.append((index, hi))
    return [index for index, flag in enumerate(keep) if flag]


def compact_svg(text: str, epsilon: float = PATH_EPSILON) -> str:
    """Collapse the drawing handler's tessellated polylines, losslessly.

    ``handlers/drawing._edge_prim`` samples an edge it cannot draw exactly into
    up to 256 points. It used to do that to **every** non-closed-circle edge —
    a dead-straight 90 mm line included — which made a three-view sheet a
    150-250 KB file whose straight edges were 99% redundant; since changelog
    0307 a LINE is two points and a circular edge is a circle or an arc, so
    what is left for this helper is the genuinely free-form geometry (ELLIPSE,
    BSPLINE — an iso view of a turned part is full of it). An asset is attached
    to the prompt as **text, verbatim** (design §8.4), so those bytes are the
    agent's context window, and a task whose drawing costs 50 000 tokens before
    the first tool call is measuring the wrong thing.

    Douglas-Peucker at :data:`PATH_EPSILON` removes only vertices that are
    already indistinguishable from the segment replacing them at the file's own
    three-decimal precision: the sheet **renders identically**, so the task is
    neither easier nor harder than the drawing the product produces. Nothing
    else in the document is touched — every `<circle>`, `<text>`, dimension
    line and arrowhead survives byte-for-byte.
    """
    def _rewrite(match: re.Match) -> str:
        chunks = match.group(1).split(" L ")
        try:
            pairs = [tuple(float(value) for value in chunk.split())
                     for chunk in chunks]
        except ValueError:                       # not a plain "M x y L x y" path
            return match.group(0)
        if len(pairs) < 3 or any(len(pair) != 2 for pair in pairs):
            return match.group(0)
        keep = _compact_path(pairs, epsilon)
        # The surviving vertices are emitted as their OWN input substrings, not
        # re-formatted: a kept point is byte-identical to the point it was, so
        # the compaction can neither add precision the source did not carry nor
        # round away precision it did. Nothing here has to know that the
        # drawing handler prints three decimals.
        body = " L ".join(chunks[index] for index in keep)
        return f'd="M {body}"'

    return _PATH_RE.sub(_rewrite, text)


#: How far a sheet's overall-dimension annotation may sit from the part's own
#: bbox extent before the sheet is lying. 0.5 mm is far looser than any
#: rounding in a `%.2f` dimension and far tighter than the defect this guards.
DIM_TOLERANCE_MM = 0.5

#: A dimension annotation in a generated sheet: `handlers/drawing._TXT` paints
#: every one of them this colour, and only the two overall dims per view are
#: rendered as a BARE number (a hole callout carries `x`/diameter glyphs and a
#: PMI-toleranced dim carries a +/- suffix), so a bare float in this colour is
#: an overall extent and nothing else.
_DIM_TEXT_RE = re.compile(r'fill="#1a56db"[^>]*>([^<]+)</text>')


def overall_dim_problems(svg_text: str, extents_mm, *,
                         tolerance: float = DIM_TOLERANCE_MM) -> list[str]:
    """Every overall dimension on *svg_text* that disagrees with the part.

    A sheet that dimensions a Ø140 flange `132.64` is worse than no sheet: the
    agent is being graded against geometry the drawing denies. That was a real
    product defect -- `_view_bounds` (`kernel/handlers/drawing.py`) sampled each
    edge at **six** points, exact for a line and wrong for a circle (a full
    circle is sampled at 0/72/144/216/288 degrees, so its silhouette extremes
    are missed and the view bounds, which are also what the overall dims are
    drawn from, come out under the truth). It is **fixed** (changelog 0307:
    exact per-edge bounding boxes), and this stays as the live guard on the
    authoring path, because a sheet that contradicts its own part is a task
    nobody can solve and the check costs a regex.

    Pure and non-raising, `task_problems`' shape: the caller decides whether a
    disagreement is a refusal or a report.
    """
    extents = [float(value) for value in extents_mm]
    out: list[str] = []
    for raw in _DIM_TEXT_RE.findall(svg_text):
        try:
            value = float(raw.strip())
        except ValueError:
            continue                     # a callout or a toleranced dim
        if not any(abs(value - extent) <= tolerance for extent in extents):
            out.append(
                f"the sheet dimensions {value:g} mm, which matches none of the "
                f"part's extents "
                f"({', '.join(f'{e:g}' for e in extents)}) within "
                f"{tolerance:g} mm")
    return sorted(set(out))


def neutral_title(svg_text: str, project: str, part_id: str) -> str:
    """Rewrite the title block's label from ``<project> / <part>`` to ``<part>``.

    A bench asset is handed to the model **verbatim**, and the project the
    drawing was rendered from is the task's own *reference* project
    (`_stage_reference` opens it under its manifest name, e.g.
    ``bench_mfd_002_angle_bracket_reference``). `tools_drawing` builds the
    title-block label as ``f"{project} / {part_id}"``, so an unscrubbed sheet
    tells the agent the id of the task it is being graded on and that a
    reference project exists — neither of which the built-in agent's own
    `generate_drawing` output would ever carry.

    Post-processed here rather than fixed at the source: the label is the
    *product's* behaviour and it is right for a product user, whose project
    name is their own. Only a bench asset has to be anonymous.

    Exact, and a no-op when the label is not there: the substring is the whole
    text node, so a dimension that happens to read like a project name cannot
    be caught by it.
    """
    return svg_text.replace(f">{project} / {part_id}</text>",
                            f">{part_id}</text>")


def render_drawing(task_dir, part_id: str, *, service, views=None,
                   out=None, check_dims: bool = True) -> Path:
    """Render ``reference/project``'s *part_id* as a three-view SVG asset.

    Uses the product's own drawing path (``generate_drawing``,
    ``core/tools_drawing.py``, ``format="svg"``), through
    :meth:`ToolRegistry.call` rather than by reaching into the service, so a
    bench drawing is exactly the drawing an agent would get from
    ``generate_drawing`` on its own project — the task cannot be easier or
    harder than the tool it measures.

    SVG only. The prompt attaches an asset as **text** (design §8.4) and the
    loader's `ASSET_SUFFIXES` refuses anything else, so a DXF option here
    would only be a way to author a bundle the loader then rejects.

    ``check_dims`` verifies the rendered sheet's overall dimensions against the
    part's own bbox (:func:`overall_dim_problems`) and **refuses** rather than
    writing a sheet that contradicts the geometry it is a drawing of. The
    curved-silhouette defect it was written for is fixed (changelog 0307); the
    guard stays live, because it is what would catch its return. Pass
    ``check_dims=False`` only to look at a bad sheet.
    """
    from ..core.tools import build_registry

    task_dir = Path(task_dir).resolve()
    raw = read_json(task_dir / "task.json")
    if part_id not in raw["target"]["parts"]:
        raise ValidationError(
            f"{part_id!r} is not one of this task's target.parts "
            f"({', '.join(raw['target']['parts'])}); a drawing of a part the "
            f"task does not score is not this task's drawing",
            {"part": part_id, "parts": list(raw["target"]["parts"])})
    proj = _stage_reference(task_dir, raw, service)
    registry = build_registry(service)
    result = registry.call("generate_drawing", {
        "project": proj, "part_id": part_id, "format": "svg",
        "views": list(views or DEFAULT_VIEWS)})
    # `registry.call` answers a refusal as an `{"error": ...}` PAYLOAD rather
    # than by raising (tools.py's contract), so an unchecked result would copy
    # nothing and report success.
    if "error" in result:
        raise ValidationError(
            f"generate_drawing refused to draw {part_id!r}: "
            f"{result['error'].get('message')}",
            {"part": part_id, "error": result["error"]})
    # Scrubbed BEFORE `check_dims`: `neutral_title` touches one text node and
    # no coordinate, but the sheet that is checked has to be the sheet that is
    # written.
    sheet = neutral_title(
        compact_svg(Path(result["path"]).read_text(encoding="utf-8")),
        proj, part_id)
    if check_dims:
        built = service._ensure_built(proj, part_id)
        box = (built.get("metrics") or {}).get("bbox") or {}
        low, high = box.get("min") or [], box.get("max") or []
        extents = [float(high[i]) - float(low[i]) for i in range(3)] \
            if len(low) == 3 and len(high) == 3 else []
        problems = overall_dim_problems(sheet, extents) if extents else \
            ["the part did not build, so the sheet could not be checked "
             "against its own geometry"]
        if problems:
            raise ValidationError(
                f"the rendered sheet for {part_id!r} contradicts the part: "
                + "; ".join(problems),
                {"part": part_id, "problems": problems, "extents_mm": extents})
    target = Path(out) if out else task_dir / "assets" / "drawing.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sheet, encoding="utf-8")
    return target


def _bbox_extents(metrics: dict) -> tuple[float, float, float]:
    bbox = metrics["bbox"]
    low, high = bbox["min"], bbox["max"]
    return tuple(float(high[i]) - float(low[i]) for i in range(3))


def seed_metrics(task_dir, *, service, tolerance: float = DEFAULT_TOLERANCE) -> Path:
    """Write a first-draft ``reference/metrics.json`` and return its path.

    Windows are sorted by ``name`` and the document goes through
    :func:`_json.write_json`, so re-running the helper on an unchanged
    reference produces byte-identical output — a diff here means the geometry
    moved, which is the only reason an author should be looking at one.
    """
    task_dir = Path(task_dir).resolve()
    raw = read_json(task_dir / "task.json")
    proj = _stage_reference(task_dir, raw, service)
    windows: list[dict] = []
    for part_id in raw["target"]["parts"]:
        built = service._ensure_built(proj, part_id)
        if not built.get("ok"):
            raise ValidationError(
                f"the reference part {part_id!r} does not build: "
                f"{built.get('error')}", {"part": part_id})
        metrics = built["metrics"]
        for label, key in (("mass", "mass_g"), ("volume", "volume_mm3")):
            value = float(metrics[key])
            windows.append({"name": f"{part_id}_{label}", "part": part_id,
                            "metric": key,
                            "min": value * (1.0 - tolerance),
                            "max": value * (1.0 + tolerance)})
        for axis, extent in zip("xyz", _bbox_extents(metrics)):
            windows.append({"name": f"{part_id}_bbox_{axis}", "part": part_id,
                            "metric": f"bbox_{axis}_mm",
                            "min": extent - BBOX_SLACK_MM,
                            "max": extent + BBOX_SLACK_MM})
        solids = int(metrics.get("n_solids", 1))
        windows.append({"name": f"{part_id}_solids", "part": part_id,
                        "metric": "n_solids", "min": solids, "max": solids})
    windows.sort(key=lambda window: window["name"])
    out = task_dir / "reference" / "metrics.json"
    write_json(out, {"schema": METRICS_SCHEMA, "windows": windows})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentcad.bench.author",
        description="Regenerate a bench task's reference artefacts.")
    parser.add_argument("command", choices=("step", "metrics", "drawing"))
    parser.add_argument("task_dir", help="benchmarks/tasks/<category>/<id>")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="relative band on mass and volume (default 0.01)")
    parser.add_argument("--part", help="drawing: which target part to draw")
    parser.add_argument("--views", help="drawing: comma-separated subset of "
                                        "top,front,right,iso "
                                        f"(default {','.join(DEFAULT_VIEWS)})")
    parser.add_argument("--out", help="drawing: where to write the SVG "
                                      "(default <task_dir>/assets/drawing.svg)")
    parser.add_argument("--no-check-dims", dest="check_dims",
                        action="store_false",
                        help="drawing: write the sheet even when its overall "
                             "dimensions contradict the part's own bbox "
                             "(they should not; see overall_dim_problems)")
    args = parser.parse_args(argv)
    if args.command == "drawing" and not args.part:
        parser.error("drawing needs --part <part_id>")

    from ..cli import _build_service, _release_work_root

    task_dir = Path(args.task_dir).resolve()
    if not (task_dir / "task.json").is_file():
        print(f"{task_dir} holds no task.json", file=sys.stderr)
        return 2
    # A throwaway projects root: the reference is COPIED into it and built
    # there, so nothing of the author's own tree — and nothing under
    # `benchmarks/` — is in the writable set.
    scratch = Path(tempfile.mkdtemp(prefix="agentcad-bench-author-"))
    service = None
    try:
        service = _build_service(scratch, examples=False)
        if args.command == "step":
            for part_id, path in sorted(export_reference(
                    task_dir, service=service).items()):
                print(f"{part_id}: {path}")
        elif args.command == "drawing":
            views = ([name.strip() for name in args.views.split(",")
                      if name.strip()] if args.views else None)
            print(render_drawing(task_dir, args.part, service=service,
                                 views=views, out=args.out,
                                 check_dims=args.check_dims))
        else:
            print(seed_metrics(task_dir, service=service,
                               tolerance=args.tolerance))
    except ValidationError as exc:
        # An authoring refusal is a sentence and exit 2, `cmd_check`'s idiom --
        # a traceback here reads as a crash in the helper rather than as the
        # helper telling the author their bundle is wrong.
        print(f"{exc}", file=sys.stderr)
        return 2
    finally:
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:  # noqa: BLE001 — cleanup, never the answer
                print(f"the kernel did not stop cleanly: {exc}",
                      file=sys.stderr)
            _release_work_root(service)
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":       # pragma: no cover — a developer entry point
    raise SystemExit(main())
