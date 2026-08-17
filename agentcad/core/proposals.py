"""Change proposals: the durable object, its lifecycle, audit and gates.

A proposal is a *CAD pull request*: a branch pair (``source`` -> ``target``),
an intent, an attributed lifecycle and a gate in front of PRD-001's merge. This
module is the whole of that workflow and nothing else — it writes files, reads
branch heads, evaluates gates, and never builds or renders. The merge itself is
``MergeOrchestrator``'s, called unchanged once the gates are green
(:meth:`ProposalManager.merge`); the review packet is slice 4.

**Where the state lives.** ``<project>/.history/agentcad/proposals/``, beside
PRD-001's ``config.json``/``checkouts.json``/``tags.json``/``merge.json``, and
therefore inside GIT_DIR: a proposal is *about* a branch pair, so it must be
visible from every branch and belong to none, and ``project_restore`` (which is
``git checkout <commit> -- .`` in a working tree) structurally cannot rewind it
(FR3). Paths always come from ``store.canonical_path_of`` — never ``path_of``,
which follows the caller's branch.

```
.history/agentcad/proposals/
  index.json     {"next_id": 4, "proposals": [summary, …]}   (a CACHE)
  next_id        4        (the id high-water mark — NOT a cache)
  policy.json    {"approvals_required": 1, "self_approve": false}  (optional)
  3/proposal.json · audit.jsonl · packet.json · renders/ · diff/
```

**Durability rules.** ``proposal.json``/``index.json`` are written with
``ProjectStore._atomic_write``; ``audit.jsonl`` is deliberately *appended* and
never rewritten (FR14 makes it append-only — a read-modify-replace cycle would
both lose that property and risk truncating the log on a crash). ``index.json``
is rebuilt from the per-proposal directories whenever it is missing or
unparseable, so it is never the source of truth; ids are decimal strings
allocated from ``next_id``, which only ever increments — a directory deleted by
hand does not hand its id to the next proposal. The high-water mark therefore
lives in its own one-line ``next_id`` file, not only in the rebuildable index:
rebuilding from the directories alone would forget the id of a proposal that
was deleted, and hand it out again.

**Attribution.** The actor is ``locks.current_client_id()`` — the identity turn
locks and branch checkouts already key on — and :func:`actor_kind` derives
human vs agent from it. That is bookkeeping, not authentication (the header is
unvalidated until PRD-005).

**Git.** Branch names resolve through ``history.resolve_branch``, never
``resolve_ref``: a tag named like a branch must not answer for it (PRD-001 X1).
``service.branches`` is read inside methods, never in ``__init__`` — the tool
pack that installs this manager is imported *before* ``tools_versioning``.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import locks
from .model import ConflictError, NotFoundError, ValidationError
from .project import ProjectStore

STATES = ("draft", "open", "approved", "changes_requested", "merged", "closed")
TERMINAL = ("merged", "closed")
ACTIVE = ("draft", "open", "approved", "changes_requested")
VERDICTS = ("approve", "request_changes", "comment")
DEFAULT_POLICY = {"approvals_required": 1, "self_approve": False}

# The design spec's transition table. Anything not in it is a validation_error
# naming the current state and the allowed set.
_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("open", "closed"),
    "open": ("approved", "changes_requested", "closed", "merged"),
    "approved": ("changes_requested", "open", "closed", "merged"),
    "changes_requested": ("open", "approved", "closed", "merged"),
    "closed": ("open",),
    "merged": (),
}

# The states ``proposal_update`` may drive. Approving is a review, merging is
# ``proposal_merge``: neither may be faked by writing a state.
_UPDATABLE = ("open", "closed")

_ID_RE = re.compile(r"^[1-9][0-9]{0,17}$")
_ASSET_KINDS = ("renders", "diff")

# Only these verdicts move the approvals count. A 'comment' is a note, not a
# retraction: taking the latest verdict per actor *including* comments let one
# nit silently un-approve an approved proposal (and left the gate lying about
# it) — the verdict an actor has to change is changed by voting again.
_COUNTED_VERDICTS = ("approve", "request_changes")

# How far back the reconciler looks for the commit a staged merge landed as.
# A conflicted merge is finished by resolve_merge within one sitting; anything
# older than this many commits on the target is not this proposal's merge.
_RECONCILE_SCAN = 200

# ``MergeOrchestrator._commit_message``'s verdict line — the only surviving
# record of the validation pass a merge completed by ``resolve_merge`` ran.
_VALIDATION_RE = re.compile(r"^Validation:\s*(.+)$", re.M)


def _now() -> str:
    """UTC, ISO-8601, *zone-aware*: the trailing ``Z`` is what makes a stamp
    unambiguous to a reader. Without it ``Date.parse`` (and
    ``datetime.fromisoformat``) read the stamp as local time, so an audit entry
    written a second ago displays as hours old. Second resolution, no
    microseconds — an audit line is a human-readable record."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def actor_kind(identity: str) -> str:
    """``"human"`` iff the action came from a person; everything else is an
    agent.

    The chat dock is a human ASKING an agent — the action is the agent's, and
    the audit trail must say so. This is a deliberate judgement call, not a
    heuristic to extend: the browser is the only *unauthenticated* surface a
    human drives directly.

    PRD-005a is the change this docstring used to promise ("PRD-005 replaces it
    with the authenticated principal's class, with no schema change"). In
    hosted mode the identity is a composed principal — ``user:nikita`` or
    ``user:nikita/browser:7f3a1b2c`` for a person, ``agent:ci`` for a bearer
    token — and neither starts with ``browser:``. Without the two prefix tests
    below, **every signed-in human would classify as an agent**, and that is
    not cosmetic: :meth:`ClaimRegistry.acquire` returns ``None`` for a
    non-human holder, so no hosted person could hold a per-part claim, and
    ``_blocking`` never blocks an agent, so nobody would be protected from
    anybody. PRD-008's whole concurrency protection would have switched off
    silently on the day hosting turned on, with no error anywhere.

    The ``browser``/else behaviour below is byte-identical, which is what
    keeps local mode unchanged; the four consumers (``comments``,
    ``presence``, ``locks._kind`` and ``proposals`` itself) import this
    function rather than re-implement it, so this is the only place to edit.
    """
    identity = identity or ""
    if identity.startswith("user:"):
        return "human"
    if identity.startswith("agent:"):
        return "agent"
    return "human" if identity == "browser" or identity.startswith("browser:") \
        else "agent"


