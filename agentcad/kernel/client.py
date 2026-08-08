"""KernelClient: owns the worker subprocess.

Spawns ``python -m agentcad.kernel.worker``, sends line-delimited JSON
requests, enforces per-request timeouts, and kills/respawns the worker on
hangs, crashes, or EOF. Thread-safe; one request in flight at a time.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from collections import deque

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
    def __init__(self, python_exe: str | None = None, timeout_s: float = 60.0):
        self._python = python_exe or sys.executable
        self._timeout = timeout_s
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._lock = threading.Lock()
        self._next_id = 0

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        with self._lock:
            self._ensure_started()

    def stop(self) -> None:
        with self._lock:
            self._kill()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure_started(self) -> None:
        if self.alive:
            return
        self._lines = queue.Queue()
        self._stderr_tail.clear()
        self._proc = subprocess.Popen(
            [self._python, "-u", "-m", "agentcad.kernel.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
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
