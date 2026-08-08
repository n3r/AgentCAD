// CodeMirror 5 wrapper for the Code tab. CodeMirror is loaded as a plain
// script in index.html (window.CodeMirror); this module owns the instance,
// dirty tracking, and the Save & Rebuild flow trigger.

let cm = null;
let onSaveCallback = null;
let currentPartId = null;
let cleanText = "";
let dirtyEl = null;
let saveBtn = null;

export function init(hostEl, { onSave }) {
  onSaveCallback = onSave;
  dirtyEl = document.getElementById("editor-dirty");
  saveBtn = document.getElementById("save-btn");

  cm = window.CodeMirror(hostEl, {
    value: "",
    mode: "python",
    theme: "agentcad",
    lineNumbers: true,
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
  saveBtn.addEventListener("click", () => save());
  updateDirty();
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

export function isDirty() {
  return cm.getValue() !== cleanText;
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
  if (isDirty()) {
    dirtyEl.textContent = "unsaved changes — ⌘S to save";
    dirtyEl.classList.add("dirty");
  } else {
    dirtyEl.textContent = currentPartId ? "saved" : "";
    dirtyEl.classList.remove("dirty");
  }
}
