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


def _fs_is_case_insensitive(root: Path) -> bool:
    """Does *root*'s filesystem fold case? (macOS APFS/HFS+ and Windows NTFS
    do — the machines the case-fold bypass actually bites.)"""
    probe = root / "AgentCadCaseProbe"
    probe.mkdir()
    try:
        return (root / "agentcadcaseprobe").exists()
    finally:
        probe.rmdir()


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


# ------------------------- FR: git internals never travel in a pushed tree

def _plant(clone: Path, env: dict, files: dict[str, str]) -> str:
    """Write *files* (relative paths) into *clone*, commit them, return HEAD.

    Used to plant `.history/**` paths — a plain `git clone` (not the AgentCAD
    layout) has its GIT_DIR at `.git`, so `.history` is an ordinary tracked
    directory here and `git add` will happily stage it."""
    for rel, text in files.items():
        target = clone / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git("add", "-A", cwd=clone, env=env)
    git("commit", "-m", "plant", cwd=clone, env=env)
    return git("rev-parse", "HEAD", cwd=clone, env=env).stdout.strip()


def test_a_push_writing_into_history_is_refused(local, home, tmp_path):
    """The RCE: a pushed tree carrying `.history/hooks/post-receive` /
    `.history/config` would be written into the served repo's live GIT_DIR by
    `checkout -f` and run as the server user. The pre-receive rule refuses it,
    all-or-nothing, and nothing lands."""
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"
    server_head = sync_server._run(project, "rev-parse", "HEAD").stdout.strip()

    _plant(clone, env, {
        ".history/hooks/post-receive": "#!/bin/sh\ntouch /tmp/PWNED\n",
        ".history/config": "[core]\n\thooksPath = /tmp/attacker\n",
        "parts/legit.py": "# legit\n",
    })
    refused = git("push", "origin", "HEAD", cwd=clone, env=env, check=False)

    assert refused.returncode != 0
    assert "writes into .history/" in refused.stderr
    # The whole push is one ref transaction: the server head never moved and
    # the legit file in the same push did not land either.
    assert sync_server._run(project, "rev-parse",
                            "HEAD").stdout.strip() == server_head
    assert not (project / "parts" / "legit.py").exists()
    # The attacker's files were never written into the live internals, and the
    # MANAGED hook (in the server-owned dir) is untouched.
    assert not (project / ".history" / "hooks" / "post-receive").is_file()
    managed = sync_server.hooks_dir(project / ".history") / "pre-receive"
    assert sync_server.HOOK_MARKER in managed.read_text()


def test_a_merge_that_only_introduces_history_is_refused(local, home, tmp_path):
    """A file present in a MERGE result but in neither parent is invisible to a
    plain `git log --name-only`; the rule uses a combined diff (`-c`) so a
    merge cannot smuggle a `.history/**` path past it."""
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"

    git("checkout", "-b", "l", cwd=clone, env=env)
    _plant(clone, env, {"parts/l.py": "# l\n"})
    git("checkout", "master", cwd=clone, env=env)
    git("checkout", "-b", "r", cwd=clone, env=env)
    _plant(clone, env, {"parts/r.py": "# r\n"})
    git("checkout", "l", cwd=clone, env=env)
    git("merge", "--no-ff", "--no-commit", "r", cwd=clone, env=env, check=False)
    (clone / ".history").mkdir(exist_ok=True)
    (clone / ".history" / "config").write_text("evil\n", encoding="utf-8")
    git("add", "-A", cwd=clone, env=env)
    git("commit", "-m", "merge that adds .history", cwd=clone, env=env)

    refused = git("push", "origin", "refs/heads/l:refs/heads/l", cwd=clone,
                  env=env, check=False)
    assert refused.returncode != 0
    assert "writes into .history/" in refused.stderr
    assert "refs/heads/l" not in sync_server._run(project, "show-ref").stdout


