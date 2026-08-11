# 0090 — Spec tools, routes, the rebuild seam and the specs.py writer

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Claude

## Summary
PRD-003 Slice 4: the feature becomes usable end to end. A tool pack
(`agentcad/core/tools_specs.py`) installs `service.specs`, exposes
`run_specs` / `list_specs` / `get_project_specs` / `set_project_specs`, and
**wraps** `service._rebuild` and `service.get_part` so every rebuild-returning
path and every part read carries a `specs` summary. A route pack
(`agentcad/server/routes_specs.py`) puts the four tools on HTTP, and
`SpecRunner` grows the `specs.py` reader/writer FR2 needs. `service.py`,
`tools.py`, `app.py` and `worker.py` are **not** edited.

## Changes

### Tool pack — `agentcad/core/tools_specs.py`
- **`register(registry, service)`** constructs `service.specs = SpecRunner(service)`
  **unconditionally** and calls `install_rebuild_specs(service)`. The pack
  deliberately does **not** self-disable without git (unlike `tools_proposals`
  and `tools_versioning`): specs are a property of the working tree, so
  `run_specs`/`list_specs` work on a project with no history at all and only
  `ref=` needs branches — a `ref` without git is a `validation_error` naming
  git. Pinned by a test that monkeypatches `ProjectHistory.available` to
  `False` and still runs a green report.
- **Four tools**, thin delegations to the runner:

  | tool | schema | returns |
  |---|---|---|
  | `run_specs` | `{project*, part_id?, ref?}` | the full report (all three tiers) |
  | `list_specs` | `{project*, part_id?}` | declarations, zero builds |
  | `get_project_specs` | `{project*}` | `{path, exists, script, declared, specs, declaration_error, warnings}` |
  | `set_project_specs` | `{project*, script*}` | the same shape, as post-state |

- **The description contract** (asserted by a test, not just written): a
  failing spec never fails a rebuild; the four statuses and what separates
  them; that a `skip` always carries a `reason` **and** a `hint` and is not a
  failure; that `error` means the check itself broke ("we do not know", not
  "it is fine"); that a rebuild evaluates the **shape tier only** while
  `run_specs` evaluates **all three tiers**; that requirement strings are
  opaque. The shared prose lives in one module-level `_STATUSES` constant.
- **Load order** is pinned by a test: `tools_proposals` < `tools_specs` <
  `tools_stackup` < `tools_versioning`. So `service.gate_providers` exists when
  this pack runs (Slice 5 appends to it) while `tolerance_stackup` and
  `service.branches` do not — which is why `check_stackup` calls
  `compute_stackup` directly and the runner reads `branches` inside its methods.

### The rebuild seam — two installed wrappers (design Decision 5)
- **`install_rebuild_specs(service)`** wraps the two *bound* service methods
  with `functools.wraps` and marks each wrapper with a `_agentcad_specs_wrapper`
  attribute, so installation is **idempotent** — wrapping twice would evaluate
  the shape tier twice per rebuild. It is a wrapper and not a `service.py` edit
  because the extension-point contract forbids editing the core
  (`tools_versioning.install_write_guard` is the precedent), and it wraps
  `_rebuild` rather than the three rebuild-returning *tools* because the
  browser's `PATCH /api/projects/{p}/parts/{id}/params` route calls
  `service.set_params` **directly**, not through the registry — a tool wrapper
  would miss the UI entirely. Verified end to end by a test that drives the
  real HTTP route and reads `specs` off the response.
