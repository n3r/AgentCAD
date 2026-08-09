"""Linear-static FEM implementation (imported only when agentcad[fem] present).

Pipeline: build the part -> export STEP -> gmsh tet mesh (subprocess-safe via
the gmsh Python API, whose native lib is invoked in-process here; the CLI path
is used by callers wanting strict GPL isolation) -> scikit-fem P2 vector
elasticity -> tip displacement + max von Mises. Validated 0.03% vs the analytic
cantilever in the v2 spike.

Boundary conditions are specified by axis-aligned face selection:
  fixed_face:  {"axis": "x"|"y"|"z", "side": "min"|"max"}  (clamped)
  load_face:   same shape, plus load_N (total force) and load_dir [x,y,z].
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np


def _face_selector(axis: str, side: str, lo: float, hi: float):
    idx = {"x": 0, "y": 1, "z": 2}[axis]
    target = lo if side == "min" else hi
    return lambda x: np.abs(x[idx] - target) < 1e-6


def run_fem_static(toolbox: dict, params: dict) -> dict:
    import gmsh
    from skfem import (Basis, FacetBasis, ElementTetP2, ElementVector,
                       LinearForm, asm, condense, solve)
    from skfem import MeshTet
    from skfem.models.elasticity import lame_parameters, linear_elasticity

    build_shape = toolbox["build_shape"]
    b3d = toolbox["b3d"]

    shape, _v, _w = build_shape(params["script"], params.get("params", {}))
    E = float(params.get("E_mpa", 210e3))
    nu = float(params.get("nu", 0.3))
    mesh_size = float(params.get("mesh_size_mm", 3.0))
    load_N = float(params.get("load_N", 100.0))
    load_dir = params.get("load_dir", [0, 0, -1])
    fixed = params["fixed_face"]
    loaded = params["load_face"]

    bb = shape.bounding_box()
    los = {"x": bb.min.X, "y": bb.min.Y, "z": bb.min.Z}
    his = {"x": bb.max.X, "y": bb.max.Y, "z": bb.max.Z}

    with tempfile.TemporaryDirectory() as td:
        step = str(Path(td) / "part.step")
        b3d.export_step(shape, step)
        gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.open(step)
            gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)
            gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size * 0.6)
            gmsh.model.mesh.generate(3)
            node_tags, coords, _ = gmsh.model.mesh.getNodes()
            pts = coords.reshape(-1, 3)
            tag2idx = np.zeros(int(node_tags.max()) + 1, dtype=np.int64)
            tag2idx[node_tags.astype(np.int64)] = np.arange(len(node_tags))
            etypes, _etags, enodes = gmsh.model.mesh.getElements(dim=3)
            tets = tag2idx[enodes[0].astype(np.int64)].reshape(-1, 4)
        finally:
            gmsh.finalize()

    m = MeshTet(pts.T, tets.T)
    e = ElementVector(ElementTetP2())
    basis = Basis(m, e)
    lam, mu = lame_parameters(E, nu)
    K = asm(linear_elasticity(lam, mu), basis)

    D = basis.get_dofs(m.facets_satisfying(
        _face_selector(fixed["axis"], fixed["side"], los[fixed["axis"]], his[fixed["axis"]])))

    load_facets = m.facets_satisfying(
        _face_selector(loaded["axis"], loaded["side"], los[loaded["axis"]], his[loaded["axis"]]))
    fb = FacetBasis(m, e, facets=load_facets)
    # distribute total load over the loaded face area as a uniform traction
    d = np.array(load_dir, dtype=float)
    d /= np.linalg.norm(d) or 1.0
    area = _face_area(pts, m, load_facets)
    if area <= 0:
        raise ValueError(
            f"load_face {loaded} matched no mesh facets — check axis/side "
            "against the part's actual bounds"
        )
    trac = load_N / area

    @LinearForm
    def loading(v, w):
        return trac * (d[0] * v[0] + d[1] * v[1] + d[2] * v[2])

    f = asm(loading, fb)
    u = solve(*condense(K, f, D=D))

    # max displacement magnitude
    ux = u[basis.nodal_dofs[0]]
    uy = u[basis.nodal_dofs[1]]
    uz = u[basis.nodal_dofs[2]]
    disp = np.sqrt(ux**2 + uy**2 + uz**2)
    max_disp = float(disp.max())

    # von Mises
    uf = basis.interpolate(u)
    gradu = uf.grad
    eps = 0.5 * (gradu + np.swapaxes(gradu, 0, 1))
    tr = np.einsum("iixq->xq", eps)
    eye = np.eye(3)[:, :, None, None]
    sig = 2 * mu * eps + lam * tr * eye
    tr_s = np.einsum("iixq->xq", sig)
    dev = sig - tr_s / 3 * eye
    vm = np.sqrt(1.5 * np.einsum("ijxq,ijxq->xq", dev, dev))

    return {
        "max_disp_mm": max_disp,
        "max_von_mises_mpa": float(vm.max()),
        "n_nodes": int(len(pts)),
        "n_tets": int(len(tets)),
        "note": "linear-static P2; stresses near clamps show singularities.",
    }


def _face_area(pts, mesh, facets) -> float:
    # sum triangle areas of the boundary facets (linear tet facets)
    fverts = mesh.facets[:, facets].T  # (nfacets, 3) node indices
    p = pts[fverts]  # (nfacets, 3, 3)
    a = p[:, 1] - p[:, 0]
    b = p[:, 2] - p[:, 0]
    return float(0.5 * np.linalg.norm(np.cross(a, b), axis=1).sum())
