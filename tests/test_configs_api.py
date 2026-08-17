"""PRD-012 slice 3 — the configuration tool pack and the route pack.

Three sections, the shape ``tests/test_specs_api.py`` uses: 1. registration
(the five tools, their schemas, and the load-order invariant that the pack
reads no seam a later pack installs) · 2. the tools, through
``registry.call`` so the argument validation and the ``{"error": …}`` payload
are exercised the way an agent meets them · 3. the routes, through
``create_app`` + ``TestClient``.

The tool tests are one class because they share one three-member flange
family: the template project (scripts + parts, **no builds**) is made once per
class and cloned per test, so every test mutates its own manifest and its own
cache directory.
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core import tools_configs
from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.packages import manager as pkgmanager
from agentcad.core.tools import ToolRegistry, build_registry
from agentcad.server.app import create_app
from agentcad.server.routes_configs import _KEY_RE

from .conftest import (
    FLANGE_SCRIPT,
    THREE_SIZE_CONFIGS,
    clone_test_service,
    make_test_service,
)

CONFIG_TOOLS = ["set_part_configs", "list_configs", "build_configs",
                "set_active_config", "set_instance_config"]

#: A part that refuses to build above 50 mm — the deterministic per-member
#: failure a matrix row has to carry. An out-of-range value would not do: the
#: validator refuses it on write, and the worker would clamp it with a warning.
FRAGILE_SCRIPT = '''\
from build123d import *

PARAMS = {
    "thick": {"default": 10.0, "min": 4.0, "max": 60.0, "unit": "mm",
              "description": "plate thickness"},
}

def build(p):
    if p.thick > 50:
        raise ValueError("thickness above 50 mm is not manufacturable")
    return Box(40, 40, p.thick)
'''

FRAGILE_CONFIGS = {
    "thin": {"params": {"thick": 10.0}, "label": "Thin"},
    "heavy": {"params": {"thick": 55.0}, "label": "Too thick"},
}

#: The mirror of FRAGILE_SCRIPT: its DEFAULT thickness is the unbuildable one,
#: so the part builds at a declared configuration and fails at base — which is
#: what a `DELETE .../active-config` whose rebuild fails needs.
INVERTED_SCRIPT = FRAGILE_SCRIPT.replace('"default": 10.0', '"default": 55.0')
assert INVERTED_SCRIPT != FRAGILE_SCRIPT

#: A script that does not load at all: `_params_spec` negative-caches None and
#: the pack must refuse with `set_params`' wording rather than persist a map it
#: could not check.
BROKEN_SCRIPT = '''\
from build123d import *

PARAMS = {"size": {"default": 10.0, "min": 1.0, "max": 50.0}}

def build(p):
    return Box(p.size, p.size, p.size
'''

WITHOUT_L = {name: entry for name, entry in THREE_SIZE_CONFIGS.items()
             if name != "l"}


def _drain(q: queue.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def _changed(q: queue.Queue) -> list[dict]:
    return [e for e in _drain(q) if e["type"] == "project_changed"]


def _counting(service, monkeypatch) -> dict:
    """Count kernel requests by method (the `tests/test_specs_api.py` pattern)."""
    calls: dict = {}
    original = service.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        calls[method] = calls.get(method, 0) + 1
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", counting)
    return calls


def _manifest_lock(store, project: str):
    """The RLock `packages.manager.manifest_scope` serializes this project on
    (taking it once is what creates the registry entry)."""
    with pkgmanager.manifest_scope(store, project):
        pass
    return pkgmanager._manifest_locks[str(Path(store.path_of(project)).resolve())]


def _held(lock) -> bool:
    """Is ``lock`` held right now? Probed from ANOTHER thread on purpose: an
    RLock is reentrant for its owner, so acquiring it here would always
    succeed and prove nothing."""
    got: list[bool] = []

    def probe():
        acquired = lock.acquire(blocking=False)
        got.append(acquired)
        if acquired:
            lock.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return not got[0]


def _entry(service, project: str, part_id: str) -> dict:
    """The part's RAW manifest entry — the only way to see a popped key."""
    manifest = json.loads(
        (service.store.path_of(project) / "project.json").read_text(
            encoding="utf-8")
    )
    return next(p for p in manifest["parts"] if p["id"] == part_id)


def _template(kernel, tmp_path_factory, name: str):
    """One project with four parts and no builds, for cloning per test."""
    projects = tmp_path_factory.mktemp(name)
    svc = make_test_service(projects, kernel)
    svc.create_project("demo")
    svc.store.add_part("demo", "flange", "Flange", DEFAULT_MATERIAL,
                       FLANGE_SCRIPT)
    svc.store.add_part("demo", "fragile", "Fragile", DEFAULT_MATERIAL,
                       FRAGILE_SCRIPT)
    svc.store.add_part("demo", "broken", "Broken", DEFAULT_MATERIAL,
                       BROKEN_SCRIPT)
    svc.store.add_part("demo", "vendor", "Vendor", DEFAULT_MATERIAL, "",
                       kind="reference", source="imports/bracket.step")
    return projects


# --------------------------------------------------------- 1. registration


