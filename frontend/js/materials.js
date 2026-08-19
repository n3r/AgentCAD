// The materials database browser (PRD-028 slice 5, Decision 10). A MODAL
// inside the workbench — reuses the `.modal-overlay`/`.modal` chrome every
// other panel (library.js, configs.js, proposals.js…) already uses — NOT a
// `#market`-style full-page takeover: assign mode needs the workbench, and
// the part being assigned to, alive underneath it. Same
// "don't import the panel that opens/writes through you, go through actions"
// idiom as `openProposal`: inspector.js's Browse… button calls
// `actions.openMaterials`, this module's "Use for part" calls
// `actions.assignMaterial` (-> `inspector.setPartMaterial`, the exact path
// the inspector's own material `<select>` uses).
//
// Layout: a category/subcategory tree (left, static per open — counts read
// the FULL catalog once so navigating never costs a second request), a
// filter bar + sortable table + compare pane (center), a detail pane
// (right). Every filter change is ONE `GET /api/materials` (the numeric
// fields debounced 250 ms; tree clicks / chip clicks / the basis select are
// discrete actions and refresh immediately).

import { api, ApiError } from "./api.js";
import * as dialogs from "./shell/dialogs.js";
import { state } from "./state.js";
import * as materialsModel from "./materials_model.js";

const CATEGORY_ORDER = [
  "metal", "polymer", "composite", "wood", "masonry", "ceramic", "other",
];

const PROCESS_RATED_LABELS = {
  machinability: "machining", weldability: "welding",
  im: "injection molding", casting: "casting",
};
const PRINT_LABELS = { fdm: "FDM", sla: "SLA", sls: "SLS", mjf: "MJF", dmls: "DMLS" };

const TABLE_COLUMNS = [
  { key: "label", label: "Material", sortable: true },
  { key: "category", label: "Category / sub", sortable: true },
  { key: "condition", label: "Condition", sortable: true },
  { key: "density_g_cm3", label: "ρ (g/cm³)", sortable: true },
  { key: "E_gpa", label: "E (GPa)", sortable: true },
  { key: "yield_mpa", label: "Yield (MPa)", sortable: true },
  { key: "max_service_temp_c", label: "T max (°C)", sortable: true },
  { key: "cost_usd_kg", label: "Cost ($/kg)", sortable: true },
];

let actions = null;

let overlay, titleEl, countEl, compareBtn, closeBtn;
let treeEl, tableWrap, compareEl, detailEl;
let filterInputs; // {densityMin, densityMax, eMin, yieldMin, tempMin, costMax}
let processHost, basisSelect, clearBtn;

let assignTarget = null;
let rows = []; // last filtered list() response's `materials`
let catalogRows = []; // unfiltered full catalog, fetched once per open (tree)
let activeCategory = null;
let activeSubcategory = null;
let activeProcess = null;
let sortKey = null;
let sortDir = "asc";
let pinned = []; // up to 4 material ids
let compareMode = false;
let detailId = null;
let detailRecord = null;
let recordCache = new Map(); // id -> full get_material() record, this open only
let toasted = new Set(); // library warnings already toasted this open (once, not per keystroke)
let filterTimer = null;
let listToken = 0;
let legacy = null;

