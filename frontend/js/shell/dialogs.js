// PRD-026 shell — the dialog system: one primitive, one overlay stack, one
// Esc listener, one registry of openable views.
//
// Three things are load-bearing here and each replaces a v0.1 habit:
//
//   * `open(spec)` resolves `{ok, values, button}` — the shape
//     `setupClaimDialog`/`askOverride` already had, generalised. A refusal at
//     submit time is shown IN the dialog (`setError`), never by closing and
//     toasting: a message that disappears is not an answer to "why did that
//     fail".
//   * ONE module-level stack and ONE document `keydown` listener own Esc and
//     the focus trap. Every dialog adding its own listener is how you get an
//     Esc that closes two things (and, with the legacy modals adopted in
//     slice 2, an Esc that closes the wrong one).
//   * `register(view, opener)` + `openView(view, args, {by})` is the surface
//     `ui_open` resolves through, and the ONLY way an agent can open UI. An
//     agent-opened dialog carries a visible attribution chip; an unknown view
//     is a toast and a refusal, never a guess.
//
// Node-importable: nothing touches `document` until `init()`/`open()` runs.

import * as model from "./dialogs_model.js";
import { toast } from "./toast.js";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), '
  + 'select:not([disabled]), textarea:not([disabled]), '
  + '[tabindex]:not([tabindex="-1"])';

let host = null;
let installed = false;
let seq = 0;
let emitter = () => {};
// Set for the duration of ONE `openView` call and consumed by the first
// `open()` the opener performs — an opener that awaits before opening loses
// the chip, which is the honest failure (we cannot prove which dialog the
// agent's request produced).
let pendingAttribution = null;

const stackEntries = [];      // bottom … top
const registry = new Map();   // view -> {opener, meta}

/** Create/adopt `#dialog-host` and install the one keydown listener. */
export function init(hostEl) {
  if (typeof document === "undefined") return null;
  host = hostEl || document.getElementById("dialog-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "dialog-host";
    document.body.appendChild(host);
  }
  if (!installed) {
    // Capture phase: the stack must answer Esc/Tab BEFORE the panel-level
    // handlers that are still out there (setupMenus, the sketcher's onKey).
    document.addEventListener("keydown", onKeyDown, true);
    installed = true;
  }
  return host;
}

/** Route UX events out (slice 3 wires `shell/events.js`). Default: no-op. */
export function setEmitter(fn) {
  emitter = typeof fn === "function" ? fn : () => {};
}

// ------------------------------------------------------------------ the stack

/** The open overlays, bottom first. A copy: nobody mutates the stack but us. */
export function stack() {
  return stackEntries.map((e) => ({
    id: e.id, view: e.view, modal: e.modal, kind: e.kind,
  }));
}

/** True while anything modal is up.
 *
 *  It ALSO answers true for a legacy `.modal-overlay` that has not been
 *  adopted yet — that is `main.js`'s old `modalOpen()` and it is still what
 *  suppresses bare-key shortcuts behind the seven hand-rolled modals. Slice 2
 *  adopts them onto `attachLegacy` and the DOM query below becomes dead
 *  weight; until then removing it would let `F`/`G`/`R` fire behind them.
 */
export function isModalOpen() {
  if (stackEntries.some((e) => e.modal)) return true;
  if (typeof document === "undefined") return false;
  return document.querySelector(".modal-overlay:not(.hidden)") != null;
}

function top() {
  return stackEntries.length ? stackEntries[stackEntries.length - 1] : null;
}

function push(entry) {
  stackEntries.push(entry);
}

function drop(entry) {
  const i = stackEntries.indexOf(entry);
  if (i >= 0) stackEntries.splice(i, 1);
  const restore = entry.restore;
  if (restore && typeof restore.focus === "function" && restore.isConnected) {
    restore.focus();
  }
}

// ---------------------------------------------------------------- primitives

