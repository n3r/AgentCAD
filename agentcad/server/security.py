"""Request authorization for hosted mode: default deny, with one allowlist.

**Not `kernel/sandbox.py`.** That module is *process confinement* and PRD-006
owns it. This one is *request authorization*. Neither is the other, and on
Linux the first does not exist yet — which is the fact the whole feature is
built around (design spec, Decision 1).

The shape, in one paragraph. ``create_app`` takes an explicit
``security=SecurityConfig`` and its one middleware delegates here; with
``security=None`` the middleware runs today's local code path *unchanged*, so
"local mode is untouched" is a property of the diff rather than a test we have
to keep passing. Everything not named in :data:`PUBLIC_PATHS` /
:data:`PUBLIC_PREFIXES` is ``401`` — a route pack added tomorrow is private
with no action by its author, and there is deliberately **no** per-route
``@public`` decorator, because a pack author must not be able to open the
anonymous surface from their own file.

**Why this is not a discovered pack.** ``_mount_route_packs`` and
``_load_tool_packs`` both fail *open*: a module with no ``router`` is silently
skipped. A security middleware that silently failed to load would leave the
instance wide open with no signal anywhere. Auth is constructed explicitly by
the caller and fails closed. That is the entire argument for the one
sanctioned ``app.py`` edit (design spec, Decision 7).

The guard, in the order Decision 7 fixes:

1. No config (local mode) → not our business; the caller runs today's path.
2. ``Host`` must equal the configured public origin's host.
3. Resolve a principal: ``Authorization: Bearer`` first, then the session
   cookie.
4. State-changing methods that are not bearer-authenticated: an ``Origin``,
   when present, must equal the configured public origin → else ``403``.
   **Before** the anonymous branch, because ``login`` and ``enrol`` are the
   only anonymous unsafe methods and are exactly what it protects.
5. No principal + a public path → allow, anonymously, with **no** client id.
6. No principal + anything else → ``401``.
7. ``set_client_id`` the composed principal, **after** ``check_client_id``
   validation — closing the gap where the ContextVar is set unvalidated today.
8. Roles are checked in ``routes_auth.py``, not here: there are five
   admin-only handlers and a path-pattern list would be a second place to get
   it wrong.
"""

from __future__ import annotations

import math
import re
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi.responses import JSONResponse

from ..core import authz, tenancy
from ..core.appmode import AppMode, _hostname
from ..core.authstore import AuthStore
from ..core.locks import check_client_id, set_client_id
from ..core.model import ValidationError
from ..core.ratelimit import TokenBucket
from ..core.tenancy import Tenant, TenancyStore

#: The session cookie's name. `HttpOnly`, `SameSite=Lax`, `Path=/`, and
#: `Secure` whenever the public origin is https (`AppMode.secure_cookies`).
SESSION_COOKIE = "agentcad_session"

#: Exact paths reachable with no credential in hosted mode.
#:
#: **This frozenset is the entire anonymous attack surface of a 005-lite
#: instance.** One careless entry widens it, which is why it is a literal in
#: one file and why `tests/test_hosted_surface.py` asserts the reachable set
#: by equality rather than by subset — a forgotten removal must not pass
#: silently. Nothing here reaches the kernel; that is proved, not asserted
#: (FR16/AC7).
PUBLIC_PATHS: frozenset[str] = frozenset({
    "/",                        # index.html off disk; the app needs a login page
    "/api/health",              # trimmed to {status, mode} without a principal
    "/api/auth/login",          # rate limited, indistinguishable failures
    # PRD-005 slice 3 (FR1) — the four doors a person who is NOT yet signed in
    # has to be able to knock on, added as **exact paths and not a prefix**:
    # `/api/auth/oidc/` would have opened `POST /api/auth/oidc/link` (which
    # must stay authenticated — see `routes_auth.oidc_link_begin` for why a
    # link started by a GET is an account-takeover shape), and
    # `/api/auth/passkey/` would have opened the register ceremony, which is
    # how an anonymous caller would add a credential to somebody's account.
    # None reaches the kernel; each is rate limited on the same per-address
    # bucket `/api/auth/login` uses, and each refusal is the same one.
    "/api/auth/oidc/login",     # 302 to the provider; 404 when unconfigured
    "/api/auth/oidc/callback",  # state+nonce+PKCE checked before anything else
    "/api/auth/passkey/login/begin",     # a challenge is a nonce, not a secret
    "/api/auth/passkey/login/complete",  # verified assertion -> session cookie
})

