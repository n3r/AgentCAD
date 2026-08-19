// PRD-031a slice 5: the Marketplace UI — browse + listing pages.
//
// A self-contained view over the ANONYMOUS public catalog (`api.market*`), so a
// logged-out visitor can search the catalog, open a listing, drag a slider to
// rebuild a bounded variant server-side, and download a STEP — no account, no
// catalog code on their machine. It reuses PRD-007's customizer viewport
// (`share-viewport.js`: `parseACM`/`showPart`/`fit`) and models its slider panel
// + debounced rebuild + 429 degrade-to-view-only on `share.js`.
//
// It is entered from `main.js` boot on the `#market` hash BEFORE the auth gate
// (so browse works with no session); a "Market" toolbar button navigates here.
// Add-to-library is shown only to a signed-in visitor and calls the existing
// authenticated `add_package` + `use_part` routes (PRD-011 verbatim) — no new
// route, no new anonymous surface.
import { api, ApiError } from "./api.js";
import * as vp from "./share-viewport.js";

let identity = null; // {mode, principal} from auth.session(); principal gated
let actions = null; // main.js actions (toast); optional
let root = null;
let vpReady = false;
let searchTimer = null;
let rebuildTimer = null;
let currentKey = null;
let viewOnly = false;
let cur = null; // {name, version, part, spec} of the open listing part

const enc = encodeURIComponent;

// -------------------------------------------------------------------- entry

/** Activate the market view. Called from main.js when the route is `#market`,
 *  for both anonymous and signed-in visitors. `id` is the resolved identity (or
 *  null in a pure-anonymous hosted boot). */
export function enter(id, act) {
  identity = id || null;
  actions = act || null;
  for (const el of ["toolbar", "workspace", "chat-dock", "auth-view"]) {
    document.getElementById(el)?.classList.add("hidden");
  }
  root = document.getElementById("market-view");
  root.classList.remove("hidden");
  window.addEventListener("hashchange", route);
  route();
}

function signedIn() {
  return !!(identity && identity.principal);
}

function toast(msg, kind) {
  if (actions && actions.toast) actions.toast(msg, kind);
}

// -------------------------------------------------------------------- router

function parseHash() {
  // `#market` → browse; `#market/<name>` → latest; `#market/<name>@<version>`.
  const raw = window.location.hash.replace(/^#market\/?/, "");
  if (!raw) return { view: "browse" };
  const [name, version] = decodeURIComponent(raw).split("@");
  return { view: "listing", name, version: version || null };
}

function route() {
  const r = parseHash();
  if (r.view === "listing") openListing(r.name, r.version);
  else renderBrowse();
}

function go(hash) {
  window.location.hash = hash;
}

// -------------------------------------------------------------------- browse

async function renderBrowse() {
  cur = null;
  currentKey = null;
  root.innerHTML = `
    <header class="mkt-head">
      <button class="mkt-home" type="button" aria-label="Marketplace">
        Agent<b>CAD</b> <span class="mkt-tag">Marketplace</span></button>
      <input id="mkt-search" type="search" class="mkt-search"
             placeholder="Search the catalog…  e.g. NEMA 17, ISO 4762, bearing"
             aria-label="Search the catalog">
      <div class="mkt-head-right"></div>
    </header>
    <div class="mkt-scroll">
      <p class="mkt-intro">A seeded, read-only catalog of validated, parametric,
        standards-tagged components. Customize with sliders and download STEP,
        STL or 3MF — no account needed.</p>
      <div id="mkt-grid" class="mkt-grid" aria-busy="true">Loading…</div>
    </div>`;
  fillHeadRight(root.querySelector(".mkt-head-right"));
  root.querySelector(".mkt-home").onclick = () => go("#market");
  const box = root.querySelector("#mkt-search");
  box.oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(box.value.trim()), 220);
  };
  await loadGrid(null);
}

async function loadGrid(q) {
  const grid = document.getElementById("mkt-grid");
  if (!grid) return;
  try {
    let items;
    if (q) items = (await api.marketSearch({ q })).hits || [];
    else items = (await api.marketList()).packages || [];
    renderGrid(grid, items, q);
  } catch (e) {
    grid.textContent = "Could not load the catalog.";
  }
}

async function runSearch(q) {
  await loadGrid(q || null);
}

