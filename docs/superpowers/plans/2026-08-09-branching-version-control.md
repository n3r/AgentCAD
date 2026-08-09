# Branching version control — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship the MVP of
[PRD-001](../../prd/in-progress/PRD-001-branching-version-control.md) —
branches, tags, per-client checkouts, a structure-aware `project.json` merge
driver, kernel-validated merges, and a minimal UI — per
[the design spec](../specs/2026-08-09-branching-version-control-design.md).

**Architecture (one paragraph):** the default branch keeps the project
directory as its working tree; every other branch gets a linked git worktree
under `<project>/.history/trees/<branch>/`. `ProjectStore` resolves every
authored-state path through a `branch_resolver` seam keyed by the existing
client-identity ContextVar, so all existing store/service/history code becomes
branch-aware for free; `.cache/` stays canonical so meshes are reused across
branches. Merges run `git merge-tree --write-tree` for scripts and a pure
`merge_manifests(base, ours, theirs)` driver for `project.json`, stage the
result in a detached temp worktree, validate it with the real kernel, and land
it as a two-parent merge commit. Everything is exposed through a tool pack and
a route pack; the UI adds a toolbar branch switcher, a versions dialog, and a
conflict list.

**Tech stack:** Python 3.12 / stdlib `subprocess` git / FastAPI / pytest
(session-scoped `kernel` fixture); vanilla ES-module frontend with vendored
CodeMirror 5.

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** Nothing in this
  feature touches the kernel or imports geometry.
- **Do not edit `worker.py`, `tools.py`, or `app.py`.** New capability arrives
  as a tool pack (`agentcad/core/tools_versioning.py`) and a route pack
  (`agentcad/server/routes_versioning.py`).
- `service.py` gets **exactly one** change in this whole plan: the
  reference-part cache signature in `_content_signature` (Slice 2, Task 4).
  Everything else that would have been a service edit is done by rewiring a
  seam from the tool pack, the way `tools_materials.py` rewires
  `service.materials`.
- Structured errors only: `NotFoundError`/`ValidationError`/`ConflictError`
  (→ 404/422/409) for the ordinary cases; `merge_conflict` is **returned** as
  a `{"error": {...}}` payload (HTTP 200 through the routes, like
  `routes_history.py`), never raised, so its type string survives the
  registry's class-name mapping.
- Mutating operations return post-state, never a bare OK.
- Atomic writes (`ProjectStore._atomic_write`) for every sidecar file under
  `.history/agentcad/`.
- **All 319 existing tests must keep passing, unedited.** Any diff to an
  existing test is a design bug — stop and re-read the spec.
- Tests: session-scoped `kernel` fixture; git-touching tests carry
  `pytest.mark.integration`, `pytest.mark.portability`, and
  `skipif(shutil.which("git") is None)` (copy `tests/test_history.py:24-28`);
  broad/example-driven ones also `pytest.mark.slow`. Use the real service (not
  `make_test_service`, which disables the snapshot hook) whenever history
  matters. `TestClient(app, base_url="http://127.0.0.1")` and
  `create_app(..., extra_allowed_hosts={"testserver"})`.
- **Examples run on a copy** —
  `shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".cache", "exports"))`,
  as in `tests/test_examples.py:44-54`. Never mutate `examples/` in place.
- **Never `uv sync` / `uv pip install` into the shared venv** from a parallel
  agent — use a scratch venv.
- **Every commit stages a changelog entry** `docs/changelog/NNNN-<slug>.md`
  written from the real diff, per `docs/changelog/README.md`. The highest
  existing entry when this plan was written is **0066**, so the slices below
  name 0067–0071 — **recompute `NNNN` at commit time** (`ls docs/changelog | tail`)
  because other work may have landed first.
- Commit messages end with `Co-Authored-By: Claude <noreply@anthropic.com>`.

---

## Slice map

| # | Slice | Lands | Changelog |
|---|---|---|---|
| 1 | Manifest merge driver (pure) | `manifest_merge.py` + exhaustive unit tests | `0067-manifest-merge-driver.md` |
| 2 | Branch-aware store, history, refs, tags | FR1–FR5, FR11–FR13 | `0068-branch-aware-project-store.md` |
| 3 | Merge orchestration + validation + packs | FR6–FR10 | `0069-branch-merge-orchestration.md` |
| 4 | Frontend: switcher, versions, conflict list | the human path | `0070-branching-ui.md` |
| 5 | Docs + AC verification | AC1–AC7 | `0071-branching-docs-and-acceptance.md` |

