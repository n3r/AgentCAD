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
make test-fast    # parallel suite (workers auto-scale, cap 8) minus slow tests
make test-pr      # required PR gate; defers exhaustive bundled-engine coverage
make test         # full parallel suite; needs the kernel (PYTEST_PARALLEL to override)
make test-portability  # OS-sensitive filesystem/process/kernel smoke suite
make test-linux   # Linux worker confinement, inside the agentcad:local image
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
        │  FastAPI server (127.0.0.1 local / bound+authed hosted) │
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
- **`actor_kind` is `human` for the `browser` identity and for an
  authenticated `user:` principal.** The chat dock is a human asking an
  *agent*, so those actions are the agent's. In local mode it is still
  bookkeeping rather than authentication — anyone can send any `X-Agent-Id`.
  In **hosted** mode a `user:` prefix is authenticated (the guard composes the
  principal and `X-Agent-Id` can only contribute the device suffix), which is
  why `core/proposals.actor_kind` must classify `user:nikita/browser:7f3a1b2c`
  as human; see the hosted-core gotchas.
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
- **`actor_kind`/`author_kind` is bookkeeping in LOCAL mode, and
  authenticated in hosted mode.** It is `human` for the browser identity and
  for a `user:` principal. In local mode any client may claim any
  `X-Agent-Id`, so say so on the surfaces that show it; in hosted mode the
  `user:` half is the guard's own composition and cannot be spoofed
  (PRD-005a). `core/proposals.actor_kind` must classify a composed
  `user:<handle>/<device>` as human — see the hosted-core gotchas for what
  broke when it did not.

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

- **Any file that participates in a content id must be written with
  `newline="\n"`** (or as bytes). Windows text mode translates `\n` →
  `\r\n`, and git then normalises it back at commit — so the advertised id
  (hashed over CRLF on disk) can never match the served tree (LF in the
  repo). Three CI rounds on PR #15 were this one lesson in three coats:
  the clone side (`_git.run` pins `-c core.autocrlf=false`), the repo side
  (`.gitattributes` `* -text`), and the write side (`from_step`, receipts,
  and every test fixture that hashes what it writes).

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

## Configuration gotchas (PRD-012 — read before touching configs, the build path or the merge)

Every item is traceable to a measurement in `docs/changelog/0200`–`0209`.
User-facing reference: `docs/agent-api.md` (the `Configurations` section) and
`docs/user-guide.md`.

- **The kernel never sees a configuration, and resolution never happens in the
  store.** Every request carries a resolved override map exactly as before.
  Resolving inside `ProjectStore.get_part` would make the next `set_params`
  bake the active configuration into the overrides — a data-corruption bug,
  not a shortcut. The two pure members on `PartRecord` are
  `config_params(name)` (pure: defaults < configuration) and
  `effective_params` (working state: defaults < active configuration <
  explicit overrides), and "PARAMS defaults <" costs no code because
  `worker._resolve_params` already fills every unset name.
- **Resolution is TOTAL over the value and strict about the name.** `configs`
  is JSON a merge or a hand edit can shape, and the driver merges a non-object
  entry **whole**, so `5`, `None`, `{"label": "M"}` and `{"params": None}` are
  all reachable without a hand edit. `config_params`/`effective_params` return
  `{}` for every one of them (an unknown *name* is still a `KeyError` — that is
  a programming error). This is not defensive polish: `_cache_key_for` reads
  `effective_params` inside `_ensure_built`, **upstream** of every
  configuration-aware branch, so raising there was a 500 on
  `GET /api/projects/{p}/parts/{id}` — the browser's first read of a part — and
  on every build of it. The damage is reported, not swallowed:
  `manifest_merge.config_problems` emits `malformed_configuration` (a
  **warning**, like the dangling `active_config`, because it resolves as base),
  and `merge.py` routes it into `report["warnings"]`. Guards at call sites that
  read *`params`* are therefore redundant; guards that read *`label`* off the
  raw entry are **not** (there is no accessor for it) — do not delete those.
- **`_rebuild(proj, part_id)` and `get_part(proj, part_id)` keep their
  signatures byte-for-byte.** `tools_specs`, `tools_holes` and
  `tools_packages` rebind them with **two-positional** wrappers, and packs load
  alphabetically, so a `config=` kwarg would be a `TypeError` through the chain
  *and* `configs` would sort innermost. The body lives in
  `_build_with(proj, record, *, affinity, status_key, config=None)`; a pure
  build goes through `_ensure_config_built`. The accepted consequence: those
  wrappers do **not** decorate configuration builds, which is why
  `build_configs` produces per-configuration `spec_results` deliberately
  instead.
- **`_status` stays 2-tuple keyed and a configuration build writes none of
  it** — `_config_status[(lock_key, part, config)]` is a separate dict, swept
  by the same two prefixes (`_forget_status`, `delete_part`). This is a
  livelock guard, not bookkeeping: with one slot per part, two instances bound
  to different configurations miss the memo on alternate `get_assembly` reads,
  republish `rebuild_finished`, and the browser's handler schedules another
  refresh — a self-sustaining 400 ms loop with full mesh re-downloads. A memo
  hit **publishes nothing**.
- **Config names are lowercase** (`CONFIG_RE = ^[a-z0-9][a-z0-9_-]{0,31}$`) and
  `label` is the display name: `s`/`m`/`l`, never `S`/`M`/`L`, so the export is
  `flange_l.step`. The object is a **configuration**; `preset` names only where
  one lives (a configuration a package publishes); never `variant` (`Variant`
  is the gate's build-sweep namedtuple). `config` already means
  `~/.agentcad/config.json` in `library.js` and `AGENTCAD_CONFIG` in tests.
- **A declared configuration is range- and enum-strict; `set_params` on top
  clamps.** The publish gate already chose *refuse* for presets, and a family
  is a published thing. Values are also **normalized on write**
  (`service.normalize_params`), or `{"n": 3}` and `{"n": 3.0}` are two
  configurations and two cache entries for one geometry.
- **`build_configs` is serial and de-duplicated by cache key**, `affinity=part_id`
  throughout. Do not re-add a fan-out: PRD-011 measured the identical
  many-variants-of-one-part parallelism at 1.08×/1.40×/1.17× against a
  pre-registered 1.5× bar and **deleted** it, and two members sharing one key
  race the worker's fixed-name `.tmp` staging. An empty matrix always carries a
  `warnings` reason (nothing declared / nothing requested / none of the
  requested names declared) — never an empty list with no explanation.
- **`set_active_config` clears the explicit overrides only when the active
  configuration actually CHANGES.** Switching *loads* that configuration, so
  the overrides go (one publish, one undo step) unless `keep_overrides`; but a
  re-selection of the already-active name, or a `DELETE …/active-config` on a
  part already at base, must not drop a `set_params` value as a side effect of
  a no-op. The consequence for the browser is written down because it is not
  guessable: the chip's **Reset to M** cannot be `set_active_config m` while
  `m` is active — it removes the overrides the pinned way, `set_params` with
  `null` per parameter.
- **Divergence is semantic, not syntactic.** `diverged` is
  `effective_params != config_params(active)`, so an override equal to the
  configuration's own value is **not** divergence (the geometry, and the cache
  key, are the pure configuration's), a parameter the configuration does not
  set at all counts, and a dangling `active_config` resolves as **base** and
  never diverges.
- **Assembly meshes are addressed by `mesh_key`, not by part.** `get_assembly`
  publishes every *built* instance's cache key and the browser fetches through
  `GET /projects/{p}/meshes/{key}` — a pack route that serves `.cache/<key>.acm`
  behind a `fullmatch` `^[0-9a-f]{32}$` gate (`$` also matches before a
  trailing newline, so an anchored `.match` accepted `"<key>\n"`) and **never
  builds**. There is deliberately no `?config=` parameter: one identity for a
  mesh, not two. `app.py`'s mesh routes are byte-identical.
- **`routes_configs._result`: a refusal raises, a build post-state is a 200
  whatever its `ok`.** The two are told apart by shape — `ToolRegistry.call`
  emits exactly `{"error": …}` for a refusal (**no `ok` key**) while a rebuild
  always carries one, so the raise is gated on `"ok" not in payload`. Only
  `set_active_config` merges its rebuild at the top level (`set_part_configs`
  nests it under `out["rebuild"]`), and that is the case this exists for: the
  manifest was written and `project_changed` published, so answering 422 gave
  the client a world-model no retry fixes and left the browser repainting the
  switcher from stale state. `_BODY_ERRORS` is still empty; the docstring's old
  absolute ("nothing about a configuration is a legitimate HTTP 200 error
  body") is gone.
- **`service.rebuild_after_write(proj, part_id)` is the seam for the rebuild
  that follows a LANDED write** — `set_params`, `update_part`,
  `set_active_config`, `set_part_configs`' nested rebuild and
  `set_solid_materials`, all five of them: it converts an `AppError` raised
  before the kernel is reached (script file gone, entry gone, unknown material,
  resolver refusal) into the same `{ok: false, error}` post-state the
  `KernelError` arm produces — `_status` written with `cache_key: None`,
  `rebuild_failed` published, plus its own `_REBUILD_REFUSED_HINT` — because
  the write already happened. **`_rebuild` itself still raises, and must:** it
  is also the READ paths' build (`_ensure_built` ← `get_metrics` / `mesh_info`
  / `ensure_mesh` / `mesh_summary` / `get_assembly`, plus `packet`, `checks`,
  `merge`), and those callers re-raise an `ok: false` as a `KernelError` — so a
  total `_rebuild` turned a permanent, client-side 404 into a 502 on the first
  call and a 404 on the second (the two `_ensure_built` branches disagreed),
  split `get_assembly`'s answer by whether an instance carried a configuration,
  and moved `checks._build_item`'s row from `error` (+ a harness `errors[]`
  entry) to `fail`. Also read the post-state (`store.get_part`) **before** the
  publish, inside whatever lock the tool holds: a tail read after the rebuild
  reintroduces the same defect ten lines down.
