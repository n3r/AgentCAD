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
- Geometry CI (`core/checks.py`): the ephemeral `--ref` service **must** have
  `bus.on_publish = None`, `branch_resolver = None` **and** `write_guard = None`
  or a check writes into the user's repo · a run materializes into a unique
  `<work-dir>/agentcad-check-<pid>-<rand>/` and **never deletes a directory it
  did not create** (a `--work-dir` overlapping the project is refused) · the
  pack is `tools_run_checks.py` (load order), never `tools_checks.py` · rows are
  **`items`**, never `checks` · `check` is report-honest and `--strict` only
  moves the verdict (skipping `strict_exempt` rows), while the `specs`/`checks`
  gates are fail-closed and never answer `pending` · `--budget` is read before
  every item **and every kernel call**, and what it stops is a
  `skip`/`budget_exceeded` + exit 2, never a red · DXF is not byte-stable, so
  determinism compares SVG only · the Action checks the working tree and takes
  `--sha` as provenance, never `--ref`.
- Review threads (`core/comments.py`, `core/anchors.py`, `core/presence.py`):
  the module is **`comments`**, never `threads` (`toolkit/threads.py` is ISO
  screw threads) · threads live in `.history/agentcad/comments/` (canonical,
  branch-free, restore-proof) and are **never** `project_changed` · an anchor is
  immutable and its status is computed on every read — `unverified` means *we
  did not look*, and **orphan rather than guess, a bias and not a guarantee**
  (a bounds-moving param or a closed curved face orphans by design; mis-pins
  are 2 in 2 693 across a parameter change and 4 in 327 when a feature is
  deleted — quote both, never "never") · resolution makes **zero kernel
  calls** · claims are per-part, human-vs-human, never for the turn holder, and
  reach `write_guard` through `locks.write_scope` (its signature is unchanged) ·
  presence is an **HTTP heartbeat**, not client→server WS · `undo {scope}`
  defaults to `"any"` and the stacks are **not** per client.
- Packages (`core/packages/`, the gate, `catalog/`): the pack is
  **`tools_packages.py`** and it registers **no gate provider** (`pac` sorts
  before `pro`, whose `gate_providers = []` is unconditional — the
  `tools_run_checks` trap) · the publish gate is a **correctness** gate, never
  a security boundary, and the real boundary is *index declares the content id,
  cache verifies every fetch and every materialisation* · a package is a
  **directory** and its id is a canonical tree digest (no archive; tar is not
  byte-stable) · **no timestamp/client id/absolute path** in the provenance
  header or `packages_lock`, and `remove_package` touches **no script byte**
  (the header is inside the script and the script is the cache key) · the
  gate's claim is each parameter's own range **plus declared presets**, a sum
  and never the cross product · the ephemeral service's `write_guard` is
  genuinely live here, so nulling it is load-bearing, and `_refuse_overlap`
  also covers the **package directory** · `_git.py` is not `history._run`
  (no work tree, 120 s, credential helper, `reset --hard`) · bundled indexes
  are **appended**, so a user index named `agentcad-core` replaces the shipped
  one outright · the fan-out measures **1.40×** on the real catalog, *under*
  the 1.5× bar — do not advertise it. Reference parts (FR13) have no script:
  `use_part` refuses them, `import_cad_file` is the path.
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
· `docs/part-authoring.md` · `docs/user-guide.md` · `docs/packages.md`
(packages, indexes, the publish gate, the bundled catalog) · `docs/geometry-ci.md`
(`agentcad check` + the GitHub Action) · `docs/roadmap.md` (PRD
index) · `docs/prd/` (one PRD per feature) · `docs/market_research.md` ·
`docs/superpowers/specs|plans/` (design specs and implementation plans).
