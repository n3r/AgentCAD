"""Tool pack: change proposals — a CAD pull request over PRD-001's branches.

Installs the two seams the feature needs — ``service.proposals``
(:class:`~agentcad.core.proposals.ProposalManager`) and
``service.gate_providers``, the empty list PRD-003 (specs) and PRD-004 (checks)
append their own gates to — and exposes the lifecycle as tools. Handlers are
thin delegations; every workflow rule lives in ``proposals.py``.

The whole pack self-disables when git is not on PATH — no tools, no seams — so
the product degrades to today's linear history rather than offering an agent a
tool that cannot run (the FEM-pack precedent, shared with ``tools_versioning``).

**Load order.** ``tools._load_tool_packs`` walks ``pkgutil.iter_modules``
alphabetically, so this module is imported *before* ``tools_versioning``:
``service.branches`` and ``service.merges`` do not exist yet when ``register()``
runs. The manager therefore reaches both lazily, inside each call, and installs
its ``branch_delete`` guard on first use (and from ``routes_proposals``, the
first point at which ``service.branches`` is guaranteed to exist) rather than
here.

Convention agents must not get backwards, repeated in the descriptions below:
**old = the target branch** (ours, what you merge into), **new = the source**
(theirs, the proposed work), exactly like ``git merge <source>``.
"""

from __future__ import annotations

from .proposals import ProposalManager
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}
_ID = {"type": "string", "description": "Proposal id, e.g. '3'"}
_SIDES = (
    "Read the pair like 'git merge <source>': the TARGET branch is OLD "
    "(ours — what the change lands in) and the SOURCE branch is NEW (theirs — "
    "the proposed work)."
)