- **Both drawing routes go through one `routes_drawing._drawing_result`:** an
  `AppError`-class refusal raises through `_result` (404/422, house type intact
  — the POST used to serve these as `200 {"error": …}`), and a kernel-class
  failure (the five `kernel/protocol.py` constants, imported and never retyped
  — there is no `"crash"`) re-raises as a `KernelError`, which `app.py` answers
  **502** with the worker's own type and `details.traceback`; the GET used to
  push those through `_RAISE`'s default and rename a timeout or a crash
  `422 ValidationError`.
- **`routes_configs._json` is strict, and the instance PATCH requires its
  key.** A parsed body that is not an object is a `ValidationError`
  (`"body must be a JSON object"`, 422); `{}` means a **genuinely absent** body
  and nothing else (it still reads the BYTES, not `content-length` — a chunked
  request carries none). `PATCH …/assembly/instances/{id}/config` then refuses
  a body with no `config` key, so **`{"config": null}` is the only way to
  unbind**: folding `[]`/`"bad"`/an empty body into `{}` made malformed input
  fire the tool's `config=None` default, which is the destructive verb — the
  one place in the branch where garbage silently mutated persisted state. The
  same `_json` is imported by `app.py` (export, `PUT /assembly`,
  `PATCH …/params`) and `routes_drawing.py`'s POST, where the identical shape
  used to be a 500 — and **`PUT /assembly` needs the same required-key guard
  for the same reason**: it is a full-list replace, so `{}` from an absent body
  wiped the assembly at 200 (`instances` is now required;
  `{"instances": []}` is the explicit clear). The rule generalises: *absence
  cannot mean "nothing to change" when the default is the destructive verb* —
  check it before putting any other route on the strict reader; `routes_drawing`'s SVG GET additionally gates `?config=`
  with a `fullmatch` `CONFIG_RE` before it builds a filename, and raises the
  refusal instead of serving it as JSON at HTTP 200 (its POST now raises too —
  see `_drawing_result` above).
- **Binding validation lives in `ProjectStore.set_instances`**, beside the
  unknown-part and dangling-mate refusals — three writers reach the store
  (`service.set_assembly`, `tools_mates._set_instance_mate`,
  `routes_assembly2.patch_instance`) and only the store sees all three. A
  bound instance resolves **purely**, so a part viewed with an override on top
  of `m` legitimately differs from its own instance bound to `m`.
- **`tools_configs` sorts at `con`** — before `drawing`, `holes`, `packages`,
  `proposals`, `specs`, `vision`. Read `service.specs` / `service.packages`
  inside handlers behind `getattr(service, …, None)`, and never append to
  `gate_providers` (`tools_proposals` resets it unconditionally — the
  `tools_run_checks` trap).
- **Every mutating tool writes under `manifest_scope` across the WHOLE
  read-modify-write**, not just the write: the FR11 referential check reads the
  part entry *and* the instance list, and `cleared_overrides` reports a map
  read before the write — a lock around the write alone leaves both TOCTOU.
  `set_instance_config` additionally takes `service._lock` (the lock
  `set_assembly` serializes the identical read-all/write-all on) but **no**
  `locks.write_scope`, because a claim is a *part* claim. Still open and
  deliberately out of scope: `tools_mates._set_instance_mate` and
  `routes_assembly2.patch_instance` do the same read-all/write-all with **no**
  lock at all.
- **The merge reaches `configs.<name>.params.<param>`, and the per-name
  `_keyed` guard is load-bearing.** `_PART_ENTRY_DICTS = {"configs":
  ("params",)}` is *threaded* through `_merge_entry_list`/`_merge_entry`/
  `_write_entry`, never read as a module global — read globally, a
  forward-compatible *instance* field named `configs` merged deep and
  contradicted the docstring. A map entry that is not an object (a hand-edited
  `"m": 5`) merges **whole**; without the guard `_as_dict` rewrites it to `{}`,
  a clean merge that destroys data. `_write_keyed_entry` goes through the
  recorded path segments and **raises** rather than joining leftovers into a
  dotted flat key.