Each slice is independently landable: 1 is pure and useful alone; 2 ships
branches/tags without merge; 3 adds merge on top; 4 is UI-only; 5 is docs.

---

## Slice 1 — the manifest merge driver

**Why first:** it is pure, kernel-free, git-free, and it is where all the CAD
semantics live. It can be test-driven exhaustively in seconds, and Slice 3
consumes it as a finished component.

**Files**
- Create: `agentcad/core/manifest_merge.py`
- Create: `tests/test_manifest_merge.py`

**Interfaces produced** (Slice 3 depends on these exact names)
```python
def merge_manifests(base: dict, ours: dict, theirs: dict) -> tuple[dict, list[dict]]
def apply_choices(merged: dict, conflicts: list[dict], choices: dict) -> tuple[dict, list[dict]]
CONFLICT_KEYS = ("kind", "key", "base", "ours", "theirs")
```
`ours` = the **target** branch, `theirs` = the **source**. A conflict entry is
`{"kind": "manifest", "key": "<dotted key>", "base": …, "ours": …, "theirs": …}`
(`base` omitted for add/add). The returned manifest carries **ours' value at
every conflicted key**, so it is always a loadable document.

- [ ] **Step 1: write the failing tests** — `tests/test_manifest_merge.py`, no
  fixtures beyond plain dicts. Cover, at minimum:
  - **Truth table per key class.** For a scalar (`units`), a part field
    (`parts.flange.label`), a param (`parts.flange.params.bolt_d`), an instance
    field (`assembly.instances.flange_1.position`), a material
    (`materials.custom_al`): both-same → clean; ours-only → ours;
    theirs-only → theirs; both-different → conflict naming that exact key.
  - **FR8 case 1** — A edits `parts.a.*`, B edits `parts.b.*` ⇒ zero conflicts,
    both land.
  - **FR8 case 2** — A rewrites the script (not the manifest at all, so the
    manifest merge is a no-op), B changes `parts.a.params.size` ⇒ zero
    conflicts. Assert the driver does not invent one.
  - **Add/add** — both sides add `parts.new` with different bodies ⇒ conflict
    at `parts.new`; with identical bodies ⇒ clean.
  - **Delete/delete** and **delete/unchanged** ⇒ deleted, no conflict.
  - **Delete/modify** ⇒ conflict at `parts.<id>` (whole entry), with
    `"ours": null` or `"theirs": null` for the deleting side.
  - **Atomic sections** — `parts.<id>.pmi` conflicts as one key even when the
    two sides edited different sub-lists; `materials.<id>` likewise.
  - **Vectors are atomic** — ours changes `position[0]`, theirs changes
    `position[2]` ⇒ **conflict**, not a blended vector.
  - **int vs float** — `6` vs `6.0` are different values (conflict when both
    changed), matching `_normalize_param`'s storage.
  - **Ordering determinism** — merged `parts` is ours' order then theirs-only
    additions; run the merge twice and assert `json.dumps` equality.
  - **Unknown top-level section** (`"drawings": {...}`) merges whole-value and
    survives round-trip.
  - **Clean-but-broken** — ours deletes `parts.flange`, theirs adds
    `assembly.instances.flange_1` referencing it ⇒ **zero conflicts** (assert
    this explicitly; the validation pass is the backstop, and Slice 3 has the
    matching test).
  - `apply_choices` — `{"parts.a.params.x": {"take": "theirs"}}`,
    `{"take": "base"}`, `{"value": 12.0}`; an unknown key is a
    `ValidationError`; a partially-applied choice set returns the remaining
    conflicts.

- [ ] **Step 2: run to verify failure** —
  `uv run pytest tests/test_manifest_merge.py -q` → import error.

- [ ] **Step 3: implement `agentcad/core/manifest_merge.py`.** Shape:
  ```python
  """Structure-aware three-way merge for project.json (pure, no I/O)."""

  def _flatten(manifest: dict) -> dict[str, object]:
      """Manifest -> {dotted key: value} at CAD granularity (see the design
      spec's key table). Lists keyed by 'id'; pmi and materials entries stay
      whole; everything unrecognized is one whole-value key."""

  def _unflatten(keys: dict[str, object], order: dict) -> dict:
      """Inverse, restoring ours-then-theirs list order."""

  def _merge_key(base, ours, theirs, *, present) -> tuple[object, bool]:
      """Returns (value, conflicted) by the truth table."""
  ```
  Keep it stdlib-only; compare with a normalizing key
  (`json.dumps(v, sort_keys=True)` with a `type(v).__name__` prefix so
  `6` ≠ `6.0`). No git, no filesystem, no imports from `service`/`project`.

