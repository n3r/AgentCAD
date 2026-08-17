# Branching version control — design

**Date:** 2026-08-09 · **Status:** approved for implementation ·
**PRD:** [PRD-001](../../prd/in-progress/PRD-001-branching-version-control.md)
**Scope:** the PRD's **MVP** section only (FR1–FR13). Phase 2/3 items are
listed under "Out of scope" and must not creep in.

## Problem

A project's history today is one linear chain of git commits in
`<project>/.history`, used only for undo: `ProjectHistory.snapshot` commits on
every `project_changed` publish, `restore` overlays an old commit and appends a
`restore <id>` commit, and `UndoCursor` walks that chain
(`agentcad/core/history.py`). There is exactly one ref (`refs/heads/master` —
whatever `git init` chose), one working tree (the project directory), and no
way to fork it. So there is no way to try a risky change in isolation, keep two
design directions alive, name "the version we sent to the machine shop", or
combine two people's / two agents' work. Every v4 collaboration feature
(proposals, review, CI, releases) needs branches and immutable named versions
underneath.

The strategic point (market_research.md, "Onshape & cloud-native CAD"): Onshape
merges per-tab with three fixed strategies and "the from workspace wins" on
conflict, because its deltas are binary database operations. Our parts are
Python files and our manifest is JSON — both merge, diff, and conflict
*meaningfully*. A real three-way merge with real conflicts is the thing
incumbents structurally cannot ship.

## Goals

- G1. Multiple named branches per project, cheap to create, **O(1) to switch**.
- G2. Immutable named versions (tags), restorable and referenceable forever.
- G3. Merges surface conflicts: scripts as standard git conflict markers,
  `project.json` **key-wise** (`parts.<id>`, `assembly.instances.<id>`,
  `materials.<id>`, per-part `pmi`, scalars) — never line-wise JSON garbage,
  never silent last-writer-wins.
- G4. A merge is complete only when the kernel revalidates the result.
- G5. Equally usable from the UI, the tools, and raw `git` — the project stays
  a plain git repository a power user can clone.

## Non-goals / out of scope for this spec

- Proposal & review workflow (PRD-002) — this is the substrate only.
- Dual-viewport geometry compare in the conflict view (PRD Phase 2). MVP ships
  a conflict **list** with pick-ours / pick-theirs / edit-by-hand.
- Pre-merge summary prediction (Phase 2), rebase/squash/history rewriting,
  cherry-pick, remotes/sync (PRD-005), multi-project versioning (PRD-011).
- Semantic review of key-wise-mergeable-but-semantically-wrong edits (e.g. both
  sides renaming solids) — the validation pass is the backstop; real review
  belongs to PRD-002.
- `git gc` housekeeping for history bloat (measure first; PRD risk list).

---

## Architecture at a glance

```
                    tools_versioning.py            routes_versioning.py
                    (branch_* / version_* /        (/api/projects/{p}/branches,
                     merge_* / resolve_merge)       …/versions, …/merge)
                              │                              │
                              └───────────┬──────────────────┘
                                          ▼
   service.branches ──────────► BranchManager            (core/branches.py)
   (seam installed by the pack)   • per-(project,client) current branch
                                  • worktree materialization
                                  • ref/tag operations
                                  • installs store.branch_resolver
                                          │
                    ┌─────────────────────┼──────────────────────┐
                    ▼                     ▼                      ▼
       MergeOrchestrator          ProjectHistory          ProjectStore
        (core/merge.py)          (core/history.py)      (core/project.py)
        • git merge-tree          • worktree-aware        • _resolve() goes
        • merge_manifests           _locate()               through the
          (core/manifest_merge.py) • branch-keyed           branch resolver
        • staged temp worktree      UndoCursor            • canonical_path_of()
        • validation pass                                   for .cache/
        • merge commit (2 parents)
```

Three new core modules (`branches.py`, `merge.py`, `manifest_merge.py`), one
tool pack, one route pack, two frontend modules. Edits to existing core files
are confined to seams: `project.py` (the resolver + `canonical_path_of` +
`lock_key`), `history.py` (`_locate`, ref primitives, branch-keyed
`UndoCursor`), `tools_locks.py` (three one-line key changes), and **exactly one
three-line change in `service.py`** — the reference-part cache signature (see
Decision 5). **`worker.py`, `tools.py` and `app.py` are not touched at all.**
The kernel is untouched — no new geometry code, so nothing new imports
`OCP`/build123d.

---

## Decision 1 — working trees: **worktree per non-default branch**

**Chosen:** the default branch keeps the project directory as its working tree
(exactly as today); every *other* branch gets a linked git worktree at
`<project>/.history/trees/<branch>/`. Switching a client's branch is a pointer
update in memory — no checkout, no file movement.

**Layout**

```
myproject/                       ← working tree of the DEFAULT branch (master)
  project.json                     (unchanged from today)
  parts/*.py
  imports/*
  exports/                       ← derived, untracked, per working tree
  .cache/                        ← derived, untracked, SHARED (canonical only)
  .history/                      ← GIT_DIR (unchanged)
    HEAD, refs/heads/*, refs/tags/*, objects/…
    info/exclude                 ← ".cache/\nexports/\n.history/\n*.tmp\n"
    worktrees/<name>/            ← git's own per-worktree admin dirs
    trees/<branch>/              ← OUR checkouts (working trees)
      .git                       ← file: "gitdir: …/.history/worktrees/<name>"
      project.json, parts/, imports/, exports/
    agentcad/                    ← our sidecar state (never committed)
      config.json                ← {"default_branch": "master"}
      checkouts.json             ← {"<client-id>": "<branch>"}
      tags.json                  ← {"<tag>": {"referrers": []}}
      merge.json                 ← staged merge state (absent when none)
      merge-<id>/                ← staged merge worktree (detached HEAD)
```

