# Parts library & package registry — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship
[PRD-011](../../prd/in-progress/PRD-011-parts-library-registry.md) as **step 1
of the marketplace chain** — a published, versioned, content-hashed package
format; a lockfile and a content-verified cache that install offline; local and
git-hosted indexes behind one client; a publish gate that builds every
parameter extreme, runs the specs and mates every connector on a throwaway
scratch project; and a seeded, gate-refereed catalog that ships in the repo and
doubles as the first git index — per
[the design spec](../specs/2026-08-16-parts-library-registry-design.md).

**Architecture (one paragraph):** `agentcad/core/packages/` is a nine-module
subpackage that touches no geometry: a package is a *directory*, its identity
is a canonical file-tree digest (`content.py`), the cache is
`~/.agentcad/packages/<name>/<version>/` with sibling receipts (`cache.py`),
the two additive manifest maps `packages`/`packages_lock` carry only
content-determined values so two branches adding the same package merge clean
(`lockfile.py` + two new key-wise heads in `manifest_merge.py`), and three
index kinds reduce to one client because a git index is a local index plus a
fetch (`indexes.py`). `gate.py` measures nothing new: it materialises the
package into a scratch project inside a throwaway cell, drives a **second,
ephemeral `AgentCADService`** sharing the warm kernel with `bus.on_publish`,
`store.branch_resolver` and `store.write_guard` all nulled, and sequences
`inspect` → `set_params` → `_rebuild` (fanned out across the pool) →
`service.specs.run` → `connectors` + `mates.resolve` → `render_view`, shaping
PRD-004's report. `use_part` copies the script in with an immutable,
timestamp-free provenance header whose status is recomputed on every read.
The tool pack is `core/tools_packages.py`; the CLI gains
`agentcad package validate` and `agentcad publish`; the seed catalog lives at
`catalog/` and is auto-registered as a local index.

**Tech stack:** Python 3.12 stdlib only (`hashlib`, `json`, `tokenize`, `ast`,
`shutil`, `tempfile`, `subprocess`, `concurrent.futures`) / FastAPI route pack
/ pytest with the session-scoped `kernel` fixture. **No new runtime
dependency** — semver, the schema validators and the search ranking are all
hand-rolled, on PRD-004's precedent.

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** This plan adds
  **zero** kernel files. Every module under `agentcad/core/packages/` and
  `core/tools_packages.py` must import neither, directly or transitively —
  asserted in a fresh interpreter with both blocked at `sys.meta_path`
  (`tests/test_checks.py::_NO_KERNEL_PROBE` is the pattern), plus a
  list-matches-the-tree test on the model of
  `tests/test_toolkit_ocp_free.py::test_ocp_free_list_matches_the_tree`.
- **Do not edit `worker.py`, `tools.py`, `app.py` or `service.py`.** New
  capability arrives as a tool pack (`core/tools_packages.py`) and a route pack
  (`server/routes_packages.py`), found by the existing loaders. `get_part` /
  `get_project` are extended by **wrapping the bound methods** with an
  idempotent attribute marker (`tools_specs.install_rebuild_specs` is the
  precedent), never by editing the service.
- **Do not edit `proposals.py`, `packet.py`, `merge.py`, `branches.py`,
  `history.py`, `specs.py`, `checks.py` or `project.py`.** PRD-001–008 are
  finished; this feature *consumes* them. `core/checks.py`'s `make_item`,
  `make_stage`, `finalize_report`, `exit_code` and `core/specs.py`'s
  `summarize`, `report_status`, `assign_ids` are **imported, never
  re-implemented**.
- **Exactly two edits to existing non-test Python files** in the whole plan:
  (a) `core/manifest_merge.py` — two new key-wise heads in `_merge_section`
  and `_write_path` (slice 2); (b) `agentcad/cli.py` — the `package` and
  `publish` subcommands plus their `main()` branches and the subparser
  `metavar` (slices 6 and 8), following `cmd_check`'s shape. Docs, catalog and
  changelog aside, **any other diff to an existing non-test file is a design
  bug — stop and re-read the design spec.**
- **The tool pack is `agentcad/core/tools_packages.py` and it registers NO
  gate provider.** `tools._load_tool_packs` walks `pkgutil.iter_modules`
  alphabetically and `tools_proposals.py:51` assigns
  `service.gate_providers = []` **unconditionally**; `pac` sorts before `pro`,
  so an appended provider would be silently discarded. This is the
  `tools_run_checks.py` trap. The module docstring states the fact and the
  prohibition; a test pins it; and the escape hatch — a second pack named
  `tools_publish.py` (`pub` > `pro`), or a lazy install from
  `routes_packages.py` — is named in the docstring so nobody rediscovers it
  the hard way.
- **At `pac` nothing later is captured at registration.** `service.specs`
  (`s`), `service.branches` (`v`), `service.proposals` and
  `service.gate_providers` (`p`) do not exist when `register()` runs. Read
  them **inside methods**, with `getattr(service, "specs", None)` guards.
- **Nothing in the gate ever opens a user project.** The gate creates its own
  project inside a throwaway cell under a work dir it has proven does not
  overlap the projects root or the package source directory
  (`checks._refuse_overlap` is the model), and deletes only the cell it made.
  The three ephemeral seams — `bus.on_publish`, `store.branch_resolver`,
  `store.write_guard` — are nulled, the last two **after** `build_registry`.
  Unlike PRD-004's runner this one really writes, so the write guard is live
  here and nulling it is load-bearing.
- **`--work-dir` is a CLI-only flag.** The seatbelt profile is fixed at worker
  spawn (`cli._build_service`), so the tool and route paths always use the
  system temp dir, which `_writable_roots` already grants.
- **Determinism over telemetry.** No timestamp, client id or absolute path
  goes into a materialised script header or into `packages_lock`. Both are
  git-tracked; a machine fact in either breaks byte-identical
  re-materialisation (AC3) and makes concurrent adds conflict. Machine facts
  live in `~/.agentcad/packages/<name>/.receipts/<version>.json`.
- **Row statuses are PRD-003's four** (`pass|fail|skip|error`), summary counts
  its five, stage/report statuses its three. Rows are **`items`**, never
  `checks`. Errors are the house three (`validation_error`, `conflict_error`,
  `not_found_error`); **no new error class and no new event type.**
- **The gate is not a security boundary** — the sentence from Decision 11 goes
  in every surface listed there, in the same slice that creates the surface.
