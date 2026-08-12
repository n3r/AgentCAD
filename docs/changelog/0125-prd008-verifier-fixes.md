# 0125 — PRD-008 verifier fixes: the deletion class, measured and gated

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary
An independent verification round confirmed both earlier fix rounds by
execution and found that the headline one does not close the class it claims
to: `LONE_AREA_REL` (changelog 0123) still lets a **cut-away face re-pin onto
the surface underneath it**, reproducibly, at confidence 0.93. This entry
measures that class properly — 327 faces that no longer exist, ground truth
from a geometric oracle — closes 23 of its 27 mis-pins with a genuinely
independent discriminator (how many faces the face touches), and states the 4
that remain on every surface that quotes a rate. Plus nine smaller fixes: the
identity bound at the claim registry, the rate limiter's eviction, a structured
`merge_in_range` conflict, a bounded and divergence-proof line-number cache, and
three changelog corrections.

## N1 — the cut-away-face class

### What the verification found
`LONE_AREA_REL = 0.30` was measured against the *lone-candidate* hole and it
does close the review's own reproduction (a boss at r=19 on a 40 mm plate,
area-share error 0.43). Widen the same boss to r=20 and the plate top left
behind is **0.237** away in area share — inside the bar — and the thread moves
onto it at confidence **0.9289**. Reproduced at (r, h) = (20, 1), (20, 2),
(19.5, 1) and (20, 4); refused only at (19, 1) and (18, 2).

The 2-in-2 693 sweep cannot bound this: **it only ever perturbs a parameter**,
so every face it scores still exists somewhere. Sweeping the lone-candidate
area bar from 1.0 down to 0.2 on that sample removes exactly zero mis-pins,
which is the sample telling us it has nothing to say about this class.

### The deletion class, measured
A second sweep, built for it (`scratchpad/n1/`, method recorded here):

- **67 deletions — 54 synthetic and 13 real.** The synthetic family is a plate
  plus one feature — boss (r ∈ 8…20 × h ∈ 1…10), square pad, rib,
  through-hole, pocket, counterbore — swept over the sizes that make the
  deleted face look most like the one underneath it. The real ones are in the
  **bundled examples**: `clamp_plate`'s clearance hole, `tapped_plate`'s
  counterbore, `angle_bracket`'s leg holes and inner fillet, `base_plate`'s
  footprint recess and anchor slots, `gusset_plate`'s bolt holes, `flange`'s
  bolt circle and bore chamfer, `injector_plate`'s igniter boss and orifices,
  `enclosure_lid`'s logo recess and screw holes.
- **Ground truth uses none of the matcher's features.** Every triangle centroid
  of a before-face is tested against the after-mesh with an exact
  point-to-triangle distance: no sample on the new boundary → **destroyed**, and
  the only correct answer is `orphaned`; all samples on one after-face → that
  face is the truth; anything in between is dropped rather than guessed.

Scored through the shipped `match_face` itself, not a model of it:

| | destroyed (n=327) | survivors (n=515) |
|---|---|---|
| **before** (area gates only) | 300 orphaned (91.7%), **27 mis-pinned** | 375 resolved (72.8%), 0 mis-pinned |
| **after** (+ adjacency gate) | 323 orphaned (98.8%), **4 mis-pinned** | 375 resolved (72.8%), 0 mis-pinned |

and on the parameter class, through the same shipped function, with the new
gate switched off and on:

| | resolved | orphaned | mis-pinned |
|---|---|---|---|
| **before** | 1 451 / 2 693 (53.9%) | 1 240 | 2 |
| **after** | 1 451 / 2 693 (53.9%) | 1 240 | 2 |

**The gate costs nothing there**: all 1 144 correct lone matches keep their
neighbor count. The two survivors are the same two `rocketry/nozzle` seam faces
as before (2 → 2 neighbors; adjacency cannot separate them either).

