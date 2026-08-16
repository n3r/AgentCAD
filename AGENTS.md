# AGENTS.md — working on AgentCAD

Guidance for any AI agent (or human) contributing to this repo. Read this
before making changes. It is the canonical contributor guide; deeper design
docs live in `docs/`.

## What this project is

**AgentCAD** is an agentic-first parametric CAD system for complex engineering
parts (rocketry, construction, prototyping). The core bet: **the model is
code** — every part is a small parametric Python script using
[build123d](https://build123d.readthedocs.io) (OpenCascade/OCCT B-rep), which
is the representation agents read, write, and diff best. The **kernel is the
referee**: every change is validated by real geometry (validity, volume, mass,
interference), and failures return as structured data so agents self-correct.

The full capability surface is exposed three ways from one registry: a browser
UI, an **MCP server** (for Claude Code and other MCP clients), and a built-in
**chat agent**. All three are peers over the same service layer.

Status: working, `v0.1.0`, ~300 tests. macOS today; the architecture is pure
Python + browser UI so Windows/Linux are reachable (packaging only).

## Quick start

```bash
make setup        # uv sync  (installs build123d/OCCT wheels, ~2 GB, one time)
make test-fast    # two-worker suite excluding broad/timeout-driven slow tests
make test-pr      # required PR gate; defers exhaustive bundled-engine coverage
make test         # full two-worker suite; needs the kernel
make test-portability  # OS-sensitive filesystem/process/kernel smoke suite
make run          # start the server AND open the browser UI (port 8630)
make serve        # headless server only
make app          # build dist/AgentCAD.app (macOS launcher)
uv sync --extra fem   # optional: enable structural FEM (gmsh/scikit-fem/meshio)
```

CLI: `agentcad serve|open|mcp|new|export|check` (see `agentcad/cli.py`). Port is
`8630`, persisted in `~/.agentcad/config.json`; kernel pool size via
`AGENTCAD_KERNEL_POOL_SIZE` (default `min(3, cores//3)`).

MCP registration for Claude Code:
```bash
claude mcp add agentcad -- uv --directory /path/to/cad_claude run agentcad mcp
```

## Architecture (two processes + peer clients)

```
Claude Code (MCP) ─┐         Browser UI ─┐        Chat agent ─┐
                   │ HTTP               │ REST+WS            │ in-process
                   ▼                    ▼                    ▼
        ┌───────────────────────────────────────────────────────┐
        │  FastAPI server (127.0.0.1 only)                       │
        │  ToolRegistry ─► AgentCADService ─► ProjectStore(files)│
        │                        │ line-delimited JSON-RPC       │
        │              Kernel worker subprocess(es)              │
        │              (warm build123d/OCCT, restartable pool)   │
        └───────────────────────────────────────────────────────┘
```

- **`agentcad/kernel/`** — the geometry process. `worker.py` imports build123d
  once (warm, ~3 s) and serves JSON-RPC over stdin/stdout; `client.py` /
  `pool.py` own the subprocess(es) with per-request timeout and
  kill-and-respawn; `mesh.py` tessellates to the `ACM1` binary format
  (`acm.py`); `refload.py` loads imported CAD. **Only this package imports
  `OCP`.** The server process must never import build123d/OCP.
- **`agentcad/core/`** — `service.py` (the application service every client
  goes through: rebuild orchestration, content-hash mesh cache, EventBus),
  `project.py` (filesystem project store, atomic writes), `model.py`
  (dataclasses + `AppError` subclasses), `tools.py` (the ToolRegistry),
  `materials.py`, `templates.py`.
- **`agentcad/server/app.py`** — FastAPI: thin REST wrappers, a WebSocket
  event channel, the generic `/api/tools/{name}` passthrough (what MCP
  proxies), and static frontend hosting.
- **`agentcad/agent/`** — `mcp_server.py` (stdio MCP proxy, auto-starts the
  server) and `chat.py` (Anthropic tool-use loop).
- **`agentcad/toolkit/`** — helpers importable *from part scripts*:
  `safe_fillet`/`safe_shell`/`safe_bool`, `sketch` (constraint solver),
  `threads` (bd_warehouse).
- **`frontend/`** — static ES modules, no bundler; Three.js + CodeMirror
  vendored under `frontend/vendor/`.

Read `docs/architecture.md` for the full picture and a rebuild data-flow walk.

## The extension-point contract — HOW TO ADD A FEATURE

v2 features are vertical slices that plug into pre-wired discovery points. **Do
not edit `worker.py` / `tools.py` / `app.py` / `service.py` cores to add a
feature** — add packs:

1. **Worker handler pack** — `agentcad/kernel/handlers/<name>.py` exporting
   `HANDLERS: dict[str, callable]` or `register(toolbox) -> dict`. The
   `toolbox` (see `worker.WORKER_TOOLBOX`) hands you `b3d`, `build_shape`,
   `build_shape_ns`, `metrics`, `shape_volume`, `place`, `export_shape`,
   `atomic_write`, `tessellate`, `WorkerError`, and the error-type constants.
   Handlers return JSON-able dicts; raise `WorkerError(type, msg, {details})`.
   Merged at worker startup (`worker._load_handler_packs`).
2. **Tool pack** — `agentcad/core/tools_<name>.py` exporting
   `register(registry, service)`. Use `agentcad.core.tools.schema(props,
   required)` and `with_hint(result)`. Register a tool **only if it can run**
   (e.g. FEM tools skip themselves when the extra is absent).
3. **Route pack** — `agentcad/server/routes_<name>.py` exporting an
   `APIRouter` named `router` or `build_router(service, registry)`. Mounted
   under `/api`. Raise `NotFoundError`/`ValidationError`/`ConflictError`
   (mapped to 404/422/409). **Whitelist request-body keys** you forward to a
   tool — don't `**body` (the registry rejects unknown/`null`-typed args).
4. **Toolkit module** — `agentcad/toolkit/<name>.py` for part-script helpers;
   re-export the public name from `toolkit/__init__.py` if it's a top-level
   symbol.

`AgentCADService` also exposes seams you extend rather than fork:
`self.materials` (density resolver), `mates.resolve` (assembly mates), the
kernel `affinity=` kwarg (pool routing), `ProjectStore.write_guard` (turn
locking), and `self.history`/`self.undo_cursor` (git-backed snapshots +
undo/redo — a mutating pack needs NO per-call hook: publishing
`project_changed` after its write is what triggers the snapshot).

## The part-script contract

A part is a plain build123d script defining `PARAMS` (numeric specs with
`default`, optional `min`/`max`/`unit`/`description`) and `build(p)` returning
a `Part`/`Solid`/`Compound`. Optional additions: `connectors(p, part)` for
assembly mates, `SPECS` for executable design intent
(`from agentcad.toolkit.specs import check_wall, …`), and
`from agentcad.toolkit import …` for robust ops. Full
contract + cheat-sheet: `docs/part-authoring.md` and the `part_template` tool.

## build123d / OCCT gotchas (hard-won — read before touching geometry)

- **Version is pinned** (`build123d>=0.11.1`, via `uv.lock`). The 0.x API
  drifts; the test suite is the compatibility harness. Don't bump casually.
- **Boolean intersection volume**: `Shape.intersect()` returns a `ShapeList`
  (no `.volume`). Use the `&` operator for a single `Part` (empty `Compound`
  when disjoint). See `worker.handle_interference`.
- **Nested `Compound.volume` undercounts** (reports only the first child
  subtree). Sum over `shape.solids()` — `worker._shape_volume` does this.
- **Rotations are intrinsic XYZ Euler degrees** everywhere: `build123d
  Location(pos, rot)`, the manifest `rotation_deg`, and THREE.Euler `'XYZ'`
  all agree. `service._apply_transform` applies the rotations Z→Y→X to the
  point (a round-trip is covered by `tests/test_kernel.py`).
- **Imported STL is one welded mesh face with no surface** (`BRep_Tool.
  Surface_s(face) is None`). It needs **crease-angle normals**, not the smooth
  per-vertex average B-rep faces use, or shading melts (see `mesh.py`).
- **STL booleans segfault OCCT** — mesh-kind references are blocked from
  booleans and excluded from interference (`skipped_mesh`).
- **`is_valid` and `is_manifold` are properties, not methods.**
- Fillet/shell/boolean fail readily; prefer the `toolkit.safe_*` helpers, and
  the Error Doctor (`kernel/error_doctor.py`) turns raw OCCT errors into hints.

## Branching gotchas (PRD-001 — read before touching the store or history)

- **Branch worktrees live at `.history/trees/<branch>/`** — *not*
  `.history/worktrees/`, which is git's own per-worktree admin directory.
  Sidecars (default branch, per-client checkouts, tag referrers, a staged
  merge) live at `.history/agentcad/`. Both are inside GIT_DIR, so they are
  never committed.
- **`ours` = the TARGET branch, `theirs` = the SOURCE** everywhere (payloads,
  tool descriptions, UI labels), exactly like `git merge <source>`. Getting it
  backwards silently discards someone's work.
- **`.cache/` is canonical and shared by every branch** (content-addressed
  keys). Use `store.canonical_path_of` for derived data and `store.path_of`
  for authored state; `store.lock_key` — not the bare project name — for turn
  locks and undo stacks.
- Authored-state access goes through the `ProjectStore.branch_resolver` seam,
  so nothing else needs to know about branches. `BranchManager.pinned()` is the
  only override, and only the merge validation pass uses it.
- **`project.json` never merges line-wise.** It is always re-merged by
  `core/manifest_merge.py` (pure, I/O-free), even when git thought the file
  merged cleanly.
- Every git call goes through `ProjectHistory._run` (hermetic env, 10 s
  timeout, never raises into a save) — never a raw `subprocess`. Merging needs
  **git 2.38+**; branches and tags do not, and with no git at all the
  versioning pack registers nothing.

## Proposal gotchas (PRD-002 — read before touching proposals or the packet)

- **Proposals are canonical and branch-independent**, at
  `.history/agentcad/proposals/<id>/` — they are workflow metadata, not model
  state, so `project_restore` must never rewind them and every branch sees the
  same list. Never write proposal state into a working tree.
- **`audit.jsonl` is appended, never atomically replaced.** FR14 makes it
  append-only; a read-modify-replace cycle breaks that and can truncate the log
  on a crash. Everything else under the proposal directory is an atomic write.
- **The packet degrades, it never raises.** A per-part stage that fails
  (build, metrics, render, geometric diff) writes its structured error into the
  payload — `build.<side>.ok: false`, `geom_diff.available: false`,
  `warnings`/`errors` — and the packet still returns `ok: true`. `ok: false`
  means *no* packet could be produced (unreadable manifest, unknown ref). A
  packet is evidence; "the new side does not build" is evidence.
- **`geom_diff` volumes come from the toolbox's `shape_volume`** (the solids
  sum), not `.volume` — a boolean result is routinely a nested Compound — and a
  **mesh-kind (imported STL) side is never booleaned** (it segfaults OCCT); it
  comes back `skipped: "mesh"`.
- **Matched renders need the explicit `frame=`.** `render_acm` auto-fits per
  mesh, so two renders of different geometry do not superimpose; the packet
  passes the union of both world bboxes to both sides.
- **`allow_invalid` overrides the kernel validation gate only** — never the
  approvals policy. One field must not mean two unrelated things.
- **`actor_kind` is `human` only for the `browser` identity.** The chat dock is
  a human asking an *agent*, so those actions are the agent's. It is
  bookkeeping, not authentication, until PRD-005.
- A packet is generated from the **branches' existing worktrees**
  (`branches.tree_of` + `branches.pinned`) and refuses a dirty tree, so its
  pinned head SHAs always describe the bytes it measured.
- **A terminal proposal is never measured again.** Merged/closed means the
  branches have moved on, so a packet built then would describe the merged
  target under this proposal's name. The merge freezes the packet — or freezes
  the *absence* of one (`{frozen: true, generated: null, ok: false, note}`) —
  and `proposal_packet`/`proposal_render` refuse to produce anything new. A
  frozen packet serves only the renders stored beside it.