- [ ] **Step 4: run** — `uv run pytest tests/test_manifest_merge.py -q` →
  all pass. Then `uv run pytest -q -m "not slow"` to prove nothing else moved.

- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0067-manifest-merge-driver.md` (recompute `NNNN`), written
  from `git diff`.

**Verification command:** `uv run pytest tests/test_manifest_merge.py -q`

---

## Slice 2 — branch-aware store, history, refs and tags (FR1–FR5, FR11–FR13)

**Files**
- Create: `agentcad/core/branches.py`
- Modify: `agentcad/core/project.py` (resolver seam, `canonical_path_of`,
  `lock_key`, `cache_dir`, `list_projects`)
- Modify: `agentcad/core/history.py` (`_locate`, `_has_repo`, `_REF_RE`, ref
  primitives, branch-keyed `UndoCursor`)
- Modify: `agentcad/core/service.py` (**one change only**: `_content_signature`)
- Modify: `agentcad/core/tools_locks.py` (three `lock_key` call sites)
- Modify: `agentcad/core/tools_history.py` (`ref` argument; ref-name restore)
- Create: `tests/test_branches.py`

**Interfaces produced** (Slices 3–4 depend on these exact names)
```python
# agentcad/core/branches.py
pinned_tree_var: ContextVar[Path | None]

class BranchManager:
    def __init__(self, service): ...        # installs store.branch_resolver
    def resolve_path(self, proj, canonical) -> Path
    def default_branch(self, proj) -> str
    def current(self, proj, client=None) -> str
    def list(self, proj) -> dict            # {branches, current, default, you}
    def create(self, proj, name, from_ref=None) -> dict
    def switch(self, proj, name) -> str
    def delete(self, proj, name) -> dict
    def tag(self, proj, name, message=None) -> dict
    def versions(self, proj) -> list[dict]
    def tree_of(self, proj, branch) -> Path
    @contextmanager
    def pinned(self, proj, path) -> Iterator[None]

