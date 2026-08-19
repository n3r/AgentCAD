# PRD-006 — Cross-platform sandboxing and resource quotas: design

- **Date:** 2026-08-18
- **PRD:** [PRD-006](../../prd/completed/PRD-006-sandboxing-quotas.md)
- **Builds on (completed):** PRD-005a (hosted mode, `AGENTCAD_MODE`, the
  state dir, `docs/deployment.md`'s trust statement), the v3 macOS seatbelt
  (`agentcad/kernel/sandbox.py`, `tests/test_sandbox.py`), the kernel
  client/pool (`kernel/client.py`, `kernel/pool.py`), the line-JSON protocol
  (`kernel/protocol.py`), the Error Doctor (`kernel/error_doctor.py`).
- **Evidence:** three spikes run on 2026-08-18 against the real worker —
  Linux confinement and Linux quotas inside the shipped compose image
  (`agentcad:local`, Docker 29, linuxkit 6.12, default seccomp profile, uid
  10001), macOS quotas on Darwin 25.6. Their reports are quoted inline where a
  decision rests on a number; the spike code is throwaway and is not shipped.
- **Ruling ledger:** every decision below marked **[ruling]** was taken by the
  orchestrator under the founder's `/goal` (build PRD-006 without pausing);
  each is stated with its reason so the founder can overturn it in review.

## The one-paragraph version

Part scripts are arbitrary Python inside the kernel worker, and today only
macOS confines them. This design makes confinement and quotas a **platform
backend behind the existing `kernel/sandbox.py` seam** and adds one **honest
status contract** for all three OSes. On Linux the worker confines *itself*
before importing OCCT — Landlock (filesystem) + seccomp (network, signals to
others) applied via `ctypes` in a preamble, no external binary, verified
inside the shipped image at 0.3 ms — with a **private per-worker temp dir**
so scripts can't reach each other through `/tmp`. Quotas are **tiers, not a
mechanism**: a cgroup v2 subtree when the operator delegates one, POSIX
rlimits where they are real (they are *not* real for memory on macOS — the
spike proved `setrlimit(RLIMIT_AS)` is `EINVAL` on Darwin), Windows job
objects, and everywhere a **parent-side supervisor** that samples the worker's
RSS and kills on breach — the only memory cap macOS has and the one that
lets the client *say why* a worker died. Every worker response now carries
its **usage** (CPU ms, wall ms, per-request peak RSS), aggregated per project
and per identity by a small meter the service owns, surfaced through
`/api/health` and a `get_usage` tool. Breaches ride the existing error
contract with `details.reason`; a green build behaves identically. In hosted
mode the Linux read posture is **narrowed** to an allow-list so a script can
no longer read the session secret — the first honest weakening of "an account
is a shell". Windows gets quotas (job objects) and reports confinement
`unsupported`; AppContainer is carved out (Decision 12).

## Decision 1 — Linux confinement is in-process Landlock + seccomp **[ruling]**

The PRD offered two mechanisms and asked for evidence. The spike settles it:

- **bwrap is not viable in the compose posture.** It is absent from the image
  and could not work anyway: `unshare -Ur` is denied under Docker's default
  seccomp profile (`unshare: Operation not permitted`), so bubblewrap would
  need `--privileged` or `--cap-add SYS_ADMIN` — exactly the capability the
  Dockerfile refuses to hand out.
- **In-process Landlock + seccomp works as shipped.** In `agentcad:local`,
  uid 10001, default seccomp: `landlock_create_ruleset(NULL,0,VERSION) → 6`
  (ABI 6), the ruleset applies in 0.3 ms, `seccomp(2)` with `TSYNC`
  installs a 14-instruction filter, and **`import build123d` + a real `Box`
  build succeed after restriction**. Writes outside the roots → `EACCES`
  (root too — Landlock beats DAC), reads anywhere → OK, forked and exec'd
  children inherit both. Import time is unchanged within noise.

**Mechanism.** `agentcad/kernel/_confine.py` (OCP-free, ctypes only):

- `landlock_apply(read_roots, write_roots, extra_files)`: probe the ABI;
  `handled_access_fs = ABI_MASK[min(abi, 6)]` (never a hard-coded mask —
  the spike found `open(..., "w")` needs `LANDLOCK_ACCESS_FS_TRUNCATE`, bit
  14, ABI ≥ 3, or every truncating open is a false denial); one
  `path_beneath` rule per read root granting `EXECUTE|READ_FILE|READ_DIR`,
  one per write root granting every handled bit, one *file* rule each for
  `/dev/null` and `/proc/self/clear_refs` (both need explicit rules — the
  `/` read grant does not cover writes to pseudo-fs; a file rule is the
  tightest option and worked). `create_ruleset` is called with attr size 8
  (accepted by every ABI). Then `prctl(PR_SET_NO_NEW_PRIVS, 1)` and
  `landlock_restrict_self`.
- `seccomp_apply(server_pid)`: BPF, default `RET_ALLOW`, `RET_KILL_PROCESS`
  on an unexpected `AUDIT_ARCH` and on x32 (`nr ≥ 0x40000000`); `RET_ERRNO
  (EPERM)` for `socket`/`socketpair` with any domain **except `AF_UNIX`**
  (the spike verified denying every other family breaks nothing — CPython,
  numpy, OCP, build123d, and `socketpair()` for `multiprocessing` all pass;
  `urlopen` fails in 73–93 ms rather than hanging; DNS dies at the socket
  call). Also `RET_ERRNO(EPERM)` for `kill`/`tkill`/`tgkill`/
  `rt_sigqueueinfo`/`rt_tgsigqueueinfo` when the pid argument is `≤ 0` (a
  broadcast `kill(-1, 9)` would take the whole uid down, server included) or
  equals `server_pid`. **The negative test is an unsigned `JGE 0x80000000` on
  the LOW word of `args[0]`** — corrected during the build: the high word of an
  `int` syscall argument is unspecified on both arches (on arm64 `mov w0, #-1`
  zeroes the top half, so a negative `pid_t` arrives zero-extended and a
  sign-extension test on the high word never fires — `os.kill(-1, SIGKILL)`
  escaped the filter in the shipped image, measured `ESRCH` rather than
  `EPERM`). The low word is exactly what the kernel truncates the argument to
  (`SYSCALL_DEFINE2(kill, pid_t, ...)`), and `tests/test_confine_unit.py` pins
  both arches' encodings by interpreting the program's bytes. Also for
  `ptrace`, `process_vm_readv/writev`,
  `pidfd_open` and `io_uring_setup`/`io_uring_enter`/`io_uring_register`
  unconditionally (io_uring was added in the slice-2 review: a ring entry can
  ask the kernel to open and use a socket, and the only syscall the filter
  would ever see is `io_uring_enter`, so the AF_UNIX rule is decorative
  without it). Signals to self and to a script's own
  children stay allowed. Installed via `seccomp(SECCOMP_SET_MODE_FILTER=1,
  TSYNC)` — the operation constant is **1**; passing `2` returns
  `EOPNOTSUPP` (it is `SECCOMP_GET_ACTION_AVAIL`, the spike hit it) — with
  the `prctl(PR_SET_SECCOMP, 2, …)` fallback (calling thread only).
  Syscall numbers and `AUDIT_ARCH_*` are tabled for `x86_64` and `aarch64`;
  any other machine reports confinement `unsupported`.
- Order: rlimits (`setrlimit` from the payload, hard = soft so a script
  cannot raise them back) → Landlock → seccomp; all in-process, before
  `import build123d`, no `preexec_fn` anywhere. On macOS the same preamble
  runs inside the seatbelt (rlimits are not a seatbelt-governed operation);
  the plan's first macOS task verifies it and the ping report proves it.

**Where it runs.** `worker.py` gains two lines at the very top, before
`import build123d`: `from ._preamble import apply_from_env;
apply_from_env()`. `_preamble.apply_from_env()` reads `AGENTCAD_CONFINE` (a
JSON blob the client puts in the child's environment); with the variable
absent it is a no-op, so importing `worker` in-process (tests do) changes
nothing. It reports what it applied on **stderr** as one line and, more
importantly, the `ping` handler's result gains
`sandbox: {landlock_abi, seccomp, rlimits, posture}` so the **client learns
the live state from the process it confined** (Decision 8, honesty). The
frozen bundle's `agentcad worker` subcommand runs `worker.main()`, so the
same preamble covers it.

**Private temp dir.** The spike found a cross-project leak: granting
`tempfile.gettempdir()` wholesale mirrors the seatbelt, but Linux `/tmp` is
shared, so a script could write into another worker's scratch. The client
therefore creates a **per-worker private temp dir** (`mkdtemp(prefix=
"agentcad-worker-")` under the system temp), exports
`TMPDIR`/`TEMP`/`TMP`/`XDG_CACHE_HOME`/`HOME` pointing at it (the `HOME`
override also silences ezdxf's `~/.cache` warning), grants **only that dir**
(never bare `/tmp`), and removes it on kill/respawn/stop. macOS gets the
same treatment for parity (the seatbelt profile grants the private dir
instead of `$TMPDIR`; `tempfile` honors `TMPDIR`, and the v3 profile
minimization already showed nothing needs the shared temp).

**Requirements** (documented in `docs/deployment.md`): Landlock ABI ≥ 1
(kernel 5.13) enables the write-root model, **ABI ≥ 3 (kernel 6.2) is the
practical floor** because of `TRUNCATE`; below it the client reports
confinement `off` with a warning (Decision 8) rather than shipping a profile
that false-denies. Landlock must be in the boot-time `lsm=` list — Ubuntu
≥ 22.04 and the compose kernels are; the CI probe step (Decision 13) prints
`uname -r`, `/sys/kernel/security/lsm` and the ABI. Docker Desktop's
`fakeowner` virtiofs bind mounts are **not** Landlock-coherent (grants have
no effect, even reads fail) — overlayfs/ext4/tmpfs are correct — so the
Linux tests run in the image against `/tmp` or a named volume, never a
macOS bind mount; this is a dev-box artifact, not a deployment concern.

## Decision 2 — Two read postures: `local` (global read) and `hosted` (allow-list) **[ruling]**

FR5 puts the narrowed cloud posture "under PRD-005 multi-tenancy". This
design ships it now, on Linux, keyed on `AGENTCAD_MODE=hosted`, because
hosted mode already exists (005a) and the trust statement cannot honestly
improve without it: with global read, a member's script reads
`<state-dir>/secret.key` and the auth store — the same uid runs the server
and the worker — and forges any session. Both postures are explicit, named
profiles (FR3/FR5):

| posture | reads | writes | where |
|---|---|---|---|
| `local` | anywhere (the v1 stance, unchanged) | project roots + private temp | macOS seatbelt (as today), Linux Landlock with a `/` read rule, local mode |
| `hosted` | allow-list: `/usr`, `/lib`, `/lib64`, `/lib32`, `/bin`, `/sbin`, `/etc`, `/opt`, `/proc`, `/dev`, `/sys`, `sys.prefix`, `sys.base_prefix`, `resource_root()` (the app tree), the projects dir, the private temp; **not** `AGENTCAD_STATE_DIR`, **not** `HOME`, not other users' homes | project roots + private temp | Linux Landlock, `AGENTCAD_MODE=hosted` |

macOS keeps `local` only (hosted mode is the Linux image; a narrowed
seatbelt profile would need its own minimization pass and is out of scope —
noted in the PRD's residuals). The posture is part of the `ping`
report and of `/api/health`.

## Decision 3 — Quotas are tiers with an honest name, not one mechanism **[ruling]**

The spikes measured what each OS can actually enforce. The design names the
tier in effect and never claims more:

| tier | memory | CPU | process count | breach → parent | `details.reason` from |
|---|---|---|---|---|---|
| **cgroup v2** (Linux, delegated subtree — Decision 4) | `memory.max` **+ `memory.swap.max=0`** (load-bearing: with swap at `max` a 400 MB alloc under a 200 MB cap "swapped instead of dying") — kernel OOM-kills | `cpu.max` throttles, never kills (`nr_throttled`) | `pids.max` → `fork()` EAGAIN | `returncode == -9` + EOF; **`memory.events oom_kill` incremented** ⇒ `memory_cap`; `pids.events max` ⇒ recorded | the events files, read **before** respawn |
| **rlimit** (Linux; macOS partially) | Linux `RLIMIT_AS` — allocation *fails*, a Python `MemoryError` with a line number and the warm worker survives ("the single best property of the rlimit tier"); **macOS: none** — `RLIMIT_AS`/`DATA`/`RSS` are `EINVAL` on Darwin | not used by default (`RLIMIT_CPU` is lifetime-cumulative and, on Darwin, a script with a `SIGXCPU` handler ran 100 s past the hard limit — the wall-clock timeout stays the backstop) | `RLIMIT_NPROC` = live uid count at spawn + headroom (per-uid, counts threads; a fixed 32 killed the worker at import) → `BlockingIOError` in-script, worker alive | `MemoryError` ⇒ `memory_cap`; `EAGAIN` ⇒ `pids_cap` (script errors, classified — Decision 9) | the exception class |
| **supervisor** (all OSes) | parent samples RSS at 0.25 s (Linux `/proc/<pid>/statm` via a kept-open fd, 0.5 µs; macOS `proc_pidinfo(PROC_PIDTASKINFO)`, 1.26 µs, `pti_resident_size` at offset 8 of the 96-byte struct; Windows `GetProcessMemoryInfo`) and kills on breach; the kill lags one interval and RSS grows at ~4 GB/s, so the overshoot is a few hundred MB — the cap must sit below the host ceiling by `interval × alloc rate` | wall clock only (existing timeouts) | — | supervisor sets its own flag **before** `proc.kill()`, then rewrites `kernel_crash` ⇒ `memory_cap` with `observed_rss_mb` | the flag |
| **job object** (Windows) | `JOB_OBJECT_LIMIT_PROCESS_MEMORY` — commit limit, allocation fails ⇒ `MemoryError` | `JobObjectCpuRateControlInformation` hard cap (throttle) | `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` → `CreateProcess` fails | `MemoryError` ⇒ `memory_cap` (script error) | the exception class |

`quotas.mechanism` in health names the tier list in effect, e.g.
`"cgroup+supervisor"`, `"rlimit+supervisor"`, `"supervisor"`,
`"job_object+supervisor"`.

**Defaults** (`kernel/quotas.py`, from the measured floors — a warm worker
is 451–482 MB RSS, 499 MB after a Box build + STEP export, VmSize 1.3–1.9 GiB;
`RLIMIT_AS` at 1.25 GiB fails at import, 1.5 GiB is the verified minimum):

| knob | default | applies to |
|---|---|---|
| `memory_mb` | **2048** | cgroup `memory.max`, supervisor cap, job-object commit limit |
| `address_space_mb` | **`3 × memory_mb`** (6144) | Linux `RLIMIT_AS` only — deliberately loose: it exists to turn a runaway *virtual* reservation into a recoverable `MemoryError`, not to be the cap (the PRD's own risk note calls it crude) |
| `pids` | **128** | cgroup `pids.max`, job-object active-process limit |
| `pids_headroom` | **64** | `RLIMIT_NPROC` = live uid process count at spawn + headroom (Linux, macOS) |
| `cpu_percent` | **400** | cgroup `cpu.max`, job-object CPU rate; unset means no CPU quota (macOS always) |
| `sample_interval_s` | **0.25** | supervisor |
| `disk_mb` | **2048** per project | disk budget (Decision 10) |

Layering (FR12): built-in defaults < instance config (`~/.agentcad/config.json`
`{"quotas": {...}}`, and `AGENTCAD_QUOTA_<KNOB>` env, env wins) < per-tenant
overrides (PRD-005, not built; the resolver takes an `overrides` dict so 005
plugs in without a signature change). `0` disables a knob.

## Decision 4 — cgroup placement is opt-in by delegation, never by capability **[ruling]**

The compose image mounts `/sys/fs/cgroup` read-only and root-owned; the
spike matrix shows only two ways to a writable subtree — `--cap-add
SYS_ADMIN` (a near-root capability; **rejected**) or a **host-delegated
subtree** bind-mounted in with `--cgroup-parent` (Model 2: no capabilities,
works as uid 10001, verified end-to-end: `pids.max=16` stopped a fork loop
after 15 children, `memory.max=200M` + `swap.max=0` SIGKILLed a 400 MB
allocator with `oom_kill 1`). So:

- `sandbox_linux.cgroup_probe()` looks, in order, at (1)
  `AGENTCAD_CGROUP_DIR` — an operator-delegated cgroup v2 directory (Model
  2's `/cg`); (2) the process's own cgroup (`/proc/self/cgroup` → path under
  `/sys/fs/cgroup`) when its directory **and** its `cgroup.subtree_control`
  are writable and `cgroup.controllers` lists `memory pids` (the systemd
  `Delegate=yes` shape) — in that case the server first creates a leaf
  `<own>/server`, moves its own pids there (the no-internal-process rule),
  then enables `+memory +pids +cpu`. Every step is probed and any failure
  falls back to the rlimit + supervisor tier with a health warning; nothing
  is assumed.
- Per worker: `mkdir <root>/worker-<n>`, write `memory.max`,
  `memory.swap.max=0`, `pids.max`, `cpu.max`; **the parent writes
  `proc.pid` into `cgroup.procs` right after `Popen`** — no `preexec_fn`
  (CPython documents it as unsafe in a threaded parent, and the server is
  threaded). The child has only begun interpreter start-up by then; pages
  already charged stay with the parent's cgroup (a few MB), everything
  after — the 500 MB OCCT import included — is charged to the worker's.
  On respawn the directory is reused; on `stop()` it is removed.
- `compose.yaml` documents Model 2 as commented lines (`cgroup_parent`,
  the bind mount, the host `mkdir`/`chown`/`subtree_control` steps) and
  adds container-wide `pids_limit`/`mem_limit` as the blast-radius cap that
  needs no host work. `systemd-run --scope` is documented as unverified.

## Decision 5 — The supervisor lives in the client's request loop

`KernelClient._request_locked` already polls the stdout queue every 0.5 s.
It now polls at `sample_interval_s` and, while a request is in flight,
samples the child's RSS through the platform backend (`plan.rss(pid)`),
keeps the request's max, and on `rss > memory_mb` sets
`self._breach = ("memory_cap", observed)` and kills. `_kill` and the
EOF/return-code path consult `_breach`, then the backend's
`explain_exit(returncode)` (cgroup events delta, `-SIGXCPU`), then fall back
to today's `kernel_crash` with `stderr_tail`. Timeout stays `timeout`, now
with `details.usage`. Cost: 0.5–1.3 µs per sample.

## Decision 6 — Metering is a protocol envelope + a service-owned meter

- **Worker** (`_meter.py`, OCP-free): at request start `t0 =
  perf_counter()`, `r0 = getrusage(RUSAGE_SELF)`, and on Linux `write("5")
  → /proc/self/clear_refs` (resets `VmHWM` **and** `ru_maxrss` — verified,
  6 µs at 25 MB RSS; cost at 500 MB is a page-table walk, unmeasured, and
  bounded by being once per request); at end `cpu_ms = Δ(utime+stime)`,
  `wall_ms`, `peak_rss_mb` = Linux `VmHWM` (a true per-request peak),
  macOS/Windows `ru_maxrss`/`PeakWorkingSetSize` (lifetime high-water —
  labelled `peak_rss_is_lifetime: true`), `rss_mb` at end. Units:
  `ru_maxrss` is **bytes on macOS, KiB on Linux** — branch on platform.
  Every response line gains `"usage": {...}` beside `result`/`error`; the
  handler dictionaries are untouched (G5).
- **Client** merges the supervisor's observed max into `peak_rss_mb` (so
  macOS gets a real per-request peak from the parent's samples), attaches
  `usage` to `KernelError.details` on failures, exposes `last_usage`, and
  calls an optional `on_usage(record)` hook (`method`, `usage`, `affinity`,
  `ok`, and the current **usage scope**).
- **Scope** (`core/usage.py`): `scope_var: ContextVar[str | None]` (a
  project id) beside `locks.client_id_var`. Set in three places, all
  additive: `app.py`'s guard middleware from the path
  (`/api/projects/{project}/…` and the assembly/config route families), the
  tool registry's `call()` from `args["project"]`, and `AgentCADService`'s
  own build/export/interference paths (authoritative). ContextVars reach
  sync endpoints via anyio's context copy — the same trick `client_id_var`
  relies on.
- **Meter** (`core/usage.py::UsageMeter`): thread-safe roll-ups keyed by
  `(project, identity)` — `requests`, `errors`, `cpu_ms`, `wall_ms`,
  `peak_rss_mb` (max), `last_at`; a bounded ring of recent records for
  `since`. `service.usage` is the seam; the CLI installs it as the kernel's
  `on_usage`. It never serializes the pool (a lock around a dict update).
- **Surface:** `/api/health` gains `usage: {totals, projects: [...top 20]}`
  (authenticated body only; the anonymous hosted body is unchanged — FR11
  "scoped to local"); a new tool pack `core/tools_usage.py` registers
  `get_usage {project?, since?}` → roll-ups per project and per identity;
  `docs/agent-api.md` documents `details.reason`/`details.usage`. The audit
  log line per principal is PRD-005 (deferred, said in the docs).

## Decision 7 — Windows: quotas via job objects, confinement `unsupported`, AppContainer carved out **[ruling]**

`sandbox_windows.py`: `CreateJobObjectW`, `SetInformationJobObject`
(`JobObjectExtendedLimitInformation`: `PROCESS_MEMORY` = `memory_mb`,
`ACTIVE_PROCESS` = `pids`, `KILL_ON_JOB_CLOSE`; `JobObjectCpuRateControl
Information` hard cap when `cpu_percent` is set), `AssignProcessToJobObject`
immediately after `Popen` (the worker does nothing before its first request,
so the assignment race is benign; recorded); the supervisor samples via
`psapi.GetProcessMemoryInfo`. Confinement reports `unsupported` — honestly,
in health and docs. AppContainer + Python + OCCT is "the least-trodden path"
in the PRD's own words, cannot be exercised on this dev box, and each
attempt is a Windows-CI round trip; it is carved out as **PRD-006b**
(Decision 12) on the 005a/031a letter precedent so folder-as-status stays
truthful.

## Decision 8 — Honesty: status is measured, never inferred

`sandbox.status()` (FR3/FR13) reports per facet: `confinement: {status:
active|off|unsupported, mechanism, posture, detail}` and `quotas: {status,
mechanism, limits}`, plus `warnings: []`. `active` for confinement is set
**only** from the worker's own `ping` report (Landlock ABI applied, seccomp
installed) — a plan that *intended* to confine but whose preamble reported
a failure is `off` with the failure in `warnings`, and hosted mode logs it
at startup as a loud warning (not fatal — the deploy-smoke job must keep
proving the compose image boots; the operator reads health). Reasons for
`unsupported`: non-darwin/linux/windows, `sandbox-exec` missing (macOS),
Landlock ABI < 3 or `EOPNOTSUPP` (Linux), unknown machine arch (Linux).
`AGENTCAD_NO_SANDBOX=1` (env, wins) / `{"sandbox": false}` still opts out
everywhere and reports `off`. The top-level `sandbox` key in health becomes
this object (FR11 shape: `{status, mechanism, quotas, …}` where `status`
is the confinement status — the historical meaning); `tests/test_server.py`
and `tests/test_sandbox.py` are updated; the frontend reads no health field
but `chat_available` (verified).

## Decision 9 — Breaches are the existing errors, classified

- Denials raised **inside the script** stay `script_error` (traceback, line,
  hint) and gain `details.denied` from `kernel/denials.py::classify(exc_type,
  message)`: `network` (`PermissionError [Errno 1]` on socket/urlopen under a
  filter), `filesystem` (`PermissionError [Errno 13]` on a path outside the
  roots), `process_count` (`BlockingIOError` `EAGAIN`/`[Errno 35]`), `memory`
  (`MemoryError`). Only when a sandbox/quota is active — the worker knows
  from its own preamble. Four Error Doctor entries carry the plain-language
  hint the PRD's experience section asks for ("network access is blocked in
  the kernel sandbox", …).
- Kills are `kernel_crash` with `details.reason` ∈ `memory_cap | pids_cap |
  cpu_cap` when attributable (Decision 5) and always `details.usage`;
  timeouts are `timeout` with `details.usage`. Previous good geometry stays
  (`_status` is not written on error paths — unchanged), the worker respawns
  warm on the next request (unchanged), and a breach on one pool worker
  cannot disturb siblings (each `KernelClient` owns its process, lock and
  breach state — FR9/AC6 is a test, not new code).

## Decision 10 — Disk budgets in the store, with a cache janitor

`quotas.disk_mb` per project covers `.cache/`, `exports/`, `imports/`.
`ProjectStore.disk_usage(proj)` sums them with a 5 s memo;
`assert_disk_budget(proj)` raises `DiskBudgetError` (an `AppError`, wire
type `DiskBudgetError`, `details: {used_mb, budget_mb}`) — called by the
service before a build, an export, an assembly export and an import write,
so an exceeded budget fails **before** the worker writes and never corrupts
state (`_atomic_write` is unchanged). `trim_cache(proj, keep_keys)` runs
after each successful build: when `.cache/` exceeds 75 % of the budget it
deletes least-recently-modified `.acm`/`.faces.u32`/`.lod1.acm` files whose
key is not currently referenced — a rebuild recreates them, nothing precious
is lost. Per-tenant budgets are the PRD-005 layer of the same resolver.

## Decision 11 — Kill-to-others is denied on Linux, as it already is on macOS

The seatbelt allows `signal (target self)` only. Linux gets parity through
the seccomp rules in Decision 1 (broadcast and server-pid signals, ptrace,
process_vm_*, pidfd_open). A script may still signal its own forked children
— it created them — and the worker itself.

## Decision 12 — Scope: what closes here and what is carved out **[ruling]**

Ships in this PRD: MVP + Phase 2 of the PRD (Linux confinement, both
postures, all quota tiers incl. cgroup delegation, breach handling, metering,
health/`get_usage`, disk budgets, layered config, macOS parity, Windows job
objects) and the docs. **Carved out as PRD-006b (Windows AppContainer)**:
FR2's confinement half and AC3's Windows clause. The reasoning is recorded
in the PRD header at close-out (mirrors 005a). Also deferred, named:
`systemd-run` scope tier (unverified), the audit-log usage line (PRD-005),
FEM/`[fem]` extra under confinement (the seatbelt today is the same — a
`test_fem` run under Landlock is a follow-up when a Linux `[fem]` job
exists).

## Decision 13 — CI proves it where it runs

- The malicious battery + AC tests are `@pytest.mark.portability` so
  Linux and Windows CI run them; each test asserts *containment when the
  live status is `active`* and additionally **fails if `AGENTCAD_EXPECT_
  SANDBOX=active` is set and the status is not** — ci.yml sets that env on
  the ubuntu job (and, for quotas, on all three), so a silent degradation to
  `off` is red rather than skipped (AC8). A first CI step prints the Landlock
  probe (`uname -r`, `/sys/kernel/security/lsm`, ABI) so a runner change is
  diagnosable.
- Locally, the Linux battery is also runnable in the image
  (`docker run … agentcad:local uv run pytest tests/test_sandbox_linux.py`)
  — the developer loop on this macOS box; documented in AGENTS.md.
- The `deploy-smoke` job's health assertion is unchanged (anonymous body).

## Architecture

```
kernel/quotas.py         Quotas dataclass; resolve(overrides=None) — defaults < config < env < overrides
kernel/sandbox.py        facade: plan(writable_dirs, quotas, posture) -> SandboxPlan; status(); supported(); available()
                         (public seams wrap_argv/status kept; wrap_argv delegates to plan for macOS)
kernel/sandbox_macos.py  the v3 seatbelt profile, moved verbatim + private-tmp grant + NPROC value for the preamble + libproc rss()
kernel/sandbox_linux.py  posture read-roots, AGENTCAD_CONFINE payload, cgroup_probe()/CgroupTier, rlimit tier, /proc rss(), explain_exit()
kernel/sandbox_windows.py job objects (ctypes), psapi rss(), status
kernel/_confine.py       ctypes Landlock + seccomp (+ arch tables) — used ONLY inside the worker preamble
kernel/_preamble.py      apply_from_env(): rlimits → landlock → seccomp; sets worker.SANDBOX_REPORT
kernel/_meter.py         per-request usage (clear_refs on Linux; rusage; wall)
kernel/denials.py        classify(exc_type, message) -> "network"|"filesystem"|"process_count"|"memory"|None
kernel/worker.py         +2 lines preamble at top; ping reports SANDBOX_REPORT; main() wraps _dispatch in the meter and adds "usage"; _script_error_from_exc adds details.denied
kernel/error_doctor.py   +4 sandbox entries
kernel/client.py         KernelClient(..., quotas=None, on_usage=None, posture=None): plan → argv/env/preexec/post_spawn; private tmp; supervisor; breach attribution; usage merge/hook; .sandbox_report
kernel/pool.py           passthrough of quotas/on_usage/posture; .sandbox_report from worker 0
core/usage.py            scope_var, UsageMeter, record shape
core/tools_usage.py      get_usage tool pack
core/project.py          disk_usage / assert_disk_budget / trim_cache; DiskBudgetError in core/model.py (or errors module — wherever AppError subclasses live)
core/service.py          service.usage; scope set on build/export/interference; budget asserts; trim after build
core/tools.py            call(): scope_var from args["project"] (3 lines)
server/app.py            middleware sets scope_var from path; /api/health sandbox object + usage
cli.py                   _build_service(..., posture=None): quotas.resolve(), UsageMeter, kernel kwargs; startup warning in hosted mode
```

## Data shapes

```jsonc
// AGENTCAD_CONFINE (client → worker env)
{"posture": "hosted", "read_roots": ["/usr", ...], "write_roots": ["/data/projects", "/tmp/agentcad-worker-x1"],
 "extra_files": ["/dev/null", "/proc/self/clear_refs"], "seccomp": {"server_pid": 123, "net": "unix_only"},
 "rlimits": {"RLIMIT_AS": [6442450944, 6442450944], "RLIMIT_NPROC": [591, 591]}}

// ping result (worker → client)
{"ok": true, "build123d": "0.11.1",
 "sandbox": {"landlock_abi": 6, "seccomp": "seccomp(2)", "rlimits": ["RLIMIT_AS", "RLIMIT_NPROC"], "posture": "hosted",
             "failures": []}}

// every response line
{"id": 7, "result": {...}, "usage": {"cpu_ms": 41.2, "wall_ms": 38.9, "peak_rss_mb": 512.4, "rss_mb": 498.1,
                                     "peak_rss_is_lifetime": false}}

// KernelError.details on a breach
{"reason": "memory_cap", "limit_mb": 2048, "observed_rss_mb": 2311.6, "tier": "supervisor",
 "usage": {...}, "stderr_tail": [...]}

// script_error details on a denial
{"traceback": "...", "line": 4, "denied": "network", "hint": "Network access is blocked in the kernel sandbox. ..."}

// /api/health (authenticated / local)
{"status": "ok", "version": "...", "kernel": "ready", "chat_available": false,
 "sandbox": {"status": "active", "mechanism": "landlock+seccomp", "posture": "hosted",
             "confinement": {"status": "active", "mechanism": "landlock+seccomp", "detail": {"landlock_abi": 6}},
             "quotas": {"status": "active", "mechanism": "rlimit+supervisor",
                        "limits": {"memory_mb": 2048, "address_space_mb": 6144, "pids": 128, "cpu_percent": null, "disk_mb": 2048}},
             "warnings": []},
 "usage": {"totals": {"requests": 12, "errors": 1, "cpu_ms": 1811.0, "wall_ms": 2210.4, "peak_rss_mb": 612.0},
           "projects": [{"project": "rocketry", "requests": 9, "cpu_ms": 1500.2, ...}]}}
```

## Testing

- **Unit (all OSes):** `quotas.resolve` layering and `0` semantics; `SandboxPlan`
  shapes per platform via monkeypatched `sys.platform`; `denials.classify`;
  `_meter` unit branches (`ru_maxrss` units); `UsageMeter` roll-ups and
  `since`; `DiskBudgetError` + `trim_cache` on a fixture project; health
  shape; `get_usage` tool.
- **Kernel (macOS dev + macOS CI):** the seatbelt regressions in
  `tests/test_sandbox.py` keep passing (AC2); private tmp is granted and
  removed; supervisor kills a balloon with `reason: memory_cap` and the next
  build succeeds with previous geometry intact (AC4, cap set to
  baseline + 300 MB in the test); `RLIMIT_NPROC` stops a fork loop as a
  `script_error` `denied: process_count`; timeout carries `details.usage`
  (AC5); a garbage-writing forked child does not corrupt the protocol; two
  projects' roll-ups are distinguishable (AC7); sibling isolation (AC6, pool
  of 2: kill one mid-build, the other completes).
- **Linux (`tests/test_sandbox_linux.py`, portability-marked; run in the
  image locally and on ubuntu CI):** the battery — socket connect, write
  outside roots, write to another worker's temp, `os.kill(-1)`, fork loop
  under `RLIMIT_NPROC`, memory balloon under `RLIMIT_AS` and under the
  supervisor — each a structured error naming the violation, worker
  respawns, next build succeeds (AC1); posture `hosted` denies reading a
  file under a fake state dir and still builds; `AGENTCAD_NO_SANDBOX=1`
  → `off`; the cgroup tier is exercised **only when
  `AGENTCAD_CGROUP_DIR` is writable** (Model 2 in Docker: the test harness
  documents the `docker run` line) — otherwise the tier's *detection*
  (falls back honestly) is what is asserted (AC8).
- **Windows (CI, portability-marked):** job-object memory cap →
  `MemoryError` → `script_error denied: memory`; confinement reports
  `unsupported`; health shape.
- **Full suite:** `make test` green on macOS (cited count in the changelog);
  the three CI jobs green with `AGENTCAD_EXPECT_SANDBOX=active` on ubuntu.

## Risks and residuals

- **Supervisor overshoot** (~interval × alloc rate, measured 380–620 MB at
  4 GB/s): defaults sit well below typical hosts; documented, and the cgroup
  tier is the hard cap where delegated. A sub-interval allocate-and-free is
  invisible to the supervisor — the cgroup tier and `RLIMIT_AS` catch it.
- **`RLIMIT_AS` false denials on very large models**: loose default
  (3 × memory), knob documented, `0` disables.
- **`clear_refs` cost at large RSS**: unmeasured beyond 25 MB; once per
  request; if a benchmark shows it, the knob `AGENTCAD_QUOTA_PEAK_RESET=0`
  falls back to lifetime `VmHWM` (labelled).
- **Protocol forgery by a forked child** (macOS spike §4): a child that
  inherits fd 1 can write a JSON line with the next id and the client
  accepts it as the result. Not new to this PRD and not a confinement
  question — the fix is an unguessable per-request id (or a protocol fd the
  script cannot reach). **Fixed here** because it is a 10-line change in
  `client.py`/`worker.py` (`id` becomes a random 64-bit token; the worker
  echoes it) and the tests exist; called out in the changelog.
- **GitHub runner Landlock LSM list**: expected present; the probe step and
  `AGENTCAD_EXPECT_SANDBOX` make an absence red and diagnosable in one CI
  run rather than a silent skip.
- **A `MemoryError` from inside OCCT/C++** under `RLIMIT_AS` may abort the
  process rather than unwind — then the supervisor/`kernel_crash` path
  reports it as a plain crash with usage; still contained.
- **NPROC is per-uid**: a busy desktop user with many processes gets a
  looser fork cap; documented as headroom, not budget.

## Post-build corrections

What the build changed about this document, so a reader of the spec is not
reading a plan the code disagrees with. The seccomp signal rule is corrected
in place above (Decision 1); these four are additions or tightenings that did
not fit inside a sentence.

1. **The cgroup own-route is opt-in by name, not by discovery** (Decision 4,
   tightened in the slice-3 review). As written, `cgroup_probe()` would look at
   the process's own cgroup whenever `AGENTCAD_CGROUP_DIR` was unset. That is
   activation by capability: `os.access` answers `W_OK` for uid 0 almost
   everywhere, so a **root** server would "discover" a writable subtree on any
   machine and start moving its own pids around. As built: unset ⇒ nothing is
   probed at all; a path ⇒ Model 2; **`auto` ⇒ the own-cgroup route**, which
   refuses root, refuses a subtree whose `st_uid` is not ours, and refuses the
   root cgroup, checking all of it *before* it mutates anything; `off` ⇒ not
   even `auto`. Every failure of a probe that was asked for reaches
   `plan.warnings`.
2. **`RLIMIT_NPROC` counts tasks, not processes.** The limit is enforced
   against the uid's `task_struct` count, and a warm worker runs 15–22 threads
   (TBB, BLAS), so `live_uid_process_count()` sums `Threads:` from
   `/proc/*/status` rather than counting pids. Measured: with the per-process
   count, the second module-scoped worker in `tests/test_sandbox_linux.py`
   died inside `import build123d` with a `pthread_create` EAGAIN — the same
   fate a three-worker pool's third worker was one thread away from.
3. **Random request ids close cross-request forgery only.** "Risks" said the
   unguessable id fixes the forked-child forgery; it fixes half of it. A
   *lingering* child (or any stale writer) can no longer compute the ids of
   requests it never observed — that is a 62-bit guess. The **running** script
   can still forge the response to its own in-flight request: it holds fd 1 and
   can reach the id through the interpreter. That is the same trust domain as
   `build()` returning a fake shape, so it is not worth an fd-passing
   redesign, and confinement was never the answer to it either (a process may
   write to its own stdout).
4. **`details["usage"]` is the kill paths' contract, not every error's.**
   Kills, timeouts and crashes carry it; a **worker-reported `script_error`
   deliberately does not**. The worker answered, so its cost travels on
   `last_usage` and through the `on_usage` hook with `ok: false`, and copying a
   per-run `cpu_ms` into the error body broke the invariant that both drawing
   routes render an **identical** error (`tests/test_configs_drawing.py`, the
   PRD-012 rule). The stub on the kill paths is what the *parent* saw:
   `cpu_ms` is `None` — "not measurable from here" is not "no CPU was spent".