- Atomic writes everywhere (`ProjectStore._atomic_write`); a cache install is
  a staging directory plus `os.replace`.
- Never `uv sync` / `uv pip install` from a parallel agent; use a scratch venv.
  Examples tests run on a **copy**. `TestClient(base_url="http://127.0.0.1")`,
  `create_app(..., extra_allowed_hosts={"testserver"})`.
- **Baseline: 2 528 tests collected** on this branch today
  (`.venv/bin/python -m pytest --collect-only -q`). Record the real
  `make test` pass count before slice 1 and cite it in every slice's
  verification. No unexplained skips.
- **Verification before completion, every slice:** run the named commands and
  cite their real output. "Should pass" is not a result.
- Every slice lands with its `docs/changelog/NNNN-<slug>.md` entry staged
  (next number after `0165`).

---

## Slice map

| # | Slice | Lands | Changelog |
|---|---|---|---|
| 1 | Format, content id, configuration schema — pure, no I/O beyond reading a tree | FR1, FR2; the frozen PRD-012 schema | `0166-package-format-and-content-id.md` |
| 2 | Cache, receipts, verification, lockfile, manifest merge heads | FR3, FR5; AC3 (tamper half) | `0167-package-cache-and-lockfile.md` |
| 3 | Local index client, resolution, offline install | FR7 (local); AC3 (offline half) | `0168-local-package-index.md` |
| 4 | The gate, part A: the cell, the ephemeral service, `format`/`contract`/`presets` | FR9 (containment + data stages) | `0169-package-gate-scaffold.md` |
| 5 | The gate, part B: extremes, fan-out, specs, connectors, previews, policy seam | FR9; AC2 | `0170-package-gate-measurements.md` |
| 6 | `agentcad package validate` | FR9 CLI half | `0171-package-validate-cli.md` |
| 7 | Tool pack: the six tools, provenance status, `get_part`/`get_project` | FR4, FR6, FR15; AC1, AC6 | `0172-package-tools.md` |
| 8 | Publishing: `agentcad publish`, index writes, immutability, yank, vendor scope | FR10; AC5 | `0173-package-publish.md` |
| 9 | The git index client | FR7 (git); AC4 | `0174-git-package-index.md` |
| 10 | Seed catalog I: `iso4762`, bundled index auto-registration, dogfood | FR11; AC1 | `0175-seed-catalog-iso4762.md` |
| 11 | Route pack + the Library dialog | FR7 UI; AC7 | `0176-package-routes-and-library-ui.md` |
| 12 | Seed catalog II: the COTS starter set | FR12 | `0177-cots-starter-set.md` |
| 13 | `package_from_step` — the McMaster path | FR13 | `0178-package-from-step.md` |
| 14 | Docs, acceptance tests, PRD close-out | AC1–AC8 | `0179-prd-011-docs-and-acceptance.md` |

Slices 1–10 are the registry-first core and land **before** any UI and before
the McMaster path, per the roadmap's sequencing decision. Each is independently
landable: 1 is pure functions nothing imports; 2 makes them persist; 3 makes
them resolve; 4–5 make them provable; 6 makes the gate usable; 7 makes the
whole thing reachable by agents; 8 closes the publish loop; 9 makes a repo an
index; 10 puts real content behind it.

---

## Slice 1 — the pure layer: format, content id, configuration schema

**Why first:** it is the contract every later slice is written against, it has
no kernel and no service, and it is where the two frozen decisions (the
published format and the PRD-012 configuration schema) are actually won.

### Files
- `agentcad/core/packages/__init__.py` (new)
- `agentcad/core/packages/format.py` (new)
- `agentcad/core/packages/content.py` (new)
- `tests/test_packages_format.py` (new)

### The shapes (copy from the design spec, Decisions 2, 3, 4)

```python
PACKAGE_FORMAT = 1
INDEX_FORMAT = 1
PRESETS_FORMAT = 1

NAME_RE    = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")        # no prereleases in v1
CONFIG_RE  = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES    = 5 * 1024 * 1024
MAX_FILES         = 500
IGNORED = (".git/", "__pycache__/", "*.pyc", ".DS_Store", "*.tmp")
```

### Tasks

- [ ] **Task 1 — `content.py`: the inventory and the content id.**
  `inventory(root) -> [(posix_relpath, bytes_len, sha256_hex)]` sorted by
  path, honouring `IGNORED`, refusing symlinks and any path that escapes the
  root. `content_id(root) -> "sha256:<hex>"` over
  `"".join(f"{path}\0{sha}\n")` — no mtimes, no modes, no walk order.
  `check_ceilings(inventory) -> [problems]`.
- [ ] **Task 2 — `format.py`: `validate_package_manifest(doc) -> [problems]`.**
  Every field from Decision 2's table; **unknown keys are problems**, not
  ignored. `disclosure` required and one of `human|agent|hybrid`; `license`
  required non-empty; `authors` a non-empty list of objects with `name`;
  `remix` and `requires` validated when present and otherwise `null`;
  `parts` a non-empty map whose `file` is inside `parts/` and exists.
