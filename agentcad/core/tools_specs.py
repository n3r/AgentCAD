"""Tool pack: executable design specs — intent the kernel can check (PRD-003).

Installs the one seam the feature needs — ``service.specs``
(:class:`~agentcad.core.specs.SpecRunner`) — exposes four tools, and wires the
rebuild seam by **wrapping** two bound service methods. Handlers are thin
delegations; every rule lives in ``core/specs.py`` and every measurement in
``kernel/handlers/specs.py``.

**The pack does NOT self-disable without git**, unlike ``tools_proposals`` and
``tools_versioning``. Specs are a property of the working tree: ``run_specs``
and ``list_specs`` work on a project with no history at all, and only the
``ref`` argument (and, from slice 5, the proposal gate) needs branches — a
``ref`` on a project without git is a ``validation_error`` naming git.

**Load order.** ``tools._load_tool_packs`` walks ``pkgutil.iter_modules``
alphabetically, so this module is imported *after* ``tools_proposals``
(``service.gate_providers`` exists) and *before* ``tools_stackup`` and
``tools_versioning`` (``tolerance_stackup``, ``service.branches`` and
``service.merges`` do **not** exist yet). The runner therefore reads
``service.branches`` inside each call and calls ``tools_stackup``'s extracted
``compute_stackup`` directly rather than through the registry — a check must
not depend on another pack's registration order.
"""

from __future__ import annotations

import functools

from .specs import SpecRunner
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}
_PART = {"type": "string",
         "description": "Restrict to one part (omit for the whole project)"}

#: The four statuses are the whole contract, so every description carries them.
_STATUSES = (
    "A failing spec is DATA, never an error: a failing spec never fails a "
    "rebuild — the geometry lands, ok stays true, and the failure is signal. "
    "Each check reports one of four statuses. 'pass' and 'fail' were MEASURED "
    "and carry measured, limit (a dict, e.g. {min_mm: 2.5}), unit and a "
    "message; wall and clearance also carry a world 'location'. 'skip' means "
    "the check could not be measured for a named structural reason "
    "(fem_extra_missing | mesh_only | deferred | unsupported_scope | "
    "no_instances) and ALWAYS carries that reason plus a hint — a skip is "
    "data, "
    "not a failure. 'error' means the check itself broke (a predicate raised, "
    "an instance id no longer exists, the kernel failed mid-measurement); it "
    "means 'we do not know', which is not 'it is fine'. Requirement strings "
    "are opaque — an id or a URL — stored and grouped, never resolved."
)

# The wrapper marker. An attribute rather than a flag on the service, so the
# question "is this method already wrapped?" is answered by the method itself.
_WRAPPED = "_agentcad_specs_wrapper"


def _summary(service, proj: str, part_id: str, build_result: dict | None):
    """The tier-1 summary for one part, or an error record — never a raise.

    ``service.specs`` is resolved at CALL time, not captured: building the
    registry twice over one service mints a new runner, and a wrapper holding
    the old one would evaluate against a stale declaration cache.
    """
    runner = getattr(service, "specs", None)
    if runner is None:
        return None
    try:
        return runner.tier1(proj, part_id, build_result)
    except Exception as exc:  # noqa: BLE001 — a broken spec layer must never
        payload = exc.to_payload() if hasattr(exc, "to_payload") else {
            "type": type(exc).__name__, "message": str(exc),
            "details": dict(getattr(exc, "details", None) or {})}
        return {"status": "error", "error": payload}   # break a rebuild (D5)


