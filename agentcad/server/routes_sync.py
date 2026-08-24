"""Route pack: git smart-HTTP for a project's history repo (PRD-005 FR8/FR9).

Three endpoints, mounted at the root under ``/git`` (``PREFIX``), one project
each::

    GET  /git/{org}/{ws}/{proj}.git/info/refs?service=git-upload-pack
    POST /git/{org}/{ws}/{proj}.git/git-upload-pack
    POST /git/{org}/{ws}/{proj}.git/git-receive-pack

**Only** those three: routing them explicitly keeps the dumb protocol off and
keeps project-name validation in Python, so nothing else under the GIT_DIR —
``config``, ``hooks/``, the review-thread store in ``.history/agentcad/`` — is
addressable at all.

Why ``git http-backend`` as a CGI child rather than ``git upload-pack
--stateless-rpc`` directly: the spike (§A7) proved the direct path silently
negotiates **protocol v0** against a git 2.50 client unless you forward
``Git-Protocol`` into the advertise call *and* emit the ``# service=``
pkt-line only for v0/v1 — two version-numbered wire details this project would
own forever, in exchange for avoiding one ``partition(b"\\r\\n\\r\\n")``.
Performance is a wash. ``http-backend`` tracks protocol versions for free.

**Nothing is held in memory.** A real push arrives chunked with no
``Content-Length`` (3 MB observed in the spike; a project with imported CAD is
much larger). The response streams straight out of the child — a clone is the
big direction — while the request body streams to an **unlinked temp file**
and is handed to the child as its stdin. That asymmetry is forced, not
preferred: see :func:`_spool_request` for the Starlette middleware behaviour
that makes a full-duplex proxy silently lose pack bytes, and for why knowing
the real length is the better ``CONTENT_LENGTH`` anyway (never the client's
header — the spike's rule).

Two seams this slice leaves open, both module attributes the PRD-005
integration slice wires (they are ``None`` here, and this file must not import
``core/authz.py`` or ``core/tenancy.py`` — those land in a sibling slice):

``require_role``
    ``(role, org, ws, proj) -> None``, raising ``AuthzError``. Called with
    ``"view"`` before an upload-pack (clone/fetch) and ``"edit"`` before a
    receive-pack (push), including the receive-pack *advertisement*. While it
    is ``None`` the floor is "an authenticated principal in hosted mode" —
    which the guard already enforces; the check here is a deliberate second
    one, because a route that is only as private as the middleware is one
    refactor away from being public.

``resolve_project``
    ``(org, ws, proj) -> Path``. While it is ``None`` the org and workspace
    are **validated but ignored** and the project resolves through
    ``service.store.canonical_path_of(proj)`` — the local/single-tenant
    mapping. The integration slice replaces it with the tenancy resolver, at
    which point ``/git/acme/hardware/widget.git`` and
    ``/git/other/hardware/widget.git`` become two different directories.

Auth. The security guard runs in the middleware, before routing, so in hosted
mode an anonymous request is already ``401`` — but git speaks **Basic**, and
the guard understands ``Bearer`` and the session cookie only. :func:`install_git_auth`
wraps ``security.guard`` (capture-and-reinstall, sentinel-guarded, idempotent)
to do two things for ``/git/`` paths and nothing else, anywhere else:

1. rewrite ``Authorization: Basic <user>:<token>`` into ``Bearer <token>``
   before the guard reads it — the username is ignored, exactly as a git
   credential helper's ``username=agentcad`` is; and
2. add ``WWW-Authenticate: Basic`` to the guard's ``401`` — without a
   challenge from the final URL a git client never offers its credential
   helper's answer, and the user sees "could not read Username … terminal
   prompts disabled" instead of the server's message.

In local mode (no ``SecurityConfig``) the wrapper is inert and these routes
are as authenticated as every other local route: not at all.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import tempfile
from pathlib import Path
from typing import IO, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core import sync_server
from ..core.model import (AuthError, NotFoundError, ServiceUnavailableError,
                          ValidationError)
from . import security as sec

#: Root-mounted: git URLs are `https://host/git/<org>/<ws>/<proj>.git`, and
#: `/api/git/...` would be a URL nobody would forgive us for. **Not** a
#: `PUBLIC_PREFIXES` entry — `security.is_public("/git/...")` is False, so
#: every route here is private by default like every other pack.
PREFIX = "/git"

#: The two smart services, and the only values `?service=` may take.
UPLOAD_PACK = "git-upload-pack"
RECEIVE_PACK = "git-receive-pack"

#: Path-segment grammar. A **superset** of `model.ID_RE` (`[a-z][a-z0-9_]{0,39}`)
#: on purpose: this exists to refuse traversal and shell-hostile names before
#: anything touches the filesystem, not to be the naming authority — the store
#: (and, later, `core/tenancy.py`) is that.
SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

#: Hard cap on a pushed request body. A push is a pack file: 3 MB is ordinary,
#: a project with imported CAD is much larger, and the point of the cap is that
#: an unbounded one is a memory-free but disk-and-time DoS. Overridable for a
#: deployment that hosts genuinely large projects.
MAX_BODY_BYTES = int(os.environ.get("AGENTCAD_SYNC_MAX_PUSH_MB", "512")) << 20

#: How much CGI header we will read before deciding the child is not speaking
#: CGI. `http-backend`'s headers are ~200 bytes.
MAX_HEADER_BYTES = 64 * 1024

_CHUNK = 64 * 1024

#: Realm for the Basic challenge. git shows it in its credential prompt.
_CHALLENGE = 'Basic realm="AgentCAD"'

# --------------------------------------------------------------- the seams

#: ``(role, org, ws, proj) -> None``; raises ``AuthzError``. See the module
#: docstring. Wired by the PRD-005 integration slice to ``core/authz.require``.
require_role: Callable[[str, str, str, str], None] | None = None

#: ``(org, ws, proj) -> Path``. See the module docstring. Wired by the
#: integration slice to the tenancy resolver.
resolve_project: Callable[[str, str, str], Path] | None = None

#: ``(dict) -> None``, called with :func:`sync_server.materialize`'s result
#: after every accepted push. A tap for the audit store (PRD-005 slice 5) and
#: what the tests assert materialization on.
on_materialize: Callable[[dict], None] | None = None


# ------------------------------------------------------------ basic auth

_WRAPPED = "_agentcad_sync_basic_auth"


def _promote_basic_to_bearer(request: Request) -> None:
    """Rewrite this request's ``Basic`` credential into a ``Bearer`` one.

    The username is discarded: a git credential helper has to send one, and
    the token is the whole credential (``AuthStore.resolve_token`` names the
    principal). Rewriting the ASGI scope rather than teaching
    ``security.resolve_principal`` about Basic keeps the widening to
    ``/git/`` paths — a Basic credential accepted everywhere would be a new
    way to authenticate to every route in the product, decided by this file.

    Anything that is not a decodable ``Basic`` header is left exactly as it
    arrived, so a malformed one still fails closed in the guard.
    """
    headers = request.scope.get("headers") or []
    for index, (name, value) in enumerate(headers):
        if name.lower() != b"authorization":
            continue
        if value[:6].lower() != b"basic ":
            return
        try:
            raw = base64.b64decode(value[6:].strip(), validate=True)
        except Exception:                       # noqa: BLE001 — fail closed
            return
        _user, sep, secret = raw.partition(b":")
        if not sep or not secret:
            return
        replaced = list(headers)
        replaced[index] = (name, b"Bearer " + secret)
        request.scope["headers"] = replaced
        # Starlette memoizes `Headers(scope=...)` on first access; the guard
        # has not read them yet (it reads `request.url.path` first), but drop
        # any cache rather than depend on that ordering.
        request.__dict__.pop("_headers", None)
        return


def _is_sync_path(path: str) -> bool:
    return isinstance(path, str) and path.startswith(PREFIX + "/")


def install_git_auth(module=sec) -> None:
    """Teach the security guard git's dialect, for ``/git/`` paths only.

    Capture-and-reinstall with a sentinel, the house idiom: idempotent across
    the many apps a test session builds, inert in local mode (``cfg is None``
    is the guard's own first branch and this wrapper's too), and a no-op for
    every path outside :data:`PREFIX`.

    It lives here rather than in ``security.py`` because ``PRD-005`` slices
    land in parallel and that file belongs to another one; the integration
    slice may fold it in, and the only thing that would change is where the
    two rules are written down.
    """
    inner = getattr(module, "guard")
    if getattr(inner, _WRAPPED, False):
        return

    def guard(cfg, request):
        if cfg is None or not _is_sync_path(request.url.path):
            return inner(cfg, request)
        _promote_basic_to_bearer(request)
        denied = inner(cfg, request)
        if denied is not None and denied.status_code == 401:
            denied.headers["WWW-Authenticate"] = _CHALLENGE
        return denied

    setattr(guard, _WRAPPED, True)
    guard.__doc__ = inner.__doc__
    module.guard = guard


# --------------------------------------------------------------- resolving

def _segment(value: str, what: str) -> str:
    if not SEGMENT_RE.match(value or ""):
        raise NotFoundError(f"no such {what}")
    return value


def _project_path(service, org: str, ws: str, proj: str) -> Path:
    """The project directory ``{org}/{ws}/{proj}.git`` addresses.

    A miss is a ``404`` that names nothing: with tenancy wired, "wrong
    workspace" and "no such project" must not be distinguishable by a caller
    who is not in the org.
    """
    _segment(org, "organization")
    _segment(ws, "workspace")
    _segment(proj, "project")
    if resolve_project is not None:
        path = Path(resolve_project(org, ws, proj))
    else:
        try:
            path = Path(service.store.canonical_path_of(proj))
        except NotFoundError:
            raise NotFoundError("no such project") from None
    if not sync_server.has_repo(path):
        raise NotFoundError("no such project")
    return path


def _authorize(role: str, org: str, ws: str, proj: str) -> None:
    """The role floor. See the module docstring's ``require_role`` seam."""
    if require_role is not None:
        require_role(role, org, ws, proj)
        return
    if sec.is_hosted() and sec.current_principal() is None:
        # Belt over the guard's braces: unreachable while the middleware runs
        # first, and the one line that keeps this pack private if it ever
        # does not.
        raise AuthError("authentication required")


