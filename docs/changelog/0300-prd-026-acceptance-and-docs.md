# 0300 — PRD-026 slice 6: acceptance tests, docs and the PRD's own record

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

`make test` on the **merged tree** (this branch after `origin/main` = PRD-024 + PRD-028) — **5286 passed, 48 skipped** (the run reported `3 failed, 5283 passed`: all three were environmental — stray gitignored `.history`/`.cache` dirs inside `examples/*` left by a prior session gave the copied example a git head (sha+dirty) and their git calls consumed the 1 ms `--budget` before stage 1; after removing the derived dirs both checks tests pass, and `test_sketch_drag`'s timing assertion passed on re-run; the final fix-wave count is below). The pre-merge run this entry originally cited (4826 passed, 44 skipped) described a tree that no longer exists — the merged-tree count is the one AC7's evidence rests on, following the PRD-014 precedent (`f8638bc`, "note the merged-tree count in 0267"). A final live-browser pass (Playwright + installed Chrome) verified AC4's drag/reload/toggle persistence and AC6's focus trap/restore end to end: 12/12 checks, zero page errors.

`make test` after the final fix wave — **5303 passed, 48 skipped** (fully green, no exclusions).

- **Merge note** (`90f17ef`, `origin/main` → this branch; the merge commit
  itself carried no changelog entry, and this paragraph is it). The merge
  brought in PRD-024 and PRD-028 and changed behaviour in three ways worth
  recording: `frontend/js/materials.js` **adopted the dialog stack**
  (`dialogs.attachLegacy` with `view: "materials"`, its own document `keydown`
  Escape listener deleted), so PRD-028's materials browser is the **ninth**
  `.modal-overlay` under the shell's contract — one stack, one Esc owner, one
  focus trap — and `materials.init(actions)` became `materials.init(panelApi)`
  for the PRD-026 DI rename; this branch's changelog entries were **renumbered
  0270-0275 → 0295-0300** to clear the collision with `origin/main`'s
  PRD-024/028 entries (0270-0294), and no reference to the old numbers survives
  in tracked files; and `.gitignore` gained `.superpowers/`, which keeps the SDD
  scratch tree from tripping `test_checks_pipeline`'s clean-tree assertion.
  This entry also covers commit **`4a8bc7c`** (the design spec, the slice plan
  and the `git mv` of the PRD), which predates the 0295-0300 series and shipped
  without an entry of its own — docs-only, and the spec is itself the artifact,
  but CLAUDE.md's rule is unqualified, so it is accounted for here.

- Focused runs from this session:
  `uv run pytest tests/test_prd026_acceptance.py tests/test_frontend_shell.py -q`
  → **238 passed** (14 new + the 224 already shipped by slices 1–5); the final
  fix wave took the same two files to **255 passed** (17 more tests: the
  sketcher's modal guard, the overlay-adoption closure, `openView`'s `when`,
  the legacy attribution chip (and a regression test that the shell dialog's
  own chip survived the `pendingAttribution` narrowing), the Tab trap's owner
  and four hostile-input interpolation probes);
  `uv run pytest tests/test_prd031a_acceptance.py tests/test_prd012_acceptance.py -q`
  → **29 passed**; `uv run pytest tests/test_tools_ui.py tests/test_routes_ui.py
  tests/test_hosted_surface.py -q` → **69 passed**.
- **Final fix wave** (the whole-branch review's five Importants, its six
  Minors and the second verifier's D1/D2), all in this branch's own files:
  - `frontend/js/sketcher.js` — `onKey` returns early on
    `dialogs.isModalOpen()`. Delete/Backspace propagate freely (only Escape is
    swallowed by the stack), and a dialog `<button>` is not an `input`, so
    Backspace aimed at Cancel ran `deleteSelection()` and destroyed the sketch
    selection behind the dialog. The `askNumber` docstring that claimed this
    was already impossible now says which line makes it true.
  - `frontend/js/shell/dialogs.js` — `setContext(fn)` (injected from `boot()`
    as `actions.context`; importing `actions.js` would be a cycle) and
    `openView` refusing `{ok: false, reason: "not_available"}` when the view's
    own `when` is false: `ui_open {view: "merge"}` on a clean branch used to
    run `openPicker()`, which *starts* a merge. `attachLegacy.notifyOpen()`
    now stamps `data-agent-opened` and an "opened by agent" chip into the
    overlay's `.modal-head`, so the attribution this file, the user guide and
    `agent-api.md` all promise exists for the nine adopted legacy views too;
    `pendingAttribution` is cleared as soon as the opener yields (it used to
    stay live across `materials.open()`'s catalog fetch, stamping whatever
    unrelated dialog opened next), and the Tab trap belongs to the topmost
    **modal** (`trapOwner`, mirroring `escOwner`) rather than to `top()`.
  - `frontend/js/shell/dialogs_model.js` / `menu_model.js` — the four
    interpolations the second verifier found raw: a button's `id` and `uid`
    through a new `idToken` (an escaped id is no longer an id), a button's
    `kind` through a three-value whitelist, the menu name through
    `escapeHtml`. Latent (every value in the tree is a source literal), but
    this is the primitive other PRDs compose and the docstring claimed it
    already.
  - `main.js` + `frontend/index.html` — the dead `#claim-modal` markup is
    deleted (slice 2 replaced it with `dialogs.open()`); `model.materials`
    (`menu: "model/22"`, `when: hasProject`) puts PRD-028's browser on the
    menu bar and in the palette's *action* section, with `#materials-btn`
    routed through `actions.run` like every other toolbar button
    (`panelApi.openMaterials` and the `#materials` deep link untouched); and
    both fire-and-forget `openView` sites (`case "ui_open"`, `palette.js`'s
    view row) now `.catch` into a toast.
  - `palette_model.entriesFromViews(views, shownActionIds)` + an `actionId` on
    the six adopted modals that have an action row — one palette row per verb
    instead of an action row and a near-identically titled "Open: …" row.
- PRD-026 does not claim "done" in this entry — Status stays
  `in progress — acceptance` until the controller's own review/merge pass;
  see the PRD's Acceptance record for exactly which halves of AC1/AC4/AC6/AC7
  are graded on a prior session's live-browser evidence rather than
  re-verified in this one.
