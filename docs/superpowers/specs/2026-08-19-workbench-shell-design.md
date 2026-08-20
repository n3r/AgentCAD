# PRD-026 Workbench shell — design spec (dialogs, palette, menus, panels, shortcuts)

Grounded in a survey of the frontend as it stands on `main` (commit `3b9e8a4`):
`frontend/js/main.js` (2391 LOC — `setupKeys`, `setupMenus`, `setupClaimDialog`,
the `actions` DI object, `toast()`), `index.html` (seven hand-rolled
`.modal-overlay` modals + `#comment-pop` + `#toasts`), `css/app.css` (the
`--ink/--panel/--accent…` token system, `.menu-*`, `.modal-*`, `.toast`),
`api.js` (`callTool`, no `listTools`), `server/app.py:391,404` (`GET /api/tools`,
`POST /api/tools/{name}`), `core/service.py:69` (`EventBus`), the `tools_*.py` /
`routes_*.py` extension points, and the node-in-pytest testing pattern
(`tests/test_frontend_tree.py`). This spec records the decisions, the rejected
alternatives, and the rulings the orchestrator made where the PRD left room; the
slice plan is the sibling `docs/superpowers/plans/2026-08-19-workbench-shell.md`.

## Scope (what this PRD builds now)

The PRD's **MVP plus the Phase-2 items that cost little once the action
registry exists**:

- **Build now:** FR1 dialog primitives (modal + non-modal), FR2 every native
  `prompt/confirm` call site replaced (21 sites, five more modules than the PRD
  lists — see §2), FR3 dialog registry keyed by view name, FR4 palette
  (actions + registry tools + navigation targets, fuzzy, recent-first), FR5
  schema-generated argument forms (scalar/enum/bool + a JSON field for
  object/array args), FR6 capability presence, FR7 menu bar, FR8 resizable/
  collapsible panels with per-workspace persistence, FR9 shortcut registry +
  "?" overlay, FR10 reduced-motion + a static a11y pass, the `ui_open` tool
  and the three UX events.
- **Defer (Phase 3, recorded in the PRD):** user shortcut remapping, layout
  presets, palette frecency learning beyond recent-first. Also deferred:
  **migrating the seven hand-rolled modals' markup onto the new primitive** —
  they *adopt* the shell (overlay stack, one Esc listener, focus trap, dialog
  registry) without rewriting their DOM (§1.4). A full rewrite is churn with no
  user-visible gain and would collide with PRD-025's workspace work.

Non-goals unchanged: no framework/bundler (vanilla ES modules, no
`package.json`), no workspace tabs (PRD-025), no tree redesign (PRD-027), no
native OS menus in the packaged app.

## 0. Module layout and the pure/DOM split (Decision 0)

New layer `frontend/js/shell/`, each DOM module paired with a **pure model
module** that runs in node (the house `tree_model.js`/`tree.js` split — it is
what makes the shell unit-testable without a DOM library):

| DOM module | Pure model (node-testable) | Owns |
|---|---|---|
| `shell/actions.js` | — (pure itself) | the single action registry |
| `shell/shortcuts.js` | `shell/shortcuts_model.js` | chord normalisation, conflict detection, dispatch table |
| `shell/dialogs.js` | `shell/dialogs_model.js` | dialog markup generation (the a11y-checkable string), validation helpers |
| `shell/palette.js` | `shell/palette_model.js` | fuzzy scoring/ranking, recent-first ordering, JSON-Schema → form-field derivation, result routing decision |
| `shell/menu.js` | `shell/menu_model.js` | action → menu tree grouping/ordering, shortcut labels |
| `shell/layout.js` | `shell/layout_model.js` | clamps, collapse semantics, persistence keys/serialisation |
| `shell/toast.js` | — | `toast()` promoted out of `main.js` (id, dismiss, optional action button) |
| `shell/events.js` | — | browser→server UX telemetry (`POST /api/ui/events`) |

Rules: no module reaches into another's DOM; `main.js` wires them in `boot()`;
every module is importable in node (no top-level `document` access — DOM work
happens in `init()`/functions). `frontend/js/main.js` shrinks (keys, menus,
toast, claim dialog move out). The existing panel DI object (`const actions =
{selectPart, …}` at `main.js:45`) is **renamed `panelApi`** in `main.js` only
where it is defined and passed — callers keep receiving the same object, so no
panel module changes for the rename. The name `actions` belongs to the registry.

