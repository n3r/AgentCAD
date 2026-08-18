"""KernelClient: owns the worker subprocess.

Spawns ``python -m agentcad.kernel.worker``, sends line-delimited JSON
requests, enforces per-request timeouts, and kills/respawns the worker on
hangs, crashes, or EOF. Thread-safe; one request in flight at a time.

With ``writable_dirs`` or ``quotas`` it spawns through a
:class:`~agentcad.kernel.sandbox.SandboxPlan`: the worker is confined, capped,
and given a private temp dir, all decided once so every respawn is identical.
With neither (the default, and what the test suite's session fixture uses) the
argv, the environment and the lifecycle are byte-for-byte the historical ones.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from collections import deque

from .._spawn import worker_argv
from . import sandbox
from .protocol import ERROR_CRASH, ERROR_TIMEOUT

STARTUP_TIMEOUT_S = 180.0  # first ping pays the build123d import cost

#: How often the request loop wakes when there is no plan: the historical poll
#: interval, kept exactly so a plan-free client behaves as it always has.
POLL_INTERVAL_S = 0.5

#: The floor under the supervisor's sampling interval, and what a switched-off
#: one falls back to. `sample_interval_s` is operator-settable and `"off"`
#: resolves it to `0.0` — which, used as a queue timeout, is a busy spin on one
#: core rather than a switched-off sampler, so it is read as "use the default".
MIN_SAMPLE_INTERVAL_S = 0.05
DEFAULT_SAMPLE_INTERVAL_S = 0.25

_MB = 1024.0 * 1024.0

#: The preamble stages whose failure means the worker is NOT confined. A
#: refused rlimit is a quota that did not apply — it belongs in the report and
#: in health's warnings, but it says nothing about whether Landlock and seccomp
#: are in force, and letting it clear `sandboxed` would understate the
#: confinement as badly as overstating it.
CONFINEMENT_STAGES = ("landlock", "seccomp")


def confinement_holds(report: dict) -> bool:
    """Whether the worker's own ping report still supports a confinement claim.

    The report is the ONLY thing allowed to make `sandboxed` true (design spec,
    Decision 8); this is the half of that rule that can be read on its own.
    """
    for failure in report.get("failures") or []:
        if failure.get("stage") in CONFINEMENT_STAGES:
            return False
    if sys.platform == "linux" and not report.get("landlock_abi"):
        # On Linux confinement IS Landlock plus seccomp; no ABI, no claim.
        return False
    return True


class KernelError(Exception):
    def __init__(self, type: str, message: str, details: dict | None = None):
        super().__init__(f"{type}: {message}")
        self.type = type
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict:
        return {"type": self.type, "message": self.message, "details": self.details}


class KernelClient:
    def __init__(
        self,
        python_exe: str | None = None,
        timeout_s: float = 60.0,
        *,
        writable_dirs: list[str] | None = None,
        quotas=None,
        posture: str | None = None,
        on_usage=None,
        name: str | None = None,
    ):
        self._python = python_exe or sys.executable
        self._timeout = timeout_s
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._lock = threading.Lock()
        #: The id of the request in flight. Random, not a counter — see
        #: `_request_locked`. Kept for tests and for a crash report.
        self._last_req_id: int | None = None
        #: Which worker this is, in a pool ("worker-0"); None for a lone client.
        self.name = name
        # Confinement and quotas are decided once, at construction, so every
        # respawn of a timed-out/crashed worker comes back under identical
        # terms. With writable_dirs=None and quotas=None (the defaults) there
        # is no plan at all and the argv is exactly the historical one — no
        # sandbox module behavior can affect existing callers.
        # worker_argv resolves to the frozen self-exec form under PyInstaller.
        base = worker_argv(self._python)
        self._plan: sandbox.SandboxPlan | None = None
        if writable_dirs is not None or quotas is not None:
            self._plan = sandbox.plan(base, list(writable_dirs or []),
                                      quotas=quotas, posture=posture,
                                      server_pid=os.getpid())
        self._argv = self._plan.argv if self._plan else base
        #: Intended confinement. Slice 2 refines it from the worker's own ping
        #: report — a preamble that failed must never read as `active` here.
        self.sandboxed: bool = bool(
            self._plan and self._plan.confinement["status"] == "active")
        self.sandbox_report: dict | None = None  # the worker's own (Slice 2)
        self.last_usage: dict | None = None      # per-request metering
        self._on_usage = on_usage                # the usage hook (Slice 4's meter)
        self._usage_hook_broken = False          # so it complains once, not per build
        #: What the supervisor decided about the request in flight, before it
        #: killed the worker: `("memory_cap", {...})`. Reset per request.
        self._breach: tuple[str, dict] | None = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        with self._lock:
            self._ensure_started()

    def stop(self) -> None:
        with self._lock:
            self._kill()
            if self._plan is not None:
                # For good this time: the private temp dir goes with it.
                self._plan.release()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure_started(self) -> None:
        if self.alive:
            return
        self._lines = queue.Queue()
        self._stderr_tail.clear()
        env = None
        if self._plan is not None:
            # The plan owns the child's environment: its private temp dir
            # (which must exist before the child looks at $TMPDIR), the
            # bytecode opt-out, and the rlimit payload the worker applies to
            # itself. Everything else is inherited.
            self._plan.prepare_tmp()
            env = {**os.environ, **self._plan.env}
        self._proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        if self._plan is not None and self._plan.backend is not None:
            # Right after Popen and before the first request: cgroup placement
            # and job-object assignment are the parent's job (never a
            # preexec_fn — CPython documents it as unsafe in a threaded
            # parent, and this one is threaded).
            self._plan.backend.attach(self._proc)
        threading.Thread(
            target=self._drain_stdout, args=(self._proc, self._lines), daemon=True
        ).start()
        threading.Thread(
            target=self._drain_stderr, args=(self._proc,), daemon=True
        ).start()
        result = self._request_locked("ping", {}, timeout_s=STARTUP_TIMEOUT_S)
        # What the worker says it applied to ITSELF — the only thing allowed
        # to make `sandboxed` true (design spec, Decision 8). A plan that
        # intended to confine but whose preamble reported a failure is `off`,
        # and on Linux a worker with no Landlock ABI is not confined at all
        # however good the intention was.
        self.sandbox_report = result.get("sandbox") or {}
        self.sandboxed = bool(self.sandboxed
                              and confinement_holds(self.sandbox_report))

    def _drain_stdout(self, proc: subprocess.Popen, lines: queue.Queue) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)  # EOF marker

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            self._stderr_tail.append(line.rstrip())

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self._plan is not None:
            # A dead worker's scratch is nobody's. The directory itself stays:
            # the respawn reuses it, and its name is already in the profile.
            self._plan.wipe_tmp()

    # --------------------------------------------------------------- request

    def request(
        self,
        method: str,
        params: dict,
        timeout_s: float | None = None,
        affinity: str | None = None,
    ) -> dict:
        # `affinity` lets a worker pool route a part to a consistent worker so
        # its shape LRU stays warm; a single client ignores it.
        with self._lock:
            self._ensure_started()
            return self._request_locked(method, params, timeout_s)

    def _request_locked(self, method: str, params: dict, timeout_s: float | None) -> dict:
        assert self._proc is not None and self._proc.stdin is not None
        timeout = timeout_s if timeout_s is not None else self._timeout

        # Drop any stale lines left over from a previous timed-out request.
        while True:
            try:
                self._lines.get_nowait()
            except queue.Empty:
                break

        # A random 62-bit token, never a counter. A part script may `os.fork()`
        # and the child inherits fd 1 — the protocol stream — so with a counter
        # a LINGERING child (or any stale writer) could compute the ids of
        # requests it never saw and answer them: a later build, an export, a
        # request from another part entirely. A random token ends that: an id
        # it did not observe is a 62-bit guess.
        #
        # What this does NOT close, deliberately: the running script can still
        # forge the response to its OWN in-flight request — it holds fd 1 and
        # can reach the id through the interpreter. That is the same trust
        # domain as `build()` simply returning a fake shape, so it is not worth
        # fd gymnastics (design spec, "Risks"). 62 bits keeps it a JSON-safe
        # integer.
        req_id = secrets.randbits(62)
        self._last_req_id = req_id
        # Before the write: a broken pipe is still a request that spent wall
        # clock, and `details.usage` is the contract for every path that ends
        # without the worker answering.
        interval, cap = self._supervision()
        started = time.monotonic()
        peak = 0
        self._breach = None
        try:
            self._proc.stdin.write(
                json.dumps({"id": req_id, "method": method, "params": params}) + "\n"
            )
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            usage = self._usage_stub(started, peak)
            self._kill()
            raise KernelError(
                ERROR_CRASH, f"kernel worker unreachable: {exc}",
                {**self._crash_details(), "usage": usage}
            ) from exc

        # The supervisor (design spec, Decision 5). It is this loop, not a
        # thread: the loop already wakes on a timer to enforce the timeout, so
        # sampling costs one `/proc` read (0.5 us) or one libproc call (1.3 us)
        # per wake, and a breach can raise straight into the caller.
        deadline = started + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Measured before the kill: `_kill` waits up to 5 s for the
                # process to die, and that teardown is not what the request
                # cost. A `wall_ms` five seconds past the timeout it reports
                # would be the first thing a reader distrusted.
                usage = self._usage_stub(started, peak)
                self._kill()
                raise KernelError(
                    ERROR_TIMEOUT,
                    f"kernel request {method!r} exceeded {timeout:.0f}s; worker restarted",
                    {"usage": usage},
                )
            if cap is not None:
                rss = self._sample_rss()
                if rss:
                    peak = max(peak, rss)
                if rss and rss > cap:
                    limit_mb = self._plan.quotas_obj.memory_mb
                    # Set BEFORE the kill, so the reason survives whichever of
                    # the kill and the worker's own EOF wins the race.
                    self._breach = ("memory_cap", {
                        "limit_mb": limit_mb,
                        "observed_rss_mb": round(rss / _MB, 1),
                        "tier": "supervisor"})
                    usage = self._usage_stub(started, peak)
                    self._kill()
                    raise KernelError(
                        ERROR_CRASH,
                        f"kernel worker exceeded its memory cap "
                        f"({limit_mb} MB); worker restarted",
                        {"reason": "memory_cap", **self._breach[1],
                         "usage": usage, **self._crash_details()},
                    )
            try:
                line = self._lines.get(timeout=min(remaining, interval))
            except queue.Empty:
                continue
            if line is None:
                # EOF. Why the worker died, if the platform backend can read it
                # off the corpse — a cgroup OOM counter, a CPU signal. (The
                # supervisor's own kills never arrive here: that branch raises
                # where it kills, with `_breach` as the reason.) The usage is
                # measured first: `_explain_exit` waits for the return code and
                # `_kill` waits for the process, and neither is request time.
                usage = self._usage_stub(started, peak)
                why = self._explain_exit()
                self._kill()
                details = {**self._crash_details(), "usage": usage}
                if why:
                    details.update(why)
                raise KernelError(
                    ERROR_CRASH,
                    "kernel worker exited unexpectedly"
                    + (f" ({why['reason']})" if why else ""),
                    details,
                )
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray non-protocol output
            if response.get("id") != req_id:
                continue  # stale response from an earlier request
            reported = response.get("usage")
            usage = dict(reported) if isinstance(reported, dict) else {}
            if peak and isinstance(reported, dict):
                # The parent's samples belong to THIS request on every
                # platform, which is what macOS and Windows cannot measure from
                # inside (`ru_maxrss`/`PeakWorkingSetSize` are lifetime marks,
                # so a 40 ms build would otherwise report the 500 MB OCCT
                # import that preceded it). Where the worker's own number is
                # already per-request (Linux, via `/proc/self/clear_refs`) the
                # two are combined and the larger wins — the sampler runs every
                # 0.25 s and can miss a spike the worker saw.
                parent_mb = round(peak / _MB, 1)
                worker_mb = usage.get("peak_rss_mb") or 0
                usage["peak_rss_mb"] = (
                    parent_mb if usage.get("peak_rss_is_lifetime", True)
                    else max(worker_mb, parent_mb))
                usage["peak_rss_is_lifetime"] = False
            if isinstance(reported, dict):
                self.last_usage = usage
            # else: a response with no usage envelope leaves `last_usage`
            # describing the last request that DID report one. Overwriting it
            # with `{}` would turn "nothing to say about this one" into "the
            # last request cost nothing".
            self._emit_usage(method, usage, ok="error" not in response)
            if "error" in response:
                err = response["error"]
                # Deliberately NOT `details["usage"] = usage`: the worker
                # answered, so its usage travels on `last_usage` and through
                # the hook (with `ok: False` — that is what the meter counts as
                # an error). Copying it into the error body would put a
                # per-run `cpu_ms` inside a payload two routes are required to
                # render identically (`tests/test_configs_drawing.py`), and
                # `details.usage` is the *kill* paths' contract: a breach, a
                # timeout and a crash have no worker report to carry it.
                raise KernelError(
                    err.get("type", "kernel_error"),
                    err.get("message", "unknown kernel error"),
                    err.get("details", {}),
                )
            return response.get("result", {})

    # ------------------------------------------------------------ supervision

    def _supervision(self) -> tuple[float, int | None]:
        """``(poll interval, RSS cap in bytes)`` for one request.

        Without a plan this is the historical 0.5 s poll and no cap at all —
        the client the session fixture builds must behave exactly as it did.
        """
        if self._plan is None:
            return POLL_INTERVAL_S, None
        quotas = self._plan.quotas_obj
        interval = max(MIN_SAMPLE_INTERVAL_S,
                       quotas.sample_interval_s or DEFAULT_SAMPLE_INTERVAL_S)
        backend = self._plan.backend
        if not quotas.memory_mb or backend is None or not backend.can_sample():
            return interval, None
        return interval, quotas.memory_mb * 1024 * 1024

    def _sample_rss(self) -> int | None:
        """One RSS sample, or ``None`` when it could not be taken.

        The supervisor must never fail a build with a bug of its own: a
        backend that raises is a sample that did not happen, not a breach.
        """
        proc = self._proc
        if proc is None or self._plan is None or self._plan.backend is None:
            return None
        try:
            return self._plan.backend.rss_bytes(proc)
        except Exception:                      # pragma: no cover - defensive
            return None

    def _explain_exit(self) -> dict | None:
        """The backend's reading of why the worker died; ``None`` if it cannot
        say (and then the crash stays the generic one it has always been).

        Called **before** ``_kill()``: a cgroup's ``memory.events`` has to be
        read while the directory is still the dead worker's. The short wait is
        what makes the signal readable at all — EOF on stdout arrives before
        the process is reaped, and ``poll()`` would answer ``None`` where
        ``wait()`` answers ``-SIGXCPU``.
        """
        if self._plan is None or self._plan.backend is None:
            return None
        proc = self._proc
        try:
            returncode = None
            if proc is not None:
                try:
                    returncode = proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    returncode = proc.poll()
            return self._plan.backend.explain_exit(proc, returncode)
        except Exception:                      # pragma: no cover - defensive
            return None

    def _usage_stub(self, started: float, peak: int) -> dict:
        """Usage for a request that never answered (a kill, a timeout).

        The worker's own meter died with it, so this is only what the parent
        saw. ``cpu_ms`` is ``None`` rather than ``0``: "not measurable from
        here" is not "no CPU was spent", and a meter summing zeros would
        under-bill exactly the requests that cost the most.
        """
        peak_mb = round(peak / _MB, 1) if peak else None
        return {"cpu_ms": None,
                "wall_ms": round((time.monotonic() - started) * 1000.0, 3),
                "peak_rss_mb": peak_mb,
                "peak_rss_is_lifetime": False}

    def _emit_usage(self, method: str, usage: dict, ok: bool) -> None:
        """Hand one record to the usage hook (Slice 4's meter installs it).

        Swallowed on purpose, and loudly once: a metering bug must never turn a
        successful build into a failure, and a silent one would make the meter
        look merely empty.
        """
        if self._on_usage is None:
            return
        try:
            self._on_usage({"method": method, "usage": usage, "ok": ok,
                            "worker": self.name})
        except Exception as exc:
            if not self._usage_hook_broken:
                self._usage_hook_broken = True
                print(f"[agentcad-usage] the on_usage hook raised and was "
                      f"ignored: {exc!r}", file=sys.stderr)

    def _crash_details(self) -> dict:
        return {"stderr_tail": list(self._stderr_tail)[-20:]}