### What shipped
`face_table` grows one field per face — `neighbors`, how many other faces share
a B-rep edge with it — computed from the same `<key>.acm` + `<key>.faces.u32`
the rest of the module reads. **No kernel call and no OCP import** (the purity
test still pins that): an edge used by one triangle of its own face is on that
face's boundary, and two boundary edges whose endpoints coincide in space are
one shared edge. OCCT triangulates a shared edge once, so the endpoints match
exactly as f32 and the comparison needs no tolerance; a pair that somehow
disagreed would *under*-count, making the gate stricter rather than looser.

`signature_of` stores it, and `match_face` refuses a **lone** winner whose
count differs from the anchored face's: `refusal = "topology_mismatch"`, with a
hint that says what it means. A signature without the field (any thread written
before this change) is not gated on it. `<key>.facesig.json` is versioned
(`{"v": 2, "faces": [...]}`) and an older sidecar is recomputed from the mesh,
because "no neighbors" and "this file predates neighbors" are the same JSON
otherwise.

Two candidates were measured and **not** shipped, which is why the numbers
above are the ones they are:

- **circularity** (`4·pi·A / P²`, a dimensionless outline descriptor): caught
  16 of the 27 deletion mis-pins alone, added **zero** on top of adjacency, and
  cost 28 resolutions on the parameter class at its best bar. A second feature
  that only duplicates the first one's catches is dead weight that looks like a
  safety net.
- **the adjacency gate on every winner** rather than only a lone one: identical
  on the deletion class, and it orphans 4 true matches on
  `injector_plate.n_orifices`, where the parameter itself changes the topology.
  Gates go on a lone winner, never on candidacy — the same rule the area gate
  follows.

`LONE_AREA_REL` stays at 0.30 — not relaxed now that a second gate exists (0.5
+ adjacency mis-pins 7 of 327 where 0.30 + adjacency mis-pins 4), and not
tightened, because tightening it alone was never going to work and the sweep
says so: without adjacency it mis-pins 27 at 0.30, 21 at 0.20 and still 14 at
0.10, while 0.20 costs 84 of the parameter class's 1 451 resolutions and 6 of
the deletion class's 375 surviving faces.

### The residue, stated rather than hidden
The 4 that survive are a **38 mm square pad on a 40 mm square plate** (and its
pocket twin). Same normal, same normalized position, same outline, four
neighbors on both, area share 0.13 apart — every number a mesh-derived
signature has is the same. Nothing here can close that, so
`test_a_square_pad_the_shape_of_the_face_under_it_still_re_pins` asserts the
mis-pin as the documented outcome, and every surface now says **"a cut-away
face can still re-pin; confirm with `face_info` before acting on an expensive
decision"** with both rates beside it: `anchors.py`'s module docstring and
tolerance block, `tools_comments._FACE_ODDS`, `AGENTS.md`, `docs/agent-api.md`,
`docs/architecture.md`, `docs/user-guide.md`, the PRD's as-built divergence and
the acceptance module's docstring.

While rewriting the tolerance block: its per-ratio lines were the **ungated**
numbers (they summed to 1 481 while the total beneath them said 1 451). The
+30% row is 425/865 (49.1%), not 455/865.

### The script path's twin hole
The same shape, twice, and both are fixed rather than stated.

1. **`find_snippet`.** 0124 required a lone hit to be corroborated by *one*
   side of its stored context, "deliberately not both". The verifier showed the
   asymmetry that reasoning missed: duplicated blocks in real Python end the
   same way (`    return shell`) far more often than they begin the same way, so
   the surviving twin of a deleted block coincides on one side routinely — and
   the other side *contradicting* was being counted as a missing vote rather
   than as evidence. A lone hit must now be contradicted by **neither** side.
   With two or more hits the context stays a tie-break, unchanged. The cost is
   small and is paid to a better source: a block that stayed in its
   neighborhood with one side rewritten is exactly what tier 2's diff answers
   correctly, and a block that moved across the file kept neither side and was
   already refused.
2. **Tier 1's identity check**, which is where the verifier's end-to-end
   `status: ok` at line 8 actually came from — `find_snippet` was never
   reached. Two functions ending in the same lines; delete the first and the
   second's copy slides up onto the anchored address, so address and text both
   match and it is different code. A home address whose stored context
   contradicts is now put to the diff instead of taken on trust. **With no diff
   to read it still answers `ok`**: without that, an ordinary edit near a
   thread in a project with no git would cost the thread its pin, which is a
   much worse trade than the one being bought.