/** Open a dialog. Resolves `{ok, values, button}` when it closes. */
export function open(spec) {
  init();
  const attribution = pendingAttribution;
  pendingAttribution = null;
  const s = { modal: true, ...spec };
  const id = `dlg${(seq += 1)}`;
  // `entry.fields` is the list of CONTROLS — `readValues`, `refreshValidity`
  // and `model.validate` all walk it, and a `{divider: true}` separator (the
  // palette's required/optional rule, §3) is not one. The renderer still
  // receives the unfiltered `s.fields`, because the separator's whole job is
  // to be drawn.
  const fields = (Array.isArray(s.fields) ? s.fields : [])
    .filter((f) => f && !f.divider && f.name);
  const bodyNode = s.body && typeof s.body === "object";
  const { html, ids } = model.markup({
    ...s,
    uid: seq,
    body: bodyNode ? null : s.body,
    bodyNode,
    attribution: attribution || s.attribution || null,
  });

  const frag = document.createElement("div");
  frag.innerHTML = html;
  const overlay = frag.firstElementChild;
  const dialogEl = overlay.querySelector(".dlg");
  if (bodyNode) overlay.querySelector(`#${ids.body}`).appendChild(s.body);
  host.appendChild(overlay);

  const entry = {
    kind: "dialog", id, view: s.view || "dialog", modal: s.modal !== false,
    el: overlay, dialogEl, ids, fields, spec: s,
    restore: document.activeElement,
    touched: new Set(), settled: false, resolve: null,
  };
  const promise = new Promise((resolve) => { entry.resolve = resolve; });
  entry.handle = {
    id, view: entry.view, el: dialogEl,
    setError: (msg) => setError(entry, msg),
    close: (result) => finish(entry, result),
  };
  push(entry);
  wire(entry);
  refreshValidity(entry);
  focusFirst(entry);
  emit({ type: "dialog_opened", view: entry.view });

  promise.handle = entry.handle;
  return promise;
}

/** Yes/no. Resolves the boolean, because a caller of `confirm` wants one. */
export function confirm(opts) {
  const o = opts || {};
  return open({
    view: o.view || "confirm",
    title: o.title || "Are you sure?",
    body: o.body,
    note: o.note,
    danger: !!o.danger,
    width: o.width || "narrow",
    buttons: [
      { id: "cancel", label: o.cancelLabel || "Cancel" },
      {
        id: "confirm",
        label: o.confirmLabel || (o.danger ? "Delete" : "OK"),
        kind: o.danger ? "danger" : "primary",
        submits: true,
      },
    ],
  }).then((res) => res.ok);
}

/** One value in, `null` when cancelled (spec §1.1: a string or null). */
export function prompt(opts) {
  const o = opts || {};
  return open({
    view: o.view || "prompt",
    title: o.title || "",
    body: o.body,
    width: o.width || "narrow",
    fields: [{
      name: "value",
      label: o.label || o.title || "Value",
      type: o.type || "text",
      value: o.value,
      placeholder: o.placeholder,
      pattern: o.pattern,
      patternMessage: o.patternMessage,
      required: o.required !== false,
      help: o.help,
      min: o.min, max: o.max, step: o.step,
      validate: o.validate,
    }],
    buttons: [
      { id: "cancel", label: o.cancelLabel || "Cancel" },
      { id: "ok", label: o.okLabel || "OK", kind: "primary", submits: true },
    ],
    onSubmit: o.onSubmit,
  }).then((res) => (res.ok && res.values.value != null
    ? String(res.values.value)
    : null));
}

/** The whole value object, or `null` when cancelled. */
export function form(spec) {
  return open(spec).then((res) => (res.ok ? res.values : null));
}

/** Close a dialog from outside (`id`, or the handle `open()` hands back). */
export function close(idOrHandle, result) {
  const entry = entryOf(idOrHandle);
  if (!entry) return false;
  finish(entry, result || { ok: false, values: readValues(entry), button: null });
  return true;
}

/** Show a submit-time refusal INSIDE the dialog. `handle`, or a dialog id. */
export function setError(idOrHandle, message) {
  const entry = idOrHandle && idOrHandle.kind === "dialog"
    ? idOrHandle
    : entryOf(idOrHandle);
  if (!entry) return false;
  const box = entry.el.querySelector(`#${entry.ids.error}`);
  if (box) box.textContent = message || "";
  entry.el.querySelector(".dlg").classList.toggle("has-error", !!message);
  return true;
}

function entryOf(idOrHandle) {
  if (!idOrHandle) return null;
  const id = typeof idOrHandle === "string" ? idOrHandle : idOrHandle.id;
  return stackEntries.find((e) => e.id === id) || null;
}

// -------------------------------------------------------------- the registry

/** Make `view` openable — by the palette, by a menu, and by the `ui_open`
 *  tool. `opener(args)` does the opening; `meta` describes the row. */
export function register(view, opener, meta) {
  if (typeof opener !== "function") {
    throw new Error(`dialog view ${view} needs an opener`);
  }
  if (registry.has(view)) throw new Error(`duplicate dialog view: ${view}`);
  registry.set(view, { opener, meta: meta || {} });
  return view;
}

/** The registered views eligible in `ctx` — the palette's "Open: …" rows. */
export function views(ctx) {
  const out = [];
  for (const [view, entry] of registry) {
    if (entry.meta.when && !entry.meta.when(ctx)) continue;
    out.push({
      view,
      title: entry.meta.title || view,
      description: entry.meta.description || "",
      agentOpenable: entry.meta.agentOpenable !== false,
    });
  }
  return out;
}

/** Open a registered view. The `ui_open` landing site.
 *
 *  An unknown view is refused in the open, not swallowed: the agent asked for
 *  something this shell does not have and the user is the one who can tell
 *  whether that matters.
 */
