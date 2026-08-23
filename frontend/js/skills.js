// The agent-skills panel (PRD-029 FR7, spec §7). A MODAL inside the
// workbench on the PRD-026 shell — the `materials.js` shape exactly: `init`,
// `open`, an `actions.register` row, a toolbar button, a `#skills` hash, and
// `dialogs.attachLegacy` so the overlay is on the one dialog stack (focus
// trap, one Esc owner, `isModalOpen()` truth).
//
// What it is FOR: a skill is agent instructions that arrived with a clone, a
// pull, a package or a marketplace fetch. Core skills ship with AgentCAD and
// are trusted by construction; a PROJECT skill needs one human act before any
// agent may load it, and this panel is that act. Granting trust is a ROUTE and
// not a tool (spec §6) — `POST …/trust` is refused unless
// `proposals.actor_kind(client) == "human"`, and the browser's `X-Agent-Id` is
// the `browser:<8 hex>` id that makes it one. No agent surface can approve
// agent instructions, which is why the button lives here and nowhere else.
//
// Everything the server sends is rendered with `textContent`. A skill body is
// third-party prose whose whole purpose is to be read by a language model;
// it is never interpolated as markup, and the preview is a `<pre>`.

import { ApiError, clientId } from "./api.js";
import * as dialogs from "./shell/dialogs.js";
import { state } from "./state.js";
import * as skillsModel from "./skills_model.js";

// Deliberately a local funnel rather than six new `api.js` methods: this slice
// owns `skills.js` and not `api.js`, and the four calls are one resource
// family. It carries the SAME `X-Agent-Id` header every other request does
// (that header is what makes trust human-gated at all) and raises the same
// `ApiError`, so `errorText` below and the modal's failure paths behave
// exactly as `materials.js`'s do.
async function req(method, path, body) {
  let res;
  const init = { method, headers: { "X-Agent-Id": clientId } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, {
      error: { type: "network_error", message: "server unreachable", details: {} },
    });
  }
  if (!res.ok) {
    let payload = null;
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error body */
    }
    // Same hosted-mode funnel `api.js` uses: a session that expired mid-modal
    // must swap the workbench for the sign-in view, not leave a dead panel.
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("agentcad:unauthenticated"));
    }
    throw new ApiError(res.status, payload);
  }
  return res.json();
}

const enc = encodeURIComponent;
const base = (proj) => `/api/projects/${enc(proj)}/skills`;

let actions = null;

let overlay, titleEl, countEl, closeBtn, bannerEl, listEl, detailEl, hintEl;
let legacy = null;

let entries = []; // the index (`skills[]`)
let hiddenRows = []; // `hidden[]` — capability-gated and disabled skills
let trust = null; // `{version, trusted: {name: digest}, disabled: [...]}`
let selected = null; // name of the previewed skill
let indexToken = 0;
let previewToken = 0;

export function init(a) {
  actions = a;
  overlay = document.getElementById("skills-modal");
  titleEl = document.getElementById("skills-title");
  countEl = document.getElementById("skills-count");
  closeBtn = document.getElementById("skills-close");
  bannerEl = document.getElementById("skills-banner");
  listEl = document.getElementById("skills-list");
  detailEl = document.getElementById("skills-detail");
  hintEl = document.getElementById("skills-hint");

  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  legacy = dialogs.attachLegacy(overlay, {
    view: "skills",
    title: "Skills…",
    description: "Review the agent skills this project can load, and trust them",
    isOpen,
    onClose: close,
    open: () => open(),
    // The action row that already offers this modal, so the palette shows one
    // row for it and not two (PRD-026 m4).
    actionId: "agent.skills",
  });
}

export function isOpen() {
  return overlay && !overlay.classList.contains("hidden");
}

