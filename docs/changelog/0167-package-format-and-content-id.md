# 0167 — 2026-08-16 — PRD-011 slice 1: the package format, the content id, the frozen configuration schema

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The pure layer of PRD-011: `agentcad/core/packages/` opens with two modules
that read a directory and validate documents, and nothing else. `content.py`
defines the **content id** — a canonical file-tree digest, not an archive hash
— plus the published size ceilings and the path-containment rules;
`format.py` validates `package.json`, `index.json` and `presets.json`, hosts
the hand-rolled version-requirement grammar, and ships
`validate_configuration`, the **schema PRD-012 must adopt** (design Decision
4). No kernel, no service, no network; nothing imports either module yet, so
this slice is inert by construction.

## Changes

- `agentcad/core/packages/__init__.py` — new subpackage. Deliberately
  code-free: `pkgutil.iter_modules` already sees it as `packages`
  (`ispkg=True`) and the tool-pack loader filters on a `tools_` prefix, so it
  registers nothing and cannot be mistaken for a pack.
- `agentcad/core/packages/content.py` —
  - `inventory(root)` → `[(posix_relpath, bytes, sha256_hex)]` sorted by path,
    honouring the published ignore list (`.git/`, `__pycache__/`, `*.pyc`,
    `.DS_Store`, `*.tmp`) and **refusing every symlink**, file or directory.
  - `content_id(root)` / `content_id_of(entries)` = `"sha256:" +
    sha256("".join(f"{path}\0{sha}\n"))`. No mtimes, no modes, no walk order.
  - `check_ceilings(entries)` — 50 MB per package, 5 MB per file, 500 files.
  - `first_difference(expected, actual)` — the first path two inventories
    disagree on (added, removed or changed), which is what makes a tamper
    report actionable.
  - `resolve_within(root, relpath)` / `is_safe_relpath(value)` — the
    containment check every declared path goes through.
  - `problem(code, message, field)` — the one problem shape every validator in
    the subpackage returns. A problem is data, never an exception: the gate
    turns each one into a PRD-004 row.
- `agentcad/core/packages/format.py` —
  - `validate_package_manifest(doc, root=None)`, `validate_index(doc)`,
    `validate_presets(doc, parts)`, `validate_configuration(entry,
    params_spec)`. **Unknown keys are problems at every level**, including
    inside `authors[]`, `provenance`, `parts.<id>`, an index entry and a
    configuration.
  - the version grammar: `parse_version`, `compare`, `satisfies`, `resolve`
    over `X.Y.Z`, `^X.Y.Z`, `~X.Y.Z`, `*`/omitted; `resolve` returns the
    highest **non-yanked** match and skips a version key it cannot parse.
  - constants: `PACKAGE_FORMAT`/`INDEX_FORMAT`/`PRESETS_FORMAT` = 1,
    `NAME_RE`, `VERSION_RE`, `CONFIG_RE`, `DISCLOSURES`, `INDEX_SCOPES`,
    `GATE_STATUSES`, and the required/optional key sets.
- `tests/test_packages_format.py` — 136 tests.
- `tests/test_packages_ocp_free.py` — a fresh-interpreter probe per module
  with `OCP`/`build123d` blocked at `sys.meta_path` (the
  `tests/test_checks.py::_NO_KERNEL_PROBE` pattern), a
  list-matches-the-tree test so a new module cannot be added without a probe,
  and a static source scan that also catches a kernel import hidden inside a
  function.

## Files

- `agentcad/core/packages/__init__.py` — new
- `agentcad/core/packages/content.py` — new
- `agentcad/core/packages/format.py` — new
- `tests/test_packages_format.py` — new
- `tests/test_packages_ocp_free.py` — new

## Divergences from the plan, and why

- **`VERSION_RE` refuses leading zeros.** The spec and plan write the grammar
  as `^\d+\.\d+\.\d+$`, which accepts `01.2.3` — two spellings of one version,
  two index keys, and `compare("01.2.3", "1.2.3") == 0`. semver's own grammar
  forbids the leading zero for exactly that reason. The implemented pattern is
  `^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`; the reason is a comment beside
  it. This is free now and breaking after the first package ships.
- **`summary` is a required manifest field**, which the plan's task list does
  not name (it appears in Decision 2's JSON but not its table). Every search
  hit and every index entry carries it, so a package without one publishes an
  empty listing. Recorded here rather than assumed.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_format.py tests/test_packages_ocp_free.py
144 passed
```

Full suite, with the whole of PRD-011 slices 1–3 in the tree:

```
.venv/bin/python -m pytest -q -n 2 --dist loadscope -rs
2763 passed, 1 skipped in 25:06
```

Baseline on this branch before slice 1 was **2527 passed, 1 skipped** (2528
collected); the three slices add **236** tests. `make test` is that command
(`test-full`). The single skip is pre-existing and explained —
`tests/test_analysis.py:166: agentcad[fem] installed; the 501 fallback is
unreachable`. The number is cited in all three of this sequence's entries
because the three slices were built and verified as one run; nothing between
them changes the count.

## Notes

- **`^0.x` is not npm's `^0.x`.** Here `^0.1.0` means `>=0.1.0, <1.0.0`; npm
  treats a `0.x` caret as `~`. A reader will assume npm, so the docstring says
  so and `test_caret_zero_is_not_npms_caret_zero` pins it.
- **What the content-id tests attack, not just assert:** two files swapping
  contents (a digest over sorted hashes alone would call the two packages
  identical), an added *empty* file, every mtime rewritten, the same files
  created in a different order, a symlink pointing at a file *inside* the
  root (still refused — two paths for one file make the id a function of how
  the tree was built), and the exact listing formula recomputed by hand.
- **The PRD-012 freeze is two tests, not a comment.** A PRD-012-shaped
  `configs` map validates through `validate_configuration` unchanged, and the
  flat `{"s": {"width": 10}}` shape is refused with the ambiguity named in the
  message: *"a part may declare a parameter called 'label'"*.
- `validate_configuration` takes `params_spec=None` (shape only) or the
  kernel's normalized `inspect` spec; a bool is refused for a numeric
  parameter even though JSON says `True == 1`.
- Ceilings are the spec's published numbers; slice 2 measures what they buy
  (full-tree re-verification cost).
