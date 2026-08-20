// PRD-026 shell — the command palette (⌘K): one input over three merged
// sources, and the routing of whatever comes back.
//
// The sources are merged per keystroke rather than copied at boot (spec §3):
//
//   1. `actions.list(ctx)`      — every UI verb, graded by the live context
//   2. `dialogs.views(ctx)`     — every registered dialog, as "Open: …"
//   3. `api.listTools()`        — the LIVE tool registry (FR6)
//   4. `state`                  — the other projects, and this project's parts
//
// (2) and (4) share the `navigation` section: they are all *places you go*.
// Nothing here enumerates tools — the palette shows exactly what
// `GET /api/tools` answered, so a pack that registers late appears on the next
// reconnect and a pack that is absent is honestly absent.
//
// Running a tool goes through the same `POST /api/tools/{name}` an agent uses.
// A tool whose required arguments the workbench already knows runs on Enter;
// anything else opens a schema-generated form first (Shift+Enter forces the
// form so the optional arguments stay reachable). The result is routed by
// `palette_model.routeResult`: a refusal goes back INTO the dialog, a small
// answer is a toast, a big one is the non-modal result panel.
//
// Node-importable: `document` is touched only inside `open()` and below.

import * as model from "./palette_model.js";
import { state as storeState, onKeys } from "../state.js";

const RECENT_KEY = "agentcad.palette.recent";
const SECTION_BADGE = { actions: "Action", navigation: "Go to", tools: "Tool" };
const PAGE = 8;
const MAX_ROWS = 60;
// The result panel's cap: past this the <pre> (and the clipboard copy) shows a
// prefix and says so. 64 KiB is far more JSON than anyone reads and far less
// than a script or a whole assembly.
const MAX_RESULT_BYTES = 64 * 1024;

let deps = null;
let recents = [];
let tools = null;          // the cached `entriesFromTools` rows, or null
let toolsPromise = null;
let lastConnected = false;

// Open-palette state. One at a time by construction.
let ui = null;             // {root, input, list, empty, handle, rows, active}

/** Wire the palette up. `actions`/`dialogs`/`api`/`toast`/`events` are the
 *  shell's own modules; `loadProject`/`selectPart` are `main.js`'s navigation
 *  verbs (the two things a palette row can do that no action covers, because
 *  both take an argument the registry has no way to enumerate). */
export function init(d) {
  deps = { state: storeState, ...(d || {}) };
  recents = loadRecents();

  deps.actions.register({
    id: "help.palette",
    title: "Command palette",
    description: "Search every action, tool and place in the workbench",
    group: "Help",
    // Same rank as `help.shortcuts` on purpose: they are the two rows of the
    // Help menu and neither is "before" the other; the menu falls back to
    // registration order, which puts the cheat-sheet first.
    menu: "help/10",
    shortcut: "Mod+K",
    keywords: ["command", "search", "run", "tool", "palette"],
    run: () => open(),
  });

  // The palette is itself an openable view — so `ui_open {view: "palette"}`
  // works, and so the palette can list itself the way it lists everything else.
  deps.dialogs.register("palette", () => open(), {
    title: "Command palette",
    description: "Search every action, tool and place in the workbench",
  });

  // The tool list is refetched on the RISING edge of `connected`: a socket
  // that dropped and came back may have come back to a different server
  // process with a different set of packs loaded.
  lastConnected = !!deps.state.connected;
  onKeys(["connected"], () => {
    const now = !!deps.state.connected;
    const rose = now && !lastConnected;
    lastConnected = now;
    if (!rose) return;
    tools = null;
    toolsPromise = null;
    if (ui) refreshTools().then(() => render());
  });

  // An action registered after boot (a late panel, a future pack) shows up in
  // an already-open palette rather than on its next opening.
  deps.actions.onChange(() => {
    if (ui) render();
  });
}

// ------------------------------------------------------------------ recents

function loadRecents() {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : [];
  } catch {
    return []; // storage disabled, or someone hand-edited it: start clean
  }
}

function remember(id) {
  recents = model.pushRecent(recents, id);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(recents));
  } catch {
    /* private mode: the ordering is a nicety, not state the app needs */
  }
}

// -------------------------------------------------------------------- tools

function refreshTools() {
  if (tools) return Promise.resolve(tools);
  if (toolsPromise) return toolsPromise;
  toolsPromise = Promise.resolve()
    .then(() => deps.api.listTools())
    .then((payload) => {
      tools = model.entriesFromTools(payload);
      return tools;
    })
    .catch(() => {
      // A palette that cannot reach the server still lists every UI action —
      // but it says so once, because "this server has no tools" and "we could
      // not ask" look identical in an empty Tools section.
      tools = null;
      deps.toast("Tool list unavailable — the palette is showing UI actions only",
                 "warn", { id: "palette-tools" });
      return [];
    })
    .finally(() => { toolsPromise = null; });
  return toolsPromise;
}

