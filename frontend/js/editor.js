// CodeMirror 5 wrapper for the Code tab. CodeMirror is loaded as a plain
// script in index.html (window.CodeMirror); this module owns the instance,
// dirty tracking, and the Save & Rebuild flow trigger.

let cm = null;
let onSaveCallback = null;
let currentPartId = null;
let cleanText = "";
let dirtyEl = null;
let saveBtn = null;
let lastDirty = false;
let gutterClickHandler = null;
const dirtyListeners = new Set();

export function init(hostEl, { onSave }) {
  onSaveCallback = onSave;
  dirtyEl = document.getElementById("editor-dirty");
  saveBtn = document.getElementById("save-btn");

  cm = window.CodeMirror(hostEl, {
    value: "",
    mode: "python",
    theme: "agentcad",
    lineNumbers: true,
    // The second gutter is PRD-008's: one marker per open script_range thread.
    // CodeMirror 5 needs the gutter DECLARED here — setGutterMarker on an
    // undeclared gutter silently does nothing — and the linenumbers gutter
    // must be listed too or declaring gutters at all drops it.
    gutters: ["CodeMirror-linenumbers", "agentcad-comments"],
    lineWrapping: true,
    indentUnit: 4,
    styleActiveLine: false,
    viewportMargin: 30,
    extraKeys: {
      "Cmd-S": () => save(),
      "Ctrl-S": () => save(),
      Tab: (inst) => {
        if (inst.somethingSelected()) inst.indentSelection("add");
        else inst.replaceSelection("    ", "end");
      },
    },
  });
  cm.on("change", updateDirty);
  // Clicking the comments gutter where there is no marker is how a script_range
  // thread starts. A marker swallows its own mousedown, so this only fires on
  // empty gutter space.
  cm.on("gutterClick", (inst, line, gutter, ev) => {
    if (gutter !== "agentcad-comments" || !gutterClickHandler) return;
    gutterClickHandler(line + 1, ev);
  });
  saveBtn.addEventListener("click", () => save());
  updateDirty();
}

/** fn(line1Based, mouseEvent) for a click on empty comment-gutter space. */
export function onCommentGutterClick(fn) {
  gutterClickHandler = fn;
}

/** The selected line range as 1-based inclusive {start, end}, or null. */
export function selectionRange() {
  if (!cm || !cm.somethingSelected()) return null;
  const from = cm.getCursor("from");
  const to = cm.getCursor("to");
  // A selection that ends at column 0 stops on the line above it, the way
  // every editor's "selected lines" count does.
  const endLine = to.ch === 0 && to.line > from.line ? to.line - 1 : to.line;
  return { start: from.line + 1, end: endLine + 1 };
}

/** Total lines, so a caller can clamp a range before it asks the server to. */
export function lineCount() {
  return cm ? cm.lineCount() : 0;
}

export function setPart(partId, script) {
  const switching = partId !== currentPartId;
  currentPartId = partId;
  if (switching) {
    cleanText = script ?? "";
    cm.setValue(cleanText);
    cm.clearHistory();
  } else if (!isDirty() && script != null && script !== cleanText) {
    // same part, fresh server copy, no local edits -> adopt it
    cleanText = script;
    cm.setValue(cleanText);
  }
  updateDirty();
}

export function markSaved(script) {
  cleanText = script;
  updateDirty();
}

export function getScript() {
  return cm.getValue();
}

/** Append text to the end of the buffer (used by the sketcher's insert).
 *  Fires CodeMirror's change event, so dirty tracking updates itself. */
export function insertText(text) {
  if (!cm || !currentPartId) return false;
  const last = cm.lineCount(); // position past the end clamps to doc end
  cm.replaceRange(text, { line: last, ch: 0 });
  cm.focus();
  cm.setCursor({ line: cm.lineCount(), ch: 0 });
  return true;
}

export function isDirty() {
  return cm.getValue() !== cleanText;
}

/** Called on every change to the buffer's dirty state (PRD-008 slice 9 wires
 *  the part claim to it: a dirty buffer claims the part, viewing never does). */
export function onDirtyChange(fn) {
  dirtyListeners.add(fn);
  return () => dirtyListeners.delete(fn);
}

// ------------------------------------------------------------ comment gutter

/** Replace every marker in the `agentcad-comments` gutter.
 *
 *  `rows` is [{line, count, status, title, thread}] with 1-based lines that
 *  have ALREADY been through the server's resolution — a moved thread hands us
 *  its current line, never the one it was authored on. Called from state, so
 *  it is idempotent: it clears the gutter first, every time. */
export function setCommentGutter(rows, onClick) {
  if (!cm) return;
  cm.clearGutter("agentcad-comments");
  for (const row of rows || []) {
    const line = Math.min(Math.max(1, row.line | 0), cm.lineCount()) - 1;
    const marker = document.createElement("span");
    marker.className = `cm-thread-mark st-${row.status || "ok"}`;
    marker.textContent = row.count > 1 ? String(row.count) : "●";
    marker.title = row.title || "";
    marker.setAttribute("role", "button");
    marker.addEventListener("mousedown", (e) => e.stopPropagation());
    marker.addEventListener("click", (e) => {
      e.stopPropagation();
      if (onClick) onClick(row);
    });
    cm.setGutterMarker(line, "agentcad-comments", marker);
  }
}

/** Scroll a 1-based line range into view and select it (thread click-to-focus). */
export function revealRange(start, end) {
  if (!cm) return;
  const last = cm.lineCount();
  const from = Math.min(Math.max(1, start | 0), last) - 1;
  const to = Math.min(Math.max(from + 1, end | 0), last) - 1;
  cm.setSelection({ line: from, ch: 0 }, { line: to, ch: cm.getLine(to).length });
  cm.scrollIntoView({ from: { line: from, ch: 0 }, to: { line: to, ch: 0 } }, 80);
  cm.focus();
}

export function refresh() {
  // CodeMirror needs a refresh after its pane becomes visible
  if (cm) cm.refresh();
}

export function save() {
  if (!currentPartId || !onSaveCallback) return;
  onSaveCallback(currentPartId, cm.getValue());
}

export function setSaving(saving) {
  saveBtn.disabled = saving;
  saveBtn.textContent = saving ? "Rebuilding…" : "Save & Rebuild";
}

function updateDirty() {
  if (!dirtyEl) return;
  const dirty = isDirty();
  if (dirty !== lastDirty) {
    lastDirty = dirty;
    for (const fn of [...dirtyListeners]) fn(dirty, currentPartId);
  }
  if (dirty) {
    dirtyEl.textContent = "unsaved changes — ⌘S to save";
    dirtyEl.classList.add("dirty");
  } else {
    dirtyEl.textContent = currentPartId ? "saved" : "";
    dirtyEl.classList.remove("dirty");
  }
}
