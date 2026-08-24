"""Git smart-HTTP sync, server half (PRD-005 FR8-server / FR9).

**These tests drive a real `git` binary against a real uvicorn socket.** They
have to: a `TestClient` cannot serve `git clone`, and every property this
slice claims — protocol negotiation, chunked pushes with no `Content-Length`,
a `pre-receive` hook's message reaching the human, the work tree catching up
afterwards — is a property of what a git *client* does with our bytes. Nothing
here asserts through the proxy's internals except the two seams and the two
pure functions, which are unit-tested directly.

The repos are deliberately tiny — a few files, a few commits, one 3 MB blob —
and the module measures ~56 s on this machine (25 tests, each with its own
uvicorn and two or three git processes). It is marked `integration` (it
crosses a process, a server and a git boundary); not `slow`, which is for the
broad/timeout-driven coverage `make test-fast` drops.
"""

from __future__ import annotations

import base64
import contextlib
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from agentcad.core import sync_server
from agentcad.core.history import ProjectHistory
from agentcad.core.model import AuthzError
from agentcad.core.tools import build_registry
from agentcad.server import routes_sync
from agentcad.server import security as security_module
from agentcad.server.app import create_app

from .conftest import make_test_service

pytestmark = pytest.mark.integration


# --------------------------------------------------------------- harness

#: A git credential helper that answers from the environment, so the token is
#: never on any argv (the spike's rule). `!`-prefixed helpers run through
#: `sh -c "<helper> get"`, so `$1` is the operation.
CREDENTIAL_HELPER = (
    "!f() { if [ \"$1\" = get ]; then "
    "printf 'username=agentcad\\npassword=%s\\n' \"$AGENTCAD_TEST_TOKEN\"; "
    "fi; }; f"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def serve(app, port: int | None = None):
    """Run *app* on a real localhost port for the duration of the block."""
    import uvicorn

    port = port or _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        assert not thread.is_alive(), "uvicorn did not shut down"


def client_env(home: Path, token: str | None = None) -> dict:
    """A hermetic environment for the *client* git: never the developer's own
    `~/.gitconfig` (an `insteadOf`, a credential helper or a signing key there
    would decide what these tests measure)."""
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "xdg"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
    }
    if token is not None:
        env["AGENTCAD_TEST_TOKEN"] = token
    return env


def git(*args: str, cwd: Path, env: dict, check: bool = True,
        token: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git"]
    if token:
        cmd += ["-c", f"credential.helper={CREDENTIAL_HELPER}"]
    result = subprocess.run([*cmd, *args], cwd=str(cwd), env=env,
                            capture_output=True, text=True, timeout=120)
    if check and result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}")
    return result


def seed(service, name: str = "demo") -> Path:
    """A project with history: two commits, a branch, an annotated tag, and
    an untracked `.cache/` (the derived data FR8 says must never sync)."""
    path = Path(service.store.create(name))
    (path / "parts").mkdir(exist_ok=True)
    (path / "parts" / "a.py").write_text("# part a\n", encoding="utf-8")
    history = ProjectHistory()
    assert history.snapshot(path, "seed") is not None
    (path / "parts" / "b.py").write_text("# part b\n", encoding="utf-8")
    assert history.snapshot(path, "add b") is not None
    sync_server._run(path, "branch", "feature")
    sync_server._run(path, "tag", "-a", "v1.0", "-m", "release 1.0")
    (path / ".cache").mkdir(exist_ok=True)
    (path / ".cache" / "mesh.bin").write_bytes(b"derived")
    return path


def url_for(base: str, proj: str = "demo", org: str = "acme",
            ws: str = "hardware") -> str:
    return f"{base}/git/{org}/{ws}/{proj}.git"


def _basic(token: str, user: str = "agentcad") -> str:
    raw = f"{user}:{token}".encode()
    return "Basic " + base64.b64encode(raw).decode()


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """Never the developer's real `~/.agentcad/config.json` (the hosted
    fixture's reason, and `build_registry` is what reads it)."""
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))


@pytest.fixture(autouse=True)
def _seams_are_clean():
    """The seams are module attributes; a test that sets one must not leak it
    into the next (they are what the integration slice wires, and a leaked
    fake would make an unrelated failure unreadable)."""
    yield
    routes_sync.require_role = None
    routes_sync.resolve_project = None
    routes_sync.on_materialize = None