export async function open() {
  entries = [];
  hiddenRows = [];
  trust = null;
  selected = null;
  overlay.classList.remove("hidden");
  if (legacy) legacy.notifyOpen();
  titleEl.textContent = "Agent skills";
  countEl.textContent = "";
  bannerEl.classList.add("hidden");
  bannerEl.textContent = "";
  note(detailEl, "Select a skill to read what an agent would load.");
  renderHint();
  closeBtn.focus();

  if (!state.projectName) {
    // Not an error: the core layer exists without a project, but trust,
    // enable state and the project layer are all per project, so there is
    // nothing honest to show until one is open.
    note(listEl, "Open a project to see the skills agents can load in it.");
    return;
  }
  note(listEl, "Loading…");
  await refresh();
}

function close() {
  overlay.classList.add("hidden");
  if (legacy) legacy.notifyClose();
  entries = [];
  hiddenRows = [];
  trust = null;
  selected = null;
  listEl.textContent = "";
  detailEl.textContent = "";
  bannerEl.textContent = "";
  bannerEl.classList.add("hidden");
}

/** Re-read the index. Called on open, after every write, and by `main.js`'s
 *  WS dispatcher on `skills_changed` (another client — or another tab of this
 *  one — trusted, untrusted or disabled something). A no-op while closed. */
export async function refresh() {
  if (!isOpen() || !state.projectName) return;
  const token = ++indexToken;
  let payload;
  try {
    payload = await req("GET", base(state.projectName));
  } catch (err) {
    if (token !== indexToken || !isOpen()) return;
    note(listEl, `Could not load skills: ${errorText(err)}`, "error");
    countEl.textContent = "";
    return;
  }
  if (token !== indexToken || !isOpen()) return;
  entries = payload.skills || [];
  hiddenRows = payload.hidden || [];
  trust = payload.trust || null;
  renderIndex();
}

// ------------------------------------------------------------------- render

function renderIndex() {
  const n = entries.length;
  countEl.textContent = `${n} skill${n === 1 ? "" : "s"}`;
  renderBanner();
  renderHint();

  listEl.textContent = "";
  if (!n && !hiddenRows.length) {
    note(listEl, "No skills are available here.");
    return;
  }
  for (const entry of skillsModel.sortRows(entries)) {
    listEl.appendChild(skillRow(entry));
  }
  if (hiddenRows.length) {
    listEl.appendChild(section(`Hidden (${hiddenRows.length})`));
    for (const row of skillsModel.sortRows(hiddenRows)) {
      listEl.appendChild(hiddenRow(row));
    }
  }
}

function renderBanner() {
  const consent = skillsModel.needsConsent(entries);
  bannerEl.classList.toggle("hidden", !consent);
  bannerEl.textContent = consent
    ? "This project provides agent instructions — review them before agents "
      + "can load them."
    : "";
}

function renderHint() {
  // Plain text, and a PATH rather than an editor: a skill lands in git like a
  // part script, so teaching one is a file you write (spec §7 — the in-browser
  // editor is PRD-026's script-editor territory).
  hintEl.textContent = state.projectName
    ? `Teach: save ${state.projectName}/skills/<name>.md`
    : "Teach: save <project>/skills/<name>.md";
}

function skillRow(entry) {
  const row = document.createElement("div");
  row.className = "skill-row";
  row.dataset.name = entry.name;
  if (entry.name === selected) row.classList.add("active");

  const head = document.createElement("div");
  head.className = "skill-row-head";

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.className = "skill-enable";
  toggle.checked = true; // the index holds only enabled skills
  toggle.setAttribute("aria-label", `Enable ${entry.name}`);
  toggle.title = "Enabled for this project";
  toggle.addEventListener("click", (e) => e.stopPropagation());
  toggle.addEventListener("change", () => setEnabled(entry.name, toggle.checked));
  head.appendChild(toggle);

  const name = document.createElement("button");
  name.type = "button";
  name.className = "skill-name";
  name.textContent = entry.name;
  head.appendChild(name);

  if (entry.version) {
    const version = document.createElement("span");
    version.className = "skill-version";
    version.textContent = entry.version;
    head.appendChild(version);
  }

  for (const b of skillsModel.badgeFor(entry, trust)) {
    const badge = document.createElement("span");
    badge.className = `skill-badge ${b.cls}`;
    badge.textContent = b.text;
    head.appendChild(badge);
  }
  row.appendChild(head);

  if (entry.description) {
    const desc = document.createElement("div");
    desc.className = "skill-desc";
    desc.textContent = entry.description;
    row.appendChild(desc);
  }
  if (entry.invalid) {
    const bad = document.createElement("div");
    bad.className = "skill-invalid";
    bad.textContent = entry.invalid;
    row.appendChild(bad);
  }

  if (entry.layer === "project") {
    row.appendChild(trustControls(entry));
  }
  row.addEventListener("click", () => selectSkill(entry.name));
  return row;
}

