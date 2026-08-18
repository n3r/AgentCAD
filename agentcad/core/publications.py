"""Publication state: share links and the customizer capability, in the state dir.

One atomically-written JSON document under ``<state-dir>/publications/``::

    store.json         {"publications": {pub_id: record}}
    scripts/<sha>.py   the pinned script bytes, content-addressed (PRD-011)
    build/             a project store the muzzled build service is rooted in

**Why this is beside identity state and not project data.** A share link is a
*capability*: it outlives a branch switch, a ``project_restore`` and even
deleting the source part, and a token is not a path. So — exactly as
``core/appmode.state_dir`` argued for the auth store — it lives under the state
dir, derived from ``config_path().parent`` and never from ``--projects-dir``.
``AgentCADService.__init__`` never constructs it, so PRD-004/011's ephemeral
services are unaffected *by construction*, and a ``--projects-dir`` isolation
test cannot make a publication leak into a user ``ProjectStore``.

**Why JSON, atomic writes and an flock — the ``authstore`` shape verbatim.**
This is tens of records, ``agentcad admin`` (and a future takedown CLI) is a
routine *second process* writing the file while the server holds it in memory
(the ``LocalIndex._index_scope`` situation), and ``tar`` of the volume is a
correct backup precisely because every write is an ``os.replace``. The staleness
cache keyed on ``(st_mtime_ns, st_size, st_ino)`` is what makes that second
process's write visible with no restart.

**Why a token is a store-backed digest, not a signed capability.** The store is
the authority, so revocation is *immediate* — the reason PRD-005a rejected JWTs.
The URL secret is 256 bits of ``secrets.token_urlsafe`` and only its
``sha256`` is stored; resolving one costs a hash and an ``hmac.compare_digest``,
never a scan. Revoked, expired and unknown tokens are **one indistinguishable
``None``** — no existence oracle over what was ever published.

Nothing here imports geometry; this module joins the fresh-interpreter probe.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:  # pragma: no cover - exercised by the portability suite, not by CI's mac
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

STORE = "store.json"
LOCK_FILE = ".lock"

#: A share token is ``shr_<pub_id8>_<secret43>``. The id is hex, so the *first*
#: two underscores always delimit it; the secret's own alphabet includes ``_``,
#: which is why every split here is ``split("_", 2)`` and never ``split("_")``
#: — the ``authstore`` bearer lesson, kept.
TOKEN_PREFIX = "shr"
_PUB_ID_BYTES = 4                          # 8 hex characters
SHARE_TOKEN_RE = re.compile(r"^shr_[0-9a-f]{8}_[A-Za-z0-9_-]{20,}$")

#: The three coarse counters, no visitor PII — the popularity signal PRD-031
#: inherits (design Decision 5).
_COUNTERS = ("views", "rebuilds", "downloads")


def _now() -> float:
    """Module-level indirection so a test can move the clock without patching
    ``time`` under a running kernel process."""
    return time.time()


def _mint_secret() -> str:
    """43 url-safe characters = 256 bits, from ``secrets`` — unguessable, the
    reason the token is stored as a digest rather than hashed with a KDF."""
    return secrets.token_urlsafe(32)


def _digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------- lock registry

class _Guard:
    """Per-root lock state, shared by every store on that root.

    Registry-scoped rather than instance-scoped for ``_index_scope``'s reason:
    two stores on one root inside one process must contend on the *same*
    in-process lock and must not each take an ``flock`` (which is per open file
    description, so a second one in the same process blocks against the first
    forever). Hence the depth counter: the outermost scope owns the handle.
    """

    __slots__ = ("lock", "depth", "handle")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.depth = 0
        self.handle = None


_guard_registry_lock = threading.Lock()
_guards: dict[str, _Guard] = {}


def _guard_for(root: Path) -> _Guard:
    key = str(root)
    with _guard_registry_lock:
        guard = _guards.get(key)
        if guard is None:
            guard = _guards[key] = _Guard()
        return guard


class PublicationStore:
    """Publications behind one lock, with atomic writes — the ``AuthStore`` shape."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        # 0700: a publication record is not another local account's business,
        # and the mode is set at creation rather than repaired later.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._guard = _guard_for(self.root.resolve())
        #: name -> ((mtime_ns, size, inode), parsed) — what makes a second
        #: process's write visible with no restart.
        self._cache: dict[str, tuple[tuple, dict]] = {}

    # ------------------------------------------------------------- plumbing

    @contextmanager
    def _scope(self):
        """Serialise a read-modify-write in-process **and** across processes.

        The flock is advisory and best-effort, as it is in ``authstore``: on a
        filesystem that does not support it, or a platform with no ``fcntl``,
        the in-process lock still holds and the cross-process case degrades to
        what it was.
        """
        guard = self._guard
        with guard.lock:
            outermost = guard.depth == 0
            if outermost and fcntl is not None:
                handle = open(self.root / LOCK_FILE, "a+b")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except OSError:
                    handle.close()          # unsupported filesystem: degrade
                    handle = None
                guard.handle = handle
            guard.depth += 1
            try:
                yield
            finally:
                guard.depth -= 1
                if guard.depth == 0 and guard.handle is not None:
                    try:
                        fcntl.flock(guard.handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        guard.handle.close()
                        guard.handle = None

    def _path(self, name: str) -> Path:
        return self.root / (name if name.endswith(".json") else f"{name}.json")

    def _read(self, name: str = "store", *, fresh: bool = False) -> dict:
        """Parse a document, reusing the cached parse while its stat is equal.

        ``fresh=True`` for every read that a write will follow: the stat key is
        a good discriminator across processes but not a proof, and a
        read-modify-write from a stale parse would drop the other writer's row.
        """
        path = self._path(name)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            self._cache.pop(name, None)
            return {}
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        if not fresh:
            cached = self._cache.get(name)
            if cached is not None and cached[0] == key:
                return cached[1]
        try:
            doc = json.loads(path.read_bytes().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # Never treat garbage as empty: that would turn a corrupt store into
            # an instance with no publications, which the next create would
            # cheerfully repopulate over the top of. Fail loudly.
            raise
        if not isinstance(doc, dict):
            raise ValueError(f"{path} is not a JSON object")
        self._cache[name] = (key, doc)
        return doc

    def _write(self, name: str, doc: dict) -> None:
        """Atomic, 0600, staged through a **random** name.

        The random staging name is the fixed-``.tmp`` trap (changelog 0181): a
        shared staging file lets two writers interleave their bytes and each
        ``os.replace`` the mixture into place — corruption, not a lost update.
        Written with a pinned ``newline`` implicitly (``json.dumps`` emits no
        ``\\r``); the bytes are what a content id would hash if one ever did.
        """
        path = self._path(name)
        data = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")
        tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        st = os.stat(path)
        self._cache[name] = ((st.st_mtime_ns, st.st_size, st.st_ino), doc)

    def _publications(self, *, fresh: bool = False) -> dict:
        doc = self._read(STORE, fresh=fresh)
        pubs = doc.get("publications")
        return pubs if isinstance(pubs, dict) else {}

    # --------------------------------------------------------------- writes

    def create(self, *, share_scope: str, project: str, part_id: str | None,
               ref: dict, script_sha: str, settings: dict, created_by: str,
               default_variant_key: str,
               material: str | None = None) -> tuple[str, str]:
        """Mint a publication and its one-time token.

        Returns ``(pub_id, share_token)``; the plaintext token is returned
        **once** and only its digest is stored.
        """
        secret = _mint_secret()
        with self._scope():
            pubs = dict(self._publications(fresh=True))
            pub_id = secrets.token_hex(_PUB_ID_BYTES)
            while pub_id in pubs:
                pub_id = secrets.token_hex(_PUB_ID_BYTES)
            token = f"{TOKEN_PREFIX}_{pub_id}_{secret}"
            pubs[pub_id] = {
                "pub_id": pub_id,
                "token_digest": _digest(token),
                "scope": share_scope,
                "project": project,
                "part_id": part_id,
                "ref": ref,
                "script_sha": script_sha,
                "material": material,
                "settings": dict(settings),
                "created_by": created_by,
                "created": _now(),
                "revoked": False,
                "default_variant_key": default_variant_key,
                "counters": {c: 0 for c in _COUNTERS},
            }
            self._write(STORE, {"publications": pubs})
        return pub_id, token

    def revoke(self, pub_id: str, by: str) -> None:
        """Flag revoked (rather than delete) so a listing can still show what
        was revoked and when. Immediate — the store is the authority. Silent on
        an unknown id: revocation must never be an existence oracle."""
        with self._scope():
            pubs = dict(self._publications(fresh=True))
            row = pubs.get(pub_id)
            if row is None or row.get("revoked"):
                return
            pubs[pub_id] = {**row, "revoked": True, "revoked_at": _now(),
                            "revoked_by": by}
            self._write(STORE, {"publications": pubs})

    def bump(self, pub_id: str, field: str, n: int = 1) -> None:
        """Increment one coarse counter, best-effort. Silent on an unknown id
        or field: a counter is popularity signal, not a correctness invariant,
        and an asset fetch must never 500 because a record just went away."""
        if field not in _COUNTERS:
            return
        with self._scope():
            pubs = dict(self._publications(fresh=True))
            row = pubs.get(pub_id)
            if row is None:
                return
            counters = dict(row.get("counters") or {})
            counters[field] = int(counters.get(field, 0)) + int(n)
            pubs[pub_id] = {**row, "counters": counters}
            self._write(STORE, {"publications": pubs})

    # ---------------------------------------------------------------- reads

    def resolve(self, share_token: str) -> dict | None:
        """The live record for a token, else ``None``.

        Revoked, expired and unknown are **one** ``None`` — no oracle. Never
        raises: it is reached by an anonymous request carrying an attacker's
        path, and a traceback here would be a 500 that says the token was
        interesting.
        """
        if not isinstance(share_token, str) or not SHARE_TOKEN_RE.match(share_token):
            return None
        parts = share_token.split("_", 2)   # the secret's alphabet includes "_"
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
            return None
        pub_id = parts[1]
        row = self._publications().get(pub_id)
        if not isinstance(row, dict):
            # Compare anyway: an early return would make "that id exists"
            # measurable. sha256 is cheap, so the symmetry is free.
            hmac.compare_digest(_digest(share_token), _digest("no such token"))
            return None
        if not hmac.compare_digest(_digest(share_token),
                                   str(row.get("token_digest", ""))):
            return None
        if row.get("revoked"):
            return None
        expires = (row.get("settings") or {}).get("expires")
        if expires is not None and _now() >= float(expires):
            return None
        return dict(row)

    def get(self, pub_id: str) -> dict | None:
        """The record for a management handle, regardless of live-ness — the
        owner-facing read (revoked links are still theirs to see)."""
        row = self._publications().get(pub_id)
        return dict(row) if isinstance(row, dict) else None

    def list_for(self, created_by: str, project: str) -> list[dict]:
        """The owner's links in one project, newest first. **Never the digest.**"""
        rows = [self._public(row) for row in self._publications().values()
                if isinstance(row, dict)
                and row.get("created_by") == created_by
                and row.get("project") == project]
        return sorted(rows, key=lambda r: (r.get("created") or 0), reverse=True)

    @staticmethod
    def _public(row: dict) -> dict:
        """What the owner listing shows: everything but the ``token_digest``.

        The digest is the one field a listing must never carry — it is the
        stored half of the capability, and although it is not the raw secret,
        it is data an owner-facing endpoint has no reason to echo.
        """
        return {k: v for k, v in row.items() if k != "token_digest"}

    # ------------------------------------------------------------- locations

    def script_path(self, script_sha: str) -> Path:
        """``scripts/<script_sha>.py`` — the pinned, content-addressed bytes."""
        return self.root / "scripts" / f"{script_sha}.py"

    def build_root(self) -> Path:
        """``build/`` — the projects root the muzzled build service is rooted in."""
        return self.root / "build"
