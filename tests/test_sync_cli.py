"""Git sync, client half (PRD-005 FR8-client/FR10): login, clone, push, pull.

Like the server half (`tests/test_sync_server.py`, whose harness these reuse),
these tests drive a **real git binary against a real uvicorn socket**. That is
not thoroughness for its own sake: every claim this slice makes is a claim
about what git does with our configuration — that a bare clone flipped into
`.history` is a repo `ProjectHistory` can commit into, that a wildcard refspec
pushes without deleting, that a token handed over through a credential helper
lands in no file and on no argv. None of those can be asserted from inside the
process that spells them.

The expensive parts are shared: one session kernel (the `kernel` fixture), tiny
projects, and the merge-driving tests are the only ones that build geometry.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agentcad.core import sync
from agentcad.core.history import ProjectHistory
from agentcad.core.tools import build_registry
from agentcad.server import security as security_module
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service
from .test_sync_server import _free_port, seed, serve, url_for

pytestmark = pytest.mark.integration

AGENTCAD = Path(sys.executable).parent / "agentcad"


# --------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """Never the developer's real `~/.agentcad/` — and that matters twice here:
    `sync.json` holds a token, and the credential helper this CLI installs
    reads it back out of a **subprocess** git spawns, so the isolation has to
    ride the environment rather than a monkeypatched attribute."""
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    for name in ("AGENTCAD_TOKEN", "AGENTCAD_URL", "AGENTCAD_SYNC_CONFIG"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def local_instance(kernel, tmp_path):
    """`(base_url, project_path, service)` for a served local-mode app."""
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    app = create_app(service, build_registry(service))
    with serve(app) as base:
        yield base, project, service


@pytest.fixture
def hosted_instance(kernel, tmp_path):
    """A served **hosted** app plus a bearer token.

    The port is allocated first because a hosted app compares every request's
    `Host` against its configured public origin: the origin has to name the
    socket the test is about to bind, or every call is a 403 that reads as
    "unhealthy while serving perfectly".
    """
    from agentcad.core.appmode import AppMode
    from agentcad.core.authstore import AuthStore
    from agentcad.server.security import SecurityConfig

    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    store = AuthStore(tmp_path / "auth")
    store.enrol(store.add_user("nikita", role="admin"), "correct horse battery")
    token = store.add_token("bot", role="member")
    cfg = SecurityConfig(mode=AppMode("hosted", origin, b"k" * 32), store=store)
    # Before `build_registry`, exactly as `cmd_serve` does it: a pack decides
    # at registration time whether it may register at all.
    security_module.install(cfg)
    app = create_app(service, build_registry(service), security=cfg)
    try:
        with serve(app, port=port) as base:
            yield base, project, service, token
    finally:
        # The slot is process-global; a leaked hosted config would make the
        # next local-mode app in this worker believe it is hosted.
        security_module.install(None)


def cli(*args: str, cwd: Path | None = None, stdin: str | None = None,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the real console script, the way a user would."""
    return subprocess.run([str(AGENTCAD), *args], cwd=None if cwd is None
                          else str(cwd), input=stdin, capture_output=True,
                          text=True, timeout=600, env={**os.environ,
                                                       **(env or {})})


def commit(project_dir: Path, relative: str, text: str) -> str:
    """Write a file and snapshot it **through ProjectHistory**.

    Using the product's own committer rather than a raw `git commit` is the
    point in half these tests: it is what proves a clone is a project history
    repo and not merely a git checkout that looks like one (a clone never ran
    `git init`, so the repo-local identity `_ensure_repo` writes has to be part
    of the clone recipe or every snapshot silently returns `None`).
    """
    path = project_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    head = ProjectHistory().snapshot(project_dir, f"edit {relative}")
    assert head is not None, f"snapshot of {relative} did not commit"
    return head


# ------------------------------------------------------------------ login

def test_login_verifies_the_token_and_stores_it_0600(hosted_instance):
    base, _project, _service, token = hosted_instance

    result = sync.login(base, token)

    assert result["instance"] == base
    assert result["principal"] == "agent:bot"
    assert result["mode"] == "hosted"
    assert sync.token_for(base) == token
    path = sync.config_path()
    assert path.is_file()
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert oct(path.parent.stat().st_mode)[-3:] == "700"


def test_login_refuses_a_bad_token_and_stores_nothing(hosted_instance):
    """A wrong token must fail *here*, not three commands later inside a git
    clone. `/api/health` cannot answer this question — it is in the anonymous
    surface, so it answers 200 to a bad token as well."""
    base, _project, _service, _token = hosted_instance
    import httpx

    assert httpx.get(f"{base}/api/health", timeout=30,
                     headers={"Authorization": "Bearer acad_no_such"}
                     ).status_code == 200

    with pytest.raises(sync.SyncError) as exc:
        sync.login(base, "acad_dead_beef")
    assert "refused that token" in str(exc.value)
    assert sync.token_for(base) is None
    assert not sync.config_path().exists()