- **`packet.json` and `proposal.json` have ONE write order**:
  `ProposalManager.record_packet`, under the manager's `RLock`. Never write
  either from the builder directly — a build outlives a merge, and a build
  overtaken by one is discarded, not published.
- **Only `approve`/`request_changes` count towards the approvals gate.** A
  `comment` changes no state, so it must not change the count either.
- **A merge staged by a proposal is HELD** (`merge.json`'s `held_by:
  "proposal:<id>"`, the one thing PRD-002 adds to `merge.py`). `resolve_merge`
  records resolutions and, at zero outstanding, answers `{held: true}` without
  finalizing; only `MergeOrchestrator.finalize_held` lands it, and only
  `proposal_merge` calls it — after re-evaluating the gates. Never "fix" that
  by finalizing at zero outstanding: the gate would become something you can
  walk past by conflicting. `proposal_merge` also records `staged_merge`, and
  the proposal still reconciles itself on the next read if such a merge lands
  behind its back — a safety net for merges staged before the hold existed.
- **`proposal_merge` holds the SOURCE branch's turn** across gate evaluation
  and the merge (`_holding_source`), because a gate result is a statement about
  one source head. The orchestrator takes the TARGET's turn inside. That order
  is fixed, and `TurnLock` raises instead of blocking, so it cannot deadlock.
- **A packet re-reads both heads when it finishes measuring.** The heads are
  read up front and the metrics/renders/booleans come off the live worktrees
  after: a head that moved discards the build and takes it again, and one that
  moves twice persists the packet marked `stale`. Never label evidence with a
  head you have not re-checked.
- **A git read that FAILS is not empty evidence.** A `cat-file` that returns
  non-zero is not "this side deleted `project.json`" and a failed `git diff` is
  not "no script changed": both are `errors[]` entries with `fatal: true` that
  force `ok: false` (the per-part FR8 degradation is separate and keeps
  `ok: true`).

## Spec gotchas (PRD-003 — read before touching specs, the runner or the gate)

- **Specs are code in the tree, not manifest state** — part scope in
  `parts/<id>.py`'s `SPECS`, project scope in a root `specs.py`. `git add -A`
  tracks them, so branching, restore, undo and merge are free. Use
  `store.path_of` (authored, branch-resolved), never `canonical_path_of`.
- **A failing spec never fails a rebuild.** Geometry lands, `ok` stays `true`,
  the failure is signal. `pass`/`fail` (measured), `skip` (a named structural
  inability, always with a `reason` **and** a `hint`) and `error` (the check
  itself broke — "we do not know", not "it is fine") are four different facts
  and must not be collapsed. Nothing about a check is ever an exception.
- **A rebuild evaluates the shape tier only.** Assembly checks and FEM are
  deferred and say so (`skip`/`deferred`); `run_specs` evaluates all three
  tiers. A 600 s solve inside a slider drag is not "without friction".
- **`SPECS` is in the script, so it is in the cache key** — editing a spec
  forces one kernel rebuild of a part whose geometry did not change. That is
  deliberate: splitting geometry identity from spec identity risks serving a
  stale mesh.
- **A part that declares nothing is absent, not green.** `specs: null` on a
  rebuild means "none declared", which is not "not evaluated"; a spec-less part
  has no row in the report and a requirement with zero checks does not exist.
  The presence scan is `ast.parse` and **never executes** the script — that is
  what makes a spec-less project cost nothing.
- **The `specs` gate is fail-closed, and it never returns `pending`.** A
  declared check that was not evaluated is red, and `allow_invalid` does not
  waive it (that flag means "override the *kernel's* verdict on geometry",
  nothing else). `ProposalManager.merge` blocks a `fail` and **nothing else**,
  so every non-green outcome this provider can produce is a `fail` — including
  a source head that moved mid-evaluation, which is a `fail` whose summary says
  to retry (the verdict is not memoized, so the retry re-measures). `pending`
  stays defined in PRD-002 for other providers; this one has no use for it.
  Three gate-only divergences from a `run_specs` report, all fail-closed:
  **every** skip is a `fail` in the gate whatever its reason
  (`fem_extra_missing`, `mesh_only`, `unsupported_scope`, `no_instances`, …) —
  "declared but not measured" is the hole the gate exists to close, and the
  reason travels in `details.reason` plus `details.skipped_in_report`; a
  spec module that will not read or declare is a synthetic `declaration` check
  row (an `errors[]` entry alone is invisible to both `report_status` and the
  gate); and a `budget_exceeded` verdict **is memoized** for that head, so the
  gate stays red with that reason until the head moves or `run_specs` warms the
  caches (which drops the memoized verdict). The memo can only keep a red,
  never make one green.
- **`declares_specs` fails closed on a syntax error.** A script with no AST is
  text-scanned for a line-anchored `SPECS` binding: answering "declares
  nothing" made the gate classify a visibly spec-declaring part as spec-less
  and skip it, so a declared check never became red. A false positive costs one
  error row on a script that already fails its build.
- **A declaration is a shape, not a marker.** `toolkit.specs.is_declaration`
  validates every key a constructor emits (`spec`/`kind`/`scope`/`name`/
  `limit`/`options`/`requirement`); an incomplete hand-written `SPECS` entry is
  a `contract_error` naming the key, never a `KeyError` in the server process
  (which is a 500). Readers still use `.get` with defaults so a format drift
  degrades. Non-finite numbers (`nan`, `inf`) are rejected at construction and
  again at evaluation: every ordered comparison against NaN is false, so a NaN
  limit reports *pass* while measuring nothing.
- **A spec cache key covers every input the check reads.** The assembly sidecar
  key includes the mate graph and each referenced part's PMI dims (what
  `check_stackup` sums — neither moves a part cache key), and the cached
  `fem_static` row is keyed by the material's `E` (the part cache key covers
  density only, while the solver is handed `E_mpa` and displacement scales with
  1/E).
- **`evaluate_specs(proj, ref=None)` means the CALLER's branch** — resolve it
  with `branches.current`, and stamp/key the verdict with *that* branch's head.
  The canonical repo head is the default branch's; using it hands one client
  another branch's verdict through the shared memo. The memo key is
  `(project, branch, head)`.
- **The gate budget is a deadline, not a between-parts check.** Every kernel
  call made under it takes `min(its own ceiling, remaining)` — a 300 s
  `spec_eval` or a 600 s `fem_static` inside a 30 s budget is not a budget —
  and a call with nothing left is refused as a `budget_exceeded` `KernelError`.
- **The `.specs.json` sidecar caches failures too**, keyed like the result: the
  same script and params produce the same `contract_error`, and the UI re-reads
  a part on every rebuild. `run_specs` is the only surface that re-measures a
  cached failure (and the only exit from a cached `spec_declare` failure or a
  memoized budget verdict) — say so in any message that tells a user to retry.
- **A `check_that` predicate gets a COPY of the metrics and runs after the
  built-in kinds.** Predicates are untrusted script code; one that writes to
  the shared metrics dict must not be able to change what another check
  measured. Records stay in declared order, and evaluation order is not a
  contract a predicate may rely on.
- **Three live name collisions:** `service._spec_cache` already means the
  PARAMS spec cache, `inspector.js`'s `renderedSpecJson` already means the
  PARAMS spec JSON, and `packet.py`'s `params_diff` rows already use
  `"source": "spec"` for the PARAMS declaration. Do not reuse any of them.
- **`_min_wall` measures along the inward face normal from a UV sample grid** —
  it over-estimates on non-parallel walls, can miss a feature finer than the
  sample spacing, and finds chamfers and fillet runouts that are genuinely
  thinner than the nominal wall (the rocketry nozzle's 3 mm wall measures
  1.02 mm at `grid=4`). It is a sampled ray cast, not a medial-axis
  measurement, and must never be described as one. `check_wall`'s `grid` is
  the knob and is quadratic in cost; **pick a limit from a measurement and pin
  the grid**, because changing it changes the number.
- **The runner reads `service.branches` inside its methods, never in
  `__init__`** — `tools_specs` loads before `tools_versioning` — and
  `check_stackup` calls `tools_stackup.compute_stackup` directly rather than
  through the registry, for the same reason.
- **The rebuild seam is a wrapper, not a `service.py` edit**
  (`install_rebuild_specs`, idempotent by attribute marker). It wraps
  `_rebuild` and `get_part` rather than the three rebuild-returning tools,
  because the browser's `PATCH .../params` route calls `service.set_params`
  directly and a tool wrapper would miss the UI entirely.

## CI gotchas (PRD-004 — read before touching `checks.py` or the action)

- **The ephemeral service must have `bus.on_publish = None`,
  `store.branch_resolver = None` and `store.write_guard = None`**, or a `--ref`
  check commits a history snapshot **into the user's repository** through the
  linked worktree (the bus hook), writes a `.history/agentcad/` sidecar that
  does not exist there (the resolver), or materializes a branch tree there on
  the first authored write (the guard's `ensure_checkout`). Set the last two
  *after* `build_registry` — that is what installs them. Resolve both paths
  first: macOS hands `/var/…` for `/private/var/…` and `ProjectStore.open`
  compares resolved paths.
- **The pack is `tools_run_checks.py`, never `tools_checks.py`.** Packs load
  alphabetically and `tools_proposals` (`p`) assigns `service.gate_providers =
  []` **unconditionally**, so a pack at `c` would have its gate silently
  discarded — no error, no warning. At `r` it also loads *before* `tools_specs`
  and `tools_versioning`, so `service.specs` and `service.branches` are read
  inside the runner's methods, never captured in `__init__`.
- **Rows are `items`, never `checks`.** `checks` already means the gate name,
  `report["checks"]` in a spec report and the proposals UI tab. `status` is the
  four-value row status; `state` is the gate's. They are not interchangeable.
- **`check` is report-honest; `--strict` is the opt-in.** A `skip` keeps its
  status, reason and hint whatever you pass; `--strict` only records ids in
  `strict_failures` and moves the derived verdict. `evaluate_specs` and the
  `specs` gate are unconditionally fail-closed — different audiences, one set
  of measurements. Neither `checks` nor `specs` ever answers `pending`:
  `ProposalManager.merge` blocks `fail` and nothing else, so `pending` is
  merge-permissive.
- **`--ref` uses `worktree add --detach <sha>`, never a branch name** — a
  branch already checked out at `.history/trees/<b>/` cannot be checked out
  twice — and resolves with **`resolve_branch` then `resolve_tag`, never
  `resolve_ref`** (`git rev-parse` searches tags *before* branches, PRD-001 X1).
- **A ref check runs on a cold cache, deliberately.** The work dir holds no
  `.cache/`; that is the price of AC7's byte-identity guarantee. Every row
  reports `cached: false`, and the tests assert it rather than hiding it.
- **DXF is not byte-stable** (`ezdxf` stamps `$TDCREATE` and fresh GUIDs), so
  the determinism stage compares **SVG only** and carries one
  `skip`/`not_byte_stable` row. That row is the one `strict_exempt: true` in
  the report: an unconditional skip is not a `--strict` candidate, or
  `--strict --verify-determinism` would be red for ever and say nothing.
- **`--budget` cannot preempt a kernel call that has already started.** It is a
  deadline on `time.monotonic` read before *every item and every kernel call* —
  the specs stage runs under it too (`SpecRunner.run(deadline=…)`), determinism
  re-reads it before each of its four calls, and below a one-second floor no
  call is issued at all. `_ensure_built` (300 s) and the drawing tools (120 s)
  take no `timeout_s`, so the worst case is **one** call's overshoot. An item
  the deadline stopped is a `skip`/`budget_exceeded`, never an `error`: a blown
  budget is `complete: false` → exit **2**, with the partial report kept as
  evidence, and never a red. An overshoot *inside the last item* keeps
  `complete: true` (everything was measured) and is named in `warnings[]`.
  `--budget`/`--min-volume` must be **finite and non-negative**: NaN compares
  false against everything, so it switches off the limit it configures.
- **A run's policy lives on a per-run runner, not on `service.checks`.** That is
  one shared object; `run()` builds a context with `_run_context` and never
  assigns to `self`, or two concurrent runs (chat + route + CLI) overwrite each
  other's deadline and interference threshold.
- **A check that measured a dirty tree may not be posted.** Its `source.sha` is
  the *committed* head, so the gate would read it as certifying bytes it never
  measured. Refused at `post_to_proposal` — which **raises**, but only the CLI
  turns that into exit 2: `run_checks` catches it and returns the measured
  report with `posted: {ok: false, error}` and a warning, so a refused post is
  a *receipt*, not an error (never discard minutes of kernel work to report a
  delivery failure). A `--ref` report's `dirty` flag is provenance about a tree
  it did not measure, and still posts.
- **The `checks` gate asks the audit before it answers `skipped`.** A
  `checks_posted` line with no readable record is a `fail` ("re-run"), never the
  permissive "nothing posted" — deleting `checks.json` must not unblock a merge.
  The record is validated (`validate_record`) against `CHECKS_SCHEMA`, against
  `validate_report`, and field-by-field against the report it wraps.
- **A check may not write anywhere but its own throwaway cell.** `--work-dir`
  that is, holds or sits inside the project (or the projects root) is refused;
  a run materializes into `<work-dir>/agentcad-check-<pid>-<rand>/` and deletes
  only that. Three seams on the ephemeral service must stay nulled —
  `bus.on_publish`, `store.branch_resolver`, `store.write_guard` — each reaches
  the user's repository through the linked worktree.
- **An imported reference part's `is_valid` is reported, never enforced.** OCCT
  calls the shipped `examples/rocketry` STEP import invalid over its 180 solids
  — the same reason `tests/test_examples.py` exempts reference parts — so the
  row passes, `details.is_valid` carries the fact and a warning names the part.
  Enforcing it would redden a clean bundled example and the dogfood workflow.
- **The Action checks the WORKING TREE; `$GITHUB_SHA` is provenance.**
  `actions/checkout` already materialized the ref and a runner has no AgentCAD
  `.history/`, so `--ref "$GITHUB_SHA"` would exit 2 on every run. Pass
  `--sha` / `--ref-label` instead.

## Review-thread gotchas (PRD-008 — read before touching comments, anchors, presence or undo)

- **Threads live at `.history/agentcad/comments/`, are canonical and
  branch-free, and are never model state.** `project_restore` cannot rewind
  them, every branch sees the same list, no merge ever touches them, and a
  comment never shows up in `git status`. Paths come from
  `store.canonical_path_of`, **never `path_of`**.
- **The module is `comments`, never `threads`.** `agentcad/toolkit/threads.py`
  is ISO screw threads and `tests/test_threads.py` is its test module. The word
  survives only in payloads and tool names (`resolve_thread`,
  `comment_changed {thread}`), which FR7 freezes.
- **An anchor is immutable; its status is computed on every read.** `ok`,
  `moved`, `orphaned` and `unverified` are four different facts. `unverified`
  means *we did not look* (unbuilt part, no git, frozen packet) and must never
  be rendered as "fine". Address geometry through `resolution.face_index` and
  lines through `resolution.start`/`end` — never the stored ordinal.
- **Orphan rather than guess — a bias, not a guarantee.** An ambiguous face
  match is an orphan; a low-confidence line remap is an orphan; and a **lone
  candidate** is an orphan unless it clears `LONE_AREA_REL` on its own *and*
  still touches the same number of faces, because `AMBIGUITY_MARGIN` cannot
  fire when there is nothing to compare against — that gap was a real mis-pin
  (a cut-away boss re-pinning onto the plate under it at 0.87 "confidence").
  Loosening a tolerance to make a pin appear is the one change this feature
  must never take. **Two measured classes, two rates, quote both** — the second
  is the one an agent hits, and the first cannot speak for it because that
  sweep never deletes anything:
  - a **parameter change** (changelog 0123, 2 693 known-truth faces): 53.9%
    resolved, **2 mis-pins**, both on a body of revolution;
  - a **deleted feature** (changelog 0125, 327 faces that no longer exist):
    98.8% correctly orphaned, **4 mis-pins**, down from 27 before the adjacency
    gate — all four a square pad on a square plate, where every number a
    mesh-derived signature has is the same on both faces.

  So **a cut-away face can still re-pin**; say that, not "never", and confirm
  with `face_info` before an expensive decision.
- **A lone survivor is not evidence, for scripts either.** `find_snippet` used
  to skip the stored context whenever exactly one copy of the snippet was left,
  so deleting the anchored one of two identical lines re-pinned the thread onto
  the unrelated survivor and reported `moved` at confidence **1.0** — the same
  mistake as the face matcher's lone candidate. A lone hit must now be
  contradicted by **neither** side of the stored `before`/`after` — "one
  agreeing side is enough" was the first fix and it was too weak, because
  duplicated blocks in real code end the same way (`    return shell`) far more
  often than they begin the same way. The same rule guards tier 1's *identity*
  check: an address that still holds the anchored text but whose stored context
  says the block around it is a different one is put to the diff instead of
  being taken on trust (and is still answered `ok` when there is no diff to
  read, so an edit near a thread in a project without git costs nothing). With
  two or more hits the context stays a tie-break. A refused hit falls through
  to the tier-2 line map, which answers from the real diff — and that is what
  makes the strict rule cheap, because a block that stayed in its neighborhood
  with one side rewritten is exactly what a diff gets right. The gap that
  remains, stated rather than hidden: an anchor that stored **no** context —
  the top *and* bottom of a file, or one written before context existed — has
  nothing to check and keeps the old behavior.
- **Two measured ceilings on face re-matching, both documented rather than
  tuned away.** (1) *Any* parameter change that alters the part's **bounding
  box** orphans every anchor on that part — `bbox_uvw` is relative to the
  bounds, which is exactly what makes a pure scale survivable and what makes a
  bounds change total. (2) A **closed curved face** (a cylinder's side) orphans
  on any edit: its area-weighted normal nearly cancels, so `NORMAL_DOT` admits
  no candidate. Both are the safe direction.
- **`metrics.n_faces` is deduped (`len(shape.faces())`); the `<key>.faces.u32`
  sidecar is the authority for face-index validation** — it is what an ordinal
  is *defined* by (the `TopExp_Explorer` walk `mesh.py` tessellates).
- **Face signatures are derived in the server from `.acm` + `.faces.u32` — no
  kernel call, no rebuild.** `signature_table` uses `service._cache_key_for`,
  never `mesh_info`/`_ensure_built`; a part that was never built resolves to
  `unverified`/`part_not_built`, and listing a hundred threads is a cheap read.
- **Hunk anchors read the persisted `packet.json` only** — never
  `service.packets.packet(...)`, which rebuilds geometry on both sides and can
  move a proposal's state. The hunk **header** is the identity, not the index.
- **Claims are human-vs-human only, and never apply to a client holding the
  turn.** Precedence: turn → own-turn bypass → claim → proceed. A claim names
  one *part*, is taken by editing (a `claim: true` heartbeat or a successful
  part-scoped write), and expires in 90 s; the **turn lock** is project-wide,
  explicit (`acquire_turn`) and applies to everyone. Agents get no claim tools
  by design — an agent blocked by a human's open editor would 409 on the first
  write of the flagship loop.
- **The part dimension reaches `write_guard` through `locks.write_scope`, not
  through the guard's signature** — every existing guard keeps working
  byte-identically, and only `write_script` / `update_part_entry` are
  claim-covered. Whole-manifest writes are turn-locked only, on purpose.
- **The claim guard is installed lazily and from `routes_presence`**, because
  `tools_versioning` (`v`) REPLACES `write_guard` after `tools_comments` (`c`)
  loads. Same trap, same fix as `ProposalManager`'s branch-delete guard. **And
  `install_write_guard` re-installs it after replacing the guard**, because
  "lazy" alone left a window: a *later* `build_registry` disarmed claims until
  the next heartbeat. It is conditional on `service.claims` already existing,
  so `checks.py`'s ephemeral service still ends with `write_guard is None`
  (PRD-004 pins that) — do not make it unconditional.
- **An armed override is spent by the first write it authorizes, and used only
  against a real conflict.** Both halves matter: using it where nothing blocks
  force-steals a claim nobody was defending, and leaving it armed because
  nothing blocked lets it steal the *next* claim with no second confirmation.
- **Presence is an HTTP heartbeat, not a client→server WebSocket.** `/ws` is in
  `app.py` (a core this feature may not edit), carries no client identity, and
  its Host guard is HTTP middleware. The heartbeat *response* is the mechanism;
  `presence_changed` is an optimization. The registry is in-memory and never
  persisted.
- **Presence is bounded, because the identity is a header anyone can rotate.**
  `MAX_ID_CHARS` (64, refused not truncated — a truncation would merge two
  identities into one roster/claim/mention key), `MAX_CLIENTS` (200, a full
  roster refuses a *new* row rather than evicting an incumbent, because a
  flood is by construction the most-recently-seen rows), and `MAX_BUCKETS`
  (512, or rotation bypasses the rate limit outright — buckets refilled to
  full burst are evicted first, since they carry no information). The
  `presence_changed` broadcast *is* the roster, so no separate bound.
- **`notification` events are broadcast to every `/ws` client and filtered
  client-side.** Honest on a single-node, unauthenticated, 127.0.0.1-only
  server; PRD-005 is what makes delivery per-principal.
- **`comment_changed` is never `project_changed`.** A comment is not a model
  change: it must trigger no history snapshot and no rebuild. `bus.on_publish`
  is a single slot already owned by `service._snapshot_on_event` — never
  assign it.
- **Per-user undo did not re-key the undo stacks.** `scope: "any"` is the
  default and is byte-identical to the behavior that predates authorship,
  because a human pressing Cmd+Z to take back the agent's edit is the product's
  flagship loop. `scope: "mine"` skips (never discards) other clients' entries,
  and reverts instead of restoring once the entry is no longer the head.
- **Authorship is a commit-message *trailer*, not a git author.** `Client: <id>`
  goes in the body, so every `%s` subject contract still holds; git's
  author/committer stay the fixed local identity because the client id is an
  unvalidated header. `log()` reports `author: null` — never `"unknown"` — for
  a commit written before authorship existed. **`author_of` reads `Merged-by:`
  too**: `merge.py` has written that trailer since PRD-001 and its exact
  message is pinned by that feature's tests, so authorship is read from both
  spellings rather than by rewriting what a merge says.
- **`undo {scope: "mine"}` off the head is a `git revert`, and it honours
  `undo_to`.** A fast-forward merge moves the branch onto a commit whose first
  parent belongs to the *source*, so `undo_to` names the state the target was
  really on; the scoped path reverts the whole range `undo_to..entry` in one
  commit (a single-commit revert would leave the merge half undone) and a
  whole-tree restore is never an option there, because it would take everyone
  else's later work with it.
- **`ProjectHistory.revert` is atomic in both directions.** A dirty tree is
  refused up front, a conflict rolls back — and a failure *after* the patch
  applied (a repo hook rejecting the commit) resets tree, index and HEAD to
  where they started before the error leaves. "Never a partial apply" is a
  statement about the way out too.
- **`actor_kind`/`author_kind` is bookkeeping, not authentication.** It is
  `human` iff the identity is the browser. Say so on every surface that shows
  it, until PRD-005.

## Sketcher gotchas (PRD-009 — read before touching the solver, the emitter or `sketcher.js`)

Every item is traceable to a measurement in `docs/changelog/0127`–`0141`.

- **`toolkit/sketch.py` runs in the SERVER process and must never import
  build123d.** With `specs.py` it is one of exactly two toolkit modules with
  that property (`facemod`, `fillet`, `shell`, `surfacing`, `sheetmetal`,
  `threads` all import `b3d` and run kernel-side). `core/sketch_emit.py` is
  OCP-free for the same reason: emitting build123d *source* is not importing
  build123d. `toolkit/sketch.py` is asserted in a fresh interpreter with `OCP`
  blocked at `sys.meta_path`; `core/sketch_emit.py` is asserted by importing it
  in a fresh interpreter and checking `sys.modules` afterwards — the same
  property, one check weaker. `numpy`/`scipy` are **declared** dependencies
  now, because they used to arrive only transitively through build123d.
- **The solver's cost is the Jacobian, not the iterations.** A finite-
  difference Jacobian costs `n_par + 1` residual evaluations — 92% of the old
  51 ms solve. Every residual ships an analytic `df`, and `Residual` **refuses
  to be constructed without one**: a missing derivative does not crash, it
  silently reinstates the O(n²) cost. A wrong one is worse (slow convergence,
  the wrong branch, or none), so every kind is covered by a central-difference
  test — `RESIDUAL_KINDS` unions the `DERIV_BUILDERS` of every
  `tests/test_sketch_*.py`, and a kind with no case fails loudly.
- **The derivative gate cannot tell you the residual is right, and it never
  could.** It compares each `df` against a central difference of **its own
  `f`**, so a geometrically *wrong* residual passes every case: the internal
  circle/circle tangency computed `d - (r1 - r2)` instead of `d - |r1 - r2|`
  for two slices — two internally tangent circles reported `ok: true` one way
  round and `ok: false`, `max_residual` 10, the other — with a green
  derivative suite over it the whole time. Wherever this branch or its
  changelogs say "every residual's derivative is proven", read exactly that
  and no more. The independent layer is
  **`tests/test_sketch_semantics.py`**: for each constraint it builds a
  configuration whose geometry it computes itself and asserts `f == 0` where
  the constraint holds *and* that `f` tracks the geometric error with the
  right sign and scale — a slope, not a smallness, because a residual that is
  the square of the error is small for reasons that have nothing to do with
  the sketch being right.
- **A residual that is second-order flat at the solution makes the Jacobian
  rank-deficient AT the solution and reports phantom DOF.** If two entities
  are already tied together by rows the sketch declares, write the remaining
  condition in the quantity those rows do *not* pin: **distance-to-a-thing-you-
  are-already-on is flat; direction is not.** This has now been found twice —
  a slot's sides read `dof 4` on a fully-determined slot (0132), and a GUI
  tangent chain read `over-constrained (1)` with a singular value of 1.8e-16
  against a 8.5e-9 rank tolerance (0137). Both fixes were residual *forms*
  (`tangent_point_perp` at a structural junction, `tangent_dir` at a
  coincident one), never a tolerance. Raising `RANK_TOL_REL` to hide it would
  have hidden real redundancy everywhere else.
- **`dof` is `n_params − rank(J)`, never `n_params − n_residuals`.** The row
  count reports a *negative* DOF for any redundant constraint — a quantity
  with no meaning that the old GUI rendered as `dof -1`.
- **Blame the *later* constraint: dependent-set analysis walks residual rows
  in declaration order.** Column-pivoted QR is the textbook method and it
  blamed an innocent original constraint in 2 of 3 measured cases, because
  pivoting selects by column norm — an artifact of residual scaling.
  `conflicting` is *a* dependent set and every surface says so.
- **`over_constrained` is not an error; unsatisfiable is.** Redundant but
  consistent returns `ok: true` with `redundant: [...]`. Only a non-empty
  `conflicting` raises.
- **The drag residual is an objective, not a constraint** — excluded from
  `ok`, `max_residual`, `n_residuals`, the rank, the DOF and the diagnostics.
  Including it makes every drag of a fully-constrained entity report
  `ok: false` (measured `max_residual` 2.43 over a 48 mm drag). **The
  exclusion is structural, not a flag**: the drag block lives outside
  `self.residuals` and is appended only inside `make_functions`, and `solve`
  slices `[:n_res]` off both the residual vector and the Jacobian before
  anything is reported. Diagnostics are off the drag path the same way — a
  well-constrained sketch never runs the greedy pass at all.
- **Warm-starting from the on-screen state causes mirror flips, it does not
  prevent them.** Seed from the *previous solution* and pull toward the cursor
  with a weak residual (measured `+18.7265 → −18.7265` for the naive form).
  `initial` is **all-or-nothing**: an incomplete seed is a cold start with
  `initial_incomplete`, which quietly re-opens that door — the GUI forgetting
  a slot's radius was exactly that bug.
- **Emitted chains share vertex literals and round to 9 decimals.** A
  centre-parametrized arc at the old 6 decimals leaves a 7.58e-7 mm gap and
  `make_face()` raises *"Face can only be created with closed wires"* — and it
  **only reproduces on non-round coordinates**, so a tidy test profile proves
  nothing. The emitter gates on a measured 1e-8 mm closure tolerance and
  refuses rather than emitting code that will not rebuild. Never format a
  coordinate for code with a display formatter.
- **One `BuildLine` and one `make_face()` per CHAIN.** `make_face()` consumes
  every pending edge in its builder and makes **one** face, so a single shared
  `BuildLine` turned two disjoint squares into one 1 mm² face instead of two
  totalling 2, and swallowed an open chain drawn next to a closed one. A chain
  is the unit of connectivity, so it is the unit of emission.
- **The closure gate does not check that the call is legal.** It measures how
  far a junction's shared literal moved the endpoints it stands for, and that
  is all: a zero-sweep arc still reached `RadiusArc(v0, v0, r)`
  (`Standard_ConstructionError`) and anything past a full turn still reached
  `CenterArc(..., 450.0)` (`ValueError`). Both are refused where the
  constructor is chosen. Same for radii — **every emitted radius is checked as
  the reader will see it**, after formatting, because nine decimals rounds
  1e-11 to `Circle(radius=0.0)`, which makes no face. A radius is also refused
  where it is *written* (`Sketch.circle/arc/radius`); the two layers see
  different numbers.
- **A sketch that closes nothing emits code that raises.** `BuildSketch` with
  no face fails at its own exit ("Unable to repositioned type
  `<class 'NoneType'>`"). That is reported as a `no_closed_profile` warning
  rather than refused, because drawing an open chain and pressing Insert is a
  legitimate half-finished state.
- **`EllipticalCenterArc` takes `arc_size`, not `end_angle`, in the pinned
  build123d 0.11.1**: `end_angle` raises `UnboundLocalError` because the
  deprecation branch reads a name only the *other* deprecated parameter binds.
  There is also **no start-AND-end anchored elliptical constructor**
  (`EllipticalStartArc` exists, but it anchors the *start* point and derives
  the other end from `start_tangent` + `arc_size`), so an elliptical arc's
  endpoints are always derived by the reader from rounded literals — the
  closure gate measures that derivation unconditionally.
- **`SlotCenterToCenter` is a BuildSketch face at the origin, not a BuildLine
  curve.** A slot tied into a larger profile emits as its compiled primitives.
- **A compiled sub-entity never gets blamed, and never has to be sent
  back.** Slot machinery carries the slot's own `con_index` and
  `origin: "slot:<name>"`, and `con_report` holds `None` where there is no
  caller-visible index; a 3-point arc's `<name>.center` is excluded from
  `initial`'s coverage requirement, because requiring a point the caller never
  wrote made the all-or-nothing seed impossible to satisfy.
- **Tangency at a junction the sketch pins is a DIRECTION residual, and the
  detector asks the JACOBIAN.** `dist(centre, line) - r` and
  `d(c1,c2) - (r1 ± r2)` sit at an extremum of the manifold the pinning rows
  cut out, so their gradient falls into that span: the row reports itself
  redundant while removing a real DOF, and `max_residual` reports the *square*
  of the geometric error (measured 8.11e-11 reported against a true
  4.97e-05 mm before the fix — a ratio of 6.1e+05; 10.0 after it, which is
  the radius). **This was found four times**, and each of the first three
  fixes was an enumeration that the next one walked past: a list of entity
  handles (slice 6), a union-find over `coincident` (slice 10), a table of
  constraint kinds that put a point on a curve (0142). The fourth was
  `distance_x(p, a1.start, 0)` + `distance_y(p, a1.start, 0)` — a junction
  pinned exactly as hard as a coincidence, on none of the lists.
  `Sketch.resolve_tangencies` replaces the enumeration with a criterion: a
  handle is **held on** a curve when the curve's own on-curve function is zero
  there *and* its gradient lies in the **row space of every other residual
  row**, evaluated at the configuration those rows project the seed onto; two
  curves share a junction when some handle is held on both. No constraint kind
  appears in it, so no new kind can make it stale. Two things it does not
  claim: a *pinned but non-zero* offset is not a junction (the value half is
  what keeps `distance_x(..., 5)` an ordinary tangency), and ellipses and
  splines have no closed-form on-curve function so they keep the symbolic
  detector — their fallback is already first-order, so a missed junction there
  costs a parameter, not an answer.
- **The rank must not be a function of row scale.** One 1e-9 mm line (a GUI
  double-click) writes 1.4e+09 into the Jacobian through `_accum_dir`'s `1/n`,
  and a relative singular-value threshold then reads every honest row as zero:
  measured rank 3 of 7 and `dof 7` on a pinned rectangle. `Sketch.row_scaled`
  normalizes rows before the SVD, and where the greedy dependent-row pass runs
  to completion **its count is the rank**, so `status`, `dof` and the blame
  sets agree by construction — `over_constrained` with an empty blame set is a
  bug, not a display problem, and the chip branches on `status`.
- **The diagnostics cache carries the greedy dependent-row set, never a
  verdict.** Its key is the residual *structure* on purpose (the GUI resends
  the whole spec every drag frame with its points at the last solution, so a
  coordinate key would miss every time) — but nonlinear rank is a function of
  the configuration, so a drag frame with two collapsed lines cached `rank 0`,
  `dof 8`, `over_constrained` and the next ordinary solve of the same
  structure was served it. The rank is now recomputed on **every** frame and
  the cached set is used only when it matches; `status`, `dof`,
  `free_entities` and the redundant/conflicting split always describe the
  frame in front of them. `diagnostics_source: "cached"` means the greedy pass
  was reused, not the answer.
- **A residual on a LENGTH is not a residual on a DIRECTION, and they need
  different degenerate-case rules.** `_unit` returns a zero direction for a
  degenerate segment, which is right for `parallel`/`perpendicular`/`angle`/
  `point_on_line` — they are functions of a direction and a zero-length
  segment has none. `distance`, `point_on_circle`, `equal_length` and the
  circle-circle tangency are functions of `|b - a|`, and there a zero
  direction is the one subgradient that makes the whole Jacobian row vanish:
  measured, `distance(a, b, 0)` at its own solution reported `rank 0`,
  `dof 2` and called itself redundant while pinning both coordinates. Those
  use `_norm_dir`, whose documented convention is the **+x unit subgradient**.
  And `distance(p, q, 0)` compiles to the **coincidence rows** — the same
  solution set, linear, rank 2 — because one subgradient can only ever remove
  one of the two DOF the geometry removes.
- **Every async response is scoped to the part that asked for it.**
  `sketch_plane` and `/api/sketch/blocks` are round trips the user can outrun:
  pick a face on part A, switch to part B, and A's basis and references opened
  over B — Insert then wrote A-plane geometry into B's script. Both paths
  record `"<project>::<part>"` with the request and re-check it on arrival,
  alongside the generation counters. `sketcher.openOnFace` re-checks it again
  on its own side of the module boundary.
- **`specToModel` and `entitiesSpec` are an inverse pair, and it is asserted
  in node** (`tests/test_sketch_frontend_roundtrip.py`, which is why
  `sketcher.js` exports `__roundTrip__`). A flag dropped on either side is
  geometry that changes meaning after one GUI round trip: a construction
  spline or slot came back as *emitted* geometry, and a fixed-radius arc came
  back free. A three-point arc is deliberately **normalized** to the canvas's
  one arc representation (centre point + radius + angles) on load — the canvas
  has no other — so it re-emits as `RadiusArc`, not `ThreePointArc`.
- **For a face: the basis is stable, the ordinal is not.** `Plane(face).x_dir`
  is **bit-identical** across rebuilds, across a fresh worker and across
  parameter changes that do not renumber faces — so sketch-on-face coordinates
  are reproducible. The face *index* is a mesh-order ordinal and a
  topology-changing edit renumbers it (`corner_r: 6.0` turned the enclosure's
  face 37 from a 5989 mm² plate into a 51 mm² sliver). Those are two different
  claims; only the second is a caveat, and it is written into the emitted
  script. Reference entities are `fixed` **and** `construction` — a fixed arc
  pins its angles too, so a reference costs **zero** parameters.
- **HTTP keep-alive is load-bearing on the drag path** (0.72 ms reused vs
  12.55 ms p50 fresh), and it is confirmed working in Chrome — but the drag
  budget is missed anyway on **display-frame quantization**, which is why the
  GUI predicts the dragged handle locally and reconciles on the response. No
  `Connection: close`, no per-frame `AbortController`, no `sendBeacon`.
- **Round-trip: the code is the source of truth for geometry; the block is
  provenance.** A hash mismatch is `diverged` and is never repaired; an
  unreadable spec is `unverified`, which is "we cannot tell", not "no sketch".
  **The hash covers the spec AND the code** (spec version 2): covering only
  the code meant `ok` said "nobody edited the geometry" while every reader
  took it to mean "this spec produced this code", and editing one coordinate
  in the comment left the block `ok`. And the block records an `initial` taken
  from the **solution**, because `initial` selects the branch: without it a
  sketch emitted on the seeded branch reopened on the other one, `ok`.
  `_read_spec` validates the spec's **shape** — every section a list of named
  objects, every constraint an object with a `type` — because
  `"points": "not-a-list"` used to read `ok` and then throw out of the
  browser's `.map()`.
  The block name is the emitted function's name, so a second block in one
  script must take the next free name or it silently shadows the first —
  `next_name` is the server's answer and Insert asks for it. The block scanner
  reads `tokenize`'s COMMENT tokens, so a docstring quoting the marker is not
  a block.
- **The face's identity is recorded, not just its ordinal.** `sketch_plane`
  returns `face_id` (`area_mm2`, `normal`, `origin`) and accepts it back as
  `expect`; reopening a saved sketch-on-face gets
  `face_check: ok | moved | unchecked` **with both measurements**. Never
  repaired — which face the user meant is not something the code can guess.
- **`plane` is caller data, not source.** `face_index` and `part` are
  validated (an int, and an identifier-ish expression) before they reach the
  generated header, and the basis vectors go through `fmt`: a crafted `part`
  put `import os` on line 2 of a generated script.

## Feature-toolkit gotchas (PRD-010 — read before touching `toolkit/patterns`, `holes`, `features` or `sheetmetal`)

Every item is traceable to a measurement in `docs/changelog/0147`–`0160`.

**OCCT succeeding is not evidence. This PRD found four separate silent
failures, and they all look like success:**

- **A misplaced cut is a SILENT NO-OP.** A ⌀4.2 tool placed entirely off the
  part cuts in 0.89 ms, leaves the volume **exactly** unchanged, reports
  `is_valid True` and raises nothing (0149). "Never silent geometry" is not
  something the kernel gives you — the helper has to *measure* engagement.
  Two tiers: a bbox screen at **0.014 ms/instance** (always on) and the exact
  `(part & tool)` probe at **2.1–2.4 ms/instance** (`verify="exact"`, which
  roughly doubles a 50-instance pattern). Warnings name **indices**, never a
  count.
- **A floating rib is a SILENT SUCCESS whose volume delta is exactly right.**
  A rib fused 25 mm above the part raises the volume by the rib's full amount,
  `is_valid True`, nothing raised — the *only* tells are `len(solids()) > 1`
  and `(part & rib).volume == 0` (0155). Same class as the misplaced cut, new
  place.
- **Draft's dominant failure returns `is_valid False` rather than raising**
  (0156). Only the extreme angles raise; most failing angles hand back one
  solid with a plausible positive volume. A hand-written `draft()` must check
  `is_valid` itself. Failure **is monotone in the angle** on all eight shapes
  measured (no islands — that is what makes the binary search sound), and the
  real ceilings are low: `prototyping/enclosure_base` **0.25°**;
  `rocketry/nozzle` and `construction/angle_bracket` refuse **every** angle.
  When it raises it is `Standard_Failure` with an **empty message**, so every
  word of the warning is ours.
- **A >180° hem leaf penetrates the sheet and the fuse swallows it silently**
  (0158): at 225° with a 4t leaf the result is one valid solid with 144.59 mm³
  of declared material simply gone. Hence `kind="teardrop"` raises. And **a
  180° hem's air gap is `2R`** — so "closed" is a small-R hem, not a
  zero-R one: at `R = 0` the fold is still one valid solid of exactly the
  right volume but with **8 faces instead of 10**, which is 2t of solid stock,
  not a hem. `inner_radius=0` is refused.

- **Two declared sheet-metal features in the same space fuse into ONE VALID
  SOLID that is simply smaller** (0161, 0162). `is_valid` stays true, the solid
  count stays 1, nothing raises — so `_checked` is structurally blind and
  `_conserved` (declared closed form vs measured volume, every cut credited
  with what it *measurably* removed) is the only thing that sees it. Two 30 mm
  hems on a 40 mm-deep 60×1 plate declare 6565.486678 mm³ and measure
  5365.486678: **1200 mm³ gone**. Only a shortfall is possible here, so the
  check is one-sided by construction.
- **A `close` corner is a whole seam only where the two CROSS-SECTIONS agree,
  and each leaf fits its mitre extension** (0162), and that failure moves no volume at all: one
  plane cuts both leaves, so neither can cross it and `_conserved` is silent by
  construction while the two still fuse through the base plate into one valid
  solid. The seam is the only casualty, so the seam is what is measured —
  shared face area from `(A.area + B.area − (A+B).area)/2`, against the
  `√2·min(profile area)` the mitre promises. Matched, ≥90°: **1.0 to within
  8e-15** (a different *leaf length* is fine, the shorter face is seamed
  whole). Worst mismatch measured 0.9586 (R 3 vs 2.9), so the screen — angles
  equal, radii equal, reach within the extension — needs no tuned threshold and
  the boolean is paid only where it has already fired. The second criterion is
  the **reach, not the angle**: `_effective_span` runs the leaf `R + t` past
  the corner, which is the outward reach of a **90° profile and of nothing
  else**, while a profile reaches `(R+t)·sin a + L·cos a` — so it holds iff
  `L ≤ (R+t)·tan(45° − a/2)` (`_max_mitre_leaf`; infinite at and above 90°,
  where a vertical leaf adds no reach). **90° is a discontinuity, not a
  limit** — `L_max` falls to 0 as `a → 90⁻` (4.36e-09 mm at 89.9999999°) and
  is unbounded at 90°, because `L·cos a ≤ (R+t)(1 − sin a)` has both sides
  vanish there and `L_max` divides through by that zero. Relatedly,
  `_profile_reach` **must** drop the leaf term from 90° up: `cos a ≤ 0` there
  mathematically, but `math.cos(math.radians(90.0))` is `6.123e-17`, and
  letting it through made a *correct* 90° corner warn at a 16.33 km leaf and
  print "the longest leaf that still mitres is **inf** mm". A test that pins
  one leaf length cannot see that; pin the decades. A *matched acute* corner inside that
  bound seams **whole and is silent** (measured 1.000000000000 at 60°/R3/L0.2,
  45°/R3/L0.5, 30°/R5/L1, 10°/R5/L1, 20°/R4/L2); it is the long leaf that
  breaks — 45°/R1/L12 wants 10.6066 mm of extension, gets 3.0, seams 0.2810.
  Do not say "`close` needs 90°": that is more pessimistic than the code, which
  has always tested the reach. Feeding the required reach in takes the failing
  rows to `1.000000000` exactly, so it is a fixable modelling bug, left warned
  rather than fixed because the blank's mitre chord is derived at 90° too and
  fold/unfold may not diverge.
- **`patterns.polar` skips the placement that moves the seed NOWHERE — never
  index 0** (0162). With `radius=r` every placement translates onto the circle,
  so none of them is the seed: skipping index 0 dropped the instance at angle 0
  and counted the centre seed in its place, with `warning=None`, one valid
  solid and the *expected* added volume — measured centres (−20,0), (0,−20),
  (0,0), (0,20) for `count=4, radius=20`. Correct volume + valid + one solid +
  wrong locations is a failure class the volume tiers cannot reach, so every
  report row now carries the instance's `center` and the helpers assert those
  centres (equidistant from the axis and evenly spaced for `polar`, one step
  along one line for `linear`). The identity test is on the **transform**, not
  the resulting position: a rotationally symmetric seed spun about its own axis
  lands its bbox back where it started at every placement.
- **A pattern instance's `center` is the rigid image of ONE reference point,
  never the bounding box of the moved shape** (0162). A bbox is rotation-
  invariant only for a seed with 180° point symmetry, so re-measuring it after
  each placement makes a **correct** polar pattern look broken: measured on a
  right-triangular gusset boss (one valid solid, added volume exact to 6e-11),
  the moved-bbox centres spread **3.5196 mm** at `count=3`, 3.8507 at 5 and
  3.0404 at 8, and the layout assertion called it "a placement bug, not a
  tolerance". Rigid images of the seed's own bbox centre spread `0.0` (1e-9
  after `POSITION_DECIMALS`) on the same patterns and still put 20 mm between
  the seed and the circle in the real bug. **Every pattern test in the suite
  used a box or a cylinder, which are immune** — that is what let it through,
  so a pattern test needs an asymmetric seed at a non-90° step. The metric was
  wrong, not the tolerance: widening one to cover 3.5 mm swallows the 20 mm bug.
- **`polar` cannot consume a seed it was handed, so `radius > 0` always leaves
  one over.** The route to exactly `count` on a circle is a seed built where
  instance 0 goes plus `radius=None`; `radius` is for a seed authored at the
  axis, where the leftover is deliberate (a hub with its bolt circle). Never
  tell a caller to "author the seed at the axis and pass `radius`" for a clean
  circle — that is advice that guarantees the thing it warns about.

**Where the metadata lives, and why it is not where you would put it:**

- **Hole records ride the built shape (`holes.ATTR`), not a registry.** The
  worker's 16-entry `_SHAPE_CACHE` returns the cached shape **without calling
  `build(p)`**, and the service's `.metrics.json` fast path makes **0 kernel
  calls at all** — so a per-build registry drains empty on the second and every
  later build of an unchanged part, silently (0150). The persisted copy is a
  `.cache/<key>.holes.json` sidecar on the `.specs.json` precedent, and it is
  mandatory, not an optimisation.
- **Nothing composes the attribute for you.** `safe_fillet`, `safe_bool` (both
  directions), a raw `part - tool` and even a re-entered `BuildPart()` +
  `add(part)` all return a new object carrying **none** of the original's
  attributes; only `.clean()`, `.moved()` and `copy` preserve it. Every helper
  calls `holes.carry()`, and `fillet.py`/`shell.py`/`boolean.py` carry a
  `@holes.carries_records` decorator. After a raw build123d op of your own,
  call `holes.carry(new, old)` — or the harvest's delta will tell you.
- **The harvest runs BEFORE the build, deliberately.** Run second it is always
  a shape-cache hit, its delta is always 0, and the drop check is dead code
  (measured `measured: false` on three parts, every time — 0151). It must also
  pass `affinity=part_id`: `KernelPool._pick` round-robins an *unkeyed*
  request, and an unkeyed harvest that lands on a cold worker paid **11 354 ms**
  on `engine/intake_manifold` against **1 ms** keyed.
- **The worker runs as `__main__`, so importing worker *state* from a handler
  pack gets a second, always-empty copy.** `from ..worker import _SHAPE_CACHE`
  re-executes the module (measured: every call then reports itself a fresh
  build). Importing a worker *function* is fine and several packs do; reach
  live state through `build_shape_ns.__globals__` instead (0151).
- **`hole_standards.py` is the THIRD OCP-free toolkit module** (with `sketch`
  and `specs`) because the server's `hole_standards` tool imports it;
  `core/tools_holes.py` is OCP-free for the same reason and is asserted in a
  fresh interpreter. **`tools_holes` loads at `h`** — before `tools_proposals`
  (`p`), `tools_specs` (`s`) and `tools_versioning` (`v`) — so it reads
  `service.branches`/`specs`/`gate_providers` **never** at registration, and it
  *wraps* `_rebuild`/`get_part`, which no later pack replaces.

**Round-2 review findings (0163), all in the hole half:**

- **The default `verify="bbox"` is a THREE-rung per-instance probe, and the box
  rung can only ever prove a MISS.** A tool compared against the *whole part's*
  bounding box says nothing about the material under it, and the aggregate
  volume delta cannot be attributed to an instance — so a ⌀10 drilled into a
  frame's own 60×60 void reported `engaged`, `warning=None`, removed exactly
  one cylinder for two instances and the record said `count: 2`. The rungs are
  bbox (0.014 ms, a proved miss, because a box contains its shape), an axis
  point classified `TopAbs_IN` (0.041 ms, a proved engagement, because the tool
  contains a ball around any interior point on its own axis), then the exact
  `(part & tool)` probe **for that instance alone**. The statuses therefore
  equal `verify="exact"`'s; `exact` only adds `engaged_mm3`/`contact_mm2` on
  every instance, at 372 ms against 115 ms for 50 holes. The sample count is a
  performance knob, **not a tolerance**: `False` from the axis rung concludes
  nothing and escalates.
- **A record's `count`, `positions` and `centers` cover only the instances that
  demonstrably removed material.** The rest are in `dropped` with their status,
  in the helper's warning, and — because every bundled part spells the call
  `part, _r, _w = holes.…` and throws that warning away — in the *harvest's*
  warnings too. `verify="off"` is the one mode whose count is intent.
- **`record["verify"]` is the mode REQUESTED; `instances[i].probe` is the tier
  that answered.** The default runs up to three probes and one call routinely
  uses two, so there is no single tier for the record to name: `"verify":
  "bbox"` beside `"probe": "axis"` is the request beside the answer. Three
  docs said `verify` named the tier and all three were wrong.
- **A record is intent; a drawing is a measurement, and the drawing re-measures
  THREE THINGS — not "everything it asserts", which is what this line used to
  say.** `carry()` deliberately does not verify survival, so the drawing pack
  (1) prints the count of circles it MATCHED, never the record's; (2) refuses a
  record whose designation is not what its own numbers spell; (3) classifies
  one point past a blind record's recorded bottom. On (3): an M8 recorded blind
  at 6 mm and later drilled through printed `M8×1.25 - 6H ↧6` with no warning
  at all; it now prints `designation_base` and puts the recorded number in a
  warning. **Degrade the claim, never guess.**
- **`bottom_present` and `seat_present` are named for exactly what they
  measure.** `bottom_present` was once `depth_verified`, which claimed more: it
  catches a hole made DEEPER and cannot catch one made SHALLOWER, because
  milling 3 mm off the top of a 12 mm plate holding a 6 mm blind hole leaves
  the bottom where the record says it is — the sheet prints `↧6` over a 3 mm
  hole, `bottom_present: true`, byte-identical to the control. `seat_present`
  exists because a counterbore's pocket and a countersink's cone travelled
  inside `designation` and printed **unmeasured**: two seats milled entirely
  off a 30 mm plate still printed `⌀9 ⌴⌀14.5↧8.8` and `⌀6.6 ⌵⌀13.44×90°`, four
  numbers for features that did not exist, byte-identical to the control. It
  is **"nothing surrounds it at any of four azimuths at its mid-depth, or its
  space is not empty"** — that sentence and no wider one, because this line has
  now been wrong three times by saying "catches a seat machined away". It
  catches a seat region milled off completely and a pocket **filled back in**
  (measured: volume 430 091 against the control's 429 198, *above* it, and the
  around-probe alone read `true` at any sampling density — "is there material
  around the seat" is not "is the seat a void"). It does **not** catch a seat
  milled off that leaves anything at one azimuth (a 2×2 mm pin reads `true`), a
  slot cut across it leaving 0.25 mm crescents, or a changed diameter/depth/
  angle. That is the `any` bias and it is a **measured** choice: a
  bbox-filtered `all` catches the pin and the slot and keeps both edge cases,
  and reads `false` on a CORRECT counterbore beside an ordinary pocket —
  degrading a true drawing on a routine layout, which is the worse failure.
  Both degradations are spelled by `designation_for_record` from a modified
  copy, so a degraded callout uses the honest grammar. The recorded diameter is
  still not re-measured beyond `_HOLE_DIA_TOL`, and nothing off the top view is
  measured at all.
- **`corroborated` travels ON THE RECORD, not only in the tables.** Every
  non-`drilled` record carries `provenance: {standard, sources, corroborated,
  conflicts}`, unioned over every row that fed it (a counterbore has two), and
  `add_holes` echoes it. It did not until round 3, and the consequence was
  exact: the single-sourced ISO 10642 ⌀17.92 seat and the *adjudicated* ANSI
  ⌀0.196 clearance hole both became manufacturing callouts with the label left
  behind in the JSON. A **disputed** cell (`conflicts` non-empty) also warns; a
  merely single-sourced one does not, deliberately — "ISO 10642 has one source"
  is permanent and unfixable, and a warning nothing can ever clear teaches
  readers to ignore warnings (PRD-004's `strict_exempt` lesson).
  **`validate_record` RE-DERIVES the provenance from the record's own size,
  fit, standard and fastener and compares it**, exactly as it does the
  designation — and for the same reason, found the same way: checking only
  internal consistency let the genuine disputed record, with its conflict note
  deleted and `corroborated` flipped to `true`, validate clean, along with
  citations naming publications in no table and one citation listed twice.
  `provenance["standard"]` is **always a list**, even of one. **And `size`/`fit`
  are typed record keys tied to `d`**, because they *select* the row everything
  else re-derives from: mutating `size` `#8`→`#10` together with the provenance
  it entitles — both sides consistently — left `d` at 4.9784 and the callout at
  the disputed `⌀0.196` while the record claimed corroboration over agreeing
  publications, and validated clean. A key that steers validation and is itself
  unvalidated is the shape of this whole defect class.
- **Two surfaces describing one hole must not disagree.** `add_holes` echoed a
  single `lookup()`, which for a seat family is the fastener HEAD row, so it
  told an agent `corroborated: true` about the very cell whose record said
  `false` with a conflict. The echo now goes through `merge_provenance` over
  the same rows the record uses. A comment there asserted the opposite of what
  the code did, which is what stopped a reader from checking.
- **One record contract, `hole_standards.validate_record`, and nothing else
  anywhere.** The harvest raises it, the drawing skips on it, the
  `.holes.json` reader discards on it. It was three checks — the drawing's was
  five key *names* — and a plausible dict `setattr`-ed onto the shape printed a
  fabricated callout. It is structural AND self-consistent: it re-derives
  `designation` from the record's own diameter, depth and thread
  (`designation_for_record`, which `toolkit.holes` also uses to *build* it).
  **It is not an authentication boundary and must never be called one** — a part
  script runs arbitrary code and can simply drill the hole; what it closes is
  the *stale or inconsistent carrier*. Same for the sidecar, which now also
  compares the **cache key it has always stored and no reader ever read**, and
  enforces the four-state `holes`/`dropped`/`warnings` invariant:
  `{"holes": [], "dropped": 0}` is an impossible fifth state that used to be
  served as "records were lost", silently. `HOLES_SIDECAR_VERSION` is 2.
- **A blind hole always prints its depth, and each `↧` qualifies the `⌀` group
  it follows.** `clearance`/`counterbore`/`countersink` omitted the hole depth
  entirely — a blind ⌀9 read `⌀9`, which a shop makes through — while a
  counterbore showed only its POCKET depth. Blind is `⌀9 ↧6 ⌴⌀14.5↧8.8`;
  through-hole strings are byte-identical. And **`depth >= stock` is not
  blind**: the guard tested `>`, so `depth=t` on a `t` plate passed silently
  and the drawing then called out a depth on a hole open at both ends.
- **Hole-table provenance is PER CELL. No file-wide default AND no row-level
  fallback — they are the same mistake at two depths.** "Two published sources
  must agree or the row does not ship" was never what the loader checked: it
  counted the FILE's source strings, so a row copied from one publication
  shipped `corroborated: true` on its neighbours' citations. Fixing that at the
  ROW level left the identical hole one level down — an `M12×1.5` pitch cell
  with a fabricated `tap_drill: 99.9` added to the group-covered `M12` row
  loaded and answered `corroborated: true` over two named publications, while a
  whole new row was correctly refused. **`size/pitch` tables are exactly where
  the data legitimately grows a cell**, so that was the likely real path. Each
  file declares `row_shape`; `provenance.groups`/`scopes` name **every data
  cell** — where a cell is the path `row_shape` names and **no finer**: one fit
  of one clearance size, one pitch *or other scalar* of one thread size (the
  scalars are the complement of the `pitches` container, not a list of names —
  an allowlist let `M8/preferred_pitch` load), one size of one head table. The
  fields INSIDE a cell (`{d, drill}`, `{head_d, head_h}`) are covered by that
  cell's citation and are **not declared separately**: 248 declarations against
  442 scalar leaves, said out loud in the refusal message itself, because a
  cell is what one line of the published table prints and `_prov_scope`
  resolves at exactly that depth. **The reason is NOT "a field no lookup
  reads"** — that clause was checkably false and is the last thing this review
  found: `cell.get("drill")` and `entry.get("drill")` read an *optional*
  in-cell field, ISO thread pitch cells carry none, so `drill: "FAKE-99"` in
  the declared `M8/1.25` cell loaded and `thread("M8")` served it as
  corroborated. The true reason is that **a value added to an optional field is
  exactly as uncatchable as a value edited in a required one**: coverage proves
  citation, never correctness, at every granularity. What `_check_cell_fields`
  does close is a field NAME no cell of that shape may carry — a fabricated
  `fabricated_mm`, and a typo like `head_dd` that would otherwise surface as a
  `KeyError` from inside `cbore()` rather than as a load error naming the
  file.
  `coarse_pitch` is a cell — it names the pitch `thread(size)` answers from,
  and flipping `iso_thread`'s M8 from 1.25 to 1.0 changes the answered tap
  drill from 6.8 to 7.0. An undeclared cell, an
  entry naming a cell that is not there, or two cells whose `/`-joined names
  collide all fail to load naming the cell. `sources` are compared
  **normalised** — whitespace collapsed (which covers NBSP), zero-width
  characters dropped (**eight named codepoints**, listed in `_INVISIBLE`, not
  the unbounded claim "zero-width characters" — U+00AD, U+200E and U+180E were
  outside the set while the docstring said otherwise), `http://` folded onto
  `https://`, case folded — because
  every one of those is a way to paste one publication twice and have the file
  claim two behind one number. All six files are validated at
  `hole_standards` **import** (`validate_all`, 0.58 ms) rather than one at a
  time on first lookup — though the module itself is imported lazily by its
  callers, so that means eager *within the module*, not at process start.
  **`corroborated` means two or more sources that AGREE**: `ansi_clearance`'s
  `#8 NORMAL` is two sources that disagreed, ships adjudicated (drill #9 /
  0.196, the rejected 0.190 in `conflicts`) and answers `corroborated: false`;
  the nine ISO 10642 countersink rows are single-sourced and answer false too.
  **A one-source or disputed cell may ship, labelled** — dropping the ISO 10642
  column would remove every metric countersink, and shipping it silently is
  what the rule exists to prevent. No wrong numeric value has ever been found
  in these tables (~90 spot-checked against the published standards); the
  defect was always the claim.

**Geometry facts worth not re-deriving:**

- **Geometrically identical is not byte-identical.** Cutting `gusset_plate`'s
  holes as `part - Compound(cylinders)` gives the same face count and a volume
  differing by a relative 2e-16 — and a different mesh (0147). That is why the
  example goldens assert an `.acm` sha and not just numbers, and why the
  helpers re-enter build123d's own `BuildPart`/`Locations`/`Hole` rather than
  hand-rolling booleans.
- **For a through hole, sliding the workplane ALONG the hole axis is
  byte-free; rotating it ABOUT the axis is not** (0153). `plane="top"` costs
  nothing in bytes; the named `"left"` face carries its own frame
  (`x_dir = -Y`) and a cutting cylinder rotated about its own axis
  re-tessellates. `construction/angle_bracket`'s vertical leg therefore spells
  its `Plane` out.
- **The cache key hashes the SCRIPT TEXT**, so any rewrite whatsoever mints a
  new `.cache/<key>.acm` — half of PRD-010's AC1 was never achievable, and the
  `.holes.json` sidecar invalidates itself on the same key.
- **`patterns.*` skip instance 0**, because a shape seed is already fused into
  the part. Re-adding it is safe (one valid solid, identical volume) but **not
  byte-free** (0149). `count` is the total, CAD-style; `count=1` is a no-op
  with a warning.
- **`flat_outline()` is derived from `unfold()`'s own top face**, not a
  parallel model — that is what makes FR12's consistency a fact instead of an
  invariant. It costs an `unfold()` (11–70 ms) where v1's walker was free.
  `fold()` and `unfold()` differ by **exactly** `angle_rad·(0.5 − k)·t²·span`
  **per bend**, the k-factor's own neutral-fibre offset (residual −0.0), and —
  **for a part with no mitred corner** — by nothing else; that is the model's
  tolerance, not an error, but **it is a sum over the bends and grows linearly
  with the bend line**. Measured (t=2, k=0.44, R=3, 90°): one 60 mm bend
  22.619467105842887, two 45.238934211700325 (exactly twice, residual 7.3e-12),
  three totalling 160 mm 60.318578948928916. Judge the *sum* against your
  process, never the 11 mm³ of one bend. A `close` corner adds a second,
  larger term, because the mitre is cut through the sheet's **thickness** in
  the fold and at the **neutral fibre** in the blank: measured on the corner
  bracket, 65.337653 against the 37.699112 the two bends alone give. Both
  numbers are the same model seen from the two sides of one k-factor. The
  mitre is cut from `unfold()` too — without it the blank has a square corner
  and cannot be bent into the model, and the two tabs silently claim one piece
  of sheet (0161).
- **`add_holes` writes source, so nothing but a validated table key or a
  `repr(float(...))` reaches the output** — the same rule `sketch_emit`
  learned when a crafted `part` put `import os` on line 2 of a generated
  script. A picked `face_index` is resolved to a literal `Plane` basis at edit
  time with the renumbering caveat inline; a *named* plane stays a name,
  because a name is a predicate re-evaluated every rebuild.

## Package gotchas (PRD-011 — read before touching `core/packages/`, the gate or the catalog)

Every item is traceable to a measurement in `docs/changelog/0166`–`0182`.
User-facing reference: `docs/packages.md`.

- **A subtraction inside `BuildSketch(Plane.XY.rotated(...))` can silently
  subtract nothing.** In the catalog extrusion rewrite (0182), one of four
  quarter-turn rotations of a channel polygon removed no area — 400 →
  351.51 → 351.51 mm², no error, no warning. The shipped code rotates the
  polygon's *coordinates* by exact quarter turns and sketches on the unrotated
  plane instead. If you subtract on a rotated plane, assert the area actually
  dropped.

**The trust chain, in one line each — these are the review-0181 fixes and each
one closed a way for `gate: green` to mean nothing:**

- **`use_part` binds to `packages_lock[name].content_id`, never to the cache
  receipt alone.** A receipt is written by whatever installed the tree, so two
  indexes publishing the same `name@version` with different bytes both produce
  *receipt-verified* caches. `cache.require_verified(name, version,
  expected_content_id=…)` compares against the lock — the git-tracked
  authority — and the header stamps the id **measured** from the copied bytes.
  A lock entry with no content id is refused.
- **Every JSON read in `core/packages/` goes through `packages/_json.py`.**
  `json.loads` raises **`RecursionError`** on a deep document and that is *not*
  a `ValueError`, so the hand-written `(OSError, ValueError,
  UnicodeDecodeError)` at eleven sites let a ~400 kB `index.json` take an
  unhandled exception out through `search`, `resolve` and `entries` — one
  poisoned index stopped every healthy index *behind* it. `_json` also refuses
  by **size before parsing** (`MAX_INDEX_BYTES`, `MAX_INDEX_PACKAGES`): a valid
  126 MB document cost 1.66 GB RSS.
- **The gate measures the INVENTORY, not the manifest.** `content.IGNORED`
  excludes `*.tmp`/`*.pyc`/… from the id, so a part declared at
  `parts/x.tmp` was proved by every stage and *not shipped* — a scriptless
  package advertised green. Both directions are now red `format` rows: a
  declared payload missing from the inventory, and an inventoried `parts/*.py`
  no part declares (code a consumer receives that no stage opened).
  `validate_package_manifest` refuses an ignored part path too.
- **The stages read a SNAPSHOT in the work cell** (`gate.SNAPSHOT_DIR`), not
  the live tree. The id used to be hashed once and the stages then read the
  directory live, so a swap-in/swap-back between the two endpoint hashes was
  measured and invisible. The published id is now the id of the bytes the
  stages consumed, structurally. The closing re-hash of the **origin** stays,
  as the publisher's earlier warning.
- **`LocalIndex.publish` re-derives the verdict; it never reads
  `report["publishable"]`.** It requires every gate stage exactly once, a
  summary that is the summary of its own rows, a status those counts imply,
  and `gate.verdict(rows)` + `complete`. A report whose rows said "build
  failed" published green, and so did one with no stages at all.
- **`provenance.header_sha256` covers the block.** `script_sha256` covers the
  body *without* it and `strip` eats the whole block, so deleting the security
  notice or laundering `index` read `ok`. It is an **integrity check, not
  authentication** — no secret, so it detects edits, not forgers; say it that
  way. A header with no digest is `unverified`, never `ok`.
- **An omitted argument does not overwrite a declared one.** `add_package`
  with no `version_req` used to write `"*"`, silently widening a `~1.0.0` pin,
  jumping the lock a major version and flipping materialised parts to
  `version_drift` — one Library-dialog click away. The declaration is read
  *before* the resolve, and `requirement_change` names anything that moved.
- **A C0 control character in a relative path is refused** in
  `content._lexical_parts`. A NUL escapes nothing, but `os.stat` raises
  `ValueError: embedded null character`, which nothing catches — an
  unauthenticated 500 on the preview route and a validator that raised instead
  of reporting.
- **`_git.validate_url` checks the ssh HOST, not just the whole string.**
  `ssh://-oProxyCommand=…/x.git` starts with `s`; git passes the host to
  **ssh**, where a leading `-` is an option, and the `--` separator protects
  git's argv only. `validate_ref` exists for the same reason (`--branch` is
  before the `--`).