- **A selection lives in a different key from the map it names**, so one branch
  removing a configuration while the other selects it merges *clean*.
  `config_problems` reports it, deliberately asymmetric: an instance binding is
  **blocking** `integrity` (`dangling_instance_config` — the binding is that
  instance's whole parameter set and it now resolves to nothing), a stale
  `active_config` is a **warning** (it resolves as base), and so is
  `malformed_configuration` — the same damage one level down, a *member* the
  key-wise merge took whole that is no longer an object with a `params` map
  (`{"params": {}}` is a legitimate configuration and is never reported). The
  repair is a tool call, never a read-time fallback that hides it.
- **The dimension table is a MEASUREMENT, not a printout.** Every number comes
  from `build_shape` inside the drawing handler — which is why the timeout
  scales `120 + 60·rows`, why the cells echo the **resolved** parameter map
  (echoing the request's overrides printed an em dash wherever a ragged member
  used the script's default), and why the config cell reads `Label (name)`
  (the name is the identity every other surface uses; a member with no label,
  or one equal to its name, prints the bare name). SVG only: DXF ignores
  it as it ignores PMI, and the guard is `if dim_table and declared and format
  == "svg"` — a DXF request used to pay 60 s per configuration for a payload
  it discards.
- **`render_view` refuses `config` without `part_id`.** An assembly render
  takes each instance's own binding, so one image can legitimately mix sizes;
  silently ignoring the argument would hand back a different picture than the
  one asked for.
- **`SpecRunner._shape_tier(…, *, record=)` moves both halves of the identity
  together**: the sidecar key is `_cache_key_for(record)` and the `spec_eval`
  params are `record.effective_params`. A `record=` that keyed the sidecar but
  not the measurement would write the base numbers into a config-keyed file and
  every key assertion would still pass. `specs._project_key` catches
  `ValidationError` on a stale binding — escaping it evaluated the whole
  project's assembly tier uncached.
- **`m`'s cache key is not the base key**, even when a configuration's params
  *are* the script's defaults: the service hashes the override map (`{}` versus
  an explicit map) while the worker resolves defaults. Making them agree would
  need an `inspect` inside every key computation and would move every existing
  key. AC5's "identical resolved params" therefore means **identical override
  maps**.
- **Zero cost when unused is a test, not an intention.** `to_manifest` writes
  `configs`/`active_config`/`config` only when set, `set_part_configs {}` pops
  the key, base `rebuild_*` payloads gain no field, and there is no
  `SCHEMA_VERSION` bump (it is written and `setdefault`-ed but never read to
  branch behaviour). `tests/test_prd012_acceptance.py::test_ac8_a_project_without_
  configurations_is_byte_identical` grades it against PRD-010's `.acm` sha
  golden.
## Hosted-core gotchas (PRD-005a — read before touching `server/security.py`, `core/authstore.py`, `core/appmode.py` or the deployment)

Every item is traceable to a decision in
`docs/superpowers/specs/2026-08-17-hosted-core-design.md` or to a test.
Changelogs `0188`–`0197`. Operator-facing reference: `docs/deployment.md`.

**The defining fact, stated before anything else: an account on a hosted
instance is for someone you trust.** A part script is arbitrary Python
(`kernel/worker.py`). PRD-006 changed *half* of what that used to mean — the
Linux worker now confines itself (Landlock + seccomp, `hosted` read posture:
no network, no writes outside the granted roots, no reads of the state dir —
except the one `publications/build` subtree PRD-007's shared-pool variant
builds need, which is both a write root and (by construction) readable — and
nothing else under the server user's home at all — remember that a write root
is always readable, because `_read_roots` appends the write roots, which is
why the config dir stopped being one wholesale and why a hosted start refuses
a state dir inside one), so "an account
is a shell" is no longer literally true and the docs
no longer say it that way. What it did **not** change: the script still runs
as the server user and the **whole projects tree** is readable and writable to
it, across every project on the instance. So `member` and `admin` are still
**not** a security boundary between each other, a per-project ACL would still
be a label rather than a boundary (that is PRD-005), registration is still
closed by construction (there is no self-registration route), and the trust
sentence is still repeated in four places (FR17: `docs/deployment.md`, the
`compose.yaml` header, `agentcad admin … --help`, and the output of
`agentcad admin user add`). Do not add a feature whose safety argument assumes
per-member isolation. And when you print the sentence, do **not** reach for
`str.capitalize()` — it lower-cases "Python".

- **`actor_kind` must classify `user:` as human** (`core/proposals.py`). A
  composed principal `user:nikita/browser:7f3a1b2c` does not start with
  `browser:`, so before the two-line fix every signed-in human classified as
  an *agent* — and `ClaimRegistry.acquire` returns `None` for a non-human
  holder while `_blocking` never blocks an agent. The day hosting turned on,
  per-part claims would have silently stopped protecting anybody, with no
  error anywhere. `tests/test_claims.py` drives the real registry, not the
  classifier.

- **The anonymous surface is nine entries in one frozenset**
  (`server/security.py`: `PUBLIC_PATHS` + `PUBLIC_PREFIXES`) and default-deny
  means **a route pack added tomorrow is private with no action by its
  author**. There is deliberately no per-route `@public` decorator: a pack
  author must not be able to open the surface from their own module.
  `tests/test_hosted_surface.py` asserts the reachable set by **equality**, so
  a forgotten removal fails too.

- **`is_public` is `startswith` on `PUBLIC_PREFIXES`, so every prefix must end
  in `/`.** `/api/public` without the slash would make `/api/publicity` public.
  Each addition gets its own negation test in
  `test_paths_that_must_not_be_public`, and `/api/public`, `/api/publicity`
  and `/jsx/evil.js` are already in that list.

- **A naive `[r.path for r in app.routes]` walk sees 23 of ~83 routes.**
  FastAPI 0.141 leaves each `include_router` as one opaque `_IncludedRouter`
  with `path = None`, so the obvious enumeration misses **every** route pack —
  exactly the population it exists to police, passing green while a pack goes
  public. Use `tests/conftest.flatten_routes`, which recurses and is
  cross-checked against `app.openapi()`.

- **`routes_packages.py`'s `search` and `preview` walk EVERY configured
  index**, `scope: "private"` ones included. That is why they stay
  authenticated and why the anonymous catalog read is a separate pack
  (`server/routes_public.py`) that filters **before** it looks anything up.
  The filter needs **both** `index.configured_scope` (what the OPERATOR wrote
  in `config.json`) and `index.scope` (PRD-011's document-aware property) to
  equal `"public"`. Do not collapse them: `scope` lets the *document* win,
  which is right for publish policy and was wrong for access control — for a
  git index the third party who authors `index.json` could declare
  `"public"` over the operator's configured `private` and have the instance
  serve it to the internet (review finding M2, changelog 0198). Every miss on
  that pack raises **one name-free message** and carries `Cache-Control`, so a
  private package and a nonexistent one are indistinguishable and a flood of
  misses is still absorbable by a CDN; a private index configured first must
  not shadow a public package of the same name. It never calls `refresh()`, so
  an anonymous request cannot make the server perform a network git fetch.

- **The CSRF Origin check sits ABOVE the `principal is None` branch in
  `security.guard`, and that ordering is the control.** `POST
  /api/auth/login` and `POST /api/auth/enrol/{token}` are the only unsafe
  methods an anonymous caller can reach; a check placed after the anonymous
  early-return covers every route *except* the two it exists for, which is
  what it did for four changelogs (review finding M1, changelog 0198).
  Origin-absent is allowed, identically for anonymous and authenticated
  callers — a browser always sends it on a cross-site POST, `curl` and the MCP
  client may not.

- **The login limiter is keyed on `(handle, address)`, never the handle
  alone** (`security.login_key`). `TokenBucket.take` does not consume on
  refusal, so a handle-only bucket held empty by a stranger at 0.5 req/s was a
  *permanent* lockout of a known handle — and handles are public (presence
  rosters, comment authors, history trailers). The per-address bucket is the
  other half and stays. Measured before the fix: the victim, with the correct
  password, refused 6 of 6 rounds. **`address` is only real if the proxy
  plumbing is right:** it is `request.client.host`, which behind the deployment
  guide's reverse proxy is the proxy for *everyone* unless (a) uvicorn runs with
  `proxy_headers` bounded to the trusted peer — `cli._uvicorn_proxy_kwargs` does
  this in hosted mode, off in local — and (b) the proxy sets `X-Forwarded-For`
  (the nginx block must; Caddy does by default). Without both, the key collapses
  to per-handle (M3 round 2, changelog 0198). `AGENTCAD_TRUSTED_PROXY` bounds the
  trust and **refuses `*`** (`appmode.trusted_proxy`) — trusting every peer lets
  a client forge the address the limiter keys on.

- **`AuthStore.enrol` revokes every existing session for that handle.**
  `agentcad admin enrol <handle>` is the *recovery* path — it is run when a
  password was lost or stolen — so leaving the old cookies live let a thief
  outlast the reset by up to `ABSOLUTE_SESSION_S` (30 days). It is inside the
  same `_scope()` (reentrant) as the password write, so a reset cannot
  half-apply.

- **Registration order: `security.install(cfg)` must run BEFORE
  `build_registry(service)`.** A tool pack decides *at registration time*
  whether its tool can run (the FEM precedent), and `whoami` only registers in
  hosted mode. `build_registry` is evaluated in the caller, before
  `create_app` installs anything — so the obvious order leaves a real hosted
  server without the tool while every route test still passes. `cli.cmd_serve`
  and the `hosted` fixture both install first, each with a comment saying why.

- **`security.current_config()` has a process-global fallback, so a router
  must capture its configuration at MOUNT time, not per request.** Otherwise a
  *local* app built after a hosted one in the same process grows working auth
  routes backed by the other app's identity store. `create_app` installs
  before it mounts packs, which is what makes mount-time capture correct;
  `routes_auth` and `routes_presence`'s beacon rule both do it, and both have
  a test that builds a local app after a hosted one.

- **Identity state derives from `config.config_path().parent`, never from
  `--projects-dir`** (`core/appmode.state_dir()`, with an
  `AGENTCAD_STATE_DIR` override) — the derivation `AGENTCAD_PACKAGES_DIR` and
  `AGENTCAD_INDEXES_DIR` already use. That is why PRD-004/011 ephemeral
  services are unaffected *by construction*, and why setting `AGENTCAD_CONFIG`
  in a test isolates the identity store for free. No `AgentCADService`
  constructs or reads it.

- **`fcntl.flock` in `authstore`, because `docker compose exec` is a second
  writer.** `agentcad admin …` runs as its own process against the same files
  while the server is up, so the `LocalIndex._index_scope` idiom (registry
  RLock **plus** an flock on a `.lock` file beside the documents) is
  load-bearing rather than decorative; a nesting depth counter keeps an inner
  scope from blocking on its own outer flock. `fcntl` is imported through
  `try/except ImportError` so the module stays importable on Windows.

- **Staleness has no TTL, and that is the point.** `_read` reuses a cached
  parse only while `(st_mtime_ns, st_size, st_ino)` is unchanged, and every
  write stages a random temp file and `os.replace`s it — a new inode every
  time. So an account disabled or a token revoked through
  `docker compose exec` is honoured by the running server on its **next**
  request, with no restart and no polling. `resolve_session` additionally
  reads the *user row* live, so a role change or a disable takes effect on the
  next request too. Every read-modify-write re-reads with `fresh=True`; skip
  that and you drop the other writer's row.

- **Tokens are SHA-256, passwords are scrypt, and the asymmetry is
  deliberate.** A bearer secret is 256 bits of `secrets.token_urlsafe(32)` —
  there is nothing to brute-force, and scrypt would put tens of milliseconds
  on every agent request. A password is human-chosen, so it gets
  `hashlib.scrypt` at n=2^15, r=8, p=1 (**63 ms measured** on Apple silicon).
  That is **below** OWASP's scrypt minimum (n=2^17) and the module says so out
  loud: registration is closed, an account is already RCE on the host so the
  password is not the weakest link, login is rate-limited per handle *and* per
  address (which is what NIST SP 800-63B relies on against online guessing),
  n=2^17 is 4× the memory on a documented 2 vCPU / 4 GB floor, and the
  parameters are stored **beside every digest**, so raising them re-hashes on
  next login instead of invalidating accounts.

- **`create_app(security=None)` is the same code path, not a disabled
  feature.** The middleware branches once at the top and everything after that
  branch is byte-identical to what local mode ran before. "Local mode is
  untouched" is therefore a property of the diff rather than a test we have to
  keep passing. Security is constructed explicitly by the caller and fails
  closed — unlike `_mount_route_packs` and `_load_tool_packs`, which fail
  *open* (a module with no `router` is silently skipped), which is the whole
  argument for the one sanctioned `app.py` edit.

- **The healthcheck must present the configured `Host`.** The hosted guard
  requires `Host` to equal `AGENTCAD_PUBLIC_ORIGIN`'s host, so the obvious
  probe — `urlopen("http://127.0.0.1:8630/api/health")` — sends
  `Host: 127.0.0.1:8630` and is **403**: the container reports *unhealthy
  while serving perfectly*, which under `restart: unless-stopped` is a restart
  loop on a healthy instance. `compose.yaml`'s probe dials the loopback
  interface and *says* it is `$AGENTCAD_PUBLIC_ORIGIN`;
  `test_the_healthcheck_sends_the_configured_host_header` pins all three
  parts. The same trap catches any `curl 127.0.0.1` you write against a hosted
  instance.

- **`X-Agent-Id` is not an identity in hosted mode**; at most it contributes a
  `<device>` suffix under the authenticated principal. `DEVICE_RE` allows at
  most one colon, bans the `user:`/`agent:` prefixes and caps at 24 chars —
  `user:` + 32 + `/` + 24 = 62 ≤ `locks.MAX_CLIENT_ID_CHARS` (which **refuses**
  rather than truncates). Without the prefix ban, `X-Agent-Id: user:anya`
  composed to `user:nikita/user:anya`: not an impersonation, but an identity
  that reads as two people everywhere it is rendered.

- **An invalid bearer never falls back to a valid cookie** on the same
  request. A revoked token quietly becoming a session is a confused deputy.
  `resolve_principal` never raises either: a store that cannot be read yields
  no principal, which fails closed.

- **A registry-level tool refusal has no seam, and one gap is left open on
  purpose.** FR19 disables `POST /api/projects/open` and the absolute-path
  form of `import_cad_file` in hosted mode. The registry-level `open_project`
  **tool** is *not* refused: it lives in `core/tools.py`, which this feature's
  constraints forbid editing, and there is no unregister seam. It is reachable
  only by an authenticated member — who can already run arbitrary Python by
  writing a part script — so it adds nothing to the threat model, but it is a
  real gap in FR19's letter. Closing it means either a tool-level mode guard
  in `core/tools.py` or an unregister seam on `ToolRegistry`; both are core
  edits and neither belongs in a follow-up commit made in a hurry.

- **A tool refusal is a 200 with an `{"error": …}` payload, not a 403.**
  `ToolRegistry.call` converts every `AppError` that way *by design* so agents
  can read and react. `import_cad_file`'s hosted refusal is therefore pinned as
  `authz_error` in the payload plus "nothing was ingested" — asserting a status
  code there would be asserting against the house contract.

- **The CSRF Origin rule is applied to anonymous state-changing routes too**,
  not only authenticated ones. A cross-site `POST /api/auth/login` that signs a
  victim into the *attacker's* account is a real if quiet attack. Bearer
  requests are exempt (a browser cannot attach one cross-site).

- **Admin HTTP routes require a signed-in *person*** (`kind == "user"`), so an
  `admin`-role bearer token is `403`: a credential must not mint another
  credential while there is no audit log. And `POST /api/auth/logout` with no
  session is **401**, not 200 — logout is not on the nine-entry allowlist, and
  widening the allowlist to make that neat would be the wrong fix.

- **`agentcad admin …` builds `AuthStore(state_dir()/"auth")` directly** — no
  service, no kernel, no port — which is what makes it cheap over
  `docker compose exec` and what lets it work while the server is down. Keep
  it that way.

- **The image copies `/app` wholesale on purpose.** `_resources.resource_root()`
  is the *parent* of the `agentcad` package, so `frontend/`, `examples/` and
  `catalog/` must sit beside it. A `pip install agentcad` into `site-packages`
  serves a 404 for the UI and silently loses the bundled catalog. The runtime
  image also installs **`git`**, which `core/history.py` shells out to and
  which no CI step ever had to install because runners ship it.

- **The compose smoke job is not on `pull_request`.** The OCCT wheels make the
  image multi-GB; PRs get the seconds-long `docker compose config --quiet`
  lint instead, and `deploy-smoke.yml` runs on main, weekly and on demand —
  the same split `ci.yml` and `geometry-ci.yml` already make.

## Share/customizer gotchas (PRD-007 — read before touching `core/publications.py`, `core/share_build.py`, `server/routes_share*.py` or the share frontend)

- **The customizer rebuild is a `GET`, not a `POST`, and that is load-bearing.**
  `GET /s/<token>/variant?<params>` is a *pure read* of a content-addressed
  artifact — the owner's state never changes — so CSRF is moot, `SameSite`/Origin
  never applies, cross-origin embedding works by construction, and the response
  is CDN-cacheable. The **only** `security.py` change PRD-007 makes is the two
  public prefixes; no Origin-check exemption exists, and none may be added. Do
  not "fix" it to a POST.

- **`/s/` and `/embed/` MUST carry the trailing slash** in `PUBLIC_PREFIXES`
  (`is_public` is `startswith`): `/s` would make `/status` public and `/embed`
  would make `/embedding` public. The negation params in
  `test_hosted_surface.py::test_paths_that_must_not_be_public` (`/s`, `/status`,
  `/svg`, `/embed`, `/embedding`) are the guard against exactly that.

- **The pin is a COPY, not a reference.** At publish the ref is resolved to an
  immutable commit and the part's script bytes are read with `cat-file blob`
  (no worktree) into `<state-dir>/publications/scripts/<sha>.py`; a later
  `write_script` on the owner's working part changes neither the stored commit
  nor the copied bytes, so a live link never drifts (AC8). The record stores
  `ref.commit` + `script_sha`.

- **The visitor path never touches a user `ProjectStore`.** Variants build in a
  single **muzzled** `AgentCADService` rooted at `.../publications/build/` — the
  PRD-004 `_ephemeral_service` recipe (`bus.on_publish=None`, then AFTER
  `build_registry`: `store.branch_resolver=None`, `store.write_guard=None`). The
  build is `_build_with(record, affinity="share:<pub>", status_key=None)`; the
  owner-tree-byte-unchanged AC (AC5/AC8) is a property of this construction.
  `ensure_share` is called only from a route pack, **never** from
  `AgentCADService.__init__`, so PRD-004/011 ephemeral services stay unaffected.

- **The viewer is kernel-free; exactly two routes reach `exec()`.** `/model`,
  `/mesh/{key}`, `/params`, `/script` (and the shells) are file reads of
  sidecars the pin wrote — `/mesh/{key}` is **404-if-absent and never builds**
  (the `get_mesh_by_key` discipline). Only `/variant` and `/download` build, and
  the surface-equality test enumerates all eight `/s/`+`/embed/` templates so a
  ninth cannot go public unreviewed. **`NOT_YET_BUILT` must stay `== set()`**
  (`test_prd005a_acceptance.py` hard-asserts it) — grow `EXPECTED_PUBLIC` and
  mount a route in the SAME change, never stage a route as "planned".

- **The customizer's containment, all of it:** param parity is the authoring
  path's own `service.normalize_params` (reject unknown/out-of-type/out-of-enum
  BEFORE build); the **range clamp is the shared pure `kernel/paramclamp.py`**
  helper, called BOTH by `worker._resolve_numeric` (inside the build) AND by
  `share_build._clamp_params` **server-side before the variant cache key** — so
  two out-of-range values that clamp to the same geometry coalesce to one key
  and one build (PRD-007 review M-2), and NaN is refused (not a degenerate 200,
  finding m-2); never re-implemented. Then a per-link AND per-IP `TokenBucket`
  (from `core/ratelimit.py`); a **global** `BoundedSemaphore` whose effective
  size is `min(AGENTCAD_SHARE_MAX_INFLIGHT, pool_size - 1)` — the `pool_size-1`
  **worker reservation** keeps a member's worker free (the real containment wall;
  `affinity="share:<pub>"` is consistent-hash cache-warmth routing, **NOT**
  segregation), and on a single-worker pool the cap is 0 so `/variant` and
  `/download` refuse with a `503 ServiceUnavailableError` naming
  `AGENTCAD_KERNEL_POOL_SIZE` rather than starving members (`require_customizer_capacity`);
  acquired non-blocking; and the content-addressed variant cache (a repeat is a
  disk read, zero kernel). The **per-IP key is `request.client.host`** (the
  proxy-resolved address), NEVER a hand-parsed `X-Forwarded-For` — a visitor
  could forge that header and mint a fresh bucket per request (the PRD-005a M3
  lesson, kept).

