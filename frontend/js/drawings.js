// 2D engineering drawings. SVG opens an in-app preview on a white sheet (the
// drawing is meant for print, so it keeps its own black/blue ink regardless of
// the app theme); DXF is written to the project's exports/ and reported via a
// toast. Both are script-part only — the server rejects reference parts.

import { api, ApiError } from "./api.js";
import { state } from "./state.js";

let actions = null;
let overlay, titleEl, viewEl, downloadEl, closeBtn;
let dimTableEl, dimTableWrap;
let lastUrl = null; // object URL to revoke when the modal closes
// FR8's dimension table, remembered for the session: it is a property of how
// you want to read the sheet, not of the part, so re-previewing another
// configuration keeps it on.
let dimTable = false;
let previewing = null; // {project, partId} — what the checkbox re-renders
// Two drawing generations are two multi-second kernel round trips, and
// toggling the checkbox starts a second one while the first is still in
// flight. Only the newest may touch the modal — an older response landing
// after it would paint the sheet the user just moved away from.
let previewSeq = 0;

export function init(a) {
  actions = a;
  overlay = document.getElementById("drawing-modal");
  titleEl = document.getElementById("drawing-title");
  viewEl = document.getElementById("drawing-view");
  downloadEl = document.getElementById("drawing-download");
  closeBtn = document.getElementById("drawing-close");
  dimTableEl = document.getElementById("drawing-dimtable");
  dimTableWrap = document.getElementById("drawing-dimtable-wrap");

  if (dimTableEl) {
    dimTableEl.addEventListener("change", () => {
      dimTable = dimTableEl.checked;
      if (previewing) previewSvg(previewing.project, previewing.partId);
    });
  }
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
  previewing = null;
  previewSeq++;             // orphan any preview still in flight
  if (lastUrl) {
    URL.revokeObjectURL(lastUrl);
    lastUrl = null;
  }
  downloadEl.classList.add("hidden");
  downloadEl.removeAttribute("href");
}

/** The part's declared family, loaded configuration and divergence, from
 *  whichever piece of state already has them — the drawing panel makes no
 *  request of its own to find out whether a part is configured.
 *
 *  `diverged` comes only from the loaded part detail (the project list carries
 *  no status), which is exactly where it matters: a per-configuration sheet is
 *  the configuration resolved PURELY, so the parameters typed over it are not
 *  on the sheet and the title has to say so. */
function configOf(partId) {
  let entry = null;
  let diverged = false;
  if (state.part && state.part.id === partId) {
    entry = state.part;
    diverged = !!(state.part.status && state.part.status.diverged);
  } else if (state.project) {
    entry = state.project.parts.find((p) => p.id === partId) || null;
  }
  const declared = (entry && entry.configs) || {};
  return {
    configured: Object.keys(declared).length > 0,
    active: (entry && entry.active_config) || null,
    diverged,
  };
}

export async function previewSvg(project, partId) {
  open();
  const seq = ++previewSeq;
  const stale = () => seq !== previewSeq;
  const { configured, active, diverged } = configOf(partId);
  // The table's columns are the configured parameters, so it has nothing to
  // say about a part with no family: the control simply isn't there.
  if (dimTableWrap) dimTableWrap.classList.toggle("hidden", !configured);
  if (dimTableEl) dimTableEl.checked = dimTable && configured;
  const wantTable = configured && dimTable;
  previewing = { project, partId };
  // A configuration sheet is the configuration AS DECLARED; when the working
  // state has been typed over, the title says so rather than letting the sheet
  // be read as "what is on screen".
  titleEl.textContent = active
    ? `${partId}@${active} · drawing${
        diverged ? " (configuration as declared — your edits are not shown)" : ""
      }`
    : `${partId} · drawing`;
  downloadEl.classList.add("hidden");
  viewEl.textContent = "";
  const status = document.createElement("div");
  status.className = "drawing-status";
  status.textContent = "Generating drawing…";
  viewEl.appendChild(status);

  try {
    // POST regenerates the file server-side; the GET streams the SVG bytes.
    // The tool route returns {error:...} at HTTP 200 on failure.
    const gen = await api.generateDrawing(project, partId, {
      format: "svg",
      config: active || undefined,
      dim_table: wantTable,
    });
    if (stale()) return;
    if (gen && gen.error) {
      showError(gen.error.message || "drawing failed");
      return;
    }
    // The GET regenerates too, so it carries the same two arguments — asking
    // for the suffixed file without them would answer a sheet the POST did
    // not write.
    const res = await fetch(
      api.drawingSvgUrl(project, partId, {
        config: active || null,
        dim_table: wantTable || null,
      })
    );
    if (stale()) return;
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
    if (stale()) return;
    const blob = new Blob([svgText], { type: "image/svg+xml" });
    // The previous sheet's object URL is about to become unreachable; revoke
    // it here or a session of toggling the checkbox leaks one blob per render.
    if (lastUrl) URL.revokeObjectURL(lastUrl);
    lastUrl = URL.createObjectURL(blob);
    const img = document.createElement("img");
    img.className = "drawing-img";
    img.alt = active
      ? `${partId} engineering drawing, configuration ${active}`
      : `${partId} engineering drawing`;
    img.src = lastUrl;
    viewEl.textContent = "";
    viewEl.appendChild(img);
    downloadEl.href = lastUrl;
    // The same suffix the server wrote: a configuration's sheet lands beside
    // the base one rather than over it.
    downloadEl.download = `${partId}${active ? `_${active}` : ""}_drawing.svg`;
    downloadEl.textContent = "Download SVG";
    downloadEl.classList.remove("hidden");
  } catch (err) {
    if (stale()) return;
    showError(err instanceof ApiError ? err.error.message : String(err));
  }
}

export async function saveDxf(project, partId) {
  const { active } = configOf(partId);
  try {
    // No `dim_table` here: DXF is a geometry exchange format and the server
    // ignores the flag for it, so sending it would only imply otherwise.
    const result = await api.generateDrawing(project, partId, {
      format: "dxf",
      config: active || undefined,
    });
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
