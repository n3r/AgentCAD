# PRD-005 Multi-tenant cloud — implementation plan

Design: `docs/superpowers/specs/2026-08-24-multi-tenant-cloud-design.md`.
Spike evidence: `docs/superpowers/specs/2026-08-24-multi-tenant-cloud-spike.md`.
TDD per slice; the controller (not subagents) runs `make test` and commits,
one counted changelog per commit; subagents never run mutating git or
`uv sync` (the controller handles the `[cloud]` extra + lock in Slice 3's
landing). Before every controller full run: sweep stray pytest processes
and `git clean -fdX examples/`.

## Wave 1 (parallel — disjoint files)

### Slice 1 — tenancy model + authz core (FR5-partial, FR6-core) — **Opus**
- `core/tenancy.py`: orgs.json store (authstore `_Guard`/read-cache/atomic
  idioms), org/workspace/member/role CRUD, `tenant_var`, `qualified()`,
  `Tenant` addressing helpers; name validation via `ID_RE` per level.
- `core/authz.py`: `role_of` (org default + per-project override),
  `require`, the view<comment<edit<admin ladder; `PermissionError` →
  `permission_error` 403 with required-role details (register beside
  005a's error mapping — find where AuthzError→403 maps).
- Tests: role precedence table, ladder, permission_error shape, orgs.json
  concurrency (second-writer flock), no service integration yet.

### Slice 2 — git sync, server half (FR8-server, FR9) — **Opus**
- `server/routes_sync.py`: streamed `git http-backend` CGI proxy for the
  three smart endpoints under `/git/{org}/{ws}/{proj}.git/…` (stream both
  directions — never buffer; spike §A has the working CGI env + header
  parsing), guard-authenticated (bearer/basic-token), `view` floor for
  upload-pack / `edit` for receive-pack (authz import is lazy so the
  route pack loads before Slice 1 merges — coordinate: this slice STUBS
  the floor check behind a small `_require = None` seam the controller
  wires after both land, and documents it).
- `core/sync_server.py`: repo prep (`receive.denyCurrentBranch=ignore`,
  the pre-receive hook closing force-push/tag-rewrite/tag-delete with
  humane messages), post-receive materialization (`checkout -f` under
  the project write scope), `http.receivepack` config.
- Tests: real localhost round trip (clone/push/refs+tags travel),
  non-FF push refused, force refused, tag rewrite/delete refused,
  materialization preserves `.cache/`, streamed 3 MB push, anonymous 401,
  wrong-role 403 (with the stub wired to a fake).

### Slice 3 — OIDC + passkeys (FR1) — **Opus**
- `pyproject.toml`: promote `pyjwt[crypto]` to explicit dep; add
  `cloud = ["webauthn>=3", "cbor2>=5"]` extra. (Controller runs
  `uv lock`/`uv sync --extra cloud --extra fem --extra usd` at landing.)
- `core/oidc.py`: code+PKCE flow on httpx+pyjwt (spike §B″ 55-line
  skeleton): discovery, JWKS cache with kid-rollover, state/nonce/PKCE,
  ID-token validation (`alg` allowlist, iss/aud/exp/nonce), closed-
  registration linking (sign in existing/invited handles only).
- `routes_auth.py` extensions: OIDC login/callback; WebAuthn
  register/authenticate ceremonies (lazy `webauthn` import; absent extra
  → routes answer 501 with the FEM wording); passkey credentials stored
  as new per-user fields (authstore accessor additions, additive schema).
- Tests: in-process mock IdP (RS256, its own key) full round trip +
  negative cases (state/PKCE/nonce/alg/exp); virtual-authenticator
  ceremony round trip + four negatives (spike's harness);
  extra-absent gating; local accounts untouched.

## Wave 2 (blocked by Wave 1)

### Slice 4 — service integration: resolver, RBAC enforcement, qualified keys (FR5, FR6, FR2) — **Opus** (needs S1)
- `project.py`: the `root_resolver` post-init seam (surgical, sibling of
  `branch_resolver`), consulted by path composition + `list_projects` +
  `create`.
- `security.py`: tenant resolution into `tenant_var` beside
  `set_client_id` (session active-workspace / `X-Agentcad-Workspace` /
  token scope); read-route `view` floor via the compiled `{proj}`-route
  matcher; wire Slice 2's floor seam.
- Registry authz wrapper (installed at the cli serve seam, covers
  tools/chat/MCP), write-guard authz wrapper (ensure_claim_guard shape,
  re-installed from install_write_guard's seam), `EventBus.publish` wrap
  (tenant stamp; `_WRAPPED` sentinel) + `/ws` tenant filter; turnlock
  re-keying via `qualified()` in the wrapper.
- Tests: AC2 (view→edit flip without restart), cross-tenant 404/403
  (same project name in two orgs — no leakage through routes, tools,
  events, locks), local mode byte-identical (no tenant → no-op
  everywhere), store roots land under `orgs/<org>/<ws>/`.

### Slice 5 — agent tokens as tools + audit (FR3, FR12) — **Opus** (needs S1; touches authstore after S3 lands)
- Token scope `{org, projects, role}` on 005a token records;
  `resolve_token` honors scope; `tools_cloud.py` pack (hosted-only
  registration): `whoami`, `create_agent_token`, `revoke_agent_token`,
  `grant_role`, `revoke_role`, `list_members`, `sync_status` (stub until
  S6 fills remote comparison — return local-only shape, documented).
- `core/audit.py`: SQLite WAL (spike §C pragmas + indexes), append taps
  in the registry wrapper (mutating tools, outcome) + routes_auth (auth
  events); admin query route + `agentcad admin audit` CLI; retention
  config; `VACUUM INTO` backup helper.
- Tests: AC5 (scoped token 403 on B, revocation next-request), AC6
  (three principal kinds distinguished), audit query filters, WAL
  busy-timeout under concurrent appends, backup correctness
  (`cp` loses, `VACUUM INTO` recovers — the spike's proof as a test).

### Slice 6 — sync CLI + remote MCP (FR8-client, FR10) — **Opus** (needs S2)
- `core/sync.py` (the `_git.py`-style credential-friendly runner) +
  `cli.py` subcommands: `login`, `clone` (bare-into-`.history` + config
  flip + excludes + checkout), `push`/`pull` (explicit refspecs; pull =
  fetch + PRD-001 merge, conflicts surfaced), `credential` helper
  (0600 config, `GIT_TERMINAL_PROMPT=0`); `sync_status` real
  implementation; `agentcad mcp --remote <url> --token …`.
- Tests: AC3 (offline clone builds; divergent push surfaces merge
  conflicts, never overwrites), token never lands in any file/URL/ps
  output (spike's leak checks as assertions), round trip against the
  S2 server.

## Wave 3 (blocked by Wave 2)

### Slice 7 — fair scheduling (FR11) — **Opus, small**
- `kernel/pool.py`: tenant-aware fair pick (tenant_var read inside
  `request`), per-tenant in-flight/queue bounds + `KernelBusyError`,
  namespaced affinity in hosted mode; local mode byte-identical
  (pinned).
- Tests: flood-vs-quiet-tenant bound (stub kernel client — deterministic),
  affinity namespacing, local-mode behavior unchanged.

### Slice 8 — frontend — **Sonnet**
- Org/workspace switcher (shell menu + palette), members + tokens
  dialogs (`dialogs.register`), role-appropriate affordances from the
  session payload, presence/lock chips rendering principals.
  Playwright verification against a hosted-mode serve (two browser
  contexts = AC1's visual half).

### Slice 9 — deployment + release pipeline (FR7, FR14, FR15) — **Sonnet**
- deploy-smoke.yml: extend to two users + role flip + scoped token +
  audit query (AC1/AC2/AC5/AC6 deployed halves); compose/docs updates
  (orgs, roles, sync, audit backup `VACUUM INTO`, `[cloud]` extra).
- `release.yml`: PyInstaller macOS/Windows artifacts on tag;
  codesign+notarytool / signtool steps gated on secrets presence with
  explicit skip notices (AC8 pipeline half); docs list the secrets the
  founder must provision.

## Wave 4

### Slice 10 — acceptance + docs — **Opus tests, Sonnet docs** (needs all)
- `tests/test_prd005_acceptance.py`: AC1 (machine half), AC2, AC3, AC5,
  AC6, AC7 (suite-is-the-gate + count-guard test in the house shape);
  AC4/AC8 graded as evidence with pointers to the workflows.
- Docs: deployment.md (the "Still deferred" list shrinks to reality),
  agent-api.md (new tools + permission_error/auth_error), user-guide
  (orgs/members/tokens/sync), architecture.md (tenancy resolver seam,
  authz choke points, sync design, audit store), AGENTS.md + CLAUDE.md
  trap bullets (denyCurrentBranch/updateInstead, pre-receive hook holes,
  WAL backup, tenant ContextVar, streamed CGI, credential helper,
  workspace naming ruling), PRD FR13/FR15 amendments, changelog.

## Non-negotiables
- Local mode byte-identical everywhere (AC7): every wrapper no-ops
  without a tenant/security config; the existing suite is the gate.
- No new anonymous surface: sync + all new routes authenticated;
  default-deny holds; `test_hosted_surface` equality test extended
  deliberately or not at all.
- Never buffer git bodies; never put tokens in URLs/extraHeader/ps.
- The pre-receive hook is the FR9 contract — branches non-FF, tag
  rewrite, tag delete all refused with the humane message (tested).
- Cores untouched except the two named seams; wrappers use
  capture-and-reinstall idioms with `_WRAPPED` sentinels.
- Changelog per commit citing the full-suite count; controller-only
  `uv lock`/`uv sync`; renumber changelogs above main at merge time.
