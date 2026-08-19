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
> your privileges** — since PRD-006 inside a deny-by-default confinement on
> macOS (the seatbelt profile), on Linux (Landlock + seccomp) and, since
> PRD-006b, on Windows (an AppContainer): writes only in your project roots and
> the worker's private temp dir, no network, capped memory and process count.
> Confinement bounds what a package's script may reach; it says nothing about
> whether the geometry is what you wanted. Install packages from indexes you
> trust, exactly as you would a pip package.
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

A preset **is** a configuration. One entry shape, and it is the schema a part
record's own configurations use, entry for entry — a family declared with
`set_part_configs` and a preset published here are the same object, validated
by the same function (see
[Configurations](agent-api.md#configurations) and
[architecture.md](architecture.md#configurations)):

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

`use_part` does **not** copy a package's presets into the part's `configs`:
it applies the chosen preset's parameters as ordinary overrides and records
the preset *name* in the provenance header, because a copied family would live
outside what that header attests to. Declare the family on your part
(`set_part_configs`) if you want one there.

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

A **configuration** — the same object a preset is, now living at
`parts.<id>.configs.<name>` (PRD-012) — merges the other way, and the contrast
is the argument:

| Key | Granularity |
|---|---|
| `packages.<name>` · `packages_lock.<name>` | per package, **entry atomic** (content-determined) |
| `parts.<id>.configs.<name>` | per name; entry add/remove, else field-wise ↓ |
| `…<name>.label` · `.description` | whole value |
| `…<name>.params.<param>` | **per parameter** (a set of independent values) |
| `parts.<id>.active_config` · `assembly.instances.<id>.config` | whole value (a selection) |

Half of a lock entry verifies against nothing, so it is atomic; a configuration
is a set of independent parameter values, so it merges per parameter. And
because a selection lives in a different key from the map it names, one branch
removing a configuration while the other selects it merges *clean* —
`manifest_merge.config_problems` reports that at the merge's validation pass
(a bound instance blocks, a stale `active_config` warns), exactly as
`package_problems` reports the requirement/lock hybrid.

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
| `unverified` | we did not look: no cache entry, a tampered one, a header with no block digest, or a header written by a newer format |

On a fresh clone with a cold cache, provenance reads **`unverified`**, not
`ok`. That is the honest answer: nothing was compared.

The header carries two digests. `script_sha256` covers the script **without**
the block, so a local edit to the geometry is detectable; `header_sha256`
covers the block's own payload and its comment lines, so an edit confined to
the header — deleting the security notice, rewriting `index` to name a
registry the part never came from — reads `modified` rather than `ok`. Both
are **integrity checks, not authentication**: there is no secret, so they
catch edits and not a determined forger. What binds the *bytes* is the content
id, and `use_part` verifies the cached tree against the id in
`packages_lock` — the git-tracked, reviewable number — before it copies
anything, then records the id it measured.

Two indexes can publish the same `name@version` with different bytes. Both
would verify against their own cache receipts, so the lock is the authority: a
cache holding a different tree than the lock names materialises **nothing**,
and the refusal prints both ids.

**A tree must also agree with its own name.** The index entry is a claim about
a tree and the tree's `package.json` is a claim about itself; installing and
materialising both check that the two match, so an index mapping `foo@1.0.0` at
a (perfectly verified) tree whose manifest says `bar@2.0.0` is refused rather
than installed under the wrong provenance.

**The cache receipt is versioned, and a partial one is not "fine".** The
offline path reconstructs a git-tracked lock entry out of the receipt, so a
receipt that cannot supply `index` and `source` would produce an offline
"success" writing `null`s into `packages` and `packages_lock` — an install no
online install would ever have written. Such a receipt reads `tampered`, and
the remedy is `add_package`, which re-verifies the tree against the index's
declared id and rewrites a current receipt.

**`use_part` is one or two undo steps, not one transaction.** Materialising is
`create_part` and — when a preset or `params` is applied — `set_params`, and
each is an ordinary guarded store write that snapshots history. So undo after a
`use_part` **with** overrides reverts the parameters and leaves the part at the
package's defaults; a second undo removes the part. Without overrides it is a
single step. A *failed* `use_part` writes nothing at all: the overrides are
checked against the part's own inspected PARAMS spec before anything is
created, so there is no transient part and nothing an undo could resurrect.
Composing the two writes into one snapshot needs a per-operation suppression
seam the history layer does not have (`history.in_restore` is process-global
and would drop a concurrent caller's snapshot), so it is recorded as a
follow-up rather than improvised.

`remove_package` **does not touch one script byte**, and it does not touch the
cache. The header lives inside the script and the script text is the rebuild
cache key, so rewriting headers to express a removal would re-key and rebuild
every materialised part.

---

## Indexes

| kind | what it is |
|---|---|
| `local` | a directory holding `index.json` and the published trees |
| `git` | a repository cloned to `~/.agentcad/indexes/<name>/` at a pinned ref — **a local index plus a fetch**. `subdir` says where the index lives *inside* the repo |
| `cloud` | reserved; lands with PRD-005-lite |

Configured in `~/.agentcad/config.json`, **in precedence order**:

```jsonc
{"indexes": [
  {"name": "my-org", "kind": "git", "url": "git@github.com:acme/cad.git", "ref": "main"},
  {"name": "agentcad-src", "kind": "git", "url": "https://github.com/…/agentcad.git",
   "ref": "main", "subdir": "catalog"},
  {"name": "vendor", "kind": "local", "path": "/Users/me/vendor-parts", "scope": "private"}
]}
```

**`subdir` — an index inside a bigger repository.** A git index was
`<checkout>/index.json` and nothing else, which serves a repo that is an index
*and only that*. Plenty of repositories ship an index **alongside their
source** — this one does: the catalog lives at `catalog/index.json` with the
application around it, so before `subdir` existed AgentCAD's own repository was
not usable as a git index, while the acceptance test said otherwise by copying
`catalog/*` to the root of a synthetic repo. `subdir` is validated as a
relative path inside the repository; the clone still goes to the repository
root, and only the index lookup moves.

The bundled `catalog/` registers itself as `agentcad-core` and is **appended
after** whatever you configured — a fallback that outranked your own
configuration would be a fallback you could not override. One consequence
worth knowing: a user index *named* `agentcad-core` **replaces the bundled one
entirely**, including as a publish target. That is the escape hatch (it is how
you shadow the shipped catalog); it is not a merge.

**A git index repository must not convert line endings.** The format is
content-addressed: every version advertises the sha256 tree digest of its
files, and git's eol conversion rewrites bytes at checkout — with the Windows
default (`core.autocrlf=true`) the checked-out tree no longer hashes to its
advertised id and **every install refuses**. The client pins
`-c core.autocrlf=false` on every git invocation, which protects the clone it
makes; the repository itself must carry a `.gitattributes` with `* -text` (as
this repository does) so that *contributors'* commits store the bytes the
publisher hashed, on every platform.

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

### An omitted argument does not overwrite a declared one

`add_package` with no `version_req` and no `index` means *"you did not say"*,
not *"anything, from anywhere"*. For a package the project already declares,
the declaration **is** the answer: the recorded `version_req` is the
requirement that gets resolved, and the recorded `index` is the index that
gets asked. (It used to write `"*"` and re-resolve, which silently widened a
deliberate `~1.0.0` pin, jumped the lock a major version, and flipped every
part materialised from it to `version_drift`.) Anything the call *did* move
comes back in `requirement_change`.

**A consequence worth knowing before it surprises you.** Because the pinned
index is now honoured, `add_package` on a project whose declared index is not
configured on this machine is a `not_found_error` naming the pin — **even when
the package is in the cache, and even offline**. That is deliberate: a pin is a
statement about *provenance*, and answering it from a different index's
download would quietly break the statement while appearing to succeed. Two
remedies, and the choice between them is the actual decision:

* **configure the index** (`~/.agentcad/config.json`) — you want that package
  from that source, which is what the project recorded; or
* **pass `index` explicitly** to re-pin, which rewrites the declaration and
  says so in `requirement_change`.

Removing the `index` key from `packages.<name>` by hand also works and means
"any configured index", but the explicit `index` argument is the reviewable
way to say it.

---

## Publishing

```bash
agentcad package validate <dir> [--strict] [--report PATH] [--work-dir DIR] [--budget S]
agentcad publish <dir> --index <name> [--work-dir DIR] [--budget S]
agentcad publish --yank <name>@<version> --index <name>
```

`validate` is **report-honest**; `publish` is **fail-closed**. Exit codes are
`agentcad check`'s: `0` green · `1` the package is wrong · `2` we could not
produce a verdict.

**`index.json` is a build product of the gate, never hand-edited.** Every
entry — the parts digest, the preset list, the preview paths, the
`gate: {status, exempt_skips, agentcad, build123d, report_id}` record — is
derived from the gate's own measurements at publish time.

**Publish re-derives the verdict from the report's own rows.** It does not read
`report["publishable"]`: it requires the report to carry every gate stage
exactly once, a summary that is the summary of its own rows, a status those
counts imply, and a `complete` run — then rules on the rows itself. That is
what makes the seam safe to hand a report a *different* process produced
(a cloud registry, PRD-005-lite), and it is the property this section used to
claim before it was true.

**What the gate measures is the package's inventory** — exactly the files the
content id covers, which is exactly what a consumer receives. A part declared
at a path the content id ignores (`*.tmp`, `*.pyc`, `__pycache__/`, …) is a red
`format` row rather than a proof of a script nobody gets, and a `parts/*.py`
that no part declares is a red row too: it ships, and no stage ever opened it.
The tree is copied into the gate's throwaway cell before the stages run, so the
content id in the report is the id of the bytes the stages read.

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

…plus two stage-level absences that are legitimate, each tied to the stage
that owns it: `presets:no_presets_declared` and `specs:not_declared` (no
SPECS). Everything else a skipped stage can mean — a budget that ran out, a
stage nobody selected, a renderer that was not there — is "we did not look",
which may not read as "publishable".

**A stage that produced no rows at all blocks too.** A `presets.json`
declaring an empty `presets` map measures exactly as much as no file, and it
has to *say* so: it reports the same `presets:no_presets_declared` skip rather
than an empty block nobody counts. A configuration that applies but does not
build is a **failure**, not a pass.

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
| `iso4014` | hex bolts, M4–M12 — ISO 4014 head heights on a **fully threaded** shank (the pinned bd_warehouse cannot build the partial thread; the package docs say where that matters) | `head_seat`, `axis` |
| `iso7380` | button-head screws, M3–M12 | `head_seat`, `axis` |
| `thread_insert` | heat-set inserts — the five ruthex sizes the pinned bd_warehouse ships (M2, M2.5, M3 short, M4 short, M6; no M5) | `seat`, `bore` |
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

### The public marketplace read (PRD-031a)

Any index whose operator-configured `scope` **and** whose document `scope` are
both `"public"` — the bundled `agentcad-core` is — is also served **anonymously**
over `/api/public/packages/…`: browse, search (`refresh`-free, network-free),
per-version metadata, the read-only part script, the digest param spec, shipped
previews, and a **listing customizer** that rebuilds a bounded variant and
exports STEP/STL/3MF. That customizer is the *only* anonymous route that reaches
the kernel, and it does so through PRD-007's containment reused verbatim — the
`pool_size-1` worker reservation, a per-IP `TokenBucket` + login gate shared with
`/s/`, `normalize_params` parity, the `paramclamp` clamp and the content-addressed
variant cache. A private index never surfaces (the dual `scope: public` filter),
and every private/nonexistent miss is one name-free 404. Add-to-library from the
marketplace is the ordinary authenticated `add_package` + `use_part` (or the
`market_install` tool) — the seeded catalog is a registry index with a web front,
not a second code path. See the [user guide](user-guide.md#browsing-the-catalog-the-marketplace)
and [agent API](agent-api.md#marketplace-catalog-prd-031a--the-anonymous-public-read).

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
- **The gate builds variants one at a time, and there is no `--jobs`.** The
  parallel path was measured against the bar its own plan pre-registered
  ("under 1.5× on a 3-worker pool, delete it") and missed it three times:
  1.08×, 1.40× and 1.17×, against an Amdahl ceiling of 1.42× (only ~44% of the
  build stage is inside kernel calls). It was also not reproducible — worker
  assignment goes through `hash(affinity)`, which Python randomises per
  process — and under `--budget` it changed the *verdict*: `jobs=1` and
  `jobs=4` disagreed on `complete` and therefore on `publishable`. It was
  deleted rather than defaulted off, and the report is byte-identical to what
  `--jobs 1` produced. Validation time is dominated by real geometry; budget
  for it.
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
