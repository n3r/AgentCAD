// PRD-026 shell — the dialog's markup, its validation rules and the focus-trap
// arithmetic. PURE: no DOM, no imports, runs in node.
//
// Why the markup is a STRING built here rather than DOM built in dialogs.js:
// the accessibility contract (role, aria-modal, aria-labelledby pointing at an
// id that exists, a `<label for>` per control, text on every button,
// aria-invalid + aria-describedby on an errored field) is then a property of a
// value a test can read, and the repo adds no node dependency to check it
// (spec §8: "an equivalent static pass at MVP level"). dialogs.js parses the
// string into the document once and binds to the ids returned beside it.

let counter = 0;

/** HTML-escape. Every interpolation below goes through it — a dialog's title
 *  can be a part id, a branch name or a server error message, none of which
 *  this module is allowed to trust. */
export function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const WIDTHS = new Set(["narrow", "default", "wide"]);

const DEFAULT_BUTTONS = [
  { id: "cancel", label: "Cancel", kind: "default" },
  { id: "ok", label: "OK", kind: "primary", submits: true },
];

/** `{html, ids}` for one dialog.
 *
 *  `spec` is the `dialogs.open()` spec (§1.1) plus the two things only the
 *  renderer needs: `uid` (stable ids, for tests) and `errors` (the current
 *  per-field messages — dialogs.js re-renders nothing, it writes into the
 *  error nodes, but a dialog can be opened with errors already known).
 */
export function markup(spec) {
  const s = spec || {};
  const uid = s.uid == null ? (counter += 1) : s.uid;
  const fields = Array.isArray(s.fields) ? s.fields : [];
  const buttons = Array.isArray(s.buttons) && s.buttons.length
    ? s.buttons
    : DEFAULT_BUTTONS;
  const errors = s.errors || {};
  const hasBody = s.body != null || s.bodyNode === true;

  const ids = {
    overlay: `dlg-overlay-${uid}`,
    dialog: `dlg-${uid}`,
    title: `dlg-title-${uid}`,
    body: hasBody ? `dlg-body-${uid}` : null,
    form: `dlg-form-${uid}`,
    error: `dlg-formerror-${uid}`,
    fields: {},
    buttons: {},
  };

  const classes = ["dlg"];
  // Whitelisted, not escaped: `width` lands inside a class attribute and
  // slice 5 pipes `ui_open`'s `args` into openers, so the only safe answer is
  // "one of three known words or nothing".
  if (WIDTHS.has(s.width) && s.width !== "default") classes.push(s.width);
  if (s.danger) classes.push("danger");
  if (s.modal === false) classes.push("nonmodal");

  const described = [];
  if (ids.body) described.push(ids.body);

  const head = [
    `<h2 class="dlg-title" id="${ids.title}">${escapeHtml(s.title || "")}</h2>`,
    s.attribution
      ? `<span class="dlg-attribution">${escapeHtml(s.attribution)}</span>`
      : "",
  ].join("");

  // A text body is a <p>; a NODE body (the "?" cheat-sheet's table) gets an
  // empty <div> for dialogs.js to append into — a <p> would be an invalid
  // parent for the block content those bodies carry.
  const bodyHtml = !ids.body
    ? ""
    : (s.bodyNode === true
      ? `<div class="dlg-text" id="${ids.body}"></div>`
      : `<p class="dlg-text" id="${ids.body}">${escapeHtml(s.body)}</p>`);
  const noteHtml = s.note
    ? `<p class="dlg-note">${escapeHtml(s.note)}</p>`
    : "";

  const fieldsHtml = fields
    .map((f) => renderField(f, uid, ids, errors[f.name]))
    .join("");

  const foot = buttons.map((b) => {
    const bid = `dlg-btn-${uid}-${b.id}`;
    ids.buttons[b.id] = bid;
    const kind = b.kind && b.kind !== "default" ? ` ${b.kind}` : "";
    return `<button type="button" class="dlg-btn${kind}" id="${bid}" `
      + `data-btn="${escapeHtml(b.id)}"${b.submits ? ' data-submits="1"' : ""}>`
      + `${escapeHtml(b.label || b.id)}</button>`;
  }).join("");

  // The overlay carries `nonmodal` too, so the CSS never has to ask `:has()`
  // whether its child is one (unsupported `:has()` = a silently modal panel).
  const html =
    `<div class="dlg-overlay${s.modal === false ? " nonmodal" : ""}" `
    + `id="${ids.overlay}" `
    + `data-view="${escapeHtml(s.view || "")}">`
    + `<div class="${classes.join(" ")}" id="${ids.dialog}" role="dialog" `
    + `aria-modal="${s.modal === false ? "false" : "true"}" `
    + `aria-labelledby="${ids.title}"`
    + (described.length ? ` aria-describedby="${described.join(" ")}"` : "")
    + ">"
    + `<header class="dlg-head">${head}</header>`
    + `<div class="dlg-body">${bodyHtml}${noteHtml}`
    + (fields.length
      ? `<form class="dlg-form" id="${ids.form}">${fieldsHtml}</form>`
      : "")
    + "</div>"
    + `<footer class="dlg-foot">`
    + `<div class="dlg-foot-error" id="${ids.error}" role="alert"></div>`
    + `<div class="dlg-btns">${foot}</div>`
    + "</footer>"
    + "</div></div>";

  return { html, ids };
}

