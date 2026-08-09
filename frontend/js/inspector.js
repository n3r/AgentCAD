// Right inspector: Parameters / Code / Metrics tabs, the rebuild error
// banner, debounced live param editing, and the script save flow.

import { api, ApiError } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import * as editor from "./editor.js";

let actions = null;

let panes = {};
let tabs = [];
let codeTab = null;
let activeTab = "params";

let bannerEl, bannerTitle, bannerBody;
let paramsPane, metricsPane;

let renderedPartId = null;
let renderedSpecJson = null;

// The material <select> is preserved across metric re-renders so a rebuild
// (which fires often) can't tear it down while the user has the dropdown open.
let materialBlockEl = null;
let materialSig = null;

// analysis results survive metric re-renders (a param edit shouldn't wipe them)
let analysisPartId = null;
let analysisResults = {}; // kind -> {loading} | {data} | {error}

const CATEGORY_ORDER = ["metal", "polymer", "composite", "wood", "masonry"];

// debounced param patching
let pending = {};
// name -> count of PATCHes queued/awaiting a response for that param. Kept
// separate from `pending` so syncParamValues doesn't snap an input back to
// the pre-patch value between the debounce flush and the server's reply.
let inflight = {};
let pendingPartId = null;
let debounceTimer = null;
let patchChain = Promise.resolve();
let dismissedError = null; // JSON of an error the user closed by hand

export function init(a) {
  actions = a;
  paramsPane = document.getElementById("pane-params");
  metricsPane = document.getElementById("pane-metrics");
  panes = {
    params: paramsPane,
    code: document.getElementById("pane-code"),
    metrics: metricsPane,
  };
  tabs = [...document.querySelectorAll("#tabs .tab")];
  codeTab = document.querySelector('#tabs .tab[data-tab="code"]');
  for (const tab of tabs) {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  }

  bannerEl = document.getElementById("banner");
  bannerTitle = document.getElementById("banner-title");
  bannerBody = document.getElementById("banner-body");
  document.getElementById("banner-close").addEventListener("click", dismissBanner);

  editor.init(document.getElementById("editor-host"), { onSave: saveScript });

  onKeys(["part"], render);
  // Materials can arrive after the part; refresh the material block when they do.
  onKeys(["materials"], () => {
    if (state.part) renderMetrics(state.part);
  });
  render();
}

export function setTab(name) {
  activeTab = name;
  for (const tab of tabs) tab.classList.toggle("active", tab.dataset.tab === name);
  for (const [key, pane] of Object.entries(panes)) {
    pane.classList.toggle("hidden", key !== name);
  }
  if (name === "code") editor.refresh();
}

export function isCodeTabActive() {
  return activeTab === "code";
}

// ------------------------------------------------------------------ banner

export function showBanner(error) {
  const err = error || {};
  dismissedError = null;
  bannerTitle.textContent = err.type || "error";
  const details = err.details || {};
  let body = err.message || "unknown error";
  if (details.traceback) body = details.traceback;
  if (details.line != null) body += `\n(line ${details.line})`;
  bannerBody.textContent = body;
  bannerEl.classList.remove("hidden");
}

export function hideBanner() {
  bannerEl.classList.add("hidden");
}

function dismissBanner() {
  // remember what was dismissed so a re-render doesn't resurrect it
  if (state.part && state.part.status && state.part.status.error) {
    dismissedError = JSON.stringify(state.part.status.error);
  }
  hideBanner();
}

// ------------------------------------------------------------------ render

function render() {
  const part = state.part;
  if (!part) {
    renderedPartId = null;
    renderedSpecJson = null;
    paramsPane.innerHTML = '<div class="pane-note">Select a part to edit its parameters.</div>';
    metricsPane.innerHTML = '<div class="pane-note">Select a part to see its metrics.</div>';
    editor.setPart(null, "");
    hideBanner();
    return;
  }

  const isReference = part.kind === "reference";
  // References have no script: hide the Code tab and never leave it active.
  if (codeTab) codeTab.classList.toggle("hidden", isReference);
  if (isReference && activeTab === "code") setTab("params");

  const specJson = JSON.stringify(part.params_spec || null);
  if (part.id !== renderedPartId || specJson !== renderedSpecJson) {
    renderedPartId = part.id;
    renderedSpecJson = specJson;
    pending = {};
    inflight = {};
    pendingPartId = part.id;
    if (isReference) buildReferencePane(part);
    else buildParamControls(part);
  } else if (!isReference) {
    syncParamValues(part);
  }
  renderWarnings(part);
  renderMetrics(part);
  editor.setPart(isReference ? null : part.id, isReference ? "" : part.script);

  if (part.status && part.status.state === "error" && part.status.error) {
    if (JSON.stringify(part.status.error) !== dismissedError) {
      showBanner(part.status.error);
    }
  } else {
    hideBanner();
  }
}

