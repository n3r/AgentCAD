"""The audit log (PRD-005 FR12): who did what, to which project, and how it went.

One **SQLite database per org**, at ``<state>/audit/<org>.db``. This is the
repository's first and only SQLite store, and it is the one place PRD-005a's
Decision 14 explicitly permits it ("if this PRD's audit log needs SQLite, it
may introduce it for the audit log"). Identity stays JSON; nothing here shares
a file with ``authstore``'s five documents, so 005a's "``tar`` of the state dir
is a correct backup" statement stays true **for identity** — and is no longer
true for this directory. See :meth:`AuditLog.vacuum_into` and
``docs/deployment.md``.

**Why SQLite and not JSONL.** The spike measured both at 100 000 rows
(``docs/superpowers/specs/2026-08-24-multi-tenant-cloud-spike.md`` §C). JSONL
appends faster (119 k rows/s vs 21 k) and backs up by ``cp``; SQLite answers
the queries an operator actually asks — "everything ``user:anya`` did", "this
project in the last six hours" — in **1.4 ms against 177 ms**, a 126x gap that
grows linearly with retention. Append throughput is not a decision axis (the
slowest configuration measured sustains 9 885 events/s and one mutating
AgentCAD action is one row); query latency is.

**The pragmas are the design.** ``journal_mode=WAL`` (a reader never blocks the
writer), ``synchronous=NORMAL`` (WAL's durability sweet spot), ``busy_timeout=
30000`` plus ``connect(timeout=30)``. That combination is what makes a second
*process* — ``docker compose exec agentcad agentcad admin audit …`` while the
server is running — correct with **no lock file and no retry logic of our
own**: the spike ran 8 concurrent writer processes and got 40 000/40 000 rows
with ``integrity_check: ok``. The three indexes ``(ts)``, ``(principal, ts)``,
``(project, ts)`` are what buy the 1.4 ms.

**Threading model: one connection per (thread, org), never shared.** A
``sqlite3.Connection`` is not thread-safe and Python's default
``check_same_thread=True`` is *kept* rather than disabled, so a connection that
leaked across threads raises instead of corrupting. Serialising every append
behind one process-wide lock was the alternative; it was rejected because the
cross-*process* case (``docker compose exec``) has to work anyway, WAL already
solves it, and a lock that only covers half the writers is a lock that lies.
Connections are opened lazily and cached in a :class:`threading.local`, so a
thread pool pays one open per thread per org and nothing pays anything in local
mode.

**Local mode writes nothing.** No tenant means no org, and
:meth:`AuditLog.append` with a falsy org is a no-op that touches no disk — the
same "the absence of a tenant is local mode" rule ``tenancy`` is built on
(FR4/AC7). Auth events, which are instance-wide rather than an act inside an
org, go to :data:`INSTANCE_ORG` — a name no real org can have, because
``ID_RE`` requires a leading letter.

**Secrets never enter a row.** A row records the *digest* of the arguments, and
:func:`canonical_args` redacts every value under a secret-shaped key before the
digest is taken (:data:`SECRET_KEY_HINTS`), so a mistyped password or a minted
bearer cannot be reconstructed from the audit database. The key survives — the
fact that a token was passed is itself information — and only the value is
replaced, so two calls that differ *only* in their secret produce the same
digest by construction.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

from .locks import current_client_id
from .model import ID_RE, ValidationError

#: Under ``<state>/``, beside ``auth/``. Never inside it: identity backup
#: semantics must not change because an audit database moved next door.
AUDIT_DIRNAME = "audit"

#: Where instance-wide events land — sign-ins, enrolments, token mints. A
#: sign-in is not an act inside an org (a person may be in several, or in
#: none), so it needs a home that is not one. ``ID_RE`` is
#: ``[a-z][a-z0-9_]{0,39}``, so a leading underscore is a name no org can ever
#: take and the two namespaces cannot collide.
INSTANCE_ORG = "_instance"

#: Milliseconds a blocked writer waits before giving up — the spike's setting,
#: and the whole of the cross-process story (``docker compose exec`` while the
#: server runs). ``sqlite3.connect(timeout=30)`` sets the same handler; this
#: states it in the file so a reader does not have to know that.
BUSY_TIMEOUT_MS = 30000

#: ``PRAGMA journal_mode=WAL`` is the one pragma that can answer
#: **SQLITE_BUSY immediately, ignoring the busy handler**: switching journal
#: mode needs a moment of exclusivity, and a sibling connection that is
#: mid-write denies it. That is a real race between two threads opening the
#: same fresh database (it failed a full-suite run before the retry existed),
#: so the switch is attempted a few times and the result is *read back* rather
#: than assumed. A filesystem that refuses WAL outright (some network mounts)
#: stays in ``delete`` mode and everything still works — slower, with readers
#: and the writer blocking each other, which is a degradation worth having
#: rather than a refusal to start.
JOURNAL_MODE_ATTEMPTS = 5
JOURNAL_MODE_BACKOFF_S = 0.05

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    principal   TEXT NOT NULL,
    action      TEXT NOT NULL,
    project     TEXT,
    args_digest TEXT,
    outcome     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts);
CREATE INDEX IF NOT EXISTS audit_principal_ts ON audit (principal, ts);
CREATE INDEX IF NOT EXISTS audit_project_ts ON audit (project, ts);
"""

