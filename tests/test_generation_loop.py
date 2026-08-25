"""PRD-018 slice 1: the generation loop, budget/termination, half-write (FR3-5).

Driven by the proven FakeMessages harness (the spike's template) against the
REAL kernel — build123d actually runs, no network. The fake scripts the model's
tool_use turns; the loop's own mechanical render+measure+specs are NOT scripted,
so any render_view/get_metrics/run_specs dispatch is proof the loop injected it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentcad.agent.generate import (
    ALLOWED_TOOLS,
    Budget,
    GenerationLoop,
    cleanup_scratch,
    run_generation,
)
from agentcad.core import tenancy
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import make_test_service

PROJECT = "genproj"

GREEN_SCRIPT = '''\
from build123d import Box
from agentcad.toolkit.specs import check_valid, check_mass

PARAMS = {"w": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm"}}
SPECS = [check_valid(name="valid"), check_mass(max_g=1000.0, name="light")]

def build(p):
    return Box(p.w, p.w, p.w)
'''

# Valid geometry, but the mass spec is impossible (a 20mm Al cube is ~21.6 g),
# so run_specs is red and the loop can never terminate green on it.
FAIL_SPEC_SCRIPT = '''\
from build123d import Box
from agentcad.toolkit.specs import check_valid, check_mass

PARAMS = {"w": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm"}}
SPECS = [check_valid(name="valid"), check_mass(max_g=1.0, name="featherweight")]

def build(p):
    return Box(p.w, p.w, p.w)
'''

BROKEN_SCRIPT = '''\
PARAMS = {}

def build(p):
    raise RuntimeError("boom: this candidate never builds")
'''


# ---- the minimal fake-client contract (the spike's harness) ------------------

def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _response(blocks, stop_reason="tool_use"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        assert self._responses, "fake ran out of scripted responses"
        return self._responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


class AlwaysMessages:
    """Returns the SAME response on every call (a model stuck in a loop)."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class AlwaysAnthropic:
    def __init__(self, response):
        self.messages = AlwaysMessages(response)


