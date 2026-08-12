# 0113 — PRD-008 slice 2: anchor resolution, and the R1 spike that re-scoped AC2

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude (Opus 5)

## Summary
`agentcad/core/anchors.py` adds anchor evidence at creation and **read-time**
resolution into four states (`ok`/`moved`/`orphaned`/`unverified`), deriving
face signatures in the server process from `<key>.acm` + `<key>.faces.u32` with
NumPy — no kernel call, no rebuild — and remapping script line ranges with an
exact snippet search (tier 1) backed by a `difflib` line map against the blob at
the anchor's stored head (tier 2). `face` and `script_range` anchors become
creatable; every *view* of a thread now carries a `resolution` block while the
stored anchor stays immutable.

**The design's five matcher tolerances were placeholders, and the mandatory
spike (risk R1) moved four of them and deleted the fifth.** The numbers are
below, and AC2's claim is smaller than the PRD's wording implies.

## The R1 spike — the numbers

**Method.** 11 bundled parts (construction, fasteners, prototyping, rocketry) ×
up to 3 numeric parameters × +1% / +10% / +30% = 91 rebuild pairs, 3 206 face
pairs. Ground truth was established **independently of the matcher**: each
target was walked in ≤2% parameter steps, each step matched by
mutual-nearest-neighbour on *absolute* centroids (reliable at that step size,
and using none of the matcher's features), and the steps composed. 2 537 of the
3 206 faces kept a truth mapping; the other 669 were destroyed by the change.
Script: `spike_r1.py` + `sweep{,2,3}.py` (scratch, not committed).

**Finding 1 — a face ordinal is not stable across a parameter change.**

| change | ordinals held |
|---|---|
| +1%  | 842 / 916 (91.9%) |
| +10% | 704 / 812 (86.7%) |
| +30% | 751 / 809 (92.8%) |

Worst case: `prototyping/enclosure_lid` renumbered **20 of its 44** faces for a
**1%** parameter change; `rocketry/injector_plate` kept 2 of 10 when the orifice
count changed. So the design's premise is confirmed empirically — the matcher is
load-bearing, not a fallback, and a UI that drew a pin at the stored ordinal
would be wrong roughly one time in ten.

**Finding 2 — the design's own tolerances mis-pin, and the area *filter* is why.**
Run with the design's constants (`NORMAL_DOT 0.985`, `AREA_REL 0.25` as a
candidacy filter, `UVW_DIST 0.15`, `AMBIGUITY_MARGIN 0.05`, `STICKY 0.02`):
**63 mis-pins** among the 2 537 known-truth faces, plus 42 matches to faces that
no longer existed. The mechanism is counter-intuitive and worth remembering:
*every rival a filter removes is a rival that would have tripped the ambiguity
check*, so narrowing candidacy converts orphans into confident wrong answers.
Area is therefore a **final gate on the winner**, never a filter, and the
ambiguity margin does the safety work.

**Finding 3 — an absolute-area comparison is not scale-invariant.** The design
keeps `bbox_uvw` precisely because a scaling parameter moves every absolute
number, then compares `area_mm2` — which such a parameter also moves. The
signature therefore gained `area_frac` (this face's share of the shape's
tessellated area), and the gate compares fractions. Absolute
`centroid`/`normal`/`area_mm2` are still stored, because they are what a human
or an agent reads.

**Tolerances derived from the data (true pairs vs their best rival):**

| quantity | true pairs | shipped |
|---|---|---|
| normal dot | min 0.9724 (+30%), 0.9969 (+10%), 0.99997 (+1%) | `NORMAL_DOT = 0.99` |
| bbox_uvw drift | p99 0.0065 / 0.060 / 0.153; max 0.1615 | `UVW_DIST = 0.15` |
| best−runner-up margin | 0.05 → 63 mis-pins · 0.15 → 1 · **0.20 → 0** | `AMBIGUITY_MARGIN = 0.20` |
| area share | gate only; refuses the 1 000× match | `AREA_REL = 0.5` |
| sticky tie-break | unreachable (see below) | **deleted** |

`STICKY_MARGIN` is not implemented: at an ambiguity margin of 0.20 any rival
close enough to be a sticky tie has already made the match ambiguous, so the
tie-break can never fire. It changed no outcome in the spike because it cannot.
Dead code that looks like a safety net is worse than no safety net.

**Verdict at the shipped constants (the code in this diff, re-run):**

| change | resolved correctly | orphaned | **mis-pinned** |
|---|---|---|---|
| +1%  | 609 / 916 (66.5%) | 307 | **0** |
| +10% | 583 / 812 (71.8%) | 229 | **0** |
| +30% | 564 / 809 (69.7%) | 245 | **0** |
| all  | 1 756 / 2 537 (69.2%) | 781 | **0** |

