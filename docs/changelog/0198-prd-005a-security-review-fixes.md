# 0198 — PRD-005a security review: four blocking fixes and six minors

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Nikita Fedorov

## Summary

An independent security review of the hosted-core branch (`prd-005-lite`,
HEAD `57f9e4d`) returned CHANGES-REQUIRED with four blocking findings. All four
are fixed here, each reproduced first with the reviewer's own command and
re-measured after: the login/enrol CSRF check was dead code, an index
*document* could override the operator's configured scope and expose a private
index anonymously, the per-handle login bucket was a permanent lockout
primitive any stranger could drive, and the password-recovery path left the
sessions it exists to kill alive for up to 30 days.

A **round-2** re-review confirmed M1, M2 and M4 hold under re-attack (bypass
sweep clean, kernel boundary byte-identical) but reopened M3 in the one topology
the deployment guide prescribes: behind a reverse proxy the login address was
the proxy's IP for every client, collapsing `(handle, address)` back to
per-handle. That is fixed here too (see "Round 2" below); the round-1 M3 write-up
is kept as it was.

The review's two clean results are untouched by this change: the auth-bypass
sweep (no route reachable without a credential outside the nine-entry
allowlist) and the kernel boundary (no anonymous request reaches execution).
Both are re-asserted green — `test_hosted_surface.py` and the runtime
`CountingKernel` proof still pass, and the anonymous surface is still the same
nine entries.

## Changes

### M1 (critical in effect) — the login/enrol CSRF check never ran

`security.guard` resolved a principal, then returned early for anonymous
requests to public paths, and only *then* applied the Origin rule. Since
`POST /api/auth/login` and `POST /api/auth/enrol/{token}` are the **only**
unsafe methods an anonymous caller can reach, the check covered every route
except the two it existed for — despite the comment beside it, `AGENTS.md` and
changelog 0189 all claiming it covered them.

Measured before, against a hosted server on 127.0.0.1:8791:

```
$ curl -s -D- -X POST -H 'Origin: https://evil.example' \
    --data-binary '{"handle":"nikita","password":"..."}' .../api/auth/login
HTTP/1.1 200 OK
set-cookie: agentcad_session=Y-Nzi89HRpa2pwjO4EqMi8_UA1XvKVHJKjxrgzbyeyM; ...
```

and the same 200 + `Set-Cookie` for `POST /api/auth/enrol/{token}`, which also
**spent** the enrolment link.

Fix: the Origin block moves above the `principal is None` branch and treats "no
principal" as "not a bearer". Origin-*absent* is still allowed, deliberately
and identically on both branches — a browser always sends `Origin` on a
cross-site POST, while `curl`, the MCP client and a same-origin `fetch` may
omit it, so refusing on absence would break every non-browser client without
stopping a browser attack. After:

```
login  Origin: https://evil.example   -> 403 ForbiddenOrigin, no Set-Cookie
login  Origin: http://127.0.0.1:8791  -> 200 + Set-Cookie
login  (no Origin header)             -> 200
enrol  Origin: https://evil.example   -> 403, link still unspent
enrol  Origin: http://127.0.0.1:8791  -> 200 + Set-Cookie
```

### M2 — an index document could override the operator's configured scope

`routes_public._public_indexes` filtered on `indexes.py`'s `scope` property,
which returns the index **document's** declared scope in preference to the
operator's configuration. PRD-011 introduced that precedence for *publish
policy*, where document-wins is harmless; PRD-005a repurposed the same property
as *anonymous access control*, where it is not. For a git index the third party
who authors `index.json` thereby decided whether this instance served their
content to the internet, over an operator who had written `scope: "private"`.

Reproduced with the reviewer's `scope2.py` — config `scope: "private"`,
document `"scope": "public"`:

```
before: ANON list -> 200 [('acme-secret', 'INTERNAL ONLY - operator marked this index private')]
        ANON /api/public/packages/acme-secret -> 200 {"name":"acme-secret", ...}
after:  ANON list -> 200 []
        ANON /api/public/packages/acme-secret -> 404 no such package in the public catalog
```

