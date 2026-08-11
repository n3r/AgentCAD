"""Geometry CI: the tool pack, the route pack and ``check_finished`` (slice 5).

Slice 4 gave the pipeline a command; this is the slice that makes it reachable
by an agent and by the browser, so what is pinned here is the *surface*, in the
four sections ``tests/test_specs_api.py`` uses: 1. registration (the schema,
the description contract, argument validation, the non-self-disable without
git) · 2. **load order**, which is the one thing about this pack that is not
obvious from reading it · 3. the run seam (``check_finished``, the last-report
cache) · 4. routes.

The load-order section is the reason the pack is called
``tools_run_checks.py``. ``tools._load_tool_packs`` walks
``pkgutil.iter_modules`` alphabetically and ``tools_proposals.register``
assigns ``service.gate_providers = []`` **unconditionally**, so a pack named
``tools_checks.py`` would load at ``c``, before ``p``, and slice 6's gate
provider would be silently thrown away. Both halves of that claim are asserted
here — the ordinal one against the real package, and the destructive one by
watching a sentinel provider disappear — because the failure it prevents is
invisible: no error, no warning, just a gate that never appears.
"""

from __future__ import annotations

import bisect
import json
import os
import pkgutil
import queue
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agentcad.core as core_pkg
from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.checks import LAST_REPORTS, STAGES, CheckRunner
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

