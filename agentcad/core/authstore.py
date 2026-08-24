"""Identity state: accounts, enrolments, browser sessions and agent tokens.

Four atomically-written JSON documents under ``<state-dir>/auth/``::

    users.json        {handle: {role, disabled, created, password: {...}|null}}
    enrolments.json   {sha256(token): {handle, expires, used}}
    sessions.json     {sha256(secret): {handle, device, created, last_seen, expires}}
    tokens.json       {token_id: {name, role, digest, created, expires, revoked,
                                  scope?: {org, workspace, projects, role}}}

**Why JSON and not SQLite.** PRD-005 specifies "per-instance SQLite (WAL)" and
names *audit volume and membership queries* as the motivation — and the audit
log is deferred out of this slice. What is left is tens of records. Against
that: this repository contains zero SQLite (every store is atomic JSON, JSONL
or git), WAL sidecars complicate the backup story, and a migration mechanism
would be new machinery for four documents. `tar` of the volume is a correct
backup precisely because every write here is an ``os.replace``.

**Why the locking is two layers.** ``agentcad admin user add`` run through
``docker compose exec`` is a **second process** writing these files while the
server holds them in memory. That is ``LocalIndex._index_scope``'s situation
exactly (``core/packages/indexes.py``), and it gets the same answer: a
``threading.RLock`` for two threads in one process, ``fcntl.flock`` on a lock
file beside the documents for two processes, and a documented degradation
where ``fcntl`` does not exist.

**Why passwords are scrypt and secrets are not.** A password is low-entropy
and human-chosen, so the cost of guessing has to be manufactured: scrypt at
n=2**15 is 63 ms measured, which is affordable *once, on the login path*. A session
or token secret is 256 bits of ``secrets.token_urlsafe`` — there is nothing to
brute-force, so it is stored as a plain SHA-256 digest and resolving one costs
microseconds instead of putting 100 ms on every agent request. The asymmetry
looks like an inconsistency and is not; it is recorded here so it is not
"fixed".

Nothing in this module imports geometry, and no ``AgentCADService``
constructs or reads it — identity is app-layer state, which is what leaves
PRD-004/011's ephemeral services unaffected by construction.
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

from .model import ID_RE, ConflictError, NotFoundError, ValidationError

try:  # pragma: no cover - exercised by the portability suite, not by CI's mac
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

#: Handles compose into a client identity as ``user:<handle>/<device>``, and
#: ``locks.check_client_id`` **refuses rather than truncates** past 64
#: characters. ``user:`` (5) + handle (≤32) + ``/browser:`` (9) + 8 = ≤54.
#: The 32 is therefore arithmetic, not taste.
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")

#: Token *names* are labels for humans reading `admin token list`; they compose
#: as ``agent:<name>`` (6 + name ≤ 64).
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")

ROLES = ("admin", "member")

#: PRD-005 FR3's ladder, for a token's **scope** — a different vocabulary from
#: :data:`ROLES` above, which is 005a's instance role and stays what it was.
#: Kept as a literal rather than imported from ``tenancy``, because ``tenancy``
#: imports *this* module (its lock guard) and the cycle would be real; a test
#: pins the two tuples equal, the same way ``tenancy.ROLES`` and
#: ``authz.ROLE_ORDER`` are pinned.
SCOPE_ROLES = ("view", "comment", "edit", "admin")

USERS, ENROLMENTS, SESSIONS, TOKENS = (
    "users.json", "enrolments.json", "sessions.json", "tokens.json")
#: PRD-005 FR1's provider configuration — the one document here that is
#: **written by a human**, not by this module (see :meth:`AuthStore.read_oidc`).
#: It joins the four rather than becoming an environment variable for the
#: reason the other four are files: ``agentcad admin ...`` through
#: ``docker compose exec`` is a supported second writer, and a client secret in
#: ``docker inspect`` output is worse than one 0600 beside the password hashes.
OIDC = "oidc.json"
DOCUMENTS = frozenset({USERS, ENROLMENTS, SESSIONS, TOKENS, OIDC})

LOCK_FILE = ".lock"

# --- scrypt -----------------------------------------------------------------
# n=2**15, r=8, p=1 — the design spec's setting (Decision 4). Cost **measured**
# on this tree, not estimated: 62.8 ms per hash and 32 MiB of memory
# (`hashlib.scrypt` x5, Apple M-series, 2026-08-17). The spec says "~100 ms";
# the real number is lower and is recorded here rather than repeated wrong.
#
# This is deliberately *below* the OWASP Password Storage Cheat Sheet's
# scrypt minimum (n=2^17, r=8, p=1). Stated rather than hidden, with the
# reasons: registration is closed and an account on this instance is already
# arbitrary code execution on the host (design Decision 1), so the password is
# nowhere near the weakest link; login is rate limited per handle *and* per
# address, which is what NIST SP 800-63B relies on against online guessing;
# n=2^17 is 4x the memory on a deployment whose documented floor is 2 vCPU /
# 4 GB; and the parameters are stored beside every digest, so raising them
# later re-hashes on next login instead of invalidating accounts.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
#: OpenSSL's default cap is exactly 32 MiB and 128*n*r is exactly 32 MiB, so
#: the default rejects these parameters by one byte. Ask for headroom.
SCRYPT_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2

#: NIST SP 800-63B §5.1.1.2: user-chosen secrets are at least 8 characters,
#: with no composition rules, and *rate limiting* rather than complexity is
#: what bounds online guessing (login is bucketed per handle and per address).
MIN_PASSWORD_CHARS = 8
#: The same section asks that at least 64 characters be accepted. The cap is
#: far above that and exists only so scrypt is never handed a megabyte.
MAX_PASSWORD_CHARS = 1024

#: An enrolment URL is mailed by hand, over Slack, or read aloud. Seven days
#: is long enough for a weekend and short enough that a leaked link in a chat
#: log is not a standing invitation.
ENROL_TTL_S = 7 * 86400

#: Sliding: a session unused for this long is dead. Absolute: no session
#: outlives this, however busy. 14/30 days is the PRD-005a design's setting —
#: long enough that a CAD user is not signing in weekly, short enough that a
#: stolen laptop's cookie expires inside a month.
SLIDING_SESSION_S = 14 * 86400
ABSOLUTE_SESSION_S = 30 * 86400
#: ``last_seen`` is only rewritten when it crosses this, so a busy session does
#: not cost an flock + fsync of the whole document on every single request.
SESSION_SLIDE_GRANULARITY_S = 86400

BEARER_PREFIX = "acad"
#: A bearer is ``acad_<id8>_<secret43>``. The id is hex, so the *first* two
#: underscores always delimit it; the secret's own alphabet includes ``_``,
#: which is why every split here is ``split("_", 2)`` and never ``split("_")``.
_TOKEN_ID_BYTES = 4

#: Fixed salt for the dummy hash an unknown or disabled handle is charged. Its
#: only job is to cost the same as a real verification.
_DUMMY_SALT = b"agentcad-unknown-handle-salt-005a"[:SCRYPT_SALT_BYTES]


def _now() -> float:
    """Module-level indirection so tests can move the clock. Every expiry in
    this file reads it; none calls ``time.time`` directly."""
    return time.time()


def _mint_secret() -> str:
    """43 url-safe characters = 256 bits. The reason tokens are not scrypt."""
    return secrets.token_urlsafe(32)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


# --------------------------------------------------------------- lock registry

class _Guard:
    """Per-root lock state, shared by every ``AuthStore`` on that root.

    Registry-scoped rather than instance-scoped for ``_index_scope``'s reason:
    two ``AuthStore`` objects on one root inside one process (the server and a
    tool pack, say) must contend on the *same* in-process lock. They must also
    not each take an ``flock`` — ``flock`` is per open file description, so a
    second one in the same process blocks against the first for ever. Hence
    the depth counter: the outermost scope owns the file handle.
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


