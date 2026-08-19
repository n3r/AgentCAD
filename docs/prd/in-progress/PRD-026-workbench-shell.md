# PRD-026 — Workbench shell revamp: dialogs, command palette, menus, panels

- **Status:** pending
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
- FR2. All existing native dialog call sites replaced: new project, open by
  path, new part, delete part (with dependency listing), example-reset
  confirmations — grep-verifiable zero `window.prompt|confirm|alert` in
  frontend/js.
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
