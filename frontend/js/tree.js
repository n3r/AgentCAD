// Left sidebar: the parts folder tree and the assembly instance tree, both
// VIRTUALIZED (PRD-027 FR2/FR7). Pure render from state; every mutation goes
// through the panel API `main.js` injects, or through the tools in `api.js`.
//
// What this module is and is not. Every ordering, filtering, selection and
// persistence RULE lives in the pure models — `tree_model.js` (the folder
// flatten, the match-bubbling filter, the Finder click table, the stored
// collapse state), `query_model.js` (the grammar) and `virtual_model.js` (the
// window arithmetic). What is left here is DOM: turn a row descriptor into an
// `<li>`, keep the rendered window in sync with `scrollTop`, and route a click,
// a key or a drop to the right verb. That split is why the interesting half of
// this feature is graded in node and only the pixels need a browser.
//
// Three things are load-bearing and easy to break:
//
//   * **Rows are exactly `ROW_HEIGHT` px.** The virtual window is division, not
//     a measured-offset table (see `virtual_model.js`), so a row that grows to
//     29 px makes the scrollbar lie by a row per thirty. `.side-list > .row`
//     pins the height in `app.css`; the thumbnail is 24 px inside it.
//   * **`virtual_model.js` is imported NAMESPACED.** `import { window }` would
//     shadow the browser global for this whole file, and `window.addEventListener`
//     below would silently call the model.
//   * **The row's `×` button is gone** (ruling 7). Delete lives in the context
//     menu and the bulk bar: a thousand-row list with a delete button per row
//     is a misclick farm. `actions.deletePart` is untouched and still the verb.

import { state, setState, onKeys } from "./state.js";
import { api, clientId } from "./api.js";
import {
  memberIdsOf, instanceTree, filterRows, folderTree, selectionAfter,
  persistTree, readTree, treeKey, isFolderPath,
} from "./tree_model.js";
import { hasFreeText, scriptOnly } from "./query_model.js";
import * as virtual from "./virtual_model.js";
import * as contextmenu from "./shell/contextmenu.js";
import * as dialogs from "./shell/dialogs.js";

/** The one row height, in CSS pixels. Mirrored by `.side-list.tree > .row` in
 *  `app.css`; the two must agree exactly (see the header). */
export const ROW_HEIGHT = 28;

/** How long the filter box waits before asking the SERVER. The client filter
 *  applies on the keystroke; only free text needs the round trip, because only
 *  the server has the part scripts. */
const SEARCH_DEBOUNCE_MS = 120;

let actions = null;
let partsView = null;
let instancesView = null;
let filterEl = null;
let filterMsgEl = null;
let countEl = null;
let rootDropEl = null;

// Which grouped instance rows (patterns / sub-assemblies) are expanded to their
// members. PRD-013 behaviour, carried across the rewrite unchanged.
const expanded = new Set();

// The persisted per-project tree state (`{collapsed, emptyFolders}` of folder
// paths). Loaded lazily on the first render after a project switch, because
// `state.projectName` is what names its localStorage key.
let treeState = {collapsed: [], emptyFolders: []};
let treeStateProject = null;

// The last server answer for the current free-text query: the ids it named and
// the snippets it carried. `null` means "no server answer applies" — either the
// query has no free text or one is still in flight.
let serverIds = null;
let snippets = {};
let searchSeq = 0;
let searchTimer = null;
// The two refusals the filter box can be showing. `parseError` is the client
// grammar's, recomputed on every render from the text in the box;
// `searchError` is the server's, and it must SURVIVE repaints it has nothing
// to do with — it is cleared when the query changes or a search succeeds.
let parseError = null;
let searchError = null;
// The query the parts list was last laid out for, so a filter change can send
// the scrollport back to the top (row 40 of the old list is nowhere in the new
// one, and the user is looking at whatever happens to be at that offset).
let lastLaidOutFilter = "";

// Thumbnails that 404ed or failed to decode, keyed `${partId}:${thumbKey}` so a
// rebuild's new key gets a fresh chance. Without it a missing thumb re-requests
// on every scroll frame.
const thumbFailed = new Set();

// The drag in flight: `{kind: "part"|"instance", ids: [...]}` or null.
let dragging = null;

export const INSTANCE_PALETTE = [
  "#8f9aa6", "#a68d6e", "#7d9b8a", "#9184a1", "#a1786e",
  "#6e8ba1", "#9aa16e", "#a16e93",
];

export function instanceColor(inst, index) {
  return inst.color || INSTANCE_PALETTE[index % INSTANCE_PALETTE.length];
}

// ------------------------------------------------------------------- boot

export function init(a) {
  actions = a;
  filterEl = document.getElementById("tree-filter");
  filterMsgEl = document.getElementById("tree-filter-msg");
  countEl = document.getElementById("tree-count");
  rootDropEl = document.getElementById("parts-root-drop");
  contextmenu.init(document.getElementById("contextmenu-host"));

  partsView = makeView(document.getElementById("parts-list"), "part");
  instancesView = makeView(document.getElementById("instances-list"), "instance");

  document.getElementById("add-part-btn").addEventListener("click", () => {
    actions.addPart();
  });
  document.getElementById("assembly-head").addEventListener("click", () => {
    actions.selectAssembly(null);
  });

  filterEl.addEventListener("input", onFilterInput);
  filterEl.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      clearFilter();
      focusTree();
    }
  });
  wireRootDrop();

  onKeys(
    ["project", "assembly", "selectedPart", "selectedInstance", "mode",
     "rebuilding", "partKinds", "presence", "selection", "treeFilter",
     "projectName"],
    render
  );
  // The window can resize without any state changing, and the window arithmetic
  // reads `clientHeight`: a taller sidebar has to grow its rendered slice.
  window.addEventListener("resize", () => {
    paint(partsView);
    paint(instancesView);
  });
  render();
}

/** One virtualized list. `kind` decides which row builder and which verbs. */
function makeView(listEl, kind) {
  const view = {
    kind, listEl,
    rows: [],            // the full flattened list (the model's answer)
    painted: null,       // "start,end" of the DOM currently in the <ul>
    focusKey: null,      // the row that owns tabIndex 0 (roving)
    raf: 0,
  };
  listEl.addEventListener("scroll", () => {
    if (view.raf) return;
    view.raf = requestAnimationFrame(() => {
      view.raf = 0;
      paint(view);
    });
  });
  listEl.addEventListener("click", (e) => onRowClick(view, e));
  listEl.addEventListener("keydown", (e) => onRowKey(view, e));
  listEl.addEventListener("contextmenu", (e) => onRowContextMenu(view, e));
  listEl.addEventListener("dragstart", (e) => onDragStart(view, e));
  listEl.addEventListener("dragend", onDragEnd);
  listEl.addEventListener("dragover", (e) => onDragOver(view, e));
  listEl.addEventListener("dragleave", (e) => onDragLeave(view, e));
  listEl.addEventListener("drop", (e) => onDrop(view, e));
  return view;
}

// --------------------------------------------------------------- the models