**The second review pass (Codex xhigh, same changelog 0181 + 0183) — races,
identity and resources:**

- **A package tree must agree with its own `package.json`.** The index entry
  names `foo@1.0.0`; the tree says what it says; the content id proves only
  that the bytes are the bytes the index meant. `cache.refuse_identity_mismatch`
  is called at install **and** at materialisation, because a cache populated by
  an older build never saw the install-time check.
- **The cache receipt is versioned (`RECEIPT_SCHEMA`) and must carry
  `content_id`/`index`/`source`.** The offline path reconstructs a *git-tracked*
  lock entry out of it, so a partial receipt produced an offline "success"
  writing `null`s into both maps. A receipt that cannot do that reads
  `tampered`; `add` heals it when the tree still hashes to the index's id.
- **Publish hashes the COPY, in staging, before promoting it.** It measured
  `source`, then later inventoried and copied `source` again — a mutation in
  that window shipped bytes B under id A, and consumers would reject a
  "published" package for ever.
- **A yank is qualified by index.** `withdrawn` holds `(index, version)` pairs:
  index A withdrawing its `1.0.0` must not veto a warm cache entry that came
  from index B.
- **`ProjectStore._atomic_write` stages through a RANDOM name.** It used one
  fixed `<name>.tmp`, so two concurrent writers interleaved into the *same*
  staging file and each replaced the mixture into place — a **corrupt
  `project.json`**, not a lost update. `PackageManager.add/remove` additionally
  run under `manifest_scope` (in-process), and `LocalIndex.publish/yank` under
  `_index_scope` (in-process **plus** `fcntl.flock`, because publishing is a
  CLI action and the two writers are routinely two processes). The lock file
  lives beside `config.json`, **never inside the index** — an index is often a
  git repo, and "a refused publish leaves the index byte-identical" is a test.
