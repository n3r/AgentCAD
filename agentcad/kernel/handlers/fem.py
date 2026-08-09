"""Worker handlers: FEM tiers (optional, requires agentcad[fem]).

Runs gmsh as a SUBPROCESS (GPL isolation across the process boundary), reads
the mesh with meshio (MIT), and assembles P2 elements with scikit-fem. Three
methods share that meshing path: fem_static (linear elasticity, validated to
0.03% vs the analytic cantilever in the spike), fem_modal (natural
frequencies, consistent mass), and fem_thermal (steady-state conduction).
Registered only when those deps are importable, so agents never see a tool
that cannot run.
"""

from __future__ import annotations


def fem_available() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) for m in ("gmsh", "skfem", "meshio"))


def register(toolbox: dict):
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def _guard():
        if not fem_available():
            raise WorkerError(
                ERROR_CONTRACT,
                "FEM requires optional deps: pip install 'agentcad[fem]'",
            )

    # The full mesh+assemble pipelines live in _fem_impl to keep import cost
    # off the common path; fem_static is validated end-to-end in the spike and
    # fem_modal/fem_thermal reuse its exact meshing path.

    def fem_static(params: dict) -> dict:
        _guard()
        from .._fem_impl import run_fem_static

        return run_fem_static(toolbox, params)

    def fem_modal(params: dict) -> dict:
        _guard()
        from .._fem_impl import run_fem_modal

        return run_fem_modal(toolbox, params)

    def fem_thermal(params: dict) -> dict:
        _guard()
        from .._fem_impl import run_fem_thermal

        return run_fem_thermal(toolbox, params)

    return {"fem_static": fem_static, "fem_modal": fem_modal,
            "fem_thermal": fem_thermal}