- **`TokenBucket` lives in `core/ratelimit.py` now**, re-exported from
  `presence.py` for PRD-008; `presence.TokenBucket is ratelimit.TokenBucket`.
  Import it from `ratelimit`, never re-implement.

- **Four senses of "token", named to stay apart:** the share capability is a
  `share_token` in code (`shr_<pub_id8>_<secret43>`, stored as a `sha256`
  digest, `split("_", 2)` because the secret's alphabet includes `_`); the
  management handle is `pub_id`; the rate-limit tokens are `bucket`; and PRD-005a
  already has the agent bearer and the enrolment token. A publication's
  `part|project` reach is `share_scope`, never the package-index `scope` or
  `locks.write_scope`.

- **No `core/gltf.py`.** The viewer streams the shipped OCP-free ACM
  (`viewport.js::parseACM`); the glTF exporter is PRD-017's, deferred to avoid
  build-then-migrate churn. **Only `agentcad/kernel/` imports OCP/build123d** —
  nothing in the share path does.

## Marketplace gotchas (PRD-031a — read before touching `server/routes_market.py`, the public catalog read, `core/tools_market.py` or the market frontend)

- **One shared kernel path, never a second wall.** The market listing customizer
  is PRD-007's containment *scoped to a catalog `content_id`*, not a new one. It
  reaches `exec()` only through `ShareBuilder.build_catalog_variant` →
  `export_catalog_variant`, which run the **same** `_variant`/`_export` tail as
  the `/s/` path: `require_customizer_capacity` (the `pool_size-1` reservation,
  503 on a single-worker pool), the process-global in-flight `BoundedSemaphore`,
  `normalize_params` parity, the `paramclamp` clamp *before* the cache key, and
  the content-addressed variant cache. Do **not** add a second set of limits.
- **`service.customizer_guard` is one object, shared.** The per-IP `TokenBucket`
  + hourly login gate live in one `CustomizerGuard` installed once by
  `ensure_share`; both `routes_share_public` (`/s/`) and `routes_market`
  (`/market`) read it, so a visitor cannot double their per-address allowance
  across the two anonymous kernel paths. The per-*listing* / per-*link* bucket
  stays route-local (keyed `catalog:<name>@<version>/<part>` vs `share:<pub_id>`)
  — that one shapes a single subject and has no double-allowance to close. AC4
  asserts the shared identity.
- **`scope: public` on every route, dual filter.** The market reuses
  `routes_public._public_indexes` (both `configured_scope == "public"` AND the
  document's `scope == "public"` — the M2 lesson) and `_find`/`_miss`. A private
  or nonexistent listing (search, detail, script, params, variant, download,
  mesh) is one name-free 404 — byte-identical, no existence oracle. `market_install`
  applies the same dual filter, so it can only pin the seeded public catalog.
- **The digest is the param spec — browse stays zero-kernel.** The customizer's
  typed spec comes from the pre-generated `index.json` `parts[part].params`, not
  a kernel `inspect`, so `/search`, `/script`, `/params`, `/preview` and the mesh
  read reach **zero kernel**; a variant is the ONE kernel call. Proven with the
  `kernel_counter` fixture and a **positive control** that does build.
- **The mesh route is kernel-free and NEVER builds.** `.../parts/{part}/mesh/{key}`
  serves a `.acm` *already in the build cache* (via `mesh_path`, keyed by the
  pinned `script_sha` computed with `share_build.script_sha_for` — no build
  registered) and 404s an absent one; `key` is hex-gated (`_is_cache_key`)
  against traversal. It lives in `routes_market.py` beside `/variant` (the exact
  `/s/{token}/mesh/{key}` layout), is **not** guarded/throttled, and is what the
  browser viewport fetches after a `/variant` returns a `mesh_key`.
- **Fixed export set `{step, stl, 3mf}`.** A catalog listing has no owner to carry
  a per-link mask, so every listing offers `ALLOWED_EXPORTS = EXPORT_FORMATS`; a
  format outside it 404s **before** the builder (and before the listing resolves).
- **`market_install` (`core/tools_market.py`, load order `mar` < `pac`).** Read
  `service.packages` **inside** the function, never in `register` — `tools_packages`
  installs it later. It is `add_package(index=<public catalog>)` + `use_part`,
  seeded-catalog-scoped; the PRD-011 lockfile pins `version`+`content_id`. The
  browser "Add to library" reuses the existing authenticated package routes — no
  new route, not on the anonymous surface. There is **no** new `market_search`
  tool — anonymous callers use `GET /api/public/packages/search`, agents keep
  `search_packages`.
- **`search`-before-`{name}` route order.** `GET /api/public/packages/search`
  MUST be declared before `/{name}` or Starlette binds `{name} == "search"`. The
  public search passes `refresh=False` (no network on the anonymous path — M2)
  and the dual-scope index list.
- **`routes_public.py` stays zero-kernel; `routes_market.py` is the K pack.** The
  three kernel-free data routes are added into `routes_public.py` (keeping its
  invariant literally true); the two kernel routes + the kernel-free mesh read
  live in the separate, separately-reviewable `routes_market.py`. Only
  `agentcad/kernel/` imports OCP/build123d — the market modules are OCP-free
  (asserted in a fresh interpreter with OCP blocked).

## Assembly-v2 gotchas (PRD-013 — read before touching `core/mates.py` expansion, `kernel/handlers/connectors.py`, `handlers/simplify.py`, `core/urdf.py`, `tools_structure.py`/`tools_urdf.py`, `routes_structure.py` or the assembly frontend)

- **ONE expansion point, and expansion REPLACES the base.** Patterns +
  sub-assemblies are flattened in exactly one place — `mates.expand` /
  `mates.resolve_project`, reached through the `service._resolved_instances`
  wrapper `tools_structure` installs. Every consumer (mass rollup, interference,
  export, stackup, specs, checks, the packet) reads that one flat list, so a
  pattern of `count` members shows up as N **everywhere** from a single edit.
  A patterned base id is **absent** from the flat list — `b` becomes
  `b[0..count-1]` and is never counted alongside them. Do not add a second
  place that re-expands; that is the double-/under-count trap (`test_structure_patterns`).
- **Sub-assembly resolution is READ-ONLY on the source, structurally — not by a
  runtime check.** `write_guard` fires only inside `write_script` /
  `save_manifest` / `imports_dir(write=True)`, keyed by the *mutated* project.
  Resolution opens a source with `store.open` (no write hook) and touches only
  read accessors, so the source is never the guarded argument — the guard is
  *unreachable*, not merely un-triggered. The one write that can happen is a
  derived, content-addressed `.cache/<key>.acm` (identical to any read-triggered
  build), never authored state. A store-spy test asserts zero
  `save_manifest`/`write_script` against a source; install a raising guard on the
  sources and resolution still succeeds (`test_structure_subassembly`).
- **DOFs CLAMP, they do not raise** (the divergence from pre-013 behaviour).
  An out-of-range slider/planar/revolute DOF is clamped to the connector's
  declared range and a `dof_clamped {instance, dof, requested, clamped}` warning
  is recorded in the resolved assembly's `warnings` — never an exception. AC4:
  80 mm on a `(0,50)` slider → 50 + warning. Existing mate tests drive in-range
  values, so they are unaffected; a test that asserted a *raise* would need
  reconciling.
- **Interface: only exported connectors are matable from outside.** A source
  declares `assembly.interface {name → {instance, connector}}` via
  `set_assembly_interface`; a parent mates the sub-assembly by naming an
  interface NAME. A non-exported name is a `ValidationError` with
  `details.interface`; a cross-project cycle is a `ValidationError` with
  `details.cycle` (`["A","B","A"]`). The interface member must be a plain,
  un-patterned part in the MVP (a patterned/sub-assembly interface member raises
  a clear Phase-2 error); the interface-mate GEOMETRY resolves through the source
  and places the whole unit at the mated pose (`handlers/connectors.mate_subassembly`).
- **The inertia-frame trap (URDF correctness).** OCCT's `matrix_of_inertia` is
  expressed about the **centre of mass**, not the origin — so the analysis
  handler's old "about the global origin" note was FALSE for off-origin parts.
  `handlers/analysis._inertia` now parallel-axis-shifts the tensor **forward to
  the origin** (making the documented contract true), and `core/urdf` shifts it
  **back to each link's COM** via `I_com = I_origin − m(‖c‖²E − c cᵀ)` — the
  round trip URDF `<inertial>` requires. Skip either half and an off-origin
  link's inertia is a positive-but-WRONG tensor whose eigenvalues about the COM
  go negative — caught by `validate_urdf`'s SPD check (`test_urdf`,
  negation-tested).
- **`simplified_rep` is DISPLAY-ONLY.** The convex-hull proxy tier
  (`handlers/simplify`, `<key>.simplified.acm`, produced lazily on a
  `?lod=simplified` miss by the `tools_structure` `mesh_info` wrapper) is never a
  metrics input — mass and interference always measure the real B-rep. It is a
  distinct build kind, NOT a coarser LOD tolerance, and rides the existing tier
  probe/serve path (no `service.py`/`worker.py` edit).
- **Load order + naming.** `tools_structure`/`tools_urdf` sort **before**
  `tools_versioning`, so read any cross-pack seam (`service.branches`,
  `store.write_guard`) **lazily in methods**, never in `register()`; neither pack
  adds a merge gate. The route pack is `routes_structure.py` (the name
  `routes_assembly2` is taken by the single-instance PATCH); it is a route pack,
  no `app.py` edit. Rotations stay intrinsic-XYZ Euler everywhere — expansion
  composes transforms in the kernel via build123d `Location`, never a second
  server-side Euler.
- **Phase 2 (schema/seams reserved, NOT built):** `assembly.couplings` (schema +
  merge land now; resolution + URDF `<mimic>` are Phase 2 — no
  `set_coupling`/`clear_coupling` tool), `explode_assembly` (the frontend slider
  is a disabled stub), ball/gear joints, the interference broad-phase, and
  cylindrical/planar-decomposed URDF. Do not claim these green.

## Sandboxing & quotas gotchas (PRD-006 — read before touching `kernel/sandbox*.py`, `_confine.py`, `_preamble.py`, `_meter.py`, `quotas.py` or the client's request loop)

Every item is traceable to a decision in
`docs/superpowers/specs/2026-08-18-sandboxing-quotas-design.md`, to a spike
measurement quoted there, or to a test. Changelogs `0230`–`0237` (numbered `0213`–`0220` on the branch; renumbered at merge because PRD-007 took `0213`–`0220` and PRD-031a `0221`–`0229`).
Operator-facing reference: `docs/deployment.md`, "Confinement and quotas".

- **No `preexec_fn`, anywhere.** CPython documents it as unsafe in a threaded
  parent and the server *is* threaded. Rlimits are applied by the worker to
  itself in `_preamble.apply_from_env()`; cgroup placement is the parent
  writing `proc.pid` into `cgroup.procs` **after** `Popen`. If you find
  yourself reaching for it, you want the preamble or `backend.attach`.

- **Never grant bare `/tmp` or `tempfile.gettempdir()`.** Linux `/tmp` is
  shared, so a wholesale grant lets one worker's script read and overwrite a
  sibling's scratch — the leak the **private per-worker** `mkdtemp(prefix=
  "agentcad-worker-")` closes. `TMPDIR`/`TEMP`/`TMP`/`XDG_CACHE_HOME`/`HOME`
  all point at it. What `agentcad check` and the package gate materialize
  their cells under is the *server's* one `agentcad-work-*` root
  (`cli._build_service` → `service.work_root` → `checks.default_work_root`),
  which is granted by name. Two different directories; do not merge them.

- **`plan()` must NOT create the roots it is handed.** It also receives
  caller-supplied `--work-dir` paths whose acceptance is decided elsewhere, and
  creating them there resurrects `test_a_refused_work_dir_is_never_created`
  ("a refused path leaves nothing behind"). Creation belongs to whoever *owns*
  the directory: `cli._writable_roots` makes the projects dir, and
  `cli._accept_work_dir` makes an accepted `--work-dir` — accept first
  (`checks.refuse_work_dir_overlap` / `gate.refuse_work_dir_overlap`, hoisted
  to module level for exactly this), create second, **both before
  `_build_service`**. Why it matters at all: on Linux a Landlock rule on a
  missing path is ENOENT, and the grant is lost with it, so every part then
  fails with a `PermissionError` instead of producing a verdict. Pinned in
  `tests/test_sandbox_plan.py` and `tests/test_checks_cli.py`.

- **`~/.agentcad` is NOT a writable root — wholesale.** Nothing in
  `agentcad/kernel/` or `agentcad/toolkit/` reads or writes the config dir,
  every `load_config()` caller is server-side, and the worker's `HOME` is its
  private temp dir — so granting it wholesale bought nothing and cost the
  sentence the docs most want to be able to say: a part script can write
  **nothing under the server user's home**. It also carries the index
  definitions and the quota knobs, so a script that could rewrite it could
  raise its own caps. The one state-dir subtree a worker MAY write is
  `<state-dir>/publications/build` — PRD-007's shared-pool variant builds
  (`core/share_build.py`'s `self._store.build_root()`, which is exactly
  `appmode.state_dir() / "publications" / "build"`) go through the SAME
  confined kernel pool, so `cli._writable_roots` carves out that one subtree
  by name (merged from main, PRD-007 × PRD-006). Never grant the state dir
  itself — `secret.key` and `auth/` are siblings of `publications/`, not
  beneath it, and must stay ungranted and off the hosted read allow-list.

- **A hosted `agentcad serve` refuses to start when `AGENTCAD_STATE_DIR` lies
  inside a kernel-writable root** (`cli._refuse_state_dir_in_a_write_root`,
  exit 2 naming both paths). The hosted read allow-list is the read roots
  **plus the write roots**, so a state dir inside one is readable *and*
  writable however narrow the allow-list is, and whoever reads `secret.key`
  forges any session. Fatal rather than a warning — unlike
  `_warn_if_unconfined`, which reports a platform that cannot confine, this is
  one misplaced path with an exact remedy. `_build_service` records
  `service.writable_roots` for it; nothing else reads that.

- **The `seccomp(2)` operation constant is `1`** (`SECCOMP_SET_MODE_FILTER`).
  `2` is `SECCOMP_GET_ACTION_AVAIL` and answers `EOPNOTSUPP`; the spike lost
  time to exactly that. The `prctl(PR_SET_SECCOMP, 2, …)` fallback is a
  different `2` (that one *is* `SECCOMP_MODE_FILTER`) and covers the calling
  thread only.

- **A signal's pid argument is tested on its LOW word, unsigned
  (`JGE 0x80000000`).** The high word of an `int` argument is unspecified on
  both arches: on arm64 `mov w0, #-1` zeroes the top half, so a negative
  `pid_t` arrives zero-extended and a high-word test never fires —
  `os.kill(-1, SIGKILL)` escaped the filter in the shipped image (measured:
  `ESRCH`, not `EPERM`). The low word is what the kernel truncates the
  argument to. `tests/test_confine_unit.py` pins both encodings by
  **interpreting the BPF program's bytes** on every OS, because a wrong jump
  offset is a silently permissive sandbox and a macOS box cannot install the
  filter to find out.

