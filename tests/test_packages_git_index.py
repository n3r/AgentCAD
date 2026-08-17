"""PRD-011 slice 9 — a repo is an index.

**AC4 is won here**, and the slice is small precisely because slice 3 made a
local index the general case: a git index is `~/.agentcad/indexes/<name>/` at a
pinned ref plus a fetch, and everything after the fetch **is** `LocalIndex`.

Three claims, each tested against its negation:

* **The runner is not `history._run`, deliberately.** Fixed argv and never a
  shell, a 120 s timeout (not 10 s), `GIT_TERMINAL_PROMPT=0`, **`HOME` not
  redirected** (a private index is the case that needs the user's credential
  helper), and a URL that is validated — one starting with `-` or carrying a
  shell metacharacter is refused **before any subprocess runs**.
* **Failure is data.** An unreachable remote is a *warning*: the last good
  checkout keeps answering and every result carries `stale: true` with the
  reason. A never-cloned index is a `not_found_error` naming the URL. No git
  on PATH means git indexes register nothing and say so — the versioning and
  proposals self-disable precedent.
* **AC4:** delete the remote entirely and `use_part` still works from the
  cache, while `add_package` of the already-cached package still succeeds
  offline and writes a **byte-identical** lock entry.

Every test here is hermetic: the "remote" is a bare repository in `tmp_path`
reached over `file://`. Nothing needs a network, so nothing is skipped for
lacking one — only for lacking `git`.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.packages import _git, cache, content, indexes
from agentcad.core.packages.manager import PackageManager
from agentcad.core.tools import build_registry
from .conftest import make_test_service


def _rmtree_repo(path) -> None:
    """Delete a git directory on any OS (the `test_checks_ref` idiom).

    Git marks everything under ``objects/`` read-only, and Windows refuses to
    unlink a read-only file (``WinError 5``) where POSIX only consults the
    parent directory. Clear the bit and retry.
    """
    def _retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=_retry)


pytestmark = pytest.mark.portability

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "packages"
WIDGET = "widget_good"

needs_git = pytest.mark.skipif(not _git.available(),
                               reason="git is not on PATH")

_IDENTITY = ("-c", "user.email=gate@agentcad.test", "-c", "user.name=Gate",
             "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main")


# --------------------------------------------------------------- fixtures


def git(*args, cwd):
    return subprocess.run(["git", *_IDENTITY, *args], cwd=str(cwd),
                          capture_output=True, text=True, check=True)


def index_document(root, versions=("1.0.0",)):
    doc = {"format": 1, "name": "acme", "scope": "public", "packages": {},
           "embeddings": None}
    for version in versions:
        rel = f"{WIDGET}/{version}"
        target = root / rel
        if not target.exists():
            shutil.copytree(FIXTURES / WIDGET, target)
            doc_path = target / "package.json"
            package = json.loads(doc_path.read_text())
            package["version"] = version
            doc_path.write_text(json.dumps(package, indent=2) + "\n")
        doc["packages"].setdefault(WIDGET, {"versions": {}})
        doc["packages"][WIDGET]["versions"][version] = {
            "content_id": content.content_id(target),
            "path": rel,
            "summary": "A bored, chamfered mounting block",
            "keywords": ["block"], "standards": [],
            "license": "Apache-2.0", "disclosure": "agent",
            "parts": {"mount_block": {"params": [], "connectors": {},
                                      "specs": []}},
            "presets": [], "previews": [],
            "gate": {"status": "green", "exempt_skips": [],
                     "agentcad": "0.1.0", "build123d": "0.11.1",
                     "report_id": "sha256:" + "ab" * 32},
            "yanked": False, "signatures": [],
        }
    (root / "index.json").write_text(json.dumps(doc, indent=2) + "\n")
    return doc


@pytest.fixture
def remote(tmp_path):
    """A bare repository holding a one-package index, reachable over
    `file://`. Committing inside a scratch fixture repo is fixture state, not
    this project's history."""
    work = tmp_path / "remote_work"
    work.mkdir()
    index_document(work)
    git("init", cwd=work)
    git("add", "-A", cwd=work)
    git("commit", "-m", "publish widget_good 1.0.0", cwd=work)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)
    return {"work": work, "bare": bare, "url": bare.as_uri()}