**Why worktrees, not checkout-on-switch.** FR2 requires two agents on two
branches of one project working *concurrently*. One working tree cannot do
that: any `git checkout` would yank the other agent's files out from under an
in-flight rebuild. Worktrees also make FR3 trivially true — switching is O(1),
not O(working set), because the tree for a branch already exists.

**Why the default branch keeps the project directory.** Every existing project,
every existing test, and every path in `docs/` assumes
`store.path_of(proj) == <projects>/<name>`. Keeping the default branch there
means an unbranched project is **byte-identical on disk to today**, the
migration is a no-op, and all 319 existing tests keep passing without edits.
The asymmetry is the point.

**Why `.history/trees/`, not the PRD's `.history/worktrees/`.** `git worktree
add` puts its own admin data in `$GIT_DIR/worktrees/<name>/` — so
`.history/worktrees/` is *already reserved by git*. Verified empirically:

```
$ git --git-dir=P/.history --work-tree=P worktree add P/.history/trees/feat feat
$ ls P/.history/worktrees/     → feat            (git's admin dir)
$ cat P/.history/trees/feat/.git → gitdir: …/P/.history/worktrees/feat
$ git --git-dir=P/.history/worktrees/feat --work-tree=P/.history/trees/feat \
      branch --show-current    → feat
```
So a linked worktree is driven with `--git-dir=<admin dir> --work-tree=<tree>`,
which is exactly the shape `ProjectHistory._run` already builds. **PRD
divergence to fold back: `.history/worktrees/` → `.history/trees/`.**

**Why nesting the trees under `.history/` is safe.** `.history/info/exclude`
already contains `.history/`, in *every existing project*, so `git add -A` in
the main working tree cannot pick up `.history/trees/**`. Any other location
(e.g. `<project>/.branches/`) would need a new exclude entry, and
`_ensure_repo` only writes `info/exclude` at init — existing repos would start
committing their own branch checkouts. (Defensively, `_ensure_repo` will now
rewrite `info/exclude` whenever its content differs, so the file self-heals.)

**Costs, accepted:** each branch costs one checkout of authored state
(`project.json` + `parts/*.py` + `imports/*`) — KBs to a few MB; the engine
example (33 parts) is ~400 KB of scripts. `.cache/` is *not* duplicated (see
Decision 5), which is where the real bytes are.

**Rejected: checkout-on-switch.** Simpler (no new paths), but fails FR2
outright, makes switching O(working set), and creates a window where a rebuild
in flight reads half of branch A and half of branch B.

## Decision 2 — ref resolution keyed by client identity

`ProjectStore` gains one seam, defaulting to today's behavior:

```python
# ProjectStore.__init__
self.branch_resolver: Callable[[str, Path], Path] | None = None

def canonical_path_of(self, proj: str) -> Path:      # the project directory
    return self._locate(proj)                        # today's _resolve body

def path_of(self, proj: str) -> Path:                # the CALLER'S working tree
    canonical = self._locate(proj)
    if self.branch_resolver is None:
        return canonical
    return self.branch_resolver(proj, canonical)

def lock_key(self, proj: str) -> str:                # turn-lock / undo key
    return proj if self.branch_resolver is None else str(self.path_of(proj))
```

Everything that reads or writes authored state already goes through
`_resolve` → so `manifest`, `save_manifest`, `script_path`, `read_script`,
`write_script`, `add_part`, `remove_part`, `set_instances`, `exports_dir`,
`imports_dir` become branch-aware for free. `cache_dir` is repointed at
`canonical_path_of` (Decision 5). `list_projects` maps each discovered
canonical path through the resolver so the part counts a client sees are its
own branch's.

`BranchManager.resolve_path(proj, canonical)` resolves in this order:

1. **Pinned tree** — `pinned_tree_var` (a `ContextVar[Path | None]`). Used by
   the merge validation pass to run ordinary service calls against a staged
   worktree (Decision 6). Highest precedence, always explicit.
2. **This client's checked-out branch** — `self._checkouts[proj][client_id]`,
   where `client_id = locks.current_client_id()`. Same ContextVar turn-locking
   already stamps (`agentcad/core/locks.py:26`), so identity flows from the
   `X-Agent-Id` middleware (`browser`, `mcp`, `alice`), the chat executor
   (`chat` / `chat:<session>`), and the `local` library default with no new
   plumbing. It propagates into sync FastAPI endpoints through anyio's
   threadpool context copy — already asserted by `tests/test_locks.py:260`.
3. **The default branch** → the canonical project directory.

`self._checkouts` is loaded from and written to
`.history/agentcad/checkouts.json` (atomic write, inside `GIT_DIR`, never
committed) so the browser stays on its branch across a server restart. A
checkout entry whose branch no longer exists is dropped on load.

**Turn locking per branch (FR2).** `TurnLock` stays keyed by an opaque string;
the *key* becomes `store.lock_key(proj)` — the resolved working-tree path,
which is `<projects>/<name>` on the default branch (so all existing lock tests
and behavior are bit-identical) and the worktree path elsewhere. The versioning
pack rewires the seam the same way `tools_materials.py` rewires
`service.materials`:

```python
service.store.write_guard = lambda proj: service.turnlock.check(
    service.store.lock_key(proj), locks.current_client_id()
)
```
`tools_locks.py`'s three tools switch from `project` to
`service.store.lock_key(project)` (default implementation returns `project`, so
they behave identically when the versioning pack is absent).

**Undo per branch (FR11).** `UndoCursor`'s per-project stacks are re-keyed by
`store.lock_key(proj)` instead of `proj`. On the default branch the key is a
stable string, so semantics are unchanged; on a branch the client gets its own
undo/redo stacks over that branch's linear history. `UndoCursor._step` already
calls `self.store.path_of(proj)` and hands it to `ProjectHistory`, which is
worktree-aware after Decision 3 — so undo/redo work per-branch with no other
change.

## Decision 3 — `ProjectHistory` learns where the git dir is

One private helper makes every existing method work on a linked worktree:

```python
@staticmethod
def _locate(project_path: Path) -> tuple[Path, Path]:
    """(git_dir, home) for a working tree — the main tree or a linked one."""
    dotgit = project_path / ".git"
    if dotgit.is_file():                     # linked worktree
        admin = Path(dotgit.read_text(encoding="utf-8")
                     .split("gitdir:", 1)[1].strip())
        return admin, admin                  # HOME=admin keeps git hermetic
    return project_path / ".history", project_path / ".history"
```

`_run` uses it for `--git-dir` and for the `HOME`/`XDG_CONFIG_HOME` hermetic
env; `_has_repo` also accepts a `.git` *file*. Every current call site passes
the project directory and gets today's exact command line. `snapshot`, `log`,
`restore`, `head`, `parent_of`, `has_commit` then all operate on whatever
working tree they are handed — which is what `_snapshot_on_event` passes
(`store.path_of(proj)`, now branch-resolved). **The snapshot-on-mutation hook
therefore commits to the mutating client's branch with zero changes to
`service.py`.**

`ProjectHistory` also grows the ref-aware primitives the branch layer needs,
all guarded by a strict ref regex so a ref name can never be read as a git
option or a revision expression (the same defense as `_COMMIT_RE`):

```python
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,63}$")   # + no "..", no "/…",
                                                        # no ".lock", no "@{"
resolve_ref(path, ref) -> str | None     # rev-parse --verify <ref>^{commit}
log(path, limit, ref=None)               # git log [<ref>]
branches(path) -> list[dict]             # for-each-ref refs/heads
tags(path) -> list[dict]                 # for-each-ref refs/tags
```

`_COMMIT_RE` stays as-is for raw commit ids; `_REF_RE` is the *additional*
accepted shape for `project_history {ref}` / `project_restore {ref}`, which
resolve the ref to a commit before touching anything.

## Decision 4 — the manifest merge driver

`agentcad/core/manifest_merge.py`, pure Python, no I/O, no git:

```python
def merge_manifests(base: dict, ours: dict, theirs: dict)
        -> tuple[dict, list[dict]]:
    """Three-way merge of project.json at CAD key granularity.

    ours = the TARGET branch (what you merge into), theirs = the SOURCE.
    Returns (merged manifest, conflicts). The merged manifest always contains
    ours' value at every conflicted key, so it is a valid document even while
    conflicts are outstanding — resolution overwrites those keys.
    """
```

**Key space.** Every leaf below is merged independently:

| Manifest location | Key | Granularity |
|---|---|---|
| top-level scalars | `schema_version`, `name`, `units` | whole value |
| `parts[]` (list keyed by `id`) | `parts.<id>` | entry add/remove; else field-wise ↓ |
| part fields | `parts.<id>.label` · `.material` · `.kind` · `.source` | whole value |
| part params | `parts.<id>.params.<name>` | per parameter |
| per-solid materials | `parts.<id>.solid_materials.<key>` | per key |
| part PMI | `parts.<id>.pmi` | **whole section** (atomic) |
| `configs` (dict, PRD-012) | `parts.<id>.configs.<name>` | entry add/remove; else field-wise ↓ |
| configuration fields | `…<name>.label` · `.description` | whole value |
| configuration params | `…<name>.params.<param>` | per parameter |
| active configuration | `parts.<id>.active_config` | whole value (a selection) |
| `assembly.instances[]` (keyed by `id`) | `assembly.instances.<id>` | entry add/remove; else field-wise ↓ |
| instance fields | `…<id>.part` · `.position` · `.rotation_deg` · `.color` · `.mate` · `.config` | whole value |
| `materials` (dict) | `materials.<id>` | **whole entry** (atomic) |
| any other top-level key | `<key>` | whole value (forward compatible) |

A configuration is not atomic *because* a `materials.<id>` entry is: a material
is one physical fact and half of one is over-clever, while a configuration is a
set of **independent parameter values** — the same argument that makes
`parts.<id>.params` merge per key, one level deeper (PRD-012 FR12: two branches
adding two different configurations of one part must merge clean). A map entry
that is not an object (a hand edit, an authored null) merges whole all the
same. The two *selections* — `active_config` and an instance's `config` — are
single values in a different key from the map they name, so a clean key-wise
merge can leave one of them naming nothing: `manifest_merge.config_problems`
reports that at the validation pass (a bound instance blocks, a stale
`active_config` warns), the way `package_problems` reports the packages/lock
hybrid.

**Per-key rule** (classic three-way):

| base | ours | theirs | result |
|---|---|---|---|
| any | X | X | X (clean — both changed identically) |
| B | B | T | T |
| B | O | B | O |
| B | O | T (O≠T≠B) | **conflict** |
| absent | absent | T | T (add) |
| absent | O | T (O≠T) | **conflict** (add/add) |
| B | deleted | deleted | deleted |
| B | deleted | B | deleted |
| B | deleted | T (T≠B) | **conflict** (delete/modify, key = the entry) |

Values are compared by structural equality after JSON round-trip
normalization (`json.dumps(sort_keys=True)`), so `6` and `6.0` are *not* equal
— matching how `_normalize_param` stores ints for `int`/`enum` params and
floats otherwise, and how a byte-comparison of `project.json` would see it.

**Deterministic ordering.** `parts` and `assembly.instances` come back in
*ours*' order for entries present in ours, then theirs-only additions in
theirs' relative order. Deletions drop out. This is stable and reproducible,
which matters because the merged `project.json` is committed and AC5 asserts a
byte-identical tag round-trip.

**Explicit non-merges.**
- `position` / `rotation_deg` are merged as a *whole vector*, never
  component-wise: merging X from one side and Z from the other yields a
  placement nobody authored.
- `pmi` is atomic per part. PMI is a nested list-of-frames document whose ids
  cross-reference each other (`fcf.datums` → `datums.id`,
  `agentcad/core/pmi.py`); a key-wise merge could produce a frame referencing a
  datum the other side deleted. PRD scopes PMI edge cases to Phase 2 — atomic
  is the honest MVP.
- `materials.<id>` is atomic per material: an entry is a small property bundle
  where merging one side's `density_g_cm3` with the other's `yield_mpa` is
  over-clever.

**Referential integrity is not the driver's job.** A clean key-wise merge can
still delete `parts.flange` on one side while the other adds an instance
referencing it. The driver does not conflict on that (neither side edited the
same key); the **validation pass** reports it as an integrity failure
(Decision 6). Documented, and covered by a test.

## Decision 5 — mesh cache and kernel affinity across branches

**`.cache/` is shared, at the canonical project root.** `ProjectStore.cache_dir`
uses `canonical_path_of`. The cache key is
`sha256(content, params, density, tolerance, format)`
(`service._cache_key`) — content-addressed, so two branches with the same
script+params land on the same `<key>.acm` and the second branch's build is a
disk-cache hit with **zero kernel work** (FR3, FR13). Nothing in the cache path
knows or needs to know about branches. Branch switching costs no rebuild for
unchanged parts, and the merge validation pass reuses both sides' meshes.

`exports/` and `imports/` follow the *working tree*: `exports/` because a
branch's outputs shouldn't clobber another's, `imports/` because it is
**tracked** content that git checks out per worktree.

**Consequence — one required `service.py` fix.** `_content_signature` keys a
reference part's cache entry on `path + st_mtime_ns + st_size`. A worktree
checkout stamps a fresh mtime, so the same imported STEP file would get a
different cache key on every branch, breaking FR13 for reference parts (and
already causing spurious rebuilds after any restore). It becomes a content
hash:

```python
return f"ref:{record.source}:sha256:{hashlib.sha256(src.read_bytes()).hexdigest()}"
```
This is a one-time cache invalidation for existing reference parts (they
rebuild once) and is strictly more correct.

**Kernel affinity is left alone — a deliberate PRD divergence.** The PRD's risk
list says "the kernel pool's affinity keys must incorporate branch to avoid
cross-branch shape-LRU pollution". Reading the code says otherwise:
`pool._pick` routes by `hash(affinity) % size` (`agentcad/kernel/pool.py:56`)
and the thing it protects is `worker._SHAPE_CACHE`, a **16-entry LRU keyed by
`_shape_key(script, values)`** (`worker.py:30`, `:302`) — i.e. content-keyed,
not part-keyed. So:
- there is no correctness issue: a "wrong" worker rebuilds, never mis-serves;
- adding the branch to the affinity key would *reduce* reuse in the common
  case, where two branches share identical content for most parts and want the
  same warm worker;
- the only real effect of branches is that a part with two different scripts
  occupies two of one worker's 16 LRU slots. Negligible.

`affinity=part_id` therefore stays as-is. **Fold back into the PRD.**

**Mid-rebuild branch switch: drain, do not cancel.** A switch changes only the
resolver's pointer, so an in-flight kernel request keeps running against the
script text it was already handed and writes its result into the shared,
content-addressed `.cache/` — the work is never wasted, and it is a cache hit
next time that content is viewed on *any* branch. The two things that could go
stale are both self-healing:
- `service._status[(proj, part_id)]` is keyed without a branch, so a build on
  branch B overwrites branch A's entry. `_ensure_built` re-derives
  `_cache_key_for` from the (branch-resolved) store and compares — a mismatch
  falls into `_rebuild`, which hits the disk cache and returns
  `{"cached": true}`. Same self-healing argument the restore path already
  relies on (`tools_history.py:68`). No change, documented.
- a late `rebuild_finished` for the old branch reaches the UI, which reloads
  that part — re-reading the now-branch-correct state. At most one redundant
  fetch.

Cancellation was rejected: it would need per-request cancellation in the JSON-RPC
worker protocol (which has none), and would throw away work that the shared
cache makes valuable regardless of branch.

## Decision 6 — merge orchestration

`agentcad/core/merge.py`, `MergeOrchestrator`. Nine steps.

**1. Preconditions.** Resolve `source` and `target` (default: the caller's
current branch is the target). Take the target's turn-lock check
(`store.write_guard` under `lock_key(proj, target)`). Require both working
trees clean (`git status --porcelain` empty) — snapshot first if not, since
every mutation is supposed to have snapshotted already. A dirty tree that
survives a snapshot is a `conflict_error` ("branch has uncommitted changes").
Require `git merge-tree --write-tree` support (**git ≥ 2.38**); otherwise a
`validation_error` naming the requirement. Refuse if a staged merge already
exists for a *different* (source, target) pair (`conflict_error`, "abort the
staged merge first").

**2. Merge base.** `git merge-base <target> <source>`. No base ⇒ unrelated
histories ⇒ `validation_error`.

**3. Fast-forward.** `base == target-head` ⇒ fast-forward: move
`refs/heads/<target>` to the source head, hard-update the target's working
tree, publish `project_changed` + `merge_completed`, return
`{"fast_forward": true, …}` — **no validation pass** (the source state was
already validated as it was built, and the result is byte-identical to a state
that already exists). `base == source-head` ⇒ `{"already_up_to_date": true}`,
a no-op.

**4. Three-way tree merge.** `git merge-tree --write-tree -z <target> <source>`
(exit 0 clean / 1 conflicts / >1 error). Verified output shape:

```
<merged-tree-oid>\0
100644 <oid> 1\tparts/flange.py\0     ← stage 1 = base   (absent on add/add)
100644 <oid> 2\tparts/flange.py\0     ← stage 2 = OURS   = target
100644 <oid> 3\tparts/flange.py\0     ← stage 3 = THEIRS = source
\0                                     ← end of conflicted-file section
<informational messages…>
```
For each conflicted **script** path, `git cat-file blob` the three stages and
build the marked text with
`git merge-file --diff3 -L "<target>" -L "base" -L "<source>" -p`.

**5. `project.json` is always re-merged by us.** Whether or not git flagged it,
the textual result is discarded and `merge_manifests(base, ours, theirs)` runs
on the three parsed manifests. Line-wise JSON merges are exactly the "JSON
garbage" G3 forbids, and a *clean* line-wise merge is the more dangerous case.
This is what "keeping the store in control" means in the PRD.

**6. Stage.** `git worktree add --detach .history/agentcad/merge-<id> <target>`,
then materialize the merged tree into it (`GIT_INDEX_FILE=<tmp>
git read-tree <merged-tree>` + `git checkout-index -a -f`), then overwrite
`project.json` with the driver's merged manifest and each conflicted script
with its marked text. Persist `.history/agentcad/merge.json`:

```json
{"id": "…", "source": "feat", "target": "master", "base": "<sha>",
 "target_head": "<sha>", "source_head": "<sha>", "tree": "<sha>",
 "dir": "…/merge-<id>", "by": "chat:main", "created": "…",
 "conflicts": [ … ], "resolved": { "<path-or-key>": … }}
```

**7. Conflicts ⇒ return, never partially apply (FR7).** Nothing outside
`.history/agentcad/` has been touched; no ref moved. The tool returns the
`merge_conflict` payload (below). `resolve_merge {choices}` records choices
into the staged state and re-materializes the affected files; when nothing is
outstanding it falls through to step 8 automatically (and re-calling
`merge_branch` with the same pair does the same, satisfying the PRD's "re-runs
the merge"). `merge_abort` removes the staged worktree
(`git worktree remove --force`) and `merge.json`.

**8. Validation pass (FR9)** — runs *inside* the staged worktree by pinning the
resolver:

```python
with branches.pinned(proj, staged_dir):
    for part_id in changed_parts:              # vs the target tree
        service.get_metrics(proj, part_id)     # → _ensure_built → kernel
    service._resolved_instances(proj)          # re-resolves mates, rejects cycles
    after = service.check_interference(proj)
```
Because `.cache/` stays canonical, unchanged parts are cache hits and changed
parts usually hit the source branch's already-built entry — the pass is mostly
free. Report:

```json
{"ok": false, "blocked": true,
 "built": [{"part": "flange", "cached": true}],
 "failures": [{"part": "nozzle", "error": {"type": "script_error", …}}],
 "integrity": [{"kind": "dangling_instance", "instance": "flange_1",
                "part": "flange"}],
 "interference": {"checked": 3, "new_pairs": [{"a": "flange_1",
                  "b": "nozzle_1", "volume_mm3": 812.4}], "skipped": null}}
```
- `integrity` covers instances referencing a missing part and mates pointing at
  a missing instance — the structural damage a clean key-wise merge can do.
- Interference **blocks only on *new* pairs**: the same check runs on the
  pre-merge target and the pair sets are diffed, so a project that already
  overlaps stays mergeable while a merge that *introduces* an overlap is
  blocked (AC4's wording: "would introduce assembly interference"). The check
  is skipped with `"skipped": "instances"` below 2 instances and above
  `MERGE_INTERFERENCE_MAX_INSTANCES = 40` (pair count is quadratic; the engine
  example's 65 instances is 2 080 boolean intersections).
- `blocked = not ok and not allow_invalid`. Blocked ⇒ the staged merge stays on
  disk and a `validation_error` carrying the report is returned (so the caller
  can retry with `allow_invalid: true` without redoing the merge).

**9. Commit and publish (FR10).**
`git commit-tree <final-tree> -p <target_head> -p <source_head>` with message:

```
merge <source> into <target>

Merged-by: chat:main
Conflicts-resolved: 2
Validation: FAILED (allow_invalid) — interference flange_1↔nozzle_1 812.4 mm^3
```
Then compare-and-swap `refs/heads/<target>` with
`git update-ref refs/heads/<target> <new> <target_head>` — a concurrent commit
on the target since staging fails the CAS and returns `conflict_error`, which
is the whole point of recording `target_head`. Then fast-forward the target's
working tree (`git -C <target tree> merge --ff-only <merge>`; guaranteed to
succeed because we hold the lock and verified cleanliness), remove the staged
worktree and `merge.json`, register the merge with the UndoCursor
(`undo_cursor.on_snapshot(proj, merge_commit, "merge <source> into <target>")`
under the target's key — so Cmd+Z restores the pre-merge target), and publish:

```json
{"type": "project_changed", "project": "p", "reason": "merge"}
{"type": "merge_completed", "project": "p", "source": "feat",
 "target": "master", "commit": "<sha>", "validation": { … }}
```
The `project_changed` publish reaches `_snapshot_on_event`, which runs
`git add -A` on a clean tree and returns `None` — harmless by construction.

## Decision 7 — branches and tags

**Names (FR1).** `^[a-z0-9][a-z0-9_/-]{0,63}$`, plus rejects `..`, a trailing
`/`, `.lock`, `@{`, and any name git refuses (`git check-ref-format`). A name
that is a directory prefix of an existing ref (`feat` when `feat/x` exists)
surfaces git's refusal as a `validation_error`.

- `branch_create {project, name, from?}` — `from` defaults to the caller's
  current branch; accepts a branch, a tag, or a commit id. `git branch <name>
  <from>` then `git worktree add .history/trees/<sanitized> <name>` (the
  directory name replaces `/` with `-` and is disambiguated on collision; the
  mapping is recorded in `checkouts.json`'s sibling `trees` map). Returns the
  branch list. **Does not** switch the caller.
- `branch_list {project}` — `{branches: [{name, head, ts, message, is_default,
  is_current, checked_out_by: [client…]}], current, default, you}`.
- `branch_switch {project, name}` — snapshots the caller's current tree
  ("checkpoint before switch", FR3's clean boundary), updates the caller's
  entry in `checkouts.json`, publishes `branch_changed {project, client,
  branch}`, returns the post-state `get_project` payload (mutating ops return
  post-state).
- `branch_delete {project, name}` — `validation_error` when `name` is the
  default branch or is the current branch **of any client**; otherwise
  `git worktree remove --force` + `git branch -D`. Tags on the deleted branch's
  commits survive (they are independent refs) — AC5.

**Tags (FR4, FR5).** `version_tag {project, name, message?}` creates an
annotated tag (`git tag -a`) at the caller's branch head, and records
`{"referrers": []}` in `.history/agentcad/tags.json`. Moving an existing tag is
a `conflict_error`; **there is no delete tool in MVP**, so FR5 ("cannot be
deleted or moved once referenced") holds trivially and the `referrers` field is
already there for PRD-015. `list_versions {project}` →
`[{name, commit, ts, author, message, referrers}]` newest-first.

**`ref` on the history tools (FR4).** `project_history {project, limit, ref?}`
and `project_restore {project, commit}` — the latter's `commit` argument now
also accepts a branch or tag name (resolved through `resolve_ref` before
anything is written), and `project_history` gains `ref` for reading another
branch's or a tag's history without switching. Restore still applies to the
**caller's current working tree** and still appends a linear `restore <id>`
commit on the caller's branch: undo semantics are unchanged per-branch (FR11).

## Decision 8 — migration

There is nothing to migrate. On the first branch/tag/merge call for a project:

1. `_ensure_repo` runs (creating `.history` and an initial snapshot if the
   project has never been mutated), and refreshes `info/exclude` if stale.
2. The default branch is **discovered, never assumed**:
   `git symbolic-ref --short HEAD` → `master` for every repo created by the
   current `_ensure_repo` (verified: hermetic `git init` with no user config
   yields `refs/heads/master`). It is written once to
   `.history/agentcad/config.json` so a later git default-branch change cannot
   silently re-point an existing project.
3. Existing linear history *is* that branch's history — no rewrite, no rewind.

A project that never uses branching is byte-identical on disk to today, and
`git log`, `git diff`, `git clone`, `git worktree list` on `.history` all see
true branches and tags (FR12). Derived data stays untracked (FR12) — the
existing `info/exclude` already covers `.cache/`, `exports/`, `.history/`.

## Decision 9 — UI (MVP)

The frontend recon is exact about what exists: one hand-rolled modal
(`#drawing-modal` in `drawings.js`), one reusable dropdown pattern
(`.menu-wrap > button.tb-btn[aria-haspopup] + .menu.hidden[role=menu]`), one
`toast()`, one CodeMirror singleton in `editor.js`, and `setupMenus()` that
**snapshots `.menu-wrap` elements once at boot** — so new menus must be static
markup in `index.html`.

- **Toolbar branch switcher** — a second `.menu-wrap` immediately after the
  project switcher (`index.html` L34), before `.toolbar-spacer`:
  `#branch-btn` (`⌥ <branch> ▾`, `max-width` like `#project-btn`) +
  `#branch-menu.menu.left.hidden`. Populated on open exactly like
  `setupProjectMenu()`: one `.menu-item` per branch (`.active` for the current,
  `span.meta` = relative time of its head commit), a `.menu-sep`, then
  "New branch…", "Merge into…", "Versions…". "New branch…" reuses the existing
  `window.prompt` + regex-validate + `toast(…, "error")` shape of
  `newProjectPrompt()`.
- **Switching** calls `api.switchBranch` then runs the same context reset as
  `loadProject()` (clear `meshBuffers`, `viewport.clear()`, face selection,
  `lastFittedTarget = null`, then `refreshProject()`), guarded by
  `confirmDiscardEdits(null)` because a branch's scripts differ from the
  editor's buffer.
- **Versions dialog** — `#versions-modal.modal-overlay.hidden` next to
  `#drawing-modal`, wired with the same three handlers `drawings.js` uses
  (close button, backdrop click, Escape). A list of tags (name, date, message,
  commit) each with **Restore** (→ `project_restore {commit: <tag>}`) and a
  "Tag current state…" button at the top. `.modal-head #drawing-title` is
  generalized to a `.modal-title` class so both modals can use it.
- **Conflict list (MVP scope)** — `#merge-modal.modal-overlay.hidden`: a left
  rail of conflicts (`kind` icon + `path`/`key`), and for the selected one
  either (scripts) a **read-only CodeMirror** created directly from the global
  `window.CodeMirror` (`{readOnly: "nocursor", mode: "python", theme:
  "agentcad", lineWrapping: false}`, `cm.refresh()` after un-hiding) showing
  the conflict-marked text, or (manifest keys) a three-column
  base / ours / theirs value display. Per conflict: **Use ours** ·
  **Use theirs** · **Edit…** (makes the CodeMirror writable and sends its text
  as `content`). A footer shows "N of M resolved" and a **Complete merge**
  button (disabled until 0 outstanding) plus **Abort**. `merge.js` is a new
  module; it does not touch `editor.js`'s singleton.
  Explicitly **not** in MVP: dual-viewport geometry compare.
- **Events.** `handleEvent()` gains two cases, both guarded by
  `ev.project !== state.projectName` like every other project-scoped event:
  `branch_changed` (refresh the label always; do the full context reset only
  when `ev.client === state.clientId`, which comes from `branch_list`'s `you`)
  and `merge_completed` (toast + `refreshProject()`; if the validation report
  is not `ok`, an error toast naming the failure). Unknown types already fall
  through `default: return`, so an old client against a new server is a no-op.
- **z-index / toasts.** `.modal-overlay` is 80 and `#toasts` is 60, so merge
  toasts would render *behind* the merge modal. `#toasts` moves to 90 (it is
  transient, non-interactive, and top-anchored).
- **Keys.** `setupKeys()`'s bare-key shortcuts (`f`, `g`, `r`) are not
  modal-aware. A `modalOpen()` guard is added next to the existing `inField`
  check, so `f` does not fit the viewport behind an open dialog.
- **CSS.** New `branch-*`, `ver-*`, `conflict-*` classes in `app.css`, colors
  strictly from the existing tokens (`--panel`, `--hairline`, `--dim`,
  `--accent*`, `--err*`, `--scrim`, `--shadow-modal`) so light mode keeps
  working; a `.modal.narrow { width: min(560px, 100%) }` variant for Versions.
  A conflicted state reuses `--err-*`; no new token is introduced.

---

## Surfaces

### Tools (`agentcad/core/tools_versioning.py`)

| Tool | Schema | Returns |
|---|---|---|
| `branch_create` | `{project*, name*, from?}` | branch list + `created` |
| `branch_list` | `{project*}` | `{branches[], current, default, you}` |
| `branch_switch` | `{project*, name*}` | `{branch, project: <get_project>}` |
| `branch_delete` | `{project*, name*}` | branch list + `deleted` |
| `version_tag` | `{project*, name*, message?}` | `{tag, commit, versions[]}` |
| `list_versions` | `{project*}` | `{versions: [{name, commit, ts, author, message, referrers}]}` |
| `merge_branch` | `{project*, source*, target?, allow_invalid?}` | merge result, or `merge_conflict`, or `validation_error` (blocked) |
| `resolve_merge` | `{project*, choices*}` | remaining conflicts, or the merge result when it completes |
| `merge_abort` | `{project*}` | `{aborted: true, source, target}` |
| `merge_status` | `{project*}` | staged merge state or `{merge: null}` (UI re-entry; not in the PRD list, additive) |
| `project_history` | gains `ref?` | unchanged shape |
| `project_restore` | `commit` now also accepts a branch/tag name | unchanged shape |

Registration is unconditional except for one guard: when `git` is not on PATH
the pack registers **nothing** (matching the FEM-pack precedent, "register a
tool only if it can run") and `service.branches` is never installed, so the
whole system degrades to today's behavior. The tools that need
`merge-tree --write-tree` (`merge_branch`, `resolve_merge`) return a
`validation_error` naming the git-version requirement rather than being hidden,
because branch/tag still work on old git.

### Routes (`agentcad/server/routes_versioning.py`)

```
GET    /api/projects/{proj}/branches                 → branch_list
POST   /api/projects/{proj}/branches      {name, from}      → branch_create
POST   /api/projects/{proj}/branches/switch {name}          → branch_switch
DELETE /api/projects/{proj}/branches/{name}                 → branch_delete
GET    /api/projects/{proj}/versions                 → list_versions
POST   /api/projects/{proj}/versions      {name, message}   → version_tag
GET    /api/projects/{proj}/merge                    → merge_status
POST   /api/projects/{proj}/merge         {source, target, allow_invalid}
POST   /api/projects/{proj}/merge/resolve {choices}
POST   /api/projects/{proj}/merge/abort
```
All are `registry.call(...)` passthroughs returning the tool payload verbatim
(HTTP 200 even for `{"error": …}`, exactly like `routes_history.py`), with
**explicitly whitelisted body keys** per the route-pack contract — never
`**body`.

### Events

```json
{"type": "branch_changed",  "project": "p", "client": "chat:main", "branch": "feat"}
{"type": "merge_completed", "project": "p", "source": "feat", "target": "master",
 "commit": "<sha>", "validation": { … }}
```
Neither is `project_changed`, so neither triggers `_snapshot_on_event`.

### Error shapes

```json
{"error": {"type": "merge_conflict",
  "message": "merge of 'feat' into 'master' has 2 conflicts",
  "details": {
    "merge_id": "9f31c0", "source": "feat", "target": "master",
    "base": "<sha>", "outstanding": 2,
    "conflicts": [
      {"kind": "script", "path": "parts/flange.py", "part": "flange",
       "base": "<text|null>", "ours": "<text>", "theirs": "<text>",
       "merged": "<<<<<<< master\n…\n=======\n…\n>>>>>>> feat\n",
       "truncated": false},
      {"kind": "manifest", "key": "parts.flange.params.bolt_d",
       "base": 6.0, "ours": 8.0, "theirs": 5.0}
    ],
    "hint": "Resolve with resolve_merge {choices: {\"<path|key>\": {\"take\": \"ours\"|\"theirs\"|\"base\"}}}; scripts also accept {\"content\": \"…\"} and manifest keys {\"value\": …}. merge_abort discards the staged merge."
  }}}
```

- **`ours` = the target branch, `theirs` = the source** (the `git merge
  <source>` convention, stated in every description string and in the marked
  text's `-L` labels so agents cannot get it backwards).
- Script bodies are capped at 256 KB per side; over that, the side is omitted
  and `"truncated": true` is set (the staged file on disk always has the full
  text).
- `merge_conflict` is **returned**, not raised: the registry derives error
  types from class names (`MergeConflictError` → `mergeconflict_error`), so the
  orchestrator returns the payload directly to keep FR7's exact type string.
- Blocked validation → `validation_error` with `details.validation` = the
  report and `details.merge_id`; the staged merge survives so
  `merge_branch {allow_invalid: true}` can finish it without redoing the work.
- Everything else uses the existing classes: unknown branch/tag →
  `NotFoundError` (404); bad name, unrelated histories, deleting the default
  or a checked-out branch, old git → `ValidationError` (422); turn-lock held,
  dirty tree, concurrent target move, an existing staged merge →
  `ConflictError` (409).

---

## Data flow — the AC1 walk (two branches, different parts)

1. Agent A: `branch_create {project: "rocketry", name: "flange-weld"}` →
   `git branch` + `git worktree add .history/trees/flange-weld`.
   `branch_switch` → A's `checkouts.json` entry points at the branch.
2. A edits `parts/flange.py` via `update_part_script`. `store.write_script`
   resolves through `branch_resolver` → writes
   `.history/trees/flange-weld/parts/flange.py`; the `project_changed` publish
   hits `_snapshot_on_event`, which calls
   `history.snapshot(store.path_of(proj))` — `_locate` sees the `.git` file,
   uses the admin git-dir, and commits on `flange-weld`.
3. Agent B (identity `mcp`), still on `master`, edits `parts/nozzle.py`.
   Different `lock_key` ⇒ no turn-lock contention (FR2). Different working
   trees ⇒ no file contention.
4. B: `merge_branch {project: "rocketry", source: "flange-weld"}`.
   `merge-tree` merges cleanly (disjoint paths); `project.json` differs only in
   nothing, or in disjoint `parts.<id>` subtrees ⇒ driver clean.
5. Staged worktree built; validation rebuilds `flange` — its mesh is already in
   the shared `.cache/` from step 2, so `{"cached": true}`, no kernel call.
   Mates re-resolve; interference finds no *new* pairs.
6. `commit-tree` with two parents, CAS `refs/heads/master`, target tree
   fast-forwarded, `merge_completed` published. `git log --graph` in `.history`
   shows the merge with both parents.

## Testing strategy

- **`tests/test_manifest_merge.py`** — pure, kernel-free, fast. The full truth
  table per key class, list-ordering determinism, add/add, delete/modify,
  int-vs-float distinction, unknown-section pass-through, and the "clean merge,
  broken references" case that the validation pass must catch.
- **`tests/test_branches.py`** — `integration`, `portability`,
  `skipif(shutil.which("git") is None)` (mirroring `tests/test_history.py:24`).
  Real service + registry with real history (**not** `make_test_service`, which
  disables the snapshot hook). Branch create/list/switch/delete, per-client
  isolation via `locks.set_client_id`, per-branch turn locks, per-branch
  undo/redo, tags, `ref` on history tools, `.cache` reuse across branches
  (count kernel `build` calls the way `test_history.py` does).
- **`tests/test_merge.py`** — same markers, `slow` for the example-based cases.
  Clean merge, script conflict + tool-only resolution, param-vs-script,
  validation blocking + `allow_invalid`, abort, fast-forward,
  concurrent-target-move CAS, two-parent assertion via
  `git rev-list --parents -n 1`.
- **`tests/test_versioning_api.py`** — routes + tool registration.
  `TestClient(app, base_url="http://127.0.0.1")` and
  `create_app(..., extra_allowed_hosts={"testserver"})`.
- **Examples on a copy** — AC1/AC4 use `examples/rocketry` copied with
  `shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".cache",
  "exports"))` exactly as `tests/test_examples.py:44` does. Note rocketry has
  **no mates** (no example does), so the mate-re-resolution branch of the
  validation pass needs a hand-built fixture.
- **Browser verification** (AC6, definition of done): create a branch, edit,
  switch back and forth, merge clean, watch the viewport update live, zero
  console errors, screenshot.

## Risks and open questions

| Risk | Mitigation / what the implementer must verify |
|---|---|
| **`git merge-tree --write-tree` needs git ≥ 2.38** (2022). Local dev is 2.50.1; Windows/Linux CI images are newer. | Version-probe once and return a clear `validation_error`. Verify what the CI runners actually have — if any is older, the fallback is a temp-index `read-tree -m` + `git merge-file` per path. |
| **`-z` output parsing** of `merge-tree` is a stable but fiddly format (informational-message section shape). | Parse only the tree oid and the conflicted-file section; treat the message section as opaque. Cover with a test that asserts stage extraction, not message text. |
| **Nested worktrees under `GIT_DIR`.** Supported, but unusual. | Verified working end-to-end (add, commit, `worktree list`). Verify `git worktree remove` and `git worktree prune` behave when a tree is deleted out from under git, and that `info/exclude`'s `.history/` really keeps `git add -A` from seeing `trees/`. |
| **Disk cost** of one checkout per branch, plus per-mutation snapshots × branches (PRD's "history bloat"). | Measure on the 33-part engine example; `.cache/` (the big part) is shared. `git gc` housekeeping is explicitly out of MVP. |
| **Long-lived staged merges** leave a worktree on disk. | `merge.json` records `created`; `merge_status` surfaces it and the UI offers Abort. No automatic expiry in MVP. |
| **`_status` cross-branch pollution** in the service. | Argued self-healing above; assert it with a test that builds the same part on two branches and checks the second read reports `cached: true` with no kernel build. |
| **Interference cost** on large assemblies during validation. | Capped at 40 instances with an explicit `skipped` marker in the report; revisit when PRD-004 (CI) lands. |
| **`chat:<session>` identities contain `:`** while `X-Agent-Id` is unvalidated, so an agent could impersonate a chat lane. | Pre-existing (turn locks have the same exposure); out of scope, noted. |
| **Two browser tabs share the `browser` identity** and therefore one branch. | Accepted for a local single-user tool; PRD-005 revisits identity. |

## PRD divergences to fold back

1. Worktrees live at `.history/trees/<branch>/`, not `.history/worktrees/` —
   the latter is git's own admin path.
2. Kernel affinity keys do **not** incorporate the branch: the worker LRU is
   content-keyed, so branch-qualifying the key would reduce reuse without
   buying correctness.
3. Interference blocks on **newly introduced** pairs, not on any pair, so a
   project with a pre-existing overlap stays mergeable.
4. One additive tool beyond the PRD list, `merge_status`, so the UI (and a
   restarted agent) can re-enter a staged merge.
5. `_content_signature` for reference parts moves from mtime to a content hash
   — required for FR13 once `imports/` is per-worktree.
