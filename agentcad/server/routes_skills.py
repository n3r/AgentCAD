"""Skill routes: the browser's read path, and the human-only trust writes.

    GET   /api/projects/{proj}/skills                     -> index + trust state
    GET   /api/projects/{proj}/skills/{name}?asset=        -> the load payload
    POST  /api/projects/{proj}/skills/{name}/trust         -> approve (human)
    POST  /api/projects/{proj}/skills/{name}/untrust       -> withdraw (human)
    PATCH /api/projects/{proj}/skills/{name}/enabled       -> hide/restore (human)

Both reads go through ``registry.call`` rather than ``service.skills``
directly, so a browser preview logs a ``skill_loaded`` exactly like chat and
MCP do — one audit path, not three. The chat chip filters on the event's
``client``, so a browser read renders no chip.

The three writes are **not tools**. Granting trust is approving agent
instructions, so it must be a human act (``actor_kind`` over the calling
client id → 403 otherwise); a human-gated tool would sit in every agent's tool
list answering "refused for you", which is noise, and the runtime — not the
model — is where that permission belongs. Each publishes ``skills_changed`` so
an open Skills modal refreshes, and returns the updated index entry.

The ``{name}`` segment is checked against ``NAME_RE`` *before* it reaches the
library: a skill name is a slug, and the library resolves names to paths.
Refusals ride ``routes_configs._result`` / the house ``AppError`` mapping, so
an unknown name is a 404 and an untrusted or disabled skill is a 422.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core import locks
from ..core.model import AuthzError, NotFoundError, ValidationError
from ..core.proposals import actor_kind
from ..core.skills import NAME_RE
from .routes_configs import _json, _result


def _skill_name(name: str) -> str:
    """A skill name is a slug. Anything else never reaches the library."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise NotFoundError(
            f"skill {name!r} not found",
            {"reason": "skill_not_found", "name": name,
             "hint": "a skill name is lowercase letters, digits and hyphens"},
        )
    return name


def _require_human(action: str) -> None:
    client = locks.current_client_id()
    if actor_kind(client) != "human":
        raise AuthzError(
            f"only a human can {action}: a skill is agent instructions, so no "
            f"agent surface may approve one",
            {"client": client, "hint": "do this from the Skills panel"},
        )


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{proj}/skills")
    def list_skills(proj: str):
        listing = _result(registry.call("list_skills", {"project": proj}))
        # Read after the tool call: an unknown project must 404 from the
        # registry, not read as an empty trust document.
        return {"skills": listing["skills"], "hidden": listing["hidden"],
                "trust": service.skills.trust_state(proj)}

    @router.get("/projects/{proj}/skills/{name}")
    def get_skill(proj: str, name: str, asset: str | None = None):
        args = {"project": proj, "name": _skill_name(name)}
        if asset is not None:
            args["asset"] = asset
        return _result(registry.call("load_skill", args))

    @router.post("/projects/{proj}/skills/{name}/trust")
    def trust_skill(proj: str, name: str):
        _require_human("trust a project skill")
        entry = service.skills.trust(proj, _skill_name(name))
        service.bus.publish({"type": "skills_changed", "project": proj})
        return entry

    @router.post("/projects/{proj}/skills/{name}/untrust")
    def untrust_skill(proj: str, name: str):
        _require_human("withdraw trust from a project skill")
        entry = service.skills.untrust(proj, _skill_name(name))
        service.bus.publish({"type": "skills_changed", "project": proj})
        return entry

    @router.patch("/projects/{proj}/skills/{name}/enabled")
    async def set_skill_enabled(proj: str, name: str, request: Request):
        _require_human("enable or disable a skill")
        body = await _json(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            # Required and strictly boolean: an absent key cannot mean
            # "disable", and "false" is not False.
            raise ValidationError(
                "body must be {\"enabled\": true|false}",
                {"got": type(body.get("enabled")).__name__},
            )
        entry = service.skills.set_enabled(proj, _skill_name(name), enabled)
        service.bus.publish({"type": "skills_changed", "project": proj})
        return entry

    return router
