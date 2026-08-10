// Merge flow: pick source/target, then either a post-merge validation report
// or the conflict list. MVP scope (per the design spec) is a conflict LIST —
// pick ours / pick theirs / edit by hand — not a dual-viewport geometry
// compare.
//
// Conventions the server states and this UI must not get backwards:
// OURS = the target branch (what you merge into), THEIRS = the source.
// /merge and /merge/resolve answer HTTP 200 with {"error": {"type":
// "merge_conflict"}} on conflict, and 422 validation_error when a merge is
// blocked by the kernel validation pass (retryable with allow_invalid).

import { api, ApiError } from "./api.js";
import { state, setState } from "./state.js";

let actions = null;
let overlay, titleEl, bodyEl, footEl, progressEl, completeBtn, abortBtn, closeBtn;

// Independent CodeMirror instance (never editor.js's singleton), created once
// into a detached host that each render re-parents into the detail pane.
let cm = null;
let cmHost = null;

let staged = null; // {merge_id|id, source, target, conflicts[], ...}
let resolvedKeys = new Set();
let selectedKey = null;
let editing = false;
let busy = false;

export function init(a) {
  actions = a;
  overlay = document.getElementById("merge-modal");
  titleEl = document.getElementById("merge-title");
  bodyEl = document.getElementById("merge-body");
  footEl = document.getElementById("merge-foot");
  progressEl = document.getElementById("merge-progress");
  completeBtn = document.getElementById("merge-complete");
  abortBtn = document.getElementById("merge-abort");
  closeBtn = document.getElementById("merge-close");

  closeBtn.addEventListener("click", close);
  completeBtn.addEventListener("click", complete);
  abortBtn.addEventListener("click", abort);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen()) close();
  });
}

export function isOpen() {
  return overlay && !overlay.classList.contains("hidden");
}

function open() {
  overlay.classList.remove("hidden");
}

function close() {
  overlay.classList.add("hidden");
  detachEditor();
  bodyEl.textContent = "";
  footEl.classList.add("hidden");
  abortBtn.classList.add("hidden");
}

// ------------------------------------------------------------ entry points

/** Branch menu → "Merge into…": choose the source and the target. */
export async function openPicker() {
  if (!state.projectName) {
    actions.toast("Open a project first", "error");
    return;
  }
  // A staged merge takes precedence: resolve or abort it before starting one.
  if (await reopenStaged()) return;

  let payload;
  try {
    payload = await api.listBranches(state.projectName);
  } catch (err) {
    actions.toast(`Branches unavailable: ${errorText(err)}`, "error");
    return;
  }
  const branches = payload.branches || [];
  if (branches.length < 2) {
    actions.toast("Create a second branch before merging", "error");
    return;
  }
  open();
  titleEl.textContent = `${state.projectName} · merge`;
  footEl.classList.add("hidden");
  abortBtn.classList.add("hidden");
  renderPicker(branches, payload.current);
}

/** On load / reconnect: reopen the conflict view when a merge is staged.
 *  Returns true when a staged merge was found. */
export async function checkStaged() {
  return reopenStaged();
}

async function reopenStaged() {
  if (!state.projectName) return false;
  let payload;
  try {
    payload = await api.mergeStatus(state.projectName);
  } catch {
    return false; // no versioning routes (no git) or the project has no repo
  }
  const merge = payload && payload.merge;
  setState({ merge: merge || null });
  if (!merge) return false;
  staged = merge;
  resolvedKeys = new Set(merge.resolved || []);
  selectedKey = null;
  open();
  renderConflicts();
  return true;
}

// ---------------------------------------------------------------- picker

function renderPicker(branches, current) {
  detachEditor();
  bodyEl.textContent = "";
  const wrap = document.createElement("div");
  wrap.className = "conflict-picker";

  const sourceSel = document.createElement("select");
  const targetSel = document.createElement("select");
  for (const branch of branches) {
    for (const [sel, el] of [
      [sourceSel, document.createElement("option")],
      [targetSel, document.createElement("option")],
    ]) {
      el.value = branch.name;
      el.textContent = branch.name;
      sel.appendChild(el);
    }
  }
  targetSel.value = current;
  const other = branches.find((b) => b.name !== current);
  sourceSel.value = other ? other.name : current;

  wrap.appendChild(row("Merge from (theirs)", sourceSel));
  wrap.appendChild(row("into (ours)", targetSel));

  const note = document.createElement("div");
  note.className = "conflict-note";
  note.textContent =
    "Scripts merge textually, project.json key-wise (per part, parameter, " +
    "instance and material). The kernel revalidates the result before it " +
    "lands; conflicts are staged and never partially applied.";
  wrap.appendChild(note);

  const actionRow = document.createElement("div");
  actionRow.className = "conflict-picker-row";
  const go = document.createElement("button");
  go.type = "button";
  go.className = "tb-btn";
  go.textContent = "Merge";
  go.addEventListener("click", () => {
    if (sourceSel.value === targetSel.value) {
      actions.toast("Pick two different branches", "error");
      return;
    }
    startMerge(sourceSel.value, targetSel.value, go);
  });
  actionRow.appendChild(go);
  wrap.appendChild(actionRow);

  bodyEl.appendChild(wrap);
}