class AuthStore:
    """The four documents, behind one lock, with atomic writes."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        # 0700: nothing here is another account's business, and the mode is
        # set at creation rather than repaired later — a permission we widened
        # back is a promise we cannot keep about who read it in between.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._guard = _guard_for(self.root.resolve())
        #: name -> ((mtime_ns, size, inode), parsed). Re-parsing four
        #: documents on every request is what this avoids; the stat key is
        #: what makes a *second process's* write visible with no restart.
        self._cache: dict[str, tuple[tuple, dict]] = {}

    # ------------------------------------------------------------- plumbing

    @contextmanager
    def _scope(self):
        """Serialise a read-modify-write in-process **and** across processes.

        ``agentcad admin ...`` through ``docker compose exec`` is routinely a
        second writer while the server holds the same document in memory. This
        is ``LocalIndex._index_scope``'s situation exactly
        (``core/packages/indexes.py``); the lock file lives beside the
        documents and is never one of them.

        The flock is **advisory and best-effort**, as it is there: on a
        filesystem that does not support it, or a platform with no ``fcntl``,
        the in-process lock still holds and the cross-process case degrades to
        what it was. Stated rather than hidden, because a lock that silently
        is not one is worse than a documented gap.
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

    def _read(self, name: str, *, fresh: bool = False) -> dict:
        """Parse a document, reusing the cached parse while its stat is equal.

        ``fresh=True`` for every read that is about to be followed by a write:
        the stat key is a good discriminator across processes (a row added or
        removed changes the size) but it is not a proof, and a read-modify-write
        that started from a stale parse would drop the other writer's row.
        Inside ``_scope`` there is no cost to being exact.
        """
        path = self.root / name
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
        raw = path.read_bytes()
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError, UnicodeDecodeError) as exc:
            # Never "treat garbage as empty": that turns a corrupt store into
            # an instance with no accounts, which the next `admin user add`
            # would cheerfully repopulate over the top of.
            raise ValidationError(
                f"the identity document {name} is unreadable: {exc}. Restore "
                f"it from a backup of the state directory.",
                {"document": name},
            ) from exc
        if not isinstance(doc, dict):
            raise ValidationError(
                f"the identity document {name} is not an object",
                {"document": name})
        self._cache[name] = (key, doc)
        return doc

    def _write(self, name: str, doc: dict) -> None:
        """Atomic, 0600, and staged through a **random** name.

        The random staging name is changelog 0181's lesson, not decoration: a
        fixed ``<name>.tmp`` lets two writers open the same staging file,
        interleave their bytes into it and each ``os.replace`` the mixture into
        place — corruption, not a lost update.
        """
        assert name in DOCUMENTS, name
        path = self.root / name
        data = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")
        tmp = path.with_name(f"{name}.{secrets.token_hex(8)}.tmp")
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

    # ---------------------------------------------------------------- users

    def add_user(self, handle: str, role: str = "member") -> str:
        """Create a **disabled** account and return its enrolment token.

        Disabled until enrolment, so a half-created account is never a
        credential. The token is the only time it is returned.
        """
        handle = _check_handle(handle)
        role = _check_role(role)
        token = _mint_secret()
        with self._scope():
            users = dict(self._read(USERS, fresh=True))
            if handle in users:
                raise ConflictError(
                    f"account {handle!r} already exists. Use "
                    f"`agentcad admin enrol {handle}` to re-mint an enrolment "
                    f"link — adding it again would be a silent password reset.",
                    {"handle": handle})
            users[handle] = {
                "role": role,
                "disabled": True,
                "created": _now(),
                "password": None,
            }
            enrolments = dict(self._read(ENROLMENTS, fresh=True))
            enrolments[_digest(token)] = {
                "handle": handle,
                "expires": _now() + ENROL_TTL_S,
                "used": False,
            }
            self._write(USERS, users)
            self._write(ENROLMENTS, enrolments)
        return token

    def mint_enrolment(self, handle: str) -> str:
        """Re-mint an enrolment token for an existing account.

        The recovery path — a lost link, or an account whose password is gone.
        Every outstanding enrolment for the handle is dropped, so the new link
        is the only one that works.
        """
        handle = _check_handle(handle)
        token = _mint_secret()
        with self._scope():
            users = self._read(USERS, fresh=True)
            if handle not in users:
                raise NotFoundError(f"no account {handle!r}", {"handle": handle})
            enrolments = {
                key: row for key, row in self._read(ENROLMENTS, fresh=True).items()
                if row.get("handle") != handle
            }
            enrolments[_digest(token)] = {
                "handle": handle,
                "expires": _now() + ENROL_TTL_S,
                "used": False,
            }
            self._write(ENROLMENTS, enrolments)
        return token

    def list_users(self) -> list[dict]:
        """Every account, sorted by handle. Never a digest, never a salt."""
        users = self._read(USERS)
        return [
            {
                "handle": handle,
                "role": row.get("role", "member"),
                "disabled": bool(row.get("disabled", True)),
                "created": row.get("created"),
                "enrolled": row.get("password") is not None,
            }
            for handle, row in sorted(users.items())
        ]

    def get_user(self, handle: str) -> dict | None:
        for row in self.list_users():
            if row["handle"] == handle:
                return row
        return None

    def disable_user(self, handle: str) -> None:
        """Disable an account. Live sessions die on their next request, because
        resolution reads the user row rather than trusting the session's."""
        self._set_user_field(handle, "disabled", True)

    def enable_user(self, handle: str) -> None:
        self._set_user_field(handle, "disabled", False)

    def set_role(self, handle: str, role: str) -> None:
        self._set_user_field(handle, "role", _check_role(role))

    def _set_user_field(self, handle: str, field: str, value) -> None:
        with self._scope():
            users = dict(self._read(USERS, fresh=True))
            row = users.get(handle)
            if row is None:
                raise NotFoundError(f"no account {handle!r}", {"handle": handle})
            users[handle] = {**row, field: value}
            self._write(USERS, users)

    def peek_enrolment(self, token: str) -> str | None:
        """The handle a live enrolment token belongs to, without spending it.

        The sign-in page renders "set a password for nikita", so previewing
        the link must not burn it. ``None`` for unknown, used or expired —
        one answer, because the token is the credential and the differences
        would say which guesses were close.
        """
        if not isinstance(token, str) or not token:
            return None
        row = self._read(ENROLMENTS).get(_digest(token))
        if not isinstance(row, dict) or row.get("used"):
            return None
        if float(row.get("expires") or 0.0) <= _now():
            return None
        return row.get("handle")

    def enrol(self, token: str, password: str) -> str:
        """Spend an enrolment token: set the password, enable the account, and
        **sign the handle out everywhere else**.

        Order is deliberate. The token is judged *first*, so a bad token is a
        404 that says nothing about the password — otherwise the response tells
        a stranger their guessed token was real.

        The revocation is the recovery path's whole point (review finding M4).
        ``agentcad admin enrol <handle>`` re-mints a link for an account that
        already exists, which is what an operator runs when a password is lost
        *or stolen*; without it an attacker's stolen cookie outlived the reset
        by up to ``ABSOLUTE_SESSION_S`` (30 days). It is also what a person
        expects a password reset to do. The caller's own new session is created
        after this returns, so it survives — ``disable_user`` does the same
        thing from ``routes_auth``, one layer up; here it is inside the write
        scope so a reset cannot half-apply.
        """
        key = _digest(token or "")
        with self._scope():
            enrolments = dict(self._read(ENROLMENTS, fresh=True))
            row = enrolments.get(key)
            if row is None or row.get("used") or row.get("expires", 0) <= _now():
                raise NotFoundError("this enrolment link is not valid")
            handle = row["handle"]
            users = dict(self._read(USERS, fresh=True))
            if handle not in users:
                raise NotFoundError("this enrolment link is not valid")
            _check_password(password)
            users[handle] = {
                **users[handle],
                "disabled": False,
                "password": _hash_password(password),
            }
            enrolments[key] = {**row, "used": True, "used_at": _now()}
            self._write(USERS, users)
            self._write(ENROLMENTS, enrolments)
            # Reentrant: `_scope` counts depth, so this is the same lock and
            # the same flock, not a second one.
            self.revoke_sessions_for(handle)
        return handle

    def verify_password(self, handle: str, password: str) -> bool:
        """``True`` iff the account exists, is enabled, and the password matches.

        An unknown or disabled handle is charged a **full dummy scrypt** before
        returning ``False``. Without it the cheap path is a user-enumeration
        oracle: an attacker learns which handles exist by timing alone.
        """
        row = self._read(USERS).get(handle or "")
        stored = row.get("password") if isinstance(row, dict) else None
        if row is None or row.get("disabled") or not isinstance(stored, dict):
            _hash_password(password or "", salt=_DUMMY_SALT, unchecked=True)
            return False
        try:
            salt = bytes.fromhex(stored["salt"])
            expected = bytes.fromhex(stored["digest"])
            candidate = hashlib.scrypt(
                (password or "").encode("utf-8")[:MAX_PASSWORD_CHARS * 4],
                salt=salt,
                n=int(stored["n"]), r=int(stored["r"]), p=int(stored["p"]),
                dklen=len(expected),
                maxmem=SCRYPT_MAXMEM,
            )
        except (KeyError, ValueError, TypeError):
            return False
        return hmac.compare_digest(candidate, expected)

    # ------------------------------------------------------------- sessions

    def create_session(self, handle: str, device: str | None) -> str:
        """Mint an opaque session secret. Only the SHA-256 digest is stored, so
        reading ``sessions.json`` does not yield live sessions."""
        secret = _mint_secret()
        with self._scope():
            if handle not in self._read(USERS, fresh=True):
                raise NotFoundError(f"no account {handle!r}", {"handle": handle})
            sessions = dict(self._read(SESSIONS, fresh=True))
            now = _now()
            sessions[_digest(secret)] = {
                "handle": handle,
                "device": device,
                "created": now,
                "last_seen": now,
                "expires": now + ABSOLUTE_SESSION_S,
            }
            self._write(SESSIONS, _prune_sessions(sessions))
        return secret

    def resolve_session(self, secret: str) -> dict | None:
        """``{handle, role, device}`` for a live session, else ``None``.

        Three ways to be dead, and each is tested by its negation: past the
        absolute cap, idle past the sliding window, or belonging to an account
        that has since been disabled or deleted. The role is read from the
        user row rather than the session, so ``admin user disable`` and a role
        change both take effect on the very next request.
        """
        if not secret or not isinstance(secret, str):
            return None
        key = _digest(secret)
        row = self._read(SESSIONS).get(key)
        if not isinstance(row, dict):
            return None
        now = _now()
        last_seen = float(row.get("last_seen") or 0.0)
        if now >= float(row.get("expires") or 0.0):
            return None
        if now - last_seen >= SLIDING_SESSION_S:
            return None
        user = self._read(USERS).get(row.get("handle") or "")
        if not isinstance(user, dict) or user.get("disabled"):
            return None
        if now - last_seen >= SESSION_SLIDE_GRANULARITY_S:
            self._slide(key, now)
        return {
            "handle": row["handle"],
            "role": user.get("role", "member"),
            "device": row.get("device"),
        }

    def _slide(self, key: str, now: float) -> None:
        with self._scope():
            sessions = dict(self._read(SESSIONS, fresh=True))
            row = sessions.get(key)
            if not isinstance(row, dict):
                return                      # revoked between read and write
            sessions[key] = {**row, "last_seen": now}
            self._write(SESSIONS, _prune_sessions(sessions))

    def revoke_session(self, secret: str) -> None:
        """Delete the row. Revocation is immediate because the store is the
        authority — which is the whole reason these are not JWTs.

        Silent on an unknown secret: logout must never be a way to learn
        whether a cookie was real, and must never 500.
        """
        if not secret or not isinstance(secret, str):
            return
        key = _digest(secret)
        with self._scope():
            sessions = dict(self._read(SESSIONS, fresh=True))
            if sessions.pop(key, None) is None:
                return
            self._write(SESSIONS, _prune_sessions(sessions))

    def revoke_sessions_for(self, handle: str) -> int:
        """Sign a handle out everywhere. Returns how many rows went."""
        with self._scope():
            sessions = self._read(SESSIONS, fresh=True)
            kept = {k: v for k, v in sessions.items() if v.get("handle") != handle}
            dropped = len(sessions) - len(kept)
            if dropped:
                self._write(SESSIONS, kept)
        return dropped

    # --------------------------------------------------------------- tokens

    def add_token(self, name: str, role: str = "member",
                  ttl_days: int | None = None,
                  scope: dict | None = None) -> str:
        """Mint ``acad_<id8>_<secret43>``. Returned **once**; stored by digest.

        ``scope`` (PRD-005 FR3) is **additive and optional**. Omitted, the
        record is byte-for-byte what 005a wrote and the token keeps 005a's
        semantics exactly: instance-wide, bounded only by its instance
        ``role``. Present, it is :func:`check_token_scope`'s normalized
        ``{org, workspace, projects, role}`` and the token reaches those
        projects and no others.

        The asymmetry is deliberate, and it is what "an unscoped token keeps
        today's behaviour" means in code rather than in prose: only a hosted
        **administrator** mints an unscoped one (``agentcad admin token add``,
        ``POST /api/auth/tokens``), while the tenant-facing
        ``create_agent_token`` tool always writes a scope. An instance that
        never mints from the tool surface is unchanged by this feature.
        """
        name = _check_name(name)
        role = _check_role(role)
        scope = check_token_scope(scope) if scope is not None else None
        secret = _mint_secret()
        with self._scope():
            tokens = dict(self._read(TOKENS, fresh=True))
            token_id = secrets.token_hex(_TOKEN_ID_BYTES)
            while token_id in tokens:
                token_id = secrets.token_hex(_TOKEN_ID_BYTES)
            tokens[token_id] = {
                "name": name,
                "role": role,
                "digest": _digest(secret),
                "created": _now(),
                "expires": (_now() + ttl_days * 86400) if ttl_days else None,
                "revoked": False,
                # Written only when there is one, so an unscoped token's row is
                # the same JSON it was before PRD-005 — nothing to migrate, and
                # a diff of `tokens.json` across the upgrade is empty.
                **({"scope": scope} if scope else {}),
            }
            self._write(TOKENS, tokens)
        return f"{BEARER_PREFIX}_{token_id}_{secret}"

    def list_tokens(self) -> list[dict]:
        """Every token, newest last. Never the digest, never the secret.

        ``scope`` appears on a row **only when the token has one**: the key set
        of a legacy row is unchanged, which is asserted by equality in
        ``tests/test_authstore.py`` and is the shape 005a's callers destructure.
        """
        rows = [
            {
                "id": token_id,
                "name": row.get("name", ""),
                "role": row.get("role", "member"),
                "created": row.get("created"),
                "expires": row.get("expires"),
                "revoked": bool(row.get("revoked")),
                **({"scope": _scope_row(row)} if _scope_row(row) else {}),
            }
            for token_id, row in self._read(TOKENS).items()
        ]
        return sorted(rows, key=lambda r: (r["created"] or 0, r["id"]))

    def get_token(self, token_id: str) -> dict | None:
        """One token's public row (scope included), or ``None``.

        What ``revoke_agent_token`` needs to know *which* org's grants to drop
        before it revokes — and it is a read of the same rows
        :meth:`list_tokens` renders, never the digest.
        """
        for row in self.list_tokens():
            if row["id"] == token_id:
                return row
        return None

    def live_tokens_named(self, name: str) -> list[dict]:
        """Every token named *name* that could authenticate right now.

        Names are labels, not keys — 005a mints two tokens called ``ci``
        happily and a test pins that. But both compose into the **one**
        principal ``agent:ci``, so a scoped mint has to be able to see the
        others before it adds a second set of grants under that name.
        """
        now = _now()
        return [
            row for row in self.list_tokens()
            if row["name"] == name and not row["revoked"]
            and not (row["expires"] and float(row["expires"]) <= now)
        ]

    def scope_for_principal(self, name: str) -> dict | None:
        """The scope ``agent:<name>`` is speaking with, or ``None``.

        ``None`` for a legacy token, for an unknown name **and for an
        ambiguous one** — two live scoped tokens sharing a name are two scopes
        for one principal, and answering with either would be a guess. Reach is
        never decided here in any case: the tenancy grants are the authority
        (``authz.role_of``), and this is what ``whoami`` renders so the holder
        can see what their token was minted for.
        """
        scopes = [row["scope"] for row in self.live_tokens_named(name)
                  if row.get("scope")]
        return dict(scopes[0]) if len(scopes) == 1 else None

    def revoke_token(self, token_id: str) -> None:
        """Mark revoked (rather than delete) so ``admin token list`` still
        shows what was revoked and when. Takes effect on the next request."""
        with self._scope():
            tokens = dict(self._read(TOKENS, fresh=True))
            row = tokens.get(token_id)
            if row is None:
                raise NotFoundError(f"no token {token_id!r}", {"id": token_id})
            tokens[token_id] = {**row, "revoked": True, "revoked_at": _now()}
            self._write(TOKENS, tokens)

    def resolve_token(self, presented: str) -> dict | None:
        """``{name, role}`` for a live bearer, else ``None``.

        Reached by an anonymous request carrying an attacker's header, so it
        never raises: a traceback here would be a 500 that says the header was
        interesting.
        """
        if not isinstance(presented, str):
            return None
        parts = presented.split("_", 2)     # the secret's alphabet includes "_"
        if len(parts) != 3 or parts[0] != BEARER_PREFIX or not parts[1] or not parts[2]:
            return None
        _prefix, token_id, secret = parts
        row = self._read(TOKENS).get(token_id)
        if not isinstance(row, dict):
            # Compare anyway: an early return here would make "that id exists"
            # measurable. sha256 is cheap, so the symmetry is free.
            hmac.compare_digest(_digest(secret), _digest("no such token"))
            return None
        if row.get("revoked"):
            return None
        expires = row.get("expires")
        if expires is not None and _now() >= float(expires):
            return None
        if not hmac.compare_digest(_digest(secret), str(row.get("digest", ""))):
            return None
        # `scope` is present ONLY for a scoped token, so an unscoped one
        # resolves to exactly the two keys 005a returned — `security.
        # resolve_principal` and every test that compares this dict by
        # equality are untouched, and a caller that never saw a scope keeps
        # 005a's instance-wide semantics with no branch of its own.
        scope = _scope_row(row)
        return {"name": row.get("name", ""), "role": row.get("role", "member"),
                **({"scope": scope} if scope else {})}

    # ------------------------------------------------- passkeys (PRD-005 FR1)
    #
    # Two new **per-user** fields, added beside `role`/`disabled`/`created`/
    # `password` rather than in documents of their own::
    #
    #     "passkeys": [{id, public_key, sign_count, label, created,
    #                   transports, backed_up}]
    #     "oidc":     {issuer, subject, email, linked}
    #
    # They belong to the account and die with it, so a second document would
    # only add a way for the two to disagree. Every reader below is
    # **schema-tolerant** — an absent or malformed field reads as "none", never
    # as an error — because these fields did not exist when today's
    # `users.json` files were written and an instance that upgraded must not
    # need a migration. Nothing here reshapes an existing field: a user row
    # that never registers a passkey is byte-identical to what it was.
    #
    # `id` and `public_key` are **base64url** text (the WebAuthn wire form), so
    # a credential is JSON without a codec: `webauthn.base64url_to_bytes` is the
    # inverse, and the route pack is the only thing that needs it.

    def add_passkey(self, handle: str, *, credential_id: str, public_key: str,
                    sign_count: int = 0, label: str | None = None,
                    transports: list | None = None,
                    backed_up: bool | None = None) -> dict:
        """Register a credential for an existing account. Returns the row.

        A credential id is globally unique (it comes out of an authenticator's
        CSPRNG), so registering one that already exists anywhere in the store
        is a conflict rather than a second row: two accounts sharing one
        credential is an identity that resolves two ways.
        """
        credential_id = _check_credential_id(credential_id)
        if not isinstance(public_key, str) or not public_key:
            raise ValidationError("a passkey needs its public key",
                                  {"handle": handle})
        with self._scope():
            users = dict(self._read(USERS, fresh=True))
            row = users.get(handle)
            if row is None:
                raise NotFoundError(f"no account {handle!r}", {"handle": handle})
            for other, other_row in users.items():
                if any(p.get("id") == credential_id
                       for p in _passkey_rows(other_row)):
                    raise ConflictError(
                        "that passkey is already registered"
                        + (" on this account" if other == handle else ""),
                        {"handle": handle})
            entry = {
                "id": credential_id,
                "public_key": public_key,
                "sign_count": max(0, int(sign_count or 0)),
                "label": (label or "passkey")[:64],
                "created": _now(),
                "transports": [str(t)[:16] for t in (transports or [])][:8],
                "backed_up": bool(backed_up),
            }
            users[handle] = {**row, "passkeys": [*_passkey_rows(row), entry]}
            self._write(USERS, users)
        return dict(entry)

    def get_passkeys(self, handle: str) -> list[dict]:
        """Every credential on an account, oldest first. Public keys included:
        a public key is public, and the route that lists them is the account's
        own."""
        return [dict(p) for p in _passkey_rows(self._read(USERS).get(handle or ""))]

    def find_by_passkey(self, credential_id: str) -> dict | None:
        """``{handle, ...credential}`` for a credential id, else ``None``.

        This is what makes a **usernameless** sign-in possible: the browser
        hands back a credential id and this resolves the account, so nobody
        types a handle. It never raises — it is reached by an anonymous request
        carrying an attacker's payload.
        """
        if not isinstance(credential_id, str) or not credential_id:
            return None
        for handle, row in sorted(self._read(USERS).items()):
            for entry in _passkey_rows(row):
                if entry.get("id") == credential_id:
                    return {"handle": handle, **dict(entry)}
        return None

    def update_sign_count(self, handle: str, credential_id: str,
                          sign_count: int) -> None:
        """Record the counter an assertion reported.

        Silent when the credential is gone (it can be removed between the
        assertion and this write); **never lowers** a stored counter, because
        the counter's only job is to be monotonic and a regression is what the
        verifier refuses on.
        """
        with self._scope():
            users = dict(self._read(USERS, fresh=True))
            row = users.get(handle)
            if row is None:
                return
            rows = _passkey_rows(row)
            updated, changed = [], False
            for entry in rows:
                if (entry.get("id") == credential_id
                        and int(sign_count or 0) > int(entry.get("sign_count") or 0)):
                    entry = {**entry, "sign_count": int(sign_count),
                             "last_used": _now()}
                    changed = True
                elif entry.get("id") == credential_id:
                    entry = {**entry, "last_used": _now()}
                    changed = True
                updated.append(entry)
            if not changed:
                return
            users[handle] = {**row, "passkeys": updated}
            self._write(USERS, users)

    def remove_passkey(self, handle: str, credential_id: str) -> bool:
        """Delete a credential. ``True`` iff one went."""
        with self._scope():
            users = dict(self._read(USERS, fresh=True))
            row = users.get(handle)
            if row is None:
                raise NotFoundError(f"no account {handle!r}", {"handle": handle})
            kept = [p for p in _passkey_rows(row) if p.get("id") != credential_id]
            if len(kept) == len(_passkey_rows(row)):
                return False
            users[handle] = {**row, "passkeys": kept}
            self._write(USERS, users)
        return True

    # ----------------------------------------------------- OIDC link (FR1)

    def link_oidc(self, handle: str, issuer: str, subject: str,
                  email: str | None = None) -> dict:
        """Bind an IdP identity to an account. One identity per account.

        Idempotent **and write-free when nothing changed**: an OIDC sign-in
        calls this every time, and re-writing `users.json` on every sign-in
        would put an flock + fsync on the login path for no new information.
        """
        if not isinstance(issuer, str) or not issuer.strip():
            raise ValidationError("an OIDC link needs an issuer")
        if not isinstance(subject, str) or not subject.strip():
            raise ValidationError("an OIDC link needs a subject")
        link = {"issuer": issuer.strip().rstrip("/"), "subject": subject.strip(),
                "email": (email or None), "linked": _now()}
        with self._scope():
            users = dict(self._read(USERS, fresh=True))
            row = users.get(handle)
            if row is None:
                raise NotFoundError(f"no account {handle!r}", {"handle": handle})
            for other, other_row in users.items():
                current = _oidc_row(other_row)
                if (other != handle and current
                        and current.get("issuer") == link["issuer"]
                        and current.get("subject") == link["subject"]):
                    raise ConflictError(
                        "that identity is already linked to another account",
                        {"issuer": link["issuer"]})
            current = _oidc_row(row)
            if (current and current.get("issuer") == link["issuer"]
                    and current.get("subject") == link["subject"]
                    and current.get("email") == link["email"]):
                return dict(current)                 # nothing to write
            users[handle] = {**row, "oidc": link}
            self._write(USERS, users)
        return dict(link)

    def find_oidc(self, handle: str) -> dict | None:
        """The identity linked to *handle*, or ``None``."""
        row = _oidc_row(self._read(USERS).get(handle or ""))
        return dict(row) if row else None

    def find_by_oidc(self, issuer: str, subject: str) -> str | None:
        """The handle an IdP identity signs in as, or ``None``.

        Never raises: it is the first thing an OIDC callback asks, and that
        callback is anonymous.
        """
        if not isinstance(issuer, str) or not isinstance(subject, str):
            return None
        issuer, subject = issuer.strip().rstrip("/"), subject.strip()
        if not issuer or not subject:
            return None
        for handle, row in sorted(self._read(USERS).items()):
            link = _oidc_row(row)
            if (link and link.get("issuer") == issuer
                    and link.get("subject") == subject):
                return handle
        return None

    def unlink_oidc(self, handle: str) -> bool:
        """Drop the link. ``True`` iff one went."""
        with self._scope():
            users = dict(self._read(USERS, fresh=True))
            row = users.get(handle)
            if row is None:
                raise NotFoundError(f"no account {handle!r}", {"handle": handle})
            if _oidc_row(row) is None:
                return False
            users[handle] = {k: v for k, v in row.items() if k != "oidc"}
            self._write(USERS, users)
        return True

    # ------------------------------------------------- provider configuration

    def read_oidc(self) -> dict:
        """``oidc.json``, or ``{}`` when the instance has no provider.

        The one document here a **human** writes, so it is read through the
        same cache and the same "unreadable is not empty" rule as the other
        four: a syntax error in a hand-edited file must be a loud refusal, not
        an instance that silently forgets its identity provider.
        """
        return dict(self._read(OIDC))

    def write_oidc(self, document: dict) -> dict:
        """Replace ``oidc.json`` (0600, atomic). Used by tests and by any
        future admin CLI; the operator's own editor is the other writer."""
        if not isinstance(document, dict):
            raise ValidationError("the OIDC configuration must be an object")
        with self._scope():
            self._write(OIDC, document)
        return dict(document)


