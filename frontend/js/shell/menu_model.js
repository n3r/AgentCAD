// PRD-026 shell — the menu bar's pure half: action list -> menu tree -> markup.
// PURE: no DOM, no imports beyond shortcuts_model (also pure), runs in node
// (tests/test_frontend_shell.py).
//
// The tree is built from `actions.list(ctx)` (already `when`-filtered, each
// entry carrying a boolean `enabled`), grouped by the menu name in its
// `menu: "file/10"` field. Menus render in a FIXED order regardless of
// registration order — File, Edit, View, Model, Help — and an empty menu is
// omitted rather than rendered as a bare button with nothing under it.

import { label as labelFor } from "./shortcuts_model.js";

export const MENU_ORDER = ["file", "edit", "view", "model", "help"];

/** `actionList` is the output of `actions.list(ctx)` — entries with a `menu`
 *  field are grouped by menu name, sorted by the numeric part of `menu`
 *  ("file/10" -> 10), and a separator is drawn before an item whose order is
 *  >= 10 past the previous item's (the "gap of >= 10 draws a separator"
 *  rule) — never before the first item of a menu. Entries with no `menu`
 *  field, or a `menu` that does not parse as `"<name>/<number>"`, are
 *  silently excluded: the menu bar is a curated subset of the registry, not
 *  every action. */
export function tree(actionList, platform) {
  const byMenu = new Map();
  for (const spec of actionList || []) {
    const parsed = parseMenuField(spec && spec.menu);
    if (!parsed) continue;
    if (!byMenu.has(parsed.name)) byMenu.set(parsed.name, []);
    byMenu.get(parsed.name).push({ spec, order: parsed.order });
  }
  const out = [];
  for (const name of MENU_ORDER) {
    const entries = byMenu.get(name);
    if (!entries || !entries.length) continue;
    entries.sort((a, b) => a.order - b.order);
    const items = [];
    let prevOrder = null;
    for (const { spec, order } of entries) {
      items.push({
        id: spec.id,
        title: spec.title,
        shortcutLabel: firstShortcutLabel(spec.shortcut, platform),
        danger: !!spec.danger,
        enabled: spec.enabled !== false,
        separatorBefore: prevOrder !== null && order - prevOrder >= 10,
      });
      prevOrder = order;
    }
    out.push({ menu: name, label: capitalize(name), items });
  }
  return out;
}

function parseMenuField(value) {
  if (typeof value !== "string") return null;
  const i = value.indexOf("/");
  if (i < 1) return null;
  const name = value.slice(0, i);
  const order = Number(value.slice(i + 1));
  if (!name || !Number.isFinite(order)) return null;
  return { name, order };
}

function firstShortcutLabel(shortcut, platform) {
  if (!shortcut) return null;
  const first = Array.isArray(shortcut) ? shortcut[0] : shortcut;
  const chord = typeof first === "string" ? first : first && first.chord;
  if (!chord) return null;
  try {
    return labelFor(chord, platform);
  } catch {
    return null;
  }
}

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** `tree` -> the menu bar's inner markup: one `.menu-wrap` per menu, each
 *  holding a trigger button (`role="menuitem"`, the roving-tabindex ARIA
 *  menubar pattern — a top-level menu button IS a menuitem of the menubar)
 *  and a `role="menu"` popup of `role="menuitem"` rows. A disabled row keeps
 *  its `data-action` (so re-enabling it needs no re-render) but carries
 *  `aria-disabled="true"` instead of vanishing — the map stays stable (spec
 *  §4). Nothing here assumes a DOM: it is a string, parsed once by the
 *  caller (the `dialogs_model.markup` precedent). The caller supplies the
 *  `role="menubar"` container (`index.html`'s static `<nav id="menubar"
 *  role="menubar">`); this returns only its children. */
export function markup(t) {
  return (t || []).map(menuWrapHtml).join("");
}

function menuWrapHtml(m) {
  // `m.menu` reaches an `id=` attribute that `aria-controls`/`aria-labelledby`
  // point at, so it goes through the same escape as everything else here —
  // `tree()` sources it from the fixed `MENU_ORDER` today, but "unreachable"
  // is not the same as "escaped", and this file's contract is that every
  // interpolation is escaped.
  const btnId = escapeHtml(`menubar-${m.menu}-btn`);
  const menuId = escapeHtml(`menubar-${m.menu}-menu`);
  return (
    `<div class="menu-wrap" data-menu="${escapeHtml(m.menu)}">`
    + `<button type="button" class="tb-btn" role="menuitem" id="${btnId}" `
    + `aria-haspopup="menu" aria-expanded="false" aria-controls="${menuId}">`
    + `${escapeHtml(m.label)}<span class="chev" aria-hidden="true">▾</span></button>`
    + `<div class="menu left hidden" role="menu" id="${menuId}" aria-labelledby="${btnId}">`
    + m.items.map(itemHtml).join("")
    + `</div></div>`
  );
}

function itemHtml(item) {
  const sep = item.separatorBefore ? `<div class="menu-sep" role="separator"></div>` : "";
  const dangerClass = item.danger ? " danger" : "";
  const disabled = item.enabled ? "" : ` aria-disabled="true"`;
  const keyshortcuts = item.shortcutLabel
    ? ` aria-keyshortcuts="${escapeHtml(item.shortcutLabel)}"` : "";
  const kbd = item.shortcutLabel
    ? `<span class="menu-kbd">${escapeHtml(item.shortcutLabel)}</span>` : "";
  return (
    `${sep}<button type="button" class="menu-item${dangerClass}" role="menuitem" `
    + `data-action="${escapeHtml(item.id)}"${disabled}${keyshortcuts}>`
    + `<span>${escapeHtml(item.title)}</span>${kbd}</button>`
  );
}

function escapeHtml(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Test seam — the node round-trip imports this and nothing else.
export const __menu__ = { MENU_ORDER, tree, markup };