/** The human act. Only a project skill gets these: a core skill is trusted by
 *  construction and "untrust the shipped library" is not a thing this panel
 *  offers (deleting it from the wheel is). */
function trustControls(entry) {
  const wrap = document.createElement("div");
  wrap.className = "skill-actions";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = entry.trusted ? "tb-btn skill-untrust" : "tb-btn skill-trust";
  btn.textContent = entry.trusted ? "Untrust" : "Review & trust";
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    btn.disabled = true;
    await writeTrust(entry.name, !entry.trusted);
  });
  wrap.appendChild(btn);
  return wrap;
}

function hiddenRow(row) {
  const el = document.createElement("div");
  el.className = "skill-row skill-row-hidden";
  const head = document.createElement("div");
  head.className = "skill-row-head";

  if (row.reason === "disabled") {
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.className = "skill-enable";
    toggle.checked = false;
    toggle.setAttribute("aria-label", `Enable ${row.name}`);
    toggle.addEventListener("change", () => setEnabled(row.name, toggle.checked));
    head.appendChild(toggle);
  }

  const name = document.createElement("span");
  name.className = "skill-name";
  name.textContent = row.name;
  head.appendChild(name);

  const why = document.createElement("span");
  why.className = "skill-badge badge-hidden";
  // A capability-gated skill is not broken and not disabled — this
  // installation simply lacks what it needs (`requires: [fem]` with no FEM
  // extra installed). Saying WHICH capability is the difference between a
  // fixable state and a mystery.
  why.textContent = row.reason === "disabled"
    ? "disabled"
    : `needs ${(row.requires || []).join(", ") || "a capability"}`;
  head.appendChild(why);
  el.appendChild(head);
  return el;
}

// ------------------------------------------------------------------- writes

async function writeTrust(name, grant) {
  try {
    await req("POST", `${base(state.projectName)}/${enc(name)}/`
      + (grant ? "trust" : "untrust"));
  } catch (err) {
    actions.toast(`Could not ${grant ? "trust" : "untrust"} ${name}: `
      + errorText(err), "error");
    await refresh();
    return;
  }
  actions.toast(grant ? `Trusted ${name}` : `Untrusted ${name}`);
  await refresh();
  if (selected === name) await selectSkill(name);
}

async function setEnabled(name, enabled) {
  try {
    await req("PATCH", `${base(state.projectName)}/${enc(name)}/enabled`,
              { enabled: !!enabled });
  } catch (err) {
    actions.toast(`Could not update ${name}: ${errorText(err)}`, "error");
  }
  await refresh();
}

// ------------------------------------------------------------------ preview

/** The preview goes through `GET …/skills/{name}`, which serves a HUMAN client
 *  straight from the library with the trust check skipped — reviewing a skill
 *  is what trusting it is for, and a panel that hides the text it is asking
 *  you to approve is a consent dialog with the body blanked out. That read is
 *  not a `load_skill`, so it publishes nothing and draws no chip in the dock.
 *  The pane says, above the body, that this one is still unreviewed, and puts
 *  the Trust button right there. Every other refusal (disabled, invalid,
 *  capability-gated, unknown) still arrives as an error. */
