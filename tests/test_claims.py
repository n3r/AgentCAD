"""PRD-008 slice 7: per-part soft claims at the ``write_guard`` seam.

The riskiest slice in the plan: it inserts a wrapper into the one seam every
persistent write passes through. Four things are therefore asserted here more
loudly than anywhere else in the feature.

**The precedence order** (design Decision 14) is turn → own-turn bypass →
claim → proceed. The turn lock decides first with its existing code path,
message and details; AC6's regression gate is that ``tests/test_locks.py``
passes *unmodified*, and it does.

**Claims are human-vs-human only.** If an agent's write were blocked by a
human's open editor, the flagship loop — human pins a comment on a face, agent
fixes it and replies — would 409 on the agent's very first write. Two tests
pin both directions.

**Coverage is bounded and honest.** Only ``write_script`` and
``update_part_entry`` carry a ``locks.write_scope``, so only the script and the
params/material/label paths are claim-covered. ``add_part``, ``remove_part``,
assembly edits, project materials, restore and undo are whole-manifest or
project-wide writes and are turn-locked *only* — pretending otherwise would be
a lie told by a green test, so a test says so out loud instead.

**The guard installs lazily** (risk R7), because tool packs load alphabetically
and ``tools_versioning`` (``v``) *replaces* ``write_guard`` after anything at
``c`` could have wrapped it. So the wrapper is (re)installed from
``routes_presence.build_router`` and from every claims entry point, and
``checks.py``'s ephemeral service — which nulls the guard on purpose — must
still end with no guard at all.
"""

from __future__ import annotations

import queue

import pytest
from fastapi.testclient import TestClient

from agentcad.core import locks
from agentcad.core.locks import CLAIM_TTL_S, ClaimRegistry
from agentcad.core.model import ConflictError, ValidationError
from agentcad.core.presence import ensure_claim_guard
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service

ALICE = {"X-Agent-Id": "browser:aaaaaaaa"}
BOB = {"X-Agent-Id": "browser:bbbbbbbb"}
BOT = {"X-Agent-Id": "bot"}


@pytest.fixture(autouse=True)
def _reset_identity():
    token = locks.client_id_var.set("browser")
    yield
    locks.client_id_var.reset(token)


@pytest.fixture
def http(kernel, tmp_path):
    """A two-part project behind a real app (the route pack is what installs
    the claim guard, so the app must be built)."""
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/api/projects", json={"name": "demo"}).status_code == 201
    for part in ("box", "lid"):
        assert client.post("/api/projects/demo/parts",
                           json={"id": part, "script": BOX_SCRIPT}
                           ).status_code == 201
    return service, registry, client


def _drain(subscription) -> list[dict]:
    events = []
    while True:
        try:
            events.append(subscription.get_nowait())
        except queue.Empty:
            return events


def _claim(client, part: str, who: dict) -> dict:
    """Take a claim the way the UI does: a heartbeat with a dirty buffer."""
    response = client.post("/api/projects/demo/presence",
                           json={"part_id": part, "surface": "editor",
                                 "claim": True}, headers=who)
    assert response.status_code == 200, response.text
    return response.json()


def _write(client, part: str, who: dict, text: str = BOX_SCRIPT):
    return client.put(f"/api/projects/demo/parts/{part}",
                      json={"script": text}, headers=who)


# -------------------------------------------------------- 1. the registry


def test_a_claim_is_taken_refreshed_released_and_never_stolen_by_accident():
    claims = ClaimRegistry()

    taken = claims.acquire("demo", "box", "browser:a")
    assert taken["holder"] == "browser:a"
    assert taken["holder_kind"] == "human"
    assert taken["expires_at"] > 0

    # Another client's acquire returns the standing claim UNCHANGED: refusing
    # a write is check()'s job, and acquire never raises.
    assert claims.acquire("demo", "box", "browser:b")["holder"] == "browser:a"
    assert claims.acquire("demo", "box", "browser:b",
                          force=True)["holder"] == "browser:b"

    assert claims.release("demo", "box", "browser:a") == {"released": False}
    assert claims.release("demo", "box", "browser:b") == {"released": True}
    assert claims.get("demo", "box") is None
    assert claims.all("demo") == {}