# --------------------------------------------------------------- validation

def _check_handle(handle: object) -> str:
    if not isinstance(handle, str) or not HANDLE_RE.match(handle):
        raise ValidationError(
            "a handle is 1-32 characters of a-z, 0-9, dot, underscore or "
            "hyphen, starting with a letter or digit. The ceiling is "
            "arithmetic: 'user:' + handle + '/browser:xxxxxxxx' must fit the "
            "64-character client identity that locks.check_client_id refuses "
            "to truncate.",
            {"handle": handle if isinstance(handle, str) else None},
        )
    return handle


def _check_name(name: object) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValidationError(
            "a token name is 1-32 characters of a-z, 0-9, dot, underscore or "
            "hyphen; it composes into the client identity 'agent:<name>'.",
            {"name": name if isinstance(name, str) else None},
        )
    return name


def _check_role(role: object) -> str:
    if role not in ROLES:
        raise ValidationError(
            f"role must be one of {', '.join(ROLES)}", {"role": role})
    return role


# ------------------------------------------------- token scope (PRD-005 FR3)
#
# A scope is `{org, workspace, projects, role}` and it is stored **normalized**:
# every entry of `projects` is a fully qualified `<workspace>/<project>`, sorted
# and de-duplicated. Normalizing on write rather than on read is PRD-012's
# ruling for declared configurations, and it buys the same thing here: one
# spelling in the document, so a comparison is a string comparison and an
# operator reading `tokens.json` sees exactly what the token reaches.
#
# `workspace` is the default the unqualified entries were resolved against. It
# is kept because it is also **the tenant a bearer request resolves to** when
# slice 4 wires token scope into `security.resolve_principal`; without it a
# token that names one workspace's projects would still have no answer to
# "which workspace am I in".

