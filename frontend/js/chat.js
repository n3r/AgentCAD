// Bottom-dock agent chat: sends messages, renders streamed deltas and
// tool-call chips from WebSocket events, and degrades gracefully when the
// chat backend is absent (no API key, or the endpoint doesn't exist yet).

import { api, ApiError } from "./api.js";
import { state, onKeys } from "./state.js";

const MCP_SNIPPET =
  "claude mcp add agentcad -- uv --directory <path-to-agentcad-repo> run agentcad mcp";

let dock, headEl, chevron, messagesEl, formEl, inputEl, sendBtn, emptyEl, hintEl;
let streamEl = null; // assistant bubble currently receiving deltas
let sending = false;
let historyProject = null;

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

  if (localStorage.getItem("agentcad.chat.open") === "1") {
    dock.classList.remove("collapsed");
  }
  updateChevron();

  onKeys(["chatAvailable", "projectName"], () => {
    renderAvailability();
    if (state.projectName !== historyProject) {
      historyProject = state.projectName;
      messagesEl.textContent = "";
      streamEl = null;
      // A turn from the previous project can no longer complete here; its
      // chat_done would be filtered out, so don't leave the input locked.
      setSending(false);
      if (state.chatAvailable && state.projectName) loadHistory(state.projectName);
    }
  });
  renderAvailability();
}

function toggle() {
  dock.classList.toggle("collapsed");
  const open = !dock.classList.contains("collapsed");
  localStorage.setItem("agentcad.chat.open", open ? "1" : "0");
  updateChevron();
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
  if (ev.type === "chat_done") {
    // Always release the composer, even if the turn belongs to a project
    // we have since navigated away from — otherwise 'sending' sticks forever.
    setSending(false);
  }
  if (ev.project && state.projectName && ev.project !== state.projectName) return;
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