def test_claims_expire_and_are_pruned_lazily(monkeypatch):
    claims = ClaimRegistry()
    claims.acquire("demo", "box", "browser:a")

    import agentcad.core.locks as locks_mod

    real = locks_mod.time.time
    monkeypatch.setattr(locks_mod.time, "time",
                        lambda: real() + CLAIM_TTL_S + 1)
    assert claims.get("demo", "box") is None
    assert claims.all("demo") == {}
    # …and an expired claim conflicts with nobody.
    claims.check("demo", "box", "browser:b")


def test_check_is_human_vs_human_and_names_the_holder():
    claims = ClaimRegistry()
    claims.acquire("demo", "box", "browser:a")

    claims.check("demo", "box", "browser:a")     # our own
    claims.check("demo", "lid", "browser:b")     # a different part
    claims.check("demo", None, "browser:b")      # a whole-manifest write
    claims.check("demo", "box", "chat:main")     # an agent: never blocked

    with pytest.raises(Exception) as excinfo:
        claims.check("demo", "box", "browser:b")
    details = excinfo.value.details
    assert details["claim"]["holder"] == "browser:a"
    assert details["overridable"] is True
    assert "browser:a" in str(excinfo.value)

    # …and there is no such thing as an agent's claim to be blocked BY: the
    # exemption runs one way only (see the test below for why).
    agent_claims = ClaimRegistry()
    assert agent_claims.acquire("demo", "box", "chat:main") is None
    assert agent_claims.get("demo", "box") is None
    agent_claims.check("demo", "box", "browser:b")


def test_an_agent_never_holds_a_claim_so_two_humans_still_conflict():
    """The hole that disabled FR11.

    ``claim_write`` used to acquire for ANY caller. Once an agent held a part,
    ``check`` returned early for *everyone* — the exemption was written
    "either side is an agent" — so a human could no longer take that claim and
    a second human wrote straight over her with no conflict, no dialog and no
    chip, for as long as the agent kept writing. The rule is asymmetric in one
    direction only: an agent is never blocked by a human, and an agent never
    holds a claim that could suppress the human-vs-human check.
    """
    claims = ClaimRegistry()

    # The agent's script write goes through and leaves the part unclaimed.
    assert claims.claim_write("demo", "box", "chat:main") is None
    assert claims.get("demo", "box") is None

    # Human A's heartbeat now actually gets the claim…
    assert claims.acquire("demo", "box", "browser:a")["holder"] == "browser:a"
    # …and human B is refused, with the payload the conflict dialog renders.
    with pytest.raises(Exception) as excinfo:
        claims.claim_write("demo", "box", "browser:b")
    details = excinfo.value.details
    assert details["claim"]["holder"] == "browser:a"
    assert details["overridable"] is True
    assert claims.get("demo", "box")["holder"] == "browser:a"

    # The agent still writes straight through A's claim, and does not evict her.
    assert claims.claim_write("demo", "box", "chat:main") is None
    assert claims.get("demo", "box")["holder"] == "browser:a"


def test_an_armed_override_never_force_steals_a_claim_nobody_defended():
    """An override authorizes *one* write, and only as far as the conflict it
    was shown for goes. A write that would have succeeded anyway must not be
    turned into a forced steal — ``overridden`` stays False and ``acquire``
    is not called with ``force``."""
    claims = ClaimRegistry()

    claims.arm_override("demo", "lid", "browser:b")
    fresh = claims.claim_write("demo", "lid", "browser:b")
    assert fresh["overridden"] is False
    assert claims.get("demo", "lid")["holder"] == "browser:b"

    # An agent's write is exempt without an override too, and takes nothing.
    other = ClaimRegistry()
    other.acquire("demo", "box", "browser:a")
    other.arm_override("demo", "box", "chat:main")
    assert other.claim_write("demo", "box", "chat:main") is None
    assert other.get("demo", "box")["holder"] == "browser:a"