def test_login_refuses_a_url_carrying_a_token():
    """The URL leak the credential helper exists to close (spike §A9 P2)."""
    with pytest.raises(ValueError) as exc:
        sync.login("https://x-access-token:acad_secret@cad.example.com", "t")
    assert "carries credentials" in str(exc.value)


def test_login_through_the_cli_never_needs_the_token_on_the_argv(
        hosted_instance):
    base, _project, _service, token = hosted_instance

    result = cli("login", base, stdin=token + "\n")

    assert result.returncode == 0, result.stderr
    assert "signed in to" in result.stdout
    assert sync.token_for(base) == token


# ------------------------------------------------------ the credential helper

def test_the_credential_helper_speaks_gits_protocol(hosted_instance):
    """`get` answers for a known host, with the token as the **password**:
    the server ignores the username (`routes_sync._promote_basic_to_bearer`
    discards it), which is why the username is a label and not an identity."""
    base, _project, _service, token = hosted_instance
    sync.remember_instance(base, token)
    host = base.split("://", 1)[1]

    answer = cli("credential", "get",
                 stdin=f"protocol=http\nhost={host}\npath=git/a/b/c.git\n\n")

    assert answer.returncode == 0
    lines = answer.stdout.splitlines()
    assert lines == [f"username={sync.CREDENTIAL_USERNAME}",
                     f"password={token}"]
    assert sync.CREDENTIAL_USERNAME == "x-access-token"


def test_the_helper_is_silent_for_an_unknown_host_and_for_store_and_erase(
        hosted_instance):
    """Silence is the protocol's "I have nothing": git then falls through
    instead of authenticating as nobody. `store`/`erase` do nothing because
    `agentcad login` owns that file — a helper that let git write into it
    would let a redirect target's 401 plant a credential nobody typed."""
    base, _project, _service, token = hosted_instance
    sync.remember_instance(base, token)

    other = cli("credential", "get",
                stdin="protocol=https\nhost=evil.example.com\n\n")
    assert other.returncode == 0 and other.stdout == ""

    for action in ("store", "erase", "capability"):
        answer = cli("credential", action,
                     stdin=f"protocol=http\nhost={base.split('://')[1]}\n"
                           f"username=x\npassword={token}\n\n")
        assert answer.returncode == 0, action
        assert answer.stdout == "", action
    assert sync.token_for(base) == token


# ------------------------------------------------------------------- clone

def test_clone_produces_a_project_history_repo(local_instance, tmp_path):
    """The spike's §A10 flip, asserted property by property: no `.git` in the
    tree, the GIT_DIR at `.history`, a non-bare repo whose work tree is the
    project, the fetch refspec pointing at `refs/remotes/`, and the managed
    excludes in place before anything could have committed `.history/`."""
    base, _project, _service = local_instance
    dest = tmp_path / "clone"

    result = sync.clone(url_for(base), dest)

    assert result["project"] == "demo"
    assert not (dest / ".git").exists()
    assert (dest / ".history" / "HEAD").is_file()
    assert (dest / "project.json").is_file()
    assert (dest / "parts" / "a.py").read_text() == "# part a\n"
    # FR8: derived data never syncs, and the excludes that make that true on
    # the next snapshot are `history._EXCLUDE_LINES`, not a second list.
    excludes = (dest / ".history" / "info" / "exclude").read_text()
    for line in (".cache/", "exports/", ".history/", "*.tmp"):
        assert line in excludes
    config = sync.local(dest, "config", "--list").stdout
    assert "core.bare=false" in config
    assert f"core.worktree={dest.resolve()}" in config
    assert "remote.origin.fetch=+refs/heads/*:refs/remotes/origin/*" in config
    assert "user.email=agentcad@local" in config
    # `--bare` writes `+refs/heads/*:refs/heads/*`, which would move local
    # branches on the next fetch. That it does not is the flip's whole point.
    assert "remote.origin.fetch=+refs/heads/*:refs/heads/*" not in config
    assert "v1.0" in sync.local(dest, "tag").stdout.split()
    sync.verify_layout(dest)


