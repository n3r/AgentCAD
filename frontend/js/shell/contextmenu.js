// PRD-026 shell primitive, built for PRD-027 — the right-click / `⋯` menu.
//
// A shell primitive rather than a popup inside `tree.js` (ruling 11): the tree
// needs one, the dashboard cards want one, and three hand-rolled popups is
// three sets of keyboard handling to get wrong. `open({x, y, items})` resolves
// when the menu closes, so a caller reads like a dialog:
//
//     await contextmenu.open({x: e.clientX, y: e.clientY, label: part.id,
//                             items: [{id: "rename", label: "Rename…", run}]});
//
// Three things are load-bearing:
//
//   * **It is NOT modal.** A context menu is a transient surface, not a
//     decision the app is waiting on: `dialogs.isModalOpen()` must stay false
//     while it is up, or every global shortcut dies for as long as a menu is
//     open somewhere on screen.
//   * **It still owns Esc while it is open**, which is the reason the modal
//     stack exists — an Esc must not reach past the topmost surface. The
//     listener is installed on `window` in the CAPTURE phase, which runs
//     BEFORE `dialogs.js`'s document-level capture listener (capture flows
//     window → document → …), so the menu wins the key for exactly as long as
//     it is open and gives it straight back when it closes. See the note on
//     the stack below.
//   * **`markup(items)` is a pure string function**, exported and tested: the
//     static accessibility pass (`role="menu"`, a `role="menuitem"` per row,
//     `aria-disabled` rather than a vanished row, an escaped label) is graded
//     in node, without a browser (`dialogs_model.markup`'s precedent).
//
// A note on the overlay stack, reviewed and deliberately kept. The obvious
// registration is a NON-modal entry on `dialogs.js`'s stack, so Esc routing is
// one rule in one place. `attachLegacy` — the only adoption seam — hard-codes
// `modal: true` (dialogs.js:408), which would make `isModalOpen()` true and is
// the one thing this menu must not do; it would also stamp agent attribution
// and emit a `dialog_opened` event for every right-click, neither of which is
// true of a context menu. So the menu owns Esc itself, and there is exactly
// ONE semantic divergence from the stack to know about:
//
//   `escOwner` gives a NON-MODAL entry the Esc key only while focus is inside
//   it, so a non-modal panel across the screen cannot swallow the sketcher's
//   Esc. This menu takes Esc UNCONDITIONALLY while it is open — it opens
//   focused (see the caller contract below) and dismisses on any outside
//   click, scroll or resize, so "open but focus is elsewhere" is a window of a
//   few milliseconds, and inside that window dismissing the menu is what Esc
//   should do anyway. It is unconditional rather than focus-tested because a
//   verb's `run` can move focus (a dialog, a toast action) while the menu is
//   still tearing down, and an Esc that fell through to the layer below then
//   would close the wrong thing.
//
// **Caller contract:** focus the row (or the `⋯` button) that opens the menu
// BEFORE calling `open()`. `close()` restores focus to whatever was focused at
// open time, so a menu opened from an unfocused row restores focus to
// `<body>` and the keyboard user loses their place in a 1 000-row tree. A
// right-click handler should `row.focus()` first; the `⋯` button already has
// focus from its own click.
//
// Node-importable: nothing touches `document`/`window` until `init()`/`open()`.

import { escapeHtml } from "./dialogs_model.js";
import { toast } from "./toast.js";

// The one open menu, or null. A second `open()` closes the first — two context
// menus on screen is never a state anybody asked for, and the alternative
// (refusing the second) loses the click the user actually made.
let current = null;
let hostEl = null;

/** Create/adopt `#contextmenu-host`. Optional: `open()` calls it. */
export function init(el) {
  if (typeof document === "undefined") return null;
  hostEl = el || document.getElementById("contextmenu-host");
  if (!hostEl) {
    hostEl = document.createElement("div");
    hostEl.id = "contextmenu-host";
    document.body.appendChild(hostEl);
  }
  return hostEl;
}

/** The menu's markup: a `<ul role="menu">` of `<li role="menuitem">`.
 *
 *  `tabindex="-1"` on every row and none on the list: focus is ROVING, moved
 *  by the arrow keys, so Tab leaves the menu instead of walking it (the ARIA
 *  menu pattern, and what `menu.js`'s `.menu-item` rows already do).
 *
 *  A disabled row is `aria-disabled` and stays VISIBLE — a verb that vanishes
 *  when it does not apply teaches the user it does not exist. It is skipped by
 *  the arrow keys and by activation.
 *
 *  Labels are escaped: they carry part ids and labels, which humans type.
 */