## 1. Dialog system (Decision 1 — FR1, FR2, FR3)

### 1.1 Primitive

`dialogs.open(spec) → Promise<{ok: boolean, values: object}>`:

```js
{
  view: "new-part",              // registry key; also emitted in events
  title, body?,                  // body: text or a DOM node
  fields?: [{name, label, type: "text"|"number"|"select"|"checkbox"|"textarea"|"json",
             value?, placeholder?, pattern?, required?, options?, help?, validate?(v, all) → string|null}],
  buttons?: [{id, label, kind: "primary"|"danger"|"default", submits?: bool}],   // default [Cancel, OK]
  danger?: bool,                 // danger variant styles the primary button + title
  modal?: true,                  // false → non-modal panel (no backdrop, no focus trap, stays until closed)
  width?: "narrow"|"default"|"wide",
}
```

Sugar over it: `dialogs.confirm({title, body, danger?, confirmLabel?}) →
Promise<boolean>`, `dialogs.prompt({title, label, value?, pattern?, validate?,
type?}) → Promise<string|null>`, `dialogs.form(spec) → Promise<object|null>`.
The promise shape is `setupClaimDialog`/`askOverride` (`main.js:2224`)
generalised — that is the one dialog in the codebase that already does it right.

Behaviour (the accessibility contract, FR1/FR10): `role="dialog"
aria-modal="true" aria-labelledby=<title id>` (`aria-describedby` when `body`
is text), a **focus trap** (Tab/Shift+Tab cycle inside; first field or primary
button focused on open), **focus restored** to the opener on close, `Esc` =
cancel, `Enter` in a single-line field = submit (textarea/json: `Mod+Enter`),
backdrop click = cancel (modal only), live validation (`aria-invalid`,
`aria-describedby` error text, primary button disabled while invalid), theme
tokens only, `prefers-reduced-motion` disables the open transition. Error text
is per-field; a submit-time failure (the API refused) is shown in the dialog
via `dialog.setError(msg)` rather than closing and toasting.

### 1.2 Overlay stack

