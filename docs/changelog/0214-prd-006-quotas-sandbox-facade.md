# 0214 — PRD-006 slice 1: quotas, the sandbox facade, the macOS backend, the private worker temp dir

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Claude

## Summary
The first slice of PRD-006 (sandboxing & quotas). `agentcad/kernel/sandbox.py`
stops being "the macOS seatbelt" and becomes the platform-independent seam the
rest of the PRD hangs off: it resolves **quotas**, creates a **private
per-worker temp dir**, builds the child's environment, and asks a **platform
backend** to confine the process and to say which quota tier it can actually
enforce. The v3 seatbelt profile moves — verbatim — into
`agentcad/kernel/sandbox_macos.py`, which also gains the macOS quota tier
(`RLIMIT_NPROC` sized from the live uid process count) and the live-process
helpers the (Slice 3) supervisor will sample through.

Nothing in this slice claims a capability it does not have: Linux and Windows
fall to a `NullBackend` that reports confinement `unsupported` until Slices 2
and 3 land their backends, and the rlimit payload the macOS backend emits is
*read* by nobody yet (the worker preamble is Slice 2).

## Changes

### New — `agentcad/kernel/quotas.py` (OCP-free)

- `DEFAULTS` is the measured table from the design spec's Decision 3:
  `memory_mb 2048`, `address_space_mb 0` (auto = 3 x memory), `pids 128`,
  `pids_headroom 64`, `cpu_percent 400`, `sample_interval_s 0.25`,
  `disk_mb 2048`.
- `resolve(overrides=None, *, env=None, config=None) -> Quotas` layers, lowest
  to highest: defaults < the instance config file's `{"quotas": {...}}` <
  `AGENTCAD_QUOTA_<KNOB>` env < `overrides` (the slot PRD-005's per-tenant
  limits plug into without a signature change). An unknown key is ignored at
  every layer; a blank env value reads as unset.
- Values are numbers or `"off"`. `cpu_percent` `0`/`"off"` resolves to `None`
  (no CPU quota); `address_space_mb` `0` means **auto** (`3 x memory_mb`) while
  an explicit `"off"` resolves it to `0`. A non-numeric value, a boolean
  (`True` is an `int` in Python, and `{"cpu_percent": true}` would cap CPU at
  1%), a negative number, a non-finite one, or a fractional count raises
  `ValueError` naming **both the key and the layer** (`env
  AGENTCAD_QUOTA_MEMORY_MB`, `config file`, `overrides`).
- `Quotas` is frozen. `Quotas.limits()` is the health dict — every cap, minus
  `sample_interval_s`, which is how often the supervisor *looks* rather than a
  limit anyone is subject to.
- `enforcement(quotas, tiers)` builds the `{"status", "mechanism", "limits"}`
  report from the tiers a backend can actually apply: `active` only when a
  tier exists, `mechanism` the tiers joined with `+` in tier order.

### New — `agentcad/kernel/sandbox_macos.py`

- `build_profile` and `_escape` **moved unchanged** from `sandbox.py`. The one
  semantic change is that the roots granted are exactly the ones handed in:
  the `tempfile.gettempdir()` that used to be appended inside the builder is
  gone. Verified by diffing the two implementations' output — old
  `build_profile(roots)` is byte-identical to new
  `build_profile(roots + [gettempdir()])`.
- `build(argv, write_roots, quotas, posture, server_pid, *, confine=True)`
  returns `(argv, env_additions, confinement, quotas_report, backend)`. It
  wraps the argv in `sandbox-exec -p <profile>` and reports
  `{"status": "active", "mechanism": "seatbelt", "detail": {"posture":
  "local"}}`; with `sandbox-exec` missing it reports `unsupported` and leaves
  the argv alone; with `confine=False` it reports `off` **and still emits the
  quotas** — opting out of the sandbox is not opting out of the caps.
- `env_additions["AGENTCAD_CONFINE"]` carries `{"rlimits": {"RLIMIT_NPROC":
  [n, n]}}` (hard == soft, so a script cannot raise it back), where `n` is the
  **live uid process count at spawn** plus `quotas.pids_headroom`.
  `RLIMIT_NPROC` is per-uid, not per-process — a fixed number killed the
  worker during `import build123d` in the spike.