#: Prefix rules, matched against the path (and, in the enumeration test,
#: against the *route template*, which is why `/api/auth/enrol/` matches
#: `/api/auth/enrol/{token}`).
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/public/",             # the scope=="public" catalog read (slice 7)
    "/api/auth/enrol/",         # single-use, 7-day, admin-minted, unguessable
    "/js/",
    "/css/",
    "/vendor/",
    "/s/",                      # PRD-007 share links — the token is in the path
    "/embed/",                 # PRD-007 embeds — every handler validates the token
)

#: PRD-007 share/embed routes carry a capability token in the path and every
#: handler validates it. **The trailing slash is load-bearing**: `is_public` is
#: `startswith`, so `/s` would make `/status` public and `/embed` would make
#: `/embedding` public (the negation tests in `test_hosted_surface.py` pin it).

#: The main app's frame policy (founder decision, 2026-08-18): the authenticated
#: surface must not be frameable, so hosted responses carry
#: `Content-Security-Policy: frame-ancestors 'none'`. The public **embed** page
#: opts out — it sets its own `frame-ancestors *` so any site may embed the
#: auth-free customizer (the growth loop) — so it is excluded here. This is a
#: response HEADER, not a route: it adds nothing to the reachable set the
#: anonymous-surface equality test enumerates.
FRAME_ANCESTORS_NONE = "frame-ancestors 'none'"
EMBED_PREFIX = "/embed/"

#: Methods that change state and therefore carry the CSRF rule.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: A device suffix contributed by `X-Agent-Id`, e.g. the browser's own
#: `browser:7f3a1b2c` (`frontend/js/api.js`).
#:
#: Bounded so the composed identity cannot exceed
#: `locks.MAX_CLIENT_ID_CHARS`, which **refuses rather than truncates**:
#: `user:` (5) + handle (≤32) + `/` (1) + device (≤24) = ≤62.
#:
#: At most ONE colon, and never a reserved prefix. Both matter: a device of
#: `user:anya` would compose to `user:nikita/user:anya`, which is not an
#: impersonation — the principal is still nikita — but it is a string the
#: presence roster, the claim map and the comment author line all render, and
#: an identity that reads as two people is a lie told by a display.
DEVICE_MAX_CHARS = 24
DEVICE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,10}(:[a-z0-9._-]{1,12})?$")
RESERVED_DEVICE_PREFIXES = ("user:", "agent:")

# --- rate limiting ----------------------------------------------------------
# `TokenBucket` is IMPORTED, never re-implemented. PRD-005a took it from
# `presence`; PRD-007 promoted it to `core/ratelimit.py` (design Decision 6),
# so the import moved here — behaviour byte-identical, the same shared class the
# share/customizer limiter uses.
#
# rate=0.2/s, burst=5: five attempts immediately, then one every five seconds.
# NIST SP 800-63B 5.2.2 asks that consecutive failed attempts be throttled to
# at most 100; at this setting the hundredth failure is ~475 s (~8 min) away,
# and a sustained attacker costs 0.2 x 63 ms = 1.3% of one core in scrypt.
#
# **The key is `(handle, address)`, never the handle alone** (`routes_auth`,
# `_login_key`). `TokenBucket.take` does not consume on refusal, so a bucket
# held empty stays empty: a handle-only key let any third party at 0.5 req/s
# lock a *known* handle out of every address on the internet, permanently, and
# handles are public — presence rosters, comment authors, history trailers all
# carry them. That is the PRD-005a security review's finding M3, and the fix
# is the key rather than the rate. What it costs: an attacker with many
# addresses now gets `burst` guesses per address against one handle instead of
# five in total. What bounds them is the per-address bucket below, which is
# unchanged, plus scrypt at 63 ms — a botnet is not what a token bucket keyed
# on anything was ever going to stop, and denying the account's owner in the
# attempt is the worse of the two failures.
LOGIN_RATE_PER_S = 0.2
LOGIN_BURST = 5.0
# Addresses are looser than handles: a NAT, an office or a CI runner is many
# people behind one address, and locking them out together is a denial of
# service against the innocent ones.
ADDRESS_RATE_PER_S = 0.5
ADDRESS_BURST = 15.0


