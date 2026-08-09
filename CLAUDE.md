# CLAUDE.md

Guidance for Claude Code working in this repo. **`AGENTS.md` is the canonical
contributor guide — read it first.** This file adds Claude-Code-specific
workflow notes and the condensed traps you must not hit.

## What this is (one paragraph)

AgentCAD is an agentic-first parametric CAD system: parts are build123d
(OCCT B-rep) Python scripts, validated by a real geometry kernel, driven by a
browser UI, an MCP server, and a built-in chat agent over one service layer.
Python 3.12 + uv. See `AGENTS.md` for the full architecture and the
extension-point contract for adding features.

## Commands

- `make setup` (uv sync) · `make test` (uv run pytest -q) · `make run`
  (server + browser, port 8630) · `make serve` (headless) · `make app`.
- To see a change in the real app, use the **`run` skill**, then drive it
  (curl the route / screenshot the UI) — don't just launch it.
- MCP: `claude mcp add agentcad -- uv --directory <repo> run agentcad mcp`.

## How to work here (process)

This project is built skill-first. Use the Superpowers process skills:
- **brainstorming** before designing a feature; **writing-plans** before a
  multi-step build; **systematic-debugging** for ANY bug (find root cause with
  evidence before fixing — it caught the real cause of the mesh-shading bug);
  **test-driven-development** and **verification-before-completion** (run the
  command, cite the output) before claiming done.
- Prefer the **extension-point packs** (handler/tool/route/toolkit) over
  editing the `worker.py`/`tools.py`/`app.py`/`service.py` cores — see the
  "extension-point contract" in `AGENTS.md`.
- For big multi-part work you may fan out with the Agent/Workflow tools, but
  **subagents must not `uv sync`/`uv pip install` into the shared venv** (use a
  scratch venv) and must not run `git`.

## Traps that will bite you (condensed from AGENTS.md)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** The server process
  must not.
- build123d **version is pinned**; the test suite is the compat harness.
- Boolean intersection volume: use the **`&` operator**, not
  `Shape.intersect()` (that returns a `ShapeList`).
- **Nested `Compound.volume` undercounts** — sum `shape.solids()`.
- Rotations are **intrinsic XYZ Euler degrees** everywhere (kernel + THREE.js).
- **Imported STL** = one welded mesh face (no surface) → needs crease-angle
  normals, not smooth averaging; and its **booleans segfault OCCT** (blocked).
- Tests: session-scoped `kernel` fixture; examples run on a **copy**;
  `TestClient(base_url="http://127.0.0.1")` and
  `create_app(..., extra_allowed_hosts={"testserver"})`; FEM tests
  `importorskip` (suite is green without the `[fem]` extra).

## Changelog — required every commit

Every commit must include a detailed changelog entry staged with the change:
`docs/changelog/NNNN-<slug>.md` (next zero-padded sequence number), following
the template in `docs/changelog/README.md`. Write it from the actual diff. See
the "Changelog" section of `AGENTS.md` for the full rule.

## Definition of done

`make test` green (cite the count) · new behavior/bug has a test · docs updated
if the surface changed · UI changes verified in a real browser · **a
`docs/changelog/NNNN-<slug>.md` entry is staged with the change** · commits end
with the `Co-Authored-By: Claude <noreply@anthropic.com>` trailer · don't
commit manifest-reformatting churn or the venv.

## Deeper docs

`AGENTS.md` (contributor guide) · `docs/architecture.md` · `docs/agent-api.md`
· `docs/part-authoring.md` · `docs/user-guide.md` · `docs/roadmap.md` ·
`docs/superpowers/specs|plans/` (design specs and implementation plans).
