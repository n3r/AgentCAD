"""PRD-027 FR4 — content-addressed thumbnails.

The whole point of this slice is that a thumbnail is *derived data keyed by the
build's cache key*: it is never on the rebuild path, it never reaches the
kernel, and a stale one is impossible by construction (a new key is a new
file). Every test here is an assertion about one of those three claims.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import struct
import threading
import time

import pytest

from agentcad.core import render, thumbnails
from agentcad.core.model import InstanceSpec
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, CountingKernel, make_test_service

MB = 1024 * 1024


def _png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a PNG's IHDR — 8-byte signature, 4-byte length,
    4-byte tag, then two big-endian u32s."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def _add_part(service, part_id="box", script=BOX_SCRIPT):
    """Add a part WITHOUT building it.

    `service.create_part` returns `get_part`, which builds — so the service
    verb cannot express "a part exists and has no geometry", which is exactly
    the state a thumbnail has to answer 404 for. The store verb can.
    """
    service.store.add_part("demo", part_id, part_id, "al6061", script)


@pytest.fixture
def service(kernel, tmp_path):
    svc = make_test_service(tmp_path / "projects", kernel)
    svc.create_project("demo")
    _add_part(svc)
    return svc


def _build(service, part_id="box") -> str:
    """Build a part and return the cache key it landed on."""
    service.get_metrics("demo", part_id)
    return service._status[service._status_key("demo", part_id)]["cache_key"]


# --------------------------------------------------------------- pure layer


def test_mesh_for_key_prefers_the_lod1_sidecar(service):
    key = _build(service)
    cache = service.store.cache_dir("demo")

    assert thumbnails.mesh_for_key(cache, key).name == f"{key}.acm"

    # A big part gets a `<key>.lod1.acm` from the worker; a small one never
    # does, so fake it by copying the full mesh (the tier's *contents* are not
    # what this asserts — its precedence is).
    shutil.copy(cache / f"{key}.acm", cache / f"{key}.lod1.acm")
    assert thumbnails.mesh_for_key(cache, key).name == f"{key}.lod1.acm"

    assert thumbnails.mesh_for_key(cache, "0" * 32) is None


def test_thumb_path_is_the_cache_key_plus_thumb_png(tmp_path):
    assert thumbnails.thumb_path(tmp_path, "abc").name == "abc.thumb.png"


def test_render_part_thumb_writes_a_192px_png(service):
    key = _build(service)
    cache = service.store.cache_dir("demo")

    png = thumbnails.render_part_thumb(cache, key)

    assert png is not None
    assert _png_size(png) == (thumbnails.THUMB_SIZE, thumbnails.THUMB_SIZE)
    assert thumbnails.THUMB_SIZE == 192
    path = thumbnails.thumb_path(cache, key)
    assert path.is_file() and path.read_bytes() == png
    # Atomic write: no staging file left behind.
    assert not list(cache.glob("*.tmp"))


def test_render_part_thumb_is_none_without_a_mesh(tmp_path):
    assert thumbnails.render_part_thumb(tmp_path, "0" * 32) is None
    assert not list(tmp_path.glob("*.thumb.png"))


def test_render_part_thumb_refuses_a_mesh_over_max_triangles(service,
                                                             monkeypatch):
    key = _build(service)
    cache = service.store.cache_dir("demo")
    monkeypatch.setattr(render, "MAX_TRIANGLES", 1)

    assert thumbnails.render_part_thumb(cache, key) is None
    assert not thumbnails.thumb_path(cache, key).is_file()


# ------------------------------------------------------------- part_thumb


def test_part_thumb_serves_the_current_key_and_never_builds(service):
    key = _build(service)
    counter = CountingKernel(service.kernel)
    service.kernel = counter

    png, served = thumbnails.part_thumb(service, "demo", "box")

    assert served == key
    assert _png_size(png) == (192, 192)
    assert counter.calls == 0, "a thumbnail must never reach the kernel"

    # Second call is a cache read, still kernel-free.
    again, _ = thumbnails.part_thumb(service, "demo", "box")
    assert again == png
    assert counter.calls == 0


def test_part_thumb_is_none_for_an_unbuilt_part(service):
    counter = CountingKernel(service.kernel)
    service.kernel = counter

    assert thumbnails.part_thumb(service, "demo", "box") is None
    assert counter.calls == 0


def test_a_param_change_mints_a_new_thumb_and_leaves_the_old_one(service):
    old_key = _build(service)
    thumbnails.part_thumb(service, "demo", "box")
    cache = service.store.cache_dir("demo")
    assert thumbnails.thumb_path(cache, old_key).is_file()

    service.set_params("demo", "box", {"size": 12.0})
    new_key = _build(service)
    assert new_key != old_key

    _png, served = thumbnails.part_thumb(service, "demo", "box")
    assert served == new_key
    assert thumbnails.thumb_path(cache, new_key).is_file()
    assert thumbnails.thumb_path(cache, old_key).is_file(), (
        "the old thumb is content-addressed derived data: it stays until the "
        "janitor sweeps it, so a client still holding the old key gets a 200"
    )


# ------------------------------------------------------------------ warmer


def test_warmer_renders_on_rebuild_finished(service):
    warmer = thumbnails.ThumbnailWarmer(service)
    warmer.start()
    try:
        key = _build(service)
        warmer.drain()
    finally:
        warmer.stop()

    assert thumbnails.thumb_path(service.store.cache_dir("demo"), key).is_file()
    assert warmer.stats["rendered"] == 1


def test_warmer_ignores_config_tagged_rebuilds(service):
    """A matrix build is not a tree row: `config`-tagged events are skipped."""
    warmer = thumbnails.ThumbnailWarmer(service)

    warmer._on_event({"type": "rebuild_finished", "project": "demo",
                      "cache_key": "a" * 32, "config": "m5"})
    assert warmer.pending_count() == 0

    warmer._on_event({"type": "rebuild_finished", "project": "demo",
                      "cache_key": "a" * 32})
    assert warmer.pending_count() == 1

    # And an event with no key at all (a pre-PRD-027 publisher) is inert.
    warmer._on_event({"type": "rebuild_finished", "project": "demo"})
    assert warmer.pending_count() == 1


def test_warmer_coalesces_repeat_enqueues(service):
    key = _build(service)
    warmer = thumbnails.ThumbnailWarmer(service)

    for _ in range(5):
        warmer.enqueue("demo", key)
    assert warmer.pending_count() == 1

    warmer.drain()   # not started: drains inline, the test seam
    assert warmer.stats["rendered"] == 1
    assert thumbnails.thumb_path(service.store.cache_dir("demo"), key).is_file()

    # A second pass over a key whose file exists is a skip, not a re-render.
    warmer.enqueue("demo", key)
    warmer.drain()
    assert warmer.stats["rendered"] == 1
    assert warmer.stats["skipped_exists"] == 1


def test_warmer_drops_the_oldest_when_full(service):
    warmer = thumbnails.ThumbnailWarmer(service, maxsize=2)

    warmer.enqueue("demo", "a" * 32)
    warmer.enqueue("demo", "b" * 32)
    warmer.enqueue("demo", "c" * 32)

    assert warmer.pending_count() == 2
    assert warmer.stats["dropped"] == 1
    assert list(warmer.pending()) == [("demo", "b" * 32), ("demo", "c" * 32)]

    warmer.drain()
    assert warmer.stats["skipped_missing"] == 2


def test_warmer_start_is_idempotent_and_stop_is_safe_twice(service):
    warmer = thumbnails.ThumbnailWarmer(service)
    warmer.start()
    thread = warmer._thread
    warmer.start()
    assert warmer._thread is thread
    assert warmer.started is True

    warmer.stop()
    warmer.stop()
    assert warmer.started is False
    assert not thread.is_alive()


def test_warmer_never_raises_on_a_render_failure(service, monkeypatch, capsys):
    key = _build(service)
    warmer = thumbnails.ThumbnailWarmer(service)

    def boom(*_a, **_k):
        raise RuntimeError("rasterizer exploded")

    monkeypatch.setattr(thumbnails, "render_part_thumb", boom)
    warmer.enqueue("demo", key)
    warmer.drain()

    assert warmer.stats["failed"] == 1
    assert "rasterizer exploded" in capsys.readouterr().err


# ------------------------------------------------------ registration + env


def _fresh(kernel, tmp_path, name="projects"):
    return make_test_service(tmp_path / name, kernel)


def _warmer_threads():
    return [t for t in threading.enumerate() if t.name == "agentcad-thumbnails"]


def test_build_registry_installs_the_warmer_but_starts_no_thread(
        kernel, tmp_path, monkeypatch):
    """`build_registry` runs in `checks.py`, `packages/gate.py`,
    `bench/cli.py`'s loop, `share_build.py` and the MCP/CLI entry points. None
    of those is an HTTP server, and a warmer thread there is an orphan whose
    late render can `mkdir` a check cell the CLI already deleted. The thread is
    the ROUTE pack's job."""
    monkeypatch.delenv("AGENTCAD_THUMBNAILS", raising=False)
    before = len(_warmer_threads())
    svc = _fresh(kernel, tmp_path)

    build_registry(svc)

    assert isinstance(svc.thumbnails, thumbnails.ThumbnailWarmer)
    assert svc.thumbnails.started is False
    assert svc.thumbnails._thread is None
    assert len(_warmer_threads()) == before


