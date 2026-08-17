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
| `AGENTCAD_SECRET_KEY` | generated | ≥32 characters. When unset, one is generated and persisted `0600` at `$AGENTCAD_STATE_DIR/secret.key`. |
| `AGENTCAD_STATE_DIR` | `<config-dir>/state` | Identity state (`auth/`, `secret.key`). Compose sets `/data/state`. |
| `AGENTCAD_PROJECTS_DIR` | `~/AgentCAD/projects` | Where projects and their `.history` git repos live. Compose sets `/data/projects`. |
| `AGENTCAD_HOST` | `127.0.0.1` | Listen address. A non-loopback bind **requires** `AGENTCAD_MODE=hosted`. |
| `AGENTCAD_PORT` | `8630` | Listen port. |
| `AGENTCAD_KERNEL_POOL_SIZE` | `max(1, min(3, cores//3))` | Kernel workers, ≈0.5 GB RSS each. Compose pins `1` rather than letting it float with the host. |
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
        proxy_read_timeout 600s;                     # a long build is a long request
    }
}
```

**Caddy**

```
cad.example.com {
    reverse_proxy 127.0.0.1:8630
}
```

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
- **Floor: 2 vCPU / 4 GB** with `AGENTCAD_KERNEL_POOL_SIZE=1` (the compose
  default). A single worker serialises builds; that is fine for a small team.
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
outstanding link for that handle.

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
| `POST` | `/api/auth/login` | rate-limited per handle **and** per address; every failure is one indistinguishable answer |
| `GET`·`POST` | `/api/auth/enrol/{token}` | single-use, 7-day, admin-minted, unguessable |
| `GET` | `/api/public/packages` | the parts catalog, `scope: "public"` indexes only |
| `GET` | `/api/public/packages/{name}` | ditto |
| `GET` | `/api/public/packages/{name}/versions/{version}` | ditto — the pre-generated metadata |
| `GET` | `/api/public/packages/{name}/versions/{version}/preview` | a shipped `.png`, resolved inside the version directory |

Nine entries, every one a file read. **Zero kernel calls**, proved by a test
that exercises the whole surface with the kernel instrumented — with a positive
control, so a broken counter cannot make it pass.

**A private index stays private.** The catalog routes serve only indexes whose
`index.json` declares `scope: "public"`; a package carried only by a
`scope: "private"` index answers exactly the same `404` as a package that does
not exist, so the surface is not an oracle for what you have. The
*authenticated* `GET /api/packages/search` does walk every index, which is why
it is not public and why this is a separate route pack rather than a flag on
that one. If you configure a private index, configure it with `scope:
"private"` in `~/.agentcad/config.json` (inside the container: `/data/home`) —
the default is `public`.

Everything else — every project, every part, every geometry route, the
WebSocket, and the package management routes — requires a session cookie or a
bearer token. Anonymous requests to them get `401`, including to paths that do
not exist, because the guard answers before routing and a `404` would be a free
map of the instance.

Responses on the public routes carry `Cache-Control: public, max-age=300`, so a
CDN or reverse proxy in front absorbs a flood. There is no per-IP limit on them
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