- **`GitIndex` takes `subdir`.** A git index was `<checkout>/index.json`, which
  serves a repo that is *only* an index — and this repository is the
  counter-example (`catalog/` beside `agentcad/`). The dogfood test now drives
  the real layout; the old one copied `catalog/*` to a synthetic repo root and
  proved the fixture.
- **`use_part` validates overrides before it writes anything**, so a refused
  materialisation leaves no transient part for an undo to resurrect. A
  *successful* one with overrides is still **two** undo steps (create +
  set_params) — documented, not composed: the only suppression seam
  (`history.in_restore`) is process-global and would drop a concurrent
  caller's snapshot.
- **`manifest_merge.package_problems` runs at `merge._integrity`'s call site.**
  `packages` and `packages_lock` merge as independent maps, so theirs'
  requirement + ours' lock is a clean merge and a dependency **nobody
  authored**; nothing downstream notices, because `use_part` reads only the
  lock and the lock verifies fine.
- **The gate warns when a swept parameter moves no geometry** — the specs
  ceiling generalised: specs read the built object, so a `build(p)` that
  ignores `p` passes every spec at every variant. Reported, never enforced,
  because `inspect` normalises the PARAMS spec and drops unknown keys, so
  there is nowhere to declare a deliberately cosmetic parameter. Measured
  before shipping: **zero** of the catalog's 16 swept parameters trip it.

