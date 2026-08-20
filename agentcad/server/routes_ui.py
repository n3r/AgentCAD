"""``POST /api/ui/events`` — the browser's UX telemetry lane (PRD-026,
design Decision 7).

The workbench shell posts here fire-and-forget when a dialog opens, a dialog
is submitted, or the command palette runs something; the server re-publishes
the event on the bus so agents and other connected browsers can see what a
human just did. The shell never blocks on the response and swallows network
errors — this route exists to be *ignorable*.

Because it accepts input from a page and re-broadcasts it to every connected
client, the surface is an allow-list and nothing else: three event types,
three optional string keys, 80 characters each. Two fields are set here and
can never be forged by the body — ``by: "browser"`` (an agent's UI event is
``ui_open``, published by `core/tools_ui.py`, and the two must stay
distinguishable at the receiver) and ``client``, which is the request's own
``X-Agent-Id`` header, the same identity the turn lock and the presence
roster use. Anything else in the body is a 422, not a silently dropped key:
a shell that starts sending a fourth field should find out.

Member-only in hosted mode by default — it is not in `security.PUBLIC_PATHS`
and must not be added there (`tests/test_hosted_surface.py` asserts the
anonymous surface by set equality).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.model import ValidationError
# The strict object-body reader: a body that parses to anything other than an
# object is a refusal, never an empty one (`routes_configs._json`'s docstring
# has the history). `app.py:38` imports it the same way.
from .routes_configs import _json as _object_body

#: The shell's UX events. `ui_open` is deliberately NOT here: that direction
#: is the agent's, and it arrives through the tool registry.
EVENT_TYPES = ("dialog_opened", "dialog_submitted", "palette_executed")

#: The only keys a browser may contribute, all optional, all strings.
PAYLOAD_KEYS = ("view", "action", "tool")

MAX_VALUE_LEN = 80


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/ui/events")
    async def ui_event(request: Request):
        body = await _object_body(request)
        kind = body.get("type")
        if kind not in EVENT_TYPES:
            raise ValidationError(
                f"type must be one of {', '.join(EVENT_TYPES)}",
                {"got": kind if isinstance(kind, str) else None})
        event = {"type": kind}
        for key, value in body.items():
            if key == "type":
                continue
            if key not in PAYLOAD_KEYS:
                raise ValidationError(
                    f"unexpected key {key!r}",
                    {"allowed": list(PAYLOAD_KEYS)})
            if not isinstance(value, str):
                raise ValidationError(
                    f"{key} must be a string",
                    {"got": type(value).__name__})
            if len(value) > MAX_VALUE_LEN:
                raise ValidationError(
                    f"{key} is {len(value)} characters, over the "
                    f"{MAX_VALUE_LEN} character limit",
                    {"key": key, "limit": MAX_VALUE_LEN})
            event[key] = value
        # Set last and unconditionally: the body cannot reach either (an
        # attempt is already a 422 above, as an unexpected key).
        event["by"] = "browser"
        event["client"] = request.headers.get("X-Agent-Id")
        service.bus.publish(event)
        return {"ok": True}

    return router