function renderGrid(grid, items, q) {
  grid.removeAttribute("aria-busy");
  grid.innerHTML = "";
  if (!items.length) {
    grid.innerHTML = `<p class="mkt-empty">No packages${
      q ? ` match “${escapeHtml(q)}”` : ""
    }.</p>`;
    return;
  }
  for (const pkg of items) grid.appendChild(card(pkg));
}

function card(pkg) {
  const name = pkg.name;
  const version = pkg.version || pkg.latest;
  const el = document.createElement("button");
  el.className = "mkt-card";
  el.type = "button";
  el.onclick = () => go(`#market/${enc(name)}`);
  const thumb = document.createElement("div");
  thumb.className = "mkt-thumb";
  const preview = firstPreview(pkg);
  if (preview) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = api.marketPreviewUrl(name, version, preview);
    img.onerror = () => thumb.classList.add("no-thumb");
    thumb.appendChild(img);
  } else {
    thumb.classList.add("no-thumb");
  }
  el.appendChild(thumb);
  const body = document.createElement("div");
  body.className = "mkt-card-body";
  body.innerHTML = `
    <div class="mkt-card-name">${escapeHtml(name)}</div>
    <div class="mkt-card-summary">${escapeHtml(pkg.summary || "")}</div>
    <div class="mkt-badges">${badges(pkg).join("")}</div>`;
  el.appendChild(body);
  if (pkg.why && pkg.why.length) {
    el.title = pkg.why.join("  ·  ");
  }
  return el;
}

function firstPreview(pkg) {
  const p = pkg.previews;
  if (Array.isArray(p) && p.length) return typeof p[0] === "string" ? p[0] : p[0].path;
  return null;
}

function badges(pkg) {
  const out = [];
  if (pkg.license) out.push(`<span class="mkt-badge lic">${escapeHtml(pkg.license)}</span>`);
  if (pkg.disclosure) {
    out.push(`<span class="mkt-badge disc">${escapeHtml(pkg.disclosure)}</span>`);
  }
  const gate = pkg.gate && pkg.gate.status;
  if (gate === "green") out.push(`<span class="mkt-badge ok" title="Passed the correctness gate — not a security boundary">validated ✓</span>`);
  for (const s of pkg.standards || []) {
    out.push(`<span class="mkt-badge std">${escapeHtml(s)}</span>`);
  }
  return out;
}

// -------------------------------------------------------------------- listing

async function openListing(name, version) {
  cur = null;
  currentKey = null;
  viewOnly = false;
  root.innerHTML = `
    <header class="mkt-head">
      <button class="mkt-home" type="button">Agent<b>CAD</b>
        <span class="mkt-tag">Marketplace</span></button>
      <button class="mkt-back" type="button">← All packages</button>
      <div class="mkt-head-right"></div>
    </header>
    <div class="mkt-scroll" id="mkt-listing">Loading…</div>`;
  fillHeadRight(root.querySelector(".mkt-head-right"));
  root.querySelector(".mkt-home").onclick = () => go("#market");
  root.querySelector(".mkt-back").onclick = () => go("#market");

  let summary, doc;
  try {
    summary = await api.marketPackage(name);
    version = version || summary.latest;
    doc = await api.marketVersion(name, version);
  } catch (e) {
    document.getElementById("mkt-listing").innerHTML =
      `<p class="mkt-empty">This listing is not available.</p>`;
    return;
  }
  renderListing(name, version, summary, doc);
}