- **The pack is `core/tools_packages.py` and it registers NO gate provider —
  deliberately, permanently, with a test that says so.** `tools._load_tool_
  packs` walks `pkgutil.iter_modules` **alphabetically** and
  `tools_proposals.py:51` assigns `service.gate_providers = []`
  **unconditionally**; `pac` sorts before `pro`, so anything this pack
  appended would be silently discarded — no error, no warning. Same trap as
  `tools_run_checks.py`. The escape hatch is named in the module docstring so
  nobody rediscovers it: a *second* pack called `tools_publish.py` (`pub` >
  `pro`), or a lazy install from `routes_packages.py`. At `pac`,
  `service.specs` (`s`), `service.branches` (`v`), `service.proposals` and
  `service.gate_providers` (`p`) **do not exist** — read them inside methods.
- **The gate is a CORRECTNESS gate, not a security boundary**, and Decision
  11 fixes the eight places that must say so. What *is* a boundary: the index
  declares a content id, the cache verifies every fetch against the
  declaration, and `use_part` re-verifies the whole tree on every
  materialisation. An index that lies about both is a compromised index, and
  the reserved-and-empty `signatures` slot is the answer (PRD-031 FR2(d)).
  Never let a doc imply the gate screens intent.
- **A package is a DIRECTORY and its id is a canonical tree digest** —
  `sha256` over `"{path}\0{filesha}\n"` sorted by path, no mtimes, no modes,
  no walk order, symlinks refused. There is no archive, because tar is not
  byte-stable across producers (the DXF lesson). Ceilings: 50 MB / 5 MB per
  file / 500 files, measured at **1.1 ms** to re-hash a realistic package and
  **67 ms** at the ceiling — which is what pays for verifying on *every*
  materialisation instead of trusting a receipt.
