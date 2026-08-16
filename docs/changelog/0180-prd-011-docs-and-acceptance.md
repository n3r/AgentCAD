# 0180 — 2026-08-16 — PRD-011 slice 14: docs, acceptance tests, close-out

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The last slice. `docs/packages.md` is the user-facing reference — the format,
the content id, the lockfile, the index kinds, the publish flow, the bundled
catalog, and **the trust model on the first screen**. `AGENTS.md` gains a
PRD-011 gotchas section, every item traceable to a measurement.
`tests/test_prd011_acceptance.py` grades AC1–AC8 with one named test each,
plus **AC9** — the agent-authoring loop adopted at the design review, which is
the criterion this whole feature exists to satisfy. `PRD-012`'s FR1 is amended
to the frozen configuration schema, in *this* commit rather than "later",
because the schema froze the moment the catalog published. The PRD moves to
`completed/` with a verification table, eleven recorded divergences and four
open items.

Two things in the tree changed because writing the acceptance tests found
them, and both are reported rather than smoothed over: a **yank was defeated
by a warm cache**, and a `docs` row named its missing part ids **only in
English**.

## Changes

- `docs/packages.md` (new) — the reference. Trust model first (the gate is a
  correctness gate; what the real boundary is; PRD-006 and the reserved
  `signatures` slot), then the format field by field, both part kinds, the
  configuration schema, the content id and its measured cost, the two manifest
  maps, the provenance header and its five statuses, the three index kinds and
  their precedence, publishing (nine stages, the exempt set, immutability,
  yank, containment), the bundled catalog, `package_from_step`, an authoring
  checklist, and a **"Known limits, stated"** section.
- `AGENTS.md` — "Package gotchas (PRD-011)", ~25 items: the pack-name /
  `gate_providers` trap and the no-provider decision; the trust boundary; the
  tree digest and why there is no archive; no machine fact in the header or
  the lock; the header immutable with status on read, and why
  `remove_package` touches no script byte; the OAT-plus-presets claim; a
  stage with no rows blocking `publishable`; the live write guard; store-level
  variant writes; the `connectors` handler's first consumer; `_git.py` versus
  `history._run`; once-per-instance `refresh`; bundled indexes appended (and
  `merge_bundled` being a replacement); the cache-versus-yank rule; reference
  parts and `use_part`'s refusal; why `face_info` cannot serve FR13; the
  **1.40×** fan-out; the live name collisions; the empty description column;
  and the LICENSE blocker.
- `CLAUDE.md` — the condensed traps line and `docs/packages.md` in "Deeper
  docs".
- `docs/agent-api.md` — a "Packages" section: the seven tools, the two CLI
  commands with their exit codes, the routes, the `get_part` /`get_project`
  additions and the five provenance statuses, all under the non-claim.
- `docs/user-guide.md` — "The Parts library": the dialog, what you get (an
  ordinary part), the nine bundled packages, the interface-model caveat, the
  cosmetic-vs-real thread behaviour, and the non-claim as a blockquote.
- `docs/architecture.md` — the subpackage table, the extension points it
  arrives through, the on-disk layout, what the gate actually orchestrates,
  and why `use_part` copies in; plus a packages paragraph in "Trust model".
- `README.md` — a Capabilities bullet, the docs link, `catalog/` in the
  project layout, and the trust-model paragraph.
- `docs/prd/pending/PRD-012-configurations.md` — **FR1 amended** to
  `configs: {name → configuration}` with the wrapped
  `{params, label?, description?}` entry, a blockquote recording the amendment
  and the three reasons, FR12 extended with the finer merge granularity the
  wrapper buys, and the "Agent path" example rewritten to the real shape.
- `docs/prd/in-progress/PRD-011-…` → `docs/prd/completed/` — status, an "As
  built" section, the AC table, "What could not be delivered as written"
  (eleven items) and "Still open" (four).
- `docs/roadmap.md` — step 1 marked **DONE** with what shipped and what step 2
  inherits; the index row moved to `completed/` and relinked; step 2 marked
  next.
- `tests/test_prd011_acceptance.py` (new) — 15 tests.
- `agentcad/core/packages/manager.py` — the offline path no longer resolves a
  version a reachable index has **yanked**, for a range.
