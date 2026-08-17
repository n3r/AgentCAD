"""Route pack: enrolment, login, sessions and account management (PRD-005a).

Registered like every other pack, and inert in local mode — every handler
begins by asking :func:`security.current_config` and answers ``404`` when
there is none, so a local instance is byte-identically what it was.

The configuration comes from ``security.current_config()`` rather than from
``service``, because identity is **app-layer** state. Putting it on the
service is what would drag it into ``checks._ephemeral_service`` and
``gate._ephemeral_service``, and PRD-004/011 being unaffected *by
construction* is the property this feature must not spend.

Roles are checked here rather than in the middleware: there are five
admin-only handlers, and a path-pattern list in the guard would be a second
place to get it wrong.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from ..core.model import AuthError, AuthzError, NotFoundError, RateLimitedError
from . import security as sec

#: Sent for every failed sign-in, whatever the reason. "No such handle",
#: "wrong password", "never enrolled" and "disabled" are **one** answer:
#: telling them apart is a user-enumeration oracle, and the person who
#: genuinely forgot which handle they chose is not helped enough to pay for
#: it.
_LOGIN_FAILED = "sign-in failed"


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    #: **This app's** configuration, captured at mount time rather than read
    #: from the process-global slot on every request. `create_app` calls
    #: `security.install()` before `_mount_route_packs`, so this is exactly the
    #: config of the app this router belongs to — and a second app built later
    #: in the same process (which the test suite does constantly, and a future
    #: embedder might) cannot make these handlers answer with its identity
    #: store, or wake them up inside a local-mode app.
    mounted_config = sec.current_config()

    # ------------------------------------------------------------ helpers

    def _config():
        cfg = mounted_config
        if cfg is None or not cfg.mode.hosted:
            # Local mode has no accounts at all. 404 rather than 501: the
            # route genuinely does not exist on this instance.
            raise NotFoundError("this instance is not running in hosted mode")
        return cfg

    def _principal():
        who = sec.current_principal()
        if who is None:
            raise AuthError("authentication required")
        return who

    def _require_admin():
        """An admin **person**, never a bearer token.

        Design Decision 14: minting credentials from the same authenticated
        HTTP surface those credentials unlock is a privilege-escalation shape
        worth avoiding while there is no audit log. A token drives the
        product; a signed-in human manages accounts.
        """
        who = _principal()
        if who.kind != "user" or who.role != "admin":
            raise AuthzError(
                "this route is for a signed-in administrator",
                {"required_role": "admin", "kind": who.kind})
        return who

    def _identity(cfg, who) -> dict:
        return {"principal": who.client_id, "kind": who.kind,
                "role": who.role, "mode": cfg.mode.name}

    def _set_session(cfg, response: JSONResponse, secret: str) -> JSONResponse:
        response.set_cookie(
            sec.SESSION_COOKIE, secret,
            httponly=True,                  # XSS cannot read it
            samesite="lax",                 # first CSRF layer; Origin is second
            secure=cfg.mode.secure_cookies,
            path="/",
        )
        return response

    async def _body(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:                   # noqa: BLE001 — a body is optional
            return {}
        return body if isinstance(body, dict) else {}

    # ------------------------------------------------------------- login

    @router.post("/auth/login")
    async def login(request: Request):
        cfg = _config()
        body = await _body(request)
        handle = body.get("handle")
        password = body.get("password")
        if not isinstance(handle, str) or not isinstance(password, str):
            raise AuthError(_LOGIN_FAILED)

        # The buckets are taken BEFORE the scrypt call, so a flood cannot
        # spend 63 ms of CPU per request. Address first (the cheaper, broader
        # bound), then handle.
        address = (request.client.host if request.client else "?") or "?"
        for bucket, key in ((cfg.address_rate, f"addr:{address}"),
                            (cfg.login_rate, f"handle:{handle[:64]}")):
            if not bucket.take(key):
                raise RateLimitedError(
                    "too many sign-in attempts",
                    {"retry_after_s": cfg.retry_after_s(bucket)})

        if not cfg.store.verify_password(handle, password):
            raise AuthError(_LOGIN_FAILED)

        device = sec._device(request.headers, None)          # noqa: SLF001
        secret = cfg.store.create_session(handle, device)
        row = cfg.store.resolve_session(secret)
        who = sec.Principal(kind="user", name=handle, role=row["role"],
                            device=device, via="cookie")
        return _set_session(cfg, JSONResponse(_identity(cfg, who)), secret)

    @router.post("/auth/logout")
    def logout(request: Request):
        """Always 200, even with no session.

        Revocation is server-side — the row is deleted, so a cookie copied
        before logout is dead on its next use. Clearing the browser's copy
        alone would not be revocation.
        """
        cfg = _config()
        secret = request.cookies.get(sec.SESSION_COOKIE)
        if secret:
            cfg.store.revoke_session(secret)
        response = JSONResponse({"ok": True})
        response.delete_cookie(sec.SESSION_COOKIE, path="/")
        return response

    @router.get("/auth/session")
    def session():
        cfg = _config()
        return _identity(cfg, _principal())

    # --------------------------------------------------------- enrolment

    @router.get("/auth/enrol/{token}")
    def enrol_page(token: str, request: Request):
        """The account this link is for — or the app itself, for a browser.

        The admin CLI prints this URL and a human pastes it into a browser, so
        a client that asks for HTML gets `index.html` (whose router reads the
        token out of the path and renders the password form) and everything
        else gets JSON. Without this the invitation flow ends at a page of
        JSON.
        """
        cfg = _config()
        wants_html = "text/html" in (request.headers.get("accept") or "")
        index = _FRONTEND_INDEX()
        if wants_html and index is not None:
            return FileResponse(index)
        handle = _enrolment_handle(cfg, token)
        return {"handle": handle, "mode": cfg.mode.name}

    @router.post("/auth/enrol/{token}")
    async def enrol(token: str, request: Request):
        cfg = _config()
        body = await _body(request)
        handle = cfg.store.enrol(token, body.get("password"))
        secret = cfg.store.create_session(
            handle, sec._device(request.headers, None))          # noqa: SLF001
        row = cfg.store.resolve_session(secret)
        who = sec.Principal(kind="user", name=handle, role=row["role"],
                            device=row.get("device"), via="cookie")
        return _set_session(cfg, JSONResponse(_identity(cfg, who)), secret)

    # ------------------------------------------------------------- users

    @router.get("/auth/users")
    def list_users():
        cfg = _config()
        _require_admin()
        return {"users": cfg.store.list_users()}

    @router.post("/auth/users", status_code=201)
    async def add_user(request: Request):
        cfg = _config()
        _require_admin()
        body = await _body(request)
        role = body.get("role") or "member"
        token = cfg.store.add_user(body.get("handle"), role=role)
        return {"handle": body.get("handle"), "role": role,
                "enrol_url": f"{cfg.mode.public_origin}/api/auth/enrol/{token}",
                "trust": TRUST_SENTENCE}

    @router.post("/auth/users/{handle}/disable")
    def disable_user(handle: str):
        cfg = _config()
        _require_admin()
        cfg.store.disable_user(handle)
        cfg.store.revoke_sessions_for(handle)
        return {"handle": handle, "disabled": True}

    # ------------------------------------------------------------ tokens

    @router.get("/auth/tokens")
    def list_tokens():
        cfg = _config()
        _require_admin()
        return {"tokens": cfg.store.list_tokens()}

    @router.post("/auth/tokens", status_code=201)
    async def add_token(request: Request):
        """Mint a bearer. The secret is in **this** response and nowhere else.

        `AuthStore.list_tokens` cannot return it — only a SHA-256 digest is
        stored — so "shown once" is a property of the storage rather than a
        promise this handler keeps.
        """
        cfg = _config()
        _require_admin()
        body = await _body(request)
        ttl_days = body.get("ttl_days")
        token = cfg.store.add_token(
            body.get("name"),
            role=body.get("role") or "member",
            ttl_days=int(ttl_days) if isinstance(ttl_days, (int, float)) else None,
        )
        # `acad_<id8>_<secret43>`; the secret's own alphabet includes "_",
        # which is why this is `split("_", 2)` and never `split("_")`.
        token_id = token.split("_", 2)[1]
        row = next(r for r in cfg.store.list_tokens() if r["id"] == token_id)
        return {**row, "token": token,
                "note": "this is the only time the token is shown"}

    @router.delete("/auth/tokens/{token_id}")
    def revoke_token(token_id: str):
        cfg = _config()
        _require_admin()
        cfg.store.revoke_token(token_id)
        return {"id": token_id, "revoked": True}

    return router


#: The four-place trust statement (design Decision 1). The other three are
#: `docs/deployment.md`, the `compose.yaml` header and `agentcad admin user
#: add --help` + its success output.
TRUST_SENTENCE = (
    "an account on this instance can execute arbitrary Python on the host; "
    "give one only to someone you would give a shell to"
)


def _enrolment_handle(cfg, token: str) -> str:
    """Whose enrolment this is — or a 404 that says nothing else.

    Read-only: it must not spend the token, or previewing the link would burn
    it.
    """
    handle = cfg.store.peek_enrolment(token)
    if handle is None:
        raise NotFoundError("this enrolment link is not valid")
    return handle


def _FRONTEND_INDEX():
    from .app import FRONTEND_DIR

    index = FRONTEND_DIR / "index.html"
    return index if index.is_file() else None
