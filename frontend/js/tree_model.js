// Grouped assembly-tree rows for the sidebar (PRD-013 FR5/FR1) and the folder
// tree, filter, selection and persisted collapse state PRD-027 adds on top.
// Pure data — NO DOM, and the one import is `query_model.js`, which is pure
// too — so the whole model is unit-tested in node exactly as it runs in the
// browser: a pattern collapses to ONE row with a `×N` badge, a sub-assembly to
// ONE read-only row naming its source, a plain part to a plain row, and a
// 1 000-part project flattens to a list a virtual window can slice.
//
// The raw manifest instances (from get_project) carry `pattern` / `assembly`
// verbatim, so the sidebar groups from them directly; expansion reads member
// ids out of the FLATTENED get_assembly view (`bolt[0]`, `engine/piston[0]`).
//
// PRD-027 note on the split: `folderTree` is where the DISPLAY order lives
// (folders first, alphabetical, then parts in manifest order) and `filterRows`
// is where the "a hit pulls its ancestors into view, open" rule lives. Neither
// knows what a `<li>` is; `tree.js` renders whatever list it gets.

import { asQuery, matches, segments, folderMatches, isFolderPath }
  from "./query_model.js";

/** Row descriptors from the RAW (un-expanded) instance list. Each row:
 *  {id, kind, part?, count?, badge?, expandable?, readonly?, source?, config?}.
 *  `kind` is "part" | "linear" | "polar" | "assembly". */
export function instanceRows(instances) {
  return (instances || []).map((inst) => {
    if (inst.pattern) {
      const count = Number(inst.pattern.count) || 0;
      return {
        id: inst.id,
        kind: inst.pattern.kind,
        part: inst.part,
        count,
        badge: `×${count}`,
        expandable: true,
        subassembly: !!inst.assembly,
        source: inst.assembly ? inst.assembly.project : undefined,
      };
    }
    if (inst.assembly) {
      return {
        id: inst.id,
        kind: "assembly",
        source: inst.assembly.project,
        expandable: true,
        readonly: true,
      };
    }
    return {
      id: inst.id,
      kind: "part",
      part: inst.part,
      config: inst.config || null,
    };
  });
}

/** The expanded member ids of a base id, read from the flattened view. A
 *  pattern member is `<base>[i]`; a sub-assembly member is `<base>/...`. */
export function memberIdsOf(baseId, flattened) {
  if (!baseId) return [];
  const bracket = `${baseId}[`;
  const slash = `${baseId}/`;
  return (flattened || [])
    .map((i) => i.id)
    .filter((id) => id.startsWith(bracket) || id.startsWith(slash));
}

/** A minimal HTML string for the row list — used by the node test to assert the
 *  badge renders; the browser builds real DOM in tree.js and does not call
 *  this. Kept deliberately tiny (no escaping needed for ids/kinds, which are
 *  validated tokens). */
export function rowsHtml(rows) {
  return (rows || [])
    .map((r) => {
      const badge = r.badge ? `<span class="row-badge">${r.badge}</span>` : "";
      const src = r.source ? `<span class="row-id">${r.source}</span>` : "";
      return `<li class="row" data-kind="${r.kind}">` +
        `<span class="row-label">${r.id}</span>${badge}${src}</li>`;
    })
    .join("");
}

// ------------------------------------------------------------ the folder tree
//
// Folders are MANIFEST METADATA, not directories (design §1): a folder exists
// exactly when a part names it, and a part's script never moves. So the tree is
// derived on every render from the parts list — there is no folder object to
// keep in sync, and renaming a folder is a bulk edit of the parts that name it.
//
// Two rules decide the display order and both are deliberate. Folders come
// before parts at every level, because a list that interleaves them makes the
// depth invisible. Folders sort alphabetically (case-insensitively — a folder
// keeps the case a human typed, ruling 9) while parts stay in MANIFEST order,
// because that is the order every other surface shows them in and a second
// sort key here would silently disagree with the assembly and the exports.

