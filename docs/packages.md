# Packages — the parts library and registry

A **package** is a directory of parametric part scripts with a manifest, a
licence, presets, docs and previews. An **index** is a directory (or a git
repository, or the bundled `catalog/`) that publishes packages. A project
records what it depends on in two additive manifest maps, and `use_part`
copies a package part *into* the project as an ordinary part.

> ## The gate is not a security boundary
>
> `agentcad publish` runs a nine-stage gate that proves **the geometry builds
> at every declared extreme, the specs pass, and the connectors mate**. It
> proves *nothing about intent*. A package is Python: `use_part` copies it into
> your project and the next rebuild executes it **in your kernel worker with
> your privileges** — on macOS inside the deny-by-default seatbelt profile
> (writes only in project roots, no network), on Linux and Windows unconfined.
> Install packages from indexes you trust, exactly as you would a pip package.
> [PRD-006](prd/pending/PRD-006-sandboxing-quotas.md) is the deferred backstop
> and this sentence is what stands in for it until then.
>
> **The trust boundary that does exist** is narrow and worth knowing exactly:
> the *index declares* a content id for every version, and the *cache verifies*
> every fetched tree against that declaration before a byte is installed, then
> re-verifies the whole tree on every materialisation. So a tampered download,
> a tampered checkout and a tampered cache are all caught. An index that lies
> about **both** the tree and the id it declares is a compromised index, and
> the answer to that is signatures — the `signatures` slot is reserved and
> empty in every index this build writes, and PRD-031 FR2(d) is where it gets
> filled.

---

## Quick start

```bash
# what is available (the bundled catalog answers with no network and no config)
agentcad …                       # or, from an agent / the chat dock:
search_packages {"query": "cap screw"}

# install a dependency, then materialise a part from it
add_package {"project": "rig", "name": "iso4762"}
use_part    {"project": "rig", "package": "iso4762", "part": "cap_screw",
             "part_id": "screw_1", "preset": "m5x16"}

# author, check and publish your own
agentcad package validate ./my_package
agentcad publish ./my_package --index my-org
```

In the browser, the **Library** button opens the same thing: search, a
preview, the disclosure badge, the declared parameter table, a preset picker
and one "Add to project" that installs the dependency and then materialises
the part.

---

## The package format

A package is a **directory**. There is no archive: the identity is a
canonical *content listing*, because tar and zip are not byte-stable across
producers (the same lesson DXF taught the determinism check).

```
my_package/
  package.json          the manifest
  parts/<id>.py         one part script per declared part
  imports/<file>.step   a reference part's vendor file (kind: "reference")
  presets.json          named configurations, per part
  docs/README.md        documentation (a stub is refused)
  previews/<id>*.png    a rendered preview per part
```

### `package.json`

| field | required | meaning |
|---|---|---|
| `format` | yes | `1` |
| `name` | yes | `^[a-z][a-z0-9_-]{0,39}$` |
| `version` | yes | `X.Y.Z`, **no leading zeros**, no prereleases in v1 |
| `summary` | yes | one line; every search hit and index entry carries it |
| `license` | yes | a non-empty licence id |
| `authors` | yes | non-empty list of `{name, email?, url?}` objects |
| `disclosure` | yes | `human` \| `agent` \| `hybrid` — who authored the geometry |
| `parts` | yes | `{id: entry}`, non-empty (see below) |
| `keywords`, `standards` | no | lists of strings; both are search facets |
| `min_agentcad` | no | `X.Y.Z` |
| `provenance` | no | `{generator?: {name, version}, vendor?: {…}}` |
| `remix` | no | **reserved**: `{of: {name, version, content_id}}` |
| `requires` | no | **reserved**: `{extras: ["fem"]}` |

**Unknown keys are errors, at every level.** A format that silently swallows
`licence` teaches authors that the typo worked.

A **part entry** is one of two kinds:

```jsonc
"cap_screw": {                       // kind: "script" (the default)
  "file": "parts/cap_screw.py",      // must be inside parts/
  "label": "Socket-head cap screw",
  "summary": "ISO 4762 …"
}
"bracket": {                         // kind: "reference" — imported geometry
  "kind": "reference",
  "source": "imports/91290A115.step",   // must be inside imports/
  "label": "91290A115",
  "summary": "ACME Supply 91290A115"
}
```