export function init(a) {
  actions = a;
  overlay = document.getElementById("materials-modal");
  titleEl = document.getElementById("materials-title");
  countEl = document.getElementById("materials-count");
  compareBtn = document.getElementById("materials-compare-btn");
  closeBtn = document.getElementById("materials-close");
  treeEl = document.getElementById("materials-tree");
  tableWrap = document.getElementById("materials-table-wrap");
  compareEl = document.getElementById("materials-compare");
  detailEl = document.getElementById("materials-detail");
  processHost = document.getElementById("mat-f-process");
  basisSelect = document.getElementById("mat-f-basis");
  clearBtn = document.getElementById("mat-f-clear");
  filterInputs = {
    density_g_cm3_min: document.getElementById("mat-f-density-min"),
    density_g_cm3_max: document.getElementById("mat-f-density-max"),
    E_gpa_min: document.getElementById("mat-f-e-min"),
    yield_mpa_min: document.getElementById("mat-f-yield-min"),
    max_service_temp_c_min: document.getElementById("mat-f-temp-min"),
    cost_usd_kg_max: document.getElementById("mat-f-cost-max"),
  };

  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  // PRD-026 merge: the overlay adopts the dialog stack (focus trap, one Esc
  // owner, `isModalOpen()` truth) instead of its own document keydown — the
  // same adoption every other legacy `.modal-overlay` got in slice 2.
  legacy = dialogs.attachLegacy(overlay, {
    view: "materials", title: "Materials…", isOpen, onClose: close,
    description: "Browse the materials database (filters, compare, detail)",
    open: () => open(),
    when: (c) => !!c.projectName,
  });

  for (const input of Object.values(filterInputs)) {
    input.addEventListener("input", scheduleRefresh);
  }
  for (const key of materialsModel.PROCESS_KEYS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "mat-chip";
    chip.textContent = key;
    chip.dataset.process = key;
    chip.addEventListener("click", () => {
      activeProcess = activeProcess === key ? null : key;
      markProcessActive();
      refresh();
    });
    processHost.appendChild(chip);
  }
  basisSelect.addEventListener("change", refresh);
  clearBtn.addEventListener("click", () => {
    for (const input of Object.values(filterInputs)) input.value = "";
    basisSelect.value = "";
    activeProcess = null;
    activeCategory = null;
    activeSubcategory = null;
    markProcessActive();
    markTreeActive();
    refresh();
  });
  treeEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".mat-tree-row");
    if (!btn) return;
    activeCategory = btn.dataset.cat || null;
    activeSubcategory = btn.dataset.sub || null;
    markTreeActive();
    refresh();
  });
  compareBtn.addEventListener("click", () => {
    compareMode = !compareMode;
    renderView();
    if (compareMode) renderCompare();
  });
}

export function isOpen() {
  return overlay && !overlay.classList.contains("hidden");
}

/** `opts.assignTo`: a part id — assign mode. The detail pane's "Use for
 *  part" button appears only then, and writes back through
 *  `actions.assignMaterial` (the same path the inspector's material
 *  `<select>` uses), never a request of its own. */
export async function open(opts) {
  if (!state.projectName) {
    actions.toast("Open a project first", "error");
    return;
  }
  assignTarget = (opts && opts.assignTo) || null;
  rows = [];
  catalogRows = [];
  activeCategory = null;
  activeSubcategory = null;
  activeProcess = null;
  sortKey = null;
  sortDir = "asc";
  pinned = [];
  compareMode = false;
  detailId = null;
  detailRecord = null;
  recordCache = new Map();
  toasted = new Set();
  for (const input of Object.values(filterInputs)) input.value = "";
  basisSelect.value = "";
  markProcessActive();
  compareBtn.classList.add("hidden");
  compareBtn.textContent = "Compare";

  overlay.classList.remove("hidden");
  if (legacy) legacy.notifyOpen();
  titleEl.textContent = assignTarget
    ? `Materials → assign to ${assignTarget}`
    : "Materials";
  note(tableWrap, "Loading…");
  note(detailEl, "Click a row to see its full record.");
  treeEl.textContent = "";
  compareEl.textContent = "";
  compareEl.classList.add("hidden");
  closeBtn.focus();

  await Promise.all([loadTree(), refresh()]);
}

function close() {
  overlay.classList.add("hidden");
  if (legacy) legacy.notifyClose();
  assignTarget = null;
  rows = [];
  catalogRows = [];
  pinned = [];
  recordCache = new Map();
  detailId = null;
  detailRecord = null;
  treeEl.textContent = "";
  tableWrap.textContent = "";
  compareEl.textContent = "";
  detailEl.textContent = "";
}

// ----------------------------------------------------------------- fetching