function effectiveValue(part, name, spec) {
  const v = part.params ? part.params[name] : undefined;
  return v !== undefined ? v : spec.default;
}

function niceStep(min, max) {
  const span = max - min;
  const raw = span / 200;
  const decade = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-6))));
  for (const m of [1, 2, 5]) {
    if (decade * m >= raw) return decade * m;
  }
  return decade * 10;
}

function buildParamControls(part) {
  paramsPane.textContent = "";
  const spec = part.params_spec;
  if (!spec || !Object.keys(spec).length) {
    paramsPane.innerHTML =
      '<div class="pane-note">This part exposes no parameters. Define a PARAMS dict in the script.</div>';
    appendWarningsHost();
    return;
  }
  for (const [name, entry] of Object.entries(spec)) {
    const wrap = document.createElement("div");
    wrap.className = "param";
    wrap.dataset.param = name;

    const head = document.createElement("div");
    head.className = "param-head";
    const label = document.createElement("span");
    label.className = "param-name";
    label.textContent = name;
    head.appendChild(label);
    if (entry.unit) {
      const unit = document.createElement("span");
      unit.className = "param-unit";
      unit.textContent = entry.unit;
      head.appendChild(unit);
    }
    wrap.appendChild(head);

    const ctl = document.createElement("div");
    ctl.className = "param-ctl";
    const value = effectiveValue(part, name, entry);
    const hasRange = entry.min != null && entry.max != null;

    let slider = null;
    if (hasRange) {
      slider = document.createElement("input");
      slider.type = "range";
      slider.min = entry.min;
      slider.max = entry.max;
      slider.step = niceStep(entry.min, entry.max);
      slider.value = value;
      slider.setAttribute("aria-label", name);
      ctl.appendChild(slider);
    }

    const num = document.createElement("input");
    num.type = "number";
    num.className = "param-num";
    num.step = "any";
    if (entry.min != null) num.min = entry.min;
    if (entry.max != null) num.max = entry.max;
    num.value = value;
    num.setAttribute("aria-label", `${name} value`);
    ctl.appendChild(num);
    wrap.appendChild(ctl);

    if (entry.description) {
      const desc = document.createElement("div");
      desc.className = "param-desc";
      desc.textContent = entry.description;
      wrap.appendChild(desc);
    }

    if (slider) {
      slider.addEventListener("input", () => {
        num.value = slider.value;
        queueParam(name, parseFloat(slider.value));
      });
    }
    num.addEventListener("input", () => {
      const v = parseFloat(num.value);
      if (!Number.isFinite(v)) return;
      if (slider) slider.value = v;
      queueParam(name, v);
    });

    paramsPane.appendChild(wrap);
  }
  appendWarningsHost();
}

function appendWarningsHost() {
  const w = document.createElement("div");
  w.className = "param-warnings";
  w.id = "param-warnings";
  paramsPane.appendChild(w);
}

