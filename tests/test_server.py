import time

import pytest
from fastapi.testclient import TestClient

from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service

pytestmark = pytest.mark.portability


@pytest.fixture
def client(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    # TestClient always sends Host: testserver on WebSocket connects,
    # so tests must allow it explicitly; production defaults stay local-only.
    app = create_app(
        service, build_registry(service), extra_allowed_hosts={"testserver"}
    )
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def demo(client):
    assert client.post("/api/projects", json={"name": "demo"}).status_code == 201
    response = client.post(
        "/api/projects/demo/parts", json={"id": "box", "script": BOX_SCRIPT}
    )
    assert response.status_code == 201
    return client


def test_health(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert data["kernel"] in ("ready", "starting")
    assert data["chat_available"] is False
    # the shared session kernel is never sandboxed, so "active" is impossible
    assert data["sandbox"] in ("off", "unsupported")


def test_frontend_theme_assets(client):
    index = client.get("/").text
    assert 'id="theme-btn"' in index
    assert 'localStorage.getItem("agentcad.theme")' in index  # pre-paint restore
    css = client.get("/css/app.css").text
    assert ':root[data-theme="light"]' in css
    assert client.get("/js/theme.js").status_code == 200


def test_project_and_part_flow(demo):
    projects = demo.get("/api/projects").json()["projects"]
    assert projects[0]["name"] == "demo"

    part = demo.get("/api/projects/demo/parts/box").json()
    assert part["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert part["params_spec"]["size"]["max"] == 100.0

    result = demo.patch(
        "/api/projects/demo/parts/box/params", json={"size": 20.0}
    ).json()
    assert result["ok"] is True
    assert result["metrics"]["volume_mm3"] == pytest.approx(8000.0, rel=1e-6)


def test_broken_script_returns_ok_false(demo):
    result = demo.put(
        "/api/projects/demo/parts/box",
        json={"script": "PARAMS = {}\ndef build(p):\n    return 1\n"},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False
    assert body["error"]["type"] == "contract_error"


def test_mesh_endpoint(demo):
    response = demo.get("/api/projects/demo/parts/box/mesh")
    assert response.status_code == 200
    assert response.content[:4] == b"ACM1"
    assert response.headers["x-mesh-key"]
    assert response.headers["cache-control"] == "no-store"


def test_export(demo):
    result = demo.post(
        "/api/projects/demo/parts/box/export", json={"format": "step"}
    ).json()
    assert result["size_bytes"] > 500


def test_assembly_flow(demo):
    result = demo.put(
        "/api/projects/demo/assembly",
        json={
            "instances": [
                {"id": "a", "part": "box", "position": [0, 0, 0]},
                {"id": "b", "part": "box", "position": [5, 0, 0]},
            ]
        },
    ).json()
    assert result["total_mass_g"] > 0

    pairs = demo.post("/api/projects/demo/assembly/interference").json()["pairs"]
    assert len(pairs) == 1

    export = demo.post("/api/projects/demo/export", json={"format": "step"}).json()
    assert export["size_bytes"] > 500


def test_error_mapping(demo):
    assert demo.get("/api/projects/ghost").status_code == 404
    assert demo.post("/api/projects", json={"name": "BAD NAME"}).status_code == 422
    assert demo.post("/api/projects", json={"name": "demo"}).status_code == 409
    body = demo.get("/api/projects/ghost").json()
    assert body["error"]["type"] == "NotFoundError"


@pytest.mark.parametrize("body", [[], "bad", 3])
def test_a_non_object_body_is_a_422_not_a_500(demo, body):
    """Fix wave (C1/C2): a house-wide, **pre-existing** gap, not a PRD-012
    regression — every one of these routes read `body = await request.json()`
    and then dereferenced it with `body.get(...)`, so a JSON array (or a bare
    string, or a number) was an `AttributeError` 500. `PUT /assembly` and
    `PATCH .../params` are untouched by PRD-012 and were in the same state,
    which is what makes it house-wide."""
    for path, method in (
        ("/api/projects/demo/parts/box/export", demo.post),
        ("/api/projects/demo/parts/box/drawing", demo.post),
        ("/api/projects/demo/assembly", demo.put),
        ("/api/projects/demo/parts/box/params", demo.patch),
    ):
        response = method(path, json=body)
        assert response.status_code == 422, (path, body, response.text)
        assert "JSON object" in response.json()["error"]["message"], path


def test_an_absent_assembly_body_is_a_422_and_never_wipes_the_assembly(demo):
    """Fix-wave re-review (Important, introduced by S6): `_json` returns `{}`
    for a **genuinely absent** body, and `set_assembly(proj,
    body.get("instances", []))` reads that as "replace the assembly with
    nothing" — a 200 that silently emptied `assembly.instances`, where the
    pre-wave code raised (a 500, but no mutation). It is the M3 shape exactly:
    the key is REQUIRED, because its absence cannot mean "nothing to change"
    when the default is the destructive verb.
    """
    placed = demo.put("/api/projects/demo/assembly", json={"instances": [
        {"id": "a", "part": "box", "position": [0, 0, 0]}]})
    assert placed.status_code == 200, placed.text
    assert [i["id"] for i in placed.json()["instances"]] == ["a"]

    empty = demo.put("/api/projects/demo/assembly")

    assert empty.status_code == 422, empty.text
    assert "instances is required" in empty.json()["error"]["message"]
    kept = demo.get("/api/projects/demo/assembly").json()["instances"]
    assert [i["id"] for i in kept] == ["a"]

    # ...and the explicit clear still clears.
    cleared = demo.put("/api/projects/demo/assembly", json={"instances": []})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["instances"] == []
    assert demo.get("/api/projects/demo/assembly").json()["instances"] == []


def test_an_absent_body_is_harmless_on_the_other_strict_reader_routes(demo):
    """The other half of the same re-review: `_json` also lets an ABSENT body
    reach `PATCH .../params` and `POST .../export`, where it is benign rather
    than destructive — an empty override map merges nothing, and an empty
    format is the export's own refusal. Confirmed, not assumed."""
    before = demo.get("/api/projects/demo/parts/box").json()["params"]

    patched = demo.patch("/api/projects/demo/parts/box/params")
    assert patched.status_code == 200, patched.text
    assert demo.get("/api/projects/demo/parts/box").json()["params"] == before

    exported = demo.post("/api/projects/demo/parts/box/export")
    assert exported.status_code == 422, exported.text


def test_tools_endpoints(demo):
    tools = demo.get("/api/tools").json()["tools"]
    assert len(tools) >= 25  # 17 core + v2 packs
    assert all("input_schema" in t for t in tools)

    result = demo.post("/api/tools/list_projects").json()
    assert result["projects"][0]["name"] == "demo"

    result = demo.post("/api/tools/get_metrics", json={"project": "demo", "part_id": "box"}).json()
    assert result["volume_mm3"] > 0


def test_host_header_guard(kernel, tmp_path):
    service = make_test_service(tmp_path / "p2", kernel)
    app = create_app(service, build_registry(service))
    evil = TestClient(app, base_url="http://evil.example.com")
    assert evil.get("/api/health").status_code == 403
    local = TestClient(app, base_url="http://localhost")
    assert local.get("/api/health").status_code == 200


def test_cross_origin_post_rejected(demo):
    response = demo.post(
        "/api/tools/list_projects",
        headers={"Origin": "http://evil.example.com"},
    )
    assert response.status_code == 403
    # same-origin fetches (browser sends our own origin) still work
    response = demo.post(
        "/api/tools/list_projects",
        headers={"Origin": "http://127.0.0.1", "Host": "127.0.0.1"},
    )
    assert response.status_code == 200


def test_websocket_rejects_foreign_origin(demo):
    import pytest as pytest_module

    with pytest_module.raises(Exception):
        with demo.websocket_connect(
            "/ws", headers={"Origin": "http://evil.example.com"}
        ) as ws:
            ws.receive_json()


def test_websocket_rebuild_events(demo):
    with demo.websocket_connect("/ws") as ws:
        demo.patch("/api/projects/demo/parts/box/params", json={"size": 30.0})
        seen = set()
        for _ in range(10):
            event = ws.receive_json()
            seen.add(event["type"])
            if "rebuild_finished" in seen:
                break
        assert "rebuild_finished" in seen
        disconnect_started = time.monotonic()
    assert time.monotonic() - disconnect_started < 2.0
