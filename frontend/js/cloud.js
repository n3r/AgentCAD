// PRD-005 Slice 8 — the org-members and agent-tokens panels. Two
// `dialogs.register`ed views over the tenant tool surface
// (`agentcad/core/tools_cloud.py`): `list_members`, `grant_role`,
// `revoke_role`, `create_agent_token`, `revoke_agent_token`.
//
// **Deliberately NOT `GET /api/auth/tokens` / `POST /api/auth/tokens`.**
// Those 005a routes gate on `who.role == "admin"` — the INSTANCE
// administrator (the person who runs the box) — while an org admin who is
// not the instance administrator is exactly who this panel is for (FR6's
// per-tenant model). `list_members(org)`'s response carries a `tokens` array
// — the scoped rows for that org, same shape (`id, name, role, created,
// expires, revoked, scope`) — for an org admin and omits it for anyone else
// (`tools_cloud.py`'s own gate, `can(tenants, "admin", ...)`), so that key's
// PRESENCE is this panel's admin affordance: no second round trip, and no
// role string to get wrong.
//
// Both panels stay open across a grant/revoke/mint: they are not
// `dialogs.form`'s one-shot submit, they are a custom body wired with its own
// buttons (the `bulk-results` precedent) and a single "Close".

import { api, ApiError, getWorkspace } from "./api.js";
import * as dialogs from "./shell/dialogs.js";

const ROLES = ["view", "comment", "edit", "admin"];

let identity = null;
let toast = () => {};

export function init(panelApi) {
  toast = (panelApi && panelApi.toast) || (() => {});
  dialogs.register("org-members", () => openMembers(), {
    title: "Org members",
    description: "Members, org-default roles, and per-project overrides",
    // The Model menu's "Org members…" row (`main.js`) runs the same opener
    // directly — this stops the palette offering a second, near-identical
    // "Open: Org members…" row for it (the m4 rule bulk.js/versions.js use).
    actionId: "cloud.members",
  });
  dialogs.register("agent-tokens", () => openTokens(), {
    title: "Agent tokens",
    description: "Scoped bearer tokens minted for this org",
    actionId: "cloud.tokens",
  });
}

/** Called once from `boot()`, alongside `workspace.init` — the whoami-
 *  extended session payload both panels read their org list from. */
export function setIdentity(id) {
  identity = id || null;
}

function orgs() {
  return (identity && identity.orgs) || [];
}

function defaultOrg() {
  const active = getWorkspace();
  if (active) return active.split("/")[0];
  if (identity && identity.org) return identity.org;
  const list = orgs();
  return list.length ? list[0] : null;
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}

async function call(name, args) {
  const res = await api.callTool(name, args);
  if (res && res.error) {
    const err = new Error(res.error.message);
    err.error = res.error;
    throw err;
  }
  return res;
}

// -------------------------------------------------------------- shared chrome

/** An org `<select>` (hidden when there is only one org this principal
 *  belongs to); `onChange(org)` fires immediately with the initial value. */
function buildOrgPicker(onChange) {
  const list = orgs();
  const wrap = document.createElement("div");
  wrap.className = "param"; // reuse the inspector's label+control layout
  let current = defaultOrg() || list[0] || null;
  if (list.length > 1) {
    const head = document.createElement("div");
    head.className = "param-head";
    const label = document.createElement("span");
    label.className = "param-name";
    label.textContent = "Org";
    head.appendChild(label);
    wrap.appendChild(head);
    const select = document.createElement("select");
    select.className = "param-select";
    for (const org of list) {
      const opt = document.createElement("option");
      opt.value = org;
      opt.textContent = org;
      if (org === current) opt.selected = true;
      select.appendChild(opt);
    }
    select.addEventListener("change", () => {
      current = select.value;
      onChange(current);
    });
    wrap.appendChild(select);
  }
  queueMicrotask(() => onChange(current));
  return wrap;
}

function field(labelText, inputEl) {
  const wrap = document.createElement("label");
  wrap.className = "auth-field";
  const span = document.createElement("span");
  span.className = "auth-label";
  span.textContent = labelText;
  wrap.append(span, inputEl);
  return wrap;
}