// Reference (imported) parts have no editable parameters; show provenance in
// the Parameters pane instead so the pane is never blank.
function buildReferencePane(part) {
  paramsPane.textContent = "";
  const block = document.createElement("div");
  block.className = "ref-block";

  const head = document.createElement("div");
  head.className = "ref-head";
  const badge = document.createElement("span");
  badge.className = "ref-badge";
  badge.textContent = "ref";
  const kind = document.createElement("span");
  kind.className = "ref-kind";
  kind.textContent = "imported CAD";
  head.append(badge, kind);
  block.appendChild(head);

  block.appendChild(kv("Source", part.source || "—"));
  const ext = (part.source || "").split(".").pop().toLowerCase();
  block.appendChild(kv("Format", ext ? `.${ext}` : "—"));

  if (part.metrics && part.metrics.mesh) {
    const flag = document.createElement("div");
    flag.className = "ref-flag";
    flag.textContent =
      "Mesh-only (STL): measured and placeable, but it can't take part in booleans.";
    block.appendChild(flag);
  }

  const note = document.createElement("div");
  note.className = "pane-note";
  note.style.padding = "12px 0 0";
  note.textContent =
    "Imported references have no script or parameters. Set the material and " +
    "run analyses from the Metrics tab.";
  block.appendChild(note);

  paramsPane.appendChild(block);
  appendWarningsHost();
}

function kv(key, value) {
  const row = document.createElement("div");
  row.className = "ref-row";
  const k = document.createElement("span");
  k.className = "ref-key";
  k.textContent = key;
  const v = document.createElement("span");
  v.className = "ref-val";
  v.textContent = value;
  row.append(k, v);
  return row;
}

function syncParamValues(part) {
  const spec = part.params_spec || {};
  for (const wrap of paramsPane.querySelectorAll(".param")) {
    const name = wrap.dataset.param;
    if (!(name in spec)) continue;
    // The user's edit is still debouncing or awaiting the PATCH response —
    // don't snap the control back to the server's stale value.
    if (name in pending || name in inflight) continue;
    const value = effectiveValue(part, name, spec[name]);
    for (const input of wrap.querySelectorAll("input")) {
      if (document.activeElement === input) continue;
      input.value = value;
    }
  }
}

function renderWarnings(part) {
  const host = document.getElementById("param-warnings");
  if (!host) return;
  host.textContent = "";
  const warnings = (part.status && part.status.warnings) || [];
  for (const w of warnings) {
    const div = document.createElement("div");
    div.textContent = `⚠ ${w}`;
    host.appendChild(div);
  }
}

// ------------------------------------------------------------- param patch

function queueParam(name, value) {
  pending[name] = value;
  pendingPartId = state.part ? state.part.id : null;
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(flushParams, 250);
}

function flushParams() {
  const values = pending;
  const partId = pendingPartId;
  pending = {};
  const names = Object.keys(values);
  if (!names.length || !partId) return;
  const proj = state.projectName;
  // Reference-count: a later flush may re-send a name before the earlier
  // PATCH resolves, and each response must only release its own claim.
  for (const n of names) inflight[n] = (inflight[n] || 0) + 1;
  const releaseInflight = () => {
    for (const n of names) {
      if (inflight[n] > 1) inflight[n] -= 1;
      else delete inflight[n];
    }
  };
  patchChain = patchChain.then(async () => {
    try {
      const result = await api.patchParams(proj, partId, values);
      applyRebuildResult(partId, result, values);
    } catch (err) {
      showBanner(err instanceof ApiError ? err.error : { message: String(err) });
    } finally {
      releaseInflight();
    }
  });
}

function applyRebuildResult(partId, result, values) {
  const isCurrent = state.part && state.part.id === partId;
  if (result.ok) {
    if (isCurrent) {
      Object.assign(state.part.params, values || {});
      state.part.metrics = result.metrics;
      state.part.status = { state: "ok", error: null, warnings: result.warnings || [] };
      setState({ part: state.part });
      hideBanner();
    }
    actions.markPartState(partId, "ok");
    actions.reloadMesh(partId);
  } else {
    if (isCurrent) {
      state.part.status = {
        state: "error",
        error: result.error,
        warnings: [],
      };
      if (values) Object.assign(state.part.params, values);
      setState({ part: state.part });
      showBanner(result.error);
    }
    actions.markPartState(partId, "error");
  }
}

// -------------------------------------------------------------- save flow

async function saveScript(partId, script) {
  editor.setSaving(true);
  try {
    const result = await api.updatePart(state.projectName, partId, { script });
    // The server persists the script either way; the editor now matches disk.
    editor.markSaved(script);
    if (state.part && state.part.id === partId) {
      state.part.script = script;
    }
    applyRebuildResult(partId, result);
    if (result.ok) {
      actions.toast("Saved — rebuild ok");
      actions.refreshPartDetail(partId); // pick up a possibly changed PARAMS spec
    }
  } catch (err) {
    showBanner(err instanceof ApiError ? err.error : { message: String(err) });
  } finally {
    editor.setSaving(false);
  }
}