Of the 669 faces the change destroyed, 657 came back `orphaned` and 12 matched
something (all at dot ≈ 1.0 and uvw ≈ 0 on the same ordinal, i.e. most likely
the truth oracle losing a face that is still there). Every accepted match scored
a margin of at least 0.2013 with a median of 0.93–0.997.

**What AC2 can honestly claim, then:**

- ✅ *"orphans when the face is cut away"* — 98.2% of destroyed faces orphan, and
  the thread stays readable, listable, resolvable and keeps its last-known
  anchor.
- ✅ *"never mis-pins"* — 0 of 2 537. This is the contract and it holds.
- ⚠️ *"a face anchor survives a param tweak"* — **about two times in three**
  (69.2%), not always. The remaining third is honestly `orphaned`, not wrong.
  Simple prismatic parts are near-perfect (`base_plate` 31/31, `clamp_plate`
  11/11, `enclosure_lid` 44/44 at +30%); parts whose faces are a repeated
  pattern are the worst case (`fasteners/tapped_plate`, a threaded hole: 27
  resolved, 76 orphaned — 104 near-identical thread faces are genuinely
  ambiguous). **The acceptance slice must test AC2 on a specific face and
  accept `orphaned` as a correct outcome for a repeated feature**, and the PRD's
  AC2 wording should be narrowed to "survives a param tweak or says honestly
  that it did not, and never points at the wrong face".

Loosening a tolerance would trade this for wrong pins: at `AMBIGUITY_MARGIN
0.15` the hit rate rises ~1.5 points and one mis-pin appears.

**R2 — the two meanings of `n_faces`: not reproducible.** The design warns that
`metrics.n_faces` (`len(shape.faces())`, deduplicated by hash) can be smaller
than the sidecar's count. It was **equal in all 14 shapes measured**: the 11
bundled parts, a two-solid compound, a compound of two *coincident* boxes, and
the test fixture. The sidecar remains the authority for face-index validation
because it is what an ordinal is *defined* by (the `TopExp_Explorer` walk
`mesh.py` tessellates in), not because a divergence was observed —
`tests/test_anchors_kernel.py` says exactly that, so the folklore does not
outlive the measurement.

**R3 — mesh signature vs `face_info`, measured on a cylinder.** Planar face:
normals and centroids agree to 1e-6/1e-3, tessellated area 0.51% low (chord
error — always low, never high). Closed curved side face: areas agree to 0.13%,
**normals do not agree at all** (mesh `(-0.447, 0.894, 0)` vs `face_info`
`(-1, 0, 0)`), because an area-weighted normal over a surface that wraps a full
turn nearly cancels while `face_info` samples `normal_at(0.5, 0.5)`. This is
survivable because the matcher only ever compares mesh-derived signatures with
each other at the same `MESH_TOLERANCE`; on such faces the estimator is wobbly,
which costs candidacy and yields an orphan — never a mis-pin.

## Changes
- **New module `agentcad/core/anchors.py`.** `RESOLUTION` + `make_resolution`
  (the four states, constructed in one place, refusing an unexplained non-`ok`
  status and a hintless `orphaned`/`unverified` — PRD-003's `make_item`
  precedent); `face_table` (pure NumPy: per-ordinal area, area-weighted centroid
  and normal, `bbox_uvw`, with an ordinal that got no triangles kept as
  `present: False` so nothing after it shifts); `sidecar_face_count`
  (`max(sidecar)+1`); `signature_table` (cache key via `service._cache_key_for`
  — never `mesh_info`/`_ensure_built`, both of which build — memoized in-process
  and persisted as `<key>.facesig.json`); `signature_of`/`total_area`;
  `match_face`; `snippet_of`/`find_snippet`/`line_map`; `validate_face`/
  `validate_script_range`; `read_context` and `resolve`.
- **Resolution dispatch.** `part`/`instance` from the manifest; `param` from a
  cheap authority that is **not** a kernel call — the service's content-hashed
  spec cache, else a static `ast` read of a literal module-level `PARAMS`
  (parsed, never executed), else `unverified`; `face` via the fast path (cache
  key equals `signature.mesh_key` → `ok`) then the matcher; `script_range` via
  tier 1 then tier 2. Cross-branch rule (Decision 7): a target missing here
  whose anchor names another branch is `unverified`/`other_branch`, never
  `orphaned`. Every result carries `against: {branch, head}`, and `resolve`
  never raises out of a listing.
- **Tier 2 reads git through `history._run_bytes`** (`cat-file blob
  <head>:parts/<id>.py`, hermetic env, 10 s timeout, undecoded) — never raw
  `subprocess`. No git, no head, or an unreachable head is `unverified`, with
  the distinguishing reason. *We did not look, so we must not claim.*