BROKEN_SCRIPT = '''\
from build123d import *

PARAMS = {"size": {"default": 10.0, "min": 1.0, "max": 100.0, "unit": "mm"}}

def build(p):
    return Box(p.size, p.size, no_such_name)
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
    """The real service (history and the event bus live), not
    ``make_test_service`` — this file is about what gets published."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    return service, registry


@pytest.fixture
def demo(stack):
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box",
                        "script": BOX_SCRIPT})
    return service, registry


@pytest.fixture
def client(demo):
    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


def _stage(report: dict, name: str) -> dict:
    return next(s for s in report["stages"] if s["name"] == name)


def _drain(subscription) -> list[dict]:
    """Every event published since ``bus.subscribe()``, in order."""
    events = []
    while True:
        try:
            events.append(subscription.get_nowait())
        except queue.Empty:
            return events


# --------------------------------------------------------- 1. registration


def test_run_checks_is_registered_with_a_whitelisted_schema(demo):
    _service, registry = demo
    tool = registry.get("run_checks")
    assert tool is not None
    assert tool.input_schema["type"] == "object"
    assert tool.input_schema["required"] == ["project"]
    assert set(tool.input_schema["properties"]) == {
        "project", "ref", "stages", "strict", "budget", "proposal"}
    types = {k: v["type"] for k, v in tool.input_schema["properties"].items()}
    assert types == {"project": "string", "ref": "string", "stages": "array",
                     "strict": "boolean", "budget": "number",
                     "proposal": "string"}


def test_the_tool_description_states_the_status_contract(demo):
    """A red check is DATA. An agent that reads the description must not have
    to discover that by catching an exception that never comes."""
    _service, registry = demo
    text = registry.get("run_checks").description.lower()
    for status in ("pass", "fail", "skip", "error"):
        assert status in text
    assert "data" in text and "never an error" in text
    assert "exit_code" in text
    for stage in STAGES:
        assert stage in text


def test_the_pack_installs_the_runner_seam(demo):
    service, _registry = demo
    assert isinstance(service.checks, CheckRunner)
    # The CLI prefers this runner precisely so a CLI run and a tool run share
    # one cache and one publisher (slice 4's `getattr(service, "checks", None)`).
    assert service.checks.service is service
    assert service.checks._registry is not None


def test_argument_validation(demo):
    _service, registry = demo
    assert registry.call("run_checks", {})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("run_checks", {"project": "demo", "junk": 1})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("run_checks", {"project": "demo", "stages": "build"})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("run_checks", {"project": "demo", "strict": "yes"})[
        "error"]["type"] == "invalid_arguments"


def test_an_unknown_project_is_payload_not_a_raise(demo):
    """The harness families only — no new error type, and never an exception
    out of ``registry.call``."""
    _service, registry = demo
    result = registry.call("run_checks", {"project": "nope"})
    assert result["error"]["type"] == "notfound_error"


def test_an_unknown_stage_is_a_validation_error(demo):
    _service, registry = demo
    result = registry.call("run_checks",
                           {"project": "demo", "stages": ["bogus"]})
    assert result["error"]["type"] == "validation_error"
    assert "bogus" in result["error"]["message"]


def test_an_explicitly_empty_stage_list_is_a_validation_error(demo):
    """Review C10: ``stages: []`` is falsy, so ``tuple(stages) if stages else
    STAGES`` read an explicit "nothing" as "everything" and launched the whole
    multi-minute pipeline — while the CLI rejected an empty ``--stages`` and the
    runner's own contract is that an empty tuple selects **none**. Three answers
    to one question; the boundary now gives the CLI's."""
    _service, registry = demo

    result = registry.call("run_checks", {"project": "demo", "stages": []})

    assert result["error"]["type"] == "validation_error"
    assert "stages" in result["error"]["message"]
    for stage in STAGES:
        assert stage in result["error"]["message"]


def test_the_runner_itself_still_lets_an_empty_selection_select_nothing(demo):
    """The direct caller's contract, stated so the boundary rule above reads as
    a decision rather than an inconsistency: ``CheckRunner.run(stages=())``
    measures nothing and reports all four stages as ``not_selected``."""
    service, _registry = demo

    report = service.checks.run("demo", stages=())

    assert [s["reason"] for s in report["stages"]] == ["not_selected"] * 4
    assert report["summary"]["total"] == 0


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1.0])
def test_the_tool_refuses_a_non_finite_budget(demo, budget):
    """Review C9 at the boundary an agent uses: ``json.loads`` accepts the bare
    ``NaN`` literal, so a REST or MCP caller can send one — and a NaN deadline
    is never in the past, so it bounds nothing while the report still claims
    ``complete: true``."""
    _service, registry = demo

    result = registry.call("run_checks", {"project": "demo", "budget": budget})

    assert result["error"]["type"] == "validation_error"
    assert "finite" in result["error"]["message"]


@pytest.mark.portability
def test_the_pack_does_not_self_disable_without_git(kernel, tmp_path,
                                                    monkeypatch):
    """Like ``tools_specs`` and unlike ``tools_proposals``: a check is a
    property of the working tree, and only ``ref`` needs git."""
    monkeypatch.setattr(
        "agentcad.core.history.ProjectHistory.available", lambda self: False)
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert registry.get("run_checks") is not None
    assert isinstance(service.checks, CheckRunner)
    # No proposals without git, so there is nothing to gate — which slice 6's
    # `install_checks_gate` has to tolerate rather than assume.
    assert getattr(service, "gate_providers", None) is None


# ----------------------------------------------------------- 2. load order


def test_the_pack_loads_after_proposals_and_before_specs():
    """The ordinal half of the naming rule, against the real package."""
    packs = [info.name for info in pkgutil.iter_modules(core_pkg.__path__)
             if info.name.startswith("tools_")]
    assert "tools_run_checks" in packs
    assert "tools_checks" not in packs, (
        "a pack named tools_checks.py loads before tools_proposals, which "
        "resets service.gate_providers — see the module docstring")
    assert packs.index("tools_proposals") < packs.index("tools_run_checks")
    assert packs.index("tools_run_checks") < packs.index("tools_specs")
    assert packs.index("tools_run_checks") < packs.index("tools_versioning")
    # And the counterfactual, so the name is defended rather than merely
    # observed: `tools_checks` would sort BEFORE `tools_proposals`.
    assert bisect.bisect(packs, "tools_checks") <= packs.index(
        "tools_proposals")


@pytest.mark.integration
@pytest.mark.portability
@pytest.mark.skipif(shutil.which("git") is None,
                    reason="git not found on PATH")
def test_tools_proposals_discards_a_provider_installed_before_it(kernel,
                                                                tmp_path):
    """The destructive half: ``tools_proposals.register`` assigns
    ``service.gate_providers = []`` unconditionally, so anything a
    lexicographically earlier pack appended is gone without a trace. This is
    the exact fate a ``tools_checks.py`` would suffer, and it is why slice 6's
    gate can be installed from ``register()`` at all."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())

    def sentinel(project, proposal):  # pragma: no cover — never called
        return None

    service.gate_providers = [sentinel]
    build_registry(service)
    assert service.gate_providers is not None
    assert sentinel not in service.gate_providers


@pytest.mark.slow
def test_the_runner_reads_service_specs_lazily(demo):
    """``tools_specs`` loads at ``s``, after this pack at ``r``, so a runner
    that captured ``service.specs`` in ``__init__`` would hold ``None``
    forever and every specs stage would be ``skip/specs_unavailable``."""
    _service, registry = demo
    report = registry.call("run_checks", {"project": "demo"})
    assert "error" not in report, report
    specs = _stage(report, "specs")
    assert specs["reason"] != "specs_unavailable"
    assert specs["reason"] == "not_declared"


# ------------------------------------------------- 3. the run seam + event


@pytest.mark.slow
def test_a_run_publishes_check_finished(demo):
    service, registry = demo
    queue = service.bus.subscribe()
    report = registry.call("run_checks", {"project": "demo"})
    assert "error" not in report, report

    finished = [e for e in _drain(queue) if e["type"] == "check_finished"]
    assert len(finished) == 1
    event = finished[0]
    assert set(event) == {"type", "project", "ref", "status", "exit_code",
                          "summary", "duration_s"}
    assert event["project"] == "demo" and event["ref"] is None
    assert event["status"] == report["status"] == "green"
    assert event["exit_code"] == report["exit_code"] == 0
    assert event["summary"] == report["summary"]


@pytest.mark.slow
def test_a_red_run_publishes_check_finished_too(demo):
    """Including red and budget-truncated: a UI that only learns about green
    runs would leave a stale badge exactly when it matters."""
    service, registry = demo
    registry.call("update_part_script", {"project": "demo", "part_id": "box",
                                         "script": BROKEN_SCRIPT})
    queue = service.bus.subscribe()
    report = registry.call("run_checks", {"project": "demo",
                                          "stages": ["build"]})
    assert report["status"] == "red" and report["exit_code"] == 1

    finished = [e for e in _drain(queue) if e["type"] == "check_finished"]
    assert [(e["status"], e["exit_code"]) for e in finished] == [("red", 1)]


@pytest.mark.slow
def test_check_finished_does_not_snapshot_the_project(demo):
    """It is not ``project_changed``, so ``_snapshot_on_event`` must ignore
    it — measuring a project is not changing it."""
    service, registry = demo
    canonical = service.store.canonical_path_of("demo")
    before = len(service.history.log(canonical, limit=100))
    registry.call("run_checks", {"project": "demo"})
    assert len(service.history.log(canonical, limit=100)) == before


@pytest.mark.slow
def test_the_last_report_cache_holds_the_last_report_per_project(demo):
    service, registry = demo
    assert service.checks.last == {}
    first = registry.call("run_checks", {"project": "demo",
                                         "stages": ["build"]})
    assert service.checks.last["demo"] is first
    second = registry.call("run_checks", {"project": "demo",
                                          "stages": ["build"]})
    assert service.checks.last["demo"] is second


def test_the_last_report_cache_is_bounded(demo):
    """In-memory and per process: a long-lived server that checked a hundred
    projects must not hold a hundred reports."""
    service, _registry = demo
    for index in range(LAST_REPORTS + 3):
        service.checks._remember(f"p{index}", {"project": f"p{index}"})
    assert len(service.checks.last) == LAST_REPORTS
    assert "p0" not in service.checks.last
    assert f"p{LAST_REPORTS + 2}" in service.checks.last


# ---------------------------------------------------------------- 4. routes


@pytest.mark.slow
def test_post_runs_a_check_and_returns_the_report(client):
    _service, _registry, http = client
    response = http.post("/api/projects/demo/checks", json={})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["schema"] == 1 and report["project"] == "demo"
    assert [s["name"] for s in report["stages"]] == list(STAGES)


@pytest.mark.slow
def test_post_whitelists_the_body(client):
    """Unknown keys are dropped at the route (never ``**body``) and refused at
    the registry — the two layers ``routes_specs`` established."""
    _service, registry, http = client
    response = http.post("/api/projects/demo/checks",
                         json={"stages": ["build"], "strict": True,
                               "junk": "ignored", "verify_determinism": True})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["strict"] is True
    assert _stage(report, "assembly")["reason"] == "not_selected"
    assert not any(s["name"] == "determinism" for s in report["stages"])
    assert registry.call("run_checks", {"project": "demo", "junk": 1})[
        "error"]["type"] == "invalid_arguments"


@pytest.mark.slow
def test_post_maps_errors_the_ordinary_way(client):
    _service, _registry, http = client
    assert http.post("/api/projects/nope/checks", json={}).status_code == 404
    bad = http.post("/api/projects/demo/checks", json={"stages": ["bogus"]})
    assert bad.status_code == 422
    assert "bogus" in bad.text


@pytest.mark.slow
def test_get_is_404_before_a_run_and_the_report_after(client):
    _service, _registry, http = client
    missing = http.get("/api/projects/demo/checks")
    assert missing.status_code == 404

    posted = http.post("/api/projects/demo/checks",
                       json={"stages": ["build"]})
    assert posted.status_code == 200, posted.text
    got = http.get("/api/projects/demo/checks")
    assert got.status_code == 200
    assert got.json() == posted.json()


@pytest.mark.slow
def test_a_second_client_sees_check_finished(client):
    _service, _registry, http = client
    with http.websocket_connect("/ws") as ws:
        response = http.post("/api/projects/demo/checks",
                             json={"stages": ["build"]})
        assert response.status_code == 200, response.text
        seen = []
        for _ in range(10):
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "check_finished":
                break
    assert seen[-1]["type"] == "check_finished"
    assert seen[-1]["project"] == "demo"
    assert seen[-1]["status"] == response.json()["status"]


# ------------------------------------------------------------------- AC9


def _normalize(report: dict) -> dict:
    """Everything that cannot be identical between two runs of the same
    project: the clock, the host block, and every duration."""
    stages = []
    for stage in report["stages"]:
        stages.append({**stage, "duration_s": 0.0})
    return {**report, "started": "", "finished": "", "duration_s": 0.0,
            "host": {}, "stages": stages}


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.timeout(900)
def test_the_cli_and_the_tool_report_the_same_thing(demo, tmp_path):
    """AC9: same report everywhere. The CLI and the tool drive the *same*
    ``CheckRunner`` code over the same project; the only differences a
    consumer may see are the clock, the host and the durations."""
    service, registry = demo
    projects = str(service.store.root)

    # Warm the cache first, so `details.cached` is true in both reports rather
    # than recording which run happened to be first.
    assert "error" not in registry.call("run_checks", {"project": "demo"})

    script = Path(sys.executable).with_name("agentcad")
    argv = ([str(script)] if script.exists()
            else [sys.executable, "-c", "from agentcad.cli import main; main()"])
    out = tmp_path / "report.json"
    env = dict(os.environ)
    env["AGENTCAD_KERNEL_POOL_SIZE"] = "1"
    result = subprocess.run(
        argv + ["check", "--project", "demo", "--projects-dir", projects,
                "--report", str(out), "--quiet"],
        capture_output=True, text=True, timeout=600, env=env)
    assert result.returncode == 0, result.stderr

    from_cli = json.loads(out.read_text())
    from_tool = registry.call("run_checks", {"project": "demo"})
    assert "error" not in from_tool, from_tool
    assert _normalize(from_cli) == _normalize(from_tool)
