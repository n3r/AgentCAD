# 0235 — PRD-006 slice 4: the usage meter, health's honest object, per-project disk budgets, and the server work root

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Claude

## Summary

The service side of PRD-006's metering and quotas (design Decisions 6, 8 and
10). Every kernel response has carried its own cost since slice 3; this adds
the **meter** that rolls those records up per project and per client identity,
the **scope** that says which project a kernel call belongs to, the two
surfaces that publish it (`/api/health`'s `usage`, and a new `get_usage` tool),
and the **per-project disk budget** with a cache janitor. `/api/health`'s
`sandbox` field stops being a bare string and becomes the measured per-facet
object. Three carry-forwards from earlier reviews land with it: the workers no
longer get the shared temp dir as a writable root (they get one server-owned
work root instead), `agentcad serve` maps a bad quota to `error: …` + exit 2
and cleans up when `build_registry`/`create_app` fail, and a hosted instance
that is not confining its workers says so loudly at startup.

## Changes

**Metering (`agentcad/core/usage.py`, new)**

- `scope_var` (a `ContextVar`, beside `locks.client_id_var`) and `scoped()`,
  which is a token reset so a nested scope restores the caller's.
- `project_from_path()` — `^/api/projects/([^/]+)`, URL-decoded.
  `/api/projects/open` maps to `None`: it registers a directory as a project,
  so a usage row named `open` would be a lie.
- `UsageMeter` — thread-safe roll-ups keyed by `(project, identity)`
  (`requests`, `errors`, `cpu_ms`, `wall_ms`, `peak_rss_mb` as a **max**,
  `last_at`) plus a bounded ring of the individual records, which is what makes
  `since` answerable. `record()` never raises for a shape it did not expect and
  takes a plain lock around a dict update — it never touches the pool.
  `cpu_ms: None` (the client's answer for a request that never came back) is
  **skipped**, never summed as zero. `totals()`, `by_project()`,
  `by_identity()`, `health()`, `snapshot()`; a `since` older than the retained
  ring is answered with a `warnings` entry rather than a silent under-report.
- The scope is set in three additive places: `app.py`'s middleware (both the
  hosted and the local branch — before the guard's early return),
  `ToolRegistry.call` (from `args["project"]`), and the service's own
  build / export / assembly-export / interference paths (authoritative).

**Surfaces**

- `agentcad/core/tools_usage.py` (new pack): `get_usage {project?, since?}`.
  Reads `service.usage` at call time and answers an **empty** snapshot when
  nothing installed a meter, rather than an AttributeError 500.
- `/api/health`: `"sandbox": sandbox.report(service.kernel)` — the object, whose
  top-level `status` still means the confinement's — and `"usage":
  service.usage.health()` when a meter exists (the key is absent, not empty,
  when there is none). The anonymous hosted body is unchanged.

**Disk budgets (`core/project.py`, `core/model.py`, `core/service.py`)**

- `DiskBudgetError(AppError)` with `details {project, used_mb, budget_mb}`,
  mapped to **HTTP 507** (the request is well formed and the caller is allowed;
  there is no room).
- `ProjectStore(root, disk_budget_mb=None)`, `disk_usage()` (memoized 5 s per
  project *and working tree*; creates no directories), `invalidate_disk_usage()`,
  `assert_disk_budget()` and `trim_cache(proj, keep_keys)`.
- The asserts sit **before the worker writes**: `_build_with` (after the cache
  hit — existing geometry must stay readable in a full project), `export_part`,
  `export_assembly`, and `imports_dir(write=True)` (after the write guard: who
  may write is a different question from whether there is room).
- The janitor runs after a successful build, trimming `.acm` / `.faces.u32` /
  `.lod1.acm` files oldest-first until the cache is back under 75 % of the
  budget. `service._referenced_cache_keys(proj)` collects the live keys from
  `_status`/`_config_status`, and `_build_with` unions in the key it just
  minted — a pure-configuration build writes no `_status`, so without that it
  would sweep away its own mesh. `.metrics.json` is never trimmed.

**CLI (`agentcad/cli.py`)**

- `_writable_roots` no longer grants `tempfile.gettempdir()` (Decision 1).
- `_build_service` resolves quotas **first** (a refused start leaves nothing
  behind), then creates one server-wide `agentcad-work-*` root, grants it,
  and exposes it as `service.work_root`; installs `UsageMeter` as the kernel's
  `on_usage` and as `service.usage`; sets `store.disk_budget_mb` from
  `quotas.disk_mb`. `_remove_work_root` / `_release_work_root` are the
  cleanup, called from every command's existing `finally` (`serve`, `export`,
  `check`, `package validate`, `publish`) and from both failure paths inside
  `_build_service`.
- `cmd_serve`: a `ValueError` out of `quotas.resolve()` is `error: …` + exit 2;
  `build_registry`/`create_app` moved **inside** the try, so a failure there
  stops the kernel and releases the work root; `_warn_if_unconfined` prints one
  `WARNING:` line to stderr when a hosted instance's confinement is not active
  — never fatal (the deploy-smoke job must keep booting).

**Work cells (`core/checks.py`, `core/packages/gate.py`, `.../from_step.py`)**

- `checks.default_work_root(service)` — the granted work root when the runner's
  service has one, else `None` (`mkdtemp`'s own default). Used by
  `CheckRunner.run`, `CheckRunner._determinism`, `PackageGate._work_root` and
  `from_step._measure` for the **default** cell only. An explicit `--work-dir`
  and the "never delete a directory it did not create" contract are unchanged:
  this only chooses the parent a run's own `mkdtemp` cell is created under.

## Files

- `agentcad/core/usage.py` — new: scope ContextVar, `scoped`,
  `project_from_path`, `UsageMeter`
- `agentcad/core/tools_usage.py` — new: the `get_usage` pack
- `agentcad/core/model.py` — `DiskBudgetError`
- `agentcad/core/project.py` — `disk_budget_mb`, `disk_usage`,
  `invalidate_disk_usage`, `assert_disk_budget`, `trim_cache`, `_budget_dirs`,
  `_dir_bytes`; the budget assert in `imports_dir(write=True)`
- `agentcad/core/service.py` — `self.usage`; budget asserts and usage scopes on
  the build/export/assembly-export/interference paths; the janitor call and
  `_referenced_cache_keys`
- `agentcad/core/tools.py` — `call()` scopes the handler by `args["project"]`
- `agentcad/server/app.py` — middleware scope, health's `sandbox` object and
  `usage`, `DiskBudgetError → 507`
- `agentcad/cli.py` — work root, meter, disk budget, `_release_work_root`,
  `cmd_serve` robustness, `_warn_if_unconfined`
- `agentcad/core/checks.py` — `default_work_root` + the two default cells
- `agentcad/core/packages/gate.py`, `agentcad/core/packages/from_step.py` —
  the same default
- `tests/test_usage.py`, `tests/test_disk_budget.py` — new
- `tests/test_server.py`, `tests/test_sandbox.py` — health's `sandbox` is an
  object now
- `tests/test_sandbox_plan.py` — the three `cmd_serve` carry-forwards
- `tests/test_checks_cli.py` — the granted roots exclude bare temp and include
  the work root; the work root is released; a named `--work-dir` is unchanged
- `tests/test_checks_ref.py`, `tests/test_packages_gate.py` — the default cell
  lands under the work root, and a run there still deletes only what it made
- `docs/agent-api.md` — a `get_usage` section (and the disk-budget refusal)
- `docs/architecture.md` — the writable roots, health's object, disk budgets
- `docs/deployment.md` — disk budget + the usage/health surface in Sizing
- `docs/geometry-ci.md` — the default `--work-dir` wording

## Notes

- **The meter is not a quota.** Nothing in `core/usage.py` refuses anything;
  what refuses is the kernel's quota tiers and the store's disk budget. The
  docs say so in those words, because a dashboard read as an enforcement point
  is how an operator ends up believing a cap exists that does not.
- **Honesty about the window.** The ring is bounded (2000 records) and the
  roll-ups are per process and in memory: a restart starts from zero, and a
  `since` older than the ring is reported as incomplete rather than answered
  short. The durable per-principal audit log is still PRD-005's.
- **A disk refusal after a landed write is a post-state**, not a 4xx:
  `set_params` and friends go through `service.rebuild_after_write`, which
  turns the pre-build `AppError` into `{"ok": false, "error": …}` — the
  PRD-012 rule, and it applies unchanged to this new refusal. A read path's
  build still raises.
- **Why the janitor can leave the cache over the watermark:** every key the
  service still points at is kept, so "everything is live" trims nothing. The
  answer there is a bigger budget, not a deletion a user would notice.
- `_referenced_cache_keys` consults the caller's working tree and the default
  branch, not every open branch: `.cache/` is canonical and shared, and the
  cost of being wrong is one rebuild of derived data, not lost work.
- **Verification:** `make test — 4161 passed, 32 skipped` (530.09s).
