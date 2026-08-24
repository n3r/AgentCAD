# PRD-005 Multi-tenant cloud — design spec

Grounded in a full seam map (005a security/authstore/appmode, identity
plumbing, store rooting, choke points, history engine, KernelPool, CLI,
deployment, frontend, tests, deps) and an executed spike (git smart-HTTP
round trip, WebAuthn-without-a-browser, OIDC-without-authlib, audit-store
benchmarks — report preserved as the sibling
`2026-08-24-multi-tenant-cloud-spike.md`). PRD:
`docs/prd/in-progress/PRD-005-multi-tenant-cloud.md`. Slice plan:
`docs/superpowers/plans/2026-08-24-multi-tenant-cloud.md`.

## Scope rulings (recorded, not re-litigated)

- **Full MVP + Phase 2** of the PRD: FR1–FR14 and the Phase-2 items (agent
  tokens as tools, `whoami`, fair scheduling, remote MCP). **FR15/AC8
  (signed/notarized desktop builds) lands as a secrets-gated release
  pipeline**: the workflow, entitlements, notarization and signing steps
  ship and run end-to-end **when** `MACOS_CERT_P12`/`APPLE_ID_*`/
  `WINDOWS_CERT_PFX` secrets exist, and skip with an explicit notice when
  they don't. No agent can conjure signing identities; the founder
  provisions secrets, the pipeline is ready. AC8's positive half is
  deferred to that provisioning — stated in docs, PRD, and the PR.
- **FR13 is satisfied by the `Client:` trailer + UI rendering**, not by
  per-commit git authors. `history.py` documents why the author is fixed
  (`AgentCAD <agentcad@local>`): rewriting authors would dress bookkeeping
  as a cryptographic claim. The principal already rides the trailer
  (`with_client_trailer`) and `author_of()` reads it back; the UI renders
  it. The PRD's "as git author" phrasing is amended, not implemented.
- **Naming:** tenancy keeps the PRD's own `org → workspace → project`.
  The shipped shell's `workspace` identifier (layout localStorage keys,
  the `#workspace` DOM id) is an internal, never-user-facing slot name —
  no rename, no migration. PRD-025 (pending) must pick a different
  user-facing word for its tabs; noted for its future design.
- **Audit introduces this repo's first SQLite** — exactly the one place
  005a's Decision 14 permits. Identity stays JSON.
- **AC1 (two machines, staged instance)** is graded as: the two-user
  machine half in tests (two cookie jars over one hosted app, live WS on
  both) + the deploy-smoke workflow extended to a scripted two-user
  session. AC4's compose deploy rides the same workflow (it runs on
  push-to-main/dispatch, not PRs — evidence lands post-merge, stated).

## 1. Tenancy model & storage — Decision 1

`core/tenancy.py`: orgs → workspaces → projects, persisted in
**`<state>/auth/orgs.json`** (authstore idiom: same `_Guard`
registry-scoped flock, mtime-keyed read cache, random-tmp atomic writes —
the file joins the four existing documents; `authstore.py` itself is
extended only with a generic document accessor if needed, not reshaped).
Shape: `{orgs: {<org>: {label, members: {<handle>: org_role},
workspaces: {<ws>: {projects: {<proj>: {roles: {<principal>: role}}}}}}}}`.
Org-level default roles + per-project overrides (FR6). Names validate
through the existing `ID_RE` grammar per level.

