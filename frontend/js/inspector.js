// Right inspector: Parameters / Code / Metrics tabs, the rebuild error
// banner, debounced live param editing, and the script save flow.

import { api, ApiError } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import * as editor from "./editor.js";
import * as presence from "./presence.js";
import * as configs from "./configs.js";

let actions = null;

let panes = {};
let tabs = [];
let codeTab = null;
let activeTab = "params";

let bannerEl, bannerTitle, bannerBody;
let editorClaimEl = null;
let paramsPane, metricsPane;

let renderedPartId = null;
let renderedSpecJson = null;

// enum param name -> its choices array. Selects store a choice *index*, and
// queue/sync index into this list so numeric choices keep their numeric type.
let paramChoices = {};

// The material <select> is preserved across metric re-renders so a rebuild
// (which fires often) can't tear it down while the user has the dropdown open.
let materialBlockEl = null;
let materialSig = null;

// The configuration switcher lives in a STATIC host outside #pane-params (a
// full param rebuild wipes that pane) and follows the materialSig idiom for
// the same reason the material block does: a rebuild fires often, and the
// <select> must survive one that lands while the dropdown is open. The chip
// beside it is repainted every render — divergence changes on a param edit,
// which does not move the signature.
let configBarEl = null;
let configSig = null;
let configChipHost = null;

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
    // The map is snapshotted here, once: a pane whose key is missing is never
    // shown and never hidden, so a new tab must be registered in this object
    // and not only in index.html.
    threads: document.getElementById("pane-threads"),
  };
  tabs = [...document.querySelectorAll("#tabs .tab")];
  codeTab = document.querySelector('#tabs .tab[data-tab="code"]');
  for (const tab of tabs) {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  }

  configBarEl = document.getElementById("config-bar");
  bannerEl = document.getElementById("banner");
  bannerTitle = document.getElementById("banner-title");
  bannerBody = document.getElementById("banner-body");
  document.getElementById("banner-close").addEventListener("click", dismissBanner);

  editor.init(document.getElementById("editor-host"), { onSave: saveScript });
  editorClaimEl = document.getElementById("editor-claim");
  // A DIRTY BUFFER claims the part; viewing never does. That is the whole
  // rule, and it is why this hangs off the editor's dirty state rather than
  // off selecting a part or opening the Code tab.
  editor.onDirtyChange(syncClaiming);

  onKeys(["part"], render);
  onKeys(["presence", "part", "selectedPart"], renderClaimChip);
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

// One decorator, called at the END of every render() — after buildParamControls
// (which rebuilds every row when the part id or the params_spec JSON changes)
// AND after syncParamValues (which does not). PRD-008's per-param thread badge
// has to survive both paths, so it hooks the one place both of them return to
// rather than either of them.
let paramDecorator = null;

export function setParamDecorator(fn) {
  paramDecorator = fn;
  if (state.part) decorateParams();
}

/** Re-run the decorator without a params re-render — for when the decorator's
 *  own data changed (a thread opened) rather than the part's. */
export function redecorateParams() {
  decorateParams();
}

function decorateParams() {
  if (!paramDecorator) return;
  try {
    paramDecorator(paramsPane, state.part || null);
  } catch (err) {
    console.error("param decorator failed", err);
  }
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
    renderConfigBar(null);
    decorateParams();
    return;
  }

  const isReference = part.kind === "reference";
  // References have no script: hide the Code tab and never leave it active.
  if (codeTab) codeTab.classList.toggle("hidden", isReference);
  if (isReference && activeTab === "code") setTab("params");

  const specJson = JSON.stringify(part.params_spec || null);
  if (part.id !== renderedPartId || specJson !== renderedSpecJson) {
    // Clicking another part is exactly the gesture that blurs a just-edited
    // field: an edit still debouncing for the old part must be committed, not
    // discarded. flushParams snapshots pendingPartId, and applyRebuildResult
    // only touches state.part when the ids match, so the late PATCH updates
    // the old part's server state without disturbing the new selection.
    if (Object.keys(pending).length && pendingPartId !== null && pendingPartId !== part.id) {
      clearTimeout(debounceTimer);
      flushParams();
    }
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
  renderSpecs(part);
  renderMetrics(part);
  editor.setPart(isReference ? null : part.id, isReference ? "" : part.script);

  if (part.status && part.status.state === "error" && part.status.error) {
    if (JSON.stringify(part.status.error) !== dismissedError) {
      showBanner(part.status.error);
    }
  } else {
    hideBanner();
  }
  renderConfigBar(part);
  // Before the decorator, never through it: `setParamDecorator` has one slot
  // and comments.js owns it (the per-param thread badge). These are classes on
  // the row, so the two coexist.
  markConfigSources(part);
  decorateParams();
}