def test_a_nested_dot_git_path_is_refused(local, home, tmp_path):
    """`git add` blocks a `.git` component, but a crafted tree does not.
    Build one with plumbing and confirm the rule catches `.git` at any depth."""
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"

    def g(*args, inp=None):
        return git(*args, cwd=clone, env=env) if inp is None else \
            subprocess.run(["git", *args], cwd=str(clone), env=env, input=inp,
                           capture_output=True, text=True, timeout=60)
    blob = g("hash-object", "-w", "--stdin", inp="evil\n").stdout.strip()
    inner = g("mktree", inp=f"100644 blob {blob}\tconfig\n").stdout.strip()
    base_tree = g("ls-tree", "HEAD").stdout
    root = g("mktree", inp=base_tree + f"040000 tree {inner}\t.git\n").stdout.strip()
    head = git("rev-parse", "HEAD", cwd=clone, env=env).stdout.strip()
    commit = g("commit-tree", root, "-p", head, inp="crafted\n").stdout.strip()
    git("update-ref", "refs/heads/craft", commit, cwd=clone, env=env)

    refused = git("push", "origin", "refs/heads/craft:refs/heads/craft",
                  cwd=clone, env=env, check=False)
    assert refused.returncode != 0
    assert "writes into .history/" in refused.stderr
    assert "refs/heads/craft" not in sync_server._run(project,
                                                      "show-ref").stdout


def test_a_case_folded_history_push_is_refused(local, home, tmp_path):
    """The PRD-005 re-check bypass, end to end against real git: a pushed tree
    spelling `.History/config` + `.History/hooks/post-checkout` passed the old
    case-SENSITIVE rule, then folded onto the live `.history` GIT_DIR on this
    (case-insensitive) filesystem at checkout. Built with plumbing so `.History`
    lands in a committed tree, and pushed on its own branch."""
    assert _fs_is_case_insensitive(tmp_path), (
        "this bypass only bites a case-insensitive fs; the test is a no-op "
        "proof otherwise — run it where the attack is real")
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"

    def g(*args, inp=None):
        return git(*args, cwd=clone, env=env) if inp is None else \
            subprocess.run(["git", *args], cwd=str(clone), env=env, input=inp,
                           capture_output=True, text=True, timeout=60)
    cfg = g("hash-object", "-w", "--stdin",
            inp='[core]\n\tfsmonitor = "touch /tmp/PWNED"\n').stdout.strip()
    hook = g("hash-object", "-w", "--stdin",
             inp="#!/bin/sh\ntouch /tmp/PWNED\n").stdout.strip()
    inner = g("mktree",
              inp=f"100755 blob {hook}\tpost-checkout\n").stdout.strip()
    upper = g("mktree", inp=f"100644 blob {cfg}\tconfig\n"
                            f"040000 tree {inner}\thooks\n").stdout.strip()
    base_tree = g("ls-tree", "HEAD").stdout
    # `.History` — the exact spelling that the case-sensitive belt missed.
    root = g("mktree",
             inp=base_tree + f"040000 tree {upper}\t.History\n").stdout.strip()
    head = git("rev-parse", "HEAD", cwd=clone, env=env).stdout.strip()
    poison = g("commit-tree", root, "-p", head, inp="casefold\n").stdout.strip()
    git("update-ref", "refs/heads/fold", poison, cwd=clone, env=env)

    refused = git("push", "origin", "refs/heads/fold:refs/heads/fold",
                  cwd=clone, env=env, check=False)
    assert refused.returncode != 0
    assert "writes into .history/" in refused.stderr
    # The ref never advanced on the server, so materialize is never even asked.
    assert "refs/heads/fold" not in sync_server._run(project,
                                                     "show-ref").stdout


