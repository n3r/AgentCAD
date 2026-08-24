"""PRD-005 slice 1: the tenancy document and the ambient tenant.

``<state>/auth/orgs.json`` — orgs, workspaces, projects, memberships and
per-project role overrides — plus the ContextVar the rest of the feature
composes paths and lock keys from. No service, no server, no geometry.

Every property is tested by its negation as well as its statement, the
`test_authstore` discipline: a malformed row *granting* a role, a second
writer's members vanishing, a tenant leaking into a thread that never set
one, ``qualified`` colliding two orgs' identically named projects.
"""

from __future__ import annotations

import contextvars
import json
import subprocess
import sys
import threading

import pytest

from agentcad.core import authstore as authstore_mod
from agentcad.core import tenancy as tenancy_mod
from agentcad.core.authstore import AuthStore
from agentcad.core.model import ConflictError, NotFoundError, ValidationError
from agentcad.core.tenancy import (
    ORGS,
    TenancyStore,
    current_tenant,
    handle_of,
    principal_key,
    qualified,
    reset_tenant,
    set_tenant,
    tenant_root,
    tenant_scope,
)

HAS_FLOCK = tenancy_mod.fcntl is not None


@pytest.fixture
def store(tmp_path):
    store = TenancyStore(tmp_path / "auth")
    store.create_org("acme", label="Acme Robotics", admin="nikita")
    store.create_workspace("acme", "main")
    store.add_project("acme", "main", "widget")
    return store


# --------------------------------------------------------------- org CRUD


def test_an_org_is_created_with_its_first_admin_in_one_write(tmp_path):
    """The bootstrap is atomic: an org created empty and then given a member
    is, in between, an org nobody can administer."""
    store = TenancyStore(tmp_path / "auth")
    store.create_org("acme", label="Acme Robotics", admin="nikita")
    assert store.org_role("acme", "nikita") == "admin"
    assert store.list_orgs() == [
        {"id": "acme", "label": "Acme Robotics", "members": 1, "workspaces": 0}]


def test_an_org_without_a_label_renders_as_its_id(tmp_path):
    store = TenancyStore(tmp_path / "auth")
    store.create_org("acme")
    assert store.list_orgs()[0]["label"] == "acme"


def test_creating_an_org_twice_conflicts_rather_than_wiping_it(store):
    """Recreating would silently drop every membership and role it holds."""
    with pytest.raises(ConflictError):
        store.create_org("acme")
    assert store.org_role("acme", "nikita") == "admin"


def test_an_agent_may_not_be_an_org_admin_or_a_member(store):
    with pytest.raises(ValidationError):
        store.create_org("other", admin="agent:ci")
    with pytest.raises(ValidationError):
        store.add_member("acme", "agent:ci", "edit")


def test_members_default_to_the_weakest_rung(store):
    store.add_member("acme", "anya")
    assert store.org_role("acme", "anya") == "view"


def test_adding_a_member_twice_conflicts(store):
    store.add_member("acme", "anya", "edit")
    with pytest.raises(ConflictError):
        store.add_member("acme", "anya", "view")
    assert store.org_role("acme", "anya") == "edit"


def test_setting_a_role_for_a_non_member_is_a_404_not_a_silent_create(store):
    """A typo'd handle would otherwise become a role held by nobody."""
    with pytest.raises(NotFoundError):
        store.set_org_role("acme", "anyah", "edit")
    assert store.list_members("acme") == [{"handle": "nikita", "role": "admin"}]


def test_a_member_can_be_removed_and_their_overrides_survive(store):
    store.add_member("acme", "anya", "edit")
    store.grant_role("acme", "main", "widget", "anya", "admin")
    store.remove_member("acme", "anya")
    assert store.org_role("acme", "anya") is None
    # Documented, and the reason re-adding restores what an operator who
    # removed the wrong person expects back.
    assert store.project_roles("acme", "main", "widget") == {"user:anya": "admin"}
    with pytest.raises(NotFoundError):
        store.remove_member("acme", "anya")


def test_org_membership_is_a_persons_property_so_an_agent_lists_no_orgs(store):
    store.add_member("acme", "anya", "view")
    assert store.orgs_for("anya") == ["acme"]
    assert store.orgs_for("user:anya/browser:7f3a1b2c") == ["acme"]
    assert store.orgs_for("agent:ci") == []
    assert store.orgs_for("stranger") == []


# --------------------------------------------- workspaces and projects


