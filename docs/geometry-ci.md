# Geometry CI — `agentcad check`

One command certifies a whole project: it rebuilds every part, re-resolves the
assembly and looks for interference, evaluates the declared design specs and
regenerates the drawings — headless, in-process, with no server, no port and no
API key — and answers with a versioned JSON report, a markdown summary and an
exit code.

```bash
agentcad check --project examples/rocketry --report report.json --md report.md
```

```
rocketry — worktree
  stage      status  pass  fail  skip  error  total     time
  build      green      4     0     0      0      4   44.8 s
  assembly   green      3     0     0      0      3    0.1 s
  specs      green      8     0     0      0      8    0.4 s
  drawings   green      3     0     1      0      4    1.0 s
warnings:
  - assembly_assembly: the imported geometry reports is_valid=false on the whole
    shape (180 solids); validity is reported for imported parts, never enforced
check: green — rocketry · 18 passed, 0 failed, 1 skipped, 0 errors of 19 in 46.3 s (exit 0)
```

(The one skip is the imported STEP reference part in the drawings stage —
`not_script`. The warning is the reference-part validity rule below: reported,
never enforced.)

The same run is available to an agent as `run_checks`, to a browser as
`POST /api/projects/{proj}/checks`, and to GitHub as the composite action in
[`.github/actions/agentcad-check`](../.github/actions/agentcad-check/README.md).
All four drive **one** `CheckRunner` over one project, so the report is
identical everywhere.

**What makes this trustworthy is determinism.** Same script + same parameters ⇒
identical geometry and byte-identical meshes (cache key
`sha256(content, params, density, tolerance)`). A red check means the change is
wrong, not that regeneration was flaky — and `--verify-determinism` is the
standing regression guard for that claim.

---

## Two rules to read the whole feature by

1. **It composes; it never measures.** Every number in a report comes from a
   surface that already exists and is reviewed: `service._ensure_built`,
   `service._resolved_instances` + `check_interference`, `SpecRunner.run`
   (PRD-003, all three tiers) and the `generate_drawing` / `flat_pattern`
   tools. A failing row's `error` is that surface's payload **verbatim** — the
   same `details.traceback`, `details.line` and Error Doctor `details.hint`
   `update_part_script` hands back — so a red check is a structured task an
   agent already knows how to fix.
2. **The report is honest; `--strict` is the opt-in.** A row that could not be
   measured stays a `skip` with its reason and its hint, whatever flags you
   pass. `--strict` records the ids it counted in `strict_failures` and moves
   only the *derived* `status` and `exit_code`, so a reader can always tell
   what was **measured** from what was **demanded**. PRD-003's `specs` proposal
   gate is the fail-closed reading of the same measurements and stays that way.

---

## The command

```
agentcad check [--project PATH|NAME] [--projects-dir DIR]
               [--ref REF] [--stages build,assembly,specs,drawings]
               [--report PATH] [--md PATH]
               [--strict] [--verify-determinism]
               [--budget SECONDS] [--min-volume MM3] [--work-dir DIR]
               [--proposal ID | --auto-proposal]
               [--sha SHA] [--ref-label NAME]
               [--quiet | --json]
```