def test_a_clone_is_a_working_offline_project(local_instance, kernel, tmp_path):
    """AC3's machine half: a cloned project builds with no server at all.

    The clone is opened by an ordinary local service — no sync code in sight —
    which creates a part and builds it on the real kernel, and snapshots it
    through `ProjectHistory`. If the clone recipe had left out the repo-local
    identity, the snapshot would silently be a no-op.
    """
    base, _project, _service = local_instance
    dest = tmp_path / "offline" / "demo"
    sync.clone(url_for(base), dest)

    offline = make_test_service(dest.parent, kernel)
    registry = build_registry(offline)
    name = offline.open_project(str(dest))["name"]
    assert name == "demo"
    created = registry.call("create_part", {"project": name, "part_id": "box",
                                            "script": BOX_SCRIPT})
    assert "error" not in created, created
    assert created["status"]["state"] == "ok", created["status"]
    assert created["metrics"]["volume_mm3"] > 0

    assert ProjectHistory().snapshot(dest, "add box") is not None
    log = ProjectHistory().log(dest, limit=5)
    assert log[0]["message"].startswith("add box")
    # The clone kept the server's history, so this is a real continuation of
    # it rather than a fresh repo that happens to hold the same files.
    assert any(entry["message"] == "seed" for entry in log)


def test_clone_refuses_a_non_sync_url_and_a_non_empty_destination(
        local_instance, tmp_path):
    base, _project, _service = local_instance
    with pytest.raises(ValueError) as exc:
        sync.clone(f"{base}/projects/demo", tmp_path / "x")
    assert "not an AgentCAD sync url" in str(exc.value)

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(sync.SyncError):
        sync.clone(url_for(base), occupied)
    assert (occupied / "keep.txt").read_text() == "mine"


# -------------------------------------------------------------------- push

def test_push_lands_on_the_server_and_materializes(local_instance, tmp_path):
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    commit(dest, "parts/c.py", "# part c\n")

    result = sync.push(dest)

    assert result["refspecs"] == ["refs/heads/*:refs/heads/*",
                                  "refs/tags/*:refs/tags/*"]
    assert [u["ref"] for u in result["updated"]] == ["refs/heads/master"]
    # The server materialized: refs advanced *and* the work tree caught up.
    assert (project / "parts" / "c.py").read_text() == "# part c\n"
    # ...and untracked derived data survived the checkout.
    assert (project / ".cache" / "mesh.bin").read_bytes() == b"derived"
    # A second push has nothing to say and says so.
    assert sync.push(dest)["updated"] == []


def test_tags_travel_both_ways(local_instance, tmp_path):
    """`--follow-tags` carries annotated tags only (spike §A6, measured), so
    `push` sends an explicit `refs/tags/*` refspec and a lightweight tag —
    which nothing in AgentCAD makes today, and a human's `git tag` does —
    travels too."""
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    # v1.0 came down with the clone; two new ones go back up.
    assert "v1.0" in sync.local(dest, "tag").stdout.split()
    commit(dest, "parts/d.py", "# part d\n")
    sync.local(dest, "tag", "-a", "v2.0", "-m", "release 2.0")
    sync.local(dest, "tag", "light-1")

    sync.push(dest)

    from agentcad.core import sync_server
    refs = sync_server._run(project, "show-ref").stdout
    assert "refs/tags/v2.0" in refs
    assert "refs/tags/light-1" in refs


def test_push_refspecs_keep_the_internal_merge_branch_off_the_wire(
        local_instance, tmp_path):
    """`pull` parks the fetched other side in a real local branch (the merge
    machinery merges branch into branch). That is bookkeeping, not somebody's
    work, so the wildcard is expanded to leave it out rather than publishing a
    branch nobody made."""
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    head = sync.local(dest, "rev-parse", "HEAD").stdout.strip()
    sync.local(dest, "branch", f"{sync.INTERNAL_BRANCH_PREFIX}master", head)
    sync.local(dest, "branch", "mine", head)

    refspecs = sync.push_refspecs(dest)

    assert "refs/heads/mine:refs/heads/mine" in refspecs
    assert "refs/heads/master:refs/heads/master" in refspecs
    assert "refs/tags/*:refs/tags/*" in refspecs
    assert not any(sync.INTERNAL_BRANCH_PREFIX in spec for spec in refspecs)
    sync.push(dest)
    from agentcad.core import sync_server
    refs = sync_server._run(project, "show-ref").stdout
    assert "refs/heads/mine" in refs
    assert sync.INTERNAL_BRANCH_PREFIX not in refs