`kind` is optional and **absent means `script`** — every package published
before reference parts existed declares none, and a version is immutable.
An entry may not carry the other kind's key.

### `presets.json` — configurations

A preset **is** a configuration. One entry shape, and it is the schema
[PRD-012](prd/pending/PRD-012-configurations.md) adopts for
`parts.<id>.configs` entry for entry:

```jsonc
{
  "format": 1,
  "presets": {
    "cap_screw": {
      "m5x16": {
        "params": {"size": "M5-0.8", "length": 16.0},
        "label": "M5 × 16",
        "description": "…"
      }
    }
  }
}
```

The parameters are **wrapped in `params`** rather than being the entry itself,
because a flat `{name: {param: value}}` map is ambiguous the day a part
declares a parameter called `label` — and part scripts declare arbitrary
parameter names. One object, one validator
(`packages.format.validate_configuration`), one word: the object is a
**configuration** and `preset` names only *where* one lives.

### The content id

```
content_id = "sha256:" + sha256("".join(f"{path}\0{sha256(bytes)}\n"))
```

over every file in the tree, sorted by POSIX path, ignoring `.git/`,
`__pycache__/`, `*.pyc`, `.DS_Store` and `*.tmp`, and **refusing every
symlink**. No mtimes, no modes, no walk order — so a directory and a copy of
it have the same id, and one added byte, one added file or one renamed file
changes it.

Ceilings, enforced on publish and on install: **50 MB per package, 5 MB per
file, 500 files.** Re-verifying the whole tree costs 1.1 ms on a realistic
8-file package and 67 ms at the ceiling, which is why every materialisation
re-verifies rather than trusting a receipt.

---

## What a project records

Two additive manifest maps, absent entirely for a project that uses no
packages (so such a project is byte-identical to one from before this feature
existed):

```jsonc
"packages":      {"iso4762": {"version_req": "^1.0.0", "index": "agentcad-core"}},
"packages_lock": {"iso4762": {"version": "1.0.0", "content_id": "sha256:…",
                              "index": "agentcad-core", "source": {…}}}
```

**Neither map holds a machine fact** — no timestamp, no client id, no absolute
path. That is what makes two branches adding the same package write
byte-identical entries and merge clean, and what makes an *offline* install
write the same entry an online one would. Machine facts live in
`~/.agentcad/packages/<name>/.receipts/<version>.json`, which is never
committed.

`manifest_merge` merges both maps **key-wise per package name, with each entry
atomic** (the `materials.<id>` precedent): two branches adding *different*
packages merge clean; the same package at two versions conflicts at
`packages_lock.<name>`, because one side's version with the other's content id
is an entry nobody authored.

### The provenance header

`use_part` prepends an immutable header to the copied script:

```python
# agentcad:package 1 {"name": "iso4762", "version": "1.0.0", "part": "cap_screw", …}
# The publish gate is a CORRECTNESS gate, not a security boundary — this
# script runs in your kernel worker with your privileges. See docs/packages.md.
```

It carries **no timestamp, no client id and no absolute path**, so
re-materialising the same package part from the cache is byte-identical. Its
**status is computed on every read** — never stored — with zero kernel calls:

| status | means |
|---|---|
| `ok` | the lock has this package at this version, the script bytes match, the cached tree verifies |
| `modified` | you edited the script. Legitimate, reported, **never repaired** |
| `version_drift` | the lock holds a different version than the header |
| `removed` | no dependency entry — a **warning**, not breakage: the part is a project file and it still builds |
| `unverified` | we did not look: no cache entry, a tampered one, or a header written by a newer format |

On a fresh clone with a cold cache, provenance reads **`unverified`**, not
`ok`. That is the honest answer: nothing was compared.

`remove_package` **does not touch one script byte**, and it does not touch the
cache. The header lives inside the script and the script text is the rebuild
cache key, so rewriting headers to express a removal would re-key and rebuild
every materialised part.

---

## Indexes