## N2 — identity was bounded only on the presence routes
`presence.py` rule 5 claimed an identity is bounded because it becomes a key in
the roster, the claim registry **and** the limiter. The claim registry was not
on that path: `PUT /api/projects/{p}/parts/{id}` with a 4 008-character
`X-Agent-Id` returned 200 and took a claim whose holder was 4 008 characters,
which the roster payload and every `claim_changed` frame then carried.

The number and the check move to `locks.check_client_id` — beside the ContextVar
the identity arrives in — and `ClaimRegistry.claim_write`, `check` and
`arm_override` call it. Refused (422), not truncated, for presence's own
reason: two ids cut to the same 64 characters would be *one* client to the
claim map. `presence.MAX_ID_CHARS` is now `locks.MAX_CLIENT_ID_CHARS` and
`presence.check_identity` delegates.

## N3–N10
- **N3 — `TokenBucket._evict` was paying out to the flood it bounded.** It
  dropped full-burst buckets (free: an absent bucket and a full one are
  indistinguishable) and *then* LRU-dropped whatever was oldest — which under a
  rotating flood is a bucket somebody just spent tokens from, so the eviction
  handed that identity a fresh burst. The LRU pass is gone: when nothing has
  refilled there is no room, and the beat is answered `throttled`. Measured:
  5 000 rotating ids now get **512** grants where they used to get 5 000.
- **N4 — changelog 0124's `MAX_BUCKETS` sentence was false** ("what stops
  rotation bypassing the limit outright"). Annotated in place with what it
  actually does (bounds memory) and what N3 changed.
- **N5 — `MAX_CLIENTS` is process-wide and the 422 said "this project".** The
  message now says "this server … across all projects and branches" and the
  details carry `scope: "server"`. The bound stays process-wide, because what
  it bounds is: one dict in one service.
- **N6 — `ClaimRegistry.check` consumed the arming before evaluating the
  conflict**, so a single-use confirmation could be thrown away on a call that
  was never going to be refused — the caller then meets a conflict it had
  already answered. It is now spent only when it is what lets the call through
  (after a real blocking claim, and after `override`/`override_var` have had
  their say). `claim_write` still spends it unconditionally, for the opposite
  and equally deliberate reason stated in its docstring: every write passes
  through that one, so an arming left behind steals the next claim. Unreachable
  today, since `claim_write` is the only caller that both consumes and
  conflicts, and a trap for the next one either way.
- **N7 — `CommentStore._seq` was unbounded and could diverge.** It carried a
  line count per append-only log forward, checked only against "does the file
  exist", so a second store on the same project (a second service in one
  process, a second process, a test) made both hand out numbers the other had
  used — in the one log whose point is that its order is a record. The cache is
  now keyed to the byte offset our own last append left the file at, so
  anybody else's write re-derives it, and it is capped at `MAX_SEQ_PATHS = 512`
  entries, dropped wholesale like `anchors._TABLE_CACHE` (forgetting a count
  costs one re-count and nothing else). `append_audit` and `append_notification`
  share one `_append_line`.
- **N8 — a range revert containing a merge raised `HistoryError`.** Every other
  refusal in `revert` is a `ConflictError` with `{commit, reason, paths,
  blocked_by}`; this one surfaced at the undo layer as
  `ValidationError: undo failed: git revert failed: …`, and it was decided by
  finding no unmerged paths in the wreckage. The range is now checked for
  merges with one `rev-list` *before* git is asked, so nothing has to be rolled
  back, and it refuses with `reason: "merge_in_range"` naming the merge in
  `blocked_by`.
- **N9 — changelog 0124's `make test-fast → 1120` was reported as off by one,
  and it is not.** 1121 is `passed + skipped`; that line reports the two
  separately, as pytest does. The arithmetic settles it without re-running the
  old tree: `make test-fast` here is **1134 passed, 1 skipped**, this entry adds
  16 tests of which 2 are `slow` and therefore not in `test-fast`, and
  1134 − 14 = 1120. 0124 now carries a note saying that, because a number
  defended in passing is a number the next reader has to re-derive.