/** Adopt a project: its stored collapse state, and a clean filter.
 *
 *  Clearing the filter is not tidiness, it is correctness. `serverIds` is a
 *  list of PART IDS the last search returned, and `filterRows` UNIONS it with
 *  the client's own matches — so a `bracket` that exists in both projects
 *  would stay in the list after the switch because the *previous* project's
 *  server answer named it. The debounced call in flight is cancelled with it.
 *
 *  `state.treeFilter` is assigned directly rather than through `setState`:
 *  this runs inside `renderNow`, `setState` re-enters `render`, and the
 *  re-entrancy guard would then swallow the very pass that is reading it. No
 *  other module subscribes to `treeFilter`. */
function syncTreeState() {
  if (treeStateProject === state.projectName) return;
  treeStateProject = state.projectName;
  let raw = null;
  try {
    raw = localStorage.getItem(treeKey(state.projectName));
  } catch {
    /* storage disabled: an all-expanded tree is the honest fallback */
  }
  treeState = readTree(raw);
  clearTimeout(searchTimer);
  searchSeq += 1;                 // an answer still in flight is now stale
  serverIds = null;
  snippets = {};
  searchError = null;
  if (filterEl) filterEl.value = "";
  state.treeFilter = "";
}

function saveTreeState() {
  try {
    localStorage.setItem(treeKey(treeStateProject),
                         persistTree(treeStateProject, treeState));
  } catch {
    /* storage disabled: the state lives for this page only */
  }
}

function toggleCollapsed(path, want) {
  const key = String(path).toLowerCase();
  const kept = treeState.collapsed.filter(
    (p) => String(p).toLowerCase() !== key);
  const collapse = want === undefined ? kept.length === treeState.collapsed.length
                                      : want;
  treeState.collapsed = collapse ? [...kept, path] : kept;
  saveTreeState();
  render();
}

/** The parts list as display rows, plus the "n of N" counts.
 *
 *  A refusal from the grammar (`colour:red`) is REPORTED, not thrown: the
 *  message goes under the filter box and the previous rows stay on screen, so
 *  a half-typed `field:` term does not blank the sidebar mid-keystroke.
 *
 *  The parse refusal is recomputed here on every render; the SERVER's refusal
 *  is not (it lives in `searchError` until the query changes or a search
 *  succeeds). They were one variable once, and a repaint from an unrelated
 *  `rebuild_finished` then wiped the server's message off the screen a
 *  fraction of a second after it appeared. */
function partRows() {
  const parts = (state.project && state.project.parts) || [];
  syncTreeState();
  const opts = {ids: serverIds, collapsed: treeState.collapsed,
                emptyFolders: treeState.emptyFolders};
  try {
    const answer = filterRows(parts, state.treeFilter || "", opts);
    parseError = null;
    renderFilterMessage();
    return answer;
  } catch (err) {
    parseError = err && err.message ? err.message : String(err);
    renderFilterMessage();
    return {rows: folderTree(parts, opts), total: parts.length,
            shown: parts.length};
  }
}

/** The instance list as display rows: the foldered PRD-013 tree, with an
 *  expanded group's members spliced in under it. */
function instanceDisplayRows() {
  const instances = (state.project && state.project.assembly
    && state.project.assembly.instances) || [];
  const flattened = (state.assembly && state.assembly.instances) || [];
  const byId = new Map(flattened.map((m) => [m.id, m]));
  const rawById = new Map();
  const colorIndex = new Map();
  instances.forEach((inst, i) => {
    rawById.set(inst.id, inst);
    colorIndex.set(inst.id, i);
  });
  syncTreeState();
  const base = instanceTree(instances, {collapsed: treeState.collapsed});
  const out = [];
  for (const row of base) {
    if (row.kind !== "instance") {
      out.push(row);
      continue;
    }
    const desc = row.instance;
    out.push({...row, raw: rawById.get(desc.id) || {id: desc.id},
              colorIndex: colorIndex.get(desc.id) || 0});
    if (!desc.expandable || !expanded.has(desc.id)) continue;
    const members = memberIdsOf(desc.id, flattened);
    members.forEach((mid, k) => {
      out.push({kind: "member", id: mid, depth: row.depth + 1,
                raw: byId.get(mid) || {id: mid},
                colorIndex: (colorIndex.get(desc.id) || 0) + k});
    });
  }
  return out;
}

/** A stable identity for a row, across re-renders and window moves. */
function rowKey(row) {
  if (!row) return null;
  if (row.kind === "folder") return `f:${row.path}`;
  return `${row.kind[0]}:${row.id}`;
}

// -------------------------------------------------------------- rendering

let rendering = false;

function render() {
  // `pruneSelection` writes to the store, which re-enters this function. The
  // guard keeps that to one pass rather than two identical ones.
  if (rendering) return;
  rendering = true;
  try {
    renderNow();
  } finally {
    rendering = false;
  }
}

function renderNow() {
  syncTreeState();
  pruneSelection();
  const answer = partRows();
  partsView.rows = annotateSiblings(answer.rows);
  partsView.painted = null;               // the data moved: repaint regardless
  // A new filter is a NEW LIST: keeping the old scroll offset lands the user
  // 40 rows into a list that may only have three, or in the middle of matches
  // they never scrolled to. Home is the only honest offset.
  const filter = state.treeFilter || "";
  if (filter !== lastLaidOutFilter) {
    lastLaidOutFilter = filter;
    partsView.listEl.scrollTop = 0;
  }
  instancesView.rows = annotateSiblings(instanceDisplayRows());
  instancesView.painted = null;
  renderCount(answer);
  paint(partsView);
  paint(instancesView);
}

/** Stamp `posinset`/`setsize` on every row, counted among its SIBLINGS.
 *
 *  A flattened tree makes the flat index the obvious answer and the wrong one:
 *  `aria-posinset` is defined relative to the row's parent, so a screen reader
 *  should hear "2 of 3" inside a three-part folder, not "812 of 1009". The
 *  parent is the nearest preceding row one level up — which is exactly what
 *  `folderTree`'s display order guarantees — so one pass with a depth-indexed
 *  stack finds it, and a second groups by it. Once per data change, never per
 *  scroll frame. */
export function annotateSiblings(rows) {
  const parentOf = new Array(rows.length);
  const stack = [];                 // stack[d] = index of the last row at d
  for (let i = 0; i < rows.length; i++) {
    const depth = rows[i].depth || 0;
    parentOf[i] = depth > 0 && stack[depth - 1] !== undefined
      ? stack[depth - 1] : -1;
    stack[depth] = i;
    stack.length = depth + 1;       // anything deeper belongs to a past branch
  }
  const sizes = new Map();
  const pos = new Array(rows.length);
  for (let i = 0; i < rows.length; i++) {
    const n = (sizes.get(parentOf[i]) || 0) + 1;
    sizes.set(parentOf[i], n);
    pos[i] = n;
  }
  for (let i = 0; i < rows.length; i++) {
    rows[i].posinset = pos[i];
    rows[i].setsize = sizes.get(parentOf[i]);
  }
  return rows;
}

/** Drop selected ids the project no longer has (a delete, a project switch).
 *  Silent, and it never touches the scalar primary — `refreshProject` owns
 *  that. */