- **Nothing content-indeterminate may enter the header or the lockfile.** No
  timestamp, no client id, no absolute path — both are git-tracked, so a
  machine fact breaks byte-identical re-materialisation and makes two branches
  adding the same package conflict. Machine facts live in
  `~/.agentcad/packages/<name>/.receipts/<version>.json`, and the receipt is a
  **sibling** of the version directory: a file *inside* it would be part of
  the content its own id attests to.
- **The header is immutable and its status is computed on every read** (five
  values, zero kernel calls) — PRD-008's anchor rule. So **`remove_package`
  does not touch one script byte**: the header is inside the script and the
  script text is the rebuild cache key, so rewriting headers to express a
  removal would re-key and rebuild every materialised part. On a fresh clone
  with a cold cache provenance reads **`unverified`**, not `ok`, and a
  *tampered* cache entry is `unverified` too — "we cannot compare" is not
  "fine".
- **The gate's claim is each parameter's own range plus every declared
  configuration — a SUM (`1 + Σ|sweep| + |presets|`), never the cross
  product.** Mutually-constrained parameters make the corner product redden
  *correct* content; an author who needs a corner declares it as a preset.
  Two consequences that bite: `set_params` stores a numeric **raw** and the
  worker clamps at build, so the `presets` stage validates against the
  *inspected* spec as well as applying it (a preset above the max would
  otherwise publish quietly); and overrides are **cleared between
  configurations**, because `set_params` merges.