function renderListing(name, version, summary, doc) {
  const host = document.getElementById("mkt-listing");
  const parts = doc.parts && typeof doc.parts === "object" ? doc.parts : {};
  const partIds = Object.keys(parts);
  const customizable = partIds.filter(
    (p) => (parts[p].params || []).length > 0
  );
  const activePart = customizable[0] || partIds[0] || null;

  host.innerHTML = `
    <div class="mkt-listing-grid">
      <section class="mkt-viewer">
        <div id="mkt-vp" class="mkt-vp"></div>
        <div id="mkt-status" class="mkt-status"></div>
        <div id="mkt-downloads" class="mkt-downloads"></div>
      </section>
      <section class="mkt-side">
        <h1 class="mkt-title">${escapeHtml(name)}</h1>
        <div class="mkt-badges">${badges({ ...doc, ...summary }).join("")}</div>
        <p class="mkt-summary">${escapeHtml(doc.summary || summary.summary || "")}</p>
        ${provenanceBlock(doc, version, summary)}
        ${partSelector(partIds, customizable, activePart)}
        <div id="mkt-params" class="mkt-params"></div>
        ${previewStrip(name, version, doc)}
        <details class="mkt-script"><summary>Read-only script</summary>
          <pre id="mkt-script-body" class="mkt-script-body">Loading…</pre></details>
        <div id="mkt-addlib" class="mkt-addlib"></div>
      </section>
    </div>`;

  // Versions selector: reload the listing at another immutable version.
  const verSel = host.querySelector("#mkt-version");
  if (verSel) {
    verSel.onchange = () => go(`#market/${enc(name)}@${enc(verSel.value)}`);
  }

  const partSel = host.querySelector("#mkt-part");
  if (partSel) {
    partSel.onchange = () => selectPart(name, version, doc, partSel.value);
  }

  renderAddToLibrary(name, version, activePart);
  loadScript(name, version, activePart);

  if (activePart) selectPart(name, version, doc, activePart);
  else {
    document.getElementById("mkt-params").innerHTML =
      `<p class="mkt-note">This package declares no parts.</p>`;
  }
}

function partSelector(partIds, customizable, active) {
  if (partIds.length <= 1) {
    return active
      ? `<div class="mkt-part-one">Part: <b>${escapeHtml(active)}</b></div>`
      : "";
  }
  const opts = partIds
    .map(
      (p) =>
        `<option value="${escapeHtml(p)}"${p === active ? " selected" : ""}>${escapeHtml(
          p
        )}${customizable.includes(p) ? "" : " (no customizer)"}</option>`
    )
    .join("");
  return `<label class="mkt-field">Part
    <select id="mkt-part">${opts}</select></label>`;
}

function provenanceBlock(doc, version, summary) {
  const rows = [];
  const versions = summary.versions || [version];
  const verOpts = versions
    .map(
      (v) => `<option value="${escapeHtml(v)}"${v === version ? " selected" : ""}>${escapeHtml(v)}</option>`
    )
    .join("");
  rows.push(
    `<div class="mkt-prov-row"><span>Version</span>
      <select id="mkt-version" class="mkt-ver-sel">${verOpts}</select></div>`
  );
  if (doc.license) rows.push(provRow("License", doc.license));
  if (doc.disclosure) rows.push(provRow("Disclosure", doc.disclosure));
  if ((doc.standards || []).length) rows.push(provRow("Standards", doc.standards.join(", ")));
  if (doc.min_agentcad) rows.push(provRow("Requires AgentCAD", doc.min_agentcad));
  const gate = doc.gate || {};
  if (gate.status) {
    const detail = [gate.agentcad && `agentcad ${gate.agentcad}`,
      gate.build123d && `build123d ${gate.build123d}`]
      .filter(Boolean).join(", ");
    rows.push(provRow("Validated", `${gate.status === "green" ? "green ✓" : gate.status}` +
      (detail ? ` — ${escapeHtml(detail)}` : ""), true));
  }
  const sigs = doc.signatures || [];
  rows.push(provRow("Signatures", sigs.length ? `${sigs.length} signature(s)` : "unsigned"));
  return `<div class="mkt-prov">${rows.join("")}
    <p class="mkt-nonclaim">The validated badge is a <b>correctness</b> gate,
      not a security boundary. See docs/packages.md.</p></div>`;
}

function provRow(label, value, raw) {
  return `<div class="mkt-prov-row"><span>${escapeHtml(label)}</span><b>${
    raw ? value : escapeHtml(String(value))
  }</b></div>`;
}

function previewStrip(name, version, doc) {
  const previews = doc.previews || [];
  if (!previews.length) return "";
  const imgs = previews
    .map((p) => {
      const path = typeof p === "string" ? p : p.path;
      return `<img loading="lazy" alt="" src="${escapeHtml(
        api.marketPreviewUrl(name, version, path)
      )}">`;
    })
    .join("");
  return `<div class="mkt-previews">${imgs}</div>`;
}

// ----------------------------------------------------------- the customizer

async function selectPart(name, version, doc, part) {
  cur = { name, version, part, spec: {} };
  currentKey = null;
  viewOnly = false;
  loadScript(name, version, part);
  renderAddToLibrary(name, version, part);
  const panel = document.getElementById("mkt-params");
  panel.innerHTML = "Loading parameters…";
  let params;
  try {
    params = (await api.marketParams(name, version, part)).params || [];
  } catch (e) {
    panel.innerHTML = `<p class="mkt-note">No parameters for this part.</p>`;
    return;
  }
  cur.spec = {};
  for (const p of params) if (p && p.name) cur.spec[p.name] = p;
  buildPanel(panel, params);
  if (params.length) rebuild();
  else setStatus("This part has no adjustable parameters.");
}

