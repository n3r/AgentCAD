"""Live presence: who else is on this project, and where they are looking.

Five rules, and each of them is a design decision rather than an
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
5. **Everything here is bounded, because nothing here is authenticated.** The
   identity arrives on a header anyone can set and anyone can rotate, so it is
   length-checked (:data:`MAX_ID_CHARS`, refused not truncated) **wherever it
   becomes a key** — which means in ``locks.check_client_id``, not here: the
   presence route was the only door that checked, and a part write carries the
   same header into the claim registry from the write guard. The roster has a
   hard ceiling (:data:`MAX_CLIENTS`) that refuses a *new* row rather than
   evicting an incumbent, and the rate limiter's buckets have one too
   (:data:`MAX_BUCKETS`). That last one is a **memory** bound first: a rotating
   identity is granted its first beat exactly like a real newcomer, so it is
   not held to 1/s — it is held to one grant per bucket per refill window, 512
   per 5 s, because a table with nothing refilled in it has no room to mint
   another. What bounds the flood's cost to everyone else is the roster
   ceiling, and the broadcast needs no separate bound of its own, because a
   ``presence_changed`` frame *is* the roster.

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
# `TokenBucket` was born here for the heartbeat and is now shared. PRD-007
# promoted it to `core/ratelimit.py` (design Decision 6); this one line keeps
# `presence.TokenBucket` importable and behaviour byte-identical, so nothing in
# PRD-008 changes shape and the presence suite remains its regression test.
# It is the ONLY PRD-008 touch that promotion required.
from .ratelimit import TokenBucket  # noqa: F401  (re-export)

#: An entry the client stops refreshing disappears after this long.
PRESENCE_TTL_S = 45.0
#: What the UI is told to beat at — a third of the TTL, so one lost request
#: is not a disappearing avatar.
PRESENCE_HEARTBEAT_S = 15.0

#: FR9's set plus ``proposals``, where reviewers actually are.
SURFACES = ("viewport", "editor", "inspector", "proposals")

MAX_LABEL_CHARS = 40
MAX_PART_CHARS = 64
#: An identity is a self-asserted header on an unauthenticated local server, so
#: it is bounded rather than trusted: it becomes a dict key in the roster, the
#: claim registry and the rate limiter, and it is echoed into everybody else's
#: toolbar. The number and the check live in ``core.locks`` — beside the
#: ContextVar the identity arrives in — because the claim registry is reached
#: by a part write that never passes a presence route. Matches
#: ``routes_presence._beacon_identity``.
MAX_ID_CHARS = locks.MAX_CLIENT_ID_CHARS

#: A heartbeat is 1/s with a burst of 5 — enough for the immediate beats a
#: project/part/branch/focus change fires, not enough to be a write amplifier.
RATE_PER_S = 1.0
RATE_BURST = 5.0

#: Hard ceiling on the whole roster, across every project and branch in the
#: process — deliberately process-wide, because what it bounds is process-wide:
#: the dict lives in one service and a flood on one project would otherwise
#: cost every other project's memory. The refusal says so rather than saying
#: "this project", which is what it used to say and was not true.
#: "One client, one id" is a convention, not a fact — nothing stops a caller
#: rotating ``X-Agent-Id`` — and every row costs a TTL of memory *and* a slot
#: in every ``presence_changed`` frame, so the broadcast grows with the flood.
#: A local CAD session has single-digit clients; this is well past any real
#: use and is still a bound.
MAX_CLIENTS = 200
#: Ceiling on live rate-limit buckets, for the same reason: a bucket is
#: allocated by the first heartbeat under an id, which is *before* the roster
#: gets a say.
MAX_BUCKETS = 512


def _text(value, limit: int) -> str | None:
    """A client-supplied display string, or None. Control characters go (this
    is rendered in someone else's toolbar) and the length is capped."""
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value.strip() if ch.isprintable())
    return cleaned[:limit] or None


def check_identity(value: object) -> str:
    """A client id fit to be a key, or a ValidationError saying why not.

    Refused rather than truncated, unlike :func:`_text`: a label is display
    data and a shortened one is merely shorter, but two identities cut to the
    same 64 characters would be *one* client to the roster, the claims and the
    mentions — a silent identity merge is a worse answer than an error.

    Public because the *route* has to ask before :meth:`PresenceRegistry.touch`
    does: the rate limiter mints a bucket keyed by the raw identity, and it
    runs first, so validating only inside ``touch`` would let an unbounded
    string become a dict key on the way past.

    One line of policy, one implementation: ``locks.check_client_id``, which
    the claim registry calls at its own doors for the same reason.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("presence needs a client identity")
    return locks.check_client_id(value)


#: The heartbeat limiter is :class:`~agentcad.core.ratelimit.TokenBucket`,
#: re-exported at the top of this module. Its defaults (:data:`RATE_PER_S`,
#: :data:`RATE_BURST`, :data:`MAX_BUCKETS`) are the presence-tuning constants
#: above, kept here as the semantic names the heartbeat prose and the presence
#: tests refer to; the class itself now lives in ``core/ratelimit.py`` so
#: PRD-007's share limiter shares one implementation (design Decision 6).


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
        An idle heartbeat returns ``False`` and must publish nothing.

        Two bounds, both of them about an identity nobody authenticated: the
        id itself is length-checked (:func:`check_identity`), and a **new** row is
        refused once the roster is full. Refused, not made room for: a flood
        of rotating ids is by construction the most recently seen half of the
        dict, so evicting the oldest would hand it every real client's seat.
        An incumbent keeps refreshing through the cap — its slot already
        exists — and the ceiling clears itself one TTL after the flood stops.
        """
        if surface is not None and surface not in SURFACES:
            raise ValidationError(
                f"unknown presence surface {surface!r}",
                {"known": list(SURFACES)},
            )
        client_id = check_identity(client_id)
        now = self._clock()
        with self._lock:
            changed = self._prune(now)
            slot = (key, client_id)
            previous = self._clients.get(slot)
            if previous is None and len(self._clients) >= MAX_CLIENTS:
                raise ValidationError(
                    f"this server has {MAX_CLIENTS} clients present across all "
                    "projects and branches, which is the maximum; an idle one "
                    f"drops out within {PRESENCE_TTL_S:.0f}s",
                    {"max": MAX_CLIENTS, "ttl_s": PRESENCE_TTL_S,
                     "scope": "server"},
                )
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

    That was not enough on its own: a *later* rebuild of the registry replaced
    the guard again and left the seam claim-free until the next heartbeat, a
    window in which one human's save lands over another's with no conflict and
    no dialog. ``install_write_guard`` therefore calls this function itself
    once it has replaced the guard — the same seam that removes the wrapper
    puts it back — but only when ``service.claims`` already exists, so a
    service that never had claims (``checks.py``'s ephemeral one, which
    PRD-004 pins as ending with ``write_guard is None``) is untouched. The
    lazy entry points stay, because they are what installs the guard the
    *first* time.

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

    An agent heartbeating with ``claim: true`` takes nothing: ``acquire``
    answers ``None`` for a non-human holder, because a claim an agent holds is
    a claim no human can conflict with.
    """
    claims = getattr(service, "claims", None)
    if claims is None:
        return False
    keep = part if (wanted and part) else None
    changed = False
    if keep:
        before = claims.get(key, keep)
        claim = claims.acquire(key, keep, who)
        if claim and claim["holder"] == who and (before is None
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
