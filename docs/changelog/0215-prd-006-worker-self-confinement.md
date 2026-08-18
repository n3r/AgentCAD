# 0215 — PRD-006 slice 2: the worker confines itself (Landlock + seccomp), meters every request, names denials; unguessable request ids; the Linux test loop

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
Slice 1 built the seam; this slice puts something behind it on Linux. The
kernel worker now restricts **itself** — Landlock for the filesystem, a
seccomp-BPF filter for sockets and cross-process reach — in a preamble that
runs before `import build123d`, applies its rlimits through the same code path
on macOS, reports what it actually applied back to the client on `ping`, and
attaches a per-request `usage` object to every response line. A denial inside a
part script keeps being an ordinary `script_error` and gains one word
(`details.denied`) plus an Error Doctor hint. Request ids become
`secrets.randbits(62)`, which closes the forked-child protocol-forgery hole the
design flagged. `make test-linux` runs the new Linux battery inside
`agentcad:local`.

## Changes

### The worker confines itself (design spec, Decision 1)
- **New `agentcad/kernel/_confine.py`** — pure `ctypes`, no external binary,
  no capability. Per-arch syscall tables for `x86_64`/`aarch64`;
  `landlock_abi()` probe; `landlock_apply(read_roots, write_roots,
  extra_files)`; `seccomp_program(arch, server_pid)` (the BPF, built
  separately so it is unit-testable) and `seccomp_apply(server_pid)`;
  `apply_rlimits()`/`known_rlimits()` (POSIX, used on macOS too).
  - The handled-access mask comes from the **probed** ABI
    (`LANDLOCK_ABI_MASK`), never a constant: a bit the running kernel does not
    know makes `create_ruleset` EINVAL and takes the whole ruleset with it, and
    where the ABI does have `LANDLOCK_ACCESS_FS_TRUNCATE` (bit 14, ABI 3) every
    write root must be granted it — `open(path, "w")` sets `O_TRUNC`, and a
    write root without that right is EACCES on every truncating open (the spike
    measured it on `/proc/self/clear_refs`). `LANDLOCK_MIN_ABI = 3` is the
    design's floor and a refusal rather than a half-model: below it the right
    does not exist, so the preamble applies no ruleset and reports `off`.
  - `create_ruleset` is called with attr size 8 — accepted by every ABI.
  - `/dev/null` and `/proc/self/clear_refs` are granted as **file** rules with
    `FS_FILE` only: a directory-only right on a file rule is `EINVAL`, which is
    how those two grants fail if handed the full mask.
  - A missing path is recorded in `failed`, never fatal;
    `landlock_apply` raises only when `landlock_restrict_self` does.
  - seccomp: default ALLOW; `RET_KILL_PROCESS` on a foreign `AUDIT_ARCH` and on
    x32 (`nr >= 0x40000000`); `EPERM` for `socket`/`socketpair` in any family
    but `AF_UNIX`, for `kill`/`tkill`/`tgkill`/`rt_sigqueueinfo`/
    `rt_tgsigqueueinfo` at a negative pid, pid 0 or `server_pid`, and for
    `ptrace`/`process_vm_readv`/`process_vm_writev`/`pidfd_open`. Installed
    with `seccomp(SECCOMP_SET_MODE_FILTER=1, TSYNC)` — operation **1**; `2` is
    `SECCOMP_GET_ACTION_AVAIL` and answers `EOPNOTSUPP` — with the
    `prctl(PR_SET_SECCOMP, 2, …)` fallback.
- **New `agentcad/kernel/_preamble.py`** — `apply_from_env()` reads
  `AGENTCAD_CONFINE` and applies rlimits → Landlock → seccomp. It **never
  raises**: every stage failure lands in `REPORT["failures"]` and the next
  stage still runs. One stderr line, `[agentcad-sandbox] …`. Absent variable
  ⇒ complete no-op, so importing `worker` in-process changes nothing.
  A module-level `_APPLIED` flag makes it idempotent, because the worker
  module is imported twice in a worker process (once as `__main__`, again as
  `agentcad.kernel.worker` when `handlers/specs.py` does `from ..worker
  import …`) and confining twice would stack a second ruleset and a second
  filter and print the report line twice.
- **New `agentcad/kernel/sandbox_linux.py`** — the backend that *emits* the
  payload. The argv comes back unchanged (there is no wrapper binary);
  `env["AGENTCAD_CONFINE"]` carries `posture`, `rlimits` (`RLIMIT_AS` when
  `address_space_mb > 0`, `RLIMIT_NPROC` = `live_uid_process_count()` +
  `pids_headroom`, hard == soft), `landlock` and `seccomp`. Posture `local`
  reads `/`; posture `hosted` gets `HOSTED_READ_ROOTS` + `sys.prefix` +
  `sys.base_prefix` + `resource_root()` + the write roots, filtered to what
  exists. Confinement is `active` only with ABI ≥ 3 on a known machine, and
  `unsupported` names its reason. `LinuxBackend` answers the whole `Backend`
  protocol honestly (`rss_bytes`/`explain_exit` are Slice 3).
- `sandbox._BACKENDS` gains `"linux": "sandbox_linux"`; `sandbox.plan()`
  defaults `server_pid` to `os.getpid()` so a caller that omits it does not
  silently reduce the seccomp rule to "no signals at pid 0".

### Metering and denials (Decisions 6 and 9)
- **New `agentcad/kernel/_meter.py`** — `Meter.start()/finish()` →
  `{cpu_ms, wall_ms, peak_rss_mb, rss_mb, peak_rss_is_lifetime}`. On Linux it
  writes `5` to `/proc/self/clear_refs` at start (hence the Landlock file rule)
  and reads `VmHWM`/`VmRSS`, so the peak is genuinely per-request; elsewhere it
  is `ru_maxrss` and the flag says so. `ru_maxrss` is **bytes on macOS, KiB on
  Linux** and the branch is asserted on both.
