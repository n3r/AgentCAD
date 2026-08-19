"""Release records and the revision state machine (PRD-015 FR6-8).

**Pure Python, no OCP/build123d, and no kernel calls of its own.** A release is
a durable record in the project manifest under ``manifest["releases"][<rev>]``:

    {name, rev, status, tag, proposal, notes,
     approvals: [{principal, ts}], waiver?, gate, bundle?}

``rev`` auto-sequences ``A, B, …`` per project (spreadsheet-style, so ``Z`` rolls
to ``AA``). The status machine is ``draft → in_review → released → superseded``;
this slice (3) only reaches ``in_review`` — the tag/finalize path (``released``,
``superseded``) is slice 4.

**The gate comes from the proposal, not from re-running specs.** ``release_start``
opens a ``release``-kind PRD-002 proposal (:class:`ProposalManager`) whose
``service.gate_providers`` — the specs gate (PRD-003) and the checks gate
(PRD-004) — are evaluated *for free* on creation. We read those already-computed
gates off the ``create`` result and add three zero-kernel release checks:

* **working tree clean** — the branch has no uncommitted tracked changes.
* **sub-assembly refs version-pinned** — a *soft* warning in v1 (PRD-013 reserves
  ``version`` on a sub-assembly ref for a later phase, so a floating ref cannot
  actually be pinned yet; naming it is the honest thing, blocking on an
  unbuildable feature is not — see the module note below).
* **drawings regenerable** — a *soft* pass in v1 (a real probe would run
  ``generate_drawing``, i.e. a kernel call; the bundle regenerates drawings
  deterministically in slice 5, and this check documents that intent without
  paying for it here).

A **red** gate leaves the release ``draft`` (the report is still returned, so the
human sees why); a **green** (or waived) gate moves it to ``in_review``. A
``waive: {reason}`` records a durable, attributed waiver object — silent override
is impossible — that unblocks a red *check* gate and survives into
``get_release``.

**Deviation, documented (design allows it):** the two soft checks above never
hard-fail a v1 release. ``subassembly_refs_pinned`` is a ``warn`` (never a
``fail``) and ``drawings_regenerable`` is always ``pass``; neither flips the
gate red. Both are wired so a later phase can tighten them without moving the
seam.
"""

from __future__ import annotations

import copy
import re

from . import locks
from .model import NotFoundError, ValidationError
from .proposals import _now, actor_kind

STATUSES = ("draft", "in_review", "released", "superseded")

_REV_RE = re.compile(r"^[A-Z]+$")

# How a proposal gate's ``state`` maps into a release-report check ``status``.
# Fail-closed: a ``pending`` provider (we do not know) is not readiness.
_GATE_STATE = {"pass": "pass", "skipped": "skip", "fail": "fail",
               "pending": "fail"}


# ------------------------------------------------------------ rev sequencing


def _is_rev(name: object) -> bool:
    return isinstance(name, str) and bool(_REV_RE.match(name))


def _inc_rev(rev: str) -> str:
    """Spreadsheet-column increment: ``A→B``, ``Z→AA``, ``AZ→BA``."""
    chars = list(rev)
    i = len(chars) - 1
    while i >= 0:
        if chars[i] == "Z":
            chars[i] = "A"
            i -= 1
        else:
            chars[i] = chr(ord(chars[i]) + 1)
            return "".join(chars)
    return "A" + "".join(chars)


def _next_rev(releases: dict) -> str:
    """The next unused revision letter for a project, or ``A`` when there are
    none. Highest is by ``(length, value)`` so ``AA`` outranks ``Z``."""
    existing = [r for r in (releases or {}) if _is_rev(r)]
    if not existing:
        return "A"
    return _inc_rev(max(existing, key=lambda r: (len(r), r)))


# -------------------------------------------------------------------- reads


def _releases_map(service, project: str) -> dict:
    manifest = service.store.manifest(project)
    releases = manifest.get("releases")
    return releases if isinstance(releases, dict) else {}


