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
  one outright. Reference parts (FR13) have no script:
  `use_part` refuses them, `import_cad_file` is the path.
- Packages, the trust chain (review changelog 0181 — **`gate: green` has to be
  load-bearing evidence**): `use_part` verifies the cache against
  **`packages_lock[name].content_id`**, not the receipt (two indexes can ship
  the same `name@version` with different bytes, and both receipts verify), and
  stamps the id it **measured** · every JSON read goes through
  **`packages/_json.py`** — `json.loads` raises **`RecursionError`**, which is
  not a `ValueError`, and it used to escape eleven sites and take the *next*
  index down with it; it also refuses by size **before** parsing · the gate
  measures the **inventory**, never the manifest (a part at `parts/x.tmp` is
  ignored by the id, so it was proved and not shipped; an undeclared
  `parts/*.py` ships and no stage opens it — both are red `format` rows) · the
  stages read a **snapshot in the cell**, so the published id is the id of the
  bytes they consumed · `LocalIndex.publish` **re-derives** `verdict(rows)` and
  never reads `report["publishable"]` · a stage with **zero rows** blocks
  unless it names a legitimate absence, and `STAGE_SKIP_EXEMPT` holds
  **(stage, reason) pairs** · `provenance.header_sha256` covers the block
  (integrity, **not** authentication — say it that way) · an **omitted**
  `version_req` never overwrites a declared pin · a **C0 control char** in a
  relative path is refused (`os.stat` raises `ValueError`, which nothing
  catches) · `_git.validate_url` checks the **ssh host**, not just the whole
  string · **the build fan-out and `--jobs` were DELETED** (1.08×/1.40×/1.17×
  against a pre-registered 1.5× bar; `jobs=1` vs `jobs=4` flipped `publishable`
  under `--budget`) — do not re-add.
- Packages, round 2 of the review (Codex xhigh, changelogs 0181+0183): a tree
  must **agree with its own `package.json`** (checked at install *and* at
  materialise) · the cache **receipt is versioned** and must carry
  `content_id`/`index`/`source`, because the offline path rebuilds a git-tracked
  lock entry from it · **publish hashes the copy in staging** before promoting
  (it used to hash the source, then copy it again later) · a **yank is
  `(index, version)`** — A's yank may not veto B's package ·
  **`ProjectStore._atomic_write` stages through a RANDOM name** (one fixed
  `.tmp` let two writers interleave into it and **corrupt `project.json`**),
  plus `manifest_scope` around package RMW and `_index_scope` (RLock +
  `fcntl.flock`, lock file **outside** the index) around `index.json` ·
  **`GitIndex(subdir=…)`** — this repo ships `catalog/` beside its source, and
  the old dogfood test proved a fixture nobody has · `use_part` **validates
  overrides before writing**, and a successful one with overrides is **two**
  undo steps (documented, not composed — `history.in_restore` is
  process-global) · `manifest_merge.package_problems` catches the
  requirement-from-theirs + lock-from-ours **hybrid nobody authored** · the
  gate **warns** (never reds) when a swept parameter moves no geometry.
