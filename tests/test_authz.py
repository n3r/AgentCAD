"""PRD-005 slice 1: the RBAC ladder and the one refusal.

`role_of` (org admin > per-project override > org default > nothing), the
view < comment < edit < admin comparison, and `permission_error`'s wire
shape — the `type` string, the 403, and the three details FR6 promises.

No service and no app fixture: the two mechanisms that fix the wire contract
(`model.error_type`'s name derivation and `app._ERROR_STATUS`'s isinstance
walk) are exercised directly, which is exactly the point — the contract holds
because of those two, not because a route was wired.

`PermissionError` below is **ours**, imported from `core.authz`, and it
shadows the builtin inside this module deliberately — the two tests at the
foot of the file are what stop that from ever being an accident.
"""

from __future__ import annotations

import pytest

from agentcad.core import tenancy as tenancy_mod
from agentcad.core.authz import (
    ROLE_ORDER,
    PermissionDeniedError,
    PermissionError,
    at_least,
    can,
    rank,
    require,
    role_of,
)
from agentcad.core.model import AppError, AuthzError, error_type
from agentcad.core.tenancy import TenancyStore


@pytest.fixture
def store(tmp_path):
    """One org, one workspace, one project, and four people on the ladder."""
    store = TenancyStore(tmp_path / "auth")
    store.create_org("acme", admin="nikita")
    store.create_workspace("acme", "main")
    store.add_project("acme", "main", "widget")
    store.add_member("acme", "anya", "edit")
    store.add_member("acme", "sam", "view")
    store.add_member("acme", "kim", "comment")
    return store


# ------------------------------------------------------------- the ladder


def test_the_ladder_is_the_documented_one_and_the_store_agrees(store):
    assert ROLE_ORDER == ("view", "comment", "edit", "admin")
    assert tenancy_mod.ROLES == ROLE_ORDER
    assert tenancy_mod.ORG_ROLES == ROLE_ORDER


@pytest.mark.parametrize("role", ROLE_ORDER)
@pytest.mark.parametrize("floor", ROLE_ORDER)
def test_a_rung_includes_every_rung_below_it(role, floor):
    assert at_least(role, floor) is (ROLE_ORDER.index(role)
                                     >= ROLE_ORDER.index(floor))


@pytest.mark.parametrize("role", [None, "", "owner", "ADMIN", 5, ["admin"]])
def test_nothing_outside_the_ladder_is_ever_above_view(role):
    """`rank` answers -1 for an unreadable role, which is *below* the bottom
    rung and never above it. Failing closed is the property; the arithmetic
    is only how."""
    assert rank(role) == -1
    assert at_least(role, "view") is False


def test_an_unknown_floor_raises_rather_than_passing(store):
    """A typo'd floor that answered True would open the check it is spelled
    into, so it is a programming error and reads like one."""
    with pytest.raises(ValueError):
        at_least("admin", "write")
    with pytest.raises(ValueError):
        require(store, "write", "nikita", "acme", "main", "widget")


# --------------------------------------------------------- role precedence


@pytest.mark.parametrize(
    "principal,override,expected",
    [
        # 1. the org default, with no override in sight
        ("anya", None, "edit"),
        ("sam", None, "view"),
        ("kim", None, "comment"),
        ("stranger", None, None),
        ("agent:ci", None, None),          # a token is never an org member
        # 2. the override wins over the org default, in BOTH directions
        ("sam", "edit", "edit"),           # raised
        ("anya", "view", "view"),          # lowered
        ("kim", "admin", "admin"),
        ("stranger", "comment", "comment"),  # a non-member reached by a grant
        ("agent:ci", "edit", "edit"),      # the only way a token gets in
        # 3. org admin outranks any override, including one that lowers
        ("nikita", None, "admin"),
        ("nikita", "view", "admin"),
        ("nikita", "comment", "admin"),
    ],
)
def test_role_precedence(store, principal, override, expected):
    if override is not None:
        store.grant_role("acme", "main", "widget", principal, override)
    assert role_of(store, principal, "acme", "main", "widget") == expected


