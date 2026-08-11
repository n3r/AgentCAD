# Geometry CI — design

- **PRD:** [PRD-004](../../prd/in-progress/PRD-004-geometry-ci.md)
- **Date:** 2026-08-11
- **Depends on (all completed):** PRD-001 (branches, refs, `.history` git
  layer) · PRD-002 (proposals, the gate seam, the packet's head protocol) ·
  PRD-003 (`SpecRunner`, `run_specs`, the fail-closed specs gate, sidecar
  caches)
- **Plan:** [2026-08-11-geometry-ci.md](../plans/2026-08-11-geometry-ci.md)

---

## Problem

Nothing re-validates a whole project at change scale. A part is checked when it
rebuilds; whether the mates still resolve, the assembly is still
interference-free, the specs are still green and the drawings still generate is
checked only when someone remembers to open the app and look. Branches and
proposals make that acute: a merge decision needs a project-wide verdict, and an
agent needs a machine-readable one.

Every ingredient already exists and is reviewed. `service._ensure_built`
rebuilds one part with a content-addressed cache. `service._resolved_instances`
re-runs the mate pass. `service.check_interference` finds interfering pairs.
`SpecRunner.run` evaluates all three spec tiers and returns a report with a
status vocabulary, a summary shape and requirement grouping.
`generate_drawing` and `flat_pattern` regenerate 2D output. **This feature adds
no measurement.** It is a *sequencer* and a *reporter*: one module that drives
those five surfaces over every part in a project, shapes one versioned report,
renders it twice (JSON and markdown), and answers with an exit code — plus the
CLI, tool, route, proposal slot and GitHub Action that carry it.

The design work is therefore almost entirely about **honesty and containment**:
what each stage may claim, what a skip means, which failure is the model's and
which is the harness's, and how a check can measure a ref without touching a
single byte the user owns.

---

## Architecture at a glance

```
agentcad check --project . --ref feat/nozzle --strict --report report.json
        │
        ▼
cli.cmd_check ──► _build_service(projects_dir, extra_writable=[work_dir])
        │              (KernelPool started once, warm build123d, no server,
        │               no port, no API key, no chat engine)
        ├──► build_registry(service)          # every tool pack, incl. specs
        │
        ├── ref mode ────────────────────────────────────────────────┐
        │   history._run(canonical, "worktree", "add", "--detach",   │
        │                <work_dir>/<project>, <resolved sha>)       │
        │   ephemeral = AgentCADService(<work_dir>, SAME kernel, …)  │
        │   ephemeral.bus.on_publish = None                          │
        │   ephemeral.store.branch_resolver = None                   │
        │   build_registry(ephemeral);  ephemeral.store.open(tree)   │
        └────────────────────────────────────────────────────────────┘
        │
        ▼
core/checks.py  CheckRunner.run(project, …) -> report
        │
        │  stage 1 build      service._ensure_built  per manifest part
        │  stage 2 assembly   service._resolved_instances + check_interference
        │  stage 3 specs      service.specs.run(project)      (all three tiers)
        │  stage 4 drawings   generate_drawing / flat_pattern  per script part
        │
        ▼
report (schema 1)  ──► report.json     (FR7)
                   ──► report.md       ($GITHUB_STEP_SUMMARY / PR comment, FR8)
                   ──► exit code       0 green · 1 red · 2 harness (FR1)
                   ──► proposals/<id>/checks.json + the `checks` gate (FR9)
                   ──► check_finished on the WebSocket channel (FR12)
```

Surfaces, all extension-point packs — **no edit to `worker.py`, `tools.py`,
`app.py`, `service.py`, `proposals.py`, `packet.py`, `merge.py`, `branches.py`
or `history.py`**:

| File | Role |
|---|---|
| `agentcad/core/checks.py` | `CheckRunner`, the stage pipeline, the report shape, the markdown renderer, the schema validator, the ref materializer, the proposal poster |
| `agentcad/core/tools_run_checks.py` | tool pack: `run_checks`; installs `service.checks` and the `checks` gate provider |
| `agentcad/server/routes_checks.py` | route pack: `POST /api/projects/{p}/checks`, `GET /api/projects/{p}/checks` |
| `agentcad/cli.py` | `cmd_check` joins `serve/open/mcp/worker/new/export` (FR1 names this file) |
| `.github/actions/agentcad-check/action.yml` | composite action (FR10) |
| `.github/workflows/geometry-ci.yml` | this repo's dogfood (FR11, AC1) |
| `docs/geometry-ci.md` | report schema, runner requirements, trust model |

---

## Decision 1 — `check` composes; it never re-implements a measurement

Every stage is a call into a surface that already exists, reviewed and tested.
The pipeline's whole job is ordering, budgeting, and turning results into rows.

| Stage | What it calls | Why that call and not another |
|---|---|---|
| `build` | `service._ensure_built(proj, part_id)` per manifest part | cache-aware (the PRD's own "content-hash cache hits for unchanged parts" mitigation), and the exact call `get_assembly`, `merge._validate` and `packet._side_state` already use. There is **no `rebuild_all`** in the codebase; iterating the manifest *is* the established pattern. |
| `assembly` | `service._resolved_instances(proj, timeout_s=…)` then `service.check_interference(proj, min_volume, timeout_s=…)` | the **service methods**, not the `check_interference` tool — the tool's schema has no `timeout_s`, so a budget could not reach it. `SpecRunner._eval_interference` already takes this exact route. |
| `specs` | `service.specs.run(proj)` | `run` is unbounded, evaluates all three tiers, and is *the documented exit from every cached refusal* PRD-003 keeps — a cached `contract_error`, a cached `spec_declare` failure, and a memoized `budget_exceeded` gate verdict. See Decision 6. |
| `drawings` | the registered `generate_drawing` / `flat_pattern` tools | they carry the "is it drawable" guards, the PMI forwarding and the export paths; re-deriving them here would be a second contract to keep in sync. |

Two consequences worth stating out loud, because they are what makes this
feature small:

- **No new kernel handler.** `agentcad/kernel/` is untouched. Nothing in
  `core/checks.py` imports `OCP` or build123d, and a test asserts it.
- **No new status vocabulary.** `core/checks.py` imports `summarize`,
  `report_status` and `group_requirements` from `core/specs.py`. Per-row
  statuses are the same four PRD-003 defined (`pass` / `fail` / `skip` /
  `error`); summary counts are the same five keys (`passed`, `failed`,
  `skipped`, `errors`, `total`); stage and report statuses are the same three
  (`green` / `red` / `skip`). One product, one vocabulary.

### There is no separate `fem-smoke` stage

FR2 lists `fem-smoke` as a fifth stage. It is **folded into `specs`**, because
`SpecRunner.run` already evaluates the expensive tier: a `check_fem_static`
declaration measures on a machine with the `[fem]` extra and reports
`skip / fem_extra_missing` (with a hint) without it — which is precisely FR4 and
AC8. A standalone smoke stage would either duplicate that or run FEM on parts
nobody declared it for, which is a 600 s solve nobody asked for. Recorded as a
PRD divergence.

---

## Decision 2 — the report is one document, versioned, with rows called `items`

```jsonc
{
  "schema": 1,
  "agentcad": "0.1.0",
  "project": "rocketry",
  "source": {                       // what was measured, and how it was named
    "kind": "worktree" | "branch" | "tag" | "commit",
    "ref": "feat/nozzle" | null,    // as the caller typed it
    "sha": "a1b2c3d4…" | null,      // AgentCAD .history commit, when there is one
    "label": "refs/heads/main" | null,   // host-VCS provenance (--ref-label)
    "host_sha": "9f8e…" | null,          // host-VCS provenance (--sha)
    "dirty": false                  // uncommitted edits in the measured tree
  },
  "started": "2026-08-11T09:14:02Z",
  "finished": "2026-08-11T09:14:44Z",
  "duration_s": 42.3,
  "status": "green" | "red" | "skip",
  "complete": true,                 // false ⇒ the budget cut the run short
  "strict": false,
  "exit_code": 0,
  "summary": {"passed": 41, "failed": 0, "skipped": 2, "errors": 0, "total": 43},
  "stages": [ … ],
  "requirements": {"REQ-12": {"status": "pass", "checks": ["nozzle:throat_wall"]}},
  "warnings": ["…"],
  "errors": [ {"type": "...", "message": "...", "details": {}} ],   // harness-level
  "host": {"platform": "linux", "python": "3.12.4", "agentcad": "0.1.0",
           "fem": false, "sandbox": false, "pool_size": 1,
           "kernel_pool": "KernelPool"}
}
```

A stage:

```jsonc
{
  "name": "build",
  "status": "green" | "red" | "skip",
  "reason": null | "no_instances" | "not_declared" | "budget_exceeded" | "not_selected",
  "duration_s": 8.1,
  "summary": {"passed": 4, "failed": 0, "skipped": 0, "errors": 0, "total": 4},
  "items": [ … ]
}
```

An item — the atom of the whole report:

```jsonc
{
  "id": "build:nozzle",             // "<stage>:<subject>", deduped like assign_ids
  "kind": "part" | "instance" | "pair" | "check" | "drawing" | "flat_pattern" | "mate",
  "subject": "nozzle",
  "status": "pass" | "fail" | "skip" | "error",
  "message": "built in 3.1 s — 41 240 mm³, 112.4 g, valid",
  "reason": "fem_extra_missing",    // skip only, ALWAYS with a hint
  "hint": "install the optional [fem] extra …",
  "error": {"type": "script_error", "message": "…",
            "details": {"traceback": "…", "line": 37, "hint": "…"}},
  "details": {"cache_key": "…", "volume_mm3": 41240.0, "is_valid": true}
}
```

Three rules the shape encodes:

1. **`error` is the structured payload the tools already return.** FR7's
   "machine consumers get exactly what agents get" is literal: a build failure's
   `error` is `KernelError.to_payload()` verbatim — the same
   `details.traceback`, `details.line` and Error-Doctor `details.hint` that
   `update_part_script` hands back (AC4). A spec row's `error` is the spec
   report's. Nothing is re-worded on the way through.
2. **Rows are `items`, never `checks`.** `checks` is already three things in
   this codebase — the built-in gate name in `ProposalManager.gates()`, the
   `report["checks"]` list of spec rows, and the proposals UI's "Checks" tab. A
   fourth meaning would be a bug generator. (See "Naming traps".)
3. **The specs stage carries its own report whole.** `stages[specs].report` is
   the `SpecRunner.run` document unmodified (its `checks`, `parts`,
   `project_checks`, `requirements`, `warnings`, `errors`), and
   `stages[specs].items` is one item per check row. The top-level
   `requirements` map is that report's, so requirement traceability is not
   re-derived — it is passed through.

`schema: 1` is checked by a hand-rolled `validate_report(report) -> list[str]`
in `core/checks.py` (AC5). **No new dependency**: `jsonschema` is not in
`pyproject.toml` and this feature is not a good enough reason to add one; the
validator is ~60 lines of key/type/enum assertions and it is what the schema doc
is generated against.

---

## Decision 3 — the stages, and what each may claim

### `build`

One item per part in the manifest. `_ensure_built` returns `{"ok": True,
metrics, warnings, lods, cache_key}` or `{"ok": False, "error": payload}`.

- `ok: false` → `fail`, `error` = the payload (AC4).
- `ok: true` and `metrics.is_valid` false → `fail`, `message` names the
  invalidity; `details` carries `n_solids` and the per-solid volumes.
- otherwise `pass`; `details` carries `cache_key`, `volume_mm3`, `mass_g`,
  `n_solids`, `is_valid`, and `cached: bool`.
- A **reference (imported) part** still builds (`build_reference`) and is a
  normal `pass`/`fail`; it is *not* skipped here. Mesh-kind skipping belongs to
  the assembly stage, where the segfault risk actually lives.

**PRD divergence — "validity per solid".** FR2 says the build stage checks
"validity per solid". The kernel's `_metrics` reports `is_valid` for the
**whole shape only**; per-solid entries (`solids[]`) carry label, volume, mass,
bbox and centre of mass, but no validity flag, and `Shape.is_valid` is a
property of the compound. v1 therefore reports whole-shape validity plus the
per-solid metric rows, and says so. A per-solid flag is a kernel change
(`worker._metrics`), out of scope here.

### `assembly`

1. `service._resolved_instances(proj, timeout_s=remaining)`. A mate that cannot
   resolve raises `ValidationError` (unknown connector, missing target,
   reference part with no connectors, a cycle, a joint-range violation surfaced
   from the worker). That exception is **caught** and becomes one `fail` item of
   kind `mate` carrying the payload — never a traceback out of the runner. This
   mirrors `merge._validate`'s `integrity: [{"kind": "mate_error"}]` row.
2. One `pass` item per resolved instance (`details.mated: bool`).
3. `service.check_interference(proj, min_volume, timeout_s=remaining)`. Each
   returned pair is a `fail` item of kind `pair` whose `subject` is `"a ↔ b"`
   and whose `details` carry `{a, b, volume_mm3}` — the offending pair named in
   both renderings (AC2).
4. Each id in `skipped_mesh` is a `skip` item, `reason: "mesh_only"`, hint:
   booleans on an imported STL segfault OCCT, so it was excluded from the
   pairwise check. **This is load-bearing**: `pairs: []` with a non-empty
   `skipped_mesh` is not proof of no interference, and the report must never
   let a reader believe otherwise. `--strict` turns exactly this into a red.
5. Fewer than two instances → the whole stage is `skip`,
   `reason: "no_instances"`.

### `specs`

`service.specs.run(proj)` (no `ref=`; see Decision 5 for why). One item per
check row, carrying its `status`, `message`, `measured`, `limit`, `unit`,
`requirement`, `reason`, `hint` and `error` unchanged. A `fail` row is a red
naming the check with measured vs limit (AC3). Zero declarations → stage `skip`,
`reason: "not_declared"` — "a part that declares nothing is absent, not green"
travels up one level intact.

`service.specs` is read **lazily inside the method**, never captured in
`__init__` (see "Load order").

### `drawings`

For each **script** part:

- `generate_drawing(project, part_id, format="svg")` — must succeed. Failure is
  a `fail` item with the worker payload.
- `flat_pattern(project, part_id, format="svg")` — **only where the script
  defines it.** Presence is an `ast.parse` scan for a module-level
  `def flat_pattern(...)`, exactly the shape of `specs.declares_specs`: it never
  executes the script, so a project with no sheet metal costs nothing. A script
  whose AST will not parse falls back to a line-anchored `^def flat_pattern`
  text scan (PRD-003's fail-closed precedent) — and it has already failed the
  build stage anyway.
- A part that does not define `flat_pattern` has **no row**. Absent, not green,
  not skipped — the same rule PRD-003 applies to a spec-less part.
- A **reference** part is a `skip`, `reason: "not_script"` (drawings and flat
  patterns are script-only by contract).

Format is SVG. **DXF is deliberately not generated by the drawings stage** —
see Decision 7; it cannot participate in a byte-stability assertion and
generating it would only double the runtime.

Stage ordering is dependency- and cost-ordered: `build` first (everything else
needs shapes and its cache warms them), `assembly` second (bounded, and the
richest early signal), `specs` third (may include FEM), `drawings` last (pure
regeneration). `--stages build,assembly` selects a subset; unselected stages
appear as `skip`, `reason: "not_selected"`, so the report always lists all four
and a consumer never has to guess whether a stage was green or absent.

---

## Decision 4 — headless: one process, one warm kernel, no server

`cmd_check` reuses `cli._build_service` (FR1 names it) and then
`build_registry(service)`. It does **not** create the FastAPI app, bind a port,
build the chat engine or read an API key. The cost profile:

- **Kernel start ≈ 3 s per worker**, paid once. `KernelPool.start()` spawns the
  workers concurrently, so pool size does not multiply the wall clock, but it
  does multiply memory (~0.5 GB/worker). CI runners are 2-core/7 GB, so the
  action sets `AGENTCAD_KERNEL_POOL_SIZE: 1` by default — exactly what
  `.github/workflows/ci.yml` already does for pytest, for the same reason.
- **Parallelism is the pool's**, per FR5. The runner does not thread; it issues
  build requests with `affinity=part_id` (which `_rebuild` already does), so a
  pool of N spreads parts across N warm workers with stable routing.
- **The service is torn down in a `finally`**: `service.kernel.stop()`, then the
  worktree removal (Decision 5). A `KeyboardInterrupt` or a crashed worker must
  not leave a registered git worktree behind.
- **Sandbox**: on macOS the workers are seatbelt-confined as usual, and the
  writable roots must include the work dir — which is why `_build_service`
  grows one optional `extra_writable: list[str] | None = None` parameter,
  computed **before** the kernel starts (the profile is fixed at spawn). On
  Linux and Windows there is no confinement (`sandbox.supported()` is macOS
  only); PRD-006 is the real answer, and until then the docs say plainly that
  Linux CI runs part scripts with the same trust model as `pytest` on the same
  repo.

---

## Decision 5 — `--ref` materializes into a throwaway worktree with its own service

FR3 and AC7 are the hard constraint: checking a ref leaves the working tree
**and `.cache/`** byte-identical. `ProjectStore.cache_dir` hard-codes
`canonical_path_of(proj)/.cache` — deliberately, so a content-addressed entry is
shared by every branch — and `branches.pinned` does **not** redirect it. So
pinning alone cannot satisfy AC7.

**The mechanism, in order:**

1. Resolve the ref explicitly and record what it was — `history.resolve_branch`,
   then `resolve_tag`, then `looks_like_commit` + `has_commit`. Never
   `resolve_ref` alone: `rev-parse` searches **tags before branches**, so a tag
   named like a branch would silently answer for it (PRD-001 X1, and the reason
   `SpecRunner._pinned` resolves branches explicitly). A name that is both gets
   a `warnings[]` entry and resolves as the **branch**; `refs/tags/<x>` and
   `refs/heads/<x>` are accepted for disambiguation.
2. `history._run(canonical, "worktree", "prune")`, then
   `history._run(canonical, "worktree", "add", "--detach", <work>/<project>,
   <sha>)` — **`--detach` with the resolved commit**, never the branch name: a
   branch that is already checked out (its `.history/trees/<b>/`) cannot be
   checked out twice. This is `MergeOrchestrator._stage`'s exact mechanism.
   Every git call goes through `history._run` (hermetic env, 10 s timeout),
   never a raw `subprocess`.
3. Build a **second, ephemeral `AgentCADService`** rooted at the work dir,
   sharing the *same* kernel object, then `ephemeral.store.open(<tree>)` and
   `build_registry(ephemeral)`. Its `canonical_path_of` is the materialized
   tree, so `.cache/`, `exports/` and every authored read land inside the work
   dir and the user's project is untouched by construction.
4. **Two lines that are not optional:**
   - `ephemeral.bus.on_publish = None` — otherwise any `project_changed`
     publish commits a history snapshot *into the linked worktree*, i.e. into
     the user's real repository, from a command whose contract is "never
     mutates". This is the single most dangerous thing in the feature.
   - `ephemeral.store.branch_resolver = None` — `build_registry` constructs a
     `BranchManager` on the ephemeral service (git is on PATH), which would
     otherwise resolve paths against a `.history/agentcad/` sidecar that does
     not exist there and write one. The check runs on one tree; it needs no
     branch layer.
5. `finally`: `history._run(canonical, "worktree", "remove", "--force",
   <path>, check=False)` then `"worktree", "prune"`. Both are non-raising —
   a failure to clean up is a `warnings[]` entry, never a red check.

**The price, stated rather than hidden: a ref check runs on a cold cache.** The
work dir holds no `.cache/`, so every part is a real kernel build. That is the
literal cost of AC7's stronger property, and it is worth it: "a check never
mutates the project" becomes a sentence with no footnote. The PRD's own open
question ("should the action carry `.cache/` between runs? Ship without,
measure, then decide") is honoured — `--work-dir` chooses *where* the worktree
lands (filesystem, disk space, an `actions/cache` path), not whether a cache
survives. A `ProjectStore.cache_dir` override seam is the recorded phase-2
follow-up, after measurement.

**Committed state vs disk.** A ref check measures the *commit*. A branch whose
working tree has uncommitted edits is measured as of its last snapshot; the
runner detects this (`git status --porcelain` on that tree) and records
`source.dirty: true` plus a warning. It does **not** snapshot first — the
packet's `_checkpoint` may commit because it is producing review evidence on
the user's behalf; a check whose contract is "never mutates" may not.

Working-tree mode (`--ref` omitted) is the ordinary path: the live service, the
caller's branch tree, the shared `.cache/`, and `exports/` written where the app
writes them. Both are git-excluded (`_EXCLUDE_LINES = (".cache/", "exports/",
".history/", "*.tmp")`), so a local pre-flight check leaves `git status` clean
and warms the caches the next rebuild will hit. AC7 constrains ref mode; the
docs state the difference in one sentence.

---

## Decision 6 — report-honest, with `--strict` as the opt-in to the gate's philosophy

The PRD asks two things that sound contradictory until you name the audiences.
PRD-003's specs *gate* is unconditionally fail-closed: every skip is a `fail`,
because it decides a merge and "declared but not measured" is exactly the hole
it exists to close. FR4 here says the opposite: "skips are first-class …
`--strict` turns skips red."

Both are right, because they are different questions:

| | audience | question | skip means |
|---|---|---|---|
| `evaluate_specs` / the `specs` gate | `ProposalManager.merge` | may this land? | red, always |
| `agentcad check` / `run_checks` | an engineer or an agent | what is the state of this project? | skip, with a reason and a hint |
| `agentcad check --strict` | a CI policy | is everything measured *and* green? | red |

So **CLI check is report-honest** and `--strict` is how a repo opts into
fail-closed. `--strict` does **not** rewrite rows — a `skip` row stays a `skip`
with its `reason` and `hint`, because collapsing the four statuses is the one
thing PRD-003 forbids. It sets `report.strict: true`, adds
`report.strict_failures: [item_id]`, and changes only the derived
`status`/`exit_code`. A reader of `report.json` can always tell what was
measured from what was demanded.

This also settles the relationship with the proposal gate: **there is no
duplication, and no second opinion.** Both go through the same `SpecRunner` and
the same `.cache/<key>.specs.json` sidecars. The difference is the *reading*
(honest vs fail-closed) and the *bound* (unbounded vs `GATE_BUDGET_S = 30 s`).
And they compose: `SpecRunner.run` is documented as the exit from every cached
refusal PRD-003 keeps — a cached `contract_error`, a cached `spec_declare`
failure, a memoized `budget_exceeded` verdict. Which means **running
`agentcad check` on a proposal's source branch is literally the "run
`run_specs` on that branch, then read the gate again" that every budget-exceeded
gate summary tells a user to do.** Geometry CI is the thing that warms the
caches the 30 s gate then reads. That is stated in the docs, because it turns
two features into one workflow.

### Exit codes (FR1, AC5)

`exit_code(report) -> int` is one pure function, tested directly:

| code | meaning | when |
|---|---|---|
| **0** | green | every stage `green` or `skip`; no `fail`, no `error`; and if `--strict`, no `skip` rows either |
| **1** | red — the model is wrong | any part failed to build or built invalid · any interfering pair · any mate that will not resolve · any spec `fail` or `error` · any drawing that will not generate · `--strict` and any skip |
| **2** | harness — we could not produce a verdict | unknown project / part / ref · `--ref` without git · worktree materialization failed · the kernel would not start or died unrecoverably · `--budget` exhausted mid-run · the report file could not be written · any unexpected exception |

The one asymmetry worth defending: a spec `error` ("the check itself broke — we
do not know") is **1, not 2**. It is a fact about the model — a predicate that
raised, an instance id that no longer exists — and the report says so in the
row. Exit 2 is reserved for "this program could not do its job", and a caller
must be able to tell those apart: 1 means read the report and fix the design; 2
means fix the environment.

A blown budget exits **2 with the completed portion reported** (FR5):
`complete: false`, every unreached stage `skip / budget_exceeded`, and the JSON
and markdown are still written. A partial report is evidence; a missing one is
not.

### `--budget` is a deadline where it can be, and says where it cannot

PRD-003's hard-won rule is that a budget is a deadline, not a between-parts
courtesy: every kernel call under it takes `min(its own ceiling, remaining)`. We
honour that where the API allows — `_resolved_instances` and
`check_interference` both take `timeout_s`, and the assembly stage passes the
remaining budget. But `service._ensure_built` / `_rebuild` hard-code 300 s and
take no timeout, and the drawing tools hard-code 120 s. Threading a timeout
through them is a `service.py` edit and a tool-schema change.

So the honest statement, which goes in `--help` and in the docs: **`--budget` is
checked before each part and each stage; the worst-case overshoot is one
in-flight kernel call (≤ 300 s for a build, ≤ 120 s for a drawing).** It is not
"a budget you can walk past" — the pool's own per-request timeout is the hard
bound, and the deadline is re-read between every item. Making it exact is a
recorded follow-up (a `timeout_s` parameter on `_rebuild`), not a silent gap.

---

## Decision 7 — `--verify-determinism` measures `.acm`, and DXF is excluded with a reason

FR6: build every part twice, assert identical mesh cache keys and bytes. The
second pass must bypass the cache (a cache hit proves nothing), so it builds
into a **temporary cache directory** — the ephemeral-service trick again, at
part granularity — and then compares:

1. `cache_key` (the `sha256(content, params, density, tolerance, format)[:32]`
   from `service._cache_key_for`) — must be equal;
2. the `<key>.acm` bytes, and `<key>.faces.u32` where present — must be equal;
3. the metric scalars (`volume_mm3`, `mass_g`, `area_mm2`) — must be equal.

A mismatch is a `fail` item of kind `part` in a `determinism` pseudo-stage,
naming which of the three diverged and the first differing byte offset. This is
the standing regression guard for the core product guarantee, so it names things
precisely rather than saying "not deterministic".

**What is byte-stable, measured in this tree today:**

| artefact | stable? | evidence |
|---|---|---|
| `.acm` mesh, `.faces.u32` | **yes** | no `datetime`/`uuid`/`random`/`time()` anywhere in `kernel/acm.py`, `kernel/mesh.py`, `kernel/worker.py` |
| SVG drawings, SVG flat patterns | **yes** | `kernel/handlers/drawing.py` and `handlers/sheetmetal.py` write `atomic_write(out, svg.encode())`; no timestamp, no id |
| **DXF drawings and flat patterns** | **NO** | `ezdxf.new()` stamps `$TDCREATE = juliandate(datetime.now())` and fresh `$FINGERPRINTGUID` / `$VERSIONGUID` on every document (`ezdxf/document.py`) |
| the reports themselves | no | `specs._now()` / `checks._now()` stamp `generated` — by design; reports are read, not diffed |

Therefore: `--verify-determinism` covers meshes and metrics (FR6 as written);
the **phase-2 drawing byte-stability assertion covers SVG only**, and DXF is
excluded by name with the reason recorded. ezdxf exposes a fixed-date /
`CONST_GUID` path; adopting it in `_build_dxf` and `_dxf_flat` is the prerequisite
before DXF can join, and it is the concrete form of the PRD's "enforce
no-timestamp exporters before promoting the assertion out of phase 2" risk.

---

## Decision 8 — the proposal slot is a file plus a gate provider; nothing in `proposals.py` moves

PRD-002 left the seam open and said so in the code: `service.gate_providers` is
"empty here, appended to by PRD-003/**PRD-004** from their own `register()`", and
`ProposalManager.gates()` already emits a built-in placeholder
`{"name": "checks", "state": "skipped", "summary": "no checks posted"}`. A
provider whose closure is named `checks` **replaces** it by name, exactly as
PRD-003's `specs` provider replaces its placeholder.

**Storage.** `<canonical>/.history/agentcad/proposals/<pid>/checks.json`, one
atomic write (`ProjectStore._atomic_write`) via a `CheckStore` that reuses
`ProposalStore.dir_of` — proposals are canonical and branch-independent, and a
check result is workflow metadata, not model state, so it belongs exactly
there and `project_restore` must never rewind it. Posting also appends to
`audit.jsonl` through `ProposalStore.append_audit` (append-only, never a
read-modify-replace) and publishes `proposal_changed` with `reason: "checks"`.

```jsonc
{"schema": 1, "posted_at": "…Z", "posted_by": "ci", "actor_kind": "agent",
 "source": "feat/nozzle", "head": "a1b2c3d…", "status": "red", "exit_code": 1,
 "complete": true, "strict": false, "summary": {…},
 "stages": [{"name": "build", "status": "red", "summary": {…}}, …],
 "report": { … the full report … }}
```

**The gate.**

| posted state | gate | why |
|---|---|---|
| nothing posted | `skipped`, "no checks posted" | byte-identical to today's placeholder, so nothing changes until a check is posted |
| `head` ≠ the proposal's current source head | `pending`, "the posted check certifies `<sha>`; the source is now `<sha>` — re-run" | a stale green must not wear a commit it never measured |
| `status: red` | **`fail`** — blocks the merge | `ProposalManager.merge` blocks a `fail` and nothing else, so this is the only state that means "no" |
| `status: green` (and current) | `pass` | |

Note the deliberate divergence from PRD-003's provider: this one **does** return
`pending`, because it is reporting *someone else's* measurement rather than
making one. That is not a hole. The gates that actually enforce —
`validation` and the fail-closed `specs` gate — re-measure the current heads on
every `merge()` call, so a stale CI green cannot merge a newer red; the PRD's
own "status races" risk resolves exactly this way. A repo that wants CI itself
to block runs `--strict` and puts a branch-protection rule on the GitHub side.

### As-built (slice 6): `pending` is gone from this gate

The table above is **superseded on one row**, in the fail-closed direction, and
the change is PRD-003's X8 finding applied here before it could bite.

**A moved head is `fail`, not `pending`.** The argument above — "this gate
reports someone else's measurement, so `pending` is honest, and the gates that
enforce re-measure anyway" — describes the report layer correctly and the
*merge* layer wrongly. `ProposalManager.merge()` blocks a gate whose state is
`fail` and **nothing else**, so `pending` is merge-**permissive**: a green
posted against an older commit would have stood there, unblocking, while the
source moved on to commits it never measured. That is exactly the hole X8 closed
in the `specs` gate (`docs/superpowers/specs/2026-08-10-executable-design-specs-design.md`,
"As-built (second review, findings X5–X8)"), and it is closed the same way — a
stale posted report is `state: "fail"` whose summary names both SHAs and says
*re-run*. The relay to `validation`/`specs` is not a defence: those gates answer
their *own* questions, and nothing else would have answered CI's.

The as-built table:

| posted state | gate | why |
|---|---|---|
| nothing posted | `skipped`, "no checks posted" | unchanged, and byte-identical to the placeholder |
| `head` ≠ the proposal's current source head (or either is unresolvable) | **`fail`**, naming both SHAs and saying re-run | a stale green must not *wear* a commit it never measured — and `pending` would have let it |
| the report did not finish (`complete: false`, a blown `--budget`) | **`fail`** | an unfinished run is not a verdict; its own `exit_code` is 2 |
| the posted file will not parse / the gate cannot evaluate it | **`fail`** | evidence we cannot read is not "no evidence" |
| `status: red` | **`fail`** | the only state PRD-002 acts on |
| `status: skip` (nothing was measured at all) | `skipped` | an empty project is not a verdict either way |
| `status: green`, complete, current | `pass` | |

**`pending` is therefore never returned by this provider**, and the remaining
permissiveness is bounded and deliberate: a proposal nobody posted a check to is
`skipped`, so **this gate can only ever block a proposal that opted in by
posting one**. Absent is skipped (an optional CI report is not a
declared-but-unmeasured spec — PRD-003's `specs` gate already covers that,
fail-closed); everything after the first post is fail-closed. `ProposalManager`
still degrades a *raising* provider to `pending`, which is why this one catches
its own exceptions and answers `fail` instead.

**Matching a proposal.** `--proposal <id>` is explicit. `--auto-proposal` looks
up `proposals.list(proj)` for **active** proposals whose `source` equals the
branch that was checked; zero → a warning and no post (never an error: most
checks are not about a proposal); more than one → exit 2, because guessing which
proposal a verdict belongs to is worse than refusing.

**Identity.** The runner sets `locks.set_client_id("ci")` before it starts, so
`actor_kind` classifies the post as an **agent** action (it is: `human` is only
the `browser` identity), and so a CI run can never collide with a human's
per-client branch checkout in `checkouts.json`.

---

## Decision 9 — the GitHub Action checks the working tree; `$GITHUB_SHA` is provenance, not a ref

This is the correction the PRD needs most. FR10 says the action runs
`agentcad check --ref $GITHUB_SHA`. **It must not.** In a repo-hosted project
there are two different gits:

- the **host repo** (GitHub) which `actions/checkout` has already materialized
  at `$GITHUB_SHA` into the working directory, and
- AgentCAD's **`.history/`** repo, a per-project git dir that is *not* committed
  to the host repo (it is a git directory, and the docs tell users to
  `.gitignore` `.history/`, `.cache/` and `exports/`).

So on a runner there is no `.history` to resolve `$GITHUB_SHA` against, and
`--ref $GITHUB_SHA` would exit 2 on every run. What the action actually does is
**check the working tree** — which *is* the ref, already checked out — and pass
the host SHA as provenance:

```yaml
agentcad check --project . --sha "$GITHUB_SHA" --ref-label "$GITHUB_REF_NAME" …
```

`--sha` / `--ref-label` populate `source.host_sha` / `source.label` and appear in
the markdown header. `--ref` stays the local/server mechanism (Decision 5),
where `.history` exists and branches and proposals are real. Recorded as a PRD
divergence.

### What ships

**`.github/actions/agentcad-check/action.yml`** — a **composite** action in this
repo (FR10's "in-repo first, marketplace later"). Composite, not Docker: a
Docker action would have to bake the ~2 GB OCCT layer and would defeat
`setup-uv`'s cache.

| input | default | |
|---|---|---|
| `project` | `.` | project directory (or a name with `projects-dir`) |
| `projects-dir` | — | for repos holding several projects |
| `stages` | `build,assembly,specs,drawings` | |
| `strict` | `false` | |
| `budget` | `` (unbounded) | seconds |
| `verify-determinism` | `false` | |
| `pool-size` | `1` | → `AGENTCAD_KERNEL_POOL_SIZE` |
| `agentcad` | `agentcad` | pip requirement; `.` uses the checked-out repo |
| `python-version` | `3.12` | |
| `fem` | `false` | install the `[fem]` extra |
| `upload-artifacts` | `true` | |
| `github-token` | `` | when set, posts a commit status (phase 2) |

| output | |
|---|---|
| `status` | `green` / `red` |
| `exit-code` | `0` / `1` / `2` |
| `report-json` / `report-md` | paths |
| `failed-stages` | comma-separated |

Steps: `astral-sh/setup-uv@v5` (`enable-cache: true`,
`cache-dependency-glob: uv.lock`) → **Linux OCCT system libraries** (the exact
six packages `ci.yml` already installs: `libgl1 libglu1-mesa libxrender1
libxcursor1 libxft2 libxinerama1` — OCCT needs an X/GL stack even headless, and
that list is hard-won) → install agentcad → `agentcad check …` with
`continue-on-error` so the summary is always written → `report.md` appended to
`$GITHUB_STEP_SUMMARY` → `actions/upload-artifact` → set outputs → re-raise the
exit code.

**Caching layers (designed, not measured):**

| layer | mechanism | v1 |
|---|---|---|
| L1 — the ~2 GB OCCT wheels | `setup-uv` `enable-cache`, keyed on `uv.lock` | **yes** — the layer that matters, already proven in `ci.yml` |
| L2 — the resolved `.venv` | `actions/cache` on `.venv`, keyed on OS + `uv.lock` + python | no — a warm uv cache makes `uv sync` cheap; measure first |
| L3 — AgentCAD's `.cache/` geometry cache | `actions/cache` on the project's `.cache/` | **no** — the PRD's explicit open question. The action documents the path so a user can add it themselves, and Decision 5 records the seam that would make it work for `--ref` runs |
| L4 — apt packages | — | no; fast, and apt cache restore is fragile |

**Runner requirements** (documented, per FR10): ~2 GB installed for OCCT wheels
plus the uv cache — budget **8 GB free disk**; ~0.5 GB RAM per kernel worker on
top of the runner baseline, so `pool-size: 1` on the standard 2-core/7 GB
runner and raise it only on a larger one.

**Matrix / OS scope for v1: `ubuntu-latest` only** for the dogfood workflow. The
pytest matrix already covers macOS (full PR suite) and Linux/Windows
(portability), and geometry CI's job is to prove the *examples* stay green, not
to re-litigate cross-OS behaviour. Cross-OS byte-identity is an explicit PRD
non-goal; a cross-OS check would compare metrics with a tolerance, which is
PRD-012's matrix. The action itself is documented as supported on Linux and
macOS runners; Windows is untested in v1 and says so.

**`.github/workflows/geometry-ci.yml`** (FR11, AC1): a matrix over the bundled
examples, one job each, using the local action with `agentcad: .`. On
`push`/`pull_request` it runs the light examples (`construction`,
`prototyping`, `rocketry`, `fasteners`); `engine` (33 parts, 65 instances)
joins the nightly `schedule` job, mirroring how `ci.yml` already defers the
engine example. **PRD divergence:** the PRD says "the three bundled examples";
there are five today.

**Untrusted forks.** The workflow triggers on `pull_request`, never
`pull_request_target`, and requests no secrets — a fork's part scripts are
arbitrary Python running on the runner, and on Linux there is no seatbelt.
Documented in `docs/geometry-ci.md` under a trust-model heading, with PRD-006 as
the real answer.

---

## Surfaces

### CLI

```
agentcad check [--project PATH|NAME] [--projects-dir DIR] [--ref REF]
               [--stages build,assembly,specs,drawings]
               [--report report.json] [--md report.md]
               [--strict] [--verify-determinism] [--budget SECONDS]
               [--min-volume MM3] [--work-dir DIR]
               [--proposal ID | --auto-proposal]
               [--sha SHA] [--ref-label NAME] [--quiet | --json]
```

`--project` accepts a path or a name, reusing `cmd_export`'s idiom (`"/" in
project or project.startswith(".")` → `service.open_project`). Human output is a
live stage table on stderr and the summary on stdout; `--json` prints the report
to stdout instead (so `agentcad check --json | jq` works) and exit codes are
unchanged in every mode.

### Tool (FR12)

`run_checks {project, ref?, stages?, strict?, budget?, proposal?}` → the full
report as post-state, structured errors embedded. **No new error type**: a red
check is data. Harness failures surface as the existing families
(`not_found_error`, `validation_error`, `kernel_error`). AC9 asserts the report
returned over MCP is identical to the CLI's for the same inputs, modulo the
`generated`/`duration_s`/`host` fields (which the test normalizes).

### Route (FR12)

- `POST /api/projects/{proj}/checks` — body whitelisted to `{ref, stages,
  strict, budget, proposal}` (never `**body`; the registry rejects unknown and
  `null`-typed args) → the report.
- `GET /api/projects/{proj}/checks` — the last report this process produced,
  or the last one posted to a proposal when `?proposal=<id>`; `404` when there
  is none.

### Event (FR12)

`{"type": "check_finished", "project", "ref", "status", "exit_code",
"summary", "duration_s"}` on the WebSocket channel, published via
`service.bus.publish` when a run completes — including a red one and a
budget-truncated one.

### Load order — the trap this pack has to dodge

`tools._load_tool_packs` walks `pkgutil.iter_modules` **alphabetically**. A pack
named `tools_checks.py` would load at `c`, *before* `tools_proposals` (`p`) —
which is where `service.gate_providers = []` is assigned **unconditionally**, so
anything appended earlier would be silently thrown away, and `service.proposals`
would not exist yet.

The pack is therefore named **`agentcad/core/tools_run_checks.py`**, after the
tool it registers, which also puts it at `r`: after `tools_proposals` (so
`gate_providers` and `service.proposals` exist and the gate installs the PRD-003
way) and before `tools_specs` (`s`) and `tools_versioning` (`v`) — so
**`service.specs` and `service.branches` must be read inside the methods, never
captured in `__init__`.** This is PRD-003's exact trap, one notch worse, and it
gets a test: registering the packs in a fresh registry must leave a gate named
`checks` in `service.gate_providers`.

### Error shapes

| situation | shape |
|---|---|
| unknown project / part | `NotFoundError` → 404, CLI exit 2 |
| `--ref` on a project with no git | `ValidationError("checking a ref needs git…")` → 422, exit 2 |
| ref that is neither branch, tag nor commit | `NotFoundError` naming what was searched, exit 2 |
| ambiguous branch/tag name | resolves as the branch + a `warnings[]` entry |
| worktree add/remove failure | `HistoryError` wrapped as `ValidationError` (add) / a warning (remove) |
| a stage that raised unexpectedly | one `error` item + a `report.errors[]` entry with `fatal: true`; the run continues, the report is `red`, exit 1 |
| a part that fails to build | `fail` item, `error` = `KernelError.to_payload()` |

---

## Data flow — the AC2 walk

1. `tests/test_prd004_acceptance.py` copies `examples/construction` (never in
   place) and edits an instance transform so two parts overlap.
2. `CheckRunner.run("construction")` — working-tree mode, live service.
3. `build`: three parts, `_ensure_built` each, all `pass` (the cache is warm
   from the copy or built once). Stage `green`.
4. `assembly`: `_resolved_instances` returns four instances, no mates → no
   kernel call; `check_interference` returns one pair
   `{"a": "beam_1", "b": "plate_1", "volume_mm3": 812.4}`.
5. That pair becomes `{"id": "assembly:beam_1↔plate_1", "kind": "pair",
   "status": "fail", "message": "beam_1 and plate_1 overlap by 812.4 mm³",
   "details": {"a": "beam_1", "b": "plate_1", "volume_mm3": 812.4}}`. Stage
   `red`.
6. `specs`: construction declares none → stage `skip`, `reason:
   "not_declared"`. `drawings`: three SVGs, all `pass`.
7. `report_status(summarize(all items))` → `red`; `exit_code(report)` → `1`.
8. `report.json` validates against the schema and contains both instance names;
   `report.md` contains a `## Failures` section naming the same pair with its
   volume. The test asserts on both renderings and on the exit code.

---

## Testing strategy

| file | covers |
|---|---|
| `tests/test_checks.py` | pure layer: `summarize`/`exit_code`/`validate_report`/markdown rendering over fixture reports; the `flat_pattern` presence scan; stage selection; strict-mode derivation. No kernel. |
| `tests/test_checks_pipeline.py` | the four stages against copies of `examples/prototyping` and `examples/construction` (`slow`); AC2, AC3, AC4. |
| `tests/test_checks_ref.py` | `--ref` on branch / tag / commit; AC7's byte-identity assertion (hash every file under the project dir and `.cache/` before and after); worktree cleanup; the dirty-tree warning; ambiguous branch/tag. `integration`, `portability`, `skipif(git is None)`. |
| `tests/test_checks_cli.py` | exit codes 0/1/2 via `subprocess` on `agentcad check` (AC5); `--json`; `--report`/`--md` written; a blown `--budget` exits 2 with a partial report. |
| `tests/test_checks_api.py` | the tool + routes + `check_finished` event; `TestClient(base_url="http://127.0.0.1")`, `create_app(..., extra_allowed_hosts={"testserver"})`; AC9's MCP-vs-CLI identity. |
| `tests/test_checks_gate.py` | the `checks` gate: absent → `skipped`, red → `fail` blocks `merge`, stale head → `pending`, green+current → `pass`; the load-order assertion. |
| `tests/test_prd004_acceptance.py` | AC1–AC10 end to end. |

Standing rules: session-scoped `kernel` fixture; examples always on a
`shutil.copytree(..., ignore=ignore_patterns(".cache", "exports"))` copy; FEM
rows exercised with `importorskip` so the suite is green **without** the extra
(AC8 tests both directions by simulating the skip rather than requiring the
extra); git-touching tests marked `integration` + `portability` and skipped when
git is absent; the real service (not `make_test_service`) wherever history
matters, with the autouse `_reset_context` fixture that rebinds
`locks.client_id_var` and `branches.pinned_tree_var`.

---

## Risks and open questions

1. **Cold cache on `--ref`** (Decision 5). The engine example is 33 parts / 65
   instances; a cold ref check of it is minutes, which is why the dogfood
   workflow runs it nightly. Mitigations shipped: `--stages`, `--budget`, pool
   parallelism. Follow-up: a `cache_dir` seam, after measurement.
2. **`--budget` cannot preempt an in-flight build or drawing** (Decision 6).
   Worst case overshoot: one call, ≤ 300 s. Follow-up: `timeout_s` on
   `_rebuild`.
3. **DXF is not byte-stable** (Decision 7) — measured, not assumed. Blocks DXF
   from the phase-2 assertion until ezdxf's fixed-date/`CONST_GUID` path is
   adopted.
4. **`git worktree` admin state leaks if the process is killed.** Mitigated by
   `prune` before every add and `remove --force` in a `finally`; a stale entry is
   harmless and self-healing, but it is a real footprint in the user's repo and
   the docs say where to look.
5. **The ephemeral service is one forgotten line from committing to the user's
   repo** (`bus.on_publish = None`). This gets its own test: run a ref check and
   assert `history.head(canonical)` is unchanged and `git worktree list` is back
   to its previous length.
6. **`$GITHUB_STEP_SUMMARY` is capped at 1 MiB.** The markdown renderer caps
   rendered failures (default 50) and appends "+N more — see `report.json`".
   A 33-part project with a broken shared toolkit import would otherwise blow
   past it.
7. **Linux CI has no sandbox.** Untrusted-fork CI is arbitrary code execution;
   `pull_request` only, no secrets, PRD-006 is the answer.
8. **`is_valid` is whole-shape only** — FR2's "validity per solid" is not
   measurable today (Decision 3).
9. **Open:** should `run_checks` be asynchronous (a job id + `check_finished`)
   rather than a blocking tool call? v1 is synchronous with a budget, because a
   job registry is PRD-020's fleet/queue concern and an MCP client that times
   out can re-read `GET /api/projects/{p}/checks`.

### Naming traps (live collisions in the tree today)

- **`checks`** already means three things: the built-in gate name in
  `ProposalManager.gates()`, `report["checks"]` in every `SpecRunner` report,
  and the proposals UI "Checks" tab. Our per-stage rows are **`items`**. The
  gate provider closure *is* named `checks` — deliberately, so it replaces the
  placeholder by name.
- **`status`** is the four-value row status (`pass|fail|skip|error`);
  **`state`** is the gate's (`pass|fail|pending|skipped`). Do not mix them; the
  gate object uses `state`.
- **`service.checks`** is new and free; `service.specs`, `service.proposals`,
  `service.packets`, `service.branches`, `service.merges` are taken.
- **`tools_checks.py` must not exist** — see "Load order".

---

## PRD divergences to fold back

1. `fem-smoke` is not a stage; FEM rides `specs` tier 3 (Decision 1).
2. "validity per solid" → whole-shape `is_valid` + per-solid metric rows
   (Decision 3).
3. The action checks the working tree and takes `$GITHUB_SHA` as provenance;
   `--ref` is the local/`.history` mechanism (Decision 9).
4. "the three bundled examples" → five exist; four run per push, `engine`
   nightly (Decision 9).
5. `--budget` is a between-item deadline with a named worst-case overshoot, not
   an exact deadline (Decision 6).
6. Phase-2 drawing byte-stability covers SVG only (Decision 7).
7. `docs/roadmap.md` points PRD-004 at `prd/pending/`; the file is in
   `prd/in-progress/`.
8. The `checks` gate never answers `pending`: a stale posted report is `fail`
   with a re-run sentence, because `merge()` blocks `fail` and nothing else
   (Decision 8, as-built — PRD-003's X8 lesson).

---

## As built — the second review (W1–W10)

An independent review of the finished feature returned CHANGES-REQUIRED with
ten reproduced findings. What changed, and why the design text above is now
read with these amendments:

1. **`--work-dir` cannot reach the project (W1, critical).** Decision 5's
   containment argument covered the *ephemeral service* and forgot the *work
   dir*. `<work-dir>/<project>` was `rmtree`'d before `worktree add`, so
   `--work-dir .` from the projects root deleted the live project — uncommitted
   files included — and the teardown deleted it again. Two changes, either of
   which alone would have been enough and both of which are now in:
   a work dir that **is, contains or sits inside** the project or the projects
   root is refused (`ValidationError` naming both paths, exit 2), and a run
   materializes into a **unique subdirectory it creates itself**
   (`<work-dir>/agentcad-check-<pid>-<rand>/<project>/`), so nothing is ever
   deleted that the run did not make. The docs' "a dir you pass is left alone"
   is now literally true.
2. **The budget bounds every stage (W2, W3).** It bounded `build`, `drawings`
   and the two assembly calls only: `SpecRunner.run` was called with no
   deadline (PRD-003 has taken one since its own gate work — `run()` now
   forwards it to `_report`, a passthrough and not a new mechanism), and one
   determinism row made four unpreemptable kernel calls behind a single check.
   The deadline is now read before every kernel call, with a `_MIN_CALL_S`
   floor below which no call is issued — because a call that cannot finish only
   overshoots and is then reported as a *timeout*, i.e. as a red row for
   something the budget did. An item the deadline stopped is
   `skip`/`budget_exceeded` and `complete: false` (exit 2), never an `error`.
   The "one in-flight kernel call" sentence in the docs, `--help` and
   `AGENTS.md` was false when written; it is true now.
3. **The third live seam (W4).** `bus.on_publish` and `store.branch_resolver`
   were nulled; `build_registry` also installs a `write_guard` whose
   `ensure_checkout` materializes a branch tree in the repository the worktree
   is linked to — the user's. It was inert only because a check happens not to
   write. Nulled, with the same comment discipline and a pinning test.
4. **The CLI's post-run steps are inside the exit-code mapping (W5).**
   `_write_check_outputs`/`_post_check`/`_print_check` ran after the
   `try/except`, so a mangled proposals index left a traceback and exit 1 — the
   code reserved for "the model is wrong".
5. **Posting is serialized against the lifecycle (W6).** `post_target`
   reconciled under `ProposalManager._lock` and released it before the write, so
   a merge landing in between got post-decision evidence. Resolution, terminal
   re-check, write and audit now happen under that lock, `record_packet`'s
   mechanism for `record_packet`'s reason; a failed audit append rolls the slot
   back, so a gate never reads evidence with no audit line behind it.
6. **A missing check step is a harness error (W7).** With an empty
   `steps.check.outputs.exit-code` the action's last step reported "red —
   failed stages: unknown", blaming the user's geometry for a failed install.
   An absent verdict now exits 2 with wording that cannot be confused with red.
7. **`--strict --verify-determinism` is no longer red by construction (W8).**
   The DXF row is a skip *by construction*; `--strict` asks whether anything
   measurable went unmeasured, so an unconditional skip is not a candidate.
   Rows carry `strict_exempt`, `finalize_report` skips them, and the row stays
   visible with its reason, its hint and its place in the counts. The design's
   earlier note (and `AGENTS.md`) said the combination was permanently red;
   that is now a fixed behaviour, not a documented wart.
8. **The action's check step is executed by a test (W9).** It was regex-scraped
   only; a test now runs its body verbatim with a real `agentcad` on `$BIN`,
   over a project path containing a space, and asserts the argv, the quoting
   and the `set +e` capture. Two input guards came with it: a requirement
   starting with `-` and a newline in any `$GITHUB_OUTPUT` value are refused.
