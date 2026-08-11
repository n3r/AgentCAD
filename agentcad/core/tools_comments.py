"""Tool pack: anchored review threads — the agent's half of PRD-008.

Installs the one seam the feature needs — ``service.comments``
(:class:`~agentcad.core.comments.CommentManager`) — and exposes four of the
five tools FR7 freezes: ``list_comments``, ``add_comment``, ``resolve_thread``
and ``reopen_thread`` (``list_notifications`` arrives with mentions). Handlers
are thin delegations: every lifecycle rule lives in ``core/comments.py`` and
every anchor rule in ``core/anchors.py``. This pack measures nothing and
resolves nothing itself.

**The pack does not self-disable without git**, unlike ``tools_proposals`` and
``tools_versioning``: a comment is not a commit. ``.history/agentcad/comments/``
is just a directory when git is absent, and only tier-2 line remapping and
``proposal_hunk`` anchors degrade — by answering ``unverified`` and saying why,
which is the whole point of the four-state contract.

**Load order.** ``tools._load_tool_packs`` walks ``pkgutil.iter_modules``
**alphabetically**, so this module (``c``) is imported *second*, before
``tools_proposals`` (``p``), ``tools_run_checks`` (``r``) and
``tools_versioning`` (``v``). Two consequences the implementation obeys and
``tests/test_comments_api.py`` pins:

* ``service.proposals``, ``service.branches`` and ``service.merges`` do **not
  exist** when :func:`register` runs. ``CommentManager`` reads them inside each
  call (``getattr(self.service, "branches", None)``) and nothing here captures
  them.
* This pack assigns **nothing a later pack assigns unconditionally**. In
  particular it adds **no gate provider**: ``tools_proposals`` sets
  ``service.gate_providers = []`` unconditionally at ``p``, so an append from
  ``c`` would be silently discarded — and threads do not gate merges anyway
  (design Decision 20: threads inform, verdicts decide).

Every mutating tool returns the **post-state thread** under a ``thread`` key,
and every mutation publishes ``comment_changed`` from the manager — never
``project_changed``, which would snapshot history for a comment.
"""

from __future__ import annotations

from .comments import ANCHOR_KINDS, MAX_ATTACHMENTS, STATES, CommentManager
from .model import ValidationError
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}
_THREAD = {"type": "string", "description": "Thread id, e.g. '7'"}

#: The four resolution states, in the words an agent has to act on. Repeated
#: in both read descriptions because an agent that renders `unverified` as
#: "fine" — or pins the STORED ordinal — is wrong in exactly the way the
#: feature exists to prevent.
_RESOLUTION = (
    "Every thread carries a live 'resolution' block computed AT READ TIME "
    "(the stored anchor is immutable evidence of what the author pointed at "
    "and is never rewritten), with four different statuses: 'ok' — it still "
    "points at what it pointed at; 'moved' — re-matched at a NEW address, "
    "which the block carries (resolution.face_index, resolution.start/end) "
    "along with the score that earned it; 'orphaned' — the target is gone or "
    "no candidate cleared the tolerance, which is the CONTRACT and not a bug "
    "(the thread stays readable, listable and resolvable and keeps its "
    "last-known anchor); 'unverified' — WE DID NOT LOOK (the part has never "
    "been built, git is absent, the packet is frozen, or the anchor belongs "
    "to another branch). 'unverified' is a fourth fact, never a synonym for "
    "'fine'. Non-'ok' statuses always carry 'reason', and 'orphaned'/"
    "'unverified' always carry a 'hint'. ALWAYS address a face through "
    "resolution.face_index and lines through resolution.start/end — NEVER the "
    "stored anchor.face_index or anchor.start, which are the ordinals at "
    "creation time and are stale the moment geometry changes."
)

#: The measured honesty (changelog 0113's R1 spike): the numbers, not a vibe.
_FACE_ODDS = (
    "Face ordinals are NOT stable across a parameter change (measured: 87-93% "
    "hold, and one bundled part renumbered 20 of its 44 faces for a 1% "
    "tweak), so a face anchor is re-matched from the mesh-derived signature it "
    "stored. Measured over 2 537 faces: about TWO TIMES IN THREE it resolves "
    "(ok/moved), and the remaining third comes back 'orphaned' — never a "
    "wrong face (0 mis-pins). A repeated feature (a thread, a bolt circle) is "
    "genuinely ambiguous and orphans by design: 'orphan, never mis-pin'."
)

