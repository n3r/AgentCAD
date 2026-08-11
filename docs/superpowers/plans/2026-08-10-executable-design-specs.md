# Executable design specs — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship
[PRD-003](../../prd/in-progress/PRD-003-design-specs-executable.md) — design
intent as executable, versioned, kernel-evaluated assertions: a `SPECS` list in
part scripts and a root `specs.py`, evaluated on every rebuild and on demand,
grouped by requirement, cached under the existing content-hash discipline, and
enforced as a red gate on PRD-002 proposals — per
[the design spec](../specs/2026-08-10-executable-design-specs-design.md).

**Architecture (one paragraph):** declarations are pure-data dicts from
`agentcad/toolkit/specs.py` (zero kernel imports), living in the tree — part
scope inside `parts/<id>.py`, project scope in a root `specs.py` — so PRD-001
versions, branches, restores and merges them for free. A new worker pack
`agentcad/kernel/handlers/specs.py` contributes `spec_declare` (execute, read
`SPECS`, never build), `spec_eval` (build via `build_shape_ns`, evaluate the
metric/wall/predicate tier against the built shape) and `clearance` (the one new
geometry op, `BRepExtrema_DistShapeShape` over two world-placed items).
`agentcad/core/specs.py`'s `SpecRunner` orchestrates three tiers — shape,
assembly, expensive/optional — partitioning declared checks by what they need,
caching tier-1 results in a `.cache/<cache_key>.specs.json` sidecar beside the
existing `.metrics.json`, and short-circuiting to zero kernel work for a part
that declares nothing (an `ast.parse` presence scan). A tool pack and a route
pack expose `run_specs`/`list_specs`/`set_project_specs`/`get_project_specs`,
wrap `service._rebuild` and `service.get_part` to attach the summary, and append
a fail-closed `specs` provider to PRD-002's `service.gate_providers`. The UI is
one chip strip in the inspector's Parameters pane.

**Tech stack:** Python 3.12 / stdlib `ast` / FastAPI / pytest (session-scoped
`kernel` fixture); build123d and OCP only inside
`agentcad/kernel/handlers/specs.py`; vanilla ES-module frontend, no bundler.

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** In this plan that is
  exactly one new file, `agentcad/kernel/handlers/specs.py`.
  `agentcad/toolkit/specs.py` and `agentcad/core/specs.py` must import neither —
  the toolkit because FR4 requires a `check_fem_*` to *declare* on a machine
  with no extras, the core module because it is server-process code. Assert both
  with a test.
- **Do not edit `worker.py`, `tools.py`, `app.py` or `service.py`.** New
  capability arrives as a worker handler pack
  (`agentcad/kernel/handlers/specs.py`), a tool pack
  (`agentcad/core/tools_specs.py`) and a route pack
  (`agentcad/server/routes_specs.py`). The rebuild seam is installed by
  **wrapping** `service._rebuild` and `service.get_part` from the tool pack —
  the `tools_versioning.install_write_guard` / `tools_proposals` branch-delete
  precedent — never by editing `service.py`.
- **Do not edit `proposals.py`, `packet.py`, `merge.py`, `branches.py`,
  `manifest_merge.py` or `history.py`.** PRD-001 and PRD-002 are finished and
  reviewed; this feature *consumes* them. The gate is one callable appended to
  `service.gate_providers`.
- Exactly **two additive changes to existing non-test files** in the whole plan:
  `agentcad/toolkit/__init__.py` (one name in `__all__` and its lazy
  `__getattr__` branch) and `agentcad/core/tools_stackup.py` (its handler body
  lifted to a module-level `compute_stackup(service, project, axis,
  from_instance, to_instance) -> dict` that the tool then calls — a pure
  refactor with no behaviour change, pinned by `tests/test_stackup.py` passing
  unedited). `core/templates.py`'s `CHEATSHEET` and the docs change in Slice 7.
- Structured errors only: `NotFoundError`/`ValidationError`/`ConflictError`
  (→ 404/422/409). **No new error type** — a failing, skipped, errored or
  unevaluated check is *payload*, never an exception.
- Mutating operations return post-state, never a bare OK.
- Atomic writes (`ProjectStore._atomic_write`) for `specs.py` and for every
  `.cache/*.specs.json` / `*.projspecs.json` sidecar.
- **All 666 existing tests must keep passing, and exactly one line of exactly
  one existing test file may change.** (Baseline: `666 passed, 1 skipped`,
  `docs/changelog/0086-prd-002-completed.md`.) The one sanctioned edit is in
  Slice 5:
  `tests/test_proposals.py::test_specs_and_checks_are_skipped_with_no_providers`
  opens with `assert getattr(service, "gate_providers", None) in (None, [])`,
  which stops being true once this feature appends its provider; the test gains
  `service.gate_providers = []` before that assertion, which is what its own
  name says it is testing. **Every other diff to an existing test file is a
  design bug** — stop and re-read the design spec. Two suites are load-bearing
  and must be run explicitly in every slice: `tests/test_proposals.py` and
  `tests/test_examples.py` (whose per-param min/max sweep runs with rocketry
  specs declared from Slice 7 on).
- Tests: session-scoped `kernel` fixture. Git-touching tests carry
  `pytest.mark.integration`, `pytest.mark.portability` and
  `skipif(shutil.which("git") is None)`; example-driven or kernel-heavy ones add
  `pytest.mark.slow`. Nothing here is `exhaustive`. Use the real service
  (**not** `make_test_service`, which sets `bus.on_publish = None`) whenever
  history matters, and copy the autouse `_reset_context` fixture that rebinds
  `locks.client_id_var` and `pinned_tree_var`.
- `TestClient(app, base_url="http://127.0.0.1")` and
  `create_app(..., extra_allowed_hosts={"testserver"})` for every HTTP/WS test.
- **Examples run on a copy** —
  `shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".cache", "exports"))`,
  as in `tests/test_examples.py:44` and
  `tests/test_prd002_acceptance.py::_copy_rocketry`. Never mutate `examples/`
  in place *except* in Slice 7, which deliberately commits new spec declarations
  into `examples/rocketry` and must then re-run `tests/test_examples.py`.
- **Never `uv sync` / `uv pip install` into the shared venv** from a parallel
  agent — use a scratch venv (this matters here: the `[fem]` extra is optional
  and AC3 requires the suite to be green **without** it).
