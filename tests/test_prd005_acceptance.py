"""PRD-005 acceptance — multi-tenant cloud, AC1–AC8.

One test per criterion, each naming it in its docstring, each graded against
the shipped surface: the real guard, the real roles document, the real audit
database, the real git binary and the shipped workflow files. The per-slice
suites (`test_tenancy_integration.py`, `test_tools_cloud.py`, `test_audit.py`,
`test_sync_cli.py`, `test_pool_fairness.py`) hold the case lists; this file
restates each criterion *compactly on the real surface* rather than
duplicating them (the `test_prd012_acceptance.py` / `test_prd026_acceptance.py`
house rule), and imports their fixtures rather than building a second harness.

| AC | Test |
|---|---|
| AC1 | `test_ac1_two_users_on_one_project_see_each_others_edits_live` (machine half; the two-machine staged half is deploy-smoke) |
| AC2 | `test_ac2_no_access_then_view_then_edit_without_a_restart` |
| AC3 | `test_ac3_a_clone_builds_offline_and_a_divergent_push_never_overwrites` |
| AC4 | `test_ac4_the_deployed_stack_is_exercised_by_the_smoke_workflow` (evidence) |
| AC5 | `test_ac5_a_scoped_token_edits_a_is_refused_on_b_and_dies_on_revocation` |
| AC6 | `test_ac6_the_audit_log_distinguishes_a_person_the_chat_agent_and_a_token` |
| AC7 | `test_ac7_*` — three sharp local-mode probes + the suite-count guard |
| AC8 | `test_ac8_the_release_pipeline_signs_and_notarizes_when_secrets_exist` (evidence) |

Four of them need a note before you read them.

* **AC1 says "from two machines", and a test is one process.** What is driven
  here is the mechanism: two enrolled people, two cookie jars, two live
  WebSockets on one hosted app, each seeing the other's edit stamped with
  their shared tenant, and the audit log naming which of them made which. The
  *staged-instance* half — two real sessions against a container over HTTPS —
  is `.github/workflows/deploy-smoke.yml` ("A second user, an org and a role
  ladder"), which runs on push-to-main and dispatch, never on a PR, so its
  evidence lands post-merge. That split is design-spec "Scope rulings", and
  `test_ac4_*` asserts the workflow really contains those steps.
* **AC2's "third user without access" has two honest shapes**, and both are
  asserted. A *person* who is not a member of the org cannot address the
  workspace at all, and gets the **name-free 404** the design chose over a
  403 (a 403 would confirm the org exists — the existence oracle FR5
  forbids). A principal who *can* address the workspace and holds nothing on
  the project — a scoped token, which is never an org member (FR3) — gets the
  structured `permission_error` on read and on write. The ladder is then
  walked on that principal, in one app, with no restart.
* **AC4 and AC8 are deployments**, which no unit test can perform: the
  criteria are graded as evidence that the pipeline that performs them ships
  and is shaped the way the criterion requires. Each names, in its docstring,
  where the runtime evidence lives.
* **AC7 is "nothing changed"**, which no single test asserts. The whole suite
  is the gate; what is here is three sharp probes at the surfaces this PRD
  touched and a check that a `make test` count is on the record.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

from agentcad.core import audit, locks, tenancy, tenancy_wiring

from .conftest import BOX_SCRIPT, HOSTED_ORIGIN, make_test_service
# The harnesses are slice 4's and slice 6's, reused verbatim: a hosted app
# with the wrappers installed, two orgs and four enrolled people; and a
# local-mode app served on a real socket with a seeded git project. Building
# a second of either here would be a second opinion about what one looks like.
from .test_sync_cli import local_instance    # noqa: F401 — a fixture
from .test_tenancy_integration import (  # noqa: F401 — `tenanted` is a fixture
    ACME, WS, make_project, tenanted,
)

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "docs" / "changelog"
WORKFLOWS = REPO / ".github" / "workflows"
PRD_NAME = "PRD-005-multi-tenant-cloud.md"


def _find_prd() -> Path:
    """Locate the PRD wherever it currently lives — a PRD moves from
    `in-progress/` to `completed/` at merge, not when the build finishes
    (changelog 0164's lesson, copied from `test_prd005a_acceptance.py`)."""
    root = REPO / "docs" / "prd"
    for stage in ("in-progress", "completed", "pending"):
        candidate = root / stage / PRD_NAME
        if candidate.is_file():
            return candidate
    found = sorted(root.rglob(PRD_NAME))
    assert found, f"{PRD_NAME} is not anywhere under {root}"
    return found[0]


PRD = _find_prd()


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """Never the developer's real `~/.agentcad/config.json` — `build_registry`
    reads it, and two of the fixtures here build a registry before a test body
    could set anything (the `hosted`/`test_sync_*` reason, and those fixtures
    set the same path again for themselves)."""
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))


