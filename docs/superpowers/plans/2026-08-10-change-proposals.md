# Change proposals & geometric diff — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship the MVP of
[PRD-002](../../prd/in-progress/PRD-002-change-proposals-geometric-diff.md) —
a durable, attributed proposal object with a governed lifecycle, an
auto-generated review packet (script/PARAMS diffs, metric deltas, assembly
deltas, matched renders, kernel-computed added/removed volumes), a gated merge
over PRD-001, and a list/detail UI — per
[the design spec](../specs/2026-08-10-change-proposals-design.md).

**Architecture (one paragraph):** a proposal is a JSON document plus an
append-only audit log under `<project>/.history/agentcad/proposals/<id>/`,
beside PRD-001's other sidecars — canonical, shared by every branch, and
outside every working tree, so `project_restore` cannot rewind it. The review
packet is generated from the two branches' **existing** worktrees
(`branches.tree_of` + `branches.pinned`), driving the ordinary service path so
the shared content-addressed `.cache/` makes unchanged parts free; git supplies
script diffs, the two manifests supply PARAMS/assembly deltas, a new kernel
handler (`geom_diff`) supplies added/removed volumes and diff meshes, and
`render_acm` gains an explicit frame so before/after renders are
superimposable. `proposal_merge` evaluates gates (approvals policy now; spec
and CI through an empty provider list) and then calls
`MergeOrchestrator.merge` — PRD-001, unchanged — recording its validation
report and passing `merge_conflict` through verbatim. Everything is exposed
through a tool pack and a route pack; the UI adds a proposals list/detail modal
with four tabs.

**Tech stack:** Python 3.12 / stdlib `subprocess` git / FastAPI / pytest
(session-scoped `kernel` fixture); build123d only inside
`agentcad/kernel/handlers/diff.py`; vanilla ES-module frontend, no bundler.

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** In this plan that is
  exactly one new file, `agentcad/kernel/handlers/diff.py`. `core/packet.py`
  must never import it — it talks to the kernel over `service.kernel.request`.
- **Do not edit `worker.py`, `tools.py`, `app.py` or `service.py`.** New
  capability arrives as a worker handler pack
  (`agentcad/kernel/handlers/diff.py`), a tool pack
  (`agentcad/core/tools_proposals.py`) and a route pack
  (`agentcad/server/routes_proposals.py`). Seams are rewired from the tool
  pack, the way `tools_versioning.install_write_guard` does it.
- **Do not edit `merge.py`, `branches.py`, `manifest_merge.py` or
  `history.py`.** PRD-001 is finished and reviewed three times; this feature
  *consumes* it. `proposal_merge` calls `MergeOrchestrator.merge` and forwards
  its payloads.
- Exactly **two additive signature changes** to existing files in the whole
  plan: `render_acm(..., frame=None)` (Slice 3) and
  `viewport.showDiffOverlay/clearDiffOverlay` (Slice 5). Both must be
  no-ops when unused, asserted by a test / a clean console.
- Structured errors only: `NotFoundError`/`ValidationError`/`ConflictError`
  (→ 404/422/409). `merge_conflict` is **returned**, never raised, and is the
  only error type the route pack lets through at HTTP 200 (`_BODY_ERRORS`).
- Mutating operations return post-state, never a bare OK.
- Atomic writes (`ProjectStore._atomic_write`) for every file under
  `.history/agentcad/proposals/` **except `audit.jsonl`**, which is appended to
  (FR14 makes it append-only; a read-modify-replace cycle would break that and
  can truncate on a crash).
- **All 510 existing tests must keep passing, unedited.** Any diff to an
  existing test file is a design bug — stop and re-read the spec. (Baseline:
  `510 passed, 1 skipped`, `docs/changelog/0076-prd-001-completed.md`.)
- Tests: session-scoped `kernel` fixture. Git-touching tests carry
  `pytest.mark.integration`, `pytest.mark.portability` and
  `skipif(shutil.which("git") is None)`; example-driven or kernel-heavy ones
  add `pytest.mark.slow`. Nothing here is `exhaustive`. Use the real service
  (**not** `make_test_service`, which sets `bus.on_publish = None` and disables
  the snapshot hook) whenever history matters, and copy the autouse
  `_reset_context` fixture that rebinds `locks.client_id_var` and
  `pinned_tree_var`.
- `TestClient(app, base_url="http://127.0.0.1")` and
  `create_app(..., extra_allowed_hosts={"testserver"})` for every HTTP/WS test.
- **Examples run on a copy** —
  `shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".cache", "exports"))`,
  as in `tests/test_examples.py:44` and `tests/test_prd001_acceptance.py:145`.
  Never mutate `examples/` in place.
- **Never `uv sync` / `uv pip install` into the shared venv** from a parallel
  agent — use a scratch venv. **Subagents do not run `git`** (the coordinating
  session commits).
- **Every commit stages a changelog entry** `docs/changelog/NNNN-<slug>.md`
  written from the real diff, per `docs/changelog/README.md`. The highest
  existing entry when this plan was written is **0076**, so the slices below
  name 0077–0082 — **recompute `NNNN` at commit time**
  (`ls docs/changelog | tail`) because other work may have landed first.
- Commit messages end with `Co-Authored-By: Claude <noreply@anthropic.com>`.

---

## Slice map

