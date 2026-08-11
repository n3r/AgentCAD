# Geometry CI — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship
[PRD-004](../../prd/in-progress/PRD-004-geometry-ci.md) — one command that
certifies a project: `agentcad check` rebuilds every part, re-resolves the
assembly, runs interference, evaluates design specs and regenerates drawings,
headless and without a server, and answers with a versioned JSON report, a
markdown summary and an exit code; plus the `run_checks` tool, a proposal
status slot, and a GitHub Action this repository dogfoods — per
[the design spec](../specs/2026-08-11-geometry-ci-design.md).

**Architecture (one paragraph):** `agentcad/core/checks.py` is a *sequencer*,
not a measurement. `CheckRunner` drives four surfaces that already exist and are
reviewed — `service._ensure_built` per manifest part, `service._resolved_instances`
+ `service.check_interference`, `service.specs.run` (PRD-003, all three tiers),
and the `generate_drawing` / `flat_pattern` tools — and shapes one `schema: 1`
report whose row statuses, summary counts and stage statuses are literally
PRD-003's (`summarize`, `report_status`, `group_requirements`, imported from
`core/specs.py`). `--ref` materializes the resolved commit into a throwaway
`git worktree --detach` under a work dir and runs against a **second, ephemeral
`AgentCADService`** sharing the same kernel, so the user's working tree and
`.cache/` are byte-untouched. The CLI gains `cmd_check`; a tool pack
`core/tools_run_checks.py` (named for load order — see Global constraints)
registers `run_checks`, installs `service.checks`, and appends a `checks` gate
provider that replaces PRD-002's built-in placeholder; a route pack
`server/routes_checks.py` exposes `POST/GET /api/projects/{p}/checks`. A
composite GitHub Action runs the same command on a runner's working tree.

**Tech stack:** Python 3.12 / stdlib `argparse`, `ast`, `hashlib`, `json`,
`tempfile` / FastAPI / pytest (session-scoped `kernel` fixture) / GitHub Actions
composite action + `astral-sh/setup-uv@v5`. **No new runtime dependency** — the
report schema validator is hand-rolled, not `jsonschema`.

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** This plan adds **zero**
  kernel files. `agentcad/core/checks.py` must import neither, directly or
  transitively — assert it with a test (import the module and assert
  `"OCP" not in sys.modules` in a fresh interpreter, the PRD-003 pattern).
- **Do not edit `worker.py`, `tools.py`, `app.py` or `service.py`.** New
  capability arrives as a tool pack (`agentcad/core/tools_run_checks.py`) and a
  route pack (`agentcad/server/routes_checks.py`), discovered by the existing
  `tools._load_tool_packs` / `app` route scan.
- **Do not edit `proposals.py`, `packet.py`, `merge.py`, `branches.py`,
  `history.py`, `manifest_merge.py`, `specs.py` or `project.py`.** PRD-001,
  PRD-002 and PRD-003 are finished and reviewed; this feature *consumes* them.
  The proposal status slot is a new file written beside the packet, and the gate
  is one callable appended to `service.gate_providers`.
- **Exactly two additive changes to existing non-test Python files** in the
  whole plan, both in `agentcad/cli.py` (which FR1 names as the home of
  `cmd_check`): (a) `_build_service` gains an optional
  `extra_writable: list[str] | None = None` parameter appended to
  `_writable_roots`' result — default `None` keeps every existing caller
  byte-identical; (b) `cmd_check` plus its subparser and its `main()` branch,
  and `{serve,open,mcp,new,export}` in the subparser `metavar` becomes
  `{serve,open,mcp,new,export,check}`. Docs and the changelog aside, **any other
  diff to an existing non-test file is a design bug** — stop and re-read the
  design spec.
