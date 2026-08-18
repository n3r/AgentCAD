"""KernelPool — a drop-in replacement for KernelClient backed by N workers.

The service only ever calls ``.request(method, params, timeout_s=, affinity=)``,
``.start()``, ``.stop()`` and reads ``.alive``, so a pool that implements the
same surface parallelizes multi-part rebuilds without any service changes.
Each underlying KernelClient still serializes its own requests (one in-flight),
so the win is cross-part concurrency. Requests are routed by affinity
(``hash(part_id) % N``) to keep a part on a warm-LRU worker; unkeyed requests
round-robin. Workers spawn lazily. Pool size 1 behaves exactly like a single
KernelClient. Validated 2.4–3.6x on batch builds in the v2 spike.
"""

from __future__ import annotations

import threading

from .client import KernelClient


class KernelPool:
    def __init__(self, size: int = 3, python_exe: str | None = None,
                 timeout_s: float = 60.0, *,
                 writable_dirs: list[str] | None = None,
                 quotas=None, posture: str | None = None, on_usage=None):
        self.size = max(1, int(size))
        # Confinement, quotas and posture are passed through unchanged: each
        # worker plans its own, so each gets its **own** private temp dir (and,
        # later, its own cgroup) rather than sharing one.
        self._workers: list[KernelClient] = [
            KernelClient(python_exe=python_exe, timeout_s=timeout_s,
                         writable_dirs=writable_dirs, quotas=quotas,
                         posture=posture, on_usage=on_usage,
                         name=f"worker-{index}")
            for index in range(self.size)
        ]
        self._rr = 0
        self._rr_lock = threading.Lock()

    @property
    def sandboxed(self) -> bool:
        # Every worker is constructed identically, so worker 0 speaks for all
        # (workers spawn lazily; the decision is made at construction).
        return self._workers[0].sandboxed

    @property
    def sandbox_report(self) -> dict | None:
        # Worker 0 is the one `start()` warms, so it is the one that has a
        # report of its own to give.
        return self._workers[0].sandbox_report

    @property
    def plan(self):
        """Worker 0's sandbox plan, for `sandbox.report(kernel)`.

        Named without the underscore on purpose: `report()` reads `_plan` from
        a client and `plan` from a pool, and a pool's plan is not the pool's
        own — it is one worker's, standing for all of them because they are
        constructed identically. The per-worker parts (the private temp dir,
        the cgroup directory) differ; the confinement, the posture and the
        caps, which is all health publishes, do not.
        """
        return self._workers[0]._plan

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        # Lazy: warm just the first worker so health reports ready quickly;
        # the rest spawn on first use.
        self._workers[0].start()

    def stop(self) -> None:
        for w in self._workers:
            w.stop()

    @property
    def alive(self) -> bool:
        return any(w.alive for w in self._workers)

    # --------------------------------------------------------------- routing

    def _pick(self, affinity: str | None) -> KernelClient:
        if affinity is not None:
            return self._workers[hash(affinity) % self.size]
        with self._rr_lock:
            worker = self._workers[self._rr % self.size]
            self._rr += 1
        return worker

    def request(self, method: str, params: dict, timeout_s: float | None = None,
                affinity: str | None = None) -> dict:
        return self._pick(affinity).request(method, params, timeout_s=timeout_s)