def login_key(handle: str, address: str) -> str:
    """The per-attempt bucket key for a sign-in: ``handle:<handle>@<address>``.

    Both halves, and here rather than inline in ``routes_auth`` so the reason
    lives next to the rate that depends on it (see ``LOGIN_RATE_PER_S``). The
    handle is bounded because it arrives unvalidated from the request body and
    an unbounded key is an unbounded dict.

    ``address`` is only as honest as the deployment. It is ``request.client.host``,
    which behind the reverse proxy the deployment guide prescribes is the
    **real client** — but only because ``cli._uvicorn_proxy_kwargs`` runs uvicorn
    with ``proxy_headers`` bounded to the trusted proxy and the proxy sets
    ``X-Forwarded-For`` (`docs/deployment.md`). Without that plumbing every
    client collapses to the proxy's address and this key degrades to
    per-handle — the site-wide lockout of review finding M3 (round 2). The
    security property is a property of the whole path, not of this function.
    """
    return f"handle:{(handle or '')[:64]}@{address or '?'}"


@dataclass(frozen=True)
class Principal:
    """Who a request is. A *string* is what the product already consumes
    (``locks.set_client_id``), so this composes into one and nothing
    downstream learns about authentication."""

    kind: str                 # "user" | "agent"
    name: str                 # handle, or token name
    role: str                 # "admin" | "member"
    device: str | None = None
    #: How the credential arrived. Load-bearing: the CSRF rule exempts
    #: bearers, because a browser cannot attach one cross-site.
    via: str = "cookie"       # "cookie" | "bearer"
    #: A bearer token's ``{org, workspace, ...}`` scope (PRD-005 FR3), read
    #: **defensively** off whatever `AuthStore.resolve_token` returns: slice 5
    #: adds the field, and until it does every token answers ``None`` and every
    #: reader here takes its untenanted branch. Never trusted as a grant — it
    #: only *selects* a tenant, which is then checked against the roles
    #: document like any other selection (:func:`resolve_tenant`).
    scope: dict | None = None
    #: The session's active workspace, ``"org/ws"``, if the session row carries
    #: one. Also defensive: nothing writes it yet (the switcher is slice 8),
    #: so it is ``None`` everywhere today and the header is the API client's
    #: way to say the same thing.
    workspace: str | None = None

    @property
    def client_id(self) -> str:
        """``user:nikita/browser:7f3a1b2c`` or ``agent:ci``.

        The device suffix is kept because it is what makes two tabs one client
        and two browsers two clients, which the per-client branch checkout and
        presence both rely on.
        """
        if self.kind == "user" and self.device:
            return f"user:{self.name}/{self.device}"
        return f"{self.kind}:{self.name}"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass
class SecurityConfig:
    """Everything the guard needs. Constructed by ``create_app``'s *caller*,
    never by ``AgentCADService`` — which is exactly why PRD-004/011's
    ephemeral services are unaffected by this feature *by construction*."""

    mode: AppMode
    store: AuthStore
    login_rate: TokenBucket = field(
        default_factory=lambda: TokenBucket(rate=LOGIN_RATE_PER_S,
                                            burst=LOGIN_BURST))
    address_rate: TokenBucket = field(
        default_factory=lambda: TokenBucket(rate=ADDRESS_RATE_PER_S,
                                            burst=ADDRESS_BURST))

    def __post_init__(self) -> None:
        # Fail closed on a nonsense construction rather than quietly running a
        # local-mode AppMode through a guard that assumes an origin exists.
        if not self.mode.hosted:
            raise ValueError(
                "SecurityConfig requires a hosted AppMode; local mode is the "
                "same code path as today and is expressed by passing no "
                "security= at all, not by a disabled config.")

    def tenancy(self) -> TenancyStore:
        """The orgs document, on the identity root, built once per config.

        A method and not a constructor argument: every existing caller builds
        ``SecurityConfig(mode=…, store=…)`` and none of them should have to
        learn about tenancy to keep working. Memoized on the instance (not as
        a dataclass field) so equality and ``repr`` are unchanged, and built on
        ``store.root`` because that is what makes ``orgs.json`` share
        ``AuthStore``'s flock rather than deadlock against it.
        """
        found = self.__dict__.get("_tenancy_store")
        if found is None:
            found = TenancyStore(self.store.root)
            self.__dict__["_tenancy_store"] = found
        return found

    def retry_after_s(self, bucket: TokenBucket) -> int:
        """Seconds until a refused caller may try again.

        Derived from the bucket's rate rather than read from it:
        ``TokenBucket`` exposes no remaining-token API and PRD-008 code is not
        ours to edit. One token's worth of refill is the honest upper bound on
        "not yet".
        """
        rate = getattr(bucket, "_rate", LOGIN_RATE_PER_S) or LOGIN_RATE_PER_S
        return max(1, math.ceil(1.0 / rate))