/** The tree's counts come from the FULL catalog, fetched once per open — not
 *  re-fetched on every filter change (that would be a second `GET` per
 *  change, which the debounce contract rules out). Picking a category only
 *  narrows the table; the tree itself stays a fixed map of the catalog. */
async function loadTree() {
  // The workbench already holds the project's full catalog (main.js loads it
  // at project open and after a materials edit) — reuse it rather than
  // fetching ~0.5 MB a second time; fall back to a fetch only when absent.
  let payload = state.materials && state.materials.materials ? state.materials : null;
  if (!payload) {
    try {
      payload = await api.listMaterials(state.projectName);
    } catch {
      return; // the table's own fetch (refresh()) reports the failure
    }
  }
  if (!isOpen()) return;
  catalogRows = payload.materials || [];
  renderTree();
}

function currentUiFilters() {
  const out = { category: activeCategory, subcategory: activeSubcategory,
                process: activeProcess, basis: basisSelect.value || null };
  for (const key of materialsModel.NUMERIC_FILTER_KEYS) {
    out[key] = filterInputs[key] ? filterInputs[key].value : "";
  }
  return out;
}

function scheduleRefresh() {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(refresh, 250);
}

async function refresh() {
  if (!isOpen()) return;
  const { category, subcategory, filter } =
    materialsModel.filterToQuery(currentUiFilters());
  const token = ++listToken;
  let payload;
  try {
    payload = await api.listMaterials(state.projectName, { category, subcategory, filter });
  } catch (err) {
    if (token !== listToken || !isOpen()) return;
    note(tableWrap, `Could not load materials: ${errorText(err)}`, "error");
    countEl.textContent = "";
    return;
  }
  if (token !== listToken || !isOpen()) return;
  rows = payload.materials || [];
  // A library/pin warning is the same on every debounced refresh: say it
  // once per open, not once per keystroke.
  for (const w of payload.warnings || []) {
    if (toasted.has(w)) continue;
    toasted.add(w);
    actions.toast(`Materials library: ${w}`, "error");
  }
  if (payload.global_error && !toasted.has(payload.global_error)) {
    toasted.add(payload.global_error);
    actions.toast(`Materials: ${payload.global_error}`, "error");
  }
  countEl.textContent = `${payload.count} material${payload.count === 1 ? "" : "s"}`;
  renderView();
}

async function getRecordCached(id) {
  if (recordCache.has(id)) return recordCache.get(id);
  const record = await api.getMaterial(id, state.projectName);
  recordCache.set(id, record);
  return record;
}

// ------------------------------------------------------------------- render

function renderView() {
  tableWrap.classList.toggle("hidden", compareMode);
  compareEl.classList.toggle("hidden", !compareMode);
  compareBtn.textContent = compareMode ? "Back to table" : `Compare (${pinned.length})`;
  if (!compareMode) renderTable();
}

function catRank(cat) {
  const i = CATEGORY_ORDER.indexOf(cat);
  return i < 0 ? CATEGORY_ORDER.length : i;
}

function treeRow(text, cat, sub, count) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = sub ? "mat-tree-row mat-tree-sub" : cat ? "mat-tree-row mat-tree-cat" : "mat-tree-row mat-tree-all";
  btn.dataset.cat = cat || "";
  btn.dataset.sub = sub || "";
  btn.textContent = text;
  const badge = document.createElement("span");
  badge.className = "mat-tree-count";
  badge.textContent = String(count);
  btn.appendChild(badge);
  return btn;
}

function renderTree() {
  treeEl.textContent = "";
  const counts = materialsModel.treeCounts(catalogRows);
  const total = catalogRows.length;
  treeEl.appendChild(treeRow("All materials", null, null, total));
  const cats = Object.keys(counts).sort((a, b) => catRank(a) - catRank(b) || a.localeCompare(b));
  for (const cat of cats) {
    treeEl.appendChild(treeRow(cat, cat, null, counts[cat].count));
    const subs = Object.keys(counts[cat].subcategories).sort();
    for (const sub of subs) {
      treeEl.appendChild(treeRow(sub, cat, sub, counts[cat].subcategories[sub]));
    }
  }
  markTreeActive();
}

