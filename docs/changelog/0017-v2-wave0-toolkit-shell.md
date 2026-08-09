# 0017 — v2 Wave 0: add toolkit package shell (lazy re-exports)

- **Commit:** d70107b
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Creates the `agentcad.toolkit` package as an empty shell with lazy re-exports,
so part scripts have a stable import surface for the robustness/sketch/threads
helpers before those submodules exist. Package-level import never hard-fails if
one submodule is mid-build.

## Changes
- New `agentcad/toolkit/__init__.py` declaring `__all__ = ["safe_fillet",
  "safe_shell", "safe_bool", "sketch", "threads"]`.
- Module-level `__getattr__` performs lazy resolution: `safe_fillet`/
  `safe_shell`/`safe_bool` are pulled from the `fillet`/`shell`/`boolean`
  submodules on first access; `sketch`/`threads` resolve to the submodules
  themselves; anything else raises `AttributeError`.
- Docstring documents the blessed script usage
  (`from agentcad.toolkit import safe_fillet, ...`).

## Files
- `agentcad/toolkit/__init__.py` — new package init with lazy `__getattr__` re-exports

## Notes
Intentionally ships no geometry yet — the submodules land in commit 0020
(`fillet`/`shell`/`boolean`) and 0019 (`sketch`). Lazy import is deliberate:
importing the package succeeds even while a submodule is missing or broken,
which is what allows the parallel Wave-1 agents to fill it in independently.