export function saveIfDirty() {
  if (state.part && editor.isDirty()) editor.save();
}

// --------------------------------------------------------------- metrics

const fmt = (v, digits = 2) => {
  if (v == null || !Number.isFinite(v)) return "—";
  if (Object.is(v, -0) || Math.abs(v) < 5e-7) v = 0;
  const abs = Math.abs(v);
  if (abs >= 10000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (abs >= 100) return v.toLocaleString("en-US", { maximumFractionDigits: 1 });
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
};

function row(key, valueHtml) {
  return `<tr><td class="metric-key">${key}</td><td class="metric-val">${valueHtml}</td></tr>`;
}

function renderMetrics(part) {
  if (part.id !== analysisPartId) {
    analysisPartId = part.id;
    analysisResults = {};
  }
  metricsPane.textContent = "";
  // Reuse the existing material block unless the part, its material, or the
  // catalog actually changed — never rebuild a possibly-open <select>.
  const catalog = (state.materials && state.materials.materials) || [];
  const sig = JSON.stringify([part.id, part.material, catalog.map((m) => m.id)]);
  if (!materialBlockEl || sig !== materialSig) {
    materialBlockEl = materialBlock(part);
    materialSig = sig;
  }
  metricsPane.appendChild(materialBlockEl);
  metricsPane.appendChild(metricsTable(part));
  metricsPane.appendChild(analysisBlock(part));
}

function metricsTable(part) {
  const host = document.createElement("div");
  const m = part.metrics;
  if (!m) {
    host.innerHTML =
      '<div class="pane-note">No metrics yet — the part has not built successfully.</div>';
    return host;
  }
  const dims = m.bbox ? [0, 1, 2].map((i) => m.bbox.max[i] - m.bbox.min[i]) : null;
  const com = m.center_of_mass;
  const mass =
    m.mass_g >= 1000
      ? `${fmt(m.mass_g / 1000)}<span class="unit">kg</span>`
      : `${fmt(m.mass_g)}<span class="unit">g</span>`;
  const stale = part.status && part.status.state === "error";

  host.innerHTML = `
    <table class="metrics-table">
      ${stale ? row("Note", '<span class="bad">stale — last rebuild failed</span>') : ""}
      ${row("Volume", `${fmt(m.volume_mm3)}<span class="unit">mm³</span>`)}
      ${row("Mass", mass)}
      ${row("Area", `${fmt(m.area_mm2)}<span class="unit">mm²</span>`)}
      ${dims ? row("Bounding box", `${fmt(dims[0])} × ${fmt(dims[1])} × ${fmt(dims[2])}<span class="unit">mm</span>`) : ""}
      ${com ? row("Center of mass", `${fmt(com[0])}, ${fmt(com[1])}, ${fmt(com[2])}<span class="unit">mm</span>`) : ""}
      ${row("Validity", m.is_valid ? '<span class="ok">valid</span>' : '<span class="bad">invalid</span>')}
      ${row("Faces", fmt(m.n_faces, 0))}
      ${row("Edges", fmt(m.n_edges, 0))}
      ${row("Solids", fmt(m.n_solids, 0))}
    </table>`;
  return host;
}

// ------------------------------------------------------------- material

function materialBlock(part) {
  const box = document.createElement("div");
  box.className = "mat-block";

  const head = document.createElement("div");
  head.className = "mat-head";
  head.textContent = "Material";
  box.appendChild(head);

  const catalog = (state.materials && state.materials.materials) || [];
  const current = catalog.find((m) => m.id === part.material) || null;

  const select = document.createElement("select");
  select.className = "mat-select";
  select.id = "material-select";
  select.setAttribute("aria-label", "Part material");

  if (!catalog.length) {
    const opt = document.createElement("option");
    opt.value = part.material || "";
    opt.textContent = part.material || "—";
    opt.selected = true;
    select.appendChild(opt);
    select.disabled = true;
  } else {
    // Surface an unknown current id so the control still shows the truth.
    if (part.material && !current) {
      const opt = document.createElement("option");
      opt.value = part.material;
      opt.textContent = `${part.material} (unknown)`;
      opt.selected = true;
      select.appendChild(opt);
    }
    const cats = [...new Set(catalog.map((m) => m.category))].sort(
      (a, b) => catRank(a) - catRank(b)
    );
    for (const cat of cats) {
      const og = document.createElement("optgroup");
      og.label = cat;
      for (const m of catalog.filter((x) => x.category === cat)) {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.label;
        if (m.id === part.material) opt.selected = true;
        og.appendChild(opt);
      }
      select.appendChild(og);
    }
  }
  select.addEventListener("change", () => setMaterial(select.value));
  box.appendChild(select);

  if (current) {
    const props = document.createElement("div");
    props.className = "mat-props";
    props.appendChild(prop("Density", current.density_g_cm3, "g/cm³"));
    props.appendChild(prop("E", current.E_gpa, "GPa"));
    props.appendChild(prop("Yield", current.yield_mpa, "MPa"));
    props.appendChild(prop("Service", current.max_service_temp_c, "°C"));
    box.appendChild(props);

    const prov = document.createElement("div");
    prov.className = "mat-prov";
    prov.textContent = `source: ${current.source}`;
    box.appendChild(prov);
  }

  const caveat = state.materials && state.materials.caveat;
  if (caveat) {
    const c = document.createElement("div");
    c.className = "mat-caveat";
    c.textContent = caveat;
    box.appendChild(c);
  }
  return box;
}

function catRank(cat) {
  const i = CATEGORY_ORDER.indexOf(cat);
  return i < 0 ? CATEGORY_ORDER.length : i;
}

function prop(label, value, unit) {
  const cell = document.createElement("div");
  cell.className = "mat-prop";
  const l = document.createElement("span");
  l.className = "mat-prop-label";
  l.textContent = label;
  const v = document.createElement("span");
  v.className = "mat-prop-val";
  if (value == null || !Number.isFinite(value)) {
    v.textContent = "—";
  } else {
    v.innerHTML = `${fmt(value)}<span class="unit">${unit}</span>`;
  }
  cell.append(l, v);
  return cell;
}

async function setMaterial(id) {
  const part = state.part;
  if (!part || !id || id === part.material) return;
  const partId = part.id;
  try {
    const result = await api.updatePart(state.projectName, partId, { material: id });
    if (state.part && state.part.id === partId) {
      state.part.material = id;
      setState({ part: state.part });
    }
    // keep the sidebar's material tooltip in sync
    if (state.project) {
      const entry = state.project.parts.find((p) => p.id === partId);
      if (entry) {
        entry.material = id;
        setState({ project: state.project });
      }
    }
    applyRebuildResult(partId, result);
    if (result.ok) actions.toast(`Material set to ${id}`);
  } catch (err) {
    showBanner(err instanceof ApiError ? err.error : { message: String(err) });
  }
}

// ------------------------------------------------------------- analysis

function analysisBlock(part) {
  const isReference = part.kind === "reference";
  const box = document.createElement("div");
  box.className = "analysis-block";

  const head = document.createElement("div");
  head.className = "analysis-head";
  head.textContent = "Analysis";
  box.appendChild(head);

  const btns = document.createElement("div");
  btns.className = "analysis-btns";
  for (const [kind, label] of [
    ["section", "Section"],
    ["wall", "Wall thickness"],
    ["inertia", "Inertia"],
  ]) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "analysis-btn";
    b.textContent = label;
    if (isReference) {
      b.disabled = true;
      b.title = "Analysis is available for script parts only";
    } else {
      b.addEventListener("click", () => runAnalysis(kind));
    }
    btns.appendChild(b);
  }
  box.appendChild(btns);

  const results = document.createElement("div");
  results.className = "analysis-results";
  for (const kind of ["section", "wall", "inertia"]) {
    if (analysisResults[kind]) {
      results.appendChild(renderAnalysisResult(kind, analysisResults[kind]));
    }
  }
  box.appendChild(results);

  if (isReference) {
    const note = document.createElement("div");
    note.className = "analysis-note";
    note.textContent = "Import references can't be analyzed (script parts only).";
    box.appendChild(note);
  }
  return box;
}