# --------------------------------------------------------------- ambient state

#: Set per request by the guard. A ContextVar rather than a request attribute
#: because tool packs and the trimmed health body read it far from the route.
_principal_var: ContextVar[Principal | None] = ContextVar(
    "agentcad_principal", default=None)
#: Per request, so a process hosting two apps cannot answer with the other
#: one's configuration.
_config_var: ContextVar["SecurityConfig | None"] = ContextVar(
    "agentcad_security_config", default=None)
#: Set once by ``create_app``. The fallback for callers that are not inside a
#: request — tool *registration*, and the CLI.
_module_config: SecurityConfig | None = None


def install(cfg: SecurityConfig | None) -> None:
    """Record the app's security configuration. Called by ``create_app``."""
    global _module_config
    _module_config = cfg


def current_config() -> SecurityConfig | None:
    """This request's security configuration, else the app's, else ``None``.

    ``None`` means local mode, and every caller treats it as "behave exactly
    as before" — the hardening in slice 5 and the tools in slice 4 all hang
    off this being falsy.
    """
    return _config_var.get() or _module_config


def current_principal() -> Principal | None:
    """Who this request is, or ``None`` for anonymous (or local mode)."""
    return _principal_var.get()


def is_hosted() -> bool:
    cfg = current_config()
    return bool(cfg and cfg.mode.hosted)


# --------------------------------------------------------------------- paths

def response_headers(path: str) -> dict[str, str]:
    """Security headers the guard stamps on a hosted response.

    The authenticated surface must not be frameable (founder decision), so every
    hosted response carries ``frame-ancestors 'none'`` — except the ``/embed/``
    page, which sets its own ``frame-ancestors *`` so any site may embed the
    public customizer. Applied with ``setdefault`` at the middleware, so a
    handler that set its own policy (the embed page) wins.
    """
    if isinstance(path, str) and path.startswith(EMBED_PREFIX):
        return {}
    return {"Content-Security-Policy": FRAME_ANCESTORS_NONE}


def is_public(path: str) -> bool:
    """Is *path* reachable with no credential?

    Takes a route **template** as happily as a concrete path, because the
    enumeration test walks ``app.routes``.
    """
    if not isinstance(path, str):
        return False
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


# ---------------------------------------------------------------- responses

def _deny(status: int, kind: str, message: str, details: dict | None = None
          ) -> JSONResponse:
    """The house error contract, built here rather than raised.

    The guard runs *before* the exception handlers are in scope for some
    paths, and an auth failure that became a 500 would be both a worse answer
    and a louder one. So it is always a response, never an exception.
    """
    return JSONResponse(
        status_code=status,
        content={"error": {"type": kind, "message": message,
                           "details": details or {}}},
    )


# ------------------------------------------------------------ principals

def resolve_principal(cfg: SecurityConfig, headers, cookies) -> Principal | None:
    """The credential on this request, or ``None``.

    Bearer first, then the session cookie. **A present-but-invalid bearer does
    not fall back to the cookie**: a revoked token that quietly became a
    browser session would be a confused deputy, and "my token stopped working"
    is the answer the holder needs to see.

    Never raises. It is reached by an anonymous request carrying an attacker's
    header, and a traceback here would be a 500 that says the header was
    interesting. A store that cannot be read yields *no* principal, which
    fails closed.
    """
    try:
        authorization = headers.get("authorization") or ""
        if authorization:
            scheme, _, presented = authorization.partition(" ")
            if scheme.lower() != "bearer" or not presented.strip():
                return None
            row = cfg.store.resolve_token(presented.strip())
            if row is None:
                return None
            return Principal(kind="agent", name=row["name"], role=row["role"],
                             device=None, via="bearer",
                             scope=_row_field(row, "scope", dict))

        secret = cookies.get(SESSION_COOKIE) if cookies else None
        if not secret:
            return None
        row = cfg.store.resolve_session(secret)
        if row is None:
            return None
        return Principal(kind="user", name=row["handle"], role=row["role"],
                         device=_device(headers, row.get("device")),
                         via="cookie",
                         workspace=_row_field(row, "active_workspace", str)
                         or _row_field(row, "workspace", str))
    except Exception:                       # noqa: BLE001 — fail closed
        return None


