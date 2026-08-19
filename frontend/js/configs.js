// The configuration matrix (PRD-012). One part's declared family, built and
// compared side by side: a row per configuration, a column per metric.
//
// It is a modal rather than an inspector block on purpose — the inspector is
// 326 px and `flex: none`, so six numbers per row for a three-member family
// simply do not fit there. Same `.modal-overlay` shape as versions/merge/
// proposals/library (see the library note in index.html): one modal shape in
// this app is worth more than one of them being modern. PRD-026 slice 2
// adopted it onto the shell's overlay stack, so Esc and the focus trap are the
// stack's and this markup is unchanged.
//
// **A matrix build is not the part's build.** `build_configs` publishes
// `rebuild_started/finished/failed` carrying `config`, and main.js hands
// exactly those here and returns: `state.rebuilding` holds bare part ids, so
// the first configuration to finish would clear the part's dot and repaint the
// inspector with another configuration's metrics. The per-row `building…` state is
// what those events are for.

import { api, ApiError } from "./api.js";
import { state } from "./state.js";
import * as dialogs from "./shell/dialogs.js";

let actions = null;
let overlay, titleEl, bodyEl, buildBtn, closeBtn;

// The last response we rendered, held module-locally so reopening the modal
// for the same part shows its numbers immediately instead of a blank table
// while the (cached, but still round-tripping) build runs again.
let lastMatrix = null;   // {partId, rows: [...], warnings: []}

let openPartId = null;   // the part the modal is showing, or null when closed
let legacy = null;       // the overlay's seat on the shell's dialog stack
let listing = null;      // list_configs' row for openPartId: declared + referrers
let buildToken = 0;      // guards a response that landed after the user moved on
let building = false;
const rowState = new Map(); // config name -> "building" | "failed"

export function init(a) {
  actions = a;
  overlay = document.getElementById("configs-modal");
  titleEl = document.getElementById("configs-title");
  bodyEl = document.getElementById("configs-body");
  buildBtn = document.getElementById("configs-build");
  closeBtn = document.getElementById("configs-close");
  if (!overlay) return;

  closeBtn.addEventListener("click", close);
  buildBtn.addEventListener("click", () => runBuild());
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  // PRD-026 FR2: Esc belongs to the shell's overlay stack, not to this module.
  legacy = dialogs.attachLegacy(overlay, {
    view: "configs", title: "Configurations…", isOpen, onClose: close,
    description: "The selected part's configuration matrix",
    open: (args) => open((args && args.part) || state.selectedPart),
    when: (c) => !!c.selectedPart,
  });
}

export function isOpen() {
  return !!overlay && !overlay.classList.contains("hidden");
}

/** Open the matrix for one part and build its family. Cached members cost a
 *  round trip and nothing else — `build_configs` is serial and de-duplicated
 *  by cache key, and reports `cached` per row. */
export async function open(partId) {
  if (!overlay || !partId) return;
  const proj = state.projectName;
  if (!proj) {
    actions.toast("Open a project first", "error");
    return;
  }
  if (!lastMatrix || lastMatrix.partId !== partId) lastMatrix = null;
  openPartId = partId;
  listing = null;
  rowState.clear();
  overlay.classList.remove("hidden");
  if (legacy) legacy.notifyOpen();
  titleEl.textContent = `${partId} · configurations`;
  render();     // seeded from state — the table never waits on a round trip

  const token = ++buildToken;
  // The referrer map is the one fact the build rows do not carry: which
  // assembly instances are bound to each configuration, i.e. what else moves
  // when you edit one. It is a manifest read, so it RACES the build rather
  // than gating it — awaiting it here is what used to flash "declares no
  // configurations" through the first round trip.
  api.listConfigs(proj, partId).then(
    (payload) => {
      if (token !== buildToken) return;
      listing = (payload.parts || [])[0] || null;
      render();
    },
    () => {
      /* referrers go unlisted rather than taking the panel down */
    }
  );
  await runBuild(token);
}

function close() {
  if (!overlay) return;
  overlay.classList.add("hidden");
  if (legacy) legacy.notifyClose();   // idempotent: Esc pops the stack itself
  buildToken++;             // orphan any in-flight build's render
  openPartId = null;
  listing = null;
  building = false;
  buildBtn.disabled = false;
  rowState.clear();
  bodyEl.textContent = "";
}

