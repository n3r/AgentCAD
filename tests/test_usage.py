"""PRD-006 slice 4 — the usage meter, its scope, health's object, `get_usage`.

Every kernel response now carries what it cost (slice 3's `on_usage` hook).
This is the service side of that: a meter that rolls the records up per
**project** and per **client identity**, the ContextVar that says which
project a kernel call belongs to, and the two surfaces that publish it —
`/api/health` (which also stops answering `sandbox` as a bare string and
starts answering the honest object) and the `get_usage` tool.

The meter is OCP-free and kernel-free, so most of this file drives it
directly with synthetic hook payloads — the same dicts `KernelClient._emit_usage`
builds. One test (AC7) wires the meter to the **real** session kernel and
proves two projects come back as two rows.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from agentcad.core import locks, usage
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service

pytestmark = pytest.mark.portability


@pytest.fixture(autouse=True)
def _reset_identity():
    """Pin `client_id_var` to its default for every test in this module.

    Three tests here assert that an unattributed record bills `local`, which
    is the ContextVar's default — and a ContextVar set at a test's top level
    is never restored, so it survives for the rest of that process. Several
    modules leave one set: `tests/test_checks_gate.py` calls
    `locks.set_client_id("ci")` directly, and every in-process
    `agentcad check` / `agentcad package …` does the same inside `cli.py`.
    Under `-n auto --dist loadscope` which of them shares a worker with this
    module is a scheduling detail, so without this fixture the file passes or
    fails depending on what else was collected — measured: adding one
    unrelated test module moved `test_checks_gate` onto the same worker and
    turned three assertions here from `local` into `ci`.

    `tests/test_branches.py::_reset_context` is the same fixture for the same
    reason.
    """
    token = locks.client_id_var.set("local")
    yield
    locks.client_id_var.reset(token)


def _event(method="build", cpu_ms=10.0, wall_ms=20.0, peak_rss_mb=100.0,
           ok=True, worker="kernel-0") -> dict:
    """One `on_usage` payload, exactly as the client emits it."""
    return {"method": method, "ok": ok, "worker": worker,
            "usage": {"cpu_ms": cpu_ms, "wall_ms": wall_ms,
                      "peak_rss_mb": peak_rss_mb,
                      "peak_rss_is_lifetime": False}}


def _row(rows: list[dict], key: str, value):
    return next((r for r in rows if r[key] == value), None)


# ------------------------------------------------------------- the roll-ups


def test_records_roll_up_per_project():
    meter = usage.UsageMeter()
    with usage.scoped("p1"):
        meter.record(_event(cpu_ms=10.0, wall_ms=20.0))
        meter.record(_event(cpu_ms=30.0, wall_ms=40.0))
    with usage.scoped("p2"):
        meter.record(_event(cpu_ms=5.0, wall_ms=6.0))

    rows = meter.by_project()
    assert len(rows) == 2
    assert _row(rows, "project", "p1")["requests"] == 2
    assert _row(rows, "project", "p1")["cpu_ms"] == pytest.approx(40.0)
    assert _row(rows, "project", "p1")["wall_ms"] == pytest.approx(60.0)
    assert _row(rows, "project", "p2")["requests"] == 1
    assert _row(rows, "project", "p2")["cpu_ms"] == pytest.approx(5.0)

    totals = meter.totals()
    assert totals["requests"] == 3
    assert totals["cpu_ms"] == pytest.approx(45.0)
    assert totals["errors"] == 0


def test_the_scope_is_restored_when_the_block_ends():
    """`scoped` is a ContextVar token, not an assignment: a nested scope must
    not leak into the caller's, or one build would bill the next."""
    assert usage.scope_var.get() is None
    with usage.scoped("outer"):
        with usage.scoped("inner"):
            assert usage.scope_var.get() == "inner"
        assert usage.scope_var.get() == "outer"
    assert usage.scope_var.get() is None


def test_a_failed_request_counts_as_an_error_and_still_bills():
    meter = usage.UsageMeter()
    with usage.scoped("p1"):
        meter.record(_event(ok=False, cpu_ms=7.0))

    row = _row(meter.by_project(), "project", "p1")
    assert row["requests"] == 1 and row["errors"] == 1
    assert row["cpu_ms"] == pytest.approx(7.0)


def test_peak_rss_is_a_maximum_not_a_sum():
    meter = usage.UsageMeter()
    with usage.scoped("p1"):
        meter.record(_event(peak_rss_mb=120.0))
        meter.record(_event(peak_rss_mb=90.0))

    assert _row(meter.by_project(), "project", "p1")["peak_rss_mb"] == 120.0