def _remote_user() -> str:
    who = sec.current_principal()
    return who.client_id if who is not None else "agentcad"


# ------------------------------------------------------------- CGI plumbing

def _cgi_env(request: Request, project_path: Path, path_info: str
             ) -> dict[str, str]:
    """The environment ``git http-backend`` reads.

    ``GIT_PROJECT_ROOT`` is the **project directory** and ``PATH_INFO`` is
    ``/.history/<gitpath>``: the backend joins the two, so the repo it opens
    is ``<project>/.history`` — the ProjectHistory GIT_DIR — with no symlink
    farm and no second copy of the tree anywhere. ``GIT_HTTP_EXPORT_ALL=1``
    because there is no ``git-daemon-export-ok`` file in a project's history
    repo and there should not be: what is exported is decided by this router
    and the role floor, not by a marker file inside user data.

    ``CONTENT_LENGTH`` is set by the caller from the bytes actually received
    (any inherited one is dropped here), and ``HTTP_CONTENT_ENCODING`` is
    forwarded so the backend inflates a gzipped request itself — git 2.50 was
    never observed sending one, but the defensive branch costs one line here
    instead of a ``gzip`` import.
    """
    git_dir = sync_server.history_dir(project_path)
    env = {
        **sync_server.git_env(git_dir),
        "GIT_PROJECT_ROOT": str(project_path),
        "PATH_INFO": f"/.history{path_info}",
        "REQUEST_METHOD": request.method,
        "QUERY_STRING": request.url.query or "",
        "CONTENT_TYPE": request.headers.get("content-type", ""),
        "GIT_HTTP_EXPORT_ALL": "1",
        "REMOTE_USER": _remote_user(),
        "REMOTE_ADDR": request.client.host if request.client else "127.0.0.1",
        "GIT_COMMITTER_NAME": "AgentCAD",
        "GIT_COMMITTER_EMAIL": "agentcad@local",
    }
    env.pop("CONTENT_LENGTH", None)
    protocol = request.headers.get("git-protocol")
    if protocol:
        # Forwarded for the ADVERTISE call too, not only the RPC: this is
        # what keeps a modern client on protocol v2 (spike §A7).
        env["GIT_PROTOCOL"] = protocol
    encoding = request.headers.get("content-encoding")
    if encoding:
        env["HTTP_CONTENT_ENCODING"] = encoding
    return env


