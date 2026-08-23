// Bottom-dock agent chat: sends messages, renders streamed deltas and
// tool-call chips from WebSocket events, and degrades gracefully when the
// chat backend is absent (no API key, or the endpoint doesn't exist yet).

import { api, ApiError } from "./api.js";
import { state, onKeys } from "./state.js";
import * as layout from "./shell/layout.js";
import * as skillsModel from "./skills_model.js";

const MCP_SNIPPET =
  "claude mcp add agentcad -- uv --directory <path-to-agentcad-repo> run agentcad mcp";

let dock, headEl, chevron, messagesEl, formEl, inputEl, sendBtn, emptyEl, hintEl;
let streamEl = null; // assistant bubble currently receiving deltas
let sending = false;
let historyProject = null;
// The dock is a single-session UI pinned to session "main"; other agents'
// sessions on the same project are acknowledged once per id, never rendered.
const DOCK_SESSION = "main";
const noticedSessions = new Set();

export function init() {
  dock = document.getElementById("chat-dock");
  headEl = document.getElementById("chat-head");
  chevron = document.getElementById("chat-chevron");
  messagesEl = document.getElementById("chat-messages");
  formEl = document.getElementById("chat-form");
  inputEl = document.getElementById("chat-input");
  sendBtn = document.getElementById("chat-send");
  emptyEl = document.getElementById("chat-empty");
  hintEl = document.getElementById("chat-hint");

  headEl.addEventListener("click", toggle);
  formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    send();
  });

  // PRD-026 slice 4: `layout.init()` (called earlier in boot()) already
  // applied the dock's initial collapsed state — migrated once from this
  // module's old `agentcad.chat.open` key — so this module no longer reads
  // localStorage itself. A MutationObserver keeps the chevron in sync with
  // the `collapsed` class regardless of WHO changes it: this header click,
  // the `view.chat.toggle` shortcut/menu row, or a future workspace switch.
  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(updateChevron)
      .observe(dock, { attributes: true, attributeFilter: ["class"] });
  }
  updateChevron();

  onKeys(["chatAvailable", "projectName"], () => {
    renderAvailability();
    if (state.projectName !== historyProject) {
      historyProject = state.projectName;
      messagesEl.textContent = "";
      streamEl = null;
      noticedSessions.clear(); // notices were wiped with the messages
      // A turn from the previous project can no longer complete here; its
      // chat_done would be filtered out, so don't leave the input locked.
      setSending(false);
      if (state.chatAvailable && state.projectName) loadHistory(state.projectName);
    }
  });
  renderAvailability();
}

function toggle() {
  layout.toggle("chat"); // persists and applies the `.collapsed` class itself
  const open = !dock.classList.contains("collapsed");
  if (open) inputEl.focus();
}

function updateChevron() {
  chevron.textContent = dock.classList.contains("collapsed") ? "▲" : "▼";
}

function renderAvailability() {
  const available = state.chatAvailable;
  emptyEl.classList.toggle("hidden", available);
  formEl.classList.toggle("hidden", !available);
  messagesEl.classList.toggle("hidden", !available);
  hintEl.textContent = available
    ? state.projectName
      ? `agent works on ${state.projectName}`
      : ""
    : "unavailable — expand for setup";
  if (!available) {
    emptyEl.innerHTML = "";
    const p = document.createElement("div");
    p.textContent =
      "Set ANTHROPIC_API_KEY and restart to chat here — or drive AgentCAD from Claude Code via:";
    const code = document.createElement("code");
    code.textContent = MCP_SNIPPET;
    emptyEl.appendChild(p);
    emptyEl.appendChild(code);
  }
}

async function loadHistory(project) {
  let payload;
  try {
    payload = await api.chatHistory(project);
  } catch {
    return; // history endpoint absent or empty — fine
  }
  if (project !== state.projectName) return;
  const messages = (payload && payload.messages) || [];
  for (const msg of messages) renderHistoryMessage(msg);
  scrollDown();
}

