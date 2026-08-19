# Sandboxing & quotas (PRD-006) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confine kernel workers on Linux (Landlock + seccomp, in-process),
keep the macOS seatbelt, cap workers everywhere with honestly-named quota
tiers (delegated cgroup v2 · Linux rlimits · Windows job objects · a
parent-side RSS supervisor on all three), meter every request, surface
status + usage in `/api/health` and a `get_usage` tool, budget disk per
project — with breaches riding the existing structured errors and a green
build behaving identically (PRD-006, AC1–AC8 minus the Windows-AppContainer
clause carved out as PRD-006b).

**Architecture (one paragraph):** `kernel/sandbox.py` becomes a facade that
returns a `SandboxPlan` (argv, child env, private temp dir, a platform
backend) from `plan(argv, writable_dirs, quotas, posture)`; the backends are
`sandbox_macos.py` (the v3 seatbelt profile moved verbatim), `sandbox_linux.py`
(postures, rlimit values, cgroup tier, `/proc` RSS, exit explanation) and
`sandbox_windows.py` (job objects, psapi RSS). The **worker confines itself**:
`_preamble.apply_from_env()` runs at the top of `worker.py` before
`import build123d`, applying rlimits → Landlock → seccomp from the
`AGENTCAD_CONFINE` JSON in its environment via `_confine.py` (ctypes), and
reports what it applied in the `ping` result. `_meter.py` stamps a `usage`
envelope on every response. `KernelClient` gains a supervisor in its
request loop, breach attribution (`details.reason`), unguessable request
ids, an `on_usage` hook, and a `sandbox_report`. `core/usage.py` rolls usage
up per `(project, identity)` from a ContextVar scope; `ProjectStore` gains
disk budgets and a cache janitor; `/api/health` reports the sandbox object +
usage; `tools_usage.py` registers `get_usage`.

**Tech stack:** Python 3.12 (`ctypes`, `resource`, `struct`), FastAPI,
pytest (+xdist), Docker for the Linux loop (`agentcad:local` image),
GitHub Actions three-OS matrix.

**Spec:** [`docs/superpowers/specs/2026-08-18-sandboxing-quotas-design.md`](../specs/2026-08-18-sandboxing-quotas-design.md)
— every "Decision N" below refers to it. Read it first. The spike numbers it
quotes are the reason for every default.

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d**, and even inside
  `kernel/` the new modules `_confine.py`, `_preamble.py`, `_meter.py`,
  `denials.py`, `quotas.py`, `sandbox*.py` are **OCP-free and importable
  from the server process** (they run in both). `tests/test_toolkit_ocp_free.py`
  is the probe pattern; add the new modules to that probe.
- **`worker.py` gains exactly two lines at the top** (the preamble call,
  before `import build123d`), the `ping` report, the `usage` envelope in
  `main()`, and `details.denied` in `_script_error_from_exc`. Handler
  dictionaries and every handler pack are untouched (G5).
- **`KernelClient()` with no `writable_dirs` and no `quotas` is
  byte-identical to today** (historical argv, no plan, no supervisor). The
  session `kernel` fixture relies on it. `KernelClient.request()` still
  returns the `result` dict; usage travels on `.last_usage`, on
  `KernelError.details["usage"]`, and through the `on_usage` hook.
- **`sandbox.wrap_argv(argv, writable_dirs)`, `sandbox.status(sandboxed=None)
  -> str`, `sandbox.available()`, `sandbox.supported()` keep their
  signatures and string semantics** (`tests/test_sandbox.py` and
  `agentcad check` call them). The health *object* is a new function,
  `sandbox.report(kernel) -> dict`.
- **No `preexec_fn` anywhere** (Decision 1/4): rlimits are applied inside
  the worker by the preamble; cgroup placement is the parent writing
  `proc.pid` after `Popen`.
- **Never grant bare `/tmp` or `tempfile.gettempdir()`**; every worker gets
  a private `mkdtemp(prefix="agentcad-worker-")` dir that is the only temp
  root granted (Decision 1) and is removed on kill/stop.
- **Honesty (Decision 8):** `confinement.status == "active"` is set only
  from the worker's own ping report; a failed preamble is `off` +
  `warnings`. Never claim `active` from intent.
- **Naming:** the object is a *confinement* and a *quota tier*, `details.
  reason ∈ {memory_cap, pids_cap, cpu_cap}`, `details.denied ∈ {network,
  filesystem, process_count, memory}`; the posture is `local`/`hosted`.
  Mechanism strings: `seatbelt`, `landlock+seccomp`, `cgroup`, `rlimit`,
  `supervisor`, `job_object`, joined with `+` in tier order.
- **Text I/O always names `encoding="utf-8"`** (Windows CI, cp1252 trap).
- **Every commit stages a `docs/changelog/NNNN-<slug>.md`** (next free is
  0214) that cites `make test — N passed`; subagents do not run `git` or
  `uv sync` — the controller commits.
- **The Linux loop on this macOS box** is `make test-linux` (Slice 2 adds
  it): copies the tree into the container's overlayfs (Landlock is not
  coherent over macOS `fakeowner` bind mounts — the spike proved reads fail
  there too) and runs the Linux test files inside `agentcad:local`.

## Slice map

| # | Slice | Delivers | Runs where |
|---|---|---|---|
| 1 | Quotas config + sandbox facade + macOS backend + private temp | `quotas.py`, `SandboxPlan`, `sandbox_macos.py`, client spawn through the plan; macOS tests stay green | macOS |
| 2 | Worker self-confinement + metering + denials + unguessable ids | `_confine.py`, `_preamble.py`, `_meter.py`, `denials.py`, worker hooks, doctor entries, `make test-linux`, Linux battery | macOS unit + Docker |
| 3 | Linux/Windows backends + supervisor + breach attribution + usage hook | `sandbox_linux.py` (postures, rlimits, cgroup tier, explain_exit), `sandbox_windows.py`, client supervisor, `on_usage`, pool passthrough | macOS + Docker (+ Windows CI) |
| 4 | Service: usage meter, scope, health, `get_usage`, disk budgets, CLI wiring | `core/usage.py`, `tools_usage.py`, `app.py`, `tools.py`, `service.py`, `project.py`, `cli.py` | macOS |
| 5 | CI, image/compose, docs, acceptance battery, PRD close-out prep | `ci.yml`, `Dockerfile`/`compose.yaml`, deployment/architecture/AGENTS/CLAUDE/agent-api, `test_prd006_acceptance.py` | all |

Slices 1 → 2 → 3 are sequential (each builds on the last); 4 can start
after 3's client hook exists; 5 last.

---

## Slice 1 — quotas config, the sandbox facade, the macOS backend, the private temp dir

### Files
- Create: `agentcad/kernel/quotas.py`, `agentcad/kernel/sandbox_macos.py`
- Modify: `agentcad/kernel/sandbox.py` (facade), `agentcad/kernel/client.py`
  (spawn through the plan; private tmp), `agentcad/kernel/pool.py` (kwargs
  passthrough), `agentcad/cli.py:47-101` (`_build_service` resolves quotas +
  posture and passes them)
- Test: `tests/test_quotas.py`, `tests/test_sandbox_plan.py`, and
  `tests/test_sandbox.py` (existing — must stay green; add the private-tmp
  test)

### The shapes

