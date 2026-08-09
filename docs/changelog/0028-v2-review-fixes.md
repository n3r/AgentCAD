# 0028 — v2 review fixes: 12 confirmed findings resolved

- **Commit:** 04ebe29
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Resolves 12 confirmed findings from the v2 review across analysis, mates,
materials, FEM, and the frontend — correctness bugs (false-positive wall check,
racy metrics, mate KeyErrors) plus robustness on writes and gizmo interaction.

## Changes
- **Wall probe covers every solid** (`kernel/handlers/analysis.py`): `_min_wall`
  now iterates all solids instead of only the first, so a thin feature on a
  second solid can't yield a false "ok".
- **Mate to a reference part errors cleanly** (`core/mates.py`): `resolve()`
  pre-checks that a mated instance and its `to_instance` are both script parts
  with connectors, raising a clear ValidationError instead of a
  `KeyError('script')` deep in the resolver.
- **Reject dangling mates on write** (`core/project.py`): `set_instances`
  validates each `mate.to_instance` exists in the assembly (and isn't self),
  so removing an anchor can't leave the assembly unreadable.
- **Non-racy assembly metrics** (`core/service.py`): `get_assembly` reads
  `mass_g` from the build result rather than re-reading `_status`.
- **Resolved materials + part provenance** (`core/service.py`): `get_project`
  reports the effective material catalog (builtin + project overrides) via a new
  `_materials_map`, and each part entry now carries `kind`/`source`.
- **FEM guards + route whitelist** (`kernel/_fem_impl.py`,
  `server/routes_analysis.py`): raises a clear error when `load_face` matches no
  facets (zero area), and the `/fem` route forwards only documented, non-null
  body keys instead of splatting the whole body.
- **Frontend gizmo/material fixes** (`frontend/js/main.js`,
  `frontend/js/inspector.js`): refetch materials on `project_changed`; suppress
  the echo of our own in-flight transform commit (`localPatchUntil`) so the
  reload can't detach the gizmo mid-drag; preserve the material `<select>`
  across metric re-renders via a change-signature guard.

## Files
- `agentcad/kernel/handlers/analysis.py` — wall probe iterates all solids
- `agentcad/core/mates.py` — reference/dangling mate pre-checks
- `agentcad/core/project.py` — reject dangling/self mates on write
- `agentcad/core/service.py` — build-result metrics, resolved materials, part kind/source
- `agentcad/kernel/_fem_impl.py` — empty load-face guard
- `agentcad/server/routes_analysis.py` — whitelist `/fem` body keys
- `frontend/js/main.js` — material refetch + own-echo suppression
- `frontend/js/inspector.js` — preserve material `<select>` across re-renders
- `tests/test_analysis.py`, `tests/test_mates.py` — two-solid wall probe, mate-to-reference error, dangling-mate rejection

## Notes
Full suite: 136 passed, 1 skipped.