def test_workspaces_and_projects_round_trip(store):
    store.create_workspace("acme", "mech", label="Mechanical")
    store.add_project("acme", "mech", "bracket")
    assert store.list_workspaces("acme") == [
        {"id": "main", "label": "main", "projects": 1},
        {"id": "mech", "label": "Mechanical", "projects": 1},
    ]
    assert store.list_projects("acme", "mech") == ["bracket"]
    assert store.has_project("acme", "mech", "bracket")
    assert not store.has_project("acme", "main", "bracket")


def test_a_project_name_is_unique_within_its_workspace_and_free_across_them(store):
    """FR5's uniqueness rule, and its other half: two workspaces (or two orgs)
    may both hold a `widget`, which is the collision `qualified` exists for."""
    with pytest.raises(ConflictError):
        store.add_project("acme", "main", "widget")
    store.create_workspace("acme", "mech")
    store.add_project("acme", "mech", "widget")
    assert store.list_projects("acme", "mech") == ["widget"]


def test_writes_against_a_missing_tenant_are_404s(store):
    with pytest.raises(NotFoundError):
        store.add_member("nope", "anya")
    with pytest.raises(NotFoundError):
        store.create_workspace("nope", "main")
    with pytest.raises(NotFoundError):
        store.add_project("acme", "nope", "widget")
    with pytest.raises(NotFoundError):
        store.grant_role("acme", "main", "nope", "anya", "edit")
    with pytest.raises(NotFoundError):
        store.list_projects("acme", "nope")


def test_forgetting_a_workspace_or_project_deletes_no_geometry(store, tmp_path):
    """Nothing in this module touches `<projects_dir>`; the document is
    membership bookkeeping. A store that deleted a customer's parts as a side
    effect of a role edit would be the worst bug this feature could ship."""
    projects = tmp_path / "projects"
    (projects / "orgs" / "acme" / "main" / "widget").mkdir(parents=True)
    store.remove_project("acme", "main", "widget")
    store.delete_workspace("acme", "main")
    assert (projects / "orgs" / "acme" / "main" / "widget").is_dir()
    assert store.list_workspaces("acme") == []


def test_removing_a_project_takes_its_overrides_with_it(store):
    store.grant_role("acme", "main", "widget", "agent:ci", "view")
    store.remove_project("acme", "main", "widget")
    assert store.project_roles("acme", "main", "widget") == {}
    with pytest.raises(NotFoundError):
        store.remove_project("acme", "main", "widget")


# ------------------------------------------------------ role overrides


def test_a_grant_replaces_rather_than_conflicting_and_normalizes_the_key(store):
    assert store.grant_role("acme", "main", "widget", "anya", "view") == "user:anya"
    assert store.grant_role("acme", "main", "widget", "user:anya", "edit") == "user:anya"
    assert store.grant_role(
        "acme", "main", "widget", "user:anya/browser:7f3a1b2c", "comment"
    ) == "user:anya"
    # One key, not three spellings of one person.
    assert store.project_roles("acme", "main", "widget") == {"user:anya": "comment"}


def test_revoking_an_override_that_is_not_there_is_a_404(store):
    with pytest.raises(NotFoundError):
        store.revoke_role("acme", "main", "widget", "anya")
    store.grant_role("acme", "main", "widget", "anya", "edit")
    store.revoke_role("acme", "main", "widget", "anya")
    assert store.project_roles("acme", "main", "widget") == {}


def test_an_agent_may_hold_a_per_project_override(store):
    store.grant_role("acme", "main", "widget", "agent:ci", "edit")
    assert store.project_roles("acme", "main", "widget") == {"agent:ci": "edit"}


@pytest.mark.parametrize("role", ["owner", "", "ADMIN", None, 3, "views"])
def test_a_role_outside_the_vocabulary_is_refused(store, role):
    with pytest.raises(ValidationError):
        store.grant_role("acme", "main", "widget", "anya", role)
    with pytest.raises(ValidationError):
        store.add_member("acme", "anya", role)


# ------------------------------------------------------ name validation


@pytest.mark.parametrize(
    "name",
    ["Acme", "1acme", "acme-corp", "acme/main", "", "a" * 41, "acme.main",
     " acme", "acme ", "_acme"],
)
def test_every_level_validates_through_the_id_grammar(store, name):
    """One grammar for org, workspace and project — which is what makes
    `org/ws/proj` splittable and a tenant one path segment."""
    with pytest.raises(ValidationError):
        store.create_org(name)
    with pytest.raises(ValidationError):
        store.create_workspace("acme", name)
    with pytest.raises(ValidationError):
        store.add_project("acme", "main", name)


