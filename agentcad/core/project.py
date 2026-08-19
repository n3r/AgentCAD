"""ProjectStore: filesystem-backed project persistence.

A project is a directory: ``project.json`` manifest, ``parts/<id>.py``
scripts, ``.cache/`` derived data, ``exports/`` outputs. Projects live under
a root directory; external project directories (e.g. the bundled examples)
can be registered by path. All manifest writes are atomic.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Callable

from . import locks
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

_PATTERN_KINDS = ("linear", "polar")


def _validate_pattern(iid: str, pattern) -> None:
    """A pattern spec: {kind, count>=1, step_mm? (linear), angle_step_deg?
    (polar), axis?, center?}. Kept in one place so set_instances and the
    focused set_pattern verb refuse the same bad specs (PRD-013 Decision 1)."""
    if not isinstance(pattern, dict):
        raise ValidationError(f"instance {iid!r}: pattern must be an object")
    kind = pattern.get("kind")
    if kind not in _PATTERN_KINDS:
        raise ValidationError(
            f"instance {iid!r}: pattern.kind must be one of {_PATTERN_KINDS}"
        )
    count = pattern.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValidationError(
            f"instance {iid!r}: pattern.count must be an integer >= 1"
        )
    if kind == "linear" and pattern.get("step_mm") is None:
        raise ValidationError(
            f"instance {iid!r}: a linear pattern needs step_mm"
        )
    if kind == "polar" and pattern.get("angle_step_deg") is None:
        raise ValidationError(
            f"instance {iid!r}: a polar pattern needs angle_step_deg"
        )


def _validate_assembly_ref(iid: str, ref) -> None:
    """A sub-assembly reference: {project, version?, config?}. `version`/`config`
    are reserved (Phase 3); MVP only requires a project name/path."""
    if not isinstance(ref, dict) or not ref.get("project"):
        raise ValidationError(
            f"instance {iid!r}: assembly reference needs a project"
        )

#: "this keyword was not passed", for a field whose ``None`` is a real value.
#: ``active_config=None`` MEANS "return to base" (pop the key), so it cannot
#: double as "leave it alone" the way ``label=None`` does.
_UNSET = object()


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
        # Write guard (set post-init, e.g. by AgentCADService): called with
        # the project name before every persistent mutation — save_manifest
        # (which all pack mutations funnel through) and write_script — and may
        # raise (ConflictError) to reject the write. None means unguarded.
        # It runs BEFORE _resolve() on purpose: the versioning pack's guard is
        # also what re-materializes a branch working tree that went missing,
        # so the path this write lands on is the one the guard just vouched
        # for (see tools_versioning.install_write_guard).
        self.write_guard: Callable[[str], None] | None = None
        # Branch resolver (set post-init by the versioning pack's
        # BranchManager): maps (project, canonical path) to the working tree
        # of the *calling client's* branch, so every authored-state read and
        # write below becomes branch-aware without touching its call sites.
        # None means "no branching": the project directory is the only tree.
        self.branch_resolver: Callable[[str, Path], Path] | None = None

    # ------------------------------------------------------------- projects

    def list_projects(self) -> list[dict]:
        found: dict[str, Path] = {}
        for child in sorted(self.root.iterdir()) if self.root.exists() else []:
            if (child / "project.json").is_file():
                found[child.name] = child
        found.update(self._external)
        out = []
        for name, path in sorted(found.items()):
            # Report each project as the caller's branch sees it (part counts
            # and path), not as the canonical directory does.
            if self.branch_resolver is not None:
                path = self.branch_resolver(name, path)
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
        existing = self.canonical_path_of(name) if self._exists(name) else None
        if existing is not None and existing != path:
            raise ConflictError(
                f"a different project named {name!r} is already registered"
            )
        self._external[name] = path
        return name

    def path_of(self, proj: str) -> Path:
        """The calling client's working tree for ``proj`` (the project
        directory unless a branch resolver says otherwise)."""
        return self._resolve(proj)

    def canonical_path_of(self, proj: str) -> Path:
        """The project directory itself — the default branch's working tree
        and the home of shared, derived data (``.cache/``)."""
        return self._locate(proj)

    def lock_key(self, proj: str) -> str:
        """Key for per-branch turn locks and undo stacks: the project name on
        the default branch, the resolved working-tree path elsewhere (so two
        clients on two branches never contend).

        The default branch keeps the *project name* even with a resolver
        installed — a project that never branches is then bit-identical to a
        pre-branching one, keys included, which is what lets every existing
        lock/undo behavior (and test) stand unchanged.
        """
        if self.branch_resolver is None:
            return proj
        resolved = self._resolve(proj)
        return proj if resolved == self._locate(proj) else str(resolved)

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
                    params=dict(entry.get("params", {})),  # JSON scalars pass through
                    kind=entry.get("kind", "script"),
                    source=entry.get("source"),
                    solid_materials=entry.get("solid_materials"),
                    # Read back as stored: resolution is the record's job
                    # (PartRecord.effective_params), never this read's — a
                    # store that resolved would let the next set_params bake
                    # the active configuration into the overrides.
                    configs=entry.get("configs"),
                    active_config=entry.get("active_config"),
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
        # One of the two part-scoped write paths (the other is
        # update_part_entry). The scope tells the write guard WHICH part this
        # write is about without changing the guard's signature — see
        # locks.write_scope. Whole-manifest writes deliberately have no scope:
        # a claim is a *part* claim, and pretending it guarded add_part or an
        # assembly edit would be a lie told by a green test.
        with locks.write_scope(part_id):
            if self.write_guard is not None:
                self.write_guard(proj)
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
        manifest = self.manifest(proj)
        self._validate_material(manifest, material)
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

    @staticmethod
    def _validate_material(manifest: dict, material: str) -> None:
        """Accept any builtin material or one defined in this project's
        ``materials`` section (full property validation happens when the
        section is written via set_project_materials)."""
        from .materials import MATERIALS

        project_materials = manifest.get("materials") or {}
        if material not in MATERIALS and material not in project_materials:
            raise ValidationError(
                f"unknown material {material!r}",
                {"known_builtin": sorted(MATERIALS), "project": sorted(project_materials)},
            )

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
        params: dict[str, float | int | bool | str] | None = None,
        configs: dict | None = None,
        active_config: str | None | object = _UNSET,
    ) -> PartRecord:
        # The params/material/label path: scoped like write_script, so the
        # guard called by save_manifest below sees the part this is about.
        with locks.write_scope(part_id):
            return self._update_part_entry(proj, part_id, label=label,
                                           material=material, params=params,
                                           configs=configs,
                                           active_config=active_config)

    def _update_part_entry(
        self,
        proj: str,
        part_id: str,
        *,
        label: str | None = None,
        material: str | None = None,
        params: dict[str, float | int | bool | str] | None = None,
        configs: dict | None = None,
        active_config: str | None | object = _UNSET,
    ) -> PartRecord:
        manifest = self.manifest(proj)
        for entry in manifest["parts"]:
            if entry["id"] == part_id:
                if label is not None:
                    entry["label"] = label
                if material is not None:
                    self._validate_material(manifest, material)
                    entry["material"] = material
                if params is not None:
                    for name, value in params.items():
                        if not isinstance(value, (int, float, bool, str)):
                            raise ValidationError(
                                f"parameter {name!r} must be a number, bool, "
                                "or string"
                            )
                    entry["params"] = dict(params)
                # A full replace, never a merge (the validated whole map is
                # what a caller wrote); an emptied map POPS the key so a part
                # that no longer has a family is byte-identical to one that
                # never had one. Order is the caller's — a family is not a
                # lockfile.
                if configs is not None:
                    if configs:
                        entry["configs"] = dict(configs)
                    else:
                        entry.pop("configs", None)
                if active_config is not _UNSET:
                    if active_config:
                        entry["active_config"] = active_config
                    else:
                        entry.pop("active_config", None)
                self.save_manifest(proj, manifest)
                return self.get_part(proj, part_id)
        raise NotFoundError(f"part {part_id!r} not found")

    # ------------------------------------------------------------- assembly

    def instances(self, proj: str) -> list[InstanceSpec]:
        return [
            InstanceSpec(
                # A sub-assembly instance (PRD-013) carries no `part` — default
                # to "" so an `assembly` reference loads without a KeyError.
                id=i["id"],
                part=i.get("part", ""),
                position=[float(v) for v in i.get("position", [0, 0, 0])],
                rotation_deg=[float(v) for v in i.get("rotation_deg", [0, 0, 0])],
                color=i.get("color"),
                mate=i.get("mate"),
                # Load-bearing, not cosmetic: set_instances rewrites the whole
                # list from to_manifest(), and both tools_mates and the gizmo
                # drag read-all/write-all — a field the dataclass does not
                # carry is destroyed by the next mate edit.
                config=i.get("config"),
                pattern=i.get("pattern"),
                assembly=i.get("assembly"),
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
            # PRD-013: an instance is EITHER a part instance (optionally
            # patterned) OR a sub-assembly reference. The two never combine —
            # an `assembly` reference has no part of its own, and a part
            # instance names no source project.
            if inst.assembly is not None:
                if inst.part:
                    raise ValidationError(
                        f"instance {inst.id!r}: a sub-assembly reference "
                        "(assembly) carries no part"
                    )
                _validate_assembly_ref(inst.id, inst.assembly)
            elif inst.part not in known_parts:
                raise ValidationError(
                    f"instance {inst.id!r} references unknown part {inst.part!r}"
                )
            if inst.pattern is not None:
                _validate_pattern(inst.id, inst.pattern)
            # A configuration binding is validated HERE because three writers
            # reach the store (service.set_assembly, tools_mates and the
            # instance PATCH) and only the store sees all three.
            if inst.config is not None:
                part_entry = next(
                    p for p in manifest["parts"] if p["id"] == inst.part
                )
                if part_entry.get("kind", "script") != "script":
                    raise ValidationError(
                        f"instance {inst.id!r}: reference/imported parts have "
                        "no parameters and cannot bind a configuration"
                    )
                declared = part_entry.get("configs") or {}
                if inst.config not in declared:
                    raise ValidationError(
                        f"instance {inst.id!r}: part {inst.part!r} declares no "
                        f"configuration {inst.config!r} "
                        f"(declares {sorted(declared)})",
                        {"declared": sorted(declared)},
                    )
            inst.position = validate_vec3(inst.position, f"{inst.id}.position")
            inst.rotation_deg = validate_vec3(
                inst.rotation_deg, f"{inst.id}.rotation_deg"
            )
        # Reject dangling mates on write, so removing an anchor instance can't
        # leave the whole assembly unreadable (mate resolution would then fail).
        for inst in instances:
            if inst.mate:
                target = inst.mate.get("to_instance")
                if target not in seen:
                    raise ValidationError(
                        f"instance {inst.id!r}: mate.to_instance {target!r} "
                        "is not an instance in this assembly"
                    )
                if target == inst.id:
                    raise ValidationError(f"instance {inst.id!r}: mate to itself")
        manifest["assembly"]["instances"] = [i.to_manifest() for i in instances]
        self.save_manifest(proj, manifest)

    def assembly_interface(self, proj: str) -> dict:
        """The project's exported connector interface (PRD-013 FR3): a map
        ``name -> {instance, connector}``. Only exported connectors are matable
        from a parent assembly; internal connectors are unreachable."""
        return dict((self.manifest(proj)["assembly"].get("interface")) or {})

    def set_assembly_interface(self, proj: str, exports: dict) -> None:
        """Replace the exported interface. Each export must name an existing
        instance (referential check at write time). An emptied map pops the key
        so a project with no interface is byte-identical to one that never had
        one."""
        manifest = self.manifest(proj)
        ids = {i["id"] for i in manifest["assembly"]["instances"]}
        if not isinstance(exports, dict):
            raise ValidationError("interface exports must be an object")
        for name, spec in exports.items():
            validate_id(name, "interface name")
            if not isinstance(spec, dict) or not spec.get("connector"):
                raise ValidationError(
                    f"interface {name!r}: needs {{instance, connector}}"
                )
            if spec.get("instance") not in ids:
                raise ValidationError(
                    f"interface {name!r}: instance {spec.get('instance')!r} "
                    "is not an instance in this assembly"
                )
        if exports:
            manifest["assembly"]["interface"] = {
                n: {"instance": s["instance"], "connector": s["connector"]}
                for n, s in exports.items()
            }
        else:
            manifest["assembly"].pop("interface", None)
        self.save_manifest(proj, manifest)

    # ------------------------------------------------------------ manifests

    def manifest(self, proj: str) -> dict:
        return self._read_manifest(self._resolve(proj))

    def save_manifest(self, proj: str, manifest: dict) -> None:
        if self.write_guard is not None:
            self.write_guard(proj)
        self._write_manifest(self._resolve(proj), manifest)

    def cache_dir(self, proj: str) -> Path:
        # Canonical, never per-branch: cache keys are content-addressed, so
        # identical script+params on any branch must hit the same entry (FR13).
        path = self.canonical_path_of(proj) / ".cache"
        path.mkdir(exist_ok=True)
        return path

    def exports_dir(self, proj: str) -> Path:
        path = self._resolve(proj) / "exports"
        path.mkdir(exist_ok=True)
        return path

    def imports_dir(self, proj: str, *, write: bool = False) -> Path:
        """Imported CAD payloads for the caller's branch.

        ``write=True`` is the ingest path. An import is authored state — it is
        tracked by git and a reference part points at it — so it goes through
        the same guard as ``write_script``: the caller's branch tree is made
        good first, and a payload can never follow the read resolver's
        fallback onto the default branch. Reads stay unguarded (a rebuild must
        not fail because someone else holds the turn).
        """
        if write and self.write_guard is not None:
            self.write_guard(proj)
        path = self._resolve(proj) / "imports"
        path.mkdir(exist_ok=True)
        return path

    # -------------------------------------------------------------- helpers

    def _exists(self, proj: str) -> bool:
        try:
            self._locate(proj)
            return True
        except NotFoundError:
            return False

    def _resolve(self, proj: str) -> Path:
        """Working tree of the calling client's branch — what every authored
        state read/write below goes through."""
        canonical = self._locate(proj)
        resolver = self.branch_resolver
        return canonical if resolver is None else resolver(proj, canonical)

    def _locate(self, proj: str) -> Path:
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
        """Write ``data`` to ``path``, atomically, **per writer**.

        The staging name carries a random suffix, and that is the whole of the
        fix recorded in changelog 0181: it used to be the fixed
        ``<name>.tmp``, so two concurrent writers opened the **same** staging
        file, interleaved their bytes into it, and then each `os.replace`d the
        mixture into place. `os.replace` was atomic the whole time — the file
        being replaced *from* was the shared one. The failure was not a lost
        update but a **corrupt `project.json`** ("Extra data" out of
        `json.loads`), which is the difference between losing one write and
        losing the project.

        The `.staging-<rand>` idiom is `cache.install`'s and
        `LocalIndex.publish`'s, for the same reason and now spelled the same
        way. Callers that need mutual exclusion still need it — this makes a
        concurrent write lose, never corrupt.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
