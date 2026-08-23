// PRD-027 FR5 — the bulk bar: the strip under the filter box that appears the
// moment more than one part is selected, and the results dialog a run with
// failed rows opens.
//
// The division of labour with `tree.js` is deliberate and one-directional.
// The four verbs a *row* can also do — Tags…, Move to folder…, Export…,
// Delete… — live in `tree.js` and are imported here, so the context menu and
// this strip are two affordances for one implementation. This module owns
// exactly two things nothing else does: the **Material** verb (there is no
// per-row material item — the inspector's picker is that) and the **results
// dialog**. Nothing in `tree.js` imports this file, so there is no cycle; the
// results dialog is handed over at init through `setBulkFailureReporter`.
//
// Why a dialog and not toasts (design §7, Risk 4): a six-part op with two
// refusals is two failures a person has to read, compare and act on. Six
// toasts stack, expire on their own timers and cannot be re-read. The dialog
// is NON-MODAL — the workbench stays usable behind it, because "three of these
// six did not export" is information, not a decision.
//
// Pure helpers (`bulkLabel`, `resultRows`, `resultSummary`) are exported and
// node-tested: they are the strings a person actually reads, and a browser is
// the wrong place to grade a plural.

import { state, setState, onKeys } from "./state.js";
import * as dialogs from "./shell/dialogs.js";
import {
  runBulk, setBulkFailureReporter, editTags, moveParts, exportParts,
  deleteParts,
} from "./tree.js";

let actions = null;
let host = null;

/** The verbs the strip offers, in the order it offers them. `run` takes the
 *  selected ids; `danger` gets the destructive styling. */
const VERBS = [
  {id: "material", label: "Material", run: (ids) => setMaterial(ids)},
  {id: "tags", label: "Tags", run: (ids) => editTags(ids)},
  {id: "folder", label: "Folder", run: (ids) => moveParts(ids)},
  {id: "export", label: "Export", run: (ids) => exportParts(ids)},
  {id: "delete", label: "Delete", danger: true, run: (ids) => deleteParts(ids)},
];

export function init(a) {
  actions = a;
  host = document.getElementById("bulk-bar");
  // `tree.js` calls this when a run comes back with any `ok: false` row.
  setBulkFailureReporter(showResults);
  dialogs.register("bulk-results", (args) => showResults(args || {}), {
    title: "Bulk operation results",
    description: "Which parts a bulk operation applied to, and why the rest "
                 + "did not",
    // A response to a call, never something to conjure: opening it out of
    // context would show an empty table that claims a run happened.
    agentOpenable: false,
    when: () => false,
  });
  onKeys(["selection", "project"], render);
  render();
}

/** The ids the strip acts on — the live selection, in nothing but its own
 *  order (the server de-duplicates and keeps order). */
function selectedIds() {
  return [...state.selection];
}

// ------------------------------------------------------------------- strip

/** `6 selected`. Its own function because it is the one string in this module
 *  a person reads on every multi-select, and a plural is worth a test. */
export function bulkLabel(n) {
  const count = Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0;
  return `${count} selected`;
}

function render() {
  if (!host) return;
  const ids = selectedIds();
  // One selected part is not a bulk: the row itself, the inspector and the
  // context menu all already act on it, and a strip that appeared for every
  // click would be a permanent 28 px of chrome.
  if (ids.length < 2) {
    host.classList.add("hidden");
    host.textContent = "";
    return;
  }
  host.textContent = "";
  host.classList.remove("hidden");

  const count = document.createElement("span");
  count.className = "bulk-count";
  count.textContent = bulkLabel(ids.length);
  host.appendChild(count);

  for (const verb of VERBS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = verb.danger ? "bulk-btn danger" : "bulk-btn";
    btn.dataset.op = verb.id;
    btn.textContent = verb.label;
    btn.title = `${verb.label} — ${bulkLabel(ids.length)}`;
    btn.addEventListener("click", () => run(verb.id));
    host.appendChild(btn);
  }

  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "bulk-clear";
  clear.textContent = "×";
  clear.title = "Clear the selection";
  clear.setAttribute("aria-label", "Clear the selection");
  clear.addEventListener("click", clearSelection);
  host.appendChild(clear);
}

/** Run one bulk verb by id — the strip's buttons and the `part.bulk.*`
 *  actions (menu, palette) both come through here. */
export function run(op) {
  const ids = selectedIds();
  if (ids.length < 2) {
    if (actions) actions.toast("Select more than one part first", "error");
    return null;
  }
  const verb = VERBS.find((v) => v.id === op);
  if (!verb) return null;
  return verb.run(ids);
}

/** Drop the multi-selection. The scalar primary is untouched: clearing a bulk
 *  selection is not "deselect the part I am looking at". */
