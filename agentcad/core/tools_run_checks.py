"""Tool pack: geometry CI — one command that certifies a project (PRD-004).

Installs the one seam the feature needs — ``service.checks``
(:class:`~agentcad.core.checks.CheckRunner`) — and exposes ``run_checks``. The
handler is a thin delegation; every rule lives in ``core/checks.py`` and every
*measurement* lives in the surfaces that runner drives (``_ensure_built``,
``_resolved_instances`` + ``check_interference``, ``SpecRunner.run``, the
drawing tools). This pack adds no measurement of its own.

**Why the file is called ``tools_run_checks.py`` and never ``tools_checks.py``.**
``tools._load_tool_packs`` walks ``pkgutil.iter_modules`` **alphabetically**. A
pack at ``c`` would load *before* ``tools_proposals`` (``p``) — which assigns
``service.gate_providers = []`` **unconditionally** — so slice 6's ``checks``
gate, appended from ``register()``, would be silently thrown away, with no
error and no warning. Named after the tool it registers, the pack sorts at
``r``: after ``tools_proposals`` (``service.proposals`` and
``service.gate_providers`` exist) and before ``tools_specs`` (``s``) and
``tools_versioning`` (``v``) — so **``service.specs`` and ``service.branches``
do not exist yet at registration time** and the runner reads them inside its
methods instead. ``tests/test_checks_api.py`` pins both halves.

**The pack does NOT self-disable without git**, unlike ``tools_proposals`` and
``tools_versioning``: a check is a property of the working tree, and only the
``ref`` argument needs history (a ``ref`` on a project without git is a
``validation_error`` naming git).
"""

from __future__ import annotations

from .checks import STAGES, CheckRunner
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}

#: The four row statuses and the one policy an agent must not have to discover
#: by catching an exception that never comes.
_STATUSES = (
    "A red check is DATA, never an error: a failing part, an interfering "
    "pair, a failing spec and a drawing that will not generate are all rows "
    "in the report, and the call returns normally. Only the harness raises "
    "(unknown project -> not_found_error, a ref without git -> "
    "validation_error). Each row reports one of four statuses: 'pass' and "
    "'fail' were MEASURED; 'skip' means the row could not be measured for a "
    "named structural reason (not_selected | budget_exceeded | mesh_only | "
    "fem_extra_missing | not_declared | no_instances | not_script) and ALWAYS "
    "carries that reason plus a hint; 'error' means the check itself broke, "
    "which means 'we do not know' and is not 'it is fine'."
)


def register(registry, service) -> None:
    # Always constructed, and constructed HERE so the CLI, the tool and the
    # route share one runner (one last-report cache, one publisher). Nothing
    # about `service.specs` / `service.branches` is captured — see the module
    # docstring; both are installed by packs that load after this one.
    service.checks = CheckRunner(service, registry)

    def run_checks(project: str, ref: str | None = None,
                   stages: list | None = None, strict: bool = False,
                   budget: float | None = None,
                   proposal: str | None = None) -> dict:
        report = service.checks.run(
            project, ref=ref,
            stages=tuple(stages) if stages else STAGES,
            strict=bool(strict), budget_s=budget)
        if proposal:
            # Accepted now so the argument does not change shape mid-plan; the
            # durable proposal copy and the `checks` gate are slice 6. Saying
            # so in the report itself is the honest half — a caller that asked
            # for a post must not have to infer that nothing was posted.
            report["warnings"].append(
                f"proposal {proposal!r}: posting a check report to a proposal "
                f"is not implemented yet; this report was not posted")
        return report

    registry.register(Tool(
        "run_checks",
        "Certify a whole project in one call: rebuild every part, re-resolve "
        "the assembly and look for interference, evaluate the declared design "
        "specs (all three tiers) and regenerate the drawings. This is exactly "
        "what the `agentcad check` command runs — the same CheckRunner over "
        "the same project, so the report is identical on both surfaces. It "
        "MEASURES NOTHING NEW: every number comes from the same rebuild, mate, "
        "interference, spec and drawing surfaces the other tools expose, so a "
        "row's 'error' is that tool's payload verbatim (the same "
        "details.traceback, details.line and Error Doctor details.hint "
        "update_part_script hands back). " + _STATUSES + " Returns {schema: 1, "
        "agentcad, project, source: {kind: worktree|branch|tag|commit, ref, "
        "sha, label, host_sha, dirty}, started, finished, duration_s, status: "
        "green|red|skip, complete, strict, strict_failures, exit_code, "
        "summary: {passed, failed, skipped, errors, total}, stages: [{name, "
        "status, reason, duration_s, summary, items: [{id, kind, subject, "
        "status, message, reason, hint, error, details}]}], requirements, "
        "warnings, errors, host}. The four stages are build, assembly, specs "
        "and drawings, ALWAYS all four in that order — an unselected one is "
        "skip/not_selected rather than absent, so you never have to guess "
        "whether a stage was green or never ran. exit_code is the verdict as "
        "one integer: 0 green, 1 red (the model is wrong — read the failing "
        "items), 2 harness (we could not produce a verdict; 'complete' is "
        "false and a budget cut the run short). 'strict' does not change a "
        "single row: it lists the skipped ids in strict_failures and lets the "
        "derived status and exit_code move, so a reader can always tell what "
        "was measured from what was demanded. 'ref' certifies a BRANCH, TAG "
        "or COMMIT instead of the working tree, materialized into a throwaway "
        "git worktree and measured through a second service, so your files "
        "and your .cache/ are byte-identical afterwards — at the price of a "
        "cold cache, which makes it much slower than checking the tree you "
        "are in. 'budget' is a soft deadline in seconds, read between items: "
        "the stages it does not reach are skip/budget_exceeded, complete "
        "becomes false and the exit code is 2, and one in-flight kernel call "
        "may overshoot it. 'proposal' is accepted but not yet implemented (the "
        "report is returned and a warning says it was not posted).",
        schema({"project": _PROJ,
                "ref": {"type": "string",
                        "description": "Branch, tag or commit to certify "
                                       "instead of the working tree"},
                "stages": {"type": "array",
                           "description": "Subset of "
                                          f"{', '.join(STAGES)} to run "
                                          "(omit for all four)"},
                "strict": {"type": "boolean",
                           "description": "Count every skipped row as a "
                                          "failure in the verdict"},
                "budget": {"type": "number",
                           "description": "Soft deadline in seconds"},
                "proposal": {"type": "string",
                             "description": "Proposal id to post the report "
                                            "to (not implemented yet)"}},
               ["project"]),
        run_checks,
    ))