@pytest.fixture
def local(kernel, tmp_path):
    """`(base_url, project_path, service)` for a local-mode app, served."""
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    app = create_app(service, build_registry(service))
    with serve(app) as base:
        yield base, project, service


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


# ------------------------------------------------------------------ clone

def test_a_clone_carries_commits_branches_and_tags(local, home, tmp_path):
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"

    assert (clone / "parts" / "a.py").read_text() == "# part a\n"
    assert (clone / "parts" / "b.py").is_file()
    assert (clone / "project.json").is_file()
    # FR8: derived data never syncs. `.cache/` is in `info/exclude`, so it was
    # never tracked and cannot travel.
    assert not (clone / ".cache").exists()

    assert "v1.0" in git("tag", cwd=clone, env=env).stdout.split()
    assert "origin/feature" in git("branch", "-r", cwd=clone,
                                   env=env).stdout.replace(" ", "")

    # And the modern wire protocol, which the CGI child negotiates for free —
    # the direct `--stateless-rpc` path silently downgraded to v0 (spike §A7).
    trace = subprocess.run(
        ["git", "-c", "protocol.version=2", "ls-remote", url_for(base)],
        cwd=str(tmp_path), env={**env, "GIT_TRACE_PACKET": "1"},
        capture_output=True, text=True, timeout=120)
    assert trace.returncode == 0, trace.stderr
    assert "version 2" in trace.stderr


def test_only_the_three_smart_endpoints_are_served(local, home, tmp_path):
    """Everything else under the GIT_DIR is unreachable by construction —
    including `.history/agentcad/`, the review-thread store."""
    import httpx

    base, _project, _service = local
    for path in ("/HEAD", "/config", "/hooks/pre-receive",
                 "/agentcad/comments/index.json", "/objects/info/packs"):
        response = httpx.get(url_for(base) + path, timeout=30)
        assert response.status_code == 404, path
    # The advertisement itself refuses anything but the two smart services.
    assert httpx.get(url_for(base) + "/info/refs",
                     timeout=30).status_code == 422
    assert httpx.get(url_for(base) + "/info/refs?service=git-daemon",
                     timeout=30).status_code == 422


# ------------------------------------------------------------------- push

def test_a_push_lands_and_the_work_tree_is_materialized(local, home, tmp_path):
    base, project, _service = local
    env = client_env(home)
    results: list[dict] = []
    routes_sync.on_materialize = results.append

    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"
    (clone / "parts" / "c.py").write_text("# part c\n", encoding="utf-8")
    git("add", "-A", cwd=clone, env=env)
    git("commit", "-m", "add c", cwd=clone, env=env)
    git("push", "origin", "HEAD", cwd=clone, env=env)

    # The refs advanced AND the work tree caught up: with
    # `receive.denyCurrentBranch=ignore` the second half is ours to do.
    assert (project / "parts" / "c.py").read_text() == "# part c\n"
    # ...and `checkout -f` is not `clean -fdx`: untracked derived data lives.
    assert (project / ".cache" / "mesh.bin").read_bytes() == b"derived"

    assert results and results[-1]["materialized"] is True
    assert results[-1]["changed"] == 1
    assert results[-1]["project"] == "demo"


def test_new_branches_and_new_tags_are_accepted(local, home, tmp_path):
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"

    git("checkout", "-b", "wip", cwd=clone, env=env)
    (clone / "parts" / "d.py").write_text("# part d\n", encoding="utf-8")
    git("add", "-A", cwd=clone, env=env)
    git("commit", "-m", "add d", cwd=clone, env=env)
    git("tag", "-a", "v2.0", "-m", "release 2.0", cwd=clone, env=env)
    # Explicit refspecs, no `+`: what `agentcad push` will send (`--follow-tags`
    # carries annotated tags only, and lightweight ones would vanish).
    git("push", "origin", "refs/heads/*:refs/heads/*",
        "refs/tags/*:refs/tags/*", cwd=clone, env=env)

    refs = sync_server._run(project, "show-ref").stdout
    assert "refs/heads/wip" in refs
    assert "refs/tags/v2.0" in refs
    # HEAD is still master, so materialization left the default branch alone.
    assert not (project / "parts" / "d.py").exists()


