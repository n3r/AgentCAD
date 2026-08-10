# 0080 — the proposal review packet

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
Slice 4 of PRD-002: the evidence. `core/packet.py` generates a proposal's
review packet from PRD-001's two branch worktrees — script and PARAMS diffs,
per-part metric deltas, assembly deltas, a frame-matched before/after render
pair and kernel-computed added/removed volumes — persists it with both branch
heads, and serves it until a head moves. Two tools (`proposal_packet`,
`proposal_render`) and four routes (packet, two render, one ACM asset) expose
it. This lands **AC2** (the warm rocketry packet: measured **0.97 s** against a
10 s budget), **AC4** (the content-hash short circuit does zero kernel work) and
**AC7** (an unbuildable side degrades with its structured error, packet still
`ok`).

## Changes
- **New module `agentcad/core/packet.py`.**
  - Four pure functions, no I/O: `changed_parts` (added/removed/modified with
    `changed_by ∈ script|params|manifest`, mirroring `merge._changed_parts`'s
    rule against refs rather than a staged tree oid), `params_delta`
    (added/removed/changed with a **type-qualified** comparison, so `6` and
    `6.0` differ exactly as `service._normalize_param` stores them),
    `assembly_delta` (instances added/removed/moved from *resolved* transforms,
    mates added/changed/cleared, `total_mass_g` with `pct` null at zero) and
    `metric_delta` (`{old, new, delta, pct}` per scalar, a per-axis
    center-of-mass delta, both bboxes plus `size_delta_mm`).
  - `PacketBuilder.build()` resolves both sides through `branches.tree_of`,
    **checkpoints each tree and refuses a dirty one** (`conflict_error`) before
    reading heads, so a packet's pinned SHAs always describe the bytes it
    measured; reads both manifests with `merge._manifest_at`'s strictness (a
    `project.json` that exists but does not parse is a `validation_error` naming
    the ref, never `{}`); splits one `git diff --unified=3 … -- parts/` per path
    with hunk anchors for PRD-008; and runs every measurement through the
    ordinary service methods under `branches.pinned`, so the canonical
    content-addressed `.cache/` makes unchanged parts free on both sides.
  - **The content-hash short circuit is in the service, not the kernel:**
    `service._cache_key_for` computed under each pin; equal ⇒
    `geom_diff {"available": true, "unchanged": true}` with no kernel call at
    all (AC4).
  - `geom_diff` calls go out with `timeout_s=300.0, affinity=<part>`; a
    `KernelError` degrades to `available: false` + an `errors` entry, a
    non-empty `skipped_mesh` (or an STL side detected from `metrics.mesh`)
    degrades to `skipped: "mesh"` with no boolean attempted, and a side that
    does not build skips the boolean entirely. A zero-volume direction writes no
    `.acm`, so its mesh URL is `null`; stale meshes from a previous generation
    are unlinked before each run.
  - Renders: both sides at 640×480 `iso` (smaller than `render_view`'s 800×600
    — the rasterizer is pure Python, so resolution is the budget's dominant
    knob) through `render_acm(..., frame=…)` with **one frame**, the union of
    both world bboxes inflated 2 %; the frame is recorded in the packet.
    Renders are written as PNG assets and published as **URLs**.
  - Honest degradation throughout (FR8): per-part stages are individually
    wrapped, failures land in `warnings`/`errors`, and `ok` stays `true`.
    A **mesh-kind reference reports no center-of-mass delta** — `build_reference`
    fills that field with the bbox center, and a delta of that is not a mass
    property — with a warning saying so.
  - Head pinning (the MVP half of FR9): `load()` re-marks `stale` against the
    branches' current heads, `packet()` regenerates on view or on
    `regenerate: true`, and a packet frozen by the merge (FR12) refuses
    `regenerate` with a `conflict_error`.
  - `render()` returns one image (`png_base64`) for a part or the whole
    assembly, in any of the four views, framed by the same world-space frame;
    `diff_mesh_path()` whitelists the part id (`validate_id`) and the kind
    before touching the filesystem.
