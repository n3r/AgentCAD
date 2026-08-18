# Deploying AgentCAD (hosted mode)

> **Read this first.** An account on this instance can
> execute arbitrary Python on the host; give one only to someone you would
> give a shell to.
>
> A part script *is* arbitrary Python — that is the product
> (`agentcad/kernel/worker.py`) — and on Linux the kernel worker has no
> confinement until PRD-006 lands. So:
>
> - **Registration is closed.** Accounts exist only because an administrator
>   minted an enrolment link over the CLI. There is no sign-up form and there
>   will not be one in this release.
> - **`admin` and `member` are not a security boundary between each other.**
>   The role governs who may manage accounts and tokens. Every member can
>   already read, write and execute as the server user.
> - **Put it on a single-purpose VM**, with nothing else you care about on it,
>   and back the volume up.
>
> What *is* enforced: the internet cannot reach anything but nine enumerated
> anonymous routes, none of which touch the geometry kernel.

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
| `AGENTCAD_STATE_DIR` | `<config-dir>/state` | Identity state (`auth/`, `secret.key`). Created `0700` and repaired to `0700` at startup — it holds the session secret and the password hashes. Compose sets `/data/state`. |
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
| `AGENTCAD_NO_SANDBOX` | unset | Disables the macOS seatbelt profile. Do not set it on a hosted instance. |
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
  project's `.cache/` and rebuild on demand, so they are not precious.

There is no per-account CPU or memory budget. Any member can queue an expensive
build; the pool has a per-request timeout and nothing more. That is a stated
residual risk (PRD-006 FR G3), and another reason accounts are for people you
trust.

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

**What the caps do NOT yet bound** is stated plainly, because it is the honest
residual: a params-driven mesh can still **balloon RSS and OOM the host** (peak
memory is uncapped until PRD-006), which also owes process/pid caps, a
variant-cache disk budget, and worker egress denial. Until then the operator's
backstop for a link under a distinct-param flood is
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

## What PRD-006 will change

This release's honest limit is that a member is a shell. PRD-006 is the
confinement work — a Linux sandbox for the kernel worker equivalent to the
macOS seatbelt profile, plus per-request CPU and memory budgets. When it lands,
the trust statement at the top of this page gets weaker in the good direction,
and the sizing table gains enforceable limits. Nothing in this deployment
blocks it: the worker is already a separate process behind a request/response
boundary.

Also deferred, and named so it is not a surprise: no audit log, no per-project
ACLs, no organisations, no SSO. Attribution exists (history trailers, proposal
and comment records) but is not a queryable per-instance log.
