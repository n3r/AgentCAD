"""Live presence: who else is on this project, and where they are looking.

Four rules, and each of them is a design decision rather than an
implementation detail (PRD-008, design Decision 13):

1. **Presence is fed by an HTTP heartbeat, never by a client→server
   WebSocket.** ``/ws`` lives in ``server/app.py`` — a core the extension-point
   contract forbids editing to add a feature — it carries no client identity
   (``set_client_id`` is called only by the HTTP middleware), and its
   origin/Host guard is HTTP middleware too. Over HTTP, presence inherits the
   reviewed guard, the identity plumbing, the error mapping and the rate
   limiting for free, and opens no new inbound channel.
2. **The heartbeat RESPONSE is the mechanism.** Every ``touch`` answers with
   the whole roster, so a client that misses every event still converges within
   one heartbeat. ``presence_changed`` is an optimization on top of that, which
   is why :meth:`PresenceRegistry.touch` reports whether the roster actually
   *changed*: five idle clients beating every 15 s must not be 20 events a
   minute.
3. **Expiry is lazy and there is no reaper thread.** Entries carry a wall-clock
   deadline and are dropped by the read that notices them. A background thread
   in the server process would be a new lifecycle to own for no gain.
4. **Presence is never persisted** (FR9). This is a dict in the service's
   process; a restart empties it, which is the honest answer to "who is here
   *now*". Nothing here touches the store.

Two smaller rules that are easy to get wrong:

* Entries are keyed by ``store.lock_key(proj)`` — the same branch-aware key
  turn locks and undo stacks use — so two clients on two branches do not
  appear to be looking at the same thing. :meth:`mention_ids`, which
  ``CommentManager`` asks for the set of mentionable ids, deliberately spans
  those keys: a mention of a teammate is about the *project*, not the branch.
* ``label`` is display data, never identity. It arrives on an unauthenticated
  heartbeat, so it is capped, stripped of control characters, and never
  written into a thread, an audit line or a lock. ``kind`` is *derived* from
  the identity with :func:`~agentcad.core.proposals.actor_kind` and is never
  taken from the client.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import locks
from .model import ValidationError
from .proposals import actor_kind

#: An entry the client stops refreshing disappears after this long.
PRESENCE_TTL_S = 45.0
#: What the UI is told to beat at — a third of the TTL, so one lost request
#: is not a disappearing avatar.
PRESENCE_HEARTBEAT_S = 15.0

#: FR9's set plus ``proposals``, where reviewers actually are.
SURFACES = ("viewport", "editor", "inspector", "proposals")

MAX_LABEL_CHARS = 40
MAX_PART_CHARS = 64

#: A heartbeat is 1/s with a burst of 5 — enough for the immediate beats a
#: project/part/branch/focus change fires, not enough to be a write amplifier.
RATE_PER_S = 1.0
RATE_BURST = 5.0


def _text(value, limit: int) -> str | None:
    """A client-supplied display string, or None. Control characters go (this
    is rendered in someone else's toolbar) and the length is capped."""
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value.strip() if ch.isprintable())
    return cleaned[:limit] or None