export function markup(items, label) {
  const rows = (items || [])
    .filter((it) => it && it.id != null)
    .map((it) => {
      const cls = it.danger ? "menu-item danger" : "menu-item";
      const disabled = it.disabled ? ' aria-disabled="true"' : "";
      const text = it.label == null ? it.id : it.label;
      return `<li class="${cls}" role="menuitem" tabindex="-1" `
        + `data-id="${escapeHtml(it.id)}"${disabled}>`
        + `<span>${escapeHtml(text)}</span></li>`;
    })
    .join("");
  return `<ul class="menu ctx-menu" role="menu" `
    + `aria-label="${escapeHtml(label || "Actions")}">${rows}</ul>`;
}

/** Is a context menu open right now? */
export function isOpen() {
  return current !== null;
}

/** Open a context menu at viewport coordinates `(x, y)`.
 *
 *  `items: [{id, label, danger?, disabled?, run}]`; `label` names the menu for
 *  a screen reader ("Actions" by default). Resolves when the menu closes —
 *  after the chosen item's `run` has been started, whether it was chosen or
 *  the menu was dismissed, because "the menu is gone" is the only thing a
 *  caller can act on either way.
 */
export function open(spec) {
  const s = spec || {};
  if (typeof document === "undefined") return Promise.resolve();
  close();
  const host = init(hostEl);
  const holder = document.createElement("div");
  holder.innerHTML = markup(s.items, s.label);
  const el = holder.firstElementChild;
  // `.menu` is `position: absolute` under a `.menu-wrap`; a context menu is
  // anchored to the POINTER, so it is fixed to the viewport. Inline, because
  // the primitive has to be correct before any stylesheet says `.ctx-menu`.
  el.style.position = "fixed";
  el.style.listStyle = "none";
  el.style.margin = "0";
  host.appendChild(el);
  const entry = {el, items: s.items || [], resolve: null,
                 restore: document.activeElement};
  const done = new Promise((resolve) => { entry.resolve = resolve; });
  current = entry;
  place(el, s.x, s.y);
  entry.onKey = (e) => onKey(e, entry);
  entry.onPointer = (e) => { if (!el.contains(e.target)) close(); };
  entry.onScroll = (e) => { if (!el.contains(e.target)) close(); };
  entry.onClick = (e) => {
    const row = e.target.closest ? e.target.closest("[data-id]") : null;
    if (!row || !el.contains(row)) return;
    e.preventDefault();
    e.stopPropagation();
    activate(entry, row.dataset.id);
  };
  // `window` capture beats `dialogs.js`'s document capture — see the header.
  window.addEventListener("keydown", entry.onKey, true);
  document.addEventListener("mousedown", entry.onPointer, true);
  document.addEventListener("contextmenu", entry.onPointer, true);
  // Capture, so a scroll INSIDE the virtualized tree closes it too: the menu
  // is pinned to the viewport and its row would scroll out from under it.
  window.addEventListener("scroll", entry.onScroll, true);
  window.addEventListener("resize", entry.onScroll, true);
  el.addEventListener("click", entry.onClick);
  focusRow(rowsOf(el)[0] || null);
  return done;
}

/** Close the open menu (if any) and resolve its promise. Idempotent. */
export function close() {
  const entry = current;
  if (!entry) return;
  current = null;
  window.removeEventListener("keydown", entry.onKey, true);
  document.removeEventListener("mousedown", entry.onPointer, true);
  document.removeEventListener("contextmenu", entry.onPointer, true);
  window.removeEventListener("scroll", entry.onScroll, true);
  window.removeEventListener("resize", entry.onScroll, true);
  if (entry.el.parentNode) entry.el.parentNode.removeChild(entry.el);
  const restore = entry.restore;
  if (restore && typeof restore.focus === "function" && restore.isConnected) {
    restore.focus();
  }
  entry.resolve();
}

// ------------------------------------------------------------------ internals

/** Put the menu fully inside the viewport, flipping when it does not fit.
 *
 *  Flip rather than clamp: a menu whose top-left is pinned to the pointer and
 *  whose bottom is cut off hides its last verbs (which are the destructive
 *  ones), while flipping it above the pointer shows all of them. Clamping is
 *  the fallback for a menu taller than the viewport itself.
 */
