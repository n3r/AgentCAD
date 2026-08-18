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
import subprocess
import sys
import threading
from collections import deque

from .._spawn import worker_argv
from . import sandbox
from .protocol import ERROR_CRASH, ERROR_TIMEOUT

STARTUP_TIMEOUT_S = 180.0  # first ping pays the build123d import cost


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
        self._next_id = 0
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
        self.last_usage: dict | None = None      # per-request metering (Slice 2/3)
        self._on_usage = on_usage                # the usage hook (Slice 4)
        self._breach: tuple[str, dict] | None = None  # the supervisor's (Slice 3)

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
        self._request_locked("ping", {}, timeout_s=STARTUP_TIMEOUT_S)

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

        self._next_id += 1
        req_id = self._next_id
        try:
            self._proc.stdin.write(
                json.dumps({"id": req_id, "method": method, "params": params}) + "\n"
            )
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._kill()
            raise KernelError(
                ERROR_CRASH, f"kernel worker unreachable: {exc}", self._crash_details()
            ) from exc

        import time

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise KernelError(
                    ERROR_TIMEOUT,
                    f"kernel request {method!r} exceeded {timeout:.0f}s; worker restarted",
                )
            try:
                line = self._lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if line is None:
                self._kill()
                raise KernelError(
                    ERROR_CRASH,
                    "kernel worker exited unexpectedly",
                    self._crash_details(),
                )
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray non-protocol output
            if response.get("id") != req_id:
                continue  # stale response from an earlier request
            if "error" in response:
                err = response["error"]
                raise KernelError(
                    err.get("type", "kernel_error"),
                    err.get("message", "unknown kernel error"),
                    err.get("details", {}),
                )
            return response.get("result", {})

    def _crash_details(self) -> dict:
        return {"stderr_tail": list(self._stderr_tail)[-20:]}