/** Every row the palette can show right now, in source order. */
function collect() {
  const ctx = deps.actions.context();
  const shortcutLabels = labelsByAction();
  const actionRows = model.entriesFromActions(deps.actions.list(ctx))
    .map((row) => (shortcutLabels[row.id]
      ? { ...row, shortcutLabel: shortcutLabels[row.id] }
      : row));
  return [
    ...actionRows,
    // The action rows are passed in so a view that an action already offers
    // does not appear twice (m4).
    ...model.entriesFromViews(deps.dialogs.views(ctx),
                              new Set(actionRows.map((r) => r.action))),
    ...model.entriesFromState({
      projects: deps.state.projects,
      parts: deps.state.project ? deps.state.project.parts : [],
      projectName: deps.state.projectName,
    }),
    ...(tools || []),
  ];
}

/** `actionId -> "⌘K"`, from the shortcut table when one was injected. */
function labelsByAction() {
  const out = {};
  const shortcuts = deps.shortcuts;
  if (!shortcuts || typeof shortcuts.list !== "function") return out;
  for (const row of shortcuts.list()) {
    if (row && row.actionId && !out[row.actionId]) out[row.actionId] = row.label;
  }
  return out;
}

// --------------------------------------------------------------------- open

/** Open the palette. A second call while it is open just refocuses the input
 *  — ⌘K twice must not stack two of them. */
export function open() {
  if (ui) {
    ui.input.focus();
    ui.input.select();
    return ui.promise;
  }
  const root = document.createElement("div");
  root.className = "palette";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "palette-input";
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "true");
  input.setAttribute("aria-controls", "palette-list");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-label", "Search actions, tools and places");
  input.placeholder = "Search actions, tools, parts…";
  input.autocomplete = "off";
  input.spellcheck = false;

  const list = document.createElement("div");
  list.className = "palette-list";
  list.id = "palette-list";
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", "Results");

  const empty = document.createElement("div");
  empty.className = "palette-empty";
  empty.textContent = "No matches";

  root.append(input, list, empty);

  const promise = deps.dialogs.open({
    view: "palette",
    title: "Command palette",
    body: root,
    width: "wide",
    modal: true,
    buttons: [{ id: "close", label: "Close" }],
  });

  ui = { root, input, list, empty, handle: promise.handle, rows: [], active: 0,
         promise };
  promise.then(() => teardown());

  input.addEventListener("input", () => {
    ui.active = 0;
    render();
  });
  list.addEventListener("mousedown", (e) => {
    // mousedown, not click: the input must not lose focus first, and the
    // dialog's own backdrop handler only fires for the overlay itself.
    const row = e.target.closest ? e.target.closest("[data-index]") : null;
    if (!row) return;
    e.preventDefault();
    execute(ui.rows[Number(row.dataset.index)], e.shiftKey);
  });

  // WINDOW capture, not document capture: `dialogs.js` installs its keydown
  // listener on `document` in the capture phase, and window sits above
  // document on the capture path — so this runs first and can keep Enter,
  // ↑/↓ and PageUp/PageDown for the listbox instead of letting the dialog
  // read them as a submit. Esc and Tab are deliberately NOT intercepted:
  // they belong to the dialog stack and the focus trap.
  ui.onKey = (e) => onKey(e);
  window.addEventListener("keydown", ui.onKey, true);

  input.focus();
  render();
  refreshTools().then(() => { if (ui) render(); });
  return promise;
}

/** Close the palette, if it is open. */
export function close() {
  if (!ui) return false;
  deps.dialogs.close(ui.handle);
  return true;
}

function teardown() {
  if (!ui) return;
  window.removeEventListener("keydown", ui.onKey, true);
  ui = null;
}

// ------------------------------------------------------------------- render

function render() {
  if (!ui) return;
  const query = ui.input.value;
  const rows = model.rank(query, collect(), recents).slice(0, MAX_ROWS);
  ui.rows = rows;
  if (ui.active >= rows.length) ui.active = Math.max(0, rows.length - 1);
  ui.list.textContent = "";
  rows.forEach((row, i) => ui.list.appendChild(optionEl(row, i)));
  ui.empty.classList.toggle("hidden", rows.length > 0);
  syncActive();
}