def register(registry, service) -> None:
    if not service.history.available():
        return  # no git -> no branches -> no proposals

    service.proposals = ProposalManager(service)
    # The gate seam: a provider takes (project, proposal) and returns a gate
    # object or None. Empty here, appended to by PRD-003/PRD-004 from their
    # own register(), so neither has to touch proposals.py.
    service.gate_providers = []

    def proposal_create(project: str, source: str, title: str,
                        target: str | None = None, description: str = "",
                        draft: bool = False) -> dict:
        return service.proposals.create(
            project, source, target=target, title=title,
            description=description, draft=bool(draft),
        )

    def proposal_list(project: str, state: str | None = None) -> dict:
        return service.proposals.list(project, state)

    # Handlers are called as handler(**args), so the parameter names ARE the
    # schema keys: 'id' shadows the builtin here by contract, not by accident.
    def proposal_get(project: str, id: str) -> dict:
        return service.proposals.get(project, id)

    def proposal_update(project: str, id: str,
                        title: str | None = None,
                        description: str | None = None,
                        state: str | None = None) -> dict:
        return service.proposals.update(project, id, title=title,
                                        description=description, state=state)

    def proposal_review(project: str, id: str, verdict: str,
                        summary: str | None = None) -> dict:
        return service.proposals.review(project, id, verdict, summary)

    def proposal_merge(project: str, id: str,
                       allow_invalid: bool = False) -> dict:
        return service.proposals.merge(project, id,
                                       allow_invalid=bool(allow_invalid))

    registry.register(Tool(
        "proposal_create",
        "Open a change proposal: a reviewable, durable object over a branch "
        "pair — the CAD equivalent of a pull request. " + _SIDES + " 'target' "
        "defaults to the project's DEFAULT branch (not your current one — a "
        "proposal is read by other clients, so its target must not depend on "
        "who opened it). Returns {proposal, gates, packet}. Say in "
        "'description' WHY the change is right; that is what a reviewer "
        "judges. 'draft' opens it unreviewable until you update it to 'open'. "
        "A second active proposal for the same pair is a conflict_error naming "
        "the existing id; an unknown branch is a notfound_error (a version tag "
        "does not answer for a branch); source == target is a "
        "validation_error. The proposal lives outside every working tree, so "
        "project_restore never rewinds it.",
        schema(
            {
                "project": _PROJ,
                "source": {"type": "string",
                           "description": "Branch holding the work (new/theirs)"},
                "target": {"type": "string",
                           "description": "Branch it should land in "
                                          "(old/ours; default: the project's "
                                          "default branch)"},
                "title": {"type": "string",
                          "description": "One line: what this change does"},
                "description": {"type": "string",
                                "description": "The argument for the change"},
                "draft": {"type": "boolean",
                          "description": "Open it as a draft (not reviewable "
                                         "until updated to 'open')"},
            },
            ["project", "source", "title"],
        ),
        proposal_create,
    ))
    registry.register(Tool(
        "proposal_list",
        "List a project's proposals oldest-id-first with {proposals: "
        "[{id, source, target, title, state, author, author_kind, created, "
        "updated, reviews, merge_commit}], counts: {<state>: n}}. States are "
        "draft, open, approved, changes_requested, merged and closed; pass "
        "'state' to filter. 'author_kind' is human or agent — bookkeeping "
        "derived from the caller's identity, not authentication.",
        schema({"project": _PROJ,
                "state": {"type": "string",
                          "description": "Only proposals in this state"}},
               ["project"]),
        proposal_list,
    ))
    registry.register(Tool(
        "proposal_get",
        "Read one proposal in full: {proposal, gates, audit, packet}. 'gates' "
        "is the merge checklist — [{name, state, summary, details}] with state "
        "pass/fail/pending/skipped — covering the proposal's own state, the "
        "approvals policy, the kernel validation pass (pending until the "
        "merge runs it), and any spec/check providers installed. 'audit' is "
        "the append-only log of every action with its actor and human/agent "
        "kind. Each review is marked stale when it was made against an older "
        "source head (it still counts).",
        schema({"project": _PROJ, "id": _ID}, ["project", "id"]),
        proposal_get,
    ))
    registry.register(Tool(
        "proposal_update",
        "Edit a proposal's title or description, or move its state: "
        "draft -> open (request review), anything active -> closed, closed -> "
        "open (reopen), changes_requested -> open (you addressed the "
        "feedback). Approving is proposal_review and merging is "
        "proposal_merge; neither can be faked by writing a state, and any "
        "other move is a validation_error carrying {from, to, allowed}. "
        "Returns the updated proposal and gates.",
        schema(
            {
                "project": _PROJ,
                "id": _ID,
                "title": {"type": "string"},
                "description": {"type": "string"},
                "state": {"type": "string",
                          "description": "'open' or 'closed'"},
            },
            ["project", "id"],
        ),
        proposal_update,
    ))
    registry.register(Tool(
        "proposal_review",
        "Record a verdict: 'approve' (-> approved), 'request_changes' (-> "
        "changes_requested, which blocks the merge until the author reopens "
        "it with proposal_update {state: 'open'}), or 'comment' (recorded, "
        "state unchanged). Put your reasoning in 'summary' — it is written to "
        "the permanent audit log with your identity and whether you are a "
        "human or an agent. The latest verdict per actor is the one that "
        "counts. Returns the updated proposal and gates.",
        schema(
            {
                "project": _PROJ,
                "id": _ID,
                "verdict": {"type": "string",
                            "description": "approve | request_changes | comment"},
                "summary": {"type": "string",
                            "description": "Why — recorded in the audit log"},
            },
            ["project", "id", "verdict"],
        ),
        proposal_review,
    ))
    registry.register(Tool(
        "proposal_merge",
        "Merge the proposal's source branch into its target through the gate. "
        + _SIDES + " Gates are checked FIRST: a red one is a conflict_error "
        "naming it in details.gates and nothing is merged — by default one "
        "approval is required and the author's own approval does not count. "
        "Then PRD-001's merge_branch runs unchanged: a real three-way merge "
        "with the kernel revalidating the merged state before a two-parent "
        "commit lands. Three outcomes. Success returns that payload plus "
        "{proposal, gates}, with the proposal moved to 'merged' and its review "
        "packet frozen. Conflicts come back as {error: {type: "
        "'merge_conflict'}} with the merge staged and the proposal untouched — "
        "resolve it with resolve_merge (or discard it with merge_abort) and "
        "call proposal_merge again. A merge whose geometry fails the kernel "
        "validation pass is a validation_error carrying details.validation; "
        "re-run with allow_invalid: true to land it anyway, which is recorded "
        "in the audit log, on the proposal and in the merge commit message. "
        "allow_invalid overrides the kernel validation gate ONLY — it never "
        "waives the approval policy. A draft cannot be merged.",
        schema(
            {
                "project": _PROJ,
                "id": _ID,
                "allow_invalid": {
                    "type": "boolean",
                    "description": "Land the merge even if the kernel "
                                   "validation pass fails (recorded; does not "
                                   "affect the approvals gate)",
                },
            },
            ["project", "id"],
        ),
        proposal_merge,
    ))
