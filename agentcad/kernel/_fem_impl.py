"""FEM implementations (imported only when agentcad[fem] present).

Pipeline shared by every solver: build the part -> export STEP -> gmsh tet
mesh (subprocess-safe via the gmsh Python API, whose native lib is invoked
in-process here; the CLI path is used by callers wanting strict GPL
isolation) -> scikit-fem P2 elements. The static solver was validated 0.03%
vs the analytic cantilever in the v2 spike; modal and thermal reuse the
exact same meshing path and element order so results stay comparable.

Solvers:
  run_fem_static  — linear elasticity: clamp one face, apply a total-force
                    traction to another; tip displacement + max von Mises.
  run_fem_modal   — natural frequencies from the generalized eigenproblem
                    K phi = w^2 M phi (consistent mass); free-free supported.
  run_fem_thermal — steady-state conduction div(k grad T) = 0 with Dirichlet
                    temperatures on two faces; reports the total heat flow.

Boundary conditions are specified by axis-aligned face selection:
  {"axis": "x"|"y"|"z", "side": "min"|"max"}
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np


def _face_selector(axis: str, side: str, lo: float, hi: float):
    idx = {"x": 0, "y": 1, "z": 2}[axis]
    target = lo if side == "min" else hi
    return lambda x: np.abs(x[idx] - target) < 1e-6


def _bounds(shape) -> tuple[dict, dict]:
    bb = shape.bounding_box()
    los = {"x": bb.min.X, "y": bb.min.Y, "z": bb.min.Z}
    his = {"x": bb.max.X, "y": bb.max.Y, "z": bb.max.Z}
    return los, his


def _gmsh_tet_mesh(b3d, shape, mesh_size: float) -> tuple[np.ndarray, np.ndarray]:
    """STEP -> gmsh tetrahedra: the exact meshing path validated by the
    fem_static spike, shared by all solvers. Returns (points, tets)."""
    import gmsh

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
    return pts, tets


def _face_facets(m, face: dict, los: dict, his: dict, what: str):
    facets = m.facets_satisfying(_face_selector(
        face["axis"], face["side"], los[face["axis"]], his[face["axis"]]))
    if len(facets) == 0:
        raise ValueError(
            f"{what} {face} matched no mesh facets — check axis/side "
            "against the part's actual bounds"
        )
    return facets


def run_fem_static(toolbox: dict, params: dict) -> dict:
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

    los, his = _bounds(shape)
    pts, tets = _gmsh_tet_mesh(b3d, shape, mesh_size)

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


def run_fem_modal(toolbox: dict, params: dict) -> dict:
    from scipy.sparse.linalg import eigsh
    from skfem import (Basis, BilinearForm, ElementTetP2, ElementVector,
                       MeshTet, asm)
    from skfem.helpers import dot
    from skfem.models.elasticity import lame_parameters, linear_elasticity

    build_shape = toolbox["build_shape"]
    b3d = toolbox["b3d"]

    shape, _v, _w = build_shape(params["script"], params.get("params", {}))
    E = float(params.get("E_mpa", 210e3))
    nu = float(params.get("nu", 0.3))
    density = float(params.get("density_g_cm3", 7.85))
    mesh_size = float(params.get("mesh_size_mm", 3.0))
    n_modes = max(1, min(int(params.get("n_modes", 6)), 24))
    fixed = params.get("fixed_face")

    los, his = _bounds(shape)
    pts, tets = _gmsh_tet_mesh(b3d, shape, mesh_size)

    m = MeshTet(pts.T, tets.T)
    basis = Basis(m, ElementVector(ElementTetP2()))
    lam, mu = lame_parameters(E, nu)
    K = asm(linear_elasticity(lam, mu), basis).tocsr()

    # Consistent unit system mm-N-MPa-tonne-s: E in MPa (N/mm^2), lengths in
    # mm, density in tonne/mm^3 (= g/cm^3 * 1e-9, since 1 g/cm^3 =
    # 1e-3 kg / 1e3 mm^3 = 1e-9 t/mm^3). 1 N = 1 t*mm/s^2, so the
    # eigenvalues of K phi = w^2 M phi come out directly in (rad/s)^2.
    rho = density * 1e-9

    @BilinearForm
    def mass(u, v, _):
        return rho * dot(u, v)

    M = asm(mass, basis).tocsr()

    if fixed is not None:
        facets = _face_facets(m, fixed, los, his, "fixed_face")
        free = basis.complement_dofs(basis.get_dofs(facets))
        Kf = K[free][:, free]
        Mf = M[free][:, free]
        k = min(n_modes, Kf.shape[0] - 2)
        # K is nonsingular once clamped: plain sigma=0 shift-invert gives the
        # smallest eigenpairs.
        w2 = eigsh(Kf, k=k, M=Mf, sigma=0, which="LM",
                   return_eigenvectors=False)
        lams = np.sort(np.clip(w2, 0.0, None))
        freqs = np.sqrt(lams) / (2.0 * np.pi)
        note = None
    else:
        # Free-free: K is singular (6 rigid-body modes at w^2 = 0), so
        # factor K - sigma*M at a small NEGATIVE sigma instead: that matrix
        # is positive definite (the null space picks up -sigma*M), and since
        # all eigenvalues are >= 0 > sigma, |lambda - sigma| is monotone in
        # lambda — shift-invert still returns the spectrum from the bottom.
        # sigma is scaled off the diagonals (~ the top eigenvalue scale) so
        # the factorization is well conditioned while |sigma| stays below
        # the first flexible eigenvalue.
        scale = float(K.diagonal().max()) / max(float(M.diagonal().max()),
                                                np.finfo(float).tiny)
        sigma = -1e-6 * scale
        k = min(n_modes + 6, K.shape[0] - 2)
        w2 = eigsh(K, k=k, M=M, sigma=sigma, which="LM",
                   return_eigenvectors=False)
        lams = np.sort(np.clip(w2, 0.0, None))
        # Drop the near-zero rigid-body modes: numerically they sit many
        # orders below the first flexible eigenvalue, so a threshold
        # relative to the largest computed eigenvalue separates them.
        tol = float(lams.max()) * 1e-6
        flexible = lams[lams > tol]
        dropped = len(lams) - len(flexible)
        freqs = np.sqrt(flexible[:n_modes]) / (2.0 * np.pi)
        note = f"free-free: {dropped} rigid-body modes omitted"

    result = {
        "frequencies_hz": [float(f) for f in freqs],
        "n_modes": int(len(freqs)),
        "n_dof": int(K.shape[0]),
        "constrained": fixed is not None,
        "mesh": {"n_nodes": int(len(pts)), "n_tets": int(len(tets))},
    }
    if note is not None:
        result["note"] = note
    return result


def run_fem_thermal(toolbox: dict, params: dict) -> dict:
    from skfem import Basis, BilinearForm, ElementTetP2, MeshTet, asm, condense, solve
    from skfem.helpers import dot, grad

    build_shape = toolbox["build_shape"]
    b3d = toolbox["b3d"]

    shape, _v, _w = build_shape(params["script"], params.get("params", {}))
    k_w_m_k = float(params.get("k_w_m_k", 50.0))
    t_hot = float(params["t_hot_c"])
    t_cold = float(params["t_cold_c"])
    mesh_size = float(params.get("mesh_size_mm", 3.0))
    hot = params["hot_face"]
    cold = params["cold_face"]
    if (hot["axis"], hot["side"]) == (cold["axis"], cold["side"]):
        raise ValueError("hot_face and cold_face must be different faces")

    los, his = _bounds(shape)
    pts, tets = _gmsh_tet_mesh(b3d, shape, mesh_size)

    m = MeshTet(pts.T, tets.T)
    basis = Basis(m, ElementTetP2())

    @BilinearForm
    def conduction(u, v, _):
        return k_w_m_k * dot(grad(u), grad(v))

    K = asm(conduction, basis).tocsr()

    hot_dofs = basis.get_dofs(_face_facets(m, hot, los, his, "hot_face")).flatten()
    cold_dofs = basis.get_dofs(_face_facets(m, cold, los, his, "cold_face")).flatten()

    x = basis.zeros()
    x[hot_dofs] = t_hot
    x[cold_dofs] = t_cold  # on a shared edge (adjacent faces) cold wins
    D = np.unique(np.concatenate([hot_dofs, cold_dofs]))
    T = solve(*condense(K, basis.zeros(), x=x, D=D))

    # Total heat flow through the hot face from the discrete reactions.
    # Units: coordinates are mm while k is W/(m*K), so each stiffness entry
    # ∫ k ∇φi·∇φj dV = [W/(m*K)] * [1/mm] * [1/mm] * [mm^3] = (mm/m) W/K
    # = 1e-3 W/K, and the reaction r = K @ T is in units of 1e-3 W. By
    # Green's identity (with div(k grad T) = 0) r_i = ∮ k (dT/dn) φi ds,
    # and since the P2 nodal basis is a partition of unity on the face,
    # summing the reactions over the hot-face DOFs gives the total flux
    # through that face; positive = heat flowing into the part.
    r = K @ T
    flux_w = float(r[hot_dofs].sum()) * 1e-3

    return {
        "t_min_c": float(T.min()),
        "t_max_c": float(T.max()),
        "flux_w": flux_w,
        "mesh": {"n_nodes": int(len(pts)), "n_tets": int(len(tets))},
    }


def _face_area(pts, mesh, facets) -> float:
    # sum triangle areas of the boundary facets (linear tet facets)
    fverts = mesh.facets[:, facets].T  # (nfacets, 3) node indices
    p = pts[fverts]  # (nfacets, 3, 3)
    a = p[:, 1] - p[:, 0]
    b = p[:, 2] - p[:, 0]
    return float(0.5 * np.linalg.norm(np.cross(a, b), axis=1).sum())