Fix: `LocalIndex` gains a read-only `configured_scope` property (the operator's
own word, ignoring the document) and `_public_indexes` requires **both**
`configured_scope == "public"` and `scope == "public"`. PRD-011's `scope`
property is unchanged, so publish policy still sees what it saw; requiring
agreement also preserves the old refusal direction (a document saying `private`
hides itself even from an operator who configured it public), and a
disagreement serves nothing.

### M3 — the per-handle login bucket was a permanent lockout primitive

`LOGIN_RATE_PER_S=0.2, LOGIN_BURST=5` keyed on `handle:` alone, and
`TokenBucket.take` does not consume on refusal, so a bucket held empty stays
empty. Any third party willing to spend 0.5 req/s against a *known* handle
locked that account out of every address indefinitely — and handles are public
(presence rosters, comment authors, history trailers).

Measured with the attacker on 203.0.113.7 and the victim on 198.51.100.9, one
attacker attempt every 2 s, victim using the **correct** password:

```
before: victim locked out in 6/6 rounds (429 every round)
after:  victim locked out in 0/6 rounds (200 every round); attacker 429 in 4/6
```

Fix: `security.login_key(handle, address)` — the bucket is keyed on the pair.
The per-address bucket (0.5/s, burst 15) is unchanged, so a single attacking
address is still throttled, and the refusal still happens before scrypt so a
flood buys no KDF. The cost is stated in the code: an attacker with many
addresses now gets `burst` guesses per address against one handle instead of
five in total. A botnet was never what a token bucket keyed on anything was
going to stop, and denying the account's owner in the attempt is the worse of
the two failures.

### M4 — password recovery did not revoke sessions

`AuthStore.enrol` set the new password without calling `revoke_sessions_for`,
unlike `disable_user`. `agentcad admin enrol <handle>` is the recovery path for
a password that was lost **or stolen**, so an attacker's cookie outlived the
reset by up to `ABSOLUTE_SESSION_S` (30 days).

```
before: attacker cookie before reset -> 200 ; after reset -> 200
after:  attacker cookie before reset -> 200 ; after reset -> 401
        newly enrolled session                            -> 200
```

Fix: `enrol` calls `revoke_sessions_for(handle)` inside its own `_scope()`
(reentrant — `threading.RLock` plus a depth counter, so it is the same lock and
the same flock, not a second one), after the password and enrolment writes, so
a reset cannot half-apply. The caller's new session is created afterwards and
survives.

### Recommended minors, all fixed

- **m1** — the anonymous 404s carried no `Cache-Control`: raising discards the
  handler's `Response`, so `response.headers[...] = ...` was lost. Since a
  flood mostly asks for names that do not exist, the traffic the CDN argument
  depends on was exactly the uncacheable part. `AppError` gained an optional
  `headers` dict, `_error_response` renders it, and `routes_public._miss()` is
  now the single miss for all four routes. Verified: `404` +
  `cache-control: public, max-age=300`.
- **m2** — the state directory was **0755**. `mkdir(parents=True, mode=0o700)`
  sets the final component only, and `agentcad admin user add` builds
  `AuthStore(state_dir()/"auth")` first, so `state` was born as an intermediate
  parent at 0755 and the later `exist_ok=True` left it there. Measured on a
  fresh boot in that order: `0o755 state / 0o700 auth / 0o600 secret.key`. New
  `appmode.ensure_state_dir()` creates it 0700 and repairs an existing one
  (best-effort chmod: a bind-mounted volume owned by another uid must not turn
  a hardening step into an outage). Verified live — the scratch server's
  pre-existing 0755 state dir came up 0700.
- **m3** — the `PREFIX` seam comment in `app.py` claimed it "is NOT a way past
  the allowlist". A pack declaring `PREFIX = "/api/public"` or `"/js"` would in
  fact land inside `PUBLIC_PREFIXES`. The comment now says what is true: no
  pack does this today, and it is a review-enforced invariant rather than a
  mechanical one.
- **m5** — `GET …/preview` with no `path` leaked FastAPI's native
  `{"detail": [...]}` 422, the one route on the anonymous surface answering in
  a second error dialect. `path` is now optional in the signature and checked
  in the handler: same 422, house `{"error": {...}}` envelope,
  `details.parameter == "path"`.