| Flag | |
|---|---|
| `--project` | A project **path** (anything containing `/` or starting with `.`) or a registered project **name**. Defaults to the current directory — the CI shape. |
| `--projects-dir` | The projects root, when you address a project by name. |
| `--ref` | Certify a **branch, tag or commit** instead of the working tree (below). |
| `--stages` | A comma-separated subset of `build,assembly,specs,drawings`. An unknown name exits 2 naming the valid ones, before the kernel starts. `determinism` is not selectable — it has its own flag. |
| `--report` / `--md` | Write the JSON report / the markdown summary. Both are atomic writes; an unwritable path is exit 2. |
| `--strict` | Count every `skip` row as a failure **in the verdict only** — except a row marked `strict_exempt` (an unconditional skip; today only the DXF determinism row, below). |
| `--verify-determinism` | Build every part a second time on a cold cache and compare the artefacts byte for byte. |
| `--budget` | A wall-clock deadline in seconds, read before each item and each kernel call. Must be **finite and non-negative** — `nan`/`inf` are refused with exit 2 before the kernel starts, because a NaN deadline is never in the past and so bounds nothing. |
| `--min-volume` | The overlap volume below which an interference is noise (default `0.001` mm³). Finite and non-negative, for the same reason: every comparison with NaN is false, so a NaN threshold reports a genuinely interfering assembly as green. |
| `--work-dir` | Where `--ref` materializes its worktree. Default: a temp dir the run owns, deleted afterwards (under the server's granted work root, so a confined worker can write into it). A dir you pass keeps everything in it — the run works inside a unique subdirectory it creates and deletes only that. A work dir that **is, holds or sits inside** the project (or the projects root) is refused, exit 2. |
| `--proposal` / `--auto-proposal` | Post the report to a change proposal (below). Mutually exclusive. |
| `--sha` / `--ref-label` | **Provenance only.** The host VCS commit and ref name (`$GITHUB_SHA`, `$GITHUB_REF_NAME`). They are recorded; they resolve nothing. |
| `--quiet` / `--json` | `--quiet` prints nothing (a harness failure still names itself on stderr — an exit 2 with no diagnosis is unactionable). `--json` puts the report **alone** on stdout, so `agentcad check --json \| jq` works. Exit codes are identical in all three modes. |

**stdout is a contract, stderr is for humans.** The stage table, the named
failures, the warnings and the harness errors go to stderr; the one-line
verdict goes to stdout.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | **green** — everything selected was measured and passed (skips are not failures). |
| `1` | **red — the model is wrong.** Any `fail` or `error` row; or `--strict` met a `skip`. Read the report and fix the design. |
| `2` | **harness — we could not produce a verdict.** `complete: false` (a blown `--budget`) *regardless of status*; an unknown project or stage; a non-finite `--budget`/`--min-volume`; `--ref` on a project with no git; an unwritable report path or work dir; `--auto-proposal` matching more than one proposal; a refused post (a terminal proposal, or a report that measured a dirty tree). Fix the environment or the invocation. |

A **spec `error`** ("the check itself broke") is deliberately `1`, not `2`: it
is a fact about the model, and a caller must be able to tell *read the report*
from *fix the environment*.

---

## The stages

A run always reports **all four stages, in this order** — an unselected one is
`skip`/`not_selected` rather than absent, so a consumer never has to guess
whether a stage was green or never ran.

| Stage | What it drives | What a row means |
|---|---|---|
| `build` | `service._ensure_built` per manifest part | `pass` — the part built; `details` carries `cache_key`, `volume_mm3`, `mass_g`, `n_solids`, `is_valid`, `cached`. `fail` — the build failed, or a **script** part reported `is_valid: false`; `error` carries the tool payload. A **configured** part (PRD-012) adds one row per configuration after its own, subject `part@config`, built purely through `service._ensure_config_built` and with `details.config` beside the same fields — no new stage and no new item kind, and the budget is read before every one of them. |
| `assembly` | `_resolved_instances`, then `check_interference` | One `pass` row per resolved instance; an unresolvable mate is one `fail` row of kind `mate`; each interfering pair is a `fail` row of kind `pair` with `{a, b, volume_mm3}`; each `skipped_mesh` id is a `skip`/`mesh_only`. Under two instances, the whole stage is `skip`/`no_instances`. |
| `specs` | `SpecRunner.run` — PRD-003, all three tiers | One row per declared check, preserving `status`, `requirement`, `reason`, `hint`, `error`, with `measured`, `limit`, `unit`, `scope`, `part` and `location` in `details`. The spec report is embedded whole as `stage["report"]`, and the check report's top-level `requirements` map is that report's traceability. Nothing declared → `skip`/`not_declared`. |
| `drawings` | `generate_drawing` (**SVG only**) per script part, then `flat_pattern` where the script defines one | `pass` — regenerated, with `path` and `size_bytes`. A part that does not define `flat_pattern` gets **no row** (absent, not green — the presence scan is `ast.parse` and never executes the script). A reference part is `skip`/`not_script`. |

### What a stage may claim, and what it may not

- The build stage claims *this part builds and the kernel calls the solids
  valid*. **An imported reference part's `is_valid` is reported, never
  enforced**: OCCT calls the shipped `examples/rocketry` STEP import invalid
  across its 180 solids, which is exactly why `tests/test_examples.py` exempts
  reference parts from that assertion. The row passes, `details.is_valid`
  carries the fact and a warning names the part. A `part@config` row claims the
  same thing about **that configuration's pure resolution** (defaults <
  configuration) and nothing about the part's working state — the part's own
  row is still the one that says whether what you are looking at builds. A
  harness failure on one of them is a `report.errors[]` entry carrying
  `config` beside `part`, so "size XL is broken" is never reported as "the
  part is broken".
