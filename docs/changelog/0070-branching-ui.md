# 0070 — Branching UI: toolbar switcher, versions dialog, conflict list

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Claude (PRD-001 slice 4)

## Summary
Slice 4 of PRD-001: the human path over the slice 2/3 backend. The toolbar
gains a branch switcher next to the project switcher, a Versions dialog lists
and restores immutable versions (tags), and a merge modal drives the whole
merge flow — source/target picker, conflict list with pick-ours /
pick-theirs / pick-base / edit-by-hand, partial resolution, the post-merge
validation report, blocked-validation "Land anyway", and abort. Two new
WebSocket cases (`branch_changed`, `merge_completed`) keep the viewport, tree
and inspector following the authored state a branch switch or a landed merge
just changed. MVP scope per the design spec: a conflict **list**, not a
dual-viewport geometry compare.

## Changes
- **`frontend/index.html`**
  - A second static `.menu-wrap` (`#branch-btn` + `#branch-menu`) right after
    the project switcher. It must be static markup: `setupMenus()` snapshots
    `.menu-wrap` elements once at boot, so a dynamically inserted menu would
    get no outside-click, Escape or arrow-key handling. The button starts
    `hidden` and is revealed only when `GET /branches` answers (no git ⇒ no
    versioning routes ⇒ no switcher).
  - `#versions-modal` (`.modal.narrow`) and `#merge-modal` alongside
    `#drawing-modal`, following its `.modal-overlay > .modal > .modal-head`
    structure; `#drawing-title` gains the generalized `.modal-title` class.
- **`frontend/js/api.js`** — a `// ---- branches / versions / merge ----`
  section: `listBranches`, `createBranch`, `switchBranch`, `deleteBranch`,
  `listVersions`, `createVersion`, `mergeStatus`, `mergeBranch`,
  `resolveMerge`, `abortMerge`. Commented with the passthrough convention:
  these routes answer HTTP 200 with `{"error": …}` for `merge_conflict`, so
  callers check `res.error` **in addition to** catching `ApiError`.