async function selectSkill(name) {
  selected = name;
  for (const row of listEl.querySelectorAll(".skill-row")) {
    row.classList.toggle("active", row.dataset.name === name);
  }
  const token = ++previewToken;
  note(detailEl, "Loading…");
  let payload;
  try {
    payload = await req("GET", `${base(state.projectName)}/${enc(name)}`);
  } catch (err) {
    if (token !== previewToken || !isOpen()) return;
    renderPreviewError(name, err);
    return;
  }
  if (token !== previewToken || !isOpen()) return;
  renderPreview(payload);
}

function renderPreviewError(name, err) {
  detailEl.textContent = "";
  const head = document.createElement("div");
  head.className = "skill-detail-head";
  head.textContent = name;
  detailEl.appendChild(head);

  const msg = document.createElement("div");
  msg.className = "skill-empty error";
  msg.textContent = errorText(err);
  detailEl.appendChild(msg);

  const details = err instanceof ApiError ? (err.error.details || {}) : {};
  if (details.hint) {
    const hint = document.createElement("div");
    hint.className = "skill-prov";
    hint.textContent = details.hint;
    detailEl.appendChild(hint);
  }
  // A fallback, not the main path: a human client is served the untrusted
  // body (see `selectSkill`). This branch is what a view the server does not
  // count as a person — an embedded or proxied one whose `X-Agent-Id` is not
  // an explicit principal — gets, and it still offers the way forward.
  if (details.reason === "skill_untrusted") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tb-btn skill-trust";
    btn.textContent = "Review & trust";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await writeTrust(name, true);
    });
    detailEl.appendChild(btn);
  }
}

/** The index row behind a previewed name, or null. The preview payload says
 *  nothing about trust (it is the same payload an agent gets); the row beside
 *  it is where `trusted` lives. */
function rowFor(name) {
  return (entries || []).find((e) => e && e.name === name) || null;
}

function renderPreview(payload) {
  detailEl.textContent = "";

  const head = document.createElement("div");
  head.className = "skill-detail-head";
  head.textContent = payload.version
    ? `${payload.name} ${payload.version}`
    : payload.name;
  detailEl.appendChild(head);

  // Unreviewed: you are reading it BECAUSE you have to decide. Say so above
  // the body — no agent can load this text until the button below is pressed.
  const row = rowFor(payload.name);
  if (row && row.layer === "project" && !row.trusted) {
    const warn = document.createElement("div");
    warn.className = "skill-truncated skill-review";
    warn.textContent =
      "Not reviewed yet — no agent can load this until you trust it. "
      + "Read it, then trust it.";
    detailEl.appendChild(warn);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tb-btn skill-trust";
    btn.textContent = "Trust this skill";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await writeTrust(payload.name, true);
    });
    detailEl.appendChild(btn);
  }

  const prov = skillsModel.provenanceLine(payload.provenance);
  if (prov) {
    const line = document.createElement("div");
    line.className = "skill-prov";
    line.textContent = prov;
    detailEl.appendChild(line);
  }

  const note_ = skillsModel.truncationNote(payload);
  if (note_) {
    const el = document.createElement("div");
    el.className = "skill-truncated";
    el.textContent = note_;
    detailEl.appendChild(el);
  }

  const assets = skillsModel.formatAssets(payload.assets);
  if (assets.length) {
    detailEl.appendChild(section("Assets"));
    const list = document.createElement("ul");
    list.className = "skill-assets";
    for (const line of assets) {
      const li = document.createElement("li");
      li.textContent = line;
      list.appendChild(li);
    }
    detailEl.appendChild(list);
  }

  const pre = document.createElement("pre");
  pre.className = "skill-content";
  // The one line that matters: a skill body is third-party text written to be
  // read by a language model. It reaches the DOM as TEXT, always.
  pre.textContent = payload.content || "";
  detailEl.appendChild(pre);
}

// ------------------------------------------------------------------- helpers

function section(text) {
  const el = document.createElement("div");
  el.className = "lib-section";
  el.textContent = text;
  return el;
}

function note(host, text, cls) {
  host.textContent = "";
  const el = document.createElement("div");
  el.className = cls ? `skill-empty ${cls}` : "skill-empty";
  el.textContent = text;
  host.appendChild(el);
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}