@pytest.mark.parametrize("name", [None, 5, b"acme", ["acme"]])
def test_a_non_string_name_is_a_validation_error_not_a_crash(store, name):
    with pytest.raises(ValidationError):
        store.create_org(name)


def test_the_error_names_the_level_that_was_wrong(store):
    with pytest.raises(ValidationError) as excinfo:
        store.create_workspace("acme", "Main")
    assert "workspace" in excinfo.value.message
    assert excinfo.value.details == {"workspace": "Main"}


@pytest.mark.parametrize(
    "principal",
    ["", None, "user:", ":nikita", "user:Nikita", "robot:ci", "user:" + "a" * 33,
     "user:_x"],
)
def test_a_malformed_principal_is_refused(principal):
    with pytest.raises(ValidationError):
        principal_key(principal)


def test_principal_normalization(store):
    assert principal_key("nikita") == "user:nikita"
    assert principal_key("user:nikita") == "user:nikita"
    assert principal_key("user:nikita/browser:7f3a1b2c") == "user:nikita"
    assert principal_key("agent:ci") == "agent:ci"
    assert handle_of("agent:ci") is None
    assert handle_of("nikita") == "nikita"


@pytest.mark.parametrize("label", ["", "   ", "x" * 201, 5, b"x"])
def test_a_label_is_bounded_free_text(store, label):
    with pytest.raises(ValidationError):
        store.create_org("other", label=label)


# ----------------------------------------------- reading a broken document


def _write_raw(store, doc) -> None:
    (store.root / ORGS).write_text(json.dumps(doc), encoding="utf-8")


def test_a_malformed_row_grants_nothing_and_raises_nothing(store):
    """Resolution is total over the value (PRD-012's `config_params` lesson):
    a hand edit, a restore or a half-finished migration must resolve as
    *nothing*, never as an exception inside an authorization check — and
    never as a role.
    """
    _write_raw(store, {"orgs": {
        "acme": 5,
        "beta": {"members": "nope", "workspaces": 7},
        "gamma": {"members": {"nikita": "root"},
                  "workspaces": {"main": {"projects": {"w": {"roles": 3}}}}},
        "delta": {"members": {"nikita": "admin"},
                  "workspaces": {"main": {"projects": {"w": {
                      "roles": {"user:anya": "root", "agent:ci": "edit"}}}}}},
    }})
    assert store.org_role("acme", "nikita") is None
    assert store.org_role("beta", "nikita") is None
    assert store.org_role("gamma", "nikita") is None       # "root" is not a rung
    assert store.project_roles("gamma", "main", "w") == {}
    assert store.project_roles("delta", "main", "w") == {"agent:ci": "edit"}
    assert store.project_roles("beta", "main", "w") == {}
    assert store.list_orgs() == [
        {"id": "beta", "label": "beta", "members": 0, "workspaces": 0},
        {"id": "delta", "label": "delta", "members": 1, "workspaces": 1},
        {"id": "gamma", "label": "gamma", "members": 1, "workspaces": 1},
    ]


def test_a_member_whose_role_is_unreadable_is_shown_not_hidden(store):
    _write_raw(store, {"orgs": {"acme": {"members": {"nikita": "root"},
                                         "workspaces": {}}}})
    assert store.list_members("acme") == [{"handle": "nikita", "role": None}]


def test_an_unparsable_document_raises_rather_than_reading_as_empty(store):
    """Treating garbage as "no orgs" would turn a corrupt file into an
    instance where nobody is a member — which the next create_org would
    cheerfully write over the top of."""
    (store.root / ORGS).write_bytes(b"{not json")
    with pytest.raises(ValidationError):
        store.list_orgs()
    (store.root / ORGS).write_text("[]", encoding="utf-8")
    with pytest.raises(ValidationError):
        store.list_orgs()


def test_an_unknown_org_reads_as_no_role_and_writes_as_a_404(store):
    assert store.org_role("nope", "nikita") is None
    assert store.project_roles("nope", "main", "widget") == {}
    with pytest.raises(NotFoundError):
        store.get_org("nope")


# ------------------------------------------------------------- the file


