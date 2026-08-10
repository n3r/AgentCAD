"""Change proposals: the durable object, its lifecycle, audit and gates.

A proposal is a *CAD pull request*: a branch pair (``source`` -> ``target``),
an intent, an attributed lifecycle and a gate in front of PRD-001's merge. This
module is the whole of that workflow and nothing else — it writes files, reads
branch heads, and never merges, builds or renders. ``proposal_merge`` (slice 2)
and the review packet (slice 4) are built on top of it.

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
hand does not hand its id to the next proposal.

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


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def actor_kind(identity: str) -> str:
    """``"human"`` iff the action came from the browser UI; everything else is
    an agent.

    The chat dock is a human ASKING an agent — the action is the agent's, and
    the audit trail must say so. This is a deliberate judgement call, not a
    heuristic to extend: the browser is the only surface a human drives
    directly. PRD-005 replaces it with the authenticated principal's class,
    with no schema change.
    """
    identity = identity or ""
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
                ),
                "proposals": _sorted([_summary(p) for p in proposals]),
            }
            if not ok or index != desired:
                self._write_index(proj, desired)
        return proposals

    def allocate_id(self, proj: str) -> str:
        """The next never-used id. Monotonic even when a proposal directory
        was removed by hand: ``next_id`` only increments."""
        with self._lock:
            self.list(proj)  # refreshes next_id past any hand-made directory
            index, _ok = self._read_index(proj)
            pid = str(max(1, int(index["next_id"])))
            index["next_id"] = int(pid) + 1
            self._write_index(proj, index)
            return pid

    # ---------------------------------------------------------------- index

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

    Owns one ``threading.RLock`` around every read-modify-write of a proposal.
    ``service.branches`` is resolved per call (see the module docstring).
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
        for role, name in (("source", source), ("target", target)):
            if self.service.history.resolve_branch(canonical, name) is None:
                raise NotFoundError(f"branch {name!r} not found",
                                    {"role": role, "branch": name})
        state = "draft" if draft else "open"
        actor = locks.current_client_id()
        with self._lock:
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
        if state is not None and state not in STATES:
            raise ValidationError(f"unknown proposal state {state!r}",
                                  {"allowed": list(STATES)})
        proposals = self.store.list(proj)
        counts = {name: 0 for name in STATES}
        for proposal in proposals:
            if proposal.get("state") in counts:
                counts[proposal["state"]] += 1
        rows = [_summary(p) for p in proposals
                if state is None or p.get("state") == state]
        return {"proposals": rows, "counts": counts}

    def get(self, proj: str, pid: str) -> dict:
        proposal = self.store.load(proj, pid)
        return {
            **self._result(proj, proposal),
            "audit": self.store.audit(proj, proposal["id"]),
            "packet": self._packet_summary(proj, proposal),
        }

    def update(self, proj: str, pid: str, title: str | None = None,
               description: str | None = None,
               state: str | None = None) -> dict:
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
            if review.get("actor"):
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

    # ------------------------------------------------------------ internals

    def _branches(self):
        branches = getattr(self.service, "branches", None)
        if branches is None:
            raise ValidationError(
                "proposals unavailable: git not found on PATH"
            )
        return branches

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
