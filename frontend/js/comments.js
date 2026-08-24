// Review threads (PRD-008): the Threads pane, the face pins, the editor
// gutter, the param badges, the composer and the notifications drawer.
//
// Four things about this file are load-bearing, and every one of them is a
// rule the server states rather than a preference:
//
//  * **An anchor is immutable; its STATUS is computed on every read.** The
//    server answers each thread with `resolution: {status, reason, hint,
//    confidence, …}` where status is one of `ok` / `moved` / `orphaned` /
//    `unverified`. Those are four different facts. `unverified` means *we did
//    not look* (the part has no mesh cached, there is no git, the packet is
//    frozen, the anchor belongs to another branch) and it must never be drawn
//    as "fine".
//  * **Click-to-focus uses `resolution.face_index` / `resolution.start` — never
//    the stored ordinal or line.** A face anchor re-matches to a different
//    ordinal often enough that the stored one is a mis-pin waiting to happen,
//    which is exactly the failure the whole feature is built to avoid. An
//    `orphaned` or `unverified` anchor is NOT focusable; its row says why.
//  * **`comment_changed` is a pointer, not a payload.** It carries an id, a
//    state, an action and a part — no body — so every event re-reads the list.
//    That is also what keeps a `moved` badge honest after somebody else's edit.
//  * **Everything user-controlled goes through textContent.** Bodies, labels,
//    part ids, reasons and hints are all strings somebody else wrote.
//
// Rendering discipline: the panel, the pins, the gutter and the badges are all
// rendered FROM `state.comments`, never poked imperatively — the panel is
// rebuilt on every change, the pins are rebuilt when the thread set or the
// geometry changes (and only repositioned per frame), the gutter is cleared
// and re-applied whole, and the param badges hook inspector.js's one decorator
// seam so they survive both its full rebuild and its value-only sync.

import { api, ApiError, clientId } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import * as viewport from "./viewport.js";
import * as inspector from "./inspector.js";
import * as editor from "./editor.js";
import * as dialogs from "./shell/dialogs.js";
import { displayPrincipal } from "./auth.js";

let actions = null;
let inboxLegacy = null;     // the inbox overlay's seat on the dialog stack
let pane = null;
let tabBadge = null;
let pinsHost = null;

let filter = "open";        // "open" | "resolved" | "all"
let expanded = null;        // thread id whose body is open in the panel
let refreshTimer = null;
let refreshSeq = 0;
let busy = false;

// The composer popover. `pending` is the anchor a click armed it with.
let popEl, popAnchorEl, popBodyEl, popHintEl, popSendBtn;
let pending = null;         // {anchor, label, onDone}

// Pins: rebuilt when the thread set or the geometry changes, repositioned on
// every frame. `world` is resolved lazily because the triangle->face sidecar
// arrives after the mesh does.
let pins = [];              // [{thread, el, world}]
// thread id -> an unsent reply, kept across re-renders.
const drafts = new Map();

const STATUS_LABEL = {
  ok: "ok",
  moved: "moved",
  orphaned: "orphaned",
  unverified: "unverified",
};

// A one-line gloss per status, shown under the four-state chip. The wording is
// the server's contract, not a paraphrase of it.
const STATUS_GLOSS = {
  ok: "still points at what it pointed at",
  moved: "re-matched to a new address; the stored anchor is unchanged",
  orphaned: "the target is gone, or no candidate cleared the tolerance",
  unverified: "not checked — this is not the same as “fine”",
};

// ---------------------------------------------------------------- lifecycle