def test_a_divergent_push_is_refused_with_the_servers_own_words(
        local_instance, tmp_path):
    """FR9 through the CLI: the server refuses, and the message the pre-receive
    hook wrote is what the human reads — not "exit status 1"."""
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    server_head = ProjectHistory().head(project)

    commit(project, "parts/server.py", "# server side\n")     # they moved
    commit(dest, "parts/mine.py", "# my side\n")              # so did we

    with pytest.raises(sync.SyncError) as exc:
        sync.push(dest)
    # The CLIENT refused, before a byte left the machine: a wildcard refspec
    # with no leading `+` will not push a non-fast-forward. That fact lives in
    # git's porcelain STDOUT, which is why the message can name the branch.
    assert "agentcad pull" in str(exc.value)
    assert "refs/heads/master" in str(exc.value)
    # Nothing landed: the whole push is one ref transaction.
    assert ProjectHistory().head(project) != server_head
    assert not (project / "parts" / "mine.py").exists()


def test_a_server_refusal_reaches_the_user_verbatim():
    """When the pre-receive hook is the one that says no, its words ARE the
    message — the client never re-words them (`core/sync.remote_lines`).

    The sample is the spike's own capture (§A5). With our refspecs the client
    usually refuses first, which is why this is asserted on the bytes git
    prints rather than staged against a live server: the relay must work the
    day a hook refuses for a reason this slice never thought of.
    """
    stderr = (
        "remote: agentcad: refs/tags/v1.1 already exists on the server; tags "
        "are immutable        \n"
        "To http://127.0.0.1:8732/proj.git\n"
        " ! [remote rejected] v1.1 -> v1.1 (pre-receive hook declined)\n"
        "error: failed to push some refs to 'http://127.0.0.1:8732/proj.git'\n"
    )
    assert sync.remote_lines(stderr) == [
        "agentcad: refs/tags/v1.1 already exists on the server; tags are "
        "immutable"]
    assert sync._push_message("http://127.0.0.1:8732", "", stderr).startswith(
        "agentcad: refs/tags/v1.1")
    exc = sync.SyncError("x", stderr=stderr)
    assert exc.remote == sync.remote_lines(stderr)


# -------------------------------------------------------------------- pull

def test_pull_fast_forwards_without_a_kernel(local_instance, tmp_path):
    """The cheap path: nothing to merge, so nothing is built and no worker is
    spawned. `merger=None` proves it — a fast-forward that needed the merge
    machinery would raise here instead of moving the ref."""
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    commit(project, "parts/theirs.py", "# theirs\n")

    result = sync.pull(dest, merger=None)

    assert [b["action"] for b in result["branches"] if b["branch"] == "master"] \
        == ["fast_forward"]
    assert (dest / "parts" / "theirs.py").read_text() == "# theirs\n"
    assert result["conflicts"] == [] and result["diverged"] == []


def test_pull_moves_a_branch_that_has_no_working_tree(local_instance, tmp_path):
    """A clone holds every server branch, and most of them have no checkout.

    Moving one is a ref update and nothing else — `update-ref`, not
    `reset --hard`, and not a checkout that would change the files the user is
    looking at. `feature` here is a real local branch (a `--bare` clone copies
    all heads) whose working tree `BranchManager` has never materialized.
    """
    base, _project, _service = local_instance
    first, second = tmp_path / "a", tmp_path / "b"
    sync.clone(url_for(base), first)
    sync.clone(url_for(base), second)
    master_before = sync.local(second, "rev-parse", "master").stdout.strip()

    head = commit(first, "parts/shared.py", "# shared\n")
    # `feature` was branched from an ancestor of this commit, so pointing it
    # here is a genuine fast-forward for it.
    sync.local(first, "update-ref", "refs/heads/feature", head)
    sync.push(first)

    result = sync.pull(second, merger=None)

    actions = {b["branch"]: b["action"] for b in result["branches"]}
    assert actions == {"master": "fast_forward", "feature": "fast_forward"}
    assert sync.local(second, "rev-parse", "feature").stdout.strip() == head
    assert (second / "parts" / "shared.py").is_file()
    assert master_before != sync.local(second, "rev-parse",
                                       "master").stdout.strip()
    # No worktree was invented for `feature` — it is materialized on demand.
    assert not (second / ".history" / "trees").exists()


def test_pull_refuses_a_dirty_work_tree_and_changes_nothing(local_instance,
                                                            tmp_path):
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    commit(project, "parts/theirs.py", "# theirs\n")
    (dest / "parts" / "a.py").write_text("# edited, never committed\n",
                                         encoding="utf-8")

    with pytest.raises(sync.SyncError) as exc:
        sync.pull(dest, merger=None)

    assert "uncommitted" in str(exc.value)
    assert (dest / "parts" / "a.py").read_text() == "# edited, never committed\n"
    assert not (dest / "parts" / "theirs.py").exists()