- `live_uid_process_count()` uses libproc `proc_listpids(PROC_UID_ONLY, uid)`
  through `ctypes` (8192-pid buffer), falls back to `ps -u <uid> -o pid=` if
  the ctypes call fails, and to a deliberately generous 512 if both do —
  guessing low would kill the worker for no security gain.
- `MacBackend` implements the backend protocol: `rss_bytes` via
  `proc_pidinfo(pid, PROC_PIDTASKINFO)` reading `pti_resident_size` at offset
  8 (`None`, never 0, when it cannot measure); `explain_exit` maps
  `-SIGXCPU` to `{"reason": "cpu_cap", "tier": "rlimit"}` and nothing else;
  `attach`/`release` are no-ops (macOS has no cgroup and no job object).
- A `posture` other than `local` appends a warning rather than being reported
  as applied: macOS keeps the global-read profile, and the hosted allow-list
  is Linux-only.

### `agentcad/kernel/sandbox.py` — the facade

- `SandboxPlan`: `argv`, `env`, `tmp_dir`, `posture`, `confinement`, `quotas`
  (the report), `quotas_obj` (the resolved `Quotas` the supervisor reads
  numbers off), `warnings`, `backend`. `prepare_tmp()` (idempotent 0700
  mkdir, called before every spawn), `wipe_tmp()` (empty it, keep it — a
  respawn reuses the directory), `release()` (remove it, release the backend;
  idempotent).
- `plan(argv, writable_dirs, *, quotas=None, posture=None, server_pid=None)`:
  resolves quotas (a `Quotas`, a dict of overrides, or the configured
  layers), defaults the posture to `default_posture()`, creates
  `mkdtemp(prefix="agentcad-worker-")`, and exports
  `TMPDIR`/`TEMP`/`TMP`/`XDG_CACHE_HOME`/`HOME` at it plus
  `PYTHONDONTWRITEBYTECODE=1`. The write roots handed to the backend are
  `realpath(writable_dirs) + [realpath(tmp_dir)]` — **never**
  `gettempdir()` itself. A backend that raises does not leak the directory.
- `Backend` documents the protocol (`attach`, `rss_bytes`, `explain_exit`,
  `release`, `warnings`); `NullBackend` answers all of it, so the client and
  the supervisor need no platform branches.
- The platform switch is one dict — `_BACKENDS = {"darwin": "sandbox_macos"}`
  — imported lazily by name, so Slice 2 adds `"linux": "sandbox_linux"` and
  Slice 3 `"win32": "sandbox_windows"` with one line each. Anything else gets
  `NullBackend` + confinement `unsupported` naming the platform.
- `_opt_out_reason()` replaces the boolean-only `_disabled()` (kept, in terms
  of it) so an `off` confinement can say **why**: `AGENTCAD_NO_SANDBOX`, or
  the config file's `"sandbox": false`. Env still wins in both directions.
  Opting out no longer switches quotas off.
- `default_posture()` is `hosted` on a hosted instance, `local` otherwise; a
  malformed `AGENTCAD_MODE` falls back to `local` rather than making a worker
  unspawnable (server startup already refuses it, loudly).
- `wrap_argv`, `status`, `available`, `supported` keep their signatures and
  string semantics; `SANDBOX_EXEC` and `build_profile` stay importable from
  `sandbox` as re-exports. `supported()` is now explicit that Linux and
  Windows are `False` until their slices land.

### `agentcad/kernel/client.py`

- `KernelClient(..., *, writable_dirs=None, quotas=None, posture=None,
  on_usage=None, name=None)`. A plan is built when `writable_dirs` **or**
  `quotas` is given; with neither (the session `kernel` fixture) the argv, the
  environment (`None`) and the lifecycle are byte-for-byte the historical
  ones.
- `sandboxed` is now read from `plan.confinement["status"] == "active"` rather
  than from "argv[0] is sandbox-exec". `sandbox_report`, `last_usage`,
  `_on_usage` and `_breach` are declared for Slices 2–4.