```python
# agentcad/kernel/quotas.py — OCP-free
from dataclasses import dataclass, asdict

DEFAULTS = {
    "memory_mb": 2048,          # cgroup memory.max / supervisor cap / job-object commit
    "address_space_mb": 0,      # 0 = auto (3 × memory_mb); Linux RLIMIT_AS only
    "pids": 128,                # cgroup pids.max / job-object active processes
    "pids_headroom": 64,        # RLIMIT_NPROC = live uid count at spawn + headroom
    "cpu_percent": 400,         # cgroup cpu.max / job-object rate; None → no CPU quota
    "sample_interval_s": 0.25,  # supervisor
    "disk_mb": 2048,            # per-project budget (.cache + exports + imports)
}
ENV_PREFIX = "AGENTCAD_QUOTA_"   # AGENTCAD_QUOTA_MEMORY_MB=4096

@dataclass(frozen=True)
class Quotas:
    memory_mb: int
    address_space_mb: int       # resolved (never 0 unless disabled by explicit "off")
    pids: int
    pids_headroom: int
    cpu_percent: int | None
    sample_interval_s: float
    disk_mb: int
    def limits(self) -> dict: ...   # the health "limits" dict (asdict minus sample_interval_s)

def resolve(overrides: dict | None = None, *, env=None, config=None) -> Quotas:
    """defaults < config["quotas"] < AGENTCAD_QUOTA_* env < overrides.
    Values: ints (mb/pids/percent) or "off"/0 to disable a knob (cpu_percent → None,
    address_space_mb 0 → auto). Unknown keys are ignored with no error; a
    non-numeric value raises ValueError naming the key and layer."""
```

```python
# agentcad/kernel/sandbox.py — facade (docstring keeps the v3 wording, updated)
from dataclasses import dataclass, field

@dataclass
class SandboxPlan:
    argv: list[str]                     # what to Popen (macOS: sandbox-exec-wrapped)
    env: dict[str, str]                 # child env overrides (merged over os.environ by the client)
    tmp_dir: str                        # the private per-worker temp dir (already created)
    posture: str                        # "local" | "hosted"
    confinement: dict                   # {"status": "active"|"off"|"unsupported", "mechanism": str|None, "detail": dict}  — INTENDED
    quotas: dict                        # {"status": "active"|"off", "mechanism": str, "limits": dict}
    warnings: list[str] = field(default_factory=list)
    backend: "Backend" = None           # platform object (below)
    def release(self) -> None: ...      # rm the tmp dir, backend.release()

class Backend:                          # protocol implemented by the three modules
    def attach(self, proc) -> None: ...             # after Popen (cgroup.procs, job object)
    def rss_bytes(self, proc) -> int | None: ...    # supervisor sample
    def explain_exit(self, proc, returncode) -> dict | None: ...  # {"reason":..., "tier":...} or None
    def release(self) -> None: ...

def plan(argv, writable_dirs, *, quotas=None, posture=None, server_pid=None) -> SandboxPlan
def wrap_argv(argv, writable_dirs) -> list[str]        # unchanged semantics; delegates to plan(...).argv on macOS
def supported() -> bool                                # macOS with sandbox-exec, Linux with landlock ABI>=3 on x86_64/aarch64, else False (Windows: False — confinement)
def available() -> bool                                # supported() and not _disabled()
def status(sandboxed: bool | None = None) -> str       # unchanged
def report(kernel) -> dict                             # health object: from kernel.sandbox_report/plan; see Slice 3
def default_posture() -> str                           # "hosted" if resolve_mode().hosted else "local"
```

`plan()` rules: `posture = posture or default_posture()`; `tmp_dir =
tempfile.mkdtemp(prefix="agentcad-worker-")`; `env` always sets
`TMPDIR/TEMP/TMP/XDG_CACHE_HOME/HOME = tmp_dir` and
`PYTHONDONTWRITEBYTECODE=1`; the write roots handed to the backend are
`realpath(writable_dirs) + [tmp_dir]` (never `gettempdir()`); the backend
is chosen by `sys.platform` (`darwin` → `sandbox_macos.build(...)`, `linux`
→ `sandbox_linux.build(...)`, `win32` → `sandbox_windows.build(...)`, else a
`NullBackend` with confinement `unsupported`, quotas `supervisor` only). When
`_disabled()`: confinement `{"status": "off", ...}`, argv unwrapped, no
`AGENTCAD_CONFINE` confinement keys — but **quotas still apply**
(`AGENTCAD_NO_SANDBOX` opts out of confinement, not of caps; document).

```python
# agentcad/kernel/sandbox_macos.py — the v3 profile, moved verbatim
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
def build_profile(writable_dirs: list[str]) -> str   # byte-identical to today's, except the roots list is exactly what it is given (the caller adds tmp_dir; no gettempdir())
def build(argv, write_roots, quotas, posture, server_pid) -> tuple[list[str], dict, dict, dict, "MacBackend"]
    # returns (wrapped_argv, env_additions, confinement, quotas_report, backend)
    # confinement: {"status": "active", "mechanism": "seatbelt", "detail": {"posture": "local"}}  (macOS is local-only; posture "hosted" → warnings += "macOS keeps the local read posture")
    # env_additions["AGENTCAD_CONFINE"] = json of {"rlimits": {"RLIMIT_NPROC": [n, n]}} where n = live uid process count + quotas.pids_headroom (count via `os.listdir` of `/proc`? no /proc on macOS — use `subprocess.run(["ps","-u",str(os.getuid()),"-o","pid="])` once per spawn, or libproc `proc_listpids(PROC_UID_LIST_TYPE, uid)`; use libproc)
class MacBackend(Backend):
    def rss_bytes(self, proc):   # ctypes libproc proc_pidinfo(pid, 4, 0, buf, 256) → struct.unpack_from("<Q", buf.raw, 8)[0]; None if n <= 0
    def explain_exit(self, proc, rc):  # rc == -signal.SIGXCPU → {"reason": "cpu_cap", "tier": "rlimit"} ; else None
```

The v3 profile text (`build_profile`) moves out of `sandbox.py`
unchanged; `sandbox.build_profile` stays importable as a re-export
(`tests/test_sandbox.py` imports it — check with `grep -n build_profile
tests/`).

```python
# agentcad/kernel/client.py — constructor and spawn
class KernelClient:
    def __init__(self, python_exe=None, timeout_s=60.0, *, writable_dirs=None,
                 quotas=None, posture=None, on_usage=None, name=None):
        ...
        base = worker_argv(self._python)
        self._plan = None
        if writable_dirs is not None or quotas is not None:
            self._plan = sandbox.plan(base, list(writable_dirs or []), quotas=quotas,
                                      posture=posture, server_pid=os.getpid())
        self._argv = self._plan.argv if self._plan else base
        # intended confinement; refined from the ping report after start()
        self.sandboxed: bool = bool(self._plan and self._plan.confinement["status"] == "active")
        self.sandbox_report: dict | None = None      # worker's own report (Slice 2)
        self.last_usage: dict | None = None          # Slice 2/3
        self._on_usage = on_usage
        self._breach: tuple[str, dict] | None = None # Slice 3
```
`_ensure_started`: `env = {**os.environ, **self._plan.env}` when a plan
exists (else `None` as today); after `Popen`, `self._plan.backend.attach(proc)`;
`stop()` → `self._plan.release()` (tmp dir removed); `_kill()` keeps the tmp
dir (a respawn reuses it) but wipes its contents. `pool.py`: `KernelPool(
size, python_exe, timeout_s, *, writable_dirs=None, quotas=None, posture=None,
on_usage=None)` passes everything through; `.sandbox_report` from worker 0.

`cli._build_service(projects_dir, extra_writable=None, *, posture=None)`:
`quotas = quotas_mod.resolve()`; `posture = posture or sandbox.default_posture()`;
constructs the client/pool with `quotas=quotas, posture=posture`
(`on_usage` comes in Slice 4). Every other `_build_service` call site is
unchanged (keyword defaults).

