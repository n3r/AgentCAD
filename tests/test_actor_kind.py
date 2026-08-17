"""PRD-005a slice 2 / FR10: ``actor_kind`` under composed hosted principals.

`user:nikita/browser:7f3a1b2c` does **not** start with `browser:`, so before
this change every signed-in human classified as an *agent* — and that is not
cosmetic. `ClaimRegistry.acquire` returns ``None`` for a non-human holder
(`core/locks.py`), and `_blocking` never blocks an agent, so PRD-008's entire
per-part claim protection would have switched off silently on the day hosting
turned on, with no error anywhere.

The classifier's return value is the cheap half of this file. The expensive
half is the bottom section, which drives the **real registry** and shows the
protection actually working — because "returns 'human'" is exactly the kind of
green that PRD-011's review found hiding a broken behaviour.
"""

from __future__ import annotations

import pytest

from agentcad.core import comments, presence
from agentcad.core.locks import ClaimRegistry
from agentcad.core.model import ConflictError
from agentcad.core.proposals import actor_kind


def test_local_classification_is_unchanged():
    assert actor_kind("browser") == "human"
    assert actor_kind("browser:7f3a1b2c") == "human"
    assert actor_kind("mcp") == "agent"
    assert actor_kind("chat:main") == "agent"
    assert actor_kind("local") == "agent"


def test_composed_principals_classify_correctly():
    assert actor_kind("user:nikita") == "human"
    assert actor_kind("user:nikita/browser:7f3a1b2c") == "human"
    assert actor_kind("agent:ci") == "agent"
    assert actor_kind("agent:mcp:claude") == "agent"


@pytest.mark.parametrize("identity", ["", None, "  ", "user", "users:nikita",
                                      "agentx:ci", "xuser:nikita"])
def test_nothing_near_the_new_prefixes_becomes_human_by_accident(identity):
    """The prefixes are exact. `users:` and `xuser:` are not `user:`, and the
    pre-existing else-branch still decides them."""
    assert actor_kind(identity) == "agent"


def test_a_browser_prefixed_device_under_no_user_is_still_human():
    """The `browser*` branch is byte-identical; only two prefixes were added
    in front of it."""
    assert actor_kind("browser:00000000") == "human"


# -------------------------------------------------- the behaviour, not the
# -------------------------------------------------- return value


def test_a_composed_human_can_actually_hold_a_claim():
    """AC10's core. Before the fix `acquire` returned None here — a hosted
    human could never hold a per-part claim at all."""
    claims = ClaimRegistry()
    taken = claims.acquire("demo", "box", "user:nikita/browser:7f3a1b2c")
    assert taken is not None
    assert taken["holder_kind"] == "human"
    assert taken["holder"] == "user:nikita/browser:7f3a1b2c"


def test_a_composed_human_actually_blocks_another_composed_human():
    """Before the fix `_blocking` never blocked an agent, so with both people
    classified `agent` nobody was protected from anybody — `check` returned
    quietly where it must raise."""
    claims = ClaimRegistry()
    claims.acquire("demo", "box", "user:alice/browser:aaaaaaaa")
    with pytest.raises(ConflictError) as exc:
        claims.check("demo", "box", "user:bob/browser:bbbbbbbb")
    assert exc.value.details["claim"]["holder"] == "user:alice/browser:aaaaaaaa"
    assert exc.value.details["overridable"] is True


def test_a_hosted_agent_neither_takes_a_claim_nor_is_blocked_by_one():
    """The other direction, which the fix must NOT break: the flagship loop is
    a human pinning a comment and an agent fixing it, so an agent blocked by a
    human's open editor would 409 on its very first write."""
    claims = ClaimRegistry()
    claims.acquire("demo", "box", "user:alice/browser:aaaaaaaa")
    assert claims.acquire("demo", "box", "agent:ci") is None
    assert claims.check("demo", "box", "agent:ci") is None


def test_the_four_consumers_import_the_rule_rather_than_re_implement_it():
    """One edit fixes all four *only* while nobody has copied the rule. If a
    consumer grows its own `startswith("browser:")`, this is what notices —
    the assertion is object identity, not equal behaviour."""
    from agentcad.core import locks

    assert comments.actor_kind is actor_kind
    assert presence.actor_kind is actor_kind
    # `locks` imports it lazily inside `_kind` (proposals imports locks, so a
    # module-level import would close a cycle) — so drive the wrapper.
    assert locks._kind("user:nikita") == "human"                 # noqa: SLF001
    assert locks._kind("agent:ci") == "agent"                    # noqa: SLF001
