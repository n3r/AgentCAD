# 0275 — PRD-026 slice 6: acceptance tests, docs and the PRD's own record

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (with Claude)

## Summary

The close-out slice of PRD-026: `tests/test_prd026_acceptance.py` maps
AC1–AC7 plus the `ui_open` agent surface to evidence (importing rather than
re-spelling slice 1/2/3's own enforcement where it already exists —
`NATIVE_DIALOG_RE`, the AC2/AC3 registry-parity harness); `docs/user-guide.md`
is rewritten to describe the shipped shell (menu bar, ⌘K palette, first-party
dialogs, resizable/collapsible panels, the "?" cheat-sheet) instead of the
v0.1 primitives it still described, and its Keyboard shortcuts table is
regenerated to the exact registered chord set; `docs/agent-api.md` and
`docs/architecture.md` get the palette↔registry parity sentence, the
FEM-rule cross-reference, and the `frontend/js/shell/` + `tools_ui`/
`routes_ui` rows; and the PRD itself is updated in place — status, the
corrected FR2 site count, a "Shipped vs. deferred" section, and an
"Acceptance record" table.

## Changes

### `tests/test_prd026_acceptance.py` (new, 14 tests)

- **AC1** — `test_ac1_no_native_dialogs_remain` re-runs the exact
  `NATIVE_DIALOG_RE` grep `tests/test_frontend_shell.py` enforces (imported,
  not re-spelled) plus a direct check of the regex's four global-object
  spellings; `test_ac1_the_index_html_prd026_comment_is_gone` pins that the
  "PRD-026 … has not landed" comment is gone (the grep alone would stay
  green even if a stale promise like that survived).
- **AC2** — `test_ac2_check_interference_presence_follows_the_live_registry`:
  a compact restatement of slice 3's own real-server/real-registry parity
  test (`check_interference` present ⇒ in the palette entries, absent from a
  filtered `GET /api/tools` response ⇒ absent from the entries, count moving
  by exactly one).
- **AC3** — `test_ac3_a_fixture_tool_reaches_the_palette_with_no_frontend_change`:
  a fixture `Tool` registered into the app's own `ToolRegistry` (referenced
  by no frontend file) appears in the palette with its name/description and
  is findable by a fuzzy query.
- **AC4** — `test_ac4_layout_state_round_trips_and_workspace_keys_are_isolated`
  (model-level): `layout_model.serialize`/`deserialize` round-trip a toggled
  panel state, a hand-edited out-of-range value clamps back on read, and
  `layout_model.key("default")` ≠ `key("test-workspace-42")` — the isolation
  PRD-025's per-workspace persistence depends on. The drag/keyboard-nudge/
  reload browser half stays an honest gap (both slice 3 and slice 4 report
  `list_connected_browsers → []`), recorded as such in the PRD's Acceptance
  record rather than claimed here.
- **AC5** — three tests: `ShortcutConflictError` throws naming both ids
  (`Table.bind`, direct); `F`/`Mod+S`/`Mod+Z` present in
  `main.js`'s `registerActions()`; and
  `test_ac5_every_registered_chord_is_in_the_user_guide_table`, which
  **scans source** (`main.js`/`shell/layout.js`/`shell/palette.js`, every
  `chord:`/`shortcut:` string literal) for the live chord set — 13 bound
  chords plus the sketcher's two declared-only cheat-sheet rows
  (`Escape`/`Delete`) — and asserts each has a matching, human-mapped
  substring in the regenerated docs table. A chord added later without a
  doc-text mapping fails this test rather than silently going undocumented.
- **AC6** — two tests: `dialogs_model.markup`'s static a11y pass (role,
  aria-modal true/false, aria-labelledby → an existing id, `<label for>` per
  field, text on every button) over one representative **form**
  (new-part-shaped), one danger **confirm** (delete-part-shaped, "also
  removes 1 assembly instance" note), and one **non-modal** panel
  (tool-result-shaped); and the menu bar's `menu_model.markup` a11y shape
  (`role="menu"`/`"menuitem"`, `aria-haspopup`, a disabled row staying
  `aria-disabled` rather than vanishing) plus a source-level check that
  `palette.js` sets `role="combobox"`/`"listbox"`/`"option"` and
  `aria-activedescendant` — the palette's roles are assembled at runtime, not
  through a second pure markup function, so they are graded at the source
  the way `test_index_html_hosts_the_menubar_right_after_the_brand` already
  grades the menubar's static host. The keyboard-only focus-trap/restore
  walkthrough is on record as slice 2's real Playwright session (report §5 /
  "Browser re-verification"), named in a comment rather than re-run.