def test_a_second_build_registry_reuses_the_warmer(kernel, tmp_path,
                                                   monkeypatch):
    """Several callers build a registry twice on one service; a fresh object
    each time would strand the previous one's thread and its bus subscriber."""
    monkeypatch.delenv("AGENTCAD_THUMBNAILS", raising=False)
    svc = _fresh(kernel, tmp_path)

    build_registry(svc)
    first = svc.thumbnails
    subscribers = svc.bus.subscriber_count()

    build_registry(svc)

    assert svc.thumbnails is first
    assert svc.bus.subscriber_count() == subscribers
    assert not _warmer_threads()


def test_mounting_the_routes_starts_exactly_one_thread(kernel, tmp_path,
                                                       monkeypatch):
    monkeypatch.delenv("AGENTCAD_THUMBNAILS", raising=False)
    before = len(_warmer_threads())
    svc = _fresh(kernel, tmp_path)
    registry = build_registry(svc)
    try:
        create_app(svc, registry, extra_allowed_hosts={"testserver"})
        assert svc.thumbnails.started is True
        assert len(_warmer_threads()) == before + 1

        # A second app on one service reuses the running thread (start() is
        # idempotent) — `create_app` is called twice in a few tests and tools.
        create_app(svc, registry, extra_allowed_hosts={"testserver"})
        assert len(_warmer_threads()) == before + 1
    finally:
        svc.thumbnails.stop()
    assert len(_warmer_threads()) == before


