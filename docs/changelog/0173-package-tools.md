# 0173 — 2026-08-16 — PRD-011 slice 7: the tool pack, provenance-on-read, structured search

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The feature becomes reachable by an agent. `agentcad/core/tools_packages.py`
registers six tools and one seam (`service.packages`), `provenance.py` emits
the immutable, timestamp-free header that `use_part` copies into the project
and recomputes its status on **every** read, and `search.py` is the
deterministic, explainable, honestly-degraded search. **AC1 and AC6 are won
here**, and AC3's two halves (byte-identical re-materialisation, refusal on a
tampered cache) are tested from the consumer's side. The pack registers **no
gate provider**, deliberately, with the load-order trap stated in the module
docstring and pinned by a test.

## Changes

- `agentcad/core/packages/provenance.py` (new)
  - `header(entry)` — one JSON line under `# agentcad:package 1 …` plus the
    security non-claim (Decision 11, place 7), ending in a blank line. Only
    `HEADER_FIELDS` survive, keys are sorted, and **no timestamp, client id or
    absolute path** appears; a test emits the same entry twice with the keys
    in opposite orders and compares bytes.
  - `parse(script)` — the first `agentcad:package` **COMMENT** token
    (`tokenize`, the `core/sketch_emit._comment_lines` precedent), so a
    docstring quoting the marker is not a header. A script that will not
    tokenize falls back to a line scan.
  - `strip(script)` / `script_sha256(text)` — the package's own bytes without
    the block, hashed as `sha256:<hex>` (the same spelling as a content id).
  - `status(head, manifest, script, verify=…)` → `removed` → `version_drift`
    → `modified` → `unverified` → `ok`, **computed on every read, never
    stored**, with zero kernel calls. A header whose `format` is not this
    build's is `unverified` — a newer agentcad wrote it, and guessing that the
    fields still mean what they mean today is how a format bump becomes a
    silent false `ok`.
  - `describe(...)` — the flat `package_provenance` block; `scan(store, proj)`
    — every materialised part with its status; `memoized_verify()` — one cache
    hash per package per call.
- `agentcad/core/packages/search.py` (new) — `search(indexes, query=…,
  index=…, keywords=…, standards=…, param=…, limit=…)` →
  `{hits, semantic, semantic_reason, indexes, warnings}`. Decision 8's score
  bands (100/80/70/60/40/30), ties broken on name ascending then version
  descending, `why` on every hit, structural `param` **range-overlap**
  filtering, `semantic: false` / `no_embedding_provider` always present, a
  yanked-only package omitted, and a broken index reported as a warning rather
  than raised.