| # | Slice | Lands | Changelog |
|---|---|---|---|
| 1 | Proposal store, state machine, audit, policy, gates | FR1–FR3, FR11, FR13–FR14 (core, no surface) | `0077-proposal-store-and-lifecycle.md` |
| 2 | Tool pack, route pack, events, gated merge | FR10–FR12, FR15; AC5, AC6, AC8, AC9 | `0078-proposal-surface-and-gated-merge.md` |
| 3 | `geom_diff` kernel handler + explicit render frame | FR6/FR7 primitives | `0079-geometric-diff-kernel-handler.md` |
| 4 | The review packet | FR4–FR9(partial); AC2, AC4, AC7 | `0080-proposal-review-packet.md` |
| 5 | Frontend: proposals list/detail, diff view, overlay | the human path; AC1, AC3 browser halves | `0081-proposals-ui.md` |
| 6 | Docs, acceptance tests, PRD close-out | AC1–AC9 | `0082-proposals-docs-and-acceptance.md` |

Each slice is independently landable: 1 is a pure-ish core module with unit
tests; 2 ships a working "CAD PR without evidence" (open, review, merge with
policy gates); 3 ships two standalone geometry primitives; 4 adds the evidence;
5 is UI-only; 6 is docs and acceptance.

---

## Slice 1 — proposal store, state machine, audit, policy, gates

**Why first:** it is where all the workflow semantics live, it is testable in
seconds without the kernel, and Slices 2/4/5 consume it as a finished
component.

**Files**
- Create: `agentcad/core/proposals.py`
- Create: `tests/test_proposals.py`

**Interfaces produced** (Slices 2–5 depend on these exact names)

```python
# agentcad/core/proposals.py
STATES = ("draft", "open", "approved", "changes_requested", "merged", "closed")
TERMINAL = ("merged", "closed")
ACTIVE = ("draft", "open", "approved", "changes_requested")
DEFAULT_POLICY = {"approvals_required": 1, "self_approve": False}

def actor_kind(identity: str) -> str        # "human" iff browser

class ProposalStore:                        # files only, no policy, no git
    def dir_of(self, proj: str) -> Path
    def load(self, proj: str, pid: str) -> dict
    def save(self, proj: str, proposal: dict) -> None
    def list(self, proj: str) -> list[dict]
    def allocate_id(self, proj: str) -> str
    def append_audit(self, proj: str, pid: str, entry: dict) -> dict
    def audit(self, proj: str, pid: str) -> list[dict]
    def policy(self, proj: str) -> dict
    def packet_path(self, proj: str, pid: str) -> Path
    def asset_dir(self, proj: str, pid: str, kind: str) -> Path   # renders|diff

class ProposalManager:
    def __init__(self, service): ...        # takes service.branches LAZILY
    def create(self, proj, source, target=None, title="", description="",
               draft=False) -> dict
    def list(self, proj, state=None) -> dict
    def get(self, proj, pid) -> dict         # {proposal, gates, audit, packet}
    def update(self, proj, pid, title=None, description=None, state=None) -> dict
    def review(self, proj, pid, verdict, summary=None) -> dict
    def gates(self, proj, proposal) -> list[dict]
    def transition(self, proposal, to: str, *, action: str, details=None) -> dict
```

- [ ] **Step 1: write the failing tests** — `tests/test_proposals.py` with the
  `_GIT` marker list, the autouse `_reset_context` fixture, and a `stack`
  fixture building a real `AgentCADService` (**not** `make_test_service`) with
  `build_registry(service)` so the versioning pack installs `service.branches`.
  Cover, at minimum:
  - **State machine.** Every legal transition in the design spec's table
    succeeds; `merged → open`, `draft → approved`, and reviewing a `closed`
    proposal are `validation_error`s whose `details` carry
    `{"from", "to", "allowed"}`. `proposal_merge`-from-`draft` is deferred to
    Slice 2 but `transition` must already refuse it.
  - **Identity and allocation.** Ids are decimal strings from 1 up; deleting a
    proposal directory by hand does **not** let the next id be reused
    (`next_id` only increments); the index is rebuilt from the directories when
    `index.json` is deleted or corrupted.
  - **Duplicate open proposal (FR2).** A second `create` for the same
    (source, target) while the first is in any of `ACTIVE` is a
    `conflict_error` with `details.existing_id`; after the first is `closed` or
    `merged`, a new one is allowed.
  - **Target default** is `branches.default_branch(proj)`, not the caller's
    current branch — assert with the caller sitting on a non-default branch.
    `source == target` is a `validation_error`. An unknown source branch is a
    `notfound_error` (resolved via `history.resolve_branch`, so a **tag** named
    like the branch does not satisfy it).
  - **Attribution (FR13).** `actor_kind("browser") == "human"`;
    `"chat"`, `"chat:main"`, `"mcp"`, `"agent_a"`, `"local"` are all `"agent"`.
    Every action writes an audit entry with the current
    `locks.current_client_id()`; drive it with `locks.set_client_id`.
  - **Audit is append-only (FR14).** Entries have monotonic `seq`; ten
    interleaved actions produce ten lines in order; there is no public method
    that edits or removes one; a corrupt trailing line is skipped on read
    rather than raising.
  - **Durability (FR3).** Files live under
    `<project>/.history/agentcad/proposals/`; `git status --porcelain` in the
    project is empty after creating a proposal; a `project_restore` to a
    pre-proposal snapshot leaves `proposal.json` byte-identical (**AC9**, also
    re-asserted in Slice 6).
  - **Policy and gates (FR11).** Defaults `{approvals_required: 1,
    self_approve: false}`; a `policy.json` overrides them; the author's own
    approval does not count under the default; the latest verdict per actor is
    the one that counts (approve → request_changes stops counting); a
    `changes_requested` state makes the `state` gate `fail`; `specs` and
    `checks` are `skipped` with an empty `service.gate_providers`; a provider
    that raises degrades to `pending` and never propagates.
  - **Stale reviews.** A review records `source_head`; when the branch moves,
    `get` marks it `"stale": true` and the approvals gate summary says so, but
    it still counts in v1.

