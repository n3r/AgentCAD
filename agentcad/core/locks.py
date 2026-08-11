"""Per-project turn locks, per-part soft claims, and client identity plumbing.

A turn lock is advisory-but-enforced: any client may acquire the turn on a
project; while it is held, persistent writes by *other* clients are rejected
with a ConflictError naming the holder, until release or TTL expiry. With no
lock held, nothing changes — every write works exactly as before.

A **claim** (PRD-008, design Decision 14) is the finer-grained sibling: it
names one *part*, it is taken implicitly by editing rather than explicitly by
asking, and it is **human-vs-human only**. Three rules, in this order, on every
persistent write:

1. the turn lock decides first, with its existing code path, message and
   details (AC6's regression gate is that ``tests/test_locks.py`` passes
   unmodified);
2. the client holding the turn is **never** claim-checked (FR12);
3. a part claimed by a *different* client conflicts only when the holder and
   the caller are both ``human``. If an agent's write were blocked by a human's
   open editor, the product's flagship loop — human pins a comment on a face,
   agent fixes it and replies — would 409 on the agent's very first write.
   Agents are governed by turns; claims stop two humans clobbering each other.

Identity travels on a ContextVar so it flows naturally through the service
layer regardless of entry point (HTTP middleware sets it per request from the
``X-Agent-Id`` header; the chat engine sets it to ``"chat"`` inside its tool
executor; plain library use defaults to ``"local"``). The *part* a write names
travels the same way, through :func:`write_scope`, for a reason worth stating:
``ProjectStore.write_guard`` is ``Callable[[str], None]``, and widening it to
carry a part would mean editing ``service.py``'s default lambda,
``tools_versioning.install_write_guard`` and every guard any test installs. A
contextvar reaches the guard without changing that seam, and every guard that
ignores it behaves byte-identically.
"""

from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager

from .model import ConflictError

DEFAULT_TTL_S = 120.0
MIN_TTL_S = 5.0
MAX_TTL_S = 3600.0

#: A claim outlives a pause in typing but not a walk to the kitchen. Refreshed
#: by every heartbeat that says ``claim: true`` and by every part-scoped write.
CLAIM_TTL_S = 90.0
#: An armed override is single-use and short: it exists to carry exactly one
#: retry from a conflict dialog to the write it was shown for.
OVERRIDE_TTL_S = 30.0

client_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentcad_client_id", default="local"
)
#: The part the innermost persistent write names, or None for a whole-manifest
#: write (which is turn-locked only, by design — a claim is a *part* claim).
write_part_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentcad_write_part", default=None
)
#: Set by ``with claim_override():`` — the library/tool entry point to the same
#: override the browser arms over HTTP.
override_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agentcad_claim_override", default=False
)


def current_client_id() -> str:
    """The calling context's client identity ("local" when never set)."""
    return client_id_var.get()


def set_client_id(cid: str) -> None:
    """Set the calling context's client identity."""
    client_id_var.set(cid)


def current_write_part() -> str | None:
    """The part the write in progress names, or None."""
    return write_part_var.get()


@contextmanager
def write_scope(part: str | None):
    """Name the part a persistent write is about, for the write guard.

    Nests safely (the token is reset in ``finally``), and is a no-op for every
    guard that does not look — which is all of them but the claim guard.
    """
    token = write_part_var.set(part)
    try:
        yield
    finally:
        write_part_var.reset(token)


@contextmanager
def claim_override(on: bool = True):
    """Let this context's writes take a part another human is holding.

    The deliberate way to say "yes, I know, take it anyway" from a tool or from
    library code; the browser arms the same override out of band, because the
    two part-write routes live in ``app.py`` and cannot grow a body key.
    """
    token = override_var.set(bool(on))
    try:
        yield
    finally:
        override_var.reset(token)


def _kind(identity: str) -> str:
    """``human`` / ``agent`` for an identity.

    Imported lazily on purpose: ``proposals`` imports this module, so a
    module-level import would be a cycle. The rule itself is defined once, in
    ``proposals.actor_kind``, and is never re-implemented here.
    """
    from .proposals import actor_kind

    return actor_kind(identity)