@pytest.fixture(autouse=True)
def _restore_tenant():
    """Undo any tenant a test leaves set (`test_tenancy_integration`'s reason:
    a ContextVar set at a test's top level outlives the test)."""
    token = tenancy.tenant_var.set(None)
    try:
        yield
    finally:
        tenancy.tenant_var.reset(token)


def _log(tenanted):
    """The audit database behind the app under test — the same accessor
    `routes_auth` and the CLI reach it through, not a hand-built path."""
    return audit.for_auth_store(tenanted.store)


def tool(client, tool_name, /, headers=None, **args):
    """POST one tool call and answer its payload.

    `client` and `tool_name` are positional-**only** so that a tool argument
    called `name` — which `create_agent_token` has — lands in `**args` instead
    of colliding with this helper's own parameter (`test_tools_cloud`'s
    `_tool`, for its reason).
    """
    return client.post(f"/api/tools/{tool_name}", json=args,
                       headers=headers or {}).json()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _client(tenanted):
    from fastapi.testclient import TestClient

    return TestClient(tenanted.app, base_url=HOSTED_ORIGIN)


def _await_subscribers(service, count: int, timeout: float = 10.0) -> None:
    """Block until *count* queues are on the bus.

    `/ws` subscribes **after** the handshake completes, so a test that started
    writing the moment `websocket_connect` returned would be racing the
    endpoint for the first event. `subscriber_count()` takes the bus's own
    lock, so this is a real instant rather than an arbitrary one.
    """
    deadline = time.monotonic() + timeout
    while service.bus.subscriber_count() < count:
        assert time.monotonic() < deadline, "the websockets never subscribed"
        time.sleep(0.01)


# =================================================================== AC1


def test_ac1_two_users_on_one_project_see_each_others_edits_live(tenanted):
    """**AC1** — two people in one org collaborate on one project: both see
    the other's edit live, and attribution is correct.

    The live half is two real WebSockets on one hosted app, each carrying its
    own session cookie, both hearing both edits stamped `acme/main`. The
    attribution half is the audit log, which is what a person can actually go
    and read afterwards: two rows on `widget`, one per principal, classified
    `human`. (A session that carries a device composes as
    `user:anya/browser:…`; the fixture's sessions do not, and the log's
    `principal` filter matches a person **and** their devices for exactly that
    reason.)

    The two-machine, staged-instance half is `deploy-smoke.yml`'s two-user
    session (see this module's docstring and `test_ac4_*`).
    """
    anya, nikita = tenanted.as_("anya"), tenanted.as_("nikita")
    assert make_project(anya, "widget").status_code == 201

    with anya.websocket_connect("/ws") as anya_ws, \
            nikita.websocket_connect("/ws") as nikita_ws:
        _await_subscribers(tenanted.service, 2)
        assert "error" not in tool(anya, "set_assembly", project="widget",
                                   instances=[])
        assert "error" not in tool(nikita, "set_assembly", project="widget",
                                   instances=[])
        heard = {"anya": [anya_ws.receive_json() for _ in range(2)],
                 "nikita": [nikita_ws.receive_json() for _ in range(2)]}

    for who, events in heard.items():
        assert [e["type"] for e in events] == ["project_changed",
                                               "project_changed"], (who, events)
        for event in events:
            assert event["project"] == "widget"
            assert event["tenant"] == f"{ACME}/{WS}", who

    rows = _log(tenanted).query(ACME, project="widget", action="set_assembly")
    assert sorted(row["principal"] for row in rows) == ["user:anya",
                                                        "user:nikita"]
    assert {row["kind"] for row in rows} == {"human"}
    assert _log(tenanted).query(ACME, principal="user:anya")[0][
        "action"] == "set_assembly"


# =================================================================== AC2


