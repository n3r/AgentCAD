"""PRD-013 Assembly v2 — slice 7: the structure route pack (REST surface).

`routes_structure.py` is a thin, whitelisted HTTP surface over the structure
tools (`set_pattern`, `add_subassembly`, `set_assembly_interface`,
`export_urdf`) — a route pack, no `app.py` edit. It mounts under `/api` the
ordinary way and raises house errors (a refusal is a 4xx, not a 200 with an
error body). `null` in the pattern body means *clear the pattern*, so it is
forwarded on presence, never stripped.
"""

import shutil

import pytest
from fastapi.testclient import TestClient

from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import make_test_service

pytestmark = pytest.mark.skipif(shutil.which("git") is None,
                                reason="git not found on PATH")

BRACKET = '''\
from build123d import *

PARAMS = {"s": {"default": 8.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Box(p.s, p.s, p.s)
    return part.part

def connectors(p, part):
    return {"seat": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''


@pytest.fixture
def client(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("demo")
    service.create_part("demo", "cube", script=BRACKET)
    service.set_assembly("demo", [{"id": "b", "part": "cube",
                                   "position": [0, 0, 0]}])
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, TestClient(app, base_url="http://127.0.0.1")


def test_set_pattern_route_expands(client):
    service, http = client
    r = http.post("/api/projects/demo/assembly/instances/b/pattern",
                  json={"pattern": {"kind": "linear", "count": 3,
                                    "step_mm": 10}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["instances"]) == 3
    node = next(n for n in body["tree"] if n["id"] == "b")
    assert node["count"] == 3


def test_set_pattern_null_clears(client):
    service, http = client
    http.post("/api/projects/demo/assembly/instances/b/pattern",
              json={"pattern": {"kind": "linear", "count": 3, "step_mm": 10}})
    # null is forwarded (not stripped) — it clears the pattern.
    r = http.post("/api/projects/demo/assembly/instances/b/pattern",
                  json={"pattern": None})
    assert r.status_code == 200, r.text
    assert len(r.json()["instances"]) == 1


def test_set_pattern_bad_kind_is_422(client):
    service, http = client
    r = http.post("/api/projects/demo/assembly/instances/b/pattern",
                  json={"pattern": {"kind": "spiral", "count": 2}})
    assert r.status_code == 422
    # Over HTTP the app serializes the exception class name.
    assert r.json()["error"]["type"] == "ValidationError"


def test_set_pattern_unknown_instance_is_404(client):
    service, http = client
    r = http.post("/api/projects/demo/assembly/instances/nope/pattern",
                  json={"pattern": {"kind": "linear", "count": 2,
                                    "step_mm": 5}})
    assert r.status_code == 404


def test_add_subassembly_route(client):
    service, http = client
    service.create_project("sub")
    service.create_part("sub", "cube", script=BRACKET)
    service.set_assembly("sub", [{"id": "p", "part": "cube"}])
    r = http.post("/api/projects/demo/assembly/subassemblies",
                  json={"id": "u", "source": "sub", "position": [10, 0, 0]})
    assert r.status_code == 200, r.text
    assert "u/p" in {i["id"] for i in r.json()["instances"]}


def test_set_interface_route(client):
    service, http = client
    r = http.put("/api/projects/demo/assembly/interface",
                 json={"exports": {"mount": {"instance": "b",
                                             "connector": "seat"}}})
    assert r.status_code == 200, r.text
    assert "mount" in r.json()["interface"]


def test_export_urdf_route(client):
    service, http = client
    r = http.post("/api/projects/demo/export/urdf", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["links"] == 1 and "path" in body
