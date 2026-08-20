// Releases panel (PRD-015 Slice 6, Decision 11): the revision state machine
// (draft -> in_review -> released -> superseded) over one project.
//
// Standalone module, bootstrapped by index.html alongside bom.js — see
// bom.js's header note for why (no main.js edit in this slice). Wired like
// versions.js: overlay/close/backdrop/Escape, plain createElement rendering.
// "Cut release…" opens release_start (a durable record + a release-kind
// proposal whose specs/checks gates are evaluated for free) and shows the
// gate report inline; reviewing/approving the proposal REUSES proposals.js's
// existing modal (`openTo`) rather than growing a second approve UI, and
// `Finalize` is this panel's one write beyond that — release_finalize is
// idempotent and refuses (409) until the proposal carries a counted approval.

import { api, ApiError } from "./api.js";
import { state, onKeys } from "./state.js";
import { openTo as openProposal } from "./proposals.js";
import { relTime } from "./versions.js";
import * as dialogs from "./shell/dialogs.js";

let overlayEl, titleEl, bodyEl, cutBtn, closeBtn;
let legacy = null;   // the overlay's seat on the shell's dialog stack (PRD-026)

let loadSeq = 0;
let releases = null;
let loadError = null;
let busy = false; // guards double-submit on Cut/Finalize

const STATUS_LABEL = {
  draft: "draft",
  in_review: "in review",
  released: "released",
  superseded: "superseded",
};

export function init() {
  overlayEl = document.getElementById("releases-modal");
  titleEl = document.getElementById("releases-title");
  bodyEl = document.getElementById("releases-body");
  cutBtn = document.getElementById("releases-cut");
  closeBtn = document.getElementById("releases-close");

  closeBtn.addEventListener("click", close);
  cutBtn.addEventListener("click", cutRelease);
  overlayEl.addEventListener("click", (e) => {
    if (e.target === overlayEl) close();
  });
  // Adopt onto the shell's dialog stack (PRD-026): Esc, focus trap and
  // isModalOpen() are the shell's now — no module-level keydown listener.
  legacy = dialogs.attachLegacy(overlayEl, {
    view: "releases", title: "Releases…", onClose: close,
    description: "Cut and review releases of the current project",
    isOpen: () => isOpen(),
    open: () => open(),
    when: (c) => !!c.projectName,
    actionId: "model.releases",
  });

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
  if (legacy) legacy.notifyOpen();
  titleEl.textContent = `${state.projectName} · releases`;
  bodyEl.textContent = "";
  const loading = document.createElement("div");
  loading.className = "ver-empty";
  loading.textContent = "Loading releases…";
  bodyEl.appendChild(loading);
  await refresh();
}

function close() {
  overlayEl.classList.add("hidden");
  if (legacy) legacy.notifyClose();   // idempotent: Esc pops the stack itself
  bodyEl.textContent = "";
}

async function refresh() {
  const proj = state.projectName;
  const seq = ++loadSeq;
  let payload;
  try {
    payload = await api.listReleases(proj);
  } catch (err) {
    if (seq !== loadSeq) return;
    releases = null;
    loadError = errorText(err);
    render();
    return;
  }
  if (seq !== loadSeq || proj !== state.projectName) return;
  releases = payload.releases || [];
  loadError = null;
  render();
}

function render() {
  bodyEl.textContent = "";
  if (loadError) {
    const el = document.createElement("div");
    el.className = "ver-empty";
    // A project with no git on PATH mounts no release routes at all (a 404
    // for every path under /releases) — say the actual reason, not "error".
    el.textContent = `Releases are unavailable: ${loadError}`;
    bodyEl.appendChild(el);
    return;
  }
  if (!releases || !releases.length) {
    const el = document.createElement("div");
    el.className = "ver-empty";
    el.textContent =
      'No releases yet. "Cut release…" opens a release proposal from the ' +
      "current branch (it must not be the project default) and evaluates " +
      "its gate.";
    bodyEl.appendChild(el);
    return;
  }
  for (const rel of releases) {
    bodyEl.appendChild(renderRow(rel));
  }
}

