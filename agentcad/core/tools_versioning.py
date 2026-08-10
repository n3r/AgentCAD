"""Tool pack: branches, immutable versions (tags) and kernel-validated merges.

Installs the two seams the feature needs and exposes them as tools:
``BranchManager`` (which registers ``ProjectStore.branch_resolver``, making
every authored-state read and write follow the calling client's branch) and
``MergeOrchestrator``. Both live on the service so route packs and the chat
agent reach the same objects. A third, smaller seam — ``install_write_guard``
— makes the store's write path branch-safe (see its docstring); it is exported
so tests can wire exactly what the pack wires.

The whole pack self-disables when git is not on PATH — no tools, no resolver,
no seams — so the product degrades to today's linear history rather than
offering an agent a tool that cannot run (the FEM-pack precedent).

Convention agents must not get backwards, repeated in every description here:
**ours = the target branch** (what you merge into), **theirs = the source**,
exactly like ``git merge <source>``.
"""

from __future__ import annotations

from . import locks
from .branches import BranchManager
from .merge import MergeOrchestrator
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}
_RESOLVE_RECIPE = (
    'Resolve with resolve_merge {choices: {"<path|key>": {"take": '
    '"ours"|"theirs"|"base"}}} — scripts also accept {"content": "…"} and '
    'manifest keys {"value": …} — or discard the staged merge with '
    "merge_abort."
)


def install_write_guard(service) -> None:
    """Make every persistent mutation branch-safe.

    Two things, in this order, because the second depends on the first:

    1. ``ensure_checkout`` — the calling client's branch tree must exist. A
       tree that vanished makes the (deliberately total) read-path resolver
       fall back to the project directory, and a *write* that followed that
       fallback would land on the default branch.
    2. the turn lock, keyed by the caller's resolved working tree, so turn
       locks and undo stacks are per-branch.
    """
    def guard(proj: str) -> None:
        service.branches.ensure_checkout(proj)
        service.turnlock.check(
            service.store.lock_key(proj), locks.current_client_id()
        )

    service.store.write_guard = guard


