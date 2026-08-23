"""Tool pack: the interop exchange surface (PRD-017 §2, FR6–FR7/FR12).

**Load order — the reason this file is called `tools_xchange`.**
``tools._load_tool_packs`` walks ``pkgutil.iter_modules`` **alphabetically**,
and ``tools_structure`` (PRD-013) does not *delegate* to
``service.export_assembly`` — it **replaces** it. A pack that wrapped the
service before ``tools_structure`` ran would be thrown away, silently, taking
assembly expansion or interop with it. ``xchange`` sorts after ``structure``,
``undo`` and ``versioning``, so what this pack captures is the **final**
method, expansion included. (The name is also why it is not
``tools_interop.py``: ``i`` sorts before ``s``.)

**How the surface grows.** ``ToolRegistry.register`` raises on a duplicate
name and has no overwrite seam, so a pack cannot re-register ``export_part``
with a wider schema. The house idiom is the ``tools_structure``/``tools_holes``
one: capture and wrap the service method, and mutate the already-registered
``Tool`` in place. Both halves are needed and neither is optional — a schema
advertising ``pmi`` over a handler that cannot take the keyword is a
``TypeError`` at call time, so the handler is rebound with the wrapper's
signature.

What this slice routes:

* ``gltf``/``glb`` — **server-side**, from the ACM1 mesh cache
  (``core/gltf.py``), never a kernel round trip.
* ``step`` on a part that has PMI — the ``export_step_pmi`` kernel handler
  (AP242, slice 1); ``pmi: false`` opts back out to today's path.
* everything else — delegated to the captured original, byte-for-byte.

Every result carries ``fidelity`` (spec §8): what survived the translation and
what did not, on the delegated paths too, because "the export succeeded" and
"the export kept your tolerances" are different sentences.

Seams left open for slice 5 (3MF v2 + structured STEP assembly): ``metadata``
is accepted here and used there; ``3mf`` for a part still delegates to the
plain writer; assembly ``3mf`` and ``export_assembly {structured: true}`` are
deliberately **not** advertised in the schema until they run — an enum entry
is a promise.

OCP-free: this is server-process code (probe in ``tests/test_interop_gltf.py``).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from . import gltf, usage
from .interop_colors import category_for, color_for
from .model import AppError, ValidationError
from .service import EXPORT_TOLERANCE

_WRAPPED = "_agentcad_xchange_wrapped"

#: What ``export_part`` accepts (slice 7 appends ``usd`` when it is available).
PART_FORMATS = ("step", "stl", "3mf", "gltf", "glb")
#: What ``export_assembly`` accepts. ``3mf`` joins in slice 5.
ASSEMBLY_FORMATS = ("step", "stl", "gltf", "glb")
#: The formats this pack writes itself, from the mesh cache.
MESH_FORMATS = ("gltf", "glb")


# --------------------------------------------------------------- fidelity


def _fidelity(fmt: str, *, pmi: str | None = None, pmi_skipped=None,
              pmi_notes=None, colors: str | None = None,
              metadata: str = "none", skipped=None) -> dict:
    """Spec §8: the axes this FORMAT can carry, and ``parametric`` always.

    An axis a format cannot express is absent rather than ``"none"`` — "STL
    has no PMI" is not news, "your STEP dropped a datum" is.
    """
    out: dict = {"geometry": "brep" if fmt == "step" else "mesh"}
    if fmt == "step":
        out["pmi"] = pmi or "none"
        if pmi_skipped is not None:
            out["pmi_skipped"] = list(pmi_skipped)
        if pmi_notes is not None:
            out["pmi_notes"] = list(pmi_notes)
    if fmt in ("3mf", "gltf", "glb"):
        out["colors"] = colors or "none"
    if fmt == "3mf":
        out["metadata"] = metadata
    if skipped:
        out["instances_skipped"] = list(skipped)
    # The honesty line: no neutral format carries parametric intent.
    out["parametric"] = "none"
    return out


def _with_fidelity(result: dict, fidelity: dict) -> dict:
    if isinstance(result, dict):
        result["fidelity"] = fidelity
    return result


# ------------------------------------------------------------ mesh export


def _atomic_write(path: Path, data: bytes) -> None:
    """tmp + ``os.replace``, the worker's own export rule: a killed export
    never leaves a torn file where a whole one used to be."""
    tmp = path.with_name(f".{path.stem}.tmp{path.suffix}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _render(items, fmt: str) -> bytes:
    return gltf.build_glb(items) if fmt == "glb" else gltf.build_gltf(items)[0]


def _mesh_path(service, project: str, key: str) -> Path:
    return service.store.cache_dir(project) / f"{key}.acm"


def _part_item(service, proj: str, part_id: str, config: str | None) -> dict:
    # `mesh_info` is the public seam that BUILDS: it goes through
    # `_ensure_built` / `_ensure_config_built`, exactly as `get_part` and the
    # mesh route do, so an unbuilt (or stale) part is built before conversion
    # and a failed build raises the ordinary KernelError.
    info = service.mesh_info(proj, part_id, config=config)
    record = service._record_for(proj, part_id, config)
    return {
        "instance_id": part_id,
        "mesh_key": info["key"],
        "acm_bytes": Path(info["path"]).read_bytes(),
        "position": [0.0, 0.0, 0.0],
        "rotation_deg": [0.0, 0.0, 0.0],
        "color_hex": color_for(record),
        "material_category": category_for(record),
    }


def _origin_map(service, proj: str) -> dict:
    """``instance id -> the project its geometry was BUILT from`` (PRD-013).

    Built lazily, and only when a mesh is missing from this project's cache:
    the expanded view drops ``origin_project`` (``to_manifest`` never writes
    it), so a cross-project sub-assembly member's cache entry lives under its
    source project and re-running the expansion is the only way back to it.
    """
    return {inst.id: (getattr(inst, "origin_project", None) or proj)
            for inst in service._resolved_instances(proj)}


def _assembly_items(service, proj: str) -> tuple[list[dict], list[dict]]:
    """``(items, skipped)`` from the **public** expanded view.

    ``service.get_assembly`` is the source on purpose: it is the PRD-013
    wrapper's own result (patterns and sub-assemblies flattened, mates
    resolved, every member built and carrying its ``mesh_key``). Re-deriving
    that list here would be a second expansion implementation to keep in sync.
    """
    assembly = service.get_assembly(proj)
    items: list[dict] = []
    skipped: list[dict] = []
    origins: dict | None = None
    for entry in assembly.get("instances", []):
        instance_id = entry.get("id")
        key = entry.get("mesh_key")
        if entry.get("state") != "ok" or not key:
            skipped.append({"id": instance_id,
                            "reason": entry.get("state") or "unbuilt"})
            continue
        owner = proj
        path = _mesh_path(service, proj, key)
        if not path.is_file():
            if origins is None:
                origins = _origin_map(service, proj)
            owner = origins.get(instance_id, proj)
            path = _mesh_path(service, owner, key)
        if not path.is_file():
            skipped.append({"id": instance_id, "reason": "mesh_not_cached"})
            continue
        try:
            record = service.store.get_part(owner, entry.get("part", ""))
        except AppError:
            record = None
        items.append({
            "instance_id": instance_id,
            "mesh_key": key,
            "acm_bytes": path.read_bytes(),
            "position": entry.get("position") or [0.0, 0.0, 0.0],
            "rotation_deg": entry.get("rotation_deg") or [0.0, 0.0, 0.0],
            "color_hex": color_for(record, entry),
            "material_category": category_for(record),
        })
    return items, skipped


# --------------------------------------------------------------- STEP PMI


def _part_pmi(service, proj: str, part_id: str) -> dict | None:
    """The part's stored PMI section, or ``None`` when it has none.

    Read from the manifest entry the way ``tools_pmi`` writes it (a loose key,
    never a ``PartRecord`` field).
    """
    for entry in service.store.manifest(proj).get("parts", []):
        if entry.get("id") == part_id:
            pmi = entry.get("pmi")
            if isinstance(pmi, dict) and any(pmi.get(k) for k in
                                             ("dims", "datums", "fcf")):
                return pmi
            return None
    return None


def _export_step_pmi(service, proj: str, part_id: str, pmi: dict,
                     config: str | None) -> dict:
    record = service._record_for(proj, part_id, config)
    name = part_id if config is None else f"{part_id}_{config}"
    service.store.assert_disk_budget(proj)      # before the worker writes
    out = service.store.exports_dir(proj) / f"{name}.step"
    params: dict = {"pmi": pmi, "out_path": str(out),
                    "name": record.label or part_id}
    if record.kind == "reference":
        # The same two shape sources `service._shape_item` resolves.
        params["source_path"] = str(
            service.store.imports_dir(proj) / Path(record.source).name)
    else:
        params["script"] = service.store.read_script(proj, part_id)
        params["params"] = record.effective_params
    with usage.scoped(proj):
        result = service.kernel.request("export_step_pmi", params,
                                        timeout_s=300.0, affinity=part_id)
    if config is not None:
        result["config"] = config
    return _with_fidelity(result, _fidelity(
        "step", pmi="attached",
        pmi_skipped=result.get("pmi_skipped", []),
        pmi_notes=result.get("pmi_notes", []),
    ))


# --------------------------------------------------------------- wrappers


def _install(service) -> None:
    export_part = service.export_part
    if not getattr(export_part, _WRAPPED, False):

        @functools.wraps(export_part)
        def _export_part(proj, part_id, format, tolerance=EXPORT_TOLERANCE, *,
                         config=None, pmi=None, metadata=None):
            # `metadata` is accepted (and validated by the tool schema) here so
            # the surface is stable; slice 5 is what stamps it into 3MF. The
            # captured original takes no such argument, so it is not forwarded
            # — and no fidelity axis claims it was written.
            if format not in PART_FORMATS:
                # The same refusal `service._check_format` raises, over the
                # extended list (EXPORT_FORMATS itself is untouched).
                raise ValidationError(
                    f"unknown export format {format!r}",
                    {"known": list(PART_FORMATS)},
                )
            if format in MESH_FORMATS:
                item = _part_item(service, proj, part_id, config)
                name = part_id if config is None else f"{part_id}_{config}"
                service.store.assert_disk_budget(proj)
                out = service.store.exports_dir(proj) / f"{name}.{format}"
                _atomic_write(out, _render([item], format))
                result = {"path": str(out), "size_bytes": out.stat().st_size}
                if config is not None:
                    result["config"] = config
                return _with_fidelity(
                    result, _fidelity(format, colors="per_instance"))
            if format == "step":
                stored = _part_pmi(service, proj, part_id)
                if stored is not None and pmi is not False:
                    return _export_step_pmi(service, proj, part_id, stored,
                                            config=config)
                return _with_fidelity(
                    export_part(proj, part_id, format, tolerance,
                                config=config),
                    _fidelity("step",
                              pmi="opted_out" if stored is not None else "none"),
                )
            return _with_fidelity(
                export_part(proj, part_id, format, tolerance, config=config),
                _fidelity(format),
            )

        setattr(_export_part, _WRAPPED, True)
        service.export_part = _export_part

    export_assembly = service.export_assembly
    if not getattr(export_assembly, _WRAPPED, False):

        @functools.wraps(export_assembly)
        def _export_assembly(proj, format):
            if format not in ASSEMBLY_FORMATS:
                raise ValidationError(
                    "assembly export supports formats: "
                    + ", ".join(ASSEMBLY_FORMATS))
            if format in MESH_FORMATS:
                items, skipped = _assembly_items(service, proj)
                if not items:
                    raise ValidationError(
                        "assembly has no instances to export")
                service.store.assert_disk_budget(proj)
                out = service.store.exports_dir(proj) / f"assembly.{format}"
                _atomic_write(out, _render(items, format))
                return _with_fidelity(
                    {"path": str(out), "size_bytes": out.stat().st_size},
                    _fidelity(format, colors="per_instance", skipped=skipped),
                )
            # step (fused) and stl delegate untouched. Slice 5 adds `3mf` and
            # `structured: true` (→ export_step_structured) right here.
            return _with_fidelity(export_assembly(proj, format),
                                  _fidelity(format))

        setattr(_export_assembly, _WRAPPED, True)
        service.export_assembly = _export_assembly


# ----------------------------------------------------------- tool schemas


def _extend_schemas(registry, service) -> None:
    """Mutate the two registered export tools in place (idempotent).

    The first in-place schema mutation in the codebase, so the test asserting
    it is visible through ``build_registry`` **and** ``GET /api/tools`` is the
    contract. ``import_cad_file``'s schema belongs to ``tools_import``.
    """
    part = registry.get("export_part")
    if part is not None:
        part.description = (
            "Export a part to exports/<part_id>.<format> (or "
            "exports/<part_id>_<config>.<format> with config). Formats: step, "
            "stl, 3mf, gltf, glb. STEP carries the part's PMI as AP242 when it "
            "has any (pmi: false opts out). glTF/GLB are Y-up (converted from "
            "our Z-up), tessellated and coloured. Every result reports what "
            "survived the translation in `fidelity`."
        )
        props = part.input_schema["properties"]
        props["format"] = {
            "type": "string",
            "description": "step | stl | 3mf | gltf | glb",
            "enum": list(PART_FORMATS),
        }
        props["pmi"] = {
            "type": "boolean",
            "description": "STEP only: attach the part's PMI (AP242). Default "
                           "true when the part has PMI; false exports plain "
                           "geometry.",
        }
        props["metadata"] = {
            "type": "object",
            "description": "Optional metadata to stamp into formats that carry "
                           "it (3MF).",
        }
        # The schema and the handler move together: the registered lambda takes
        # neither `pmi` nor `metadata`, and ToolRegistry.call splats the args.
        part.handler = (
            lambda project, part_id, format, tolerance=EXPORT_TOLERANCE,
            config=None, pmi=None, metadata=None:
                service.export_part(project, part_id, format, tolerance,
                                    config=config, pmi=pmi, metadata=metadata)
        )

    assembly = registry.get("export_assembly")
    if assembly is not None:
        assembly.description = (
            "Export the whole assembly (instances placed by their transforms). "
            "Formats: step, stl, gltf, glb. glTF/GLB deduplicate meshes (N "
            "instances of one part are one mesh), carry per-instance colours "
            "and are Y-up. The result reports `fidelity`."
        )
        assembly.input_schema["properties"]["format"] = {
            "type": "string",
            "description": "step | stl | gltf | glb",
            "enum": list(ASSEMBLY_FORMATS),
        }


def register(registry, service) -> None:
    _install(service)
    _extend_schemas(registry, service)