- **New `agentcad/kernel/denials.py`** — `classify(exc_type, message, *,
  active)` → `network | filesystem | process_count | memory | None`. With
  nothing applied the answer is always `None`.
- `kernel/worker.py`: two lines at the top run the preamble before any
  geometry import; `handle_ping` result gains `sandbox` (the preamble's own
  report); `main()` meters every request and puts `usage` on the result, error
  **and** shutdown lines; `_script_error_from_exc` adds `details.denied`.
  Handler dictionaries and every handler pack are untouched.
- `kernel/error_doctor.py`: four entries, **first in the list** so an OS denial
  is never diagnosed as a boolean failure — `sandbox_network_denied`,
  `sandbox_write_denied`, `sandbox_process_cap`, `sandbox_memory_cap`. The
  network regex leads with `(?s)`: since Python 3.11 a global inline flag
  anywhere else in a pattern is a `re.error`.
- `kernel/client.py`: `sandbox_report` and `sandboxed` are now taken from the
  worker's own ping report (`sandboxed` requires no failures and, on Linux, a
  live Landlock ABI); every response's `usage` lands on `last_usage`.

### Unguessable request ids (design spec, "Risks")
- `client._request_locked` draws `secrets.randbits(62)` instead of a counter
  and records it as `_last_req_id`. A part script can `os.fork()` and the child
  inherits fd 1, so with a predictable id it could write the line the client is
  waiting for and have it accepted as the worker's answer — confinement cannot
  close that (a process may write to its own stdout), an unguessable id can.
  The worker already echoes `id` unchanged.

### The Linux loop
- **New `scripts/linux-test.sh`** + `make test-linux`: the tree is **copied**
  into `agentcad:local` (Docker Desktop's `fakeowner` virtiofs mounts are not
  Landlock-coherent — grants have no effect and even reads fail) and run with
  `PYTHONPATH` shadowing the image's baked-in package for both the server-side
  imports and the spawned worker. The image's venv ships no pip, so the script
  runs `ensurepip` first.

## Files
- `agentcad/kernel/_confine.py` — new: Landlock + seccomp + rlimits via ctypes
- `agentcad/kernel/_preamble.py` — new: `apply_from_env()`, `REPORT`
- `agentcad/kernel/_meter.py` — new: per-request CPU/wall/RSS
- `agentcad/kernel/denials.py` — new: `classify()`
- `agentcad/kernel/sandbox_linux.py` — new: the Linux backend and payload
- `agentcad/kernel/sandbox.py` — `_BACKENDS` gains linux; `server_pid` default
- `agentcad/kernel/worker.py` — preamble, ping report, usage envelope, `denied`
- `agentcad/kernel/error_doctor.py` — four sandbox entries, first in the list
- `agentcad/kernel/client.py` — random ids, live sandbox report, `last_usage`
- `scripts/linux-test.sh`, `Makefile` — `make test-linux`
- `AGENTS.md` — one line for the new target
- `docs/agent-api.md` — `details.denied` on the error-payload convention
- `tests/test_confine_unit.py` — new: the BPF interpreted on every OS
- `tests/test_denials.py`, `tests/test_meter.py`, `tests/test_protocol_ids.py`
  — new
- `tests/test_sandbox_linux.py` — new: the Linux battery (13 tests)
- `tests/test_sandbox_plan.py` — the Linux plan shape (all-OS, stubbed), the
  new modules in the OCP-free probe, `win32`-only unsupported case
- `tests/test_sandbox.py` — the preamble runs inside the seatbelt
- `tests/test_toolkit.py` — the four doctor regexes

## Notes
- **A real bug the battery caught.** The design's signal rule tested the *high*
  word of `args[0]` for a sign-extended negative pid. On arm64 that never
  fires: `mov w0, #-1` zeroes the top half of the register, so a negative
  `pid_t` arrives zero-extended, and `os.kill(-1, SIGKILL)` escaped the filter
  in the shipped image (measured: `ESRCH`, not `EPERM`). The filter now tests
  the **low** word with an unsigned `JGE 0x80000000`, which is exactly what the
  kernel truncates the argument to (`SYSCALL_DEFINE2(kill, pid_t, …)`) and is
  correct on both arches. `tests/test_confine_unit.py` pins both encodings.
- The BPF program is built by `seccomp_program()` and **interpreted** by a
  20-line BPF machine in the unit tests, on every OS, because a wrong jump
  offset is a silently permissive sandbox and a macOS dev box cannot install
  the filter to find out.
- `sandbox.supported()`/`status()` still answer `unsupported` on Linux: the
  health *object* (`sandbox.report(kernel)`) is a later slice and flipping the
  legacy string now would change `/api/health` ahead of it.
- macOS emits only `rlimits` in the payload (Slice 1 pinned that), so
  `REPORT["posture"]` is `None` there; the posture travels on the plan.
- The `agentcad:local` image predates the branch, which is why the loop copies
  the tree and sets `PYTHONPATH` rather than running the installed package.
- `make test` — **4055 passed, 22 skipped in 568.08s (0:09:28)** (macOS, full
  parallel suite).
- `make test-linux` — 58 passed, 1 skipped in 24.44 s inside `agentcad:local`
  (linuxkit 6.12 aarch64, uid 10001, Landlock ABI 6, `seccomp(2)` with TSYNC);
  `tests/test_sandbox_linux.py` alone: 13 passed in 18.86 s, none skipped
  (`AGENTCAD_EXPECT_SANDBOX=active` is set by the script, so a degradation to
  `off` would have been red rather than skipped).
