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

import base64
import binascii
import importlib
import importlib.util
import json
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from ..core import audit as audit_mod
from ..core.model import (
    AuthError, AuthzError, NotFoundError, RateLimitedError, ValidationError,
)
from . import security as sec

#: Sent for every failed sign-in, whatever the reason. "No such handle",
#: "wrong password", "never enrolled" and "disabled" are **one** answer:
#: telling them apart is a user-enumeration oracle, and the person who
#: genuinely forgot which handle they chose is not helped enough to pay for
#: it.
_LOGIN_FAILED = "sign-in failed"

#: What the browser shows in the passkey prompt ("Use your passkey for
#: AgentCAD?"). Not the instance's hostname — that is the *relying party id*,
#: which is derived from the public origin.
PASSKEY_RP_NAME = "AgentCAD"

#: A WebAuthn ceremony is a human pressing a fingerprint sensor. Five minutes
#: is generous for that and short enough that a challenge lifted off a screen
#: is dead by the time it is typed anywhere. The cap is what keeps an
#: anonymous `login/begin` flood bounded in memory.
PASSKEY_CHALLENGE_TTL_S = 300.0
PASSKEY_CHALLENGE_MAX = 512


def passkeys_available() -> bool:
    """Is the ``agentcad[cloud]`` extra installed?

    ``find_spec`` rather than an import: ``import webauthn`` costs ~105 ms of
    interpreter time (measured in the PRD-005 spike), and this is asked on
    every passkey request. The FEM precedent
    (``kernel/handlers/fem.fem_available``) is the shape; the difference is
    that FEM gates a *tool registration* and this gates a *route*, so the
    absent answer is a 501 rather than a missing tool.
    """
    try:
        return importlib.util.find_spec("webauthn") is not None
    except (ImportError, ValueError):        # a broken half-install
        return False