- **`publishable` is blocked by a stage that produced no rows**, not only by
  rows. `validate --stages format` must not answer "publishable" — it did not
  look. **A stage with ZERO rows and no reason blocks too**: both branches used
  to key off `stage["reason"]`, so `make_stage(name, [])` was invisible to the
  verdict, to `exempt_skips` and to the summary, and `presets.json =
  {"format": 1, "presets": {}}` published green through a stage that had looked
  at nothing. Only `("presets", "no_presets_declared")`, `("specs",
  "not_declared")` — **(stage, reason) pairs**, so no other stage is exempted
  by a string it does not own — and the five world-facts in
  `PUBLISH_SKIP_EXEMPT` are exempt, and **every exempt skip is published as
  `<stage>:<reason>`** in the index entry, one shape, so a consumer reads what
  was not measured. A `presets` row whose configuration applies but does not
  build is a **fail**, never a `pass` saying "applied and built".
- **The gate really writes, so nulling the ephemeral service's write guard is
  load-bearing** — `checks._ephemeral_service` predicted exactly this. All
  three seams (`bus.on_publish`, `store.branch_resolver`, `store.write_guard`)
  are nulled, the last two **after** `build_registry`, and `_refuse_overlap`
  has one path PRD-004 did not: **the package source directory** (a cell
  inside it would change the id the gate is attesting to). The run re-hashes
  the package after its stages, and `LocalIndex.publish` re-hashes **again**,
  because a tree can move between a finished report and a publish.
