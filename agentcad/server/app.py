"""FastAPI application: REST + WebSocket + static frontend hosting.

Routes are thin wrappers over AgentCADService; the generic tool passthrough
(``/api/tools``) is what the MCP server proxies. Chat routes are registered
only when a chat engine is provided (see agentcad.agent.chat).
"""

from __future__ import annotations

import asyncio
import json
import queue
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import agentcad
from ..core.model import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from ..core.service import AgentCADService
from ..core.tools import ToolRegistry
from ..kernel import sandbox
from ..kernel.client import KernelError

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

_ERROR_STATUS = {
    NotFoundError: 404,
    ValidationError: 422,
    ConflictError: 409,
}


def _error_response(exc: AppError) -> JSONResponse:
    status = next(
        (code for cls, code in _ERROR_STATUS.items() if isinstance(exc, cls)), 400
    )
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "type": type(exc).__name__,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _hostname(host_header: str) -> str:
    """Host header without the port ([::1]:8630 -> [::1], 127.0.0.1:8630 -> 127.0.0.1)."""
    host = host_header.strip()
    if host.startswith("["):  # bracketed IPv6
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def _browser_request_allowed(headers, allowed_hosts: frozenset) -> tuple[bool, str]:
    """Same-origin policy for a localhost-only, unauthenticated API.

    1. Host must be a local name — defeats DNS rebinding (a rebound
       evil.com carries Host: evil.com).
    2. If the browser sent an Origin header it must exactly match our own
       origin — defeats cross-origin "simple request" CSRF against
       state-changing routes. Non-browser clients send no Origin and pass.
    """
    host = headers.get("host", "")
    if _hostname(host) not in allowed_hosts:
        return False, f"disallowed Host {host!r}"
    origin = headers.get("origin")
    if origin is not None and origin != f"http://{host}":
        return False, f"cross-origin request from {origin!r} rejected"
    return True, ""


