// 2D engineering drawings. SVG opens an in-app preview on a white sheet (the
// drawing is meant for print, so it keeps its own black/blue ink regardless of
// the app theme); DXF is written to the project's exports/ and reported via a
// toast. PDF downloads the same way DXF does, but as bytes (no in-app
// preview — PDF has no place to render inline). Both SVG/PDF and DXF are
// script-part only — the server rejects reference parts.
//
// PRD-014: `sheet`/`views`/`sections` ride every request. The POST
// (`make_drawing`) forwards them from the body and the GET preview routes
// (`get_drawing_svg`/`get_drawing_pdf`) accept them as query params — `views`
// comma-separated, `sections` as JSON — so the sheet-format select, the view
// checkboxes, and the section control all take visible effect on the preview
// (the GET step is what re-renders the bytes the browser shows).

import { api, ApiError } from "./api.js";
import { state } from "./state.js";
import * as dialogs from "./shell/dialogs.js";

let actions = null;
let overlay, titleEl, viewEl, downloadEl, downloadPdfEl, closeBtn;
let dimTableEl, dimTableWrap;
let sheetEl;
let viewEls = {};
let sectionToggleEl, sectionPanelEl, sectionPlaneEl, sectionOffsetEl;
let sectionApplyEl, sectionClearEl;
let lastUrl = null; // object URL to revoke when the modal closes
// FR8's dimension table, remembered for the session: it is a property of how
// you want to read the sheet, not of the part, so re-previewing another
// configuration keeps it on.
let dimTable = false;
// Sheet format (FR1) and the four projected views (FR1/Experience), likewise
// remembered for the session rather than reset per part or per open.
const DEFAULT_SHEET = "iso_a3";
const ALL_VIEWS = ["top", "front", "right", "iso"];
let sheet = DEFAULT_SHEET;
let viewChecks = { top: true, front: true, right: true, iso: true };
// A single section plane (FR6), v1 scope per the design doc ("one section is
// fine for v1"). `null` means no section requested.
let section = null; // {plane, offset_mm}
let previewing = null; // {project, partId} — what every control re-renders
// Two drawing generations are two multi-second kernel round trips, and
// toggling a control starts a second one while the first is still in
// flight. Only the newest may touch the modal — an older response landing
// after it would paint the sheet the user just moved away from. Every new
// control below reuses this same guard.
let previewSeq = 0;
let legacy = null;   // the overlay's seat on the shell's dialog stack

export function init(a) {
  actions = a;
  overlay = document.getElementById("drawing-modal");
  titleEl = document.getElementById("drawing-title");
  viewEl = document.getElementById("drawing-view");
  downloadEl = document.getElementById("drawing-download");
  downloadPdfEl = document.getElementById("drawing-download-pdf");
  closeBtn = document.getElementById("drawing-close");
  dimTableEl = document.getElementById("drawing-dimtable");
  dimTableWrap = document.getElementById("drawing-dimtable-wrap");
  sheetEl = document.getElementById("drawing-sheet");
  viewEls = {
    top: document.getElementById("drawing-view-top"),
    front: document.getElementById("drawing-view-front"),
    right: document.getElementById("drawing-view-right"),
    iso: document.getElementById("drawing-view-iso"),
  };
  sectionToggleEl = document.getElementById("drawing-section-toggle");
  sectionPanelEl = document.getElementById("drawing-section-panel");
  sectionPlaneEl = document.getElementById("drawing-section-plane");
  sectionOffsetEl = document.getElementById("drawing-section-offset");
  sectionApplyEl = document.getElementById("drawing-section-apply");
  sectionClearEl = document.getElementById("drawing-section-clear");

  if (dimTableEl) {
    dimTableEl.addEventListener("change", () => {
      dimTable = dimTableEl.checked;
      if (previewing) previewSvg(previewing.project, previewing.partId);
    });
  }
  if (sheetEl) {
    sheetEl.value = sheet;
    sheetEl.addEventListener("change", () => {
      sheet = sheetEl.value;
      if (previewing) previewSvg(previewing.project, previewing.partId);
    });
  }
  Object.entries(viewEls).forEach(([name, el]) => {
    if (!el) return;
    el.checked = viewChecks[name];
    el.addEventListener("change", () => {
      viewChecks[name] = el.checked;
      if (previewing) previewSvg(previewing.project, previewing.partId);
    });
  });
  if (sectionToggleEl && sectionPanelEl) {
    sectionToggleEl.addEventListener("click", () => {
      sectionPanelEl.classList.toggle("hidden");
    });
  }
  if (sectionApplyEl) {
    sectionApplyEl.addEventListener("click", () => {
      const plane = sectionPlaneEl ? sectionPlaneEl.value : "xy";
      const offset = sectionOffsetEl ? parseFloat(sectionOffsetEl.value) : 0;
      section = { plane, offset_mm: Number.isFinite(offset) ? offset : 0 };
      if (sectionClearEl) sectionClearEl.classList.remove("hidden");
      if (previewing) previewSvg(previewing.project, previewing.partId);
    });
  }
  if (sectionClearEl) {
    sectionClearEl.addEventListener("click", () => {
      section = null;
      sectionClearEl.classList.add("hidden");
      if (previewing) previewSvg(previewing.project, previewing.partId);
    });
  }
  if (downloadPdfEl) {
    downloadPdfEl.addEventListener("click", () => {
      if (previewing) savePdf(previewing.project, previewing.partId);
    });
  }
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  // PRD-026 FR2: Esc belongs to the shell's overlay stack, not to this module.
  legacy = dialogs.attachLegacy(overlay, {
    view: "drawing", title: "Drawing preview…", onClose: close,
    description: "A 2D drawing of the selected part",
    isOpen: () => !overlay.classList.contains("hidden"),
    open: (args) => previewSvg((args && args.project) || state.projectName,
                               (args && args.part) || state.selectedPart),
    when: (c) => !!c.selectedPart,
  });
}