function renderHistoryMessage(msg) {
  try {
    const role = msg.role === "user" ? "user" : "assistant";
    const content = msg.content;
    if (typeof content === "string") {
      if (content.trim()) addBubble(role, content);
      return;
    }
    if (Array.isArray(content)) {
      for (const block of content) {
        if (block.type === "text" && block.text && block.text.trim()) {
          addBubble(role, block.text);
        } else if (block.type === "tool_use") {
          addToolChip(block.name || "tool", block.input || {}, "ok");
        }
      }
    }
  } catch {
    /* unknown history shape — skip the message */
  }
}

// -------------------------------------------------------------------- send

async function send() {
  const text = inputEl.value.trim();
  if (!text || sending) return;
  if (!state.projectName) {
    addNotice("Open a project first — the agent works within one project.");
    return;
  }
  inputEl.value = "";
  addBubble("user", text);
  scrollDown();
  setSending(true);
  try {
    await api.chat(state.projectName, text);
    // response streams in over the websocket (chat_delta / chat_done)
  } catch (err) {
    setSending(false);
    if (err instanceof ApiError && err.status === 404) {
      addNotice("The chat backend is not available in this build. Use the MCP route instead:");
      const code = document.createElement("code");
      code.textContent = MCP_SNIPPET;
      code.style.margin = "0 auto";
      messagesEl.appendChild(code);
    } else {
      addNotice(`Chat failed: ${err.message}`);
    }
    scrollDown();
  }
}

function setSending(on) {
  sending = on;
  sendBtn.disabled = on;
  inputEl.placeholder = on ? "Agent is working…" : "Ask the agent to model something…";
}

// Called by main.js when the websocket reconnects: a chat_done published
// while the socket was down is gone for good, so unlock the composer.
export function resetSending() {
  if (sending) setSending(false);
}

// ------------------------------------------------------------- ws events

export function handleEvent(ev) {
  // Events without a session come from a pre-session backend: treat as ours.
  const session = ev.session || DOCK_SESSION;
  if (ev.type === "chat_done" && session === DOCK_SESSION) {
    // Always release the composer, even if the turn belongs to a project
    // we have since navigated away from — otherwise 'sending' sticks forever.
    // Scoped to our own session: another agent's chat_done must not unlock
    // a composer that is still waiting on its own turn.
    setSending(false);
  }
  if (ev.project && state.projectName && ev.project !== state.projectName) return;
  if (session !== DOCK_SESSION) {
    // Another agent's session on this project: surface a one-line notice the
    // first time each session id shows up, but never render its stream.
    if (!noticedSessions.has(session)) {
      noticedSessions.add(session);
      const div = addNotice(`another agent session is active: ${session}`);
      div.classList.add("session-notice");
      scrollDown();
    }
    return;
  }
  switch (ev.type) {
    case "chat_delta": {
      if (!streamEl) {
        streamEl = addBubble("assistant", "");
        streamEl.classList.add("streaming");
      }
      streamEl.textContent += ev.text || "";
      scrollDown();
      break;
    }
    case "chat_tool_call": {
      finishStream();
      const chip = addToolChip(ev.name || "tool", ev.args || {}, "pending");
      chip.dataset.tool = ev.name || "tool";
      scrollDown();
      break;
    }
    case "chat_tool_result": {
      const chips = messagesEl.querySelectorAll(".tool-chip.pending");
      let chip = null;
      for (const c of chips) {
        if (c.dataset.tool === (ev.name || "tool")) chip = c;
      }
      if (!chip && chips.length) chip = chips[chips.length - 1];
      if (chip) {
        chip.classList.remove("pending");
        chip.classList.add(ev.ok === false ? "err" : "ok");
        chip.querySelector(".tool-status").textContent =
          ev.ok === false ? "error" : "ok";
        if (ev.result !== undefined) {
          // The backend sends `result` as a pre-serialized JSON string,
          // truncated to 2000 chars (so it may be cut mid-token). Render it
          // verbatim via textContent — never innerHTML — and mark truncation.
          const pre = chip.querySelector("pre");
          let text;
          if (typeof ev.result === "string") {
            text = ev.result;
            if (text.length >= 2000) text += " … (truncated)";
          } else {
            text = safeJson(ev.result); // older/other payload shapes
          }
          if (pre) pre.textContent += "\n→ " + text;
        }
      }
      scrollDown();
      break;
    }
    case "chat_done": {
      finishStream(); // sending already reset above, before the project filter
      break;
    }
    // PRD-029 FR7/AC1. A skill is agent INSTRUCTIONS entering the agent's
    // context, so the dock says so — the transparency half of the trust story
    // (spec §7), and an inspectable prompt-injection surface.
    //
    // The `client` filter is the whole correctness of this chip: the Skills
    // modal's own preview goes through `load_skill` too (so a human read is
    // logged like every other surface), and that read must render NOTHING
    // here. Only the chat engine's own ids — `chat` / `chat:<session>` —
    // qualify; `browser:<hex>` and `mcp` do not.
    case "skill_loaded": {
      if (!skillsModel.isChatClient(ev.client)) break;
      addSkillChip(ev);
      scrollDown();
      break;
    }
    // The budget evicted it (its `tool_result` in the history was rewritten to
    // a stub). The chip STAYS — the transcript above it was written while the
    // skill was loaded — and is struck through instead.
    case "skill_unloaded": {
      markSkillUnloaded(ev.name);
      break;
    }
  }
}

