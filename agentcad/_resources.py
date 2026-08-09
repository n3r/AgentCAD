"""Frozen-aware resolution of bundled resource directories.

``frontend/`` and ``examples/`` live at the repo root during development. In
a PyInstaller onedir bundle they are shipped as data files under the
extraction root (``sys._MEIPASS`` — for onedir builds that is the
``_internal/`` directory next to the executable). Everything that needs one
of those directories resolves it through :func:`resource_root` so the two
layouts stay in one place.
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Directory that contains ``frontend/`` and ``examples/``.

    Unfrozen: the repo root (parent of the ``agentcad`` package). Frozen:
    PyInstaller's bundle root ``sys._MEIPASS`` (falling back to the
    executable's directory if a non-PyInstaller freezer ever sets
    ``sys.frozen`` without it).
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
