# 0176 — 2026-08-16 — PRD-011 slice 10: the seed catalog — `iso4762`, bundled, published and dogfooded

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The registry gets real content. `catalog/` at the repo root is the bundled
local index; `catalog/iso4762/1.0.0/` is an ISO 4762 socket-head cap screw,
M3–M12, with an enum `size`, a bounded `length`, the cosmetic-vs-real thread
choice FR11 asks for, eight catalogue presets, a rigid `head_seat` and a
cylindrical `axis` connector, and four SPECS that measure the built solid
against the **published ISO 4762 table**. It went through the real publish gate
— 9 stages, 124 passed, 1 exempt skip — and `catalog/index.json` is the gate's
own output, not a hand-written document. `cli._register_catalog` declares the
catalog so a fresh install searches and installs with **no network and no
config file**, and `tests/test_catalog.py` proves the same bytes come back
through a `GitIndex` clone. **AC1 is re-won on the real package**, end to end,
on a copy of the prototyping example.

## Changes

- `catalog/index.json` (new) — the `agentcad-core` index. The four-line empty
  document (format, name, scope, no packages) is the only part anybody typed;
  every version entry was written by `agentcad publish` from the gate's own
  measurements.
- `catalog/iso4762/1.0.0/**` (new, 5 files) — `package.json`,
  `parts/cap_screw.py`, `presets.json`, `docs/README.md`,
  `previews/cap_screw_iso.png` (640×480, 6 593 bytes, rendered by
  `render_view`).
  - `PARAMS`: `size` (enum over the seven ISO 4762 designations M3–M12),
    `length` (number, 5–60 mm, unit and description — the `contract` stage
    requires all four of those on a numeric parameter), `thread`
    (enum `cosmetic | real`).
  - `connectors`: `head_seat` rigid at the under-head bearing face (the
    origin), `axis` cylindrical down the shank. The moving side of a mate must
    be rigid, so `head_seat` is what a consumer mates.
  - `SPECS`: `check_valid`, plus three `check_that` predicates comparing the
    measured bounding box with the ISO 4762 table — head diameter `dk`, head
    height `k`, and length under the head — within 0.05 mm.
- `agentcad/cli.py` — `CATALOG_INDEX`, `bundled_index_entries()` and
  `_register_catalog(service)`, called from `_build_service` beside
  `_register_examples`; `cmd_publish` merges the bundled entries so
  `publish --index agentcad-core` reaches the catalog.
- `agentcad/core/packages/indexes.py` — `merge_bundled(config, bundled)`:
  bundled index entries **appended** to the configured ones, skipping a name
  the user already configured.
