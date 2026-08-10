# 0079 — `geom_diff` kernel handler and the explicit render frame

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
Slice 3 of PRD-002: the two standalone geometry primitives the review packet
(slice 4) will consume — a `geom_diff` worker handler pack that reports the
added and removed material between two versions of a part, and one additive
`frame=` keyword on `render_acm` so a before/after render pair shares a camera
and superimposes. Nobody calls either one yet: `geom_diff` is a new kernel
method and `render_acm(frame=None)` is byte-identical to the previous
implementation (asserted against a digest captured from it), so this slice is
independently revertible.

## Changes
- **New handler pack `agentcad/kernel/handlers/diff.py`** — a `register(toolbox)`
  pack contributing one method:
  ```
  method: "geom_diff"
  params: {"old":  {"script", "params"} | {"source": "<path>"} | null,
           "new":  {"script", "params"} | {"source": "<path>"} | null,
           "added_path": "<abs .acm path>" | null,      # tessellate new - old
           "removed_path": "<abs .acm path>" | null,    # tessellate old - new
           "tolerance": 0.1}
  result: {"added_mm3", "removed_mm3", "old_volume_mm3", "new_volume_mm3",
           "added_triangles", "removed_triangles", "skipped_mesh"?}
  ```
  - `new - old` is the added material, `old - new` the removed material; a
    `null` side means the part is absent there, so the other side's whole
    volume is reported as added (or removed) with no boolean attempted.
  - **Volumes always come from `toolbox["shape_volume"]`**, never `.volume`:
    a difference is routinely a nested `Compound`, whose `.volume` reports only
    the first child subtree.
  - **Mesh-kind (imported STL) sides are never booleaned** — the side is named
    in `skipped_mesh`, both `added_mm3`/`removed_mm3` come back `0.0`, and the
    per-side volumes are still reported. An OCCT boolean on a welded mesh Face
    segfaults the worker; the test asserts the kernel still answers `ping`
    afterwards.
  - **Each direction is guarded as a unit** (boolean + volume + tessellation):
    any exception becomes `WorkerError(ERROR_KERNEL, "geometric diff
    unavailable: …", {"stage": "added"|"removed"})`, so the packet can degrade
    to `geom_diff.available: false` with its metrics and renders intact.
  - Diff meshes are written with `toolbox["tessellate"]` +
    `toolbox["atomic_write"]`; the triangle count is read from the ACM1 header
    (`nt` at byte 8). **A zero-volume side writes no file** and reports `0`
    triangles, so the UI knows there is nothing to overlay.
- **`agentcad/core/render.py` — one additive keyword argument**
  `render_acm(meshes, view, width, height, frame=None)`. `frame` is a
  world-space bbox `{"min": [x, y, z], "max": [x, y, z]}`; the new
  `_frame_extents(frame, view)` helper projects its eight corners through the
  existing `_camera_basis(view)` and its 2-D center/span replace the per-mesh
  auto-fit. Nothing else in the function changes; a malformed frame is a
  `ValidationError`, a degenerate (zero-size) frame is clamped by the same
  `1e-9` floor the auto-fit uses, and a frame smaller than the geometry clips
  at the viewport rather than failing.

## Files
- `agentcad/kernel/handlers/diff.py` — new (~105 lines): the pack.
- `agentcad/core/render.py` — `_frame_extents()` added; `render_acm` gained the
  `frame` keyword and a five-line branch at the fit step; docstring extended.
- `tests/test_geom_diff.py` — new: 8 tests (drilled hole ⌀6×20 → `removed_mm3`
  within 1 % of π·3²·20 · the filled-hole reverse · identical inputs write no
  mesh · the multi-solid case against a hand-computed number · absent sides ·
  the STL side skipped with the worker still alive · the structured
  `details.stage` failure · the written ACM1 parsed by `acm.read` with the
  triangle count matching).
