# 0361 — PRD-018 slice 4: generate_part/accept_candidate tools, provenance, accept

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Nikita Fedorov

## Summary
Wires slices 1-3 (the generation loop, intent normalization, intake) into the
public tool + route surface: an API-key-gated `tools_generate` pack exposing
`generate_part`, `accept_candidate`, `list_generations` and
`generation_status`, plus FR11 provenance surfaced on `get_part`, a
scratch-part listing guard, and the FR12 proposal-or-direct accept path.

## Changes
- `core/tools_generate.py` (new pack, sorts before proposals/specs/versioning,
  so `service.proposals`/`service.specs`/`service.branches` are read lazily in
  handlers):
  - `generate_part {project, prompt, images?, files?, candidates?, budget?}` —
    resolves uploaded import filenames through `core/intake.prepare_vision`,
    derives an intent record (`agent/intent.normalize_intent`), freezes its
    draft specs, folds the FENCED untrusted document text + the standards/
    document rules into the loop prompt, and drives `agent/generate.run_generation`
    over the intent + prepared vision. Returns the per-candidate results, the
    intent record and `draft_specs` (FR2), and persists a **generation record**
    under the top-level manifest loose key `generations`.
  - `accept_candidate {project, generation_id, candidate, part_id?, propose?}` —
    rename-via-recreate (read the scratch part's script/params, `create_part`
    at the target id, `set_params`, stamp the `generated` loose key), delete
    every scratch id of the gen, and land either directly (default/local) or
    via a `gen/<id>` proposal branch (hosted+git, or `propose:true`). Enforces
    the frozen-spec contract (FR8): a candidate that weakened or deleted a
    frozen intent-spec is **refused**.
  - `list_generations` / `generation_status` — read the persisted records; the
    shape is synchronous (`background:false`, PRD-020 async deferred).
  - `install_generated_provenance(service)` — UNconditional `get_part` wrapper
    (the `install_rebuild_specs` pattern) surfacing `generated` on a generated
    part, so provenance survives even after the key is removed (AC5).
  - `install_scratch_listing_guard(service, SCRATCH_PREFIX)` — hides in-flight
    `gen_*` scratch parts from `get_project`'s part list and corrects
    `list_projects`' `n_parts`; installed only with the (key-gated) tools.
  - `GenerationUnavailable` (ValidationError → 422), the ChatUnavailable twin.
  - `_await` — runs the async loop from a sync tool handler: `asyncio.run`
    with no running loop, else a worker thread under a copied context (so the
    HTTP async route's tenant/identity reach the loop).
- `server/routes_generate.py` (new) — `POST /projects/{proj}/generate`: a thin
  convenience over the tool that returns the honest `generation_unavailable`
  422 when the pack is unconfigured (the tool would otherwise 404). Uploads
  reuse `POST .../imports`; progress rides the existing bus/WS.
- Tests: `tests/test_tools_generate.py` (14) and
  `tests/test_generation_provenance.py` (2).

## Files
- `agentcad/core/tools_generate.py` — new tool pack + two service seams
- `agentcad/server/routes_generate.py` — new route pack (unavailable shape)
- `tests/test_tools_generate.py` — end-to-end generate/accept, gating, frozen
  spec refusal, proposal-vs-direct, the untrusted-document rule, the route
- `tests/test_generation_provenance.py` — provenance survives project_restore,
  generated part is an ordinary script part (AC5)

## Notes
- **Generation-record storage:** a top-level manifest loose key `generations`
  (not a sidecar) — it round-trips `_read_manifest`/`_write_manifest` untouched
  and is git-tracked, so `list_generations` survives `project_restore` for free.
- **Scratch cleanup vs the guard:** `generate.cleanup_scratch` iterates
  `service.get_project`, which the listing guard wraps to HIDE scratch parts —
  so accept cleans up via a raw-manifest `_cleanup_scratch` scoped to the gen's
  own `gen_<id>_` prefix (never a sibling generation's in-flight candidates).
- **provenance.by vs the git trailer:** `by` records the human/agent who
  accepted (captured before the geometry write), while the write itself runs
  under the `gen:<id>` identity so the audit/trailer attributes the generator
  (Decision 9).
- **Caveat (S1 limitation):** `agent/generate._initial_content` hard-codes
  `media_type: image/png` when it builds image blocks. PNG uploads and every
  rasterized PDF page are PNG and correct; a JPEG upload would be mislabeled
  png to the model. `core/intake` states the true `media_type` and it is
  passed through in the images dicts and now honored by the block builder
  committed loop, which this slice does not edit. Not exercised by any required
  test; noted for a future `generate.py` fix.

## Notes
`make test` — 7199 passed, 51 skipped (34:02, box shared with a concurrent session); the non-passing were the count-guards reading the pre-commit newest changelog (this entry cites the count), the documented test_releases/supervisor load timeouts (25/25 pass in 66 s in isolation) and the pre-existing prd028 AC6 FEM timeout. This commit also folds a one-line fix to `generate._initial_content` (honor intake's `media_type` instead of hardcoding image/png — a JPEG upload was mislabeled).
