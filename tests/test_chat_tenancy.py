"""The chat surface carries its turn's tenant across the executor (PRD-005 F1).

The built-in chat agent dispatches each tool call on the event loop's thread
pool (`agent/chat.py::_run_turn_locked`). `loop.run_in_executor` does **not**
propagate contextvars into the worker thread, so the turn's ambient tenant —
set by the request that started the turn and inherited through
`asyncio.create_task` — would read as `None` inside the tool call. That is a
tenancy hole with three faces at once: no role floor (a viewer drives any
mutating tool through chat), no audit row (the org is `None`, so the tap writes
nothing), and the wrong, flat storage root (the tenant-rooted project is
invisible).

The fix captures the tenant in the async caller and re-sets it inside
`_call_tool`, beside the client-id it already establishes. These tests drive
`_call_tool` through an executor exactly the way `_run_turn_locked` does — the
tenant captured while it is ambient, dispatched on a thread that never inherits
it, threaded across explicitly — and assert all three faces are covered.

Why not `copy_context().run`: the chat is deliberately attributed as the `chat`
principal, which `audit.current_principal_id` reads by finding **no** HTTP
principal in the worker thread. Copying the whole request context would carry
`security`'s principal across too, and the audit row would name the human who
typed the message. Threading only the tenant keeps the worker context clean.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentcad.agent.chat import ChatEngine
from agentcad.core import audit, tenancy

from .test_tenancy_integration import (  # noqa: F401 — `tenanted` is a fixture
    ACME, WS, make_project, tenanted,
)


@pytest.fixture(autouse=True)
def _restore_tenant():
    """Undo any tenant a test leaves set — a ContextVar with a module default
    outlives the test that set it (`test_tenancy_integration`'s reason)."""
    token = tenancy.tenant_var.set(None)
    try:
        yield
    finally:
        tenancy.tenant_var.reset(token)


def _engine(tenanted):
    return ChatEngine(tenanted.registry, tenanted.service.bus, api_key=None)


def _drive(engine, tenant, name, args, session="main"):
    """Run one `_call_tool` the way `_run_turn_locked` does.

    The tenant is captured *inside* the async caller (where the turn's context
    still holds it) and handed to `_call_tool` across `run_in_executor`, whose
    worker thread never inherits it. This is the real dispatch, not a hand-set
    ambient tenant — remove the propagation and the worker sees `None`.
    """
    async def go():
        with tenancy.tenant_scope(tenant):
            captured = tenancy.current_tenant()
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, engine._call_tool, name, args, session, captured)

    return asyncio.run(go())


def _log(tenanted):
    return audit.for_auth_store(tenanted.store)


# --------------------------------------------------- (a) the role floor


def test_a_viewer_is_refused_a_mutating_tool_through_chat(tenanted):
    """`chat` holding only `view` cannot drive a mutating tool — the floor is
    the registry wrapper's, and the chat surface reaches it now that the tenant
    crosses the executor. Without the fix the worker's tenant is `None`, the
    floor no-ops, and the viewer's edit lands."""
    anya = tenanted.as_("anya")
    assert make_project(anya, "widget").status_code == 201
    tenanted.orgs.grant_role(ACME, WS, "widget", "chat", "view")

    result = _drive(_engine(tenanted), (ACME, WS), "set_assembly",
                    {"project": "widget", "instances": []})

    assert result["error"]["type"] == "permission_error"
    details = result["error"]["details"]
    assert details["required"] == "edit"
    assert details["project"] == "widget"
    assert details["principal_role"] == "view"


def test_a_viewer_may_still_drive_a_read_tool_through_chat(tenanted):
    """The floor is a floor, not a wall: `view` is enough for a read."""
    anya = tenanted.as_("anya")
    assert make_project(anya, "widget").status_code == 201
    tenanted.orgs.grant_role(ACME, WS, "widget", "chat", "view")

    result = _drive(_engine(tenanted), (ACME, WS), "get_assembly",
                    {"project": "widget"})
    assert "error" not in result, result


# ----------------------------------------------- (b) the audit row


def test_a_chat_mutation_writes_one_audit_row_under_the_tenant(tenanted):
    """A mutating chat call lands exactly one row, in the org's log, named for
    the `chat` principal and classified as an agent — the general tap reached
    through the same executor boundary."""
    anya = tenanted.as_("anya")
    assert make_project(anya, "widget").status_code == 201
    tenanted.orgs.grant_role(ACME, WS, "widget", "chat", "edit")

    result = _drive(_engine(tenanted), (ACME, WS), "set_assembly",
                    {"project": "widget", "instances": []})
    assert "error" not in result, result

    rows = _log(tenanted).query(ACME, action="set_assembly")
    assert len(rows) == 1
    assert rows[0]["principal"] == "chat"
    assert rows[0]["kind"] == "agent"
    assert rows[0]["project"] == "widget"
    assert rows[0]["outcome"] == "ok"


def test_a_refused_chat_mutation_is_still_recorded(tenanted):
    """The tap is outermost, so a *refused* call is a row too — "who tried
    what" is exactly what an audit log is read for."""
    anya = tenanted.as_("anya")
    assert make_project(anya, "widget").status_code == 201
    tenanted.orgs.grant_role(ACME, WS, "widget", "chat", "view")

    result = _drive(_engine(tenanted), (ACME, WS), "set_assembly",
                    {"project": "widget", "instances": []})
    assert result["error"]["type"] == "permission_error"

    rows = _log(tenanted).query(ACME, action="set_assembly")
    assert [r["outcome"] for r in rows] == ["permission_error"]
    assert rows[0]["principal"] == "chat"


# --------------------------------------- (c) the storage root


def test_a_chat_mutation_reads_and_writes_the_tenants_storage_root(tenanted):
    """The write lands under the tenant's root, and nothing leaks to the flat
    one. The project exists only at `orgs/<org>/<ws>/`, so a chat mutation that
    succeeds on it proves the store rooted at the tenant inside the worker."""
    anya = tenanted.as_("anya")
    assert make_project(anya, "widget").status_code == 201
    tenanted.orgs.grant_role(ACME, WS, "widget", "chat", "edit")

    result = _drive(_engine(tenanted), (ACME, WS), "set_assembly",
                    {"project": "widget", "instances": []})
    assert "error" not in result, result

    root = Path(tenanted.projects)
    tenant_dir = root / tenancy.ORGS_DIRNAME / ACME / WS / "widget"
    assert tenant_dir.is_dir()
    assert (tenant_dir / "project.json").is_file()
    # No flat-root leakage: the un-tenanted path was never touched.
    assert not (root / "widget").exists()


def test_without_the_tenant_the_worker_roots_flat_and_misses_the_project(
        tenanted):
    """The negative control that makes the storage-root claim airtight: hand
    `_call_tool` no tenant (the pre-fix state, where the executor thread never
    saw one) and the store roots flat, so the tenant's `widget` is invisible."""
    anya = tenanted.as_("anya")
    assert make_project(anya, "widget").status_code == 201
    tenanted.orgs.grant_role(ACME, WS, "widget", "chat", "edit")

    seen = _drive(_engine(tenanted), (ACME, WS), "get_assembly",
                  {"project": "widget"})
    assert "error" not in seen, seen

    flat = _drive(_engine(tenanted), None, "get_assembly",
                  {"project": "widget"})
    assert flat["error"]["type"] == "notfound_error"