def push(remote, message="update"):
    git("add", "-A", cwd=remote["work"])
    git("commit", "-m", message, cwd=remote["work"])
    git("push", str(remote["bare"]), "main", cwd=remote["work"])


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTCAD_INDEXES_DIR", str(tmp_path / "indexes"))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    return tmp_path / "cache"


@pytest.fixture
def service(tmp_path, kernel, cache_root):
    svc = make_test_service(tmp_path / "projects", kernel)
    svc.create_project("rig")
    return svc


def make_git_index(remote, name="acme", ref="main"):
    return indexes.GitIndex(name, remote["url"], ref=ref)


# ============================================== the runner and its guardrails


@pytest.mark.parametrize("url", [
    "--upload-pack=touch /tmp/pwned",
    "-o ProxyCommand=sh",
    "https://example.com/a.git; rm -rf /",
    "https://example.com/a.git && whoami",
    "https://example.com/`id`.git",
    "https://example.com/$(id).git",
    "https://example.com/a.git\nrm -rf /",
    "ftp://example.com/a.git",
    "example.com/a.git",
    "",
    None,
])
def test_a_url_that_is_not_one_is_refused_before_any_subprocess(url,
                                                                monkeypatch):
    """Fixed argv makes a metacharacter inert; it is still refused, because
    defence that only works when the *other* defence works is not defence."""
    monkeypatch.setattr(subprocess, "run", _never_called)
    with pytest.raises(ValidationError):
        _git.validate_url(url)


@pytest.mark.parametrize("url", [
    "https://github.com/acme/parts.git",
    "ssh://git@github.com/acme/parts.git",
    "git@github.com:acme/parts.git",
    "/srv/mirrors/parts.git",
    "file:///srv/mirrors/parts.git",
])
def test_the_four_url_shapes_the_design_names_are_accepted(url):
    assert _git.validate_url(url) == url


def _never_called(*args, **kwargs):     # pragma: no cover - the point is that
    raise AssertionError("a subprocess was started")   # it is never reached