def list_releases(service, project: str) -> dict:
    """Every release of ``project``, rev order (``A`` before ``AA``)."""
    records = _releases_map(service, project)
    rows = [copy.deepcopy(rec) for rev, rec in records.items()
            if _is_rev(rev) and isinstance(rec, dict)]
    rows.sort(key=lambda r: (len(r.get("rev") or ""), r.get("rev") or ""))
    return {"project": project, "releases": rows}


def get_release(service, project: str, rev: str) -> dict:
    """One release record plus its gate report (and, from slice 5, its
    artifact list). An unknown rev is a ``NotFoundError``."""
    record = _releases_map(service, project).get(rev)
    if not isinstance(record, dict):
        raise NotFoundError(
            f"release {rev!r} not found in project {project!r}",
            {"project": project, "rev": rev})
    record = copy.deepcopy(record)
    return {"project": project, "release": record, "gate": record.get("gate")}


# ---------------------------------------------------------------- the start


def release_start(service, project: str, notes: str | None = None,
                  waive: dict | None = None) -> dict:
    """Cut a release: allocate the next rev, open a ``release``-kind proposal,
    compose the gate report, and persist the draft record.

    Zero kernel calls here — the specs/checks gates are evaluated by the
    proposal's providers on ``create`` and read back off the result. Returns
    ``{rev, proposal, gate, status}``; a red gate returns the report and leaves
    the record ``draft`` (the record is written either way).
    """
    branches = _branches(service)
    source = branches.current(project)
    target = branches.default_branch(project)
    if source == target:
        raise ValidationError(
            f"a release must be cut from a branch other than the default "
            f"({target!r}); create a release branch and switch to it first",
            {"project": project, "branch": source, "default": target})

    waiver = _make_waiver(waive)
    rev = _next_rev(_releases_map(service, project))

    # 1. Open the proposal. Its specs + checks gates are evaluated for free.
    created = service.proposals.create(
        project, source, target=target, kind="release",
        title=f"Release {rev}", description=notes or "")
    proposal = created["proposal"]
    pid = proposal["id"]

    # 2. Compose the gate report from the proposal's gates + release checks.
    gate = _gate_report(service, project, source,
                        created.get("gates") or [], waiver)
    status = "in_review" if gate["status"] == "green" else "draft"

    # 3. Persist the record (RMW the live branch manifest).
    record = {
        "name": f"Release {rev}",
        "rev": rev,
        "status": status,
        "tag": None,
        "proposal": pid,
        "notes": notes,
        "approvals": [],
        "gate": gate,
        "bundle": None,
    }
    if waiver is not None:
        record["waiver"] = waiver

    manifest = service.store.manifest(project)
    releases = manifest.get("releases")
    releases = releases if isinstance(releases, dict) else {}
    releases[rev] = record
    manifest["releases"] = releases
    service.store.save_manifest(project, manifest)
    service.bus.publish({"type": "project_changed", "project": project,
                         "reason": "release"})

    return {"rev": rev, "proposal": pid, "gate": gate, "status": status}


# ------------------------------------------------------------- the gate report


def _gate_report(service, project: str, source: str,
                 proposal_gates: list, waiver: dict | None) -> dict:
    """``{status: green|red, checks: [{name, status, detail, …}], waiver}``.

    The specs and checks checks are lifted from the proposal's already-evaluated
    gates; the last three are release-specific and computed here without a
    kernel call. A ``waive`` marks every failing check ``waived`` and stops it
    blocking — the record still carries the failing check, so nothing is hidden.
    """
    checks = [
        _proposal_check("specs", _find_gate(proposal_gates, "specs")),
        _proposal_check("checks", _find_gate(proposal_gates, "checks")),
        _clean_tree_check(service, project, source),
        _subassembly_refs_check(service, project),
        _drawings_regenerable_check(service, project),
    ]
    blocking = [c for c in checks if c["status"] == "fail"]
    if waiver is not None:
        for check in blocking:
            check["waived"] = True
    red = bool(blocking) and waiver is None
    return {"status": "red" if red else "green", "checks": checks,
            "waiver": waiver}


def _find_gate(gates: list, name: str) -> dict | None:
    for gate in gates or []:
        if isinstance(gate, dict) and gate.get("name") == name:
            return gate
    return None