def test_ac2_no_access_then_view_then_edit_without_a_restart(tenanted):
    """**AC2** — a third principal with no access is refused on read *and*
    write; granting `view` then `edit` flips each capability with no restart.

    Two shapes of "no access", both real and both asserted (see the module
    docstring): a person outside the org gets the name-free 404 at the
    workspace boundary, and a principal that can address the workspace but
    holds nothing on the project gets the structured `permission_error`. The
    ladder is walked on the second, because it is the only principal that can
    hold *nothing* on a project it can address — an org member always has a
    default role, which is what "granting view" would already be.

    Nothing is restarted, rebuilt or re-created between the rungs: the guard
    re-reads the roles document, whose mtime-keyed cache is what makes a
    `docker compose exec` grant land on the next request.
    """
    nikita = tenanted.as_("nikita")
    assert make_project(nikita, "widget").status_code == 201

    # (a) A person outside the org: the workspace is not addressable, and the
    # refusal names neither the org nor the workspace.
    outsider = tenanted.as_("bob")           # a member of globex only
    header = {"X-Agentcad-Workspace": f"{ACME}/{WS}"}
    for answer in (outsider.get("/api/projects/widget", headers=header),
                   outsider.put("/api/projects/widget/assembly",
                                json={"instances": []}, headers=header)):
        assert answer.status_code == 404
        assert answer.json()["error"]["message"] == "no such workspace"
        assert ACME not in answer.text

    # (b) A principal that CAN address the workspace and holds nothing on the
    # project: a scoped token (never an org member — FR3). Minting one also
    # *grants* it its scope's role, so the org admin takes that back first —
    # which is the third rung of the ladder anyway, walked in reverse.
    minted = tool(nikita, "create_agent_token", name="ci", org=ACME,
                  workspace=WS, projects=["widget"], role="edit")
    assert "error" not in minted, minted
    assert tenanted.orgs.project_roles(ACME, WS, "widget") == {
        "agent:ci": "edit"}
    tenanted.orgs.revoke_role(ACME, WS, "widget", "agent:ci")

    agent = _client(tenanted)
    read = "/api/projects/widget"
    write = "/api/projects/widget/assembly"
    body = {"instances": []}

    # Both are refused at the `view` floor, and that is the honest shape: a
    # principal who may not read the project never reaches the write guard, so
    # the rung it names is the one they failed — the *first* thing they need,
    # not the last.
    for answer in (agent.get(read, headers=_bearer(minted["token"])),
                   agent.put(write, json=body,
                             headers=_bearer(minted["token"]))):
        assert answer.status_code == 403
        error = answer.json()["error"]
        assert error["type"] == "PermissionError"
        assert error["details"] == {"required": "view", "project": "widget",
                                    "principal_role": None}
    # The tool surface answers the same refusal in the house envelope.
    refused = agent.post("/api/tools/get_assembly", json={"project": "widget"},
                         headers=_bearer(minted["token"])).json()
    assert refused["error"]["type"] == "permission_error"

    # view: reads land, writes are still refused and name the rung they need.
    tenanted.orgs.grant_role(ACME, WS, "widget", "agent:ci", "view")
    assert agent.get(read, headers=_bearer(minted["token"])).status_code == 200
    held = agent.put(write, json=body, headers=_bearer(minted["token"]))
    assert held.status_code == 403
    assert held.json()["error"]["details"] == {
        "required": "edit", "project": "widget", "principal_role": "view"}

    # edit: the same app, the same token, the very next request.
    tenanted.orgs.grant_role(ACME, WS, "widget", "agent:ci", "edit")
    assert agent.put(write, json=body,
                     headers=_bearer(minted["token"])).status_code == 200
    assert "error" not in agent.post(
        "/api/tools/set_assembly",
        json={"project": "widget", "instances": []},
        headers=_bearer(minted["token"])).json()

    # ...and revoking the grant closes it again, still with no restart.
    tenanted.orgs.revoke_role(ACME, WS, "widget", "agent:ci")
    assert agent.put(write, json=body,
                     headers=_bearer(minted["token"])).status_code == 403


# =================================================================== AC3


@pytest.mark.integration
def test_ac3_a_clone_builds_offline_and_a_divergent_push_never_overwrites(
        local_instance, kernel, tmp_path, monkeypatch):
    """**AC3** — a laptop clone builds and edits fully offline; a deliberately
    divergent branch surfaces PRD-001 merge conflicts rather than overwriting.

    Driven against a **real git binary on a real socket** (slice 6's harness,
    imported rather than rebuilt), in three movements:

    1. clone, then open the clone with an ordinary local service — no sync
       code in sight — and build a part on the real kernel;
    2. both sides commit, and `agentcad push` is refused with the branch
       named, having sent nothing;
    3. `agentcad pull` runs the divergence through PRD-001's merge
       orchestrator, which reports the conflict as `merge_conflict` and leaves
       the local branch, the local file and the server exactly as they were.

    The CLI-session half of the criterion (a person typing this) is
    `test_sync_cli.py::test_the_cli_reports_conflicts_and_exits_1`, which
    drives the console script and asserts the exit code and the wording.
    """
    from agentcad.core import sync
    from agentcad.core.history import ProjectHistory
    from agentcad.core.tools import build_registry

    from .test_sync_cli import commit
    from .test_sync_server import url_for

    # Never the developer's own `~/.agentcad/` — `sync` reads a token out of
    # it from a subprocess git spawns, so the isolation has to ride the env.
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    base, project, _service = local_instance
    dest = tmp_path / "laptop" / "demo"

    # (1) offline: the clone is a working project, on the local kernel.
    sync.clone(url_for(base), dest)
    offline = make_test_service(dest.parent, kernel)
    registry = build_registry(offline)
    name = offline.open_project(str(dest))["name"]
    built = registry.call("create_part", {"project": name, "part_id": "box",
                                          "script": BOX_SCRIPT})
    assert "error" not in built, built
    assert built["status"]["state"] == "ok"
    assert built["metrics"]["volume_mm3"] > 0
    assert ProjectHistory().snapshot(dest, "add box") is not None

    # (2) both sides move the SAME file, and the push is refused outright.
    commit(project, "parts/a.py", "# part a, their way\n")
    commit(dest, "parts/a.py", "# part a, my way\n")
    their_head = ProjectHistory().head(project)
    my_head = ProjectHistory().head(dest)
    with pytest.raises(sync.SyncError) as refused:
        sync.push(dest)
    assert "refs/heads/master" in str(refused.value)
    assert "agentcad pull" in str(refused.value)
    assert ProjectHistory().head(project) == their_head, \
        "a refused push must leave the server exactly as it was"

    # (3) the pull surfaces PRD-001's conflict and overwrites nothing.
    def merger(branch, remote_ref, **kwargs):
        return sync.merge_diverged(offline, name, branch, remote_ref, **kwargs)

    result = sync.pull(dest, merger=merger)
    assert len(result["conflicts"]) == 1, result
    error = result["conflicts"][0]["merge"]["error"]
    assert error["type"] == "merge_conflict"
    assert [c["path"] for c in error["details"]["conflicts"]] == ["parts/a.py"]
    assert ProjectHistory().head(dest) == my_head
    assert (dest / "parts" / "a.py").read_text() == "# part a, my way\n"
    assert (project / "parts" / "a.py").read_text() == "# part a, their way\n"


