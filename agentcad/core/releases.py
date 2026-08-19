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
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

from . import locks
from .model import ConflictError, NotFoundError, ValidationError
from .proposals import _COUNTED_VERDICTS, _now, actor_kind

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
    rows = [_hydrate_bundle(service, project, copy.deepcopy(rec))
            for rev, rec in records.items()
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
    record = _hydrate_bundle(service, project, copy.deepcopy(record))
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


# ------------------------------------------------------------- the finalize


def _ensure_mutable(record: dict) -> None:
    """Refuse to mutate a finalized release record (FR12).

    ``released`` and ``superseded`` are terminal: the record is append-only, so
    any tool that would rewrite one raises ``ConflictError`` directing the
    caller to branch off the tag instead. A ``draft``/``in_review`` record is
    still live and passes through. This is the record-level immutability seam —
    the tag's *tree* is already immutable structurally (no write path can land
    on a tag; you can only ``branch_create(from_ref=tag)``)."""
    status = record.get("status")
    if status in ("released", "superseded"):
        rev = record.get("rev")
        raise ConflictError(
            f"release {rev} is {status}; a finalized release is immutable — "
            "branch off its tag (branch_create from_ref=release/<rev>) to "
            "evolve it",
            {"rev": rev, "status": status})


def _release_approvals(proposal: dict, policy: dict) -> list[dict]:
    """The approve reviews that count for this proposal, as
    ``[{principal, ts}]`` (principal = the reviewer's ``actor``, ts = the
    review's own stamp — no new wall-clock).

    Mirrors :meth:`ProposalManager._approvals_gate` exactly: the *latest*
    counted verdict per actor decides (a later ``request_changes`` retracts an
    earlier approve; a ``comment`` never does), and — unless the policy allows
    self-approval — the author's own approve does not count. Sorted by
    ``(ts, principal)`` so the recorded list is deterministic."""
    latest: dict[str, dict] = {}
    for review in proposal.get("reviews") or []:
        actor = review.get("actor")
        if actor and review.get("verdict") in _COUNTED_VERDICTS:
            latest[actor] = review
    self_approve = bool(policy.get("self_approve"))
    author = proposal.get("author")
    approvals = [
        {"principal": actor, "ts": review.get("ts")}
        for actor, review in latest.items()
        if review.get("verdict") == "approve"
        and (self_approve or actor != author)
    ]
    approvals.sort(key=lambda a: (a.get("ts") or "", a.get("principal") or ""))
    return approvals


def _prior_released_rev(releases: dict, rev: str) -> str | None:
    """The immediately-prior rev (``_inc_rev(prior) == rev``) if it exists and
    is ``released`` — the one this finalize supersedes. ``None`` for rev ``A``
    or when the predecessor was never released."""
    for name, record in (releases or {}).items():
        if _is_rev(name) and isinstance(record, dict) \
                and _inc_rev(name) == rev \
                and record.get("status") == "released":
            return name
    return None


def release_finalize(service, project: str, rev: str) -> dict:
    """Finalize an ``in_review`` release: tag it, register the referrer,
    transition the record to ``released`` and supersede the prior rev.

    **Idempotent.** A second call on an already-``released`` rev returns the
    same record and creates nothing. A ``draft`` (the gate never passed) is a
    ``ValidationError``; a ``superseded`` (or any other attempt to rewrite a
    finalized record) is a ``ConflictError`` (FR12).

    **Approval-gated.** The release proposal must be approved — the same
    approval rule the merge gate uses (:meth:`ProposalManager._approvals_gate`):
    at least one counted ``approve`` review that the policy accepts (the
    author's own does not count unless ``self_approve``). The approving
    principals are copied onto the record as ``approvals: [{principal, ts}]``.

    Zero kernel calls: this is git (``branches.tag``) + manifest only. The tag
    is ``release/<rev>`` **lower-cased** because a git ref is lowercase by the
    project's ``valid_ref_name`` rule — the record's ``tag`` field carries that
    exact, resolvable name.
    """
    branches = _branches(service)
    record = _releases_map(service, project).get(rev)
    if not isinstance(record, dict):
        raise NotFoundError(
            f"release {rev!r} not found in project {project!r}",
            {"project": project, "rev": rev})

    status = record.get("status")
    if status == "released":
        return _hydrate_bundle(service, project, copy.deepcopy(record))  # idempotent no-op
    _ensure_mutable(record)                    # superseded / terminal -> refuse
    if status != "in_review":
        raise ValidationError(
            f"release {rev} cannot be finalized from {status!r}: its gate has "
            "not passed (a red gate leaves the release draft — fix the failing "
            "checks or record a waiver, then cut it again)",
            {"project": project, "rev": rev, "status": status})

    # The release proposal must be approved.
    pid = record.get("proposal")
    proposal = service.proposals.store.load(project, pid)
    policy = service.proposals.store.policy(project)
    approvals = _release_approvals(proposal, policy)
    if not approvals:
        raise ConflictError(
            f"release proposal {pid} is not approved; a release is finalized "
            "only after its proposal is approved (proposal_review verdict "
            "'approve' by a reviewer other than the author)",
            {"project": project, "rev": rev, "proposal": pid})

    # The tag must be the APPROVED state, not whatever the branch drifted to
    # after approval — `branches.tag` auto-commits the working tree, so an
    # uncommitted edit or a new commit past the approved head would be tagged
    # (and immutable) while carrying the approval. Re-gate at finalize (review
    # MED-2): refuse a dirty tree, and refuse a head that moved past every
    # approved review's `source_head`.
    source = proposal.get("source")
    clean = _clean_tree_check(service, project, source)
    if clean.get("status") != "pass":
        raise ConflictError(
            f"the release branch {source!r} has uncommitted changes since "
            "approval; commit or discard them and re-approve before finalizing",
            {"project": project, "rev": rev, "gate": clean})
    self_approve = bool(policy.get("self_approve"))
    author = proposal.get("author")
    approved_heads = {
        r.get("source_head") for r in (proposal.get("reviews") or [])
        if r.get("verdict") == "approve" and (self_approve or r.get("actor") != author)
        and r.get("source_head")}
    head = service.history.head(service.store.path_of(project))
    if head and approved_heads and head not in approved_heads:
        raise ConflictError(
            f"the release branch {source!r} moved since approval (head "
            f"{head[:7]} was not the approved state); re-approve the proposal "
            "then finalize",
            {"project": project, "rev": rev, "head": head})

    # Create the immutable tag, then register the referrer. If a prior finalize
    # died AFTER tagging but BEFORE the record transitioned (the tag+RMW are not
    # one atomic op), the tag already exists — treat that as an idempotent RESUME
    # rather than a permanent wedge (there is no tag-delete tool), and keep the
    # already-created tag (which pinned the approved state) — review MED-1.
    tag_name = f"release/{rev.lower()}"
    try:
        branches.tag(project, tag_name, message=record.get("notes")
                     or record.get("name") or tag_name)
    except ConflictError:
        pass                                   # tag exists: resume the transition
    branches.add_referrer(project, tag_name, {"release": rev})

    # Transition the record (RMW the live branch manifest) and supersede the
    # immediately-prior released rev.
    manifest = service.store.manifest(project)
    releases = manifest.get("releases")
    releases = releases if isinstance(releases, dict) else {}
    current = releases.get(rev)
    if not isinstance(current, dict):
        raise NotFoundError(
            f"release {rev!r} not found in project {project!r}",
            {"project": project, "rev": rev})
    _ensure_mutable(current)                   # re-check under the RMW
    current["status"] = "released"
    current["tag"] = tag_name
    current["approvals"] = approvals
    prior = _prior_released_rev(releases, rev)
    if prior is not None:
        releases[prior]["status"] = "superseded"
    manifest["releases"] = releases
    service.store.save_manifest(project, manifest)

    # project_changed keeps the manifest write committed (clean tree); the
    # release_changed event is the UI/agent signal for the transition.
    service.bus.publish({"type": "project_changed", "project": project,
                         "reason": "release"})
    service.bus.publish({"type": "release_changed", "project": project,
                         "rev": rev, "status": "released"})

    # Build the reproducible bundle inline (FR10-11). Synchronous is fine for v1
    # (the design notes a background job is a PRD-020 concern). It is
    # best-effort: a bundle failure must NOT un-release an already-tagged
    # release — the tag and the transition are already durable — so we record
    # the failure on the record and return. ``release_bundle`` re-runs it
    # idempotently. ``build_bundle`` is referenced through the module global so
    # a test can stub it (the finalize regression suite does, to stay fast).
    try:
        build_bundle(service, project, rev)
    except Exception as exc:                                    # noqa: BLE001
        _persist_bundle(service, project, rev, {"error": str(exc)})
    # Re-read so the returned record carries the bundle the build just persisted.
    current = _releases_map(service, project).get(rev)
    return _hydrate_bundle(service, project, copy.deepcopy(current))


# --------------------------------------------------------------- the bundle


#: The ISO-10303-21 ``FILE_NAME`` header's second entry — the write timestamp.
#: (``DOTALL`` because the header may wrap; ``count=1`` — only the first.)
_STEP_FILE_NAME_TS = re.compile(
    rb"(FILE_NAME\s*\(\s*'[^']*'\s*,\s*)'[^']*'", re.DOTALL)

#: An OCCT assembly-session artifact: the ``NEXT_ASSEMBLY_USAGE_OCCURRENCE``
#: entity's first field is a PROCESS-GLOBAL monotonic counter, so two assembly
#: exports in one kernel process (the bundle shares its kernel across rebuilds)
#: carry different ids for byte-identical geometry. Not the shape — a counter.
_STEP_NAUO_ID = re.compile(
    rb"(NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(\s*)'[^']*'")


def _normalize_step_bytes(data: bytes) -> bytes:
    """A copy of *data* with STEP's two non-geometry, non-deterministic fields
    neutralized so two exports of the same shape at the same tag compare equal
    (FR11): the ``FILE_NAME`` write timestamp and the process-global
    ``NEXT_ASSEMBLY_USAGE_OCCURRENCE`` session counter (assemblies only).
    Everything else a STEP writer emits is deterministic for a fixed shape. The
    README names STEP as the one normalized-comparison artifact class."""
    data = _STEP_FILE_NAME_TS.sub(rb"\1'NORMALIZED'", data, count=1)
    data = _STEP_NAUO_ID.sub(rb"\1'NORMALIZED'", data)
    return data


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bundle_readme(record: dict, rev: str, tag: str, tag_date: str,
                   produced: list[Path], skipped: list[str]) -> str:
    """The bundle README (FR10) — deterministic: it carries the release name,
    notes, the gate report, any waiver and a sorted artifact list, all off the
    stable record + the tag's commit date, never the wall clock."""
    gate = record.get("gate") or {}
    lines = [f"# {record.get('name') or ('Release ' + rev)}", ""]
    lines.append(f"- Revision: {rev}")
    lines.append(f"- Tag: {tag}")
    lines.append(f"- Date (tag commit): {tag_date}")
    if record.get("notes"):
        lines += ["", "## Notes", "", str(record["notes"])]

    lines += ["", "## Gate", "", f"Status: {gate.get('status', 'unknown')}", ""]
    for check in gate.get("checks") or []:
        mark = " (waived)" if check.get("waived") else ""
        lines.append(f"- {check.get('name')}: {check.get('status')}{mark} — "
                     f"{check.get('detail', '')}")

    waiver = record.get("waiver")
    if isinstance(waiver, dict):
        lines += ["", "## Waiver", "",
                  f"- Reason: {waiver.get('reason')}",
                  f"- Principal: {waiver.get('principal')} "
                  f"({waiver.get('principal_kind')})",
                  f"- Recorded: {waiver.get('ts')}"]

    lines += ["", "## Artifacts", ""]
    for name in sorted(p.name for p in produced):
        cls = "step" if Path(name).suffix.lower() in (".step", ".stp") \
            else "deterministic"
        lines.append(f"- {name} ({cls})")

    if skipped:
        lines += ["", "## Skipped", ""]
        lines += [f"- {note}" for note in skipped]

    lines += [
        "", "## Reproducibility", "",
        "Rebuilding this bundle at tag `" + tag + "` yields byte-identical "
        "sha256 for every `deterministic`-class artifact (drawings, BOM, flat "
        "patterns, this README). STEP files (`*.step`) are the one "
        "normalized-comparison class: their ISO-10303-21 `FILE_NAME` header "
        "carries a write timestamp, and an assembly STEP additionally carries "
        "an OCCT process-global `NEXT_ASSEMBLY_USAGE_OCCURRENCE` session "
        "counter — neither is geometry — so STEP files are compared only after "
        "those two fields are normalized (see `artifacts.json` `class` and the "
        "release management docs).", ""]
    return "\n".join(lines)


def _zip_dir(src: Path, zip_path: Path) -> None:
    """Zip *src*'s files (sorted, flat) into *zip_path*, replacing any prior."""
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in sorted(src.iterdir(), key=lambda p: p.name):
            if child.is_file():
                zf.write(child, child.name)


def _hydrate_bundle(service, project: str, record: dict) -> dict:
    """Resolve a record's persisted project-relative bundle ``dir``/``zip``
    (review LOW-5) back to absolute paths for the caller. Mutates + returns the
    already-deepcopied ``record`` — the manifest keeps the portable relatives,
    the API hands back runtime-usable absolutes."""
    bundle = record.get("bundle") if isinstance(record, dict) else None
    if isinstance(bundle, dict):
        root = Path(service.store.path_of(project))
        for key in ("dir", "zip"):
            val = bundle.get(key)
            if isinstance(val, str) and val and not Path(val).is_absolute():
                bundle[key] = str(root / val)
    return record


def _persist_bundle(service, project: str, rev: str, bundle: dict) -> None:
    """RMW the manifest to stamp ``releases[rev].bundle``. This writes
    generation-owned output onto the record (the bundle pointer), so it does NOT
    go through ``_ensure_mutable`` — a released record is append-only for its
    *state*, and the bundle is that release's own produced artifact index, added
    once and refreshable by an idempotent rebuild."""
    manifest = service.store.manifest(project)
    releases = manifest.get("releases")
    releases = releases if isinstance(releases, dict) else {}
    record = releases.get(rev)
    if isinstance(record, dict):
        # Store PROJECT-RELATIVE paths in the committed manifest (review LOW-5);
        # the caller keeps the absolute summary it was handed.
        root = Path(service.store.path_of(project)).resolve()

        def _rel(value):
            try:
                return Path(str(value)).resolve().relative_to(root).as_posix()
            except (ValueError, OSError):
                return value
        persisted = dict(bundle)
        for key in ("dir", "zip"):
            if key in persisted:
                persisted[key] = _rel(persisted[key])
        record["bundle"] = persisted
        manifest["releases"] = releases
        service.store.save_manifest(project, manifest)
        # Snapshot the bundle stamp (and any exports the build wrote) so the
        # working tree is left CLEAN — otherwise the next release's
        # working_tree_clean gate would see the uncommitted manifest and go red.
        service.bus.publish({"type": "project_changed", "project": project,
                             "reason": "release"})


def build_bundle(service, project: str, rev: str) -> dict:
    """Produce the reproducible release bundle (FR10-11) for *project* rev *rev*
    at its tag ``release/<rev-lower>`` and copy it into
    ``exports/releases/<rev>/`` (with a ``<rev>.zip`` beside it).

    The whole build runs against an **ephemeral service materialized at the
    tag** (its tree IS the tagged state; its cache is cold, so every export
    builds for real — this is where the per-tag geometry and mass come from).
    The produced files are copied OUT of the throwaway worktree BEFORE it is
    torn down. Idempotent: a rebuild overwrites the directory. Returns the
    bundle summary and persists it onto the record.

    Zero OCP here — the geometry is done by the ephemeral service's kernel
    through the export tools; this module stays pure Python.
    """
    record = _releases_map(service, project).get(rev)
    if not isinstance(record, dict):
        raise NotFoundError(
            f"release {rev!r} not found in project {project!r}",
            {"project": project, "rev": rev})
    tag = record.get("tag") or f"release/{rev.lower()}"
    canonical = Path(service.store.canonical_path_of(project)).resolve()

    # The tag's commit date pins the drawing title block (byte-stable, no clock).
    tag_date = "-"
    for entry in service.history.tags(canonical):
        if entry.get("name") == tag:
            tag_date = (str(entry.get("ts") or "")[:10]) or "-"
            break

    from ._worktree import materialized_service

    produced: list[Path] = []
    skipped: list[str] = []

    def _keep(path_like) -> None:
        if path_like is None:
            return
        p = Path(path_like)
        if p.is_file():
            produced.append(p.resolve())

    with materialized_service(service, project, tag) as (eph, registry, name):
        exports = Path(eph.store.exports_dir(name)).resolve()
        manifest = eph.store.manifest(name)
        parts = [p for p in (manifest.get("parts") or []) if isinstance(p, dict)]
        script_parts = [p for p in parts if p.get("kind") != "reference"]

        # STEP per part (this also warms the ephemeral build cache, so the BOM
        # below reads real, built masses rather than `unbuilt`).
        for part in script_parts:
            pid = part.get("id")
            try:
                res = eph.export_part(name, pid, "step")
                _keep(res.get("path") or (exports / f"{pid}.step"))
            except Exception as exc:                            # noqa: BLE001
                skipped.append(f"{pid}: STEP export failed ({exc})")

        # STEP for the whole assembly (an assembly with no instances raises —
        # skip it gracefully rather than fail the bundle).
        try:
            res = eph.export_assembly(name, "step")
            _keep(res.get("path") or (exports / "assembly.step"))
        except Exception as exc:                                # noqa: BLE001
            skipped.append(f"assembly: STEP skipped ({exc})")

        # Drawings (pdf + svg) per script part, title block PINNED at the tag
        # via the PRD-014 version override so they are byte-stable (FR11).
        for part in script_parts:
            pid = part.get("id")
            for fmt in ("pdf", "svg"):
                out = registry.call("generate_drawing", {
                    "project": name, "part_id": pid, "format": fmt,
                    "version": {"ref": rev, "date": tag_date}})
                if "error" in out:
                    skipped.append(
                        f"{pid}: {fmt} drawing skipped "
                        f"({out['error'].get('message')})")
                    continue
                _keep(out.get("path") or (exports / f"{pid}_drawing.{fmt}"))

        # Flat patterns for sheet-metal parts; a solid errors/no-ops on
        # flat_pattern(p) — catch and skip, noting which.
        for part in script_parts:
            pid = part.get("id")
            out = registry.call("flat_pattern", {
                "project": name, "part_id": pid, "format": "svg"})
            if "error" in out:
                skipped.append(f"{pid}: flat pattern skipped (not sheet metal)")
                continue
            _keep(out.get("path") or (exports / f"{pid}_flat.svg"))

        # BOM (csv + json) at the tag — after the builds above, so masses are
        # real.
        for fmt in ("csv", "json"):
            out = registry.call("export_bom", {"project": name, "format": fmt})
            if "error" in out:
                skipped.append(f"bom.{fmt} skipped ({out['error'].get('message')})")
                continue
            _keep(out.get("path") or (exports / f"bom.{fmt}"))

        # De-duplicate, preserving order.
        seen: set[Path] = set()
        unique: list[Path] = []
        for p in produced:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        produced = unique

        # README (deterministic) — written into the bundle set, then hashed.
        readme_path = exports / "README.md"
        readme_path.write_text(
            _bundle_readme(record, rev, tag, tag_date, produced, skipped),
            encoding="utf-8")
        produced.append(readme_path.resolve())

        # Classify + hash every produced file. artifacts.json never lists
        # itself; STEP is the `step` class, everything else `deterministic`.
        files = []
        for p in sorted(produced, key=lambda x: x.name):
            data = p.read_bytes()
            cls = "step" if p.suffix.lower() in (".step", ".stp") \
                else "deterministic"
            files.append({"path": p.name, "sha256": _sha256_bytes(data),
                          "bytes": len(data), "class": cls})
        artifacts = {
            "rev": rev, "tag": tag, "generated": tag_date, "files": files,
            "classes": {
                "deterministic": "byte-identical across rebuilds at this tag",
                "step": "compared after FILE_NAME timestamp normalization",
            },
        }
        art_path = exports / "artifacts.json"
        art_path.write_text(json.dumps(artifacts, indent=2, sort_keys=True),
                            encoding="utf-8")

        # Copy OUT into the real project's exports/releases/<rev>/ BEFORE
        # teardown removes the worktree.
        dest = Path(service.store.exports_dir(project)).resolve() \
            / "releases" / rev
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for p in produced:
            shutil.copy2(p, dest / p.name)
        shutil.copy2(art_path, dest / "artifacts.json")

    # The worktree is gone; the real dest survives. Zip it beside the directory.
    zip_path = dest.parent / f"{rev}.zip"
    _zip_dir(dest, zip_path)

    # The RETURNED summary carries absolute paths (convenient for the immediate
    # caller); `_persist_bundle` stores PROJECT-RELATIVE paths into the manifest
    # (review LOW-5 — an absolute, machine-specific path in a git-tracked
    # manifest is non-portable and invites cross-machine merge churn).
    summary = {
        "dir": str(dest),
        "zip": str(zip_path),
        "artifacts": [f["path"] for f in files],
        "generated": tag_date,
        "skipped": skipped,
    }
    _persist_bundle(service, project, rev, summary)
    return summary


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
