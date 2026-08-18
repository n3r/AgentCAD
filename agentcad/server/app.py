"""FastAPI application: REST + WebSocket + static frontend hosting.

Routes are thin wrappers over AgentCADService; the generic tool passthrough
(``/api/tools``) is what the MCP server proxies. Chat routes are registered
only when a chat engine is provided (see agentcad.agent.chat).
"""

from __future__ import annotations

import asyncio
import json
import queue

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import agentcad
from .._resources import resource_root
from ..core.locks import set_client_id
from ..core.model import (
    AppError,
    AuthError,
    AuthzError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from ..core.service import AgentCADService
from ..core.tools import ToolRegistry
# The strict body reader the configuration routes already use: it reads the
# BYTES (not `content-length`) and refuses a non-object body, which every route
# below used to dereference with `.get(...)` and turn into a 500.
from .routes_configs import _json as _object_body
from . import security as security_module
from ..kernel import sandbox
from ..kernel.client import KernelError

FRONTEND_DIR = resource_root() / "frontend"

_ERROR_STATUS = {
    NotFoundError: 404,
    ValidationError: 422,
    ConflictError: 409,
    AuthError: 401,
    AuthzError: 403,
    RateLimitedError: 429,
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
        # Almost always empty; the anonymous catalog's misses use it to stay
        # cacheable (`core/model.AppError.headers`).
        headers=getattr(exc, "headers", None) or None,
    )


LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}
_WS_STOP = object()


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


async def _wait_for_websocket_disconnect(ws: WebSocket) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return


def _wake_websocket_event_waiter(q: queue.Queue) -> None:
    # A full queue belongs to a disconnected client, so discard one stale
    # event to guarantee that the blocked q.get can consume the sentinel.
    while True:
        try:
            q.put_nowait(_WS_STOP)
            return
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass


def create_app(
    service: AgentCADService,
    registry: ToolRegistry,
    chat_engine=None,
    extra_allowed_hosts: frozenset | set = frozenset(),
    security=None,
) -> FastAPI:
    """The app. *security* is a ``server.security.SecurityConfig`` or ``None``.

    **``None`` is not "auth disabled" — it is the same code path as before.**
    The middleware body below branches once, at the top, and everything after
    that branch is byte-identical to what shipped before PRD-005a, which is
    what makes "local mode is unchanged" a property of the diff instead of a
    test we have to keep passing (AC9).

    ``security`` is an explicit parameter rather than a discovered
    ``middleware_*`` pack for one reason: pack discovery fails **open**
    (``_mount_route_packs`` silently skips a module with no ``router``), and a
    security middleware that silently failed to load would leave the instance
    wide open with no signal. This is the one sanctioned core touch, which
    PRD-005's own technical approach pre-authorises; all of its logic lives in
    ``server/security.py`` so this diff stays reviewable at a glance.
    """
    app = FastAPI(title="AgentCAD", version=agentcad.__version__)
    allowed_hosts = frozenset(LOCAL_HOSTNAMES) | frozenset(extra_allowed_hosts)
    security_module.install(security)

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        if security is not None:
            denied = security_module.guard(security, request)
            response = denied if denied is not None else await call_next(request)
            # The one hardening header (founder decision 2026-08-18): the
            # authenticated surface is not frameable. `setdefault`, so the
            # `/embed/` page's own `frame-ancestors *` (set in its handler and
            # excluded by `response_headers`) is not clobbered. This is a
            # header, not a route — the anonymous-surface equality test is
            # untouched.
            for name, value in security_module.response_headers(
                    request.url.path).items():
                response.headers.setdefault(name, value)
            return response
        # --- unchanged local-mode path below this line ---
        allowed, reason = _browser_request_allowed(request.headers, allowed_hosts)
        if not allowed:
            return JSONResponse(
                status_code=403,
                content={"error": {"type": "ForbiddenOrigin", "message": reason,
                                   "details": {}}},
            )
        # Client identity for turn locking: agents send X-Agent-Id; anything
        # without the header (the browser UI, plain curl) is "browser". The
        # ContextVar set here reaches async endpoints via the task context and
        # sync endpoints via anyio's to_thread.run_sync context copy.
        set_client_id(request.headers.get("x-agent-id") or "browser")
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
        if security is not None and security_module.current_principal() is None:
            # FR21. Health is public so a load balancer and a browser opening
            # the sign-in page can reach it, but the full body names the
            # version, whether the kernel is up, whether chat is configured
            # and whether the worker is confined — a reconnaissance packet for
            # a stranger. The trimmed body is what "ok" needs to mean.
            return {"status": "ok", "mode": security.mode.name}
        return {
            **({"mode": security.mode.name} if security is not None else {}),
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
        if security is not None and security.mode.hosted:
            # FR19. This route registers ANY absolute path on the server as a
            # project. On a loopback bind it could only ever reach the
            # operator's own disk, which is what made it safe; on a hosted
            # instance it is "/etc as a project tree" for every member. The
            # refusal is here rather than in the guard because the guard is a
            # path allowlist, not a policy engine.
            raise AuthzError(
                "POST /api/projects/open is disabled in hosted mode: it "
                "registers an arbitrary filesystem path on the server as a "
                "project. Create one with POST /api/projects instead.",
                {"mode": security.mode.name},
            )
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
        body = await _object_body(request)
        return service.set_params(proj, part_id, body)

    @app.delete("/api/projects/{proj}/parts/{part_id}")
    def delete_part(proj: str, part_id: str):
        service.delete_part(proj, part_id)
        return {"deleted": part_id}

    @app.get("/api/projects/{proj}/parts/{part_id}/metrics")
    def get_metrics(proj: str, part_id: str):
        return service.get_metrics(proj, part_id)

    @app.get("/api/projects/{proj}/parts/{part_id}/mesh")
    def get_mesh(proj: str, part_id: str, lod: str | None = None):
        # ?lod=lod1 asks for the coarse preview tier; when the part has no
        # such tier the full-resolution buffer is served (X-Mesh-Lod: full),
        # so small parts cost the client zero extra requests.
        info = service.mesh_info(proj, part_id, lod=lod)
        return Response(
            content=info["path"].read_bytes(),
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Mesh-Key": info["key"],
                "X-Mesh-Lod": info.get("lod") or "full",
            },
        )

    @app.get("/api/projects/{proj}/parts/{part_id}/mesh/faces")
    def get_mesh_faces(proj: str, part_id: str):
        # Triangle->B-rep-face sidecar for the FULL-resolution mesh (one u32
        # per triangle, mesh face order). 404 when the build predates the
        # sidecar (stale cache entry) or the part is a reference import.
        info = service.mesh_info(proj, part_id)
        sidecar = info["path"].parent / f"{info['key']}.faces.u32"
        if not sidecar.is_file():
            raise NotFoundError(
                f"no face map for part {part_id!r} (rebuild required, or the "
                "part is an imported reference)"
            )
        return Response(
            content=sidecar.read_bytes(),
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Mesh-Key": info["key"],
            },
        )

    @app.post("/api/projects/{proj}/parts/{part_id}/export")
    async def export_part(proj: str, part_id: str, request: Request):
        body = await _object_body(request)
        return service.export_part(
            proj, part_id, body.get("format", ""), body.get("tolerance", 0.05),
            config=body.get("config"),
        )

    # ------------------------------------------------------------ assembly

    @app.get("/api/projects/{proj}/assembly")
    def get_assembly(proj: str):
        return service.get_assembly(proj)

    @app.put("/api/projects/{proj}/assembly")
    async def set_assembly(proj: str, request: Request):
        body = await _object_body(request)
        # The key is REQUIRED for the reason the instance PATCH's `config` is:
        # this is a full-list REPLACE, so "no instances key" cannot mean
        # "nothing to change" — `body.get("instances", [])` would wipe the
        # assembly, and the strict body reader (which answers `{}` for a
        # genuinely absent body) is exactly what makes that reachable at 200.
        if "instances" not in body:
            raise ValidationError(
                'instances is required; send {"instances": []} to clear the '
                "assembly"
            )
        return service.set_assembly(proj, body["instances"])

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
            # session defaults to "main" (the browser dock's lane); the engine
            # validates the id ([a-z0-9_-]{1,32}) and raises 422 on a bad one.
            return await chat_engine.start_turn(
                body.get("project", ""),
                body.get("message", ""),
                session=body.get("session", "main"),
            )

        @app.get("/api/chat/history")
        def chat_history(project: str, session: str = "main"):
            return {
                "messages": chat_engine.history(project, session),
                "session": session,
            }

        @app.delete("/api/chat/history")
        def clear_chat_history(project: str, session: str = "main"):
            chat_engine.clear_history(project, session)
            return {"cleared": True, "session": session}

    # ------------------------------------------------------------------ ws

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        # Browsers do not apply same-origin policy to WebSockets: enforce the
        # same host/origin rules as HTTP before accepting the stream.
        if security is not None:
            if not security_module.guard_websocket(security, ws):
                await ws.close(code=1008)
                return
        else:
            allowed, _reason = _browser_request_allowed(ws.headers, allowed_hosts)
            if not allowed:
                await ws.close(code=1008)
                return
        await ws.accept()
        q = service.bus.subscribe()
        disconnect = asyncio.create_task(_wait_for_websocket_disconnect(ws))
        event_waiter = asyncio.create_task(asyncio.to_thread(q.get))
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {disconnect, event_waiter},
                    timeout=20.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect in done:
                    break
                if event_waiter not in done:
                    await ws.send_text(json.dumps({"type": "ping"}))
                    continue
                event = event_waiter.result()
                if event is _WS_STOP:
                    break
                await ws.send_text(json.dumps(event))
                event_waiter = asyncio.create_task(asyncio.to_thread(q.get))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            service.bus.unsubscribe(q)
            _wake_websocket_event_waiter(q)
            if not disconnect.done():
                disconnect.cancel()
            await asyncio.gather(disconnect, event_waiter, return_exceptions=True)

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
            # A pack may declare `PREFIX` to mount somewhere other than /api;
            # PRD-007's share links need `/s/<token>` at the root and the
            # extension point could not express it. The default is unchanged,
            # so the sixteen existing packs do not move.
            #
            # `security.is_public` is consulted with the full request path, so
            # a pack that moves to the root is still private — but be exact
            # about the limit of that: a pack declaring `PREFIX = "/api/public"`
            # or `"/js"` WOULD land inside `PUBLIC_PREFIXES` and be anonymously
            # reachable. No pack does, and none may; that is an invariant this
            # comment states and code review enforces, not one the mechanism
            # provides. `security.py` says a pack author cannot open the
            # anonymous surface *from a decorator* — this is the seam where
            # they could, and the reason the prefixes are a short literal list
            # in one file rather than a pattern. If a pack ever needs an
            # anonymous route, the entry goes in `PUBLIC_PATHS` /
            # `PUBLIC_PREFIXES` where `test_hosted_surface.py` counts it.
            app.include_router(router, prefix=getattr(module, "PREFIX", "/api"))