#: A key whose *name* contains any of these has its value redacted before the
#: arguments are digested. Substring matching, lower-cased: ``token``,
#: ``agent_token``, ``AGENTCAD_TOKEN`` and ``tokens`` all match one entry.
SECRET_KEY_HINTS = (
    "token", "password", "passwd", "passphrase", "secret", "credential",
    "api_key", "apikey", "authorization", "cookie", "private_key",
)

#: What a redacted value becomes. A constant, so the digest of two calls that
#: differ only in their secret is the same digest — which is the property the
#: redaction is for.
REDACTED = "[redacted]"

#: Arguments nest (a PMI section, an assembly instance list). Beyond this depth
#: the structure is summarised rather than walked: an audit digest must not be
#: a way to make the server recurse on a caller's JSON.
MAX_ARG_DEPTH = 8

#: Bounds on what a row may carry. An audit row is never the reason a write
#: fails, so an over-long value is **clamped** rather than refused — the
#: alternative is a product that 500s because somebody's device name was long.
MAX_PRINCIPAL_CHARS = 128
MAX_ACTION_CHARS = 64
MAX_PROJECT_CHARS = 128
MAX_OUTCOME_CHARS = 64

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

#: Retention is off by default: an audit log that forgets by default is not one.
#: ``AGENTCAD_AUDIT_RETENTION_DAYS`` turns pruning on for a deployment with a
#: policy that requires it.
RETENTION_ENV = "AGENTCAD_AUDIT_RETENTION_DAYS"

#: Pruning is opportunistic (on append) and rate limited, so a busy instance
#: does not run a DELETE on every event.
PRUNE_INTERVAL_S = 3600.0


def _now() -> float:
    """Module-level indirection so a test can move the clock, ``authstore``'s
    idiom. Every timestamp here reads it."""
    return time.time()


# --------------------------------------------------------------- arguments