function markTreeActive() {
  for (const row of treeEl.querySelectorAll(".mat-tree-row")) {
    const cat = row.dataset.cat || null;
    const sub = row.dataset.sub || null;
    row.classList.toggle("active", cat === activeCategory && sub === activeSubcategory);
  }
}

function markProcessActive() {
  for (const chip of processHost.querySelectorAll(".mat-chip")) {
    chip.classList.toggle("active", chip.dataset.process === activeProcess);
  }
}

function renderTable() {
  tableWrap.textContent = "";
  if (!rows.length) {
    note(tableWrap, "No materials match these filters.");
    return;
  }
  const sorted = sortKey ? materialsModel.sortRows(rows, sortKey, sortDir) : rows;
  const table = document.createElement("table");
  table.className = "prop-table mat-table";

  const head = document.createElement("tr");
  const pinTh = document.createElement("th");
  pinTh.scope = "col";
  pinTh.textContent = "Pin";
  head.appendChild(pinTh);
  for (const col of TABLE_COLUMNS) {
    const th = document.createElement("th");
    th.scope = "col";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mat-sort-btn";
    btn.textContent = col.label + (sortKey === col.key ? (sortDir === "asc" ? " ▲" : " ▼") : "");
    btn.addEventListener("click", () => {
      if (sortKey === col.key) sortDir = sortDir === "asc" ? "desc" : "asc";
      else { sortKey = col.key; sortDir = "asc"; }
      renderTable();
    });
    th.appendChild(btn);
    head.appendChild(th);
  }
  table.appendChild(head);

  const atCap = pinned.length >= 4;
  for (const row of sorted) {
    const tr = document.createElement("tr");
    tr.className = "mat-row";
    tr.dataset.id = row.id;

    const pinTd = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.setAttribute("aria-label", `Pin ${row.label} for compare`);
    cb.checked = pinned.includes(row.id);
    cb.disabled = atCap && !cb.checked;
    cb.addEventListener("click", (e) => e.stopPropagation());
    cb.addEventListener("change", () => togglePin(row.id, cb.checked));
    pinTd.appendChild(cb);
    tr.appendChild(pinTd);

    const nameTd = document.createElement("td");
    nameTd.innerHTML =
      `${escapeHtml(row.label)}<div class="mat-row-id">${escapeHtml(row.id)}</div>`;
    tr.appendChild(nameTd);

    const catTd = document.createElement("td");
    catTd.textContent = `${row.category} / ${row.subcategory || "unclassified"}`;
    tr.appendChild(catTd);

    tr.appendChild(textTd(row.condition || "—"));
    tr.appendChild(textTd(fmtCell(row.density_g_cm3)));
    tr.appendChild(textTd(fmtCell(row.E_gpa)));
    tr.appendChild(textTd(fmtCell(row.yield_mpa)));
    tr.appendChild(textTd(fmtCell(row.max_service_temp_c)));
    tr.appendChild(textTd(fmtCell(row.cost_usd_kg)));

    tr.addEventListener("click", () => selectDetail(row.id));
    if (row.id === detailId) tr.classList.add("active");
    table.appendChild(tr);
  }
  tableWrap.appendChild(table);
}

function textTd(text) {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}

function togglePin(id, checked) {
  if (checked) {
    if (pinned.length >= 4) return;
    pinned.push(id);
  } else {
    pinned = pinned.filter((p) => p !== id);
  }
  compareBtn.classList.toggle("hidden", pinned.length < 2);
  compareBtn.textContent = compareMode ? "Back to table" : `Compare (${pinned.length})`;
  if (pinned.length < 2 && compareMode) {
    compareMode = false;
    renderView();
  } else {
    renderTable(); // repaint the cap/disabled state on the other checkboxes
  }
}

