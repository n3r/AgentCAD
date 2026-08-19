# 0220 — PRD-006 final review fixes: the pidfd hole, a per-worker fork budget, honest facets and tiers

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude

## Summary

The whole-branch review of PRD-006 found two Critical defects — a seccomp gap
that let a part script SIGKILL the server through a `/proc/<pid>` descriptor,
and an `RLIMIT_NPROC` budget computed once per client that killed the third
worker of a three-worker pool during `import build123d` — plus six Important
and five Minor findings. This lands all of them: the syscall filter closes the
pidfd family, the fork budget is re-measured at every spawn and scaled by the
pool size, the CLI accepts and creates a `--work-dir` before the confined
workers spawn, a lost Landlock root grant stops reading as an unconfined
worker, kills and timeouts reach the usage meter, `~/.agentcad` stops being a
kernel-writable root, a hosted instance refuses to serve with its state dir
inside one, and health stops claiming any facet or tier the worker did not
report applying.

## Changes

### C1 — `pidfd_send_signal` bypassed the signal filter (`kernel/_confine.py`)

- Added `pidfd_send_signal: 424`, `pidfd_getfd: 438` and `process_madvise: 440`
  to **both** `ARCH` tables (the numbers are identical on x86_64 and aarch64)
  and to `_PEEK_SYSCALLS`, the unconditional `RET_ERRNO(EPERM)` list.
- Why it was a hole: `pidfd_send_signal(pidfd, sig, ...)` names its target by a
  **file descriptor**, so `args[0]` is not a `pid_t` and the filter's
  negative-pid / server-pid analysis never ran on it. Denying `pidfd_open` was
  not enough — **a `/proc/<pid>` directory fd is a valid pidfd**, and `/proc`
  is readable in both postures, so a script could
  `os.open("/proc/<server pid>", O_RDONLY|O_DIRECTORY)` and kill the server
  through it (the review verified the victim dying with -9 in the image).
  `pidfd_getfd` steals another process's descriptors and `process_madvise`
  reaches into its address space through the same handle.

### C2 — one fork budget for N workers (`kernel/sandbox*.py`, `client.py`, `pool.py`)

- `RLIMIT_NPROC` is now `live uid task count, measured at EVERY spawn +
  pids_headroom × pool_size`.
- `sandbox.plan(..., pool_size=1)`, `KernelClient(..., pool_size=1)`,
  `KernelPool` passes its own `size`. `Backend.refresh()` is a new protocol
  method returning environment additions recomputed for the spawn about to
  happen; `SandboxPlan.spawn_env()` is `env` plus that, and
  `client._ensure_started` merges **`spawn_env()`** rather than `plan.env`.
  `plan.env` stays the construction-time snapshot health and the tests read.
- `SandboxPlan` gains a `pool_size` field. `LinuxBackend.remember()/refresh()`
  keep the payload and re-derive its `rlimits`; `MacBackend(quotas, pool_size)`
  does the same through a new module-level `sandbox_macos._rlimits`. The
  Windows `build` takes and ignores `pool_size` (no rlimits there).
- Why both halves are needed: the limit is per-**uid** but the kernel checks it
  against the *calling* process's own ceiling, and a warm worker runs 15–22
  threads. Computed once and handed identically to three slots, workers 0 and 1
  spent the headroom just by existing and worker 2 died inside
  `import build123d`.

### I1 — a `--work-dir` that did not exist lost its Landlock grant (`cli.py`)

- `checks._refuse_overlap` and `gate._refuse_overlap` are now thin wrappers
  over module-level `checks.refuse_work_dir_overlap(root, canonical,
  projects_root)` and `gate.refuse_work_dir_overlap(root, projects_root,
  source)`, so the CLI can ask the same question without a service.
- New `cli._accept_work_dir(raw, refuse)`: resolve → accept-or-refuse →
  `mkdir(parents=True, exist_ok=True)`, all **before** `_build_service`, in
  `cmd_check`, `cmd_package_validate` and `cmd_publish`.
- Previously the path was granted but never created: on Linux the Landlock rule
  was ENOENT, the grant was lost, and every part failed with a
  `PermissionError` instead of producing a verdict. Creating it is only safe
  after acceptance, which is why the refusal moved with it — "a refused path
  leaves nothing behind" is unchanged, and so is "the runner never deletes a
  directory it did not create".

### I2 — a lost root grant no longer clears the confinement claim