def _parse_cgi_headers(head: bytes) -> tuple[int, dict[str, str]]:
    status = 200
    headers: dict[str, str] = {}
    for line in head.replace(b"\r\n", b"\n").split(b"\n"):
        if not line.strip():
            continue
        name, _, value = line.decode("latin-1").partition(":")
        name, value = name.strip(), value.strip()
        if name.lower() == "status":
            try:
                status = int(value.split()[0])
            except (ValueError, IndexError):
                status = 500
        elif name.lower() == "content-length":
            # Dropped: we stream, so Starlette must frame the response itself.
            continue
        else:
            headers[name] = value
    return status, headers


async def _spool_request(request: Request) -> tuple[IO[bytes], int]:
    """Stream the request body to an unlinked temp file; return it and its size.

    **Why a file and not the child's stdin directly.** Feeding the body to the
    child while the response streams back is what this route wants and what
    Starlette 1.5's ``BaseHTTPMiddleware`` makes impossible: it hands the
    endpoint a ``wrapped_receive``, and ``StreamingResponse.__call__`` hands
    the *same* ``wrapped_receive`` to ``listen_for_disconnect``, which loops
    on it waiting for ``http.disconnect``. The moment the response starts —
    and ``http-backend`` emits its CGI headers for a receive-pack
    *immediately*, before reading a byte — there are two concurrent consumers
    of one receive channel, and every chunk the disconnect listener takes is a
    chunk the pack never gets. Measured exactly that way: a 4 KB push landed,
    a 3 MB push stalled at 288 KB with ``unpack-objects`` waiting forever.
    Every route pack in this app is behind that middleware (``app.py``'s one
    ``@app.middleware("http")``), so this is not ours to opt out of.

    What that costs and what it does not: the body never exists in memory (one
    64 KB chunk at a time, straight to disk, bounded by
    :data:`MAX_BODY_BYTES`), the *response* still streams — which is the
    direction that carries a whole repository — and a push is half-duplex
    anyway (``send-pack`` writes the pack, then waits for the report). The
    file is created with ``tempfile.TemporaryFile``: unlinked at creation, so
    it cannot leak into the projects tree and vanishes on close, crash or
    kill.

    Knowing the real length is also the *right* CGI: ``CONTENT_LENGTH`` is set
    from the bytes we actually received, never from the client's header (a
    push arrives chunked with no header at all).
    """
    spool = tempfile.TemporaryFile()
    total = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise ValidationError(
                    f"push exceeds the {MAX_BODY_BYTES >> 20} MB limit",
                    {"limit_bytes": MAX_BODY_BYTES},
                )
            spool.write(chunk)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool, total