- **The Landlock handled-access mask comes from the probed ABI, never a
  constant** — a bit the running kernel does not know makes `create_ruleset`
  EINVAL and takes the whole ruleset with it. And **`LANDLOCK_ACCESS_FS_TRUNCATE`
  (bit 14, ABI 3) must be in every write root's grant**: `open(path, "w")`
  sets `O_TRUNC`, so a write root without it is EACCES on every truncating
  open. That is why `LANDLOCK_MIN_ABI = 3` and why below it the preamble
  applies no ruleset and reports `off` rather than shipping false denials.
  `/dev/null` and `/proc/self/clear_refs` are **file** rules with `FS_FILE`
  only — a directory right on a file rule is EINVAL.

- **`clear_refs` is what makes `peak_rss_mb` a per-request number on Linux**
  (`_meter` writes `5` to `/proc/self/clear_refs`, which resets `VmHWM` *and*
  `ru_maxrss`). It needs its own Landlock file rule; without it the peak
  silently falls back to a lifetime high-water mark. Elsewhere it *is* the
  lifetime mark and `peak_rss_is_lifetime: true` says so — read the flag.

- **`ru_maxrss` is bytes on macOS and KiB on Linux.** Branch on the platform;
  `tests/test_meter.py` asserts both.

- **Windows: the venv `python.exe` is a launcher; the supervisor samples the
  job's processes, not the `Popen` handle.** A venv `python.exe` (uv-managed
  ones included) starts the real interpreter as a **child** and stays behind as
  a stub, so `GetProcessMemoryInfo(proc._handle)` measured 3.9 MB for a worker
  with build123d imported (Windows CI, changelog 0238) while the quota tier
  worked perfectly — the child *inherits* the job object, which is why the
  commit limit still produced its `MemoryError`. `WindowsBackend.rss_bytes`
  therefore walks `QueryInformationJobObject(JobObjectBasicProcessIdList)` and
  reports the **largest** working set in the job: the max, never the sum, since
  the launcher and the interpreter share their mapped pages. The `Popen` handle
  is only the fallback (no job, or a refused query). Every Win32 entry point
  stays a module-level seam (`_job_process_ids`, `_open_process`,
  `_memory_counters`, `_close_handle`) so `tests/test_sandbox_plan.py` can
  drive the whole sampler from a macOS box — `tests/test_sandbox_windows.py`
  now asserts ≥ 100 MB so a stub-only sample fails loudly instead of passing a
  sanity bound.

