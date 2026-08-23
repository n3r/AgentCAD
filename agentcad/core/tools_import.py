"""Tool pack: import external CAD files as reference parts.

Two landings, one tool (PRD-017 FR8–FR10):

* **flat** — today's path, byte-for-byte: one reference part pointing at the
  uploaded file. Needs a ``part_id``.
* **structured** — the kernel's ``import_structured`` materializes one
  ``.brep`` per unique product beside the uploaded file, each lands as a plain
  reference part (ids derived from the product names), and every occurrence
  lands as an assembly instance with its composed transform and colour.

``structured`` defaults to **auto** (see ``_looks_structured``). The extension
is checked *before* the kernel is asked anything: ``inspect_cad_tree`` refuses
a non-STEP outright, and STL/BREP carry no product tree by construction.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ..kernel.client import KernelError
from .imports import ingest_file, safe_import_name
from .model import ID_RE, AuthzError, InstanceSpec, ValidationError
from .tools import Tool, schema

#: Read through ``sys.modules`` rather than imported: a headless registry build
#: (``agentcad check``, the publish gate) never loads the server, has no hosted
#: config, and must not pay for FastAPI to learn that. Same shape as
#: ``core/tools_auth.py``.
_SECURITY_MODULE = "agentcad.server.security"

#: The only extensions that can carry a product tree — the kernel pack's
#: ``STRUCTURED_EXTS``, restated here because the server process may not import
#: a kernel handler module (it imports OCP). ``routes_import`` reads it from
#: here, so there is one list on this side of the wall, not two.
STRUCTURED_EXTS = {".step", ".stp"}

#: A read-only XCAF walk of a large assembly is slower than a plain build, and
#: the materializing half also writes one ``.brep`` per product.
INSPECT_TIMEOUT_S = 300.0
IMPORT_TIMEOUT_S = 900.0

#: OCCT's STEP reader gives an unnamed instance a name of the form
#: ``=>[0:1:1:2]`` (the referred label's entry). It is a placeholder, not a
#: name somebody authored — see ``_looks_structured``.
_PLACEHOLDER_PREFIX = "=>"


def _refuse_a_host_path_in_hosted_mode(source: str) -> None:
    """FR19. ``source`` as an absolute path makes the tool read the *server's*
    disk, which on a loopback bind was the caller's own disk and on a hosted
    instance is not.

    Guarded so local behaviour is byte-identical: this is a pack, not a core,
    and the whole refusal is one early return that never fires without a
    hosted ``SecurityConfig``.
    """
    module = sys.modules.get(_SECURITY_MODULE)
    cfg = module.current_config() if module is not None else None
    if cfg is None or not cfg.mode.hosted:
        return
    raise AuthzError(
        "import_cad_file may not read a path on the server in hosted mode. "
        "Upload the file to the project's imports/ directory first and pass "
        "its filename.",
        {"mode": cfg.mode.name},
    )


# ------------------------------------------------------------------ id rules


def _slug(text: str, fallback: str = "part") -> str:
    """A product/occurrence name reduced to an ``ID_RE`` id.

    Lowercase, ``[a-z0-9_]`` only, a leading digit prefixed (an id must start
    with a letter) and 40 characters at most. Deterministic: the same file
    always produces the same ids, which is what makes a re-import's suffixing
    predictable.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    if slug and not slug[0].isalpha():
        slug = f"p_{slug}"
    slug = (slug or fallback)[:40]
    return slug if ID_RE.match(slug) else fallback


def _unique(base: str, used: set[str]) -> tuple[str, bool]:
    """``base``, or ``base_2``/``base_3``/… when it is taken. Returns the id
    and whether it had to move, so the caller can say so in ``warnings``.

    ``used`` carries the project's EXISTING ids as well as the ones this import
    has already minted — importing the same file twice lands a second set
    beside the first rather than failing halfway through with a ConflictError.
    """
    if base not in used:
        return base, False
    for n in range(2, 1000):
        suffix = f"_{n}"
        candidate = f"{base[:40 - len(suffix)]}{suffix}"
        if candidate not in used:
            return candidate, True
    raise ValidationError(
        f"cannot find a free id for {base!r}: too many collisions"
    )


def _is_placeholder(name: str | None) -> bool:
    return not name or name.startswith(_PLACEHOLDER_PREFIX)


def _looks_structured(payload: dict) -> bool:
    """Auto-detect: does this file carry a product *structure*, or is it one
    part that happens to be a compound?

    More than one occurrence is necessary but **not sufficient**. AgentCAD's
    own ``export_step`` writes a multi-solid part as a compound, and OCCT's
    reader presents that as N unnamed occurrences of N products all called
    ``SOLID`` — auto-structuring it would turn one re-imported widget into N
    parts and N instances, which is the opposite of what the round trip means.
    A file authored as an assembly names *something*: its instances, or (when
    only the components are anonymous) more than one of its products.

    ``structured: true`` overrides this in either direction; the detection only
    decides what happens when the caller said nothing.
    """
    if payload["counts"]["occurrences"] <= 1:
        return False
    if any(not _is_placeholder(o.get("name")) for o in payload["occurrences"]):
        return True
    return len({p.get("name") for p in payload["products"]}) > 1


