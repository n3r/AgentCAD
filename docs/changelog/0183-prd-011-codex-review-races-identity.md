# 0183 — PRD-011, Codex xhigh review: races, identity binding and resource bounds

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (Opus 5)

## Summary

A second, independent review (Codex, xhigh) of PRD-011 raised 13 findings
against `661d10f` — i.e. against the tree **before** changelog 0181's fixes.
Five were already closed by that pass and are re-verified here with their
probes rather than assumed; eight were open, and this entry is those eight plus
two resource bounds the review's twelfth finding pointed at.

The through-line this time is different from 0181's. That pass was about
*evidence*: `gate: green` had to mean something. This one is about **the gaps
between two correct steps** — a hash taken here and a copy made there, an index
entry that names a tree and a tree that names itself, two writers who each read
before either wrote. Every finding below is a place where each half was right
and the pair was not.

The sharpest one was not in the review. Chasing the "two concurrent adds lose
one" finding produced a **corrupt `project.json`** instead of a lost update:
`ProjectStore._atomic_write` staged every write through one fixed
`<name>.tmp`, so two writers opened the *same* file, interleaved their bytes,
and each `os.replace`d the mixture into place. `os.replace` was atomic
throughout — the file it replaced *from* was shared.

## Already closed by changelog 0181 — verified, not assumed

| Codex | claim | verified by | result |
|---|---|---|---|
| #1 | gate inputs excluded from the content id | `probe_ghost2.py` | `gate status: red`, `publishable: False`, publish raises `9 blocker(s)` |
| #5 | a skipped stage can still be publishable | `gate.verdict([make_stage("presets", [])])` | `{publishable: False, blockers: ['presets']}` |
| #7 | header edits read `ok` | `v_prov.py` | A2/A3/A5/A6/A7 all `modified`; baseline `ok`; A4 `unverified` |
| #3 (core) | the cache is not bound to the lock | `probe_lock_vs_cache.py` | refused, naming both ids, nothing materialised |
| #12 (core) | index resource ceilings, NUL paths | `probe_recursion.py`, `probe_nul.py` | poisoned index no longer takes the next index down; NUL path reported, not raised |

Two parts of #12 needed measuring rather than asserting:

* **Embeddings/`signatures` vector size — moot, and now stated as such.** The
  whole document is size-checked **before** it is parsed, so no field inside a
  32 MB document can exceed 32 MB. There is no separate vector ceiling to add.
* **Per-package version count — real, and now bounded.** Neither the byte
  ceiling nor the package count bounds *one* package's version list. A
  **valid** minimal version entry is **405 B compact (measured)**, so 32 MB is
  **~82 800 versions of a single package**, and `format.resolve` parses every
  one of them on every search: **182 ms at 100 000 versions**, per package, per
  keystroke. `MAX_VERSIONS_PER_PACKAGE = 2 000`.

## Changes

### Identity and evidence