- **Variant parts are written through `ProjectStore`, never `service.create_
  part`/`set_params`** — both build eagerly, so the build phase makes no
  manifest writes at all. The default variant **reuses** the scratch part the
  earlier stages made; a second part with the same script and no overrides
  reported every spec twice.
- **This feature is the kernel `connectors` handler's first server-side
  consumer**, and the moving side of a mate must be **rigid** — a part
  declaring only cylindrical connectors is mated against the bundled probe
  cube. A failed *batch* mate falls back to one round trip per connector, paid
  only by a package that is already wrong.
- **`_git.py` is not `history._run`, and the docstring says why in full**:
  `_run` hard-codes `--git-dir`/`--work-tree`, a **10 s** timeout and a
  redirected `HOME`. An index fetch has no work tree, routinely exceeds 10 s,
  and a private index is exactly the case that needs the user's credential
  helper. So: fixed argv, 120 s, `GIT_TERMINAL_PROMPT=0` **plus**
  `GIT_SSH_COMMAND="ssh -o BatchMode=yes"` when the caller set none (terminal
  prompts do not cover ssh), `HOME` untouched, URL validated, and
  `fetch` + **`reset --hard`** — never a merge, or a force-pushed index leaves
  the client on a document nobody published. `GitIndex` **refuses `publish` and
  `yank`**: both would write into a checkout the next `refresh()` hard-resets.
- **`refresh()` is once per client instance unless forced.** The obvious
  reading — every call — means a network fetch per keystroke in the Library
  dialog. `list_packages` deliberately never refreshes, so its `latest` is
  what the last refresh knows.
- **Bundled indexes are APPENDED, never prepended**, so a user's configuration
  always outranks the shipped catalog. The consequence is an escape hatch and
  not a merge: a user index *named* `agentcad-core` **replaces the bundled one
  entirely**, including as a publish target.
- **The cache is for "no index answered", not for "the index answered no".**
  The offline fallback skips a version a reachable index has **yanked** when
  the requirement is a range (an explicitly-named version still installs, as
  it does online). Without that, a yank only ever bound machines that had
  never installed the package — found by writing AC5 (changelog 0180).
- **`use_part` never touches an index or the network.** It reads
  `packages_lock`, `cache.require`s the whole tree, and copies. A package in
  `packages` with no `packages_lock` entry is **refused** — guessing a version
  invents a dependency.
- **A reference part (FR13) is a package part with no script.** `kind` is
  absent-means-`script` for ever (published versions are immutable);
  `contract`, `connectors` and `policy` report an exempt `reference_part`
  skip; the `build` stage stages the file into the cell's own `imports/` and
  builds it once; `is_valid: false` on imported geometry is **reported, never
  enforced** (PRD-004's rule — OCCT calls the shipped 180-solid rocketry STEP
  invalid). A configuration naming a reference part is a **fail**, not a skip.
  **`use_part` refuses a reference part**: the provenance header lives inside
  the script, so a materialised one could carry no provenance at all —
  `import_cad_file` over the cached file is the documented path, and it is
  FR13's one hole in v1.
- **`face_info` takes a script, so it cannot read an imported solid's faces.**
  That is why `kernel/handlers/reffaces.py` exists (`reference_faces`), and
  why the mesh-derived alternative does not work here: the reference build
  path writes **no `.faces.u32` sidecar**, so `anchors.signature_table` returns
  zero rows for a reference part — and an area-weighted normal over a closed
  cylinder nearly cancels, so an axis is not in it anyway.
- **The build fan-out was DELETED and `--jobs`/`jobs` no longer exists**
  (changelog 0181). The plan pre-registered "under 1.5× on a 3-worker pool,
  delete it" and three independent measurements came in at **1.08× / 1.40× /
  1.17×** against an Amdahl ceiling of **1.42×** (only 44% of the build stage
  is inside kernel calls). It was never reproducible either — `KernelPool._pick`
  routes on `hash(affinity) % size` and `hash(str)` is `PYTHONHASHSEED`-
  randomised, so a speedup was a per-process sample, not a property — and it
  cost determinism where it mattered: under `--budget`, `jobs=1` and `jobs=4`
  disagreed on `complete` and therefore on `publishable`. The old safety
  premise was false too (a preset whose params equal a swept variant collides
  on one cache key). **Do not re-add it**; the build stage is sequential in
  `plan` order and its report is byte-identical to the old `jobs=1` output.
- **Names that already mean something else:** `registry` is the `ToolRegistry`
  (never the package registry — the manager is `service.packages`); `items`
  never `checks` for rows; `preset` is a *place* and `configuration` is the
  object; package provenance is always qualified (`package_provenance`,
  `packages/provenance.py`) because `provenance` already means PRD-010's hole
  citations and PRD-002's packet provenance.
- **The index digest publishes name/type/range/unit per parameter and no
  `description`**, so the Library dialog's description column is empty for
  catalog packages. The data is in the package; the digest is a listing.
- **Licensing is settled** (founder decision, Aug 2026): the repository is
  **Apache-2.0** (`LICENSE` + the `pyproject.toml` fields) and every seed
  catalog package declares the same. The format requires a licence per
  package; third-party packages choose their own.

## Conventions (match these)

- **Structured errors**: `{"error": {"type", "message", "details"}}`; script
  failures carry `details.traceback` and `details.line`, and `details.hint`
  from the Error Doctor. Mutating operations return post-state, never bare OK.
- **Atomic writes** (temp + `os.replace`) for every manifest/script/cache file.
- **Determinism**: same script + params ⇒ identical geometry and byte-identical
  meshes. Cache key = `sha256(content, params, density, tolerance)`.
- **Security/trust**: server binds `127.0.0.1` only, with a Host-allowlist +
  same-origin guard (`server/app._browser_request_allowed`) on HTTP and WS.
  Part scripts run with user privileges by design (local single-user tool);
  on macOS the kernel worker is additionally confined by a deny-by-default
  `sandbox-exec` profile (`kernel/sandbox.py`: writes only in project roots,
  no network; `AGENTCAD_NO_SANDBOX=1` opts out). API key from env only,
  never persisted.
- Comment density and style: match the surrounding file. Comments state
  constraints the code can't, not narration.

## Testing (`make test`)

- Two xdist workers run by module scope, each with one **session-scoped
  `kernel` fixture** (`tests/conftest.py`) that amortizes the warm import.
- Ordinary service tests use `make_test_service`, which disables synchronous
  git snapshots; `tests/test_history.py` and MCP integration keep real history.
- Mark broad/process-heavy coverage `slow`; `make test-fast` excludes it while
  `make test-pr` can still include it when it is required for merge confidence.
- Mark only unusually broad stress coverage `exhaustive`; PR CI excludes it,
  while scheduled/manual exhaustive CI and local `make test` always run it.
  The marker is not a shortcut for an ordinarily slow regression test.
- Mark tests `portability` when they exercise behavior that can vary by host OS
  (filesystem/encoding, Git, subprocesses, local sockets, OCCT wheels, or CAD
  import/export). Linux and Windows CI run this focused group; do not mark pure
  domain logic merely to increase the cross-platform test count.
- **Examples tests run on a copy** — never mutate `examples/` in place.
- FastAPI `TestClient` must pass `base_url="http://127.0.0.1"` and, for
  WebSocket tests, `create_app(..., extra_allowed_hosts={"testserver"})` (the
  host guard).
- FEM tests use `pytest.importorskip` — the suite is green **without** the
  `[fem]` extra. **Never `uv sync`/`uv pip install` into the shared venv from a
  parallel agent** — use a scratch venv.
- Reproduce a bug as a failing test before fixing (see the mesh-normals fix and
  its `tests/test_mesh.py` regression tests for the pattern).

## Changelog — REQUIRED for every commit

**Every commit must include a detailed changelog entry under
`docs/changelog/`.** Stage it *with* the change so the entry lands in the same
commit. One file per commit, named `NNNN-<slug>.md` where `NNNN` is the next
zero-padded sequence number (highest existing + 1) and `<slug>` is a short
kebab-case summary. Follow the template in `docs/changelog/README.md`:
a header (sequence, date, one-line summary), a **Summary** paragraph, a
**Changes** list (grounded in what the diff actually does, not just the commit
subject), a **Files** list, and **Notes** (rationale, gotchas, follow-ups).
Write the changelog from the real diff, not from memory.

## Definition of done for a change

1. `make test` green (state the count; no unexplained skips).
2. New/changed behavior has a test; a bug fix has a regression test.
3. Docs updated if the surface changed (README, `docs/*.md`, and the
   `CHEATSHEET` in `templates.py` for authoring-facing changes).
4. For UI changes: verify in a real browser (screenshot), zero console errors.
5. **A `docs/changelog/NNNN-<slug>.md` entry is written and staged with the
   change** (see the Changelog section above).
6. Commit messages end with the `Co-Authored-By` trailer. Don't commit
   server-generated manifest reformatting churn or the shared venv.

## Where to read more

- `docs/architecture.md` — processes, components, ACM1 format, rebuild flow
- `docs/agent-api.md` — the 73/76 agent tools with schemas + a worked loop
- `docs/geometry-ci.md` — `agentcad check`, the report schema, the GitHub Action
- `docs/part-authoring.md` — the script contract, toolkit, mates, sketch solver
- `docs/user-guide.md` — the UI surface by surface
- `docs/roadmap.md` — the PRD index with statuses (what we're building and why)
- `docs/prd/` — one detailed PRD per roadmap feature (see `docs/prd/README.md`)
- `docs/market_research.md` — the competitive/market evidence behind the roadmap
- `docs/superpowers/specs|plans/` — the design specs and implementation plans