class _ChallengeStore:
    """Server-side WebAuthn challenges: in memory, TTL'd, size-capped.

    In memory for :class:`agentcad.core.oidc.PendingFlows`' reason, stated
    there in full: a hosted AgentCAD is one uvicorn process (``cli.cmd_serve``
    passes no ``workers=``, and the kernel pool, the event bus and the turn
    locks are already in-process singletons), the record lives minutes, and
    losing it to a restart costs one retry. A challenge is not a credential —
    it is a nonce — so this is not identity state and it does not belong in
    ``authstore``'s documents, where it would put an flock + fsync on the
    sign-in path.
    """

    def __init__(self, ttl_s: float = PASSKEY_CHALLENGE_TTL_S,
                 cap: int = PASSKEY_CHALLENGE_MAX) -> None:
        self._ttl = ttl_s
        self._cap = cap
        self._rows: dict[str, dict] = {}

    def put(self, key: str, record: dict) -> None:
        self._prune()
        if len(self._rows) >= self._cap:
            for old in sorted(self._rows,
                              key=lambda k: self._rows[k]["created"]
                              )[:self._cap // 4 or 1]:
                self._rows.pop(old, None)
        # `challenge` is written here rather than by the caller, so the record
        # a `take` returns always carries the bytes WE minted — the verifier
        # is never handed a challenge that came out of a request body.
        self._rows[key] = {**record, "challenge": key, "created": time.time()}

    def take(self, key: object) -> dict | None:
        """Pop a challenge. **Single use**: a replayed assertion finds
        nothing, which is what makes a captured one worthless."""
        self._prune()
        if not isinstance(key, str) or not key:
            return None
        row = self._rows.pop(key, None)
        if row is None or time.time() - row["created"] > self._ttl:
            return None
        return row

    def _prune(self) -> None:
        cutoff = time.time() - self._ttl
        for key in [k for k, row in self._rows.items() if row["created"] < cutoff]:
            self._rows.pop(key, None)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(text: object) -> bytes | None:
    """Lenient about padding, strict about everything else. ``None`` on junk —
    every caller is holding an anonymous request body."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None


def _client_challenge(credential: object) -> str | None:
    """The challenge a credential's ``clientDataJSON`` names.

    **A lookup key and nothing else.** The bytes this returns are used to find
    *our own* record; the challenge the verifier is then given is the one we
    minted and stored, never this one. Reading attacker-controlled JSON to
    decide which stored nonce to compare against is safe; comparing it to
    itself would not be.
    """
    if not isinstance(credential, dict):
        return None
    raw = _b64u_decode((credential.get("response") or {}).get("clientDataJSON")
                       if isinstance(credential.get("response"), dict) else None)
    if raw is None or len(raw) > 8192:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError, UnicodeDecodeError):
        # `json.loads` raises RecursionError on deep nesting, and it is NOT a
        # ValueError (changelog 0181's eleven-site lesson).
        return None
    challenge = data.get("challenge") if isinstance(data, dict) else None
    return challenge if isinstance(challenge, str) and challenge else None


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

    # ------------------------------------------------------------- the audit
    #
    # Auth events are **instance-wide**, not acts inside an org: a person may
    # be a member of several orgs or of none, and a failed sign-in belongs to
    # no tenant at all. They therefore land in `audit.INSTANCE_ORG`
    # (`_instance.db`), a name no org can take because `ID_RE` demands a
    # leading letter. Tool calls, which always name a project, land in their
    # own org's database.
    #
    # Every tap is `audit.record`, which swallows a storage failure with a
    # warning: an audit backend that has gone read-only must not be the reason
    # nobody can sign in. The trade is stated in `core/audit.py` rather than
    # hidden here.

    def _log():
        cfg = mounted_config
        return audit_mod.for_auth_store(cfg.store) if cfg is not None else None

    def _audit(action: str, *, principal: str | None = None,
               outcome: str = "ok", project: str | None = None,
               args: object = None) -> None:
        audit_mod.record(_log(), audit_mod.INSTANCE_ORG, action,
                         principal=principal, project=project, args=args,
                         outcome=outcome)

    def _claimed(handle: object) -> str:
        """The principal a **failed** attempt claimed to be.

        A sign-in that failed did not authenticate anybody, so this is the
        handle from the request body, spelled as a principal only when it
        could be one. What tells a claim from an identity is the row's
        `outcome` column, never the principal string — which is why the two
        are recorded together and why the handle is recorded at all: "somebody
        tried to be nikita 400 times" is the question an audit log exists to
        answer.
        """
        from ..core.authstore import HANDLE_RE

        if isinstance(handle, str) and HANDLE_RE.match(handle):
            return f"user:{handle}"
        return "unknown"

    def _refused(exc, action: str, *, principal: str | None = None,
                 outcome: str | None = None):
        """Record a refusal and hand the exception back to be raised.

        `raise _refused(AuthError(...), "login", ...)` keeps the tap on the
        same line as the refusal it records, so a new refusal path cannot
        silently be an unrecorded one.
        """
        wire = (type(exc).__name__.replace("Error", "").lower() + "_error")
        _audit(action, principal=principal, outcome=outcome or wire)
        return exc

    def _set_session(cfg, response: Response, secret: str) -> Response:
        # The annotation was `JSONResponse`; it is `Response` since PRD-005
        # slice 3, because the OIDC callback hands a browser a redirect with
        # the very same cookie. The body is unchanged — one place sets this
        # cookie, whatever the response type.
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
            raise _refused(AuthError(_LOGIN_FAILED), "login",
                           principal=_claimed(handle), outcome="malformed")

        # The buckets are taken BEFORE the scrypt call, so a flood cannot
        # spend 63 ms of CPU per request. Address first (the cheaper, broader
        # bound), then the handle **at this address**.
        #
        # `sec.login_key` is what makes the second bucket a throttle rather than
        # a lockout: keyed on the handle alone, a stranger could hold it empty
        # forever and the account's owner would never sign in again (review
        # finding M3, `security.LOGIN_RATE_PER_S`). The refusal is also
        # identical for a right and a wrong password — the buckets are taken
        # before the credential is looked at, so nothing here is an oracle.
        #
        # `request.client.host` is the REAL client behind the deployment guide's
        # reverse proxy, because uvicorn is run with `proxy_headers` bounded to
        # the trusted proxy (`cli._uvicorn_proxy_kwargs`) and the proxy sets
        # `X-Forwarded-For`. If that plumbing is missing this is the proxy's
        # address for everyone and the key degrades to per-handle — the round-2
        # reopening of M3. See `sec.login_key`.
        address = (request.client.host if request.client else "?") or "?"
        for bucket, key in ((cfg.address_rate, f"addr:{address}"),
                            (cfg.login_rate, sec.login_key(handle, address))):
            if not bucket.take(key):
                raise _refused(
                    RateLimitedError(
                        "too many sign-in attempts",
                        {"retry_after_s": cfg.retry_after_s(bucket)}),
                    "login", principal=_claimed(handle),
                    outcome="rate_limited")

        if not cfg.store.verify_password(handle, password):
            raise _refused(AuthError(_LOGIN_FAILED), "login",
                           principal=_claimed(handle), outcome="failed")

        device = sec._device(request.headers, None)          # noqa: SLF001
        secret = cfg.store.create_session(handle, device)
        row = cfg.store.resolve_session(secret)
        who = sec.Principal(kind="user", name=handle, role=row["role"],
                            device=device, via="cookie")
        _audit("login", principal=who.client_id, args={"via": "password"})
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
        who = sec.current_principal()
        if secret:
            cfg.store.revoke_session(secret)
        # Recorded even when there was no session to revoke, with the outcome
        # saying which: "somebody's browser is repeatedly logging nobody out"
        # is a signal, and the 200 this route always returns is not.
        _audit("logout", principal=who.client_id if who else "unknown",
               outcome="ok" if secret else "no_session")
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
        try:
            handle = cfg.store.enrol(token, body.get("password"))
        except (NotFoundError, ValidationError) as exc:
            # The principal is genuinely unknown here: an invalid token names
            # nobody, and a valid one is not spent. `peek_enrolment` would say
            # who — and would be a way to put a handle in the log by guessing
            # a token, so it is not asked.
            raise _refused(exc, "enrol", principal="unknown")
        secret = cfg.store.create_session(
            handle, sec._device(request.headers, None))          # noqa: SLF001
        row = cfg.store.resolve_session(secret)
        who = sec.Principal(kind="user", name=handle, role=row["role"],
                            device=row.get("device"), via="cookie")
        _audit("enrol", principal=who.client_id)
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
        _audit("user_add", args={"handle": body.get("handle"), "role": role})
        return {"handle": body.get("handle"), "role": role,
                "enrol_url": f"{cfg.mode.public_origin}/api/auth/enrol/{token}",
                "trust": TRUST_SENTENCE}

    @router.post("/auth/users/{handle}/disable")
    def disable_user(handle: str):
        cfg = _config()
        _require_admin()
        cfg.store.disable_user(handle)
        dropped = cfg.store.revoke_sessions_for(handle)
        _audit("user_disable", args={"handle": handle, "sessions": dropped})
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
        # The arguments are digested, never stored — and `add_token`'s body
        # carries no secret anyway (the secret is minted here). What the digest
        # is for is correlating this row with the `token_revoke` that follows.
        _audit("token_add", args={"id": token_id, "name": row["name"],
                                  "role": row["role"]})
        return {**row, "token": token,
                "note": "this is the only time the token is shown"}

    # ------------------------------------------------- the audit (FR12)

    def _audit_reader(cfg, org: str):
        """Who may read *org*'s audit log.

        Two doors, and the second is the point of a multi-tenant product: the
        **instance administrator** (005a's role, the operator of the box) reads
        anything, including the instance-wide `_instance` log; an **org admin**
        reads their own org's log and nothing else. A member does not — an
        audit log names every action of every colleague, and "who is allowed to
        watch" is an administrative question by construction.

        Always a *person*: `authz.role_of` gives an agent no org default, so a
        bearer cannot reach `admin` at org level, and the instance-admin branch
        tests `kind` explicitly. A token that could read the audit log would be
        a token that could read what its own theft looked like.
        """
        who = _principal()
        if who.kind == "user" and who.role == "admin":
            return who
        if who.kind == "user" and org != audit_mod.INSTANCE_ORG:
            from ..core.authz import can
            from ..core.tenancy import TenancyStore

            if can(TenancyStore(cfg.store.root), "admin", who.client_id,
                   org, "*"):
                return who
        raise AuthzError(
            "reading the audit log is for an administrator of this instance "
            "or of the org",
            {"required_role": "admin", "org": org, "kind": who.kind})

    @router.get("/auth/audit")
    def audit_query(request: Request):
        """Query one org's audit log. Newest first, paginated.

        `org` defaults to `_instance`, where the auth events this file records
        live; a tenant's tool activity is in `?org=<org>`.
        """
        cfg = _config()
        params = request.query_params
        org = params.get("org") or audit_mod.INSTANCE_ORG
        who = _audit_reader(cfg, org)
        log = _log()
        limit = _positive(params.get("limit"), audit_mod.DEFAULT_LIMIT, "limit")
        offset = _positive(params.get("offset"), 0, "offset", floor=0)
        if not log.path_for(org).exists():
            # A query must not CREATE a database: `?org=` is caller-supplied,
            # and an admin sweeping org names would otherwise leave a file per
            # guess behind. An org with no events answers with no rows.
            rows, total = [], 0
        else:
            rows = log.query(
                org,
                principal=params.get("principal"),
                project=params.get("project"),
                action=params.get("action"),
                since=audit_mod.parse_time(params.get("since"), "since"),
                until=audit_mod.parse_time(params.get("until"), "until"),
                limit=limit, offset=offset)
            total = log.count(org)
        # Reading the log is itself an administrative act, so it is in the log.
        _audit("audit_query", principal=who.client_id,
               args={"org": org, "principal": params.get("principal"),
                     "project": params.get("project"),
                     "action": params.get("action")})
        return {"org": org, "rows": rows, "limit": limit, "offset": offset,
                # Unfiltered, so the UI can say "showing 200 of 12 431".
                "total": total,
                "next_offset": (offset + len(rows)) if len(rows) == limit
                else None}

    @router.delete("/auth/tokens/{token_id}")
    def revoke_token(token_id: str):
        cfg = _config()
        _require_admin()
        cfg.store.revoke_token(token_id)
        _audit("token_revoke", args={"id": token_id})
        return {"id": token_id, "revoked": True}

    # ============================================== OIDC (PRD-005 FR1)
    #
    # Three routes and one rule: **an OIDC identity signs in a handle it is
    # linked to, and linking never creates one** (`core/oidc.py` holds the
    # policy and the argument). `login` and `callback` are the only two that
    # are anonymous — they are in `security.PUBLIC_PATHS` by exact path, not by
    # prefix, so `/api/auth/oidc/link` stays behind the guard and the CSRF rule
    # that comes with an unsafe method.

    #: One `OidcClient` per provider fingerprint, so editing `oidc.json` takes
    #: effect on the next request with no restart while the discovery and JWKS
    #: caches survive the requests in between. The pending-flow store is
    #: separate and outlives a re-read, so an admin saving the file mid-sign-in
    #: does not strand a browser at the IdP.
    _oidc_slot: dict = {}

    def _oidc():
        """``(client, module)``, or a 404 when this instance has no provider.

        The module is imported **lazily**: `import jwt` costs ~65 ms of
        interpreter start-up (spike B1) and every app in this process — local
        ones included — mounts this pack.
        """
        cfg = _config()
        oidc = importlib.import_module("agentcad.core.oidc")
        config = oidc.OidcConfig.from_document(cfg.store.read_oidc(),
                                               cfg.mode.public_origin)
        if config is None:
            raise NotFoundError(
                "this instance has no single-sign-on provider configured")
        if _oidc_slot.get("fingerprint") != config.fingerprint:
            _oidc_slot["fingerprint"] = config.fingerprint
            _oidc_slot["client"] = oidc.OidcClient(
                config, pending=_oidc_slot.setdefault("pending",
                                                      oidc.PendingFlows()))
        return _oidc_slot["client"], oidc

    def _address_budget(cfg, request: Request) -> None:
        """Charge this address the same bucket `POST /auth/login` charges.

        Deliberately the *same* bucket and not a new one: an instance has one
        anonymous sign-in budget per address, and a second door with its own
        allowance is a way around the first. Handle-keyed throttling has no
        meaning here — neither door is given a handle.
        """
        address = (request.client.host if request.client else "?") or "?"
        if not cfg.address_rate.take(f"addr:{address}"):
            raise RateLimitedError(
                "too many sign-in attempts",
                {"retry_after_s": cfg.retry_after_s(cfg.address_rate)})

    def _set_flow_cookie(cfg, response: Response, oidc, pending) -> Response:
        """Bind this authorization request to **this** browser.

        `oidc.FLOW_COOKIE` carries the whole argument; the short summary is
        that `state` proves a flow started *here* and this proves it started
        *in this browser*, which is the difference between a sign-in and a
        login CSRF that lands a victim inside the attacker's account.
        """
        response.set_cookie(
            oidc.FLOW_COOKIE, pending.binding,
            httponly=True,
            samesite="lax",             # the callback is a top-level GET
            secure=cfg.mode.secure_cookies,
            max_age=int(oidc.PENDING_TTL_S),
            path="/api/auth/oidc",
        )
        return response

    @router.get("/auth/oidc/login")
    def oidc_login(request: Request):
        """Start a sign-in: 302 to the provider. 404 when none is configured."""
        cfg = _config()
        _address_budget(cfg, request)
        client, oidc = _oidc()
        pending = client.begin()
        return _set_flow_cookie(
            cfg, RedirectResponse(client.authorization_url(pending),
                                  status_code=302), oidc, pending)

    @router.post("/auth/oidc/link")
    def oidc_link_begin():
        """Start a **link**: bind a provider identity to the caller's handle.

        A POST, not a GET with a flag, and that is the security control rather
        than taste. The guard's cross-origin check covers unsafe methods only
        (`security.guard`, the CSRF branch), so a plain `GET …/login?link=1`
        could be triggered by a cross-site navigation: the victim's browser
        starts a flow, the *attacker* authenticates at the IdP in it, and the
        attacker's identity ends up bound to the victim's account — an account
        takeover dressed as a convenience.
        """
        cfg = _config()
        who = _principal()
        if who.kind != "user":
            raise AuthzError(
                "only a signed-in person can link a single-sign-on identity",
                {"kind": who.kind})
        client, oidc = _oidc()
        pending = client.begin(link_handle=who.name)
        # The authenticated ceremony STARTS are recorded; the anonymous ones
        # (`GET /auth/oidc/login`, `POST /auth/passkey/login/begin`) are not.
        # Not an oversight: those two are reachable with no credential, and a
        # row per attempt would let a stranger grow the audit database from the
        # outside. Their *completions* are recorded, success and failure alike,
        # which is where the security-relevant fact is.
        _audit("oidc_link_begin", principal=who.client_id)
        return _set_flow_cookie(
            cfg, JSONResponse({"authorization_url":
                               client.authorization_url(pending),
                               "handle": who.name}), oidc, pending)

    @router.delete("/auth/oidc/link")
    def oidc_unlink():
        cfg = _config()
        who = _principal()
        if who.kind != "user":
            raise AuthzError("only a signed-in person can unlink an identity",
                             {"kind": who.kind})
        unlinked = cfg.store.unlink_oidc(who.name)
        _audit("oidc_unlink", principal=who.client_id,
               outcome="ok" if unlinked else "no_link")
        return {"handle": who.name, "unlinked": unlinked}

    @router.get("/auth/oidc/callback")
    def oidc_callback(request: Request):
        """Finish the flow. Sets **the same session cookie** `login` sets.

        One session mechanism, whatever authenticated it: `_set_session` is
        called here verbatim, so revocation, the sliding window and the
        role-read-from-the-user-row all behave identically for an SSO session
        and a password one. Nothing about OIDC survives into the session — the
        store row is the authority, exactly as it is for `POST /auth/login`.
        """
        cfg = _config()
        client, oidc = _oidc()
        params = request.query_params
        if params.get("error"):
            # The provider refused (`access_denied`, `login_required`, ...).
            # Its own code is echoed because the person needs to know whether
            # to try again or call an administrator.
            raise _refused(
                AuthError("the identity provider refused this sign-in",
                          {"idp_error": str(params.get("error"))[:64]}),
                "login", principal="unknown", outcome="idp_refused")
        verified = client.complete(params.get("code"), params.get("state"),
                                   request.cookies.get(oidc.FLOW_COOKIE))

        if verified.link_handle:
            # An explicit link by somebody who is already signed in. Their
            # session is untouched — re-minting it would sign a person out of
            # their other browsers for adding a credential.
            #
            # The session is re-read here rather than trusted from the pending
            # record: a browser that signed out (or signed in as somebody
            # else) between starting the link and finishing it must not bind
            # the identity to the handle it *used* to hold.
            who = sec.current_principal()
            if who is None or who.kind != "user" or who.name != verified.link_handle:
                raise AuthError("sign in again and re-start the link")
            handle = oidc.link_identity(cfg.store, client.config, verified)
            _audit("oidc_link", principal=f"user:{handle}",
                   args={"issuer": verified.issuer,
                         "subject": verified.subject})
            response = JSONResponse({"handle": handle, "linked": True,
                                     "issuer": verified.issuer,
                                     "subject": verified.subject,
                                     "email": verified.email})
            response.delete_cookie(oidc.FLOW_COOKIE, path="/api/auth/oidc")
            return response

        handle, how = oidc.sign_in_handle(cfg.store, client.config, verified)
        device = sec._device(request.headers, None)          # noqa: SLF001
        secret = cfg.store.create_session(handle, device)
        row = cfg.store.resolve_session(secret)
        if row is None:                     # disabled between the two calls
            raise _refused(AuthError(oidc.UNLINKED), "login",
                           principal=f"user:{handle}", outcome="disabled")
        who = sec.Principal(kind="user", name=handle, role=row["role"],
                            device=device, via="cookie")
        _audit("login", principal=who.client_id,
               args={"via": "oidc", "link": how, "issuer": verified.issuer})
        payload = {**_identity(cfg, who), "via": "oidc", "link": how,
                   "issuer": verified.issuer, "subject": verified.subject}
        # A browser lands here by following the provider's redirect, so it is
        # sent on into the app rather than shown a page of JSON — the
        # `enrol_page` precedent, and for the same reason. Every non-browser
        # client (curl, a test, an audit script) gets the payload.
        wants_html = "text/html" in (request.headers.get("accept") or "")
        response: Response = (RedirectResponse("/", status_code=303)
                              if wants_html else JSONResponse(payload))
        response.delete_cookie(oidc.FLOW_COOKIE, path="/api/auth/oidc")
        return _set_session(cfg, response, secret)

    # ========================================== passkeys (PRD-005 FR1)
    #
    # WebAuthn, behind the `agentcad[cloud]` extra. `webauthn` is imported
    # INSIDE the handlers (~105 ms, spike B1) and the routes answer 501 when it
    # is absent — the FEM precedent (`routes_analysis._fem_unavailable`) with
    # its wording. Local password accounts are untouched and remain the only
    # thing a no-extra instance needs.

    _challenges = _ChallengeStore()

    def _passkey_gate() -> JSONResponse | None:
        """The 501 for an instance without the extra, or ``None``."""
        if passkeys_available():
            return None
        return JSONResponse(
            status_code=501,
            content={"error": {
                "type": "PasskeysUnavailable",
                "message": "passkeys require: pip install 'agentcad[cloud]'",
                "details": {}}},
        )

    def _webauthn():
        return importlib.import_module("webauthn")

    def _relying_party(cfg) -> tuple[str, str]:
        """``(rp_id, origin)`` for this instance.

        The rp id is the origin's **host with no port** (`AppMode.origin_host`)
        because that is what WebAuthn calls the relying party id — a port in it
        makes every ceremony fail with a `SecurityError` in the browser and
        nothing in the log. The expected origin is the full configured origin,
        scheme and port included, which is what `clientDataJSON` carries.
        """
        return cfg.mode.origin_host, cfg.mode.public_origin

    def _passkey_person():
        who = _principal()
        if who.kind != "user":
            # Decision 14's shape: a bearer token must not mint a credential
            # that then unlocks the surface the bearer already has. A human
            # registers a passkey; a token drives the product.
            raise AuthzError(
                "only a signed-in person can manage passkeys", {"kind": who.kind})
        return who

    def _credential(body: dict) -> dict:
        """The credential out of a request body, in either shape a client
        sends: the raw `PublicKeyCredential` JSON, or wrapped in
        `{"credential": ...}`."""
        if isinstance(body.get("credential"), dict):
            return body["credential"]
        return body if isinstance(body.get("response"), dict) else {}

    @router.get("/auth/passkeys")
    def list_passkeys():
        """This account's credentials. No `webauthn` needed — it is a read of
        `users.json`, so it answers on an instance without the extra too (and
        an instance that *removed* the extra can still revoke what it
        registered)."""
        cfg = _config()
        who = _passkey_person()
        return {"passkeys": [
            {k: v for k, v in row.items() if k != "public_key"}
            for row in cfg.store.get_passkeys(who.name)]}

    @router.delete("/auth/passkeys/{credential_id}")
    def delete_passkey(credential_id: str):
        cfg = _config()
        who = _passkey_person()
        removed = cfg.store.remove_passkey(who.name, credential_id)
        if not removed:
            raise NotFoundError("no such passkey on this account")
        _audit("passkey_delete", principal=who.client_id,
               args={"credential_id": credential_id})
        return {"id": credential_id, "removed": True}

    @router.post("/auth/passkey/register/begin")
    async def passkey_register_begin(request: Request):
        cfg = _config()
        gate = _passkey_gate()
        if gate is not None:
            return gate
        who = _passkey_person()
        wa = _webauthn()
        from webauthn.helpers import structs

        body = await _body(request)
        existing = cfg.store.get_passkeys(who.name)
        rp_id, _origin = _relying_party(cfg)
        options = wa.generate_registration_options(
            rp_id=rp_id,
            rp_name=PASSKEY_RP_NAME,
            # The handle, not a random opaque id. WebAuthn prefers an opaque
            # user id, and the reason is privacy — but this handle is already
            # public on this instance (presence rosters, comment authors,
            # history trailers all carry it), and using it keeps
            # re-registration on one authenticator a *replacement* rather than
            # a second discoverable credential for the same account. Resolution
            # never depends on it: `find_by_passkey` goes by credential id.
            user_id=who.name.encode("utf-8"),
            user_name=who.name,
            user_display_name=who.name,
            # So the same authenticator cannot register twice and leave the
            # account with a credential it will never be offered.
            exclude_credentials=[
                structs.PublicKeyCredentialDescriptor(
                    id=wa.base64url_to_bytes(row["id"]))
                for row in existing],
            authenticator_selection=structs.AuthenticatorSelectionCriteria(
                # DISCOVERABLE, which is what makes the usernameless sign-in
                # below possible at all: without a resident key the browser has
                # nothing to offer when nobody has typed a handle.
                resident_key=structs.ResidentKeyRequirement.PREFERRED,
                user_verification=structs.UserVerificationRequirement.PREFERRED,
            ),
        )
        label = body.get("label")
        _challenges.put(_b64u(options.challenge), {
            "purpose": "register", "handle": who.name,
            "label": label if isinstance(label, str) else None})
        return json.loads(wa.options_to_json(options))

    @router.post("/auth/passkey/register/complete")
    async def passkey_register_complete(request: Request):
        cfg = _config()
        gate = _passkey_gate()
        if gate is not None:
            return gate
        who = _passkey_person()
        wa = _webauthn()
        body = await _body(request)
        credential = _credential(body)
        record = _challenges.take(_client_challenge(credential))
        if (record is None or record.get("purpose") != "register"
                or record.get("handle") != who.name):
            raise _refused(
                AuthError("this registration has expired; start again"),
                "passkey_register", principal=who.client_id,
                outcome="stale_challenge")
        rp_id, origin = _relying_party(cfg)
        try:
            verified = wa.verify_registration_response(
                credential=json.dumps(credential),
                # The challenge WE minted, looked up by the one the client
                # named. Never the client's own bytes.
                expected_challenge=wa.base64url_to_bytes(record["challenge"]),
                expected_origin=origin,
                expected_rp_id=rp_id,
            )
        except Exception as exc:            # noqa: BLE001 — the library's own
            raise _refused(
                AuthError(f"this passkey could not be verified "
                          f"({type(exc).__name__})"),
                "passkey_register", principal=who.client_id,
                outcome="unverified") from exc
        transports = []
        response = credential.get("response")
        if isinstance(response, dict) and isinstance(response.get("transports"), list):
            transports = [t for t in response["transports"] if isinstance(t, str)]
        row = cfg.store.add_passkey(
            who.name,
            credential_id=_b64u(verified.credential_id),
            public_key=_b64u(verified.credential_public_key),
            sign_count=verified.sign_count,
            label=record.get("label") or sec._device(request.headers, None)  # noqa: SLF001
            or "passkey",
            transports=transports,
            backed_up=verified.credential_backed_up,
        )
        _audit("passkey_register", principal=who.client_id,
               args={"credential_id": row["id"], "label": row.get("label")})
        return {"handle": who.name,
                **{k: v for k, v in row.items() if k != "public_key"}}

    @router.post("/auth/passkey/login/begin")
    async def passkey_login_begin(request: Request):
        """Options for a sign-in. Anonymous, so it says nothing.

        A `handle` may be sent (the handle-first flow, for a browser that
        cannot offer a discoverable credential), but an unknown handle — or one
        with no passkeys — gets **the same** usernameless challenge a known one
        with none would: an `allowCredentials` that is empty for strangers and
        populated for members is a user-enumeration oracle on an unauthenticated
        endpoint, which is exactly what `_LOGIN_FAILED` exists to avoid.
        """
        cfg = _config()
        gate = _passkey_gate()
        if gate is not None:
            return gate
        _address_budget(cfg, request)
        wa = _webauthn()
        from webauthn.helpers import structs

        body = await _body(request)
        handle = body.get("handle")
        allow, bound = [], None
        if isinstance(handle, str) and handle:
            rows = cfg.store.get_passkeys(handle)
            if rows:
                bound = handle
                allow = [structs.PublicKeyCredentialDescriptor(
                    id=wa.base64url_to_bytes(row["id"])) for row in rows]
        rp_id, _origin = _relying_party(cfg)
        options = wa.generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=allow or None,
            user_verification=structs.UserVerificationRequirement.PREFERRED,
        )
        _challenges.put(_b64u(options.challenge),
                        {"purpose": "login", "handle": bound})
        return json.loads(wa.options_to_json(options))

    @router.post("/auth/passkey/login/complete")
    async def passkey_login_complete(request: Request):
        """Verify an assertion and open a session. Every refusal is
        `_LOGIN_FAILED` — the same one `POST /auth/login` gives, for the same
        reason."""
        cfg = _config()
        gate = _passkey_gate()
        if gate is not None:
            return gate
        wa = _webauthn()
        body = await _body(request)
        credential = _credential(body)
        record = _challenges.take(_client_challenge(credential))
        # Every refusal below is the same `_LOGIN_FAILED` to the caller and a
        # DIFFERENT `outcome` in the audit row. That is not a leak: the row is
        # readable only by an administrator, and "which of the five ways did it
        # fail" is exactly what an operator needs and an attacker must not be
        # told. The response stays indistinguishable.
        if record is None or record.get("purpose") != "login":
            raise _refused(AuthError(_LOGIN_FAILED), "login",
                           principal="unknown", outcome="stale_challenge")
        raw_id = credential.get("rawId") or credential.get("id")
        found = cfg.store.find_by_passkey(raw_id if isinstance(raw_id, str) else "")
        if found is None:
            raise _refused(AuthError(_LOGIN_FAILED), "login",
                           principal=_claimed(record.get("handle")),
                           outcome="unknown_credential")
        if record.get("handle") and record["handle"] != found["handle"]:
            raise _refused(AuthError(_LOGIN_FAILED), "login",
                           principal=_claimed(record["handle"]),
                           outcome="credential_mismatch")
        user = cfg.store.get_user(found["handle"])
        if user is None or user["disabled"]:
            # `admin user disable` must stop a passkey exactly as it stops a
            # password. The user row is the authority, as it is everywhere
            # else in this file.
            raise _refused(AuthError(_LOGIN_FAILED), "login",
                           principal=_claimed(found["handle"]),
                           outcome="disabled")
        rp_id, origin = _relying_party(cfg)
        try:
            verified = wa.verify_authentication_response(
                credential=json.dumps(credential),
                expected_challenge=wa.base64url_to_bytes(record["challenge"]),
                expected_origin=origin,
                expected_rp_id=rp_id,
                credential_public_key=wa.base64url_to_bytes(found["public_key"]),
                # The stored counter. A cloned authenticator replays a
                # *lower* one and the library refuses — which is the only
                # thing the counter is for.
                credential_current_sign_count=int(found.get("sign_count") or 0),
            )
        except Exception as exc:            # noqa: BLE001 — the library's own
            raise _refused(AuthError(_LOGIN_FAILED), "login",
                           principal=_claimed(found["handle"]),
                           outcome="unverified") from exc
        cfg.store.update_sign_count(found["handle"], found["id"],
                                    verified.new_sign_count)
        device = sec._device(request.headers, None)          # noqa: SLF001
        secret = cfg.store.create_session(found["handle"], device)
        row = cfg.store.resolve_session(secret)
        if row is None:
            raise _refused(AuthError(_LOGIN_FAILED), "login",
                           principal=_claimed(found["handle"]),
                           outcome="disabled")
        who = sec.Principal(kind="user", name=found["handle"], role=row["role"],
                            device=device, via="cookie")
        _audit("login", principal=who.client_id, args={"via": "passkey"})
        return _set_session(cfg, JSONResponse({**_identity(cfg, who),
                                               "via": "passkey"}), secret)

    return router


#: The four-place trust statement (design Decision 1). The other three are
#: `docs/deployment.md`, the `compose.yaml` header and `agentcad admin user
#: add --help` + its success output.
TRUST_SENTENCE = (
    "an account on this instance can execute arbitrary Python on the host; "
    "give one only to someone you would give a shell to"
)


def _positive(value: object, default: int, what: str, floor: int = 1) -> int:
    """A query-string integer, or a 422 that names the parameter.

    Not silently defaulted: `?limit=all` returning 200 rows would look like an
    answer to a question nobody asked. The upper bound is `AuditLog.query`'s,
    which clamps rather than refuses — a limit that is too large is a caller
    asking for everything, and everything is what `MAX_LIMIT` means.
    """
    if value is None or value == "":
        return default
    try:
        number = int(str(value))
    except ValueError as exc:
        raise ValidationError(f"{what} must be an integer",
                              {what: str(value)[:32]}) from exc
    if number < floor:
        raise ValidationError(f"{what} must be at least {floor}",
                              {what: number})
    return number


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
