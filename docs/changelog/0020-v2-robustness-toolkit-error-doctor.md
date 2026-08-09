# 0020 — v2: robustness toolkit (safe_fillet/shell/bool) + Error Doctor

- **Commit:** a34594d
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Adds the `agentcad.toolkit` robustness helpers that recover from common OCCT
failures with honest warnings, plus the Error Doctor — a catalog mapping real
OCCT/build123d failure signatures to plain-language diagnosis + fix hints, wired
into every worker error via the Wave-0 hook.

## Changes
- **`safe_fillet`** (`toolkit/fillet.py`): attempts the requested radius, then
  binary-searches down to the largest working radius (seeded by `part.max_fillet`
  when available), validating `is_valid`/`volume > 0`. Returns
  `(part, achieved_radius, warning|None)`; degrades to the unfilleted part if
  even `min_radius` fails.
- **`safe_shell`** (`toolkit/shell.py`): tries `offset()` with the given `Kind`,
  then `Kind.INTERSECTION`, then opening only the largest face, then an
  approximate boolean-subtract inner shell. The boolean fallback's warning
  states plainly that wall thickness is only approximate on curved/slanted faces
  (measured up to ~20% thin on dome mid-sections). Returns `(part, warning|None)`.
- **`safe_bool`** (`toolkit/boolean.py`): plain build123d op first, then raw
  `BRepAlgoAPI` at `fuzzy` and `10*fuzzy` when the op raises, yields an
  invalid/empty shape, or (fuse) leaves >1 disjoint solid — rescuing tangent/
  sub-tolerance-gap faces. Returns `(shape, warning|None)`; raises when all
  strategies fail.
- **Error Doctor** (`kernel/error_doctor.py`): 22 ordered signature entries
  (id/regex/diagnosis/fix), matched via `re.search` against
  `"<ExcType>: <msg>\n<traceback>"` — fillet/chamfer-too-large, stale edges,
  open/non-planar/disconnected wires, offset/shell collapse, null shape,
  degenerate spline/primitive, zero-length extrude, revolve-crosses-axis, loft
  vertex order, plane zero-normal, missing sketch, generic BRep failure, plus
  invalid-result / disjoint / empty-boolean cases. Exposes `diagnose_text`,
  `diagnose_exception` (walks `__cause__`/`__context__`), and the worker-facing
  `diagnose(type, msg, tb) -> hint|None`, which the Wave-0 `worker._diagnose`
  hook attaches to `details.hint` on every failure.

## Files
- `agentcad/toolkit/fillet.py` — `safe_fillet` largest-working-radius search
- `agentcad/toolkit/shell.py` — `safe_shell` graduated fallbacks + approximate-thickness warning
- `agentcad/toolkit/boolean.py` — `safe_bool` fuzzy-tolerance escalation via `BRepAlgoAPI`
- `agentcad/kernel/error_doctor.py` — 22-signature diagnosis catalog + `diagnose` hook
- `tests/test_toolkit.py` — signature matching, radius recovery, shell/bool fallbacks, kernel error gains `details.hint`

## Notes
The Error Doctor entries were each triggered on build123d 0.11.1 / OCP 7.8.x
(macOS arm64). The fillet/shell/bool helpers are additive re-exports through the
0017 toolkit shell; hints point agents at these `safe_*` variants.
