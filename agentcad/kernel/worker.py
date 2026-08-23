"""Kernel worker subprocess: executes part scripts against build123d/OCCT.

Run as ``python -m agentcad.kernel.worker``. Speaks the line-delimited JSON
protocol from ``protocol.py`` on stdin/stdout. Imports build123d once (warm)
and stays alive across requests; the server kills and respawns it on hangs.

User scripts run with stdout redirected to stderr so stray ``print`` calls
cannot corrupt the protocol stream.
"""

from __future__ import annotations

# Confinement first, before anything imports a geometry kernel: on Linux this
# process restricts *itself* (Landlock + seccomp) and applies its rlimits, so
# the OCCT import and every part script afterwards run inside it. A no-op when
# the client set no `AGENTCAD_CONFINE` (PRD-006, design spec Decision 1).
from ._preamble import REPORT as SANDBOX_REPORT
from ._preamble import apply_from_env

apply_from_env()

import contextlib  # noqa: E402 - everything below runs confined
import hashlib
import json
import os
import struct
import sys
import traceback
import types
from collections import OrderedDict
from pathlib import Path

import build123d as b3d

from ._meter import Meter
from .denials import active_facets, classify
from .handlers._bop import checked_common_volume
from . import paramclamp
from .mesh import tessellate, tessellate_with_faces
from .protocol import ERROR_CONTRACT, ERROR_KERNEL, ERROR_SCRIPT, WorkerError

SCRIPT_FILENAME = "<part>"
_SHAPE_CACHE: OrderedDict[str, object] = OrderedDict()
_SHAPE_CACHE_MAX = 16


# ---------------------------------------------------------------- script exec


def _script_error_from_exc(exc: BaseException) -> WorkerError:
    tb = traceback.format_exc()
    line = None
    if isinstance(exc, SyntaxError) and exc.filename == SCRIPT_FILENAME:
        line = exc.lineno
    else:
        for frame in reversed(traceback.extract_tb(exc.__traceback__)):
            if frame.filename == SCRIPT_FILENAME:
                line = frame.lineno
                break
    details = {"traceback": tb, "line": line}
    # Which promise the script walked into, when there is a promise at all.
    # `active` is what this worker's preamble ACTUALLY applied, not merely that
    # it was asked to: a payload whose every stage failed leaves a non-empty
    # REPORT and must still classify nothing, or an unconfined worker would
    # label an ordinary PermissionError a sandbox denial (Decision 9).
    #
    # Per **facet**, not one blanket boolean (review M3): an applied fork
    # budget is evidence for `process_count`, and none at all for
    # `filesystem` — so a NO_SANDBOX worker that still has its rlimits no
    # longer calls a plain DAC EACCES a sandbox denial. `active_facets` owns
    # the mapping, including the parent-installed `quotas` tiers (a Windows job
    # object's breach arrives here as an ordinary MemoryError).
    active = active_facets(SANDBOX_REPORT)
    denied = classify(type(exc).__name__, str(exc), active=active, traceback=tb)
    if denied is not None:
        details["denied"] = denied
    return WorkerError(ERROR_SCRIPT, f"{type(exc).__name__}: {exc}", details)


def _exec_script(script: str) -> dict:
    ns: dict = {"__name__": "__agentcad_part__"}
    try:
        code = compile(script, SCRIPT_FILENAME, "exec")
        with contextlib.redirect_stdout(sys.stderr):
            exec(code, ns)
    except WorkerError:
        raise
    except BaseException as exc:  # noqa: BLE001 — every script failure must be reported
        raise _script_error_from_exc(exc) from exc
    return ns


PARAM_TYPES = ("number", "int", "bool", "enum", "string")
# Spec fields legal only on some types (unit/description are always allowed).
_TYPE_ONLY_FIELDS = {
    "min": ("number", "int"),
    "max": ("number", "int"),
    "choices": ("enum",),
    "max_len": ("string",),
}
DEFAULT_MAX_LEN = 200


def _effective_max_len(entry: dict):
    """max_len with an explicit None treated as absent (inspect's normalized
    output emits None for unset fields, and must round-trip as PARAMS)."""
    max_len = entry.get("max_len")
    return DEFAULT_MAX_LEN if max_len is None else max_len


