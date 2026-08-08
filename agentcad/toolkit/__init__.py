"""AgentCAD part-authoring toolkit.

Blessed helpers importable from part scripts (which run in the app venv) to
write robust, non-trivial geometry:

    from agentcad.toolkit import safe_fillet, safe_shell, safe_bool  # robustness
    from agentcad.toolkit import sketch                               # constraint solver
    from agentcad.toolkit import threads                              # bd_warehouse fasteners

Submodules are importable directly; the convenience names below are re-exported
lazily so importing the package never hard-fails if one submodule is mid-build.
"""

from __future__ import annotations

__all__ = ["safe_fillet", "safe_shell", "safe_bool", "sketch", "threads"]


def __getattr__(name: str):
    if name in ("safe_fillet", "safe_shell", "safe_bool"):
        module = {"safe_fillet": "fillet", "safe_shell": "shell",
                  "safe_bool": "boolean"}[name]
        import importlib
        return getattr(importlib.import_module(f".{module}", __name__), name)
    if name in ("sketch", "threads"):
        import importlib
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
