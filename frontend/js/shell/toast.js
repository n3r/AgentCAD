// PRD-026 shell — transient notices, promoted out of `main.js` unchanged in
// behaviour (4 s, 8 s for an error) and widened by exactly two things the
// shell needs: a caller-supplied `id` (so a repeating notice REPLACES itself
// instead of stacking N copies) and an optional action button.
//
// A toast is the wrong place for anything the user must act on or read
// carefully — a tool refusal belongs in the dialog that asked (spec §3), and
// this module deliberately offers no way to make one sticky forever.

const TIMEOUTS = { error: 8000, info: 4000, success: 4000, warn: 6000 };

let host = null;
const live = new Map(); // id -> {el, timer}
let seq = 0;

/** Point the module at its host element. Optional: `toast()` finds `#toasts`
 *  itself, which keeps it usable from a module that runs before `boot()`. */
export function init(hostEl) {
  if (hostEl) host = hostEl;
  return hostOf();
}

function hostOf() {
  if (host && host.isConnected !== false) return host;
  if (typeof document === "undefined") return null;
  host = document.getElementById("toasts");
  return host;
}

/** Show a notice. Returns its id (so `dismiss(id)` can take it back).
 *
 *  `opts.action` is `{label, run}` — one button, because a toast with two
 *  choices is a dialog that lost its way.
 */
export function toast(message, kind, opts = {}) {
  // `|| "info"`, not a default parameter: a caller passing `null`/`""` for the
  // kind must not end up with `class="toast null"`.
  const k = kind || "info";
  const id = opts.id || `toast-${(seq += 1)}`;
  const parent = hostOf();
  if (!parent) return id; // node / pre-boot: the message is not the app's state
  dismiss(id);

  const el = document.createElement("div");
  el.className = `toast ${k === "info" ? "" : k}`.trim();
  el.dataset.toastId = id;
  // A toast appears without user action; screen readers need to be told.
  el.setAttribute("role", k === "error" ? "alert" : "status");
  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = message;
  el.appendChild(text);

  if (opts.action && typeof opts.action.run === "function") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-action";
    btn.textContent = opts.action.label || "Undo";
    btn.addEventListener("click", () => {
      dismiss(id);
      opts.action.run();
    });
    el.appendChild(btn);
  }

  parent.appendChild(el);
  const ms = opts.timeout == null ? (TIMEOUTS[k] || TIMEOUTS.info) : opts.timeout;
  const timer = ms > 0 ? setTimeout(() => dismiss(id), ms) : null;
  live.set(id, { el, timer });
  return id;
}

export function dismiss(id) {
  const entry = live.get(id);
  if (!entry) return false;
  if (entry.timer) clearTimeout(entry.timer);
  entry.el.remove();
  live.delete(id);
  return true;
}
