# 0102 — 2026-08-11 — PRD-004 slice 5: the `run_checks` tool, its routes and `check_finished`

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

Fifth slice of PRD-004 (geometry CI): the pipeline slices 1–4 built becomes
reachable by an agent and by the browser (FR12, AC9). A tool pack registers
`run_checks` and installs `service.checks`, a route pack exposes
`POST/GET /api/projects/{proj}/checks`, and every completed run — from the CLI,
the tool or the route — publishes one `check_finished` event and leaves its
report in a small in-memory per-project cache. No measurement moved and no core
file was edited.

## Changes

- `agentcad/core/tools_run_checks.py` (new pack):
  - `register(registry, service)` installs `service.checks =
    CheckRunner(service, registry)` — constructed *here* so that the CLI (which
    prefers `getattr(service, "checks", None)`, slice 4), the tool and the
    route share **one** runner, and therefore one last-report cache and one
    publisher.
  - `run_checks {project, ref?, stages?, strict?, budget?, proposal?}` → the
    full `schema: 1` report. `stages` is an array validated against `STAGES` by
    the runner (an unknown name is a `validation_error` naming it), `budget`
    maps to `budget_s`. `proposal` is accepted and **not** acted on until slice
    6; asking for it appends a `warnings[]` entry to the returned report saying
    the report was not posted, so a caller never has to infer that from
    silence.
  - The description states the four row statuses, the skip reasons, the exit
    codes, that all four stages always appear (an unselected one is
    `skip/not_selected`), and that a red check is **data** — the call returns
    normally and only the harness raises.
  - The module docstring records the naming rule: the pack is **not**
    `tools_checks.py`, because `tools._load_tool_packs` walks
    `pkgutil.iter_modules` alphabetically and `tools_proposals.register`
    assigns `service.gate_providers = []` unconditionally, so a pack at `c`
    would have slice 6's gate silently discarded. At `r` it loads after
    `tools_proposals` and before `tools_specs`/`tools_versioning`, so
    `service.specs` and `service.branches` are read lazily inside the runner's
    methods rather than captured at registration.
- `agentcad/server/routes_checks.py` (new pack):
  - `POST /projects/{proj}/checks` — body whitelisted to
    `{ref, stages, strict, budget, proposal}` (never `**body`, `null` means
    "omitted"), forwarded through the registry.
  - `GET /projects/{proj}/checks` — the last report this process produced for
    the project, `404` when there is none.
  - `_BODY_ERRORS` is empty: nothing a check *measured* is an HTTP error, so a
    red project is an ordinary `200`; only "no verdict at all" (unknown
    project → 404, unknown stage or a `ref` without git → 422) maps to an error
    status, via the app's normal `NotFoundError`/`ValidationError` handlers.
- `agentcad/core/checks.py` (the two additions the plan places here, so every
  surface behaves identically):
  - `CheckRunner.run` now ends with `_remember(proj, report)` and
    `_publish(proj, ref, report)`.
  - `check_finished` = `{type, project, ref, status, exit_code, summary,
    duration_s}` on `service.bus` — for **every** completed run including a red
    one and a budget-truncated one, best effort (a dropped event must not lose
    a report). It is not `project_changed`, so `_snapshot_on_event` ignores it:
    measuring a project is not changing it.
  - `CheckRunner.last` — the last report per project, bounded to the new
    `LAST_REPORTS = 8`, insertion-ordered so the eviction is genuinely the
    least recently produced. `last_report(proj)` raises `NotFoundError` rather
    than returning `None`, because "no check has run here" is a 404 and is not
    the same answer as a green report. In memory only; the durable copy of a
    report belongs to a proposal (slice 6).

## Files

- `agentcad/core/tools_run_checks.py` — new tool pack (`run_checks`,
  `service.checks`)
- `agentcad/server/routes_checks.py` — new route pack (POST/GET checks)
- `agentcad/core/checks.py` — `LAST_REPORTS`, `CheckRunner.last`,
  `_remember`, `last_report`, `_publish`, and the two calls at the end of
  `run()`
- `tests/test_checks_api.py` — new: registration, load order, the run seam and
  the routes
- `docs/changelog/0102-run-checks-tool-and-routes.md` — this entry

## Notes

- **The load-order claim is pinned twice, empirically.** The ordinal half:
  `pkgutil.iter_modules` over `agentcad/core` lists `tools_proposals` at index
  10 and `tools_run_checks` at 11, with `tools_specs` at 14 — while
  `bisect.bisect(packs, "tools_checks")` is **1**, i.e. before
  `tools_proposals`. The destructive half: a sentinel provider appended to
  `service.gate_providers` before `build_registry` is *gone* afterwards, which
  is exactly what would happen to slice 6's gate from a `c`-named pack. The
  test also asserts no module named `tools_checks` exists.
- AC9 ("the same report everywhere") is asserted by running the real CLI in a
  subprocess and the tool in-process over the same project and comparing the
  reports after normalizing `started`, `finished`, `host` and every
  `duration_s`. The cache is warmed first, so `details.cached` records the
  project's state rather than which run happened to be first.
- Deviation from the plan's Task 3, in the codebase's favour: the route
  *drops* unknown body keys (`_body_keys`, the `routes_specs`/`routes_proposals`
  convention) rather than rejecting them, and the **registry** is what refuses
  an unknown argument. Both layers are tested.
- The pack does not self-disable without git (the `tools_specs` precedent): a
  check is a property of the working tree, and only `ref` needs history. With
  no git there are no proposals either, so `service.gate_providers` is absent —
  which slice 6's `install_checks_gate` must tolerate rather than assume.
