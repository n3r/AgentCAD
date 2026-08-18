# 0212 — PRD-012 follow-ups: total build path, one drawing result, an honest summary

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary

Closes the four items PRD-012's whole-branch review ledgered as "deferred, not
blocking" (0209 / 0211): a landed write served as a refusal when an `AppError`
fired before the build; the drawing POST answering refusals as `200 {"error"}`
while the GET renamed every kernel failure `422 ValidationError`; the proposal
packet's `_summary` (and the browser) ignoring rebinding-only and mates-only
assembly changes; and the AC9 frame-time figures having been measured on
software GL. Items 1–3 are code; item 4 is a measurement recorded here.

## Changes

### 1. The rebuild that follows a landed write is total over pre-build refusals

- `core/model.py` grows `error_type(exc) -> str` — the wire spelling
  `ToolRegistry.call` has always produced (`NotFoundError` →
  `notfound_error`). It lives in `model` so the two producers can pin each
  other; a copy with no test drifts.
- `core/service.py` grows `_REBUILD_REFUSED_HINT`,
  `AgentCADService._refused_build(...)` and the public seam
  **`rebuild_after_write(proj, part_id)`**:

  ```python
  try:
      return self._rebuild(proj, part_id)
  except AppError as exc:
      return self._refused_build(proj, part_id, exc,
                                 status_key=self._status_key(proj, part_id))
  ```

  `_refused_build` does everything the `KernelError` arm of `_build_with` does
  for a build that ran and failed — `_status` written (`state: "error"`,
  `cache_key: None` because no geometry was ever addressed, the previous good
  `metrics` kept), `rebuild_failed` published, `{ok: false, error}` returned —
  plus its own `hint`, which is also what stops `with_hint` from decorating it
  with the script-failure one.
- **`_rebuild` and `_build_with` are byte-identical to `main`.** The conversion
  is deliberately *not* in them: `_rebuild` is also the build every READ path
  runs (`_ensure_built` ← `get_metrics` / `mesh_info` / `ensure_mesh` /
  `mesh_summary` / `get_assembly`, plus `packet`, `checks`, `merge`), and three
  of those callers re-raise an `ok: false` as a `KernelError`. See "Fix round 1"
  below for what that cost when the first cut put it there.
- All five "landed write + rebuild" call sites go through the seam:
  `service.set_params` and `service.update_part` (`core/service.py`),
  `set_active_config` and `set_part_configs`' nested `rebuild`
  (`core/tools_configs.py`), and `set_solid_materials` (`core/tools_solids.py`).
- Two of them also had a **tail read after the rebuild**, which reintroduces
  the same defect ten lines down (a `store.get_part` that can raise
  `NotFoundError` *after* the manifest was saved). Both now read the post-state
  right after the write, inside whatever lock the tool holds:
  `tools_configs.set_active_config`'s `after = store.get_part(...)` moved
  inside `manifest_scope`, and `tools_solids.set_solid_materials` reads
  `solid_materials` straight after `save_manifest`, before it publishes.
- `server/routes_configs.py`'s module docstring gains the qualifier that makes
  its own `PATCH …/params` precedent argument true for every failure class —
  *on a write path* — and says why `_rebuild` itself is not total.
- `docs/agent-api.md` Conventions: how to tell a pre-build refusal from a build
  that ran (the `error.type` is **not** one of the five `protocol.py` kernel
  constants, and the `hint` begins "the change was saved"), and that only a
  write path ever produces one.

### 2. One `_drawing_result` for both drawing routes

- `server/routes_drawing.py` gains `_KERNEL_TYPES` (the five `protocol.py`
  constants, imported — there is no `"crash"`) and `_drawing_result(payload)`,
  used by the POST and the GET.
  - an `AppError`-class refusal raises through `_result` → 404 / 422 with the
    house type (the POST stops serving refusals as `200 {"error"}`);
  - a kernel-class failure re-raises as `KernelError` → `app.py`'s **502** with
    the kernel's own type and `details.traceback` intact (the GET stops calling
    a worker crash or timeout a `ValidationError`, i.e. "your request was
    invalid, do not retry").
  - `generate_drawing` returns no `ok` post-state, so every `{"error"}` it
    yields is one of the two classes; a success is untouched.
