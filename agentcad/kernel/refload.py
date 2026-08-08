"""Reference-CAD loading (STEP / BREP / STL) with a content-addressed LRU.

Imported vendor geometry is expensive to parse (~0.3 s/MB for unique STEP), so
loaded shapes are cached in-process keyed by (realpath, mtime_ns, size) — a
re-import only happens when the file actually changes. Used by both the worker
core (polymorphic assembly/interference items) and the ``reference`` handler
pack. Only the kernel worker process imports this module (it imports OCP).

STL loads as a triangulation-only Face: it can be tessellated and measured
(volume from the mesh) but MUST NOT take part in booleans — cut/intersect on a
mesh Face segfaults OCCT. ``load_reference`` marks the kind so callers can
refuse boolean participation.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

import build123d as b3d

_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_CACHE_MAX = 8

# Extensions that yield a real B-rep (booleans/exact metrics OK) vs mesh-only.
_BREP_EXTS = {".step", ".stp", ".brep"}
_MESH_EXTS = {".stl"}
SUPPORTED_EXTS = _BREP_EXTS | _MESH_EXTS


class ReferenceError(Exception):
    """Unsupported/unreadable reference file."""


def _cache_key(path: Path) -> tuple:
    st = path.stat()
    return (os.path.realpath(path), st.st_mtime_ns, st.st_size)


def load_reference(source_path: str) -> tuple[object, str]:
    """Load a reference file; return (build123d shape, kind) where kind is
    "solid" (STEP/BREP) or "mesh" (STL). Cached by file identity."""
    path = Path(source_path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ReferenceError(
            f"unsupported reference format {ext!r}; supported: "
            f"{', '.join(sorted(SUPPORTED_EXTS))}"
        )
    if not path.is_file():
        raise ReferenceError(f"reference file not found: {source_path}")

    key = _cache_key(path)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]

    if ext in (".step", ".stp"):
        shape, kind = b3d.import_step(str(path)), "solid"
    elif ext == ".brep":
        shape, kind = b3d.import_brep(str(path)), "solid"
    else:  # .stl
        shape, kind = b3d.import_stl(str(path)), "mesh"

    _CACHE[key] = (shape, kind)
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return shape, kind


def is_mesh_kind(source_path: str) -> bool:
    return Path(source_path).suffix.lower() in _MESH_EXTS
