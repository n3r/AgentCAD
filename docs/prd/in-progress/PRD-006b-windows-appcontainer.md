# PRD-006b — Windows AppContainer: the confinement half of the Windows story

- **Status:** pending
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-18
- **Origin:** carved out of [PRD-006](../completed/PRD-006-sandboxing-quotas.md)
  on 2026-08-18 by the orchestrator under the founder's `/goal` (build PRD-006
  without pausing), on the 005a / 031a letter-suffix precedent so
  folder-as-status stays truthful for both halves. **The founder can fold it
  back** into PRD-006 at review; nothing here depends on the split.
- **Depends on:** PRD-006 (the backend seam, the plan, the status contract and
  the Windows job-object quota tier all ship there)
- **Related:** PRD-005 (a hosted Windows instance is not a scenario anyone has
  asked for; this is about a *local* Windows user's confinement parity)

> **Carve-out note.** PRD-006 ships everything else: Linux confinement, both
> read postures, all quota tiers including Windows job objects, breach
> handling, metering, health, disk budgets and layered configuration. What
> moved here is exactly **FR2's confinement half** and **AC3's Windows
> clause**. PRD-006 closes with Windows confinement reporting `unsupported`,
> in `/api/health` and in the docs, rather than closing on a claim it did not
> verify.

## Problem & motivation

A part script is arbitrary Python executed in the kernel worker. After
PRD-006 that script is confined on macOS (a `sandbox-exec` seatbelt profile)
and on Linux (an in-process Landlock ruleset plus a seccomp filter), and on
Windows it is not confined at all: `sandbox_windows.py` caps what it may
*take* — a job object bounds committed memory, active processes and CPU rate —
but nothing bounds what it may *reach*. A downloaded project or an installed
package (PRD-011) runs on a Windows machine with the user's full privileges:
it can read and write anywhere the user can, and it can open sockets.

That is the same exposure the other two platforms had before PRD-006, and it
is the one platform of the three where the answer was not verifiable on the
box the work was done on. PRD-006 states it plainly rather than shipping an
unproven profile; this PRD is where it gets closed.

The scope is deliberately narrow. Confinement here buys a **local Windows
user** the parity the macOS and Linux users already have. It is not a hosting
prerequisite: the hosted image is Linux.

## Users & jobs

- **Local Windows user:** open a project or install a package from someone
  else without granting it the run of the profile directory.
- **Design agent:** unchanged — a denial must arrive as the same structured
  `script_error` with `details.denied` that the other two platforms already
  produce, so no agent learns a third dialect.
- **Ops / the reader of `/api/health`:** see `confinement.status: "active"`
  with a mechanism on all three OSes, or see the honest reason it is not.

## Goals

- G1. Windows workers run write- and network-confined with the same
  deny-by-default semantics the seatbelt and the Landlock profile already
  have: writes only inside the project roots and the worker's private temp
  dir, no network.
- G2. Anything the script forks or spawns inherits the confinement.
- G3. The reported contract is the existing one — `sandbox.report()` answers
  `active` with a mechanism, set from the worker's own report and never from
  intent — so no caller learns a Windows-shaped special case.

## Non-goals

- Re-doing the Windows **quota** tier: job objects ship with PRD-006 and are
  not revisited here.
- A hosted Windows deployment. The image is Linux; this is local parity.
- Any change to the `local`/`hosted` posture model. Windows is `local`.
- Windows Sandbox / Hyper-V isolation — heavier than the threat model needs
  and unavailable on Home editions.

## Experience

Invisible when everything is fine, exactly as on the other two platforms. A
script that opens a socket or writes outside its project fails as an ordinary
`script_error` with a traceback, a line number, `details.denied` and an Error
Doctor hint; the previous good geometry stays and the worker stays warm.
`/api/health` reports `sandbox.confinement: {"status": "active", "mechanism":
"appcontainer"}` instead of today's `{"status": "unsupported", "detail":
{"note": "AppContainer confinement is PRD-006b"}}`.

## Functional requirements

- FR1. The worker process runs inside an **AppContainer**: a package SID
  created with `CreateAppContainerProfile`, launched through
  `CreateProcess` with a `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`
  attribute list, with **no** network capabilities granted (the absence of
  `INTERNET_CLIENT` is the network denial).
- FR2. Every write root PRD-006's plan already computes — the projects dir,
  registered examples, `~/.agentcad`, the server's work root and the worker's
  private temp dir — is made reachable by granting the package SID an ACE on
  it, and nothing else is. Read access follows the `local` posture.
- FR3. The mechanism string is `appcontainer`, joined with `+` in tier order
  like every other mechanism, and `sandbox.supported()` answers `True` on
  `win32` only where the profile actually applies.
- FR4. `confinement.status` is set from the worker's **own** report (the
  `ping` handler's `sandbox` object), never from the intent to confine — the
  PRD-006 honesty rule, unchanged. An environment where the profile cannot be
  created reports `off` with the reason in `warnings`.
- FR5. `AGENTCAD_NO_SANDBOX=1` opts out of the confinement and **not** of the
  job-object quotas, as on the other two platforms.
- FR6. The private per-worker temp dir, the granted-roots computation and the
  `AGENTCAD_CONFINE` payload are reused as they are. No new seam.

## Agent surface

None. The whole point is that a Windows denial is the same `script_error` with
the same `details.denied` value an agent already handles.

## Technical approach

`agentcad/kernel/sandbox_windows.py` already owns the Windows backend, the job
object and the psapi sampler, and already returns the five-tuple
`(argv, env, confinement, quotas_report, backend)` that `sandbox.plan()`
consumes. The work is inside that one module plus the spawn path:

- `CreateAppContainerProfile` / `DeriveAppContainerSidFromAppContainerName`
  for a stable per-installation profile; `DeleteAppContainerProfile` is *not*
  called per worker (profile creation is not free and the SID is reused).
- `InitializeProcThreadAttributeList` +
  `UpdateProcThreadAttribute(PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES)`.
  CPython's `subprocess.Popen` does not expose an attribute list, so the spawn
  needs either a small `ctypes` `CreateProcessW` wrapper on this path or a
  `STARTUPINFOEX` shim — **which one is the spike's first question**, because
  the client's process handling (stdin/stdout pipes, `_kill`, the supervisor's
  handle) must not fork into two code paths.
- ACEs on the write roots via `SetNamedSecurityInfoW` with the package SID,
  added when the root is created and audited on start.
- The report the worker returns on `ping` gains the AppContainer facts, so
  FR4's honesty rule has something to be honest about.

## MVP & phasing

One slice. There is no useful half: a profile that confines writes but not the
network, or that the worker cannot confirm, is exactly the "claims `active`
from intent" failure PRD-006's Decision 8 exists to prevent.

## Acceptance criteria

- AC1. On Windows CI, the malicious battery is contained: a socket connect, a
  write outside the project roots, and a write into another worker's private
  temp dir each fail as a structured error naming the violation; the worker
  survives or respawns and the next build succeeds.
- AC2. A normal build, a STEP export and an `[fem]`-free full portability run
  are unaffected — the load-bearing negative. OCCT and CPython must not need
  anything the profile denies.
- AC3. `/api/health` reports `sandbox.confinement.status: "active"` with
  mechanism `appcontainer` on `windows-latest`, and
  `AGENTCAD_EXPECT_SANDBOX=active` is added to that CI job's environment (the
  matrix already carries the knob, empty), so a silent degradation is red.
- AC4. `AGENTCAD_NO_SANDBOX=1` reports `off` and the job-object quotas still
  apply.
- AC5. PRD-006's AC3 is thereby closed: all three OSes report a confinement
  mechanism, and PRD-006 moves to `completed/` with no Windows asterisk.

## Risks & open questions

- **AppContainer + CPython + OCCT is the least-trodden path of the three**
  (PRD-006's own words). The OCCT wheels load a large set of DLLs and probe
  the filesystem at import; a profile that denies one of those reads is an
  import failure, not a graceful denial. Mitigation: build the profile from a
  measured import trace, not from a guess, and keep AC2 as the gate.
- **No local Windows box.** Every iteration is a Windows-CI round trip, which
  is the practical reason this is carved out rather than rushed. Mitigation:
  the first commit should be a probe workflow that prints what the profile
  denies, not an implementation.
- **`subprocess` does not expose `STARTUPINFOEX`.** A hand-rolled
  `CreateProcessW` must reproduce pipe inheritance and handle cleanup exactly,
  or the kernel protocol breaks in ways that look like flaky tests.
- **Profile lifetime.** A per-worker profile leaks registry state if creation
  and deletion are not symmetric; a per-installation profile is simpler but
  shared between concurrent instances. Decide with evidence.
- **Windows Server / older builds.** `CreateAppContainerProfile` needs
  Windows 8+; report `unsupported` with the reason below that, never a
  silent `off`.

## Competitive references

Unchanged from PRD-006: no CAD competitor sandboxes user geometry code,
because none executes user code at all — Onshape's FeatureScript is jailed by
language design and correspondingly powerless, and desktop incumbents run
macros with full user privilege and trust the desktop boundary
(`market_research.md`, "Cloud-native CAD: Onshape"). Keeping full Python and
confining it at the OS boundary is the trade; this PRD is the third platform
of that trade.