- **`frontend/js/state.js`** — `branch`, `branches`, `clientId` (branch_list's
  `you`, used to tell our own `branch_changed` from another client's),
  `versions`, `merge`.
- **`frontend/js/main.js`**
  - `loadBranchState()` (on project load and on WS reconnect) fills the label
    and hides the switcher when the routes are absent; `setupBranchMenu()`
    rebuilds the menu on open like `setupProjectMenu()` — one item per branch
    (`.active` for the current, `(default)` suffix, `span.meta` = relative time
    of its head commit, subject as `title`), a `.menu-sep`, then "New branch…",
    "Merge into…", "Versions…".
  - `switchToBranch()` guards with `confirmDiscardEdits(null)`, POSTs the
    switch and runs `reloadBranchContext()` — the same context reset
    `loadProject()` does (`meshBuffers.clear()`, `viewport.clear()`,
    `clearFaceSelection()`, `lastFittedTarget = null`) plus a refetch and a
    re-select so the viewport, tree and inspector show the new branch's state.
  - `newBranchPrompt()` validates against the server's branch grammar
    (`^[a-z0-9][a-z0-9_/-]{0,63}$`) and switches after creating (the tool
    deliberately does not switch you).
  - WS: `branch_changed` relabels always, and resets the context only when
    `ev.client === state.clientId` and it is not the echo of our own switch
    (`branchSwitchUntil`, the same shape as `localPatchUntil`); another
    client's switch just marks the cached branch list stale.
    `merge_completed` toasts (fast-forward when `validation` is null, error
    when `validation.ok === false`) and refreshes the project.
  - `actions` gains `refreshProject`/`loadProject` for the new modules, and
    `setupKeys()` gains a `modalOpen()` guard so `f`/`g`/`r` do not act behind
    an open dialog.
- **`frontend/js/versions.js` (new)** — Versions dialog wired like
  `drawings.js` (close button, backdrop click, Escape). Lists tags newest-first
  (name, message, author · relative date · short commit) with **Restore**
  (`project_restore` with the tag name — the tool now accepts ref names) and
  "Tag current state…" (prompt + `^[a-z0-9][a-z0-9._/-]{0,63}$` validation +
  toast). Exports `relTime()`, shared with the branch menu.
- **`frontend/js/merge.js` (new)** — the merge modal.
  - Picker: source (theirs) / target (ours) selects, defaulting to
    "other branch → current".
  - Conflict view: left rail of conflicts, right pane either a **read-only
    CodeMirror** created from the global `window.CodeMirror` (`readOnly:
    "nocursor"`, `mode: "python"`, `theme: "agentcad"`) showing the diff3
    marked text — an independent instance, never `editor.js`'s singleton, and
    `cm.refresh()` runs after the host is visible — or a base/ours/theirs value
    table for manifest keys. Per conflict: **Use ours (target)**, **Use theirs
    (source)**, **Use base** when a base exists, and **Edit…** → **Save edit**
    (posts the buffer as `{"content": …}`).
  - Each pick POSTs `/merge/resolve` immediately, so partial resolution is real
    and the outstanding list re-renders from the server; the footer shows
    "N of M resolved" and **Complete merge** (enabled at zero outstanding —
    the re-entry path for a merge staged but blocked by validation).
  - Success renders the post-merge report (commit, two parents, conflicts
    resolved, rebuilt parts, failures, integrity, new interference pairs);
    a 422 `validation_error` renders the same report as **blocked** with a
    **Land anyway (allow_invalid)** action that retries without redoing the
    merge; **Abort merge** discards the staged merge.
  - `checkStaged()` runs at project load: `GET /merge` reopens the conflict
    view after a reload or a server restart.
- **`frontend/css/app.css`** — `branch-*`, `ver-*`, `conflict-*` classes and
  `.modal-foot`, built strictly from existing tokens so light mode keeps
  working; `.modal-head #drawing-title` generalized to `.modal-head
  .modal-title`; `.modal.narrow { width: min(560px, 100%) }`; `#toasts` raised
  from `z-index: 60` to `90` so merge toasts render above the modal (80).

## Files
- `frontend/index.html` — branch `.menu-wrap`, `#versions-modal`,
  `#merge-modal`, `.modal-title` on the drawing title
- `frontend/js/api.js` — branch/version/merge calls
- `frontend/js/state.js` — `branch`, `branches`, `clientId`, `versions`, `merge`
- `frontend/js/main.js` — branch menu, switch + context reset, two WS cases,
  `modalOpen()` key guard, `actions.refreshProject`/`loadProject`
- `frontend/js/versions.js` — new: versions dialog + `relTime()`
- `frontend/js/merge.js` — new: picker, conflict list, reports, abort
- `frontend/css/app.css` — new classes, `.modal.narrow`, toast z-index

## Notes
- **Browser verification (AC6).** Driven headless (Chrome via Playwright,
  software GL) against a scratch project on a scratch projects dir, with three
  client identities (`browser`, `agent_b`, `agent_c`) so branches really
  diverged: created a branch from the switcher and switched back and forth
  (the edit made on `feat` was invisible on `master` and the viewport/tree/
  inspector followed); tagged and restored a version; ran a clean three-way
  merge (two parents, `validation.ok`, the source's part rebuilt from the
  shared cache); ran a merge with one script conflict and one manifest-key
  conflict, resolved the key with "Use theirs" (partial, "1 of 2 resolved"
  re-rendered) and the script with "Use ours", which completed the merge;
  reloaded mid-merge and the conflict view reopened from `GET /merge`;
  resolved a conflict by hand-editing the CodeMirror buffer; aborted a staged
  merge; and blocked a merge on a broken part, then landed it with "Land
  anyway". Console was clean throughout (the only browser-logged line is
  Chrome's own "422 (Unprocessable Entity)" network notice for the blocked
  merge, which the UI handles and renders as the report).
- Light and dark themes were both screenshotted; the conflict view, versions
  dialog and branch menu use only existing tokens.
- The frontend has no JS test harness, so the logic lives in the page modules;
  `make test-fast` stayed green (416 passed, 1 skipped).
- Not in MVP (Phase 2 per the PRD): dual-viewport geometry compare, a
  pre-merge summary, and branch deletion from the UI — `api.deleteBranch` is
  wired but unused, and the DELETE route cannot carry a `/` in a branch name.
