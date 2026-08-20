# PRD-026 — Workbench shell revamp: dialogs, command palette, menus, panels

- **Status:** completed — merged in PR #29 (MVP + Phase-2; Phase 3 — user remapping, layout presets, palette frecency — deferred)
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** founder idea #8 (Aug 2026), engineering-reviewed
- **Depends on:** — (foundational for other UI PRDs)
- **Related:** PRD-025 (workspace bar rides the shell), PRD-016, PRD-027, PRD-008 (threads UI uses dialog/panel primitives)

## Problem & motivation

The workbench still runs on v0.1 shell primitives: **`window.prompt()` for
creating projects and parts, `window.confirm()` for deletions**, a single
fixed three-pane layout, no menu system beyond the project/export dropdowns,
no command palette, and shortcuts that exist but are undiscoverable. Each is
fine alone; together they read as a prototype — and they are now the
bottleneck for every UI-carrying PRD (workspaces, review threads, conflict
views, Produce panels all need real dialogs, panels, and layout).

Founder idea #8 names this directly ("revamp menus, working areas, sidebars
and modal windows — right now we are using system dialogues"). The
competitive bar: Shapr3D demonstrates that adaptive, low-chrome UX is a
purchasable differentiator; every serious tool ships a command palette
(VS Code normalized it; Onshape/Fusion have searchable command finders)
(market_research.md, "Adjacent tools" / "Shapr3D"). For AgentCAD there is a
sharper reason: **the registry is the product's verb set** — a command
palette over it gives humans exactly the surface agents already have, which
is the human-agent parity story made tangible.

## Users & jobs

- **Every human user:** discover and invoke any action from the keyboard;
  never lose work to a mis-styled native dialog; arrange panels to their
  task.
- **New user:** menus and palette are the map of what the product can do.
- **Agent:** deep-link and event hooks into the same dialogs/panels the
  human uses ("I've opened the merge conflict view for you"), and a
  guarantee that human-visible actions and tool calls stay one vocabulary.
- **Other PRDs (as internal customers):** dialog, panel, palette, menu, and
  toast primitives they compose instead of re-inventing.

## Goals

- G1. Zero native `prompt/confirm/alert` calls — a first-party dialog
  system (modal + non-modal), accessible and theme-correct.
