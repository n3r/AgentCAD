# 0299 — PRD-026 slice 2: every native prompt/confirm is gone; the legacy modals adopt the shell

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (with Claude)

## Summary

PRD-026 FR2/FR3 (spec §1.4): the 21 remaining `prompt(`/`confirm(` call sites
across `main.js`, `versions.js`, `merge.js`, `proposals.js`, `market.js` and
`sketcher.js` became first-party dialogs, and all **eight** hand-rolled
`.modal-overlay`s adopted the shell's overlay stack through
`dialogs.attachLegacy`. `dialogs.isModalOpen()` no longer needs its
`.modal-overlay:not(.hidden)` DOM fallback, and `model.branches.delete` — which
slice 1 could not register — is a real action now that a branch picker exists.

## Changes

### The 21 call sites (site → dialog)

- `main.js` `confirmDiscardEdits` → `confirm({view: "discard-edits"})`. The
  guard is a **promise** now and all four call sites (`loadProject`,
  `selectPart`, `selectAssembly`, `switchToBranch`) `await` it.
- `main.js` `addPart` → `form({view: "new-part"})`: `id` (live
  `[a-z][a-z0-9_]{0,39}` validation), `label`, and a `material` **select**
  built from `state.materials` — but only when the catalog is already cached,
  because a free-text material box invites a typo `updatePart` then refuses.
  The label now reaches `POST /parts`; a chosen material is a second
  `updatePart` call whose failure says "created, but the material did not
  stick". New chord **`Mod+N`** (spec ruling 3).
- `main.js` `deletePart` → danger confirm naming the blast radius: "Deletes
  `<id>` and its script file." plus "Also removes N assembly instance(s): a, b,
  c" computed from `state.project.assembly.instances`.
- `main.js` import id → `prompt({view: "import-part-id"})`;
  `newProjectPrompt`/`openProjectPrompt` → `new-project` / `open-project`;
  `newBranchPrompt` → `new-branch` (BRANCH_RE); branch delete → the
  `delete-branch` danger confirm, plus a new `deleteBranchPrompt()` picker
  (a select of branches that are neither current nor default — the two the
  server always refuses).
- `versions.js` → one `new-version` form (name + message, where there were two
  prompts) and a `restore-version` danger confirm.
- `merge.js` → `merge-abort` danger confirm.
- `proposals.js` → `review-summary` prompt (textarea, ⌘/Ctrl+Enter submits) and
  one `edit-proposal` form replacing the title/description prompt pair.
- `market.js` → one `market-add-to-project` form with a project **select** (the
  list was already in hand, so free text was never a legitimate answer) and a
  validated `part_id`.
- `sketcher.js` → one `askNumber()` helper over
  `prompt({view: "sketch-number", type: "number"})` for distance, radius and
  slot width, and one two-field form for the ellipse's semi-axes (blank still
  means "leave it free"). `applyConstraint`/`slotClick` are async;
  the sketcher's own `onKey` is untouched — the dialog is modal, so its keys
  cannot fire while the dialog is up.
- Regexes are not re-spelled in dialog specs: `bare(ID_RE)` / `bare(BRANCH_RE)`
  / `TAG_RE.source` feed the field `pattern`, which
  `dialogs_model.validate` anchors as `^(?:…)$`.

### Legacy adoption (all eight overlays)

`drawing`, `versions`, `share`, `merge`, `proposals`, `library`, `configs`,
`notifications`: each module's own `document.addEventListener("keydown", …Esc…)`
is replaced by one `dialogs.attachLegacy(overlayEl, {view, title, isOpen,
onClose, open, when})`, with `notifyOpen()`/`notifyClose()` inside its open and
close functions. Markup unchanged; what they gain is the stack, one Esc owner,
the focus trap and focus restore, and a registry row.

### Shell changes

- `dialogs.isModalOpen()` — the `.modal-overlay:not(.hidden)` DOM fallback is
  **removed**. An open overlay with no stack entry is now a bug in its adopter,
  and a fallback that covers for it is how such a bug survives.
