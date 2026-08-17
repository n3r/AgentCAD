# 0174 — 2026-08-16 — PRD-011 slice 8: `agentcad publish`, immutability, yank, the vendor gate

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The publish loop closes. `LocalIndex.publish(source, report)` refuses
everything that is not a green, current, redistributable, not-yet-published
package and then writes the tree and the entry atomically; `indexes.publish`
runs the gate over **every** stage first; `LocalIndex.yank` withdraws a version
without deleting a byte; and `agentcad publish <dir> --index <name>` is the
CLI, with `cmd_check`'s exit-code API. **AC5 is won here.** The published
`gate.exempt_skips` shape was decided rather than inherited (see divergences).

## Changes

- `agentcad/core/packages/gate.py` — `verdict()` now qualifies **every**
  exempt skip as `<stage>:<reason>`, row-level and stage-level alike. Slice 5
  emitted a bare reason for a row and `<stage>:<reason>` for a stage; slice 8
  publishes this list into the index entry, so the two shapes were about to
  become a format. Docstrings on `verdict`, `PUBLISH_SKIP_EXEMPT` and
  `STAGE_SKIP_EXEMPT` updated with the reason.
- `agentcad/core/packages/indexes.py`
  - `LocalIndex.publish(source, report)` — the fail-closed sequence, in the
    order each refusal has to happen: the report is a gate report and is
    `publishable` (else a `validation_error` carrying the failing rows in
    `details.checks`) → the tree **still hashes to the id the report
    measured** → the vendor gate → **immutability**. Only then does it copy
    the inventoried files into `<name>/.staging-<rand>/`, `os.replace` them
    into `<name>/<version>/`, build the entry, run `format.validate_index`
    over the whole prospective document, and rewrite `index.json` atomically.
    Anything that raises after the copy removes the tree, so a failed publish
    leaves the index byte-identical.
  - `LocalIndex.yank(name, version, yanked=True)` — flips the flag, deletes
    nothing, is idempotent (`already: true`) and reversible.
  - `LocalIndex._refuse_non_redistributable` — FR13's confinement:
    `provenance.vendor.redistributable is False` into an index whose scope is
    `public` is a `validation_error` naming the vendor and the index.
  - `LocalIndex._write_index` — atomic through `ProjectStore._atomic_write`,
    and it drops the `entries()` stamp cache so a publish never serves the
    pre-publish document.
  - module-level `publish(index, source, service, jobs=, work_dir=,
    budget_s=)` — gate then publish, with `stages=GATE_STAGES` **hard-coded**:
    publish takes no stage subset precisely so `skip / not_selected` cannot
    reach the verdict.
  - the entry builders: `_index_entry`, `_parts_digest` (params from the
    `contract` rows, connectors from the `connectors` rows, spec names from
    the `specs` rows — **derived from the gate's own measurements**),
    `_preset_names` (`<part>.<config>`), `_preview_paths`, `_report_id`
    (`sha256:` over the canonical report JSON), `_copy_inventory`.
- `agentcad/core/packages/manager.py` — `_resolve_in` now accepts an
  **explicitly named** yanked version, with a warning, and refuses a *range*
  that only yanked versions satisfy. `resolve()` carries `yanked`; `add()`
  returns `yanked` and the `warnings` this add raised (sliced off
  `self.warnings` at a watermark, so index-loading warnings do not leak into
  it).
- `agentcad/cli.py` — `cmd_publish(args)` and the `publish` subparser
  (`path` optional, `--index` required, `--yank NAME@VERSION`,
  `--projects-dir`, `--jobs`, `--work-dir`, `--budget`), `publish` added to
  the subparser `metavar` and to the module docstring.
- `tests/test_packages_publish.py` (new) — 30 tests.
- `tests/test_packages_gate.py` — three assertions updated to the qualified
  shape, plus `test_every_exempt_skip_is_qualified_by_its_stage`.
- `tests/test_packages_cli.py` — the `--help` metavar assertion.

## Divergences from the plan, and why

- **`gate.exempt_skips` is now uniformly `<stage>:<reason>` — a change to
  slice 5's report format.** The previous agent flagged that the list mixed a
  bare reason (`no_policy_configured`) with `<stage>:<reason>`
  (`specs:not_declared`), and that slice 8 was about to copy it into the
  published index entry. Two shapes in one published list means every consumer
  writes a parser with a branch. **Resolved at the gate**, not worked around
  at the publisher: the stage is prepended to row-level exemptions too, which
  is also strictly more informative (`build:string_param_unbounded` and
  `specs:fem_extra_missing` are now distinguishable). Pinned by a test that
  asserts the shape, not just the values.
- **`publishable`'s strictness was left alone, and publish agrees with it.**
  The second flagged wart. Publish runs every stage, so `not_selected` cannot
  arise; the two stage-level exemptions (`no_presets_declared`,
  `not_declared`) are legitimate absences and
  `test_a_package_with_no_presets_and_no_specs_still_publishes` proves a
  package with neither publishes. Everything else a skipped stage can mean —
  `specs_unavailable`, `renderer_unavailable`, `budget_exceeded` — is "we did
  not look", which may not read as "publishable". No change was needed at the
  gate, and the test says so rather than the changelog only asserting it.
- **`publish(index, dir)` is split into two.** The plan writes one function
  that runs the gate and publishes. It ships as
  `indexes.publish(index, source, service, …)` (gate + publish, the callable
  the CLI uses) over `LocalIndex.publish(source, report)` (everything except
  running the gate). The split is what makes every refusal testable **without
  a kernel** — a subset report, a moved tree, a missing `build123d`, a
  republish — and it is the seam a cloud index (PRD-005-lite) reuses.
- **`--yank` takes `NAME@VERSION`, and `path` is optional.** Decision 10
  writes `agentcad publish --yank <name>@<version>`; the plan's task list
  writes `[--yank]` as a bare flag beside a required `<dir>`. The spec wins,
  and `path` becomes `nargs="?"` so a yank — which measures nothing — needs no
  directory and **starts no kernel**. A `--yank` that is not `name@version` is
  exit 2 naming the form.
- **A refusal is exit 1, not 2.** The plan gives `cmd_check`'s codes without
  saying where a `conflict_error` lands. A republish, a vendor refusal or a
  moved tree is a *verdict* — deterministic, reproducible, and about the
  package — so it is 1. Exit 2 stays for "we could not produce a verdict": an
  unknown index, an unusable work dir, a crashed kernel.
- **A stray directory with no index entry is also a `conflict_error`.** Not in
  the plan. A half-published tree is exactly the state a crashed publish
  leaves, and overwriting it would silently redefine a version whose bytes
  somebody may already hold.
- **`build123d: null` in the report refuses the publish.** `format`'s index
  schema requires a non-empty `build123d`, and the entry's job is to record
  what the package was *proved* against. Silently writing `"unknown"` would
  make the field a lie; fail-closed names the fix (re-run on a machine whose
  kernel answers `ping`).
- **`presets` are published as `<part>.<config>`.** Two parts may legitimately
  ship a configuration with the same name, and `use_part` takes a part *and* a
  preset, so an unqualified list would be ambiguous the day a package ships
  two parts.
- **`manager.py` grew the yanked-exact-version path**, which the plan's slice-8
  file list does not name. Task 3 requires "an explicitly-named yanked version
  warns and proceeds", and resolution is where that decision lives. A *range*
  still refuses: `^1.0.0` is the caller asking us to choose, and choosing a
  yanked version is what the flag exists to prevent.

## Notes

- **"Wrote nothing" is measured, not asserted.** `tree_hash` walks the whole
  index directory, path and bytes, before and after every refusal — a red
  gate, a moved tree, a republish, a vendor refusal.
- **The window nobody else closes.** The gate re-hashes the package after its
  own stages (changelog 0171), which catches a tree that moved *during* the
  run. `LocalIndex.publish` re-hashes again, because between a finished report
  and a publish the tree can still move — and publishing then would attest a
  content id nobody measured. That is a whole test.
- **Immutability is not defended by comparing bytes.** Republishing identical
  content is still a `conflict_error`, because a byte comparison would let a
  publisher redefine "identical" later. The test publishes the same directory
  twice and asserts the index is unchanged.
- **A yank cannot break a project that already depends on the version.**
  `use_part` never resolves — it reads the lock and the cache — and the test
  yanks *between* `add_package` and `use_part`.
- The published entry is validated through `format.validate_index` **as part
  of the prospective document**, so a bad entry is refused before
  `index.json` is written rather than discovered by the next reader.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_publish.py
30 passed in 13.81s
```

The suites this slice changed behaviour in:

```
.venv/bin/python -m pytest -q tests/test_packages_gate.py tests/test_packages_cli.py \
    tests/test_packages_index.py tests/test_packages_tools.py
215 passed
```

The real command, on the green fixture, into a scratch index:

```
$ agentcad publish tests/fixtures/packages/widget_good --index agentcad-core \
      --projects-dir <scratch>/projects --work-dir <scratch>/work
widget_good@1.0.0 · sha256:c5dfda79cd3522bb152386cec21dfa74f738725c98f4eef5d4eb9cebf8103091
  stage       status  pass  fail  skip  error  total     time
  format      green      5     0     0      0      5    0.0 s
  …
  policy      green      0     0     1      0      1    0.0 s
not measured (exempt from the publish verdict):
  - policy:no_policy_configured
The publish gate is a CORRECTNESS gate, not a security boundary: …
publish: widget_good@1.0.0 → index 'agentcad-core' ·
sha256:c5dfda79cd3522bb152386cec21dfa74f738725c98f4eef5d4eb9cebf8103091 · gate green
$ echo $?
0
```

…the same command again → **exit 1**, `"A version is IMMUTABLE — republishing
is refused even when the content id is identical"`; the AC2a fixture → **exit
1** with `build:strut@length=max` under `failures:`; `--index nope` → **exit
2**; `--yank widget_good@1.0.0` → **exit 0**, `"nothing was deleted; a
lockfile naming it keeps resolving"`, and the entry's `yanked` is `true`. The
`--work-dir` is left empty afterwards — the cell is deleted, the directory is
not.

Full suite, with PRD-011 slices 7–9 in the tree (`make test`):

```
.venv/bin/python -m pytest -q -n 2 --dist loadscope -rs
3008 passed, 1 skipped in 1549.33s (0:25:49)
```

The baseline after slice 6 was **2885 passed, 1 skipped** (changelogs
0170–0172); slices 7–9 add **123** tests (51 tools + 30 publish + 37 git +
4 OCP-free probes + 1 gate verdict-shape). The single skip is pre-existing and
explained — `tests/test_analysis.py:166: agentcad[fem] installed; the 501
fallback is unreachable`. The number is cited in all three of this sequence's
entries because the three slices were built and verified as one run.
