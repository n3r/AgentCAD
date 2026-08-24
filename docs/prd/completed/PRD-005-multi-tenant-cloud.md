# PRD-005 — Multi-tenant cloud service

- **Status:** completed (PR #35)
  workspaces, per-project roles, audit principals, OIDC/passkeys, per-tenant
  fair scheduling, local-first sync, signed desktop builds)
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-005a (hard — the hosted deployment, identity substrate
  and default-deny surface this PRD extends) · PRD-006 (must land before
  hosting untrusted tenants, and before per-project roles can mean anything)
- **Related:** PRD-001 (push/pull syncs branches and tags), PRD-006 (worker isolation and metering), PRD-007 (share links ride tenancy), PRD-008 (presence names principals), PRD-020 (fleet principals and quotas)

---

## Carved out to PRD-005a (17 Aug 2026)

The founder decision recorded in [roadmap.md](../../roadmap.md), "Sequencing
decision — the marketplace chain (16 Aug 2026)" — *"005-lite: deploy +
identity + public read only. Orgs, roles, audit principals and local-first
sync stay deferred as genuine deployment work"* — lifted the hosted-core slice
of this PRD onto the critical path as
[**PRD-005a**](../in-progress/PRD-005a-hosted-core.md) (step 2), because
PRD-007 and PRD-031a need a hosted instance, identity and public read, and
need none of the tenancy work.

This document stays `pending` and keeps everything below that is marked
*retained*. Its design spec is
[2026-08-17-hosted-core-design.md](../../superpowers/specs/2026-08-17-hosted-core-design.md).

**Requirements: moved vs retained**

| | Moved to PRD-005a | Retained here |
|---|---|---|
| Identity & auth | FR2 (principal through `client_id_var`; bare `X-Agent-Id` rejected in cloud mode), FR3 (agent tokens — scoped to the instance rather than to orgs/projects), FR4 (local mode unchanged) | **FR1** (OIDC + WebAuthn passkeys — 005a ships local accounts only) |
| Tenancy & roles | FR7 (HTTPS, the Host/origin guard extended to configured public origins) | **FR5** (orgs → workspaces → projects, per-tenant storage roots), **FR6** (per-project view/comment/edit/admin at `write_guard` / tool dispatch / read routes) |
| Sync | — | **FR8, FR9, FR10** (`push`/`pull`/`clone` over authenticated smart-HTTP) |
| Scheduling, audit, deployment | FR13 (UI identity chips, principal in history), FR14 (one `docker compose up`) | **FR11** (per-tenant fair scheduling), **FR12** (append-only audit log), **FR15** (signed/notarised desktop builds) |
| Acceptance | AC4 (compose deploy + `/api/health` reports the mode), AC7 (local mode unchanged) | **AC1, AC2** (two users, per-project roles), **AC3** (offline clone + push), **AC5** (token scoped to project A, 403 on B), **AC6** (audit log distinguishes principals), **AC8** (notarisation/signing) |

**Technical-approach lines superseded for the hosted-core slice** (each with
the reasoning in design-spec Decision 14):

1. *"Identity/membership/audit persist in a per-instance SQLite (WAL)"* —
   005a uses four atomically-written JSON documents with `fcntl.flock`,
   because the audit volume that motivated SQLite is retained *here*, not
   shipped there. If this PRD's audit log needs SQLite, it may introduce it
   for the audit log.
2. *"default to bundled Caddy-style [ACME] automation with an escape hatch"* —
   005a inverts it: bring-your-own proxy by default, Caddy behind
   `--profile proxy`, because default ACME forces a public DNS name at first
   `up` and breaks air-gapped installs, staging boxes and the CI smoke job.
3. **FR2 needs a caveat this PRD did not anticipate.** "The resolved principal
   flows through the existing `client_id_var` … so turn locks, history
   attribution, and events need no per-feature changes" is true *except* for
   `actor_kind` (`agentcad/core/proposals.py:112-124`): `user:nikita` does not
   start with `browser:`, so without a two-line change every authenticated
   human classifies as an agent and PRD-008's human-only per-part claims
   (`agentcad/core/locks.py:292-293`, `:398-399`) silently stop protecting
   anybody. 005a makes that change; this PRD inherits it.