- **Subagents do not run `git`** (the coordinating session commits).
- **Every commit stages a changelog entry** `docs/changelog/NNNN-<slug>.md`
  written from the real diff, per `docs/changelog/README.md`. The highest
  existing entry when this plan was written is **0086**, so the slices below
  name 0087–0093 — **recompute `NNNN` at commit time**
  (`ls docs/changelog | tail`) because other work may have landed first.
- Commit messages end with `Co-Authored-By: Claude <noreply@anthropic.com>`.

---

## Slice map

| # | Slice | Lands | Changelog |
|---|---|---|---|
| 1 | Toolkit constructors — the declaration vocabulary | FR1, FR3, FR4 | `0087-design-spec-constructors.md` |
| 2 | Kernel pack: `spec_declare`, `spec_eval`, `clearance` | FR1, FR6, FR9 primitives + the one new geometry op | `0088-spec-kernel-handlers.md` |
| 3 | `SpecRunner`: tiers, caching, report, requirement grouping | FR5–FR10, FR12 | `0089-spec-runner.md` |
| 4 | Tool pack, route pack, rebuild seam, `specs.py` writer | FR2, FR5, FR7; the agent surface | `0090-spec-tools-and-rebuild-summary.md` |
| 5 | `evaluate_specs` + the fail-closed proposal gate | FR11; AC7 | `0091-spec-gate.md` |
| 6 | Frontend: spec chips in the inspector | FR13 (MVP half); AC8 | `0092-spec-chips-ui.md` |
| 7 | Rocketry specs, docs, acceptance tests, PRD close-out | AC1–AC9 | `0093-specs-docs-and-acceptance.md` |

Each slice is independently landable: 1 is a pure module nothing imports yet;
2 ships three standalone kernel primitives nobody calls; 3 is the orchestrator
with no surface; 4 makes the feature usable end to end; 5 makes it enforceable;
6 makes it visible; 7 is examples, docs and acceptance.

---

## Slice 1 — the declaration vocabulary (`agentcad/toolkit/specs.py`)

**Why first:** it is the contract every other slice is written against, it is
pure Python with no kernel and no I/O, and its eager validation is the mechanism
FR1 rests on.

**Files**
- Create: `agentcad/toolkit/specs.py`
- Modify: `agentcad/toolkit/__init__.py` (one `__all__` entry + one
  `__getattr__` branch — follow the existing lazy re-export shape)
- Create: `tests/test_specs_toolkit.py`

**Interfaces produced** (Slices 2–7 depend on these exact names and keys)

```python
SPEC_FORMAT = 1
PART_KINDS    = ("valid", "mass", "volume", "bbox", "wall", "that", "fem_static")
PROJECT_KINDS = ("interference_free", "clearance", "stackup")

def check_valid(*, name=None, requirement=None) -> dict
def check_mass(min_g=None, max_g=None, *, name=None, requirement=None) -> dict
def check_volume(min_mm3=None, max_mm3=None, *, name=None, requirement=None) -> dict
def check_bbox(within_mm, *, name=None, requirement=None) -> dict        # scalar or [x,y,z]
def check_wall(min_mm, *, grid=8, name=None, requirement=None) -> dict
def check_that(fn, name, *, requirement=None) -> dict                    # fn(part, metrics) -> bool
def check_fem_static(fixed_face, load_face, load_N, *, max_vm_mpa=None,
                     max_disp_mm=None, name=None, requirement=None) -> dict
def check_interference_free(min_volume_mm3=0.001, *, name=None, requirement=None) -> dict
def check_clearance(a, b, min_mm, *, name=None, requirement=None) -> dict
def check_stackup(from_instance, to_instance, axis, within, *,
                  name=None, requirement=None) -> dict

# every constructor returns
{"spec": SPEC_FORMAT, "kind": str, "scope": "part"|"project", "name": str,
 "limit": dict, "requirement": str | None, "options": dict}
# check_that additionally carries "fn": <callable>, stripped at the JSON boundary
```

- [ ] **Step 1: write the failing tests** — `tests/test_specs_toolkit.py`. Pure;
  no `kernel` fixture, no git, no marks. Cover:
  - every constructor returns the documented key set with `spec == 1`, the right
    `kind` and `scope`, a defaulted `name`, and `requirement` riding through
    verbatim (an id **and** a URL — the string is opaque and is never parsed);
  - **eager validation is FR1's mechanism**: `check_wall(min_mm="thick")`,
    `check_mass()` with neither bound, `check_mass(min_g=5, max_g=1)`,
    `check_bbox([1, 2])`, `check_that("not callable", name="x")`,
    `check_clearance("a", "a", min_mm=1)` and
    `check_stackup(..., axis="w", ...)` each raise at **construction**, with a
    message naming the argument;
  - `check_bbox` accepts a scalar and a 3-vector and normalizes both to
    `{"within_mm": [x, y, z]}`;
  - every declaration except `check_that` survives `json.dumps`; `check_that`'s
    dict holds a callable under `"fn"` and is otherwise identical;
  - `from agentcad.toolkit import specs` works, `"specs" in
    agentcad.toolkit.__all__`, and `agentcad.toolkit.specs` resolves through the
    package `__getattr__`;
  - **no kernel import**: `importlib.import_module("agentcad.toolkit.specs")` in
    a subprocess with `build123d` and `OCP` blocked from `sys.modules` succeeds
    (a `sys.meta_path` finder that raises for those two names is the cheapest
    honest form; asserting `"OCP" not in sys.modules` after the import is a
    weaker but acceptable fallback).
- [ ] **Step 2: run to verify failure** —
  `uv run pytest tests/test_specs_toolkit.py -q` → import error.
- [ ] **Step 3: implement `agentcad/toolkit/specs.py`.** Stdlib only. Module
  docstring states: declaration is data, evaluation is the kernel's job; a
  `check_fem_*` declares cleanly with no `[fem]` extra; constructors validate
  eagerly *so that a bad spec is a `script_error` with `details.line`, exactly
  like a bad `PARAMS`*. Private `_positive`, `_bounds`, `_vec3` validators.
  `"spec": 1` is the marker **and** the format version — a dict without it is
  not a spec.
- [ ] **Step 4: run** — `uv run pytest tests/test_specs_toolkit.py tests/test_toolkit.py -q`,
  then `uv run pytest -q -m "not slow"` to prove nothing else moved.
- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0087-design-spec-constructors.md` (recompute `NNNN`), written
  from `git diff`. **In the same commit, fix the stale status metadata**:
  `docs/roadmap.md`'s PRD-003 row still says `pending` and links
  `prd/pending/PRD-003-…` while the file lives in `docs/prd/in-progress/`, and
  the PRD's own `- **Status:** pending` line needs the same correction (the PRD
  README's rule is that location, row and `Status:` move together — the same
  fix-up PRD-002's Slice 1 made).

**Verification command:** `uv run pytest tests/test_specs_toolkit.py -q`

---

## Slice 2 — the kernel pack: `spec_declare`, `spec_eval`, `clearance`

**Why now:** three primitives with hard geometry and process gotchas, testable
on their own, that Slice 3 consumes. `clearance` is the only genuinely new
geometry in the feature — landing it separately means a `BRepExtrema` surprise
surfaces before the runner is built on top of it.

**Files**
- Create: `agentcad/kernel/handlers/specs.py`
- Create: `tests/test_specs_kernel.py`

**Interfaces produced**

```python
# kernel method "spec_declare"
params: {"script": str, "scope": "part"|"project"}
result: {"declared": [ <declaration, JSON-safe> ], "warnings": [str]}

# kernel method "spec_eval"
params: {"script": str, "params": {…}, "density_g_cm3": float,
         "densities": {…}|None, "indices": [int]|None}
result: {"checks": [ <check record> ], "declared": [ … ], "warnings": [str]}

# kernel method "clearance"
params: {"a": item, "b": item, "min_mm": float|None}
        # item = {name?, script, params?} | {name?, source: str},
        #        + position [x,y,z], rotation_deg [rx,ry,rz]
result: {"distance_mm": float, "point_a": [x,y,z], "point_b": [x,y,z],
         "ok": bool,                 # only when min_mm was given
         "skipped_mesh": ["a"|"b"]}  # present only when non-empty
```

### Task 1 — `spec_declare` and `spec_eval`

- [ ] **Step 1: failing tests** in `tests/test_specs_kernel.py` (session
  `kernel` fixture, `slow`):
  - `spec_declare` on a script whose `SPECS` holds one of every part-scope
    constructor returns them all, JSON-safe, with `check_that`'s callable
    replaced by `"predicate": true`; **and issues no build** — assert with the
    counting `kernel.request` monkeypatch, signature
    `(method, params, timeout_s=None, affinity=None)`, that `method == "build"`
    is never seen;
  - `spec_declare` on a `specs.py`-shaped module (`scope="project"`) returns the
    project-scope declarations, and a part-scope constructor found there (or
    vice versa) is reported in `warnings`, not raised;
  - a script with no `SPECS` returns `{"declared": [], …}`;
  - `SPECS = "hello"` and `SPECS = [{"not": "a spec"}]` are **structural**
    errors — `WorkerError(ERROR_CONTRACT, …)` naming
    `agentcad.toolkit.specs` — while `SPECS = [check_wall(min_mm="x")]` is a
    `script_error` carrying `details.line` (this is FR1's split; assert both);
  - `spec_eval` evaluates `check_valid`/`check_mass`/`check_volume`/`check_bbox`
    from metrics with `measured`, `limit`, `unit` and a message, pass and fail
    directions each;
  - `spec_eval`'s `check_wall` reports `measured` within 0.15 mm of a known
    2.5 mm shell and a **non-null `location`** (the `_min_wall` contract);
  - `check_that` receives `(part, metrics)`, a truthy return is `pass`, a falsy
    return is `fail`, a non-bool return is `error` naming the type, and a
    predicate that **raises** is `status: "error"` with `details.traceback` —
    and the worker is still alive afterwards (AC5);
  - `indices` selects a subset, in order; `indices: null` selects the shape
    tier and returns `fem_static` as
    `{"status": "skip", "reason": "deferred"}`;
  - two `spec_eval` calls for the same `(script, params)` do not rebuild — the
    worker's shape LRU is hit (assert by timing or by a second call on a
    deliberately slow script; the honest form is asserting the LRU key, so keep
    this test cheap).
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement the two handlers.** `register(toolbox)` destructuring
  only the keys used (`build_shape_ns`, `metrics`, `shape_volume`, `place`,
  `WorkerError`, `ERROR_SCRIPT`, `ERROR_CONTRACT`, `ERROR_KERNEL`) at the top —
  the `handlers/diff.py` shape. Read `SPECS` out of the namespace
  `build_shape_ns` returns (the `connectors(p, part)` / `analysis(p)`
  precedent). `_min_wall` is **imported from the sibling pack**
  (`from .analysis import _min_wall`) — never re-implemented. Every per-check
  evaluation is wrapped in its own `try/except Exception` producing an `error`
  record; **the handler as a whole must not raise for a bad check**, only for a
  structural/`SPECS`-level problem.
- [ ] **Step 4: run** — `uv run pytest tests/test_specs_kernel.py -q -k "declare or eval"`.

### Task 2 — `clearance` (the one new geometry op)

- [ ] **Step 1: failing tests** (session `kernel`, `slow`):
  - two 10 mm boxes at a known 3.0 mm world gap → `distance_mm` within `1e-6`
    of 3.0, with `point_a`/`point_b` on the facing faces;
  - rotation is honoured — the same pair with `rotation_deg` applied reports the
    rotated gap (intrinsic XYZ, via `toolbox["place"]`);
  - overlapping solids → `distance_mm == 0.0`;
  - `min_mm` given → `ok` is present and correct; omitted → `ok` absent;
  - an STL `source` on either side comes back in `skipped_mesh` with **no
    distance query attempted**, and the worker is still alive;
  - a script that defines `analysis(p)` is measured through the **envelope**,
    not `build(p)` — assert with an envelope deliberately larger than the real
    part, so the reported clearance is smaller (conservative in the safe
    direction);
  - a shape that cannot be built on one side raises a structured
    `WorkerError(ERROR_KERNEL, …, {"stage": "distance", …})`, not a crash or a
    hang;
  - **name-collision guard:** `spec_declare`, `spec_eval` and `clearance` are
    absent from `worker.HANDLERS` before the pack loads (a colliding pack name
    is dropped with only a stderr warning, so this is worth pinning).
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `clearance`.** Import
  `from OCP.BRepExtrema import BRepExtrema_DistShapeShape` at module top level
  (the `analysis.py` pattern). Resolve items with a private `_item_shape`
  mirroring `worker._item_shape`'s script/reference split **including the
  `analysis(p)` envelope**, place with `toolbox["place"]`, then
  `LoadS1`/`LoadS2`/`SetMultiThread(True)`/`Perform()`/`Value()` and
  `PointOnShape1(1)`/`PointOnShape2(1)`. Leave `SetDeflection` at the exact
  default — an approximate distance reported as a measurement would be
  dishonest. Comment the three rules: the envelope makes the measurement
  conservative; a mesh side is skipped **provisionally** (distance is not a
  boolean, but the exclusion rule stands until measured); zero distance is the
  interference case and this handler does not also try to be
  `check_interference_free`.
- [ ] **Step 4: run** — `uv run pytest tests/test_specs_kernel.py tests/test_kernel.py tests/test_analysis.py -q`.
- [ ] **Step 5: measure.** Time `clearance` on a rocketry instance pair and on an
  `examples/engine` pair (a scratch script is fine — do not add a slow test for
  this). Also try one STL side deliberately, out of the test suite, and record
  whether `BRepExtrema` survives it. **These three numbers go in the changelog**
  — the design spec asks for evidence, not a guess.
- [ ] **Step 6: changelog + commit** —
  `docs/changelog/0088-spec-kernel-handlers.md` (recompute `NNNN`), carrying the
  measurements from Step 5.

**Verification command:** `uv run pytest tests/test_specs_kernel.py -q` plus
`make test`.

---

## Slice 3 — `SpecRunner`: tiers, caching, report, requirement grouping

**Files**
- Create: `agentcad/core/specs.py`
- Modify: `agentcad/core/tools_stackup.py` (lift the handler body to a
  module-level `compute_stackup`; the tool calls it)
- Create: `tests/test_specs.py`

**Interfaces produced** (Slices 4–7 depend on these exact names)

```python
# agentcad/core/specs.py
SPEC_RESULT_VERSION = 1
GATE_BUDGET_S = 30.0