/** The regenerate-driving controls (sheet/views/section), normalized to what
 *  `generate_drawing` expects: `views` is omitted when every view is checked
 *  or none is — "empty selection = all" (Experience note) rather than a
 *  request nothing could draw — and `sections` is a one-element array (or
 *  omitted) since v1 keeps a single section. `sheet` is always sent; it is
 *  harmless to send the default explicitly. */
function drawingArgs() {
  const chosen = ALL_VIEWS.filter((v) => viewChecks[v]);
  const viewsVal =
    chosen.length && chosen.length < ALL_VIEWS.length ? chosen : undefined;
  return { sheetVal: sheet, viewsVal, sectionsVal: section ? [section] : undefined };
}

function open() {
  overlay.classList.remove("hidden");
  if (legacy) legacy.notifyOpen();
}

function close() {
  overlay.classList.add("hidden");
  if (legacy) legacy.notifyClose();   // idempotent: Esc pops the stack itself
  viewEl.textContent = "";
  previewing = null;
  previewSeq++;             // orphan any preview still in flight
  if (lastUrl) {
    URL.revokeObjectURL(lastUrl);
    lastUrl = null;
  }
  downloadEl.classList.add("hidden");
  downloadEl.removeAttribute("href");
  if (downloadPdfEl) downloadPdfEl.classList.add("hidden");
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
  const { sheetVal, viewsVal, sectionsVal } = drawingArgs();
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
  if (downloadPdfEl) downloadPdfEl.classList.add("hidden");
  viewEl.textContent = "";
  const status = document.createElement("div");
  status.className = "drawing-status";
  status.textContent = "Generating drawing…";
  viewEl.appendChild(status);

  try {
    // POST regenerates the file server-side; the GET streams the SVG bytes.
    // The POST raises like every other pack route (a refusal is a 4xx, a
    // kernel failure a 502); the `catch` below is the error path.
    await api.generateDrawing(project, partId, {
      format: "svg",
      config: active || undefined,
      dim_table: wantTable,
      sheet: sheetVal,
      views: viewsVal,
      sections: sectionsVal,
    });
    if (stale()) return;
    // The GET regenerates too, so it carries the same arguments — asking for
    // the suffixed file without them would answer a sheet the POST did not
    // write. `views` is comma-separated and `sections` is JSON, matching the
    // route's query parameters.
    const res = await fetch(
      api.drawingSvgUrl(project, partId, {
        config: active || null,
        dim_table: wantTable || null,
        sheet: sheetVal,
        views: viewsVal ? viewsVal.join(",") : null,
        sections: sectionsVal ? JSON.stringify(sectionsVal) : null,
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
    // PDF is a separate on-demand download (its own POST + GET, below) rather
    // than something this SVG preview fetched — just reveal the button now
    // that we know the part/config is drawable.
    if (downloadPdfEl) downloadPdfEl.classList.remove("hidden");
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
    // The POST raises like every other pack route; the `catch` is the error
    // path (an `ApiError` for a 4xx refusal or a 502 kernel failure alike).
    const result = await api.generateDrawing(project, partId, {
      format: "dxf",
      config: active || undefined,
    });
    const kb = ((result.size_bytes || 0) / 1024).toFixed(1);
    actions.toast(`Wrote ${result.path} (${kb} KB)`);
  } catch (err) {
    const detail = err instanceof ApiError ? err.error.message : String(err);
    actions.toast(`Drawing failed: ${detail}`, "error");
  }
}

/** Download the PDF twin (FR11) of whatever the SVG preview is currently
 *  showing — same sheet/views/section/config/dim-table request shape as
 *  `previewSvg`, mirroring `saveDxf`'s POST-then-toast error handling but,
 *  like the SVG preview, following the POST with a GET for the actual bytes
 *  (no in-app PDF preview surface to reuse, so this streams straight to a
 *  browser download instead of an `<img>`). */
export async function savePdf(project, partId) {
  const { configured, active } = configOf(partId);
  const wantTable = configured && dimTable;
  const { sheetVal, viewsVal, sectionsVal } = drawingArgs();
  try {
    await api.generateDrawing(project, partId, {
      format: "pdf",
      config: active || undefined,
      dim_table: wantTable,
      sheet: sheetVal,
      views: viewsVal,
      sections: sectionsVal,
    });
    const res = await fetch(
      api.drawingPdfUrl(project, partId, {
        config: active || null,
        dim_table: wantTable || null,
        sheet: sheetVal,
        views: viewsVal ? viewsVal.join(",") : null,
        sections: sectionsVal ? JSON.stringify(sectionsVal) : null,
      })
    );
    const type = res.headers.get("content-type") || "";
    if (!res.ok || !type.includes("pdf")) {
      let msg = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        msg = (body.error && body.error.message) || msg;
      } catch {
        /* keep msg */
      }
      actions.toast(`Drawing failed: ${msg}`, "error");
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${partId}${active ? `_${active}` : ""}_drawing.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    actions.toast(`Downloaded ${a.download} (${(blob.size / 1024).toFixed(1)} KB)`);
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