# agentcad/core/project.py
ProjectStore.branch_resolver: Callable[[str, Path], Path] | None
ProjectStore.canonical_path_of(proj) -> Path
ProjectStore.lock_key(proj) -> str
```

### Task 1 — `ProjectStore` resolver seam

- [ ] **Step 1: failing tests** (kernel-free, in `tests/test_branches.py`):
  a bare `ProjectStore` with `branch_resolver = None` behaves exactly as today
  (`path_of == canonical_path_of == root/name`, `lock_key == proj`); with a
  stub resolver returning a second directory, `manifest`/`read_script`/
  `write_script`/`script_path`/`exports_dir`/`imports_dir` all follow the
  resolver while `cache_dir` stays canonical; `list_projects` reports the
  resolver's part counts.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement.** Rename today's `_resolve` body to `_locate`, then:
  ```python
  def canonical_path_of(self, proj: str) -> Path:
      return self._locate(proj)

  def _resolve(self, proj: str) -> Path:          # every existing caller
      canonical = self._locate(proj)
      resolver = self.branch_resolver
      return canonical if resolver is None else resolver(proj, canonical)

  def lock_key(self, proj: str) -> str:
      """Key for per-branch turn locks and undo stacks: the project name when
      branching is inactive, otherwise the resolved working-tree path."""
      return proj if self.branch_resolver is None else str(self._resolve(proj))
  ```
  Point `cache_dir` at `canonical_path_of`; leave `exports_dir`/`imports_dir`
  on `_resolve`. In `list_projects`, map each discovered path through the
  resolver before `_read_manifest`.
- [ ] **Step 4: run** — `uv run pytest tests/test_project.py tests/test_service.py tests/test_branches.py -q`.

### Task 2 — `ProjectHistory` works in a linked worktree

- [ ] **Step 1: failing tests** — build a repo by hand with
  `git worktree add`, then assert `ProjectHistory.snapshot/log/head/restore`
  operate on the *linked* tree (commit lands on the linked branch, main tree
  unchanged); assert `resolve_ref` accepts a branch name, a tag name and a
  commit id, and rejects `--help`, `..`, `a/`, `x.lock`, `HEAD@{1}`.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** `_locate` (see the spec, Decision 3), use it in
  `_run` for `--git-dir` and the hermetic `HOME`/`XDG_CONFIG_HOME`, extend
  `_has_repo` to accept a `.git` file, add `_REF_RE` + `resolve_ref`,
  `branches`, `tags`, and `log(..., ref=None)`. Make `_ensure_repo` rewrite
  `info/exclude` when its content differs (self-healing upgrade).
  **Do not touch `_COMMIT_RE`'s existing use** — `_REF_RE` is additive.
- [ ] **Step 4: run** — `uv run pytest tests/test_history.py tests/test_branches.py -q`
  (the whole existing history suite must pass untouched).

### Task 3 — branch-keyed `UndoCursor` and turn locks

- [ ] **Step 1: failing tests** — two clients on two branches: each has its own
  undo stack; A's undo does not move B's tree; A holding the turn on branch
  `feat` does not block B writing on `master`; A holding the turn on `master`
  *does* block B on `master` (the existing behavior, re-asserted).
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement.** In `history.py`, give `UndoCursor` a
  `_key(proj)` = `self.store.lock_key(proj)` (with a `getattr` fallback to
  `proj` so an older store still works) and use it for `self._undo` /
  `self._redo` / `status`. In `tools_locks.py`, replace `project` with
  `service.store.lock_key(project)` in `acquire_turn`, `release_turn` and
  `get_turn` (the default `lock_key` returns `project`, so behavior without
  the versioning pack is unchanged — `tests/test_locks.py` must pass
  untouched).
- [ ] **Step 4: run** — `uv run pytest tests/test_locks.py tests/test_history.py tests/test_branches.py -q`.

### Task 4 — reference-part cache signature (the single `service.py` edit)

- [ ] **Step 1: failing test** — a reference part's cache key is unchanged by
  `os.utime`-ing the imported file, and two copies of the same import at
  different paths/mtimes produce the same key.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** in `service._content_signature`:
  ```python
  if src and src.is_file():
      digest = hashlib.sha256(src.read_bytes()).hexdigest()
      return f"ref:{record.source}:sha256:{digest}"
  ```
  Comment it with the reason (imports are per-worktree; mtime-keyed signatures
  would break FR13 across branches).
- [ ] **Step 4: run** — `uv run pytest tests/test_reference.py tests/test_service.py -q`.

### Task 5 — `BranchManager`

- [ ] **Step 1: failing tests** in `tests/test_branches.py` (real service +
  registry with the real history hook, `git`-skipif, `integration`,
  `portability`):
  - `create` makes a ref **and** a worktree at `.history/trees/<name>`;
    `git worktree list` shows both.
  - Name validation: `Feat`, `-x`, `a..b`, `a/`, `x.lock`, `""`, a 65-char name
    → `validation_error`; `feat/x-1` → ok.
  - `switch` is per-client: with `locks.set_client_id("agent_a")` on `feat` and
    `"agent_b"` on the default, `store.read_script` returns each client's own
    text; a write by A never appears in B's tree.
  - The snapshot hook commits to the mutating client's branch:
    `project_history` under A shows A's edit, under B does not.
  - `delete` of the default branch or of any client's current branch →
    `validation_error`; otherwise the worktree and ref are gone.
  - `default_branch` is discovered (`master` for repos this code creates) and
    persisted to `.history/agentcad/config.json`.
  - `checkouts.json` round-trips a restart (build a second `BranchManager` over
    the same project and assert the client is still on its branch); an entry
    pointing at a deleted branch is dropped.
  - **FR3/FR13 cache reuse:** build a part on the default branch, create a
    branch, switch, read the same part — count kernel `build` calls with the
    `monkeypatch`-a-counting-`kernel.request` pattern from
    `tests/test_history.py` and assert **zero** new builds.
  - `tag` + `versions`: tag, mutate, `project_restore {commit: "<tag>"}`
    restores **byte-identical** `project.json` and script bytes; re-tagging the
    same name → `conflict_error`; the tag survives `branch_delete` (AC5).
  - `project_history {ref: "<other-branch>"}` reads that branch without
    switching.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `agentcad/core/branches.py`.** All git goes through
  `ProjectHistory._run` (never a raw `subprocess` call) so the hermetic env,
  the 10 s timeout, and the never-raise-into-a-save discipline are inherited.
  Sidecar state (`config.json`, `checkouts.json`, `tags.json`) lives under
  `.history/agentcad/` and is written with `ProjectStore._atomic_write`.
  Worktree directory names sanitize `/` → `-` with collision disambiguation,
  recorded in the sidecar. `pinned()` is a `contextmanager` around
  `pinned_tree_var`.
- [ ] **Step 4: run** — `uv run pytest tests/test_branches.py -q`, then the
  full suite: `make test` (expect 319 + the new cases, zero regressions).

- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0068-branch-aware-project-store.md` (recompute `NNNN`).

