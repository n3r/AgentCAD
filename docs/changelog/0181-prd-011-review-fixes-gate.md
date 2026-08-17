# 0181 — PRD-011 review fixes: the trust chain, the gate, and deleting the fan-out

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (Opus 5)

## Summary

An independent review of PRD-011 returned CHANGES-REQUIRED with one
through-line: **`gate: green` is not currently load-bearing evidence.** This
entry is the trust-chain and gate half of the response (a second pass covers
catalog content). Every finding below was **reproduced with a probe before it
was fixed and re-run after**, and the before/after output is quoted rather than
summarised.

Six ways the chain was breakable, each of them a place where a document was
believed instead of a measurement:

* `use_part` verified the cached tree against its own **receipt** and then
  stamped the **lock's** content id into the header, comparing neither with the
  other — so two indexes publishing the same `name@version` with different
  bytes produced a header claiming index B's id over index A's bytes, status
  `ok`;
* eleven JSON readers restated `(OSError, ValueError, UnicodeDecodeError)`,
  which does not include **`RecursionError`** — a ~400 kB `index.json` took an
  unhandled exception out through `search`, `resolve` and `entries`, so one
  poisoned index stopped every healthy index *behind* it;
* the gate measured the **manifest** while the content id measured the
  **inventory**, so a part declared at `parts/x.tmp` was proved by every stage
  and shipped by none — a scriptless package advertised green;
* a stage that produced **zero rows** was invisible to the verdict;
* the gate hashed the tree once and then let every stage read it **live**;
* `LocalIndex.publish` read `report["publishable"]` instead of deriving it.

And one decision taken rather than deferred: the build **fan-out is deleted**,
with the numbers that made it a deletion.

## Changes

### The trust chain

- **`core/packages/cache.py` — `require_verified(name, version,
  expected_content_id=…)`** (C1). Returns `(path, measured_id)` and refuses
  when the measured id is not the id the caller is materialising against. A
  receipt only says *these bytes are the bytes that were installed* — it is
  written by whatever installed the tree — so receipt verification cannot tell
  two different `widget_good@1.0.0` trees apart. `require` is now a thin
  wrapper over it, so no existing caller changed.
- **`core/tools_packages.py` — `materialize` binds to the lock.** It reads
  `packages_lock[name].content_id`, refuses a lock entry that carries none
  (fail-closed, on `_locked`'s rule: an unbound cache is a tree nobody
  recorded), verifies the cache against it, and stamps the **measured** id into
  the provenance header. The two are equal because the verification refused
  otherwise; stamping the measurement is what makes that equality a fact in the
  file rather than an assumption.