export function init(a) {
  actions = a;
  pane = document.getElementById("pane-threads");
  tabBadge = document.getElementById("threads-count");
  pinsHost = document.getElementById("pins");

  popEl = document.getElementById("comment-pop");
  popAnchorEl = document.getElementById("comment-pop-anchor");
  popBodyEl = document.getElementById("comment-pop-body");
  popHintEl = document.getElementById("comment-pop-hint");
  popSendBtn = document.getElementById("comment-pop-send");
  document.getElementById("comment-pop-cancel")
    .addEventListener("click", closeComposer);
  popSendBtn.addEventListener("click", submitComposer);
  popBodyEl.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      closeComposer();
    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submitComposer();
    }
  });
  document.addEventListener("pointerdown", (e) => {
    if (!pending || popEl.classList.contains("hidden")) return;
    if (e.target.closest && e.target.closest("#comment-pop")) return;
    closeComposer();
  }, true);

  inspector.setParamDecorator(decorateParams);
  viewport.onFrame(positionPins);
  editor.onCommentGutterClick((line, ev) => {
    const part = state.part;
    if (!part || part.kind === "reference") return;
    // A selection that contains the clicked line is the range the author
    // meant; otherwise it is that one line.
    const sel = editor.selectionRange();
    const range =
      sel && sel.start <= line && line <= sel.end ? sel : { start: line, end: line };
    openComposer(
      { kind: "script_range", part_id: part.id, start: range.start, end: range.end },
      { at: { x: ev.clientX, y: ev.clientY } }
    );
  });

  document.getElementById("notif-btn").addEventListener("click", openInbox);
  document.getElementById("notif-close").addEventListener("click", closeInbox);
  document.getElementById("notif-read-all").addEventListener("click", async () => {
    try {
      await api.markNotificationsRead(state.projectName);
    } catch {
      /* nothing to mark, or no routes */
    }
    refreshNotifications();
  });
  const inbox = document.getElementById("notifications-modal");
  inbox.addEventListener("click", (e) => {
    if (e.target.id === "notifications-modal") closeInbox();
  });
  // PRD-026 FR2: Esc belongs to the shell's overlay stack, not to this module.
  // (`#comment-pop` is NOT a `.modal-overlay` — it is an anchored popover with
  // its own field-level Escape, and it stays as it is.)
  inboxLegacy = dialogs.attachLegacy(inbox, {
    view: "notifications", title: "Mentions…", onClose: closeInbox,
    description: "Notifications addressed to you on this project",
    isOpen: drawerOpen, open: () => openInbox(),
  });

  onKeys(["projectName", "branch"], () => {
    setState({ comments: null });
    scheduleRefresh();
    refreshNotifications();
  });
  onKeys(["notifications"], renderInbox);
  onKeys(["comments"], () => {
    render();
    syncPins();
    syncGutter();
    // The badges' data changed without the part changing, so inspector's own
    // render — which is what normally calls the decorator — will not run.
    inspector.redecorateParams();
  });
  onKeys(["part", "selectedPart", "mode"], () => {
    syncPins();
    syncGutter();
  });
  render();
}

/** Coalesce bursts (an agent replying to three threads) into one read. */
export function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 150);
}

export async function refresh() {
  const proj = state.projectName;
  if (!proj) {
    setState({ comments: null });
    return;
  }
  const seq = ++refreshSeq;
  let payload;
  try {
    payload = await api.listComments(proj, {});
  } catch {
    // No comment routes (a server built without the pack) or a project that
    // vanished: the pane says so through its empty state rather than a toast.
    if (seq === refreshSeq) setState({ comments: null });
    return;
  }
  if (seq !== refreshSeq || proj !== state.projectName) return;
  setState({ comments: payload });
}

/** `comment_changed {project, thread, state, action, part}` — a POINTER. It
 *  carries no body on purpose, so the honest response is to re-read. */
export function handleEvent() {
  scheduleRefresh();
}

/** The mesh's triangle->face sidecar landed: a pin can stop guessing from the
 *  centroid the anchor recorded and read the resolved face off the geometry
 *  that is actually on screen. */
export function meshChanged() {
  syncPins();
}

// ----------------------------------------------------- notifications drawer
//
// The bus is a broadcast — every /ws client receives every `notification` and
// filters on `to` — so this drawer shows only what the server would have told
// this identity anyway through `GET /notifications`, which answers for the
// identity of the REQUEST and never takes one as an argument.

export async function refreshNotifications() {
  const proj = state.projectName;
  if (!proj) {
    setState({ notifications: null });
    return;
  }
  let payload;
  try {
    payload = await api.listNotifications(proj);
  } catch {
    setState({ notifications: null }); // no routes: the button stays hidden
    return;
  }
  if (proj !== state.projectName) return;
  setState({ notifications: payload });
}