| kind | what it is |
|---|---|
| `local` | a directory holding `index.json` and the published trees |
| `git` | a repository cloned to `~/.agentcad/indexes/<name>/` at a pinned ref — **a local index plus a fetch** |
| `cloud` | reserved; lands with PRD-005-lite |

Configured in `~/.agentcad/config.json`, **in precedence order**:

```jsonc
{"indexes": [
  {"name": "my-org", "kind": "git", "url": "git@github.com:acme/cad.git", "ref": "main"},
  {"name": "vendor", "kind": "local", "path": "/Users/me/vendor-parts", "scope": "private"}
]}
```

The bundled `catalog/` registers itself as `agentcad-core` and is **appended
after** whatever you configured — a fallback that outranked your own
configuration would be a fallback you could not override. One consequence
worth knowing: a user index *named* `agentcad-core` **replaces the bundled one
entirely**, including as a publish target. That is the escape hatch (it is how
you shadow the shipped catalog); it is not a merge.

An index that is unreachable, malformed or misconfigured is **skipped with a
warning**, never fatal: one broken index must not make the others unsearchable,
and every failure travels in `tried` on a `not_found_error`. A git index whose
remote has gone keeps answering from its **last good checkout**, marked
`stale`.

Resolution walks the indexes in order; the first that answers wins; `index=`
pins one. When **no index answers**, resolution falls back to the **cache** —
the highest cached version that satisfies the requirement *and still verifies*
— and reconstructs a lock entry byte-identical to the online one. A cached
tree that does not verify is not a fallback; it is the thing verification
exists to catch.

Requirements: `X.Y.Z` · `^X.Y.Z` (`>=X.Y.Z <X+1.0.0`) · `~X.Y.Z`
(`>=X.Y.Z <X.Y+1.0`) · `*`. **`^0.x.y` here is `>=0.x.y, <1.0.0`** — npm treats
a `0.x` caret as `~`, and a reader will assume npm.

---

## Publishing

```bash
agentcad package validate <dir> [--strict] [--report PATH] [--jobs N] [--work-dir DIR] [--budget S]
agentcad publish <dir> --index <name> [--jobs N] [--work-dir DIR] [--budget S]
agentcad publish --yank <name>@<version> --index <name>
```

`validate` is **report-honest**; `publish` is **fail-closed**. Exit codes are
`agentcad check`'s: `0` green · `1` the package is wrong · `2` we could not
produce a verdict.

**`index.json` is a build product of the gate, never hand-edited.** Every
entry — the parts digest, the preset list, the preview paths, the
`gate: {status, exempt_skips, agentcad, build123d, report_id}` record — is
derived from the gate's own measurements at publish time.

### The nine stages

| stage | what it measures |
|---|---|
| `format` | the manifest, the inventory, the ceilings, the README floor, the shipped previews |
| `contract` | one `inspect` per part: PARAMS and `build(p)`, **and** the package standard — every `number`/`int` parameter declares `min`, `max`, `unit` and `description` |
| `presets` | every configuration, validated against the inspected spec **and applied** through `set_params` |
| `build` | every part at **each parameter's own min and max**, plus **every declared configuration** |
| `specs` | PRD-003's checks, over every variant — not only the default |
| `connectors` | every declared connector, mated in one scratch assembly |
| `previews` | a server-side render per part, plus the shipped PNG parsed (**no pixel comparison**) |
| `docs` | the README names every part id; every part has a summary and a module docstring |
| `policy` | `service.package_policy` if one is installed, else one honest skip |

**The gate's claim, stated exactly: each parameter's own range, and every
configuration the package ships. Never "every combination."** The cross
product would redden *correct* content whose parameters are mutually
constrained (a wall thicker than a bore radius). An author who needs a corner
declares it **as a preset**, and the gate builds every preset. The variant
count is `1 + Σ|sweep| + |presets|` — a sum.

### Fail-closed, with a named exempt set

Any `fail` or `error` blocks publish. A `skip` blocks **unless** its reason is
a fact about the *world* rather than about the package:

`fem_extra_missing` · `no_policy_configured` · `string_param_unbounded` ·
`no_connectors_declared` · `reference_part`