@pytest.fixture
def diverged(local_instance, kernel, tmp_path):
    """A clone and its server, each one commit ahead of the other, plus the
    merger a divergent `agentcad pull` drives."""
    base, project, service = local_instance
    dest = tmp_path / "work" / "demo"
    sync.clone(url_for(base), dest)

    client = make_test_service(dest.parent, kernel)
    build_registry(client)                  # installs branches + merges
    name = client.open_project(str(dest))["name"]

    def merger(branch, remote_ref, **kwargs):
        return sync.merge_diverged(client, name, branch, remote_ref, **kwargs)

    return base, project, dest, merger, client, name


def test_a_divergence_is_merged_by_the_prd001_machinery(diverged):
    """The real entry point: `MergeOrchestrator.merge`, branch into branch,
    staged and kernel-validated — so a pull gets the structure-aware
    `project.json` driver and the two-parent commit for free."""
    base, project, dest, merger, client, name = diverged
    commit(project, "parts/theirs.py", "# theirs\n")
    commit(dest, "parts/mine.py", "# mine\n")

    result = sync.pull(dest, merger=merger)

    entry = next(b for b in result["branches"] if b["branch"] == "master")
    assert entry["state"] == "diverged" and entry["action"] == "merged"
    assert "error" not in entry["merge"], entry["merge"]
    assert result["conflicts"] == []
    # Both sides' work is present, and the merge is a real two-parent commit.
    assert (dest / "parts" / "mine.py").is_file()
    assert (dest / "parts" / "theirs.py").is_file()
    parents = sync.local(dest, "rev-list", "--parents", "-n", "1",
                         "HEAD").stdout.split()
    assert len(parents) == 3, parents
    # The scratch branch is bookkeeping and does not outlive the merge.
    assert not any(b["name"].startswith(sync.INTERNAL_BRANCH_PREFIX)
                   for b in client.history.branches(dest))

    # ...and the push that was refused before the merge now lands (FR9's
    # recovery path is the ordinary one).
    pushed = sync.push(dest)
    assert pushed["updated"]
    assert (project / "parts" / "mine.py").read_text() == "# mine\n"


def test_a_conflicting_divergence_is_listed_and_nothing_is_overwritten(
        diverged):
    base, project, dest, merger, client, name = diverged
    before = (dest / "parts" / "a.py").read_text()
    commit(project, "parts/a.py", "# part a, their way\n")
    commit(dest, "parts/a.py", "# part a, my way\n")
    my_head = ProjectHistory().head(dest)

    result = sync.pull(dest, merger=merger)

    assert len(result["conflicts"]) == 1
    error = result["conflicts"][0]["merge"]["error"]
    assert error["type"] == "merge_conflict"
    paths = [c["path"] for c in error["details"]["conflicts"]]
    assert paths == ["parts/a.py"]
    assert error["details"]["conflicts"][0]["kind"] == "script"
    assert "resolve_merge" in error["details"]["hint"]
    # Never reset, never overwrite: the branch, the file and the server are
    # exactly as they were.
    assert ProjectHistory().head(dest) == my_head
    assert (dest / "parts" / "a.py").read_text() == "# part a, my way\n"
    assert before != (dest / "parts" / "a.py").read_text()
    assert client.merges.status(name)["merge"]["outstanding"] == 1


def test_the_cli_reports_conflicts_and_exits_1(diverged):
    """What a person sees. `--no-merge` keeps the kernel out of it, so this
    asserts the reporting and the exit code without spawning a worker."""
    _base, project, dest, _merger, _client, _name = diverged
    commit(project, "parts/a.py", "# theirs\n")
    commit(dest, "parts/a.py", "# mine\n")

    result = cli("pull", str(dest), "--no-merge")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "diverged from the server: master" in result.stderr
    assert "Nothing was overwritten." in result.stderr


def test_the_cli_clones_and_pushes(local_instance, tmp_path):
    """The subcommands themselves, argparse and all — the layer every other
    test in this file reaches around."""
    base, project, _service = local_instance
    dest = tmp_path / "cli-clone"

    cloned = cli("clone", url_for(base), str(dest))
    assert cloned.returncode == 0, cloned.stderr
    assert "cloned demo into" in cloned.stdout
    # Not signed in to this instance: a warning, never a refusal (a local
    # instance has no accounts at all).
    assert "agentcad login" in cloned.stderr

    commit(dest, "parts/via_cli.py", "# via the cli\n")
    pushed = cli("push", str(dest))
    assert pushed.returncode == 0, pushed.stderr
    assert "pushed 1 ref(s)" in pushed.stdout
    assert (project / "parts" / "via_cli.py").is_file()

    assert cli("push", str(dest)).stdout.startswith("everything up to date")
    assert cli("pull", str(dest)).stdout.strip() == "already up to date"