#: The header an API client uses to say which workspace it is talking to.
#: ``org/ws``, one slash, both halves the ``ID_RE`` grammar. Lower-cased
#: because ASGI headers are.
WORKSPACE_HEADER = "x-agentcad-workspace"

#: Route templates under this prefix are **not** the generic ``{proj}`` shape:
#: `/git/{org}/{ws}/{proj}.git/…` carries its own tenant in the URL and is
#: floored by `routes_sync.require_role`, which `tenancy_wiring` wires. Letting
#: the matcher below read its `{proj}` would apply *this request's* tenant to
#: another one's path.
_SYNC_PREFIX = "/git/"

#: Which path parameter names a project. Two spellings because the API packs
#: use `{proj}` and nothing forbids a future pack from spelling it out.
_PROJECT_PARAMS = ("proj", "project")


def resolve_tenant(cfg: SecurityConfig, principal: Principal | None, headers
                   ) -> tuple[Tenant | None, JSONResponse | None]:
    """This request's ``(org, workspace)``, or a refusal to return.

    Precedence, and the reason for each rung:

    1. **A bearer token's scope.** A scoped token *is* a tenant statement made
       by the admin who minted it, so nothing the caller sends may redirect it
       — the header is not even read when a scope is present, and the
       membership check below is not applied to it (FR3: an agent is never an
       org member, so requiring membership would make every token unusable).
    2. **``X-Agentcad-Workspace: org/ws``** — how an API client, the MCP
       server and a browser that has switched workspaces say where they are.
    3. **The session's active workspace**, when the session row carries one
       (slice 8's switcher writes it; today it never does).
    4. **The principal's own memberships**, when they name exactly one
       ``(org, workspace)`` — a single-org instance then needs no header at
       all. With several, the alphabetically first is the default, stated here
       rather than discovered: dropping such a caller to the untenanted root
       instead would quietly create their projects outside every org.
    5. Otherwise **``None``** — local mode, and a hosted instance with no orgs
       (a PRD-005a instance), which is byte-for-byte what it was.

    **Every rung is checked against the roles document.** A selection is only
    a selection; the answer to "may this principal be there at all" is
    ``authz.role_of(..., proj=None)``, and a selection that fails it is a
    **name-free 404** rather than a 403 — a 403 would confirm that the org and
    workspace exist, which is the existence oracle FR5 forbids. A malformed
    header is the one exception: it is a client bug, it confirms nothing, and
    it gets a 400 that says so.
    """
    if principal is None:
        return None, None
    store = cfg.tenancy()
    who = principal.client_id

    scoped = _scope_tenant(principal.scope)
    if scoped is not None:
        # **A scope is not caller input**: it is a field of the credential the
        # admin minted, so it *places* the request without a membership check
        # — an agent principal has no org default by design (FR3), so asking
        # for one here would make every scoped token unusable. What the token
        # may then do is still decided per project, by an explicit grant.
        # Existence is checked, name-free, so a scope pointing at a deleted
        # workspace fails as a miss rather than as a root nobody chose.
        if not store.has_workspace(scoped[0], scoped[1]):
            return None, _deny(404, "NotFoundError", "no such workspace", {})
        return scoped, None

    selected = None
    raw = (headers.get(WORKSPACE_HEADER) or "").strip() if headers else ""
    if raw:
        try:
            selected = parse_workspace(raw)
        except ValidationError as exc:
            return None, _deny(400, "ValidationError", exc.message,
                               exc.details)
    elif principal.workspace:
        selected = _scope_tenant({"workspace": principal.workspace})

    if selected is None:
        return _default_tenant(store, who), None

    if authz.role_of(store, who, selected[0], selected[1]) is None:
        # Name-free: the same answer whether the org does not exist, the
        # workspace does not, or this principal simply holds nothing there.
        return None, _deny(404, "NotFoundError", "no such workspace", {})
    return selected, None