function pruneSelection() {
  const sel = state.selection;
  if (!sel || !sel.size) return;
  const live = new Set(((state.project && state.project.parts) || [])
    .map((p) => p.id));
  const kept = [...sel].filter((id) => live.has(id));
  if (kept.length === sel.size) return;
  setState({selection: new Set(kept),
            selectionAnchor: live.has(state.selectionAnchor)
              ? state.selectionAnchor : null});
}

function renderCount(answer) {
  if (!countEl) return;
  const filtering = !!(state.treeFilter || "").trim();
  countEl.textContent = filtering
    ? `${answer.shown} of ${answer.total}`
    : (answer.total ? String(answer.total) : "");
  countEl.title = filtering
    ? `${answer.shown} of ${answer.total} parts match the filter`
    : `${answer.total} part${answer.total === 1 ? "" : "s"}`;
}

/** Render the window of `view.rows` that is currently in view.
 *
 *  The two spacer `<li>`s carry the height of everything above and below, so
 *  the scrollbar describes the whole tree while the DOM holds a few dozen
 *  nodes: `padTop + rendered·28 + padBottom === rows.length · 28`, exactly. */
function paint(view) {
  if (!view || !view.listEl) return;
  const el = view.listEl;
  const total = view.rows.length;
  if (!total) {
    view.painted = "empty";
    el.textContent = "";
    el.appendChild(emptyRow(view));
    return;
  }
  const win = virtual.window({
    scrollTop: el.scrollTop,
    viewportHeight: el.clientHeight,
    rowHeight: ROW_HEIGHT,
    total,
  });
  const signature = `${win.start},${win.end},${total}`;
  if (view.painted === signature) return;
  view.painted = signature;

  // Which row the keyboard is on, BEFORE the wipe: every repaint replaces the
  // `<li>` the user is standing on, and a focus that fell back to `<body>`
  // would strand a keyboard user every time a folder opened or the list
  // scrolled under them.
  // `closest('li.row')`, not `active.dataset`: the focused element is often
  // the row's `⋯` BUTTON, whose own dataset has no `rowKey` — reading it
  // directly lost the row on every repaint while the button had focus.
  const active = document.activeElement;
  const activeRow = active && active.closest ? active.closest("li.row") : null;
  const focusedKey = activeRow && el.contains(activeRow)
    ? activeRow.dataset.rowKey || null
    : null;

  const frag = document.createDocumentFragment();
  frag.appendChild(spacer(win.padTop));
  const focusKey = resolveFocusKey(view, win);
  for (let i = win.start; i < win.end; i++) {
    const row = view.rows[i];
    const li = view.kind === "part" ? partRowEl(row) : instanceRowEl(row);
    const key = rowKey(row);
    li.dataset.rowIndex = String(i);
    li.dataset.rowKey = key;
    li.setAttribute("aria-level", String((row.depth || 0) + 1));
    // Siblings, not the flat list: `aria-setsize` is "how many rows share this
    // row's parent", so a part in a 3-part folder announces "3 of 3", never
    // "812 of 1009" (`annotateSiblings`, computed once per data change).
    li.setAttribute("aria-setsize", String(row.setsize || total));
    li.setAttribute("aria-posinset", String(row.posinset || (i + 1)));
    li.style.paddingLeft = `${7 + (row.depth || 0) * 12}px`;
    li.tabIndex = key === focusKey ? 0 : -1;
    frag.appendChild(li);
  }
  frag.appendChild(spacer(win.padBottom));
  el.textContent = "";
  el.appendChild(frag);
  // Restore focus ONLY to the very row that had it, and only when the repaint
  // kept that row. Two rules, both learned the hard way:
  //
  //   * `{preventScroll: true}` — the window carries 8 overscan rows above the
  //     viewport, so the focused row is routinely rendered but OUTSIDE the
  //     scrollport. A bare `.focus()` scrolls it back into view, which means
  //     every repaint yanked the list back and the tree fought the user's own
  //     scroll for as long as any row was focused (i.e. from the first click).
  //   * no fallback. Focusing "whatever owns the tab stop" when the user's row
  //     has scrolled away MOVES them somewhere they never asked to be. If the
  //     row is gone, focus goes where the browser puts it and the roving tab
  //     stop (below) is still there for Tab to come back to.
  if (focusedKey) {
    const back = el.querySelector(
      `li.row[data-row-key="${cssEscape(focusedKey)}"]`);
    if (back) back.focus({preventScroll: true});
  }
}

/** `CSS.escape`, or a good-enough fallback: a row key holds a part id or a
 *  folder path, and a folder path can carry spaces and dots. */
