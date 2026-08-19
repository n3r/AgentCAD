// Parts library: search the configured package indexes, read what a package
// declares before installing it, and insert one of its parts at a preset.
//
// Wired like versions.js and proposals.js — a plain `.modal-overlay` with a
// close button and backdrop click. Deliberately NOT a native `<dialog>`: one
// modal shape in this app is worth more than one of them being modern. PRD-026
// slice 2 adopted it onto the shell's overlay stack (`dialogs.attachLegacy`),
// so the markup is unchanged and Esc/focus are the stack's.
//
// "Add to project" is two calls, in this order and never one:
//   1. `add_package` — resolve, verify the content id, install into the cache
//      and record BOTH manifest maps. This is the dependency.
//   2. `use_part` — copy the script in under its provenance header. This is
//      the geometry.
// A project can legitimately depend on a package it has not materialised, so
// the first call is not a means to the second; when the package is already
// installed at a satisfying version, `add_package` is idempotent and leaves
// `project.json` byte-identical.
//
// The install affordance carries the security non-claim as *visible text* in
// the dialog footer (never a tooltip): the publish gate is a correctness gate,
// not a security boundary.

import { api, ApiError } from "./api.js";
import { state } from "./state.js";
import * as dialogs from "./shell/dialogs.js";

const PART_ID_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

let actions = null;
let overlay, titleEl, searchEl, listEl, detailEl, closeBtn;
let legacy = null;   // the overlay's seat on the shell's dialog stack
let hits = [];
let selected = null;
let searchToken = 0;
let searchTimer = null;

export function init(a) {
  actions = a;
  overlay = document.getElementById("library-modal");
  titleEl = document.getElementById("library-title");
  searchEl = document.getElementById("library-search");
  listEl = document.getElementById("library-list");
  detailEl = document.getElementById("library-detail");
  closeBtn = document.getElementById("library-close");

  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  // PRD-026 FR2: Esc belongs to the shell's overlay stack, not to this module.
  legacy = dialogs.attachLegacy(overlay, {
    view: "library", title: "Parts library…", isOpen, onClose: close,
    description: "Search the installed packages for a part to use",
    open: () => open(),
    when: (c) => !!c.projectName,
  });
  searchEl.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => refresh(searchEl.value), 180);
  });
}

export function isOpen() {
  return overlay && !overlay.classList.contains("hidden");
}

export async function open() {
  if (!state.projectName) {
    actions.toast("Open a project first", "error");
    return;
  }
  overlay.classList.remove("hidden");
  if (legacy) legacy.notifyOpen();
  titleEl.textContent = `Parts library → ${state.projectName}`;
  note(listEl, "Searching…");
  detailEl.textContent = "";
  searchEl.focus();
  await refresh(searchEl.value);
}

function close() {
  overlay.classList.add("hidden");
  if (legacy) legacy.notifyClose();   // idempotent: Esc pops the stack itself
  listEl.textContent = "";
  detailEl.textContent = "";
  hits = [];
  selected = null;
}

function note(host, text, cls) {
  host.textContent = "";
  const el = document.createElement("div");
  el.className = cls ? `lib-empty ${cls}` : "lib-empty";
  el.textContent = text;
  host.appendChild(el);
}

async function refresh(query) {
  // Every response is scoped to the request that asked for it: typing is
  // faster than a search, and an out-of-order answer would render the wrong
  // result set (PRD-009's rule for `sketch_plane`, same shape).
  const token = ++searchToken;
  let payload;
  try {
    payload = await api.searchPackages(query ? { query } : {});
  } catch (err) {
    if (token !== searchToken) return;
    note(listEl, `Search failed: ${errorText(err)}`, "error");
    return;
  }
  if (token !== searchToken || !isOpen()) return;
  hits = payload.hits || [];
  for (const warning of payload.warnings || []) {
    actions.toast(`Package index: ${warning}`, "error");
  }
  renderList();
  if (hits.length) select(hits[0]);
  else detailEl.textContent = "";
}

function renderList() {
  listEl.textContent = "";
  if (!hits.length) {
    note(listEl,
      "No packages match. The bundled catalog ships with the app; add more " +
      "indexes (a directory or a git URL) in ~/.agentcad/config.json.");
    return;
  }
  for (const hit of hits) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "lib-hit";
    row.dataset.name = hit.name;

    const preview = (hit.previews || [])[0];
    if (preview) {
      const img = document.createElement("img");
      img.className = "lib-hit-thumb";
      img.loading = "lazy";
      img.alt = "";
      img.src = api.packagePreviewUrl(hit.name, hit.version, preview, hit.index);
      row.appendChild(img);
    }

    const main = document.createElement("div");
    main.className = "lib-hit-main";
    const name = document.createElement("div");
    name.className = "lib-hit-name";
    name.textContent = hit.name;
    const ver = document.createElement("span");
    ver.className = "lib-hit-ver";
    ver.textContent = ` ${hit.version}`;
    name.appendChild(ver);
    main.appendChild(name);
    if (hit.summary) {
      const summary = document.createElement("div");
      summary.className = "lib-hit-summary";
      summary.textContent = hit.summary;
      main.appendChild(summary);
    }
    if (hit.why && hit.why.length) {
      const why = document.createElement("div");
      why.className = "lib-hit-why";
      why.textContent = hit.why.join(" · ");
      main.appendChild(why);
    }
    row.appendChild(main);
    row.addEventListener("click", () => select(hit));
    listEl.appendChild(row);
  }
  markActive();
}