- `frontend/js/drawings.js`: the two comments asserting the old contract are
  rewritten ("the POST raises like every other pack route; the `catch` below is
  the error path") and the two now-unreachable `result.error` branches in
  `showDrawing` / `saveDxf` are deleted. `ApiError` already carries
  `.error.message` for a 502 exactly as for a 4xx, so the UX is unchanged.

### 3. The summary counts what the reviewer can see

- `core/packet.py`'s `_summary` counts **distinct touched instance ids** across
  added ∪ removed ∪ moved ∪ mates ∪ configs, and adds `mates_changed` /
  `configs_changed` counters. Summing the five lists would have reported "3
  instance changes" for one instance that moved, was re-mated and was rebound —
  a number larger than the assembly and disagreeing with the bullets below it.
- `core/proposals.py`'s `_absent_packet` stub grows the same two keys, so the
  two producers of `summary` cannot ship two shapes of it.
- `frontend/js/proposals.js`'s `assemblyBlock` renders the `configs_changed`
  rows it has never shown (`config f1: s → l`, `base` for the unbound state) —
  the packet has carried them since PRD-012, so a rebinding-only proposal used
  to render an Assembly section with nothing in it.
- `docs/agent-api.md`, the packet section: one sentence on the three counters.

### 4. Frame time on a real GPU

No code. The AC9 probe was re-run headed on the real GPU with a vsync-relative
criterion; the numbers, both renderer strings, the criterion and the verdict
are in the Notes below, and `0207-configs-ui.md` gets one pointer line under
its Notes.

## Files

- `agentcad/core/model.py` — `error_type(exc)`
- `agentcad/core/service.py` — `_REBUILD_REFUSED_HINT`, `_refused_build`, the
  new `rebuild_after_write` seam, `set_params` and `update_part` wired to it,
  and the `AppError`/`error_type` imports. **`_rebuild` and `_build_with` are
  unchanged from `main`** — see "Fix round 1".
- `agentcad/core/tools_configs.py` — `set_active_config` and
  `set_part_configs`' nested rebuild on the seam; `set_active_config` reads its
  post-state inside the lock
- `agentcad/core/tools_solids.py` — `set_solid_materials` on the seam, and its
  `solid_materials` read moved ahead of the publish (R2)
- `agentcad/server/routes_configs.py` — docstring qualifier
- `agentcad/server/routes_drawing.py` — module docstring, `_KERNEL_TYPES`,
  `_drawing_result`, both routes
- `agentcad/core/packet.py` — `_summary`
- `agentcad/core/proposals.py` — `_absent_packet`'s summary stub
- `frontend/js/drawings.js` — comments + two dead error branches removed
- `frontend/js/proposals.js` — `configs_changed` bullets in `assemblyBlock`
- `docs/agent-api.md` — one bullet for item 1, one sentence for item 3
- `docs/changelog/0207-configs-ui.md` — one pointer line under Notes
- `docs/superpowers/plans/2026-08-18-prd-012-followups.md` — the plan this
  implements (new file, added with the branch)
- `AGENTS.md` / `CLAUDE.md` — the two PRD-012 gotcha bullets for
  `rebuild_after_write` and `_drawing_result`
- `tests/test_configs.py` — `error_type` pinned against `ToolRegistry.call`
- `tests/test_configs_api.py` — `TestPreBuildRefusalsArePostStates` (15 tests:
  the five write sites, and the read paths that must keep raising)
- `tests/test_configs_drawing.py` — `TestDrawingRouteFailureClasses` (7 tests)
- `tests/test_service.py` — the refused arm, the `KernelError` arm and the
  read-path arm, service level, no tool packs
- `tests/test_checks_pipeline.py` — a missing script file is still an `error`
  row with a harness `errors[]` entry (R4)
- `tests/test_packet.py` — five `_summary` cases
- `tests/test_proposals.py` — the two producers agree on the key set

## Notes

**No existing test was edited.** `grep -rn "/drawing" tests/*.py` finds three
route call sites and none of them pinned the POST's old `200 {"error"}` shape
(plan review P15), and `grep -rn "script file missing" tests/` is empty, so
nothing pinned the old pre-build refusals either. Everything here is additive.

**The kernel type a raising `build` actually emits is `script_error`**, not the
`contract_error` the plan predicted — measured against the real worker
(`ValueError: thickness above 50 mm is not manufacturable`, with
`details.traceback` and `details.line`). Both constants are in `_KERNEL_TYPES`,
so the implementation is unaffected; the tests pin the measured value.

**No `rebuild_started` is orphaned by the refusal.** With the conversion on the
write seam rather than inside `_build_with`, an `AppError` from the pre-kernel
statements propagates out of `_rebuild` exactly as on `main`, and
`rebuild_after_write` catches it one frame up. Every `AppError` source that can
fire *without a concurrent mutation* (`material_density`, `_cache_key_for` →
`_content_signature` → `read_script`) sits above the `rebuild_started` publish;
`store.read_script` / `store.imports_dir` in the `build_params` block sit below
it but read what `_content_signature` read four lines earlier, so they need a
concurrent delete to fire. Either way `frontend/js/main.js` clears
`state.rebuilding` on `rebuild_failed`, which `_refused_build` always
publishes.

**Item 4 — frame time on a real GPU (2026-08-18).** Playwright driving a
**headed** Google Chrome, frontmost for the whole probe (macOS throttles
`requestAnimationFrame` for occluded or background windows, so a backgrounded
one produces garbage), against
`uv run agentcad serve --no-open --projects-dir <scratch>/projects`, project
`cfgdemo` at the 3-configuration stage (`flange` s/m/l active `m`, instances
`flange_a@s` + `flange_b@l`). No editing / reset / binding steps were re-run —
the fixture was already at that stage, and those steps mutate it and buy
nothing.

Two runs, kept separate because they probed different things:

- **Run 1 — headed Chrome via its executable path**, no `--use-gl` / `--use-angle`
  flags. Unmasked renderer (`WEBGL_debug_renderer_info`): `ANGLE (Apple, ANGLE
  Metal Renderer: Apple M1 Max, Unspecified Version)`. Assembly stage, three
  80-frame `requestAnimationFrame` probes: **idle avg 8.3 / p50 8.3 / max
  10.4 ms**, **static 8.3 / 8.3 / 10.4**, **post-drag 8.3 / 8.3 / 10.4**.
- **Run 2 — the control run**, Playwright `channel="chrome"`, headed and
  frontmost. `gl.RENDERER = "WebKit WebGL"`, same unmasked string. One 80-frame
  probe per stage: `about:blank` **avg 8.3 / p50 8.3 / max 10.2 ms** (the
  display's vsync interval, 120 Hz ProMotion), part stage (`flange` @ `m`)
  **8.3 / 8.3 / 10.4**, assembly stage (`flange_a@s` + `flange_b@l`)
  **8.3 / 8.3 / 10.3**. There is no idle / static / post-drag breakdown in this
  run — that is run 1's.
- **Criterion:** median frame delta within ~1 ms of the vsync interval and no
  frame above 2× it. Stated against the cap rather than an absolute because the
  viewport runs a continuous `setAnimationLoop` and rAF deltas are clamped to
  the refresh interval whenever the renderer keeps up — a healthy 120 Hz page
  *cannot* beat 8.3 ms, so an absolute "< 16.7 ms" threshold would have been
  unfalsifiable.
- **Verdict: PASS** — the 3-configuration assembly renders at the display cap
  with zero dropped frames, identical to the blank-tab control. No LOD change
  needed (`?lod=lod1` in `loadAssembly` stays unused); the spec's risk item
  closes. 0207's `avg 50 ms (max 85 ms)` was swiftshader software GL, not the
  configuration path.

**Out-of-scope follow-up observed during item 4:** the browser console carried
exactly one error — `GET /api/auth/session` → **404**. That is PRD-005a's
`auth.js` probing the session in LOCAL mode, where the auth routes are not
mounted. Not PRD-012's, and worth a 005a follow-up (answer 204 / `{mode:
"local"}`, or skip the probe in local mode).

**Two other write-then-`_rebuild` call sites stay on `_rebuild`.**
`packages/gate.py:1489` (`_build_one`) and `packages/from_step.py:273` both add
a part to a scratch/gate project and then rebuild it, but they are not
user-facing writes — nothing a caller authored is committed and then reported
back — and both already handle the failure themselves: the gate wraps the call
in `except Exception` and turns it into one red row, and `from_step` reads
`result.get("ok")` and raises its own `ValidationError` naming the file. They
are unchanged from `main`; the seam is for the five tools a user or an agent
actually writes through.

**Four other copies of the registry's wire mapping exist**
(`type(exc).__name__.replace("Error","").lower() + "_error"`). The source of
truth is `ToolRegistry.call` (`tools.py:63`); `model.error_type` (`model.py:76`)
is the only one **pinned** against it (`tests/test_configs.py`). The unpinned
copies are `core/merge.py:602`, `core/checks.py:938`, `core/packet.py:473` and
`core/packages/gate.py:260` — all four agree today, which is exactly why
`merge.py:596-605` and `gate.py:1487-1491` are byte-identical through this
change. Folding them onto `model.error_type` is a four-line follow-up,
deliberately out of scope here.

**Also deliberately out of scope** (unchanged from the plan): `_config_status`
growth; the memo write outside `service._lock`; per-part de-duplication; `""`
as `active_config`; `configs: {}` residue in the pure merge; the bare-string
merge warning; the lock-free instance RMW in `tools_mates` / `routes_assembly2`;
`get_assembly` raising on a dangling binding (a contract decision, not a
follow-up).

## Fix round 1

Critical review (Opus): 1 Critical, 4 Important, 6 Minor; Codex spark: no
findings. Items 2 and 3 were clean. Every finding was in one family — the first
cut put the `AppError` conversion **inside `_rebuild`/`_build_with`**, and
`_rebuild` is not only the write paths' build.

- **R1 (Critical) — the read paths.** `get_metrics` / `mesh_info` /
  `ensure_mesh` / `mesh_summary` re-raise an `ok: false` from `_ensure_built`
  as a `KernelError`, which `app.py` answers **502**. With a total `_rebuild`,
  `GET …/parts/{id}/metrics` for a missing script answered `502` on the first
  call (memo miss → `_rebuild` → refusal → `KernelErrorFromResult`) and `404`
  on the second (memo hit → `_ensure_built`'s own `_cache_key_for` raises) —
  two classes of answer to two identical requests, and 502 ("the kernel failed,
  retry") for a permanent client-side fault. **Fix:** `_rebuild` and
  `_build_with` are reverted to `main` byte-for-byte and the conversion moved
  to the new `service.rebuild_after_write` seam, used only by the five write
  sites. **Pinned:** `GET …/metrics` is 404 twice in a row; `mesh_info` raises
  `NotFoundError` twice; `_rebuild` itself raises.
- **R5 (Important) — `get_assembly` answered one fault two ways.** An unbound
  instance builds through `_ensure_built` (which returned the refusal → HTTP
  200 with `state: "error"`) and a bound one through `_ensure_config_built`
  (whose own `_cache_key_for` raises → 404 for the whole assembly). Closed by
  the same revert; **pinned** with both instance shapes raising.
- **R4 (Important) — geometry-CI rows.** `checks._build_item`'s `except` arm
  stopped being reachable, so a missing script file moved from an `error` row
  **plus** a harness `errors[]` entry to a plain `fail` row — changing
  `summary.errors` / `summary.failed`, `report.md`'s `## Harness errors`
  section and the CLI's verdict line. Closed by the revert; **pinned** with a
  `CheckRunner._build_item` row in `tests/test_checks_pipeline.py`.
- **R2 (Important) — `set_solid_materials` was not actually fixed.** Its tail
  `store.get_part(...).solid_materials` sat *after* the rebuild, so the
  entry-vanished half of the case still answered a bare refusal (no `ok` key)
  for a landed manifest write; the original test passed only because it
  unlinked the script file, which leaves the entry intact. The read moved
  ahead of the publish and a second test drives the real entry-vanished race.
- **R3 (Important) — the `docs/agent-api.md` discriminator was false.** "No
  `details.traceback`" identifies a pre-build refusal only against
  `script_error` and `kernel_error`; `contract_error`, `timeout` and
  `kernel_crash` carry none either, so an agent applying it to a timeout or a
  crash would have concluded "the build was never attempted, do not retry".
  Rewritten to discriminate on `error.type` (not one of the five
  `kernel/protocol.py` constants) or on the `hint`, and to say that only a
  **write** path ever produces one.
- **R6 (Minor)** — `routes_configs`'s docstring said `_build_with` produces the
  same `{ok: false, error, hint}` for both arms; the `KernelError` arm returns
  no `hint` at all (`with_hint` adds one a layer up, and only for tools).
  Corrected, and it now documents `rebuild_after_write` rather than claiming
  `_rebuild` is total.
- **R7 (Minor)** — the "no current source sits below `rebuild_started`" comment
  was stronger than the code (`read_script` / `imports_dir` in the
  `build_params` block do). The comment is gone with the revert; the honest
  version is in the Notes above.
- **R8 (Minor)** — the `_ensure_config_built` memo test drove a state that
  cannot occur (`_cache_key_for` runs before `_build_with`, so every named
  `AppError` source raises out of `_ensure_config_built` first) and never read
  the memo back. Replaced by the R5 assembly pin.
- **R9 / R10 / R11 (Minor)** — the four unpinned copies of the wire mapping are
  now named in the Notes; item 4's two runs are reported separately; the plan
  file is in the Files list.

Focused verification (after the fix round):
`uv run pytest tests/test_configs.py tests/test_configs_api.py tests/test_configs_drawing.py tests/test_service.py tests/test_solids.py tests/test_drawings.py tests/test_drawing_holes.py tests/test_packet.py tests/test_proposals.py tests/test_prd002_acceptance.py tests/test_server.py tests/test_specs.py tests/test_holes.py -q`
plus `tests/test_checks_pipeline.py` (the new geometry-CI row) and
`tests/test_prd012_acceptance.py` — **560 passed, 2 skipped** (the two skips are
the pre-existing `importorskip`s) — and `node --check` on
`frontend/js/drawings.js` and `frontend/js/proposals.js`.

Full suite: `make test` — 3940 passed, 7 skipped in 8:41 on 8 workers (measured after fix round 1; the first cut measured 3935 passed, 7 skipped).