- **m7** — `.gitignore` covered `.env` but not `.env.*`; `.env.prod` /
  `.env.staging` are the ordinary way to keep deployments apart and the one
  carrying the production secret is exactly the one that must not be committed.
  Added with `!.env.example`.
- **m8** — the AST scan in `test_public_catalog.py` now calls itself a lint in
  its own docstring, and names the runtime `CountingKernel` test as the actual
  proof. A source scan for three attribute spellings is defeated by `getattr`,
  by an alias, or by a helper in another module.
- **m9** — stale `AGENTS.md`: the PRD-002/008 sections still said `actor_kind`
  is human "iff the identity is the browser … until PRD-005", and Conventions
  still said "server binds `127.0.0.1` only". Both are now written as the
  local/hosted split they actually are, and the architecture diagram says
  `127.0.0.1 local / bound+authed hosted`.

### Round 2 — M3 behind the reverse proxy (option a: parse `X-Forwarded-For`)

The round-1 M3 fix keys the login limiter on `(handle, address)` where
`address = request.client.host`. That is the **raw socket peer**. The deployment
guide (`docs/deployment.md`) prescribes an nginx/Caddy reverse proxy on the same
host doing `proxy_pass http://127.0.0.1:8630`, and the documented nginx block did
**not** forward `X-Forwarded-For`. So every internet client reached the app as
`127.0.0.1`: `(handle, address)` collapsed to `(handle, proxy-ip)` — per-handle
again, M3 reopened — and the unchanged per-address bucket became an instance-wide
login DoS (all clients share one address bucket).

Reproduced by wrapping the app in uvicorn's own `ProxyHeadersMiddleware`
(trusting `127.0.0.1`) so the simulation is the real deploy, not a mock:

```
A. behind proxy, no XFF (docs as written)  attacker=429 victim=429  VICTIM LOCKED OUT
B. behind proxy, WITH XFF (the fix)        attacker=429 victim=200  victim ok
C. spoof: attacker forges a leading XFF    attacker=429 victim=200  victim ok
D. direct-bind (no proxy mw): client forges attacker=429 victim=200  victim ok
```

**I chose option (a): make the address real, rather than dropping the bucket.**
A hosted app behind a proxy has to parse `X-Forwarded-For` eventually anyway
(rate limiting now, audit principals in full PRD-005 later), and uvicorn already
ships a correct, trust-bounded parser — so doing it right now is the better
investment than leaning on scrypt plus a global cap and giving up the per-client
throttle.

What I verified about uvicorn's `ProxyHeadersMiddleware` (read from source,
`uvicorn 0.52.1`, and confirmed live): with `forwarded_allow_ips` set to the
trusted peer, it only acts when the **immediate socket peer** is trusted, and it
walks `X-Forwarded-For` **from the right**, returning the first hop that is not
trusted. With one trusted local proxy that is the client as the proxy saw it; a
client that prepends its own value has it overwritten by the proxy's append
(`$proxy_add_x_forwarded_for`), so it cannot forge the address across one correct
hop. `uvicorn.run(app, …)` with the default `proxy_headers=True` already installs
this middleware and resolves `forwarded_allow_ips=None` to `127.0.0.1` — so the
real gap was purely that the documented proxy did not *send* the header — but the
config is now set **explicitly** so a version bump cannot silently flip a
security property.

The changes:

