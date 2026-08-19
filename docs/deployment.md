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
>   just theirs. Per-project ACLs are PRD-005 and are not built.
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
cannot mint another token. A credential that could mint credentials is a
privilege-escalation shape worth avoiding while there is no audit log.

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
| **Windows** | **none.** Reported `unsupported`, honestly, in health and here — AppContainer is carved out as [PRD-006b](prd/pending/PRD-006b-windows-appcontainer.md) | — | — | — | — |

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

No audit log, no per-project ACLs, no organisations, no SSO. No per-account
compute budget (the caps are per worker per request). Windows confinement is
[PRD-006b](prd/pending/PRD-006b-windows-appcontainer.md). A narrowed *macOS*
read posture (the seatbelt equivalent of `hosted`) is not built — hosted mode
is the Linux image. Attribution exists (history trailers, proposal and comment
records) but is not a queryable per-instance log.