function renderField(field, uid, ids, error) {
  // A `{divider: true}` entry is a SEPARATOR, not a control: the palette's
  // `formFields` emits one between a tool's required and optional arguments
  // (PRD-026 §3). Without this branch it fell through to the generic `else`
  // and rendered an unlabeled, focusable `<input type="text">` under
  // `ids.fields["undefined"]` — an extra Tab stop with no accessible name.
  if (field.divider) {
    return '<div class="dlg-divider" role="separator" aria-orientation="horizontal"></div>';
  }
  const name = field.name;
  const base = `dlg-f-${uid}-${cssSafe(name)}`;
  const entry = {
    input: base,
    label: `${base}-label`,
    help: field.help ? `${base}-help` : null,
    error: `${base}-error`,
  };
  ids.fields[name] = entry;

  const describedBy = [entry.help, error ? entry.error : null]
    .filter(Boolean).join(" ");
  const common =
    `id="${entry.input}" name="${escapeHtml(name)}"`
    + (field.required ? " required" : "")
    + (field.placeholder ? ` placeholder="${escapeHtml(field.placeholder)}"` : "")
    + (describedBy ? ` aria-describedby="${describedBy}"` : "")
    + (error ? ' aria-invalid="true"' : "");

  let control;
  if (field.type === "select") {
    const options = (field.options || []).map((opt) => {
      const value = opt && typeof opt === "object" ? opt.value : opt;
      const text = opt && typeof opt === "object" ? (opt.label ?? opt.value) : opt;
      const sel = String(value) === String(field.value ?? "") ? " selected" : "";
      return `<option value="${escapeHtml(value)}"${sel}>`
        + `${escapeHtml(text)}</option>`;
    }).join("");
    control = `<select class="dlg-input" ${common}>${options}</select>`;
  } else if (field.type === "textarea" || field.type === "json") {
    const rows = Number(field.rows) > 0 ? Math.min(40, Math.round(Number(field.rows))) : 4;
    control = `<textarea class="dlg-input dlg-area" rows="${rows}" `
      + `${common}>${escapeHtml(field.value)}</textarea>`;
  } else if (field.type === "checkbox") {
    control = `<input class="dlg-check" type="checkbox" ${common}`
      + (field.value ? " checked" : "") + ">";
  } else {
    const type = field.type === "number" ? "number" : "text";
    const numeric = field.type === "number"
      ? (field.min == null ? "" : ` min="${escapeHtml(field.min)}"`)
        + (field.max == null ? "" : ` max="${escapeHtml(field.max)}"`)
        + (field.step == null ? "" : ` step="${escapeHtml(field.step)}"`)
      : "";
    control = `<input class="dlg-input" type="${type}" ${common}${numeric}`
      + (field.value == null ? "" : ` value="${escapeHtml(field.value)}"`)
      + ">";
  }

  const cls = field.type === "checkbox" ? "dlg-field dlg-field-check" : "dlg-field";
  return `<div class="${cls}" data-field="${escapeHtml(name)}">`
    + `<label for="${entry.input}" id="${entry.label}">`
    + `${escapeHtml(field.label || name)}</label>`
    + control
    + (entry.help
      ? `<div class="dlg-help" id="${entry.help}">${escapeHtml(field.help)}</div>`
      : "")
    + `<div class="dlg-error" id="${entry.error}" role="alert">`
    + `${escapeHtml(error || "")}</div>`
    + "</div>";
}

