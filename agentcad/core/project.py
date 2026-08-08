"""ProjectStore: filesystem-backed project persistence.

A project is a directory: ``project.json`` manifest, ``parts/<id>.py``
scripts, ``.cache/`` derived data, ``exports/`` outputs. Projects live under
a root directory; external project directories (e.g. the bundled examples)
can be registered by path. All manifest writes are atomic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .materials import DEFAULT_MATERIAL, get_material
from .model import (
    ConflictError,
    InstanceSpec,
    NotFoundError,
    PartRecord,
    ValidationError,
    validate_id,
    validate_vec3,
)

SCHEMA_VERSION = 2  # v2 adds part kind/source (reference imports) and instance mates


def _empty_manifest(name: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "units": "mm",
        "parts": [],
        "assembly": {"instances": []},
    }


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._external: dict[str, Path] = {}

    # ------------------------------------------------------------- projects

    def list_projects(self) -> list[dict]:
        found: dict[str, Path] = {}
        for child in sorted(self.root.iterdir()) if self.root.exists() else []:
            if (child / "project.json").is_file():
                found[child.name] = child
        found.update(self._external)
        out = []
        for name, path in sorted(found.items()):
            try:
                manifest = self._read_manifest(path)
            except ValidationError:
                continue
            out.append(
                {
                    "name": name,
                    "path": str(path),
                    "n_parts": len(manifest.get("parts", [])),
                }
            )
        return out

    def create(self, name: str) -> Path:
        validate_id(name, "project name")
        path = self.root / name
        if path.exists() or name in self._external:
            raise ConflictError(f"project {name!r} already exists")
        (path / "parts").mkdir(parents=True)
        self._write_manifest(path, _empty_manifest(name))
        return path

    def open(self, path: str | Path) -> str:
        """Register an external project directory; returns its name."""
        path = Path(path).resolve()
        manifest = self._read_manifest(path)
        name = manifest.get("name", "")
        validate_id(name, "project name")
        existing = self._resolve(name) if self._exists(name) else None
        if existing is not None and existing != path:
            raise ConflictError(
                f"a different project named {name!r} is already registered"
            )
        self._external[name] = path
        return name

    def path_of(self, proj: str) -> Path:
        return self._resolve(proj)

    # ---------------------------------------------------------------- parts

    def part_ids(self, proj: str) -> list[str]:
        return [p["id"] for p in self.manifest(proj)["parts"]]

    def get_part(self, proj: str, part_id: str) -> PartRecord:
        for entry in self.manifest(proj)["parts"]:
            if entry["id"] == part_id:
                return PartRecord(
                    id=entry["id"],
                    label=entry.get("label", entry["id"]),
                    material=entry.get("material", DEFAULT_MATERIAL),
                    params={k: float(v) for k, v in entry.get("params", {}).items()},
                    kind=entry.get("kind", "script"),
                    source=entry.get("source"),
                )
        raise NotFoundError(f"part {part_id!r} not found in project {proj!r}")

    def script_path(self, proj: str, part_id: str) -> Path:
        return self._resolve(proj) / "parts" / f"{part_id}.py"

    def read_script(self, proj: str, part_id: str) -> str:
        self.get_part(proj, part_id)  # existence check
        path = self.script_path(proj, part_id)
        if not path.is_file():
            raise NotFoundError(f"script file missing for part {part_id!r}")
        return path.read_text(encoding="utf-8")

    def write_script(self, proj: str, part_id: str, text: str) -> None:
        self.get_part(proj, part_id)
        self._atomic_write(self.script_path(proj, part_id), text.encode())

    def add_part(
        self,
        proj: str,
        part_id: str,
        label: str,
        material: str,
        script: str,
        *,
        kind: str = "script",
        source: str | None = None,
    ) -> PartRecord:
        validate_id(part_id, "part id")
        get_material(material)
        manifest = self.manifest(proj)
        if any(p["id"] == part_id for p in manifest["parts"]):
            raise ConflictError(f"part {part_id!r} already exists")
        record = PartRecord(
            id=part_id, label=label or part_id, material=material,
            kind=kind, source=source,
        )
        manifest["parts"].append(record.to_manifest())
        if kind == "script":
            script_file = self.script_path(proj, part_id)
            script_file.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(script_file, script.encode())
        self.save_manifest(proj, manifest)
        return record

    def remove_part(self, proj: str, part_id: str) -> None:
        manifest = self.manifest(proj)
        if not any(p["id"] == part_id for p in manifest["parts"]):
            raise NotFoundError(f"part {part_id!r} not found")
        used_by = [
            i["id"]
            for i in manifest["assembly"]["instances"]
            if i["part"] == part_id
        ]
        if used_by:
            raise ConflictError(
                f"part {part_id!r} is used by assembly instance(s): {', '.join(used_by)}",
                {"instances": used_by},
            )
        manifest["parts"] = [p for p in manifest["parts"] if p["id"] != part_id]
        self.save_manifest(proj, manifest)
        script = self.script_path(proj, part_id)
        if script.is_file():
            script.unlink()

    def update_part_entry(
        self,
        proj: str,
        part_id: str,
        *,
        label: str | None = None,
        material: str | None = None,
        params: dict[str, float] | None = None,
    ) -> PartRecord:
        manifest = self.manifest(proj)
        for entry in manifest["parts"]:
            if entry["id"] == part_id:
                if label is not None:
                    entry["label"] = label
                if material is not None:
                    get_material(material)
                    entry["material"] = material
                if params is not None:
                    for name, value in params.items():
                        if isinstance(value, bool) or not isinstance(
                            value, (int, float)
                        ):
                            raise ValidationError(
                                f"parameter {name!r} must be a number"
                            )
                    entry["params"] = {k: float(v) for k, v in params.items()}
                self.save_manifest(proj, manifest)
                return self.get_part(proj, part_id)
        raise NotFoundError(f"part {part_id!r} not found")

    # ------------------------------------------------------------- assembly

    def instances(self, proj: str) -> list[InstanceSpec]:
        return [
            InstanceSpec(
                id=i["id"],
                part=i["part"],
                position=[float(v) for v in i.get("position", [0, 0, 0])],
                rotation_deg=[float(v) for v in i.get("rotation_deg", [0, 0, 0])],
                color=i.get("color"),
                mate=i.get("mate"),
            )
            for i in self.manifest(proj)["assembly"]["instances"]
        ]

    def set_instances(self, proj: str, instances: list[InstanceSpec]) -> None:
        manifest = self.manifest(proj)
        known_parts = {p["id"] for p in manifest["parts"]}
        seen: set[str] = set()
        for inst in instances:
            validate_id(inst.id, "instance id")
            if inst.id in seen:
                raise ValidationError(f"duplicate instance id {inst.id!r}")
            seen.add(inst.id)
            if inst.part not in known_parts:
                raise ValidationError(
                    f"instance {inst.id!r} references unknown part {inst.part!r}"
                )
            inst.position = validate_vec3(inst.position, f"{inst.id}.position")
            inst.rotation_deg = validate_vec3(
                inst.rotation_deg, f"{inst.id}.rotation_deg"
            )
        manifest["assembly"]["instances"] = [i.to_manifest() for i in instances]
        self.save_manifest(proj, manifest)

    # ------------------------------------------------------------ manifests

    def manifest(self, proj: str) -> dict:
        return self._read_manifest(self._resolve(proj))

    def save_manifest(self, proj: str, manifest: dict) -> None:
        self._write_manifest(self._resolve(proj), manifest)

    def cache_dir(self, proj: str) -> Path:
        path = self._resolve(proj) / ".cache"
        path.mkdir(exist_ok=True)
        return path

    def exports_dir(self, proj: str) -> Path:
        path = self._resolve(proj) / "exports"
        path.mkdir(exist_ok=True)
        return path

    def imports_dir(self, proj: str) -> Path:
        path = self._resolve(proj) / "imports"
        path.mkdir(exist_ok=True)
        return path

    # -------------------------------------------------------------- helpers

    def _exists(self, proj: str) -> bool:
        try:
            self._resolve(proj)
            return True
        except NotFoundError:
            return False

    def _resolve(self, proj: str) -> Path:
        if proj in self._external:
            return self._external[proj]
        path = self.root / proj
        if (path / "project.json").is_file():
            return path
        raise NotFoundError(f"project {proj!r} not found")

    def _read_manifest(self, path: Path) -> dict:
        manifest_file = path / "project.json"
        if not manifest_file.is_file():
            raise ValidationError(f"{path} is not a project (no project.json)")
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"project.json is corrupt: {exc}") from exc
        if not isinstance(manifest, dict) or "name" not in manifest:
            raise ValidationError("project.json missing required fields")
        manifest.setdefault("schema_version", SCHEMA_VERSION)
        manifest.setdefault("units", "mm")
        manifest.setdefault("parts", [])
        manifest.setdefault("assembly", {"instances": []})
        manifest["assembly"].setdefault("instances", [])
        return manifest

    def _write_manifest(self, path: Path, manifest: dict) -> None:
        self._atomic_write(
            path / "project.json",
            json.dumps(manifest, indent=2).encode(),
        )

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