def declares_specs(script: str) -> bool          # pure, ast.parse, never exec

class SpecRunner:
    def __init__(self, service): ...             # takes service.branches LAZILY
    def declarations(self, proj, part_id=None) -> dict     # list_specs payload
    def tier1(self, proj, part_id, build_result) -> dict | None   # the rebuild summary
    def run(self, proj, part_id=None, ref=None) -> dict    # run_specs report
    def evaluate_specs(self, proj, ref=None) -> dict       # FR11 gate shape
    def gate_provider(self) -> Callable[[str, dict], dict] # Slice 5 uses it
    def specs_path(self, proj) -> Path                     # store.path_of(proj)/"specs.py"
    def write_project_specs(self, proj, script: str) -> dict
```

### Task 1 — the pure parts

- [ ] **Step 1: failing tests** in `tests/test_specs.py` — plain strings and
  dicts, no kernel, no git:
  - `declares_specs`: true for `SPECS = [...]`, for `SPECS: list = [...]`, for a
    conditional/loop-built `SPECS`; false for `SPECS` only in a comment, only in
    a docstring, only as a local inside a function, and for a script that does
    not parse; memoized by `sha256(script)`;
  - requirement grouping: a requirement's status is `fail` when any of its
    checks failed **or errored**, `pass` when at least one passed and none
    failed, `skip` when all skipped; a requirement with zero checks is absent
    (FR12);
  - id assignment: `<part>:<name>` and `project:<name>`; a duplicate name inside
    one scope gets `#2` and a `warnings` entry;
  - `compute_stackup` returns exactly what `tolerance_stackup` returned before
    the refactor (`tests/test_stackup.py` passing unedited is the real
    assertion; add one direct call for the new entry point).
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** `declares_specs`, the grouping/id helpers and the
  `compute_stackup` extraction. Stdlib only; no imports from `service`,
  `proposals`, `packet` or the kernel.
- [ ] **Step 4: run** — `uv run pytest tests/test_specs.py -q -k "not slow"` and
  `uv run pytest tests/test_stackup.py -q`.

### Task 2 — the runner

- [ ] **Step 1: failing tests** (real service + `kernel` fixture; `slow` where
  the kernel is involved):
  - **AC9 — zero added work.** A project of spec-less parts: `run_specs` and a
    `set_params` rebuild issue **zero** `spec_declare`/`spec_eval` kernel calls
    (counting monkeypatch), and the rebuild payload's `specs` is `None`. Clear
    `service._status` first to force the full cache-key path, as
    `tests/test_branches.py:783` does.
  - **Tier 1 on rebuild.** A part with `check_mass` + `check_wall` +
    `check_that`: one `spec_eval` call with `affinity == part_id`; the summary
    counts; `fem_static`, `clearance`, `interference_free` and `stackup`
    declarations come back `skip` with `reason: "deferred"` (never silently
    dropped).
  - **FR10 caching.** A second rebuild at the same params issues **zero**
    `spec_eval` calls and reads `.cache/<key>.specs.json`; changing a param
    mints a new key and re-evaluates; the sidecar is a valid JSON document with
    `version == SPEC_RESULT_VERSION`; a corrupt sidecar is discarded and
    re-evaluated (the `metrics.json` precedent), never raised.
  - **AC2 — a failing spec never fails a rebuild.** `set_params` that drives the
    wall below the minimum returns `ok: True` with the failure in `specs`, and
    the mesh file exists.
  - **AC4 — project scope.** `check_clearance` on a too-close pair reports the
    measured minimum distance; `check_interference_free` on an overlapping pair
    is `fail` with the offending pair in `details.pairs`; both are absent from a
    rebuild and present in `run_specs`.
  - **AC5 — a raising predicate** is `status: "error"` with a traceback, the
    rebuild is `ok: True`, and the other checks in the same part still report.
  - **AC3 — FEM.** Without the extra, `check_fem_static` in `run_specs` is
    `{"status": "skip", "reason": "fem_extra_missing", "hint": …}`; with it
    (guarded by the `tests/test_analysis.py::_require_fem`
    `pytest.importorskip` triple) it evaluates against `max_vm_mpa` /
    `max_disp_mm`. The suite is green **without** the extra.
  - **AC6 — declarations without a build.** `list_specs` on a project whose
    parts have never been built issues zero `build` calls and returns every
    declaration with its requirement.
  - **Honest degradation.** An unknown instance id in `specs.py` is
    `status: "error"` naming the id; a `specs.py` that will not execute is an
    `errors[]` entry in `declarations()` and leaves the part specs readable; a
    `KernelError` from `spec_eval` turns that part's checks into `error`
    records and the report still returns.
  - **`ref`.** `run(proj, ref="feat")` evaluates under
    `branches.pinned(proj, branches.tree_of(proj, "feat"))`; an unknown ref is a
    `notfound_error`; a **tag** named like a branch is a `validation_error`
    (`history.resolve_branch`, never `resolve_ref` — PRD-001 X1); a `ref` on a
    project with no git is a `validation_error` naming git.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `SpecRunner`.** Three tiers per the design spec.
  `service.branches` read **inside** methods, never in `__init__` (pack load
  order). Kernel calls: `spec_declare`/`spec_eval` with `timeout_s=300.0,
  affinity=part_id`; `clearance` with `timeout_s=300.0, affinity=project`;
  `fem_static` with `timeout_s=600.0`. Caches: `.cache/<cache_key>.specs.json`
  (via `service._cache_key_for` and `store.cache_dir`) and
  `.cache/<project_key>.projspecs.json`. **No OCP import anywhere in this
  file** — the runner talks to the kernel over `service.kernel.request`. A
  declaration cache keyed by `sha256(script)`; a bounded LRU memo keyed by
  `(project, head, declaration_hash)` for `evaluate_specs`.
