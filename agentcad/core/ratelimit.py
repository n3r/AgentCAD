"""A token-bucket rate limiter, shared by every consumer that needs one.

**Promoted here from ``core/presence.py`` (PRD-007 design Decision 6).**
PRD-008 grew ``TokenBucket`` for the presence heartbeat; PRD-007's share and
customizer limiter is its second consumer, and the roadmap asks for the
promotion so it is not imported *from a finished feature's module*. The move is
a pure refactor: the class is verbatim, ``presence`` keeps the name importable
with a one-line re-export, and ``server/security.py``'s login limiter switches
its import here. Behaviour is byte-identical — the presence suite is the
regression test, and ``test_ratelimit.py`` pins the promotion with
``presence.TokenBucket is ratelimit.TokenBucket``.

Nothing here imports geometry; this module joins the fresh-interpreter probe
that proves the server process never pulls in ``OCP``/build123d.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

#: A heartbeat is 1/s with a burst of 5 — the presence defaults, kept here as
#: the bucket's own defaults so a bare ``TokenBucket()`` behaves exactly as it
#: did in ``presence``. ``presence`` re-declares its semantic aliases
#: (``RATE_PER_S``/``RATE_BURST``/``MAX_BUCKETS``) beside its heartbeat prose;
#: the numbers agree by construction and the presence tests hold both to it.
RATE_PER_S = 1.0
RATE_BURST = 5.0

#: Ceiling on live buckets: a bucket is allocated by the first call under an
#: identity, which is *before* any higher-level roster gets a say, so a rotating
#: identity would otherwise leak a dict entry per call.
MAX_BUCKETS = 512


class TokenBucket:
    """Per-identity rate limit for the heartbeat.

    Over-rate calls are answered with the roster and ``throttled: true``
    rather than an error: a heartbeat that surfaced as a red toast would teach
    users to distrust a status indicator.

    **Bounded** at :data:`MAX_BUCKETS`, because a bucket is minted by the first
    heartbeat under an identity and a rotating identity therefore leaks a dict
    entry per beat.

    **Eviction drops only what carries no information.** A bucket refilled to
    its full burst is indistinguishable from an absent one, so dropping it
    grants nobody anything. An LRU pass on top of that was not free and looked
    it: under a rotating flood the least recently used bucket is one somebody
    just spent tokens from, and deleting it hands that identity a brand new
    burst — the limiter paying out exactly where it is being attacked. So when
    nothing has refilled there is no room, and the beat is answered
    ``throttled`` rather than granted at an incumbent's expense. The table
    clears itself: every bucket refills inside ``burst / rate`` seconds (5) and
    is evictable from then on, which also subsumes "evict a bucket with its
    roster row" — a row lives for ``PRESENCE_TTL_S`` (45 s).

    What this still does **not** do is give a rotating identity the per-client
    rate: the limiter cannot tell one beat under a new id from a real
    newcomer's first beat, so each fresh id is granted one. What it does do is
    bound the flood — 5 000 ids used once each get :data:`MAX_BUCKETS` of them
    through and the rest are throttled, where before the fix all 5 000 were
    granted, because the LRU pass kept making room. So the ceiling is one
    grant per bucket per refill window (512 per 5 s), not the 1/s a single
    client gets, and the roster's own ceiling is what bounds the thing a flood
    makes everyone else download. Eviction is deliberately *not* driven by the
    leave beacon, which is self-asserted — that would make "I'm leaving" a way
    to ask for a fresh burst.
    """

    def __init__(self, rate: float = RATE_PER_S, burst: float = RATE_BURST,
                 clock: Callable[[], float] = time.time,
                 limit: int = MAX_BUCKETS) -> None:
        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._limit = max(1, int(limit))
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def _tokens(self, entry: tuple[float, float], now: float) -> float:
        tokens, last = entry
        return min(self._burst, tokens + (now - last) * self._rate)

    def _evict(self, now: float) -> None:
        """Drop every bucket that has refilled to its burst. Under the lock.

        There is no second pass: see the class docstring for why an LRU one
        would be a payout to the flood it is meant to bound.
        """
        for who in [w for w, e in self._buckets.items()
                    if self._tokens(e, now) >= self._burst]:
            del self._buckets[who]

    def take(self, who: str) -> bool:
        now = self._clock()
        with self._lock:
            entry = self._buckets.get(who)
            if entry is None and len(self._buckets) >= self._limit:
                self._evict(now)
                if len(self._buckets) >= self._limit:
                    return False       # no room that is free to make
            tokens = self._tokens(entry or (self._burst, now), now)
            if tokens < 1.0:
                self._buckets[who] = (tokens, now)
                return False
            self._buckets[who] = (tokens - 1.0, now)
            return True

    def forget(self, who: str) -> None:
        """Drop one identity's bucket. For a caller that KNOWS the identity is
        gone for good — never for a client-asserted leave."""
        with self._lock:
            self._buckets.pop(who, None)