def test_an_unmeasurable_cpu_is_skipped_not_counted_as_zero():
    """The client sends `cpu_ms: None` for a request that never answered (a
    kill, a timeout) — "not measurable from here" is not "no CPU was spent".
    The meter must not turn it into a zero that dilutes nothing, nor crash."""
    meter = usage.UsageMeter()
    with usage.scoped("p1"):
        meter.record(_event(cpu_ms=None, peak_rss_mb=None, wall_ms=13.0))
        meter.record(_event(cpu_ms=4.0, wall_ms=1.0))

    row = _row(meter.by_project(), "project", "p1")
    assert row["requests"] == 2
    assert row["cpu_ms"] == pytest.approx(4.0)
    assert row["wall_ms"] == pytest.approx(14.0)
    assert row["peak_rss_mb"] == 100.0  # the one sample that existed


def test_a_junk_event_is_ignored_rather_than_raised():
    """The hook runs inside the kernel client's request loop. A meter that
    raised on a shape it did not expect would turn a green build red."""
    meter = usage.UsageMeter()
    meter.record({})
    meter.record({"usage": None})
    meter.record({"usage": {"cpu_ms": "nonsense"}})
    meter.record(None)

    assert meter.totals()["requests"] == 3  # the three dicts; None is not one


def test_identity_comes_from_the_client_id_var():
    meter = usage.UsageMeter()
    token = locks.client_id_var.set("agent-7")
    try:
        with usage.scoped("p1"):
            meter.record(_event(cpu_ms=3.0))
    finally:
        locks.client_id_var.reset(token)
    with usage.scoped("p1"):
        meter.record(_event(cpu_ms=2.0))

    rows = meter.by_identity()
    assert {r["identity"] for r in rows} == {"agent-7", "local"}
    assert _row(rows, "identity", "agent-7")["cpu_ms"] == pytest.approx(3.0)
    # ...and both are one project.
    assert len(meter.by_project()) == 1


def test_since_filters_to_the_recent_window():
    meter = usage.UsageMeter()
    with usage.scoped("old"):
        meter.record(_event(cpu_ms=100.0))
    cut = time.time()
    time.sleep(0.01)
    with usage.scoped("new"):
        meter.record(_event(cpu_ms=1.0))

    assert {r["project"] for r in meter.by_project()} == {"old", "new"}
    recent = meter.by_project(since=cut)
    assert [r["project"] for r in recent] == ["new"]
    assert recent[0]["cpu_ms"] == pytest.approx(1.0)


def test_top_bounds_the_row_count():
    meter = usage.UsageMeter()
    for index in range(30):
        with usage.scoped(f"p{index:02d}"):
            meter.record(_event(cpu_ms=float(index)))

    rows = meter.by_project(top=5)
    assert len(rows) == 5
    assert rows[0]["project"] == "p29"  # ranked by cost, biggest first


def test_a_since_older_than_the_retained_window_says_so():
    """Honesty: the ring is bounded, so an old `since` cannot be answered in
    full. The snapshot says the window is short rather than under-reporting
    silently."""
    meter = usage.UsageMeter(keep_recent=2)
    with usage.scoped("p1"):
        for _ in range(5):
            meter.record(_event(cpu_ms=1.0))

    snap = meter.snapshot(since=0.0)
    assert snap["window"]["kept"] == 2 and snap["window"]["capacity"] == 2
    assert any("retained" in warning for warning in snap["warnings"])
    assert snap["totals"]["requests"] == 2      # what the window can answer
    assert meter.totals()["requests"] == 5      # what the roll-up knows


# --------------------------------------------------------- the path -> scope


@pytest.mark.parametrize("path,expected", [
    ("/api/projects/a%20b/parts", "a b"),
    ("/api/projects/demo", "demo"),
    ("/api/projects/demo/parts/box/params", "demo"),
    ("/api/projects", None),
    ("/api/projects/", None),
    ("/api/health", None),
    ("/", None),
    ("", None),
    # `open` registers a directory as a project; it is not a project id, and a
    # usage row named "open" would be a lie.
    ("/api/projects/open", None),
])
def test_project_from_path(path, expected):
    assert usage.project_from_path(path) == expected


# ------------------------------------------------------------- the surfaces