- Configurations (`core/tools_configs.py`, the build path, the merge — PRD-012,
  changelogs 0201–0209): config names are **lowercase** (`CONFIG_RE`), `label`
  is the display name, and the object is a *configuration* (never `variant`,
  never `preset` for a manifest one) · **`_rebuild`/`get_part` keep their
  signatures byte-for-byte** (three packs rebind them two-positionally) — a
  config build goes through `_build_with` / `_ensure_config_built`, so those
  wrappers deliberately do **not** decorate it · **nothing new entered
  `_cache_key`'s payload**: config-awareness is `record.effective_params`, and
  a config's key is never the base key even when its params *are* the defaults
  (the service hashes overrides, the worker resolves defaults) · `build_configs`
  is **serial and de-duplicated by cache key**, `affinity=part_id` — do not
  re-add the deleted fan-out — and an empty matrix always carries a `warnings`
  reason · `_status` stays **2-tuple keyed** and a config build writes none of
  it (`_config_status` is separate, and a memo hit publishes nothing — one slot
  per part is a browser **livelock**) · a **declared** config is range/enum-strict
  and normalized on write while `set_params` on top still clamps ·
  `set_active_config` clears the explicit overrides **only when the active
  config actually changes** (so the UI's "Reset to M" is `set_params` nulls,
  not a re-selection) and divergence is *semantic* (`effective !=
  config_params(active)`) · assembly meshes are addressed by **`mesh_key`**
  through `GET /projects/{p}/meshes/{key}` (never builds, `fullmatch` gate, no
  `?config=`) and a bound instance resolves **purely** · `tools_configs` loads
  at **`con`** (read `service.specs`/`packages` inside handlers; never touch
  `gate_providers`) · the merge reaches `configs.<name>.params.<param>` and the
  per-name `_keyed` guard stops a non-object entry merging to `{}`;
  `dangling_instance_config` **blocks**, a dangling `active_config` warns, and
  so does `malformed_configuration` · **resolution is total over the value**:
  `config_params`/`effective_params` return `{}` for a non-object entry or a
  missing/`None` `params` (an unknown *name* is still a `KeyError`), because
  `_cache_key_for` reads them inside `_ensure_built` and a raise there was a
  500 on the part's primary read — but a call site reading `label` off the raw
  entry still needs its own guard · `routes_configs._result`: a **refusal
  envelope raises** (it has no `ok` key), a **build post-state is a 200
  whatever its `ok`** (the write landed) · the rebuild after a landed write
  goes through **`service.rebuild_after_write`** (all five write sites), which
  turns a pre-build `AppError` into that post-state — **`_rebuild` itself still
  raises and must**, because it is also the READ paths' build and they re-raise
  `ok: false` as a `KernelError` (502 instead of 404, `checks` rows moving
  `error`→`fail`, `get_assembly` answering bound and unbound instances two
  ways) · both drawing routes go through **`routes_drawing._drawing_result`**:
  an `AppError` refusal is a 404/422, a kernel-class type (the five
  `protocol.py` constants) a **502** with the worker's type intact ·
  `routes_configs._json` is **strict**
  (non-object body → 422; `{}` only for a genuinely absent body) and is shared
  by `app.py`'s export/`PUT assembly`/`PATCH params` and `routes_drawing`'s
  POST; `PATCH …/instances/{id}/config` **requires** the `config` key, so
  `{"config": null}` is the only unbind, and `PUT /assembly` requires
  `instances` for the same reason (a full-list replace: `{}` wiped it at 200) · the
  drawing's dim table is a **measurement** (resolved values, `Label (name)`,
  SVG only, timeout `120 + 60·rows`), its kernel request is pinned
  `affinity=part_id`, and `render_view` refuses `config` without `part_id`.
