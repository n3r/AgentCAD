# 0032 — Add AGENTS.md and CLAUDE.md contributor guides

- **Commit:** dd15c11
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Adds two top-level contributor guides: `AGENTS.md` (the canonical guide for any
AI agent or human working in the repo) and `CLAUDE.md` (a Claude-Code entry
point that defers to `AGENTS.md` and adds workflow notes plus condensed traps).
Documentation only — no code or behavior change.

## Changes
- Adds `AGENTS.md` (189 lines): project description ("the model is code" /
  build123d B-rep, kernel-as-referee), quick start (`make setup|test|run|serve|
  app`, CLI, MCP registration), a two-process architecture diagram with a
  package-by-package tour (`kernel/`, `core/`, `server/`, `agent/`, `toolkit/`,
  `frontend/`), and — centrally — the **extension-point contract**: add
  features as handler/tool/route/toolkit packs rather than editing the
  `worker.py`/`tools.py`/`app.py`/`service.py` cores. Also documents the
  part-script contract, build123d/OCCT gotchas (intersection volume via `&`,
  nested `Compound.volume` undercount, intrinsic XYZ Euler rotations, imported
  STL crease-normals and boolean segfaults, `is_valid`/`is_manifold` as
  properties), conventions (structured errors, atomic writes, determinism,
  127.0.0.1 host guard), testing notes, and a definition of done.
- Adds `CLAUDE.md` (65 lines): points to `AGENTS.md` as canonical; adds
  Claude-Code-specific workflow (Superpowers skills: brainstorming,
  writing-plans, systematic-debugging, TDD, verification-before-completion; the
  `run` skill for real-app checks; MCP add command), a condensed traps list,
  the definition of done, and links to deeper `docs/`.

## Files
- `AGENTS.md` — new canonical contributor guide (architecture, extension-point
  contract, gotchas, conventions, testing, done criteria)
- `CLAUDE.md` — new Claude-Code entry point deferring to `AGENTS.md` with
  workflow notes and condensed traps

## Notes
Both guides encode hard-won kernel constraints (only `agentcad/kernel/` may
import `OCP`; pinned build123d; subagents must not `uv sync` into the shared
venv or run `git`) so future contributors and agents avoid known failure modes.
The mesh-shading fix (0030) is cited as the worked example for the
systematic-debugging + regression-test pattern.