- `cli._uvicorn_proxy_kwargs(mode)` — hosted runs uvicorn with
  `proxy_headers=True, forwarded_allow_ips=trusted_proxy()`; local runs it with
  `proxy_headers=False` (a loopback tool has no proxy, and this keeps "the local
  client cannot set its own forwarded address" trivially true). Passed into the
  existing `uvicorn.run` call.
- `appmode.trusted_proxy(env)` / `DEFAULT_TRUSTED_PROXY` — resolves
  `AGENTCAD_TRUSTED_PROXY` (default `127.0.0.1`, since the proxy is local per the
  docs) and **refuses `*`**: trusting every peer turns `X-Forwarded-For` back into
  attacker-controlled input and silently re-opens M3. Validated in `_serve_bind`
  alongside the bind interlock, so a dangerous value is a clean exit 2 before the
  kernel pool spawns (verified live: exit code 2, message names the setting).
- **The address read is unchanged** (`routes_auth` still reads
  `request.client.host`): the middleware rewrites `scope["client"]` upstream, so
  behind a trusting proxy that value *is* the real client. Comments in
  `routes_auth` and `security.login_key` now say the key is only as honest as the
  proxy plumbing.
- Direct public bind with **no** proxy is safe under the same default: the
  immediate peer is then the public client, which is not in the trusted set, so
  its `X-Forwarded-For` is ignored and the socket address stands.

Docs: the nginx block gains `proxy_set_header X-Forwarded-For
$proxy_add_x_forwarded_for;` with a note that it is not optional and why; Caddy's
`reverse_proxy` sets it automatically (stated, and it is why the plain directive
is already correct); a new "How the address is trusted, and the one way to break
it" paragraph states the single-hop trust model, the two-proxy case, and that `*`
is refused; an `AGENTCAD_TRUSTED_PROXY` env row; and the public-surface login row
now says *address* is the real client only when the proxy forwards the header.

Measured after, in both topologies (`ProxyHeadersMiddleware` simulation and a
live loopback hosted server on 8792, where the loopback client is itself the
trusted proxy so real uvicorn honors its XFF):

- Behind proxy, with XFF: a flood from `X-Forwarded-For: 203.0.113.7` drained to
  429 while `X-Forwarded-For: 198.51.100.9` still answered 401 (its own budget) —
  per-real-client buckets.
- Spoof: `X-Forwarded-For: 198.51.100.9, 8.8.8.8` was keyed on the rightmost
  untrusted hop `8.8.8.8`, and `198.51.100.9`'s budget was left untouched — a
  client cannot frame another by forging a leading entry.
- Direct-bind: a client at socket peer `203.0.113.7` forging
  `X-Forwarded-For: 198.51.100.9` was keyed on `203.0.113.7`; the victim at
  `198.51.100.9` signed in.

### Documentation

- `docs/deployment.md`: the private-index section was actively wrong — it told
  operators to set `scope: "private"` in config, which did nothing. It now
  states the both-must-agree rule, says which side matters and why it matters
  most for a git index, notes that the default on both sides is `public`, and
  shows a config example. Also: the login row says `(handle, address)`, the
  `admin enrol` section states that a reset signs the handle out everywhere,
  the cache paragraph says the 404s are cacheable too, `AGENTCAD_STATE_DIR`
  documents 0700, and `AGENTCAD_SECRET_KEY` now says **leave it unset** — the
  generated-key file is the recommended path, because an explicit value is
  process environment and therefore visible to `docker inspect`,
  `/proc/<pid>/environ`, and whatever shell history or CI log it passed
  through.
- `AGENTS.md` hosted-core gotchas: four new entries (the CSRF ordering, the
  two-scope filter, the `(handle, address)` key, enrol-revokes-sessions).
- The design spec carries four inline corrections rather than a rewrite: the
  Decision 7 step order printed in the spec is what the implementation copied,
  so the note says step 6 runs before step 4 and why.

## Files

- `agentcad/server/security.py` — CSRF block moved above the anonymous branch
  and made principal-agnostic; guard-order docstring corrected; new
  `login_key(handle, address)`; the rate-limit rationale block rewritten.
- `agentcad/server/routes_auth.py` — login takes the bucket with
  `sec.login_key(handle, address)`.
- `agentcad/core/authstore.py` — `enrol` revokes the handle's sessions inside
  its write scope.
- `agentcad/core/packages/indexes.py` — new `LocalIndex.configured_scope`
  property (inherited by `GitIndex`); `scope`'s docstring now says it is the
  publish-policy answer and not an access-control one. No behaviour change to
  `scope`.
- `agentcad/server/routes_public.py` — `_public_indexes` requires both scopes;
  `_miss()` carries `Cache-Control` and replaces four bare
  `NotFoundError(NO_SUCH_PACKAGE)` raises; `path` is optional and validated in
  the handler.
- `agentcad/core/model.py` — `AppError.headers`.
- `agentcad/server/app.py` — `_error_response` renders `exc.headers`; the
  `PREFIX` seam comment corrected.
- `agentcad/core/appmode.py` — `ensure_state_dir()`; `_secret` uses it.
  **Round 2:** `trusted_proxy(env)` and `DEFAULT_TRUSTED_PROXY` (refuses `*`).
- `agentcad/cli.py` — both `AuthStore(...)` construction sites use
  `ensure_state_dir()`. **Round 2:** `_uvicorn_proxy_kwargs(mode)` passed into
  `uvicorn.run`; `_serve_bind` validates `trusted_proxy()` in hosted mode.
- **Round 2** `agentcad/server/routes_auth.py`, `agentcad/server/security.py`
  — comments only: the login address is the real client via the proxy plumbing,
  not this function's own guarantee. No code change to the address read.
- `.gitignore` — `.env.*` with `!.env.example`.
- `docs/deployment.md`, `AGENTS.md`,
  `docs/superpowers/specs/2026-08-17-hosted-core-design.md` — as above.
- `tests/conftest.py` — `configure_private_index(..., document_scope=...)`, so
  the disagreement case is expressible (the fixture setting both is why the
  original leak tests never caught M2).
- `tests/test_security_guard.py` — six tests for the anonymous unsafe methods:
  login and enrol from a foreign origin (403, no cookie, link unspent), from
  the right origin (200), login with no Origin header (200), and a public GET
  from a foreign origin still 200.
- `tests/test_auth_routes.py` — `test_a_third_party_cannot_lock_a_known_handle_out`
  (two `TestClient`s with different `client=` addresses; attacker throttled,
  victim signs in) and
  `test_a_recovery_enrolment_kills_the_sessions_it_is_recovering_from`.
- `tests/test_authstore.py` — `test_enrolment_revokes_every_existing_session_for_that_handle`,
  including the negation (another handle's session survives).
- `tests/test_public_catalog.py` — the both-scopes-must-agree unit test, the
  end-to-end operator-private/document-public test (invisible in the listing,
  indistinguishable 404, and still visible to the authenticated search), the
  four-way cacheable-404 parametrization, and the house-envelope preview test.
- `tests/test_appmode.py` — `test_the_state_directory_itself_is_0700_however_it_was_created`,
  reproducing the AuthStore-first ordering that produced 0755. **Round 2:**
  `test_trusted_proxy_defaults_to_loopback_and_refuses_wildcard`.
- **Round 2** `tests/test_hosted_hardening.py` — `_uvicorn_proxy_kwargs` per
  mode, and `serve` refusing `AGENTCAD_TRUSTED_PROXY=*` with exit 2 before the
  service builds.
- **Round 2** `tests/test_auth_routes.py` — four topology tests wrapping the app
  in uvicorn's `ProxyHeadersMiddleware`: no-XFF collapse (the regression this
  round fixes, asserted so the doc requirement has a test), per-real-client
  keying with XFF, the forged-leading-XFF spoof guard, and direct-bind ignoring
  a client's XFF.

## Verification

Targeted suites, round 1 (this tree):

```
tests/test_security_guard.py tests/test_auth_routes.py
tests/test_public_catalog.py tests/test_authstore.py tests/test_appmode.py
    -> 211 passed in 26.14s

tests/test_hosted_hardening.py tests/test_hosted_surface.py
tests/test_prd005a_acceptance.py tests/test_claims.py
    ->  95 passed in 11.73s

pytest -k "packages or catalog or index"
    -> 743 passed, 2967 deselected in 219.65s
```

Round 2 re-ran the full set together (the seven new proxy/trusted-proxy tests
land in `test_auth_routes`, `test_hosted_hardening` and `test_appmode`):

```
tests/test_security_guard.py tests/test_auth_routes.py tests/test_public_catalog.py
tests/test_authstore.py tests/test_appmode.py tests/test_hosted_hardening.py
tests/test_hosted_surface.py tests/test_prd005a_acceptance.py tests/test_claims.py
    -> 313 passed in 30.46s
```

The kernel boundary and bypass sweep are unchanged: `test_hosted_surface.py`
(the by-equality anonymous-surface set and the runtime kernel-count proof) is in
that run and green; no route was added or removed, and the round-2 change touches
only how uvicorn resolves the socket address, never what is reachable.

**Full suite on the committed tree (all three review rounds + m11):
`make test` — 3689 passed, 1 skipped** (`uv run pytest -q -n 2 --dist
loadscope` + the serial bench/drag tail, 1484.85 s, exit 0; run at `-n 2`
because a second checkout was saturating the machine, not a small-runner
concession). It sits 4 below 0197's 3693 because the fix round replaced two
now-invalid tests (an anonymous-login test that asserted the pre-M1 behaviour,
and a scope test that set both scopes) with the stricter negation tests named
above — a deliberate substitution, not a loss. The suites listed above are the
ones this diff can move (the guard, the auth routes, the auth store, app mode,
the public catalog, hosted hardening/surface, the PRD-005a acceptance set and
claims), plus every package/index test because `indexes.py` gained a property.

Live evidence for every finding was taken against a scratch hosted server
(`--projects-dir` and `AGENTCAD_STATE_DIR` under the session scratchpad, bound
127.0.0.1:8791), before and after each fix. No frontend file changed.

### Round 3 — m11: the trust-everyone refusal was spelling-deep

The round-2 verification pass confirmed M3 holds in every topology, and found
one hardening gap: `trusted_proxy` refused the literal `*` but accepted
`0.0.0.0/0`, which uvicorn 0.52.1 reads as functionally identical to `*` —
so an operator who typed the CIDR spelling of "trust all my proxies" would
reopen M3's forgery. The refusal now rejects by *meaning*: `*` or any
prefix-length-zero network (`0.0.0.0/0`, `::/0`, in any list position, IPv4
or IPv6), via `ipaddress.ip_network(...).prefixlen == 0`. A bounded CIDR
(`0.0.0.0/1`, `10.0.0.0/8`) and a bare IP still pass — refusing them would be
wrong. The default (`127.0.0.1`) and the documented single-hop setup were
never affected; this only closes the actively-typed footgun. The existing
`test_trusted_proxy_defaults_to_loopback_and_refuses_wildcard` gained the
CIDR-all cases and a bounded-CIDR survivor.

## Notes

### Deferred, with reasons

- **m4 — `Origin`/`Host` case sensitivity and a loose `_ORIGIN_RE` that accepts
  userinfo.** Both fail *closed*: a mixed-case Host or an origin written
  `https://user@host` is refused, so the exposure is availability (a confusing
  403) rather than access. Worth a follow-up that normalises both sides and
  tightens the regex; not blocking, and normalising a comparison in a security
  guard deserves its own tests rather than a ride-along.
- **m6 — container hardening (`cap_drop`, `no-new-privileges`, `mem_limit`,
  `pids_limit`) and `AGENTCAD_SECRET_KEY` visible in `docker inspect`.** Real
  hardening, but it is deployment-profile work that overlaps PRD-006's
  territory (worker confinement), and doing it piecemeal here would set a
  profile PRD-006 then has to renegotiate. Recorded as an explicit follow-up.
  The cheap half is done: `docs/deployment.md` now documents the generated-key
  file as the recommended path and says what an explicit env key exposes.
- **m10 — `SameSite=Lax` plus read-triggering GET rebuilds.** A cross-site
  top-level GET can make the server do work it did not need to do. Blind (the
  attacker sees nothing) and a nuisance-CPU issue rather than a data one;
  follow-up.

### Gotchas for the next reader

- The reviewer's `scope2.py` still prints `effective scopes: [('operator-private',
  'public'), ...]` after the fix. That is `index.scope`, PRD-011's
  document-aware property, deliberately left alone; the access decision is
  `configured_scope`. Reading `scope` and calling it an access answer is the
  bug — if you add a third consumer, say which of the two you mean.
- The `_scope()` reentrancy that lets `enrol` call `revoke_sessions_for` is a
  `threading.RLock` plus an explicit depth counter for the flock. A future
  helper that takes the lock from a *different* `AuthStore` instance on the
  same root is still serialised (the guard registry is keyed on the resolved
  root), but one that opened its own flock would deadlock.
- `AppError.headers` is empty everywhere except `routes_public._miss()`. It
  exists because raising discards the handler's `Response`; if you find
  yourself setting a header and then raising, this is the seam.