- `dialogs.closeModals()` — new. `comments.showThread()` used to hide the
  covering overlay by hand (`document.querySelector(".modal-overlay:not(.hidden)")`);
  with the overlay on the stack that would have stranded the entry
  (`isModalOpen()` true forever, every global shortcut dead). Non-modal panels
  are left alone.
- `attachLegacy` now forwards an adopter's `when` into the registry meta, so an
  adopted view's precondition reaches the palette's "Open: …" filter.
- `dialogs.prompt` accepts `rows` (the textarea prompt).
- `actions.context()` gains `hasOtherBranches`, so `model.branches.delete`'s
  eligibility reads `ctx` rather than module state (the m6 precedent).
- **The shortcut machine path is now total** — a bug the browser pass found,
  not a slice-2 regression. `fromEvent` builds a chord from whatever the
  browser calls the key; `Table.lookup` handed that straight back to
  `normalize`, an AUTHOR-facing parser that throws on shapes only an author
  could get wrong, from inside an untried document listener. Every key whose
  DOM name parses badly was an uncaught exception on every press of it:
  `" "` (the space bar) threw `empty shortcut chord`, `Super`/`Hyper` threw
  `shortcut chord is a bare modifier`. Three changes, structural first:
  new **`canonical(chord)`** (`normalize` without the throw) is what
  `Table.lookup` uses, so an unparseable chord is a **miss**; `keyName(" ")`
  → `"Space"` (which `NAMED`/`MAC_KEYS`/`PC_KEYS` already knew); and
  `fromEvent` ignores **every UI Events modifier-key value**
  (`Super`/`Hyper`/`Fn`/`CapsLock`/… ) rather than seven of them. `normalize`
  still throws — a binding an author wrote wrong must fail the build. Same
  family as the `+` keystroke fixed in 0296's review round 1, closed at the
  root this time.
- **The Tab focus trap yields to a text editor.** Adopting `#merge-modal` put
  the stack's trap over the conflict resolver's live CodeMirror — Python part
  scripts, where Tab indents and indentation is the block structure — and the
  listener is in the capture phase, so it took the keystroke before CodeMirror
  saw it. The trap now returns early when the event target is inside
  `.CodeMirror` or `[contenteditable]`. `input`/`textarea`/`select` stay
  trapped: they do not consume Tab, so for them the trap is the only thing
  keeping focus in the dialog.
- **A danger dialog opens on its safe button WITH fields, not just without.**
  `delete-branch` is danger with a `<select>`, so the old rule focused the
  field and left a submittable form under the Enter still travelling from the
  palette row that opened it — and its primary button deletes a working tree.
  The picker also opens **unchosen** (`— choose a branch —`, `value: ""`,
  `required`), so the primary is disabled until somebody deliberately picks.
  Two independent guards on the one irreversible dialog in the slice.
- **`frontend/js/patterns.js` (new)** — `ID_RE`, `BRANCH_RE`, `TAG_RE` and
  `bare()`, the client-side twins of `core/model.ID_RE`,
  `core/branches._BRANCH_RE` and `core/history._REF_RE`, in one module.
  `market.js` had hand-copied the part-id pattern into its dialog spec; it was
  correct, and it was exactly the drift this slice claimed to have avoided.