### Tasks
- [ ] **Step 1:** `tests/test_quotas.py` (failing): defaults resolve to the table;
  env `AGENTCAD_QUOTA_MEMORY_MB=4096` wins over config `{"quotas": {"memory_mb": 1024}}`;
  `overrides={"pids": 32}` wins over env; `cpu_percent="off"` → `None`;
  `address_space_mb` auto = 3 × memory; `"memory_mb": "lots"` → `ValueError`
  mentioning `memory_mb`; `.limits()` has no `sample_interval_s`.
- [ ] **Step 2:** implement `quotas.py`; run `uv run pytest tests/test_quotas.py -q` → green.
- [ ] **Step 3:** `tests/test_sandbox_plan.py` (failing, all-OS, monkeypatch
  `sys.platform` and the backend modules where a real OS call would run):
  `plan()` creates a tmp dir under `tempfile.gettempdir()` named
  `agentcad-worker-*`, `env["TMPDIR"] == tmp_dir`, `PYTHONDONTWRITEBYTECODE`
  set, `release()` removes it; the write roots handed to the backend do
  **not** contain `tempfile.gettempdir()` itself; `AGENTCAD_NO_SANDBOX=1` →
  `confinement.status == "off"` and argv unwrapped, quotas still `active`;
  unknown platform → `unsupported`; `default_posture()` follows
  `AGENTCAD_MODE`.
- [ ] **Step 4:** move the seatbelt into `sandbox_macos.py`, write the facade,
  keep `wrap_argv`/`status`/`available`/`supported`/`build_profile` names
  importable from `sandbox`; run `tests/test_sandbox_plan.py` → green.
- [ ] **Step 5:** wire `client.py`/`pool.py`/`cli.py`; run
  `uv run pytest tests/test_sandbox.py tests/test_pool.py tests/test_server.py -q`
  → green (macOS). Add to `tests/test_sandbox.py`: a sandboxed worker's
  script writing `os.path.join(tempfile.gettempdir(), "x")` **succeeds** (the
  granted temp is `$TMPDIR` = the private dir) and the dir name starts with
  `agentcad-worker-`; writing to the *system* temp's parent sibling
  (`Path(tempfile.gettempdir()).parent / "agentcad-probe"` — a different
  dir under the same `/var/folders/...` tree) is `PermissionError`.
- [ ] **Step 6:** `make test` (cite count) → controller commits with changelog 0214.

### Verification
`uv run pytest tests/test_quotas.py tests/test_sandbox_plan.py tests/test_sandbox.py -q`
green; `agentcad serve` still starts; `/api/health` unchanged so far
(`sandbox` is still the string).

---

## Slice 2 — the worker confines itself, meters, classifies denials; unguessable ids; the Linux loop

### Files
- Create: `agentcad/kernel/_confine.py`, `agentcad/kernel/_preamble.py`,
  `agentcad/kernel/_meter.py`, `agentcad/kernel/denials.py`,
  `scripts/linux-test.sh` (+ `Makefile` target `test-linux`),
  `tests/test_confine_unit.py`, `tests/test_denials.py`, `tests/test_meter.py`,
  `tests/test_sandbox_linux.py`, `tests/test_protocol_ids.py`
- Modify: `agentcad/kernel/worker.py` (top two lines; `handle_ping`; `main()`
  envelope; `_script_error_from_exc`), `agentcad/kernel/error_doctor.py`
  (+4 entries), `agentcad/kernel/client.py` (random ids; ping report;
  `last_usage`), `tests/test_toolkit_ocp_free.py` (probe the new modules),
  `tests/test_error_doctor.py`

### The shapes

```python
# agentcad/kernel/_confine.py — Linux only at call time; importable everywhere (no import-time syscalls)
ARCH = {"x86_64": {"audit": 0xC000003E, "socket": 41, "socketpair": 53, "kill": 62, "tkill": 200, "tgkill": 234,
                   "rt_sigqueueinfo": 129, "rt_tgsigqueueinfo": 297, "ptrace": 101, "process_vm_readv": 310,
                   "process_vm_writev": 311, "pidfd_open": 434, "seccomp": 317,
                   "landlock_create_ruleset": 444, "landlock_add_rule": 445, "landlock_restrict_self": 446},
        "aarch64": {"audit": 0xC00000B7, "socket": 198, "socketpair": 199, "kill": 129, "tkill": 130, "tgkill": 131,
                    "rt_sigqueueinfo": 138, "rt_tgsigqueueinfo": 240, "ptrace": 117, "process_vm_readv": 270,
                    "process_vm_writev": 271, "pidfd_open": 434, "seccomp": 277,
                    "landlock_create_ruleset": 444, "landlock_add_rule": 445, "landlock_restrict_self": 446}}
LANDLOCK_ABI_MASK = {1: 0x1FFF, 2: 0x3FFF, 3: 0x7FFF, 4: 0x7FFF, 5: 0xFFFF, 6: 0xFFFF}   # FS bits per ABI (4 adds net, 5 adds IOCTL_DEV=bit15, 6 adds scope)
FS_READ = 0b1000 | 0b100 | 0b1  # READ_FILE(0x4)|READ_DIR(0x8)|EXECUTE(0x1)  → 0xD
FS_TRUNCATE = 1 << 14

def landlock_abi() -> int            # syscall probe; <=0 → not available (returns 0 on ENOSYS/EOPNOTSUPP)
def landlock_apply(read_roots, write_roots, extra_files) -> dict
    # {"abi": 6, "applied": True, "rules": n, "failed": [(path, errno_name)...]}; raises OSError only if restrict_self fails
def seccomp_program(arch: str, server_pid: int) -> bytes   # the BPF, unit-testable
def seccomp_apply(server_pid: int) -> str                  # "seccomp(2)" | "prctl"; raises OSError on failure
def apply_rlimits(rlimits: dict[str, list[int]]) -> list[str]  # names applied (soft=hard); skips names missing on the platform; used on macOS+Linux
```
BPF (spike-verified core, extended per Decision 1/11): load arch → JEQ
expected else `RET_KILL_PROCESS`; load nr → JGE 0x40000000 → kill; nr in
{socket, socketpair}: load args[0] (offset 16 low word) → JEQ AF_UNIX(1) →
ALLOW else `RET_ERRNO(EPERM)`; nr in {kill, tkill, tgkill, rt_sigqueueinfo,
rt_tgsigqueueinfo}: load args[0] low32 → JEQ 0 → EPERM; load args[0]
high32 (offset 20) → JEQ 0xFFFFFFFF (negative) → EPERM; low32 JEQ
server_pid → EPERM; else ALLOW; nr in {ptrace, process_vm_readv,
process_vm_writev, pidfd_open} → EPERM; default ALLOW. Encode with
`struct.pack("=HBBI", code, jt, jf, k)`; `sock_fprog =
struct.pack("=HxxxxxxQ", n, addr)`; `seccomp(1, SECCOMP_FILTER_FLAG_TSYNC=1,
&fprog)` after `prctl(PR_SET_NO_NEW_PRIVS=38, 1)`; fallback
`prctl(PR_SET_SECCOMP=22, 2, &fprog)`. Return codes: `SECCOMP_RET_ALLOW
= 0x7FFF0000`, `SECCOMP_RET_ERRNO = 0x00050000 | errno`,
`SECCOMP_RET_KILL_PROCESS = 0x80000000`. Landlock: `create_ruleset(&attr(8
bytes: handled_access_fs), 8, 0)`; per path `fd = os.open(p, O_PATH |
O_CLOEXEC)`; `add_rule(rfd, LANDLOCK_RULE_PATH_BENEATH=1, struct.pack("=QI",
access, fd), 0)`; `restrict_self(rfd, 0)`. Missing paths are recorded in
`failed`, not fatal (a write root that does not exist yet is created by the
client before spawn — `plan()` does `os.makedirs(root, exist_ok=True)` for
each writable dir it is given).