function place(el, x, y) {
  const box = el.getBoundingClientRect();
  const vw = window.innerWidth || box.width;
  const vh = window.innerHeight || box.height;
  const px = Number.isFinite(x) ? x : 0;
  const py = Number.isFinite(y) ? y : 0;
  const left = px + box.width > vw ? Math.max(0, px - box.width) : px;
  const top = py + box.height > vh ? Math.max(0, py - box.height) : py;
  el.style.left = `${Math.max(0, Math.min(left, Math.max(0, vw - box.width)))}px`;
  el.style.top = `${Math.max(0, Math.min(top, Math.max(0, vh - box.height)))}px`;
}

/** The rows the keyboard can reach — a disabled row is visible but skipped. */
function rowsOf(el) {
  return [...el.querySelectorAll('[role="menuitem"]')]
    .filter((row) => row.getAttribute("aria-disabled") !== "true");
}

function focusRow(row) {
  if (row && typeof row.focus === "function") row.focus();
}

/** Report a verb that failed. A toast, because the menu is GONE by then —
 *  there is no dialog left to put the message in (spec §3's rule is that a
 *  refusal belongs in the dialog that asked; here nothing asked). `toast()` is
 *  a no-op without a document, so this is safe in node and before boot. */
function reportFailure(err, item) {
  console.error("context menu item failed", item && item.id, err);
  const what = (item && (item.label || item.id)) || "That action";
  toast(`${what} failed: ${(err && err.message) || err}`, "error");
}

/** Run one item's verb so that a failure is REPORTED, not lost.
 *
 *  Every verb this menu carries is async (`Rename…`, `Delete…`, `Export…` all
 *  await a dialog and then a tool call), so a bare `try/catch` catches only
 *  the vanishingly rare synchronous throw and lets every real failure become
 *  an unhandled rejection — a menu item that silently does nothing, with the
 *  reason in a console the user never opens. Both paths are covered here:
 *  the `try` for a synchronous throw, the `.catch` for a rejected promise.
 *
 *  Returns the promise (or `null`), so a caller — or a test — can await the
 *  settled verb; nothing in the menu itself needs to, because the menu has
 *  already closed.
 */
function runItem(item, report = reportFailure) {
  if (!item || typeof item.run !== "function") return null;
  let result;
  try {
    result = item.run();
  } catch (err) {
    report(err, item);
    return null;
  }
  return Promise.resolve(result).catch((err) => { report(err, item); });
}

function activate(entry, id) {
  const item = entry.items.find((it) => it && String(it.id) === String(id));
  if (!item || item.disabled) return;
  // Closed BEFORE the verb runs: `close()` restores focus to the row that
  // opened the menu, and a dialog the verb opens must capture ITS restore
  // target after that, not before (or Esc on the dialog returns focus to a
  // menu row that no longer exists).
  close();
  runItem(item);
}

function onKey(e, entry) {
  const el = entry.el;
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    close();
    return;
  }
  if (e.key === "Tab") {
    // WAI-ARIA menu pattern: Tab closes the menu and moves on. NOT
    // `preventDefault`ed — `close()` restores focus to the row that opened it
    // and the browser's own Tab then walks forward from there, which is where
    // the user expects to land.
    close();
    return;
  }
  // Every other key belongs to the menu only while focus is inside it — a
  // menu that swallowed the arrow keys of a list behind it would be worse
  // than no menu.
  if (!el.contains(e.target)) return;
  const rows = rowsOf(el);
  if (!rows.length) return;
  const index = rows.indexOf(document.activeElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const step = e.key === "ArrowDown" ? 1 : -1;
    focusRow(rows[(index + step + rows.length) % rows.length]);
    return;
  }
  if (e.key === "Home" || e.key === "End") {
    e.preventDefault();
    focusRow(e.key === "Home" ? rows[0] : rows[rows.length - 1]);
    return;
  }
  if (e.key === "Enter" || e.key === " ") {
    if (index < 0) return;
    e.preventDefault();
    e.stopPropagation();
    activate(entry, rows[index].dataset.id);
  }
}

// Test seam — the node round-trip imports this and nothing else.
export const __contextMenu__ = {markup, open, close, isOpen, init,
                                runItem, reportFailure, onKey};