async def _proxy(request: Request, project_path: Path, path_info: str,
                 after=None) -> StreamingResponse:
    """Run ``git http-backend`` for this request and stream its answer back."""
    backend = sync_server.http_backend()
    spool, length = await _spool_request(request)
    env = _cgi_env(request, project_path, path_info)
    env["CONTENT_LENGTH"] = str(length)
    try:
        proc = await asyncio.create_subprocess_exec(
            backend,
            cwd=str(project_path),
            env=env,
            stdin=spool,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    finally:
        # The child dup'd the descriptor; ours is done, and the file is
        # already unlinked, so this is the last reference we hold.
        spool.close()

    async def drain_stderr() -> bytes:
        """Read the child's stderr to EOF, keeping the first
        :data:`MAX_HEADER_BYTES` of it.

        **To EOF, in a loop** — not one ``read()``, which returns as soon as
        anything is available and then leaves the pipe unattended.
        ``unpack-objects`` writes progress there for as long as it is
        inflating a pack: the pipe fills at 64 KB, the child blocks writing to
        it, and a large push hangs with no error anywhere.
        """
        kept: list[bytes] = []
        size = 0
        try:
            while True:
                chunk = await proc.stderr.read(_CHUNK)
                if not chunk:
                    break
                if size < MAX_HEADER_BYTES:
                    kept.append(chunk)
                    size += len(chunk)
        except Exception:               # noqa: BLE001
            pass
        return b"".join(kept)[:MAX_HEADER_BYTES]

    stderr = asyncio.create_task(drain_stderr())

    async def kill() -> None:
        stderr.cancel()
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await asyncio.gather(stderr, proc.wait(), return_exceptions=True)

    # ---- headers first, so a failed child is still an honest HTTP answer
    buffer = b""
    try:
        while b"\r\n\r\n" not in buffer and len(buffer) < MAX_HEADER_BYTES:
            chunk = await proc.stdout.read(_CHUNK)
            if not chunk:
                break
            buffer += chunk
    except BaseException:
        await kill()
        raise

    head, separator, rest = buffer.partition(b"\r\n\r\n")
    if not separator:
        # Bounded: the child is answering nothing, and waiting forever for it
        # to also close its stderr would turn a broken backend into a hang.
        try:
            detail = await asyncio.wait_for(asyncio.shield(stderr), 5.0)
        except Exception:                           # noqa: BLE001
            detail = b""
        await kill()
        raise ServiceUnavailableError(
            "git-http-backend produced no response",
            {"stderr": detail.decode("utf-8", "replace")[:500]},
        )
    status, headers = _parse_cgi_headers(head)
    media_type = headers.pop("Content-Type", "application/octet-stream")

    async def body():
        try:
            if rest:
                yield rest
            while True:
                chunk = await proc.stdout.read(_CHUNK)
                if not chunk:
                    break
                yield chunk
            await asyncio.gather(stderr, return_exceptions=True)
            code = await proc.wait()
            if after is not None and code == 0 and status == 200:
                # After the last byte of the pack has been processed and
                # before the response ends: the client's `git push` returns
                # only once the server has materialized, which is the honest
                # ordering for "the hosted copy is up to date".
                await after()
        finally:
            await kill()

    return StreamingResponse(body(), status_code=status, headers=headers,
                             media_type=media_type)


# ------------------------------------------------------------------ routes

def build_router(service, registry) -> APIRouter:
    router = APIRouter()
    install_git_auth()

    async def _open(org: str, ws: str, proj: str, role: str) -> Path:
        """Authorize, resolve, and make the repo safe to serve — in that order.

        The two git calls are `to_thread`'d: these handlers are `async def`
        (they have to be, to stream), so a synchronous ``subprocess.run`` in
        one would block the whole event loop — every other request in the
        process — for as long as git takes.
        """
        _authorize(role, org, ws, proj)
        path = _project_path(service, org, ws, proj)
        try:
            await asyncio.to_thread(sync_server.prepare_repo, path)
        except sync_server.SyncError as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        return path

    @router.get("/{org}/{ws}/{proj}.git/info/refs")
    async def info_refs(org: str, ws: str, proj: str, request: Request):
        """The ref advertisement — the first request of every clone and push.

        ``?service=`` decides the role floor: the receive-pack advertisement
        is the push handshake and answers to ``edit``, not ``view``. Anything
        other than the two smart services is a 422 rather than a fall-through
        to the dumb protocol, which this pack does not serve.
        """
        service_name = request.query_params.get("service", "")
        if service_name not in (UPLOAD_PACK, RECEIVE_PACK):
            raise ValidationError(
                "only the smart git protocol is served here",
                {"service": service_name,
                 "supported": [UPLOAD_PACK, RECEIVE_PACK]},
            )
        role = "edit" if service_name == RECEIVE_PACK else "view"
        path = await _open(org, ws, proj, role)
        return await _proxy(request, path, "/info/refs")

    @router.post("/{org}/{ws}/{proj}.git/git-upload-pack")
    async def upload_pack(org: str, ws: str, proj: str, request: Request):
        """A clone or a fetch. Reads; never touches the work tree."""
        path = await _open(org, ws, proj, "view")
        return await _proxy(request, path, f"/{UPLOAD_PACK}")

    @router.post("/{org}/{ws}/{proj}.git/git-receive-pack")
    async def receive_pack(org: str, ws: str, proj: str, request: Request):
        """A push.

        The FR9 rules are the ``pre-receive`` hook's, installed by
        ``sync_server.prepare_repo`` above — they refuse *inside* git's ref
        transaction, atomically for the whole push, and their messages reach
        the human as ``remote: agentcad: …``. Nothing in this handler decides
        what a push may do.

        What this handler does is materialize afterwards: with
        ``receive.denyCurrentBranch=ignore`` the refs advance and the work
        tree does not, so the checkout is the step that makes the pushed state
        the state the app serves. ``pending_edits`` is captured **before** the
        push, because afterwards a stale work tree and an edited one are
        indistinguishable to ``git status``.
        """
        path = await _open(org, ws, proj, "edit")
        dirty = await asyncio.to_thread(sync_server.pending_edits, path)

        async def after() -> None:
            def run() -> dict:
                return sync_server.materialize(
                    path,
                    lambda: sync_server.project_write_scope(service, proj, path),
                    dirty=dirty,
                )
            try:
                result = await asyncio.to_thread(run)
            except Exception as exc:            # noqa: BLE001
                # The push has already landed — the bytes are in the repo and
                # the client has been told so by git itself. A failed
                # checkout (a ConflictError from the turn lock, a git error)
                # must not become an exception in the middle of a response
                # body that is already 200.
                result = {"materialized": False, "reason": type(exc).__name__,
                          "error": str(exc), "changed": 0}
            if on_materialize is not None:
                on_materialize({"project": proj, "org": org, "workspace": ws,
                                **result})

        return await _proxy(request, path, f"/{RECEIVE_PACK}", after=after)

    return router
