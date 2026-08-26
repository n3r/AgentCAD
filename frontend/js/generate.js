// Task-to-part generation panel (PRD-018 slice 6, design spec §11, Experience
// section): a prompt + attachment well driving `generate_part`, a live
// progress transcript while that (long, synchronous) call is in flight, and a
// candidate gallery once it resolves. Same `.modal-overlay`/`dialogs.attachLegacy`
// shape as skills.js/materials.js — `init(panelApi)` from main.js's boot(),
// a toolbar button, a Model/Agent-menu row, adopted onto the one dialog stack.
//
// The live-progress problem: `generate_part` does not return until the WHOLE
// loop (every candidate) has reached a terminal state, so "watch it converge"
// has to come from the WebSocket while the POST is still in flight — the
// resolved response is only ever the FINAL, authoritative shape (candidates,
// scratch ids, the intent record). Both `generation_progress`/`generation_done`
// and the loop's own `chat_tool_call`/`chat_tool_result` (tagged
// `generation_id`) are forwarded here from main.js's WS switch. The browser
// does not know its own `generation_id` until the HTTP call resolves, so
// `handleEvent` locks onto the FIRST `generation_id` it sees after a submit —
// good enough for one browser driving one generation at a time; a second,
// unrelated generation racing on the same project while this panel is open
// would be misattributed, and that is a known v1 limitation, not a bug nobody
// noticed (kept out of the request filter for the same reason the chat dock's
// own `session` filter is a heuristic, not a lock).
//
// Vision/measure tool RESULTS on the wire never carry image bytes
// (`agent/chat.py::_render_tool_result` replaces `png_base64` with a
// placeholder before publishing) — so the live thumbnail and the gallery's
// renders both come from a fresh `POST /api/projects/{proj}/render
// {part_id: scratch_id}` call, exactly the route the chat-free vision pack
// already serves, addressed by the `part_id` `_scope_args` stamped onto the
// `render_view` tool_call event.

import { api, ApiError, clientId, wsHeader } from "./api.js";
import { state, onKeys } from "./state.js";
import * as dialogs from "./shell/dialogs.js";

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg"]);
const MAX_CANDIDATES = 4;

let panel = null; // panelApi (toast, refreshProject, selectPart, …)
let legacy = null;

let overlayEl, titleEl, closeBtn, newBtn, formEl, promptEl, attachHost,
  attachBtn, attachInput, candidatesEl, maxIterEl, wallclockEl, errorEl,
  submitEl, intentEl, progressEl, galleryEl;

let phase = "form"; // "form" | "running" | "done"
let busy = false;
let attachments = []; // [{name, displayName, kind: "image"|"document"}]
let currentResult = null; // the resolved generate_part payload
let lockedGenId = null; // the generation_id this run's WS events belong to
// True from the moment a submit starts until the NEXT one starts (i.e. it
// stays true through "done", not just "running"). `generate_part` is a long
// SYNCHRONOUS call and the server only flushes queued WS traffic when it
// yields control back to its event loop — on a busy server that can land
// at, or even just after, the moment the HTTP response itself resolves. If
// `handleEvent` gated strictly on `phase === "running"` a burst of
// legitimately-late events would be silently dropped the instant `submit()`
// flips to "done" — this flag is what still lets a late-arriving transcript
// render into its (now-hidden) lanes instead of vanishing.
let liveWindowOpen = false;

// candidate index -> {el, bodyEl, statusEl, thumbEl, thumbUrl, chips: Map}
let lanes = new Map();