```python
# agentcad/kernel/_preamble.py
ENV = "AGENTCAD_CONFINE"
REPORT: dict = {}     # what was applied; worker.handle_ping copies it into the result under "sandbox"
def apply_from_env() -> dict:
    """No-op (REPORT = {}) when ENV is absent/empty. Otherwise, in order:
    rlimits → landlock (linux, when 'landlock' key present) → seccomp (linux, when 'seccomp' key present).
    Never raises: every failure is recorded in REPORT["failures"] as {"stage", "error"} and printed once to stderr
    as one line '[agentcad-sandbox] ...'. Sets sys.dont_write_bytecode = True."""
```
Payload keys (the client writes them, Slice 1/3): `posture`, `rlimits`
(`{"RLIMIT_AS": [soft, hard], "RLIMIT_NPROC": [...]}`), `landlock:
{"read_roots": [...], "write_roots": [...], "extra_files": [...]}`,
`seccomp: {"server_pid": int}`. REPORT shape: `{"posture", "rlimits":
[names], "landlock_abi": int|None, "seccomp": "seccomp(2)"|"prctl"|None,
"failures": [...]}` — Slice 2 already emits `rlimits` on macOS through the
same code path (Slice 1's macOS backend puts `RLIMIT_NPROC` in the
payload), which is how the design's "macOS preamble runs inside the
seatbelt" claim gets verified here.

```python
# agentcad/kernel/_meter.py
class Meter:
    def start(self) -> None      # perf_counter, getrusage(RUSAGE_SELF); on Linux write "5\n" to /proc/self/clear_refs (ignore OSError; record self.peak_reset_ok)
    def finish(self) -> dict     # {"cpu_ms", "wall_ms", "peak_rss_mb", "rss_mb", "peak_rss_is_lifetime": bool}
                                 # Linux: VmHWM/VmRSS from /proc/self/status (kB); lifetime flag = not peak_reset_ok
                                 # macOS: ru_maxrss BYTES; rss_mb via libproc self pid; lifetime True
                                 # Windows: psapi GetProcessMemoryInfo(GetCurrentProcess()) PeakWorkingSetSize/WorkingSetSize; lifetime True
```

```python
# agentcad/kernel/denials.py
def classify(exc_type: str, message: str, *, active: bool) -> str | None
    # active=False → None. "PermissionError" + "[Errno 1]"|"Operation not permitted" → "network"
    # "PermissionError" + "[Errno 13]"|"Permission denied" → "filesystem"
    # "BlockingIOError"|"OSError" + ("[Errno 11]"|"[Errno 35]"|"Resource temporarily unavailable") → "process_count"
    # "MemoryError" → "memory" ; else None
```
Worker: `_script_error_from_exc` adds `details["denied"] = classify(type(exc).__name__, str(exc), active=bool(_preamble.REPORT))` when not None. `handle_ping` result gains `"sandbox": dict(_preamble.REPORT)`. `main()`: `m = Meter(); m.start()` before `_dispatch`, `response["usage"] = m.finish()` for both result and error lines (ping included). Error Doctor entries (ids double as telemetry): `sandbox_network_denied` (regex `PermissionError: \[Errno 1\] Operation not permitted(?s).*(socket|urlopen|connect|getaddrinfo)`), `sandbox_write_denied` (`PermissionError: \[Errno 13\] Permission denied`), `sandbox_process_cap` (`\[Errno (11|35)\] Resource temporarily unavailable`), `sandbox_memory_cap` (`^MemoryError`) — each with the plain-language diagnosis from the PRD's experience section and a fix ("the kernel sandbox blocks network access; fetch data on the agent side and pass it as a parameter", "write only under the project or the temp dir (`tempfile.gettempdir()`)", "the worker's process cap stopped a fork loop; do not fork inside a part script", "the script exceeded the worker's memory cap; reduce mesh resolution / split the part / raise `AGENTCAD_QUOTA_MEMORY_MB`").

Client (`_request_locked`): `req_id = secrets.randbits(62)` (not the
counter); response matching unchanged; after a successful `ping` in
`_ensure_started`, `self.sandbox_report = result.get("sandbox") or {}` and
`self.sandboxed = self.sandboxed and not self.sandbox_report.get("failures")
and (sys.platform != "linux" or self.sandbox_report.get("landlock_abi"))`;
every response's `usage` → `self.last_usage`.

`scripts/linux-test.sh`:
```bash
#!/bin/sh
# Run the Linux sandbox tests inside the shipped image on a macOS dev box.
# The tree is COPIED into the container (Landlock is not coherent over Docker Desktop bind mounts).
set -eu
IMAGE=${AGENTCAD_LINUX_IMAGE:-agentcad:local}
REPO=$(cd "$(dirname "$0")/.." && pwd)
exec docker run --rm -v "$REPO":/src:ro -e AGENTCAD_EXPECT_SANDBOX=active -e AGENTCAD_EXPECT_QUOTAS=active \
  -w /work "$IMAGE" sh -c '
    cp -r /src /work-src && rm -rf /work && mv /work-src /work && cd /work \
    && python -m pip install -q --disable-pip-version-check pytest pytest-timeout \
    && PYTHONPATH=/work python -m pytest -q -p no:cacheprovider '"${*:-tests/test_sandbox_linux.py tests/test_confine_unit.py tests/test_denials.py tests/test_meter.py}"
```
(Makefile: `test-linux: ; sh scripts/linux-test.sh`.) Note the image
predates the branch; `PYTHONPATH=/work` shadows the baked-in package for
both the server-side imports and the spawned worker (`sys.executable -u -m
agentcad.kernel.worker` inherits the env).

### Tasks
- [ ] **Step 1:** `tests/test_confine_unit.py` (all-OS, pure): `seccomp_program("x86_64", 4242)`
  decodes (with `struct.iter_unpack("=HBBI", ...)`) to a program whose first
  instruction loads offset 4, whose kill instructions have `k == 0x80000000`,
  which contains `RET_ERRNO(EPERM) == 0x00050001`, and which references
  `4242` and `AF_UNIX (1)`; `LANDLOCK_ABI_MASK[3] & FS_TRUNCATE` is truthy
  and `LANDLOCK_ABI_MASK[2] & FS_TRUNCATE` is 0; `apply_rlimits({"RLIMIT_NOFILE": [512, 512]})`
  in a subprocess reports the limit (POSIX); on Windows it returns `[]`.
- [ ] **Step 2:** `tests/test_denials.py`: the four classifications + `active=False → None`.
- [ ] **Step 3:** `tests/test_meter.py`: `Meter().start(); ...; finish()` keys and
  types; `peak_rss_is_lifetime` is `False` only on Linux when `clear_refs`
  succeeded; `cpu_ms >= 0`; on macOS `peak_rss_mb` uses bytes→MB (patch
  `resource.getrusage` to a namespace with `ru_maxrss=1_048_576` → `1.0` on
  darwin, `1024.0` on linux).
- [ ] **Step 4:** `tests/test_protocol_ids.py`: two consecutive requests on the
  session `kernel` have non-consecutive, ≥ 2^40 ids (patch `json.dumps` via
  a spy on `proc.stdin.write`? Simpler: assert `client._last_req_id` values
  differ by more than 1 and are ≥ 2**40 after two requests — add
  `_last_req_id` for tests).
