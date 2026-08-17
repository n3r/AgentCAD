# 0207 — Configurations in the browser: a config bar, provenance marks, a matrix

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (PRD-012 slice 7)

## Summary
The browser half of PRD-012. A part that declares a family gets a configuration
switcher above its parameters, a divergence chip with a one-click reset, marks
saying where each parameter's value came from, a tree badge, a per-instance
binding picker in the placement panel, and a modal matrix that builds the whole
family and compares its metrics. Assembly geometry becomes content-addressed so
two instances of one part bound to different configurations render as two
different meshes. A part with no configurations gets none of it — the app looks
exactly as it did before (G5).

## Changes

- **`frontend/js/api.js`** — five configuration passthroughs (`listConfigs`,
  `setActiveConfig`, `clearActiveConfig`, `buildConfigs`, `setInstanceConfig`)
  and `getMeshByKey`, the sixth hand-rolled `fetch` (the ACM1 body is binary,
  so it cannot go through `request()`), carrying `X-Agent-Id` like the other
  five. `setInstanceConfig` sends `{config}` **even when null** — the route
  forwards on `"config" in body`, and an omitted key would read as "no
  argument at all" rather than *unbind*. `drawingSvgUrl` gained a `params`
  argument (`{config, dim_table}`) because the GET regenerates the sheet.
- **`frontend/js/inspector.js`** — `renderConfigBar(part)` renders a static
  `#config-bar` host (see index.html) with a base/configuration `<select>`, a
  **Matrix** button, and the divergence chip. The `<select>` is rebuilt only
  when `[part.id, active_config, Object.keys(configs)]` changes — the
  `materialSig` idiom, so a rebuild landing while the dropdown is open cannot
  close it — while the chip repaints every render (divergence moves on a param
  edit, which does not move the signature). `setActiveConfig(name)` is shaped
  like `setMaterial`, including the single-use `handleWriteConflict` retry, and
  routes `"base"` to `clearActiveConfig` (a `null` config on the PUT is a 422 by
  design). **Reset to M** clears every explicit override itself with one
  `patchParams(…, {name: null, …})`: `set_active_config` on the *already active*
  configuration is a no-op for overrides, so re-selecting M would not do it.
  `markConfigSources(part)` toggles `.param-from-config` / `.param-overridden`
  on each `.param` wrap and is called immediately before `decorateParams()` —
  **not** through `setParamDecorator`, whose single slot comments.js owns.
- **`frontend/js/configs.js` (new)** — the matrix modal. `init/open/
  onRebuildEvent`, a `.modal.wide` + `.prop-table` with a row per configuration
  and columns mass / volume / bbox X,Y,Z (+ spec chips when the part declares
  SPECS), a per-row `building…` state driven by the config-tagged WS events, and
  a module-local `lastMatrix` so reopening for the same part shows its numbers
  immediately. A member that fails to build is a red row **in place**, never the
  loss of the matrix; a refusal (unknown name, unloadable script) takes the
  panel, because that is a different fact.
- **`frontend/js/main.js`** — `instanceMeshes: Map<mesh_key, entry>` beside
  `meshBuffers`. `loadAssembly` fetches only the `mesh_key`s it does not already
  hold (the unconditional per-refresh refetch of every part goes away as a side
  effect) and prunes keys no instance references; `renderAssemblyFromCache`
  looks up by `inst.mesh_key`; assembly-mode `reloadMesh(partId)` schedules an
  assembly refresh instead of refetching part-addressed geometry, which cannot
  answer *which* mesh a bound instance now shows. Both maps are cleared together
  on a project load and a branch context reload. The three `rebuild_*` cases
  hand a `ev.config`-carrying event to `configs.onRebuildEvent` and **return** —
  `state.rebuilding` is a Set of bare part ids, so the first configuration to
  finish would otherwise clear the part's dot and repaint the inspector with
  another variant's metrics. `runExport` sends `config` through
  `api.callTool("export_part", …)` when one is loaded (the core export route
  takes none), and checks the passthrough's HTTP-200 `{error}` body.
  `configs.init(actions)` joins `boot()`.
- **`frontend/js/tree.js`** — a `.row-badge` on a configured part (the active
  name, or `cfg` at base; title `N configurations · active: …`), and
  `part@config` on a bound instance row.
- **`frontend/js/placement.js`** — a configuration `<select>` **before** the
  mated early-return (a mate positions an instance; it does not choose which
  configuration is instanced), `config` in the rebuild signature, and
  `api.setInstanceConfig` on change followed by `refreshProject()`. The family
  comes from `state.project.parts` — no new fetch.
- **`frontend/js/drawings.js`** — a `dim table` checkbox for a configured part,
  remembered for the session; preview and DXF pass the part's `active_config`,
  and the preview passes `dim_table` on **both** the POST and the GET (the GET
  regenerates, so asking for the suffixed file without them would serve a sheet
  the POST did not write). The download name and the modal title carry the
  configuration.
