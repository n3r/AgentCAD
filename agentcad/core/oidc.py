"""OpenID Connect sign-in: authorization code + PKCE, on ``httpx`` + ``pyjwt``.

**Why hand-rolled and not authlib.** The PRD-005 spike measured it: authlib
buys ~55 lines and costs a dependency tree (`joserfc`) plus a 138 ms import,
while ``httpx`` and ``pyjwt[crypto]`` are already in this repo's closure —
``pyjwt[crypto]`` arrives as an undeclared transitive of ``mcp`` and is
promoted to an explicit dependency by the same change that adds this module.
The RP half of OIDC is a token exchange and a signature check; that is what
this file is.

**Why the JWKS is fetched by hand.** ``jwt.PyJWKClient`` does discovery,
caching and rollover for you — over ``urllib.request.urlopen`` (measured in
the spike: ``uses urllib: True, uses httpx: False``). A hosted instance behind
a corporate proxy, or with a private CA, configures ``httpx``; a fetch that
quietly ignores that configuration is a sign-in that fails in production and
works everywhere else. So the JWKS is fetched with ``httpx`` and handed to
:class:`jwt.PyJWK` as a parsed dict.

**Closed registration is the whole linking policy.** PRD-005a's Decision 1
holds: an account on this instance is arbitrary code execution on the host, so
registration is closed and *nothing here creates a handle*. An OIDC identity
either

* is **already linked** to a handle (``users.json``'s per-user ``oidc``
  field) — it signs that handle in; or
* is **auto-linked** by a *verified* email that the instance's own
  ``oidc.json`` maps to an existing handle (``email_handles``, written by an
  admin) — the invitation and the mapping are two separate admin acts; or
* is **explicitly linked** by a signed-in person (``POST
  /api/auth/oidc/link``, which is authenticated and therefore carries the
  guard's cross-origin check — linking on a bare ``GET`` would let a
  cross-site navigation bind an attacker's IdP identity to a victim's handle).

Anything else is refused with **one** message, because "no such mapping",
"disabled account" and "already linked elsewhere" told apart are an
enumeration oracle on an anonymous endpoint.

**Where the in-flight state lives.** ``state``/``nonce``/PKCE verifier are
held in :class:`PendingFlows` — in memory, per app, TTL-bounded and
size-capped. The alternative (a fifth ``authstore`` document) would put an
``flock`` + ``fsync`` on the sign-in path and add a second thing to prune, to
protect a record that lives ninety seconds and whose loss costs the user one
retry. It is safe because a hosted AgentCAD is **one process**: ``cli.cmd_serve``
calls ``uvicorn.run(app, ...)`` with no ``workers=``, and the kernel pool, the
event bus and the turn locks are already in-process singletons — a
multi-process deployment is not a thing this server supports today. If that
ever changes, this is the class to move into the store, and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field

import httpx
import jwt

from .model import AuthError, ConflictError, ValidationError

#: Test seam. ``httpx`` transport used for every call this module makes, so a
#: test can drive an in-process mock IdP with no network and no thread (see
#: ``tests/test_oidc.py``). ``None`` in production, which is the real network.
#: The same module-level indirection idiom as ``authstore._now``.
TRANSPORT: httpx.BaseTransport | None = None


def _now() -> float:
    """Module-level indirection so tests can move the clock. Every expiry and
    every cache lifetime in this file reads it."""
    return time.time()


#: The path the IdP redirects back to. Composed with the instance's public
#: origin into ``redirect_uri`` unless the document overrides it, because a
#: redirect URI that disagrees with the route is a sign-in that ends in a 404.
CALLBACK_PATH = "/api/auth/oidc/callback"

#: Signature algorithms an ID token may use. **``none`` is not on it and can
#: never be**: the allowlist is passed to ``jwt.decode`` verbatim, so an
#: unsigned token is refused by pyjwt before any claim is read (the spike's
#: `alg=none : rejected (InvalidAlgorithmError)`). HS* is absent too — a shared
#: -secret HMAC would make the client secret a signing key, which is not what
#: an IdP's JWKS is for.
ID_TOKEN_ALGORITHMS = ("RS256", "RS384", "RS512",
                       "ES256", "ES384", "ES512",
                       "PS256", "PS384", "PS512")

#: Claims that must be present. ``nonce`` is checked separately (it is
#: compared, not merely required).
REQUIRED_CLAIMS = ("exp", "iat", "iss", "aud", "sub")

#: Tolerance for a clock that is a little wrong on either side. Small on
#: purpose: it is subtracted from every expiry check.
CLOCK_SKEW_S = 60

#: Discovery and JWKS caching. Discovery is re-read hourly; the JWKS is
#: re-read whenever a token names a ``kid`` we do not hold, but at most once
#: per :data:`JWKS_MIN_REFRESH_S` — that bound is what stops a stranger posting
#: junk ``kid``s from turning our sign-in endpoint into a load generator
#: against the IdP.
DISCOVERY_TTL_S = 3600.0
JWKS_MIN_REFRESH_S = 30.0

#: One HTTP timeout for discovery, token exchange and JWKS. A sign-in request
#: holds a worker thread while these run.
HTTP_TIMEOUT_S = 10.0

#: How long an authorization request may stay in flight, and how many may be
#: in flight at once. The cap is what makes an anonymous ``GET
#: /api/auth/oidc/login`` flood bounded memory rather than unbounded.
PENDING_TTL_S = 600.0
PENDING_MAX = 512

#: The one refusal an unlinked identity gets. Deliberately says nothing about
#: which of the several reasons applied — see the module docstring.
UNLINKED = ("this identity is not linked to an account on this instance. "
            "Ask an administrator to invite you, or sign in and link it.")


# --------------------------------------------------------------- configuration

@dataclass(frozen=True)
class OidcConfig:
    """One instance's provider, read from ``<state>/auth/oidc.json``.

    Admin-editable JSON rather than environment variables, for the reason the
    rest of identity is a document: ``agentcad admin ...`` through ``docker
    compose exec`` is a supported second writer, an env var needs a container
    restart to change, and a client *secret* in ``docker inspect`` output is
    worse than one in a 0600 file beside the password hashes.

    Shape (unknown keys are ignored, so a future field cannot break an old
    instance)::

        {
          "enabled": true,
          "issuer": "https://idp.example.com",
          "client_id": "agentcad-hosted",
          "client_secret": "...",
          "scopes": ["openid", "email", "profile"],
          "label": "Example SSO",
          "redirect_uri": null,
          "allowed_email_domains": ["example.com"],
          "email_handles": {"nikita@example.com": "nikita"}
        }
    """

    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    label: str = "single sign-on"
    #: Empty means "any domain". Non-empty gates *linking* (never an existing
    #: link — an instance that changes its domain must not lock its people
    #: out of accounts they already own).
    allowed_email_domains: tuple[str, ...] = ()
    #: The admin-authored ``verified email -> existing handle`` map. This is
    #: the *only* automatic path from an IdP identity to a handle, and it can
    #: only ever name a handle that already exists.
    email_handles: dict[str, str] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Changes iff the provider changed. The route pack keeps one client
        per fingerprint, so editing ``oidc.json`` takes effect on the next
        request (no restart) while the discovery/JWKS caches survive the
        requests in between."""
        payload = json.dumps({
            "issuer": self.issuer, "client_id": self.client_id,
            "redirect_uri": self.redirect_uri, "scopes": list(self.scopes),
            "secret": hashlib.sha256(self.client_secret.encode()).hexdigest(),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @classmethod
    def from_document(cls, doc: object, public_origin: str | None
                      ) -> "OidcConfig | None":
        """Parse ``oidc.json``. ``None`` when the instance has no provider.

        ``None`` — not an exception — for absent/empty/``enabled: false``,
        because "this instance has no OIDC" is a **404 on the route**, not an
        error. A document that is *present but wrong* raises, naming the field:
        the reader is an operator who just edited it.
        """
        if not isinstance(doc, dict) or not doc:
            return None
        if not doc.get("enabled", True):
            return None
        issuer = _text(doc, "issuer").rstrip("/")
        client_id = _text(doc, "client_id")
        client_secret = _text(doc, "client_secret")
        if not (issuer.startswith("https://") or issuer.startswith("http://")):
            raise ValidationError(
                "oidc.json: 'issuer' must be an absolute http(s) URL, e.g. "
                "https://idp.example.com", {"field": "issuer"})
        redirect = doc.get("redirect_uri")
        if redirect in (None, ""):
            if not public_origin:
                raise ValidationError(
                    "oidc.json: 'redirect_uri' is required when the instance "
                    "has no public origin", {"field": "redirect_uri"})
            redirect = f"{public_origin.rstrip('/')}{CALLBACK_PATH}"
        if not isinstance(redirect, str):
            raise ValidationError("oidc.json: 'redirect_uri' must be a string",
                                  {"field": "redirect_uri"})
        scopes = doc.get("scopes") or ["openid", "email", "profile"]
        if (not isinstance(scopes, list)
                or not all(isinstance(s, str) and s for s in scopes)):
            raise ValidationError("oidc.json: 'scopes' must be a list of strings",
                                  {"field": "scopes"})
        if "openid" not in scopes:
            # Without it the IdP is free to answer with no ID token at all,
            # and there is nothing to verify.
            scopes = ["openid", *scopes]
        domains = doc.get("allowed_email_domains") or []
        if (not isinstance(domains, list)
                or not all(isinstance(d, str) for d in domains)):
            raise ValidationError(
                "oidc.json: 'allowed_email_domains' must be a list of strings",
                {"field": "allowed_email_domains"})
        mapping = doc.get("email_handles") or {}
        if (not isinstance(mapping, dict)
                or not all(isinstance(k, str) and isinstance(v, str)
                           for k, v in mapping.items())):
            raise ValidationError(
                "oidc.json: 'email_handles' maps a verified email to an "
                "EXISTING handle, e.g. {\"nikita@example.com\": \"nikita\"}",
                {"field": "email_handles"})
        label = doc.get("label")
        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect,
            scopes=tuple(scopes),
            label=label if isinstance(label, str) and label else "single sign-on",
            allowed_email_domains=tuple(d.strip().lower().lstrip("@")
                                        for d in domains if d.strip()),
            email_handles={k.strip().lower(): v.strip()
                           for k, v in mapping.items()},
        )


def _text(doc: dict, field_name: str) -> str:
    value = doc.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"oidc.json: {field_name!r} is required and must be a non-empty "
            f"string", {"field": field_name})
    return value.strip()


