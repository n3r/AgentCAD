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


def _health_ok(base: str) -> bool:
    try:
        return httpx.get(f"{base}/api/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def _ensure_server(base: str) -> bool:
    """Return True once /api/health answers, auto-starting the server if needed."""
    if _health_ok(base):
        return True
    print(
        f"agentcad-mcp: no server at {base}; "
        "starting 'uv run agentcad serve --no-open' in the background",
        file=sys.stderr,
    )
    try:
        subprocess.Popen(
            ["uv", "run", "agentcad", "serve", "--no-open"],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
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


async def _serve(base: str) -> None:
    http = httpx.AsyncClient(base_url=base, timeout=REQUEST_TIMEOUT)

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
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text=json.dumps(payload, indent=2, default=str)
                )
            ]
        )

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
            "'uv run agentcad serve --no-open' (or set AGENTCAD_URL) and retry.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    asyncio.run(_serve(base))
