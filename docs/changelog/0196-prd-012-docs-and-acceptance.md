# 0196 — PRD-012 slice 8: the configuration surface documented, and AC1–AC9 graded

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude

## Summary
The closing slice of PRD-012 (Configurations): the shipped surface is written
down where a reader looks for it (`agent-api`, `user-guide`, `architecture`,
`packages`, `geometry-ci`, `part-authoring`), the traps the seven build slices
paid for are condensed into house gotchas (`AGENTS.md`, `CLAUDE.md`), and the
PRD's nine acceptance criteria are graded by one test each in a new
`tests/test_prd012_acceptance.py`. No product code changed.

The docs describe the **code**, not the spec, wherever the two diverged during
the build — each of those is called out in Notes below.

## Changes

### `tests/test_prd012_acceptance.py` (new, 13 tests)

The house acceptance shape (`tests/test_prd011_acceptance.py`): a module
docstring with the `| AC | Test |` table, one `test_acN_<claim>` per criterion,
and the two meta-tests. `TestTheFlangeFamily` is the heavy class
(`@pytest.mark.timeout(600)`, seven tests, a class-scoped template project
cloned per test) built on `tests/conftest.py`'s `FLANGE_SCRIPT` +
`THREE_SIZE_CONFIGS` — the PRD's own S/M/L walkthrough.

- **AC1** — `build_configs` returns three rows in family order with three
  distinct masses, and L's matrix mass is cross-checked against
  `set_active_config l` + `get_metrics`: two different code paths (a pure
  configuration build and a working-state rebuild) arriving at the same number
  is what "a variant's identity does not depend on session state" means. The
  matrix is also asserted to leave `active_config`/`params` untouched.
- **AC2** — the `dim_table` drawing: the SVG carries one `Label (name)` cell
  per member and every configured-parameter column once, and
  `detected.dim_table` echoes `{columns, rows, placement, warnings, dropped}`
  with X/Y measured as the OD (100/140/200) and Z as the thickness — numbers
  that come from three built shapes, not from the request. The browser half of
  the criterion ("one browser check") is graded as evidence in changelog 0195.
- **AC3** — removing a bound configuration is a `conflict_error` naming the
  instance in `details.instances` **and** in the message, with the family
  unwritten; `list_configs.referrers` is asserted as the lookup that makes it
  predictable; unbinding then lets the same call through.
- **AC4** — `flange_l.step` beside an unchanged `flange.step`. The base file's
  bytes are read before and after the configuration export (STEP stamps a
  timestamp, so two exports are never comparable — one file across the second
  call is).
- **AC5** — anchored by the kernel-call counter (`tests/test_specs_api.py`'s
  `_counting`), because two equal key strings prove only that the service
  agreed with itself. A four-member family whose `m2` is `m` spelled again
  issues exactly **three** `build` calls, `m2`'s row says `cached`, and
  toggling `active_config` four times afterwards issues **none**.
- **AC6** — two S and two L instances: distinct masses, one `mesh_key` per
  size (not per instance), and `check_interference` reporting the L pair only.
  Discriminating in both directions: the S pair stands 110 mm apart, which
  clears at ⌀100 and would overlap by 30 mm at ⌀200.
- **AC7** — `get_part.status.diverged`/`diverged_params` after a `set_params`
  on top of an active configuration, then the `null`-removes round trip, with
  `mesh_info(...)["key"]` back on the pure configuration's key at both ends
  (the only assertion that says the *geometry* came back).
- **AC8** — split. `test_ac8_a_project_without_configurations_is_byte_identical`
  rebuilds a copy of `examples/rocketry` through `measure_part` and compares
  against `GOLDENS[("rocketry", "flange")]` — PRD-010's metrics **and `.acm`
  sha** — then asserts the raw manifest carries no `configs`/`active_config`/
  instance `config` before *or* after a write, and that `get_part` still
  answers `{}`/`None`/`diverged: False`.
  `test_ac8_the_full_suite_count_is_cited` is the evidence check on this entry
  (and on the newest entry, if a later one lands).