- [ ] **Step 5:** implement `_confine.py`, `_preamble.py`, `_meter.py`, `denials.py`,
  worker hooks, doctor entries, client ids/report/last_usage; extend
  `tests/test_error_doctor.py` with the four regexes; run the four unit
  files + `tests/test_error_doctor.py` + `tests/test_sandbox.py` on macOS →
  green. Confirm on macOS that a sandboxed client's `sandbox_report["rlimits"]
  == ["RLIMIT_NPROC"]` (the preamble ran inside the seatbelt — Decision 1's
  verification point) — add that assertion to `tests/test_sandbox.py`.
- [ ] **Step 6:** `tests/test_sandbox_linux.py` (`pytestmark = [portability, integration,
  slow, skipif(sys.platform != "linux")]`). Fixtures: `roots` (tmp project
  dir), `confined` = `KernelClient(writable_dirs=[roots], quotas=Quotas(memory_mb=1024, ...))`
  started once per module (module-scoped; `stop()` at teardown). Tests
  (each drives the real worker with a part script; `mesh_path` under
  `roots/.cache`):
  - `test_ping_reports_landlock_and_seccomp`: `sandbox_report["landlock_abi"] >= 3`,
    `["seccomp"] in ("seccomp(2)", "prctl")`, `failures == []`; and if
    `os.environ.get("AGENTCAD_EXPECT_SANDBOX") == "active"` then `client.sandboxed is True`
    (else skip with the reason printed — Decision 13's honesty gate).
  - `test_network_is_denied`: script does `socket.create_connection(("1.1.1.1", 80), timeout=2)` →
    `KernelError` `script_error` with `details["denied"] == "network"` and the hint mentions "sandbox";
    then a normal build succeeds (worker alive).
  - `test_write_outside_roots_is_denied`: `open("/app/pwned"|"/usr/pwned"|str(Path.home()/"pwned"), "w")` →
    `denied == "filesystem"`; `Path("/usr/pwned").exists() is False`.
  - `test_private_tmp_is_the_only_temp`: script writes `tempfile.gettempdir()/x` → ok and that dir
    name starts with `agentcad-worker-`; script writes `/tmp/agentcad-other/x` (mkdir by the test) → denied.
  - `test_kill_broadcast_is_denied`: `os.kill(-1, signal.SIGKILL)` → `PermissionError` script_error; the
    test process is still alive (obviously) and the worker survives.
  - `test_fork_child_inherits`: `os.fork()` child tries `open("/usr/pwned","w")`, parent waits and
    raises `RuntimeError(f"child {status}")` → message shows a non-zero exit; a `subprocess.run([sys.executable, "-c", "import socket; socket.socket()"])`
    returns non-zero.
  - `test_hosted_posture_hides_state_dir`: a second module-scoped client with `posture="hosted"` and a fake
    state dir `tmp/state/secret.key` written by the test; script `open(secret_path).read()` → `denied == "filesystem"`
    (a **read** denial under the allow-list), while a normal build succeeds and `open("/etc/hostname")` reads.
  - `test_no_sandbox_env_reports_off`: with `AGENTCAD_NO_SANDBOX=1` at construction, `sandboxed is False`,
    `sandbox_report.get("landlock_abi") is None`, and `socket.socket()` in a script succeeds (creating a socket
    is not a network round trip).
- [ ] **Step 7:** `scripts/linux-test.sh` + `make test-linux`; run it → the Slice-2 subset of
  `test_sandbox_linux.py` green inside `agentcad:local` (the rlimit/supervisor tests come in Slice 3 —
  mark them `xfail(strict=False)` for now or write them in Slice 3). Paste the run's tail into the changelog.
- [ ] **Step 8:** `make test` on macOS (cite) → controller commits with changelog 0215.

### Verification
`make test-linux` green for the battery; `make test` green on macOS; the
session `kernel` fixture (no plan) shows `sandbox_report == {}` and every
response still carries `usage`.

---

## Slice 3 — Linux and Windows backends, the supervisor, breach attribution, the usage hook

### Files
- Create: `agentcad/kernel/sandbox_linux.py`, `agentcad/kernel/sandbox_windows.py`,
  `tests/test_supervisor.py`, `tests/test_sandbox_windows.py`
- Modify: `agentcad/kernel/client.py` (supervisor loop, `_breach`, `explain_exit`,
  `on_usage`, `details.usage` on errors), `agentcad/kernel/pool.py`,
  `agentcad/kernel/sandbox.py` (`report()`), `tests/test_sandbox_linux.py`
  (rlimit/cgroup/supervisor tests), `tests/test_sandbox_plan.py`

### The shapes

```python
# agentcad/kernel/sandbox_linux.py
HOSTED_READ_ROOTS = ["/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", "/etc", "/opt", "/proc", "/dev", "/sys"]
def read_roots(posture: str, projects_roots: list[str], tmp_dir: str) -> list[str]
    # local → ["/"]; hosted → HOSTED_READ_ROOTS (existing ones) + [sys.prefix, sys.base_prefix, str(resource_root()), *projects_roots, tmp_dir]
def live_uid_process_count() -> int      # count /proc/[0-9]* whose status Uid line matches os.getuid(); fallback 256
def build(argv, write_roots, quotas, posture, server_pid) -> tuple[list[str], dict, dict, dict, "LinuxBackend"]
    # argv unchanged; env["AGENTCAD_CONFINE"] = json({posture, rlimits, landlock:{read_roots, write_roots, extra_files:["/dev/null","/proc/self/clear_refs"]}, seccomp:{server_pid}})
    # rlimits: RLIMIT_AS = address_space_mb (if > 0), RLIMIT_NPROC = live_uid_process_count() + pids_headroom
    # confinement intended: {"status": "active" if landlock_abi()>=3 and machine in ARCH else "unsupported"/"off", "mechanism": "landlock+seccomp", "detail": {"landlock_abi": abi}}
    # quotas: mechanism "cgroup+rlimit+supervisor" | "rlimit+supervisor"; status "active"
class CgroupTier:
    @staticmethod
    def probe() -> "CgroupTier | None"     # Decision 4: AGENTCAD_CGROUP_DIR, then own delegated cgroup (creates <own>/server, migrates self, enables +memory +pids +cpu); every step verified; None on any failure (reason kept for warnings)
    def make_worker(self, name: str, quotas) -> str   # mkdir <root>/<name>; write memory.max, memory.swap.max=0, pids.max, cpu.max ("{cpu_percent*1000} 100000" or "max"); returns dir
    def attach(self, cg_dir, pid) -> None             # write pid to cgroup.procs
    def oom_kills(self, cg_dir) -> int                # memory.events "oom_kill N"
    def release(self, cg_dir) -> None                 # rmdir (ignore EBUSY)
class LinuxBackend(Backend):
    def attach(self, proc): if self.cg: self.cg.attach(self.cg_dir, proc.pid); self._oom0 = self.cg.oom_kills(self.cg_dir)
    def rss_bytes(self, proc): read f"/proc/{proc.pid}/statm" field 2 × os.sysconf("SC_PAGE_SIZE"); None on OSError
    def explain_exit(self, proc, rc):
        # cgroup oom_kills delta > 0 → {"reason": "memory_cap", "tier": "cgroup"}
        # rc == -SIGXCPU → {"reason": "cpu_cap", "tier": "rlimit"}; else None