class TurnLock:
    """Thread-safe per-project turn locks with wall-clock TTL expiry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # project -> (holder, expires_at); expired entries are treated as free.
        self._held: dict[str, tuple[str, float]] = {}

    @staticmethod
    def _conflict(holder: str, expires_at: float) -> ConflictError:
        return ConflictError(
            f"project is locked by {holder}",
            {"holder": holder, "expires_at": expires_at},
        )

    def acquire(self, project: str, holder: str,
                ttl_s: float = DEFAULT_TTL_S) -> dict:
        """Take (or refresh) the turn. Succeeds when the lock is free, expired,
        or already held by ``holder``; otherwise raises ConflictError."""
        ttl = min(max(float(ttl_s), MIN_TTL_S), MAX_TTL_S)
        now = time.time()
        with self._lock:
            current = self._held.get(project)
            if current is not None:
                other, expires_at = current
                if other != holder and expires_at > now:
                    raise self._conflict(other, expires_at)
            expires_at = now + ttl
            self._held[project] = (holder, expires_at)
            return {"holder": holder, "expires_at": expires_at}

    def release(self, project: str, holder: str) -> dict:
        """Release the turn. Not held (or expired) is a no-op returning
        ``{"released": False}``; held by another raises ConflictError."""
        now = time.time()
        with self._lock:
            current = self._held.get(project)
            if current is None or current[1] <= now:
                self._held.pop(project, None)
                return {"released": False}
            other, expires_at = current
            if other != holder:
                raise self._conflict(other, expires_at)
            del self._held[project]
            return {"released": True}

    def get(self, project: str) -> dict | None:
        """Current lock info, or None when free/expired."""
        now = time.time()
        with self._lock:
            current = self._held.get(project)
            if current is None:
                return None
            holder, expires_at = current
            if expires_at <= now:
                del self._held[project]
                return None
            return {"holder": holder, "expires_at": expires_at}

    def check(self, project: str, client_id: str) -> None:
        """Raise ConflictError when the turn is held by someone else and
        unexpired. Free, expired, or own lock: fine."""
        now = time.time()
        with self._lock:
            current = self._held.get(project)
            if current is None:
                return
            holder, expires_at = current
            if holder != client_id and expires_at > now:
                raise self._conflict(holder, expires_at)


class ClaimRegistry:
    """Thread-safe per-part soft claims with wall-clock TTL expiry.

    Built exactly like :class:`TurnLock` — one ordinary lock, one dict, no
    background thread, raise-never-block — and keyed by the same branch-aware
    ``store.lock_key(proj)``, so two clients on two branches never contend.

    "Soft" is the whole design: a claim is *taken by editing*, never asked for;
    it expires on its own; it binds only two humans; and it can always be
    overridden by a person who decides they need the part. It is a way to stop
    two people silently clobbering each other, not a permission system — this
    server has no authentication to build one on.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (lock_key, part) -> claim dict
        self._held: dict[tuple[str, str], dict] = {}
        # (lock_key, part, client_id) -> expiry of a single-use override
        self._armed: dict[tuple[str, str, str], float] = {}

    # ------------------------------------------------------------- internals

    @staticmethod
    def _record(part: str, holder: str, expires_at: float) -> dict:
        return {"part": part, "holder": holder,
                "holder_kind": _kind(holder), "expires_at": expires_at}

    def _live(self, key: str, part: str, now: float) -> dict | None:
        """The unexpired claim on one part, dropping it if it has expired.
        Expiry is lazy here for the same reason it is in the presence
        registry: a reaper thread would be a lifecycle to own for no gain."""
        claim = self._held.get((key, part))
        if claim is None:
            return None
        if claim["expires_at"] <= now:
            del self._held[(key, part)]
            return None
        return claim

    # ---------------------------------------------------------------- public

    def acquire(self, key: str, part: str, holder: str,
                ttl_s: float = CLAIM_TTL_S, *, force: bool = False) -> dict:
        """Take or refresh the claim on ``part``.

        Free, expired or already ours: taken. Held by somebody else: returned
        **unchanged** unless ``force`` — a claim is never stolen by accident,
        only by an override the caller asked for. Never raises: refusing to
        write is :meth:`check`'s job, and doing both here would make every
        caller reason about two failure modes.
        """
        ttl = min(max(float(ttl_s), MIN_TTL_S), MAX_TTL_S)
        now = time.time()
        with self._lock:
            current = self._live(key, part, now)
            if current is not None and current["holder"] != holder and not force:
                return dict(current)
            claim = self._record(part, holder, now + ttl)
            self._held[(key, part)] = claim
            return dict(claim)

    def release(self, key: str, part: str, holder: str) -> dict:
        """Give up our own claim. Somebody else's is left alone (a release is
        not an override), so this is always safe to call."""
        now = time.time()
        with self._lock:
            current = self._live(key, part, now)
            if current is None or current["holder"] != holder:
                return {"released": False}
            del self._held[(key, part)]
            return {"released": True}

    def release_all(self, key: str, holder: str) -> list[str]:
        """Every part this client holds, released — the ``pagehide`` path.
        Returns the parts, so the caller can announce them."""
        now = time.time()
        with self._lock:
            mine = [p for (k, p), c in self._held.items()
                    if k == key and c["holder"] == holder
                    and c["expires_at"] > now]
            for part in mine:
                del self._held[(key, part)]
            return mine

    def get(self, key: str, part: str) -> dict | None:
        """The current claim on one part, or None when free/expired."""
        with self._lock:
            claim = self._live(key, part, time.time())
            return dict(claim) if claim else None

    def all(self, key: str) -> dict[str, dict]:
        """Every live claim on one working tree, keyed by part."""
        now = time.time()
        with self._lock:
            for slot in [s for s, c in self._held.items()
                         if s[0] == key and c["expires_at"] <= now]:
                del self._held[slot]
            return {p: dict(c) for (k, p), c in self._held.items() if k == key}

    def arm_override(self, key: str, part: str, client_id: str,
                     ttl_s: float = OVERRIDE_TTL_S) -> dict:
        """Arm a single-use override for one identity and part.

        What the browser's conflict dialog calls before retrying: the two
        part-write routes live in ``app.py``, a core this feature may not edit,
        so the override cannot ride on the write itself. Library and tool
        callers use ``with claim_override():`` instead — one mechanism, two
        entry points.
        """
        expires_at = time.time() + max(float(ttl_s), 1.0)
        with self._lock:
            self._armed[(key, part, client_id)] = expires_at
        return {"part": part, "client_id": client_id,
                "armed_until": expires_at}

    def _consume(self, key: str, part: str, client_id: str) -> bool:
        """Spend an armed override, if there is a live one. Single-use: the
        dialog authorized *this* write, not a session of them."""
        slot = (key, part, client_id)
        with self._lock:
            expires_at = self._armed.pop(slot, None)
        return expires_at is not None and expires_at > time.time()

    def check(self, key: str, part: str | None, client_id: str, *,
              override: bool = False) -> None:
        """Raise ConflictError when ``part`` is claimed by a different human.

        Whole-manifest writes (``part is None``), an agent on either side, our
        own claim, an expired one, an armed override or an enclosing
        ``claim_override()`` all pass. The error names the holder and says
        ``overridable`` so a UI can offer the one button that resolves it.
        """
        if part is None:
            return
        claim = self.get(key, part)
        if claim is None or claim["holder"] == client_id:
            return
        if claim["holder_kind"] != "human" or _kind(client_id) != "human":
            return  # claims are human-vs-human only (FR11, design Decision 14)
        if override or override_var.get() or self._consume(key, part, client_id):
            return
        raise ConflictError(
            f"{claim['holder']} is editing {part}",
            {"claim": claim, "overridable": True},
        )

    def claim_write(self, key: str, part: str | None,
                    client_id: str) -> dict | None:
        """:meth:`check` plus the acquisition policy, for the write guard.

        Returns None when there was nothing to do, else
        ``{claim, changed, overridden}``. The policy in one sentence: a write
        takes the claim when the part is free or already ours, steals it when
        an override was spent, and leaves it exactly where it was when we got
        through only because one of the two parties is an agent — an agent's
        write must not quietly evict the human who is still typing.
        """
        if part is None:
            return None
        before = self.get(key, part)
        overridden = bool(
            before is not None and before["holder"] != client_id
            and (override_var.get()
                 or self._consume(key, part, client_id))
        )
        if not overridden:
            self.check(key, part, client_id)
        if before is not None and before["holder"] != client_id and not overridden:
            return None  # an agent writing under a human's claim: not ours
        claim = self.acquire(key, part, client_id, force=overridden)
        changed = before is None or before["holder"] != client_id
        return {"claim": claim, "changed": changed, "overridden": overridden}
