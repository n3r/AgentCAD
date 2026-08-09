"""Built-in chat agent: an Anthropic Messages API tool-use loop.

The engine renders its tool list from the shared ToolRegistry (the same source
the MCP server uses), runs each turn as an asyncio background task, and
publishes progress on the EventBus so the UI can stream it over the WebSocket:

    {"type": "chat_delta",       "project": ..., "session": ..., "text": ...}
    {"type": "chat_tool_call",   "project": ..., "session": ..., "name": ...,
     "args": ...}
    {"type": "chat_tool_result", "project": ..., "session": ..., "name": ...,
     "ok": bool,
     "result": <JSON string, truncated to 2000 chars; any png_base64 is
                replaced with "<image omitted>">}
    {"type": "chat_done",        "project": ..., "session": ..., "turn_id": ...}

History is kept in memory per (project, session) for the server's lifetime.
A session is a named conversation lane within a project (id matching
``[a-z0-9_-]{1,32}``, default "main" — the browser dock's lane), so several
agents can hold independent conversations about one project. Turns are
serialized per (project, session) with an asyncio.Lock — the user message is
appended inside the lock — so concurrent POST /api/chat calls cannot
interleave one session's history and break the Messages API
tool_use/tool_result pairing; turns in *different* sessions run concurrently
(cross-session write consistency is the per-project turn lock's job, see
agentcad.core.locks). If a turn dies between issuing tool_use and recording
results, the history is repaired with synthetic error tool_results before the
lock is released.

Tool calls run under client identity "chat" for the default session and
"chat:<session>" otherwise, so a turn lock acquired by a session names it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from typing import Any, Callable

from ..core import locks
from ..core.model import ValidationError
from ..core.service import EventBus
from ..core.tools import ToolRegistry

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096
MAX_TOOL_CALLS_PER_TURN = 30
DEFAULT_SESSION = "main"
SESSION_ID_RE = re.compile(r"[a-z0-9_-]{1,32}")


def validate_session(session: Any) -> str:
    """Return the session id, or raise ValidationError (HTTP 422) on a bad one."""
    if not isinstance(session, str) or not SESSION_ID_RE.fullmatch(session):
        raise ValidationError(
            "chat session id must match [a-z0-9_-]{1,32}",
            {"session": repr(session)},
        )
    return session

SYSTEM_PROMPT = """\
You are the AgentCAD assistant — an agentic CAD copilot inside AgentCAD, a parametric
CAD system where every part is a Python script built with build123d (OpenCascade
B-rep). You operate on projects exclusively through the provided tools; the geometry
kernel validates every change and returns real metrics (volume mm^3, mass g, area,
bounding box, center of mass, validity).

Part-script contract (summary):
- A part script defines PARAMS, a dict of typed parameter specs
  ({"name": {"default": ..., "min": ..., "max": ..., "unit": ..., "description": ...}}
  with "default" required and the rest optional; an optional "type" of "number"
  (the default), "int", "bool", "enum" (requires "choices"), or "string" enables
  non-numeric parameters), and a function build(p) that
  receives the resolved parameter values as attributes (p.name) and returns a
  build123d Part, Solid, or Compound.
- Units are millimeters; angles are degrees. Numeric overrides outside min/max are
  clamped with warnings; wrong-typed values and unknown parameter names are errors.

Working rules:
- Call the part_template tool before writing your first part script in a
  conversation — it returns the full contract, a starter script, and a build123d
  cheat-sheet, so you do not have to guess the API.
- When a rebuild fails you receive a structured error with the traceback and failing
  line; read it, fix the script, and retry rather than giving up. The previous good
  geometry is kept while a script is broken.
- Prefer small verifiable steps: make one change, read the returned metrics or error,
  then continue.
- Always end your reply with a short summary of what changed (projects/parts created
  or updated, parameters set, exports written) — or say explicitly that nothing
  changed.