export function init(a) {
  panel = a;
  overlayEl = document.getElementById("generate-modal");
  titleEl = document.getElementById("generate-title");
  closeBtn = document.getElementById("generate-close");
  newBtn = document.getElementById("generate-new");
  formEl = document.getElementById("generate-form");
  promptEl = document.getElementById("generate-prompt");
  attachHost = document.getElementById("generate-attachments");
  attachBtn = document.getElementById("generate-attach-btn");
  attachInput = document.getElementById("generate-attach-input");
  candidatesEl = document.getElementById("generate-candidates");
  maxIterEl = document.getElementById("generate-max-iter");
  wallclockEl = document.getElementById("generate-wallclock");
  errorEl = document.getElementById("generate-error");
  submitEl = document.getElementById("generate-submit");
  intentEl = document.getElementById("generate-intent");
  progressEl = document.getElementById("generate-progress");
  galleryEl = document.getElementById("generate-gallery");

  closeBtn.addEventListener("click", close);
  newBtn.addEventListener("click", startOver);
  overlayEl.addEventListener("click", (e) => {
    if (e.target === overlayEl) close();
  });
  formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
  });
  attachBtn.addEventListener("click", () => attachInput.click());
  attachInput.addEventListener("change", () => {
    const files = [...(attachInput.files || [])];
    attachInput.value = "";
    if (files.length) addAttachments(files);
  });

  legacy = dialogs.attachLegacy(overlayEl, {
    view: "generate",
    title: "Generate a part…",
    description: "Generate a parametric part from a prompt, photos or a PDF",
    isOpen,
    onClose: close,
    open: () => open(),
    when: (c) => !!c.projectName,
    actionId: "agent.generate",
  });

  onKeys(["projectName"], () => {
    if (isOpen()) startOver();
  });
}

export function isOpen() {
  return overlayEl && !overlayEl.classList.contains("hidden");
}

export function open() {
  if (!state.projectName) {
    panel.toast("Open a project first", "error");
    return Promise.resolve();
  }
  overlayEl.classList.remove("hidden");
  if (legacy) legacy.notifyOpen();
  titleEl.textContent = `${state.projectName} · generate`;
  if (phase === "form") promptEl.focus();
  return Promise.resolve();
}

function close() {
  overlayEl.classList.add("hidden");
  if (legacy) legacy.notifyClose();
}

/** Back to a blank prompt — the modal stays open. Used by "New generation…"
 *  and by a project switch while the panel happens to be open. */
function startOver() {
  phase = "form";
  busy = false;
  attachments = [];
  currentResult = null;
  lockedGenId = null;
  liveWindowOpen = false;
  lanes.clear();
  formEl.reset();
  formEl.classList.remove("hidden");
  submitEl.disabled = false;
  submitEl.textContent = "Generate";
  newBtn.classList.add("hidden");
  hideError();
  renderAttachments();
  intentEl.classList.add("hidden");
  intentEl.textContent = "";
  progressEl.classList.add("hidden");
  progressEl.textContent = "";
  galleryEl.classList.add("hidden");
  galleryEl.textContent = "";
}

// ------------------------------------------------------------- attachments

async function addAttachments(files) {
  if (!state.projectName) return;
  for (const file of files) {
    let upload;
    try {
      const buf = await file.arrayBuffer();
      upload = await api.uploadImport(state.projectName, file.name, buf);
    } catch (err) {
      panel.toast(`Upload failed: ${errorText(err)}`, "error");
      continue;
    }
    const ext = (upload.source.match(/\.[^.]*$/) || [""])[0]
      .slice(1).toLowerCase();
    attachments.push({
      name: upload.source,
      displayName: file.name,
      kind: IMAGE_EXTS.has(ext) ? "image" : "document",
    });
  }
  renderAttachments();
}

function removeAttachment(idx) {
  attachments.splice(idx, 1);
  renderAttachments();
}

function renderAttachments() {
  attachHost.textContent = "";
  attachments.forEach((a, idx) => {
    const chip = document.createElement("span");
    chip.className = "gen-attach-chip";
    const kind = document.createElement("span");
    kind.className = "gen-attach-kind";
    kind.textContent = a.kind === "image" ? "🖼" : "📄";
    const name = document.createElement("span");
    name.textContent = a.displayName;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.textContent = "×";
    rm.title = "Remove attachment";
    rm.addEventListener("click", () => removeAttachment(idx));
    chip.append(kind, name, rm);
    attachHost.appendChild(chip);
  });
}

// ------------------------------------------------------------------ submit

function readBudget() {
  const budget = {};
  const maxIter = maxIterEl.value.trim();
  const wallclock = wallclockEl.value.trim();
  if (maxIter !== "") budget.max_iterations = Math.max(1, parseInt(maxIter, 10));
  if (wallclock !== "") budget.wall_clock_s = Math.max(1, parseFloat(wallclock));
  return Object.keys(budget).length ? budget : undefined;
}