def _proposal_check(name: str, gate: dict | None) -> dict:
    """Translate one proposal gate into a release-report check. The whole gate
    object rides along under ``gate`` so a failing check is fully named (the
    specs gate's ``details.failures`` is exactly AC4's evidence)."""
    if not isinstance(gate, dict):
        return {"name": name, "status": "skip",
                "detail": f"the {name} gate is not installed"}
    return {"name": name,
            "status": _GATE_STATE.get(gate.get("state"), "fail"),
            "detail": gate.get("summary") or "",
            "gate": {"state": gate.get("state"),
                     "details": gate.get("details")}}


def _clean_tree_check(service, project: str, source: str) -> dict:
    """A release must be cut from a committed state — no uncommitted tracked
    changes on the branch. Zero kernel calls (``git status --porcelain``)."""
    try:
        tree = service.branches.tree_of(project, source)
        dirty = service.history._dirty_paths(tree)
    except Exception as exc:  # noqa: BLE001 — a probe failure is a fail, not a
        return {"name": "working_tree_clean", "status": "fail",  # crash
                "detail": f"could not inspect the working tree: {exc}"}
    if dirty:
        return {"name": "working_tree_clean", "status": "fail",
                "detail": f"{len(dirty)} uncommitted path(s) on {source!r}; "
                          "commit or discard them before cutting a release",
                "paths": dirty}
    return {"name": "working_tree_clean", "status": "pass",
            "detail": f"the working tree on {source!r} is clean"}


def _subassembly_refs_check(service, project: str) -> dict:
    """Warn (never block) when a sub-assembly instance references its source by
    a floating (un-pinned) ref.

    A sub-assembly ref is ``{project, version?, config?}`` and ``version`` is
    reserved for a later PRD-013 phase — so in v1 every sub-assembly ref is
    floating and there is no way to pin one. Blocking would make any project
    with a sub-assembly un-releasable; naming the floating refs is the honest,
    forward-compatible middle (a later phase tightens ``warn`` to ``fail``)."""
    manifest = service.store.manifest(project)
    assembly = manifest.get("assembly") or {}
    floating = []
    for inst in assembly.get("instances") or []:
        if not isinstance(inst, dict):
            continue
        ref = inst.get("assembly")
        if isinstance(ref, dict) and not ref.get("version"):
            floating.append(inst.get("id"))
    if floating:
        return {"name": "subassembly_refs_pinned", "status": "warn",
                "detail": "sub-assembly references are not version-pinned "
                          "(pinning is reserved for a later phase): "
                          + ", ".join(str(i) for i in floating),
                "instances": floating}
    return {"name": "subassembly_refs_pinned", "status": "pass",
            "detail": "no floating sub-assembly references"}


def _drawings_regenerable_check(service, project: str) -> dict:
    """A soft pass in v1: a real probe would run ``generate_drawing`` (a kernel
    call this zero-kernel path refuses). The release bundle regenerates drawings
    deterministically in slice 5; this check documents that intent."""
    manifest = service.store.manifest(project)
    parts = [p for p in (manifest.get("parts") or []) if isinstance(p, dict)]
    return {"name": "drawings_regenerable", "status": "pass",
            "detail": f"{len(parts)} part(s) present; drawings are regenerated "
                      "deterministically in the release bundle (soft check, no "
                      "kernel probe in v1)"}


# ----------------------------------------------------------------- the waiver


def _make_waiver(waive: dict | None) -> dict | None:
    """A durable, attributed waiver — or None. A silent override is impossible:
    a waiver is always this recorded object, attributed with
    ``locks.current_client_id()`` and stamped with the proposals ``ts``
    convention (the only wall-clock this module writes)."""
    if waive is None:
        return None
    if not isinstance(waive, dict):
        raise ValidationError("waive must be an object {reason}",
                              {"got": type(waive).__name__})
    reason = waive.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("a waiver needs a non-empty 'reason'",
                              {"waive": waive})
    actor = locks.current_client_id()
    return {"reason": reason, "principal": actor,
            "principal_kind": actor_kind(actor), "ts": _now()}


def _branches(service):
    branches = getattr(service, "branches", None)
    if branches is None:
        raise ValidationError("releases unavailable: git not found on PATH")
    return branches
