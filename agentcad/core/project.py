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
import threading
import time
from pathlib import Path
from typing import Callable

from . import locks
from .materials import DEFAULT_MATERIAL, LIBRARY_VERSION, get_material
from .navigation import normalize_folder, normalize_tags
from .model import (
    ConflictError,
    DiskBudgetError,
    InstanceSpec,
    NotFoundError,
    PartRecord,
    ValidationError,
    validate_id,
    validate_vec3,
)

SCHEMA_VERSION = 2  # v2 adds part kind/source (reference imports) and instance mates

#: How long a `disk_usage` measurement is reused. A build path asks twice in a
#: second (the budget check, then the janitor) and a project tree can hold
#: thousands of cache files, so the walk is memoized — but only for as long as
#: nothing anyone does could plausibly be waiting on a fresh number.
_DISK_MEMO_S = 5.0

#: What the janitor may delete: a mesh (``<key>.acm``), its LOD sidecar
#: (``<key>.lod1.acm``) and its face-index sidecar (``<key>.faces.u32``). All
#: three are content-addressed and rebuildable. The ``<key>.metrics.json``
#: sidecar is left alone — it is bytes, not megabytes, and a build treats a
#: sidecar without its mesh as a miss.
#:
#: ``<key>.thumb.png`` (PRD-027 FR4) joins them for the same reason: it is a
#: 192² render of ``<key>.acm``, so a swept mesh should not leave its picture
#: behind — and it buckets on the same ``key`` as the mesh, so the two go
#: together. The **assembly** composite (``asm-<hash>.thumb.png``) buckets on
#: its own name and is in no keep-set, so it is swept when it is old and the
#: cache is over the watermark; the next read re-renders it from meshes that
#: are still there.
_TRIMMABLE = (".acm", ".faces.u32", ".thumb.png")

#: The cache is trimmed back to this fraction of the budget, so a janitor pass
#: buys room for several builds rather than running on every one.
_TRIM_WATERMARK = 0.75

#: A cache file younger than this is never trimmed, whatever the keep-set says.
#: The keep-set is the SERVICE's memory (`_status`/`_config_status`), and that
#: is empty after a restart — so a cold assembly read over the watermark could
#: sweep away a sibling part's mesh that another request had just built and the
#: browser had not fetched yet. Ten minutes is far longer than any build →
#: fetch round trip and far shorter than anything a janitor needs to reclaim
#: (review M4).
_TRIM_MIN_AGE_S = 600.0


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

#: What one entry of ``update_parts_meta``'s ``edits`` map may name. Closed on
#: purpose: a bulk op that accepted an unrecognized key would report success
#: for N parts it did not touch.
_META_EDIT_KEYS = frozenset({"folder", "tags", "material"})

#: "this keyword was not passed", for a field whose ``None`` is a real value.
#: ``active_config=None`` MEANS "return to base" (pop the key), so it cannot
#: double as "leave it alone" the way ``label=None`` does.
_UNSET = object()