def _create(script, part_id="draft", id="tu_create"):
    return _tool_use(id, "create_part",
                     {"project": PROJECT, "part_id": part_id, "script": script})


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.fixture()
def stack(tmp_path, kernel):
    bus = EventBus()
    service = make_test_service(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": PROJECT})
    return service, registry, bus


def _run(coro):
    return asyncio.run(coro)


# ============================================================ AC4 exit: success

def test_success_terminates_spec_green(stack):
    service, registry, bus = stack
    fake = FakeAnthropic([_response([_text("Drafting a cube."),
                                     _create(GREEN_SCRIPT)])])
    queue = bus.subscribe()

    result = _run(run_generation(
        service, registry, project=PROJECT, prompt="a 20mm cube under 1kg",
        client_factory=lambda: fake, gen_id="okgen", bus=bus,
        budget=Budget(max_iterations=6, wall_clock_s=60)))

    cand = result["candidates"][0]
    assert cand["terminal_state"] == "spec_green"
    assert cand["spec_green"] is True
    assert cand["metrics"]["is_valid"] is True
    assert cand["spec_report"]["status"] == "green"
    assert cand["script"] == GREEN_SCRIPT
    assert result["best"] == 0

    # The loop performed look-and-measure itself: the fake NEVER emitted a
    # render_view / get_metrics / run_specs tool_use, so every such dispatch is
    # the loop's. Each is flagged auto.
    events = _drain(queue)
    auto = [e for e in events if e["type"] == "chat_tool_call" and e.get("auto")]
    auto_names = {e["name"] for e in auto}
    assert {"render_view", "get_metrics", "run_specs"} <= auto_names
    log = cand["iteration_log"][0]
    assert log["rendered"] and log["measured"] and log["specs_run"]
    assert log["wrote_script"] and log["kernel_valid"] is True
    assert log["stop_reason"] == "spec_green"


# ============================================ AC3/AC4 exit: budget best-so-far

def test_budget_one_returns_best_so_far_with_named_failures(stack):
    service, registry, bus = stack
    fake = FakeAnthropic([_response([_create(FAIL_SPEC_SCRIPT)])])

    result = _run(run_generation(
        service, registry, project=PROJECT, prompt="feather cube",
        client_factory=lambda: fake, gen_id="budgen", bus=bus,
        budget={"max_iterations": 1, "wall_clock_s": 60}))

    cand = result["candidates"][0]
    assert cand["terminal_state"] == "budget_exhausted"
    assert cand["spec_green"] is False
    # best-so-far: kernel-valid geometry retained, its failing checks NAMED.
    assert cand["metrics"]["is_valid"] is True
    assert cand["script"] == FAIL_SPEC_SCRIPT
    assert cand["failing_checks"], "the impossible mass spec must be named"
    assert any("featherweight" in c for c in cand["failing_checks"])
    # exactly one model turn was bought; the log records the one write
    # iteration plus a budget-stop marker naming the reason.
    assert len(fake.messages.calls) == 1
    write_iters = [e for e in cand["iteration_log"] if e.get("wrote_script")]
    assert len(write_iters) == 1
    assert any(e.get("stop_reason") == "max_iterations"
               for e in cand["iteration_log"])


# ================================================ AC4 exit: abandonment + FR5

def test_abandonment_preserves_error_and_siblings_continue(stack):
    service, registry, bus = stack
    broken = AlwaysAnthropic(_response([_create(BROKEN_SCRIPT)]))
    good = FakeAnthropic([_response([_create(GREEN_SCRIPT)])])

    def factory(candidate):
        return broken if candidate == 0 else good

    result = _run(run_generation(
        service, registry, project=PROJECT, prompt="a cube",
        client_factory=factory, candidates=2, gen_id="abgen", bus=bus,
        budget=Budget(max_iterations=8, wall_clock_s=60)))

    c0, c1 = result["candidates"]
    # the crashing candidate is abandoned, its structured error preserved
    assert c0["terminal_state"] == "abandoned"
    assert c0["spec_green"] is False
    assert c0["error"] is not None
    assert isinstance(c0["error"], dict) and c0["error"].get("type")
    assert any(e.get("phase") == "abandoned" or e.get("stop_reason") == "abandoned"
               or e.get("error") for e in c0["iteration_log"])
    # abandonment fired after the consecutive-error threshold, not on turn 1
    assert len(c0["iteration_log"]) >= 3
    # the sibling was unaffected and reached green
    assert c1["terminal_state"] == "spec_green"
    assert c1["spec_green"] is True
    assert result["best"] == 1


# =================================================== half-write / cleanup (AC3)

def test_cleanup_removes_all_scratch_ids(stack):
    service, registry, bus = stack
    fake = FakeAnthropic([_response([_create(FAIL_SPEC_SCRIPT)])])
    gen_id = "cleangen"

    result = _run(run_generation(
        service, registry, project=PROJECT, prompt="x",
        client_factory=lambda: fake, gen_id=gen_id, bus=bus,
        budget={"max_iterations": 1}))
    cand = result["candidates"][0]

    # the loop left the scratch part (for the gallery), under gen_<id>_<n>.
    parts = {p["id"] for p in registry.call("get_project", {"project": PROJECT})["parts"]}
    assert cand["scratch_id"] in parts
    assert cand["scratch_id"] == f"gen_{gen_id}_0"

    removed = cleanup_scratch(service, PROJECT, gen_id)
    assert removed == [cand["scratch_id"]]
    parts_after = {p["id"] for p in registry.call("get_project", {"project": PROJECT})["parts"]}
    assert cand["scratch_id"] not in parts_after
    assert not any(pid.startswith("gen_") for pid in parts_after)


# ==================================================== restricted tool surface

def test_restricted_tool_list_and_forbidden_refused(stack):
    service, registry, bus = stack

    # A control part a rogue delete_part would remove if it were permitted.
    assert "error" not in registry.call(
        "create_part", {"project": PROJECT, "part_id": "keepme",
                        "script": GREEN_SCRIPT})

    fake = FakeAnthropic([
        _response([_create(FAIL_SPEC_SCRIPT)]),
        _response([_tool_use("tu_del", "delete_part",
                             {"project": PROJECT, "part_id": "keepme"})]),
        _response([_text("giving up")], stop_reason="end_turn"),
    ])

    result = _run(run_generation(
        service, registry, project=PROJECT, prompt="x",
        client_factory=lambda: fake, gen_id="rgen", bus=bus,
        budget=Budget(max_iterations=6, wall_clock_s=60)))

    # the tool list shown to the model is exactly the allowed subset
    tools_shown = {t["name"] for t in fake.messages.calls[0]["tools"]}
    assert tools_shown <= ALLOWED_TOOLS
    assert "delete_part" not in tools_shown
    assert "generate_part" not in tools_shown
    assert "set_assembly" not in tools_shown

    # delete_part was refused at dispatch: the control part survives.
    parts = {p["id"] for p in registry.call("get_project", {"project": PROJECT})["parts"]}
    assert "keepme" in parts
    cand = result["candidates"][0]
    assert cand["terminal_state"] == "budget_exhausted"


# ============================================ mechanical inject every iteration

def test_render_and_measure_injected_every_write_iteration(stack):
    service, registry, bus = stack
    # Two write iterations, neither of which asks the loop to look/measure.
    fake = FakeAnthropic([
        _response([_create(FAIL_SPEC_SCRIPT)]),
        _response([_tool_use("tu_upd", "update_part_script",
                             {"project": PROJECT, "part_id": "draft",
                              "script": FAIL_SPEC_SCRIPT})]),
        _response([_text("done")], stop_reason="end_turn"),
    ])

    result = _run(run_generation(
        service, registry, project=PROJECT, prompt="x",
        client_factory=lambda: fake, gen_id="injgen", bus=bus,
        budget=Budget(max_iterations=6, wall_clock_s=60)))

    cand = result["candidates"][0]
    write_iters = [e for e in cand["iteration_log"] if e["wrote_script"]]
    assert len(write_iters) == 2
    for entry in write_iters:
        assert entry["rendered"] and entry["measured"] and entry["specs_run"]
        assert entry["kernel_valid"] is True


# =================================================== events fire on the bus

def test_generation_progress_and_done_events(stack):
    service, registry, bus = stack
    fake = FakeAnthropic([_response([_create(GREEN_SCRIPT)])])
    queue = bus.subscribe()

    _run(run_generation(
        service, registry, project=PROJECT, prompt="x",
        client_factory=lambda: fake, gen_id="evgen", bus=bus,
        budget=Budget(max_iterations=6)))

    events = _drain(queue)
    progress = [e for e in events if e["type"] == "generation_progress"]
    done = [e for e in events if e["type"] == "generation_done"]
    assert progress, "expected generation_progress events"
    assert all(e["generation_id"] == "evgen" for e in progress)
    assert {e["phase"] for e in progress} >= {"iterate", "measured", "done"}
    assert len(done) == 1
    assert done[0]["generation_id"] == "evgen"
    assert done[0]["best"] == 0


# ================================================== tenancy captured across hop

def test_tenant_captured_across_thread_hop(stack):
    service, registry, bus = stack

    class RecordingRegistry:
        """Wraps the registry to record the ambient tenant at each call — set
        inside the executor thread by the loop's `_call_tool`."""

        def __init__(self, inner):
            self.inner = inner
            self.seen = []

        def list(self):
            return self.inner.list()

        def get(self, name):
            return self.inner.get(name)

        def call(self, name, args):
            self.seen.append((name, tenancy.current_tenant()))
            return self.inner.call(name, args)

    recording = RecordingRegistry(registry)
    fake = FakeAnthropic([_response([_create(GREEN_SCRIPT)])])

    async def main():
        token = tenancy.set_tenant(("acme", "main"))
        try:
            return await run_generation(
                service, recording, project=PROJECT, prompt="x",
                client_factory=lambda: fake, gen_id="tengen", bus=bus,
                budget=Budget(max_iterations=6))
        finally:
            tenancy.reset_tenant(token)

    result = asyncio.run(main())
    assert result["candidates"][0]["terminal_state"] == "spec_green"

    # Every tool the loop dispatched ran under the tenant we set — the executor
    # hop did not lose it.
    assert recording.seen, "no tools were dispatched"
    assert all(tenant == ("acme", "main") for _name, tenant in recording.seen)
    # and the mechanical tools really ran there too
    assert {n for n, _ in recording.seen} >= {"create_part", "render_view",
                                              "get_metrics", "run_specs"}


# ============================================================ construction guards

def test_loop_requires_client_factory(stack):
    service, registry, _bus = stack
    with pytest.raises(Exception):
        GenerationLoop(service, registry, project=PROJECT, prompt="x")