#: The one place the shape is spelled out for a reader; the validator below is
#: the enforcement.
SCOPE_KEYS = ("org", "workspace", "projects", "role")

#: A scope may not name an unbounded number of projects: it is written by a
#: tool call and read on every authorization question.
MAX_SCOPE_PROJECTS = 200


def check_token_scope(scope: object, *, workspace: str | None = None) -> dict:
    """Normalize a token scope, or raise ``ValidationError``.

    Accepts ``projects`` entries in either spelling — ``"widget"`` (resolved
    against ``workspace``, from the scope or the argument) or
    ``"main/widget"`` — and always **stores the qualified form**. An empty
    ``projects`` list is refused rather than read as "the whole org": a
    credential whose reach is decided by an omission is exactly the kind of
    scope that widens silently as the org grows.
    """
    if not isinstance(scope, dict):
        raise ValidationError(
            "a token scope is an object {org, projects, role} (workspace "
            "optional when every project is qualified as '<workspace>/"
            "<project>')",
            {"scope": None})
    unknown = sorted(set(scope) - set(SCOPE_KEYS))
    if unknown:
        raise ValidationError(
            f"unknown token scope field(s): {', '.join(unknown)}",
            {"unknown": unknown, "expected": list(SCOPE_KEYS)})
    org = _check_scope_name(scope.get("org"), "org")
    default_ws = scope.get("workspace") if scope.get("workspace") else workspace
    if default_ws is not None:
        default_ws = _check_scope_name(default_ws, "workspace")
    role = scope.get("role")
    if role not in SCOPE_ROLES:
        raise ValidationError(
            f"a token scope's role must be one of {', '.join(SCOPE_ROLES)} "
            f"(weakest first)", {"role": role if isinstance(role, str) else None})
    raw = scope.get("projects")
    if not isinstance(raw, list) or not raw:
        raise ValidationError(
            "a token scope names at least one project. A scope with no "
            "projects is not 'the whole org' — it is a credential whose reach "
            "nobody stated, and it would widen every time a project is added.",
            {"projects": raw if isinstance(raw, list) else None})
    if len(raw) > MAX_SCOPE_PROJECTS:
        raise ValidationError(
            f"a token scope names at most {MAX_SCOPE_PROJECTS} projects",
            {"count": len(raw), "max": MAX_SCOPE_PROJECTS})
    qualified: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            raise ValidationError(
                "a scoped project is '<project>' or '<workspace>/<project>'",
                {"project": entry if isinstance(entry, str) else None})
        head, sep, tail = entry.partition("/")
        if sep:
            ws, proj = head, tail
        elif default_ws is None:
            raise ValidationError(
                f"project {entry!r} is unqualified and this scope names no "
                f"workspace: pass 'workspace' or write '<workspace>/{entry}'.",
                {"project": entry})
        else:
            ws, proj = default_ws, entry
        qualified.add(f"{_check_scope_name(ws, 'workspace')}/"
                      f"{_check_scope_name(proj, 'project')}")
    return {"org": org, "workspace": default_ws,
            "projects": sorted(qualified), "role": role}