function roleSelect(defaultRole) {
  const select = document.createElement("select");
  select.className = "param-select";
  for (const role of ROLES) {
    const opt = document.createElement("option");
    opt.value = role;
    opt.textContent = role;
    if (role === (defaultRole || "view")) opt.selected = true;
    select.appendChild(opt);
  }
  return select;
}

// ------------------------------------------------------------- org members

export async function openMembers() {
  const wrap = document.createElement("div");
  wrap.className = "cloud-panel";

  const listHost = document.createElement("div");
  const formHost = document.createElement("div");
  wrap.appendChild(buildOrgPicker((org) => loadMembers(org, listHost, formHost)));
  wrap.appendChild(listHost);
  wrap.appendChild(formHost);

  return dialogs.open({
    view: "org-members",
    title: "Org members",
    body: wrap,
    width: "wide",
    buttons: [{ id: "close", label: "Close" }],
  });
}

async function loadMembers(org, listHost, formHost) {
  listHost.textContent = "Loading…";
  formHost.textContent = "";
  if (!org) {
    listHost.textContent = "No org selected.";
    return;
  }
  let res;
  try {
    res = await call("list_members", { org });
  } catch (err) {
    listHost.textContent = `Could not list members: ${errorText(err)}`;
    return;
  }
  renderMembers(org, res, listHost, formHost);
}