// ----------------------------------------------------- configurations (bar)

/** The display token for a configuration NAME. Names are lowercase by grammar
 *  (`^[a-z0-9][a-z0-9_-]{0,31}$`) and the label is the prose one; the chip and
 *  its reset button want the identity, short and unmistakable. */
function configToken(name) {
  return String(name).toUpperCase();
}

/** The bar between the error banner and the parameters: a base/configuration
 *  <select>, the divergence chip with its reset, and the Matrix button.
 *  Invisible — not empty, invisible — for a part that declares no
 *  configurations, so the app looks exactly as it did before the feature. */
export function renderConfigBar(part) {
  if (!configBarEl) return;
  const declared = (part && part.configs) || {};
  const names = Object.keys(declared);
  if (!part || !names.length) {
    configBarEl.classList.add("hidden");
    configBarEl.textContent = "";
    configChipHost = null;
    configSig = null;
    return;
  }
  configBarEl.classList.remove("hidden");
  const sig = JSON.stringify([part.id, part.active_config, names]);
  if (sig !== configSig) {
    configSig = sig;
    buildConfigBar(part, declared, names);
  }
  renderConfigChip(part, declared);
}

function buildConfigBar(part, declared, names) {
  configBarEl.textContent = "";

  const row = document.createElement("div");
  row.className = "cfg-row";

  const label = document.createElement("span");
  label.className = "cfg-label";
  label.textContent = "Config";
  row.appendChild(label);

  const select = document.createElement("select");
  select.className = "cfg-select";
  select.setAttribute("aria-label", `Active configuration for ${part.id}`);
  const base = document.createElement("option");
  base.value = "base";
  base.textContent = "base";
  base.title = "The part's own parameters, with no configuration loaded";
  if (!part.active_config) base.selected = true;
  select.appendChild(base);
  for (const name of names) {
    const entry = declared[name] && typeof declared[name] === "object"
      ? declared[name]
      : {};
    const opt = document.createElement("option");
    opt.value = name;
    // The label is the display name; the NAME is the identity and the value.
    opt.textContent = entry.label || name;
    if (entry.description) opt.title = entry.description;
    if (name === part.active_config) opt.selected = true;
    select.appendChild(opt);
  }
  select.addEventListener("change", () => setActiveConfig(select.value));
  row.appendChild(select);

  const matrix = document.createElement("button");
  matrix.type = "button";
  matrix.className = "tb-btn cfg-matrix";
  matrix.textContent = "Matrix";
  matrix.title =
    `Build every configuration of ${part.id} and compare their metrics ` +
    "side by side";
  matrix.addEventListener("click", () => configs.open(part.id));
  row.appendChild(matrix);

  configBarEl.appendChild(row);
  configChipHost = document.createElement("div");
  configChipHost.className = "cfg-chip-host";
  configBarEl.appendChild(configChipHost);
}

function renderConfigChip(part, declared) {
  if (!configChipHost) return;
  configChipHost.textContent = "";
  const status = part.status || {};
  const active = part.active_config;
  if (!active || !status.diverged) return;

  const names = status.diverged_params || [];
  const entry = declared[active] && typeof declared[active] === "object"
    ? declared[active]
    : {};
  const token = configToken(active);

  const chip = document.createElement("span");
  chip.className = "cfg-chip diverged";
  chip.textContent = `${token} — modified`;
  chip.title = names.length
    ? `overrides: ${names.join(", ")}`
    : `${entry.label || active} is loaded, with parameter overrides on top`;
  configChipHost.appendChild(chip);

  const reset = document.createElement("button");
  reset.type = "button";
  reset.className = "cfg-reset";
  reset.textContent = `Reset to ${token}`;
  reset.title =
    `Drop every parameter override and show ${entry.label || active} as ` +
    "declared. One step — ⌘Z puts them back.";
  reset.addEventListener("click", () => resetToActiveConfig());
  configChipHost.appendChild(reset);
}

