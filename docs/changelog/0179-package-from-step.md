# 0179 — 2026-08-16 — PRD-011 slice 13: `package_from_step`, the McMaster path

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

FR13. A supplier hands you a STEP; `package_from_step` wraps it as a
**reference-part package** — the file under `imports/`, a `kind: "reference"`
part entry, `provenance.vendor` with `redistributable: false`, a README naming
the vendor and the terms, and a rendered preview — and the nine-stage publish
gate measures it green without a single line of the package being a script.
The confinement FR13 asks for is the mechanism slice 8 already built:
`redistributable: false` into a `public` index is a refusal that writes
nothing, and the same package into a `private` index publishes.

**Connector placement is not automated, and the tool says so** in its
description, in the payload, and in the README it writes. What it does is
report the imported solid's own B-rep faces — planar with a normal and a
centre, cylindrical with an axis and a radius — largest first, as candidates
an author turns into a `connectors` function. That is the design spec's
divergence 7, delivered as written.

## Changes

- `agentcad/kernel/handlers/reffaces.py` (new — a handler pack) —
  `reference_faces {source_path, limit?}` → `{n_faces, kinds, limit,
  truncated, faces[]}` over `load_reference`. Indices are the
  `TopExp_Explorer(FACE)` walk `face_info` and `mesh.py` use; rows are sorted
  by area and capped (a vendor assembly carries thousands of faces and a
  payload with all of them is not a suggestion list). An **STL is refused**,
  not answered with an empty list.
- `agentcad/core/packages/from_step.py` (new) — `scaffold(service, …)`:
  validate → **build the vendor file once in a throwaway cell** → read the
  candidates → render the preview → write the directory. Every refusal
  happens before a byte is written. The cell is `gate._ephemeral_service`
  reused, so all three seams are nulled and no user project is opened.
- `agentcad/core/packages/format.py` — a part entry is a `script` (`file`
  inside `parts/`) or a `reference` (`source` inside `imports/`). `kind` is
  optional and **absent means `script`**, because every package published
  before this slice declares none and a version is immutable. An entry may not
  carry the other kind's key. `part_kind` / `part_payload` are the accessors
  every other module now reads a part entry through.
- `agentcad/core/packages/gate.py` — reference parts through all nine stages:
  `contract`, `connectors` and `policy` emit one exempt `skip /
  reference_part` row each; `presets` **fails** a configuration that names a
  reference part; `build` stages the file into the cell's own `imports/` and
  builds it once; `docs` requires the summary and cannot require a module
  docstring; `previews` renders it like any other part; and `is_valid: false`
  on imported geometry is **reported, never enforced**.
- `agentcad/core/tools_packages.py` — the seventh tool, and `use_part`'s
  explicit refusal of a reference part (see the divergences).
- `tests/test_packages_from_step.py` (new) — 33 tests.
- `tests/test_packages_ocp_free.py` — a probe for `from_step`.

## Divergences from the plan, and why

- **This slice adds a kernel file, which the plan's global constraints say it
  would not.** Task 2 requires "`face_info` candidates (planar faces by area,
  cylindrical faces by axis)", and neither existing surface can produce them:
  `face_info` takes a **script** and builds it, and a reference part has none;
  the mesh-derived alternative (PRD-008's `anchors.signature_table`, which
  reads `.acm` + `.faces.u32` with no kernel call) returns **zero rows for a
  reference part** — measured, because the reference build path writes no
  `.faces.u32` sidecar at all — and even with one, an area-weighted normal
  over a closed cylinder nearly cancels, so a cylinder's *axis* is not in it.
  So the candidates come from a **handler pack**, `kernel/handlers/
  reffaces.py`, which is the sanctioned extension point and the only place
  allowed to import OCP. The alternative was to ship Task 2 as a bounding box
  and call it connector assist.
- **`use_part` REFUSES a reference part, and that is FR13's one hole in v1.**
  The provenance header lives *inside the script* (design Decision 5) and a
  reference part has no script, so a materialised one could carry no
  provenance at all — `provenance.scan` already skips `kind == "reference"`
  for the same reason, and `get_part` would have nothing to compute a status
  from. Inventing a per-part manifest field to hold it would freeze a schema
  in the last slice of the feature. So the package **validates, publishes,
  resolves and installs**, and materialising it into a project is
  `import_cad_file` over the cached file. The refusal is a `validation_error`
  that says exactly that and names the cached path. Stated here, in the tool
  description and in `docs/packages.md` rather than discovered.
- **A configuration on a reference part is a `fail`, not a skip.** Every other
  reference-part row is an exempt skip, because "no script" is a fact about
  the kind. A package that ships a configuration for imported geometry is
  *wrong* — there is nothing for it to set — so this one reddens.
- **`SUPPORTED_EXTS` includes `.brep`.** The plan and the tool name say STEP;
  BREP is the same thing (an exact B-rep with real faces) through the same
  loader, and excluding it would have been a restriction with no reason behind
  it. STL is the one that is refused, and it is refused for two reasons, both
  in the message: one welded triangulation face with no surface (nothing to
  suggest connectors from) and booleans that segfault OCCT.