/** A `notification` event already filtered on `to` by main.js. */
export function notified(ev) {
  refreshNotifications();
  actions.toast(`${ev.from} mentioned you in thread ${ev.thread}`);
}

function renderInbox() {
  const payload = state.notifications;
  const btn = document.getElementById("notif-btn");
  const badge = document.getElementById("notif-count");
  if (!btn || !badge) return;
  btn.classList.toggle("hidden", !payload);
  const unread = (payload && payload.unread) || 0;
  badge.textContent = String(unread);
  badge.classList.toggle("hidden", !unread);
  btn.title = unread
    ? `${unread} unread mention${unread === 1 ? "" : "s"}`
    : "Mentions";
  if (drawerOpen()) renderDrawer();
}

function drawerOpen() {
  const overlay = document.getElementById("notifications-modal");
  return overlay && !overlay.classList.contains("hidden");
}

export function openInbox() {
  document.getElementById("notifications-modal").classList.remove("hidden");
  if (inboxLegacy) inboxLegacy.notifyOpen();
  renderDrawer();
  refreshNotifications();
}

function closeInbox() {
  document.getElementById("notifications-modal").classList.add("hidden");
  if (inboxLegacy) inboxLegacy.notifyClose();  // idempotent: Esc pops it too
}

function renderDrawer() {
  const body = document.getElementById("notif-body");
  if (!body) return;
  body.textContent = "";
  const rows = ((state.notifications || {}).notifications || []).slice().reverse();
  if (!rows.length) {
    body.appendChild(
      note(
        "No mentions. Write @chat:main (or another client id) in a comment " +
        "to notify someone. Identity here is self-asserted and this inbox is " +
        "visible to anyone on this machine — per-principal delivery is a " +
        "later feature."
      )
    );
    return;
  }
  for (const row of rows) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `notif-row${row.read ? " read" : ""}`;
    const who = el("span", "notif-from");
    who.textContent = row.from || "?";
    const what = el("span", "notif-what");
    what.textContent = `mentioned you in thread ${row.thread}`;
    const when = el("span", "notif-when");
    when.textContent = row.ts || "";
    item.append(who, what, when);
    item.addEventListener("click", async () => {
      closeInbox();
      showThread(row.thread);
      try {
        await api.markNotificationsRead(state.projectName, [row.seq]);
      } catch {
        /* the badge just stays until the next read */
      }
      refreshNotifications();
    });
    body.appendChild(item);
  }
}

/** Reveal one thread in the panel — the notification drawer's and the diff
 *  chip's click target. Closes whatever modal is covering the inspector. */