function markActive() {
  for (const row of listEl.querySelectorAll(".lib-hit")) {
    row.classList.toggle("active", selected && row.dataset.name === selected.name);
  }
}

function select(hit) {
  selected = hit;
  markActive();
  renderDetail(hit);
}

function renderDetail(hit) {
  detailEl.textContent = "";

  const head = document.createElement("div");
  head.className = "lib-head";
  const preview = (hit.previews || [])[0];
  if (preview) {
    const img = document.createElement("img");
    img.className = "lib-preview";
    img.alt = `${hit.name} preview`;
    img.src = api.packagePreviewUrl(hit.name, hit.version, preview, hit.index);
    head.appendChild(img);
  }
  const main = document.createElement("div");
  main.className = "lib-head-main";
  const name = document.createElement("div");
  name.className = "lib-name";
  name.textContent = `${hit.name} ${hit.version}`;
  main.appendChild(name);
  if (hit.summary) {
    const summary = document.createElement("div");
    summary.className = "lib-summary";
    summary.textContent = hit.summary;
    main.appendChild(summary);
  }

  const badges = document.createElement("div");
  badges.className = "lib-badges";
  // The disclosure badge is PRD-031 FR1/AC5's field, and it is only knowable
  // at authoring time — which is why PRD-011 requires it and shows it here.
  badges.appendChild(badge(`disclosure: ${hit.disclosure || "unknown"}`,
                           "disclosure"));
  if (hit.license) badges.appendChild(badge(hit.license));
  for (const std of hit.standards || []) badges.appendChild(badge(std));
  badges.appendChild(badge(`index: ${hit.index}`));
  main.appendChild(badges);
  head.appendChild(main);
  detailEl.appendChild(head);

  const partIds = Object.keys(hit.parts || {});
  for (const partId of partIds) {
    detailEl.appendChild(section(`part · ${partId}`));
    detailEl.appendChild(paramTable((hit.parts[partId] || {}).params || []));
    const connectors = (hit.parts[partId] || {}).connectors || {};
    const names = Object.keys(connectors);
    if (names.length) {
      const row = document.createElement("div");
      row.className = "lib-param";
      const label = document.createElement("span");
      label.className = "lib-param-name";
      label.textContent = "connectors";
      const value = document.createElement("span");
      value.className = "lib-param-desc";
      value.textContent = names.map((n) => `${n} (${connectors[n]})`).join(", ");
      row.append(label, value);
      detailEl.appendChild(row);
    }
  }

  detailEl.appendChild(insertRow(hit, partIds));
}

function badge(text, extra) {
  const el = document.createElement("span");
  el.className = extra ? `lib-badge ${extra}` : "lib-badge";
  el.textContent = text;
  return el;
}

function section(text) {
  const el = document.createElement("div");
  el.className = "lib-section";
  el.textContent = text;
  return el;
}

/** The declared parameter table, read-only. It is the index digest's own
 *  record of what the part accepts — a *range*, not a value — so it is not
 *  the inspector's control table (which writes) but it wears the same type
 *  scale so the two read as one product. */
function paramTable(params) {
  const host = document.createElement("div");
  host.className = "lib-params";
  if (!params.length) {
    const el = document.createElement("div");
    el.className = "lib-param-desc";
    el.textContent = "no parameters";
    host.appendChild(el);
    return host;
  }
  for (const param of params) {
    const row = document.createElement("div");
    row.className = "lib-param";
    const name = document.createElement("span");
    name.className = "lib-param-name";
    name.textContent = param.name;
    const range = document.createElement("span");
    range.className = "lib-param-range";
    range.textContent = rangeText(param);
    const desc = document.createElement("span");
    desc.className = "lib-param-desc";
    desc.textContent = param.description || "";
    row.append(name, range, desc);
    host.appendChild(row);
  }
  return host;
}

function rangeText(param) {
  if (param.type === "enum") {
    return (param.choices || []).join(" | ");
  }
  if (param.min != null && param.max != null) {
    return `${param.min}…${param.max}${param.unit ? ` ${param.unit}` : ""}`;
  }
  return param.type || "number";
}