/** The case-folded key a folder path is identified by. Folders are stored
 *  verbatim and matched case-insensitively (ruling 9), so `Chassis` and
 *  `chassis` are ONE folder in the tree — the first spelling seen wins the
 *  display name, and a persisted `collapsed` entry finds it either way. */
function folderKey(path) {
  return segments(path).map((s) => s.toLowerCase()).join("/");
}

function nodeFor(root, folder) {
  let node = root;
  for (const name of segments(folder)) {
    const key = name.toLowerCase();
    let child = node.children.get(key);
    if (!child) {
      child = {display: name, children: new Map(), items: [], count: 0,
               path: node.path ? `${node.path}/${name}` : name,
               key: node.key ? `${node.key}/${key}` : key};
      node.children.set(key, child);
    }
    node = child;
  }
  return node;
}

function byFolderName(a, b) {
  const x = a.display.toLowerCase();
  const y = b.display.toLowerCase();
  if (x !== y) return x < y ? -1 : 1;
  // Two spellings can only differ in case here (`folderKey` merged the rest),
  // and the comparison still has to be TOTAL or the sort is unstable.
  if (a.display !== b.display) return a.display < b.display ? -1 : 1;
  return 0;
}

/** Flatten one grouped tree into display rows. `rowOf(item, depth)` builds the
 *  leaf row, so the parts tree and the instances tree share every ordering,
 *  counting and collapse rule and differ only in what a leaf looks like. */
function flatten(node, depth, collapsedKeys, rowOf, out) {
  for (const child of [...node.children.values()].sort(byFolderName)) {
    const collapsed = collapsedKeys.has(child.key);
    out.push({kind: "folder", path: child.path, name: child.display,
              depth, count: child.count, collapsed});
    if (!collapsed) flatten(child, depth + 1, collapsedKeys, rowOf, out);
  }
  for (const item of node.items) out.push(rowOf(item, depth));
  return out;
}

/** Group `items` by `folderOf(item)` and flatten to display rows.
 *
 *  `count` on a folder row is its WHOLE SUBTREE, not its direct children: a
 *  collapsed `Chassis ▸ 3` has to say how many parts it is hiding, and the
 *  direct count would say `1` while three rows disappeared.
 */
function treeRows(items, folderOf, rowOf, opts) {
  const o = opts || {};
  const root = {display: "", children: new Map(), items: [], count: 0,
                path: "", key: ""};
  // Total over the argument, like `filterRows`: `get_project` is a network
  // payload and a render that happens one tick before it lands hands this
  // `undefined` — an empty tree is the honest answer, a thrown
  // "items is not iterable" is a blank sidebar with a console trace.
  for (const item of (Array.isArray(items) ? items : [])) {
    const folder = folderOf(item);
    const node = nodeFor(root, folder);
    node.items.push(item);
    // Every ancestor counts it, which is what makes `count` a subtree total.
    let walk = root;
    walk.count += 1;
    for (const name of segments(folder)) {
      walk = walk.children.get(name.toLowerCase());
      walk.count += 1;
    }
  }
  // A "New folder…" entry exists only in the client's persisted state until a
  // part lands in it (ruling 6). Materialised AFTER the parts so a folder a
  // part already names keeps that part's count and is never duplicated.
  for (const path of o.emptyFolders || []) nodeFor(root, path);
  const collapsedKeys = new Set((o.collapsed || []).map(folderKey));
  collapsedKeys.delete("");
  return flatten(root, 0, collapsedKeys, rowOf, []);
}

/** The parts sidebar as a flat list of display rows.
 *
 *  `{kind: "folder", path, name, depth, count, collapsed}` and
 *  `{kind: "part", id, part, depth}`, in display order, with the descendants
 *  of a collapsed folder omitted entirely — the list IS what gets rendered, so
 *  the virtual window slices it directly and a collapsed subtree costs nothing.
 *
 *  Total over the part: a `folder` that is not a string (a hand edit, a merge)
 *  reads as root rather than throwing mid-flatten.
 */
export function folderTree(parts, opts) {
  return treeRows(parts, (p) => (p ? p.folder : null),
                  (part, depth) => ({kind: "part", id: part.id, part, depth}),
                  opts);
}