# =================================================================== AC4


def test_ac4_the_deployed_stack_is_exercised_by_the_smoke_workflow():
    """**AC4** — the whole stack deploys from the public repo with one compose
    file, `/api/health` reports the mode, and TLS serves the UI.

    **Graded as evidence, because a test cannot deploy a container.** The
    runtime evidence is the `deploy-smoke` workflow run: it builds the image,
    `docker compose up`s it, waits on the healthcheck, and drives the whole
    PRD-005 story against the running instance. It runs on push-to-main and
    on dispatch — never on `pull_request`, because a multi-GB image build on
    every PR is not a gate anybody would keep — so the evidence for a given
    change lands *after* merge (design-spec "Scope rulings"). The compose file
    itself is graded structurally, with no Docker daemon, by
    `tests/test_deploy_config.py`; PRD-005a's AC8 covers the health body and
    the restart-persistence half.

    What is asserted here is that the workflow really contains this PRD's
    steps — the check that would have caught the flow being weakened or
    dropped while the criterion still claimed it.
    """
    import yaml

    path = WORKFLOWS / "deploy-smoke.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = document[True]                # YAML 1.1 reads bare `on:` as True
    assert "pull_request" not in triggers, \
        "the multi-GB image build must not be on the PR path"
    job = next(iter(document["jobs"].values()))
    runs = "\n".join(step.get("run") or "" for step in job["steps"])

    # The bootstrap is the shipped CLI, not a script that mirrors it.
    bootstrap = next(step.get("run") or "" for step in job["steps"]
                     if "agentcad admin org add" in (step.get("run") or ""))
    for command in ("agentcad admin org add acme",
                    "agentcad admin org workspace add acme main",
                    "agentcad admin org member add acme anya --role view"):
        assert command in bootstrap, command
    # ...over `docker compose exec`, and with no python one-liner reaching
    # around the product to write the document itself (which is what this
    # step did until the CLI existed).
    assert "docker compose exec" in bootstrap
    assert "python" not in bootstrap, \
        "the org bootstrap is a CLI now; nothing should reach around it"

    # The tenancy story, step by step (AC1/AC2/AC5/AC6's deployed halves).
    for needle in (
            "/api/auth/enrol/",                       # two enrolled people
            "/api/tools/grant_role",                  # the role flip
            "/api/tools/create_agent_token",          # a scoped token
            "/api/tools/revoke_agent_token",          # revocation
            "/git/acme/main/widget.git",              # authenticated sync
            "agentcad admin audit query acme",        # the audit log
    ):
        assert needle in runs, needle
    # The health body and the mode are PRD-005a's, and still exercised.
    assert "/api/health" in runs


# =================================================================== AC5