- `agentcad/core/tools_packages.py` (new) — the pack.
  - `service.packages = PackageManager(service)`; `install_provenance(service)`
    wraps `get_part` (adds `package_provenance`) and `get_project` (adds a
    `packages` summary `{name: {version, provenance_ok}}`), idempotent by
    attribute marker, composing with `tools_specs`' later wrapper.
  - `materialize(...)` — the `use_part` core: lock → `cache.require` (verify
    the whole tree, every time) → `package.json` → the part file →
    `presets.json` → header → `create_part` → `set_params`. Never touches an
    index or the network. A `set_params` that the part refuses **deletes the
    part it just made** — source nobody typed must not be left behind
    (`script_blocks.apply_generated_block`'s rule).
  - `list_installed(...)` — per package `{version, version_req, index,
    content_id, source, cache, cache_reason, latest, stale, parts}` plus the
    configured indexes and the loader's warnings.
  - Six tools: `search_packages`, `add_package`, `remove_package`,
    `list_packages`, `use_part`, `validate_package`.
- `tests/test_packages_tools.py` (new) — 51 tests.
- `tests/test_packages_ocp_free.py` — probes for `provenance` and `search`,
  plus `EXTRA_OCP_FREE` for `core/tools_packages.py` (which is outside the
  subpackage, so it must not join the set the tree-match test compares); the
  static source scan covers the pack too.

## Files

- `agentcad/core/packages/provenance.py` — new
- `agentcad/core/packages/search.py` — new
- `agentcad/core/tools_packages.py` — new
- `tests/test_packages_tools.py` — new
- `tests/test_packages_ocp_free.py` — three probes, one new constant

## Divergences from the plan, and why

- **The header's JSON is one line, not the spec's wrapped block.** The design
  spec renders the payload across three `#` lines for legibility *in prose*; a
  wrapped payload in the file would need a continuation grammar in `parse` and
  buys nothing, because a comment line has no length limit. The security
  non-claim is still three wrapped lines, because it is prose.
- **`parse` distinguishes "no header" from "an unreadable header".** A marker
  comment whose payload will not parse answers a dict carrying `malformed`
  and `None` for every field, rather than `None`. "There is a provenance claim
  here and we cannot read it" is a different fact from "there is none", and
  collapsing them would hide exactly the tampering this module exists to
  surface.
- **`status`'s third argument is `verify`, not `cache`.** The plan writes
  `status(header, manifest, cache)`; the implementation takes the script (it
  cannot answer `modified` without the bytes) and an injectable `verify` seam
  defaulted to `cache.verify`, which is what lets `scan` and `get_project`
  hash each package once instead of once per part.
- **`unverified` covers a cache entry that does not verify, not only a
  missing one.** The spec's table says "no cache entry to compare against";
  a *tampered* cache entry is the same epistemic state for the project part —
  we cannot compare — and calling it `ok` would be a claim nobody measured.
  The consequence is stated because it is user-visible: **on a fresh clone
  with a cold cache, provenance reads `unverified`, not `ok`.**
- **`scan` rows name the PROJECT part in `part` and the package's own part in
  `package_part`.** The two collided in the first draft (the header's `part`
  overwrote the row's subject) and the test caught it; separating them is the
  fix rather than picking a winner.
- **`list_packages` does not `refresh()` an index.** It is the call a project
  header makes on every open, and a network fetch per keystroke is not a
  listing. `latest` therefore reports what the last refresh knows, and
  `search_packages` is the surface that refreshes. Stated in the tool
  description so a caller is not surprised.
- **Search returns one hit per package per index (the highest non-yanked
  version), not one per version.** A package with five versions would
  otherwise flood a result set. The tie-break rule the spec gives (name, then
  version descending) still earns its place: two *different* indexes can carry
  the same name, and that is what orders them.
- **`add_package`'s result grows a `warnings` key** (empty in this slice) so
  slice 8's "an explicitly-named yanked version warns and proceeds" has a
  place to land that is not a new shape.
- **`use_part` builds twice** — once at the package's defaults and once at the
  preset's — because `create_part` returns `get_part`. Deliberate:
  `set_params` is what validates a preset's names and types against the
  script's own PARAMS spec before a byte reaches the manifest, and a package
  from an unvalidated local index is exactly the case that needs it. The gate
  avoided the same double build by writing through the store, which is right
  for a dozen variants and wrong for one materialisation of one part.
- **A header whose `format` is not 1 reads `unverified`.** Not asked for; the
  five-status table says nothing about drift, and a v1 reader answering `ok`
  about a v2 header would be a claim it did not earn.

## Notes

- **The load-order trap is stated where it bites.** `tools._load_tool_packs`
  walks alphabetically and `tools_proposals.py:51` assigns
  `service.gate_providers = []` **unconditionally**; `pac` sorts before `pro`,
  so a provider appended by this pack would be silently discarded. The module
  docstring says so in full, names the escape hatch (`tools_publish.py`, since
  `pub` > `pro`, or a lazy install from `routes_packages.py`), and
  `test_the_pack_registers_no_gate_provider` pins it.
- **Nothing is captured at registration.** `service.specs` (`s`),
  `service.branches` (`v`), `service.proposals` and `service.gate_providers`
  (`p`) do not exist at `pac`; `test_the_manager_captures_nothing_at_
  registration` constructs the manager against a service whose `__getattr__`
  raises.
- **FR15 is structural, not careful.** `get_project` reads
  `packages_lock`; the key is absent for a project that uses no packages, so
  nothing is read, tokenized or hashed. The test asserts the payload key, the
  absent provenance, a byte-identical manifest **and** that no extra kernel
  call is made.
- **`remove_package` does not touch one script byte**, and the test compares
  the script text before and after. The header lives inside the script and the
  script text is the rebuild cache key, so rewriting headers to express a
  removal would re-key and rebuild every materialised part.
- **What the tests attack:** a docstring quoting the marker, a marker with
  unreadable JSON, the index deleted before `use_part`, the cached script
  edited after installation, a manifest with `packages` and no
  `packages_lock`, a `part_id` that already exists, `removed` racing
  `modified` (removed wins), the cache root deleted under a materialised part,
  and a broken index sitting in front of a good one during a search.
- AC1 is the walk in one test: `add_package` → `use_part` with a preset →
  `set_mate` onto a tapped hole → the resolved transform (`[0, 0, 10]`) → a
  clean `check_interference_free`.

## Measurement — what provenance costs `get_project`

The claim that pays for scanning every part's script on every `get_project` is
that a package-free project pays **nothing** and a package-heavy one pays
milliseconds. Measured on the development machine (median of 7, a 50-part
project, one package):

| project | cost |
|---|---|
| 50 parts, **no packages** | **0 ms** — the `packages_lock` key is absent, so no script is read, tokenized or hashed |
| 50 parts, all materialised from one package | **9.3 ms** (scan + one whole-tree cache verification) |
| the same 50 scripts, read and marker-tested only | 6.3 ms |

So two thirds of the cost is reading fifty files and one third is the single
cache hash — which is why the verification is memoised per package rather than
per part, and why the substring fast-path in `parse` runs before `tokenize`.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_tools.py tests/test_packages_ocp_free.py
64 passed
```

The rest of the packages suite, unchanged by the new pack:

```
.venv/bin/python -m pytest -q tests/test_packages_gate.py tests/test_packages_cli.py \
    tests/test_packages_index.py tests/test_packages_cache.py
206 passed in 24.69s
```

The surfaces a new tool pack could have broken:

```
.venv/bin/python -m pytest -q tests/test_tools.py tests/test_service.py \
    tests/test_server.py tests/test_mcp.py tests/test_prd001_acceptance.py
41 passed in 72.49s
```

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