- `agentcad/core/packages/manager.py` — `reload_indexes` reads the
  configuration through `_configuration()`, which merges
  `service.bundled_indexes`. A service without that attribute (every test
  service, `checks.py`'s ephemeral one) loads exactly what it loaded before.
- `packaging/pyinstaller/agentcad.spec` — `catalog/` joins `examples/` in the
  data files.
- `tests/test_catalog.py` (new, `slow`) — 20 tests.

## Divergences from the plan, and why

- **The auto-registration is a declaration on the service, not an index
  client.** The plan puts `_register_catalog` in `cli.py` beside
  `_register_examples`, and it is there — but `_build_service` runs *before*
  `build_registry`, and `service.packages` (which owns the index list) does
  not exist until the tool pack loads. So `_register_catalog` sets
  `service.bundled_indexes`, a list of ordinary configuration entries, and
  `PackageManager` merges them when it builds its clients. That keeps the
  registration in the one place a CLI entry point can reach and costs the
  manager six lines. `cmd_publish` does not build a service before it resolves
  `--index`, so it merges the same entries itself through
  `indexes.merge_bundled`.
- **Bundled indexes are appended, never prepended.** The design spec's example
  configuration lists `agentcad-core` first, which reads as precedence. It
  cannot be: a bundled fallback that outranks the user's own configuration is
  a fallback nobody can override. Configured indexes keep precedence and a
  user index *named* `agentcad-core` replaces the bundled one outright — two
  tests pin both halves.
- **The package is authored outside the index and published into it.** Task 4
  reads as though `agentcad publish catalog/iso4762/… --index agentcad-core`
  publishes a package that already sits at its destination. It cannot: the
  publisher refuses a destination directory that already exists (changelog
  0174 — a half-published tree is what a crashed publish leaves). So the
  package was authored in a scratch directory and published *into* `catalog/`,
  which is why the repository holds exactly the bytes `publish` wrote. A later
  fix is a version bump, whose destination is new — which is the format's
  immutability rule doing its job rather than a workflow wart.
- **`docs/packages.md` does not exist yet** (slice 14 owns it), so Task 4's
  "say so in `docs/packages.md`" — that the index is a build product of the
  gate — is said in `tests/test_catalog.py`'s module docstring and here, and
  slice 14 moves it.
- **Seven sizes, not twenty.** bd_warehouse's `iso4762` catalogue runs M1.6 to
  M64. The package declares M3–M12, which is the range the design spec's own
  example summary names and the range a `length` of 5–60 mm is honest for. The
  gate builds one variant per enum choice, so the enum is also the cost.
- **`length` is continuous, not a catalogue list.** A bounded numeric range is
  what lets the gate build the part at both of its extremes (the `contract`
  stage fails an unbounded numeric parameter); the catalogue combinations ship
  as the eight presets, which the gate also builds. The README says so, so
  nobody reads `length: 37.5` as a claim that such a screw is stocked.
- **AC1 asserts an interference *number*, not just a mate.** The plan asks for
  the mate; the assertion that makes it evidence is that the screw and the
  plate do not overlap — and then that the same screw with a real thread
  *does* (see below). "No interference" is only evidence when the check can
  report some.

## Measurements

### The cosmetic-vs-real thread choice is not cosmetic

FR11 asks for the choice to be exposed; measuring what it changes is what
makes the exposure useful. On M5-0.8 × 16, measured through the kernel:

| | shank diameter | volume | build |
|---|---|---|---|
| `cosmetic` | **⌀4.134** (thread *root*) | 477.9 mm³ | 0.05 s |
| `real` | **⌀5.000** (nominal *major*) | 550.4 mm³ | 0.12 s |

Bounding boxes are equal to within 1e-6 mm, so switching does not move a mate.
But a **cosmetic** screw drops into a PRD-010 tapped hole (tap drill ⌀4.2 for
M5) reporting *no* interference, and the **real** one overlaps it — which is
what thread engagement is, not a modelling error. The AC1 test asserts both
directions on the same mate. The README carries the table, because an agent
that runs `check_interference` after inserting a fastener needs to know which
answer it is entitled to.

Real-thread cost grows with the number of turns, not the diameter: M8 × 16
takes 0.13 s, M3 × 60 takes 1.93 s, M3 × 100 takes 5.17 s. The default is
`cosmetic` for that reason.

### The build fan-out on the real catalog — 1.08×, not 1.55×

Changelog 0171 kept the `ThreadPoolExecutor` on a **1.55×** measurement, taken
on a synthetic package whose every variant carried a real thread. The shipped
catalog package is cheaper, and the honest number is much smaller. Same method
(every worker pre-warmed, a fresh cell per run, median of 3, 3-worker pool,
20 variants):

| package | `jobs=1` | `jobs=3` | speedup |
|---|---|---|---|
| synthetic all-real-thread cap screw (0171) | 7.85 s | 5.05 s | 1.55× |
| **`catalog/iso4762` as shipped** | **5.11 s** | **4.71 s** | **1.08×** |

And the reason is measured, not guessed. Instrumenting
`service.kernel.request` inside a `jobs=1` run: of the build stage's **5.85 s**,
only **2.59 s (44%)** is spent inside kernel `build` calls; the other
**3.26 s** is per-variant work in the server process — `store.get_part`, the
cache key, the metrics sidecar, the status slot. Amdahl's ceiling for three
workers over a 44% parallel fraction is 1.42×, and 1.08× is what is left after
load imbalance (`KernelPool._pick` routes by `hash(affinity) % size`). The
kernel side itself parallelises fine: the same 20 builds issued directly at the
pool take 1.10 s serial and 0.22 s across three threads.

**Recommendation, stated rather than acted on:** the fan-out is now below the
plan's own 1.5× keep-threshold on the content the catalog actually is. It is
left in place for this slice because slice 12 adds eight more packages with
different cost profiles and the decision should be taken on the whole catalog,
not one package; the measurement is repeated there. `jobs=1` remains a
first-class path and `test_jobs_one_and_jobs_four_produce_identical_reports`
still pins that the two agree row for row, so deleting the executor stays a
one-function change.

## Verification

Targeted, this slice:

```
.venv/bin/python -m pytest -q tests/test_catalog.py
20 passed in 19.38s
```

The suites this slice could have broken (the index loader, the manager, the
publisher, the CLI, the frozen-resource helpers):

```
.venv/bin/python -m pytest -q tests/test_catalog.py tests/test_packages_index.py \
    tests/test_packages_tools.py tests/test_packages_publish.py \
    tests/test_packages_git_index.py tests/test_packages_cli.py
204 passed in 53.00s

.venv/bin/python -m pytest -q tests/test_frozen_helpers.py tests/test_tools.py \
    tests/test_server.py tests/test_packages_cache.py tests/test_packages_format.py \
    tests/test_packages_gate.py tests/test_packages_ocp_free.py tests/test_manifest_merge.py
394 passed in 8.32s
```

The real command, on the real package, into the real index:

```
$ .venv/bin/agentcad publish <scratch>/pkgsrc/iso4762 --index agentcad-core \
      --projects-dir <scratch>/projects --work-dir <scratch>/work
iso4762@1.0.0 · sha256:6f5c1c9a6b3757e5f42364af116578bd6464cd74290056303d93bd5a34c64a7d
  stage       status  pass  fail  skip  error  total     time
  format      green      5     0     0      0      5    0.0 s
  contract    green      4     0     0      0      4    0.0 s
  presets     green      8     0     0      0      8    5.6 s
  build       green     20     0     0      0     20    5.3 s
  specs       green     80     0     0      0     80    0.0 s
  connectors  green      3     0     0      0      3    0.0 s
  previews    green      2     0     0      0      2    0.0 s
  docs        green      2     0     0      0      2    0.0 s
  policy      green      0     0     1      0      1    0.0 s
not measured (exempt from the publish verdict):
  - policy:no_policy_configured
publish: iso4762@1.0.0 → index 'agentcad-core' · gate green
$ echo $?
0
```

20 build variants is `1 + 7 sizes + 2 length bounds + 2 thread choices + 8
presets` — the OAT sum, never the product — and 80 spec rows is four checks
over each of them.

## Notes

- **What the tests attack.** A one-byte edit to a catalog package (the content
  id must move, or matching it proves nothing); one wrong row in the ISO 4762
  table (the head-diameter check must redden, and *only* it); a `build` that
  returns something which is not a bd_warehouse fastener (every predicate must
  fail rather than pass, because "we could not establish the size" is not "it
  is fine"); a user index configured under the bundled name (it wins) and
  under another name (it keeps precedence); a missing catalog directory (a
  warning, never a startup failure); and the git-clone dogfood, which compares
  the served document and every served tree's content id against the local
  index.
- **`index.json` is a build product and the test is what keeps it one.**
  `test_every_published_entry_matches_the_tree_on_disk` recomputes the content
  id of every published tree from the repository's own bytes.
- **Two size vocabularies live in this tree and they do not match.** PRD-010's
  hole standards name the thread `"M5"`; bd_warehouse — and therefore this
  package's `size` enum — names it `"M5-0.8"`. Passing one where the other is
  expected is a `ValueError` naming the known sizes, which is how it was found.
  Both the package README and AC1's plate script say so.
- **The catalog is data.** Deleting it degrades the product to "no packages
  configured" and breaks no code path: `bundled_index_entries()` answers `[]`,
  `_register_catalog` warns only when a catalog directory exists without an
  `index.json`, and every consumer sees an empty index list.