def test_ac5_a_scoped_token_edits_a_is_refused_on_b_and_dies_on_revocation(
        tenanted):
    """**AC5** — an agent token scoped to project A edits A, is 403'd on B,
    and stops working on its very next call once revoked.

    Through the **real tools on the wired registry**: minted by
    `create_agent_token`, refused by the registry floor `tenancy_wiring`
    installs, revoked by `revoke_agent_token`. `test_tools_cloud.py` grades
    the same criterion against the decision function and the token record;
    what this adds is that the general tool surface — the one every agent
    actually calls — is where the refusal happens.

    The scope is a **ceiling, not a grant**: `ci` is scoped to `widget`, and
    it still needs an explicit per-project role there, because an agent has no
    org default (FR3).
    """
    nikita = tenanted.as_("nikita")
    for name in ("widget", "gadget"):
        assert make_project(nikita, name).status_code == 201
    minted = tool(nikita, "create_agent_token", name="ci", org=ACME,
                  workspace=WS, projects=["widget"], role="edit")
    assert minted["token"] and minted["id"]
    # The mint is also the grant: `agent:ci` holds `edit` on `widget` and
    # nothing anywhere else, because an agent has no org default.
    assert minted["principal"] == "agent:ci"

    agent = _client(tenanted)
    edit_a = agent.post("/api/tools/set_assembly",
                        json={"project": "widget", "instances": []},
                        headers=_bearer(minted["token"])).json()
    assert "error" not in edit_a, edit_a

    # B: same org, same workspace, a project the token was not scoped to and
    # holds no role on. Both surfaces refuse it.
    edit_b = agent.post("/api/tools/set_assembly",
                        json={"project": "gadget", "instances": []},
                        headers=_bearer(minted["token"])).json()
    assert edit_b["error"]["type"] == "permission_error"
    assert edit_b["error"]["details"]["required"] == "edit"
    assert edit_b["error"]["details"]["principal_role"] is None
    assert agent.put("/api/projects/gadget/assembly", json={"instances": []},
                     headers=_bearer(minted["token"])).status_code == 403

    # Revocation bites on the next request — the store is the authority, which
    # is the whole reason these are not JWTs.
    assert agent.get("/api/projects/widget",
                     headers=_bearer(minted["token"])).status_code == 200
    assert "error" not in tool(nikita, "revoke_agent_token",
                               token_id=minted["id"])
    assert agent.get("/api/projects/widget",
                     headers=_bearer(minted["token"])).status_code == 401
    assert agent.post("/api/tools/whoami", json={},
                      headers=_bearer(minted["token"])).status_code == 401


# =================================================================== AC6


def test_ac6_the_audit_log_distinguishes_a_person_the_chat_agent_and_a_token(
        tenanted):
    """**AC6** — the audit log tells a human edit, a chat-agent edit and an
    agent-token edit apart on one project.

    Graded through the **general tap**, wired into the serve path by
    `tenancy_wiring._install_registry` — not through `tools_cloud`'s own taps,
    which only see the tenancy tools. The tool the three principals call is an
    ordinary `set_assembly`, which knows nothing about any of this.

    The chat dock genuinely is a third thing: it has no HTTP principal at all
    and identifies itself by setting `locks.set_client_id("chat")` inside its
    executor (`agent/chat.py::_call_tool`). It is driven here through the REAL
    path — `_call_tool` dispatched on the event loop's executor exactly as
    `_run_turn_locked` does — not a hand-set tenant: `run_in_executor` does not
    propagate contextvars, so the turn's tenant is captured in the async caller
    and threaded across the boundary, and it is that thread the floor, the
    audit row and the storage root all read. An audit that only read
    `security.current_principal()` would record it as whoever was signed in, or
    as nobody.
    """
    anya, nikita = tenanted.as_("anya"), tenanted.as_("nikita")
    assert make_project(anya, "widget").status_code == 201

    # 1. a person, in a browser session.
    assert "error" not in tool(anya, "set_assembly", project="widget",
                               instances=[])

    # 2. the built-in chat agent, through its executor. It is granted `edit`
    #    like anything else — `chat` reads as a handle in the roles document,
    #    and as an *agent* in the log.
    from agentcad.agent.chat import ChatEngine

    tenanted.orgs.grant_role(ACME, WS, "widget", "chat", "edit")
    engine = ChatEngine(tenanted.registry, tenanted.service.bus, api_key=None)

    async def _chat_edit():
        # What `_run_turn_locked` does at the dispatch site: capture the turn's
        # ambient tenant, then hand it to `_call_tool` across the executor.
        with tenancy.tenant_scope((ACME, WS)):
            tenant = tenancy.current_tenant()
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, engine._call_tool, "set_assembly",
                {"project": "widget", "instances": []}, "main", tenant)

    answered = asyncio.run(_chat_edit())
    assert "error" not in answered, answered

    # 3. an agent token.
    minted = tool(nikita, "create_agent_token", name="claude", org=ACME,
                  workspace=WS, projects=["widget"], role="edit")
    assert "error" not in _client(tenanted).post(
        "/api/tools/set_assembly",
        json={"project": "widget", "instances": []},
        headers=_bearer(minted["token"])).json()

    rows = _log(tenanted).query(ACME, project="widget", action="set_assembly")
    assert {(row["principal"].split("/")[0], row["kind"]) for row in rows} == {
        ("user:anya", "human"),
        ("chat", "agent"),
        ("agent:claude", "agent"),
    }
    assert {row["outcome"] for row in rows} == {"ok"}
    # Interleaved in one log, on one project, newest first — which is what an
    # admin actually reads.
    assert [row["ts"] for row in rows] == sorted(
        (row["ts"] for row in rows), reverse=True)