/** The instance sidebar, foldered the same way.
 *
 *  The PRD-013 grouping is untouched and rides along: a row carries
 *  `row.instance` — the `instanceRows()` descriptor with its `×N` badge,
 *  `expandable`, `readonly` and `source` — so the sub-assembly and pattern
 *  rows stay ONE row each inside their folder (nesting their members under
 *  folders is Phase 3, deliberately out of this PRD).
 */
export function instanceTree(instances, opts) {
  const list = Array.isArray(instances) ? instances : [];
  const descriptors = instanceRows(list);
  const pairs = list.map((inst, i) => ({inst, row: descriptors[i]}));
  return treeRows(pairs, (p) => (p.inst ? p.inst.folder : null),
                  (p, depth) => ({kind: "instance", id: p.row.id,
                                  instance: p.row, depth}),
                  opts);
}

/** Every part filed at `path` **or under it**, by id, in manifest order.
 *
 *  Read from the PROJECT, never from the rendered rows. A folder's context
 *  menu ("Select 12 parts", "Move 12 parts to…") used to count the visible
 *  flattened rows under the folder, which made a collapsed folder contain
 *  **zero** parts and an expanded folder miss everything inside a collapsed
 *  child of its own — the menu offered to move twelve parts and moved four.
 *  Membership is a fact about the manifest; visibility and the filter are
 *  facts about the screen, and they are not the same question.
 *
 *  Matching is segment-wise and case-insensitive (`query_model.folderMatches`,
 *  which is `navigation.folder_matches`), so `Chassis` contains `Chassis/Left`
 *  and does not contain `ChassisBrackets`.
 */
export function partsInFolder(parts, path) {
  return (Array.isArray(parts) ? parts : [])
    .filter((p) => p && p.id != null && folderMatches(p.folder, path || ""))
    .map((p) => p.id);
}

/** The stored "empty" folders that survive a reload — which is none of them.
 *
 *  A folder created with **New folder…** is a session-only placeholder: the
 *  dialog promises it "is dropped on reload if it is still empty", and it was
 *  not — `readTree` restored the list unconditionally, so an experiment made
 *  months ago came back on every load of that project forever. Adopting a
 *  project's stored state prunes every path no part occupies; a path some
 *  part is filed at (or under) is kept, where it is harmless because the
 *  parts would draw the folder anyway.
 */
export function pruneEmptyFolders(emptyFolders, parts) {
  const list = Array.isArray(emptyFolders) ? emptyFolders : [];
  return list.filter((path) => (Array.isArray(parts) ? parts : [])
    .some((p) => p && folderMatches(p.folder, String(path || ""))));
}

