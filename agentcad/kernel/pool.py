"""KernelPool — a drop-in replacement for KernelClient backed by N workers.

The service only ever calls ``.request(method, params, timeout_s=, affinity=)``,
``.start()``, ``.stop()`` and reads ``.alive``, so a pool that implements the
same surface parallelizes multi-part rebuilds without any service changes.
Each underlying KernelClient still serializes its own requests (one in-flight),
so the win is cross-part concurrency. Requests are routed by affinity
(``hash(part_id) % N``) to keep a part on a warm-LRU worker; unkeyed requests
round-robin. Workers spawn lazily. Pool size 1 behaves exactly like a single
KernelClient. Validated 2.4–3.6x on batch builds in the v2 spike.

Fair scheduling (PRD-005 FR11, design spec §7)
----------------------------------------------
One process serves every tenant, so without a gate one org's 200-part rebuild
occupies every worker and every other org waits behind it. ``request()`` reads
the **ambient tenant** (``core.tenancy.tenant_var``, set per request by
``server/security.py``) and, when there is one, adds two things and nothing
else:

* **Namespaced affinity** — ``"<org>/<ws>:<affinity>"`` before hashing, the
  ``share_build.SHARE_AFFINITY`` precedent. Two orgs with a part called
  ``bracket`` no longer land on the same worker by construction, so neither
  can evict the other's shape from that worker's LRU under the same key.
  This is cache hygiene, **not** isolation: two namespaced keys still collide
  on a small pool, exactly as two part ids do today.
* **A per-tenant entry gate** — at most ``max(1, size - 1)`` requests in
  flight per tenant, so a flooding tenant provably leaves one worker's worth
  of concurrency for everyone else. Over the cap a request waits FIFO within
  its tenant; past :data:`TENANT_QUEUE_DEPTH` waiters it is refused
  immediately with :class:`KernelBusyError` rather than parked (a queue that
  grows without bound is a request thread leak, and the caller can retry).
  Releases drain waiting tenants **round-robin** (:meth:`_drain_locked`), so
  no tenant is systematically served last.

**Local mode is the absence of a tenant.** With no tenant set — every local
run, every existing test, ``agentcad check``, the bench harness — ``request``
takes the historical line: ``self._pick(affinity).request(...)``, no
namespacing, no accounting, not one lock acquired. That branch is pinned by
``tests/test_pool_fairness.py`` against an oracle re-implementation of the
pre-change ``_pick``.

The gate only decides *entry*. The actual serialization is still each
worker's own single-in-flight lock, which is why this layer can be this
small: it never picks a worker, never queues per worker, and never holds its
lock across a kernel call.
"""

from __future__ import annotations

import threading
from collections import deque

# `core.model` is stdlib-only (exceptions and dataclasses) and `pool.py` is
# the *service-side* half of the kernel package — it is never imported by the
# worker process, which is what the "only kernel/ imports OCP" rule is about.
# The one thing that cannot be lazy is a base class, and a busy refusal has to
# BE the house's rate-limit error to inherit its 429 without a core edit; see
# `KernelBusyError`. Everything else this module needs from `core` (the
# tenant) is imported lazily, at first use — the `kernel/sandbox.py` ->
# `core.appmode` precedent.
from ..core.model import RateLimitedError
from .client import KernelClient

#: How many waiters one tenant may have parked before the next request is
#: refused outright (design spec §7). Per tenant, not global: a second tenant
#: arriving while the first is 32 deep is admitted immediately, which is the
#: whole point.
TENANT_QUEUE_DEPTH = 32

#: The longest a queued request will wait for a slot before giving up and
#: refusing. Not a kernel timeout — the request has not been sent yet — but a
#: ceiling that keeps a wedged pool from parking request threads for ever. A
#: pool that cannot drain 32 waiters in five minutes is already refusing
#: everything downstream. Instance-overridable (``pool.queue_wait_s``) so a
#: test does not have to wait it out.
QUEUE_WAIT_S = 300.0

#: Cached ``core.tenancy.current_tenant`` (see :func:`_ambient_tenant`).
_current_tenant = None


