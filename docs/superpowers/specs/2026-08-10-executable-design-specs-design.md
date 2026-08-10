# Executable design specs — design

**Date:** 2026-08-10 · **Status:** approved for implementation ·
**PRD:** [PRD-003](../../prd/in-progress/PRD-003-design-specs-executable.md)
**Builds on:** [PRD-001](../../prd/completed/PRD-001-branching-version-control.md)
(branches, refs, `pinned`) and
[PRD-002](../../prd/completed/PRD-002-change-proposals-geometric-diff.md)
(the `service.gate_providers` seam, built for this PRD) and their design specs
([PRD-001](2026-08-09-branching-version-control-design.md) ·
[PRD-002](2026-08-10-change-proposals-design.md)).
**Scope:** every functional requirement FR1–FR13 and every acceptance criterion
AC1–AC9. The PRD's MVP/Phase-2 split governs the **UI only**: MVP ships the
per-part inspector chips; the requirement-grouped project Specs *panel* and the
viewport thin-point markers are out of scope (Phase 2). Everything else the ACs
name — including `check_stackup`, `check_fem_static`, result caching (FR10) and
the proposal gate (FR11) — is in, because AC3, AC6, AC7 and AC9 cannot be
verified without them.

## Problem

The kernel is a syntax referee. It answers "does this script build a valid
solid?" and nothing else. Every statement of *intent* — "under 120 g", "walls
never below 2.5 mm", "0.5 mm to the chamber" — lives in chat, in a README, or
in a spreadsheet, and diverges from the model the moment somebody drags a
slider. Three edits later the part is 140 g and nobody knows.

For an agent this is the missing half of the feedback loop. The structured-error
contract (`script_error` with `details.line` and `details.hint`) tells an agent
precisely when it broke the *model*, which is why agents self-correct geometry
so well here. Nothing tells an agent when it broke the *requirement*, so an
agent's termination condition today is "it builds", which is the wrong
condition. PRD-018 (task→part), PRD-019 (optimization) and PRD-024 (bench) all
need a machine-readable "is it right yet", and PRD-002 shipped a gate seam with
nothing behind it: `proposal_get` returns `{"name": "specs", "state":
"skipped", "summary": "spec evaluation not installed"}`.

Three things have to exist, in this order:

1. **A declaration surface** that rides branches, diffs and merges for free —
   which, given "the model is code", means *code in the tree*, not rows in a
   database;
2. **An execution model** that is cheap enough to run on every rebuild and
   honest enough to be evidence: deterministic, cached under the existing
   content-hash discipline, degrading with reasons rather than silence;
