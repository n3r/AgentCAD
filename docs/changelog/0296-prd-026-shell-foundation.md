# 0296 — PRD-026 slice 1: the shell foundation — action registry, shortcuts, dialogs, toasts

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (with Claude)

## Summary

The first frontend slice of the workbench shell revamp (PRD-026, spec
`docs/superpowers/specs/2026-08-19-workbench-shell-design.md` §0–§2, §6, §8):
a new `frontend/js/shell/` layer of six vanilla ES modules — the single action
registry every later surface (palette, menus, shortcuts) reads, a shortcut
table with conflict detection and one `keydown` listener, a first-party dialog
primitive (modal + non-modal, focus trap, overlay stack, view registry for
`ui_open`), and toasts — with `main.js` losing its v0.1 primitives
(`setupKeys`, `modalOpen`, `toast`, `setupClaimDialog`). Every module is
node-importable and the pure halves are unit-tested from pytest.

## Changes

- **`shell/actions.js`** — `register({id, title, run, when?, enabled?,
  shortcut?, menu?, group?, keywords?, danger?})` (duplicate id throws;
  registration is atomic — a chord conflict raised by a listener unregisters
  the entry), `get`, `list(ctx)` (only `when`-true entries, graded `enabled`),
  `run(id, ctx, {source})`, `context()` (from `state`, `modalOpen` delegates
  to `dialogs.isModalOpen()`, `hasInstances` for the export rows), `onChange`,
  `onRun` (slice 3's telemetry seam). `when` decides presence, `enabled`
  decides actionability — so a menu row greys instead of vanishing.
- **`shell/shortcuts_model.js` (pure)** — `normalize` (Mod/Ctrl/Alt/Shift
  order, a literal `+` key survives the split), `fromEvent` (mac `Mod` = ⌘,
  else Ctrl; Ctrl-on-mac stays `Ctrl+`; `?` from Shift+/; `AltGraph`/bare
  modifiers → `null`; never throws), `label` (`⌘K`/`⇧⌘B` vs `Ctrl+Shift+B`),
  `Table` (nested `Map<scope, Map<chord, row>>`; `bind` throws
  `ShortcutConflictError` naming both ids — always, not only in dev).
- **`shell/shortcuts.js`** — one document `keydown` listener (bubble phase):
  behind an open modal only `modal-safe` bindings fire (none yet); in a text
  field only modifier chords fire; `Mod+S` defers to CodeMirror when the
  editor is focused; `Mod+Z` declines inside a field (native text undo); a
  `when`-false binding leaves the keystroke to the browser, an `enabled`-false
  one swallows it (so ⌘S with no part no longer opens "Save page as…").
  Migrated chords: `F`, `G`, `R`, `Mod+Z`, `Mod+Y`, `Mod+Shift+Z`, `Mod+S`,
  `?` (the cheat-sheet). `declare()` records the sketcher's documented-only
  `Escape`/`Delete` rows. `Mod+K` is deliberately **not** bound (slice 3).
- **`shell/dialogs_model.js` (pure)** — `markup(spec) → {html, ids}`: the
  dialog DOM as a string (`role="dialog" aria-modal="true" aria-labelledby`,
  `<label for>` per field, `aria-invalid`/`aria-describedby` on errors, text
  on every button, `nonmodal` class set from JS — no `:has()`), everything
  interpolated through `escapeHtml`, `width` whitelisted, `rows` coerced;
  `validate` (required/pattern/min/max/step/json/custom — a **string**
  `pattern` is anchored `^(?:…)$`, a `RegExp` chooses its own); `focusables`.
- **`shell/dialogs.js`** — `open(spec) → Promise<{ok, values, button}>`,
  `confirm`/`prompt`/`form` sugar, the overlay stack with one capture-phase
  Esc listener whose owner is the topmost **modal** (or a non-modal holding
  focus — `escOwner`, pure), focus trap + restore, backdrop cancel, the view
  registry (`register`/`views`/`openView(view, args, {by})` with the "opened
  by agent" attribution chip), `attachLegacy(overlayEl, …)` for the
  hand-rolled modals — nine of them once slice 2 has adopted them all
  (drawing, versions, share, merge, proposals, library, configs,
  notifications, and `materials`, which arrived with PRD-028 in the
  `origin/main` merge); its Esc path always `notifyClose()`s, so an adopter can
  never strand the stack. Plus `setEmitter` (no-op until slice 3).
  `isModalOpen()` also answers true for a `.modal-overlay:not(.hidden)` until
  slice 2 adopts the legacy modals.
- **`shell/toast.js`** — `toast(message, kind, {id, timeout, action})`,
  `dismiss`; promoted out of `main.js` (`panelApi.toast` still points at it).
- **`main.js`** — the panel DI object is `panelApi` (definition + `init`
  calls only); `registerActions()` declares every existing toolbar verb as an
  action with `menu:`/`group:`/`shortcut:` (`project.*`, `part.*`, `edit.*`,
  `view.*`, `model.*`, `help.shortcuts`); toolbar buttons call `actions.run`;
  the claim dialog moved onto `dialogs.open({view: "claim"})` with the same
  callers' contract (`#claim-modal` removed from `index.html`); the `?` sheet
  is generated from `shortcuts.list()`. Not registered on purpose:
  `model.branches.delete` (needs slice 2's picker), `project.switch` (slice
  3), `view.*.toggle` (slice 4).
- **`index.html`** — `#dialog-host` before `#toasts`. **`app.css`** — the
  `.dlg-*` block (z-index 90), toasts to 100, `.toast-action`, reduced-motion
  covers `.dlg-overlay`, the duplicate `.toast` rule merged.
- **Behaviour change worth knowing:** behind a legacy modal the modifier
  chords (`Mod+S`/`Mod+Z`/`Mod+Y`/`Mod+Shift+Z`) are now suppressed too —
  v0.1's `modalOpen()` guarded only the bare keys.
- `tests/test_packages_api.py` — one assertion followed the `panelApi` rename.

## Files

- `frontend/js/shell/{actions,shortcuts_model,shortcuts,dialogs_model,dialogs,toast}.js` — new
- `frontend/js/main.js`, `frontend/index.html`, `frontend/css/app.css` — as above
- `tests/test_frontend_shell.py` — new (90 node-in-pytest tests)
- `tests/test_packages_api.py` — one assertion

## Notes

Reviewed (Opus task review → Needs fixes; fix round 1 → scoped re-review):
the review caught `fromEvent` throwing on any `+` keystroke (the first
statement of the document listener — typing `+` in the editor would have
raised every time), raw NUL bytes that made `shortcuts_model.js` binary to
git, the ⌘S `preventDefault` regression, Esc closing an unfocused non-modal,
`attachLegacy`'s Esc path able to strand the stack, and the unanchored
`pattern` — all fixed with tests. Deferred minors (recorded in the SDD
ledger): `model.drawing` is offered for reference parts the server refuses;
`pendingAttribution` is a non-re-entrant slot; a registered view can be
opened twice.

**Not verified in a browser** — no Chrome extension was reachable from the
build session; the DOM-behavioural half (trap cycling, focus restore, backdrop,
both themes) is evidence-graded on the unit-tested `focusables`/`validate`/
`markup` logic and the dispatch tests that run the shipped `onKeyDown` in
node. The controller's browser pass is owed before the PR merges.

`make test` — **4676 passed, 44 skipped** on the combined slice 1 + slice 5 tree (the run reported `11 failed, 4667 passed`: nine `*_count_is_cited` guards read the newest changelog before its count was filled, `test_checks_pipeline` asserts a clean tree while the slices were uncommitted, and `test_checks_cli::test_blown_budget_exits_two_with_a_partial_report_on_disk` — a 1 ms `--budget` whose deadline must not pass before stage 1 starts — lost the race on a machine at load average 18–41 from two other suites; it is re-run in isolation before the PR and CI is authoritative).