function buildPanel(panel, params) {
  panel.innerHTML = "";
  if (!params.length) {
    panel.innerHTML = `<p class="mkt-note">This part has no adjustable parameters.</p>`;
    return;
  }
  const spec = document.createElement("div");
  spec.className = "mkt-spec";
  spec.innerHTML = "<h3>Parameters</h3>";
  for (const entry of params) spec.appendChild(paramControl(entry));
  panel.appendChild(spec);
}

function paramControl(entry) {
  const type = entry.type || "number";
  const row = document.createElement("label");
  row.className = "mkt-param";
  const title = document.createElement("span");
  title.className = "mkt-param-label";
  title.textContent = entry.description || entry.name;
  if (entry.unit) title.textContent += ` (${entry.unit})`;
  row.appendChild(title);

  let input;
  if (type === "enum") {
    input = document.createElement("select");
    for (const choice of entry.choices || []) {
      const opt = document.createElement("option");
      opt.value = String(choice);
      opt.textContent = String(choice);
      input.appendChild(opt);
    }
  } else if (type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
  } else if (type === "string") {
    input = document.createElement("input");
    input.type = "text";
    if (entry.max_len) input.maxLength = entry.max_len;
  } else {
    input = document.createElement("input");
    input.type = "range";
    if (entry.min != null) input.min = entry.min;
    if (entry.max != null) input.max = entry.max;
    input.step = type === "int" ? 1 : "any";
    input.value = entry.min != null ? entry.min : 0;
    const out = document.createElement("output");
    out.textContent = input.value;
    input.addEventListener("input", () => (out.textContent = input.value));
    row.appendChild(out);
  }
  input.dataset.param = entry.name;
  input.addEventListener("input", scheduleRebuild);
  input.addEventListener("change", scheduleRebuild);
  row.appendChild(input);
  return row;
}

function collectParams() {
  const out = {};
  const panel = document.getElementById("mkt-params");
  if (!panel) return out;
  for (const input of panel.querySelectorAll("[data-param]")) {
    if (input.type === "checkbox") out[input.dataset.param] = input.checked;
    else out[input.dataset.param] = input.value;
  }
  return out;
}

function scheduleRebuild() {
  if (viewOnly) return;
  clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(rebuild, 180);
}

async function rebuild() {
  if (!cur) return;
  ensureViewport();
  setStatus("Rebuilding…");
  let data;
  try {
    data = await api.marketVariant(cur.name, cur.version, cur.part, collectParams());
  } catch (e) {
    if (e instanceof ApiError && e.status === 429) {
      const retry = (e.error.details && e.error.details.retry_after_s) || 5;
      return degradeToViewOnly(retry);
    }
    if (e instanceof ApiError && e.status === 503) {
      return setStatus("The customizer is busy — try again shortly.", "error");
    }
    if (e instanceof ApiError && e.status === 422) {
      return setStatus("That value is out of range for this part.", "error");
    }
    return setStatus("Could not rebuild that variant.", "error");
  }
  await loadMesh(data.mesh_key);
  const warn = (data.warnings || []).length ? `  ⚠ ${data.warnings.join("; ")}` : "";
  setStatus(fmtMetrics(data.metrics) + warn);
  buildDownloads();
}

async function loadMesh(key) {
  if (!key || key === currentKey || !cur) return;
  try {
    const { buffer } = await api.marketMesh(cur.name, cur.version, cur.part, key);
    vp.showPart("listing", buffer, key);
    vp.fit();
    currentKey = key;
  } catch (e) {
    /* the /variant metrics already showed; a mesh miss just leaves the frame */
  }
}

function ensureViewport() {
  if (vpReady) return;
  const host = document.getElementById("mkt-vp");
  if (!host) return;
  vp.init(host);
  vpReady = true;
}

