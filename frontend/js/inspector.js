// Right inspector: Parameters / Code / Metrics tabs, the rebuild error
// banner, debounced live param editing, and the script save flow.

import { api, ApiError } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import * as editor from "./editor.js";

let actions = null;

let panes = {};
let tabs = [];
let activeTab = "params";

let bannerEl, bannerTitle, bannerBody;
let paramsPane, metricsPane;

let renderedPartId = null;
let renderedSpecJson = null;

// debounced param patching
let pending = {};
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
  for (const tab of tabs) {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  }

  bannerEl = document.getElementById("banner");
  bannerTitle = document.getElementById("banner-title");
  bannerBody = document.getElementById("banner-body");
  document.getElementById("banner-close").addEventListener("click", dismissBanner);

  editor.init(document.getElementById("editor-host"), { onSave: saveScript });

  onKeys(["part"], render);
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

  const specJson = JSON.stringify(part.params_spec || null);
  if (part.id !== renderedPartId || specJson !== renderedSpecJson) {
    renderedPartId = part.id;
    renderedSpecJson = specJson;
    pending = {};
    pendingPartId = part.id;
    buildParamControls(part);
  } else {
    syncParamValues(part);
  }
  renderWarnings(part);
  renderMetrics(part);
  editor.setPart(part.id, part.script);

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

function syncParamValues(part) {
  const spec = part.params_spec || {};
  for (const wrap of paramsPane.querySelectorAll(".param")) {
    const name = wrap.dataset.param;
    if (!(name in spec)) continue;
    if (name in pending) continue; // user's edit is still in flight
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
  patchChain = patchChain.then(async () => {
    try {
      const result = await api.patchParams(proj, partId, values);
      applyRebuildResult(partId, result, values);
    } catch (err) {
      showBanner(err instanceof ApiError ? err.error : { message: String(err) });
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
  const m = part.metrics;
  if (!m) {
    metricsPane.innerHTML =
      '<div class="pane-note">No metrics yet — the part has not built successfully.</div>';
    return;
  }
  const dims = m.bbox
    ? [0, 1, 2].map((i) => m.bbox.max[i] - m.bbox.min[i])
    : null;
  const com = m.center_of_mass;
  const mass =
    m.mass_g >= 1000
      ? `${fmt(m.mass_g / 1000)}<span class="unit">kg</span>`
      : `${fmt(m.mass_g)}<span class="unit">g</span>`;
  const stale = part.status && part.status.state === "error";

  metricsPane.innerHTML = `
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
}