/** The tree under a filter: `{rows, total, shown}`.
 *
 *  `query` is a parsed query, or a string this parses (`query_model.asQuery`).
 *  Four rules:
 *
 *  * a part is in when the client's `matches()` says so **OR** when
 *    `opts.ids` names it. A UNION, never an intersection: `opts.ids` is the
 *    server's answer to a free-text query, and the server is the only side
 *    that can see script text. Intersecting would drop every script-only hit
 *    — `filterRows(parts, "counterbore", {ids: ["base_plate"]})` would answer
 *    `shown: 0` for a row the server just told us matched, which is the exact
 *    query shape the server gets asked about. The union is what makes the
 *    120 ms debounce feel instant: the client's own matches render on the
 *    keystroke and the server's script hits JOIN them when they land, rather
 *    than replacing a list that was already right.
 *  * ...UNTIL `opts.authoritative` says the answer in `opts.ids` is the
 *    server's answer to THIS query, at which point it REPLACES the client's
 *    row set. The union is a provisional answer, not a better one: for free
 *    text the server sees a superset and the two agree, but for a `kind:`
 *    term the client is wrong in both directions (`get_project` reports the
 *    manifest kind, and `package` is derived from a provenance header the
 *    browser cannot read), so a union would keep rows the server excluded.
 *    `tree.js` sets it only once an answer for the current query has landed —
 *    before that the provisional list is what the user sees, exactly as
 *    before.
 *  * `opts.evidence` (`{id: matched_on}`) is the server's own evidence for
 *    the rows it named. A row the client also matched keeps the CLIENT's
 *    evidence (it can see every metadata source); a row only the server kept
 *    uses the server's, falling back to `["script"]` — the browser has no
 *    script text, so for a free-text query the script is the only thing that
 *    *can* have matched.
 *  * **every part row carries `matchedOn`** — its `matched_on` evidence, in
 *    `query_model.SOURCES` order. This is the one place evidence is reported
 *    (there is no separate `evidence` map to keep in sync), and `tree.js`
 *    renders the "why" badge off it. A row kept ONLY because the server named
 *    it gets `["script"]`: the browser has no script text, so the script is
 *    the only thing that CAN have matched, and saying so is what lets the row
 *    show a `script` badge and ask for a snippet. A row both sides kept keeps
 *    the client's evidence (the client can see the metadata sources; whether
 *    the script matched too is the server's to add to the result list).
 *  * a folder survives only if something under it did — match-bubbling, so a
 *    filter never shows an empty folder;
 *  * an ancestor of a hit is FORCED OPEN. A hit hidden inside a collapsed
 *    folder is a filter that looks broken, so while a filter is active the
 *    persisted collapse state is not consulted at all; clearing the box
 *    restores it, because it was never mutated.
 *
 *  `total` is the whole project and `shown` the number of matching parts —
 *  the filter box's "n of N" reads both off one call.
 *
 *  An EMPTY query is not a filter: it is the ordinary listing, it honours the
 *  persisted collapse and empty folders, and `opts.ids` is ignored (ids only
 *  ever come from a search of a non-empty query; with nothing to narrow, the
 *  union is everything anyway).
 */
export function filterRows(parts, query, opts) {
  const o = opts || {};
  const list = Array.isArray(parts) ? parts : [];
  const q = asQuery(query);
  if (!q.terms.length) {
    return {rows: folderTree(list, o), total: list.length, shown: list.length};
  }
  const ids = o.ids ? new Set(o.ids) : null;
  const authoritative = ids !== null && o.authoritative === true;
  const fromServer = o.evidence || {};
  const scripts = o.scripts || {};
  const kept = [];
  const evidence = new Map();
  for (const part of list) {
    const id = part ? part.id : null;
    const found = matches(part, q, {scriptText: scripts[id] || ""});
    const named = ids !== null && ids.has(id);
    if (authoritative ? !named : (found === null && !named)) continue;
    kept.push(part);
    const theirs = fromServer[id];
    evidence.set(id, found !== null ? found
      : (Array.isArray(theirs) && theirs.length ? theirs : ["script"]));
  }
  const rows = folderTree(kept).map((row) => (row.kind === "part"
    ? {...row, matchedOn: evidence.get(row.id) || []}
    : row));
  return {rows, total: list.length, shown: kept.length};
}

// -------------------------------------------------------------- the selection
//
// A multi-selection lives beside — never instead of — the scalar
// `selectedPart`/`selectedInstance` the inspector, viewport, comments and
// presence all read (design §7). That is why `primary` is a THIRD return value
// and why it is `null` for a modifier click: only a plain click designates a
// new primary, so Cmd-clicking a sixth part to bulk-tag it does not yank the
// inspector and the camera onto it.

function toSet(value) {
  if (value instanceof Set) return value;
  return new Set(Array.isArray(value) ? value : []);
}

/** The Finder rules: `{selection, anchor, primary}` after one click.
 *
 *  * plain click → just that row; it becomes the anchor AND the primary;
 *  * Cmd/Ctrl click → toggle that row, anchor moves, `primary: null`
 *    ("leave the primary alone", never "clear it");
 *  * Shift click → the range from the anchor to the clicked row over the
 *    VISIBLE order (so a collapsed folder's hidden parts are not swept in),
 *    replacing the selection — or unioned with it when Cmd is held too;
 *  * Shift with no usable anchor (none yet, or it has scrolled out of the
 *    filtered list) → a plain click, which is also how the next Shift gets an
 *    anchor to range from.
 *
 *  The caller's Set is never mutated: a new one comes back every time, so a
 *  render that captured the old one still sees the old one.
 */