export function clearSelection() {
  setState({selection: new Set(), selectionAnchor: null});
}

/** Is there a bulk selection to act on?
 *
 *  The `part.bulk.*` actions gate on `ctx.selectionSize > 1` instead — an
 *  eligibility predicate has to grade a synthetic context the same way it
 *  grades the live one, and only `actions.context()` can offer that. This is
 *  the live-state twin, for callers that already have no context to hand. */
export function hasSelection() {
  return state.selection.size > 1;
}

// ---------------------------------------------------------------- material

/** The one verb this module owns. There is no per-row material item — the
 *  inspector's picker is that — so this is the only place a material reaches
 *  many parts at once, and it goes through `bulk_part_op material`, which is
 *  ONE manifest write and one undo step (never N `update_part` calls). */
async function setMaterial(ids) {
  const catalog = (state.materials && state.materials.materials) || [];
  if (!catalog.length) {
    if (actions) {
      actions.toast("The material catalog has not loaded yet", "error");
    }
    return null;
  }
  const values = await dialogs.form({
    view: "bulk-material",
    title: `Material for ${ids.length} parts`,
    width: "narrow",
    fields: [{
      name: "material", label: "Material", type: "select", required: true,
      value: catalog[0].id,
      options: catalog.map((m) => ({value: m.id, label: m.label || m.id})),
      help: "Applied to every selected part in one undoable step. Each part "
            + "rebuilds afterwards — density feeds the mass metrics.",
    }],
    buttons: [{id: "cancel", label: "Cancel"},
              {id: "apply", label: "Apply", kind: "primary", submits: true}],
  });
  if (!values) return null;
  return runBulk(ids, "material", {material: values.material});
}

// --------------------------------------------------------- results dialog

/** One row per part the call carried, in call order: `{id, status, detail}`.
 *
 *  Total over the payload: a result with no `results` array (a shape from an
 *  older server, a truncated response) answers an EMPTY table rather than
 *  throwing inside the dialog that was opened to explain a failure. */
export function resultRows(result) {
  const rows = (result && Array.isArray(result.results)) ? result.results : [];
  return rows.filter((r) => r && r.id != null).map((r) => ({
    id: String(r.id),
    status: r.ok === false ? "failed" : "ok",
    // The one column that says something per row: the refusal for a failed
    // one, the written file for a successful export, nothing otherwise.
    detail: r.ok === false && r.error
      ? `${r.error.type || "error"}: ${r.error.message || ""}`.trim()
      : (r.path ? String(r.path) : ""),
  }));
}

/** `4 of 6 parts applied · 2 failed` — the sentence over the table. */
export function resultSummary(result) {
  const rows = resultRows(result);
  const failed = rows.filter((r) => r.status === "failed").length;
  const applied = rows.length - failed;
  const op = (result && result.op) || "operation";
  const noun = `part${rows.length === 1 ? "" : "s"}`;
  return failed
    ? `${op}: ${applied} of ${rows.length} ${noun} applied · ${failed} failed`
    : `${op}: ${applied} of ${rows.length} ${noun} applied`;
}

/** The non-modal results table. Built as DOM, never as an HTML string: the
 *  cells carry part ids and server error messages, neither of which this
 *  module is allowed to interpolate as markup. */
export function showResults(result) {
  const rows = resultRows(result);
  const wrap = document.createElement("div");
  wrap.className = "bulk-results";

  const summary = document.createElement("p");
  summary.className = "bulk-results-summary";
  summary.textContent = resultSummary(result);
  wrap.appendChild(summary);

  const table = document.createElement("table");
  table.className = "bulk-results-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const text of ["Part", "Status", "Detail"]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = text;
    headRow.appendChild(th);
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    if (row.status === "failed") tr.className = "failed";
    const id = document.createElement("td");
    id.className = "bulk-results-id";
    id.textContent = row.id;
    const status = document.createElement("td");
    status.textContent = row.status === "failed" ? "failed" : "ok";
    const detail = document.createElement("td");
    detail.className = "bulk-results-detail";
    detail.textContent = row.detail;
    tr.append(id, status, detail);
    body.appendChild(tr);
  }
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.textContent = "No rows were reported.";
    tr.appendChild(td);
    body.appendChild(tr);
  }
  table.appendChild(body);
  wrap.appendChild(table);

  return dialogs.open({
    view: "bulk-results",
    // NON-modal: what failed is information, and the workbench behind it stays
    // usable while the reader goes and looks at one of the named parts.
    modal: false,
    title: "Bulk operation results",
    body: wrap,
    width: "wide",
    buttons: [{id: "ok", label: "Close", kind: "primary"}],
  });
}