- **The tool pack is `agentcad/core/tools_run_checks.py`. It must NOT be named
  `tools_checks.py`.** `tools._load_tool_packs` walks `pkgutil.iter_modules`
  alphabetically; `tools_checks` (`c`) would load *before* `tools_proposals`
  (`p`), which assigns `service.gate_providers = []` **unconditionally** and
  would silently discard a provider appended earlier. At `r` the pack loads
  after `tools_proposals` (so `gate_providers` and `service.proposals` exist)
  and before `tools_specs` (`s`) and `tools_versioning` (`v`) — therefore
  **`service.specs` and `service.branches` are read inside the methods, never
  captured in `__init__`** (PRD-003's exact trap).
- Structured errors only: `NotFoundError` / `ValidationError` / `ConflictError`
  (→ 404/422/409). **No new error type** — a failing, skipped or errored item is
  *payload*, never an exception (PRD-004's "Agent surface" says so explicitly).
- Row statuses are PRD-003's four (`pass|fail|skip|error`), summary counts its
  five (`passed|failed|skipped|errors|total`), stage/report statuses its three
  (`green|red|skip`). **Import `summarize`, `report_status`, `group_requirements`
  and `assign_ids` from `core/specs.py`; do not re-implement them.**
- Per-stage rows are called **`items`**, never `checks` (`checks` already means
  the built-in gate name, `report["checks"]` in a spec report, and the proposals
  UI tab). Gate objects use `state`; rows use `status`.
- Atomic writes (`ProjectStore._atomic_write`) for `report.json`, `report.md`
  and `proposals/<id>/checks.json`; `audit.jsonl` is **appended** through
  `ProposalStore.append_audit`, never rewritten.
- Every git call goes through `history._run` / `_run_bytes` (hermetic env, 10 s
  timeout) — **never a raw `subprocess`**.
- **All 914 tests must keep passing** (baseline: `914 passed, 1 skipped`,
  `docs/changelog/0097-prd-003-completed.md`). **No existing test file may be
  edited.** One test asserts `service.gate_providers` contains a gate named
  `checks`; `tests/test_proposals.py::test_specs_and_checks_are_skipped_with_no_providers`
  already sets `service.gate_providers = []` itself, so it is unaffected — verify
  that before slice 6 and stop if it is not true.
- Tests: session-scoped `kernel` fixture. Git-touching tests carry
  `pytest.mark.integration`, `pytest.mark.portability` and
  `skipif(shutil.which("git") is None)`; example-driven or kernel-heavy ones add
  `pytest.mark.slow`. Nothing here is `exhaustive`. Use the real service (**not**
  `make_test_service`, which sets `bus.on_publish = None`) wherever history
  matters, and copy the autouse `_reset_context` fixture that rebinds
  `locks.client_id_var` and `branches.pinned_tree_var`.
- `TestClient(app, base_url="http://127.0.0.1")` and
  `create_app(..., extra_allowed_hosts={"testserver"})` for every HTTP/WS test.
- **Examples run on a copy** —
  `shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".cache", "exports"))`,
  as in `tests/test_examples.py:44`. **Never mutate `examples/` in place** — no
  slice in this plan has an exception.
- **Never `uv sync` / `uv pip install` into the shared venv** from a parallel
  agent — use a scratch venv. This matters here: AC8 requires the suite green
  **without** the `[fem]` extra.
- **Subagents do not run `git`** (the coordinating session commits). They may
  *read* git state only through tests that drive `ProjectHistory`.
- **Every commit stages a changelog entry** `docs/changelog/NNNN-<slug>.md`
  written from the real diff, per `docs/changelog/README.md`. The highest
  existing entry when this plan was written is **0097**, so the slices below
  name 0098–0105 — **recompute `NNNN` at commit time** (`ls docs/changelog |
  tail`) because other work may have landed first.
- Commit messages end with `Co-Authored-By: Claude <noreply@anthropic.com>`.
- **Verification before completion, every slice:** run the named commands and
  cite their real output. "Should pass" is not a result.

---

## Slice map

| # | Slice | Lands | Changelog |
|---|---|---|---|
| 1 | The pure layer: report shape, statuses, exit codes, markdown, validator | FR7, FR8 primitives; AC5 (schema half) | `0098-check-report-shape.md` |
| 2 | `CheckRunner`: the four stages over a live service (working tree) | FR2, FR4, FR5; AC2, AC3, AC4 | `0099-check-stage-pipeline.md` |
| 3 | `--ref`: worktree materialization, ephemeral service, `--verify-determinism` | FR3, FR6; AC6, AC7 | `0100-check-ref-and-determinism.md` |
| 4 | CLI `agentcad check` | FR1; AC5 (exit codes) | `0101-agentcad-check-cli.md` |
| 5 | Tool pack, route pack, `check_finished` | FR12; AC9 | `0102-run-checks-tool-and-routes.md` |
| 6 | Proposal posting + the `checks` gate provider | FR9; G4 | `0103-check-proposal-gate.md` |
| 7 | The GitHub Action + the dogfood workflow | FR10, FR11; AC1 | `0104-geometry-ci-action.md` |
| 8 | Docs, acceptance tests, PRD close-out | AC1–AC10 | `0105-prd-004-docs-and-acceptance.md` |

Each slice is independently landable: 1 is a pure module nothing imports yet;
2 makes it measure; 3 makes it safe on a ref; 4 makes it usable; 5 makes it
reachable by agents; 6 makes it visible where decisions happen; 7 makes it run
on every push; 8 is docs and acceptance.

---

## Slice 1 — the pure layer (`agentcad/core/checks.py`)

**Why first:** it is the contract slices 2–8 are written against, it has no
kernel and no I/O, and it is where AC5 is actually won. A fixture report is
enough to build the whole reporting half before a single part is built.

### Files
- `agentcad/core/checks.py` (new — pure functions and dataclasses only, so far)
- `tests/test_checks.py` (new)

### The shapes (copy these exactly from the design spec, Decision 2)

```python
REPORT_SCHEMA = 1
STAGES = ("build", "assembly", "specs", "drawings")   # declared order

# One row. Rows are ITEMS, never "checks".
item = {"id": "build:nozzle", "kind": "part", "subject": "nozzle",
        "status": "pass|fail|skip|error", "message": str,
        "reason": str|None, "hint": str|None,       # skip => both non-None
        "error": {"type","message","details"}|None, "details": dict}

stage = {"name": str, "status": "green|red|skip", "reason": str|None,
         "duration_s": float, "summary": {...}, "items": [item, ...]}

report = {"schema": 1, "agentcad": ..., "project": ..., "source": {...},
          "started", "finished", "duration_s", "status", "complete", "strict",
          "exit_code", "summary", "stages", "requirements", "warnings",
          "errors", "host"}
```

### Tasks

- [ ] **Task 1 — module skeleton and re-exports.** `from .specs import
  summarize, report_status, group_requirements, assign_ids` and a local `_now()`
  matching `specs._now` (UTC, ISO-8601, trailing `Z`, second resolution). Module
  docstring states the three rules from the design spec: composes never
  measures, `items` never `checks`, no new status vocabulary.
- [ ] **Task 2 — `make_item(...)`, `make_stage(...)`, `finalize_report(...)`.**
  `make_stage` computes its own `summary` via `summarize(items)` and `status`
  via `report_status(summary)`; a stage explicitly skipped keeps
  `status: "skip"` with a `reason`. `finalize_report` flattens every stage's
  items, computes the top-level `summary`, `status`, `requirements` (passing the
  specs stage's rows through `group_requirements`), and calls `exit_code`.
  **Invariant test:** a `skip` item always has both `reason` and `hint`
  (PRD-003's rule) — enforce it in `make_item` with a `ValueError`, so a
  malformed skip is a programming error caught by a test, never a silent row.
- [ ] **Task 3 — `exit_code(report) -> int`.** The whole table from the design
  spec, as one pure function over `(status, complete, strict, strict_failures)`.
  `complete: False` → 2 regardless of status; `strict` and any `skip` row → 1;
  `failed or errors` → 1; else 0. Test every branch directly, no kernel.
- [ ] **Task 4 — `render_markdown(report) -> str` (FR8).** Header line (project,
  source, status, duration, agentcad version, platform, `fem: yes/no`), the
  stage table, then a `## Failures` section: one block per `fail`/`error` item
  with its message, `error.details.line` when present, and its `hint` quoted.
  Then `## Skipped` (grouped by `reason`) when non-empty. **Cap the rendered
  failures at `MAX_RENDERED_FAILURES = 50`** and append
  `_+N more — see report.json_`: `$GITHUB_STEP_SUMMARY` is capped at 1 MiB.
  Must be valid GitHub-flavoured markdown and valid as a PR comment body.
- [ ] **Task 5 — `validate_report(report) -> list[str]` (AC5).** Hand-rolled:
  required keys, types, enum membership for every `status`/`state`, `schema == 1`,
  every stage name in `STAGES` (plus `determinism`), every item `id` unique,
  every `skip` carrying `reason` and `hint`. Returns a list of human-readable
  problems (empty = valid). **No `jsonschema` dependency.**
- [ ] **Task 6 — `declares_flat_pattern(script) -> bool`.** `ast.parse`, look for
  a module-level `FunctionDef` named `flat_pattern`; on `SyntaxError` fall back
  to a line-anchored `^[ \t]*def[ \t]+flat_pattern[ \t]*\(` regex (PRD-003's
  fail-closed precedent — a false positive costs one error row on a script that
  already failed its build). Memoize by `sha256(script)` with the same bounded
  dict pattern `specs._DECLARES_MEMO` uses. **Never executes the script.**

### Verification
- [ ] `uv run pytest -q tests/test_checks.py` — cite the count.
- [ ] `uv run python -c "import agentcad.core.checks, sys; assert 'OCP' not in sys.modules"`
- [ ] `uv run pytest -q -n 2 --dist loadscope -m "not exhaustive"` still green.
- [ ] Changelog `0098-check-report-shape.md`.

---

## Slice 2 — `CheckRunner`: the four stages (working tree)

**Why second:** this is the feature. Once it runs against a live service every
later slice is packaging.

### Files
- `agentcad/core/checks.py` (grow: `CheckRunner`)
- `tests/test_checks_pipeline.py` (new, `slow`)

### The API

```python
class CheckRunner:
    def __init__(self, service): ...        # NO service.specs / .branches here

    def run(self, proj: str, *, ref: str | None = None,
            stages: tuple[str, ...] = STAGES, strict: bool = False,
            budget_s: float | None = None, min_volume: float = 0.001,
            verify_determinism: bool = False,
            sha: str | None = None, ref_label: str | None = None,
            work_dir: str | None = None) -> dict: ...
```

Slice 2 implements `ref=None` and `verify_determinism=False`; both raise
`NotImplementedError` until slice 3 (with a test asserting that, so the seam is
real and not aspirational).

### Tasks

- [ ] **Task 1 — the deadline helper.** `_deadline = time.monotonic() +
  budget_s` (monotonic, **never** wall clock — an NTP step must not move a
  budget), `_remaining()`, `_out_of_budget()`. Copy the shape of
  `SpecRunner._out_of_budget` / `_budgeted`. Every stage checks the deadline
  **before each item**; an exhausted budget marks the remaining stages
  `skip / budget_exceeded` and sets `report["complete"] = False`. Docstring
  states the honest limitation: `_ensure_built` (300 s) and the drawing tools
  (120 s) take no `timeout_s`, so the worst-case overshoot is one in-flight
  kernel call.
- [ ] **Task 2 — `_stage_build`.** Iterate `service.store.manifest(proj)["parts"]`;
  `service._ensure_built(proj, pid)` each. Map per the design spec: `ok: false`
  → `fail` with `error` = the payload verbatim (this is AC4 — the same
  `details.line` and Error-Doctor `details.hint` `update_part_script` returns);
  `ok: true` + `metrics["is_valid"] is False` → `fail`; else `pass` with
  `details = {cache_key, volume_mm3, mass_g, n_solids, is_valid, cached}`.
  Reference parts are ordinary rows here. Never let a `KernelError` escape —
  `_ensure_built` already converts, but wrap defensively and record an `error`
  item plus a `report["errors"]` entry.
- [ ] **Task 3 — `_stage_assembly`.** `service._resolved_instances(proj,
  timeout_s=self._remaining())` inside `try/except ValidationError` → one `fail`
  item of kind `mate` carrying the payload. Then one `pass` item per resolved
  instance (`details.mated`). Then `service.check_interference(proj, min_volume,
  timeout_s=self._remaining())` — **the service method, not the tool** (the tool
  has no `timeout_s` in its schema). Each pair → a `fail` item of kind `pair`,
  `subject = f"{a} ↔ {b}"`, `details = {a, b, volume_mm3}`. Each id in
  `skipped_mesh` → a `skip` item, `reason: "mesh_only"`, hint naming the OCCT
  segfault. `< 2` instances → the whole stage `skip / no_instances`.
- [ ] **Task 4 — `_stage_specs`.** `runner = getattr(self.service, "specs",
  None)` **read here, not in `__init__`**; `None` → stage
  `skip / specs_unavailable`. `report = runner.run(proj)` (no `ref=` — see
  slice 3). Embed the report as `stage["report"]` and map each row in
  `report["checks"]` to an item preserving `status`, `message`, `measured`,
  `limit`, `unit`, `requirement`, `reason`, `hint`, `error`. `report["declared"]
  == 0` → stage `skip / not_declared`. Any exception → one `error` item plus a
  `report["errors"]` entry; the runner never propagates.
- [ ] **Task 5 — `_stage_drawings`.** For each **script** part: call the
  registered `generate_drawing` through `self._registry` (the runner takes an
  optional `registry`; when absent it imports `tools_drawing`'s handler shape via
  the registry it was given — pass the registry in from the caller, do not
  rebuild one). `format="svg"` only (DXF is not byte-stable — design Decision 7).
  Then `flat_pattern` **only when `declares_flat_pattern(script)`**; a part that
  does not define it gets **no row** (absent, not green). Reference parts →
  `skip / not_script`. A tool returning `{"error": {...}}` → a `fail` item with
  that payload (`ToolRegistry.call` converts `AppError`/`KernelError`; it does
  not raise).
- [ ] **Task 6 — `run()` assembly.** Stage selection (`stages` tuple; unselected
  stages emit `skip / not_selected` so all four always appear), `source` block
  (`kind: "worktree"`, `dirty` from `git status --porcelain` when the project has
  history — best-effort, never raising), `host` block (`platform.system()`,
  `sys.version`, `agentcad.__version__`, `fem_available()`, `sandbox.supported()
  and not disabled`, pool size, kernel class name), timing, then
  `finalize_report`.

### Tests (`tests/test_checks_pipeline.py`, `slow`)
- [ ] AC4: copy `examples/prototyping`, break one part's script with a
  `SyntaxError`/`NameError` at a known line → build stage `red`, the item's
  `error.details.line` matches, `error.details.hint` present.
- [ ] AC2: copy `examples/construction`, move an instance so two parts overlap →
  assembly stage `red`, the item names both instances and its `volume_mm3` > 0.
- [ ] AC3: copy `examples/rocketry` (which declares specs), tighten a limit so a
  check fails → specs stage `red`, the item carries `measured` and `limit`.
- [ ] A clean copy of each of `construction`, `prototyping`, `rocketry` →
  `status == "green"`, `exit_code == 0`.
- [ ] `--stages build` → the other three are `skip / not_selected`.
- [ ] A tiny `budget_s` → `complete is False`, unreached stages
  `skip / budget_exceeded`, `exit_code == 2`, and the report still validates.
- [ ] Skip semantics: an assembly with an imported STL reference produces a
  `skip / mesh_only` item; `strict=True` flips `exit_code` to 1 while the row
  **stays** `skip` with its reason (this is the whole point of Decision 6).
- [ ] AC8 (no-extra half): simulate `fem_available() is False` and assert the
  fem-linked spec rows are `skip / fem_extra_missing`, `exit_code == 0`, and
  `strict=True` → 1. **The suite must be green without the `[fem]` extra.**

### Verification
- [ ] `uv run pytest -q tests/test_checks.py tests/test_checks_pipeline.py`
- [ ] `uv run pytest -q -n 2 --dist loadscope -m "not exhaustive"` — cite count.
- [ ] Changelog `0099-check-stage-pipeline.md`.

---

## Slice 3 — `--ref`, the ephemeral service, and `--verify-determinism`

**Why third:** it is the riskiest code in the feature (it touches the user's git
repository) and everything above it is already proven, so a failure here is
unambiguous.

### Files
- `agentcad/core/checks.py` (grow: `_resolve_ref`, `materialized`, determinism)
- `tests/test_checks_ref.py` (new — `integration`, `portability`, `slow`,
  `skipif(shutil.which("git") is None)`)

### Tasks

- [ ] **Task 1 — `_resolve_ref(proj, ref) -> {"kind","ref","sha"}`.** Try
  `history.resolve_branch`, then `resolve_tag`, then
  `looks_like_commit(ref) and history.has_commit(...)`. Accept explicit
  `refs/heads/<x>` and `refs/tags/<x>`. **Never `resolve_ref` alone** —
  `rev-parse` searches tags before branches (PRD-001 X1). A name that is both a
  branch and a tag resolves as the **branch** and adds a `warnings[]` entry
  naming the ambiguity. No git → `ValidationError` naming git. Unknown →
  `NotFoundError` saying what was searched.
- [ ] **Task 2 — `materialized(proj, sha, work_dir)` context manager.**
  1. `history._run(canonical, "worktree", "prune", check=False)`
  2. `history._run(canonical, "worktree", "add", "--detach", str(tree), sha)`
     — **`--detach` with the resolved commit**, never the branch name (a branch
     already checked out at `.history/trees/<b>/` cannot be checked out twice).
     This is `MergeOrchestrator._stage`'s exact mechanism.
  3. `finally`: `"worktree", "remove", "--force", str(tree), check=False` then
     `"worktree", "prune", check=False`. A cleanup failure is a `warnings[]`
     entry, **never** a red check.
  Default `work_dir` is `tempfile.mkdtemp(prefix="agentcad-check-")`; the
  materialized tree is `<work_dir>/<project-name>/`.
- [ ] **Task 3 — the ephemeral service.** Inside `materialized`:
  ```python
  ephemeral = AgentCADService(Path(work_dir), self.service.kernel, EventBus())
  ephemeral.bus.on_publish = None        # NON-NEGOTIABLE
  ephemeral.store.open(tree)
  registry = build_registry(ephemeral)
  ephemeral.store.branch_resolver = None # set AFTER build_registry
  ```
  **Both assignments get a comment naming the failure they prevent**:
  `on_publish` would commit a history snapshot into the linked worktree — i.e.
  into the user's real repository — from a command whose contract is "never
  mutates"; `branch_resolver` would send every path through a `BranchManager`
  resolving against a `.history/agentcad/` sidecar that does not exist there.
  The ephemeral service shares the **same kernel object** — do not start a
  second pool.
- [ ] **Task 4 — wire `run(ref=...)`.** Resolve, materialize, build the
  ephemeral service+registry, run the same four stages against it, and stamp the
  **real** `source` block (`kind`, `ref`, `sha`) on the report — the inner
  `SpecRunner.run` is called with `ref=None` (it measures the tree it is given)
  and its report's own `ref` field stays `None`; the check report is where the
  ref is named. Record `source.dirty` by running `git status --porcelain` in the
  *named branch's* tree when the ref is a branch, plus a warning — and **do not
  snapshot**: a check may not mutate.
- [ ] **Task 5 — `--verify-determinism` (FR6).** A `determinism` pseudo-stage:
  for each part, build once through the normal cache, then build again into a
  fresh temp cache directory (a second ephemeral service rooted at a temp copy of
  the *tree*, so the cache is cold), and compare (a) `cache_key`, (b)
  `<key>.acm` bytes and `<key>.faces.u32` where present, (c)
  `volume_mm3`/`mass_g`/`area_mm2`. A mismatch is a `fail` naming **which** of
  the three diverged and the first differing byte offset. `.acm` is byte-stable
  (no timestamps/ids anywhere in `acm.py`/`mesh.py`/`worker.py`); **do not
  compare DXF** — `ezdxf` stamps `$TDCREATE` and fresh GUIDs on every document.

### Tests
- [ ] AC7: create a branch and a tag on a copied example, run `check --ref <tag>`
  and `--ref <branch>`, and assert **byte-identity**: hash every file under the
  project dir (including `.cache/`) before and after and compare the two maps.
- [ ] The repo is untouched: `history.head(canonical)` unchanged, and
  `git worktree list` back to its previous length after the run (including after
  an exception injected mid-run).
- [ ] `--ref` on a project with no git → `ValidationError` naming git.
- [ ] A tag and a branch with the same name → resolves as the branch, warning
  present.
- [ ] A branch with uncommitted edits → `source.dirty is True`, a warning, and
  the report measures the **committed** state (assert a value only present in
  the commit).
- [ ] AC6: `--verify-determinism` on a clean copy of `construction` and
  `prototyping` → `determinism` stage green, `exit_code == 0`.

### Verification
- [ ] `uv run pytest -q tests/test_checks_ref.py -p no:randomly` — cite count.
- [ ] `uv run pytest -q -n 2 --dist loadscope -m "not exhaustive"`.
- [ ] Changelog `0100-check-ref-and-determinism.md`.

---

## Slice 4 — the CLI (`agentcad check`)

### Files
- `agentcad/cli.py` (the two sanctioned additive edits)
- `tests/test_checks_cli.py` (new)

### Tasks

- [ ] **Task 1 — `_build_service(projects_dir, extra_writable=None)`.** Append
  `extra_writable` to `_writable_roots(projects_dir)` **before** the kernel
  starts — the seatbelt profile is fixed at spawn, so a `--work-dir` outside
  `tempfile.gettempdir()` must be known first. Default `None` leaves every
  existing caller byte-identical; assert that with a test.
- [ ] **Task 2 — `cmd_check(args)`.** Resolve `--work-dir` (or `mkdtemp`) →
  `_build_service(projects_dir, extra_writable=[work_dir])` →
  `build_registry(service)` → `locks.set_client_id("ci")` →
  `service.checks.run(...)` (or `CheckRunner(service, registry)` directly, if
  slice 5 has not landed) → write `--report` / `--md` atomically → print → exit.
  **No FastAPI app, no port, no chat engine, no API key.**
  `service.kernel.stop()` in a `finally`.
  `--project` accepts a path or a name using `cmd_export`'s idiom
  (`"/" in project or project.startswith(".")` → `service.open_project(path)`).
- [ ] **Task 3 — the flags**, exactly as the design spec's CLI block:
  `--project --projects-dir --ref --stages --report --md --strict
  --verify-determinism --budget --min-volume --work-dir --proposal
  --auto-proposal --sha --ref-label --quiet --json`. `--stages` is a
  comma-separated list validated against `STAGES` (an unknown name is exit 2 with
  the valid list). `--proposal`/`--auto-proposal` are accepted and *ignored with
  a warning* until slice 6, so the CLI surface does not change shape mid-plan.
- [ ] **Task 4 — human output.** A stage table to **stderr** as each stage
  finishes, the one-line verdict to stdout; `--json` prints the report to stdout
  instead (so `agentcad check --json | jq` works); `--quiet` prints nothing but
  the exit code. Exit codes identical in all three modes.
- [ ] **Task 5 — subparser + `main()` branch + `metavar` update.** Keep the
  hidden `worker` subcommand hidden.

### Tests (`tests/test_checks_cli.py`)
- [ ] AC5: `subprocess.run([sys.executable, "-m", "agentcad.cli", "check", ...])`
  — or the console script — on a green copy → 0; on a broken copy → 1; on an
  unknown project → 2. Cover all three.
- [ ] `--report`/`--md` files are written and `validate_report` passes on the
  JSON; the markdown contains the failing item's name.
- [ ] `--stages bogus` → exit 2 naming the valid stages.
- [ ] `--budget 0.001` → exit 2 with a partial report on disk.
- [ ] `_build_service()` with no `extra_writable` produces the same writable
  roots as before (pin the existing behaviour).

### Verification
- [ ] `uv run agentcad check --project examples/prototyping --md /tmp/r.md
      --report /tmp/r.json; echo "exit=$?"` — paste the real table and exit code.
- [ ] Full suite green — cite count.
- [ ] Changelog `0101-agentcad-check-cli.md`.

---

## Slice 5 — the tool pack, the route pack and `check_finished`

### Files
- `agentcad/core/tools_run_checks.py` (new — **not** `tools_checks.py`)
- `agentcad/server/routes_checks.py` (new)
- `tests/test_checks_api.py` (new)

### Tasks

- [ ] **Task 1 — the pack.** `register(registry, service)` installs
  `service.checks = CheckRunner(service, registry)` and registers `run_checks`
  with `schema({project, ref, stages, strict, budget, proposal}, ["project"])`.
  The module docstring states the load-order facts (after `tools_proposals`,
  before `tools_specs`/`tools_versioning`; `service.specs` and
  `service.branches` read lazily) and why the file is not called
  `tools_checks.py`. The tool description states the four statuses and that a red
  check is **data**, not an error.
- [ ] **Task 2 — `check_finished`.** `service.bus.publish({"type":
  "check_finished", "project", "ref", "status", "exit_code", "summary",
  "duration_s"})` at the end of every run — including red and
  budget-truncated. Publish from `CheckRunner.run`, so the CLI, the tool and the
  route all emit it.
- [ ] **Task 3 — routes.** `build_router(service, registry)`:
  `POST /projects/{proj}/checks` with the body **whitelisted** to
  `{ref, stages, strict, budget, proposal}` (never `**body`), and
  `GET /projects/{proj}/checks` returning the last report this process produced
  (an in-memory `service.checks.last[proj]`), `404` when there is none. Raise
  `NotFoundError`/`ValidationError` and let the app map them.
- [ ] **Task 4 — the last-report cache.** A small bounded dict on `CheckRunner`
  keyed by project (the last report only) — not persisted; slice 6 adds the
  durable proposal copy.

### Tests
- [ ] AC9: run the CLI and the tool over the same copied project and assert the
  reports are equal after normalizing `started`/`finished`/`duration_s`/`host`
  and every `duration_s`.
- [ ] `POST` returns the report; the body whitelist rejects an unknown key;
  `GET` before any run is 404, after a run returns the same report.
- [ ] A WS test observes `check_finished`
  (`create_app(..., extra_allowed_hosts={"testserver"})`).
- [ ] `run_checks` on an unknown project returns
  `{"error": {"type": "not_found_error", ...}}` — not a raise.

### Verification
- [ ] `uv run pytest -q tests/test_checks_api.py` and the full suite.
- [ ] Changelog `0102-run-checks-tool-and-routes.md`.

---

## Slice 6 — proposal posting and the `checks` gate

### Files
- `agentcad/core/checks.py` (grow: `CheckStore`, `post_to_proposal`,
  `checks_gate_provider`)
- `agentcad/core/tools_run_checks.py` (grow: `install_checks_gate`)
- `tests/test_checks_gate.py` (new — `integration`, `portability`)

### Tasks

- [ ] **Task 1 — `CheckStore`.** Reuse `ProposalStore.dir_of(proj)`; write
  `<pid>/checks.json` with `ProjectStore._atomic_write`. Payload per the design
  spec (`schema, posted_at, posted_by, actor_kind, source, head, status,
  exit_code, complete, strict, summary, stages[name/status/summary], report`).
  Proposals are canonical and branch-independent — **never** write check state
  into a working tree.
- [ ] **Task 2 — `post_to_proposal(proj, pid, report)`.** Resolve the proposal
  (`service.proposals.get`), refuse a **terminal** proposal (merged/closed —
  a terminal proposal is never measured again, PRD-002's rule), write
  `checks.json`, append to `audit.jsonl` via `ProposalStore.append_audit`
  (append-only — never a read-modify-replace), and publish
  `proposal_changed` with `reason: "checks"`.
- [ ] **Task 3 — `--auto-proposal`.** `service.proposals.list(proj)` filtered to
  **active** states whose `source` equals the branch that was checked. Zero → a
  warning and no post (most checks are not about a proposal). More than one →
  exit 2; guessing is worse than refusing.
- [ ] **Task 4 — the gate provider.** A closure literally named `checks` (so it
  replaces PRD-002's built-in placeholder by name), appended by an idempotent
  `install_checks_gate(service)` called from `register()` — which is safe
  because the pack loads **after** `tools_proposals`. The table:
  nothing posted → `skipped` (byte-identical to today's placeholder) ·
  posted `head` ≠ current source head → `pending` with both SHAs named ·
  `status: red` → **`fail`** (the only state that blocks) · green + current →
  `pass`. The provider never raises: any exception becomes a `pending` gate
  naming the failure (a check gate is evidence, not enforcement — the
  fail-closed enforcement is PRD-003's `specs` gate, which re-measures on every
  `merge()`).
- [ ] **Task 5 — identity.** `locks.set_client_id("ci")` in `cmd_check` (slice 4
  already added it) so `actor_kind` classifies the post as **agent** and a CI run
  never collides with a human's per-client checkout in `checkouts.json`.

### Tests
- [ ] The four gate states, each asserted through `service.proposals.get(...)`'s
  `gates` list.
- [ ] A red posted report **blocks** `service.proposals.merge(...)`; a stale one
  (`pending`) does not, and the docstring/test names why (the `specs` and
  `validation` gates re-measure current heads).
- [ ] Load order: after `build_registry(service)` on a git-backed project,
  `[g.__name__ for g in service.gate_providers]` contains both `specs` and
  `checks`, and `ProposalManager.gates()` returns exactly one gate named
  `checks`.
- [ ] Posting to a merged proposal raises `ValidationError`/`ConflictError`.
- [ ] `audit.jsonl` gained exactly one line and the previous lines are byte-identical.

### Verification
- [ ] `uv run pytest -q tests/test_checks_gate.py tests/test_proposals.py
      tests/test_proposals_api.py` — the two proposals suites are load-bearing
      here and must pass **unedited**.
- [ ] Full suite green — cite count.
- [ ] Changelog `0103-check-proposal-gate.md`.

---

## Slice 7 — the GitHub Action and the dogfood workflow

### Files
- `.github/actions/agentcad-check/action.yml` (new — composite)
- `.github/actions/agentcad-check/README.md` (new — inputs/outputs, runner
  requirements, trust model)
- `.github/workflows/geometry-ci.yml` (new)

### Tasks

- [ ] **Task 1 — `action.yml` (composite).** Inputs and outputs exactly as the
  design spec's table. Steps: `astral-sh/setup-uv@v5` (`enable-cache: true`,
  `cache-dependency-glob: uv.lock`) → Linux OCCT system libraries, **the exact
  six `ci.yml` already installs** (`libgl1 libglu1-mesa libxrender1 libxcursor1
  libxft2 libxinerama1` — OCCT needs an X/GL stack even headless; that list is
  hard-won, copy it verbatim) → install agentcad → run
  `agentcad check --project "$PROJECT" --sha "$GITHUB_SHA" --ref-label
  "$GITHUB_REF_NAME" --report report.json --md report.md …` with
  `continue-on-error` so the summary is always written → `cat report.md >>
  "$GITHUB_STEP_SUMMARY"` → `actions/upload-artifact` → set outputs → re-exit
  with the saved code. `AGENTCAD_KERNEL_POOL_SIZE` from the `pool-size` input
  (default `1`).
  **Do NOT pass `--ref $GITHUB_SHA`** — `actions/checkout` has already
  materialized the ref into the working tree, and a runner has no AgentCAD
  `.history` repo to resolve it against (design Decision 9). The SHA is
  provenance.
- [ ] **Task 2 — `geometry-ci.yml`.** `ubuntu-latest`, matrix over the light
  bundled examples (`construction`, `prototyping`, `rocketry`, `fasteners`) on
  `push`/`pull_request`, with `engine` added on the nightly `schedule` job —
  mirroring how `ci.yml` already defers the engine example. Uses the local
  action with `agentcad: .`. `concurrency` group like `ci.yml`'s.
  **`pull_request`, never `pull_request_target`; no secrets** — a fork's part
  scripts are arbitrary Python and Linux has no seatbelt.
- [ ] **Task 3 — the README.** Inputs/outputs table; runner requirements (~2 GB
  installed OCCT wheels + uv cache → budget 8 GB free disk; ~0.5 GB RAM per
  kernel worker → `pool-size: 1` on a standard 2-core runner); the caching
  layers and which are on in v1 (L1 uv cache yes; L2 `.venv` and L3 the
  project's `.cache/` no — the PRD's open question, ship and measure); the
  `.gitignore` a repo-hosted project needs (`.history/`, `.cache/`,
  `exports/`); the trust model (Linux is unconfined until PRD-006).
- [ ] **Task 4 — AC1.** Push the branch and confirm the workflow is **green on a
  live run**. This AC cannot be satisfied locally; the coordinating session
  pushes and cites the run URL and conclusion.

### Verification
- [ ] `actionlint` (or `uv run python -c "import yaml, pathlib; [yaml.safe_load(p.read_text()) for p in pathlib.Path('.github').rglob('*.yml')]"`) parses both files.
- [ ] The live workflow run is green — cite the URL and the four job names.
- [ ] Local suite still green.
- [ ] Changelog `0104-geometry-ci-action.md`.

---

## Slice 8 — docs, acceptance tests, PRD close-out

### Files
- `docs/geometry-ci.md` (new)
- `docs/agent-api.md`, `docs/architecture.md`, `docs/user-guide.md`,
  `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/roadmap.md`
- `tests/test_prd004_acceptance.py` (new)
- move `docs/prd/in-progress/PRD-004-geometry-ci.md` →
  `docs/prd/completed/`

### Tasks

- [ ] **Task 1 — `docs/geometry-ci.md`.** The CLI contract; the full report
  schema (generated *against* `validate_report`, not written from memory); exit
  codes; the stage table and what each stage may claim; ref mode vs working-tree
  mode and the byte-identity guarantee; the honest `--budget` overshoot; the
  determinism guard and why DXF is excluded; consuming the action from a CAD
  repo; runner requirements and caching; the trust model.
- [ ] **Task 2 — `docs/agent-api.md`.** A `### Geometry CI` section for
  `run_checks` + the `check_finished` event, and update the "42/45 agent tools"
  line in `AGENTS.md`.
- [ ] **Task 3 — `docs/architecture.md`.** A `## Geometry CI` section after
  "Design specs", with the ephemeral-service diagram.
- [ ] **Task 4 — `AGENTS.md`** gains a **"CI gotchas (PRD-004)"** section:
  the ephemeral service must have `bus.on_publish = None` and
  `branch_resolver = None` or a check commits into the user's repo ·
  the pack is `tools_run_checks.py` for load order, never `tools_checks.py` ·
  rows are `items`, never `checks` (four live meanings) ·
  `check` is report-honest and `--strict` is the opt-in to the gate's
  fail-closed philosophy, while `evaluate_specs` is unconditionally fail-closed ·
  `--ref` uses `worktree add --detach <sha>`, never a branch name ·
  `resolve_branch` then `resolve_tag`, never `resolve_ref` (tags shadow
  branches) · DXF is not byte-stable (ezdxf `$TDCREATE` + GUIDs) ·
  a ref check runs on a **cold cache**, deliberately, to buy AC7 ·
  `--budget` cannot preempt an in-flight build (300 s) or drawing (120 s).
  Mirror the condensed list into `CLAUDE.md`'s traps section.
- [ ] **Task 5 — `tests/test_prd004_acceptance.py`.** AC2–AC9 as named tests
  (AC1 is the live run, cited in the changelog; AC10 is the suite count).
  Examples on copies; `slow`; git tests `integration` + `portability`.
- [ ] **Task 6 — close-out.** Move the PRD, set its status to `completed`, and
  fix `docs/roadmap.md` — its PRD-004 row points at `prd/pending/` while the
  file lives in `prd/in-progress/`.

### Verification
- [ ] `uv run pytest -q tests/test_prd004_acceptance.py -q`
- [ ] `make test` — **cite the count** and compare to the 914 baseline.
- [ ] Every doc link resolves (`uv run python - <<'PY'` link check or `grep`).
- [ ] Changelog `0105-prd-004-docs-and-acceptance.md`.

---

## Rollback / landing notes

- Slices 1–3 add one new module and no surface; reverting them is a file
  deletion. Slice 4 is the first user-visible change and is two additive edits
  to `cli.py`. Slice 5 adds two packs, both discovered by name — deleting the
  files removes the tool and the routes cleanly. Slice 6 is the only slice that
  writes into a user's `.history/agentcad/`; its file (`checks.json`) is additive
  and ignored by every existing reader, and removing the gate provider restores
  PRD-002's placeholder exactly.
- The one irreversible-feeling risk is slice 3 touching `git worktree` state in a
  user's project. It is not actually irreversible — `git worktree prune` heals
  any leak — but that is why slice 3 has its own test asserting
  `git worktree list` and `history.head` are unchanged after a run, **including
  after an exception injected mid-run**.
- If the live AC1 run (slice 7) is red for an environment reason rather than a
  geometry reason, do **not** loosen the check to make it green. Fix the runner
  (system libs, pool size, disk) or scope the matrix, and say which in the
  changelog.