Storage roots (FR5): `<projects_dir>/orgs/<org>/<ws>/<proj>` in hosted
mode. **Local mode is byte-for-byte today's flat root** — tenancy resolves
only when a request carries a tenant (FR4/AC7's non-negotiable).

## 2. Store & service integration — Decision 2 (the load-bearing one)

One process keeps **one `AgentCADService`, one `ProjectStore`, one kernel
pool** (a store-per-tenant service fleet would multiply kernels at
~0.5 GB/worker). Tenancy enters as a **per-request ContextVar resolver**,
the exact `branch_resolver` precedent:

- `core/tenancy.py` owns `tenant_var: ContextVar[Tenant | None]`
  (`Tenant = (org, workspace)`), set by `security.guard()` beside
  `set_client_id` (the sanctioned identity seam) from the principal's
  resolved org/workspace context (from the URL's project addressing —
  see below — or the token's scope), cleared for local mode.
- `ProjectStore` gains a **`root_resolver: Callable[[], Path | None]`
  post-init seam** (sibling of `write_guard`/`branch_resolver`, same
  install pattern): `_locate`/`create`/`list_projects` consult it; `None`
  → today's root. This is a surgical `project.py` change (project.py is
  not a no-touch core), threaded through the existing private path
  helpers so every path composition inherits it.
- **Project addressing stays single-segment** in every existing route and
  tool (`{proj}` unchanged): the tenant is carried by the request context
  (session's active org/workspace, header `X-Agentcad-Workspace` for API
  clients, token scope for bearer principals), never spliced into project
  ids. `ID_RE` unchanged. Cross-tenant reach is impossible because path
  composition goes through the resolver and membership is checked before
  the resolver answers (Decision 3).
- **Tenant-qualified keys**: turn locks, claims, presence rosters, undo
  cursors and event routing key on project name today; a cross-tenant
  name collision must not collide them. One helper —
  `tenancy.qualified(proj) -> str` (`org/ws/proj` in hosted, `proj`
  local) — is applied at the wrapper layer: the RBAC write-guard wrapper
  (Decision 3) re-keys the turnlock check; `EventBus.publish` is wrapped
  (captured-original + `_WRAPPED` sentinel, the `tools_structure` idiom)
  to stamp `event["tenant"]`; the `/ws` route filters events by the
  connection's tenant. Local mode: no stamp, no filter, no re-key.

## 3. RBAC — Decision 3

`core/authz.py`: `role_of(principal, org, ws, proj) -> str | None`
(org default + per-project override, precedence documented),
`require(action, ...) -> None | raises PermissionError` with the ladder
view < comment < edit < admin. New `PermissionError` (`permission_error`,
403, details name the required role and the project) beside 005a's
`AuthzError`; `auth_error` (401) already exists.

Enforcement at three points, all wrappers, no core edits:
1. **Reads**: `security.guard()` (own 005a file) — after principal
   resolution, a route whose path binds `{proj}` (a compiled matcher over
   the mounted routes, built once at install) requires `view`.
   Non-project routes are untouched. Local mode: no-op.
2. **Tools**: the registry built by `build_registry` is wrapped at serve
   time (`cli` seam, where security.install already runs first) with an
   authz-checking `call` — `args["project"]` requires the tool's
   registered floor (`edit` for mutating tools by default; a small
   explicit map for exceptions like `proposal_review` needing `comment`+).
   Chat/MCP dispatch go through the same registry object, so one wrapper
   covers all three surfaces.
3. **Writes (defense in depth)**: a write-guard **wrapper** in the
   `presence.ensure_claim_guard` shape (capture current guard, call it
   first, then `authz.require("edit", ...)`), re-installed from the same
   `install_write_guard` seam so registry rebuilds can't drop it.

## 4. Auth: OIDC + passkeys — Decision 4

- **OIDC (FR1)**: hand-rolled code+PKCE on **httpx + pyjwt** — both
  already in the dependency closure (`pyjwt[crypto]` is promoted to an
  explicit `pyproject` dependency rather than an undeclared transitive of
  `mcp`). No authlib (spike: it buys ~55 lines, costs a new dep tree and
  a 138 ms import). JWKS fetched via httpx (not `jwt.PyJWKClient`'s
  urllib — no shared proxy/CA config), cached with kid-rollover refresh.
  Config per instance: issuer, client id/secret, allowed email→handle
  mapping (closed registration holds: OIDC **signs in** existing local
  handles or auto-links by verified email to an *invited* handle — it
  never opens registration).
- **Passkeys (FR1)**: `webauthn>=3` behind a new **`agentcad[cloud]`
  extra**, imported lazily inside `routes_auth` handlers (the FEM
  gating precedent: the routes register 404/501-absent without the
  extra; local accounts always work — spike area D). `soft-webauthn` is
  rejected (forces `cryptography<45`); tests use the spike's ~70-line
  virtual ES256 authenticator (cryptography + cbor2, both in the extra's
  closure) — full ceremony in 2–3 ms, four negative cases covered.
- Sessions/tokens/enrolment/scrypt: 005a's `authstore` unchanged; passkey
  credentials and OIDC links are new per-user fields in `users.json`.

## 5. Agent tokens as tools + audit — Decision 5

- Tools (Phase-2 promotion the PRD reserves for "once an audit log
  exists" — it now does): `whoami {}`,
  `create_agent_token {name, org, projects, role, ttl_days?}` (secret
  returned once), `revoke_agent_token {token_id}`,
  `grant_role {project, principal, role}`, `revoke_role`,
  `list_members {org}`, `sync_status {project}`. All in a
  `tools_cloud.py` pack, registered **only in hosted mode** (the
  security.install-before-build_registry ordering exists for exactly
  this); admin-floor enforced via authz. Token scope extends 005a's
  token records with `{org, projects: [...], role}` — `resolve_token`
  honors scope, revocation is next-request (mtime cache, proven).
- **Audit (FR12)**: `core/audit.py`, SQLite WAL at
  `<state>/audit/<org>.db` (`journal_mode=WAL`, `synchronous=NORMAL`,
  `busy_timeout=30000`, indexes `(ts)`, `(principal, ts)`,
  `(project, ts)` — spike C: 126× query win over JSONL at 100k rows).
  Rows `{ts, principal, action, project, args_digest, outcome}` from two
  taps: the authz-wrapped registry `call` (every mutating tool, outcome
  included) and `routes_auth` (auth events). Admin query route + CLI.
  **Backup**: `VACUUM INTO` documented in deployment.md (a raw `cp` of a
  WAL db loses unflushed rows — measured); the audit db lives outside
  the identity JSON so 005a's tar-backup statement stays true for
  identity.

## 6. Git sync — Decision 6 (spike-driven throughout)

Server (`server/routes_sync.py`, mounted under
`/git/<org>/<ws>/<proj>.git/`):
- **`git http-backend` as a streamed CGI child** — not hand-rolled
  `--stateless-rpc` (the spike proved direct mode silently downgrades to
  protocol v0 unless two version-numbered wire details are owned
  forever; CGI is one `partition(b"\r\n\r\n")`). Request bodies
  **stream** to the child's stdin (pushes arrive chunked, 3 MB+ observed
  — never `await request.body()`), responses stream back. Only the three
  smart endpoints route; everything else 404s. Auth: the security guard
  (bearer or basic-with-token), role floor `edit` for receive-pack,
  `view` for upload-pack.
- **Receive config**: `receive.denyCurrentBranch=ignore` — the spike
  proved `updateInstead` is structurally unusable against the
  `.history` layout (receive-pack derives the worktree by stripping
  `/.git`, ignores `core.worktree`, and rejects every push). A
  **pre-receive hook** (installed at repo init/first serve) closes FR9's
  three holes git's knobs leave open: non-fast-forward branch pushes,
  tag rewrites, and tag deletes are refused with humane
  `remote: agentcad: … pull and merge, never force` messages
  (`denyNonFastForwards` covers only `refs/heads/`; PRD-015 release
  tags must be immutable).
- **Materialization**: after receive, `checkout -f <default>` inside the
  project's write scope (write guard + turnlock honored) — 0.02–0.14 s
  measured; preserves untracked `.cache/`/`exports/`, and the write
  scope prevents clobbering a concurrent editor's uncommitted state.
  Then a rebuild is the ordinary next-open path (FR9: a broken pushed
  state is a build error, not a rejected push).
- CLI (`cli.py` new subcommands, `core/sync.py` runner modeled on
  `packages/_git.py` — the credential-friendly second runner, not
  `history._run`): `agentcad login <url>` (token → `~/.agentcad`
  config, 0600), `clone <url>` (**bare-clone into `.history` then flip
  `core.bare`/`core.worktree`/fetch refspec + `info/exclude` +
  `checkout -f`** — `--separate-git-dir` leaves a forbidden `.git`
  pointer file), `push`/`pull` (explicit refspecs — `--follow-tags`
  misses lightweight tags; pull = fetch + PRD-001 merge machinery,
  conflicts surfaced, never reset), `credential` (the git credential
  helper — tokens never in URLs or `http.extraHeader`, both leak;
  `GIT_TERMINAL_PROMPT=0` always). `sync_status` compares local/remote
  refs. Remote MCP: `agentcad mcp --remote <url> --token …` proxies the
  hosted API.

## 7. Fair scheduling — Decision 7

`kernel/pool.py`: `request()` reads `tenancy.tenant_var` directly (no
signature change, no call-site edits). Per-tenant accounting: a bounded
in-flight/queue cap per tenant (default: max(1, size-1) in flight, queue
depth 32, `429`-style `KernelBusyError` beyond) and **fair pick**:
round-robin across tenants with waiting work, affinity hash *within* a
tenant; affinity keys are namespaced `<org>/<ws>/` in hosted mode (the
`share_build` namespacing precedent) so cross-tenant part-id collisions
cannot poison a worker's shape LRU (FR11). Local mode (no tenant):
today's behavior byte-for-byte, pinned by test.

## 8. Frontend & deployment

- Frontend (Sonnet slice): org/workspace switcher (menu + palette via
  the shell registries), members panel + tokens panel
  (`dialogs.register`, calling `routes_auth`/tenancy routes), identity
  chips already render principals (005a `auth.js`) — extend the
  presence/lock chips to strip `user:`/`agent:` prefixes the same way;
  role-appropriate affordances (a viewer gets no edit controls — drive
  from a `whoami`-shaped session payload).
- Deployment: compose unchanged structurally; docs gain org/roles/audit/
  sync/backup (`VACUUM INTO`) sections; **deploy-smoke.yml extends** to
  the two-user + role-flip + token-scope + audit-query script (AC1/AC2/
  AC5/AC6's deployed half). Release pipeline: new `release.yml` building
  PyInstaller artifacts for macOS/Windows with **secrets-gated**
  codesign + notarytool + signtool steps (skip-with-notice absent
  secrets), attached to GitHub releases on tag.

## 9. Testing strategy

Two-user fixtures (the exploration confirmed none exist): extend the
`hosted` fixture family with a second/third enrolled user and per-test
org/workspace scaffolding; agent-token clients via `Authorization:
Bearer`. OIDC: an in-process mock IdP (FastAPI sub-app: discovery, JWKS,
authorize, token endpoints, RS256 via the test's own key) — full
code+PKCE round trip without the network. Passkeys: the spike's virtual
authenticator (tests importorskip the `[cloud]` extra; the extra is
installed locally + in one CI leg via the existing extras pattern).
Sync: real git round trips against the TestClient-hosted app via a
localhost uvicorn instance (the spike's harness), covering clone → edit
→ push → materialize → divergent push refused → pull-merge; hook
behavior (force push, tag rewrite/delete refused). Audit: three
principal kinds on one project distinguished (AC6). Fairness: two
tenants, one flooding — the quiet tenant's request lands within bound
(deterministic with a stub kernel). Local mode: the whole existing suite
IS the regression gate (AC7) — no fixture changes.

## 10. What does not change

Kernel workers and protocol; the manifest; `service.py`/`tools.py`/
`app.py`/`worker.py` cores (identity middleware in `app.py` was 005a's
sanctioned touch and already exists; everything here lands via packs,
wrappers, `security.py`, and the two named surgical seams:
`ProjectStore.root_resolver` and `KernelPool` fair pick); local-mode
behavior everywhere (FR4/AC7); 005a's anonymous surface (new routes are
default-deny; the sync endpoints are authenticated, never public);
`history.py`'s fixed-author design; the flat project namespace in ids,
routes, and tools.
