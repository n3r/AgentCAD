"""Route pack: share-link management (PRD-007 slice 2).

The **authenticated** half of share links — ``POST/GET/DELETE /api/share`` at
the default ``/api`` PREFIX. Its anonymous twin (``/s/`` and ``/embed/``) is a
separate root-mounted pack (``routes_share_public.py``), because a route pack
carries exactly one ``PREFIX`` and the public surface mounts at the root. Two
packs, one capability — the ``routes_auth.py`` / ``routes_public.py`` precedent.

Inert in local mode, exactly like ``routes_auth``: every handler asks
:func:`security.current_config` first and answers ``404`` when there is none, so
a loopback instance is byte-identically what it was and the publication store is
never constructed on it (``ensure_share`` runs only when a request reaches a
handler here).

Naming (design's live-collision list): the URL secret is a ``share_token``, the
management handle is a ``pub_id``, and a publication's ``part|project`` reach is
its ``share_scope`` — never the package-index ``scope`` or ``locks.write_scope``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core.model import AuthError, NotFoundError, ValidationError
from ..core.share_build import ensure_share
from . import security as sec

#: MVP is part-scope only; project/assembly scope is a larger containment
#: surface (a whole-project pin) deferred to Phase 2 (design Decision 8).
SHARE_SCOPES = ("part",)

#: The export mask MVP: B-rep + mesh via the existing ``service.export`` paths.
#: SVG drawings and flat patterns are Phase 2, where a script defines them.
EXPORT_FORMATS = ("step", "stl", "3mf")

_DAY_S = 86400


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    # Captured at mount time, not read per request — the `routes_auth` reason:
    # a second app built later in the same process must not make these handlers
    # answer with its identity store or wake them inside a local-mode app.
    mounted_config = sec.current_config()

    def _config():
        cfg = mounted_config
        if cfg is None or not cfg.mode.hosted:
            raise NotFoundError("this instance is not running in hosted mode")
        return cfg

    def _principal():
        who = sec.current_principal()
        if who is None:
            raise AuthError("authentication required")
        return who

    def _str(body: dict, key: str, *, required: bool = False) -> str | None:
        value = body.get(key)
        if value is None:
            if required:
                raise ValidationError(f"{key!r} is required", {"field": key})
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{key!r} must be a non-empty string",
                                  {"field": key})
        return value

    def _bool(body: dict, key: str, default: bool) -> bool:
        value = body.get(key, default)
        if not isinstance(value, bool):
            raise ValidationError(f"{key!r} must be a boolean", {"field": key})
        return value

    def _exports(body: dict) -> list[str]:
        raw = body.get("exports", [])
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise ValidationError("'exports' must be a list of format names",
                                  {"known": list(EXPORT_FORMATS)})
        bad = sorted(set(raw) - set(EXPORT_FORMATS))
        if bad:
            raise ValidationError(
                f"unknown export format(s): {', '.join(bad)}",
                {"unknown": bad, "known": list(EXPORT_FORMATS)})
        # De-duplicate, preserve the mask's canonical order.
        return [f for f in EXPORT_FORMATS if f in set(raw)]

    def _expires(body: dict) -> int | None:
        days = body.get("expires_days")
        if days is None:
            return None                      # never, until revoked (the default)
        if not isinstance(days, (int, float)) or isinstance(days, bool) \
                or days <= 0:
            raise ValidationError(
                "'expires_days' must be a positive number of days",
                {"field": "expires_days"})
        import time
        return int(time.time() + float(days) * _DAY_S)

    # ------------------------------------------------------------- routes

    @router.post("/share")
    def share_create(request: Request, body: dict):
        cfg = _config()
        who = _principal()
        project = _str(body, "project", required=True)
        share_scope = _str(body, "scope") or "part"
        if share_scope not in SHARE_SCOPES:
            raise ValidationError(
                f"scope must be one of {', '.join(SHARE_SCOPES)} (project scope "
                f"is Phase 2)", {"scope": share_scope, "known": list(SHARE_SCOPES)})
        part_id = _str(body, "part_id", required=True)
        ref = _str(body, "ref")
        customizer = _bool(body, "customizer", False)
        show_script = _bool(body, "show_script", False)
        exports = _exports(body)
        expires = _expires(body)

        builder = ensure_share(service)
        pin = builder.pin(service, project, part_id, ref)
        settings = {
            "customizer": customizer,
            "exports": exports,
            "show_script": show_script,
            "expires": expires,
            "config": None,                  # RESERVED for PRD-012 (FR13)
        }
        pub_id, token = service.publications.create(
            share_scope=share_scope, project=project, part_id=part_id,
            ref=pin["ref"], script_sha=pin["script_sha"], settings=settings,
            created_by=who.name, default_variant_key=pin["default_variant_key"],
            material=pin["material"])
        _announce(project)
        # The secret is returned exactly once — it is the shareable artifact.
        return JSONResponse(
            status_code=201,
            content={"url": f"{cfg.mode.public_origin}/s/{token}",
                     "pub_id": pub_id})

    @router.get("/share")
    def share_list(project: str):
        _config()
        who = _principal()
        return {"links": service.publications.list_for(who.name, project)}

    @router.delete("/share/{pub_id}")
    def share_revoke(pub_id: str):
        _config()
        who = _principal()
        record = service.publications.get(pub_id)
        # A link that is not this owner's is a 404, not a 403 — no oracle over
        # who published what.
        if record is None or (record.get("created_by") != who.name
                              and not who.is_admin):
            raise NotFoundError("no such share link", {"pub_id": pub_id})
        service.publications.revoke(pub_id, by=who.name)
        _announce(record.get("project", ""))
        return {"revoked": True, "pub_id": pub_id}

    def _announce(project: str) -> None:
        bus = getattr(service, "bus", None)
        if bus is not None and project:
            bus.publish({"type": "share_changed", "project": project})

    return router
