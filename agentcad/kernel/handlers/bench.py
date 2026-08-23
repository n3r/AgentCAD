"""Worker handler pack for the bench geometry scorer (PRD-024).

``iou`` measures how much of the candidate is the reference, and vice versa, as
one number. Five traps this handler is written around:

* **Only the intersection is booleaned.** ``union = volA + volB - inter`` is
  arithmetic; a ``|`` on multi-solid Compound operands is exactly the operand
  shape ``worker.pairwise_interference``'s docstring warns about, and it would
  double the OCCT failure surface for a number we already have.
* **Both sides are decomposed into solids**, for ``pairwise_interference``'s
  two reasons: build123d's ``&`` misbehaves when an operand is a multi-solid
  Compound, and an AABB prefilter skips almost all of the N*M work. The
  pairwise sum equals ``volume(A & B)`` only when each side's own solids are
  mutually disjoint — true of a well-formed part, false of a candidate that
  self-overlaps, where it over-counts — so the total is clamped to
  ``min(sum, volA, volB)`` and the ratio to ``[0, 1]``. Both solid counts ride
  in the result so a reader can see when the clamp could have bitten.
* **A mesh side is never booleaned** — an imported STL is one welded Face and
  an OCCT boolean on it segfaults the worker. Such a side short-circuits to
  ``status: "skipped_mesh"`` with both per-side volumes still reported, exactly
  as ``handlers/diff.py`` does.
* **Every boolean is guarded** into a ``WorkerError(ERROR_KERNEL, ...,
  {"stage": ...})`` so the scorer can record ``status: "error"`` (FR7) rather
  than crash the harness or bank a silent zero.
* **A boolean that lies is refused, not banked.** OCCT 7.9 returns an empty
  (or negative-volume) intersection, with no error, for operands carrying
  G1-tangent face junctions — any filleted swept solid. Two *coincident*
  copies of the bench's own coolant elbow intersect to nothing. That is the
  worst possible IoU input: the candidate is the reference and the handler
  would answer ``iou: 0.0``. ``_bop.checked_common_volume`` detects it and the
  handler raises the same kernel error every other failed boolean raises, so
  the subscore is ``error`` and **excluded**, never a banked false zero.

Volumes come from ``shape_volume`` (sum over ``shape.solids()``), never
``.volume``: a boolean result is routinely a nested Compound and
``Compound.volume`` reports only the first child subtree.

Alignment is applied to the **candidate** only; the reference is the datum.
The transform is ``translate(anchor_ref) . rotate(r) . translate(-anchor_cand)``
composed as build123d ``Location``s and applied with ``shape.moved(loc)`` —
``worker._place``'s mechanism, intrinsic XYZ Euler degrees throughout. Scale is
never normalised: a part of the wrong size is a wrong part.

``rotations_deg`` is the task's finite, declared list (default ``[[0,0,0]]``);
every entry is evaluated, the **maximum** IoU wins and ties keep the first, so
the answer is deterministic. The list is capped by the bench task loader, not
here — each entry is a full boolean.

This handler is NEVER registered as a model-facing tool: a bench-only tool
would contaminate the very measurement it exists to take.
"""

from __future__ import annotations

import contextlib
import math
import sys

from ._bop import checked_common_volume

ALIGN_MODES = ("world", "com", "bbox_center")