- **`_rebuild`** — a **successful** payload gains exactly one key, `specs`
  (`None` when the part declares nothing: "none declared", which is not "not
  evaluated"). A **failed** payload (`{"ok": False, "error": …}`) gains
  nothing: there is no geometry to assert over and a spec block there would
  compete with `with_hint`'s "fix the script first".
- **`get_part`** — `detail["specs"]` beside `detail["metrics"]`, from the
  result cache (`tier1` with no build result recomputes the key and reads the
  sidecar), so a built part costs **zero kernel calls**. A part whose build
  **failed** carries no `specs` key at all, by the same rule as a failed
  rebuild — and this also stops a broken part from paying for its failing build
  again on every read.
- **Best effort, always.** Any exception inside the runner is caught and
  reported as `{"specs": {"status": "error", "error": {type, message,
  details}}}` and never propagates: a broken spec layer must never break a
  rebuild.
- **The seam is pinned by five tests**, because `_rebuild` is private and a
  signature change would otherwise break it silently: installed exactly once
  (and `service._rebuild is` the same object after a second install), a
  successful payload's key set unchanged apart from `specs` (compared against
  `service._rebuild.__wrapped__`, the untouched original), a spec-less part's
  payload identical to the pre-feature payload with **zero** `spec_eval` /
  `spec_declare` calls, the wrapper surviving a second `build_registry` over
  the same service (it resolves `service.specs` at call time rather than
  capturing the runner), and an exception in the runner never escaping.

### The `specs.py` reader/writer — `agentcad/core/specs.py`
- **`SpecRunner.write_project_specs(proj, script)`** — writes
  `store.path_of(proj)/"specs.py"` with `ProjectStore._atomic_write`,
  **unconditionally**, and reports afterwards: a broken script is *saved* and
  its error returned, because you must be able to save one in order to fix it
  (the `update_part_script` rule). An empty (or whitespace-only) script deletes
  the file. It calls `store.write_guard` explicitly — the store's own guard
  fires only for `write_script`/`save_manifest`/`imports_dir` — so the write
  lands on the caller's branch tree and is refused with a `conflict_error`
  while another client holds the turn. It then publishes `project_changed`,
  which is what snapshots the file into git: a mutating pack needs no per-call
  history hook, and `specs.py` therefore branches, merges, restores and undoes
  for free (FR2, structurally).
- **`SpecRunner.read_project_specs(proj)`** and the shared `_project_state`
  helper. A project with no `specs.py` answers
  `{"script": None, "specs": [], "exists": False}` — not a 404.

### Route pack — `agentcad/server/routes_specs.py`
```
GET    /api/projects/{proj}/specs               ?part_id=      -> list_specs
POST   /api/projects/{proj}/specs/run    {part_id, ref}        -> run_specs
GET    /api/projects/{proj}/specs/file                  -> get_project_specs
PUT    /api/projects/{proj}/specs/file   {script}       -> set_project_specs
```
`routes_proposals`'s `_RAISE` / `_result` / `_body_keys` / `_json` helpers
verbatim, with an **empty `_BODY_ERRORS`** — this pack has no error type that
is a legitimate HTTP 200 body, because everything about a *check* is payload
rather than an error in the first place (a project that is entirely red is an
ordinary 200). Body keys are whitelisted explicitly, never `**body`; `_json`
reads the request **bytes**, so a chunked `PUT` with no `content-length` still
reaches the writer (pinned by a test). `script: ""` is forwarded (it deletes
the file) while `null` is not, and a missing `script` is `invalid_arguments`
→ 422.

## Files
- `agentcad/core/tools_specs.py` — new tool pack: `register`,
  `install_rebuild_specs`, `_summary`, four tools.