function optionEl(row, i) {
  const el = document.createElement("div");
  el.className = "palette-option";
  el.id = `palette-opt-${i}`;
  el.dataset.index = String(i);
  el.setAttribute("role", "option");
  el.setAttribute("aria-selected", "false");
  if (row.enabled === false) {
    el.classList.add("disabled");
    el.setAttribute("aria-disabled", "true");
  }

  const title = document.createElement("span");
  title.className = row.section === "tools" ? "palette-title mono" : "palette-title";
  title.textContent = row.title;
  el.appendChild(title);

  if (row.description) {
    const desc = document.createElement("span");
    desc.className = "palette-desc";
    desc.textContent = row.description;
    el.appendChild(desc);
  }

  const badge = document.createElement("span");
  badge.className = "palette-badge";
  badge.textContent = SECTION_BADGE[row.section] || row.section;
  el.appendChild(badge);

  if (row.shortcutLabel) {
    const kbd = document.createElement("kbd");
    kbd.className = "palette-kbd";
    kbd.textContent = row.shortcutLabel;
    el.appendChild(kbd);
  }
  return el;
}

function syncActive() {
  if (!ui) return;
  const children = [...ui.list.children];
  children.forEach((el, i) => {
    const on = i === ui.active;
    el.classList.toggle("active", on);
    el.setAttribute("aria-selected", on ? "true" : "false");
  });
  const current = children[ui.active];
  if (current) {
    ui.input.setAttribute("aria-activedescendant", current.id);
    if (typeof current.scrollIntoView === "function") {
      current.scrollIntoView({ block: "nearest" });
    }
  } else {
    ui.input.removeAttribute("aria-activedescendant");
  }
}

function move(delta) {
  if (!ui || !ui.rows.length) return;
  const n = ui.rows.length;
  ui.active = ((ui.active + delta) % n + n) % n;
  syncActive();
}

function onKey(e) {
  if (!ui) return;
  if (!ui.root.contains(e.target)) return;
  switch (e.key) {
    case "ArrowDown": move(1); break;
    case "ArrowUp": move(-1); break;
    case "PageDown": move(PAGE); break;
    case "PageUp": move(-PAGE); break;
    case "Enter": execute(ui.rows[ui.active], e.shiftKey); break;
    default: return; // Esc, Tab and every printable key belong elsewhere
  }
  e.preventDefault();
  e.stopPropagation();
}

// ------------------------------------------------------------------ execute

function execute(row, withOptions) {
  if (!ui || !row) return;
  if (row.enabled === false) {
    deps.toast(`${row.title} is not available right now`, "warn");
    return;
  }
  if (row.section === "tools") {
    // Nobody awaits this: catch here, or a throw anywhere down the tool path
    // is an unhandled rejection with no user-visible trace.
    runTool(row, !!withOptions).catch((err) => {
      deps.toast(`${row.title}: ${err && err.message ? err.message : err}`,
                 "error");
    });
    return;
  }
  // Everything else closes the palette FIRST: the verb may open a dialog of
  // its own, and it should not open behind this one.
  close();
  if (row.action) {
    // `palette_executed` for an action is emitted by `main.js`'s
    // `actions.onRun` listener (it sees `source: "palette"`), so it is
    // recorded exactly once and a menu run is never mistaken for a palette one.
    // Only the recents are this function's business.
    remember(row.id);
    deps.actions.run(row.action, null, { source: "palette" });
    return;
  }
  if (row.view) {
    // Same reasoning as the tool path above: `openView` is async and nobody
    // awaits it, so a throwing opener needs a catch of its own.
    deps.dialogs.openView(row.view, {}, { by: "user" }).catch((err) => {
      deps.toast(`${row.title}: ${err && err.message ? err.message : err}`,
                 "error");
    });
  } else if (row.nav && row.nav.kind === "project" && deps.loadProject) {
    deps.loadProject(row.nav.name);
  } else if (row.nav && row.nav.kind === "part" && deps.selectPart) {
    deps.selectPart(row.nav.name);
  } else {
    return; // nothing to run: not a run, so neither remembered nor emitted
  }
  ranRow(row.id);
}

/** THE rule for both the recents and the telemetry: a row counts as run when
 *  the verb was actually invoked — including when it then failed, because the
 *  user did run it and a refusal is a result. A tool form the user CANCELS is
 *  not a run: nothing was invoked, so it is neither remembered nor emitted.
 *  (Actions take the `remember` half only; `main.js` owns their event.) */
function ranRow(id) {
  remember(id);
  if (deps.events && typeof deps.events.emit === "function") {
    deps.events.emit("palette_executed", { action: id });
  }
}

// ------------------------------------------------------------ running tools