export function showThread(tid) {
  // Through the shell's stack, never by hiding the overlay by hand: the
  // adopted modals hold a stack entry, and hiding the element behind their
  // backs would leave `isModalOpen()` true forever.
  dialogs.closeModals();
  expanded = String(tid);
  filter = "all";
  inspector.setTab("threads");
  render();
  const row = pane.querySelector(".th-row .th-body");
  if (row) row.scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------------ reading

function threads() {
  return (state.comments && state.comments.threads) || [];
}

function counts() {
  return (state.comments && state.comments.counts) || {};
}

function resolutionOf(thread) {
  return thread.resolution || { status: "unverified", reason: "not_resolved" };
}

/** Open threads anchored to one part, by kind. */
function threadsFor(kind, partId) {
  return threads().filter(
    (t) =>
      t.state === "open" &&
      (t.anchor || {}).kind === kind &&
      (t.anchor || {}).part === partId
  );
}

/** Open `proposal_hunk` threads on one proposal, grouped by `file\u0000hunk`
 *  at the hunk each one resolves to NOW. Used by proposals.js. */
export function hunkThreads(proposalId) {
  const out = new Map();
  for (const thread of threads()) {
    const anchor = thread.anchor || {};
    if (anchor.kind !== "proposal_hunk") continue;
    if (String(anchor.proposal) !== String(proposalId)) continue;
    const res = resolutionOf(thread);
    if (res.status !== "ok" && res.status !== "moved") continue;
    const hunk = res.hunk != null ? res.hunk : anchor.hunk;
    const key = `${anchor.file}\u0000${hunk}`;
    if (!out.has(key)) out.set(key, []);
    out.get(key).push(thread);
  }
  return out;
}

// ------------------------------------------------------------- the composer

/** Arm the composer with an anchor. `at` is a {x, y} client point to place it
 *  near; omit it to centre the popover. */
export function openComposer(anchor, { label, at, onDone } = {}) {
  if (!state.projectName) {
    actions.toast("Open a project first", "error");
    return;
  }
  pending = { anchor, label: label || anchorLabel(anchor), onDone };
  popAnchorEl.textContent = pending.label;
  popBodyEl.value = "";
  popHintEl.textContent = "⌘↵ to post";
  popSendBtn.disabled = false;
  popEl.classList.remove("hidden");
  place(at);
  popBodyEl.focus();
}

function place(at) {
  const width = popEl.offsetWidth || 300;
  const height = popEl.offsetHeight || 150;
  const margin = 10;
  let x = at ? at.x + 12 : (window.innerWidth - width) / 2;
  let y = at ? at.y + 12 : (window.innerHeight - height) / 2;
  x = Math.max(margin, Math.min(x, window.innerWidth - width - margin));
  y = Math.max(margin, Math.min(y, window.innerHeight - height - margin));
  popEl.style.left = `${Math.round(x)}px`;
  popEl.style.top = `${Math.round(y)}px`;
}

function closeComposer() {
  pending = null;
  popEl.classList.add("hidden");
}

async function submitComposer() {
  if (!pending || busy) return;
  const body = popBodyEl.value.trim();
  if (!body) {
    popHintEl.textContent = "a comment needs some text";
    return;
  }
  const { anchor, onDone } = pending;
  busy = true;
  popSendBtn.disabled = true;
  popHintEl.textContent = "posting…";
  try {
    const res = await api.addComment(state.projectName, { anchor, body });
    closeComposer();
    expanded = (res.thread || {}).id || null;
    if (onDone) onDone(res.thread);
    await refresh();
    actions.toast("Thread opened");
  } catch (err) {
    popSendBtn.disabled = false;
    popHintEl.textContent = errorText(err);
  } finally {
    busy = false;
  }
}

// ---------------------------------------------------------------- the panel

function render() {
  if (!pane) return;
  const open = counts().open || 0;
  if (tabBadge) {
    tabBadge.textContent = String(open);
    tabBadge.classList.toggle("hidden", !open);
  }

  pane.textContent = "";
  if (!state.projectName) {
    pane.appendChild(note("No project open."));
    return;
  }
  if (!state.comments) {
    pane.appendChild(note("Review threads are unavailable on this server."));
    return;
  }

  pane.appendChild(renderBar());

  const rows = threads()
    .filter((t) => filter === "all" || t.state === filter)
    .sort((a, b) => String(b.updated).localeCompare(String(a.updated)));
  if (!rows.length) {
    pane.appendChild(
      note(
        filter === "open"
          ? "No open threads. Click a face and press Comment, or use the " +
            "gutter in the Code tab, to start one."
          : `No ${filter === "all" ? "" : `${filter} `}threads.`
      )
    );
    return;
  }
  const list = el("div", "th-list");
  for (const thread of rows) list.appendChild(renderThread(thread));
  pane.appendChild(list);
}

function renderBar() {
  const bar = el("div", "th-bar");
  const c = counts();
  for (const [key, label, n] of [
    ["open", "Open", c.open || 0],
    ["resolved", "Resolved", c.resolved || 0],
    ["all", "All", (c.open || 0) + (c.resolved || 0)],
  ]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `th-filter${filter === key ? " active" : ""}`;
    btn.textContent = `${label} ${n}`;
    btn.addEventListener("click", () => {
      filter = key;
      render();
    });
    bar.appendChild(btn);
  }
  // `orphaned` is an ANCHOR's status, not a thread state — it is deliberately
  // shown beside the state filters rather than as a fourth one.
  if (c.orphaned) {
    const chip = el("span", "th-orphan-count");
    chip.textContent = `${c.orphaned} orphaned`;
    chip.title =
      "Anchors whose target is gone, or where no candidate cleared the " +
      "matcher's tolerance. The threads are intact and still carry their " +
      "last-known anchor.";
    bar.appendChild(chip);
  }
  const spacer = el("span", "th-bar-spacer");
  bar.appendChild(spacer);
  if (state.selectedPart && state.mode === "part") {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "th-new";
    add.textContent = "+ Thread";
    add.title = `Open a thread on part ${state.selectedPart}`;
    add.addEventListener("click", (e) =>
      openComposer(
        { kind: "part", part_id: state.selectedPart },
        { at: { x: e.clientX, y: e.clientY } }
      )
    );
    bar.appendChild(add);
  }
  return bar;
}

function renderThread(thread) {
  const res = resolutionOf(thread);
  const row = el("div", `th-row st-${res.status}`);
  if (thread.state === "resolved") row.classList.add("th-resolved");
  const isOpen = expanded === thread.id;

  const head = document.createElement("button");
  head.type = "button";
  head.className = "th-head";
  head.setAttribute("aria-expanded", isOpen ? "true" : "false");

  const crumb = el("span", "th-crumb");
  crumb.textContent = anchorLabel(thread.anchor, res);
  head.appendChild(crumb);
  head.appendChild(statusChip(res));
  const meta = el("span", "th-meta");
  const n = (thread.comments || []).length;
  meta.textContent = `${n}`;
  meta.title = `${n} comment${n === 1 ? "" : "s"}`;
  head.appendChild(meta);
  head.addEventListener("click", () => {
    expanded = isOpen ? null : thread.id;
    render();
  });
  row.appendChild(head);

  const first = (thread.comments || [])[0] || {};
  const preview = el("div", "th-preview");
  preview.textContent = first.body || "(deleted)";
  row.appendChild(preview);

  if (isOpen) row.appendChild(renderBody(thread, res));
  return row;
}

function renderBody(thread, res) {
  const box = el("div", "th-body");

  // Why the anchor is where it is, in the reader's words. Present for every
  // status but `ok`, because that is exactly when the server owes a reason.
  if (res.status !== "ok") {
    const why = el("div", `th-why st-${res.status}`);
    const line = [STATUS_GLOSS[res.status] || res.status];
    if (res.reason) line.push(`reason: ${res.reason}`);
    if (res.hint) line.push(res.hint);
    why.textContent = line.join(" · ");
    box.appendChild(why);
  }
  if (res.confidence != null || res.margin != null) {
    const nums = el("div", "th-nums");
    const bits = [];
    if (res.confidence != null) bits.push(`confidence ${pct(res.confidence)}`);
    if (res.margin != null) bits.push(`margin ${res.margin}`);
    if (res.against && res.against.branch) {
      bits.push(`against ${res.against.branch}`);
    }
    nums.textContent = bits.join(" · ");
    box.appendChild(nums);
  }

  for (const comment of thread.comments || []) {
    box.appendChild(renderComment(thread, comment));
  }

  const foot = el("div", "th-foot");
  const reply = document.createElement("textarea");
  reply.className = "th-reply";
  reply.rows = 2;
  reply.placeholder = "Reply…  @chat:main notifies the agent";
  reply.setAttribute("aria-label", `Reply to thread ${thread.id}`);
  // The whole panel re-renders on every `comment_changed` — including somebody
  // else's — so a half-typed reply has to survive being rebuilt underneath the
  // person typing it.
  reply.value = drafts.get(thread.id) || "";
  reply.addEventListener("input", () => drafts.set(thread.id, reply.value));
  box.appendChild(reply);

  foot.appendChild(
    action("Reply", async () => {
      const body = reply.value.trim();
      if (!body) return;
      drafts.delete(thread.id);
      await mutate(() =>
        api.addComment(state.projectName, { thread: thread.id, body })
      );
    })
  );
  if (thread.state === "open") {
    foot.appendChild(
      action("Resolve", () =>
        mutate(() => api.resolveThread(state.projectName, thread.id))
      )
    );
  } else {
    foot.appendChild(
      action("Reopen", () =>
        mutate(() => api.reopenThread(state.projectName, thread.id))
      )
    );
  }
  const focusable = res.status === "ok" || res.status === "moved";
  const go = action("Show", () => focus(thread, res));
  go.disabled = !focusable;
  go.title = focusable
    ? "Select and reveal what this thread points at"
    : `Not focusable: the anchor is ${res.status}` +
      (res.reason ? ` (${res.reason})` : "");
  foot.appendChild(go);
  box.appendChild(foot);
  return box;
}

function renderComment(thread, comment) {
  const box = el("div", "th-comment");
  const head = el("div", "th-comment-head");
  const who = el("span", `th-who ${comment.author_kind === "agent" ? "agent" : "human"}`);
  who.textContent = comment.author ? displayPrincipal(comment.author) : "?";
  who.title =
    `${comment.author} — ${comment.author_kind}. Identity here is ` +
    "self-asserted bookkeeping, not authentication.";
  head.appendChild(who);
  const when = el("span", "th-when");
  when.textContent = comment.edited ? `${comment.ts} (edited)` : comment.ts;
  head.appendChild(when);
  box.appendChild(head);

  const body = el("div", "th-text");
  if (comment.deleted) {
    body.classList.add("deleted");
    body.textContent = "(deleted)";
  } else {
    body.textContent = comment.body || "";
  }
  box.appendChild(body);

  for (const attach of comment.attachments || []) {
    const chip = el("span", `th-attach${attach.available ? "" : " missing"}`);
    chip.textContent = attach.path;
    chip.title = attach.available
      ? attach.path
      : `${attach.path} — not on this branch (exports/ is branch-scoped)`;
    box.appendChild(chip);
  }

  const mine = comment.author === clientId;
  const isRoot = (thread.comments || [])[0] === comment;
  if (mine && !comment.deleted && !isRoot) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "th-del";
    del.textContent = "×";
    del.title = "Delete this comment (leaves a tombstone and an audit line)";
    del.setAttribute("aria-label", "Delete comment");
    del.addEventListener("click", () =>
      mutate(() =>
        api.deleteComment(state.projectName, thread.id, comment.id)
      )
    );
    box.appendChild(del);
  }
  return box;
}