…plus two stage-level absences that are legitimate: `no_presets_declared` and
`not_declared` (no SPECS). Everything else a skipped stage can mean — a
budget that ran out, a stage nobody selected, a renderer that was not there —
is "we did not look", which may not read as "publishable".

**Every exempt skip is published in the index entry** as
`<stage>:<reason>`, so a consumer reads what was *not* measured. That is what
stops "validated" from becoming a badge.

### Immutability and yank

Republishing an existing `name@version` is a `conflict_error` **even when the
content id is identical** — a byte comparison would let a publisher redefine
"identical" later. A fix is a new version.

`--yank` flips `yanked: true` and **deletes nothing**: a lockfile naming a
yanked version keeps resolving and `use_part` never even notices (it reads the
lock and the cache), a fresh *range* never selects it — including from a warm
cache — and an explicitly-named yanked version warns and proceeds.

### Containment

A gate run materialises into `<work-dir>/agentcad-package-<pid>-<rand>/`,
drives a **second, ephemeral service** rooted there over the same warm kernel
with `bus.on_publish`, `store.branch_resolver` and `store.write_guard` all
nulled, and **deletes only the cell it made**. A `--work-dir` that is, holds or
sits inside the projects root **or the package directory** is refused with both
paths named. No user project is ever opened.

---

## The bundled catalog

`catalog/` at the repo root is the `agentcad-core` index, and it ships nine
packages, every one green through the real gate:

| package | part | connectors |
|---|---|---|
| `iso4762` | socket-head cap screws, M3–M12 | `head_seat` (rigid), `axis` (cylindrical) |
| `iso4014` | hex bolts, M4–M12 | `head_seat`, `axis` |
| `iso7380` | button-head screws, M3–M12 | `head_seat`, `axis` |
| `thread_insert` | heat-set inserts, M2–M6 | `seat`, `bore` |
| `din625` | 6xx/60xx ball bearings | `bore` (cylindrical), `face` (rigid) |
| `extrusion_2020`, `extrusion_3030` | T-slot bar, cut to length | six rigid: both ends and each face's slot centreline |
| `nema17`, `nema23` | motor outlines | `face_mount` (rigid), `shaft` (cylindrical) |

Two things to know before you use them:

- **The non-fastener packages are interface models.** A bearing with no balls,
  an extrusion with no web fillets, a motor whose body is a solid block. Each
  is the geometry a bracket has to fit and clear, at the published interface
  dimensions. Do not read a motor mass off one; each README names what is not
  modelled.
- **Three size vocabularies live here.** PRD-010's hole standards say `"M5"`;
  bd_warehouse fasteners say `"M5-0.8"`; a ruthex insert says `"M3-0.5-4.0"`.
  Each package's README says which one its `size` enum speaks.

Fasteners expose `thread: cosmetic | real`. They are dimensionally identical
to within 1e-6 mm on the bounding box, so switching never moves a mate — but a
**cosmetic** screw's shank is drawn at the thread *root* (⌀4.134 for M5) and
drops into a ⌀4.2 tap-drilled hole reporting no interference, while a **real**
thread reaches the nominal ⌀5.000 and overlaps it. That overlap *is* thread
engagement. `cosmetic` is the default because real-thread cost grows with the
number of turns (M3 × 100 takes 5.17 s).

The catalog is **data**: delete it and the product degrades to "no packages
configured" with a warning, breaking no code path.

---

## Vendor STEP files — `package_from_step`

```
package_from_step {"source": "/abs/91290A115.STEP", "dest": "./acme_bracket",
                   "name": "acme_bracket", "part": "bracket",
                   "vendor": "ACME Supply", "part_number": "91290A115",
                   "terms": "…"}
```

It builds the file once in a throwaway cell (so a STEP your kernel cannot load
is a refusal, not a directory to clean up), then writes the package: the file
under `imports/`, a `kind: "reference"` part entry, `provenance.vendor` with
**`redistributable: false`**, a README naming the vendor and the terms, and a
rendered preview.

- **Confinement is mechanical, not a label.** Publishing a package whose
  `provenance.vendor.redistributable` is `false` into an index with
  `scope: "public"` is refused, naming the vendor and the index. Vendor-derived
  geometry belongs in personal and organisation indexes, and **legal review
  precedes any public seeding** of it.