```

```python
# agentcad/kernel/sandbox_windows.py — ctypes.windll only inside functions
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8; JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100; JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9; JobObjectCpuRateControlInformation = 15
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1; JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
class IO_COUNTERS(ctypes.Structure): _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount","WriteOperationCount","OtherOperationCount","ReadTransferCount","WriteTransferCount","OtherTransferCount")]
class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure): _fields_ = [("PerProcessUserTimeLimit", c_longlong), ("PerJobUserTimeLimit", c_longlong), ("LimitFlags", c_uint32), ("MinimumWorkingSetSize", c_size_t), ("MaximumWorkingSetSize", c_size_t), ("ActiveProcessLimit", c_uint32), ("Affinity", c_size_t), ("PriorityClass", c_uint32), ("SchedulingClass", c_uint32)]
class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure): _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", c_size_t), ("JobMemoryLimit", c_size_t), ("PeakProcessMemoryUsed", c_size_t), ("PeakJobMemoryUsed", c_size_t)]
class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure): _fields_ = [("ControlFlags", c_uint32), ("CpuRate", c_uint32)]
class PROCESS_MEMORY_COUNTERS(ctypes.Structure): _fields_ = [("cb", c_uint32), ("PageFaultCount", c_uint32), ("PeakWorkingSetSize", c_size_t), ("WorkingSetSize", c_size_t), ("QuotaPeakPagedPoolUsage", c_size_t), ("QuotaPagedPoolUsage", c_size_t), ("QuotaPeakNonPagedPoolUsage", c_size_t), ("QuotaNonPagedPoolUsage", c_size_t), ("PagefileUsage", c_size_t), ("PeakPagefileUsage", c_size_t)]
def build(argv, write_roots, quotas, posture, server_pid) -> (argv, {}, {"status": "unsupported", "mechanism": None, "detail": {"note": "AppContainer confinement is PRD-006b"}}, {"status": "active", "mechanism": "job_object+supervisor", "limits": ...}, WindowsBackend)
class WindowsBackend(Backend):
    def attach(self, proc):   # CreateJobObjectW(None, None); SetInformationJobObject(job, 9, &ext, sizeof) with flags KILL_ON_JOB_CLOSE | PROCESS_MEMORY (memory_mb MiB) | ACTIVE_PROCESS (pids); if cpu_percent: SetInformationJobObject(job, 15, &rate{ENABLE|HARD_CAP, CpuRate = min(10000, cpu_percent*100 // os.cpu_count())}); AssignProcessToJobObject(job, int(proc._handle)); on any failure: record warning, no exception (quotas.status "off" for this client)
    def rss_bytes(self, proc):  # GetProcessMemoryInfo(int(proc._handle), &pmc, sizeof) → WorkingSetSize
    def explain_exit(self, proc, rc): None
    def release(self): CloseHandle(job)  (kills survivors: KILL_ON_JOB_CLOSE)
```

```python
# agentcad/kernel/client.py — supervisor + attribution + hook (Decision 5/9)
def _request_locked(self, method, params, timeout_s):
    ...
    interval = self._plan.quotas_obj.sample_interval_s if self._plan else 0.5
    cap = self._plan.quotas_obj.memory_mb * 1024 * 1024 if (self._plan and self._plan.quotas_obj.memory_mb) else None
    peak = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0: self._kill(); raise KernelError(ERROR_TIMEOUT, ..., {"usage": self._usage_stub(peak)})   # AC5
        if cap is not None and self._plan.backend:
            rss = self._plan.backend.rss_bytes(self._proc)
            if rss: peak = max(peak, rss)
            if rss and rss > cap:
                self._breach = ("memory_cap", {"limit_mb": self._plan.quotas_obj.memory_mb, "observed_rss_mb": round(rss / 2**20, 1), "tier": "supervisor"})
                self._kill()
                raise KernelError(ERROR_CRASH, f"kernel worker exceeded its memory cap ({...} MB); worker restarted", {"reason": "memory_cap", **details, "usage": self._usage_stub(peak), **self._crash_details()})
        try: line = self._lines.get(timeout=min(remaining, interval))
        except queue.Empty: continue
        if line is None:   # EOF
            rc = self._proc.poll() if self._proc else None
            why = self._plan.backend.explain_exit(self._proc, rc) if (self._plan and self._plan.backend) else None
            self._kill()
            details = {**self._crash_details(), "usage": self._usage_stub(peak)}
            if why: details.update(why)
            raise KernelError(ERROR_CRASH, "kernel worker exited unexpectedly" + (f" ({why['reason']})" if why else ""), details)
        ... (json parse, id match)
        usage = response.get("usage") or {}
        if peak: usage["peak_rss_mb"] = max(usage.get("peak_rss_mb") or 0, round(peak / 2**20, 1)); usage["peak_rss_is_lifetime"] = False
        self.last_usage = usage
        self._emit_usage(method, usage, ok="error" not in response)
        ...
```
`_emit_usage` calls `self._on_usage({"method": method, "usage": usage, "ok": ok, "worker": self._name})`
inside `try/except Exception` (a meter bug must never fail a build). `_usage_stub(peak)` =
`{"cpu_ms": None, "wall_ms": elapsed, "peak_rss_mb": peak_mb or None, "peak_rss_is_lifetime": False}`.
`KernelPool` forwards `quotas/posture/on_usage` and passes `name=f"worker-{i}"`.

```python
# agentcad/kernel/sandbox.py
def report(kernel) -> dict:
    """The /api/health object. kernel = KernelClient | KernelPool | any proxy exposing
    .sandboxed / .sandbox_report / ._plan (via getattr, defaults)."""
    plan = getattr(kernel, "_plan", None) or getattr(kernel, "plan", None)
    live = getattr(kernel, "sandbox_report", None) or {}
    conf = dict(plan.confinement) if plan else {"status": status(getattr(kernel, "sandboxed", False)), "mechanism": None, "detail": {}}
    if plan and conf["status"] == "active":
        conf["status"] = "active" if (live and not live.get("failures") and (sys.platform != "linux" or live.get("landlock_abi"))) else ("off" if live else "active")  # "active" only when the live report agrees; a not-yet-pinged worker keeps the intent
        if live: conf["detail"] = {**conf["detail"], **{k: live[k] for k in ("landlock_abi", "seccomp", "rlimits") if k in live}}
    return {"status": conf["status"], "mechanism": conf.get("mechanism"), "posture": plan.posture if plan else "local",
            "confinement": conf, "quotas": plan.quotas if plan else {"status": "off", "mechanism": None, "limits": {}},
            "warnings": list(plan.warnings) if plan else []}
