"""Tool pack: releases — the revision record + state machine over a project's
manifest (PRD-015 FR6-8).

Thin delegations; every rule lives in ``core/releases.py``. The pack
self-disables when git is not on PATH — a release opens a PRD-002 proposal, and
proposals need branches — so an agent never sees ``release_start`` on a project
that cannot run it (the ``tools_proposals`` / ``tools_versioning`` precedent).

**Load order.** ``tools._load_tool_packs`` walks ``pkgutil.iter_modules``
alphabetically, so this module registers *after* ``tools_proposals`` (which
resets ``service.gate_providers = []`` and installs ``service.proposals``) — the
seam ``release_start`` reaches. This pack registers **no gate provider** of its
own: the release checks are composed inline in ``release_start`` from the
proposal's already-evaluated specs/checks gates, not appended to
``gate_providers`` (which would run against *every* proposal). ``release_finalize``
— the tag/finalize path — is slice 4 and is deliberately absent here.
"""

from __future__ import annotations

from . import releases
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}
_REV = {"type": "string", "description": "Revision letter, e.g. 'A'"}


def register(registry, service) -> None:
    if not service.history.available():
        return  # no git -> no branches -> no proposals -> no releases

    def release_start(project: str, notes: str | None = None,
                      waive: dict | None = None) -> dict:
        return releases.release_start(service, project, notes=notes,
                                      waive=waive)

    def list_releases(project: str) -> dict:
        return releases.list_releases(service, project)

    def get_release(project: str, rev: str) -> dict:
        return releases.get_release(service, project, rev)

    registry.register(Tool(
        "release_start",
        "Cut a release of the current branch: allocate the next revision "
        "(A, B, … per project), open a 'release'-kind change proposal for it, "
        "and evaluate the release GATE. The gate reads the proposal's already-"
        "computed specs and checks gates and adds three release checks — the "
        "working tree is clean, sub-assembly references are version-pinned "
        "(a soft warning in v1), and drawings are regenerable (a soft check in "
        "v1). Returns {rev, proposal, gate, status}. A GREEN gate moves the "
        "release to 'in_review'; a RED gate returns the report and leaves it "
        "'draft' with each failing check named in gate.checks — the record is "
        "written either way, so you can see why. Pass waive: {reason} to record "
        "an explicit, attributed waiver that unblocks a red specs/checks gate "
        "and proceeds; the waiver is durable and shows in get_release (a silent "
        "override is impossible). A release must be cut from a branch OTHER than "
        "the project default. 'notes' is stored on the record and becomes the "
        "proposal description. This does NOT finalize or tag — that is "
        "release_finalize (a later slice).",
        schema(
            {
                "project": _PROJ,
                "notes": {"type": "string",
                          "description": "Release notes (stored on the record "
                                         "and used as the proposal description)"},
                "waive": {"type": "object",
                          "description": "Waive a red gate: {reason: <why>}. "
                                         "Recorded and attributed."},
            },
            ["project"],
        ),
        release_start,
    ))
    registry.register(Tool(
        "list_releases",
        "List a project's releases in revision order with {project, releases: "
        "[{name, rev, status, tag, proposal, notes, approvals, waiver?, gate, "
        "bundle?}]}. status is draft | in_review | released | superseded.",
        schema({"project": _PROJ}, ["project"]),
        list_releases,
    ))
    registry.register(Tool(
        "get_release",
        "Read one release: {project, release: <the record>, gate: <the gate "
        "report>}. The record carries its rev, status, the proposal id, notes, "
        "approvals, an optional waiver, and the composed gate report "
        "({status: green|red, checks: [{name, status, detail}], waiver}). An "
        "unknown rev is a notfound_error.",
        schema({"project": _PROJ, "rev": _REV}, ["project", "rev"]),
        get_release,
    ))
