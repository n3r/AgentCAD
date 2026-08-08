"""Kernel worker subprocess: executes part scripts against build123d/OCCT.

Run as ``python -m agentcad.kernel.worker``. Speaks the line-delimited JSON
protocol from ``protocol.py`` on stdin/stdout. Imports build123d once (warm)
and stays alive across requests; the server kills and respawns it on hangs.

User scripts run with stdout redirected to stderr so stray ``print`` calls
cannot corrupt the protocol stream.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import traceback
import types
from collections import OrderedDict
from pathlib import Path

import build123d as b3d

from .mesh import tessellate
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
    return WorkerError(
        ERROR_SCRIPT,
        f"{type(exc).__name__}: {exc}",
        {"traceback": tb, "line": line},
    )


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
        default = entry["default"]
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            raise WorkerError(
                ERROR_CONTRACT, f"PARAMS[{name!r}]['default'] must be a number"
            )
        mn, mx = entry.get("min"), entry.get("max")
        for bound_name, bound in (("min", mn), ("max", mx)):
            if bound is not None and (
                isinstance(bound, bool) or not isinstance(bound, (int, float))
            ):
                raise WorkerError(
                    ERROR_CONTRACT, f"PARAMS[{name!r}][{bound_name!r}] must be a number"
                )
        if mn is not None and mx is not None and mn > mx:
            raise WorkerError(ERROR_CONTRACT, f"PARAMS[{name!r}]: min > max")
        if (mn is not None and default < mn) or (mx is not None and default > mx):
            raise WorkerError(
                ERROR_CONTRACT, f"PARAMS[{name!r}]: default outside [min, max]"
            )
    return spec


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
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WorkerError(
                ERROR_CONTRACT, f"parameter {name!r} must be a number, got {value!r}"
            )
        mn, mx = entry.get("min"), entry.get("max")
        if mn is not None and value < mn:
            warnings.append(f"param {name} clamped to min {mn}")
            value = mn
        if mx is not None and value > mx:
            warnings.append(f"param {name} clamped to max {mx}")
            value = mx
        values[name] = value
    return values, warnings


def _shape_key(script: str, values: dict) -> str:
    payload = script + "\x00" + json.dumps(values, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def build_shape(script: str, overrides: dict) -> tuple[object, dict, list[str]]:
    """Execute the script contract; returns (build123d shape, values, warnings)."""
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
        return _SHAPE_CACHE[key], values, warnings

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
    return result, values, warnings


# ------------------------------------------------------------------- geometry


def _metrics(shape, density_g_cm3: float) -> dict:
    volume = float(shape.volume)
    bb = shape.bounding_box()
    com = shape.center(b3d.CenterOf.MASS)
    return {
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


def _place(shape, position, rotation_deg):
    loc = b3d.Location(tuple(position), tuple(rotation_deg))
    return shape.moved(loc)


def _atomic_write(path: str, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def _export_shape(shape, fmt: str, out_path: str, tolerance: float) -> dict:
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(sys.stderr):
        if fmt == "step":
            b3d.export_step(shape, str(target))
        elif fmt == "stl":
            b3d.export_stl(shape, str(target), tolerance=tolerance)
        elif fmt == "3mf":
            mesher = b3d.Mesher()
            mesher.add_shape(shape, linear_deflection=tolerance)
            mesher.write(str(target))
        else:
            raise WorkerError(ERROR_CONTRACT, f"unknown export format {fmt!r}")
    return {"path": str(target), "size_bytes": target.stat().st_size}


# ------------------------------------------------------------------- handlers


def handle_ping(params: dict) -> dict:
    return {"ok": True, "build123d": getattr(b3d, "__version__", "unknown")}


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
            name: {
                "default": entry["default"],
                "min": entry.get("min"),
                "max": entry.get("max"),
                "unit": entry.get("unit"),
                "description": entry.get("description"),
            }
            for name, entry in spec.items()
        }
    }


def handle_build(params: dict) -> dict:
    shape, _values, warnings = build_shape(params["script"], params.get("params", {}))
    tolerance = float(params.get("tolerance", 0.1))
    buffer = tessellate(shape.wrapped, tolerance)
    _atomic_write(params["mesh_path"], buffer)
    return {
        "metrics": _metrics(shape, float(params.get("density_g_cm3", 1.0))),
        "warnings": warnings,
    }


def handle_export(params: dict) -> dict:
    shape, _values, _warnings = build_shape(params["script"], params.get("params", {}))
    return _export_shape(
        shape,
        params["format"],
        params["out_path"],
        float(params.get("tolerance", 0.05)),
    )


def handle_export_assembly(params: dict) -> dict:
    placed = []
    for item in params["items"]:
        shape, _v, _w = build_shape(item["script"], item.get("params", {}))
        placed.append(
            _place(shape, item.get("position", [0, 0, 0]), item.get("rotation_deg", [0, 0, 0]))
        )
    compound = b3d.Compound(children=placed)
    return _export_shape(
        compound, params["format"], params["out_path"], float(params.get("tolerance", 0.05))
    )


def handle_interference(params: dict) -> dict:
    items = params["items"]
    min_volume = float(params.get("min_volume", 0.001))
    placed = []
    for item in items:
        shape, _v, _w = build_shape(item["script"], item.get("params", {}))
        placed.append(
            (
                item.get("name", "?"),
                _place(shape, item.get("position", [0, 0, 0]), item.get("rotation_deg", [0, 0, 0])),
            )
        )
    pairs = []
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            name_a, shape_a = placed[i]
            name_b, shape_b = placed[j]
            # Note: Shape.intersect() returns a ShapeList in build123d 0.9;
            # the & operator returns a single Part (empty Compound when disjoint).
            with contextlib.redirect_stdout(sys.stderr):
                common = shape_a & shape_b
            volume = float(common.volume)
            if volume > min_volume:
                pairs.append({"a": name_a, "b": name_b, "volume_mm3": volume})
    return {"pairs": pairs}


HANDLERS = {
    "ping": handle_ping,
    "inspect": handle_inspect,
    "build": handle_build,
    "export": handle_export,
    "export_assembly": handle_export_assembly,
    "interference": handle_interference,
}


# ----------------------------------------------------------------- main loop


def _dispatch(method: str, params: dict) -> dict:
    handler = HANDLERS.get(method)
    if handler is None:
        raise WorkerError(ERROR_CONTRACT, f"unknown method {method!r}")
    try:
        return handler(params)
    except WorkerError:
        raise
    except Exception as exc:  # noqa: BLE001 — OCCT throws many exception types
        raise WorkerError(
            ERROR_KERNEL,
            f"{type(exc).__name__}: {exc}",
            {"traceback": traceback.format_exc()},
        ) from exc


def main() -> None:
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
        if method == "shutdown":
            stdout.write(json.dumps({"id": req_id, "result": {"ok": True}}) + "\n")
            stdout.flush()
            return
        try:
            result = _dispatch(method, request.get("params", {}))
            response = {"id": req_id, "result": result}
        except WorkerError as err:
            response = {"id": req_id, "error": err.to_payload()}
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()


if __name__ == "__main__":
    main()