class KernelBusyError(RateLimitedError):
    """No kernel slot for this tenant right now — retry (429).

    A ``RateLimitedError`` and not a ``ServiceUnavailableError`` on the house's
    own documented split (``core/model.py``): "a 429 clears when a slot frees,
    a 503 here is a standing condition an operator must fix". This clears when
    a slot frees. It is also exactly what ``share_build`` already raises when
    the pool has no room for an anonymous build, so the two "the kernel is
    full" refusals answer alike.

    Wire type, via the derived-name convention (``model.error_type``, the
    ``PermissionError`` precedent from PRD-005 slice 1): **``kernelbusy_error``**
    on the tool surface — the house spelling, one word, like
    ``notfound_error`` and ``serviceunavailable_error``, never
    ``kernel_busy_error``. HTTP bodies spell the class name
    (``"KernelBusyError"``) with status **429** through ``app.py``'s
    ``_ERROR_STATUS`` isinstance walk, and carry ``details.retry_after_s``
    like every other 429. **Zero core edits**: the status, the wire spelling
    and the tool envelope are all inherited.

    ``details``: ``tenant``, ``in_flight``, ``queued``, ``limit``,
    ``queue_depth``, ``retry_after_s`` (and ``waited_s`` when the refusal came
    from the wait ceiling rather than the queue bound).
    """


#: Seconds a refused caller is told to wait. Short: the cap is per-tenant and
#: a slot frees whenever any one of that tenant's own requests finishes.
BUSY_RETRY_AFTER_S = 2


def _ambient_tenant():
    """The default ``tenant_provider``: PRD-005's ambient ``(org, ws)``, or None.

    Imported **lazily and cached**, so importing ``kernel.pool`` does not drag
    ``core.tenancy`` (and its authstore guard registry) into every process that
    only wants to talk to a worker. ``current_tenant`` is a ContextVar read —
    a dict lookup — so the cached indirection costs nothing per request.
    """
    global _current_tenant
    if _current_tenant is None:
        from ..core.tenancy import current_tenant
        _current_tenant = current_tenant
    return _current_tenant()


#: Module attribute rather than a constructor argument: the pool is built in
#: ``cli.py`` long before anything knows whether this process is hosted, and
#: the tenant is ambient state, not pool configuration. Rebind it to stub
#: tenancy in a test; ``request`` resolves it through the module globals on
#: every call, so a rebind takes effect on existing pools too.
tenant_provider = _ambient_tenant