function row(labelText, control) {
  const el = document.createElement("div");
  el.className = "conflict-picker-row";
  const label = document.createElement("label");
  label.textContent = labelText;
  el.append(label, control);
  return el;
}

async function startMerge(source, target, btn) {
  if (busy) return;
  busy = true;
  resolvedKeys = new Set();
  selectedKey = null;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Merging…";
  }
  try {
    const res = await api.mergeBranch(state.projectName, { source, target });
    handleResult(res, source, target);
  } catch (err) {
    handleFailure(err, source, target);
  } finally {
    busy = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Merge";
    }
  }
}

// ------------------------------------------------------- response handling

function handleResult(res, source, target) {
  if (res && res.error) {
    if (res.error.type === "merge_conflict") {
      staged = { ...res.error.details };
      setState({ merge: staged });
      open();
      renderConflicts();
      return;
    }
    actions.toast(`Merge failed: ${res.error.message || "error"}`, "error");
    return;
  }
  if (res && res.held) {
    // Every conflict is resolved and nothing landed: the merge belongs to a
    // proposal, which is where it is completed.
    actions.toast(
      `Resolved — this merge belongs to ${res.held_by}; complete it there`
    );
    reopenStaged();
    return;
  }
  staged = null;
  resolvedKeys = new Set();
  setState({ merge: null });
  open();
  showResult(res, source, target);
}

function handleFailure(err, source, target) {
  const details = err instanceof ApiError ? err.error.details || {} : {};
  if (details.validation) {
    staged = staged || { source, target, conflicts: [], outstanding: 0 };
    staged.source = details.source || staged.source || source;
    staged.target = details.target || staged.target || target;
    open();
    showBlocked(details.validation);
    return;
  }
  actions.toast(`Merge failed: ${errorText(err)}`, "error");
  // A staged merge the server refused (a branch moved under it, so its
  // recorded resolutions no longer apply): re-read the staged state rather
  // than leaving stale conflicts on screen.
  if (details.merge_id) reopenStaged();
}

// ------------------------------------------------------------- conflicts

function renderConflicts() {
  detachEditor();
  titleEl.textContent =
    `merge ${staged.source} → ${staged.target} · ` +
    `${staged.conflicts.length} conflict${staged.conflicts.length === 1 ? "" : "s"}`;
  bodyEl.textContent = "";
  abortBtn.classList.remove("hidden");
  footEl.classList.remove("hidden");

  const list = document.createElement("div");
  list.className = "conflict-list";
  const detail = document.createElement("div");
  detail.className = "conflict-detail";
  bodyEl.append(list, detail);

  if (!staged.conflicts.length) {
    const done = document.createElement("div");
    done.className = "conflict-note";
    // A held merge does not complete here, so it must not be told to: the
    // Complete button beside this note is disabled, and the merge lands in
    // the proposal that holds it (where its gates are re-checked first).
    done.textContent = staged.held_by
      ? "Every conflict is resolved and recorded. This merge belongs to " +
        `${staged.held_by}: it completes there, after that proposal's gates ` +
        "are re-checked."
      : "Every conflict is resolved. Complete the merge to run the validation " +
        "pass and land it.";
    list.appendChild(done);
  }

  if (!staged.conflicts.some((c) => keyOf(c) === selectedKey)) {
    selectedKey = staged.conflicts.length ? keyOf(staged.conflicts[0]) : null;
  }
  for (const conflict of staged.conflicts) {
    const key = keyOf(conflict);
    const item = document.createElement("button");
    item.type = "button";
    item.className = "conflict-item";
    if (key === selectedKey) item.classList.add("active");
    const path = document.createElement("span");
    path.className = "conflict-path";
    path.textContent = key;
    const kind = document.createElement("span");
    kind.className = "conflict-kind";
    kind.textContent =
      conflict.kind === "manifest"
        ? "project.json"
        : conflict.kind === "binary"
          ? "binary file"
          : `script${conflict.part ? ` · ${conflict.part}` : ""}`;
    item.append(path, kind);
    item.addEventListener("click", () => {
      selectedKey = key;
      editing = false;
      renderConflicts();
    });
    list.appendChild(item);
  }

  renderDetail(detail);
  updateProgress();
}