def _fidelity(*, geometry: str, structure: str, colors: str) -> dict:
    """Design spec §8, import shape. ``parametric`` is always present and
    always ``none`` — no neutral format carries parametric intent."""
    return {
        "geometry": geometry,
        "structure": structure,
        "colors": colors,
        "pmi": "not_read",
        "parametric": "none",
    }


def register(registry, service) -> None:
    def _resolve_source(project: str, source: str) -> str:
        """The uploaded basename under the project's imports/ dir.

        `source` is either a filename already there, or an absolute path to
        ingest — the hosted refusal guards only the second, before the read.
        """
        src = Path(source)
        if src.is_absolute() or "/" in source:
            _refuse_a_host_path_in_hosted_mode(source)
            return ingest_file(service.store, project, src.name, str(src))
        name = safe_import_name(source)
        if not (service.store.imports_dir(project) / name).is_file():
            raise ValidationError(
                f"no imported file {name!r} in project; upload it first "
                "or pass an absolute path"
            )
        return name

    def _flat(project: str, name: str, part_id: str | None, label: str | None,
              material: str, warnings: list[str]) -> dict:
        # Unchanged from v2: one reference part for the whole file. `part_id`
        # is required *here* rather than in the schema, because a structured
        # import derives its ids from the file.
        if not part_id:
            raise ValidationError(
                "import_cad_file needs a 'part_id' for a flat import "
                "(a structured import derives part ids from the file's "
                "product names)"
            )
        detail = service.create_part(
            project, part_id, label=label or part_id, material=material,
            kind="reference", source=name,
        )
        status = detail.get("status", {})
        metrics = detail.get("metrics") or {}
        mesh_only = bool(metrics.get("mesh"))
        return {
            "part": detail,
            "imported": {
                "source": name,
                "n_solids": metrics.get("n_solids"),
                "is_valid": metrics.get("is_valid"),
                "mesh_only": mesh_only,
                "warnings": status.get("warnings", []),
            },
            "warnings": warnings,
            "fidelity": _fidelity(
                # An STL is a mesh whether or not the build got far enough to
                # say so; everything else on the flat path is exact B-rep.
                geometry=("mesh" if mesh_only
                          or Path(name).suffix.lower() == ".stl" else "brep"),
                structure="flat",
                colors="none",
            ),
        }

    def _structured(project: str, name: str, material: str,
                    prefix: str | None, warnings: list[str]) -> dict:
        """N reference parts + N instances from one file's product tree.

        Writes, in order: one manifest write per ``create_part`` (the existing
        path), **one** manifest write for the provenance loose keys, and
        **one** ``set_instances`` for the whole instance batch — then a single
        trailing ``project_changed``, so the batch is one undo step on top of
        the per-part ones.
        """
        # write=True: the .brep files are authored state landing in the
        # project tree, so the batch answers to the write guard and the disk
        # budget exactly like the upload that preceded it.
        imports = service.store.imports_dir(project, write=True)
        payload = service.kernel.request(
            "import_structured",
            {"source_path": str(imports / name), "out_dir": str(imports)},
            timeout_s=IMPORT_TIMEOUT_S,
        )
        warnings = [*warnings, *payload.get("warnings", [])]

        manifest = service.store.manifest(project)
        used_parts = {p["id"] for p in manifest["parts"]}
        used_instances = {i["id"] for i in manifest["assembly"]["instances"]}

        # Ids first, for the whole file: a collision has to be resolved against
        # the parts this import is about to add as well as the ones already
        # there, and an instance may not be created before its part exists.
        part_ids: list[str] = []
        moved: list[str] = []
        for product in payload["products"]:
            base = _slug(f"{prefix}_{product['name']}" if prefix
                         else product["name"])
            part_id, bumped = _unique(base, used_parts)
            used_parts.add(part_id)
            part_ids.append(part_id)
            if bumped:
                moved.append(f"{base} -> {part_id}")
        if moved:
            warnings.append(
                "part id(s) already in use, suffixed: " + ", ".join(moved))

        parts = []
        for part_id, product in zip(part_ids, payload["products"]):
            detail = service.create_part(
                project, part_id, label=product["name"], material=material,
                kind="reference", source=product["file"],
            )
            # Provenance travels with the result too, not only the manifest.
            detail["source_label"] = product["name"]
            detail["import_source"] = name
            parts.append(detail)

        # One manifest write for the loose keys (design §7: never PartRecord
        # fields — old manifests load, and `manifest_merge` merges them per
        # field like every other scalar on a part entry).
        manifest = service.store.manifest(project)
        entries = {e["id"]: e for e in manifest["parts"]}
        for part_id, product in zip(part_ids, payload["products"]):
            entry = entries[part_id]
            entry["source_label"] = product["name"]
            entry["import_source"] = name
        service.store.save_manifest(project, manifest)

        specs = list(service.store.instances(project))
        landed = []
        for occurrence in payload["occurrences"]:
            product = payload["products"][occurrence["product_index"]]
            part_id = part_ids[occurrence["product_index"]]
            # An OCCT placeholder is not a name: fall back to the product's,
            # and let the suffixing number the repeats.
            base = _slug(product["name"] if _is_placeholder(occurrence["name"])
                         else occurrence["name"])
            instance_id, _bumped = _unique(base, used_instances)
            used_instances.add(instance_id)
            spec = InstanceSpec(
                id=instance_id,
                part=part_id,
                position=list(occurrence["position"]),
                rotation_deg=list(occurrence["rotation_deg"]),
                color=occurrence.get("color"),
            )
            specs.append(spec)
            landed.append(spec)
        # One validated write for the whole batch (never one per occurrence).
        service.store.set_instances(project, specs)
        service.bus.publish({"type": "project_changed", "project": project})

        tree = {key: value for key, value in payload.items()
                if key != "warnings"}
        # The mapping a caller needs to relate the file to the project.
        tree["products"] = [
            {**product, "part_id": part_id}
            for product, part_id in zip(payload["products"], part_ids)
        ]
        colors = ("per_instance"
                  if any(o.get("color") for o in payload["occurrences"])
                  else "none")
        return {
            "parts": parts,
            "instances": [spec.to_manifest() for spec in landed],
            "tree": tree,
            "warnings": warnings,
            "fidelity": _fidelity(geometry="brep", structure="tree",
                                  colors=colors),
        }

    def import_cad_file(project: str, source: str, part_id: str | None = None,
                        label: str | None = None, material: str = "al6061",
                        structured: bool | None = None,
                        prefix: str | None = None) -> dict:
        name = _resolve_source(project, source)
        ext = Path(name).suffix.lower()
        warnings: list[str] = []

        if prefix is not None and _slug(prefix, fallback="") == "":
            raise ValidationError(
                f"prefix {prefix!r} has no usable characters "
                "(part ids are [a-z][a-z0-9_]{0,39})"
            )
        if structured and ext not in STRUCTURED_EXTS:
            # Honest refusal rather than a silent flat landing: a mesh (.stl)
            # or a single shape (.brep) has no product tree to read, and a
            # caller who asked for one asked for something the file cannot
            # answer. `structured: false` (or omitted) imports it flat.
            raise ValidationError(
                f"{ext} files carry no product tree — only "
                f"{'/'.join(sorted(STRUCTURED_EXTS))} can be imported "
                "structurally; omit 'structured' to import it as one part",
                {"source": name, "supported": sorted(STRUCTURED_EXTS)},
            )

        go_structured = bool(structured)
        if ext in STRUCTURED_EXTS and structured is None:
            try:
                payload = service.kernel.request(
                    "inspect_cad_tree",
                    {"source_path": str(
                        service.store.imports_dir(project) / name)},
                    timeout_s=INSPECT_TIMEOUT_S,
                )
            except KernelError as exc:
                # Auto-detection is a convenience, not the import: a file the
                # walk cannot read still gets today's flat landing (and the
                # reason, rather than a silent fallback). An explicit
                # `structured: true` never reaches here — it raises.
                warnings.append(
                    "could not inspect the file's product tree "
                    f"({exc.message}); imported as a single part")
            else:
                go_structured = _looks_structured(payload)

        if not go_structured:
            return _flat(project, name, part_id, label, material, warnings)

        ignored = [key for key, value in (("part_id", part_id),
                                          ("label", label)) if value]
        if ignored:
            warnings.append(
                f"{', '.join(ignored)} ignored by a structured import: part "
                "ids and labels come from the file's product names "
                "(pass structured: false to import the file as one part)")
        return _structured(project, name, material, prefix, warnings)

    registry.register(Tool(
        "import_cad_file",
        "Import an external CAD file (.step/.stp/.brep/.stl) as reference "
        "part(s) — no script, but placeable in assemblies and (STEP/BREP) "
        "usable in booleans. STL is mesh-only (measure/display, no booleans). "
        "'source' is an absolute path to ingest, or a filename already "
        "uploaded to the project's imports/ dir. A STEP file with a product "
        "tree is imported STRUCTURALLY by default: one reference part per "
        "unique product (ids from the product names, + 'prefix'), one "
        "assembly instance per occurrence with its composed transform and "
        "colour. 'structured': false forces one part for the whole file "
        "(then 'part_id' is required); true forces the tree read.",
        schema(
            {
                "project": {"type": "string"},
                "source": {"type": "string", "description": "abs path or uploaded filename"},
                "part_id": {"type": "string",
                            "description": "required for a flat import; "
                                           "unused by a structured one"},
                "label": {"type": "string"},
                "material": {"type": "string"},
                "structured": {"type": "boolean",
                               "description": "read the STEP product tree "
                                              "(default: auto)"},
                "prefix": {"type": "string",
                           "description": "prepended to generated part ids"},
            },
            ["project", "source"],
        ),
        import_cad_file,
    ))