One module-level stack of open overlays (new dialogs **and** the adopted
legacy modals). A single document-level `keydown` listener handles `Esc` for
the **topmost** entry only. `shortcuts.js` reads `dialogs.isModalOpen()` for
its `modalOpen` context (replacing `main.js:1520 modalOpen()`'s DOM query).
Nested dialogs (a confirm on top of a form) work: the inner traps focus, the
outer resumes when it closes.

### 1.3 Registry (FR3, the `ui_open` target)

`dialogs.register(view, opener: (args) → Promise|void, {title, description,
agentOpenable: true})`; `dialogs.openView(view, args, {by: "user"|"agent"})`.
Each built-in dialog (`new-project`, `open-project`, `new-part`, `delete-part`,
`import-part-id`, `new-branch`, `delete-branch`, `new-version`,
`restore-version`, `review-summary`, `new-proposal`, `market-add-to-project`,
`sketch-number`, `shortcuts` (the "?" overlay), `palette`) and each adopted
legacy modal (`drawing`, `versions`, `share`, `merge`, `proposals`, `library`,
`configs`, `notifications`, `claim`) registers here; the palette lists the
registered, `when`-eligible views as "Open: …" entries, and `ui_open` resolves
only through this registry (an unknown view → toast "Agent asked to open
‹x›, which this shell does not have" + a `dialog_opened` event is **not**
emitted). `by: "agent"` shows a persistent attribution chip in the dialog head
("opened by agent") — PRD "always visible attribution".

### 1.4 Replacing the 21 native call sites (FR2)

Every `prompt(`/`confirm(` in `frontend/js` becomes a `dialogs.*` call; the
exact mapping is in the plan. Notable shapes:

- **New part** (`main.js:487`): form dialog — `id` (live `[a-z][a-z0-9_]{0,39}`
  validation, pre-filled suggestion), `label`, `material` (select from
  `GET /api/materials` if cached, else text), `template` (select: blank /
  the toolkit templates already offered by `addPart`'s code path, if any —
  otherwise omit the field rather than fake it). Reachable from the sidebar
  "+", File menu, palette.
- **Delete part** (`main.js:505`): danger confirm naming the blast radius —
  "also removes N assembly instance(s)" computed from `state.project.assembly`
  (instances whose `part` is the id), plus the script-file note.
- **Sketcher numeric prompts** (5 sites): one `sketch-number` dialog
  (`dialogs.prompt({type:"number"})`) — the sketcher's own key handling stays
  untouched; the dialog is modal so sketcher keys do not fire while it is up.
- **Market "which project"** (`market.js:553`): a select of known projects,
  not a free-text prompt.
- **Proposal title+description** (`proposals.js:1070`): one form dialog with
  two fields instead of two prompts.
- `confirmDiscardEdits` (`main.js:169`) is async today? It is called from
  part-switch guards; it becomes `await dialogs.confirm(...)` and its callers
  are made async (the tree click handler awaits it).

The seven legacy modals **adopt** the shell: each module replaces its own
`document.addEventListener("keydown", Esc…)` with `dialogs.attachLegacy(
overlayEl, {view, onClose, isOpen})`, which pushes/pops the overlay stack on
open/close, applies the focus trap + restore, and registers the view. Their
markup and open/close functions are otherwise unchanged. `main.js:1520
modalOpen()` is deleted. The `index.html:355-358` comment promising this is
removed with the change.

AC1 becomes a test: `tests/test_prd026_acceptance.py` greps `frontend/js`
(including `shell/`) for `\b(window\.)?(prompt|confirm|alert)\s*\(` → empty.

Rejected: `<dialog>` element with `showModal()`. It gives a free focus trap
and Esc, but styling the backdrop (`::backdrop`) against the token system is
uneven across the three browsers the hosted mode targets, `showModal` throws
when already open (our nested/legacy adoption needs a stack we control), and
the a11y static pass needs the same explicit roles anyway. A div-based dialog
with an explicit trap is ~80 lines and fully under our contract.

## 2. Action registry (Decision 2 — FR7's "single action registry")

`shell/actions.js`:

```js
actions.register({
  id: "part.new",            // dotted, namespaced by area
  title: "New part…",        // ellipsis = opens a dialog
  description?: "Create a part in the current project",
  run: (ctx) => Promise|void,
  when?: (ctx) => bool,      // eligibility; absent = always
  shortcut?: "Mod+N",        // declared HERE; shortcuts.js registers it
  menu?: "file/30",          // "<menu>/<order>" — absent = palette/shortcut only
  group?: "Parts",           // palette section label
  keywords?: ["create", "add"],
  danger?: bool,
})
actions.list(ctx) → [{id,title,…, enabled}]   // only `when`-true entries
actions.run(id, ctx?) → Promise                // emits palette_executed when invoked from the palette
```

`ctx` is computed once per query from `state` (`projectName`, `selectedPart`,
`mode`, `branch`, `health`, `chatAvailable`, `inField`, `modalOpen`, `sketcherOpen`).
A duplicate `id` throws at registration (same rule as `ToolRegistry`).
Registry tools are **not** copied into `actions` — the palette merges two
sources at query time (§3) so a tool's presence is always the live registry's,
never a stale copy (FR6). Menus and shortcuts read only `actions`.

The initial action set (all existing toolbar verbs + the dialog openers):
`project.new`, `project.open-path`, `project.switch` (per project, dynamic),
`project.import-cad`, `project.export.*` (stl/step/3mf… the existing export menu
rows), `project.share`, `part.new`, `part.delete`, `part.save-script` (`Mod+S`),
`edit.undo` (`Mod+Z`), `edit.redo` (`Mod+Y`/`Shift+Mod+Z`), `view.fit` (`F`),
`view.gizmo.translate` (`G`), `view.gizmo.rotate` (`R`), `view.theme.toggle`,
`view.sidebar.toggle` (`Mod+B`), `view.inspector.toggle` (`Shift+Mod+B`),
`view.chat.toggle` (`Mod+J`), `view.repmode.*`, `model.sketch`,
`model.library`, `model.market`, `model.versions`, `model.branches.new`,
`model.branches.delete`, `model.proposals`, `model.configs`,
`model.drawing`, `help.shortcuts` (`?`), `help.palette` (`Mod+K`),
`help.docs`. Exact ids are fixed in the plan; the rule is "every toolbar
button is an action and the button calls `actions.run`".

## 3. Command palette (Decision 3 — FR4, FR5, FR6)

`Mod+K` (and a toolbar "⌘K" affordance) opens a modal dialog (`view:
"palette"`) with one input and a listbox (`role="listbox"`, `aria-activedescendant`,
`↑/↓/Enter/Esc`, `PageUp/Down`). Sources merged per keystroke:

1. **UI actions** — `actions.list(ctx)`.
2. **Registry tools** — `api.listTools()` (`GET /api/tools`, fetched once on
   first open, refreshed on WS reconnect and on `health` change), each as
   `{id: "tool:"+name, title: name, description}`; shown under a "Tools"
   section with a monospace name. FR6: the list *is* the registry — a tool a
   pack did not register is simply absent; nothing frontend-side enumerates
   tools.
3. **Navigation targets** — projects (`open project: …`) and the current
   project's parts (`select part: …`), from `state`.

Ranking (`palette_model.rank(query, entries, recents)`): a subsequence fuzzy
scorer (contiguous runs + word-start bonuses + shorter-title bonus, score 0 =
filtered out), ties broken recent-first (`localStorage["agentcad.palette.recent"]`,
last 20 ids), then by section order actions › navigation › tools. Empty
query = recents, then the first N of each section.

**Running a tool** (`palette_model.formFields(schema, ctx)`): required args
come first, optional after a divider, `project`/`part_id`/`instance_id` are
**prefilled** from `ctx` (still editable); field types from JSON Schema
`type`: `string` → text (`enum` → select), `number`/`integer` → number (step
1 for integer), `boolean` → checkbox, `object`/`array` → a `json` field
(validated `JSON.parse`, error shown inline) — so every tool is runnable with
parity, and the PRD's "complex tools open their dedicated dialog" becomes a
**registry flag the action registry can set**: `actions.register({id:
"tool:set_assembly", …})` overrides the generic form for that tool when an
action with that id exists (none do at MVP; the seam is tested). A tool with
**no required args** runs immediately (optional args are still reachable: the
entry has a secondary "with options…" affordance via `Shift+Enter`). Execution
goes through `api.callTool(name, body)` — the same `POST /api/tools/{name}`
agents use.

**Result routing** (`palette_model.routeResult(name, result)`): an `{error}`
payload → the dialog's error line (the tool refusal is a 200 — never a toast
that disappears); a result whose JSON is ≤ 120 chars or has ≤ 3 scalar keys →
toast (success kind); otherwise → the **result panel** (a non-modal dialog
`view: "tool-result"` with pretty JSON, a copy button, and the tool name);
geometry-changing tools need nothing extra — the server already publishes
`project_changed` and the viewport refreshes. The browser then posts
`palette_executed {action}` (§6).