def register(toolbox: dict) -> dict:
    b3d = toolbox["b3d"]
    build_shape = toolbox["build_shape"]
    shape_volume = toolbox["shape_volume"]
    WorkerError = toolbox["WorkerError"]
    ERROR_KERNEL = toolbox["ERROR_KERNEL"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def _triple(value, label) -> tuple[float, float, float]:
        """A 3-vector of finite floats, or a contract error naming the field."""
        try:
            nums = [float(v) for v in value]
        except (TypeError, ValueError) as exc:
            raise WorkerError(
                ERROR_CONTRACT, f"{label} must be three numbers") from exc
        if len(nums) != 3 or not all(math.isfinite(n) for n in nums):
            raise WorkerError(ERROR_CONTRACT,
                              f"{label} must be three finite numbers")
        return (nums[0], nums[1], nums[2])

    def _side(item, label) -> tuple[object, str]:
        """(shape, kind) for one side — ``worker._item_shape``'s grammar: an
        item carries ``source`` (a reference file) or ``script`` (+ ``params``),
        plus an optional ``position``/``rotation_deg`` placement."""
        if not isinstance(item, dict):
            raise WorkerError(ERROR_CONTRACT, f"{label} must be an item object")
        if item.get("source"):
            from ..refload import load_reference

            shape, kind = load_reference(item["source"])
        elif item.get("script"):
            shape, _values, _warnings = build_shape(
                item["script"], item.get("params") or {})
            kind = "script"
        else:
            raise WorkerError(
                ERROR_CONTRACT, f"{label} needs a 'script' or a 'source'")
        placed = b3d.Location(_triple(item.get("position") or (0, 0, 0),
                                      f"{label}.position"),
                              _triple(item.get("rotation_deg") or (0, 0, 0),
                                      f"{label}.rotation_deg"))
        return shape.moved(placed), kind

    def _anchor(shape, align) -> tuple[float, float, float]:
        if align == "com":
            point = shape.center(b3d.CenterOf.MASS)
            return (point.X, point.Y, point.Z)
        if align == "bbox_center":
            point = shape.bounding_box().center()
            return (point.X, point.Y, point.Z)
        return (0.0, 0.0, 0.0)

    def _guarded(fn, stage):
        try:
            return fn()
        except WorkerError:
            raise
        except Exception as exc:  # noqa: BLE001 — any OCCT failure degrades
            raise WorkerError(ERROR_KERNEL, f"iou unavailable: {exc}",
                              {"stage": stage}) from exc

    def _finite(value: float, stage: str) -> float:
        """*value*, or a kernel error naming the stage that produced a NaN.

        Not a formality. Every comparison against NaN is false, so a NaN
        volume walks straight through the two clamps the IoU is built on:
        ``union <= 0.0`` is false, ``min(1.0, nan)`` answers **1.0** (``min``
        keeps its running minimum when the test ``nan < 1.0`` fails), and
        ``max(0.0, 1.0)`` leaves it there. A degenerate shape whose volume
        OCCT could not compute would therefore have scored a perfect
        ``iou: 1.0`` — the single worst answer this handler can give, because
        the scorer's own non-finite guard (`scoring._geometry_part`) never sees
        a non-finite number to reject. Refused here instead, as
        ``status: "error"`` one level up (FR7).
        """
        number = float(value)
        if not math.isfinite(number):
            raise WorkerError(ERROR_KERNEL,
                              f"iou unavailable: {stage} is not a finite "
                              f"number ({number})", {"stage": stage})
        return number

    def _boxes_touch(a, b, tol=1e-6) -> bool:
        return not (a.max.X < b.min.X - tol or b.max.X < a.min.X - tol
                    or a.max.Y < b.min.Y - tol or b.max.Y < a.min.Y - tol
                    or a.max.Z < b.min.Z - tol or b.max.Z < a.min.Z - tol)

    def _common_volume(solid_a, box_a, solid_b, box_b) -> float:
        """One pair's intersection volume, or a kernel error if OCCT lied.

        The old body clamped with ``max(volume, 0.0)`` and returned ``0.0`` on
        an empty result — which laundered both of ``_bop``'s degenerate
        signatures (a negative volume, and an empty intersection between
        solids that plainly overlap) into a confident, wrong zero.
        """
        volume, degenerate = checked_common_volume(
            solid_a, box_a, solid_b, box_b, shape_volume)
        if degenerate:
            raise WorkerError(
                ERROR_KERNEL,
                "iou unavailable: degenerate boolean — OCCT could not "
                "intersect this solid pair (tangent-jointed swept solids are "
                "unreliable BOP operands in OCCT 7.9); refusing to bank the "
                "empty result as a measurement",
                {"stage": "intersect"})
        return volume

    def _decompose(shape) -> list:
        return [(s, s.bounding_box()) for s in (shape.solids() or [shape])]

    def _pass(candidate, right) -> float:
        """One placed candidate against the decomposed reference: the summed
        pairwise solid intersection, AABB-prefiltered.

        OCCT chatters on stdout and the worker's protocol lives there, so the
        boolean loop borrows ``pairwise_interference``'s redirect."""
        left = _decompose(candidate)
        total = 0.0
        with contextlib.redirect_stdout(sys.stderr):
            for solid_a, box_a in left:
                for solid_b, box_b in right:
                    if _boxes_touch(box_a, box_b):
                        total += _common_volume(solid_a, box_a,
                                                solid_b, box_b)
        return total

    def handle_iou(params: dict) -> dict:
        align = params.get("align") or "world"
        if align not in ALIGN_MODES:
            raise WorkerError(
                ERROR_CONTRACT,
                f"unknown align mode {align!r}; expected one of "
                f"{', '.join(ALIGN_MODES)}")
        # The raw value is tested BEFORE defaulting: `params.get(...) or
        # default` would swallow an explicit `[]` into the identity rotation
        # and score it silently. Only an absent (or null) key defaults.
        raw = params.get("rotations_deg")
        if raw is None:
            raw = [[0.0, 0.0, 0.0]]
        elif not isinstance(raw, list) or not raw:
            raise WorkerError(ERROR_CONTRACT,
                              "rotations_deg must be a non-empty list")
        rotations = [_triple(r, "rotations_deg entry") for r in raw]

        cand, cand_kind = _side(params.get("candidate"), "candidate")
        ref, ref_kind = _side(params.get("reference"), "reference")
        vol_a = _finite(_guarded(lambda: shape_volume(cand),
                                 "candidate_volume"), "candidate_volume")
        vol_b = _finite(_guarded(lambda: shape_volume(ref), "reference_volume"),
                        "reference_volume")
        base = {
            "candidate_volume_mm3": vol_a,
            "reference_volume_mm3": vol_b,
            "candidate_solids": len(cand.solids()),
            "reference_solids": len(ref.solids()),
            "align": align,
            "rotation_deg": [0.0, 0.0, 0.0],
        }

        skipped = [name for name, kind in (("candidate", cand_kind),
                                           ("reference", ref_kind))
                   if kind == "mesh"]
        if skipped:
            # No boolean is attempted: an STL operand segfaults OCCT. Both
            # per-side volumes are still reported, as handlers/diff.py does.
            return {**base, "intersection_mm3": 0.0,
                    "union_mm3": vol_a + vol_b, "iou": 0.0,
                    "status": "skipped_mesh", "skipped_mesh": skipped}

        anchor_c = _guarded(lambda: _anchor(cand, align), "align")
        anchor_r = _guarded(lambda: _anchor(ref, align), "align")
        # Decomposed once: the reference never moves, only the candidate does.
        right = _guarded(lambda: _decompose(ref), "intersect")

        best = None
        for rot in rotations:
            # translate(anchor_ref) . rotate(rot) . translate(-anchor_cand):
            # the right-hand Location is applied first.
            placement = (b3d.Location(anchor_r, rot)
                         * b3d.Location(tuple(-v for v in anchor_c)))
            total = _finite(
                _guarded(lambda: _pass(cand.moved(placement), right),
                         "intersect"), "intersect")
            # The pairwise sum is volume(A & B) only when each side's solids
            # are mutually disjoint; a self-overlapping side over-counts.
            inter = min(total, vol_a, vol_b)
            union = vol_a + vol_b - inter
            score = 0.0 if union <= 0.0 else max(0.0, min(1.0, inter / union))
            if best is None or score > best[0]:
                best = (score, inter, union, list(rot))
        score, inter, union, rot = best
        # `base` already carries the TRUE `len(shape.solids())` for both sides
        # -- the same number the skipped_mesh path reports. `_decompose`'s
        # `or [shape]` fallback is a boolean-loop convenience (a solid-less
        # shape still has to be intersected as itself) and must not be
        # laundered into the reported count.
        return {**base,
                "intersection_mm3": inter, "union_mm3": union, "iou": score,
                "rotation_deg": rot, "status": "ok"}

    return {"iou": handle_iou}