function renderDetail(host) {
  const conflict = staged.conflicts.find((c) => keyOf(c) === selectedKey);
  if (!conflict) {
    const note = document.createElement("div");
    note.className = "conflict-note";
    note.textContent = staged.conflicts.length
      ? "Select a conflict."
      : "Nothing outstanding.";
    host.appendChild(note);
    return;
  }

  const head = document.createElement("div");
  head.className = "conflict-head";
  const path = document.createElement("span");
  path.className = "conflict-path";
  path.textContent = keyOf(conflict);
  head.appendChild(path);
  const buttons = document.createElement("span");
  buttons.className = "conflict-actions";
  head.appendChild(buttons);
  host.appendChild(head);

  const note = document.createElement("div");
  note.className = "conflict-note";
  note.textContent =
    `ours = ${staged.target} (target) · theirs = ${staged.source} (source)`;
  host.appendChild(note);

  const hasBase = conflict.kind === "manifest"
    ? Object.prototype.hasOwnProperty.call(conflict, "base")
    : conflict.kind === "binary"
      ? !!(conflict.sides && conflict.sides.base)
      : conflict.base != null;

  buttons.appendChild(
    pickButton(`Use ours (${staged.target})`, conflict, { take: "ours" })
  );
  buttons.appendChild(
    pickButton(`Use theirs (${staged.source})`, conflict, { take: "theirs" })
  );
  if (hasBase) {
    buttons.appendChild(pickButton("Use base", conflict, { take: "base" }));
  }

  if (conflict.kind === "manifest") {
    host.appendChild(valueTable(conflict));
    return;
  }

  // Binary (imports/*.stl, *.step): there is no text to show or edit, only a
  // side to copy through byte for byte.
  if (conflict.kind === "binary") {
    host.appendChild(binaryTable(conflict));
    return;
  }

  if (conflict.truncated) {
    const warn = document.createElement("div");
    warn.className = "conflict-note err";
    warn.textContent =
      "This file is too large to show in full — the staged copy on disk has " +
      "the complete text. Pick a side, or resolve it with the tools.";
    host.appendChild(warn);
  }

  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "tb-btn";
  edit.textContent = editing ? "Save edit" : "Edit…";
  edit.addEventListener("click", () => {
    if (!editing) {
      editing = true;
      cm.setOption("readOnly", false);
      cm.focus();
      edit.textContent = "Save edit";
      return;
    }
    applyChoice(conflict, { content: cm.getValue() });
  });
  buttons.appendChild(edit);

  host.appendChild(ensureEditor());
  cm.setOption("readOnly", editing ? false : "nocursor");
  cm.setValue(conflict.merged || conflict.ours || "");
  // CodeMirror measures on creation; it must re-measure once its host is
  // visible and sized inside the modal.
  requestAnimationFrame(() => cm.refresh());
}

function pickButton(label, conflict, choice) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tb-btn";
  btn.textContent = label;
  btn.addEventListener("click", () => applyChoice(conflict, choice));
  return btn;
}

// A side with no value OMITS its key (that side deleted it, or both branches
// added the key so there is no base). An authored JSON null is a value like
// any other, and must not read as "deleted".
function valueTable(conflict) {
  const has = (key) => Object.prototype.hasOwnProperty.call(conflict, key);
  const table = document.createElement("table");
  table.className = "conflict-table";
  for (const label of ["base", "ours", "theirs"]) {
    if (label === "base" && !has("base")) continue; // both branches added it
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent =
      label === "ours"
        ? `ours (${staged.target})`
        : label === "theirs"
          ? `theirs (${staged.source})`
          : "base";
    const td = document.createElement("td");
    td.textContent = has(label)
      ? JSON.stringify(conflict[label], null, 2)
      : "— deleted —";
    tr.append(th, td);
    table.appendChild(tr);
  }
  return table;
}