def test_a_three_megabyte_push_streams(local, home, tmp_path):
    """The body arrives chunked with no `Content-Length` (measured in the
    spike). Nothing may buffer it — and the proof that nothing does is that a
    3 MB pack arrives intact with `CONTENT_LENGTH` never set for the child."""
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"

    blob = os.urandom(3 * 1024 * 1024)      # incompressible: the pack is 3 MB
    (clone / "imports").mkdir(exist_ok=True)
    (clone / "imports" / "big.bin").write_bytes(blob)
    git("add", "-A", cwd=clone, env=env)
    git("commit", "-m", "add a big import", cwd=clone, env=env)
    git("push", "origin", "HEAD", cwd=clone, env=env)

    landed = project / "imports" / "big.bin"
    assert landed.is_file() and landed.read_bytes() == blob


# ------------------------------------------------- FR9: the pre-receive hook

def _two_clones(base, tmp_path, env) -> tuple[Path, Path]:
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    git("clone", url_for(base), str(tmp_path / "b"), cwd=tmp_path, env=env)
    return tmp_path / "a", tmp_path / "b"


def _commit(clone: Path, env: dict, name: str, text: str) -> str:
    (clone / "parts" / name).write_text(text, encoding="utf-8")
    git("add", "-A", cwd=clone, env=env)
    git("commit", "-m", f"add {name}", cwd=clone, env=env)
    return git("rev-parse", "HEAD", cwd=clone, env=env).stdout.strip()


def test_a_divergent_push_is_refused_and_the_server_is_unchanged(
        local, home, tmp_path):
    """FR9's ordinary path: B pushes, A diverges, A's plain push is refused
    (by the client, against the advertisement it just read) and the recovery
    is the ordinary pull-and-merge."""
    base, project, _service = local
    env = client_env(home)
    a, b = _two_clones(base, tmp_path, env)

    _commit(b, env, "b1.py", "# b1\n")
    git("push", "origin", "HEAD", cwd=b, env=env)
    server_head = sync_server._run(project, "rev-parse", "HEAD").stdout.strip()

    _commit(a, env, "a1.py", "# a1\n")
    refused = git("push", "origin", "HEAD", cwd=a, env=env, check=False)
    assert refused.returncode != 0
    assert "rejected" in refused.stderr
    assert sync_server._run(project, "rev-parse",
                            "HEAD").stdout.strip() == server_head

    git("pull", "--no-rebase", "-X", "ours", "origin", "master",
        cwd=a, env=env)
    git("push", "origin", "HEAD", cwd=a, env=env)
    assert (project / "parts" / "a1.py").is_file()
    assert (project / "parts" / "b1.py").is_file()


def test_a_forced_push_is_refused_with_the_humane_message(local, home,
                                                          tmp_path):
    """The hole `receive.denyNonFastForwards` alone would leave open when the
    client says `--force`: the hook is what refuses, and its message is what
    the person sees."""
    base, project, _service = local
    env = client_env(home)
    a, b = _two_clones(base, tmp_path, env)

    _commit(b, env, "b1.py", "# b1\n")
    git("push", "origin", "HEAD", cwd=b, env=env)
    server_head = sync_server._run(project, "rev-parse", "HEAD").stdout.strip()

    _commit(a, env, "a1.py", "# a1\n")
    refused = git("push", "--force", "origin", "HEAD", cwd=a, env=env,
                  check=False)
    assert refused.returncode != 0
    assert "pull and merge, never force" in refused.stderr
    assert "refs/heads/master diverged" in refused.stderr
    assert sync_server._run(project, "rev-parse",
                            "HEAD").stdout.strip() == server_head
    # And nothing landed: `pre-receive` is all-or-nothing across the push.
    assert not (project / "parts" / "a1.py").exists()


def test_a_tag_rewrite_is_refused(local, home, tmp_path):
    """PRD-015 ships release tags. `denyNonFastForwards` is `refs/heads/`-only,
    so without the hook this succeeds."""
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"
    before = sync_server._run(project, "rev-parse", "v1.0^{}").stdout.strip()

    _commit(clone, env, "x.py", "# x\n")
    git("tag", "-f", "-a", "v1.0", "-m", "moved", cwd=clone, env=env)
    refused = git("push", "--force", "origin", "refs/tags/v1.0:refs/tags/v1.0",
                  cwd=clone, env=env, check=False)
    assert refused.returncode != 0
    assert "tags are immutable" in refused.stderr
    assert sync_server._run(project, "rev-parse",
                            "v1.0^{}").stdout.strip() == before


