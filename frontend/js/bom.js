// BOM view (PRD-015 Slice 6, Decision 11): the assembly's bill of materials —
// one row per rolled-up part, footer totals, CSV/JSON export, and inline
// edits for the manual bom inputs (part_number / unit_cost_usd / source) via
// `api.patchBom`.
//
// A standalone module (not wired through main.js's `actions`): index.html
// bootstraps it directly alongside releases.js, both importing the same
// `state.js` singleton main.js already populates so `state.projectName`
// reads the live project with no cooperation required from main.js. Modal
// chrome and interaction follow the versions.js/configs.js precedent:
// overlay/close/backdrop/Escape, every node built with createElement +
// textContent, and a `loadSeq` guard drops an out-of-order fetch the way
// proposals.js's `loadSeq` does.
//
// Honesty (design Decision 2): `mass_source`/`cost_source` are never
// silently rendered as if they were measured. A `material_estimate` cost
// carries an "(est)" tag and an `unbuilt`/`stale` mass carries the matching
// tag, so nobody reads a guess as a fact.

import { api, ApiError } from "./api.js";
import { state, onKeys } from "./state.js";

let overlayEl, titleEl, bodyEl, footEl, closeBtn, csvLink, jsonLink;
let structureSel;

let loadSeq = 0;
let bom = null;       // last-loaded get_bom payload, or null
let loadError = null;

export function init() {
  overlayEl = document.getElementById("bom-modal");
  titleEl = document.getElementById("bom-title");
  bodyEl = document.getElementById("bom-body");
  footEl = document.getElementById("bom-foot");
  closeBtn = document.getElementById("bom-close");
  csvLink = document.getElementById("bom-download-csv");
  jsonLink = document.getElementById("bom-download-json");
  structureSel = document.getElementById("bom-structure");

  closeBtn.addEventListener("click", close);
  overlayEl.addEventListener("click", (e) => {
    if (e.target === overlayEl) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen()) close();
  });
  structureSel.addEventListener("change", refresh);

  // A branch switch or a project-wide rebuild can move mass/cost under a BOM
  // that is currently open — re-read rather than let it go stale silently.
  onKeys(["project", "projectName"], () => {
    if (isOpen()) refresh();
  });
}

export function isOpen() {
  return overlayEl && !overlayEl.classList.contains("hidden");
}

export async function open() {
  if (!state.projectName) {
    toast("Open a project first", "error");
    return;
  }
  overlayEl.classList.remove("hidden");
  titleEl.textContent = `${state.projectName} · bill of materials`;
  bodyEl.textContent = "";
  footEl.textContent = "";
  const loading = document.createElement("div");
  loading.className = "ver-empty";
  loading.textContent = "Loading BOM…";
  bodyEl.appendChild(loading);
  await refresh();
}

function close() {
  overlayEl.classList.add("hidden");
  bodyEl.textContent = "";
  footEl.textContent = "";
}

function exportParams() {
  return { structure: structureSel.value || "flat" };
}

async function refresh() {
  const proj = state.projectName;
  const seq = ++loadSeq;
  let payload;
  try {
    payload = await api.getBom(proj, exportParams());
  } catch (err) {
    if (seq !== loadSeq) return;
    bom = null;
    loadError = errorText(err);
    render();
    return;
  }
  if (seq !== loadSeq || proj !== state.projectName) return;
  bom = payload;
  loadError = null;
  render();
  updateExportLinks();
}

function updateExportLinks() {
  const proj = state.projectName;
  const params = exportParams();
  csvLink.href = api.bomCsvUrl(proj, params);
  jsonLink.href = api.bomJsonUrl(proj, params);
  csvLink.classList.remove("hidden");
  jsonLink.classList.remove("hidden");
}

function render() {
  bodyEl.textContent = "";
  footEl.textContent = "";
  if (loadError) {
    const el = document.createElement("div");
    el.className = "ver-empty";
    el.textContent = `Could not load the BOM: ${loadError}`;
    bodyEl.appendChild(el);
    return;
  }
  if (!bom || !bom.lines || !bom.lines.length) {
    const el = document.createElement("div");
    el.className = "ver-empty";
    el.textContent =
      "No parts in the assembly yet — add an instance to see a BOM line.";
    bodyEl.appendChild(el);
    return;
  }

  if (bom.warnings && bom.warnings.length) {
    const warn = document.createElement("div");
    warn.className = "bom-warnings";
    warn.textContent =
      `${bom.warnings.length} part(s) report an unbuilt/stale mass — ` +
      "their totals use the last-known value, if any.";
    bodyEl.appendChild(warn);
  }

  const table = document.createElement("table");
  table.className = "prop-table bom-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of [
    "#", "Qty", "Part #", "Name", "Config", "Material",
    "Unit mass", "Unit cost", "Ext. cost", "Source",
  ]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const line of bom.lines) {
    tbody.appendChild(renderRow(line));
  }
  table.appendChild(tbody);
  bodyEl.appendChild(table);

  footEl.textContent = "";
  const totals = bom.totals || {};
  const massTotal = document.createElement("span");
  massTotal.textContent = `Total mass: ${fmtMass(totals.mass_g)}`;
  const costTotal = document.createElement("span");
  costTotal.textContent = `Total cost: ${fmtCost(totals.cost_usd)}`;
  footEl.append(massTotal, costTotal);
  if (bom.generated_ref) {
    const ref = document.createElement("span");
    ref.className = "bom-ref";
    ref.textContent = `as of ${bom.generated_ref}`;
    footEl.appendChild(ref);
  }
}

