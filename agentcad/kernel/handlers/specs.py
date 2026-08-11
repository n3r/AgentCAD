"""Worker handler pack for executable design specs (PRD-003).

Three methods, all of them primitives the service orchestrates:

* ``spec_declare`` — execute a part script or a project ``specs.py`` and report
  the ``SPECS`` it declares, JSON-safe. **It never builds**: a declaration must
  be readable on a part that does not build, which is what makes the spec list
  visible before any geometry exists.
* ``spec_eval`` — build the part and evaluate the *shape tier*
  (``valid``/``mass``/``volume``/``bbox``/``wall``/``that``) against the built
  shape and its metrics. Everything else in the vocabulary is a named skip.
* ``clearance`` — the minimum distance between two world-placed items, via
  ``BRepExtrema_DistShapeShape``. The one genuinely new geometry op in the
  feature.

The rules this file is written around:

* **A ``check_that`` predicate never leaves this process.** It is a Python
  callable read out of the script's own namespace (the ``connectors(p, part)``
  / ``analysis(p)`` seam, via ``build_shape_ns``), executed here with
  ``(part, metrics)``, and replaced by ``"predicate": true`` on the way out.
  Predicates are untrusted script code and the server process must never run
  them.
* **A predicate must not be able to change another check's verdict.** It is
  script code holding a reference to shared state, so each check is handed its
  own ``deepcopy`` of the metrics dict and every ``that`` check is evaluated
  **last** — a predicate that writes ``metrics["mass_g"] = 0`` then cannot turn
  a failing ``check_mass`` green. Records are still emitted in declared order.
  Evaluation order is therefore not a contract a predicate may rely on: the
  built-in kinds run first, ``that`` predicates run afterwards in declared
  order among themselves. The built *shape* is shared and cannot be copied —
  a predicate that mutates the B-rep is out of scope, exactly as a ``build``
  that does is.
* **``_min_wall`` is imported from the sibling analysis pack, never
  re-implemented.** Two implementations of wall thickness is the worst outcome
  available. The same reasoning is why ``_exec_script``, ``_item_shape`` and
  ``_script_error_from_exc`` are imported from ``worker`` rather than mirrored
  here — ``motion.py`` already takes that back-edge for
  ``pairwise_interference``, and the import happens inside ``register()``,
  which only runs once ``worker`` is fully imported.
* **A broken check is payload; a broken ``SPECS`` is an error.** Every
  per-check evaluation is guarded on its own and degrades to an ``error``
  record carrying ``details.traceback``/``details.line``; only a structural
  problem (``SPECS`` is not a list, or holds something no constructor produced)
  raises ``contract_error``, and a constructor rejecting its own argument
  raises ``script_error`` with a line number while the module executes — the
  ``PARAMS`` contract verbatim.
* **``clearance`` measures the ``analysis(p)`` envelope** when a script defines
  one, exactly as ``interference`` does. An envelope contains the real part, so
  the reported distance is an *under*-estimate — conservative in the safe
  direction. A mesh (STL) side is skipped because the query has no solution on
  a welded mesh Face (measured; see ``docs/changelog/0088-*``), ``SetDeflection``
  is left at its exact default (an approximate distance reported as a
  measurement would be dishonest), and a zero distance is simply the
  interference case: this handler does not also try to be
  ``check_interference_free``.

Records are emitted without an ``id`` or a ``part`` field: the worker does not
know which part it is building. ``SpecRunner`` joins them on ``index`` and adds
``<part>:<name>``.
"""

from __future__ import annotations

import copy
import math

from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from ...toolkit.specs import declaration_problem, json_safe
from .analysis import _min_wall

#: Kinds this handler can measure from one built shape (Decision 3, tier 1).
SHAPE_TIER = ("valid", "mass", "volume", "bbox", "wall", "that")

#: Reported alongside ``measured`` so a UI never has to guess.
UNITS = {"mass": "g", "volume": "mm3", "bbox": "mm", "wall": "mm"}

_DEFERRED_HINT = "run_specs evaluates this tier"
_SCOPE_HINT = ("a project-scope check is evaluated over the assembly by "
               "run_specs, not against one part")


def _slack(limit: float) -> float:
    """Comparison tolerance: a measurement is never exact to the last ulp."""
    return max(1e-9, abs(limit) * 1e-9)


def _limit(limit: dict, key: str, label: str | None = None):
    """One bound out of a declaration's ``limit``, guaranteed comparable.

    The constructors reject a non-finite number where the argument is read, but
    a hand-written ``SPECS`` entry reaches here directly — and every ordered
    comparison against NaN is false, so a NaN bound would report *pass* without
    measuring anything. Raising inside a per-check evaluation is an ``error``
    record, which is what 'this check cannot be decided' means.
    """
    value = limit.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise ValueError(
            f"{label or key} must be a finite number, got {value!r}")
    return float(value)


