// PRD-026 shell — the menu bar's DOM half, and the shared `.menu-wrap`
// interaction primitive it and the pre-existing project/branch/export
// dropdowns both run on.
//
// `attach(wrapEl)` absorbs `main.js`'s old `setupMenus()` (outside-click
// close, Esc close, ↑/↓ roving inside the open menu) and widens it two ways:
// the shared document-level listeners now query `.menu-wrap` LIVE at event
// time rather than once at boot (the "a menu inserted later gets no
// handling" caveat the old code carried in a comment — fixed by construction
// rather than documented as a limitation), and a row with `data-action`
// now runs it through the action registry (`Enter`/`Space` reach it for
// free: they are `<button>` elements, so the browser's own activation
// already fires `click`). `←/→` moves focus between sibling `.menu-wrap`s
// that share a `[role="menubar"]` ancestor — a no-op for the standalone
// project/branch/export wraps, which have none.
//
// `init({actions, host})` is the menu bar itself: renders `menu_model.tree`
// into `host` (`#menubar`) via `menu_model.markup`, wires every generated
// wrap through `attach`, and re-renders on `actions.onChange` (a new/changed
// action) and every time a menu is opened (so a stale `enabled`/`when` never
// shows — spec §4).

import { tree, markup } from "./menu_model.js";

// scope -> Set<Element>, but we never need to look them up by scope; a flat
// Set is enough. Entries whose element has been removed from the document
// (an old menubar render's nodes) are filtered live rather than pruned
// eagerly — cheap, and avoids the module having to know when a re-render
// definitely happened.
const wraps = new Set();
let globalWired = false;
// Which top-level menubar menu is open, tracked across re-renders. Declared
// up here (rather than down by `render()`/`toggle()`, where it's read) so
// `closeWrap` — called from three places that are NOT `toggle()` — can see
// and clear it without a forward reference.
let openMenuName = null;

// Filters to connected elements AND prunes disconnected ones out of `wraps`
// as it goes, so the Set stays bounded by "wraps currently in the document"
// rather than growing by one render's worth of nodes on every open/close
// (`render()` replaces `hostEl.innerHTML` on every `toggle()` call, which is
// every single open/close of a menubar dropdown).
function connectedWraps() {
  const out = [];
  for (const w of wraps) {
    if (w.isConnected) out.push(w);
    else wraps.delete(w);
  }
  return out;
}

function menuOf(wrap) {
  return wrap.querySelector(":scope > .menu, :scope > [role='menu']");
}

function buttonOf(wrap) {
  return wrap.querySelector(":scope > button[aria-haspopup]");
}

function isHidden(wrap) {
  const menu = menuOf(wrap);
  return !menu || menu.classList.contains("hidden");
}

/** Close one wrap's dropdown. This is the ONLY thing that closes a wrap
 *  outside of `toggle()` (outside-click, Esc, and running a row all go
 *  through here), so it is also the one place that must clear the menu
 *  bar's `openMenuName` tracking when the wrap it just closed is the one
 *  `toggle()` thinks is still open — otherwise the next click on that same
 *  trigger computes `opening = openMenuName !== name` as `false` (the stale
 *  name still matches) and silently no-ops instead of reopening. */
function closeWrap(wrap) {
  const menu = menuOf(wrap);
  if (!menu || menu.classList.contains("hidden")) return;
  menu.classList.add("hidden");
  const btn = buttonOf(wrap);
  if (btn) btn.setAttribute("aria-expanded", "false");
  if (wrap.dataset.menu && wrap.dataset.menu === openMenuName) {
    openMenuName = null;
  }
}

