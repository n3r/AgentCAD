"""PRD-006 slice 4, Decision 10 — the per-project disk budget and its janitor.

`quotas.disk_mb` covers a project's `.cache/`, `exports/` and `imports/`. The
store measures them (with a short memo, because a build path may ask twice a
second) and refuses **before** the kernel writes: a budget enforced after the
worker had already streamed a mesh would leave a half-written file behind, and
`_atomic_write` is not what saves you from a full disk.

`trim_cache` is the other half — a janitor, not a quota. Meshes are
content-addressed and rebuildable, so the oldest unreferenced ones go when the
cache passes 75 % of the budget, and a key the service still points at stays.
"""

from __future__ import annotations

import os
import time

import pytest

from agentcad.core.model import DiskBudgetError
from agentcad.core.project import ProjectStore

from .conftest import BOX_SCRIPT, make_test_service

pytestmark = pytest.mark.portability

MB = 1024 * 1024


def _fill(path, megabytes: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * int(megabytes * MB))


@pytest.fixture
def store(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    store.create("demo")
    return store


# ------------------------------------------------------------- measurement


def test_disk_usage_splits_the_three_directories(store):
    _fill(store.cache_dir("demo") / "k.acm", 0.5)
    _fill(store.exports_dir("demo") / "demo.step", 0.25)
    _fill(store.imports_dir("demo") / "vendor.step", 0.125)

    used = store.disk_usage("demo")

    assert used["cache_bytes"] == int(0.5 * MB)
    assert used["exports_bytes"] == int(0.25 * MB)
    assert used["imports_bytes"] == int(0.125 * MB)
    assert used["used_bytes"] == int(0.875 * MB)


def test_disk_usage_is_memoized_for_five_seconds(store):
    _fill(store.exports_dir("demo") / "a.step", 0.5)
    first = store.disk_usage("demo")
    _fill(store.exports_dir("demo") / "b.step", 0.5)

    assert store.disk_usage("demo") == first  # the memo still answers

    store.invalidate_disk_usage("demo")
    assert store.disk_usage("demo")["used_bytes"] == int(1.0 * MB)


def test_disk_usage_creates_no_directories(tmp_path):
    """It is a measurement, and a measurement that makes an `exports/` on a
    read path would show up in every project tree and every git diff."""
    store = ProjectStore(tmp_path / "projects")
    store.create("demo")
    root = store.path_of("demo")

    store.disk_usage("demo")

    assert not (root / "exports").exists()
    assert not (root / "imports").exists()
    assert not (root / ".cache").exists()


# ------------------------------------------------------------- the refusal


def test_no_budget_means_no_check(store):
    _fill(store.exports_dir("demo") / "huge.step", 2.0)
    assert store.disk_budget_mb is None
    store.assert_disk_budget("demo")  # does not raise


def test_over_budget_raises_disk_budget_error(store):
    store.disk_budget_mb = 1
    _fill(store.exports_dir("demo") / "huge.step", 1.5)

    with pytest.raises(DiskBudgetError) as exc_info:
        store.assert_disk_budget("demo")

    details = exc_info.value.details
    assert details["used_mb"] >= 1
    assert details["budget_mb"] == 1
    assert details["project"] == "demo"
    assert "AGENTCAD_QUOTA_DISK_MB" in exc_info.value.message


def test_under_budget_passes(store):
    store.disk_budget_mb = 4
    _fill(store.exports_dir("demo") / "small.step", 1.0)
    store.assert_disk_budget("demo")


def test_an_import_write_is_refused_over_budget(store):
    store.disk_budget_mb = 1
    _fill(store.cache_dir("demo") / "k.acm", 1.5)

    with pytest.raises(DiskBudgetError):
        store.imports_dir("demo", write=True)

    # ...and a READ of the same directory still works: a rebuild must not fail
    # because the project is full of exports.
    assert store.imports_dir("demo").is_dir()


def test_the_service_refuses_an_export_over_budget(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", "Box", BOX_SCRIPT)
    service.store.disk_budget_mb = 1
    _fill(service.store.exports_dir("demo") / "ballast.bin", 1.5)
    service.store.invalidate_disk_usage("demo")

    with pytest.raises(DiskBudgetError):
        service.export_part("demo", "box", "step")


def test_the_service_refuses_a_build_over_budget(kernel, tmp_path):
    """A read path's build raises: the refusal is the caller's answer."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", "Box", BOX_SCRIPT)
    # A script the cache has never seen, so the build is a real kernel call.
    service.store.write_script("demo", "box",
                               BOX_SCRIPT.replace("p.size, p.size, p.size",
                                                  "p.size, p.size, p.size * 2"))
    service.store.disk_budget_mb = 1
    _fill(service.store.exports_dir("demo") / "ballast.bin", 1.5)
    service.store.invalidate_disk_usage("demo")

    with pytest.raises(DiskBudgetError):
        service._rebuild("demo", "box")


def test_a_cache_hit_still_answers_when_the_project_is_full(kernel, tmp_path):
    """The budget guards the *write*. Geometry that already exists must still
    be readable, or filling a project would make it unopenable."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", "Box", BOX_SCRIPT)
    service.store.disk_budget_mb = 1
    _fill(service.store.exports_dir("demo") / "ballast.bin", 1.5)
    service.store.invalidate_disk_usage("demo")

    assert service._rebuild("demo", "box")["ok"] is True


def test_a_build_after_a_landed_write_is_a_post_state_not_a_raise(kernel,
                                                                   tmp_path):
    """PRD-012's rule holds for this refusal too: `set_params` writes first, so
    an over-budget rebuild is the part's post-state, never a 4xx after the
    manifest was saved."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", "Box", BOX_SCRIPT)
    service.store.disk_budget_mb = 1
    _fill(service.store.exports_dir("demo") / "ballast.bin", 1.5)
    service.store.invalidate_disk_usage("demo")

    result = service.set_params("demo", "box", {"size": 12.0})

    assert result["ok"] is False
    assert result["error"]["type"] == "diskbudget_error"
    assert service.store.get_part("demo", "box").params["size"] == 12.0


def test_the_wire_type_is_disk_budget_error(kernel, tmp_path):
    """A tool call answers the refusal as a typed payload, not a traceback."""
    from agentcad.core.tools import build_registry

    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", "Box", BOX_SCRIPT)
    service.store.disk_budget_mb = 1
    _fill(service.store.exports_dir("demo") / "ballast.bin", 1.5)
    service.store.invalidate_disk_usage("demo")

    result = build_registry(service).call(
        "export_part", {"project": "demo", "part_id": "box", "format": "step"})

    assert result["error"]["type"] == "diskbudget_error"
    assert result["error"]["details"]["budget_mb"] == 1


# ---------------------------------------------------------------- the janitor


def _cache_file(store, key, suffix, megabytes, mtime):
    path = store.cache_dir("demo") / f"{key}{suffix}"
    _fill(path, megabytes)
    os.utime(path, (mtime, mtime))
    return path


def test_trim_cache_is_a_no_op_under_the_watermark(store):
    store.disk_budget_mb = 8
    kept = _cache_file(store, "old", ".acm", 1.0, time.time() - 1000)

    assert store.trim_cache("demo", set()) == 0
    assert kept.exists()


def test_trim_cache_removes_the_oldest_unreferenced_mesh_first(store):
    store.disk_budget_mb = 4      # watermark: 3 MB
    now = time.time()
    oldest = _cache_file(store, "aaa", ".acm", 1.5, now - 3000)
    sidecar = _cache_file(store, "aaa", ".faces.u32", 0.25, now - 3000)
    middle = _cache_file(store, "bbb", ".acm", 1.5, now - 2000)
    newest = _cache_file(store, "ccc", ".acm", 1.5, now - 1000)

    freed = store.trim_cache("demo", {"ccc"})

    assert freed >= int(1.5 * MB)
    assert not oldest.exists() and not sidecar.exists()
    assert middle.exists(), "trimming stopped as soon as it was under the mark"
    assert newest.exists(), "a referenced key is never deleted"


def test_trim_cache_keeps_referenced_keys_even_when_it_cannot_get_under(store):
    store.disk_budget_mb = 1      # watermark: 0.75 MB, and everything is live
    live = _cache_file(store, "aaa", ".acm", 1.0, time.time() - 3000)

    assert store.trim_cache("demo", {"aaa"}) == 0
    assert live.exists()


def test_the_lod_sidecar_is_a_candidate_in_its_own_right(store):
    """`<key>.lod1.acm` ends in `.acm` and its key is the part before the first
    dot — a suffix match that read `.lod1.acm` as its own kind would leave the
    preview tiers behind forever."""
    store.disk_budget_mb = 2      # watermark: 1.5 MB
    now = time.time()
    lod = _cache_file(store, "aaa", ".lod1.acm", 1.0, now - 3000)
    keep = _cache_file(store, "bbb", ".acm", 1.0, now - 100)

    assert store.trim_cache("demo", {"bbb"}) == int(1.0 * MB)
    assert not lod.exists()
    assert keep.exists()


def test_the_metrics_sidecar_is_never_trimmed(store):
    """It is bytes, not megabytes, and a build treats a sidecar without its
    mesh as a miss — so deleting it buys nothing and risks a surprise."""
    store.disk_budget_mb = 2
    now = time.time()
    metrics = _cache_file(store, "aaa", ".metrics.json", 0.01, now - 3000)
    _cache_file(store, "aaa", ".acm", 2.0, now - 3000)

    store.trim_cache("demo", set())

    assert metrics.exists()


def test_trim_cache_refreshes_the_memo(store):
    store.disk_budget_mb = 4
    now = time.time()
    _cache_file(store, "aaa", ".acm", 2.0, now - 3000)
    _cache_file(store, "bbb", ".acm", 1.5, now - 100)
    before = store.disk_usage("demo")["cache_bytes"]

    store.trim_cache("demo", {"bbb"})

    assert store.disk_usage("demo")["cache_bytes"] < before


def test_a_build_trims_and_keeps_what_the_service_still_points_at(kernel,
                                                                  tmp_path):
    """The janitor runs after a successful build. The key that build just
    minted is referenced by definition, so it must survive its own trim."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", "Box", BOX_SCRIPT)
    stale = _cache_file(service.store, "deadbeef", ".acm", 1.7,
                        time.time() - 5000)
    # Under the budget (so the build is allowed) and over the 1.5 MB
    # watermark (so the janitor runs) — the gap the two thresholds create.
    service.store.disk_budget_mb = 2
    service.store.invalidate_disk_usage("demo")

    built = service.set_params("demo", "box", {"size": 11.0})

    assert built["ok"] is True
    assert not stale.exists(), "the oldest unreferenced mesh should be gone"
    key = service._status[service._status_key("demo", "box")]["cache_key"]
    assert (service.store.cache_dir("demo") / f"{key}.acm").is_file()