def _fmt(value: float) -> str:
    return f"{value:.4g}"


def _bounded(measured: float, limit: dict, lo_key: str, hi_key: str,
             unit: str, noun: str) -> dict:
    """A one- or two-sided numeric budget, with the message a human reads."""
    lo, hi = _limit(limit, lo_key), _limit(limit, hi_key)
    if lo is not None and measured < lo - _slack(lo):
        return {"status": "fail", "measured": measured,
                "message": f"{noun} {_fmt(measured)} {unit} is below the "
                           f"{_fmt(lo)} {unit} minimum"}
    if hi is not None and measured > hi + _slack(hi):
        return {"status": "fail", "measured": measured,
                "message": f"{noun} {_fmt(measured)} {unit} exceeds the "
                           f"{_fmt(hi)} {unit} maximum"}
    if lo is not None and hi is not None:
        within = f"[{_fmt(lo)}, {_fmt(hi)}] {unit}"
    elif lo is not None:
        within = f"the {_fmt(lo)} {unit} minimum"
    else:
        within = f"the {_fmt(hi)} {unit} maximum"
    return {"status": "pass", "measured": measured,
            "message": f"{noun} {_fmt(measured)} {unit} is within {within}"}


def _eval_valid(decl: dict, shape, metrics: dict) -> dict:
    is_valid, solids = bool(metrics["is_valid"]), int(metrics["n_solids"])
    ok = is_valid and solids > 0
    return {"status": "pass" if ok else "fail", "measured": ok,
            "details": {"is_valid": is_valid, "n_solids": solids},
            "message": "the part builds into a valid solid" if ok else
                       f"the part is not a valid solid (is_valid={is_valid}, "
                       f"n_solids={solids})"}


def _eval_mass(decl: dict, shape, metrics: dict) -> dict:
    return _bounded(float(metrics["mass_g"]), decl["limit"], "min_g", "max_g",
                    "g", "mass")


def _eval_volume(decl: dict, shape, metrics: dict) -> dict:
    return _bounded(float(metrics["volume_mm3"]), decl["limit"], "min_mm3",
                    "max_mm3", "mm3", "volume")


def _eval_bbox(decl: dict, shape, metrics: dict) -> dict:
    bbox = metrics["bbox"]
    size = [float(bbox["max"][i] - bbox["min"][i]) for i in range(3)]
    axes = decl["limit"].get("within_mm")
    if not isinstance(axes, (list, tuple)) or len(axes) != 3:
        raise ValueError(
            f"within_mm must be a finite number per axis, got {axes!r}")
    within = [_limit({"v": v}, "v", f"within_mm[{i}]")
              for i, v in enumerate(axes)]
    if None in within:
        raise ValueError(
            f"within_mm must be a finite number per axis, got {axes!r}")
    over = [axis for axis, i in (("x", 0), ("y", 1), ("z", 2))
            if size[i] > within[i] + _slack(within[i])]
    got = " x ".join(_fmt(v) for v in size)
    want = " x ".join(_fmt(v) for v in within)
    if over:
        return {"status": "fail", "measured": size,
                "message": f"bounding box {got} mm exceeds {want} mm on "
                           f"{', '.join(over)}"}
    return {"status": "pass", "measured": size,
            "message": f"bounding box {got} mm fits within {want} mm"}


def _eval_wall(decl: dict, shape, metrics: dict) -> dict:
    minimum = _limit(decl["limit"], "min_mm")
    if minimum is None:
        raise ValueError("min_mm must be a finite number, got None")
    probe = _min_wall(shape, minimum, int(decl["options"].get("grid", 8)))
    measured = probe["min_thickness_mm"]
    if measured is None:
        # No ray from a sampled face point hit an opposing face: there is
        # nothing to report, and reporting "ok" would be a lie.
        return {"status": "error", "measured": None,
                "message": "minimum wall thickness could not be measured "
                           "(no sampled ray hit an opposing face)"}
    ok = bool(probe.get("ok"))
    return {"status": "pass" if ok else "fail", "measured": float(measured),
            "location": probe["location"],
            "message": f"min wall {_fmt(measured)} mm is "
                       + ("at or above" if ok else "below")
                       + f" the {_fmt(minimum)} mm minimum"}