def create_app(
    service: AgentCADService,
    registry: ToolRegistry,
    chat_engine=None,
    extra_allowed_hosts: frozenset | set = frozenset(),
) -> FastAPI:
    app = FastAPI(title="AgentCAD", version=agentcad.__version__)
    allowed_hosts = frozenset(LOCAL_HOSTNAMES) | frozenset(extra_allowed_hosts)

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        allowed, reason = _browser_request_allowed(request.headers, allowed_hosts)
        if not allowed:
            return JSONResponse(
                status_code=403,
                content={"error": {"type": "ForbiddenOrigin", "message": reason,
                                   "details": {}}},
            )
        return await call_next(request)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return _error_response(exc)

    @app.exception_handler(KernelError)
    async def handle_kernel_error(request: Request, exc: KernelError):
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "type": exc.type,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    # ---------------------------------------------------------------- meta

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": agentcad.__version__,
            "kernel": "ready" if service.kernel.alive else "starting",
            "chat_available": bool(chat_engine and chat_engine.available),
            # Reflects the ACTUAL kernel the service runs (the client decides
            # at construction and exposes .sandboxed), not a config recompute.
            "sandbox": sandbox.status(
                getattr(service.kernel, "sandboxed", False)
            ),
        }

    # ------------------------------------------------------------ projects

    @app.get("/api/projects")
    def list_projects():
        return {"projects": service.list_projects()}

    @app.post("/api/projects", status_code=201)
    async def create_project(request: Request):
        body = await request.json()
        return service.create_project(body.get("name", ""))

    @app.post("/api/projects/open")
    async def open_project(request: Request):
        body = await request.json()
        return service.open_project(body.get("path", ""))

    @app.get("/api/projects/{proj}")
    def get_project(proj: str):
        return service.get_project(proj)

    # --------------------------------------------------------------- parts

    @app.post("/api/projects/{proj}/parts", status_code=201)
    async def create_part(proj: str, request: Request):
        body = await request.json()
        return service.create_part(
            proj,
            body.get("id", ""),
            body.get("label"),
            body.get("script"),
            body.get("material", "al6061"),
        )

    @app.get("/api/projects/{proj}/parts/{part_id}")
    def get_part(proj: str, part_id: str):
        return service.get_part(proj, part_id)

    @app.put("/api/projects/{proj}/parts/{part_id}")
    async def update_part(proj: str, part_id: str, request: Request):
        body = await request.json()
        return service.update_part(
            proj,
            part_id,
            script=body.get("script"),
            label=body.get("label"),
            material=body.get("material"),
        )

    @app.patch("/api/projects/{proj}/parts/{part_id}/params")
    async def set_params(proj: str, part_id: str, request: Request):
        body = await request.json()
        return service.set_params(proj, part_id, body)

    @app.delete("/api/projects/{proj}/parts/{part_id}")
    def delete_part(proj: str, part_id: str):
        service.delete_part(proj, part_id)
        return {"deleted": part_id}

    @app.get("/api/projects/{proj}/parts/{part_id}/metrics")
    def get_metrics(proj: str, part_id: str):
        return service.get_metrics(proj, part_id)

    @app.get("/api/projects/{proj}/parts/{part_id}/mesh")
    def get_mesh(proj: str, part_id: str):
        info = service.mesh_info(proj, part_id)
        return Response(
            content=info["path"].read_bytes(),
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Mesh-Key": info["key"]},
        )

    @app.post("/api/projects/{proj}/parts/{part_id}/export")
    async def export_part(proj: str, part_id: str, request: Request):
        body = await request.json()
        return service.export_part(
            proj, part_id, body.get("format", ""), body.get("tolerance", 0.05)
        )

    # ------------------------------------------------------------ assembly

    @app.get("/api/projects/{proj}/assembly")
    def get_assembly(proj: str):
        return service.get_assembly(proj)

    @app.put("/api/projects/{proj}/assembly")
    async def set_assembly(proj: str, request: Request):
        body = await request.json()
        return service.set_assembly(proj, body.get("instances", []))

    @app.post("/api/projects/{proj}/assembly/interference")
    async def check_interference(proj: str, request: Request):
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        return service.check_interference(proj, body.get("min_volume", 0.001))

    @app.post("/api/projects/{proj}/export")
    async def export_assembly(proj: str, request: Request):
        body = await request.json()
        return service.export_assembly(proj, body.get("format", ""))

    # --------------------------------------------------------------- tools

    @app.get("/api/tools")
    def list_tools():
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in registry.list()
            ]
        }

    @app.post("/api/tools/{name}")
    async def call_tool(name: str, request: Request):
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        return registry.call(name, body)

    # ---------------------------------------------------------------- chat

    if chat_engine is not None:

        @app.post("/api/chat")
        async def chat(request: Request):
            body = await request.json()
            return await chat_engine.start_turn(
                body.get("project", ""), body.get("message", "")
            )

        @app.get("/api/chat/history")
        def chat_history(project: str):
            return {"messages": chat_engine.history(project)}

        @app.delete("/api/chat/history")
        def clear_chat_history(project: str):
            chat_engine.clear_history(project)
            return {"cleared": True}

    # ------------------------------------------------------------------ ws

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        # Browsers do not apply same-origin policy to WebSockets: enforce the
        # same host/origin rules as HTTP before accepting the stream.
        allowed, _reason = _browser_request_allowed(ws.headers, allowed_hosts)
        if not allowed:
            await ws.close(code=1008)
            return
        await ws.accept()
        q = service.bus.subscribe()
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    event = await loop.run_in_executor(None, q.get, True, 20.0)
                except queue.Empty:
                    await ws.send_text(json.dumps({"type": "ping"}))
                    continue
                await ws.send_text(json.dumps(event))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            service.bus.unsubscribe(q)

    # ---------------------------------------------------------- route packs

    # Extension point: each agentcad/server/routes_*.py exports either an
    # APIRouter named `router`, or `build_router(service, registry) -> APIRouter`.
    _mount_route_packs(app, service, registry)

    # -------------------------------------------------------------- static

    if FRONTEND_DIR.is_dir():
        @app.get("/")
        def index():
            return FileResponse(FRONTEND_DIR / "index.html")

        for sub in ("js", "css", "vendor"):
            path = FRONTEND_DIR / sub
            if path.is_dir():
                app.mount(f"/{sub}", StaticFiles(directory=path), name=sub)

    return app


def _mount_route_packs(app: FastAPI, service: AgentCADService, registry: ToolRegistry) -> None:
    import importlib
    import pkgutil

    import agentcad.server as server_pkg

    for info in pkgutil.iter_modules(server_pkg.__path__):
        if not info.name.startswith("routes_"):
            continue
        module = importlib.import_module(f"agentcad.server.{info.name}")
        builder = getattr(module, "build_router", None)
        router = builder(service, registry) if callable(builder) else getattr(module, "router", None)
        if router is not None:
            app.include_router(router, prefix="/api")