def install_rebuild_specs(service) -> None:
    """Attach the spec summary to every rebuild result and to ``get_part``.

    **Why a wrapper and not a ``service.py`` edit.** The extension-point
    contract forbids editing the service core to add a feature; the
    ``tools_versioning.install_write_guard`` seam and ``tools_proposals``'s
    ``branch_delete`` guard are the precedent for rewiring a bound method from
    a pack instead.

    **Why ``_rebuild`` and not the three rebuild-returning tools.**
    ``update_part``, ``set_params`` and ``set_solid_materials`` all end in
    ``self._rebuild(...)``, ``_ensure_built`` calls it on a miss — and the
    browser's ``PATCH /api/projects/{p}/parts/{id}/params`` route calls
    ``service.set_params`` **directly**, not through the registry, so wrapping
    the tools would miss the UI entirely.

    Idempotent: wrapping twice would evaluate the shape tier twice on every
    rebuild. ``_rebuild`` is private, so three tests pin this seam — installed
    exactly once, the success payload's key set unchanged apart from ``specs``,
    and a spec-less part identical to its pre-feature payload and kernel-call
    count.
    """
    rebuild = service._rebuild
    if not getattr(rebuild, _WRAPPED, False):

        @functools.wraps(rebuild)
        def _rebuild(proj: str, part_id: str) -> dict:
            result = rebuild(proj, part_id)
            # On a FAILED rebuild the key is absent: there is no geometry to
            # assert over, and a spec block beside a build failure would
            # compete with with_hint's "fix the script first". ``None`` means
            # "declares none" — which is not "not evaluated".
            if isinstance(result, dict) and result.get("ok"):
                result["specs"] = _summary(service, proj, part_id, result)
            return result

        setattr(_rebuild, _WRAPPED, True)
        service._rebuild = _rebuild

    get_part = service.get_part
    if not getattr(get_part, _WRAPPED, False):

        @functools.wraps(get_part)
        def _get_part(proj: str, part_id: str) -> dict:
            detail = get_part(proj, part_id)
            if not isinstance(detail, dict):
                return detail
            # A part that does not build carries NO specs key, exactly as a
            # failed rebuild does: there is no shape to assert over, the build
            # error is already the message to act on, and evaluating would pay
            # for that failing build again on every read.
            state = (detail.get("status") or {}).get("state")
            if state != "error":
                # Otherwise from the result cache: ``_ensure_built`` has
                # already run, so a built part costs one cache-key computation
                # and a disk read. This is what makes the inspector chips
                # live — main.js's rebuild_finished handler already refetches
                # the part.
                detail["specs"] = _summary(service, proj, part_id, None)
            return detail

        setattr(_get_part, _WRAPPED, True)
        service.get_part = _get_part


def install_specs_gate(service) -> None:
    """Append the fail-closed ``specs`` gate to PRD-002's provider list.

    The whole of PRD-003's enforcement surface is this one ``append``:
    ``proposals.py`` is finished and reviewed, and a provider returning
    ``state: "fail"`` is already a hard block that ``allow_invalid`` cannot
    waive. Reverting the feature's enforcement is deleting this call.

    ``service.gate_providers`` is absent when git is (``tools_proposals``
    self-disables), which is not an error here: specs still work on a project
    with no history, there is simply nothing to gate.

    Idempotent by name. ``build_registry`` may run twice over one service — the
    versioning pack's ``install_write_guard`` precedent — and two providers
    named ``specs`` would evaluate the gate twice and then have one silently
    overwrite the other in ``ProposalManager.gates``.
    """
    providers = getattr(service, "gate_providers", None)
    if providers is None:
        return
    providers[:] = [p for p in providers
                    if getattr(p, "__name__", None) != "specs"]
    providers.append(service.specs.gate_provider())


