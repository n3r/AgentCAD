# 0360 — PRD-018 slice 3: intake (image + PDF via pypdfium2)

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
Image and PDF intake for the generation loop (FR1): `core/intake.py`
prepares uploads into vision-ready image blocks + fenced reference text.

## Changes
- `core/imports.py`: `SUPPORTED_EXTS` gains `.png/.jpg/.jpeg/.pdf` (100 MB
  guard + `safe_import_name` traversal defense unchanged; a PDF uploads
  via the traversal-safe path, vision-gates at use time).
- `core/intake.py`: `pdf_available()` (the `fem_available` probe twin),
  `prepare_vision(paths, max_pages=8, max_text_chars=20000)` →
  `[{png_base64, media_type, source_name, kind, page?, text?}]` — images
  pass through under the `png_base64` key (rides chat's image-block
  re-entry; no re-encode, no Pillow) with an honest `media_type`; PDFs
  (gated on the `[pdf]` extra, else `validation_error` naming it)
  rasterize each page (bounded) via pypdfium2 → `core/render.encode_png`
  and extract native text (bounded). All bounds → `validation_error`,
  never exceptions.
- **Security invariant:** `fence_document_text()` + `DOCUMENT_TEXT_IS_DATA`
  wrap extracted text as explicit reference DATA, never instructions —
  the anti-prompt-injection fence, centralized and tested here.
- `pyproject.toml`: `pdf = ["pypdfium2>=4"]` extra.

## Files
- `core/intake.py`, `tests/test_intake.py` (16 tests) — new
- `core/imports.py`, `pyproject.toml` — extended

## Notes
For S4: `chat._render_tool_result` hardcodes `image/png`, so the
image-block consumer must read `media_type` from the intake result for a
JPEG upload (intake labels honestly, never mislabels). pypdfium2 renders
to numpy (150 dpi, capped) → `encode_png` — the repo's dependency-free
PNG path. `make test` — 7192 passed, 51 skipped (14:29); the non-passing were the count-guards reading the pre-commit newest changelog (this commit adds the count), one PRD-017 AC7 set-equality assertion updated to a subset check (S3's intake extensions are a legitimate addition; the guard still refuses an unsupported ext), and the documented supervisor/navigation load flakes + prd028 FEM timeout (34/34 pass in isolation).