export function selectionAfter(current, anchor, visibleIds, clickedId, mods) {
  const m = mods || {};
  const visible = Array.isArray(visibleIds) ? visibleIds : [];
  const base = toSet(current);
  if (m.shift) {
    const from = visible.indexOf(anchor);
    const to = visible.indexOf(clickedId);
    if (from >= 0 && to >= 0) {
      const range = visible.slice(Math.min(from, to), Math.max(from, to) + 1);
      return {selection: m.meta ? new Set([...base, ...range]) : new Set(range),
              anchor, primary: null};
    }
    return {selection: new Set([clickedId]), anchor: clickedId,
            primary: clickedId};
  }
  if (m.meta) {
    const selection = new Set(base);
    if (selection.has(clickedId)) selection.delete(clickedId);
    else selection.add(clickedId);
    return {selection, anchor: clickedId, primary: null};
  }
  return {selection: new Set([clickedId]), anchor: clickedId,
          primary: clickedId};
}

// ------------------------------------------------------- the persisted state

/** `localStorage` key for one project's tree state. Per project, because a
 *  collapsed `Chassis` in one project says nothing about another. */
export function treeKey(project) {
  return `agentcad.tree.${project == null ? "" : project}`;
}

/** Cap on stored paths, per list. A tree state is a convenience; a `collapsed`
 *  array that grew without bound (a script, a bug, a paste) would be read back
 *  on every project open, so the clamp is on BOTH sides of the round trip. */
export const MAX_TREE_PATHS = 500;

/** Valid, de-duplicated folder paths, at most `MAX_TREE_PATHS` of them.
 *
 *  `localStorage` is user-writable and survives every upgrade, so what comes
 *  back is untrusted input: anything that is not a folder path by the
 *  `navigation.py` grammar is DROPPED silently. Dropping is right here — a
 *  stale collapse entry is not an error to report, it is a folder that opens.
 */
function clampPaths(value) {
  if (!Array.isArray(value)) return [];
  const out = [];
  const seen = new Set();
  for (const raw of value) {
    if (!isFolderPath(raw)) continue;
    const key = folderKey(raw);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(raw);
    if (out.length >= MAX_TREE_PATHS) break;
  }
  return out;
}

/** One project's tree state as the JSON to store at `treeKey(project)`.
 *
 *  The project name rides inside the payload as well as in the key so a blob
 *  that ends up under the wrong key is identifiable; `readTree` ignores it
 *  (the key is the authority) rather than second-guessing the caller.
 */
export function persistTree(project, state) {
  const s = state || {};
  return JSON.stringify({
    project: project == null ? null : String(project),
    collapsed: clampPaths(s.collapsed),
    emptyFolders: clampPaths(s.emptyFolders),
  });
}

/** `{collapsed, emptyFolders}` from stored JSON — never throws.
 *
 *  Unparseable, missing, or not an object all read as "no state", which is the
 *  honest answer: a fully expanded tree with no pending folders.
 */
export function readTree(json) {
  let data = json;
  if (typeof json === "string") {
    try {
      data = JSON.parse(json);
    } catch {
      return {collapsed: [], emptyFolders: []};
    }
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return {collapsed: [], emptyFolders: []};
  }
  return {collapsed: clampPaths(data.collapsed),
          emptyFolders: clampPaths(data.emptyFolders)};
}

// Re-exported so `tree.js` and the "New folder…" dialog validate against the
// same predicate this module clamps with, without importing two modules to
// build one row.
export { isFolderPath };

// Test seam — the node round-trip imports this and nothing else.
export const __treeModel__ = { instanceRows, memberIdsOf, rowsHtml, folderTree,
                               filterRows, instanceTree, selectionAfter,
                               persistTree, readTree, treeKey, isFolderPath,
                               partsInFolder, pruneEmptyFolders };
