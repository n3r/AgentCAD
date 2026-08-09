# 0052 — Direct-manipulation UI: gap closure + real-browser verification

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

The roadmap's "Direct-manipulation UI" audit: the one remaining v2 gap
(Shift-to-snap was advertised by the placement card but never wired) is
fixed, the stale user-guide blockquote claiming on-canvas controls "are not
in this build" is replaced with the current truth, and the whole v3 UI
surface was verified in a real browser (headless Chrome driving the live
app) with screenshots landed in docs/assets.

## Changes

- **Shift-to-snap wired**: keydown/keyup listeners (plus a window-blur
  reset) toggle `viewport.setGizmoSnap` — the 1 mm / 5° snap the placement
  card has promised since v2 now actually engages while Shift is held.
- **user-guide**: the "On-canvas controls are the next wave … not in this
  build" blockquote replaced with the shipped-controls paragraph (gizmo with
  G/R and Shift-snap, transform panel, material dropdown, Import, drawing
  preview, analysis actions, sketcher, push/pull).
- **Browser verification** (headless Chrome + puppeteer-core over the live
  server, 11/11 checks): project switching; typed param controls
  (checkbox/select/text/number) rendering AND a checkbox toggle driving a
  real rebuild (volume 14 949 → 15 552 mm³); per-solid Metrics rows (body /
  lid with independent masses); analysis buttons incl. Curvature; the
  sketcher opening with its constraint palette and solved/DOF status; face
  click → amber highlight + push/pull card (Face 1 · 576 mm² · correct
  normal); Undo button; the 🔒 lock chip appearing when an agent takes the
  turn; the drawing preview modal; and **zero console errors** end to end.
- Screenshots: `docs/assets/ui-typed-params.png`, `ui-sketcher.png`,
  `ui-face-pushpull.png`, `ui-lock-chip.png`.

## Files

- `frontend/js/main.js` — Shift-to-snap wiring
- `docs/user-guide.md` — stale blockquote replaced
- `docs/assets/ui-*.png` — four verification screenshots

## Notes

The interactive pass used puppeteer-core with the system Chrome (the
claude-in-chrome extension was not connected); one genuine environment
finding: `--disable-gpu` breaks WebGL context creation and with it the whole
app boot — the verification script runs without it.