- **AC9** — a structural grep over the surfaces the 0195 browser session drove
  (`renderConfigBar`/`markConfigSources`/`cfg-chip` in `inspector.js`,
  `row-badge` in `tree.js`, `setInstanceConfig` in `placement.js` and `api.js`,
  `getMeshByKey` in `api.js` and `main.js`, `buildConfigs` in `configs.js`,
  `#config-bar`/`#configs-modal` in `index.html`), the six route paths in
  `routes_configs.py`, and `ERROR COUNT: 0` + `FAILED REQUESTS: 0` in the
  changelog entry.
- **Meta** — the roadmap row for `[012]` links to the folder the PRD is
  actually in (the PRD-010 close-out lesson, changelog 0164); the five tools
  are registered *and* documented; and the docs carry the shipped surface
  (including the measured tool count).

### Docs

- **`docs/agent-api.md`** — the count line moves **73/76 → 85/88** (measured
  with `build_registry` on a service with no `[fem]` extra: 85 tools; the
  extra adds three). A new `### Configurations` section: the five-tool table
  with bold required arguments and prose returns, then the narrative — a
  configuration is the object a package preset is and shares its one validator;
  a **declared** configuration is range/enum-strict while an override on top
  clamps; resolution order and *semantic* divergence; `set_active_config`
  clears the overrides only on a real switch; a red matrix row is a 200
  payload and a refusal is not; the removal conflict and how to clear it; the
  per-configuration artifacts, the CLI flag, the eight routes, the events, and
  the merge's two problem kinds. Amended rows: `get_part`, `get_project`,
  `get_assembly` (`config` + `mesh_key`), `set_assembly` (`config` on an
  instance), `check_interference`, `sweep_motion`, `tolerance_stackup` (the
  bound-path warning), `generate_drawing` (`config`, `dim_table`) and
  `render_view` (`config`, and why it requires `part_id`).
- **`docs/user-guide.md`** — the count line (73 → 85); the configuration bar
  (switcher, provenance marks, divergence chip + **Reset to M**, Matrix) in
  Inspector → Parameters; the configured-part badge in Sidebar → Parts; the
  per-instance picker and `part@config` rows in Sidebar → Assembly; the dim
  table in the 2D-drawings paragraph; and a `## Configurations` section that
  says out loud that **declaring a family is done through the tools or the
  chat** (the browser is where you *use* one), why switching clears overrides,
  why declared values are refused where dragged ones are clamped, what the
  matrix answers, and what a bound instance is.
- **`docs/architecture.md`** — the count line in the diagram and the sentence
  under it (73/76 → 85/88); `configs`/`packages` added to the tool-pack and
  route-pack rows; and a `## Configurations` section after Packages: the
  manifest fields written only when set, the two pure members and why
  resolution is not in the store, why nothing new entered `_cache_key`, the
  `_rebuild`/`_build_with`/`_ensure_config_built` split and the `_config_status`
  livelock guard, the serial de-duplicated matrix and the deleted fan-out, the
  content-addressed mesh route, store-side binding validation, and the pack's
  `con` load position.
- **`docs/packages.md`** — the `presets.json — configurations` heading now
  cross-links the two configuration docs instead of a **stale
  `prd/pending/PRD-012-…` path** (the PRD is in `in-progress/`), and states
  that `use_part` does *not* copy presets into a part's `configs` and why.
