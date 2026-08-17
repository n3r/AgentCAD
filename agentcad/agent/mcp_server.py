"""MCP stdio server: proxies the AgentCAD HTTP API for MCP clients.

Tools are mirrored 1:1 from ``GET /api/tools`` (the ToolRegistry, so this
surface cannot drift from the built-in chat agent), and each ``call_tool``
becomes ``POST /api/tools/{name}``. Results come back as JSON text content;
transport/HTTP failures are returned as tool results describing the problem,
never as MCP protocol errors, so agents can read and react to them.

If no AgentCAD server is reachable, ``uv run agentcad serve --no-open`` is
started in the background (cwd = repo root) and health is polled for up to
30 seconds before giving up with a clear message on stderr.

Register with Claude Code:

    claude mcp add agentcad -- uv --directory /path/to/cad_claude run agentcad mcp
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

import agentcad
from ..config import get_port

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STARTUP_TIMEOUT_S = 30.0
REQUEST_TIMEOUT = httpx.Timeout(600.0, connect=10.0)


def _base_url() -> str:
    override = os.environ.get("AGENTCAD_URL")
    if override:
        return override.rstrip("/")
    return f"http://127.0.0.1:{get_port()}"


def _client_headers() -> dict[str, str]:
    """Headers every proxied call carries.

    ``X-Agent-Id`` is the local turn-locking identity and is unchanged: in
    local mode it *is* who you are, and in hosted mode the guard demotes it to
    a ``<device>`` under the bearer's principal (PRD-005a, Decision 6).

    ``AGENTCAD_TOKEN`` adds the bearer that makes a hosted instance answer at
    all. A blank value is treated as absent rather than sent as ``Bearer `` —
    an empty credential is a 401 on a hosted box and pure noise on a local
    one.
    """
    headers = {"X-Agent-Id": os.environ.get("AGENTCAD_AGENT_ID", "mcp")}
    token = (os.environ.get("AGENTCAD_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _may_autostart(base: str | None = None) -> bool:
    """May an unreachable *base* be answered by spawning a local server?

    Only when it is loopback. Auto-starting a local instance because a
    **remote** one is unreachable is a footgun rather than a convenience: the
    agent would then quietly drive an empty machine that is not the one the
    operator configured, and every result would look real.

    The decision is on the parsed **host**, never a substring — a
    ``http://127.0.0.1.evil.example`` that passed a ``startswith`` check would
    be exactly the confusion this exists to stop.
    """
    from urllib.parse import urlsplit

    # The same set `check_bind` refuses a non-loopback bind against, imported
    # rather than re-spelled: two copies that drifted would let one say yes
    # while the other said no. `urlsplit` strips the IPv6 brackets, so `[::1]`
    # arrives here as `::1` — both spellings are in the set.
    from ..core.appmode import LOOPBACK_HOSTS

    try:
        host = urlsplit(base if base is not None else _base_url()).hostname
    except ValueError:                      # a malformed authority
        return False
    return bool(host) and host in LOOPBACK_HOSTS


def _health_ok(base: str) -> bool:
    try:
        return httpx.get(f"{base}/api/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def _server_spawn_argv() -> list[str]:
    """Command that starts the AgentCAD server in the background.

    A frozen (PyInstaller) bundle has no repo and no uv — it re-execs its own
    executable, whose entry point is the same `agentcad` CLI.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "serve", "--no-open"]
    return ["uv", "run", "agentcad", "serve", "--no-open"]


def _ensure_server(base: str) -> bool:
    """Return True once /api/health answers, auto-starting the server if needed."""
    if _health_ok(base):
        return True
    if not _may_autostart(base):
        print(
            f"agentcad-mcp: no server at {base}, and it is not a loopback "
            f"address — refusing to start a LOCAL server in its place. Start "
            f"or fix the instance at {base} (and set AGENTCAD_TOKEN if it runs "
            f"in hosted mode), or unset AGENTCAD_URL to use a local one.",
            file=sys.stderr,
        )
        return False
    argv = _server_spawn_argv()
    print(
        f"agentcad-mcp: no server at {base}; "
        f"starting {' '.join(argv)!r} in the background",
        file=sys.stderr,
    )
    try:
        # Detach the server from this MCP process: setsid on POSIX; a new
        # process group on Windows (start_new_session is POSIX-only).
        detach = (
            {"start_new_session": True}
            if os.name == "posix"
            else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        )
        subprocess.Popen(
            argv,
            # `uv run` needs the repo as cwd; the frozen executable is
            # location-independent (REPO_ROOT is meaningless inside a bundle).
            cwd=None if getattr(sys, "frozen", False) else REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detach,
        )
    except OSError as exc:
        print(f"agentcad-mcp: could not launch the server: {exc}", file=sys.stderr)
        return False
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if _health_ok(base):
            return True
        time.sleep(0.5)
    return False


def _tool_result(payload) -> types.CallToolResult:
    """Wrap a tool payload as MCP content. Plain-JSON tools stay a single
    TextContent blob; a result carrying ``png_base64`` (render_view) becomes
    an ImageContent plus the JSON without the base64, so agents actually see
    the image."""
    content: list = []
    if isinstance(payload, dict) and isinstance(payload.get("png_base64"), str):
        content.append(
            types.ImageContent(
                type="image", data=payload["png_base64"], mimeType="image/png"
            )
        )
        payload = {k: v for k, v in payload.items() if k != "png_base64"}
    content.append(
        types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))
    )
    return types.CallToolResult(content=content)


async def _serve(base: str) -> None:
    # Identity for the server's turn locking (acquire_turn/release_turn):
    # every proxied call carries X-Agent-Id so concurrent MCP agents can be
    # told apart. Set AGENTCAD_AGENT_ID to give each agent a stable name, and
    # AGENTCAD_TOKEN to authenticate against a hosted instance.
    http = httpx.AsyncClient(
        base_url=base,
        timeout=REQUEST_TIMEOUT,
        headers=_client_headers(),
    )

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        try:
            resp = await http.get("/api/tools")
            resp.raise_for_status()
            listed = resp.json()["tools"]
        except Exception as exc:  # noqa: BLE001 — surface an empty list, not a crash
            print(f"agentcad-mcp: failed to list tools: {exc}", file=sys.stderr)
            listed = []
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t["input_schema"],
                )
                for t in listed
            ]
        )

    async def on_call_tool(ctx, params) -> types.CallToolResult:
        args = params.arguments or {}
        try:
            resp = await http.post(f"/api/tools/{params.name}", json=args)
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — report as a tool result
            payload = {
                "error": {
                    "type": "transport_error",
                    "message": f"could not reach the AgentCAD server at {base}: {exc}",
                }
            }
        return _tool_result(payload)

    server = Server(
        "agentcad",
        version=agentcad.__version__,
        instructions=(
            "Agentic-first parametric CAD. Parts are build123d Python scripts; "
            "call part_template before writing your first script."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await http.aclose()


def run_mcp_server() -> None:
    base = _base_url()
    if not _ensure_server(base):
        print(
            f"agentcad-mcp: AgentCAD server unreachable at {base} after "
            f"{STARTUP_TIMEOUT_S:.0f}s. Start it manually with "
            f"{' '.join(_server_spawn_argv())!r} (or set AGENTCAD_URL) and retry.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    asyncio.run(_serve(base))