- `_preamble._landlock` files per-path grant failures under
  **`stage: "landlock_root"`** instead of `"landlock"`.
  `client.CONFINEMENT_STAGES` stays `("landlock", "seccomp")`, so
  `confinement_holds` now returns True for them.
- `sandbox.report()` renders them as *"the worker lost a Landlock grant (the
  ruleset is in force; writes there will be denied): …"* rather than "could not
  apply landlock".
- The ruleset landed and the process is confined — more narrowly than intended,
  not less. Calling that `off` was the mirror image of claiming `active` from
  intent, and under `AGENTCAD_EXPECT_SANDBOX=active` it turned one missing
  directory into a red ubuntu CI job.

### I3 — kills, timeouts and crashes now reach the meter (`kernel/client.py`)

- `self._emit_usage(method, usage, ok=False)` before all four raises that end a
  request without a worker answer: the pre-write `BrokenPipeError`, the
  timeout, the supervisor's memory breach and the EOF crash.
- `core/usage.py` already documented `cpu_ms: None` records for exactly these
  paths, but the hook only fired on success, so `errors` could never rise and a
  60 s timeout contributed nothing to the wall clock it burned.

### I4 — CI timeout (`.github/workflows/ci.yml`)

- The `macos-latest` PR job's `timeout` goes 20 → 30 minutes: PRD-006 put
  `test_supervisor.py`'s real 1.5 GB balloons, `test_sandbox_plan.py` and the
  acceptance battery on the PR path, and a suite that times out is
  indistinguishable from one that failed.

### I5 — `~/.agentcad` is no longer a writable root (`cli._writable_roots`)

- Dropped from the roots (and no longer created there). Nothing under
  `agentcad/kernel/` or `agentcad/toolkit/` reads or writes the config dir,
  every `load_config()` caller is server-side, and the worker's `HOME` is its
  private temp dir — so the grant bought nothing and cost the sentence the docs
  most want to say: a part script can write **nothing under the server user's
  home**. The file also carries index definitions and the quota knobs, so a
  script that could rewrite it could raise its own caps.

### I6 — a hosted state dir inside a write root refuses to serve (`cli.py`)

- `_build_service` records `service.writable_roots`; new
  `cli._refuse_state_dir_in_a_write_root(mode, service)` runs in `cmd_serve`
  before `build_registry` and exits 2 with
  `error: AGENTCAD_STATE_DIR (<path>) lies inside a kernel-writable root
  (<root>); part scripts could read secret.key — set AGENTCAD_STATE_DIR
  outside the projects tree`.
- The hosted read allow-list is the read roots **plus the write roots**, so a
  state dir inside one is readable *and* writable however narrow the allow-list
  is, and whoever reads `secret.key` forges any session. Fatal rather than a
  warning — unlike `_warn_if_unconfined`, this is one misplaced path with an
  exact remedy. Local mode is not checked; compose's `/data/state` is a sibling
  of `/data/projects` and passes.

### M1/M2 — `sandbox.report()` cannot contradict the kernel, and drops a tier it lost

- `report()` downgrades to `off` when `getattr(kernel, "sandboxed", None)` is
  `False`, closing the gap where a worker that answered `ping` with no
  `sandbox` object left `live` empty and the plan's `active` stood.
- An empty live `rlimits` list removes `rlimit` from `quotas.mechanism` (and
  makes the quotas `off` if it was the only tier), with the reason in
  `warnings`.

### M3 — denial classification is per facet (`kernel/denials.py`, `worker.py`)

- New `denials.active_facets(report)`: `filesystem` needs `landlock_abi`,
  `network` needs `seccomp`, `process_count`/`memory` need an applied rlimit or
  a parent-installed `quotas` tier. `classify(..., active=)` now takes a bool
  **or** a collection of facet names, and `worker._script_error_from_exc`
  passes `active_facets(SANDBOX_REPORT)`.
- An `AGENTCAD_NO_SANDBOX` Linux worker still gets its caps, and used to label
  an ordinary DAC `EACCES` a sandbox denial.

### M4 — the cache janitor protects freshly written meshes (`core/project.py`)

- `trim_cache(proj, keep_keys, *, min_age_s=_TRIM_MIN_AGE_S)` skips any file
  modified in the last 10 minutes. The keep-set is the *service's*
  `_status`/`_config_status`, which is empty after a restart, so a cold
  assembly read over the watermark could delete a sibling part's mesh that
  another request built seconds earlier and the browser had not fetched. One
  clock reading per sweep, so the cut-off does not drift across a large
  directory.

### F1 — the macOS `details.denied` regression closed (`kernel/sandbox_macos.py`, `sandbox_linux.py`, `_preamble.py`, `denials.py`)