def _check_scope_name(value: object, what: str) -> str:
    """``ID_RE`` per level — ``tenancy.check_name``'s grammar, reached without
    importing ``tenancy`` (which imports this module)."""
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ValidationError(
            f"invalid {what} name {value!r} in a token scope: must match "
            f"[a-z][a-z0-9_]{{0,39}}, the same grammar as a project id.",
            {what: value if isinstance(value, str) else None})
    return value


def _scope_row(row: object) -> dict | None:
    """The scope on a stored token row, schema-tolerantly.

    An absent field, a ``null``, a string where an object belongs, or a
    half-written scope all read as "unscoped" — the passkey/OIDC readers'
    tolerance, for the same reason: rows written before PRD-005 have no scope
    and that is the ordinary case on every upgraded instance. A malformed scope
    reading as *unscoped* is the conservative direction only because an
    unscoped token is still bounded by the instance role and by whatever
    tenancy grants name it; nothing here widens anybody's reach.
    """
    if not isinstance(row, dict):
        return None
    scope = row.get("scope")
    if (not isinstance(scope, dict)
            or not isinstance(scope.get("org"), str)
            or not isinstance(scope.get("projects"), list)
            or scope.get("role") not in SCOPE_ROLES):
        return None
    projects = [p for p in scope["projects"] if isinstance(p, str) and p]
    if not projects:
        return None
    workspace = scope.get("workspace")
    return {"org": scope["org"],
            "workspace": workspace if isinstance(workspace, str) else None,
            "projects": sorted(set(projects)),
            "role": scope["role"]}


