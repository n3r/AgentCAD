# 0092 — design-spec chips in the inspector's Parameters pane

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary
PRD-003 Slice 6: the feature becomes **visible**. Every declared check gets one
chip under the parameter warnings, coloured by status and carrying its
measurement, its requirement and its message in the `title` — live on every
rebuild, because `state.part.specs` already rides the part payload. This is
FR13's MVP half (AC8) only: the requirement-grouped project panel and the
viewport thin-point marker are Phase 2 and are deliberately absent.

## Changes

### The chip strip — `frontend/js/inspector.js`
- **`appendSpecsHost()`** mirrors `appendWarningsHost()`: an empty
  `<div class="spec-block" id="part-specs">` appended by the pane builders —
  all three of them (`buildParamControls`, its no-PARAMS early return, and
  `buildReferencePane`) — so the host is torn down and re-created with the
  controls and can never accumulate.
- **`renderSpecs(part)`** is called from `render()`'s unconditional tail beside
  `renderWarnings(part)`. `part.specs == null` — the part declares nothing —
  renders **nothing at all**: no header, no "no specs" note, and (via
  `.spec-block:empty`) not one pixel of height. "Nothing declared" and
  "declared but not evaluated" are different facts and only the second is worth
  chrome. A reference part reaches the same rule through `buildReferencePane`.
- **`specChip(check)`** is the atom, modelled on `proposals.js`'s `gateChip`:
  `createElement("span")`, `className = "spec-chip spec-" + status`,
  `textContent = check.name`. **`createElement` + `textContent` only** — names,
  requirements and messages are script-controlled strings, so `inspector.js`'s
  own `row()`/`arow()` template-literal builders are the wrong precedent here.
- **The `title`** is `name — status`, then `measured vs limit` (units included:
  `2 mm vs min 2.5 mm`), then `requirement: ENG-014`, then the kernel's
  message, and for a `skip` its `reason — hint`. `specLimit` renders the
  kind-specific limit dict **generically** (suffix → unit, longest first, so
  `_mm3` beats `_mm`), so a limit key this build has never seen still reads as
  something (`max vm 200 MPa`) instead of vanishing.
- **A red strip gets one summary line** (`1 failing, 1 errored of 6 design
  specs`); a green one is chips and nothing else. The residue shape from
  Slice 4/5 is handled honestly: `specs.status === "error"` says *"Design specs
  could not be evaluated — …"* with the kernel's message, and renders the error
  records when there are any, or a single `error` chip named `specs` when the
  rebuild wrapper's catch-all produced none. An empty chip row would be a
  reassuring lie.
- **`applyRebuildResult`** now copies `result.specs` onto `state.part` next to
  `result.metrics`, guarded by `"specs" in result` (a failed build carries no
  `specs` key). Without it the chips would be stale for the length of the
  `rebuild_finished` → `refreshPartDetail` round trip after a slider drag.

### `frontend/js/api.js`
- A `// ---- design specs ----` section: `listSpecs`, `runSpecs`,
  `getProjectSpecs`, `setProjectSpecs`, one-line arrows over the module-private
  `enc()`. **The chips call none of them** — they exist for the Phase-2 panel
  and for driving the feature by hand; the comment says so.