function cssEscape(value) {
  if (typeof CSS !== "undefined" && CSS && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
}

/** The key that owns `tabIndex = 0`.
 *
 *  A tree has exactly one tab stop, and under virtualization it has to be a
 *  row that is actually IN the DOM: a remembered row that has scrolled out of
 *  the window would leave nothing tabbable, and Tab could no longer enter the
 *  tree at all. So the search is over the WINDOW, not the whole list — which
 *  is also why this is O(rows on screen) and not O(1 000) on every scroll
 *  frame. When the remembered row is off screen the first rendered row takes
 *  the stop, which is where a keyboard user would expect to land anyway. */
function resolveFocusKey(view, win) {
  for (let i = win.start; i < win.end; i++) {
    if (rowKey(view.rows[i]) === view.focusKey) return view.focusKey;
  }
  view.focusKey = win.end > win.start ? rowKey(view.rows[win.start]) : null;
  return view.focusKey;
}

function spacer(height) {
  const li = document.createElement("li");
  li.className = "tree-pad";
  // `role="none"` + aria-hidden: a spacer is layout, and a tree whose children
  // include two anonymous items would report a size two rows too large.
  li.setAttribute("role", "none");
  li.setAttribute("aria-hidden", "true");
  li.style.height = `${Math.max(0, height)}px`;
  return li;
}

function emptyRow(view) {
  const li = document.createElement("li");
  li.className = "side-empty";
  li.setAttribute("role", "none");
  if (view.kind === "instance") {
    li.textContent = "No instances";
  } else if (!state.project) {
    li.textContent = "No project open";
  } else if ((state.treeFilter || "").trim()) {
    li.textContent = "No parts match";
  } else {
    li.textContent = "No parts yet — press +";
  }
  return li;
}

// ------------------------------------------------------------------ rows

function folderRowEl(row) {
  const li = document.createElement("li");
  li.className = "row row-folder";
  li.setAttribute("role", "treeitem");
  li.dataset.path = row.path;
  li.dataset.kind = "folder";
  li.setAttribute("aria-expanded", row.collapsed ? "false" : "true");

  const twist = document.createElement("span");
  twist.className = "row-twist";
  twist.textContent = row.collapsed ? "▸" : "▾";
  twist.setAttribute("aria-hidden", "true");
  li.appendChild(twist);

  const label = document.createElement("span");
  label.className = "row-label";
  label.textContent = row.name;
  label.title = row.path;
  li.appendChild(label);

  const count = document.createElement("span");
  count.className = "row-count";
  count.textContent = String(row.count);
  count.title = `${row.count} part${row.count === 1 ? "" : "s"} in ${row.path}`;
  li.appendChild(count);
  return li;
}

function partRowEl(row) {
  if (row.kind === "folder") return folderRowEl(row);
  const part = row.part || {};
  const li = document.createElement("li");
  li.className = "row";
  li.setAttribute("role", "treeitem");
  li.dataset.id = part.id;
  li.dataset.kind = "part";
  li.draggable = true;
  const isPrimary = state.mode === "part" && part.id === state.selectedPart;
  const inSelection = state.selection.has(part.id);
  if (isPrimary) li.classList.add("selected");
  if (inSelection) li.classList.add("multi");
  li.setAttribute("aria-selected", isPrimary || inSelection ? "true" : "false");

  li.appendChild(thumbEl(part));

  const label = document.createElement("span");
  label.className = "row-label";
  label.textContent = part.label || part.id;
  label.title = [part.id, part.material || null,
                 part.folder || null,
                 (part.tags || []).length ? `#${part.tags.join(" #")}` : null,
                 snippets[part.id] || null]
    .filter(Boolean).join(" · ");
  li.appendChild(label);

  // "why is this row here" — only while a filter is active, and only when the
  // evidence is the part's SCRIPT, which is the one source the browser cannot
  // see for itself.
  if (row.matchedOn && scriptOnly(row.matchedOn)) {
    const badge = document.createElement("span");
    badge.className = "row-badge";
    badge.textContent = "script";
    badge.title = snippets[part.id]
      ? `Matched in the script: …${snippets[part.id]}…`
      : "Matched in the part's script";
    li.appendChild(badge);
  }

  const kindInfo = state.partKinds[part.id];
  if (kindInfo && kindInfo.kind === "reference") {
    const badge = document.createElement("span");
    badge.className = "row-badge";
    badge.textContent = "ref";
    badge.title = kindInfo.source
      ? `imported reference · ${kindInfo.source}`
      : "imported reference";
    li.appendChild(badge);
  }

  // A configured part wears the configuration it is showing (or a neutral
  // `cfg` at base) — get_project carries both fields, so this costs no fetch.
  const cfgNames = Object.keys(part.configs || {});
  if (cfgNames.length) {
    const badge = document.createElement("span");
    badge.className = "row-badge";
    badge.textContent = part.active_config || "cfg";
    badge.title =
      `${cfgNames.length} configuration${cfgNames.length === 1 ? "" : "s"}` +
      ` · active: ${part.active_config || "base"}`;
    li.appendChild(badge);
  }

  // Presence and claims, rendered FROM STATE like everything else here — this
  // list is rebuilt on every relevant change, so an indicator poked in
  // imperatively would survive exactly until the next paint. "presence" is in
  // this module's onKeys for the same reason.
  const claim = claimOn(part.id);
  if (claim) {
    const chip = document.createElement("span");
    chip.className = "row-claim";
    chip.textContent = "editing";
    chip.title =
      `${labelOf(claim.holder)} has ${part.id} open for editing. ` +
      "A soft claim: it expires on its own, it only ever binds two humans, " +
      "and it can always be overridden.";
    li.appendChild(chip);
  } else {
    const watchers = othersOn(part.id);
    if (watchers.length) {
      const d = dot("presence");
      d.title = watchers
        .map((c) => `${c.label} — ${c.focus && c.focus.surface}`)
        .join("\n");
      li.appendChild(d);
    }
  }

  if (state.rebuilding.has(part.id)) {
    li.appendChild(dot("building"));
  } else if (part.state === "error") {
    const d = dot("error");
    d.title = "Last rebuild failed";
    li.appendChild(d);
  }

  li.appendChild(menuButton(part.id, `Actions for part ${part.id}`));
  return li;
}

/** The 24 px preview, or the placeholder glyph.
 *
 *  `thumb_key` is `null` for an unbuilt or failed part — there is nothing to
 *  render and the route would 404 — so the placeholder is the FIRST answer,
 *  not an error path. The `k` in the URL is what earns the immutable cache
 *  header, so a rebuild's new key is a new URL and the browser refetches
 *  without any cache busting of ours. */
function thumbEl(part) {
  const key = part.thumb_key || null;
  if (!key || thumbFailed.has(`${part.id}:${key}`)) return placeholderThumb();
  const img = document.createElement("img");
  img.className = "row-thumb";
  img.loading = "lazy";
  img.decoding = "async";
  img.width = 24;
  img.height = 24;
  img.alt = "";
  img.src = api.partThumbUrl(state.projectName, part.id, key);
  img.addEventListener("error", () => {
    thumbFailed.add(`${part.id}:${key}`);
    if (img.parentNode) img.parentNode.replaceChild(placeholderThumb(), img);
  }, {once: true});
  return img;
}

function placeholderThumb() {
  const span = document.createElement("span");
  span.className = "row-thumb row-thumb-empty";
  span.setAttribute("aria-hidden", "true");
  span.textContent = "▣";
  return span;
}

function menuButton(id, label) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "row-menu";
  btn.dataset.menuFor = id;
  btn.textContent = "⋯";
  btn.title = label;
  btn.setAttribute("aria-label", label);
  btn.setAttribute("aria-haspopup", "menu");
  return btn;
}

function instanceRowEl(row) {
  if (row.kind === "folder") return folderRowEl(row);
  const inst = row.raw || {id: row.id};
  const desc = row.instance || null;
  const li = document.createElement("li");
  li.className = "row";
  li.setAttribute("role", "treeitem");
  li.dataset.id = row.id;
  li.dataset.kind = row.kind;                 // "instance" | "member"
  if (row.kind === "member") li.classList.add("row-member");
  else li.draggable = true;
  const isSelected = state.mode === "assembly"
    && row.id === state.selectedInstance;
  if (isSelected) li.classList.add("selected");
  li.setAttribute("aria-selected", isSelected ? "true" : "false");

  const group = desc && desc.expandable;
  if (group) {
    li.classList.add("row-group");
    const open = expanded.has(desc.id);
    li.setAttribute("aria-expanded", open ? "true" : "false");
    const twist = document.createElement("span");
    twist.className = "row-twist";
    twist.dataset.twistFor = desc.id;
    twist.textContent = open ? "▾" : "▸";
    twist.setAttribute("aria-hidden", "true");
    li.appendChild(twist);
  }

  const swatch = document.createElement("span");
  swatch.className = "row-swatch";
  swatch.style.background = instanceColor(inst, row.colorIndex || 0);
  li.appendChild(swatch);

  const label = document.createElement("span");
  label.className = "row-label";
  label.textContent = row.id;
  li.appendChild(label);

  if (desc && desc.count != null) {
    const badge = document.createElement("span");
    badge.className = "row-badge";
    badge.textContent = desc.badge;                 // "×N"
    badge.title = `${desc.kind} pattern · ${desc.count} members`;
    li.appendChild(badge);
  }
  if (desc && desc.kind === "assembly") {
    const badge = document.createElement("span");
    badge.className = "row-badge";
    badge.textContent = "sub";
    badge.title = desc.source
      ? `sub-assembly · source ${desc.source} (read-only)`
      : "sub-assembly (read-only)";
    li.appendChild(badge);
    if (desc.source) {
      const open = document.createElement("button");
      open.type = "button";
      open.className = "row-open-src";
      open.dataset.openSource = desc.source;
      open.textContent = "open";
      open.title = `Open source project ${desc.source}`;
      open.setAttribute("aria-label", `Open source project ${desc.source}`);
      li.appendChild(open);
    }
  }
  if (!group && inst.part) {
    const ref = document.createElement("span");
    ref.className = "row-id";
    // `part@config` for a bound instance: two instances of one part showing
    // different geometry is the whole point of a binding, and the part id
    // alone cannot say which is which.
    ref.textContent = inst.config ? `${inst.part}@${inst.config}` : inst.part;
    if (inst.config) ref.title = `${inst.part}, configuration ${inst.config}`;
    li.appendChild(ref);
  }
  if (row.kind === "instance") {
    li.appendChild(menuButton(row.id, `Actions for instance ${row.id}`));
  }
  return li;
}