def _empty_manifest(name: str) -> dict:
    """The one shape every new project starts from.

    ``materials_library`` (PRD-028 FR9) records which shipped material library
    the project was created against. It is written HERE rather than in
    ``service.create_project`` because this is the single place a new manifest
    comes from — a project created by the CLI, by a template copy or by a
    package cell gets the pin the same way. It is additive and merges whole
    (``manifest_merge`` treats an unknown top-level key atomically), and
    nothing resolves against it: what preserves byte-stable rebuilds is the
    editorial immutability rule (a builtin id's density never changes), and
    the pin is what ``list_materials`` reports back.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "units": "mm",
        "materials_library": LIBRARY_VERSION,
        "parts": [],
        "assembly": {"instances": []},
    }


def _dir_bytes(path: Path) -> int:
    """Bytes under *path*, symlinks not followed, unreadable subtrees skipped.

    ``os.scandir`` rather than ``Path.rglob`` because the entry already carries
    the stat on every platform that matters, and a `.cache/` with thousands of
    meshes is walked on the build path.
    """
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue  # absent, or not ours to read: it holds nothing we know of
    return total


class ProjectStore:
    def __init__(self, root: Path, disk_budget_mb: int | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._external: dict[str, Path] = {}
        # Per-project disk budget in MB, covering .cache/, exports/ and
        # imports/ (PRD-006 Decision 10). ``None`` — the default, and what
        # every library caller and test gets — means no check at all, so the
        # store behaves exactly as it did before quotas existed. The server
        # sets it from `quotas.disk_mb` in `cli._build_service`.
        self.disk_budget_mb = disk_budget_mb
        self._disk_memo: dict[tuple[str, str], tuple[float, dict]] = {}
        self._disk_lock = threading.Lock()
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
                return self._part_record(entry)
        raise NotFoundError(f"part {part_id!r} not found in project {proj!r}")

    @staticmethod
    def _part_record(entry: dict) -> PartRecord:
        """One manifest part entry -> a PartRecord.

        The single construction site, so a field added to the record is read
        back by every caller — `get_part` and the bulk `update_parts_meta`,
        which builds N records from ONE manifest read rather than re-reading
        (and re-parsing) a 1 000-part manifest once per part.
        """
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
            # PRD-027: absent keys read as root / no tags, so a
            # pre-PRD-027 manifest loads with no migration.
            folder=entry.get("folder"),
            tags=list(entry.get("tags") or []),
        )

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

    # ------------------------------------------- navigation meta (PRD-027)

    def update_part_meta(
        self,
        proj: str,
        part_id: str,
        *,
        folder: str | None | object = _UNSET,
        tags: list[str] | None = None,
    ) -> PartRecord:
        """Set one part's navigation metadata (FR1).

        ``folder`` omitted leaves it alone, ``folder=None`` (or ``""``) files
        the part at root; ``tags=None`` leaves them alone, ``tags=[]`` clears
        them. Clearing POPS the key, so a part that has been organized and
        then un-organized is byte-identical to one that never was.

        Scoped like `write_script` and `update_part_entry`: the guard that
        `save_manifest` calls sees the part this write is about, so a PRD-008
        claim on it refuses.
        """
        # Validate before taking the scope: a malformed tag is the caller's
        # mistake, and it should not cost a claim check or a guard call.
        if folder is not _UNSET:
            folder = normalize_folder(folder)
        if tags is not None:
            tags = normalize_tags(tags)
        with locks.write_scope(part_id):
            manifest = self.manifest(proj)
            for entry in manifest["parts"]:
                if entry["id"] == part_id:
                    self._apply_meta(entry, folder=folder, tags=tags)
                    self.save_manifest(proj, manifest)
                    return self._part_record(entry)
        raise NotFoundError(f"part {part_id!r} not found in project {proj!r}")

    def update_parts_meta(
        self, proj: str, edits: dict[str, dict]
    ) -> list[PartRecord]:
        """Set navigation metadata (and material) on MANY parts in ONE write.

        ``edits`` maps part id -> ``{"folder": str|None, "tags": list|None,
        "material": str|None}``; for ``folder`` the KEY BEING PRESENT is what
        means "set it" (its ``None`` is a real value, root), while ``tags``
        and ``material`` treat ``None`` as "leave alone".

        The single `save_manifest` is the point of the method, not an
        optimization: one manifest write is one `project_changed` publish is
        one history snapshot is **one undo step** (design ruling 4). A bulk
        op built out of N `update_part_meta` calls would cost the user N
        presses of Cmd+Z to take back one gesture.

        Nothing is mutated until everything validates, and the write guard is
        invoked once per part id **inside that part's `write_scope`** before
        the first mutation — so a part another human holds refuses the whole
        bulk with the usual `ConflictError` (ruling 5) rather than leaving
        half of it applied.

        **PRECONDITION — the caller MUST serialize this call.** This is an
        UNSERIALIZED read-modify-write with a deliberately wide window: the
        manifest is read at the top, then N write-guard calls run (each one a
        branch-checkout check and a turn-lock check, i.e. real I/O), and only
        then is the mutated manifest saved. A concurrent `set_params`,
        `update_part_entry` or `set_instances` on the same project inside that
        window is **silently lost** — the last save wins and the loser was told
        it succeeded. The store cannot close this itself, so the caller holds::

            with manifest_scope(service.store, proj), service._lock:
                service.store.update_parts_meta(proj, edits)

        Both locks, outer-to-inner in exactly that order — `manifest_scope`
        (from `packages/manager.py`) against the configuration tools and the
        package manager, `service._lock` against `service.update_part` /
        `set_params` / `set_assembly`. It is the order
        `tools_configs.set_instance_config` already documents and takes; both
        are reentrant.

        **Why this method does not take `manifest_scope` itself:** because the
        caller holds `service._lock` around it (that is the service layer's
        serialization primitive for a manifest RMW — `service.update_part` is
        the precedent), acquiring `manifest_scope` *inside* here would make the
        acquisition order `service._lock` → `manifest_scope`, the exact inverse
        of the house order above. Two threads — one entering through
        `set_instance_config`, one through the bulk op — would then hold one
        lock each and wait for the other. A lock taken at the wrong level is
        worse than no lock, so it is the caller's, named here instead. (The
        layering says the same thing: `core/project.py` is the bottom of the
        import graph, and pulling `packages.manager` — and with it `cache`,
        `indexes`, `lockfile`, `_git` — into the store to reach one RLock
        inverts the dependency as well as the locks.)

        **OBLIGATION — `material` is not just a field.** It feeds
        `_cache_key` (via `service.material_density`), so a caller that changes
        one must, per affected part, publish `project_changed` and then call
        `service.rebuild_after_write(proj, part_id)` — the `service.update_part`
        precedent. Writing it here without that leaves the part's cached mesh,
        its `_status` entry and its mass metrics computed against the OLD
        density, and nothing will correct them until something else happens to
        invalidate the key. `folder`/`tags` carry no such obligation: they
        reach no cache key and no geometry request.
        """
        if not isinstance(edits, dict):
            raise ValidationError(
                "edits must be an object of part_id -> "
                "{folder?, tags?, material?}"
            )
        manifest = self.manifest(proj)
        entries = {p["id"]: p for p in manifest["parts"]}
        missing = [pid for pid in edits if pid not in entries]
        if missing:
            raise NotFoundError(
                f"unknown part(s) in project {proj!r}: "
                f"{', '.join(sorted(missing))}",
                {"missing": sorted(missing)},
            )
        # 1. validate every edit, writing nothing
        planned: dict[str, dict] = {}
        for part_id, edit in edits.items():
            if not isinstance(edit, dict):
                raise ValidationError(
                    f"edit for part {part_id!r} must be an object")
            # A typo'd key must not be a silent no-op: `{"tag": [...]}` or
            # `{"folders": "x"}` would otherwise report success having changed
            # nothing, which for a bulk op means N parts quietly untouched.
            unknown = sorted(set(edit) - _META_EDIT_KEYS)
            if unknown:
                raise ValidationError(
                    f"edit for part {part_id!r} has unknown key(s) "
                    f"{', '.join(repr(k) for k in unknown)}; allowed: "
                    f"{', '.join(sorted(_META_EDIT_KEYS))}",
                    {"part": part_id, "unknown": unknown,
                     "allowed": sorted(_META_EDIT_KEYS)},
                )
            plan: dict = {}
            if "folder" in edit:
                plan["folder"] = normalize_folder(edit["folder"])
            if edit.get("tags") is not None:
                plan["tags"] = normalize_tags(edit["tags"])
            if edit.get("material") is not None:
                self._validate_material(manifest, edit["material"])
                plan["material"] = edit["material"]
            planned[part_id] = plan
        # 2. the guard, per part, in that part's scope — still writing nothing
        for part_id in edits:
            with locks.write_scope(part_id):
                if self.write_guard is not None:
                    self.write_guard(proj)
        # 3. mutate in place, 4. ONE save
        for part_id, plan in planned.items():
            entry = entries[part_id]
            self._apply_meta(
                entry,
                folder=plan["folder"] if "folder" in plan else _UNSET,
                tags=plan.get("tags"),
            )
            if "material" in plan:
                entry["material"] = plan["material"]
        self.save_manifest(proj, manifest)
        return [self._part_record(entries[part_id]) for part_id in edits]

    @staticmethod
    def _apply_meta(entry: dict, *, folder, tags) -> None:
        """Write already-validated meta onto a manifest part entry.

        Cleared values pop their key (the `configs` precedent) — the manifest
        never carries ``"folder": null`` or ``"tags": []``.
        """
        if folder is not _UNSET:
            if folder:
                entry["folder"] = folder
            else:
                entry.pop("folder", None)
        if tags is not None:
            if tags:
                entry["tags"] = list(tags)
            else:
                entry.pop("tags", None)

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
                folder=i.get("folder"),
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
            # PRD-027: validated HERE for the same reason `config` is — four
            # writers reach set_instances (service.set_assembly,
            # tools_structure's wrapper, tools_mates, the instance PATCH) and
            # only the store sees all of them.
            inst.folder = normalize_folder(inst.folder)
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
        if write:
            # After the guard, before the directory: the turn lock's answer is
            # about *who* may write and the budget's about *whether* there is
            # room, and a caller who does not hold the turn should be told that
            # first. Reads are never budget-checked — a rebuild must not fail
            # because the project is full of exports.
            self.assert_disk_budget(proj)
        path = self._resolve(proj) / "imports"
        path.mkdir(exist_ok=True)
        return path

    # --------------------------------------------------------- disk budget

    def disk_usage(self, proj: str) -> dict:
        """Bytes this project occupies, split by directory.

        ``{"used_bytes", "cache_bytes", "exports_bytes", "imports_bytes"}``.
        A **measurement**: it creates nothing (an `exports/` conjured by a read
        would show up in every project tree and every git diff) and it never
        raises for a directory it cannot walk — an unreadable subtree measures
        as the part of it we could see, which is the honest floor.

        Memoized for five seconds per (project, working tree), because a build
        asks twice — once for the budget, once for the janitor — and the walk
        is O(cache files).
        """
        dirs = self._budget_dirs(proj)
        memo_key = (proj, str(dirs["exports"].parent))
        now = time.monotonic()
        with self._disk_lock:
            cached = self._disk_memo.get(memo_key)
            if cached is not None and now - cached[0] < _DISK_MEMO_S:
                return dict(cached[1])
        used = {f"{name}_bytes": _dir_bytes(path) for name, path in dirs.items()}
        used["used_bytes"] = sum(used.values())
        with self._disk_lock:
            self._disk_memo[memo_key] = (now, dict(used))
        return used

    def invalidate_disk_usage(self, proj: str) -> None:
        """Forget the memo for *proj* — after anything that frees or fills it."""
        with self._disk_lock:
            for key in [k for k in self._disk_memo if k[0] == proj]:
                self._disk_memo.pop(key, None)

    def assert_disk_budget(self, proj: str) -> None:
        """Raise :class:`DiskBudgetError` when *proj* is at its budget.

        Called **before** the kernel is asked to write (a build, an export, an
        assembly export, an import), so a full project fails cleanly instead of
        leaving a truncated mesh behind. ``disk_budget_mb`` of ``None`` or 0 is
        no budget and no walk.
        """
        budget = self.disk_budget_mb
        if not budget:
            return
        used_mb = self.disk_usage(proj)["used_bytes"] / (1024 * 1024)
        if used_mb < budget:
            return
        raise DiskBudgetError(
            f"project {proj!r} has used {used_mb:.1f} MB of its {budget} MB "
            f"disk budget (.cache, exports and imports). Delete exports or "
            f"imports you no longer need, or raise the budget with "
            f"AGENTCAD_QUOTA_DISK_MB.",
            {"project": proj, "used_mb": round(used_mb, 1),
             "budget_mb": budget},
        )

    def trim_cache(self, proj: str, keep_keys: set[str], *,
                   min_age_s: float = _TRIM_MIN_AGE_S) -> int:
        """Delete the oldest unreferenced meshes until the cache is under the
        watermark. Returns the bytes freed.

        A janitor, not a quota: it runs after a *successful* build, and
        everything it removes is content-addressed derived data that the next
        read rebuilds. A key in *keep_keys* is one the service still points at
        (a part's badge, a configuration's memo, the build that just finished)
        and is never touched — which is also why the cache can end up over the
        watermark and stay there: the answer to "every mesh is live" is a
        bigger budget, not a deletion the user would notice.

        *min_age_s* is the **second** protection, and it exists because the
        first one is not enough (review M4): *keep_keys* is built from the
        service's in-memory ``_status``/``_config_status``, which are empty
        after a restart. A cold assembly read that goes over the watermark
        could therefore delete a sibling part's mesh that another request built
        seconds earlier and whose browser fetch had not arrived yet — nothing
        is lost for good (the next read rebuilds it), but the user pays a
        rebuild for a file that was on disk. A file younger than this is
        somebody's, whether or not this process remembers whose.

        It reads :meth:`disk_usage`, which is **memoized for 5 s**, so a build
        that lands immediately after another one measures the older number and
        can decline to trim work that has since arrived. That is a bounded
        under-trigger by design — the memo is what keeps a per-build budget
        check from walking three directory trees on every request, and the next
        build past the memo window sees the real size. Nothing over-deletes:
        the stale read is always *smaller* than the truth.
        """
        budget = self.disk_budget_mb
        if not budget:
            return 0
        target = int(budget * 1024 * 1024 * _TRIM_WATERMARK)
        cache_bytes = self.disk_usage(proj)["cache_bytes"]
        if cache_bytes <= target:
            return 0
        cache = self.canonical_path_of(proj) / ".cache"
        # One clock reading for the whole sweep: a per-entry `time.time()`
        # would make the cut-off drift across a large directory.
        floor = time.time() - max(0.0, min_age_s)
        candidates = []
        try:
            with os.scandir(cache) as entries:
                for entry in entries:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if not entry.name.endswith(_TRIMMABLE):
                        continue
                    if entry.name.split(".", 1)[0] in keep_keys:
                        continue
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    if stat.st_mtime > floor:
                        continue   # too young to be nobody's
                    candidates.append((stat.st_mtime, stat.st_size, entry.path))
        except OSError:
            return 0
        freed = 0
        for _mtime, size, path in sorted(candidates):
            if cache_bytes - freed <= target:
                break
            try:
                os.unlink(path)
            except OSError:
                continue          # someone else got there first; keep going
            freed += size
        if freed:
            self.invalidate_disk_usage(proj)
        return freed

    def _budget_dirs(self, proj: str) -> dict[str, Path]:
        """The three directories the budget covers, as paths — not created.

        ``.cache`` is canonical (content-addressed, shared by every branch);
        ``exports``/``imports`` are the caller's working tree, exactly as
        `exports_dir`/`imports_dir` resolve them.
        """
        return {"cache": self.canonical_path_of(proj) / ".cache",
                "exports": self._resolve(proj) / "exports",
                "imports": self._resolve(proj) / "imports"}

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