- [ ] **Step 2: run to verify failure** —
  `uv run pytest tests/test_proposals.py -q` → import error.

- [ ] **Step 3: implement `agentcad/core/proposals.py`.** One
  `threading.RLock` per manager (the `MergeOrchestrator` precedent). All paths
  from `store.canonical_path_of(proj)`, **never `path_of`**. Sidecar writes via
  `ProjectStore._atomic_write`; `audit.jsonl` appended with
  `open(path, "a", encoding="utf-8")` + `flush()`, with the reason in a
  comment. Branch names resolved with `history.resolve_branch` (never
  `resolve_ref` — a tag must not answer for a branch). `service.branches` is
  read inside methods, never in `__init__` (pack load order, see Slice 2).
  No git writes, no kernel calls, no imports from `merge.py`/`packet.py`.

- [ ] **Step 4: run** — `uv run pytest tests/test_proposals.py -q` → all pass;
  then `uv run pytest -q -m "not slow"` to prove nothing else moved.

- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0077-proposal-store-and-lifecycle.md` (recompute `NNNN`),
  written from `git diff`. In the same commit, fix the stale status metadata:
  `docs/roadmap.md`'s PRD-002 row still says `pending` and links
  `prd/pending/…` while the file lives in `docs/prd/in-progress/`, and the
  PRD's own `- **Status:** pending` line needs the same correction (the PRD
  README's rule is that location, row and `Status:` move together).

**Verification command:** `uv run pytest tests/test_proposals.py -q`

---

## Slice 2 — tool pack, route pack, events, gated merge (FR10–FR12, FR15)

**Files**
- Create: `agentcad/core/tools_proposals.py`
- Create: `agentcad/server/routes_proposals.py`
- Create: `tests/test_proposals_api.py`
- Modify: `tests/test_proposals.py` (merge-gate cases)

**Interfaces produced**

Tools `proposal_create`, `proposal_list`, `proposal_get`, `proposal_update`,
`proposal_review`, `proposal_merge`. Routes as listed in the design spec minus
the packet/render/diff three (Slice 4). Event `proposal_changed {project, id,
state, reason}`. Service seams: `service.proposals`, `service.gate_providers`.

### Task 1 — the pack, registration and load order

- [ ] **Step 1: failing tests** in `tests/test_proposals_api.py`, laid out in
  the three sections `test_versioning_api.py` uses:
  - every tool registered; `input_schema["type"] == "object"`; `project` in
    both `properties` and `required`; a non-empty description;
  - a **description-contract** test: the descriptions must name `old`/`new` as
    target/source (the PRD-001 ours/theirs convention, restated), must name the
    follow-up tool for the conflict path (`resolve_merge`), and must say that
    `allow_invalid` overrides the kernel validation gate **only**;
  - argument validation → `invalid_arguments` for a missing required arg, a
    wrong type, and an unknown key;
  - **self-disable**: with `ProjectHistory.available` monkeypatched to
    `False`, no `proposal_*` tool is registered and `service.proposals` is
    absent;
  - **load order**: building the registry and immediately calling
    `proposal_create` works — i.e. the pack does not touch `service.branches`
    at `register()` time (`tools_proposals` is imported *before*
    `tools_versioning`).
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `tools_proposals.register`:**
  ```python
  def register(registry, service) -> None:
      if not service.history.available():
          return          # no git -> no branches -> no proposals
      service.proposals = ProposalManager(service)
      service.gate_providers = []
  ```
  Handlers are thin delegations to `service.proposals`, matching
  `tools_versioning`'s shape (inner functions first, `registry.register(Tool(…))`
  calls after, `_PROJ` and a shared `_SIDES` prose constant at module level).
  Descriptions state the returned payload inline, name the error types by name,
  and repeat the old = target / new = source convention.
- [ ] **Step 4: run** — `uv run pytest tests/test_proposals_api.py -q -k registration`.

### Task 2 — the branch-delete guard (FR2)

- [ ] **Step 1: failing test** — with an open proposal on `feat`,
  `branch_delete {name: "feat"}` is a `conflict_error` naming the proposal id;
  the same for the *target* branch; after closing the proposal the delete
  succeeds; with no proposals the behavior is byte-identical to today
  (`tests/test_branches.py` must pass untouched).
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** by wrapping `service.branches.delete` **lazily**
  (on first proposal-manager use, not at `register()` time — `service.branches`
  does not exist yet then), the way `install_write_guard` rewires the store
  guard. Idempotent: wrapping twice must not double-wrap.
- [ ] **Step 4: run** — `uv run pytest tests/test_branches.py tests/test_proposals.py -q`.

### Task 3 — `proposal_merge` over PRD-001 (FR10, FR12)

- [ ] **Step 1: failing tests** (in `tests/test_proposals.py`, `slow` where the
  kernel is involved):
  - **Happy path:** an approved proposal merges; the result is
    `MergeOrchestrator.merge`'s payload plus `{proposal, gates}`; the proposal
    is `merged` with `merge.commit` equal to the merge commit; the commit has
    **two parents**; the audit has a `merged` entry.
  - **AC5 — blocked validation:** a proposal whose merge introduces
    interference is refused with the PRD-001 `validation_error` carrying
    `details.validation`; the proposal state does **not** change; the audit
    records `merge_attempted` with `outcome: "blocked"`; re-running with
    `allow_invalid: true` lands it, and the override appears in **all three**
    places — the audit (`override` entry), `proposal.merge.allow_invalid`, and
    the merge commit message (`Validation: FAILED (allow_invalid)`).
  - **AC6 — policy:** under defaults, merging with zero approvals is a
    `conflict_error` naming the policy in `details.gates`; the author's own
    approval does not satisfy it; `allow_invalid: true` does **not** bypass it
    (assert explicitly — this is the decision most likely to be "fixed"
    wrongly later); one approval from another identity lets it through.
  - **Conflict pass-through:** a proposal whose merge conflicts returns the
    `merge_conflict` payload **verbatim** plus `details.proposal`; the proposal
    state is unchanged; `merge_status` shows the staged merge; resolving with
    `resolve_merge` and calling `proposal_merge` again completes it.
  - **Draft:** merging a `draft` is a `conflict_error`.
  - **Freeze (FR12):** after a merge, a `packet.json` present on disk is
    marked `frozen` and `proposal_packet {regenerate: true}` (Slice 4) will
    refuse to regenerate — assert the flag here, the refusal in Slice 4.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** `ProposalManager.merge` per the design spec's
  Decision 7: gates → refuse on `fail` **before** merging → `service.merges.merge(...)`
  → branch on success / returned `merge_conflict` / raised `validation_error`.
  Add nothing to locking: `MergeOrchestrator._holding_target` already holds the
  target's turn across validation and finalization.
- [ ] **Step 4: run** — `uv run pytest tests/test_proposals.py tests/test_merge.py -q`.

### Task 4 — routes and events

- [ ] **Step 1: failing tests** in `tests/test_proposals_api.py`:
  - each route returns the tool payload verbatim; `merge_conflict` arrives at
    HTTP **200** with an `{"error": …}` body while `invalid_arguments`, a
    kernel error and any unmapped type are **422**; 404 for an unknown
    proposal, 409 for a duplicate/red gate;
  - unknown body keys ignored and `null` not forwarded (post
    `{"evil": "…", "project": "other", "description": None}` and still get 200);
  - **AC8:** `proposal_changed` observed on a `service.bus.subscribe()` queue
    for create, update, review and merge, with the documented field set, and
    over a real WebSocket with `extra_allowed_hosts={"testserver"}`;
  - `proposal_changed` does **not** trigger a git snapshot (it is not
    `project_changed`) — assert the history length is unchanged;
  - **AC9:** `project_restore` to a pre-proposal snapshot leaves the proposal
    readable and unchanged.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `routes_proposals.build_router`,** copying
  `routes_versioning`'s `_RAISE` / `_BODY_ERRORS` / `_result` / `_body_keys` /
  `_json` helpers verbatim, with the module docstring as a route table.
  Early-return an empty router when `registry.get("proposal_list") is None`.
  Whitelist every body key explicitly — never `**body`.
- [ ] **Step 4: run** — `uv run pytest tests/test_proposals_api.py tests/test_server.py tests/test_tools.py -q`, then `make test`.

- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0078-proposal-surface-and-gated-merge.md` (recompute `NNNN`).

