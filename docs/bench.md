# AgentCAD-Bench — `agentcad bench`

A public, reproducible benchmark for **agentic CAD**: 25 tasks, driven through
the product's own tool surface, scored by the geometry kernel. Every number is
a measurement — a boolean intersection, a spec run, a bounding box — and there
is **no LLM judging anywhere** in the pipeline.

```bash
uv run agentcad bench score benchmarks/examples/submission-mfd-001 \
    --task model_from_drawing/mfd_001_spacer_plate
```

```
model_from_drawing/mfd_001_spacer_plate — model_from_drawing · task set bench-v1 v1 · harness 1 · agentcad 0.1.0
  subscore      status             value  weight   contrib
  built         ok                1.0000    0.15    0.1500
  geometry      ok                0.9919    0.50    0.4959
  interference  not_applicable    0.0000    0.00         —
  metrics       ok                1.0000    0.15    0.1500
  specs         ok                1.0000    0.10    0.1000
  valid         ok                1.0000    0.10    0.1000
bench score: model_from_drawing/mfd_001_spacer_plate — 0.9959 over 5 subscore(s)
```

The same five commands serve every audience: `bench run` drives an agent over a
task set, `bench score` measures one submission, `bench prompt` prints the exact
prompt an agent is handed, `bench report` aggregates a results directory and
gates it against a baseline, and `bench publish` renders the leaderboard. There
is **no bench tool, route or event** — see
[Why there is no `bench_*` tool](#why-there-is-no-bench_-tool).

**Sibling document:** [`docs/geometry-ci.md`](geometry-ci.md). `agentcad check`
certifies a *project*; `agentcad bench` scores an *agent*. Both drive the same
headless in-process service over one warm kernel, with no server and no port.

---

## Two rules to read the whole feature by

1. **A candidate that cannot be measured measures zero.** `error` means *the
   harness* failed to measure — a dead worker, a blown budget. `not_applicable`
   is declared by `task.json` (a zero weight) and is **never** decided by a
   run. A submission that is absent, unbuildable, mesh-only or simply wrong is
   *measured*, and it measures **zero**. Excluded subscores are renormalised
   away (below), so the opposite reading would mean a candidate improves its
   total by destroying the evidence: delete the part, break the build, hand
   back an STL.
2. **The reference script is the solution; the reference STEP is the datum.**
   Both ship. Scoring compares a candidate against the checked-in STEP, never
   against a freshly regenerated one — otherwise every published number would
   silently become a function of the build123d pin. A slow test re-exports each
   datum and compares it to the bytes on disk (IoU ≥ 0.9999, volume within 1e-6
   relative, every bbox bound within 1e-3 mm), so drift is a red test rather
   than a quiet re-scoring of history.

---

## What is measured, and what is not

**Measured.** Six subscores per task, each in `[0, 1]`, each weighted by
`task.json`: did the target parts *build*, is the resulting B-rep *valid*, do
the task's declared *specs* pass, how much of the candidate is the reference
(*geometry*, a real boolean IoU), does the assembly *interfere*, and do the
part's *metrics* (volume, mass, bbox, counts) land inside the task's windows.

**Not measured.** Style, script readability, tool efficiency, how many turns it
took, and whether a human would like the design. No model reads a submission
and forms an opinion about it. The transcript is published so a person can, but
the *score* is arithmetic over kernel output.

---

## The commands

```
agentcad bench run     --report DIR [--tasks GLOB] [--set NAME] [--agent builtin]
                       [--model NAME] [--work-dir DIR] [--budget SECONDS]
                       [--quiet | --json]
agentcad bench score   SUBMISSION --task ID [--tasks-dir DIR] [--out DIR]
                       [--work-dir DIR] [--budget SECONDS] [--quiet | --json]
agentcad bench prompt  ID [--tasks-dir DIR] [--json]
agentcad bench report  RESULTS [--baseline PATH] [--epsilon F]
                       [--md PATH] [--json-out PATH] [--quiet | --json]
agentcad bench publish LEADERBOARD [-o PATH] [--title TEXT]
```

| Flag | |
|---|---|
| `--tasks` | A glob over **task ids** — `model_from_drawing/*`, `*/mfd_00*`. Composes (AND) with `--set`. |
| `--set` | Select by `task.json`'s `sets` membership. `core` is all 25; `fast` is one task per category, the CI subset. Naming neither selects `core`, so a bare `bench run` keeps meaning the same suite as the set grows. |
| `--agent` | Today only `builtin`, the shipped chat agent. An external agent does not use `bench run` at all — it hands in a project directory and you `bench score` it. |
| `--report` | The results directory to write. A previous run's `tasks/` is cleared first; a non-empty directory that does not look like a results directory is **refused untouched** (`--report ~/Documents` is a typo, never a delete command). |
| `--tasks-dir` | The task tree to load from (default: the shipped `benchmarks/tasks`). On `score` and `prompt`. |
| `--work-dir` | Where a run or a score materializes its throwaway cells, in unique subdirectories it creates and removes. A work dir that **is, holds or sits inside** the submission, the task tree, the results directory or the projects root is refused, exit 2. It is also the **only** path either command grants the confined worker a write into: the task bundle and the submission are read-only inputs, and the worker executes the candidate's own Python. |
| `--budget` | A wall-clock ceiling in seconds. On `run` it overrides `task.json`'s wall budget (never the tool-call ceiling — that is what keeps a task inside one engine turn). On `score` it is a deadline read before every kernel call. Must be finite and non-negative: a NaN deadline is never in the past, so it bounds nothing. |
| `--baseline` / `--epsilon` | Gate a report against `benchmarks/baseline.json`, tolerating a drop of `epsilon` (default 0.02) on the total and on each category. |
| `--md` / `--json-out` | Write the markdown summary (`$GITHUB_STEP_SUMMARY`, a PR comment) / the JSON report. |
| `--quiet` / `--json` | `--quiet` prints nothing; `--json` puts the document **alone** on stdout, so `agentcad bench score --json \| jq` works. Neither moves the exit code. (`prompt` has only `--json`: printing the prompt *is* the command.) |

**stdout is a contract, stderr is for humans** — the tables and the harness
errors go to stderr, the one-line verdict to stdout.

### Exit codes

| | `0` | `1` | `2` |
|---|---|---|---|
| `bench run` | every selected task ran and was scored | — | harness |
| `bench score` | a score was produced | — | harness |
| `bench prompt` | the prompt was printed | — | harness |
| `bench report` | no baseline, or the baseline is met | **a regression** | harness |
| `bench publish` | the page was written | **a row was rejected**, nothing written | harness |

`run` and `score` are deliberately never `1`: a low score or an over-budget task
is a **measurement**, and making it a failing exit would turn the runner and the
release gate into the same thing. FR11's gate is `bench report --baseline`, and
only there. `publish`'s `1` is the one exception, and it is not about a model —
a leaderboard is a claim about other people's work, so a row that does not
disclose everything refuses the **whole** board rather than being dropped from
it.

Exit `2` keeps `agentcad check`'s meaning exactly: *we could not produce a
verdict*. An unknown task, an unreadable results directory, a refused
`--work-dir`, an incomparable baseline — and the one scoring case,
[every subscore excluded](#the-total).

---

## The task bundle

A task lives at `benchmarks/tasks/<category>/<id>/`; the id used everywhere is
`"<category>/<id>"`. The five categories are `model_from_drawing`,
`modify_to_spec`, `fix_the_broken_part`, `assemble_and_clear` and
`optimize_under_constraints`, five tasks each.

```
benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/
  task.json                       # the rubric (schema-versioned)
  prompt.md                       # handed to the agent verbatim
  assets/drawing.svg              # optional; attached to the prompt as TEXT
  starter/                        # optional; a COMPLETE project directory
  reference/
      project/                    # a COMPLETE project directory — the solution
      steps/spacer_plate.step     # the IoU datum, exported from reference/project
      metrics.json                # the metric windows
  specs/
      project.py                  # optional: injected as <copy>/specs.py
      parts/spacer_plate.py       # optional: appended to <copy>/parts/spacer_plate.py
```

`starter/` and `reference/project/` are **ordinary AgentCAD project
directories** — no bespoke installer, no manifest synthesis. That is what makes
AC1 literally `bench score reference/project --task <id>` with no special case:
the reference is graded by the machinery it defines.

### `task.json`

```json
{
  "schema": 1,
  "id": "model_from_drawing/mfd_001_spacer_plate",
  "task_set": "bench-v1",
  "version": 1,
  "category": "model_from_drawing",
  "title": "Spacer plate from a three-view drawing",
  "sets": ["core", "fast"],
  "authored_against": "0.1.0",
  "source": {"kind": "authored"},
  "prompt": "prompt.md",
  "assets": ["assets/drawing.svg"],
  "starter": null,
  "target": {"project": "bench_mfd_001_spacer_plate", "parts": ["spacer_plate"]},
  "budgets": {"wall_s": 600, "turns": 24},
  "frame": {
    "align": "world",
    "rotations_deg": [],
    "datum": "the plate's bottom face lies on Z = 0 and its centre is at the origin"
  },
  "reference": {
    "project": "reference/project",
    "steps": {"spacer_plate": "reference/steps/spacer_plate.step"},
    "metrics": "reference/metrics.json"
  },
  "specs": {"project": null, "parts": {"spacer_plate": "specs/parts/spacer_plate.py"}},
  "weights": {
    "built": 0.15, "valid": 0.10, "specs": 0.10,
    "geometry": 0.50, "interference": 0.00, "metrics": 0.15
  }
}
```

The loader (`agentcad/bench/tasks.py`) validates the **whole** bundle before
anything spawns and reports *every* problem at once — fixing one defect per run
is a bad afternoon. It refuses, among others: a path that escapes the bundle, a
`.stl` reference, a rubric that calls `check_fem_static` (the `[fem]` extra is
not a bench dependency), `budgets.turns` greater than the chat engine's
`MAX_TOOL_CALLS_PER_TURN`, weights that do not sum to 1, more than 8 declared
rotations, and an empty metric-window list on a task that weights `metrics`.

**`assets` are text.** SVG, MD, TXT, JSON, CSV — attached to the prompt inline,
fenced, and named by their path relative to the bundle. There are no PNG assets
in v1: the chat surface accepts a `str` message and adding an image block is a
product change with its own review (see [Seams](#phase-2--3-seams)). The model
reads an SVG as markup, so a drawing needs no OCR and no vision model.

What a generated sheet *carries*, though, is narrower than a draughtsman's:
three orthographic views, the **overall extents** of each view and one hole
callout. It is not a fully dimensioned drawing, and a task whose prompt said
only "model the attached drawing" would be unanswerable. So **the prompt
carries the full dimension set in words** — every feature size, the material
and the datum — and the sheet is the shape, the proportions and the
corroborating overall dimensions. Read the two together, as the reviewer
checklist below requires.

### The rubric

The rubric is deliberately **separate from the reference geometry**, and a
reference part script carries **no `SPECS` of its own**. `specs/parts/<part>.py`
is appended to the candidate's script inside the scoring copy and **re-binds**
`SPECS` — the last module-level binding wins, so whatever the candidate declared
is discarded. Every constructor is imported under a `_bench_` alias, because the
candidate's own module namespace is in scope:

```python
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)

SPECS = [
    _bench_check_valid(name="valid", requirement="MFD-001"),
    _bench_check_wall(min_mm=3.0, grid=4, name="ligament", requirement="MFD-001"),
    _bench_check_bbox(within_mm=(80.2, 50.2, 6.2), name="envelope",
                      requirement="MFD-001"),
]
```

`specs/project.py` replaces `<copy>/specs.py` outright for assembly-scope
checks, and when a task ships none, that file is **deleted** from the copy. Only
rubric-owned rows count: `<part>:*` for the parts the injection touched, and
`project:*` only when the task ships a project rubric. A submission cannot
inflate its `specs` subscore by declaring checks it wrote itself.

### Metric windows

`reference/metrics.json` is a closed list of inclusive bands over keys the
kernel already reports — `volume_mm3`, `area_mm2`, `mass_g`, `n_solids`,
`n_faces`, `n_edges`, `bbox_{x,y,z}_mm`, `com_{x,y,z}_mm`:

```json
{"schema": 1, "windows": [
  {"name": "height", "part": "spacer_plate", "metric": "bbox_z_mm", "min": 5.95, "max": 6.05},
  {"name": "material", "part": "spacer_plate", "metric": "volume_mm3", "max": 23250.0}
]}
```

An absent bound stays absent: "no ceiling" and "an infinite ceiling" are
different claims and only the first one is honest.

---

## Scoring

`bench score` never touches the submission. It copies it into a throwaway cell,
injects the rubric into the **copy**, and opens a *muzzled* service over it —
`bus.on_publish = None`, `branch_resolver = None`, `write_guard = None`, the
same three nullings `agentcad check`'s ephemeral service makes. No history
commit, no branch sidecar, `.cache/` lands in the cell, and the cell is removed
in a `finally`.

| subscore | value |
|---|---|
| `built` | `passed / len(target.parts)`, where a part passes when the service builds it. A part missing from the manifest counts as failed, never as an error — and so does a build that **timed out** (`reason: "build_timeout"`) or one that **took the worker down** (`reason: "build_crash"`). Either becomes an `error` only when a `--budget` had already expired, which is our truncation. |
| `valid` | `valid_parts / len(target.parts)` over `metrics.is_valid`. A part that did not build is invalid. `check`'s imported-geometry escape is deliberately absent: a bench candidate that imports a mesh is measured, not forgiven. |
| `specs` | `passed / (passed + failed + errors)` over the rubric-owned rows of one `SpecRunner.run`. A `skip` leaves the denominator — *unless* the candidate induced it (`mesh_only`, `no_instances`), in which case it counts as a **fail** and is named under `detail.skipped_as_failed`. A machine-specific skip (a missing extra) is neither a pass nor a fail. **A zero denominator is `0.0` with `status: "ok"`, never a division and never an exclusion**: `reason: "no_rubric_attached"` when the task ships a rubric and none of it could be appended (the candidate deleted the part, or handed back a mesh reference), `reason: "nothing_measured"` when it attached and every row skipped or the part did not build. Both are the candidate's doing, and an exclusion would renormalise the weight onto whatever is left — the exploit rule 2 exists for. |
| `geometry` | The mean over the target parts of the kernel's IoU against that part's reference STEP. A part with no datum is not scored; a part that resolves mesh-only contributes `0.0` and is named. |
| `interference` | `clean_pairs / C(n, 2)` over the resolved assembly instances. `skipped_mesh` pairs count as **un-clean**: an unmeasurable pair is not a clean pair. Fewer than two instances, with a non-zero weight, is `0.0` — the task asked for an assembly and got none. |
| `metrics` | `satisfied / len(windows)`. A window whose part did not build is unsatisfied. |

### The IoU

One kernel call per part, into `agentcad/kernel/handlers/bench.py`:

* **Only the intersection is booleaned.** `union = volA + volB − inter` is
  arithmetic; `|` on multi-solid Compound operands would double the OCCT
  failure surface for a number already in hand.
* Both sides are **decomposed into solids** and AABB-prefiltered. The pairwise
  sum equals `volume(A & B)` only when each side's solids are mutually disjoint
  — true of a well-formed part, false of one that self-overlaps — so the total
  is clamped to `min(Σ, volA, volB)` and the ratio to `[0, 1]`. Both solid
  counts ride in the result so a reader can see when the clamp could have bitten.
* **A mesh side is never booleaned** (an STL is one welded face and OCCT
  segfaults on it): the call short-circuits to `status: "skipped_mesh"` with
  both volumes still reported.
* Alignment is applied to the **candidate** only, as
  `translate(anchor_ref) ∘ rotate(r) ∘ translate(−anchor_cand)`, in intrinsic
  XYZ Euler degrees. Modes are `world` (default), `com`, `bbox_center`.
  **Scale is never normalised** — a part of the wrong size is a wrong part.
  `frame.rotations_deg` is a task-declared finite list (≤ 8); the maximum IoU
  wins and ties keep the first, so the answer is deterministic.

Volumes come from the worker's `shape_volume` (a sum over `shape.solids()`),
never `.volume`: a boolean result is routinely a nested Compound and
`Compound.volume` reports only the first child subtree.

### The total

```
included = [s for s in subscores if s.status not in ("error", "not_applicable")]
W        = sum(s.weight for s in included)
total    = round(sum(s.value * s.weight for s in included) / W, 6)   if W > 0
```

The renormalised weights are published as `weights_effective`, so a reader can
reproduce the arithmetic without knowing the rule. If `W == 0` — every subscore
excluded — the total is `0.0`, a note says so and **`bench score` exits 2**: no
verdict was produced.

### `score.json`

```json
{
  "schema": 1, "agentcad": "0.1.0", "harness": 1,
  "task": "model_from_drawing/mfd_001_spacer_plate",
  "task_set": "bench-v1", "task_version": 1, "category": "model_from_drawing",
  "total": 0.995937,
  "weights_effective": {"built": 0.15, "geometry": 0.5, "metrics": 0.15,
                        "specs": 0.1, "valid": 0.1},
  "subscores": {
    "geometry": {"value": 0.991873, "weight": 0.5, "status": "ok",
                 "detail": {"parts": {"spacer_plate": {
                   "align": "world", "candidate_volume_mm3": 23096.506237,
                   "intersection_mm3": 23050.152241, "iou": 0.991873,
                   "reference_volume_mm3": 23192.654884,
                   "rotation_deg": [0.0, 0.0, 0.0], "union_mm3": 23239.00888}}}},
    "interference": {"value": 0.0, "weight": 0.0, "status": "not_applicable",
                     "detail": {"reason": "weight_zero"}}
  },
  "notes": []
}
```

**Determinism (FR6/AC3)**, all of it in one writer:

1. `json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"`,
   through the store's atomic write.
2. Every float is `round(x, 6)` **before** serialisation, recursively.
3. `allow_nan=False`: a NaN would serialise as a bare literal no strict parser
   accepts. A non-finite measurement is a `status: "error"` subscore instead.
4. Every list is sorted by a stated key.
5. **No timestamp, host, path, duration or client id anywhere in the body.**
   Those live in the sibling `run.json`. Every path that could appear in a
   message is scrubbed to `<cell>` / `<task>` / `<submission>` / `<projects>`.
6. `harness` is the scorer's version and is bumped whenever a subscore's
   computation changes; `task_version` is the task's. Two scores are comparable
   iff `(task_set, task_version, harness)` agree, and `bench report` warns when
   they do not.

Scoring the same submission twice therefore produces byte-identical bytes. One
honest exception: a candidate whose *own* script raises a message that is not
stable across runs (an address, a temp path of its own making) can make its
zero-scoring `score.json` differ byte-wise. AC3 is a claim about honest
submissions.

---

## `bench run` — driving the built-in agent

```bash
export ANTHROPIC_API_KEY=sk-ant-…          # required, checked before anything spawns
uv run agentcad bench run --set fast --agent builtin --report out/
```

**Prerequisite: `ANTHROPIC_API_KEY`.** `bench run` drives the shipped chat
agent, which is an Anthropic client, and a run without a key is **refused
before the kernel spawns** (`runner.require_agent`) rather than discovered as a
401 three minutes in. There is no offline mode and no other provider: the one
seam is `runner.CLIENT_FACTORY`, which the tests set to a scripted client, and
it is not a flag. If you have no key, drive AgentCAD from your own agent over
`agentcad mcp` and hand the result to `bench score`.

**`--model`** takes any model id the Anthropic Messages API accepts and is
passed straight through to `ChatEngine`; it is neither validated nor enumerated
here, so a typo surfaces as an API error on the first turn. The default is the
chat engine's own `DEFAULT_MODEL` (`agentcad/agent/chat.py`) — today
`claude-sonnet-5` — which means **a `bench run` with no `--model` measures
whatever the product currently ships**. The id that actually ran is stamped in
`bench.json`, every `run.json` and the report, and a baseline records it, so a
model change shows up as a comparison rather than as a silent drift.

Each task gets a **fresh scratch project** in a throwaway cell under the work
root, with its own service over the **shared** warm kernel. The prompt (plus
every asset, inlined as text) is one user message; the agent works through the
ordinary tool surface; what it leaves **on disk** is what gets scored.

* **The bundled examples are not registered** (`_build_service(examples=False)`).
  A task derived from `examples/rocketry` must not be solvable by opening
  `examples/rocketry`. The **catalog stays registered** — an `assemble_and_clear`
  task legitimately reaches for fasteners, and benching without a shipped
  product surface would measure something other than the product.
* **Budgets live in a client-factory wrapper**, not in `chat.py`: wall clock,
  cumulative tool calls and API turns are checked *before* each API call, and
  the exhaustion surfaces inside the engine's own blanket handler. The turn ends
  cleanly, the transcript stays valid, and the on-disk state is still scored.
  `run.json` records `stopped` and `over_budget`. The real wall bound is
  `wall_s + 30 s + the in-flight kernel request's own timeout` — a call already
  in flight cannot be preempted. The tool-call cap is likewise **soft**: it
  refuses the *next* API call, so the last request's own tool blocks all run.
* **One engine turn per task.** The loader refuses `turns >
  MAX_TOOL_CALLS_PER_TURN`, so there is no continuation logic. If 30 tool calls
  proves too tight for the assembly and optimisation categories that is a
  *product* finding, reported as `stopped: "tool_calls"` in every affected
  `run.json` — after which the bench measures the change.
* **Serial, and there is no `--jobs`.** The packages build fan-out was deleted
  after failing a pre-registered speed bar *and* flipping a verdict under
  `--budget`; a benchmark whose numbers depend on a worker count is not a
  benchmark. Do not re-add it.

Results layout:

```
out/
  bench.json                     # header + the ROSTER of selected tasks
  report.json  report.md         # written by `bench report`
  tasks/<category>/<id>/
      submission/                # the project directory the agent produced
      transcript.json            # the turn, path-redacted, images elided
      run.json                   # timestamps, model, host, usage, budgets, stopped
      score.json                 # the measurement
```

`bench.json`'s `tasks` roster is written from the **selection**, not from the
survivors: `bench report` takes its denominator from that index, so a task that
was selected and never scored has to appear there or it would quietly leave the
arithmetic.

---

## `bench report` and the release gate

```bash
uv run agentcad bench report out/ --baseline benchmarks/baseline.json \
    --epsilon 0.05 --md "$GITHUB_STEP_SUMMARY" --json-out out/report.json
```

```
task set bench-v1 · harness 1 · agent external · model worked-example · 1 task(s)
  category                          score      n  missing
  model_from_drawing               0.9959      1        0
bench report: 0.9959 over 1 task(s)
```

Pure: no service, no kernel, no network. A **category total is the unweighted
mean of its tasks**; the **overall total is the unweighted mean of the category
totals**. At five-per-category the two coincide — saying it this way means a v2
that adds tasks to one category cannot silently reweight the headline number.

* A **missing task scores 0.0** and is flagged `missing`. With `--baseline`,
  a baseline task the results do not contain is a full regression, listed under
  scope `coverage`. Gating green by not running the hard half is impossible.
* The gate is on **total and per category only**. Per-task deltas are printed,
  never gated: a single task under a stochastic agent is noise, and gating on
  it would make the release gate a coin flip. This is the one place the design
  deliberately measures more than it enforces.
* A baseline whose `(task_set, harness)` differs from the measured report is
  **exit 2**, not a pass. Comparing across harness versions is not a comparison.
* `benchmarks/baseline.json` ships with `total: null` until the first real run
  records one; `--baseline` against a null baseline exits 0 and warns.

---

## `bench publish` — the leaderboard

```
benchmarks/leaderboard/
  rows/<row-id>/row.json
  rows/<row-id>/report.json      # produced by `bench report --json-out`
```

A row is **rejected** — `publish` exits 1 having written **nothing**, and there
is no override flag — unless all five hold:

1. every disclosure key is present and every string one is non-empty — `agent`,
   `model`, `agentcad`, `task_set`, `date`, `harness_command`, `submission`,
   `transcript` as non-empty strings, `harness` as an int and `config` as an
   object that may be `{}` but must be present. (`schema` and `id` are the
   envelope, checked separately: they identify the document, they disclose
   nothing about the run. `notes` is **not** required and may be absent.)
2. `report.json` validates against the report schema;
3. the row's `task_set` / `harness` / `agentcad` equal the report's;
4. `submission` and `transcript` are absolute `https://` URLs, **or** paths
   relative to the row's own directory that stay inside it and exist there;
5. the report covers **every** task of the declared set — a partial run is not a
   leaderboard row, and a task the report itself flags `missing` is a partial
   run just as much as an absent one.

**Rule 4 is narrower than "repo-relative paths that exist"** (ledger D24): a
relative link is resolved against `<leaderboard>/rows/<row-id>/` and must stay
inside it, textually (no `..`, no absolute path, no scheme) *and* after
`resolve()`, so a symlink cannot walk out either. `../../../etc/passwd` is not a
submission, and the row directory is the only base that means the same thing to
the validator and to a reader of the page. Only an `https://` link renders as an
anchor; a relative one renders as `<code>` text.

**The row's identity is its directory name**, never the document's `id` — a
`row.json` carrying a disagreeing `id` is rejected rather than silently given
two names. An **unreadable** row document is a rejected row (exit 1), not a
harness error: a row we cannot read has disclosed nothing, which is exactly what
rule 1 exists to refuse.

The page is one self-contained HTML file (default `docs/bench/index.html`):
inline `<style>`, no script, no CDN, no web font, no clock reading, no
filesystem path. Rows are ordered by `total` descending, ties by `id` ascending,
so republishing the same input produces the same bytes.

---

## Submitting from outside the repo

Anyone can score their own agent without adding a line of code to AgentCAD. The
agent works through the **MCP server** — the same surface the built-in agent
uses — and hands in an ordinary project directory.

**1. Give your agent AgentCAD.**

```bash
claude mcp add agentcad -- uv --directory /path/to/agentcad run agentcad mcp
```

**2. Print the task's prompt with `bench prompt`.** It writes on stdout exactly
what the built-in runner hands its agent — every asset already inlined as text,
named by its path relative to the bundle:

```bash
uv run agentcad bench prompt model_from_drawing/mfd_001_spacer_plate > prompt.txt
```

**Do not `cat prompt.md`.** That file is authored for two audiences, and the
reviewer's half sits in HTML comments: the rationale for a weight override, the
reference parameters a threshold was derived from — part of the answer, in
short. `tasks.prompt_text` strips those comments before the built-in agent ever
sees them (`bench prompt` is that function and nothing else), so pasting the
raw file would hand your model a document the built-in agent never gets, and
your score would not be comparable to anyone else's. `--json` emits
`{"task", "prompt", "assets"}` if you would rather pipe it.

**3. Hand the agent that text, verbatim,** and nothing else — no reference
project, no `specs/`, no `reference/metrics.json`. Let it work in a scratch
project whose name matches `target.project` (any name works for `bench score` —
the scorer opens the directory you point at — but matching keeps the two paths
identical).

**4. Score the directory it produced.**

```bash
uv run agentcad bench score /path/to/scratch/bench_mfd_001_spacer_plate \
    --task model_from_drawing/mfd_001_spacer_plate --out /tmp/mfd-001
```

The submission is never written to: it is copied into a throwaway cell, the
rubric is injected into the **copy**, and `.cache/` lands in the cell.

**5. To claim a leaderboard row**, run the whole `core` set, keep the results
directory (`bench run --report out/`), produce `report.json` with
`bench report --json-out`, and open a PR adding
`benchmarks/leaderboard/rows/<your-row>/{row.json,report.json}` with your
submissions and transcripts linked. The five disclosure rules above are checked
mechanically, and rule 4 means an archive you ship in-tree must sit **inside
your own row directory**.

### A worked submission

[`benchmarks/examples/submission-mfd-001/`](../benchmarks/examples/submission-mfd-001/)
is that walkthrough's output for `mfd_001_spacer_plate`, produced by hand
against the prompt the way an agent over the MCP server would produce it: a
`project.json` and one `parts/spacer_plate.py`, nothing else. It is deliberately
**not** a copy of the reference. Two entirely realistic readings differ:

* R4 corner rounds instead of the sheet's R5;
* Ø6.6 holes — the ISO 273 *medium* clearance for M6 — instead of the Ø6.0 THRU
  the sheet dimensions.

Everything else matches, including the datum. It scores **0.9959**: `built`,
`valid`, `specs` and `metrics` all 1.0, and the whole deviation lands on
`geometry`. The two errors pull opposite ways — the wider holes remove ~143 mm³
the reference keeps, the smaller corner radius keeps ~46 mm³ the reference
removes — so 189 mm³ of the two shapes disagree, and that symmetric difference
against a 23 239 mm³ union is the 0.0081 of IoU it loses. Reproduce it with the
command at the top of this document; `score.json`'s
`subscores.geometry.detail.parts.spacer_plate` reports all four volumes, so the
arithmetic is checkable by hand.

That is the shape of the instrument: a near-right answer scores near 1.0 and
the *reason* is a number you can check by hand. A **frame** error is the
expensive one: the same script with the plate's corner at the origin instead of
its centre — a dimensionally perfect part in the wrong pose — measures
`geometry` **0.1369** and totals **0.5684**, with every other subscore still
1.0. Which is why every prompt states the datum in words, and why a task author
who leaves it out has written a coin flip.

---

## Authoring a task

Every task in `benchmarks/tasks/` satisfies all of this, and a reviewer checks
it against the bundle:

1. `benchmarks/tasks/<category>/<id>/` with `id` matching
   `^[a-z][a-z0-9_]{2,47}$`.
2. `task.json` as above. `source` is `{"kind": "authored"}` or
   `{"kind": "derived", "example": "<dir>", "parts": [...]}`. `sets` is
   `["core"]`, plus `"fast"` for the CI subset (one task per category).
   `authored_against` is today's `agentcad.__version__`.
3. `prompt.md` **names the part id the agent must create** and **states the
   datum in words** (which face is on Z = 0, which axis the long dimension runs
   along, where the origin is). This is the frame-alignment mitigation; a
   reviewer reads `prompt.md` against `frame.datum` and they must agree.
4. `weights` are the category defaults unless the task argues an override **in
   an HTML comment** at the top of `prompt.md`. The comment is stripped from
   the prompt the agent sees (`strip_reviewer_comments`), which is the point:
   the argument is for a reviewer. **Never write the rationale as prose** — that
   reaches the model.
5. `budgets.turns <= 30`; `budgets.wall_s` is 600 for single-part tasks, 900 for
   assembly and optimisation tasks.
6. `reference/project/` is a complete project directory whose part scripts carry
   **no** `SPECS` — the rubric lives in `specs/`.
7. `specs/parts/<part>.py` re-binds `SPECS` and imports every constructor under
   a `_bench_` alias. `specs/project.py` for assembly-scope checks. No
   `check_fem_static`.
8. `reference/steps/<part>.step` is generated by the helper, never hand-edited.
   A datum is **required for every target part whenever `geometry` weight is
   greater than 0** — the loader refuses the bundle otherwise — and optional
   below it: `fix_005_invalid_shell` ships one at weight 0.00 (it is still the
   record of what the fixed part is), while the `asm_*`/`opt_*` bundles declare
   `reference.steps: {}` because nothing would open a datum that cost 5.2 MB to
   generate.
9. `reference/metrics.json` is seeded by the helper, then **hand-tightened**: a
   window must be tight enough to fail a wrong answer and loose enough that the
   reference passes with margin.
10. **The reference must score exactly 1.0.** If it does not, the rubric is
    wrong, not the reference.
11. A derived task copies the example's script **into the bundle**; it never
    references `examples/` at run time.

```bash
# 1. write task.json, prompt.md, reference/project/, specs/
# 2. generate the datum and seed the windows
uv run python -m agentcad.bench.author step    benchmarks/tasks/<c>/<id>
uv run python -m agentcad.bench.author metrics benchmarks/tasks/<c>/<id>
# 3. (model-from-drawing tasks) render the drawing asset from the reference
uv run python -m agentcad.bench.author drawing benchmarks/tasks/<c>/<id> --part <part_id>
# 4. hand-tighten reference/metrics.json, then prove it
uv run agentcad bench score benchmarks/tasks/<c>/<id>/reference/project --task <c>/<id>
```

Two authoring notes worth stating out loud. **The weight-override rationale is
reviewer-facing and the agent never sees it**: `prompt_text` runs
`strip_reviewer_comments` over `prompt.md`, so every `<!-- … -->` block (and the
blank it leaves) is removed before the prompt is handed to the model. That is
deliberate — telling an agent "geometry is not scored on this task" tells it how
it is marked and changes what it spends its budget on — so the argument stays in
the file where a reviewer reads it in the diff, and **rationale written as prose
would reach the model**. Assets are attached verbatim, because an SVG's own
comments are part of the drawing. And `author.py drawing` refuses to write a
sheet whose overall dimensions contradict the part it drew (`check_dims=True`),
because the drawing handler's view bounds undersize a curved silhouette; a bench
asset that lies is a task nobody can solve.

---

## Why there is no `bench_*` tool

The bench adds **no** model-facing tool, no route, no event, no error type and
no manifest key. It is CLI-only, and the `iou` scorer is a kernel handler that
`build_registry` never sees — a test asserts its absence. The reason is the
measurement itself: a tool the model can call to ask *how close am I to the
reference?* turns the benchmark into a search problem over its own answer key.
`agentcad/bench/` is also OCP-free by contract (only `agentcad/kernel/` may
import build123d/OCP), which is what lets the scorer run in the same process as
the server-side code it measures.

---

## What this does not guarantee

* **It is a correctness measurement, not a security boundary.** A candidate's
  script is arbitrary Python running in the same interpreter as the spec runner,
  so a determined submission could monkeypatch `agentcad.toolkit.specs`. The
  exposure is bounded to **one subscore**: `geometry`, `metrics`,
  `interference`, `built` and `valid` are kernel-measured and unfakeable, and
  transcripts are published. This is the publish gate's exact posture — a
  correctness gate, never a security boundary — and if a real shortcut appears
  the answer is a red row, not a sandbox.
* **Frame ambiguity is mitigated, not solved.** `frame.datum` is prose in the
  prompt; `align` and `rotations_deg` are mechanical. A correct part in an
  undeclared pose still scores badly, and that is the *task author's* bug, which
  AC1 cannot catch (the reference is in the right pose by construction). The
  mitigation is the checklist above and a reviewer who reads `frame.datum`
  against `prompt.md`.
* **`assemble_and_clear` grades non-interference, not placement.** The rubric a
  v1 assembly task can express is one-sided: `check_clearance(min_mm=…)` sets a
  *floor* on the gap between two instances, and metric windows are keyed to a
  **part**, so nothing in the rubric can see where an instance was placed. A
  candidate that creates every instance and parks them far apart therefore
  satisfies the `specs` and `interference` channels in full. The prompts state
  the seating in words and a reviewer reads them, but the scorer does not
  penalise it — and `check_stackup` cannot close the hole, because it measures
  worst-case tolerance accumulation along a *mate chain* and these instances are
  placed rather than mated. v1 ships the limitation disclosed rather than
  papered over; closing it needs a max-clearance (or placement-window) check in
  `toolkit/specs.py` and a v2 task set that uses it.
* **Two product findings bound what `geometry` can measure at all.** Both were
  found by authoring the task set and are stated here because a subscore that
  is silently unmeasurable is worse than one that says so.
  * *A swept surface does not survive the STEP round trip as a boolean
    operand.* `fix_005_invalid_shell`'s coolant elbow is a swept pipe:
    script-against-script and STEP-against-STEP both intersect cleanly at
    21 711.685 mm³, but the boolean the IoU handler actually takes — the
    candidate's script solid against the checked-in STEP — returns `None`,
    i.e. **0 mm³ of intersection between two solids of identical volume**. The
    task therefore weights `geometry` 0.00 and carries the weight on `metrics`,
    which measures the same fact through mass, volume and bbox. This is an
    OCCT/product observation, not a bench one: any AgentCAD feature that
    booleans an imported swept surface meets it.
  * *Generated view bounds undersize a curved silhouette.*
    `kernel/handlers/drawing.py`'s `_view_bounds` samples each edge at six
    points, which is exact for a line and wrong for a circle, so a sheet's
    overall dimensions can come out under the truth. `author.py drawing`
    refuses to write such a sheet (`check_dims=True`), because a bench asset
    that contradicts its own part is a task nobody can solve.
* **A build that times out or crashes is the candidate's, and it is a zero.**
  Nothing in the scorer shortens a build's own ceiling, so with no `--budget` a
  `timeout` is a fact about how long the candidate's script runs, and a worker
  the build killed is a fact about the geometry the script asked OCCT for:
  `built` scores each 0.0 with `status: "ok"` and `reason: "build_timeout"` /
  `"build_crash"`, and the weights are **not** renormalised. Only a `--budget`
  that has already expired makes either one a harness `error`.
  The crash lane is the one place the harness/candidate split (`error` means
  *we* could not measure) is decided the other way, and deliberately: nothing
  else stops measuring when a worker dies — `SpecRunner.run` treats a
  mid-measurement `KernelError` as a row payload and returns real rows for
  every other part — so an excluded `built`/`valid`/`geometry`/`metrics` would
  renormalise a candidate with one reliably-crashing part onto its passing
  rubric (measured: 0.24 → 0.60 on an `mts`-weighted task). A
  slow-but-correct part is, in exchange, scored against a wall clock the bench
  never declared — the honest lever there is the task's own `budgets`.
* **Per-task deltas are not gated** — see above.
* **A stochastic agent against a per-category gate.** `--epsilon` is the only
  knob and 0.05 on a five-task category mean is loose. The gate is advisory on
  the schedule and blocking on a release branch; many samples per task is
  unaffordable and is not proposed.
* **The optimisation category's constraint is sometimes the parameter range.**
  `opt_002_stiffest_gusset` and `opt_005_shortest_screw` are genuinely
  constraint-bound: the optimum sits where a declared engineering check (wall,
  clearance, thread engagement) stops being satisfiable, which is the shape the
  category promises. On `opt_001_lightest_bracket`, `opt_003_thinnest_lid` and
  `opt_004_most_bolts` the binding limit is instead the **`PARAMS` range the
  script declares** — the best answer is the end of the slider, and an agent
  that reads `PARAMS` and goes to the bound scores as well as one that
  reasoned. Those three still measure that the agent can read a constraint set,
  drive a parametric model and verify it, but they do not measure engineering
  judgement. Closing it is a v2 task-set change (widen the declared ranges so a
  real check binds first), not a harness change, and the three tasks are
  disclosed rather than removed.
* **Contamination.** These tasks are public, so they will eventually appear in
  training corpora. The mitigations are versioned task sets (every score is
  labelled `bench-v1`) and published transcripts (a shortcut is inspectable);
  a **rotation policy adding fresh tasks per release is Phase 3** — designed,
  with `task_set` stamped everywhere it needs to be, and not yet built. Note
  too that a run is not blind: the scratch project the runner creates is named
  `bench_<task_id>` (`target.project`), so the agent can see it is being
  benchmarked and which task it is on. That is deliberate — the name is what
  makes `bench run` and an external `bench score` submission the same shape —
  but it is contamination surface an "unaware agent" claim would not survive.
  Public-and-reproducible beats secret-and-unverifiable for this purpose, and
  we would rather say that out loud than imply a secrecy we do not have.

---

## CI

[`.github/workflows/bench.yml`](../.github/workflows/bench.yml), deliberately a
**separate** workflow file: `ci.yml`'s shape is asserted byte-wise by
`tests/test_prd006_acceptance.py`, so a bench job added to it would fail a test
that has nothing to do with the bench.

| job | when | what |
|---|---|---|
| `selftest` | every push and pull request, **no secret** | the whole bench suite including AC1 (all 25 references score 1.0) and the STEP-drift check. This is the PR-blocking half. |
| `guard` | always | reads whether `ANTHROPIC_API_KEY` is configured and emits a visible `::notice::` when it is not. It exists because the `secrets` context is not readable from a job-level `if:`, and a *silently* skipped benchmark is indistinguishable from one that scored zero. |
| `builtin` | `schedule` / `workflow_dispatch` / `push` to main — **never `pull_request`** | `bench run --set fast --agent builtin` then `bench report --baseline`, whose exit code is the gate. Uploads the results directory `always()`. |

`builtin`'s `if:` carries **three** ANDed conditions: the key is present, the
event is not a pull request, and the ref is `refs/heads/main`. The first two are
the trust rule — it holds a secret and executes agent-authored Python, so a
fork's pull request must never reach it (`geometry-ci.yml`'s rule, one workflow
over). The third is the *spend* rule: `on.push` covers `roadmap` as well as
`main` because `selftest` should run on both, and a trigger list wider than the
job's own guard is how a paid run starts where nobody expected one. For the same
reason `cancel-in-progress` is scoped to pull requests: cancelling an in-flight
paid run burns the spend and leaves no result.

`benchmarks/` is resolved like `examples/` and `catalog/` (not in the wheel) and
is in `.dockerignore` along with `out/`, so the task tree never lands in the
image; `out/` is in `.gitignore` too, because a results directory is evidence
to publish deliberately, not a tree to commit by accident.

---

## Phase 2 / 3 seams

Designed, not built. Each already has the seam it needs:

| item | the seam |
|---|---|
| Launch leaderboard rows (built-in + external) | `benchmarks/leaderboard/rows/` and `bench publish` already accept and validate them; the numbers need real paid runs. |
| A `fem/` category | `check_fem_static` is refused by the loader **by name**; lifting the refusal plus an `importorskip` guard is the whole change. FR3 keeps core tasks extra-free. |
| Task-set v2 rotation | `task_set` is stamped in `task.json`, `score.json`, `run.json`, the report, the baseline and every row; a v2 set is a new directory and a new stamp. |
| PRD-018 wiring | `bench report --json-out` is a stable machine artefact and `--baseline` is the objective function. |
| PNG assets | the chat surface refuses a non-`str` message; widening it is a product change with its own review. SVG-as-text covers every v1 task. |
| Multi-turn continuation | the runner already records *why* a turn stopped; `turns <= MAX_TOOL_CALLS_PER_TURN` makes it unreachable in v1. |
| Voxel-IoU fallback | the handler's `status: "error"` path is already the honest degradation; a second, uncalibrated scorer would be worse than one that says it could not measure. |

---

## Where the code lives

| | |
|---|---|
| `agentcad/bench/tasks.py` | The bundle schema, `Task`/`Frame`/`Budgets`/`MetricWindow`, `task_problems`, `load_task`/`load_tasks`, `prompt_text`. |
| `agentcad/bench/scoring.py` | `Scorer`, rubric injection, the six subscores, `score.json`. |
| `agentcad/bench/runner.py` | `BudgetedClient`, `run_task`, the transcript and `run.json`. |
| `agentcad/bench/report.py` | `aggregate`, `compare_baseline`, the markdown, the exit code. |
| `agentcad/bench/publish.py` | The five disclosure rules and the static page. |
| `agentcad/bench/cli.py` | `add_bench_parser` / `cmd_bench` — the whole CLI surface. |
| `agentcad/bench/author.py` | The task-authoring helper (`step`, `metrics`, `drawing`). Writes into the repo; never pointed at a bundle you do not own. |
| `agentcad/kernel/handlers/bench.py` | The `iou` handler — kernel-internal, never a tool. |
| `benchmarks/` | The task bundles, `baseline.json`, the leaderboard rows and the example submission. |

Tests: `tests/test_bench_tasks.py` (the loader),
`test_bench_tasks_fix_asm.py` (the `fix_*`/`asm_*` bundles),
`test_bench_kernel_iou.py`
(AC4), `test_bench_scoring.py` (AC2/AC3), `test_bench_runner.py` (AC8, offline
against a scripted client), `test_bench_report.py`, `test_bench_publish.py`
(AC7), `test_bench_cli.py`, `test_bench_author.py`, and
`tests/test_prd024_acceptance.py` (AC1, AC6, AC9, the STEP-drift check and the
workflow's shape).