- The assembly stage claims *the mates resolve and no pair of B-rep instances
  overlaps by more than `--min-volume`*. **It does not claim a mesh instance
  clears anything**: booleans on an imported STL segfault OCCT, so those are
  `skip`/`mesh_only` and an empty pair list is not proof.
- The specs stage claims exactly what `run_specs` claims. A part that declares
  nothing has no row — absent, not green.
- The drawings stage claims *the drawing regenerates*. It does not claim
  byte-stability; that is `--verify-determinism`'s job, and only for SVG.

### Skip reasons

Every `skip` carries a `reason` **and** a `hint` — enforced at construction, so
a row that says "not measured" without saying why or what to do about it is a
`ValueError` a test catches rather than a silent hole.

`not_selected` · `budget_exceeded` · `no_instances` · `mesh_only` ·
`not_declared` · `specs_unavailable` · `not_script` · `drawings_unavailable` ·
`not_byte_stable` — plus every reason a spec row can carry
(`fem_extra_missing`, `deferred`, `unsupported_scope`, …), passed through
unchanged.

One of them, `not_byte_stable`, is **unconditional**: no project can make that
row pass. It is the only row that carries `strict_exempt: true`, and it is the
only reason that field exists (see `--verify-determinism`).

---

## The report

`report.json` is a versioned document — read `schema` first. It is validated by
`agentcad.core.checks.validate_report(report) -> list[str]` (empty = valid), a
hand-rolled checker with **no `jsonschema` dependency**, which is also what the
acceptance tests use.

```jsonc
{
  "schema": 1,
  "agentcad": "0.1.0",
  "project": "prototyping",
  "source": {                    // what was measured, and how it was named
    "kind": "worktree",          // worktree | branch | tag | commit
    "ref": null,                 // the ref as you typed it (--ref)
    "sha": null,                 // the commit that was measured
    "label": null,               // --ref-label: host VCS ref name (provenance)
    "host_sha": null,            // --sha: host VCS commit (provenance)
    "dirty": false               // the measured branch has uncommitted edits
  },
  "started": "2026-08-11T07:07:15Z",
  "finished": "2026-08-11T07:07:16Z",
  "duration_s": 1.194,
  "status": "green",             // green | red | skip
  "complete": true,              // false ⇒ a budget cut the run short ⇒ exit 2
  "strict": false,
  "strict_failures": [],         // the skip ids --strict counted (rows unchanged;
                                 // a strict_exempt row is never a candidate)
  "exit_code": 0,
  "summary": {"passed": 6, "failed": 0, "skipped": 0, "errors": 0, "total": 6},
  "stages": [ /* below */ ],
  "requirements": {},            // requirement id -> {status, checks: [item ids]}
  "warnings": [],                // strings; never a failure
  "errors": [],                  // harness problems; `fatal: true` when a stage died
  "host": {
    "platform": "darwin",
    "python": "3.12.4 …",
    "agentcad": "0.1.0",
    "fem": true,                 // the [fem] extra is installed
    "sandbox": true,             // the kernel worker is confined (macOS)
    "pool_size": 1,
    "kernel_pool": "KernelClient"
  }
}
```

One stage:

```jsonc
{
  "name": "build",
  "status": "green",             // green | red | skip
  "reason": null,                // non-null ⇒ the stage was explicitly skipped
  "duration_s": 0.392,
  "summary": {"passed": 2, "failed": 0, "skipped": 0, "errors": 0, "total": 2},
  "items": [ /* below */ ]
  // the specs stage additionally carries "report": <the SpecRunner document>
}
```