- `dialogs_model.validate` treats a **non-numeric `step`** as no step.
  `step: "any"` is the HTML spelling of "free decimal" (a millimetre field
  wants it, or the browser's spinner snaps to whole numbers) and it used to
  reach the multiple-of arithmetic and pass by accident — `(num - base) /
  "any"` is `NaN` and `NaN > 1e-9` is false. Right answer, no reason.
- `closeModals()` returns `before - after` measured on the stack, so it cannot
  report closing something that stayed open.

### Actions and views

- New action `model.branches.delete` (`menu: "model/32"`, `danger`,
  `when: branch && hasOtherBranches`); `model.proposals` moved 32 → 33.
- Registered views: `new-part`, `delete-part`, `new-project`, `open-project`,
  `new-branch`, `delete-branch`, `new-version`, `new-proposal`,
  `edit-proposal`, `market-add-to-project`, `merge-abort`, plus the eight
  adopted modals. `agentOpenable: false` on the destructive ones
  (`delete-part`, `delete-branch`, `merge-abort`).
- **Not** registered: `discard-edits`, `import-part-id`, `restore-version`,
  `review-summary`, `sketch-number` — each names context nobody can pass from a
  palette row (a navigation you are not making, a file already uploaded, a
  version, a verdict, an entity you are mid-way through drawing). They are
  dialogs with a `view:` and no registry row, on the registry's own rule that a
  row with nothing behind it is a menu that lies.

## Files

- `frontend/js/main.js` — the seven sites above, `materialField()`,
  `deleteBranchPrompt()`/`runDeleteBranch()`, `bare()`, the
  `model.branches.delete` action, six `dialogs.register` calls.
- `frontend/js/versions.js`, `merge.js`, `proposals.js`, `market.js`,
  `sketcher.js` — their call sites plus (bar `market`/`sketcher`) adoption.
- `frontend/js/library.js`, `configs.js`, `drawings.js`, `comments.js`,
  `share-links.js` — adoption only.
- `frontend/js/shell/dialogs.js` — `isModalOpen` fallback removed,
  `closeModals()` added, `attachLegacy` forwards `when`, `prompt` takes `rows`.
- `frontend/js/shell/actions.js` — `hasOtherBranches` in `context()`.
- `frontend/index.html` — the "PRD-026 … has not landed" comment replaced by
  what actually landed. `frontend/css/app.css` — the z-index note says eight.
- `frontend/js/shell/shortcuts_model.js` — `canonical()`, `MODIFIER_KEYS`,
  the space-bar `keyName` mapping; `Table.lookup` no longer re-parses.
- `frontend/js/shell/dialogs_model.js` — the non-numeric `step` guard.
- `frontend/js/patterns.js` (new) — the three identifier rules and `bare()`.
- `tests/test_frontend_shell.py` — +35 tests (the AC1 grep, the view/registry
  tables, five behavioural node tests against the DOM stub).

## Notes

- **Behaviour change worth naming:** `share-links.js`'s overlay never had an
  Escape of its own. It has one now, because adoption gives it one.
- `comments.js`'s composer popover (`#comment-pop`) keeps its element-scoped
  Escape: it is an anchored popover, not a `.modal-overlay`, and it is not on
  the stack.
- `Mod+N` is `when`-gated, not `enabled`-gated: with no project open there is
  nothing to create, so the chord declines and the browser keeps ⌘N. That is
  the opposite of the `Mod+S` ruling (0296 I2) and deliberately so — v0.1 never
  bound ⌘N, so there is no prior suppression to preserve.
- `make test` — **4812 passed, 44 skipped** (the run reported `2 failed,
  4810 passed`: `test_checks_pipeline` asserts a clean tree while this slice
  was uncommitted, and `test_checks_cli`'s 1 ms `--budget` race lost again on
  a loaded machine — both pre-existing, tracked in the PR notes; CI is
  authoritative). The focused runs all passed:
  `tests/test_frontend_shell.py` → 224 passed, and the frontend/server subset
  (`test_frontend_*`, `test_server`, `test_hosted_surface`, `test_packages_api`,
  `test_prd0{08,09,13}_acceptance`) → 369 passed.
- **Verified in a real browser** (`agentcad serve --port 8631` driven by
  Playwright against the installed Chrome, SwiftShader for WebGL): the new-part
  form (`role=dialog`, `aria-modal`, `aria-labelledby` resolving, focus on the
  first field, live pattern error, primary disabled until valid, Esc closes);
  the delete-part confirm rendering "Also removes 1 assembly instance:
  gusset_1" and **opening on Cancel**; the delete-branch picker offering only
  `dasd` (the current and default branches excluded); the `new-version` form
  opening ON TOP of the adopted versions overlay, where the first Esc closes
  the form and leaves versions up and the second closes versions — the
  stack, working; the library overlay closing on Esc with its own listener
  gone; the palette's "Open: …" rows listing exactly the registered views its
  `when` predicates allow; both themes.
- `docs/user-guide.md`'s shortcut table still predates slices 3/4 and now
  slice 2's `⌘N` too — the docs slice regenerates it from `shortcuts.list()`.