- **`cache.refuse_identity_mismatch`** (Codex #6). Resolution trusts the index's
  outer `name`/`version` keys; the content id proves the bytes are the bytes the
  index meant; **nothing compared either with the tree's own `package.json`**.
  An index mapping `foo@1.0.0` onto a perfectly verified tree whose manifest
  says `bar@2.0.0` installed cleanly and materialised bar's code under foo's
  provenance header — every downstream check passed, because every one was
  asking a different question. Checked at `cache.install` **and** again in
  `tools_packages._package_doc`, because a cache populated by an older build
  never saw the install-time check.
- **The receipt is versioned** (Codex #4). `RECEIPT_SCHEMA = 1` plus
  `RECEIPT_REQUIRED = (content_id, index, source)`, validated by
  `cache.receipt_problem`. `PackageManager._resolve_cached` rebuilds a
  **git-tracked** lock entry out of the receipt, so a receipt carrying only
  `content_id` produced an offline *success* that wrote `index: null,
  source: null` into both manifest maps — an install no online install would
  ever have written, which is precisely the byte-identity the offline path
  exists to preserve. An unversioned or partial receipt now reads `tampered`;
  `add` **heals** it when the tree still hashes to the id the index declares
  (the same reasoning as the interrupted-install path, which this generalises).
- **Publish hashes the copy, in staging, before promoting it** (Codex #2).
  `actual` was measured off `source`, and the copy then read `source` again —
  a mutation in that window shipped bytes B under id A, and every consumer
  would reject the "successfully published" package for ever, with nothing to
  say which side was wrong. The staging directory is hashed and compared with
  `actual` before `staging.replace(target)`, so the published tree hashes to
  its advertised id **by construction**.

### Races

- **`ProjectStore._atomic_write` stages through a random name.** One line, and
  the difference between losing a write and losing the project. **This is a
  touch to a core file outside this change's nominal file set**
  (`core/project.py`) — flagged deliberately: it is reachable from every
  concurrent writer in the codebase, not only from packages, and the
  `.staging-<rand>` idiom it now uses is `cache.install`'s and
  `LocalIndex.publish`'s already. Revert it and the corruption returns.
- **`manager.manifest_scope`** (Codex #10a) — one reentrant lock per resolved
  project path, shared by every `PackageManager` in the process, wrapped around
  `add` and `remove`'s whole read-modify-write. **In-process only, stated as
  the boundary**: it makes the server safe (one process serves every route,
  tool and MCP call) and does nothing for two CLI processes on one project,
  which is PRD-008's territory and not somewhere to invent a third opinion.
- **`LocalIndex._index_scope`** (Codex #10b) — an `RLock` *and* `fcntl.flock`,
  because publishing genuinely is multi-process: `agentcad publish` in a shell
  loop, or CI and a human. Unserialized, the second publish dropped the first
  entry *after its caller was told it had published*, leaving a package tree
  reachable by nothing — the "half-published version" state `publish` already
  refuses to write over. The lock file lives beside `config.json`, keyed by a
  digest of the index path, **never inside the index**: an index is routinely a
  git repository, and "a refused publish leaves the index byte-identical" is an
  invariant this feature's own tests assert. `flock` failure degrades to the
  in-process lock, documented rather than hidden.

### Correctness of the dependency graph

- **A yank is qualified by index** (Codex #3, tail). `withdrawn` holds
  `(index, version)` pairs and `_resolve_cached` matches on the **receipt's**
  index, so index A withdrawing its own `1.0.0` can no longer veto a warm cache
  entry installed from index B. A yank is a statement a publisher makes about
  their package; it binds their package only. The reverse still holds: the
  index that *did* withdraw it still suppresses its own entry for a range.
- **`manifest_merge.package_problems`** (Codex #13), called from
  `merge._integrity`'s call site so a violation surfaces as ordinary merge
  integrity damage rather than a new mechanism. `packages` and `packages_lock`
  merge as two independent maps — correct per map, and they are two halves of
  **one** fact. Theirs' requirement with ours' lock is a clean merge and a
  dependency **no branch authored**, and nothing downstream notices: `use_part`
  reads only the lock, and the lock verifies against its own content id
  perfectly well. Three kinds: `package_requirement_violated`,
  `package_index_mismatch`, `package_lock_orphan`.

### Reach and honesty

- **`GitIndex(subdir=…)`** (Codex #8). A git index was `<checkout>/index.json`,
  full stop — which serves a repository that is an index *and nothing else*,
  and not one that ships an index **alongside its source**. This repository is
  the second kind (`catalog/index.json`), so AgentCAD's own repo was not usable
  as a git index while the acceptance test said it was, by copying `catalog/*`
  to the root of a synthetic repo — a test that proved the fixture rather than
  the product. `subdir` is validated as a safe relative path; the clone still
  targets the repository root and only the index lookup moves.
  `test_THIS_repository_is_usable_as_a_git_index_with_subdir` drives the real
  layout and asserts every advertised content id is the id of the tree the git
  index serves.
- **`use_part` validates overrides before it writes** (Codex #11). A preset or
  `params` the part cannot accept used to `create_part` and then `delete_part`,
  publishing two `project_changed` events — so one undo after a **failed**
  `use_part` resurrected a transient part the user never successfully made. The
  overrides are now checked against the inspected PARAMS spec first, which is
  also *stronger* than `set_params` alone (`set_params` stores a numeric raw
  and the worker clamps it at build; `validate_configuration` catches the range
  and enum violations — the gate's own `presets` reasoning).

  **Where the boundary is, precisely.** What this closes is the *validation*
  failure — a preset or `params` the part cannot take — which is the one a user
  actually meets, and it now writes nothing at all. A failure **after**
  `create_part` that is not a validation failure (a kernel crash inside
  `set_params`, a disk error) still publishes two events and still rolls the
  part back, so an undo there can still resurrect it. That path is unchanged
  and is not claimed to be fixed; closing it needs the same per-operation
  history seam as the success path.

  The **success** path is still two undo steps, and that is documented rather
  than composed: the only suppression seam is `history.in_restore`, which is
  process-global and would silently drop a concurrent caller's snapshot. A
  per-operation seam is PRD-012-era work, recorded as a follow-up.
- **The gate warns when a swept parameter moves no geometry** (Codex #9,
  generalised). A spec reads the *built object*, so a `build(p)` that ignores
  its parameters passes every spec at every variant — every variant is the same
  solid. The gate chose the values, so it can see what specs cannot.
  **Reported, never enforced**, on the same rule as `is_valid=false` for
  imported geometry: a hard red needs an escape hatch for a legitimately
  cosmetic parameter, and `handle_inspect` normalises the PARAMS spec and drops
  unknown keys, so a `"geometric": false` marker would be a kernel change.
  Measured before shipping: across all nine catalog packages and their **16
  swept parameters, zero** built to the same volume at both extremes, so this
  warns on nothing that is currently correct.

### Prose and tests, after the verifier's SHIP pass

- **Two grep-shaped tests became behavioural.**
  `test_the_merge_orchestrator_reports_it_as_integrity_damage` asserted
  `inspect.getsource(...)` contained `"package_problems"`; it is replaced by
  three real merges in `test_packages_index.py` — the hybrid blocking with
  `package_requirement_violated` in `validation.integrity`, an ordinary merge
  of a package-bearing project coming back `integrity: []`, and a
  remove-versus-bump conflicting at **both** maps with no extra row.
  `test_the_stores_staging_file_is_unique_per_writer` was a source grep plus a
  single-writer smoke test; it is replaced by a two-writer hammer with a
  spinning reader (50 writes each, distinct 60 kB/90 kB documents) asserting
  zero corrupt reads, zero writer errors and no surviving staging files, plus
  a failure-path test. Checked by sabotage: removing the lock makes the
  concurrency test fail with *"2 adds held the manifest at once"*, and the old
  substring test would have passed.
- **`test_two_concurrent_adds_both_land` no longer passes for the wrong
  reason.** It gated on a barrier inside `store.manifest` with a 5 s timeout —
  and asserting the barrier had actually been reached revealed that it never
  was: **the lock makes that interleave impossible by construction**, so the
  test had been silently timing out and proving nothing. It now starts both
  threads on a barrier *before* `add`, instruments `manifest_scope` and asserts
  maximum occupancy of exactly 1.
- **`_json.py`'s ceiling arithmetic re-measured** against the verifier's
  method, which grows the real `catalog/index.json` one package at a time and
  takes the marginal cost — so the number includes the package wrapper and
  separators a real entry costs, rather than a `{version: entry}` pair weighed
  in isolation. Per entry **2 236 B** at `indent=2` / **1 124 B** compact (was
  2 178 / 1 089); minimal pathological package record **20 B** (was 23), so
  32 MB holds **1.68 M** (was 1.46 M) and the count ceiling fires at **1.00 MB**
  (was 1.1); minimal *valid* version entry **523 B** (was 405), derived by
  stripping a real entry key by key for as long as `validate_index` accepts it
  rather than hand-building one, so 32 MB is **~64 200** versions of one
  package (was ~82 800). `format.resolve` over 100 000 versions: **168 ms**.
- **Three refusals stopped misdiagnosing.** `GitIndex.entries` answered "has
  never been cloned from <url>" for *every* missing document, which with
  `subdir` is routinely wrong — the clone is there and the subdirectory is a
  typo. It now distinguishes never-cloned, subdir-absent, subdir-not-a-
  directory, and cloned-but-no-`index.json` (which points at `subdir` as the
  fix). And the identity check's not-a-package case answered "package.json is
  unreadable: FileNotFoundError: [Errno 2] … /abs/path/package.json" — an
  errno and a filesystem path in a message an agent may hand to a user; it is
  now a structured refusal naming the relative expectation.

## Files

- `agentcad/core/project.py` — **core touch**: `_atomic_write` stages through a
  random name and cleans up on failure.
- `agentcad/core/merge.py` — `package_problems` in the post-merge integrity
  report (one call plus an import).
- `agentcad/core/manifest_merge.py` — `package_problems`.
- `agentcad/core/packages/cache.py` — `RECEIPT_SCHEMA`, `receipt_problem`,
  `refuse_identity_mismatch`, receipt healing, unique receipt staging.
- `agentcad/core/packages/indexes.py` — staging re-hash, `_index_scope`,
  `index_lock_path`, `GitIndex.subdir`/`checkout`, per-package version ceiling.
- `agentcad/core/packages/manager.py` — `manifest_scope`, index-qualified
  `withdrawn`, lock-bound offline `require`.
- `agentcad/core/packages/gate.py` — `_report_indifferent_parameters`.
- `agentcad/core/packages/_json.py` — `MAX_VERSIONS_PER_PACKAGE`, measured.
- `agentcad/core/tools_packages.py` — pre-validated overrides, identity-checked
  `_package_doc`, `use_part` description.
- `AGENTS.md`, `CLAUDE.md`, `docs/packages.md`, `docs/agent-api.md`.
- `tests/test_packages_{index,tools,publish,cache,gate,git_index}.py`,
  `tests/test_manifest_merge.py` — the new tests and two under-specified
  fixtures (`package.json` stubs that named no version, which the identity
  check correctly refuses).

## Verification

Every open finding reproduced before the fix and re-run after.

**Codex #2 (copy race).** Before: `publish re-hashes the COPIED tree? False`,
`_copy_inventory verifies each file's hash? False`. After: a `_copy_inventory`
that mutates the staged tree makes publish raise *"changed while it was being
copied … the gate and this publish measured `sha256:…`, and the copy hashes to
`sha256:…`"*, the index tree is byte-identical afterwards, and no staging
directory survives.

**Codex #4 (receipt).** Before: `verify with a content_id-only receipt: ok`,
`offline lock would record: index=None source=None`. After: `tampered`
(`receipt_schema`), the offline resolve refuses, and `add` heals the entry back
to `ok` with `schema: 1`.

**Codex #6 (identity).** Before: *"cache.install accepted foo@1.0.0 for a tree
that says bar@2.0.0: INSTALLED"*, and the cached `package.json` read
`bar 2.0.0`. After: *"the fetched tree filed as foo@1.0.0 says it is
'bar'@'2.0.0'"*, nothing installed.

**Codex #3 tail (cross-index yank).** Before: `resolve(^1.0.0) REFUSED: no index
or cache entry provides widget_good ^1.0.0` — index A's yank vetoing index B's
package. After: `resolve(^1.0.0) -> index_b offline: True`.

**Codex #10a (manifest race).** The forced interleave (both threads read before
either writes) before the fix did not merely lose a package — it raised
`ValidationError: project.json is corrupt: Extra data: line 26 column 2`. After:
`results: {'alpha': 'ok', 'beta': 'ok'}`, `manifest packages: ['alpha', 'beta']`,
`LOST: []`, manifest parses.

**Codex #11 (undo).** Before: a bad preset produced `use_part project_changed
publishes: 2` on the failure path (create + delete). After: **0** on the
failure path, the part does not exist, and the refusal names the parameter
(*"params.length: 999999.0 is above max 80.0"*). The success path is 2 with
overrides and 1 without — both pinned by tests, both documented.

**Codex #13 (merge hybrid).** Before: `a post-merge package validator exists?
False`, `_integrity() sees it? []`. After: `package_requirement_violated` and
`package_index_mismatch`, both naming the package and both halves.

**Codex #8 (git subdir).** Before: `GitIndex(subdir=...) -> TypeError`. After:
a bare clone of a working tree shaped like this repository, served through
`subdir: "catalog"`, offers the same package set as `catalog/index.json` and
every advertised content id equals `content_id(index.fetch(name, version))`.

**Codex #9 (parameter indifference), measured across the real catalog:**

```
din625:         14 pass rows, 0 no-op param(s) of 1
extrusion_2020:  7 pass rows, 0 no-op param(s) of 1
extrusion_3030:  7 pass rows, 0 no-op param(s) of 1
iso4014:        17 pass rows, 0 no-op param(s) of 3
iso4762:        20 pass rows, 0 no-op param(s) of 3
iso7380:        17 pass rows, 0 no-op param(s) of 3
nema17:          6 pass rows, 0 no-op param(s) of 1
nema23:          6 pass rows, 0 no-op param(s) of 1
thread_insert:  13 pass rows, 0 no-op param(s) of 2
```

Nine packages, 16 swept parameters, zero false positives — which is what makes
the warning safe to ship and would have made a hard red tempting. It is still
a warning, for the escape-hatch reason above.

Targeted suites, on this working tree:

```
uv run pytest tests/test_packages_api.py tests/test_packages_cache.py \
  tests/test_packages_cli.py tests/test_packages_format.py \
  tests/test_packages_from_step.py tests/test_packages_gate.py \
  tests/test_packages_git_index.py tests/test_packages_index.py \
  tests/test_packages_ocp_free.py tests/test_packages_publish.py \
  tests/test_packages_tools.py tests/test_prd011_acceptance.py \
  tests/test_catalog.py tests/test_manifest_merge.py
777 passed in 112.38s (0:01:52)
```

…and the suites for the two core files this entry touches, run separately
because they are slow:

```
tests/test_merge.py tests/test_project.py tests/test_history.py tests/test_locks.py
87 passed in 147.74s
tests/test_proposals.py tests/test_service.py tests/test_versioning_api.py
90 passed in 150.91s
```

Counted from the diff (`--collect-only` per file, at `HEAD` and now):

| file | HEAD | now | Δ |
|---|---|---|---|
| `test_packages_index.py` | 42 | 69 | +27 |
| `test_packages_gate.py` | 98 | 112 | +14 |
| `test_packages_tools.py` | 51 | 64 | +13 |
| `test_packages_publish.py` | 30 | 38 | +8 |
| `test_packages_git_index.py` | 37 | 43 | +6 |
| `test_packages_cache.py` | 43 | 47 | +4 |
| `test_manifest_merge.py` | 78 | 82 | +4 |
| `test_packages_api.py` | 29 | 32 | +3 |
| `test_packages_ocp_free.py` | 14 | 15 | +1 |
| unchanged (`cli`, `format`, `from_step`, acceptance) | 208 | 208 | 0 |
| **total** | **630** | **710** | **+80** |

That **+80** supersedes changelog 0181's **+51**, which counted the same twelve
files before this pass added to them; 0182's `test_catalog.py` **+44** is
unchanged. **Full suite on this tree: `make test` — 3235 passed, 1 skipped**
(`uv run pytest -q -n 2 --dist loadscope`, 1568.14 s, exit 0). Prior
measurements, kept as provenance for different bytes: 3193 (0182, mid-review),
3206 (after round 2), 3228 (after round 3).

**One anomalous run, recorded rather than discarded:** the first full-suite run
on this exact tree finished in an unusually short 12 m 56 s with **4 failures,
all in the `engine` example** — `project.json` had vanished from the test's own
freshly-copied fixture *between* `open_project` (which validated it) and the
first `get_part`. Standalone the module passes (140.7 s), the immediate rerun
of the full suite is the green 3235 above at the normal ~26 m, concurrent
pytest tmp-root pruning was ruled out (no other session started during the
run), and nothing in this diff deletes outside its own staging directories.
Cause unestablished; log preserved at the session scratchpad as
`fullsuite-final11.log`. If an `engine` fixture ever reports a missing
`project.json` again, treat it as the second occurrence of a real bug, not the
first of a fluke.

## Notes

- **Two fixtures were under-specified, not two tests wrong.**
  `test_packages_cache.py`'s and `test_packages_index.py`'s `package.json`
  stubs named a package and no version, so the identity check refused them —
  correctly. They now carry both halves, and the index fixture takes its
  version from the directory it already encodes it in.
- **What the in-process manifest lock does not do**, stated so nobody reads it
  as more: two `agentcad` CLI processes writing one project are still
  unserialized. The store's random staging name means that race now *loses a
  write* rather than corrupting the manifest, which is the difference that
  mattered; a cross-process protocol for projects belongs with PRD-008's write
  guard, not here.
- **Two more fixed-`.tmp` writers survive, deliberately unfixed — pick them up
  next.** `ProjectStore._atomic_write` was not the only place with that idiom:

  * `agentcad/kernel/worker.py:403` — `_atomic_write`, `target.name + ".tmp"`.
    `worker.py` is on the **never-edit** core list, so it is not something to
    change in a review round for a PRD it does not belong to.
  * `agentcad/config.py:38` — `save_config`, `path.with_suffix(".tmp")`. Two
    processes writing `~/.agentcad/config.json` at once (a CLI and a server)
    can interleave into one staging file exactly as the store did.

  Both are **pre-existing and outside this PRD's scope**, and neither is
  hypothetical: `tests/test_packet.py:1166`
  (`test_two_concurrent_builds_produce_one_packet_with_whole_assets`) already
  records that concurrent builds wrote over *"each other's fixed-name .tmp
  files"* and could publish a URL for a file the other build had unlinked —
  the same failure, found the hard way, in a different subsystem. The fix is
  the one-line `secrets.token_hex` staging name this entry applied to
  `project.py`, and it should be applied deliberately with its own tests
  rather than smuggled in here.
- **Follow-ups recorded rather than improvised:** single-snapshot composition
  for `use_part` (needs a per-operation history seam, and it is also what would
  close the non-validation failure path above); a `geometric: false` marker in
  the PARAMS spec so parameter-indifference could become a red row (needs
  `handle_inspect` to stop dropping unknown keys); and Codex #9's root cause —
  specs cannot see parameters — which is a spec-language question, not a gate
  one.

## Windows CI round (post-PR)

PR #15's first CI run failed only on `pytest (windows-latest, portability)`,
three classes, all test-side and all previously-solved traps:

- `os.geteuid()` does not exist on Windows and was called at decorator
  evaluation, breaking collection — now the `test_specs.py` idiom
  (`getattr(os, "geteuid", lambda: 1)`) plus `os.name == "nt"` in the skip,
  because chmod 0o000 does not make a file unreadable there either.
- `shutil.which("git")` answers `...\git.EXE` on Windows, so
  `argv[0].endswith("git")` was false — the assertion now compares the
  basename case-insensitively.
- `shutil.rmtree` of a bare fixture repo hit `WinError 5` on git's read-only
  `objects/` — the PRD-004 trap, fixed with `test_checks_ref.py`'s
  `_rmtree_repo` (clear the bit and retry), copied with attribution.

`agentcad/core/packages/indexes.py`'s `fcntl` import was already guarded (the
cross-process index lock degrades to the in-process RLock on platforms
without it, documented in its docstring) — verified, not changed.

**Second Windows round: the checkout was not byte-stable.** Every catalog
package "does not hash to its advertised id" on windows-latest and nowhere
else — Windows git defaults to `core.autocrlf=true`, which rewrites LF → CRLF
at checkout, and a content-addressed index cannot survive a line-ending
rewrite. `_git.run` now pins `-c core.autocrlf=false` on every invocation
(`-c` beats every config scope, so a user's global gitconfig cannot re-break
it). This was a real product bug, not a test bug: any Windows user's git
index would have refused every install with a content-id mismatch.