- `_ensure_started` calls `plan.prepare_tmp()`, spawns with
  `{**os.environ, **plan.env}`, and calls `plan.backend.attach(proc)` right
  after `Popen` (never a `preexec_fn` — CPython documents it as unsafe in a
  threaded parent, and the server is threaded). `_kill()` wipes the private
  temp dir's contents and keeps the directory; `stop()` releases it.

### `agentcad/kernel/pool.py`

- `KernelPool(..., *, writable_dirs=None, quotas=None, posture=None,
  on_usage=None)` passes everything through and names each worker
  (`worker-0`, ...), so each plans its own private temp dir rather than
  sharing one. `sandbox_report` reads worker 0's, the one `start()` warms.

### `agentcad/cli.py`

- `_build_service(projects_dir, extra_writable=None, *, posture=None)`
  resolves `quotas_mod.resolve()` and `sandbox.default_posture()` once,
  before the workers spawn, and passes both to the client/pool. Every other
  call site is unchanged (keyword defaults).
- `cmd_serve` now stops the kernel when it exits. Every other command already
  did; `serve` did not, and since a worker now owns a directory it would have
  leaked one `agentcad-worker-*` dir per pool slot per run. Two paths, because
  one is not enough: Ctrl-C arrives as a `KeyboardInterrupt` and takes a
  `finally` around `uvicorn.run`, but **`docker stop` does not** — uvicorn's
  `capture_signals` restores the previous SIGTERM handler and re-raises the
  signal after its graceful shutdown, so with the default handler the process
  dies *inside* `uvicorn.run` with exit 143 and no `finally` runs (measured on
  uvicorn 0.52.1: three `agentcad-worker-*` dirs left behind by a real
  `agentcad serve` + `kill -TERM`). `cmd_serve` therefore installs the handler
  uvicorn restores — `sys.exit(128 + signum)` — and puts the previous one back
  afterwards. Re-measured: SIGTERM exit 143 and **0** dirs left, SIGINT exit 0
  and 0 dirs left.
- `_writable_roots`' docstring says why the system temp dir is still granted
  **by name** (the `agentcad check` / package-gate work cells materialize
  under it) and that nothing grants it implicitly any more.

### Tests

- `tests/test_quotas.py` (19 cases): the default table, each layer winning
  over the one below, `resolve()` with no arguments reading the real layers,
  unknown/blank values, every refusal, `limits()`, `enforcement()`.
- `tests/test_sandbox_plan.py` (30 cases): all-OS facade tests with the
  platform and the backend monkeypatched (private temp dir + 0700 + the five
  env vars, the write roots the backend is handed, `release`/`wipe_tmp`/
  `prepare_tmp`, both opt-outs, the unknown/linux/win32 fallbacks, the
  quotas on the plan, `default_posture`, the unchanged `wrap_argv`/`status`,
  `build_profile`'s roots, `cmd_serve`'s `finally` and its SIGTERM handler); the OCP-free probe
  (fresh interpreter, `OCP`/`build123d` blocked at `sys.meta_path`) for
  `quotas`, `sandbox` and `sandbox_macos`; and the darwin-only units for the
  macOS backend (the wrapped argv, the `AGENTCAD_CONFINE` payload, the hosted
  warning, `live_uid_process_count`, `rss_bytes`, `explain_exit`).
- `tests/test_sandbox.py` gains real sandboxed-worker coverage of the private
  temp dir: a script's `tempfile.gettempdir()` **is** the worker's own
  `agentcad-worker-*` dir and writing there succeeds; writing into the shared
  system temp dir (the v3 profile granted it) **and** into a sibling of it
  under the same `/var/folders` tree are both `PermissionError`; `stop()`
  removes the directory.
- `tests/test_sandbox.py`'s existing "write outside the roots is denied" test
  now spells the probe path out instead of expanding `~` **in the worker**:
  the child's `HOME` is its private temp dir, so `~` inside a script is
  somewhere it may write. The thing confinement protects — the developer's
  real home — is the parent process's `Path.home()`.