def test_agentcad_thumbnails_off_mounts_the_routes_without_a_thread(
        kernel, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_THUMBNAILS", "off")
    before = len(_warmer_threads())
    svc = _fresh(kernel, tmp_path)

    create_app(svc, build_registry(svc), extra_allowed_hosts={"testserver"})

    assert isinstance(svc.thumbnails, thumbnails.ThumbnailWarmer)
    assert svc.thumbnails.started is False
    assert svc.thumbnails._thread is None
    assert len(_warmer_threads()) == before


# --------------------------------------------------- service/store surface


def test_rebuild_finished_carries_the_cache_key_on_both_paths(service):
    seen: list[dict] = []
    q = service.bus.subscribe()
    try:
        key = _build(service)
        # Force the *cached* branch: forget the status, keep the files.
        service._status.clear()
        service.get_metrics("demo", "box")
        while not q.empty():
            seen.append(q.get_nowait())
    finally:
        service.bus.unsubscribe(q)

    finished = [e for e in seen if e["type"] == "rebuild_finished"]
    assert len(finished) == 2
    assert [e["cached"] for e in finished] == [False, True]
    assert all(e["cache_key"] == key for e in finished)


def test_get_project_carries_thumb_key(service):
    project = service.get_project("demo")
    assert project["parts"][0]["thumb_key"] is None

    key = _build(service)
    project = service.get_project("demo")
    assert project["parts"][0]["thumb_key"] == key


def test_trim_cache_sweeps_an_unreferenced_thumb(service):
    key = _build(service)
    cache = service.store.cache_dir("demo")
    thumbnails.render_part_thumb(cache, key)
    thumb = thumbnails.thumb_path(cache, key)
    assert thumb.is_file()

    # Over the watermark with nothing referenced: the janitor's normal sweep.
    (cache / "aaaaaaaa.acm").write_bytes(b"\0" * (2 * MB))
    old = time.time() - 100
    for entry in cache.iterdir():
        os.utime(entry, (old, old))
    service.store.disk_budget_mb = 1
    service.store._disk_memo.clear()

    service.store.trim_cache("demo", set(), min_age_s=0)

    assert not thumb.is_file(), ".thumb.png must be in _TRIMMABLE"


# ---------------------------------------------------------------- assembly


def _two_part_assembly(service):
    _add_part(service, "plate")
    _build(service, "box")
    _build(service, "plate")
    service.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "b", "part": "plate", "position": [30, 0, 0], "color": "#ff0000"},
    ])