function degradeToViewOnly(retrySeconds) {
  viewOnly = true;
  const panel = document.getElementById("mkt-params");
  panel?.querySelectorAll("[data-param]").forEach((i) => (i.disabled = true));
  setStatus(
    `Rebuild limit reached — showing view-only. Retrying in ${retrySeconds}s…`,
    "error"
  );
  setTimeout(() => {
    viewOnly = false;
    panel?.querySelectorAll("[data-param]").forEach((i) => (i.disabled = false));
    setStatus("You can adjust parameters again.");
  }, Math.max(1, retrySeconds) * 1000);
}

function buildDownloads() {
  const host = document.getElementById("mkt-downloads");
  if (!host || !cur) return;
  host.innerHTML = "<span class=\"mkt-dl-label\">Download:</span>";
  for (const fmt of ["step", "stl", "3mf"]) {
    const btn = document.createElement("button");
    btn.className = "mkt-dl";
    btn.type = "button";
    btn.textContent = fmt.toUpperCase();
    btn.onclick = () => {
      const url = api.marketDownloadUrl(cur.name, cur.version, cur.part, fmt, collectParams());
      window.location.assign(url);
    };
    host.appendChild(btn);
  }
}

// -------------------------------------------------------------------- script

async function loadScript(name, version, part) {
  const body = document.getElementById("mkt-script-body");
  if (!body || !part) return;
  try {
    body.textContent = await api.marketScript(name, version, part);
  } catch (e) {
    body.textContent = "(script unavailable)";
  }
}

// ------------------------------------------------------------- add-to-library

function renderAddToLibrary(name, version, part) {
  const host = document.getElementById("mkt-addlib");
  if (!host) return;
  if (!signedIn()) {
    host.innerHTML = `<p class="mkt-note">Sign in to add this package to a
      project (browsing and downloads need no account).</p>`;
    return;
  }
  host.innerHTML = `<button class="mkt-add" type="button">Add to library…</button>`;
  host.querySelector(".mkt-add").onclick = () => addToLibrary(name, part);
}

async function addToLibrary(name, part) {
  let projects;
  try {
    projects = (await api.listProjects()).projects || [];
  } catch (e) {
    return toast("Could not list your projects.", "error");
  }
  if (!projects.length) {
    return toast("Create a project first, then add this package.", "error");
  }
  const names = projects.map((p) => p.name);
  const proj = window.prompt(
    `Add “${name}” to which project?\n\n${names.join(", ")}`,
    names[0]
  );
  if (!proj) return;
  if (!names.includes(proj)) return toast(`No project named “${proj}”.`, "error");
  const partId = window.prompt("Part id for the new project part:", part || "part");
  if (!partId) return;
  try {
    // PRD-011 verbatim: add_package pinned to the public catalog index, then
    // use_part. `index` is left to the server's public-catalog resolution.
    await api.addPackage(proj, { name, index: "agentcad-core" });
    await api.usePackagePart(proj, name, { part, part_id: partId });
    toast(`Added ${name}/${part} to ${proj}. Open the project to see it.`, "ok");
  } catch (e) {
    const msg = e instanceof ApiError ? e.error.message : String(e);
    toast(`Add to library failed: ${msg}`, "error");
  }
}

// -------------------------------------------------------------------- chrome

function fillHeadRight(host) {
  if (!host) return;
  host.innerHTML = "";
  if (signedIn()) {
    const back = document.createElement("button");
    back.className = "mkt-workbench";
    back.type = "button";
    back.textContent = "Open workbench →";
    back.onclick = () => {
      window.location.hash = "";
      window.location.reload();
    };
    host.appendChild(back);
  } else {
    const sign = document.createElement("button");
    sign.className = "mkt-workbench";
    sign.type = "button";
    sign.textContent = "Sign in";
    sign.onclick = () => {
      window.location.hash = "";
      window.location.reload();
    };
    host.appendChild(sign);
  }
}

// ---------------------------------------------------------------- utilities

function setStatus(msg, kind) {
  const el = document.getElementById("mkt-status");
  if (!el) return;
  el.textContent = msg || "";
  el.className = `mkt-status ${kind || ""}`;
}

function fmtMetrics(metrics) {
  if (!metrics) return "";
  const bits = [];
  if (typeof metrics.mass_g === "number") bits.push(`mass ${metrics.mass_g.toFixed(1)} g`);
  const bb = metrics.bbox;
  if (bb && bb.min && bb.max) {
    const d = bb.max.map((v, i) => (v - bb.min[i]).toFixed(1));
    bits.push(`bbox ${d[0]} × ${d[1]} × ${d[2]} mm`);
  }
  return bits.join("  ·  ");
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