- **`docs/geometry-ci.md`** — the `build` stage row already documented
  `part@config` (slice 6); this adds what a `part@config` row *claims* (that
  configuration's pure resolution, not the working state) and that a harness
  failure carries `config` beside `part`, plus the budget bullet's
  before-every-configuration reading.
- **`docs/part-authoring.md`** — one pointer under "Design for parameter
  robustness": named sizes are **not** a script concept, they live in the
  manifest, and feature variation stays script logic on an enum parameter.
- **`docs/roadmap.md`** — row `[012]` → `in progress`, linking
  `prd/in-progress/`. (The completion commit flips it to `completed/`.)

### House guidance

- **`AGENTS.md`** — a `## Configuration gotchas (PRD-012 …)` section in the
  house style, 19 items, each traceable to a measurement in changelogs
  0188–0196: the kernel never sees a configuration and the store never
  resolves; the pinned `_rebuild`/`get_part` signatures and what that costs;
  `_status` vs `_config_status` as a **livelock** guard; lowercase names and
  the naming collisions; strict-declared vs clamped-override; the serial
  de-duplicated matrix and the deleted fan-out; overrides cleared only on a
  real switch (and the browser consequence for "Reset to M"); semantic
  divergence; `mesh_key` addressing and the `fullmatch` gate; store-side
  binding validation; the `con` load position; `manifest_scope` across the
  whole RMW (and the two writers still unlocked); the threaded
  `_PART_ENTRY_DICTS` and the `_keyed` guard; the asymmetric merge problems;
  the dimension table as a measurement; `render_view`'s refusal;
  `_shape_tier(record=)` moving both halves of the identity together; why
  `m`'s key is not the base key; and zero-cost-when-unused as a test.
  The Conventions "Determinism" bullet now says explicitly that PRD-012 added
  nothing to the cache-key payload, and the "Where to read more" line carries
  the new tool count.
- **`CLAUDE.md`** — one condensed traps bullet in the existing style.

## Fix round 1 (review: spec PASS, quality approve-with-fixes — 4 Important, 7 minor)

- **I1 — the budget bullet in `docs/geometry-ci.md` claimed too much.** It said
  the **build** and **drawings** stages both check the budget "before every
  configuration row", but only `_stage_build` expands `part@config`
  (`checks.py`'s subject loop); `_stage_drawings` iterates manifest parts and
  emits one row each. The clause is split: build checks before every row it
  emits (the part's own **and** each `part@config`), drawings checks before
  each part and draws the working state only.
- **I2 — the tool-count sweep was incomplete.** `README.md` (the "now **73
  tools**" sentence and the docs index line) and `docs/roadmap.md`'s v3
  snapshot paragraph still said 73/76. All three now read 85/88, and the Notes
  below name **six** documents rather than four, plus the three files that keep
  a historical figure deliberately (`docs/market_research.md`, `PRD-018`,
  `PRD-024`).
- **I3 — prose called the object a "variant" while this slice's own
  `AGENTS.md` rule says never to.** Seven uses reworded to *configuration*:
  `docs/user-guide.md` ×5 (including the two bolded leads, now
  "**Switching loads the configuration.**" and "**Configurations reach
  everything downstream.**"), `docs/agent-api.md`'s `set_active_config`
  paragraph, and the matching `AGENTS.md` gotcha. The roadmap's `[012]`
  description follows ("named **parameter sets**"). What is left is
  legitimate: the publish gate's `Variant` build sweep (`AGENTS.md`,
  `docs/packages.md`), the two naming rules themselves, and PRD-011's
  "many-variants-of-one-part" fan-out measurement quoted verbatim in
  `AGENTS.md` and `docs/architecture.md`.
- **I4 — the suite-count placeholder** in this entry (and `0195`) is the
  controller's to fill; `test_ac8_the_full_suite_count_is_cited` stays red
  until it is, by design.
- **Minor (a)** — `Label (name)`'s fallback is now stated where the table is
  documented: a configuration with **no label, or one equal to its name**,
  prints the bare name rather than repeating it (`_row_label`). Said in both
  `docs/agent-api.md` and the `AGENTS.md` gotcha.
- **Minor (b)** — "17 items" → **19** (the real count of top-level bullets in
  the new `AGENTS.md` section).
- **Minor (c)** — the drawing **preview** route's query parameters are now
  documented: `GET /api/projects/{proj}/parts/{id}/drawing.svg` takes
  `?config=<name>` and `?dim_table=1|true`, and the GET *regenerates* the
  sheet before serving the suffixed file — so `?config=` without `?dim_table=`
  serves a sheet with no table.
- **Minor (d)** — `TestTheFlangeFamily.family_projects` is a
  `@pytest.fixture(scope="class")` over a **`@staticmethod`**, not a
  `@classmethod` whose `cls` nothing used.

## Files
- `tests/test_prd012_acceptance.py` — **new**, 13 tests (AC1–AC9 + 4 meta)
- `docs/agent-api.md` — count line, `### Configurations`, ten amended rows
- `docs/user-guide.md` — count line, config bar, badge, picker,
  `## Configurations`, the drawing dim table
- `docs/architecture.md` — count lines, pack rows, `## Configurations`
- `docs/packages.md` — the presets cross-link and the `use_part` non-claim
- `docs/geometry-ci.md` — what a `part@config` row claims; the budget bullet
- `docs/part-authoring.md` — the manifest-resident pointer
- `docs/roadmap.md` — row `[012]` → in progress; its description and the v3
  snapshot paragraph's tool count (fix round 1)
- `README.md` — the two tool-count lines (fix round 1)
- `AGENTS.md` — `## Configuration gotchas (PRD-012 …)`; the determinism and
  read-more lines
- `CLAUDE.md` — the condensed traps bullet
- `docs/changelog/0196-prd-012-docs-and-acceptance.md` — this entry

## Notes
- **Where the docs describe the code rather than the spec**, deliberately, and
  why: `set_active_config` clears overrides **only when the active
  configuration actually changes** (slice-3 fix round — a `DELETE` on a part
  already at base used to drop `set_params` values), so the "Reset to M" chip
  is `set_params` nulls and the doc says so; the dimension table prints the
  **resolved** parameter map and `Label (name)` (slice-6 fix round I1/M1 — a
  ragged member used to print an em dash beside geometry built from the
  script's default); `render_view` **refuses** `config` without `part_id`
  (slice 4); an empty `build_configs` matrix always carries a `warnings`
  reason; `set_instance_config` is a fifth tool the PRD's agent-surface list
  did not name; and the merge's `dangling_instance_config` blocks while a
  dangling `active_config` warns. The design spec's Decision list is the
  intent; these entries are the shipped behaviour.
- **The tool count is measured, not guessed**: `build_registry` over a service
  with no `[fem]` extra registers **85** tools (the five new ones included),
  and the extra adds `fem_static`/`fem_modal`/`fem_thermal` → 88. The previous
  "73 (76)" was already stale before this PRD. **Six documents carried it and
  all six are updated** (fix round 1): `docs/agent-api.md`,
  `docs/architecture.md` (twice — the diagram and the sentence under it),
  `docs/user-guide.md`, `AGENTS.md`, `README.md` (twice) and
  `docs/roadmap.md`. Three files keep a historical figure on purpose and are
  left alone: `docs/market_research.md` (a v3 snapshot, and the roadmap line
  beside it says so in its own words) and the two pending PRDs
  `PRD-018`/`PRD-024`, which describe the surface as it was when they were
  written.
- **AC8's byte-identical half is graded against the strongest statement in the
  tree** — PRD-010's `.acm` sha golden for `examples/rocketry`'s flange — on a
  **copy**, because the bundled examples are working projects that a test must
  not write into. Any of slice 1's twenty
  `record.params → record.effective_params` renames that had changed what a
  configuration-free part builds would surface there as a moved sha.
- **`test_ac8_the_full_suite_count_is_cited` fails until the count below is
  filled in**: it requires the digits **immediately before the word
  `passed`** (`\d{4,6}\s+passed`), not merely a long digit string somewhere in
  the file — every entry's own title is a four-digit number, so the looser
  reading is satisfied by an entry that cites nothing. The literal placeholder
  ("N passed, M skipped") is red on purpose and the close-out cannot forget.
  (Now filled by the controller, so it is green.)
- No product code was touched by this slice; the only Python it adds is the
  test module. Slice 7's frontend files were read (the AC9 grep) and not
  edited.
- Verification, round 0: `uv run pytest tests/test_prd012_acceptance.py -q
  -m "" -p no:randomly` — **12 passed, 1 failed**, the failure being
  `test_ac8_the_full_suite_count_is_cited` against the then-unfilled
  placeholder below (measured green by filling it with a throwaway number and
  reverting). The same module under `-n 2 --dist loadscope` behaved
  identically. **Fix round 1**, with the count now filled:
  `uv run pytest tests/test_prd012_acceptance.py tests/test_prd011_acceptance.py
  -q -m "" -p no:randomly` — **28 passed**. Docs-reading neighbours:
  `tests/test_prd011_acceptance.py tests/test_server.py tests/test_tools.py
  tests/test_mcp.py` — 33 passed; `tests/test_prd010_acceptance.py
  tests/test_prd004_acceptance.py` — 29 passed;
  `tests/test_packages_api.py tests/test_packages_tools.py
  tests/test_packages_publish.py tests/test_configs_api.py
  tests/test_configs.py` — 230 passed.
  Full suite: `make test` — 3489 passed, 7 skipped in 12:10 on 8 workers, with exactly one red row: `tests/test_prd012_acceptance.py::test_ac8_the_full_suite_count_is_cited` itself, red only because this line had not yet been filled; with it filled the same suite is 3490 passed, 7 skipped.