- [ ] **Step 4: run** — `uv run pytest tests/test_specs.py -q`, then `make test`.
- [ ] **Step 5: measure.** Time `check_wall` on the rocketry nozzle and on an
  `examples/surfacing` part; record both in the changelog. If the interactive
  rebuild cost is unacceptable, the lever is the declared `grid` option — **not**
  dropping the check.
- [ ] **Step 6: changelog + commit** — `docs/changelog/0089-spec-runner.md`
  (recompute `NNNN`), carrying the Step-5 measurements and a note that
  `tools_stackup` was refactored without behaviour change.

**Verification command:** `uv run pytest tests/test_specs.py tests/test_stackup.py -q`
plus `make test` (cite the count).

---

## Slice 4 — tool pack, route pack, the rebuild seam, the `specs.py` writer

**Files**
- Create: `agentcad/core/tools_specs.py`
- Create: `agentcad/server/routes_specs.py`
- Create: `tests/test_specs_api.py`
- Modify: `tests/test_specs.py` (writer cases)

**Interfaces produced**

Tools `run_specs {project, part_id?, ref?}`, `list_specs {project, part_id?}`,
`set_project_specs {project, script}`, `get_project_specs {project}`. Routes as
listed in the design spec. Service seams: `service.specs`, and the two installed
method wrappers. `update_part_script` / `set_params` / `set_solid_materials`
post-state gains `specs`; `get_part` gains `specs` beside `metrics`.

### Task 1 — the pack, registration and load order

- [ ] **Step 1: failing tests** in `tests/test_specs_api.py`, in the three
  sections `test_versioning_api.py` uses:
  - every tool registered; `input_schema["type"] == "object"`; `project` in both
    `properties` and `required`; a non-empty description;
  - a **description-contract** test: the descriptions must say that a failing
    spec never fails a rebuild, that a `skip` carries a `reason` and a `hint`
    and is not a failure, that `error` means the check itself broke, that a
    rebuild evaluates the shape tier only while `run_specs` evaluates
    everything, and (once Slice 5 lands) that a red `specs` gate blocks a
    proposal merge and `allow_invalid` does not waive it;
  - argument validation → `invalid_arguments` for a missing required arg, a
    wrong type and an unknown key;
  - **no self-disable**: with `ProjectHistory.available` monkeypatched to
    `False`, `run_specs` and `list_specs` are still registered and still work,
    and only a `ref=` argument raises a `validation_error` naming git (this is
    the deliberate difference from `tools_proposals`/`tools_versioning`);
  - **load order**: building the registry and immediately calling `run_specs`
    works — the pack must not touch `service.branches` at `register()` time
    (`tools_specs` sorts *before* `tools_versioning`), and `check_stackup` must
    reach `compute_stackup` directly, not `registry.call("tolerance_stackup")`
    (`tools_stackup` sorts *after* `tools_specs`).
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `tools_specs.register`** per the design spec's
  Surfaces section. Handlers are thin delegations to `service.specs`, matching
  `tools_proposals`'s shape (module-level `_PROJ` and shared prose constants,
  inner `def`s first, `registry.register(Tool(…))` after).
- [ ] **Step 4: run** — `uv run pytest tests/test_specs_api.py -q -k registration`.

### Task 2 — the rebuild seam