async function renderCompare() {
  note(compareEl, "Loading…");
  let records;
  try {
    records = await Promise.all(pinned.map((id) => getRecordCached(id)));
  } catch (err) {
    note(compareEl, `Could not load compare set: ${errorText(err)}`, "error");
    return;
  }
  if (!compareMode || !isOpen()) return;
  const present = new Set();
  for (const rec of records) {
    for (const key of Object.keys((rec && rec.properties) || {})) present.add(key);
  }
  const keys = Object.keys(materialsModel.PROPERTY_UNITS).filter((k) => present.has(k));
  const cols = materialsModel.compareRows(records, keys);

  compareEl.textContent = "";
  const table = document.createElement("table");
  table.className = "prop-table mat-table";
  const head = document.createElement("tr");
  const corner = document.createElement("th");
  corner.scope = "col";
  corner.textContent = "Property";
  head.appendChild(corner);
  for (const rec of records) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = rec.label || rec.id;
    head.appendChild(th);
  }
  table.appendChild(head);
  for (const row of cols) {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.scope = "row";
    th.textContent = row.unit && row.unit !== "-" ? `${row.label} (${row.unit})` : row.label;
    tr.appendChild(th);
    for (const v of row.values) tr.appendChild(textTd(v));
    table.appendChild(tr);
  }
  compareEl.appendChild(table);
}

// -------------------------------------------------------------- detail pane

async function selectDetail(id) {
  detailId = id;
  for (const tr of tableWrap.querySelectorAll(".mat-row")) {
    tr.classList.toggle("active", tr.dataset.id === id);
  }
  note(detailEl, "Loading…");
  let record;
  try {
    record = await getRecordCached(id);
  } catch (err) {
    if (detailId !== id) return;
    note(detailEl, `Could not load ${id}: ${errorText(err)}`, "error");
    return;
  }
  if (detailId !== id || !isOpen()) return;
  detailRecord = record;
  renderDetail(record);
}

