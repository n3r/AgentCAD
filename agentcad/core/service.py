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
import re
import sys
import threading
from pathlib import Path
from typing import Callable

from ..kernel.client import KernelClient, KernelError
from .history import ProjectHistory, UndoCursor
from .locks import TurnLock, current_client_id
from .materials import MATERIALS, get_material
from .model import InstanceSpec, NotFoundError, ValidationError, validate_vec3
from .project import ProjectStore
from .templates import CHEATSHEET, DEFAULT_PART_SCRIPT

MESH_TOLERANCE = 0.1
EXPORT_TOLERANCE = 0.05
EXPORT_FORMATS = ("step", "stl", "3mf")

# Coarse preview tier for progressive mesh loading. Every kernel build is
# asked for the tier; the WORKER writes it only when the full mesh's triangle
# count exceeds the threshold, so small parts never pay for it. Tiers live as
# <key>.lod1.acm sidecars next to the full <key>.acm (same ACM1 format).
MESH_LOD_TOLERANCE = 0.8
LOD_TRIANGLE_THRESHOLD = 150_000

# A tier suffix names a cache sidecar file, so it must stay a plain token
# (never a path fragment) no matter what a URL query hands us.
_LOD_SUFFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        # Optional pre-fan-out hook, invoked synchronously with each event
        # before it reaches subscribers. The service uses it to snapshot
        # project history on every project_changed publish — the one seam
        # that sees every mutation path (service methods AND pack tools).
        self.on_publish: Callable[[dict], None] | None = None

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
        hook = self.on_publish
        if hook is not None:
            try:
                hook(event)
            except Exception as exc:  # noqa: BLE001 — a hook bug must never
                # break event delivery (or the mutation that published).
                print(f"[bus] on_publish hook failed: {exc}", file=sys.stderr)
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
        # Per-part build state, keyed by _status_key: the caller's working
        # tree, not the project name, so branches keep their own badges.
        self._status: dict[tuple[str, str], dict] = {}
        self._spec_cache: dict[str, dict] = {}
        # Seams the v2 feature packs replace; defaults preserve v1 behavior.
        self.materials = _DefaultMaterialResolver()
        # Multi-user turn locking: every persistent store write is checked
        # against the per-project turn lock under the caller's identity.
        # With no lock held the guard is a no-op (full backward compat).
        self.turnlock = TurnLock()
        self.store.write_guard = (
            lambda proj: self.turnlock.check(proj, current_client_id())
        )
        # Git-backed project history: snapshot on every project_changed
        # publish. Hooking the bus (not the mutating methods) means pack
        # mutations — mates/materials/PMI/solids, which write through the
        # store and publish themselves — are covered by the same seam.
        self.history = ProjectHistory()
        # One-keystroke undo/redo: an in-memory two-stack cursor over the
        # durable git history (tools_undo / routes_undo / Cmd+Z in the UI).
        self.undo_cursor = UndoCursor(self.history, self.store, bus)
        bus.on_publish = self._snapshot_on_event

    def _snapshot_on_event(self, event: dict) -> None:
        """EventBus pre-fan-out hook: commit a history snapshot for each
        project_changed publish. Every mutation path publishes AFTER its
        write is persisted, so the snapshot always sees the new state.
        Suppressed while project_restore runs — it commits its own linear
        'restore' entry (see tools_history)."""
        if event.get("type") != "project_changed" or self.history.in_restore:
            return
        proj = event.get("project")
        if not proj:
            return
        try:
            path = self.store.path_of(proj)
        except NotFoundError:
            return
        message = "project_changed"
        if event.get("part"):
            message += f" {event['part']}"
        if event.get("reason"):
            message += f" ({event['reason']})"
        commit = self.history.snapshot(path, message)
        if commit:
            self.undo_cursor.on_snapshot(proj, commit, message)

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

    def _status_key(self, proj: str, part_id: str) -> tuple[str, str]:
        """Key of a part's in-memory build state.

        Two branches of one project hold different scripts and params for the
        same part id, so their ok/error badges must not share a slot.
        ``store.lock_key`` is exactly that identity — and it is the project
        name while branching is inactive, so the key is unchanged there.
        """
        return (self.store.lock_key(proj), part_id)

    def _forget_status(self, lock_key: str) -> None:
        """Drop every build state recorded against one working tree.

        A merge's validation pass builds parts with the resolver pinned to its
        staged worktree, so the entries are keyed by that temporary directory.
        It is deleted when the merge finalizes or aborts; without this the
        entries would outlive it for the life of the process."""
        for key in [k for k in self._status if k[0] == lock_key]:
            self._status.pop(key, None)

    def get_project(self, proj: str) -> dict:
        manifest = self.store.manifest(proj)
        parts = []
        for entry in manifest["parts"]:
            status = self._status.get(self._status_key(proj, entry["id"]))
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
        self.bus.publish(
            {"type": "project_changed", "project": proj, "part": part_id}
        )
        return self.get_part(proj, part_id)

    def get_part(self, proj: str, part_id: str) -> dict:
        record = self.store.get_part(proj, part_id)
        is_reference = record.kind == "reference"
        script = None if is_reference else self.store.read_script(proj, part_id)
        self._ensure_built(proj, part_id)
        status = self._status.get(
            self._status_key(proj, part_id), {"state": "unbuilt"}
        )
        detail = {
            "id": record.id,
            "label": record.label,
            "material": record.material,
            "params": record.params,
            "kind": record.kind,
            "source": record.source,
            "solid_materials": record.solid_materials,
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
        self.bus.publish(
            {"type": "project_changed", "project": proj, "part": part_id}
        )
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
        # Param overrides are authored state persisted in the manifest, so
        # they publish (and history-snapshot) like every other mutation.
        self.bus.publish(
            {"type": "project_changed", "project": proj, "part": part_id}
        )
        return self._rebuild(proj, part_id)

    def delete_part(self, proj: str, part_id: str) -> None:
        with self._lock:
            self.store.remove_part(proj, part_id)
            self._status.pop(self._status_key(proj, part_id), None)
        self.bus.publish(
            {"type": "project_changed", "project": proj, "part": part_id}
        )

    def get_metrics(self, proj: str, part_id: str) -> dict:
        self.store.get_part(proj, part_id)
        result = self._ensure_built(proj, part_id)
        if not result["ok"]:
            raise KernelErrorFromResult(result)
        return result["metrics"]

    def mesh_info(self, proj: str, part_id: str, lod: str | None = None) -> dict:
        """Built mesh path + cache key, from the build result (no racy re-read
        of the shared status dict — a concurrent delete cannot KeyError us).

        When *lod* names an existing sidecar tier (``<key>.<lod>.acm``) that
        tier's path is returned with ``lod`` set; otherwise the full-resolution
        mesh with ``lod: None`` (small parts have no tier — that is the normal
        fallback, not an error)."""
        self.store.get_part(proj, part_id)
        result = self._ensure_built(proj, part_id)
        if not result["ok"]:
            raise KernelErrorFromResult(result)
        key = result["cache_key"]
        cache = self.store.cache_dir(proj)
        if lod and _LOD_SUFFIX_RE.match(lod):
            tier = cache / f"{key}.{lod}.acm"
            if tier.is_file():
                return {"path": tier, "key": key, "lod": lod}
        return {"path": cache / f"{key}.acm", "key": key, "lod": None}

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
            timeout_s=600.0,  # large assemblies: pairs grow quadratically
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
        """Cache-key content for a part: script text for scripts, or the
        imported file's content hash for reference parts.

        Content, not path+mtime: imports/ is per working tree, so a branch
        checkout restamps the mtime of a byte-identical file and would
        otherwise mint a fresh cache key on every branch (breaking FR13's
        determinism guarantee, and forcing a rebuild after every restore).
        """
        if record.kind == "reference":
            src = self.store.imports_dir(proj) / Path(record.source).name \
                if record.source else None
            if src and src.is_file():
                digest = hashlib.sha256(src.read_bytes()).hexdigest()
                return f"ref:{record.source}:sha256:{digest}"
            return f"ref:{record.source}:missing"
        return self.store.read_script(proj, record.id)

    def _solid_densities(self, proj: str, record) -> dict[str, float]:
        """Resolved density per ``solid_materials`` key (label or index string).
        Empty for reference parts and parts without per-solid materials."""
        if record.kind != "script" or not record.solid_materials:
            return {}
        return {
            key: self.material_density(proj, material_id)
            for key, material_id in record.solid_materials.items()
        }

    def _cache_key_for(self, proj: str, record) -> str:
        return self._cache_key(
            self._content_signature(proj, record),
            record.params,
            self.material_density(proj, record.material),
            self._solid_densities(proj, record),
        )

    def _cache_key(self, content: str, params: dict, density: float,
                   densities: dict | None = None) -> str:
        payload_dict = {
            "content": content,
            "params": {k: params[k] for k in sorted(params)},
            "density": density,
            "tolerance": MESH_TOLERANCE,
            "format": "acm1",
        }
        # Only added when per-solid densities exist, so cache keys of parts
        # without solid_materials stay byte-identical to the pre-feature keys.
        if densities:
            payload_dict["densities"] = {k: densities[k] for k in sorted(densities)}
        payload = json.dumps(payload_dict, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def _ensure_built(self, proj: str, part_id: str) -> dict:
        status = self._status.get(self._status_key(proj, part_id))
        if status is not None and status["state"] in ("ok", "error"):
            record = self.store.get_part(proj, part_id)
            current = self._cache_key_for(proj, record)
            if status["state"] == "ok":
                mesh = self.store.cache_dir(proj) / f"{status['cache_key']}.acm"
                if mesh.is_file() and current == status["cache_key"]:
                    return {"ok": True, "metrics": status["metrics"],
                            "warnings": status.get("warnings", []),
                            "lods": status.get("lods", []),
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
                stored = json.loads(metrics_path.read_text(encoding="utf-8"))
                cached_metrics = stored["metrics"]
            except (json.JSONDecodeError, KeyError, OSError):
                # A crash mid-write left a corrupt sidecar: discard it and
                # fall through to a fresh kernel build (spec §8).
                try:
                    metrics_path.unlink()
                except OSError:
                    pass
            else:
                self._status[self._status_key(proj, part_id)] = {
                    "state": "ok",
                    "cache_key": key,
                    "metrics": cached_metrics,
                    "warnings": stored.get("warnings", []),
                    "lods": stored.get("lods", []),
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
                        "lods": stored.get("lods", []),
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
            solid_densities = self._solid_densities(proj, record)
            if solid_densities:
                build_params["densities"] = solid_densities
        # Always request the preview tier; the worker writes it only when the
        # full mesh exceeds the threshold (one round-trip, no service state).
        build_params["lod_tolerances"] = {"lod1": MESH_LOD_TOLERANCE}
        build_params["lod_min_triangles"] = LOD_TRIANGLE_THRESHOLD
        try:
            result = self.kernel.request(
                method, build_params, timeout_s=300.0, affinity=part_id
            )
        except KernelError as exc:
            payload = exc.to_payload()
            status_key = self._status_key(proj, part_id)
            self._status[status_key] = {
                "state": "error",
                "cache_key": key,
                "metrics": self._status.get(status_key, {}).get("metrics"),
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
        lods = result.get("lods", [])
        ProjectStore._atomic_write(
            metrics_path,
            json.dumps(
                {"metrics": metrics, "warnings": warnings, "lods": lods}
            ).encode(),
        )
        self._status[self._status_key(proj, part_id)] = {
            "state": "ok",
            "cache_key": key,
            "metrics": metrics,
            "warnings": warnings,
            "lods": lods,
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
                "lods": lods, "cache_key": key}

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