@pytest.fixture
def bare(tmp_path):
    """A registry over a kernel-less service: registration touches neither."""
    service = make_test_service(tmp_path / "projects", None)
    return service, build_registry(service)


def test_every_configuration_tool_is_registered(bare):
    _service, registry = bare
    names = {tool.name for tool in registry.list()}
    assert set(CONFIG_TOOLS) <= names


def test_the_pack_registers_before_the_seams_a_later_pack_installs(tmp_path):
    """`tools_configs` sorts at `con` — before `specs`, `packages`,
    `proposals` — so `register()` may read none of those seams, and must never
    append to `gate_providers` (`tools_proposals` resets it unconditionally)."""
    service = make_test_service(tmp_path / "projects", None)
    assert getattr(service, "specs", None) is None
    assert getattr(service, "packages", None) is None
    registry = ToolRegistry()
    tools_configs.register(registry, service)
    assert {tool.name for tool in registry.list()} == set(CONFIG_TOOLS)
    assert getattr(service, "gate_providers", None) is None


def test_the_configuration_tool_schemas_are_the_documented_ones(bare):
    _service, registry = bare
    schemas = {name: registry.get(name).input_schema for name in CONFIG_TOOLS}
    assert schemas["set_part_configs"]["required"] == [
        "project", "part_id", "configs"]
    assert schemas["set_part_configs"]["properties"]["configs"]["type"] == "object"
    assert schemas["list_configs"]["required"] == ["project"]
    assert schemas["build_configs"]["required"] == ["project"]
    assert schemas["build_configs"]["properties"]["configs"]["type"] == "array"
    assert schemas["set_active_config"]["required"] == ["project", "part_id"]
    assert schemas["set_active_config"]["properties"]["config"]["type"] == "string"
    assert schemas["set_active_config"][
        "properties"]["keep_overrides"]["type"] == "boolean"
    assert schemas["set_instance_config"]["required"] == ["project", "instance"]


def test_a_bad_argument_type_is_an_invalid_arguments_refusal(bare):
    _service, registry = bare
    out = registry.call("set_part_configs", {"project": "demo",
                                             "part_id": "flange",
                                             "configs": ["s", "m"]})
    assert out["error"]["type"] == "invalid_arguments"


# ------------------------------------------------------------- 2. the tools


