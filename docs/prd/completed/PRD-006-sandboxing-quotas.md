# PRD-006 — Cross-platform sandboxing and resource quotas

- **Status:** completed — merged to main in PR #22 (`69fc968`, 2026-08-19).
  Changelogs `0230`–`0238` (renumbered at merge from `0213`–`0220`; `0238` is
  the merge with PRD-007/031a). AC1–AC8 verified as recorded below, with one
  named carve-out: **AC3's Windows clause** — that clause and G2/FR2's Windows
  confinement half are
  [PRD-006b](../pending/PRD-006b-windows-appcontainer.md), and Windows reports
  `unsupported` here rather than closing on an unverified claim. The three-OS
  CI matrix that AC8 asks for ran green on the PR: ubuntu (Landlock **ABI 7**
  live on x86_64, `AGENTCAD_EXPECT_SANDBOX=active`), windows (the job-object
  tier, sampling the interpreter behind the venv launcher), macOS (the real
  seatbelt + supervisor). Two CI-only findings were fixed on the way: the
  Windows venv `python.exe` is a launcher, so the supervisor now samples the
  job's processes; and one Linux battery case assumed the image's `/app`.
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026) — promoted from v3 residual to cloud prerequisite
- **Depends on:** — (none hard; must land before PRD-005 hosts untrusted tenants)
- **Related:** PRD-005 (tenant isolation + metering consumer), PRD-006b (the Windows confinement carve-out), PRD-007 (customizer rebuild rate limits), PRD-011 (third-party package scripts run confined), PRD-020 (fleet quotas ride this metering), PRD-004 (headless CI runs sandboxed)
- **Design:** [2026-08-18 sandboxing & quotas](../../superpowers/specs/2026-08-18-sandboxing-quotas-design.md)
  · **Plan:** [five slices](../../superpowers/plans/2026-08-18-sandboxing-quotas.md)

> **What shipped, and what it is honestly worth.** The Linux worker confines
> **itself** — a Landlock ruleset and a seccomp filter applied through
> `ctypes` before `import build123d`, needing no capability, no `bwrap` binary
> and no `--privileged` (bubblewrap was ruled out with evidence: it is absent
> from the image and `unshare -Ur` is denied under Docker's default seccomp
> profile). Two named read postures ship: `local` (global read, the v1 stance)
> and `hosted` (an allow-list excluding the state dir and leaving **nothing**
> under the server user's home reachable — the config dir was dropped from the
> write roots, and a write root is readable by construction), so a
> hosted member's script can no longer read the session signing key. Quotas
> ship as honest **tiers** — a delegated cgroup v2 subtree, POSIX rlimits
> where they are real, Windows job objects, and a parent-side RSS supervisor
> everywhere — with the tier in force named in `/api/health` rather than a
> mechanism promised. Every response carries its `usage`; a meter rolls it up
> per project and per identity behind `/api/health` and `get_usage`; disk
> budgets refuse a write before the worker makes it.
>
> **One deliberate deviation from FR8.** FR8 asks for a *pids* kill reported
> as `kernel_crash` with `details.reason: "pids_cap"`. The build does better
> and therefore differently: both process-count mechanisms (`RLIMIT_NPROC`
> and the cgroup's `pids.max`) make the script's own `fork()` return `EAGAIN`,
> so the breach arrives as an ordinary `script_error` with
> `details.denied: "process_count"`, a traceback and a line number — **and the
> warm worker survives**, which a kill would not have allowed. `pids_cap`
> stays reserved vocabulary in the docs so an agent's handler can be written
> once; nothing emits it. The same is true of `cpu_cap`: the CPU tiers
> throttle rather than kill, and the branch that would emit it needs a
> `SIGXCPU` from an `RLIMIT_CPU` AgentCAD never sets (it is
> lifetime-cumulative — the per-request wall-clock timeout is the CPU
> backstop). `memory_cap` is the one reason the shipped tiers produce.
>
> **The residual, stated rather than implied:** a member's script still runs
> as the server user and the **whole projects tree** is readable and writable
> to it, across every project on the instance. Per-project isolation is
> PRD-005. So "an account is a shell" is no longer literally true on Linux —
> no network, no writes outside the granted roots, no reads of the state dir
> and nothing under the server user's home at all, capped
> memory/pids/CPU — but accounts
> remain for people you trust and registration stays closed.
>
> **Acceptance, per platform.** Everything below was run and is cited in
> changelogs `0236`–`0238`, and the CI matrix ran green on PR #22 (run
> 2026-08-19: ubuntu 834 passed, windows 799 passed, macOS PR suite green).
> AC1 (the malicious battery contained on Linux) —
> `tests/test_sandbox_linux.py`, run in `agentcad:local` (112 passed,
> 2 skipped) **and** on the ubuntu job with `AGENTCAD_EXPECT_SANDBOX=active`
> (Landlock ABI 7, `lsm=…landlock…`), so a degradation there is red rather
> than skipped. AC2 (macOS seatbelt regressions still pass) —
> `tests/test_sandbox.py`. AC3 — `active` measured on macOS and Linux;
> **Windows reports `unsupported`**, which is this PRD's honest answer and
> 006b's subject. AC4/AC5/AC6 — `tests/test_supervisor.py`, real workers and
> real allocations. AC7 — `tests/test_usage.py`. AC8 — the
> `AGENTCAD_NO_SANDBOX=1` opt-out and the unconfinable-environment case are
> in `tests/test_prd006_acceptance.py`; the "full suite green on the
> **three-OS** matrix" half closed with the PR's green run. Also deferred and
> named: the
> `systemd-run --scope` tier (unverified), the per-principal audit log
> (PRD-005), a narrowed macOS read posture, and FEM under confinement.