async function submit() {
  if (busy || !state.projectName) return;
  const prompt = promptEl.value.trim();
  if (!prompt) return;

  const candidates = Math.min(
    MAX_CANDIDATES, Math.max(1, parseInt(candidatesEl.value, 10) || 1));
  const images = attachments.filter((a) => a.kind === "image").map((a) => a.name);
  const files = attachments.filter((a) => a.kind === "document").map((a) => a.name);
  const budget = readBudget();

  busy = true;
  submitEl.disabled = true;
  submitEl.textContent = "Generating…";
  hideError();
  beginProgress(candidates);

  let result;
  try {
    result = await api.generatePart(state.projectName, {
      prompt, images, files, candidates, ...(budget ? { budget } : {}),
    });
  } catch (err) {
    busy = false;
    submitEl.disabled = false;
    submitEl.textContent = "Generate";
    formEl.classList.remove("hidden");
    progressEl.classList.add("hidden");
    showError(errorText(err));
    return;
  }
  busy = false;
  if (result && result.error) {
    submitEl.disabled = false;
    submitEl.textContent = "Generate";
    formEl.classList.remove("hidden");
    progressEl.classList.add("hidden");
    showError(result.error.message || "generation failed");
    return;
  }

  currentResult = result;
  phase = "done";
  formEl.classList.add("hidden");
  newBtn.classList.remove("hidden");
  renderIntent(result.intent, result.draft_specs);
  await renderGallery(result);
}

function beginProgress(nCandidates) {
  phase = "running";
  lockedGenId = null;
  liveWindowOpen = true;
  lanes.clear();
  progressEl.textContent = "";
  progressEl.classList.remove("hidden");
  galleryEl.classList.add("hidden");
  galleryEl.textContent = "";
  for (let n = 0; n < nCandidates; n += 1) ensureLane(n);
}

// -------------------------------------------------------------- live events

/** Called by main.js's WS switch for `generation_progress`, `generation_done`,
 *  and `chat_tool_call`/`chat_tool_result` events that carry a `generation_id`
 *  (the loop's own transcript, tagged so it can be told apart from the main
 *  chat dock's `session: "main"` stream). A no-op unless a generation is
 *  actually in flight from THIS panel. */
export function handleEvent(ev) {
  if (!liveWindowOpen || !ev || !ev.generation_id) return;
  if (ev.project && state.projectName && ev.project !== state.projectName) return;
  if (lockedGenId === null) lockedGenId = ev.generation_id;
  else if (ev.generation_id !== lockedGenId) return; // another generation's noise

  const n = ev.candidate;
  switch (ev.type) {
    case "generation_progress":
      renderProgressEvent(ev, n);
      break;
    case "chat_tool_call":
      renderToolCall(ev, n);
      break;
    case "chat_tool_result":
      renderToolResult(ev, n);
      break;
    default:
      break;
  }
}

function ensureLane(n) {
  let lane = lanes.get(n);
  if (lane) return lane;
  const el = document.createElement("div");
  el.className = "gen-lane";

  const head = document.createElement("div");
  head.className = "gen-lane-head";
  const thumb = document.createElement("img");
  thumb.className = "gen-lane-thumb";
  thumb.alt = "";
  thumb.hidden = true;
  const title = document.createElement("span");
  title.className = "gen-lane-title";
  title.textContent = `Candidate ${n}`;
  const status = document.createElement("span");
  status.className = "gen-lane-status";
  status.textContent = "waiting…";
  head.append(thumb, title, status);
  el.appendChild(head);

  const body = document.createElement("div");
  body.className = "gen-lane-body";
  el.appendChild(body);

  progressEl.appendChild(el);
  lane = { el, bodyEl: body, statusEl: status, thumbEl: thumb, chips: new Map() };
  lanes.set(n, lane);
  return lane;
}

function renderProgressEvent(ev, n) {
  const lane = ensureLane(n);
  if (ev.phase === "iterate") {
    const div = document.createElement("div");
    div.className = "gen-iter-note";
    div.textContent = `iteration ${ev.iteration}`;
    lane.bodyEl.appendChild(div);
    lane.statusEl.textContent = `iteration ${ev.iteration}…`;
  } else if (ev.phase === "measured") {
    const div = document.createElement("div");
    div.className = "gen-measure-note";
    const valid = ev.kernel_valid === true ? "valid"
      : ev.kernel_valid === false ? "invalid" : "unknown";
    div.textContent = `measured — kernel: ${valid}, specs: ${ev.spec_status || "?"}`;
    lane.bodyEl.appendChild(div);
  } else if (ev.phase === "done") {
    lane.statusEl.textContent = ev.spec_green
      ? "spec green"
      : `${ev.terminal_state || "done"}`;
  }
  lane.bodyEl.scrollTop = lane.bodyEl.scrollHeight;
}