function binaryTable(conflict) {
  const wrap = document.createElement("div");
  const note = document.createElement("div");
  note.className = "conflict-note";
  note.textContent =
    "Binary file — pick a side and its bytes are copied through unchanged. " +
    "Hand-written content is not accepted here.";
  wrap.appendChild(note);

  const table = document.createElement("table");
  table.className = "conflict-table";
  const sides = conflict.sides || {};
  for (const [label, info] of [
    ["base", sides.base],
    ["ours", sides.ours],
    ["theirs", sides.theirs],
  ]) {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent =
      label === "ours"
        ? `ours (${staged.target})`
        : label === "theirs"
          ? `theirs (${staged.source})`
          : "base";
    const td = document.createElement("td");
    td.textContent = info
      ? `${Number(info.bytes).toLocaleString("en-US")} bytes · ` +
        `${String(info.sha256).slice(0, 12)}`
      : "— absent —";
    tr.append(th, td);
    table.appendChild(tr);
  }
  wrap.appendChild(table);
  return wrap;
}

async function applyChoice(conflict, choice) {
  if (busy) return;
  busy = true;
  const key = keyOf(conflict);
  try {
    const res = await api.resolveMerge(state.projectName, { [key]: choice });
    resolvedKeys.add(key);
    editing = false;
    handleResult(res, staged.source, staged.target);
  } catch (err) {
    handleFailure(err, staged.source, staged.target);
  } finally {
    busy = false;
  }
}

function updateProgress() {
  const outstanding = staged ? staged.conflicts.length : 0;
  const total = outstanding + resolvedKeys.size;
  // A merge staged by a proposal is HELD: resolving here records the choices,
  // but only proposal_merge lands it — after re-checking that proposal's
  // gates against the branches as they are now.
  const heldBy = staged && staged.held_by;
  progressEl.textContent =
    (total ? `${resolvedKeys.size} of ${total} resolved` : "nothing outstanding") +
    (heldBy ? ` · held by ${heldBy}` : "");
  completeBtn.disabled = outstanding > 0 || !!heldBy;
  completeBtn.textContent = heldBy ? "Complete in the proposal" : "Complete merge";
  completeBtn.title = heldBy
    ? `This merge belongs to ${heldBy}: resolve the conflicts here, then ` +
      "merge that proposal — its gates are re-checked before anything lands"
    : outstanding
      ? "Resolve every conflict first"
      : "Run the validation pass and land the merge";
}

// ------------------------------------------------------------- completion

async function complete() {
  if (!staged || busy) return;
  const { source, target } = staged;
  busy = true;
  completeBtn.disabled = true;
  completeBtn.textContent = "Merging…";
  try {
    const res = await api.mergeBranch(state.projectName, { source, target });
    handleResult(res, source, target);
  } catch (err) {
    handleFailure(err, source, target);
  } finally {
    busy = false;
    completeBtn.textContent = "Complete merge";
  }
}

async function abort() {
  if (!staged || busy) return;
  if (!confirm(`Discard the staged merge of ${staged.source} into ${staged.target}?`)) {
    return;
  }
  busy = true;
  try {
    await api.abortMerge(state.projectName);
  } catch (err) {
    actions.toast(`Abort failed: ${errorText(err)}`, "error");
    return;
  } finally {
    busy = false;
  }
  staged = null;
  resolvedKeys = new Set();
  setState({ merge: null });
  actions.toast("Merge aborted");
  close();
}

// ---------------------------------------------------------------- reports

function showBlocked(validation) {
  detachEditor();
  bodyEl.textContent = "";
  footEl.classList.add("hidden");
  abortBtn.classList.remove("hidden");
  titleEl.textContent = `merge ${staged.source} → ${staged.target} · blocked`;

  const host = document.createElement("div");
  host.className = "conflict-detail";
  const lead = document.createElement("div");
  lead.className = "conflict-note err";
  lead.textContent =
    "The merged state failed the kernel validation pass, so nothing landed. " +
    "Fix the source branch and merge again, or land it anyway — the failures " +
    "are recorded in the merge commit.";
  host.appendChild(lead);
  host.appendChild(reportBlock(validation));

  const row = document.createElement("div");
  row.className = "conflict-actions";
  const land = document.createElement("button");
  land.type = "button";
  land.className = "tb-btn";
  land.textContent = "Land anyway (allow_invalid)";
  land.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    land.disabled = true;
    land.textContent = "Landing…";
    const { source, target } = staged;
    try {
      const res = await api.mergeBranch(state.projectName, {
        source,
        target,
        allow_invalid: true,
      });
      handleResult(res, source, target);
    } catch (err) {
      handleFailure(err, source, target);
    } finally {
      busy = false;
    }
  });
  row.appendChild(land);
  host.appendChild(row);
  bodyEl.appendChild(host);
}