@pytest.mark.timeout(600)
class TestConfigTools:
    """The five tools against one real three-member family."""

    @pytest.fixture(scope="class")
    @classmethod
    def tool_projects(cls, kernel, tmp_path_factory):
        return _template(kernel, tmp_path_factory, "configs_tools_projects")

    @pytest.fixture
    def stack(self, kernel, tmp_path, tool_projects):
        service = clone_test_service(tool_projects, tmp_path / "projects",
                                     kernel)
        return service, build_registry(service)

    @staticmethod
    def _family(registry, part_id="flange", configs=None) -> dict:
        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": part_id,
            "configs": THREE_SIZE_CONFIGS if configs is None else configs})
        assert "error" not in out, out
        return out

    # ------------------------------------------- set_part_configs (Task 1)

    def test_a_good_family_is_stored_in_the_callers_order(self, stack):
        service, registry = stack
        out = self._family(registry)
        assert out["part_id"] == "flange"
        assert out["active_config"] is None
        assert list(out["configs"]) == ["s", "m", "l"]   # never sorted
        assert "rebuild" not in out       # nothing is active: nothing to build
        stored = service.store.get_part("demo", "flange")
        assert list(stored.configs) == ["s", "m", "l"]
        assert stored.configs["m"]["label"] == "Medium"
        assert stored.configs["l"]["params"] == \
            THREE_SIZE_CONFIGS["l"]["params"]

    def test_values_are_normalized_on_write(self, stack):
        """`{"outer_d": 100}` and `{"outer_d": 100.0}` must not be two
        configurations (and two cache keys) for one geometry."""
        service, registry = stack
        out = self._family(registry, configs={
            "s": {"params": {"outer_d": 100, "bore_d": 50}, "label": "Small"}})
        assert out["configs"]["s"]["params"] == {"outer_d": 100.0,
                                                "bore_d": 50.0}
        stored = service.store.get_part("demo", "flange").configs["s"]["params"]
        assert isinstance(stored["outer_d"], float)

    def test_an_uppercase_name_is_refused_with_every_problem(self, stack):
        service, registry = stack
        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange",
            "configs": {"L": {"params": {"outer_d": 200.0}},
                        "XL": {"params": {"outer_d": 240.0}}}})
        assert out["error"]["type"] == "validation_error"
        assert out["error"]["details"]["part_id"] == "flange"
        fields = [p["field"] for p in out["error"]["details"]["problems"]]
        assert fields == ["configs.L", "configs.XL"]   # every problem at once
        assert service.store.get_part("demo", "flange").configs is None

    def test_an_out_of_range_value_is_refused(self, stack):
        """Decision 2: a declared configuration is range-strict (the publish
        gate's choice), while an explicit `set_params` override is clamped."""
        service, registry = stack
        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange",
            "configs": {"xl": {"params": {"outer_d": 4000.0}}}})
        assert out["error"]["type"] == "validation_error"
        assert [p["field"] for p in out["error"]["details"]["problems"]] == \
            ["configs.xl.params.outer_d"]
        assert service.store.get_part("demo", "flange").configs is None

    def test_an_unknown_parameter_is_refused(self, stack):
        _service, registry = stack
        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange",
            "configs": {"xl": {"params": {"widht": 40.0}}}})
        assert out["error"]["type"] == "validation_error"
        assert [p["field"] for p in out["error"]["details"]["problems"]] == \
            ["configs.xl.params.widht"]

    def test_a_reference_part_cannot_be_configured(self, stack):
        _service, registry = stack
        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "vendor",
            "configs": THREE_SIZE_CONFIGS})
        assert out["error"]["type"] == "validation_error"
        assert "no parameters to configure" in out["error"]["message"]

    def test_a_script_that_does_not_load_is_refused(self, stack):
        """The `set_params` wording, because a user who meets one meets both."""
        service, registry = stack
        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "broken",
            "configs": {"s": {"params": {"size": 20.0}}}})
        assert out["error"]["type"] == "validation_error"
        assert "does not currently load" in out["error"]["message"]
        assert service.store.get_part("demo", "broken").configs is None

    def test_an_emptied_map_pops_the_key(self, stack):
        """G5/AC8: a part that no longer has a family is byte-identical to one
        that never had one."""
        service, registry = stack
        self._family(registry)
        assert "configs" in _entry(service, "demo", "flange")
        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange", "configs": {}})
        assert out["configs"] == {}
        assert "configs" not in _entry(service, "demo", "flange")

    def test_exactly_one_project_changed_publish_per_call(self, stack):
        service, registry = stack
        events = service.bus.subscribe()
        self._family(registry)
        assert _changed(events) == [
            {"type": "project_changed", "project": "demo", "part": "flange",
             "reason": "configs"}]

    def test_a_rebuild_rides_a_change_to_the_active_configuration(self, stack):
        """The response carries the new geometry only when the geometry moved:
        editing a member nobody activated costs no build."""
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "m"})

        edited = {**THREE_SIZE_CONFIGS,
                  "s": {"params": {"outer_d": 90.0}, "label": "Small"}}
        assert "rebuild" not in self._family(registry, configs=edited)

        edited = {**edited, "m": {"params": {**THREE_SIZE_CONFIGS["m"]["params"],
                                            "thick": 20.0}, "label": "Medium"}}
        out = self._family(registry, configs=edited)
        assert out["rebuild"]["ok"] is True
        assert out["rebuild"]["cache_key"] == \
            service.mesh_info("demo", "flange")["key"]

    # ------------------------------------------------ FR11 conflict (Task 2)

    def test_a_configuration_an_instance_binds_cannot_be_removed(self, stack):
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "f1", "part": "flange", "config": "l"}]})

        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange", "configs": WITHOUT_L})
        assert out["error"]["type"] == "conflict_error"
        details = out["error"]["details"]
        assert details["part"] == "flange"
        assert details["configs"] == ["l"]
        assert details["instances"] == ["f1"]
        assert details["active_config"] is False
        assert "f1" in out["error"]["message"]
        # ...and the refusal happened before the write.
        assert list(service.store.get_part("demo", "flange").configs) == \
            ["s", "m", "l"]

    def test_the_active_configuration_cannot_be_removed(self, stack):
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "l"})

        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange", "configs": WITHOUT_L})
        assert out["error"]["type"] == "conflict_error"
        assert out["error"]["details"]["active_config"] is True
        assert out["error"]["details"]["configs"] == ["l"]
        assert out["error"]["details"]["instances"] == []
        assert service.store.get_part("demo", "flange").active_config == "l"

    def test_unbinding_lets_the_removal_through(self, stack):
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "f1", "part": "flange", "config": "l"}]})
        assert "error" not in registry.call("set_instance_config", {
            "project": "demo", "instance": "f1"})

        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange", "configs": WITHOUT_L})
        assert "error" not in out, out
        assert list(out["configs"]) == ["s", "m"]
        assert "l" not in service.get_part("demo", "flange")["configs"]

    def test_the_locks_span_the_whole_read_modify_write(self, stack,
                                                       monkeypatch):
        """Fix round 1 (I1/I2). FR11 reads the part entry AND the instance
        list, and `cleared_overrides` reports a map read before the write — a
        lock that covers only the write leaves both TOCTOU.
        `set_instance_config` additionally takes `service._lock`, the lock
        `service.set_assembly` serializes the identical read-all/write-all on.
        """
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "f1", "part": "flange", "config": "l"}]})

        manifest_lock = _manifest_lock(service.store, "demo")
        # The probe discriminates: neither lock is held between calls, so a
        # True below is the tool holding it and not a broken helper.
        assert _held(manifest_lock) is False
        assert _held(service._lock) is False
        seen: dict[str, bool] = {}
        store_instances = service.store.instances
        store_get_part = service.store.get_part

        def watched_instances(project):
            seen.setdefault("instances", _held(manifest_lock))
            seen.setdefault("instances_service_lock", _held(service._lock))
            return store_instances(project)

        def watched_get_part(project, part_id):
            seen.setdefault("get_part", _held(manifest_lock))
            return store_get_part(project, part_id)

        monkeypatch.setattr(service.store, "instances", watched_instances)
        monkeypatch.setattr(service.store, "get_part", watched_get_part)

        refused = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange", "configs": WITHOUT_L})
        assert refused["error"]["type"] == "conflict_error"
        assert seen["get_part"] is True        # the entry read is inside
        assert seen["instances"] is True       # ...and so is the referrer read

        seen.clear()
        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "m"})
        assert seen["get_part"] is True

        seen.clear()
        assert "error" not in registry.call("set_instance_config", {
            "project": "demo", "instance": "f1", "config": "s"})
        assert seen["instances"] is True
        assert seen["instances_service_lock"] is True

    # ------------------------------------------- set_active_config (Task 3)

    def test_switching_clears_the_overrides_and_returns_the_rebuild(self, stack):
        """Decision 5: switching *loads* a variant, so no hidden state
        survives it — one publish, one undo step, pure M."""
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_params", {
            "project": "demo", "part_id": "flange", "values": {"thick": 20.0}})

        events = service.bus.subscribe()
        out = registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "m"})
        assert "error" not in out, out
        assert out["part_id"] == "flange" and out["active_config"] == "m"
        assert out["cleared_overrides"] == {"thick": 20.0}
        assert out["diverged"] is False and out["diverged_params"] == []
        assert out["ok"] is True and out["metrics"]["mass_g"] > 0
        assert _entry(service, "demo", "flange")["params"] == {}
        assert [e["reason"] for e in _changed(events)] == ["active_config"]
        # The rebuilt working state IS the configuration's geometry.
        assert out["cache_key"] == service._ensure_config_built(
            "demo", "flange", "m")["cache_key"]

    def test_keep_overrides_keeps_them_and_reports_the_divergence(self, stack):
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_params", {
            "project": "demo", "part_id": "flange", "values": {"thick": 20.0}})

        out = registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "m",
            "keep_overrides": True})
        assert out["cleared_overrides"] == {}
        assert out["diverged"] is True
        assert out["diverged_params"] == ["thick"]
        assert service.store.get_part("demo", "flange").params == {"thick": 20.0}

    def test_omitting_the_config_returns_to_base(self, stack):
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "l"})

        out = registry.call("set_active_config", {"project": "demo",
                                                 "part_id": "flange"})
        assert "error" not in out, out
        assert out["active_config"] is None
        assert out["diverged"] is False
        assert "active_config" not in _entry(service, "demo", "flange")
        assert out["cache_key"] == service.mesh_info("demo", "flange")["key"]

    def test_re_selecting_the_active_configuration_keeps_the_overrides(
            self, stack):
        """Fix round 1 (MINOR 5): only a real CHANGE of the active
        configuration clears the overrides, so neither a re-selection nor a
        return-to-base on a part already at base drops `set_params` values."""
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "m"})
        assert "error" not in registry.call("set_params", {
            "project": "demo", "part_id": "flange", "values": {"thick": 20.0}})

        again = registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "m"})
        assert again["cleared_overrides"] == {}
        assert again["diverged"] is True and again["diverged_params"] == ["thick"]
        assert service.store.get_part("demo", "flange").params == {"thick": 20.0}

        # ...and a part already at base keeps them too.
        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange"})       # m -> base: clears
        assert "error" not in registry.call("set_params", {
            "project": "demo", "part_id": "flange", "values": {"thick": 20.0}})
        base = registry.call("set_active_config", {"project": "demo",
                                                  "part_id": "flange"})
        assert base["cleared_overrides"] == {}
        assert service.store.get_part("demo", "flange").params == {"thick": 20.0}

    def test_an_unknown_configuration_names_the_declared_ones(self, stack):
        service, registry = stack
        self._family(registry)
        out = registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "xl"})
        assert out["error"]["type"] == "validation_error"
        assert out["error"]["details"]["declared"] == ["l", "m", "s"]
        assert service.store.get_part("demo", "flange").active_config is None

    # -------------------------------------- list_configs/build_configs (T4)

    def test_list_configs_reports_the_referrers_of_a_bound_name(self, stack):
        """`referrers` makes FR11 a lookup before it is a surprise."""
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "f1", "part": "flange", "config": "l"},
                          {"id": "f2", "part": "flange", "config": "l"},
                          {"id": "f3", "part": "flange"}]})
        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "m"})
        assert "error" not in registry.call("set_params", {
            "project": "demo", "part_id": "flange", "values": {"thick": 20.0}})

        row = registry.call("list_configs", {"project": "demo",
                                             "part_id": "flange"})["parts"][0]
        assert row["part_id"] == "flange"
        assert list(row["configs"]) == ["s", "m", "l"]
        assert row["active_config"] == "m"
        assert row["diverged"] is True and row["diverged_params"] == ["thick"]
        assert row["referrers"] == {"l": ["f1", "f2"]}

        # Project-wide: the configured parts, and only those.
        parts = registry.call("list_configs", {"project": "demo"})["parts"]
        assert [p["part_id"] for p in parts] == ["flange"]

    def test_build_configs_returns_a_row_per_member_in_family_order(self, stack):
        service, registry = stack
        self._family(registry)
        out = registry.call("build_configs", {"project": "demo",
                                              "part_id": "flange"})
        assert "error" not in out, out
        assert out["part_id"] == "flange"
        rows = out["configs"]
        assert [row["name"] for row in rows] == ["s", "m", "l"]
        assert [row["label"] for row in rows] == ["Small", "Medium", "Large"]
        assert all(row["ok"] and row["cached"] is False for row in rows)
        assert "spec_results" not in rows[0]     # slice 6 fills it
        masses = [row["metrics"]["mass_g"] for row in rows]
        assert masses[0] < masses[1] < masses[2]
        keys = [row["cache_key"] for row in rows]
        assert len(set(keys)) == 3
        for key in keys:
            assert (service.store.cache_dir("demo") / f"{key}.acm").is_file()
        # A pure-configuration build is never the part's badge.
        assert service._status == {}
        # ...and a second call is honest about the cache.
        again = registry.call("build_configs", {"project": "demo",
                                                "part_id": "flange"})
        assert all(row["cached"] is True for row in again["configs"])

    def test_a_member_that_cannot_build_is_a_row_not_an_exception(self, stack):
        _service, registry = stack
        assert "error" not in registry.call("set_part_configs", {
            "project": "demo", "part_id": "fragile",
            "configs": FRAGILE_CONFIGS})
        out = registry.call("build_configs", {"project": "demo",
                                              "part_id": "fragile"})
        assert "error" not in out, out
        rows = {row["name"]: row for row in out["configs"]}
        assert [row["name"] for row in out["configs"]] == ["thin", "heavy"]
        assert rows["thin"]["ok"] is True
        assert rows["heavy"]["ok"] is False
        assert "not manufacturable" in json.dumps(rows["heavy"]["error"])
        assert rows["heavy"]["metrics"] is None
        assert rows["heavy"]["cache_key"]

    def test_two_members_with_the_same_params_are_built_once(self, stack,
                                                             monkeypatch):
        """AC5/Decision 4: serial and de-duplicated by cache key — the twin
        costs no kernel build and reports `cached`."""
        service, registry = stack
        twinned = {**THREE_SIZE_CONFIGS,
                   "m2": {"params": dict(THREE_SIZE_CONFIGS["m"]["params"]),
                          "label": "Medium (again)"}}
        self._family(registry, configs=twinned)

        calls = _counting(service, monkeypatch)
        out = registry.call("build_configs", {
            "project": "demo", "part_id": "flange", "configs": ["m", "m2"]})
        rows = {row["name"]: row for row in out["configs"]}
        assert [row["name"] for row in out["configs"]] == ["m", "m2"]
        assert rows["m2"]["cache_key"] == rows["m"]["cache_key"]
        assert rows["m2"]["metrics"] == rows["m"]["metrics"]
        assert rows["m"]["cached"] is False and rows["m2"]["cached"] is True
        assert calls.get("build") == 1

        # Fix wave (F5): `cached` is a claim about artifacts on disk, so the
        # de-duplicated sibling of a FAILED build must report False — there is
        # no `.acm` and no `.metrics.json` for it to have hit.
        assert "error" not in registry.call("set_part_configs", {
            "project": "demo", "part_id": "fragile",
            "configs": {"heavy": {"params": {"thick": 55.0}},
                        "heavy2": {"params": {"thick": 55.0}}}})
        failed = registry.call("build_configs", {"project": "demo",
                                                 "part_id": "fragile"})
        rows = failed["configs"]
        assert [row["name"] for row in rows] == ["heavy", "heavy2"]
        assert [row["ok"] for row in rows] == [False, False]
        assert rows[0]["cache_key"] == rows[1]["cache_key"]
        assert [row["cached"] for row in rows] == [False, False]
        cache = service.store.cache_dir("demo")
        assert not (cache / f"{rows[0]['cache_key']}.acm").is_file()

    def test_build_configs_refuses_a_name_the_part_does_not_declare(self, stack):
        _service, registry = stack
        self._family(registry)
        out = registry.call("build_configs", {
            "project": "demo", "part_id": "flange", "configs": ["s", "xl"]})
        assert out["error"]["type"] == "validation_error"
        assert out["error"]["details"]["declared"] == ["l", "m", "s"]

    def test_the_project_wide_build_groups_rows_by_part(self, stack):
        _service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_part_configs", {
            "project": "demo", "part_id": "fragile",
            "configs": {"thin": FRAGILE_CONFIGS["thin"]}})
        out = registry.call("build_configs", {"project": "demo"})
        assert "warnings" not in out
        assert [p["part_id"] for p in out["parts"]] == ["flange", "fragile"]
        assert [row["name"] for row in out["parts"][1]["configs"]] == ["thin"]
        assert all(row["ok"] for part in out["parts"]
                   for row in part["configs"])

    def test_a_project_with_no_configured_part_says_so(self, stack):
        """Never an empty list with no reason — for either reader."""
        _service, registry = stack
        out = registry.call("build_configs", {"project": "demo"})
        assert out == {"parts": [], "warnings": ["no configured parts"]}
        assert registry.call("list_configs", {"project": "demo"}) == \
            {"parts": [], "warnings": ["no configured parts"]}
        # A named part still gets its honest empty row.
        row = registry.call("list_configs", {"project": "demo",
                                             "part_id": "flange"})["parts"][0]
        assert row["configs"] == {} and row["referrers"] == {}

    def test_an_empty_matrix_carries_its_reason(self, stack):
        """Fix round 1 (MINOR 4): `configs: []` never arrives bare — a caller
        must be able to tell "nothing declared" from "the filter matched
        nothing" without guessing at the build."""
        _service, registry = stack
        nothing = registry.call("build_configs", {"project": "demo",
                                                 "part_id": "flange"})
        assert nothing["configs"] == []
        assert nothing["warnings"] == ["part 'flange' declares no configurations"]

        self._family(registry)
        unrequested = registry.call("build_configs", {
            "project": "demo", "part_id": "flange", "configs": []})
        assert unrequested["configs"] == []
        assert unrequested["warnings"] == ["no configurations requested"]

        # Project-wide, a part that declares none of the requested names says
        # so in its own row rather than reading as a part that built nothing.
        assert "error" not in registry.call("set_part_configs", {
            "project": "demo", "part_id": "fragile",
            "configs": {"thin": FRAGILE_CONFIGS["thin"]}})
        wide = registry.call("build_configs", {"project": "demo",
                                               "configs": ["s"]})
        rows = {part["part_id"]: part for part in wide["parts"]}
        assert [row["name"] for row in rows["flange"]["configs"]] == ["s"]
        assert rows["fragile"]["configs"] == []
        assert "declares none of the requested" in \
            rows["fragile"]["warnings"][0]

    # ------------------------------------------ set_instance_config (Task 5)

    def test_set_assembly_reads_an_instance_configuration(self, stack):
        service, registry = stack
        self._family(registry)
        out = registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "f1", "part": "flange", "config": "l"}]})
        assert "error" not in out, out
        assert out["instances"][0]["config"] == "l"
        assert service.store.instances("demo")[0].config == "l"

    def test_set_instance_config_binds_and_unbinds_one_instance(self, stack):
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "f1", "part": "flange"},
                          {"id": "f2", "part": "flange"}]})

        events = service.bus.subscribe()
        out = registry.call("set_instance_config", {
            "project": "demo", "instance": "f2", "config": "s"})
        assert "error" not in out, out
        bound = {i["id"]: i for i in out["instances"]}
        assert bound["f2"]["config"] == "s"
        assert "config" not in bound["f1"]        # the neighbour is untouched
        assert [e["reason"] for e in _changed(events)] == ["instance_config"]

        out = registry.call("set_instance_config", {"project": "demo",
                                                   "instance": "f2"})
        assert "config" not in out["instances"][1]
        assert service.store.instances("demo")[1].config is None

    def test_binding_an_undeclared_configuration_is_refused(self, stack):
        service, registry = stack
        self._family(registry)
        assert "error" not in registry.call("set_assembly", {
            "project": "demo", "instances": [{"id": "f1", "part": "flange"}]})
        out = registry.call("set_instance_config", {
            "project": "demo", "instance": "f1", "config": "xl"})
        assert out["error"]["type"] == "validation_error"
        assert out["error"]["details"]["declared"] == ["l", "m", "s"]
        assert service.store.instances("demo")[0].config is None

        missing = registry.call("set_instance_config", {
            "project": "demo", "instance": "nope", "config": "s"})
        assert missing["error"]["type"] == "notfound_error"


