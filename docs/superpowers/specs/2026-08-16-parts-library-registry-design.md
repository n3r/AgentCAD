# Parts library & package registry — design

- **PRD:** [PRD-011](../../prd/in-progress/PRD-011-parts-library-registry.md)
- **Date:** 2026-08-16
- **Roadmap position:** **step 1 of the marketplace chain**
  (roadmap, "Sequencing decision — the marketplace chain (16 Aug 2026)").
  Registry-first: the git-hosted index and the kernel-validated seed catalog
  are primary deliverables, not phase 2.
- **Depends on (all completed):** PRD-001 (`manifest_merge`, git history,
  branch resolver) · PRD-002 (the gate seam this feature deliberately does
  **not** use) · PRD-003 (`SpecRunner.run`, the four-status vocabulary) ·
  PRD-004 (the ephemeral-service pattern, the report shape, `items` rows)
- **Freezes for a pending PRD:** the configuration schema PRD-012 must adopt
  (see Decision 4).
- **Plan:** [2026-08-16-parts-library-registry.md](../plans/2026-08-16-parts-library-registry.md)

---

## Problem

Every project re-derives its fasteners. `examples/fasteners/parts/cap_screw.py`
is 27 lines wrapping `toolkit.threads.cap_screw` — and it exists once, inside
one example, reachable by nobody. There is no unit of reuse between projects
except copy-paste, and no way for anyone else's work to arrive.

The roadmap resequenced this feature on 16 Aug 2026 for a reason that changes
what the design has to optimise for. The compounding asset is **a catalog of
kernel-validated, mate-ready parametric parts plus a format others adopt**; the
storefront is the commodity half and carries all four of PRD-031's blockers
(cloud, identity, moderation, sandbox). PRD-011's git-hosted index needs none
of them — *a repo is an index*, code runs locally like pip — so the catalog can
start compounding today. Therefore:

1. **The format is the deliverable.** It will be published and depended upon;
   changing it later breaks every pinned consumer. Every field is either
   *present with a reason* or *reserved with a reason* (Decision 2).
2. **The gate is the curation.** "It is in the registry" has to mean something
   measured, or the catalog is GrabCAD with fewer files. The gate is
   orchestration over five surfaces that already exist and are reviewed
   (Decision 9) — this feature adds **no new measurement**, exactly as PRD-004
   added none.
3. **The gate is not a security boundary**, and the design says so in eight
   places rather than one (Decision 11).