/** Load a configuration (or `"base"`). Shaped like setMaterial, including the
 *  single-use write-conflict override: this writes the part's manifest entry,
 *  so a soft claim refuses it exactly as a param write is refused. */
export async function setActiveConfig(name) {
  const part = state.part;
  if (!part) return;
  const partId = part.id;
  const next = name === "base" ? null : name;
  if (next === (part.active_config || null)) return;
  const proj = state.projectName;
  const write = () =>
    next === null
      ? api.clearActiveConfig(proj, partId)
      : api.setActiveConfig(proj, partId, next);
  try {
    let result;
    try {
      result = await write();
    } catch (err) {
      if (!(await actions.handleWriteConflict(err, partId))) throw err;
      result = await write();
    }
    if (state.project) {
      const entry = state.project.parts.find((p) => p.id === partId);
      if (entry) {
        entry.active_config = result.active_config || null;
        setState({ project: state.project });
      }
    }
    applyRebuildResult(partId, result);
    // Switching CLEARS the explicit overrides server-side, so the local params
    // map is stale in a way no response key can patch: re-read the part.
    actions.refreshPartDetail(partId);
    const cleared = Object.keys(result.cleared_overrides || {}).length;
    const what = next === null ? "base" : configToken(next);
    actions.toast(
      cleared
        ? `Loaded ${what} — cleared ${cleared} override${cleared === 1 ? "" : "s"}`
        : `Loaded ${what}`
    );
  } catch (err) {
    // Put the control back on the truth before showing why.
    configSig = null;
    renderConfigBar(state.part);
    showBanner(err instanceof ApiError ? err.error : { message: String(err) });
  }
}

/** Drop every explicit override so the part shows its configuration as
 *  declared. `set_active_config` on the ALREADY-ACTIVE configuration is a
 *  no-op for overrides (re-selecting must not silently discard a caller's
 *  set_params), so this clears them itself — one PATCH, one undo step. */
async function resetToActiveConfig() {
  const part = state.part;
  if (!part || !part.active_config) return;
  const partId = part.id;
  const names = Object.keys(part.params || {});
  if (!names.length) {
    actions.toast("No overrides to clear");
    return;
  }
  const values = Object.fromEntries(names.map((n) => [n, null]));
  try {
    let result;
    try {
      result = await api.patchParams(state.projectName, partId, values);
    } catch (err) {
      if (!(await actions.handleWriteConflict(err, partId))) throw err;
      result = await api.patchParams(state.projectName, partId, values);
    }
    // Deliberately not applyRebuildResult(…, values): its Object.assign would
    // write `null` into state.part.params rather than removing the keys.
    applyRebuildResult(partId, result);
    actions.refreshPartDetail(partId);
    actions.toast(`Reset to ${configToken(part.active_config)}`);
  } catch (err) {
    showBanner(err instanceof ApiError ? err.error : { message: String(err) });
  }
}

/** Where each parameter's value comes from, as classes on the `.param` wrap.
 *  Both can be set at once — a configuration declares `thick` and you typed
 *  over it — and the CSS orders `.param-overridden` last so the override, the
 *  value actually in effect, is the mark you see. */
