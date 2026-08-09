"""AgentCADService: the application service every client goes through.

Orchestrates the ProjectStore and KernelClient, caches meshes/metrics by
content hash, and publishes events on the EventBus. REST routes, MCP tools,
and the chat agent are all thin wrappers over this class.
"""

from __future__ import annotations

import hashlib
import json
import math
import queue
import threading
from pathlib import Path

from ..kernel.client import KernelClient, KernelError
from .materials import MATERIALS, get_material
from .model import InstanceSpec, NotFoundError, ValidationError, validate_vec3
from .project import ProjectStore
from .templates import CHEATSHEET, DEFAULT_PART_SCRIPT

MESH_TOLERANCE = 0.1
EXPORT_TOLERANCE = 0.05
EXPORT_FORMATS = ("step", "stl", "3mf")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # slow consumer: drop rather than block the kernel path


class _DefaultMaterialResolver:
    """v1 material lookup: the fixed builtin table, project-independent. The
    materials-v2 pack replaces ``service.materials`` with a project-aware,
    user-extensible resolver exposing the same ``density(proj, id)`` method."""

    def density(self, proj: str, material_id: str) -> float:
        return get_material(material_id).density_g_cm3


class AgentCADService:
    def __init__(self, projects_dir: Path, kernel: KernelClient, bus: EventBus):
        self.store = ProjectStore(projects_dir)
        self.kernel = kernel
        self.bus = bus
        self._lock = threading.RLock()
        self._status: dict[tuple[str, str], dict] = {}
        self._spec_cache: dict[str, dict] = {}
        # Seams the v2 feature packs replace; defaults preserve v1 behavior.
        self.materials = _DefaultMaterialResolver()

    def _resolved_instances(self, proj: str):
        """Assembly instances with any declarative mates resolved to concrete
        transforms. Seam: when the mates module is present it resolves mate
        chains (and rejects cycles); otherwise instances pass through."""
        instances = self.store.instances(proj)
        if not any(getattr(i, "mate", None) for i in instances):
            return instances
        try:
            from . import mates
        except ImportError:
            return instances
        return mates.resolve(self, proj, instances)

    # ------------------------------------------------------------- projects

    def list_projects(self) -> list[dict]:
        return self.store.list_projects()

    def create_project(self, name: str) -> dict:
        with self._lock:
            self.store.create(name)
        return self.get_project(name)

    def open_project(self, path: str) -> dict:
        with self._lock:
            name = self.store.open(path)
        return self.get_project(name)

    def get_project(self, proj: str) -> dict:
        manifest = self.store.manifest(proj)
        parts = []
        for entry in manifest["parts"]:
            status = self._status.get((proj, entry["id"]))
            parts.append(
                {
                    "id": entry["id"],
                    "label": entry.get("label", entry["id"]),
                    "material": entry.get("material"),
                    "params": entry.get("params", {}),
                    "kind": entry.get("kind", "script"),
                    "source": entry.get("source"),
                    "state": status["state"] if status else "unbuilt",
                }
            )
        return {
            "name": manifest["name"],
            "units": manifest.get("units", "mm"),
            "path": str(self.store.path_of(proj)),
            "parts": parts,
            "assembly": {
                "instances": [i.to_manifest() for i in self.store.instances(proj)]
            },
            "materials": self._materials_map(proj),
        }

    def _materials_map(self, proj: str) -> dict:
        """Resolved material catalog (builtin + project overrides) matching the
        densities mass metrics actually use. Falls back to builtins if the
        project-aware resolver isn't active."""
        effective = getattr(self.materials, "effective", None)
        try:
            catalog = effective(proj) if callable(effective) else MATERIALS
        except Exception:  # noqa: BLE001 — never let a bad materials file break get_project
            catalog = MATERIALS
        return {
            m.id: {"label": m.label, "density_g_cm3": m.density_g_cm3}
            for m in catalog.values()
        }

    # ---------------------------------------------------------------- parts

    def create_part(
        self,
        proj: str,
        part_id: str,
        label: str | None = None,
        script: str | None = None,
        material: str = "al6061",
        kind: str = "script",
        source: str | None = None,
    ) -> dict:
        if kind not in ("script", "reference"):
            raise ValidationError(f"unknown part kind {kind!r}")
        if kind == "reference" and not source:
            raise ValidationError("reference parts require a 'source' import path")
        with self._lock:
            self.store.add_part(
                proj, part_id, label or part_id, material,
                script or DEFAULT_PART_SCRIPT, kind=kind, source=source,
            )
        self.bus.publish({"type": "project_changed", "project": proj})
        return self.get_part(proj, part_id)

    def get_part(self, proj: str, part_id: str) -> dict:
        record = self.store.get_part(proj, part_id)
        is_reference = record.kind == "reference"
        script = None if is_reference else self.store.read_script(proj, part_id)
        self._ensure_built(proj, part_id)
        status = self._status.get((proj, part_id), {"state": "unbuilt"})
        detail = {
            "id": record.id,
            "label": record.label,
            "material": record.material,
            "params": record.params,
            "kind": record.kind,
            "source": record.source,
            "script": script,
            "params_spec": None if is_reference else self._params_spec(script),
            "status": {
                "state": status.get("state", "unbuilt"),
                "error": status.get("error"),
                "warnings": status.get("warnings", []),
            },
            "metrics": status.get("metrics"),
        }
        return detail

    def update_part(
        self,
        proj: str,
        part_id: str,
        script: str | None = None,
        label: str | None = None,
        material: str | None = None,
    ) -> dict:
        with self._lock:
            self.store.get_part(proj, part_id)
            if script is not None:
                self.store.write_script(proj, part_id, script)
            if label is not None or material is not None:
                self.store.update_part_entry(
                    proj, part_id, label=label, material=material
                )
        self.bus.publish({"type": "project_changed", "project": proj})
        return self._rebuild(proj, part_id)

    def set_params(self, proj: str, part_id: str, values: dict) -> dict:
        """Set parameter overrides. A value of None removes the override.

        Names and types are validated against the script's PARAMS spec *before*
        anything is written, so a bad call can never poison the manifest
        (spec §8). Numeric values are stored raw — the worker clamps at build.
        """
        for name, value in values.items():
            if value is None:
                continue
            if not isinstance(value, (int, float, bool, str)):
                raise ValidationError(
                    f"parameter {name!r} must be a JSON scalar "
                    "(number, bool, or string)"
                )
        with self._lock:
            record = self.store.get_part(proj, part_id)
            script = self.store.read_script(proj, part_id)
            spec = self._params_spec(script)
            if spec is None:
                raise ValidationError(
                    "cannot set parameters: the part script does not currently "
                    "load — fix the script first (see get_part.status)",
                )
            unknown = sorted(set(values) - set(spec))
            if unknown:
                raise ValidationError(
                    f"unknown parameter(s): {', '.join(unknown)}",
                    {"unknown": unknown, "known": sorted(spec)},
                )
            merged = dict(record.params)
            for name, value in values.items():
                if value is None:
                    merged.pop(name, None)
                else:
                    merged[name] = _normalize_param(name, spec[name], value)
            self.store.update_part_entry(proj, part_id, params=merged)
        return self._rebuild(proj, part_id)

    def delete_part(self, proj: str, part_id: str) -> None:
        with self._lock:
            self.store.remove_part(proj, part_id)
            self._status.pop((proj, part_id), None)
        self.bus.publish({"type": "project_changed", "project": proj})

    def get_metrics(self, proj: str, part_id: str) -> dict:
        self.store.get_part(proj, part_id)
        result = self._ensure_built(proj, part_id)
        if not result["ok"]:
            raise KernelErrorFromResult(result)
        return result["metrics"]

    def mesh_info(self, proj: str, part_id: str) -> dict:
        """Built mesh path + cache key, from the build result (no racy re-read
        of the shared status dict — a concurrent delete cannot KeyError us)."""
        self.store.get_part(proj, part_id)
        result = self._ensure_built(proj, part_id)
        if not result["ok"]:
            raise KernelErrorFromResult(result)
        key = result["cache_key"]
        return {"path": self.store.cache_dir(proj) / f"{key}.acm", "key": key}

    def ensure_mesh(self, proj: str, part_id: str) -> Path:
        return self.mesh_info(proj, part_id)["path"]

    def mesh_summary(self, proj: str, part_id: str) -> dict:
        from ..kernel import acm

        self.store.get_part(proj, part_id)
        result = self._ensure_built(proj, part_id)
        if not result["ok"]:
            raise KernelErrorFromResult(result)
        key = result["cache_key"]
        mesh = acm.read(self.store.cache_dir(proj) / f"{key}.acm")
        return {
            "vertices": len(mesh["positions"]),
            "triangles": len(mesh["indices"]),
            "edges": len(mesh["edge_lengths"]),
            "bbox": result["metrics"]["bbox"],
        }

    def export_part(
        self, proj: str, part_id: str, format: str, tolerance: float = EXPORT_TOLERANCE
    ) -> dict:
        self._check_format(format)
        record = self.store.get_part(proj, part_id)
        script = self.store.read_script(proj, part_id)
        out = self.store.exports_dir(proj) / f"{part_id}.{format}"
        result = self.kernel.request(
            "export",
            {
                "script": script,
                "params": record.params,
                "format": format,
                "out_path": str(out),
                "tolerance": tolerance,
            },
            timeout_s=300.0,  # export may rebuild the shape from scratch
        )
        return result

    # ------------------------------------------------------------- assembly

    def get_assembly(self, proj: str) -> dict:
        instances = self._resolved_instances(proj)
        detail = []
        total_mass = 0.0
        bounds_min = [math.inf] * 3
        bounds_max = [-math.inf] * 3
        for inst in instances:
            entry = inst.to_manifest()
            built = self._ensure_built(proj, inst.part)
            if built["ok"]:
                metrics = built["metrics"]  # from the build result, not a racy re-read
                entry["mass_g"] = metrics["mass_g"]
                entry["state"] = "ok"
                total_mass += metrics["mass_g"]
                corners = _bbox_corners(metrics["bbox"])
                for corner in corners:
                    world = _apply_transform(corner, inst.position, inst.rotation_deg)
                    for axis in range(3):
                        bounds_min[axis] = min(bounds_min[axis], world[axis])
                        bounds_max[axis] = max(bounds_max[axis], world[axis])
            else:
                entry["state"] = "error"
                entry["error"] = built["error"]
            detail.append(entry)
        bbox = (
            {"min": bounds_min, "max": bounds_max}
            if total_mass > 0 or any(map(math.isfinite, bounds_min))
            else None
        )
        return {
            "instances": detail,
            "total_mass_g": total_mass,
            "bbox": bbox if math.isfinite(bounds_min[0]) else None,
        }

    def set_assembly(self, proj: str, instances: list[dict]) -> dict:
        specs = []
        for item in instances:
            specs.append(
                InstanceSpec(
                    id=item.get("id", ""),
                    part=item.get("part", ""),
                    position=validate_vec3(
                        item.get("position", [0, 0, 0]), "position"
                    ),
                    rotation_deg=validate_vec3(
                        item.get("rotation_deg", [0, 0, 0]), "rotation_deg"
                    ),
                    color=item.get("color"),
                    mate=item.get("mate"),
                )
            )
        with self._lock:
            self.store.set_instances(proj, specs)
        self.bus.publish({"type": "project_changed", "project": proj})
        return self.get_assembly(proj)

    def _shape_item(self, proj: str, record, resolved) -> dict:
        """Build a worker item (script or reference) for a placed instance."""
        item = {
            "position": resolved.position,
            "rotation_deg": resolved.rotation_deg,
        }
        if record.kind == "reference":
            item["source"] = str(
                self.store.imports_dir(proj) / Path(record.source).name
            )
        else:
            item["script"] = self.store.read_script(proj, record.id)
            item["params"] = record.params
        return item

    def check_interference(self, proj: str, min_volume: float = 0.001) -> dict:
        resolved = self._resolved_instances(proj)
        items = []
        for inst in resolved:
            record = self.store.get_part(proj, inst.part)
            item = self._shape_item(proj, record, inst)
            item["name"] = inst.id
            items.append(item)
        if len(items) < 2:
            return {"pairs": [], "checked": len(items)}
        result = self.kernel.request(
            "interference", {"items": items, "min_volume": min_volume},
            timeout_s=300.0,
        )
        out = {"pairs": result["pairs"], "checked": len(items)}
        if result.get("skipped_mesh"):
            out["skipped_mesh"] = result["skipped_mesh"]
        return out

    def export_assembly(self, proj: str, format: str) -> dict:
        if format not in ("step", "stl"):
            raise ValidationError("assembly export supports formats: step, stl")
        items = []
        for inst in self._resolved_instances(proj):
            record = self.store.get_part(proj, inst.part)
            items.append(self._shape_item(proj, record, inst))
        if not items:
            raise ValidationError("assembly has no instances to export")
        out = self.store.exports_dir(proj) / f"assembly.{format}"
        return self.kernel.request(
            "export_assembly",
            {"items": items, "format": format, "out_path": str(out)},
            timeout_s=300.0,
        )

    # ---------------------------------------------------------------- misc

    def part_template(self) -> dict:
        return {"template": DEFAULT_PART_SCRIPT, "cheatsheet": CHEATSHEET}

    # -------------------------------------------------------------- rebuild

    def material_density(self, proj: str, material_id: str) -> float:
        """Density (g/cm^3) for mass metrics. Seam: the resolver is swapped for
        the project-aware v2 resolver; the default reads the builtin table."""
        return self.materials.density(proj, material_id)

    def _content_signature(self, proj: str, record) -> str:
        """Cache-key content for a part: script text for scripts, or file
        identity (path+mtime+size) for reference parts."""
        if record.kind == "reference":
            src = self.store.imports_dir(proj) / Path(record.source).name \
                if record.source else None
            if src and src.is_file():
                st = src.stat()
                return f"ref:{record.source}:{st.st_mtime_ns}:{st.st_size}"
            return f"ref:{record.source}:missing"
        return self.store.read_script(proj, record.id)

    def _cache_key_for(self, proj: str, record) -> str:
        return self._cache_key(
            self._content_signature(proj, record),
            record.params,
            self.material_density(proj, record.material),
        )

    def _cache_key(self, content: str, params: dict, density: float) -> str:
        payload = json.dumps(
            {
                "content": content,
                "params": {k: params[k] for k in sorted(params)},
                "density": density,
                "tolerance": MESH_TOLERANCE,
                "format": "acm1",
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def _ensure_built(self, proj: str, part_id: str) -> dict:
        status = self._status.get((proj, part_id))
        if status is not None and status["state"] in ("ok", "error"):
            record = self.store.get_part(proj, part_id)
            current = self._cache_key_for(proj, record)
            if status["state"] == "ok":
                mesh = self.store.cache_dir(proj) / f"{status['cache_key']}.acm"
                if mesh.is_file() and current == status["cache_key"]:
                    return {"ok": True, "metrics": status["metrics"],
                            "warnings": status.get("warnings", []),
                            "cache_key": status["cache_key"]}
            elif current == status["cache_key"]:
                return {"ok": False, "error": status["error"]}
        return self._rebuild(proj, part_id)

    def _rebuild(self, proj: str, part_id: str) -> dict:
        record = self.store.get_part(proj, part_id)
        density = self.material_density(proj, record.material)
        key = self._cache_key_for(proj, record)
        cache = self.store.cache_dir(proj)
        mesh_path = cache / f"{key}.acm"
        metrics_path = cache / f"{key}.metrics.json"

        self.bus.publish(
            {"type": "rebuild_started", "project": proj, "part": part_id}
        )

        if mesh_path.is_file() and metrics_path.is_file():
            try:
                stored = json.loads(metrics_path.read_text())
                cached_metrics = stored["metrics"]
            except (json.JSONDecodeError, KeyError, OSError):
                # A crash mid-write left a corrupt sidecar: discard it and
                # fall through to a fresh kernel build (spec §8).
                try:
                    metrics_path.unlink()
                except OSError:
                    pass
            else:
                self._status[(proj, part_id)] = {
                    "state": "ok",
                    "cache_key": key,
                    "metrics": cached_metrics,
                    "warnings": stored.get("warnings", []),
                    "error": None,
                }
                self.bus.publish(
                    {
                        "type": "rebuild_finished",
                        "project": proj,
                        "part": part_id,
                        "metrics": cached_metrics,
                        "cached": True,
                    }
                )
                return {"ok": True, "metrics": cached_metrics,
                        "warnings": stored.get("warnings", []),
                        "cache_key": key}

        if record.kind == "reference":
            method = "build_reference"
            build_params = {
                "source_path": str(
                    self.store.imports_dir(proj) / Path(record.source).name
                ),
                "density_g_cm3": density,
                "mesh_path": str(mesh_path),
                "tolerance": MESH_TOLERANCE,
            }
        else:
            method = "build"
            build_params = {
                "script": self.store.read_script(proj, part_id),
                "params": record.params,
                "density_g_cm3": density,
                "mesh_path": str(mesh_path),
                "tolerance": MESH_TOLERANCE,
            }
        try:
            result = self.kernel.request(
                method, build_params, timeout_s=300.0, affinity=part_id
            )
        except KernelError as exc:
            payload = exc.to_payload()
            self._status[(proj, part_id)] = {
                "state": "error",
                "cache_key": key,
                "metrics": self._status.get((proj, part_id), {}).get("metrics"),
                "warnings": [],
                "error": payload,
            }
            self.bus.publish(
                {
                    "type": "rebuild_failed",
                    "project": proj,
                    "part": part_id,
                    "error": payload,
                }
            )
            return {"ok": False, "error": payload}

        metrics = result["metrics"]
        warnings = result.get("warnings", [])
        ProjectStore._atomic_write(
            metrics_path,
            json.dumps({"metrics": metrics, "warnings": warnings}).encode(),
        )
        self._status[(proj, part_id)] = {
            "state": "ok",
            "cache_key": key,
            "metrics": metrics,
            "warnings": warnings,
            "error": None,
        }
        self.bus.publish(
            {
                "type": "rebuild_finished",
                "project": proj,
                "part": part_id,
                "metrics": metrics,
                "cached": False,
            }
        )
        return {"ok": True, "metrics": metrics, "warnings": warnings,
                "cache_key": key}

    def _params_spec(self, script: str) -> dict | None:
        key = hashlib.sha256(script.encode()).hexdigest()
        if key in self._spec_cache:
            return self._spec_cache[key]
        try:
            result = self.kernel.request("inspect", {"script": script})
            spec = result["params_spec"]
        except KernelError:
            spec = None  # negative-cache: a broken/hanging script is
            # inspected at most once per content hash, not on every read
        if len(self._spec_cache) > 256:
            self._spec_cache.clear()
        self._spec_cache[key] = spec
        return spec

    @staticmethod
    def _check_format(format: str) -> None:
        if format not in EXPORT_FORMATS:
            raise ValidationError(
                f"unknown export format {format!r}",
                {"known": list(EXPORT_FORMATS)},
            )


class KernelErrorFromResult(KernelError):
    """Adapts a failed RebuildResult back into a raisable KernelError."""

    def __init__(self, result: dict):
        err = result["error"]
        super().__init__(
            err.get("type", "kernel_error"),
            err.get("message", "build failed"),
            err.get("details", {}),
        )


def _normalize_param(name: str, entry: dict, value):
    """Validate one override against its (worker-normalized) spec entry and
    normalize its Python type. Out-of-range numbers are NOT clamped here —
    the worker clamps at build time with a warning."""
    ptype = entry.get("type") or "number"
    if ptype == "bool":
        if not isinstance(value, bool):
            raise ValidationError(f"parameter {name!r} must be a bool")
        return value
    if ptype == "enum":
        choices = entry.get("choices") or []
        # Canonicalize to the declared choice so the manifest stores the
        # author-declared value (int 3 for a caller's 3.0, not the raw float).
        # bools are never members: True == 1 would match a numeric choice.
        matched = (
            None
            if isinstance(value, bool)
            else next((c for c in choices if value == c), None)
        )
        if matched is None:
            raise ValidationError(
                f"parameter {name!r} must be one of the declared choices",
                {"choices": choices},
            )
        return matched
    if ptype == "string":
        if not isinstance(value, str):
            raise ValidationError(f"parameter {name!r} must be a string")
        max_len = entry.get("max_len") or 200
        if len(value) > max_len:
            raise ValidationError(f"parameter {name!r} exceeds max_len {max_len}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"parameter {name!r} must be a number")
    if ptype == "int":
        if isinstance(value, float) and not value.is_integer():
            raise ValidationError(f"parameter {name!r} must be an integer")
        return int(value)
    return float(value)


def _bbox_corners(bbox: dict) -> list[list[float]]:
    mn, mx = bbox["min"], bbox["max"]
    return [
        [x, y, z]
        for x in (mn[0], mx[0])
        for y in (mn[1], mx[1])
        for z in (mn[2], mx[2])
    ]


def _apply_transform(
    point: list[float], position: list[float], rotation_deg: list[float]
) -> list[float]:
    """Intrinsic XYZ Euler rotation (degrees) then translation.

    Intrinsic XYZ means R = Rx . Ry . Rz, so the Z rotation hits the vector
    first (matches build123d Location and THREE.Euler 'XYZ').
    """
    rx, ry, rz = (math.radians(a) for a in rotation_deg)
    x, y, z = point
    # rotate about Z
    x, y = (
        x * math.cos(rz) - y * math.sin(rz),
        x * math.sin(rz) + y * math.cos(rz),
    )
    # rotate about Y
    x, z = (
        x * math.cos(ry) + z * math.sin(ry),
        -x * math.sin(ry) + z * math.cos(ry),
    )
    # rotate about X
    y, z = (
        y * math.cos(rx) - z * math.sin(rx),
        y * math.sin(rx) + z * math.cos(rx),
    )
    return [x + position[0], y + position[1], z + position[2]]
