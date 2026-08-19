// Versions dialog: the project's immutable named versions (annotated git
// tags). Lists them newest-first, tags the current branch state, and restores
// a version through the ordinary project_restore path (which now accepts a
// ref name). Wired like drawings.js: close button, backdrop click, Escape.

import { api, ApiError } from "./api.js";
import { state, setState } from "./state.js";
import * as dialogs from "./shell/dialogs.js";
import { TAG_RE, bare } from "./patterns.js";

let actions = null;
let legacy = null;   // the overlay's seat on the shell's dialog stack
let overlay, titleEl, bodyEl, tagBtn, closeBtn;

export function init(a) {
  actions = a;
  overlay = document.getElementById("versions-modal");
  titleEl = document.getElementById("versions-title");
  bodyEl = document.getElementById("versions-body");
  tagBtn = document.getElementById("versions-tag");
  closeBtn = document.getElementById("versions-close");

  closeBtn.addEventListener("click", close);
  tagBtn.addEventListener("click", () => tagPrompt());
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  // PRD-026 FR2: the overlay stack owns Esc. This module's own `keydown`
  // listener is gone — with two overlays up, two listeners meant one Esc
  // closing both.
  legacy = dialogs.attachLegacy(overlay, {
    view: "versions", title: "Versions…", isOpen, onClose: close,
    description: "The project's named versions (annotated tags)",
    open: () => open(),
    when: (c) => !!c.branch,
    actionId: "model.versions",   // one palette row, not two (m4)
  });
  dialogs.register("new-version", (args) => tagPrompt(args), {
    title: "New version…",
    description: "Tag the current branch state with a version name",
    when: (c) => !!c.branch,
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
  titleEl.textContent = `${state.projectName} · versions`;
  bodyEl.textContent = "";
  const status = document.createElement("div");
  status.className = "ver-empty";
  status.textContent = "Loading versions…";
  bodyEl.appendChild(status);
  await refresh();
}

function close() {
  overlay.classList.add("hidden");
  if (legacy) legacy.notifyClose();   // idempotent: Esc pops the stack itself
  bodyEl.textContent = "";
}

async function refresh() {
  const proj = state.projectName;
  let payload;
  try {
    payload = await api.listVersions(proj);
  } catch (err) {
    render(null, errorText(err));
    return;
  }
  if (proj !== state.projectName) return;
  setState({ versions: payload.versions || [] });
  render(state.versions, null);
}

function render(versions, message) {
  bodyEl.textContent = "";
  if (message) {
    const el = document.createElement("div");
    el.className = "ver-empty";
    el.textContent = message;
    bodyEl.appendChild(el);
    return;
  }
  if (!versions || !versions.length) {
    const el = document.createElement("div");
    el.className = "ver-empty";
    el.textContent =
      "No versions yet. “Tag current state…” names this branch's current " +
      "state so it can be restored forever.";
    bodyEl.appendChild(el);
    return;
  }
  for (const version of versions) {
    const row = document.createElement("div");
    row.className = "ver-row";

    const main = document.createElement("div");
    main.className = "ver-main";
    const name = document.createElement("div");
    name.className = "ver-name";
    name.textContent = version.name;
    main.appendChild(name);
    if (version.message && version.message !== version.name) {
      const msg = document.createElement("div");
      msg.className = "ver-msg";
      msg.textContent = version.message;
      main.appendChild(msg);
    }
    const meta = document.createElement("div");
    meta.className = "ver-meta";
    meta.textContent = [
      version.author,
      relTime(version.ts),
      (version.commit || "").slice(0, 8),
    ]
      .filter(Boolean)
      .join(" · ");
    main.appendChild(meta);

    const restore = document.createElement("button");
    restore.className = "tb-btn";
    restore.type = "button";
    restore.textContent = "Restore";
    restore.title = `Restore this branch's working state to ${version.name}`;
    restore.addEventListener("click", () => restoreVersion(version, restore));

    row.append(main, restore);
    bodyEl.appendChild(row);
  }
}

async function tagPrompt(args) {
  if (!state.projectName) {
    actions.toast("Open a project first", "error");
    return;
  }
  // One form where there were two prompts: the message is about the name, and
  // being asked for it in a second box you cannot see the name from was how
  // "What is “v1.2”?" got answered "v1.2".
  const values = await dialogs.form({
    view: "new-version",
    title: "New version",
    width: "narrow",
    fields: [
      { name: "name", label: "Version name", required: true,
        value: (args && args.name) || "",
        placeholder: "v1.2",
        pattern: bare(TAG_RE),
        patternMessage: "lowercase letters, digits, . _ - /; max 64",
        help: "e.g. v1.2 or shop-rev-a" },
      { name: "message", label: "What is it?", type: "textarea", rows: 3,
        value: (args && args.message) || "",
        placeholder: "Optional description" },
    ],
    buttons: [{ id: "cancel", label: "Cancel" },
              { id: "tag", label: "Create version", kind: "primary",
                submits: true }],
  });
  if (!values) return;
  const name = String(values.name).trim();
  const message = values.message ? String(values.message) : undefined;
  let res;
  try {
    res = await api.createVersion(state.projectName, name, message);
  } catch (err) {
    actions.toast(`Tag failed: ${errorText(err)}`, "error");
    return;
  }
  if (res && res.error) {
    actions.toast(`Tag failed: ${res.error.message || "error"}`, "error");
    return;
  }
  actions.toast(`Tagged ${name}`);
  await refresh();
}

async function restoreVersion(version, btn) {
  const ok = await dialogs.confirm({
    view: "restore-version",
    title: `Restore this branch's files to “${version.name}”?`,
    body: "Every script, parameter and the assembly go back to the state that "
      + "version recorded. Your current working state is not kept.",
    note: version.message || undefined,
    danger: true,
    confirmLabel: "Restore",
  });
  if (!ok) return;
  btn.disabled = true;
  btn.textContent = "Restoring…";
  let res;
  try {
    res = await api.projectRestore(state.projectName, version.name);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Restore";
    actions.toast(`Restore failed: ${errorText(err)}`, "error");
    return;
  }
  btn.disabled = false;
  btn.textContent = "Restore";
  // The tool passthrough reports expected failures as {error} at HTTP 200.
  if (res && res.error) {
    actions.toast(`Restore failed: ${res.error.message || "error"}`, "error");
    return;
  }
  actions.toast(`Restored ${version.name}`);
  close();
  await actions.refreshProject();
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}

/** Compact "3m ago" style stamp for an ISO date; falls back to a plain date
 *  beyond a month. Shared with the toolbar branch menu. */
export function relTime(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const secs = Math.round((Date.now() - t) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(t).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