# ============================================ the tap wiring (FR12) itself
#
# `audit.tap_registry` shipped in slice 5 "tested and deliberately not
# installed anywhere yet" (changelog 0348). These are the properties its
# installation has to have, and the reason AC6 above can be graded through it.


def test_a_mutating_tool_call_lands_exactly_one_row_with_its_outcome(tenanted):
    anya = tenanted.as_("anya")
    make_project(anya, "widget")
    log = _log(tenanted)

    assert "error" not in tool(anya, "set_assembly", project="widget",
                               instances=[])
    rows = log.query(ACME, action="set_assembly")
    assert len(rows) == 1
    row = rows[0]
    assert row["project"] == "widget" and row["outcome"] == "ok"
    assert row["principal"] == "user:anya" and row["kind"] == "human"
    assert row["args_digest"] and len(row["args_digest"]) == 64

    # A refusal is recorded too, with the refusal as the outcome: "who tried
    # what" is exactly the question an audit log is read for.
    vee = tenanted.as_("vee")                # a viewer
    assert tool(vee, "set_assembly", project="widget",
                instances=[])["error"]["type"] == "permission_error"
    assert [r["outcome"] for r in log.query(ACME, action="set_assembly")] == [
        "permission_error", "ok"]


def test_a_read_tool_lands_no_row(tenanted):
    anya = tenanted.as_("anya")
    make_project(anya, "widget")
    assert tool(anya, "get_assembly", project="widget")["instances"] == []
    assert tool(anya, "list_projects")["projects"]
    assert _log(tenanted).query(ACME) == [], \
        "a read is not an event; the log would be noise, not evidence"


def test_installing_the_wiring_twice_records_one_row_not_two(tenanted):
    """The `_TAPPED`/`_CALL` sentinels, on the real seam: `install` is
    idempotent and is called twice in tests and once per `cmd_serve`."""
    anya = tenanted.as_("anya")
    make_project(anya, "widget")
    tenancy_wiring.install(tenanted.service, tenanted.registry)
    tenancy_wiring.install(tenanted.service, tenanted.registry)
    assert "error" not in tool(anya, "set_assembly", project="widget",
                               instances=[])
    assert len(_log(tenanted).query(ACME, action="set_assembly")) == 1


def test_uninstalling_the_wiring_takes_the_tap_back_off_the_registry(tenanted):
    """`uninstall` restores `registry.call` to the class's own method — the
    tap rides the same `_agentcad_inner` handle as the floor it wraps, so
    neither is left behind on a service a test hands to something else."""
    anya = tenanted.as_("anya")
    make_project(anya, "widget")
    tenancy_wiring.uninstall(tenanted.service, tenanted.registry)
    assert not hasattr(tenanted.registry.call, "_agentcad_inner")

    with tenancy.tenant_scope((ACME, WS)):
        locks.set_client_id("user:anya")
        tenanted.registry.call("set_assembly", {"project": "widget",
                                                "instances": []})
    assert _log(tenanted).query(ACME) == [], "the tap outlived its wiring"


def test_local_mode_writes_no_row_and_constructs_no_audit_log(
        kernel, tmp_path, monkeypatch):
    """The property that makes the tap safe to install unconditionally: with
    no tenant it is never reached, so a local `agentcad serve` neither writes
    a row nor **creates** `<state>/audit/` (AC7 is "unchanged", not "unchanged
    except for a database")."""
    from agentcad.core.tools import build_registry

    opened: list = []
    monkeypatch.setattr(audit, "for_auth_store",
                        lambda *a, **k: opened.append(a))
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    tenancy_wiring.install(service, registry, config=lambda: None)
    try:
        service.create_project("widget")
        assert "error" not in registry.call("set_assembly",
                                            {"project": "widget",
                                             "instances": []})
    finally:
        tenancy_wiring.uninstall(service, registry)
    assert opened == [], "an audit log was constructed in local mode"
    assert not (tmp_path / "audit").exists()