def test_an_org_admin_cannot_be_held_down_by_a_project_override(store):
    """Not a convenience: an org admin may rewrite the override in one call,
    so honouring a demotion would be a restriction that is not one — a fiction
    the members panel would render as a guarantee.
    """
    store.grant_role("acme", "main", "widget", "nikita", "view")
    assert role_of(store, "nikita", "acme", "main", "widget") == "admin"
    # And it really is one call, which is why the fiction would not survive.
    store.grant_role("acme", "main", "widget", "nikita", "admin")
    assert role_of(store, "nikita", "acme", "main", "widget") == "admin"


def test_a_device_suffix_is_the_same_person(store):
    store.grant_role("acme", "main", "widget", "sam", "edit")
    assert role_of(store, "user:sam/browser:7f3a1b2c",
                   "acme", "main", "widget") == "edit"


def test_an_override_does_not_reach_the_next_project(store):
    store.add_project("acme", "main", "gadget")
    store.grant_role("acme", "main", "widget", "sam", "admin")
    assert role_of(store, "sam", "acme", "main", "gadget") == "view"


def test_a_role_is_scoped_to_its_org_and_workspace(store):
    """FR5: no cross-tenant reach. The same project name in another org, or
    the same org's other workspace, is a different project."""
    store.create_org("beta", admin="anya")
    store.create_workspace("beta", "main")
    store.add_project("beta", "main", "widget")
    assert role_of(store, "nikita", "beta", "main", "widget") is None
    assert role_of(store, "anya", "beta", "main", "widget") == "admin"
    assert role_of(store, "anya", "acme", "main", "widget") == "edit"
    # An unknown workspace or project is no role, never an exception.
    assert role_of(store, "anya", "acme", "nope", "widget") == "edit"  # org default
    assert role_of(store, "sam", "acme", "nope", "widget") == "view"
    assert role_of(store, "agent:ci", "acme", "nope", "widget") is None


def test_the_org_level_question_ignores_project_overrides(store):
    """`proj=None` is what the org-admin tools (list_members, grant_role) ask,
    and a per-project grant must not make anybody an org admin."""
    store.grant_role("acme", "main", "widget", "sam", "admin")
    assert role_of(store, "sam", "acme", "main") == "view"
    assert role_of(store, "nikita", "acme", "main") == "admin"
    assert can(store, "admin", "sam", "acme", "main") is False
    assert can(store, "admin", "sam", "acme", "main", "widget") is True


def test_role_of_never_raises_whatever_it_is_handed(store):
    """It runs inside a request guard on every read; an exception there is a
    500 that says the store, or the header, was interesting."""
    for principal in ["", None, 5, "user:", "robot:x", "a" * 500, ["nikita"]]:
        assert role_of(store, principal, "acme", "main", "widget") is None
    assert role_of(store, "nikita", "nope", "nope", "nope") is None


def test_a_malformed_document_denies_rather_than_grants(store, tmp_path):
    """The store's readers are total over the value so that this is true:
    garbage in `orgs.json` is *no* role, never a higher one."""
    (store.root / tenancy_mod.ORGS).write_text(
        '{"orgs": {"acme": {"members": {"nikita": "root", "sam": true},'
        ' "workspaces": {"main": {"projects": {"widget":'
        ' {"roles": {"user:anya": "root"}}}}}}}}',
        encoding="utf-8")
    assert role_of(store, "nikita", "acme", "main", "widget") is None
    assert role_of(store, "sam", "acme", "main", "widget") is None
    assert role_of(store, "anya", "acme", "main", "widget") is None


# ------------------------------------------------------------ the refusal


def test_require_returns_the_effective_role_when_it_is_enough(store):
    assert require(store, "view", "anya", "acme", "main", "widget") == "edit"
    assert require(store, "edit", "anya", "acme", "main", "widget") == "edit"
    assert require(store, "admin", "nikita", "acme", "main", "widget") == "admin"


def test_require_refuses_a_rung_short_and_names_all_three_facts(store):
    with pytest.raises(PermissionError) as excinfo:
        require(store, "edit", "sam", "acme", "main", "widget")
    exc = excinfo.value
    assert exc.details == {"required": "edit", "project": "widget",
                           "principal_role": "view"}
    assert "'edit'" in exc.message and "'view'" in exc.message
    assert "widget" in exc.message