- **`agentcad/core/tools_proposals.py`** — installs the `service.packets` seam
  and registers `proposal_packet {project, id, regenerate?}` and
  `proposal_render {project, id, side, part?, view?}`. Both descriptions restate
  the old = target / new = source convention and state that packet-internal
  failures are payload fields, not errors.
- **`agentcad/server/routes_proposals.py`** — `GET …/packet?regenerate=1`,
  `GET …/render/{side}/{part}` and `GET …/render/{side}` (both `image/png`,
  `Cache-Control: no-store`, decoded from the render tool exactly as
  `routes_vision` does) and `GET …/diff/{part}/{kind}.acm`
  (`application/octet-stream` off disk, like the mesh route in `app.py`). The
  packet route stays a registry passthrough, so its error mapping is unchanged.

## Files
- `agentcad/core/packet.py` — new (~840 lines): the four pure deltas and
  `PacketBuilder`.
- `agentcad/core/tools_proposals.py` — `service.packets` seam, two tools.
- `agentcad/server/routes_proposals.py` — four routes, a `_png` helper, the
  route table in the module docstring.
- `tests/test_packet.py` — new: 28 tests. 16 pure (classification, the
  type-qualified param comparison, per-field spec rows, instance/mate/mass
  deltas, the metric shapes, the mesh center-of-mass caveat) and 12 against the
  real service, git and kernel (both sides from the worktrees with audit +
  `proposal_changed{reason: "packet"}`; **AC4** zero-kernel-work; **AC7** honest
  degradation; the drilled hole within 1 % with a parseable `removed.acm`; the
  shared render frame; on-demand assembly renders; a binary import by size and
  digest; persistence, staleness and regeneration; the frozen refusal; the dirty
  tree; the unreadable manifest; and the timed **AC2** rocketry case).
- `tests/test_proposals_api.py` — extended (branch-local, per the plan): the two
  new tools in `PROPOSAL_TOOLS`, the packet/render tool round trip, the packet
  route's regenerate semantics and the two asset routes (PNG bytes, ACM1 bytes,
  404 for a missing mesh or an unknown part, 422 for an unknown kind).

## Notes
- **AC2 measured: 0.97 s warm**, packet directory **235 KiB** on disk (a copy of
  `examples/rocketry`, nozzle script comment + `wall` 3.0 → 2.6, timed
  regeneration after a cold pass). Budget is 10 s. The cold pass is dominated by
  the one real nozzle rebuild; warm, the whole packet is one `geom_diff`
  boolean (0.18 s, changelog 0079), two 640×480 renders and a handful of git
  calls, everything else being disk-cache hits.
- **The manifest stores parameter *overrides*, not specs**, so the ordinary
  `params_diff` row is `{"name", "field": "value", "old", "new"}` — the design
  spec's example says `"field": "default"`, which is what the *spec* dict shape
  would produce. `params_delta` handles both: a dict-valued parameter is
  compared field by field over `default/min/max/type/unit/choices/description`,
  a scalar one as `value`. Divergence to fold back into the PRD in slice 6.
- `changed_by` distinguishes `params` (the entry's overrides) from `manifest`
  (label, material, kind, …); the design named only `script`/`params`.
- **An instance-only move produces no part rows at all**, so AC4's "every part's
  `geom_diff.unchanged` is true" is vacuous on its own. The test therefore also
  makes a manifest-only part edit (a label), which produces a part row whose
  cache keys match — the short circuit is asserted on a real row *and* the
  kernel-call count is asserted to contain no `build`/`geom_diff`.
- The dirty-tree refusal mirrors `BranchManager._checkpoint` rather than calling
  it: that method is private to `branches.py`, which this PRD must not touch.
- Assembly renders are deliberately **not** in the packet (`assembly.renders` is
  `null`) — they are the expensive kind and the design's first cost lever;
  `proposal_render` with no `part` draws one on demand.
- Follow-ups (slice 5/6): the UI consumes `parts[].renders.old/new` and
  `geom_diff.*_mesh` as URLs; `docs/agent-api.md` needs the two tools, the
  packet shape and the four routes, and the tool count bumped.