## Problem & motivation

Part scripts are arbitrary Python executed in the kernel worker. Today only
macOS confines them: `agentcad/kernel/sandbox.py` wraps the worker argv in a
deny-by-default seatbelt profile — global read, writes only inside project
roots + the temp dir, no network, kill-and-respawn on the existing
per-request timeout. Linux and Windows run the same untrusted code with full
user privileges, and **no** platform bounds CPU, memory, processes, or disk:
a fork bomb or a memory balloon takes the host down, and an expensive loop
burns 120 s of CPU that nobody accounts for.

This was an acceptable v3 posture for a local single-user tool
(market_research.md, "Where AgentCAD stands today" lists "macOS-sandboxed
script execution" as the current state). It is disqualifying for everything
v4 builds: a multi-tenant cloud (PRD-005) executes untrusted Python from
strangers and their agents — and Linux, the platform the cloud actually runs
on, is precisely the one with no confinement. Share-link customizers
(PRD-007) invite anonymous rebuild load that needs rate and resource caps.
Package installs (PRD-011) put third-party scripts on local machines too.
And the business guardrails demand compute-metered hosting, "never
per-seat, never metered APIs" (market_research.md, "Business-model
guardrails (the Ondsel constraint)") — compute-metered requires a meter.
That meter is the same substrate PRD-020's fleet quotas consume. The
three-OS CI matrix already exists, so confinement claims are provable per
platform, not aspirational.

## Users & jobs

- **Cloud operator (PRD-005):** run strangers' scripts without host
  compromise; keep one tenant from starving or crashing the rest; bill on
  measured compute.
- **Self-hoster / IT:** the same guarantees on their own infra; a provable
  no-network posture for air-gapped deployments.
- **Local user:** protection from a malicious or buggy downloaded project or
  package (PRD-011) — today "open this example" means "trust this code".
- **Design agent:** breaches come back as the structured errors it already
  handles (`timeout`, `kernel_crash`) so the existing fix loop needs no new
  behavior.
- **Ops / automation:** `/api/health` and usage data for capacity, quota,
  and (later) billing decisions.

## Goals

- G1. Linux confinement with the same deny-by-default semantics as the
  seatbelt profile: write only project roots + temp, no network, contained
  failure on violation.
- G2. Windows confinement (AppContainer) so the three-OS support matrix has
  one documented security story.
- G3. Resource governance per worker: memory cap, CPU quota, process count,
  wall-clock, disk budget — enforced by the OS, with kill-and-respawn on
  breach.
- G4. Metering: per-request CPU-seconds, peak RSS, and wall time, aggregated
  per project and (under PRD-005) per tenant/principal, surfaced through
  `/api/health` and the audit log — the substrate for PRD-020 quotas and
  hosted pricing.
- G5. Zero contract change for agents: breaches are the existing structured
  errors with richer `details`; a green build behaves identically sandboxed
  or not.

## Non-goals

- MicroVM/gVisor-per-request isolation — heavier than the threat model needs
  while worker-level Landlock/seccomp + cgroups holds; kept as a named
  fallback if evidence demands it.
- Sandboxing the server process — it executes no user code and stays behind
  the host guard / auth layer.
- Network egress allowlists for manufacturing connectors — PRD-022's API
  calls run in the server process, never in the worker.
- Python-level import/audit hooks as a security boundary — bypassable by
  construction; the boundary is the OS.

## Experience

**Human path.** Invisible when everything is fine. `/api/health` shows
`sandbox: active` with the mechanism per platform. A script that tries the
forbidden fails like any build failure: the error panel shows a structured
error naming the denial ("network access is blocked in the kernel sandbox"),
previous good geometry stays.

**Agent path.** `update_part_script` with a `socket.connect` in it returns
the normal script-failure payload (the denial raises inside the script —
traceback, line, hint). A memory balloon returns
`{"error": {"type": "kernel_crash", "details": {"reason": "memory_cap",
"usage": {...}}}}`; a runaway loop returns `timeout` exactly as today. The
agent's existing read-error → fix → rebuild loop handles all of it; the new
`details.reason` tells it whether to fix the script or shrink the job.

**Operator path.** Compose/instance config sets default caps; per-tenant
overrides come with PRD-005. Health and usage endpoints feed dashboards; the
audit log carries per-principal usage lines.

## Functional requirements

**Confinement**
- FR1. Linux workers run confined: filesystem writes restricted to project
  roots + the worker temp dir; global read (the accepted v1 posture,
  matching the seatbelt profile's documented stance); no network (socket
  family denial). Mechanism: Landlock (filesystem) + seccomp (network/
  syscall filter) applied in-process before build123d imports, or a
  bubblewrap argv-wrap where available — the chosen mechanism reports itself
  in `/api/health`.
- FR2. Windows workers run under AppContainer (write + network confinement)
  with a job object for resource caps; phased after Linux (see MVP), same
  reported semantics.
- FR3. One parity contract across the three OSes, documented in one place:
  write only project roots + temp; read anywhere (local mode); no network;
  exec/fork allowed but inherited-confined. `sandbox.status()` answers
  `active | off | unsupported` truthfully per platform;
  `AGENTCAD_NO_SANDBOX=1` opt-out preserved everywhere.
- FR4. Confinement wraps the worker process through the existing seam
  (`sandbox.wrap_argv(argv, writable_dirs)` on the `KernelClient` spawn
  path) or a worker preamble; anything the script forks/spawns inherits it.
- FR5. Cloud posture narrows reads: under PRD-005 multi-tenancy the
  global-read allowance is replaced by tenant root + interpreter/
  site-packages read-only — cross-tenant reads must be impossible. Local
  parity posture and cloud posture are both explicit, named profiles.

**Quotas**
- FR6. Per-worker OS-enforced caps, configurable with sane defaults: memory
  (cgroup v2 `memory.max` on Linux; job object on Windows; rlimit tier on
  macOS), CPU quota (`cpu.max`), process count (`pids.max` — the fork-bomb
  stop), wall-clock per request (the existing 120 s build / 300 s handler
  timeouts remain that layer).
- FR7. Disk budget per project (per tenant under PRD-005) covering
  `.cache/`, `exports/`, `imports/`; an exceeded budget fails the write with
  a structured error naming the budget and never corrupts state (atomic
  writes preserved).
- FR8. Breach handling rides the existing kill-and-respawn path: OOM/pids
  kill → `kernel_crash` with `details.reason` (`memory_cap` | `pids_cap`);
  wall-clock → `timeout`; in every case previous good geometry is kept, the
  worker respawns warm, and the next request succeeds.
- FR9. Blast-radius isolation: a breach on one pool worker never disturbs
  in-flight requests on sibling workers.

**Metering**
- FR10. Every JSON-RPC response carries the request's resource usage
  (user+sys CPU seconds, peak RSS, wall ms); the service aggregates per
  project and per client identity (`client_id_var`; PRD-005 principals when
  present).
- FR11. `/api/health` gains `sandbox: {status, mechanism, quotas}` and
  `usage` roll-ups; under PRD-005 the audit log records per-principal usage.
  A local instance reports the same shape scoped to `local`.
- FR12. Quota configuration is layered like materials: built-in defaults <
  instance config < per-tenant overrides (PRD-005 admin surface).
- FR13. Degradation is honest: an environment that cannot confine (old
  kernel without Landlock, missing bwrap, no cgroup delegation) reports
  `off` with a health warning — never a silent claim of `active`. Suite
  green on all three CI OSes with sandbox active where supported.

## Agent surface

- No new tools in the MVP — deliberately: the contract *is* that breaches
  are existing structured errors.
- Changed: `kernel_crash` and `timeout` errors gain `details.reason` and
  `details.usage {cpu_s, peak_rss_mb, wall_ms}` so an agent can distinguish
  "my script ballooned" from "the kernel died on its own".
- New (phase 2): `get_usage {project?, since?}` → usage roll-ups per
  project/principal — the budget-awareness primitive PRD-020 fleets and
  PRD-005 admins consume.
- `/api/health` payload extension as FR11 (route change, additive).

## Technical approach

- **Platform backends behind the existing facade:** `kernel/sandbox.py`
  becomes a dispatcher over `sandbox_macos` (the current seatbelt profile,
  unchanged), `sandbox_linux.py`, `sandbox_windows.py`, preserving the two
  public seams used today: `wrap_argv(argv, writable_dirs)` (spawn path in
  `client.py`) and `status()` (health). `KernelClient`/`KernelPool` already
  plumb `writable_dirs`.
- **Linux mechanism (design-spec spike, decide with evidence):**
  (a) bubblewrap argv-wrap — closest analog to `sandbox-exec`, needs the
  `bwrap` binary + user namespaces; (b) in-process self-confinement — a
  worker preamble applies a Landlock ruleset + seccomp filter via ctypes
  before importing anything heavy; no external binary, works inside the
  compose image. Preference: (b) for dependency freedom; the seccomp filter
  stays narrow (network-family deny + a small denylist) so Python/OCCT
  syscall drift can't cause false denials, with Landlock carrying the
  filesystem policy.
- **cgroups:** the spawner places each worker in a transient cgroup scope
  (direct cgroupfs when delegated, `systemd-run --scope` where available;
  plain rlimits as documented fallback). Pool respawn logic unchanged.
- **Windows:** AppContainer profile via CreateProcess attribute lists +
  a job object for memory/pids/CPU — phase 3; `status()` reports honestly
  until then.
- **Metering:** additive envelope field in `kernel/protocol.py` responses
  (worker reads its own rusage per request); `client.py` surfaces it; a
  small usage accumulator in the service tags by identity and feeds health
  + audit. Handler packs untouched.
- **Disk budgets:** checked in `ProjectStore` write paths (beside
  `write_guard`) plus a cache janitor for `.cache/` size.
- Docs: the security section of `docs/architecture.md` and AGENTS.md's
  trust posture get the per-OS contract table.

## MVP & phasing

- **MVP (Linux first — it's what the cloud runs on):** Landlock/seccomp (or
  bwrap) confinement with seatbelt-parity semantics; cgroup memory/CPU/pids
  caps in the pool spawn path; breach → kill-and-respawn → structured
  `timeout`/`kernel_crash` with `details.reason`; health reporting; the
  malicious-script battery in Linux CI.
- **Phase 2:** per-request usage metering + `/api/health` usage +
  `get_usage`; disk budgets; layered quota config (per-tenant with
  PRD-005); the narrowed cloud read posture (FR5).
- **Phase 3:** Windows AppContainer + job objects; scheduling integration
  (PRD-005 fairness weights fed by usage) and PRD-020 fleet budget
  enforcement.

## Acceptance criteria

- AC1. The malicious battery is contained on Linux CI: a network attempt, a
  write outside project roots, a fork bomb, and a memory balloon each fail
  as a structured error naming the violation; the worker respawns and the
  next build succeeds (tests).
- AC2. The same battery stays contained on macOS (seatbelt regression tests
  keep passing).
- AC3. `/api/health` reports `sandbox: active` with the mechanism on all
  three OSes in CI (Windows lands with phase 3; this criterion closes the
  PRD).
- AC4. A script allocating 4 GiB on a 2 GiB-capped worker returns
  `kernel_crash` with `details.reason: "memory_cap"` and `details.usage`;
  the part's previous geometry is intact (test).
- AC5. Wall-clock behavior unchanged: the existing timeout path still fires
  and now carries `details.usage` (test).
- AC6. Killing/saturating one pool worker does not disturb a concurrent
  build on a sibling worker (test).
- AC7. Two projects' builds produce distinguishable CPU/RSS roll-ups in the
  usage surface; under PRD-005, per-tenant attribution is correct (test).
- AC8. Full suite green on the three-OS CI matrix with sandbox active where
  supported; `AGENTCAD_NO_SANDBOX=1` still opts out; an unconfinable
  environment reports `off` + warning, never `active` (tests).

## Risks & open questions

- **Landlock/seccomp availability in containers:** Landlock needs kernel ≥
  5.13 and interacts with Docker's default seccomp profile; the compose
  image must not need `--privileged`. Spike first; bwrap is the fallback;
  document minimum host requirements.
- **cgroup delegation inside containers** (nested cgroups, systemd absent):
  support both direct-cgroupfs and systemd-run paths; document what the
  compose deployment guarantees.
- **RLIMIT_AS is a crude memory cap** (OCCT's mmap patterns can trip it
  spuriously) — it is the macOS/fallback tier only; cgroup `memory.max` is
  the real mechanism; measure OCCT headroom before picking defaults.
- **Global-read leaks in cloud mode:** the local v1 posture (read anywhere)
  is fine on your own machine and unacceptable cross-tenant — FR5's
  narrowed profile is mandatory before PRD-005 onboards strangers; this is
  a sequencing constraint, stated in both PRDs.
- **Metering overhead** must stay negligible (<1% per request) — rusage
  reads are cheap, but the accumulator must not serialize the pool.
- **Windows AppContainer + Python + OCCT** is the least-trodden path of the
  three; timebox a spike and honestly report `unsupported` until it holds.

## Competitive references

No CAD competitor sandboxes user geometry code because none executes user
code at all: Onshape's FeatureScript is jailed by language design — no
network, no system access, and correspondingly no power (market_research.md,
"Cloud-native CAD: Onshape", extensibility "jailed"); desktop incumbents run
macros with full user privilege and simply trust the desktop boundary. We
keep full Python power and confine it at the OS boundary instead — the same
trade that made model-as-code possible. The metering half has a business
edge: compute-metered hosting and agent fleets ("Business-model guardrails",
"Where AgentCAD wins" point 4) are only honest with a per-request meter;
Onshape's per-company API metering is the own-goal we refuse to repeat.