def test_deleting_a_tag_or_a_branch_is_refused(local, home, tmp_path):
    """Both knobs (`denyDeletes`, `denyNonFastForwards`) are branch-only; the
    hook covers tags too, and says so in one voice."""
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"

    tag = git("push", "origin", ":refs/tags/v1.0", cwd=clone, env=env,
              check=False)
    assert tag.returncode != 0
    assert "deletes are refused on the hosted copy" in tag.stderr
    assert "refs/tags/v1.0" in sync_server._run(project, "show-ref").stdout

    branch = git("push", "origin", ":refs/heads/feature", cwd=clone, env=env,
                 check=False)
    assert branch.returncode != 0
    assert "deletes are refused on the hosted copy" in branch.stderr
    assert "refs/heads/feature" in sync_server._run(project, "show-ref").stdout


# ----------------------------------------------------------------- hosted

@pytest.fixture
def hosted_sync(kernel, tmp_path, monkeypatch):
    """A hosted app on a known port (the origin has to name the port before
    uvicorn binds it, because the guard compares `Host` against it)."""
    from agentcad.core.appmode import AppMode
    from agentcad.core.authstore import AuthStore
    from agentcad.server.security import SecurityConfig

    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    store = AuthStore(tmp_path / "auth")
    store.enrol(store.add_user("nikita", role="admin"), "correct horse battery")
    token = store.add_token("ci", role="admin")
    cfg = SecurityConfig(mode=AppMode("hosted", origin, b"k" * 32), store=store)
    security_module.install(cfg)
    app = create_app(service, build_registry(service), security=cfg)
    try:
        with serve(app, port=port) as base:
            yield base, project, token, store
    finally:
        security_module.install(None)


def test_hosted_refuses_the_anonymous_clone_and_challenges_for_basic(
        hosted_sync, home, tmp_path):
    import httpx

    base, _project, _token, _store = hosted_sync
    env = client_env(home)

    anonymous = httpx.get(url_for(base) + "/info/refs?service=git-upload-pack",
                          timeout=30)
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["type"] == "AuthError"
    # Without the challenge a git client never offers its credential helper's
    # answer — it asks for a username on a terminal that is not there.
    assert anonymous.headers["WWW-Authenticate"].startswith("Basic ")

    refused = git("clone", url_for(base), str(tmp_path / "anon"), cwd=tmp_path,
                  env=env, check=False)
    assert refused.returncode != 0

    wrong = httpx.get(
        url_for(base) + "/info/refs?service=git-upload-pack",
        headers={"Authorization": _basic("acad_nope_nope")}, timeout=30)
    assert wrong.status_code == 401


def test_hosted_basic_with_a_token_clones_and_pushes(hosted_sync, home,
                                                     tmp_path):
    """git speaks Basic; the username is ignored and the password is a bearer
    token, which is exactly what the `agentcad credential` helper will send."""
    base, project, token, _store = hosted_sync
    env = client_env(home, token=token)

    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env,
        token=True)
    clone = tmp_path / "a"
    assert (clone / "parts" / "a.py").is_file()

    _commit(clone, env, "hosted.py", "# hosted\n")
    git("push", "origin", "HEAD", cwd=clone, env=env, token=True)
    assert (project / "parts" / "hosted.py").is_file()

    # The token never reached the clone's config or any file in it.
    recorded = git("config", "--get", "remote.origin.url", cwd=clone,
                   env=env).stdout.strip()
    assert recorded == url_for(base) and token not in recorded


def test_a_revoked_token_stops_working_on_the_next_request(hosted_sync, home,
                                                           tmp_path):
    base, _project, token, store = hosted_sync
    env = client_env(home, token=token)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env,
        token=True)

    store.revoke_token(store.list_tokens()[-1]["id"])
    refused = git("fetch", "origin", cwd=tmp_path / "a", env=env, check=False,
                  token=True)
    assert refused.returncode != 0


# ------------------------------------------------------------------ seams

def test_the_role_floor_seam_is_consulted_for_both_verbs(local, home,
                                                         tmp_path):
    base, _project, _service = local
    env = client_env(home)
    calls: list[tuple] = []

    def require(role, org, ws, proj):
        calls.append((role, org, ws, proj))
        if role == "edit":
            raise AuthzError("read-only here", {"required_role": role})

    routes_sync.require_role = require
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"
    _commit(clone, env, "z.py", "# z\n")
    refused = git("push", "origin", "HEAD", cwd=clone, env=env, check=False)

    assert refused.returncode != 0
    assert "403" in refused.stderr
    assert ("view", "acme", "hardware", "demo") in calls
    # The receive-pack ADVERTISEMENT answers to `edit`, not `view`: a push
    # that only failed at the RPC would have already streamed a pack.
    assert ("edit", "acme", "hardware", "demo") in calls