- [ ] **Task 3 — semver, hand-rolled.** `parse_version`, `compare`,
  `satisfies(version, requirement)` for `X.Y.Z`, `^X.Y.Z`, `~X.Y.Z`, `*`,
  and `resolve(versions, requirement, allow_yanked=False) -> str | None`
  returning the **highest non-yanked** match. Table-driven tests including
  `^0.x` (which is `>=0.x.y, <1.0.0` here — say so in the docstring, because
  npm's `^0` rule differs and a reader will assume npm).
- [ ] **Task 4 — `validate_configuration(entry, params_spec) -> [problems]`
  and `validate_presets(doc, parts) -> [problems]`.** The wrapped shape:
  `{params, label?, description?}`, name matching `CONFIG_RE`, `params` a
  map of JSON scalars, unknown keys refused. **`params_spec` may be `None`**
  (validate shape only) — the gate passes the real spec once `inspect` has
  run.
- [ ] **Task 5 — `validate_index(doc) -> [problems]`.** `index.json` per
  Decision 2, including `scope in {"public","private"}`, `path` relative and
  non-escaping, `content_id` well-formed, `gate.exempt_skips` a list,
  `signatures` present-and-empty (reserved).
- [ ] **Task 6 — the PRD-012 freeze, as a test not a comment.**
  `test_a_prd012_shaped_config_map_validates_through_the_same_function`
  builds `{"s": {"params": {...}, "label": "Small"}}` — the manifest shape
  PRD-012 will store — and asserts `validate_configuration` accepts it
  unchanged. A second test asserts the *flat* PRD-012 FR1 shape
  (`{"s": {"width": 10}}`) is **refused**, with the ambiguity named in the
  message (`a part may declare a parameter called 'label'`).

### Tests (`tests/test_packages_format.py`)
- [ ] content id: identical for a directory and a copy of it; different for
  one added byte, one added file, one renamed file; unaffected by mtime
  changes (`os.utime`) and by creating the files in a different order.
- [ ] ceilings refuse an oversized file, an oversized tree, too many files.
- [ ] path traversal (`../x`, an absolute path, a symlink out) refused.
- [ ] every manifest field: missing, wrong-typed, unknown-key.
- [ ] semver table, including yanked exclusion.
- [ ] the two PRD-012 freeze tests above.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_format.py` — cite count.
- [ ] `.venv/bin/python -c "import agentcad.core.packages.format, sys; assert 'OCP' not in sys.modules"`
- [ ] Full suite still green — cite count.
- [ ] Changelog `0166-package-format-and-content-id.md`.

---

## Slice 2 — cache, receipts, lockfile, and the manifest merge heads

**Why second:** installs and verification are the FR2/FR3/FR5 contract, and the
merge driver has to learn the two keys in the same commit that introduces them
or `apply_choices` is broken for them.

### Files
- `agentcad/core/packages/cache.py` (new)
- `agentcad/core/packages/lockfile.py` (new)
- `agentcad/core/manifest_merge.py` (**edited** — the only PRD-001 module this
  plan touches)
- `tests/test_packages_cache.py` (new)
- `tests/test_manifest_merge.py` (extended)

### Tasks

- [ ] **Task 1 — `cache.py` layout and install.** `root()` →
  `~/.agentcad/packages` (honouring `AGENTCAD_CONFIG`'s directory so tests
  never touch a real home — mirror `config.config_path()`'s override, and add
  `AGENTCAD_PACKAGES_DIR` for the same reason). `install(src, name, version,
  expected_content_id, *, index, source) -> Path`: verify the source tree's id
  against `expected_content_id` **before** copying (mismatch ⇒
  `ValidationError` naming both ids and the first differing path), copy into
  `<name>/.staging-<rand>/`, `os.replace` into `<name>/<version>/`, then write
  the receipt.
- [ ] **Task 2 — verification.** `verify(name, version) -> dict` returning
  `{"status": "ok"|"tampered"|"missing", "expected", "actual", "first_diff"}`
  — **never raises**; `require(name, version)` raises `ValidationError` on
  anything but `ok`, with the remove-and-re-add fix in the message. Callers
  that must not fail (`list_packages`) use `verify`; `use_part` uses
  `require`.
- [ ] **Task 3 — receipts.** `<name>/.receipts/<version>.json` =
  `{content_id, index, source, fetched_at, bytes, files}`. A sibling, never
  inside the version directory (a file inside changes the tree's own id — put
  that sentence in the module docstring).
- [ ] **Task 4 — `lockfile.py`.** `read(manifest)`, `add(manifest, name,
  version_req, index, resolved)`, `remove(manifest, name)`,
  `entry_for(manifest, name)`. Entries carry **only content-determined
  values** (Decision 6). `remove` drops both maps' entries and returns the
  materialised part ids that will now report `removed` provenance (found in
  slice 7 via `provenance.scan`; here the hook returns an empty list).
  Removing the last entry removes the key entirely, so a project that ends up
  with no packages is byte-identical to one that never had any (FR15).
- [ ] **Task 5 — `manifest_merge` heads.** In `_merge_section`, route
  `packages` and `packages_lock` through `_merge_entry_dict` when `_keyed(...)`
  — entries **atomic**, on the `materials.<id>` precedent (merging one side's
  version with the other's content id yields an entry nobody authored). In
  `_write_path`, extend the `head in ("assembly", "materials") and len(segs)
  == 2` branch to include both new heads; without it a resolution writes a
  bogus flat `"packages.iso4762"` key. Update the module docstring's key-space
  table.

### Tests
- [ ] install → verify `ok`; flip one byte in a cached script → `tampered`
  naming that file; delete the directory → `missing`.
- [ ] `require` raises on `tampered` and **does not re-fetch** (assert no
  index object is touched — pass a spy).
- [ ] a failed install leaves no partial directory (raise inside the copy).
- [ ] lock entries: byte-identical for the same package/version/index from two
  independent installs; removing the last package removes the key.
- [ ] `manifest_merge`: two branches adding *different* packages merge clean;
  the same package at different versions conflicts at
  `packages_lock.iso4762`; `apply_choices({"packages.iso4762": {"take":
  "theirs"}})` writes into the map and not into a flat key; `take` on a side
  that lacks the entry removes it.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_cache.py tests/test_manifest_merge.py`
- [ ] Full suite green — cite count.
- [ ] Changelog `0167-package-cache-and-lockfile.md`.

---

## Slice 3 — the local index client, resolution, and the offline path

**Why third:** it closes the install loop end to end without a server, a kernel
or a network, and it is the client shape slices 8 and 9 extend.

### Files
- `agentcad/core/packages/indexes.py` (new — protocol + `LocalIndex`)
- `agentcad/core/packages/manager.py` (new — `PackageManager`, install half)
- `tests/test_packages_index.py` (new)

### Tasks

- [ ] **Task 1 — the protocol and `LocalIndex`.** `name`, `kind`, `scope`,
  `refresh()` (no-op), `entries()` (parsed + validated `index.json`, cached on
  mtime), `fetch(name, version) -> Path` (the index-relative directory, with a
  containment check so a crafted `path` cannot escape the index root).
- [ ] **Task 2 — configuration.** `load_indexes(config) -> [Index]` from
  `~/.agentcad/config.json`'s `indexes` list, in precedence order; an
  unreadable or invalid index is **skipped with a warning**, never fatal — one
  broken index must not make the others unreachable.
- [ ] **Task 3 — `PackageManager.resolve(name, version_req, index=None)`.**
  Walk indexes in order; first that answers wins; `index=` pins one.
  Returns `{index, entry, version}` or raises `not_found_error` naming the
  package, the requirement and **every index tried with why each failed**.
- [ ] **Task 4 — `PackageManager.add(project, name, version_req, index)`.**
  Resolve → `cache.install` → `lockfile.add` → `store.save_manifest` →
  `bus.publish project_changed`. Returns post-state.
- [ ] **Task 5 — the offline path.** When no index answers (unreachable,
  missing, or none configured), resolve from the **cache**: the highest cached
  version satisfying the requirement whose tree verifies. Reconstruct the lock
  entry from the receipt and assert in a test that it is **byte-identical** to
  the online one. Neither index nor cache ⇒ `not_found_error`.

### Tests
- [ ] resolve across two indexes with precedence; `index=` pinning; a name in
  neither.
- [ ] add → manifest has both maps, cache has the tree, `project_changed`
  fired once.
- [ ] offline: point the index at a deleted directory, `add` the already-cached
  package, assert the lock entry equals the online one byte for byte.
- [ ] a malformed `index.json` in one of two indexes: the other still answers,
  and the warning names the broken one.
- [ ] `fetch` refuses an entry whose `path` escapes the index root.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_index.py`
- [ ] Full suite green — cite count.
- [ ] Changelog `0168-local-package-index.md`.

---

## Slice 4 — the gate, part A: the cell, the ephemeral service, the data stages

**Why fourth:** containment is the part that can damage a user, so it lands
before anything expensive runs inside it. The three data stages prove the
scaffold end to end without a single build.

### Files
- `agentcad/core/packages/gate.py` (new)
- `tests/test_packages_gate.py` (new, `slow`)

### The API

```python
GATE_STAGES = ("format", "contract", "presets", "build", "specs",
               "connectors", "previews", "docs", "policy")

# Closed set. Each member is a fact about the WORLD, never about the
# package's correctness — see the design spec, Decision 10. `publish` runs
# every stage, so `not_selected` can never reach a verdict.
PUBLISH_SKIP_EXEMPT = ("fem_extra_missing", "no_policy_configured",
                       "string_param_unbounded", "no_connectors_declared",
                       "reference_part")

class PackageGate:
    def __init__(self, service): ...          # NO service.specs here
    def run(self, path: str | Path, *, stages=GATE_STAGES, strict=False,
            jobs: int | None = None, work_dir: str | None = None,
            budget_s: float | None = None) -> dict: ...
```

Slice 4 implements `format`, `contract`, `presets`; the other six are
`skip / not_implemented` rows with a test asserting exactly that, so the seam
is real and not aspirational.

### Tasks

- [ ] **Task 1 — the cell.** `_work_dir` + `_refuse_overlap` copied in spirit
  from `checks.py` with **one extra path**: the package source directory. A
  run materialises into `<work-dir>/agentcad-package-<pid>-<rand>/` via
  `mkdtemp` and deletes only that; a caller's `--work-dir` is left exactly as
  it was.
- [ ] **Task 2 — the ephemeral service.** `_ephemeral_service(cell, kernel)`:
  `AgentCADService(cell, kernel, EventBus())` → `bus.on_publish = None` →
  `create_project("pkg_gate")` → `build_registry` → `store.branch_resolver =
  None` → `store.write_guard = None`. Docstring names each seam and the
  failure it prevents, **and states that unlike PRD-004's runner this one
  writes, so the guard is live and nulling it is load-bearing.** The kernel is
  shared, never restarted.
- [ ] **Task 3 — the report.** Import `make_item`, `make_stage`,
  `finalize_report`, `exit_code` from `core/checks.py`; the report carries
  `package: {name, version, content_id}` in place of `project`, and a
  top-level `note` holding the security non-claim verbatim.
- [ ] **Task 4 — stage `format`.** `validate_package_manifest`, inventory,
  ceilings, README present and non-trivial, previews present, every declared
  part file present. One row per problem, each naming the file.
- [ ] **Task 5 — stage `contract`.** One `kernel.request("inspect",
  {"script": text})` per part. Rows: PARAMS present, `build` present, and the
  **package standard** — every `number`/`int` parameter declares `min`, `max`,
  `unit` and `description`. A missing bound is a `fail` naming the parameter
  and the fix; the row message says why ("the gate's extremes claim is
  vacuous without it").
- [ ] **Task 6 — stage `presets`.** Materialise one scratch part per package
  part, then for each configuration call `service.set_params(scratch, part,
  cfg.params)` — validation and the build that proves it are the same act. A
  rejected value is a `fail` carrying the service's own message.
- [ ] **Task 7 — verdict.** `report["publishable"]` is true iff no `fail`/
  `error` and every `skip`'s reason is in `PUBLISH_SKIP_EXEMPT` (those rows
  carry `strict_exempt: true`). `--strict` moves the derived verdict only,
  never a row — PRD-004's split.

### Tests
- [ ] **containment, four tests:** the user's projects dir is byte-identical
  after a run (hash the tree before and after); the cell is deleted; a
  `--work-dir` that is / holds / sits inside the projects root or the package
  dir is refused with both paths named; the ephemeral service ends with all
  three seams `None`.
- [ ] format rows for: missing `disclosure`, unknown key, oversized file,
  missing README, a `parts.<id>.file` that does not exist.
- [ ] contract rows for: no PARAMS, no `build`, a numeric param without `max`.
- [ ] presets rows for: unknown parameter, out-of-type value, a preset naming
  a part that does not exist.
- [ ] the six unimplemented stages are `skip / not_implemented`.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_gate.py`
- [ ] `.venv/bin/python -c "import agentcad.core.packages.gate, sys; assert 'OCP' not in sys.modules"`
- [ ] Full suite green — cite count.
- [ ] Changelog `0169-package-gate-scaffold.md`.

---

## Slice 5 — the gate, part B: extremes, fan-out, specs, connectors, previews

**Why fifth:** this is the feature — the sentence "it is in the registry" gets
its meaning here. **AC2 is won in this slice.**

### Files
- `agentcad/core/packages/gate.py` (grow)
- `tests/test_packages_gate.py` (grow)
- `tests/fixtures/packages/` (new — a good package, a break-at-extreme
  package, a broken-connector package)

### Tasks

- [ ] **Task 1 — the variant matrix.** `variants(part, params_spec, presets)`
  → `[(variant_id, params, label)]`: the default, then per parameter
  one-at-a-time (`min`/`max` · `True`/`False` · every enum choice · the
  default for a string, with a `skip / string_param_unbounded` row saying the
  space is not swept), then every preset. **No cross product** — the docstring
  carries Decision 9a's reason, and a test asserts the count is
  `1 + Σ|sweep| + |presets|` rather than `Π`.
- [ ] **Task 2 — one scratch part per variant.** Create them all up front
  (serial `add_part`, then `set_params`), so the build phase makes **no
  manifest writes** and concurrent `_rebuild` calls touch only distinct cache
  keys and distinct `_status` slots.
- [ ] **Task 3 — the fan-out.** `ThreadPoolExecutor(max_workers=jobs or
  min(pool_size, 4))` over `service._rebuild(scratch, variant_id)`.
  `KernelPool._pick` routes by `hash(affinity) % size` and
  `KernelClient.request` holds a per-worker lock, so this is safe — **and it
  is the first in-process fan-out in the codebase**, so measure it (Task 8)
  and keep `jobs=1` working.
- [ ] **Task 4 — stage `build`.** One row per variant: `pass` with volume,
  mass and validity; `fail` carrying `KernelError.to_payload()` **verbatim**
  (same traceback, line and Error-Doctor hint an agent already knows how to
  read). A variant that builds to zero volume or `is_valid: false` is a
  `fail`, not a pass with a note.
- [ ] **Task 5 — stage `specs`.** `service.specs.run(scratch)` (read
  `service.specs` inside the method), fold its rows in with their statuses
  intact. `fem_extra_missing` skips stay skips and are publish-exempt.
- [ ] **Task 6 — stage `connectors`.** Per part, one
  `kernel.request("connectors", …)` — **this feature is that handler's first
  server-side consumer**; nothing in `core/` or the frontend has ever called
  it. Then build **one** scratch assembly: an anchor instance of the part plus
  one mated instance per connector (the part itself when it declares a rigid
  connector, otherwise the bundled probe part), and a single
  `service.get_assembly(scratch)` resolves them all in one `resolve_mates`
  round trip. A connector that fails to resolve is a `fail` naming it and
  carrying the resolver's message. A part with no connectors is one
  `skip / no_connectors_declared` row — declaring none is legitimate.
- [ ] **Task 7 — stages `previews`, `docs`, `policy`.** `render_view` per part
  from the built mesh (server-side, no kernel) and the shipped PNG must exist
  and parse; **no pixel comparison** — renderer drift would redden correct
  content, and the row message says that is why. `docs`: README plus a summary
  and a module docstring per part. `policy`: call `service.package_policy`
  when the attribute exists, otherwise one `skip / no_policy_configured` row
  — the seam PRD-031 FR2(b) plugs into.
- [ ] **Task 8 — measure the fan-out.** On the `iso4762` fixture, record
  wall-clock at `jobs=1` and `jobs=pool_size` in the changelog. If the speedup
  is under 1.5× on a 3-worker pool, **delete the ThreadPoolExecutor and ship
  the gate serial** — a slower gate is not a wrong one, and an unexercised
  concurrency path is a liability.

### Tests
- [ ] **AC2a:** a fixture package whose `build` raises at `length=max` fails
  `validate`, `publishable` is false, and `details.checks` names
  `build:cap_screw@length=max` with the traceback.
- [ ] **AC2b:** a fixture whose `connectors` returns a bad axis fails the
  `connectors` stage naming the connector.
- [ ] the variant count is the OAT sum, not the product.
- [ ] a package with no numeric parameters still produces a default row.
- [ ] `jobs=1` and `jobs=4` produce **identical reports** modulo timings.
- [ ] a part with no connectors skips rather than fails.
- [ ] a `check_fem_static` spec without the `[fem]` extra is a skip that does
  **not** block publish, and is listed in `gate.exempt_skips`.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_gate.py` — cite count.
- [ ] Cite the fan-out measurement (both wall-clock numbers) in the changelog.
- [ ] Full suite green — cite count.
- [ ] Changelog `0170-package-gate-measurements.md`.

---

## Slice 6 — `agentcad package validate`

**Why sixth:** the gate becomes usable by a human and by CI before it becomes a
publisher.

### Files
- `agentcad/cli.py` (edited — the `package` subcommand)
- `tests/test_packages_cli.py` (new)

### Tasks

- [ ] **Task 1 — `cmd_package_validate`,** shaped exactly like `cmd_check`:
  setup inside the try (an unwritable work dir is exit 2, not a traceback),
  `_build_service(..., extra_writable=[work_dir])` when `--work-dir` is
  passed, `locks.set_client_id("ci")`, kernel stopped in a `finally`.
- [ ] **Task 2 — flags.** `--strict`, `--report PATH`, `--jobs N`,
  `--work-dir DIR`, `--budget SECONDS` (reusing `_finite_arg`).
- [ ] **Task 3 — output.** A human summary per stage plus the failing rows,
  and `--report` writes the JSON. The security non-claim prints once, at the
  end, above the verdict.
- [ ] **Task 4 — exit codes.** `0` green · `1` red (the package is wrong) ·
  `2` harness. Add `package` to the subparser `metavar`.

### Tests
- [ ] the good fixture exits 0; each broken fixture exits 1 with the failing
  item named on stderr; a nonexistent directory exits 2.
- [ ] `--report` writes a document that `validate_report` accepts.
- [ ] `--work-dir` inside the projects root exits 2 naming both paths.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_cli.py`
- [ ] `.venv/bin/agentcad package validate tests/fixtures/packages/iso4762_good` — cite output and `echo $?`.
- [ ] Full suite green. Changelog `0171-package-validate-cli.md`.

---

## Slice 7 — the tool pack: the consumer surface and provenance

**Why seventh:** with format, cache, index and gate in place this is the slice
that makes the feature exist for agents. **AC1 and AC6 are won here.**

### Files
- `agentcad/core/packages/provenance.py` (new)
- `agentcad/core/tools_packages.py` (new)
- `agentcad/core/packages/search.py` (new)
- `tests/test_packages_tools.py` (new)

### Tasks

- [ ] **Task 1 — `provenance.py`.** `header(entry) -> str` emitting the
  `# agentcad:package 1 {...}` block **with no timestamp, no client id and no
  absolute path** (a test pins byte-identical output across two calls);
  `parse(script)` reading `tokenize` COMMENT tokens (the
  `core/script_blocks.py` precedent, so a docstring quoting the marker is not
  a header); `status(header, manifest, cache) -> "ok"|"modified"|
  "version_drift"|"removed"|"unverified"` — **computed on every read, zero
  kernel calls** (PRD-008's rule), and `scan(store, proj)` listing every
  materialised part with its status.
- [ ] **Task 2 — the pack, with the docstring that matters.** Module docstring
  states: the alphabetical load order, `tools_proposals.py:51`'s
  unconditional `gate_providers = []`, that `pac` sorts before `pro`, that
  **this pack registers no gate provider ever**, and that the escape hatch is
  `tools_publish.py` (`pub` > `pro`) or a lazy install from
  `routes_packages.py`. Also: `service.specs` / `service.branches` /
  `service.proposals` do not exist at `pac` and are read inside methods.
- [ ] **Task 3 — `service.packages = PackageManager(service)`** plus the six
  tools: `search_packages`, `add_package`, `remove_package`, `list_packages`,
  `use_part` and `validate_package` (which delegates to slice 4–5's gate with
  no side effects outside its own cell — it is the PRD's flagship authoring
  loop, an agent reading `details.checks`, fixing, re-validating). Every
  description carries the security non-claim in the sentence Decision 11
  fixes.
- [ ] **Task 4 — `use_part`.** `cache.require(...)` (verify **every** time) →
  header + script → `service.create_part` → `service.set_params` with the
  preset's params → return the ordinary `get_part` payload plus
  `package_provenance`. Idempotent re-materialisation is byte-identical (AC3);
  an existing `part_id` is a `conflict_error`. A package in `packages` with no
  `packages_lock` entry is **refused**, fail-closed, telling the caller to run
  `add_package`.
- [ ] **Task 5 — `remove_package`.** Drops both entries and returns the part
  ids whose provenance now reads `removed`. **It does not touch one script
  byte** — the header is inside the script and the script text is the cache
  key, so rewriting headers would re-key and rebuild every materialised part
  to express a removal. Put that sentence in the code.
- [ ] **Task 6 — `search.py`.** Structured filters and the deterministic
  ranking from Decision 8, each hit carrying `why`. `semantic` and
  `semantic_reason` are always present; MVP always answers
  `false / no_embedding_provider`.
- [ ] **Task 7 — the `get_part` / `get_project` wrappers,** idempotent by
  attribute marker, composing with `tools_specs`' later wrapper. `get_part`
  gains `package_provenance`; `get_project` gains a `packages` summary
  (`{name: {version, provenance_ok}}`) — the detail stays in `list_packages`.
  Zero kernel calls on both paths; a test asserts the call count is unchanged
  for a project with no packages (FR15).

### Tests
- [ ] **AC1 (kernel-side half):** `add_package` + `use_part` with the
  `m5x16` preset into a **copy** of `examples/prototyping`, then `set_mate`
  onto a tapped hole; assert the resolved transform and a clean interference
  check.
- [ ] **AC3:** materialise twice from cache with the index deleted → identical
  bytes; tamper with the cached script → `use_part` refuses.
- [ ] **AC6:** `get_part` names `iso4762@1.0.0`; after `remove_package` the
  part still builds and its provenance reads `removed`; after a local edit it
  reads `modified`.
- [ ] load order: no provider named `packages` in `service.gate_providers`;
  constructing the manager captures no `specs`/`branches` attribute.
- [ ] a project with no packages: `get_project` payload keys and kernel-call
  count byte-identical to the pre-feature ones.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_tools.py`
- [ ] Full suite green — cite count.
- [ ] Changelog `0172-package-tools.md`.

---

## Slice 8 — publishing: `agentcad publish`, immutability, yank, vendor scope

### Files
- `agentcad/core/packages/indexes.py` (grow — `LocalIndex.publish`)
- `agentcad/cli.py` (edited — the `publish` subcommand)
- `tests/test_packages_publish.py` (new)

### Tasks

- [ ] **Task 1 — `publish(index, dir)`.** Run the gate; refuse unless
  `publishable`; compute the content id; copy the tree to
  `<index>/<name>/<version>/`; write the index entry with the `parts` digest
  **derived from the gate's own measurements**, the preset list, the preview
  paths and `gate: {status, exempt_skips, agentcad, report_id}`; rewrite
  `index.json` atomically.
- [ ] **Task 2 — immutability (FR10, AC5).** An existing `name@version` is a
  `conflict_error` naming it, **even when the content id is identical** —
  a byte comparison would let a publisher redefine "identical" later.
- [ ] **Task 3 — yank.** `--yank <name>@<version>` flips `yanked: true` and
  deletes nothing. `resolve` skips yanked versions for a fresh requirement,
  a lock entry naming one keeps resolving, and an explicitly-named yanked
  version warns and proceeds.
- [ ] **Task 4 — the vendor gate.** Publishing a package whose
  `provenance.vendor.redistributable` is `false` to an index with
  `scope: "public"` is a `validation_error` naming the vendor and the index.
  This is the mechanism behind FR13's confinement — a flag the publisher
  checks, not a label nobody enforces.
- [ ] **Task 5 — the CLI.** `agentcad publish <dir> --index <name>
  [--yank] [--jobs N] [--work-dir DIR]`, `cmd_check`'s shape and exit codes;
  `publish` added to the subparser `metavar`.

### Tests
- [ ] publish → the index answers `search` and `add_package` installs it.
- [ ] **AC5:** republish is a `conflict_error`; a yanked version still
  resolves from an existing lock and is skipped by a fresh `^1.0.0`.
- [ ] a red gate blocks publish and writes nothing into the index (assert the
  index tree hash is unchanged).
- [ ] the vendor refusal.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_publish.py`
- [ ] Full suite green. Changelog `0173-package-publish.md`.

---

## Slice 9 — the git index: a repo is an index

**Why here:** this is the headline capability of the roadmap's step 1, and it
is small precisely because slice 3 made a local index the general case.
**AC4 is won here.**

### Files
- `agentcad/core/packages/_git.py` (new)
- `agentcad/core/packages/indexes.py` (grow — `GitIndex`)
- `tests/test_packages_git_index.py` (new, `portability`)

### Tasks

- [ ] **Task 1 — `_git.py`, and the docstring that defends it.** It states, in
  full, **why this is not `history._run`**: `_run` hard-codes
  `--git-dir <project>/.history --work-tree <project>`, a 10 s timeout, and
  `HOME`/`XDG_CONFIG_HOME` redirected into `.history`; an index fetch has no
  work tree, routinely exceeds 10 s, and needs the user's credential helper
  for a private repo. Rules: fixed argv never a shell; 120 s timeout;
  `GIT_TERMINAL_PROMPT=0`; `HOME` **not** redirected; URL validated
  (`https://`, `ssh://`, `git@host:path`, or an absolute path) and never
  starting with `-`.
- [ ] **Task 2 — `GitIndex`.** `~/.agentcad/indexes/<name>/`; `refresh()` is
  `clone --depth 1` on first use, then `fetch --depth 1` + `reset --hard
  <ref>`; everything after that **is** `LocalIndex` over the checkout.
  `git` absent (`service.history.available()` is false) ⇒ git indexes
  register nothing and `list_packages` says so — the versioning/proposals
  self-disable precedent.
- [ ] **Task 3 — failure is data.** An unreachable remote is a *warning* on
  `refresh`, not an exception: the last good checkout keeps answering, and
  `search`/`add` carry `stale: true` with the reason. A never-cloned index is
  a `not_found_error` naming the URL.

### Tests
- [ ] against a `file://` bare repo created in a tmp dir: add by URL, search,
  install.
- [ ] **AC4:** delete the remote entirely → `use_part` still works from cache
  and `add_package` of the already-cached package still succeeds offline with
  a byte-identical lock entry.
- [ ] refresh after a remote commit picks up a new version.
- [ ] a URL starting with `-`, and one with a shell metacharacter, are
  refused before any subprocess runs.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_git_index.py`
- [ ] Full suite green. Changelog `0174-git-package-index.md`.

---

## Slice 10 — seed catalog I: `iso4762`, bundled and dogfooded

**Why here:** the registry is now real, so it gets real content — and the
bundled catalog is simultaneously the default local index and the fixture the
git client is tested against.

### Files
- `catalog/index.json` (new)
- `catalog/iso4762/1.0.0/**` (new — `package.json`, `parts/cap_screw.py`,
  `presets.json`, `docs/README.md`, `previews/*.png`)
- `agentcad/cli.py` (`_register_catalog`, beside `_register_examples`)
- `tests/test_catalog.py` (new, `slow`)

### Tasks

- [ ] **Task 1 — the package.** `parts/cap_screw.py` over
  `toolkit.threads.cap_screw`, growing what
  `examples/fasteners/parts/cap_screw.py` never had: `size` as an **enum**
  over the bd_warehouse catalogue sizes, `length` a bounded number with unit
  and description, `connectors(p, part)` returning `axis` (cylindrical) and
  `head_seat` (rigid), and `SPECS` asserting head and shank diameters against
  the standard.
- [ ] **Task 2 — presets.** One configuration per catalogue size/length pair
  the roadmap's AC1 needs (`m5x16` at minimum), each with a `label`.
- [ ] **Task 3 — docs and previews.** `docs/README.md` covering the
  cosmetic-vs-real thread choice the threads toolkit documents, and previews
  generated by `render_view` (committed PNGs, small).
- [ ] **Task 4 — publish it.** Run `agentcad publish catalog/iso4762/…
  --index agentcad-core` and commit the resulting `index.json` — the index is
  a build product of the gate, never hand-edited. Say so in `docs/packages.md`.
- [ ] **Task 5 — auto-registration.** `_register_catalog(service)` beside
  `_register_examples`, resolving `resource_root() / "catalog"`, so a fresh
  install searches and installs with no network and no config. A missing
  catalog is a warning, never a startup failure.
- [ ] **Task 6 — packaging.** Add `catalog/` to the PyInstaller data files
  beside `examples/`.

### Tests (`tests/test_catalog.py`)
- [ ] every catalog package passes `PackageGate` green with no non-exempt
  skips — **this is what stops the seed catalog from rotting**.
- [ ] `index.json` validates and every entry's `content_id` matches the tree
  on disk (so a hand-edit is caught).
- [ ] the catalog is registered on a fresh service and `search_packages
  {"query": "cap screw"}` returns `iso4762` with `why` naming the match.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_catalog.py`
- [ ] `.venv/bin/agentcad package validate catalog/iso4762/1.0.0` — cite the verdict.
- [ ] Full suite green. Changelog `0175-seed-catalog-iso4762.md`.

---

## Slice 11 — the route pack and the Library dialog

**Why after the catalog:** the UI has something to show, and AC7 can be
verified in a real browser against real content.

### Files
- `agentcad/server/routes_packages.py` (new)
- `frontend/js/library.js` (new), `frontend/index.html`, `frontend/css/` (edited)
- `tests/test_packages_api.py` (new)

### Tasks

- [ ] **Task 1 — the routes** from the design spec's Surfaces section.
  Whitelisted body keys, never `**body`; `_RAISE` mapping to 404/422/409;
  a module docstring listing which error types are legitimate 200 bodies
  (**none** — every package failure is a refusal, unlike a check report).
- [ ] **Task 2 — the dialog.** Search field, results list with preview PNG,
  summary, standards, licence, **disclosure badge** and param table; a preset
  picker; "Add to project" calling `add_package` then `use_part`. Reuse the
  inspector's param-table rendering rather than a second one.
- [ ] **Task 3 — the non-claim in the UI.** The install affordance carries the
  one-line version ("package scripts run in your kernel with your privileges")
  linking to `docs/packages.md`. Not a tooltip — visible text.
- [ ] **Task 4 — no native dialogs.** PRD-026 has not landed; follow whatever
  the existing modals do (`placement.js`) and leave a note that PRD-026's
  dialog system adopts this.

### Tests
- [ ] route tests with `TestClient(base_url="http://127.0.0.1")` and
  `create_app(..., extra_allowed_hosts={"testserver"})`: search, install,
  use, remove, and the 404/409/422 shapes.
- [ ] **AC7, a real browser session** (the `run` skill): search, insert a
  preset fastener, see it in the tree and the viewport — screenshot, **zero
  console errors**.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_api.py`
- [ ] Browser session screenshot + console log cited.
- [ ] Full suite green. Changelog `0176-package-routes-and-library-ui.md`.

---

## Slice 12 — seed catalog II: the COTS starter set (FR12)

### Files
- `catalog/iso4014/`, `catalog/iso7380/`, `catalog/thread_insert/`,
  `catalog/din625/`, `catalog/extrusion_2020/`, `catalog/extrusion_3030/`,
  `catalog/nema17/`, `catalog/nema23/` (new)
- `catalog/index.json` (regenerated by `publish`)

### Tasks

- [ ] **Task 1 — fasteners:** `iso4014` hex bolts and `iso7380` button heads
  over `toolkit.threads.hex_bolt` / the fastener classes; `thread_insert`.
- [ ] **Task 2 — bearings:** `din625` (608-class) with `bore`/`outer`
  cylindrical connectors and SPECS on bore, OD and width.
- [ ] **Task 3 — extrusions:** `extrusion_2020` / `extrusion_3030` with a
  bounded `length`, slot geometry, and rigid connectors on each face's slot
  centreline.
- [ ] **Task 4 — motors:** `nema17` / `nema23` outlines with a rigid
  `face_mount` connector and a cylindrical `shaft`, SPECS on bolt-circle and
  shaft diameters.
- [ ] **Task 5 — every one publishes through the gate.** The gate *is* the
  curation: a package that will not pass does not go in the catalog, and the
  failure is the work item.

### Tests
- [ ] `tests/test_catalog.py` covers the new packages automatically
  (parametrised over the catalog); mark the whole module `slow`, and
  `exhaustive` if the wall clock exceeds two minutes.
- [ ] one interoperability test: a `din625` bearing mated onto an
  `extrusion_2020` bracket resolves and is interference-clean.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_catalog.py` — cite count and duration.
- [ ] Full suite green. Changelog `0177-cots-starter-set.md`.

---

## Slice 13 — `package_from_step`: the McMaster path (FR13)

### Files
- `agentcad/core/packages/from_step.py` (new)
- `agentcad/core/tools_packages.py` (grow — the seventh tool)
- `tests/test_packages_from_step.py` (new)

### Tasks

- [ ] **Task 1 — the flow.** Over the `import_cad_file` precedent: validate
  and copy the STEP with `imports.safe_import_name` / `ingest_file`'s rules,
  scaffold a package directory with the file under `imports/`, a
  reference-part entry, a `package.json` carrying
  `provenance.vendor = {name, part_number, url, terms, redistributable:
  false}`, and a `docs/README.md` stub naming the vendor.
- [ ] **Task 2 — connector assist, honestly scoped.** The tool returns
  `face_info` candidates (planar faces by area, cylindrical faces by axis) as
  **suggestions**; the author writes `connectors`. **Automatic connector
  inference is not implemented** and the tool description says so — see the
  design spec's divergence 7.
- [ ] **Task 3 — private-scope enforcement,** already built in slice 8: a
  test asserts publishing a vendor package to a `public` index is refused.
- [ ] **Task 4 — the licensing statement.** The tool description, the
  generated README and `docs/packages.md` all say that vendor-derived geometry
  stays in personal/org indexes and that legal review precedes any public
  seeding.

### Tests
- [ ] a bundled STEP scaffolds a package that passes the gate as a
  reference-part package (no PARAMS sweep — a reference part has none; the
  `contract` stage skips with a reason).
- [ ] the public-index refusal.
- [ ] mesh-kind (STL) input is refused with the booleans caveat named.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_packages_from_step.py`
- [ ] Full suite green. Changelog `0178-package-from-step.md`.

---

## Slice 14 — docs, acceptance tests, PRD close-out

### Files
- `docs/packages.md` (new), `docs/agent-api.md`, `docs/user-guide.md`,
  `docs/architecture.md`, `AGENTS.md`, `CLAUDE.md`, `README.md` (edited)
- `docs/prd/in-progress/PRD-011-…` → `docs/prd/completed/`, `docs/roadmap.md`
- `docs/prd/pending/PRD-012-configurations.md` (edited — FR1 amendment)
- `tests/test_prd011_acceptance.py` (new)

### Tasks

- [ ] **Task 1 — `docs/packages.md`:** the published format reference
  (`package.json`, `index.json`, `presets.json`, the content id, the
  lockfile), the index kinds, the publish flow, and **the trust model on the
  first screen** — the gate is a correctness gate, not a security boundary;
  package scripts run in your kernel with your privileges; PRD-006 is the
  deferred backstop.
- [ ] **Task 2 — `AGENTS.md` gotchas section**, in the house's voice, every
  item traceable to a measurement or a file: the pack-name/`gate_providers`
  trap and the no-provider decision · the header is immutable and its status
  is computed on every read, and `remove_package` touches no script byte
  because the script text is the cache key · no timestamps in the header or
  the lock · the content id is a tree digest and tar is not byte-stable · the
  gate's claim is per-parameter ranges plus declared presets, **never** the
  cross product · `_git.py` is not `history._run` and why · the gate is the
  first ephemeral-service consumer whose write guard is genuinely live · the
  `connectors` handler's first server-side consumer · `use_part` never
  touches the network.
- [ ] **Task 3 — PRD-012 FR1 amendment.** Edit
  `docs/prd/pending/PRD-012-configurations.md` to the wrapped configuration
  entry, with a one-line note pointing at this design spec's Decision 4 and
  the ambiguity that forced it. Do this in **this** commit, not "later" — the
  schema is frozen the moment slice 10 publishes a package.
- [ ] **Task 4 — `docs/agent-api.md`** for the six tools and the two CLI
  commands; **`docs/user-guide.md`** for the Library dialog;
  **`docs/architecture.md`** for the subpackage and the cache/index paths;
  `README` one paragraph.
- [ ] **Task 5 — `tests/test_prd011_acceptance.py`,** one test per AC1–AC8,
  each naming its criterion in the docstring, examples on a **copy**.
- [ ] **Task 6 — close-out.** Move the PRD to `completed/`, update
  `docs/roadmap.md`'s index row and the sequencing table (step 1 done, step 2
  next), and record the verified ACs.
- [ ] **Task 7 — the open blocker, stated not solved.** `docs/packages.md`
  and the close-out note record that **this repository still has no LICENSE
  file and no `license` field**, that the seed catalog's packages carry a
  licence the repository itself does not yet declare, and that it is a
  founder decision blocking 031a.

### Verification
- [ ] `.venv/bin/python -m pytest -q tests/test_prd011_acceptance.py` — cite count.
- [ ] `make test` — cite the full count and any skips.
- [ ] Browser session for AC7 re-verified after any UI change.
- [ ] Changelog `0179-prd-011-docs-and-acceptance.md`.

---

## Rollback / landing notes

- **Slices 1–3 are inert**: nothing imports them until slice 7 registers the
  pack, so they can land and sit.
- **The one-line revert** for the whole consumer surface is deleting
  `core/tools_packages.py` — no seam in the service survives it, because the
  two wrappers are installed from `register()` and are idempotent.
- **The one-line revert for merge behaviour** is removing the two heads from
  `manifest_merge`; existing manifests without the keys are unaffected either
  way.
- **The catalog is data.** Deleting `catalog/` degrades the product to "no
  packages configured" and breaks no code path — `_register_catalog` warns and
  continues, exactly as `_register_examples` does.
- **If the fan-out does not pay** (slice 5, Task 8), delete it. Serial is the
  fallback and the report is identical.