- **Connector placement is not automated.** The tool reports the imported
  solid's own B-rep faces — planar with a normal and a centre, cylindrical
  with an axis and a radius — largest first, as *suggestions*, in the payload
  and in the generated README. An author turns them into a `connectors`
  function. Inferring which cylinder is "the shaft" from an unlabelled vendor
  solid is a research problem; PRD-032 is where it belongs.
- **STL is refused.** A mesh loads as one welded triangulation face with no
  surface — nothing to suggest connectors from — and its booleans segfault
  OCCT. Import it into a project instead.
- **`use_part` does not materialise a reference part**, and this is v1's one
  hole in FR13. The provenance header lives *inside the script* and a
  reference part has none, so a materialised one could carry no provenance at
  all. The package validates, publishes and installs; bring the cached file
  into a project with `import_cad_file`. The refusal says so and names the
  cached path.
- A vendor file above the **5 MB per-file ceiling** is refused with the
  number. The ceiling was not raised for this case: it is part of the format,
  every consumer enforces it on install, and a version published above it
  would be uninstallable by a client that pinned the old number.

---

## Authoring a package

1. `agentcad package validate ./pkg` — read `stages[].items[]`, fix, repeat.
   Every row names its subject, its file and, where it measured something, the
   measured value and the declared limit **as data**. That loop is what
   `validate_package` exists for as a *tool*: an agent runs the gate, reads the
   rows, edits and re-validates with no human in it.
2. Give every numeric parameter `min`, `max`, `unit` and `description`. The
   gate's "builds at every extreme" claim is vacuous without the bounds, so
   the `contract` stage fails without them.
3. Declare the corners the one-at-a-time sweep cannot reach **as presets**.
4. Declare `SPECS` — they are evaluated at *every* variant, so a wall check on
   the thinnest configuration is exactly the check a catalog wants.
5. Declare `connectors` — and remember the moving side of a mate must be
   **rigid**, so a part whose only connector is cylindrical cannot be mated
   onto anything by itself.
6. Write `docs/README.md` naming every part **id** (not just its label — the
   id is what `use_part` takes), and commit a rendered preview per part.

---

## Known limits, stated

- **The publish gate is not a security boundary.** Said above, said in every
  tool description, carried in every report as `note`, and copied into the
  header in your own repository.
- **Copy-in means you do not get fixes automatically.** `list_packages`
  reports `latest` and `stale`; there is no `update_package` yet.
- **`latest` is what the last index refresh knows.** `list_packages`
  deliberately does not fetch — a network call per project open is not a
  listing. `search_packages` is what refreshes.
- **The index digest carries no parameter `description`s**, so the Library
  dialog's description column is empty for catalog packages. The data is in
  the package; the *digest* the index publishes records name, type, range and
  unit only.
- **Semantic search is present and always off**: every result carries
  `semantic: false` with `semantic_reason: "no_embedding_provider"`, because
  this build registers none. Present-and-false so you can tell that keyword
  search is what you got.
- **The gate's build fan-out earns 1.40× on the real catalog** (3-worker pool,
  107 variants, five interleaved repetitions), which is *below* the 1.5× bar
  the implementation plan set for keeping it. About 44% of the build stage is
  inside kernel calls and the rest is per-variant work in the server process,
  so Amdahl's ceiling at three workers is 1.42×. `--jobs 1` is a first-class
  path and the report is identical either way. Do not expect `--jobs` to make
  validation three times faster.
- **The repository and the seed catalog are both Apache-2.0** (founder
  decision, Aug 2026): `LICENSE` at the repo root, `license = "Apache-2.0"`
  in `pyproject.toml`, and every catalog package's `package.json` declares
  the same. The format requires a licence per package; third-party packages
  may of course choose their own.

---

## See also

`docs/agent-api.md` (the seven tools and the two CLI commands) ·
`docs/user-guide.md` (the Library dialog) · `docs/architecture.md` (where the
subpackage sits) · `AGENTS.md` (the gotchas that will bite an implementer) ·
`docs/prd/completed/PRD-011-parts-library-registry.md`
