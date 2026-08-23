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

What this pack routes:

* ``gltf``/``glb`` — **server-side**, from the ACM1 mesh cache
  (``core/gltf.py``), never a kernel round trip.
* ``usd`` — server-side from the same buffers (``core/usd_export.py``), and
  **only when ``agentcad[usd]`` is installed**: the format enum entry and the
  route behaviour appear together or not at all (the FEM gating rule — an
  agent never sees a tool that cannot run). Without the extra a ``usd``
  request is the ordinary unknown-format ``validation_error``.
* ``step`` on a part that has PMI — the ``export_step_pmi`` kernel handler
  (AP242, slice 1); ``pmi: false`` opts back out to today's path.
* ``3mf`` (part and assembly) — the ``export_3mf_rich`` kernel handler
  (slice 5): per-solid names/colours and stamped metadata, where the plain
  writer emitted neither.
* ``export_assembly {format: "step", structured: true}`` — the
  ``export_step_structured`` kernel handler: a real product tree instead of
  one fused compound. ``structured`` defaults to **false**, so today's
  fused export is untouched unless it is asked for.
* everything else — delegated to the captured original, byte-for-byte.

Every result carries ``fidelity`` (spec §8): what survived the translation and
what did not, on the delegated paths too, because "the export succeeded" and
"the export kept your tolerances" are different sentences.

**Where 3MF metadata comes from** (spec §4, precedence): the caller's explicit
``metadata`` wins per key, then the part's own identity — ``title`` from its
label, ``part_number`` from ``entry["bom"]["part_number"]`` (PRD-015's loose
key), ``designer`` a constant, and ``creation_date`` from **the same resolved
version** PRD-014 prints in a drawing's title block
(``tools_drawing._drawing_version``: a tag or the HEAD sha, and HEAD's *commit*
date). Never ``datetime.now()`` — a wall clock in the file would make two
exports of one state differ on the only axis 3MF has left, since lib3mf already
mints a fresh ``p:UUID`` per object per write (spike D.2). A project with no
history resolves to ``"-"``, which is not a date, so the field is **omitted**
rather than stamped with a placeholder.

OCP-free: this is server-process code (probe in ``tests/test_interop_gltf.py``).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from . import gltf, usage, usd_export
from .interop_colors import category_for, color_for
from .model import AppError, ValidationError
from .service import EXPORT_TOLERANCE
# The version seam PRD-014's title block uses, reused verbatim so a drawing and
# a 3MF exported from one state name the same version. It is private to
# `tools_drawing` only in the underscore sense — this is the `tools_structure`
# imports `service._apply_transform` precedent, not a new coupling. (It is also
# OCP-free: `tools_drawing` reaches the kernel package only for `_sheets`,
# which is pure data.)
from .tools_drawing import _drawing_version

_WRAPPED = "_agentcad_xchange_wrapped"

#: What ``export_part`` accepts before the optional formats are added.
BASE_PART_FORMATS = ("step", "stl", "3mf", "gltf", "glb")
#: What ``export_assembly`` accepts before the optional formats are added.
BASE_ASSEMBLY_FORMATS = ("step", "stl", "3mf", "gltf", "glb")
#: The formats this pack writes itself, from the mesh cache.
MESH_FORMATS = ("gltf", "glb")

#: Written here too, but only when ``agentcad[usd]`` is installed (FR11) — the
#: FEM gating rule: an agent is never offered a format that cannot run. The
#: file is ``.usda`` text, hence the extension override.
USD_FORMAT = "usd"
_EXTENSIONS = {USD_FORMAT: usd_export.SUFFIX.lstrip(".")}

#: Appended to both export descriptions when — and only when — usd is live.
USD_DESCRIPTION = (
    " usd writes a .usda stage (Z-up and millimetres DECLARED, never "
    "converted: metersPerUnit 0.001), one Mesh per unique part referenced by "
    "one prim per instance, with per-instance displayColor."
)


def part_formats() -> tuple[str, ...]:
    """The live export enum: the base list, plus ``usd`` when it can be written.

    A function and not a constant because availability is a property of the
    *interpreter*, and the gating tests monkeypatch it both ways. The module
    attribute is looked up on ``usd_export`` at call time for the same reason.
    """
    return BASE_PART_FORMATS + ((USD_FORMAT,)
                                if usd_export.usd_available() else ())