def register(registry, service) -> None:
    if not service.history.available():
        return  # no git: branches, versions and merges cannot run at all

    service.branches = BranchManager(service)
    service.merges = MergeOrchestrator(service)
    install_write_guard(service)

    def branch_create(project: str, name: str, **kwargs) -> dict:
        return service.branches.create(project, name, kwargs.get("from"))

    def branch_list(project: str) -> dict:
        return service.branches.list(project)

    def branch_switch(project: str, name: str) -> dict:
        branch = service.branches.switch(project, name)
        return {"branch": branch, "project": service.get_project(project)}

    def branch_delete(project: str, name: str) -> dict:
        return service.branches.delete(project, name)

    def version_tag(project: str, name: str, message: str | None = None) -> dict:
        return service.branches.tag(project, name, message)

    def list_versions(project: str) -> dict:
        return {"versions": service.branches.versions(project)}

    def merge_branch(project: str, source: str, target: str | None = None,
                     allow_invalid: bool = False) -> dict:
        return service.merges.merge(project, source, target,
                                    allow_invalid=bool(allow_invalid))

    def resolve_merge(project: str, choices: dict) -> dict:
        return service.merges.resolve(project, choices)

    def merge_abort(project: str) -> dict:
        return service.merges.abort(project)

    def merge_status(project: str) -> dict:
        return service.merges.status(project)

    registry.register(Tool(
        "branch_create",
        "Create a branch of a project and materialize its working tree. "
        "Branch names are lowercase [a-z0-9_/-], up to 64 characters. "
        "'from' defaults to your current branch and also accepts a tag or a "
        "commit id. Creating does NOT switch you — call branch_switch. Cheap: "
        "one checkout of the scripts and manifest; the mesh cache is shared, "
        "so nothing rebuilds.",
        schema(
            {
                "project": _PROJ,
                "name": {"type": "string",
                         "description": "New branch name, e.g. 'flange-weld'"},
                "from": {"type": "string",
                         "description": "Branch, tag or commit to fork from "
                                        "(default: your current branch)"},
            },
            ["project", "name"],
        ),
        branch_create,
    ))
    registry.register(Tool(
        "branch_list",
        "List a project's branches with each one's head commit, time, subject "
        "and which clients have it checked out, plus {current, default, you}. "
        "Branches are per-client: two agents can sit on different branches of "
        "one project at the same time, each with its own turn lock and undo "
        "stack.",
        schema({"project": _PROJ}, ["project"]),
        branch_list,
    ))
    registry.register(Tool(
        "branch_switch",
        "Point YOUR client at a branch (other clients are unaffected). O(1): "
        "the branch's working tree already exists, so nothing is checked out "
        "and unchanged parts do not rebuild. Snapshots your current tree "
        "first, so a switch is always a clean, restorable boundary. Returns "
        "the branch and the post-switch project state.",
        schema({"project": _PROJ,
                "name": {"type": "string", "description": "Branch to work on"}},
               ["project", "name"]),
        branch_switch,
    ))
    registry.register(Tool(
        "branch_delete",
        "Delete a branch and its working tree. Refused for the default branch "
        "and for a branch any client currently has checked out. Versions "
        "(tags) made on the branch survive it.",
        schema({"project": _PROJ, "name": {"type": "string"}},
               ["project", "name"]),
        branch_delete,
    ))
    registry.register(Tool(
        "version_tag",
        "Name the current state of your branch as an immutable version (an "
        "annotated git tag): 'the revision we sent to the machine shop'. "
        "Versions cannot be moved or deleted — re-using a name is a "
        "conflict_error. Restore one with project_restore {commit: '<name>'}.",
        schema(
            {
                "project": _PROJ,
                "name": {"type": "string",
                         "description": "Version name, e.g. 'v1.2' or 'shop-rev-a'"},
                "message": {"type": "string",
                            "description": "What this version is (default: the name)"},
            },
            ["project", "name"],
        ),
        version_tag,
    ))
    registry.register(Tool(
        "list_versions",
        "List a project's versions newest-first: {name, commit, ts, author, "
        "message, referrers}. Any of them can be read with project_history "
        "{ref} or restored with project_restore {commit: '<name>'}.",
        schema({"project": _PROJ}, ["project"]),
        list_versions,
    ))
    registry.register(Tool(
        "merge_branch",
        "Merge branch 'source' into 'target' (default: your current branch). "
        "Fast-forwards when the target has nothing of its own; otherwise a "
        "real three-way merge — part scripts via git's textual merge, "
        "project.json key-wise (per part, per parameter, per instance, per "
        "material), so concurrent edits to different parts, or to one part's "
        "script and its params, both land. OURS = the target branch, THEIRS = "
        "the source. Conflicts come back as {error: {type: 'merge_conflict'}} "
        "with base/ours/theirs for each conflict and the merge staged, "
        "untouched, until you resolve or abort it — nothing is ever partially "
        "applied. " + _RESOLVE_RECIPE + " Before the merge lands, the kernel "
        "revalidates it: changed parts rebuild, mates re-resolve and newly "
        "introduced interference is reported; failures block the merge unless "
        "allow_invalid is true, which lands it with the failures recorded in "
        "the merge commit. The result is one merge commit with both parents. "
        "Calling this again on a staged merge whose branches have moved is a "
        "conflict_error: its recorded resolutions no longer apply, so discard "
        "it with merge_abort and merge again.",
        schema(
            {
                "project": _PROJ,
                "source": {"type": "string",
                           "description": "Branch to merge FROM (theirs)"},
                "target": {"type": "string",
                           "description": "Branch to merge INTO (ours); "
                                          "default: your current branch"},
                "allow_invalid": {
                    "type": "boolean",
                    "description": "Land the merge even if the validation pass "
                                   "fails (recorded in the commit message)",
                },
            },
            ["project", "source"],
        ),
        merge_branch,
    ))
    registry.register(Tool(
        "resolve_merge",
        "Resolve conflicts of the staged merge. 'choices' maps each conflict's "
        'path (scripts, e.g. "parts/flange.py") or key (manifest, e.g. '
        '"parts.flange.params.bolt_d") to {"take": "ours"|"theirs"|"base"}, or '
        'to {"content": "<full file text>"} for a script, or {"value": …} for '
        "a manifest key. OURS = the target branch, THEIRS = the source. A "
        "binary conflict (kind 'binary', anything under imports/) reports "
        "sizes and digests per side and takes a side only — no 'content'. "
        "Taking a side that does not have the file (that branch deleted it) "
        "deletes it; taking 'base' when both branches added the file is a "
        "validation_error. You may resolve a few at a time; the reply lists "
        "what is still outstanding, and the merge completes (validation pass "
        "included) as soon as nothing is.",
        schema(
            {
                "project": _PROJ,
                "choices": {"type": "object",
                            "description": "conflict path/key -> choice"},
            },
            ["project", "choices"],
        ),
        resolve_merge,
    ))
    registry.register(Tool(
        "merge_abort",
        "Discard the staged merge: its working tree and state are removed and "
        "no branch moves. A no-op when nothing is staged.",
        schema({"project": _PROJ}, ["project"]),
        merge_abort,
    ))
    registry.register(Tool(
        "merge_status",
        "Inspect the staged merge, if any: {merge: {id, source, target, base, "
        "by, created, outstanding, conflicts, resolved}} or {merge: null}. Use "
        "it to re-enter a merge you (or another client) started earlier.",
        schema({"project": _PROJ}, ["project"]),
        merge_status,
    ))
