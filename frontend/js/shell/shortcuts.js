// PRD-026 shell — the one keyboard listener.
//
// Bindings are NOT registered here: an action declares `shortcut` and this
// module binds it from `actions.onChange`. That is deliberate — it makes
// `actions.register` the single place a chord can enter the app, so a conflict
// throws from the registration that caused it, with both ids named, in every
// build (spec §6 / AC5).
//
// Three dispatch rules, each of them a v0.1 behaviour preserved rather than
// invented:
//
//   1. modal open → only `scope: "modal-safe"` bindings fire. Esc and Enter
//      belong to the dialog stack, which is why none exist at MVP.
//   2. in a text field → only chords carrying a modifier fire. (`F`, `G`, `R`
//      and `?` must not type-and-act.)
//   3. a binding may carry its own `when(ctx)` — `Mod+Z` steps back OUT of a
//      field only (inside one, the browser's own text undo is the right
//      answer) and `Mod+S` defers to CodeMirror's binding when the editor has
//      focus, exactly as `main.js`'s `setupKeys` did.
//
// A binding whose `when` says no does NOT `preventDefault`: the keystroke was
// never ours, so the browser (or CodeMirror) still gets it.

import { Table, fromEvent, label as labelFor, normalize } from "./shortcuts_model.js";

let table = new Table();
let actionsApi = null;
let dialogsApi = null;
let platform = "";
let installed = false;
const declared = []; // documented-only rows for the cheat-sheet

/** Wire the listener and bind every action that already declares a shortcut. */
export function init(deps) {
  const d = deps || {};
  actionsApi = d.actions || null;
  dialogsApi = d.dialogs || null;
  platform = d.platform
    || (typeof navigator === "undefined" ? "" : (navigator.platform || ""));
  if (actionsApi) {
    // `all()`, not `list({})`: a chord is bound whether or not its action
    // is eligible in an empty context.
    for (const spec of actionsApi.all()) bindSpec(spec);
    actionsApi.onChange((_id, spec) => bindSpec(spec));
  }
  if (!installed && typeof document !== "undefined") {
    document.addEventListener("keydown", onKeyDown);
    installed = true;
  }
  return table;
}

/** Bind one chord by hand (`{chord, id, scope?, when?}`). Throws on conflict. */
export function bind(spec) {
  return table.bind(spec);
}

function bindSpec(spec) {
  if (!spec || !spec.shortcut) return;
  const list = Array.isArray(spec.shortcut) ? spec.shortcut : [spec.shortcut];
  for (const item of list) {
    const one = typeof item === "string" ? { chord: item } : item;
    table.bind({
      chord: one.chord,
      id: spec.id,
      scope: one.scope || "global",
      when: one.when || null,
      title: spec.title,
      group: spec.group || groupOf(spec.id),
    });
  }
}

function groupOf(id) {
  const area = String(id).split(".")[0];
  return area.charAt(0).toUpperCase() + area.slice(1);
}

/** A chord this app does not own but the user must be told about — the
 *  sketcher's modal keys, which `stopPropagation` before we ever see them.
 *  Data for the cheat-sheet, never a live binding. */
export function declare(row) {
  declared.push({
    chord: row.chord,
    label: safeLabel(row.chord),
    actionId: null,
    title: row.title,
    group: row.group || "Sketching",
    declaredOnly: true,
  });
  return declared[declared.length - 1];
}

/** Every row the "?" cheat-sheet shows: live bindings then declared ones. */
export function list() {
  const bound = table.list().map((row) => ({
    chord: row.chord,
    label: safeLabel(row.chord),
    actionId: row.id,
    title: (actionsApi && actionsApi.get(row.id) && actionsApi.get(row.id).title)
      || row.title || row.id,
    group: row.group || groupOf(row.id),
    scope: row.scope,
  }));
  return [...bound, ...declared];
}

function safeLabel(chord) {
  try {
    return labelFor(chord, platform);
  } catch {
    return String(chord);
  }
}

/** Test/boot seam: forget every binding (never called by the app). */
export function reset() {
  table = new Table();
  declared.length = 0;
}

function onKeyDown(e) {
  const chord = fromEvent(e, platform);
  if (!chord) return;
  // Look the chord up BEFORE asking anything of the DOM: this runs on every
  // keystroke in the app, including every character typed into the editor, and
  // an unbound key must cost one Map hit and nothing else.
  const globalRow = table.lookup(chord, "global");
  const safeRow = table.lookup(chord, "modal-safe");
  if (!globalRow && !safeRow) return;
  // Rule 1: behind a modal only `modal-safe` bindings fire — Esc and Enter
  // belong to the dialog stack.
  const modalOpen = dialogsApi ? dialogsApi.isModalOpen() : false;
  const row = modalOpen ? safeRow : (globalRow || safeRow);
  if (!row) return;

  // Duck-typed rather than `instanceof Element`: this function is unit-tested
  // in node, where there is no `Element` to be an instance of.
  const target = e.target && typeof e.target.closest === "function"
    ? e.target : null;
  const ctx = {
    ...(actionsApi ? actionsApi.context() : {}),
    modalOpen,
    inField: target
      ? target.closest("input, textarea, select, .CodeMirror, [contenteditable]") != null
      : false,
    inCodeMirror: target ? target.closest(".CodeMirror") != null : false,
  };
  // Rule 2: a bare key in a text field types; it does not act. (There is no
  // `field-safe` scope: the lookup above knows only `global` and `modal-safe`,
  // so a third name would have been a branch nothing could reach. A binding
  // that must survive a field carries a modifier, or says so in its `when`.)
  const bare = !/^(Mod|Ctrl|Alt)\+/.test(chord);
  if (ctx.inField && bare) return;
  // Rule 3: the binding's own predicate, then the action's.
  if (row.when && !row.when(ctx)) return;
  const spec = actionsApi ? actionsApi.get(row.id) : null;
  if (!spec) return;
  if (spec.when && !spec.when(ctx)) return;

  e.preventDefault();
  const result = actionsApi.run(row.id, ctx, { source: "shortcut" });
  if (result && typeof result.catch === "function") {
    result.catch((err) => console.error(`shortcut ${chord} failed`, err));
  }
}

export const __shortcutsDispatch__ = { onKeyDown, normalize };