- **Windows confinement is an AppContainer, and five things about it are not
  guessable** (PRD-006b, `kernel/sandbox_windows.py`, changelog 0242):
  1. **The client spawns through the backend.** `subprocess` can pass a handle
     list and nothing else, so a lowbox token needs `CreateProcessW` +
     `STARTUPINFOEX` (`PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`).
     `Backend.spawn(argv, env)` returns a `ConfinedProcess` with exactly the
     `Popen` surface the client uses, or `None` everywhere else —
     `client._ensure_started` is `backend.spawn(...) or subprocess.Popen(...)`.
     If you touch that object, it must keep `stdin/stdout/stderr`, `pid`,
     `_handle`, `poll()`, `wait(timeout)` **raising `subprocess.TimeoutExpired`**,
     `kill()`, `returncode` and `close()`.
  2. **The start is suspended.** `CREATE_SUSPENDED` →
     `AssignProcessToJobObject` → `ResumeThread`, so no instruction runs
     outside the job; `attach()` is a no-op for a process the backend spawned
     (it checks `proc.job_assigned`) but still sets `attached`, which is what
     the psapi sampler reads.
  3. **`%TEMP%` is rewritten by the token** to
     `%LOCALAPPDATA%\Packages\<profile name>\AC\Temp`. The plan points
     `LOCALAPPDATA`/`APPDATA`/`USERPROFILE` at the private temp dir, and
     `prepare_tmp_hook` **creates that tree** before every spawn — without it
     the first `tempfile` call in the container raises `FileNotFoundError`
     (probe round 1).
  4. **`icacls` grants take `*<SID>`, not a name**
     (`icacls <path> /grant "*S-1-15-2-…:(OI)(CI)M"`), the `(OI)(CI)` ACE
     reaches **pre-existing** children so no `/T` is needed (measured), and
     `icacls` can exit **0** while printing `Failed processing 1 files` — read
     the summary line, not just the exit code.
  5. **The profile name is salted** (`<state-dir>/appcontainer.salt`), because
     `DeriveAppContainerSidFromAppContainerName` is a *hash of the name*: an
     unsalted name is a SID any other local account could derive and then own,
     and our ACEs are permanent and inheritable. The salt never leaves the
     server — the worker is told the SID, never the name.
  Honesty runs through all of it: the parent only ever *intends*, the worker
  reports `TokenIsAppContainer` off its own token in `_preamble`, and
  `confinement_holds`/`sandbox.report` require the flag **and** a matching SID
  before health may say `active`.