def test_materialize_refuses_a_case_folded_history_tree(kernel, tmp_path):
    """Belt behind the hook, on THIS filesystem: even if a `.History` commit is
    HEAD (a repo that predates v3, or was populated out of band), the folded
    `tree_git_internals` predicate refuses the `checkout -f`, so the variant
    never reaches the live GIT_DIR to fold onto `.history`."""
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    git_dir = str(project / ".history")

    def g(*args, inp=None):
        return subprocess.run(
            ["git", "--git-dir", git_dir, "--work-tree", str(project), *args],
            input=inp, capture_output=True, text=True, timeout=60,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": git_dir,
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    blob = g("hash-object", "-w", "--stdin", inp="evil\n").stdout.strip()
    upper = g("mktree", inp=f"100644 blob {blob}\tconfig\n").stdout.strip()
    base_tree = g("ls-tree", "HEAD").stdout
    root = g("mktree",
             inp=base_tree + f"040000 tree {upper}\t.HISTORY\n").stdout.strip()
    head = g("rev-parse", "HEAD").stdout.strip()
    commit = g("commit-tree", root, "-p", head, inp="poison\n").stdout.strip()
    g("update-ref", "refs/heads/master", commit)

    result = sync_server.materialize(project)
    assert result["materialized"] is False
    assert result["reason"] == "git_internals_in_tree"
    assert ".HISTORY/config" in result["offending"]
    # Nothing folded onto the live GIT_DIR: `.history/config` is git's, untouched.
    assert "evil" not in (project / ".history" / "config").read_text()


def test_the_managed_hook_and_its_sidecar_are_installed_and_versioned(
        local, home, tmp_path):
    """The hook delegates its NTFS/HFS folds to a `checkpaths.py` sidecar; both
    are installed in the server-owned dir, the hook is v3, and the sidecar is
    the module's script verbatim (so the fold the hook runs IS the fold the
    server runs)."""
    _base, project, _service = local
    sync_server.prepare_repo(project)
    hooks = sync_server.hooks_dir(project / ".history")
    assert (hooks / "pre-receive").read_text() == sync_server.PRE_RECEIVE_HOOK
    assert sync_server.HOOK_MARKER in (hooks / "pre-receive").read_text()
    assert "v3" in sync_server.HOOK_MARKER
    sidecar = hooks / sync_server.CHECKPATHS_NAME
    assert sidecar.read_text() == sync_server.CHECKPATHS_SCRIPT


def test_a_stale_hook_and_sidecar_are_rewritten(local, home, tmp_path):
    """A hand-edited (or downgraded) hook/sidecar is replaced, not trusted — the
    security fix has to actually reach a repo that a prior version prepared."""
    _base, project, _service = local
    sync_server.prepare_repo(project)
    hooks = sync_server.hooks_dir(project / ".history")
    (hooks / "pre-receive").write_text("#!/bin/sh\nexit 0\n")
    (hooks / sync_server.CHECKPATHS_NAME).write_text("# stale\n")

    result = sync_server.prepare_repo(project, force=True)

    assert result["hook"] == "installed"
    assert (hooks / "pre-receive").read_text() == sync_server.PRE_RECEIVE_HOOK
    assert (hooks / sync_server.CHECKPATHS_NAME).read_text() \
        == sync_server.CHECKPATHS_SCRIPT


def test_an_unmanaged_hook_in_history_never_runs(local, home, tmp_path):
    """`core.hooksPath` points at a server-owned dir holding only the managed
    `pre-receive`. An unmanaged `post-receive` sitting in `.history/hooks/`
    (planted out of band, or that predates the hardening) is never consulted —
    the marker it would touch stays absent across a perfectly good push."""
    base, project, _service = local
    env = client_env(home)
    git("clone", url_for(base), str(tmp_path / "a"), cwd=tmp_path, env=env)
    clone = tmp_path / "a"

    marker = tmp_path / "pwned"
    unmanaged = project / ".history" / "hooks" / "post-receive"
    unmanaged.parent.mkdir(parents=True, exist_ok=True)
    unmanaged.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    os.chmod(unmanaged, 0o755)

    _commit(clone, env, "ok.py", "# ok\n")
    git("push", "origin", "HEAD", cwd=clone, env=env)

    assert (project / "parts" / "ok.py").is_file()      # the push landed
    assert not marker.exists()                          # ...and no hook ran


def test_materialize_refuses_a_tree_carrying_git_internals(kernel, tmp_path):
    """Belt behind the pre-receive rule: even if a poisoned commit is HEAD (a
    repo that predates the hook, or was populated out of band), `materialize`
    refuses to `checkout -f` it, so the bytes never reach the live GIT_DIR."""
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    git_dir = str(project / ".history")

    def g(*args, inp=None):
        return subprocess.run(
            ["git", "--git-dir", git_dir, "--work-tree", str(project), *args],
            input=inp, capture_output=True, text=True, timeout=60,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": git_dir,
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    blob = g("hash-object", "-w", "--stdin", inp="evil\n").stdout.strip()
    inner = g("mktree", inp=f"100644 blob {blob}\tpost-receive\n").stdout.strip()
    hooks = g("mktree", inp=f"040000 tree {inner}\thooks\n").stdout.strip()
    base_tree = g("ls-tree", "HEAD").stdout
    root = g("mktree",
             inp=base_tree + f"040000 tree {hooks}\t.history\n").stdout.strip()
    head = g("rev-parse", "HEAD").stdout.strip()
    commit = g("commit-tree", root, "-p", head, inp="poison\n").stdout.strip()
    g("update-ref", "refs/heads/master", commit)

    result = sync_server.materialize(project)
    assert result["materialized"] is False
    assert result["reason"] == "git_internals_in_tree"
    assert ".history/hooks/post-receive" in result["offending"]
    # Nothing was written into the live internals.
    assert not (project / ".history" / "hooks" / "post-receive").is_file()


def test_history_exec_never_fires_a_repo_local_poison(kernel, tmp_path):
    """Defense in depth (PRD-005 re-check step 5): even if a poison DID land in
    the live GIT_DIR, AgentCAD's own history engine must not execute it. The
    engine's hermetic HOME/`GIT_CONFIG_NOSYSTEM` suppress the operator's GLOBAL
    config but NOT a repo-local `.history/config`, so `_exec` pins
    `core.fsmonitor=false` + `core.hooksPath=/dev/null` per call. Plant both a
    repo-local `core.fsmonitor` command AND a `post-checkout` hook and prove
    neither fires across real history operations."""
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    history = ProjectHistory()
    git_dir = project / ".history"
    marker = tmp_path / "PWNED"

    # A repo-local fsmonitor command — runs on almost every git call — and a
    # post-checkout hook, both writing the marker. This is exactly the state a
    # landed poison (or a future belt gap) would leave the GIT_DIR in.
    with (git_dir / "config").open("a", encoding="utf-8") as handle:
        handle.write(f'[core]\n\tfsmonitor = "touch {marker}"\n')
    hook = git_dir / "hooks" / "post-checkout"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    os.chmod(hook, 0o755)

    # Drive the engine the way the app does: a status, a snapshot, a checkout.
    history._run(project, "status", "--porcelain", check=False)
    (project / "parts" / "poke.py").write_text("# poke\n", encoding="utf-8")
    history.snapshot(project, "poke")
    history._run(project, "checkout", "-f", "HEAD", "--", check=False)

    assert not marker.exists(), (
        "history._exec executed a repo-local fsmonitor/hook — the per-call "
        "safety pins are not in force")


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
    # Everything in REPO_CONFIG, plus the server-owned `core.hooksPath`.
    assert set(first["config"]) == ({name for name, _ in sync_server.REPO_CONFIG}
                                    | {"core.hooksPath"})
    assert sync_server.prepare_repo(project) == {"config": [],
                                                 "hook": "current"}

    # The managed hook lives in the SERVER-OWNED dir, not `.history/hooks` —
    # so an unmanaged hook name a push could plant there is never consulted.
    hook = sync_server.hooks_dir(project / ".history") / "pre-receive"
    assert hook.parent == project / ".history" / "agentcad-hooks"
    assert os.access(hook, os.X_OK)
    assert sync_server.HOOK_MARKER in hook.read_text()
    # And `core.hooksPath` actually points at it (absolute).
    config = sync_server._config_map(project)
    assert config["core.hookspath"] == str(hook.parent.resolve())
    assert config["core.fsmonitor"] == "false"

    # An older (or hand-edited) hook is REWRITTEN, not trusted: the rules are
    # the server's, and a stale one is a silently weaker instance.
    hook.write_text("#!/bin/sh\n# agentcad pre-receive hook v0\nexit 0\n")
    assert sync_server.prepare_repo(project)["hook"] == "installed"
    assert sync_server.HOOK_MARKER in hook.read_text()

    # `git config --list` answers in ITS spelling, not ours (variable names
    # are case-insensitive and come back lower-cased) — which is why
    # `prepare_repo` compares that way and does not rewrite on every request.
    assert config["receive.denycurrentbranch"] == "ignore"
    assert config["http.receivepack"] == "true"
    assert sync_server.prepare_repo(project, force=True)["config"] == []


def test_prepare_repo_neutralizes_injected_dangerous_config(kernel, tmp_path):
    """A `filter.*` smudge command, a `core.hooksPath` redirect and a
    `core.fsmonitor` command are the shapes a checked-out `.history/config`
    would plant. `prepare_repo` resets/unsets them every time it runs, so the
    repo config a push might rewrite is never the authority."""
    service = make_test_service(tmp_path / "projects", kernel)
    project = seed(service)
    sync_server.prepare_repo(project)
    owned = str(sync_server.hooks_dir(project / ".history").resolve())

    # Simulate what a checkout of a malicious `.history/config` would leave.
    sync_server._run(project, "config", "filter.evil.smudge", "touch /tmp/x")
    sync_server._run(project, "config", "core.hooksPath", "/tmp/attacker")
    sync_server._run(project, "config", "core.fsmonitor", "/tmp/evilfsmonitor")
    sync_server._run(project, "config", "core.sshCommand", "/tmp/evilssh")

    written = sync_server.reconcile_repo(project)["config"]
    assert "filter.evil.smudge" in written
    config = sync_server._config_map(project)
    assert "filter.evil.smudge" not in config
    assert config["core.hookspath"] == owned          # reset to ours
    assert config["core.fsmonitor"] == "false"        # reset to false
    assert "core.sshcommand" not in config            # unset


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