export async function openView(view, args, opts) {
  const by = (opts && opts.by) || "user";
  const entry = registry.get(view);
  if (!entry) {
    toast(`Agent asked to open “${view}”, which this shell does not have`,
          "error");
    return { ok: false, reason: "unknown_view", view };
  }
  if (by === "agent" && entry.meta.agentOpenable === false) {
    toast(`“${view}” cannot be opened by an agent`, "error");
    return { ok: false, reason: "not_agent_openable", view };
  }
  pendingAttribution = by === "agent" ? "opened by agent" : null;
  try {
    const result = await entry.opener(args || {});
    return { ok: true, view, result };
  } finally {
    pendingAttribution = null;
  }
}

/** Adopt an existing `.modal-overlay` into the stack (spec §1.4).
 *
 *  The overlay keeps its markup and its own open/close functions; what it
 *  gains is the stack (so Esc closes the TOP thing), the focus trap and the
 *  focus restore. The handle is explicit rather than a `MutationObserver`:
 *  the module already knows when it opens and closes, and an observer would
 *  make the ordering of two overlays depend on the microtask queue.
 */
export function attachLegacy(overlayEl, opts) {
  const o = opts || {};
  const view = o.view;
  let entry = null;
  if (typeof o.open === "function") {
    register(view, o.open, {
      title: o.title || view,
      description: o.description || "",
      agentOpenable: o.agentOpenable !== false,
    });
  }
  const handle = {
    view,
    notifyOpen() {
      if (entry) return handle;
      init();
      entry = {
        kind: "legacy", id: `legacy-${view}-${(seq += 1)}`, view, modal: true,
        el: overlayEl, dialogEl: overlayEl.querySelector(".modal") || overlayEl,
        restore: document.activeElement,
        // The stack is popped HERE, not by the adopter. If Esc only called
        // `onClose` and the adopter forgot to route back through
        // `notifyClose` (or its close path early-returned), the entry would
        // sit on the stack forever — `isModalOpen()` permanently true, every
        // global shortcut dead, and Esc "closing" a dialog already gone.
        // `notifyClose` is idempotent, so an adopter that DOES call it too is
        // free.
        close: () => {
          if (o.onClose) o.onClose();
          handle.notifyClose();
        },
      };
      push(entry);
      emit({ type: "dialog_opened", view });
      return handle;
    },
    notifyClose() {
      if (!entry) return handle;
      const done = entry;
      entry = null;
      drop(done);
      return handle;
    },
    isOpen: () => entry != null,
    detach() {
      handle.notifyClose();
      registry.delete(view);
    },
  };
  if (o.isOpen && o.isOpen()) handle.notifyOpen();
  return handle;
}

// --------------------------------------------------------------------- wiring

function wire(entry) {
  const el = entry.el;
  for (const btn of el.querySelectorAll("[data-btn]")) {
    btn.addEventListener("click", () => {
      if (btn.dataset.submits) submit(entry, btn.dataset.btn);
      else finish(entry, { ok: false, values: readValues(entry), button: btn.dataset.btn });
    });
  }
  if (entry.modal) {
    el.addEventListener("mousedown", (e) => {
      if (e.target === el) finish(entry, { ok: false, values: readValues(entry), button: null });
    });
  }
  const formEl = entry.ids.form && el.querySelector(`#${entry.ids.form}`);
  if (formEl) {
    formEl.addEventListener("submit", (e) => {
      e.preventDefault();
      submit(entry, primaryButtonId(entry));
    });
    // `input` covers typing, `change` covers a checkbox/select the user picked
    // without typing — both mark the field touched, or its error would never
    // be allowed to show.
    const touch = (e) => {
      const name = e.target && e.target.name;
      if (name) entry.touched.add(name);
      refreshValidity(entry);
      setError(entry, "");
    };
    formEl.addEventListener("input", touch);
    formEl.addEventListener("change", touch);
  }
}

function primaryButtonId(entry) {
  const btn = entry.el.querySelector("[data-submits]");
  return btn ? btn.dataset.btn : "ok";
}

function controlOf(entry, name) {
  const ids = entry.ids.fields[name];
  return ids ? entry.el.querySelector(`#${ids.input}`) : null;
}

function readValues(entry) {
  const values = {};
  for (const field of entry.fields) {
    const el = controlOf(entry, field.name);
    if (!el) continue;
    if (field.type === "checkbox") values[field.name] = el.checked;
    else if (field.type === "number") {
      values[field.name] = el.value === "" ? "" : Number(el.value);
    } else values[field.name] = el.value;
  }
  return values;
}

