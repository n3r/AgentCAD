# 0100 — 2026-08-11 — PRD-004 slice 3: `--ref` and `--verify-determinism`

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

Third slice of PRD-004 (geometry CI): `CheckRunner.run` learns to measure a
**commit** instead of the working tree, and to prove the product guarantee.
`--ref` resolves a branch/tag/commit explicitly, materializes the resolved sha
into a throwaway detached `git worktree`, and drives the same four-stage
pipeline through a **second, ephemeral `AgentCADService`** rooted in the work
dir and sharing the same kernel — so the user's project is byte-identical
afterwards, `.cache/` included (FR3, AC7). `--verify-determinism` appends a
derived `determinism` stage: every part is built a second time on a cold cache
and the stable artefacts are compared byte for byte (FR6, AC6). Still no CLI,
no tool and no route (slices 4–5); still zero kernel imports.

## Changes

- `agentcad/core/checks.py`:
  - `run(..., ref=..., verify_determinism=...)` no longer raises
    `NotImplementedError`. `run` now splits into two paths that share one
    `finalize_report` call: working-tree mode (unchanged behaviour) and
    `_run_ref`. Two small seams make that possible without duplicating the
    pipeline — `_measure(runner, …)` (the four stages over *whichever* service
    a runner holds) and `_bind(service, registry)` (a runner over the ephemeral
    service that **shares this run's deadline and min-volume**, because a
    second budget would be a second promise).
  - `_resolve_ref(proj, canonical, ref, warnings) -> {"kind", "ref", "sha"}`:
    `resolve_branch`, then `resolve_tag`, then `looks_like_commit` +
    `has_commit` (spelled out to 40 hex through `rev-parse`). **Never
    `resolve_ref`** — `git rev-parse` searches `refs/tags` *before*
    `refs/heads`, so a tag named like a branch would silently answer for it
    (PRD-001 X1). A name that is both resolves as the **branch** and adds a
    `warnings[]` entry naming the ambiguity and how to ask for the tag;
    `refs/heads/<x>` and `refs/tags/<x>` are accepted for disambiguation. No
    git, or a project with no `.history` repo → `ValidationError` naming git
    (→ 422, exit 2); an unknown ref → `NotFoundError` naming all three places
    it searched.
  - `_materialized(canonical, sha, work_dir, warnings)` — a context manager:
    `worktree prune` → `worktree add --detach <work_dir>/<project>/ <sha>` →
    `yield` → **`finally`** `worktree remove --force` then `prune`.
    `--detach` with the resolved **commit**, never a branch name (a branch
    already checked out at `.history/trees/<b>/` cannot be checked out twice);
    this is `MergeOrchestrator._stage`'s exact mechanism, and every git call
    goes through `history._run` (hermetic env, 10 s timeout, never a raw
    subprocess). An `add` that fails is a `ValidationError`; a `remove` that
    fails is a `warnings[]` entry plus an `rmtree`, never a red check —
    `git worktree prune` heals the registration on the next run.
  - `_ephemeral_service(work_dir, tree, kernel) -> (service, registry, name)`
    (module-level, so slice 5 and the tests can reach it): a second
    `AgentCADService` rooted at the work dir, sharing the **same kernel
    object** (a second pool would cost another ~3 s/worker and ~0.5 GB), with
    the two non-negotiable assignments, each commented with the failure it
    prevents:
    - `bus.on_publish = None` — the service's own constructor installs
      `_snapshot_on_event`, so any `project_changed` publish would commit a
      history snapshot *into the linked worktree*, i.e. into the user's real
      repository, from a command whose contract is "never mutates".
    - `store.branch_resolver = None`, set **after** `build_registry` (which is
      what installs it, via `BranchManager`) — otherwise every authored read
      and write resolves against a `.history/agentcad/` sidecar that does not
      exist there, and writes one.
    Both paths are resolved (`Path.resolve()`) before the store sees them:
    macOS hands `/var/…` for `/private/var/…`, and `ProjectStore.open`
    compares its resolved argument against `root / <name>`, so an unresolved
    root makes the store believe a different project of that name is already
    registered.
  - `_ref_dirty` / `_branch_tree`: a ref check measures the **commit**, so a
    branch with uncommitted edits gets `source.dirty: true` and a warning
    naming the snapshot that *was* measured — and the runner deliberately does
    **not** snapshot first (the packet's `_checkpoint` may commit because it is
    producing review evidence on the user's behalf; a check may not).
    `_branch_tree` is read-only on purpose — `BranchManager.tree_of` would
    *materialize* a missing tree, which is a write — and answers the **main**
    worktree from `symbolic-ref`, because AgentCAD's repos are `--git-dir
    <project>/.history --work-tree <project>` and git lists the main worktree
    as its *git directory* rather than as the project directory. Only the
    linked `.history/trees/<b>/` checkouts are listed at the path they live at.
  - The report's `source` block in ref mode is stamped by the **outer** runner
    (`kind`, the ref as the caller typed it, the resolved `sha`, `dirty`,
    plus `--sha`/`--ref-label` provenance); the inner `SpecRunner.run` is
    called with no `ref=` — it measures the tree it was given.
  - `_determinism_stage` / `_determinism` / `_determinism_item` — the derived
    `determinism` stage (already in `DERIVED_STAGES` and `validate_report`'s
    vocabulary since slice 1). It is **not** in `STAGES` and not selectable
    with `--stages`: it does not certify the project, it certifies the product
    guarantee. The second build runs against a throwaway `copytree` of the
    measured tree with `.cache`, `exports`, `.history` and `.git` excluded, so
    the cache is genuinely cold and that side carries no git at all. It is
    guarded exactly like a real stage (budget check, then never propagating).
  - `_compare_builds(...) -> (divergences, compared)` and
    `_byte_diff(a, b) -> int | None` (module-level, streamed in 1 MiB blocks):
    the cache key, then `<key>.acm` and `<key>.faces.u32`, then `volume_mm3` /
    `mass_g` / `area_mm2` — exact equality, not a tolerance. Divergences name
    *which* artefact and *where* ("`.acm` differs at byte 12"), and the second
    return value is what makes a green row mean something: an artefact neither
    build wrote is not counted as agreement, so a `pass` row's
    `details.compared` says what it actually looked at.
  - `_compare_svg` adds the SVG drawing to the comparison for script parts
    (`handlers/drawing.py` writes `atomic_write(out, svg.encode())` — no
    timestamp, no id). **DXF is excluded by name**, as one `skip` row
    (`determinism:dxf`, `reason: "not_byte_stable"`) whose hint says why:
    `ezdxf` stamps `$TDCREATE` and fresh `$FINGERPRINTGUID`/`$VERSIONGUID`
    into every document. A drawing that will not generate produces no
    divergence and no row — the drawings stage rules on that — but it does
    produce a warning, so "SVG was not compared" is never silent.
  - A part that will not build makes its determinism row an **`error`**, not a
    pass and not a fail: "we do not know whether this part is deterministic".
  - New imports: `contextlib`, `shutil`, `tempfile`, `pathlib.Path`,
    `.history.HistoryError`/`looks_like_commit`, `.model.NotFoundError`. None
    of them pull `OCP` or build123d — the fresh-interpreter probe still passes.
- New `tests/test_checks_ref.py` (17 test functions, 18 collected —
  AC6 is parametrized over two examples; `integration` + `portability` +
  `slow`, `timeout(900)`, skipped without git, and driven by the **real**
  service because history matters): AC7's byte-identity map (with the cache
  warmed first, so its stillness means something), the repo-untouched
  assertions (head, commit count, `git status`, the worktree registry) — also
  after an exception injected mid-run — the temp work dir's lifetime, a
  **relative** work dir never landing inside the project, the ambiguous
  branch/tag, tag and short-commit refs, both no-git paths, an unknown ref, an
  unknown project, the dirty-branch warning with the commit's value measured
  (and the working-tree run measuring the disk's), `_byte_diff`,
  `_compare_builds`, the DXF skip row, determinism composed with `--ref`, AC6
  on `construction` and `prototyping`, and a part that will not build.

## Files

- `agentcad/core/checks.py` — grew the ref materializer, the ephemeral-service
  constructor and the determinism stage (~570 lines); slices 1–2 untouched
  apart from `run`'s split
- `tests/test_checks_ref.py` — new; 17 tests (18 collected)
- `tests/test_checks_pipeline.py` — **one test flipped**, the seam slice 2
  declared for this slice (see Notes)
- `docs/changelog/0100-check-ref-and-determinism.md` — this entry

## Notes

- **The one test edit, and why it is not the plan's "no existing test file may
  be edited".** Slice 2 landed
  `test_ref_and_verify_determinism_are_declared_seams_not_silent_no_ops`,
  asserting both parameters raise `NotImplementedError` — a seam written to be
  flipped by this slice, in this plan's own new test file. It is now
  `test_ref_and_verify_determinism_are_live_not_silent_no_ops` and pins what
  replaced the seam: a `--ref` this runner cannot satisfy is still refused *by
  name* (`NotFoundError` for an unknown project, `ValidationError` naming git
  for a project with no history), never quietly answered by measuring the
  working tree and calling it a ref. No test predating PRD-004 was touched.
- **AC7's byte-identity map excludes `.history/`, and says so.** git's own
  bookkeeping — a worktree registration, an index stat refresh from `git
  status` — is git's, and the design's risk #4 already records that a stale
  registration is expected and self-healing. What must not move is a byte the
  *user* owns, so the hash map covers the working tree and `.cache/`, and the
  git admin state is asserted separately and exactly: same head, same commit
  count, empty `git status --porcelain`, same number of registered worktrees.
- **The stated price: a ref check runs on a cold cache.** The work dir holds no
  `.cache/`, so every part is a real kernel build. The AC7 test asserts it
  rather than hiding it — every build row reports `cached: false`. This is the
  literal cost of "a check never mutates the project" being a sentence with no
  footnote (design Decision 5); a `ProjectStore.cache_dir` override seam is the
  recorded phase-2 follow-up, after measurement.
- **`--strict` and `--verify-determinism` together are red by construction**,
  because the DXF row is a `skip` and `--strict` counts every skip. That is the
  honest reading of both flags ("is everything measured *and* green?" — DXF is
  not measured), and the row's hint names the prerequisite: adopting ezdxf's
  fixed-date / `CONST_GUID` path in the drawing handlers.
- **Work-dir ownership.** A work dir this runner created (`mkdtemp(prefix=
  "agentcad-check-")`) is deleted in a `finally`; one the caller passed
  (slice 4's `--work-dir`, an `actions/cache` path, a bigger disk) is left
  alone — only the materialized tree inside it is removed. Either way it is
  made **absolute** first: `history._run` runs git with `cwd` set to the
  project, so a relative `--work-dir` handed straight to `worktree add` would
  materialize the throwaway tree *inside the user's project directory*. That
  has its own test.
- **Known edge in the SVG comparison.** `generate_drawing` stamps
  `"<project> / <part>"` into the drawing's title block, so the two sides must
  be registered under the same project name for the bytes to be comparable.
  They always are — both names come from the same `project.json` `name` field
  (the ref-mode tree and the determinism copy are copies of it) — *unless* a
  project directory's name and its manifest's `name` have been hand-edited
  apart, in which case the row reports an SVG divergence that is really a
  label difference. The message names the byte, so it is actionable rather
  than silent; a name-equality guard is the follow-up if it ever shows up.
- Determinism's second service is built from a **copy of the measured tree**,
  not from another worktree of the same repo: it needs a cold cache, not a
  second checkout, and a copy with no `.git`/`.history` cannot commit anywhere
  even in principle.
- Verification: `uv run pytest -q tests/test_checks_ref.py -p no:randomly` →
  **18 passed** in 18.03 s; `uv run pytest -q tests/test_checks.py
  tests/test_checks_pipeline.py -p no:randomly` → **82 passed** in 191.30 s;
  `uv run python -c "import agentcad.core.checks, sys; assert 'OCP' not in
  sys.modules"` → clean; `make test-fast` → **777 passed, 1 skipped** in
  185.60 s (unchanged — every test in this slice is `slow`); `make test` →
  **1014 passed, 1 skipped** in 1372.52 s (0:22:52), against 0099's 996-passed
  baseline — exactly the 18 tests this slice adds, with the one slice-2 seam
  test rewritten rather than added.
