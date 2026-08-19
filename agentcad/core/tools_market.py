"""Tool pack: marketplace install (PRD-031a slice 3).

A marketplace is a registry index + a web front, so installing from it is
PRD-011 **verbatim**: ``add_package`` (resolve + content-verify + record both
manifest maps) then ``use_part`` (materialise a part, the lockfile pinning the
version). ``market_install`` is the one-call agent convenience over that path,
**scoped to install-from-seeded-catalog only** — it pins the install to a
public-scoped catalog index, so it can never pull a package that lives only in a
private index (design Decision 6). The browser "Add to library" affordance uses
the existing authenticated ``POST /api/projects/{proj}/packages`` +
``.../use`` routes and needs no new route.

**Load-order note.** ``tools_market`` (``mar``) sorts *before* ``tools_packages``
(``pac``) in ``_load_tool_packs``, so ``service.packages`` does not yet exist at
``register`` time. It is read **inside** the tool function, never in
``register`` — the same discipline ``tools_run_checks``/``tools_packages``
follow. Nothing kernel-reaching happens at registration.
"""

from __future__ import annotations

from .model import NotFoundError
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}


def _public_catalog_indexes(manager) -> list:
    """The configured indexes an install may draw from: **both scopes public**.

    The same dual-scope filter ``routes_public._public_indexes`` applies for the
    anonymous read, here for the authenticated install — so ``market_install``
    is confined to the seeded public catalog and can never pin a private index.
    ``configured_scope`` (the operator's word) AND ``scope`` (the document's)
    must both be ``"public"``; anything else, including an index kind carrying
    neither, is refused.
    """
    return [ix for ix in manager.indexes
            if getattr(ix, "configured_scope", None) == "public"
            and getattr(ix, "scope", None) == "public"]


def _seeded_index_for(manager, package: str) -> str:
    """The first public-scoped index carrying *package*, or a refusal.

    A package resolvable only from a private index is refused here — before any
    install — so ``market_install``'s seeded-catalog scope is a property of the
    call, not of what happens to be reachable (AC6)."""
    for index in _public_catalog_indexes(manager):
        try:
            entries = index.entries() or {}
        except (NotFoundError, ValueError):
            continue
        record = (entries.get("packages") or {}).get(package)
        if isinstance(record, dict) and record.get("versions"):
            return index.name
    raise NotFoundError(
        f"package {package!r} is not in the seeded public catalog; "
        f"market_install only installs from a public-scoped index",
        {"package": package})


def register(registry, service) -> None:
    def market_install(project: str, package: str, part: str, part_id: str,
                       version_req: str | None = None,
                       preset: str | None = None,
                       params: dict | None = None) -> dict:
        # Read `service.packages` INSIDE the function — it is installed by
        # `tools_packages.register`, which runs AFTER this pack (load order).
        from .tools_packages import materialize

        manager = service.packages
        index = _seeded_index_for(manager, package)     # seeded-catalog scope
        add_result = manager.add(project, package, version_req, index)
        used = materialize(service, project, package, part, part_id,
                           preset=preset, params=params)
        return {
            "project": project,
            "package": package,
            "index": index,
            "lock": add_result.get("lock"),
            "requirement_change": add_result.get("requirement_change"),
            "part": used,
        }

    registry.register(Tool(
        "market_install",
        "Install a package from the seeded PUBLIC marketplace catalog into a "
        "project and materialise one of its parts, in one call — a thin "
        "composition of add_package (pinned to a public-scoped catalog index) "
        "and use_part. SCOPED TO THE SEEDED CATALOG: a package that lives only "
        "in a private index is refused with a not_found_error before anything "
        "is installed, so this can never pull private content. add_package "
        "resolves the requirement, content-verifies the fetched tree, and "
        "records both manifest maps ('packages' and 'packages_lock'); the "
        "lockfile pins the exact version+content_id, so the materialised part "
        "rebuilds byte-identically forever (PRD-011 AC3 inherited, unchanged by "
        "a later listing update until an explicit upgrade). use_part copies the "
        "part in under an immutable provenance header. Optional 'version_req' is "
        "X.Y.Z | ^X.Y.Z | ~X.Y.Z | * (omitted keeps a declared pin, absent is "
        "not '*'); 'preset' applies a shipped configuration and 'params' "
        "overrides parameters one by one, both validated before anything is "
        "written. Returns {project, package, index, lock, requirement_change, "
        "part} — the lock entry is what pins the version.",
        schema({"project": _PROJ,
                "package": {"type": "string",
                            "description": "Catalog package name"},
                "part": {"type": "string",
                         "description": "Part id inside the package"},
                "part_id": {"type": "string",
                            "description": "Id for the new project part"},
                "version_req": {"type": "string",
                                "description": "X.Y.Z | ^X.Y.Z | ~X.Y.Z | *"},
                "preset": {"type": "string",
                           "description": "A shipped configuration name"},
                "params": {"type": "object",
                           "description": "Parameter overrides"}},
               ["project", "package", "part", "part_id"]),
        market_install,
    ))