function refreshValidity(entry) {
  if (!entry.fields.length) return { errors: {}, valid: true };
  const values = readValues(entry);
  const { errors, valid } = model.validate(entry.fields, values);
  for (const field of entry.fields) {
    const ids = entry.ids.fields[field.name];
    const control = controlOf(entry, field.name);
    const box = entry.el.querySelector(`#${ids.error}`);
    // An error is SHOWN only once the user has been near the field (or has
    // tried to submit): a form that greets you in red has told you nothing.
    const show = entry.touched.has(field.name) && errors[field.name];
    if (box) box.textContent = show ? errors[field.name] : "";
    if (control) {
      control.setAttribute("aria-invalid", show ? "true" : "false");
      const described = [ids.help, show ? ids.error : null].filter(Boolean);
      if (described.length) control.setAttribute("aria-describedby", described.join(" "));
      else control.removeAttribute("aria-describedby");
    }
  }
  const submitBtn = entry.el.querySelector("[data-submits]");
  if (submitBtn) submitBtn.disabled = !valid;
  return { errors, valid };
}

async function submit(entry, button) {
  for (const field of entry.fields) entry.touched.add(field.name);
  const { errors, valid } = refreshValidity(entry);
  if (!valid) {
    const first = Object.keys(errors)[0];
    const control = controlOf(entry, first);
    if (control) control.focus();
    return;
  }
  const values = readValues(entry);
  if (typeof entry.spec.onSubmit === "function") {
    let keep;
    try {
      keep = await entry.spec.onSubmit(values, entry.handle);
    } catch (err) {
      setError(entry, err && err.message ? err.message : String(err));
      return;
    }
    if (keep === false) return; // the caller kept it open to show an error
  }
  emit({ type: "dialog_submitted", view: entry.view });
  finish(entry, { ok: true, values, button });
}

function finish(entry, result) {
  if (entry.settled) return;
  entry.settled = true;
  entry.el.remove();
  drop(entry);
  // Always the full shape: `handle.close()` with no argument must not resolve
  // `undefined` into a caller that is about to read `.ok`.
  entry.resolve(result || { ok: false, values: {}, button: null });
}

function focusFirst(entry) {
  const first = entry.fields.length ? controlOf(entry, entry.fields[0].name) : null;
  // A DANGER dialog with nothing to fill in opens on its SAFE button: the
  // destructive one is one Tab away and never one stray Enter away.
  const preferred = entry.spec.danger
    ? entry.el.querySelector("[data-btn]:not([data-submits])")
    : entry.el.querySelector("[data-submits]");
  const target = first || preferred || entry.el.querySelector("[data-btn]");
  if (target) target.focus();
}

function emit(event) {
  try {
    emitter(event);
  } catch (err) {
    console.error("dialog emitter failed", err);
  }
}

// -------------------------------------------------------------- the listener

/** Which stack entry owns an Esc: the topmost modal, or a non-modal that
 *  currently holds focus. Pure (the focus test is injected) so the rule is
 *  unit-testable without a DOM. */
export function escOwner(entries, isFocusInside) {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const entry = entries[i];
    if (entry.modal || isFocusInside(entry)) return entry;
  }
  return null;
}

function onKeyDown(e) {
  const entry = top();
  if (!entry) return;
  if (e.key === "Escape") {
    // Esc belongs to the topmost MODAL — and to a non-modal panel only while
    // focus is inside it. Otherwise the first non-modal dialog (slice 3's
    // tool-result panel) would swallow the Esc the sketcher needs to cancel a
    // pending entity, from across the screen, forever.
    const owner = escOwner(stackEntries, (en) => en.el
      && typeof en.el.contains === "function"
      && en.el.contains(document.activeElement));
    if (!owner) return;
    e.preventDefault();
    e.stopPropagation();
    if (owner.kind === "legacy") owner.close();
    else finish(owner, { ok: false, values: readValues(owner), button: null });
    return;
  }
  if (e.key === "Tab" && entry.modal) {
    const list = [...entry.dialogEl.querySelectorAll(FOCUSABLE)]
      .filter((el) => el.offsetParent !== null || el === document.activeElement);
    const next = model.focusables(list, list.indexOf(document.activeElement),
                                  e.shiftKey);
    if (next < 0) return;
    e.preventDefault();
    list[next].focus();
    return;
  }
  if (e.key === "Enter" && entry.kind === "dialog") {
    const target = e.target;
    const multiline = target && target.tagName === "TEXTAREA";
    if (multiline && !(e.metaKey || e.ctrlKey)) return;
    if (target && target.tagName === "BUTTON") return; // its own click fires
    if (!entry.dialogEl.contains(target)) return;
    e.preventDefault();
    e.stopPropagation();
    submit(entry, primaryButtonId(entry));
  }
}

// Test seam — the node round-trip drives the shipped listener rather than a
// copy of it (the `shortcuts.__shortcutsDispatch__` precedent).
export const __dialogsDispatch__ = { onKeyDown, escOwner };
