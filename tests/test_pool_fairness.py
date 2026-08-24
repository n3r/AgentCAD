"""Per-tenant fair kernel scheduling (PRD-005 slice 7, FR11).

Everything here runs against a **fake worker** that has the KernelClient
request surface and the one property that matters for scheduling — a single
in-flight lock — and nothing else. No OCCT, no subprocess, no sleeps used as
logic: every wait is a `Condition.wait_for` on state the fake publishes, and
every worker body is released by an explicit credit from the test.

What is proven here, in the order the design spec (§7) states it:

* **Local mode is unchanged.** With no tenant the pool takes the historical
  line — pinned against an oracle re-implementation of the pre-change `_pick`,
  and by sabotaging `_admit` so the test fails loudly if the untenanted path
  ever enters the gate.
* **Namespaced affinity** keeps two orgs' identically-named parts off one
  worker's LRU slot name.
* **The per-tenant cap** (`max(1, size - 1)`) holds under a flood, and a quiet
  tenant is admitted without waiting for a single completion of the flood.
* **Queue overflow** refuses with `KernelBusyError` — 429, wire type
  `kernelbusy_error`.
* **The drain is FIFO per tenant and round-robin across tenants.**
* **It is thread-safe** — 8 threads x 50 requests, all served, accounting back
  to zero.

What is deliberately NOT claimed: the gate bounds *entry*, not per-worker
queueing. Two tenants whose namespaced affinities hash to the same worker
still queue behind that worker's single in-flight lock, exactly as two part
ids of one tenant do today. The flood test therefore measures the thing the
gate actually controls (admission) and pins the end-to-end bound in the
non-colliding case, which is the case the namespacing makes the common one.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque

import pytest

from agentcad.core import tenancy
from agentcad.core.model import RateLimitedError, error_type
from agentcad.kernel import pool as pool_mod
from agentcad.kernel.pool import KernelBusyError, KernelPool, _namespaced

ORG_A = ("acme", "main")
ORG_B = ("globex", "main")
KEY_A = "acme/main"
KEY_B = "globex/main"

# A generous ceiling for every `wait_for` in this module. It is a deadlock
# detector, not a timing assumption: the events it waits on are set by threads
# that have no work to do but set them.
DEADLINE = 10.0


# --------------------------------------------------------------- the fake

class _Recorder:
    """Shared observation point for every fake worker in one pool."""

    def __init__(self):
        self.cv = threading.Condition()
        #: tenant -> requests past the pool's gate right now (the number the
        #: per-tenant cap is a bound on).
        self.gated: dict[str, int] = {}
        self.max_gated: dict[str, int] = {}
        #: (worker name, tenant, label) in the order bodies actually started.
        self.bodies: list[tuple[str, str, str]] = []
        #: completions at the moment each labelled body started.
        self.start_marks: dict[str, int] = {}
        self.completions = 0
        #: When metered, a body may not finish until the test grants a credit.
        self.metered = False
        self.credits = threading.Semaphore(0)

    # the fake worker calls these ------------------------------------------

    def enter_gate(self, tenant: str) -> None:
        with self.cv:
            self.gated[tenant] = self.gated.get(tenant, 0) + 1
            self.max_gated[tenant] = max(self.max_gated.get(tenant, 0),
                                         self.gated[tenant])
            self.cv.notify_all()

    def leave_gate(self, tenant: str) -> None:
        with self.cv:
            self.gated[tenant] -= 1
            self.cv.notify_all()

    def enter_body(self, worker: str, tenant: str, label: str) -> None:
        with self.cv:
            self.bodies.append((worker, tenant, label))
            self.start_marks[label] = self.completions
            self.cv.notify_all()

    def leave_body(self) -> None:
        with self.cv:
            self.completions += 1
            self.cv.notify_all()

    def hold(self) -> None:
        if self.metered:
            self.credits.acquire()

    # the test calls these -------------------------------------------------

    def allow(self, count: int) -> None:
        for _ in range(count):
            self.credits.release()

    def wait_for(self, predicate) -> None:
        with self.cv:
            assert self.cv.wait_for(predicate, timeout=DEADLINE), (
                f"timed out; gated={self.gated} bodies={len(self.bodies)} "
                f"completions={self.completions}")


class _FakeWorker:
    """A KernelClient's request surface, and its single in-flight lock.

    The lock is not decoration: it is the only serialization the real pool
    relies on, so a fairness layer tested without it would be tested against a
    pool that does not exist.
    """

    def __init__(self, rec: _Recorder, **kwargs):
        self._rec = rec
        self.name = kwargs.get("name") or "worker-0"
        self._lock = threading.Lock()
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    @property
    def alive(self) -> bool:
        return self.started and not self.stopped

    def request(self, method: str, params: dict, timeout_s: float | None = None):
        tenant = params.get("tenant", "-")
        label = params.get("label", "-")
        self._rec.enter_gate(tenant)
        try:
            with self._lock:
                self._rec.enter_body(self.name, tenant, label)
                self._rec.hold()
                self._rec.leave_body()
            return {"worker": self.name, "method": method}
        finally:
            self._rec.leave_gate(tenant)


def _make_pool(monkeypatch, size: int) -> tuple[KernelPool, _Recorder]:
    rec = _Recorder()
    monkeypatch.setattr(pool_mod, "KernelClient",
                        lambda **kw: _FakeWorker(rec, **kw))
    return KernelPool(size=size), rec


def _idle(pool: KernelPool) -> bool:
    """The gate's accounting is back to nothing."""
    return (pool._inflight == {} and pool._waiting == {}
            and len(pool._rotation) == 0)