async function mutate(run) {
  if (busy) return;
  busy = true;
  try {
    await run();
    await refresh();
  } catch (err) {
    actions.toast(errorText(err), "error");
  } finally {
    busy = false;
  }
}

// --------------------------------------------------------- click-to-focus

/** Reveal what a thread points at *now*. Every branch below reads the
 *  RESOLUTION, never the stored anchor: after a rebuild the stored face
 *  ordinal or line number can name something else entirely. */
function focus(thread, res) {
  const anchor = thread.anchor || {};
  if (res.status !== "ok" && res.status !== "moved") return;
  switch (anchor.kind) {
    case "face": {
      const index = res.face_index != null ? res.face_index : anchor.face_index;
      Promise.resolve(actions.selectPart(anchor.part)).then(() => {
        viewport.highlightFace(anchor.part, index);
        viewport.fit();
        syncPins();
      });
      return;
    }
    case "script_range": {
      const start = res.start != null ? res.start : anchor.start;
      const end = res.end != null ? res.end : anchor.end;
      Promise.resolve(actions.selectPart(anchor.part)).then(() => {
        inspector.setTab("code");
        editor.revealRange(start, end);
      });
      return;
    }
    case "param": {
      Promise.resolve(actions.selectPart(anchor.part)).then(() => {
        inspector.setTab("params");
        const row = document.querySelector(
          `#pane-params .param[data-param="${cssEscape(anchor.param)}"]`
        );
        if (row) {
          row.scrollIntoView({ block: "center" });
          row.classList.add("th-flash");
          setTimeout(() => row.classList.remove("th-flash"), 1200);
        }
      });
      return;
    }
    case "instance":
      actions.selectAssembly(anchor.instance);
      return;
    case "part":
      actions.selectPart(anchor.part);
      return;
    case "proposal_hunk":
      if (actions.openProposal) actions.openProposal(anchor.proposal, "files");
      return;
    default:
  }
}

