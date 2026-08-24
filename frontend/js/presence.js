// Live presence: the heartbeat and the avatar strip it feeds.
//
// Three things worth knowing before editing this file:
//
//  * Presence rides on HTTP, not on the WebSocket. `/ws` carries no client
//    identity (the server binds X-Agent-Id in HTTP middleware), so a heartbeat
//    is the only channel that can say *who* is here. See the server's
//    core/presence.py docstring.
//  * The heartbeat RESPONSE carries the whole roster, so this module is
//    correct even if it never receives a single `presence_changed` event —
//    that event only makes the strip update sooner than the next beat.
//  * An over-rate beat comes back 200 with {throttled: true}. Nothing here
//    ever shows a heartbeat failure to the user: presence is a nicety, and a
//    red toast every 15 s would be worse than not knowing who is around.

import { api, clientId, wsQuery } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import { INSTANCE_PALETTE } from "./tree.js";
import { displayPrincipal } from "./auth.js";

const BEAT_MS = 15000; // the server's PRESENCE_HEARTBEAT_S
const MIN_GAP_MS = 1200; // stay inside the server's 1/s token bucket
const LABEL_KEY = "agentcad.label";

let timer = null;
let bumpTimer = null;
let lastBeat = 0;
let surface = "viewport";
let claiming = false;
let strip = null;

function label() {
  try {
    return localStorage.getItem(LABEL_KEY) || "Browser";
  } catch {
    return "Browser";
  }
}

// ------------------------------------------------------------- the heartbeat

function focusPayload() {
  return {
    part_id: state.mode === "assembly" ? null : state.selectedPart || null,
    surface,
    label: label(),
    claim: claiming,
  };
}

async function beat() {
  if (!state.projectName) return;
  lastBeat = Date.now();
  const project = state.projectName;
  let payload;
  try {
    payload = await api.heartbeat(project, focusPayload());
  } catch {
    return; // offline, or the project vanished: the next beat retries
  }
  if (project !== state.projectName) return; // raced a project switch
  setState({ presence: payload });
}

/** Beat now (coalesced), for a focus/part/project/branch change. */
function bump() {
  if (bumpTimer) return;
  const wait = Math.max(0, MIN_GAP_MS - (Date.now() - lastBeat));
  bumpTimer = setTimeout(() => {
    bumpTimer = null;
    beat();
  }, wait);
}

function leave() {
  if (!state.projectName) return;
  // `?workspace=org/ws` because a `sendBeacon` cannot set the header the rest of
  // the app rides (`api.js::wsQuery`, the `security.resolve_tenant` fallback):
  // without it the leave would resolve the wrong workspace for a user in two
  // orgs and drop a presence row that is not ours. Empty in local mode, so the
  // URL is byte-identical there.
  const url = `/api/projects/${encodeURIComponent(state.projectName)}/presence`
    + wsQuery();
  const body = JSON.stringify({ leave: true, client_id: clientId });
  // sendBeacon so the request survives the page going away. It cannot carry
  // headers, so the identity rides in the body — a beacon without one would
  // ask the server to drop the client called "browser" instead of us. The
  // server documents that exception; identity is self-asserted either way.
  if (navigator.sendBeacon) {
    navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
    return;
  }
  fetch(url, {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "application/json", "X-Agent-Id": clientId },
    body,
  }).catch(() => {});
}

/** Called by the editor/param UI (slice 9): a dirty buffer or a dragged
 *  control claims the part; viewing never does. */
export function setClaiming(on) {
  const next = !!on;
  if (next === claiming) return;
  claiming = next;
  bump();
}

// ------------------------------------------------------------- the avatars

function initials(text) {
  const clean = (text || "?").trim();
  return clean.slice(0, 1).toUpperCase() || "?";
}

function color(id) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  return INSTANCE_PALETTE[Math.abs(hash) % INSTANCE_PALETTE.length];
}

function where(client) {
  const focus = client.focus || {};
  const surfaces = {
    editor: "in the editor",
    inspector: "in the inspector",
    proposals: "reviewing proposals",
    viewport: "in the viewport",
  };
  const at = surfaces[focus.surface] || "here";
  return focus.part_id ? `${at} on ${focus.part_id}` : at;
}