- **The per-file ceiling is refused at the scaffold, with the number.**
  `content.MAX_FILE_BYTES` is 5 MB and a vendor STEP can exceed it — the
  design spec's risk list anticipates "a reference-part package carrying a
  40 MB STEP". The ceiling was **not raised**: it is part of the format, every
  consumer enforces it on install, and a version published above it would be
  uninstallable by a client that pinned the old number. So the refusal names
  the number and the alternative (keep it as a project import). This is a real
  limit on FR13 and it is documented rather than quietly worked around.
- **The gate stages a reference part's file into its own cell.** `_rebuild`
  hands the worker `store.imports_dir(proj) / source`, so the file has to be
  inside the gate's own project. The copy goes cell-ward only — the package
  directory is still read-only to the gate, and
  `test_the_gate_never_writes_into_the_package` re-hashes it after the run.
- **`is_valid: false` on imported geometry is a `pass` with a warning**, which
  is PRD-004's rule imported into the gate rather than a new judgement: OCCT
  calls the shipped 180-solid `examples/rocketry` STEP invalid, which is why
  `tests/test_examples.py` exempts reference parts. Enforcing it would redden
  correct vendor content nobody could fix. The exemption is tied to the part
  being a *reference*, not to the flag: the same result on a script part is
  still a `fail`, and a test asserts both directions.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_from_step.py tests/test_packages_ocp_free.py
47 passed in 4.91s
```

(33 in this slice's module; 14 OCP-free probes, one of them new.)

The rest of the PRD-011 surface, unchanged by a new part kind:

```
.venv/bin/python -m pytest -q tests/test_packages_gate.py tests/test_packages_publish.py \
    tests/test_packages_tools.py tests/test_packages_format.py \
    tests/test_packages_index.py tests/test_packages_cache.py \
    tests/test_packages_api.py tests/test_packages_cli.py
453 passed in 52.98s
```

(Re-measured after slice 14's row-detail change, which touched no assertion
here.)

The real command, on a real scaffolded vendor package:

```
$ .venv/bin/agentcad package validate <scratch>/vendor_pkg \
      --projects-dir <scratch>/cliprojects --work-dir <scratch>/work
acme_bracket@1.0.0 · sha256:d9ebda938478f9db98f77f5b83c90f6b40b53ba11a9a36fd178b2ffb9ab655e4
  stage       status  pass  fail  skip  error  total     time
  format      green      5     0     0      0      5    0.0 s
  contract    green      0     0     1      0      1    0.0 s
  presets     skip       0     0     0      0      0    0.0 s  (no_presets_declared)
  build       green      1     0     0      0      1    0.0 s
  specs       skip       0     0     0      0      0    0.0 s  (not_declared)
  connectors  green      0     0     1      0      1    0.0 s
  previews    green      2     0     0      0      2    0.0 s
  docs        green      2     0     0      0      2    0.0 s
  policy      green      0     0     1      0      1    0.0 s
not measured (exempt from the publish verdict):
  - connectors:reference_part
  - contract:reference_part
  - policy:no_policy_configured
  - presets:no_presets_declared
  - specs:not_declared
package validate: green — acme_bracket@1.0.0 · 10 passed, 0 failed, 3 skipped,
0 errors of 13 in 0.4 s · publishable: yes (exit 0)
$ echo $?
0
```

Five exempt skips and no blocker — which is the point of the `exempt_skips`
list being published: a consumer of this package reads *what was not
measured* (it declares no parameters, no configurations, no specs and no
connectors) instead of inferring that a green gate means the same thing it
means for `iso4762`.

## Notes

- **The scaffold measures before it writes.** A STEP this kernel cannot load
  is a `validation_error` and **no directory**, which is a whole test: the
  alternative — scaffold first, discover at gate time — leaves a half-package
  somebody has to clean up, and a package version is immutable so the fix
  would be a version bump.
- **No machine fact reaches the scaffolded files.** Two scaffolds of one
  source write byte-identical `package.json` and `README.md`; the rule that
  makes a lock entry reproducible applies here for the same reason.
- **What the tests attack:** a `.STEP` extension that has to normalise to
  `.step`, a filename that must reduce to a basename, a non-empty destination
  (refused, and the file that was already there is still there), a source
  above the ceiling, a broken STEP, a configuration declared on a reference
  part, a missing summary, a policy that raises if it is ever handed a
  reference part, an index whose scope is public, and `use_part` on the
  finished package.
- **`reference_faces` on the fixture** — a 30 × 20 × 10 block with a ⌀8 bore
  — reports `{"planar": 6, "cylindrical": 1}`, the bore's axis as
  `(0, 0, ±1)` and its radius as 4.0. That axis is the thing this slice added
  a kernel handler for; nothing derived from a mesh could report it.