def canonical_args(value: object, _depth: int = 0) -> object:
    """*value* with every secret-shaped key's value replaced by :data:`REDACTED`.

    Recursive over dicts and lists, depth-capped (:data:`MAX_ARG_DEPTH`).
    Anything that is not JSON-able is rendered with ``repr`` — the digest has
    to be total over its input, because it is computed on the way to recording
    a call that has *already happened*.
    """
    if _depth >= MAX_ARG_DEPTH:
        return f"<depth {_depth}>"
    if isinstance(value, dict):
        out = {}
        for key, entry in value.items():
            name = str(key)
            if _is_secret_key(name):
                out[name] = REDACTED
            else:
                out[name] = canonical_args(entry, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [canonical_args(entry, _depth + 1) for entry in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)[:200]


def _is_secret_key(name: str) -> bool:
    """Does *name*'s value need redacting before the digest?

    Substring against :data:`SECRET_KEY_HINTS`, with **one exemption**: a key
    whose last ``_``-delimited segment is ``id`` is an *identifier*, not a
    secret, even when the rest of the name contains a hint. ``token_id`` and
    ``credential_id`` are exactly what an audit reader correlates a revoke or a
    delete by, and the substring test used to redact them — so every revocation
    of a different token produced the *same* digest, and the log lost its
    forensic value at the one place it is read for it. The secret itself still
    travels under ``token``/``secret``/``client_secret``/``private_key``/…,
    whose last segment is never ``id``.
    """
    lowered = name.lower()
    if lowered.rsplit("_", 1)[-1] == "id":
        return False
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def args_digest(args: object) -> str | None:
    """sha256 of the canonical JSON of *args*, secrets redacted first.

    ``None`` for ``None`` — "this action had no arguments" is different from
    "this action had ``{}``", and the column is nullable so it can say so.

    The digest, not the arguments: an audit row is read by an administrator who
    may not be a member of the project, and a script body, a file path or a
    parameter sweep in it would make the log a second copy of the customer's
    data. What the digest is *for* is correlation — the same call, twice, is
    the same digest.
    """
    if args is None:
        return None
    payload = json.dumps(
        canonical_args(args), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=repr,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- store

def _check_org(org: object) -> str:
    """An org name fit to be a **file name**, or a ``ValidationError``.

    Load-bearing rather than defensive: ``<state>/audit/<org>.db`` composes a
    caller-supplied string into a path, so ``../`` or an absolute path here
    would be a write anywhere the server user can write. ``ID_RE`` allows no
    dot, no slash and no leading underscore, and :data:`INSTANCE_ORG` is the
    one exception, named explicitly.
    """
    if org == INSTANCE_ORG:
        return INSTANCE_ORG
    if not isinstance(org, str) or not ID_RE.match(org):
        raise ValidationError(
            f"invalid org name {org!r} for an audit log: it becomes the file "
            f"name <state>/audit/<org>.db, so it must match "
            f"[a-z][a-z0-9_]{{0,39}}.",
            {"org": org if isinstance(org, str) else None})
    return org


def retention_from_env() -> float | None:
    """``AGENTCAD_AUDIT_RETENTION_DAYS``, or ``None``.

    Unset, empty, ``0`` and unparseable all mean "keep everything": the
    failure mode of a mistyped retention knob must be *more* history, never
    less.
    """
    raw = (os.environ.get(RETENTION_ENV) or "").strip()
    if not raw:
        return None
    try:
        days = float(raw)
    except ValueError:
        return None
    return days if days > 0 else None


class AuditLog:
    """The per-org databases under one state directory.

    One instance may serve every org; the databases are separate files and the
    connections are per ``(thread, org)``.
    """

    def __init__(self, state_dir: Path | str, *,
                 retention_days: float | None = None) -> None:
        self.root = Path(state_dir) / AUDIT_DIRNAME
        # 0700 at creation, authstore's reason: a permission widened and
        # narrowed back is a promise we cannot keep about who read it between.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.retention_days = retention_days
        self._local = threading.local()
        self._setup_lock = threading.Lock()
        self._pruned: dict[str, float] = {}

    # ------------------------------------------------------------ plumbing

    def path_for(self, org: str) -> Path:
        return self.root / f"{_check_org(org)}.db"

    def _connection(self, org: str) -> sqlite3.Connection:
        """This thread's connection to *org*'s database, opened on first use.

        The **setup** is serialised in-process (``_setup_lock``) even though
        the connections are not shared: two threads creating the same fresh
        database race on the journal-mode switch and on the schema DDL, and
        that race is not what ``busy_timeout`` covers (see
        :data:`JOURNAL_MODE_ATTEMPTS`). Once a database exists and is in WAL,
        the pragma is a no-op and this lock is never contended again.
        """
        org = _check_org(org)
        cache = getattr(self._local, "connections", None)
        if cache is None:
            cache = self._local.connections = {}
        conn = cache.get(org)
        if conn is not None:
            return conn
        path = self.root / f"{org}.db"
        with self._setup_lock:
            # `isolation_level=None` is autocommit: every INSERT lands and is
            # visible to the other process immediately. The alternative —
            # sqlite3's implicit transactions — holds a write open until
            # something commits, which is exactly how an audit row goes
            # missing after a crash.
            conn = sqlite3.connect(str(path), timeout=30.0,
                                   isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            for attempt in range(JOURNAL_MODE_ATTEMPTS):
                try:
                    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                except sqlite3.OperationalError:
                    mode = None
                if str(mode).lower() == "wal":
                    break
                time.sleep(JOURNAL_MODE_BACKOFF_S * (attempt + 1))
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
        try:
            os.chmod(path, 0o600)
        except OSError:                      # a bind mount owned elsewhere
            pass
        cache[org] = conn
        return conn

    def close(self) -> None:
        """Close **this thread's** connections. Other threads keep theirs —
        there is no registry of them, by design: a connection another thread is
        mid-INSERT on is not this thread's to close."""
        cache = getattr(self._local, "connections", None) or {}
        for conn in cache.values():
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local.connections = {}

    # -------------------------------------------------------------- append

    def append(self, org: str | None, row: dict | None = None, **fields) -> dict | None:
        """Record one event. ``None`` (and no disk write) when *org* is falsy.

        The falsy-org no-op **is** local mode: nothing sets a tenant outside
        hosted mode, so an audit tap installed everywhere costs an untenanted
        instance one dict lookup and writes nothing (FR4/AC7).

        Long values are clamped rather than refused — see
        :data:`MAX_PRINCIPAL_CHARS`. A missing ``action`` is a programming
        error and raises, because a row that does not say what happened is not
        an audit row.
        """
        if not org:
            return None
        record = {**(row or {}), **fields}
        action = record.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ValidationError("an audit row needs an action",
                                  {"row": sorted(record)})
        stored = {
            "ts": float(record.get("ts") or _now()),
            "principal": _clip(record.get("principal") or "unknown",
                               MAX_PRINCIPAL_CHARS),
            "action": _clip(action.strip(), MAX_ACTION_CHARS),
            "project": (_clip(record["project"], MAX_PROJECT_CHARS)
                        if record.get("project") else None),
            "args_digest": (str(record["args_digest"])[:64]
                            if record.get("args_digest") else None),
            "outcome": _clip(record.get("outcome") or "ok", MAX_OUTCOME_CHARS),
        }
        conn = self._connection(org)
        conn.execute(
            "INSERT INTO audit (ts, principal, action, project, args_digest, "
            "outcome) VALUES (?, ?, ?, ?, ?, ?)",
            (stored["ts"], stored["principal"], stored["action"],
             stored["project"], stored["args_digest"], stored["outcome"]))
        self._maybe_prune(org)
        return stored

    # --------------------------------------------------------------- query

    def query(self, org: str, *, principal: str | None = None,
              project: str | None = None, action: str | None = None,
              since: float | None = None, until: float | None = None,
              limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[dict]:
        """Newest first, filtered, bounded by *limit* (capped at
        :data:`MAX_LIMIT`).

        ``principal`` matches the **person** as well as their browsers: a
        composed identity is ``user:nikita/browser:7f3a1b2c``, so
        ``principal="user:nikita"`` matches every device that person used. An
        exact-match-only filter would answer "nothing" for the query an
        operator actually types, which is worse than no filter at all.
        """
        clauses, params = [], []
        if principal:
            clauses.append("(principal = ? OR principal LIKE ? ESCAPE '\\')")
            params += [principal, _like_prefix(f"{principal}/")]
        if project:
            clauses.append("project = ?")
            params.append(project)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(float(since))
        if until is not None:
            clauses.append("ts < ?")
            params.append(float(until))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        bounded = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
        rows = self._connection(org).execute(
            "SELECT id, ts, principal, action, project, args_digest, outcome "
            f"FROM audit{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            (*params, bounded, max(0, int(offset or 0)))).fetchall()
        return [_render(row) for row in rows]

    def count(self, org: str) -> int:
        """Every row in the org's database. The CLI's "showing N of M"."""
        return int(self._connection(org).execute(
            "SELECT COUNT(*) FROM audit").fetchone()[0])

    def orgs(self) -> list[str]:
        """Every org with an audit database, sorted. ``_instance`` included."""
        return sorted(path.stem for path in self.root.glob("*.db"))

    # ------------------------------------------------------------ retention

    def prune(self, org: str, days: float | None = None) -> int:
        """Delete rows older than *days*; returns how many went. ``0`` when
        neither the argument nor the instance sets a retention."""
        window = days if days is not None else self.retention_days
        if not window or window <= 0:
            return 0
        cutoff = _now() - float(window) * 86400.0
        cursor = self._connection(org).execute(
            "DELETE FROM audit WHERE ts < ?", (cutoff,))
        return int(cursor.rowcount or 0)

    def _maybe_prune(self, org: str) -> None:
        """At most one prune per org per :data:`PRUNE_INTERVAL_S`.

        Opportunistic rather than scheduled, because this process may be a CLI
        that lives for 40 ms and there is no daemon to hang a timer on.
        """
        if not self.retention_days:
            return
        now = _now()
        if now - self._pruned.get(org, 0.0) < PRUNE_INTERVAL_S:
            return
        self._pruned[org] = now
        try:
            self.prune(org)
        except sqlite3.Error:                # retention must never break append
            pass

    # -------------------------------------------------------------- backup

    def vacuum_into(self, org: str, dest: Path | str) -> Path:
        """Write a consistent copy of *org*'s database to *dest*.

        **``cp audit.db`` is not a backup and this method is why.** A WAL
        database keeps recent commits in the ``-wal`` sidecar until a
        checkpoint; the spike copied a 50-row database and the copy answered
        ``no such table: audit`` — every row was still in the WAL. ``VACUUM
        INTO`` (SQLite >= 3.27) writes a fully checkpointed, integrity-checked
        single file, and it does it **while writers keep working**, which is
        what makes it a backup for a running server. Older SQLite falls back to
        the online backup API, which has the same property.

        Refuses an existing destination: ``VACUUM INTO`` does, and silently
        overwriting somebody's previous backup is not an improvement.
        """
        dest = Path(dest)
        if dest.exists():
            raise ValidationError(
                f"{dest} already exists; a backup never overwrites one "
                f"(pick a dated name).", {"dest": str(dest)})
        dest.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connection(org)
        if sqlite3.sqlite_version_info >= (3, 27):
            conn.execute("VACUUM INTO ?", (str(dest),))
        else:                                # pragma: no cover - old SQLite
            target = sqlite3.connect(str(dest))
            try:
                conn.backup(target)
            finally:
                target.close()
        return dest

    def integrity(self, org: str) -> str:
        """``"ok"`` for a healthy database. What a restore is verified with."""
        return str(self._connection(org).execute(
            "PRAGMA integrity_check").fetchone()[0])


def parse_time(value: object, what: str = "time") -> float | None:
    """A ``since``/``until`` bound as epoch seconds, or ``None`` for absent.

    Three spellings, because three kinds of caller type them: epoch seconds
    (a script), ISO-8601 (``2026-08-24`` or ``2026-08-24T09:30:00Z`` — a
    person, a UI), and a relative window (``7d``, ``24h``, ``30m`` — an
    operator at a terminal, which is what the CLI's ``--since`` is for).

    A **naive** ISO value is read as UTC, not as the server's local zone, and
    it is stated rather than assumed: rows are stored as ``time.time()``
    (epoch, zone-free), an audit query that silently shifted by the container's
    ``TZ`` would quietly return the wrong day, and a container's ``TZ`` is
    usually UTC anyway.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text[-1:] in ("d", "h", "m", "s") and text[:-1].replace(".", "", 1).isdigit():
        seconds = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}[text[-1]]
        return _now() - float(text[:-1]) * seconds
    try:
        return float(text)
    except ValueError:
        pass
    from datetime import datetime, timezone

    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"{what} must be epoch seconds, an ISO-8601 time "
            f"(2026-08-24 or 2026-08-24T09:30:00Z) or a window like '7d', "
            f"'24h', '30m'.", {what: text[:64]}) from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def _clip(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


def _like_prefix(prefix: str) -> str:
    """``prefix%`` with LIKE's wildcards escaped — a principal may contain
    ``_``, which LIKE reads as "any character"."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def _render(row) -> dict:
    """A stored row, plus the one field that is **computed on every read**.

    ``kind`` is derived from the principal string rather than stored, the
    ``comments``/``anchors`` discipline: a classification that was written down
    is a classification that can disagree with the identity it describes.
    """
    return {
        "id": int(row["id"]),
        "ts": float(row["ts"]),
        "principal": row["principal"],
        "kind": actor_kind_of(row["principal"]),
        "action": row["action"],
        "project": row["project"],
        "args_digest": row["args_digest"],
        "outcome": row["outcome"],
    }


def actor_kind_of(principal: object) -> str:
    """``"human"`` or ``"agent"``, through ``proposals.actor_kind``.

    Imported lazily and never re-implemented: that function is the *one* place
    this product decides what counts as a person (``user:`` and ``browser:``
    are human; a bearer, the chat dock and everything else are agents), and a
    second copy here would be a second answer.
    """
    from .proposals import actor_kind

    return actor_kind(principal if isinstance(principal, str) else "")


# ------------------------------------------------------- shared instances

_shared_lock = threading.Lock()
_shared: dict[str, AuditLog] = {}


def shared(state_dir: Path | str, *,
           retention_days: float | None = None) -> AuditLog:
    """One :class:`AuditLog` per state directory per process.

    ``authstore._guard_for``'s registry idiom, for its reason: the route pack,
    the tool pack and the CLI all reach the same database, and three instances
    would mean three sets of connections and three prune clocks. The
    *connections* are still per thread — this shares the object, not the
    handle.
    """
    key = str(Path(state_dir).resolve())
    with _shared_lock:
        log = _shared.get(key)
        if log is None:
            log = _shared[key] = AuditLog(
                state_dir,
                retention_days=(retention_days if retention_days is not None
                                else retention_from_env()))
        return log


def for_auth_store(store, **kwargs) -> AuditLog:
    """The audit log beside an :class:`~agentcad.core.authstore.AuthStore`.

    The identity documents live in ``<state>/auth/``; the audit databases live
    in ``<state>/audit/``. Deriving one from the other keeps the route pack and
    the tool pack from each having their own opinion about where the state
    directory is — and it is the *store's* root rather than
    ``appmode.state_dir()`` because a test (and a second app in one process)
    builds a store on a path of its own.
    """
    return shared(Path(store.root).parent, **kwargs)


# ------------------------------------------------------------- the taps

#: Set on a wrapped ``call`` so installing the tap twice is a no-op — the
#: ``tools_structure``/``EventBus`` captured-original idiom, sentinel and all.
_TAPPED = "_agentcad_audit_tapped"

#: A tool whose name starts with one of these is a **read** and is not
#: recorded. Everything else is, including a tool nobody has classified: an
#: over-recorded read is noise, an unrecorded write is the failure that
#: matters, so the default leans the safe way.
READ_PREFIXES = (
    "get_", "list_", "find_", "search_", "read_", "describe_", "check_",
    "measure_", "inspect_", "preview_", "validate_", "compare_", "resolve_",
)

#: Read tools whose names do not start with a read prefix.
READ_NAMES = frozenset({
    "whoami", "part_template", "sync_status", "project_status",
})


def is_mutating_tool(name: object) -> bool:
    """Does a call to *name* deserve an audit row?"""
    if not isinstance(name, str) or not name:
        return True
    if name in READ_NAMES:
        return False
    return not name.startswith(READ_PREFIXES)


def current_principal_id() -> str:
    """Who is calling, in the spelling every other surface renders.

    Three sources, in order, and the order is the point:

    1. the hosted request's authenticated principal
       (``security.current_principal().client_id`` — ``user:nikita/browser:x``
       or ``agent:ci``), looked up through ``sys.modules`` so that ``core``
       never imports ``server`` (the ``tools_auth`` precedent);
    2. otherwise ``locks.current_client_id()``, which is how the **built-in
       chat agent** is distinguished: it sets ``"chat"`` (or
       ``"chat:<session>"``) inside its executor and has no HTTP principal at
       all (``agent/chat.py::_call_tool``);
    3. ``"local"``, the ContextVar's own default.

    Those three are exactly AC6's three principal kinds, and they are told
    apart by the string rather than by a flag any of them could set.
    """
    module = sys.modules.get("agentcad.server.security")
    who = module.current_principal() if module is not None else None
    if who is not None:
        return who.client_id
    return current_client_id()


def _current_org() -> str | None:
    """This request's org, or ``None`` in local mode."""
    from .tenancy import current_tenant

    tenant = current_tenant()
    return tenant[0] if tenant else None


def tap_registry(call, log: AuditLog, *, org=None, principal=None,
                 is_mutating=None):
    """Wrap a ``ToolRegistry.call`` so every mutating call writes one row.

    **Contract** (this is the hook ``tenancy_wiring.install`` consumes — it
    wraps the shared ``ToolRegistry`` at the serve seam):

    * ``call`` is any ``(name, args) -> dict`` — ``registry.call`` bound, or
      an already-wrapped one. The returned callable has the same signature and
      returns the same object; it never rewrites a result.
    * Exactly one row per mutating call, appended **after** the call, carrying
      ``outcome`` — ``"ok"``, the tool's own ``error.type`` (the house
      ``{"error": {...}}`` envelope), or ``raised:<ExceptionClass>`` for an
      exception, which is then re-raised unchanged.
    * ``org``/``principal``/``is_mutating`` are callables, overridable for
      tests; the defaults are :func:`_current_org` (so **local mode writes
      nothing**), :func:`current_principal_id` and :func:`is_mutating_tool`.
    * Idempotent: wrapping a wrapped call returns it unchanged
      (:data:`_TAPPED`), so a registry rebuilt behind a re-installed wrapper
      cannot end up recording twice.
    * A broken audit backend does **not** break the product: a
      ``sqlite3.Error`` while recording is swallowed with a warning on stderr.
      The trade is stated rather than hidden — a log that can silently drop is
      weaker evidence, and a CAD server that refuses writes because a database
      file went read-only is worse.
    """
    if getattr(call, _TAPPED, False):
        return call
    resolve_org = org or _current_org
    resolve_principal = principal or current_principal_id
    mutating = is_mutating or is_mutating_tool

    @functools.wraps(call)
    def _tapped(name, args=None):
        try:
            result = call(name, args)
        except BaseException as exc:         # noqa: BLE001 — recorded, re-raised
            _record(log, resolve_org(), resolve_principal(), name, args,
                    f"raised:{type(exc).__name__}", mutating)
            raise
        error = result.get("error") if isinstance(result, dict) else None
        outcome = "ok"
        if isinstance(error, dict):
            outcome = str(error.get("type") or "error")
        elif error:
            outcome = "error"
        _record(log, resolve_org(), resolve_principal(), name, args, outcome,
                mutating)
        return result

    setattr(_tapped, _TAPPED, True)
    return _tapped


def _record(log: AuditLog, org, principal, name, args, outcome, mutating) -> None:
    if not org or not mutating(name):
        return
    project = args.get("project") if isinstance(args, dict) else None
    try:
        log.append(org, {
            "principal": principal,
            "action": str(name),
            "project": project if isinstance(project, str) else None,
            "args_digest": args_digest(args),
            "outcome": outcome,
        })
    except (sqlite3.Error, OSError) as exc:  # see tap_registry's docstring
        print(f"agentcad: audit append failed ({type(exc).__name__}: {exc})",
              file=sys.stderr)


def record(log: AuditLog | None, org: str | None, action: str, *,
           principal: str | None = None, project: str | None = None,
           args: object = None, outcome: str = "ok") -> None:
    """Append one row from a **route** (or any non-registry caller).

    The same swallow-and-warn contract as :func:`tap_registry`, and the same
    "no org, no row" rule. ``principal`` defaults to
    :func:`current_principal_id`, which is what a tool call would have
    recorded — an auth route that knows better (a *failed* sign-in names the
    handle that was claimed, not one that authenticated) passes it explicitly.
    """
    if log is None or not org:
        return
    try:
        log.append(org, {
            "principal": principal or current_principal_id(),
            "action": action,
            "project": project,
            "args_digest": args_digest(args),
            "outcome": outcome,
        })
    except (sqlite3.Error, OSError) as exc:
        print(f"agentcad: audit append failed ({type(exc).__name__}: {exc})",
              file=sys.stderr)
