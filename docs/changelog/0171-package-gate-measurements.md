# 0171 — 2026-08-16 — PRD-011 slice 5: the publish gate, part B — extremes, the fan-out, specs, connectors, previews

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The sentence "it is in the registry" gets its meaning here. `gate.py` grows
the six stages slice 4 left as `not_implemented`: every part is built at every
declared extreme **and** at every declared configuration (a sum, never a cross
product), PRD-003's specs are evaluated over the whole variant set, every
declared connector is mated in one assembly round trip, each part is rendered
and its shipped preview parsed, the docs are checked, and the policy seam is
either called or honestly skipped. **AC2 is won here**, by two committed
fixture packages that are wrong in exactly one way each. The build fan-out was
measured rather than assumed, and it stays: **1.55x** on a 3-worker pool for a
real-thread cap screw.

## Changes

- `agentcad/core/packages/gate.py`
  - `variants(part, params_spec, presets)` → `[Variant(id, params, label)]`,
    plus `unswept(params_spec)`. The default, then **one parameter at a time**
    (`min`/`max` · `True`/`False` · every enum choice · nothing for a string),
    then every configuration. Count is `1 + Σ|sweep| + |presets|`, and a test
    asserts the arithmetic against the product it is not.
  - Stage `build`: one scratch part per variant, all created **first** through
    `store.add_part`/`store.update_part_entry` (`service.create_part` returns
    `get_part`, which builds — a dozen variants through it would serialise a
    dozen builds before the fan-out could see them), then `service._rebuild`
    per variant on a `ThreadPoolExecutor(jobs or min(pool_size, 4))`. A
    failure carries `KernelError.to_payload()` **verbatim**; a variant that
    builds to zero volume or `is_valid: false` is a `fail`, not a pass with a
    note; a string parameter contributes one `skip / string_param_unbounded`
    row (exempt).
  - Stage `specs`: `service.specs.run(scratch)` — read *inside* the method —
    over a project that by now holds one part per variant, so a package's
    SPECS are evaluated at every extreme rather than only at its default. Rows
    keep PRD-003's statuses and requirements; only the subject is renamed from
    the scratch part id to the variant. A skip whose reason is in
    `PUBLISH_SKIP_EXEMPT` (today `fem_extra_missing`) is marked
    `strict_exempt`.
  - Stage `connectors`: one `kernel.request("connectors", …)` per part — **the
    first server-side consumer that handler has ever had** — then one scratch
    assembly with an anchor per part and one mated instance per connector, and
    a single `set_assembly` (which returns `get_assembly`, so it is one
    `resolve_mates` round trip and not two). The mover is the part itself when
    it declares a rigid connector and the bundled `PROBE_SCRIPT` cube when it
    does not, because the moving side of a mate must be rigid. A part with no
    connectors is one `skip / no_connectors_declared` (exempt).
  - Stage `previews`: `render_view` through the registry per part, plus the
    **shipped** `previews/<part>*.png` parsed by `_png_problem` (signature,
    IHDR with a matching CRC and positive dimensions, `IEND`). No image
    library and **no pixel comparison** — renderer drift would redden correct
    content, and the row says so.
  - Stage `docs`: the README names every declared part, and every part has a
    `summary` in `package.json` and a module docstring.
  - Stage `policy`: `service.package_policy` if one is installed, its rows
    re-shaped through `make_item` (a third-party policy may not emit a row
    that breaks every consumer), a raising policy is one `error` row; with
    none configured, one `skip / no_policy_configured` (exempt).
  - `IMPLEMENTED_STAGES == GATE_STAGES`; `STAGE_SKIP_EXEMPT` gains
    `not_declared`.
- `tests/fixtures/packages/` (new)
  - `widget_good/` — the green fixture: a bored, chamfered block with two
    connectors, two SPECS, two presets, docs and a real committed preview.
  - `break_at_extreme/` — AC2a: builds at its default, raises at `length=max`.
  - `broken_connector/` — AC2b: builds everywhere, returns `"up"` where an
    axis belongs.
- `tests/test_packages_gate.py` — 65 → 97 tests.

## Measurement — does the fan-out pay?

The plan's rule: *under 1.5x on a 3-worker pool, delete the
ThreadPoolExecutor and ship the gate serial.* Measured with **every worker
pre-warmed** (the pool spawns lazily, and paying two cold ~3 s build123d
imports serially would have flattered the parallel run by ~0.4x), a fresh cell
per run, median of 3, dev machine:

| package | variants | `jobs=1` | `jobs=3` | speedup |
|---|---|---|---|---|
| real-thread ISO 4762 cap screw | 9 | **7.85 s** | **5.05 s** | **1.55x** |
| `widget_good` (cheap builds) | 11 | 0.22 s | 0.19 s | 1.15x |

So it stays — on the workload the seed catalog actually is, and **not by
much**. The ceiling is load imbalance, not the pool: `KernelPool._pick` routes
by `hash(affinity) % size`, so nine builds of unequal cost land unevenly on
three workers. Two honest consequences, both in the code:

- with a single `KernelClient` — the default, and the whole test suite —
  `jobs` resolves to 1 and `_fan_out` is a plain loop;
- `jobs=1` is a first-class path, and
  `test_jobs_one_and_jobs_four_produce_identical_reports` pins that the two
  agree row for row.

## Files

- `agentcad/core/packages/gate.py` — six stages, the variant matrix, the fan-out
- `tests/fixtures/packages/widget_good/**` — new (7 files incl. a preview PNG)
- `tests/fixtures/packages/break_at_extreme/**` — new
- `tests/fixtures/packages/broken_connector/**` — new
- `tests/test_packages_gate.py` — the slice-5 section

## Divergences from the plan, and why