**Verification command:** `make test` — cite the count and confirm every
pre-existing test passed unedited.

---

## Slice 3 — merge orchestration, validation pass, tool + route packs (FR6–FR10)

**Files**
- Create: `agentcad/core/merge.py`
- Create: `agentcad/core/tools_versioning.py`
- Create: `agentcad/server/routes_versioning.py`
- Create: `tests/test_merge.py`, `tests/test_versioning_api.py`
- Modify: `agentcad/core/tools_history.py` (already touched in Slice 2 — no
  further change expected)

**Interfaces produced**
```python
# agentcad/core/merge.py
class MergeOrchestrator:
    def merge(self, proj, source, target=None, allow_invalid=False) -> dict
    def resolve(self, proj, choices: dict) -> dict
    def abort(self, proj) -> dict
    def status(self, proj) -> dict
MERGE_INTERFERENCE_MAX_INSTANCES = 40
```
Tools: `branch_create`, `branch_list`, `branch_switch`, `branch_delete`,
`version_tag`, `list_versions`, `merge_branch`, `resolve_merge`, `merge_abort`,
`merge_status`. Routes as listed in the design spec. Events `branch_changed`,
`merge_completed`.

### Task 1 — git plumbing helpers

- [ ] **Step 1: failing tests** — a hand-built repo with a known conflict;
  assert the parser returns the merged tree oid and per-path stages
  `{1: base_oid|None, 2: ours_oid, 3: theirs_oid}`; assert the git-version
  probe rejects a faked `2.30` with a `validation_error` naming 2.38.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** in `merge.py`: `_merge_tree(path, ours, theirs)`
  running `git merge-tree --write-tree -z <ours> <theirs>` (exit 0 clean,
  1 conflicts, >1 error), splitting the output on `\0` — first field is the
  tree oid, then `<mode> <oid> <stage>\t<path>` records until an empty field,
  then opaque messages. `_blob(path, oid) -> bytes`, and
  `_marked(path, stages, ours_label, theirs_label)` shelling
  `git merge-file --diff3 -L … -p`.
- [ ] **Step 4: run** — `uv run pytest tests/test_merge.py -q -k plumbing`.

### Task 2 — merge, stage, conflict payload

- [ ] **Step 1: failing tests:**
  - **Clean, disjoint parts (AC1 shape, hand-built project):** two branches
    edit different parts ⇒ no conflicts, merge commit has **two parents**
    (`git rev-list --parents -n 1 HEAD` has three fields), target tree contains
    both edits.
  - **Fast-forward:** target has no commits of its own since the base ⇒
    `{"fast_forward": true}`, no merge commit, no validation pass.
  - **Already up to date:** merging an ancestor ⇒ no-op payload.
  - **Script conflict (AC2 shape):** both sides edit the same lines of one
    script ⇒ `{"error": {"type": "merge_conflict"}}` with a conflict naming
    `parts/<id>.py`, `part`, `ours`, `theirs`, `base`, and a `merged` text
    containing `<<<<<<<`, `|||||||`, `=======`, `>>>>>>>`; **nothing outside
    `.history/agentcad/` changed** and no ref moved.
  - **Manifest conflict:** both sides change the same param ⇒ a
    `{"kind": "manifest", "key": "parts.<id>.params.<name>"}` entry.
  - **FR8 param-vs-script (AC3):** A edits the script, B edits that part's
    params ⇒ clean, both land.
  - **`resolve_merge`:** `{"take": "theirs"}` on the script and
    `{"value": …}` on the key; the payload reports remaining conflicts, and
    the last resolution completes the merge. `{"content": "…"}` also works.
    An unknown path/key → `validation_error`.
  - **`merge_abort`** removes `.history/agentcad/merge.json` and the staged
    worktree (`git worktree list` back to normal); a second abort is a no-op.
  - **`merge_status`** reports the staged merge between the two calls.
  - **Concurrent target move:** stage a conflicted merge, commit on the target
    behind its back, then resolve ⇒ `conflict_error` (the CAS on
    `update-ref` fired), staged state preserved.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** steps 1–7 of the design spec's Decision 6. Note:
  `project.json` is **always** re-merged with `merge_manifests`, whether or not
  git flagged it. Cap script bodies in the payload at 256 KB per side with
  `"truncated": true`.
- [ ] **Step 4: run** — `uv run pytest tests/test_merge.py -q`.

