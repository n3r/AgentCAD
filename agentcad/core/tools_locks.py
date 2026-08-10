"""Tool pack: multi-user turn locks (advisory, enforced on writes).

Etiquette for agents sharing a project: call ``acquire_turn`` before a batch
of edits and ``release_turn`` when done. While you hold the turn, writes by
every other client fail with a ``conflict_error`` naming you as the holder;
your own writes pass. Locks expire automatically after their TTL so a crashed
agent can never wedge the project.

Identity: HTTP callers are identified by the ``X-Agent-Id`` request header
(agents SHOULD send one; the MCP proxy sends ``AGENTCAD_AGENT_ID`` or "mcp");
callers without the header appear as "browser", and the built-in chat agent
is "chat".
"""

from __future__ import annotations

from . import locks
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}


def register(registry, service) -> None:
    def _scope(project: str) -> dict:
        """Which lock the event is about: the turn is per-branch, so a client
        on another branch must not reflect it. ``key`` is the lock's identity
        (the project name on the default branch, the working tree elsewhere)
        and ``branch`` names it the way a UI can compare."""
        branches = getattr(service, "branches", None)
        branch = None
        if branches is not None:
            try:
                branch = branches.current(project)
            except Exception:  # noqa: BLE001 — an event must never raise
                branch = None
        return {"key": service.store.lock_key(project), "branch": branch}

    def acquire_turn(project: str, ttl_s: float = locks.DEFAULT_TTL_S) -> dict:
        service.store.manifest(project)  # existence check -> notfound_error
        holder = locks.current_client_id()
        # lock_key is the project name unless branching is active, where it is
        # the caller's working tree: the turn is per-branch, not per-project.
        info = service.turnlock.acquire(
            service.store.lock_key(project), holder, ttl_s
        )
        service.bus.publish(
            {"type": "lock_changed", "project": project,
             **_scope(project), "holder": info["holder"]}
        )
        return {**info, "you": holder}

    def release_turn(project: str) -> dict:
        holder = locks.current_client_id()
        result = service.turnlock.release(
            service.store.lock_key(project), holder
        )
        service.bus.publish(
            {"type": "lock_changed", "project": project,
             **_scope(project), "holder": None}
        )
        return result

    def get_turn(project: str) -> dict:
        return {
            "lock": service.turnlock.get(service.store.lock_key(project)),
            "you": locks.current_client_id(),
        }

    registry.register(Tool(
        "acquire_turn",
        "Take (or refresh) the editing turn on a project before a batch of "
        "edits. While you hold it, writes by every other client fail with a "
        "conflict_error naming you; your own writes succeed. The lock expires "
        "after ttl_s seconds (default 120, clamped to 5..3600) — re-acquire to "
        "refresh, and call release_turn as soon as you are done. If someone "
        "else holds the turn you get a conflict_error with the holder and "
        "expiry. HTTP callers are identified by their X-Agent-Id header "
        "(send a stable one); no header means the shared 'browser' identity.",
        schema(
            {
                "project": _PROJ,
                "ttl_s": {
                    "type": "number",
                    "description": "Lock lifetime in seconds "
                                   "(default 120, clamped to 5..3600)",
                },
            },
            ["project"],
        ),
        acquire_turn,
    ))
    registry.register(Tool(
        "release_turn",
        "Release the editing turn you hold on a project so others can write "
        "again. Call this as soon as your batch of edits is done. Returns "
        "{released: false} if nothing was held; releasing a turn held by "
        "someone else is a conflict_error.",
        schema({"project": _PROJ}, ["project"]),
        release_turn,
    ))
    registry.register(Tool(
        "get_turn",
        "Show who currently holds the editing turn on a project (lock is "
        "null when free) plus your own client identity ('you'). Use it to "
        "decide whether to wait, or to explain a conflict_error to the user.",
        schema({"project": _PROJ}, ["project"]),
        get_turn,
    ))