function finishStream() {
  if (streamEl) {
    streamEl.classList.remove("streaming");
    if (!streamEl.textContent.trim()) streamEl.remove();
    streamEl = null;
  }
}

// ------------------------------------------------------------------- DOM

function addBubble(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  return div;
}

function addNotice(text) {
  const div = document.createElement("div");
  div.className = "msg notice";
  div.textContent = text;
  messagesEl.appendChild(div);
  return div;
}

function addToolChip(name, args, status) {
  const details = document.createElement("details");
  details.className = `tool-chip ${status}`;
  const summary = document.createElement("summary");
  const nameEl = document.createElement("span");
  nameEl.className = "tool-name";
  nameEl.textContent = name;
  const statusEl = document.createElement("span");
  statusEl.className = "tool-status";
  statusEl.textContent = status === "pending" ? "running…" : status;
  summary.appendChild(nameEl);
  summary.appendChild(statusEl);
  details.appendChild(summary);
  const pre = document.createElement("pre");
  pre.textContent = safeJson(args);
  details.appendChild(pre);
  messagesEl.appendChild(details);
  return details;
}

/** "📘 snap-fits · core" — a flat pill, distinct from a `.tool-chip` (which is
 *  a disclosure with the call's arguments in it). Keyed by `data-skill` so the
 *  matching `skill_unloaded` can find it by name. */
function addSkillChip(ev) {
  const div = document.createElement("div");
  div.className = "skill-chip";
  div.dataset.skill = ev.name ? String(ev.name) : "";
  // The label lives in its own span so `.unloaded` can strike THAT and not the
  // "unloaded" word explaining it: a `line-through` set on the chip is painted
  // straight through every inline descendant, and a child cannot opt out.
  const label = document.createElement("span");
  label.className = "skill-chip-name";
  label.textContent = skillsModel.chipLabel(ev);
  div.appendChild(label);
  messagesEl.appendChild(div);
  return div;
}

function markSkillUnloaded(name) {
  if (!name) return;
  for (const chip of messagesEl.querySelectorAll(".skill-chip")) {
    if (chip.dataset.skill !== String(name)) continue;
    if (chip.classList.contains("unloaded")) continue;
    chip.classList.add("unloaded");
    const note = document.createElement("span");
    note.className = "skill-chip-note";
    note.textContent = "unloaded";
    chip.appendChild(note);
  }
}

function safeJson(value) {
  try {
    const s = JSON.stringify(value, null, 1);
    return s.length > 2000 ? s.slice(0, 2000) + " …" : s;
  } catch {
    return String(value);
  }
}

function scrollDown() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