- Hosted core (`server/security.py`, `core/authstore.py`, `core/appmode.py`;
  changelogs 0188–0197): a part script is arbitrary Python and, PRD-006 or
  not, it runs **as the server user over the whole projects tree** — so roles
  are not a boundary, per-project ACLs are PRD-005, and registration is closed;
  say it, don't soften it (but "an account is a shell" is no longer literally
  true on Linux — see the PRD-006 trap) · `actor_kind`
  must read `user:` as **human** or every hosted person silently loses their
  claims · the anonymous surface is **nine entries in one frozenset** and
  default-deny makes a new pack private with no action by its author; there is
  deliberately **no `@public` decorator** · `is_public` is `startswith`, so
  every prefix ends in `/` (`/api/public` would open `/api/publicity`) and each
  addition gets a negation test · a naive `[r.path for r in app.routes]` walk
  sees **23 of 83** routes (FastAPI leaves `include_router` opaque) — use
  `conftest.flatten_routes` · `routes_packages`' search/preview walk **every**
  index, so the anonymous catalog is a **separate** scope-filtered pack whose
  misses share **one name-free 404** · `security.install()` must run **before**
  `build_registry` or hosted-only tools register nowhere · a router captures
  `current_config()` at **mount** time (the slot is process-global) · identity
  state comes from `config.config_path().parent`, **never** `--projects-dir` ·
  `fcntl.flock` because `docker compose exec` is a second writer, and the read
  cache keys on `(mtime_ns, size, inode)` so a revocation lands on the **next**
  request with no restart · tokens are **sha256**, passwords **scrypt n=2^15**
  (63 ms measured, below OWASP's n=2^17 and argued out loud) · a hosted
  healthcheck or `curl` must send `Host: $AGENTCAD_PUBLIC_ORIGIN` — `127.0.0.1`
  is **403** and reads as "unhealthy while serving perfectly" · a tool refusal
  is a **200 with an `{"error": …}` payload**, not a 403 · the `open_project`
  **tool** is a known, deliberate FR19 gap (`core/tools.py` is off-limits, no
  unregister seam; reachable only by a member who already has RCE).
- Sandboxing & quotas (`kernel/sandbox*.py`, `_confine.py`, `_preamble.py`,
  `_meter.py`, `quotas.py`, `denials.py`, `core/usage.py`; changelogs
  0230–0237): Linux confinement is **in-process Landlock + seccomp** applied by
  the worker to itself before `import build123d` — **no `preexec_fn` anywhere**
  (the server is threaded; cgroup placement is the parent writing `proc.pid`
  after `Popen`) · **never grant bare `/tmp`**: every worker gets a private
  `agentcad-worker-*` dir, and the *server's* one `agentcad-work-*` root is the
  separate thing `agentcad check` and the package gate materialize cells under
  · `plan()` must **not** create the roots it is handed (`--work-dir` may still
  be refused); `cli._writable_roots` creates the two the server owns, because a
  Landlock rule on a missing path is ENOENT — the grant is lost **and** the
  worker downgrades to `off` · the `seccomp` op constant is **1** (`2` is
  `GET_ACTION_AVAIL` → `EOPNOTSUPP`) · the signal rule tests the pid's **low
  word** unsigned (`JGE 0x80000000`) — a high-word test never fires on arm64 and
  `os.kill(-1, 9)` escaped it · the handled-access mask comes from the **probed
  ABI**, and `TRUNCATE` (bit 14, ABI 3) must be in every write root or every
  truncating `open` is a false denial — hence `LANDLOCK_MIN_ABI = 3` ·
  `/proc/self/clear_refs` (a **file** rule, `FS_FILE` only) is what makes
  `peak_rss_mb` per-request on Linux; elsewhere `peak_rss_is_lifetime: true`
  and `ru_maxrss` is **bytes on macOS, KiB on Linux** · `RLIMIT_NPROC` counts
  **tasks** (threads) per uid, measured at spawn + headroom ·
  **`AGENTCAD_NO_SANDBOX=1` opts out of confinement, not the caps** ·
  `AGENTCAD_CGROUP_DIR`
  unset probes nothing, a path is Model-2 delegation, **`auto` refuses root**
  (a root server would "discover" a subtree anywhere = activation by
  capability), and `memory.swap.max=0` is load-bearing beside `memory.max` ·
  with a cgroup in force the supervisor never fires, so pin the tier in tests ·
  `KernelClient()` with **no args is byte-for-byte historical** (the session
  fixture depends on it) · confinement `active` comes **only** from the worker's
  ping report, and only a landlock/seccomp stage failure clears it ·
  `details.usage` is the **kill paths'** contract (a `script_error` carries
  none — both
  drawing routes must render an identical error) · the Linux loop is
  **`make test-linux`**, which **copies** the tree (Docker Desktop's `fakeowner`
  mounts are not Landlock-coherent) · `AGENTCAD_EXPECT_SANDBOX=active` is the
  honesty gate CI sets so a degradation is **red, not skipped**.