```
(`KernelPool` exposes `_plan` of worker 0 through a `plan` property.)

### Tasks
- [ ] **Step 1:** `tests/test_supervisor.py` (macOS + Linux; skip Windows for the balloon
  test): a client with `writable_dirs=[tmp]`, `quotas=Quotas(memory_mb=<baseline+300>, ...)`
  where baseline = `client.last_usage["rss_mb"]` after a first Box build;
  script allocates `bytearray(1_500_000_000)` and touches every page
  (`b[::4096] = ...`) → `KernelError` type `kernel_crash`, `details["reason"]
  == "memory_cap"`, `details["observed_rss_mb"] > memory_mb`, `details["usage"]`
  present; the **next build succeeds** and the earlier mesh file is
  byte-identical (AC4). Also: `timeout_s=1` with a `time.sleep(5)` script →
  `timeout` with `details["usage"]["wall_ms"] >= 1000` (AC5). Also: pool of
  2 with a 2 s build on affinity "a" while affinity "b" gets killed by a
  balloon — "a" completes (AC6; use threads).
- [ ] **Step 2:** implement `sandbox_linux.py`, `sandbox_windows.py`, the client loop,
  `report()`, pool passthrough → `tests/test_supervisor.py` green on macOS.
- [ ] **Step 3:** extend `tests/test_sandbox_linux.py`: `test_rlimit_as_makes_balloon_recoverable`
  (client with `Quotas(address_space_mb=1536, memory_mb=8192)` — RLIMIT_AS
  below the supervisor cap; script `bytearray(4 << 30)` → `script_error`
  `denied == "memory"`, worker alive, next build ok); `test_rlimit_nproc_stops_fork_loop`
  (`pids_headroom=32`; script forks in a loop up to 200 collecting pids,
  kills its children at the end → `script_error` `denied == "process_count"`,
  next build ok — bounded by a 120 s timeout); `test_cgroup_tier_when_delegated`
  (skip unless `AGENTCAD_CGROUP_DIR` is set and writable: `plan().quotas["mechanism"]`
  starts with `cgroup`, `memory.max` file equals `memory_mb*2**20`,
  and a 4 GiB balloon under `memory_mb=512` yields `reason == "memory_cap"`,
  `tier == "cgroup"`); `test_cgroup_probe_falls_back_honestly` (with
  `AGENTCAD_CGROUP_DIR` pointing at a read-only dir → mechanism
  `rlimit+supervisor` and a warning naming the dir).
- [ ] **Step 4:** `tests/test_sandbox_windows.py` (`skipif != win32`, portability):
  `plan()` on Windows → confinement `unsupported`, quotas mechanism
  `job_object+supervisor`; a client with `memory_mb=1024` runs a script
  allocating 3 GiB → `script_error` with `denied == "memory"` (commit limit →
  `MemoryError`) and the next build succeeds; `sandbox.report()` shape.
  Also `tests/test_sandbox_plan.py` gains the Windows-shape test under
  `monkeypatch.setattr(sys, "platform", "win32")` with the backend's ctypes
  calls stubbed (`monkeypatch.setattr(sandbox_windows, "_job_create", lambda: 1)`
  etc.) so it runs everywhere.
- [ ] **Step 5:** `make test-linux tests/test_sandbox_linux.py tests/test_supervisor.py`
  → green (the cgroup test skips; document the Model-2 `docker run` line to
  exercise it in the test's docstring); `make test` on macOS (cite) →
  controller commits with changelog 0216.

### Verification
AC4/AC5/AC6 pass on macOS; the Linux battery is complete; `sandbox.report(kernel)`
answers the object for a bare client (`status "off"`/`"unsupported"`, empty
quotas) without raising.

---

## Slice 4 — service: usage meter + scope, health, `get_usage`, disk budgets, CLI wiring

### Files
- Create: `agentcad/core/usage.py`, `agentcad/core/tools_usage.py`,
  `tests/test_usage.py`, `tests/test_disk_budget.py`
- Modify: `agentcad/core/model.py` (`DiskBudgetError(AppError)`),
  `agentcad/core/project.py` (`disk_usage`, `assert_disk_budget`,
  `trim_cache`), `agentcad/core/service.py` (`self.usage`, scope on
  build/export/interference, budget asserts, trim after build),
  `agentcad/core/tools.py:51-58` (`call()` sets scope), `agentcad/server/app.py`
  (middleware scope; `/api/health` object + usage), `agentcad/cli.py`
  (`UsageMeter` → `on_usage`; hosted startup warning), `tests/test_server.py:41`,
  `tests/test_sandbox.py:189`, `tests/test_hosted_hardening.py:237`,
  `tests/test_security_guard.py:52` (key set unchanged — `sandbox` is still a key)

### The shapes

```python
# agentcad/core/usage.py — OCP-free
scope_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("agentcad_usage_scope", default=None)
@contextmanager
def scoped(project: str | None): tok = scope_var.set(project); try: yield; finally: scope_var.reset(tok)
def project_from_path(path: str) -> str | None   # r"^/api/projects/([^/]+)" → unquoted id, else None

class UsageMeter:
    def __init__(self, keep_recent: int = 2000): ...
    def record(self, event: dict) -> None
        # event = client hook payload; adds project=scope_var.get(), identity=locks.current_client_id() (guarded), at=time.time()
        # roll-up key (project, identity): requests, errors, cpu_ms, wall_ms, peak_rss_mb (max), last_at
    def totals(self) -> dict
    def by_project(self, since: float | None = None, top: int = 20) -> list[dict]
    def by_identity(self, since: float | None = None, top: int = 20) -> list[dict]
    def health(self) -> dict          # {"totals": ..., "projects": by_project()}
    def snapshot(self, project=None, since=None) -> dict   # get_usage payload
```

```python
# agentcad/core/tools_usage.py
def register(registry, service):
    registry.register(Tool(name="get_usage", description="Kernel resource usage roll-ups (CPU ms, wall ms, peak RSS) per project and per client identity, since an optional unix time.",
        input_schema={"type": "object", "properties": {"project": {"type": "string"}, "since": {"type": "number"}}},
        handler=lambda project=None, since=None: service.usage.snapshot(project, since)))