def test_a_hosted_instance_whose_audit_log_cannot_be_opened_says_so(
        tenanted, monkeypatch, capsys):
    """The other side of "never raise": an instance that has orgs and cannot
    open their databases records nothing, and silence there would be the worst
    kind of green. It warns once and the write still lands."""
    def broken(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(audit, "for_auth_store", broken)
    anya = tenanted.as_("anya")
    make_project(anya, "widget")
    for _ in range(3):
        assert "error" not in tool(anya, "set_assembly", project="widget",
                                   instances=[])
    warnings = capsys.readouterr().err
    assert "audit log is unavailable" in warnings
    assert warnings.count("audit log is unavailable") == 1, warnings


# =================================================================== AC7


def test_ac7_local_mode_registers_no_hosted_tool(kernel, tmp_path,
                                                 monkeypatch):
    """**AC7**, probe 1 — the agent surface a local instance offers is the one
    it always offered: none of PRD-005's tools register without a security
    config, because a tool pack decides at *registration* time (the FEM
    precedent, and why `cmd_serve` installs the config first)."""
    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    assert security_module.current_config() is None
    service = make_test_service(tmp_path / "projects", kernel)
    names = {t.name for t in build_registry(service).list()}
    assert names & {"whoami", "create_agent_token", "revoke_agent_token",
                    "grant_role", "revoke_role", "list_members",
                    "sync_status"} == set()


def test_ac7_local_mode_wrappers_are_identity_functions(kernel, tmp_path):
    """**AC7**, probe 2 — every wrapper installed and no tenant anywhere: the
    store roots flat, the lock key is the bare project name, and the event
    reaches the subscriber unstamped. Local mode is a property of these
    functions rather than of a test we have to keep passing."""
    from agentcad.core.tools import build_registry

    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    tenancy_wiring.install(service, registry, config=lambda: None)
    try:
        service.create_project("widget")
        assert (tmp_path / "projects" / "widget" / "project.json").is_file()
        assert not (tmp_path / "projects" / "orgs").exists()
        assert service.store.lock_key("widget") == "widget"
        queue_ = service.bus.subscribe()
        registry.call("set_assembly", {"project": "widget", "instances": []})
        assert queue_.get_nowait() == {"type": "project_changed",
                                       "project": "widget"}
    finally:
        tenancy_wiring.uninstall(service, registry)


def test_ac7_an_untenanted_hosted_instance_is_byte_for_byte_005a(hosted):
    """**AC7**, probe 3 — the boundary case a self-hoster upgrading into this
    release actually lands in: hosted, authenticated, and with no orgs at all.
    `whoami` answers PRD-005a's four keys exactly, and a project lands in the
    flat root."""
    from .conftest import login

    client, _store = hosted
    login(client)
    tenancy_wiring.install(client.agentcad_service)
    try:
        payload = client.post("/api/tools/whoami", json={}).json()
        assert set(payload) == {"principal", "kind", "role", "mode"}
        assert client.post("/api/projects",
                           json={"name": "widget"}).status_code == 201
        root = client.agentcad_service.store.root
        assert (root / "widget" / "project.json").is_file()
        assert not (root / "orgs").exists()
    finally:
        tenancy_wiring.uninstall(client.agentcad_service)


def test_ac7_the_full_suite_count_is_cited():
    """**AC7**'s evidence half — "the full suite is green with no auth
    configured" is a claim about a *run*, so what is checkable here is that a
    `make test` count is on the record in the newest changelog entry (the
    PRD-004 AC10 / PRD-012 AC8 / PRD-026 AC7 / PRD-027 AC6 precedent).
    Recomputing it would mean running the full suite from inside itself, and
    `--collect-only` counts cases, not what `make test` reports."""
    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    text = latest.read_text(encoding="utf-8")
    assert "make test" in text, \
        f"{latest.name} is the newest changelog entry and cites no `make test`"
    assert re.search(r"\b\d{3,6}\s+passed\b", text.replace(",", "")), \
        f"{latest.name} does not cite a `make test` suite count"


# =================================================================== AC8


def test_ac8_the_release_pipeline_signs_and_notarizes_when_secrets_exist():
    """**AC8** — the macOS build passes notarization and the Windows build
    passes signing in the release pipeline.

    **Graded as evidence, and honestly short of the criterion.** No agent can
    conjure an Apple Developer ID or an EV code-signing certificate, so what
    ships is the pipeline: `release.yml` builds both bundles on every tag and
    runs `codesign` + `notarytool` / `signtool` **iff** the secrets are
    provisioned, and prints an explicit unsigned notice when they are not
    (design-spec "Scope rulings", and the PRD's own FR15/AC8 amendment). The
    positive runtime evidence is a tagged release run *after* the founder
    provisions `MACOS_CERT_P12`/`APPLE_ID_*`/`WINDOWS_CERT_PFX`; the secrets
    are listed in `docs/deployment.md`.

    What is asserted is the shape the criterion depends on: the gate is a
    job-level `env` boolean, never `secrets.*` inside an `if:` (the `bench.yml`
    rule — `if:` interpolation of a secret is both unreliable and a leak
    path), and every signing step hangs off it in both directions.
    """
    import yaml

    document = yaml.safe_load(
        (WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
    assert set(document[True]) == {"push", "workflow_dispatch"}, document[True]
    jobs = document["jobs"]
    assert {"macos", "windows"} <= set(jobs)

    for job_name, flag, signer in (("macos", "HAVE_MACOS_SIGNING", "codesign"),
                                   ("windows", "HAVE_WINDOWS_SIGNING",
                                    "signtool")):
        job = jobs[job_name]
        gate = job["env"][flag]
        assert "secrets." in gate, (job_name, gate)
        conditions = [str(step.get("if") or "") for step in job["steps"]]
        assert f"env.{flag} == 'true'" in conditions, job_name
        assert f"env.{flag} != 'true'" in conditions, job_name
        for condition in conditions:
            assert "secrets." not in condition, (job_name, condition)
        runs = "\n".join(step.get("run") or "" for step in job["steps"])
        assert signer in runs, job_name
        # The build itself is never gated: an unsigned bundle still ships.
        build = next(step for step in job["steps"]
                     if "onedir" in (step.get("name") or ""))
        assert not build.get("if")

    macos = "\n".join(step.get("run") or "" for step in jobs["macos"]["steps"])
    assert "notarytool" in macos and "--options runtime" in macos
    assert (REPO / "packaging" / "entitlements.plist").is_file()


# ============================================ the org bootstrap CLI (FR5)
#
# `agentcad admin org …` is what `deploy-smoke.yml` now runs to bootstrap a
# tenancy, so it is graded here rather than left to the workflow: the workflow
# proves it runs in a container, these prove it writes what it claims.


@pytest.fixture
def admin_cli(tmp_path, monkeypatch):
    """Run the real `main()` in-process against an isolated state dir."""
    from agentcad.cli import main

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("AGENTCAD_STATE_DIR", raising=False)

    def run(*argv):
        monkeypatch.setattr("sys.argv", ["agentcad", *argv])
        main()

    return run


def _orgs(tmp_path):
    from agentcad.core.tenancy import TenancyStore

    return TenancyStore(tmp_path / "state" / "auth")


def test_the_org_cli_bootstraps_a_tenancy_the_way_deploy_smoke_does(
        admin_cli, tmp_path, capsys):
    """The exact three commands the workflow runs, and the document they
    leave: the org with its admin in one write, the workspace, the member."""
    admin_cli("admin", "org", "add", "acme", "--label", "Acme Robotics",
              "--admin", "smoke")
    admin_cli("admin", "org", "workspace", "add", "acme", "main",
              "--label", "Mechanical")
    admin_cli("admin", "org", "member", "add", "acme", "anya", "--role", "view")
    capsys.readouterr()

    orgs = _orgs(tmp_path)
    assert orgs.get_org("acme")["label"] == "Acme Robotics"
    assert orgs.org_role("acme", "smoke") == "admin"
    assert orgs.org_role("acme", "anya") == "view"
    assert orgs.has_workspace("acme", "main")
    # Tenancy is bookkeeping: not one directory under the projects tree.
    assert not (tmp_path / "state" / "projects").exists()


def test_the_org_cli_defaults_a_member_to_the_weakest_rung(admin_cli,
                                                           tmp_path):
    admin_cli("admin", "org", "add", "acme")
    admin_cli("admin", "org", "member", "add", "acme", "anya")
    assert _orgs(tmp_path).org_role("acme", "anya") == "view"


@pytest.mark.parametrize("argv", [
    ("admin", "org", "add", "Acme"),                     # ID_RE
    ("admin", "org", "workspace", "add", "nosuch", "main"),
    ("admin", "org", "member", "add", "nosuch", "anya"),
])
def test_the_org_cli_exits_nonzero_on_a_refusal(admin_cli, argv):
    """An `AppError` is `error: …` on stderr and exit 2, never a traceback —
    `cmd_admin`'s contract, and this block rides it."""
    with pytest.raises(SystemExit) as exit_info:
        admin_cli(*argv)
    assert exit_info.value.code != 0


def test_the_org_cli_starts_no_service_and_no_kernel(admin_cli, monkeypatch):
    """`_auth_store`'s property, which is the whole reason `docker compose
    exec` works while the server is running (or wedged)."""
    from agentcad.kernel.client import KernelClient

    def boom(*args, **kwargs):               # pragma: no cover - the assertion
        raise AssertionError("the admin CLI started a kernel")

    monkeypatch.setattr(KernelClient, "start", boom)
    admin_cli("admin", "org", "add", "acme", "--admin", "smoke")


# ============================================================ the record


def test_the_prd_records_the_two_amended_criteria():
    """The gradings this file leans on are the PRD's own text, not a reading
    of it invented here: FR15/AC8 ship secrets-gated, and FR13 is the
    `Client:` trailer rendered by the UI rather than a rewritten git author
    (design-spec "Scope rulings", PRD amended in slice 9)."""
    text = PRD.read_text(encoding="utf-8")
    assert "secrets-gated" in text and "provisioned certificates" in text
    assert "`Client:` trailer" in text
    assert "AgentCAD <agentcad@local>" in text