- [ ] **Step 1: failing tests**:
  - `install_rebuild_specs` is **idempotent** — calling it twice does not
    double-evaluate (count `spec_eval` calls) and does not double-wrap;
  - `_rebuild`'s success payload has exactly its previous keys **plus** `specs`;
    the failure payload (`{"ok": False, "error": …}`) has **no** `specs` key;
  - a spec-less part's payload and kernel-call count are byte-identical to the
    pre-feature behaviour (this is the guard against wrapper drift);
  - `get_part` carries `specs` beside `metrics`, from the cache, without
    triggering a rebuild when the part is already built;
  - an exception thrown inside the runner is swallowed into
    `{"specs": {"status": "error", "error": {…}}}` and **never** propagates out
    of a rebuild;
  - `tests/test_service.py`, `tests/test_tools.py` and `tests/test_server.py`
    pass unedited.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** `install_rebuild_specs(service)` in
  `tools_specs.py`, wrapping the two bound methods with `functools.wraps` and a
  sentinel attribute for idempotence. Comment **why** the seam is a wrapper and
  not a `service.py` edit, and **why** `_rebuild` rather than the three tools
  (the browser's `PATCH .../params` route calls `service.set_params` directly).
- [ ] **Step 4: run** — `uv run pytest tests/test_specs_api.py tests/test_service.py tests/test_tools.py -q`.

### Task 3 — the `specs.py` writer

- [ ] **Step 1: failing tests** (in `tests/test_specs.py`, `_GIT` marked):
  - `set_project_specs` writes `store.path_of(proj)/"specs.py"` atomically and
    returns post-state (declarations, or the declaration error) — never a bare
    OK;
  - it calls `store.write_guard` and is refused with a `conflict_error` under
    another client's turn lock;
  - it publishes `project_changed`, so the file lands in the branch's git
    history and `project_history` shows it;
  - a syntactically broken script is **written** and reported, not refused (the
    `update_part_script` precedent: you must be able to save a broken file to
    fix it);
  - an empty string deletes the file; `get_project_specs` on a project with none
    returns `{"script": None, "specs": []}`, not a 404;
  - the file rides branches: written on `feat`, absent on `master`, present
    again after `branch_switch`.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** the two tools and `SpecRunner.write_project_specs`.
- [ ] **Step 4: run** — `uv run pytest tests/test_specs.py tests/test_branches.py -q`.

### Task 4 — routes

- [ ] **Step 1: failing tests** in `tests/test_specs_api.py`: each route returns
  the tool payload verbatim; 404 for an unknown project/part, 422 for a bad
  `ref`; unknown body keys ignored and `null` not forwarded (post
  `{"evil": "x", "project": "other", "part_id": None}` and still get 200); a
  **chunked** PUT body reaches `set_project_specs` intact (the
  `routes_proposals._json` bytes-reading form, not the `content-length` one).
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `routes_specs.build_router`,** copying
  `routes_proposals`'s `_RAISE`/`_result`/`_body_keys`/`_json` helpers verbatim
  with an empty `_BODY_ERRORS`, and the module docstring as a route table.
  Whitelist every body key explicitly — never `**body`.
- [ ] **Step 4: run** — `uv run pytest tests/test_specs_api.py tests/test_server.py tests/test_mcp.py -q`,
  then `make test`.
- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0090-spec-tools-and-rebuild-summary.md` (recompute `NNNN`).

**Verification command:** `make test` — cite the count and confirm every
pre-existing test passed unedited.

---

## Slice 5 — `evaluate_specs` and the fail-closed proposal gate

**Why now:** it is the last behavioural piece, it depends on Slices 3–4, and it
is the one place where this feature changes another feature's outcome — so it
lands alone, with its own suite.

**Files**
- Modify: `agentcad/core/specs.py` (`evaluate_specs`, `gate_provider`)
- Modify: `agentcad/core/tools_specs.py` (append the provider)
- Modify: `tests/test_proposals.py` — **the one sanctioned existing-test edit in
  this plan**: `test_specs_and_checks_are_skipped_with_no_providers` gains
  `service.gate_providers = []` before its
  `assert getattr(service, "gate_providers", None) in (None, [])` line, because
  that is what the test's own name says it is testing and the provider now
  exists. Nothing else in that file changes; its two sibling provider tests
  already assign the list and are unaffected.
- Create: `tests/test_specs_gate.py`

**Interfaces produced**

```python
SpecRunner.evaluate_specs(proj, ref=None) -> {
    "available": bool, "status": "green"|"red"|"skip"|"pending",
    "ref": str|None, "head": str|None, "checked_at": str,
    "summary": {…}, "failures": [...], "skips": [...], "errors": [...],
    "reason": str|None}

# the gate, appended to PRD-002's service.gate_providers
{"name": "specs", "state": "pass"|"fail"|"pending"|"skipped",
 "summary": str,
 "details": {"status", "summary", "failures", "skips", "errors",
             "ref", "source_head", "specs_py_changed", "reason"}}
```

- [ ] **Step 1: failing tests** in `tests/test_specs_gate.py` (`_GIT` triple,
  autouse `_reset_context`, the real service + `build_registry`, `slow` where
  the kernel is involved):
  - **AC7:** `evaluate_specs(proj, ref=<tag of a good state>)` is `green`;
    `evaluate_specs(proj, ref=<branch with a broken budget>)` is `red` naming
    the failing check. Both under `pinned`, both recording the head.
  - The gate **replaces** PRD-002's placeholder: `proposal_get`'s gates still
    have exactly the five names `["state","approvals","validation","specs",
    "checks"]` in that order and never six, with `specs` no longer the
    placeholder. `tests/test_proposals.py` passes with only the one sanctioned
    line added above.
  - A provider that raises internally still yields a **`specs`** gate (name the
    function `specs` so even `ProposalManager.gates`'s own except-branch
    produces the right name) and never a duplicate.
  - **Fail-closed:** a declared check that was never evaluated is `fail`, not
    `pass` and not `pending`, with `details.reason` and a summary naming
    `run_specs`; the same for a kernel error and for a source branch whose part
    does not build.
  - `skipped` when the source ref declares no specs at all; `pass` with skips
    present and named; `pending` **only** when the source head moved during
    evaluation.
  - `proposal_merge` with a red `specs` gate raises `conflict_error` naming
    `specs` with `details.gates`, **before** anything is merged, and
    `allow_invalid: true` does **not** waive it (assert explicitly — this is the
    decision most likely to be "fixed" wrongly later).
  - Fixing the geometry on the source branch turns the gate green and the merge
    lands; the audit log carries the blocked attempt and the merge.
  - `details.specs_py_changed` is `true` when the source branch touched
    `specs.py` and `false` otherwise.
  - **Cost:** a second `proposal_get` at the same source head issues zero kernel
    calls (the memo); a head move invalidates it.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement.** `evaluate_specs` resolves a named ref with
  `history.resolve_branch`, evaluates under `branches.pinned(proj,
  branches.tree_of(proj, branch))`, and **re-reads the head afterwards** — a
  moved head is `pending`, never a verdict labelled with a commit it did not
  measure. `gate_provider()` returns a closure named `specs` that catches
  everything internally, applies `GATE_BUDGET_S`, and computes
  `specs_py_changed` with one
  `history._run("diff", "--name-only", target_head, source_head, "--",
  "specs.py")` (never a raw `subprocess`). Append it in
  `tools_specs.register` only when `service.gate_providers` exists.
- [ ] **Step 4: run** — `uv run pytest tests/test_specs_gate.py tests/test_proposals.py tests/test_proposals_api.py tests/test_prd002_acceptance.py -q`,
  then `make test`.
- [ ] **Step 5: measure.** Time a cold and a warm `proposal_get` on a rocketry
  copy with specs declared; record both in the changelog. If the cold read is
  unacceptable, the next lever is storing the report beside `packet.json` at
  packet-build time — which is a `packet.py` change and therefore a **separate**
  slice, not a patch to this one.
- [ ] **Step 6: changelog + commit** — `docs/changelog/0091-spec-gate.md`
  (recompute `NNNN`), stating the fail-closed decision and its rationale
  explicitly, with the Step-5 numbers.

**Verification command:** `make test` plus
`uv run pytest tests/test_specs_gate.py -q`.

---

## Slice 6 — frontend: spec chips in the inspector

**Files**
- Modify: `frontend/js/inspector.js` (`appendSpecsHost`, `renderSpecs`, the
  chip atom, one call in `render()`)
- Modify: `frontend/js/api.js` (a `// ---- specs ----` section)
- Modify: `frontend/css/app.css` (`.spec-block`, `.spec-chips`, `.spec-chip` +
  four states)

**`index.html`, `main.js` and `state.js` are NOT modified** — the chips ride
`state.part.specs`, `inspector.render` is already subscribed via
`onKeys(["part"], render)`, and `main.js`'s `rebuild_finished` case already
calls `refreshPartDetail(ev.part)`. If a slice-6 change to any of those three
files seems necessary, stop and re-read Decision 5 and Decision 9.

- [ ] **Step 1: `api.js`** — one-line arrows over the module-private `enc()`:
  `listSpecs`, `runSpecs`, `getProjectSpecs`, `setProjectSpecs`. They exist for
  the Phase-2 panel and for manual testing; the chips call none of them.
- [ ] **Step 2: `inspector.js`** — `appendSpecsHost()` mirroring
  `appendWarningsHost()` (a `<div class="spec-chips" id="spec-chips">` appended
  by `buildParamControls`, so it is re-created with the controls and never
  accumulates), and `renderSpecs(part)` called from `render()`'s unconditional
  tail beside `renderWarnings(part)`. One chip per check via a local
  `specChip(check)` atom modelled on `proposals.js`'s `gateChip`:
  `createElement("span")`, `className = "spec-chip spec-" + status`,
  `textContent = check.name`, `title` = measured vs limit + requirement +
  message. **`createElement` + `textContent` only** — names, requirements and
  messages are script-controlled strings, so `inspector.js`'s own
  `row()`/`arow()` template-literal builders are the wrong precedent here.
  `part.specs == null` renders nothing at all (no header, no empty note); a
  reference part goes through `buildReferencePane` and renders nothing by the
  same rule.
- [ ] **Step 3: CSS** — a `/* --- design specs --- */` block near the analysis
  block, copying `.gate-chip`'s recipe (mono 10 px, `border-radius: 9px`,
  `padding: 2px 7px`, `border: 1px solid var(--hairline)`) with four states:
  pass → `var(--ok)` border + colour, no background (there is **no**
  `--ok-soft`/`--ok-ring` pair and none is added — `.gate-pass` already solves
  this); fail → `--err-text` on `--err-soft` with `--err-ring`; error → the same
  in `--err`; skip → `--dim` on the default hairline. `.spec-chips` copies
  `.sk-chips`' `display: flex; gap: 4px; flex-wrap: wrap`. **No new token**, so
  light mode keeps working.
