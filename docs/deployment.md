# Deploying AgentCAD (hosted mode)

> **Read this first.** An account on this instance can
> execute arbitrary Python on the host; give one only to someone you would
> give a shell to.
>
> A part script *is* arbitrary Python — that is the product
> (`agentcad/kernel/worker.py`). Since PRD-006 that Python runs **confined**
> on Linux: the worker applies a Landlock ruleset and a seccomp filter to
> itself before it imports any geometry, so a script gets **no network**,
> writes only inside the granted roots (the projects tree, the server's work
> root and its own private temp dir), and —
> under `AGENTCAD_MODE=hosted` — cannot read the state directory, nor
> **anything under the server user's home**. Memory, process
> count and CPU are capped
> ([below](#confinement-and-quotas)); a breach kills that one worker and
> comes back as an ordinary build error.
>
> That is a real improvement and it is **not** isolation between the people
> using the instance. What has not changed:
>
> - **A member's script still runs as the server user, and the whole projects
>   tree is readable and writable to it.** Every project on the instance, not
>   just theirs — not even just their org's. PRD-005 (below) adds per-project
>   *roles*, but a role gates who may **call the API**; it is not a
>   filesystem boundary around what a script that IS running can touch. See
>   "Organisations, workspaces and roles" below.
> - **Registration is closed.** Accounts exist only because an administrator
>   minted an enrolment link over the CLI. There is no sign-up form and there
>   will not be one in this release.
> - **`admin` and `member` are not a security boundary between each other.**
>   The role governs who may manage accounts and tokens. Every member can
>   already read and write every project as the server user.
> - **Put it on a single-purpose VM**, with nothing else you care about on it,
>   and back the volume up.
>
> So: accounts are still for people you trust. What *is* enforced: the
> internet cannot reach anything but nine enumerated anonymous routes, none of
> which touch the geometry kernel — and what the worker is actually doing is
> measured, not assumed, at `GET /api/health` → `sandbox`.

---

## Quick start

```bash
cp .env.example .env          # set AGENTCAD_PUBLIC_ORIGIN
docker compose up -d
docker compose exec agentcad agentcad admin user add you --admin
```

The last command prints a single-use, 7-day enrolment URL. Open it in a
browser, choose a password, and you land signed in.

Check it is alive:

```bash
curl -s localhost:8630/api/health        # {"status":"ok","mode":"hosted"}
```

That trimmed body is deliberate: without a principal, health says only that the
process is up. Version, kernel state, chat availability and sandbox status are
reconnaissance for a stranger, so they need a session.

---

## Configuration

Configuration is environment-only. Nothing here is read from a file the
container ships.

| Variable | Default | What it does |
|---|---|---|
| `AGENTCAD_MODE` | `local` | `local` or `hosted`. **Never inferred** — an unrecognised value refuses to start, because defaulting would fail open. |
| `AGENTCAD_PUBLIC_ORIGIN` | — | Required in hosted mode. Scheme + host (+ port), no path, no trailing slash. Host and Origin checks compare against it; enrolment URLs are built from it. |
| `AGENTCAD_SECRET_KEY` | generated | ≥32 characters. **Leave it unset — that is the recommended path**: one is generated and persisted `0600` at `$AGENTCAD_STATE_DIR/secret.key`, where it is readable only by the server's own user. An explicit value is process environment, so it is visible to `docker inspect`, to anything that can read `/proc/<pid>/environ`, and to whatever shell history or CI log the value passed through. Set it only when you have a reason the file cannot serve (several instances sharing one key), and then treat it as a secret in the orchestrator rather than in `.env`. |
| `AGENTCAD_STATE_DIR` | `<config-dir>/state` | Identity state (`auth/`, `secret.key`). Created `0700` and repaired to `0700` at startup — it holds the session secret and the password hashes. Compose sets `/data/state`. **Keep it outside every kernel-writable root that isn't `publications/build`**: the hosted read allow-list is the read roots plus the *write* roots, so a state dir inside one is readable (and writable) by a part script, and whoever reads `secret.key` can forge any session. A hosted `agentcad serve` **refuses to start** if the state dir itself lies inside a write root, naming both paths and exiting 2. The one designed exception is `<state-dir>/publications/build` — PRD-007's share-link/customizer variant builds go through the shared kernel pool and need to write there, so that one subtree (and only that subtree) *is* a granted write root and *is* on the hosted read allow-list; `secret.key` and `auth/` are siblings of `publications/`, not beneath it, and stay out of both. Since the config dir is no longer a write root wholesale the `<config-dir>/state` default is otherwise safe, but set it explicitly on any hosted instance anyway. |
| `AGENTCAD_PROJECTS_DIR` | `~/AgentCAD/projects` | Where projects and their `.history` git repos live. Compose sets `/data/projects`. |
| `AGENTCAD_HOST` | `127.0.0.1` | Listen address. A non-loopback bind **requires** `AGENTCAD_MODE=hosted`. |
| `AGENTCAD_TRUSTED_PROXY` | `127.0.0.1` | Hosted mode only. The immediate peer(s) allowed to set `X-Forwarded-For`, passed to uvicorn's `forwarded_allow_ips` (IPs or CIDRs, comma-separated). Default matches the local proxy above. **`*` is refused** — it would let any client forge the address the login limiter keys on. |
| `AGENTCAD_PORT` | `8630` | Listen port. |
| `AGENTCAD_KERNEL_POOL_SIZE` | `max(1, min(3, cores//3))` | Kernel workers, ≈0.5 GB RSS each. Compose pins `2` rather than letting it float with the host: the PRD-007 share customizer reserves one worker for members, so it needs `>= 2` to run (a 1-worker pool answers `/variant`/`/download` with `503`). A viewer-only deployment can drop back to `1`. |
| `AGENTCAD_SHARE_MAX_INFLIGHT` | `2` | Hosted mode, PRD-007. The operator ceiling on **concurrent anonymous customizer builds**. The *effective* cap is this clamped to `AGENTCAD_KERNEL_POOL_SIZE - 1`, so at least one worker is always reserved for signed-in members — that reservation, not the `affinity=` routing (which is consistent-hash cache-warmth, **not** isolation), is what keeps a share flood from starving members. Over the cap a `/s/<token>/variant` returns `429 quota_exceeded`. On a **single-worker pool** the effective cap is `0`: the customizer cannot run without starving members, so `/variant` and `/download` return `503 service_unavailable` (viewer links stay up) — the customizer needs `AGENTCAD_KERNEL_POOL_SIZE >= 2`. |
| `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE` | unset | Hosted mode, PRD-007. **Off by default.** Set to `N` to require sign-in once an anonymous address crosses `N` customizer rebuilds/hour (`/variant` → `401`) — a login wall on a link under a distinct-param flood, without taking the viewer offline. The pre-006 backstop for stranger compute; the per-IP threshold is only honest behind the trusted proxy (see `AGENTCAD_TRUSTED_PROXY`). |
| `AGENTCAD_EXAMPLES` | `1` | `0` skips registering the bundled examples. Compose sets `0`: in a container they live in a read-only image layer, so edits vanish on redeploy. |
| `AGENTCAD_CONFIG` | `~/.agentcad/config.json` | User config path; also the root the packages/indexes/state defaults derive from. |
| `AGENTCAD_PACKAGES_DIR` / `AGENTCAD_INDEXES_DIR` | under the config dir | PRD-011 package cache and index checkouts. |
| `AGENTCAD_SYNC_MAX_PUSH_MB` | `512` | PRD-005. Caps a single `git push`'s body on the server (streamed, never buffered, in both directions). Raise it for a deployment that hosts genuinely large imported CAD. |
| `AGENTCAD_AUDIT_RETENTION_DAYS` | unset (keep everything) | PRD-005. Days of audit history to keep before opportunistic, hourly-rate-limited pruning starts discarding older rows. Unset, empty, `0`, or unparseable all mean "keep everything" — the failure mode of a mistyped knob is *more* history, never less. See "Audit" below. |
| `AGENTCAD_QUOTA_<KNOB>` | see [Confinement and quotas](#confinement-and-quotas) | Per-worker caps: `MEMORY_MB`, `ADDRESS_SPACE_MB`, `PIDS`, `PIDS_HEADROOM`, `CPU_PERCENT`, `SAMPLE_INTERVAL_S`, `DISK_MB`. Env beats the config file's `{"quotas": {…}}`, which beats the built-in defaults. `off` switches a knob off, and so does `0` — **except on `ADDRESS_SPACE_MB`, where `0` means *auto*** (3 × `MEMORY_MB`) and only the literal `off` disables it. A value that is not a number **refuses to start** (`error: …`, exit 2) and names both the knob and the layer it came from. |
| `AGENTCAD_CGROUP_DIR` | unset | A cgroup v2 subtree the operator **delegated** to this container, which is the only tier that OOM-kills rather than sampling. Unset means nothing is probed at all. A path is the compose recipe below; `auto` opts into the own-cgroup (systemd `Delegate=yes`) route, which refuses root and any subtree it does not own; `off` means "not even `auto`". Any failure falls back to rlimit + supervisor **with a health warning**, never silently. |
| `AGENTCAD_NO_SANDBOX` | unset | Opts out of **confinement** — the seatbelt on macOS, Landlock + seccomp on Linux — and reports `off` with the reason. It does **not** opt out of the quotas: the caps and the rlimits still apply. Do not set it on a hosted instance. |
| `AGENTCAD_URL` | `http://127.0.0.1:<port>` | **Client-side.** Which instance the MCP proxy talks to. |
| `AGENTCAD_TOKEN` | unset | **Client-side.** Bearer token the MCP proxy sends. |
| `AGENTCAD_AGENT_ID` | `mcp` | **Client-side.** Names this agent for turn locking and presence. |

The mode interlock, in full:

| | loopback bind | non-loopback bind |
|---|---|---|
| `local` | today's behaviour | **refused** |
| `hosted` | allowed | allowed |

You cannot expose an interface without turning authentication on. `agentcad
serve --host 0.0.0.0` in local mode exits 2 and says so.

---

## TLS

The compose file publishes **plain HTTP** on `8630` and expects a proxy in
front. This is deliberate: automatic ACME as the default would demand a real
public DNS name at first `up`, which breaks an air-gapped install, a staging
box and the CI smoke job — the three places this file most needs to work.

Whatever you put in front, set `AGENTCAD_PUBLIC_ORIGIN` to the **https** URL
users type. The session cookie becomes `Secure` automatically when that origin
is https (and stays non-`Secure` on plain http, because a `Secure` cookie over
http is never sent back and reads as "login silently does nothing").

**nginx**

```nginx
server {
    listen 443 ssl;
    server_name cad.example.com;
    ssl_certificate     /etc/letsencrypt/live/cad.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cad.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8630;
        proxy_http_version 1.1;
        proxy_set_header   Host cad.example.com;     # must equal the origin's host
        proxy_set_header   Upgrade $http_upgrade;    # /ws carries the event stream
        proxy_set_header   Connection "upgrade";
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;  # the real client
        proxy_read_timeout 600s;                     # a long build is a long request
    }
}
```

The `X-Forwarded-For` line is **not optional**. The login rate limit is keyed on
`(handle, address)`, and without this header every internet client arrives at the
app as `127.0.0.1` (the proxy), which collapses the key to per-handle — one
stranger can then lock any known handle out for everyone. `$proxy_add_x_forwarded_for`
appends the connecting client to any inbound value, so the app trusts the entry
*this* proxy added, not one a client sent (see the caveat below).

**Caddy**

```
cad.example.com {
    reverse_proxy 127.0.0.1:8630
}
```

Caddy's `reverse_proxy` sets `X-Forwarded-For` (and `X-Forwarded-Proto`,
`X-Forwarded-Host`) automatically, appending the client's address, so the plain
directive above is already correct — nothing to add.

**How the address is trusted, and the one way to break it.** The app runs
uvicorn with `proxy_headers` bounded to `AGENTCAD_TRUSTED_PROXY` (default
`127.0.0.1`, since the proxy above is local). uvicorn reads `X-Forwarded-For`
from the right and takes the first hop that is **not** trusted — which, with a
single local proxy, is the client as that proxy saw it. A client that prepends
its own `X-Forwarded-For` has it overwritten by the proxy's append, so it cannot
forge its address across one correctly configured hop. This holds only for
*exactly* the trusted hop count: if you set `AGENTCAD_TRUSTED_PROXY` to a range
that includes untrusted clients — and above all to `*` — any client can forge
its address and the rate-limit key becomes attacker-controlled. `*` is refused
at startup for that reason. If you run **two** proxies in series (e.g. a CDN in
front of nginx), trust both hops explicitly and make sure each appends rather
than overwrites `X-Forwarded-For`. Directly bound to the internet with no proxy
at all, the trusted default is safe: the immediate peer is then the public
client, so its `X-Forwarded-For` is ignored and the socket address stands.

**Bundled Caddy (one command).** Set `AGENTCAD_DOMAIN` in `.env` to a name that
resolves publicly to this host, then:

```bash
docker compose --profile proxy up -d
```

The `Host` header your proxy forwards must equal the host in
`AGENTCAD_PUBLIC_ORIGIN`, port ignored. If it does not, every request is a
`403 ForbiddenOrigin` — that is the DNS-rebinding defence doing its job, not a
bug.

---

## Sizing

- **≈0.5 GB RSS per kernel worker**, plus the server process.
- **Floor: 2 vCPU / 4 GB** with `AGENTCAD_KERNEL_POOL_SIZE=2` (the compose
  default). Two workers is the minimum the **share customizer** needs — one to
  build a visitor variant on, one kept free for members (a 1-worker pool refuses
  `/variant` with `503`). A viewer-only deployment can run `1`.
- **4 vCPU / 8 GB** for `AGENTCAD_KERNEL_POOL_SIZE=3`, which is what a handful
  of concurrent editors wants.
- Disk is projects plus their `.history` git repos. Mesh caches live under each
  project's `.cache/` and rebuild on demand, so they are not precious — which
  is what lets a **per-project disk budget** (`AGENTCAD_QUOTA_DISK_MB`,
  default 2048) cover `.cache/`, `exports/` and `imports/`: an over-budget
  project is refused *before* the worker writes (`diskbudget_error`, HTTP 507),
  and a janitor trims the oldest unreferenced meshes once the cache passes
  75 % of the budget.
- `GET /api/health` (with a principal) publishes `usage` — kernel CPU ms, wall
  ms and peak RSS rolled up per project and per client identity — beside
  `sandbox`, the measured confinement/quota object. The `get_usage` tool
  answers the same numbers with a `since` window. Both are in-memory and
  per-process: a restart starts from zero, and there is still no audit log.

**The per-worker caps**, which is what the host must be sized *above*:

| Knob | Default | What it bounds |
|---|---|---|
| `AGENTCAD_QUOTA_MEMORY_MB` | `2048` | RSS per worker — the cgroup's `memory.max` where one is delegated, otherwise the supervisor's kill threshold (and the job-object commit limit on Windows). |
| `AGENTCAD_QUOTA_ADDRESS_SPACE_MB` | `3 × memory_mb` (6144) | Linux `RLIMIT_AS` only. Deliberately loose: it exists to turn a runaway *reservation* into a recoverable `MemoryError` with a line number, not to be the cap. |
| `AGENTCAD_QUOTA_PIDS` | `128` | cgroup `pids.max` / job-object active processes — the fork-bomb stop. |
| `AGENTCAD_QUOTA_PIDS_HEADROOM` | `64` | `RLIMIT_NPROC` = the live uid **task** count, re-measured **at every spawn** (respawns included), plus this **× the pool size**. Per-uid and counting threads, so it is headroom, not a budget: what it bounds is at most `headroom × pool size` *extra* tasks across the whole pool. It is scaled and re-measured because the limit is per-uid but is checked against the calling process's own ceiling — one number computed once starved the third worker of a three-worker pool, which died inside `import build123d`. The hard per-worker process cap is `AGENTCAD_QUOTA_PIDS`. |
| `AGENTCAD_QUOTA_CPU_PERCENT` | `400` | cgroup `cpu.max` / job-object rate cap, as a share of one core (`400` = 4 cores). Throttles; it never kills. `off` for no CPU quota (macOS always). |
| `AGENTCAD_QUOTA_SAMPLE_INTERVAL_S` | `0.25` | How often the parent samples a worker's RSS. Not a limit — see the overshoot note below. |
| `AGENTCAD_QUOTA_DISK_MB` | `2048` | **Per project**, not per worker. |

Budget **`memory_mb × pool size` plus ~0.5 GB for the server, plus headroom**:
where the supervisor is the memory tier (macOS always; Linux without a
delegated cgroup) the kill lags one sample interval, and a script allocating at
the ~4 GB/s this was measured at overshoots by 380–620 MB before it dies. The
cap must therefore sit *below* the host's ceiling, not at it. With a delegated
cgroup the kernel kills at the charge and there is no overshoot.

There is still no per-**account** CPU or memory budget: any member can queue
expensive builds one after another, and the caps bound each one rather than
their sum. That is a stated residual (PRD-006 G3 vs. PRD-005's per-tenant
fair scheduling), and another reason accounts are for people you trust.

---

## Accounts

Everything runs against the state files directly — **no service, no kernel, no
port** — so it works over `docker compose exec` while the server is down.

```bash
# invite someone (prints a single-use, 7-day enrolment URL)
docker compose exec agentcad agentcad admin user add anya
docker compose exec agentcad agentcad admin user add nikita --admin

docker compose exec agentcad agentcad admin user list
docker compose exec agentcad agentcad admin user disable anya    # also ends sessions
docker compose exec agentcad agentcad admin enrol anya           # re-mint a link
```

Adding a handle that already exists is refused rather than treated as a
password reset. Use `admin enrol` for the recovery path; it kills any earlier
outstanding link for that handle, and **setting the new password signs that
handle out everywhere** — every existing session for it is revoked the moment
the link is spent. That is the point of the path: you run it when a password
was lost *or stolen*, and a reset that left the thief's cookie alive for the
remaining 30 days of its life would not be a recovery.

---

## Single sign-on and passkeys (PRD-005)

Local accounts (the enrolment-link flow above) always work — OIDC and
passkeys are additive, never a replacement for them, and **neither opens
registration**: both sign in an existing local handle, they do not create
one.

**Passkeys** need the optional `[cloud]` extra:

```bash
uv sync --extra cloud     # or: pip install "agentcad[cloud]"
```

Without it the passkey routes answer `501` — the same "extra not installed"
pattern the `[fem]` extra's routes use — and a plain install still serves
password sign-in and OIDC. `[cloud]` pulls in `webauthn>=3` and `cbor2>=5.6`;
nothing else in the product needs either. **The compose image ships with
`[cloud]` already installed** (`Dockerfile`'s `uv sync` carries `--extra
cloud`), so a `docker compose` deployment can serve passkeys with no extra
step; the manual `uv sync --extra cloud` above is for a bare `pip`/`uv`
install (a desktop build, or a hosted instance run outside the image).

Registering and signing in with a passkey asks the authenticator for **user
verification** — a PIN, biometric or security-key touch, not mere physical
presence — so an enrolled credential is not usable by whoever merely holds
the device. The exact requirement level (`preferred` vs. `required`) is set
once in `server/routes_auth.py`'s WebAuthn options and applies to every
instance; check that file for the shipped value, since it is the kind of
detail that is easy for a doc to drift from.

**OIDC** is *not* gated by the extra: `httpx` and `pyjwt[crypto]` are hard
dependencies, so single sign-on works on a plain install. Configure one
provider per instance at `<state-dir>/auth/oidc.json` (inside the container,
under the `agentcad-data` volume's `state/auth/`), hand-edited or written by
`docker compose exec`:

```json
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
```

`redirect_uri` left `null` derives from `AGENTCAD_PUBLIC_ORIGIN`; set it only
when the instance sits behind a path prefix a proxy rewrites. `email_handles`
is the **only** automatic path from a verified IdP identity to an account,
and it can only ever name a handle that **already exists** — closed
registration holds through SSO exactly as it holds through the enrolment
link. `allowed_email_domains` gates *linking* only, never an existing link,
so an instance that changes its email domain later cannot lock out people
who already linked. Editing the file takes effect on the next request (no
restart); a document that is present but malformed raises naming the field,
because the reader is an operator who just edited it.

---

## Organisations, workspaces and roles (PRD-005)

Every project on a hosted instance now lives under an **organisation** and a
**workspace**: `org -> workspace -> project`. A project's directory moves to
`<projects-dir>/orgs/<org>/<workspace>/<project>`; the untenanted flat root
(everything above this section) is what an instance with **no** orgs still
uses byte-for-byte — creating the first org is what turns tenancy on, and
nothing about local mode or a single-admin instance changes until you do.

**Bootstrap.** `agentcad admin org add|workspace add|member add` — the same
"no service, no kernel, no port" contract `agentcad admin user`/`admin
token` already rely on, so this works over `docker compose exec` before
anyone signs in and whether or not the server is up:

```bash
docker compose exec agentcad agentcad admin org add acme --label 'Acme Robotics' --admin nikita
docker compose exec agentcad agentcad admin org workspace add acme main --label Mechanical
docker compose exec agentcad agentcad admin org member add acme anya --role view
```

`org add --admin` is one atomic write — an org is never briefly adminless,
which matters because the *next* step (granting anyone else a role) needs an
admin able to call it. The `--admin`/`member add` handle must already be an
enrolled local handle (see "Accounts" above); org membership is bookkeeping
about a person, not a second account, and an agent token can never be one
(see below). `member add --role` sets that person's **org-default** role
(`view` if omitted); per-project overrides come later, from the running
instance (`grant_role`, below), not from this bootstrap.

**The ladder**, weakest first: `view` (reads) < `comment` (review threads,
proposals) < `edit` (geometry) < `admin` (who may do the above). It is a
total order — a rung includes every rung below it. **Org admin wins
outright**: a member whose org role is `admin` is `admin` on every project in
the org, and a per-project override cannot hold them down (they could always
rewrite the override themselves, so honouring a demotion would be a fiction
the members panel would render as a guarantee). Below that, a **per-project
override** beats the **org default** — the whole of "per-project overrides":
it may raise an org viewer to `edit` on one project, or hold an org editor to
`view` on another. An **agent token has no org default at all** (see "Scoped
agent tokens" below) — it reaches a project only through an explicit grant.

Once an org exists, membership and grants are the agent-tool surface —
callable over `POST /api/tools/<name>` with a signed-in admin's session (a
project-`admin` floor; an org admin holds that on every project in the org):

| Tool | Floor | What it does |
|---|---|---|
| `whoami` | any signed-in principal | extended with `org`, `workspace`, `orgs`, `roles` once the instance has at least one org — byte-for-byte the old `{principal, kind, role, mode}` on one that has none |
| `list_members {org, workspace?}` | `view` in the org | members and their org-default roles, the org's workspaces, and (admin only) its scoped tokens — never a secret |
| `grant_role {project, principal, role, org?, workspace?}` | `admin` on the project | a per-project override, in either direction |
| `revoke_role {project, principal, org?, workspace?}` | `admin` on the project | drops the override; the principal falls back to their org default |

`principal` is `user:<handle>`, `agent:<name>`, or a bare handle (read as a
person). `org`/`workspace` are inferred from the caller's active tenant when
there is exactly one to infer; an API client sending
`X-Agentcad-Workspace: <org>/<workspace>` (or a browser session that has
switched workspaces) names it explicitly, and a scoped token's own scope
always wins and cannot be redirected by an argument that disagrees with it.

**A grant takes effect on the very next request** — no restart, and the same
browser session that was just refused a write succeeds on its next try the
moment an admin grants it (this exec is effectively a second writer; the
guard's own store re-stats the file). A refusal is a structured
`permission_error` (`403`) naming the rung required and the rung actually
held — never whether the project *exists*: a caller who holds nothing on a
project sees the same answer whether it belongs to their own org with no
grant or to a different org entirely, which is what keeps a cross-tenant
probe from being an existence oracle. Reaching a workspace by header or
session that the caller has no role in at all is a **name-free `404`**, for
the same reason.

**Everyone on one instance still shares one server-user blast radius.** A
role governs who may *call the API* to build, edit or read a project — it is
not a filesystem boundary around what a script that IS running can touch. A
member authorized to build project A still runs as the server user with
`AGENTCAD_PROJECTS_DIR` wholly readable and writable underneath them,
including every OTHER org's projects — that has not changed, and per-project
roles do not change it. PRD-005 isolates *callers* from each other by role;
it does not isolate *scripts* from each other on disk. Only run this on a VM
shared by people (and their agents) you would already give a shell to.

---

## What a stranger can reach

The whole anonymous surface, enumerated. It is a literal in one file
(`agentcad/server/security.py`), and a test asserts the reachable set by
**equality** — a route pack added tomorrow is private with no action by its
author, and a forgotten entry fails the build rather than passing quietly.

| Method | Path | What it is |
|---|---|---|
| `GET` | `/` | `index.html` off disk — the app needs a login page |
| `GET` | `/js/**`, `/css/**`, `/vendor/**` | static files off disk |
| `GET` | `/api/health` | trimmed to `{status, mode}` |
| `POST` | `/api/auth/login` | rate-limited per `(handle, address)` **and** per address — where *address* is the real client only when the proxy forwards `X-Forwarded-For` (above); every failure is one indistinguishable answer |
| `GET`·`POST` | `/api/auth/enrol/{token}` | single-use, 7-day, admin-minted, unguessable |
| `GET` | `/api/public/packages` | the parts catalog, `scope: "public"` indexes only |
| `GET` | `/api/public/packages/{name}` | ditto |
| `GET` | `/api/public/packages/{name}/versions/{version}` | ditto — the pre-generated metadata |
| `GET` | `/api/public/packages/{name}/versions/{version}/preview` | a shipped `.png`, resolved inside the version directory |
| `GET` | `/s/{token}`, `/embed/{token}` | a shared model's HTML shell off disk (PRD-007). `/embed/` sends `Content-Security-Policy: frame-ancestors *` so any site may embed the public customizer; every other hosted response sends `frame-ancestors 'none'`, so the authenticated app is never frameable |
| `GET` | `/s/{token}/model`·`/mesh/{key}`·`/params`·`/script` | attribution + metrics, the cached `.acm` bytes (404-if-absent, **never builds**), the slider spec, and the pinned script iff enabled — all file reads of sidecars the publish pin wrote, **zero kernel** |
| `GET` | `/s/{token}/variant`·`/download/{fmt}` | **the two kernel-reaching anonymous routes** — the customizer rebuild and its variant export. Param-validated to the authoring path's parity (unknown/out-of-type/out-of-enum refused before any build), per-link **and** per-IP token buckets, a global in-flight semaphore (`AGENTCAD_SHARE_MAX_INFLIGHT`), and the content-addressed variant cache in front. A disabled format or a `customizer:false` link `404`s **before** the builder |

Fifteen route templates. Thirteen are file reads that make **zero kernel calls**
(proved by a test that exercises the surface with the kernel instrumented, with a
positive control so a broken counter cannot make it pass). The **two** PRD-007
customizer routes are the one deliberate exception — the first anonymous requests
that reach the kernel, bounded exactly as the table says.

**What PRD-006 now bounds, and what is still open.** Since PRD-006 the worker a
variant build runs in is capped (memory by the supervisor and, where delegated,
a cgroup; process count; CPU) and confined (no egress, writes only under its
roots — the variant build store is one of them), so a params-driven mesh that
balloons is killed with `reason: memory_cap` instead of OOM-ing the host; see
"Confinement and quotas" below. What is still open is a **disk budget for the
variant cache itself** (per-project budgets do not cover `<state-dir>/publications/build`)
— the operator's backstop for a link under a distinct-param flood remains
`AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE` (off by default). The per-IP rate limit and
that gate are only honest behind the trusted proxy (`AGENTCAD_TRUSTED_PROXY`) —
the same caveat as the login limiter, for the same reason.

**The share token rides in the URL path**, which keeps it out of a `Referer`
(the pages send `Referrer-Policy: no-referrer`), but a path is **not**
log-invisible: a reverse proxy (nginx/Caddy) logs the request path by default,
so the token lands in the proxy access log — treat those logs as containing
secrets. uvicorn's own access log is quieted (`log_level=warning`), so it is the
proxy tier, not the app, that records the token. What makes a path token
acceptable is not log-secrecy but **immediate revocation and expiry**: rotate a
link (`agentcad share revoke`) if a log is exposed, and set a TTL on links that
should not live forever.

**Popular is cheaper, but not free.** The variant cache keys on the
range-**clamped** params, so out-of-range floods (and any repeat) coalesce onto
one build and one cached artifact. A genuinely-distinct **in-range** flood still
builds each variant, and the on-disk variant cache is **unbounded** until
PRD-006 adds a disk budget — the login gate above is the operator's backstop
until then.

**A private index stays private, and *you* decide which.** An index is
anonymously readable only when **both** your configuration and the index's own
`index.json` say `scope: "public"`. Your configuration is the one that
matters for exposure: write `"scope": "private"` on the entry in
`~/.agentcad/config.json` (inside the container: `/data/home/config.json`) and
that index is never consulted by the anonymous routes, whatever its document
claims. That direction is load-bearing for a **git** index, where the
`index.json` is written by whoever owns the repository and not by you — before
this was fixed (PRD-005a security review, finding M2) that third party could
publish `"scope": "public"` and have your instance serve their content to the
internet over your `private`. The reverse still holds too: a document that says
`private` hides itself even from an operator who configured it public.

The default on both sides is `public`, so an entry with no `scope` at all is
anonymously readable — set it explicitly on anything that is not meant to be.

A package carried only by a private index answers exactly the same `404` as a
package that does not exist, so the surface is not an oracle for what you have.
The *authenticated* `GET /api/packages/search` does walk every index, which is
why it is not public and why this is a separate route pack rather than a flag
on that one.

```json
{"indexes": [
  {"name": "acme-internal", "kind": "git",
   "url": "https://git.example.com/acme/parts.git", "scope": "private"}
]}
```

Everything else — every project, every part, every geometry route, the
WebSocket, and the package management routes — requires a session cookie or a
bearer token. Anonymous requests to them get `401`, including to paths that do
not exist, because the guard answers before routing and a `404` would be a free
map of the instance.

Responses on the public routes carry `Cache-Control: public, max-age=300` —
including the `404`s, which are most of a flood, since a flood asks for names
that do not exist — so a CDN or reverse proxy in front absorbs it. There is no
per-IP limit on them
in this release; the payload is a static file read.

---

## Agents, tokens and MCP

```bash
docker compose exec agentcad agentcad admin token add ci
docker compose exec agentcad agentcad admin token add nightly --ttl-days 30
docker compose exec agentcad agentcad admin token list
docker compose exec agentcad agentcad admin token revoke <id>
```

The secret is printed **once** — only its SHA-256 digest is stored, so a lost
token is revoked and replaced, never recovered.

Point an MCP client at the instance:

```bash
claude mcp add agentcad \
  --env AGENTCAD_URL=https://cad.example.com \
  --env AGENTCAD_TOKEN=acad_xxxxxxxx_… \
  --env AGENTCAD_AGENT_ID=claude \
  -- uv --directory /path/to/cad_claude run agentcad mcp
```

Call `whoami` first; it answers `{principal, kind, role, mode}` and is the
quickest confirmation that the token reached the right instance. A **remote**
`AGENTCAD_URL` is never auto-started: if it is unreachable the proxy says so
rather than quietly starting a local server and answering from the wrong
machine.

Note that admin *routes* require a signed-in person, so an `admin`-role token
cannot mint or revoke another token — instance-wide or scoped (below), same
rule either way. PRD-005a held this while there was no audit log; PRD-005
keeps it for a sharper reason: `create_agent_token`/`revoke_agent_token`
require **org-level** admin (`authz.role_of` with no project named), and that
can only ever come from org membership or an org-default role — both of
which are bookkeeping about a *person* (`tenancy.handle_of`: a token is
never an org member). A bearer token therefore structurally cannot mint or
revoke a token, not by policy this project chooses to enforce, but because
the RBAC model gives an agent principal no org membership to hold that role
in.

**This does not mean a token can never grant or revoke a role.** `grant_role`
and `revoke_role` check **project-level** admin, and the RBAC ladder's
per-project override (see "Organisations, workspaces and roles" below)
applies to an agent principal exactly as it does to a person: a token minted
with `role: "admin"` on a project holds that override there, and can call
`grant_role`/`revoke_role` on that project like any other admin — it just
cannot touch org membership or mint another credential. The floor that is
genuinely closed to every token is **token minting**, not the RBAC surface as
a whole.

### Scoped agent tokens (PRD-005)

The tokens above are **instance-wide** — a hangover from PRD-005a, and still
the right shape for a CI credential that has no notion of an org. Once an
org exists, mint a **scoped** one instead: reachable to a project (or
several named projects), at a role, and never wider than that — over the
tool surface, not the CLI, because minting a credential is itself worth an
audit row (see "Audit" below), and the tool surface is where every mutating
call gets one.

```bash
curl -sf -b <admin's session cookie jar> \
  -H "X-Agentcad-Workspace: acme/main" \
  -H 'Content-Type: application/json' \
  -d '{"name":"ci","org":"acme","projects":["widget"],"role":"edit"}' \
  https://cad.example.com/api/tools/create_agent_token
```

The response's `token` field is shown **once** — the same "SHA-256 digest
only; a lost token is revoked and replaced, never recovered" contract as an
instance-wide token. The scope is *also* written as per-project grants for
the composed principal `agent:<name>`, so
`revoke_agent_token {token_id}` both kills the credential and drops the
grants it was minted with (kept if another live token happens to share its
name). This is a *promotion*, not a new hole: PRD-005a's Decision 14 kept
minting off the token-authenticated surface specifically "while there is no
audit log" — the audit log now exists, which is the condition the PRD
itself named for revisiting it.

---

## Audit (PRD-005)

One SQLite database **per org**, at `<state-dir>/audit/<org>.db`, plus one
instance-wide database for events that belong to no org — sign-ins,
enrolments, and account/instance-token administration — at
`<state-dir>/audit/_instance.db` (`_instance` cannot collide with a real org
name; the grammar refuses a leading underscore). Rows are
`{ts, principal, action, project, args_digest, outcome}`. `args_digest` is a
sha256 of the call's arguments with anything secret-shaped redacted first —
**never the arguments themselves**: an administrator reading the log may not
be a member of the project a row is about, and a script body or a parameter
sweep sitting in it would make the log a second copy of the customer's data.
The digest is for correlation — the same call, twice, is the same digest.

```bash
docker compose exec agentcad agentcad admin audit query acme
docker compose exec agentcad agentcad admin audit query acme \
  --principal user:anya --since 24h --json
docker compose exec agentcad agentcad admin audit query _instance --action login
```

Filters compose with AND; `--since`/`--until` take epoch seconds, an
ISO-8601 timestamp, or a window like `7d`/`24h`/`30m`; `--principal` matches
a person **and** every device they used (`user:anya` also matches
`user:anya/browser:7f3a1b2c`). This reads the files directly — no service,
no kernel, no port — so it works over `docker compose exec` whether the
server is up or down, the same contract as `admin user`/`admin token`.

**Retention** is off by default — an audit log that forgets by default is
not one. Set `AGENTCAD_AUDIT_RETENTION_DAYS` to a positive number of days to
turn pruning on (see the Configuration table above); pruning is
opportunistic, running on append and rate-limited to once per org per hour,
never a cron job of its own.

**Backup: `VACUUM INTO`, never a raw copy.** The database is WAL-mode, so a
plain `cp`/`tar` taken while the server is writing can capture the main file
without the `-wal` sidecar holding its most recent commits — measured in the
PRD-005 spike: a 50-row database copied that way answered `no such table:
audit`. Use the dedicated command instead, which is safe against a live
server:

```bash
docker compose exec agentcad agentcad admin audit backup acme /backup/acme-audit.db
```

This is **in addition to** the whole-volume tar in "Backup" below, not a
replacement for it. The audit databases live under `<state-dir>/audit/`,
outside `auth/`, specifically so 005a's "a `tar` of the state dir is a
correct backup" statement stays true **for identity** (accounts, sessions,
tokens) — it was never true for a WAL database, which is why the audit
store gets its own backup command rather than inheriting the tar's.

**What writes a row.** `routes_auth.py` taps every sign-in, sign-out,
enrolment, and account/instance-token administration event (instance-wide).
Everything else — every mutating tool call, from any of the three surfaces
that share one `ToolRegistry` (HTTP, chat, MCP) — goes through the general
tap, `core/audit.py`'s `tap_registry`, **installed on the serve path**
(`tenancy_wiring._install_registry`, outermost — a refused call is recorded
with `outcome: "permission_error"`). That single tap covers `tools_cloud.py`'s
own admin actions too (`create_agent_token`, `revoke_agent_token`,
`grant_role`, `revoke_role`); there is no second, pack-local write for them
to duplicate. An ordinary `set_part_params` under a tenant produces a row; a
request with no tenant produces none, so local mode never touches the audit
store. The three-principal-kind distinction PRD-005 promises (a human edit,
a chat-agent edit, an MCP-agent edit, one project) is asserted end-to-end in
`tests/test_prd005_acceptance.py`; the deploy smoke's audit-query step
checks only that two *human* principals (`user:smoke`, `user:anya`) are
distinguishable in a real deployed instance's log — the full three-way
distinction is graded by the acceptance test, not by that workflow.

**Reading it over HTTP.** `GET /api/auth/audit?org=<org>` answers the same
query the CLI does — `principal`, `project`, `action`, `since`, `until`,
`limit`, `offset` as query params, newest first, paginated (`next_offset`
when a page is full). `org` defaults to `_instance`. Open to the **instance**
administrator for any org, and to an **org** admin for their own org only —
never a plain member, and never a token (the same "a token cannot read what
its own theft looked like" rule as minting).

---

## Sync: git against a hosted instance (PRD-005)

Every project's history is reachable as an ordinary git remote at
`<instance>/git/<org>/<workspace>/<project>.git`, authenticated the same way
as everything else (a bearer token — or, because git speaks Basic, never
Bearer, a credential helper that turns git's username/password prompt into
one). `view` clones and fetches; `edit` pushes. `agentcad login` stores a
token and installs that credential helper, so a plain `git clone`/`push`/
`pull` against that URL works without ever putting the token in a URL, a
config file, or `http.extraHeader` — all three leak, into `.git/config`,
`ps`, and a proxy access log respectively.

```bash
agentcad login https://cad.example.com --token acad_xxxxxxxx_…
agentcad clone https://cad.example.com/git/acme/main/widget.git
#  lands at <projects-dir>/widget, a real local project — scripts, manifest
#  and history all present, builds fully offline with the local kernel.
cd widget
agentcad push          # every local branch and tag, explicit refspecs
agentcad pull          # fetch + fast-forward, or the PRD-001 merge machinery
agentcad status --fetch
```

`push` never forces and never deletes. A push the server cannot fast-forward
is refused **inside git's own ref transaction** (a pre-receive hook, so the
refusal is atomic for the whole push — one bad ref rejects every ref in that
push, on purpose), with a message `git` prints verbatim:

```
remote: agentcad: refs/heads/main diverged - pull and merge, never force
remote: agentcad: refusing to delete refs/heads/old - deletes are refused on the hosted copy
remote: agentcad: refs/tags/v1 already exists - tags are immutable
```

Branch non-fast-forwards, **every** ref delete (branch or tag), and tag
*rewrites* are all refused this way — PRD-015 release tags have to stay
immutable, and git's own `denyNonFastForwards` alone only covers
`refs/heads/*`. The fix for a divergence is always `agentcad pull`: it
fetches, fast-forwards what can fast-forward, and merges what cannot through
the same kernel-validated merge the UI uses, surfacing conflicts rather than
ever resetting or overwriting local work. A **pushed** state that does not
build is not a rejected push — the server checks it out and rebuilds on the
next open, exactly like any other change, and a broken pushed state shows up
there as an ordinary build error, never as a refusal at push time.

`AGENTCAD_SYNC_MAX_PUSH_MB` (default `512`, see the Configuration table
above) caps a single push's body on the server. Push and clone bodies are
**streamed** the whole way, never buffered in memory, in both directions.

`agentcad mcp --remote <url> --token …` points the MCP proxy at a hosted
instance instead of auto-starting a local server — the same "unreachable
means say so, never silently answer from the wrong machine" rule as
`AGENTCAD_URL` above.

---

## Backup

**No downtime and no quiescing.** Every write in the identity store and the
project store is staged to a temporary file and `os.replace`d into place, so an
archive taken while the server runs is a set of complete files. Git history
lives inside each project directory, so it travels with them.

```bash
docker run --rm \
  -v agentcad_agentcad-data:/data:ro \
  -v "$PWD:/backup" \
  busybox tar czf /backup/agentcad-$(date +%F).tar.gz -C /data .
```

That archive is everything: projects, their `.history` repos, accounts,
sessions, tokens, the session secret, the package cache and index checkouts.

Treat it as a secret. It contains password digests, live session digests and
the session key — enough to impersonate, given the digests are what
authentication compares against.

## Restore

```bash
docker compose down
docker volume rm agentcad_agentcad-data
docker volume create agentcad_agentcad-data
docker run --rm -v agentcad_agentcad-data:/data -v "$PWD:/backup" \
  busybox tar xzf /backup/agentcad-2026-08-17.tar.gz -C /data
docker compose up -d
```

If you restore onto a *different* host and left `AGENTCAD_SECRET_KEY` unset,
the key travels inside the archive, so sessions survive. Accounts and tokens
always survive — they are files.

## Upgrade

```bash
git pull
docker compose build
docker compose up -d
```

The volume is untouched. There is no schema migration step: the state files are
plain JSON documents, read defensively, and a field added by a later version is
ignored by an earlier one. Roll back by checking out the previous revision and
rebuilding.

---

## Desktop builds (release pipeline)

`.github/workflows/release.yml` builds the PyInstaller onedir bundle
(`scripts/build_binary.sh` — the same command a developer runs locally) for
macOS and Windows on every `v*` tag (or `workflow_dispatch`), proves the
compose image still builds on Linux, and attaches every artifact to a GitHub
release.

**Signing and notarization are secrets-gated, and unsigned by default.** With
no secrets provisioned the pipeline still builds and still uploads an
**unsigned** bundle, logged with an explicit
`::notice::unsigned build — signing secrets not provisioned` rather than
silently skipping anything. Provision these repository secrets to turn
signing on:

| Secret | What it is |
|---|---|
| `MACOS_CERT_P12` | a Developer ID Application certificate + private key, exported as `.p12`, base64-encoded |
| `MACOS_CERT_PASSWORD` | the `.p12`'s export password |
| `APPLE_ID` | the Apple ID enrolled in the Developer Program that owns the certificate |
| `APPLE_TEAM_ID` | the ten-character team id |
| `APPLE_APP_PASSWORD` | an **app-specific** password for that Apple ID (never the account password) — `notarytool` needs it |
| `WINDOWS_CERT_PFX` | a code-signing certificate + private key, `.pfx`, base64-encoded |
| `WINDOWS_CERT_PASSWORD` | the `.pfx`'s password |

With the macOS five present, the job imports the certificate into a
throwaway keychain, codesigns every binary in the bundle (hardened runtime,
`packaging/entitlements.plist`), zips it, submits it to `notarytool --wait`,
and attempts to staple the result. A staple failure on a bare onedir
executable (rather than a `.app`/`.pkg`/`.dmg`, the containers Apple
guarantees stapling for) is reported as a warning, not fatal — the online
notarization record itself, already waited for, is what Gatekeeper actually
checks.

With the Windows two present, the job signs the executable with
`signtool sign /fd SHA256` and a timestamp server, then verifies the
signature before zipping.

An optional, **non-secret** repository variable `PUBLISH_GHCR=true` also
pushes the compose image to `ghcr.io/<owner>/agentcad:<tag>`, using the
workflow's automatic token — off by default, since publishing an image under
the repository's namespace is a policy choice this pipeline should not make
on its own the first time someone pushes a tag.

**AC8's positive claim — a build that actually passes notarization/signing —
is therefore evidence this pipeline produces once the founder provisions the
certificates above, not something any CI run demonstrates by merely
existing.** No agent can conjure a signing identity, and this repository's CI
has never held one.

---

## Confinement and quotas

Two separate things, reported separately, and worth keeping apart in your head:
**confinement** is what a script may *reach* (files, network, other
processes), **quotas** are how much of the machine it may *take*. Opting out of
one is not opting out of the other.

### What confines a worker

| | mechanism | reads | writes | network | other processes |
|---|---|---|---|---|---|
| **Linux** | Landlock + seccomp, applied by the worker to itself before `import build123d` | `local` posture: anywhere. `hosted` posture (this deployment): an allow-list — `/usr`, `/lib*`, `/bin`, `/sbin`, `/etc`, `/opt`, `/proc`, `/dev`, `/sys`, the interpreter, the app tree, and everything it may write to (the roots in the next column — a write root is readable by construction, which is why `<state-dir>/publications/build` is the one subtree of `AGENTCAD_STATE_DIR` on this list). **Not** the rest of `AGENTCAD_STATE_DIR` (`secret.key`, `auth/`), and **nothing under the server user's home** — the config dir stopped being a write root wholesale, so there is no broader exception to state. The worker's own `HOME` env var points at its private temp dir, so `~` inside a script is not the server user's home at all | the granted roots only — the projects dir, each registered example, the server's one `agentcad-work-*` root, the worker's own private temp dir, and `<state-dir>/publications/build` (PRD-007's shared-pool variant builds — `core/share_build.py`). Nothing else, root included (Landlock beats DAC) | denied: every socket family but `AF_UNIX` | no `ptrace`, no `process_vm_readv/writev`, no `pidfd_open`/`pidfd_send_signal`/`pidfd_getfd`, no `process_madvise`, no `io_uring`, and no signal to pid ≤ 0 or to the server |
| **macOS** | `sandbox-exec` seatbelt profile (the v3 profile, unchanged) | anywhere (`local` posture only — the narrowed profile is Linux) | the same roots | denied | signals to self only |
| **Windows** | an AppContainer (a package SID with no capabilities), the roots granted to it with `icacls`, the worker spawned through `CreateProcessW` with `SECURITY_CAPABILITIES` | anywhere the SID has an ACE: the interpreter, the venv and the app tree (`RX`), plus everything it may write. `local` posture only — the narrowed hosted allow-list is Landlock | the same roots (`M`), granted per plan; everything else answers `[Errno 13]` | denied: no `INTERNET_CLIENT` capability, so a connect fails with `[WinError 10013]` | the job object caps how many it may start; a child inherits the container |

Both postures are explicit named profiles; the hosted one is what
`AGENTCAD_MODE=hosted` selects. Forks and `exec`s inherit the confinement, so a
script cannot escape by delegating.

**Linux requirements.** Landlock must be **ABI ≥ 3** (kernel 6.2; ABI 1 is
5.13 but lacks `TRUNCATE`, without which every truncating `open(path, "w")`
inside a write root would be a false denial — so below ABI 3 the worker
applies no ruleset and reports `off` rather than shipping a profile that
lies). Landlock is a boot-time LSM: it must also be in the kernel's `lsm=`
list. So the requirement is a **kernel**, not a distribution release: 6.2 or
newer — Ubuntu 24.04, or 22.04 with the HWE kernel (22.04 GA is 5.15, which is
Landlock ABI 1 and therefore below the floor). Check **both** halves, because
either alone can be the reason confinement is `off`:
`cat /sys/kernel/security/lsm` for the list, and the ABI the probe prints
(`python -c "from agentcad.kernel import _confine; print(_confine.landlock_abi())"`,
which is the CI step's third line) for the version.

No capability, no `bwrap` binary and no `--privileged` are needed — the
ruleset is applied through `ctypes` inside the worker, verified in this image
as uid 10001 under Docker's default seccomp profile, at 0.3 ms.

**Windows requirements, and what the AppContainer leaves behind.** Windows 8 or
later (`userenv!CreateAppContainerProfile`) and `icacls` on `PATH`; below that,
health says `unsupported` rather than implying a switch. Two consequences an
operator should know, because neither is undone by stopping the server:

- **The ACEs are permanent and inheritable.** Each plan grants the package SID
  `(OI)(CI)M` on the projects dir, an accepted `--work-dir` and
  `<state-dir>/publications/build`, and `(OI)(CI)RX` on the interpreter, the
  venv and the whole application tree (`.git/` and `catalog/` included — an
  AppContainer that cannot read the app cannot run the worker). They stay on
  those directories until they are removed, and new files under them inherit
  them.
- **The profile name is salted, and that is load-bearing.** A package SID is a
  *hash of the profile name* (`DeriveAppContainerSidFromAppContainerName`), so
  a name derived only from the install path would be a SID any other local
  account could derive — create a profile for, run a process as, and reach
  everything the ACEs above allow. The name therefore mixes a random
  per-installation salt kept at `<state-dir>/appcontainer.salt` (16 bytes,
  created on first use beside `secret.key`); it never leaves the server
  process, since the worker is told the SID and never the name. **Do not copy a
  state directory between machines** if you also copy the projects tree and
  care about that boundary, and if the file cannot be written the server still
  confines — health carries a warning saying the SID is derivable.

**Removing it again.** The profile is per installation and is deliberately
never deleted by the worker path, so uninstalling is two steps — the profile,
then the ACEs, which outlive it (an ACE names a SID, and a SID whose profile is
gone is still that SID):

```powershell
# 1. the profile. There is no reliable in-box cmdlet, so this is the API call:
uv run python -c "from agentcad.kernel.sandbox_windows import AppContainerProfile, profile_name; name = profile_name(); AppContainerProfile.delete(name); print('deleted', name)"

# 2. the ACEs, one per granted root (the SID is in `/api/health` ->
#    sandbox.confinement.detail.sid, or print it before deleting the profile):
icacls "C:\path\to\projects" /remove "*S-1-15-2-…"
icacls "C:\path\to\agentcad"  /remove "*S-1-15-2-…"
```

`agentcad.kernel.sandbox_windows.acl_revoke(path, sid)` is the same `icacls
/remove` if you would rather script it in Python. Deleting
`<state-dir>/appcontainer.salt` afterwards is what makes the *next* start use a
fresh, unrelated SID.

### What caps a worker

Quotas are **tiers**, and health names the tier actually in force rather than
promising a mechanism:

| tier | where | memory | CPU | processes | on breach |
|---|---|---|---|---|---|
| `cgroup` | Linux, only with a delegated subtree (below) | `memory.max` + `memory.swap.max=0` — the kernel OOM-kills | `cpu.max` throttles | `pids.max` | `kernel_crash`, `details.reason: "memory_cap"`, `tier: "cgroup"` |
| `rlimit` | Linux (`RLIMIT_AS`, `RLIMIT_NPROC`), macOS (`RLIMIT_NPROC` only) | the allocation *fails*: an ordinary `script_error` with a line number, and the warm worker survives | — (`RLIMIT_CPU` is lifetime-cumulative; the wall-clock timeout is the backstop) | `RLIMIT_NPROC` | `script_error` with `details.denied: "memory"` / `"process_count"` |
| `supervisor` | everywhere | the parent samples the child's RSS every `sample_interval_s` and kills on breach — the only memory tier macOS has | wall clock (the existing timeouts) | — | `kernel_crash`, `details.reason: "memory_cap"`, `observed_rss_mb`, `tier: "supervisor"` |
| `job_object` | Windows | commit limit → `MemoryError` in the script | CPU rate hard cap | active-process limit | `script_error` with `details.denied: "memory"` |

**On Windows the supervisor samples the job, not the process it spawned.** A
venv `python.exe` (uv-managed ones included) is a *launcher*: it starts the
real interpreter as a **child** and stays behind as a ~4 MB stub. The child
inherits the job object, so the commit limit and the CPU cap apply to the
interpreter — but the handle the server holds is the stub's, and measuring it
reported ~4 MB for a worker with build123d imported. So the sampler walks the
job's process list and reports the **largest** working set in it (the max, not
the sum: the two processes share their mapped pages). With no job object — the
`supervisor`-only line in `mechanism` — there is nothing to walk, and the
sample falls back to that stub handle and under-reports; the memory tier that
bites on Windows is the job object's commit limit either way.

`RLIMIT_AS`/`RLIMIT_DATA`/`RLIMIT_RSS` are `EINVAL` on Darwin (measured), which
is why macOS has no in-process memory cap and the supervisor exists.

**The supervisor kills late, by design.** It samples; it does not intercept. At
the default 0.25 s and the ~4 GB/s a `bytearray` allocation reaches, the worker
overshoots its cap by 380–620 MB before the kill lands, and an allocate-and-free
that fits between two samples is invisible to it entirely. Size the host above
the cap, and use the cgroup tier where you need a hard one.

### The delegated cgroup (optional)

The image mounts `/sys/fs/cgroup` read-only and root-owned, and the only other
route to a writable subtree is `--cap-add SYS_ADMIN` — a near-root capability
this project refuses to ask for. So the cgroup tier is opt-in **by
delegation**. On the host, once:

```bash
sudo mkdir -p /sys/fs/cgroup/agentcad
echo "+memory +pids +cpu" | sudo tee /sys/fs/cgroup/cgroup.subtree_control
echo "+memory +pids +cpu" | sudo tee /sys/fs/cgroup/agentcad/cgroup.subtree_control
sudo chown -R 10001:10001 /sys/fs/cgroup/agentcad
```

then uncomment the three lines `compose.yaml` already carries:

```yaml
    cgroup_parent: /agentcad
    volumes:
      - /sys/fs/cgroup/agentcad:/cg:rw
    environment:
      AGENTCAD_CGROUP_DIR: /cg
```

Verified end to end this way: `pids.max=16` stopped a fork loop after 15
children, and `memory.max=200M` with swap at `0` SIGKILLed a 400 MB allocator
with `memory.events oom_kill 1`. Swap at its default `max` does **not** work —
the allocation swaps instead of dying — which is why the code writes
`memory.swap.max=0` beside every `memory.max`.

`AGENTCAD_CGROUP_DIR=auto` instead probes the process's *own* cgroup, the shape
a systemd unit with `Delegate=yes` produces. It is a separate opt-in because it
is the route that moves the server's own pids, and it refuses rather than
guesses: not as root (`os.access` answers `W_OK` for uid 0 almost everywhere,
so a root server would "discover" a subtree on any machine — that is activation
by capability), not on a subtree owned by someone else, not on the root cgroup.
This route is **unverified on a real systemd host**; the delegated-directory
one above is what was measured.

### Reading the live state

```bash
curl -s -b jar localhost:8630/api/health | jq .sandbox
```

```jsonc
{"status": "active",                       // the CONFINEMENT's status
 "mechanism": "landlock+seccomp",
 "posture": "hosted",
 "confinement": {"status": "active", "mechanism": "landlock+seccomp",
                 "detail": {"landlock_abi": 6, "posture": "hosted",
                            "seccomp": "seccomp(2)",
                            "rlimits": ["RLIMIT_AS", "RLIMIT_NPROC"]}},
 "quotas": {"status": "active", "mechanism": "rlimit+supervisor",
            "limits": {"memory_mb": 2048, "address_space_mb": 6144,
                       "pids": 128, "pids_headroom": 64,
                       "cpu_percent": 400, "disk_mb": 2048}},
 "warnings": []}
```

`status` is `active` **only because the worker itself said so** — the client
asks it on `ping` what it managed to apply, and a preamble that failed a stage
reports `off` with the failure in `warnings`, never `active` from intent. A
hosted instance whose confinement is not active also prints one `WARNING:` line
to stderr at startup; it is not fatal, because a container that refuses to boot
teaches an operator less than one that says what it is doing. `mechanism` is
`null` beside `off` on purpose: naming a mechanism next to `off` would claim
something is in force. The same rule governs the quota **tiers**: a worker that
reports no applied rlimits drops `rlimit` from `quotas.mechanism`, with the
reason in `warnings`. A `warnings` entry that begins "the worker lost a
Landlock grant" is a *narrower* confinement, not a failed one — one root (a
directory that did not exist when the worker spawned) was not granted, and
writes there will be denied while everything else stays in force.

### What a breach looks like to a user or an agent

Nothing new: it is the error contract that already existed.

- A denial raised **inside the script** stays a `script_error` with a traceback
  and a line number, plus `details.denied` ∈ `network` | `filesystem` |
  `process_count` | `memory` and an Error Doctor `details.hint`. The previous
  good geometry stays, the worker stays warm.
- A **kill** is `kernel_crash` with `details.usage` always, and
  `details.reason` when it is attributable. **The shipped tiers emit exactly
  one reason, `memory_cap`** — from the supervisor's kill or from a delegated
  cgroup's OOM counter — with `details.tier` naming which. `pids_cap` and
  `cpu_cap` are reserved vocabulary, documented so an agent's handler can be
  written once: a **pids** breach does not kill the worker at all (the
  `fork()` gets `EAGAIN` and the script sees the `script_error` above), and
  `cpu_cap` needs a `SIGXCPU` from an `RLIMIT_CPU` that AgentCAD never sets —
  the CPU tiers throttle (`cpu.max`, the job-object rate cap) and the
  wall-clock timeout is the backstop.
- A **timeout** is `timeout`, exactly as before, now carrying `details.usage`.
- An over-budget project is `diskbudget_error` (HTTP 507) raised *before* the
  worker writes.

### Still deferred, named so it is not a surprise

PRD-005 shipped organisations/workspaces/per-project roles, OIDC + passkey
sign-in (local accounts still always work), scoped agent tokens, a queryable
per-org audit log with its general per-tool-call tap wired into the serve
path ("Audit" above), git sync (clone/push/pull against a hosted instance
over authenticated smart-HTTP), and per-tenant fair kernel scheduling. What
is still genuinely deferred:

- **SAML/SCIM.** Only generic OIDC (code+PKCE) is built — no enterprise IdP
  provisioning protocol, no SCIM user/group sync.
- **Billing.** Nothing here meters or charges for usage; `get_usage`/
  `/api/health`'s `usage` object is operational visibility, not invoicing.
- **A multi-process/multi-instance event bus.** The WS fan-out, turn locks,
  presence and undo stacks are one process's in-memory state — a hosted
  instance is one process, one store, one kernel pool, by design. Running
  two behind a load balancer would not share any of that.
- **No per-account compute budget.** The caps in "Sizing" above are per
  worker per request, never per account or per org: any member (or their
  agent) can still queue expensive builds one after another.

Windows confinement landed as PRD-006b (an AppContainer, above), but hosted
mode is still the Linux image — a Windows *host* posture is not built, and
neither is a narrowed *macOS* read posture (the seatbelt equivalent of
`hosted`).