def assembly_formats() -> tuple[str, ...]:
    return BASE_ASSEMBLY_FORMATS + ((USD_FORMAT,)
                                    if usd_export.usd_available() else ())


#: Import-time snapshots, for callers that read the surface as data (the
#: tests, and the OCP-free probe). ``register`` always recomputes.
PART_FORMATS = part_formats()
ASSEMBLY_FORMATS = assembly_formats()

#: Stamped as the 3MF ``Designer`` when the caller names none. The tool wrote
#: the file; the human is named by the project's history, not by a guess here.
DESIGNER = "AgentCAD"

#: The metadata keys ``export_3mf_rich`` knows, lowercase. Callers may spell
#: them the 3MF way (``Title``, ``PartNumber``) — both normalize to these.
METADATA_KEYS = ("title", "designer", "description", "creation_date",
                 "part_number")


# --------------------------------------------------------------- fidelity


def _fidelity(fmt: str, *, pmi: str | None = None, pmi_skipped=None,
              pmi_notes=None, colors: str | None = None,
              metadata: str = "none", structure: str | None = None,
              skipped=None) -> dict:
    """Spec §8: the axes this FORMAT can carry, and ``parametric`` always.

    An axis a format cannot express is absent rather than ``"none"`` — "STL
    has no PMI" is not news, "your STEP dropped a datum" is.
    """
    out: dict = {"geometry": "brep" if fmt == "step" else "mesh"}
    if structure is not None:
        # A structured STEP is a product TREE with per-instance colours. It
        # carries no PMI: the AP242 PMI writer is the single-part path, and
        # claiming `pmi: "none"` here would read as "your PMI was dropped".
        out["structure"] = structure
        out["colors"] = colors or "none"
    elif fmt == "step":
        out["pmi"] = pmi or "none"
        if pmi_skipped is not None:
            out["pmi_skipped"] = list(pmi_skipped)
        if pmi_notes is not None:
            out["pmi_notes"] = list(pmi_notes)
    if fmt in ("3mf", "gltf", "glb", USD_FORMAT):
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


def _staging_name(path: Path) -> str:
    """A staging name **no other writer can be holding** (changelog 0181).

    The fixed ``.<stem>.tmp<suffix>`` this used to be is one name per target
    path, so two exports of one part raced into the same file and each
    ``os.replace``d the interleaved mixture into place — ``os.replace`` was
    atomic the whole time; the file being replaced *from* was shared.
    ``ProjectStore._atomic_write`` is the precedent, spelled the same way.
    """
    return f".{path.stem}.{os.urandom(6).hex()}.tmp{path.suffix}"


def _atomic_write(path: Path, data: bytes) -> None:
    """tmp + ``os.replace``, the worker's own export rule: a killed export
    never leaves a torn file where a whole one used to be."""
    tmp = path.with_name(_staging_name(path))
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        # Only ever OUR staging file: the random suffix is what makes that
        # sentence true, and cleaning up a shared one was the second half of
        # the corruption.
        tmp.unlink(missing_ok=True)
        raise


def _render(items, fmt: str) -> bytes:
    """Serialize *items* into *fmt*'s bytes, refusing as a ``validation_error``.

    ``GltfError``/``UsdError`` are ``ValidationError`` subclasses, so they can
    no longer escape as a 500 — but the wire ``type`` a registry envelope
    carries is the class NAME (``ToolRegistry.call``), and a caller matching on
    the documented ``validation_error`` would not recognize ``gltf_error``. The
    class keeps its name for the reader; the envelope keeps the contract.
    """
    try:
        if fmt == USD_FORMAT:
            return usd_export.build_usd(items)
        return (gltf.build_glb(items) if fmt == "glb"
                else gltf.build_gltf(items)[0])
    except (gltf.GltfError, usd_export.UsdError) as exc:
        raise ValidationError(exc.message,
                              {**exc.details, "format": fmt}) from exc


def _self_written(fmt: str) -> bool:
    """Formats this pack writes itself from the ACM cache."""
    return fmt in MESH_FORMATS or fmt == USD_FORMAT


def _export_path(service, proj: str, name: str, fmt: str) -> Path:
    return (service.store.exports_dir(proj)
            / f"{name}.{_EXTENSIONS.get(fmt, fmt)}")