function renderRow(rel) {
  const row = document.createElement("div");
  row.className = "ver-row rel-row";

  const main = document.createElement("div");
  main.className = "ver-main";

  const head = document.createElement("div");
  head.className = "rel-head";
  const name = document.createElement("span");
  name.className = "ver-name";
  name.textContent = `${rel.name || rel.rev} (${rel.rev})`;
  head.appendChild(name);
  const chip = document.createElement("span");
  chip.className = `rel-chip rel-${rel.status}`;
  chip.textContent = STATUS_LABEL[rel.status] || rel.status;
  head.appendChild(chip);
  if (rel.status === "released" || rel.status === "superseded") {
    const lock = document.createElement("span");
    lock.className = "rel-lock";
    lock.title = "Finalized: append-only. Branch off its tag to evolve it.";
    lock.textContent = "\u{1F512}"; // lock
    head.appendChild(lock);
  }
  main.appendChild(head);

  if (rel.notes) {
    const notes = document.createElement("div");
    notes.className = "ver-msg";
    notes.textContent = rel.notes;
    main.appendChild(notes);
  }

  const gate = rel.gate || {};
  const gateLine = document.createElement("div");
  gateLine.className = "ver-meta";
  const checks = gate.checks || [];
  const failing = checks.filter((c) => c.status === "fail");
  const parts = [`gate: ${gate.status || "?"}`];
  if (failing.length) {
    parts.push(`${failing.length} failing (${failing.map((c) => c.name).join(", ")})`);
  }
  if (gate.waiver) parts.push(`waived: ${gate.waiver.reason}`);
  if (rel.tag) parts.push(`tag: ${rel.tag}`);
  if (rel.approvals && rel.approvals.length) {
    parts.push(`approved by ${rel.approvals
      .map((a) => `${a.principal} (${relTime(a.ts)})`)
      .join(", ")}`);
  }
  gateLine.textContent = parts.join(" · ");
  main.appendChild(gateLine);

  row.appendChild(main);

  const actionsCol = document.createElement("div");
  actionsCol.className = "rel-actions";

  if (rel.proposal) {
    const reviewBtn = document.createElement("button");
    reviewBtn.className = "tb-btn";
    reviewBtn.type = "button";
    reviewBtn.textContent = "Review proposal";
    reviewBtn.addEventListener("click", () => openProposal(rel.proposal, "overview"));
    actionsCol.appendChild(reviewBtn);
  }

  if (rel.status === "in_review") {
    const finalizeBtn = document.createElement("button");
    finalizeBtn.className = "tb-btn";
    finalizeBtn.type = "button";
    finalizeBtn.textContent = "Finalize";
    finalizeBtn.title =
      "Tags release/<rev> at the approved head. Refuses until the release " +
      "proposal has a counted approve review.";
    finalizeBtn.addEventListener("click", () => finalizeRelease(rel, finalizeBtn));
    actionsCol.appendChild(finalizeBtn);
  }

  row.appendChild(actionsCol);
  return row;
}

async function cutRelease() {
  if (!state.projectName || busy) return;
  const notes = await dialogs.prompt({
    title: "Cut release",
    label: "Release notes (optional)",
    type: "textarea",
    rows: 3,
    required: false,
    okLabel: "Cut release",
  });
  if (notes === null) return;   // cancelled — do not open a release
  busy = true;
  cutBtn.disabled = true;
  let res;
  try {
    res = await api.releaseStart(state.projectName, { notes: notes || undefined });
  } catch (err) {
    busy = false;
    cutBtn.disabled = false;
    toast(`Cut release failed: ${errorText(err)}`, "error");
    return;
  }
  busy = false;
  cutBtn.disabled = false;
  const gateOk = res.gate && res.gate.status === "green";
  toast(
    `Release ${res.rev}: ${res.status}` +
      (gateOk ? "" : " (gate red — see the report)"),
    gateOk ? "info" : "error"
  );
  await refresh();
}

async function finalizeRelease(rel, btn) {
  if (busy) return;
  busy = true;
  btn.disabled = true;
  btn.textContent = "Finalizing…";
  let res;
  try {
    res = await api.releaseFinalize(state.projectName, rel.rev);
  } catch (err) {
    busy = false;
    btn.disabled = false;
    btn.textContent = "Finalize";
    toast(`Finalize failed: ${errorText(err)}`, "error");
    return;
  }
  busy = false;
  toast(`Release ${rel.rev} finalized as ${res.tag}`);
  await refresh();
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}

// See bom.js's identical helper: this module is bootstrapped independently
// of main.js, so it targets the shared #toasts host directly.
function toast(message, kind = "info") {
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${kind === "error" ? "error" : ""}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 8000 : 4000);
}
