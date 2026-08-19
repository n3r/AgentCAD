# 0267 — 2026-08-19 — PRD-014 review fixes: honest 422s on the drawing surface

## Summary

An adversarial code review of PRD-014 returned **SHIP, no HIGH defects** — the
headline guarantees held under probing (PDF byte-determinism verified across two
processes with different `PYTHONHASHSEED`; the PDF is `pdfinfo`-valid with correct
xref/`/Length`; section wire tracing is faithful on a multi-body/holed part; the
display-list refactor preserved existing SVG output). This entry closes the two
MED validation gaps + one LOW it did find.

## Fixes

- **MED-1 — an unknown `views` value was a 502, not a 422** (`tools_drawing.py`).
  `generate_drawing` validated `format`/`sheet`/`scale`/`sections`/`details` but
  not view names; an unknown name reached `_VIEW_DIRS[name]` in the worker as a
  bare `KeyError`, which `_dispatch` wraps as `ERROR_KERNEL` and the route answers
  **502** (a retryable server fault) — for a bad request. New `_validate_views`
  gates it as a `ValidationError` (422), like every other spec.
- **MED-2 — `_json_query` let `RecursionError` escape as a 500**
  (`routes_drawing.py`). The GET routes parse `sections`/`details` as JSON from a
  query string; `json.loads` on a deeply-nested value raises **`RecursionError`**,
  which is *not* a `ValueError` (the CLAUDE.md packages trap re-appearing), so it
  slipped the `except (ValueError, TypeError)` and became a FastAPI 500 — breaking
  the parser's documented "422 not 500" promise. Now it catches `RecursionError`
  **and** refuses a raw string over `_MAX_JSON_QUERY` (16 KiB) *before* parsing,
  so the recursion never starts.
- **LOW — no cap on `sections`/`details` count** (`tools_drawing.py`). Each
  section is one `b3d.section` per solid and scales the request timeout
  (`120 + 30·len`), so an unbounded valid list is a self-inflicted slow build.
  `_validate_sections`/`_validate_details` now cap at `_MAX_EXTRA_VIEWS` (26,
  where the A–Z section lettering also stops being clean).

## Tests

`tests/test_drawings_sections.py` grows three regression tests: an unknown view is
a 422 (tool result + HTTP), a deeply-nested `sections` JSON is a 422 not a 500,
and a 40-section request is refused.

## Notes

Non-defects the reviewer noted and left: `_drawing_version` handles unborn/
no-repo and a non-dict `version` override without crashing; table float cells use
`:.2f`/`round(...,3)` (deterministic, locale-free, consistent within `_cell`) —
not a determinism defect. The SHIP verdict stands; these are hardening, no
behavior change for valid input.

`make test` — **4514 passed, 32 skipped** (clean run; the full suite measured
4505 with the 9 `*_cites_a_make_test_count` guards reading this entry's own count
before it was filled — green once it lands; +3 review-fix regression tests).
