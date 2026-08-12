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
  detector reads the CONSTRAINT GRAPH.** `dist(centre, line) - r` and
  `d(c1,c2) - (r1 ± r2)` sit at an extremum of the manifold the pinning rows
  cut out, so their gradient falls into that span: the row reports itself
  redundant while removing a real DOF, and `max_residual` reports the *square*
  of the geometric error (measured 8.11e-11 reported against a true
  4.97e-05 mm before the fix — a ratio of 6.1e+05; 10.0 after it, which is
  the radius). This has been found three times — structurally shared handles
  (slice 6), a `coincident` junction
  (slice 10), a `point_on_circle` junction (review) — because each fix keyed
  off a hardcoded handle list. It is now derived from `ON_CURVE_ARGS`:
  **every constraint type is classified as an incidence or explicitly not one,
  and a test fails when a new type is added without that decision.**
- **The rank must not be a function of row scale.** One 1e-9 mm line (a GUI
  double-click) writes 1.4e+09 into the Jacobian through `_accum_dir`'s `1/n`,
  and a relative singular-value threshold then reads every honest row as zero:
  measured rank 3 of 7 and `dof 7` on a pinned rectangle. `Sketch.row_scaled`
  normalizes rows before the SVD, and where the greedy dependent-row pass runs
  to completion **its count is the rank**, so `status`, `dof` and the blame
  sets agree by construction — `over_constrained` with an empty blame set is a
  bug, not a display problem, and the chip branches on `status`.
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
- `docs/agent-api.md` — the 65/68 agent tools with schemas + a worked loop
- `docs/geometry-ci.md` — `agentcad check`, the report schema, the GitHub Action
- `docs/part-authoring.md` — the script contract, toolkit, mates, sketch solver
- `docs/user-guide.md` — the UI surface by surface
- `docs/roadmap.md` — the PRD index with statuses (what we're building and why)
- `docs/prd/` — one detailed PRD per roadmap feature (see `docs/prd/README.md`)
- `docs/market_research.md` — the competitive/market evidence behind the roadmap
- `docs/superpowers/specs|plans/` — the design specs and implementation plans