- `sandbox_macos.build()` now puts `payload["confinement"] = ["filesystem",
  "network"]` into the `AGENTCAD_CONFINE` JSON whenever the seatbelt is
  genuinely wrapped around the argv (`confine=True` and `has_seatbelt()`);
  `MacBackend.remember()`/`refresh()` carry the same declaration through every
  respawn, not just the first spawn. `sandbox_linux.build()` declares the same
  two facets, for symmetry, only when it actually emits `landlock+seccomp`
  (belt and braces — the Linux worker already self-reports both).
- `_preamble.apply_from_env()` copies `payload["confinement"]` verbatim into
  `REPORT["confinement"]` — a claim from whichever process actually applied
  the confinement, exactly like `landlock_abi`/`seccomp`, never computed by
  the worker itself.
- `denials.active_facets()` now also claims `filesystem`/`network` when the
  *parent* declared them in `report["confinement"]`, on top of the existing
  `landlock_abi`/`seccomp` self-report — closing M3's macOS gap noted below:
  the seatbelt is applied to the argv by the parent, before the worker ever
  runs, so `landlock_abi`/`seccomp` are always `None`/`None` there and the
  facet used to have no evidence to stand on.
- A second, related gap surfaced once the facet was live: macOS's seatbelt
  answers a plain `deny file-write*` with **EPERM** (errno 1), not Landlock's
  EACCES (errno 13), and `classify()`'s EPERM branch required a socket-call
  frame before naming anything — so a real seatbelt write denial still came
  back unlabelled even with the facet active. New `denials._names_a_path()`
  reads the same signal a socket frame gives the network branch: CPython's
  `OSError.__str__` appends the failing path only when the call that raised it
  named one (`open()`, never `os.kill()`), so an EPERM message ending in
  `: '<path>'` is real evidence of a file operation and now resolves to
  `filesystem`. Verified against the real sandbox-exec on this box (not
  simulated): `tests/test_sandbox.py::test_write_outside_roots_denied` and
  `::test_network_denied_worker_survives` now assert
  `err.details["denied"] == "filesystem"`/`"network"` and pass.

### M5 — stale text

- `kernel/sandbox.py`'s module docstring no longer says Linux and Windows
  "arrive in later slices"; its `plan()` comment no longer names `~/.agentcad`
  as a created write root.
- `docs/deployment.md`: `posture` added to the health example's
  `confinement.detail`, and the over-long line at the Landlock-requirements
  paragraph wrapped to the file's ~80-column convention.

## Files

- `agentcad/kernel/_confine.py` — pidfd/`process_madvise` numbers in both ARCH
  tables and in `_PEEK_SYSCALLS`; why-it-matters comment on the signal block
- `agentcad/kernel/sandbox.py` — `Backend.refresh()`, `SandboxPlan.spawn_env()`
  and `pool_size`, `plan(pool_size=)`, `report()`'s M1/M2/I2 rules, module
  docstring and the `plan()` write-root comment
- `agentcad/kernel/sandbox_linux.py` — `build(pool_size=)`,
  `_rlimits(quotas, pool_size)`, `LinuxBackend.remember()/refresh()`; F1's
  `payload["confinement"]` declaration (only when landlock+seccomp are
  genuinely emitted)
- `agentcad/kernel/sandbox_macos.py` — `build(pool_size=)`, module-level
  `_rlimits`, `MacBackend(quotas, pool_size)` + `refresh()`, docstring; F1's
  `payload["confinement"]` declaration and `MacBackend.remember()`
- `agentcad/kernel/sandbox_windows.py` — `build(pool_size=)`, ignored honestly
- `agentcad/kernel/client.py` — `pool_size` argument, `spawn_env()` at every
  spawn, `_emit_usage` on the four kill paths, `CONFINEMENT_STAGES` note
- `agentcad/kernel/pool.py` — passes `pool_size=self.size`
- `agentcad/kernel/_preamble.py` — the `landlock_root` stage; F1's
  `REPORT["confinement"]` copy-through
- `agentcad/kernel/denials.py` — `FACETS`, `active_facets`, per-facet
  `classify`; F1's parent-declared `confinement` facets and `_names_a_path()`
- `agentcad/kernel/worker.py` — `active_facets(SANDBOX_REPORT)`
- `agentcad/kernel/quotas.py` — the `pids_headroom` paragraph and the DEFAULTS
  comment