# ------------------------------------------------------------ 3. the routes


@pytest.mark.timeout(600)
class TestConfigRoutes:
    """Every route in the pack, through the app the browser talks to."""

    @pytest.fixture(scope="class")
    @classmethod
    def route_projects(cls, kernel, tmp_path_factory):
        return _template(kernel, tmp_path_factory, "configs_routes_projects")

    @pytest.fixture
    def client(self, kernel, tmp_path, route_projects):
        service = clone_test_service(route_projects, tmp_path / "projects",
                                     kernel)
        registry = build_registry(service)
        app = create_app(service, registry,
                         extra_allowed_hosts={"testserver"})
        return service, TestClient(app, base_url="http://127.0.0.1")

    @staticmethod
    def _put_family(client, configs=None) -> dict:
        response = client.put(
            "/api/projects/demo/parts/flange/configs",
            json={"configs": THREE_SIZE_CONFIGS if configs is None
                  else configs})
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_configs_routes_round_trip(self, client):
        _service, http = client
        body = self._put_family(http)
        assert list(body["configs"]) == ["s", "m", "l"]

        one = http.get("/api/projects/demo/parts/flange/configs")
        assert one.status_code == 200, one.text
        assert one.json()["parts"][0]["part_id"] == "flange"
        assert one.json()["parts"][0]["referrers"] == {}

        every = http.get("/api/projects/demo/configs")
        assert every.status_code == 200, every.text
        assert [p["part_id"] for p in every.json()["parts"]] == ["flange"]

    def test_a_refused_family_is_a_422_and_a_referenced_one_a_409(self, client):
        service, http = client
        response = http.put("/api/projects/demo/parts/flange/configs",
                            json={"configs": {"L": {"params": {}}}})
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["type"] == "ValidationError"
        assert error["details"]["problems"][0]["field"] == "configs.L"

        self._put_family(http)
        service.set_assembly("demo", [{"id": "f1", "part": "flange",
                                       "config": "l"}])
        conflict = http.put("/api/projects/demo/parts/flange/configs",
                            json={"configs": WITHOUT_L})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["details"]["instances"] == ["f1"]

    def test_the_active_config_routes_switch_and_return_to_base(self, client):
        service, http = client
        self._put_family(http)
        put = http.put("/api/projects/demo/parts/flange/active-config",
                       json={"config": "l"})
        assert put.status_code == 200, put.text
        assert put.json()["active_config"] == "l"
        assert put.json()["ok"] is True

        deleted = http.delete("/api/projects/demo/parts/flange/active-config")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["active_config"] is None
        assert service.store.get_part("demo", "flange").active_config is None

    def test_a_switch_whose_rebuild_fails_is_a_200_post_state(self, client):
        """Fix wave (F1/V4): the write LANDED — the manifest holds the new
        `active_config` and `project_changed` was published — so throwing the
        post-state away and answering 422 gives the client a model of the world
        no retry can fix. The house answer on the identical failure is
        `PATCH .../params`: 200 with `ok: false`. A refusal envelope (which
        carries no `ok`) still raises; a build post-state (which always has
        one) does not.
        """
        service, http = client
        assert http.put("/api/projects/demo/parts/fragile/configs",
                        json={"configs": FRAGILE_CONFIGS}).status_code == 200

        response = http.put("/api/projects/demo/parts/fragile/active-config",
                            json={"config": "heavy"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is False
        assert body["active_config"] == "heavy"
        assert "not manufacturable" in json.dumps(body["error"])
        assert service.store.get_part("demo", "fragile").active_config == "heavy"

        # ...and a genuine refusal is still a refusal, not a 200 body.
        refused = http.put("/api/projects/demo/parts/fragile/active-config",
                           json={"config": "nope"})
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["type"] == "ValidationError"
        assert service.store.get_part("demo", "fragile").active_config == "heavy"

    def test_a_return_to_base_whose_rebuild_fails_is_a_200_post_state(
            self, client):
        """The DELETE half of the same defect (V4): the part returns to base,
        base does not build, and the client was told the return-to-base failed
        while `project.json` already held it."""
        service, http = client
        service.store.add_part("demo", "inverted", "Inverted", DEFAULT_MATERIAL,
                               INVERTED_SCRIPT)
        assert http.put(
            "/api/projects/demo/parts/inverted/configs",
            json={"configs": {"thin": {"params": {"thick": 10.0}}}}
        ).status_code == 200
        put = http.put("/api/projects/demo/parts/inverted/active-config",
                       json={"config": "thin"})
        assert put.status_code == 200, put.text
        assert put.json()["ok"] is True

        response = http.delete("/api/projects/demo/parts/inverted/active-config")

        assert response.status_code == 200, response.text
        assert response.json()["ok"] is False
        assert response.json()["active_config"] is None
        assert service.store.get_part("demo", "inverted").active_config is None

    def test_a_malformed_or_empty_instance_patch_never_unbinds(self, client):
        """Fix wave (C3/V3): the only place in the branch where **malformed
        input silently mutated persisted state**. `_json` folded `[]`, `"bad"`
        and *no body at all* into `{}`, `"config" in body` was then false, and
        the tool's `config=None` default — whose documented meaning is
        *unbind* — fired. The state assertion is the load-bearing half.
        """
        service, http = client
        self._put_family(http)
        service.set_assembly("demo", [{"id": "f1", "part": "flange",
                                       "config": "l"}])
        url = "/api/projects/demo/assembly/instances/f1/config"

        for payload in ([], "bad", 3, {}):
            response = http.patch(url, json=payload)
            assert response.status_code == 422, (payload, response.text)
            assert service.store.instances("demo")[0].config == "l"

        empty = http.patch(url)
        assert empty.status_code == 422, empty.text
        assert "config is required" in empty.json()["error"]["message"]
        assert service.store.instances("demo")[0].config == "l"

        # `{"config": null}` stays the one way to say "unbind".
        cleared = http.patch(url, json={"config": None})
        assert cleared.status_code == 200, cleared.text
        assert service.store.instances("demo")[0].config is None

    def test_deleting_the_active_config_at_base_keeps_the_overrides(self, client):
        """Fix round 1 (MINOR 5): the DELETE is idempotent, and an idempotent
        call must not have a side effect — a part already at base keeps its
        `set_params` values."""
        service, http = client
        self._put_family(http)
        service.set_params("demo", "flange", {"thick": 20.0})
        response = http.delete("/api/projects/demo/parts/flange/active-config")
        assert response.status_code == 200, response.text
        assert response.json()["active_config"] is None
        assert response.json()["cleared_overrides"] == {}
        assert service.store.get_part("demo", "flange").params == {"thick": 20.0}

    def test_putting_configs_without_the_key_is_a_422(self, client):
        _service, http = client
        response = http.put("/api/projects/demo/parts/flange/configs", json={})
        assert response.status_code == 422, response.text

    def test_the_project_configs_route_says_when_there_is_no_family(self, client):
        _service, http = client
        response = http.get("/api/projects/demo/configs")
        assert response.status_code == 200, response.text
        assert response.json() == {"parts": [],
                                   "warnings": ["no configured parts"]}

    def test_putting_a_null_config_is_a_422_that_names_delete(self, client):
        """`_body_keys` strips `null`, so the PUT cannot express "base" — it
        says so instead of silently doing something else (that is what the
        DELETE is for)."""
        _service, http = client
        self._put_family(http)
        response = http.put("/api/projects/demo/parts/flange/active-config",
                            json={"config": None})
        assert response.status_code == 422, response.text
        assert "DELETE" in response.json()["error"]["message"]
        empty = http.put("/api/projects/demo/parts/flange/active-config",
                         json={})
        assert empty.status_code == 422, empty.text

    def test_the_build_route_returns_the_matrix(self, client):
        _service, http = client
        self._put_family(http)
        response = http.post("/api/projects/demo/configs/build",
                             json={"part_id": "flange", "configs": ["s"]})
        assert response.status_code == 200, response.text
        rows = response.json()["configs"]
        assert [row["name"] for row in rows] == ["s"]
        assert rows[0]["ok"] is True

        whole = http.post("/api/projects/demo/configs/build", json={})
        assert whole.status_code == 200, whole.text
        assert [p["part_id"] for p in whole.json()["parts"]] == ["flange"]

    def test_the_instance_config_patch_binds_and_unbinds(self, client):
        service, http = client
        self._put_family(http)
        service.set_assembly("demo", [{"id": "f1", "part": "flange"}])

        bound = http.patch("/api/projects/demo/assembly/instances/f1/config",
                           json={"config": "l"})
        assert bound.status_code == 200, bound.text
        assert bound.json()["instances"][0]["config"] == "l"

        # `null` unbinds: the route forwards on `"config" in body`, not on
        # truthiness (a stripped null would leave the binding in place).
        cleared = http.patch("/api/projects/demo/assembly/instances/f1/config",
                             json={"config": None})
        assert cleared.status_code == 200, cleared.text
        assert "config" not in cleared.json()["instances"][0]
        assert service.store.instances("demo")[0].config is None

        missing = http.patch(
            "/api/projects/demo/assembly/instances/nope/config",
            json={"config": "l"})
        assert missing.status_code == 404, missing.text

    def test_the_mesh_route_serves_a_built_configuration_by_key(self, client):
        service, http = client
        self._put_family(http)
        built = http.post("/api/projects/demo/configs/build",
                          json={"part_id": "flange", "configs": ["m"]})
        key = built.json()["configs"][0]["cache_key"]

        response = http.get(f"/api/projects/demo/meshes/{key}")
        assert response.status_code == 200, response.text
        assert response.headers["x-mesh-key"] == key
        assert response.headers["x-mesh-lod"] == "full"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"] == "application/octet-stream"
        expected = (service.store.cache_dir("demo") / f"{key}.acm").read_bytes()
        assert response.content == expected
        # A tier that does not exist falls back to the full mesh (small parts
        # never pay for one), and a lod that is not a token is ignored.
        for lod in ("lod1", "../../etc/passwd"):
            fallback = http.get(f"/api/projects/demo/meshes/{key}?lod={lod}")
            assert fallback.status_code == 200, fallback.text
            assert fallback.headers["x-mesh-lod"] == "full"
            assert fallback.content == expected

        # A tier that DOES exist is served, and named in the header.
        (service.store.cache_dir("demo") / f"{key}.lod1.acm").write_bytes(
            b"ACM1 coarse")
        tier = http.get(f"/api/projects/demo/meshes/{key}?lod=lod1")
        assert tier.status_code == 200, tier.text
        assert tier.headers["x-mesh-lod"] == "lod1"
        assert tier.content == b"ACM1 coarse"

    def test_the_mesh_route_never_builds_and_404s_when_it_must(self, client):
        service, http = client
        self._put_family(http)
        pure = service._cache_key_for(
            "demo", service._record_for("demo", "flange", "l"))
        assert not (service.store.cache_dir("demo") / f"{pure}.acm").is_file()

        unbuilt = http.get(f"/api/projects/demo/meshes/{pure}")
        assert unbuilt.status_code == 404, unbuilt.text
        assert "not built" in unbuilt.json()["error"]["message"]
        # It never builds: the key is still absent after the 404.
        assert not (service.store.cache_dir("demo") / f"{pure}.acm").is_file()

        for bad in ("nope", "../project", pure.upper(), pure[:31],
                    f"{pure}%0A"):
            malformed = http.get(f"/api/projects/demo/meshes/{bad}")
            assert malformed.status_code == 404, (bad, malformed.text)
        # The gate is applied with `fullmatch` because `$` also matches BEFORE a
        # trailing newline — an anchored `.match` would look for `<key>\n.acm`.
        assert not _KEY_RE.fullmatch(f"{pure}\n")
        assert _KEY_RE.fullmatch(pure)
        assert http.get(f"/api/projects/nosuch/meshes/{pure}").status_code == 404