async function runAnalysis(kind) {
  const part = state.part;
  if (!part) return;
  const partId = part.id;
  analysisResults[kind] = { loading: true };
  renderMetrics(part);
  // The /analyze route always forwards min_required, and the tool registry
  // rejects a null number — so send 0 (ignored for section/inertia; treated as
  // "no threshold" for wall, where we suppress the comparison row below).
  const body = { kind, min_required: 0 };
  if (kind === "section") body.plane = "XY";
  try {
    const data = await api.analyzePart(state.projectName, partId, body);
    if (!state.part || state.part.id !== partId) return;
    analysisResults[kind] =
      data && data.error ? { error: data.error.message || "analysis failed" } : { data };
  } catch (err) {
    if (!state.part || state.part.id !== partId) return;
    analysisResults[kind] = {
      error: err instanceof ApiError ? err.error.message : String(err),
    };
  }
  if (state.part && state.part.id === partId) renderMetrics(state.part);
}

const ANALYSIS_TITLES = {
  section: "Cross-section",
  wall: "Min wall thickness",
  inertia: "Mass properties",
};

function renderAnalysisResult(kind, r) {
  const card = document.createElement("div");
  card.className = "analysis-card";

  const title = document.createElement("div");
  title.className = "analysis-card-title";
  title.textContent = ANALYSIS_TITLES[kind] || kind;
  card.appendChild(title);

  const body = document.createElement("div");
  body.className = "analysis-card-body";

  if (r.loading) {
    body.innerHTML = '<span class="analysis-run">running…</span>';
  } else if (r.error) {
    body.innerHTML = `<span class="bad">${escapeHtml(r.error)}</span>`;
  } else {
    body.innerHTML = analysisRows(kind, r.data);
  }
  card.appendChild(body);
  return card;
}

