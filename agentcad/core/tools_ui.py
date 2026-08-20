"""Tool pack: ``ui_open`` — ask the connected browser(s) to open a view.

The agent-side half of PRD-026's workbench shell (design Decision 7). An agent
that has just created a part, or that wants a human to confirm something, can
put the right panel or dialog in front of them instead of describing where to
click.

Three properties are the whole contract, and each is a deliberate limit:

**It is a broadcast, not a message.** The event bus has no per-client routing
— every ``/ws`` client receives every event — and building one is PRD-025/005
scope. So ``ui_open`` reaches *every* connected browser, and the shell shows
"opened by agent" attribution rather than pretending the agent addressed one
person.

**It is capability-honest.** Publishing an event says nothing about whether
anything received it, so the result carries ``delivered_to`` — the number of
subscribers the publish actually reached — and, when that is zero, a note
saying in words that no browser is connected and nothing will open. This is
the ``tools_history`` ``available: False`` precedent: an agent must be able to
tell "done" from "there was nobody there", and a bare ``{"ok": true}`` cannot.

**It is rate limited.** A tool that pops UI open is a tool that can be used to
make the app unusable. One token bucket per *process* (10 opens / 10 s, so a
short burst is fine and a loop is not) refuses with the registry's
``validation_error`` envelope naming the limit and carrying ``retry_after_s``.
Per process rather than per registry or per caller because the thing being
protected is the browser, and there is one of it.

Nothing here touches the kernel, the store, or any seam a later pack installs,
so the pack registers unconditionally and reads ``service.bus`` at call time.
It registers **no gate provider** — see the ``tools_run_checks`` load-order
trap in ``AGENTS.md``: this module sorts after ``tools_proposals``, whose
``service.gate_providers = []`` is unconditional.
"""

from __future__ import annotations

import json
import re
import threading
import time

from .model import ValidationError
from .tools import Tool, schema

#: A view id is the shell's own vocabulary (`part-settings`, `export`), not a
#: path or a name — deliberately narrower than `ID_RE` (hyphens, no
#: underscores) because that is what `frontend/js/shell/dialogs.js` registers.
VIEW_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")

#: `args` is forwarded verbatim to every connected browser and is not a
#: geometry payload — 4 KiB is generous for "which part, which tab".
MAX_ARGS_BYTES = 4096

BUCKET_CAPACITY = 10
BUCKET_WINDOW_S = 10.0

#: Injectable clock. `time.monotonic` (never `time.time`): a clock step
#: backwards must not hand out a free bucket. Read through the module global
#: at every call so a test can substitute a fake one.
_now = time.monotonic


class _Bucket:
    """A token bucket: *capacity* tokens, refilled at capacity/window per
    second, so ten opens may burst and the eleventh waits one second."""

    def __init__(self, capacity: int, window_s: float) -> None:
        self.capacity = float(capacity)
        self.rate = capacity / window_s
        self.tokens = float(capacity)
        self.updated = _now()
        self._lock = threading.Lock()

    def take(self) -> float | None:
        """``None`` when a token was taken, else the seconds until the next
        one. The server is threaded, so the read-modify-write is locked."""
        with self._lock:
            now = _now()
            # `max(0.0, ...)` guards a fake or stepped clock, not monotonic
            # itself: a negative elapsed must never *remove* tokens.
            elapsed = max(0.0, now - self.updated)
            self.updated = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return None
            return round((1.0 - self.tokens) / self.rate, 3)


_bucket = _Bucket(BUCKET_CAPACITY, BUCKET_WINDOW_S)


def _reset_bucket() -> None:
    """Refill the process-wide bucket. Tests only — a full bucket and a fresh
    reading of `_now` (which a test may have just replaced)."""
    global _bucket
    _bucket = _Bucket(BUCKET_CAPACITY, BUCKET_WINDOW_S)


def register(registry, service) -> None:
    def ui_open(view: str, args: dict | None = None) -> dict:
        if not isinstance(view, str) or not VIEW_RE.match(view):
            raise ValidationError(
                f"invalid view {view!r}: must match {VIEW_RE.pattern}",
                {"view": view if isinstance(view, str) else None})
        payload = {} if args is None else args
        # The schema already declares `args` an object, so `registry.call`
        # refuses a list before this — but the handler is also called
        # directly (a library caller, the chat engine's own dispatch), and a
        # non-dict would reach every browser as a broadcast.
        if not isinstance(payload, dict):
            raise ValidationError(
                "args must be a JSON object",
                {"got": type(payload).__name__})
        try:
            encoded = json.dumps(payload)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"args must be JSON-serialisable: {exc}",
                                  {"got": type(payload).__name__}) from exc
        size = len(encoded.encode("utf-8"))
        if size > MAX_ARGS_BYTES:
            raise ValidationError(
                f"args is {size} bytes, over the {MAX_ARGS_BYTES} byte limit",
                {"bytes": size, "limit": MAX_ARGS_BYTES})

        # Last, so a refused open costs the caller a token only when it was
        # otherwise going to work — a malformed loop is answered by the
        # validation error, which is the more useful refusal.
        retry_after_s = _bucket.take()
        if retry_after_s is not None:
            raise ValidationError(
                f"ui_open rate limit: {BUCKET_CAPACITY} per "
                f"{BUCKET_WINDOW_S:g} s",
                {"retry_after_s": retry_after_s})

        # Counted BEFORE the publish, so `delivered_to` is the number of
        # subscribers the fan-out actually iterated.
        delivered_to = service.bus.subscriber_count()
        service.bus.publish({"type": "ui_open", "view": view,
                             "args": payload, "by": "agent"})
        note = ("no browser is connected; nothing will open"
                if delivered_to == 0
                else f"published to {delivered_to} connected client(s)")
        return {"ok": True, "view": view, "args": payload,
                "delivered_to": delivered_to, "note": note}

    registry.register(Tool(
        "ui_open",
        "Open a view in the connected browser(s): a dialog, a panel or a "
        "workbench view, named by the shell's own view id. This is a "
        "BROADCAST — every connected client receives it and shows 'opened by "
        "agent' attribution; there is no way to address one person. The "
        "result is capability-honest: 'delivered_to' is how many clients the "
        "publish reached, and 0 means no browser is connected and nothing "
        "will open (the call still succeeds). Rate limited to 10 opens per "
        "10 s per server; over the limit the refusal carries "
        "details.retry_after_s.",
        schema({
            "view": {"type": "string",
                     "description": "Shell view id, matching "
                                    "[a-z][a-z0-9-]{0,39} (e.g. "
                                    "'part-settings', 'export')"},
            "args": {"type": "object",
                     "description": "Arguments for the view, forwarded "
                                    "verbatim; JSON must be <= 4096 bytes"},
        }, ["view"]),
        ui_open,
    ))
