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
                 writable_dirs: list[str] | None = None):
        self.size = max(1, int(size))
        self._workers: list[KernelClient] = [
            KernelClient(python_exe=python_exe, timeout_s=timeout_s,
                         writable_dirs=writable_dirs)
            for _ in range(self.size)
        ]
        self._rr = 0
        self._rr_lock = threading.Lock()

    @property
    def sandboxed(self) -> bool:
        # Every worker is constructed identically, so worker 0 speaks for all
        # (workers spawn lazily; the decision is made at construction).
        return self._workers[0].sandboxed

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