def scope_allows(scope: object, org: str | None = None,
                 workspace: str | None = None,
                 project: str | None = None) -> bool:
    """May a token carrying *scope* reach ``org/workspace/project``?

    ``scope is None`` answers **True** for everything: that is the legacy,
    instance-wide token 005a mints, and the whole point of the additive schema
    is that it behaves today exactly as it did yesterday. What bounds *it* is
    the instance role and the tenancy grants, unchanged.

    With a scope: the org must match, and — when a project is named — the pair
    must be in the list. An unqualified question (``workspace=None``) matches
    the project under **any** workspace of the org, because the caller who has
    not resolved a workspace yet is asking "could this token possibly be about
    this project", and answering "no" there would deny a request the tenant
    resolver was about to make legal.

    Never raises: it is reached from an authorization path holding whatever
    ``tokens.json`` contained, and an unreadable scope must deny rather than
    explode. It is also **not** a role check — the role a scope carries is what
    ``create_agent_token`` writes into the tenancy grants, and
    ``authz.require`` is what compares rungs.
    """
    if scope is None:
        return True
    if not isinstance(scope, dict):
        return False
    normalized = _scope_row({"scope": scope})
    if normalized is None:
        return False
    if org is not None and normalized["org"] != org:
        return False
    if project is None:
        return True
    if workspace is not None:
        return f"{workspace}/{project}" in normalized["projects"]
    return any(entry.split("/", 1)[-1] == project
               for entry in normalized["projects"])