class ProposalStore:
    """Files only: no policy decisions, no git, no events.

    One ``threading.RLock`` (the ``MergeOrchestrator`` precedent) serializes
    id allocation, index refreshes and audit appends within the process.
    """

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._lock = threading.RLock()

    # ------------------------------------------------------------ locations

    def dir_of(self, proj: str) -> Path:
        return self.store.canonical_path_of(proj) / ".history" / "agentcad" \
            / "proposals"

    def _proposal_dir(self, proj: str, pid: str) -> Path:
        return self.dir_of(proj) / self._valid_id(pid)

    def packet_path(self, proj: str, pid: str) -> Path:
        return self._proposal_dir(proj, pid) / "packet.json"

    def asset_dir(self, proj: str, pid: str, kind: str) -> Path:
        """``renders/`` or ``diff/`` under the proposal, created on demand."""
        if kind not in _ASSET_KINDS:
            raise ValidationError(
                f"unknown asset kind {kind!r}", {"allowed": list(_ASSET_KINDS)}
            )
        path = self._proposal_dir(proj, pid) / kind
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _valid_id(pid: object) -> str:
        # Ids reach here from a REST path segment as well as from a tool
        # argument, so they are whitelisted before they touch the filesystem.
        if not isinstance(pid, str) or not _ID_RE.match(pid):
            raise NotFoundError(f"proposal {pid!r} not found")
        return pid

    # ------------------------------------------------------------ proposals

    def load(self, proj: str, pid: str) -> dict:
        path = self._proposal_dir(proj, pid) / "proposal.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NotFoundError(f"proposal {pid!r} not found") from exc
        if not isinstance(data, dict):
            raise NotFoundError(f"proposal {pid!r} not found")
        return data

    def save(self, proj: str, proposal: dict) -> None:
        pid = self._valid_id(proposal.get("id"))
        ProjectStore._atomic_write(
            self._proposal_dir(proj, pid) / "proposal.json",
            json.dumps(proposal, indent=2).encode(),
        )
        with self._lock:
            index, _ok = self._read_index(proj)
            summaries = [s for s in index["proposals"] if s.get("id") != pid]
            summaries.append(_summary(proposal))
            index["proposals"] = _sorted(summaries)
            index["next_id"] = max(index["next_id"], int(pid) + 1)
            self._write_index(proj, index)

    def list(self, proj: str) -> list[dict]:
        """Every proposal, oldest id first, read from the directories.

        The directories are the source of truth; ``index.json`` is refreshed
        here when it disagrees, which is what makes it a rebuildable cache.
        """
        base = self.dir_of(proj)
        proposals = []
        if base.is_dir():
            for entry in sorted(base.iterdir(), key=_id_key):
                if not entry.is_dir() or not _ID_RE.match(entry.name):
                    continue
                try:
                    proposals.append(self.load(proj, entry.name))
                except NotFoundError:
                    continue  # a half-written or hand-mangled directory
        with self._lock:
            index, ok = self._read_index(proj)
            desired = {
                "next_id": max(
                    index["next_id"],
                    max((int(p["id"]) for p in proposals), default=0) + 1,
                    # The high-water mark outlives both the index and the
                    # directories: it is the only thing that remembers an id
                    # whose proposal was deleted by hand.
                    self._high_water(proj),
                ),
                "proposals": _sorted([_summary(p) for p in proposals]),
            }
            if not ok or index != desired:
                self._write_index(proj, desired)
        return proposals

    def allocate_id(self, proj: str) -> str:
        """The next never-used id. Monotonic even when a proposal directory
        was removed by hand AND the index was then lost: the id counter is
        persisted separately, and written BEFORE the directory it names
        exists — a crash in between costs an id, it never reuses one."""
        with self._lock:
            self.list(proj)  # refreshes next_id past any hand-made directory
            index, _ok = self._read_index(proj)
            pid = str(max(1, int(index["next_id"]), self._high_water(proj)))
            self._write_high_water(proj, int(pid) + 1)
            index["next_id"] = int(pid) + 1
            self._write_index(proj, index)
            return pid

    # ---------------------------------------------------------------- index

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
            return {"next_id": 1, "proposals": []}, False
        if not isinstance(data, dict):
            return {"next_id": 1, "proposals": []}, False
        next_id = data.get("next_id")
        rows = data.get("proposals")
        return (
            {
                "next_id": next_id if isinstance(next_id, int) and next_id >= 1
                else 1,
                "proposals": [r for r in rows if isinstance(r, dict)]
                if isinstance(rows, list) else [],
            },
            isinstance(next_id, int) and isinstance(rows, list),
        )

    def _write_index(self, proj: str, index: dict) -> None:
        ProjectStore._atomic_write(
            self.dir_of(proj) / "index.json",
            json.dumps(index, indent=2).encode(),
        )

    # ---------------------------------------------------------------- audit

    def append_audit(self, proj: str, pid: str, entry: dict) -> dict:
        """Append one line to ``audit.jsonl`` and return the stored entry.

        Appended, never rewritten: FR14 makes the log append-only, and there
        is deliberately no method here that edits or removes an entry.
        """
        path = self._proposal_dir(proj, pid) / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        actor = entry.get("actor") or locks.current_client_id()
        with self._lock:
            record = {
                "seq": self._line_count(path) + 1,
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

    def audit(self, proj: str, pid: str) -> list[dict]:
        """Every entry, in order. A corrupt line (a torn write) is skipped
        rather than raised — the rest of the log is still evidence."""
        path = self._proposal_dir(proj, pid) / "audit.jsonl"
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

    @staticmethod
    def _line_count(path: Path) -> int:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return 0
        return len([line for line in text.splitlines() if line.strip()])

    # --------------------------------------------------------------- policy

    def policy(self, proj: str) -> dict:
        """The merge policy, read at call time (never cached): the file is the
        seam, and there is deliberately no policy tool or route in v1."""
        policy = dict(DEFAULT_POLICY)
        try:
            data = json.loads(
                (self.dir_of(proj) / "policy.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return policy
        if not isinstance(data, dict):
            return policy
        required = data.get("approvals_required")
        if isinstance(required, int) and not isinstance(required, bool) \
                and required >= 0:
            policy["approvals_required"] = required
        if isinstance(data.get("self_approve"), bool):
            policy["self_approve"] = data["self_approve"]
        return policy


def _summary(proposal: dict) -> dict:
    """The list-view row: everything the badge, the list and the detail header
    need, without the reviews or the packet."""
    merge = proposal.get("merge") or {}
    return {
        "id": proposal.get("id"),
        "project": proposal.get("project"),
        "source": proposal.get("source"),
        "target": proposal.get("target"),
        "title": proposal.get("title", ""),
        "state": proposal.get("state"),
        "author": proposal.get("author"),
        "author_kind": proposal.get("author_kind"),
        "created": proposal.get("created"),
        "updated": proposal.get("updated"),
        "reviews": len(proposal.get("reviews") or []),
        "merge_commit": merge.get("commit"),
    }


def _sorted(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: int(row.get("id") or 0))


def _id_key(path: Path) -> tuple[int, str]:
    return (int(path.name) if path.name.isdigit() else 1 << 62, path.name)


class ProposalManager:
    """The lifecycle: creation rules, transitions, reviews, policy and gates.

    Owns one ``threading.RLock`` around every read-modify-write of a proposal
    — the packet builder's included (:meth:`record_packet`), so
    ``proposal.json`` and ``packet.json`` have exactly ONE serialization point
    and a slow packet build can never resurrect a state the merge has moved
    past. ``service.branches`` is resolved per call (see the module docstring).
    """

    def __init__(self, service) -> None:
        self.service = service
        self.store = ProposalStore(service.store)
        self._lock = threading.RLock()

    # ----------------------------------------------------------- public api

    def create(self, proj: str, source: str, target: str | None = None,
               title: str = "", description: str = "",
               draft: bool = False) -> dict:
        branches = self._branches()
        canonical = branches._ensure_history(proj)
        source = _text(source, "source")
        target = _text(target, "target") if target else \
            branches.default_branch(proj)
        if source == target:
            raise ValidationError(
                f"a proposal cannot merge branch {source!r} into itself",
                {"source": source, "target": target},
            )
        state = "draft" if draft else "open"
        actor = locks.current_client_id()
        with self._lock:
            # Under the lock, with the branch-delete guard: a branch that is
            # here when the proposal is written cannot have been deleted
            # between the check and the write.
            for role, name in (("source", source), ("target", target)):
                if self.service.history.resolve_branch(canonical, name) is None:
                    raise NotFoundError(f"branch {name!r} not found",
                                        {"role": role, "branch": name})
            for existing in self.store.list(proj):
                if (existing.get("source"), existing.get("target")) \
                        == (source, target) \
                        and existing.get("state") in ACTIVE:
                    raise ConflictError(
                        f"an open proposal already exists for {source!r} -> "
                        f"{target!r}",
                        {"existing_id": existing.get("id"), "source": source,
                         "target": target},
                    )
            pid = self.store.allocate_id(proj)
            stamp = _now()
            proposal = {
                "id": pid,
                "project": proj,
                "source": source,
                "target": target,
                "title": _text(title, "title", allow_empty=True),
                "description": _text(description, "description",
                                     allow_empty=True),
                "author": actor,
                "author_kind": actor_kind(actor),
                "state": state,
                "created": stamp,
                "updated": stamp,
                "reviews": [],
                "merge": None,
                "packet": None,
            }
            self.store.save(proj, proposal)
            self.store.append_audit(proj, pid, {
                "action": "created",
                "details": {"source": source, "target": target,
                            "state": state},
            })
        self._publish(proj, proposal, "created")
        return {**self._result(proj, proposal), "packet": None}

    def list(self, proj: str, state: str | None = None) -> dict:
        self.ensure_branch_guard()
        if state is not None and state not in STATES:
            raise ValidationError(f"unknown proposal state {state!r}",
                                  {"allowed": list(STATES)})
        proposals = self.store.list(proj)
        for index, proposal in enumerate(proposals):
            if proposal.get("staged_merge"):
                proposals[index] = self.reconcile(proj, proposal["id"])
        counts = {name: 0 for name in STATES}
        for proposal in proposals:
            if proposal.get("state") in counts:
                counts[proposal["state"]] += 1
        rows = [_summary(p) for p in proposals
                if state is None or p.get("state") == state]
        return {"proposals": rows, "counts": counts}

    def get(self, proj: str, pid: str) -> dict:
        self.ensure_branch_guard()
        proposal = self.reconcile(proj, pid)
        return {
            **self._result(proj, proposal),
            "audit": self.store.audit(proj, proposal["id"]),
            "packet": self._packet_summary(proj, proposal),
        }

    def update(self, proj: str, pid: str, title: str | None = None,
               description: str | None = None,
               state: str | None = None) -> dict:
        self.ensure_branch_guard()
        with self._lock:
            proposal = self.store.load(proj, pid)
            # Validate the transition BEFORE touching anything, so a refused
            # state change never leaves a half-applied edit or a stray audit
            # entry behind.
            if state is not None:
                self._check_update_state(proposal, state)
            fields = []
            if title is not None:
                proposal["title"] = _text(title, "title", allow_empty=True)
                fields.append("title")
            if description is not None:
                proposal["description"] = _text(description, "description",
                                                allow_empty=True)
                fields.append("description")
            if fields:
                proposal["updated"] = _now()
                self.store.append_audit(proj, proposal["id"], {
                    "action": "updated", "details": {"fields": fields},
                })
            if state == "closed":
                # Before the transition: a proposal that stops merging must
                # not leave a merge staged in its name.
                self._release_staged_merge(proj, proposal)
            if state is not None:
                self.transition(proposal, state,
                                action=_update_action(proposal["state"], state))
            if fields or state is not None:
                self.store.save(proj, proposal)
        if fields or state is not None:
            self._publish(proj, proposal, "updated")
        return self._result(proj, proposal)

    def review(self, proj: str, pid: str, verdict: str,
               summary: str | None = None) -> dict:
        self.ensure_branch_guard()
        if verdict not in VERDICTS:
            raise ValidationError(f"unknown verdict {verdict!r}",
                                  {"allowed": list(VERDICTS)})
        with self._lock:
            proposal = self.store.load(proj, pid)
            to = {"approve": "approved",
                  "request_changes": "changes_requested"}.get(
                      verdict, proposal.get("state"))
            self._check_transition(proposal, to)
            actor = locks.current_client_id()
            head = self._source_head(proj, proposal)
            proposal.setdefault("reviews", []).append({
                "actor": actor,
                "actor_kind": actor_kind(actor),
                "verdict": verdict,
                "summary": _text(summary, "summary", allow_empty=True)
                if summary is not None else None,
                "ts": _now(),
                "source_head": head,
            })
            self.transition(proposal, to, action="reviewed",
                            details={"verdict": verdict, "source_head": head})
            self.store.save(proj, proposal)
        self._publish(proj, proposal, "review")
        return self._result(proj, proposal)

    def merge(self, proj: str, pid: str, allow_invalid: bool = False) -> dict:
        """The gate, then PRD-001's merge — which is not re-implemented,
        re-checked or wrapped.

        Gates are evaluated and a red one refuses **before** anything is
        merged, so a blocked proposal never leaves a staged merge behind.
        Then ``MergeOrchestrator.merge`` runs and its three outcomes are
        forwarded: success is recorded on the proposal (state ``merged``,
        packet frozen, audit ``merged`` plus ``override`` when the kernel
        gate was overridden); a returned ``merge_conflict`` is passed through
        **verbatim** with only ``details.proposal`` added, leaving the state
        alone; a raised blocked-validation ``validation_error`` propagates with
        the same one addition.

        The staged merge left behind by either of the last two is **held** by
        this proposal (``held_by``): ``resolve_merge`` records resolutions
        against it but cannot land it, because landing it there would skip
        every gate above. The second ``proposal_merge`` re-evaluates them
        against the branches as they are then and finishes it
        (:meth:`_orchestrate`); CLOSING the proposal aborts it instead
        (:meth:`_release_staged_merge`), because a hold must not outlive the
        gates that would have released it. A pair whose staged merge is held
        by anyone else is refused before PRD-001 is asked anything.

        ``allow_invalid`` reaches PRD-001's kernel gate and nothing else — it
        is the caller's statement about the kernel's verdict on geometry, and
        letting it also waive the approvals policy would make one field mean
        two unrelated things (FR11).

        **Locking.** PRD-001 holds the TARGET branch's turn across validation
        and finalization (``MergeOrchestrator._holding_target``). This layer
        holds the SOURCE branch's turn across gate evaluation and the merge
        call, because a gate result is a statement about a specific source
        head: without it another client could commit between "specs pass" and
        the orchestrator resolving the ref, and the merge would land content no
        gate ever saw. The order is fixed — proposal takes the source, the
        orchestrator takes the target — and cannot deadlock in any case:
        ``TurnLock.acquire`` never blocks, it raises, so two proposals merging
        in opposite directions produce a clean ``conflict_error``.
        """
        self._merges()  # refuse early with no git, and install the guard
        with self._lock:
            proposal = self.store.load(proj, pid)
            landed = self._reconcile(proj, proposal)
            if landed is not None:
                # The merge this proposal staged was completed by
                # ``resolve_merge`` while we were away: it is already merged,
                # and the documented recovery ("resolve it, then call
                # proposal_merge again") answers with the merge that landed
                # rather than re-merging an ancestor into its own target.
                gates = self.gates(proj, proposal)
                return {**landed, "proposal": self._view(proj, proposal),
                        "gates": gates}
            state = proposal.get("state")
            if state == "draft":
                raise ConflictError(
                    f"proposal {pid} is a draft and cannot be merged; open it "
                    "first",
                    {"id": pid, "state": state},
                )
            if state in TERMINAL:
                raise ConflictError(
                    f"proposal {pid} is already {state}",
                    {"id": pid, "state": state},
                )
            source, target = proposal.get("source"), proposal.get("target")
            with self._holding_source(proj, source):
                gates = self.gates(proj, proposal)
                failing = next((g for g in gates if g.get("state") == "fail"),
                               None)
                if failing is not None:
                    raise ConflictError(
                        f"proposal {pid} cannot merge: {failing.get('summary')}",
                        {"id": pid, "failing": failing.get("name"),
                         "gates": gates},
                    )
                # The head the gates were evaluated against, read INSIDE the
                # hold: no in-process writer can move it before the
                # orchestrator resolves the same ref (FR11 TOCTOU).
                gates_head = self._resolve(proj, source)
                result, allow_invalid = self._orchestrate(
                    proj, proposal, allow_invalid)
            attempt = {"source": source, "target": target,
                       "allow_invalid": allow_invalid}
            error = result.get("error") if isinstance(result, dict) else None
            if isinstance(error, dict):
                details = error.setdefault("details", {})
                # Remember WHICH merge this proposal staged, and with which
                # allow_invalid. resolve_merge finishes that merge without ever
                # hearing about the proposal, so this record is the only way
                # the proposal can later recognise its own merge as the one
                # that landed — and the only surviving statement of the
                # override the landed merge actually ran under (FR10).
                staged = {
                    "merge_id": details.get("merge_id"),
                    "source": source,
                    "target": target,
                    "source_head": self._resolve(proj, source),
                    "target_head": self._resolve(proj, target),
                    "allow_invalid": bool(allow_invalid),
                    "ts": _now(),
                }
                proposal["staged_merge"] = staged
                self.store.save(proj, proposal)
                self.store.append_audit(proj, pid, {
                    "action": "merge_attempted",
                    "details": {**attempt, "outcome": "conflict",
                                "merge_id": staged["merge_id"]},
                })
                # Verbatim, so the UI's existing conflict modal and
                # resolve_merge keep working unchanged.
                details["proposal"] = pid
                return result
            if result.get("held"):
                # A staged merge for this same pair that ANOTHER holder owns:
                # its gates are the ones that must be re-checked, not ours, and
                # nothing here may finish it. ``_orchestrate`` refuses this
                # before the orchestrator is asked; this is the belt.
                raise _held_elsewhere(pid, result.get("held_by"), source,
                                      target, result.get("merge_id"))

            report = result.get("validation")
            report = report if isinstance(report, dict) else None
            proposal.pop("staged_merge", None)  # this call finished it
            parents = result.get("parents") or (
                [result.get("previous"), result.get("commit")]
                if result.get("fast_forward") else [])
            proposal["merge"] = {
                "commit": result.get("commit"),
                "parents": result.get("parents") or [],
                # The two commits the merge really consumed — a fast-forward
                # has one git parent, but it still moved a target from one
                # commit to another, and the frozen packet is checked against
                # both (``stale_at_merge``).
                "heads": parents if len(parents) == 2 and all(parents) else [],
                "gates_source_head": gates_head,
                "ts": _now(),
                "allow_invalid": bool(allow_invalid),
                "fast_forward": bool(result.get("fast_forward")),
                "validation": report,
            }
            if gates_head and len(parents) == 2 and parents[1] != gates_head:
                # The source turn hold is what PREVENTS this; a landed merge
                # cannot be un-landed, so if it ever happens it is recorded
                # rather than smoothed over.
                self.store.append_audit(proj, pid, {
                    "action": "gate_head_mismatch",
                    "details": {"gates_source_head": gates_head,
                                "merged_source_head": parents[1],
                                "commit": result.get("commit")},
                })
            self.transition(proposal, "merged", action="merged",
                            details={"commit": result.get("commit"),
                                     **attempt})
            if allow_invalid and report is not None and not report.get("ok"):
                # The third place the override is recorded: the audit log, the
                # proposal, and MergeOrchestrator's commit message (FR10).
                self.store.append_audit(proj, pid, {
                    "action": "override",
                    "details": {"gate": "validation",
                                "commit": result.get("commit"),
                                "validation": report},
                })
            self.store.save(proj, proposal)
            self._freeze_packet(proj, proposal)
            gates = self.gates(proj, proposal)
        self._publish(proj, proposal, "merged")
        return {**result, "proposal": self._view(proj, proposal),
                "gates": gates}

    def _orchestrate(self, proj: str, proposal: dict,
                     allow_invalid: bool) -> tuple[dict, bool]:
        """PRD-001's merge for this proposal — or the completion of the staged
        merge this proposal already HOLDS. Returns ``(result, allow_invalid)``.

        A conflicted (or validation-blocked) proposal merge is staged with
        ``held_by``, which stops ``resolve_merge`` from finalizing it: at zero
        outstanding the orchestrator answers ``held: true`` and lands nothing.
        The caller has just re-evaluated the gates against the branches as they
        are NOW, so this call is the one allowed to finish it —
        ``finalize_held`` runs the orchestrator's own validation and
        finalization, and the override the staged merge carried survives unless
        this call asks for a stronger one.
        """
        merges = self._merges()
        pid = proposal["id"]
        source, target = proposal["source"], proposal["target"]
        hold = _hold_key(pid)
        current = merges.status(proj).get("merge") or {}
        same_pair = (current.get("source"), current.get("target")) \
            == (source, target)
        holder = current.get("held_by")
        if same_pair and holder and holder != hold:
            # Someone else's staged merge, for our pair. Passing our own hold
            # into PRD-001 would REWRITE ``held_by`` on the way to being
            # refused — and the next call would then resume that merge and land
            # another proposal's resolutions, which nobody here approved. So
            # this is refused before anything is asked of the orchestrator.
            raise _held_elsewhere(pid, holder, source, target,
                                  current.get("id"))
        resume = (holder == hold and same_pair
                  and not current.get("outstanding"))
        allow_invalid = bool(allow_invalid) or (
            bool((proposal.get("staged_merge") or {}).get("allow_invalid"))
            if resume else False)
        try:
            if resume:
                result = merges.finalize_held(proj, allow_invalid=allow_invalid)
            else:
                result = merges.merge(proj, source, target,
                                      allow_invalid=allow_invalid,
                                      held_by=hold)
        except ValidationError as exc:
            report = (exc.details or {}).get("validation")
            if not isinstance(report, dict):
                raise  # not the kernel gate: PRD-001's error, untouched
            self.store.append_audit(proj, pid, {
                "action": "merge_attempted",
                "details": {"source": source, "target": target,
                            "allow_invalid": allow_invalid,
                            "outcome": "blocked"},
            })
            raise ValidationError(
                exc.message, {**(exc.details or {}), "proposal": pid}
            ) from exc
        return result, allow_invalid

    @contextmanager
    def _holding_source(self, proj: str, source: str | None):
        """Hold the SOURCE branch's turn across gate evaluation and the merge.

        ``MergeOrchestrator._holding_target``'s pattern, one branch over: a
        gate result is about a specific source head, so the head must not move
        between the gate and the merge that consumes it. A caller that already
        holds the turn keeps it; releasing a hold that expired and was taken by
        someone else raises, over a body that has already merged — so that
        release is swallowed (PRD-001's D1 lesson).
        """
        turnlock = getattr(self.service, "turnlock", None)
        branches = getattr(self.service, "branches", None)
        if turnlock is None or branches is None or not source:
            yield
            return
        with branches.pinned(proj, branches.tree_of(proj, source)):
            key = self.service.store.lock_key(proj)
        holder = locks.current_client_id()
        existing = turnlock.get(key)
        turnlock.acquire(key, holder)  # ConflictError when someone else has it
        try:
            yield
        finally:
            if existing is None or existing.get("holder") != holder:
                try:
                    turnlock.release(key, holder)
                except ConflictError:
                    pass  # our hold expired and another client took the turn

    def _release_staged_merge(self, proj: str, proposal: dict) -> None:
        """Abort the staged merge a closing proposal HOLDS (FR11's other end).

        A hold outlives nothing useful: ``resolve_merge`` cannot land the merge
        (that is the hold's whole purpose) and the proposal whose gates would
        release it is closed, so the staged merge is unfinishable — and the
        next proposal for the same pair used to meet it and could inherit the
        resolutions recorded against it, unseen by its own approvers. So it is
        discarded through PRD-001's own public ``abort``, and audited as the
        reconciler audits any staged merge that went away.

        Only a merge THIS proposal holds is touched. The caller holds
        ``_lock`` and saves.
        """
        merges = getattr(self.service, "merges", None)
        if merges is None:
            return
        pid = proposal["id"]
        current = merges.status(proj).get("merge") or {}
        if current.get("held_by") != _hold_key(pid):
            return
        aborted = merges.abort(proj)
        proposal.pop("staged_merge", None)
        self.store.append_audit(proj, pid, {
            "action": "merge_discarded",
            "details": {"merge_id": aborted.get("merge_id") or current.get("id"),
                        "source": current.get("source"),
                        "target": current.get("target"),
                        "reason": "closed"},
        })

    def reconcile(self, proj: str, pid: str) -> dict:
        """Load a proposal, first finalizing it if a merge it staged has since
        landed. Every read path goes through here (see :meth:`_reconcile`)."""
        with self._lock:
            proposal = self.store.load(proj, pid)
            self._reconcile(proj, proposal)
            return proposal

    def record_packet(self, proj: str, pid: str, packet: dict) -> bool:
        """Persist a freshly built packet — the ONE writer of ``packet.json``
        besides the freeze, and serialized against the lifecycle.

        ``PacketBuilder`` measures for seconds outside this lock, so by the
        time it gets here the proposal may have merged: writing then would
        publish post-decision evidence over the frozen packet and hand
        ``proposal.json`` back a stale state. A build that lost that race is
        DISCARDED — nothing is written, ``False`` says so, and the caller
        serves the frozen packet instead.
        """
        with self._lock:
            proposal = self.store.load(proj, pid)
            self._reconcile(proj, proposal)
            if proposal.get("state") in TERMINAL:
                return False
            ProjectStore._atomic_write(
                self.store.packet_path(proj, pid),
                json.dumps(packet, indent=2).encode(),
            )
            self.store.append_audit(proj, pid, {
                "action": "packet_generated",
                "details": {"source_head": packet["source_head"],
                            "target_head": packet["target_head"],
                            "elapsed_ms": packet["elapsed_ms"],
                            "parts": [p["part"] for p in packet["parts"]]},
            })
            proposal["packet"] = {
                "generated": packet["generated"],
                "source_head": packet["source_head"],
                "target_head": packet["target_head"],
                "ok": packet["ok"],
            }
            self.store.save(proj, proposal)
            state = proposal.get("state")
        self.service.bus.publish({
            "type": "proposal_changed", "project": proj, "id": pid,
            "state": state, "reason": "packet",
        })
        return True

    def transition(self, proposal: dict, to: str, *, action: str,
                   details: dict | None = None) -> dict:
        """Move ``proposal`` to ``to`` in memory and record it in the audit
        log. The caller saves — every caller here does so immediately."""
        self._check_transition(proposal, to)
        frm = proposal.get("state")
        proposal["state"] = to
        proposal["updated"] = _now()
        self.store.append_audit(proposal["project"], proposal["id"], {
            "action": action,
            "details": {**(details or {}), "from": frm, "to": to},
        })
        return proposal

    # --------------------------------------------------------------- gates

    def gates(self, proj: str, proposal: dict) -> list[dict]:
        """``[{name, state, summary, details?}]`` with
        ``state ∈ pass|fail|pending|skipped``.

        ``validation`` is deliberately NOT pre-evaluated: it *is* PRD-001's
        merge validation pass, and running it twice would double the kernel
        cost for no new information.
        """
        policy = self.store.policy(proj)
        gates = [
            self._state_gate(proposal),
            self._approvals_gate(proj, proposal, policy),
            self._validation_gate(proposal),
            {"name": "specs", "state": "skipped",
             "summary": "spec evaluation not installed"},
            {"name": "checks", "state": "skipped",
             "summary": "no checks posted"},
        ]
        for provider in list(getattr(self.service, "gate_providers", None) or []):
            name = getattr(provider, "__name__", None) \
                or type(provider).__name__
            try:
                gate = provider(proj, proposal)
            except Exception as exc:  # noqa: BLE001 — an optional pack must
                # never block a merge or crash a read (PRD-003/PRD-004 plug in
                # here from their own register()).
                gate = {"name": name, "state": "pending",
                        "summary": f"{name} errored: {exc}"}
            if not isinstance(gate, dict) or not gate.get("name"):
                continue
            for index, existing in enumerate(gates):
                if existing["name"] == gate["name"]:
                    gates[index] = gate
                    break
            else:
                gates.append(gate)
        return gates

    def _state_gate(self, proposal: dict) -> dict:
        state = proposal.get("state")
        if state == "changes_requested":
            return {"name": "state", "state": "fail",
                    "summary": "changes were requested; the author has not "
                               "re-requested review",
                    "details": {"state": state}}
        return {"name": "state", "state": "pass",
                "summary": f"state is {state!r}", "details": {"state": state}}

    def _approvals_gate(self, proj: str, proposal: dict,
                        policy: dict) -> dict:
        head = self._source_head(proj, proposal)
        latest: dict[str, dict] = {}
        for review in proposal.get("reviews") or []:
            # The latest *counted* verdict per actor. A 'comment' is recorded
            # and audited like any other review but is deliberately invisible
            # here: it changes no state, so it must not silently retract the
            # approval the same actor gave a minute earlier.
            if review.get("actor") and review.get("verdict") in _COUNTED_VERDICTS:
                latest[review["actor"]] = review
        approvals = [r for actor, r in latest.items()
                     if r.get("verdict") == "approve"
                     and (policy["self_approve"]
                          or actor != proposal.get("author"))]
        stale = [r for r in approvals if _is_stale(r, head)]
        required = policy["approvals_required"]
        count = len(approvals)
        note = ""
        if not policy["self_approve"]:
            note = " (self-approval does not count)"
        if stale:
            note += (f" ({len(stale)} made against an older source head — "
                     "stale, but still counted)")
        return {
            "name": "approvals",
            "state": "pass" if count >= required else "fail",
            "summary": f"{required} approval{'' if required == 1 else 's'} "
                       f"required, {count} recorded{note}",
            "details": {"approvals_required": required, "approvals": count,
                        "self_approve": policy["self_approve"],
                        "author": proposal.get("author")},
        }

    @staticmethod
    def _validation_gate(proposal: dict) -> dict:
        report = (proposal.get("merge") or {}).get("validation")
        if not isinstance(report, dict):
            return {"name": "validation", "state": "pending",
                    "summary": "the kernel validation pass runs as part of "
                               "the merge"}
        ok = bool(report.get("ok"))
        return {"name": "validation", "state": "pass" if ok else "fail",
                "summary": "kernel validation passed" if ok
                           else "kernel validation failed",
                "details": {"validation": report}}

    # ------------------------------------------------- the branch-delete guard

    def ensure_branch_guard(self) -> None:
        """Refuse to delete a branch an active proposal names (FR2).

        Wraps the bound ``BranchManager.delete`` — the
        ``install_write_guard`` precedent — so one hook covers the tool, the
        REST route and the UI at once and ``branches.py`` stays untouched.
        Installed lazily and idempotently rather than from the pack's
        ``register()``: ``tools_proposals`` is imported *before*
        ``tools_versioning``, so ``service.branches`` does not exist yet then.
        """
        branches = getattr(self.service, "branches", None)
        if branches is None or getattr(branches.delete, "_proposal_guard", False):
            return

        inner = branches.delete

        def delete(proj: str, name: str) -> dict:
            # The check and the deletion are ONE critical section: creating a
            # proposal takes the same lock, so a proposal can never be opened
            # against a branch between "no proposal names it" and the branch
            # going away.
            with self._lock:
                self._check_branch_free(proj, name)
                return inner(proj, name)

        delete._proposal_guard = True
        branches.delete = delete

    def _check_branch_free(self, proj: str, name: str) -> None:
        if not self.store.dir_of(proj).is_dir():
            return  # no proposals here: byte-identical to PRD-001's behavior
        for proposal in self.store.list(proj):
            if proposal.get("state") not in ACTIVE:
                continue  # a merged/closed proposal holds nothing hostage
            for role in ("source", "target"):
                if proposal.get(role) != name:
                    continue
                raise ConflictError(
                    f"branch {name!r} is the {role} of proposal "
                    f"{proposal.get('id')} ({proposal.get('state')}); close "
                    "or merge it first",
                    {"proposal": proposal.get("id"), "branch": name,
                     "role": role, "state": proposal.get("state")},
                )

    # ------------------------------------------------------------ internals

    def _branches(self):
        branches = getattr(self.service, "branches", None)
        if branches is None:
            raise ValidationError(
                "proposals unavailable: git not found on PATH"
            )
        self.ensure_branch_guard()
        return branches

    def _merges(self):
        merges = getattr(self.service, "merges", None)
        if merges is None:
            raise ValidationError(
                "proposals unavailable: git not found on PATH"
            )
        self.ensure_branch_guard()
        return merges

    # ------------------------------------------- the staged-merge reconciler

    def _reconcile(self, proj: str, proposal: dict) -> dict | None:
        """Finish a proposal whose staged merge landed behind its back.

        ``proposal_merge`` hands a conflict straight to PRD-001's
        ``resolve_merge``, which completes the merge knowing nothing about
        proposals: the commit lands, the branch moves, and the proposal is
        left sitting at ``approved`` with no merge record — while a later
        ``proposal_merge`` "recovers" by merging an ancestor and recording
        ``allow_invalid: false`` over a merge that really ran with the
        override. So the proposal remembers the merge it staged
        (``staged_merge``) and every read path checks, here, whether that
        merge is still staged, was discarded, or has landed.

        Landed is recognised by the commit itself: the finalizer commits with
        exactly ``-p <target_head> -p <source_head>``, so the merge this
        proposal staged is the commit on the target whose parents are the two
        heads it recorded. Its ``allow_invalid`` is the one the STAGED merge
        carried (``MergeOrchestrator.resolve`` reuses it verbatim) and its
        verdict is read back out of the commit message.

        Returns the merge payload when it finalized the proposal in this call,
        else None. The caller holds ``_lock``.
        """
        staged = proposal.get("staged_merge")
        if not isinstance(staged, dict) or proposal.get("state") in TERMINAL:
            return None
        merges = getattr(self.service, "merges", None)
        if merges is None:
            return None
        pid = proposal["id"]
        current = (merges.status(proj).get("merge") or {})
        if current.get("id") and (current["id"] == staged.get("merge_id")
                                  or current.get("held_by") == _hold_key(pid)):
            # Still staged: nothing has landed yet. The hold is the stable
            # identity of "this proposal's merge" — ``resolve_merge`` re-stages
            # under a NEW merge id every time it records a resolution, so the
            # id alone would read a resolved conflict as a discarded merge.
            return None
        commit = self._landed_commit(proj, staged)
        if commit is None:
            # Aborted, or re-staged from moved heads: either way this proposal
            # no longer has a merge in flight, and must not claim a later one.
            proposal.pop("staged_merge", None)
            self.store.save(proj, proposal)
            self.store.append_audit(proj, pid, {
                "action": "merge_discarded",
                "details": {"merge_id": staged.get("merge_id"),
                            "source": staged.get("source"),
                            "target": staged.get("target")},
            })
            return None
        return self._finish_landed(proj, proposal, staged, commit)

    def _landed_commit(self, proj: str, staged: dict) -> str | None:
        """The commit the staged merge landed as, or None."""
        target = staged.get("target")
        heads = (staged.get("target_head"), staged.get("source_head"))
        if not target or not all(heads):
            return None
        canonical = self.service.store.canonical_path_of(proj)
        result = self.service.history._run(
            canonical, "rev-list", "--parents", "-n", str(_RECONCILE_SCAN),
            f"refs/heads/{target}", check=False)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) == 3 and tuple(fields[1:]) == heads:
                return fields[0]
        return None

    def _finish_landed(self, proj: str, proposal: dict, staged: dict,
                       commit: str) -> dict:
        pid = proposal["id"]
        allow_invalid = bool(staged.get("allow_invalid"))
        message = self.service.history._run(
            self.service.store.canonical_path_of(proj), "log", "-1",
            "--pretty=%B", commit, check=False).stdout
        report = _recovered_validation(message)
        parents = [staged["target_head"], staged["source_head"]]
        proposal.pop("staged_merge", None)
        proposal["merge"] = {
            "commit": commit,
            "parents": parents,
            "heads": parents,
            "ts": _now(),
            "allow_invalid": allow_invalid,
            "fast_forward": False,
            "validation": report,
            # This record was reconstructed from the commit, not written by
            # the call that merged: say so rather than pass it off as first-
            # hand evidence.
            "reconciled": True,
        }
        self.transition(proposal, "merged", action="merged",
                        details={"commit": commit,
                                 "source": staged.get("source"),
                                 "target": staged.get("target"),
                                 "allow_invalid": allow_invalid,
                                 "via": "resolve_merge",
                                 "merge_id": staged.get("merge_id")})
        if allow_invalid and report is not None and not report.get("ok"):
            self.store.append_audit(proj, pid, {
                "action": "override",
                "details": {"gate": "validation", "commit": commit,
                            "validation": report, "via": "resolve_merge"},
            })
        self.store.save(proj, proposal)
        self._freeze_packet(proj, proposal)
        self._publish(proj, proposal, "merged")
        return {
            "merged": True,
            "fast_forward": False,
            "already_landed": True,
            "source": staged.get("source"),
            "target": staged.get("target"),
            "commit": commit,
            "parents": parents,
            "validation": report,
        }

    def _freeze_packet(self, proj: str, proposal: dict) -> None:
        """FR12: the evidence a decision was made on is never regenerated.

        The ABSENCE of a packet is frozen too. A proposal merged before anyone
        opened its packet has nothing to show — and a packet built afterwards
        would measure the post-merge branches and pass that off as the change
        that was reviewed. So the absence is recorded durably, as a frozen
        packet that says so.

        A packet that was already STALE when the merge landed is frozen with
        ``stale_at_merge: true``. Freezing sets ``stale: false`` (a frozen
        packet is pinned, so "stale against today's heads" is meaningless), and
        that used to swallow the one thing a reader most needs to know: the
        evidence describes older commits than the ones that merged.
        """
        pid = proposal["id"]
        path = self.store.packet_path(proj, pid)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            if data.get("frozen"):
                return
        else:
            data = _absent_packet(proposal)
        data["frozen"] = True
        data["stale"] = False
        data["stale_at_merge"] = self._stale_at_merge(proposal, data)
        path.parent.mkdir(parents=True, exist_ok=True)
        ProjectStore._atomic_write(path, json.dumps(data, indent=2).encode())

    @staticmethod
    def _stale_at_merge(proposal: dict, packet: dict) -> bool:
        """Did the packet being frozen describe the commits that merged?

        The merge record carries ``heads`` = ``[target, source]`` — the merge
        commit's two parents, or a fast-forward's ``previous``/``commit``. A
        packet whose pinned heads differ was generated against something else,
        and a frozen packet cannot say so any other way.
        """
        heads = (proposal.get("merge") or {}).get("heads") or []
        if packet.get("generated") is None or len(heads) != 2:
            return False
        return (packet.get("target_head") != heads[0]
                or packet.get("source_head") != heads[1])

    def _resolve(self, proj: str, branch: str | None) -> str | None:
        if not isinstance(branch, str) or not branch:
            return None
        return self.service.history.resolve_branch(
            self.service.store.canonical_path_of(proj), branch)

    def _source_head(self, proj: str, proposal: dict) -> str | None:
        canonical = self.service.store.canonical_path_of(proj)
        source = proposal.get("source")
        if not isinstance(source, str) or not source:
            return None
        return self.service.history.resolve_branch(canonical, source)

    def _check_update_state(self, proposal: dict, state: str) -> None:
        frm = proposal.get("state")
        allowed = [s for s in _TRANSITIONS.get(frm, ()) if s in _UPDATABLE]
        if state not in allowed:
            raise ValidationError(
                f"cannot move proposal {proposal.get('id')} from {frm!r} to "
                f"{state!r}",
                {"id": proposal.get("id"), "from": frm, "to": state,
                 "allowed": allowed},
            )

    @staticmethod
    def _check_transition(proposal: dict, to: str) -> None:
        frm = proposal.get("state")
        allowed = list(_TRANSITIONS.get(frm, ()))
        # A same-state move is how a 'comment' review records a verdict
        # without changing state — legal only while the proposal is active.
        if to == frm and frm in ACTIVE:
            return
        if to not in allowed:
            raise ValidationError(
                f"cannot move proposal {proposal.get('id')} from {frm!r} to "
                f"{to!r}",
                {"id": proposal.get("id"), "from": frm, "to": to,
                 "allowed": allowed},
            )

    def _packet_summary(self, proj: str, proposal: dict) -> dict | None:
        """``{generated, stale, ok, frozen}`` for the persisted packet, or
        None when there is none. The packet itself is slice 4."""
        try:
            data = json.loads(
                self.store.packet_path(proj, proposal["id"])
                .read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        canonical = self.service.store.canonical_path_of(proj)
        heads = {
            "source_head": self.service.history.resolve_branch(
                canonical, proposal.get("source") or ""),
            "target_head": self.service.history.resolve_branch(
                canonical, proposal.get("target") or ""),
        }
        stale = bool(data.get("stale")) or any(
            data.get(key) != value for key, value in heads.items()
        )
        return {
            "generated": data.get("generated"),
            "stale": False if data.get("frozen") else stale,
            # A frozen packet is pinned, so it is never "stale" — but it may
            # have been stale when it was frozen, which is the one staleness
            # that mattered: it describes commits other than the ones merged.
            "stale_at_merge": bool(data.get("stale_at_merge")),
            "ok": bool(data.get("ok")),
            "frozen": bool(data.get("frozen")),
        }

    def _view(self, proj: str, proposal: dict) -> dict:
        """A copy of the proposal annotated for readers: each review carries
        ``stale`` against the current source head (it still counts in v1)."""
        view = copy.deepcopy(proposal)
        head = self._source_head(proj, proposal)
        for review in view.get("reviews") or []:
            review["stale"] = _is_stale(review, head)
        return view

    def _result(self, proj: str, proposal: dict) -> dict:
        return {"proposal": self._view(proj, proposal),
                "gates": self.gates(proj, proposal)}

    def _publish(self, proj: str, proposal: dict, reason: str) -> None:
        self.service.bus.publish({
            "type": "proposal_changed",
            "project": proj,
            "id": proposal["id"],
            "state": proposal["state"],
            "reason": reason,
        })


def _recovered_validation(message: str) -> dict | None:
    """The validation verdict of a merge nobody told us about, read back out
    of ``MergeOrchestrator._commit_message``. ``recovered`` marks it as
    reconstructed: the pass itself ran, its full report did not survive."""
    match = _VALIDATION_RE.search(message or "")
    if match is None:
        return None
    verdict = match.group(1).strip()
    return {
        "ok": verdict.lower().startswith("ok"),
        "recovered": True,
        "summary": verdict,
        "blocked": False,
        "built": [],
        "failures": [],
        "integrity": [],
        "warnings": [],
        "interference": {"checked": 0, "new_pairs": [], "skipped": None},
    }


def _absent_packet(proposal: dict) -> dict:
    """The durable record that no packet existed when the decision was made."""
    return {
        "proposal": proposal.get("id"),
        "ok": False,
        "stale": False,
        "stale_at_merge": False,
        "frozen": True,
        "generated": None,
        "generated_by": None,
        "note": "no review packet was generated before this proposal was "
                "closed out; one is never generated afterwards, because it "
                "would measure the branches as they are NOW and present that "
                "as the change that was reviewed",
        "source": proposal.get("source"),
        "target": proposal.get("target"),
        "source_head": None,
        "target_head": None,
        "base": None,
        "summary": {"parts_changed": 0, "parts_added": 0, "parts_removed": 0,
                    "instances_changed": 0, "mass_delta_g": None},
        "parts": [],
        "assembly": None,
        "manifest": {"scalars_changed": [], "materials_changed": []},
        "binary": [],
        "warnings": [],
        "errors": [],
    }


def _held_elsewhere(pid: str, holder: str | None, source: str, target: str,
                    merge_id: str | None) -> ConflictError:
    """The refusal a caller meets when the pair's staged merge belongs to
    someone else: who holds it, and what to do about it."""
    return ConflictError(
        f"a staged merge of {source!r} into {target!r} is held by {holder}; "
        f"close or merge that proposal first, then merge proposal {pid}",
        {"id": pid, "held_by": holder, "merge_id": merge_id},
    )


def _hold_key(pid: str) -> str:
    """What a staged merge records as its ``held_by``: the proposal that may
    complete it, and nothing else may."""
    return f"proposal:{pid}"


def _is_stale(review: dict, head: str | None) -> bool:
    recorded = review.get("source_head")
    return bool(recorded and head and recorded != head)


def _update_action(frm: str, to: str) -> str:
    if to == "closed":
        return "closed"
    if frm == "closed" and to == "open":
        return "reopened"
    return "state_changed"


def _text(value: object, what: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValidationError(f"{what} must be a non-empty string"
                              if not allow_empty else f"{what} must be a string")
    return value
