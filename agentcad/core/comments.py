"""Anchored review threads: the durable document, its lifecycle and audit.

A thread is a root comment plus ordered replies, pinned to something in the
model — a part, a face, a parameter, a script line range, an assembly instance
or a proposal diff hunk — with state ``open``/``resolved`` (PRD-008 FR4). This
module is the whole of that workflow and nothing else: it writes files and
validates anchors, and it never builds, renders or touches git.

**Where the state lives.** ``<project>/.history/agentcad/comments/``, beside
PRD-002's ``proposals/`` and PRD-001's ``config.json``/``checkouts.json``, and
therefore inside GIT_DIR. Three consequences, all deliberate: ``git add -A``
can never stage a thread (discussion is not model state), ``project_restore``
— which is ``git checkout <commit> -- .`` in a *working tree* — structurally
cannot rewind one (AC8 is true by construction, not by an exclude line someone
must keep correct), and every branch sees the same list, because a comment on
a face must be visible from wherever you are working and belong to no branch.
Paths always come from ``store.canonical_path_of`` — never ``path_of``, which
follows the caller's branch.

```
.history/agentcad/comments/
  next_id             "8\\n"     the id high-water mark (NOT a cache)
  index.json          {"next_id": 8, "threads": [summary, …]}   (a CACHE)
  notifications.jsonl mention + read lines, append-only, one log per project
  7/thread.json · 7/audit.jsonl
```

**Durability rules — PRD-002's, copied rather than reinvented.**
``thread.json``/``index.json``/``next_id`` are ``ProjectStore._atomic_write``;
``audit.jsonl`` is *appended* and never rewritten (a read-modify-replace cycle
loses append-only-ness and can truncate the log on a crash), and there is
deliberately no method here that edits or removes a line. ``index.json`` is
rebuilt from the per-thread directories whenever it is missing or unparseable,
so it is never the source of truth; ids are decimal strings from ``next_id``,
which only increments, so a directory deleted by hand cannot hand its id to
the next thread.

**The anchor is immutable.** It is written once, at creation, and never
updated: a stored anchor is evidence of what the author pointed at, and
evidence that rewrites itself is not evidence. Where that target is *now* is
computed at read time by ``core/anchors.py`` (PRD-008 slice 2) into four
states — ``ok``/``moved``/``orphaned``/``unverified`` — and nothing in this
module stores a status.

**Attribution.** The actor is ``locks.current_client_id()`` — the identity
turn locks and branch checkouts already key on — and :func:`actor_kind` is
imported from ``proposals.py``, never re-implemented. That is bookkeeping, not
authentication (the header is unvalidated until PRD-005): editing and deleting
are restricted to a comment's own author as an honesty check, and anyone may
resolve or reopen anything, because there is no authentication to base a rule
on. The audit says who did.

``face``, ``script_range`` and ``proposal_hunk`` anchors are validated by
``core/anchors.py``, which also annotates every *view* — never the document —
with a ``resolution`` block. A ``proposal_hunk`` anchor reads PRD-002's
**persisted** ``packet.json`` and never ``service.packets.packet(...)``, which
rebuilds geometry and can move a proposal's state.

**Mentions.** ``@<identity>`` in a body notifies that identity — but only when
it names a *plausible* one (``browser``, ``browser:<nonce>``, ``chat``,
``chat:<session>``, or a client the presence registry currently knows).
``@todo`` stays prose. Deliveries are appended to a single per-project
``notifications.jsonl`` with a ``to`` field, never one file per identity: an
identity is an arbitrary string from an unvalidated header, and a filename
derived from one is a path-traversal surface. ``read`` is a *line* in the same
log, so unread is derived (mentions minus every seq a later ``read`` line
names) and nothing is ever rewritten.

**Events.** Every mutation publishes exactly one ``comment_changed`` through
``service.bus.publish`` (:meth:`CommentManager._publish`), and an idempotent
no-op publishes none. It is deliberately **not** ``project_changed``: that
event's ``on_publish`` hook snapshots project history, and a comment is not a
model change. ``bus.on_publish`` itself is a single slot already owned by
``service._snapshot_on_event`` and is never assigned here. Each mention adds a
``notification`` event beside that one, published from the same seam.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from . import anchors, locks
from .model import NotFoundError, ValidationError
from .project import ProjectStore
from .proposals import actor_kind

STATES = ("open", "resolved")
ANCHOR_KINDS = ("part", "face", "param", "script_range", "instance",
                "proposal_hunk")
ACTIONS = ("created", "replied", "resolved", "reopened", "comment_edited",
           "comment_deleted", "mentioned")
MAX_ATTACHMENTS = 8
#: The same cap, for the same reason, on the other unbounded list a single
#: comment carries. Each deliverable mention mints a ``notifications.jsonl``
#: line AND a broadcast WebSocket frame, so an uncapped body was one mutation
#: costing a thousand records and a thousand frames. Handles beyond the cap are
#: not silently dropped — the body is stored verbatim and never rewritten, so a
#: dropped mention would be an ``@name`` sitting in the text that quietly
#: notified nobody. The comment is refused instead, with ``{max, given}``.
MAX_MENTIONS = 8
MAX_BODY_BYTES = 16 * 1024
# The cap on the snippet a script_range anchor stores as its evidence; read by
# the anchor builder in core/anchors.py, which writes into this envelope.
MAX_SNIPPET_LINES, MAX_SNIPPET_BYTES = 40, 4096

_ID_RE = re.compile(r"^[1-9][0-9]{0,17}$")

# ``@handle`` anywhere in a body. The lookbehind is load-bearing: it keeps
# ``nobody@example.com`` an address and ``@@chat`` prose, so neither delivers.
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_.:-]{1,64})")
# Sentence punctuation the character class swallows. "ping @chat:main." names
# a session; stripping these before the plausibility check only ever turns a
# non-identity into an identity, never one identity into another.
_MENTION_TRAILING = ".:-"
# The two identity families that exist without a presence registry. Anything
# else must be *currently present* to be mentionable — see
# :func:`plausible_mention`.
_BROWSER, _CHAT = "browser", "chat"
_BROWSER_NONCE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

NOTIFICATION_KINDS = ("mention", "read")

# The exact fields each kind carries, whitelisted before anything is stored:
# an anchor reaches here from a REST body and from a tool argument, and a
# stray key would be persisted forever as evidence of nothing.
_ANCHOR_FIELDS: dict[str, tuple[str, ...]] = {
    "part": ("part",),
    "face": ("part", "face_index"),
    "param": ("part", "param"),
    "script_range": ("part", "start", "end"),
    "instance": ("instance",),
    "proposal_hunk": ("proposal", "file", "hunk"),
}

# The evidence each kind captures AT CREATION, from the geometry or the script
# itself. Like ``_PROVENANCE`` below these are stamped here and refused from a
# caller: a signature a client can assert is not evidence of anything, and an
# anchor whose snippet does not match the script it names would resolve against
# a fiction. They are stored on the anchor beside the required fields.
_ANCHOR_EVIDENCE: dict[str, tuple[str, ...]] = {
    "face": ("signature",),
    "script_range": ("snippet", "snippet_sha256", "before", "after"),
    # The hunk's header byte-for-byte and the packet generation it was read
    # from: the two things Decision 8 re-maps a regenerated packet by.
    "proposal_hunk": ("hunk_header", "generation"),
}

# The tool/REST surface says ``part_id``/``instance_id`` (FR1's wording); the
# stored envelope says ``part``/``instance`` (the design spec's). Both spell
# the same anchor, so both are accepted and one is stored.
_ANCHOR_ALIASES = {"part_id": "part", "instance_id": "instance"}

# Stamped by this module from the caller's branch, never taken from the
# caller: provenance a client can write is not provenance.
_PROVENANCE = ("branch", "head")

_EXPORTS = "exports"


def _now() -> str:
    """UTC, ISO-8601, zone-aware — ``proposals._now``'s stamp, so an audit
    line from either log reads the same way."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_mentions(body: object) -> list[str]:
    """Every ``@handle`` in *body*, in order, deduplicated.

    Pure syntax — plausibility is :func:`plausible_mention`'s job, and the body
    itself is never rewritten: a handle that delivers nothing stays exactly
    where the author typed it.
    """
    if not isinstance(body, str):
        return []
    found: list[str] = []
    for match in _MENTION_RE.finditer(body):
        handle = match.group(1)
        if handle not in found:
            found.append(handle)
    return found