def test_an_armed_override_does_not_survive_to_steal_a_later_claim():
    """K8 — the other side of the same coin, and the one that bites.

    An arming that is only *used* on a conflict was also only *consumed* on a
    conflict, so a retry that landed on a part the holder had already let go
    left it armed. The report's sequence: A holds ``box``, B's dialog arms an
    override, A releases, B's retry succeeds without needing it — and then A
    takes the part back inside the 30-second window, at which point B's next
    ordinary write silently spends the old authorization and steals a claim
    nobody confirmed taking. The arming is now spent by the first write it
    authorizes, whether or not that write turned out to need it.
    """
    from agentcad.core.model import ConflictError

    claims = ClaimRegistry()
    claims.acquire("demo", "box", "browser:a")
    claims.arm_override("demo", "box", "browser:b")     # the conflict dialog
    claims.release("demo", "box", "browser:a")          # A lets go first

    retry = claims.claim_write("demo", "box", "browser:b")
    assert retry["overridden"] is False                 # nothing to override
    assert claims.get("demo", "box")["holder"] == "browser:b"
    assert claims._armed.get(("demo", "box", "browser:b")) is None

    # A takes the part back inside the old override's window.
    claims.release("demo", "box", "browser:b")
    claims.acquire("demo", "box", "browser:a")
    with pytest.raises(ConflictError):
        claims.claim_write("demo", "box", "browser:b")
    assert claims.get("demo", "box")["holder"] == "browser:a"

    # A second steal needs a second confirmation, and then it works.
    claims.arm_override("demo", "box", "browser:b")
    assert claims.claim_write("demo", "box", "browser:b")["overridden"] is True
    assert claims.get("demo", "box")["holder"] == "browser:b"


def test_an_override_is_single_use_and_so_is_the_contextvar():
    claims = ClaimRegistry()
    claims.acquire("demo", "box", "browser:a")

    claims.arm_override("demo", "box", "browser:b")
    claims.check("demo", "box", "browser:b")            # spends it
    with pytest.raises(Exception):
        claims.check("demo", "box", "browser:b")        # and it is spent

    with locks.claim_override():
        claims.check("demo", "box", "browser:b")
    with pytest.raises(Exception):
        claims.check("demo", "box", "browser:b")


def test_claim_write_leaves_a_humans_claim_alone_for_an_agent():
    """The acquisition policy: a write takes a free part, refreshes its own,
    steals under an override, and touches nothing when it got through only
    because one of the two parties is an agent."""
    claims = ClaimRegistry()
    claims.acquire("demo", "box", "browser:a")

    assert claims.claim_write("demo", "box", "chat:main") is None
    assert claims.get("demo", "box")["holder"] == "browser:a"

    assert claims.claim_write("demo", None, "browser:b") is None

    fresh = claims.claim_write("demo", "lid", "browser:b")
    assert fresh["changed"] is True and fresh["claim"]["holder"] == "browser:b"
    assert claims.claim_write("demo", "lid", "browser:b")["changed"] is False

    claims.arm_override("demo", "box", "browser:b")
    stolen = claims.claim_write("demo", "box", "browser:b")
    assert stolen["overridden"] is True
    assert claims.get("demo", "box")["holder"] == "browser:b"


def test_write_scope_carries_the_part_and_always_unwinds():
    assert locks.current_write_part() is None
    with locks.write_scope("box"):
        assert locks.current_write_part() == "box"
        with locks.write_scope("lid"):
            assert locks.current_write_part() == "lid"
        assert locks.current_write_part() == "box"
    assert locks.current_write_part() is None

    with pytest.raises(RuntimeError):
        with locks.write_scope("box"):
            raise RuntimeError("boom")
    assert locks.current_write_part() is None


# ------------------------------------------------------------- 2. AC5, HTTP


def test_ac5_conflict_override_and_an_untouched_other_part(http):
    service, _registry, client = http
    subscription = service.bus.subscribe()
    _claim(client, "box", ALICE)

    refused = _write(client, "box", BOB)
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["details"]["claim"]["holder"] == "browser:aaaaaaaa"
    assert error["details"]["claim"]["part"] == "box"
    assert error["details"]["overridable"] is True
    assert "browser:aaaaaaaa" in error["message"]

    # Bob's OTHER part is untouched throughout — this is a part claim.
    assert _write(client, "lid", BOB).status_code == 200

    armed = client.post("/api/projects/demo/claims/override",
                        json={"part": "box"}, headers=BOB)
    assert armed.status_code == 200, armed.text
    assert armed.json()["armed_until"] > 0
    assert armed.json()["claim"]["holder"] == "browser:aaaaaaaa"

    assert _write(client, "box", BOB).status_code == 200
    overridden = [e for e in _drain(subscription)
                  if e["type"] == "claim_changed"
                  and e.get("overridden_by") == "browser:bbbbbbbb"]
    assert overridden, "an override must be announced — it is the audit trail"
    assert overridden[-1]["part"] == "box"

    # The claim moved with the write; the override is spent, so Alice now has
    # to arm her own to take it back.
    assert _write(client, "box", ALICE).status_code == 409
    service.bus.unsubscribe(subscription)