// Same shape as chat.js's addToolChip/tool-chip matching, scoped to one lane.
function renderToolCall(ev, n) {
  const lane = ensureLane(n);
  const details = document.createElement("details");
  details.className = "tool-chip pending";
  const summary = document.createElement("summary");
  const nameEl = document.createElement("span");
  nameEl.className = "tool-name";
  nameEl.textContent = ev.name || "tool";
  const statusEl = document.createElement("span");
  statusEl.className = "tool-status";
  statusEl.textContent = "running…";
  summary.append(nameEl, statusEl);
  details.appendChild(summary);
  const pre = document.createElement("pre");
  pre.textContent = safeJson(ev.args);
  details.appendChild(pre);
  lane.bodyEl.appendChild(details);
  const list = lane.chips.get(ev.name) || [];
  list.push(details);
  lane.chips.set(ev.name, list);
  lane.bodyEl.scrollTop = lane.bodyEl.scrollHeight;

  if (ev.name === "render_view" && ev.args && ev.args.part_id) {
    lane._scratchId = ev.args.part_id; // learned for the thumbnail refresh below
  }
}

function renderToolResult(ev, n) {
  const lane = ensureLane(n);
  const list = lane.chips.get(ev.name) || [];
  const chip = list.length ? list[list.length - 1] : null;
  if (chip) {
    chip.classList.remove("pending");
    chip.classList.add(ev.ok === false ? "err" : "ok");
    chip.querySelector(".tool-status").textContent =
      ev.ok === false ? "error" : "ok";
    const pre = chip.querySelector("pre");
    if (pre && ev.result !== undefined) {
      let text = typeof ev.result === "string" ? ev.result : safeJson(ev.result);
      if (text.length >= 2000) text += " … (truncated)";
      pre.textContent += "\n→ " + text;
    }
  }
  if (ev.name === "render_view" && ev.ok !== false && lane._scratchId) {
    refreshLaneThumb(lane, lane._scratchId);
  }
  lane.bodyEl.scrollTop = lane.bodyEl.scrollHeight;
}

async function refreshLaneThumb(lane, scratchId) {
  const url = await fetchRenderPng(scratchId);
  if (!url) return;
  if (lane.thumbUrl) URL.revokeObjectURL(lane.thumbUrl);
  lane.thumbUrl = url;
  lane.thumbEl.src = url;
  lane.thumbEl.hidden = false;
}