function renderMembers(org, res, listHost, formHost) {
  listHost.textContent = "";
  const isAdmin = Array.isArray(res.tokens);

  const table = document.createElement("table");
  table.className = "bulk-results-table";
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const text of ["Handle", "Org role"]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = text;
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  const members = res.members || [];
  if (!members.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 2;
    td.textContent = "No members.";
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  for (const m of members) {
    const tr = document.createElement("tr");
    const handle = document.createElement("td");
    handle.textContent = m.handle;
    const role = document.createElement("td");
    role.textContent = m.role || "(unrecognized)";
    tr.append(handle, role);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  listHost.appendChild(table);

  const workspaces = res.workspaces || [];
  const wsNote = document.createElement("div");
  wsNote.className = "ver-meta";
  wsNote.textContent = workspaces.length
    ? `Workspaces: ${workspaces.map((w) => w.id).join(", ")}`
    : "No workspaces.";
  listHost.appendChild(wsNote);

  formHost.textContent = "";
  if (!isAdmin) {
    const note = document.createElement("div");
    note.className = "pane-note";
    note.textContent =
      "Granting or revoking a per-project role requires admin in this org.";
    formHost.appendChild(note);
    return;
  }
  formHost.appendChild(buildRoleForm(org, workspaces));
}

function buildRoleForm(org, workspaces) {
  const box = document.createElement("div");
  box.className = "cloud-form";

  const title = document.createElement("div");
  title.className = "menu-label";
  title.textContent = "Grant / revoke a per-project role";
  box.appendChild(title);

  const wsSelect = document.createElement("select");
  wsSelect.className = "param-select";
  for (const w of workspaces) {
    const opt = document.createElement("option");
    opt.value = w.id;
    opt.textContent = w.id;
    wsSelect.appendChild(opt);
  }
  const projectInput = document.createElement("input");
  projectInput.type = "text";
  projectInput.className = "param-text";
  projectInput.placeholder = "project id";

  const principalInput = document.createElement("input");
  principalInput.type = "text";
  principalInput.className = "param-text";
  principalInput.placeholder = "user:handle or agent:name";

  const role = roleSelect("view");

  box.append(
    field("Workspace", wsSelect),
    field("Project", projectInput),
    field("Principal", principalInput),
    field("Role", role)
  );

  const btnRow = document.createElement("div");
  btnRow.className = "cloud-actions";
  const grantBtn = document.createElement("button");
  grantBtn.type = "button";
  grantBtn.className = "tb-btn";
  grantBtn.textContent = "Grant";
  const revokeBtn = document.createElement("button");
  revokeBtn.type = "button";
  revokeBtn.className = "tb-btn";
  revokeBtn.textContent = "Revoke override";
  btnRow.append(grantBtn, revokeBtn);
  box.appendChild(btnRow);

  const busy = () => { grantBtn.disabled = true; revokeBtn.disabled = true; };
  const idle = () => { grantBtn.disabled = false; revokeBtn.disabled = false; };

  grantBtn.addEventListener("click", async () => {
    const project = projectInput.value.trim();
    const principal = principalInput.value.trim();
    if (!project || !principal) {
      toast("Name a project and a principal first", "error");
      return;
    }
    busy();
    try {
      const res = await call("grant_role", {
        project, principal, role: role.value, org, workspace: wsSelect.value,
      });
      toast(`Granted ${res.role} on ${project} to ${res.principal}`);
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      idle();
    }
  });

  revokeBtn.addEventListener("click", async () => {
    const project = projectInput.value.trim();
    const principal = principalInput.value.trim();
    if (!project || !principal) {
      toast("Name a project and a principal first", "error");
      return;
    }
    busy();
    try {
      const res = await call("revoke_role", {
        project, principal, org, workspace: wsSelect.value,
      });
      toast(res.note || `Revoked the override on ${project} for ${res.principal}`);
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      idle();
    }
  });

  return box;
}

// ------------------------------------------------------------ agent tokens

export async function openTokens() {
  const wrap = document.createElement("div");
  wrap.className = "cloud-panel";

  const secretHost = document.createElement("div");
  const listHost = document.createElement("div");
  const formHost = document.createElement("div");
  wrap.appendChild(secretHost);
  wrap.appendChild(buildOrgPicker((org) =>
    loadTokens(org, listHost, formHost, secretHost)));
  wrap.appendChild(listHost);
  wrap.appendChild(formHost);

  return dialogs.open({
    view: "agent-tokens",
    title: "Agent tokens",
    body: wrap,
    width: "wide",
    buttons: [{ id: "close", label: "Close" }],
  });
}

async function loadTokens(org, listHost, formHost, secretHost) {
  listHost.textContent = "Loading…";
  formHost.textContent = "";
  if (!org) {
    listHost.textContent = "No org selected.";
    return;
  }
  let res;
  try {
    res = await call("list_members", { org });
  } catch (err) {
    listHost.textContent = `Could not list tokens: ${errorText(err)}`;
    return;
  }
  renderTokens(org, res, listHost, formHost, secretHost);
}

function renderTokens(org, res, listHost, formHost, secretHost) {
  listHost.textContent = "";
  const tokens = res.tokens;
  if (!Array.isArray(tokens)) {
    const note = document.createElement("div");
    note.className = "pane-note";
    note.textContent = "Viewing and minting tokens requires admin in this org.";
    listHost.appendChild(note);
    formHost.textContent = "";
    return;
  }

  const table = document.createElement("table");
  table.className = "bulk-results-table";
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const text of ["Name", "Role", "Projects", "Expires", "Status", ""]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = text;
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  if (!tokens.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.textContent = "No scoped tokens minted in this org yet.";
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  for (const t of tokens) {
    const tr = document.createElement("tr");
    if (t.revoked) tr.className = "failed";
    const name = document.createElement("td");
    name.textContent = t.name;
    const role = document.createElement("td");
    role.textContent = (t.scope && t.scope.role) || t.role;
    const projects = document.createElement("td");
    projects.textContent = (t.scope && (t.scope.projects || []).join(", ")) || "—";
    const expires = document.createElement("td");
    expires.textContent = t.expires ? new Date(t.expires * 1000).toLocaleDateString() : "never";
    const status = document.createElement("td");
    status.textContent = t.revoked ? "revoked" : "live";
    const actions = document.createElement("td");
    if (!t.revoked) {
      const revokeBtn = document.createElement("button");
      revokeBtn.type = "button";
      revokeBtn.className = "tb-btn";
      revokeBtn.textContent = "Revoke";
      revokeBtn.addEventListener("click", async () => {
        revokeBtn.disabled = true;
        try {
          await call("revoke_agent_token", { token_id: t.id });
          toast(`Revoked token ${t.name} (${t.id})`);
          loadTokens(org, listHost, formHost, secretHost);
        } catch (err) {
          toast(errorText(err), "error");
          revokeBtn.disabled = false;
        }
      });
      actions.appendChild(revokeBtn);
    }
    tr.append(name, role, projects, expires, status, actions);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  listHost.appendChild(table);

  formHost.textContent = "";
  formHost.appendChild(buildMintForm(org, res.workspaces || [], secretHost,
    () => loadTokens(org, listHost, formHost, secretHost)));
}

function buildMintForm(org, workspaces, secretHost, onMinted) {
  const box = document.createElement("div");
  box.className = "cloud-form";

  const title = document.createElement("div");
  title.className = "menu-label";
  title.textContent = "Mint a scoped token";
  box.appendChild(title);

  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.className = "param-text";
  nameInput.placeholder = "e.g. ci";

  const wsSelect = document.createElement("select");
  wsSelect.className = "param-select";
  for (const w of workspaces) {
    const opt = document.createElement("option");
    opt.value = w.id;
    opt.textContent = w.id;
    wsSelect.appendChild(opt);
  }

  const projectsInput = document.createElement("input");
  projectsInput.type = "text";
  projectsInput.className = "param-text";
  projectsInput.placeholder = "comma-separated project ids";

  const role = roleSelect("edit");

  const ttlInput = document.createElement("input");
  ttlInput.type = "number";
  ttlInput.className = "param-num";
  ttlInput.min = "1";
  ttlInput.placeholder = "never";

  box.append(
    field("Name", nameInput),
    field("Workspace", wsSelect),
    field("Projects", projectsInput),
    field("Role", role),
    field("TTL (days, optional)", ttlInput)
  );

  const mintBtn = document.createElement("button");
  mintBtn.type = "button";
  mintBtn.className = "tb-btn auth-submit";
  mintBtn.textContent = "Mint token";
  box.appendChild(mintBtn);

  mintBtn.addEventListener("click", async () => {
    const name = nameInput.value.trim();
    const projects = projectsInput.value.split(",").map((p) => p.trim()).filter(Boolean);
    if (!name || !projects.length) {
      toast("Name the token and at least one project", "error");
      return;
    }
    mintBtn.disabled = true;
    try {
      const res = await call("create_agent_token", {
        name, org, workspace: wsSelect.value, projects, role: role.value,
        ttl_days: ttlInput.value ? Number(ttlInput.value) : undefined,
      });
      showSecret(secretHost, res);
      onMinted();
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      mintBtn.disabled = false;
    }
  });

  return box;
}

/** The secret is in `res.token` and NOWHERE else — `list_members`/
 *  `list_tokens` cannot return it, only a digest is stored. Shown once, with
 *  a copy button, above the token table; dismissing it (or closing/reopening
 *  the panel) drops it from memory for good. */
function showSecret(host, res) {
  host.textContent = "";
  const box = document.createElement("div");
  box.className = "cloud-secret";
  const note = document.createElement("div");
  note.className = "ver-meta";
  note.textContent =
    `Token "${res.name}" minted — this is the only time the secret is shown. `
    + "Copy it now; it cannot be retrieved later.";
  const row = document.createElement("div");
  row.className = "auth-field";
  const secretInput = document.createElement("input");
  secretInput.type = "text";
  secretInput.className = "param-text";
  secretInput.readOnly = true;
  secretInput.value = res.token;
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "tb-btn";
  copyBtn.textContent = "Copy";
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(res.token);
      toast("Copied to clipboard");
    } catch {
      secretInput.select();
      toast("Select-and-copy: clipboard access was refused", "error");
    }
  });
  const dismissBtn = document.createElement("button");
  dismissBtn.type = "button";
  dismissBtn.className = "tb-btn";
  dismissBtn.textContent = "Dismiss";
  dismissBtn.addEventListener("click", () => { host.textContent = ""; });
  row.append(secretInput, copyBtn, dismissBtn);
  box.append(note, row);
  host.appendChild(box);
}
