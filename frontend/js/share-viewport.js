// PRD-007 slice 5: the slim viewer core for the share/embed page.
//
// A share page reuses the SAME WebGL viewport the editor uses (`viewport.js`:
// `parseACM` mesh decoding, `showPart` display, orbit `init`/`fit`), but NONE
// of the editor-only surface — no `TransformControls` gizmo, no CodeMirror, no
// inspector. This module is the explicit reuse seam: it re-exports only what a
// read/customize page needs, so the bundle a stranger downloads stays small and
// the reuse is one import a test can assert (design "Surfaces", Decision 7).
export {
  init,
  showPart,
  fit,
  onFrame,
  setTheme,
  clear,
  hasContent,
  parseACM,
} from "./viewport.js";