#: A WebAuthn credential id is base64url text of the authenticator's own
#: random bytes. Bounded because it arrives from an anonymous request body and
#: an unbounded id is an unbounded key; the alphabet is checked because it is
#: compared against stored ids and rendered in a credential list.
CREDENTIAL_ID_MAX_CHARS = 512
_CREDENTIAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


def _check_credential_id(credential_id: object) -> str:
    if (not isinstance(credential_id, str)
            or not _CREDENTIAL_ID_RE.match(credential_id)):
        raise ValidationError(
            f"a passkey credential id is 1-{CREDENTIAL_ID_MAX_CHARS} base64url "
            f"characters (A-Z, a-z, 0-9, '-', '_', unpadded)",
            {"length": len(credential_id) if isinstance(credential_id, str)
             else None})
    return credential_id


def _passkey_rows(row: object) -> list[dict]:
    """The credentials on a user row, schema-tolerantly.

    An absent field, a ``null``, a string where a list belongs, or a list with
    junk in it all read as "the credentials that are actually there". A user
    row written before PRD-005 has none, and that must never be an exception —
    it is the ordinary case on every upgraded instance.
    """
    if not isinstance(row, dict):
        return []
    rows = row.get("passkeys")
    if not isinstance(rows, list):
        return []
    return [entry for entry in rows
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and isinstance(entry.get("public_key"), str)]


