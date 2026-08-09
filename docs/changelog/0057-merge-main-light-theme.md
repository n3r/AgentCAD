# 0057 — Merge main (light theme + CI) into the v3 branch

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Brings `origin/main` — the merged light-ui PR #1 (token-driven light/dark
theming, theme switcher, and an ubuntu-only CI workflow) — into the v3
`roadmap` branch ahead of its own PR, reconciling the two branches' parallel
work.

## Changes

- Merge of `origin/main` (e1bdc4e). One textual conflict: both branches
  added an import in `frontend/js/main.js` (v3's `sketcher`, light-ui's
  `theme`) — both kept. Everything else auto-merged: `index.html` carries
  both the theme button and the v3 Sketch button/facecard/sketcher hosts;
  `app.css` combines the light-theme token overhaul with the v3 control
  styles; `viewport.js` keeps `setTheme` alongside the v3 exports
  (`setInstanceTransform`, face maps, `highlightFace`).
- `.github/workflows/test.yml` (light-ui's ubuntu-only suite run) removed —
  superseded by v3's `ci.yml` three-OS matrix, which includes the same
  ubuntu job; keeping both would run the suite twice per push.
- Changelog sequence is now strictly increasing across both branches
  (light-ui's 0034/0035 + v3's renumbered 0036–0056, see 0056).

## Files

- `frontend/js/main.js` — import conflict resolution
- `.github/workflows/test.yml` — removed (superseded by `ci.yml`)
- plus the merged light-ui content (theme.js, app.css tokens, index.html
  button, viewport palette, user-guide theme section, changelogs 0034/0035)

## Notes

The v3 controls added before this merge (sketcher overlay, face card, lock
chip, motion row) were styled against the dark palette; they render with
their own explicit colors in both themes. A follow-up could bind them to the
theme tokens for full light-mode polish — tracked as a residual, not a
regression (they are new surfaces, not restyled ones).