- **`RLIMIT_NPROC` counts tasks (threads), per uid, not processes.** A warm
  worker runs 15–22 threads, so `live_uid_process_count()` sums `Threads:`
  from `/proc/*/status`; a per-process count under-measured a multi-worker
  pool by ~20 tasks per live worker and killed the second module-scoped worker
  inside `import build123d` with a `pthread_create` EAGAIN.

- **The fork budget is `live task count (measured at EVERY spawn) +
  pids_headroom × pool_size`.** Both halves are load-bearing, because the limit
  is per-*uid* but the kernel checks it against the **calling** process's own
  ceiling. Computed once in `KernelClient.__init__` and handed identically to
  three pool slots, the third worker forked into a budget its siblings had
  already spent and died inside `import build123d` (verified). So:
  `sandbox.plan(..., pool_size=)` (`KernelPool` passes its `size`), and
  `client._ensure_started` merges **`plan.spawn_env()`**, not `plan.env` —
  `plan.env` stays the construction-time snapshot that health and the tests
  read, while `Backend.refresh()` re-measures the rlimit payload at every spawn
  and every respawn. What the cap bounds is at most `headroom × pool_size`
  *extra* tasks across the pool; the hard per-worker process cap is `pids`,
  and that one is the cgroup's. macOS runs the same formula (its count is
  processes, which is the right measure there).

- **`AGENTCAD_NO_SANDBOX=1` (and `{"sandbox": false}`) opts out of
  confinement, not of the caps.** The argv is unwrapped, the preamble applies
  no ruleset, confinement reports `off` *with the reason* — and the quotas and
  rlimits still apply. A runaway script may not take the machine down whether
  or not the operator trusts it with the filesystem.

- **`AGENTCAD_CGROUP_DIR` is opt-in twice over.** Unset ⇒ nothing is probed at
  all (the shipped container and every laptop). A path ⇒ the operator
  delegated a subtree (Model 2). `auto` ⇒ the own-cgroup/systemd
  `Delegate=yes` route, which **refuses root** (`os.access` answers `W_OK` for
  uid 0 almost everywhere, so a root server would "discover" a subtree on any
  machine — that is activation by capability), refuses a subtree it does not
  own, and refuses the root cgroup. `off` ⇒ not even `auto`. Every failure of
  a probe that was *asked for* reaches `plan.warnings`; never fall back
  silently. `memory.swap.max=0` is load-bearing beside every `memory.max` —
  with swap at `max` a 400 MB allocation under a 200 MB cap swapped instead of
  dying.

- **With a cgroup in force the supervisor can never fire** (the kernel kills at
  the charge, so sampled RSS never crosses the cap), which is why
  `tests/test_supervisor.py` sets `AGENTCAD_CGROUP_DIR=off` and
  `address_space_mb="off"` to pin the tier it is testing. Three tiers share one
  `memory_mb` knob; a test that does not pin the tier tests whichever one the
  host happens to have.

- **`KernelClient()` with no `writable_dirs` and no `quotas` is the historical
  client** — no plan, no private temp dir, no supervisor, historical argv and
  a 0.5 s poll. The session-scoped `kernel` fixture depends on that, and
  `sandbox.report()` reads everything through `getattr` so a plan-free client
  answers the health object instead of raising. Do not "simplify" the branch
  away.

