"""Tool pack: import external CAD files as reference parts."""

from __future__ import annotations

import sys
from pathlib import Path

from .imports import ingest_file, safe_import_name
from .model import AuthzError, ValidationError
from .tools import Tool, schema

#: Read through ``sys.modules`` rather than imported: a headless registry build
#: (``agentcad check``, the publish gate) never loads the server, has no hosted
#: config, and must not pay for FastAPI to learn that. Same shape as
#: ``core/tools_auth.py``.
_SECURITY_MODULE = "agentcad.server.security"


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


def register(registry, service) -> None:
    def import_cad_file(project: str, source: str, part_id: str,
                        label: str | None = None, material: str = "al6061") -> dict:
        # `source` is either a filename already under the project's imports/
        # dir, or an absolute path to ingest.
        src = Path(source)
        if src.is_absolute() or "/" in source:
            _refuse_a_host_path_in_hosted_mode(source)
            name = ingest_file(service.store, project, src.name, str(src))
        else:
            name = safe_import_name(source)
            if not (service.store.imports_dir(project) / name).is_file():
                raise ValidationError(
                    f"no imported file {name!r} in project; upload it first "
                    "or pass an absolute path"
                )
        detail = service.create_part(
            project, part_id, label=label or part_id, material=material,
            kind="reference", source=name,
        )
        status = detail.get("status", {})
        metrics = detail.get("metrics") or {}
        return {
            "part": detail,
            "imported": {
                "source": name,
                "n_solids": metrics.get("n_solids"),
                "is_valid": metrics.get("is_valid"),
                "mesh_only": bool(metrics.get("mesh")),
                "warnings": status.get("warnings", []),
            },
        }

    registry.register(Tool(
        "import_cad_file",
        "Import an external CAD file (.step/.stp/.brep/.stl) as a reference "
        "part — no script, but placeable in assemblies and (STEP/BREP) usable "
        "in booleans. STL is mesh-only (measure/display, no booleans). "
        "'source' is an absolute path to ingest, or a filename already "
        "uploaded to the project's imports/ dir.",
        schema(
            {
                "project": {"type": "string"},
                "source": {"type": "string", "description": "abs path or uploaded filename"},
                "part_id": {"type": "string"},
                "label": {"type": "string"},
                "material": {"type": "string"},
            },
            ["project", "source", "part_id"],
        ),
        import_cad_file,
    ))