- [ ] **Step 4: verify in the real browser** (use the **`run` skill**; this is
  AC8 and the definition of done): open the rocketry copy, select the nozzle,
  see green `wall_min` / `mass_max` chips; drag `wall` below 2.5 mm and watch
  `wall_min` go red with the measured value in its tooltip; drag it back and
  watch it go green. Repeat in light mode. **Zero console errors.** Screenshot
  the green state, the red state and the light-mode state.
- [ ] **Step 5: changelog + commit** — `docs/changelog/0092-spec-chips-ui.md`
  (recompute `NNNN`), with the screenshots and the clean console on the record
  (the AC8 test in Slice 7 asserts this evidence exists, exactly as
  `test_prd002_acceptance.py` does for its browser halves).

**Verification command:** `make test` plus the browser session above, with
screenshots and a clean console.

---

## Slice 7 — rocketry specs, docs, acceptance criteria, PRD close-out

**Files**
- Modify: `examples/rocketry/parts/nozzle.py` (a `SPECS` block)
- Create: `examples/rocketry/specs.py`
- Modify: `examples/rocketry/README.md` (the spec loop, beside the existing
  "How an agent iterates on this project" section)
- Modify: `agentcad/core/templates.py` (`CHEATSHEET`: an `Optional: SPECS = […]`
  paragraph in the numbered contract block beside the `SOLID_LABELS` note, and a
  `DESIGN SPECS (from agentcad.toolkit.specs import …)` section following the
  file's ALL-CAPS-plus-dashed-underline convention). **Do not** change
  `DEFAULT_PART_SCRIPT` — every new part would then carry a spec.
- Modify: `docs/agent-api.md` (a "Design specs" section next to "Drawings and
  analysis"; the four new tools; the `specs` key on rebuild-returning tools and
  on `get_part`; the routes; bump the tool count from 60/63)
- Modify: `docs/part-authoring.md` (a `## Design specs (SPECS)` section after
  the connectors section, including the wall-thickness caveat)
- Modify: `docs/architecture.md` (the three tiers, the `.specs.json` sidecar,
  the new handler pack, the gate provider, and — in the trust-model section —
  that specs run in the confined kernel worker and never in the server process)
- Modify: `docs/user-guide.md` (the inspector chips)
- Modify: `AGENTS.md` (a "Spec gotchas (PRD-003)" section — see below)
- Modify: `docs/roadmap.md` (PRD-003 row) and the PRD's own `Status:` line
- Modify: the PRD itself with the **eleven divergences to fold back** listed at
  the end of the design spec
- Create: `tests/test_prd003_acceptance.py`
- Create: `docs/changelog/0093-specs-docs-and-acceptance.md`

**The `AGENTS.md` traps to write** (condensed, in that file's voice):

- Specs are **code in the tree**, not manifest state — part scope in
  `parts/<id>.py`'s `SPECS`, project scope in a root `specs.py`. `git add -A`
  tracks them, so branching, restore, undo and merge are free; use
  `store.path_of` (authored, branch-resolved), never `canonical_path_of`.
- **A failing spec never fails a rebuild.** Geometry lands, `ok` stays `true`,
  the failure is signal. `skip` (with a `reason` and a `hint`), `fail` and
  `error` are three different things and must not be collapsed.
- **A rebuild evaluates the shape tier only.** Assembly checks and FEM are
  deferred and say so; `run_specs` evaluates everything.
- **`SPECS` is in the script, so it is in the cache key** — editing a spec
  forces one kernel rebuild of a part whose geometry did not change. That is
  deliberate: splitting geometry identity from spec identity risks serving a
  stale mesh.
- **The `specs` gate is fail-closed.** A declared check that was not evaluated
  is red, and `allow_invalid` does not waive it (it means "override the
  *kernel's* verdict on geometry", nothing else).
- **Three live name collisions:** `service._spec_cache` already means the PARAMS
  spec cache, `inspector.js`'s `renderedSpecJson` already means the PARAMS spec
  JSON, and `packet.py`'s `params_diff` rows already use `"source": "spec"` for
  the PARAMS declaration. Do not reuse any of them.
- **`_min_wall` measures along the inward face normal from a UV sample grid** —
  it over-estimates on non-parallel walls and can miss a feature finer than the
  sample spacing. `check_wall`'s `grid` is the knob; it is not a medial-axis
  measurement and must not be described as one.

**Acceptance criteria → concrete tests** (one named test per criterion,
mirroring `tests/test_prd002_acceptance.py`, with the `| AC | Test |` table in
the module docstring)

| AC | Test | Assertion |
|---|---|---|
| AC1 | `test_ac1_rocketry_ships_green_specs_and_thinning_turns_red` — `examples/rocketry` **on a copy** | as shipped, `run_specs` is green across the chamber mass budget, the nozzle wall minimum and the flange bolt-circle clearance; `set_params {"wall": 2.0}` turns it red naming `check_wall` with `measured`, `limit` and a non-null `location` |
| AC2 | `test_ac2_failing_spec_still_lands_geometry` | the same `set_params` returns `ok: True`, the mesh exists, and the failure is in `specs` |
| AC3 | `test_ac3_fem_check_skips_without_extra_and_evaluates_with_it` | paired: without `[fem]`, `{"status": "skip", "reason": "fem_extra_missing"}` + a hint; with it (`importorskip` triple), a real verdict. Suite green without the extra |
| AC4 | `test_ac4_project_specs_measure_clearance_and_name_interference` | `check_clearance` reports the measured minimum for a too-close pair; `check_interference_free` names the offending pair in `details.pairs` |
| AC5 | `test_ac5_raising_predicate_is_an_error_not_a_crash` | `status: "error"` with `details.traceback`; the rebuild is `ok: True`; sibling checks still report |
| AC6 | `test_ac6_requirements_group_and_list_specs_does_not_build` | requirement grouping matches the declarations; `list_specs` issues **zero** `build` kernel calls (counting monkeypatch) |
| AC7 | `test_ac7_evaluate_specs_green_for_tag_red_for_branch` | over PRD-001 refs: green for a tagged good state, red for a branch with a broken budget |
| AC8 | `test_ac8_spec_chips_verified_in_browser` | the PRD-001 AC6 / PRD-002 precedent: assert Slice 6's recorded browser session (screenshots + clean console) is on the changelog record; do not re-drive a browser |
| AC9 | `test_ac9_specless_parts_add_no_kernel_work` | a spec-less project issues zero `spec_declare`/`spec_eval` calls on rebuild and on `run_specs`; full suite green with the count cited |

- [ ] **Step 1:** write the rocketry specs. `parts/nozzle.py` gains
  `check_wall(min_mm=2.5, requirement="ENG-014")` and
  `check_mass(max_g=…, requirement="SYS-042")` — **pick the mass budget from the
  shipped default's actual `metrics.mass_g`** (inconel718, so it is
  material-dependent), with enough headroom that the spec stays green across
  `tests/test_examples.py`'s min/max sweep of all five nozzle params. A root
  `specs.py` gains `check_interference_free()` and the flange bolt-circle
  `check_clearance(..., requirement="INT-003")`. Then run
  `uv run pytest tests/test_examples.py -q` — **it must pass unedited**; its
  three contracts (build valid at defaults, build at every param extreme,
  `check_interference` clean) are the constraint on what the specs may assert.
- [ ] **Step 2:** write the AC tests (they are the last failing tests; they
  should pass once Slices 1–6 are in).
- [ ] **Step 3:** update the docs above, matching each file's existing style.
  `docs/agent-api.md` must state: a failing spec is data, not an error; the
  three statuses beyond `pass` and what each means; that a rebuild evaluates the
  shape tier only; that the `specs` gate is fail-closed and `allow_invalid` does
  not waive it; and that requirement strings are opaque — we store them, we do
  not resolve them.
- [ ] **Step 4:** `make test` → green; **record the exact count** (666 passed,
  1 skipped before this work).
- [ ] **Step 5:** `make test-portability` → green (every new git-touching test
  carries the `portability` marker).
- [ ] **Step 6:** write the changelog from `git diff`, fold the eleven
  divergences back into the PRD, update its `Status:` line and the roadmap row,
  and commit. Follow the PRD-002 close-out convention: the PRD stays in
  `docs/prd/in-progress/` with `Status:` updated and an AC-verification
  paragraph; moving it to `docs/prd/completed/` and flipping the roadmap row to
  `completed` happen when the branch merges, not on the branch.

**Verification command:** `make test` and `make test-portability`, both green,
with the counts cited; plus `git diff --name-status main -- tests/` showing only
additions (`A`) of the new test files.

---

## Rollback / landing notes

- **Slice 1 is inert on its own**: `toolkit/specs.py` is imported by nothing
  until a part script imports it, so landing it changes no behaviour.
- **Slice 2 is inert on its own**: three handlers nobody calls. It is also
  independently useful — `clearance` is a general-purpose measurement — and
  independently revertible.
- **Slice 3 is inert on its own**: `core/specs.py` is imported by nothing until
  Slice 4's pack exists.
- **Slice 4 is the first behaviour change.** If the `_rebuild` wrapper proves
  fragile, the fallback is to attach `specs` from `get_part` only (chips still
  work, the rebuild post-state does not carry it) — a smaller FR5, not a broken
  feature.
- **Slice 5 is the only slice that changes another feature's outcome.** It is
  revertible by not appending the provider: PRD-002's `skipped` placeholder
  returns and merges behave exactly as they do today.
- **If `BRepExtrema` proves too slow or unreliable** on real assemblies (the
  design spec's first risk), `check_clearance` degrades to
  `status: "error"` with the structured kernel payload and everything else in
  the vocabulary still ships. A bbox lower-bound pre-filter is the first
  optimisation and is sound (`dist(A,B) ≥ dist(boxA,boxB)`), but it is a **new**
  slice with Slice 2's measurement behind it, not a patch to this one — and it
  must never replace a real `measured` with a bound.
- **If `check_wall` on every rebuild proves too expensive**, the lever is the
  declared `grid` option, then moving `wall` to tier 2. Dropping the check is
  not a lever — it is the PRD's headline example and AC1's subject.
- **If the cold `proposal_get` gate proves too slow**, the lever is persisting
  the report beside `packet.json` at packet-build time. That touches `packet.py`
  and is therefore a separate slice with its own review.
