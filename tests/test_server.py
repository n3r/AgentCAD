import pytest
from fastapi.testclient import TestClient

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT


@pytest.fixture
def client(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    app = create_app(service, build_registry(service))
    return TestClient(app)


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
    assert len(tools) == 17
    assert all("input_schema" in t for t in tools)

    result = demo.post("/api/tools/list_projects").json()
    assert result["projects"][0]["name"] == "demo"

    result = demo.post("/api/tools/get_metrics", json={"project": "demo", "part_id": "box"}).json()
    assert result["volume_mm3"] > 0


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
