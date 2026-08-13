# 0147 — 2026-08-13 — PRD-010 slice 1: the identity harness, spike S1, and the re-measured baseline

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 1)

## Summary

Nothing ships to a user here. This is the gate PRD-010's AC1 will be judged
against, written down **before** `toolkit.patterns`/`toolkit.holes` exist so
slice 7's rewrite of the bundled `construction` scripts cannot quietly change
their geometry. It opens with spike **S1**, which had to answer one question
before the harness could be built on its assumption: *is a helper that re-enters
a `BuildPart` and `add()`s the caller's part byte-faithful on REAL parts,
measured THROUGH THE KERNEL WORKER?* The design had measured that in-process on
a synthetic plate only. Measured through the worker on three bundled parts:
**yes, byte-identical, at the parameters the bundled projects actually ship.**

## Spike S1 — byte identity through the kernel worker

Each part was built twice through `KernelClient.request("build", …)` at
`tolerance=0.1` (the service's `MESH_TOLERANCE`) at the params stored in the
example's `project.json`: once from the committed script, once from a variant
whose hole/pattern block was replaced by the helper form
`BuildPart() → add(part) → Locations(...)/PolarLocations(...) → Hole(...)`.
The comparison is the sha256 of the `.acm` payload `handle_build` writes, plus
every metric.

| part | route | sha256(`.acm`)[:16] | bytes | volume_mm³ | faces | edges |
|---|---|---|---:|---:|---:|---:|
| `construction/gusset_plate` | shipped | `56b50449ac4e6bc1` | 72044 | 320571.094852 | 20 | 54 |
| `construction/gusset_plate` | **helper** | `56b50449ac4e6bc1` | 72044 | 320571.094852 | 20 | 54 |
| `rocketry/flange` | shipped | `e983be0745dc9a39` | 94924 | 124081.241942 | 16 | 38 |
| `rocketry/flange` | **helper** | `e983be0745dc9a39` | 94924 | 124081.241942 | 16 | 38 |
| `prototyping/enclosure_lid` | shipped | `7c8698c9cfe08391` | 83092 | 19086.934339 | 48 | 128 |
| `prototyping/enclosure_lid` | **helper** | `7c8698c9cfe08391` | 83092 | 19086.934339 | 48 | 128 |

**sha identical: 3 of 3. Metric diffs: none, on any of the nine compared keys**
(`volume_mm3`, `area_mm2`, `mass_g`, `n_faces`, `n_edges`, `n_solids`,
`is_valid`, `bbox`, `center_of_mass` — compared with `==`, not `approx`).

Two details worth keeping, because they widen the claim past what the design
measured:

- The **flange** variant does not merely move the hole block: it closes the
  builder, runs the helper, and re-enters a *third* `BuildPart` to apply the two
  `chamfer()` calls to an `add()`-ed solid. Byte-identical anyway. So an
  operation *after* the helper does not disturb identity.
- The **lid** variant does the same around a `CounterSinkHole`, so identity is
  not special to `Hole`.

### The negative control — the `.acm` really is sensitive to the route

If any construction route produced the same bytes, the table above would prove
nothing. Cutting `gusset_plate`'s holes as `part - Compound(children=cylinders)`
instead, same params, same worker:

| route | sha256[:16] | volume_mm³ | faces | edges |
|---|---|---:|---:|---:|
| `Locations` + `Hole` (shipped) | `56b50449ac4e6bc1` | 320571.0948520879 | 20 | 54 |
| `Compound` subtraction | **`a00ec644efd8c9b7`** | 320571.09485208784 | 20 | 54 |

Same face count, same edge count, a volume differing by a relative **2e-16**
(inside `rel=1e-9` several million times over) — and a different mesh. That is
the whole reason restated-AC1 has a byte half, and the reason the harness
asserts a sha and not just numbers. It is also asserted as a live test
(`test_two_routes_to_one_geometry_differ_in_bytes`), so if a future OCCT makes
the two routes tessellate identically the harness announces that it has lost
its teeth instead of silently passing everything.

**Verdict: S1 holds. The design's Decision 1 mechanism is sound on real parts
through the real pipeline, and restated-AC1 (b) — "a byte-identical `.acm`
payload under the new key" — is reachable, not aspirational. No design
amendment needed.**

## Changes

- **`tests/test_examples_golden.py` (new)** — the harness. Goldens for all
  three `construction` parts plus `rocketry/flange` and
  `prototyping/enclosure_lid` as controls, captured **on a copy** at the params
  each bundled `project.json` stores, through `service._rebuild` (so the
  measurement covers the real pipeline: cache key, sidecar, and the `.acm` the
  mesh cache serves). Each golden is a literal in the test file — not a JSON
  fixture nobody reads — asserting `volume_mm3`/`area_mm2`/`mass_g`/`bbox`/
  `center_of_mass` at `rel=1e-9`, `n_faces`/`n_edges`/`n_solids`/`is_valid`
  exactly, the `.acm` byte length, and the `.acm` sha256.
- The module exports `measure_part`, `script_acm_sha256` and
  `assert_matches_golden` for slices 3, 4 and 7, so the identity assertion is
  written once rather than re-grown per slice.
- `test_goldens_cover_every_construction_part` fails if a part is added to the
  `construction` example without a golden — slice 7 rewrites that project, so an
  unmeasured part there is the exact hole this harness exists to close.
- **`tests/test_toolkit_ocp_free.py` (new)** — the fresh-interpreter scaffold:
  each listed module is imported in a subprocess with `OCP`/`build123d` blocked
  at `sys.meta_path`, a smoke expression is evaluated on it, and `"OCP" not in
  sys.modules` is asserted afterwards. It covers `agentcad.toolkit` (the lazy
  `__init__`), `sketch` and `specs` today; adding a module is one line, and
  slice 2 adds `hole_standards`. `test_ocp_free_list_matches_the_tree` keeps the
  list honest by classifying every `agentcad/toolkit/*.py` by its imports and
  demanding the two sets agree — a new server-side module with no probe, or a
  listed module that has grown a kernel import, both fail there by name.

## Files

- `tests/test_examples_golden.py` — **new** (goldens + the reusable identity probe)
- `tests/test_toolkit_ocp_free.py` — **new** (the OCP-free scaffold)
- `docs/changelog/0147-feature-toolkit-ii-baseline.md` — this entry

## Notes

- **The `abs=1e-9` floor on the float comparisons is not a loosening, it is a
  necessity.** Three goldens carry a nominal zero with float noise in it
  (`gusset_plate`'s `bbox.min.y` is `-1.5e-14`, `base_plate`'s centre of mass is
  `-2.1e-15` in x). A pure `rel=1e-9` against `-1.5e-14` demands agreement to
  1e-23. The floor is one nanometre.
- **A sha golden is a statement about this toolchain** — the pinned
  build123d/OCCT wheels on this platform. The failure modes are
  distinguishable and the test says so: if all five move together, suspect the
  wheels; if one moves, that is a geometry change and the point of the harness.
  The full suite runs on macOS only (`.github/workflows/ci.yml`); the
  Linux/Windows jobs run the `portability` group, which these are not in.
- **Two pre-existing failures were found by this slice's baseline run, and they
  are not caused by it.** `tests/test_prd009_acceptance.py::
  test_ac6_the_full_suite_count_is_cited` and `tests/test_prd008_acceptance.py::
  test_ac9_the_full_suite_count_is_cited` each require the *newest* changelog
  entry to cite a suite count, and `0146-prd-009-completed.md` says "Final
  suite: 2018 passed" without the literal string `make test`, which is what both
  assertions look for. They were red on `f684717` before a line of this slice
  was written, and they go green with this entry, which cites one properly.
  Recorded rather than silently fixed: the "2018 passed, 1 skipped" baseline in
  the plan was a green-suite claim that the tree did not support.
- The spike scripts (`spike_identity.py`, `spike_identity_control.py`) were run
  from the session scratchpad rather than a repo `scratchpad/` directory: the
  repo has no such directory and does not `.gitignore` one, so creating it
  would have put throwaway probes on a commit path. The numbers above are the
  artifact; the scripts are reproducible from this entry's description.

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_examples_golden.py tests/test_toolkit_ocp_free.py
12 passed in 4.11s
```

Full suite (`make test` equivalent, split because one process exceeds this
sandbox's foreground time cap; `-n 2 --dist loadscope` is what the Makefile
runs):

```
$ .venv/bin/python -m pytest -q -n 4 --dist loadscope tests/ --ignore=tests/test_examples.py
2044 passed, 1 skipped in 317.04s
$ .venv/bin/python -m pytest -q -n 2 tests/test_examples.py
20 passed in 938.12s
```

**`make test` total: 2064 passed, 1 skipped** across the two chunks, with slice
2 landed in the same run.

**The re-measured baseline, as the plan asked, is not the plan's number.** On
`f684717`, before a line of this slice, chunk A was **1996 passed, 2 failed, 1
skipped** and chunk B **20 passed** — i.e. **2016 passed, 2 failed, 1 skipped**,
not "2018 passed, 1 skipped". The two failures are the changelog-citation
assertions described above; 2018 is the *collected* count, and the note counted
them as green. This slice adds 11 tests (7 golden + 4 OCP-free) and turns the
two red ones green by citing a count properly. The one skip is the
long-standing `[fem]`-extra skip.