4. The agent-surface tools `grant_role`, `revoke_role`, `list_members` and
   `sync_status` are retained here — they presuppose roles and sync that do
   not exist in 005a. `create_agent_token` / `revoke_agent_token` are
   **CLI-only** in 005a (`agentcad admin token …`); promoting them to tools is
   this PRD's call to make once an audit log exists.

**Open questions this carve-out closes for the remainder:** the identity-store
shape question is answered *for identity* (files) and left open *for audit*
(where SQLite's motivation actually lives); the "single-org self-host is safe
earlier" risk is restated more strictly by 005a's Decision 1 — what makes a
hosted instance unsafe is that Linux has no confinement, not that it has more
than one org.

---

## Problem & motivation

The server binds `127.0.0.1` only, behind a Host-allowlist and same-origin
guard (`server/app._browser_request_allowed`); there is no concept of a
second human user, and "identity" is an unauthenticated `X-Agent-Id` header
dropped into a ContextVar (`agentcad/core/locks.py`) so turn locks can name a
holder. Nothing can be hosted, nobody can be invited, and no action is
attributable in a way anyone should trust. Every collaboration feature v4
exists for — sharing (PRD-007), review threads and presence (PRD-008), a
hosted registry index (PRD-011), agent fleets (PRD-020) — needs this
substrate, and the roadmap's headline ("cloud CAD") is false until it exists.

The competitive evidence: Onshape proves cloud-native CAD wins teams (2M+
users, "PDM built in" as the killer pitch) and simultaneously proves the trap
— no offline mode is its #1 recurring complaint and architecturally
unfixable, and per-company API metering (the "85 requests/day" uproar) is a
structural mismatch with agent workflows (market_research.md, "Cloud-native
CAD: Onshape (the collaboration benchmark)"). Ondsel shows the other failure
mode: a closed cloud layer on an open core died with the company
(market_research.md, "Open-source CAD: FreeCAD, code-CAD, and the Ondsel
lesson", "Business-model guardrails (the Ondsel constraint)"). The structural
answer is local-first with sync, on an open-source stack that self-hosts —
free wins for a git-substrate system ("Where AgentCAD wins", point 5), and
exactly what the air-gapped A&D startups PTC courts need. Agents as
authenticated principals with scoped permissions is what Onshape Labs is
promising and has not shipped ("The 2025–26 convergence").

## Users & jobs

- **Team engineer (human):** work on shared projects from any machine; keep
  working offline on a laptop; sync back without losing anything.
- **Org admin (human):** control who can see/edit which projects; read a
  trustworthy audit trail; manage agent credentials like team members.
- **Design agent (chat/MCP):** act under a real, revocable identity with
  scoped permissions (e.g. propose-but-not-merge once PRD-002 lands) instead
  of a header anyone can spoof.
- **Self-hoster / IT:** deploy the identical open-source stack on their own
  infra — including air-gapped — with one compose file.
- **CI and automation (PRD-004, PRD-020):** authenticate headlessly with
  tokens scoped to exactly the projects they check.

## Goals

- G1. Real identity: OIDC and passkey (WebAuthn) auth; every request resolves
  to an authenticated principal — `user:<handle>`, `agent:chat:<session>`,
  `agent:mcp:<name>` — replacing the honor-system `X-Agent-Id` header.
- G2. Tenancy: orgs contain workspaces contain projects; storage namespaced
  per tenant; per-project roles (view / comment / edit / admin) enforced at
  one choke point.
- G3. Local-first stays sacred: a project remains a plain git repo;
  `agentcad push/pull` syncs laptop ↔ cloud including branches and tags
  (PRD-001); offline work is never blocked, divergence resolves through real
  merges, never silent overwrite.
- G4. Fairness: kernel pool scheduling is per-tenant fair — one tenant's
  rebuild storm cannot starve another's single build.
- G5. Auditability: an append-only log of every mutating action keyed by
  principal, queryable by org admins.
- G6. Self-host parity: `docker compose up` from the public repo deploys the
  same binary the hosted service runs; hosted vs self-hosted differ by
  configuration only. Signed/notarized desktop builds ride the same release
  pipeline.

## Non-goals

- Billing, plans, payments — PRD-006's metering is the substrate; pricing is
  not a PRD.
- Share links, public viewing, customizer — PRD-007 (rides this).
- Comments, presence UI — PRD-008 (consumes principals from this).
- Same-file CRDT co-editing — deliberate roadmap non-goal.
- Per-seat licensing or metered tool APIs — the business guardrails forbid
  both (market_research.md, "Business-model guardrails").
- Enterprise SSO ceremony (SAML/SCIM) — OIDC + passkeys cover the target
  segment first.

## Experience

**Human path.** Sign in at the instance URL with a passkey or an OIDC
provider; pick an org; workspaces list projects with role-appropriate
affordances (a viewer gets no edit controls, no turn acquisition). Lock
chips, history entries, and chat attributions show real names — the same
chips that today say `mcp` or `chat` say `nikita` and `agent:mcp:claude`. To
work offline: `agentcad clone https://…/acme/eng/rocket`, edit locally
against the local kernel, `agentcad push` when back online; divergence drops
into PRD-001 merge flow with surfaced conflicts.

**Agent path.** An admin mints a token scoped to (org, projects, role,
expiry) and puts it in the MCP config; every tool call executes as that
principal. `get_turn` answers `user:nikita` vs `agent:mcp:claude`; a call
outside the token's scope returns a structured `permission_error` naming the
missing role. `whoami` lets an agent introspect its own scope before
planning.

**Handoff.** A human grants an agent edit on one project; the agent works,
the human watches the same project live from the browser; the audit log
records both, interleaved, with no ambiguity about who did what.

## Functional requirements

**Identity & auth**
- FR1. OIDC authorization-code flow and WebAuthn passkeys; browser sessions
  via secure cookies, API/MCP via bearer tokens. A self-hosted instance
  works with local accounts + passkeys alone (no external IdP required).
- FR2. Every request resolves to a principal (`user:…`, `agent:chat:…`,
  `agent:mcp:…`); the resolved principal flows through the existing
  `client_id_var` ContextVar so turn locks, history attribution, and events
  need no per-feature changes. In cloud mode a bare `X-Agent-Id` header is
  rejected, not trusted.
- FR3. Agent tokens: minted and revoked by users/admins, scoped to org +
  project set + role + expiry; revocation effective on the next request.
- FR4. Local mode (no auth configured, `127.0.0.1`) behaves exactly as
  today: no login, identity plumbing unchanged, full suite green — auth is a
  deployment mode, never a tax on local use.

**Tenancy & roles**
- FR5. Orgs → workspaces → projects; storage rooted per tenant
  (`<data>/orgs/<org>/<workspace>/<project>`); project names unique per
  workspace; no cross-tenant path reachable through any route or tool.
- FR6. Per-project roles view/comment/edit/admin (org-level defaults +
  per-project overrides), enforced at the choke points that already exist:
  `ProjectStore.write_guard` for writes (stacked with turn locks), the
  `/api/tools/{name}` dispatch for tools, and read routes for reads.
  Violations return `permission_error` (403) naming the required role.
- FR7. HTTPS: TLS terminated by the compose stack's bundled proxy (or the
  platform LB); the Host-allowlist/same-origin guard extends to the
  configured public origins; localhost-only remains the default posture
  outside cloud mode.

**Sync & local-first**
- FR8. `agentcad push` / `agentcad pull` sync a project's git repo (the
  `.history` GIT_DIR engine from `agentcad/core/history.py`) with the hosted
  copy over authenticated smart-HTTP; branches and tags (PRD-001) travel;
  derived data (`.cache/`, `exports/`) never syncs.
- FR9. Divergence on push/pull resolves via PRD-001 merge machinery —
  conflicts are surfaced (CLI and UI), never silently last-writer-wins. A
  pushed state is validated like any change: the server rebuilds on next
  open/build and broken states show as ordinary build errors, not rejected
  pushes.
- FR10. `agentcad clone <url>` produces a fully working local project
  (scripts, manifest, history) that builds offline with the local kernel.

**Scheduling, audit, deployment**
- FR11. Kernel pool requests carry the tenant; scheduling is weighted-fair
  across tenants with bounded queue depth per tenant. Pool affinity keys are
  namespaced by tenant + project so cross-tenant part-id collisions cannot
  poison a worker's shape LRU.
- FR12. Append-only audit log per org: `{ts, principal, action (tool/route),
  project, args digest, outcome}` for every mutating call plus auth events;
  admin-queryable; retention configurable.
- FR13. UI identity: presence/lock chips, history, and chat attribution
  render principal names (avatars where a profile exists); `project_history`
  commits carry the acting principal in the `Client:` trailer
  (`history.with_client_trailer`/`author_of()`), rendered by the UI — not as
  the git commit **author**, which stays fixed (`AgentCAD <agentcad@local>`)
  so per-commit bookkeeping is never dressed up as a cryptographic claim.
- FR14. One `docker compose up` from the public repo yields the full stack
  (server, TLS proxy, persistent volume); the image wraps the same
  distribution the PyInstaller bundle ships; zero closed components.
- FR15. Release pipeline produces signed/notarized macOS and signed Windows
  desktop builds alongside the compose image (pipeline ships secrets-gated;
  positive signing evidence requires provisioned certificates).

## Agent surface

New tools: `whoami {}` → principal, org, roles ·
`create_agent_token {name, scope, role, ttl_days?}` (secret returned once) ·
`revoke_agent_token {token_id}` · `grant_role {project, principal, role}` ·
`revoke_role {project, principal}` · `list_members {org}` ·
`sync_status {project}` → ahead/behind vs remote.
Changed: every tool executes under an authenticated principal; `acquire_turn`
/ `get_turn` / `release_turn` report principal identities; `project_history`
entries carry the acting principal.
New error types: `auth_error` (401), `permission_error` (403, details name
the required role) — same structured `{error: {type, message, details}}`
contract.
CLI: `agentcad login`, `agentcad clone <url>`, `agentcad push`, `agentcad
pull`. Events: the WS channel is tenant-scoped; `project_changed` gains the
acting principal.

## Technical approach

- **Route pack** `agentcad/server/routes_auth.py` (OIDC/passkey flows,
  sessions, tokens, membership) and an identity layer in the one middleware
  that already assigns client identity in `app.create_app` — the sanctioned
  core touch, since that seam exists precisely for this. RBAC lives in a new
  `agentcad/core/authz.py` consulted by `write_guard`, tool dispatch, and
  read routes.
- **Tenant store resolution:** `ProjectStore` is already path-rooted; cloud
  mode instantiates a store per tenant behind a resolver keyed by the
  authenticated org/workspace. Identity/membership/audit persist in a
  per-instance SQLite (WAL) — projects stay plain files and plain git repos
  regardless.
- **Sync:** serve each project's `.history` repo over authenticated
  smart-HTTP (`git http-backend` behind the auth layer, or in-process
  dulwich — decide in design); the CLI shells out to git with a credential
  helper. The server materializes the worktree from the default branch on
  receive (the GIT_DIR/worktree split makes this a checkout, not a copy).
- **Fair scheduling:** extend `KernelPool` (`_pick` is affinity-hash +
  round-robin today) with per-tenant weighted queues; the `affinity=` kwarg
  seam already flows from the service — add `tenant=` beside it.
- **Audit:** a tool-dispatch wrapper plus an EventBus subscriber appending
  to the org's audit store.
- **Frontend:** login page, org/workspace navigation, members and tokens
  panels; identity chips reuse the existing lock-chip rendering.
- **MCP remote mode:** `agentcad mcp --remote <url> --token …` proxies the
  hosted API instead of auto-starting a local server.
- Kernel untouched. Manifest untouched. All new behavior is additive; local
  mode is the zero-config default.

## MVP & phasing

- **MVP:** auth (OIDC + passkeys + local accounts), orgs/workspaces/roles,
  tenant-scoped stores, `permission_error` contract, audit log, push/pull
  sync + clone, compose deployment with TLS, identity-aware UI chips, signed
  desktop builds in the release pipeline.
- **Phase 2:** agent tokens with scoped roles + `whoami`/token tools;
  per-tenant fair scheduling (required before onboarding heavy tenants);
  remote MCP mode; audit query UI.
- **Phase 3:** org policy defaults (e.g. agents propose-but-not-merge with
  PRD-002), workspace-level sharing defaults feeding PRD-007, hosted
  registry scopes for PRD-011.

## Acceptance criteria

- AC1. Two users in one org collaborate on one project from two machines
  against a hosted instance — both see edits live, attribution is correct
  (browser session, staged instance).
- AC2. A third user without access receives structured `permission_error` on
  read and write; granting view then edit flips each capability without a
  restart (test). **Amended**: `permission_error` (403) is what a person who
  *has some role in the org/workspace* but not enough on this project gets —
  a person with **no membership in the org/workspace at all** gets a
  name-free **404** (`"no such workspace"`) the moment they try to address
  it, the same answer whether the org exists and they hold nothing there or
  the org does not exist at all, because a 403 there would itself confirm
  the org exists (FR5's "no cross-tenant path reachable" extends to "no
  cross-tenant *existence check* reachable" — `security.resolve_tenant`).
  The acceptance test covers both shapes, not only the 403 one.
- AC3. A laptop clone builds and edits fully offline, then `agentcad push`
  syncs; a deliberately divergent branch surfaces PRD-001 merge conflicts
  rather than overwriting (test + CLI session).
- AC4. The whole stack deploys from the public repo with one compose file on
  a clean VM; `/api/health` reports the deployment mode; TLS serves the UI
  (documented run, CI-checked compose config).
- AC5. An agent token scoped to project A edits A and is 403'd on project B;
  revocation takes effect on the next call (test).
- AC6. The audit log distinguishes a human edit, a chat-agent edit
  (`agent:chat:main`), and an MCP edit (`agent:mcp:<name>`) on one project
  (test).
- AC7. Local mode unchanged: full suite green with no auth configured;
  existing MCP setup keeps working unmodified (existing tests).
- AC8. The macOS build passes notarization and the Windows build passes
  signing in the release pipeline (CI evidence; pipeline ships
  secrets-gated, positive signing evidence requires provisioned
  certificates).

## Risks & open questions

- **Untrusted tenant code:** hosting strangers means executing their scripts
  — gate hosted onboarding on PRD-006 (Linux confinement + quotas);
  single-org self-host is safe earlier. Sequencing must be explicit in the
  design spec.
- **Sync conflict UX at the CLI:** pull-merge-push loops with PRD-001
  conflicts need a humane CLI story; MVP may require resolving in the UI or
  editor and re-pushing — document honestly.
- **Identity store shape** (one SQLite per instance vs per-org files):
  audit volume and membership queries favor SQLite; keep projects as files
  either way. Decide in design with a migration note.
- **Term collision:** tenancy "workspaces" vs PRD-025's UI workspace tabs
  (Build/Produce/Test/Library/Market). Settle naming before either surface
  ships — one of the two must rename.
- **Compose TLS:** bundled ACME vs bring-your-own-proxy; default to bundled
  Caddy-style automation with an escape hatch.
- **WebSocket fan-out at tenant scale** (today one process, one bus): fine
  for MVP scale; note the multi-process bus question for the design spec.

## Competitive references

Onshape (market_research.md, "Cloud-native CAD: Onshape"): the existence
proof for cloud CAD and the cautionary tale — no offline (unfixable),
metered API access, agent permissions promised via Onshape Labs but not
shipped. Fusion Team: file-sync with component locking, not cloud-native.
Ondsel ("Open-source CAD… the Ondsel lesson"): a closed cloud layer on an
open core died with the company. We differ by: local-first with real git
sync (offline is a feature, not a gap), agents as first-class authenticated
principals with scoped revocable tokens, an identical open-source self-host
path (one compose file), and no API metering ever — the API is the product.