- `agentcad/core/packages/gate.py` — three rows now carry as *data* what they
  previously carried only in their sentence.

## The two findings, and their fixes

### A yank was defeated by a warm cache

Writing AC5 found it. `resolve()` walks the indexes; when none answers it
falls back to the cache. A yanked version makes the index answer *no* — and
the cache then said yes. So a publisher's withdrawal only ever bound machines
that had never installed the package, which is the opposite of the population
it needs to bind.

Fixed at the seam that owns the distinction: **the cache is for "no index
answered", not for "the index answered no"**. `_resolve_in` collects the
versions any reachable index marks `yanked`, and `_resolve_cached` skips them
for a *range*, recording the reason in `tried`. An explicitly-named version
still installs from the cache, exactly as it does online — a lock entry naming
a yanked version has to keep re-installing, which is AC5's other half.

```
add_package {"name": "widget_good", "version_req": "^1.0.0"}
  -> not_found_error: "… vendor: only yanked versions of 'widget_good' match
     ^1.0.0; cache: widget_good@1.0.0 is cached but the index has YANKED it,
     and ^1.0.0 is a range — name the version explicitly if you must have it"
add_package {"name": "widget_good", "version_req": "1.0.0"}   -> installs
```

### A report row that could only be read by a human

AC9 is the criterion that an agent repairs a package **from the report's
structured content**. Writing it is what tested that claim, and one row failed:
the `docs` stage's README row named the missing part ids only inside its
English sentence (`details` was `{}`). An agent could not act on it without
parsing prose — which would have made AC9 either a lie or a regex.

The row now carries `details: {"missing": [...], "path": "docs/README.md"}`,
and the `format`/`presets` problem rows carry `details.field` beside their
`code`. **This is the finding the AC was added to produce**, and it is worth
stating as a rule: *a gate row must carry, as data, everything a mechanical
fix needs.* The two rows that already did — `contract` (`missing` keys) and
`specs` (`measured`, `limit` as `{keyword: value}`) — are why the other two
fixes needed no change.

## AC9 — what it proves, and what it does not

`test_ac9_an_agent_takes_a_red_package_green_from_the_report_alone` copies
`widget_good`, breaks it in three ways the gate names precisely, and plays the
agent:

| break | row | mechanical fix, derived from |
|---|---|---|
| the declared volume floor is above what the family's own shortest variant reaches | `specs:mount_block@length=min:not_hollowed_out` | `details.limit == {"min_mm3": 8000.0}` (the constructor keyword **and** its value), `details.measured` across every failing row of that check, `details.part` for the part id, and the `format` row's `details.file` for the file |
| a configuration names `mount_blockk`, a part the package does not ship | `presets:presets.mount_blockk` | `details.field`, plus the declared part ids read off the `format` stage's own rows |
| the README stopped naming its part | `docs:docs/README.md` | `details.missing` and `details.path` |

`_agent_pass` obeys one rule: **it may read the report and the files the
report names, and it knows nothing else about this package** — no part id, no
parameter name and no file path is written into it. One pass takes the report
from three blockers to `publishable: true`, and the repaired package then
publishes.

**What it does not prove**, said in the test's own docstring: that an agent
would pick the *right* fix. The spec row admits two consistent repairs — move
the limit, or narrow the parameter's range — and choosing between them is
engineering judgement the report cannot and should not make. What AC9 grades
is that the rows are *addressable*.

## The fan-out, restated where the docs will be read

Changelog 0178 measured **1.40×** on the whole catalog (107 variants, five
interleaved repetitions, 3-worker pool) against the plan's own 1.5×
keep-threshold, with 44% of the build stage inside kernel calls and an Amdahl
ceiling of 1.42×. That number — not 0171's 1.55× on a synthetic package, and
not 0176's 1.08× on one real one — is what `docs/packages.md`, `AGENTS.md` and
the PRD's divergence list all carry. **No document advertises a speedup the
catalog does not get**, and the keep/delete decision remains the review
round's: `jobs=1` is a first-class path, the reports agree row for row, and
removal is a one-function change.

## The blocker this feature does not solve