function renderDetail(record) {
  detailEl.textContent = "";

  const head = document.createElement("div");
  head.className = "mat-detail-head";
  const name = document.createElement("div");
  name.className = "mat-detail-name";
  name.textContent = record.label || record.id;
  head.appendChild(name);
  const meta = document.createElement("div");
  meta.className = "mat-detail-meta";
  meta.textContent = [record.category, record.subcategory, record.condition]
    .filter(Boolean).join(" · ") || record.id;
  head.appendChild(meta);
  detailEl.appendChild(head);

  if (record.standards && record.standards.length) {
    detailEl.appendChild(section("Standards"));
    const wrap = document.createElement("div");
    wrap.className = "lib-badges";
    for (const std of record.standards) wrap.appendChild(badge(std));
    detailEl.appendChild(wrap);
  }

  const propertyKeys = Object.keys(materialsModel.PROPERTY_UNITS)
    .filter((k) => record.properties && record.properties[k]);
  if (propertyKeys.length) {
    detailEl.appendChild(section("Properties"));
    const uncited = new Set(record.uncited || []);
    for (const key of propertyKeys) {
      detailEl.appendChild(propertyRow(key, record.properties[key], uncited.has(key)));
    }
  }

  const process = record.process;
  if (process && Object.keys(process).length) {
    detailEl.appendChild(section("Process"));
    const wrap = document.createElement("div");
    wrap.className = "mat-chips";
    for (const key of Object.keys(PROCESS_RATED_LABELS)) {
      if (process[key]) {
        wrap.appendChild(staticChip(`${PROCESS_RATED_LABELS[key]}: ${process[key]}`));
      }
    }
    if (process.printable) {
      for (const key of Object.keys(PRINT_LABELS)) {
        if (process.printable[key]) {
          wrap.appendChild(staticChip(`${PRINT_LABELS[key]}: ${process.printable[key]}`));
        }
      }
    }
    if (process.sheet) wrap.appendChild(staticChip("sheet formable"));
    detailEl.appendChild(wrap);
    if (process.source) {
      const src = document.createElement("div");
      src.className = "mat-prov";
      src.textContent = `source: ${process.source}`;
      detailEl.appendChild(src);
    }
  }

  if (record.links && record.links.length) {
    detailEl.appendChild(section("Links"));
    const wrap = document.createElement("div");
    wrap.className = "mat-links";
    for (const link of record.links) {
      // The server refuses non-http(s) link urls; the renderer refuses them
      // again so a stale or hand-edited record can never become a
      // `javascript:` click (defence in depth, not the boundary).
      if (!/^https:\/\//i.test(String(link.url || ""))) continue;
      const a = document.createElement("a");
      a.href = link.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = link.label || link.url;
      wrap.appendChild(a);
    }
    detailEl.appendChild(wrap);
  }

  if (record.warnings && record.warnings.length) {
    detailEl.appendChild(section("Warnings"));
    const list = document.createElement("ul");
    list.className = "mat-warn";
    for (const w of record.warnings) {
      const li = document.createElement("li");
      li.textContent = w;
      list.appendChild(li);
    }
    detailEl.appendChild(list);
  }

  if (record.caveat) {
    const caveat = document.createElement("div");
    caveat.className = "mat-caveat";
    caveat.textContent = record.caveat;
    detailEl.appendChild(caveat);
  }

  if (assignTarget) {
    const use = document.createElement("button");
    use.type = "button";
    use.className = "tb-btn mat-use";
    use.textContent = `Use for ${assignTarget}`;
    use.addEventListener("click", async () => {
      use.disabled = true;
      await actions.assignMaterial(assignTarget, record.id);
      close();
    });
    detailEl.appendChild(use);
  }
}

function propertyRow(key, prop, uncited) {
  const row = document.createElement("div");
  row.className = "mat-prop-row";

  const top = document.createElement("div");
  top.className = "mat-prop-top";
  const label = document.createElement("span");
  label.className = "mat-prop-name";
  label.textContent = materialsModel.PROPERTY_LABELS[key] || key;
  const value = document.createElement("span");
  value.className = "mat-prop-value";
  value.textContent = materialsModel.formatProperty(prop);
  const b = materialsModel.basisBadge(uncited ? null : prop.basis);
  const badgeEl = document.createElement("span");
  badgeEl.className = `mat-badge ${b.cls}`;
  badgeEl.textContent = b.text;
  top.append(label, value, badgeEl);
  row.appendChild(top);

  if (prop.source) {
    const src = document.createElement("div");
    src.className = "mat-prov";
    src.textContent = `source: ${prop.source}${prop.as_of ? ` (as of ${prop.as_of})` : ""}`;
    row.appendChild(src);
  }

  if (prop.table && prop.table.length) {
    const table = document.createElement("table");
    table.className = "prop-table mat-t-table";
    const head = document.createElement("tr");
    const th1 = document.createElement("th");
    th1.scope = "col";
    th1.textContent = "T (°C)";
    const th2 = document.createElement("th");
    th2.scope = "col";
    th2.textContent = prop.unit || "value";
    head.append(th1, th2);
    table.appendChild(head);
    for (const [t, v] of prop.table) {
      const tr = document.createElement("tr");
      tr.appendChild(textTd(String(t)));
      tr.appendChild(textTd(String(v)));
      table.appendChild(tr);
    }
    row.appendChild(table);
  }

  return row;
}

function section(text) {
  const el = document.createElement("div");
  el.className = "lib-section";
  el.textContent = text;
  return el;
}

function badge(text) {
  const el = document.createElement("span");
  el.className = "lib-badge";
  el.textContent = text;
  return el;
}

function staticChip(text) {
  const el = document.createElement("span");
  el.className = "mat-chip static";
  el.textContent = text;
  return el;
}

function note(host, text, cls) {
  host.textContent = "";
  const el = document.createElement("div");
  el.className = cls ? `mat-empty ${cls}` : "mat-empty";
  el.textContent = text;
  host.appendChild(el);
}

function fmtCell(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return String(Math.round(v * 1e6) / 1e6);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}
