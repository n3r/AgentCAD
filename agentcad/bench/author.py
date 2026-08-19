"""Authoring helper for bench tasks. NOT an ``agentcad`` subcommand.

    uv run python -m agentcad.bench.author step    benchmarks/tasks/<c>/<id>
    uv run python -m agentcad.bench.author metrics benchmarks/tasks/<c>/<id>

``step`` copies ``reference/project`` into a throwaway projects root, builds
every part named in ``task.json``'s ``target.parts`` and exports each to
``reference/steps/<part>.step``.
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
    parser.add_argument("command", choices=("step", "metrics"))
    parser.add_argument("task_dir", help="benchmarks/tasks/<category>/<id>")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="relative band on mass and volume (default 0.01)")
    args = parser.parse_args(argv)

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
        service = _build_service(scratch)
        if args.command == "step":
            for part_id, path in sorted(export_reference(
                    task_dir, service=service).items()):
                print(f"{part_id}: {path}")
        else:
            print(seed_metrics(task_dir, service=service,
                               tolerance=args.tolerance))
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