function renderRow(line) {
  const row = document.createElement("tr");
  row.dataset.partId = line.part_id;
  if (typeof line.level === "number") {
    row.style.paddingLeft = `${line.level * 12}px`;
  }

  row.appendChild(cell(String(line.item ?? "")));
  row.appendChild(cell(String(line.qty ?? "")));
  row.appendChild(editableCell(line, "part_number", line.part_number || ""));
  row.appendChild(cell(line.label || line.part_id || ""));
  row.appendChild(cell(line.config || ""));
  row.appendChild(cell(line.material || ""));
  row.appendChild(cell(fmtMass(line.unit_mass_g), massTag(line.mass_source)));
  row.appendChild(editableCell(line, "unit_cost_usd",
    line.unit_cost_usd != null ? String(line.unit_cost_usd) : "",
    costTag(line.cost_source)));
  row.appendChild(cell(fmtCost(line.ext_cost_usd)));
  // The "Source" column edits `url` on the record — `set_bom_fields` has no
  // field literally named `source` (that name is the BOM line's DERIVED
  // value: a manual url, else a reference part's import path, else an
  // inherited package vendor url). No `apiField` override here, so
  // `commitEdit` PATCHes `url` (the 3rd arg is the display value, not a
  // field-name alias).
  row.appendChild(editableCell(line, "url", line.source || ""));

  return row;
}

function cell(text, tag) {
  const td = document.createElement("td");
  td.textContent = text;
  if (tag) {
    const span = document.createElement("span");
    span.className = "bom-tag";
    span.textContent = tag;
    td.appendChild(span);
  }
  return td;
}

/** An inline-editable cell (`field` is the exact `set_bom_fields` key —
 *  `part_number` / `unit_cost_usd` / `url`): a plain text/number input,
 *  PATCHed on `change` (blur/Enter, never per keystroke — the
 *  `inspector.js` text-field convention). */
function editableCell(line, field, value, tag) {
  const td = document.createElement("td");
  const input = document.createElement("input");
  input.className = "bom-edit";
  input.type = field === "unit_cost_usd" ? "number" : "text";
  if (field === "unit_cost_usd") {
    input.step = "any";
    input.min = "0";
  }
  input.value = value;
  input.addEventListener("change", () => commitEdit(line, field, input));
  td.appendChild(input);
  if (tag) {
    const span = document.createElement("span");
    span.className = "bom-tag";
    span.textContent = tag;
    td.appendChild(span);
  }
  return td;
}

async function commitEdit(line, field, input) {
  const proj = state.projectName;
  let value = input.value.trim();
  const body = {};
  if (field === "unit_cost_usd") {
    if (value === "") {
      // No route/tool support for CLEARING a manual cost in v1 — leaving the
      // field blank simply resubmits nothing rather than lying about a 0.
      return;
    }
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0) {
      toast("Unit cost must be a non-negative number", "error");
      return;
    }
    body.unit_cost_usd = n;
  } else {
    body[field] = value;
  }
  input.disabled = true;
  try {
    await api.patchBom(proj, line.part_id, body);
  } catch (err) {
    toast(`Could not save: ${errorText(err)}`, "error");
    input.disabled = false;
    return;
  }
  input.disabled = false;
  if (proj === state.projectName) await refresh();
}

function massTag(source) {
  if (source === "unbuilt") return "(unbuilt)";
  if (source === "stale") return "(stale)";
  return null;
}

function costTag(source) {
  if (source === "material_estimate") return "(est)";
  if (source === "none") return "(none)";
  return null;
}

function fmtMass(g) {
  if (g == null) return "—";
  return g >= 1000 ? `${(g / 1000).toFixed(3)} kg` : `${g.toFixed(1)} g`;
}

function fmtCost(usd) {
  if (usd == null) return "—";
  return `$${usd.toFixed(2)}`;
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}

// A tiny local toast, targeting the same #toasts host main.js uses — this
// module is bootstrapped independently (see the header note) so it cannot
// import main.js's `actions.toast`.
function toast(message, kind = "info") {
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${kind === "error" ? "error" : ""}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 8000 : 4000);
}
