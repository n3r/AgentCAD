// PRD-026 shell — chord normalisation, conflict detection and the dispatch
// table. PURE: no DOM, no imports, runs in node (tests/test_frontend_shell.py).
//
// One canonical spelling for a chord, decided here and nowhere else:
//
//   * modifier order is Mod, Ctrl, Alt, Shift — always, whatever the author
//     typed ("shift+mod+z" and "Mod+Shift+Z" are the same binding, and a table
//     that spelled them differently would have accepted both and fired one);
//   * `Mod` is ⌘ on macOS and Ctrl everywhere else, resolved at DISPATCH
//     (`fromEvent`), never at registration — the registry is platform-free;
//   * a single-character key is upper-cased, a named key takes its DOM
//     `KeyboardEvent.key` spelling (Escape, Enter, ArrowUp, F1…), and `?`
//     stays `?` because that is what the browser reports for Shift+/ and the
//     chord is the character, not the keystroke that produced it.

const MODS = {
  mod: "Mod", cmd: "Mod", command: "Mod", meta: "Mod", super: "Mod",
  ctrl: "Ctrl", control: "Ctrl",
  alt: "Alt", option: "Alt", opt: "Alt",
  shift: "Shift",
};

const MOD_ORDER = ["Mod", "Ctrl", "Alt", "Shift"];

// DOM `key` spellings we accept case-insensitively. Anything else with more
// than one character is title-cased as written (so an exotic key still round
// trips rather than being silently mangled).
const NAMED = {};
for (const key of ["Escape", "Enter", "Tab", "Backspace", "Delete", "Insert",
                   "Home", "End", "PageUp", "PageDown", "ArrowUp", "ArrowDown",
                   "ArrowLeft", "ArrowRight", "Space"]) {
  NAMED[key.toLowerCase()] = key;
}
for (let i = 1; i <= 12; i += 1) NAMED[`f${i}`] = `F${i}`;

/** Canonical spelling of a chord. Throws on an empty one.
 *
 *  `+` is both the separator and a perfectly ordinary key, so it is lifted out
 *  before the split rather than being lost in it: `"Mod++"` is Mod plus the
 *  `+` key, `"+"` is the bare `+` key. (`normalize` is for AUTHOR-written
 *  chords; `fromEvent` builds its chord directly and never round-trips through
 *  this parser.)
 */
export function normalize(chord) {
  const { mods, key } = parseChord(chord);
  return [...MOD_ORDER.filter((m) => mods.has(m)), key].join("+");
}

/** `{mods: Set, key}` — the one parser `normalize` and `label` share, so a
 *  chord whose key is `+` survives both and not just the first. */
function parseChord(chord) {
  let text = String(chord == null ? "" : chord).trim();
  let plusKey = false;
  if (text === "+") {
    plusKey = true;
    text = "";
  } else if (text.endsWith("+")) {
    plusKey = true;
    text = text.slice(0, -1);                       // "Mod++" -> "Mod+"
    if (text.endsWith("+")) text = text.slice(0, -1); // -> "Mod"
  }
  const parts = text
    .split("+")
    .map((p) => p.trim())
    .filter((p) => p.length);
  if (!parts.length && !plusKey) throw new Error("empty shortcut chord");
  const mods = new Set();
  let key = plusKey ? "+" : null;
  for (const part of parts) {
    const mod = MODS[part.toLowerCase()];
    // A lone modifier NAME is not a chord ("Shift" alone) — the throw below
    // catches it; with other parts present it is a modifier.
    if (mod && (parts.length > 1 || plusKey)) {
      mods.add(mod);
      continue;
    }
    if (mod) throw new Error(`shortcut chord is a bare modifier: ${chord}`);
    if (key !== null) throw new Error(`shortcut chord has two keys: ${chord}`);
    key = keyName(part);
  }
  if (key === null) throw new Error(`shortcut chord has no key: ${chord}`);
  return { mods, key };
}

function keyName(raw) {
  if (raw.length === 1) return raw.toUpperCase();
  const named = NAMED[raw.toLowerCase()];
  return named || raw;
}

/** The chord a keydown event names, or null for a bare modifier press.
 *
 *  `platform` is `navigator.platform` (or anything the caller has): only
 *  "is this a Mac" is read from it, and that single bit decides whether ⌘ or
 *  Ctrl is `Mod`. A Ctrl press ON a Mac is a real, different chord (`Ctrl+…`),
 *  which is why the two are not collapsed.
 */