def parse_workspace(raw: object) -> Tenant:
    """``"acme/main"`` -> ``("acme", "main")``, or ``ValidationError``.

    Exactly one slash and both halves through ``tenancy.check_name``, because
    the pair composes into a directory path and into a lock key.
    """
    if not isinstance(raw, str) or raw.count("/") != 1:
        raise ValidationError(
            f"{WORKSPACE_HEADER} must be 'org/workspace'",
            {"header": WORKSPACE_HEADER})
    org, _, workspace = raw.partition("/")
    return (tenancy.check_name(org, "org"),
            tenancy.check_name(workspace, "workspace"))


def _scope_tenant(scope: object) -> Tenant | None:
    """``(org, workspace)`` out of a token scope, or ``None``.

    Total over the value on purpose (PRD-012's lesson): the scope is written by
    a slice that has not landed, read on every request, and a shape we did not
    expect must resolve as *no selection* rather than raise inside the guard.
    A scope naming an org but no workspace is not a tenant — it is a token that
    still has to be told which workspace, and the header may say so.
    """
    if not isinstance(scope, dict):
        return None
    org = scope.get("org")
    workspace = scope.get("workspace") or scope.get("ws")
    if workspace is None and isinstance(org, str) and "/" in org:
        org, _, workspace = org.partition("/")
    try:
        if isinstance(org, str) and isinstance(workspace, str):
            return (tenancy.check_name(org, "org"),
                    tenancy.check_name(workspace, "workspace"))
    except ValidationError:
        return None
    return None


def _default_tenant(store: TenancyStore, who: str) -> Tenant | None:
    """The principal's own workspace when they have one, else ``None``.

    Sorted, so "the first" is a fact and not an artifact of dict order. An
    agent principal is never an org member (``tenancy.handle_of``), so a token
    with no scope resolves to no tenant — conservative, and the direction a
    token should fail in.
    """
    try:
        for org in store.orgs_for(who):
            for workspace in store.list_workspaces(org):
                return (org, str(workspace["id"]))
    except Exception:                       # noqa: BLE001 — a broken document
        return None                         # denies rather than raises
    return None


# ------------------------------------------------------- project-route floor

def _template_regex(template: str) -> "re.Pattern | None":
    """A regex over concrete paths for one route template, or ``None``.

    ``None`` when the template binds no project parameter. The group is named
    ``proj`` whichever spelling the template used, so the caller reads one
    name.
    """
    pattern, found = "", False
    for piece in re.split(r"(\{[^{}]*\})", template):
        if not piece:
            continue
        if piece.startswith("{") and piece.endswith("}"):
            name, _, converter = piece[1:-1].partition(":")
            body = ".+" if converter == "path" else "[^/]+"
            if name in _PROJECT_PARAMS and not found:
                found = True
                pattern += f"(?P<proj>{body})"
            else:
                pattern += body
        else:
            pattern += re.escape(piece)
    return re.compile(f"^{pattern}/?$") if found else None


def project_routes(app) -> tuple[frozenset[str], tuple]:
    """``(literal paths, compiled {proj} matchers)`` for *app*, computed once.

    Built from the app's own routes rather than from a hand-kept prefix list,
    so a route pack that mounts project-bound routes somewhere new is floored
    the day it lands. It walks ``include_context`` the way
    ``tests/conftest.flatten_routes`` does, because FastAPI does not flatten
    ``include_router`` and a naive ``app.routes`` walk sees 23 routes of 83.

    The literal set is consulted first and it is load-bearing: ``POST
    /api/projects/open`` matches ``/api/projects/{proj}``, and reading it as a
    project called ``open`` would demand a role on a project nobody has.

    Cached on ``app.state`` — the app is the thing that owns the answer, and a
    module-level cache would be wrong in a process serving two apps (the test
    suite's normal state).
    """
    cached = getattr(app.state, "_agentcad_project_routes", None)
    if cached is not None:
        return cached
    literals: set[str] = set()
    matchers: list = []
    seen: set[str] = set()

    def walk(routes, prefix=""):
        for route in routes:
            context = getattr(route, "include_context", None)
            if context is not None:                 # FastAPI's _IncludedRouter
                walk(context.included_router.routes,
                     prefix + (getattr(context, "prefix", "") or ""))
                continue
            path = getattr(route, "path", None)
            if not isinstance(path, str):
                continue
            template = prefix + path
            if template in seen:
                continue
            seen.add(template)
            if template.startswith(_SYNC_PREFIX):
                continue                            # its own tenant, its own floor
            if "{" not in template:
                literals.add(template.rstrip("/") or "/")
                continue
            compiled = _template_regex(template)
            if compiled is not None:
                matchers.append(compiled)

    walk(getattr(app, "routes", []) or [])
    answer = (frozenset(literals), tuple(matchers))
    try:
        app.state._agentcad_project_routes = answer
    except Exception:                       # noqa: BLE001 — an app without state
        pass
    return answer