function analysisRows(kind, d) {
  if (!d) return "—";
  if (kind === "section") {
    return arow("Area", `${fmt(d.area_mm2)}<span class="unit">mm²</span>`) +
      arow("Plane", d.plane) +
      arow("Faces", fmt(d.n_faces, 0));
  }
  if (kind === "wall") {
    if (d.min_thickness_mm == null) return arow("Result", "no wall detected");
    let out = arow("Min wall", `${fmt(d.min_thickness_mm)}<span class="unit">mm</span>`);
    if (d.location) {
      out += arow(
        "At",
        `${fmt(d.location[0])}, ${fmt(d.location[1])}, ${fmt(d.location[2])}<span class="unit">mm</span>`
      );
    }
    // Only show a comparison when a real threshold was requested (we send 0 to
    // satisfy the route's mandatory arg; 0 means "just report the thickness").
    if (d.ok != null && d.min_required_mm > 0) {
      out += arow(
        "vs required",
        d.ok
          ? '<span class="ok">meets ' + fmt(d.min_required_mm) + " mm</span>"
          : '<span class="bad">below ' + fmt(d.min_required_mm) + " mm</span>"
      );
    }
    return out;
  }
  if (kind === "inertia") {
    const t = d.inertia_tensor_g_mm2;
    const diag = t ? [t[0][0], t[1][1], t[2][2]] : null;
    let out = arow("Volume", `${fmt(d.volume_mm3)}<span class="unit">mm³</span>`);
    if (d.center_of_mass) {
      out += arow(
        "Center of mass",
        `${fmt(d.center_of_mass[0])}, ${fmt(d.center_of_mass[1])}, ${fmt(d.center_of_mass[2])}<span class="unit">mm</span>`
      );
    }
    if (diag) {
      out += arow(
        "Ixx, Iyy, Izz",
        `${fmt(diag[0])}, ${fmt(diag[1])}, ${fmt(diag[2])}<span class="unit">g·mm²</span>`
      );
    }
    return out;
  }
  return escapeHtml(JSON.stringify(d));
}

function arow(key, valueHtml) {
  return `<div class="analysis-row"><span class="analysis-k">${key}</span>` +
    `<span class="analysis-v">${valueHtml}</span></div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