def plausible_mention(handle: object, present: object = ()) -> bool:
    """Does *handle* name an identity that could exist?

    ``browser``, ``browser:<nonce>``, ``chat``, ``chat:<session>`` (validated
    with the chat engine's own ``SESSION_ID_RE`` — imported, never re-written)
    or a client id in *present*, the presence registry's roster.

    Everything else is prose: ``@todo`` and ``@nobody`` are not notifications
    waiting for a recipient, and minting one for them would fill a drawer with
    noise nobody can act on.
    """
    # Imported lazily: ``agent.chat`` imports ``core.tools``, which imports
    # this module's pack, and the dependency only points that way.
    from ..agent.chat import SESSION_ID_RE

    if not isinstance(handle, str) or not handle:
        return False
    if handle in (_BROWSER, _CHAT):
        return True
    if handle in (present or ()):
        return True
    prefix, sep, rest = handle.partition(":")
    if not sep or not rest:
        return False
    if prefix == _CHAT:
        return bool(SESSION_ID_RE.fullmatch(rest))
    if prefix == _BROWSER:
        return bool(_BROWSER_NONCE_RE.match(rest))
    return False


class CommentStore:
    """Files only: no anchor validation, no lifecycle rules, no events.

    One ``threading.RLock`` (``ProposalStore``'s precedent) serializes id
    allocation, index refreshes and audit appends within the process.
    """

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._lock = threading.RLock()
        # Line counts of the append-only logs, carried forward instead of
        # recounted — see _next_seq.
        self._seq: dict[Path, int] = {}

    # ------------------------------------------------------------ locations

    def dir_of(self, proj: str) -> Path:
        return self.store.canonical_path_of(proj) / ".history" / "agentcad" \
            / "comments"

    def _thread_dir(self, proj: str, tid: str) -> Path:
        return self.dir_of(proj) / self._valid_id(tid)

    @staticmethod
    def _valid_id(tid: object) -> str:
        # Ids reach here from a REST path segment as well as from a tool
        # argument, so they are whitelisted before they touch the filesystem.
        if not isinstance(tid, str) or not _ID_RE.match(tid):
            raise NotFoundError(f"thread {tid!r} not found")
        return tid

    # ---------------------------------------------------------------- threads

    def load(self, proj: str, tid: str) -> dict:
        path = self._thread_dir(proj, tid) / "thread.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NotFoundError(f"thread {tid!r} not found") from exc
        if not isinstance(data, dict):
            raise NotFoundError(f"thread {tid!r} not found")
        return data

    def save(self, proj: str, thread: dict) -> None:
        tid = self._valid_id(thread.get("id"))
        ProjectStore._atomic_write(
            self._thread_dir(proj, tid) / "thread.json",
            json.dumps(thread, indent=2).encode(),
        )
        with self._lock:
            index, _ok = self._read_index(proj)
            summaries = [s for s in index["threads"] if s.get("id") != tid]
            summaries.append(_summary(thread))
            index["threads"] = _sorted(summaries)
            index["next_id"] = max(index["next_id"], int(tid) + 1)
            self._write_index(proj, index)

    def list(self, proj: str) -> list[dict]:
        """Every thread, oldest id first, read from the directories.

        The directories are the source of truth; ``index.json`` is refreshed
        here when it disagrees, which is what makes it a rebuildable cache.
        """
        base = self.dir_of(proj)
        threads = []
        if base.is_dir():
            for entry in sorted(base.iterdir(), key=_id_key):
                if not entry.is_dir() or not _ID_RE.match(entry.name):
                    continue
                try:
                    threads.append(self.load(proj, entry.name))
                except NotFoundError:
                    continue  # a half-written or hand-mangled directory
        with self._lock:
            index, ok = self._read_index(proj)
            desired = {
                "next_id": max(
                    index["next_id"],
                    max((int(t["id"]) for t in threads), default=0) + 1,
                    # The high-water mark outlives both the index and the
                    # directories: it is the only thing that remembers an id
                    # whose thread was deleted by hand.
                    self._high_water(proj),
                ),
                "threads": _sorted([_summary(t) for t in threads]),
            }
            if not ok or index != desired:
                self._write_index(proj, desired)
        return threads

    def allocate_id(self, proj: str) -> str:
        """The next never-used id. Monotonic even when a thread directory was
        removed by hand AND the index was then lost: the counter is persisted
        separately and written BEFORE the directory it names exists — a crash
        in between costs an id, it never reuses one."""
        with self._lock:
            self.list(proj)  # refreshes next_id past any hand-made directory
            index, _ok = self._read_index(proj)
            tid = str(max(1, int(index["next_id"]), self._high_water(proj)))
            self._write_high_water(proj, int(tid) + 1)
            index["next_id"] = int(tid) + 1
            self._write_index(proj, index)
            return tid

    # ------------------------------------------------------------------ index

    def _high_water_path(self, proj: str) -> Path:
        return self.dir_of(proj) / "next_id"

    def _high_water(self, proj: str) -> int:
        """The lowest id never handed out, from its own tiny file. 0 when
        there is none (a store written before this file existed)."""
        try:
            value = int(
                self._high_water_path(proj).read_text(encoding="utf-8").strip()
            )
        except (OSError, ValueError):
            return 0
        return value if value >= 1 else 0

    def _write_high_water(self, proj: str, value: int) -> None:
        if value > self._high_water(proj):
            ProjectStore._atomic_write(
                self._high_water_path(proj), f"{value}\n".encode()
            )

    def _read_index(self, proj: str) -> tuple[dict, bool]:
        """(index, parsed_cleanly). A missing or corrupt index is not an
        error — it is rebuilt from the directories."""
        path = self.dir_of(proj) / "index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"next_id": 1, "threads": []}, False
        if not isinstance(data, dict):
            return {"next_id": 1, "threads": []}, False
        next_id = data.get("next_id")
        rows = data.get("threads")
        return (
            {
                "next_id": next_id if isinstance(next_id, int) and next_id >= 1
                else 1,
                "threads": [r for r in rows if isinstance(r, dict)]
                if isinstance(rows, list) else [],
            },
            isinstance(next_id, int) and isinstance(rows, list),
        )

    def _write_index(self, proj: str, index: dict) -> None:
        ProjectStore._atomic_write(
            self.dir_of(proj) / "index.json",
            json.dumps(index, indent=2).encode(),
        )

    # ------------------------------------------------------------------ audit

    def append_audit(self, proj: str, tid: str, entry: dict) -> dict:
        """Append one line to ``audit.jsonl`` and return the stored entry.

        Appended, never rewritten — the log is the only record of who said
        what and when, and a rewrite is how such a record stops being one.
        """
        path = self._thread_dir(proj, tid) / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        actor = entry.get("actor") or locks.current_client_id()
        with self._lock:
            record = {
                "seq": self._next_seq(path),
                "ts": entry.get("ts") or _now(),
                "actor": actor,
                "actor_kind": entry.get("actor_kind") or actor_kind(actor),
                "action": entry.get("action") or "updated",
                "details": entry.get("details") or {},
            }
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
                handle.flush()
        return record

    def audit(self, proj: str, tid: str) -> list[dict]:
        """Every entry, in order. A corrupt line (a torn write) is skipped
        rather than raised — the rest of the log is still evidence."""
        return self._read_lines(self._thread_dir(proj, tid) / "audit.jsonl")

    # ---------------------------------------------------------- notifications

    def notifications_path(self, proj: str) -> Path:
        return self.dir_of(proj) / "notifications.jsonl"

    def append_notification(self, proj: str, entry: dict) -> dict:
        """Append one line to ``notifications.jsonl`` and return it.

        **One log per project, with a ``to`` field** — never a file per
        identity. An identity is an arbitrary string from an unvalidated
        header, so a filename derived from one is a path-traversal surface;
        a shared log needs no sanitizing at all. ``read`` is a *line* rather
        than a mutation for the same reason ``audit.jsonl`` is: an append-only
        record that is rewritten to mark something is no longer append-only.
        """
        path = self.notifications_path(proj)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            record = {"seq": self._next_seq(path),
                      "kind": entry.get("kind") or "mention",
                      **{key: value for key, value in entry.items()
                         if key not in ("seq", "kind")},
                      "ts": entry.get("ts") or _now()}
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
                handle.flush()
        return record

    def notifications(self, proj: str) -> list[dict]:
        """Every line of the project's log, in order, corrupt lines skipped."""
        return self._read_lines(self.notifications_path(proj))

    @staticmethod
    def _read_lines(path: Path) -> list[dict]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                entries.append(record)
        return entries

    def _next_seq(self, path: Path) -> int:
        """The next 1-based line number for an append-only log.

        Counted *forward* rather than by re-reading the file, which made every
        append O(n) and a burst of them O(n²) — and a single comment can drive
        :data:`MAX_MENTIONS` appends. The count is re-derived from the file
        whenever it is not already known or the file is not there (a project
        deleted and recreated under a long-lived store), so it is always the
        number a full re-count would give. Callers hold ``self._lock``.
        """
        known = self._seq.get(path)
        if known is None or not path.exists():
            known = self._line_count(path)
        self._seq[path] = known + 1
        return known + 1

    @staticmethod
    def _line_count(path: Path) -> int:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return 0
        return len([line for line in text.splitlines() if line.strip()])