def project_of(app, path: str) -> str | None:
    """The project this request addresses, or ``None`` if it addresses none."""
    if not isinstance(path, str) or not path:
        return None
    if (path.rstrip("/") or "/") in project_routes(app)[0]:
        return None
    for matcher in project_routes(app)[1]:
        found = matcher.match(path)
        if found is not None:
            return found.group("proj")
    return None


def _row_field(row: object, key: str, kind: type):
    """``row[key]`` when it is a *kind*, else ``None``.

    The identity documents grow fields in later slices (a token's scope, a
    session's active workspace) and this file must read them before they
    exist and survive them arriving misshapen. One helper so that "read it
    defensively" is a fact about the code rather than a promise in a comment.
    """
    if not isinstance(row, dict):
        return None
    found = row.get(key)
    return found if isinstance(found, kind) else None


def _device(headers, stored: str | None) -> str | None:
    """The ``<device>`` suffix, and *only* the suffix.

    In hosted mode ``X-Agent-Id`` is never an identity — at most it names which
    of this person's browsers is talking, namespaced under the authenticated
    principal. Anything that does not match the bounded grammar is dropped
    rather than sanitised, and the session's own stored device is the
    fallback.
    """
    for candidate in ((headers.get("x-agent-id") or "").strip(), stored):
        if not candidate or not isinstance(candidate, str):
            continue
        if len(candidate) > DEVICE_MAX_CHARS:
            continue
        if candidate.startswith(RESERVED_DEVICE_PREFIXES):
            continue
        if DEVICE_RE.match(candidate):
            return candidate
    return None


# -------------------------------------------------------------------- guard

def guard(cfg: SecurityConfig | None, request) -> JSONResponse | None:
    """``None`` to allow, else the refusal to return. Never raises."""
    if cfg is None:                          # local mode: not our business
        return None
    _config_var.set(cfg)
    _principal_var.set(None)
    tenancy.tenant_var.set(None)

    host = _hostname(request.headers.get("host", ""))
    if host != cfg.mode.origin_host:
        # The hosted replacement for the loopback Host allowlist. A rebound
        # evil.com arrives carrying Host: evil.com, which is not ours.
        return _deny(403, "ForbiddenOrigin",
                     f"disallowed Host {host!r}",
                     {"expected": cfg.mode.origin_host})

    principal = resolve_principal(cfg, request.headers, request.cookies)
    _principal_var.set(principal)
    path = request.url.path
    method = (request.method or "GET").upper()

    # CSRF. `SameSite=Lax` is the first layer; this is the second.
    #
    # **It runs BEFORE the anonymous branch below, and that ordering is the
    # whole control.** `POST /api/auth/login` and `POST /api/auth/enrol/{token}`
    # are the only unsafe methods an anonymous caller can reach, so a check
    # placed after `principal is None: return None` would cover every route
    # except the two it exists for — which is what it did until the PRD-005a
    # security review (finding M1). A cross-site POST that signs a victim into
    # the ATTACKER's account, or that spends an enrolment link the victim
    # pasted, is a real if quiet attack.
    #
    # A bearer is exempt because a browser cannot attach one cross-site, and
    # `principal is None` is treated as "not a bearer" — an anonymous request
    # carries no credential to exempt.
    #
    # Origin-*absent* is allowed, deliberately and identically for anonymous
    # and authenticated callers: a browser always sends `Origin` on a
    # cross-site POST (and on any `Content-Type` it could forge from a form),
    # while `curl`, the MCP client and a same-origin `fetch` may omit it.
    # Refusing on absence would break every non-browser client without
    # stopping a browser attack.
    if method in UNSAFE_METHODS and (principal is None
                                     or principal.via != "bearer"):
        origin = request.headers.get("origin")
        if origin is not None and origin != cfg.mode.public_origin:
            return _deny(403, "ForbiddenOrigin",
                         f"cross-origin request from {origin!r} rejected",
                         {"expected": cfg.mode.public_origin})

    if principal is None:
        if is_public(path):
            return None                      # anonymous, and no client id
        return _deny(401, "AuthError", "authentication required",
                     {"path": path})

    try:
        set_client_id(check_client_id(principal.client_id))
    except ValidationError as exc:
        # Unreachable while the handle and device grammars hold; kept because
        # the alternative to refusing an over-long identity is a silent
        # identity MERGE in the claim map and the roster.
        return _deny(400, "ValidationError", exc.message, exc.details)

    # Tenancy (PRD-005 FR5), beside `set_client_id` and for the same reason:
    # this is the one place a request's ambient identity is established, and
    # everything downstream — the store's root resolver, the lock keys, the
    # event stamp — reads the ContextVar rather than being handed a tenant.
    #
    # The ContextVar is set here, in the middleware, and never reset: Starlette
    # runs the downstream app in a task whose context is COPIED from this one,
    # so a reset afterwards would restore a variable the endpoint never saw,
    # and every hosted request passes through this line before it can read one.
    tenant, refusal = resolve_tenant(cfg, principal, request.headers)
    if refusal is not None:
        return refusal
    tenancy.tenant_var.set(tenant)
    if tenant is None:
        return None                          # no orgs: a PRD-005a instance

    # The read floor (FR6). A route that binds a project needs `view` on it —
    # one rule over the mounted routes, not a per-route table, because a
    # per-route table is a second place to get it wrong. Mutation floors are
    # NOT here: they are the tool registry's wrapper and the store's write
    # guard (`core/tenancy_wiring.py`), both of which see the operation rather
    # than guessing it from a method.
    proj = project_of(request.app, request.url.path)
    if proj is None:
        return None
    try:
        authz.require(cfg.tenancy(), "view", principal.client_id,
                      tenant[0], tenant[1], proj)
    except authz.PermissionError as exc:
        # 403 and not 404: the caller can already see this workspace (they
        # resolved a tenant in it), so the honest answer is which role they
        # need. It is `require`'s message that stays existence-free.
        return _deny(403, "PermissionError", exc.message, exc.details)
    except Exception:                        # noqa: BLE001 — never a 500 here
        return _deny(403, "PermissionError", "not permitted", {})
    return None


