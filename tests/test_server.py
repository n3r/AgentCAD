import pytest
from fastapi.testclient import TestClient

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT


@pytest.fixture
def client(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
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


def test_tools_endpoints(demo):
    tools = demo.get("/api/tools").json()["tools"]
    assert len(tools) >= 25  # 17 core + v2 packs
    assert all("input_schema" in t for t in tools)

    result = demo.post("/api/tools/list_projects").json()
    assert result["projects"][0]["name"] == "demo"

    result = demo.post("/api/tools/get_metrics", json={"project": "demo", "part_id": "box"}).json()
    assert result["volume_mm3"] > 0


def test_host_header_guard(kernel, tmp_path):
    service = AgentCADService(tmp_path / "p2", kernel, EventBus())
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