"""


class ChatUnavailable(ValidationError):
    """Raised when no Anthropic API key is configured (maps to HTTP 422)."""


def _render_tool_result(result: Any) -> tuple[str, str | list[dict]]:
    """(bus event JSON, tool_result content) for a tool result.

    Plain results stay a JSON string. A result carrying ``png_base64``
    (render_view) becomes a two-block content list — an image block plus the
    JSON without the base64 — so the model actually sees the render; the bus
    event replaces the base64 with a placeholder so the WebSocket stream never
    carries megabytes of image data.
    """
    if isinstance(result, dict) and isinstance(result.get("png_base64"), str):
        png_b64 = result["png_base64"]
        rest = {k: v for k, v in result.items() if k != "png_base64"}
        event_json = json.dumps(
            {**rest, "png_base64": "<image omitted>"}, default=str
        )
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": png_b64,
                },
            },
            {"type": "text", "text": json.dumps(rest, default=str)},
        ]
        return event_json, content
    result_json = json.dumps(result, default=str)
    return result_json, result_json


def _block_to_dict(block: Any) -> dict:
    """Convert a response content block (SDK object or dict) to a plain dict."""
    if isinstance(block, dict):
        return dict(block)
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    return {k: v for k, v in vars(block).items() if not k.startswith("_")}


class ChatEngine:
    def __init__(
        self,
        registry: ToolRegistry,
        bus: EventBus,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.registry = registry
        self.bus = bus
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client_factory = client_factory or self._default_client_factory
        self._client: Any = None
        # Both keyed by (project, session).
        self._history: dict[tuple[str, str], list[dict]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ---------------------------------------------------------- availability

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _default_client_factory(self) -> Any:
        import anthropic

        return anthropic.AsyncAnthropic(api_key=self.api_key)

    # --------------------------------------------------------------- history

    def history(self, project: str, session: str = DEFAULT_SESSION) -> list[dict]:
        validate_session(session)
        return list(self._history.get((project, session), []))

    def clear_history(self, project: str, session: str = DEFAULT_SESSION) -> None:
        validate_session(session)
        self._history.pop((project, session), None)

    # ----------------------------------------------------------------- turns

    async def start_turn(
        self, project: str, message: str, session: str = DEFAULT_SESSION
    ) -> dict:
        """Start a chat turn in the background; progress arrives on the bus."""
        if not self.available:
            raise ChatUnavailable(
                "chat is unavailable: no Anthropic API key configured",
                {"fix": "set the ANTHROPIC_API_KEY environment variable and restart, "
                        "or drive AgentCAD from Claude Code via 'agentcad mcp'"},
            )
        if not isinstance(message, str) or not message.strip():
            raise ValidationError("chat message must be a non-empty string")
        validate_session(session)
        turn_id = uuid.uuid4().hex[:12]
        task = asyncio.create_task(
            self._run_turn(project, session, turn_id, message)
        )
        self._tasks[turn_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(turn_id, None))
        return {"turn_id": turn_id}

    def _tool_definitions(self) -> list[dict]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self.registry.list()
        ]

    async def _run_turn(
        self, project: str, session: str, turn_id: str, message: str
    ) -> None:
        # Serialize per (project, session): one session's turns queue up,
        # while other sessions on the same project proceed concurrently.
        lock = self._locks.setdefault((project, session), asyncio.Lock())
        async with lock:
            await self._run_turn_locked(project, session, turn_id, message)

    async def _run_turn_locked(
        self, project: str, session: str, turn_id: str, message: str
    ) -> None:
        history = self._history.setdefault((project, session), [])
        try:
            if self._client is None:
                self._client = self._client_factory()
            loop = asyncio.get_running_loop()
            history.append({"role": "user", "content": message})
            tools = self._tool_definitions()
            calls = 0

            while True:
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=list(history),
                )
                blocks = [_block_to_dict(b) for b in response.content]
                history.append({"role": "assistant", "content": blocks})

                tool_uses = []
                for block in blocks:
                    if block.get("type") == "text" and block.get("text"):
                        self.bus.publish(
                            {
                                "type": "chat_delta",
                                "project": project,
                                "session": session,
                                "text": block["text"],
                            }
                        )
                    elif block.get("type") == "tool_use":
                        tool_uses.append(block)

                if not tool_uses:
                    break

                results = []
                for block in tool_uses:
                    name = block.get("name", "")
                    args = block.get("input") or {}
                    self.bus.publish(
                        {
                            "type": "chat_tool_call",
                            "project": project,
                            "session": session,
                            "name": name,
                            "args": args,
                        }
                    )
                    result = await loop.run_in_executor(
                        None, self._call_tool, name, args, session
                    )
                    ok = not (
                        isinstance(result, dict)
                        and (result.get("error") or result.get("ok") is False)
                    )
                    event_json, content = _render_tool_result(result)
                    self.bus.publish(
                        {
                            "type": "chat_tool_result",
                            "project": project,
                            "session": session,
                            "name": name,
                            "ok": ok,
                            "result": event_json[:2000],
                        }
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.get("id", ""),
                            "content": content,
                        }
                    )
                    calls += 1
                history.append({"role": "user", "content": results})

                if calls >= MAX_TOOL_CALLS_PER_TURN:
                    self.bus.publish(
                        {
                            "type": "chat_delta",
                            "project": project,
                            "session": session,
                            "text": f"[turn stopped: reached the limit of "
                                    f"{MAX_TOOL_CALLS_PER_TURN} tool calls]",
                        }
                    )
                    break
        except Exception as exc:  # noqa: BLE001 — a failed turn must not kill the server
            print(f"chat turn {turn_id} failed: {exc!r}", file=sys.stderr)
            self._repair_history(history)
            self.bus.publish(
                {
                    "type": "chat_delta",
                    "project": project,
                    "session": session,
                    "text": f"[chat error] {exc}",
                }
            )
        finally:
            self.bus.publish(
                {
                    "type": "chat_done",
                    "project": project,
                    "session": session,
                    "turn_id": turn_id,
                }
            )

    def _call_tool(self, name: str, args: dict, session: str = DEFAULT_SESSION):
        """Run one registry call under the chat identity (turn locking).

        The default session keeps the plain "chat" identity; any other session
        is stamped "chat:<session>" so a turn lock it acquires names the lane
        that holds it. Executor threads do not inherit the event-loop task's
        contextvars, and a reused worker thread keeps whatever its ambient
        context last held — so the identity is set explicitly at the start of
        every call rather than relying on per-work-item context isolation.
        """
        cid = "chat" if session == DEFAULT_SESSION else f"chat:{session}"
        locks.set_client_id(cid)
        return self.registry.call(name, args)

    @staticmethod
    def _repair_history(history: list[dict]) -> None:
        """Keep the transcript valid: every assistant tool_use must be followed
        by matching tool_results. Called when a turn dies mid-loop."""
        if not history or history[-1].get("role") != "assistant":
            return
        content = history[-1].get("content")
        if not isinstance(content, list):
            return
        dangling = [
            b for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if not dangling:
            return
        history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": b.get("id", ""),
                        "content": "[tool execution interrupted by an error]",
                        "is_error": True,
                    }
                    for b in dangling
                ],
            }
        )