class _ws_headers:
    """``ws.headers``, with ``?workspace=org/ws`` standing in for the header.

    A browser cannot set a header on a ``WebSocket`` — the API has no room for
    one — so the query string is the only way a switched workspace can reach
    the socket before the session carries an active one. Reading it here rather
    than in ``app.py`` keeps the ``/ws`` route untouched.
    """

    def __init__(self, ws) -> None:
        self._ws = ws

    def get(self, key, default=None):
        found = self._ws.headers.get(key, None)
        if found is None and key == WORKSPACE_HEADER:
            try:
                found = self._ws.query_params.get("workspace")
            except Exception:               # noqa: BLE001 — no query params
                found = None
        return default if found is None else found


def guard_websocket(cfg: SecurityConfig | None, ws) -> bool:
    """``True`` to accept the upgrade.

    Browsers do not apply the same-origin policy to WebSockets, and the bus
    fans every project event to every subscriber, so the socket is
    authenticated — there is no anonymous read of the event stream. The cookie
    rides the upgrade; a bearer works too, for an agent that watches events.
    """
    if cfg is None:
        return True
    _config_var.set(cfg)
    _principal_var.set(None)
    tenancy.tenant_var.set(None)
    if _hostname(ws.headers.get("host", "")) != cfg.mode.origin_host:
        return False
    origin = ws.headers.get("origin")
    if origin is not None and origin != cfg.mode.public_origin:
        return False
    principal = resolve_principal(cfg, ws.headers, ws.cookies)
    if principal is None:
        return False
    _principal_var.set(principal)
    try:
        set_client_id(check_client_id(principal.client_id))
    except ValidationError:
        return False
    # The connection's tenant, resolved once, here — and this is what the
    # event filter binds to. `/ws` subscribes to the bus on the next line of
    # `app.py`, in this same context, so `tenancy_wiring`'s subscribe wrapper
    # reads exactly the tenant established for this socket (there is no
    # per-message request to re-read it from). A refusal answers `False`: a
    # websocket has no body to put a 404 in, and a socket we cannot place is
    # a socket we must not fan events to.
    tenant, refusal = resolve_tenant(cfg, principal, _ws_headers(ws))
    if refusal is not None:
        return False
    tenancy.tenant_var.set(tenant)
    return True