Rejected: copying tools into `actions` at boot (would drift when a pack loads
late or the service restarts); a server-side palette endpoint (the registry is
already the endpoint).

## 4. Menu bar (Decision 4 — FR7)

A `<nav id="menubar" role="menubar">` row inserted **inside the existing
`#toolbar`** (left of the brand's sibling controls; the toolbar's one-off
buttons stay — they are the quick-access row, the menus are the map). Menus:
**File · Edit · View · Model · Help**; rows are generated from
`actions.list()` filtered by `menu` (`menu_model.tree(actions) →
[{menu, items:[{id,title,shortcutLabel,danger,enabled}]}]`), separators
between tens (`file/10`, `file/20`… — a gap of ≥10 in `order` draws a
separator), every row shows its shortcut label (`⌘K` on macOS, `Ctrl+K`
elsewhere — `shortcuts_model.label(chord, platform)`). Keyboard: `←/→`
between menus, `↑/↓` roving, `Enter` runs, `Esc` closes, `Alt`-free (no
mnemonics at MVP). Implementation extends the existing `.menu-wrap/.menu/
.menu-item` primitive (`main.js:1079 setupMenus`, moved into `shell/menu.js`,
which also keeps serving the project/branch/export menus — the "snapshots
`.menu-wrap` at boot" caveat becomes `menu.attach(wrapEl)`). Disabled rows
(`when` false) render `aria-disabled` rather than vanishing, so the map stays
stable.

Rejected: replacing the toolbar wholesale — the PRD asks for a compact menu
system, and PRD-025's workspace bar will reshape the header anyway; a menu
bar row beside the existing controls is the minimal, non-churning step.

## 5. Layout manager (Decision 5 — FR8)

`layout.init({workspace: "default"})` (PRD-025 will pass the workspace id):
three resizable regions — `#sidebar` (width, 160–480, default 216),
`#inspector` (width, 240–640, default 326), `#chat-dock` (height, 120–60vh,
default 264; its existing collapsed state is folded in) — each with a
`<div class="resize-handle" role="separator" aria-orientation aria-valuenow
tabindex="0">` inserted by `layout.js` (pointer drag with `setPointerCapture`,
`←/→`/`↑/↓` nudges by 16 px for keyboard users, double-click/`Enter`
collapses/restores). State `{sidebar:{size,collapsed}, inspector:{…},
chat:{…}}` persists under `localStorage["agentcad.layout.<workspace>"]`
(`layout_model.serialize/deserialize` with clamping on read so a stale or
hand-edited value can't wedge a panel off-screen). `#viewport` already has a
`ResizeObserver` (`viewport.js:195`), so no explicit resize call. Responsive:
below 1100 px CSS width the inspector auto-collapses once (not persisted);
below 800 px the sidebar too — `layout_model.responsiveDefaults(width)`.
Toggles are actions (`view.sidebar.toggle` …) so they are in the palette,
menus and shortcuts. The chat dock's own collapse button keeps working and
writes through `layout` (its `agentcad.chat.open` key is migrated on first
read, then dropped).

## 6. Shortcuts (Decision 6 — FR9)

`shortcuts_model.normalize("Mod+Shift+k") → "Mod+Shift+K"` (modifier order
Mod, Ctrl, Alt, Shift; `Mod` = ⌘ on macOS/Ctrl elsewhere, decided at
dispatch from `navigator.platform`); `shortcuts_model.fromEvent(e) → chord`;
`register({chord, id, when?, scope?: "global"|"field-safe"})` throws
`ShortcutConflictError` when the same chord is already bound in the same
scope — **always, not only in dev** (registration is static code; a conflict
is a programming error and the unit test AC5 asks for a throw). One document
`keydown` listener: ignore when `dialogs.isModalOpen()` unless the action is
`field-safe`-scoped (Esc handling belongs to the dialog stack); ignore bare
keys when `inField`; `Mod+S` defers to CodeMirror's own binding exactly as
today (`main.js:1544`). Existing bindings migrate unchanged: `F`, `G`, `R`,
`Mod+Z`, `Mod+Y`/`Shift+Mod+Z`, `Mod+S`; Esc/Enter stay where they are (dialog
stack, tree rows, sketcher). The sketcher's `onKey` (`sketcher.js:136`) keeps
its `stopPropagation` — it is a modal *mode*, recorded as such in the
cheat-sheet ("while sketching: …" rows are declared data, not live bindings).
`?` (`Shift+/`, not in a field) opens the cheat-sheet dialog generated from
`shortcuts.list()` grouped by the owning action's area, plus the declared
sketcher rows. `docs/user-guide.md`'s shortcut table is regenerated from the
same data by hand in the docs slice and a test asserts every registered
chord appears in it.

## 7. `ui_open`, UX events, the route (Decision 7 — agent surface)

- `agentcad/core/tools_ui.py` (loads at `ui`, no gate provider, registers
  unconditionally): `ui_open {view: str, args?: object}` → validates `view`
  against `^[a-z][a-z0-9-]{0,39}$` and `args` as a JSON object ≤ 4 KiB, then
  `service.bus.publish({"type": "ui_open", "view", "args", "by": "agent"})`
  and returns `{"ok": true, "view", "delivered_to": n, "note"}` where `n` is
  `len(bus._subscribers)` exposed as a new `EventBus.subscriber_count()` — a
  browser-less server answers `delivered_to: 0` with the note "no browser is
  connected; nothing will open" (capability-honest, the `tools_history`
  `available: False` precedent). A per-process token bucket (10 opens / 10 s)
  refuses with a `validation_error`-class `{"error"}` payload naming the
  limit (PRD "ui_open abuse"). The broadcast reaches every connected
  client; the shell shows "opened by agent" attribution (§1.3).
- `agentcad/server/routes_ui.py`: `POST /api/ui/events` body `{type, …}` —
  `type ∈ {dialog_opened, dialog_submitted, palette_executed}`, payload
  allow-listed to `view`/`action`/`tool` string keys ≤ 80 chars, published on
  the bus with `"by": "browser"` and the `X-Agent-Id` client id as `client`.
  Member-only by default (not in `PUBLIC_PATHS`); a 422 on anything else.
  `frontend/js/shell/events.js` posts fire-and-forget (never blocks UI, swallows
  network errors).
- Frontend `handleEvent` gains `case "ui_open"` → `dialogs.openView(view,
  args, {by: "agent"})` and ignores the three telemetry types.
- Docs: `docs/agent-api.md` gains `ui_open` + the three events;
  `docs/architecture.md` gains the `frontend/js/shell/` row and the two new
  packs.

Rejected: a per-client `ui_open` target — the bus has no per-client routing
(every WS client gets every event) and building it is PRD-025/005 scope; we
record that `ui_open` is a broadcast.

## 8. Styling and accessibility (Decision 8 — FR10)

All new CSS in `frontend/css/app.css` (one file stays one file), token-only
colours, `:focus-visible` rings via the existing rule, `@media
(prefers-reduced-motion: reduce)` covers the dialog/palette transitions. The
**static a11y pass** is a node test over `dialogs_model.markup(spec)` and
`menu_model.markup(tree)`: every dialog has `role="dialog" aria-modal
aria-labelledby` pointing at an existing id; every field has a `<label for>`;
every button has text; the menubar has `role="menubar"/menu/menuitem`; the
listbox has `role="listbox"/option`; the separators have `aria-orientation`.
(No `package.json`, no axe: the repo has no node dependency and we are not
adding one — the PRD allows "equivalent static pass at MVP level".) Focus
trap/restore is tested in node with a **minimal DOM stand-in**? No — the trap
is DOM-behavioural; it is verified in the browser (AC6) and its *logic*
(`dialogs_model.nextFocusIndex(list, current, backwards)`) is unit-tested.

## 9. Testing (Decision 9)

- Node-in-pytest for every `*_model.js` (`tests/test_frontend_shell.py`):
  fuzzy ranking, recent-first, schema→fields (prefill, enum, json field),
  result routing thresholds, shortcut normalisation/conflict throw/labels,
  menu tree + separators, layout clamp/serialise/responsive defaults, dialog
  markup a11y pass.
- Python: `tests/test_tools_ui.py` (`ui_open` validation, delivered_to 0/1,
  rate limit, event shape), `tests/test_routes_ui.py` (allow-list, 422s,
  member-only), AC3 parity test: register a fixture tool into the app's
  registry → `GET /api/tools` lists it → `palette_model.entriesFromTools`
  includes it with name+description (the frontend needs no change: the test
  proves the palette's tool source is the registry response).
- `tests/test_prd026_acceptance.py`: AC1 grep, AC2 (`check_interference`
  present ⇒ in entries; registry without it ⇒ absent), AC3, AC5 (conflict
  throws; F/Mod+S/Mod+Z in the cheat-sheet data), AC6 static a11y, the
  user-guide shortcut table contains every registered chord, `index.html`
  has no `PRD-026 … has not landed` comment left.
- Browser (AC1/AC2/AC4/AC6 visual halves): driven by the controller with the
  Chrome tools where available; otherwise evidence-graded in the changelog
  (the PRD-014 precedent), never claimed.

## 10. Rulings ledger (made by the orchestrator, no human in the loop)

1. Phase-2 items (menu bar, full schema forms, `ui_open` + events,
   per-workspace layout memory API) are **in** — they fall out of the action
   registry cheaply; Phase 3 stays out.
2. Legacy modals adopt the shell (stack, Esc, trap, registry) but keep their
   markup — see Scope.
3. Toggle chords: `Mod+B` sidebar, `Shift+Mod+B` inspector, `Mod+J` chat dock,
   `Mod+K` palette, `Mod+N` new part, `?` cheat-sheet. Browser defaults on
   these are prevented (all are `preventDefault`-able).
4. The panel DI object is renamed `panelApi` in `main.js` only.
5. `ui_open` is a broadcast with visible attribution + a 10/10 s bucket; no
   per-client targeting.
6. Object/array tool args get a JSON field rather than being excluded — parity
   over polish; the "dedicated dialog" override is a tested seam, unused.
7. No node dependency is added; the a11y check is a static markup pass.
8. Menu taxonomy File/Edit/View/Model/Help is global (not workspace-scoped);
   PRD-025 may re-home rows by editing `menu:` strings.
