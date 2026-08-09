"""KernelPool tests: parallel determinism, affinity, drop-in equivalence."""

import concurrent.futures
import time

import pytest

from agentcad.core.service import AgentCADService, EventBus
from agentcad.kernel.pool import KernelPool

from .conftest import BOX_SCRIPT


def _build(client, script, params, mesh_path):
    return client.request("build", {
        "script": script, "params": params, "density_g_cm3": 2.7,
        "mesh_path": str(mesh_path),
    })


def test_pool_parallel_matches_sequential(kernel, tmp_path):
    pool = KernelPool(size=2)
    pool.start()
    try:
        # sequential reference (single shared kernel)
        seq = {}
        for s in (5.0, 10.0, 15.0, 20.0):
            r = _build(kernel, BOX_SCRIPT, {"size": s}, tmp_path / f"seq{s}.acm")
            seq[s] = (tmp_path / f"seq{s}.acm").read_bytes()

        # parallel via the pool
        def job(s):
            _build(pool, BOX_SCRIPT, {"size": s}, tmp_path / f"par{s}.acm")
            return s, (tmp_path / f"par{s}.acm").read_bytes()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            par = dict(ex.map(job, [5.0, 10.0, 15.0, 20.0]))

        for s in (5.0, 10.0, 15.0, 20.0):
            assert par[s] == seq[s]  # byte-identical, no cross-worker nondeterminism
    finally:
        pool.stop()


def test_affinity_routes_same_part_to_same_worker():
    pool = KernelPool(size=3)
    # same affinity -> same worker index; different keys can differ
    idxs = {pool._pick("partA") for _ in range(5)}
    assert len(idxs) == 1
    w_a = pool._pick("partA")
    assert pool._pick("partA") is w_a


def test_size_one_is_single_worker(kernel, tmp_path):
    pool = KernelPool(size=1)
    pool.start()
    try:
        assert pool.size == 1
        r = _build(pool, BOX_SCRIPT, {"size": 10.0}, tmp_path / "m.acm")
        assert r["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    finally:
        pool.stop()


def test_pool_respawns_after_crash(tmp_path):
    from agentcad.kernel.client import KernelError

    pool = KernelPool(size=2, timeout_s=60.0)
    pool.start()
    try:
        # a hanging build on an affinity-routed worker times out and respawns
        with pytest.raises(KernelError):
            pool.request("build",
                         {"script": "while True:\n    pass\n", "params": {},
                          "mesh_path": str(tmp_path / "x.acm")},
                         timeout_s=3.0, affinity="hang")
        # the pool still serves requests afterward
        assert pool.request("ping", {}, affinity="hang")["ok"] is True
    finally:
        pool.stop()


def test_service_works_with_pool(tmp_path):
    pool = KernelPool(size=2)
    pool.start()
    try:
        service = AgentCADService(tmp_path / "projects", pool, EventBus())
        service.create_project("demo")
        service.create_part("demo", "box", script=BOX_SCRIPT)
        assert service.get_metrics("demo", "box")["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    finally:
        pool.stop()
