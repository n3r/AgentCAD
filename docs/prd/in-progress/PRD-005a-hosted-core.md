# PRD-005a — Hosted core ("005-lite"): deployment, identity, public read

- **Status:** in-progress
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-17
- **Origin:** carved out of [PRD-005](../pending/PRD-005-multi-tenant-cloud.md)
  by the founder decision recorded in
  [roadmap.md](../../roadmap.md), "Sequencing decision — the marketplace chain
  (16 Aug 2026)": *"005-lite: deploy + identity + public read only. Orgs,
  roles, audit principals and local-first sync stay deferred as genuine
  deployment work."*
- **Depends on:** PRD-011 (completed — the `scope: "public"` package index is
  the public-read payload) · PRD-008 (completed — presence, claims and turn
  locks are the concurrency model this PRD keeps instead of inventing roles)
- **Related:** PRD-005 (the deferred remainder) · PRD-006 (the confinement
  backstop this PRD deliberately does **not** wait for, and states the price
  of) · PRD-007 (step 3 — consumes the public-route seam and the rate-limit
  primitive) · PRD-031a (step 4 — consumes public catalog read + authenticated
  add-to-library)

> **Carve-out note.** This is PRD-005's *first* slice, not a replacement.
> PRD-005 stays in `pending/` and keeps the deferred remainder: orgs,
> workspaces, per-project roles, audit principals, OIDC/passkeys, per-tenant
> fair scheduling, local-first `push`/`pull`/`clone`, and signed desktop
> builds. The mapping FR-by-FR is in
> [PRD-005's "Carved out to PRD-005a" header](../pending/PRD-005-multi-tenant-cloud.md).

## Problem & motivation

Two roadmap steps are blocked on one missing thing, and it is not
multi-tenancy. PRD-007 (share links & customizer, step 3) needs *a hosted
instance a stranger can open*. PRD-031a (seeded read-only catalog, step 4)
needs *a public URL that lists validated packages* plus *an account that can
add one to a library*. Neither needs orgs, roles, audit principals or git
sync — which are the expensive, genuinely deferred parts of PRD-005.

Today none of it is reachable. `agentcad/cli.py:170` binds
`host="127.0.0.1"` with no flag to change it; `agentcad/server/app.py:117-131`
installs exactly one middleware, whose entire access-control content is "the
Host header must be `127.0.0.1`/`localhost`/`::1`" plus a same-origin check —
a defence against DNS rebinding and CSRF against a *loopback* server, which
`docs/changelog/0011-backend-review-fixes-user-guide.md:57` already says "is
not a substitute for auth if the server is ever exposed". Identity is the
`X-Agent-Id` header dropped verbatim into a ContextVar
(`agentcad/server/app.py:130` → `agentcad/core/locks.py:109`); the browser
mints its own (`frontend/js/api.js:38-70`) and
`agentcad/core/locks.py:76-81` documents that it "arrives on a header anyone
can set, on a server with no authentication, so it is bounded rather than
trusted".

The competitive case is PRD-005's and unchanged (Onshape's hosted funnel;
Ondsel's closed-cloud failure; market_research.md, "Cloud-native CAD:
Onshape", "Business-model guardrails"). What this PRD adds is the honest
scoping: a hosted instance is worth shipping *before* tenancy, and it is
**only** worth shipping if the security posture is stated rather than
assumed — because PRD-006 has not landed and Linux, the platform a
deployment runs on, has no confinement at all
(`agentcad/kernel/sandbox.py:29` — `sys.platform == "darwin"` gates the
whole module).

## Users & jobs

- **Founder / first operator:** run one AgentCAD on a VM behind a domain, with
  one command and a documented backup, so links can be shown to people.
- **Invited teammate (human):** get an enrolment URL, set a password, sign in
  from any machine, and work on the shared projects with attribution that is
  a real name instead of `browser:7f3a1b2c`.
- **Design agent (MCP / CI):** hold a revocable bearer token instead of a
  self-asserted header, and drive the same hosted instance the humans use.
- **Anonymous visitor:** browse the validated public catalog and its
  pre-generated previews with no account — and reach nothing else.
- **PRD-007 and PRD-031a (downstream features):** inherit an enumerated,
  test-pinned public-route surface and a rate-limit primitive instead of
  inventing their own carve-outs.

## Goals

- G1. **One deployable instance.** `docker compose up` from the repo serves
  the real app on a configured public origin, with persistent projects and
  identities across restarts and a documented backup.
- G2. **Real identity, minimal mechanism.** Every request in hosted mode
  resolves to an authenticated principal (`user:<handle>` or
  `agent:<token-name>`) or to *anonymous*, and anonymous reaches only an
  enumerated public surface. Sessions for browsers, bearer tokens for
  agents/CI.
- G3. **Public read that executes nothing.** The anonymous surface is
  pre-generated data on disk — the `scope: "public"` package index and its
  shipped preview PNGs — plus the login page and health. **Zero kernel calls
  on the anonymous path, provable by test.**
- G4. **Default-deny by construction.** A route added tomorrow is private
  until somebody names it public in one file, and a test fails when the
  reachable-anonymous set changes.
- G5. **Local mode untouched.** With no hosted configuration the product
  behaves byte-identically to today — same middleware behaviour, same
  identity plumbing, same MCP setup, full suite green.
- G6. **An honest trust boundary.** The documentation says, in every surface
  that matters, that *an account on a 005-lite instance is arbitrary code
  execution on the host*, that registration is therefore closed, and exactly
  what PRD-006 changes about that.

## Non-goals

- **Orgs, workspaces, per-project roles** — PRD-005. This instance has one
  shared project space and two instance-level roles (`admin`, `member`).
- **Audit log** — PRD-005 FR12. History, proposal `audit.jsonl` and comment
  `audit.jsonl` already record attribution; a queryable per-org audit store
  is tenancy work.
- **Local-first sync** (`clone`/`push`/`pull`) — PRD-005 FR8–FR10.
- **OIDC and WebAuthn passkeys** — PRD-005 FR1. Local accounts only; the
  credential table is shaped so they are additive later.
- **Per-tenant fair scheduling** — PRD-005 FR11.
- **Signed/notarised desktop builds** — PRD-005 FR15.
- **Sandboxing, quotas, metering** — PRD-006. This PRD *documents* the
  exposure it does not fix and refuses the surfaces that would exploit it.
- **Share links, embeds, the customizer** — PRD-007. This PRD ships the seam
  (`PUBLIC_PATHS`, the enumeration test, the rate-limit primitive) and the
  written verdict on whether bounded-param rebuilds are defensible; it ships
  no visitor-facing rebuild.
- **Open registration** — never in 005-lite. Accounts are minted by an admin.
- **Billing, plans, email delivery, SMTP** — an air-gapped self-host must
  work, so nothing in the enrolment path may require a mail server.

## Experience

**Operator path.** Clone the repo on a VM. Put `AGENTCAD_PUBLIC_ORIGIN`,
`AGENTCAD_SECRET_KEY` and a volume path in `.env`. `docker compose up -d`.
Point an existing reverse proxy (Caddy/nginx/an LB) at the container, or
enable the bundled `proxy` compose profile. Then
`docker compose exec agentcad agentcad admin user add nikita --admin`, which
prints a **one-time enrolment URL**. `/api/health` answers
`{"status": "ok", "mode": "hosted"}` to anyone; the full body needs a session.

**Member path.** Open the enrolment URL, choose a password, land signed in.
The app looks exactly as it does locally, except the header carries the
handle and a "Sign out" item, and lock chips, presence avatars, claim
banners, comment authors and history entries say `nikita` instead of
`browser:7f3a1b2c`. Two members on one instance see all the same projects and
can both write; PRD-008's turn locks and per-part claims are what keeps them
out of each other's way — the same machinery, now naming real people.

**Agent path.** An admin runs
`agentcad admin token add ci --role member`, which prints
`acad_<id>_<secret>` once. The agent's MCP config gains
`AGENTCAD_URL=https://cad.example.com` and `AGENTCAD_TOKEN=acad_…`; every
proxied tool call then executes as `agent:ci`. `whoami` returns the
principal and role so an agent can introspect before planning. Revoking the
token stops the next call.

**Visitor path.** `https://cad.example.com/` shows the sign-in page. The
catalog is browsable without signing in: the packages, versions, parameter
schemas, connector kinds, spec names, gate verdicts and preview PNGs that
PRD-011's index already publishes. Clicking "Add to library" prompts a sign
in — because adding a package writes a project.

**Handoff.** A member and their MCP agent hold two principals on one
instance; the member watches the agent's rebuilds arrive over the same
WebSocket, and every lock chip, claim and comment names which of the two
acted.

## Functional requirements

### Modes and the binding interlock

- FR1. Exactly two modes, chosen explicitly by `AGENTCAD_MODE`
  (`local` — the default — or `hosted`). Mode is never *inferred* from
  configuration: a missing setting must fail closed, not silently downgrade.
- FR2. `hosted` refuses to start without `AGENTCAD_PUBLIC_ORIGIN` (an
  absolute `https://` or `http://` origin) and a session secret
  (`AGENTCAD_SECRET_KEY`, or one generated once and persisted 0600 in the
  state dir). The error names the missing setting.
- FR3. **The interlock:** `agentcad serve --host <non-loopback>` is refused
  in `local` mode, and `hosted` mode is refused on a loopback-only bind
  unless `--host` is given explicitly. You cannot expose an interface without
  turning auth on, and you cannot turn auth on and stay invisible by accident.

### Identity

- FR4. Local accounts only: handle + password. Handles match
  `[a-z0-9][a-z0-9._-]{0,31}` (≤ 32 chars) so the composed client identity
  stays inside `locks.MAX_CLIENT_ID_CHARS` (64). Passwords are stored as
  `hashlib.scrypt` digests with per-user salts; no new runtime dependency.
- FR5. **No self-registration route exists.** An admin mints an account with
  a single-use, time-limited enrolment token; the enrolee sets the password
  at that URL. Nothing in the flow sends email.
- FR6. Browser sessions are opaque random ids in an `HttpOnly`, `SameSite=Lax`
  cookie (`Secure` whenever the public origin is `https`), stored server-side
  by digest with a sliding TTL and an absolute cap; logout revokes
  immediately.
- FR7. Agent/CI credentials are bearer tokens (`Authorization: Bearer
  acad_<id>_<secret>`), minted and revoked by an admin, optionally
  expiring; revocation takes effect on the next request. Tokens are shown
  once.
- FR8. Login is rate-limited per account **and** per client address, and
  answers a structured `429` carrying `retry_after_s`. Unknown handle and
  wrong password are indistinguishable.
- FR9. The resolved principal becomes the client identity the rest of the
  product already consumes: `set_client_id("user:<handle>/<device>")` or
  `set_client_id("agent:<token-name>")`. In hosted mode a bare `X-Agent-Id`
  is **never** the identity — at most it contributes the `<device>` suffix,
  namespaced under the authenticated principal. In local mode the header
  behaves exactly as today.
- FR10. `actor_kind` (`agentcad/core/proposals.py:112-124`) must classify a
  `user:` principal as `human` and an `agent:` principal as `agent`. Its own
  docstring already designates this the PRD-005 change ("replaces it with the
  authenticated principal's class, with no schema change"). Without it every
  hosted human is classified `agent`, and PRD-008's per-part claims —
  which are human-only by construction (`agentcad/core/locks.py:292-293`,
  `:398-399`) — silently stop protecting anybody.
- FR11. `whoami` (tool + route) returns `{principal, kind, handle|name, role,
  mode}`.

### Authorization (deliberately flat)

- FR12. Two instance roles: `admin` and `member`. Members may read and write
  every project on the instance; admins additionally manage users, enrolments
  and tokens. There is no per-project ACL — see the trust note in FR17.
- FR13. **Default deny.** In hosted mode every HTTP route and the WebSocket
  require an authenticated principal unless the path matches the single
  enumerated `PUBLIC_PATHS` allowlist. A route pack added later is private
  with no action by its author.
- FR14. The public surface is exactly: the static frontend and `/`; a trimmed
  `GET /api/health`; the auth endpoints needed to sign in or enrol; and the
  read-only public catalog surface of FR15. Nothing else, ever, in this PRD.
- FR15. Public catalog read: package search/listing, per-version metadata and
  shipped preview PNGs, restricted to indexes whose declared
  `scope == "public"` (`agentcad/core/packages/indexes.py:105-110`). A
  package that exists only in a `private`-scope index is `404` on the public
  surface, indistinguishably from one that does not exist.
- FR16. **No anonymous request may reach the kernel.** Provable: a test
  exercises the whole public surface with the kernel instrumented and asserts
  zero kernel requests.
- FR17. Documented, in `docs/deployment.md`, in the compose file's header, in
  the admin CLI's help, and on the account-creation path: *an account on this
  instance can execute arbitrary Python on the host* (a part script is
  arbitrary Python — `agentcad/kernel/worker.py:57-59` — and Linux has no
  confinement until PRD-006). Therefore accounts are for people you would
  give a shell to, registration is closed, and roles are not a security
  boundary between members.

### Hosted-mode hardening

- FR18. The Host/Origin guard extends rather than relaxes: in hosted mode the
  Host must match the configured public origin's host, and a browser `Origin`
  on any state-changing method must equal the configured public origin.
  Bearer-token requests are exempt from the Origin rule (a browser cannot
  attach a bearer cross-site).
- FR19. `POST /api/projects/open` — which registers *any* absolute
  filesystem path as a project (`agentcad/server/app.py:177`) — and the
  absolute-path form of `import_cad_file`
  (`agentcad/core/tools_import.py:13-19`) are **disabled in hosted mode**
  with a structured error naming the mode.
- FR20. The presence beacon may no longer name an arbitrary identity
  (`agentcad/server/routes_presence.py:142`): in hosted mode a beacon
  identity outside the caller's own principal namespace is refused.
- FR21. `GET /api/health` is public but trimmed in hosted mode
  (`{status, mode}`); version, kernel state, chat availability and sandbox
  status require a principal.
- FR22. Bundled examples are not auto-registered in hosted mode
  (`agentcad/cli.py:89-99` opens them from the read-only image and
  `_writable_roots` grants writes into it).

### Deployment

- FR23. A `Dockerfile` (multi-stage, non-root, the OCCT system libraries the
  Linux CI job already installs, `git` for the history engine) and a
  `compose.yaml` with one service, one named volume mounted at `/data`
  (`/data/projects`, `/data/state`, `/data/home` for `~/.agentcad`), a
  healthcheck against `/api/health`, and an optional `proxy` profile for TLS.
- FR24. Configuration is environment-only and enumerated in one place:
  `AGENTCAD_MODE`, `AGENTCAD_PUBLIC_ORIGIN`, `AGENTCAD_SECRET_KEY`,
  `AGENTCAD_STATE_DIR`, `AGENTCAD_PROJECTS_DIR`, `AGENTCAD_HOST`,
  `AGENTCAD_PORT`, `AGENTCAD_KERNEL_POOL_SIZE`, `AGENTCAD_EXAMPLES`,
  plus the existing `AGENTCAD_CONFIG`, `AGENTCAD_PACKAGES_DIR`,
  `AGENTCAD_INDEXES_DIR`, `AGENTCAD_NO_SANDBOX`, `AGENTCAD_URL`,
  `AGENTCAD_AGENT_ID` and the new `AGENTCAD_TOKEN`.
- FR25. Identity state lives in atomically-written JSON under
  `<config-dir>/state/auth/`, resolved from `config.config_path().parent` —
  the same derivation `AGENTCAD_PACKAGES_DIR` and `AGENTCAD_INDEXES_DIR`
  already use (`agentcad/core/packages/cache.py:96-104`,
  `agentcad/core/packages/_git.py:98-106`) so `AGENTCAD_CONFIG` isolates it in
  tests — with an `AGENTCAD_STATE_DIR` override. It is never inside a project,
  never inside `.history`, never under `--projects-dir`, and **no
  `AgentCADService` constructs or reads it**, so PRD-004/011 ephemeral
  services are unaffected by construction.
- FR26. Reads and writes are serialised in-process **and** across processes
  with `fcntl.flock` (the `LocalIndex._index_scope` precedent,
  `agentcad/core/packages/indexes.py:502`), because `agentcad admin …` run
  through `docker compose exec` is routinely a second writer. The server
  re-reads on mtime change, so an admin-created account works without a
  restart.
- FR27. `docs/deployment.md` documents sizing (≈0.5 GB RSS per kernel worker
  plus the server), TLS options, the backup procedure (an archive of the
  volume — no database file needs quiescing, because every write is an atomic
  replace), restore, and upgrade.

## Agent surface

New tools: `whoami {}` → `{principal, kind, role, mode}`.
New CLI: `agentcad admin user add|list|disable`, `agentcad admin token
add|list|revoke`, `agentcad admin enrol` (re-mint an enrolment URL) — all
operating directly on the state DB, so they work over `docker compose exec`
with no running session.
New routes (route pack `agentcad/server/routes_auth.py`): `POST /api/auth/login`,
`POST /api/auth/logout`, `GET /api/auth/session`, `GET|POST
/api/auth/enrol/{token}`; admin-only `GET|POST /api/auth/users`,
`GET|POST|DELETE /api/auth/tokens`.
New public route pack (`agentcad/server/routes_public.py`):
`GET /api/public/packages`, `GET /api/public/packages/{name}`,
`GET /api/public/packages/{name}/versions/{version}`,
`GET /api/public/packages/{name}/versions/{version}/preview`.
New error types: `auth_error` (401) and `permission_error` (403) — same
structured `{error: {type, message, details}}` contract; `rate_limited` (429)
with `details.retry_after_s`.
Changed: in hosted mode every existing tool and route executes under an
authenticated principal, and `X-Agent-Id` stops being an identity.
Unchanged: no new event type; the WebSocket payloads are byte-identical.

## Technical approach

- **One new core touch, flagged.** `create_app` (`agentcad/server/app.py:117`)
  has the only middleware in the app and no extension point for adding
  another; PRD-005's own technical approach already sanctions editing it
  ("the one middleware that already assigns client identity … the sanctioned
  core touch, since that seam exists precisely for this"). The edit stays
  minimal: the guard logic lives in a new `agentcad/server/security.py`; the
  middleware body becomes a call into it; `create_app` gains one optional
  `security=` parameter that defaults to `None` = today's behaviour. The
  WebSocket handler gets the same call. A test pins the middleware count and
  order so nothing else accretes there.
- **A second, two-line core seam:** `_mount_route_packs`
  (`agentcad/server/app.py:401-414`) hardcodes `prefix="/api"`. It learns to
  honour an optional module-level `PREFIX`, so PRD-007's `/s/<token>` and
  `/embed/<token>` pages can ship as a route pack without editing `app.py`
  again.
- **Route packs, not core, for everything else:** `routes_auth.py`
  (management) and `routes_public.py` (anonymous read).
- **Storage:** `agentcad/core/authstore.py` — four atomically-written JSON
  documents (`users`, `enrolments`, `sessions`, `tokens`) under the state
  dir, held in memory behind a lock, re-read on mtime change, serialised
  across processes with `fcntl.flock`. **This diverges from PRD-005's
  "per-instance SQLite (WAL)"**: the audit-log volume that motivated SQLite is
  deferred, the repository has never used a database, and plain atomic JSON is
  the house persistence primitive with a simpler backup story. Password
  hashing is `hashlib.scrypt`; session and token *secrets* are ≥256-bit random
  and stored as SHA-256 digests (a slow KDF buys nothing against an
  unguessable secret and would put ~100 ms on every request).
- **The `actor_kind` change (FR10) is the one edit to finished PRD-002 code**,
  and its docstring commissions it. It is two lines, its four consumers
  (`comments.py:91`, `presence.py:63`, `locks.py:148-157`, and proposals
  itself) import rather than re-implement it, and a test pins that local-mode
  classification is byte-identical.
- **Rate limiting** reuses `agentcad.core.presence.TokenBucket`
  (`presence.py:137`) by import — no edit to PRD-008 code. PRD-007 should
  promote it to its own module when it needs a second consumer.
- **Identity composition:** the principal is set through the existing
  `locks.set_client_id`, so turn locks, claims, presence, comments,
  notifications, history attribution and the undo `{scope: "mine"}` filter
  all become principal-aware with **zero** changes to PRD-008 code.
- **Frontend:** one new `frontend/js/auth.js` plus a login/enrol view; a 401
  handler in the single `request()` funnel (`frontend/js/api.js:72-96`); the
  identity chip reads `/api/auth/session`. No bundler, no new vendor.
- **MCP:** `agent/mcp_server.py` already honours `AGENTCAD_URL`
  (`mcp_server.py:41-45`) and sets headers in one place
  (`mcp_server.py:128-130`) — add `AGENTCAD_TOKEN` and suppress the
  auto-spawn when the URL is not loopback.
- Kernel untouched. Manifest untouched. `service.py`, `tools.py`,
  `worker.py`, `project.py`, `locks.py`, `presence.py`, `comments.py`
  untouched. `proposals.py` gets exactly the `actor_kind` change above.
- **Known limitation, recorded not fixed:** `comments.plausible_mention`
  (`comments.py:193-221`) accepts only the `browser`/`chat` families plus
  whoever is in the live presence roster, so `@user:handle` resolves for a
  member who is currently connected and not for one who is offline. Editing
  it means editing finished PRD-008 code for a cosmetic gain; PRD-005's
  member list is the right place to fix it.

## MVP & phasing

- **MVP (this PRD):** modes + the binding interlock · the auth store · the
  security seam with `PUBLIC_PATHS` and its enumeration test · enrolment,
  login, logout, session, `whoami` · bearer tokens + remote MCP credentials ·
  hosted-mode hardening (FR18–FR22) · the public catalog read · Dockerfile +
  compose + `docs/deployment.md` · a CI compose smoke job.
- **Phase 2 (PRD-007 consumes, does not re-derive):** the `/s/` and `/embed/`
  public pages under the `PREFIX` seam, the capability-token principal, the
  bounded-param rebuild and its quotas.
- **Phase 3 (PRD-005 proper):** orgs/workspaces/roles, audit principals,
  OIDC + passkeys as additional credential kinds, per-tenant scheduling,
  `clone`/`push`/`pull`.

## Acceptance criteria

- **AC1.** `AGENTCAD_MODE=hosted` without `AGENTCAD_PUBLIC_ORIGIN` (or
  without a resolvable secret) refuses to start with an error naming the
  missing setting; `agentcad serve --host 0.0.0.0` in `local` mode is refused
  (test).
- **AC2.** In hosted mode, every route of the fully-mounted app answers `401`
  to an unauthenticated request except an enumerated set, and the test that
  enumerates the anonymous-reachable surface fails when a new route joins it
  (test walking `app.routes`).
- **AC3.** An admin-minted enrolment URL is single-use: it sets a password,
  signs the browser in, and a second use `404`s. The member then edits a part
  and a second member receives the change over the WebSocket; lock chips,
  presence, comment author and history author all read `user:<handle>`
  (test + a browser session against a deployed instance).
- **AC4.** Logout invalidates the session on the next request; a revoked
  bearer token `401`s on the next call; an expired one likewise (test).
- **AC5.** Login is rate-limited per account and per address, returning `429`
  with `details.retry_after_s`; a wrong password and an unknown handle return
  identical bodies and take comparable time; no route ever returns a password
  or token digest (test).
- **AC6.** The public catalog surface serves packages and preview PNGs from a
  `scope: "public"` index anonymously, and returns an indistinguishable `404`
  for a package carried only by a `scope: "private"` index (test).
- **AC7.** Exercising every public route with the kernel instrumented
  produces **zero** kernel requests (test).
- **AC8.** `docker compose up` on a clean host serves the UI on the
  configured origin, `/api/health` reports `{"mode": "hosted"}`, an admin can
  be created through `docker compose exec`, and projects plus identities
  survive `docker compose down && docker compose up` (documented run + a CI
  smoke job).
- **AC9.** Local mode is byte-identically unchanged: the full suite is green
  with no hosted configuration, the middleware behaves exactly as before, and
  an existing MCP registration keeps working unmodified (existing tests, no
  edits).
- **AC10.** Two signed-in members are classified `human`: one takes a per-part
  claim, the other is refused with the claim conflict, and a token-bearing
  agent is neither blocked by that claim nor able to take one — i.e. PRD-008's
  claim semantics are identical under composed principals to what they are
  under `browser:<nonce>` identities today (test, on the `tests/test_claims.py`
  fixtures).
- **AC11.** An MCP client configured with `AGENTCAD_URL` + `AGENTCAD_TOKEN`
  against a hosted instance lists tools and calls one successfully; `whoami`
  returns `agent:<name>` and the role; clearing the token makes the same call
  `401` (test + documented session).

## Risks & open questions

- **An account is a shell (the defining risk).** Until PRD-006, any member
  can run arbitrary Python as the server user. Mitigation is honesty and
  posture, not code: closed registration, a documented trust statement in
  four places (FR17), a single-purpose VM, and the anonymous surface proved
  kernel-free (FR16/AC7). PRD-006 is what changes this; nothing in 005-lite
  claims otherwise.
- **Flat authorization may look like a gap.** It is a deliberate statement of
  the truth: with RCE available to every member, a per-project ACL would be a
  *label*, not a boundary. PRD-005 revisits this once PRD-006 makes isolation
  real.
- **A single shared WebSocket bus** fans every event to every member. Correct
  for one shared project space; PRD-005's tenant-scoped channel is the
  refinement.
- **Password auth will read as unfashionable.** Passkeys need a crypto
  dependency and a browser flow; OIDC needs an IdP a self-hoster may not
  have; magic links need SMTP an air-gapped instance cannot have. Local
  accounts are the only mechanism with zero new dependencies that works
  everywhere, and the credential table is shaped for additional kinds.
- **Image size.** OCCT wheels make the runtime image multi-GB; the compose
  smoke job therefore cannot run on every PR. Mitigation: a cheap
  `docker compose config` lint on PRs, the full build on main/schedule.
- **Open question for the founder:** should a 005-lite instance's members be
  able to *delete* each other's projects? Today no route deletes a project at
  all, so the question is not forced — but PRD-027's bulk operations will
  force it.

## Competitive references

Unchanged from PRD-005 (Onshape as the hosted-CAD existence proof and the
no-offline cautionary tale; Ondsel as the closed-cloud failure; both in
market_research.md). One difference is specific to this slice: where Onshape's
free tier made *sharing* the price of the funnel by forcing documents public,
005-lite's anonymous surface is a deliberately tiny, pre-generated,
execution-free allowlist — the funnel is opt-in per artifact (PRD-007) and the
default is private, per the business guardrails ("Business-model guardrails").