def test_assembly_key_changes_when_an_instance_moves(service):
    _two_part_assembly(service)
    first = thumbnails.assembly_key(service, "demo")
    assert first is not None and len(first) == 32

    service.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "b", "part": "plate", "position": [60, 0, 0], "color": "#ff0000"},
    ])
    assert thumbnails.assembly_key(service, "demo") != first


def test_assembly_key_is_none_when_nothing_is_built(service):
    # The STORE verb, because `service.set_assembly` returns `get_assembly`,
    # which builds — and an instance of an unbuilt part is the state under test.
    service.store.set_instances("demo", [InstanceSpec(id="a", part="box")])
    assert thumbnails.assembly_key(service, "demo") is None


def test_assembly_thumb_composites_and_caches(service):
    _two_part_assembly(service)
    counter = CountingKernel(service.kernel)
    service.kernel = counter

    png, key = thumbnails.assembly_thumb(service, "demo")

    assert _png_size(png) == (192, 192)
    assert key == thumbnails.assembly_key(service, "demo")
    cached = service.store.cache_dir("demo") / f"asm-{key}.thumb.png"
    assert cached.is_file()
    assert counter.calls == 0

    again, again_key = thumbnails.assembly_thumb(service, "demo")
    assert (again, again_key) == (png, key)
    assert counter.calls == 0


def test_the_assembly_thumb_key_has_its_own_trim_bucket(service):
    """`trim_cache` keys on `name.split(".", 1)[0]`, so `asm.<hash>.thumb.png`
    would key as the literal `asm` and share a bucket with every other
    assembly. The dash keeps each composite its own key."""
    _two_part_assembly(service)
    _png, key = thumbnails.assembly_thumb(service, "demo")
    name = f"asm-{key}.thumb.png"
    assert name.split(".", 1)[0] == f"asm-{key}"


def test_assembly_thumb_falls_back_to_the_first_built_part(service):
    key = _build(service)          # no instances at all
    png, served = thumbnails.assembly_thumb(service, "demo")
    assert served == key
    assert _png_size(png) == (192, 192)


def test_assembly_thumb_is_none_on_a_cold_project(service):
    counter = CountingKernel(service.kernel)
    service.kernel = counter
    assert thumbnails.assembly_thumb(service, "demo") is None
    assert counter.calls == 0


def test_has_thumb_is_a_cheap_gate(service):
    assert thumbnails.has_thumb(service, "demo") is False

    key = _build(service)
    assert thumbnails.has_thumb(service, "demo") is True   # a mesh is enough

    thumbnails.render_part_thumb(service.store.cache_dir("demo"), key)
    assert thumbnails.has_thumb(service, "demo") is True

    # It renders nothing: a cold project with only a thumb on disk still says
    # yes, and neither branch touches the kernel.
    counter = CountingKernel(service.kernel)
    service.kernel = counter
    assert thumbnails.has_thumb(service, "demo") is True
    assert counter.calls == 0


# ------------------------------------------------------------------ routes