- **Variants are created through the store, not `service.create_part`.** The
  plan says "serial `add_part`, then `set_params`", which reads as the service
  methods — but `create_part` returns `get_part`, which calls `_ensure_built`,
  and `set_params` ends in `_rebuild`. Both build **eagerly and serially**, so
  using them would have built every variant before the fan-out existed. The
  gate writes the manifest entries through `ProjectStore` (the same layer the
  service itself uses) and lets `_rebuild` be the only thing that builds.
- **The default variant reuses the scratch part the other stages already
  made.** Two parts with the same script and no overrides are one part;
  creating a second reported every spec twice (observed: two
  `mount_block@default:valid` rows and a duplicate-id warning) and built the
  same cache key twice.
- **A failed batch mate falls back to one round trip per connector.** The
  design spec says one assembly and one `resolve_mates`; that holds on the
  green path. When the batch raises, the resolver's message names one
  instance, so attribution costs N calls — paid only by a package that is
  already wrong, which is when naming the culprit is worth it.
- **The `connectors` stage emits a per-part row as well as a row per
  connector.** Without it a package's declared connector *types* never appear
  in the report, and slice 8 derives the index entry's `connectors` digest
  from the gate's own measurements.
- **The specs stage measures every variant, not just the default.** That falls
  out of `SpecRunner.run(project)` over a project holding one part per
  variant, and it is a stronger claim than the plan asked for: `check_wall` on
  a package's thinnest configuration is exactly the check a catalog wants.
- **The `docs` stage adds one requirement the plan did not name: the README
  must mention every declared part id.** The plan says "README plus a summary
  and a module docstring per part", and the README half would otherwise
  duplicate the `format` stage's presence-and-floor check exactly. Naming the
  parts is the cheapest check that distinguishes documentation from prose, and
  it caught both AC2 fixtures' READMEs on the first run. It is a *fail*, so it
  is worth calling out: a package whose README refers to a part only by its
  label ("Mounting block") and never by its id will be red until it names the
  id. That is deliberate — the id is what `use_part` takes.
- **`exempt_skips` mixes two shapes:** a bare reason for a row-level exempt
  skip (`no_policy_configured`) and `<stage>:<reason>` for a stage-level one
  (`specs:not_declared`). Slice 8 copies this list into the published index
  entry's `gate.exempt_skips`, so the shape is about to become part of the
  format — it is called out here rather than discovered there.
- **Added, not asked for: the run refuses a moving target.** The content id is
  re-computed after the stages, and a package directory that changed while the
  gate was running makes the report `complete: false` (exit 2) with both ids in
  `warnings[]`. Nothing in the gate can move the tree — it never writes into
  the package — but an editor, a build script or a concurrent `git checkout`
  can, and slice 8 is about to publish that id as *what was measured*. The
  re-hash costs ~1 ms on a realistic package (changelog 0168's measurement),
  which is the whole argument for doing it unconditionally.
- **`STAGE_SKIP_EXEMPT` gains `not_declared`.** A package that ships no SPECS
  is legitimate — the same judgement `no_connectors_declared` already encodes
  for a plain solid. Both stage-level exemptions are recorded in
  `report["exempt_skips"]` as `<stage>:<reason>`, so a consumer reads what was
  not measured instead of inferring it.
- **The `fem_extra_missing` case is tested through `_spec_item` and
  `verdict`, not on a fem-less machine.** This machine has the `[fem]` extra
  installed (`tests/test_analysis.py`'s one explained skip says so), so a
  test that waited for a real FEM skip would assert nothing here. The row
  shape and the verdict arithmetic are what the claim rests on, and both are
  pinned.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_gate.py tests/test_packages_ocp_free.py
106 passed in 6.98s
```

The gate over the green fixture, in full (`agentcad package validate`'s output
in slice 6 is the same document): **9 stages, 52 passed, 1 skipped
(`no_policy_configured`), 0 failed, `publishable: true`, exit 0, 0.52 s.**

Full suite, with PRD-011 slices 4–6 in the tree:

```
.venv/bin/python -m pytest -q -n 2 --dist loadscope -rs
2885 passed, 1 skipped in 25:06
```

The baseline after slices 1–3 was **2763 passed, 1 skipped** (changelogs
0167–0169); slices 4–6 add **122** tests (97 gate + 24 CLI + 1 OCP-free
probe). `make test` is that command (`test-full`). The single skip is
pre-existing and explained — `tests/test_analysis.py:166: agentcad[fem]
installed; the 501 fallback is unreachable`. The number is cited in all three
of this sequence's entries because the three slices were built and verified as
one run; nothing between them changes the count.

## Notes

- **What the AC2 fixtures attack.** `break_at_extreme` is red on exactly one
  row — `build:strut@length=max` — with the traceback in `error.details`,
  while `strut@default` and `strut@length=min` pass: a gate that only built
  the default would call it green. `broken_connector` builds at every extreme
  and is red only at `connectors:bracket`, with the kernel's message naming
  `pivot`.
- **The gate found a bug in its own fixture** on first run: `mount_block`
  chamfered the bore ring as well as the outer edges, which fails at
  `bore_d=max` in a `std` block where the wall is 2 mm. The fix is a modelling
  answer (chamfer the outer edges only), which is exactly the loop this
  feature exists to shorten, and the comment in the fixture records it.
- **`_png_problem` is a real parse, not an extension check** — a truncated
  PNG fails on the missing `IEND` and a corrupted header on the IHDR CRC.
  Pillow is not a dependency of this project and one preview check is not a
  good enough reason to make it one.
- The variant matrix's claim, stated once and repeated nowhere else: *each
  parameter's own range, and every configuration the package ships.* Never
  "every combination". `widget_good`'s `wide_16` preset is the demonstration —
  a corner the sweep cannot reach, declared by the author, built by the gate.
