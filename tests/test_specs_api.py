"""Design specs: the tool pack, the rebuild seam and the route pack (slice 4).

The runner itself is ``tests/test_specs.py``; this file covers the *surface*,
in the three sections ``tests/test_versioning_api.py`` uses: 1. registration
(schemas, the description contract, argument validation, the deliberate
NON-self-disable without git, and load order) · 2. the rebuild seam (two
installed wrappers, their idempotence, and the guarantee that a spec-less part
is unchanged) · 3. routes (registry passthroughs, whitelisted bodies).

The seam is the risky part and is written against three invariants:

* **installed exactly once** — a double wrap would double-evaluate;
* **additive** — a successful ``_rebuild`` payload gains ``specs`` and nothing
  else, a failed one gains nothing at all, and a spec-less part costs no kernel
  call and produces the same payload it did before the feature;
* **best effort** — an exception inside the runner is swallowed into the
  payload, because a broken spec layer must never break a rebuild.
"""

from __future__ import annotations

import pkgutil
import shutil

import pytest
from fastapi.testclient import TestClient

import agentcad.core as core_pkg
from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.specs import SpecRunner
from agentcad.core.tools import build_registry
from agentcad.core.tools_specs import install_rebuild_specs
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]

SPEC_TOOLS = ["run_specs", "list_specs", "set_project_specs",
              "get_project_specs"]

# A hollow box: the cavity is what gives check_wall something to measure, and
# ``wall`` is the parameter that drives it red.
SPEC_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import check_mass, check_wall

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 60.0, "unit": "mm"},
          "wall": {"default": 2.5, "min": 0.5, "max": 5.0, "unit": "mm"}}

SPECS = [
    check_wall(min_mm=2.0, grid=4, requirement="ENG-014"),
    check_mass(max_g=500.0, requirement="SYS-042"),
]

def build(p):
    inner = p.size - 2 * p.wall
    return Box(p.size, p.size, p.size) - Box(inner, inner, inner)
'''

PROJECT_SPECS = '''\
from agentcad.toolkit.specs import check_interference_free