@pytest.fixture
def client(kernel, tmp_path):
    from fastapi.testclient import TestClient

    svc = make_test_service(tmp_path / "projects", kernel)
    svc.kernel = CountingKernel(svc.kernel)
    app = create_app(svc, build_registry(svc),
                     extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")
    http.svc = svc
    try:
        yield http
    finally:
        warmer = getattr(svc, "thumbnails", None)
        if warmer is not None:
            warmer.stop()


def test_part_thumb_route_404s_an_unbuilt_part_without_a_kernel_call(client):
    svc = client.svc
    svc.create_project("demo")
    _add_part(svc)
    before = svc.kernel.calls

    cold = client.get("/api/projects/demo/thumb.png")
    assert cold.status_code == 404

    unbuilt = client.get("/api/projects/demo/parts/box/thumb.png")
    assert unbuilt.status_code == 404
    assert unbuilt.json()["error"]["type"] == "NotFoundError"

    missing = client.get("/api/projects/demo/parts/nope/thumb.png")
    assert missing.status_code == 404

    assert svc.kernel.calls == before, "the thumb routes never build"


def test_part_thumb_route_renders_on_demand_and_caches(client):
    svc = client.svc
    svc.create_project("demo")
    _add_part(svc)
    key = _build(svc)
    before = svc.kernel.calls

    response = client.get("/api/projects/demo/parts/box/thumb.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["etag"] == f'"{key}"'
    assert response.headers["cache-control"] == "no-cache"
    assert _png_size(response.content) == (192, 192)
    assert svc.kernel.calls == before


def test_part_thumb_route_is_immutable_only_on_a_key_match(client):
    svc = client.svc
    svc.create_project("demo")
    _add_part(svc)
    key = _build(svc)
    url = "/api/projects/demo/parts/box/thumb.png"

    matched = client.get(f"{url}?k={key}")
    assert matched.headers["cache-control"] == (
        "private, max-age=31536000, immutable")

    stale = client.get(f"{url}?k={'0' * 32}")
    assert stale.status_code == 200
    assert stale.headers["cache-control"] == "no-cache"

    # A malformed `k` is ignored, never a 422.
    junk = client.get(f"{url}?k=not-a-key")
    assert junk.status_code == 200
    assert junk.headers["cache-control"] == "no-cache"


def test_part_thumb_route_answers_304_to_a_matching_if_none_match(client):
    svc = client.svc
    svc.create_project("demo")
    _add_part(svc)
    key = _build(svc)
    url = "/api/projects/demo/parts/box/thumb.png"

    assert client.get(url).status_code == 200
    fresh = client.get(url, headers={"If-None-Match": f'"{key}"'})
    assert fresh.status_code == 304
    assert fresh.content == b""
    assert fresh.headers["etag"] == f'"{key}"'

    stale = client.get(url, headers={"If-None-Match": '"deadbeef"'})
    assert stale.status_code == 200


def test_assembly_thumb_route(client):
    svc = client.svc
    svc.create_project("demo")
    _add_part(svc)
    _build(svc)
    svc.set_assembly("demo", [{"id": "a", "part": "box"}])
    before = svc.kernel.calls

    response = client.get("/api/projects/demo/thumb.png")
    assert response.status_code == 200
    key = response.headers["etag"].strip('"')
    assert key == thumbnails.assembly_key(svc, "demo")

    matched = client.get(f"/api/projects/demo/thumb.png?k={key}")
    assert matched.headers["cache-control"] == (
        "private, max-age=31536000, immutable")
    assert client.get("/api/projects/demo/thumb.png",
                      headers={"If-None-Match": f'"{key}"'}).status_code == 304
    assert svc.kernel.calls == before


def test_thumb_routes_are_member_only(client):
    """Default-deny: nothing here joins the anonymous surface."""
    from agentcad.server import security

    assert not security.is_public("/api/projects/demo/thumb.png")
    assert not security.is_public("/api/projects/demo/parts/box/thumb.png")


def test_render_time_at_192_is_a_few_milliseconds(service):
    """Recorded, not gated: the design's claim is ~5-20 ms at 192**2, which is
    what makes the on-demand route a static-asset path in spirit."""
    key = _build(service)
    cache = service.store.cache_dir("demo")
    start = time.perf_counter()
    thumbnails.render_part_thumb(cache, key)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert elapsed_ms < 2000.0, elapsed_ms
    print(f"\n192x192 part thumbnail render: {elapsed_ms:.1f} ms")


def test_thumbnails_module_is_ocp_free():
    """It runs in the *server* process (routes, warmer thread), and only
    `agentcad/kernel/` may import the geometry kernel."""
    import ast

    text = pathlib.Path(thumbnails.__file__).read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert not any(n.split(".")[0] in ("OCP", "build123d") for n in names), names


# ------------------------------------- PRD-013 expansion must stay kernel-free

class RefusingKernel:
    """Any kernel request is a test failure, loudly and at the call site.

    `CountingKernel` proves a number after the fact; this proves *which* op was
    attempted, which is what the review needed: `mates.expand` issues
    `resolve_assembly` for polar-pattern and sub-assembly members and
    `resolve_mates` for the mate pass, and a guard on `mate` alone let the
    first one through from `GET /thumb.png`.
    """

    def __init__(self, inner):
        self._inner = inner

    def request(self, op, *_a, **_k):
        raise AssertionError(f"a thumbnail reached the kernel: {op!r}")

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def structured(client):
    """A client whose registry has PRD-013's expansion installed, with one
    built part and a kernel that refuses every request."""
    svc = client.svc
    svc.create_project("demo")
    _add_part(svc)
    _build(svc)
    assert getattr(svc._resolved_instances, "_agentcad_structure_wrapped", False), (
        "tools_structure must have rebound the seam, or this proves nothing"
    )
    svc.kernel = RefusingKernel(svc.kernel)
    return client


def _put_instances(service, items):
    """Write instances through the STORE (the service verb builds)."""
    service.store.set_instances("demo", [InstanceSpec(**i) for i in items])


def test_a_polar_pattern_thumb_makes_no_kernel_request(structured):
    """The reproduction from the review: one unmated polar pattern, and
    `_resolved_instances` -> `mates.expand` -> `resolve_assembly`."""
    _put_instances(structured.svc, [
        {"id": "p", "part": "box",
         "pattern": {"kind": "polar", "count": 4, "angle_step_deg": 90.0}},
    ])

    response = structured.get("/api/projects/demo/thumb.png")

    assert response.status_code == 200
    # A polar member's transform is composed in the kernel, so the base
    # instance composites once, at its stored transform.
    assert len(thumbnails._instance_rows(structured.svc, "demo")) == 1


def test_a_subassembly_instance_thumb_makes_no_kernel_request(structured):
    _put_instances(structured.svc, [
        {"id": "sub", "assembly": {"project": "somewhere-else"}},
        {"id": "a", "part": "box"},
    ])

    response = structured.get("/api/projects/demo/thumb.png")

    assert response.status_code == 200
    # The sub-assembly reference carries no part of its own and is skipped;
    # only the plain instance composites.
    rows = thumbnails._instance_rows(structured.svc, "demo")
    assert [inst.id for _row, inst in rows] == ["a"]


def test_a_subassembly_only_project_never_calls_the_kernel(structured):
    _put_instances(structured.svc, [
        {"id": "sub", "assembly": {"project": "somewhere-else"}},
    ])
    # No placeable instance at all, so this takes the loose-part fallback; what
    # it asserts is the *absence of a kernel call*, which RefusingKernel makes
    # a hard failure rather than a number to check afterwards.
    assert structured.get("/api/projects/demo/thumb.png").status_code == 200
    assert thumbnails._instance_rows(structured.svc, "demo") == []


def test_a_linear_pattern_is_expanded_purely(structured):
    """Linear expansion is a translation — `mates.expand` issues no op for it,
    so we can (and do) reproduce it locally with `mates`' own helpers."""
    _put_instances(structured.svc, [
        {"id": "p", "part": "box",
         "pattern": {"kind": "linear", "count": 3, "step_mm": 20.0}},
    ])

    rows = thumbnails._instance_rows(structured.svc, "demo")

    assert [inst.id for _row, inst in rows] == ["p[0]", "p[1]", "p[2]"]
    assert [row["position"][0] for row, _inst in rows] == [0.0, 20.0, 40.0]
    assert structured.get("/api/projects/demo/thumb.png").status_code == 200

    # More members is different content, so a different composite key.
    three = thumbnails.assembly_key(structured.svc, "demo")
    _put_instances(structured.svc, [
        {"id": "p", "part": "box",
         "pattern": {"kind": "linear", "count": 2, "step_mm": 20.0}},
    ])
    assert thumbnails.assembly_key(structured.svc, "demo") != three


def test_a_mated_instance_composites_at_its_stored_transform(structured):
    """`mates.resolve` executes every part's script in the worker; a thumbnail
    may not pay that, so a mated instance keeps the transform on disk."""
    _put_instances(structured.svc, [
        {"id": "a", "part": "box", "position": [1.0, 2.0, 3.0]},
        {"id": "b", "part": "box", "position": [9.0, 0.0, 0.0],
         "mate": {"to_instance": "a", "connector": "c", "to_connector": "c"}},
    ])

    rows = thumbnails._instance_rows(structured.svc, "demo")

    assert {inst.id: row["position"][0] for row, inst in rows} == {
        "a": 1.0, "b": 9.0}
    assert structured.get("/api/projects/demo/thumb.png").status_code == 200


@pytest.mark.parametrize("pattern", [
    # TypeError/ValueError out of int("not-a-number").
    {"kind": "linear", "count": "not-a-number"},
    # IndexError out of `axis[1]`. `_validate_pattern` never checks `axis`, so
    # this one is reachable through the `set_pattern` API, not just a hand edit.
    {"kind": "linear", "count": 2, "step_mm": 5.0, "axis": [[0, 0, 0]]},
    # AttributeError out of `pattern.get(...)` — a hand-edited manifest whose
    # pattern is the kind string instead of the object.
    "polar",
])
def test_a_malformed_pattern_degrades_instead_of_refusing(structured, pattern):
    """Anything escaping expansion would be a 4xx/500 from an <img> tag; the
    answer is always the base instance."""
    svc = structured.svc
    manifest = svc.store.manifest("demo")
    manifest["assembly"]["instances"] = [
        {"id": "p", "part": "box", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0], "pattern": pattern},
    ]
    svc.store.save_manifest("demo", manifest)

    assert structured.get("/api/projects/demo/thumb.png").status_code == 200
    assert len(thumbnails._instance_rows(svc, "demo")) == 1


def test_an_origin_project_member_is_never_looked_up_here(service):
    """`origin_project` names the project a member is BUILT from. It is
    transient and `to_manifest` omits it, so a stored instance never carries
    one — but if a future writer let one through, resolving its part id against
    THIS project would composite a same-id part's geometry."""
    _build(service)
    service.store.set_instances("demo", [InstanceSpec(id="a", part="box")])
    instances = service.store.instances("demo")
    instances[0].origin_project = "somewhere-else"
    service.store.instances = lambda _proj, _i=instances: list(_i)

    assert thumbnails._instances(service, "demo") == []


# ---------------------------------------------- corrupt / truncated meshes

def test_an_unreadable_mesh_is_no_mesh_not_a_500(service):
    key = _build(service)
    cache = service.store.cache_dir("demo")
    # An empty LOD1 sidecar wins the precedence and cannot be parsed: the
    # header `unpack_from` raises `struct.error`, which is not a ValueError.
    (cache / f"{key}.lod1.acm").write_bytes(b"")

    assert thumbnails.mesh_for_key(cache, key).name == f"{key}.lod1.acm"
    assert thumbnails.render_part_thumb(cache, key) is None
    assert thumbnails.part_thumb(service, "demo", "box") is None

    # And through the route: a 404, never a 500.
    (cache / f"{key}.lod1.acm").write_bytes(b"ACM1 but truncated")
    assert thumbnails.render_part_thumb(cache, key) is None


def test_has_thumb_does_not_create_the_cache_directory(service, tmp_path):
    """A *gate* must not leave a directory behind in a project it was asked
    about — `cache_dir` mkdirs, `canonical_path_of` does not."""
    cache = service.store.canonical_path_of("demo") / ".cache"
    if cache.exists():
        shutil.rmtree(cache)

    assert thumbnails.has_thumb(service, "demo") is False
    assert not cache.exists()


def test_a_real_check_run_leaves_no_thumbnail_thread(kernel, tmp_path,
                                                     monkeypatch):
    """The `agentcad check` path end to end: an ephemeral service, its full
    registry and a run — and no `agentcad-thumbnails` thread afterwards. A late
    render from one would `_atomic_write` into a cell the CLI has deleted."""
    from agentcad.core.checks import CheckRunner

    monkeypatch.delenv("AGENTCAD_THUMBNAILS", raising=False)
    before = len(_warmer_threads())
    svc = _fresh(kernel, tmp_path)
    svc.create_project("demo")
    _add_part(svc)
    registry = build_registry(svc)

    report = CheckRunner(svc, registry).run("demo")

    assert report["stages"]
    assert svc.thumbnails.started is False
    assert len(_warmer_threads()) == before