def test_the_document_is_atomic_private_and_leaves_no_staging_files(store):
    path = store.root / ORGS
    assert json.loads(path.read_text(encoding="utf-8"))["orgs"]["acme"]["label"]
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert oct(store.root.stat().st_mode)[-3:] == "700"
    assert [p.name for p in store.root.iterdir() if p.name.endswith(".tmp")] == []


def test_the_lock_file_is_never_the_document(store):
    assert ".lock" in {p.name for p in store.root.iterdir()}
    assert ORGS != authstore_mod.LOCK_FILE
    assert ORGS not in authstore_mod.DOCUMENTS


def test_a_second_process_write_is_visible_with_no_restart(store):
    """The mtime-keyed cache, which is what makes `docker compose exec
    agentcad admin ...` land on the running server's next request."""
    assert store.list_members("acme") == [{"handle": "nikita", "role": "admin"}]
    subprocess.run(
        [sys.executable, "-c",
         "from agentcad.core.tenancy import TenancyStore;"
         f"TenancyStore({str(store.root)!r}).add_member('acme', 'anya', 'edit')"],
        check=True)
    assert store.org_role("acme", "anya") == "edit"


# ---------------------------------------------------------- concurrency


def test_the_guard_is_authstores_own_object_not_a_second_one(tmp_path):
    """`flock` is per open file description: two descriptors on one lock file
    inside one process block against each other for ever. Sharing the
    depth-counted guard is the only correct choice, so it is asserted by
    identity rather than assumed."""
    root = tmp_path / "auth"
    assert TenancyStore(root)._guard is AuthStore(root)._guard
    assert TenancyStore(root)._guard is TenancyStore(root)._guard


def test_a_tenancy_write_inside_an_identity_write_does_not_deadlock(tmp_path):
    """The negation of the test above, and the reason it matters: granting a
    role and creating the account it names belong in one admin operation.
    With private guards this hangs for ever, so it is bounded by a join."""
    root = tmp_path / "auth"
    auth, tenancy = AuthStore(root), TenancyStore(root)
    done: list[str] = []

    def body() -> None:
        with auth._scope():                  # the outermost scope owns the flock
            tenancy.create_org("acme", admin="nikita")
            auth.add_user("nikita")
        done.append("ok")

    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    thread.join(timeout=30)
    assert done == ["ok"], "a tenancy write inside an AuthStore scope deadlocked"
    assert tenancy.org_role("acme", "nikita") == "admin"


def test_concurrent_threads_lose_no_writes(store):
    """Without the in-process lock the read-modify-write windows overlap and
    the last writer wins — with a four-level document, silently."""
    errors: list[BaseException] = []

    def add(base: int) -> None:
        try:
            for i in range(10):
                store.add_member("acme", f"u{base}{i}", "view")
        except BaseException as exc:        # noqa: BLE001 — reported below
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(store.list_members("acme")) == 81       # 80 + the org's admin


@pytest.mark.portability
@pytest.mark.skipif(not HAS_FLOCK, reason="fcntl.flock is POSIX-only")
def test_concurrent_processes_lose_no_writes(tmp_path):
    """The cross-process half — the one `docker compose exec` needs. Without
    the flock these interleave and memberships vanish."""
    root = tmp_path / "auth"
    TenancyStore(root).create_org("acme", admin="nikita")
    script = (
        "import sys;"
        "from agentcad.core.tenancy import TenancyStore;"
        "s = TenancyStore(sys.argv[1]);"
        "[s.add_member('acme', 'p%ss%s' % (sys.argv[2], i)) for i in range(10)]"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, str(root), str(n)])
             for n in range(4)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    assert len(TenancyStore(root).list_members("acme")) == 41