```

```python
# agentcad/core/project.py additions
def disk_usage(self, proj: str) -> dict            # {"used_bytes", "cache_bytes", "exports_bytes", "imports_bytes"} memoized 5 s per proj (self._disk_memo)
def assert_disk_budget(self, proj: str) -> None    # raises DiskBudgetError(details={"used_mb", "budget_mb"}) when used >= budget; budget from self.disk_budget_mb (None → no check)
def trim_cache(self, proj: str, keep_keys: set[str]) -> int   # deletes oldest-mtime .cache files (.acm/.faces.u32/.lod1.acm) whose stem (before the first ".") is not in keep_keys until cache_bytes <= 0.75*budget; returns bytes freed; invalidates the memo
```
`ProjectStore.__init__` gains `disk_budget_mb: int | None = None` (the
service sets it from quotas in `cli._build_service`; tests set it directly).
`service._build_with`: `self.store.assert_disk_budget(proj)` before the
kernel request; after a successful build `self.store.trim_cache(proj,
self._referenced_cache_keys(proj))` where the helper collects `cache_key`
values from `_status`/`_config_status` entries of that project. `export_part`,
`export_assembly`: assert before the request. `imports_dir(write=True)`:
assert after `write_guard`.

`app.py`: middleware sets `usage.scope_var.set(usage.project_from_path(request.url.path))`
next to `set_client_id` (both local and hosted branches — the hosted branch
returns early after `security_module.guard`; set the scope **before** the
guard call so both paths get it). `/api/health` (authenticated/local body):
`"sandbox": sandbox.report(service.kernel)`, `"usage": service.usage.health()`
when `service.usage` exists. `tools.py::call`: `with usage.scoped(args.get("project") if isinstance(args, dict) else None): return tool.handler(...)`.
`cli._build_service`: `meter = UsageMeter()`; kernel kwargs `on_usage=meter.record`;
`service.usage = meter`; `service.store.disk_budget_mb = quotas.disk_mb or None`;
in `cmd_serve` when `mode.hosted` and `sandbox.report(kernel)["status"] != "active"`:
`print("WARNING: kernel confinement is not active ...", file=sys.stderr)` naming the
warnings list.

### Tasks
- [ ] **Step 1:** `tests/test_usage.py` (failing): `UsageMeter.record` under `scoped("p1")`
  twice and `scoped("p2")` once → `by_project()` has two rows with the right
  request counts and `cpu_ms` sums, `totals()["requests"] == 3`, `since`
  filters, identity comes from `locks.client_id_var`; `project_from_path("/api/projects/a%20b/parts")
  == "a b"`; `get_usage` through `build_registry(service).call("get_usage", {})`
  returns the snapshot shape; `/api/health` in a `TestClient` app has
  `sandbox` as an object with `status`/`mechanism`/`quotas`/`confinement` and
  a `usage` dict; two projects built through the service produce
  distinguishable `by_project()` rows (AC7 — use the session `kernel` with an
  `on_usage` wired by the test: `kernel._on_usage = meter.record` via a
  fixture that resets it).
- [ ] **Step 2:** `tests/test_disk_budget.py`: a store with `disk_budget_mb=1` and a
  1.5 MB file in `exports/` → `assert_disk_budget` raises `DiskBudgetError`
  with `details["used_mb"] >= 1`; the service refuses `export_part` with the
  wire type `DiskBudgetError`; `trim_cache` deletes the oldest unreferenced
  `.acm` first and keeps referenced keys; the memo refreshes after
  `trim_cache`.
- [ ] **Step 3:** implement; update the four existing health assertions (`data["sandbox"]["status"]`);
  run `uv run pytest tests/test_usage.py tests/test_disk_budget.py tests/test_server.py tests/test_sandbox.py tests/test_hosted_hardening.py tests/test_security_guard.py -q` → green.
- [ ] **Step 4:** `make test` (cite) → controller commits with changelog 0217.

### Verification
`agentcad serve` → `curl /api/health` shows the sandbox object and usage;
`get_usage` appears in `GET /api/tools` (registry count +1 — update any
tool-count assertion: `grep -rn "len(registry" tests/ | head`).

---

## Slice 5 — CI, image & compose, docs, the acceptance file, close-out prep

### Files
- Modify: `.github/workflows/ci.yml` (probe step; `AGENTCAD_EXPECT_SANDBOX=active`
  on ubuntu + macos, `AGENTCAD_EXPECT_QUOTAS=active` on all three; the
  portability suite gains nothing else — the tests are marked), `Dockerfile`
  (header trust comment rewritten; nothing functional), `compose.yaml`
  (header rewritten; `pids_limit: 512`, `mem_limit` commented with sizing;
  the commented Model-2 cgroup delegation block), `docs/deployment.md` (top
  trust blockquote, sizing table, env table rows `AGENTCAD_QUOTA_*`,
  `AGENTCAD_CGROUP_DIR`, `AGENTCAD_NO_SANDBOX` semantics, a new "Confinement
  and quotas" section replacing "What PRD-006 will change"), `docs/architecture.md`
  (Trust model: per-OS contract table), `AGENTS.md` (a "Sandboxing & quotas
  gotchas (PRD-006)" section + the trust sentence at :1680 rewritten),
  `CLAUDE.md` (trap block), `docs/agent-api.md` (`details.reason`/`denied`/`usage`,
  `get_usage`, health shape), `docs/user-guide.md` (one paragraph on the
  error panel wording), `docs/roadmap.md` (006 row → in-progress note; the
  final move happens in the close-out commit on main), `docs/prd/in-progress/PRD-006-…`
  (status header: what shipped, the 006b carve-out — the file moves at close-out)
- Create: `tests/test_prd006_acceptance.py`, `docs/prd/pending/PRD-006b-windows-appcontainer.md`
  (a short PRD: FR2 confinement half + AC3 Windows clause, deps 006, origin "carved out 2026-08-18")

### The acceptance file
`tests/test_prd006_acceptance.py` (portability-marked; each AC one test or a
pointer): AC1 → imports the Linux battery's tests by name and asserts they
exist + on Linux with `AGENTCAD_EXPECT_SANDBOX=active` the module-level
`confined` fixture reports `sandboxed is True`; AC2 → `tests/test_sandbox.py`
still collects ≥ the pre-PRD count (guard against deletion); AC3 → `sandbox.report()`
`status == "active"` on darwin/linux when `AGENTCAD_EXPECT_SANDBOX=active`,
and on win32 `confinement.status == "unsupported"` with the 006b note; AC4–AC7
→ pointers to `test_supervisor.py`/`test_usage.py`; AC8 → `AGENTCAD_NO_SANDBOX=1`
→ `off`, and a fake `landlock_abi = 0` (monkeypatch `_confine.landlock_abi`)
→ `plan().confinement["status"] == "unsupported"` with a warning, plus the
`*_the_full_suite_count_is_cited` pattern (read the newest changelog and
assert it contains `make test` and `passed`).

### Tasks
- [ ] **Step 1:** ci.yml: add step `Sandbox probe (Linux)` (`uname -r; cat /sys/kernel/security/lsm || true;
  uv run python -c "from agentcad.kernel import _confine; print('landlock abi', _confine.landlock_abi())"`);
  env vars per job as above.
- [ ] **Step 2:** the docs. `docs/deployment.md`'s blockquote becomes: a member's part
  script now runs confined on Linux — no network, writes only under the
  projects tree and its private temp dir, reads narrowed to system paths +
  the app + projects (never the state dir), memory/process/CPU caps as
  configured — **and** the residual: it still runs as the server user with
  the projects tree readable and writable across projects (per-project ACLs
  are PRD-005), so accounts remain for people you trust; registration stays
  closed. Sizing table gains the caps. A "Confinement and quotas" section
  documents the tiers, `/api/health` fields, `AGENTCAD_QUOTA_*`,
  `AGENTCAD_CGROUP_DIR` + the Model-2 recipe (host `mkdir /sys/fs/cgroup/agentcad`,
  `+memory +pids +cpu` in both `subtree_control`s, `chown -R 10001:10001`,
  compose `cgroup_parent: /agentcad` + `- /sys/fs/cgroup/agentcad:/cg:rw` +
  `AGENTCAD_CGROUP_DIR=/cg`), the supervisor overshoot caveat, and the
  Landlock requirements (kernel ≥ 6.2 recommended, `lsm=` list). AGENTS.md
  gotchas: no `preexec_fn`; never grant `/tmp`; `seccomp` op constant is 1;
  `TRUNCATE` bit; `ru_maxrss` units; `clear_refs`; `AGENTCAD_NO_SANDBOX`
  opts out of confinement not caps; the `fakeowner` mount trap and `make
  test-linux`; the four `*_the_full_suite_count_is_cited`-style tests.
- [ ] **Step 3:** `tests/test_prd006_acceptance.py`; PRD-006b file; PRD-006 header status
  note; roadmap row text (status stays "in-progress" until the close-out commit).
- [ ] **Step 4:** `make test` (cite) → controller commits with changelog 0218 (the
  "PRD-006 completed" changelog is the close-out commit on main after merge).

### Verification
Push → CI three jobs green with the probe output visible; `make test-linux`
green; PR opened.

---

## Self-review against the spec

- Decision 1 (Landlock+seccomp, TRUNCATE, seccomp op=1, arch tables,
  private tmp) → Slice 2 + Slice 1 tmp. ✓
- Decision 2 (postures) → Slice 3 `read_roots` + hosted test. ✓
- Decision 3 (tiers/defaults) → Slice 1 quotas + Slice 3 backends. ✓
- Decision 4 (cgroup by delegation, parent-side attach) → Slice 3. ✓
- Decision 5 (supervisor in loop) → Slice 3. ✓
- Decision 6 (meter/scope/health/get_usage) → Slice 2 worker + Slice 4. ✓
- Decision 7 (Windows) → Slice 3 + 006b in Slice 5. ✓
- Decision 8 (honesty) → Slice 2 report + Slice 3 `report()` + Slice 4 warning + Slice 5 CI env. ✓
- Decision 9 (denials, reasons) → Slice 2 + 3. ✓
- Decision 10 (disk budgets) → Slice 4. ✓
- Decision 11 (kill-to-others) → Slice 2 BPF. ✓
- Decision 12 (scope/carve-out) → Slice 5. ✓
- Decision 13 (CI proves) → Slice 2 script + Slice 5 ci.yml. ✓
- Risks: protocol forgery fix → Slice 2 random ids. ✓
