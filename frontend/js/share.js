// PRD-007 slice 5: the anonymous share/customizer page controller.
//
// Served (as `share.html`) at `/s/<token>` and `/embed/<token>`. It reads the
// kernel-free `/model` + `/params` sidecars, renders the reused viewport, and —
// for a customizer link — drives `/variant?<params>` on slider input, degrading
// to view-only on a `quota_exceeded` (429). The share token stays in the URL:
// it IS the shareable artifact (unlike the one-time enrolment token PRD-005a
// strips with `history.replaceState`).
import * as vp from "./share-viewport.js";

const PART_ID = "shared";

// The token and the embed/standalone mode come from the PATH, not an injected
// flag — the server streams `share.html` verbatim, and the data routes live
// under `/s/` for both pages.
const PATH = window.location.pathname;
const EMBED = PATH.startsWith("/embed/");
const TOKEN = PATH.replace(/^\/(s|embed)\//, "").split("/")[0];
const BASE = `/s/${TOKEN}`;

const els = {};
let currentKey = null;
let debounceTimer = null;
let viewOnly = false;

function $(id) {
  return document.getElementById(id);
}

function setStatus(msg, kind = "") {
  if (els.status) {
    els.status.textContent = msg || "";
    els.status.className = `share-status ${kind}`;
  }
}

function fmtMetrics(metrics) {
  if (!metrics) return "";
  const bits = [];
  if (typeof metrics.mass_g === "number") {
    bits.push(`mass ${metrics.mass_g.toFixed(1)} g`);
  }
  const bb = metrics.bbox;
  if (bb && bb.min && bb.max) {
    const d = bb.max.map((v, i) => (v - bb.min[i]).toFixed(1));
    bits.push(`bbox ${d[0]} × ${d[1]} × ${d[2]} mm`);
  }
  return bits.join("  ·  ");
}

async function loadMesh(key) {
  if (!key || key === currentKey) return;
  const r = await fetch(`${BASE}/mesh/${key}`);
  if (!r.ok) return;
  const buf = await r.arrayBuffer();
  vp.showPart(PART_ID, buf, key);
  vp.fit();
  currentKey = key;
}

// ------------------------------------------------------------ param panel

function collectParams() {
  const out = {};
  for (const input of els.panel.querySelectorAll("[data-param]")) {
    const name = input.dataset.param;
    if (input.type === "checkbox") out[name] = input.checked;
    else out[name] = input.value;
  }
  return out;
}

function paramControl(name, entry) {
  const type = entry.type || "number";
  const row = document.createElement("label");
  row.className = "share-param";
  const title = document.createElement("span");
  title.className = "share-param-label";
  title.textContent = entry.description || name;
  if (entry.unit) title.textContent += ` (${entry.unit})`;
  row.appendChild(title);

  let input;
  if (type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(entry.default);
  } else if (type === "enum") {
    input = document.createElement("select");
    for (const choice of entry.choices || []) {
      const opt = document.createElement("option");
      opt.value = String(choice);
      opt.textContent = String(choice);
      if (choice === entry.default) opt.selected = true;
      input.appendChild(opt);
    }
  } else if (type === "string") {
    input = document.createElement("input");
    input.type = "text";
    if (entry.max_len) input.maxLength = entry.max_len;
    input.value = entry.default != null ? String(entry.default) : "";
  } else {
    // number / int → a clamped slider with a live readout.
    input = document.createElement("input");
    input.type = "range";
    if (entry.min != null) input.min = entry.min;
    if (entry.max != null) input.max = entry.max;
    input.step = type === "int" ? 1 : "any";
    input.value = entry.default != null ? entry.default : entry.min || 0;
    const out = document.createElement("output");
    out.textContent = input.value;
    input.addEventListener("input", () => (out.textContent = input.value));
    row.appendChild(out);
  }
  input.dataset.param = name;
  input.addEventListener("input", scheduleRebuild);
  input.addEventListener("change", scheduleRebuild);
  row.appendChild(input);
  return row;
}

async function buildPanel() {
  const r = await fetch(`${BASE}/params`);
  if (!r.ok) return;
  const spec = (await r.json()).params_spec || {};
  els.panel.innerHTML = "";
  const names = Object.keys(spec);
  if (!names.length) {
    els.panel.textContent = "This part has no adjustable parameters.";
    return;
  }
  for (const name of names) {
    els.panel.appendChild(paramControl(name, spec[name]));
  }
}

function scheduleRebuild() {
  if (viewOnly) return;
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(rebuild, 180); // local debounce, one build/stop
}

async function rebuild() {
  const params = new URLSearchParams(collectParams());
  setStatus("Rebuilding…");
  let r;
  try {
    r = await fetch(`${BASE}/variant?${params.toString()}`);
  } catch (e) {
    setStatus("Network error", "error");
    return;
  }
  if (r.status === 429) {
    let retry = 5;
    try {
      retry = (await r.json()).error.details.retry_after_s || retry;
    } catch (e) {
      /* keep default */
    }
    degradeToViewOnly(retry);
    return;
  }
  if (r.status === 401) {
    setStatus("This link now requires sign-in to keep customizing.", "error");
    return;
  }
  if (r.status === 422) {
    setStatus("That value is out of range for this part.", "error");
    return;
  }
  if (!r.ok) {
    setStatus("Could not rebuild that variant.", "error");
    return;
  }
  const data = await r.json();
  await loadMesh(data.mesh_key);
  const warn = (data.warnings || []).length
    ? `  ⚠ ${data.warnings.join("; ")}`
    : "";
  setStatus(fmtMetrics(data.metrics) + warn);
}

function degradeToViewOnly(retrySeconds) {
  viewOnly = true;
  for (const input of els.panel.querySelectorAll("[data-param]")) {
    input.disabled = true;
  }
  setStatus(
    `Rebuild limit reached — showing view-only. Retrying in ${retrySeconds}s…`,
    "error"
  );
  setTimeout(() => {
    viewOnly = false;
    for (const input of els.panel.querySelectorAll("[data-param]")) {
      input.disabled = false;
    }
    setStatus("You can adjust parameters again.");
  }, Math.max(1, retrySeconds) * 1000);
}

// -------------------------------------------------------------- downloads

function buildDownloads(exports) {
  els.downloads.innerHTML = "";
  if (!exports || !exports.length) return;
  for (const fmt of exports) {
    const btn = document.createElement("button");
    btn.className = "share-download";
    btn.textContent = `Download ${fmt.toUpperCase()}`;
    btn.addEventListener("click", () => {
      const params = new URLSearchParams(collectParams());
      // A plain navigation so the browser handles the file download and its
      // Content-Disposition; the token is already in the path.
      window.location.assign(`${BASE}/download/${fmt}?${params.toString()}`);
    });
    els.downloads.appendChild(btn);
  }
}

// ------------------------------------------------------------------- boot

async function boot() {
  els.status = $("share-status");
  els.panel = $("share-panel");
  els.downloads = $("share-downloads");
  els.attribution = $("share-attribution");
  const container = $("share-viewport");
  if (EMBED) document.body.classList.add("embed");

  vp.init(container);

  let model;
  try {
    const r = await fetch(`${BASE}/model`);
    if (!r.ok) throw new Error("not found");
    model = await r.json();
  } catch (e) {
    setStatus("This shared link is no longer available.", "error");
    return;
  }

  const a = model.attribution || {};
  if (els.attribution) {
    els.attribution.textContent = `${a.part_id || "part"} · ${
      a.ref ? a.ref.name : ""
    } · shared by ${a.created_by || "someone"}`;
  }

  await loadMesh(model.default_variant_key);
  setStatus(fmtMetrics(model.metrics));

  const settings = model.settings || {};
  buildDownloads(settings.exports);
  if (settings.customizer) {
    await buildPanel();
  } else if (els.panel) {
    els.panel.textContent = "";
    const controls = document.getElementById("share-controls");
    if (controls) controls.classList.add("view-only");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