/** A config-tagged rebuild event, handed over by main.js. */
export function onRebuildEvent(ev) {
  if (!isOpen() || !ev || !ev.config) return;
  if (ev.part !== openPartId || ev.project !== state.projectName) return;
  if (ev.type === "rebuild_started") rowState.set(ev.config, "building");
  else if (ev.type === "rebuild_failed") rowState.set(ev.config, "failed");
  else rowState.delete(ev.config);
  render();
}

async function runBuild(token) {
  const partId = openPartId;
  const proj = state.projectName;
  if (!partId || !proj) return;
  if (token === undefined) token = ++buildToken;
  building = true;
  buildBtn.disabled = true;
  render();
  let payload;
  try {
    payload = await api.buildConfigs(proj, { part_id: partId });
  } catch (err) {
    if (token !== buildToken) return;
    building = false;
    buildBtn.disabled = false;
    // A refusal takes the whole matrix (an unknown name, a script that will
    // not load); a member that fails to BUILD is a row, not this.
    note(err instanceof ApiError ? err.error.message : String(err), "err");
    return;
  }
  if (token !== buildToken) return;
  building = false;
  buildBtn.disabled = false;
  rowState.clear();
  lastMatrix = {
    partId,
    rows: payload.configs || [],
    warnings: payload.warnings || [],
  };
  render();
}

// ------------------------------------------------------------------ render

function note(text, cls) {
  bodyEl.textContent = "";
  const el = document.createElement("div");
  el.className = cls ? `prop-note ${cls}` : "prop-note";
  el.textContent = text;
  bodyEl.appendChild(el);
}

/** The declared family, from whichever piece of state already has it. Both
 *  `get_part` and `get_project` carry `configs`, so the modal knows what its
 *  rows ARE the moment it opens and never has to guess "no configurations"
 *  while a fetch is in flight. */
function declaredFamily() {
  if (listing && listing.configs) return listing.configs;
  if (state.part && state.part.id === openPartId) return state.part.configs || {};
  const entry = state.project
    ? state.project.parts.find((p) => p.id === openPartId)
    : null;
  return (entry && entry.configs) || {};
}

/** The build rows once we have them, otherwise the declared names — in family
 *  order either way — so the first paint is the real table with its numbers
 *  pending, not an empty one. */
function currentRows() {
  if (lastMatrix && lastMatrix.partId === openPartId) return lastMatrix.rows;
  const declared = declaredFamily();
  return Object.keys(declared).map((name) => ({
    name,
    label: (declared[name] && declared[name].label) || null,
    pending: true,
  }));
}

function render() {
  if (!isOpen()) return;
  bodyEl.textContent = "";
  const rows = currentRows();
  if (!rows.length) {
    note(
      building
        ? "Building…"
        : `${openPartId} declares no configurations. Declare a family with ` +
          "set_part_configs and it appears here."
    );
    return;
  }

  const table = document.createElement("table");
  table.className = "prop-table cfg-table";
  const head = document.createElement("tr");
  for (const col of [
    "config", "state", "mass g", "volume mm³", "X mm", "Y mm", "Z mm", "specs",
  ]) {
    const th = document.createElement("th");
    th.textContent = col;
    head.appendChild(th);
  }
  table.appendChild(head);
  for (const row of rows) table.appendChild(matrixRow(row));
  bodyEl.appendChild(table);

  const active = activeConfigOf();
  for (const warning of (lastMatrix && lastMatrix.warnings) || []) {
    const el = document.createElement("div");
    el.className = "prop-note";
    el.textContent = `⚠ ${warning}`;
    bodyEl.appendChild(el);
  }
  const foot = document.createElement("div");
  foot.className = "prop-note";
  foot.textContent = active
    ? `${openPartId} is showing ${active}. Every row is a pure resolution of ` +
      "the declared configuration — parameter overrides never reach it."
    : "Every row is a pure resolution of the declared configuration — " +
      "parameter overrides never reach it.";
  bodyEl.appendChild(foot);
}