- **`frontend/js/merge.js`** — the integrity bullet prints `config` and falls
  back to `message`, so PRD-012's `dangling_instance_config` names the
  configuration that went missing instead of reading `…: box_1 → box`.
- **`frontend/index.html`** — the `#config-bar` host between `#banner` and
  `#pane-params` (outside the pane a full param rebuild wipes), the
  `#configs-modal` matrix dialog, and the drawing modal's dim-table label.
- **`frontend/css/app.css`** — `#config-bar`, `.cfg-*`, `.param-from-config` /
  `.param-overridden`, `.placement-config*` and `.drawing-opt`, using **only
  existing tokens** so light mode keeps working. The provenance mark is a left
  rule and not a background: PRD-008's per-param thread badge sits in the same
  row and has to stay readable. A row can be both from-config and overridden
  (the family declares it, you typed over it); `.param-overridden` is ordered
  second so the value actually in effect is the mark you see.
- **`tests/test_presence.py`** — the hand-rolled-`fetch` identity pin counts
  `X-Agent-Id` in the served `api.js`; `getMeshByKey` is a sixth one, so the
  count moves 5 → 6 and the comment names all five plus `request()`. The pin's
  claim is unchanged: every hand-rolled fetch speaks under the browser identity.

## Files
- `frontend/js/api.js` — configuration passthroughs, `getMeshByKey`, drawing URL params
- `frontend/js/inspector.js` — `renderConfigBar`, `setActiveConfig`, `markConfigSources`
- `frontend/js/configs.js` — **new**, the matrix modal
- `frontend/js/main.js` — `instanceMeshes`, event guard, export config, boot wiring
- `frontend/js/tree.js` — configured-part badge, `part@config` instance rows
- `frontend/js/placement.js` — the per-instance configuration picker
- `frontend/js/drawings.js` — `config` + `dim_table` on preview and save
- `frontend/js/merge.js` — `config` / `message` in the integrity bullet
- `frontend/index.html` — `#config-bar`, `#configs-modal`, the dim-table label
- `frontend/css/app.css` — the configuration styles (existing tokens only)
- `tests/test_presence.py` — the hand-rolled-fetch count, 5 → 6

## Notes

**Browser session (AC9).** A real headless Chrome (Chrome for Testing 1234,
`--use-gl=angle --use-angle=swiftshader`) driven end to end against
`agentcad serve --port 8630 --projects-dir <scratch>`, in a scratch project
`cfgdemo` holding `tests/conftest.py`'s `FLANGE_SCRIPT` and its
`THREE_SIZE_CONFIGS` s/m/l family. `localStorage["agentcad.project"]` was set in
an init script *before* any page script ran, so the session never opened — and
never mutated — the repo's bundled examples (`git status examples/` is clean).
The Claude-in-Chrome MCP extension was not connected in this environment
("Browser extension is not connected", twice), so the session was driven with
Playwright over the same real Chrome instead; console messages and every HTTP
response were captured from the page, which is what the two counts below are.

Steps and what was observed:

1. Booted into `cfgdemo`; `#config-bar` visible, `<select>` at `base` with
   options `base / Small(s) / Medium(m) / Large(l)` (labels shown, names as
   values).
2. **base → M → L** in the switcher: metrics and viewport followed —
   base/M `140 × 140 × 14 mm`, `372.6 g`; L `200 × 200 × 14 mm`, `740.8 g`
   (M's declared params *are* the script defaults, so base and M agree by
   design; the family's three masses are distinct in the matrix below).
   Screenshot pixels differ across all three.
3. **Edited `thick` to 22 on M** → chip `M — modified`, `title="overrides:
   thick"`; provenance marks `outer_d/bore_d/bc_d → param-from-config`,
   `thick → param-overridden`, the three unconfigured params unmarked.
4. **Reset to M** → chip gone, `thick` back to `14`, `param-overridden` gone
   and the three from-config marks intact.
5. Two instances **bound through the placement picker** — `flange_a → s`,
   `flange_b → l`. Sidebar rows read `flange@s` / `flange@l`; the assembly
   reports two distinct mesh keys (`13a413f5…` / `14c4d3cb…`) and the stage
   shows two different sizes of one part.
6. **Matrix** (`Matrix` → `#configs-modal`): `s 203.4 g / 75,342 mm³ /
   100×100×14`, `m 372.6 / 138,016 / 140×140×14`, `l 740.8 / 274,362 /
   200×200×14`, all `cached`, `m` marked as the active row.
7. **Drawing preview with the dim table**: title `flange@m · drawing`, download
   name `flange_m_drawing.svg`, and the sheet carries the configured-parameter
   table (config / outer_d / bore_d / bc_d / X / Y / Z for Small, Medium,
   Large) in the top right.

A second pass covered G5 and the light theme: a part with no configurations
(`plain`) leaves `#config-bar` with `className === "hidden"`, every `.param`
row bare (`"param"`), and no tree badge; the light theme renders the bar, the
badge and both provenance marks correctly (only existing tokens, both theme
blocks unchanged).

Re-run in full against the fix-round-1 build (fresh numbers, and these are the
ones that count):

- `ERROR COUNT: 0` (page console; the only messages were Chrome's own
  swiftshader/WebGL performance warnings)
- `FAILED REQUESTS: 0` (every HTTP response 2xx/3xx, both passes)
- **Frame time, 3-configuration assembly:** avg 50 ms (max 85 ms) over 80
  frames — that is software rasterization (swiftshader) doing the work, not the
  configuration path; the spec's `GEOM_CACHE_MAX = 32` concern did not bite (two
  bound configurations plus the part-mode mesh is three cache entries). Feel on
  a real GPU is the same as any two-instance assembly: the extra cost of a bound
  instance is one extra cached mesh, and `loadAssembly` fetches a key only once.