function cssEscape(value) {
  const text = String(value == null ? "" : value);
  return window.CSS && window.CSS.escape
    ? window.CSS.escape(text)
    : text.replace(/["\\]/g, "\\$&");
}

// ------------------------------------------------------------------- pins

/** Rebuild the pin overlay from state. Only OPEN face threads on the part on
 *  stage get one, and only while their anchor resolves — an orphan has no
 *  place to be drawn, and drawing it anywhere would be the mis-pin. */
function syncPins() {
  if (!pinsHost) return;
  pinsHost.textContent = "";
  pins = [];
  if (state.mode !== "part" || !state.selectedPart) return;
  const rows = threadsFor("face", state.selectedPart).filter((t) => {
    const status = resolutionOf(t).status;
    return status === "ok" || status === "moved";
  });
  rows.forEach((thread, i) => {
    const res = resolutionOf(thread);
    const index = res.face_index != null ? res.face_index : thread.anchor.face_index;
    // The centroid of the RESOLVED face on the geometry that is ON SCREEN, and
    // nothing else. There used to be a fallback to the centroid the anchor
    // recorded at creation, for the window before the triangle->face sidecar
    // loads — but that is the anchor's OLD position on geometry that has since
    // changed, which is a guessed pin: indistinguishable from a located one,
    // wrong exactly when the thread `moved`, and permanent if the fetch fails.
    // No centroid, no pin; `meshChanged()` re-runs this when the map arrives.
    const world = viewport.faceCentroid(state.selectedPart, index);
    if (!world) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `pin st-${res.status}`;
    btn.textContent = String(i + 1);
    btn.title =
      `Thread ${thread.id} on face ${index}` +
      (res.status === "moved" ? ` (moved from ${thread.anchor.face_index})` : "") +
      `\n${((thread.comments || [])[0] || {}).body || ""}`;
    btn.addEventListener("click", () => {
      expanded = thread.id;
      inspector.setTab("threads");
      render();
      viewport.highlightFace(state.selectedPart, index);
    });
    pinsHost.appendChild(btn);
    pins.push({ thread, el: btn, world });
  });
  positionPins();
}

function positionPins() {
  if (!pins.length) return;
  for (const pin of pins) {
    const at = viewport.projectPoint(pin.world);
    if (!at) {
      pin.el.style.display = "none";
      continue;
    }
    pin.el.style.display = "flex";
    pin.el.style.transform = `translate(${at.x.toFixed(1)}px, ${at.y.toFixed(1)}px)`;
  }
}

// ---------------------------------------------------------- editor gutter

function syncGutter() {
  const partId = state.part && state.part.kind !== "reference" ? state.part.id : null;
  if (!partId) {
    editor.setCommentGutter([], null);
    return;
  }
  const byLine = new Map();
  for (const thread of threadsFor("script_range", partId)) {
    const res = resolutionOf(thread);
    if (res.status === "orphaned") continue; // nothing to point the gutter at
    const line = res.start != null ? res.start : thread.anchor.start;
    if (!byLine.has(line)) byLine.set(line, []);
    byLine.get(line).push({ thread, res });
  }
  const rows = [];
  for (const [line, items] of byLine) {
    const worst = items.some((i) => i.res.status === "unverified")
      ? "unverified"
      : items.some((i) => i.res.status === "moved")
        ? "moved"
        : "ok";
    rows.push({
      line,
      count: items.length,
      status: worst,
      thread: items[0].thread.id,
      title: items
        .map(
          (i) =>
            `${((i.thread.comments || [])[0] || {}).body || ""}` +
            (i.res.status === "ok"
              ? ""
              : ` — ${i.res.status}${i.res.reason ? ` (${i.res.reason})` : ""}`)
        )
        .join("\n"),
    });
  }
  editor.setCommentGutter(rows, (row) => {
    expanded = row.thread;
    inspector.setTab("threads");
    render();
  });
}

// ------------------------------------------------------------ param badges

/** inspector.js's decorator seam, called after EVERY params render — both the
 *  full rebuild and the values-only sync. */
function decorateParams(paramsPane, part) {
  if (!paramsPane) return;
  for (const old of paramsPane.querySelectorAll(".param-badge")) old.remove();
  if (!part) return;
  const byParam = new Map();
  for (const thread of threadsFor("param", part.id)) {
    const name = (thread.anchor || {}).param;
    if (!byParam.has(name)) byParam.set(name, []);
    byParam.get(name).push(thread);
  }
  for (const row of paramsPane.querySelectorAll(".param")) {
    const name = row.dataset.param;
    const head = row.querySelector(".param-head");
    if (!head) continue;
    const rows = byParam.get(name) || [];
    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "param-badge";
    if (!rows.length) {
      // The empty badge is how a param thread is STARTED; it stays invisible
      // until the row is hovered or focused, so a pane of twelve parameters
      // does not become a pane of twelve buttons.
      badge.classList.add("empty");
      badge.textContent = "💬";
      badge.title = `Open a review thread on ${name}`;
      badge.setAttribute("aria-label", `Comment on ${name}`);
      badge.addEventListener("click", (e) => {
        e.stopPropagation();
        openComposer(
          { kind: "param", part_id: part.id, param: name },
          { at: { x: e.clientX, y: e.clientY } }
        );
      });
    } else {
      const orphaned = rows.some((t) => resolutionOf(t).status === "orphaned");
      if (orphaned) badge.classList.add("st-orphaned");
      badge.textContent = String(rows.length);
      badge.title =
        `${rows.length} open thread${rows.length === 1 ? "" : "s"} on ${name}` +
        (orphaned ? " (one anchor is orphaned)" : "");
      badge.addEventListener("click", (e) => {
        e.stopPropagation();
        expanded = rows[0].id;
        inspector.setTab("threads");
        render();
      });
    }
    head.appendChild(badge);
  }
}

// ------------------------------------------------------------------ labels

/** The breadcrumb: what the author pointed at, and where it is NOW when that
 *  is a different address. */
export function anchorLabel(anchor, res) {
  const a = anchor || {};
  const part = a.part || a.part_id || "";
  const moved = res && res.status === "moved";
  switch (a.kind) {
    case "face": {
      const stored = a.face_index;
      const now = res && res.face_index != null ? res.face_index : stored;
      return moved && now !== stored
        ? `${part} · face ${stored} → ${now}`
        : `${part} · face ${now}`;
    }
    case "param":
      return `${part} · ${a.param}`;
    case "script_range": {
      const start = moved && res.start != null ? res.start : a.start;
      const end = moved && res.end != null ? res.end : a.end;
      return moved
        ? `${part} · L${a.start}–${a.end} → L${start}–${end}`
        : `${part} · L${start}–${end}`;
    }
    case "instance":
      return `instance ${a.instance || a.instance_id}`;
    case "proposal_hunk": {
      const hunk = moved && res.hunk != null ? res.hunk : a.hunk;
      return `#${a.proposal} · ${a.file} hunk ${hunk}`;
    }
    case "part":
    default:
      return part || a.kind || "anchor";
  }
}

function statusChip(res) {
  const chip = el("span", `anchor-chip st-${res.status}`);
  chip.textContent = STATUS_LABEL[res.status] || res.status;
  const lines = [`${res.status} — ${STATUS_GLOSS[res.status] || ""}`];
  if (res.reason) lines.push(`reason: ${res.reason}`);
  if (res.hint) lines.push(res.hint);
  if (res.confidence != null) lines.push(`confidence ${pct(res.confidence)}`);
  if (res.margin != null) lines.push(`margin ${res.margin}`);
  if (res.against && res.against.branch) {
    lines.push(`resolved against ${res.against.branch}`);
  }
  chip.title = lines.join("\n");
  return chip;
}

// ------------------------------------------------------------------- atoms

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function note(text) {
  const div = el("div", "pane-note");
  div.textContent = text;
  return div;
}

function action(label, run) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "th-action";
  btn.textContent = label;
  btn.addEventListener("click", () => run());
  return btn;
}

function pct(value) {
  return `${Math.round(Number(value) * 100)}%`;
}

function errorText(err) {
  if (err instanceof ApiError) {
    return err.error && err.error.message ? err.error.message : err.message;
  }
  return String(err && err.message ? err.message : err);
}