def test_the_params_path_is_claim_covered_too(http):
    _service, _registry, client = http
    _claim(client, "box", ALICE)

    refused = client.patch("/api/projects/demo/parts/box/params",
                           json={"size": 12.0}, headers=BOB)
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["claim"]["part"] == "box"

    ok = client.patch("/api/projects/demo/parts/box/params",
                      json={"size": 12.0}, headers=ALICE)
    assert ok.status_code == 200


def test_an_agent_writes_straight_through_a_humans_claim(http):
    """The flagship loop: a human's open editor must never 409 the agent that
    was asked to fix the thing the human commented on."""
    _service, _registry, client = http
    _claim(client, "box", ALICE)

    assert _write(client, "box", BOT).status_code == 200
    # …and the agent did not evict the human who is still typing.
    roster = client.get("/api/projects/demo/presence", headers=ALICE).json()
    assert roster["claims"]["box"]["holder"] == "browser:aaaaaaaa"


def test_an_agents_write_does_not_disable_the_conflict_between_two_humans(http):
    """FR11 end to end, in the order that broke it: the agent writes *first*.

    The agent used to take the claim on `box`, which made every subsequent
    ``check`` return early — so Alice's heartbeat could not take the part and
    Bob overwrote her silently. Now the agent leaves no claim behind and the
    dialog Alice's conflict is supposed to raise still appears.
    """
    _service, _registry, client = http

    assert _write(client, "box", BOT).status_code == 200
    assert client.get("/api/projects/demo/presence",
                      headers=ALICE).json()["claims"] == {}

    payload = _claim(client, "box", ALICE)
    assert payload["claims"]["box"]["holder"] == "browser:aaaaaaaa"

    refused = _write(client, "box", BOB)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["claim"]["holder"] == (
        "browser:aaaaaaaa")
    assert refused.json()["error"]["details"]["overridable"] is True

    # A further agent write refreshes nothing and steals nothing.
    assert _write(client, "box", BOT).status_code == 200
    roster = client.get("/api/projects/demo/presence", headers=ALICE).json()
    assert roster["claims"]["box"]["holder"] == "browser:aaaaaaaa"


def test_the_turn_holder_is_never_claim_checked(http):
    """FR12. Bob takes the turn; Alice's claim on box does not stop him."""
    _service, _registry, client = http
    _claim(client, "box", ALICE)

    acquired = client.post("/api/tools/acquire_turn",
                           json={"project": "demo"}, headers=BOB).json()
    assert acquired["holder"] == "browser:bbbbbbbb"

    assert _write(client, "box", BOB).status_code == 200
    # Alice is now stopped by the TURN, with the turn's own message — not by a
    # claim, and with no override offered.
    refused = _write(client, "box", ALICE)
    assert refused.status_code == 409
    assert "locked by" in refused.json()["error"]["message"]
    assert "claim" not in refused.json()["error"]["details"]


def test_whole_manifest_writes_are_turn_locked_only(http):
    """Honest, bounded coverage: a claim is a *part* claim."""
    _service, _registry, client = http
    _claim(client, "box", ALICE)

    assert client.post("/api/projects/demo/parts",
                       json={"id": "shim", "script": BOX_SCRIPT},
                       headers=BOB).status_code == 201
    assert client.put("/api/projects/demo/assembly",
                      json={"instances": [{"id": "box_1", "part": "box"}]},
                      headers=BOB).status_code == 200
    assert client.delete("/api/projects/demo/parts/shim",
                         headers=BOB).status_code == 200


# ---------------------------------------------------- 3. claims and presence


def test_a_heartbeat_claims_releases_and_publishes(http):
    service, _registry, client = http
    subscription = service.bus.subscribe()

    payload = _claim(client, "box", ALICE)
    assert payload["claims"]["box"]["holder"] == "browser:aaaaaaaa"
    taken = [e for e in _drain(subscription) if e["type"] == "claim_changed"]
    assert taken and taken[-1]["part"] == "box"
    assert taken[-1]["holder"] == "browser:aaaaaaaa"

    # Moving to another part moves the claim: one client edits one thing.
    moved = _claim(client, "lid", ALICE)
    assert set(moved["claims"]) == {"lid"}

    # Viewing never claims: a heartbeat without `claim` drops ours.
    idle = client.post("/api/projects/demo/presence",
                       json={"part_id": "lid", "surface": "viewport"},
                       headers=ALICE).json()
    assert idle["claims"] == {}
    released = [e for e in _drain(subscription)
                if e["type"] == "claim_changed" and e["holder"] is None]
    assert released, "a released claim must be announced too"

    service.bus.unsubscribe(subscription)