- `agentcad/cli.py` — `_accept_work_dir`, `_within`,
  `_refuse_state_dir_in_a_write_root`, `service.writable_roots`, the three
  commands' work-dir handling, `_writable_roots` without `~/.agentcad`
- `agentcad/core/checks.py` — module-level `refuse_work_dir_overlap`
- `agentcad/core/packages/gate.py` — module-level `refuse_work_dir_overlap`
- `agentcad/core/project.py` — `_TRIM_MIN_AGE_S` and `trim_cache(min_age_s=)`
- `.github/workflows/ci.yml` — macOS PR job timeout 20 → 30
- `AGENTS.md`, `docs/architecture.md`, `docs/deployment.md`,
  `docs/prd/in-progress/PRD-006-sandboxing-quotas.md` — the write roots, the
  fork-budget formula, the state-dir refusal, the `landlock_root` stage, the
  per-facet denials, the janitor floor, the pidfd family, the health example
- `tests/test_confine_unit.py` — the pidfd family denied on both arches
- `tests/test_sandbox_plan.py` — the scaled/re-measured fork budget on both
  backends, `pool_size` defaults, `landlock_root`, M1, M2, the roots pin; F1's
  macOS payload now carries `confinement`, and so does the (self-reporting)
  Linux one
- `tests/test_sandbox_linux.py` — the live pidfd-at-the-server case, a
  three-worker pool, the lost grant that stays confined, a work dir that exists
  at spawn
- `tests/test_sandbox.py` — F1: `details["denied"]` pinned to `"filesystem"`/
  `"network"` on the real seatbelt, and the preamble report's `confinement` key
- `tests/test_supervisor.py` — the breach, the timeout, the crash and the
  broken pipe all reaching the hook
- `tests/test_usage.py` — a killed request counts as an error and bills its
  wall clock; the assertion now tolerates the meter's own one-decimal rounding
  instead of comparing raw floats
- `tests/test_denials.py`, `tests/test_protocol_ids.py` — per-facet evidence;
  F1's parent-declared macOS facets and the EPERM-with-a-path case
- `tests/test_disk_budget.py` — a freshly written mesh is never trimmed
- `tests/test_checks_cli.py` — an accepted work dir exists before the spawn; a
  refused one costs no worker
- `tests/test_deploy_config.py` — the hosted state-dir refusal and the passing
  compose layout

## Notes

**Verification.** The fix-wave implementer's own harness refused every code-executing command, so the suites were run afterwards on its tree: by the controller for the wave alone (`make test` — 1 failed / 4193 passed / 36 skipped; `make test-linux` — 110 passed / 2 skipped) and by the F1 fixer on the finished tree (fix wave + F1): `make test` — **4195 passed, 36 skipped** (macOS; the one earlier failure — the single failure was `tests/test_usage.py`'s over-tight `pytest.approx` on the meter's rounded `wall_ms`, fixed with F1) and `make test-linux` — **111 passed, 2 skipped** inside `agentcad:local` (110/2 for the wave alone; the 3-worker pool case, the `pidfd_send_signal` denial and the `landlock_root` split all ran there).

**A deliberate consequence of M3, closed by F1.** The ruling's facet rules
keyed on the worker's own preamble report, and on **macOS** the seatbelt is
applied to the argv by the *parent* and leaves no trace in that report — so a
seatbelt `EACCES`/`EPERM` on macOS stopped carrying `details.denied` (the
error, its traceback and the Error Doctor's message-matched
`sandbox_write_denied` / `sandbox_network_denied` hints were always unchanged;
only the machine-readable word was missing). F1 closes it the way this note
predicted: the parent declares its confinement facets in the payload, the same
way it already declares parent-installed quota *tiers* (`payload["quotas"]`).

**F1's own verification, on this box's real seatbelt (not simulated).**
`uv run pytest tests/test_sandbox.py tests/test_sandbox_plan.py
tests/test_denials.py tests/test_protocol_ids.py tests/test_usage.py -q` — 150
passed, including the real `sandbox-exec` denial tests this fix targets. The
Linux confinement battery inside the shipped image is green too. The
authoritative full-suite counts on the finished tree: `make test` — 4195
passed, 36 skipped; `make test-linux` — 111 passed, 2 skipped.

**`KernelClient()` with no arguments is byte-identical.** `pool_size` is only
read when a plan exists, which needs `writable_dirs` or `quotas`; the
plan-free client keeps its historical argv, environment, 0.5 s poll and absent
supervisor.

**The build fan-out ruling still stands** — nothing here re-adds parallelism;
`build_configs` and the package gate are untouched.