### Task 3 — validation pass (FR9)

- [ ] **Step 1: failing tests:**
  - **Blocked build (AC-adjacent):** a merge whose result has a broken script
    ⇒ `validation_error` with `details.validation.failures` naming the part,
    the target ref unmoved, staged merge still present; re-running with
    `allow_invalid: true` lands it and the merge commit message contains
    `Validation: FAILED`.
  - **Interference (AC4):** two branches move instances so the merged assembly
    overlaps ⇒ blocked by default with the **pair named** in
    `validation.interference.new_pairs`; `allow_invalid: true` lands it with
    the same pair named in the result and in the commit message.
  - **Pre-existing overlap does not block** — an assembly that already
    interferes on the target merges cleanly (only *new* pairs block).
  - **Referential integrity:** the Slice-1 "clean but broken" case (ours
    deletes a part, theirs adds an instance of it) ⇒ blocked with
    `validation.integrity` naming the dangling instance.
  - **Cache reuse:** the validation pass of a merge whose parts were already
    built on both branches performs **zero** kernel `build` calls.
  - **Instance cap:** an assembly above `MERGE_INTERFERENCE_MAX_INSTANCES`
    reports `interference.skipped == "instances"` and does not block on it.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** step 8: run inside
  `with service.branches.pinned(proj, staged_dir):` and call the *ordinary*
  service methods (`get_metrics`, `_resolved_instances`, `check_interference`)
  so the kernel path, the mesh cache and the mates resolver are reused
  verbatim. Diff the interference pair set against the pre-merge target's.
- [ ] **Step 4: run** — `uv run pytest tests/test_merge.py -q`.

### Task 4 — finalize (FR10) + tool pack + route pack

- [ ] **Step 1: failing tests** in `tests/test_versioning_api.py`:
  - every new tool is registered and its schema validates
    (`registry.call` with a bad type → `invalid_arguments`);
  - when `git` is absent (monkeypatched `shutil.which`) the pack registers
    **nothing** and `service.branches` is absent;
  - each route returns the tool payload verbatim, `merge_conflict` arriving as
    HTTP **200** with an `{"error": …}` body (like `routes_history.py`);
  - unknown body keys are ignored (whitelisted forwarding), `null` values are
    not forwarded;
  - `branch_changed` and `merge_completed` are observed on a bus subscription
    with the documented field sets;
  - undo after a merge restores the pre-merge target state.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** step 9 (`commit-tree` with two parents, CAS
  `update-ref`, target-tree fast-forward, `undo_cursor.on_snapshot`, both
  publishes), then the packs. `tools_versioning.register` must:
  ```python
  if not service.history.available():
      return                      # no git → register nothing (FEM precedent)
  service.branches = BranchManager(service)          # installs the resolver
  service.merges = MergeOrchestrator(service)
  service.store.write_guard = lambda proj: service.turnlock.check(
      service.store.lock_key(proj), locks.current_client_id()
  )
  ```
  Tool descriptions must state the `ours` = target / `theirs` = source
  convention and the resolution recipe (agents read these, not this plan).
  The route pack whitelists body keys explicitly — never `**body`.
- [ ] **Step 4: run** — `uv run pytest tests/test_merge.py tests/test_versioning_api.py tests/test_tools.py tests/test_server.py -q`, then `make test`.

- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0069-branch-merge-orchestration.md` (recompute `NNNN`).

**Verification command:** `make test` (cite the count) plus
`uv run pytest tests/test_merge.py -q`.

---

## Slice 4 — frontend: branch switcher, versions dialog, conflict list

**Files**
- Modify: `frontend/index.html` (branch `.menu-wrap`; `#versions-modal`;
  `#merge-modal`)
- Modify: `frontend/js/api.js` (a `// ---- branches / versions / merge ----`
  section)
- Modify: `frontend/js/state.js` (`branch`, `branches`, `versions`,
  `clientId`, `merge`)
- Modify: `frontend/js/main.js` (`setupBranchMenu`, two WS cases, expose
  `refreshProject`/`loadProject` on `actions`, modal-aware key guard)
- Create: `frontend/js/versions.js`, `frontend/js/merge.js`
- Modify: `frontend/css/app.css` (`branch-*`, `ver-*`, `conflict-*`,
  `.modal-title`, `.modal.narrow`, `#toasts` z-index)