function dot(kind) {
  const d = document.createElement("span");
  d.className = `row-dot ${kind}`;
  return d;
}

// -------------------------------------------------------------- activation

function rowAt(view, el) {
  const li = el && el.closest ? el.closest("li.row") : null;
  if (!li || !view.listEl.contains(li)) return null;
  const index = Number(li.dataset.rowIndex);
  return Number.isInteger(index) ? {li, index, row: view.rows[index]} : null;
}

function onRowClick(view, e) {
  const hit = rowAt(view, e.target);
  if (!hit) return;
  const twist = e.target.closest ? e.target.closest("[data-twist-for]") : null;
  if (twist) {
    e.stopPropagation();
    const id = twist.dataset.twistFor;
    if (expanded.has(id)) expanded.delete(id);
    else expanded.add(id);
    render();
    return;
  }
  const menuBtn = e.target.closest ? e.target.closest(".row-menu") : null;
  if (menuBtn) {
    e.stopPropagation();
    const box = menuBtn.getBoundingClientRect();
    openRowMenu(view, hit, box.left, box.bottom + 2);
    return;
  }
  const src = e.target.closest ? e.target.closest("[data-open-source]") : null;
  if (src) {
    e.stopPropagation();
    if (actions.loadProject) actions.loadProject(src.dataset.openSource);
    return;
  }
  view.focusKey = rowKey(hit.row);
  activate(view, hit, {shift: e.shiftKey, meta: e.metaKey || e.ctrlKey});
}

/** Open a row (a click, or Enter). Folders toggle; parts select through the
 *  Finder table; instances select on stage. */
function activate(view, hit, mods) {
  const row = hit.row;
  if (!row) return;
  if (row.kind === "folder") {
    toggleCollapsed(row.path);
    return;
  }
  if (view.kind === "part") {
    const visible = view.rows.filter((r) => r.kind === "part").map((r) => r.id);
    const next = selectionAfter(state.selection, state.selectionAnchor,
                                visible, row.id, mods || {});
    setState({selection: next.selection, selectionAnchor: next.anchor});
    if (next.primary) actions.selectPart(next.primary);
    return;
  }
  const desc = row.instance;
  if (desc && desc.expandable) {
    const flattened = (state.assembly && state.assembly.instances) || [];
    const members = memberIdsOf(desc.id, flattened);
    if (members.length) actions.selectAssembly(members[0]);
    return;
  }
  actions.selectAssembly(row.id);
}

/** `Space` toggles a part's membership in the multi-selection without moving
 *  the primary — the keyboard twin of a Cmd-click. */
function toggleSelection(id) {
  const next = selectionAfter(state.selection, state.selectionAnchor,
                              [], id, {meta: true});
  setState({selection: next.selection, selectionAnchor: next.anchor});
}

// ---------------------------------------------------------------- keyboard

function onRowKey(view, e) {
  const hit = rowAt(view, e.target);
  if (!hit) return;
  const row = hit.row;
  switch (e.key) {
    case "ArrowDown":
      e.preventDefault();
      focusRowAt(view, hit.index + 1);
      return;
    case "ArrowUp":
      e.preventDefault();
      focusRowAt(view, hit.index - 1);
      return;
    case "Home":
      e.preventDefault();
      focusRowAt(view, 0);
      return;
    case "End":
      e.preventDefault();
      focusRowAt(view, view.rows.length - 1);
      return;
    case "ArrowRight":
      e.preventDefault();
      if (row.kind === "folder" && row.collapsed) toggleCollapsed(row.path, false);
      else if (row.kind === "folder") focusRowAt(view, hit.index + 1);
      else if (row.instance && row.instance.expandable
               && !expanded.has(row.instance.id)) {
        expanded.add(row.instance.id);
        render();
      }
      return;
    case "ArrowLeft":
      e.preventDefault();
      if (row.kind === "folder" && !row.collapsed) toggleCollapsed(row.path, true);
      else if (row.instance && row.instance.expandable
               && expanded.has(row.instance.id)) {
        expanded.delete(row.instance.id);
        render();
      } else focusRowAt(view, parentIndex(view, hit.index));
      return;
    case "Enter":
      e.preventDefault();
      activate(view, hit, {});
      return;
    case " ":
      if (view.kind !== "part" || row.kind !== "part") return;
      e.preventDefault();
      toggleSelection(row.id);
      return;
    case "ContextMenu": {
      e.preventDefault();
      const box = hit.li.getBoundingClientRect();
      openRowMenu(view, hit, box.left + 24, box.bottom);
      return;
    }
    default:
  }
}

/** The index of the row's enclosing folder, or the row itself when it has
 *  none (ArrowLeft at the root is a no-op, never a jump to row 0). */
function parentIndex(view, index) {
  const depth = (view.rows[index] || {}).depth || 0;
  if (depth === 0) return index;
  for (let i = index - 1; i >= 0; i--) {
    const row = view.rows[i];
    if (row.kind === "folder" && (row.depth || 0) === depth - 1) return i;
  }
  return index;
}

/** Move the roving tab stop to `index`, scrolling and repainting so the row
 *  exists in the DOM before it is focused. */
function focusRowAt(view, index) {
  if (!view.rows.length) return;
  const i = Math.max(0, Math.min(index, view.rows.length - 1));
  view.focusKey = rowKey(view.rows[i]);
  const el = view.listEl;
  const top = i * ROW_HEIGHT;
  if (top < el.scrollTop) el.scrollTop = top;
  else if (top + ROW_HEIGHT > el.scrollTop + el.clientHeight) {
    el.scrollTop = top + ROW_HEIGHT - el.clientHeight;
  }
  view.painted = null;
  paint(view);
  const li = el.querySelector(`li.row[data-row-index="${i}"]`);
  if (li) li.focus({preventScroll: true});
}

/** Put the keyboard back in the parts tree (the filter box's Esc). */
export function focusTree() {
  if (!partsView) return;
  paint(partsView);
  const li = partsView.listEl.querySelector('li.row[tabindex="0"]')
    || partsView.listEl.querySelector("li.row");
  // `preventScroll` here too: the tab stop is always inside the rendered
  // window, so the only thing the browser's scroll-into-view could do is nudge
  // the list by an overscan row the instant the user pressed Esc.
  if (li) li.focus({preventScroll: true});
}