# ------------------------------------------------------------- pending flows

#: The cookie that binds an authorization request to **the browser that
#: started it**. Without it this flow has a *login CSRF*: an attacker starts a
#: sign-in here, authenticates at the IdP as themselves, and then makes a
#: victim's browser follow the callback carrying the attacker's ``code`` and
#: ``state`` — the victim is silently signed in **as the attacker** and works
#: (and uploads) inside the attacker's account. ``state`` alone cannot stop it:
#: it proves the flow started here, not that it started *in this browser*.
#: ``SameSite=Lax`` is deliberate and load-bearing — the callback arrives as a
#: top-level GET navigation from the provider, which ``Strict`` would strip the
#: cookie from, breaking every real sign-in.
FLOW_COOKIE = "agentcad_oidc_flow"


@dataclass(frozen=True)
class Pending:
    """One authorization request in flight. Single use, TTL-bounded."""

    state: str
    nonce: str
    verifier: str
    #: Set when a *signed-in* person started this flow to link an identity to
    #: their own handle. ``None`` for a plain sign-in.
    link_handle: str | None
    created: float
    #: The secret handed to the browser in :data:`FLOW_COOKIE` and required
    #: back at the callback. See that constant for what it stops.
    binding: str = ""

    @property
    def challenge(self) -> str:
        """The S256 PKCE challenge for this flow's verifier."""
        return _b64u(hashlib.sha256(self.verifier.encode("ascii")).digest())