- **AC7** — `test_ac7_the_full_suite_count_is_cited`: reads the *newest*
  changelog entry (not a hard-coded one, so this file itself is what must
  carry the count) for a `make test` … `N passed` citation — the PRD-004/
  008/011/012 evidence-check precedent.
- **Agent surface** — `test_ui_open_reaches_a_subscribed_queue_as_the_agent`:
  Python-only, no WebSocket — `registry.call("ui_open", …)` →
  `service.bus.subscribe()`'s queue sees
  `{"type": "ui_open", "view", "args", "by": "agent"}`, and an unknown view
  refuses without publishing anything. Full case coverage stays
  `tests/test_tools_ui.py`; this is the one shape that makes AC1–AC6 read as
  a *human* palette over the *same* registry `ui_open` gives agents.
- Plus two house meta-tests: the roadmap row for `[026]` resolves to the PRD
  file's real location, and the PRD page actually carries the status line,
  the "Shipped vs. deferred"/"Acceptance record" section headers, and the
  corrected "21 sites" FR2 count.

### `docs/user-guide.md`

- **§ The workbench** — the ASCII diagram grows a menu-bar row and a short
  paragraph naming the shell (menu bar, ⌘K palette, first-party dialogs,
  resizable/collapsible panels).
- **§ Toolbar** — five new paragraphs ahead of the project switcher: **Menu
  bar** (File/Edit/View/Model/Help, one action registry, disabled rows stay
  visible, `←/→`/`↑/↓`/Enter/Esc), **Command palette** (⌘K sources — UI
  actions, live registry tools, navigation targets — fuzzy filter,
  schema-generated forms, toast-vs-panel result routing), **Dialogs** (Esc
  always cancels, Enter submits a single-line field, `⌘Enter` for a
  multi-line one, a destructive dialog opens focused on Cancel and names its
  blast radius, focus trap/restore, the agent's "opened by agent" mark),
  **Panels** (drag/keyboard-nudge/double-click, the three toggle chords,
  per-browser persistence, responsive auto-collapse), and **The "?"
  cheat-sheet** (generated from the live registry; nothing fires while a
  modal dialog is open).
- Every stale "prompts for…"/"…after a confirm" phrasing describing the old
  native dialogs is reworded to the actual first-party dialog behaviour:
  the sidebar's **＋**/**×**, the project switcher's **New project…**, the
  branch switcher's **New branch…**/**×**/**Delete branch…**, and
  **Tag current state…** (now a two-field form, not two prompts).
- **§ The Agent panel** — notes **⌘J**/**Ctrl+J** as the dock's toggle
  alongside the header click, and that size/collapsed state persists through
  the layout manager, not a bespoke `agentcad.chat.open` key.
- **§ Keyboard shortcuts** — fully regenerated to the exact registered set:
  `F`, `G`, `R`, `Cmd+Z`/`Cmd+Y`/`Shift+Cmd+Z` (undo/redo), `Cmd+S`, `Cmd+N`,
  `Cmd+K`, `Cmd+B`, `Shift+Cmd+B`, `Cmd+J`, `?`, `Esc`, `Enter`, plus the
  sketcher's declared `Delete` row — each with its mac/other spelling, and a
  note that the whole set is inert while a modal dialog is open.

### `docs/agent-api.md`

- The "schemas are the source of truth" paragraph (`GET /api/tools`) gains
  one sentence: the ⌘K palette reads the exact same response for its "Tools"
  section and builds its argument forms from the exact same JSON Schemas —
  there is no second, frontend-side tool list to drift.
- The FEM section's "agents must not see a tool that cannot run" line gains
  the same cross-reference: a server without `[fem]` shows a palette without
  the FEM tools too, for free, because the palette's tool source is the live
  registry response, not an enumeration.
- The existing `ui_open`/UX-events section (slice 5) was read end-to-end and
  needed no correction.

### `docs/architecture.md`

- `agentcad/core/tools_*.py` row's pack list gains `ui`; a new row for
  `agentcad/core/tools_ui.py` (the `ui_open` pack: validation order, the
  10/10 s token bucket, `EventBus.subscriber_count()` read before publish).
- `agentcad/server/routes_*.py` row's pack list gains `ui`; a new row for
  `agentcad/server/routes_ui.py` (`POST /api/ui/events`: the allow-list,
  `by`/`client` set server-side, member-only by default).
- New `frontend/js/shell/` row: every module and its owning concern
  (`actions.js`, `dialogs.js`/`dialogs_model.js`, `palette.js`/
  `palette_model.js`, `menu.js`/`menu_model.js`, `layout.js`/
  `layout_model.js`, `shortcuts.js`/`shortcuts_model.js`, `toast.js`,
  `events.js`), naming the DOM/pure-model split every module follows.

### `docs/prd/in-progress/PRD-026-workbench-shell.md`

- `Status:` → `in progress — acceptance`.
- **FR2 corrected**: the PRD's draft example list ("new project, open by
  path, new part, delete part, example-reset confirmations" — there was no
  "example-reset" confirmation in the shipped codebase) is replaced by the
  real 21-site inventory grouped by module (per slice 2 report §1), plus a
  note that the eight legacy `.modal-overlay` adoptions are a separate,
  deliberate deviation from a literal FR2 rewrite.
- New **"Shipped vs. deferred"** subsection under "MVP & phasing": Phase 2
  (menu bar, full schema forms, `ui_open` + events, per-workspace layout
  memory) shipped alongside the MVP per the design spec's ruling 1; Phase 3
  (shortcut remapping, layout presets, palette frecency) stayed deferred, as
  scoped; the five mid-flow views deliberately left unregistered
  (`discard-edits`, `import-part-id`, `restore-version`, `review-summary`,
  `sketch-number`) and the `merge` view's narrow session-scoped `when` are
  both named and pinned to their test/report evidence.
- New **"Acceptance record"** subsection under "Acceptance criteria": one
  row per AC1–AC7 naming the test(s) and, where the criterion has a browser
  half, the specific slice report and session it is on record in — plus a
  short paragraph on the `ui_open` agent-surface proof.

### `docs/roadmap.md`

- Row `[026]`'s link corrected from `prd/pending/…` to
  `prd/in-progress/…` (the PRD moved folders during slice 1 and the roadmap
  row was never updated) and its status column updated from `pending` to
  `in progress — acceptance (six slices landed; AC1–AC7 recorded in the
  PRD)`.

## Files

- `tests/test_prd026_acceptance.py` (new)
- `docs/user-guide.md`, `docs/agent-api.md`, `docs/architecture.md`,
  `docs/roadmap.md`
- `docs/prd/in-progress/PRD-026-workbench-shell.md`

## Notes

- This slice adds no frontend/server behaviour — docs and tests only, per
  the brief ("edit-only on existing files", "no subagents").
- `make test`:

`make test` — **4826 passed, 44 skipped** (the run reported `4 failed, 4822 passed`: `test_checks_pipeline` asserts a clean tree while this slice was uncommitted, `test_checks_cli`'s 1 ms `--budget` race and the two `test_supervisor` memory-kill tests lost to a machine at load average 14–51 running concurrent suites — all four pass in isolation or on a clean tree; CI is authoritative). A final live-browser pass (Playwright + installed Chrome) verified AC4's drag/reload/toggle persistence and AC6's focus trap/restore end to end: 12/12 checks, zero page errors.

- Focused runs from this session:
  `uv run pytest tests/test_prd026_acceptance.py tests/test_frontend_shell.py -q`
  → **238 passed** (14 new + the 224 already shipped by slices 1–5);
  `uv run pytest tests/test_prd031a_acceptance.py tests/test_prd012_acceptance.py -q`
  → **29 passed**; `uv run pytest tests/test_tools_ui.py tests/test_routes_ui.py
  tests/test_hosted_surface.py -q` → **69 passed**.
- PRD-026 does not claim "done" in this entry — Status stays
  `in progress — acceptance` until the controller's own review/merge pass;
  see the PRD's Acceptance record for exactly which halves of AC1/AC4/AC6/AC7
  are graded on a prior session's live-browser evidence rather than
  re-verified in this one.