/** Put the keyboard in the filter box and select what is there (`/`). */
export function focusFilter() {
  if (!filterEl) return;
  filterEl.focus();
  filterEl.select();
}

// ------------------------------------------------------------------ filter

function onFilterInput() {
  const q = filterEl.value;
  clearTimeout(searchTimer);
  searchSeq += 1;                  // an in-flight answer is now stale
  let free = false;
  try {
    free = hasFreeText(q);
  } catch {
    free = false;                  // the grammar refused; partRows() reports it
  }
  searchError = null;              // it was about the PREVIOUS query
  if (!free) {
    serverIds = null;
    snippets = {};
  }
  // Synchronous: the client owns every metadata source, so the list moves on
  // the keystroke and the server's script hits JOIN it 120 ms later.
  setState({treeFilter: q});
  if (free) searchTimer = setTimeout(() => runServerSearch(q), SEARCH_DEBOUNCE_MS);
}

function clearFilter() {
  clearTimeout(searchTimer);
  searchSeq += 1;
  serverIds = null;
  snippets = {};
  searchError = null;
  if (filterEl) filterEl.value = "";
  setState({treeFilter: ""});
}

async function runServerSearch(q) {
  const proj = state.projectName;
  if (!proj) return;
  const seq = ++searchSeq;
  let payload;
  try {
    payload = await api.searchParts(proj, q, 500);
  } catch (err) {
    if (seq !== searchSeq) return;
    searchError = err && err.error ? err.error.message : String(err);
    renderFilterMessage();
    return;
  }
  if (seq !== searchSeq || proj !== state.projectName) return;
  searchError = null;
  serverIds = (payload.parts || []).map((p) => p.id);
  snippets = {};
  for (const p of payload.parts || []) {
    if (p.snippet) snippets[p.id] = p.snippet;
  }
  render();
}

/** Paint whichever refusal is current. The client's parse error wins: it is
 *  about the text in the box right now, while a server error describes a call
 *  made for an earlier keystroke. */
function renderFilterMessage() {
  if (!filterMsgEl) return;
  const message = parseError || searchError || "";
  filterMsgEl.textContent = message;
  filterMsgEl.classList.toggle("hidden", !message);
  if (filterEl) {
    filterEl.classList.toggle("invalid", !!message);
    if (message) filterEl.setAttribute("aria-invalid", "true");
    else filterEl.removeAttribute("aria-invalid");
  }
}

// ------------------------------------------------------------ context menu

/** The ids a verb applies to: the whole selection when the row is part of it,
 *  otherwise just the row. Exactly the drag rule, and for the same reason —
 *  acting on a row you right-clicked outside your selection must not silently
 *  act on the selection instead. */
function targetIds(id) {
  return state.selection.has(id) && state.selection.size > 1
    ? [...state.selection]
    : [id];
}

function onRowContextMenu(view, e) {
  const hit = rowAt(view, e.target);
  if (!hit) return;
  e.preventDefault();
  view.focusKey = rowKey(hit.row);
  openRowMenu(view, hit, e.clientX, e.clientY);
}

function openRowMenu(view, hit, x, y) {
  const row = hit.row;
  if (!row) return;
  // Focus the row FIRST: `contextmenu.close()` restores focus to whatever was
  // focused when it opened, and a menu opened from an unfocused row would
  // strand the keyboard on <body> in a thousand-row list.
  view.painted = null;
  paint(view);
  const li = view.listEl.querySelector(`li.row[data-row-index="${hit.index}"]`);
  if (li) li.focus({preventScroll: true});
  const items = row.kind === "folder"
    ? folderMenuItems(view, row)
    : (view.kind === "part" ? partMenuItems(row) : instanceMenuItems(row));
  contextmenu.open({x, y, items, label: row.kind === "folder"
    ? `Folder ${row.path}` : `Row ${row.id}`});
}

function partMenuItems(row) {
  const ids = targetIds(row.id);
  const many = ids.length > 1;
  const suffix = many ? ` (${ids.length} parts)` : "";
  return [
    {id: "rename", label: "Rename…", disabled: many,
     run: () => renamePart(row.part)},
    {id: "tags", label: `Tags…${suffix}`, run: () => editTags(ids)},
    {id: "folder", label: `Move to folder…${suffix}`,
     run: () => moveParts(ids)},
    {id: "newfolder", label: "New folder…", run: () => newFolder()},
    {id: "export", label: `Export…${suffix}`, run: () => exportParts(ids)},
    {id: "delete", label: `Delete…${suffix}`, danger: true,
     run: () => deleteParts(ids)},
  ];
}

function folderMenuItems(view, row) {
  const ids = view.kind === "part"
    ? view.rows.filter((r) => r.kind === "part"
        && underFolder(r.part && r.part.folder, row.path)).map((r) => r.id)
    : [];
  return [
    {id: "toggle", label: row.collapsed ? "Expand" : "Collapse",
     run: () => toggleCollapsed(row.path)},
    {id: "select", label: `Select ${ids.length} part`
       + `${ids.length === 1 ? "" : "s"}`,
     disabled: !ids.length,
     run: () => setState({selection: new Set(ids),
                          selectionAnchor: ids[ids.length - 1] || null})},
    {id: "newfolder", label: "New folder…", run: () => newFolder(row.path)},
    {id: "move", label: `Move ${ids.length} part`
       + `${ids.length === 1 ? "" : "s"} to…`,
     disabled: !ids.length, run: () => moveParts(ids)},
  ];
}

function instanceMenuItems(row) {
  return [
    {id: "folder", label: "Move to folder…",
     run: () => moveInstance(row.id)},
    {id: "newfolder", label: "New folder…", run: () => newFolder()},
  ];
}

/** Is `folder` inside `path` (itself included)? Case-insensitive on segments,
 *  the way `navigation.py` matches folders. */
function underFolder(folder, path) {
  const have = String(folder || "").toLowerCase().split("/").filter(Boolean);
  const want = String(path || "").toLowerCase().split("/").filter(Boolean);
  return want.every((s, i) => have[i] === s);
}

// -------------------------------------------------------------- the verbs
//
// Exported, because the BULK BAR runs the same four: a row's context menu
// and the strip under the filter box are two affordances for one verb, and
// two copies of "what Tags… does" would drift the first time one of them
// learned about a new argument. `bulk.js` imports them and adds the one it
// owns (Material) plus the results dialog for a run with failed rows.

async function renamePart(part) {
  if (!part) return;
  const label = await dialogs.prompt({
    view: "rename-part",
    title: `Rename “${part.id}”`,
    label: "Label",
    value: part.label || part.id,
    help: "The display name. The part id and its script file never move.",
    okLabel: "Rename",
  });
  if (label == null) return;
  try {
    await api.updatePart(state.projectName, part.id, {label: label.trim()});
  } catch (err) {
    actions.toast(`Rename failed: ${err.message}`, "error");
    return;
  }
  actions.refreshProject();
}