function showResult(res, source, target) {
  detachEditor();
  bodyEl.textContent = "";
  footEl.classList.add("hidden");
  abortBtn.classList.add("hidden");
  const src = res.source || source;
  const dst = res.target || target;
  titleEl.textContent = `merge ${src} → ${dst}`;

  const host = document.createElement("div");
  host.className = "conflict-detail";
  const lead = document.createElement("div");
  lead.className = "conflict-note";
  if (res.already_up_to_date) {
    lead.textContent = `${dst} already contains ${src} — nothing to do.`;
  } else if (res.fast_forward) {
    lead.textContent =
      `Fast-forwarded ${dst} to ${src} (${(res.commit || "").slice(0, 8)}) — ` +
      "revalidated by the kernel like any other merge.";
  } else {
    lead.textContent =
      `Merged as ${(res.commit || "").slice(0, 8)} with two parents · ` +
      `${res.conflicts_resolved || 0} conflict` +
      `${res.conflicts_resolved === 1 ? "" : "s"} resolved.`;
  }
  host.appendChild(lead);
  if (res.validation) host.appendChild(reportBlock(res.validation));
  bodyEl.appendChild(host);
}

/** The kernel validation report, rendered. Exported so the proposals modal's
 *  Checks tab shows the SAME block for the same report rather than growing a
 *  second one that can drift (PRD-002 slice 5). */
export function reportBlock(validation) {
  const el = document.createElement("div");
  el.className = "conflict-report";

  const verdict = document.createElement("div");
  verdict.className = validation.ok ? "good" : "bad";
  verdict.textContent = validation.ok
    ? "Validation passed."
    : "Validation failed.";
  el.appendChild(verdict);

  const warnings = validation.warnings || [];
  if (warnings.length) {
    el.appendChild(heading("Warnings"));
    el.appendChild(bullets(warnings));
  }

  const built = validation.built || [];
  if (built.length) {
    el.appendChild(heading("Rebuilt"));
    el.appendChild(
      bullets(
        built.map((b) => `${b.part}${b.cached ? " (cached)" : ""}`)
      )
    );
  }
  for (const failure of validation.failures || []) {
    el.appendChild(heading(`Build failure · ${failure.part}`));
    const pre = document.createElement("pre");
    pre.textContent =
      (failure.error && (failure.error.message || failure.error.type)) ||
      "build failed";
    el.appendChild(pre);
  }
  const integrity = validation.integrity || [];
  if (integrity.length) {
    el.appendChild(heading("Referential integrity"));
    el.appendChild(
      bullets(
        integrity.map((i) =>
          `${i.kind}: ${[i.instance, i.part, i.mate].filter(Boolean).join(" → ")}`
        )
      )
    );
  }
  const interference = validation.interference || {};
  const pairs = interference.new_pairs || [];
  if (pairs.length || interference.skipped) {
    el.appendChild(heading("Interference"));
    if (interference.skipped) {
      el.appendChild(bullets([`skipped (${interference.skipped})`]));
    } else {
      el.appendChild(
        bullets(
          pairs.map(
            (p) => `${p.a} ↔ ${p.b} · ${Number(p.volume_mm3).toFixed(1)} mm³`
          )
        )
      );
    }
  }
  return el;
}

function heading(text) {
  const el = document.createElement("h4");
  el.textContent = text;
  return el;
}

function bullets(items) {
  const ul = document.createElement("ul");
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    ul.appendChild(li);
  }
  return ul;
}

// ---------------------------------------------------------------- editor

function ensureEditor() {
  if (!cmHost) {
    cmHost = document.createElement("div");
    cmHost.className = "conflict-cm";
    cm = window.CodeMirror(cmHost, {
      value: "",
      mode: "python",
      theme: "agentcad",
      lineNumbers: true,
      lineWrapping: false,
      readOnly: "nocursor",
    });
  }
  return cmHost;
}

function detachEditor() {
  editing = false;
  if (cmHost && cmHost.parentNode) cmHost.parentNode.removeChild(cmHost);
}

function keyOf(conflict) {
  return conflict.kind === "manifest" ? conflict.key : conflict.path;
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}
