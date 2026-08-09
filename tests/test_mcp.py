"""End-to-end MCP test: real uvicorn server + 'agentcad mcp' over stdio.

A real AgentCAD server is started as a subprocess on a portctl-allocated port
(service mcp-test, instance test; falls back to 8634) with a temp projects
dir. The MCP stdio server is then spawned via ``uv run agentcad mcp`` with
AGENTCAD_URL pointing at it, and driven with the mcp package's stdio client:
handshake, list_tools (17 tools), a JSON round-trip, and a broken-script
update whose error comes back as tool-result content.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
FALLBACK_PORT = 8634
PORTCTL_ARGS = ["-service", "mcp-test", "-instance", "test"]
SERVER_STARTUP_TIMEOUT_S = 120


def _allocate_port() -> tuple[int, bool]:
    if shutil.which("portctl") is None:
        return FALLBACK_PORT, False
    try:
        out = subprocess.run(
            ["portctl", "allocate", *PORTCTL_ARGS],
            capture_output=True, text=True, timeout=10,
        )
        match = re.search(r"[Aa]llocated port (\d+)", out.stdout + out.stderr)
        if match:
            return int(match.group(1)), True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return FALLBACK_PORT, False


def _release_port() -> None:
    try:
        subprocess.run(
            ["portctl", "release", *PORTCTL_ARGS],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    port, allocated = _allocate_port()
    projects_dir = tmp_path_factory.mktemp("mcp_projects")
    log_path = tmp_path_factory.mktemp("mcp_logs") / "server.log"
    base = f"http://127.0.0.1:{port}"
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            [
                "uv", "run", "agentcad", "serve", "--no-open",
                "--port", str(port), "--projects-dir", str(projects_dir),
            ],
            cwd=REPO_ROOT, stdout=log, stderr=log,
        )
    try:
        deadline = time.monotonic() + SERVER_STARTUP_TIMEOUT_S
        healthy = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = log_path.read_text(errors="replace")[-2000:]
                raise RuntimeError(
                    f"server exited early (code {proc.returncode}):\n{tail}"
                )
            try:
                if httpx.get(f"{base}/api/health", timeout=1.0).status_code == 200:
                    healthy = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        if not healthy:
            tail = log_path.read_text(errors="replace")[-2000:]
            raise RuntimeError(f"server never became healthy on {base}:\n{tail}")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if allocated:
            _release_port()


@pytest.mark.timeout(600)
def test_mcp_stdio_end_to_end(live_server):
    params = StdioServerParameters(
        command="uv",
        args=["run", "agentcad", "mcp"],
        cwd=str(REPO_ROOT),
        env={**os.environ, "AGENTCAD_URL": live_server},
    )

    async def main():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Tool list mirrors the registry 1:1.
                listed = await session.list_tools()
                names = [t.name for t in listed.tools]
                assert len(names) >= 25, names  # 17 core + v2 packs
                for expected in ("list_projects", "create_part",
                                 "update_part_script", "part_template",
                                 "import_cad_file", "generate_drawing"):
                    assert expected in names
                template = next(t for t in listed.tools if t.name == "part_template")
                assert template.input_schema.get("type") == "object"

                # list_projects returns JSON text content.
                result = await session.call_tool("list_projects", {})
                payload = json.loads(result.content[0].text)
                assert "projects" in payload

                # Create a project + part (real kernel build of the template).
                result = await session.call_tool(
                    "create_project", {"name": "mcptest"},
                    read_timeout_seconds=60,
                )
                payload = json.loads(result.content[0].text)
                assert payload.get("name") == "mcptest"

                result = await session.call_tool(
                    "create_part", {"project": "mcptest", "part_id": "widget"},
                    read_timeout_seconds=300,
                )
                payload = json.loads(result.content[0].text)
                assert payload.get("id") == "widget"

                # render_view: image content plus JSON text without the base64.
                result = await session.call_tool(
                    "render_view",
                    {"project": "mcptest", "part_id": "widget",
                     "width": 320, "height": 240},
                    read_timeout_seconds=300,
                )
                kinds = [c.type for c in result.content]
                assert kinds == ["image", "text"], kinds
                image = result.content[0]
                assert image.mime_type == "image/png"
                assert base64.b64decode(image.data)[:8] == b"\x89PNG\r\n\x1a\n"
                payload = json.loads(result.content[1].text)
                assert "png_base64" not in payload
                assert payload["view"] == "iso"
                assert payload["width"] == 320

                # Broken script: the error arrives as readable tool content.
                result = await session.call_tool(
                    "update_part_script",
                    {
                        "project": "mcptest",
                        "part_id": "widget",
                        "script": "this is ( not valid python",
                    },
                    read_timeout_seconds=300,
                )
                text = result.content[0].text
                assert "error" in text
                payload = json.loads(text)
                assert payload["ok"] is False
                assert payload["error"]["type"] == "script_error"

    asyncio.run(main())