One row. **Rows are `items`, never `checks`** — `checks` already means the gate
name, `report["checks"]` inside a spec report, and the proposals UI tab.
Likewise `status` is the four-value row status and `state` is the gate's; they
are not interchangeable.

```jsonc
{
  "id": "build:enclosure_base",  // "<stage>:<subject>", unique within a report
  "kind": "part",                // part|instance|pair|check|drawing|flat_pattern|mate
  "subject": "enclosure_base",
  "status": "pass",              // pass | fail | skip | error
  "message": "built — 3.869e+04 mm³, 40.23 g, 1 solid(s), valid",
  "reason": null,                // skip ⇒ non-null
  "hint": null,                  // skip ⇒ non-null
  "requirement": null,           // spec rows carry their requirement id
  "strict_exempt": false,        // true ⇒ an UNCONDITIONAL skip --strict ignores
  "error": null,                 // fail/error ⇒ the tool payload, verbatim
  "details": {"cache_key": "4a2e…", "volume_mm3": 38686.8, "mass_g": 40.23,
              "n_solids": 1, "is_valid": true, "cached": false}
}
```

The four row statuses are PRD-003's, and they are four different facts:
`pass`/`fail` were **measured**; `skip` is a **named structural inability** to
measure; `error` means **the check itself broke** — "we do not know", which is
not "it is fine".

### `report.md`

The markdown rendering is valid GitHub-flavoured markdown, valid as a
`$GITHUB_STEP_SUMMARY` and valid as a PR comment body: a header line (project,
source, duration, version, platform, `fem: yes/no`, strict, exit code), the
stage table, `## Failures` (one block per `fail`/`error` row with its message,
its `error.details.line` and its hint quoted), `## Skipped` grouped by reason,
then the warnings and any harness errors.

```markdown
# Geometry CI — `prototyping` — **red**

worktree · 0.6 s · agentcad 0.1.0 · darwin · python 3.12.4 · fem: yes · strict: no · exit 1

| Stage | Status | Pass | Fail | Skip | Error | Total | Time |
|---|---|---:|---:|---:|---:|---:|---|
| build | red | 1 | 1 | 0 | 0 | 2 | 0.2 s |
…

## Failures

### `build:enclosure_lid` — fail

build failed: Standard_Failure:

- `script_error` at line 43

> A primitive (Cylinder/Hole/Cone/Box/...) was constructed with a non-positive
> dimension … Fix: Validate radius/height/side parameters are > 0 …
```

Rendered failures and skips are capped at `MAX_RENDERED_FAILURES = 50` with a
`_+N more — see report.json_` line: `$GITHUB_STEP_SUMMARY` is capped at 1 MiB
and a 33-part project with a broken shared import would blow past it.

---

## Working tree vs `--ref`

By default a check measures **the tree you are in**: fast, cache-warm, and it
reports `source.dirty` when there are uncommitted edits.

`--ref <branch|tag|commit>` measures a **commit** instead, and the containment
is the point:

```
                 ┌─ your project ────────────────────────────┐
                 │  parts/  project.json  .cache/  exports/  │   ← byte-untouched
                 │  .history/  (the project's git repo)      │
                 └──────────────┬────────────────────────────┘
                                │ git worktree add --detach <sha>
                                ▼
   ┌─ <work-dir>/agentcad-check-<pid>-<rand>/<project>/ ─────┐
   │  a second, EPHEMERAL AgentCADService                    │
   │    bus.on_publish = None       (never commits)          │
   │    store.branch_resolver = None  (no sidecar)           │
   │    store.write_guard = None    (no ensure_checkout)     │
   │    the SAME kernel object      (no second pool)         │
   │  its own cold .cache/                                   │
   └─────────────────────────────────────────────────────────┘
                     removed in a `finally`, then `worktree prune`
```

- The ref is resolved **explicitly**: `resolve_branch`, then `resolve_tag`,
  then a full/short commit id. Never `git rev-parse` alone — it searches tags
  *before* branches, so a tag named like a branch would silently answer for it.
  A name that is both resolves as the **branch** and adds a warning naming the
  ambiguity; `refs/heads/<x>` and `refs/tags/<x>` disambiguate.