- Materials library (`core/materials.py`, `materials_query.py`,
  `materials_lint.py`, `materials_data/`, PRD-028): property keys are a
  **closed set**, one canonical unit each, and density must be a **point**
  in the shipped library (`_cache_key` hashes it) · the loader **raises at
  import** on any `library`-profile lint error — author a work-in-progress
  family file `_`-prefixed so the loader skips it · the 30 legacy ids/densities
  are **immutable forever** (a corrected value gets a new id) · `library` vs
  `user` lint profile (a hand-written entry is uncited-as-warning, not
  rejected) · `masonry` stays the concrete family, not renamed · FEM
  resolution is **service-side** (no kernel change): thermal evaluates at the
  mean of the two fixed temperatures, a clamped table read warns
  `temperature_out_of_table_range:`-prefixed, `fem_static`'s no-material
  fallback (210000 MPa / ν 0.3) is recorded as `fallback_default` ·
  `find_materials` zero-result is a `validation_error` with
  `nearest_relaxation`, and a range qualifies `_min`/`_max` by its
  conservative bound while a missing property never qualifies · no
  aggregator name (MatWeb/MakeItFrom/Prospector/Granta) in any `source` ·
  the CLI is `.venv/bin/agentcad materials lint`, never `python -m
  agentcad.cli` (no `__main__` guard).
- Bench (`agentcad/bench/`, `kernel/handlers/bench.py`, `benchmarks/`):
  **`error` means the harness could not measure; `not_applicable` is declared
  by `task.json` (weight 0) and never by a run** — an absent, broken or
  mesh-only candidate measures **zero**, because excluded subscores renormalise
  and the alternative rewards destroying evidence · IoU booleans **only the
  intersection** (`union = volA + volB − inter`, never `|`), both sides
  solids-decomposed, `inter` clamped to `min(Σ, volA, volB)`, and a mesh side
  short-circuits **before** any boolean · the rubric is **injected into a copy
  and re-binds `SPECS`**, only rubric-owned rows count, and a
  candidate-inducible skip (`mesh_only`, `no_instances`) is a **fail** ·
  `score.json` carries **no timestamp/host/path/duration** (they live in
  `run.json`) and is `sort_keys` + `round(x, 6)` + `allow_nan=False` ·
  `handlers/bench.py` is a **handler pack, not a tool** (`iou` must stay out of
  `build_registry`) and `bench/**` is **OCP-free**, both asserted ·
  `_build_service(examples=False)` is load-bearing (a derived task must not be
  solvable by opening the example) while the catalog stays registered ·
  `bench.json["tasks"]` is the roster the report's denominator comes from, and
  a baseline task missing from it is a **`coverage` regression** ·
  `--report DIR` **refuses** a directory that is not a results directory and
  clears only `tasks/` · **`bench report --baseline` exit 1 is the gate** (`run`
  and `score` are 0/2 only; per-task deltas are printed, never gated) ·
  `publish` **rule 4 is row-relative** and a rejected row refuses the whole
  board · the reference **script** is the solution, the reference **STEP** is
  the datum and is **never byte-compared** (re-export + IoU/volume/bbox) · the
  secret CI job **never runs on `pull_request`** and lives in its own
  `bench.yml` (`ci.yml` is byte-asserted) · no fan-out, no `--jobs` · an
  external evaluator reads a prompt with **`agentcad bench prompt`**, never
  `cat prompt.md` (the file carries reviewer-only HTML comments the runner
  strips) · the **only** write root either command grants the confined worker
  is the work dir — never the task bundle (a candidate script would rewrite
  the reference STEP it is scored against) and never the submission.
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
· `docs/part-authoring.md` · `docs/user-guide.md` · `docs/materials.md`
(materials library: schema, taxonomy, versioning, the lint, sourcing rules) ·
`docs/deployment.md`
(hosted mode: `docker compose`, accounts, tokens, backup) · `docs/packages.md`
(packages, indexes, the publish gate, the bundled catalog) · `docs/geometry-ci.md`
(`agentcad check` + the GitHub Action) · `docs/bench.md` (AgentCAD-Bench:
the task bundle, the subscores, `agentcad bench`) · `docs/roadmap.md` (PRD
index) · `docs/prd/` (one PRD per feature) · `docs/market_research.md` ·
`docs/superpowers/specs|plans/` (design specs and implementation plans).
