// PRD-007 slice 5: the owner-side "Share…" dialog and Links panel.
//
// Hosted-mode only (a share link needs a public origin): `setupShare` unhides
// the toolbar button once boot knows the app is hosted, and the dialog drives
// `api.shareCreate/shareList/shareRevoke`. The created URL is shown **once**
// (the server returns the secret exactly once) with a copy button; the panel
// below lists the owner's live links with coarse counters and a revoke button.
import { api, ApiError } from "./api.js";
import { state } from "./state.js";
import * as dialogs from "./shell/dialogs.js";

const EXPORT_FORMATS = ["step", "stl", "3mf"];

let modal = null;
let body = null;
let legacy = null;   // the overlay's seat on the shell's dialog stack

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function close() {
  if (modal) modal.classList.add("hidden");
  if (legacy) legacy.notifyClose();   // idempotent: Esc pops the stack itself
}

function open() {
  if (!state.selectedPart) return;
  render();
  modal.classList.remove("hidden");
  if (legacy) legacy.notifyOpen();
}

function isOpen() {
  return !!modal && !modal.classList.contains("hidden");
}

function field(labelText, control) {
  const wrap = el("label", "share-field");
  wrap.appendChild(el("span", "share-field-label", labelText));
  wrap.appendChild(control);
  return wrap;
}

function render() {
  body.innerHTML = "";
  const project = state.projectName;
  const partId = state.selectedPart;

  body.appendChild(
    el("p", "share-hint", `Publishing “${partId}” from ${project}. Anyone with `
      + `the link can view it; a customizer link also exposes the part's `
      + `parameters as bounded sliders.`)
  );

  // ---- ref / version ----
  const refSel = el("select", "share-input");
  const head = el("option", null, "Current state (tag a new version)");
  head.value = "";
  refSel.appendChild(head);
  for (const v of state.versions || []) {
    const name = typeof v === "string" ? v : v.name;
    if (!name) continue;
    const opt = el("option", null, name);
    opt.value = name;
    refSel.appendChild(opt);
  }

  // ---- customizer ----
  const customizer = el("input");
  customizer.type = "checkbox";
  customizer.checked = true;

  // ---- exports ----
  const exportsWrap = el("div", "share-exports");
  const exportBoxes = {};
  for (const fmt of EXPORT_FORMATS) {
    const box = el("input");
    box.type = "checkbox";
    box.checked = fmt === "step";
    exportBoxes[fmt] = box;
    const lbl = el("label", "share-export");
    lbl.appendChild(box);
    lbl.appendChild(el("span", null, fmt.toUpperCase()));
    exportsWrap.appendChild(lbl);
  }

  // ---- show script ----
  const showScript = el("input");
  showScript.type = "checkbox";

  // ---- expiry ----
  const expires = el("input", "share-input");
  expires.type = "number";
  expires.min = "1";
  expires.placeholder = "never";

  const form = el("div", "share-form");
  form.appendChild(field("Version", refSel));
  form.appendChild(field("Customizer (sliders that rebuild)", customizer));
  form.appendChild(field("Downloads", exportsWrap));
  form.appendChild(field("Show the pinned script", showScript));
  form.appendChild(field("Expires after (days)", expires));

  const createBtn = el("button", "tb-btn primary", "Create link");
  const result = el("div", "share-result");

  createBtn.addEventListener("click", async () => {
    createBtn.disabled = true;
    result.textContent = "Publishing…";
    result.className = "share-result";
    const payload = {
      project,
      part_id: partId,
      customizer: customizer.checked,
      exports: EXPORT_FORMATS.filter((f) => exportBoxes[f].checked),
      show_script: showScript.checked,
    };
    if (refSel.value) payload.ref = refSel.value;
    const days = parseInt(expires.value, 10);
    if (Number.isFinite(days) && days > 0) payload.expires_days = days;
    try {
      const { url } = await api.shareCreate(payload);
      showCreated(result, url);
      await renderLinks(project);
    } catch (e) {
      result.className = "share-result error";
      result.textContent =
        e instanceof ApiError && e.error
          ? e.error.message
          : "Could not create the link.";
    } finally {
      createBtn.disabled = false;
    }
  });

  form.appendChild(createBtn);
  form.appendChild(result);
  body.appendChild(form);

  const links = el("div", "share-links");
  links.id = "share-links-list";
  body.appendChild(links);
  renderLinks(project);
}

function showCreated(result, url) {
  result.className = "share-result ok";
  result.innerHTML = "";
  result.appendChild(el("div", "share-created-label",
    "Link created (copy it now — it is shown only once):"));
  const row = el("div", "share-created-row");
  const input = el("input", "share-input");
  input.readOnly = true;
  input.value = url;
  const copy = el("button", "tb-btn", "Copy");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(url);
      copy.textContent = "Copied";
    } catch {
      input.select();
    }
  });
  row.appendChild(input);
  row.appendChild(copy);
  result.appendChild(row);
}

async function renderLinks(project) {
  const container = document.getElementById("share-links-list");
  if (!container) return;
  container.innerHTML = "";
  let links;
  try {
    links = (await api.shareList(project)).links || [];
  } catch {
    return;
  }
  const live = links.filter((l) => !l.revoked);
  if (!live.length) {
    container.appendChild(el("p", "share-hint", "No active links yet."));
    return;
  }
  container.appendChild(el("h4", "share-links-head", "Active links"));
  for (const link of live) {
    const row = el("div", "share-link-row");
    const c = link.counters || {};
    const meta = el("div", "share-link-meta");
    meta.appendChild(el("span", "share-link-part",
      `${link.part_id} · ${link.ref ? link.ref.name : ""}`));
    meta.appendChild(el("span", "share-link-counts",
      `${c.views || 0} views · ${c.rebuilds || 0} rebuilds · ${c.downloads || 0} downloads`));
    row.appendChild(meta);
    const revoke = el("button", "tb-btn danger", "Revoke");
    revoke.addEventListener("click", async () => {
      revoke.disabled = true;
      try {
        await api.shareRevoke(link.pub_id);
        await renderLinks(project);
      } catch {
        revoke.disabled = false;
      }
    });
    row.appendChild(revoke);
    container.appendChild(row);
  }
}

/** Wire the toolbar button; reveal it only in hosted mode. */
export function setupShare(identity) {
  modal = document.getElementById("share-modal");
  body = document.getElementById("share-body");
  const btn = document.getElementById("share-btn");
  if (!modal || !btn) return;

  const hosted = !!identity && identity.mode !== "local";
  if (!hosted) return; // local mode: /api/share 404s, so never offer it
  btn.classList.remove("hidden");

  btn.addEventListener("click", open);
  const closeBtn = document.getElementById("share-modal-close");
  if (closeBtn) closeBtn.addEventListener("click", close);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) close();
  });
  // PRD-026 FR2: this overlay never had an Escape of its own — adoption is
  // what gives it one, plus the focus trap and the focus restore. Registered
  // here and not at module scope because `setupShare` returns above in local
  // mode: /api/share 404s there, so the view genuinely does not exist.
  legacy = dialogs.attachLegacy(modal, {
    view: "share", title: "Share a part…", isOpen, onClose: close,
    description: "Create and manage public share links for the selected part",
    open: () => open(),
    when: (c) => !!c.selectedPart,
  });
}