- [ ] **Step 1: `api.js`** — one-line arrows using the module-private `enc()`:
  `listBranches`, `createBranch`, `switchBranch`, `deleteBranch`,
  `listVersions`, `createVersion`, `mergeStatus`, `mergeBranch`,
  `resolveMerge`, `abortMerge`. Remember the passthrough convention: these
  routes return `{"error": …}` at HTTP 200, so callers check `res.error` **in
  addition to** catching `ApiError`.

- [ ] **Step 2: `index.html`** — a second `.menu-wrap` immediately after the
  project switcher and before `.toolbar-spacer`:
  ```html
  <div class="menu-wrap">
    <button id="branch-btn" class="tb-btn" aria-haspopup="menu" aria-expanded="false"
            title="Current branch">
      <span id="branch-name">master</span><span class="chev">▾</span>
    </button>
    <div id="branch-menu" class="menu left hidden" role="menu"></div>
  </div>
  ```
  **It must be static markup** — `setupMenus()` snapshots `.menu-wrap` once at
  boot (`main.js:810`), so a dynamically inserted menu gets no outside-click,
  Escape, or arrow-key handling. Add `#versions-modal` and `#merge-modal`
  alongside `#drawing-modal`, following its `.modal-overlay > .modal >
  .modal-head + body` structure.

- [ ] **Step 3: `main.js` — `setupBranchMenu()`**, modelled on
  `setupProjectMenu()` (L840–890): on open, `await api.listBranches(...)`,
  `setState({branches, branch, clientId: payload.you})`, rebuild the menu
  (`.menu-item.active` for the current, `span.meta` = relative time), then a
  `.menu-sep` and "New branch…", "Merge into…", "Versions…". Switching runs
  `confirmDiscardEdits(null)` first, then `api.switchBranch`, then the same
  context reset `loadProject()` performs (`meshBuffers.clear()`,
  `viewport.clear()`, `clearFaceSelection()`, `lastFittedTarget = null`) and
  `refreshProject()`. Add `refreshProject` and `loadProject` to the `actions`
  bag so `versions.js` / `merge.js` can trigger a reload.

- [ ] **Step 4: `main.js` — WS cases**, inside `handleEvent()`'s switch, each
  guarded by `if (ev.project !== state.projectName) return;`:
  ```js
  case "branch_changed":
    if (ev.client === state.clientId) { /* full context reset + refreshProject */ }
    else { setState({ branches: null }); }      // stale menu, relabel on open
    return;
  case "merge_completed":
    toast(ev.validation && ev.validation.ok
      ? `Merged ${ev.source} into ${ev.target}`
      : `Merged ${ev.source} into ${ev.target} with validation failures`,
      ev.validation && ev.validation.ok ? "info" : "error");
    refreshProject();
    return;
  ```

- [ ] **Step 5: `versions.js`** — `init(actions)` + `open()`, wired exactly
  like `drawings.js` (close button, backdrop click, `Escape`). Lists tags
  (name, relative date, message, short commit) with a **Restore** action
  (`api.projectRestore(proj, tagName)` — the tool now accepts ref names) and a
  "Tag current state…" field using the existing `prompt` + validate + `toast`
  shape.

- [ ] **Step 6: `merge.js`** — conflict list modal. Left rail of conflicts;
  right pane is a **read-only CodeMirror** for scripts
  (`window.CodeMirror(host, {readOnly: "nocursor", mode: "python", theme:
  "agentcad", lineWrapping: false})` — the global is already loaded by
  `index.html`; **call `cm.refresh()` after un-hiding the modal**) or a
  base/ours/theirs value table for manifest keys. Per conflict: **Use ours**,
  **Use theirs**, **Edit…** (flip `readOnly` off and post the buffer as
  `content`). Footer: "N of M resolved", **Complete merge**, **Abort**. Do not
  touch `editor.js`'s singleton — create an independent instance.

- [ ] **Step 7: CSS** — new `branch-*` / `ver-*` / `conflict-*` classes using
  **only** existing tokens (light mode breaks otherwise); generalize
  `.modal-head #drawing-title` to a `.modal-title` class (update
  `drawings.js`/`index.html` accordingly); add
  `.modal.narrow { width: min(560px, 100%); }`; raise `#toasts` to `z-index: 90`
  so merge toasts render above the modal (80). Add a `modalOpen()` guard next
  to `setupKeys()`'s `inField` check so `f`/`g`/`r` don't act behind a dialog.

- [ ] **Step 8: verify in the real browser** (use the **`run` skill**; this is
  AC6 and the definition of done): create a branch from the switcher, edit a
  part, switch back and forth and watch the viewport/tree/inspector follow,
  merge clean and see the result live, force a script conflict from two chat
  sessions and resolve it in the conflict list. **Zero console errors.**
  Screenshot each of: the branch menu, the versions dialog, the conflict list.