The other half of the design work is containment. A gate run builds dozens of
variants and *writes* while it does — the first consumer of the ephemeral
service that genuinely needs `write_guard = None` rather than being inert by
accident (PRD-004 predicted this in `checks._ephemeral_service`'s docstring).

---

## Architecture at a glance

```
agent / CLI / browser
        │
        │  search_packages · add_package · use_part · list_packages · remove_package
        ▼
agentcad/core/tools_packages.py ──► service.packages  (PackageManager)
        │                                   │
        │                                   ├── indexes.py    local | git | (cloud, reserved)
        │                                   │       git index = local index + a fetch step
        │                                   │       ~/.agentcad/indexes/<name>/
        │                                   ├── cache.py      ~/.agentcad/packages/<name>/<version>/
        │                                   │                 + .receipts/<version>.json
        │                                   ├── lockfile.py   project.json: packages / packages_lock
        │                                   ├── format.py     package.json + index.json schemas
        │                                   ├── content.py    the content id (canonical tree digest)
        │                                   ├── provenance.py the immutable header, status on read
        │                                   └── search.py     structured always; semantic optional
        │
        │  agentcad package validate <dir>          agentcad publish <dir> --index <name>
        ▼
agentcad/core/packages/gate.py   PackageGate.run(dir) -> report (PRD-004 shape)
        │
        │  throwaway cell  <work-dir>/agentcad-package-<pid>-<rand>/
        │  ephemeral AgentCADService rooted there, SAME warm kernel,
        │  bus.on_publish = None · store.branch_resolver = None · store.write_guard = None
        │
        ├─ format     package.json, inventory, size ceilings      (pure data)
        ├─ contract   kernel `inspect` per part: PARAMS/build      (1 call/part)
        ├─ presets    every preset applied through set_params
        ├─ build      defaults + per-param extremes + presets,
        │             one scratch PART per variant, fanned out on
        │             the pool by the affinity _rebuild already passes
        ├─ specs      service.specs.run(scratch)      (PRD-003, all three tiers)
        ├─ connectors kernel `connectors` per part, then ONE assembly whose
        │             probe instances mate to every declared connector
        ├─ previews   render_view per part; shipped PNGs must exist and parse
        ├─ docs       README + per-part documentation
        └─ policy     service.package_policy if configured, else skip/no_policy
                      (PRD-031's static AST gate plugs in here)
```

Surfaces — every one an extension point. **No edit to `worker.py`,
`tools.py`, `app.py`, `service.py`, `proposals.py`, `packet.py`, `specs.py`,
`checks.py`, `branches.py`, `history.py` or `project.py`.**

| File | Role | New? |
|---|---|---|
| `agentcad/core/packages/` (9 modules + `_git.py`) | format, content id, cache, lockfile, indexes, search, provenance, gate, manager | new subpackage |
| `agentcad/core/tools_packages.py` | tool pack: the six tools; installs `service.packages`; wraps `get_part`/`get_project` | new |
| `agentcad/server/routes_packages.py` | route pack: search / install / use / publish | new |
| `agentcad/core/manifest_merge.py` | two new key-wise heads (Decision 13) | **edited** (PRD-001's driver, additively) |
| `agentcad/cli.py` | `agentcad package validate` and `agentcad publish` | edited (the `cmd_check` precedent) |
| `catalog/` (repo root) | the seeded index: `index.json` + package directories | new |
| `frontend/js/library.js` | the Library dialog | new |
| `docs/packages.md` | the published format reference and the trust model | new |

---

## Decision 1 — registry-first: a directory is a package, a repo is an index

A package is a **directory**, and v1 has no archive format at all.

```
iso4762/
  package.json          the manifest — name, version, license, disclosure, …
  parts/cap_screw.py    ordinary part scripts (PARAMS + build + connectors + SPECS)
  presets.json          named configurations (Decision 4)
  docs/README.md        required
  previews/cap_screw_iso.png
  imports/vendor.step   optional, reference-part packages only
```

An index is a **directory containing `index.json` and package directories**:

```
catalog/
  index.json
  iso4762/1.0.0/…          one directory per published version
  iso4014/1.0.0/…
```

Three consequences that are the whole point of the sequencing decision:

- A **git repo is an index** with no server, no auth and no packaging step:
  clone it and you have a local index. A git index is therefore *a local index
  plus a fetch step* (Decision 7), which is what makes three index kinds one
  client shape instead of three clients.
- A **directory is diffable and reviewable**. A tarball is not. The whole
  product thesis is that the model is code that people can read before they
  run it; shipping the catalog as opaque blobs would contradict it on the one
  surface where strangers' code arrives.
- **Publishing is a directory copy plus an index-entry write** — which is why
  the seed catalog can live in this repo, be dogfooded by CI, and be consumed
  by URL the day someone points at it.

No tarball also removes a class of bug the house has already paid for once:
tar is not byte-stable across producers (ordering, mtimes, uid, format), the
same property that forced PRD-004's determinism stage to compare SVG and not
DXF. See Decision 3.

---

## Decision 2 — the format, field by field, present or reserved

`package.json`, `"format": 1`. Unknown keys are **rejected**, not ignored: a
published format that silently swallows a typo teaches authors that the typo
worked.

```jsonc
{
  "format": 1,
  "name": "iso4762",                 // ^[a-z][a-z0-9_-]{0,39}$
  "version": "1.2.0",                // ^\d+\.\d+\.\d+$  — no prereleases in v1
  "summary": "ISO 4762 socket-head cap screws, M3–M12",
  "keywords": ["fastener", "screw", "socket head", "metric"],
  "standards": ["ISO 4762"],         // present: the highest-value search facet
                                     // a parts catalog has, and PRD-031 FR1
                                     // names it in listing metadata
  "license": "Apache-2.0",           // SPDX id, required, non-empty
  "authors": [                       // objects, never bare strings: a string
    {"name": "AgentCAD", "url": "…"} // cannot grow a verified identity later
  ],
  "disclosure": "agent",             // "human" | "agent" | "hybrid" — REQUIRED
  "min_agentcad": "0.1.0",
  "provenance": {                    // present, narrow (see below)
    "generator": {"name": "agentcad", "version": "0.1.0"},
    "vendor": null                   // {name, part_number, url, terms,
                                     //  redistributable: false}
  },
  "parts": {                         // declared, not discovered
    "cap_screw": {
      "file": "parts/cap_screw.py",
      "label": "Socket-head cap screw",
      "summary": "ISO 4762 cap screw with axis and head-seat connectors"
    }
  },
  "remix": null,                     // RESERVED — shape frozen, never written
  "requires": {"extras": []}         // RESERVED — {"extras": ["fem"]}
}
```

| Field | Present or reserved | Why, decided now because it cannot be decided later |
|---|---|---|
| `format` | present | A format without a version number is a format that can never change. |
| `name`, `version` | present | FR1. `\d+\.\d+\.\d+` only — prereleases need a precedence rule (`1.0.0-rc.1 < 1.0.0`) and a policy on whether a range may resolve to one; both are answerable later **additively** by widening the regex, and unanswerable-and-then-wrong if guessed now. |
| `license` | present | PRD-031 FR1 and FR4 (CC-ND constrains remix) both key off it, and back-filling a licence onto content someone already redistributed is not possible. **This repo has no LICENSE file and no `license` field in `pyproject.toml`** (verified); the roadmap flags it as blocking before 031a. It is also blocking for the *seed catalog*, whose packages need a real value in this field on the first commit. |
| `disclosure` | **present** | PRD-031 FR1/AC5 makes it a listing badge and a search filter. It is only knowable at authoring time. If it lands with 031, every package seeded before then has an unknowable value forever — and this catalog is deliberately agent-built, so the honest answer is `"agent"` from commit one. This is the single strongest argument in this table for shipping a 031 field early. |
| `authors` | present, as objects | `{"name", "email"?, "url"?}`. A bare string cannot grow a `verified: true` or a publisher id without a migration. |
| `standards` | present | Search facet (Decision 8) and 031 listing metadata. Free to fill for a standards catalog; impossible to back-fill accurately. |
| `min_agentcad` | present — but the **index** records the real compat key | A package's actual compatibility surface is the pinned **build123d** version, not the app version: AGENTS.md pins build123d and calls the test suite the compatibility harness, so a package proved under 0.11.1 can break under 0.12 with `min_agentcad` untouched. `min_agentcad` stays the declaration an author can make; `index.json`'s `gate.build123d` records what the package was actually *proved* against, because only the gate knows it. The PRD's rule stands unchanged: a package cannot demand a different build123d — the app's pinned stack is the harness. |
| `provenance.generator` | present | FR5 of 031 wants the generator version travelling with artifacts. One object, written by the publisher. |
| `provenance.vendor` | present, and **mechanical** | `{name, part_number, url, terms, redistributable}`. FR13's vendor-licensing confinement is enforced by this flag, not by a label: `publish` to an index whose `scope` is not `private` **refuses** a package with `redistributable: false`, naming the vendor. A label nobody enforces is a liability; a flag the publisher checks is a control. |
| `remix` | **reserved** | `{"of": {"name", "version", "content_id"}}`. Validated when present, **never written by this feature**. Forking needs identity (who forked) and licence enforcement (CC-ND blocks remix-publish, 031 FR4) — both are 031/005. But the slot has to exist, or a 031 fork is a format change that breaks every pinned consumer. |
| `requires` | reserved | `{"extras": ["fem"]}` for a package whose SPECS declare `check_fem_static`. Reserved rather than built because the gate's exempt-skip rule (Decision 10) already reports the fact honestly, and an unenforced requirement is worse than none. |
| signatures | **reserved in `index.json`, not here** | A signature over content that contains the signature is circular. 031 FR2(d) signs the content id; the slot is `index.json`'s version entry (`"signatures": []`). |
| `yanked` | **not in `package.json` at all** | FR10 makes published versions immutable. Yank state is index state, so it lives in the index entry. Putting it in the package would require rewriting an immutable artifact to yank it. |
| `previews`, `docs` | **not fields** | They are paths, discovered by inventory. A field that duplicates the filesystem drifts from it. |

**`index.json`**, `"format": 1`, is the other published document:

```jsonc
{
  "format": 1,
  "name": "agentcad-core",
  "scope": "public",                     // "public" | "private" — vendor gate
  "packages": {
    "iso4762": {
      "versions": {
        "1.2.0": {
          "content_id": "sha256:9f3c…",   // Decision 3
          "path": "iso4762/1.2.0",        // index-relative, never absolute
          "summary": "…", "keywords": [...], "standards": [...],
          "license": "Apache-2.0", "disclosure": "agent",
          "min_agentcad": "0.1.0",
          "parts": {                      // the search + agent-context digest
            "cap_screw": {
              "params": [{"name": "size", "type": "enum",
                          "choices": ["M3-0.5", "…"], "unit": null,
                          "description": "…"},
                         {"name": "length", "type": "number",
                          "min": 5.0, "max": 60.0, "unit": "mm",
                          "description": "…"}],
              "connectors": {"axis": "cylindrical", "head_seat": "rigid"},
              "specs": ["shank_dia", "head_dia"]
            }
          },
          "presets": ["m3x10", "m5x16", "…"],
          "previews": ["previews/cap_screw_iso.png"],
          "gate": {"status": "green", "exempt_skips": [],
                   "agentcad": "0.1.0", "build123d": "0.11.1",
                   "report_id": "sha256:…"},
          "yanked": false,
          "signatures": []                // RESERVED
        }
      }
    }
  },
  "embeddings": null                      // optional; see Decision 8
}
```

The `parts` digest is why an agent can pick a package without downloading it,
and why `search_packages` can filter on parameter names and ranges without a
kernel call. It is derived at publish from the gate's own measurements — never
hand-written — so it cannot disagree with the package.

`gate` records **what was measured**, including which skips were exempted. "It
is in the registry" then means a checkable sentence rather than a badge.

---

## Decision 3 — the content id is a canonical tree digest, not an archive hash

FR2 says "the sha256 of its canonical archive". There is no archive
(Decision 1), and hashing one would have been the wrong number anyway.

```
content_id = "sha256:" + sha256(
    "".join(f"{posix_relpath}\0{sha256_hex(file_bytes)}\n"
            for path in sorted(files_in_package))
)
```

- Files are sorted by POSIX-normalised relative path; the listing is bytes,
  `\n`-terminated, `\0`-separated. No mtimes, no modes, no uids, no ordering
  from a directory walk.
- The ignore list is fixed and published: `.git/`, `__pycache__/`, `*.pyc`,
  `.DS_Store`, `*.tmp`. Anything else counts, including files the format does
  not know about — a package that ships an extra file has a different id.
- The same number falls out of a directory, a git checkout of that directory,
  and a copy of it in the cache. That is the property that makes "verify from
  cache" meaningful, and no tar format has it.

Ceilings, enforced at publish and at install, and published as part of the
format: **50 MB per package, 5 MB per file, 500 files.** They exist for three
reasons and the third is the load-bearing one: they bound a registry's disk,
they bound a malicious index's ability to fill it — and they make it
affordable to re-verify the whole tree on **every** materialisation (Decision
6) instead of trusting a receipt.

---

## Decision 4 — presets **are** configurations, and this is the frozen schema

PRD-011's own risk section: presets must *be* PRD-012 configurations or the
product grows two variant systems. PRD-012 is not designed, so this design
freezes the schema it must adopt.

**The schema.** One entry shape, wherever a named variant lives:

```jsonc
// presets.json in a package (per part), and — when PRD-012 lands —
// project.json's parts.<id>.configs, entry for entry.
{
  "format": 1,
  "presets": {
    "cap_screw": {                        // part
      "m5x16": {                          // ^[a-z0-9][a-z0-9_-]{0,31}$
        "params": {"size": "M5-0.8", "length": 16.0},
        "label": "M5 × 16",               // optional
        "description": "…"                // optional
      }
    }
  }
}
```

**PRD-012 FR1 currently specifies `configs: {name → {param: value}}` — the
param map *is* the entry. That shape must change to the wrapped one above,
and this design records it as an amendment 012 has to accept.** Three
reasons, in order of force:

1. **The flat map is not extensible at all, and its failure mode is
   ambiguity, not inconvenience.** `{"S": {"width": 10, "label": "Small"}}`
   is unreadable the day a part declares a `label` parameter — and part
   scripts declare arbitrary parameter names, including `label`, `params`,
   `description`. There is no reserved-prefix trick that fixes this without
   being uglier than the wrapper. A published format cannot carry an
   ambiguity that a consumer's parameter name can trigger.
2. **Every consumer already needs the metadata.** PRD-012's own inspector
   switcher needs a display name, PRD-014's dimension tables need a caption,
   PRD-007's customizer needs a description, PRD-031's listing needs both.
   The wrapper is not speculative generality; it is the fields four PRDs have
   already written down.
3. **It merges better.** `manifest_merge` reaches `parts.<id>.configs.<name>.params.<param>`
   key-wise, which is exactly PRD-012 FR12 ("concurrent additions of
   different configs merge clean; divergent edits of the same config conflict
   explicitly") at finer granularity than the flat map could give.

The cost is one nesting level. That is the whole cost.

**One word for the object, one word for the place.** The object is a
**configuration** and it is validated by one function,
`packages.format.validate_configuration(entry, params_spec)`. `preset` names
only *where* one lives — a configuration published by a package. PRD-012's
`configs` map holds the same objects, validated by the same function, and a
test pins that (slice 1 ships the function and a test that a PRD-012-shaped
map validates through it unchanged).

**What this feature does NOT do:** `use_part` does not write `configs` into
the part record. PRD-012 owns that field, its `active_config`, its referential
integrity and its merge granularity; writing half of it here would freeze
behaviour 012 has not designed. `use_part` applies the chosen preset's params
as ordinary overrides and records the preset *name* in the provenance header,
so 012 can later adopt the whole preset map with a one-line copy and no
migration.

**Validation.** A configuration's `params` are validated exactly as
`set_params` validates: unknown names rejected, types enforced, enum
membership checked. Not by importing `service._normalize_param` (a private),
but by *applying* the configuration on the gate's scratch project through the
public `service.set_params` — so validation and the build that proves it are
the same act (Decision 9, `presets` stage).

---

## Decision 5 — materialize on use; the provenance header is immutable

FR4 picks copy-in. **Confirmed**, and for a stronger reason than the PRD gives
("portability and clone/push"). The decisive argument is that copy-in makes a
package part an **ordinary part at every seam in the codebase**, and reference
resolution would touch all of them:

| Seam | What it does today | Under reference resolution |
|---|---|---|
| `service._content_signature` | returns the script text (or the import's content hash) | a third `kind` |
| `service._rebuild` | sends `store.read_script(...)` text to the worker | resolve through the cache first |
| `mates.resolve` | `service.store.read_script(proj, inst.part)` | same |
| `packet.py` | `git diff` over `parts/*.py` | a package bump would be an invisible geometry change |
| `checks.py --ref` | measures a detached worktree on a **cold cache** | a bare CI checkout could not build at all |
| `anchors.py`, `comments.py`, `script_blocks.py` | address lines in `parts/<id>.py` | every one needs a resolver |
| `history` | tracks `project.json`, `parts/*.py`, `imports/` | a package part would be untracked or a phantom |

The PRD-004 row is the one that settles it: the GitHub Action checks a working
tree on a runner that has no `~/.agentcad`. Copy-in means **a project builds
with no cache, no index and no network** — the lockfile is for
re-materialisation and audit, not for building.

**The provenance header** is prepended to the materialised script:

```python
# agentcad:package 1 {"name": "iso4762", "version": "1.2.0",
#   "part": "cap_screw", "preset": "m5x16", "index": "agentcad-core",
#   "content_id": "sha256:9f3c…", "script_sha256": "sha256:41ab…"}
# The publish gate is a CORRECTNESS gate, not a security boundary — this
# script runs in your kernel worker with your privileges. See docs/packages.md.
```

- **Deterministic: no timestamp, no client id, no absolute path.** AC3 demands
  byte-identical re-materialisation from cache; a timestamp would break it on
  the second install. If anyone wants a time, it goes in the machine-local
  cache receipt, never in a file that lands in a git-tracked project. A test
  pins it.
- Read with `tokenize`'s COMMENT tokens (the `core/script_blocks.py`
  precedent), so a docstring quoting the marker is not a header.
- `script_sha256` covers the package's script bytes **without the header**, so
  a local edit is detectable and reportable — never repaired.

**The header is immutable; its status is computed on every read** — PRD-008's
anchor rule, and for the same reason: a stored status is a claim that goes
stale silently. `get_part` (wrapped, no kernel call, one manifest read plus a
regex) reports one of five:

| status | means |
|---|---|
| `ok` | the lock has this package at this version and the cached tree verifies |
| `modified` | the script bytes differ from `script_sha256` — the user edited it. Legitimate, reported, never repaired |
| `version_drift` | the lock holds a different version than the header |
| `removed` | no dependency entry — **FR6's warning, not breakage**. The part is a project file; it builds |
| `unverified` | no cache entry to compare against. "We did not look", never "fine" |

`remove_package` therefore **does not touch a single script byte** — which it
must not, because the header is inside the script and the script text is the
cache key (`service._cache_key`): rewriting headers on removal would re-key
every materialised part, invalidate every `.acm`, and cost a full rebuild to
express "this dependency is gone".

---

## Decision 6 — lockfile and cache: what is hashed, when, and what offline means

**Manifest keys** (additive, and absent for projects that use no packages, so
FR15's byte-identical guarantee is structural):

```jsonc
"packages":      {"iso4762": {"version_req": "^1.2.0", "index": "agentcad-core"}},
"packages_lock": {"iso4762": {"version": "1.2.0",
                              "content_id": "sha256:9f3c…",
                              "index": "agentcad-core",
                              "source": {"kind": "git",
                                         "url": "https://…/catalog.git",
                                         "ref": "main"}}}
```

**Nothing in either map is a timestamp, a path or a machine fact.** Two
branches that add `iso4762@1.2.0` from the same index write byte-identical
entries and merge clean; a lock carrying `installed_at` would conflict on
every concurrent add. Machine facts live in the cache receipt, which is never
committed.

**Version requirements**, hand-rolled, no new dependency (PRD-004's precedent):
`1.2.0` (exact), `^1.2.0` (>=1.2.0, <2.0.0), `~1.2.0` (>=1.2.0, <1.3.0), `*`
or omitted (highest non-yanked). Resolution picks the **highest non-yanked**
version satisfying the requirement. A requirement that matches only yanked
versions resolves to the yanked one *only* if a lock entry already names it
(FR10) — otherwise it is a `not_found_error` saying so.

**Cache layout:**

```
~/.agentcad/packages/<name>/<version>/          the extracted tree (FR5)
~/.agentcad/packages/<name>/.receipts/<version>.json
        {"content_id", "index", "source", "fetched_at", "bytes", "files"}
~/.agentcad/indexes/<name>/                     git index checkouts
```

The receipt is a sibling, never inside the version directory: a file inside
would change the tree's own content id.

**When the hash is verified:**

| Moment | What happens |
|---|---|
| install (`add_package`) | fetch → compute content id → compare with the index entry's declared `content_id` → mismatch is a `validation_error` naming both ids. Then write the tree into a temp dir and `os.replace` it into place, receipt last. |
| **every** `use_part` | re-verify the whole cached tree before materialising. Affordable because of Decision 3's ceilings; unconditional because a receipt is a claim and the tampered file is the thing we are looking for. |
| `list_packages` | verifies and reports per package (`ok` / `tampered` / `missing`), no exceptions raised |
| publish | verifies the built tree against the id it is about to publish |

**On mismatch: refuse, and never silently re-fetch.** A `validation_error`
naming the package, the version, the expected and actual ids and the first
differing file, with the fix spelled out (`remove the cache entry and re-add`).
Silently re-downloading over a mismatch is how a compromise becomes invisible.

**Offline** (FR5, AC4):

- `use_part` **never touches the network**, ever, on any path. It reads the
  lock, reads the cache, verifies, copies. That is the whole of AC4's "keeps
  working offline after the index disappears".
- `add_package` with no reachable index resolves **from the cache** when a
  cached version satisfies the requirement and verifies. The lock entry it
  writes is reconstructed from the receipt and is **byte-identical** to the one
  an online install would have written — because every field in it is
  content-determined. Offline is not a second code path with a second answer.
- `add_package` with neither index nor cache is a `not_found_error` naming the
  package, the requirement, every index it tried and why each failed.
- A package present in `packages` but absent from `packages_lock` (a
  hand-edited manifest) makes `use_part` **refuse**, fail-closed, telling the
  caller to run `add_package`. Guessing a version there is inventing a
  dependency.

---

## Decision 7 — one index client, three kinds; a git index is a local index plus a fetch

```python
class Index(Protocol):
    name: str
    kind: str            # "local" | "git" | "cloud"
    scope: str           # "public" | "private"
    def refresh(self) -> None: ...          # no-op for local
    def entries(self) -> dict: ...          # parsed index.json
    def fetch(self, name: str, version: str) -> Path: ...   # -> a directory
```

- **local** — a configured directory. `refresh()` does nothing; `fetch()`
  returns `<index>/<entry.path>`.
- **git** — `~/.agentcad/indexes/<name>/` is a clone at a pinned ref;
  `refresh()` fetches and hard-resets to it; everything after that is the
  local client over the checkout. **That reduction is the design**: one
  implementation of parsing, searching and fetching, and the git-ness is 40
  lines.
- **cloud** — interface only, registered by nothing, landing with PRD-005-lite
  as step 2. Reserved so that step 4 (031a) is an index kind, not a rewrite.

Configured in `~/.agentcad/config.json`, in precedence order:

```jsonc
"indexes": [
  {"name": "agentcad-core", "kind": "local", "path": "<bundled catalog>"},
  {"name": "acme", "kind": "git", "url": "https://…/parts.git", "ref": "main",
   "scope": "private"}
]
```

First index that answers a name wins; `add_package {index}` pins one
explicitly. Ambiguity exists only at install time — afterwards the lock records
which index answered, so it never recurs.

**The git runner is NOT `history._run`, and this is deliberate.** AGENTS.md's
rule ("every git call goes through `ProjectHistory._run` — never a raw
`subprocess`") is a statement about the *project history repository*, and
`_run` is built for exactly that: it hard-codes `--git-dir <project>/.history
--work-tree <project>`, a **10 s** timeout, and `HOME`/`XDG_CONFIG_HOME`
redirected into `.history` so the user's `~/.gitconfig` cannot interfere. Every
one of those is wrong for fetching a remote index: there is no work tree, a
clone routinely exceeds 10 s, and redirecting `HOME` disables the credential
helper a private index repo needs.

So `packages/_git.py` is a second, small runner with its own rules, and the
docstring says why it exists so nobody "fixes" it back:

- `git` resolved via `service.history.available()` (already cached, no new
  probe); no git ⇒ **git indexes register nothing** and say so, exactly as the
  versioning and proposals packs self-disable.
- Timeout 120 s, configurable; `GIT_TERMINAL_PROMPT=0` and `GIT_ASKPASS=`
  unset-to-empty so a server never blocks on a password prompt.
- `HOME` **not** redirected: a private index is the case that needs the user's
  credentials.
- Fixed argv, never a shell; the URL is validated (`https://`, `ssh://`,
  `git@host:path`, or an absolute path) and can never begin with `-`.
- `--depth 1` clone, `fetch --depth 1` + `reset --hard` on refresh.

---

## Decision 8 — search: structured always, semantic optional and honestly degraded

`search_packages {query?, index?, keywords?, standards?, param?, limit?}`
runs entirely in the server process over parsed `index.json` documents. No
kernel call, no download, no network beyond an index `refresh()`.

Ranking, deterministic and explainable: exact name (100) > name prefix (80) >
standards match (70) > keyword match (60) > summary/description substring (40)
> part-name or param-name match (30). Ties break on name, then version
descending. Every hit carries `{name, version, index, summary, standards,
license, disclosure, parts digest, presets, why: ["keyword:screw"]}` — `why`
because a search an agent cannot explain is a search it cannot correct.

`param` filters structurally: `{"name": "length", "min": 10, "max": 20}`
matches a part whose `length` range **overlaps** [10, 20]. This is the facet
that makes the index digest worth carrying.

**Semantic search is optional and never a hard dependency** (FR8). The index
may carry `embeddings: {model, dim, vectors: {…}}`, computed at publish or
index build. The result always reports what it did:

```jsonc
{"hits": [...], "semantic": false, "semantic_reason": "no_embedding_provider"}
```

MVP registers no provider, so the field is present and always `false` with a
reason. Present-and-false beats absent: an agent can tell that keyword search
is what it got, which is precisely the "degrades honestly" clause. The chat
agent already needs an Anthropic key; embeddings must not become a second hard
dependency for a catalog to be searchable.

---

## Decision 9 — the gate is orchestration over existing seams; it measures nothing new

Exactly PRD-004's posture. Every stage is a call into a reviewed surface.

| Stage | Calls | Why that call |
|---|---|---|
| `format` | nothing — pure data | `package.json` schema, semver, licence, disclosure, inventory, ceilings, path-traversal refusal, README present |
| `contract` | `kernel.request("inspect", {script})` | The same call `service._params_spec` makes. Asserts PARAMS + `build`, and the **package standard**: every numeric param declares `min`, `max`, `unit`, `description` (Decision 9a) |
| `presets` | `service.set_params(scratch, part, preset.params)` on the scratch project | Validation and the build that proves it become one act; no private `_normalize_param` import |
| `build` | `service._rebuild(scratch, variant_part_id)` per variant, fanned out | The service rebuild pipeline, cache-aware, with the `affinity=part_id` it already passes (`service.py:697`) |
| `specs` | `service.specs.run(scratch)` | PRD-003, all three tiers, and the documented exit from every cached refusal |
| `connectors` | `kernel.request("connectors", {script, params})`, then one `service.get_assembly(scratch)` | The `connectors` handler exists in `kernel/handlers/connectors.py` and **nothing in `core/` or the frontend has ever called it** — this feature is its first server-side consumer. `get_assembly` runs `mates.resolve` for us |
| `previews` | `render_view` (server-side `render_acm`, no kernel) | Same renderer the packet uses |
| `docs` | pure data | README non-trivial; every declared part has a summary and a module docstring |
| `policy` | `service.package_policy` if present, else `skip / no_policy_configured` | The seam PRD-031 FR2(b) plugs its AST gate into (Decision 11) |

**No new status vocabulary and no new report shape.** `packages/gate.py`
imports `summarize`, `report_status`, `assign_ids` from `core/specs.py` and
`make_item` / `make_stage` / `finalize_report` from `core/checks.py`. Rows are
**`items`**; statuses are the four (`pass|fail|skip|error`); stage and report
statuses are the three (`green|red|skip`). One product, one vocabulary — and
`agentcad package validate --report r.json` produces a document a reader of a
CI report already knows how to read.

### 9a — the extremes matrix: OAT plus presets, and explicitly **not** the cross product

The examples suite is the existing harness and this generalises it verbatim
(`tests/test_examples.py::test_parts_build_at_param_extremes`): for each
parameter, sweep it alone while the others stay at their defaults —

| type | swept values |
|---|---|
| `number` / `int` | `min`, `max` |
| `bool` | `True`, `False` |
| `enum` | every choice |
| `string` | the default only — the space is unbounded and the row says so |

plus the default configuration and **every declared preset**. Variants per
part: `1 + Σ|sweep(param)| + |presets|`.

Two decisions inside that:

- **A numeric parameter without `min` and `max` fails the `contract` stage.**
  `handle_inspect` makes both optional (`worker.py:490`), so "builds at every
  parameter's min and max" is otherwise a vacuous claim on an unbounded
  parameter. The examples suite already demands min/max/unit/description of
  every bundled part; a published package is held to the standard the
  bundled content already meets. The failure names the parameter and the fix.
- **The corner cross-product is out of scope, with the reason stated.**
  Parameters are routinely mutually constrained (a wall thicker than a bore
  radius), so demanding every corner would redden *correct* content — the
  worse failure mode, and the same judgement PRD-010 made about `close`
  corners and PRD-004 made about `strict_exempt`. An author who wants corner
  coverage declares those corners **as presets**, which the gate builds. The
  gate's claim is therefore exactly: *each parameter's own range, and every
  configuration the package ships* — never "every combination", and no
  document may say otherwise.

**Fan-out.** Each variant is created as its **own scratch part** (one
`add_part` each, up front, serial and cheap), so the build phase makes no
manifest writes at all: `service._rebuild(scratch, variant_id)` writes only
`.cache/` files (distinct keys) and one `_status` dict slot (distinct key).
Calls are dispatched on a `ThreadPoolExecutor(min(pool_size, 4))`;
`KernelPool._pick` routes by `hash(affinity) % size` and `KernelClient.request`
holds a per-worker lock (`client.py:135`), so concurrent calls are safe.

**This is the first in-process fan-out across the pool in the whole codebase**
(nothing outside `kernel/client.py` has ever used a thread). `pool.py`'s
"2.4–3.6x on batch builds" is a v2 *spike* number, not a shipped measurement.
The plan therefore measures serial against parallel on a real package and
keeps `--jobs 1` as an escape; if it does not pay, the fan-out is deleted and
the gate is serial, which is a slower gate and not a wrong one.

### 9b — the connector smoke is one assembly and one kernel round trip

For part `P` with connectors `C₁…Cₙ`, the gate builds **one** scratch
assembly:

- `a0` — an instance of `P` at the origin, the anchor.
- for each `Cᵢ`, one instance mated onto `a0.Cᵢ`. The moving side must be
  **rigid** (`_mates_resolver.py:261`: "the anchor connector carries the DOF"),
  so the mover is `P` itself when `P` declares any rigid connector, and
  otherwise a tiny bundled **probe part** with a single rigid connector at its
  origin.

One `service.get_assembly(scratch)` then resolves the whole set in a single
`resolve_mates` call. Per-connector rows are derived from the resolved
transforms: a connector that fails to resolve is a `fail` naming it and the
resolver's message. Cost: one kernel call for `connectors` per part plus one
`resolve_mates` for the package — not one per connector.

### 9c — the scratch project: PRD-004's containment rules, reused not reinvented

```
<work-dir>/agentcad-package-<pid>-<rand>/       mkdtemp, 0700, deleted by us only
    pkg_gate/                                   an ordinary project
        parts/<pkg>__<part>__<variant>.py
        project.json
        .cache/
```

- A second `AgentCADService` rooted at the cell, **sharing the warm kernel**
  (never a second pool: ~3 s and ~0.5 GB per worker).
- `bus.on_publish = None`, then `store.open(tree)`, then `build_registry`,
  then `store.branch_resolver = None` and `store.write_guard = None` — in that
  order, because `build_registry` is what installs the last two.
  `checks._ephemeral_service` says the write guard is "inert BY ACCIDENT.
  One future write inside a stage would make it live". **This is that write:**
  the gate calls `add_part` and `set_params` dozens of times, so the guard is
  live here and nulling it is load-bearing, not prophylactic.
- `--work-dir` is refused when it is, holds, or sits inside the projects root
  **or the package source directory** (`checks._refuse_overlap` plus one
  path). A run deletes only the cell it made.
- **`--work-dir` is a CLI-only flag.** The sandbox profile is fixed at worker
  spawn (`cli._build_service`'s docstring), so a running server cannot widen
  the writable roots; the tool and route paths always use the system temp dir,
  which `_writable_roots` already grants.
- **No user project is ever opened by a gate run**, so there is no path by
  which one can be written to. That is a structural claim, not a careful one.

---

## Decision 10 — publishing is fail-closed, with a named exempt set; versions are immutable

`agentcad package validate` is **report-honest** and `agentcad publish` is
**fail-closed** — PRD-004's exact split between `check` and the gates, for the
same reason: two audiences, one set of measurements.

- **Publish always runs every stage.** `validate` takes a stage subset;
  `publish` does not, so `skip / not_selected` cannot reach the verdict.
- Any `fail` or `error` blocks publish, naming the failing item in
  `details.checks[]` (the PRD's `validation_error` shape).
- A `skip` blocks publish **unless** its reason is in `PUBLISH_SKIP_EXEMPT`.
  The set is closed and each member is a fact about the *world*, never about
  the package's correctness:

  | reason | why it may not block |
  |---|---|
  | `fem_extra_missing` | this machine cannot measure; the package is not at fault |
  | `no_policy_configured` | there is no policy module yet (Decision 11) |
  | `string_param_unbounded` | a string parameter's space is unbounded, so only its default is swept (Decision 9a) |
  | `no_connectors_declared` | declaring no connectors is legitimate — a plain solid is a package |
  | `reference_part` | a reference-part package has no PARAMS to sweep (FR13) |

  Exempt rows carry `strict_exempt: true` — PRD-004's mechanism, and its
  lesson applies: a warning nothing can ever clear teaches readers to ignore
  warnings.
- **Every exempt skip is recorded in the published index entry**
  (`gate.exempt_skips`), so a consumer can read what was *not* measured. That
  is what stops "validated" from becoming a badge.

**Immutability (FR10).** Republishing an existing `name@version` is a
`conflict_error` naming it, even if the content id is identical — a byte
comparison would let a publisher redefine "identical" later. **Yank**
(`agentcad publish --yank <name>@<version>`) flips `yanked: true` in the index
and deletes nothing: a lockfile naming it keeps resolving, a fresh requirement
never selects it, and `add_package` on an explicitly-named yanked version
warns and proceeds.

---

## Decision 11 — the publish gate is not a security boundary, said in eight places

A package is Python. `use_part` copies it into the project and the next
rebuild executes it in the kernel worker with the user's privileges — on macOS
inside the deny-by-default seatbelt profile (`kernel/sandbox.py`: writes only
in project roots, no network), on Linux and Windows unconfined. **The gate
proves that geometry builds, specs pass and connectors mate. It proves nothing
about intent.** PRD-006 is the backstop and is deferred to step 5.

The non-claim appears, in these words or a faithful shortening, in:

1. `docs/packages.md`, in the first screen, not an appendix;
2. the `add_package` tool description;
3. the `use_part` tool description;
4. the `publish_package` / `validate_package` tool descriptions;
5. the gate report itself, as a top-level `"note"` field, so it travels with
   every copy of the evidence;
6. `index.json`'s format documentation;
7. the materialised provenance header (Decision 5), which is the copy that
   ends up in the consumer's repository;
8. the Library dialog's install affordance.

**The policy seam.** PRD-031 FR2(b) is a static AST gate — import allowlist,
no `exec`/`eval`/dynamic import/dunder escapes — and its technical approach
says "one policy module, three consumers" (011's CLI, 031's publish, 029's
skill lint). This design ships **the seam and no policy**: if
`service.package_policy` exists, the `policy` stage calls
`policy.check(source, path) -> [items]` and folds the rows in; if it does not,
the stage is one honest `skip / no_policy_configured` row. Building the
allowlist now would mean freezing which toolkit imports a package may use
before a single third-party package exists, and an allowlist that is wrong is
worse than one that is absent and labelled.

---

## Decision 12 — module layout, the OCP boundary, and the pack-name trap

**`agentcad/core/packages/`** is a subpackage, not one 2 000-line module.
`pkgutil.iter_modules(agentcad.core.__path__)` sees it as `packages`
(`ispkg=True`) and the loader filters on `startswith("tools_")`, so it
registers nothing and cannot be mistaken for a pack.

| Module | Contents | OCP-free |
|---|---|---|
| `format.py` | `package.json` / `index.json` / configuration schemas, semver, validation | yes |
| `content.py` | the content id, the inventory, the ceilings | yes |
| `cache.py` | `~/.agentcad/packages`, receipts, verification, atomic install | yes |
| `lockfile.py` | the two manifest maps, resolution, offline reconstruction | yes |
| `indexes.py` | the client protocol, local + git, `_git.py` runner | yes |
| `search.py` | structured filters, ranking, the semantic seam | yes |
| `provenance.py` | header emit/parse, status-on-read | yes |
| `gate.py` | the stage pipeline over the ephemeral service | yes |
| `manager.py` | `PackageManager` — the façade `service.packages` | yes |

**Every one is OCP-free**, asserted in a fresh interpreter with `OCP` and
`build123d` blocked at `sys.meta_path` — the `tests/test_checks.py`
`_NO_KERNEL_PROBE` pattern, one probe over `agentcad.core.packages` with a
smoke expression, plus a list-matches-the-tree test on the model of
`tests/test_toolkit_ocp_free.py::test_ocp_free_list_matches_the_tree` so a new
module cannot be added without a probe. `gate.py` sequences kernel calls
*through a service* and imports `KernelError` from `kernel.client` — the module
that spawns workers, not one that imports geometry — exactly as `checks.py`
does.

`agentcad/core/tools_packages.py` is OCP-free too, and asserted, on the
`core/tools_holes.py` precedent.

### The pack name, and the load-order trap

`tools._load_tool_packs` walks `pkgutil.iter_modules` **alphabetically**, and
`tools_proposals.py:51` assigns `service.gate_providers = []`
**unconditionally**. `tools_packages` sorts at `pac`, which is **before**
`pro` — so anything this pack appended to `gate_providers` would be silently
discarded, no error, no warning. That is the trap that forced
`tools_run_checks.py` over `tools_checks.py`.

**Decision: the pack is `agentcad/core/tools_packages.py`, and it registers no
gate provider — deliberately, permanently, and with a test that says so.**

- **The publish gate is not a merge gate.** It gates `publish`, a CLI/tool
  action on a *directory*, not a proposal merge. PRD-011 lists no gate in its
  Agent-surface section and none in its acceptance criteria. A materialised
  package part is an ordinary part, so PRD-004's `checks` gate already
  rebuilds it, runs its specs and re-resolves the assembly on every proposal.
  There is nothing a `packages` gate would add that step 1 needs.
- **The name is the subject, and the subject is right.** `tools_packages.py`
  is what a reader looks for. Renaming it to dodge a hazard it does not have
  would buy a worse name for a capability we decided not to build.
- **The hazard is documented and pinned.** The module docstring states the
  load-order fact and the prohibition in full; a test asserts no provider
  named `packages` is ever in `service.gate_providers`.
- **The escape hatch is named, not left to be rediscovered.** If a `packages`
  gate is ever wanted (the plausible one: "every dependency in this proposal
  is locked and verifies"), it goes in a *second* pack, `tools_publish.py` —
  `pub` sorts after `pro` — or is installed lazily from
  `routes_packages.py`, which is the `routes_presence` claim-guard precedent.
  It never goes in this file.

The rest of the load order at `pac`: `tools_holes` (`h`), `tools_import`
(`i`), `tools_mates` (`m`) are already loaded; `tools_proposals` (`p`),
`tools_run_checks` (`r`), `tools_specs` (`s`), `tools_versioning` (`v`) are
**not**. So `service.specs`, `service.branches`, `service.proposals` and
`service.gate_providers` are read **inside methods, never captured at
registration** — PRD-003's and PRD-004's exact rule.

`get_part` and `get_project` gain package provenance by **wrapping the bound
methods** with an idempotent attribute marker
(`tools_specs.install_rebuild_specs` is the precedent, and `tools_specs` at
`s` wraps after us, composing cleanly). Provenance costs **zero kernel calls**:
one manifest read and a `tokenize` scan of a script `get_part` already read —
PRD-008's `signature_table` rule.

---

## Decision 13 — what the manifest merge driver needs

`manifest_merge._merge_section` special-cases `parts`, `assembly` and
`materials`; **every other top-level key merges as one whole value**
(`_merge_atomic`). So today, two branches each adding a *different* package
would conflict on the whole `packages` object. Two additions, both on the
`materials` precedent:

1. `_merge_section` routes `packages` and `packages_lock` through
   `_merge_entry_dict` — per-package-name entries, each **atomic**. Atomic per
   entry for the same reason `materials.<id>` is: merging one side's version
   with the other side's content id yields a lock entry nobody authored and
   that verifies against nothing.
2. `_write_path` learns the two heads. It currently writes
   `("materials", id)` and `("assembly", …)` into their dicts and falls
   through to `_write_slot(manifest, ".".join(segs))` otherwise — which for
   `("packages", "iso4762")` would create a bogus flat key
   `"packages.iso4762"`. `apply_choices` is broken for the new keys until
   both sides are added, so they land in the same commit with a test.

This is an additive change to a PRD-001 module, which the extension-point
contract permits (the four forbidden cores are `worker.py`, `tools.py`,
`app.py`, `service.py`). It is **the only edit to an existing PRD-001–008
module in this feature.**

---

## Surfaces

### Tools (6)

| Tool | Shape |
|---|---|
| `search_packages` | `{query?, index?, keywords?, standards?, param?, limit?}` → `{hits, semantic, semantic_reason}` |
| `add_package` | `{project, name, version_req?, index?}` → post-state: `{package, lock, cached, offline}` |
| `remove_package` | `{project, name}` → `{removed, materialized_parts: [...]}` — names the parts that now report `removed` provenance |
| `list_packages` | `{project?}` → installed with `{version, latest, stale, cache: ok\|tampered\|missing}` + configured indexes |
| `use_part` | `{project, package, part, part_id, preset?, params?}` → the ordinary `get_part` payload plus `provenance` |
| `validate_package` | `{path, strict?, stages?, jobs?}` → the gate report (no publish, no side effects) |

`validate_package` is a *tool* because the PRD's flagship authoring loop is an
agent running the gate, reading `details.checks`, fixing and re-validating —
"the kernel referees curation". It has no side effects outside its own
throwaway cell, so it is safe to expose. `package_from_step` is added as a
seventh tool by the McMaster slice.

**`publish` is CLI-only in this feature**; the cloud publish route lands with
PRD-005-lite (step 2). Registering a `publish_package` tool that can only
write to a local directory would be a tool an agent cannot usefully call.

### CLI

```
agentcad package validate <dir> [--strict] [--report PATH] [--jobs N] [--work-dir DIR]
agentcad publish <dir> --index <name> [--yank] [--jobs N] [--work-dir DIR]
```

Exit codes are `check`'s: `0` green · `1` red (the package is wrong) · `2`
harness (we could not produce a verdict). Headless, one warm kernel, no server
— `cmd_check`'s shape exactly, including the `finally` that stops the kernel.

### Routes (`routes_packages.py`)

```
GET  /api/packages/search        ?query=&index=&limit=
GET  /api/projects/{p}/packages
POST /api/projects/{p}/packages           {name, version_req?, index?}
DELETE /api/projects/{p}/packages/{name}
POST /api/projects/{p}/packages/{name}/use  {part, part_id, preset?, params?}
```

Whitelisted body keys, never `**body`; `notfound`/`validation`/`conflict`
re-raised to 404/422/409.

### Events and errors

`project_changed` on add / remove / use (ordinary store writes, so history
snapshots and undo are free). **No new event type.** Errors are the house
three: `validation_error` (gate failures with `details.checks[]`, tampered
cache, bad format), `conflict_error` (republish, a `part_id` already in the
project), `not_found_error` (unresolvable package, missing preset, missing
part). No new error class.

---

## Seed content: the bundled catalog is a local index *and* a git index

`catalog/` at the repo root holds `index.json` and the published package
directories. It is:

- **auto-registered as a local index at startup**, exactly as
  `cli._register_examples` registers the bundled example projects — so
  `search_packages` answers on a fresh install with no network and no config;
- **the same bytes a git index serves**, so adding this repository by URL
  exercises the git client against real content with nothing hosted;
- **dogfooded**: `agentcad package validate` over every catalog package runs
  in the suite, which is what stops the seed catalog from rotting.

Contents, in the order the plan lands them:

1. `iso4762` — socket-head cap screws over `toolkit.threads.cap_screw`
   (`SocketHeadCapScrew`), `size` as an enum, `length` bounded, connectors
   `axis` (cylindrical) and `head_seat` (rigid), presets per catalogue size,
   SPECS asserting head and shank diameters. `examples/fasteners/parts/cap_screw.py`
   is the 27-line ancestor; the package adds the connectors and presets it
   never had.
2. `iso4014` hex bolts (`toolkit.threads.hex_bolt`), `iso7380` button heads,
   threaded inserts.
3. `din625` (608-class) bearings, `extrusion_2020` / `extrusion_3030`,
   `nema17` / `nema23` motor outlines — each with connectors, interface-dimension
   SPECS and docs.

Every one passes the same gate. The gate **is** the curation.

---

## Data flow — the AC1 walk

```
add_package {project: "rig", name: "iso4762"}
  manager.add
    indexes in precedence order -> "agentcad-core" (local, bundled catalog)
    resolve "^1.0.0" -> 1.0.0 (highest non-yanked)
    cache.install(entry)
        content.tree_digest(<catalog>/iso4762/1.0.0)  == entry.content_id ?
        copy -> ~/.agentcad/packages/iso4762/.staging-<rand>/ -> os.replace
        receipt -> ~/.agentcad/packages/iso4762/.receipts/1.0.0.json
    lockfile.add(manifest) -> packages + packages_lock ; store.save_manifest
    bus.publish project_changed        -> history snapshot, undo entry

use_part {project: "rig", package: "iso4762", part: "cap_screw",
          preset: "m5x16", part_id: "screw_1"}
  cache.verify("iso4762", "1.0.0")               <- every time, no exceptions
  provenance.header(...)  +  parts/cap_screw.py  -> parts/screw_1.py
  service.create_part(...)  ; service.set_params(rig, screw_1, preset.params)
  -> rebuild: cache key = sha256(header+script, params, density, tolerance)

set_mate {instance: "screw_1_1", connector: "head_seat",
          to_instance: "plate_1", to_connector: "bolt_hole_1"}
  mates.resolve -> a real ISO 4762 M5×16 seated on a PRD-010 tapped hole
```

---

## Testing strategy

- `tests/test_packages_format.py` — schemas, semver, configuration validation
  (including a PRD-012-shaped `configs` map through the same function),
  content id determinism (directory vs copy vs reordered walk), ceilings.
- `tests/test_packages_cache.py` — install, receipt, verify, **tamper detection
  and refusal**, atomic replace, offline reconstruction of a byte-identical
  lock entry.
- `tests/test_packages_index.py` — local client; git client against a
  `file://` repo created in a tmp dir (`portability`), refresh, pinned ref,
  index unreachable ⇒ cache path (AC4).
- `tests/test_packages_gate.py` (`slow`) — each stage; the two AC2 corruptions
  (a variant that breaks at an extreme, a broken connector) named in
  `details.checks`; the containment assertions: the user's projects dir is
  byte-identical after a run, the three ephemeral seams are nulled, a
  `--work-dir` overlapping the projects root or the package dir is refused.
- `tests/test_packages_tools.py` — the six tools, provenance statuses,
  `remove_package` degradation, manifest key-wise merge, the load-order
  assertions (no gate provider; nothing captured at registration).
- `tests/test_packages_ocp_free.py` — fresh-interpreter probes.
- `tests/test_prd011_acceptance.py` — AC1–AC8, examples on a **copy**,
  `TestClient(base_url="http://127.0.0.1")`,
  `create_app(..., extra_allowed_hosts={"testserver"})`.
- `tests/test_catalog.py` (`slow`) — every bundled catalog package passes the
  gate.

---

## Risks and open questions

- **First in-process pool fan-out in the codebase.** Measured in slice 5;
  `--jobs 1` is the escape and deleting the fan-out is an acceptable outcome.
- **`bd_warehouse` size/length interactions.** A catalogue fastener's valid
  lengths depend on its size, so an OAT sweep can reach a combination
  bd_warehouse refuses. That is what presets are for, and if it proves
  systemic the package declares a narrower `length` range per size by
  splitting the family into one part per size — a modelling answer, not a gate
  weakening.
- **Cache re-verification cost** if the ceilings prove too generous for
  reference-part packages carrying a 40 MB STEP. Measured in slice 2; the
  fallback is a receipt fast path keyed on `(size, mtime_ns)` per file, which
  is weaker and would have to say so.
- **No LICENSE file in this repository** (verified: no `LICENSE*` at root, no
  `license` field in `pyproject.toml`). The format requires a licence per
  package; the seed catalog's packages therefore need a real value, and this
  is the roadmap's own "resolve before 031a" blocker arriving one step early.
  **Founder decision, not an engineering one.**
- **`packages` in `get_project`** grows the payload every client parses. Kept
  to a summary (`{name: {version, provenance_ok}}`); the detail is
  `list_packages`.

## Naming traps (live collisions in this tree today)

- **`registry` already means `ToolRegistry`** — every pack's signature is
  `register(registry, service)`. The package registry is never called
  `registry` in code; the manager is `service.packages`, the module is
  `core/packages/`, and a module named `core/registry.py` is forbidden.
- **`packages` vs `packages_lock`** are the manifest keys; the *cache* is the
  cache and the *index* is the index. Three words, three things, no overlap.
- **`preset` is a place, `configuration` is the object** (Decision 4).
- **`items`, never `checks`** — the gate report reuses PRD-004's rows and
  PRD-004's reason.
- **`provenance`** already means two things in this tree: PRD-010's
  hole-standard citations (`hole_standards.merge_provenance`) and PRD-002's
  packet provenance. Package provenance always appears qualified —
  `package_provenance` in payload keys, `packages/provenance.py` in the tree —
  and never as a bare `provenance` key on a part.

---

## PRD divergences to fold back

1. **FR1: presets do not have the schema PRD-012 declares.** PRD-012 FR1
   specifies `configs: {name → {param: value}}`; this design freezes the
   wrapped `{name: {params, label?, description?}}` and **PRD-012 FR1 must be
   amended to match**. The flat map is ambiguous the day a part declares a
   `label` parameter, and four PRDs already need the metadata (Decision 4).
2. **FR2: there is no archive.** A package is a directory and the content id
   is a canonical file-tree digest, because tar is not byte-stable across
   producers — the DXF lesson. "The sha256 of its canonical archive" becomes
   "the sha256 of its canonical content listing" (Decision 3).
3. **FR9: "every part builds at every parameter's min and max" is not
   achievable as written, and is under-specified where it is.** `min`/`max`
   are optional in the PARAMS contract (`worker._normalized_spec_entry`), so
   the gate additionally *requires* them of a published package and fails the
   `contract` stage otherwise. And the gate sweeps **one parameter at a time
   plus every declared preset** — not the cross product, which would redden
   correct content whose parameters are mutually constrained (Decision 9a).
4. **FR9: the gate does not check drawings**, though PRD-031 FR2(a) says
   PRD-011's validation includes them. A package contains parts, not sheets,
   and `generate_drawing` is a project-scoped tool over PMI a package does not
   ship. If 031 wants drawings in the gate it is a stage added there, and
   031 FR2(a)'s parenthetical should drop "drawings".
5. **FR7/FR8: the cloud index and semantic search ship as interfaces, not
   implementations.** The cloud client is a registered-by-nothing protocol
   until PRD-005-lite; `semantic` is present in every search result and always
   `false` with a reason. Both are honest absences rather than missing fields
   — but neither is delivered in this feature, and the PRD's phase-2 list is
   where they stay.
6. **The "Agent surface" lists `agentcad publish` and a cloud publish route as
   one item; only the CLI ships.** A `publish_package` *tool* that can write
   only to a local directory is a tool an agent cannot usefully call, and the
   route needs PRD-005's tenancy.
7. **FR13 (`package_from_step`) ships thinner than written.** It wraps a
   vendor STEP as a reference-part package with `provenance.vendor` and
   `redistributable: false` enforced at publish, but **agent-assisted
   connector placement is not automated** — the tool reports `face_info`
   candidates and the author (human or agent) writes the `connectors`
   function. Automating connector inference from an imported solid is a
   research problem, not a slice, and PRD-032 is where it belongs.
8. **The PRD's own "MVP" ordering is superseded by the roadmap.** The seeded
   COTS catalog and the git index are MVP here (step 1, registry-first), while
   the Library dialog moves behind them. Recorded because the PRD text still
   reads "phase 2" for both.
