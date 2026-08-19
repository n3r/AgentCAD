# 0297 — PRD-026 slice 3: the ⌘K command palette, tool forms from JSON Schema, result routing, UX events

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (with Claude)

## Summary

The palette (PRD-026 spec §3): `Mod+K` opens a modal combobox over four
sources merged at query time — UI actions, the **live** tool registry
(`GET /api/tools`, never a frontend enumeration — FR6), registered dialog
views ("Open: …"), and navigation targets (projects, parts). A tool whose
required arguments the context cannot answer gets a form generated from its
JSON Schema; execution goes through the same `POST /api/tools/{name}` agents
use; results route to a toast, a non-modal result panel, or the dialog's
error line. The browser now reports `dialog_opened`/`dialog_submitted`/
`palette_executed` through `POST /api/ui/events`, and handles the agent's
`ui_open` event.

## Changes

- **`shell/palette_model.js` (pure, 500 LOC)** — `score` (fzy-style two-row
  DP, not greedy: word-start/camel +80, contiguous +60, per char +100, per
  skip −3, shorter-text bonus; text considered up to 2000 chars so a ~640-char
  tool description stays searchable), `rank` (score › recent index › section
  actions‹navigation‹tools › title; empty query = recents then the head of
  each section), `entriesFromTools/Actions/Views/State`, `formFields(schema,
  ctx)` (required first, a divider, optional; `project`/`part_id`/
  `instance_id` prefilled; string→text, enum→select, number, integer step 1,
  boolean→checkbox, object/array→json), `needsForm`, `coerce` (numbers, json,
  explicit false, empty optionals omitted; a broken json field is named),
  `routeResult` (`{error}` → error; ≤ 120 chars or ≤ 3 scalar keys → toast;
  else panel), `summarize`, `errorMessage`, `resultText` (64 KiB cap,
  circular-safe), `pushRecent` (20).
- **`shell/palette.js`** — registers `help.palette` (`Mod+K`, `menu:
  "help/10"`) and the `palette` view; `role="combobox"`/`listbox`/`option`
  with `aria-activedescendant`; `↑/↓/PageUp/PageDown/Enter` are intercepted
  at **`window` capture** (before `dialogs.js`'s document-capture listener
  would read Enter as a form submit) and removed on every close path;
  `Shift+Enter` opens the form for a tool that would otherwise run
  immediately; a disabled action is listed dimmed and refuses with a toast;
  the `tool:<name>` action seam overrides the generic form when such an
  action exists (none yet); the tool cache is invalidated on the rising edge
  of `state.connected`; a failed `listTools()` shows one quiet toast; the
  `tool-result` panel (`view: "tool-result"`, non-modal, pretty JSON, Copy);
  a refusal keeps the form open with the message on its error line. One
  rule for telemetry: *invoked = run, including a refusal; a cancelled form
  is not a run* — `palette_executed` is emitted once per run (from
  `actions.onRun` for actions, from the palette for tools/views/navigation)
  and the recent list is updated at the outcome.
- **`shell/events.js`** — `emit(type, payload)`/`emit(event)` →
  `api.postUiEvent`, fire-and-forget, allow-list filtered client-side to the
  route's contract (three types, `view`/`action`/`tool` clipped to 80) so a
  well-formed client never earns a 422. `dialogs.setEmitter(events.emit)`.
- **`shell/dialogs_model.js` / `dialogs.js`** — a `{divider: true}` field
  renders as `<div class="dlg-divider" role="separator">` and is filtered out
  of the dialog's value/validation path (the task review caught it rendering
  as a stray unlabeled text input between the required and optional halves).
- **`api.js`** — `listTools()`, `postUiEvent(body)`. **`main.js`** —
  `events.init`, `dialogs.setEmitter`, `actions.onRun` → `palette_executed`
  for `source === "palette"`, `palette.init`; `handleEvent` gains `case
  "ui_open"` → `dialogs.openView(view, args, {by: "agent"})` and ignores the
  three telemetry types; the `#palette-btn` toolbar button labelled from
  `shortcuts.list()` (`⌘K` on macOS, `Ctrl+K` elsewhere); menu orders
  regrouped so the View toggles (30–32) and the palette/cheat-sheet (help
  10/11) read as groups (repmode → view/40,41; theme → view/50).
- **`index.html`** — `#palette-btn`. **`app.css`** — the `/* palette */`
  block (`.palette-*`, `.dlg-result pre`, `.dlg-divider`), tokens only.

## Files

- `frontend/js/shell/{palette_model,palette,events}.js` — new
- `frontend/js/shell/{dialogs_model,dialogs}.js` — the divider field
- `frontend/js/{api,main}.js`, `frontend/index.html`, `frontend/css/app.css`
- `tests/test_frontend_shell.py` — +75 tests (palette model, the palette in
  a DOM stub driving the shipped module, the events client, AC2 both
  directions, **AC3 parity**: a fixture `Tool` registered into the live
  registry → `GET /api/tools` → node `entriesFromTools` finds it with
  name+description — no frontend change)

## Notes

Reviewed (Opus task review → Needs fixes for the divider + three untested
behaviours; fix round 1 → scoped re-review). Deferred minors recorded in the
ledger: `Mod+K` is silenced behind the seven legacy modals until slice 2
adopts them (worth a user-guide line); the palette registers itself
agent-openable; navigation rows show raw part ids; `default: null` counts as
answered. Not verified in a browser (no Chrome reachable) — the capture-order
interception and the look of the divider/result panel are evidence-graded on
the node tests that drive the shipped modules against a DOM stub.

`make test` — **4750 passed, 44 skipped** on the combined slice 3 + slice 4 tree (the run reported `12 failed, 4741 passed`: nine `*_count_is_cited` guards read the newest changelog before its count was filled, `test_checks_pipeline` asserts a clean tree while the slices were uncommitted, `test_sketch_diagnostics::test_the_full_budget_completes_the_same_analysis` passed on an isolated re-run, and `test_checks_cli`'s 1 ms `--budget` race lost again at load average 17–65 — CI is authoritative).