- **Confinement status is measured, never inferred.** `active` comes only from
  the worker's own `ping` report; a plan that *intended* to confine and whose
  preamble failed is `off` with the failure in `warnings`. Conversely only a
  `landlock`/`seccomp` stage failure clears the claim (`client.
  confinement_holds`) — a refused *rlimit* is a quota that did not apply, and
  saying `off` for it understates the confinement as badly as claiming
  `active` on intent overstates it. A **lost root grant** is the same kind of
  thing and has its own stage, **`landlock_root`** (`_preamble._landlock`): the
  ruleset landed and the process is confined, one path out of it was not
  granted. Filing that under `landlock` made one missing directory a red
  ubuntu CI job under `AGENTCAD_EXPECT_SANDBOX=active`. It is still a failure —
  it is in `failures` and health shows it in `warnings` — and
  `CONFINEMENT_STAGES` stays `("landlock", "seccomp")`.

- **`sandbox.report()` never contradicts `client.sandboxed`,** and it drops a
  quota tier the worker did not apply. Two corollaries of the same honesty
  rule: the kernel's own flag wins wherever it is present (a worker that
  answered `ping` with no `sandbox` object left `live` empty, so the plan's
  `active` used to stand while `sandboxed` was already False), and an empty
  `rlimits` list removes `rlimit` from `quotas.mechanism` — `mechanism` is read
  as a promise.

- **`denials.classify` needs the traceback, and an unattributed EPERM is
  `None`.** The seccomp filter answers EPERM for a refused `kill` too, and
  labelling that `network` sends an agent to fix a socket that is not in the
  script. A `network` answer requires a frame naming `socket`/`urlopen`/
  `connect`/`getaddrinfo`.

- **What may be classified is per FACET, not one boolean** — `denials.
  active_facets(_preamble.REPORT)`: `filesystem` needs `landlock_abi`,
  `network` needs `seccomp`, `process_count`/`memory` need an applied rlimit or
  a parent-installed `quotas` tier. "Something is in force" is not evidence for
  all four: an `AGENTCAD_NO_SANDBOX` worker still gets its caps, and used to
  label an ordinary DAC `EACCES` a sandbox denial. The consequence to know
  (F1, shipped): **on macOS the seatbelt is applied to the argv by the
  PARENT**, before the worker ever runs, so `landlock_abi`/`seccomp` are
  always `None`/`None` there — but `sandbox_macos.build()` declares the two
  facets it genuinely enforces in `payload["confinement"]`, and
  `_preamble.apply_from_env` copies that claim through verbatim, so a real
  seatbelt `EACCES`/`EPERM` DOES carry `details.denied` (`tests/test_sandbox.py`
  proves it against the real seatbelt). **Linux does the opposite on purpose**
  (independent re-review, post-F1): `sandbox_linux.build()` does NOT declare
  `payload["confinement"]`, even though it could — the Linux worker applies
  Landlock/seccomp to itself and self-reports `landlock_abi`/`seccomp`
  directly, so a parent-declared claim there would be a second, unconditional
  one that survives a stage failing *inside* the worker after the parent
  already decided (at plan time) to request it, which is exactly the false
  positive M3 exists to close.

- **Every kill, timeout and crash reaches the `on_usage` hook** (`ok: False`,
  `cpu_ms: None`), not just answered requests. The requests that cost the most
  are exactly the ones that never answer; emitting only on the success path
  left `core/usage.py`'s `errors` at zero and a 60 s timeout out of the wall
  clock entirely.

- **`details.usage` is the kill paths' contract, not every error's.** A
  worker-reported `script_error` deliberately carries none: its usage travels
  on `last_usage` and through the `on_usage` hook. Copying a per-run `cpu_ms`
  into the error body breaks the invariant that both drawing routes render an
  **identical** error (`tests/test_configs_drawing.py`).

- **The Linux loop on a macOS box is `make test-linux`, and it COPIES the
  tree.** Docker Desktop's `fakeowner` virtiofs bind mounts are not
  Landlock-coherent — grants have no effect and even *reads* fail — so a
  `-v "$PWD":/app` run proves nothing. `scripts/linux-test.sh` copies into the
  image's overlayfs, shadows the baked-in package with `PYTHONPATH`, and sets
  `AGENTCAD_EXPECT_SANDBOX=active`. This is a dev-box artifact, not a
  deployment concern (overlayfs/ext4/tmpfs are correct).

- **`AGENTCAD_EXPECT_SANDBOX=active` is the honesty gate, and CI sets it.**
  Every containment test asserts *when the live status is active* and skips
  otherwise, so without the variable a silent degradation to `off` would be
  green. `ci.yml`'s matrix carries `expect_sandbox` — `active` on **all three**
  rows since PRD-006b gave Windows a real AppContainer, so a profile, an ACL or
  a lowbox spawn that quietly degraded to an unconfined worker is red — and
  `AGENTCAD_EXPECT_QUOTAS=active` on all three; the "Sandbox probe (Linux)"
  step prints `uname -r`, `/sys/kernel/security/lsm` and the ABI so a runner
  change is diagnosable in one red run. If you add a containment test, gate it
  the same way — never with a bare `skipif`.

- **`tests/test_prd006_acceptance.py` grades AC1–AC8, and one of them is an
  evidence check.** "Full suite green" is a claim about a *run*, so
  `test_ac8_the_full_suite_count_is_cited` reads the newest changelog entry and
  requires `make test` and a real `N passed` — the PRD-004/008/011/012
  precedent. Fill the number in; the literal placeholder is red on purpose.

## Conventions (match these)

- **Structured errors**: `{"error": {"type", "message", "details"}}`; script
  failures carry `details.traceback` and `details.line`, and `details.hint`
  from the Error Doctor. Mutating operations return post-state, never bare OK.
- **Atomic writes** (temp + `os.replace`) for every manifest/script/cache file.
- **Determinism**: same script + params ⇒ identical geometry and byte-identical
  meshes. Cache key = `sha256(content, params, density, tolerance)`. PRD-012
  added **nothing** to that payload: a configuration is config-aware because
  `_cache_key_for` hashes `record.effective_params`, so every pre-existing key
  still hits and `tests/test_solids.py`'s pinned bytes never move.
- **Security/trust**: in the default **local** mode the server binds
  `127.0.0.1` only, with a Host-allowlist + same-origin guard
  (`server/app._browser_request_allowed`) on HTTP and WS — and a non-loopback
  bind is *refused* in that mode. In **hosted** mode (`AGENTCAD_MODE=hosted`,
  PRD-005a) it may bind a public interface, and the loopback assumption is
  replaced by the default-deny request guard in `server/security.py`: every
  request is an authenticated principal or one of nine enumerated anonymous
  paths. Read the hosted-core gotchas before assuming either half.
  Part scripts run as the server user by design, and the confinement bounds
  what they may *reach*, never whose they are (an account on a hosted instance
  still reaches every project, which is why registration is closed). The
  worker is confined by a deny-by-default `sandbox-exec` profile on macOS, by
  an in-process Landlock + seccomp preamble on Linux, and by an **AppContainer**
  on Windows (writes only in project roots + a private temp dir, no network;
  `AGENTCAD_NO_SANDBOX=1` opts out of the confinement but **not** of the
  quotas). Per-worker memory/pids/CPU caps and a per-project disk budget
  ride the same seam — read the sandboxing gotchas. API key from env only,
  never persisted.
- Comment density and style: match the surrounding file. Comments state
  constraints the code can't, not narration.

## Testing (`make test`)

- xdist workers (`-n auto`, physical cores, capped at 8 — override via
  `PYTEST_PARALLEL`) run by `loadscope`: one scheduling unit per module, or
  per class where a module defines test classes. Each worker holds one
  **session-scoped `kernel` fixture** (`tests/conftest.py`) that amortizes
  the warm import. `tests/test_examples.py` is deliberately class-per-example
  with the engine sweep split into generated part-chunk classes, each
  per-part parametrized — 1-test classes would defeat xdist's pending≤2
  refill watermark and its count-descending queue reorder (see the module
  docstring) — so the examples spread across workers instead of pinning
  ~18 minutes to one.
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
- `docs/agent-api.md` — the 85/88 agent tools with schemas + a worked loop
- `docs/geometry-ci.md` — `agentcad check`, the report schema, the GitHub Action
- `docs/part-authoring.md` — the script contract, toolkit, mates, sketch solver
- `docs/user-guide.md` — the UI surface by surface
- `docs/roadmap.md` — the PRD index with statuses (what we're building and why)
- `docs/prd/` — one detailed PRD per roadmap feature (see `docs/prd/README.md`)
- `docs/market_research.md` — the competitive/market evidence behind the roadmap
- `docs/superpowers/specs|plans/` — the design specs and implementation plans