function insertRow(hit, partIds) {
  const host = document.createElement("div");
  host.className = "lib-insert";

  const partSelect = document.createElement("select");
  partSelect.setAttribute("aria-label", "Part");
  for (const partId of partIds) {
    const opt = document.createElement("option");
    opt.value = partId;
    opt.textContent = partId;
    partSelect.appendChild(opt);
  }

  const presetSelect = document.createElement("select");
  presetSelect.setAttribute("aria-label", "Configuration");

  const partIdInput = document.createElement("input");
  partIdInput.type = "text";
  partIdInput.setAttribute("aria-label", "New part id");

  // `presets` is published as `<part>.<config>`: two parts may legitimately
  // ship a configuration with the same name, and `use_part` takes both.
  const fillPresets = () => {
    presetSelect.textContent = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "package defaults";
    presetSelect.appendChild(none);
    const prefix = `${partSelect.value}.`;
    for (const preset of hit.presets || []) {
      if (!preset.startsWith(prefix)) continue;
      const opt = document.createElement("option");
      opt.value = preset.slice(prefix.length);
      opt.textContent = opt.value;
      presetSelect.appendChild(opt);
    }
    partIdInput.value = suggestId(partSelect.value, presetSelect.value);
  };
  partSelect.addEventListener("change", fillPresets);
  presetSelect.addEventListener("change", () => {
    partIdInput.value = suggestId(partSelect.value, presetSelect.value);
  });
  fillPresets();

  const status = document.createElement("span");
  status.className = "lib-status";

  const add = document.createElement("button");
  add.type = "button";
  add.className = "tb-btn";
  add.textContent = "Add to project";
  add.addEventListener("click", () =>
    insert(hit, partSelect.value, presetSelect.value, partIdInput.value,
           add, status));

  host.append(partSelect, presetSelect, partIdInput, add, status);
  return host;
}

function suggestId(part, preset) {
  const base = preset ? `${part}_${preset}` : part;
  const cleaned = base.replace(/[^A-Za-z0-9_]/g, "_");
  const taken = new Set(
    ((state.project && state.project.parts) || []).map((p) => p.id));
  if (!taken.has(cleaned)) return cleaned;
  for (let i = 2; i < 100; i++) {
    if (!taken.has(`${cleaned}_${i}`)) return `${cleaned}_${i}`;
  }
  return cleaned;
}

async function insert(hit, part, preset, partId, btn, status) {
  const id = (partId || "").trim();
  if (!PART_ID_RE.test(id)) {
    status.className = "lib-status error";
    status.textContent = "Part id must be a Python identifier";
    return;
  }
  btn.disabled = true;
  status.className = "lib-status";
  status.textContent = "Installing…";
  const proj = state.projectName;
  let added = null;
  try {
    // The project's OWN declared requirement, when it has one. Sending only
    // {name, index} means "the caller did not say", and for a package this
    // project already pins that used to be read as `*` — one click silently
    // widened a deliberate `~1.0.0`, jumped the lock a major version and
    // flipped every part materialised from it to `version_drift`. The server
    // preserves a declaration now; sending it as well means the dialog is
    // asking for what the project asked for rather than relying on that.
    const body = { name: hit.name, index: hit.index };
    const declared = await declaredRequirement(proj, hit.name);
    if (declared) body.version_req = declared;
    added = await api.addPackage(proj, body);
    status.textContent = "Building…";
    await api.usePackagePart(proj, hit.name, {
      part, part_id: id, preset: preset || undefined,
    });
  } catch (err) {
    btn.disabled = false;
    status.className = "lib-status error";
    status.textContent = errorText(err);
    return;
  }
  btn.disabled = false;
  status.textContent = "";
  actions.toast(`Inserted ${id} from ${hit.name}@${hit.version}`);
  // A declaration this insert moved is named, never absorbed: the user asked
  // for a part, not for a dependency change.
  const moved = added && added.requirement_change;
  if (moved) {
    const parts = Object.keys(moved).map(
      (key) => `${key} ${moved[key].from ?? "—"} → ${moved[key].to ?? "—"}`);
    actions.toast(`${hit.name}: ${parts.join(", ")}`);
  }
  close();
  await actions.refreshProject();
  if (actions.selectPart) actions.selectPart(id);
}

async function declaredRequirement(proj, name) {
  // Best-effort and never fatal: with no answer the server still preserves
  // the declaration, so a failed listing must not stop an install.
  try {
    const listed = await api.listPackages(proj);
    const entry = (listed && listed.packages && listed.packages[name]) || null;
    return (entry && entry.version_req) || null;
  } catch (err) {
    return null;
  }
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}