**Verification command:** `make test` — cite the count and confirm every
pre-existing test passed unedited.

---

## Slice 3 — `geom_diff` kernel handler + explicit render frame

**Why now:** two small primitives with hard geometry gotchas, testable on their
own, that Slice 4 consumes. Landing them separately means a boolean surprise
surfaces before the packet is built on top of it.

**Files**
- Create: `agentcad/kernel/handlers/diff.py`
- Modify: `agentcad/core/render.py` (**one additive keyword argument**)
- Create: `tests/test_geom_diff.py`
- Modify: `tests/test_render.py` (framing cases)

**Interfaces produced**

```python
# kernel method "geom_diff"
params: {"old": item|None, "new": item|None,
         "added_path": str|None, "removed_path": str|None, "tolerance": float}
        # item = {"script", "params"} | {"source": "<path>"}
result: {"added_mm3", "removed_mm3", "old_volume_mm3", "new_volume_mm3",
         "added_triangles", "removed_triangles", "skipped_mesh"?}

# agentcad/core/render.py
def render_acm(meshes, view="iso", width=800, height=600,
               frame: dict | None = None) -> bytes
```

### Task 1 — the render frame (FR6)

- [ ] **Step 1: failing tests** in `tests/test_render.py`:
  - `frame=None` produces **byte-identical** output to today for an existing
    fixture (guard against an accidental behavior change);
  - two different shapes rendered with the same `frame`, `view` and size place
    a common feature at the same pixel — assert by rendering a small marker
    solid at a fixed world position in both scenes and finding the same
    non-background pixel, or by unit-testing an extracted
    `_frame_extents(frame, view) -> (center, span)` helper directly;
  - a `frame` smaller than the geometry still renders (clipping is allowed,
    crashing is not); a degenerate (zero-size) frame does not divide by zero.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement.** Project the frame's 8 corners through the existing
  `_camera_basis(view)` and use their 2-D extents in place of the per-mesh
  `mins`/`maxs` at `render.py:129-130`. Nothing else changes. Document in the
  docstring that the frame is a **world-space** bbox `{"min": [...], "max":
  [...]}` and that omitting it keeps the auto-fit.