class PendingFlows:
    """In-memory, TTL'd, size-capped store of :class:`Pending` by ``state``.

    Not thread-safe by a lock because every operation is a single dict
    mutation under the GIL and a lost race costs one retry — but pruning walks
    the dict, so it takes a copy of the keys first rather than mutating during
    iteration.
    """

    def __init__(self, ttl_s: float = PENDING_TTL_S, cap: int = PENDING_MAX):
        self._ttl = ttl_s
        self._cap = cap
        self._rows: dict[str, Pending] = {}

    def __len__(self) -> int:
        return len(self._rows)

    def put(self, pending: Pending) -> None:
        self._prune()
        if len(self._rows) >= self._cap:
            # Oldest first: an attacker flooding `oidc/login` evicts their own
            # junk before a real person's flow in almost every ordering, and
            # the memory is bounded either way.
            for key in sorted(self._rows,
                              key=lambda k: self._rows[k].created)[:self._cap // 4 or 1]:
                self._rows.pop(key, None)
        self._rows[pending.state] = pending

    def take(self, state: object) -> Pending | None:
        """Pop a flow. **Single use** — a replayed callback finds nothing,
        which is what makes an intercepted code useless on its own."""
        self._prune()
        if not isinstance(state, str) or not state:
            return None
        row = self._rows.pop(state, None)
        if row is None:
            return None
        if _now() - row.created > self._ttl:
            return None
        return row

    def _prune(self) -> None:
        cutoff = _now() - self._ttl
        for key in [k for k, row in self._rows.items() if row.created < cutoff]:
            self._rows.pop(key, None)


# ------------------------------------------------------------------- the client

@dataclass(frozen=True)
class Verified:
    """A validated ID token, reduced to what the linking policy reads."""

    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None
    link_handle: str | None
    claims: dict


class OidcClient:
    """The relying party. One per provider fingerprint, held by the route pack.

    Holds the discovery document and the JWKS between requests; holds no
    credential and no session.
    """

    def __init__(self, config: OidcConfig, *,
                 pending: PendingFlows | None = None) -> None:
        self.config = config
        self.pending = pending if pending is not None else PendingFlows()
        self._meta: dict | None = None
        self._meta_at = 0.0
        self._keys: dict[str, dict] = {}
        self._keys_at = 0.0

    # -- HTTP ---------------------------------------------------------------

    def _http(self) -> httpx.Client:
        # `follow_redirects=False`: an IdP that answers a token request with a
        # redirect is not one we chase.
        return httpx.Client(transport=TRANSPORT, timeout=HTTP_TIMEOUT_S,
                            follow_redirects=False)

    def metadata(self) -> dict:
        """The discovery document, cached, with the issuer **asserted**.

        The issuer check is the reason to fetch discovery at all: it is what
        binds ``authorization_endpoint``/``token_endpoint``/``jwks_uri`` — all
        URLs the IdP hands us — to the issuer the operator configured.
        """
        if self._meta is not None and _now() - self._meta_at < DISCOVERY_TTL_S:
            return self._meta
        url = f"{self.config.issuer}/.well-known/openid-configuration"
        with self._http() as client:
            try:
                response = client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                meta = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise AuthError(
                    "the identity provider's discovery document could not be "
                    f"read ({type(exc).__name__})",
                    {"issuer": self.config.issuer}) from exc
        if not isinstance(meta, dict):
            raise AuthError("the identity provider's discovery document is "
                            "not an object", {"issuer": self.config.issuer})
        if str(meta.get("issuer", "")).rstrip("/") != self.config.issuer:
            raise AuthError(
                "the identity provider's discovery document declares a "
                "different issuer than this instance is configured for",
                {"issuer": self.config.issuer})
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(meta.get(key), str) or not meta[key]:
                raise AuthError(
                    f"the identity provider's discovery document has no "
                    f"{key!r}", {"issuer": self.config.issuer})
        self._meta, self._meta_at = meta, _now()
        return meta

    # -- the flow -----------------------------------------------------------

    def begin(self, *, link_handle: str | None = None) -> Pending:
        """Mint ``state``/``nonce``/PKCE verifier and remember them server-side.

        256 bits each from ``secrets``: ``state`` is the CSRF binding between
        this browser's redirect and this browser's callback, ``nonce`` binds
        the ID token to *this* request, and the verifier is what makes an
        intercepted authorization code unusable (PKCE S256, RFC 7636).
        """
        pending = Pending(state=secrets.token_urlsafe(32),
                          nonce=secrets.token_urlsafe(32),
                          verifier=secrets.token_urlsafe(64),
                          link_handle=link_handle,
                          created=_now(),
                          binding=secrets.token_urlsafe(32))
        self.pending.put(pending)
        return pending

    def authorization_url(self, pending: Pending) -> str:
        """Where the browser goes next."""
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": pending.state,
            "nonce": pending.nonce,
            "code_challenge": pending.challenge,
            "code_challenge_method": "S256",
        }
        base = self.metadata()["authorization_endpoint"]
        joiner = "&" if "?" in base else "?"
        return base + joiner + urllib.parse.urlencode(params)

    def complete(self, code: object, state: object,
                 binding: object = None) -> Verified:
        """Exchange the code and verify the ID token, or refuse.

        *binding* is the browser's :data:`FLOW_COOKIE` value; it must match the
        one minted with this flow's ``state``. Every refusal is an
        :class:`AuthError` (401) with the same message: this runs on an
        anonymous endpoint, and the caller is either a browser finishing a
        sign-in or somebody poking at it.
        """
        pending = self.pending.take(state)
        if pending is None:
            # Unknown, expired or already spent. One answer for all three.
            raise AuthError("this sign-in could not be completed; start again")
        if not isinstance(code, str) or not code:
            raise AuthError("this sign-in could not be completed; start again")
        if not hmac.compare_digest(binding if isinstance(binding, str) else "",
                                   pending.binding):
            # A callback in a browser that did not start this flow. See
            # FLOW_COOKIE — this is the login-CSRF stop, and it is checked
            # BEFORE the code is spent at the provider.
            raise AuthError("this sign-in could not be completed; start again")

        token = self._exchange(code, pending)
        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise AuthError("the identity provider returned no ID token")
        claims = self._verify(id_token)

        if not hmac.compare_digest(str(claims.get("nonce") or ""),
                                   pending.nonce):
            # The one check pyjwt cannot do for us: it binds this token to
            # this browser's authorization request.
            raise AuthError("this sign-in could not be completed; start again")

        email = claims.get("email")
        return Verified(
            issuer=str(claims["iss"]).rstrip("/"),
            subject=str(claims["sub"]),
            email=email.strip().lower() if isinstance(email, str) else None,
            # `is True`, not truthiness: a provider that sends the *string*
            # "true" is not one this instance auto-links from. The failure
            # direction is the safe one (the person is refused and an admin
            # links them explicitly), and it is stated rather than papered
            # over with a coercion that would also accept "false"'s cousins.
            email_verified=claims.get("email_verified") is True,
            name=claims.get("name") if isinstance(claims.get("name"), str) else None,
            link_handle=pending.link_handle,
            claims=claims,
        )

    def _exchange(self, code: str, pending: Pending) -> dict:
        """The token request. **HTTP Basic** client authentication.

        RFC 6749 §2.3.1 makes ``client_secret_basic`` the method every
        conforming server must support and the one it must prefer, and its
        credentials are form-urlencoded *before* base64 — which is why the two
        halves are quoted rather than handed to httpx raw. An IdP that
        advertises only ``client_secret_post`` gets the secret in the body
        instead; nothing else is attempted (``none`` and the assertion methods
        are not configurations this instance can express).
        """
        meta = self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "code_verifier": pending.verifier,
        }
        methods = meta.get("token_endpoint_auth_methods_supported")
        post_only = (isinstance(methods, list) and methods
                     and "client_secret_basic" not in methods)
        auth = None
        if post_only:
            data["client_secret"] = self.config.client_secret
        else:
            auth = httpx.BasicAuth(
                urllib.parse.quote(self.config.client_id, safe=""),
                urllib.parse.quote(self.config.client_secret, safe=""))
        with self._http() as client:
            try:
                response = client.post(meta["token_endpoint"], data=data,
                                       auth=auth,
                                       headers={"Accept": "application/json"})
            except httpx.HTTPError as exc:
                raise AuthError(
                    "the identity provider's token endpoint could not be "
                    f"reached ({type(exc).__name__})") from exc
            if response.status_code != 200:
                detail = ""
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        detail = str(body.get("error") or "")
                except ValueError:
                    pass
                raise AuthError(
                    "the identity provider refused the authorization code",
                    {"idp_error": detail} if detail else {})
            try:
                token = response.json()
            except ValueError as exc:
                raise AuthError("the identity provider's token response was "
                                "not JSON") from exc
        if not isinstance(token, dict):
            raise AuthError("the identity provider's token response was not "
                            "an object")
        return token

    # -- ID token -----------------------------------------------------------

    def _verify(self, id_token: str) -> dict:
        """Signature, algorithm, issuer, audience, expiry — then the claims."""
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise AuthError("the ID token is malformed") from exc
        alg = header.get("alg")
        if alg not in ID_TOKEN_ALGORITHMS:
            # Refused here *and* by `jwt.decode`'s allowlist below. Two
            # checks, because `alg: none` is the one failure mode that turns a
            # token into a forgeable claim, and belt-and-braces costs a line.
            raise AuthError(f"unsupported ID token algorithm {alg!r}",
                            {"allowed": list(ID_TOKEN_ALGORITHMS)})
        key = self._signing_key(header.get("kid"), alg)
        try:
            claims = jwt.decode(
                id_token, key.key, algorithms=list(ID_TOKEN_ALGORITHMS),
                audience=self.config.client_id, issuer=self.config.issuer,
                leeway=CLOCK_SKEW_S,
                options={"require": list(REQUIRED_CLAIMS)},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(
                f"the ID token was rejected ({type(exc).__name__})") from exc
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1:
            # RFC 7519 §4.1.3 lets a token carry several audiences; OIDC Core
            # §3.1.3.7 then *requires* `azp` to name the party it was issued
            # for. Without this a token minted for another client of the same
            # IdP would verify here.
            if str(claims.get("azp") or "") != self.config.client_id:
                raise AuthError("the ID token names several audiences and is "
                                "not authorized for this client")
        return claims

    def _signing_key(self, kid: object, alg: str) -> jwt.PyJWK:
        """The JWKS key for *kid*, refreshing on a miss (key rollover).

        An IdP rotates by publishing the new key beside the old one and then
        signing with the new ``kid``. A cache that never refreshed would break
        at rotation; one that refreshed on every miss would let a stranger
        drive our request rate at the IdP — so a miss refreshes at most once
        per :data:`JWKS_MIN_REFRESH_S`.
        """
        kid = kid if isinstance(kid, str) and kid else None
        row = self._pick(kid)
        if row is None and _now() - self._keys_at >= JWKS_MIN_REFRESH_S:
            self._fetch_jwks()
            row = self._pick(kid)
        if row is None:
            raise AuthError("the ID token was signed by a key the identity "
                            "provider does not publish",
                            {"kid": kid} if kid else {})
        try:
            return jwt.PyJWK(row, algorithm=row.get("alg") or alg)
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise AuthError("the identity provider's signing key is "
                            "unusable") from exc

    def _pick(self, kid: str | None) -> dict | None:
        if kid is not None:
            return self._keys.get(kid)
        # No `kid` in the header: unambiguous only when the JWKS publishes a
        # single signing key. Guessing among several is how a verifier ends up
        # accepting whichever key happens to work.
        rows = list(self._keys.values())
        return rows[0] if len(rows) == 1 else None

    def _fetch_jwks(self) -> None:
        url = self.metadata()["jwks_uri"]
        with self._http() as client:
            try:
                response = client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                doc = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise AuthError(
                    "the identity provider's JWKS could not be read "
                    f"({type(exc).__name__})") from exc
        self._keys_at = _now()
        keys = doc.get("keys") if isinstance(doc, dict) else None
        if not isinstance(keys, list):
            raise AuthError("the identity provider's JWKS has no 'keys'")
        fresh: dict[str, dict] = {}
        for index, row in enumerate(keys):
            if not isinstance(row, dict):
                continue
            if row.get("use") == "enc":
                continue            # an encryption key never verifies a token
            kid = row.get("kid")
            fresh[kid if isinstance(kid, str) and kid else f"#{index}"] = row
        self._keys = fresh


# ------------------------------------------------------------ linking policy

def sign_in_handle(store, config: OidcConfig, verified: Verified) -> tuple[str, str]:
    """Which handle this identity signs in as: ``(handle, how)``.

    ``how`` is ``"linked"`` (the identity was already bound to this handle) or
    ``"auto_linked"`` (a verified email the instance maps to an existing
    handle — the link is written here, once).

    **Never creates an account.** The only two ways out of this function are a
    handle that already exists or an :class:`AuthError` carrying
    :data:`UNLINKED`.
    """
    existing = store.find_by_oidc(verified.issuer, verified.subject)
    if existing:
        user = store.get_user(existing)
        # "Invited" is `disabled` **and never enrolled** — the account has no
        # password because nobody has set one yet. A disabled account that
        # *was* enrolled is a **revoked** one, and signing it in through a side
        # door would undo `agentcad admin user disable` entirely.
        invited = bool(user and user["disabled"] and not user["enrolled"])
        if user is None or (user["disabled"] and not invited):
            raise AuthError(UNLINKED)
        store.link_oidc(existing, verified.issuer, verified.subject,
                        email=verified.email)
        if invited:
            store.enable_user(existing)
        return existing, "linked"

    handle = _mapped_handle(config, verified)
    if handle is None:
        raise AuthError(UNLINKED)
    user = store.get_user(handle)
    if user is None:
        # The map names a handle that does not exist. Closed registration:
        # we do not create it, and the operator's mistake is not the visitor's
        # business.
        raise AuthError(UNLINKED)
    if store.find_oidc(handle) is not None:
        # This handle is already bound to a *different* IdP identity. Silently
        # rebinding it would make the map an account-takeover primitive.
        raise AuthError(UNLINKED)
    if user["disabled"] and user["enrolled"]:
        raise AuthError(UNLINKED)                    # revoked, not invited
    store.link_oidc(handle, verified.issuer, verified.subject,
                    email=verified.email)
    if user["disabled"]:
        # Invited, never enrolled: the admin invited the handle *and* mapped
        # the email, and the IdP verified it. That is enrolment, minus a
        # password this account will never have.
        store.enable_user(handle)
    return handle, "auto_linked"


def link_identity(store, config: OidcConfig, verified: Verified) -> str:
    """Bind this identity to the signed-in handle that started the flow.

    The caller is authenticated, so the refusals here are specific — there is
    no enumeration oracle to protect against, and "already linked to somebody
    else" is exactly what the person needs to be told.
    """
    handle = verified.link_handle
    if not handle:
        raise ValidationError("this flow did not begin as a link")
    if store.get_user(handle) is None:
        raise AuthError(UNLINKED)
    owner = store.find_by_oidc(verified.issuer, verified.subject)
    if owner is not None and owner != handle:
        raise ConflictError(
            "that identity is already linked to another account on this "
            "instance", {"issuer": verified.issuer})
    _check_domain(config, verified, refuse=True)
    store.link_oidc(handle, verified.issuer, verified.subject,
                    email=verified.email)
    return handle


def _mapped_handle(config: OidcConfig, verified: Verified) -> str | None:
    """The handle the instance's own map names for this *verified* email."""
    if not verified.email or not verified.email_verified:
        # An unverified email is a claim by the person, not by the IdP. It is
        # the single most common OIDC account-takeover shape, so it never
        # links anything.
        return None
    if not _check_domain(config, verified, refuse=False):
        return None
    return config.email_handles.get(verified.email)


def _check_domain(config: OidcConfig, verified: Verified, *, refuse: bool) -> bool:
    if not config.allowed_email_domains:
        return True
    domain = (verified.email or "").rpartition("@")[2]
    ok = bool(domain) and domain in config.allowed_email_domains
    if not ok and refuse:
        raise AuthError(UNLINKED)
    return ok


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