- **`--detach` with the resolved commit**, never a branch name: a branch
  already checked out at `.history/trees/<b>/` cannot be checked out twice.
- **The work dir is never written to directly.** Everything a run makes goes
  into one `agentcad-check-<pid>-<rand>/` subdirectory it created itself, and
  the teardown deletes exactly that. A work dir that is, contains or sits
  inside the project — or the projects root — is **refused** (exit 2, naming
  both paths): the throwaway tree is named after the project, so `--work-dir .`
  from the projects root would otherwise resolve onto the live project.
- The three muzzles are not optional. A live event bus would publish
  `project_changed`, and the service's snapshot hook would commit **into the
  linked worktree** — i.e. into your real repository — from a command whose
  contract is "never mutates". A live branch resolver would send every read and
  write through a `.history/agentcad/` sidecar that does not exist there, and
  create one. A live `write_guard` (the versioning pack installs one) would
  call `branches.ensure_checkout` on the first authored write and materialize a
  branch tree in your `.history` repo.
- A branch with uncommitted edits is measured **as committed**: `source.dirty`
  is `true`, a warning names the snapshot that was measured, and the runner
  deliberately does *not* snapshot first. A check may not mutate.
- **The stated price: a ref check runs on a cold cache.** The work dir holds no
  `.cache/`, so every part is a real kernel build and every row reports
  `cached: false`. That is the literal cost of "a check never mutates the
  project" being a sentence with no footnote. A cache-dir override is the
  recorded phase-2 follow-up, after measurement.

A cleanup failure is a warning, never a red check: `git worktree prune` heals a
stale registration on the next run.

**On a GitHub runner, do not use `--ref`.** `actions/checkout` has already
materialized the ref into the working directory, and a runner has no AgentCAD
`.history/` repo to resolve against — so the action checks the tree and passes
the SHA as provenance.

---

## `--budget`

The budget is a **deadline on `time.monotonic`** (never wall clock: an NTP step
must not move a budget), read **before each item and before each kernel call**.
What it cut short is reported as `skip`/`budget_exceeded`, every later stage is
marked the same way, `complete` goes `false` and the exit code is `2` — a
partial report is evidence, a missing one is not.

Every stage is under it:

- **build** checks it before every row it emits — the part's own **and** each
  `part@config` row of a configured part — so a budget that dies mid-family
  names the members it never reached instead of dropping them;
- **drawings** checks it before each part; the drawings stage draws the
  working state only and emits no per-configuration rows;
- the two **assembly** calls take the remainder as their `timeout_s`;
- the **specs** stage runs `SpecRunner.run(deadline=…)`, so PRD-003's own
  machinery bounds each tier's kernel call and reports a check it never reached
  as an `error` row that says so (fail-closed);
- **determinism** re-reads it before *each* of the four calls one row makes
  (two builds, two drawings) rather than once per row.

Below a **one-second floor** no kernel call is issued at all: a call that
cannot finish inside the budget can only overshoot it and then fail as a
*timeout*, which would read as "the model is wrong" for something the budget
did. An item the deadline stopped is a `skip`/`budget_exceeded` — never an
`error`, and never a red. A red report means the model is wrong; a blown budget
is exit 2, and the two must not be confused.

**The honest limitation:** `_ensure_built` (300 s) and the drawing tools (120 s)
take no `timeout_s`, so a budget cannot preempt a kernel call that has already
started. The worst-case overshoot is **one in-flight call**. When the deadline
expires *inside the last item* — the one case no later check can see — the
report stays `complete` (everything selected was measured, so there is a
verdict) and the overshoot is named in `warnings[]`. `complete: false` means
*something was not measured*; it never means "this took longer than you asked".

The budget is **per run, not per runner**: `service.checks` is one object shared
by the CLI, the chat agent, the tool and the route, and each `run()` measures
through a private run context. Two concurrent checks cannot see, let alone
overwrite, each other's deadline or `--min-volume`.

---

## `--verify-determinism`