**This repository still has no LICENSE file and no `license` field in
`pyproject.toml`**, while the nine catalog packages declare `Apache-2.0`
because the format requires a licence per package. So the seed catalog states
a licence the repository itself does not. It is recorded in
`docs/packages.md`, in the PRD's "Still open", in `AGENTS.md` and in the
roadmap's step-1 row. **It is a founder decision, not an engineering one, and
it blocks PRD-031a.**

## Files

- `docs/packages.md` — new
- `AGENTS.md`, `CLAUDE.md`, `README.md` — new gotchas / traps / capability
- `docs/agent-api.md`, `docs/user-guide.md`, `docs/architecture.md` — new sections
- `docs/prd/pending/PRD-012-configurations.md` — FR1 amended, FR12 extended
- `docs/prd/completed/PRD-011-parts-library-registry.md` — moved, with "As built"
- `docs/roadmap.md` — step 1 done, index row relinked
- `tests/test_prd011_acceptance.py` — new
- `agentcad/core/packages/manager.py` — the yank/cache rule
- `agentcad/core/packages/gate.py` — three rows carry their data

## Verification

Targeted, this slice:

```
.venv/bin/python -m pytest -q tests/test_prd011_acceptance.py
15 passed in 11.90s
```

The whole PRD-011 surface, every suite this feature owns or touched:

```
.venv/bin/python -m pytest -q tests/test_prd011_acceptance.py tests/test_catalog.py \
    tests/test_packages_api.py tests/test_packages_tools.py \
    tests/test_packages_publish.py tests/test_packages_index.py \
    tests/test_packages_gate.py tests/test_packages_cli.py \
    tests/test_packages_cache.py tests/test_packages_format.py \
    tests/test_packages_git_index.py tests/test_packages_from_step.py \
    tests/test_packages_ocp_free.py tests/test_manifest_merge.py \
    tests/test_threads.py
699 passed in 114.30s (0:01:54)
```

Full suite (`make test` is `test-full` is this command), measured on the
working tree this entry describes, with nothing landing during the run:

```
.venv/bin/python -m pytest -q -n 2 --dist loadscope -rs
3151 passed, 1 skipped in 1823.51s (0:30:23)
```

The single skip is pre-existing and explained —
`tests/test_analysis.py:166: agentcad[fem] installed; the 501 fallback is
unreachable`. Provenance for the earlier numbers in this sequence, kept as
provenance and **not** as this entry's count (the 0163 pattern): **2527** was
the pre-PRD-011 baseline, **2763** after slices 1–3, **2885** after 4–6 and
**3008** after 7–9 (changelogs 0167–0175). Slices 10–14 add the remaining
**143** — 63 catalog, 29 API, 33 `from_step`, 15 acceptance, and the probes.
This number is measured on the branch before the review commit; re-measure it
on the committed tree and replace it if the tree moves, which is exactly what
`test_ac8_the_full_suite_count_is_cited` exists to force.

AC7's browser sessions are not re-run in this slice: it changes no frontend
byte, and the sessions in changelogs 0177 and 0178 (the second against all
nine catalog packages, 0 console errors, 0 page errors, 0 responses ≥ 400) are
the evidence `test_ac7_…` grades.

## Notes

- **`_find_prd()` and property-based status assertions**, both copied
  deliberately from PRD-010's close-out trap (changelog 0164): a PRD moves to
  `completed/` at **merge**, so a test that pins one directory — or one status
  word — is red for the whole review window. This file searches for the PRD and
  accepts any post-implementation status.
- **AC7 is graded as evidence, and says so.** A test cannot be a browser
  session; what it grades is that the surface those sessions drove still
  exists, still calls the routes it called, and still carries the non-claim as
  visible text (the test asserts the element has no `title=`).
- **AC4 was sharpened while being written.** Deleting the *remote* is not
  enough to reach the cache path: a `GitIndex`'s last good checkout keeps
  answering, which is its designed degradation. The test now asserts that
  first, then deletes the checkout, and only then claims the install came from
  the cache — with a lock entry byte-identical to the online one.
- **Every AC test runs on a copy**, and the four that touch a user-visible
  root (`examples/`, `catalog/`) copy before they touch.