- **`core/packages/_json.py` (new) — one safe JSON reader** (C2, m10). Byte
  ceiling checked **before** the parse (`MAX_JSON_BYTES` 4 MB;
  `MAX_INDEX_BYTES` 32 MB; `MAX_INDEX_PACKAGES` 50 000), and `RecursionError`
  in the caught set. `read` / `read_object` / `read_optional` / `loads`, all
  raising only `ValidationError`. Adopted at every one of the eleven sites the
  review named: `indexes.entries`, `indexes._read_json`,
  `indexes._preset_names`, `tools_packages._package_doc`,
  `tools_packages._preset_params`, `gate._read_package`, `gate._stage_presets`,
  `gate._presets_for`, `cache.read_receipt` (whose docstring said "never
  raises" and was wrong), `provenance.parse`, and — transitively, since they
  catch `ValidationError` — `search.search` and `manager._resolve_in`.
- **`core/packages/provenance.py` — `header_sha256`** (M8). A new payload field
  digesting the block, so an edit confined to it reads `modified`. `split()`
  recovers the block the way `strip()` recovers the body. The body is covered
  transitively, because `script_sha256` is one of the digested keys — which
  keeps `header()` a pure function of one argument and AC3's byte-identical
  re-materialisation untouched. A header with **no** digest is `unverified`,
  never `ok`. Stated in the docstring as an **integrity check, not
  authentication**: there is no secret, so it detects edits and not a
  determined forger.

  **The digest covers the payload AS PARSED** — every key it carries, not the
  ones `DIGESTED_FIELDS` names — plus the comment lines **verbatim** and
  whether the blank separator was there. The first version of this fix covered
  only the named fields and the un-commented note text, which left three edits
  still reading `ok` (the adversarial verifier's v_prov A2/A3/A6): an unknown
  payload key smuggled in beside them, note lines re-indented, and the
  separator deleted. `parse()` therefore carries the raw payload under
  `"payload"`; `describe()` — the shape that reaches an API consumer — does
  not.
- **`core/packages/manager.py`, `lockfile.py`, `frontend/js/library.js` — an
  omitted argument does not overwrite a declared one** (M7). `add` reads the
  declaration *before* it resolves, so a `~1.0.0` pin is the requirement that
  gets resolved; `lockfile.add` keeps an existing `version_req` when the caller
  passes none; the result carries `requirement_change` (null when nothing
  moved) and the Library dialog both sends the declared requirement and toasts
  any change.

### The gate

- **`gate.py` — the stages read a snapshot** (M10). `_read_package(cell)` now
  copies the inventoried files into `<cell>/.package-snapshot/`, hashes **that
  copy**, and points `self.source` at it; `self.origin` keeps the real
  directory for the closing re-hash. The published id is therefore the id of
  the bytes the stages consumed, structurally rather than carefully. The copy
  is skipped when the tree breaks the published ceilings — that is already a
  red `format` row, and copying a hostile amount of data to report a size
  problem is the wrong trade.
- **`gate.py` — `_format_part_files` measures the inventory** (C4). A declared
  payload path that is not in `self.inventory` is a red `format` row naming
  `content.IGNORED`; every inventoried `parts/*.py` that no part declares is a
  red row too. `format.validate_package_manifest` refuses an ignored part path
  at the manifest layer as well, so an author sees it against the field they
  typed.
- **`gate.py` — `verdict` blocks a stage that produced no rows** (C5), unless
  it carries an exempt reason. `STAGE_SKIP_EXEMPT` became **(stage, reason)
  pairs**, closing a latent hole where any stage emitting `not_declared` would
  have been exempted by a string it does not own. `_stage_presets` returns the
  disclosed `presets:no_presets_declared` skip when the document declares no
  configuration, and `_stage_policy` emits a `pass` row per part when a policy
  returns nothing (so a clean policy is not a zero-row stage).
- **`gate.py` — a preset that applies but does not build is a `fail`** (M11).
  The row read "applied and built" with `built: false` in its own details.
- **`indexes.py` — publish re-derives the verdict** (M9). New
  `_refuse_an_inconsistent_report`: every gate stage exactly once, a summary
  that is `specs.summarize` over the report's own rows, a status
  `specs.report_status` implies (a strict report may be red where its rows are
  not, and nothing else), and then `gate.verdict(rows)` + `complete` instead of
  `report["publishable"]`. Same two functions `finalize_report` uses — no
  second arithmetic.
- **`gate.py` — `validate_gate_report` catches duplicate stage names** (m16),
  and its docstring now spells out the exact-prose coupling to
  `checks.validate_report`: which test contains it, and that editing the
  PRD-004 message means editing both.
- **`indexes.py`, `cli.py` — an incomplete run is refused honestly** (m15).
  "0 blocker(s) … fix the package" became "was not fully measured … re-run the
  gate to completion", with `report["warnings"]` in the message, in
  `details.warnings`, and printed by the CLI.

### Smaller, same pass

- **`content._lexical_parts` refuses C0 control characters** (m9). A NUL
  escapes nothing, but `os.stat` raises `ValueError: embedded null character`,
  which nothing in this subpackage catches — an **unauthenticated 500** on the
  preview route, and a hostile `index.json` that took `resolve` down before the
  next index got its turn.
- **`_git.validate_url` checks the ssh host** (m12), and `validate_ref` is new.
  `ssh://-oProxyCommand=…/x.git` starts with `s`, passes every other rule, and
  git hands the host to **ssh**, where a leading `-` is an option; the `--`
  separator protects git's own argv only. `--branch <ref>` sits before that
  separator, hence the ref check.
- **`manager._resolve_cached` reports `yanked: True`** for a version a
  reachable index withdrew and the caller named explicitly (m14), with the same
  warning the online path raises. The cache quietly disagreeing with the index
  it just consulted is the one thing the fallback may not do.

### Corrected after the second review pass

- **`_json.py`'s ceilings, remeasured.** The comment claimed 32 MB was
  "~100 000 index entries". Measured on the format `_write_index` actually
  writes (marginal bytes per `{version: entry}` pair, over the nine catalog
  entries): **2 178 B** at `indent=2` and **1 089 B** compact, so 32 MB is
  **~15 400** entries, or ~30 800 minified — wrong by 3.4–6.7x. Corrected in
  place, with the reason an index that outgrows it wants PRD-005's served
  registry rather than a bigger constant.

  The follow-on claim — that `MAX_INDEX_PACKAGES` is therefore dead code — is
  **true only for realistically-shaped entries**, and the two ceilings turn out
  to have genuinely different jobs. A package record can be
  `"p1":{"versions":{}}` at **23 B compact (measured)**, so 32 MB holds
  **1.46 million** of them and `search` sorts every one on every keystroke
  (50 000 alone is ~4 ms per search). The count ceiling fires on that document
  at **1.1 MB**. Bytes bound the parse; the count bounds the walk. Both
  documented with the numbers, and
  `test_the_package_count_ceiling_fires_where_the_byte_ceiling_cannot` builds a
  document over the count and under the bytes so only one ceiling can be doing
  the work.
- **`cache.install`'s refusal no longer calls a healthy cache corrupt.** When
  the cached tree verifies and the incoming install is simply a *different*
  `name@version`, the message rendered "…and does not verify (**ok**)" — a
  sentence contradicting its own parenthesis, and it misdiagnosed exactly the
  C1 scenario as corruption. That case now has its own refusal naming both
  content ids and `reason: same_version_different_content`; the genuine-failure
  branch gained the verify `reason` alongside the status.
- **The not-in-inventory row derives its cause instead of assuming it.** It
  hard-coded the `IGNORED`-pattern explanation, so a part that was simply
  missing was told to "rename it to a path the content id covers" — advice that
  does nothing. `_why_not_inventoried` answers `ignored_pattern` /
  `case_mismatch` / `absent` / `not_inventoried`, carried in
  `details.cause`. A **symlink** is deliberately not among them and the
  docstring says why: `content.inventory` refuses any symlink outright, so the
  whole tree fails to inventory and `_format_inventory` names the file first.
- **A bug this entry introduced, found by writing that test and fixed here.**
  When the tree cannot be inventoried at all, `self.inventory` is `None` and
  the inventory comparison was run against an empty set — producing one
  spurious "not in the package's content" row **per declared part**, every one
  of them naming the wrong cause, on top of the one real row. Guarded, and
  `test_an_unreadable_tree_does_not_produce_a_row_per_declared_part` holds it.
- **The `add_package` pin refusal is documented where users meet it**, not only
  in this entry's Notes: `docs/packages.md` gains "An omitted argument does not
  overwrite a declared one" with both remedies (configure the index, or pass
  `index` explicitly to re-pin), and `docs/agent-api.md`'s `add_package` row
  says it in one sentence.

### The fan-out: deleted

`ThreadPoolExecutor`, `MAX_JOBS`, `_jobs`, `_Run.jobs`, `_fan_out`, the
`--jobs` flag on `package validate` and `publish`, the `jobs` tool argument and
the `jobs=` parameter of `indexes.publish` are **gone**. `_stage_build` builds
each plan entry in order.

The implementation plan pre-registered the bar — *"under 1.5× on a 3-worker
pool, delete it"* — and three independent measurements missed it:

| measurement | speedup |
|---|---|
| `iso4762`, 9 variants, 3-worker pool (changelog 0171) | **1.55×** claimed |
| real catalog, 107 variants, five interleaved repetitions (AGENTS.md, 0180) | **1.40×** |
| review re-measurement, three runs | **1.08× / 1.40× / 1.17×** |

Only ~44% of the build stage is inside kernel calls, so **Amdahl's ceiling at
three workers is 1.42×** — the 1.55× in changelog 0171 was above the ceiling of
the thing being measured, which is itself the tell. `KernelPool._pick` routes on
`hash(affinity) % size` and `hash(str)` is `PYTHONHASHSEED`-randomised, so each
process drew a different worker assignment and any measured speedup was a
**per-process sample rather than a property**. Worse, it cost determinism where
determinism is the product: under `--budget`, `jobs=1` and `jobs=4` disagreed on
`complete` and therefore on `publishable` (reproduced 3×). And the safety
premise in the deleted docstring was false — a preset whose parameters equal a
swept variant's produces the same cache key, so two threads could collide on
one key.

**No published format or index entry shape changes.** The report is
byte-identical to the old `jobs=1` output, `exempt_skips` keeps its
`<stage>:<reason>` shape, and `_index_entry` is untouched. A test
(`test_the_fan_out_is_gone_from_every_surface`) pins the whole surface, because
a flag left behind is a flag someone re-enables.

## Files

- `agentcad/core/packages/_json.py` — **new**: the one safe JSON reader.
- `agentcad/core/packages/gate.py` — snapshot, inventory rows, zero-row
  verdict, `(stage, reason)` exemptions, preset build failure, duplicate-stage
  validation, fan-out deleted, safe JSON.
- `agentcad/core/packages/indexes.py` — re-derived verdict, report consistency,
  incomplete-run refusal, index ceilings, safe JSON, no `jobs`.
- `agentcad/core/packages/cache.py` — `require_verified`, receipt read that
  really never raises.
- `agentcad/core/packages/provenance.py` — `header_sha256`, `split`, status.
- `agentcad/core/packages/manager.py` — declaration read before resolve,
  `requirement_change`, honest `yanked` offline.
- `agentcad/core/packages/lockfile.py` — a falsy `version_req` preserves.
- `agentcad/core/packages/content.py` — C0 control characters refused.
- `agentcad/core/packages/format.py` — an ignored part path is a problem.
- `agentcad/core/packages/_git.py` — ssh host check, `validate_ref`.
- `agentcad/core/tools_packages.py` — lock-bound materialisation, safe JSON,
  no `jobs`, tool descriptions.
- `agentcad/cli.py` — `--jobs` removed from both commands, warnings printed.
- `frontend/js/library.js` — sends the declared requirement, names a change.
- `tests/test_packages_{tools,gate,publish,index,cache,api,cli,ocp_free,
  from_step}.py` — the new tests and the `jobs` removals.
- `AGENTS.md`, `CLAUDE.md`, `docs/packages.md`, `docs/agent-api.md` — the
  trust chain, the inventory rule, the snapshot, the re-derived verdict, and
  `--jobs` gone from every command line.

## Verification

Every finding reproduced before the fix and re-run after, with the reviewer's
own probes.

**C1 — `probe_lock_vs_cache.py`.** Before: `header content_id` = index B's id,
`bytes copied == corp's tree: True` (index A's), `provenance status reported:
ok`. After:

```
use_part -> validation_error: the cached copy of widget_good@1.0.0 is not the
tree this project locked: packages_lock declares sha256:c5dfda79…, and the
cache … holds sha256:c4bfe869…. It verifies against its own receipt, so this
is not corruption — it is a DIFFERENT widget_good@1.0.0, installed from
another index.
```

and no part was created.

**C2 — `probe_recursion.py`.** Before:

```
search.search over [evil, good]:            !!! UNHANDLED RecursionError
PackageManager.resolve('widget_good'):      !!! UNHANDLED RecursionError
```

After:

```
search.search over [evil, good]: OK -> ['widget_good']
PackageManager.resolve('widget_good'): OK -> good
```

The healthy index behind the poisoned one gets its turn.

**C4 — `probe_ghost2.py`.** Before: `format.validate_package problems: []`,
`gate status: green`, `publishable: True`, `PUBLISHED: ghost@1.0.0` with a
served tree of four files and **no script**. After: the manifest validator
names `parts.mount_block.file`, `gate status: red`, `publishable: False`, and
publish raises `did not pass the publish gate (9 blocker(s): …)`.

**C5.** `gate.verdict([make_stage("presets", [])])` was
`{'publishable': True, 'blockers': []}`; it is now
`{'publishable': False, 'blockers': ['presets']}`. The reachable route
(`presets.json = {"format": 1, "presets": {}}`) now reports
`presets:no_presets_declared` in `exempt_skips` and stays publishable — the
disclosure, not the silence.

**M7 — `probe_req_reset.py`.** Before: `~1.0.0` → `*`, lock `1.0.0` → `2.0.0`,
provenance of the materialised part `version_drift`. After: `~1.0.0` stays,
lock stays `1.0.0`, provenance `ok`.

**M8 — `probe_strip.py`.** Before every case read `ok`. After:

```
baseline status: ok
comment inserted inside the block  -> modified
security notice deleted            -> modified
header payload edited (index laundered) -> modified
```

**M8, second pass — `v_prov.py`.** The first fix covered only the named fields
and the un-commented note text. Before:

```
A2 extra payload field : ok        A3 indented notes : ok
A6 blank sep removed   : ok
```

After (same probe, whole output):

```
baseline: ok
A1 forged+recomputed: ok | index reads: trusted-core
A2 extra payload field: modified
A3 indented notes: modified
A4 legacy header: unverified
A5 trailing ws on note: modified
A6 blank sep removed: modified
A7 lifted onto other body: modified
pure-function identity: True
```

A1 still reads `ok` **and must**: recomputing a digest needs no secret, so that
is the documented limit of the mechanism, not a gap in it —
`test_a_recomputed_digest_still_reads_ok_and_that_is_the_documented_claim`
exists so nobody "fixes" it into a false promise. A4 confirms the legacy
`unverified` path and `pure-function identity: True` confirms AC3.

**M9 — `probe_forged2.py`.** Before: rows say the build failed,
`gate.verdict()` agrees, `publishable: True` is believed, `PUBLISHED ->
widget_good@1.0.0`; and a zero-stage report published as 2.0.0. After both
raise (`does not carry every gate stage exactly once` / the re-derived
verdict), and the index is byte-identical afterwards.

**M10.** A stage that breaks `origin/parts/block.py` mid-run and restores it
was measured by the old code (the contract stage failed) and is now invisible
to the stages, which read the snapshot; the reported `package.content_id`
equals both the snapshot's id and `content_id(good)`.

**m9 — `probe_nul.py` + `probe_route.py`.** Before:
`validate_index on the hostile doc: []`, `is_safe_relpath(NUL path) -> True`,
`resolve raised ValueError: lstat: embedded null character`, and the route
answered **500**. After: the document is refused with a named problem,
`is_safe_relpath -> False`, `resolved from: good`, and the route answers 422.

**Item 5, the cache message.** Before, for a cache holding a *different*
`w@1.0.0` than the one being installed:

```
w@1.0.0 is already in the cache at … and does not verify (ok). Refusing to
overwrite it: remove that directory and add the package again.
```

After: `… it VERIFIES, and it is a different w@1.0.0 than the one being
installed: the cached tree hashes to sha256:… and this one hashes to
sha256:…. Nothing is corrupt — two indexes have published the same name and
version with different content.`

Targeted suites, on this working tree:

```
uv run pytest tests/test_packages_api.py tests/test_packages_cache.py \
  tests/test_packages_cli.py tests/test_packages_format.py \
  tests/test_packages_from_step.py tests/test_packages_gate.py \
  tests/test_packages_git_index.py tests/test_packages_index.py \
  tests/test_packages_ocp_free.py tests/test_packages_publish.py \
  tests/test_packages_tools.py tests/test_prd011_acceptance.py \
  tests/test_manifest_merge.py tests/test_geometry_ci_action.py
718 passed in 55.05s
```

While changelog 0182's catalog pass was mid-flight, four PRD-011 acceptance
tests (AC1, AC3, AC4, AC6) failed here for a reason outside this change:
`catalog/**` had been edited and `catalog/index.json` still carried the old
content ids, so `add_package` correctly refused the drift. They were graded
against a pristine `git archive HEAD catalog` at the time and all four passed.
**On the combined tree, with 0182's regenerated `index.json`, they pass
directly** — the run quoted above needs no such workaround, and
`tests/test_catalog.py` is a further 67 passed in 45.38s.

**Full suite — `make test` — is not re-measured here.** The last measurement of
the whole suite is the prior tree's: **3151 passed, 1 skipped** (changelog
0180, 30 m 23 s, `-n 2 --dist loadscope`). Quoted as provenance and not as this
entry's count, on the 0163 pattern.

Counted from the diff rather than estimated (`pytest --collect-only` over each
file at `HEAD` and now — collected **cases**, which is what `make test`
reports, so a parametrized test counts once per case):

| this entry's twelve test files | HEAD | now | Δ |
|---|---|---|---|
| `test_packages_index.py` | 42 | 58 | +16 |
| `test_packages_gate.py` | 98 | 110 | +12 |
| `test_packages_tools.py` | 51 | 62 | +11 |
| `test_packages_publish.py` | 30 | 35 | +5 |
| `test_packages_api.py` | 29 | 32 | +3 |
| `test_packages_cache.py` | 43 | 46 | +3 |
| `test_packages_ocp_free.py` | 14 | 15 | +1 |
| the other five, unchanged | 245 | 245 | 0 |
| **total** | **552** | **603** | **+51** |

43 test functions added (54 cases) and **two removed** (3 cases) — both of them
`--jobs` tests that no longer describe anything:
`test_jobs_below_one_is_exit_two_and_not_a_traceback` (1) and
`test_jobs_one_and_jobs_four_produce_identical_reports` (2, parametrized). The
earlier draft of this paragraph said "adds 30 tests and removes one", which was
wrong on both counts and is corrected here rather than quietly restated.

With changelog 0182's `tests/test_catalog.py` (23 → 67, **+44**) the combined
tree is **+95** against 0180's 3151. Re-measure on the committed tree and
replace this paragraph — which is exactly what
`test_ac8_the_full_suite_count_is_cited` exists to force.

## Notes

- **`header_sha256` is deliberately a checksum, not a MAC.** There is no
  secret in a git-tracked file, so a determined editor can recompute it exactly
  as they can recompute `script_sha256`. The claim, stated in the docstring and
  in `docs/packages.md`, is that the block can no longer be edited *silently* —
  and the thing that binds the **bytes** remains the content id, verified
  against the lock.
- **The snapshot costs one tree copy per run**, on top of an inventory walk
  that already read every byte. At the published ceilings that is 50 MB into a
  temp directory; at realistic sizes it is tens of kilobytes.
- **`_stage_policy` gained a `pass` row per part.** A policy that finds nothing
  used to produce a zero-row stage, which the new verdict rule would have
  blocked — the row is both the fix and the more honest report.
- **Deferred, and named:** an `add_package` that resolves against a *pinned*
  index which is no longer configured now raises `NotFoundError` naming the
  pin, where it previously fell through to whichever index answered. That is
  the intended reading of "a pin is a statement about provenance", but it is a
  behaviour change a future user may meet as a new error.
