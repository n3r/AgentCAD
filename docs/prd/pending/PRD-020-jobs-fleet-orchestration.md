# PRD-020 — Jobs and fleet orchestration

- **Status:** pending
- **Phase:** v6 — generative engineering & the manufacturing bridge
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-001 (hard — branches are the fleet workspace) ·
  PRD-005 (hard — authenticated principals, roles, scoped tokens) ·
  PRD-006 (hard — metering feeds quotas)
- **Related:** PRD-002 (proposals are the coordination primitive), PRD-004
  (CI runs as jobs), PRD-018/PRD-019 (generation/studies as job kinds),
  PRD-011 (registry validation jobs), PRD-015 (release bundles as jobs),
  PRD-023 (doc regeneration jobs)

## Problem & motivation

Everything long-running in AgentCAD today blocks a caller: rebuilds hold an
HTTP request for up to the kernel's 300 s timeout, chat turns run inside the
request, renders and exports are synchronous, and nothing survives a server
restart. There is no queue, no cancellation, no retry, no notion of "how much
compute did this client consume." That is tolerable for one human on
localhost; it is disqualifying for the workload v6 exists to serve — CI runs
(PRD-004), design studies (PRD-019), generation loops (PRD-018), release
bundles (PRD-015), registry validation (PRD-011), and above all *fleets*:
many agents working one project overnight.