def _as_int(value):
    """The value as an int if it is an integral number (3 or 3.0), else None.
    bool passes isinstance(x, int), so it is rejected explicitly first."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def _numeric_field(name: str, field: str, value, ptype: str):
    if ptype == "int":
        coerced = _as_int(value)
        if coerced is None:
            raise WorkerError(
                ERROR_CONTRACT, f"PARAMS[{name!r}][{field!r}] must be an integer"
            )
        return coerced
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkerError(
            ERROR_CONTRACT, f"PARAMS[{name!r}][{field!r}] must be a number"
        )
    return value


def _validate_numeric_spec(name: str, entry: dict, ptype: str) -> None:
    default = _numeric_field(name, "default", entry["default"], ptype)
    mn = entry.get("min")
    mx = entry.get("max")
    mn = None if mn is None else _numeric_field(name, "min", mn, ptype)
    mx = None if mx is None else _numeric_field(name, "max", mx, ptype)
    if mn is not None and mx is not None and mn > mx:
        raise WorkerError(ERROR_CONTRACT, f"PARAMS[{name!r}]: min > max")
    if (mn is not None and default < mn) or (mx is not None and default > mx):
        raise WorkerError(
            ERROR_CONTRACT, f"PARAMS[{name!r}]: default outside [min, max]"
        )


def _valid_choice(c) -> bool:
    return isinstance(c, str) or (
        not isinstance(c, bool) and isinstance(c, (int, float))
    )


def _validate_params_spec(spec) -> dict:
    if not isinstance(spec, dict):
        raise WorkerError(ERROR_CONTRACT, "PARAMS must be a dict of parameter specs")
    for name, entry in spec.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise WorkerError(ERROR_CONTRACT, f"invalid parameter name {name!r}")
        if not isinstance(entry, dict) or "default" not in entry:
            raise WorkerError(
                ERROR_CONTRACT, f"PARAMS[{name!r}] must be a dict with a 'default'"
            )
        ptype = entry.get("type", "number")
        if ptype not in PARAM_TYPES:
            raise WorkerError(
                ERROR_CONTRACT,
                f"PARAMS[{name!r}]['type'] must be one of: {', '.join(PARAM_TYPES)}",
            )
        for field, legal_on in _TYPE_ONLY_FIELDS.items():
            # An explicit None means "absent" (inspect's normalized output
            # emits None for unset fields and must be legal as PARAMS).
            if entry.get(field) is not None and ptype not in legal_on:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"PARAMS[{name!r}][{field!r}] is not allowed on a {ptype} spec",
                )
        default = entry["default"]
        if ptype in ("number", "int"):
            _validate_numeric_spec(name, entry, ptype)
        elif ptype == "bool":
            if not isinstance(default, bool):
                raise WorkerError(
                    ERROR_CONTRACT, f"PARAMS[{name!r}]['default'] must be a bool"
                )
        elif ptype == "enum":
            choices = entry.get("choices")
            if (
                not isinstance(choices, list)
                or not choices
                or not all(_valid_choice(c) for c in choices)
            ):
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"PARAMS[{name!r}]['choices'] must be a non-empty list of "
                    "strings and/or numbers",
                )
            if isinstance(default, bool) or not any(default == c for c in choices):
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"PARAMS[{name!r}]: default {default!r} is not in choices",
                    {"choices": choices},
                )
        else:  # string
            if not isinstance(default, str):
                raise WorkerError(
                    ERROR_CONTRACT, f"PARAMS[{name!r}]['default'] must be a string"
                )
            max_len = _effective_max_len(entry)
            if isinstance(max_len, bool) or not isinstance(max_len, int) or max_len <= 0:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"PARAMS[{name!r}]['max_len'] must be a positive integer",
                )
            if len(default) > max_len:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"PARAMS[{name!r}]: default exceeds max_len {max_len}",
                )
    return spec


def _resolve_numeric(
    name: str, entry: dict, value, ptype: str, warnings: list[str]
):
    if ptype == "int":
        coerced = _as_int(value)
        if coerced is None:
            raise WorkerError(
                ERROR_CONTRACT, f"parameter {name!r} must be an integer, got {value!r}"
            )
        value = coerced
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkerError(
            ERROR_CONTRACT, f"parameter {name!r} must be a number, got {value!r}"
        )
    # A NaN passes neither the min nor the max comparison below (both are
    # False), so without this guard it would slip past the clamp and reach
    # build(p) as a degenerate value (PRD-007 review finding m-2). inf is left
    # alone — it clamps to max correctly.
    if paramclamp.is_nan(value):
        raise WorkerError(
            ERROR_CONTRACT, f"parameter {name!r} must be a finite number, got {value!r}"
        )
    return paramclamp.clamp_numeric(entry, value, name, ptype, warnings)


def _resolve_params(spec: dict, overrides: dict) -> tuple[dict, list[str]]:
    unknown = sorted(set(overrides) - set(spec))
    if unknown:
        raise WorkerError(
            ERROR_CONTRACT,
            f"unknown parameter(s): {', '.join(unknown)}",
            {"unknown": unknown, "known": sorted(spec)},
        )
    values: dict = {}
    warnings: list[str] = []
    for name, entry in spec.items():
        value = overrides.get(name, entry["default"])
        ptype = entry.get("type", "number")
        if ptype in ("number", "int"):
            value = _resolve_numeric(name, entry, value, ptype, warnings)
        elif ptype == "bool":
            if not isinstance(value, bool):
                raise WorkerError(
                    ERROR_CONTRACT, f"parameter {name!r} must be a bool, got {value!r}"
                )
        elif ptype == "enum":
            choices = entry["choices"]
            # Canonicalize to the declared choice (3.0 matches an int 3 but
            # must reach build(p) — and the shape-cache key — as the int).
            # bools are never members: True == 1 would match a numeric choice.
            matched = (
                None
                if isinstance(value, bool)
                else next((c for c in choices if value == c), None)
            )
            if matched is None:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"parameter {name!r} must be one of "
                    f"{', '.join(repr(c) for c in choices)}, got {value!r}",
                    {"choices": choices},
                )
            value = matched
        else:  # string
            if not isinstance(value, str):
                raise WorkerError(
                    ERROR_CONTRACT, f"parameter {name!r} must be a string, got {value!r}"
                )
            max_len = _effective_max_len(entry)
            if len(value) > max_len:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"parameter {name!r} exceeds max_len {max_len}",
                )
        values[name] = value
    return values, warnings


def _shape_key(script: str, values: dict) -> str:
    payload = script + "\x00" + json.dumps(values, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def build_shape(script: str, overrides: dict) -> tuple[object, dict, list[str]]:
    """Execute the script contract; returns (build123d shape, values, warnings)."""
    shape, values, warnings, _ns = build_shape_ns(script, overrides)
    return shape, values, warnings


def build_shape_ns(script: str, overrides: dict) -> tuple[object, dict, list[str], dict]:
    """Like build_shape but also returns the script's module namespace, so
    callers can access optional contract additions such as connectors(p, part)."""
    ns = _exec_script(script)
    if "PARAMS" not in ns:
        raise WorkerError(ERROR_CONTRACT, "script must define a PARAMS dict")
    if "build" not in ns or not callable(ns["build"]):
        raise WorkerError(ERROR_CONTRACT, "script must define a build(p) function")
    spec = _validate_params_spec(ns["PARAMS"])
    values, warnings = _resolve_params(spec, overrides)

    key = _shape_key(script, values)
    if key in _SHAPE_CACHE:
        _SHAPE_CACHE.move_to_end(key)
        return _SHAPE_CACHE[key], values, warnings, ns

    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = ns["build"](types.SimpleNamespace(**values))
    except BaseException as exc:  # noqa: BLE001
        raise _script_error_from_exc(exc) from exc

    if isinstance(result, b3d.BuildPart):
        result = result.part
    if not isinstance(result, (b3d.Solid, b3d.Compound, b3d.Part)):
        raise WorkerError(
            ERROR_CONTRACT,
            "build(p) must return a build123d Part, Solid, or Compound "
            f"(got {type(result).__name__})",
        )

    _SHAPE_CACHE[key] = result
    if len(_SHAPE_CACHE) > _SHAPE_CACHE_MAX:
        _SHAPE_CACHE.popitem(last=False)
    return result, values, warnings, ns


# ------------------------------------------------------------------- geometry


def _shape_volume(shape) -> float:
    """Total solid volume. build123d 0.11 ``Compound.volume`` undercounts a
    *nested* compound (it reports only the first child subtree), so sum over
    the flattened solid list; fall back to ``.volume`` for non-solid shapes
    (e.g. an imported STL Face)."""
    solids = shape.solids()
    if solids:
        return float(sum(s.volume for s in solids))
    return float(shape.volume)


def _metrics(shape, density_g_cm3: float, densities: dict | None = None,
             labels: list | None = None) -> dict:
    """Whole-shape metrics; multi-solid shapes additionally get a per-solid
    ``solids`` list. ``labels`` names solids by index (fallback "solid_<i>");
    ``densities`` maps label-or-index-string to a density override, a solid's
    density resolving label match > index match > ``density_g_cm3``. The
    aggregate mass of a multi-solid shape is the sum of per-solid masses;
    single-solid shapes keep the plain volume*density math."""
    volume = _shape_volume(shape)
    bb = shape.bounding_box()
    com = shape.center(b3d.CenterOf.MASS)
    out = {
        "volume_mm3": volume,
        "area_mm2": float(shape.area),
        "mass_g": volume * density_g_cm3 / 1000.0,
        "bbox": {
            "min": [bb.min.X, bb.min.Y, bb.min.Z],
            "max": [bb.max.X, bb.max.Y, bb.max.Z],
        },
        "center_of_mass": [com.X, com.Y, com.Z],
        "is_valid": bool(shape.is_valid),
        "n_faces": len(shape.faces()),
        "n_edges": len(shape.edges()),
        "n_solids": len(shape.solids()),
    }
    solids = shape.solids()
    if len(solids) > 1:
        per_solid = []
        for i, solid in enumerate(solids):
            label = labels[i] if labels and i < len(labels) else f"solid_{i}"
            density = density_g_cm3
            if densities:
                if label in densities:
                    density = float(densities[label])
                elif str(i) in densities:
                    density = float(densities[str(i)])
            solid_volume = float(solid.volume)
            sbb = solid.bounding_box()
            scom = solid.center(b3d.CenterOf.MASS)
            per_solid.append({
                "label": label,
                "volume_mm3": solid_volume,
                "mass_g": solid_volume * density / 1000.0,
                "bbox": {
                    "min": [sbb.min.X, sbb.min.Y, sbb.min.Z],
                    "max": [sbb.max.X, sbb.max.Y, sbb.max.Z],
                },
                "center_of_mass": [scom.X, scom.Y, scom.Z],
            })
        out["solids"] = per_solid
        out["mass_g"] = float(sum(s["mass_g"] for s in per_solid))
    return out


def _place(shape, position, rotation_deg):
    loc = b3d.Location(tuple(position), tuple(rotation_deg))
    return shape.moved(loc)


def _atomic_write(path: str, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def _write_lod_tiers(ocp_shape, params: dict, buffer: bytes) -> tuple[int, list[str]]:
    """Write coarse LOD sidecar meshes next to the already-written full mesh.

    ``params["lod_tolerances"]`` maps a filename suffix (e.g. ``"lod1"``) to a
    tessellation tolerance; each requested tier is written as
    ``<mesh_path minus .acm>.<suffix>.acm`` in the same ACM1 format — but only
    when the full mesh's triangle count exceeds ``params["lod_min_triangles"]``
    (default 0), so small parts never pay for an extra tessellation. The full
    mesh buffer already written is never touched (its bytes stay pinned).

    Returns ``(full-mesh triangle count, list of tier suffixes written)``.
    """
    # ACM1 header: magic(4) | nv u32 | nt u32 | ... — triangle count at byte 8.
    triangles = struct.unpack_from("<I", buffer, 8)[0]
    lod_tolerances = params.get("lod_tolerances") or {}
    min_triangles = int(params.get("lod_min_triangles") or 0)
    lods: list[str] = []
    if not lod_tolerances or triangles <= min_triangles:
        return triangles, lods
    # OCCT keeps an existing (finer) triangulation that already satisfies a
    # coarser deflection, so the cached full-resolution mesh must be dropped
    # or the tier would silently duplicate the full mesh.
    from OCP.BRepTools import BRepTools  # kernel process only

    mesh_path = str(params["mesh_path"])
    base = mesh_path[: -len(".acm")] if mesh_path.endswith(".acm") else mesh_path
    for suffix in sorted(lod_tolerances):
        BRepTools.Clean_s(ocp_shape)
        lod_buffer = tessellate(ocp_shape, float(lod_tolerances[suffix]))
        _atomic_write(f"{base}.{suffix}.acm", lod_buffer)
        lods.append(suffix)
    return triangles, lods


def _export_shape(shape, fmt: str, out_path: str, tolerance: float) -> dict:
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file with the same suffix (exporters sniff extensions),
    # then atomically replace, so a killed export never leaves a torn file.
    tmp = target.with_name(f".{target.stem}.tmp{target.suffix}")
    with contextlib.redirect_stdout(sys.stderr):
        if fmt == "step":
            b3d.export_step(shape, str(tmp))
        elif fmt == "stl":
            b3d.export_stl(shape, str(tmp), tolerance=tolerance)
        elif fmt == "3mf":
            mesher = b3d.Mesher()
            mesher.add_shape(shape, linear_deflection=tolerance)
            mesher.write(str(tmp))
        else:
            raise WorkerError(ERROR_CONTRACT, f"unknown export format {fmt!r}")
    os.replace(tmp, target)
    return {"path": str(target), "size_bytes": target.stat().st_size}


# ------------------------------------------------------------------- handlers


def handle_ping(params: dict) -> dict:
    # `sandbox` is what this process ACTUALLY applied to itself, which is how
    # the client learns its live confinement rather than assuming the plan it
    # asked for landed (design spec, Decision 8). Empty when unconfined.
    return {"ok": True, "build123d": getattr(b3d, "__version__", "unknown"),
            "sandbox": dict(SANDBOX_REPORT)}


def handle_inspect(params: dict) -> dict:
    """Validate the script contract and return normalized PARAMS specs."""
    ns = _exec_script(params["script"])
    if "PARAMS" not in ns:
        raise WorkerError(ERROR_CONTRACT, "script must define a PARAMS dict")
    if "build" not in ns or not callable(ns["build"]):
        raise WorkerError(ERROR_CONTRACT, "script must define a build(p) function")
    spec = _validate_params_spec(ns["PARAMS"])
    return {
        "params_spec": {
            name: _normalized_spec_entry(entry) for name, entry in spec.items()
        }
    }


def _normalized_spec_entry(entry: dict) -> dict:
    ptype = entry.get("type", "number")
    out = {
        "type": ptype,
        "default": entry["default"],
        "min": entry.get("min"),
        "max": entry.get("max"),
        "unit": entry.get("unit"),
        "description": entry.get("description"),
    }
    if ptype == "enum":
        out["choices"] = list(entry["choices"])
    if ptype == "string":
        out["max_len"] = _effective_max_len(entry)
    return out


def _solid_labels(ns: dict) -> list | None:
    """Optional SOLID_LABELS contract addition: a list of solid names applied
    by index. Advisory — extra labels beyond n_solids are ignored (with a
    warning added by handle_build)."""
    labels = ns.get("SOLID_LABELS")
    if labels is None:
        return None
    if not isinstance(labels, list) or not all(
        isinstance(label, str) for label in labels
    ):
        raise WorkerError(
            ERROR_CONTRACT, "SOLID_LABELS must be a list of strings"
        )
    return labels


def handle_build(params: dict) -> dict:
    shape, _values, warnings, ns = build_shape_ns(
        params["script"], params.get("params", {})
    )
    labels = _solid_labels(ns)
    tolerance = float(params.get("tolerance", 0.1))
    buffer, face_ids = tessellate_with_faces(shape.wrapped, tolerance)
    _atomic_write(params["mesh_path"], buffer)
    # Triangle->B-rep-face sidecar (one u32 per triangle, mesh face order):
    # written after the mesh so a reader that sees the sidecar has the mesh.
    mesh_path = str(params["mesh_path"])
    base_path = mesh_path[: -len(".acm")] if mesh_path.endswith(".acm") else mesh_path
    _atomic_write(f"{base_path}.faces.u32", face_ids)
    triangles, lods = _write_lod_tiers(shape.wrapped, params, buffer)
    densities = params.get("densities") or {}
    metrics = _metrics(
        shape,
        float(params.get("density_g_cm3", 1.0)),
        densities=densities or None,
        labels=labels,
    )
    warnings = list(warnings)
    if labels and len(labels) > metrics["n_solids"]:
        warnings.append(
            f"SOLID_LABELS has {len(labels)} labels but the part has "
            f"{metrics['n_solids']} solid(s); extra labels are ignored"
        )
    solids = metrics.get("solids") or []
    matchable = {s["label"] for s in solids} | {str(i) for i in range(len(solids))}
    for key in sorted(densities):
        if key not in matchable:
            warnings.append(f"solid_materials: no solid matches {key}")
    return {
        "metrics": metrics,
        "warnings": warnings,
        "triangles": triangles,
        "lods": lods,
    }


def handle_export(params: dict) -> dict:
    shape, _values, _warnings = build_shape(params["script"], params.get("params", {}))
    return _export_shape(
        shape,
        params["format"],
        params["out_path"],
        float(params.get("tolerance", 0.05)),
    )


def _item_shape(item: dict, analysis: bool = False) -> tuple[object, str]:
    """Resolve an assembly/interference item to (shape, kind).

    Script items carry ``script`` (+ ``params``); reference items carry
    ``source`` (a path). ``kind`` is "script"/"solid" (booleanable) or "mesh"
    (STL — placeable and exportable, but not booleanable).

    With ``analysis=True``, a script that defines the optional contract
    function ``analysis(p)`` gets that shape instead — a simplified,
    CONSERVATIVE stand-in for interference checking (the canonical case:
    real ISO thread geometry replaced by its nominal-diameter envelope,
    which strictly contains the thread — clear envelope implies clear
    thread). Display, export, and metrics always use ``build(p)``."""
    if item.get("source"):
        from .refload import load_reference

        shape, ref_kind = load_reference(item["source"])
        return shape, ref_kind
    shape, values, _warnings, ns = build_shape_ns(
        item["script"], item.get("params", {}))
    fn = ns.get("analysis")
    if analysis and callable(fn):
        import types

        key = _shape_key(item["script"], values) + ":analysis"
        if key in _SHAPE_CACHE:
            _SHAPE_CACHE.move_to_end(key)
            return _SHAPE_CACHE[key], "script"
        result = fn(types.SimpleNamespace(**values))
        if hasattr(result, "part") and result.part is not None:
            result = result.part
        _SHAPE_CACHE[key] = result
        if len(_SHAPE_CACHE) > _SHAPE_CACHE_MAX:
            _SHAPE_CACHE.popitem(last=False)
        return result, "script"
    return shape, "script"


def handle_export_assembly(params: dict) -> dict:
    placed = []
    for item in params["items"]:
        shape, _kind = _item_shape(item)
        placed.append(
            _place(shape, item.get("position", [0, 0, 0]), item.get("rotation_deg", [0, 0, 0]))
        )
    compound = b3d.Compound(children=placed)
    return _export_shape(
        compound, params["format"], params["out_path"], float(params.get("tolerance", 0.05))
    )


def pairwise_interference(placed, min_volume: float = 0.001) -> list[dict]:
    """Exact pairwise intersection volumes for ``[(name, shape), ...]``.

    Decomposes every shape into its solids up front, for two reasons:
    1. build123d 0.9's ``&`` silently misbehaves when an operand is a
       multi-solid Compound (bolt sets etc.) — Solid-vs-Solid is reliable;
    2. per-solid AABB prefiltering skips almost all of the N*M work for
       hardware compounds, where only one screw is near any given part.
    When a solid pair's AABB overlap is small relative to the larger solid,
    the larger one is cropped to the overlap box first (a cheap prismatic
    boolean), so a small detailed solid — a threaded stud — is never
    intersected against a whole casting. Shared by ``handle_interference``
    and the motion sweep's per-sample collision check.

    Every Solid-vs-Solid boolean goes through
    ``handlers/_bop.checked_common_volume``: OCCT 7.9 answers ``IsDone()``
    while returning an empty (or negative-volume) intersection for operands
    with G1-tangent face junctions — any filleted swept solid, e.g. the
    ``examples/engine`` manifolds — which this function used to bank as a
    clean 0.0. A pair whose boolean could not be computed is reported
    **fail-closed**: it lands in the returned list with an extra
    ``"degenerate": True`` key whatever its volume, and its ``volume_mm3``
    (0.0) carries no information. That optional key is the only change to the
    return shape; readers that do not know it see an ordinary interfering
    pair, which is the safe reading.
    """
    decomposed = []
    for name, shape in placed:
        solids = shape.solids() or [shape]
        decomposed.append((name, [(s, s.bounding_box()) for s in solids],
                           shape.bounding_box()))

    def _boxes_touch(a, b, tol=0.05):
        return not (
            a.max.X < b.min.X - tol or b.max.X < a.min.X - tol
            or a.max.Y < b.min.Y - tol or b.max.Y < a.min.Y - tol
            or a.max.Z < b.min.Z - tol or b.max.Z < a.min.Z - tol
        )

    def _box_volume(bb):
        return max(bb.max.X - bb.min.X, 0) * max(bb.max.Y - bb.min.Y, 0) * \
            max(bb.max.Z - bb.min.Z, 0)

    def _crop_box(a, b, tol=0.05):
        lo = (max(a.min.X, b.min.X) - tol, max(a.min.Y, b.min.Y) - tol,
              max(a.min.Z, b.min.Z) - tol)
        hi = (min(a.max.X, b.max.X) + tol, min(a.max.Y, b.max.Y) + tol,
              min(a.max.Z, b.max.Z) + tol)
        size = [hi[k] - lo[k] for k in range(3)]
        center = [(hi[k] + lo[k]) / 2 for k in range(3)]
        return b3d.Pos(*center) * b3d.Box(*size), \
            size[0] * size[1] * size[2]

    def _common_vol(a, ba, b, bb):
        """(volume, degenerate) for one Solid-vs-Solid boolean."""
        return checked_common_volume(a, ba, b, bb, _shape_volume)

    def _solid_common(sa, ba, sb, bb):
        box, ov = _crop_box(ba, bb)
        big, big_bb = (sa, ba) if _box_volume(ba) >= _box_volume(bb) \
            else (sb, bb)
        small, small_bb = (sb, bb) if big is sa else (sa, ba)
        if ov < 0.4 * _box_volume(big_bb):
            cropped = big & box
            if cropped is None:
                return 0.0, False
            # the crop may split into pieces (or vanish) — keep every
            # boolean strictly Solid-vs-Solid
            total, degenerate = 0.0, False
            for piece in cropped.solids():
                volume, bad = _common_vol(piece, piece.bounding_box(),
                                          small, small_bb)
                total += volume
                degenerate = degenerate or bad
            return total, degenerate
        return _common_vol(big, big_bb, small, small_bb)

    pairs = []
    for i in range(len(decomposed)):
        for j in range(i + 1, len(decomposed)):
            name_a, sols_a, box_a = decomposed[i]
            name_b, sols_b, box_b = decomposed[j]
            if not _boxes_touch(box_a, box_b):
                continue
            volume, degenerate = 0.0, False
            with contextlib.redirect_stdout(sys.stderr):
                for sa, ba in sols_a:
                    for sb, bb in sols_b:
                        if _boxes_touch(ba, bb):
                            part, bad = _solid_common(sa, ba, sb, bb)
                            volume += part
                            degenerate = degenerate or bad
            # Fail closed: a pair OCCT could not boolean is reported even
            # though its measured volume is 0.0.
            if volume > min_volume or degenerate:
                entry = {"a": name_a, "b": name_b, "volume_mm3": volume}
                if degenerate:
                    entry["degenerate"] = True
                pairs.append(entry)
    return pairs


def handle_interference(params: dict) -> dict:
    items = params["items"]
    min_volume = float(params.get("min_volume", 0.001))
    placed = []
    skipped_mesh = []
    for item in items:
        shape, kind = _item_shape(item, analysis=True)
        if kind == "mesh":
            # Booleans on an STL mesh Face segfault OCCT — exclude it from the
            # pairwise check and report it so the caller can surface the gap.
            skipped_mesh.append(item.get("name", "?"))
            continue
        placed.append(
            (
                item.get("name", "?"),
                _place(shape, item.get("position", [0, 0, 0]), item.get("rotation_deg", [0, 0, 0])),
            )
        )
    pairs = pairwise_interference(placed, min_volume)
    result = {"pairs": pairs}
    if skipped_mesh:
        result["skipped_mesh"] = skipped_mesh
    return result


HANDLERS = {
    "ping": handle_ping,
    "inspect": handle_inspect,
    "build": handle_build,
    "export": handle_export,
    "export_assembly": handle_export_assembly,
    "interference": handle_interference,
}


# A shared toolbox handed to handler packs so v2 features (reference imports,
# drawings, analysis, FEM, connectors) reuse the exact build/metric/export/
# tessellate paths instead of re-deriving them. Extension point: each module
# in agentcad/kernel/handlers/ may export HANDLERS (dict) and/or a
# register(toolbox) -> dict function.
WORKER_TOOLBOX = {
    "b3d": b3d,
    "build_shape": build_shape,
    "build_shape_ns": build_shape_ns,
    "metrics": _metrics,
    "shape_volume": _shape_volume,
    "place": _place,
    "export_shape": _export_shape,
    "atomic_write": _atomic_write,
    "tessellate": tessellate,
    "write_lod_tiers": _write_lod_tiers,
    "WorkerError": WorkerError,
    "ERROR_SCRIPT": ERROR_SCRIPT,
    "ERROR_CONTRACT": ERROR_CONTRACT,
    "ERROR_KERNEL": ERROR_KERNEL,
}


def _load_handler_packs() -> None:
    """Discover agentcad/kernel/handlers/*.py and merge their handlers."""
    import importlib
    import pkgutil

    try:
        from . import handlers as handlers_pkg
    except ImportError:
        return
    for info in pkgutil.iter_modules(handlers_pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f".handlers.{info.name}", __package__)
        register = getattr(module, "register", None)
        pack = register(WORKER_TOOLBOX) if callable(register) else None
        pack = pack if isinstance(pack, dict) else getattr(module, "HANDLERS", {})
        for name, fn in pack.items():
            if name in HANDLERS:
                print(f"warning: handler {name!r} from {info.name} shadows a "
                      "builtin; ignoring", file=sys.stderr)
                continue
            HANDLERS[name] = fn


def _diagnose(err: "WorkerError") -> "WorkerError":
    """Error Doctor: enrich a kernel/script/contract error with a plain-language
    hint keyed off its message + traceback. No-op if the doctor module is
    absent or already provided a hint."""
    if err.details.get("hint"):
        return err
    try:
        from .error_doctor import diagnose
    except ImportError:
        return err
    hint = diagnose(err.type, err.message, err.details.get("traceback", ""))
    if hint:
        err.details = {**err.details, "hint": hint}
    return err


# ----------------------------------------------------------------- main loop


def _dispatch(method: str, params: dict) -> dict:
    handler = HANDLERS.get(method)
    if handler is None:
        raise WorkerError(ERROR_CONTRACT, f"unknown method {method!r}")
    try:
        return handler(params)
    except WorkerError as err:
        raise _diagnose(err) from err
    except Exception as exc:  # noqa: BLE001 — OCCT throws many exception types
        raise _diagnose(
            WorkerError(
                ERROR_KERNEL,
                f"{type(exc).__name__}: {exc}",
                {"traceback": traceback.format_exc()},
            )
        ) from exc


def main() -> None:
    _load_handler_packs()
    stdout = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        req_id = request.get("id")
        method = request.get("method", "")
        # One meter per request, started before the handler and read after it,
        # so `usage` describes this request and not the warm worker's history
        # (design spec, Decision 6). It rides every response line: result,
        # error and shutdown alike.
        meter = Meter()
        meter.start()
        if method == "shutdown":
            stdout.write(json.dumps({"id": req_id, "result": {"ok": True},
                                     "usage": meter.finish()}) + "\n")
            stdout.flush()
            return
        try:
            result = _dispatch(method, request.get("params", {}))
            response = {"id": req_id, "result": result}
        except WorkerError as err:
            response = {"id": req_id, "error": err.to_payload()}
        response["usage"] = meter.finish()
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()


if __name__ == "__main__":
    main()