def test_a_sync_command_outside_a_project_is_a_usage_error(tmp_path):
    """Exit 2, not 1: "you are in the wrong directory" is a usage mistake, not
    a refusal by the other side."""
    for command in ("push", "pull", "status"):
        result = cli(command, str(tmp_path))
        assert result.returncode == 2, (command, result.stdout, result.stderr)
        assert "not an AgentCAD project" in result.stderr


# ------------------------------------------------------------------ status

def test_status_counts_ahead_and_behind(local_instance, tmp_path):
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)

    def state(branch="master", **kwargs):
        report = sync.status(dest, **kwargs)
        return next(b for b in report["branches"] if b["branch"] == branch)

    assert state()["state"] == "up_to_date"
    # Every server branch is a real local branch here, not just a
    # remote-tracking ref: a `--bare` clone copies all of `refs/heads/*`, and
    # for AgentCAD that is the right shape — branches belong to the project,
    # and their working trees are materialized on demand by `BranchManager`.
    assert state("feature")["state"] == "up_to_date"
    assert {b["branch"] for b in sync.status(dest)["branches"]} == {"master",
                                                                    "feature"}

    commit(dest, "parts/mine.py", "# mine\n")
    assert state() == {**state(), "state": "ahead", "ahead": 1, "behind": 0}

    sync.push(dest)
    assert state()["state"] == "up_to_date"

    commit(project, "parts/theirs.py", "# theirs\n")
    assert state()["state"] == "up_to_date"          # offline by default
    assert state(fetch=True) == {**state(), "state": "behind", "ahead": 0,
                                 "behind": 1}

    commit(dest, "parts/more.py", "# more\n")
    diverged = state(fetch=True)
    assert (diverged["state"], diverged["ahead"], diverged["behind"]) \
        == ("diverged", 1, 1)
    assert sync.status(dest)["diverged"] == ["master"]


def test_status_through_the_cli_exits_1_on_a_divergence(local_instance,
                                                        tmp_path):
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    commit(project, "parts/theirs.py", "# theirs\n")
    commit(dest, "parts/mine.py", "# mine\n")

    clean = cli("status", str(dest), "--json")
    assert clean.returncode == 0                     # nothing fetched yet
    assert json.loads(clean.stdout)["branches"]

    result = cli("status", str(dest), "--fetch")
    assert result.returncode == 1
    assert "diverged" in result.stdout


# ------------------------------------------------------------- token safety

def _walk_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            yield path


def test_the_token_lands_in_no_file_no_url_and_no_argv(hosted_instance,
                                                       tmp_path, monkeypatch):
    """The spike's four-way leak measurement (§A9), ported as assertions.

    `http.extraHeader` puts the token in `ps` for every user on the machine and
    re-sends it across a same-host redirect; a URL-embedded token is written
    into `remote.origin.url` and lives on disk in every clone. Only the
    credential helper leaks nothing — so this test proves the *absence*, in the
    three places the spike found the other three patterns leaking.
    """
    base, _project, _service, token = hosted_instance
    sync.login(base, token)
    dest = tmp_path / "clone"

    argvs: list[list[str]] = []
    real_exec = sync._exec
    monkeypatch.setattr(sync, "_exec",
                        lambda cmd, **kw: (argvs.append(list(cmd)),
                                           real_exec(cmd, **kw))[1])

    sync.clone(url_for(base), dest)
    commit(dest, "parts/mine.py", "# mine\n")

    # A live `ps` sampler running for the duration of a real push: the spike
    # counted 2 processes carrying the token with `http.extraHeader`.
    ps = shutil.which("ps")
    seen: list[str] = []
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            probe = subprocess.run([ps, "-eww", "-o", "args="],
                                   capture_output=True, text=True)
            seen.append(probe.stdout)
            time.sleep(0.005)

    sampler = threading.Thread(target=sample, daemon=True)
    if ps:
        sampler.start()
    try:
        sync.push(dest)
    finally:
        stop.set()
        if ps:
            sampler.join(timeout=10)

    assert argvs, "no git call was recorded"
    for argv in argvs:
        assert not any(token in part for part in argv), argv
    if ps:      # the spike's measurement itself; skipped where there is no ps
        assert seen, "the ps sampler never ran"
        assert not any(token in snapshot for snapshot in seen)

    url = sync.local(dest, "config", "--get", "remote.origin.url").stdout
    assert token not in url and "@" not in url
    carriers = [str(path) for path in _walk_files(dest)
                if token.encode() in path.read_bytes()]
    assert carriers == []
    # ...and the one file that DOES hold it is outside the clone, 0600.
    assert token in sync.config_path().read_text()
    assert sync.config_path().resolve() not in [p.resolve()
                                                for p in _walk_files(dest)]