- [ ] **Step 4: run** — `uv run pytest tests/test_render.py -q`.

### Task 2 — the `geom_diff` handler (FR7)

- [ ] **Step 1: failing tests** in `tests/test_geom_diff.py` (session `kernel`
  fixture, `slow`):
  - **AC3 core:** a 20 mm cube vs the same cube with a ⌀6×20 hole →
    `removed_mm3` within **1 %** of `π·3²·20`, `added_mm3 == 0.0`;
  - the reverse direction (hole filled) reports the volume as `added_mm3`;
  - **identical inputs** → both volumes `0.0` and no mesh files written;
  - **multi-solid parts:** a two-solid part differenced against a variant
    reports the summed solid volume — this is the `Compound.volume`
    undercount trap; assert against a hand-computed number, not against
    `.volume`;
  - **mesh-kind reference:** an STL `source` on either side comes back in
    `skipped_mesh` with both volumes `0.0` and **no boolean attempted** (the
    STL-boolean segfault); the process is still alive afterwards;
  - **failure is structured:** a script that builds but whose difference
    raises comes back as a `KernelError` payload with
    `details.stage in {"added", "removed"}`, not as a crash or a hang;
  - **meshes:** when `added_path`/`removed_path` are given and the volume is
    non-zero, a readable ACM1 file is written (`acm.read` parses it, triangle
    count matches the reported `*_triangles`); a zero volume writes no file.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `agentcad/kernel/handlers/diff.py`** as a
  `register(toolbox)` pack returning `{"geom_diff": geom_diff}`. Use
  `toolbox["build_shape"]` (and the reference loader for `source` items,
  mirroring `worker._item_shape`'s split), `-` for the differences,
  **`toolbox["shape_volume"]`** for every volume (never `.volume`),
  `toolbox["tessellate"]` (which takes `shape.wrapped`) plus
  `toolbox["atomic_write"]` for the meshes, and
  `toolbox["WorkerError"](toolbox["ERROR_KERNEL"], …, {"stage": …})` for
  failures. Guard each boolean in its own `try/except Exception`. Comment the
  three traps (solids-sum volume, mesh booleans segfault, difference may return
  a nested Compound).
  **If `-` misbehaves on a multi-solid operand** (the way `&` does — see
  `worker.pairwise_interference`'s docstring), decompose to solids and
  difference per-solid, summing volumes; decide from the failing test, not from
  the docs.
- [ ] **Step 4: run** — `uv run pytest tests/test_geom_diff.py tests/test_kernel.py -q`.

- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0079-geometric-diff-kernel-handler.md` (recompute `NNNN`).
  Record the measured boolean timings for the rocketry nozzle and for a
  `examples/surfacing` part — the design spec asks for evidence, not a guess.

**Verification command:** `uv run pytest tests/test_geom_diff.py tests/test_render.py -q`
plus `make test`.

---

## Slice 4 — the review packet (FR4–FR8, the head-pinning half of FR9)

**Files**
- Create: `agentcad/core/packet.py`
- Modify: `agentcad/core/tools_proposals.py` (`proposal_packet`,
  `proposal_render`)
- Modify: `agentcad/server/routes_proposals.py` (packet, render, diff routes)
- Create: `tests/test_packet.py`
- Modify: `tests/test_proposals_api.py` (the three new routes)

**Interfaces produced**

```python
# agentcad/core/packet.py
class PacketBuilder:
    def __init__(self, service): ...
    def build(self, proj: str, proposal: dict) -> dict
    def load(self, proj: str, proposal: dict) -> dict | None   # persisted, staleness-marked
def changed_parts(old_manifest: dict, new_manifest: dict,
                  changed_scripts: set[str]) -> list[dict]      # pure
def params_delta(old_entry: dict, new_entry: dict) -> dict      # pure
def assembly_delta(old_asm: dict, new_asm: dict) -> dict        # pure
def metric_delta(old: dict, new: dict) -> dict                  # pure
```

### Task 1 — the pure delta functions

- [ ] **Step 1: failing tests** in `tests/test_packet.py`, plain dicts, no
  kernel, no git:
  - `changed_parts`: added / removed / modified classification; a part changed
    only in the manifest (params) and one changed only in its script bytes are
    both `modified` with the right `changed_by`; a part present on neither side
    never appears.
  - `params_delta`: params added/removed; per-field changes
    (`default`/`min`/`max`/`type`/`unit`/`choices`/`description`) reported as
    `old → new`; **`6` and `6.0` are different values** (the
    `manifest_merge._norm` type-qualified comparison), matching how
    `_normalize_param` stores them.
  - `assembly_delta`: instances added/removed/moved; a mate added, changed and
    cleared; `total_mass_g` delta with `pct` `null` when the old value is 0.
  - `metric_delta`: `{old, new, delta, pct}`; `pct` `null` at zero; per-axis
    `center_of_mass` delta; `bbox` reported as both boxes plus
    `size_delta_mm`; a part present on one side only reports `null` for the
    absent side.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** the four pure functions. Stdlib only; no imports
  from `service`, `merge`, `branches` or the kernel.
- [ ] **Step 4: run** — `uv run pytest tests/test_packet.py -q -k "not slow"`.

### Task 2 — the builder

- [ ] **Step 1: failing tests** (real service + kernel; `slow`):
  - **Two sides from the branch worktrees.** A packet for a param change
    contains the script diff (or none, when only params changed), the PARAMS
    diff, metric deltas for the changed part, and `source_head`/`target_head`
    equal to the two branch heads.
  - **AC4 — the content-hash short-circuit.** A move-an-instance-only change
    produces assembly deltas and **zero** per-part kernel work: count
    `kernel.request` calls with the monkeypatch-a-counting-`request` pattern
    from `tests/test_history.py` and assert no `build` and no `geom_diff`; the
    part's `geom_diff` reads `{"available": true, "unchanged": true}`.
  - **AC7 — honest degradation.** A source side whose script raises yields
    `build.new.ok is False` with the structured error (traceback + line), the
    rest of the packet intact, and `packet["ok"] is True`.
  - **Geometric diff wired end to end.** A hole-drilling change reports
    `removed_mm3` within 1 % (**AC3**'s testable half) and writes a
    `diff/<part>.removed.acm` that `acm.read` parses.
  - **Matched renders.** Both sides' PNGs exist, are the same size, decode as
    PNG, and were produced with the same `frame` (assert the frame recorded in
    the packet is the union of the two bboxes).
  - **Binary paths.** A changed file under `imports/` appears in `binary` with
    `{bytes, sha256}` per side and **no bytes**; it is never fed to a diff or a
    boolean.
  - **Staleness (FR9 partial).** A second `build` with unmoved heads returns
    the persisted packet without regenerating (count kernel calls); moving the
    source head marks `stale: true` and a view regenerates; a `frozen` packet
    (post-merge) refuses `regenerate: true` with a `conflict_error`.
  - **Dirty tree.** A branch tree with uncommitted changes that cannot be
    snapshotted is a `conflict_error` — never a packet pinned to heads that do
    not describe the measured bytes.
  - **Unbuildable manifest.** A `project.json` that exists but does not parse
    on either ref is a `validation_error` naming the ref and the file (the
    `merge._manifest_at` rule, re-implemented for refs, not re-used).
  - **AC2 — the budget.** On a copy of `examples/rocketry`, a warm packet for a
    nozzle wall-thickness change completes in **< 10 s** and contains the
    script diff, PARAMS diff, metric deltas, both renders and the geometric
    diff volumes. Mark `slow` + `pytest.mark.timeout(600)`; warm the cache by
    building both sides first, exactly as the AC says ("generates warm").
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement `PacketBuilder`** per the design spec's Decision 4:
  `branches.tree_of` for both sides, `branches.pinned` around every service
  call, `history._run` for `git diff`, the pure functions from Task 1,
  `service.get_metrics` / `service.get_assembly` / `service.ensure_mesh`,
  `service._cache_key_for` under each pin for the short-circuit,
  `service.kernel.request("geom_diff", …, timeout_s=300.0, affinity=part_id)`,
  and `render_acm(..., frame=…)` at 640×480 `iso`. Every per-part stage is
  wrapped so a failure degrades to a `null` section plus an `errors` entry.
  **No OCP import anywhere in this file.**
- [ ] **Step 4: run** — `uv run pytest tests/test_packet.py -q`.

### Task 3 — tools and routes

- [ ] **Step 1: failing tests** in `tests/test_proposals_api.py`:
  `proposal_packet {regenerate?}` returns the packet; `proposal_render {side,
  part?, view?}` returns a single `png_base64` (so MCP/chat lift it into image
  content — assert the key is top-level, which is what
  `mcp_server._tool_result` keys on); `GET …/render/{side}/{part}` returns
  `image/png` bytes with `Cache-Control: no-store`; `GET …/diff/{part}/{kind}.acm`
  returns `application/octet-stream` starting with `ACM1`; an unknown part or a
  missing diff mesh is a 404.
- [ ] **Step 2: run to verify failure.**
- [ ] **Step 3: implement** the two tools and three routes, following
  `routes_vision.py` for the PNG response and the mesh route in `app.py` for
  the ACM one.
- [ ] **Step 4: run** — `uv run pytest tests/test_proposals_api.py tests/test_mcp.py -q`, then `make test`.

- [ ] **Step 5: changelog + commit** —
  `docs/changelog/0080-proposal-review-packet.md` (recompute `NNNN`). Record
  the **measured** AC2 timing and the on-disk size of the rocketry packet.

**Verification command:** `make test` (cite the count) plus
`uv run pytest tests/test_packet.py -q`.

---

## Slice 5 — frontend: proposals list/detail, diff view, geometry overlay

**Files**
- Modify: `frontend/index.html` (toolbar button, `#proposals-modal`)
- Modify: `frontend/js/api.js` (a `// ---- proposals ----` section)
- Modify: `frontend/js/state.js` (`proposals`, `proposal`)
- Modify: `frontend/js/main.js` (`setupProposals`, one WS case, `actions` wiring)
- Modify: `frontend/js/viewport.js` (`showDiffOverlay` / `clearDiffOverlay`)
- Create: `frontend/js/proposals.js`
- Modify: `frontend/css/app.css` (`prop-*`, `diff-*`, `gate-*`, `.modal.wide`)

- [ ] **Step 1: `api.js`** — one-line arrows over the module-private `enc()`:
  `listProposals`, `createProposal`, `getProposal`, `updateProposal`,
  `getPacket`, `reviewProposal`, `mergeProposal`, plus URL builders for the
  render and diff endpoints (`getDiffMesh` follows `getMesh`'s hand-rolled
  ArrayBuffer shape). Repeat the dual-error comment `api.js:97-99` already
  carries: these routes answer HTTP 200 with an `{"error": …}` body for
  `merge_conflict`, so callers check `res.error` **in addition to** catching
  `ApiError`.

- [ ] **Step 2: `index.html`** — a `#proposals-btn.tb-btn` with a
  `#proposals-count` badge in the toolbar, and `#proposals-modal.modal-overlay.hidden`
  alongside `#merge-modal`, following `#drawing-modal`'s
  `.modal-overlay > .modal > .modal-head + body (+ .modal-foot)` structure with
  `class="modal wide"`. **It must be static markup** — `setupMenus()` snapshots
  `.menu-wrap` elements once at boot, and modal handlers are wired at init.

- [ ] **Step 3: `proposals.js`** — `init(actions)` + `open()` + `isOpen()` +
  `handleEvent(ev)`, wired exactly like `versions.js`/`merge.js` (close button,
  backdrop click, Escape). Master/detail: the list on the left (state chip,
  `#id`, title, author + human/agent badge, relative age, gate dot); the detail
  on the right with the four tabs. Build content with `document.createElement`
  + `textContent` — never `innerHTML` for data. A module `busy` flag guards
  double-submit; buttons disable and relabel ("Merging…") like `merge.js`.
  Reuse `merge.js`'s validation-report block for the Checks tab rather than
  writing a second one.

- [ ] **Step 4: the Files tab** — a plain-DOM unified diff: one
  `<div class="diff-line">` per line with `.diff-add` / `.diff-del` /
  `.diff-hunk` / `.diff-ctx`, in an `overflow-x: auto` container, carrying
  `data-part` / `data-hunk` / `data-line` attributes (unused now; PRD-008's
  anchors). **Do not vendor a CodeMirror merge addon** — the frontend is
  offline-only and adding one means editing `scripts/vendor_frontend.sh`.

- [ ] **Step 5: the Geometry tab** — `viewport.showDiffOverlay(partId, buffer,
  key, kind)` / `clearDiffOverlay()`, modelled **exactly** on
  `highlightFace`/`clearFaceHighlight` (`viewport.js:371-420`): a separate mesh
  parented to the **scene root, not `contentGroup`**, translucent
  (`transparent: true`, `depthWrite: false`), with its own dispose path,
  cleared by `clearContent()` and on part switch. Colors from theme tokens via
  the existing `setTheme` path. `scene`/`contentGroup` stay module-private.

- [ ] **Step 6: `main.js`** — `setupProposals()` (button, badge refresh on
  project load and on `proposal_changed`), `proposals.init(actions)` next to
  `versions.init(actions)`, and one case in `handleEvent()`'s switch:
  ```js
  case "proposal_changed":
    if (ev.project !== state.projectName) return;
    proposals.handleEvent(ev);
    return;
  ```

- [ ] **Step 7: CSS** — a `/* --- proposals modal --- */` block after the merge
  block, using **only** existing tokens (`--panel`, `--hairline`, `--dim`,
  `--accent*`, `--ok*`, `--err*`, `--raised`, `--mono`, `--scrim`,
  `--shadow-modal`) so light mode keeps working; add
  `.modal.wide { width: min(1100px, 100%); }`. No new token.

- [ ] **Step 8: verify in the real browser** (use the **`run` skill**; this is
  AC1's and AC3's browser half and the definition of done): create a branch and
  edit the rocketry nozzle from a chat session, open the proposal in the
  browser, read all four tabs, approve and merge — **zero terminal use**; then
  drill a hole on a branch and confirm the red removed-volume overlay renders
  in the Geometry tab. **Zero console errors.** Screenshot each of: the
  proposals list, the Overview tab with the render pair, the Files tab, the
  Geometry tab with the overlay, and the Checks tab with a red gate.

- [ ] **Step 9: changelog + commit** —
  `docs/changelog/0081-proposals-ui.md` (recompute `NNNN`), with the
  screenshots and the clean console on the record (the AC1/AC3 tests in Slice 6
  assert that this evidence exists, exactly as `test_prd001_acceptance.py` does
  for AC6).

**Verification command:** `make test` plus the browser session above with
screenshots and a clean console.

---

## Slice 6 — docs, acceptance criteria, PRD close-out

**Files**
- Modify: `docs/agent-api.md` (8 new tools with schemas; the packet shape; the
  `proposal_changed` event; the routes; bump the tool count from 52/55)
- Modify: `docs/architecture.md` (the `.history/agentcad/proposals/` layout, the
  packet data flow, the `geom_diff` handler, the gate-provider seam)
- Modify: `docs/user-guide.md` (the proposals modal, surface by surface)
- Modify: `AGENTS.md` (proposal traps: proposals are canonical and
  branch-independent; `audit.jsonl` is appended, not atomically replaced;
  `geom_diff` volumes come from the solids sum; renders need an explicit frame;
  `allow_invalid` overrides only the kernel gate)
- Modify: `docs/roadmap.md` (PRD-002 row → completed, link corrected) and move
  `docs/prd/in-progress/PRD-002-change-proposals-geometric-diff.md` →
  `docs/prd/completed/`, updating its `Status:` line **in the same commit**
- Modify: the PRD itself with the eight **divergences to fold back** listed at
  the end of the design spec
- Create: `tests/test_prd002_acceptance.py`
- Create: `docs/changelog/0082-proposals-docs-and-acceptance.md`

**Acceptance criteria → concrete tests** (one named test per criterion,
mirroring `tests/test_prd001_acceptance.py`, with the `| AC | Test |` table in
the module docstring)

| AC | Test | Assertion |
|---|---|---|
| AC1 | `test_ac1_roundtrip_agent_proposes_human_merges` — `examples/rocketry` **on a copy** | an agent identity (`chat:main`) creates a branch, edits, and opens a proposal through tools; a `browser` identity reviews and merges through the **routes**; `proposal_get`'s audit shows `created`/`packet_generated` as `agent` and `reviewed`/`merged` as `human`. The zero-terminal browser half is Slice 5's session; this test asserts that evidence is on the record. |
| AC2 | `test_ac2_packet_generates_warm_under_10s` | on a rocketry copy, warm packet for a nozzle wall change < 10 s and carries script diff + PARAMS diff + metric deltas + both renders (same frame) + geometric diff volumes |
| AC3 | `test_ac3_drilled_hole_reports_removed_volume` | `removed_mm3` within 1 % of the analytic hole volume; a `diff/<part>.removed.acm` parses. The overlay half is Slice 5's screenshot |
| AC4 | `test_ac4_instance_move_does_no_per_part_kernel_work` | assembly deltas present; **zero** `build`/`geom_diff` kernel calls; every part's `geom_diff.unchanged is True` |
| AC5 | `test_ac5_failed_validation_blocks_then_overrides` | blocked with `details.validation`; `allow_invalid: true` lands it; the override recorded in the audit, on the proposal, and in the merge commit message |
| AC6 | `test_ac6_self_approval_does_not_satisfy_policy` | merging with zero approvals and with only the author's approval are both `conflict_error`s naming the policy; `allow_invalid` does not bypass it |
| AC7 | `test_ac7_unbuildable_side_degrades_honestly` | the failing part carries the structured script error; the rest of the packet is intact; `packet["ok"] is True` |
| AC8 | `test_ac8_second_client_sees_proposal_changed_live` | WS test with `extra_allowed_hosts={"testserver"}`; create → review → merge transitions all observed |
| AC9 | `test_ac9_project_restore_does_not_rewind_proposals` | snapshot, create a proposal, mutate, restore the pre-proposal snapshot: `proposal.json` and `audit.jsonl` byte-identical; full suite green |

- [ ] **Step 1:** write the AC tests (they are the last failing tests; they
  should pass once Slices 1–5 are in).
- [ ] **Step 2:** update the docs above, matching each file's existing style.
  `docs/agent-api.md` needs a "Change proposals" section next to "Branches,
  versions and merges", stating: the old = target / new = source convention,
  that `actor_kind` is bookkeeping and not authentication until PRD-005, that
  `allow_invalid` overrides only the kernel validation gate, that renders come
  back as URLs from `proposal_packet` and as image content from
  `proposal_render`, and that packet-internal failures are payload fields, not
  errors.
- [ ] **Step 3:** `make test` → green; **record the exact count** (510 passed,
  1 skipped before this work).
- [ ] **Step 4:** `make test-portability` → green (every new git-touching test
  carries the `portability` marker).
- [ ] **Step 5:** write the changelog from `git diff`, move the PRD to
  `docs/prd/completed/`, update the roadmap row, fold the divergences back into
  the PRD, and commit.

**Verification command:** `make test` and `make test-portability`, both green,
with the counts cited; plus `git diff --name-status main -- tests/` showing only
additions (`A`) of the new test files.

---

## Rollback / landing notes

- **Slice 1 is inert on its own**: `proposals.py` is imported by nothing until
  Slice 2's pack exists, so landing it changes no behavior.
- **The whole feature self-disables when `git` is not on PATH**: the pack
  registers no tools, `service.proposals` is never created, and
  `routes_proposals.build_router` returns an empty router — the same graceful
  boundary PRD-001 already has (`tests/test_history.py::test_git_missing_degrades_gracefully`).
- **Slice 3 is independently useful and independently revertible**: `geom_diff`
  is a handler nobody calls until Slice 4, and `render_acm(frame=None)` is
  byte-identical to today.
- **If the B-rep boolean diff proves unreliable** on real parts (the design
  spec's first risk), the packet still ships: `geom_diff.available: false` with
  metrics, diffs and renders intact is the designed degradation. A mesh-space
  diff is the fallback — but it is a *new* slice, not a patch to this one, and
  only after the measurement in Slice 3's changelog.
- **If AC2's 10 s budget is missed**, the levers in order are render size, then
  rendering the assembly only when it changed. Dropping the geometric diff is
  not a lever — it is the PRD's differentiator.