- `tests/test_render_frame.py` — new: 8 tests (`frame=None` byte-identical to
  two pre-change sha256 goldens · `_frame_extents` unit case · the same marker
  solid landing on the same pixel in two different scenes framed identically,
  **and** drifting under the auto-fit so the assertion can fail · a frame
  smaller than the geometry · a degenerate frame · three malformed frames).

## Notes
- **Measured: `-` does *not* share `&`'s multi-solid bug.** The design flagged
  the risk that the difference operator misbehaves on multi-solid `Compound`
  operands the way `&` does (see `worker.pairwise_interference`). It does not:
  a two-cube compound differenced against the same compound with a ⌀4×10 bore
  reports 125.66 mm³ (analytic: 125.66), and the reverse direction reports
  `0.0`. No per-solid decomposition is needed — the handler differences whole
  shapes. `worker._shape_volume` is still mandatory for the *volume*; that trap
  is real and separate.
- **Measured boolean cost on real parts** (this machine, one param change per
  part, warm = both shapes already in the worker's 16-entry shape LRU, so it is
  the two booleans + two tessellations alone; examples copied, never mutated):

  | part | change | cold (builds + booleans) | warm (booleans only) |
  |---|---|---|---|
  | `rocketry/nozzle` (splined/lofted contour) | `wall` 3.0 → 3.4 | 0.21 s | **0.18 s** |
  | `prototyping/enclosure_base` | `wall` 2.5 → 3.0 | 0.86 s | 0.47 s |
  | `engine/cylinder_head` | `valve_d` 19 → 20 | 5.29 s | 1.55 s |
  | `engine/engine_block` | `bore` 66 → 67 | 6.01 s | 1.84 s |
  | `engine/exhaust_manifold` (swept primaries) | `primary_d` 34 → 35 | 7.99 s | 1.58 s |
  | `engine/intake_manifold` (lofted plenum) | `runner_d` 30 → 31 | 38.57 s | **15.03 s** |

  Every case produced correct-looking volumes (e.g. the cylinder head's
  `valve_d` change is pure material removal: 262.6 mm³ removed, 0.0 added) and
  none failed or crashed the worker. `examples/surfacing` does not exist — the
  surfacing-heavy stand-ins are the rocketry nozzle (splines + loft) and the
  engine's swept exhaust manifold and lofted intake manifold.
- **Budget implication for slice 4:** the nozzle case AC2 targets is 0.18 s
  warm, comfortably inside the 10 s packet budget, but a lofted part like the
  intake manifold costs 15 s of boolean time *by itself*. The 300 s per-request
  timeout is the backstop, and `geom_diff.available: false` (this handler's
  structured failure, or a caller-side timeout) is the designed degradation —
  the packet must never block on it.
- **`frame=None` is pinned by digest**, not by re-deriving the framing: the two
  sha256 goldens in `tests/test_render_frame.py` were captured by running the
  pre-change `render_acm` on fixed mesh dicts. Any future change to the fit
  math will break that test on purpose.
- **Deviation from the plan (tests):** the plan put the framing cases in
  `tests/test_render.py`; they are in a new `tests/test_render_frame.py`
  instead, so no pre-existing test file is edited (it imports `_decode_png`
  from `tests/test_render.py`). The plan also marked the `geom_diff` tests
  `slow`; they run in 4.6 s on 20 mm cubes, so they are marked `portability`
  only (like `tests/test_kernel.py`) and stay in `make test-fast` coverage. The
  example measurements above were taken with a throwaway script, not a test —
  the expensive parts do not belong in the suite.
- Verification: `uv run pytest tests/test_geom_diff.py tests/test_render_frame.py
  tests/test_render.py tests/test_kernel.py -q` → 56 passed; `make test-fast` →
  545 passed, 1 skipped; `make test` → **584 passed, 1 skipped** (the 568/1
  baseline of `0078` plus this slice's 16 cases, every pre-existing test
  unedited).