def _oidc_row(row: object) -> dict | None:
    """The OIDC link on a user row, or ``None`` — same tolerance as above."""
    if not isinstance(row, dict):
        return None
    link = row.get("oidc")
    if (not isinstance(link, dict)
            or not isinstance(link.get("issuer"), str)
            or not isinstance(link.get("subject"), str)
            or not link["issuer"] or not link["subject"]):
        return None
    return link


def _check_password(password: object) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_CHARS:
        raise ValidationError(
            f"a password must be at least {MIN_PASSWORD_CHARS} characters "
            f"(NIST SP 800-63B 5.1.1.2; login is rate limited per account and "
            f"per address, which is what bounds guessing).",
            {"min": MIN_PASSWORD_CHARS},
        )
    if len(password) > MAX_PASSWORD_CHARS:
        raise ValidationError(
            f"a password may be at most {MAX_PASSWORD_CHARS} characters — "
            f"scrypt over an unbounded body is a CPU denial of service.",
            {"max": MAX_PASSWORD_CHARS},
        )
    return password


def _hash_password(password: str, salt: bytes | None = None,
                   unchecked: bool = False) -> dict:
    """scrypt with the parameters recorded beside the digest.

    ``unchecked`` is the dummy path: an unknown handle must be charged the same
    work as a real one, and it must not raise on a password that a real
    enrolment would have refused.
    """
    if not unchecked:
        _check_password(password)
    salt = salt or secrets.token_bytes(SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8")[:MAX_PASSWORD_CHARS * 4],
        salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM,
    )
    return {
        "kdf": "scrypt",
        "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "salt": salt.hex(),
        "digest": digest.hex(),
    }


def _prune_sessions(sessions: dict) -> dict:
    """Drop rows that can never authenticate again.

    Without this the document grows one row per sign-in for ever, and every
    request pays to parse them. Only provably-dead rows go, so pruning can
    never sign anybody out early.
    """
    now = _now()
    return {
        key: row for key, row in sessions.items()
        if isinstance(row, dict)
        and now < float(row.get("expires") or 0.0)
        and now - float(row.get("last_seen") or 0.0) < SLIDING_SESSION_S
    }
