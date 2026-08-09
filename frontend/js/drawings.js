// 2D engineering drawings. SVG opens an in-app preview on a white sheet (the
// drawing is meant for print, so it keeps its own black/blue ink regardless of
// the app theme); DXF is written to the project's exports/ and reported via a
// toast. Both are script-part only — the server rejects reference parts.

import { api, ApiError } from "./api.js";

let actions = null;
let overlay, titleEl, viewEl, downloadEl, closeBtn;
let lastUrl = null; // object URL to revoke when the modal closes

export function init(a) {
  actions = a;
  overlay = document.getElementById("drawing-modal");
  titleEl = document.getElementById("drawing-title");
  viewEl = document.getElementById("drawing-view");
  downloadEl = document.getElementById("drawing-download");
  closeBtn = document.getElementById("drawing-close");

  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !overlay.classList.contains("hidden")) close();
  });
}

function open() {
  overlay.classList.remove("hidden");
}

function close() {
  overlay.classList.add("hidden");
  viewEl.textContent = "";
  if (lastUrl) {
    URL.revokeObjectURL(lastUrl);
    lastUrl = null;
  }
  downloadEl.classList.add("hidden");
  downloadEl.removeAttribute("href");
}

export async function previewSvg(project, partId) {
  open();
  titleEl.textContent = `${partId} · drawing`;
  downloadEl.classList.add("hidden");
  viewEl.textContent = "";
  const status = document.createElement("div");
  status.className = "drawing-status";
  status.textContent = "Generating drawing…";
  viewEl.appendChild(status);

  try {
    // POST regenerates the file server-side; the GET streams the SVG bytes.
    // The tool route returns {error:...} at HTTP 200 on failure.
    const gen = await api.generateDrawing(project, partId, { format: "svg" });
    if (gen && gen.error) {
      showError(gen.error.message || "drawing failed");
      return;
    }
    const res = await fetch(api.drawingSvgUrl(project, partId));
    const type = res.headers.get("content-type") || "";
    if (!res.ok || !type.includes("svg")) {
      // The GET route returns a JSON error body on failure.
      let msg = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        msg = (body.error && body.error.message) || msg;
      } catch {
        /* keep msg */
      }
      showError(msg);
      return;
    }
    const svgText = await res.text();
    const blob = new Blob([svgText], { type: "image/svg+xml" });
    lastUrl = URL.createObjectURL(blob);
    const img = document.createElement("img");
    img.className = "drawing-img";
    img.alt = `${partId} engineering drawing`;
    img.src = lastUrl;
    viewEl.textContent = "";
    viewEl.appendChild(img);
    downloadEl.href = lastUrl;
    downloadEl.download = `${partId}_drawing.svg`;
    downloadEl.textContent = "Download SVG";
    downloadEl.classList.remove("hidden");
  } catch (err) {
    showError(err instanceof ApiError ? err.error.message : String(err));
  }
}

export async function saveDxf(project, partId) {
  try {
    const result = await api.generateDrawing(project, partId, { format: "dxf" });
    if (result && result.error) {
      actions.toast(`Drawing failed: ${result.error.message || "error"}`, "error");
      return;
    }
    const kb = ((result.size_bytes || 0) / 1024).toFixed(1);
    actions.toast(`Wrote ${result.path} (${kb} KB)`);
  } catch (err) {
    const detail = err instanceof ApiError ? err.error.message : String(err);
    actions.toast(`Drawing failed: ${detail}`, "error");
  }
}

function showError(message) {
  viewEl.textContent = "";
  const el = document.createElement("div");
  el.className = "drawing-status error";
  el.textContent = message;
  viewEl.appendChild(el);
}