A derived `determinism` stage — not in `STAGES`, not selectable with
`--stages`, because it does not certify the *project*, it certifies the
**product guarantee**. Every part is built a second time against a throwaway
copy of the measured tree with `.cache`, `exports`, `.history` and `.git`
excluded, so the second cache is genuinely cold, and the two builds are
compared for **exact** equality (not a tolerance):

1. the `cache_key`,
2. `<key>.acm` and `<key>.faces.u32` where present,
3. `volume_mm3` / `mass_g` / `area_mm2`,
4. the **SVG** drawing, for script parts.

A divergence names *which* artefact and the first differing byte offset
("`.acm` differs at byte 12"). A `pass` row's `details.compared` lists what it
actually looked at, so an artefact neither build wrote is never counted as
agreement. A part that will not build makes its row an `error` — "we do not
know whether this part is deterministic" — not a pass and not a fail.

**DXF is excluded by name**, as one `skip`/`not_byte_stable` row: `ezdxf`
stamps `$TDCREATE` and fresh `$FINGERPRINTGUID`/`$VERSIONGUID` into every
document, so an equality assertion over it would fail on every run. The row's
hint names the prerequisite (adopting ezdxf's fixed-date / `CONST_GUID` path in
the drawing handlers).

It is the one row in the whole report marked **`strict_exempt: true`**, and it
is the only reason that field exists. `--strict` asks *"is anything unmeasured
that could have been measured"*; this skip is unconditional — no project can
make it pass — so counting it would make `--strict --verify-determinism` red
forever and tell a reader nothing. The row stays visible, with its reason and
its hint, and in the `skipped` count; only the derived verdict leaves it alone.

---

## The agent and browser surfaces

**Tool.** `run_checks {project, ref?, stages?, strict?, budget?, proposal?}`
returns the same report as data. A red check is **data**: the call returns
normally and only the harness raises (unknown project → `not_found_error`; an
unknown stage, an **empty** `stages` list, a non-finite `budget` or a `ref`
without git → `validation_error`; a terminal proposal → `conflict_error`).
`stages: []` is refused rather than read as "all four" — it is falsy, and
guessing which of the two opposite meanings a caller wanted is worse than
saying so. (The direct `CheckRunner.run(stages=())` contract is unchanged and
selects **none**; the boundary is where the ambiguity is refused.)

**Routes** (under `/api`):

| | |
|---|---|
| `POST /projects/{proj}/checks` | body whitelisted to `{ref, stages, strict, budget, proposal}` → the report. A **red project is an ordinary 200**: only "no verdict at all" is an HTTP error. |
| `GET /projects/{proj}/checks` | the last report **this process** produced for the project; `404` when there is none. In memory, bounded to the 8 most recent projects — never persisted. |
| `GET /projects/{proj}/checks?proposal=<id>` | the **durable** record posted to that proposal; `404` when nothing was posted. |

**Event.** Every completed run — from the CLI, the tool or the route, including
a red one and a budget-truncated one — publishes
`check_finished {project, ref, status, exit_code, summary, duration_s}` on the
WebSocket channel. It is deliberately **not** `project_changed`: measuring a
project is not changing it, so it triggers no history snapshot.

---

## Posting to a proposal

`--proposal <id>` (or `run_checks {proposal}`) attaches the report to a change
proposal (PRD-002). It is stored durably as
`.history/agentcad/proposals/<id>/checks.json` — an envelope
(`schema, posted_at, posted_by, actor_kind, source, head, status, exit_code,
complete, strict, summary, stages[], report`) wrapping the report verbatim —
appended to the proposal's append-only `audit.jsonl`, and announced as
`proposal_changed {reason: "checks"}`.

`head` is the commit **the report says it measured** (`source.sha`), never the
head at posting time. Noticing when those two have drifted apart is the gate's
entire job.

**A dirty working tree cannot be posted.** A working-tree run records the tree's
current commit as `source.sha` *and* `dirty: true` beside it, so posting it
would claim the committed bytes were measured when an uncommitted edit means
they were not — commit `C` has the broken drawing, the local fix makes the run
green, the gate passes, and the merge lands `C`. Posting such a report is
refused — nothing written, nothing audited, the gate stays `skipped` — and the
refusal reaches you differently on each surface:

- **CLI** (`--proposal`/`--auto-proposal`): the message on stderr and **exit
  2**. The report files are still written; only the post did not happen.
- **Tool and route** (`run_checks {proposal}`, `POST .../checks`): the refusal
  is a **receipt, not an error**. The report comes back normally — HTTP 200, no
  top-level `error` — carrying `posted: {id, ok: false, error: {...}}` and a
  `NOT posted` line in `warnings`. Discarding minutes of kernel work to say
  "not posted" would help nobody, so **read `posted.ok`**: `status` and
  `exit_code` describe the geometry and say nothing about the post.

Commit or stash and re-run. A `--ref` run is unaffected: it measured the commit
it materialized, and its `dirty` flag describes a tree it deliberately did not
measure. CI runners are always clean, so the Action path never meets this.

`--auto-proposal` posts to the one **active** proposal whose source branch is
the branch that was checked. No match is a warning and the check's own exit
code; more than one match is exit 2 — guessing which proposal a verdict belongs
to is worse than refusing. A **terminal** (merged/closed) proposal is refused:
it is never measured again.

### The `checks` gate

The posted record is read by a gate provider named `checks`, which replaces
PRD-002's built-in placeholder of the same name and appears in
`proposal_get`'s `gates` list.

| State | When |
|---|---|
| `skipped` | **nothing was ever posted** — no record *and* no `checks_posted` line in the proposal's audit log (byte-identical to the placeholder) — or the posted report measured nothing at all |
| `pass` | a **complete, green** report against the source branch's **current** head |
| `fail` | the report is red · certifies a **different** head · did not finish (`complete: false`) · will not parse · **is not a valid record** · **is missing while the audit says it was posted** · could not be evaluated |

**A deleted record is a `fail`, not a `skipped`.** `checks.json` is an ordinary
file; the audit log is append-only and nothing in this feature can remove a line
from it. So the gate asks the audit first: a proposal it names as checked is
never merge-permissive again, and deleting a red report says *re-run*, not
*nothing to see*. A proposal nobody ever posted to still skips — that is the one
permissive branch, and it is what "posting is how a proposal opts in" means.

**A posted record is validated before a verdict is derived from it** — against
`CHECKS_SCHEMA`, against `validate_report` for the report it embeds, and
field-by-field against that report: `status`, `exit_code`, `complete` and `head`
are *copies* of the report's own values, so a mismatch means one of the two was
edited. A hand-written `{"head": …, "status": "green"}` is a `fail`
(`reason: invalid_record`), not a pass.

**`pending` is deliberately absent.** `ProposalManager.merge()` blocks a gate
whose state is `fail` and *nothing else*, so `pending` is merge-**permissive**:
a green posted against an older commit would have waved through content it
never measured. A moved head is therefore a `fail` whose summary says *re-run*
— the same answer PRD-003's `specs` gate gives, for the same reason.

**Posting is how a proposal opts in.** Absent evidence is `skipped`, so this
gate can only ever block a proposal someone posted a check to; from the first
post on it is fail-closed. That asymmetry is deliberate: an optional CI report
is not a declared-but-unmeasured spec, and PRD-003's fail-closed `specs` gate
already covers the latter. The `checks` gate is **evidence**; the `specs` and
`validation` gates are enforcement, and they re-measure on every `merge()`.

The provider never raises: both of its except-branches answer `fail`, because
`ProposalManager` degrades a *raising* provider to `pending`, which is the
outcome this gate exists to avoid.

---

## Running it in GitHub Actions

The composite action is
[`.github/actions/agentcad-check`](../.github/actions/agentcad-check/README.md)
(its README has the full input/output tables, which version with `action.yml`).
The minimum a CAD repository needs:

```yaml
name: Geometry CI
on: [push, pull_request]
permissions:
  contents: read
jobs:
  geometry:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: n3r/AgentCAD/.github/actions/agentcad-check@main
        with:
          project: .
```

