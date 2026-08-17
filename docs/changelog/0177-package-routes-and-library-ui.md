# 0177 — 2026-08-16 — PRD-011 slice 11: the route pack and the Library dialog

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The registry becomes something a human can use. `routes_packages.py` is five
registry passthroughs plus a preview server; `frontend/js/library.js` is the
Parts library dialog — search, a preview, the disclosure badge, the declared
parameter table, a preset picker and one "Add to project" that installs the
dependency and then materialises the part. **AC7 is won here**, in a real
Chrome session against the real bundled catalog: search "cap screw", insert the
`m5x16` preset, see it in the tree and the viewport, **zero console errors and
zero page errors**.

## Changes

- `agentcad/server/routes_packages.py` (new)
  - `GET /api/packages/search`, `GET|POST /api/projects/{p}/packages`,
    `DELETE /api/projects/{p}/packages/{name}`,
    `POST /api/projects/{p}/packages/{name}/use`, and
    `GET /api/packages/{name}/versions/{version}/preview`.
  - Whitelisted, null-stripped body keys — never `**body`; `_RAISE` maps the
    house three error types to 404/422/409. **`_BODY_ERRORS` is empty**, and
    the module docstring says why: a check report is evidence even when it is
    red, but every package failure is a *refusal* with nothing to render.
  - The preview route resolves the entry-supplied path with
    `content.resolve_within` **and** requires a `.png`, because the path is
    caller data that came back to us from a search hit.
- `frontend/index.html` — a `Library` toolbar button and the `#library-modal`
  dialog (proposals/versions shape), with the security non-claim as **visible
  footer text**.
- `frontend/js/library.js` (new) — the dialog.
- `frontend/js/api.js` — `searchPackages`, `listPackages`, `addPackage`,
  `removePackage`, `usePackagePart`, `packagePreviewUrl`.
- `frontend/css/app.css` — the `.lib-*` block.
- `frontend/js/main.js` — the import, `library.init(actions)` and
  `setupLibrary()`.
- `tests/test_packages_api.py` (new) — 29 tests.

## Divergences from the plan, and why

- **A sixth route the plan does not list: the preview image.** A search hit
  carries `previews: ["previews/cap_screw_iso.png"]`, and those bytes live in
  the *index*, not in the project and not in the cache — a listing has to
  render before anything is installed. There is no existing route that could
  serve them, so the dialog would have shown a broken image or no image at
  all. It is the one route with a containment rule of its own, and five tests
  attack it (`../`, an absolute path, a non-PNG, an unknown version, a pinned
  index that does not carry it).
- **The gate is not routed, and a test pins the route set.** The plan's
  "Surfaces" list is five routes; `validate_package` is a tool and
  `agentcad package validate` is the CLI. Routing the gate would let a browser
  request start a dozen kernel builds on the shared pool, and `--work-dir`
  cannot be widened from a running server anyway (the seatbelt profile is
  fixed at worker spawn). `test_the_gate_is_not_reachable_over_http` asserts
  the exact set of mounted package paths, so adding one is a deliberate act.
- **The dialog does not reuse `inspector.js`'s param table.** The plan says to.
  The inspector's `buildParamControls` builds *controls that write* — a slider
  bound to `queueParam`, which patches the open part's parameters. The library
  shows a **declared range** from the index digest for a part that does not
  exist in the project yet; there is nothing to write to and no part to write
  it on. Reusing it would have meant a second mode inside a function whose
  every branch ends in a mutation. What is reused is the *type scale*: the
  `.lib-param*` rules sit on the same tokens as `.param-*`, so the two tables
  read as one product.
- **Keyword and standards facets travel as comma-separated strings.** The tool
  takes lists; a query string does not have one shape for that. Repeated
  parameters (`?standards=a&standards=b`) would need a different FastAPI
  signature for one filter, so the route splits on commas and the API client
  joins. Empty segments are dropped, so `?keywords=` is "no filter" rather
  than "match the empty keyword".
- **`add_package` then `use_part`, always two calls.** The dialog could have
  had one route that does both. It must not: a project can legitimately depend
  on a package it has not materialised, and `add_package` is idempotent, so
  the two calls are two facts. `test_the_library_module_installs_before_it_
  materialises` pins the order.
- **No native `<dialog>`.** PRD-026 has not landed; this is the sixth modal in
  the app and it wears the shape the other five wear. The note is in the
  markup so PRD-026 finds it.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_api.py
29 passed in 4.26s
```

The suites a new route pack and a new frontend module could have broken:

```
.venv/bin/python -m pytest -q tests/test_packages_api.py tests/test_catalog.py \
    tests/test_packages_tools.py tests/test_packages_index.py tests/test_server.py
155 passed in 23.95s
```

### AC7 — a real browser session

`agentcad serve --port 8630 --projects-dir <scratch>/uiprojects --no-open`,
driven with Playwright against the *bundled* catalog (no fixture index, no
config file entry):

1. open the project → 2. click **Library** → 3. type `cap screw` → the hit
list shows `iso4762 1.0.0` with its preview thumbnail and
`keyword:cap screw` as the `why` → 4. the detail pane shows the preview, the
`disclosure: agent` badge, `Apache-2.0`, `ISO 4762`, `index: agentcad-core`,
and the declared table (`size` M3-0.5…M12-1.75, `length` 5…60 mm, `thread`
cosmetic|real, connectors `head_seat (rigid), axis (cylindrical)`) → 5. pick
the `m5x16` preset → **Add to project**.

Result: the part appears in the tree, the screw renders in the viewport
(`cap_screw_m5x16 · part · 956 tris · ok`), the inspector shows
`size = M5-0.8`, `length = 16` and four green spec chips (`valid`,
`head_diameter_iso4762`, `head_height_iso4762`, `length_under_head`).

```
--- console ---
warning: [.WebGL-…]GL Driver Message (OpenGL, Performance, …): GPU stall due to ReadPixels  ×4
--- page errors ---
ERROR COUNT: 0
```

The four warnings are SwiftShader's, not the app's: headless Chrome on this
machine has no GPU and `viewport.init()` aborts boot without a WebGL context,
so the session runs with `--use-gl=angle --use-angle=swiftshader`. That is an
environment fact and it is recorded rather than filtered, because a run that
hides its warnings cannot claim zero errors.

**The catalog held one package when this session ran.** Slice 12 adds eight
more, which changes the listing this dialog renders, so AC7 is driven again
there against all nine — nine hits, nine decoded thumbnails, two packages
materialised into one project, still zero errors (changelog 0178).

## Notes

- **What the route tests attack:** an unresolvable name (404), an unknown
  project (404), a `part_id` that already exists (409), a missing preset
  (404), `use_part` for a package that was never added (refused, fail-closed),
  a body that is not an object (422), a `null` value (reads as omitted), an
  unknown key (not forwarded — the registry would reject the whole call), and
  the assertion that **no** route ever answers 200 with an `error` key.
- **Every search response is scoped to the request that asked for it.** Typing
  outruns a search; an out-of-order answer would render the wrong result set.
  Same rule PRD-009 applies to `sketch_plane`, same shape (a monotonic token
  re-checked on arrival), and a test reads the source for it.
- **The non-claim is visible text, not a tooltip.** The test asserts the
  sentence is in the footer *and* that the element carries no `title=`: a
  claim nobody can read is a claim nobody made.
- The install affordance disables itself while the two calls run and reports
  the server's own message on failure, rather than a toast that outlives the
  dialog.