function cssSafe(name) {
  return String(name).replace(/[^A-Za-z0-9_-]/g, "_");
}

/** Per-field validation. Returns `{errors: {name: message}, valid}`.
 *
 *  Order inside a field is required → type → range/pattern → custom, and the
 *  first failure wins: a caller's `validate(v, all)` should never have to
 *  re-check that the value is a number.
 */
export function validate(fields, values) {
  const errors = {};
  const all = values || {};
  for (const field of fields || []) {
    const raw = all[field.name];
    const message = validateOne(field, raw, all);
    if (message) errors[field.name] = message;
  }
  return { errors, valid: Object.keys(errors).length === 0 };
}

function validateOne(field, raw, all) {
  if (field.type === "checkbox") {
    if (field.required && !raw) return "Required";
    return field.validate ? field.validate(!!raw, all) || null : null;
  }
  const value = raw == null ? "" : String(raw);
  if (field.required && value.trim() === "") return "Required";
  if (value === "") return field.validate ? field.validate(value, all) || null : null;

  if (field.type === "number") {
    const num = Number(value);
    if (!Number.isFinite(num)) return "Must be a number";
    if (field.min != null && num < field.min) return `Must be at least ${field.min}`;
    if (field.max != null && num > field.max) return `Must be at most ${field.max}`;
    if (field.step) {
      const base = field.min != null ? field.min : 0;
      const steps = (num - base) / field.step;
      if (Math.abs(steps - Math.round(steps)) > 1e-9) {
        return `Must be a multiple of ${field.step}`;
      }
    }
  } else if (field.type === "json") {
    try {
      JSON.parse(value);
    } catch (err) {
      return `Invalid JSON: ${err.message}`;
    }
  }

  if (field.pattern) {
    // A STRING pattern is anchored, because this field is named after HTML's
    // `pattern` attribute and that one is implicitly anchored: unanchored,
    // `[a-z][a-z0-9_]{0,39}` (the shape spec §1.4 hands slice 2) happily
    // accepts "Bad Id!". A caller who passes a RegExp object chose its own
    // anchoring and is left alone.
    const re = field.pattern instanceof RegExp
      ? field.pattern
      : new RegExp(`^(?:${field.pattern})$`);
    if (!re.test(value)) return field.patternMessage || "Invalid format";
  }
  return field.validate ? field.validate(value, all) || null : null;
}

/** The next index in a focus trap: forwards or backwards, wrapping.
 *
 *  `current` is the index of the focused element (-1 when focus is not inside
 *  the trap yet, which is why forwards from -1 is 0 and backwards from -1 is
 *  the last element). An empty trap answers -1 and the caller focuses nothing.
 */
export function focusables(list, current, backwards) {
  const n = Array.isArray(list) ? list.length : Number(list) || 0;
  if (n <= 0) return -1;
  if (current == null || current < 0) return backwards ? n - 1 : 0;
  const step = backwards ? -1 : 1;
  return ((current + step) % n + n) % n;
}

// Test seam.
export const __dialogs__ = { markup, validate, focusables, escapeHtml };