export function fromEvent(event, platform) {
  const key = event && event.key;
  if (!key || key === "Shift" || key === "Control" || key === "Alt"
      || key === "Meta" || key === "OS" || key === "Dead"
      || key === "AltGraph") {
    return null;
  }
  const mac = isMac(platform);
  const mods = [];
  if (mac ? event.metaKey : event.ctrlKey) mods.push("Mod");
  if (mac ? event.ctrlKey : false) mods.push("Ctrl");
  if (event.altKey) mods.push("Alt");
  // Shift is part of the chord only when it did not already change the key the
  // browser reports: `Shift+/` arrives as `?` and IS the chord `?`, while
  // `Shift+B` arrives as `B` and is the chord `Shift+B`.
  if (event.shiftKey && (key.length > 1 || /^[a-z0-9]$/i.test(key))) {
    mods.push("Shift");
  }
  // Assembled DIRECTLY, never via `normalize`: this runs on every keystroke in
  // the app from inside an untried document listener, and round-tripping
  // through a parser that splits on "+" made an ordinary `+` keypress (or a
  // browser-zoom ⌘+) throw instead of returning a chord.
  return [...MOD_ORDER.filter((m) => mods.includes(m)), keyName(key)].join("+");
}

const MAC_GLYPH = { Mod: "⌘", Ctrl: "⌃", Alt: "⌥", Shift: "⇧" };
// Apple's order, which is not our storage order: ⌃⌥⇧⌘key.
const MAC_ORDER = ["Ctrl", "Alt", "Shift", "Mod"];
const MAC_KEYS = {
  Escape: "Esc", Enter: "↵", Tab: "⇥", Backspace: "⌫", Delete: "⌦",
  ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→", Space: "␣",
};
const PC_KEYS = { Escape: "Esc", Space: "Space" };

/** How the chord is written for a human on `platform`. */
export function label(chord, platform) {
  const { mods, key } = parseChord(chord);
  if (isMac(platform)) {
    const glyphs = MAC_ORDER.filter((m) => mods.has(m)).map((m) => MAC_GLYPH[m]);
    return glyphs.join("") + (MAC_KEYS[key] || key);
  }
  const names = [];
  if (mods.has("Mod") || mods.has("Ctrl")) names.push("Ctrl");
  if (mods.has("Alt")) names.push("Alt");
  if (mods.has("Shift")) names.push("Shift");
  names.push(PC_KEYS[key] || key);
  return names.join("+");
}

function isMac(platform) {
  return /mac|iphone|ipad|ipod/i.test(String(platform || ""));
}

/** Two bindings on one chord in one scope is a programming error, not a
 *  runtime condition — the registration is static code, so it throws in every
 *  build (AC5) and names BOTH ids, because "conflict on Mod+S" without the
 *  incumbent is a bug report with the useful half missing. */
export class ShortcutConflictError extends Error {
  constructor(chord, existingId, newId) {
    super(`shortcut conflict on ${chord}: already bound to ${existingId}, `
          + `cannot bind ${newId}`);
    this.name = "ShortcutConflictError";
    this.chord = chord;
    this.existingId = existingId;
    this.id = newId;
  }
}

/** The dispatch table. `scope` separates bindings that survive an open modal
 *  (`"modal-safe"`) from the ordinary ones (`"global"`), so the same chord may
 *  legitimately mean two things in the two situations. */
export class Table {
  constructor() {
    this._rows = []; // insertion order is the cheat-sheet's order
    // scope -> Map<chord, row>. Nested rather than a composed `scope+chord`
    // string key: no separator can then collide with (or be forbidden in) a
    // chord, and no separator byte can sneak into the source.
    this._index = new Map();
  }

  bind({ chord, id, scope = "global", when = null, title = null, group = null }) {
    const canon = normalize(chord);
    let byChord = this._index.get(scope);
    if (!byChord) {
      byChord = new Map();
      this._index.set(scope, byChord);
    }
    const clash = byChord.get(canon);
    if (clash) throw new ShortcutConflictError(canon, clash.id, id);
    const row = { chord: canon, id, scope, when, title, group };
    byChord.set(canon, row);
    this._rows.push(row);
    return row;
  }

  lookup(chord, scope = "global") {
    const byChord = this._index.get(scope);
    return (byChord && byChord.get(normalize(chord))) || null;
  }

  list() {
    return this._rows.map((r) => ({ ...r }));
  }
}

// Test seam — the node round-trip imports this and nothing else.
export const __shortcuts__ = {
  normalize, fromEvent, label, Table, ShortcutConflictError,
};