export function markConfigSources(part) {
  if (!paramsPane) return;
  const active = part && part.active_config;
  const declared = (part && part.configs) || {};
  const configured = Object.keys(declared).length > 0;
  const entry = active && typeof declared[active] === "object" ? declared[active] : null;
  // Nothing to be "from" and nothing to be "over" without a family: a part
  // with no configurations keeps exactly the parameter rows it always had.
  const fromConfig = configured ? (entry && entry.params) || {} : {};
  const overrides = configured ? (part && part.params) || {} : {};
  for (const wrap of paramsPane.querySelectorAll(".param")) {
    const name = wrap.dataset.param;
    wrap.classList.toggle(
      "param-from-config",
      Object.prototype.hasOwnProperty.call(fromConfig, name)
    );
    wrap.classList.toggle(
      "param-overridden",
      Object.prototype.hasOwnProperty.call(overrides, name)
    );
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
  paramChoices = {};
  const spec = part.params_spec;
  if (!spec || !Object.keys(spec).length) {
    paramsPane.innerHTML =
      '<div class="pane-note">This part exposes no parameters. Define a PARAMS dict in the script.</div>';
    appendWarningsHost();
    appendSpecsHost();
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
    const type = entry.type || "number";

    if (type === "bool") {
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "param-check";
      check.checked = Boolean(value);
      check.setAttribute("aria-label", name);
      check.addEventListener("change", () => queueParam(name, check.checked));
      ctl.appendChild(check);
    } else if (type === "enum") {
      const choices = entry.choices || [];
      paramChoices[name] = choices;
      const select = document.createElement("select");
      select.className = "param-select";
      select.setAttribute("aria-label", name);
      // Surface a stale override that matches no declared choice instead of
      // silently showing choices[0] (mirrors the material "(unknown)" row).
      // The change handler's `choice !== undefined` guard ignores value "-1".
      if (!choices.some((c) => c === value)) {
        const opt = document.createElement("option");
        opt.value = "-1";
        opt.textContent = `${value} (not in choices)`;
        opt.selected = true;
        opt.disabled = true;
        select.appendChild(opt);
      }
      choices.forEach((choice, i) => {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = String(choice);
        if (choice === value) opt.selected = true;
        select.appendChild(opt);
      });
      select.addEventListener("change", () => {
        const choice = choices[Number(select.value)];
        if (choice !== undefined) queueParam(name, choice);
      });
      ctl.appendChild(select);
    } else if (type === "string") {
      const text = document.createElement("input");
      text.type = "text";
      text.className = "param-text";
      if (entry.max_len != null) text.maxLength = entry.max_len;
      text.value = value == null ? "" : value;
      text.setAttribute("aria-label", `${name} value`);
      // "change" (blur/Enter), not per keystroke; queueParam keeps the debounce.
      text.addEventListener("change", () => queueParam(name, text.value));
      ctl.appendChild(text);
    } else {
      buildNumericControls(ctl, name, entry, value, type === "int");
    }
    wrap.appendChild(ctl);

    if (entry.description) {
      const desc = document.createElement("div");
      desc.className = "param-desc";
      desc.textContent = entry.description;
      wrap.appendChild(desc);
    }

    paramsPane.appendChild(wrap);
  }
  appendWarningsHost();
  appendSpecsHost();
}

function buildNumericControls(ctl, name, entry, value, isInt) {
  const hasRange = entry.min != null && entry.max != null;

  let slider = null;
  if (hasRange) {
    slider = document.createElement("input");
    slider.type = "range";
    slider.min = entry.min;
    slider.max = entry.max;
    slider.step = isInt ? 1 : niceStep(entry.min, entry.max);
    slider.value = value;
    slider.setAttribute("aria-label", name);
    ctl.appendChild(slider);
  }

  const num = document.createElement("input");
  num.type = "number";
  num.className = "param-num";
  num.step = isInt ? "1" : "any";
  if (entry.min != null) num.min = entry.min;
  if (entry.max != null) num.max = entry.max;
  num.value = value;
  num.setAttribute("aria-label", `${name} value`);
  ctl.appendChild(num);

  if (slider) {
    slider.addEventListener("input", () => {
      num.value = slider.value;
      queueParam(name, parseFloat(slider.value));
    });
  }
  num.addEventListener("input", () => {
    const v = parseFloat(num.value);
    if (!Number.isFinite(v)) return;
    // Mid-edit fractional values for an int param aren't sent (the kernel
    // rejects them); step="1" already marks the field invalid while typing.
    if (isInt && !Number.isInteger(v)) return;
    if (slider) slider.value = v;
    queueParam(name, v);
  });
  if (isInt) {
    // On commit (blur/Enter), round a lingering fractional value and send it.
    num.addEventListener("change", () => {
      const v = parseFloat(num.value);
      if (!Number.isFinite(v) || Number.isInteger(v)) return;
      const rounded = Math.round(v);
      num.value = rounded;
      if (slider) slider.value = rounded;
      queueParam(name, rounded);
    });
  }
}

function appendWarningsHost() {
  const w = document.createElement("div");
  w.className = "param-warnings";
  w.id = "param-warnings";
  paramsPane.appendChild(w);
}

// Design-spec chips sit right below the warnings, because a failing spec is
// the warnings tier. The host is appended by the pane builders (never by
// renderSpecs) so it is torn down and re-created with the controls and can
// never accumulate; it stays empty — and invisible, via `.spec-block:empty` —
// for a part that declares nothing.
function appendSpecsHost() {
  const s = document.createElement("div");
  s.className = "spec-block";
  s.id = "part-specs";
  paramsPane.appendChild(s);
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
  // A reference part declares no specs, so this host stays empty — but it is
  // appended anyway so renderSpecs has one contract, not two.
  appendSpecsHost();
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
      if (input.type === "checkbox") {
        // Focus on a checkbox is not an in-progress edit — always sync it.
        input.checked = Boolean(value);
        continue;
      }
      // Text/number/range: skip the focused control so typing isn't clobbered.
      if (document.activeElement === input) continue;
      input.value = value;
    }
    const select = wrap.querySelector("select");
    if (select) {
      // Focus on a (closed) select is not an in-progress edit either.
      // Options hold choice *indices* as values; a "(not in choices)"
      // placeholder holds "-1", so match by option value, not selectedIndex.
      const idx = (paramChoices[name] || []).findIndex((c) => c === value);
      if (idx >= 0) {
        select.value = String(idx);
      } else {
        const placeholder = select.querySelector('option[value="-1"]');
        if (placeholder) placeholder.selected = true;
        else select.selectedIndex = -1;
      }
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

// -------------------------------------------------------------- design specs

// One chip per declared check, live on every rebuild: `part.specs` rides the
// part payload (get_part and the PATCH post-state both carry it), so this
// needs no new event and no new state key.
//
// `part.specs == null` — the part declares nothing — renders NOTHING: no
// header, no "no specs" note. "Nothing declared" and "declared but not
// evaluated" are different facts and only the second one is worth pixels.
function renderSpecs(part) {
  const host = document.getElementById("part-specs");
  if (!host) return;
  host.textContent = "";
  const specs = part.specs;
  if (!specs) return;

  const checks = specs.checks || [];
  const note = specSummaryText(specs, checks);
  if (note) {
    const line = document.createElement("div");
    line.className = "spec-summary";
    line.textContent = note;
    host.appendChild(line);
  }

  const chips = document.createElement("div");
  chips.className = "spec-chips";
  if (checks.length) {
    for (const check of checks) chips.appendChild(specChip(check));
  } else if (specs.status === "error") {
    // Residue with no records at all (the rebuild wrapper's catch-all): say
    // that the specs did not run rather than showing an empty, reassuring row.
    chips.appendChild(
      specChip({ name: "specs", status: "error", message: specError(specs) })
    );
  }
  // `SPECS = []` declares nothing to show: an empty row is a 12px strip of
  // margin that reads as "something is here", which is worse than nothing.
  if (chips.childElementCount) host.appendChild(chips);
}

// Only a red strip gets a summary line — a green one is just chips (the
// design's rule: no chrome for the healthy case).
function specSummaryText(specs, checks) {
  if (specs.status === "error") {
    return `Design specs could not be evaluated — ${specError(specs)}`;
  }
  const s = specs.summary || {};
  const failed = s.failed || 0;
  const errors = s.errors || 0;
  if (!failed && !errors) return "";
  const parts = [];
  if (failed) parts.push(`${failed} failing`);
  if (errors) parts.push(`${errors} errored`);
  const total = s.total != null ? s.total : checks.length;
  return `${parts.join(", ")} of ${total} design ${
    total === 1 ? "spec" : "specs"
  }`;
}

function specError(specs) {
  const err = specs.error || {};
  return err.message || err.type || "unknown error";
}

// The chip atom, modelled on proposals.js's gateChip. createElement +
// textContent only: names, requirements and messages are script-controlled
// strings, so the template-literal row()/arow() builders above are the wrong
// precedent here.
function specChip(check) {
  const status = check.status || "error";
  const chip = document.createElement("span");
  chip.className = `spec-chip spec-${status}`;
  chip.textContent = check.name || check.id || check.kind || "check";
  chip.title = specTitle(check, status);
  return chip;
}

function specTitle(check, status) {
  const lines = [`${check.name || "check"} — ${status}`];
  const measured = specMeasured(check);
  const limit = specLimit(check.limit);
  if (measured && limit) lines.push(`${measured} vs ${limit}`);
  else if (measured) lines.push(measured);
  else if (limit) lines.push(limit);
  if (check.requirement) lines.push(`requirement: ${check.requirement}`);
  if (check.message) lines.push(check.message);
  if (status === "skip" && check.reason) {
    lines.push(check.hint ? `${check.reason} — ${check.hint}` : check.reason);
  }
  return lines.join("\n");
}

function specMeasured(check) {
  const value = check.measured;
  if (value == null) return "";
  const unit = check.unit ? ` ${check.unit}` : "";
  if (Array.isArray(value)) return `${value.map(specNum).join(" x ")}${unit}`;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return `${specNum(value)}${unit}`;
  return String(value);
}

// The limit dict is kind-specific ({min_g}, {within_mm: [x, y, z]},
// {max_vm_mpa}, …), so it is rendered generically rather than switched on
// kind: a key this build has never heard of still reads as something ("max
// vm 200 MPa") instead of vanishing. Longest suffix first — _mm3 before _mm.
const LIMIT_UNITS = [
  ["_mm3", "mm3"],
  ["_mpa", "MPa"],
  ["_mm", "mm"],
  ["_deg", "deg"],
  ["_g", "g"],
  ["_n", "N"],
];

function specLimit(limit) {
  if (!limit || typeof limit !== "object") return "";
  const parts = [];
  for (const [key, value] of Object.entries(limit)) {
    if (value == null) continue;
    const shown = Array.isArray(value)
      ? value.map(specNum).join(" x ")
      : typeof value === "number"
        ? specNum(value)
        : String(value);
    const hit = LIMIT_UNITS.find(([suffix]) => key.endsWith(suffix));
    const label = (hit ? key.slice(0, -hit[0].length) : key).replace(/_/g, " ");
    parts.push(`${label} ${shown}${hit ? ` ${hit[1]}` : ""}`.trim());
  }
  return parts.join(", ");
}

function specNum(value) {
  return typeof value === "number" ? String(Number(value.toPrecision(4))) : String(value);
}

// ------------------------------------------------------------- param patch

function queueParam(name, value) {
  pending[name] = value;
  // A control being dragged is the other half of "taken by editing".
  syncClaiming();
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
    syncClaiming();
  };
  patchChain = patchChain.then(async () => {
    try {
      let result;
      try {
        result = await api.patchParams(proj, partId, values);
      } catch (err) {
        // A refused write is offered the override exactly once; the arming
        // route is single-use, so a second 409 is a real refusal.
        if (!(await actions.handleWriteConflict(err, partId))) throw err;
        result = await api.patchParams(proj, partId, values);
      }
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
      // The rebuild post-state carries the shape-tier spec summary (absent on
      // a failed build), so the chips repaint with this response instead of
      // waiting for the rebuild_finished → refreshPartDetail round trip.
      if ("specs" in result) state.part.specs = result.specs;
      // Spread, never replace: `status` also carries `diverged` /
      // `diverged_params`, which a rebuild says nothing about. Overwriting the
      // object wholesale blinked the divergence chip and its Reset button off
      // after every debounced PATCH — a slider drag flapped them and jumped
      // the pane by the chip row's height.
      state.part.status = {
        ...(state.part.status || {}),
        state: "ok",
        error: null,
        warnings: result.warnings || [],
      };
      setState({ part: state.part });
      hideBanner();
    }
    actions.markPartState(partId, "ok");
    actions.reloadMesh(partId);
  } else {
    if (isCurrent) {
      state.part.status = {
        ...(state.part.status || {}),
        state: "error",
        error: result.error,
        warnings: [],
      };
      // A failed rebuild carries no `specs` key — there is no shape to assert
      // over — so the chips from the LAST good build would sit, green, beside
      // a red build banner. Clear them: nothing evaluated is not "all fine".
      state.part.specs = null;
      if (values) Object.assign(state.part.params, values);
      setState({ part: state.part });
      showBanner(result.error);
    }
    actions.markPartState(partId, "error");
  }
}

// -------------------------------------------------------------- save flow

async function saveScript(partId, script, retrying = false) {
  editor.setSaving(true);
  try {
    let result;
    try {
      result = await api.updatePart(state.projectName, partId, { script });
    } catch (err) {
      if (retrying || !(await actions.handleWriteConflict(err, partId))) throw err;
      editor.setSaving(false);
      return saveScript(partId, script, true);
    }
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

// ------------------------------------------------------------------ claims

/** One rule, one place: we are claiming iff there is an edit in progress —
 *  an unsaved buffer, or a param write queued or in flight. Presence turns
 *  that into a per-part claim on the next heartbeat, and drops it as soon as
 *  it is false: a claim nobody is using teaches people to click Override
 *  reflexively, which is worse than no claim at all. */
function syncClaiming() {
  presence.setClaiming(
    editor.isDirty() ||
      Object.keys(pending).length > 0 ||
      Object.keys(inflight).length > 0
  );
}

/** "<label> is editing" above the editor. The claim HOLDER's display label,
 *  never the raw identity — that is what the tooltip is for. */
function renderClaimChip() {
  if (!editorClaimEl) return;
  const partId = state.part ? state.part.id : null;
  const claim = partId ? presence.otherClaim(partId) : null;
  if (!claim) {
    editorClaimEl.classList.add("hidden");
    return;
  }
  editorClaimEl.textContent = `${presence.labelFor(claim.holder)} is editing`;
  editorClaimEl.title =
    `${claim.holder} (${claim.holder_kind}) has ${partId} open. Saving will ` +
    "offer to override — claims are soft and expire on their own.";
  editorClaimEl.classList.remove("hidden");
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
    </table>
    ${solidsTable(m.solids)}`;
  return host;
}

function solidsTable(solids) {
  if (!Array.isArray(solids) || !solids.length) return "";
  const rows = solids
    .map((s) =>
      row(
        escapeHtml(s.label),
        `${fmt(s.volume_mm3)}<span class="unit">mm³</span> · ` +
          `${fmt(s.mass_g)}<span class="unit">g</span>`
      )
    )
    .join("");
  return `<div class="mat-head">Per-solid</div><table class="metrics-table">${rows}</table>`;
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
    ["curvature", "Curvature"],
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
  for (const kind of ["section", "wall", "inertia", "curvature"]) {
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
  curvature: "Surface curvature",
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
  if (kind === "curvature") {
    let out =
      arow("Faces", fmt(d.n_faces, 0)) +
      arow("Worst |K|", `${fmtCurv(d.worst_gaussian_abs)}<span class="unit">1/mm²</span>`) +
      arow("Sampled", fmt(d.sampled_points, 0));
    const faces = d.faces || [];
    for (const f of faces.slice(0, 8)) {
      out += arow(
        `Face ${f.index}`,
        `K ${fmtCurv(f.gaussian.min)}…${fmtCurv(f.gaussian.max)} · ` +
          `H ${fmtCurv(f.mean_curvature.min)}…${fmtCurv(f.mean_curvature.max)}`
      );
    }
    if (faces.length > 8) {
      out += arow("", `+${faces.length - 8} more faces`);
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

// Curvatures are tiny (1/mm, 1/mm²): exponent notation below fmt()'s
// resolution, plain formatting above it, flat treated as exactly 0.
const fmtCurv = (v) => {
  if (v == null || !Number.isFinite(v)) return "—";
  if (Math.abs(v) < 1e-9) return "0";
  return Math.abs(v) < 0.01 ? v.toExponential(2) : fmt(v, 3);
};

function arow(key, valueHtml) {
  return `<div class="analysis-row"><span class="analysis-k">${key}</span>` +
    `<span class="analysis-v">${valueHtml}</span></div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