- G2. A command palette (⌘K / Ctrl+K) covering every UI action *and* every
  registry tool (with argument prompting from the tool's JSON Schema),
  fuzzy-searchable, keyboard-first.
- G3. A compact menu system (File/Edit/View/… or equivalent) exposing the
  same actions hierarchically; every menu row shows its shortcut.
- G4. Panel/layout manager: resizable and collapsible sidebars/inspector/
  dock, per-workspace layout memory (feeds PRD-025), sane responsive
  behavior down to laptop widths.
- G5. A shortcut system: central registration, conflict detection, a "?"
  cheat-sheet overlay, and (later) user remapping.
- G6. Everything themable through the existing token system (dark/light
  already shipped) and accessible: focus traps, Esc/Enter conventions,
  ARIA roles, visible focus.

## Non-goals

- Adopting a frontend framework or bundler — the shell stays vanilla ES
  modules (the no-build constraint is a project value; risk addressed
  below).
- Workspace tabs themselves (PRD-025), tree redesign (PRD-027), direct-
  modeling surfaces (PRD-016) — they *consume* these primitives.
- Full user-defined layout persistence/sharing (later phase).
- Native OS menu bars in the packaged app (browser-consistent UI only).

## Experience

Creating a part today: a bare `prompt("part id")`. After: ⌘K → "new part" →
an in-app dialog with id validation live (`[a-z][a-z0-9_]{0,39}`), label +
material + template fields, Enter to create, Esc to cancel — same dialog
reachable from the sidebar "+" and the File menu. Deleting: a danger-styled
confirm dialog naming the blast radius ("also removes 2 instances") instead
of `confirm()`.

The palette lists actions with their context: UI actions ("Fit view",
"Switch to Test", "Toggle theme"), model verbs from the registry
("check_interference — boolean-check every instance pair"), and recent
targets ("open project: rocketry"). Picking a registry tool with required
args opens a schema-generated mini-form (string/number/enum fields from the
tool's JSON Schema — the same schema agents consume); simple no-arg tools
run immediately with the result as a toast or routed panel.

Panels: drag borders to resize (min/max clamps), double-click to collapse;
the chat dock, sidebar, and inspector remember size per workspace. The "?"
overlay shows the live shortcut map grouped by area.

**Agent path.** Agents don't need the palette (they have the registry), but
gain: `ui_open {view, args?}` (open a named dialog/panel deep-view — merge
conflicts, part creation prefilled, spec detail) so an agent can *show*
instead of describe; every dialog open/submit emits events so an agent can
narrate or react. The palette↔registry unification is enforced by test: a
new tool registered by any pack appears in the palette with no frontend
change.

## Functional requirements

**Dialog system**
- FR1. Modal dialog primitive: focus trap, Esc/Enter, backdrop, danger
  variant, form validation states; non-modal panel primitive for
  persistent surfaces; both theme-token styled.
- FR2. All existing native dialog call sites replaced — grep-verifiable zero
  `window.prompt|confirm|alert` in `frontend/js`. **Correction (shipped,
  slice 2 report §1): 21 sites, five more modules than this PRD's draft
  list implied** (there was no "example-reset" confirmation in the shipped
  codebase; the real inventory is below) —
  `main.js` (9: discard-edits guard, new part, delete part, import-part-id,
  new project, open by path, the delete-branch picker + its per-row `×`
  confirm, new branch), `versions.js` (2: tag/name+message, restore),
  `merge.js` (1: abort), `proposals.js` (2: review summary, edit
  title+description), `market.js` (2, one folded into the other: add-to-
  project's project **select** + part id), `sketcher.js` (5: distance,
  radius, slot width, and the ellipse's two semi-axis fields, all through
  one `sketch-number` dialog). The **nine** legacy `.modal-overlay` modals
  (drawing, versions, share, merge, proposals, library, configs,
  `notifications`, and `materials` — PRD-028's browser, adopted when
  `origin/main` merged in) are a *separate* deviation —
  they **adopt** the shell (overlay stack, one Esc listener, focus trap,
  dialog registry) rather than being rewritten onto the dialog primitive; see
  "Shipped vs. deferred" below.
- FR3. Dialogs are componentized so other PRDs register theirs (a dialog
  registry keyed by view name — the target of `ui_open`).

**Command palette**
- FR4. ⌘K/Ctrl+K opens; fuzzy match over UI actions + registry tools
  (name, description) + projects/parts as navigation targets; keyboard
  navigation; recent-first ranking.
- FR5. Registry tools with required args get a schema-generated form
  (string/number/int/bool/enum from JSON Schema; project/part_id
  auto-filled from context); execution routes through the same
  `/api/tools/{name}` path agents use; results surface as toast (scalar),
  panel (structured), or viewport effect (geometry ops).
- FR6. Palette entries carry capability presence (a tool absent from the
  registry never appears — FEM rule).

**Menus, panels, shortcuts**
- FR7. Menu bar with the action tree; every row = same action objects the
  palette uses (single action registry; no drift).
- FR8. Resizable/collapsible sidebar, inspector, chat dock with per-
  workspace persistence (localStorage) and keyboard toggles.
- FR9. Central shortcut registry with conflict detection at registration;
  "?" cheat-sheet overlay; existing shortcuts (F, Cmd+S, Cmd+Z, Esc,
  Enter) migrate in unchanged.
- FR10. Reduced-motion respect; all overlays and dialogs pass a basic a11y
  audit (labels, roles, focus order) — checked in CI with axe-core or
  equivalent static pass at MVP level.

## Agent surface

New tool: `ui_open {view, args?}` (no-op with a structured note when no
browser is connected — capability-honest). New events: `dialog_opened`,
`dialog_submitted {view}`, `palette_executed {action}` (agent-observable UX
telemetry, also feeds future onboarding skills). No kernel or manifest
changes.

## Technical approach

All frontend, vanilla ES modules (`frontend/js/shell/…`): `dialogs.js`
(primitives + registry), `palette.js`, `menu.js`, `layout.js`,
`shortcuts.js`, with one `actions.js` registry that palette/menus/shortcuts
share — UI actions declared as `{id, title, run, when, shortcut?}` and
registry tools auto-ingested from `GET /api/tools` (schemas already served).
`ui_open` lands as a tiny tool pack that publishes a `ui_open` WS event the
shell handles. CSS composes the existing token system. The no-framework
risk is contained by keeping primitives small and DOM-direct (the codebase
already does this well — sketcher, gizmo, chat dock).

## MVP & phasing

- **MVP:** dialog primitives + all native-dialog replacements (FR1–FR3);
  palette over UI actions + no-arg and simple-arg tools (FR4–FR6);
  resizable panels (FR8); shortcut registry + "?" overlay (FR9).
- **Phase 2:** menu bar (FR7), full schema-form arg prompting, `ui_open` +
  events, per-workspace layout memory (with PRD-025).
- **Phase 3:** user shortcut remapping; layout presets; palette learning
  (frecency).

### Shipped vs. deferred (as landed, six slices)

Phase 2 (menu bar FR7, full schema-form arg prompting, `ui_open` + events,
per-workspace layout memory) shipped in full alongside the MVP — the design
spec's ruling 1 pulled it forward because it "falls out of the action
registry cheaply." **Phase 3 stayed deferred, exactly as scoped:**

- **User shortcut remapping** — not built. `shortcuts_model.Table` binds a
  fixed chord per registration; there is no UI or persisted override layer.
- **Layout presets** — not built. `layout_model` persists one size/collapsed
  state per panel per workspace; there is no named, savable/loadable set of
  layouts.
- **Palette frecency** — not built beyond "recent-first." `palette_model.rank`
  tie-breaks on `localStorage["agentcad.palette.recent"]` (last-20,
  most-recent-first) with no frequency weighting or decay.

Two deliberate scope decisions inside the MVP itself, made by the
orchestrator and pinned by tests, not oversights:

- **Five mid-flow dialogs have a `view:` id (so the dialog stack and the UX
  events name them) but deliberately carry no `dialogs.register` row, so
  nothing can open them out of context** — `openView` cannot reach them at all,
  which is the whole point of the ruling:
  `discard-edits` (a navigation guard, not a destination), `import-part-id`
  (names a file already uploaded), `restore-version` (names a specific tag),
  `review-summary` (a verdict on a specific proposal), `sketch-number` (an
  entity you are mid-way through drawing in the sketcher). Opening any of
  these standalone would either no-op or need arguments the caller cannot
  supply — "a registry row with nothing behind it is a menu that lies" (slice
  2 report §2). Pinned by
  `tests/test_frontend_shell.py::test_a_mid_flow_dialog_is_not_offered_as_an_openable_view`.
- **The `merge` view's `when` predicate is narrow.** It reads
  `() => !!staged`, module state `merge.js` sets only after a merge is
  attempted in the *same browser session* — so the palette's "Open: Staged
  merge…" row and `ui_open {view: "merge"}` will rarely fire even when the
  server genuinely has a merge staged from an earlier session or another
  client. Honest (it never claims a merge exists when its own state says
  otherwise) but narrow; widening it needs a cheap "is a merge staged" read
  that today only `reopenStaged()` performs, on demand (slice 2 report §7
  concern 4).

The nine legacy `.modal-overlay` modals (drawing, versions, share, merge,
proposals, library, configs, notifications — and `materials`, PRD-028's
browser, adopted when `origin/main` merged into this branch) were a scoped
non-goal from the design spec (§0, "Defer"): they **adopt** the shell
(overlay stack, one Esc listener, focus trap, dialog registry via
`dialogs.attachLegacy`) rather than being rewritten onto the `dialogs.open`
markup primitive. Their DOM and open/close functions are unchanged. This was
an explicit call, not a shortfall: a full rewrite is churn with no
user-visible gain and would collide with PRD-025's workspace work.

## Acceptance criteria

- AC1. `grep -rn "window.prompt\|window.confirm\|window.alert" frontend/js`
  returns nothing; creating and deleting a part via the new dialogs works
  in a browser session with zero console errors.
- AC2. ⌘K → type "interfer" → run `check_interference` on the current
  project → result toast/panel appears; the same palette entry disappears
  when the tool is absent (registry-driven, tested by launching without a
  pack).
- AC3. A newly registered tool (test fixture pack) appears in the palette
  with name+description and runs — no frontend change (parity test).
- AC4. Panels resize and persist across reload per workspace; keyboard
  toggles work (browser-verified).
- AC5. "?" shows the live shortcut map including F/Cmd+S/Cmd+Z; a
  conflicting registration throws in dev (unit test).
- AC6. Dialogs pass the automated a11y check; focus is trapped and restored
  correctly (test with keyboard-only walkthrough).
- AC7. Full suite green; UI verified in a real browser per definition of
  done.

### Acceptance record

| AC | Evidence |
|---|---|
| AC1 | `tests/test_frontend_shell.py::test_no_native_dialogs_remain` (the `NATIVE_DIALOG_RE` grep) and `tests/test_prd026_acceptance.py`'s own restatement; the `index.html` "PRD-026 … has not landed" comment's removal is pinned by `test_the_modal_overlay_dom_fallback_is_gone_now_that_all_nine_are_adopted` and `tests/test_prd026_acceptance.py::test_ac1_the_index_html_prd026_comment_is_gone`. The browser half (create/delete a part via the new dialogs, zero console errors) is slice 2 §5's live Playwright session against the installed Chrome — new-part focus/validation, delete-part's blast-radius note, the delete-branch picker, the dialog stack nesting, both themes, zero page errors. |
| AC2 | `tests/test_frontend_shell.py::test_ac2_check_interference_is_in_the_palette_because_the_registry_has_it` (real registry → `GET /api/tools` → `entriesFromTools`, both directions) and `tests/test_prd026_acceptance.py`'s compact restatement. |
| AC3 | `tests/test_frontend_shell.py::test_ac3_a_tool_registered_into_the_live_registry_reaches_the_palette` (a fixture `Tool` registered into the app's registry, served over real `GET /api/tools`, piped into `palette_model.entriesFromTools`, findable by `rank`) and `tests/test_prd026_acceptance.py`'s restatement. |
| AC4 | `tests/test_prd026_acceptance.py`'s layout round-trip + workspace-key-isolation test over `layout_model.serialize`/`deserialize`/`key`; the 30 `Slice 4` node tests (26 functions, one parametrized ×5) in `tests/test_frontend_shell.py` (clamp, responsive defaults, the localStorage-migration fix-round tests). The browser half was then verified live (controller-dispatched Playwright + installed Chrome against `agentcad serve`; the record is **changelog 0300's notes** — the session's own transcript lives under `.superpowers/`, which the merge added to `.gitignore`, so 0300 is the citation that exists for everyone): ArrowRight×3 grows the sidebar 48 px with `aria-valuenow` tracking, the size survives a reload via `agentcad.layout.default`, ⌘B/⇧⌘B/⌘J toggle and restore, double-click collapses and Enter expands — 12/12 checks passed, zero page errors. |
| AC5 | `tests/test_frontend_shell.py::test_a_second_binding_on_one_chord_throws_naming_both_ids` (`ShortcutConflictError`) plus `tests/test_prd026_acceptance.py`'s own throw assertion; `F`/`Mod+S`/`Mod+Z` (and the full registered set) confirmed present in `frontend/js/main.js`'s `registerActions()`; every registered chord's presence in `docs/user-guide.md`'s regenerated shortcut table is asserted by `tests/test_prd026_acceptance.py`. |
| AC6 | `tests/test_frontend_shell.py::test_markup_passes_the_static_a11y_pass` (dialog) and the menu-bar/palette a11y tests, plus `tests/test_prd026_acceptance.py`'s representative form/confirm/nonmodal + palette/menubar pass. The keyboard-only focus-trap/restore walkthrough is slice 1 §"Browser re-verification" and slice 2 §5/§"Browser re-verification" (Tab cycling, focus landing on Cancel for danger dialogs, Esc belonging to the topmost modal, Tab not hijacked from a `.CodeMirror` inside an adopted modal) — re-driven in the final controller browser pass (Tab×8 wraps inside the dialog, Esc returns focus to `#add-part-btn`, `?`/`F` are no-ops behind the modal; 12/12, zero page errors). |
| AC7 | `tests/test_prd026_acceptance.py::test_ac7_the_full_suite_count_is_cited`, reading the newest `docs/changelog/NNNN-*.md` entry for a `make test` count (the PRD-004/008/011/012 precedent — this entry stays the evidence check, not a re-run of the suite from inside the suite). The browser-verification half is slices 1–2's live Chrome sessions (see AC1/AC6); PRD-026 does not itself claim "done" — see Status. |

The `ui_open` agent surface (design §7, PRD "Agent surface") is proved
end-to-end in `tests/test_tools_ui.py` (registration, `delivered_to`
0/1/2, the rate limit, the exact published event shape) and restated
compactly in `tests/test_prd026_acceptance.py` — a tool call publishes
`{"type": "ui_open", "view", "args", "by": "agent"}` to a subscribed queue,
Python-only, no WebSocket required.

## Risks & open questions

- **No-framework scale risk:** shell primitives are where vanilla JS
  codebases start hurting. Mitigation: strict module boundaries, one
  action registry, no cross-module DOM reach-ins; revisit only if the
  primitives themselves become the bottleneck.
- **Palette arg-forms** for complex tools (nested objects like
  `set_assembly`) — MVP explicitly scopes to scalar/enum args; complex
  tools open their dedicated dialog instead (registry flag).
- **Menu taxonomy** needs a design pass with PRD-025 (workspace-scoped
  menus vs global) — resolve in the design spec.
- **`ui_open` abuse** (agent yanking surfaces around): rate-limit + always
  visible attribution ("opened by agent"), consistent with PRD-025's
  navigation etiquette.

## Competitive references

VS Code's palette is the pattern users expect; Onshape/Fusion ship command
search; Shapr3D sells "low-chrome, learnable" as a differentiator
(market_research.md, "Adjacent tools"). We differ: the palette is not a
separate command list but the *same registry agents drive* — human/agent
parity as a testable invariant, plus `ui_open` letting agents hand surfaces
to humans mid-collaboration.