### `frontend/css/app.css`
- A `/* --- design specs --- */` block above the analysis block: `.spec-block`
  (margins copied from `.param-warnings`, so the chips align with the parameter
  labels), `.spec-block:empty { display: none }`, `.spec-summary`, `.spec-chips`
  (`.sk-chips`' flex/gap/wrap), and `.spec-chip` copying `.gate-chip`'s recipe
  (mono 10 px, `border-radius: 9px`, `padding: 2px 7px`, `1px solid
  var(--hairline)`) minus its fixed `min-width` — a spec chip is as wide as its
  name. Four states: `pass` → `--ok` border and colour with no background (the
  `.gate-pass` solution; there is no `--ok-soft`/`--ok-ring` pair and none was
  added), `fail` → `--err-text` on `--err-soft` with `--err-ring`, `error` →
  the same in `--err`, `skip` → `--dim` on the default hairline. **No new CSS
  token**, so light mode keeps working with no extra definition.

## Files
- `frontend/js/inspector.js` — `appendSpecsHost`, `renderSpecs`, `specChip`,
  `specTitle`/`specMeasured`/`specLimit`/`specNum`, `specSummaryText`,
  `specError`; the `render()` call; the three host call sites; the
  `applyRebuildResult` specs copy
- `frontend/js/api.js` — the design-specs section
- `frontend/css/app.css` — the design-specs block

**`frontend/index.html`, `frontend/js/main.js` and `frontend/js/state.js` are
not touched** (design Decisions 5 and 9): the chips ride the existing
`state.part`, `inspector.render` is already subscribed via
`onKeys(["part"], render)`, and `main.js`'s `rebuild_finished` case already
calls `refreshPartDetail(ev.part)`. No new event, no new state key, no new
CSS token. No backend file changed.

## Verification
`make test-fast` → **717 passed, 1 skipped** (no Python changed; this proves
the server side is untouched). `node --check` clean on `inspector.js` and
`api.js`.

**Real browser session** (headless Chrome via Playwright's `channel="chrome"`,
a scratch server on port 8641 over a scratch projects dir — the user's :8630
was never touched). The scratch project holds `shell` (six declared checks:
`check_valid`, `check_wall(min_mm=2.5)`, `check_mass(max_g=400)`, a
`check_that` predicate, a `check_fem_static`, and a deliberately raising
predicate), `bracket` (four passing checks) and `plate` (no `SPECS`).
Screenshots in the session scratchpad under `shots/`:

| shot | what it shows |
|---|---|
| `00-all-green-dark` | `bracket`: four green chips, **no summary line** |
| `01-chips-dark` | `shell`: green `valid` / `wall_min` / `mass_max` / `single_solid`, grey `stiffness`, red `broken_predicate` |
| `02-chips-fail-dark` | `wall` dragged to 2.0 mm → `wall_min` red, summary `1 failing, 1 errored of 6 design specs` (**AC8**) |
| `03-chips-recovered-dark` | `wall` back to 3.0 mm → `wall_min` green again |
| `04-no-specs-dark` | `plate`: the host is empty, `innerHTML === ""`, height **0 px** |
| `05-chips-light`, `06-chips-fail-light` | the same pass and fail states in light mode |

Measured through the live DOM: at `wall = 3.0` the `wall_min` title reads
`3 mm vs min 2.5 mm · ENG-014 · min wall 3 mm is at or above the 2.5 mm
minimum`; at `wall = 2.0` it reads `2 mm vs min 2.5 mm` with class
`spec-chip spec-fail`; `stiffness` is `spec-skip` with `deferred — run_specs
evaluates this tier`; `broken_predicate` is `spec-error` carrying the
`RuntimeError`. The flip is driven by the ordinary slider/number PATCH — no
new event was added.

**Console: 0 page errors, 0 JS errors, 0 HTTP ≥ 400** across the whole session
in both themes. The only console output at all was four SwiftShader
`GPU stall due to ReadPixels` performance *warnings* from the headless GL
backend, which are the environment's and not the app's.

## Notes
- **Two small divergences from the plan's Slice 6 text**, both inside the
  listed files:
  1. The host element is `<div class="spec-block" id="part-specs">` and the
     `.spec-chips` flex row is created inside it by `renderSpecs`, rather than
     the host itself being `.spec-chips`. The plan's own Files list names
     `.spec-block`, and the wrapper is what lets the red summary line and the
     chips share one host that collapses to nothing when empty.
  2. `applyRebuildResult` copies `result.specs`, so the chips repaint from the
     PATCH post-state instead of waiting for the WS round trip. This is the
     `result.metrics` precedent one line up, and it is still "no new event".
- The `title` is multi-line where the design sketched a single line. Same
  content, and a tooltip is the one place where more lines cost nothing.
- Colour is not the only channel: the status word is the first line of every
  chip's `title`. A per-status glyph is the obvious next step and belongs with
  the Phase-2 requirement panel, not here.
- Slice 7 owns the rocketry `SPECS`, the docs and
  `tests/test_prd003_acceptance.py`. Its `test_ac8_spec_chips_verified_in_
  browser` should assert against **this file** (`0092-spec-chips-ui.md`) for
  the browser-evidence phrases, following
  `test_prd002_acceptance.py::test_ac1_browser_half_evidence_is_recorded`.
