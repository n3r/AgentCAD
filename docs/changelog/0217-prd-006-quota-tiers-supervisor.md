# 0217 — PRD-006 slice 3: the real quota tiers, the supervisor, breach attribution

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Claude

## Summary
The caps stop being a plan and start being enforced. Linux gains its cgroup v2
tier (opt-in by delegation), a `/proc` RSS sampler and OOM/CPU exit
attribution; Windows gains a job-object backend; the client gains the
parent-side supervisor inside its request loop, which kills a ballooning worker
and *names* the reason; every kill path carries `details.usage`; and
`sandbox.report(kernel)` answers the per-facet health object. AC4 (a runaway
script is killed, attributed and recovered from), AC5 (a timeout carries the
wall clock it burned) and AC6 (a breach on one pool worker leaves its sibling
alone) pass on macOS and Linux.

## Changes
- **`kernel/sandbox_linux.py`** — `CgroupTier` (Decision 4): `probe()` takes an
  operator-delegated directory from `AGENTCAD_CGROUP_DIR` (Model 2) or a
  genuinely delegated own cgroup, verifying every step — it is a directory, it
  is a cgroup v2 one, it delegates `memory`+`pids` (enabling them if it may),
  and a child cgroup can really be created and removed. The **root cgroup is
  refused outright**: a CI runner and a developer laptop both land in it, and
  reorganising it is a machine-wide change nobody asked for. A non-root own
  cgroup that holds this process gets a `server` leaf and **only this process**
  moved into it (the no-internal-process rule). `make_worker` writes
  `memory.max`, `memory.swap.max=0` (load-bearing: with swap at `max` the
  spike's 400 MB allocation under a 200 MB cap swapped instead of dying),
  `pids.max` and `cpu.max`; `attach` writes the pid after `Popen`; `oom_kills`
  reads the counter that separates a kernel OOM from the supervisor's kill and
  from a timeout kill (all three are `returncode == -9`); `release` rmdirs.
  `AGENTCAD_CGROUP_DIR=off` switches the tier off outright.
- **`LinuxBackend`** — `rss_bytes` reads field 2 of `/proc/<pid>/statm` times
  the page size; `explain_exit` answers `{"reason": "memory_cap", "tier":
  "cgroup"}` on an OOM-counter delta and `{"reason": "cpu_cap", "tier":
  "rlimit"}` on `-SIGXCPU`; `place_in` creates the worker's cgroup and returns
  False (with a warning) rather than raising, so the mechanism string names
  only tiers that were actually installed.
- **`live_uid_process_count()` now counts tasks, not processes.** `RLIMIT_NPROC`
  is enforced against the uid's `task_struct` count and a warm worker runs
  15-22 threads, so the per-process count under-measured a multi-worker pool by
  ~20 tasks per live worker. Measured: the second module-scoped worker in
  `tests/test_sandbox_linux.py` died inside `import build123d` with a
  `pthread_create` EAGAIN until this was fixed — the same fate a three-worker
  pool's third worker was one thread away from.
- **`kernel/sandbox_windows.py`** (new) — the job object is created **and
  configured in `build()`**, in the server process, and the process is only
  *assigned* to it after `Popen`; that ordering is what lets
  `quotas.mechanism` say `job_object+supervisor` honestly. Flags:
  `KILL_ON_JOB_CLOSE` always, `PROCESS_MEMORY` (commit limit) and
  `ACTIVE_PROCESS` when the knobs are set, plus a `CpuRateControl` hard cap as
  a share of the whole machine. `rss_bytes` is psapi's `WorkingSetSize`;
  `release()` closes the handle, which kills survivors. Confinement is
  `unsupported` with the reason "AppContainer confinement is PRD-006b"
  (Decision 7). Every `ctypes.WinDLL` lookup is inside a function, so the
  module imports on macOS and Linux and its plan shape is asserted there.
- **`kernel/client.py`** — the supervisor lives in `_request_locked` (Decision
  5): it samples the child's RSS through the backend at
  `max(0.05, sample_interval_s or 0.25)`, keeps the request's peak, and on
  `rss > memory_mb` sets `_breach` *before* killing and raises `kernel_crash`
  with `reason`/`tier`/`limit_mb`/`observed_rss_mb`. EOF consults `_breach`
  first, then the backend's `explain_exit`. Timeouts, breaches and crashes all
  carry `details["usage"]` from `_usage_stub` (`cpu_ms: None` — "not
  measurable from here" is not "no CPU was spent"). The parent's peak merges
  into the response's `usage`: it *replaces* a lifetime high-water mark (macOS,
  Windows), and is combined with `max` where the worker's own number is already
  per-request (Linux). `_emit_usage` hands `{"method", "usage", "ok",
  "worker"}` to the `on_usage` hook inside a `try/except` that complains once
  on stderr — a metering bug may never fail a build. A client built with no
  `writable_dirs` and no `quotas` still runs the historical 0.5 s poll with no
  sampler and no plan.
- **`kernel/sandbox.py`** — `report(kernel)` (new): the per-facet health
  object, read through `getattr` so a plan-free client answers it without
  raising. Confinement stays `active` only while the worker's own ping report
  agrees (via `client.confinement_holds`, so a *quota*-stage failure does not
  clear a confinement claim); a downgrade drops the mechanism too, and the
  worker's failures plus any post-plan backend warning land in `warnings`.
  `supported()` now answers **True on Linux** when the Landlock ABI is at least
  3 and the machine has a syscall table, which makes `status()`/`available()`
  — and so `/api/health`'s legacy string — truthful there. `_BACKENDS` gains
  `win32`. An opt-out on a platform with **no** confinement backend now stays
  `unsupported` instead of being rewritten to `off`: there is no switch to
  have flipped.
- **`kernel/pool.py`** — a `plan` property exposing worker 0's plan, which is
  how `sandbox.report()` reads a pool.
- **`kernel/_preamble.py` + `kernel/worker.py`** — the payload gains a `quotas`
  key: tiers the *parent* installed around the worker (`["job_object"]` on
  Windows, `["cgroup"]` on Linux). The worker applies nothing for it and
  reports it, because `denials.classify` refuses to name a denial no worker
  reported a live cap for — without it a job object's `MemoryError` would read
  as the machine running out of memory (Decision 9, and `denials.py`'s own
  docstring lists that case).

## Files
- `agentcad/kernel/sandbox_linux.py` — `CgroupTier`, the tier wiring in
  `build()`, a live `LinuxBackend`, task-counting `live_uid_process_count`
- `agentcad/kernel/sandbox_windows.py` — new: job object, psapi sampler
- `agentcad/kernel/client.py` — supervisor loop, `_breach`, `_usage_stub`,
  `_emit_usage`, `details.usage` on every kill path
- `agentcad/kernel/sandbox.py` — `report()`, `supported()` on Linux, the
  `win32` backend entry, the opt-out/`unsupported` fix
- `agentcad/kernel/pool.py` — the `plan` property
- `agentcad/kernel/_preamble.py`, `agentcad/kernel/worker.py` — the `quotas`
  payload key and the `active` term that reads it
- `scripts/linux-test.sh` — `tests/test_supervisor.py` in the default list
- `tests/test_supervisor.py` — new: AC4/AC5/AC6, the hook, the interval floor
- `tests/test_sandbox_windows.py` — new: the Windows battery (`skipif != win32`)
- `tests/test_sandbox_linux.py` — the rlimit, cgroup and fallback tests
- `tests/test_sandbox_plan.py` — the Windows plan shape (stubbed, all-OS), the
  cgroup tier units, `report()`, `supported()`, the preamble `quotas` probe
- `tests/test_protocol_ids.py` — the ping report gained a `quotas` key

## Notes
- `make test — 4096 passed, 32 skipped in 560.15s` on macOS 26.6 (arm64).
- `sh scripts/linux-test.sh` in `agentcad:local` — **84 passed, 2 skipped in
  90.93s** (the skips are the delegated-cgroup test and one meter case). The cgroup
  tier was then exercised **for real** under Decision 4's Model 2 (host
  `mkdir /sys/fs/cgroup/agentcad` + `chown 10001`, `docker run
  --cgroup-parent=/agentcad -v /sys/fs/cgroup/agentcad:/cg:rw -e
  AGENTCAD_CGROUP_DIR=/cg`): **24 passed, 0 skipped**, including a real kernel
  OOM kill reported as `reason: memory_cap`, `tier: cgroup`. The recipe is in
  `test_cgroup_tier_when_delegated`'s docstring.
