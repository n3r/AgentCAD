"""Helpers for importing external CAD files as reference parts."""

from __future__ import annotations

import shutil
from pathlib import Path

from .model import ValidationError

# CAD geometry formats plus the PRD-018 intake formats (images + PDF). The
# image/PDF entries only make those files *uploadable* through the same
# traversal-safe `ingest_file` path — they are consumed by `core/intake.py`
# (vision), never built as reference parts. `.pdf` is always in the set so a
# PDF can be uploaded, but `intake.prepare_vision` gates on the optional
# `[pdf]` extra at USE time (the FEM gating idiom): absent pypdfium2 the upload
# succeeds and the vision step answers a `[pdf]`-extra validation_error.
SUPPORTED_EXTS = {
    ".step", ".stp", ".brep", ".stl",  # CAD geometry
    ".png", ".jpg", ".jpeg",           # PRD-018 vision intake (images)
    ".pdf",                            # PRD-018 vision intake (extra-gated use)
}
MAX_IMPORT_BYTES = 100 * 1024 * 1024  # 100 MB


def safe_import_name(filename: str) -> str:
    """Reduce a user filename to a safe basename with a supported extension."""
    # Reduce to the final path component — this is the security boundary: a
    # basename cannot traverse out of the imports/ dir regardless of input.
    name = Path(filename.replace("\\", "/")).name
    if not name or name.startswith("."):
        raise ValidationError(f"invalid import filename {filename!r}")
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ValidationError(
            f"unsupported CAD format {ext!r}",
            {"supported": sorted(SUPPORTED_EXTS)},
        )
    return name


def ingest_file(store, proj: str, filename: str, src_path: str) -> str:
    """Copy an external file into the project's imports/ dir; return the
    stored basename (used as a reference part's ``source``)."""
    name = safe_import_name(filename)
    src = Path(src_path)
    if not src.is_file():
        raise ValidationError(f"source file not found: {src_path}")
    if src.stat().st_size > MAX_IMPORT_BYTES:
        raise ValidationError("import exceeds the 100 MB limit")
    dest = store.imports_dir(proj, write=True) / name
    shutil.copyfile(src, dest)
    return name