export async function editTags(ids) {
  const parts = partsById(ids);
  const many = ids.length > 1;
  const current = many ? "" : (parts[0] && parts[0].tags || []).join(" ");
  const values = await dialogs.form({
    view: "part-tags",
    title: many ? `Tags for ${ids.length} parts` : `Tags for ${ids[0]}`,
    width: "narrow",
    fields: [
      ...(many ? [{
        name: "mode", label: "Apply", type: "select", value: "tag",
        options: [{value: "tag", label: "Add these tags"},
                  {value: "untag", label: "Remove these tags"}],
      }] : []),
      {name: "tags", label: "Tags", value: current,
       placeholder: "fastener m5 printed",
       help: "Space- or comma-separated. Lowercased on write; letters, "
             + "digits, _ . - only; 32 per part."
             + (many ? "" : " Empty clears every tag on this part.")},
    ],
    buttons: [{id: "cancel", label: "Cancel"},
              {id: "save", label: "Save", kind: "primary", submits: true}],
  });
  if (!values) return;
  const tags = splitTags(values.tags);
  if (many) {
    await runBulk(ids, values.mode === "untag" ? "untag" : "tag", {tags});
    return;
  }
  await writeMeta(ids[0], {tags});
}

export async function moveParts(ids) {
  const folder = await folderPrompt(ids.length > 1
    ? `Move ${ids.length} parts to…` : `Move ${ids[0]} to…`,
    currentFolder(ids));
  if (folder === undefined) return;
  const moving = movingIds(ids, folder);
  if (!moving.length) return;      // every one of them is already there
  if (moving.length > 1) {
    await runBulk(moving, "folder", {folder});
    return;
  }
  await writeMeta(moving[0], {folder});
}

async function moveInstance(id) {
  const inst = ((state.project && state.project.assembly
    && state.project.assembly.instances) || []).find((i) => i.id === id);
  const folder = await folderPrompt(`Move ${id} to…`,
                                    (inst && inst.folder) || "");
  if (folder === undefined) return;
  try {
    await api.patchInstance(state.projectName, id, {folder});
  } catch (err) {
    actions.toast(`Move failed: ${err.message}`, "error");
    return;
  }
  actions.refreshProject();
}

/** The folder dialog, shared by every "move to…" path. Resolves the folder
 *  (`null` for the project root) or `undefined` when cancelled — the two
 *  are different answers and `null` is a real one. */
async function folderPrompt(title, value) {
  const answer = await dialogs.prompt({
    view: "part-folder",
    title,
    label: "Folder",
    value: value || "",
    required: false,
    placeholder: "chassis/left side",
    help: "Slash-separated, up to 8 levels. Leave empty for the project root. "
          + "Folders are manifest metadata — the script file never moves.",
    validate: (v) => (!v || isFolderPath(String(v).trim())
      ? null
      : "1–8 segments of letters, digits, spaces, _ . - each starting with a "
        + "letter or digit"),
    okLabel: "Move",
  });
  if (answer == null) return undefined;
  const folder = answer.trim();
  return folder === "" ? null : folder;
}

async function newFolder(parent) {
  const answer = await dialogs.prompt({
    view: "new-folder",
    title: "New folder",
    label: "Folder",
    value: parent ? `${parent}/` : "",
    placeholder: "chassis/left side",
    help: "Folders are implicit: this one is remembered in this browser until "
          + "a part is filed into it, and is dropped on reload if it is still "
          + "empty.",
    validate: (v) => (isFolderPath(String(v || "").trim())
      ? null
      : "1–8 segments of letters, digits, spaces, _ . - each starting with a "
        + "letter or digit"),
    okLabel: "Create",
  });
  if (answer == null) return;
  const path = answer.trim();
  if (!isFolderPath(path)) return;
  syncTreeState();
  if (!treeState.emptyFolders.some((p) => p.toLowerCase() === path.toLowerCase())) {
    treeState.emptyFolders = [...treeState.emptyFolders, path];
    saveTreeState();
  }
  render();
}

export async function exportParts(ids) {
  const values = await dialogs.form({
    view: "part-export",
    title: ids.length > 1 ? `Export ${ids.length} parts` : `Export ${ids[0]}`,
    width: "narrow",
    fields: [{name: "format", label: "Format", type: "select", value: "step",
              options: [{value: "step", label: "STEP (.step)"},
                        {value: "stl", label: "STL (.stl)"},
                        {value: "3mf", label: "3MF (.3mf)"}]}],
    buttons: [{id: "cancel", label: "Cancel"},
              {id: "export", label: "Export", kind: "primary", submits: true}],
  });
  if (!values) return;
  await runBulk(ids, "export", {format: values.format});
}

export async function deleteParts(ids) {
  if (ids.length === 1) {
    // One part keeps the existing dialog: it names the assembly instances the
    // delete takes with it, which is the blast radius that matters.
    await actions.deletePart(ids[0]);
    return;
  }
  const ok = await dialogs.confirm({
    view: "bulk-delete-parts",
    title: `Delete ${ids.length} parts?`,
    body: `Deletes ${ids.join(", ")} and their script files.`,
    note: "Assembly instances of these parts refuse the delete unless you "
          + "confirm again — this is one undoable step either way.",
    danger: true,
    confirmLabel: `Delete ${ids.length} parts`,
  });
  if (!ok) return;
  await runBulk(ids, "delete", {});
}

// ------------------------------------------------------------- the writers

function partsById(ids) {
  const parts = (state.project && state.project.parts) || [];
  const wanted = new Set(ids);
  return parts.filter((p) => wanted.has(p.id));
}

/** Two folder paths, compared the way `navigation.py` compares them:
 *  case-insensitively, with `null` / `""` both meaning the project root. */
export function sameFolder(a, b) {
  return String(a == null ? "" : a).toLowerCase()
    === String(b == null ? "" : b).toLowerCase();
}

/** The ids that would actually MOVE. Dropping a part on a row in its own
 *  folder is the commonest miss in a drag, and writing it anyway costs a
 *  manifest RMW, a `project_changed`, and — the part that matters — an UNDO
 *  STEP the user then has to press ⌘Z through to reach the edit they meant. */
function movingIds(ids, folder) {
  const byId = new Map(((state.project && state.project.parts) || [])
    .map((part) => [part.id, part]));
  return ids.filter((id) => {
    const part = byId.get(id);
    return !part || !sameFolder(part.folder, folder);
  });
}

function currentFolder(ids) {
  const parts = partsById(ids);
  const first = parts.length ? (parts[0].folder || "") : "";
  return parts.every((p) => (p.folder || "") === first) ? first : "";
}

/** `"a, b  c"` → `["a", "b", "c"]`. The server normalizes (lowercase, dedupe);
 *  this only has to split. */
export function splitTags(text) {
  return String(text == null ? "" : text)
    .split(/[\s,]+/)
    .map((t) => t.trim())
    .filter(Boolean);
}

/** `set_part_meta` for one part, patched into `state.project` on the way back
 *  so the row re-files with no refetch (the tool answers with the stored
 *  values, which is the only place the browser can learn them). */
async function writeMeta(id, meta) {
  let res;
  try {
    res = await api.setPartMeta(state.projectName, id, meta);
  } catch (err) {
    actions.toast(`Could not update ${id}: ${err.message}`, "error");
    return;
  }
  if (res && res.error) {
    actions.toast(`Could not update ${id}: ${res.error.message}`, "error");
    return;
  }
  actions.patchPartsMeta([id], {folder: res.folder, tags: res.tags});
}