3. **A gate** that makes a red spec block a merge, which PRD-002 deliberately
   left to this PRD (its as-built note: "PRD-003 (specs) and PRD-004 (checks)
   own their own blocking semantics").

Everything else in this document follows from those three.

---

## Architecture at a glance

```
        agentcad/toolkit/specs.py        ← pure-data constructors, ZERO kernel imports
        (check_wall, check_mass, …)         imported BY part scripts and specs.py
                    │  declaration
                    ▼
   parts/<id>.py  →  SPECS = [...]        specs.py  →  SPECS = [...]     (project root,
   (part scope)                           (project scope)                 tracked by git)
                    │                            │
                    └──────────────┬─────────────┘
                                   ▼
                     service.specs ──► SpecRunner              (core/specs.py)
                     (seam installed     • declaration cache  (script sha)
                      by the pack)       • result cache       (<cache_key>.specs.json)
                                         • three evaluation tiers
                                         • report + requirement grouping
                                         • evaluate_specs(project, ref) → gate shape
                                   │
        ┌──────────────┬───────────┴────────┬──────────────────┬───────────────┐
        ▼              ▼                    ▼                  ▼               ▼
  kernel "spec_declare"  kernel "spec_eval"   kernel "clearance"   service.check_    tolerance
  kernel "clearance"     (metrics/wall/that)  (BRepExtrema)        interference      stackup
        └──────── agentcad/kernel/handlers/specs.py ────────┘     (unchanged)      (extracted)

   tools_specs.py            routes_specs.py         service._rebuild / service.get_part
   (run_specs, list_specs,   (/api/projects/{p}/       (WRAPPED at register time —
    set/get_project_specs)    specs, …/specs/run)       the install_write_guard precedent)
                                   │
                                   └──► service.gate_providers.append(specs)   ← PRD-002 seam
```

Two new modules (`agentcad/toolkit/specs.py`, `agentcad/core/specs.py`), one
new kernel handler pack (`agentcad/kernel/handlers/specs.py`), one tool pack,
one route pack, one frontend function. **`worker.py`, `tools.py`, `app.py`,
`service.py`, `proposals.py`, `packet.py`, `merge.py`, `branches.py` and
`history.py` are not edited at all.** Exactly two existing files change, both
additively: `agentcad/toolkit/__init__.py` (one name in `__all__` and its lazy
re-export) and `agentcad/core/tools_stackup.py` (its handler body is lifted to
a module-level pure function so `check_stackup` can call it without going
through the registry). `core/templates.py`'s `CHEATSHEET` gains a section,
which is an authoring-surface change the definition of done already requires.

---

## Decision 1 — intent is code in the tree, in exactly two places

**Chosen:** part-scope specs are a module-level `SPECS` list in the part script;
project-scope specs are a module-level `SPECS` list in a root-level `specs.py`.
Both are plain Python evaluated by the kernel worker. **No manifest storage, no
new schema section, no database.**

```python
# parts/nozzle.py
from agentcad.toolkit.specs import check_wall, check_mass, check_that

SPECS = [
    check_wall(min_mm=2.5, requirement="ENG-014"),
    check_mass(max_g=2000.0, requirement="SYS-042"),
    check_that(lambda part, metrics: metrics["bbox"]["max"][2] <= 180.0,
               name="fits_fairing", requirement="SYS-011"),
]
```

```python
# specs.py  (project root)
from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(),
    check_clearance("flange_1", "injector_plate_1", min_mm=0.5,
                    requirement="INT-003"),
]
```

**Why not the manifest.** `project.json` would work — `_read_manifest` is
permissive, unknown top-level sections survive, and `manifest_merge.py` handles
them generically, so a `"specs"` section needs no schema bump. It was rejected
for three reasons, in order of weight:

1. **`check_that` is the point.** A predicate over the built part is what makes
   this a *test* framework rather than a constraint form, and a JSON manifest
   cannot hold a lambda. Splitting the vocabulary — declarative checks in JSON,
   predicates in code — would give the feature two homes and two diffs.
2. **The house bet is that the model is code.** A spec in a script is read,
   written and diffed by an agent with the same machinery as the geometry it
   governs, and a human reviewing a proposal sees "the agent gave itself a
   looser budget" as three red lines in the Files tab.
3. **Merging.** `project.json` never merges line-wise; it is always re-merged
   by `manifest_merge.py`, and a specs section would need new merge rules
   (list-of-objects, identity by what?). `parts/*.py` and root text files
   already three-way merge with diff3 markers and a resolvable
   `resolve_merge` payload. Nothing to build.

**Versioning, branching, restore and undo are free.** `ProjectHistory` commits
with `git add -A` and the only managed excludes are `.cache/`, `exports/`,
`.history/` and `*.tmp` (`history.py:46`) — there is no allow-list of tracked
files. A root `specs.py` is therefore committed, branched, restored and
undone by machinery that needs no change, which is FR2 satisfied structurally
rather than by a rule.

**Path resolution.** `specs.py` is *authored state*, so it is always reached
through `store.path_of(proj) / "specs.py"` — the branch-resolved path — never
`canonical_path_of`. Under `branches.pinned(proj, tree)` the same expression
reads that branch's copy, which is how `evaluate_specs(project, ref)` works
(Decision 7). Derived spec *results* go the other way: they are content-keyed
and live in `.cache/`, which is canonical and shared by every branch (PRD-001
Decision 5).

**Discovery is presence, not convention.** Nothing in the store enumerates
root-level `.py` files, and nothing should start: `specs.py` is discovered by
`is_file()`, part specs by the AST scan of Decision 3. A second root-level
Python file is not a spec file and is not executed.

### Declarations are pure data; evaluation is the kernel's job (FR4)

`agentcad/toolkit/specs.py` imports nothing from `agentcad.kernel`, nothing
from `OCP`, nothing from `build123d`. Each constructor returns a plain dict:

```python
def check_wall(min_mm: float, *, name: str | None = None,
               requirement: str | None = None, grid: int = 8) -> dict:
    """Minimum wall thickness over the built part (mm)."""
    _positive("min_mm", min_mm)
    return {"spec": 1, "kind": "wall", "scope": "part",
            "name": name or "wall_min",
            "limit": {"min_mm": float(min_mm)},
            "requirement": requirement,
            "options": {"grid": int(grid)}}
```

`"spec": 1` is the marker and the format version — a dict without it is not a
spec and is rejected with a message naming `agentcad.toolkit.specs`. Every
constructor takes `requirement: str | None` (FR3) and `name: str | None`.

**Constructors validate eagerly, and that is how FR1 is met.** `SPECS` is built
while the module executes, so `check_wall(min_mm="thick")` raises inside
`_exec_script`, which is already `script_error` carrying `details.traceback`
and `details.line` — byte-identically to a malformed `PARAMS`. No new
machinery, no new error type. The narrow residue that eager validation cannot
catch — `SPECS = "hello"`, or a list holding a dict that no constructor
produced — is a **structural** error, and Decision 5 reports it as data on an
otherwise successful rebuild rather than failing the build (PRD divergence 1).

A `check_fem_static(...)` declaration therefore constructs cleanly on a machine
with no `[fem]` extra, which is FR4 verbatim, and the machine-specific question
("can we run it?") is asked at evaluation time by Decision 3's tier 3.

## Decision 2 — the v1 vocabulary, and what each check actually costs

FR3's ten constructors, with the measurement each one reuses. **The rule the
PRD sets and this spec keeps: a constructor lands only when its measurement
already exists as a handler.** Exactly one exception is sanctioned — `clearance`
(Decision 6) — and it is the only new geometry code in the feature.

| constructor | scope | measured from | kernel work | notes |
|---|---|---|---|---|
| `check_valid()` | part | `metrics.is_valid` and `n_solids > 0` | none beyond the build | |
| `check_mass(min_g?, max_g?)` | part | `metrics.mass_g` | none | material-dependent by design; the density is already in the cache key |
| `check_volume(min_mm3?, max_mm3?)` | part | `metrics.volume_mm3` | none | the solids-sum volume, never `.volume` |
| `check_bbox(within_mm)` | part | `metrics.bbox` | none | `within_mm` is a scalar (all axes) or `[x, y, z]`; `measured` is the size vector |
| `check_wall(min_mm, grid=8)` | part | `handlers/analysis._min_wall` | ray casts, `O(solids × faces × grid²)` | yields `location` — FR6's world point, and AC1's "thin point" |
| `check_that(fn, name)` | part | the predicate, over `(part, metrics)` | none beyond the build | runs **inside the sandboxed worker**, never in the server |
| `check_interference_free(min_volume_mm3=0.001)` | project | `service.check_interference` | one `interference` request | names the offending pair in `details.pairs` (AC4) |
| `check_clearance(a, b, min_mm)` | project | the new `clearance` handler | one `clearance` request per pair | Decision 6 |
| `check_stackup(from_instance, to_instance, axis, within)` | project | `tools_stackup`'s math, extracted | none | pure manifest/PMI arithmetic |
| `check_fem_static(fixed_face, load_face, load_N, max_vm_mpa?, max_disp_mm?)` | part | the `fem_static` kernel method | one 600 s-budget request, **or a skip** | FR8/AC3 |

Three vocabulary facts worth stating because they are load-bearing:

- **`metrics` is passed to predicates exactly as the kernel produces it** —
  `{volume_mm3, area_mm2, mass_g, bbox: {min, max}, center_of_mass, is_valid,
  n_faces, n_edges, n_solids, solids?}`. There is deliberately **no derived
  `bbox.size`**, because a second metrics shape is a second thing to keep
  true; a predicate writes `metrics["bbox"]["max"][2] -
  metrics["bbox"]["min"][2]`, and the ergonomic case is `check_bbox`. The
  PRD's example uses `metrics["bbox"]["size"][2]`, which does not exist —
  PRD divergence 2.
- **`limit` is a dict, not a scalar** (`{"min_mm": 2.5}`, `{"max_g": 2000.0}`,
  `{"min_g": …, "max_g": …}`). FR6 says "limit"; a two-sided check has two, and
  a dict says which is which without a convention. PRD divergence 3.
- **Part-scope specs exist only in scripts.** A reference (imported STEP/STL)
  part has no script, so it declares nothing; project-scope checks may still
  name its instances, and the ones that cannot measure a mesh skip with
  `reason: "mesh_only"` (FR8).

**Deliberately not in v1:** `check_that` at project scope (there is no single
built shape to hand a predicate; a project-scope `that` declaration is reported
`status: "error"`, `reason: "unsupported_scope"`, naming the two scopes),
draft-angle and DFM checks (PRD-021 is the sanctioned expansion path), and
`advisory: true` (the PRD's own open question — deferred until a real user asks,
because the rebuild/boundary split already expresses severity contextually).

## Decision 3 — three evaluation tiers, one kernel call for the shape tier

Evaluation is service orchestration, exactly like the review packet: the runner
partitions declared checks by what they need and pays for each need once.

| tier | checks | when it runs | cost |
|---|---|---|---|
| **1 — shape** | `valid`, `mass`, `volume`, `bbox`, `wall`, `that` | on every rebuild **and** in `run_specs` | one `spec_eval` request with `affinity=part_id`, or a disk-cache hit |
| **2 — assembly** | `interference_free`, `clearance`, `stackup` | `run_specs` / `evaluate_specs` only | one `interference` request, one `clearance` request per pair, zero for `stackup` |
| **3 — expensive/optional** | `fem_static` | `run_specs` / `evaluate_specs` only | one `fem_static` request (600 s budget) or a skip |

**Why tier 1 alone runs on rebuild (G2).** A rebuild is what an engineer does
while dragging a slider; a 600 s FEM solve or a whole-assembly interference
pass there would make the feature something you turn off. Tier 2 is not
part-scoped at all (moving one part changes every clearance), and tier 3 is the
one thing in the vocabulary whose cost is unbounded. So a rebuild reports tiers
2 and 3 as `{"status": "skip", "reason": "deferred", "hint": "run_specs
evaluates this tier"}` — visible, named, never silent. `run_specs`,
`evaluate_specs` and the proposal gate run all three.

### The shape tier: one call, the namespace, and the predicate problem

A `check_that` predicate is a Python callable. It cannot cross the JSON-RPC
boundary, so the *service* can never see it — which is exactly right, because
predicates must run confined in the worker, never in the server process (the
PRD's first risk). The kernel therefore reads `SPECS` from the script's own
namespace:

```
method: "spec_eval"
params: {
  "script": "<part script text>",
  "params": {…},                 # the manifest's overrides
  "density_g_cm3": 8.19,
  "densities": {…}|null,         # per-solid, when solid_materials exist
  "indices": [0, 1, 3]           # which declared checks to evaluate; null = the shape tier
}
result: {
  "checks": [ <check record>, … ],     # one per requested index, in order
  "declared": [ <declaration>, … ],    # every declared spec, JSON-safe
  "warnings": []
}
```

The handler calls `toolbox["build_shape_ns"]`, which returns
`(shape, values, warnings, ns)` — the same seam `connectors(p, part)` and
`analysis(p)` already use — and reads `ns.get("SPECS")`. Because
`build_shape_ns` is backed by the worker's 16-entry shape LRU keyed on
`sha256(script, resolved values)`, a `spec_eval` sent with `affinity=part_id`
immediately after the `build` that produced the part is a cache hit: **the
marginal cost of the shape tier is the wall ray casts and the predicates, not a
rebuild.**

`spec_declare` is the same execution without the build (FR7, AC6):

```
method: "spec_declare"
params: {"script": "<text>", "scope": "part"|"project"}
result: {"declared": [ <declaration>, … ], "warnings": []}
```

It executes the module and reads `SPECS`; it never calls `build`. A
`check_that` declaration comes back with its callable replaced by
`"predicate": true` — every declaration crossing the boundary is JSON-safe by
construction, and the callable never leaves the worker.

### Caching: the existing content-hash discipline, extended by one sidecar (FR10)

Tier-1 results depend on exactly `(script text, resolved params, density,
per-solid densities)` — which is precisely what `service._cache_key_for`
already hashes, because `SPECS` lives *in* the script text. So:

```
.cache/<cache_key>.acm            the mesh              (today)
.cache/<cache_key>.metrics.json   {metrics, warnings, lods}   (today)
.cache/<cache_key>.specs.json     {version, checks, declared, tiers: {…}}   (new)
```

Written with `ProjectStore._atomic_write`, read before any kernel call, and
invalidated for free — editing a spec changes the script, which mints a new
key. `.cache/` is canonical and shared by every branch, so the *source* branch
of a proposal reuses the target's results for every unchanged part, which is
what makes the gate cheap (Decision 7). Tier-2 and tier-3 results are appended
into the same file as they are computed, under `tiers`, so a `run_specs` that
computed FEM once does not compute it again.

Project-tier results get their own key, because they depend on the assembly
rather than one part:

```
project_key = sha256(specs.py text · sorted[(instance id, part cache_key,
                     resolved position, resolved rotation_deg)])[:32]
.cache/<project_key>.projspecs.json
```

**Determinism is the precondition, not a bonus.** The same script and params
produce identical geometry, so identical measurements, so a cached spec result
is as trustworthy as a cached mesh. PRD-004's speed rests on this file.

### Zero added work for a spec-less part (FR5, AC9)

The runner must decide "does this part declare specs?" without executing
anything. It parses the script text with `ast.parse` and looks for a
module-level assignment binding the name `SPECS`:

```python
def declares_specs(script: str) -> bool:
    """True iff the module binds a top-level name SPECS. AST, never exec —
    the kernel is the only thing that runs a part script (the rule
    packet.params_spec already follows for PARAMS declarations)."""
```

Memoized by `sha256(script)`. A part with no `SPECS` costs one `ast.parse`
(microseconds) and **no kernel request at all** — AC9 asserts this by counting
`kernel.request` calls with the established
`(method, params, timeout_s=None, affinity=None)` monkeypatch. The scan is a
*presence* test and it is exact: a script that builds `SPECS` in a loop still
binds the name, and a script that mentions `SPECS` only in a comment or a
string does not. `SyntaxError` from `ast.parse` returns `False` — a script that
does not parse fails its build with a line number anyway.

`specs.py` presence is the same question with a cheaper answer: `is_file()`.

### Failure, degradation and the four statuses (FR6, FR8, FR9)

A check record is:

```json
{"id": "nozzle:wall_min", "name": "wall_min", "kind": "wall", "scope": "part",
 "part": "nozzle", "status": "fail",
 "measured": 1.42, "limit": {"min_mm": 2.5}, "unit": "mm",
 "requirement": "ENG-014",
 "location": [12.04, 0.0, 41.5],
 "message": "min wall 1.42 mm is below the 2.5 mm minimum",
 "details": {}}
```

`status` is one of four, and the distinctions are the whole contract:

- **`pass`** — measured, within limit.
- **`fail`** — measured, outside limit. `measured`, `limit` and `message` are
  always populated; `location` when the measurement yields one (today: `wall`,
  and `clearance`'s witness points).
- **`skip`** — *could not be measured, for a named and structural reason*.
  `reason ∈ fem_extra_missing | mesh_only | deferred | unsupported_scope |
  no_instances`, always with a `hint`. **Skips are data, never hidden** (G4)
  and never a failure; a CI `--strict` mode (PRD-004) escalates them.
- **`error`** — *the check itself broke*: a predicate raised (AC5), an unknown
  instance id (the PRD's rename risk), a malformed declaration, a kernel error
  while measuring. `details.traceback` and `details.line` are carried through
  from the worker. An `error` is **not** a build failure and **not** a `fail`;
  it is "we do not know", and Decision 7 treats not-knowing as red at a
  boundary.

Every stage is independently fallible and none of them raises out of an
evaluation: a `KernelError` from `spec_eval` turns that part's requested checks
into `error` records carrying the structured payload, and the report still
returns. This is PRD-002's packet discipline applied one layer up — *the report
degrades, it never raises.* The only things that raise are the ordinary
argument errors (`NotFoundError` for an unknown project/part, `ValidationError`
for a bad ref).

## Decision 4 — the report and requirement traceability

`run_specs {project, part_id?}` returns one document (FR7, FR12):

```json
{
  "project": "rocketry",
  "ref": null,
  "generated": "2026-08-10T09:12:04Z",
  "status": "red",
  "summary": {"passed": 7, "failed": 1, "skipped": 1, "errors": 0, "total": 9},
  "checks": [ <every record, flat, in declaration order per scope> ],
  "parts": {
    "nozzle":  {"status": "red",  "summary": {…}, "cached": true,
                "checks": ["nozzle:wall_min", "nozzle:mass_max"]},
    "flange":  {"status": "green", "summary": {…}, "cached": true, "checks": []}
  },
  "project_checks": {"status": "green", "summary": {…}, "checks": ["project:no_interference"]},
  "requirements": {
    "ENG-014": {"status": "fail",  "checks": ["nozzle:wall_min"]},
    "SYS-042": {"status": "pass",  "checks": ["nozzle:mass_max"]},
    "INT-003": {"status": "pass",  "checks": ["project:clearance_flange_1_injector_plate_1"]}
  },
  "declared": 9,
  "warnings": []
}
```

- **`status`** is `green` (nothing failed or errored), `red` (something did), or
  `skip` (nothing was declared at all). A report with skips and no failures is
  `green` with the skips named in `summary` — G4 again.
- **`id` is `<part_id>:<name>` for part scope and `project:<name>` for project
  scope**, and it is the join key used by `parts`, `project_checks` and
  `requirements` so the flat `checks` list is the single source of every
  record. A duplicate name inside one scope gets `#2`, `#3` … appended and a
  `warnings` entry — never a silently merged row.
- **`requirements` maps only requirement strings that at least one check
  carries** (FR12: "a requirement with zero checks does not exist to us"). A
  requirement's status is `fail` if any of its checks failed or errored,
  `pass` if at least one passed and none failed, `skip` if all of them skipped.
  The string is opaque — an id like `SYS-042` or a URL — and is never parsed,
  resolved or validated against anything. That is the whole of "we are not a
  requirements database".

`list_specs {project, part_id?}` returns the declarations with no evaluation
and **no build** (AC6):

```json
{"project": "rocketry", "declared": 9,
 "parts": {"nozzle": {"specs": [ <declaration>, … ]}, …},
 "project_specs": {"path": "specs.py", "specs": [ … ]},
 "requirements": {"ENG-014": ["nozzle:wall_min"], …},
 "errors": []}
```

A declaration is the constructor's dict minus `fn`, plus `"predicate": true`
for `check_that`. `errors` carries per-file declaration failures (a `specs.py`
that will not execute) so `list_specs` is readable even when one file is
broken — the same degradation rule as everywhere else.

## Decision 5 — the rebuild seam is two wrappers, not a core edit

FR5 says the rebuild payload gains `specs`. Every rebuild-returning path in the
product funnels through **`AgentCADService._rebuild`** (`update_part` and
`set_params` both end `return self._rebuild(...)`; `_ensure_built` calls it on a
miss; `set_solid_materials` reaches it the same way), and the browser's params
route calls `service.set_params` *directly* rather than through the registry —
so wrapping the tools would miss the UI, and adding the key inside `service.py`
would edit a core the extension-point contract forbids.

**Chosen:** the tool pack wraps two bound service methods at registration time,
the way `tools_versioning.install_write_guard` rewires `store.write_guard` and
`tools_proposals` wraps `branches.delete`.

```python
def install_rebuild_specs(service) -> None:
    """Attach the spec summary to every rebuild result and to get_part.

    _rebuild is the single funnel for every rebuild-returning path, and the
    browser's PATCH .../params calls service.set_params directly, so a tool
    wrapper would miss it. Idempotent: wrapping twice must not double-evaluate.
    """
```

- **`_rebuild`** — on a successful result, `result["specs"] = runner.tier1(proj,
  part_id, result)`; on a failed result (`{"ok": False, "error": …}`) the key is
  **absent**, because there is no geometry to assert over and a spec block
  beside a build failure would compete with `with_hint`'s "fix the script
  first" guidance. A part declaring no specs gets `"specs": null` — an explicit
  "none declared", distinguishable from "not evaluated".
- **`get_part`** — `detail["specs"]` beside `detail["metrics"]`, from the result
  cache, evaluating only if the cache is cold. This is what makes the inspector
  chips live: `main.js`'s `rebuild_finished` handler already calls
  `refreshPartDetail(ev.part)`, so the refetch carries fresh specs with **zero
  changes to `main.js`'s event switch** (Decision 9).

Both wrappers are strictly best-effort: any exception inside them is caught,
logged into the payload as `{"specs": {"status": "error", "error": {…}}}` and
never propagated. **A broken spec layer must never break a rebuild.**

**No new event in MVP.** A `specs_evaluated` WebSocket event was designed and
rejected: `rebuild_finished` already drives the only consumer through
`refreshPartDetail`, `EventBus.on_publish` is a single slot the service holds,
and an event nobody reads is surface to maintain. PRD-004 and the Phase-2 Specs
panel can add one when they have a consumer that a refetch cannot serve.
PRD divergence 4.

**The private-method risk is real and is pinned by tests.** `_rebuild` is
private; a future signature change would break the wrapper silently. Slice 4
therefore asserts (a) the wrapper is installed exactly once, (b) `_rebuild`'s
key set is unchanged apart from `specs`, and (c) a spec-less part's payload and
kernel-call count are identical to today's.

## Decision 6 — `clearance` is the one new geometry op, in a new pack

`BRepExtrema_DistShapeShape` appears nowhere in the repo today; `interference`
reports *intersection volume* and a non-touching pair is simply absent from
`pairs`, so nothing can currently answer "how far apart are these two?".

**Chosen:** a new pack `agentcad/kernel/handlers/specs.py` contributing three
methods — `spec_declare`, `spec_eval` and `clearance`. **Not** a new `kind` on
`analyze`: `analyze` takes one script, applies no world transform, and its
`register()` closure is built around a single shape; a two-placed-shapes
measurement is a different signature on a different pack, which is the
`handlers/diff.py` precedent verbatim, and it keeps `analysis.py` (a large
OCP module) untouched.

```
method: "clearance"
params: {"a": item, "b": item, "min_mm": float|null}
        # item = {name?, script, params?} | {name?, source: "<path>"},
        #        + position [x,y,z] and rotation_deg [rx,ry,rz]
result: {"distance_mm": 0.42, "point_a": [x,y,z], "point_b": [x,y,z],
         "ok": false,                     # only when min_mm was given
         "skipped_mesh": ["b"]}           # present only when non-empty
```

Rules, each one a trap the repo has already paid for or a new one this handler
introduces:

- **Items are resolved the way `handle_interference` resolves them**, including
  the `analysis(p)` conservative envelope (`build_shape_ns` + `ns["analysis"]`)
  and `toolbox["place"]` for the world transform. Using the envelope makes the
  measured distance an **under**-estimate of the true clearance — conservative
  in the safe direction, and consistent with the interference check the same
  assembly is judged by. A part whose envelope is a superset can only report
  *less* room than it has.
- **`skipped_mesh` is inherited from interference, deliberately and
  provisionally.** An STL reference is one welded mesh Face; `BRepExtrema` is a
  distance query, not a boolean, so it very likely does *not* segfault the way
  `&` does — but "likely" is not evidence, and the standing rule is that mesh
  operands are excluded. v1 skips a mesh side with `reason: "mesh_only"`;
  Slice 2 measures it on `examples/construction`'s `imports/11.stl` and records
  the result, and admitting mesh sides is a follow-up with that measurement
  behind it, not a guess.
- **Zero distance is the interference case.** Touching or overlapping solids
  return `distance_mm == 0.0`; `check_clearance(min_mm=0.5)` fails and the
  message says so. The check does not try to also be `check_interference_free`.
- **Failures are structured, not fatal.** The whole measurement runs in one
  `try/except Exception` re-raised as
  `WorkerError(ERROR_KERNEL, "clearance unavailable: <reason>", {"stage":
  "distance", "a": …, "b": …})`; the runner catches `KernelError` and records
  `status: "error"` with the payload. The Error Doctor enriches
  `details.hint` for free (`worker._diagnose` runs on every error leaving
  `_dispatch`).
- **`SetMultiThread(True)`** is set; `SetDeflection` is left at the exact
  default in v1 — an approximate mode is the Phase-2 speed knob, and an
  approximate clearance reported as a measurement would be dishonest.
- **Cost is a measured risk, not an assumption.** The per-request timeout
  applies (`timeout_s=300.0`, `affinity=project`); Slice 2 records the timing
  for a rocketry pair and an `examples/engine` pair in its changelog. The PRD
  asks for exactly this.
- The pack registers unconditionally; `spec_declare`, `spec_eval` and
  `clearance` collide with none of the six builtins and none of the eleven
  existing pack methods (`_load_handler_packs` loses a colliding name with only
  a stderr warning, so the check matters).

`_min_wall` is **imported from the sibling pack**
(`from .analysis import _min_wall`) rather than re-implemented or moved. Two
implementations of wall thickness is the worst outcome available; the import is
inside `agentcad/kernel/`, where OCP is allowed, and `analysis.py` is not
edited.

## Decision 7 — `evaluate_specs` and the proposal gate: fail-closed

### The service seam (FR11)

```python
# agentcad/core/specs.py
def evaluate_specs(self, project: str, ref: str | None = None) -> dict:
    """Gate-shaped status for any ref. ref=None means the caller's branch."""
```

Returns

```json
{"available": true, "status": "red", "ref": "nozzle-thinner",
 "head": "9f31c0…", "checked_at": "2026-08-10T09:12:04Z",
 "summary": {"passed": 7, "failed": 1, "skipped": 1, "errors": 0, "total": 9},
 "failures": [ <check records> ], "skips": [ <check records> ],
 "errors": [ <check records> ], "reason": null}
```

A named `ref` is resolved with `history.resolve_branch` (a tag must never answer
for a branch — PRD-001 X1) and evaluated under
`branches.pinned(proj, branches.tree_of(proj, branch))` — PRD-002's exact
mechanism, which means the mesh cache, kernel pool, mates resolver and error
shapes are reused verbatim and every unchanged part is a disk-cache hit. The
head is read **before and after** the evaluation; a head that moved makes the
result `pending` rather than a verdict labelled with a commit it did not
measure (the packet's re-read rule, one layer up).

### The gate provider, and what blocks

`tools_specs.register` appends one provider to PRD-002's list:

```python
def specs(project: str, proposal: dict) -> dict | None:
    """Named `specs` so it replaces PRD-002's placeholder, and so that even
    the manager's own except-branch — which names the gate after the provider
    function — produces a `specs` gate rather than a duplicate."""
```

It catches everything internally and always returns a `{"name": "specs", …}`
dict, because `ProposalManager.gates`'s fallback would otherwise leave the
`specs` placeholder at `skipped` *and* append a second gate.

**The gate is evaluated against the proposal's SOURCE branch, not a merge
preview.** A merge preview would need a staged merge tree that does not exist
before the merge, and "will the merged result be green" at project scale is
PRD-004's job. The gate answers the question a reviewer actually asks — *is the
proposed state green?* — and says so in its summary. `proposal_merge` already
evaluates gates inside `_holding_source` and records
`merge.gates_source_head`, so the verdict is pinned to a head that cannot move
under it; the provider records the same head in `details.source_head`, and the
head-mismatch audit entry PRD-002 already writes covers the rest.

**Blocking semantics — PRD-003 owns these, and they are fail-closed:**

| state | when |
|---|---|
| `skipped` | the source ref declares no specs at all |
| `pass` | every declared check was evaluated and none failed or errored (skips are allowed and are named in the summary) |
| `fail` | any check failed **or** errored, **or** any declared check could not be evaluated — a kernel error, a source branch whose parts do not build, or an evaluation that exceeded the gate budget |
| `pending` | the source head moved during evaluation — the one condition a retry resolves |

PRD-002's rule is that any `fail` blocks the merge and `allow_invalid` cannot
waive a provider gate (it means "override the *kernel's* verdict on geometry"
and must not come to mean two things). So a red spec is a hard block, recorded
in the audit log with the rest of the gates, and the only way past it is to fix
the geometry or the spec — both of which are commits on the source branch that
a reviewer can see.

**Why fail-closed on *unevaluated*.** A declared-but-unmeasured spec is not
evidence of green; treating it as green would let a proposal merge by simply
never running. This is the deliberate divergence from PRD-002's default
(a provider outage degrades to `pending`), and it is the divergence PRD-002's
as-built note explicitly reserved for this PRD. The cost of being wrong in the
other direction is a merge that violates stated intent, which is the entire
problem this PRD exists to solve.

**Cost control.** The provider is called on *every* `proposal_get` — PRD-002
caches nothing. Three mechanisms keep that cheap, in order:

1. **The disk result cache.** Every part the source branch did not change has
   the same cache key on both sides (`.cache/` is canonical and shared), so its
   tier-1 result is already on disk.
2. **A per-runner memo** keyed by `(project, source_head, declaration_hash)`,
   bounded LRU, invalidated by definition when the head moves.
3. **A wall-clock budget**, `SPEC_GATE_BUDGET_S = 30.0`. On exhaustion the gate
   is `fail` with `details.reason = "budget_exceeded"` and a summary naming
   `run_specs` — fail-closed, never a silent green, and a `run_specs` call
   populates the caches so the retry is fast.

**One cheap thing the gate does that nothing else would.** The provider adds
`details.specs_py_changed` — computed with a single
`history._run("diff", "--name-only", target_head, source_head, "--",
"specs.py")` — so the Checks tab says *"this proposal changes specs.py"*.
Without it, a proposal that **weakens a spec** is invisible to review:
PRD-002's packet builds part rows only for `parts/*.py`, so a changed root
`specs.py` gets no row, and `merge._validate` only revalidates changed parts,
so it triggers no validation either. A full `specs` section in the packet is a
`packet.py` change and therefore out of scope here (Risks); the flag costs one
git call and closes the hole that matters.

## Decision 8 — writing project specs needs a tool

Part specs are written by `update_part_script`, which already exists. `specs.py`
has no writer at all: there is no generic file-write tool, and FR2's "a project
may hold `specs.py`" is unreachable for an agent without one. The PRD's agent
surface lists only `run_specs` and `list_specs`; two more are unavoidable
(PRD divergence 5):

- **`set_project_specs {project, script}`** — writes `specs.py` atomically under
  `store.write_guard` (turn locking; the store's guard only fires for
  `write_script`/`save_manifest`/`imports_dir`, so this pack calls it
  explicitly), publishes `project_changed` (which is what snapshots it into git
  history and therefore onto the current branch — the "a mutating pack needs no
  per-call hook" seam), and **returns post-state**: the declarations, or the
  declaration error. An empty string deletes the file.
- **`get_project_specs {project}`** — the text plus its declarations.

`set_project_specs` writes unconditionally and reports afterwards, matching
`update_part_script`: you must be able to save a broken file in order to fix it,
and a mutating operation returns post-state, never a bare OK.

## Decision 9 — UI (MVP): a chip strip in the Parameters pane

MVP is FR13's first half only — **per-part spec chips in the inspector, live on
rebuild.** The requirement-grouped project Specs panel and the viewport
thin-point marker are Phase 2 and must not creep in.

- **Where.** `#pane-params`, immediately after `#param-warnings`. The PRD calls
  a failing spec "the warnings tier" and that is literally where the warnings
  tier lives; it is also the tab an engineer is on while dragging the slider
  that breaks the budget. `renderSpecs(part)` is appended to `render()`'s
  unconditional tail beside `renderWarnings(part)`, with an
  `appendSpecsHost()` mirroring `appendWarningsHost()` so the host is
  re-created with the controls and never accumulates.
- **What.** One `.spec-chip` per check: `span("spec-chip spec-" + status,
  name)`, with `title` carrying `measured vs limit`, the requirement and the
  message. Built with `createElement` + `textContent` — names, requirements and
  messages are all script-controlled strings, so the `row()`/`arow()`
  template-literal builders elsewhere in `inspector.js` are the wrong
  precedent; `proposals.js`'s `gateChip(state)` atom is the right one, and
  `.spec-chip` copies `.gate-chip`'s recipe exactly (mono 10 px, `border-radius:
  9px`, `padding: 2px 7px`, colour by state). **No new CSS token** — pass is
  `--ok`, fail is `--err-text` on `--err-soft` with `--err-ring`, error is the
  same in `--err`, skip is `--dim` on the default hairline. There is no
  `--ok-soft`/`--ok-ring` pair and none is added; `.gate-pass` already solves
  that by using the border colour alone.
- **Empty and reference states.** `part.specs === null` (nothing declared)
  renders nothing at all — no empty header, no "no specs" note. A reference part
  goes through `buildReferencePane` and declares nothing, so it renders nothing
  by the same rule.
- **Live update, with no new event and no new state key.** `state.part.specs`
  rides the existing `state.part`; `inspector.render` is already subscribed via
  `onKeys(["part"], render)`; `main.js`'s `rebuild_finished` case already calls
  `refreshPartDetail(ev.part)`, and `get_part` now carries `specs` (Decision 5).
  **`main.js`, `state.js` and `api.js` need no change for the chips**; `api.js`
  gains the `runSpecs`/`listSpecs`/`setProjectSpecs` arrows only because the
  Phase-2 panel and manual testing want them, following the one-line-arrow-over-
  `enc()` convention.
- **`index.html` is untouched.** The chips live inside a `.pane` that JS owns.
  The boot-time `.menu-wrap` snapshot constraint does not apply because nothing
  new is a menu.

---

## Surfaces

### Tools (`agentcad/core/tools_specs.py`)

**Registration and load order.** `tools._load_tool_packs` walks
`pkgutil.iter_modules` alphabetically, so `tools_specs` is imported **after**
`tools_proposals` (so `service.gate_providers` exists — append to it here) and
**before** `tools_stackup` and `tools_versioning` (so the `tolerance_stackup`
tool and `service.branches` / `service.merges` do **not** exist yet). Therefore:

```python
def register(registry, service) -> None:
    service.specs = SpecRunner(service)          # always — specs need no git
    install_rebuild_specs(service)               # idempotent method wrappers
    providers = getattr(service, "gate_providers", None)
    if providers is not None:                    # absent when git is missing
        providers.append(service.specs.gate_provider())
```

`SpecRunner` reaches `service.branches` and the stack-up math **inside** each
call, never in `__init__`. `check_stackup` calls the extracted
`tools_stackup.compute_stackup(service, …)` directly rather than
`registry.call("tolerance_stackup", …)` — the tool is not registered yet at
this point, and a check should not depend on a tool's registration order.

Unlike proposals, **the pack does not self-disable without git**: specs are a
property of the working tree, `run_specs`/`list_specs` work on a project with no
history at all, and only the `ref=` argument and the gate need branches. A
`ref` passed on a project without git is a `validation_error` naming git — the
same shape `tools_versioning` uses.

| Tool | Schema | Returns |
|---|---|---|
| `run_specs` | `{project*, part_id?, ref?}` | the full report (Decision 4) |
| `list_specs` | `{project*, part_id?}` | declarations, no build |
| `set_project_specs` | `{project*, script*}` | `{path, declared, specs, error?}` |
| `get_project_specs` | `{project*}` | `{path, script, specs, error?}` |

Changed tools (through the `_rebuild` wrapper, not through their own code):
`update_part_script`, `set_params`, `set_solid_materials` gain `specs` in their
post-state; `get_part` gains `specs` beside `metrics`.

Descriptions state, inline: that a failing spec never fails a rebuild; that
skips are data with a `reason` and a `hint`; that `error` means the check broke
and is distinct from `fail`; that a rebuild evaluates the shape tier only and
`run_specs` evaluates everything; and that a red `specs` gate blocks a proposal
merge and `allow_invalid` does not waive it.

### Routes (`agentcad/server/routes_specs.py`)

```
GET    /api/projects/{proj}/specs                        ?part_id=      -> list_specs
POST   /api/projects/{proj}/specs/run   {part_id?, ref?}                -> run_specs
GET    /api/projects/{proj}/specs/file                                  -> get_project_specs
PUT    /api/projects/{proj}/specs/file  {script}                        -> set_project_specs
```

All are `registry.call` passthroughs reusing `routes_proposals`'s
`_RAISE`/`_result`/`_body_keys`/`_json` helpers verbatim — **the
`routes_proposals` form of `_json`, which reads the request bytes** rather than
trusting `content-length` (a chunked body has none, and the header-trusting
variant turns a body into "no arguments at all"). Body keys are whitelisted
explicitly; never `**body`. `_BODY_ERRORS` is empty here — this pack has no
error type that is a legitimate HTTP 200 body.

### Events

None (Decision 5). `set_project_specs` publishes the existing
`project_changed`, which is what snapshots it.

### Error shapes

```json
{"error": {"type": "notfound_error",
  "message": "part 'nozzel' not found in project 'rocketry'", "details": {}}}

{"error": {"type": "validation_error",
  "message": "ref 'shop-rev-a' is a tag, not a branch",
  "details": {"ref": "shop-rev-a"}}}

{"error": {"type": "validation_error",
  "message": "specs are versioned by git, which is not available",
  "details": {"ref": "feat"}}}
```

- Unknown project / part → `notfound_error` (404).
- Unknown or non-branch `ref`, `ref` without git → `validation_error` (422).
- **Everything about a check is payload, never an error**: a failing check, a
  broken predicate, a missing `[fem]` extra, a `specs.py` that will not
  execute, an unknown instance id in a project spec — all of it arrives as
  records with `status` and `reason`, or as `errors[]` entries in `list_specs`.
  There is **no new error type** (the PRD's own rule), and `run_specs` returns
  `ok`-shaped data for a project that is entirely red.

---

## Data flow — the AC1 walk

1. The rocketry example ships `parts/nozzle.py` with
   `check_wall(min_mm=2.5, requirement="ENG-014")` and
   `check_mass(max_g=…, requirement="SYS-042")`, and a root `specs.py` with
   `check_interference_free()` and the flange bolt-circle
   `check_clearance(…, min_mm=…, requirement="INT-003")`. All green as shipped.
2. `run_specs {project: "rocketry"}`. The runner AST-scans three scripts: only
   `nozzle` binds `SPECS`. Its `.cache/<key>.specs.json` exists from the last
   rebuild, so tier 1 is a disk read — **zero kernel calls**. `specs.py` exists,
   so `spec_declare` runs once (cached by script sha), then tier 2: one
   `interference` request and one `clearance` request. Report: `status: green`,
   `requirements` covering `ENG-014`, `SYS-042`, `INT-003`.
3. An agent calls `set_params {part_id: "nozzle", values: {"wall": 2.0}}`. The
   service writes the override, publishes `project_changed`, rebuilds. The
   wrapper sees a new cache key, no `.specs.json` beside it, and issues one
   `spec_eval` with `affinity="nozzle"` — a shape-LRU hit on the worker that
   just built it. `check_wall` reports `min_thickness_mm ≈ 2.0` with the ray
   origin as `location`.
4. The tool returns `{"ok": true, "metrics": …, "warnings": [], "lods": [],
   "cache_key": …, "specs": {"status": "red", "summary": {"failed": 1, …},
   "checks": [ … ]}}`. **The geometry landed** (AC2): `ok` is `true`, the mesh
   is written, the viewport updates, and the failure is signal.
5. The browser's `rebuild_finished` handler calls `refreshPartDetail("nozzle")`;
   `get_part` carries `specs`; `inspector.render` paints a red `wall_min` chip
   whose `title` reads `1.98 mm vs min 2.5 mm · ENG-014`.
6. `run_specs {project: "rocketry"}` is now red, naming `check_wall` with
   measured, limit and the thin point (AC1).
7. The agent opens a proposal from its branch. `proposal_get` evaluates gates;
   the `specs` provider runs `evaluate_specs(proj, ref="nozzle-thinner")` under
   `pinned(source_tree)` — every part but `nozzle` is a shared-cache hit —
   and returns `{"name": "specs", "state": "fail", "summary": "1 of 9 checks
   failing (ENG-014)", "details": {…, "source_head": "…",
   "specs_py_changed": false}}`.
8. `proposal_merge` re-evaluates the same gate inside `_holding_source`, finds
   `fail`, and raises `conflict_error` naming `specs` with `details.gates`
   before anything is merged. `allow_invalid: true` does not waive it. The
   agent raises `wall` back to 2.6, the gate goes green, the merge lands, and
   the audit log carries the whole sequence.

---

## Testing strategy

**Shared harness.** Kernel-touching tests use the session-scoped `kernel`
fixture. Ref/branch tests copy `tests/test_proposals.py`'s `_GIT` triple
(`integration` + `portability` + `skipif(shutil.which("git") is None)`), the
autouse `_reset_context` that rebinds `locks.client_id_var` and
`pinned_tree_var`, and the real `AgentCADService(...)` + `build_registry(service)`
`stack` fixture with an `assert getattr(service, "specs", None) is not None`
seam check. HTTP tests use
`create_app(..., extra_allowed_hosts={"testserver"})` and
`TestClient(app, base_url="http://127.0.0.1")`.

- **`tests/test_specs_toolkit.py`** — pure, no kernel, no git. Every constructor
  produces the documented dict; `requirement` and `name` ride through; bad
  arguments raise at *construction* (this is FR1's mechanism); every
  declaration except `check_that` is `json.dumps`-able; `from agentcad.toolkit
  import specs` works and `toolkit/__init__.__all__` carries it; the module
  imports with `build123d` and `OCP` absent from `sys.modules` (assert by
  import, not by inspection).
- **`tests/test_specs_kernel.py`** — session `kernel`, `slow`. `spec_declare`
  returns declarations and **issues no build** (counting monkeypatch);
  `spec_eval` evaluates a metric check, a wall check with a `location`, and a
  predicate; a predicate that raises comes back `status: "error"` with a
  traceback and the worker is still alive (AC5); `SPECS = "hello"` is a
  structural error, not a crash; `clearance` on two known-offset boxes matches
  the analytic gap to 1e-6, reports both witness points, returns `0.0` for
  overlapping solids, and skips an STL side as `mesh_only`; a colliding handler
  name would be refused (assert `spec_eval`/`spec_declare`/`clearance` are not
  in `worker.HANDLERS` before the pack loads).
- **`tests/test_specs.py`** — the runner. `declares_specs` on a dozen scripts
  (top-level, conditional, in a comment, in a string, unparseable); the AST scan
  costs **zero** kernel calls for a spec-less part (AC9's counting assertion);
  the `.specs.json` sidecar is written, read back and invalidated by a param
  change (FR10); the three tiers partition correctly and a rebuild defers tiers
  2–3 with `reason: "deferred"`; requirement grouping (AC6); an unknown instance
  id in `specs.py` is `status: "error"` naming the id; `check_clearance` reports
  the measured minimum for a too-close pair and `check_interference_free` names
  the offending pair (AC4); `check_fem_static` skips with
  `reason: "fem_extra_missing"` and a hint, and evaluates under
  `pytest.importorskip("skfem"/"gmsh"/"meshio")` — the
  `tests/test_analysis.py::_require_fem` pattern, so the suite is green
  **without** the extra (AC3).
- **`tests/test_specs_api.py`** — the three `test_versioning_api.py` sections.
  Every tool registered with `input_schema["type"] == "object"`, `project` in
  properties *and* required, a non-empty description; a **description-contract**
  test asserting the descriptions state that a failing spec never fails a
  rebuild and that a red gate blocks a merge; `invalid_arguments` for a missing,
  mistyped and unknown argument; each route returning the tool payload verbatim
  with unknown body keys ignored and `null` not forwarded; the wrapper tests
  (installed once, `_rebuild`'s key set unchanged, a spec-less part byte-
  identical to today); `set_project_specs` writing under the turn lock and
  refusing under someone else's.
- **`tests/test_specs_gate.py`** — `_GIT`. `evaluate_specs` is green for a
  tagged good state and red for a branch with a broken budget (AC7); the gate
  replaces PRD-002's placeholder rather than duplicating it; a provider that
  raises internally still yields a `specs` gate; `fail` blocks `proposal_merge`
  with a `conflict_error` naming the gate, and `allow_invalid: true` does not
  waive it; an unevaluated declared spec is `fail`, not `pass`; a moved source
  head is `pending`; `specs_py_changed` is true when the source branch touched
  `specs.py`.

  **One existing test needs a one-line, intent-preserving edit and no other
  does.** `tests/test_proposals.py::test_specs_and_checks_are_skipped_with_no_providers`
  opens with `assert getattr(service, "gate_providers", None) in (None, [])`,
  which stops being true the moment this pack appends its provider. Its *name*
  says what it means, so it gains `service.gate_providers = []` before that
  assertion; everything after it still holds unchanged, because that fixture's
  demo project declares no specs and the gate is therefore `skipped` for the
  real reason instead of the placeholder reason. The two sibling tests
  (`test_a_gate_provider_can_add_or_replace_a_gate`,
  `test_a_broken_gate_provider_degrades_to_pending`) already **assign**
  `service.gate_providers`, so they are unaffected. Any *other* diff to an
  existing test file means the gate is wrong, not the test.
- **`tests/test_prd003_acceptance.py`** — one named test per criterion
  (`test_ac1_…` … `test_ac9_…`) mirroring `tests/test_prd002_acceptance.py`,
  on a **copy** of `examples/rocketry`
  (`shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".cache",
  "exports"))`), reusing its `_copy_rocketry` and `_thin_the_nozzle` helpers.
  AC8's browser half follows the PRD-001/PRD-002 precedent: the test asserts the
  recorded browser session is on the changelog record rather than re-driving a
  browser.
- **`tests/test_examples.py` must pass unedited** with the new rocketry specs.
  Its three contracts constrain them: every part builds valid at defaults, every
  part builds at every param extreme (`set_params` must still return
  `ok: True` — which FR5/AC2 guarantee, since a failing spec never fails a
  rebuild), and `check_interference(name)["pairs"] == []`.
- **Browser verification** (AC8, definition of done): open rocketry, watch a
  green `wall_min` chip go red as the wall slider drops below 2.5 mm and back to
  green, in both themes. Zero console errors, screenshots in the changelog.
- **Markers.** Kernel-heavy and example-driven cases are `slow`; anything that
  shells git carries `integration` + `portability` + the git skipif. Nothing
  here is `exhaustive`.

---

## Risks and open questions

| Risk | Mitigation / what the implementer must verify empirically |
|---|---|
| **`clearance` cost.** `BRepExtrema_DistShapeShape` is `O(faces²)` in the worst case, and the PRD names this explicitly. | `SetMultiThread(True)`, the conservative `analysis(p)` envelope (which is *cheaper* as well as safer), `timeout_s=300.0`, and a structured `error` record on timeout. **Measure a rocketry pair and an `examples/engine` pair in Slice 2 and record both numbers in the changelog** — a bbox pre-filter is a sound lower bound (`dist(A,B) ≥ dist(boxA,boxB)`) and is the first optimisation, but only with a measurement behind it, and never at the cost of reporting a real `measured`. |
| **`check_wall` on every rebuild.** 8×8 rays per face per solid; a 200-face part is ~12 800 `Perform` calls, inside the interactive edit loop. | It is one request, `affinity=part_id` onto the warm worker, and cached by cache key so it is paid once per `(script, params)`. **Time it on the rocketry nozzle and on an `examples/surfacing` part in Slice 3 and record it.** If it is unacceptable, the lever is a declared `grid` (already a constructor option) or moving `wall` to tier 2 — *not* dropping the check, which is the PRD's headline example. |
| **Wall thickness is a ray cast, not a medial-axis measurement.** It measures along the inward face normal from a UV-grid sample, so it over-estimates on non-parallel walls and can miss a thin feature finer than the sample spacing on a large face. | This is the shipped `analyze_part(kind="wall")` behaviour and the check must not silently promise more. Document it in `docs/part-authoring.md` and in the constructor docstring; `grid` is the knob. A true medial-axis check is a new measurement and therefore a new PRD, per the vocabulary-sprawl rule. |
| **A proposal that weakens a spec is invisible in the review packet.** `packet.py` builds part rows only from `parts/*.py`, and `merge._validate` only revalidates changed parts, so a changed root `specs.py` produces no diff row and no validation. | The gate carries `details.specs_py_changed` (one `git diff --name-only`), so the Checks tab says it. A full `specs` section in the packet is a `packet.py` change and is **out of scope**; it is the natural PRD-002 Phase-2/PRD-008 follow-up and must be named in the changelog as a known gap. |
| **The `_rebuild` wrapper wraps a private method.** A signature change would break it silently. | Idempotent installation, a test asserting it is installed exactly once, a test asserting `_rebuild`'s key set is unchanged apart from `specs`, and a test asserting a spec-less part's payload and kernel-call count are identical to today's. The alternative (editing `service.py`) is forbidden; the alternative-alternative (`bus.on_publish`) is a single slot the service holds. |
| **The gate runs on every `proposal_get`.** PRD-002 caches no gate. | The shared canonical `.cache/` makes unchanged parts free, a per-head memo makes repeated reads free, and `SPEC_GATE_BUDGET_S = 30` bounds the worst case — fail-closed with an actionable summary, never a silent green. **Measure a cold and a warm `proposal_get` on rocketry with specs and record both.** If it is still too slow, the next lever is storing the report beside `packet.json` at packet-build time — which is a `packet.py` change and therefore a separate slice. |
| **Fail-closed will surprise someone.** A proposal whose specs were never evaluated cannot merge. | It is the deliberate divergence PRD-002 reserved for this PRD, it is stated in the gate summary with the exact fix (`run_specs`), and the alternative — merging on unmeasured intent — defeats the feature. Revisit only with a user report, and then as a policy field in `policy.json` (`specs_required`), not as a silent default change. |
| **Predicates are arbitrary code.** | The trust model is unchanged: part scripts already execute in the worker, which on macOS is confined by the deny-by-default `sandbox-exec` profile. What changes is that a *project-level* file now executes too — under exactly the same confinement. Document in `docs/architecture.md`'s trust-model section that specs run in the kernel worker and **never** in the server process. |
| **Instance-id coupling.** A project spec names assembly instances; a rename breaks it. | `status: "error"` naming the missing id — honest, not silent, and it makes the gate red, which is the correct answer to "the spec no longer describes this assembly". A rename-refactor tool is a later PRD. |
| **`SPECS` inside a script is in the cache key.** Editing a spec mints a new key and forces a full kernel rebuild of a part whose geometry did not change. | Correct but wasteful. Accepted for v1 — the cost is one build, the alternative is a second content signature that splits geometry identity from spec identity, and a wrong split there would serve stale meshes. Note it in `AGENTS.md`. |
| **Vocabulary sprawl.** | The PRD's rule is kept literally: ten constructors, nine of which reuse an existing measurement, and one sanctioned new op. PRD-021 is the expansion path. Any eleventh constructor in this feature's implementation is a design bug. |

### Naming traps (all three are live collisions in the tree today)

- **`service._spec_cache` already means the PARAMS spec cache** (`service.py`).
  The design-spec seam is `service.specs`; the runner's caches are named
  `_declaration_cache` / `_result_cache`.
- **`inspector.js`'s `renderedSpecJson` already means the PARAMS spec JSON.**
  The chips keep no render cache; they re-derive from `state.part.specs`.
- **`packet.py`'s `params_diff` rows already carry `"source": "spec"`** meaning
  "the script's PARAMS declaration". Nothing in this feature may reuse that key.

---

## PRD divergences to fold back

1. **A structurally malformed `SPECS` is reported as data, not as a failed
   rebuild.** FR1's mechanism is honoured for the common case for free —
   constructors validate eagerly, so a bad argument raises during module exec
   and is already a `script_error` with `details.line`, exactly like `PARAMS`.
   The residue (`SPECS = "hello"`, a non-constructor dict) is reported as
   `specs.error` on an otherwise-successful rebuild, because failing a build on
   a broken *assertion* contradicts FR5 and would take away the geometry you
   need in order to fix it.
2. **`metrics["bbox"]` has `min` and `max`, not `size`.** The PRD's `check_that`
   example uses `metrics["bbox"]["size"][2]`; the kernel's metrics dict has no
   `size`, and a second metrics shape is not worth the ergonomics. Use
   `check_bbox(within_mm=…)`, or `max[2] - min[2]`.
3. **A check record's `limit` is a dict**, and the record carries `id`, `scope`,
   `part`, `unit` and `details` beyond FR6's list. Two-sided checks have two
   limits, and a flat list of records needs a join key.
4. **No new event.** FR13's "live on rebuild events" is met by the existing
   `rebuild_finished` → `refreshPartDetail` → `get_part` path, which now carries
   `specs`; a `specs_evaluated` event with no consumer is surface without value.
5. **Two additive tools beyond the PRD's list:** `set_project_specs` and
   `get_project_specs`. FR2 gives `specs.py` no writer, and the agent path
   ("the agent writes the spec first") is unreachable without one.
6. **Spec evaluation is tiered, and a rebuild runs the shape tier only.** FR5
   says part-level specs evaluate on every rebuild, and they do; the
   assembly-scope and FEM tiers are deferred to `run_specs`/`evaluate_specs`
   and reported at rebuild time as `skip` with `reason: "deferred"` — because a
   600 s solve inside a slider drag is not "without friction".
7. **The proposal gate is fail-closed, and evaluates the SOURCE branch.** A
   declared check that was not evaluated is `fail`, not `pending` — the
   divergence PRD-002's as-built note reserved for this PRD. The gate answers
   "is the proposed state green", not "will the merge be green" (that is
   PRD-004).
8. **Project-scope `check_that` is not supported in v1** — there is no single
   built shape to hand a predicate; it reports `status: "error"`,
   `reason: "unsupported_scope"`.
9. **`clearance` is a method on a new pack, not a kind on `analyze`.** The PRD
   says "joins the analysis pack"; `analyze` takes one script and applies no
   world transform, so a two-placed-shapes measurement is a different
   signature — the `handlers/diff.py` precedent.
10. **The MVP UI is the chip strip only.** The requirement-grouped project Specs
    panel and the viewport thin-point marker stay Phase 2, as the PRD's own MVP
    section says; FR13's report-level requirement grouping ships in `run_specs`.
11. **The pack does not self-disable without git.** Unlike proposals and
    versioning, specs are a property of the working tree; only `ref=` and the
    gate need branches, and those raise a `validation_error` naming git.
