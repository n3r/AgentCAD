# Hosted core (PRD-005a / "005-lite") — design

- **PRD:** [PRD-005a](../../prd/in-progress/PRD-005a-hosted-core.md), carved
  out of [PRD-005](../../prd/pending/PRD-005-multi-tenant-cloud.md)
- **Date:** 2026-08-17
- **Roadmap position:** **step 2 of the marketplace chain**
  ([roadmap.md](../../roadmap.md), "Sequencing decision — the marketplace
  chain (16 Aug 2026)"). Step 1 (PRD-011) is done; steps 3 (PRD-007) and 4
  (PRD-031a) are what this unblocks, and neither needs multi-tenancy.
- **Depends on (completed):** PRD-011 (the `scope: "public"` index is the
  public-read payload) · PRD-008 (presence, claims and turn locks are the
  concurrency model this design keeps instead of inventing roles) ·
  PRD-004 (the ephemeral-service containment rules this design must not
  disturb)
- **Explicitly does NOT depend on:** PRD-006. That is the whole risk of this
  slice, and Decision 1 is about paying for it honestly.
- **Plan:** [2026-08-17-hosted-core.md](../plans/2026-08-17-hosted-core.md)

---

## Problem

Two roadmap steps are blocked on one thing that is not multi-tenancy.
PRD-007 needs a hosted instance a stranger can open. PRD-031a needs a public
URL listing validated packages and an account that can add one to a library.
Orgs, roles, audit principals and git sync — the expensive parts of PRD-005 —
are needed by neither.

The gap is total. `agentcad/cli.py:170` binds `host="127.0.0.1"` with no flag
to change it. `agentcad/server/app.py:117-131` installs exactly one
middleware, whose entire access-control content is a Host allowlist and an
Origin equality check — measures against DNS rebinding and CSRF *against a
loopback server*, and `docs/changelog/0011-backend-review-fixes-user-guide.md:57`
already records that they are "not a substitute for auth if the server is
ever exposed". Identity is the `X-Agent-Id` header dropped verbatim into a
ContextVar (`app.py:130` → `core/locks.py:109`), which `locks.py:76-81`
describes as arriving "on a header anyone can set, on a server with no
authentication, so it is bounded rather than trusted".

So the design work is not "add a login form". It is three questions, and the
first one decides the other two:

1. **What is a hosted AgentCAD without PRD-006?** A part script is arbitrary
   Python `exec`'d in the kernel worker (`agentcad/kernel/worker.py:57-59`),
   and `agentcad/kernel/sandbox.py:29` gates the entire confinement module on
   `sys.platform == "darwin"`. **Linux — the platform a deployment runs on —
   has no confinement at all.** Any design that pretends otherwise ships a
   remote shell.
2. **What may an anonymous request touch?** Twenty-eight of the roughly
   seventy mounted routes can reach the kernel. The answer has to be an
   enumeration that a test defends, not a policy nobody can check.
3. **What do two accounts mean on one instance,** when the thing that would
   make roles meaningful (isolation) is precisely what is deferred?

---

## Architecture at a glance

```
                 anonymous                       authenticated
                     │                                 │
   ┌─────────────────┴─────────────┐   ┌───────────────┴─────────────────┐
   │ /  /js /css /vendor           │   │ everything else (default deny)   │
   │ GET /api/health   (trimmed)   │   │  · session cookie  (browser)     │
   │ POST /api/auth/login          │   │  · Bearer acad_…   (agent/CI)    │
   │ GET|POST /api/auth/enrol/{t}  │   └───────────────┬─────────────────┘
   │ GET /api/public/packages/**   │                   │
   └─────────────────┬─────────────┘                   │
                     └──────────────┬──────────────────┘
                                    ▼
      agentcad/server/app.py  create_app(..., security=SecurityConfig|None)
        one middleware  ──►  agentcad/server/security.py
                               PUBLIC_PATHS (the enumeration)
                               resolve_principal(request) -> Principal|None
                               guard(request) -> Response|None
                               set_client_id("user:nikita/browser:7f3a1b2c")
                                    │
        ┌───────────────────────────┼──────────────────────────────┐
        ▼                           ▼                              ▼
 routes_auth.py            routes_public.py               every existing pack
 (management)              (index reads only,             (unchanged, now
        │                   never a kernel call)           principal-aware
        ▼                           │                      for free)
 agentcad/core/authstore.py         │
   <config-dir>/state/auth/*.json   └─► service.packages, indexes where
   atomic writes + fcntl.flock          scope == "public"  (data on disk)
   scrypt passwords · sha256 secrets

 deployment: Dockerfile + compose.yaml → one service, one /data volume
             /data/projects · /data/state · /data/home (~/.agentcad)
```

Nothing enters `AgentCADService`. Identity lives in the **app layer**, which
is why PRD-004's and PRD-011's ephemeral services
(`core/checks.py:801-851`, `core/packages/gate.py:197-234`) are unaffected by
construction rather than by care.

---

## Decision 1 — the trust boundary is "the internet vs the people we invited", and nothing finer

**An account on a 005-lite instance is a remote shell.** The chain is short
and each link is in the tree today:

- `PUT /api/projects/{proj}/parts/{id}` (`app.py:203`) accepts a Python
  script; so do `POST …/parts` (`app.py:188`), `PUT …/specs/file`
  (`routes_specs.py:86`) and `POST /api/tools/{name}` (`app.py:308`).
- The script reaches `exec(code, ns)` in `agentcad/kernel/worker.py:57-59`.
- `sandbox.wrap_argv` (`sandbox.py:101-105`) returns the argv unchanged when
  `supported()` is false, and `supported()` is
  `sys.platform == "darwin" and os.path.isfile("/usr/bin/sandbox-exec")`
  (`sandbox.py:29`).

Therefore, on a Linux host, every authenticated member can read, write and
execute as the server user. Three consequences follow, and the design commits
to all three rather than hiding any:

1. **Registration is closed.** There is no `/api/auth/register`. Accounts are
   minted by an admin from the CLI. An open sign-up form on a 005-lite
   instance would be a public shell handed out over HTTPS.
2. **Roles are not a security boundary between members** (Decision 5). Since
   any member can execute code as the server user, a per-project ACL would be
   a label, not a boundary. Claiming otherwise would be the dishonest part.
3. **The anonymous surface must not be able to reach the kernel at all**
   (Decision 8), and that is provable rather than asserted (AC7).

This sentence — *"an account on this instance can execute arbitrary Python on
the host; give one only to someone you would give a shell to"* — appears in
`docs/deployment.md`, in the `compose.yaml` header comment, in
`agentcad admin user add --help`, and in the output of a successful
`agentcad admin user add`. Four places, on the PRD-011 precedent (Decision 11
of that spec put "the gate is not a security boundary" in eight).

**What PRD-006 changes.** Linux confinement (seccomp/landlock/namespaces) plus
cgroup CPU/memory/pid caps turn "a member is a shell" into "a member is a
bounded compute job", which is the precondition for open registration,
for third-party code on our servers (031b), and for per-project roles to mean
something. 005-lite ships the deployment PRD-006 will harden, not a
substitute for it.

---

## Decision 2 — is a bounded-param rebuild on a member-authored script defensible without PRD-006? Yes — and that is a different question from arbitrary upload

PRD-007's customizer is the reason this question cannot be deferred: the
whole feature is *a logged-out stranger moves a slider and the server
rebuilds*. The mandate asks whether that is shippable before PRD-006. Working
it through explicitly:

**What the visitor actually supplies.** PRD-007 FR8 restricts the input to
the script's declared typed `PARAMS` — number/int/bool/enum/string with
min/max/choices/max_len — validated with `set_params` semantics. Params reach
the worker as the `p` mapping passed to `build(p)`; they are **data**, never
source. There is no `eval` of visitor input anywhere in that path. So a
visitor does **not** gain code execution. That is a categorically different
threat from "a stranger uploads a script", which 005-lite refuses outright and
which is exactly what PRD-031b needs PRD-006 for.

**What the visitor does gain.** Unbounded *compute against somebody else's
code*: a param can drive an O(n³) loop, a fillet count, a tessellation
density. And the author's script — arbitrary Python by a *member* — is
running on our host either way, which Decision 1 already accounts for.

**What already bounds it, today, with no new machinery:**

| Threat | Existing mitigation | file:line |
|---|---|---|
| Runaway script | per-request timeout + kill-and-respawn | `agentcad/kernel/client.py` (`KernelClient` lifecycle), `docs/architecture.md:38-40` |
| Repeated identical variants | content-hash mesh cache | `core/service.py` rebuild cache; PRD-007 FR9 keys on it |
| Request floods | `TokenBucket` | `core/presence.py:137` |
| Worker poisoning across parts | pool affinity routing | `kernel/pool.py`, the `affinity=` seam |

**What is still missing without PRD-006:** memory caps (a params-driven
balloon still OOMs the host), process caps (a fork bomb in an author's script),
disk budget, and network egress denial from the worker on Linux.

**Verdict, recorded here so PRD-007 does not have to re-derive it:**
bounded-param rebuilds of *member-authored, gate-known* scripts are
defensible on a single-operator instance before PRD-006, provided (a) params
are validated server-side with `set_params` parity, (b) per-link and per-IP
token buckets are enforced, (c) the variant cache is in front, (d) the owner's
store is never written, and (e) the operator accepts that a hostile author is
still a host compromise — which Decision 1 already says out loud.
**Arbitrary script upload by an unauthenticated party is refused until PRD-006,
without exception.**

**And 005-lite itself ships neither.** It ships the seam PRD-007 needs
(`PUBLIC_PATHS`, the `PREFIX` mount, the rate-limit primitive) and this
written verdict. Its own anonymous surface makes **zero** kernel calls
(Decision 8, AC7). Keeping the customizer in PRD-007 keeps 005-lite small and
keeps the risky surface behind a feature that is designed for it.

---

## Decision 3 — two explicit modes with a binding interlock; never inferred

`AGENTCAD_MODE` ∈ {`local` (default), `hosted`}.

- **local:** byte-identical to today. `create_app(..., security=None)`; the
  middleware body is the code that is there now. This is not "auth disabled",
  it is *the same code path*, so AC9 is a property of the design rather than a
  test to keep passing.
- **hosted:** refuses to start without `AGENTCAD_PUBLIC_ORIGIN` and a session
  secret (`AGENTCAD_SECRET_KEY`, or one generated once and persisted `0600` in
  the state dir). The startup error names the missing setting.

**The interlock, which is the load-bearing part:**

| | `--host` loopback | `--host` non-loopback |
|---|---|---|
| `local` | today's behaviour | **refused** — "binding a non-loopback interface requires AGENTCAD_MODE=hosted" |
| `hosted` | allowed (reverse proxy on the same host / container port publishing) | allowed |

You cannot expose an interface without turning auth on. Mode is never derived
from "is auth configured", because a derived mode fails *open* on a typo — the
one failure direction that must be impossible.

*Rejected: three modes (`local` / `lan` / `hosted`).* A "trusted LAN" mode is
the posture that produces the incident; a LAN is a network, not a trust
boundary, and 005-lite's whole thesis is that an account is a shell.

---

## Decision 4 — identity: invite-only local accounts, sessions for browsers, bearer tokens for agents

Four candidates were weighed against three constraints: **no new runtime
dependency** (PRD-011's precedent, and every dependency is a supply-chain
surface on a box that already runs arbitrary Python), **works air-gapped**
(PRD-005 G6, and A&D self-hosters are the named segment), and **does not grow
into the deferred orgs/roles work**.

| Mechanism | New dependency | Air-gapped | Verdict |
|---|---|---|---|
| **Local accounts, handle + password** | none — `hashlib.scrypt`, `secrets`, `hmac` are stdlib | yes | **chosen** |
| WebAuthn passkeys | a crypto/CBOR library + a non-trivial browser flow | yes | PRD-005 (FR1); additive later |
| OIDC | an OIDC client + an IdP the self-hoster may not have | no (needs an IdP) | PRD-005 |
| Magic links | SMTP or a mail provider | **no** | rejected outright |

Password storage: `hashlib.scrypt(n=2**15, r=8, p=1)` with a per-user
16-byte salt, parameters stored beside the digest so they can be raised later,
compared with `hmac.compare_digest`. Cost is ~100 ms, which is why it is on
the login path only.

**Sessions (browser).** `secrets.token_urlsafe(32)` in a cookie
`agentcad_session`; `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` whenever
the public origin is `https`. The server stores the **SHA-256 digest**, not
the secret, so reading the state file does not yield live sessions. Sliding
TTL 14 days, absolute cap 30 days; `last_seen` is only rewritten when it
crosses a day boundary, so a busy session does not rewrite the store on every
request. Logout deletes the row — revocation is immediate because the store is
the authority.

*Rejected: JWT.* Statelessness buys nothing here (one process, one store) and
costs immediate revocation, which FR7 requires.

**Tokens (agent/CI/MCP).** `acad_<id8>_<secret43>`, shown once, stored as a
SHA-256 digest, optional expiry, revocable. **Deliberately not scrypt:** with
≥256 bits of entropy there is nothing to brute-force, and scrypt would put
~100 ms on every single agent request. This asymmetry is recorded here
because it looks like an inconsistency and is not.

**Enrolment, not registration.** `agentcad admin user add <handle> [--admin]`
creates a disabled account and prints a single-use, 7-day URL
`/api/auth/enrol/<token>`; the enrolee sets a password there and lands signed
in. No email, no SMTP, works air-gapped, works over `docker compose exec`.
Second use of the token `404`s. Spending a link **revokes every existing
session for that handle** — the path is also the recovery path for a *stolen*
password (review finding M4 — changelog 0198).

**Rate limiting.** Login is bucketed per `(handle, address)` **and** per
client address,
reusing `agentcad.core.presence.TokenBucket` (`presence.py:137`) *by import* —
no edit to PRD-008 code. Unknown handle and wrong password return identical
bodies; the unknown-handle path performs a dummy scrypt against a fixed salt
so the timings do not separate. PRD-007 will want the bucket as a shared
module; promoting it is that PRD's change, noted here so it is not
rediscovered.

> **Correction (review finding M3 — changelog 0198).** "Per handle" as written
> was a lockout primitive: `TokenBucket.take` does not consume on refusal, so a
> stranger spending 0.5 req/s against a *known* handle — and handles are public
> — held its bucket empty indefinitely and the owner could never sign in. The
> key is `(handle, address)`; the per-address bucket is unchanged.
>
> **Correction, round 2 (same finding).** `address` is `request.client.host`,
> the raw socket peer — which behind the reverse proxy Decision 2 prescribes is
> the proxy for every client, collapsing the key back to per-handle. The fix
> parses `X-Forwarded-For`: uvicorn runs with `proxy_headers` bounded to
> `AGENTCAD_TRUSTED_PROXY` (default the local proxy; `*` refused) so it resolves
> the real client, and the documented proxy must forward the header. This is
> option (a) — a hosted app has to parse the forwarded address eventually
> (audit principals in full PRD-005), so it is done right rather than dropped.

---

## Decision 5 — one shared project space, two instance roles, and PRD-008 as the concurrency model

Three candidate models:

- **(A) Every member reads and writes every project; `admin` adds user and
  token management.** ← chosen
- (B) Owner-only write, others read. Needs an owner field on projects, which
  is manifest or sidecar state — i.e. the beginning of the tenancy schema
  PRD-005 owns, invented twice.
- (C) Reuse PRD-008 claims as authorization. Wrong by construction: a claim is
  soft, 90-second, human-vs-human, never for the turn holder, and explicitly
  "a way to stop two people silently clobbering each other, not a permission
  system" (`core/locks.py:242-243`).

(A) wins for one reason that is not laziness: **Decision 1 means a per-project
boundary would be fiction.** A member who cannot write project B through the
API can write it through a part script in project A. Shipping (B) would be
shipping a UI affordance labelled as security. (A) states the truth and lets
PRD-008's real machinery — project-wide turn locks, per-part claims, presence,
comment attribution, per-client branch checkouts — do the coordination it was
built for, now naming real people.

**What 005-full revisits, recorded so it is not a surprise:** orgs →
workspaces → projects with per-tenant storage roots (PRD-005 FR5); per-project
view/comment/edit/admin roles at `write_guard` / tool dispatch / read routes
(FR6); tenant-scoped WebSocket channels; audit principals (FR12); per-tenant
fair scheduling (FR11). Nothing in 005-lite blocks any of them: `admin` and
`member` become the org-level default policy, and every project on a 005-lite
instance becomes one workspace of one org.

---

## Decision 6 — the composed principal, and the one line of PRD-002 that must change

The principal is a string, because that is what the whole product already
consumes through `locks.set_client_id` (`locks.py:109`). Composition:

| Credential | client identity |
|---|---|
| session cookie | `user:<handle>/<device>` where `<device>` is the browser's own `browser:<8hex>` from `frontend/js/api.js:38-70` |
| bearer token | `agent:<token-name>` |
| none (public route) | never set — the guard rejects before any handler runs |

`<device>` is kept because it is what makes two tabs one client and two
browsers two clients, which the per-client branch checkout
(`core/branches.py:262-268`) and presence both rely on. `X-Agent-Id` in hosted
mode contributes *only* that suffix, and only after `check_client_id`
validation; it is never the identity.

**Length is a hard constraint, not a style note.** `MAX_CLIENT_ID_CHARS = 64`
and `check_client_id` **refuses rather than truncates** (`locks.py:81`,
`:84-101`) precisely to prevent silent identity merges. `user:` (5) + handle
(≤32) + `/browser:` (9) + 8 = ≤54. Hence the handle grammar
`[a-z0-9][a-z0-9._-]{0,31}` in FR4 — it is derived from this arithmetic.

### The `actor_kind` trap — the sharpest finding in this design

`agentcad/core/proposals.py:112-124`:

```python
return "human" if identity == "browser" or identity.startswith("browser:") else "agent"
```

`user:nikita/browser:7f3a1b2c` does **not** start with `browser:`, so every
signed-in human would be classified `agent`. That is not cosmetic:

- `ClaimRegistry.acquire` returns `None` for a non-human holder
  (`locks.py:292-293`) — **no hosted human could ever hold a per-part claim.**
- `_blocking` never blocks an agent (`locks.py:398-399`) — so nobody would be
  protected from anybody.
- Presence would render every person with the agent affordance
  (`presence.py:297`), and comment `author_kind` would lie
  (`comments.py:91`).

PRD-008's entire concurrency protection would silently switch off on the day
hosting turned on, with no error anywhere. The fix is two lines in
`actor_kind` — recognise `user:` as human and `agent:` as agent, leaving the
`browser*`/else behaviour byte-identical — and the function's own docstring
commissions exactly this: *"PRD-005 replaces it with the authenticated
principal's class, with no schema change."* Its four consumers
(`comments.py:91`, `presence.py:63`, `locks.py:148-157`, proposals itself)
import it rather than re-implement it, so one edit fixes all four. AC10 pins
the resulting claim semantics against `tests/test_claims.py`'s fixtures.

### Two consequences of a new identity string, accepted

1. **`checkouts.json` orphans.** `core/branches.py:262-268` persists raw
   client ids as JSON object keys. A member's first hosted request carries a
   new identity, so they land on the default branch — which is the documented
   first-run behaviour already pinned by `tests/test_presence.py:433-450`.
   Benign; recorded so it is not debugged twice.
2. **Mentions degrade for offline members.** `comments.plausible_mention`
   (`comments.py:193-221`) accepts `browser`/`chat` plus whoever is in the live
   presence roster, so `@user:nikita` resolves while Nikita is connected and
   not while he is offline. Fixing it means editing finished PRD-008 code for
   a cosmetic gain; PRD-005's member list is the right owner. Recorded, not
   fixed.

---

## Decision 7 — the security seam: one sanctioned core edit, and a second two-line one

**The middleware extension point does not exist.** Verified: `create_app`
installs exactly one `@app.middleware("http")` (`app.py:117-131`); route packs
receive `(service, registry)` and return an `APIRouter`
(`app.py:401-414`); `app.include_router` cannot add middleware and packs never
see `app`. Two route packs already document having designed around this —
`routes_presence.py:16-22` ("`/ws` is in `server/app.py`, a core this feature
may not edit … a route pack cannot see `create_app`'s `allowed_hosts`") and
`routes_presence.py:172-176`.

Two ways to fix it:

- **(A) Edit `create_app`.** One optional parameter `security=None`; the
  middleware body delegates to a new `agentcad/server/security.py`; the
  WebSocket handler makes the same call before `accept()`. **Chosen.**
  PRD-005's own technical approach pre-authorises it: *"an identity layer in
  the one middleware that already assigns client identity in `app.create_app`
  — the sanctioned core touch, since that seam exists precisely for this"*
  (PRD-005:186-188).
- (B) A discovered `middleware_*.py` pack loader, mirroring
  `_mount_route_packs`. **Rejected, and the reason is the important part:**
  pack discovery is fail-*open* — `_mount_route_packs` silently skips a module
  with no `router`, and `_load_tool_packs` has the same shape. A security
  middleware that silently fails to load leaves the instance wide open with no
  signal. Auth must be constructed explicitly by the caller and must fail
  closed.

> **⚠️ Flagged core edit.** `agentcad/server/app.py` is edited twice by this
> feature and by nothing else: the middleware body plus one `create_app`
> parameter, and the WebSocket guard call. All real logic lives in
> `server/security.py`, so the diff to `app.py` is reviewable at a glance. A
> test pins the middleware count at one and asserts the local-mode code path
> is unchanged.

**The second, two-line core seam.** `_mount_route_packs` hardcodes
`prefix="/api"` (`app.py:414`). PRD-007 FR5/FR6 need `/s/<token>` and
`/embed/<token>` at the *root*, which no route pack can express today. Letting
the loader honour an optional module-level `PREFIX` (default `"/api"`) costs
two lines here and saves PRD-007 a third core edit. Shipped now, used later.

**The guard, in order:**

1. Local mode → today's `_browser_request_allowed` + `set_client_id(header or
   "browser")`. Return.
2. Hosted mode → Host must equal the configured public origin's host.
3. Resolve a principal: `Authorization: Bearer` first, then the session
   cookie. Unknown/expired/revoked → no principal.
4. If the path matches `PUBLIC_PATHS` → allow, do **not** set a client id, and
   mark the request anonymous (handlers can read it for the trimmed health
   body).
5. Otherwise no principal → `401 auth_error`.
6. Cookie-authenticated **state-changing** methods (POST/PUT/PATCH/DELETE):
   `Origin`, when present, must equal the configured public origin →
   otherwise `403`. Bearer requests are exempt (a browser cannot attach a
   bearer cross-site) and so are safe methods. `SameSite=Lax` is the second
   layer.
7. `set_client_id(composed)` after `check_client_id` validation — closing the
   `app.py:130` gap where the ContextVar is set without validation today.
8. Admin-only routes check the role in `routes_auth.py`, not the middleware;
   there are five of them and a path-pattern list would be a second place to
   get wrong.

> **Correction (PRD-005a security review, finding M1 — changelog 0198).** The
> order above is wrong, and the implementation copied it faithfully: step 4
> returns for the anonymous surface, so step 6 was never reached by
> `POST /api/auth/login` or `POST /api/auth/enrol/{token}` — the only unsafe
> methods an anonymous caller can reach, and precisely the ones a cross-site
> POST would target (signing a victim into the attacker's account, or spending
> an enrolment link). **Step 6 runs before step 4**, and it treats "no
> principal" as "not a bearer". Origin-absent stays allowed, identically on
> both branches.

`PUBLIC_PATHS` is a literal in `security.py` — exact paths plus two prefixes
(`/api/public/`, `/api/auth/enrol/`) — and **default deny** means a route pack
added tomorrow is private with no action by its author (FR13).

---

## Decision 8 — the public surface, route by route

The table below is the whole exposure decision. **Public** means reachable
with no credential in hosted mode. Everything not listed as public is `401`.
The route inventory is grounded in the files named; **K** marks a route that
can reach `exec()` in the kernel worker.

### Public in hosted mode — the entire list

| Method | Path | file:line | Why safe |
|---|---|---|---|
| GET | `/` | `app.py:389` | `index.html` off disk; the app is useless without a login page |
| GET | `/js/**`, `/css/**`, `/vendor/**` | `app.py:393-396` | `StaticFiles` off disk |
| GET | `/api/health` | `app.py:152` | **trimmed** to `{status, mode}` in hosted mode (FR21); the full body — version, kernel state, chat availability, sandbox status — needs a principal |
| POST | `/api/auth/login` | new `routes_auth.py` | rate-limited per handle and per address; indistinguishable failures |
| GET·POST | `/api/auth/enrol/{token}` | new `routes_auth.py` | single-use, 7-day, admin-minted, unguessable |
| GET | `/api/public/packages` | new `routes_public.py` | index JSON on disk, `scope == "public"` only |
| GET | `/api/public/packages/{name}` | new `routes_public.py` | ditto |
| GET | `/api/public/packages/{name}/versions/{version}` | new `routes_public.py` | ditto |
| GET | `/api/public/packages/{name}/versions/{version}/preview` | new `routes_public.py` | shipped PNG, resolved with `content.resolve_within` |

Nine entries, zero kernel calls, zero project state. That is the complete
anonymous attack surface of a 005-lite instance.

### Private — grouped by why, with the kernel column

| Group | Routes | Kernel | Disposition in hosted mode |
|---|---|---|---|
| **Script/tool ingress (RCE by design)** | `POST`/`PUT /api/projects/{p}/parts[/{id}]` (`app.py:188`, `:203`), `PUT /api/projects/{p}/specs/file` (`routes_specs.py:86`), `POST /api/tools/{name}` (`app.py:308`), `POST /api/chat` (`app.py:317`) | **K** | authenticated members only, forever (Decision 1) |
| **Rebuild-on-read** | `GET …/parts/{id}` (`app.py:199`), `…/metrics` (`:224`), `…/mesh` (`:228`), `…/mesh/faces` (`:244`), `GET …/assembly` (`app.py:274`), `POST …/interference` (`:283`), `POST …/render` (`routes_vision.py:14`), `POST …/analyze` + 3 FEM (`routes_analysis.py:12,48,57,65`), proposal renders/packet (`routes_proposals.py:126,133,139`) | **K** | authenticated. These are why "public read of a project" is not on the table: a `GET` here is a kernel build |
| **Project & part mutation** | `POST /api/projects` (`app.py:172`), `DELETE …/parts/{id}` (`:219`), exports (`:265`, `:288`), assembly PATCH (`routes_assembly2.py:13`), drawings (`routes_drawing.py:12,20`), specs run (`routes_specs.py:76`), checks (`routes_checks.py:76`), undo/redo (`routes_undo.py:33,42`), branches/versions/merge (`routes_versioning.py:77-133`), proposals (`routes_proposals.py:99-174`), history/restore (`routes_history.py:18,22`), materials (`routes_materials.py:11,15`), imports upload (`routes_import.py:14`) | mixed **K** | authenticated |
| **Collaboration state** | comments (`routes_comments.py:106-168`), presence (`routes_presence.py:114,119,167`) | no | authenticated; plus FR20's beacon fix below |
| **Package management** | `GET /api/packages/search` (`routes_packages.py:98`), `…/preview` (`:137`), project package add/remove/use (`:114,118,125,130`) | `use` is **K** | **stay authenticated** — `search` and `preview` walk *all configured indexes* including `scope: "private"` ones, so exposing them would leak a private index. The public read is the separate `routes_public.py` surface, filtered by scope. This is the single most important detail of Decision 8 |
| **Event channel** | `WS /ws` (`app.py:342`) | no | authenticated (cookie on the upgrade); the bus is global, which is correct for one shared project space |
| **Filesystem reach** | `POST /api/projects/open` (`app.py:177`) registers *any* absolute path as a project; `import_cad_file` (`core/tools_import.py:13-19`) ingests any absolute path | no | **disabled in hosted mode** with a structured error naming the mode (FR19). These are local-tool affordances that make no sense on a server and are a host-filesystem read primitive there |

### Why the public catalog read is a separate route pack, not a flag on `routes_packages.py`

`GET /api/packages/search` iterates `manager.indexes` (`routes_packages.py:98`
→ `search_packages`), and `…/preview` loops over every configured index
(`routes_packages.py:153-155`). A user's private git index would be searchable
and its previews served. `routes_public.py` therefore does its own filtering:
a filter on the index's scope. A package carried only by a private index
returns the same `404` as one that does not exist — no oracle.

> **Correction (review finding M2 — changelog 0198).** The filter as designed
> was `ix.scope == "public"`, reusing the property PRD-011 made load-bearing
> for *publish policy* (`indexes.py`; `LocalIndex.publish` refuses
> non-redistributable vendor content into a `public` index). That property
> lets the index **document** win over the operator's configuration, which is
> right for policy and wrong for access control: for a git index the third
> party who authors `index.json` would decide whether this instance serves it
> to the internet, overriding a configured `scope: "private"`. The filter now
> requires `ix.configured_scope == "public"` (the operator's own word) **and**
> `ix.scope == "public"` (so a document saying `private` still hides itself).
> PRD-011's `scope` property is unchanged.

The payload is exactly what `catalog/index.json` already ships: name, version,
`content_id`, summary, license, disclosure, per-part `params` (name, type,
choices), `connectors`, `specs`, `presets`, `previews`, and the `gate` block
with its verdict. That is precisely PRD-031a's browse surface, pre-generated,
and it costs one file read.

---

## Decision 9 — threat model: what changes on a non-localhost bind, and what 005-lite refuses

### The eight things a loopback bind was silently providing

| # | What loopback provided | 005-lite replacement |
|---|---|---|
| 1 | Only local processes could connect | authentication (Decision 4) + the mode interlock (Decision 3) |
| 2 | `Host` ∈ `LOCAL_HOSTNAMES` was a real filter (`app.py:57,79`) | Host must equal the configured public origin's host |
| 3 | `Origin == f"http://{host}"` (`app.py:82`) — hardcoded `http` | compared against the configured origin, `https` included |
| 4 | No credential existed, so CSRF had nothing to steal | `SameSite=Lax` + the Origin check on state-changing cookie-authenticated methods; bearer exempt |
| 5 | `X-Agent-Id` was harmless because everyone was already the same user | header is no longer an identity; principal is server-derived and validated |
| 6 | `POST /api/projects/open` could only reach the operator's own disk | disabled in hosted mode |
| 7 | Every expensive `GET` was self-inflicted DoS | authentication is the gate; per-route quotas are PRD-006/PRD-007 |
| 8 | The presence beacon could only be forged by the operator | beacon identity must be within the caller's principal namespace |

### The beacon (`routes_presence.py:72-82`, `:128-144`)

`_beacon_identity` is the only body-sourced identity on the whole router,
and it **truncates to 64** where `locks.check_client_id` **refuses** — so two
identities differing only past character 64 are one leave target. PRD-008
already narrowed the blast radius to "drop a roster row" (claims are left to
their 90-second TTL). In hosted mode the guard tightens it further: a beacon
identity that is not the caller's own principal (or a `<device>` under it) is
refused. The route pack does this itself by comparing against
`locks.current_client_id()`; no PRD-008 core file changes.

### Residual risks 005-lite does not close (and says so)

- **A hostile member owns the host** until PRD-006. Mitigation is posture:
  closed registration, a single-purpose VM, the four-place trust statement.
- **An authenticated DoS.** Any member can queue an expensive kernel job; the
  pool has a per-request timeout but no CPU/memory budget. PRD-006 FR G3.
- **No audit log.** History trailers (`core/history.py:113`), proposal and
  comment `audit.jsonl` carry attribution, but there is no per-instance
  queryable log. PRD-005 FR12.
- **Secrets at rest.** The state files are `0600` in a volume; there is no
  envelope encryption. Operator responsibility, documented.
- **Anonymous read is cacheable and unmetered.** `routes_public.py` sets
  `Cache-Control: public, max-age=300` so a CDN or reverse proxy absorbs a
  flood; there is no per-IP limit on it in 005-lite because the payload is a
  static file read.

---

## Decision 10 — where identity state lives, and why nothing else breaks

`agentcad/core/authstore.py`, four atomic JSON documents under
`<config-dir>/state/auth/`:

```
users.json        {handle: {role, disabled, created, password: {kdf,n,r,p,salt,digest}}}
enrolments.json   {sha256(token): {handle, expires, used}}
sessions.json     {sha256(secret): {handle, device, created, last_seen, expires}}
tokens.json       {token_id: {name, role, digest, created, expires, revoked}}
```

**Path derivation is the load-bearing choice.** `config.config_path().parent`
— the same derivation `AGENTCAD_PACKAGES_DIR` and `AGENTCAD_INDEXES_DIR`
already use (`core/packages/cache.py:96-104`, `core/packages/_git.py:98-106`),
whose docstrings say why: *"so the `AGENTCAD_CONFIG` override every test
already sets keeps the cache out of a real home directory too."* Every test
that sets `AGENTCAD_CONFIG` gets an isolated auth store for free. An
`AGENTCAD_STATE_DIR` override exists for the container.

**Why not SQLite, diverging from PRD-005 FR-approach.** PRD-005 specifies
"per-instance SQLite (WAL)" and its open question names *audit volume and
membership queries* as the motivation — and **the audit log is deferred out of
005-lite**. What remains is tens of records. Against that: the repository
contains zero SQLite today (every store is atomic JSON, JSONL or git), WAL
sidecars complicate the backup story FR26 has to write, and a schema/migration
mechanism would be new machinery for five documents. Plain atomic JSON is the
house primitive, and `tar` of the volume is a correct backup because every
write is an `os.replace`. If PRD-005's audit log later needs SQLite, it can
introduce it for the audit log; nothing here blocks that.

**Concurrency is the real hazard, and it has a precedent.** `agentcad admin
user add` run through `docker compose exec` is a **second process** writing
the same files while the server holds them in memory. This is exactly
PRD-011's situation — *"`LocalIndex.publish/yank` under `_index_scope`
(in-process **plus** `fcntl.flock`, because publishing is a CLI action and the
two writers are routinely two processes)"* (`indexes.py:502`; AGENTS.md
"Package gotchas"). Same solution: a `threading.Lock` plus `fcntl.flock`
around every read-modify-write, and the server re-reads on mtime change so an
admin-created account works without a restart. `fcntl` is POSIX-only; hosted
mode is Linux/macOS only and says so. Local mode never constructs the store,
so Windows local use is untouched.

**Why nothing else breaks:**

- **PRD-004/011 ephemeral services.** `AgentCADService.__init__` takes
  `(projects_dir, kernel, bus)` and touches nothing global
  (`core/service.py:91-118`). The auth store is constructed by `create_app`'s
  caller, never by the service, so `checks._ephemeral_service`
  (`checks.py:801-851`) and `gate._ephemeral_service` (`gate.py:197-234`) are
  unaffected **by construction**. This is the direct answer to "what would
  break if a users DB were required at construction": nothing, because it is
  not.
- **`--projects-dir` isolation.** The state dir is derived from the config
  path, not the projects dir. A `--projects-dir` pointed anywhere never sees
  identity data, and identity data is never inside a project, a `.history`, or
  a manifest — so manifest merge, `git add -A`, `project_restore` and branch
  worktrees cannot touch it.
- **Sandbox writable roots.** The store is written by the *server* process,
  never the kernel worker, so `cli._writable_roots` (`cli.py:69-86`, fixed at
  spawn) needs no change. It happens to be under `~/.agentcad`, already a
  writable root, so even a future kernel-side reader would work.
- **Backups.** One archive of `/data` is complete: projects (with their
  `.history` git repos) plus the state dir. No file needs quiescing.

---

## Decision 11 — deployment: one image, one volume, bring-your-own TLS with a bundled escape hatch

**Image.** Multi-stage, `python:3.12-slim`. Builder runs `uv sync --locked
--no-dev`; runtime copies the venv. Runtime `apt` set is exactly what the
Linux CI job already proves is needed — `libgl1 libglu1-mesa libxrender1
libxcursor1 libxft2 libxinerama1` (`.github/workflows/ci.yml`, "Install OCCT
system libraries (Linux)") — **plus `git`**, which the history engine shells
out to (`core/history.py:47-48`) and which no existing CI step had to install
because runners ship it. Non-root user; `HOME=/data/home` so `~/.agentcad`
(config, package cache, git index checkouts, the state dir) persists.
The image is multi-GB because of the OCCT wheels; that is stated, not hidden,
and it is why the compose build cannot run on every PR (Decision 12).

**Compose.** One service, one named volume at `/data`:

```
/data/projects   AGENTCAD_PROJECTS_DIR
/data/home       HOME → ~/.agentcad → config.json, packages/, indexes/, state/
```

`healthcheck` hits `/api/health`. `restart: unless-stopped`. A commented
`proxy` profile ships a Caddy service for one-command TLS.

**TLS: bring-your-own by default, bundled by profile.** Automatic ACME as the
*default* forces a real public DNS name at first `up`, which breaks an
air-gapped install, a staging box, and the CI smoke job — the three places the
compose file most needs to work. So the default publishes plain HTTP on a
host port and `docs/deployment.md` documents putting an existing proxy in
front; `--profile proxy` adds Caddy with ACME for operators who want the
one-command path. PRD-005's "bundled ACME with an escape hatch" is inverted,
deliberately, and Decision 14 records it as a divergence.

**Resources.** ~0.5 GB RSS per kernel worker (`config.py:56-57`,
`docs/architecture.md:50-51`) plus the server. Default pool size is
`max(1, min(3, cores//3))`, which is 1 on a 2-vCPU box. Compose pins
`AGENTCAD_KERNEL_POOL_SIZE` explicitly rather than letting it float with the
host, and `docs/deployment.md` gives 2 vCPU / 4 GB as the floor for a
single-worker instance and 4 vCPU / 8 GB for three workers.

**Bundled examples off in hosted mode.** `cli._register_examples`
(`cli.py:89-99`) opens the shipped `examples/` from the resource root and
`_writable_roots` grants writes into it — in a container that is the image
layer, so edits are lost on redeploy and `.cache` writes land in an ephemeral
layer. `AGENTCAD_EXAMPLES=0` (the compose default) skips registration.

**`--host` and `AGENTCAD_HOST`.** `cli.cmd_serve` gains `--host` (default
`127.0.0.1`) subject to Decision 3's interlock, and honours `AGENTCAD_HOST` /
`AGENTCAD_PORT` so the container needs no command override.

---

## Decision 12 — testing and CI: everything that matters runs without Docker

The auth story is entirely testable through `TestClient`, which is how
2 500+ existing tests already drive the app
(`tests/test_server.py:14-22`: `create_app(service, build_registry(service),
extra_allowed_hosts={"testserver"})` + `TestClient(base_url="http://127.0.0.1")`).
Hosted-mode tests add one fixture that passes `security=SecurityConfig(...)`
with a tmp state dir and `public_origin="http://testserver"`. No Docker, no
network, no new dependency.

**The three tests that carry the design:**

1. **The enumeration test.** Walk `app.routes` of a fully-mounted hosted app;
   for each, issue an unauthenticated request; assert the set that does not
   answer `401` equals a literal list of nine. It fails when a new route pack
   goes public by accident — this is PRD-007 AC9's test, delivered one step
   early because it is what makes PRD-007 safe to write.
2. **The kernel-silence test.** Instrument `service.kernel.request` with a
   counter, exercise every public route, assert zero. FR16/AC7.
3. **The local-mode-unchanged test.** Assert `create_app(...)` with no
   `security=` installs exactly one middleware and that the existing server
   tests pass untouched. AC9 is then a property of the diff, not a hope.

**CI.** Two additions to the existing workflows, deliberately asymmetric:

- On every PR: a `docker compose config` lint (seconds, no build) plus the
  normal pytest job, which now includes the auth suite.
- On push to `main`, weekly schedule and `workflow_dispatch`: a new
  `deploy-smoke.yml` that builds the image, `compose up`s it, and asserts
  `/api/health` reports `mode: hosted`, an admin can be created through
  `docker compose exec`, login works, a private route `401`s anonymously, the
  public catalog route serves, and state survives `down` + `up`. Multi-GB
  layer caching makes this minutes, not seconds, which is why it is not a PR
  gate — the same split `ci.yml` and `geometry-ci.yml` already make between
  per-PR and nightly work.

---

## Decision 13 — the PRD carve: a new `PRD-005a`, not an annotation

House convention is one PRD per feature with folder-as-status
(`docs/prd/README.md:9-13`), and numbers are "never reused, never renumbered"
(`README.md:14-15`). Two options:

- **(A) New `docs/prd/in-progress/PRD-005a-hosted-core.md`; PRD-005 stays
  `pending/` with a carve-out header naming exactly what moved.** ← chosen.
  The roadmap already uses letter suffixes for exactly this
  (031a / 031b, roadmap.md:104-106, 169), so `005a` needs no new convention.
  Status stays truthful: 005a goes to `completed/` when its ACs verify, while
  005 stays `pending` because its remainder genuinely is.
- (B) Annotate PRD-005 in place and move it to `in-progress/`. Rejected: it
  would mark a mostly-unbuilt PRD as in-progress, and on completion would
  force either a false `completed/` or a status the folder cannot express.
  Folder-as-status stops working the moment one file has two statuses.

The remainder stays explicitly recorded: PRD-005's header gains a
"Carved out to PRD-005a" block mapping every FR and AC to *moved* or
*retained*, and the roadmap index gains a 005a row while 005's row is reworded
to the remainder. Both move in the same commit, per roadmap.md:14.

*(Incidental: `docs/prd/README.md:11` says `docs/prd/shipped/` where the tree
has `completed/`. Noted, not fixed here — it is not this feature's diff.)*

---

## Decision 14 — divergences from PRD-005 as written

| PRD-005 says | 005a does | Why |
|---|---|---|
| FR1 — OIDC + WebAuthn passkeys | local accounts, handle + password | Zero new dependencies, works air-gapped, does not grow into orgs. Passkeys/OIDC are additive credential kinds later (Decision 4) |
| Technical approach — "per-instance SQLite (WAL)" | four atomic JSON documents + `fcntl.flock` | The audit volume that motivated SQLite is deferred; the repo has no database; backup is simpler (Decision 10) |
| FR7 / risks — "default to bundled Caddy-style ACME with an escape hatch" | bring-your-own proxy by default, Caddy behind `--profile proxy` | Default ACME forces public DNS at first `up`, breaking air-gapped, staging and the CI smoke job (Decision 11) |
| FR2 — "the resolved principal flows through `client_id_var` so … history attribution … need no per-feature changes" | true **except** `actor_kind`, which needs two lines | `user:…` does not start with `browser:`, so every human would classify as an agent and PRD-008 claims would silently stop protecting anyone (Decision 6) |
| FR5/FR6 — orgs, workspaces, per-project roles | one shared space, `admin`/`member` | Deferred by the founder decision; and with RCE available to any member a per-project ACL would be a label, not a boundary (Decision 5) |
| FR12 — audit log | not shipped | Deferred; existing history/proposal/comment attribution stands |
| Risks — "single-org self-host is safe earlier" | restated more strictly: **safe only if every member is someone you would give a shell to** | "Single-org" is not the property that makes it safe; "no Linux confinement" is the property that makes it unsafe (Decision 1) |
| Agent surface — `create_agent_token`, `grant_role`, `revoke_role`, `list_members`, `sync_status` tools | only `whoami` as a tool; token and user management is **CLI-only** | Role tools presuppose roles that do not exist here. Minting credentials from the same authenticated HTTP surface those credentials unlock is a privilege-escalation shape worth avoiding while there is no audit log |
| `agentcad mcp --remote <url> --token …` | `AGENTCAD_URL` + `AGENTCAD_TOKEN` env | `AGENTCAD_URL` already exists (`agent/mcp_server.py:41-45`) and MCP configs pass env, not argv |

---

## Surfaces

**Tools (1 new):** `whoami {}` → `{principal, kind, role, mode}`. Registered
by `core/tools_auth.py` **only when a security config is present**, on the
FEM-tools precedent (register a tool only if it can run).

**Routes.** `routes_auth.py`: `POST /api/auth/login`, `POST /api/auth/logout`,
`GET /api/auth/session`, `GET|POST /api/auth/enrol/{token}`; admin-only
`GET|POST /api/auth/users`, `GET|POST|DELETE /api/auth/tokens`.
`routes_public.py`: the four `GET /api/public/packages…` reads.

**CLI.** `agentcad admin user add|list|disable`, `agentcad admin token
add|list|revoke`, `agentcad admin enrol <handle>` — all operate directly on
the state files, so they work over `docker compose exec` with no session.
`agentcad serve` gains `--host`.

**Errors.** `auth_error` (401), `permission_error` (403), `rate_limited` (429
with `details.retry_after_s`) — the same structured
`{error: {type, message, details}}` contract, added to `_ERROR_STATUS`
(`app.py:34-38`) via new `AppError` subclasses.

**Events.** None. WebSocket payloads are byte-identical.

**Frontend.** `frontend/js/auth.js` + a login/enrol view; a 401 handler in the
single `request()` funnel (`api.js:72-96`); the identity chip reads
`/api/auth/session`. No bundler, no new vendor.

---

## Data flow — the AC3 walk

1. Operator: `docker compose up -d`, then `docker compose exec agentcad
   agentcad admin user add nikita --admin`. The CLI takes `flock` on
   `users.json`, writes a disabled account, mints an enrolment token, prints
   the URL and the four-place trust sentence.
2. Nikita opens `/api/auth/enrol/<token>`. The guard matches the
   `/api/auth/enrol/` prefix in `PUBLIC_PATHS` → allowed, anonymous. He posts
   a password; `authstore` scrypts it, enables the account, marks the
   enrolment used, mints a session, sets the cookie.
3. He opens `/`. Static, public. `api.js` calls `/api/auth/session` → 200 with
   `{principal: "user:nikita", role: "admin"}`; the chip renders.
4. He edits a part. The middleware resolves the cookie → `Principal(user,
   nikita)`, composes `user:nikita/browser:7f3a1b2c` with the header's device
   suffix, validates it through `check_client_id`, and calls
   `set_client_id`. `PUT …/parts/{id}` is not in `PUBLIC_PATHS` and has a
   principal → allowed. The Origin equals the public origin → CSRF check
   passes.
5. Downstream, nothing knows about auth: `ProjectStore.write_guard` sees the
   composed id, `write_scope` names the part, `ClaimRegistry.claim_write`
   calls `actor_kind` → **`human`** (Decision 6's two lines), Nikita takes the
   claim, the history commit carries `Client: user:nikita/browser:7f3a1b2c`
   (`history.py:113`), and `project_changed` fans out on `/ws`.
6. Anya, signed in on the same instance, receives the event on her
   authenticated WebSocket, sees Nikita's avatar in the roster labelled
   `human`, and is refused the part with a claim conflict — PRD-008's
   machinery, unmodified, now naming real people.

---

## Testing strategy

- **`tests/test_authstore.py`** — pure: scrypt round-trip, digest storage,
  enrolment single-use, session sliding/absolute expiry, token revocation and
  expiry, `flock` cross-process serialisation (two subprocesses), mtime
  re-read, atomic replace under a simulated crash. No kernel, no server.
- **`tests/test_security_guard.py`** — the guard in isolation: mode matrix,
  Host/Origin matrix, bearer-vs-cookie precedence, the CSRF rule and its
  bearer exemption, the composed-identity length ceiling, `check_client_id`
  refusal.
- **`tests/test_hosted_surface.py`** — the three carrying tests of Decision 12
  (enumeration, kernel silence, local-mode-unchanged) plus the
  `/api/projects/open` and beacon refusals.
- **`tests/test_auth_routes.py`** — login/logout/session/enrol/whoami, rate
  limiting and its `retry_after_s`, indistinguishable failures, admin-only
  routes returning `permission_error` to a member.
- **`tests/test_public_catalog.py`** — the public read against the bundled
  `catalog/` (`scope: "public"`) plus a synthetic `private` index that must
  `404` indistinguishably; preview path containment.
- **`tests/test_claims.py` extension** — AC10: composed principals classify
  `human`, claim semantics identical to the `browser:<nonce>` fixtures.
- **`tests/test_deploy_config.py`** — parse `compose.yaml` and assert the
  invariants a smoke job would otherwise catch late: the volume is mounted,
  `AGENTCAD_MODE=hosted`, no secret is committed, `AGENTCAD_EXAMPLES=0`.
- **`deploy-smoke.yml`** — the only Docker-requiring verification, off the PR
  path (Decision 12).

Existing tests are not edited. Local mode is the same code path, so the whole
current suite is the regression test for AC9.

---

## Risks and open questions

- **The defining risk is Decision 1's**, and it is accepted rather than
  mitigated: until PRD-006, a member is a shell. Everything else in this
  design is downstream of saying that plainly.
- **`PUBLIC_PATHS` drift.** One careless entry widens the anonymous surface.
  Mitigation: it is a literal in one file, default-deny means silence is safe,
  and the enumeration test fails on any change.
- **Password auth will attract review pressure** toward passkeys. The
  credential table is shaped for additional kinds; the argument is
  dependencies and air-gap, not preference.
- **`fcntl` is POSIX-only.** Hosted mode is Linux/macOS. Local mode never
  constructs the store, so Windows local use is unaffected — but the
  portability suite must assert the import is lazy.
- **Image size and CI cost.** Multi-GB layers; hence the PR/nightly split.
- **Open, genuinely for the founder:** may members delete each other's
  projects? No route deletes a project today, so nothing forces it — but
  PRD-027's bulk operations will.
- **Open, genuinely for the founder:** should the anonymous catalog page be a
  real UI in 005-lite, or is the JSON surface enough until PRD-031a builds
  the browse experience? This design ships the API and leaves the page to
  031a; a shipped-but-ugly page is the alternative.

---

## Naming traps (live collisions in this tree today)

- **`security.py` vs `sandbox.py`.** `agentcad/kernel/sandbox.py` is *process
  confinement*; `agentcad/server/security.py` is *request authorisation*.
  Neither is the other, and PRD-006 owns the first.
- **"workspace".** PRD-005 means a tenancy container; PRD-025 means a UI tab
  (Build/Test/Produce/Library/Market). 005-lite uses **neither** — it says
  "instance" and "project space" — which sidesteps the collision PRD-005's own
  open questions flag, and leaves the rename decision to whichever of the two
  ships first.
- **`routes_auth.py` vs `routes_authz`.** One pack, named `routes_auth.py`;
  there is no separate authorization pack, because authorization in 005-lite
  is one role bit checked in five handlers.
- **`token`.** Three unrelated senses now live in the tree: an agent bearer
  token (this design), a share-link capability token (PRD-007), and
  `TokenBucket`'s rate-limit tokens (`presence.py:137`). Name variables
  `bearer`, `share_token` and `bucket` respectively.
- **`scope`.** A package index's `public|private` scope
  (`indexes.py:105`), `locks.write_scope` (the part a write names), and
  PRD-005's token scopes. This design uses only the first two and never
  introduces the third.
- **`state`.** `<config-dir>/state/` is *identity* state, not
  `service._status` (build state) and not `.history` (model state).

---

## PRD divergences to fold back

All of Decision 14's rows are already written into
[PRD-005a](../../prd/in-progress/PRD-005a-hosted-core.md) — it was authored
from this design rather than before it. The one thing to fold back into
**PRD-005** (which stays pending) is its carve-out header: FR1's passkey/OIDC
clause survives, its SQLite technical-approach line and its bundled-ACME
default are superseded for the hosted-core slice, and its FR2 must gain the
`actor_kind` caveat so the full PRD does not re-inherit the assumption that
principals need no per-feature changes.