def test_the_module_imports_no_geometry():
    """Server-side tenancy is OCP-free by construction, not by care."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys\n"
         "class Block:\n"
         "    def find_module(self, name, path=None):\n"
         "        if name.split('.')[0] in {'OCP', 'build123d'}:\n"
         "            raise ImportError(name)\n"
         "sys.meta_path.insert(0, Block())\n"
         "import agentcad.core.tenancy, agentcad.core.authz\n"
         "print('ok')"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "ok"


# ------------------------------------------------------- the ambient tenant


def test_without_a_tenant_everything_is_todays_behaviour(tmp_path):
    """FR4/AC7's non-negotiable, at this module's own level: no tenant means
    the bare project name and no root at all."""
    assert current_tenant() is None
    assert qualified("widget") == "widget"
    assert tenant_root(tmp_path) is None


def test_with_a_tenant_names_and_roots_are_qualified(tmp_path):
    with tenant_scope(("acme", "main")):
        assert current_tenant() == ("acme", "main")
        assert qualified("widget") == "acme/main/widget"
        assert tenant_root(tmp_path) == tmp_path / "orgs" / "acme" / "main"
    assert current_tenant() is None
    assert qualified("widget") == "widget"


def test_two_orgs_identically_named_projects_do_not_collide(tmp_path):
    """The whole reason `qualified` exists: turn locks, presence rosters,
    claim maps and event routing all key on a project name today."""
    with tenant_scope(("acme", "main")):
        first = qualified("widget")
    with tenant_scope(("beta", "main")):
        second = qualified("widget")
    with tenant_scope(("acme", "mech")):
        third = qualified("widget")
    assert len({first, second, third}) == 3


def test_the_tenant_root_creates_nothing(tmp_path):
    with tenant_scope(("acme", "main")):
        root = tenant_root(tmp_path)
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "tenant",
    [("Acme", "main"), ("acme", "Main"), ("acme/main", "x"), ("acme",),
     "acme/main", ("acme", None), ("acme", "main", "widget")],
)
def test_a_junk_tenant_is_refused_before_it_becomes_a_path(tenant):
    """The tenant composes into a directory name and a lock key; a caller
    that resolved junk must fail here rather than three layers down."""
    with pytest.raises(ValidationError):
        set_tenant(tenant)
    assert current_tenant() is None


def test_reset_restores_the_outer_tenant_rather_than_clearing_it(tmp_path):
    """A middleware that reset to None instead of to the token would erase an
    outer tenant it did not set."""
    outer = set_tenant(("acme", "main"))
    inner = set_tenant(("beta", "mech"))
    assert qualified("w") == "beta/mech/w"
    reset_tenant(inner)
    assert qualified("w") == "acme/main/w"
    reset_tenant(outer)
    assert current_tenant() is None


def test_the_tenant_does_not_leak_into_a_thread_that_never_set_one():
    """A ContextVar is per-context: a bare `threading.Thread` starts from the
    default, which is local mode. The negation of the test below."""
    seen: list = []
    with tenant_scope(("acme", "main")):
        thread = threading.Thread(target=lambda: seen.append(current_tenant()))
        thread.start()
        thread.join()
        assert current_tenant() == ("acme", "main")
    assert seen == [None]


def test_a_copied_context_carries_the_tenant():
    """Which is what ASGI does per request, and why the ContextVar is the
    right carrier: work dispatched with `copy_context().run` stays in the
    tenant that started it."""
    seen: list = []
    with tenant_scope(("acme", "main")):
        ctx = contextvars.copy_context()
    thread = threading.Thread(target=lambda: seen.append(
        ctx.run(lambda: (current_tenant(), qualified("widget")))))
    thread.start()
    thread.join()
    assert seen == [(("acme", "main"), "acme/main/widget")]
    assert current_tenant() is None


def test_two_threads_hold_different_tenants_at_the_same_time():
    """One process, one service, one kernel pool (design Decision 2): two
    requests for two tenants are two threads, and neither may see the other's.
    """
    barrier = threading.Barrier(2)
    seen: dict[str, str] = {}

    def body(org: str) -> None:
        with tenant_scope((org, "main")):
            barrier.wait(timeout=30)
            seen[org] = qualified("widget")

    threads = [threading.Thread(target=body, args=(org,))
               for org in ("acme", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert seen == {"acme": "acme/main/widget", "beta": "beta/main/widget"}


def test_the_orgs_directory_name_is_a_single_segment(tmp_path):
    """`<projects_dir>/orgs/<org>/<ws>` — one interposed segment, so a hosted
    tree can never be mistaken for a flat local one (`orgs` is itself a legal
    project name)."""
    with tenant_scope(("acme", "main")):
        root = tenant_root(tmp_path)
    assert root.relative_to(tmp_path).parts == ("orgs", "acme", "main")
    assert tenancy_mod.ORGS_DIRNAME == "orgs"


def test_tenant_root_accepts_a_string_projects_dir(tmp_path):
    with tenant_scope(("acme", "main")):
        assert tenant_root(str(tmp_path)) == tmp_path / "orgs" / "acme" / "main"


def test_no_test_in_this_file_leaked_a_tenant_into_the_suite():
    """Every setter above is scoped or reset; if one is not, the rest of the
    suite would run inside a tenant and local mode would stop being local."""
    assert current_tenant() is None