function render() {
  if (!strip) {
    // The lazy-create pattern renderLockIndicator uses: no index.html or CSS
    // churn for a strip that only exists once somebody else is here.
    const connDot = document.getElementById("conn-dot");
    if (!connDot || !connDot.parentNode) return;
    strip = document.createElement("span");
    strip.id = "presence-strip";
    strip.style.cssText =
      "display:none;align-items:center;gap:2px;margin-right:8px;";
    connDot.parentNode.insertBefore(strip, connDot);
  }
  const clients = (state.presence && state.presence.clients) || [];
  strip.textContent = "";
  for (const client of clients) {
    const dot = document.createElement("span");
    const mine = client.id === clientId;
    dot.textContent = initials(client.label);
    // The label, never the raw nonce: `browser:7f3a1b2c` is plumbing, and a
    // tooltip is where a person looks for a person.
    dot.title = `${client.label}${mine ? " (you)" : ""} — ${where(client)}`;
    dot.style.cssText =
      "display:inline-flex;align-items:center;justify-content:center;" +
      "width:20px;height:20px;border-radius:50%;font-size:11px;" +
      "font-weight:600;color:#fff;user-select:none;" +
      `background:${color(client.id)};` +
      (mine ? "outline:2px solid var(--accent, #6ea8fe);outline-offset:1px;" : "") +
      (client.kind === "agent" ? "border-radius:5px;" : "");
    strip.appendChild(dot);
  }
  // One client is just you: a strip of one avatar is noise, not information.
  strip.style.display = clients.length > 1 ? "inline-flex" : "none";
}

// ------------------------------------------------------------------- wiring

export function init() {
  document.addEventListener(
    "pointerdown",
    (ev) => {
      const target = ev.target;
      if (!target || !target.closest) return;
      let next = null;
      const modal = document.getElementById("proposals-modal");
      if (modal && !modal.classList.contains("hidden") && target.closest("#proposals-modal")) {
        next = "proposals";
      } else if (target.closest("#pane-code")) next = "editor";
      else if (target.closest("#inspector")) next = "inspector";
      else if (target.closest("#viewport")) next = "viewport";
      if (next && next !== surface) {
        surface = next;
        bump();
      }
    },
    true
  );

  window.addEventListener("pagehide", leave);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) bump();
  });

  // A project, part, branch or mode change is a focus change worth announcing
  // before the next scheduled beat.
  onKeys(["projectName", "selectedPart", "branch", "mode"], bump);
  onKeys(["presence"], render);

  timer = setInterval(beat, BEAT_MS);
  bump();
}

/** The roster as the last beat (or event) saw it. */
export function roster() {
  return (state.presence && state.presence.clients) || [];
}

/** A `presence_changed` event: the same roster, sooner. */
export function handleEvent(ev) {
  setState({
    presence: {
      ...(state.presence || { you: clientId }),
      clients: ev.clients || [],
      claims: ev.claims || {},
    },
  });
}

/** `claim_changed {project, part, holder, holder_kind, expires_at,
 *  overridden_by?}` — one part changing hands, or being let go (`holder`
 *  null). Merged into the roster's claims rather than triggering a fetch: the
 *  next heartbeat's response is the authority and will correct any drift. */
export function handleClaim(ev) {
  const base = state.presence || { you: clientId, clients: [] };
  const claims = { ...(base.claims || {}) };
  if (ev.holder) {
    claims[ev.part] = {
      part: ev.part,
      holder: ev.holder,
      holder_kind: ev.holder_kind,
      expires_at: ev.expires_at,
    };
  } else {
    delete claims[ev.part];
  }
  setState({ presence: { ...base, claims } });
}

/** The claim on `partId` held by SOMEBODY ELSE, or null. Our own claim is not
 *  a conflict and is never shown as one. */
export function otherClaim(partId) {
  const claims = (state.presence && state.presence.claims) || {};
  const claim = claims[partId];
  return claim && claim.holder !== clientId ? claim : null;
}

/** The display label a client is heartbeating under, falling back to its
 *  identity. Labels are presence data — never persisted into a thread, an
 *  audit line or a lock — so this is the only place they come from. The
 *  fallback runs `id` through `auth.js`'s `displayPrincipal` convention
 *  (PRD-005 slice 8): in hosted mode `id` is a composed principal
 *  (`user:nikita/browser:…`, `agent:mcp:claude`) once nobody has picked a
 *  presence nickname, and `nikita`/`claude (agent)` is what a lock chip or
 *  the avatar strip should say instead of the raw string. */
export function labelFor(id) {
  const found = roster().find((client) => client.id === id);
  return (found && found.label) || displayPrincipal(id) || "someone";
}

export function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}