async function runTool(row, withOptions) {
  const name = row.tool;
  // The "dedicated dialog" seam (spec §3): an action registered as
  // `tool:<name>` overrides the generic form for that tool. None exist at
  // MVP; this is the one line that makes adding one enough.
  const dedicated = deps.actions.get(`tool:${name}`);
  if (dedicated) {
    close();
    remember(row.id);   // `main.js` emits the event for a registry run
    deps.actions.run(dedicated.id, null, { source: "palette" });
    return;
  }

  const ctx = deps.actions.context();
  const fields = model.formFields(row.schema, ctx);
  const runnable = fields.filter((f) => !f.divider);
  if (!withOptions && !model.needsForm(row.schema, ctx)) {
    // Nothing to ask: run it with what the context already answers.
    close();
    let body;
    try {
      body = model.coerce(runnable, valuesOf(runnable));
    } catch (err) {
      deps.toast(err.message, "error");
      return;
    }
    await callAndRoute(name, body, null);
    ranRow(row.id);     // the call was made; a refusal is still a run
    return;
  }

  close();
  await deps.dialogs.form({
    view: `tool:${name}`,
    title: name,
    note: row.description || undefined,
    fields,
    width: "default",
    buttons: [
      { id: "cancel", label: "Cancel" },
      { id: "run", label: "Run", kind: "primary", submits: true },
    ],
    // The refusal path: `onSubmit` returning false keeps the dialog open with
    // its error line filled in, so a rejected argument is still on screen next
    // to the field that caused it (spec §3 — "never a toast that disappears").
    onSubmit: async (values, handle) => {
      const body = model.coerce(runnable, values);   // throws → dialogs shows it
      const ok = await callAndRoute(name, body, handle);
      // Recorded either way, exactly like the no-form path above: the same
      // failure must not be counted two different ways depending on the
      // tool's arity.
      ranRow(row.id);
      return ok;
    },
  });
}

/** Values for a form we are NOT showing: the prefills, and nothing else. */
function valuesOf(fields) {
  const values = {};
  for (const f of fields) values[f.name] = f.value;
  return values;
}

/** Call the tool and route its answer. Returns false when the caller (a form)
 *  should stay open. */
async function callAndRoute(name, body, handle) {
  let result;
  try {
    result = await deps.api.callTool(name, body);
  } catch (err) {
    // A transport failure, not a refusal: `api.js` raises ApiError with the
    // server's own {type, message} when it has one.
    const message = (err && err.error && err.error.message) || (err && err.message)
      || "the server could not be reached";
    if (handle) handle.setError(message);
    else deps.toast(`${name}: ${message}`, "error");
    return false;
  }
  const route = model.routeResult(result);
  if (route === "error") {
    const message = model.errorMessage(result);
    if (handle) handle.setError(message);
    else deps.toast(`${name}: ${message}`, "error");
    return false;
  }
  if (route === "toast") deps.toast(`${name}: ${model.summarize(result)}`, "success");
  else showResult(name, result);
  return true;
}

/** Pretty JSON for the result panel, size-capped and circular-safe.
 *
 *  The panel is BY DEFINITION the large-result path, so it is the one place
 *  where a multi-MB payload (a script, a whole assembly) turns into a
 *  multi-MB text node and a multi-MB `clipboard.writeText`. Past the cap the
 *  panel shows a prefix and says so. `JSON.stringify` is caught rather than
 *  trusted: it throws on a cycle and on a BigInt, and this runs inside a
 *  promise nobody awaits, where a throw would be an unhandled rejection.
 */
export function resultText(result, limit = MAX_RESULT_BYTES) {
  let text;
  try {
    text = JSON.stringify(result, null, 2);
  } catch (err) {
    return { text: `[this result cannot be shown as JSON: ${err.message}]`,
             truncated: false };
  }
  if (text == null) return { text: String(result), truncated: false };
  if (text.length <= limit) return { text, truncated: false };
  return { text: text.slice(0, limit), truncated: true };
}

/** The non-modal result panel: pretty JSON and a copy button. */
function showResult(name, result) {
  const wrap = document.createElement("div");
  wrap.className = "dlg-result";
  const pre = document.createElement("pre");
  const { text, truncated } = resultText(result);
  pre.textContent = text;
  if (truncated) {
    const note = document.createElement("div");
    note.className = "dlg-note";
    note.textContent =
      `Showing the first ${Math.round(MAX_RESULT_BYTES / 1024)} KB — `
      + "the full result is larger.";
    wrap.appendChild(note);
  }
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "dlg-btn palette-copy";
  copy.textContent = "Copy JSON";
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      deps.toast("Result copied", "success");
    } catch {
      deps.toast("Could not copy — select the text instead", "warn");
    }
  });
  wrap.append(copy, pre);
  return deps.dialogs.open({
    view: "tool-result",
    title: name,
    body: wrap,
    width: "wide",
    modal: false,
    buttons: [{ id: "close", label: "Close" }],
  });
}

/** Test seam only. */
export function reset() {
  teardown();
  deps = null;
  recents = [];
  tools = null;
  toolsPromise = null;
}

export const __paletteUI__ = { collect, render, execute, runTool, onKey };