def _eval_that(decl: dict, shape, metrics: dict) -> dict:
    predicate = decl.get("fn")
    if not callable(predicate):
        # json_safe() strips the callable, so this is a declaration that made a
        # JSON round trip and can no longer be evaluated.
        return {"status": "error", "measured": None,
                "message": "check_that declaration has no predicate (it must "
                           "be evaluated in the process that read SPECS)"}
    returned = predicate(shape, metrics)          # (part, metrics), in-worker
    if not isinstance(returned, bool):
        return {"status": "error", "measured": None,
                "details": {"returned": repr(returned)[:80]},
                "message": f"predicate returned {type(returned).__name__}, "
                           "expected a bool"}
    return {"status": "pass" if returned else "fail", "measured": returned,
            "message": f"predicate {decl['name']!r} returned {returned}"}


_EVALUATORS = {"valid": _eval_valid, "mass": _eval_mass, "volume": _eval_volume,
               "bbox": _eval_bbox, "wall": _eval_wall, "that": _eval_that}


def register(toolbox: dict) -> dict:
    build_shape_ns = toolbox["build_shape_ns"]
    shape_metrics = toolbox["metrics"]
    place = toolbox["place"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]
    ERROR_KERNEL = toolbox["ERROR_KERNEL"]
    # Imported here, not at module import time: register() runs from
    # worker._load_handler_packs(), by which point worker is fully imported.
    # These are single sources of truth (script execution and its line-number
    # reporting, the item/reference split with its analysis envelope) that a
    # second implementation would silently fork.
    from ..worker import (_exec_script, _item_shape, _script_error_from_exc,
                          _solid_labels)

    def _declarations(ns: dict, scope: str) -> tuple[list, list]:
        """The raw SPECS list, structurally validated. Nothing is dropped: a
        scope mismatch is a warning naming the check, never a silent removal."""
        raw = ns.get("SPECS")
        if raw is None:
            return [], []
        if not isinstance(raw, (list, tuple)):
            raise WorkerError(
                ERROR_CONTRACT,
                "SPECS must be a list of declarations from "
                f"agentcad.toolkit.specs (got {type(raw).__name__})")
        declarations, warnings = [], []
        for index, entry in enumerate(raw):
            # The whole emitted shape, not just the marker: a dict missing
            # name/limit/options is residue every reader downstream would index
            # into, and the reason names the key that is wrong.
            problem = declaration_problem(entry)
            if problem is not None:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"SPECS[{index}] is not a declaration from "
                    f"agentcad.toolkit.specs ({problem}) — build it with one "
                    f"of that module's check_* constructors "
                    f"(got {entry!r:.60})")
            declarations.append(entry)
            if entry["scope"] != scope:
                warnings.append(
                    f"SPECS[{index}] {entry['name']!r} is a "
                    f"{entry['scope']}-scope check declared in a "
                    f"{scope}-scope module; it cannot be evaluated here")
        return declarations, warnings

    def _record(decl: dict, index: int) -> dict:
        # ``.get`` throughout, though ``_declarations`` has already validated
        # the shape: a record must degrade with a future format drift rather
        # than raise out of the handler and cost the caller a whole report.
        kind = decl.get("kind") or "unknown"
        return {"index": index, "name": decl.get("name") or f"spec_{index}",
                "kind": kind, "scope": decl.get("scope") or "part",
                "requirement": decl.get("requirement"),
                "limit": decl.get("limit") or {},
                "unit": UNITS.get(kind), "measured": None,
                "location": None, "message": "", "details": {}}

    def _evaluate(decl: dict, index: int, shape, metrics) -> dict:
        """One check, guarded on its own: this never raises (AC5)."""
        record = _record(decl, index)
        if record["scope"] != "part":
            return {**record, "status": "skip", "reason": "unsupported_scope",
                    "hint": _SCOPE_HINT,
                    "message": f"{record['kind']} is a project-scope check "
                               "and is not evaluated against a single part"}
        if record["kind"] not in SHAPE_TIER:
            return {**record, "status": "skip", "reason": "deferred",
                    "hint": _DEFERRED_HINT,
                    "message": f"{record['kind']} is not evaluated with the "
                               "shape tier"}
        try:
            # deepcopy, not the shared dict: a check_that predicate is script
            # code, and one that writes to metrics must not be able to change
            # what any other check measured (the metrics are plain JSON-safe
            # numbers, lists and dicts, so the copy is cheap).
            return {**record,
                    **_EVALUATORS[record["kind"]](decl, shape,
                                                  copy.deepcopy(metrics()))}
        except Exception as exc:  # noqa: BLE001 — a broken check is payload
            # _script_error_from_exc gives the traceback and, when a frame
            # belongs to the script, the line the predicate lives on.
            details = dict(_script_error_from_exc(exc).details)
            if details.get("line") is None:
                details.pop("line", None)
            return {**record, "status": "error",
                    "message": f"{type(exc).__name__}: {exc}",
                    "details": details}

    def handle_spec_declare(params: dict) -> dict:
        scope = params.get("scope") or "part"
        if scope not in ("part", "project"):
            raise WorkerError(
                ERROR_CONTRACT,
                f"scope must be 'part' or 'project', got {scope!r}")
        # _exec_script, not build_shape_ns: a project specs.py has no PARAMS
        # and no build(p), and a part must be able to declare before it builds.
        ns = _exec_script(params["script"])
        declarations, warnings = _declarations(ns, scope)
        return {"declared": [json_safe(d) for d in declarations],
                "warnings": warnings}

    def handle_spec_eval(params: dict) -> dict:
        shape, _values, warnings, ns = build_shape_ns(
            params["script"], params.get("params") or {})
        declarations, decl_warnings = _declarations(ns, "part")
        indices = params.get("indices")
        if indices is None:
            selected = list(range(len(declarations)))
        else:
            selected = [int(i) for i in indices]
            for position, index in enumerate(selected):
                if not 0 <= index < len(declarations):
                    raise WorkerError(
                        ERROR_CONTRACT,
                        f"indices[{position}] = {index} is out of range for "
                        f"{len(declarations)} declared spec(s)")

        cached: dict = {}

        def metrics() -> dict:
            # Computed at most once, and only if a selected check needs it.
            if "metrics" not in cached:
                cached["metrics"] = shape_metrics(
                    shape, float(params.get("density_g_cm3", 1.0)),
                    params.get("densities") or None, _solid_labels(ns))
            return cached["metrics"]

        # Predicates last, records in declared order: a mutating predicate
        # cannot reach a built-in check that has already been measured, and the
        # report a caller joins on ``index`` is unaffected by the reordering.
        evaluated = {
            i: _evaluate(declarations[i], i, shape, metrics)
            for i in sorted(selected,
                            key=lambda i: declarations[i]["kind"] == "that")
        }
        return {
            "checks": [evaluated[i] for i in selected],
            "declared": [json_safe(d) for d in declarations],
            "warnings": list(warnings) + decl_warnings,
        }

    def handle_clearance(params: dict) -> dict:
        minimum = params.get("min_mm")
        names = {side: (params.get(side) or {}).get("name", side)
                 for side in ("a", "b")}
        placed, skipped = {}, []
        for side in ("a", "b"):
            item = params.get(side) or {}
            try:
                # analysis=True: the conservative envelope, exactly as
                # handle_interference resolves the same assembly.
                shape, kind = _item_shape(item, analysis=True)
                if kind == "mesh":
                    # An STL is one welded mesh Face. Measured (changelog 0088):
                    # BRepExtrema does NOT segfault it the way a boolean does —
                    # it returns Perform() == False, NbSolution() == 0 in 0.5 ms
                    # and raises from Value(). So there is simply nothing to
                    # report, and a named skip beats a structured error.
                    skipped.append(side)
                    continue
                placed[side] = place(shape, item.get("position") or [0, 0, 0],
                                     item.get("rotation_deg") or [0, 0, 0])
            except WorkerError:
                raise           # a script_error keeps its line number
            except Exception as exc:  # noqa: BLE001
                raise WorkerError(
                    ERROR_KERNEL,
                    f"clearance unavailable: {type(exc).__name__}: {exc}",
                    {"stage": "resolve", "side": side, "a": names["a"],
                     "b": names["b"]}) from exc
        if skipped:
            return {"distance_mm": None, "point_a": None, "point_b": None,
                    "skipped_mesh": skipped}

        try:
            query = BRepExtrema_DistShapeShape()
            query.LoadS1(placed["a"].wrapped)
            query.LoadS2(placed["b"].wrapped)
            query.SetMultiThread(True)      # SetDeflection stays at its default
            performed = query.Perform()
            if not performed or not query.IsDone() or query.NbSolution() < 1:
                raise ValueError("no distance solution")
            distance = float(query.Value())
            point_a, point_b = query.PointOnShape1(1), query.PointOnShape2(1)
        except Exception as exc:  # noqa: BLE001 — structured, never fatal
            raise WorkerError(
                ERROR_KERNEL,
                f"clearance unavailable: {type(exc).__name__}: {exc}",
                {"stage": "distance", "a": names["a"], "b": names["b"]}) from exc

        result = {"distance_mm": distance,
                  "point_a": [point_a.X(), point_a.Y(), point_a.Z()],
                  "point_b": [point_b.X(), point_b.Y(), point_b.Z()]}
        if minimum is not None:
            minimum = float(minimum)
            result["ok"] = distance >= minimum - _slack(minimum)
        return result

    return {"spec_declare": handle_spec_declare, "spec_eval": handle_spec_eval,
            "clearance": handle_clearance}
