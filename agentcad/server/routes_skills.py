"""Skill routes: the browser's read path, and the human-only trust writes.

    GET   /api/projects/{proj}/skills                     -> index + trust state
    GET   /api/projects/{proj}/skills/{name}?asset=        -> the load payload
    POST  /api/projects/{proj}/skills/{name}/trust         -> approve (human)
    POST  /api/projects/{proj}/skills/{name}/untrust       -> withdraw (human)
    PATCH /api/projects/{proj}/skills/{name}/enabled       -> hide/restore (human)

**The reads are two paths, because they have two audiences.**

A *human* client reads through :meth:`SkillLibrary.load` directly with
``enforce_trust=False``: reviewing a skill is what trusting it is *for*, and a
panel that refuses to show you the thing it is asking you to approve is a
consent dialog with the text blanked out. That read makes no registry call and
publishes no ``skill_loaded`` — a person reading a file is not an agent loading
instructions, and logging it as one puts noise in the audit trail the chip and
the transcript are built on. Everything else the library checks still
applies: a disabled, invalid, capability-gated or unknown skill is refused
exactly as it is for an agent.

Any *other* client keeps going through ``registry.call("load_skill")``, so an
agent's read is the tool's read — trust-enforced, audited, identical to MCP's.

``GET /projects/{proj}/skills`` reads ``service.skills.index(proj)`` without
redaction for the same reason: the panel is the human surface, and
``list_skills`` (the agent's) is the one that hides an unreviewed skill's
prose.

The three writes are **not tools**. Granting trust is approving agent
instructions, so it must be a human act; a human-gated tool would sit in every
agent's tool list answering "refused for you", which is noise, and the
runtime — not the model — is where that permission belongs. Each publishes
``skills_changed`` so an open Skills modal refreshes, and returns the updated
index entry.

**A human is an EXPLICIT principal** (:func:`_is_human`), not merely something
``actor_kind`` calls human. ``server/app.py`` turns a request with no
``X-Agent-Id`` header into the bare client id ``"browser"``, and
``proposals.actor_kind("browser")`` is ``"human"`` — so before this rule an
agent could approve its own instructions by *dropping a header*. The gate now
demands ``browser:<id>`` (what ``frontend/js/api.js`` mints and stores) or
``user:<id>`` (a hosted principal, including the composed
``user:x/browser:y``); ``browser``, ``chat``, ``chat:<s>``, ``mcp``,
``agent:*`` and ``local`` are all refused. The gate runs *before* the name
check, so a non-human learns nothing about which skills exist.

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


#: The two explicit principals a person can arrive as: the browser's minted
#: `browser:<8 hex>` and hosted mode's `user:<name>` (bare or composed with a
#: browser id). The bare `browser` fallback deliberately does NOT match.
_PRINCIPAL_PREFIXES = ("browser:", "user:")


def _is_human(client: str | None = None) -> bool:
    """Is the calling client a person we can *name*?

    Two conditions, and both are load-bearing: ``actor_kind`` is the house
    definition of "human" (and the only place PRD-005a's hosted principals are
    classified), and the explicit-principal test is what stops the header-less
    fallback id from inheriting that classification.

    **In local mode this is a consent gate, not a security boundary.** The
    server takes ``X-Agent-Id`` unvalidated, so any local process can send
    ``browser:deadbeef`` and approve a skill; the gate raises the bar from
    "omit a header" to "impersonate the browser on purpose", which is the
    same bar every other local-mode human act has (and a local part script
    already runs as the server user — see the hosted-core trap). In hosted
    mode the principal is the authenticated session's, which is the boundary.
    """
    cid = locks.current_client_id() if client is None else client
    cid = cid or ""
    if actor_kind(cid) != "human":
        return False
    return any(cid.startswith(p) and cid[len(p):].strip()
               for p in _PRINCIPAL_PREFIXES)


def _require_human(action: str) -> None:
    client = locks.current_client_id()
    if not _is_human(client):
        raise AuthzError(
            f"only a human can {action}: a skill is agent instructions, so no "
            f"agent surface may approve one",
            {"client": client, "hint": "do this from the Skills panel; an "
                                       "agent id, or a request with no "
                                       "X-Agent-Id header, is not a person"},
        )


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{proj}/skills")
    def list_skills(proj: str):
        # `path_of` first, and it is the 404: the library reads an absent
        # `skills/` directory as an empty layer, so without this a typo'd
        # project would answer with the core list and an empty trust document.
        service.store.path_of(proj)
        return {"skills": service.skills.index(proj),
                "hidden": service.skills.hidden(proj),
                "trust": service.skills.trust_state(proj)}

    @router.get("/projects/{proj}/skills/{name}")
    def get_skill(proj: str, name: str, asset: str | None = None):
        skill = _skill_name(name)
        if _is_human():
            # The review read. Not the tool: no trust check, no audit event,
            # no chat-engine bookkeeping — a person is reading, not loading.
            service.store.path_of(proj)
            return service.skills.load(skill, proj, asset,
                                       enforce_trust=False)
        args = {"project": proj, "name": skill}
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