async function fetchRenderPng(scratchId) {
  if (!state.projectName) return null;
  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(state.projectName)}/render`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Agent-Id": clientId,
          ...wsHeader(),
        },
        body: JSON.stringify({ part_id: scratchId, view: "iso", width: 320, height: 240 }),
      });
    if (!res.ok) return null;
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  } catch {
    return null; // a render is a nicety here — the transcript and the final
                 // gallery still tell the truth without it
  }
}

// ---------------------------------------------------------------- the intent

function renderIntent(intent, draftSpecs) {
  intentEl.textContent = "";
  if (!intent) {
    intentEl.classList.add("hidden");
    return;
  }
  intentEl.classList.remove("hidden");
  const head = document.createElement("div");
  head.className = "gen-intent-head";
  head.textContent = "What the loop aimed at";
  intentEl.appendChild(head);

  if (intent.envelope) {
    intentEl.appendChild(intentRow("Envelope", envelopeText(intent.envelope)));
  }
  if (intent.material) intentEl.appendChild(intentRow("Material", intent.material));
  if (intent.quantities && intent.quantities.count != null) {
    intentEl.appendChild(intentRow("Quantity", String(intent.quantities.count)));
  }
  if (intent.interfaces && intent.interfaces.length) {
    intentEl.appendChild(intentRow(
      "Interfaces",
      intent.interfaces.map((i) => i.kind || i.name || "interface").join(", ")));
  }
  if (intent.constraints && intent.constraints.length) {
    intentEl.appendChild(intentRow(
      "Constraints",
      intent.constraints.map((c) => c.kind || "constraint").join(", ")));
  }

  const chips = document.createElement("div");
  chips.className = "gen-intent-chips";
  for (const cite of intent.standards_cited || []) {
    const chip = document.createElement("span");
    chip.className = "gen-chip gen-chip-standard";
    chip.textContent = `${cite.pack || "?"}/${cite.table || "?"}`;
    if (cite.row) chip.title = `row: ${cite.row}`;
    chips.appendChild(chip);
  }
  for (const spec of draftSpecs || []) {
    const chip = document.createElement("span");
    chip.className = "gen-chip";
    chip.textContent = spec.name || spec.kind || "spec";
    chips.appendChild(chip);
  }
  if (chips.childElementCount) intentEl.appendChild(chips);
}

function intentRow(key, val) {
  const row = document.createElement("div");
  row.className = "gen-intent-row";
  const k = document.createElement("span");
  k.className = "gen-intent-key";
  k.textContent = key;
  const v = document.createElement("span");
  v.className = "gen-intent-val";
  v.textContent = val;
  row.append(k, v);
  return row;
}

function envelopeText(envelope) {
  const within = envelope.within_mm;
  if (Array.isArray(within)) return within.map((n) => `${n}`).join(" x ") + " mm";
  return JSON.stringify(envelope);
}

// -------------------------------------------------------------- the gallery

async function renderGallery(result) {
  galleryEl.textContent = "";
  galleryEl.classList.remove("hidden");
  progressEl.classList.add("hidden");
  const head = document.createElement("div");
  head.className = "gen-gallery-head";
  const candidates = result.candidates || [];
  const nGreen = candidates.filter((c) => c.spec_green).length;
  head.textContent = `${candidates.length} candidate${candidates.length === 1 ? "" : "s"}`
    + (nGreen ? ` · ${nGreen} spec-green` : " · none spec-green yet");
  galleryEl.appendChild(head);

  for (const cand of candidates) {
    galleryEl.appendChild(buildCandidateCard(cand, result.generation_id,
      result.best === cand.candidate));
  }
  // Renders are fetched after the cards exist (each is its own request; a
  // failure just leaves that card without a thumbnail — never blocks the
  // rest of the gallery from showing).
  await Promise.all(candidates.map(async (cand) => {
    if (!cand.scratch_id) return;
    const url = await fetchRenderPng(cand.scratch_id);
    if (!url) return;
    const img = galleryEl.querySelector(`[data-thumb-for="${cand.candidate}"]`);
    if (img) { img.src = url; img.hidden = false; }
  }));
}

function statusChip(cand) {
  const chip = document.createElement("span");
  if (cand.spec_green) {
    chip.className = "gen-status-chip gen-status-green";
    chip.textContent = "spec green";
  } else if (cand.terminal_state === "abandoned") {
    chip.className = "gen-status-chip gen-status-red";
    const msg = cand.error && cand.error.message ? `: ${cand.error.message}` : "";
    chip.textContent = `abandoned${msg}`;
  } else {
    chip.className = "gen-status-chip gen-status-amber";
    const n = (cand.failing_checks || []).length;
    chip.textContent = `budget exhausted — best so far, ${n} check${n === 1 ? "" : "s"} failing`;
  }
  return chip;
}

function buildCandidateCard(cand, generationId, isBest) {
  const card = document.createElement("div");
  card.className = `gen-card${isBest ? " gen-card-best" : ""}`;

  const head = document.createElement("div");
  head.className = "gen-card-head";
  const thumb = document.createElement("img");
  thumb.className = "gen-card-thumb";
  thumb.alt = "";
  thumb.hidden = true;
  thumb.dataset.thumbFor = String(cand.candidate);
  const title = document.createElement("div");
  title.className = "gen-card-title";
  const name = document.createElement("span");
  name.className = "gen-card-name";
  name.textContent = `Candidate ${cand.candidate}${isBest ? " (best)" : ""}`;
  title.appendChild(name);
  title.appendChild(statusChip(cand));

  const actions = document.createElement("div");
  actions.className = "gen-card-actions";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "tb-btn gen-card-toggle";
  toggle.textContent = cand.spec_green ? "Hide details" : "Show details";
  const acceptBtn = document.createElement("button");
  acceptBtn.type = "button";
  acceptBtn.className = "tb-btn primary";
  acceptBtn.textContent = "Accept";
  if (!cand.script) {
    acceptBtn.disabled = true;
    acceptBtn.title = "This candidate never produced a script to accept";
  }
  acceptBtn.addEventListener("click", () => accept(generationId, cand.candidate, acceptBtn));
  actions.append(toggle, acceptBtn);

  head.append(thumb, title, actions);
  card.appendChild(head);

  const body = document.createElement("div");
  body.className = "gen-card-body" + (cand.spec_green ? "" : " hidden");
  body.appendChild(metricsLine(cand.metrics));
  if (!cand.spec_green && (cand.failing_checks || []).length) {
    const fail = document.createElement("div");
    fail.className = "gen-failing";
    fail.textContent = `Failing: ${cand.failing_checks.join(", ")}`;
    body.appendChild(fail);
  }
  const chips = specChips(cand.spec_report);
  if (chips) body.appendChild(chips);
  const table = paramsTable(cand.params);
  if (table) body.appendChild(table);
  if (cand.error && cand.error.message) {
    const err = document.createElement("div");
    err.className = "gen-error-note";
    err.textContent = cand.error.message;
    body.appendChild(err);
  }
  card.appendChild(body);

  toggle.addEventListener("click", () => {
    const hidden = body.classList.toggle("hidden");
    toggle.textContent = hidden ? "Show details" : "Hide details";
  });

  return card;
}

function metricsLine(metrics) {
  const div = document.createElement("div");
  div.className = "gen-metrics";
  if (!metrics || metrics.error) {
    div.textContent = "no metrics (the candidate never built cleanly)";
    return div;
  }
  const parts = [];
  if (metrics.mass_g != null) parts.push(`mass ${round2(metrics.mass_g)} g`);
  if (metrics.bbox) {
    const { min, max } = metrics.bbox;
    if (Array.isArray(min) && Array.isArray(max)) {
      const dims = min.map((v, i) => round2(max[i] - v));
      parts.push(`bbox ${dims.join(" x ")} mm`);
    }
  }
  div.textContent = parts.length ? parts.join(" · ") : "no metrics";
  return div;
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

function specChips(report) {
  const checks = report && Array.isArray(report.checks) ? report.checks : null;
  if (!checks || !checks.length) return null;
  const host = document.createElement("div");
  host.className = "spec-chips";
  for (const check of checks) {
    const status = check.status || "error";
    const chip = document.createElement("span");
    chip.className = `spec-chip spec-${status}`;
    chip.textContent = check.name || check.id || check.kind || "check";
    if (check.message) chip.title = check.message;
    host.appendChild(chip);
  }
  return host;
}

function paramsTable(params) {
  const entries = Object.entries(params || {});
  if (!entries.length) return null;
  const table = document.createElement("table");
  table.className = "gen-params-table";
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr><th>Param</th><th>Value</th></tr>";
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  for (const [name, value] of entries) {
    const row = document.createElement("tr");
    const k = document.createElement("td");
    k.className = "gen-param-name";
    k.textContent = name;
    const v = document.createElement("td");
    v.textContent = typeof value === "number" ? String(round2(value)) : String(value);
    row.append(k, v);
    tbody.appendChild(row);
  }
  table.appendChild(tbody);
  return table;
}

// ------------------------------------------------------------------- accept

async function accept(generationId, candidateIndex, btn) {
  if (busy) return;
  busy = true;
  btn.disabled = true;
  btn.textContent = "Accepting…";
  let res;
  try {
    res = await api.acceptCandidate(state.projectName, generationId, candidateIndex);
  } catch (err) {
    busy = false;
    btn.disabled = false;
    btn.textContent = "Accept";
    panel.toast(`Accept failed: ${errorText(err)}`, "error");
    return;
  }
  busy = false;
  if (res && res.error) {
    btn.disabled = false;
    btn.textContent = "Accept";
    panel.toast(`Accept failed: ${res.error.message || "refused"}`, "error");
    return;
  }
  const proposalNote = res.proposal
    ? ` (opened as a proposal on ${res.branch || "a generation branch"})`
    : "";
  panel.toast(`Accepted candidate ${candidateIndex} as ${res.part_id}${proposalNote}`);
  close();
  await panel.refreshProject();
  await panel.selectPart(res.part_id);
}

// -------------------------------------------------------------------- misc

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.classList.remove("hidden");
}

function hideError() {
  errorEl.textContent = "";
  errorEl.classList.add("hidden");
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}

function safeJson(value) {
  try {
    const s = JSON.stringify(value, null, 1);
    return s.length > 2000 ? s.slice(0, 2000) + " …" : s;
  } catch {
    return String(value);
  }
}
