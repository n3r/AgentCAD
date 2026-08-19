"""PRD-007 slice 1: the promoted rate limiter.

``TokenBucket`` was PRD-008's, born inside ``core/presence.py`` for the
heartbeat. PRD-007 is its second consumer (the share/customizer limiter), and
the roadmap calls for promoting it to a module of its own. The move is a **pure
refactor** (design Decision 6): the class is verbatim, ``presence`` keeps the
name importable with a one-line re-export, and these are the cases that used to
live in ``test_presence.py`` — moved unchanged so the behaviour is pinned in
its new home. The re-export identity test is what proves PRD-008 code did not
fork.
"""

from __future__ import annotations

from agentcad.core import presence, ratelimit


class Clock:
    """An injected clock — the bucket takes one, so no test has to monkeypatch
    the global ``time`` module out from under a running kernel process."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_presence_reexports_the_same_object():
    """The promotion is a move, not a copy: ``presence.TokenBucket`` must be
    the *same class object* as ``ratelimit.TokenBucket``, or PRD-008 and
    PRD-007 would be rate-limiting through two subtly different implementations
    that drift. `is`, not `==`."""
    assert presence.TokenBucket is ratelimit.TokenBucket


def test_eviction_never_hands_a_drained_bucket_a_fresh_burst():
    """K-N3: eviction is only free if what it drops carries no information.

    Dropping a bucket that has been refilled to its full burst grants nobody
    anything — an absent bucket and a full one answer identically. Dropping the
    *least recently used* one does not have that property: the LRU entry under
    a rotating flood is a bucket somebody just spent tokens from, and deleting
    it hands that identity a brand new burst. So a table with no refilled
    bucket in it has no room, and the beat is throttled instead of being
    granted at somebody else's expense.
    """
    clock = Clock()
    bucket = ratelimit.TokenBucket(clock=clock, limit=4)
    for n in range(4):
        assert bucket.take(f"browser:{n}") is True        # 4 drained-by-one

    # No room, and nothing evictable: the newcomer is throttled...
    assert bucket.take("browser:new") is False
    assert len(bucket._buckets) == 4                       # noqa: SLF001
    # ...and crucially, browser:0 still has its four remaining tokens, rather
    # than the five a fresh bucket would have handed it.
    assert [bucket.take("browser:0") for _ in range(5)] == \
        [True] * 4 + [False]

    # Once a bucket has refilled to the burst it is indistinguishable from an
    # absent one, so it is what eviction takes.
    clock.advance(ratelimit.RATE_BURST / ratelimit.RATE_PER_S + 1)
    assert bucket.take("browser:new") is True
    assert len(bucket._buckets) <= 4                       # noqa: SLF001


def test_the_token_bucket_refills_at_one_per_second():
    clock = Clock()
    bucket = ratelimit.TokenBucket(clock=clock)
    assert [bucket.take("browser:a") for _ in range(7)] == (
        [True] * 5 + [False, False])
    # Another identity has its own bucket.
    assert bucket.take("browser:b") is True
    clock.advance(2.0)
    assert [bucket.take("browser:a") for _ in range(3)] == [True, True, False]


def test_forget_drops_one_identity():
    """``forget`` is for a caller that KNOWS an identity is gone for good.
    After it, the identity is a newcomer again — a full burst."""
    clock = Clock()
    bucket = ratelimit.TokenBucket(clock=clock)
    for _ in range(5):
        bucket.take("who")
    assert bucket.take("who") is False
    bucket.forget("who")
    assert bucket.take("who") is True