- **N10 — changelog 0123's Verification block** showed "override still armed …
  (not spent)" as a *good* outcome, which K8 inverted two entries later. The
  block now carries a superseded-by note that reads it the other way round and
  points at 0124 and this entry.

## Files
- `agentcad/core/anchors.py` — `_boundary` and the `neighbors` field on
  `face_table`; `_SIG_VERSION`/versioned `.facesig.json`; `signature_of` stores
  `neighbors`; `match_face` gains the `topology_mismatch` gate via
  `_same_neighborhood`; `find_snippet`'s lone-hit rule tightened;
  `_resolve_script_range`'s tier-1 identity check consults the stored context
  and falls back to it when no blob is readable; module docstring, tolerance
  block and refusal hints rewritten to both classes
- `agentcad/core/locks.py` — `MAX_CLIENT_ID_CHARS`, `check_client_id`, called
  from `claim_write` / `check` / `arm_override`; `check` consumes the arming
  only against a real conflict
- `agentcad/core/presence.py` — `MAX_ID_CHARS` delegates to `locks`;
  `check_identity` delegates; `_evict` drops only refilled buckets and `take`
  refuses when there is no room; `MAX_CLIENTS`'s refusal names the server;
  module docstring rule 5 corrected
- `agentcad/core/comments.py` — `MAX_SEQ_PATHS`, `_append_line` replacing
  `_next_seq`, both append paths through it
- `agentcad/core/history.py` — `merge_in_range` conflict, checked up front;
  `revert`'s docstring
- `agentcad/core/tools_comments.py` — `_FACE_ODDS` carries both rates and says
  a cut-away face can still re-pin
- `agentcad/server/routes_presence.py` — the identity paragraph says which door
  it is not
- `tests/test_anchors.py` — the adjacency field and its topological invariance;
  the lone-candidate topology gate and the ungated old signature; the
  contradicted-lone-hit rule and the with-rivals tie-break; the tier-1 identity
  case (git) and its no-diff fallback
- `tests/test_anchors_kernel.py` — `PAD`/`PAD_GONE` at the four reproducing
  sizes → `orphaned`/`topology_mismatch`; `SQUARE_PAD` asserting the residue
- `tests/test_claims.py` — the 4 008-character header on a part write, and the
  registry's own refusal
- `tests/test_presence.py` — eviction never hands a drained bucket a burst
- `tests/test_comments.py` — two stores on one log; the bounded cache
- `tests/test_comments_api.py` — the tool description asserts both rates
- `tests/test_undo_authors.py` — the structured `merge_in_range` conflict
- `tests/test_prd008_acceptance.py` — AC2's docstring states both classes
- `AGENTS.md`, `docs/agent-api.md`, `docs/architecture.md`,
  `docs/user-guide.md`, `docs/prd/in-progress/PRD-008-review-threads-presence.md`
  — both rates, the honest "can still re-pin", the script-range rule, the
  presence bounds, `merge_in_range`
- `docs/changelog/0123-prd008-review-fixes.md`,
  `docs/changelog/0124-prd008-codex-fixes.md` — corrections (N4, N9, N10)

## Verification
The verifier's own harness, before and after (its headline attack):
```
scratchpad/verify2/v_anchors.py
  before: r=20.0 h=1.0: moved/rematched_by_signature conf=0.9289 -> face 5
          r=20.0 h=2.0: moved/rematched_by_signature conf=0.9232 -> face 5
          r=19.5 h=1.0: moved/rematched_by_signature conf=0.9177 -> face 5
          r=20.0 h=4.0: moved/rematched_by_signature conf=0.9126 -> face 5
          FAIL attack(a): MIS-PIN reproduced at [(20.0,1.0),(20.0,2.0),
                                                 (19.5,1.0),(20.0,4.0)]
  after : r=20.0 h=1.0: orphaned/topology_mismatch conf=0.9289
          r=20.0 h=2.0: orphaned/topology_mismatch conf=0.9232
          r=19.5 h=1.0: orphaned/topology_mismatch conf=0.9177
          r=20.0 h=4.0: orphaned/topology_mismatch conf=0.9126
          PASS attack(a)
  after : FAIL "attack(a): ONE accidentally-matching side is enough to pin"
          — the harness asserts the OLD find_snippet behaviour; it now
          returns [] where the harness expects [1]
```