#: Identity here is a self-asserted header, and every surface must say so.
_IDENTITY = (
    "'author'/'actor' is locks.current_client_id() (browser, browser:<nonce>, "
    "chat:<session>, an MCP agent's X-Agent-Id) and 'author_kind'/'actor_kind' "
    "is 'human' iff that identity is the browser. This is BOOKKEEPING, NOT "
    "AUTHENTICATION — the header is unvalidated until multi-user identity "
    "lands. Anyone may resolve or reopen any thread; only a comment's own "
    "author may edit or delete it, as an honesty check rather than an "
    "authorization rule, and the per-thread audit log records who did what. "
    "Agents get no claim tools: coordinate through acquire_turn and branches."
)


def register(registry, service) -> None:
    # Constructed HERE so the tools, the routes and the UI share one manager
    # (one RLock over each thread document, one publisher). Nothing about
    # `service.proposals` / `service.branches` is captured — see the module
    # docstring; both are installed by packs that load after this one.
    service.comments = CommentManager(service)

    def list_comments(project: str, part_id: str | None = None,
                      state: str | None = None, kind: str | None = None,
                      branch: str | None = None,
                      anchor_status: str | None = None,
                      resolve_anchors: bool | None = None) -> dict:
        return service.comments.list(
            project, state=state, kind=kind, part_id=part_id, branch=branch,
            anchor_status=anchor_status,
            # `null` on an optional argument means "omitted" (the registry's
            # convention), and the default is TRUE: a caller sending a uniform
            # payload of nulls must get resolutions, not a silently cheap list.
            resolve_anchors=True if resolve_anchors is None
            else bool(resolve_anchors),
        )

    def add_comment(project: str, body: str, anchor: dict | None = None,
                    thread: str | None = None,
                    attachments: list | None = None) -> dict:
        if (anchor is None) == (thread is None):
            # One call opens threads and replies to them, so the rule that
            # distinguishes them is named rather than guessed at: an anchor
            # opens, a thread id replies, and asking for both would silently
            # discard one of them.
            raise ValidationError(
                "add_comment takes exactly one of 'anchor' (open a new "
                "thread on it) or 'thread' (reply to that thread)",
                {"required": ["anchor", "thread"],
                 "anchor": anchor is not None, "thread": thread is not None},
            )
        if thread is not None:
            return {"thread": service.comments.reply(
                project, thread, body, attachments)}
        return {"thread": service.comments.create(
            project, anchor, body, attachments)}

    def resolve_thread(project: str, thread: str) -> dict:
        return {"thread": service.comments.resolve(project, thread)}

    def reopen_thread(project: str, thread: str) -> dict:
        return {"thread": service.comments.reopen(project, thread)}

    registry.register(Tool(
        "list_comments",
        "List the review threads on a project — the anchored, resolvable "
        "conversation about the model, which lives outside history snapshots "
        "and outside branches (every branch sees the same list, and "
        "project_restore cannot rewind one). " + _RESOLUTION + " " +
        _FACE_ODDS + " Returns {threads: [{id, project, state: "
        "open|resolved, anchor: {kind, ...evidence}, resolution: {status, "
        "reason, hint, confidence, face_index|start/end, against: {branch, "
        "head}}, branch, author, author_kind, created, updated, resolved, "
        "comments: [{id, author, author_kind, ts, body, attachments: [{path, "
        "available}], mentions, edited, deleted}]}], counts: {open, resolved, "
        "orphaned}}. 'counts' describes the WHOLE project, never the filtered "
        "page, so a badge does not change when a filter is applied. Listing "
        "NEVER BUILDS a part: resolution reads the manifest, the meshes a "
        "build already wrote and at most one git blob per anchor, so a face "
        "anchor on a part that has never been built comes back 'unverified'/"
        "'part_not_built' rather than costing a 300 s rebuild — build the part "
        "first if you need the answer. Pass resolve_anchors: false for the "
        "cheapest possible listing: no 'resolution' block at all and no "
        "'orphaned' count, because nothing was looked at. " + _IDENTITY,
        schema({"project": _PROJ,
                "part_id": {"type": "string",
                            "description": "Only threads anchored to this "
                                           "part"},
                "state": {"type": "string",
                          "description": "open | resolved"},
                "kind": {"type": "string",
                         "description": "Anchor kind: "
                                        f"{' | '.join(ANCHOR_KINDS)}"},
                "branch": {"type": "string",
                           "description": "Only threads authored on this "
                                          "branch (the default is every "
                                          "thread — a review comment on main "
                                          "must stay visible from a feature "
                                          "branch)"},
                "anchor_status": {"type": "string",
                                  "description": "Only threads whose anchor "
                                                 "resolves to this status: "
                                                 "ok | moved | orphaned | "
                                                 "unverified (a "
                                                 "validation_error together "
                                                 "with resolve_anchors: "
                                                 "false, which computes none)"},
                "resolve_anchors": {"type": "boolean",
                                    "description": "Compute each anchor's "
                                                   "current status (default "
                                                   "true; false is the "
                                                   "cheapest listing)"}},
               ["project"]),
        list_comments,
    ))

    registry.register(Tool(
        "add_comment",
        "Open a review thread on something specific, or reply to one. Pass "
        "EXACTLY ONE of 'anchor' (opens a new thread) or 'thread' (appends a "
        "reply to that thread id) — both or neither is a validation_error. "
        "Returns the post-state {thread}. The six anchor kinds, each validated "
        "at creation so a bad anchor is a validation_error rather than a "
        "stored orphan: {kind: 'part', part: 'nozzle'} — the whole part; "
        "{kind: 'face', part: 'nozzle', face_index: 12} — one B-rep face, "
        "validated against the built mesh's face count (the part must have "
        "been built, and an imported reference part has no faces to anchor "
        "to), storing a mesh-derived signature {centroid, normal, area_mm2, "
        "bbox_uvw} that the matcher re-identifies the face by; {kind: "
        "'param', part: 'nozzle', param: 'wall'} — one PARAMS entry; {kind: "
        "'script_range', part: 'nozzle', start: 40, end: 47} — 1-based "
        "INCLUSIVE line range, storing the exact snippet and its surrounding "
        "context so the range follows an edit made above it; {kind: "
        "'instance', instance: 'nozzle_1'} — one assembly instance; {kind: "
        "'proposal_hunk', proposal: '3', file: 'parts/nozzle.py', hunk: 1} — "
        "one diff hunk of a change proposal. The anchor's branch, head and "
        "evidence are stamped by the server and REFUSED from the caller: a "
        "signature a client can assert is not evidence of anything. " +
        _RESOLUTION + " Attachments are project files that must live under "
        "exports/ — pass what render_view returned, absolute or as "
        "'exports/renders/x.png'; anything resolving outside that tree "
        f"(including through a symlink) is a validation_error, and at most "
        f"{MAX_ATTACHMENTS} are allowed per comment. A body is markdown-ish "
        "text stored verbatim and rendered as text by the UI. A resolved "
        "thread still takes replies — the conversation about a decision "
        "outlives the decision. " + _IDENTITY,
        schema({"project": _PROJ,
                "anchor": {"type": "object",
                           "description": "What to anchor a NEW thread to "
                                          "(exactly one of anchor|thread)"},
                "thread": {"type": "string",
                           "description": "Existing thread id to reply to "
                                          "(exactly one of anchor|thread)"},
                "body": {"type": "string",
                         "description": "The comment text (non-empty)"},
                "attachments": {"type": "array",
                                "description": "Paths under exports/, e.g. "
                                               "['exports/renders/iso.png']"}},
               ["project", "body"]),
        add_comment,
    ))

    registry.register(Tool(
        "resolve_thread",
        "Mark a review thread resolved — the thread's work is done. "
        "Idempotent: resolving a resolved thread changes nothing, records "
        "nothing and publishes nothing. Returns the post-state {thread}. The "
        "thread stays readable and still takes replies, and its anchor keeps "
        "resolving, including when that anchor is 'orphaned' — resolving is "
        "how a thread is retired, and deleting its root comment is refused "
        f"for exactly that reason. The two states are {' and '.join(STATES)}: "
        "there is no 'closed' and no assignment. " + _IDENTITY,
        schema({"project": _PROJ, "thread": _THREAD}, ["project", "thread"]),
        resolve_thread,
    ))

    registry.register(Tool(
        "reopen_thread",
        "Move a resolved review thread back to 'open' — the fix did not land, "
        "or the problem came back. Idempotent, and the mirror of "
        "resolve_thread in every respect: it returns the post-state {thread}, "
        "records the actor in the thread's audit log, and clears the "
        "'resolved' block. " + _IDENTITY,
        schema({"project": _PROJ, "thread": _THREAD}, ["project", "thread"]),
        reopen_thread,
    ))