class KernelPool:
    def __init__(self, size: int = 3, python_exe: str | None = None,
                 timeout_s: float = 60.0, *,
                 writable_dirs: list[str] | None = None,
                 quotas=None, posture: str | None = None, on_usage=None):
        self.size = max(1, int(size))
        # Confinement, quotas and posture are passed through unchanged: each
        # worker plans its own, so each gets its **own** private temp dir (and,
        # later, its own cgroup) rather than sharing one.
        #
        # `pool_size` is the exception, and it is not a per-worker fact: it is
        # how many of us share this uid's RLIMIT_NPROC budget. Without it every
        # slot claimed the same "live count + headroom" and the third worker
        # forked into a budget the first two had already spent (review C2).
        self._workers: list[KernelClient] = [
            KernelClient(python_exe=python_exe, timeout_s=timeout_s,
                         writable_dirs=writable_dirs, quotas=quotas,
                         posture=posture, on_usage=on_usage,
                         name=f"worker-{index}", pool_size=self.size)
            for index in range(self.size)
        ]
        self._rr = 0
        self._rr_lock = threading.Lock()

        # ------------------------------------------------------- fair gate
        #: At most this many requests in flight per tenant. `size - 1` is the
        #: whole guarantee: one worker's worth of concurrency is never
        #: claimable by a single tenant. Floored at 1 so a single-worker pool
        #: still serves (there it is exactly today's behaviour — the worker's
        #: own lock was already serializing).
        self.tenant_limit = max(1, self.size - 1)
        self.queue_depth = TENANT_QUEUE_DEPTH
        self.queue_wait_s = QUEUE_WAIT_S
        #: One lock for all of it. Held only for O(tenants) bookkeeping, never
        #: across a kernel call.
        self._fair_lock = threading.Lock()
        self._inflight: dict[str, int] = {}
        self._waiting: dict[str, deque[threading.Event]] = {}
        #: Tenants with waiters, in service order; the front is next. Rotated
        #: after each admission, which is the round-robin.
        self._rotation: deque[str] = deque()

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
        tenant = tenant_provider()
        if tenant is None:
            # Local mode: the historical line, byte for byte. No key building,
            # no accounting, no lock.
            return self._pick(affinity).request(method, params, timeout_s=timeout_s)
        key = _tenant_key(tenant)
        self._admit(key)
        try:
            return self._pick(_namespaced(key, affinity)).request(
                method, params, timeout_s=timeout_s)
        finally:
            self._release(key)

    # ------------------------------------------------------------ fair gate

    def _admit(self, key: str) -> None:
        """Take one of ``key``'s slots, waiting FIFO or refusing.

        Three outcomes and no fourth: return holding a slot, block until a
        release hands us one, or raise :class:`KernelBusyError`.
        """
        with self._fair_lock:
            queue = self._waiting.get(key)
            if not queue and self._inflight.get(key, 0) < self.tenant_limit:
                # Free slot AND nobody ahead of us. The second half is what
                # makes the queue FIFO: an arriving request never jumps a
                # waiter of its own tenant.
                self._inflight[key] = self._inflight.get(key, 0) + 1
                return
            if queue is None:
                queue = self._waiting[key] = deque()
            if len(queue) >= self.queue_depth:
                raise self._busy(key, "the kernel is busy for this workspace; "
                                      "please retry shortly")
            event = threading.Event()
            queue.append(event)
            if key not in self._rotation:
                self._rotation.append(key)

        if event.wait(self.queue_wait_s):
            # The releasing thread incremented `_inflight[key]` for us before
            # setting the event, so the slot is already ours — handing it over
            # under the lock is what stops a third thread taking it in between.
            return

        with self._fair_lock:
            queue = self._waiting.get(key)
            if queue is not None and event in queue:
                queue.remove(event)
                self._prune_locked(key)
                raise self._busy(
                    key, f"waited {self.queue_wait_s:g}s for a kernel slot in "
                         f"this workspace and gave up",
                    waited_s=self.queue_wait_s)
        # Admitted in the gap between the wait timing out and this lock: the
        # slot is ours and refusing it now would leak it.
        return

    def _release(self, key: str) -> None:
        with self._fair_lock:
            remaining = self._inflight.get(key, 0) - 1
            if remaining > 0:
                self._inflight[key] = remaining
            else:
                self._inflight.pop(key, None)
            self._drain_locked()

    def _drain_locked(self) -> None:
        """Hand freed slots to waiters, walking tenants round-robin.

        Called with ``_fair_lock`` held. Each admission increments the
        tenant's in-flight count *here*, before the waiter wakes, and then
        rotates that tenant to the back — so with two tenants queued the
        slots alternate rather than going to whoever happens to be first in a
        dict. Terminates: every pass either removes a rotation entry, admits
        a waiter (bounded by the number of waiters), or rotates once with no
        further tenant left to try.
        """
        attempts = 0
        while self._rotation and attempts < len(self._rotation):
            key = self._rotation[0]
            queue = self._waiting.get(key)
            if not queue:
                self._waiting.pop(key, None)
                self._rotation.popleft()
                attempts = 0
                continue
            if self._inflight.get(key, 0) >= self.tenant_limit:
                self._rotation.rotate(-1)
                attempts += 1
                continue
            self._inflight[key] = self._inflight.get(key, 0) + 1
            queue.popleft().set()
            if queue:
                self._rotation.rotate(-1)
            else:
                self._waiting.pop(key, None)
                self._rotation.popleft()
            attempts = 0

    def _prune_locked(self, key: str) -> None:
        """Drop a tenant that has no waiters left (called with the lock held)."""
        if not self._waiting.get(key):
            self._waiting.pop(key, None)
            try:
                self._rotation.remove(key)
            except ValueError:
                pass

    def _busy(self, key: str, message: str, **extra) -> KernelBusyError:
        """Build the refusal. Called with ``_fair_lock`` held, or after it."""
        return KernelBusyError(message, {
            "tenant": key,
            "in_flight": self._inflight.get(key, 0),
            "queued": len(self._waiting.get(key) or ()),
            "limit": self.tenant_limit,
            "queue_depth": self.queue_depth,
            "kernel_pool_size": self.size,
            "retry_after_s": BUSY_RETRY_AFTER_S,
            **extra,
        })


def _tenant_key(tenant) -> str:
    """``(org, ws)`` -> ``"org/ws"`` — the accounting key and affinity prefix.

    Tolerant of a plain string because ``tenant_provider`` is a rebindable
    seam and a caller that supplies one should get sane behaviour rather than
    a crash on the hot path.
    """
    if isinstance(tenant, str):
        return tenant
    return "/".join(str(part) for part in tenant)


def _namespaced(key: str, affinity: str | None) -> str | None:
    """``"org/ws:partid"``, or ``None`` left alone.

    An unkeyed request stays unkeyed: it round-robins, and prefixing ``None``
    would silently turn every tenant's unkeyed traffic into one hot affinity.
    """
    return None if affinity is None else f"{key}:{affinity}"