The competitive evidence says this workload is structurally ours to take
(market_research.md, "Where AgentCAD wins" #4, "Business-model guardrails"):
per-seat GUI licensing plus COM automation makes agent fleets economically
and technically impossible on incumbent stacks — community SolidWorks MCP
servers literally fall back to generating VBA macros — and Onshape meters
its API per company (the "85 requests/day" forum uproar). "Run 50 design
agents overnight" is the demo no competitor can price, and compute-metered
(never seat-metered) hosting is the business model the guardrails demand.
The missing substrate is a job system with quotas, plus fleet semantics that
keep fifty agents from trampling each other: scoped roles coordinating
**only** through branches and proposals — never shared mutable state.

## Users & jobs

- **Engineer (human):** kick off a study or CI run, close the laptop, find
  results (and proposals) in the morning; watch progress in a job tray, not
  a frozen spinner.
- **Orchestrating human or agent:** fan a task out to N drafter agents on N
  branches, collect proposals, route them to reviewers.
- **Drafter / reviewer / optimizer agents:** scoped roles — a drafter
  branches and proposes; a reviewer comments and approves; an optimizer runs
  studies. None can exceed its scope; a drafter cannot merge its own work.
- **Operator / admin (human):** set per-principal quotas, see the queue,
  keep one tenant's fleet from starving another (PRD-005/006).

## Goals

- G1. A persisted, resumable job queue in the service for everything
  long-running; jobs survive server restarts.
- G2. One uniform lifecycle surface — submit / status / cancel / list — as
  tools, REST, and UI, with progress streamed as WebSocket events.
- G3. Retries with backoff for transient failures (`kernel_crash`,
  `timeout`), never for deterministic ones (`validation_error`).
- G4. Per-principal quotas (concurrency, queue depth, CPU-seconds via
  PRD-006 metering) with fair scheduling — no principal starves the pool,
  and interactive use keeps reserved headroom.
- G5. Fleet semantics: role-scoped principals coordinating exclusively
  through branches (PRD-001) and proposals (PRD-002); role permissions
  (PRD-005) make "a drafter cannot merge its own work" enforceable.
- G6. Job kinds are an extension point: feature packs register kinds the
  same way they register tools — no core edits to add one.

## Non-goals

- Multi-node distributed execution — one node, one kernel pool first;
  horizontal scale is a PRD-005 maturity follow-on.
- A workflow/DAG engine — jobs are flat units; composition and sequencing
  live in the orchestrating agent, which is better at it.
- Cron/scheduling ("every night at 2am") — later; v1 is on-demand.
- Replacing the synchronous fast path — interactive rebuilds/param edits
  stay request-response; jobs are for work that outlives a request.

## Experience

**Human path.** A job tray in the workbench shell: badge with active count;
panel listing queued/running/recent jobs, each with kind, principal,
progress bar, phase message, cancel button, and links to outputs (report,
proposal, bundle). Anything long kicked off from its own panel — study, CI,
generation, release bundle — routes through the tray automatically. An
admin surface (with PRD-005) shows per-principal usage against quota.

**Agent path.** `submit_job {kind: "study", args: {...}}` → `{job_id}`;
then poll `job_status` or watch `job_update` events; `cancel_job` to stop;
`list_jobs {state: "running"}` to survey. A fleet run is an orchestration
recipe over this: for each of N tasks, create branch `fleet/<run>/<n>`
(PRD-001) and submit a generation or edit job scoped to that branch under a
drafter token; each job's terminal artifact is a proposal (PRD-002).
Reviewer agents list proposals, review, approve; merges happen only under a
principal whose role carries merge permission. Nothing coordinates through
shared files, ever.

**Handoff.** The tray and the proposal queue serve both species: a human
can cancel an agent's job, pick up its branch, or take over review at any
point.

## Functional requirements

**Job model & lifecycle**
- FR1. Job record: `{id, kind, principal, project?, args, state, progress
  {done?, total?, phase, message}, created/started/finished, artifacts:
  [paths/refs], error?, retries, priority, idempotency_key?}`. States:
  `queued → running → succeeded | failed | canceled`, plus `interrupted`
  (crash recovery, FR6).
- FR2. Job kinds register via packs (`register_jobs(jobs, service)` from
  `tools_<name>.py` modules) declaring: executor coroutine, cancellation
  behavior, `resumable | restartable | abandon` crash policy, and
  retryable error types. Submitting an unknown kind is a
  `validation_error` listing available kinds.
- FR3. `submit/status/cancel/list` as tools + REST + UI; listing filters by
  project, principal, state; results paginate.
- FR4. Cancellation is cooperative first (executors observe a cancel flag at
  their checkpoint boundaries) with a hard path for kernel work (the pool's
  existing kill-and-respawn); a canceled job records partial artifacts and
  leaves no orphaned state (branches, turn locks, temp files cleaned or
  explicitly handed off).
- FR5. Retries: only error types the kind declares retryable
  (`kernel_crash`, `timeout`), max-N with exponential backoff, every attempt
  recorded in the job record.
- FR6. Persistence: the queue and job records live in the server's data dir
  and survive restart — queued jobs run, `running` jobs are resumed
  (resumable kinds, from their last checkpoint), re-queued (restartable
  kinds, guarded by `idempotency_key`), or marked `interrupted` (abandon
  kinds).

**Scheduling, quotas, events**
- FR7. Fair scheduling per principal (round-robin across principals with
  per-principal concurrency caps); the scheduler reserves configurable
  headroom in the kernel pool for synchronous interactive requests.
- FR8. Quotas per principal: max concurrent jobs, max queued, CPU-seconds
  and wall-clock budgets fed by PRD-006 metering. Exceeding one fails
  submission with `quota_exceeded` naming the limit, current usage, and
  reset horizon. Pre-PRD-005, principals are the existing client identities
  (`X-Agent-Id`, `chat:<session>`, `browser`) and quotas are advisory;
  hosted deployments enforce against authenticated principals.
- FR9. Every transition publishes `job_update {job_id, kind, project,
  state, progress}` on the WebSocket channel; job submission/completion/
  cancellation are attributed in the audit trail (PRD-005).

**Fleet semantics**
- FR10. A job runs under exactly one principal + role token (PRD-005); its
  writes go to its own branch or scratch space. A job that must touch a
  shared working branch takes the per-project turn lock like any client —
  the store choke point stays the single write arbiter.
- FR11. Role scopes: `drafter` (branch, edit own branch, open proposals),
  `reviewer` (read, comment, approve), `optimizer` (studies, read-only on
  geometry). Enforcement lives in PRD-005's permission layer + PRD-002's
  author-cannot-merge rule; the job system's obligation is to *carry* the
  scoped token and refuse kinds the role doesn't permit.
- FR12. A completed fleet run leaves only durable, reviewable outputs —
  branches, proposals, reports, job records. A sweep test can assert: no
  stray turn locks, no temp worktrees, no unattributed writes.

## Agent surface

New tools: `submit_job {kind, args, project?, priority?, idempotency_key?}`
· `job_status {job_id}` · `cancel_job {job_id}` · `list_jobs {project?,
principal?, state?, limit?}` · `retry_job {job_id}` (failed jobs only).
New events: `job_update {job_id, kind, project, state, progress}`.
New error types: `quota_exceeded {limit, usage, resets_at}`,
`job_not_found`; job *failures* are recorded on the job record with the
structured error of the underlying operation, not thrown at `job_status`
callers.

## Technical approach

- **Queue core** — `agentcad/core/jobs.py`: the scheduler (asyncio task
  spawning executor coroutines), the persistence layer, quota accounting,
  and the kind registry. Persistence proposal: SQLite (WAL mode) in the
  server data dir — concurrent-safe, queryable for `list_jobs`, one file;
  the JSONL-append alternative is the design-spec fallback if SQLite's
  locking fights the test harness.
- **Compute governance stays in the pool** — executors do their kernel work
  through the same `AgentCADService`/`KernelPool` path as everything else;
  the pool remains the single compute governor, so job concurrency caps and
  interactive headroom compose with pool size (`AGENTCAD_KERNEL_POOL_SIZE`)
  rather than fighting it. Executor code must not block the event loop
  (kernel calls offload to threads exactly as the server does today).
- **Extension point** — job kinds register from existing packs; first
  movers: `check` (PRD-004), `study` (PRD-019), `generate` (PRD-018),
  `release_bundle` (PRD-015), `package_validate` (PRD-011), `docs`
  (PRD-023), long connector calls (PRD-022).
- **Tool pack** `agentcad/core/tools_jobs.py` + **route pack**
  `agentcad/server/routes_jobs.py`; events ride the existing
  EventBus→WebSocket channel; cores untouched.
- **Quota/metering seam** — counters written by PRD-006's per-worker
  metering, read by the scheduler; surfaced in `/api/health` alongside the
  existing sandbox status.
- **Frontend** — job tray module in the shell (PRD-026 placement);
  progress from `job_update` events.

Kernel untouched. Storage: a new server-level data file (jobs DB); project
manifests unchanged.

## MVP & phasing

- **MVP:** queue + SQLite persistence + submit/status/cancel/list (tools,
  REST, tray UI) + `job_update` events + per-identity concurrency caps;
  `study` and `check` as the first registered kinds; restart recovery for
  queued jobs (running → `interrupted`).
- **Phase 2:** retries with backoff, fair scheduler with interactive
  headroom, resumable checkpoints (study/CI kinds), metering-backed quotas
  (PRD-006), priorities, `retry_job`.
- **Phase 3:** fleet hardening — role-scoped tokens (PRD-005), the
  author-cannot-merge integration (PRD-002), overnight-fleet sweep
  guarantees (FR12), admin usage surface; multi-node scheduling only if
  hosted load demands it.

## Acceptance criteria

- AC1. Ten parallel study jobs from three principals saturate the kernel
  pool with fair interleaving (per-principal completion spread asserted via
  pool instrumentation), while an interactive rebuild submitted mid-run
  completes within its normal latency budget (headroom test).
- AC2. A canceled job stops cleanly: state `canceled`, partial artifacts
  recorded, and a sweep finds no orphaned branches, turn locks, or temp
  state (test).
- AC3. Kill the server with 3 queued + 1 running job; on restart the queued
  jobs execute, and the running job resumes or is marked per its kind's
  crash policy — nothing is silently lost (test).
- AC4. A scripted overnight-fleet simulation (N drafter jobs on
  `fleet/<run>/<n>` branches producing proposals) leaves only branches,
  proposals, reports, and job records — the FR12 sweep passes (integration
  test with PRD-001/002).
- AC5. A principal at its concurrency cap gets `quota_exceeded` naming the
  limit and usage; other principals' submissions are unaffected (test).
- AC6. An injected `kernel_crash` retries with backoff and succeeds on
  attempt 2, with both attempts on the job record; a `validation_error`
  failure does not retry (test).
- AC7. With PRD-005: a drafter-scoped token can submit generation jobs and
  open proposals but cannot merge its own proposal (403) or submit an
  admin-only kind (cross-PRD integration test).
- AC8. Browser session: submit a study, watch the tray go queued → running
  → done with live progress, open the report from the tray, cancel a second
  job mid-run — zero console errors.

## Risks & open questions

- **Event-loop starvation:** a badly written executor blocks every job and
  the API. Mitigation: executor contract (async + thread offload for
  blocking calls), a stall watchdog, a review checklist for new kinds.
- **Double execution after crash:** at-least-once semantics can re-run
  side-effectful work (exports, external calls). Mitigation: idempotency
  keys (FR6), per-kind crash policy; connector kinds (PRD-022) default to
  `abandon` + explicit `retry_job`.
- **Persistence choice:** SQLite locking vs. the suite's parallel
  TestClient instances. Mitigation: WAL + short transactions; benchmark in
  the design spec; JSONL fallback specified.
- **Fairness vs. simplicity:** weighted fair queuing is easy to over-build.
  Mitigation: plain per-principal round-robin + caps in MVP; revisit with
  hosted telemetry.
- **Identity spoofing pre-PRD-005:** local identities are self-declared
  headers, so quotas are honor-system until auth lands — documented;
  hosted mode requires PRD-005.
- **Open question:** whether chat turns migrate onto the job system
  (unifying the turn queue) or stay parallel — decide in the design spec
  with PRD-008's presence work in view.

## Competitive references

No CAD ships job orchestration for agents. The incumbent structure actively
prevents it: per-seat GUI licenses + COM/VBA automation (community
SolidWorks MCP servers generate VBA macros as a fallback), Onshape's
per-company API metering (market_research.md, "The desktop incumbents",
"Cloud-native CAD: Onshape"). nTop Automate proves headless engineering
compute sells — as a closed enterprise SKU ("The workflow ring"). We differ
by: open-source, compute-metered-never-seat-metered (the "Business-model
guardrails" constraint), quotas as the billing substrate, and fleet output
that is inherently reviewable because the only coordination primitive is
the proposal.