def test_a_push_without_a_token_fails_with_the_servers_message(
        hosted_instance, tmp_path):
    """`GIT_TERMINAL_PROMPT=0`, and the credential helper's other virtue: git
    only offers a helper's answer after a 401 from the final URL, so the
    server's own challenge — not a hung prompt — is what a signed-out user
    meets."""
    base, _project, _service, token = hosted_instance
    sync.login(base, token)
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    commit(dest, "parts/mine.py", "# mine\n")
    sync.forget_instance(base)

    with pytest.raises(sync.SyncError) as exc:
        sync.push(dest)
    # git's own words are "could not read Username … terminal prompts
    # disabled" — a message about a terminal, for a command that never wanted
    # one. What it means here is exactly one thing.
    assert "terminal prompts disabled" in exc.value.stderr
    assert str(exc.value).startswith(f"not signed in to {base}")
    assert "agentcad login" in str(exc.value)


# ------------------------------------------ poisoned remote / server defenses

def _server_git(project: Path, *args: str, inp: str | None = None):
    """A raw plumbing call against a served project's history repo."""
    git_dir = str(project / ".history")
    return subprocess.run(
        ["git", "--git-dir", git_dir, "--work-tree", str(project), *args],
        input=inp, capture_output=True, text=True, timeout=60,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": git_dir,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _poison_master(project: Path, subpath: str = "hooks/post-merge") -> str:
    """Advance the served project's master to a commit whose tree writes an
    executable into `.history/<subpath>` — a compromised/hostile server."""
    def g(*args, inp=None):
        return _server_git(project, *args, inp=inp)
    blob = g("hash-object", "-w", "--stdin",
             inp="#!/bin/sh\ntouch /tmp/PWNED_client\n").stdout.strip()
    parts = subpath.split("/")
    tree = g("mktree", inp=f"100755 blob {blob}\t{parts[-1]}\n").stdout.strip()
    for comp in reversed(parts[:-1]):
        tree = g("mktree", inp=f"040000 tree {tree}\t{comp}\n").stdout.strip()
    base_tree = g("ls-tree", "HEAD").stdout
    root = g("mktree",
             inp=base_tree + f"040000 tree {tree}\t.history\n").stdout.strip()
    head = g("rev-parse", "HEAD").stdout.strip()
    commit = g("commit-tree", root, "-p", head, inp="poison\n").stdout.strip()
    g("update-ref", "refs/heads/master", commit)
    return commit


def test_clone_refuses_a_poisoned_default_branch(local_instance, tmp_path):
    """A hostile server whose default branch writes into `.history`: the clone
    belt refuses before a byte is checked out, and cleans up after itself."""
    base, project, _service = local_instance
    _poison_master(project)
    dest = tmp_path / "clone"

    with pytest.raises(sync.SyncError) as exc:
        sync.clone(url_for(base), dest)

    assert "git internals" in str(exc.value)
    assert not dest.exists()
    assert str(dest.resolve()) not in sync.load()["clones"]


def test_clone_refuses_a_hostile_default_branch_name(local_instance, tmp_path):
    """A server advertising HEAD `refs/heads/-evil`: the short name would reach
    `git checkout` as an option. `_head_branch` refuses it as argv."""
    base, project, _service = local_instance
    head = _server_git(project, "rev-parse", "HEAD").stdout.strip()
    _server_git(project, "update-ref", "--", "refs/heads/-evil", head)
    _server_git(project, "symbolic-ref", "HEAD", "refs/heads/-evil")
    dest = tmp_path / "clone"

    with pytest.raises(sync.SyncError) as exc:
        sync.clone(url_for(base), dest)

    assert "-evil" in str(exc.value)
    assert not dest.exists()


def test_pull_refuses_a_poisoned_remote_branch(local_instance, tmp_path):
    """A clean clone, then the server advances master to a poisoned commit. The
    fast-forward belt refuses; the local ref never moves and nothing is planted
    into the workstation's `.history`."""
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    before = ProjectHistory().head(dest)
    _poison_master(project)

    with pytest.raises(sync.SyncError) as exc:
        sync.pull(dest, merger=None)

    assert "git internals" in str(exc.value)
    assert ProjectHistory().head(dest) == before
    assert not (dest / ".history" / "hooks" / "post-merge").is_file()


def _poison_master_named(project: Path, topdir: str) -> str:
    """Advance master to a commit whose tree writes `<topdir>/config` — a
    case/spelling VARIANT of `.history` (e.g. `.History`), the PRD-005 re-check
    bypass, straight into what folds to the live GIT_DIR on a case-insensitive
    workstation."""
    def g(*args, inp=None):
        return _server_git(project, *args, inp=inp)
    cfg = g("hash-object", "-w", "--stdin",
            inp='[core]\n\tfsmonitor = "touch /tmp/PWNED_client"\n').stdout.strip()
    tree = g("mktree", inp=f"100644 blob {cfg}\tconfig\n").stdout.strip()
    base_tree = g("ls-tree", "HEAD").stdout
    root = g("mktree",
             inp=base_tree + f"040000 tree {tree}\t{topdir}\n").stdout.strip()
    head = g("rev-parse", "HEAD").stdout.strip()
    commit = g("commit-tree", root, "-p", head, inp="casefold\n").stdout.strip()
    g("update-ref", "refs/heads/master", commit)
    return commit


def test_clone_refuses_a_case_folded_history_default_branch(local_instance,
                                                            tmp_path):
    """A hostile server whose default branch writes `.History/config` — the
    case-fold bypass. The clone belt now folds, so it refuses before a byte is
    checked out (where `.History` would land on the live `.history` GIT_DIR)."""
    base, project, _service = local_instance
    _poison_master_named(project, ".History")
    dest = tmp_path / "clone"

    with pytest.raises(sync.SyncError) as exc:
        sync.clone(url_for(base), dest)

    assert "git internals" in str(exc.value)
    assert not dest.exists()


def test_pull_refuses_a_case_folded_history_remote_branch(local_instance,
                                                          tmp_path):
    """A clean clone, then the server advances master to `.HISTORY/config`. The
    fast-forward belt folds and refuses; the local ref never moves and nothing
    lands in the workstation's live `.history`."""
    base, project, _service = local_instance
    dest = tmp_path / "clone"
    sync.clone(url_for(base), dest)
    before = ProjectHistory().head(dest)
    live = (dest / ".history" / "config").read_text()
    _poison_master_named(project, ".HISTORY")

    with pytest.raises(sync.SyncError) as exc:
        sync.pull(dest, merger=None)

    assert "git internals" in str(exc.value)
    assert ProjectHistory().head(dest) == before
    # The live GIT_DIR config is git's own, unchanged — nothing folded onto it.
    assert (dest / ".history" / "config").read_text() == live
    assert "fsmonitor" not in (dest / ".history" / "config").read_text()


def test_clone_token_is_restored_when_the_clone_fails(hosted_instance, tmp_path):
    """`clone --token` must not clobber a working token: a wrong one that fails
    the clone is rolled back to what the user already had."""
    base, _project, _service, token = hosted_instance
    sync.login(base, token)                     # a good, verified token
    dest = tmp_path / "clone"

    with pytest.raises(sync.SyncError):
        sync.clone(url_for(base), dest, token="acad_wrong_token")

    assert sync.token_for(base) == token        # the good token survives
    assert not dest.exists()
    assert str(dest.resolve()) not in sync.load()["clones"]


def test_clone_forgets_a_first_token_when_the_clone_fails(hosted_instance,
                                                          tmp_path):
    """The other half: a `--token` that was the FIRST for the instance and then
    failed leaves no unverified credential behind."""
    base, _project, _service, _token = hosted_instance   # no login → no token
    dest = tmp_path / "clone"

    with pytest.raises(sync.SyncError):
        sync.clone(url_for(base), dest, token="acad_wrong_token")

    assert sync.token_for(base) is None
    assert not dest.exists()


# --------------------------------------------------------------- remote MCP

@pytest.mark.timeout(300)
def test_mcp_remote_proxies_a_hosted_instance(hosted_instance):
    """FR10 end to end: `agentcad mcp --remote` is the same stdio server every
    MCP client already speaks to, pointed at a hosted instance and carrying a
    bearer. Local mode is untouched — this changes which base URL it proxies,
    nothing else."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    base, _project, _service, token = hosted_instance
    sync.login(base, token)
    params = StdioServerParameters(
        command=str(AGENTCAD),
        # No `--token`: it comes from what `login` stored, which is the way
        # that keeps it off this very argv.
        args=["mcp", "--remote", base],
        env={**os.environ},
    )

    async def drive():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = [tool.name for tool in listed.tools]
                assert "list_projects" in names
                result = await session.call_tool("list_projects", {})
                return json.loads(result.content[0].text)

    payload = asyncio.run(drive())
    assert [p["name"] for p in payload["projects"]] == ["demo"]


def test_mcp_remote_refuses_to_run_without_a_token(hosted_instance):
    base, _project, _service, _token = hosted_instance

    result = cli("mcp", "--remote", base)

    assert result.returncode == 2
    assert "agentcad login" in result.stderr


def test_mcp_remote_refuses_a_url_it_cannot_read():
    result = cli("mcp", "--remote", "not-a-url")
    assert result.returncode == 2
    assert "http://" in result.stderr