/** One `bulk_part_op` end to end: the call, its refusal, its per-item
 *  failures and its toast. Exported because the bulk bar runs the SAME path —
 *  one implementation of "what a bulk verb does", so the context menu and the
 *  strip cannot drift. */
export async function runBulk(ids, op, args) {
  const result = await runBulkOp(ids, op, args);
  if (result) reportBulk(result, op, args);
  return result;
}

async function runBulkOp(ids, op, args) {
  let result;
  try {
    result = await api.bulkPartOp(state.projectName, ids, op, args);
  } catch (err) {
    actions.toast(`${op} failed: ${err.message}`, "error");
    return null;
  }
  if (result && result.error) {
    actions.toast(`${op} refused: ${result.error.message}`, "error");
    return null;
  }
  return result;
}

/** Report one bulk result. A clean run is one toast carrying the undo label;
 *  anything with a failed row goes to the results dialog `bulk.js` owns (a
 *  toast per item would be six toasts for a six-part selection). */
export function reportBulk(result, op, args) {
  const failed = (result.results || []).filter((r) => r && r.ok === false);
  if (op !== "export") {
    // The write already landed; patch what we know rather than refetch.
    const applied = (result.results || []).filter((r) => r && r.ok !== false)
      .map((r) => r.id);
    if (op === "folder" && applied.length) {
      actions.patchPartsMeta(applied, {folder: (args && args.folder) || null});
    } else if (applied.length) {
      actions.refreshProject();
    }
  }
  if (failed.length) {
    if (onBulkFailures) onBulkFailures(result);
    return;
  }
  const label = result.undo_label || `${op} ×${result.applied}`;
  actions.toast(label.charAt(0).toUpperCase() + label.slice(1));
}

// `bulk.js` installs the results dialog here rather than being imported: this
// module is the one every panel already depends on, and importing the bulk bar
// from the tree (which the bulk bar reads) would close a cycle.
let onBulkFailures = null;

/** Install the "some rows failed" reporter (`bulk.js`'s results dialog). */
export function setBulkFailureReporter(fn) {
  onBulkFailures = typeof fn === "function" ? fn : null;
}

// ------------------------------------------------------------ drag and drop

function onDragStart(view, e) {
  const hit = rowAt(view, e.target);
  if (!hit || !hit.row || hit.row.kind === "folder"
      || hit.row.kind === "member") {
    e.preventDefault();
    return;
  }
  const id = hit.row.id;
  const ids = view.kind === "part" ? targetIds(id) : [id];
  dragging = {kind: view.kind, ids};
  e.dataTransfer.effectAllowed = "move";
  try {
    e.dataTransfer.setData("text/plain", ids.join("\n"));
  } catch {
    /* Safari refuses setData outside a user gesture in some paths */
  }
  hit.li.classList.add("dragging");
  if (view.kind === "part" && rootDropEl) rootDropEl.classList.remove("hidden");
}

function onDragEnd() {
  dragging = null;
  if (rootDropEl) {
    rootDropEl.classList.add("hidden");
    rootDropEl.classList.remove("drop-target");
  }
  for (const el of document.querySelectorAll(".row.dragging, .row.drop-target")) {
    el.classList.remove("dragging", "drop-target");
  }
}

/** The folder a drop on `row` means. A folder row is itself; a part or
 *  instance row is the folder that row is filed under — "put it where that
 *  one is", which is the gesture people already make in Finder. */
function dropFolderOf(row) {
  if (!row) return undefined;
  if (row.kind === "folder") return row.path;
  if (row.kind === "part") return (row.part && row.part.folder) || null;
  if (row.kind === "instance") return (row.raw && row.raw.folder) || null;
  return undefined;
}

function onDragOver(view, e) {
  if (!dragging || dragging.kind !== view.kind) return;
  const hit = rowAt(view, e.target);
  const folder = hit ? dropFolderOf(hit.row) : undefined;
  if (folder === undefined) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  for (const el of view.listEl.querySelectorAll(".row.drop-target")) {
    if (el !== hit.li) el.classList.remove("drop-target");
  }
  hit.li.classList.add("drop-target");
}

function onDragLeave(view, e) {
  const hit = rowAt(view, e.target);
  if (hit) hit.li.classList.remove("drop-target");
}

function onDrop(view, e) {
  if (!dragging || dragging.kind !== view.kind) return;
  const hit = rowAt(view, e.target);
  const folder = hit ? dropFolderOf(hit.row) : undefined;
  if (folder === undefined) return;
  e.preventDefault();
  const {kind, ids} = dragging;
  onDragEnd();
  moveTo(kind, ids, folder);
}

function wireRootDrop() {
  if (!rootDropEl) return;
  rootDropEl.addEventListener("dragover", (e) => {
    if (!dragging || dragging.kind !== "part") return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    rootDropEl.classList.add("drop-target");
  });
  rootDropEl.addEventListener("dragleave",
    () => rootDropEl.classList.remove("drop-target"));
  rootDropEl.addEventListener("drop", (e) => {
    if (!dragging || dragging.kind !== "part") return;
    e.preventDefault();
    const ids = dragging.ids;
    onDragEnd();
    moveTo("part", ids, null);
  });
}

/** Re-file a drag's payload. One part is `set_part_meta`; several are one
 *  `bulk_part_op folder` — which is one manifest write and one undo step,
 *  the whole point of the bulk tool. */
function moveTo(kind, ids, folder) {
  if (!ids.length) return;
  if (kind === "instance") {
    const instances = (state.project && state.project.assembly
      && state.project.assembly.instances) || [];
    const byId = new Map(instances.map((inst) => [inst.id, inst]));
    const moving = ids.filter((id) => {
      const inst = byId.get(id);
      return !inst || !sameFolder(inst.folder, folder);
    });
    if (!moving.length) return;
    Promise.all(moving.map((id) =>
      api.patchInstance(state.projectName, id, {folder})))
      .then(() => actions.refreshProject())
      .catch((err) => actions.toast(`Move failed: ${err.message}`, "error"));
    return;
  }
  const moving = movingIds(ids, folder);
  if (!moving.length) return;      // dropped where they already are
  if (moving.length === 1) {
    writeMeta(moving[0], {folder});
    return;
  }
  runBulk(moving, "folder", {folder});
}

// ------------------------------------------------------------- presence bits

// state.presence is read directly rather than through presence.js: that module
// already imports INSTANCE_PALETTE from here, and closing the cycle for three
// one-line lookups would be a real fragility for no gain.

function claimOn(partId) {
  const claims = (state.presence && state.presence.claims) || {};
  const claim = claims[partId];
  return claim && claim.holder !== clientId ? claim : null;
}

/** Other clients whose focus names this part. */
function othersOn(partId) {
  const clients = (state.presence && state.presence.clients) || [];
  return clients.filter(
    (c) => c.id !== clientId && c.focus && c.focus.part_id === partId
  );
}

function labelOf(id) {
  const clients = (state.presence && state.presence.clients) || [];
  const found = clients.find((c) => c.id === id);
  return (found && found.label) || id || "someone";
}