def test_a_principal_with_no_role_at_all_is_a_null_not_a_missing_key(store):
    with pytest.raises(PermissionError) as excinfo:
        require(store, "view", "stranger", "acme", "main", "widget")
    assert excinfo.value.details == {"required": "view", "project": "widget",
                                     "principal_role": None}


def test_the_refusal_does_not_say_whether_the_project_exists(store):
    """A cross-tenant probe must not be an existence oracle (FR5)."""
    store.create_org("beta", admin="anya")
    store.create_workspace("beta", "main")
    store.add_project("beta", "main", "widget")
    with pytest.raises(PermissionError) as real:
        require(store, "view", "sam", "beta", "main", "widget")
    with pytest.raises(PermissionError) as imaginary:
        require(store, "view", "sam", "beta", "main", "ghost")
    assert (real.value.message.replace("widget", "X")
            == imaginary.value.message.replace("ghost", "X"))


def test_an_unprintable_principal_does_not_travel_through_the_message(store):
    with pytest.raises(PermissionError) as excinfo:
        require(store, "view", "z" * 500, "acme", "main", "widget")
    assert len(excinfo.value.message) < 300


# ------------------------------------------- the wire contract (FR6)


def test_the_wire_type_is_permission_error_and_it_is_derived(store):
    """`model.error_type` is `type(exc).__name__.replace("Error", "").lower()
    + "_error"`, and `ToolRegistry.call` carries its own copy of that
    expression. The class is *named* `PermissionError` for exactly this
    reason — the name IS the wire string FR6 fixes."""
    exc = PermissionError("nope", {"required": "edit"})
    assert error_type(exc) == "permission_error"
    assert type(exc).__name__ == "PermissionError"


def test_the_http_status_is_403_and_it_is_inherited(store):
    """`app.py`'s `_ERROR_STATUS` walk is isinstance-based, so subclassing
    `AuthzError` (already 403) buys the status with no core edit. Asserted
    through the real response builder, not a copy of its table."""
    from agentcad.server.app import _error_response

    exc = PermissionError("nope", {"required": "edit", "project": "widget",
                                   "principal_role": "view"})
    assert isinstance(exc, AuthzError) and isinstance(exc, AppError)
    response = _error_response(exc)
    assert response.status_code == 403
    import json as _json
    body = _json.loads(response.body)
    assert body["error"]["type"] == "PermissionError"      # HTTP spells classes
    assert body["error"]["details"]["required"] == "edit"


def test_a_tool_refusal_is_a_permission_error_payload(store):
    """The tool surface's half: a 200 carrying `{"error": {...}}`, which is
    the shape PRD-005a fixed for a refusal and the one the registry wrapper
    (slice 4) will produce."""
    from agentcad.core.tools import Tool, ToolRegistry

    registry = ToolRegistry()

    def handler(project: str) -> dict:
        require(store, "edit", "sam", "acme", "main", project)
        return {"ok": True}

    registry.register(Tool(
        name="set_params", description="", handler=handler,
        input_schema={"type": "object",
                      "properties": {"project": {"type": "string"}},
                      "required": ["project"]}))
    payload = registry.call("set_params", {"project": "widget"})
    assert payload["error"]["type"] == "permission_error"
    assert payload["error"]["details"] == {
        "required": "edit", "project": "widget", "principal_role": "view"}


def test_the_alias_is_the_class_so_importers_need_not_shadow_the_builtin():
    """`core/model.py` records why `AuthzError` is not called
    `PermissionError`: the builtin is a real exception this codebase catches
    around filesystem work. The wire string forces the class name here, so the
    alias is what other modules import."""
    assert PermissionDeniedError is PermissionError
    assert PermissionDeniedError.__name__ == "PermissionError"
    assert error_type(PermissionDeniedError("nope")) == "permission_error"


def test_the_builtin_permission_error_is_not_this_one():
    """Spelled with an explicit import so the check cannot be fooled by the
    module's own namespace: an OSError-family failure must never be mistaken
    for an authorization refusal, in either direction."""
    import builtins

    assert PermissionError is not builtins.PermissionError
    assert not issubclass(PermissionError, OSError)
    assert not issubclass(builtins.PermissionError, AppError)