- [ ] **Step 9: changelog + commit** — `docs/changelog/0070-branching-ui.md`
  (recompute `NNNN`).

**Verification command:** `make test` plus the browser session above with
screenshots and a clean console.

---

## Slice 5 — docs, acceptance criteria, PRD close-out

**Files**
- Modify: `docs/agent-api.md` (10 new tools with schemas; `ref` on the two
  history tools; bump the tool count)
- Modify: `docs/architecture.md` (the `.history/` layout with `trees/` and
  `agentcad/`; the branch-resolution seam; the merge data flow)
- Modify: `docs/user-guide.md` (branch switcher, versions dialog, conflict
  list)
- Modify: `AGENTS.md` (extension-point list unchanged; add the branching traps
  — `.history/trees/` vs git's `worktrees/`, `ours` = target, `.cache/` is
  canonical/shared)
- Modify: `docs/roadmap.md` (PRD-001 row → shipped) and move
  `docs/prd/in-progress/PRD-001-branching-version-control.md` →
  `docs/prd/shipped/`, updating its `Status:` line **in the same commit**
- Modify: the PRD itself with the five **divergences to fold back** listed at
  the end of the design spec (worktree path, affinity, new-pairs-only
  interference, `merge_status`, reference cache signature)
- Create: `tests/test_branching_acceptance.py` (or extend `test_merge.py`) —
  the AC-mapped tests
- Create: `docs/changelog/0071-branching-docs-and-acceptance.md`

**Acceptance criteria → concrete tests**

| AC | Test | Assertion |
|---|---|---|
| AC1 | `test_ac1_disjoint_parts_merge_clean` — `examples/rocketry` **on a copy** | two branches edit `flange` and `nozzle`; merge has zero conflicts, `validation.ok is True`, `git rev-list --parents -n 1 <target>` lists two parents, both edits present |
| AC2 | `test_ac2_script_conflict_resolved_by_tools_only` | same lines of `parts/flange.py` on both sides ⇒ `merge_conflict` naming the part with `ours`/`theirs`/`base`; resolution uses **only** `resolve_merge`; the completed merge returns a validation report |
| AC3 | `test_ac3_param_vs_script_merges_clean` | A `update_part_script`, B `set_params` on the same part ⇒ zero conflicts, both land |
| AC4 | `test_ac4_interference_blocks_then_allows` | blocked by default with the pair named; `allow_invalid: true` lands it with the same pair named in the result **and** the commit message |
| AC5 | `test_ac5_tag_round_trip_and_survives_branch_delete` | tag → mutate → restore: `project.json` bytes and every `parts/*.py` byte identical to the tagged state; `branch_delete` then `list_versions` still lists the tag and restore still works |
| AC6 | manual browser session (Slice 4, Step 8) | screenshots + zero console errors, recorded in the changelog |
| AC7 | `make test` | full suite green; every pre-existing test file unedited (`git diff --stat` over `tests/` shows only additions of new files) |

- [ ] **Step 1:** write the AC tests (they are the last failing tests; they
  should pass once Slices 1–4 are in).
- [ ] **Step 2:** update the docs above, matching each file's existing style.
- [ ] **Step 3:** `make test` → green; **record the exact count** (319 before
  this work).
- [ ] **Step 4:** `make test-portability` → green (the git/worktree paths are
  OS-sensitive; every new git-touching test carries the `portability` marker).
- [ ] **Step 5:** write the changelog from `git diff`, move the PRD to
  `docs/prd/shipped/`, update the roadmap row, and commit.

**Verification command:** `make test` and `make test-portability`, both green,
with the counts cited; plus `git diff --stat origin/main -- tests/` showing no
edits to pre-existing test files.

---

## Rollback / landing notes

- Slices 1 and 2 are safe to land alone: with the versioning pack absent
  (Slice 3), `store.branch_resolver` is never installed, so `path_of`,
  `lock_key`, `TurnLock` and `UndoCursor` all behave exactly as they do today.
- The whole feature self-disables when `git` is not on PATH: the pack registers
  no tools, `service.branches` is never created, and the product degrades to
  the current linear-history behavior (already covered by
  `tests/test_history.py::test_git_missing_degrades_gracefully`).
- If `git merge-tree --write-tree` turns out to be unavailable on a CI runner,
  branches and tags still work; only `merge_branch`/`resolve_merge` return the
  version `validation_error`. That is the intended graceful boundary.
