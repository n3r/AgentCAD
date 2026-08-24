"""Client half of git sync (PRD-005 FR8-client/FR10): login, clone, push, pull.

The server half is ``server/routes_sync.py`` + ``core/sync_server.py``. This
module is what a *person's* machine runs: it clones a hosted project into the
``core/history.py`` layout, pushes their commits back, and pulls the other
side's work in **through the PRD-001 merge machinery** rather than over the top
of their own.

Three rules shape every line here, all of them measured in the PRD-005 spike
(``docs/superpowers/specs/2026-08-24-multi-tenant-cloud-spike.md``):

1. **The token is never on an argv, never in a URL, never in a header we
   spell.** The spike tried four ways of authenticating a git call (§A9):
   ``http.extraHeader`` puts the token in ``ps`` for every process on the box
   *and* re-sends it across a same-host redirect; a URL-embedded token lands in
   ``remote.origin.url``, on disk, in every clone. Only the **credential
   helper** leaks nothing (``files in the clone containing the token: 0``), and
   it gives the better error too, because git only offers a helper's answer
   after a ``401`` from the final URL — so the server's own message reaches the
   human. Hence :func:`helper_command` and ``agentcad credential``.
2. **A clone must be a ProjectHistory repo, not a git checkout that looks like
   one.** ``git clone --separate-git-dir`` leaves a ``.git`` *pointer file* in
   the project, which ``history.py``'s docstring forbids outright. The winner
   (spike §A10) is: clone ``--bare`` into ``<dest>/.history``, then flip
   ``core.bare``/``core.worktree``/the fetch refspec, write ``info/exclude``
   **before** any status call, set the repo-local identity ``_ensure_repo``
   would have set, and ``checkout -f`` the default branch. :func:`clone` is
   that sequence and :func:`verify_layout` is the assertion that it worked.
3. **Never reset, never force, never overwrite.** :func:`pull` fast-forwards
   what can fast-forward and hands everything else to
   ``MergeOrchestrator.merge`` — the same staged, kernel-validated merge the UI
   drives, with the same ``merge_conflict`` payload. A divergence is a merge or
   a refusal; it is never a ``reset --hard``. :func:`push` sends two explicit
   non-forcing refspecs and nothing else (see :func:`push_refspecs`).

**Why this is a second git runner and not ``history._run``** — the same
argument ``packages/_git.py`` makes, and this module is modelled on that one:
``_run`` hard-codes ``--git-dir``/``--work-tree``, times out at 10 s (a clone
routinely exceeds that), and redirects ``HOME`` — which disables the credential
helper this whole feature is built on. So :func:`run` is credential-friendly:
120 s, no ``HOME`` redirect, ``GIT_TERMINAL_PROMPT=0`` so a missing token is an
error instead of a hung prompt, validated URLs, fixed argv, no shell.

:func:`local` is the other half of that split: work-tree operations that touch
**no** network (a fast-forward, a status) run hermetically — ``HOME`` into the
GIT_DIR, ``GIT_CONFIG_NOSYSTEM=1``, exactly ``history._exec``'s environment —
because a user's global ``gitconfig`` (a merge driver, a hook, a signing key)
must not decide what a sync does to their files. Two runners, one rule each,
named so nobody has to guess which is which.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .. import config as user_config
# The managed exclude list, IMPORTED and not re-spelled: a clone that excluded
# a different set from the one `ProjectHistory._refresh_excludes` maintains
# would track `.cache/` on one machine and not the other, and FR8's "derived
# data never syncs" would hold only where the project was created.
from .history import _EXCLUDE_LINES, valid_ref_name
from .model import ValidationError

#: A clone of a real project is not a 10 s operation (``history``'s timeout).
DEFAULT_TIMEOUT = 120.0

#: Refused anywhere in a URL. Inert against fixed argv, refused anyway — the
#: ``packages/_git.py`` rule, for the same reason: defence that only works when
#: the *other* defence works is not defence.
_METACHARACTERS = set(";|&$`<>()\n\r\t\\\"'*?[]{}!~ ")

#: The one predicate that decides whether a tree path lands in a repo's git
#: internals, imported from the server half so client, server and merge all fold
#: identically (case, NTFS trailing dot/space, HFS-ignorable unicode). A
#: malicious server, or a poisoned remote branch a collaborator pushed, can
#: advertise a tree that writes into this clone's own GIT_DIR
#: (``<project>/.history``) — a ``post-merge`` hook, a ``config`` — which then
#: runs as the user on the checkout/merge a clone or pull performs. A byte-exact
#: test missed ``.History`` on macOS/Windows (the PRD-005 re-check); the shared
#: fold does not. ``.gitignore``/``.gitattributes`` do not fold to ``.git``.
from .sync_server import is_git_internal_path

#: The schemes an AgentCAD instance is reachable on. ``http://`` is here
#: because a developer instance and every test in this repo is
#: ``http://127.0.0.1:<port>``; :func:`login` says so out loud for a
#: non-loopback one rather than refusing a legitimate lab setup.
_SCHEMES = ("https://", "http://")

#: Where :func:`pull` parks the fetched other side while the merge machinery
#: works on it. A real local branch is required — ``MergeOrchestrator`` merges
#: branch into branch — so it gets a namespace of its own, and
#: :func:`push_refspecs` keeps it off the wire.
INTERNAL_BRANCH_PREFIX = "incoming/"

#: The refusal a divergent pull raises when the fetched other side — its tip, or
#: the merge it would produce — writes into this clone's own git internals. A
#: poisoned ``.history/hooks/post-merge``/``.history/config`` materialized into
#: ``<project>/.history`` runs as the user on the next git call. Fixer 1's
#: checkout/ff belts close the fast-forward path; this is the same rule for the
#: staged merge a DIVERGENT pull drives through ``MergeOrchestrator``.
_MERGE_INTERNALS_MSG = (
    "refusing to merge a branch that writes into .history/ — the remote's git "
    "internals are not yours to pull"
)

#: What a git credential helper puts in the ``username`` field. The server
#: **ignores it** (``routes_sync._promote_basic_to_bearer`` discards the user
#: and treats the password as the bearer), so this is a label, not an
#: identity — and it is spelled the way every token-as-password service spells
#: it, so a human reading ``git`` output or a proxy log is not told that
#: "agentcad" is who they are. Their principal is ``user:<handle>``, and it is
#: the token that names it.
CREDENTIAL_USERNAME = "x-access-token"

#: ``/git/{org}/{ws}/{proj}.git`` — the server's mount (``routes_sync``), whose
#: segment grammar this mirrors deliberately (a superset of ``model.ID_RE``).
_REPO_PATH_RE = re.compile(
    r"^/git/(?P<org>[a-z][a-z0-9_-]{0,63})/(?P<ws>[a-z][a-z0-9_-]{0,63})/"
    r"(?P<proj>[a-z][a-z0-9_-]{0,63})\.git/?$")

_git_path: str | None = None
_checked = False


class SyncError(RuntimeError):
    """A sync operation that did not succeed.

    Carries the git call's **whole stderr**, because for this feature the
    server's message *is* the product: the pre-receive hook refuses a
    divergence with ``remote: agentcad: refs/heads/master diverged — pull and
    merge, never force``, and a client that swallowed that in favour of "exit
    status 1" would have thrown away the only actionable thing it was told.
    :func:`remote_lines` pulls those lines back out verbatim.
    """

    def __init__(self, message: str, *, returncode: int | None = None,
                 stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr

    @property
    def remote(self) -> list[str]:
        return remote_lines(self.stderr)


def remote_lines(stderr: str) -> list[str]:
    """The ``remote: …`` lines of a git stderr, verbatim and in order.

    The prefix is stripped (git added it; the server did not write it) and
    nothing else is: the hook's wording was chosen to be read by a human and
    re-wording it here would be inventing a second, worse error message.
    """
    out = []
    for line in (stderr or "").splitlines():
        text = line.strip()
        if text.startswith("remote:"):
            body = text[len("remote:"):].strip()
            if body:
                out.append(body)
    return out


# ------------------------------------------------------------------ the git

def available() -> bool:
    """True when a git executable is on PATH (resolved once, cached)."""
    global _git_path, _checked
    if not _checked:
        _git_path = shutil.which("git")
        _checked = True
    return _git_path is not None


def executable() -> str:
    available()
    return _git_path or "git"


def helper_command() -> str:
    """The shell command git runs as the credential helper for our calls.

    Configured as ``credential.helper=!<this>``: the ``!`` makes git run it
    through ``sh -c "<this> get"``, so the operation arrives as ``$1`` and the
    query on stdin. Three ways of naming this program, in the order that is
    right on the machine we are on:

    1. a **frozen** bundle re-execs itself (there is no ``agentcad`` script and
       no interpreter to borrow);
    2. the ``agentcad`` console script **beside the running interpreter** —
       ``.venv/bin/agentcad`` for ``uv run``, which is the case in this repo
       and in every test — falling back to ``PATH``;
    3. the interpreter itself with a one-line ``-c``. There is no
       ``agentcad/__main__.py``, so ``-m agentcad`` is not an option; this is
       the honest last resort rather than a module that does not exist.

    Every component is ``shlex.quote``d because the string reaches ``sh``.
    """
    if getattr(sys, "frozen", False):
        return f"{shlex.quote(sys.executable)} credential"
    name = "agentcad.exe" if os.name == "nt" else "agentcad"
    beside = Path(sys.executable).parent / name
    script = str(beside) if beside.is_file() else shutil.which("agentcad")
    if script:
        return f"{shlex.quote(script)} credential"
    return (f"{shlex.quote(sys.executable)} -c "
            f"{shlex.quote('from agentcad.cli import main; main()')} credential")


def _base_env() -> dict[str, str]:
    """Environment shared by both runners: never a prompt, never an askpass.

    ``GIT_TERMINAL_PROMPT=0`` is the one that matters for a CLI: without it a
    missing or wrong token stops the command on an invisible ``Username for
    'https://…':`` prompt instead of failing with the server's own message.
    """
    env = {**os.environ,
           "GIT_TERMINAL_PROMPT": "0",
           "GIT_ASKPASS": "",
           "SSH_ASKPASS": ""}
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def _pins() -> list[str]:
    """Per-invocation config every call carries.

    ``core.autocrlf=false``: the server's copy of a script is the bytes it
    received, and a Windows client whose global config rewrites LF to CRLF at
    checkout would push a whole-file diff of nothing on its first commit.
    ``-c`` beats every config scope, so a user's global setting cannot re-break
    it (the ``packages/_git.py`` lesson, measured on PR #15's CI).

    ``core.hooksPath=/dev/null`` + ``core.fsmonitor=false``: a *poisoned remote*
    or a collaborator's push can carry a ``.history/hooks/post-merge`` /
    ``post-checkout`` and a ``.history/config`` with an ``fsmonitor`` command
    into a clone. On this workstation those would run as the user — on a plain
    ``git status`` (``fsmonitor``) or the checkout/merge a pull performs. Pinned
    off on **every** call so no repo-carried config can make a sync run code.
    (The materialize belt, :func:`_assert_no_git_internals`, is what stops the
    files landing at all; this is the second lock on the same door.)
    """
    return ["-c", "core.autocrlf=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false"]


def _credential_pins() -> list[str]:
    """The credential helper, as the **only** helper for this invocation.

    The empty value first is git's documented way to reset an inherited helper
    list: a global ``osxkeychain``/``manager`` would otherwise be asked before
    ours and could answer for this host with a stale credential that ours is
    then never asked to replace. After ``agentcad login`` the answer in
    ``sync.json`` is the authoritative one for this instance, so it is the only
    one offered.
    """
    return ["-c", "credential.helper=",
            "-c", f"credential.helper=!{helper_command()}"]


def run(*args: str, cwd=None, timeout: float = DEFAULT_TIMEOUT,
        check: bool = True) -> subprocess.CompletedProcess:
    """One **network** git call: credential-friendly, fixed argv, no shell.

    ``HOME`` is deliberately *not* redirected (unlike ``history._exec``): a
    person's proxy, CA bundle and ssh configuration live there, and a sync that
    ignored them would fail on exactly the corporate network this feature is
    for. What replaces the hermetic environment as a defence is that the
    credential list is reset per invocation (:func:`_credential_pins`) and the
    URL is validated (:func:`validate_url`).
    """
    cmd = [executable(), *_pins(), *_credential_pins(), *args]
    return _exec(cmd, cwd=cwd, env=_base_env(), timeout=timeout, check=check)


def local(project_dir, *args: str, timeout: float = DEFAULT_TIMEOUT,
          check: bool = True) -> subprocess.CompletedProcess:
    """One **local** git call against a project's history repo.

    ``--git-dir <project>/.history --work-tree <project>`` and
    ``history._exec``'s hermetic environment, byte for byte: ``HOME`` and
    ``XDG_CONFIG_HOME`` into the GIT_DIR, ``GIT_CONFIG_NOSYSTEM=1``. Nothing
    here talks to a network, so nothing here needs a credential — and a global
    ``gitconfig`` carrying a merge driver, a hook or a signing key must not get
    a vote on what a fast-forward does to somebody's files.

    The timeout is 120 s rather than ``history``'s 10 s: a cold ``checkout -f``
    over a large project is the one call here that legitimately takes seconds.
    """
    project_dir = Path(project_dir)
    git_dir = history_dir(project_dir)
    env = {**_base_env(),
           "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": str(git_dir),
           "XDG_CONFIG_HOME": str(git_dir / "xdg")}
    cmd = [executable(), *_pins(),
           "--git-dir", str(git_dir), "--work-tree", str(project_dir), *args]
    return _exec(cmd, cwd=project_dir, env=env, timeout=timeout, check=check)


def _exec(cmd, *, cwd, env, timeout, check) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, cwd=None if cwd is None else str(cwd),
                                env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout)
    except FileNotFoundError as exc:
        raise SyncError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"git {cmd[-1]} timed out after {timeout:g}s") from exc
    if check and result.returncode != 0:
        stderr = result.stderr or ""
        # The last line is git's own summary; the `remote:` lines are the
        # server's and the caller prints them separately, in full.
        tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        raise SyncError(f"git failed ({result.returncode}): {tail}",
                        returncode=result.returncode, stderr=stderr)
    return result


def history_dir(project_dir) -> Path:
    return Path(project_dir) / ".history"


def git_internals_in_tree(project_dir, ref: str) -> list[str]:
    """Paths in *ref*'s tree that would write into this clone's git internals.

    ``.history/**`` or any ``.git`` component. Reads the object out of the
    shared ``.history`` object store, so it answers for the default branch, a
    branch with no checkout, or a fetched ``refs/remotes/*`` ref alike. Empty
    list = the tree is safe to check out or fast-forward into.
    """
    result = local(project_dir, "ls-tree", "-r", "-t", "--name-only", "-z",
                   ref, check=False)
    return sorted(path for path in (result.stdout or "").split("\0")
                  if path and is_git_internal_path(path))


def _assert_no_git_internals(project_dir, ref: str) -> None:
    """Refuse a checkout/merge of a tree that carries our git internals.

    The client half of the server's pre-receive rule: a poisoned remote (or a
    branch a collaborator managed to push before the server was hardened) must
    not be materialized onto this workstation, where a planted
    ``.history/hooks/post-merge`` runs as the user.
    """
    offending = git_internals_in_tree(project_dir, ref)
    if offending:
        raise SyncError(
            f"refusing to check out {ref!r}: it writes into the project's git "
            f"internals ({', '.join(offending[:5])}) — a poisoned remote. "
            "Nothing was changed.")


# ------------------------------------------------------------------- urls

def validate_url(url) -> str:
    """The URL, or a ``ValueError`` naming the rule it broke.

    ``packages/_git.py``'s validator, narrowed to http(s) (an AgentCAD
    instance is an HTTP server) and widened by one refusal: a URL carrying
    **userinfo** is rejected outright. That is not a syntax preference — a
    token in ``https://x:tok@host/…`` is recorded in ``remote.origin.url``, on
    disk, in every clone, which is precisely the leak the credential helper
    exists to close (spike §A9, P2).
    """
    if not isinstance(url, str) or not url:
        raise ValueError("a remote needs a non-empty url")
    if url.startswith("-"):
        raise ValueError(
            f"refusing the url {url!r}: a value starting with '-' would be "
            "read by git as an option, not a repository")
    bad = sorted(_METACHARACTERS & set(url))
    if bad:
        raise ValueError(
            f"refusing the url {url!r}: it contains {''.join(bad)!r}")
    if not url.startswith(_SCHEMES):
        raise ValueError(
            f"refusing the url {url!r}: expected http:// or https://")
    parts = urlsplit(url)
    if not parts.hostname:
        raise ValueError(f"refusing the url {url!r}: it names no host")
    if parts.username or parts.password:
        raise ValueError(
            f"refusing the url {url!r}: it carries credentials. A token in a "
            "URL is written into remote.origin.url and lives on disk in every "
            "clone — sign in with `agentcad login` instead, which stores it "
            "0600 and hands it to git through a credential helper")
    return url


def instance_of(url) -> str:
    """The ``scheme://host[:port]`` a URL belongs to — the key everything else
    uses.

    It is also **git's** key: a credential helper is queried by
    ``protocol``+``host`` (the port included, the path never), so storing
    anything finer-grained would mean a helper that could not find its own
    entry.
    """
    validate_url(url)
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


def parse_repo_url(url) -> dict:
    """``{instance, org, workspace, project, url}`` for a sync URL.

    The shape is the server's mount, ``/git/{org}/{ws}/{proj}.git``. Parsed
    here rather than guessed later so ``agentcad clone`` can tell somebody who
    pasted their browser's address bar what a sync URL looks like.
    """
    validate_url(url)
    parts = urlsplit(url)
    match = _REPO_PATH_RE.match(parts.path)
    if not match:
        raise ValueError(
            f"{url!r} is not an AgentCAD sync url: expected "
            "<instance>/git/<org>/<workspace>/<project>.git")
    return {"instance": f"{parts.scheme}://{parts.netloc}",
            "org": match.group("org"),
            "workspace": match.group("ws"),
            "project": match.group("proj"),
            "url": url.rstrip("/")}


# ----------------------------------------------------------- the token store

def config_path() -> Path:
    """``~/.agentcad/sync.json`` — beside ``config.json``.

    Beside it, and resolved *through* it, so the ``AGENTCAD_CONFIG`` override
    every test already sets keeps real tokens out of a test run and test tokens
    out of a real home directory (``packages/_git.indexes_root``'s rule).

    A separate file rather than a block in ``config.json`` because this one
    holds secrets and is written 0600: merging them would silently tighten the
    permissions of a file the user edits by hand, or — worse — loosen this one.
    """
    override = os.environ.get("AGENTCAD_SYNC_CONFIG")
    if override:
        return Path(override)
    return user_config.config_path().parent / "sync.json"


def load() -> dict:
    """The stored remotes/clones, or an empty document.

    Total over the file: an unreadable or malformed ``sync.json`` reads as
    "nothing is stored", because the alternative is a CLI that cannot even
    print its own help until a human repairs a JSON file.
    """
    try:
        with open(config_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, RecursionError):
        # `json.loads` raises RecursionError on deep nesting and it is NOT a
        # ValueError (changelog 0181's eleven-site lesson).
        return {"version": 1, "instances": {}, "clones": {}}
    if not isinstance(data, dict):
        return {"version": 1, "instances": {}, "clones": {}}
    data.setdefault("version", 1)
    for key in ("instances", "clones"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def save(document: dict) -> None:
    """Write ``sync.json`` atomically, and **0600 from the first byte**.

    The staging file is created with ``O_EXCL`` at mode 0600 — not written and
    then ``chmod``ed, which leaves a window in which a token is world-readable
    on a shared box — and the name carries a random suffix so two writers lose
    a race instead of interleaving into one another's bytes
    (``ProjectStore._atomic_write``'s lesson, changelog 0181). The directory is
    0700 for the same reason.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, stat.S_IRWXU)
    except OSError:
        pass                                # a shared config dir is the user's
    payload = json.dumps(document, indent=2, sort_keys=True).encode()
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def remember_instance(url: str, token: str, principal: str | None = None) -> str:
    """Store *token* for the instance *url* names. Returns the instance key."""
    if not isinstance(token, str) or not token.strip():
        raise ValueError("a token is required")
    instance = instance_of(url)
    document = load()
    entry = {"token": token.strip()}
    if principal:
        entry["principal"] = principal
    document["instances"][instance] = entry
    save(document)
    return instance


def forget_instance(url: str) -> bool:
    document = load()
    if document["instances"].pop(instance_of(url), None) is None:
        return False
    save(document)
    return True


def token_for(url: str) -> str | None:
    """The stored token for whatever instance *url* belongs to, or ``None``."""
    try:
        instance = instance_of(url)
    except ValueError:
        return None
    entry = load()["instances"].get(instance)
    token = entry.get("token") if isinstance(entry, dict) else None
    return token if isinstance(token, str) and token else None


def remember_clone(project_dir, remote_url: str) -> None:
    document = load()
    document["clones"][str(Path(project_dir).resolve())] = {
        "remote": remote_url}
    save(document)


def remote_for(project_dir) -> str | None:
    """The remote a directory was cloned from.

    ``sync.json`` first (what ``agentcad clone`` recorded), then the repo's own
    ``remote.origin.url`` — which is the authority when a project was cloned on
    another machine, or by plain ``git``, and is why nothing here *depends* on
    the sidecar.
    """
    project_dir = Path(project_dir).resolve()
    entry = load()["clones"].get(str(project_dir))
    if isinstance(entry, dict) and isinstance(entry.get("remote"), str):
        return entry["remote"]
    if not history_dir(project_dir).is_dir():
        return None
    result = local(project_dir, "config", "--get", "remote.origin.url",
                   check=False)
    url = (result.stdout or "").strip()
    return url or None


# ------------------------------------------------------- the credential helper

def credential_answer(query: dict) -> dict | None:
    """``{username, password}`` for a git credential query, or ``None``.

    ``None`` means **silence** on stdout, which is what a helper owes git when
    it has nothing: git then falls through to the next helper (or, with
    ``GIT_TERMINAL_PROMPT=0``, fails with a message the user can act on).
    Answering with an empty password instead would authenticate as nobody and
    turn "you are not signed in" into "your token was rejected".
    """
    protocol = (query.get("protocol") or "").strip()
    host = (query.get("host") or "").strip()
    if not protocol or not host:
        return None
    token = token_for(f"{protocol}://{host}")
    if not token:
        return None
    return {"username": CREDENTIAL_USERNAME, "password": token}


def credential_main(action: str, stdin, stdout) -> int:
    """``agentcad credential <action>`` — git's credential protocol.

    ``get`` answers from ``sync.json``; ``store`` and ``erase`` are accepted
    and do nothing, deliberately: ``agentcad login`` owns that file, and a
    helper that let git write into it would let a redirect target's ``401``
    plant a credential nobody typed. Every unknown action is also a silent
    ``0`` — git adds verbs (``capability``), and a helper that fails on one it
    does not know breaks every call.

    The query is ``key=value`` lines terminated by a blank line or EOF; keys
    that repeat (``wwwauth[]``) keep the last value, which is git's own rule
    and irrelevant to the two keys read here.
    """
    if action != "get":
        return 0
    query: dict[str, str] = {}
    for raw in stdin:
        line = raw.rstrip("\n").rstrip("\r")
        if not line:
            break
        key, sep, value = line.partition("=")
        if sep and key:
            query[key.strip()] = value
    answer = credential_answer(query)
    if answer is None:
        return 0
    stdout.write(f"username={answer['username']}\n"
                 f"password={answer['password']}\n")
    stdout.flush()
    return 0


# ------------------------------------------------------------------- login

def login(url: str, token: str, *, timeout: float = 15.0) -> dict:
    """Verify *token* against the instance at *url*, then store it 0600.

    Verified **first**: storing an unverified token means the next failure is a
    git clone failing with "authentication required", three commands later,
    with nothing pointing back at the typo.

    The probe is ``GET /api/auth/session`` — cheap, and the only kind of
    endpoint that can answer this question. ``/api/health`` cannot: it is in
    the anonymous surface (``security.PUBLIC_PATHS``), so a **wrong** token
    still gets a ``200`` there, merely a trimmed body. A hosted instance
    answers 401 for a bad token and the principal for a good one; a **local**
    instance has no accounts at all and answers 404, which is recorded as
    ``mode: "local"`` rather than treated as a failure — a local instance is a
    legitimate sync target and its answer to "who am I" is honestly "nobody".
    """
    import httpx

    instance = instance_of(url)
    if not isinstance(token, str) or not token.strip():
        raise ValueError("a token is required — mint one with "
                         "`agentcad admin token add <name>` on the instance")
    token = token.strip()
    try:
        response = httpx.get(f"{instance}/api/auth/session", timeout=timeout,
                             headers={"Authorization": f"Bearer {token}"},
                             follow_redirects=False)
    except httpx.HTTPError as exc:
        raise SyncError(f"could not reach {instance}: {exc}") from exc
    if response.status_code in (401, 403):
        raise SyncError(
            f"{instance} refused that token ({response.status_code}). Mint a "
            "new one with `agentcad admin token add <name>` on the instance; "
            "a token is shown once and cannot be recovered.")
    identity: dict = {}
    if response.status_code == 200:
        try:
            body = response.json()
            identity = body if isinstance(body, dict) else {}
        except ValueError:
            identity = {}
    elif response.status_code != 404:
        raise SyncError(f"{instance} answered {response.status_code} for "
                        "/api/auth/session; is that an AgentCAD instance?")
    principal = identity.get("principal")
    mode = identity.get("mode") or "local"
    remember_instance(instance, token, principal if isinstance(principal, str)
                      else None)
    return {"instance": instance, "principal": principal, "mode": mode,
            "insecure": urlsplit(instance).scheme == "http"
            and urlsplit(instance).hostname not in ("127.0.0.1", "localhost",
                                                    "::1")}


# ------------------------------------------------------------------- clone

def clone(url: str, dest, *, token: str | None = None) -> dict:
    """Clone a hosted project into the ``history.py`` layout at *dest*.

    The spike's §A10 sequence, and every step of it is load-bearing:

    ``git clone --bare <url> <dest>/.history``
        ``--separate-git-dir`` would leave a ``.git`` pointer *file* in the
        project directory, which ``history.py`` forbids; a plain clone would
        put the repo at ``<dest>/.git``, which is the same problem spelled
        differently.
    ``core.bare=false`` + ``core.worktree=<dest>``
        what makes the bare repo the project's history repo. ``core.worktree``
        is an **absolute path** (as ``git init`` writes it), so a project
        directory that is later moved needs this rewritten — the same caveat
        every AgentCAD project already carries.
    ``remote.origin.fetch=+refs/heads/*:refs/remotes/origin/*``
        ``--bare`` writes ``+refs/heads/*:refs/heads/*``, which would make the
        next ``fetch`` move **local branches** under the user's feet. This is
        the line that makes a fetch a fetch.
    ``info/exclude``
        written **before** the first status or checkout, or ``.history/``
        itself shows up as untracked and the first snapshot commits the repo
        into itself. The list is ``history._EXCLUDE_LINES``, imported rather
        than re-spelled.
    identity + ``commit.gpgsign=false``
        what ``ProjectHistory._ensure_repo`` sets on ``git init``, and a clone
        never ran ``init``. Without it the first snapshot in the clone fails
        with "Please tell me who you are" — ``history._run`` redirects ``HOME``,
        so the user's global identity is invisible to it, deliberately.
    ``checkout -f <default> --``
        the work tree the app reads. Forced because a bare clone has no index.

    One consequence of ``--bare`` worth stating rather than discovering: every
    server branch arrives as a **real local branch**, not only a
    remote-tracking ref. For AgentCAD that is the right shape — a branch
    belongs to the project, and ``BranchManager`` materializes its working tree
    on demand — so a clone shows the same branch list the server does, and
    :func:`pull` moves the ones with no checkout by ``update-ref``.
    """
    parsed = parse_repo_url(url)
    instance = parsed["instance"]
    dest = Path(dest).expanduser()
    if dest.exists() and any(dest.iterdir()):
        raise SyncError(f"{dest} already exists and is not empty")
    # Stored BEFORE the clone, because the credential helper is what answers
    # the server's 401 mid-clone — and rolled back on failure so a typo'd
    # `--token` neither leaves an unverified credential behind (there was none)
    # NOR overwrites a working token the user already had (there was one, and
    # `previous` is exactly what we put back).
    previous = _instance_entry(instance)
    replaced = False
    if token and token_for(instance) != token:
        remember_instance(instance, token)
        replaced = True
    git_dir = history_dir(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        try:
            run("clone", "--bare", "--", parsed["url"], str(git_dir))
        except SyncError as exc:
            raise _translated(instance, exc) from exc
        config = [
            ("core.bare", "false"),
            ("core.worktree", str(dest.resolve())),
            ("core.logallrefupdates", "true"),
            ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
            ("user.name", "AgentCAD"),
            ("user.email", "agentcad@local"),
            ("commit.gpgsign", "false"),
            # Persisted so a plain `git push` from this directory authenticates
            # the same way `agentcad push` does. It carries no secret: the
            # helper is a command, the token is in the 0600 sidecar.
            ("credential.helper", f"!{helper_command()}"),
        ]
        for key, value in config:
            run("--git-dir", str(git_dir), "config", key, value)
        _write_excludes(git_dir)
        branch = _head_branch(git_dir) or "master"
        # Before writing a single tracked file: refuse a poisoned tree that
        # would plant a hook/config into this clone's own `.history`.
        _assert_no_git_internals(dest, branch)
        local(dest, "checkout", "-f", branch, "--")
        # One incremental fetch, immediately: `--bare` put the server's
        # branches in `refs/heads/*` and left `refs/remotes/` empty, so until
        # this runs there is nothing to compare a branch against and `status`
        # honestly reports every branch as `local_only`. It downloads nothing
        # (every object is already here) and it is also the first call that
        # exercises the credential path under the FLIPPED config, which is
        # where a broken one would otherwise surface as a failing push days
        # later.
        fetch_remote(dest)
        # INSIDE the try: a `verify_layout` that raises must clean up like any
        # other failure, and `remember_clone` must not record a clone that
        # never fully materialized.
        verify_layout(dest)
        remember_clone(dest, parsed["url"])
    except BaseException:
        # A half-made clone is not a project: it would fail every read with a
        # different message than "you have not cloned this yet".
        shutil.rmtree(dest, ignore_errors=True)
        _forget_clone(dest)
        if replaced:
            _restore_instance(instance, previous)
        raise
    return {"path": str(dest), "branch": branch, "remote": parsed["url"],
            "project": parsed["project"], "org": parsed["org"],
            "workspace": parsed["workspace"]}


def _instance_entry(instance: str) -> dict | None:
    """The stored ``instances[instance]`` entry (token + principal), or None."""
    entry = load()["instances"].get(instance)
    return entry if isinstance(entry, dict) else None


def _restore_instance(instance: str, previous: dict | None) -> None:
    """Put an instance entry back exactly as it was — or remove it if there
    was none. The rollback for a `clone --token` whose clone then failed."""
    document = load()
    if previous is None:
        document["instances"].pop(instance, None)
    else:
        document["instances"][instance] = previous
    save(document)


def _forget_clone(project_dir) -> None:
    """Drop any ``clones[...]`` entry for *project_dir* (no-op if absent)."""
    document = load()
    if document["clones"].pop(str(Path(project_dir).resolve()), None) is not None:
        save(document)


def _write_excludes(git_dir: Path) -> None:
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = (exclude.read_text(encoding="utf-8")
               if exclude.is_file() else "")
    present = {line.strip() for line in current.splitlines()}
    missing = [line for line in _EXCLUDE_LINES if line not in present]
    if not missing:
        return
    if current and not current.endswith("\n"):
        current += "\n"
    exclude.write_text(current + "".join(f"{line}\n" for line in missing),
                       encoding="utf-8")


def _head_branch(git_dir: Path) -> str | None:
    """The branch the (cloned) HEAD points at, **validated as a ref name**.

    The name comes from the *server's* advertised HEAD, and it is then handed
    to ``git checkout <branch> --`` as argv. A malicious server that advertised
    ``refs/heads/-evil`` (short: ``-evil``) or a name carrying a revision
    expression would otherwise turn a clone into argument injection. Anything
    ``history.valid_ref_name`` rejects is refused here rather than run; an empty
    HEAD (an unborn remote) is a plain ``None`` and the caller falls back to
    ``master``.
    """
    result = _exec([executable(), *_pins(), "--git-dir", str(git_dir),
                    "symbolic-ref", "--short", "HEAD"],
                   cwd=None, env=_base_env(), timeout=DEFAULT_TIMEOUT,
                   check=False)
    name = (result.stdout or "").strip()
    if not name:
        return None
    if not valid_ref_name(name):
        raise SyncError(
            f"the server advertised an unusable default branch {name!r} — a "
            "ref name may not start with '-' or carry a revision expression. "
            "Refusing rather than passing it to git as an argument.")
    return name


def verify_layout(project_dir) -> None:
    """Assert the result of :func:`clone` is a ProjectHistory repo.

    Four properties, checked rather than assumed, because every one of them is
    a way the flip can half-work and leave something that looks like a project
    until the first snapshot: no ``.git`` in the tree, a GIT_DIR at
    ``.history`` with a ``HEAD`` (``ProjectHistory._has_repo``'s own test), a
    non-bare repo whose work tree is the project directory, and a
    ``project.json`` on disk.
    """
    project_dir = Path(project_dir)
    git_dir = history_dir(project_dir)
    problems = []
    if (project_dir / ".git").exists():
        problems.append(".git exists in the project directory")
    if not (git_dir / "HEAD").is_file():
        problems.append("no .history/HEAD")
    if not (project_dir / "project.json").is_file():
        problems.append("no project.json in the work tree")
    if not problems:
        bare = local(project_dir, "rev-parse", "--is-bare-repository",
                     check=False).stdout.strip()
        if bare != "false":
            problems.append(f"core.bare is {bare!r}")
        tree = local(project_dir, "rev-parse", "--show-toplevel",
                     check=False).stdout.strip()
        if tree and Path(tree).resolve() != project_dir.resolve():
            problems.append(f"work tree resolves to {tree}")
    if problems:
        raise SyncError(
            f"{project_dir} is not an AgentCAD project history repo: "
            + "; ".join(problems))


# -------------------------------------------------------------------- refs

def _for_each_ref(project_dir, pattern: str) -> dict[str, str]:
    result = local(project_dir, "for-each-ref", "--format=%(refname) %(objectname)",
                   pattern, check=False)
    out: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        ref, _, oid = line.strip().partition(" ")
        if ref and oid:
            out[ref] = oid
    return out


def _counts(project_dir, left: str, right: str) -> tuple[int, int]:
    """``(ahead, behind)`` of *left* relative to *right*."""
    result = local(project_dir, "rev-list", "--left-right", "--count",
                   f"{left}...{right}", check=False)
    parts = (result.stdout or "").split()
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _worktrees(project_dir) -> dict[str, Path]:
    """``{branch: working tree}`` from ``git worktree list``.

    The main tree (the project directory) plus every linked branch tree under
    ``.history/trees/``. Read from git rather than from
    ``BranchManager``'s sidecar so a pull works on a clone that no service has
    opened yet.
    """
    project_dir = Path(project_dir)
    git_dir = history_dir(project_dir)
    result = local(project_dir, "worktree", "list", "--porcelain", check=False)
    trees: dict[str, Path] = {}
    path: Path | None = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):].strip())
            # git reports the MAIN worktree as the GIT_DIR here — it derives it
            # by stripping a trailing `/.git`, finds `/.history` instead, and
            # ignores `core.worktree` (the same derivation that makes
            # `receive.denyCurrentBranch=updateInstead` unusable against this
            # layout, spike §A2). The project directory is the answer, and
            # translating it here is what keeps every caller from having to
            # know that.
            if path.resolve() == git_dir.resolve():
                path = project_dir
        elif line.startswith("branch ") and path is not None:
            ref = line[len("branch "):].strip()
            if ref.startswith("refs/heads/"):
                trees[ref[len("refs/heads/"):]] = path
    return trees


def dirty_paths(project_dir) -> list[str]:
    """Uncommitted **tracked** changes in the main work tree.

    ``-uno``: untracked files are derived data and imports the excludes do not
    know about, and neither a pull nor a push has any business refusing over
    them.
    """
    result = local(project_dir, "status", "--porcelain", "-uno", check=False)
    return [line[3:].strip() for line in (result.stdout or "").splitlines()
            if line.strip()]


def status(project_dir, *, remote: str = "origin", fetch: bool = False) -> dict:
    """Ahead/behind per branch against ``origin``, plus unpushed tags.

    Pure and offline by default — this is what PRD-005's ``sync_status`` tool
    calls, and a tool that opened a network connection to answer a status
    question would make every project list slow and every offline session an
    error. ``fetch=True`` is the CLI's opt-in.

    Branch states: ``up_to_date``, ``ahead``, ``behind``, ``diverged``,
    ``local_only`` (no counterpart on the server yet) and — for a remote branch
    with no local counterpart — ``remote_only``.
    """
    project_dir = Path(project_dir)
    verify_layout(project_dir)
    if fetch:
        fetch_remote(project_dir, remote=remote)
    heads = _for_each_ref(project_dir, "refs/heads/")
    tracking = _for_each_ref(project_dir, f"refs/remotes/{remote}/")
    prefix = f"refs/remotes/{remote}/"
    branches = []
    for ref, oid in sorted(heads.items()):
        name = ref[len("refs/heads/"):]
        if name.startswith(INTERNAL_BRANCH_PREFIX):
            continue
        counterpart = f"{prefix}{name}"
        if counterpart not in tracking:
            branches.append({"branch": name, "state": "local_only",
                             "ahead": 0, "behind": 0, "head": oid,
                             "remote_head": None})
            continue
        ahead, behind = _counts(project_dir, ref, counterpart)
        state = ("diverged" if ahead and behind else
                 "ahead" if ahead else "behind" if behind else "up_to_date")
        branches.append({"branch": name, "state": state, "ahead": ahead,
                         "behind": behind, "head": oid,
                         "remote_head": tracking[counterpart]})
    known = {b["branch"] for b in branches}
    for ref, oid in sorted(tracking.items()):
        name = ref[len(prefix):]
        if name == "HEAD" or name in known:
            continue
        branches.append({"branch": name, "state": "remote_only", "ahead": 0,
                         "behind": 0, "head": None, "remote_head": oid})
    local_tags = _for_each_ref(project_dir, "refs/tags/")
    return {
        "path": str(project_dir),
        "remote": remote_for(project_dir),
        "branches": branches,
        "tags": sorted(ref[len("refs/tags/"):] for ref in local_tags),
        "dirty": dirty_paths(project_dir),
        "diverged": sorted(b["branch"] for b in branches
                           if b["state"] == "diverged"),
    }


# --------------------------------------------------------------------- push

def push_refspecs(project_dir) -> list[str]:
    """The refspecs ``agentcad push`` sends. Normally exactly two.

    ``refs/heads/*:refs/heads/*`` and ``refs/tags/*:refs/tags/*`` — "push
    everything, delete nothing, force nothing", and each half of that is a
    decision:

    * **not** ``--mirror``: a mirror push is ``+refs/*:refs/*``, which forces
      *and* propagates deletions. The server refuses both (the pre-receive hook
      closes FR9's three holes), so a mirror push would be a whole-push
      rejection every time somebody deleted a local branch.
    * **not** ``--all``/``--follow-tags``: ``--follow-tags`` carries **annotated
      tags only** (spike §A6, measured — ``light-1`` did not travel). AgentCAD's
      own tags are annotated today, and depending on that would make a
      lightweight tag somebody made by hand vanish silently.
    * **no leading ``+``**, ever: without it a non-fast-forward is refused by
      the *client*, before a byte reaches the network, and the user is told to
      pull. With it, the server's hook refuses instead — the same outcome, one
      round trip later, with a scarier message.
    * a wildcard refspec pushes only refs that **exist locally**: a branch that
      is on the server and not here is simply not mentioned, so "push
      everything" never means "delete what I do not have".

    The one exception is :data:`INTERNAL_BRANCH_PREFIX`: ``pull`` parks the
    fetched other side in a real local branch for the merge machinery, and that
    is bookkeeping, not somebody's work. When one exists (a merge is staged, or
    was aborted without cleanup) the wildcard is expanded to explicit per-branch
    refspecs that leave it out, rather than publishing a branch nobody made.
    """
    heads = [ref[len("refs/heads/"):]
             for ref in _for_each_ref(project_dir, "refs/heads/")]
    internal = [name for name in heads
                if name.startswith(INTERNAL_BRANCH_PREFIX)]
    if not internal:
        return ["refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*"]
    return [f"refs/heads/{name}:refs/heads/{name}"
            for name in sorted(heads) if name not in internal
            ] + ["refs/tags/*:refs/tags/*"]


def push(project_dir, *, remote: str = "origin") -> dict:
    """Push every branch and tag. Never forces, never deletes.

    A refusal is a :class:`SyncError` carrying the server's own words: the
    pre-receive hook's ``agentcad: refs/heads/master diverged — pull and merge,
    never force`` is the message this command exists to deliver, and the caller
    prints :attr:`SyncError.remote` verbatim.
    """
    project_dir = Path(project_dir)
    verify_layout(project_dir)
    url = remote_for(project_dir)
    if not url:
        raise SyncError(f"{project_dir} has no {remote!r} remote; clone it "
                        "with `agentcad clone` or add one with `git remote add`")
    refspecs = push_refspecs(project_dir)
    result = run("--git-dir", str(history_dir(project_dir)),
                 "push", "--porcelain", "--", remote, *refspecs,
                 cwd=project_dir, check=False)
    if result.returncode != 0:
        raise SyncError(
            _push_message(url, result.stdout or "", result.stderr or ""),
            returncode=result.returncode, stderr=result.stderr or "")
    return {"remote": url, "refspecs": refspecs,
            "updated": _porcelain_updates(result.stdout or "")}


def _push_message(url: str, stdout: str, stderr: str) -> str:
    """One sentence a person can act on, from three different refusals.

    They are genuinely different facts and used to read as one exit status:

    * the **server** refused (the pre-receive hook) — its own words win,
      always; they were written to be read;
    * the **client** refused, before a byte left the machine: a wildcard
      refspec with no leading ``+`` will not push a non-fast-forward, so git
      says ``[rejected] … (non-fast-forward)`` in its **porcelain stdout** and
      leaves stderr with a hint about ``git push --help``. Reading stdout is
      what makes this case nameable at all;
    * nobody is **signed in** — see :func:`_auth_message`.
    """
    lines = remote_lines(stderr)
    if lines:
        return lines[0]
    auth = _auth_message(url, stderr)
    if auth:
        return auth
    rejected = [row for row in _porcelain_rows(stdout) if row["flag"] == "!"]
    if rejected or "non-fast-forward" in stderr or "fetch first" in stderr:
        named = ", ".join(sorted(row["ref"] for row in rejected)) or "a branch"
        return (f"the server has commits you do not ({named}): pull and merge "
                "them with `agentcad pull`, never force")
    return stderr.strip().splitlines()[-1] if stderr.strip() else "push failed"


def _auth_message(url: str, stderr: str) -> str | None:
    """"You are not signed in", when that is what git's message really means.

    ``fatal: could not read Username for 'https://…': terminal prompts
    disabled`` is what a user meets when **no helper had an answer** — which
    for this CLI means exactly one thing: there is no token for that instance.
    (A *wrong* token reads differently: the helper answers, the server returns
    401, and the server's own message reaches the user — spike §A9 P4/P4b.)
    Left untranslated it is a message about a terminal, for a command that
    never wanted one.
    """
    text = stderr or ""
    if "terminal prompts disabled" not in text and \
            "could not read Username" not in text:
        return None
    try:
        instance = instance_of(url)
    except ValueError:
        instance = url
    return (f"not signed in to {instance}: run `agentcad login {instance}` "
            "with a token from that instance (`agentcad admin token add "
            "<name>` there)")


def _porcelain_rows(stdout: str) -> list[dict]:
    """``git push --porcelain`` rows as ``{flag, ref, summary}``.

    Machine-readable by construction: ``<flag>\\t<from>:<to>\\t<summary>``, with
    ``=`` for up to date, ``*`` for a newly created ref and ``!`` for one the
    client or the server refused.
    """
    rows = []
    for line in stdout.splitlines():
        if line.startswith(("To ", "Done")) or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        flag, refs, summary = fields[0], fields[1], fields[2]
        rows.append({"flag": flag, "ref": refs.partition(":")[2] or refs,
                     "summary": summary})
    return rows


def _porcelain_updates(stdout: str) -> list[dict]:
    """The rows that changed something — everything but ``=`` (up to date)."""
    return [row for row in _porcelain_rows(stdout) if row["flag"] != "="]


# --------------------------------------------------------------------- pull

def _translated(url: str, exc: SyncError) -> SyncError:
    """*exc*, or a signed-out refusal that says so (see :func:`_auth_message`)."""
    message = _auth_message(url, exc.stderr) or str(exc)
    remote = remote_lines(exc.stderr)
    if remote:
        message = remote[0]
    return SyncError(message, returncode=exc.returncode, stderr=exc.stderr)


def fetch_remote(project_dir, *, remote: str = "origin") -> None:
    """Fetch branches and tags. Touches no local branch and no work tree.

    ``--tags`` (not ``--force``): the server refuses a tag rewrite, so a tag
    that would clobber a local one of the same name is a genuine disagreement
    and git's own refusal is the right answer. ``--prune`` keeps the
    remote-tracking refs honest — it removes ``refs/remotes/origin/x`` for a
    branch that no longer exists on the server, which is bookkeeping, not
    anybody's work: the local ``refs/heads/x`` is untouched.
    """
    try:
        run("--git-dir", str(history_dir(Path(project_dir))),
            "fetch", "--prune", "--tags", "--", remote,
            cwd=Path(project_dir))
    except SyncError as exc:
        raise _translated(remote_for(project_dir) or remote, exc) from exc


def pull(project_dir, *, remote: str = "origin", merger=None) -> dict:
    """Fetch, then bring every branch forward — fast-forward or merge.

    Three outcomes per branch, and the third is the whole point:

    ``up_to_date``/``ahead``/``local_only``
        nothing to do; ``ahead`` is a hint to push.
    ``behind``
        a genuine fast-forward: the local branch is an ancestor of the
        server's, so moving it invents nothing. It is done in the branch's own
        **working tree** when it has one (``merge --ff-only``, which refuses a
        dirty tree) and by ``update-ref`` when it does not — never by
        ``reset --hard``, which would also discard uncommitted work.
    ``diverged``
        handed to *merger* — :func:`merge_diverged`, which drives PRD-001's
        ``MergeOrchestrator``: staged, kernel-validated, two-parent, with the
        conflict payload the UI shows. With no merger the branch is reported
        ``diverged`` and **nothing is touched**; there is no path through this
        function that resets, forces or discards.

    A dirty main work tree refuses the whole pull before anything is fetched
    into a branch: AgentCAD commits on every ``project_changed``, so a dirty
    tree means an edit that never made it to a snapshot, and a fast-forward
    would take it with it.
    """
    project_dir = Path(project_dir)
    verify_layout(project_dir)
    dirty = dirty_paths(project_dir)
    if dirty:
        raise SyncError(
            f"{project_dir} has {len(dirty)} uncommitted change"
            f"{'' if len(dirty) == 1 else 's'}; commit or discard them before "
            "pulling (the app snapshots on every change — this is an edit made "
            "outside it)",
        )
    fetch_remote(project_dir, remote=remote)
    before = status(project_dir, remote=remote)
    trees = _worktrees(project_dir)
    results = []
    for entry in before["branches"]:
        name, state = entry["branch"], entry["state"]
        if state in ("up_to_date", "ahead", "local_only", "remote_only"):
            results.append({**entry, "action": "none"})
            continue
        if state == "behind":
            _fast_forward(project_dir, name, f"refs/remotes/{remote}/{name}",
                          trees.get(name))
            results.append({**entry, "action": "fast_forward"})
            continue
        if merger is None:
            results.append({**entry, "action": "diverged"})
            continue
        outcome = merger(name, f"refs/remotes/{remote}/{name}")
        results.append({**entry, "action": "merged", "merge": outcome})
    return {"path": str(project_dir), "remote": before["remote"],
            "branches": results,
            "conflicts": [r for r in results
                          if isinstance(r.get("merge"), dict)
                          and "error" in r["merge"]],
            "diverged": [r["branch"] for r in results
                         if r["action"] == "diverged"]}


def _fast_forward(project_dir, branch: str, remote_ref: str,
                  tree: Path | None) -> None:
    """Move *branch* to *remote_ref*, which is known to be a descendant.

    Three paths, because a branch here may or may not have a checkout and the
    two kinds of checkout are addressed differently:

    * **no working tree** — the ref moves on its own (``update-ref``) and
      ``BranchManager`` materializes the tree on demand, as it always does;
    * **the project directory** (the default branch's tree) — ``merge
      --ff-only`` through :func:`local`, which supplies ``--git-dir``/
      ``--work-tree``: there is no ``.git`` in a project, so a bare ``-C`` there
      discovers no repository at all;
    * **a linked branch tree** under ``.history/trees/`` — that one *does* carry
      a ``.git`` file, so ``-C <tree>`` is the correct (and only) way to name
      its own admin directory.

    ``merge --ff-only`` and never ``reset --hard``: it moves the ref and the
    files, and it is unable to discard anything, refusing a dirty tree instead.
    """
    project_dir = Path(project_dir)
    # Refuse a fetched tip that carries our git internals — for every path,
    # including the `update-ref` one, whose tree `BranchManager` materializes
    # later without a second look. A poisoned remote never advances a ref here.
    _assert_no_git_internals(project_dir, remote_ref)
    if tree is None:
        local(project_dir, "update-ref", f"refs/heads/{branch}", remote_ref)
        return
    if Path(tree).resolve() == project_dir.resolve():
        local(project_dir, "merge", "--ff-only", remote_ref)
        return
    git_dir = history_dir(project_dir)
    result = _exec([executable(), *_pins(), "-C", str(tree),
                    "merge", "--ff-only", remote_ref],
                   cwd=tree,
                   # `local()`'s hermetic environment, spelled out because a
                   # linked worktree's GIT_DIR is its own admin directory under
                   # `.history/worktrees/`, not `.history` itself.
                   env={**_base_env(), "GIT_CONFIG_NOSYSTEM": "1",
                        "HOME": str(git_dir),
                        "XDG_CONFIG_HOME": str(git_dir / "xdg")},
                   timeout=DEFAULT_TIMEOUT, check=False)
    if result.returncode != 0:
        raise SyncError(
            f"could not fast-forward {branch!r}: "
            f"{(result.stderr or result.stdout or '').strip().splitlines()[-1]}",
            returncode=result.returncode, stderr=result.stderr or "")


def merge_diverged(service, proj: str, branch: str, remote_ref: str, *,
                   allow_invalid: bool = False) -> dict:
    """Merge the fetched *remote_ref* into local *branch* — PRD-001's merge.

    This is the entry point a divergent pull drives, and it is deliberately
    **not** a second merge implementation: ``MergeOrchestrator.merge`` is the
    one the UI, the tools and the proposals all go through, so a pull gets the
    structure-aware ``project.json`` driver, the staged worktree, the kernel
    validation pass and the compare-and-swap ref update for free, and a
    conflict comes back in the exact payload the UI renders.

    It merges **branch into branch** (``_branch`` refuses anything that is not
    a local branch), so the fetched side is first given a real local branch
    under :data:`INTERNAL_BRANCH_PREFIX`. That branch is bookkeeping: it is
    deleted the moment the merge lands, kept while conflicts are outstanding
    (the staged merge names it), and never pushed (:func:`push_refspecs`).

    Returns ``MergeOrchestrator.merge``'s result — including its
    ``{"error": {"type": "merge_conflict", …}}`` payload, which is a *value*
    here and not an exception, exactly as the tool layer receives it.
    """
    incoming = f"{INTERNAL_BRANCH_PREFIX}{branch}"
    canonical = service.store.canonical_path_of(proj)
    head = service.history.resolve_ref(canonical, remote_ref)
    if head is None:
        raise SyncError(f"cannot resolve {remote_ref!r} in {canonical}")
    # BELT (PRD-005): the fetched tip itself must not carry our git internals.
    # ``MergeOrchestrator`` would materialize the incoming side into a scratch
    # worktree and, on landing, ``reset --hard`` the merge into a live tree —
    # bypassing the checkout/ff belts. Refuse BEFORE parking it as
    # ``incoming/<branch>``: nothing is created, the local tree is untouched.
    offending = git_internals_in_tree(canonical, head)
    if offending:
        raise SyncError(
            f"{_MERGE_INTERNALS_MSG} ({', '.join(offending[:5])}). "
            "Nothing was changed.")
    _reset_incoming(service, proj, canonical, incoming, head)
    try:
        result = service.merges.merge(proj, incoming, branch,
                                      allow_invalid=allow_invalid)
    except ValidationError as exc:
        # The staged-merge belt (``merge.py::_assert_no_git_internals``) fired:
        # the RESULT tree — which a merge can synthesize a ``.history`` path
        # into, present in NEITHER tip — would land in the work tree. It refused
        # before staging or moving a ref, so nothing landed; drop the scratch
        # branch we parked and refuse the whole pull, exactly as a failed merge
        # leaves the local tree unchanged.
        planted = (exc.details or {}).get("git_internals")
        if planted:
            _forget_incoming(service, proj, incoming)
            raise SyncError(
                f"{_MERGE_INTERNALS_MSG} ({', '.join(planted[:5])}). "
                "Nothing was changed.") from exc
        raise
    if isinstance(result, dict) and "error" not in result:
        # The merge landed (or was already up to date): the scratch branch has
        # served its purpose and a project's branch list should not grow one
        # entry per pull.
        try:
            service.branches.delete(proj, incoming)
        except Exception:                       # noqa: BLE001 — bookkeeping
            pass
    return result


def _reset_incoming(service, proj: str, canonical: Path, incoming: str,
                    head: str) -> None:
    """Point the scratch branch at *head*, however it was left last time."""
    existing = {b["name"] for b in service.history.branches(canonical)}
    if incoming in existing:
        try:
            service.branches.delete(proj, incoming)
        except Exception:                       # noqa: BLE001
            # A staged merge holds it, or its tree will not commit. Leave it
            # and let `merge` refuse with its own (precise) message.
            return
    service.branches.create(proj, incoming, head)


def _forget_incoming(service, proj: str, incoming: str) -> None:
    """Undo the scratch ``incoming/<branch>`` a refused merge left parked.

    The staged-merge belt refuses BEFORE anything is staged or any ref moves,
    so the only residue is the branch (and its worktree) :func:`_reset_incoming`
    created. Abort any staged merge first for good measure, then delete the
    branch — best-effort, because a pull that is already refusing must not turn
    a failed cleanup into a second, more confusing error.
    """
    try:
        service.merges.abort(proj)
    except Exception:                           # noqa: BLE001 — bookkeeping
        pass
    try:
        service.branches.delete(proj, incoming)
    except Exception:                           # noqa: BLE001 — bookkeeping
        pass
