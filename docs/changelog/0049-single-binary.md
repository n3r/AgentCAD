# 0049 — Single-binary distribution (PyInstaller onedir bundle)

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

A bundled distribution removes the repo+uv prerequisite (roadmap
"Single-binary distribution"): `make dist` produces a 387 MB self-contained
`dist/agentcad` directory whose executable serves the UI, registers the
bundled examples, and re-executes itself as the OCCT kernel worker — verified
by an end-to-end smoke test on this machine (health → tool discovery →
project/part creation → frozen-worker build → frontend HTML).

## Changes

- **Frozen-aware seams**: `agentcad/_spawn.py` (`worker_argv()` — the normal
  `python -u -m agentcad.kernel.worker` argv unfrozen, `[sys.executable,
  "worker"]` frozen; composed with the sandbox wrapper in `KernelClient`),
  `agentcad/_resources.py` (`resource_root()` — repo root vs `sys._MEIPASS`),
  a hidden `worker` CLI subcommand, frontend/examples resolution through
  `resource_root()` (including the sandbox writable-roots list), and a
  frozen-aware MCP `_ensure_server` (`[sys.executable, "serve", "--no-open"]`
  instead of the uv command).
- **`packaging/pyinstaller/agentcad.spec`**: onedir; hiddenimports via
  `collect_submodules("agentcad")` (covers all pkgutil pack-discovery seams,
  version-proof as packs are added) + OCP/bd_warehouse/uvicorn; datas:
  frontend, examples, build123d data, lib3mf dylibs (ctypes-loaded,
  invisible to analysis — required for 3MF export); binaries:
  `collect_dynamic_libs("OCP")`; FEM extras intentionally excluded (tool
  pack skips registration, same as the suite). vtk needed nothing
  (`cadquery-ocp-novtk` lock).
- **`scripts/build_binary.sh`** (scratch `.buildvenv` via uv) +
  **`scripts/smoke_binary.sh`** (health/tools/examples/build/frontend
  end-to-end against the bundle) + `make dist` / `make smoke`.
- README "Run without a toolchain" section.

## Files

- `agentcad/_spawn.py`, `agentcad/_resources.py`, `agentcad/cli.py`,
  `agentcad/kernel/client.py`, `agentcad/server/app.py`,
  `agentcad/agent/mcp_server.py`
- `packaging/pyinstaller/agentcad.spec`, `packaging/pyinstaller/entry.py`
- `scripts/build_binary.sh`, `scripts/smoke_binary.sh`, `Makefile`,
  `.gitignore`
- `tests/test_frozen_helpers.py` — 9 unit tests (both spawn paths, resource
  roots, MCP argv) — no PyInstaller needed in the suite
- `README.md`

## Notes

Bundle is arm64-only, ad-hoc signed, not notarized — distribution beyond
this machine needs signing/notarization. Bundled examples are opened in
place under `_internal/examples` (mirrors repo behavior); read-only install
locations would need a copy-to-home step. First launch after a fresh build
pays one-time Gatekeeper verification of ~400 MB of dylibs (minutes); warm
launches ~15–20 s. The worktree merge composed the frozen spawn with the
sandbox wrapper and fixed a latent `REPO_ROOT` reference in
`_writable_roots`.
