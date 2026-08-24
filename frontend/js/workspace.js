// PRD-005 Slice 8 — the org/workspace switcher: a toolbar chip showing the
// active "org/ws", a dialog listing every org/workspace this principal
// belongs to, and (for free, via `dialogs.register`) a command-palette row.
//
// There is no route that writes a session's *active* workspace — S4's report
// left that setter for this slice, and no Python edit is in scope here — so
// a selection applies the only lever `security.resolve_tenant` actually
// offers a browser: `api.setWorkspace` persists it and sends
// `X-Agentcad-Workspace: org/ws` on every request from here on. This module
// then reloads the page rather than trying to hot-swap live state — the same
// "re-boot rather than re-run boot()" rule `auth.js`'s `onSignedIn` and
// `main.js`'s `showSignIn` already use (every panel's `init()` runs once;
// switching tenants changes which projects even exist).
//
// Zero behaviour in local mode and on an untenanted hosted instance (005a
// with no orgs): `init()` sees an empty `identity.orgs` and returns without
// touching the DOM or registering anything.

import { api, setWorkspace, getWorkspace } from "./api.js";
import * as dialogs from "./shell/dialogs.js";

let chipEl = null;
let identity = null;      // the whoami-extended session payload from boot()
let memberships = null;   // [{org, workspaces: [{id,label}], error?}], cached

/** `identity` is `auth.session()`'s payload — `{orgs, org, workspace, ...}`
 *  when this is a hosted session in at least one org. */
export function init(id) {
  identity = id || null;
  chipEl = document.getElementById("workspace-chip");
  if (!chipEl) return;
  const orgs = (identity && identity.orgs) || [];
  if (!orgs.length) return;              // local mode / untenanted: no-op
  chipEl.classList.remove("hidden");
  refreshChip();
  chipEl.addEventListener("click", () => open());
  dialogs.register("workspace-switch", () => open(), {
    title: "Switch org / workspace",
    description: "Every org and workspace you belong to",
    // The Model menu's "Switch workspace…" row (`main.js`) runs the same
    // opener directly — same m4 rule cloud.js's two panels follow.
    actionId: "workspace.switch",
  });
}

function currentKey() {
  return getWorkspace()
    || (identity && identity.org && identity.workspace
        ? `${identity.org}/${identity.workspace}` : null);
}

function refreshChip() {
  const key = currentKey() || "Select workspace";
  chipEl.textContent = key;
  chipEl.title = `${key} — click to switch org/workspace`;
}

/** Fetched once per page load and cached: `list_members(org)` is the
 *  tenancy-safe read of an org's workspace list (view floor, which every
 *  member of an org this principal belongs to already clears by
 *  construction — `add_member` never stores a role below "view"). */
async function memberList() {
  if (memberships) return memberships;
  const orgs = (identity && identity.orgs) || [];
  memberships = await Promise.all(orgs.map(async (org) => {
    try {
      const res = await api.callTool("list_members", { org });
      if (res && res.error) return { org, workspaces: [], error: res.error.message };
      return { org, workspaces: res.workspaces || [] };
    } catch (err) {
      return { org, workspaces: [], error: err && err.message ? err.message : String(err) };
    }
  }));
  return memberships;
}

export function open() {
  const wrap = document.createElement("div");
  wrap.className = "ws-switch";
  const loading = document.createElement("div");
  loading.className = "ver-empty";
  loading.textContent = "Loading orgs and workspaces…";
  wrap.appendChild(loading);

  const promise = dialogs.open({
    view: "workspace-switch",
    title: "Switch org / workspace",
    body: wrap,
    width: "narrow",
    buttons: [{ id: "close", label: "Close" }],
  });

  memberList().then((rows) => renderRows(wrap, rows)).catch((err) => {
    wrap.textContent = "";
    const el = document.createElement("div");
    el.className = "ver-empty";
    el.textContent = `Could not load memberships: ${err && err.message ? err.message : err}`;
    wrap.appendChild(el);
  });

  return promise;
}

function renderRows(wrap, rows) {
  wrap.textContent = "";
  const current = currentKey();
  for (const { org, workspaces, error } of rows) {
    const head = document.createElement("div");
    head.className = "menu-label";
    head.textContent = org;
    wrap.appendChild(head);
    if (error) {
      const el = document.createElement("div");
      el.className = "ver-empty";
      el.textContent = error;
      wrap.appendChild(el);
      continue;
    }
    if (!workspaces.length) {
      const el = document.createElement("div");
      el.className = "ver-empty";
      el.textContent = "No workspaces";
      wrap.appendChild(el);
      continue;
    }
    for (const ws of workspaces) {
      const key = `${org}/${ws.id}`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "menu-item";
      if (key === current) btn.classList.add("active");
      const label = document.createElement("span");
      label.textContent = ws.label && ws.label !== ws.id ? `${ws.id} (${ws.label})` : ws.id;
      btn.appendChild(label);
      if (key === current) {
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = "current";
        btn.appendChild(meta);
      }
      btn.addEventListener("click", () => select(key));
      wrap.appendChild(btn);
    }
  }
}

function select(orgWs) {
  if (orgWs === currentKey()) {
    dialogs.closeModals();
    return;
  }
  setWorkspace(orgWs);
  location.reload();
}