def register(registry, service) -> None:
    # Always constructed: specs need no git, and the runner reads every seam it
    # does not own (branches, history) inside its methods.
    service.specs = SpecRunner(service)
    install_rebuild_specs(service)
    install_specs_gate(service)

    def run_specs(project: str, part_id: str | None = None,
                  ref: str | None = None) -> dict:
        return service.specs.run(project, part_id, ref)

    def list_specs(project: str, part_id: str | None = None) -> dict:
        return service.specs.declarations(project, part_id)

    def get_project_specs(project: str) -> dict:
        return service.specs.read_project_specs(project)

    def set_project_specs(project: str, script: str) -> dict:
        return service.specs.write_project_specs(project, script)

    registry.register(Tool(
        "run_specs",
        "Evaluate a project's declared design specs and report. Specs are "
        "code in the tree — a SPECS list in each part script (part scope) and "
        "in a root specs.py (project scope), built from "
        "agentcad.toolkit.specs's check_* constructors. Evaluation is tiered: "
        "a REBUILD evaluates the SHAPE TIER only (valid, mass, volume, bbox, "
        "wall, that), while run_specs evaluates ALL THREE TIERS — the shape "
        "tier, the assembly tier (interference_free, clearance, stackup) and "
        "the expensive tier (fem_static). A check the tier boundary defers is "
        "reported as skip/deferred on a rebuild, never dropped. " + _STATUSES +
        " Returns {project, ref, generated, status: green|red|skip, summary: "
        "{passed, failed, skipped, errors, total}, checks: [<every record, "
        "flat, id '<part>:<name>' or 'project:<name>'>], parts: {<id>: "
        "{status, summary, cached, checks: [id]}}, project_checks, "
        "requirements: {<requirement>: {status, checks: [id]}}, declared, "
        "warnings, errors}. status is red when anything failed or errored, "
        "green when nothing did (skips are allowed and named), skip when "
        "nothing was declared at all. Results are cached under the same "
        "content hash as the mesh, so re-running after no change costs no "
        "kernel work — FAILURES are cached under that key too (a SPECS that "
        "will not declare, a script that will not build, a predicate that "
        "hangs), and run_specs is the surface that ignores a cached failure "
        "and measures again. 'ref' evaluates another BRANCH's state without "
        "switching yours (a tag is a validation_error — a tag must never "
        "answer for a branch; a ref on a project with no git is a "
        "validation_error naming git). A proposal's 'specs' GATE is this same "
        "evaluation over its source branch, and it is FAIL-CLOSED: a red gate "
        "blocks proposal_merge, and so does a declared check that could not "
        "be evaluated at all — allow_invalid does NOT waive it (that flag is "
        "about the kernel's verdict on geometry and nothing else). The gate "
        "diverges from this report in three ways, all fail-closed: EVERY skip "
        "is a FAIL there whatever its reason (fem_extra_missing, mesh_only, "
        "unsupported_scope, no_instances) because declared-but-not-measured "
        "must not pass a merge; a specs.py that will not read or declare is a "
        "red 'declaration' row named after the file; and the gate runs under a "
        "30 s deadline whose exhaustion is red and is remembered for that "
        "head. It never answers 'pending' — a source head that moves during "
        "evaluation is a fail saying to retry. Run this tool on the source "
        "branch to see, "
        "and to fix, what the gate is red about — it is unbounded, it "
        "re-measures cached failures, and it clears a remembered "
        "budget_exceeded verdict.",
        schema({"project": _PROJ, "part_id": _PART,
                "ref": {"type": "string",
                        "description": "Branch to evaluate instead of yours"}},
               ["project"]),
        run_specs,
    ))
    registry.register(Tool(
        "list_specs",
        "List every declared spec with NO evaluation and no build — this "
        "works on a project whose parts have never been built, and on a part "
        "whose script does not build at all. Returns {project, declared, "
        "parts: {<id>: {specs: [<declaration>]}}, project_specs: {path, "
        "exists, specs}, requirements: {<requirement>: [id]}, errors, "
        "warnings}. A declaration is the constructor's dict — {spec, kind, "
        "scope, name, limit, requirement, options} — with a check_that "
        "predicate reported as 'predicate': true (the callable never leaves "
        "the kernel worker). A file that will not execute is an errors[] "
        "entry, so one broken specs.py never hides the part specs. Use "
        "run_specs to measure them.",
        schema({"project": _PROJ, "part_id": _PART}, ["project"]),
        list_specs,
    ))
    registry.register(Tool(
        "get_project_specs",
        "Read the project-scope spec file, specs.py, and its declarations: "
        "{path, exists, script, declared, specs, declaration_error, "
        "warnings}. A project with no specs.py answers {script: null, specs: "
        "[]} — not a 404. 'declaration_error' is the script error when the "
        "file will not execute (it is reported, not raised, so you can read a "
        "broken file in order to fix it). specs.py holds the "
        "checks that span parts (check_interference_free, check_clearance, "
        "check_stackup); per-part checks belong in the part script's SPECS.",
        schema({"project": _PROJ}, ["project"]),
        get_project_specs,
    ))
    registry.register(Tool(
        "set_project_specs",
        "Write the project-scope spec file, specs.py, and return its "
        "POST-STATE: {path, exists, script, declared, specs, "
        "declaration_error, warnings}. The script is a plain module with a "
        "module-level SPECS "
        "list built from agentcad.toolkit.specs (check_interference_free, "
        "check_clearance, check_stackup); a constructor validates its "
        "arguments eagerly, so a bad limit is a script_error with a line "
        "number. The file is written UNCONDITIONALLY and reported afterwards "
        "— a broken script is saved and its error returned, because you must "
        "be able to save one in order to fix it (the update_part_script "
        "rule). An empty script deletes the file. specs.py is tracked by git "
        "like every other file in the project, so it branches, merges, "
        "restores and undoes for free; the write is refused with a "
        "conflict_error while another client holds the turn.",
        schema({"project": _PROJ,
                "script": {"type": "string",
                           "description": "Full specs.py text "
                                          "('' deletes the file)"}},
               ["project", "script"]),
        set_project_specs,
    ))