def _mesh_path(service, project: str, key: str) -> Path:
    return service.store.cache_dir(project) / f"{key}.acm"


def _part_item(service, proj: str, part_id: str, config: str | None) -> dict:
    # `mesh_info` is the public seam that BUILDS: it goes through
    # `_ensure_built` / `_ensure_config_built`, exactly as `get_part` and the
    # mesh route do, so an unbuilt (or stale) part is built before conversion
    # and a failed build raises the ordinary KernelError.
    info = service.mesh_info(proj, part_id, config=config)
    record = service._record_for(proj, part_id, config)
    acm_bytes = Path(info["path"]).read_bytes()
    if not gltf.has_triangles(acm_bytes):
        # A part whose build produced no triangles (an empty sketch, a boolean
        # that cancelled) has nothing a mesh format can carry. One part, one
        # refusal — the ASSEMBLY path skips the member instead (see
        # `_assembly_items`), because there the rest of the file is still real.
        raise ValidationError(
            f"nothing to tessellate: part {part_id!r} has no triangles",
            {"part_id": part_id},
        )
    return {
        "instance_id": part_id,
        "mesh_key": info["key"],
        "acm_bytes": acm_bytes,
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
        acm_bytes = path.read_bytes()
        if not gltf.has_triangles(acm_bytes):
            # A degenerate member is a ROW, not a dead export: `parse_acm`
            # refusing here used to cost the caller the other thirty-nine
            # instances, and `instances_skipped` is the contract that already
            # says what did not make it into the file.
            skipped.append({"id": instance_id, "reason": "empty_mesh"})
            continue
        try:
            record = service.store.get_part(owner, entry.get("part", ""))
        except AppError:
            record = None
        items.append({
            "instance_id": instance_id,
            "mesh_key": key,
            "acm_bytes": acm_bytes,
            "position": entry.get("position") or [0.0, 0.0, 0.0],
            "rotation_deg": entry.get("rotation_deg") or [0.0, 0.0, 0.0],
            "color_hex": color_for(record, entry),
            "material_category": category_for(record),
        })
    return items, skipped


# ------------------------------------------------------- metadata & colours


def _normalize_metadata(metadata) -> dict:
    """The caller's ``metadata`` argument → the kernel's key vocabulary.

    ``Title``/``PartNumber``/``part_number`` all land on ``part_number``-style
    lowercase keys; an unknown key is a ``validation_error`` HERE rather than a
    kernel refusal, because the tool schema is where a caller looks.
    """
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be an object")
    out: dict = {}
    for key, value in metadata.items():
        name = str(key).strip().lower()
        # `PartNumber` and `CreationDate` are the 3MF spellings; accept both.
        name = {"partnumber": "part_number",
                "creationdate": "creation_date"}.get(name, name)
        if name not in METADATA_KEYS:
            raise ValidationError(
                f"unknown metadata key {key!r}",
                {"known": list(METADATA_KEYS)},
            )
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValidationError(f"metadata.{name} must be a string")
        # lib3mf takes a C string: a NUL truncates the value silently at the
        # library boundary (`Title\0evil` is stamped as `Title`), and the other
        # C0 controls are not legal XML character data at all — so a value
        # carrying one is refused here rather than half-written there.
        bad = next((c for c in value if ord(c) < 0x20 and c not in "\t\n\r"),
                   None)
        if bad is not None:
            raise ValidationError(
                f"metadata.{name} contains a control character "
                f"(U+{ord(bad):04X}); 3MF metadata is text",
                {"key": name},
            )
        out[name] = value
    return out


def _version_date(service, proj: str) -> str | None:
    """The project's resolved version date (``YYYY-MM-DD``), or ``None``.

    PRD-014's own resolver, so a drawing's title block and a 3MF's
    ``CreationDate`` cannot disagree. ``"-"`` (no repo, or an unborn branch) is
    not a date and is reported as "no date" rather than stamped.
    """
    date = str(_drawing_version(service, proj).get("date") or "").strip()
    return date if date and date != "-" else None


def _part_entry(service, proj: str, part_id: str) -> dict:
    for entry in service.store.manifest(proj).get("parts", []):
        if isinstance(entry, dict) and entry.get("id") == part_id:
            return entry
    return {}


def _part_number(entry: dict) -> str | None:
    bom = entry.get("bom")
    number = bom.get("part_number") if isinstance(bom, dict) else None
    return number if isinstance(number, str) and number else None


def _metadata_for(service, proj: str, *, title: str,
                  part_number: str | None = None, explicit=None) -> dict:
    """Derived defaults, then the caller's explicit keys on top (spec §4)."""
    out = {"title": title, "designer": DESIGNER}
    if part_number:
        out["part_number"] = part_number
    date = _version_date(service, proj)
    if date:
        out["creation_date"] = date
    out.update(_normalize_metadata(explicit))
    return out


def _solid_colors(record) -> dict:
    """``solid label/index -> #rrggbb`` for a part's per-solid materials.

    Keyed exactly as ``set_solid_materials`` writes them (label **or** index
    string); the kernel resolves the same precedence the density lookup does.
    A part with NO per-solid materials gets no colours at all — a single
    uniform material is not a colour claim, and an uncoloured 3MF prints in the
    slicer's own default rather than in a guess.
    """
    materials = getattr(record, "solid_materials", None) or {}
    return {key: color_for(record, solid_material=material)
            for key, material in materials.items()}


# ------------------------------------------------------------------- 3MF v2


def _kernel_source(service, proj: str, record) -> dict:
    """The two shape sources ``service._shape_item`` resolves, in the kernel's
    own key names (``source_path``, not ``source``)."""
    if record.kind == "reference":
        return {"source_kind": "reference",
                "source_path": str(service.store.imports_dir(proj)
                                   / Path(record.source).name)}
    return {"source_kind": "script",
            "script": service.store.read_script(proj, record.id),
            "params": record.effective_params}


def _export_3mf_part(service, proj: str, part_id: str, tolerance: float,
                     config: str | None, metadata) -> dict:
    record = service._record_for(proj, part_id, config)
    name = part_id if config is None else f"{part_id}_{config}"
    service.store.assert_disk_budget(proj)          # before the worker writes
    out = service.store.exports_dir(proj) / f"{name}.3mf"
    solid_colors = _solid_colors(record)
    params: dict = {
        "out_path": str(out),
        "tolerance": tolerance,
        "name": record.label or part_id,
        "metadata": _metadata_for(
            service, proj, title=record.label or part_id,
            part_number=_part_number(_part_entry(service, proj, part_id)),
            explicit=metadata),
        **_kernel_source(service, proj, record),
    }
    if solid_colors:
        params["solid_colors"] = solid_colors
        # Solids the author did not assign a material to still get the part's
        # own colour — a mixed part should not print half-uncoloured.
        params["default_color"] = color_for(record)
    with usage.scoped(proj):
        result = service.kernel.request("export_3mf_rich", params,
                                        timeout_s=300.0, affinity=part_id)
    if config is not None:
        result["config"] = config
    return _with_fidelity(result, _fidelity(
        "3mf", colors=result.get("colors", "none"),
        metadata="attached" if result.get("metadata_stamped") else "none"))


def _export_3mf_assembly(service, proj: str, tolerance: float) -> dict:
    items = _structured_items(service, proj)
    service.store.assert_disk_budget(proj)
    out = service.store.exports_dir(proj) / "assembly.3mf"
    params = {
        "items": items,
        "out_path": str(out),
        "tolerance": tolerance,
        "metadata": _metadata_for(service, proj, title=proj),
    }
    with usage.scoped(proj):
        result = service.kernel.request("export_3mf_rich", params,
                                        timeout_s=300.0)
    return _with_fidelity(result, _fidelity(
        "3mf",
        # One object per instance, each carrying that instance's colour: the
        # kernel counts them per solid, the caller asked about instances.
        colors="per_instance" if result.get("colors") != "none" else "none",
        metadata="attached" if result.get("metadata_stamped") else "none"))


# -------------------------------------------------- structured STEP (FR2)


def _structured_items(service, proj: str) -> list[dict]:
    """One kernel item per resolved instance: identity, source, pose, colour.

    Built from ``service._resolved_instances`` + ``_record_for`` +
    ``_shape_item`` — the same three seams ``tools_structure``'s own
    ``export_assembly`` uses, so PRD-013 expansion (patterns, sub-assemblies,
    mates) feeds this list exactly as it feeds the fused export. It is NOT
    ``get_assembly``: that view carries *meshes*, and a product needs a script
    or a source file.

    ``part_id`` is the dedup key, and it is (owner project, part,
    configuration): two instances of one part bound to different
    configurations are different geometry and must be two products.
    """
    items: list[dict] = []
    for inst in service._resolved_instances(proj):
        owner = getattr(inst, "origin_project", None) or proj
        record = service._record_for(owner, inst.part, inst.config)
        placed = service._shape_item(owner, record, inst)
        key = f"{owner}/{inst.part}"
        if inst.config:
            key = f"{key}#{inst.config}"
        # `_shape_item` already resolved the script-or-source pair; only the
        # key name differs (the kernel handlers take `source_path`).
        source = ({"source_kind": "reference", "source_path": placed["source"]}
                  if placed.get("source")
                  else {"source_kind": "script", "script": placed["script"],
                        "params": placed["params"]})
        items.append({
            "part_id": key,
            "part_name": record.label or inst.part,
            "part_color": color_for(record),
            "name": inst.id,
            "position": placed["position"],
            "rotation_deg": placed["rotation_deg"],
            "color": color_for(record, inst),
            **source,
        })
    if not items:
        raise ValidationError("assembly has no instances to export")
    return items


def _export_step_structured(service, proj: str) -> dict:
    items = _structured_items(service, proj)
    service.store.assert_disk_budget(proj)
    out = service.store.exports_dir(proj) / "assembly.step"
    with usage.scoped(proj):
        result = service.kernel.request(
            "export_step_structured",
            {"items": items, "out_path": str(out), "name": proj},
            timeout_s=300.0,
        )
    return _with_fidelity(result, _fidelity(
        "step", structure="tree", colors="per_instance"))


# --------------------------------------------------------------- STEP PMI


def _part_pmi(service, proj: str, part_id: str) -> dict | None:
    """The part's stored PMI section, or ``None`` when it has none.

    Read from the manifest entry the way ``tools_pmi`` writes it (a loose key,
    never a ``PartRecord`` field) and **re-validated on the way out**.

    ``set_part_pmi`` validates on write, but a loose key is exactly the kind of
    thing a hand edit, a merge or an older tool can leave malformed — and this
    value is handed to the kernel, where a missing ``kind`` was a ``KeyError``
    inside ``map_pmi``: a 502 blaming the worker for a manifest the user can
    fix. Validating here makes it a 422 naming the part and the problem.
    """
    from .pmi import validate_pmi

    for entry in service.store.manifest(proj).get("parts", []):
        if entry.get("id") == part_id:
            pmi = entry.get("pmi")
            if not isinstance(pmi, dict) or not any(
                    pmi.get(k) for k in ("dims", "datums", "fcf")):
                return None
            try:
                return validate_pmi(pmi)
            except ValidationError as exc:
                raise ValidationError(
                    f"part {part_id!r} has a malformed pmi section: "
                    f"{exc.message}",
                    {"part_id": part_id, **exc.details},
                ) from exc
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
    # "attached" is a claim about the FILE, so it is read off what the worker
    # actually wrote: a part whose every entry was skipped exports a perfectly
    # good AP242 file with no PMI in it, and reporting `pmi: "attached"` there
    # is the one lie `fidelity` exists to prevent (FR12). The skipped rows and
    # the notes ride along either way — they are how the caller learns why.
    attached = result.get("pmi_attached") or {}
    carried = any(int(attached.get(key) or 0) > 0
                  for key in ("dims", "datums", "fcf"))
    return _with_fidelity(result, _fidelity(
        "step", pmi="attached" if carried else "none",
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
            if format not in part_formats():
                # The same refusal `service._check_format` raises, over the
                # extended list (EXPORT_FORMATS itself is untouched). Without
                # the `usd` extra, `usd` lands here like any other unknown.
                raise ValidationError(
                    f"unknown export format {format!r}",
                    {"known": list(part_formats())},
                )
            if _self_written(format):
                item = _part_item(service, proj, part_id, config)
                name = part_id if config is None else f"{part_id}_{config}"
                service.store.assert_disk_budget(proj)
                out = _export_path(service, proj, name, format)
                _atomic_write(out, _render([item], format))
                result = {"path": str(out), "size_bytes": out.stat().st_size}
                if config is not None:
                    result["config"] = config
                return _with_fidelity(
                    result, _fidelity(format, colors="per_instance"))
            if format == "3mf":
                # The plain writer's 3MF had neither names nor colours (the
                # spike's D.1 trap); every part 3MF goes through the rich
                # handler now, including one with nothing to stamp but a title.
                return _export_3mf_part(service, proj, part_id, tolerance,
                                        config, metadata)
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
        def _export_assembly(proj, format, *, structured=False,
                             tolerance=EXPORT_TOLERANCE):
            if format not in assembly_formats():
                raise ValidationError(
                    "assembly export supports formats: "
                    + ", ".join(assembly_formats()))
            if structured and format != "step":
                raise ValidationError(
                    "structured export is STEP only (the other formats are "
                    "already per-instance)")
            if format == "step" and structured:
                return _export_step_structured(service, proj)
            if format == "3mf":
                return _export_3mf_assembly(service, proj, tolerance)
            if _self_written(format):
                items, skipped = _assembly_items(service, proj)
                if not items:
                    raise ValidationError(
                        "assembly has no instances to export")
                service.store.assert_disk_budget(proj)
                out = _export_path(service, proj, "assembly", format)
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
    formats = part_formats()
    part = registry.get("export_part")
    if part is not None:
        part.description = (
            "Export a part to exports/<part_id>.<format> (or "
            "exports/<part_id>_<config>.<format> with config). Formats: "
            + ", ".join(formats)
            + ". STEP carries the part's PMI as AP242 when it "
            "has any (pmi: false opts out). glTF/GLB are Y-up (converted from "
            "our Z-up), tessellated and coloured. Every result reports what "
            "survived the translation in `fidelity`."
            + (USD_DESCRIPTION if USD_FORMAT in formats else "")
        )
        props = part.input_schema["properties"]
        props["format"] = {
            "type": "string",
            "description": " | ".join(formats),
            "enum": list(formats),
        }
        props["pmi"] = {
            "type": "boolean",
            "description": "STEP only: attach the part's PMI (AP242). Default "
                           "true when the part has PMI; false exports plain "
                           "geometry.",
        }
        props["metadata"] = {
            "type": "object",
            "description": "3MF only: metadata to stamp into the model — "
                           "title, designer, description, creation_date, "
                           "part_number. Each key overrides the derived "
                           "default (label, part number from the BOM fields, "
                           "and the project's version date).",
        }
        # The schema and the handler move together: the registered lambda takes
        # neither `pmi` nor `metadata`, and ToolRegistry.call splats the args.
        part.handler = (
            lambda project, part_id, format, tolerance=EXPORT_TOLERANCE,
            config=None, pmi=None, metadata=None:
                service.export_part(project, part_id, format, tolerance,
                                    config=config, pmi=pmi, metadata=metadata)
        )

    formats = assembly_formats()
    assembly = registry.get("export_assembly")
    if assembly is not None:
        assembly.description = (
            "Export the whole assembly (instances placed by their transforms). "
            "Formats: " + ", ".join(formats) + ". step is one fused solid "
            "unless structured: true, which writes a real STEP product tree "
            "(one product per part, one occurrence per instance, names and "
            "colours). glTF/GLB deduplicate meshes (N instances of one part "
            "are one mesh), carry per-instance colours and are Y-up; 3MF is "
            "one coloured object per instance with model metadata. The result "
            "reports `fidelity`."
            + (USD_DESCRIPTION if USD_FORMAT in formats else "")
        )
        props = assembly.input_schema["properties"]
        props["format"] = {
            "type": "string",
            "description": " | ".join(formats),
            "enum": list(formats),
        }
        props["structured"] = {
            "type": "boolean",
            "description": "STEP only: write a product tree (one product per "
                           "part, one occurrence per instance) instead of one "
                           "fused solid. Default false.",
        }
        # Same rule as export_part: the registered lambda takes neither
        # keyword, and ToolRegistry.call splats the arguments.
        assembly.handler = (
            lambda project, format, structured=False:
                service.export_assembly(project, format, structured=structured)
        )


def register(registry, service) -> None:
    _install(service)
    _extend_schemas(registry, service)