- **`comments.py` wiring.** `_validate_face`/`_validate_script_range` now
  delegate to `anchors`; `_SUPPORTED` grows to five kinds; `_view` gained a
  `resolution` block (view only — never stored) and a `context` parameter so a
  listing resolves branch and head **once per page**; `list` gained
  `anchor_status` and `resolve_anchors` and its `counts` gained `orphaned`
  (whole-project, like the other two, and absent when nothing was resolved).
- **New `_ANCHOR_EVIDENCE` table.** `signature`, `snippet`, `snippet_sha256`,
  `before` and `after` are derived at creation and **refused from the caller**,
  exactly like `branch`/`head`: a signature a client can assert is not evidence,
  and an anchor whose snippet does not match the script it names would resolve
  against a fiction.

## Files
- `agentcad/core/anchors.py` — new (958 lines, roughly half of them the
  docstrings that carry the measurements)
- `agentcad/core/comments.py` — the two validator stubs replaced, `_view`/`list`
  extended, `_ANCHOR_EVIDENCE` added, module docstring updated
- `tests/test_anchors.py` — new, 38 cases, no kernel (three of them
  `integration`/`portability`, needing git for tier 2): the four-state
  constructor, `face_table` over a hand-built cube ACM (areas, normals and
  `bbox_uvw` asserted exactly), the matcher (scale invariance, symmetry →
  orphan, cut-away → orphan, the area gate, the constants themselves), tier 1
  including **AC3**, tier 2 line maps as units *and* against a real blob read
  back from a real commit (remap, deleted range, unreachable head), dispatch
  against a kernel that fails the test if anything asks it to build (**R8**) —
  at the `anchors.resolve` and the `CommentManager.list` seams both — the
  cross-branch rule, proof that a caller-supplied `snippet` is discarded in
  favour of the real one, and import purity (`OCP`/`build123d` absent from
  `sys.modules` in a fresh interpreter)
- `tests/test_anchors_kernel.py` — new, 9 cases, `slow`/`integration`: **AC2**
  both halves against real geometry with the face identified geometrically
  rather than by ordinal, the byte-identical fast path, an out-of-range index
  refused at creation, **R2** and **R3**
- `tests/test_comments.py` — three slice-1 cases updated for the slice-2
  contract (see Notes)

## Verification
```
uv run pytest tests/test_anchors.py tests/test_anchors_kernel.py tests/test_comments.py -q
  -> 77 passed in 22.19s
make test-fast   -> 957 passed, 1 skipped in 218.26s
make test        -> 1260 passed, 1 skipped in 1404.81s (0:23:24)
```
Baseline was 1213 passed, 1 skipped (changelog 0112); +47 is exactly this
slice's two new modules (38 + 9), so nothing existing was lost.

## Notes
- **Three pre-existing test cases were edited**, all in `tests/test_comments.py`
  and all because slice 1 deliberately encoded a temporary state that slice 2
  supersedes: `face`/`script_range` are no longer "not supported yet", and
  `counts` gained `orphaned` (the design's Decision 17 shape). No assertion was
  weakened; the unsupported-kind case still covers `proposal_hunk` and the
  malformed anchors.
- **Deviation from the plan's wording:** evidence fields went into a new
  `_ANCHOR_EVIDENCE` table rather than into `_ANCHOR_FIELDS`, because that table
  doubles as the *required-input* list — adding `signature` to it would have
  made callers required to supply one. The stricter reading (evidence is
  derived, never accepted) is also the safer one.
- **Deviation from the design:** `STICKY_MARGIN` deleted as unreachable; the
  area filter demoted to a gate and made scale-invariant (`area_frac`); the
  matcher returns a fourth value naming its refusal so the resolution's `reason`
  is not re-derived from a score.
- `<key>.facesig.json` is written beside the mesh on the *read* path. It is
  invisible to PRD-004's determinism stage, which compares an explicit
  `(".acm", ".faces.u32")` tuple, and a failed write is swallowed — a read-only
  cache must not break a comment list.
- Resolution costs, per call: one manifest read, at most one face table per
  distinct part (memoized by cache key, then by the sidecar file), at most one
  `git cat-file blob` per `script_range` anchor that tier 1 could not place.
  Zero kernel calls, asserted by a test whose kernel raises on any request.
- Slices 3 and 4 inherit: `list(anchor_status=…, resolve_anchors=…)` is ready
  for `list_comments`; `proposal_hunk` still raises inside the private validator
  table and is refused by the public path; the `resolution` block's shape
  (`status`, `reason`, `hint`, `confidence`, `face_index`/`start`/`end`,
  `margin`, `n_faces`, `against`) is what the UI's four status chips render.