def test_get_usage_returns_the_snapshot(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    meter = usage.UsageMeter()
    service.usage = meter
    with usage.scoped("demo"):
        meter.record(_event(cpu_ms=12.0))

    result = build_registry(service).call("get_usage", {})

    assert "error" not in result
    assert result["totals"]["requests"] == 1
    assert _row(result["projects"], "project", "demo")["cpu_ms"] == \
        pytest.approx(12.0)
    assert [r["identity"] for r in result["identities"]] == ["local"]


def test_get_usage_filters_by_project(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.usage = usage.UsageMeter()
    with usage.scoped("a"):
        service.usage.record(_event(cpu_ms=1.0))
    with usage.scoped("b"):
        service.usage.record(_event(cpu_ms=2.0))

    result = build_registry(service).call("get_usage", {"project": "b"})

    assert [r["project"] for r in result["projects"]] == ["b"]
    assert result["totals"]["cpu_ms"] == pytest.approx(2.0)


def test_get_usage_without_a_meter_is_an_empty_snapshot(kernel, tmp_path):
    """A service built by a library caller (or `make_test_service`) has no
    meter — the CLI installs it. The tool answers an empty roll-up rather than
    an AttributeError 500."""
    service = make_test_service(tmp_path / "projects", kernel)
    assert getattr(service, "usage", None) is None

    result = build_registry(service).call("get_usage", {})

    assert result["totals"]["requests"] == 0
    assert result["projects"] == [] and result["identities"] == []


def test_the_tool_registry_scopes_a_call_by_its_project_argument(kernel,
                                                                 tmp_path):
    """`call()` is the agent's door. A tool that reaches the kernel without
    going through the service's own scoped paths still bills the right
    project."""
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    seen = []
    from agentcad.core.tools import Tool

    registry.register(Tool("probe_scope", "test-only",
                           {"type": "object",
                            "properties": {"project": {"type": "string"}}},
                           lambda project=None: seen.append(
                               usage.scope_var.get()) or {"ok": True}))

    registry.call("probe_scope", {"project": "demo"})
    registry.call("probe_scope", {})

    assert seen == ["demo", None]
    assert usage.scope_var.get() is None  # and it is put back afterwards


@pytest.fixture
def client(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.usage = usage.UsageMeter()
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1")


def test_health_publishes_the_sandbox_object(client):
    body = client.get("/api/health").json()

    sandbox_obj = body["sandbox"]
    assert isinstance(sandbox_obj, dict)
    assert set(sandbox_obj) >= {"status", "mechanism", "posture",
                                "confinement", "quotas", "warnings"}
    # The session kernel is built the historical way, so it is never confined.
    assert sandbox_obj["status"] in ("off", "unsupported")
    assert sandbox_obj["confinement"]["status"] == sandbox_obj["status"]


def test_health_publishes_usage(client):
    body = client.get("/api/health").json()

    assert set(body["usage"]) >= {"totals", "projects"}
    assert body["usage"]["totals"]["requests"] == 0


def test_the_middleware_scopes_a_request_by_its_path(client):
    """The scope has to be set on the way IN, before any handler runs, or a
    route that reaches the kernel without the service's own scoped path (an
    export, a check) bills nobody."""
    assert client.post("/api/projects", json={"name": "scoped"}).status_code \
        == 201
    seen = []
    app = client.app

    @app.get("/api/projects/{project}/probe-scope")
    def _probe(project: str):                      # pragma: no cover - routed
        seen.append(usage.scope_var.get())
        return {"ok": True}

    assert client.get("/api/projects/scoped/probe-scope").status_code == 200
    assert seen == ["scoped"]


# ------------------------------------------------------------------ AC7


def test_two_projects_are_two_rows_through_a_real_build(kernel, tmp_path,
                                                        monkeypatch):
    """AC7 end to end: two real kernel builds in two projects come back as two
    distinguishable roll-ups, with the identity the caller had."""
    meter = usage.UsageMeter()
    monkeypatch.setattr(kernel, "_on_usage", meter.record, raising=False)
    service = make_test_service(tmp_path / "projects", kernel)
    for name in ("alpha", "beta"):
        service.create_project(name)
        service.create_part(name, "box", "Box", BOX_SCRIPT)

    rows = meter.by_project()

    assert {r["project"] for r in rows} >= {"alpha", "beta"}
    for name in ("alpha", "beta"):
        row = _row(rows, "project", name)
        assert row["requests"] >= 1
        assert row["wall_ms"] > 0
    assert meter.totals()["requests"] >= 2
    assert [r["identity"] for r in meter.by_identity()] == ["local"]