def _summary(thread: dict) -> dict:
    """The list-view row: what a panel, a badge and a filter need, without the
    comment bodies."""
    anchor = thread.get("anchor") or {}
    return {
        "id": thread.get("id"),
        "project": thread.get("project"),
        "state": thread.get("state"),
        "kind": anchor.get("kind"),
        "part": anchor.get("part"),
        "branch": thread.get("branch"),
        "author": thread.get("author"),
        "author_kind": thread.get("author_kind"),
        "created": thread.get("created"),
        "updated": thread.get("updated"),
        "comments": len(thread.get("comments") or []),
    }


def _sorted(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


def _id_key(path: Path) -> tuple[int, str]:
    return (int(path.name) if path.name.isdigit() else 1 << 62, path.name)


class CommentManager:
    """The lifecycle: anchor validation, replies, resolve/reopen, edits.

    Owns one ``threading.RLock`` around every read-modify-write of a thread
    document, so ``thread.json`` has exactly one serialization point.
    ``service.branches`` is read inside methods, never captured in
    ``__init__``: the pack that installs this manager (``tools_comments``,
    ``c``) is imported before ``tools_versioning`` (``v``).
    """

    def __init__(self, service) -> None:
        self.service = service
        self.store = CommentStore(service.store)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ public api

    def create(self, proj: str, anchor: object, body: object,
               attachments: object = None) -> dict:
        """Open a thread on ``anchor``. Returns the post-state thread."""
        self.service.store.manifest(proj)  # existence check -> notfound_error
        anchor = self._anchor(proj, anchor)
        body = _body(body)
        files = self._attachments(proj, attachments)
        actor = locks.current_client_id()
        mentions = self._mentions(proj, body)
        with self._lock:
            tid = self.store.allocate_id(proj)
            stamp = _now()
            thread = {
                "id": tid,
                "project": proj,
                "state": "open",
                "anchor": anchor,
                "branch": anchor["branch"],
                "author": actor,
                "author_kind": actor_kind(actor),
                "created": stamp,
                "updated": stamp,
                "resolved": None,
                "comments": [_comment("1", actor, stamp, body, files,
                                      mentions)],
            }
            self.store.save(proj, thread)
            details = {"kind": anchor["kind"], "comment": "1"}
            if anchor.get("part"):
                details["part"] = anchor["part"]
            self.store.append_audit(proj, tid, {"action": "created",
                                                "details": details})
            notices = self._deliver(proj, tid, "1", actor, mentions)
        return self._publish(proj, self._view(proj, thread), "created", notices)

    def reply(self, proj: str, tid: str, body: object,
              attachments: object = None) -> dict:
        """Append a comment. A resolved thread still takes replies — the
        conversation about a decision outlives the decision."""
        body = _body(body)
        files = self._attachments(proj, attachments)
        actor = locks.current_client_id()
        mentions = self._mentions(proj, body)
        with self._lock:
            thread = self.store.load(proj, tid)
            cid = _next_comment_id(thread)
            stamp = _now()
            thread["comments"].append(
                _comment(cid, actor, stamp, body, files, mentions))
            thread["updated"] = stamp
            self.store.save(proj, thread)
            self.store.append_audit(proj, tid, {"action": "replied",
                                                "details": {"comment": cid}})
            notices = self._deliver(proj, tid, cid, actor, mentions)
        return self._publish(proj, self._view(proj, thread), "replied", notices)

    def resolve(self, proj: str, tid: str) -> dict:
        return self._set_state(proj, tid, "resolved")

    def reopen(self, proj: str, tid: str) -> dict:
        return self._set_state(proj, tid, "open")

    def edit_comment(self, proj: str, tid: str, cid: str, body: object) -> dict:
        """Replace a comment's body — its own author only.

        The audit records ``previous_sha256`` rather than the previous text:
        proof that the text changed, without an edit trail that retains what
        the author took back.
        """
        body = _body(body)
        actor = locks.current_client_id()
        with self._lock:
            thread = self.store.load(proj, tid)
            comment = _comment_of(thread, tid, cid)
            _check_author(comment, actor, tid)
            if comment.get("deleted"):
                raise ValidationError(
                    f"comment {cid} of thread {tid} was deleted",
                    {"thread": tid, "comment": cid},
                )
            previous = hashlib.sha256(
                (comment.get("body") or "").encode("utf-8")
            ).hexdigest()
            stamp = _now()
            # An edit re-scans: leaving the recorded mentions to describe a
            # body that no longer contains them would make the field a lie,
            # and a mistyped handle would be unfixable. Only the *newly*
            # mentioned are delivered — nobody is notified twice for one
            # comment, and "already delivered" is read from the audit log
            # rather than from ``comment["mentions"]``, which an edit rewrites:
            # removing a handle and putting it back used to erase the only
            # record of the first delivery and ring the same person again.
            already = self._delivered(proj, tid, cid)
            mentions = self._mentions(proj, body)
            comment["body"] = body
            comment["mentions"] = mentions
            comment["edited"] = stamp
            thread["updated"] = stamp
            self.store.save(proj, thread)
            self.store.append_audit(proj, tid, {
                "action": "comment_edited",
                "details": {"comment": cid, "previous_sha256": previous},
            })
            notices = self._deliver(
                proj, tid, cid, actor,
                [m for m in mentions if m not in already])
        return self._publish(proj, self._view(proj, thread), "comment_edited",
                             notices)

    def delete_comment(self, proj: str, tid: str, cid: str) -> dict:
        """Tombstone a comment — its own author only, and never the root.

        A thread is retired by resolving it; deleting its root would delete
        the anchor and the reason the thread exists, leaving replies pointing
        at nothing.
        """
        actor = locks.current_client_id()
        with self._lock:
            thread = self.store.load(proj, tid)
            comment = _comment_of(thread, tid, cid)
            if thread["comments"][0] is comment:
                raise ValidationError(
                    f"the root comment of thread {tid} cannot be deleted; "
                    "resolve the thread instead",
                    {"thread": tid, "comment": cid},
                )
            _check_author(comment, actor, tid)
            if comment.get("deleted"):
                return self._view(proj, thread)  # idempotent
            stamp = _now()
            comment["deleted"] = True
            comment["body"] = None
            comment["attachments"] = []
            thread["updated"] = stamp
            self.store.save(proj, thread)
            self.store.append_audit(proj, tid, {
                "action": "comment_deleted", "details": {"comment": cid},
            })
        return self._publish(proj, self._view(proj, thread), "comment_deleted")

    def get(self, proj: str, tid: str) -> dict:
        self.service.store.manifest(proj)  # existence check -> notfound_error
        return self._view(proj, self.store.load(proj, tid))

    def list(self, proj: str, state: str | None = None,
             kind: str | None = None, part_id: str | None = None,
             branch: str | None = None, anchor_status: str | None = None,
             proposal: str | None = None,
             resolve_anchors: bool = True) -> dict:
        """``{threads, counts}``, each thread carrying its live ``resolution``.

        ``counts`` describes the whole project, not the filtered page — a
        badge saying "2 open" must not change because a filter is applied — so
        every thread is resolved even when the page shows a few. That stays
        cheap by construction: resolution reads the manifest, at most one
        cached face table per part and at most one git blob per anchor, and
        **never builds** (design Decision 4). ``resolve_anchors=False`` is the
        cheapest possible listing: no ``resolution`` block at all, and no
        ``orphaned`` count, because nothing was looked at.

        ``proposal`` narrows to one change proposal's hunk threads, so the
        proposals UI fetches exactly its own conversation in one call.
        """
        self.service.store.manifest(proj)  # existence check -> notfound_error
        if state is not None and state not in STATES:
            raise ValidationError(f"unknown thread state {state!r}",
                                  {"allowed": list(STATES)})
        if kind is not None and kind not in ANCHOR_KINDS:
            raise ValidationError(f"unknown anchor kind {kind!r}",
                                  {"allowed": list(ANCHOR_KINDS)})
        if anchor_status is not None and anchor_status not in anchors.RESOLUTION:
            raise ValidationError(f"unknown anchor status {anchor_status!r}",
                                  {"allowed": list(anchors.RESOLUTION)})
        if anchor_status is not None and not resolve_anchors:
            raise ValidationError(
                "anchor_status filters on a resolution that "
                "resolve_anchors=false never computes",
                {"anchor_status": anchor_status})
        threads = self.store.list(proj)
        counts = {name: 0 for name in STATES}
        if resolve_anchors:
            counts["orphaned"] = 0
        # One branch/head resolution for the whole page, not one per thread.
        context = self._context(proj)
        rows = []
        for thread in threads:
            # ``STATES``, not ``counts``: ``orphaned`` is an anchor's status,
            # not a thread's state, and a hand-edited document must not be able
            # to increment it by claiming to be in it.
            if thread.get("state") in STATES:
                counts[thread["state"]] += 1
            view = self._view(proj, thread, context=context,
                              resolve_anchors=resolve_anchors)
            status = (view.get("resolution") or {}).get("status")
            if status == "orphaned":
                counts["orphaned"] += 1
            anchor = thread.get("anchor") or {}
            if ((state is None or thread.get("state") == state)
                    and (kind is None or anchor.get("kind") == kind)
                    and (part_id is None or anchor.get("part") == part_id)
                    and (branch is None or thread.get("branch") == branch)
                    and (proposal is None
                         or anchor.get("proposal") == proposal)
                    and (anchor_status is None or status == anchor_status)):
                rows.append(view)
        return {"threads": rows, "counts": counts}

    def audit(self, proj: str, tid: str) -> list[dict]:
        self.store.load(proj, tid)  # existence check -> notfound_error
        return self.store.audit(proj, tid)

    # --------------------------------------------------------- notifications

    def notifications(self, proj: str) -> list[dict]:
        """Every delivery recorded for a project, for any identity.

        The raw ``mention`` lines — no ``read`` bookkeeping and no identity
        filter. :meth:`list_notifications` is what a *client* calls; this is
        the project-wide view the drawer's counts and the tests read.
        """
        return [record for record in self.store.notifications(proj)
                if record.get("kind") == "mention"]

    def list_notifications(self, project: str | None = None,
                           unread: bool = False) -> dict:
        """``{notifications, unread}`` for the **calling identity only**.

        Oldest first, each record carrying ``read``. Omitting ``project``
        answers across every project on this server, because a drawer is a
        per-person inbox and a mention does not stop mattering when you switch
        projects.
        """
        identity = locks.current_client_id()
        rows: list[dict] = []
        for proj in self._projects(project):
            rows.extend(self._for(identity, self.store.notifications(proj)))
        pending = len([row for row in rows if not row["read"]])
        if unread:
            rows = [row for row in rows if not row["read"]]
        return {"notifications": rows, "unread": pending}

    def mark_read(self, proj: str, ids: object = None) -> dict:
        """Mark this identity's notifications read by appending a ``read``
        line. Omitting ``ids`` marks every unread one.

        A route rather than a tool (design Decision 11): agents do not need a
        read cursor, and FR7 freezes the agent surface at five tools.
        """
        self.service.store.manifest(proj)  # existence check -> notfound_error
        identity = locks.current_client_id()
        with self._lock:
            rows = self._for(identity, self.store.notifications(proj))
            mine = {row["seq"] for row in rows if isinstance(row.get("seq"), int)}
            unread = [row["seq"] for row in rows
                      if not row["read"] and isinstance(row.get("seq"), int)]
            if ids is None:
                marked = unread
            else:
                marked = [seq for seq in _read_ids(ids, mine) if seq in unread]
            if marked:
                self.store.append_notification(
                    proj, {"kind": "read", "to": identity, "ids": marked})
        return {"marked": marked, "unread": len(set(unread) - set(marked))}

    def _projects(self, project: str | None) -> list[str]:
        if project is not None:
            self.service.store.manifest(project)  # -> notfound_error
            return [project]
        return [row["name"] for row in self.service.store.list_projects()
                if isinstance(row.get("name"), str)]

    @staticmethod
    def _for(identity: str, lines: list[dict]) -> list[dict]:
        """This identity's mention lines, each flagged ``read``.

        Unread is *derived*: the mention seqs addressed to an identity minus
        every seq named by a ``read`` line for the same identity. Nothing is
        ever rewritten to record a read, which is what keeps the log
        append-only.
        """
        read: set[int] = set()
        mentions: list[dict] = []
        for record in lines:
            if record.get("to") != identity:
                continue
            if record.get("kind") == "read":
                read.update(seq for seq in record.get("ids") or []
                            if isinstance(seq, int))
            elif record.get("kind") == "mention":
                mentions.append(record)
        return [{**record, "read": record.get("seq") in read}
                for record in mentions]

    # ------------------------------------------------------------- internals

    def _set_state(self, proj: str, tid: str, state: str) -> dict:
        """``resolved``/``open``, idempotently. Resolving a resolved thread
        records nothing: a no-op is not an event, and an audit full of them
        stops being readable.

        Anyone may resolve or reopen anything — there is no authentication to
        base a rule on (PRD-005), and the audit says who did.
        """
        actor = locks.current_client_id()
        with self._lock:
            thread = self.store.load(proj, tid)
            if thread.get("state") == state:
                return self._view(proj, thread)
            stamp = _now()
            thread["state"] = state
            thread["updated"] = stamp
            thread["resolved"] = (
                {"actor": actor, "actor_kind": actor_kind(actor), "ts": stamp}
                if state == "resolved" else None
            )
            self.store.save(proj, thread)
            self.store.append_audit(proj, tid, {
                "action": "resolved" if state == "resolved" else "reopened",
                "details": {"state": state},
            })
        return self._publish(
            proj, self._view(proj, thread),
            "resolved" if state == "resolved" else "reopened")

    def _mentions(self, proj: str, body: str) -> list[str]:
        """The plausible identities *body* names, in order, deduplicated.

        Implausible handles are dropped here and nowhere else: the body is
        stored verbatim, so ``@todo`` survives in the text and delivers
        nothing.

        More than :data:`MAX_MENTIONS` deliverable handles is refused rather
        than truncated — see the constant for why.
        """
        present = self._present_ids(proj)
        found: list[str] = []
        for handle in parse_mentions(body):
            if not plausible_mention(handle, present):
                # "ping @chat:main." — the character class swallows sentence
                # punctuation, so retry once without it. This can only turn a
                # non-identity into an identity, never one into another.
                handle = handle.rstrip(_MENTION_TRAILING)
                if not plausible_mention(handle, present):
                    continue
            if handle not in found:
                found.append(handle)
        if len(found) > MAX_MENTIONS:
            raise ValidationError(
                f"at most {MAX_MENTIONS} mentions per comment",
                {"max": MAX_MENTIONS, "given": len(found)},
            )
        return found

    def _present_ids(self, proj: str) -> set[str]:
        """Client ids the presence registry currently knows, or none.

        An optional seam read lazily like every other cross-pack seam: the
        presence pack (PRD-008 slice 6) installs ``service.presence`` and
        exposes ``mention_ids(project)``; without it, the two static identity
        families are all a mention can name.
        """
        getter = getattr(getattr(self.service, "presence", None),
                         "mention_ids", None)
        if getter is None:
            return set()
        try:
            return {cid for cid in getter(proj) if isinstance(cid, str)}
        except Exception:                                  # noqa: BLE001
            # Presence is ephemeral and best-effort; a registry that cannot
            # answer must not stop a comment from being written.
            return set()

    def _delivered(self, proj: str, tid: str, cid: str) -> set[str]:
        """Everyone a notification for *this comment* has already gone to.

        Read from the thread's ``audit.jsonl`` — the append-only record
        :meth:`_deliver` writes — because that is the only account of a
        delivery that an edit cannot rewrite.
        """
        return {who
                for entry in self.store.audit(proj, tid)
                if entry.get("action") == "mentioned"
                and (entry.get("details") or {}).get("comment") == cid
                for who in (entry.get("details") or {}).get("to") or ()
                if isinstance(who, str)}

    def _deliver(self, proj: str, tid: str, cid: str, actor: str,
                 mentions: list[str]) -> list[dict]:
        """Record one ``mention`` line per recipient and return the events.

        Self-mentions deliver nothing — you do not need telling that you wrote
        your own name — and a comment that mentions nobody records nothing at
        all, so the log stays a record of deliveries rather than of scans.
        """
        recipients = [who for who in mentions if who != actor]
        if not recipients:
            return []
        notices = []
        for who in recipients:
            record = self.store.append_notification(proj, {
                "kind": "mention", "to": who, "project": proj, "thread": tid,
                "comment": cid, "from": actor,
            })
            notices.append({
                "type": "notification", "to": who, "project": proj,
                "thread": tid, "comment": cid, "from": actor,
                "ts": record["ts"],
            })
        self.store.append_audit(proj, tid, {
            "action": "mentioned",
            "details": {"comment": cid, "to": recipients},
        })
        return notices

    def _publish(self, proj: str, view: dict, action: str,
                 notices: list[dict] | None = None) -> dict:
        """Announce one mutation and hand the view straight back.

        Called from the single return point of each mutator, so every mutation
        publishes exactly once and an idempotent no-op — which returns
        earlier — publishes nothing: a no-op is not an event, and five clients
        re-resolving a resolved thread must not be five events.

        The type is ``comment_changed`` and **never** ``project_changed``: the
        bus's ``on_publish`` hook snapshots project history on that one, and a
        comment is not a model change. The payload is deliberately a *pointer*
        (design Decision 12) — id, state, action and the part it hangs on — so
        a UI re-reads what it needs and no comment body is fanned out to every
        connected client.
        """
        bus = getattr(self.service, "bus", None)
        if bus is not None:
            bus.publish({
                "type": "comment_changed",
                "project": proj,
                "thread": view.get("id"),
                "state": view.get("state"),
                "action": action,
                "part": (view.get("anchor") or {}).get("part"),
            })
            # …then one ``notification`` per mention, after the mutation is
            # announced, so a client that reacts to a notification by reading
            # the thread finds the comment that mentions it. The bus is a
            # BROADCAST: every /ws client receives every notification and
            # filters on ``to``. That is honest for a single-user,
            # 127.0.0.1-only server with no authentication — per-principal
            # delivery is PRD-005 — and it is said out loud in the tool
            # description and in docs/agent-api.md rather than implied.
            for notice in notices or ():
                bus.publish(notice)
        return view

    def _context(self, proj: str) -> dict:
        """What one page of threads is read against: the project root, the
        reader's branch and its head. Resolved once and passed down, never
        once per thread."""
        return anchors.read_context(self.service, proj)

    def _view(self, proj: str, thread: dict, root: Path | None = None,
              context: dict | None = None,
              resolve_anchors: bool = True) -> dict:
        """A copy annotated for readers: each attachment carries ``available``
        against the caller's branch, and the anchor carries where it points
        *now*.

        A missing file is reported, never raised: ``exports/`` is
        branch-scoped, so a render made on another branch legitimately is not
        here, and a thread must stay readable regardless.

        ``resolution`` is computed here and **only here** — it belongs to the
        view, never to storage. The stored anchor is evidence of what the
        author pointed at, and evidence that rewrites itself is not evidence;
        ``core/anchors.py`` owns the four states and the rule that an
        ambiguous match is an orphan, not a guess.
        """
        view = copy.deepcopy(thread)
        if context is None and root is None:
            context = self._context(proj)
        root = (context or {}).get("root") if root is None else root
        root = self.service.store.path_of(proj) if root is None else root
        for comment in view.get("comments") or []:
            comment["attachments"] = [
                {"path": path, "available": (root / path).is_file()}
                for path in comment.get("attachments") or []
                if isinstance(path, str)
            ]
        if resolve_anchors:
            view["resolution"] = anchors.resolve(
                self.service, proj, view.get("anchor"), context)
        return view

    # --------------------------------------------------------------- anchors

    def _anchor(self, proj: str, anchor: object) -> dict:
        """Validate and normalize an anchor for storage (FR1).

        A bad anchor is a ``validation_error``, never a stored orphan: FR3's
        ``orphaned`` describes a target that went away *after* the fact, and
        minting one at creation would make the honest state meaningless.
        """
        if not isinstance(anchor, dict):
            raise ValidationError(
                "anchor must be an object carrying a 'kind'",
                {"allowed": list(ANCHOR_KINDS)},
            )
        kind = anchor.get("kind")
        if kind not in ANCHOR_KINDS:
            raise ValidationError(
                f"unknown anchor kind {kind!r}",
                {"kind": kind, "allowed": list(ANCHOR_KINDS)},
            )
        allowed = _ANCHOR_FIELDS[kind]
        evidence = _ANCHOR_EVIDENCE.get(kind, ())
        fields: dict[str, object] = {}
        for key, value in anchor.items():
            key = _ANCHOR_ALIASES.get(key, key)
            if key == "kind" or key in _PROVENANCE or key in evidence:
                continue  # provenance and evidence are derived here, never taken
            if key not in allowed:
                raise ValidationError(
                    f"anchor kind {kind!r} does not take {key!r}",
                    {"kind": kind, "allowed": list(allowed)},
                )
            fields[key] = value
        missing = [key for key in allowed if key not in fields]
        if missing:
            raise ValidationError(
                f"anchor kind {kind!r} requires {', '.join(missing)}",
                {"kind": kind, "required": list(allowed), "missing": missing},
            )
        validated = _VALIDATORS[kind](self, proj, fields)
        return {"kind": kind, **validated, **self._provenance(proj)}

    def _provenance(self, proj: str) -> dict:
        """The branch the anchor was authored on and that branch's head.

        Both are context, not identity: threads are branch-free storage, and
        slice 2 uses ``head`` to read the script as it was when the author
        pointed at it. Empty strings without git or without the versioning
        pack — the comments surface works either way.
        """
        branch, head = "", ""
        branches = getattr(self.service, "branches", None)
        history = self.service.history
        if branches is not None:
            branch = branches.current(proj) or ""
        if history.available():
            canonical = self.service.store.canonical_path_of(proj)
            resolved = (history.resolve_branch(canonical, branch) if branch
                        else history.head(self.service.store.path_of(proj)))
            head = resolved or ""
        return {"branch": branch, "head": head}

    def _validate_part(self, proj: str, fields: dict) -> dict:
        return {"part": self._part(proj, fields["part"])}

    def _validate_param(self, proj: str, fields: dict) -> dict:
        part = self._part(proj, fields["part"])
        param = _text(fields["param"], "anchor.param")
        record = self.service.store.get_part(proj, part)
        if record.kind != "script":
            raise ValidationError(
                f"part {part!r} is a {record.kind} part and has no parameters",
                {"part": part, "kind": record.kind},
            )
        # The PARAMS spec, not ``get_part`` — which calls ``_ensure_built``
        # and would turn opening a comment into a 300 s build. ``inspect``
        # only imports the script, and its result is content-hash cached.
        spec = self.service._params_spec(
            self.service.store.read_script(proj, part)
        )
        if spec is None:
            raise ValidationError(
                f"cannot anchor to a parameter: part {part!r}'s script does "
                "not currently load — fix the script first",
                {"part": part},
            )
        if param not in spec:
            raise ValidationError(
                f"unknown parameter {param!r} on part {part!r}",
                {"part": part, "param": param, "params": sorted(spec)},
            )
        return {"part": part, "param": param}

    def _validate_instance(self, proj: str, fields: dict) -> dict:
        instance = _text(fields["instance"], "anchor.instance")
        known = [i["id"] for i in
                 self.service.store.manifest(proj)["assembly"]["instances"]]
        if instance not in known:
            raise ValidationError(
                f"unknown assembly instance {instance!r}",
                {"instance": instance, "instances": known},
            )
        return {"instance": instance}

    def _validate_face(self, proj: str, fields: dict) -> dict:
        """Against ``max(sidecar) + 1`` — never ``metrics.n_faces``, which
        build123d deduplicates (PRD-008 R2). Captures the signature the
        matcher re-identifies the face by."""
        return anchors.validate_face(self.service, proj, fields)

    def _validate_script_range(self, proj: str, fields: dict) -> dict:
        """Against the current script's line count, capturing the exact
        snippet and its context — the evidence tier 1 resolves with."""
        return anchors.validate_script_range(self.service, proj, fields)

    def _validate_proposal_hunk(self, proj: str, fields: dict) -> dict:
        """Against the *persisted* ``packet.json`` — never
        ``service.packets.packet``, which rebuilds geometry and can move the
        proposal's state. Captures the hunk's header and the generation it was
        read from."""
        return anchors.validate_proposal_hunk(self.service, proj, fields)

    def _part(self, proj: str, value: object) -> str:
        part = _text(value, "anchor.part")
        known = self.service.store.part_ids(proj)
        if part not in known:
            raise ValidationError(f"unknown part {part!r}",
                                  {"part": part, "parts": known})
        return part

    # ----------------------------------------------------------- attachments

    def _attachments(self, proj: str, values: object) -> list[str]:
        """FR8/AC9: every attachment resolves inside the project's
        ``exports/`` tree, or the whole call is a ``validation_error``.

        Stored as a project-relative POSIX path (``exports/renders/x.png``),
        whether the caller passed that or the absolute path ``render_view``
        returns.
        """
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValidationError("attachments must be a list of paths")
        if len(values) > MAX_ATTACHMENTS:
            raise ValidationError(
                f"at most {MAX_ATTACHMENTS} attachments per comment",
                {"max": MAX_ATTACHMENTS, "given": len(values)},
            )
        exports = self.service.store.exports_dir(proj).resolve()
        return [self._attachment(exports, value) for value in values]

    @staticmethod
    def _attachment(exports: Path, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError("each attachment must be a path string")
        raw = value.strip()
        path = Path(raw)
        if path.is_absolute():
            candidate = path
        else:
            parts = path.parts
            if not parts or parts[0] != _EXPORTS:
                raise ValidationError(
                    f"attachment {raw!r} must be under exports/",
                    {"path": raw, "root": _EXPORTS},
                )
            candidate = exports.joinpath(*parts[1:])
        # Resolve BOTH sides before comparing: macOS hands back /private/var
        # for /var, and a symlink inside exports/ pointing out of the tree is
        # exactly the path disclosure FR8 exists to refuse.
        resolved = candidate.resolve()
        if not resolved.is_relative_to(exports):
            raise ValidationError(
                f"attachment {raw!r} resolves outside exports/",
                {"path": raw, "root": _EXPORTS},
            )
        if not resolved.is_file():
            raise ValidationError(
                f"attachment {raw!r} does not exist",
                {"path": raw},
            )
        return f"{_EXPORTS}/{resolved.relative_to(exports).as_posix()}"


# One entry per kind in ``ANCHOR_KINDS``, so the vocabulary is a single list
# and every kind refuses in the same shape. Each validator raises
# ``ValidationError`` and nothing else: a bad anchor is a refusal a caller can
# read, never an exception type the REST layer has to guess at.
_VALIDATORS = {
    "part": CommentManager._validate_part,
    "face": CommentManager._validate_face,
    "param": CommentManager._validate_param,
    "script_range": CommentManager._validate_script_range,
    "instance": CommentManager._validate_instance,
    "proposal_hunk": CommentManager._validate_proposal_hunk,
}


def _comment(cid: str, actor: str, ts: str, body: str,
             attachments: list[str], mentions: list[str] | None = None) -> dict:
    return {
        "id": cid,
        "author": actor,
        "author_kind": actor_kind(actor),
        "ts": ts,
        "body": body,
        "attachments": attachments,
        # The PLAUSIBLE identities the body names — the ones a notification
        # was delivered to (minus the author's own). Implausible handles stay
        # in the body and appear nowhere else.
        "mentions": list(mentions or []),
        "edited": None,
        "deleted": False,
    }


def _read_ids(value: object, mine: set[int]) -> list[int]:
    """Whitelist the ``ids`` of a read mark: integers, and this identity's own.

    Another identity's seq is a ``validation_error`` rather than a silent
    no-op: marking somebody else's notification read is not a thing that can
    succeed, so it must not look like it did.
    """
    if not isinstance(value, list):
        raise ValidationError("ids must be a list of notification seq numbers",
                              {"ids": value})
    wanted: list[int] = []
    for seq in value:
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise ValidationError(
                "each notification id must be an integer seq number",
                {"id": seq})
        if seq not in mine:
            raise ValidationError(
                f"notification {seq} is not addressed to this client",
                {"id": seq, "ids": sorted(mine)})
        if seq not in wanted:
            wanted.append(seq)
    return wanted


def _comment_of(thread: dict, tid: str, cid: object) -> dict:
    for comment in thread.get("comments") or []:
        if comment.get("id") == cid:
            return comment
    raise NotFoundError(f"comment {cid!r} not found in thread {tid!r}",
                        {"thread": tid, "comment": cid})


def _check_author(comment: dict, actor: str, tid: str) -> None:
    """Editing and deleting are the author's own. An honesty check, not
    authorization: the identity is a self-asserted header until PRD-005, and
    nothing here may be described as enforcing who someone is."""
    author = comment.get("author")
    if author != actor:
        raise ValidationError(
            f"comment {comment.get('id')} of thread {tid} belongs to "
            f"{author}; only its author may edit or delete it",
            {"thread": tid, "comment": comment.get("id"), "author": author,
             "actor": actor},
        )


def _next_comment_id(thread: dict) -> str:
    """Per-thread sequential ids. Derived from the maximum rather than the
    length, so a thread whose document was hand-edited cannot mint a duplicate
    id — replies are addressed by id in the audit log."""
    highest = 0
    for comment in thread.get("comments") or []:
        try:
            highest = max(highest, int(comment.get("id")))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def _body(value: object) -> str:
    """The body is stored verbatim. Its markdown subset (text, code, links)
    is NOT parsed or sanitized here: it is rendered as text by the client, so
    there is no server-side interpretation to get wrong."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("body must be a non-empty string")
    size = len(value.encode("utf-8"))
    if size > MAX_BODY_BYTES:
        raise ValidationError(
            f"body exceeds {MAX_BODY_BYTES} bytes",
            {"max_bytes": MAX_BODY_BYTES, "bytes": size},
        )
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{what} must be a non-empty string")
    return value