function installGlobal() {
  if (globalWired || typeof document === "undefined") return;
  globalWired = true;
  document.addEventListener("click", (e) => {
    for (const wrap of connectedWraps()) {
      if (!wrap.contains(e.target)) closeWrap(wrap);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      for (const wrap of connectedWraps()) closeWrap(wrap);
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const openWrap = connectedWraps().find((w) => !isHidden(w));
    if (!openWrap) return;
    const menu = menuOf(openWrap);
    const items = [...menu.querySelectorAll(".menu-item, [role='menuitem']")]
      .filter((i) => !i.disabled && i.getAttribute("aria-disabled") !== "true");
    if (!items.length) return;
    e.preventDefault();
    const idx = items.indexOf(document.activeElement);
    const next = e.key === "ArrowDown"
      ? items[(idx + 1) % items.length]
      : items[(idx - 1 + items.length) % items.length];
    next.focus();
  });
}

/** Wire one `.menu-wrap`'s shared behaviour: outside-click/Esc/↑↓ close+rove
 *  (installed once, globally, on first call — see `installGlobal`), a
 *  `data-action` row running through the action registry on click, and
 *  `←/→` moving between sibling menus of a shared `[role="menubar"]`.
 *  Idempotent per element (a re-render's fresh nodes need a fresh call, but
 *  calling it twice on the same node is harmless). */
export function attach(wrapEl) {
  if (!wrapEl) return wrapEl;
  if (!wraps.has(wrapEl)) {
    wraps.add(wrapEl);
    wrapEl.addEventListener("click", (e) => {
      const item = e.target.closest("[data-action]");
      if (!item || !wrapEl.contains(item)) return;
      if (item.getAttribute("aria-disabled") === "true") return;
      e.stopPropagation();
      closeWrap(wrapEl);
      if (actionsRef) {
        actionsRef.run(item.dataset.action, actionsRef.context(), { source: "menu" });
      }
    });
    wrapEl.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const bar = wrapEl.closest('[role="menubar"]');
      if (!bar) return;
      // Only the generated menubar wraps (`data-menu`) ever live inside a
      // `[role="menubar"]`, so this branch is theirs alone — the standalone
      // project/branch/export wraps have no menubar ancestor and never reach
      // here (the lookup above returns null for them).
      const siblings = [...bar.querySelectorAll(":scope > .menu-wrap[data-menu]")];
      const idx = siblings.indexOf(wrapEl);
      if (idx === -1 || siblings.length < 2) return;
      e.preventDefault();
      const wasOpen = !isHidden(wrapEl);
      const next = siblings[(idx + (e.key === "ArrowRight" ? 1 : -1) + siblings.length)
        % siblings.length];
      if (wasOpen) {
        // Reopening through `toggle` re-renders (so the destination menu's
        // `enabled`/`when` are fresh — the same guarantee a click gets) and
        // focuses the new trigger itself.
        toggle(next.dataset.menu);
      } else {
        closeWrap(wrapEl);
        buttonOf(next)?.focus();
      }
    });
  }
  installGlobal();
  return wrapEl;
}

function openWrap(wrap) {
  const menu = menuOf(wrap);
  const btn = buttonOf(wrap);
  if (!menu || !btn) return;
  menu.classList.remove("hidden");
  btn.setAttribute("aria-expanded", "true");
  btn.focus();
}

// ---------------------------------------------------------------- the bar

let hostEl = null;
let actionsRef = null;
let platform = "";

export function init(deps) {
  const d = deps || {};
  actionsRef = d.actions || null;
  hostEl = typeof d.host === "string"
    ? (typeof document === "undefined" ? null : document.getElementById(d.host))
    : d.host || null;
  platform = typeof navigator === "undefined" ? "" : (navigator.platform || "");
  render();
  if (actionsRef) actionsRef.onChange(() => render());
  return hostEl;
}

function render() {
  if (!hostEl || !actionsRef) return;
  const list = actionsRef.list(actionsRef.context());
  hostEl.innerHTML = markup(tree(list, platform));
  for (const wrap of hostEl.querySelectorAll(".menu-wrap")) {
    attach(wrap);
    const name = wrap.dataset.menu;
    const btn = buttonOf(wrap);
    if (!btn) continue;
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggle(name);
    });
    if (name === openMenuName) openWrap(wrap);
  }
}

/** Open/close one top-level menu by name, refreshing its `enabled`/`when`
 *  first (spec §4: "re-render on `actions.onChange` and on open"). */
function toggle(name) {
  const opening = openMenuName !== name;
  openMenuName = opening ? name : null;
  render();
}

// Test seam: which menu `toggle()`/`closeWrap()` currently believe is open,
// for a node test to assert the C1 fix (closeWrap resets this on outside-
// click/Esc/run-a-row, not only on toggle()) without a real browser.
function __openMenuName__() {
  return openMenuName;
}

export const __menuBar__ = { attach, init, __openMenuName__ };