Two smaller decisions worth recording. The chip and its reset use the
configuration's **name**, upper-cased (`M — modified`, `Reset to M`); the
`<select>` uses the **label** (`Medium`). The name is the identity and the label
is prose, and the chip is where the identity matters. And `instanceMeshes` is
pruned to the keys the current assembly references on every `loadAssembly`,
because content-addressing otherwise means a session that walks a family holds
every mesh it ever rendered.

## Fix round 1

Review answered: 1 partial-spec item, 2 Important, 9 minor. Frontend only.

- **I1 — the matrix flashed "declares no configurations" through its first
  round trip.** `currentRows()` now seeds from state (`state.part.configs`,
  falling back to the project row) instead of waiting on `list_configs`, so the
  first paint is the real table with `building…` in every cell. `list_configs`
  is no longer awaited before the build either — it races it, and now **earns
  its keep by rendering `referrers`**: each row names the assembly instances
  bound to it, which is the one fact a build row cannot carry. Proved with a
  `MutationObserver` over `#configs-body` across a cold open: paint 0 is the
  seeded table, paint 1 the metrics, paint 2 the referrers, and
  **empty-state flashes: 0**.
- **I2 — the divergence chip blinked off after every debounced PATCH.**
  `inspector.applyRebuildResult` (both branches) and `main.js`'s
  `rebuild_finished` / `rebuild_failed` replaced `state.part.status` wholesale,
  dropping `diverged` / `diverged_params` until the next `get_part` landed — so
  a slider drag flapped the chip and its Reset button and jumped the pane by a
  row. All four now spread the previous status. Session probe: two further
  edits plus the rebuild events, chip and Reset both still present (`True`).
- **Partial spec — `merge.js` integrity rows.** `dangling_instance_config`
  now prints the path as `instance → part@config` **and** its `message`, which
  is where the remediation (`set_instance_config null`) lives; `message` also
  remains the fallback for a kind this build has never heard of.
- **Minors.** `.cfg-body .bad` gets `var(--err)` (the scoped `.bad` rules did
  not reach the matrix, so a failed row read as ordinary text) · a finished row
  stops saying `building…` while the serial build works through its siblings
  (`building && live === "building"`) · `drawings.previewSvg` gained a
  `previewSeq` guard checked after every `await` plus `close()`, and revokes
  the previous object URL before reassigning (toggling the checkbox leaked one
  blob per render, and a slow first response could repaint over a newer sheet)
  · `placement.setInstanceConfig` uses the same two-attempt
  `handleWriteConflict` shape as `inspector.setActiveConfig` (defence in depth:
  `set_instance_config` takes no `write_scope` today, and the helper answers
  false for anything that is not an overridable 409) · `deletePart` clears
  `instanceMeshes`, which is content-keyed and so cannot be selected by part id
  · `resetToActiveConfig` toasts "No overrides to clear" instead of returning
  silently · two comments say "configuration" instead of "variant".

One harness change, not a product change: the AC9 script's fixed sleeps around
the write gestures became `settle()`, which polls for the expected DOM state
and dismisses a claim dialog whenever one interposes. A 409 claim refusal
arrives on the write's round trip, which is not on a schedule, so a fixed sleep
could dismiss the dialog before it appeared and then wait forever behind it.

Full suite: `make test` — 3489 passed, 7 skipped in 12:10 on 8 workers, with exactly one red row: `tests/test_prd012_acceptance.py::test_ac8_the_full_suite_count_is_cited` itself, red only because this line had not yet been filled; with it filled the same suite is 3490 passed, 7 skipped.