Both classes, scored through the shipped `match_face`:
```
scratchpad/n1/param_shipped.py chains_v2.json      (parameter class)
  without the topology gate  known=2693 resolved=1451 (53.9%) orphaned=1240 MISPIN=2
  with the topology gate     known=2693 resolved=1451 (53.9%) orphaned=1240 MISPIN=2
  resolution delta: 0   mis-pin delta: 0

scratchpad/n1/delcollect.py + deleval.py           (deletion class, n=842)
  shipped (area gates only)  destroyed n=327: 300 orphaned (91.7%) MISPIN=27
                             survivors n=515: 375 resolved (72.8%) MISPIN=0
  + lone: neighbours match   destroyed n=327: 323 orphaned (98.8%) MISPIN=4
                             survivors n=515: 375 resolved (72.8%) MISPIN=0
  end-to-end through match_face, refusals on destroyed faces:
    no_candidate 244, area_mismatch 49, topology_mismatch 23, ambiguous 7,
    none 4  (the 4: pad_w38 h1/h2/h5 and pocket_w38 d1)
```

Baseline before this change was 1425 passed, 1 skipped (changelog 0122/0124).
Net **+16**, which is exactly the 16 test functions added — no existing test
was deleted, and the one that was rewritten in place
(`test_a_lone_survivor_one_side_of_the_context_corroborates_is_a_match` →
`..._contradicts_is_not_a_match`, whose assertion inverts) kept its slot.

```
uv run pytest -q tests/test_anchors.py tests/test_anchors_kernel.py \
  tests/test_comments.py tests/test_comments_api.py tests/test_claims.py \
  tests/test_presence.py tests/test_undo_authors.py \
  tests/test_prd008_acceptance.py tests/test_history.py
  -> 228 passed in 87.98s (0:01:27)

make test-fast -> 1134 passed, 1 skipped in 252.32s (0:04:12)
make test      -> 1441 passed, 1 skipped in 1426.45s (0:23:46)
```

## Notes
- **Cost of the new field.** `_boundary` is one `np.unique` over the triangle
  edges plus one over the boundary-edge endpoints; on a pathological synthetic
  mesh (200 000 triangles, every edge a boundary edge) it is 0.51 s, and it runs
  once per cache key because the table is persisted next to the mesh. A listing
  that finds a `.facesig.json` at the current version does no work at all — and
  one written by the previous version is recomputed once, which is the upgrade
  path.
- **The class is narrowed, not closed, and the code says so.** 4 in 327 is what
  a mesh-derived signature can do against a face whose replacement is the same
  shape in the same place; closing it needs the B-rep (a surface identity, a
  face's own UV domain), which resolution may not reach for — it must not
  build. That constraint is pinned by the import-purity test and by the
  "listing never builds" acceptance case, and it is worth more than the 4.
- **The browser was not re-driven.** No frontend file changed; the pin path
  reads `resolution.face_index` exactly as before, and the only user-visible
  difference is that some threads that used to show a pin after a deletion now
  show as orphaned — the outcome the UI already renders.
- **One verifier check reports `buckets=None`** (`v_claims_presence.py`'s
  `_find_bucket` cannot locate the limiter through the mounted route object).
  That is harness introspection, not the bound: exercised directly, 5 000
  rotating ids leave 512 buckets, at the cap.
- **A second verifier check disagrees by design.** `v_anchors.py` asserts that
  a lone snippet hit with one coinciding side *is* accepted, which is the
  behaviour this entry removes; and its K10 script-range case expects
  `other_branch` where the resolver answers `no_head` first — both `unverified`,
  neither a pin, and the cross-branch reason is only reachable once a blob is
  readable. Left as-is.