def test_the_project_resolver_seam_decides_which_directory_is_served(
        local, home, tmp_path, kernel):
    """While `resolve_project` is None the org/workspace are validated and
    ignored; wired, they choose the directory."""
    base, _project, service = local
    other = seed(service, "other")
    (other / "parts" / "only_in_other.py").write_text("# other\n",
                                                      encoding="utf-8")
    ProjectHistory().snapshot(other, "other")

    seen: list[tuple] = []

    def resolve(org, ws, proj):
        seen.append((org, ws, proj))
        return other

    routes_sync.resolve_project = resolve
    env = client_env(home)
    git("clone", url_for(base, proj="demo", org="tenant", ws="ws"),
        str(tmp_path / "a"), cwd=tmp_path, env=env)
    assert (tmp_path / "a" / "parts" / "only_in_other.py").is_file()
    assert seen[0] == ("tenant", "ws", "demo")


def test_an_unknown_project_and_a_hostile_segment_are_the_same_404(local,
                                                                  tmp_path):
    import httpx

    base, _project, _service = local
    for url in (url_for(base, proj="nosuch"),
                url_for(base, proj="demo", org="..").replace("/..", "/%2e%2e"),
                url_for(base, proj="Demo"),
                url_for(base, proj="demo", ws="a/b")):
        response = httpx.get(url + "/info/refs?service=git-upload-pack",
                             timeout=30, follow_redirects=False)
        assert response.status_code == 404, url
        assert "nosuch" not in response.text


# ------------------------------------------------- unit: prepare + hook file

def test_prepare_repo_is_idempotent_and_versioned(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)

    first = sync_server.prepare_repo(project)
    assert first["hook"] == "installed"
    assert set(first["config"]) == {name for name, _ in
                                    sync_server.REPO_CONFIG}
    assert sync_server.prepare_repo(project) == {"config": [],
                                                 "hook": "current"}

    hook = project / ".history" / "hooks" / "pre-receive"
    assert os.access(hook, os.X_OK)
    assert sync_server.HOOK_MARKER in hook.read_text()

    # An older (or hand-edited) hook is REWRITTEN, not trusted: the rules are
    # the server's, and a stale one is a silently weaker instance.
    hook.write_text("#!/bin/sh\n# agentcad pre-receive hook v0\nexit 0\n")
    assert sync_server.prepare_repo(project)["hook"] == "installed"
    assert sync_server.HOOK_MARKER in hook.read_text()

    # `git config --list` answers in ITS spelling, not ours (variable names
    # are case-insensitive and come back lower-cased) — which is why
    # `prepare_repo` compares that way and does not rewrite on every request.
    config = sync_server._config_map(project)
    assert config["receive.denycurrentbranch"] == "ignore"
    assert config["http.receivepack"] == "true"
    assert sync_server.prepare_repo(project, force=True)["config"] == []