def _wait_until(predicate, what: str) -> None:
    """Poll pool-internal state (which publishes no condition of its own).

    A bounded poll on a predicate, never a fixed sleep standing in for one.
    """
    deadline = time.monotonic() + DEADLINE
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError(f"timed out waiting for {what}")


def _run(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


def _tenant_request(pool, tenant, label, affinity, errors=None):
    """One request, from a thread that sets its own ambient tenant.

    A ContextVar is not inherited by `threading.Thread`, which is exactly the
    hosted shape: every request thread resolves its own tenant.
    """
    def body():
        with tenancy.tenant_scope(tenant):
            try:
                pool.request("build",
                             {"tenant": f"{tenant[0]}/{tenant[1]}", "label": label},
                             affinity=affinity)
            except BaseException as exc:  # noqa: BLE001 - the test re-raises
                if errors is None:
                    raise
                errors.append(exc)
    return _run(body)


# ------------------------------------------------------- local mode (AC7)

class _OraclePick:
    """The pre-change `_pick`, re-implemented from the diff's parent.

        if affinity is not None:
            return self._workers[hash(affinity) % self.size]
        with self._rr_lock:
            worker = self._workers[self._rr % self.size]
            self._rr += 1
    """

    def __init__(self, size: int):
        self.size = size
        self.rr = 0

    def pick(self, affinity: str | None) -> str:
        if affinity is not None:
            return f"worker-{hash(affinity) % self.size}"
        index = self.rr % self.size
        self.rr += 1
        return f"worker-{index}"


def test_local_mode_worker_selection_is_byte_for_byte_the_old_pick(monkeypatch):
    """No tenant -> the historical routing, request for request.

    Nothing about tenancy is monkeypatched: `tenant_provider` performs its real
    lazy import of `core.tenancy` and reads the real ContextVar, whose default
    is `None`. That default IS local mode.
    """
    pool, rec = _make_pool(monkeypatch, size=3)
    oracle = _OraclePick(3)
    # A scripted series mixing keyed and unkeyed requests, with repeats: the
    # round-robin cursor must advance on unkeyed requests only, and a repeated
    # key must return to its worker.
    series = [None, "part_a", None, "part_b", "part_b", None, None,
              "part_c", "part_a", None, "part_c", None]

    for label, affinity in enumerate(series):
        chosen = pool.request("ping", {"label": str(label)}, affinity=affinity)
        assert chosen["worker"] == oracle.pick(affinity)

    assert pool._rr == sum(1 for a in series if a is None)
    assert _idle(pool)


def test_local_mode_never_enters_the_fair_gate(monkeypatch):
    """The untenanted branch returns before any accounting — proven by sabotage."""
    pool, rec = _make_pool(monkeypatch, size=3)

    def _explode(key):
        raise AssertionError(f"local mode entered the fair gate for {key!r}")

    monkeypatch.setattr(pool, "_admit", _explode)
    for affinity in (None, "part_a", "part_a"):
        pool.request("ping", {}, affinity=affinity)
    assert _idle(pool)


def test_lifecycle_is_untouched(monkeypatch):
    """`start()` still warms worker 0 only; `stop()` stops all; `alive` is any."""
    pool, rec = _make_pool(monkeypatch, size=3)
    assert pool.alive is False
    pool.start()
    assert [w.started for w in pool._workers] == [True, False, False]
    assert pool.alive is True
    pool.stop()
    assert all(w.stopped for w in pool._workers)
    assert pool.alive is False


# ------------------------------------------------------------ namespacing

def test_namespaced_affinity_is_the_tenant_prefixed_key():
    assert _namespaced(KEY_A, "bracket") == "acme/main:bracket"
    # An unkeyed request stays unkeyed: prefixing None would collapse every
    # tenant's round-robin traffic onto one hot affinity.
    assert _namespaced(KEY_A, None) is None


def test_two_tenants_route_the_same_part_id_through_different_keys(monkeypatch):
    pool, rec = _make_pool(monkeypatch, size=3)
    seen: list[str | None] = []
    original = pool._pick
    monkeypatch.setattr(pool, "_pick",
                        lambda affinity: (seen.append(affinity),
                                          original(affinity))[1])

    for tenant in (ORG_A, ORG_B):
        with tenancy.tenant_scope(tenant):
            pool.request("build", {"tenant": "-"}, affinity="bracket")
    with tenancy.tenant_scope(ORG_A):
        pool.request("build", {"tenant": "-"}, affinity=None)

    assert seen == ["acme/main:bracket", "globex/main:bracket", None]


def test_namespacing_moves_part_ids_into_different_buckets(monkeypatch):
    """Equal part ids in two orgs land on different workers for most ids.

    Not "for every id" — two namespaced keys can still collide on a 3-worker
    pool, and pretending otherwise would be the isolation claim the docstring
    refuses to make. The deterministic half is the other assertion: a part id
    is stable within its tenant.
    """
    pool, rec = _make_pool(monkeypatch, size=3)
    part_ids = [f"part_{i}" for i in range(60)]

    def bucket(key: str, part: str) -> int:
        return hash(_namespaced(key, part)) % pool.size

    differing = [p for p in part_ids if bucket(KEY_A, p) != bucket(KEY_B, p)]
    # P(all 60 agree) = 3**-60. A single differing id would already prove the
    # keys are distinct; the bulk assertion proves they are *spread*.
    assert len(differing) > 20

    # Stable within a tenant: the whole point of affinity routing survives.
    for part in part_ids[:5]:
        assert bucket(KEY_A, part) == bucket(KEY_A, part)
        assert pool._pick(_namespaced(KEY_A, part)) is pool._pick(
            _namespaced(KEY_A, part))


# ----------------------------------------------------------- the flood (b)

def test_flood_cannot_take_every_worker_and_a_quiet_tenant_is_admitted(monkeypatch):
    """20 concurrent requests from A + 1 from B, with every body held.

    The bound the gate guarantees: A is never past the gate more than
    `max(1, size - 1)` times at once, and B — whose own cap is untouched by
    A's flood — is admitted and reaches a worker body having waited for
    **zero** completions of the flood, comfortably inside the `size`
    completions the slice contracts for.
    """
    pool, rec = _make_pool(monkeypatch, size=4)
    rec.metered = True
    assert pool.tenant_limit == 3

    # The flood shares one affinity, so it occupies exactly one worker and the
    # measurement below is about the gate rather than about a hash collision.
    flood_worker = hash(_namespaced(KEY_A, "bulk")) % pool.size
    quiet = next(f"part_{i}" for i in itertools.count()
                 if hash(_namespaced(KEY_B, f"part_{i}")) % pool.size != flood_worker)

    threads = [_tenant_request(pool, ORG_A, f"a{i}", "bulk") for i in range(20)]
    # Wait for the gate to settle: 3 admitted, the rest parked in the queue.
    rec.wait_for(lambda: rec.gated.get(KEY_A, 0) == 3)
    with pool._fair_lock:
        assert pool._inflight[KEY_A] == 3
        assert len(pool._waiting[KEY_A]) == 17
    assert rec.completions == 0

    threads.append(_tenant_request(pool, ORG_B, "b0", quiet))
    rec.wait_for(lambda: "b0" in rec.start_marks)

    assert rec.start_marks["b0"] == 0            # not one flood completion
    assert rec.start_marks["b0"] <= pool.size    # the contracted bound
    assert rec.max_gated[KEY_A] <= pool.tenant_limit
    with pool._fair_lock:
        assert pool._inflight[KEY_B] == 1        # B's cap is its own

    rec.allow(1000)
    for thread in threads:
        thread.join(timeout=DEADLINE)
        assert not thread.is_alive()
    assert rec.completions == 21
    assert rec.max_gated[KEY_A] <= pool.tenant_limit
    assert _idle(pool)


def test_flood_bodies_never_occupy_more_than_the_cap(monkeypatch):
    """The cap holds when the flood is spread across every worker, too."""
    pool, rec = _make_pool(monkeypatch, size=4)
    rec.metered = True

    threads = [_tenant_request(pool, ORG_A, f"a{i}", f"part_{i}")
               for i in range(20)]
    rec.wait_for(lambda: rec.gated.get(KEY_A, 0) == pool.tenant_limit)
    # Release one credit at a time: every completion re-admits a queued waiter,
    # and the invariant must survive each hand-off.
    for done in range(1, 21):
        rec.allow(1)
        rec.wait_for(lambda done=done: rec.completions >= done)
        assert rec.max_gated[KEY_A] <= pool.tenant_limit
    for thread in threads:
        thread.join(timeout=DEADLINE)
        assert not thread.is_alive()
    assert rec.max_gated[KEY_A] <= pool.tenant_limit
    assert rec.completions == 20
    assert _idle(pool)


# ------------------------------------------------------- queue overflow (b)

def test_queue_overflow_refuses_with_the_busy_error(monkeypatch):
    pool, rec = _make_pool(monkeypatch, size=2)
    rec.metered = True
    pool.queue_depth = 3
    assert pool.tenant_limit == 1

    threads = [_tenant_request(pool, ORG_A, f"a{i}", "bulk") for i in range(4)]
    rec.wait_for(lambda: rec.gated.get(KEY_A, 0) == 1)

    def parked() -> bool:
        with pool._fair_lock:
            return len(pool._waiting.get(KEY_A, ())) == 3

    rec.wait_for(parked)

    with tenancy.tenant_scope(ORG_A):
        with pytest.raises(KernelBusyError) as caught:
            pool.request("build", {"tenant": KEY_A}, affinity="bulk")

    exc = caught.value
    # The house error family: a 429 that clears when a slot frees, not a 503
    # an operator has to fix (`core/model.py`'s own split).
    assert isinstance(exc, RateLimitedError)
    assert error_type(exc) == "kernelbusy_error"
    assert exc.details["tenant"] == KEY_A
    assert exc.details["limit"] == 1
    assert exc.details["queue_depth"] == 3
    assert exc.details["queued"] == 3
    assert exc.details["kernel_pool_size"] == 2
    assert exc.details["retry_after_s"] == pool_mod.BUSY_RETRY_AFTER_S

    rec.allow(1000)
    for thread in threads:
        thread.join(timeout=DEADLINE)
        assert not thread.is_alive()
    assert _idle(pool)


def test_busy_error_maps_to_429_without_a_core_edit():
    """The status comes from `app.py`'s isinstance walk, which is why the base
    class was chosen rather than a new mapping entry."""
    from agentcad.server.app import _ERROR_STATUS

    exc = KernelBusyError("busy", {})
    status = next(code for cls, code in _ERROR_STATUS.items()
                  if isinstance(exc, cls))
    assert status == 429


def test_a_second_tenant_is_admitted_while_the_first_is_queue_full(monkeypatch):
    """The queue bound is per tenant — that is the whole point of it."""
    pool, rec = _make_pool(monkeypatch, size=2)
    rec.metered = True
    pool.queue_depth = 2

    threads = [_tenant_request(pool, ORG_A, f"a{i}", "bulk") for i in range(3)]
    rec.wait_for(lambda: rec.gated.get(KEY_A, 0) == 1)

    def full() -> bool:
        with pool._fair_lock:
            return len(pool._waiting.get(KEY_A, ())) == 2

    rec.wait_for(full)

    threads.append(_tenant_request(pool, ORG_B, "b0", "bulk"))
    rec.wait_for(lambda: rec.gated.get(KEY_B, 0) == 1)

    rec.allow(1000)
    for thread in threads:
        thread.join(timeout=DEADLINE)
        assert not thread.is_alive()
    assert _idle(pool)


# --------------------------------------------------------- the drain (c)

def test_waiters_are_fifo_within_a_tenant(monkeypatch):
    pool, rec = _make_pool(monkeypatch, size=2)  # tenant_limit == 1
    events = [threading.Event() for _ in range(3)]
    with pool._fair_lock:
        pool._inflight[KEY_A] = 1
        pool._waiting[KEY_A] = deque(events)
        pool._rotation.append(KEY_A)

    pool._release(KEY_A)
    assert [e.is_set() for e in events] == [True, False, False]
    pool._release(KEY_A)
    assert [e.is_set() for e in events] == [True, True, False]
    pool._release(KEY_A)
    assert [e.is_set() for e in events] == [True, True, True]
    # Each release gave its freed slot straight to the next waiter — counted
    # before the waiter woke, which is what stops a fourth thread taking it in
    # between — so the cap of 1 was never exceeded on the way through.
    assert pool._inflight[KEY_A] == 1
    assert pool._waiting == {} and len(pool._rotation) == 0


def test_drain_walks_waiting_tenants_round_robin(monkeypatch):
    """Two tenants with headroom and waiters: one each, alternating.

    Written white-box because with a purely per-tenant cap a *release* only
    ever frees its own tenant's slot; the rotation is what makes a drain that
    can serve several tenants (a raised cap, a withdrawn waiter) serve them in
    turn instead of in dict order.
    """
    pool, rec = _make_pool(monkeypatch, size=4)
    pool.tenant_limit = 2
    events = {}
    with pool._fair_lock:
        for key in (KEY_A, KEY_B):
            pool._inflight[key] = 1
            queue = pool._waiting[key] = deque()
            for index in range(2):
                event = threading.Event()
                queue.append(event)
                events[(key, index)] = event
            pool._rotation.append(key)
        pool._drain_locked()

    assert events[(KEY_A, 0)].is_set() and events[(KEY_B, 0)].is_set()
    assert not events[(KEY_A, 1)].is_set() and not events[(KEY_B, 1)].is_set()
    assert pool._inflight == {KEY_A: 2, KEY_B: 2}
    # A was served, rotated to the back, then B — leaving the order it started
    # in, with A next in line for the following free slot.
    assert list(pool._rotation) == [KEY_A, KEY_B]


def test_a_new_request_never_jumps_a_waiter_of_its_own_tenant(monkeypatch):
    pool, rec = _make_pool(monkeypatch, size=2)  # tenant_limit == 1
    parked = threading.Event()
    with pool._fair_lock:
        pool._inflight[KEY_A] = 1
        pool._waiting[KEY_A] = deque([parked])
        pool._rotation.append(KEY_A)

    admitted = threading.Event()

    def latecomer():
        with tenancy.tenant_scope(ORG_A):
            pool._admit(KEY_A)
        admitted.set()

    thread = _run(latecomer)

    def queued_behind() -> bool:
        with pool._fair_lock:
            return len(pool._waiting.get(KEY_A, ())) == 2

    _wait_until(queued_behind, "the latecomer to queue behind the waiter")
    # The cap is 1 and one slot is held, so the newcomer queues behind the
    # parked waiter rather than taking the slot the release is about to free.
    pool._release(KEY_A)
    assert parked.is_set()
    assert not admitted.is_set()
    pool._release(KEY_A)
    thread.join(timeout=DEADLINE)
    assert admitted.is_set()
    pool._release(KEY_A)
    assert _idle(pool)


def test_a_waiter_that_times_out_refuses_and_withdraws(monkeypatch):
    pool, rec = _make_pool(monkeypatch, size=2)  # tenant_limit == 1
    pool.queue_wait_s = 0.05
    with pool._fair_lock:
        pool._inflight[KEY_A] = 1
        pool._waiting[KEY_A] = deque()
        pool._rotation.append(KEY_A)

    with pytest.raises(KernelBusyError) as caught:
        pool._admit(KEY_A)
    assert caught.value.details["waited_s"] == pytest.approx(0.05)
    # Withdrawn, not left behind to be handed a slot nobody will release.
    with pool._fair_lock:
        assert pool._waiting == {} and len(pool._rotation) == 0
    pool._release(KEY_A)
    assert _idle(pool)


# ---------------------------------------------------------- thread safety

def test_eight_threads_fifty_requests_each(monkeypatch):
    """No deadlock, every request served, accounting back to zero."""
    pool, rec = _make_pool(monkeypatch, size=3)
    assert pool.tenant_limit == 2
    tenants = [("acme", "main"), ("globex", "main"),
               ("initech", "main"), ("umbrella", "main")]
    errors: list[BaseException] = []
    served = itertools.count()
    counted = threading.Lock()
    total = 0

    def worker(index: int):
        nonlocal total
        tenant = tenants[index % len(tenants)]
        key = f"{tenant[0]}/{tenant[1]}"
        with tenancy.tenant_scope(tenant):
            for step in range(50):
                try:
                    affinity = None if step % 5 == 0 else f"part_{step % 7}"
                    pool.request("ping", {"tenant": key, "label": str(next(served))},
                                 affinity=affinity)
                    with counted:
                        total += 1
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

    threads = [_run(worker, index) for index in range(8)]
    for thread in threads:
        thread.join(timeout=DEADLINE * 3)
        assert not thread.is_alive(), "a request thread deadlocked"

    assert errors == []
    assert total == 8 * 50
    assert rec.completions == 8 * 50
    for tenant in tenants:
        key = f"{tenant[0]}/{tenant[1]}"
        assert rec.max_gated[key] <= pool.tenant_limit
    assert _idle(pool)