## Files
- `agentcad/kernel/quotas.py` — new: the knobs, the layered resolver, `Quotas`
- `agentcad/kernel/sandbox_macos.py` — new: the moved seatbelt profile, the
  macOS `build()`, `MacBackend`, libproc helpers
- `agentcad/kernel/sandbox.py` — facade: `SandboxPlan`, `Backend`,
  `NullBackend`, `plan()`, `default_posture()`, `_opt_out_reason()`
- `agentcad/kernel/client.py` — spawns through the plan; private temp dir
  lifecycle; `quotas`/`posture`/`on_usage`/`name` parameters
- `agentcad/kernel/pool.py` — passes them through, names its workers
- `agentcad/cli.py` — `_build_service` resolves quotas + posture; `cmd_serve`
  stops the kernel on both exit paths (`finally` + a SIGTERM handler);
  `_writable_roots` docstring
- `tests/test_quotas.py`, `tests/test_sandbox_plan.py` — new
- `tests/test_sandbox.py` — private-temp coverage, probe path fix

## Notes

**Verification.** `make test` — 3993 passed, 7 skipped (8:53 on 8 workers). An earlier run of the same tree reported 3991 passed with `tests/test_sketch_drag.py::test_the_cached_block_is_measurably_cheaper` red: it compares two wall-clock medians, is load-sensitive under `-n auto`, and passes in isolation (`tests/test_sketch_drag.py` — 17 passed). It touches nothing in this entry.
`uv run pytest tests/test_quotas.py tests/test_sandbox_plan.py
tests/test_sandbox.py -q` — 61 passed (19 + 30 + 12). `uv run ruff check` is clean on every
file this entry touches. The real app was driven, not just tested: `agentcad
serve --port 8641` reports `{"kernel": "ready", "sandbox": "active"}`, three
`agentcad-worker-*` dirs exist while it runs, a part created over the HTTP API
builds to `volume_mm3 = 1728.0`, and the script's own
`tempfile.gettempdir()` resolved to its worker's private directory.

**Deviations from the slice brief, and why.** (1) The backend `build()` takes a
keyword-only `confine` flag: the quotas must be computed even when the
operator opted out of confinement, and only the backend knows how to compute
them. (2) `SandboxPlan` grew `prepare_tmp()`/`wipe_tmp()` beside `release()`,
because the brief's `_kill()`-keeps-the-dir / `stop()`-removes-it rule needs
both, and a client that is stopped and started again must not hand a worker a
`$TMPDIR` that no longer exists. (3) `quotas.enforcement()` exists so the
`active`-only-if-a-tier-applies rule is written once rather than in each
backend. (4) `sandbox.report(kernel)` is **not** implemented here: it is the
health object, its shape depends on the worker's ping report, and the brief
defers it to Slice 3. `/api/health` still publishes the `sandbox` string.

**The private temp dir is a mechanism this slice installs, not yet a boundary
the shipped app gets.** `cli._writable_roots` still grants
`tempfile.gettempdir()` **by name**, because `agentcad check` and the package
gate materialize their work cells under it (`core/checks.py`,
`core/packages/gate.py` both `mkdtemp` there) and the profile is fixed at
spawn, before the runner picks a cell. While that grant stands, one worker can
still reach a sibling's private dir in a real `agentcad serve`. Closing it
means giving those two commands a work root that is known **before** the
kernel spawns, which touches `--work-dir`'s "never delete a directory it did
not create" contract — deliberately left to a later slice and called out here
so it is not mistaken for done.

**`AGENTCAD_NO_SANDBOX` opts out of confinement, not of quotas.** The env var
(and `{"sandbox": false}`) leaves the argv unwrapped and reports confinement
`off` with the reason, while the caps and the rlimit payload still apply. A
runaway script may not take the machine down whether or not the operator
trusts it with the filesystem.

**What Slice 2 picks up.** `AGENTCAD_CONFINE` is emitted and read by nobody:
the worker preamble that applies the rlimits (and, on Linux, Landlock +
seccomp) is Slice 2, as is the honest downgrade of `confinement.status` from
the worker's own ping report — until then `active` on a plan means *intended*,
which is why `client.sandboxed` carries the comment that says so.