def test_leaving_drops_the_roster_row_and_leaves_the_claims_to_the_ttl(http):
    """A leave is a **beacon**, and a beacon names its own identity in the
    body because ``sendBeacon`` cannot set headers — so anybody can send one
    for anybody. Dropping a roster row on that basis is harmless (the next
    heartbeat puts it back). Releasing that identity's *claims* was not: one
    forged beacon switched off the protection a human was relying on while
    still typing. Claims now expire on their own 90-second TTL, which is the
    behaviour a soft claim is designed around.
    """
    _service, _registry, client = http
    _claim(client, "box", ALICE)
    _claim(client, "lid", BOB)

    forged = client.post(
        "/api/projects/demo/presence",
        json={"leave": True, "client_id": "browser:aaaaaaaa"}, headers=BOB)
    assert forged.status_code == 200

    roster = client.get("/api/projects/demo/presence", headers=BOB).json()
    assert set(roster["claims"]) == {"box", "lid"}
    assert roster["claims"]["box"]["holder"] == "browser:aaaaaaaa"
    assert "browser:aaaaaaaa" not in {c["id"] for c in roster["clients"]}
    # …and Bob still cannot write to the part Alice is holding.
    assert _write(client, "box", BOB).status_code == 409

    # Alice's own leave is the same: the roster row goes, the claim waits out
    # its TTL like every other claim nobody is refreshing.
    client.post("/api/projects/demo/presence", json={"leave": True},
                headers=ALICE)
    assert set(client.get("/api/projects/demo/presence",
                          headers=BOB).json()["claims"]) == {"box", "lid"}


def test_a_claim_on_an_unknown_project_is_404_and_a_bad_part_is_422(http):
    _service, _registry, client = http

    assert client.post("/api/projects/nope/claims/override",
                       json={"part": "box"}, headers=BOB).status_code == 404
    assert client.post("/api/projects/demo/claims/override",
                       json={}, headers=BOB).status_code == 422


# ------------------------------------------------------- 4. R7, installation


def test_the_guard_survives_a_full_build_registry(http):
    """R7, and K9's window.

    ``tools_versioning`` REPLACES ``write_guard``, so a rebuild used to leave
    the seam claim-free until the *next* heartbeat or override request put the
    wrapper back — and this test used to insert exactly such a heartbeat
    before asking about the conflict, which is how it stayed green over a real
    hole. It now asks the question with **no intervening claims entry point**:
    the rebuild itself re-installs the wrapper, because it is the same seam
    that removed it.
    """
    service, _registry, client = http
    _claim(client, "box", ALICE)
    assert getattr(service.store.write_guard, "_claims_installed", False)

    build_registry(service)  # the versioning pack installs its own guard
    assert getattr(service.store.write_guard, "_claims_installed", False)
    # Straight to a conflicting write — no heartbeat, no override request.
    assert _write(client, "box", BOB).status_code == 409

    # And the turn check is still underneath it (the previous guard runs
    # FIRST), which is what the lazy re-install has always had to preserve.
    _claim(client, "box", ALICE)
    assert getattr(service.store.write_guard, "_claims_installed", False)
    assert _write(client, "box", BOB).status_code == 409


def test_installation_is_idempotent_and_the_kill_switch_works(http):
    service, _registry, client = http
    ensure_claim_guard(service)
    guard = service.store.write_guard
    ensure_claim_guard(service)
    assert service.store.write_guard is guard  # no wrapper on a wrapper

    _claim(client, "box", ALICE)
    assert _write(client, "box", BOB).status_code == 409
    # The documented rollback (the plan's landing notes): the wrapper reads the
    # registry on every call, so nulling it makes the guard a passthrough to
    # whatever it wrapped — the turn lock still works, claims stop existing.
    service.claims = None
    assert _write(client, "box", BOB).status_code == 200


