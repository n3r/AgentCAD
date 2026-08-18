"""Tool pack: ``share_create`` / ``share_list`` / ``share_revoke`` (PRD-007).

The agent-facing half of share links, registered **only when this process is
serving a hosted app** — the ``whoami`` precedent (``tools_auth.py``): a share
link is meaningless on a loopback instance with no public origin, so an agent is
never offered a tool that cannot run. The module reads the server's security
config out of ``sys.modules`` rather than importing the server, so the headless
paths (``agentcad check``, the publish gate) pay nothing to learn they have no
hosted config.

Thin wrappers over the same ``PublicationStore`` + ``ShareBuilder`` the
management routes use (``ensure_share``): the pin is a copy, the store is under
the state dir, and ``created_by`` is the *current request's* principal — never
whoever built the registry.
"""

from __future__ import annotations

import sys
import time

from .model import AuthError, ValidationError
from .share_build import ensure_share
from .tools import Tool, schema

_SECURITY_MODULE = "agentcad.server.security"

#: Kept in step with ``routes_share`` — one capability, two surfaces.
SHARE_SCOPES = ("part",)
EXPORT_FORMATS = ("step", "stl", "3mf")


def register(registry, service) -> None:
    module = sys.modules.get(_SECURITY_MODULE)
    cfg = module.current_config() if module is not None else None
    if cfg is None or not cfg.mode.hosted:
        return

    def _who():
        who = module.current_principal()
        if who is None:
            raise AuthError("authentication required")
        return who

    def _validate_exports(exports):
        exports = exports or []
        if not isinstance(exports, list) or not all(isinstance(x, str)
                                                    for x in exports):
            raise ValidationError("'exports' must be a list of format names",
                                  {"known": list(EXPORT_FORMATS)})
        bad = sorted(set(exports) - set(EXPORT_FORMATS))
        if bad:
            raise ValidationError(f"unknown export format(s): {', '.join(bad)}",
                                  {"unknown": bad, "known": list(EXPORT_FORMATS)})
        return [f for f in EXPORT_FORMATS if f in set(exports)]

    def share_create(project: str, part_id: str, scope: str = "part",
                     ref: str | None = None, customizer: bool = False,
                     exports: list | None = None, show_script: bool = False,
                     expires_days: float | None = None) -> dict:
        who = _who()
        if scope not in SHARE_SCOPES:
            raise ValidationError(
                f"scope must be one of {', '.join(SHARE_SCOPES)} "
                f"(project scope is Phase 2)", {"scope": scope})
        exports = _validate_exports(exports)
        expires = None
        if expires_days is not None:
            if not isinstance(expires_days, (int, float)) \
                    or isinstance(expires_days, bool) or expires_days <= 0:
                raise ValidationError(
                    "'expires_days' must be a positive number of days")
            expires = int(time.time() + float(expires_days) * 86400)

        builder = ensure_share(service)
        pin = builder.pin(service, project, part_id, ref)
        settings = {"customizer": bool(customizer), "exports": exports,
                    "show_script": bool(show_script), "expires": expires,
                    "config": None}
        pub_id, token = service.publications.create(
            share_scope=scope, project=project, part_id=part_id,
            ref=pin["ref"], script_sha=pin["script_sha"], settings=settings,
            created_by=who.name, default_variant_key=pin["default_variant_key"],
            material=pin["material"])
        _announce(service, project)
        return {"url": f"{cfg.mode.public_origin}/s/{token}", "pub_id": pub_id}

    def share_list(project: str) -> dict:
        return {"links": service.publications.list_for(_who().name, project)}

    def share_revoke(project: str, pub_id: str) -> dict:
        who = _who()
        record = service.publications.get(pub_id)
        if record is None or (record.get("created_by") != who.name
                              and not who.is_admin):
            return {"revoked": False, "pub_id": pub_id}
        service.publications.revoke(pub_id, by=who.name)
        _announce(service, project)
        return {"revoked": True, "pub_id": pub_id}

    registry.register(Tool(
        "share_create",
        "Publish a part at a version as an unlisted share link. Returns the "
        "URL (shown once) and a pub_id. A customizer link additionally exposes "
        "the part's typed PARAMS as bounded sliders. Hosted mode only.",
        schema({
            "project": {"type": "string", "description": "Project name"},
            "part_id": {"type": "string", "description": "Part id to publish"},
            "scope": {"type": "string",
                      "description": "'part' (MVP; project is Phase 2)"},
            "ref": {"type": "string",
                    "description": "A version tag; a branch auto-tags; "
                                   "omitted pins the current head"},
            "customizer": {"type": "boolean",
                           "description": "Expose PARAMS sliders that rebuild"},
            "exports": {"type": "array",
                        "description": "Allowed downloads: step, stl, 3mf"},
            "show_script": {"type": "boolean",
                            "description": "Serve the pinned script read-only"},
            "expires_days": {"type": "number",
                             "description": "Days until expiry (default: never)"},
        }, ["project", "part_id"]),
        share_create,
    ))
    registry.register(Tool(
        "share_list",
        "The caller's active share links in a project, with coarse "
        "view/rebuild/download counters — never the raw token. Hosted mode only.",
        schema({"project": {"type": "string", "description": "Project name"}},
               ["project"]),
        share_list,
    ))
    registry.register(Tool(
        "share_revoke",
        "Revoke a share link by pub_id, immediately. Hosted mode only.",
        schema({"project": {"type": "string", "description": "Project name"},
                "pub_id": {"type": "string", "description": "The link handle"}},
               ["project", "pub_id"]),
        share_revoke,
    ))


def _announce(service, project: str) -> None:
    bus = getattr(service, "bus", None)
    if bus is not None and project:
        bus.publish({"type": "share_changed", "project": project})