def test_prepare_repo_refuses_a_project_with_no_history(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(sync_server.SyncError):
        sync_server.prepare_repo(tmp_path / "empty")


@pytest.mark.portability
def test_the_backend_is_present_on_this_machine():
    """A missing `git-http-backend` would present as 'sync just 500s'. It
    ships with git on macOS, Debian and git-for-windows; probe it out loud."""
    report = sync_server.probe()
    assert "error" not in report, report
    assert Path(report["http_backend"]).is_file()


# ------------------------------------------------- unit: materialization

def test_materialize_reports_the_branch_and_the_changed_count(kernel,
                                                              tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    # Simulate what a push leaves behind: the ref moved, the work tree did not.
    (project / "parts" / "c.py").write_text("# c\n", encoding="utf-8")
    ProjectHistory().snapshot(project, "add c")
    (project / "parts" / "c.py").unlink()

    result = sync_server.materialize(project)
    assert result == {"materialized": True, "reason": None,
                      "branch": "master", "changed": 1}
    assert (project / "parts" / "c.py").read_text() == "# c\n"
    assert sync_server.materialize(project)["changed"] == 0


def test_materialize_refuses_to_clobber_uncommitted_edits(kernel, tmp_path):
    """`checkout -f` is not `clean -fdx` — but it DOES eat uncommitted tracked
    edits, so a dirty tree captured before the push skips the checkout."""
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    (project / "parts" / "a.py").write_text("# live edit\n", encoding="utf-8")

    dirty = sync_server.pending_edits(project)
    assert dirty == ["parts/a.py"]

    result = sync_server.materialize(project, dirty=dirty)
    assert result["materialized"] is False
    assert result["reason"] == "uncommitted_edits"
    assert (project / "parts" / "a.py").read_text() == "# live edit\n"

    forced = sync_server.materialize(project, dirty=dirty, force=True)
    assert forced["materialized"] is True
    assert (project / "parts" / "a.py").read_text() == "# part a\n"


def test_materialize_runs_inside_the_projects_write_scope(kernel, tmp_path):
    """The write context is the project's turn lock: a push that lands while
    another client holds the turn updates the refs and leaves the tree alone,
    rather than clobbering the session that is holding it."""
    from agentcad.core import locks
    from agentcad.core.model import ConflictError

    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    (project / "parts" / "c.py").write_text("# c\n", encoding="utf-8")
    ProjectHistory().snapshot(project, "add c")
    (project / "parts" / "c.py").unlink()

    service.turnlock.acquire("demo", "user:someone_else")
    locks.set_client_id("agent:sync")
    with pytest.raises(ConflictError):
        sync_server.materialize(
            project,
            lambda: sync_server.project_write_scope(service, "demo", project))
    assert not (project / "parts" / "c.py").exists()

    service.turnlock.release("demo", "user:someone_else")
    sync_server.materialize(
        project,
        lambda: sync_server.project_write_scope(service, "demo", project))
    assert (project / "parts" / "c.py").is_file()


def test_a_push_that_cannot_materialize_still_lands(local, home, tmp_path):
    """The bytes are on the server and git has already said so; a failed
    checkout must not become an exception in a response that is already 200."""
    base, project, service = local
    env = client_env(home)
    results: list[dict] = []
    routes_sync.on_materialize = results.append

    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"
    _commit(clone, env, "c.py", "# c\n")
    service.turnlock.acquire("demo", "user:someone_else")
    try:
        git("push", "origin", "HEAD", cwd=clone, env=env)
    finally:
        service.turnlock.release("demo", "user:someone_else")

    assert sync_server._run(project, "cat-file", "-e",
                            "HEAD:parts/c.py", check=False).returncode == 0
    assert not (project / "parts" / "c.py").exists()
    assert results[-1]["materialized"] is False
    assert results[-1]["reason"] == "ConflictError"


# --------------------------------------------------------- unit: the wrapper

def test_installing_the_basic_auth_wrapper_is_idempotent():
    class Fake:
        def guard(self, *_args):
            return None

    module = Fake()
    original = module.guard
    routes_sync.install_git_auth(module)
    wrapped = module.guard
    assert wrapped is not original
    routes_sync.install_git_auth(module)
    assert module.guard is wrapped


def test_basic_credentials_are_understood_on_git_paths_and_nowhere_else(
        hosted_sync):
    """The wrapper must not widen how the rest of the product authenticates.
    The same token, in the same header, opens the git route and is refused by
    every other one — where `Bearer` is still the only spelling."""
    import httpx

    base, _project, token, _store = hosted_sync

    assert routes_sync._is_sync_path("/git/a/b/c.git/info/refs")
    assert not routes_sync._is_sync_path("/api/projects")
    assert not routes_sync._is_sync_path("/gitlab")     # the trailing slash

    git_route = httpx.get(url_for(base) + "/info/refs?service=git-upload-pack",
                          headers={"Authorization": _basic(token)}, timeout=30)
    assert git_route.status_code == 200

    api = httpx.get(f"{base}/api/projects",
                    headers={"Authorization": _basic(token)}, timeout=30)
    assert api.status_code == 401
    assert httpx.get(f"{base}/api/projects",
                     headers={"Authorization": f"Bearer {token}"},
                     timeout=30).status_code == 200


def test_an_oversized_push_is_refused_before_git_sees_it(local, home,
                                                         tmp_path,
                                                         monkeypatch):
    """The cap is what keeps an unbounded body from being an unbounded temp
    file. It is checked while spooling, so the refusal costs no git work."""
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"
    _commit(clone, env, "big.py", "# " + "x" * 4096 + "\n")

    monkeypatch.setattr(routes_sync, "MAX_BODY_BYTES", 512)
    refused = git("push", "origin", "HEAD", cwd=clone, env=env, check=False)
    assert refused.returncode != 0
    assert "422" in refused.stderr
    assert not (project / "parts" / "big.py").exists()