def test_an_ephemeral_check_service_still_ends_with_no_guard(kernel, tmp_path):
    """R7's other half, mirroring ``tests/test_checks_ref.py``: nothing here
    may reinstall a guard behind ``checks.py``'s back, because a check runs on
    a linked worktree of the user's real repository."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.bus.on_publish = None
    build_registry(service)
    service.store.branch_resolver = None
    service.store.write_guard = None

    assert service.store.write_guard is None
    assert getattr(service, "claims", None) is None


def test_check_spends_an_arming_only_when_it_is_what_lets_the_call_through():
    """An arming is single-use, so who spends it is a decision, not a detail.

    ``check`` consumed it *first* and then looked at whether anything blocked,
    which throws away the confirmation the dialog collected for the write that
    is actually refused — the caller then gets a conflict it had already
    answered. (``claim_write`` spends it unconditionally on purpose and for the
    opposite reason: every write passes through that one, so an arming left
    behind is a steal waiting for a claim to appear.) Unreachable today, since
    ``claim_write`` is the only caller that both consumes and conflicts, and a
    trap for the next one either way.
    """
    claims = ClaimRegistry()
    claims.acquire("demo", "box", "browser:a")

    # Nothing blocks (our own part): the arming is untouched.
    claims.arm_override("demo", "lid", "browser:b")
    claims.check("demo", "lid", "browser:b")
    assert ("demo", "lid", "browser:b") in claims._armed      # noqa: SLF001

    # Blocked, but an explicit override already lets it through: untouched too.
    claims.arm_override("demo", "box", "browser:b")
    claims.check("demo", "box", "browser:b", override=True)
    assert ("demo", "box", "browser:b") in claims._armed      # noqa: SLF001

    # Blocked with nothing else to appeal to: NOW it is spent, once.
    claims.check("demo", "box", "browser:b")
    assert ("demo", "box", "browser:b") not in claims._armed  # noqa: SLF001
    with pytest.raises(ConflictError):
        claims.check("demo", "box", "browser:b")


def test_an_over_long_identity_cannot_take_a_claim_on_a_part_write(http):
    """The identity bound was only ever on the *presence* routes.

    ``presence.py``'s rule 5 says an id is bounded because it becomes a key in
    the roster, the claim registry and the rate limiter — and the claim
    registry was not on that path. A part write carries the same header and
    reaches ``claim_write`` straight from the write guard, so a 4 008-character
    ``X-Agent-Id`` took a claim whose holder was 4 008 characters, and the
    roster payload and every ``claim_changed`` frame then carried it.
    """
    service, _registry, client = http
    huge = {"X-Agent-Id": "browser:" + "z" * 4000}

    refused = _write(client, "box", huge)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["max"] == locks.MAX_CLIENT_ID_CHARS

    # Nothing oversized reached the registry, and so nothing can reach a
    # broadcast: the claim map IS what presence and claim_changed carry.
    assert service.claims.all(service.store.lock_key("demo")) == {}
    payload = client.post("/api/projects/demo/presence", json={},
                          headers=ALICE).json()
    assert all(len(holder) <= locks.MAX_CLIENT_ID_CHARS
               for holder in payload["claims"])
    assert all(len(claim["holder"]) <= locks.MAX_CLIENT_ID_CHARS
               for claim in payload["claims"].values())

    # An ordinary write by an ordinary identity is untouched.
    assert _write(client, "box", ALICE).status_code == 200


def test_the_registry_refuses_an_over_long_identity_rather_than_cutting_it():
    """Refused, not truncated, for the reason presence gives: two identities
    cut to the same 64 characters would be ONE client to the claim map."""
    claims = ClaimRegistry()
    huge = "browser:" + "z" * 4000

    with pytest.raises(ValidationError) as excinfo:
        claims.claim_write("demo", "box", huge)
    assert excinfo.value.details["given"] == len(huge)
    with pytest.raises(ValidationError):
        claims.check("demo", "box", huge)
    with pytest.raises(ValidationError):
        claims.arm_override("demo", "box", huge)
    assert claims.all("demo") == {}
    assert claims._armed == {}                             # noqa: SLF001


def test_a_library_caller_overrides_with_the_context_manager(http):
    """The tool/library entry point to the same override the browser arms."""
    service, _registry, client = http
    _claim(client, "box", ALICE)
    token = locks.client_id_var.set("browser:bbbbbbbb")
    try:
        with pytest.raises(Exception):
            service.store.write_script("demo", "box", BOX_SCRIPT)
        with locks.claim_override():
            service.store.write_script("demo", "box", BOX_SCRIPT)
    finally:
        locks.client_id_var.reset(token)
    assert service.claims.get(service.store.lock_key("demo"),
                              "box")["holder"] == "browser:bbbbbbbb"