- Two numbers in the plan were changed against measured evidence and are
  documented in the tests that use them: the `RLIMIT_AS` test caps at 2048 MiB
  rather than the spike's 1536 MiB floor (a test pinned to a measured floor
  flakes on the next architecture), and the cgroup test caps memory at 1024 MB
  rather than 512 (a warm worker is 451-482 MB RSS, so a 512 MB cgroup would
  OOM-kill it during `import build123d` and prove nothing about a balloon).
- A worker-reported `script_error` deliberately does **not** gain
  `details.usage`: the worker answered, so its usage travels on `last_usage`
  and through the hook with `ok: False`, and copying a per-run `cpu_ms` into
  the error body broke the invariant that both drawing routes render an
  identical error (`tests/test_configs_drawing.py`). `details.usage` is the
  kill paths' contract.
- With a cgroup in force the supervisor can never fire: the kernel kills at the
  charge, so RSS never exceeds `memory.max` and the sampled value never crosses
  the cap. The two tiers share the `memory_mb` knob and the in-kernel one wins
  deterministically — which is why `tests/test_supervisor.py` sets
  `AGENTCAD_CGROUP_DIR=off` to pin the tier it is testing.
- The `NullBackend` path (an unknown platform) still reports `supervisor` while
  its `rss_bytes` always answers `None` — the loop is armed but blind. Left as
  Slice 1 pinned it; it is only reachable on a platform AgentCAD has no backend
  for at all.
- Health does not publish the new object yet: `/api/health` still returns the
  legacy string, and wiring `sandbox.report()` into it (plus the docs) is
  Slice 5's step.