def test_the_environment_blocks_prompts_and_leaves_home_alone(monkeypatch):
    """`history._run` redirects HOME into `.history` so a user's ~/.gitconfig
    cannot interfere. That is exactly wrong here: a private index is the case
    that needs the user's credential helper."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    import os

    monkeypatch.setenv("HOME", "/home/somebody")
    monkeypatch.setattr(subprocess, "run", fake_run)
    _git.run("ls-remote", "https://example.com/a.git")
    env = seen["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == ""
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    # The three `history._run` does that this must NOT do.
    assert env["HOME"] == "/home/somebody"
    assert env.get("XDG_CONFIG_HOME") == os.environ.get("XDG_CONFIG_HOME")
    assert "GIT_CONFIG_NOSYSTEM" not in env, \
        "a private index needs the user's credential helper"
    assert seen["timeout"] == _git.DEFAULT_TIMEOUT >= 120, \
        "a clone routinely exceeds history._run's 10 s"
    assert "shell" not in seen, "fixed argv, never a shell"
    # `shutil.which("git")` answers `...\git.EXE` on Windows, so compare the
    # basename case-insensitively rather than a raw suffix.
    assert Path(seen["cmd"][0]).name.lower() in ("git", "git.exe")
    assert "--git-dir" not in seen["cmd"] and "--work-tree" not in seen["cmd"]


def test_a_caller_supplied_ssh_command_is_left_alone(monkeypatch):
    """BatchMode is a default, not an override: a user with a custom
    GIT_SSH_COMMAND has a reason for it."""
    seen = {}
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /keys/acme")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: seen.update(kw)
                        or subprocess.CompletedProcess(cmd, 0, "", ""))
    _git.run("ls-remote", "https://example.com/a.git")
    assert seen["env"]["GIT_SSH_COMMAND"] == "ssh -i /keys/acme"


def test_a_failing_git_call_is_a_git_error_carrying_its_stderr(tmp_path,
                                                               monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: nope\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(_git.GitError) as exc:
        _git.run("fetch")
    assert "fatal: nope" in str(exc.value)


def test_a_timeout_is_a_git_error_and_not_a_hang(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(_git.GitError):
        _git.run("fetch")


# ================================================= the client, against a repo


@needs_git
def test_a_git_index_clones_on_first_use_and_answers_like_a_local_one(
        remote, cache_root):
    index = make_git_index(remote)
    assert index.kind == "git"
    index.refresh()
    assert index.stale is False
    assert list(index.versions(WIDGET)) == ["1.0.0"]
    assert index.fetch(WIDGET, "1.0.0").is_dir()
    assert (index.path / ".git").exists()


@needs_git
def test_the_lock_entry_records_the_url_and_ref_and_no_absolute_path(
        remote, cache_root):
    index = make_git_index(remote)
    index.refresh()
    source = index.source_of(index.entry(WIDGET, "1.0.0"))
    assert source == {"kind": "git", "url": remote["url"], "ref": "main",
                      "path": f"{WIDGET}/1.0.0"}
    assert str(index.path) not in json.dumps(source)


@needs_git
def test_add_by_url_then_search_then_install(remote, service, cache_root):
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "acme", "kind": "git", "url": remote["url"], "ref": "main"}]})
    registry = build_registry(service)
    hits = registry.call("search_packages", {"query": WIDGET})["hits"]
    assert [(hit["name"], hit["index"], hit["kind"])
            for hit in hits] == [(WIDGET, "acme", "git")]
    added = registry.call("add_package", {"project": "rig", "name": WIDGET})
    # Surface a structured error instead of dying on KeyError below — on the
    # first Windows CI runs the envelope was invisible behind `added["lock"]`.
    assert "error" not in added, added
    assert added["lock"]["source"]["url"] == remote["url"]
    detail = registry.call("use_part", {
        "project": "rig", "package": WIDGET, "part": "mount_block",
        "part_id": "block"})
    assert detail["status"]["state"] == "ok"


@needs_git
def test_a_refresh_picks_up_a_version_published_after_the_clone(remote,
                                                               cache_root):
    index = make_git_index(remote)
    index.refresh()
    assert list(index.versions(WIDGET)) == ["1.0.0"]
    index_document(remote["work"], versions=("1.0.0", "1.1.0"))
    push(remote, "publish 1.1.0")
    index.refresh(force=True)
    assert sorted(index.versions(WIDGET)) == ["1.0.0", "1.1.0"]


@needs_git
def test_a_force_pushed_index_repo_is_followed_not_merged(remote, cache_root,
                                                          tmp_path):
    """`reset --hard` after a shallow fetch, on purpose: an index repo whose
    history was rewritten must not leave the client on a branch that no longer
    exists, and a merge would invent a document nobody published."""
    index = make_git_index(remote)
    index.refresh()
    rewritten = tmp_path / "rewritten"
    rewritten.mkdir()
    index_document(rewritten, versions=("2.0.0",))
    git("init", cwd=rewritten)
    git("add", "-A", cwd=rewritten)
    git("commit", "-m", "a different history entirely", cwd=rewritten)
    git("push", "--force", str(remote["bare"]), "main", cwd=rewritten)
    index.refresh(force=True)
    assert sorted(index.versions(WIDGET)) == ["2.0.0"]
    assert index.stale is False


# ============================================================ failure is data


@needs_git
def test_an_unreachable_remote_is_a_warning_and_the_checkout_keeps_answering(
        remote, cache_root):
    index = make_git_index(remote)
    index.refresh()
    _rmtree_repo(remote["bare"])
    index.refresh(force=True)
    assert index.stale is True
    assert index.stale_reason and remote["url"] in index.stale_reason
    assert list(index.versions(WIDGET)) == ["1.0.0"], \
        "the last good checkout must keep answering"


@needs_git
def test_search_carries_the_staleness_and_its_reason(remote, cache_root):
    from agentcad.core.packages import search
    index = make_git_index(remote)
    index.refresh()
    _rmtree_repo(remote["bare"])
    index.refresh(force=True)
    result = search.search([index], query=WIDGET)
    assert result["hits"][0]["stale"] is True
    assert any("stale" in warning for warning in result["warnings"])


@needs_git
def test_a_never_cloned_index_is_not_found_naming_the_url(tmp_path, cache_root):
    index = indexes.GitIndex("acme", (tmp_path / "nothing.git").as_uri())
    index.refresh()
    assert index.stale is True
    with pytest.raises(NotFoundError) as exc:
        index.entries()
    assert "nothing.git" in str(exc.value)


@needs_git
def test_a_broken_git_index_does_not_stop_the_next_one(remote, service,
                                                       tmp_path, cache_root):
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "gone", "kind": "git",
         "url": (tmp_path / "nothing.git").as_uri()},
        {"name": "acme", "kind": "git", "url": remote["url"]}]})
    manager = PackageManager(service)
    resolution = manager.resolve(WIDGET, "^1.0.0")
    assert resolution["index"] == "acme"
    assert any(entry["index"] == "gone" for entry in resolution["tried"])


def test_without_git_a_git_index_registers_nothing_and_says_so():
    warnings = []
    built = indexes.load_indexes(
        {"indexes": [{"name": "acme", "kind": "git",
                      "url": "https://example.com/a.git"}]},
        warnings, git_available=lambda: False)
    assert built == []
    assert any("git" in warning and "acme" in warning for warning in warnings)


@pytest.mark.parametrize("entry,fragment", [
    ({"name": "acme", "kind": "git"}, "url"),
    ({"name": "acme", "kind": "git", "url": "nope"}, "url"),
    ({"name": "acme", "kind": "git", "url": "https://x/a.git", "ref": 3},
     "ref"),
])
def test_a_misconfigured_git_index_is_skipped_with_a_reason(entry, fragment):
    warnings = []
    assert indexes.load_indexes({"indexes": [entry]}, warnings,
                                git_available=lambda: True) == []
    assert any(fragment in warning for warning in warnings)


def test_a_configured_git_index_defaults_its_ref_to_main():
    built = indexes.load_indexes(
        {"indexes": [{"name": "acme", "kind": "git",
                      "url": "https://example.com/a.git"}]},
        [], git_available=lambda: True)
    assert [(i.name, i.kind, i.ref) for i in built] == [("acme", "git", "main")]


@needs_git
def test_a_git_index_is_read_only_through_this_client(remote, cache_root):
    """It inherits `publish`/`yank` from `LocalIndex`, and both would write
    into a checkout the very next refresh hard-resets — a write that vanishes
    with no error is the worst shape a failure can take."""
    index = make_git_index(remote)
    index.refresh()
    with pytest.raises(ValidationError) as exc:
        index.publish(FIXTURES / WIDGET, {})
    assert "push" in str(exc.value)
    with pytest.raises(ValidationError):
        index.yank(WIDGET, "1.0.0")
    assert json.loads((index.path / "index.json").read_text())[
        "packages"][WIDGET]["versions"]["1.0.0"]["yanked"] is False


# ==================================================================== AC4


@needs_git
def test_ac4_the_remote_disappears_and_everything_still_works(
        remote, service, cache_root, tmp_path):
    """Delete the remote entirely: `use_part` keeps working from the cache,
    and `add_package` of the already-cached package still succeeds offline
    with a **byte-identical** lock entry."""
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "acme", "kind": "git", "url": remote["url"], "ref": "main"}]})
    registry = build_registry(service)
    added = registry.call("add_package", {"project": "rig", "name": WIDGET})
    assert "error" not in added, added
    online = json.dumps(service.store.manifest("rig")["packages_lock"][WIDGET],
                        indent=2, sort_keys=True)

    _rmtree_repo(remote["bare"])
    _rmtree_repo(indexes.GitIndex("acme", remote["url"]).path)

    detail = registry.call("use_part", {
        "project": "rig", "package": WIDGET, "part": "mount_block",
        "part_id": "block"})
    assert detail["status"]["state"] == "ok"

    service.create_project("second")
    manager = PackageManager(service)
    manager.reload_indexes()
    added = manager.add("second", WIDGET, "^1.0.0")
    assert added["offline"] is True
    offline = json.dumps(service.store.manifest("second")["packages_lock"][WIDGET],
                         indent=2, sort_keys=True)
    assert offline == online, "offline is not a second answer"


@needs_git
def test_a_tampered_checkout_installs_nothing_and_names_both_ids(
        remote, service, cache_root):
    """The index is data from somewhere else. A checkout whose tree no longer
    matches the id its own document declares installs NOTHING."""
    index = make_git_index(remote)
    index.refresh()
    script = index.path / WIDGET / "1.0.0" / "parts" / "mount_block.py"
    script.write_text(script.read_text() + "\n# planted\n")
    manager = PackageManager(service, indexes=[index])
    with pytest.raises(ValidationError) as exc:
        manager.add("rig", WIDGET, "^1.0.0")
    assert "content id mismatch" in str(exc.value)
    assert cache.cached_versions(WIDGET) == []


# ============ the index lives in a SUBDIRECTORY (Codex #8, changelog 0181)


@needs_git
def test_a_git_index_serves_an_index_that_lives_in_a_subdirectory(tmp_path):
    """`subdir` — and the reason it had to exist: **this repository**.

    `GitIndex` hard-coded `<checkout>/index.json`, so a repo that is an index
    and nothing else worked and a repo that ships an index *alongside its
    source* did not. AgentCAD is the second kind (`catalog/index.json`), which
    means the shipped catalog was not usable as a git index at all — while the
    acceptance test said it was, by copying `catalog/*` to the root of a
    synthetic repo. That proved the fixture, not the product.
    """
    work = tmp_path / "repo"
    (work / "catalog").mkdir(parents=True)
    (work / "agentcad").mkdir()          # the source tree lives here too
    (work / "agentcad" / "__init__.py").write_text("__version__ = '0.1.0'\n")
    (work / "README.md").write_text("# a repo that is ALSO an index\n")
    index_document(work / "catalog")
    git("init", cwd=work)
    git("add", "-A", cwd=work)
    git("commit", "-m", "repo with a catalog", cwd=work)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)

    # Without `subdir` the index is simply not there: index.json is one level in.
    plain = indexes.GitIndex("plain", bare.as_uri(), root=tmp_path / "co1")
    plain.refresh()
    with pytest.raises(NotFoundError):
        plain.entries()

    served = indexes.GitIndex("acme", bare.as_uri(), root=tmp_path / "co2",
                              subdir="catalog")
    served.refresh()
    assert served.entries()["name"] == "acme"
    assert served.versions(WIDGET)
    # The clone went to the repository root; the INDEX is the subdirectory.
    assert served.checkout != served.path
    assert served.path == served.checkout / "catalog"
    assert (served.checkout / "agentcad" / "__init__.py").is_file()
    assert served.fetch(WIDGET, "1.0.0").is_dir()


@needs_git
def test_load_indexes_passes_subdir_through_and_validates_it(tmp_path):
    warnings = []
    built = indexes.load_indexes(
        {"indexes": [{"name": "ok", "kind": "git", "url": "https://e.com/r.git",
                      "subdir": "catalog"},
                     {"name": "escape", "kind": "git",
                      "url": "https://e.com/r.git", "subdir": "../../etc"}]},
        warnings, git_available=lambda: True)
    assert [i.name for i in built] == ["ok"]
    assert built[0].subdir == "catalog"
    assert any("escape" in w and "subdir" in w for w in warnings), warnings


@needs_git
def test_THIS_repository_is_usable_as_a_git_index_with_subdir(tmp_path):
    """The dogfood test, driving **this repo's real layout**.

    A bare clone of a working tree shaped exactly like this repository —
    `catalog/` beside `agentcad/`, `docs/`, `tests/` — served through
    `subdir: "catalog"`, must offer the nine bundled packages and hand back
    trees that hash to the ids `catalog/index.json` advertises. If this fails,
    the README's "point a git index at this repo" instruction is wrong.
    """
    catalog = REPO / "catalog"
    if not (catalog / "index.json").is_file():
        pytest.skip("no bundled catalog in this checkout")

    work = tmp_path / "agentcad_repo"
    shutil.copytree(catalog, work / "catalog")
    for extra in ("agentcad", "docs", "tests"):
        (work / extra).mkdir(parents=True)
        (work / extra / "placeholder.py").write_text("# the source tree\n")
    (work / "pyproject.toml").write_text("[project]\nname = 'agentcad'\n")
    git("init", cwd=work)
    git("add", "-A", cwd=work)
    git("commit", "-m", "agentcad", cwd=work)
    bare = tmp_path / "agentcad.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)

    index = indexes.GitIndex("agentcad-core", bare.as_uri(),
                             root=tmp_path / "checkouts", subdir="catalog")
    index.refresh()
    doc = index.entries()
    shipped = json.loads((catalog / "index.json").read_text())
    assert set(doc["packages"]) == set(shipped["packages"])
    assert len(doc["packages"]) >= 9, sorted(doc["packages"])

    # Every advertised content id is the id of the tree the git index serves —
    # which is what "the bundled catalog IS what a git index would serve" means.
    for name, record in sorted(doc["packages"].items()):
        for version, entry in sorted(record["versions"].items()):
            tree = index.fetch(name, version)
            assert content.content_id(tree) == entry["content_id"], \
                f"{name}@{version} does not hash to its advertised id"


@needs_git
def test_a_missing_subdir_is_diagnosed_as_a_missing_subdir(tmp_path):
    """"Never cloned from <url>" was the only answer `entries()` had, and with
    `subdir` it is routinely wrong: the clone is right there and the
    subdirectory is a typo. Sending someone to debug their remote in that case
    is worse than saying nothing, because it is confidently wrong."""
    work = tmp_path / "repo"
    (work / "src").mkdir(parents=True)
    (work / "src" / "a.py").write_text("x = 1\n")
    git("init", cwd=work)
    git("add", "-A", cwd=work)
    git("commit", "-m", "source only", cwd=work)
    bare = tmp_path / "r.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)

    absent = indexes.GitIndex("acme", bare.as_uri(), root=tmp_path / "c1",
                              subdir="catalog")
    absent.refresh()
    with pytest.raises(NotFoundError) as info:
        absent.entries()
    assert "no such directory" in info.value.message
    assert "the clone is fine" in info.value.message
    assert "never been cloned" not in info.value.message
    assert info.value.details["subdir"] == "catalog"

    # A file where the subdir should be is its own diagnosis.
    (work / "catalog").write_text("not a directory\n")
    git("add", "-A", cwd=work)
    git("commit", "-m", "catalog is a file", cwd=work)
    subprocess.run(["git", "push", str(bare), "main"], cwd=work, check=True,
                   capture_output=True)
    not_a_dir = indexes.GitIndex("acme", bare.as_uri(), root=tmp_path / "c2",
                                 subdir="catalog")
    not_a_dir.refresh()
    with pytest.raises(NotFoundError) as info:
        not_a_dir.entries()
    assert "is not a directory" in info.value.message


@needs_git
def test_a_repo_with_no_index_at_the_root_suggests_subdir(tmp_path):
    """The cloned-but-empty case names what is missing and points at the option
    that fixes it, instead of blaming the remote."""
    work = tmp_path / "repo"
    (work / "src").mkdir(parents=True)
    (work / "src" / "a.py").write_text("x = 1\n")
    git("init", cwd=work)
    git("add", "-A", cwd=work)
    git("commit", "-m", "source only", cwd=work)
    bare = tmp_path / "r.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)

    index = indexes.GitIndex("acme", bare.as_uri(), root=tmp_path / "c")
    index.refresh()
    with pytest.raises(NotFoundError) as info:
        index.entries()
    assert "there is no index.json in it" in info.value.message
    assert "'subdir'" in info.value.message
    assert "never been cloned" not in info.value.message


@needs_git
def test_a_remote_that_was_never_cloned_still_says_so(tmp_path):
    """The negation: the original message is still the right one when it is
    actually true."""
    index = indexes.GitIndex("gone", "file:///nowhere/x.git",
                             root=tmp_path / "c", subdir="catalog")
    index.refresh()
    with pytest.raises(NotFoundError) as info:
        index.entries()
    assert "has never been cloned" in info.value.message