- `agentcad/server/routes_specs.py` — new route pack: four routes.
- `agentcad/core/specs.py` — `_project_state`, `read_project_specs`,
  `write_project_specs` (the Slice-3 handoff's one unimplemented method).
- `tests/test_specs_api.py` — new test module (24 tests) in three sections:
  registration, the rebuild seam, routes.
- `tests/test_specs.py` — a `TestProjectSpecsWriter` class (7 `_GIT` tests) and
  a sidecar-clearing addition to the module-scoped `specs_projects`
  fixture (see the note below).
- `docs/changelog/0090-spec-tools-and-rebuild-summary.md` — this entry.

## Notes

### Deliberate divergences from the design spec, and why
1. **The declaration error field is `declaration_error`, not `error`.** The
   design's tool table says `{path, declared, specs, error?}`. A top-level
   `error` key is the *tool-envelope failure marker* everywhere in this repo —
   `ToolRegistry.call` produces it, every route pack's `_result` raises on it
   (this shape turned a saved-but-broken `specs.py` into an HTTP 422 the first
   time it was written), and callers assert `"error" not in result`. A
   *reported* declaration failure must not wear that name. `list_specs`'s
   plural `errors[]` already dodges the same collision.
2. **A spec-less part's rebuild payload gains `specs: null`.** The plan asks
   for a payload "byte-identical to the pre-feature behaviour"; the design asks
   for an explicit `null` meaning "none declared". The design wins because
   "declares nothing" and "not evaluated" must stay distinguishable; the test
   asserts the payload is identical *apart from* that key, and that the kernel
   call count is unchanged.
3. **`get_part` on a part that does not build carries no `specs` key**, rather
   than an `error` block. It is the same rule the failed-rebuild path follows,
   it avoids duplicating a build error the UI already shows, and it stops a
   broken part from re-running its failing build on every read.
4. **`set_project_specs` validates nothing *before* writing.** A
   validate-then-write writer (refuse anything that does not parse or whose
   `SPECS` does not pass `spec_declare` structurally) was considered and
   rejected: it is the plan's and the design's explicit rule that a broken
   `specs.py` is written and reported, exactly as `update_part_script` does,
   because refusing it means the agent that wrote the broken file has no way
   to replace it. Validation still happens — one `spec_declare` right after
   the write — and lands in `declaration_error` on the post-state.
5. **The gate sentence is not in the descriptions yet.** The plan's
   description contract includes "a red `specs` gate blocks a proposal merge
   and `allow_invalid` does not waive it" — true only once Slice 5 appends the
   provider, so it lands there rather than promising a gate that does not exist.

### The one edit to an existing test file
`tests/test_specs.py`'s module-scoped `specs_projects` fixture now clears
`*.specs.json` from the prepared tree. `create_part` ends in `get_part`, so
with this slice's wrapper installed every part is spec-evaluated as it is
created and the clones would start with a **warm** spec sidecar — which is
correct behaviour, but it made two Slice-3 tests that measure the *cold* path
(`…one_call_with_part_affinity`, `…kernel_error_turns_the_parts_checks…`) see
zero kernel calls. Meshes are deliberately left warm. No assertion in any
existing test changed. **No test file predating PRD-003 was touched.**

### One consequence worth knowing
`service.create_part` and `service.update_part` both end in `get_part`/
`_rebuild`, so **a part that declares specs is evaluated as soon as it is
created**, not on first inspection. That is the `get_part` contract working as
designed (it is what makes the chips live), it costs one `spec_eval` on the
worker that just built the part, and it is free for a spec-less part. It is
also why `tests/test_specs.py`'s prepared fixture now clears its spec sidecars.

### Known gaps and what Slice 5 needs
- `evaluate_specs` and `gate_provider` are **not** here (Slice 5). The pack
  already runs after `tools_proposals`, so `service.gate_providers` exists at
  `register()` time and the append is a two-line addition; nothing else in this
  pack has to move.
- `service.specs` is replaced on every `build_registry` over the same service.
  The wrappers resolve it at call time for exactly that reason — a gate
  provider closure that captures the runner must do the same, or take it from
  `service`.
- The `specs` summary attached at rebuild time is **tier 1 only**; the gate
  must call `run`/`evaluate_specs`, not read a rebuild payload.
- A `run_specs` on a project whose `specs.py` will not execute returns the
  report with an `errors[]` entry and no project checks — the gate's
  fail-closed rule has to treat that as red, not as "nothing declared".

### Test suite
- `uv run pytest tests/test_specs.py tests/test_specs_api.py -q` →
  **77 passed** (29 s).
- `uv run pytest tests/test_specs_toolkit.py tests/test_specs_kernel.py -q` →
  **110 passed**, both unedited.
- `uv run pytest tests/test_service.py tests/test_tools.py tests/test_server.py
  tests/test_mcp.py tests/test_stackup.py -q` → **42 passed**, all unedited.
- `uv run pytest tests/test_proposals.py tests/test_branches.py -q` →
  **111 passed** (2:31), both unedited.
- `make test-fast` → **717 passed, 1 skipped** (2:55).
- `make test` → **853 passed, 1 skipped** (20:47) against a baseline of 822
  passed, 1 skipped — exactly the 31 new tests (24 in `tests/test_specs_api.py`,
  7 in `tests/test_specs.py`'s writer class), with **no assertion changed in
  any existing test**.
