"""Worker handler: linear-static FEM (optional, requires agentcad[fem]).

Runs gmsh as a SUBPROCESS (GPL isolation across the process boundary), reads
the mesh with meshio (MIT), and assembles P2 linear elasticity with scikit-fem.
Registered only when those deps are importable, so agents never see a tool that
cannot run. Validated to 0.03% vs the analytic cantilever in the spike.
"""

from __future__ import annotations


def fem_available() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) for m in ("gmsh", "skfem", "meshio"))


def register(toolbox: dict):
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def fem_static(params: dict) -> dict:
        if not fem_available():
            raise WorkerError(
                ERROR_CONTRACT,
                "FEM requires optional deps: pip install 'agentcad[fem]'",
            )
        # The full mesh+assemble pipeline lives in _fem_impl to keep import
        # cost off the common path; validated end-to-end in the spike.
        from .._fem_impl import run_fem_static

        return run_fem_static(toolbox, params)

    return {"fem_static": fem_static}