SPECS = [check_interference_free(requirement="INT-003")]
'''


@pytest.fixture(autouse=True)
def _reset_context():
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "specs", None) is not None
    return service, registry


@pytest.fixture
def demo(stack):
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box",
                        "script": SPEC_BOX})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "plain",
                        "script": BOX_SCRIPT})
    (service.store.path_of("demo") / "specs.py").write_text(
        PROJECT_SPECS, encoding="utf-8")
    return service, registry


@pytest.fixture
def client(demo):
    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


def _counting(service, monkeypatch) -> dict:
    calls: dict = {}
    original = service.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        calls[method] = calls.get(method, 0) + 1
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", counting)
    return calls


# --------------------------------------------------------- 1. registration


def test_every_spec_tool_is_registered(demo):
    _service, registry = demo
    names = {tool.name for tool in registry.list()}
    assert set(SPEC_TOOLS) <= names
    for name in SPEC_TOOLS:
        tool = registry.get(name)
        assert tool.input_schema["type"] == "object"
        assert "project" in tool.input_schema["properties"]
        assert "project" in tool.input_schema["required"]
        assert tool.description


def test_the_pack_installs_the_runner_seam(demo):
    service, _registry = demo
    assert isinstance(service.specs, SpecRunner)
    # service._spec_cache already means the PARAMS spec cache: the two names
    # must not have collided.
    assert service.specs is not getattr(service, "_spec_cache", None)


def test_tool_descriptions_state_the_status_contract(demo):
    """The four statuses are the whole contract, so the descriptions carry
    them: a failing spec never fails a rebuild, a skip is data with a reason
    and a hint, an error is 'the check broke', and a rebuild evaluates the
    shape tier only while run_specs evaluates everything."""
    _service, registry = demo
    run = registry.get("run_specs").description.lower()
    assert "never fails a rebuild" in run
    assert "reason" in run and "hint" in run
    assert "skip" in run and "error" in run
    assert "shape tier" in run
    assert "all three tiers" in run

    listing = registry.get("list_specs").description.lower()
    assert "no build" in listing or "never builds" in listing

    writer = registry.get("set_project_specs").description.lower()
    assert "specs.py" in writer
    assert "post-state" in writer or "declarations" in writer


def test_argument_validation(demo):
    _service, registry = demo
    assert registry.call("run_specs", {})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("run_specs", {"project": "demo", "part_id": 3})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("list_specs", {"project": "demo", "junk": 1})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("set_project_specs", {"project": "demo"})[
        "error"]["type"] == "invalid_arguments"


@pytest.mark.slow
def test_the_pack_does_not_self_disable_without_git(kernel, tmp_path,
                                                    monkeypatch):
    """The deliberate difference from tools_proposals/tools_versioning: specs
    are a property of the working tree, so only ``ref`` needs git."""
    monkeypatch.setattr(
        "agentcad.core.history.ProjectHistory.available", lambda self: False)
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    names = {tool.name for tool in registry.list()}
    assert set(SPEC_TOOLS) <= names
    assert getattr(service, "branches", None) is None

    registry.call("create_project", {"name": "demo"})
    registry.call("create_part", {"project": "demo", "part_id": "box",
                                  "script": SPEC_BOX})
    report = registry.call("run_specs", {"project": "demo"})
    assert "error" not in report, report
    assert report["status"] == "green"
    assert "error" not in registry.call("list_specs", {"project": "demo"})

    refused = registry.call("run_specs", {"project": "demo", "ref": "feat"})
    assert refused["error"]["type"] == "validation_error"
    assert "git" in refused["error"]["message"]


def test_the_pack_sorts_after_proposals_and_before_stackup_and_versioning():
    """``_load_tool_packs`` walks ``pkgutil.iter_modules`` alphabetically:
    ``service.gate_providers`` exists by the time this pack runs (slice 5 needs
    it), while ``tolerance_stackup`` and ``service.branches`` do not — which is
    why the runner calls ``compute_stackup`` directly and reads ``branches``
    inside its methods."""
    packs = [info.name for info in pkgutil.iter_modules(core_pkg.__path__)
             if info.name.startswith("tools_")]
    assert packs.index("tools_proposals") < packs.index("tools_specs")
    assert packs.index("tools_specs") < packs.index("tools_stackup")
    assert packs.index("tools_specs") < packs.index("tools_versioning")


@pytest.mark.slow
def test_a_freshly_built_registry_serves_run_specs_immediately(kernel,
                                                               tmp_path):
    """The pack must not touch ``service.branches`` at register() time: a
    registry built one line earlier serves a full report. (The other half of
    the load-order rule — a project-scope stackup check reaching
    ``compute_stackup`` directly rather than the not-yet-registered
    ``tolerance_stackup`` tool — is ``tests/test_specs.py``'s
    ``test_run_reports_the_stackup_from_the_mate_chain``.)"""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    registry.call("create_project", {"name": "demo"})
    registry.call("create_part", {"project": "demo", "part_id": "box",
                                  "script": SPEC_BOX})
    report = registry.call("run_specs", {"project": "demo"})
    assert "error" not in report, report
    assert [check["id"] for check in report["checks"]] == [
        "box:wall_min", "box:mass_max"]


# ----------------------------------------------------- 2. the rebuild seam


@pytest.mark.slow
def test_install_rebuild_specs_is_idempotent(demo, monkeypatch):
    service, _registry = demo
    rebuild, get_part = service._rebuild, service.get_part

    install_rebuild_specs(service)
    install_rebuild_specs(service)

    assert service._rebuild is rebuild          # not re-wrapped
    assert service.get_part is get_part
    calls = _counting(service, monkeypatch)
    result = service.set_params("demo", "box", {"size": 21.0})
    assert result["specs"]["summary"]["total"] == 2
    assert calls["spec_eval"] == 1              # not evaluated twice


@pytest.mark.slow
def test_the_wrapper_survives_a_second_registration(demo):
    """``build_registry`` may run again over the same service (the tests do
    it); the wrapper must resolve ``service.specs`` at call time rather than
    capture the runner it was installed with."""
    service, _registry = demo
    build_registry(service)
    result = service._rebuild("demo", "box")
    assert result["specs"]["status"] == "green"


@pytest.mark.slow
def test_a_successful_rebuild_gains_specs_and_nothing_else(demo):
    service, _registry = demo
    raw = service._rebuild.__wrapped__("demo", "box")
    wrapped = service._rebuild("demo", "box")

    assert set(wrapped) == set(raw) | {"specs"}
    assert {k: v for k, v in wrapped.items() if k != "specs"} == raw
    assert wrapped["specs"]["status"] == "green"
    assert [c["id"] for c in wrapped["specs"]["checks"]] == [
        "box:wall_min", "box:mass_max"]


@pytest.mark.slow
def test_a_failed_rebuild_carries_no_specs_key(demo):
    """There is no geometry to assert over, and a spec block beside a build
    failure would compete with with_hint's 'fix the script first'."""
    _service, registry = demo
    broken = registry.call("update_part_script", {
        "project": "demo", "part_id": "box",
        "script": "def build(p):\n    raise RuntimeError('nope')\n"})
    assert broken["ok"] is False
    assert "specs" not in broken


@pytest.mark.slow
def test_get_part_on_a_part_that_does_not_build_carries_no_specs_key(
        demo, monkeypatch):
    """The same rule on the read side: no shape to assert over, the build error
    is already the message to act on, and evaluating would pay for the failing
    build again on every read."""
    _service, registry = demo
    assert registry.call("update_part_script", {
        "project": "demo", "part_id": "box",
        "script": SPEC_BOX + "\nraise RuntimeError('nope')\n"})["ok"] is False

    detail = registry.call("get_part", {"project": "demo", "part_id": "box"})
    assert detail["status"]["state"] == "error"
    assert "specs" not in detail


@pytest.mark.slow
def test_a_spec_less_part_is_unchanged_and_costs_no_kernel_call(
        demo, monkeypatch):
    """The guard against wrapper drift: the presence scan is an ast.parse, so
    a part that declares nothing reaches no kernel call at all, and its payload
    is the pre-feature payload plus an explicit ``specs: null``."""
    service, _registry = demo
    raw = service._rebuild.__wrapped__("demo", "plain")
    calls = _counting(service, monkeypatch)
    service._status.clear()                     # force the full cache-key path

    wrapped = service._rebuild("demo", "plain")

    assert wrapped["specs"] is None
    assert {k: v for k, v in wrapped.items() if k != "specs"} == raw
    assert calls.get("spec_eval", 0) == 0
    assert calls.get("spec_declare", 0) == 0


@pytest.mark.slow
def test_the_rebuild_returning_tools_carry_the_post_state_summary(demo):
    service, registry = demo
    thinned = registry.call("set_params", {"project": "demo",
                                           "part_id": "box",
                                           "values": {"wall": 1.0}})
    assert thinned["ok"] is True                # AC2: the geometry still lands
    assert thinned["specs"]["status"] == "red"
    assert thinned["specs"]["checks"][0]["status"] == "fail"
    assert thinned["specs"]["requirements"]["ENG-014"]["status"] == "fail"
    assert (service.store.cache_dir("demo")
            / f"{thinned['cache_key']}.acm").is_file()

    rewritten = registry.call("update_part_script", {
        "project": "demo", "part_id": "box", "script": SPEC_BOX})
    assert rewritten["specs"]["status"] == "red"    # the override still holds


@pytest.mark.slow
def test_get_part_carries_specs_beside_metrics_without_rebuilding(
        demo, monkeypatch):
    service, registry = demo
    service._rebuild("demo", "box")             # warm: mesh + sidecar on disk
    calls = _counting(service, monkeypatch)

    detail = registry.call("get_part", {"project": "demo", "part_id": "box"})

    assert detail["metrics"]["volume_mm3"] > 0
    assert detail["specs"]["status"] == "green"
    assert detail["specs"]["cached"] is True
    assert calls == {}                          # no build, no spec_eval
    # additive for the UI: the pre-feature detail, plus one key
    raw = service.get_part.__wrapped__("demo", "box")
    assert set(detail) == set(raw) | {"specs"}
    assert {k: v for k, v in detail.items() if k != "specs"} == raw
    assert registry.call("get_part", {"project": "demo",
                                      "part_id": "plain"})["specs"] is None


@pytest.mark.slow
def test_an_exception_in_the_runner_never_escapes_a_rebuild(demo, monkeypatch):
    """A broken spec layer must never break a rebuild — the whole point of a
    wrapper that is strictly best-effort."""
    service, registry = demo

    def exploding(*args, **kwargs):
        raise RuntimeError("the spec layer is on fire")

    monkeypatch.setattr(service.specs, "tier1", exploding)
    result = registry.call("set_params", {"project": "demo",
                                          "part_id": "box",
                                          "values": {"size": 22.0}})

    assert result["ok"] is True
    assert result["specs"]["status"] == "error"
    assert "on fire" in result["specs"]["error"]["message"]
    assert result["specs"]["error"]["type"] == "RuntimeError"
    detail = registry.call("get_part", {"project": "demo", "part_id": "box"})
    assert detail["specs"]["status"] == "error"


@pytest.mark.slow
def test_the_browsers_own_part_routes_carry_specs(client):
    """The reason the seam wraps ``_rebuild`` and ``get_part`` rather than the
    three rebuild-returning tools: ``PATCH …/params`` calls
    ``service.set_params`` **directly**, not through the registry, and the
    inspector chips ride ``GET …/parts/{id}`` after ``rebuild_finished``. A
    tool wrapper would miss the UI entirely."""
    _service, _registry, http = client

    patched = http.patch("/api/projects/demo/parts/box/params",
                         json={"wall": 1.0})
    assert patched.status_code == 200, patched.text
    assert patched.json()["ok"] is True
    assert patched.json()["specs"]["status"] == "red"
    assert patched.json()["specs"]["checks"][0]["name"] == "wall_min"

    detail = http.get("/api/projects/demo/parts/box")
    assert detail.status_code == 200, detail.text
    assert detail.json()["specs"]["status"] == "red"
    assert detail.json()["specs"]["checks"][0]["location"] is not None
    assert http.get("/api/projects/demo/parts/plain").json()["specs"] is None


# --------------------------------------------------------------- 3. routes


@pytest.mark.slow
def test_the_spec_routes_round_trip(client):
    _service, _registry, http = client

    declared = http.get("/api/projects/demo/specs")
    assert declared.status_code == 200, declared.text
    payload = declared.json()
    assert payload["declared"] == 3
    assert [d["name"] for d in payload["parts"]["box"]["specs"]] == [
        "wall_min", "mass_max"]
    assert payload["project_specs"]["specs"][0]["name"] == "no_interference"
    assert payload["requirements"]["ENG-014"] == ["box:wall_min"]

    one_part = http.get("/api/projects/demo/specs",
                        params={"part_id": "box"}).json()
    assert list(one_part["parts"]) == ["box"]
    assert one_part["project_specs"]["specs"] == []

    report = http.post("/api/projects/demo/specs/run", json={})
    assert report.status_code == 200, report.text
    assert report.json()["status"] == "green"
    assert report.json()["summary"]["total"] == 3

    scoped = http.post("/api/projects/demo/specs/run",
                       json={"part_id": "box"}).json()
    assert list(scoped["parts"]) == ["box"]
    assert scoped["project_checks"]["checks"] == []


@pytest.mark.slow
def test_the_project_specs_file_routes_read_and_write(client):
    service, _registry, http = client

    read = http.get("/api/projects/demo/specs/file")
    assert read.status_code == 200, read.text
    assert read.json()["script"] == PROJECT_SPECS
    assert read.json()["path"] == "specs.py"
    assert [d["name"] for d in read.json()["specs"]] == ["no_interference"]

    written = http.put("/api/projects/demo/specs/file",
                       json={"script": PROJECT_SPECS.replace(
                           "INT-003", "INT-004")})
    assert written.status_code == 200, written.text
    assert written.json()["declared"] == 1
    assert written.json()["specs"][0]["requirement"] == "INT-004"
    assert (service.store.path_of("demo") / "specs.py").read_text(
        encoding="utf-8").count("INT-004") == 1

    emptied = http.put("/api/projects/demo/specs/file", json={"script": ""})
    assert emptied.status_code == 200, emptied.text
    assert emptied.json() == {"path": "specs.py", "exists": False,
                              "script": None, "declared": 0, "specs": [],
                              "declaration_error": None, "warnings": []}
    assert not (service.store.path_of("demo") / "specs.py").exists()
    assert http.get("/api/projects/demo/specs/file").json()["script"] is None


@pytest.mark.slow
def test_unknown_body_keys_are_ignored_and_nulls_are_not_forwarded(client):
    _service, _registry, http = client
    response = http.post(
        "/api/projects/demo/specs/run",
        json={"evil": "rm -rf", "project": "other", "part_id": None,
              "ref": None})
    assert response.status_code == 200, response.text
    assert response.json()["project"] == "demo"
    assert set(response.json()["parts"]) == {"box"}


@pytest.mark.slow
def test_a_body_without_a_content_length_is_still_read(client):
    """A chunked request carries no ``content-length``: trusting the header
    would turn this body into 'no arguments at all' — a missing script rather
    than a write."""
    _service, _registry, http = client

    def chunks():
        yield b'{"script": "from agentcad.toolkit.specs import '
        yield b'check_interference_free\\nSPECS = [check_interference_free()]\\n"}'

    response = http.put("/api/projects/demo/specs/file", content=chunks(),
                        headers={"Content-Type": "application/json"})

    assert "content-length" not in response.request.headers
    assert response.status_code == 200, response.text
    assert response.json()["declared"] == 1


@pytest.mark.slow
def test_route_error_mapping(client):
    _service, _registry, http = client
    assert http.get("/api/projects/ghost/specs").status_code == 404
    assert http.get("/api/projects/demo/specs",
                    params={"part_id": "ghost"}).status_code == 404
    assert http.post("/api/projects/demo/specs/run",
                     json={"part_id": "ghost"}).status_code == 404
    assert http.get("/api/projects/ghost/specs/file").status_code == 404
    # a missing required body key is invalid_arguments — a 422, never a 200
    # body nobody inspects
    bad = http.put("/api/projects/demo/specs/file", json={})
    assert bad.status_code == 422, bad.text
    assert bad.json()["error"]["message"]


@pytest.mark.slow
def test_a_broken_specs_py_is_written_and_reported_over_http(client):
    """The ``update_part_script`` precedent: you must be able to save a broken
    file in order to fix it."""
    service, _registry, http = client
    response = http.put("/api/projects/demo/specs/file",
                        json={"script": "SPECS = [oops\n"})

    assert response.status_code == 200, response.text
    assert response.json()["specs"] == []
    assert response.json()["declaration_error"]["type"] == "script_error"
    assert (service.store.path_of("demo") / "specs.py").read_text(
        encoding="utf-8") == "SPECS = [oops\n"


class TestRefRoutes:
    """``ref`` is the one argument that needs git (PRD-001 X1: a tag must never
    answer for a branch)."""

    pytestmark = _GIT + [pytest.mark.slow]

    def test_an_unknown_ref_is_a_404_and_a_tag_is_a_422(self, client):
        service, _registry, http = client
        assert http.post("/api/projects/demo/specs/run",
                         json={"ref": "ghost"}).status_code == 404

        service.branches.tag("demo", "shop-rev-a")
        tagged = http.post("/api/projects/demo/specs/run",
                           json={"ref": "shop-rev-a"})
        assert tagged.status_code == 422, tagged.text
        assert "tag" in tagged.json()["error"]["message"]