class TokenBucket:
    """Per-identity rate limit for the heartbeat.

    Over-rate calls are answered with the roster and ``throttled: true``
    rather than an error: a heartbeat that surfaced as a red toast would teach
    users to distrust a status indicator.
    """

    def __init__(self, rate: float = RATE_PER_S, burst: float = RATE_BURST,
                 clock: Callable[[], float] = time.time) -> None:
        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def take(self, who: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(who, (self._burst, now))
            tokens = min(self._burst, tokens + (now - last) * self._rate)
            if tokens < 1.0:
                self._buckets[who] = (tokens, now)
                return False
            self._buckets[who] = (tokens - 1.0, now)
            return True


class PresenceRegistry:
    """In-memory, TTL'd roster keyed ``(lock_key, client_id)``.

    Thread-safe by one ordinary lock, like :class:`~agentcad.core.locks
    .TurnLock`: every operation is O(roster) and none of them blocks.
    """

    def __init__(self, service=None, *,
                 clock: Callable[[], float] = time.time) -> None:
        self.service = service
        self._clock = clock
        self._lock = threading.Lock()
        # (lock_key, client_id) -> entry
        self._clients: dict[tuple[str, str], dict] = {}

    # ------------------------------------------------------------- internals

    @staticmethod
    def _public(entry: dict) -> dict:
        """What other clients are told. ``seen``/``expires`` stay private:
        they are bookkeeping, and a UI that rendered them would be inviting
        people to read a heartbeat as a status."""
        return {
            "id": entry["id"],
            "kind": entry["kind"],
            "label": entry["label"],
            "focus": {"part_id": entry["part_id"],
                      "surface": entry["surface"]},
            "since": entry["since"],
        }

    def _prune(self, now: float) -> bool:
        """Drop expired entries. Called under the lock by every operation —
        this is the whole of expiry (rule 3). Returns whether anything went."""
        dead = [k for k, e in self._clients.items() if e["expires"] <= now]
        for key in dead:
            del self._clients[key]
        return bool(dead)

    # ---------------------------------------------------------------- public

    def touch(self, key: str, client_id: str, *, project: str,
              part_id: str | None = None, surface: str | None = None,
              label: str | None = None) -> tuple[dict, bool]:
        """Register or refresh one client. Returns ``(entry, changed)``, where
        ``changed`` is true only when the roster others can see differs — a
        join, a leave someone else's read collected, a focus or label change.
        An idle heartbeat returns ``False`` and must publish nothing."""
        if surface is not None and surface not in SURFACES:
            raise ValidationError(
                f"unknown presence surface {surface!r}",
                {"known": list(SURFACES)},
            )
        now = self._clock()
        with self._lock:
            changed = self._prune(now)
            slot = (key, client_id)
            previous = self._clients.get(slot)
            entry = {
                "id": client_id,
                "kind": actor_kind(client_id),
                "label": _text(label, MAX_LABEL_CHARS) or client_id,
                "part_id": _text(part_id, MAX_PART_CHARS),
                "surface": surface or "viewport",
                "project": project,
                "since": previous["since"] if previous else now,
                "seen": now,
                "expires": now + PRESENCE_TTL_S,
            }
            self._clients[slot] = entry
            if previous is None or self._public(previous) != self._public(entry):
                changed = True
            return dict(entry), changed

    def leave(self, key: str, client_id: str) -> bool:
        """Drop one client (the ``pagehide`` beacon). Returns whether the
        roster changed, so a double leave publishes once."""
        now = self._clock()
        with self._lock:
            changed = self._prune(now)
            return self._clients.pop((key, client_id), None) is not None or changed

    def roster(self, key: str) -> list[dict]:
        """Everyone present on one working tree, oldest arrival first."""
        now = self._clock()
        with self._lock:
            self._prune(now)
            entries = [e for (k, _cid), e in self._clients.items() if k == key]
        return [self._public(e) for e in sorted(entries, key=lambda e: e["since"])]

    def mention_ids(self, project: str) -> set[str]:
        """Client ids ``@mentions`` in *project* may plausibly name.

        Spans lock keys on purpose (see the module docstring) and is the only
        method ``CommentManager`` calls — it treats an absent or raising
        registry as "nobody", so this must stay cheap and total.
        """
        now = self._clock()
        with self._lock:
            self._prune(now)
            return {e["id"] for e in self._clients.values()
                    if e["project"] == project}

    def claims(self, key: str) -> dict:
        """The claim map that rides along with every roster.

        Empty until the claims pack (slice 7) installs ``service.claims``;
        read lazily and defensively so presence works with or without it.
        """
        registry = getattr(self.service, "claims", None)
        if registry is None:
            return {}
        try:
            return registry.all(key)
        except Exception:                                      # noqa: BLE001
            return {}

    def payload(self, key: str, project: str, you: str, **extra) -> dict:
        """The heartbeat's whole answer — the mechanism, not a courtesy."""
        return {
            "you": you,
            "clients": self.roster(key),
            "claims": self.claims(key),
            "ttl_s": PRESENCE_TTL_S,
            "heartbeat_s": PRESENCE_HEARTBEAT_S,
            **extra,
        }

    def publish(self, key: str, project: str) -> None:
        """Announce a roster change. Never ``project_changed``: who is looking
        at a part is not a change to the model, and the bus's ``on_publish``
        hook snapshots history on that one."""
        bus = getattr(self.service, "bus", None)
        if bus is None:
            return
        bus.publish({
            "type": "presence_changed",
            "project": project,
            "clients": self.roster(key),
            "claims": self.claims(key),
        })


def ensure_presence(service) -> PresenceRegistry:
    """Install ``service.presence`` once, idempotently.

    Called from ``routes_presence.build_router`` rather than from a tool pack:
    presence has no tools (an agent's presence is its writes), and a service
    built without the server — a hand-built registry, ``checks.py``'s ephemeral
    one — is then exactly as it was, with ``CommentManager`` falling back to
    the two static identity families for mentions.
    """
    found = getattr(service, "presence", None)
    if not isinstance(found, PresenceRegistry):
        found = PresenceRegistry(service)
        service.presence = found
    return found


# ---------------------------------------------------------------- the claims
#
# Claims live beside presence rather than in a module of their own because
# they are the same fact seen twice — "somebody is editing this part" is what
# the avatar strip shows and what the write guard enforces — and because one
# route pack owns both surfaces. The *mechanism* is in ``core/locks.py``
# (:class:`~agentcad.core.locks.ClaimRegistry`); what follows is only the
# wiring into the store's one write seam.


def ensure_claims(service) -> locks.ClaimRegistry:
    """Install ``service.claims`` once, idempotently."""
    found = getattr(service, "claims", None)
    if not isinstance(found, locks.ClaimRegistry):
        found = locks.ClaimRegistry()
        service.claims = found
    return found


def publish_claim(service, proj: str, part: str, claim: dict | None,
                  overridden_by: str | None = None) -> None:
    """Announce one part's claim changing hands (or being let go).

    Never ``project_changed``: who is holding a part is not a change to the
    model, and that event snapshots history.
    """
    bus = getattr(service, "bus", None)
    if bus is None:
        return
    event = {
        "type": "claim_changed",
        "project": proj,
        "part": part,
        "holder": claim["holder"] if claim else None,
        "holder_kind": claim["holder_kind"] if claim else None,
        "expires_at": claim["expires_at"] if claim else None,
    }
    if overridden_by:
        event["overridden_by"] = overridden_by
    bus.publish(event)


def ensure_claim_guard(service) -> None:
    """Wrap ``store.write_guard`` with the claim check, idempotently.

    **Lazy on purpose** (risk R7). Tool packs load alphabetically and
    ``tools_versioning`` (``v``) *replaces* ``write_guard`` — it does not wrap
    it — so anything installed at ``c`` would be silently discarded. This runs
    from ``routes_presence.build_router`` (route packs mount after every tool
    pack) and from every claims entry point, which is exactly how
    ``ProposalManager`` installs its branch-delete guard.

    The wrapper calls the previous guard **first**, so ``ensure_checkout`` and
    the turn check keep their order and their errors: a turn held by another
    client raises the existing ``ConflictError``, unchanged, before a claim is
    ever consulted (AC6's regression gate).

    Kill switch: ``service.claims = None`` turns the wrapper into a
    passthrough — the registry is read on every call, never captured.
    """
    store = service.store
    previous = store.write_guard
    if getattr(previous, "_claims_installed", False):
        return
    ensure_claims(service)

    def guard(proj: str) -> None:
        if previous is not None:
            previous(proj)                       # ensure_checkout + the turn
        part = locks.current_write_part()
        claims = getattr(service, "claims", None)
        if part is None or claims is None:
            # A whole-manifest write (add/remove part, assembly, materials,
            # restore, undo) is turn-locked ONLY, by design: a claim is a part
            # claim, and guarding project-wide operations with one would be a
            # promise this feature cannot keep.
            return
        key = store.lock_key(proj)
        who = locks.current_client_id()
        turn = service.turnlock.get(key)
        if turn is not None and turn["holder"] == who:
            return          # FR12: the turn holder is never claim-checked
        outcome = claims.claim_write(key, part, who)   # raises on conflict
        if outcome and outcome["changed"]:
            publish_claim(service, proj, part, outcome["claim"],
                          who if outcome["overridden"] else None)

    guard._claims_installed = True
    store.write_guard = guard


def sync_claim(service, key: str, proj: str, who: str, part: str | None,
               wanted: bool) -> bool:
    """Bring one client's claims in line with its heartbeat.

    ``claim: true`` takes (or refreshes) the claim on the part it is focused
    on; anything else lets go of everything that client held here. That is the
    rule FR11 wants stated once: **viewing never claims** — a claim means a
    dirty editor buffer or a control being dragged, and a claim nobody is
    actually using is worse than none, because it teaches people to click
    Override reflexively.
    """
    claims = getattr(service, "claims", None)
    if claims is None:
        return False
    keep = part if (wanted and part) else None
    changed = False
    if keep:
        before = claims.get(key, keep)
        claim = claims.acquire(key, keep, who)
        if claim["holder"] == who and (before is None
                                       or before["holder"] != who):
            publish_claim(service, proj, keep, claim)
            changed = True
    for held, claim in claims.all(key).items():
        if claim["holder"] != who or held == keep:
            continue
        if claims.release(key, held, who)["released"]:
            publish_claim(service, proj, held, None)
            changed = True
    return changed