function activeConfigOf() {
  if (listing) return listing.active_config || null;
  if (state.part && state.part.id === openPartId) {
    return state.part.active_config || null;
  }
  return null;
}

function matrixRow(row) {
  const tr = document.createElement("tr");
  const live = rowState.get(row.name);
  // A row that already reported is NOT pending just because the serial
  // build is still working through its siblings — only the one the kernel
  // is on says "building…".
  const pending = row.pending || (building && live === "building");
  const failed = live === "failed" || row.ok === false;

  const name = document.createElement("td");
  name.className = "cfg-name";
  name.textContent = row.name;
  if (row.name === activeConfigOf()) name.classList.add("cfg-active");
  if (row.label) name.title = row.label;
  // Which assembly instances are bound to this configuration — the one thing
  // a build row cannot say, and the reason `list_configs` is called at all.
  const bound = ((listing && listing.referrers) || {})[row.name] || [];
  if (bound.length) {
    const refs = document.createElement("span");
    refs.className = "cfg-refs";
    refs.textContent = ` ${bound.join(", ")}`;
    refs.title =
      `${bound.length} assembly instance${bound.length === 1 ? "" : "s"} ` +
      `bound to ${row.name}`;
    name.appendChild(refs);
  }
  tr.appendChild(name);

  const stateCell = document.createElement("td");
  if (live === "building" || pending) {
    stateCell.textContent = "building…";
  } else if (failed) {
    const bad = document.createElement("span");
    bad.className = "bad";
    bad.textContent = "failed";
    const err = row.error || {};
    bad.title = err.message || err.type || "build failed";
    stateCell.appendChild(bad);
  } else if (row.ok) {
    stateCell.textContent = row.cached ? "cached" : "built";
  } else {
    stateCell.textContent = "—";
  }
  tr.appendChild(stateCell);

  const m = (row.ok && row.metrics) || null;
  const dims = m && m.bbox
    ? [0, 1, 2].map((i) => m.bbox.max[i] - m.bbox.min[i])
    : null;
  for (const value of [
    m ? m.mass_g : null,
    m ? m.volume_mm3 : null,
    dims ? dims[0] : null,
    dims ? dims[1] : null,
    dims ? dims[2] : null,
  ]) {
    const td = document.createElement("td");
    td.textContent = value == null ? (pending ? "…" : "—") : fmt(value);
    tr.appendChild(td);
  }

  const specs = document.createElement("td");
  renderSpecCell(specs, row);
  tr.appendChild(specs);

  // The failure in place: the row keeps its identity and says what broke,
  // rather than the matrix collapsing into one error.
  if (failed && row.error && row.error.message) {
    const wrap = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.className = "cfg-error";
    cell.textContent = row.error.message;
    wrap.appendChild(cell);
    // A <tr> can't hold a <tr>; hand both back through a fragment.
    const frag = document.createDocumentFragment();
    frag.appendChild(tr);
    frag.appendChild(wrap);
    return frag;
  }
  return tr;
}

function renderSpecCell(td, row) {
  const results = row.spec_results;
  if (!results) {
    td.textContent = "";
    return;
  }
  if (results.error) {
    const chip = document.createElement("span");
    chip.className = "spec-chip spec-error";
    chip.textContent = "specs";
    chip.title = results.error.message || results.error.type || "specs failed";
    td.appendChild(chip);
    return;
  }
  const chips = document.createElement("div");
  chips.className = "spec-chips";
  for (const check of results.checks || []) {
    const status = check.status || "error";
    const chip = document.createElement("span");
    chip.className = `spec-chip spec-${status}`;
    chip.textContent = check.name || check.id || "check";
    chip.title = `${check.name || "check"} — ${status}` +
      (check.message ? `\n${check.message}` : "");
    chips.appendChild(chip);
  }
  td.appendChild(chips);
}

// The inspector's number formatter, in the two shapes this table needs.
function fmt(v) {
  if (v == null || !Number.isFinite(v)) return "—";
  if (Object.is(v, -0) || Math.abs(v) < 5e-7) v = 0;
  const abs = Math.abs(v);
  if (abs >= 10000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (abs >= 100) return v.toLocaleString("en-US", { maximumFractionDigits: 1 });
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}
