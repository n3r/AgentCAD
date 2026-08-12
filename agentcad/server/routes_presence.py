"""Presence routes: who is on this project, over HTTP (PRD-008 slice 6).

    POST /api/projects/{proj}/presence  {part_id?, surface?, label?, claim?,
                                         leave?}  -> {you, clients, claims,
                                                      ttl_s, heartbeat_s}
    GET  /api/projects/{proj}/presence            -> the same payload, and
                                                     registers nobody
    POST /api/projects/{proj}/claims/override  {part} -> {armed_until, claim}

The heartbeat is also where a **claim** is taken: ``claim: true`` says "this
client's editor buffer is dirty, or a control is being dragged", and
*viewing* never claims. Everything else about claims — the precedence rule,
the human-vs-human restriction, the turn-holder exemption — lives in
``core/locks.py`` and ``core/presence.py``; this pack only carries them in.

Why HTTP and not the WebSocket the PRD's FR9 imagines: ``/ws`` is in
``server/app.py``, a core this feature may not edit; it carries no client
identity, because ``set_client_id`` is HTTP middleware; and its Host guard is
HTTP middleware too — a route pack cannot see ``create_app``'s
``allowed_hosts``, so it could not reproduce that guard correctly. The
substance of FR9 (report focus, see others) is met; the transport is the
reviewed one. See design Decision 13.

The identity is **the request's** — the ``X-Agent-Id`` the app's middleware
bound — and never an argument, with exactly one documented exception: a
``pagehide`` beacon. ``navigator.sendBeacon`` cannot set headers, so the leave
carries ``client_id`` in its body. That grants nobody anything: the header is
itself unvalidated and self-asserted (identity here is bookkeeping, not
authentication), and the worst a forged leave can do is drop a roster row that
the sender's next 15-second heartbeat puts straight back — and that the TTL
would have dropped within 45 s regardless. The stated blast radius is the
*whole* blast radius: a leave touches the roster and nothing else. It
deliberately does **not** release the leaver's claims, because that would make
a forged beacon a way to disarm the protection another human is editing under,
and a claim already expires on its own in 90 s. Nothing else on this router
reads an identity from a body.

The response is the mechanism. Every call, including a throttled one, answers
with the whole roster, so a client that misses every ``presence_changed``
converges within one heartbeat. That is also why over-rate calls are HTTP 200
with ``throttled: true`` rather than a 429: a heartbeat must never surface as
an error in a status indicator.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core import locks
from ..core.model import ValidationError
from ..core.presence import (
    TokenBucket,
    ensure_claim_guard,
    ensure_claims,
    ensure_presence,
    publish_claim,
    sync_claim,
)


def _beacon_identity(body: dict) -> str | None:
    """The ``client_id`` a leaving beacon named, if it looks like one at all.

    Bounded and printable because it is echoed to other clients' toolbars; not
    otherwise validated, because the header it stands in for is not either.
    """
    found = body.get("client_id")
    if not isinstance(found, str):
        return None
    found = found.strip()
    return found[:64] if found and found.isprintable() else None


async def _json(request: Request) -> dict:
    """The body, or ``{}``. Read the BYTES, not the header: ``sendBeacon``
    posts without a ``content-length`` a proxy is obliged to preserve, and a
    ``pagehide`` beacon that read as "no body" would never say ``leave``."""
    if not await request.body():
        return {}
    body = await request.json()
    return body if isinstance(body, dict) else {}


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    # Installed here, not from a tool pack: presence has no tools, and route
    # packs are mounted after every tool pack has run — which is the whole
    # reason the claim guard can only be installed lazily (``tools_versioning``
    # REPLACES ``write_guard`` after anything at ``c`` could have wrapped it).
    presence = ensure_presence(service)
    ensure_claims(service)
    ensure_claim_guard(service)
    throttle = TokenBucket()

    def _where(proj: str) -> str:
        """The branch-aware key everything else in the process locks on. Also
        the project's existence check — an unknown project is a 404 from the
        store, before any roster work."""
        service.store.canonical_path_of(proj)
        return service.store.lock_key(proj)

    @router.get("/projects/{proj}/presence")
    def get_presence(proj: str):
        return presence.payload(_where(proj), proj, locks.current_client_id())

    @router.post("/projects/{proj}/presence")
    async def heartbeat(proj: str, request: Request):
        body = await _json(request)
        key = _where(proj)
        who = locks.current_client_id()

        if body.get("leave"):
            # The pagehide beacon (see the module docstring for why this one
            # body may name its own identity). Publishing on the way out is
            # what makes an avatar disappear at once rather than at the TTL.
            #
            # A ROSTER ROW ONLY. This used to release every claim the named
            # identity held, which made a forged beacon a way to switch off
            # the one protection a human editing a part is relying on — a far
            # bigger blast radius than the "drop a row the next heartbeat puts
            # back" this router's docstring promises. A claim is soft and
            # expires on its own 90-second TTL; waiting that out is the
            # designed behaviour, and it is the only one a self-asserted
            # identity can be trusted with.
            leaving = _beacon_identity(body) or who
            if presence.leave(key, leaving):
                presence.publish(key, proj)
            return presence.payload(key, proj, who)

        if not throttle.take(who):
            return presence.payload(key, proj, who, throttled=True)

        entry, changed = presence.touch(
            key, who, project=proj,
            part_id=body.get("part_id"),
            surface=body.get("surface"),
            label=body.get("label"),
        )
        # A claims entry point, so the guard is (re)installed here too: a
        # registry rebuilt after the app was built must not disarm it.
        ensure_claim_guard(service)
        # The part the REGISTRY normalized, not the raw body: a claim is keyed
        # by part id, and it must be the same string presence just echoed to
        # every other client.
        claimed = sync_claim(service, key, proj, who, entry["part_id"],
                             bool(body.get("claim")))
        if changed or claimed:
            presence.publish(key, proj)
        return presence.payload(key, proj, who)

    @router.post("/projects/{proj}/claims/override")
    async def override_claim(proj: str, request: Request):
        """Arm a single-use, 30-second override for this identity and part.

        Out of band because it has to be: the two part-write routes
        (``PUT …/parts/{id}`` and ``PATCH …/parts/{id}/params``) live in
        ``app.py``, a core this feature may not edit, so the override cannot
        ride on the write itself. The conflict dialog calls this and retries
        once. Arming publishes ``claim_changed`` with ``overridden_by``, so
        taking somebody's part is on the record before the write lands.
        """
        body = await _json(request)
        key = _where(proj)
        part = body.get("part")
        if not isinstance(part, str) or not part.strip():
            raise ValidationError("claims/override needs a part",
                                  {"got": body.get("part")})
        part = part.strip()
        who = locks.current_client_id()
        ensure_claim_guard(service)
        claims = ensure_claims(service)
        armed = claims.arm_override(key, part, who)
        publish_claim(service, proj, part, claims.get(key, part),
                      overridden_by=who)
        return {"part": part, "armed_until": armed["armed_until"],
                "claim": claims.get(key, part)}

    return router