The action sets up uv (with its cache), installs the OCCT system libraries on
Linux, installs agentcad, runs the same `agentcad check` you run locally,
appends `report.md` to the job summary, uploads both reports as an artifact and
re-raises the check's exit code. The summary and the artifact land under
`if: always()`, so a red check is **always** accompanied by its evidence.

This repository dogfoods it in
[`.github/workflows/geometry-ci.yml`](../.github/workflows/geometry-ci.yml):
the light bundled examples (`construction`, `prototyping`, `rocketry`,
`fasteners`) on every push and pull request, and `engine` (33 parts, 65
instances) on the nightly schedule — the same split `ci.yml` makes for its
exhaustive suite.

### Runner requirements

- **Disk:** the OCCT wheels are ~2 GB installed, plus the uv cache — budget
  **8 GB free**.
- **Memory:** ~0.5 GB per kernel worker on top of the runner baseline, so
  `pool-size: 1` (the default) on a standard 2-core / 7 GB runner.
- **OS:** `ubuntu-latest` and macOS runners are supported. On Linux the action
  installs the six system libraries OCCT needs even headless — `libgl1
  libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1`, the same list
  `.github/workflows/ci.yml` uses. Windows runners are untested in v1.

### Caching

| Layer | Mechanism | v1 |
|---|---|---|
| L1 — the ~2 GB OCCT wheels | `astral-sh/setup-uv` `enable-cache`, keyed on `uv.lock` | **yes** — the layer that matters |
| L2 — the resolved `.venv` | `actions/cache` on `.venv` | no — a warm uv cache makes the install cheap; measure first |
| L3 — AgentCAD's geometry cache | `actions/cache` on the project's `.cache/` | no — the PRD's open question; add it yourself if a cold rebuild dominates |
| L4 — apt packages | — | no; fast, and apt cache restore is fragile |

To try L3, cache `<project>/.cache/` keyed on the part scripts: a hit skips the
rebuild of every part whose `cache_key` is unchanged.

### What a repo-hosted project should `.gitignore`

```gitignore
.history/
.cache/
exports/
```

`.history/` is AgentCAD's own per-project git directory, `.cache/` holds
derived geometry and `exports/` holds generated STEP/STL/drawings. None belongs
in the host repository; all three are rebuilt by the check.

### Trust model

**A check executes the project's part scripts.** They are arbitrary Python, run
with the permissions of the workflow. Since PRD-006 the kernel worker confines
itself on Linux too (Landlock + seccomp: no network, writes only in the
checkout's project roots and a private temp dir), as it already did on macOS
through a `sandbox-exec` profile — **but the runner's kernel decides whether it
can**. A runner image without Landlock in its boot `lsm=` list, or below ABI 3,
confines nothing and reports `off`; you would not see that in a green check.
So treat Linux CI as running under the same trust model as `pytest` on the same
repository, with the confinement as a second line rather than the argument.

Therefore: use **`pull_request`, never `pull_request_target`**, hand the job no
secrets, and keep `permissions` read-only. A fork's pull request otherwise runs
a stranger's code with your token.

---

## Where the code lives

| | |
|---|---|
| `agentcad/core/checks.py` | The report shape, the validator, the markdown, `CheckRunner` (the four stages, `--ref`, determinism), `CheckStore`, the posting API and the gate provider. Imports no `OCP`/build123d — asserted by a test. |
| `agentcad/cli.py` | `cmd_check` and the flags. |
| `agentcad/core/tools_run_checks.py` | The tool pack: `run_checks`, `service.checks`, `install_checks_gate`. **Not** `tools_checks.py` — see `AGENTS.md`. |
| `agentcad/server/routes_checks.py` | The route pack. |
| `.github/actions/agentcad-check/` | The composite action, its README and the report → step-outputs helper. |

Tests: `tests/test_checks.py` (the pure layer), `test_checks_pipeline.py` (the
stages), `test_checks_ref.py` (containment and determinism),
`test_checks_cli.py` (the command), `test_checks_api.py` (tool, routes,
event), `test_checks_gate.py` (posting and the gate),
`test_geometry_ci_action.py` (the Action and the workflow), and
`test_prd004_acceptance.py` (one named test per acceptance criterion).
