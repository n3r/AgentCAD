"""PRD-012 slices 1–2 — the configuration model, resolution, the build path.

The model half is manifest and dataclass work: a configuration never reaches
the kernel (the service resolves it into an override map on the way *into* a
request), so those tests run without a geometry worker — the service is built
with ``kernel=None`` on purpose, and any call that would need one would fail
loudly rather than pass on a mock.

``TestFlangeFamily`` is the build half (slice 2) and needs the real kernel: it
builds one three-member size family and asserts the per-configuration cache
identity, the quiet memo, the event tagging and the exported artifacts.
"""

import dataclasses
import hashlib
import json
import queue
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.model import InstanceSpec, PartRecord, ValidationError
from agentcad.core.service import MESH_TOLERANCE, _divergence
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import (
    FLANGE_SCRIPT,
    THREE_SIZE_CONFIGS,
    clone_test_service,
    make_test_service,
)

# A normalized PARAMS spec exactly as the kernel's `inspect` returns one
# (`worker._normalized_spec_entry`): one int, one string-valued enum, one
# numeric enum, one number.
SPEC = {
    "n": {"type": "int", "default": 2, "min": 1, "max": 8, "unit": None,
          "description": "hole count"},
    "grade": {"type": "enum", "default": "std", "min": None, "max": None,
              "unit": None, "description": "width grade",
              "choices": ["std", "wide"]},
    "count": {"type": "enum", "default": 2, "min": None, "max": None,
              "unit": None, "description": "bolt count", "choices": [2, 3, 4]},
    "width": {"type": "number", "default": 80.0, "min": 10.0, "max": 300.0,
              "unit": "mm", "description": "plate width"},
}


@pytest.fixture
def service(tmp_path):
    """A real service over a real store with one script part. No kernel: a
    cache key is a hash of manifest + script bytes, and `normalize_params`
    takes the spec as an argument (that is what makes it a seam)."""
    svc = make_test_service(tmp_path / "projects", None)
    svc.create_project("demo")
    svc.store.add_part("demo", "flange", "Flange", DEFAULT_MATERIAL,
                       FLANGE_SCRIPT)
    return svc


def record(**kwargs) -> PartRecord:
    base = {"id": "flange", "label": "Flange", "material": DEFAULT_MATERIAL}
    base.update(kwargs)
    return PartRecord(**base)


# ----------------------------------------------------------------- the model


def test_a_record_without_configurations_serializes_as_before():
    data = record(params={"thick": 20.0}).to_manifest()
    assert data == {"id": "flange", "label": "Flange",
                    "material": DEFAULT_MATERIAL, "params": {"thick": 20.0}}
    assert "configs" not in data and "active_config" not in data


def test_to_manifest_writes_the_configuration_keys_only_when_set():
    data = record(configs=THREE_SIZE_CONFIGS, active_config="m").to_manifest()
    assert data["configs"] == THREE_SIZE_CONFIGS
    assert data["active_config"] == "m"
    assert list(data["configs"]) == ["s", "m", "l"]  # family order, not sorted
    # An emptied map and a cleared active configuration write nothing.
    empty = record(configs={}, active_config=None).to_manifest()
    assert "configs" not in empty and "active_config" not in empty


def test_effective_params_layers_the_active_configuration_under_overrides():
    rec = record(params={"thick": 20.0, "outer_d": 210.0},
                 configs=THREE_SIZE_CONFIGS, active_config="l")
    assert rec.effective_params == {
        "outer_d": 210.0,  # the explicit override wins over the config's 200
        "bore_d": 120.0,
        "bc_d": 170.0,
        "thick": 20.0,
    }
    # `params` keeps meaning "explicit overrides" — resolution never bakes in.
    assert rec.params == {"thick": 20.0, "outer_d": 210.0}


def test_effective_params_of_a_base_part_is_its_override_map():
    rec = record(params={"thick": 20.0}, configs=THREE_SIZE_CONFIGS)
    assert rec.effective_params == {"thick": 20.0}
    assert record().effective_params == {}


def test_a_dangling_active_config_resolves_as_base():
    """A merge or a hand edit can leave `active_config` naming a configuration
    the map no longer declares. That resolves as base here (loudly in the
    merge report), never as a KeyError out of a geometry read."""
    rec = record(params={"thick": 20.0}, configs=THREE_SIZE_CONFIGS,
                 active_config="xl")
    assert rec.effective_params == {"thick": 20.0}
    rec_no_map = record(active_config="l")
    assert rec_no_map.effective_params == {}


def test_config_params_is_pure_resolution_and_a_copy():
    rec = record(params={"outer_d": 999.0}, configs=THREE_SIZE_CONFIGS,
                 active_config="s")
    # Pure: the active configuration and the overrides are both ignored.
    assert rec.config_params("l") == {"outer_d": 200.0, "bore_d": 120.0,
                                      "bc_d": 170.0}
    resolved = rec.config_params("l")
    resolved["outer_d"] = 1.0
    assert rec.configs["l"]["params"]["outer_d"] == 200.0
    assert THREE_SIZE_CONFIGS["l"]["params"]["outer_d"] == 200.0


def test_config_params_of_an_unknown_name_raises_key_error():
    """Every tool boundary validates membership first; a KeyError here is a
    programming error, not a user error."""
    with pytest.raises(KeyError):
        record(configs=THREE_SIZE_CONFIGS).config_params("xl")
    with pytest.raises(KeyError):
        record().config_params("s")


def test_an_instance_writes_its_config_only_when_bound():
    assert "config" not in InstanceSpec(id="f1", part="flange").to_manifest()
    bound = InstanceSpec(id="f1", part="flange", config="l").to_manifest()
    assert bound["config"] == "l"


def test_divergence_is_semantic_and_ignores_a_dangling_active_config():
    """`get_part.status.diverged` must never fire on state the geometry does not
    have: a dangling `active_config` resolves as base (so nothing diverges),
    while an override of a parameter the configuration DOES set diverges under
    that parameter's name."""
    dangling = record(params={"thick": 20.0}, configs=THREE_SIZE_CONFIGS,
                      active_config="xl")
    assert _divergence(dangling) == (False, [])
    assert _divergence(record(params={"thick": 20.0})) == (False, [])
    overridden = record(params={"outer_d": 210.0}, configs=THREE_SIZE_CONFIGS,
                        active_config="l")   # the config sets outer_d = 200
    assert _divergence(overridden) == (True, ["outer_d"])
    equal = record(params={"outer_d": 200.0}, configs=THREE_SIZE_CONFIGS,
                   active_config="l")
    assert _divergence(equal) == (False, [])


# ----------------------------------------------------------------- the store


def test_a_manifest_without_configurations_round_trips_byte_identically(service):
    """AC8: a project authored before configurations existed must survive the
    write paths unchanged — no new keys, nothing dropped."""
    store = service.store
    store.update_part_entry("demo", "flange", params={"thick": 20.0})
    store.set_instances("demo", [InstanceSpec(id="f1", part="flange")])
    before = json.dumps(store.manifest("demo"), sort_keys=True)

    store.update_part_entry("demo", "flange", params={"thick": 20.0})
    store.set_instances("demo", store.instances("demo"))
    assert json.dumps(store.manifest("demo"), sort_keys=True) == before


def test_the_store_reads_and_writes_the_configuration_fields(service):
    store = service.store
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS,
                            active_config="m")
    rec = store.get_part("demo", "flange")
    assert rec.configs == THREE_SIZE_CONFIGS
    assert rec.active_config == "m"
    assert list(rec.configs) == ["s", "m", "l"]
    assert rec.effective_params == THREE_SIZE_CONFIGS["m"]["params"]


def test_configurations_survive_a_params_write(service):
    """`set_params` edits the entry in place; a family must not be collateral
    damage of an override."""
    store = service.store
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS,
                            active_config="l")
    store.update_part_entry("demo", "flange", params={"thick": 20.0})
    rec = store.get_part("demo", "flange")
    assert rec.configs == THREE_SIZE_CONFIGS
    assert rec.active_config == "l"
    assert rec.params == {"thick": 20.0}


def test_an_empty_map_pops_the_key_and_none_clears_the_active_config(service):
    store = service.store
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS,
                            active_config="l")
    store.update_part_entry("demo", "flange", active_config=None)
    entry = next(p for p in store.manifest("demo")["parts"]
                 if p["id"] == "flange")
    assert "active_config" not in entry and entry["configs"]
    store.update_part_entry("demo", "flange", configs={})
    entry = next(p for p in store.manifest("demo")["parts"]
                 if p["id"] == "flange")
    assert "configs" not in entry
    # Omitting the keywords changes neither field.
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS,
                            active_config="s")
    store.update_part_entry("demo", "flange", label="Flange II")
    rec = store.get_part("demo", "flange")
    assert rec.configs == THREE_SIZE_CONFIGS and rec.active_config == "s"


def test_an_instance_config_survives_a_read_all_write_all_round_trip(service):
    """`tools_mates` and the gizmo drag both read every instance and write
    every instance back: a field the dataclass does not carry is destroyed by
    the next mate edit."""
    store = service.store
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS)
    store.set_instances("demo", [InstanceSpec(id="f1", part="flange",
                                              config="l")])
    instances = store.instances("demo")
    assert instances[0].config == "l"
    store.set_instances("demo", instances)
    assert store.instances("demo")[0].config == "l"
    entry = store.manifest("demo")["assembly"]["instances"][0]
    assert entry["config"] == "l"


def test_an_instance_cannot_bind_an_undeclared_configuration(service):
    store = service.store
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS)
    with pytest.raises(ValidationError) as exc:
        store.set_instances("demo", [InstanceSpec(id="f1", part="flange",
                                                  config="xl")])
    assert exc.value.details["declared"] == ["l", "m", "s"]
    assert "xl" in exc.value.message
    # ...and the refusal happened before the write.
    assert store.manifest("demo")["assembly"]["instances"] == []


def test_an_instance_of_an_unconfigured_part_cannot_bind_one(service):
    store = service.store
    with pytest.raises(ValidationError) as exc:
        store.set_instances("demo", [InstanceSpec(id="f1", part="flange",
                                                  config="l")])
    assert exc.value.details["declared"] == []


def test_a_reference_part_instance_cannot_bind_a_configuration(service):
    store = service.store
    store.add_part("demo", "vendor", "Vendor", DEFAULT_MATERIAL, "",
                   kind="reference", source="imports/bracket.step")
    with pytest.raises(ValidationError) as exc:
        store.set_instances("demo", [InstanceSpec(id="v1", part="vendor",
                                                  config="l")])
    assert "cannot bind a configuration" in exc.value.message


def test_an_unbound_instance_is_still_written_without_the_key(service):
    store = service.store
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS)
    store.set_instances("demo", [InstanceSpec(id="f1", part="flange")])
    assert "config" not in store.manifest("demo")["assembly"]["instances"][0]
    assert store.instances("demo")[0].config is None


def test_a_hand_edited_null_configs_reads_as_an_empty_map(service):
    """`get_part` and `get_project` must agree: a hand edit (or a merge) that
    leaves `"configs": null` in project.json is a part with no family, not a
    `None` the UI has to special-case."""
    path = service.store.path_of("demo") / "project.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entry = next(p for p in manifest["parts"] if p["id"] == "flange")
    entry["configs"] = None
    entry["active_config"] = None
    path.write_text(json.dumps(manifest), encoding="utf-8")

    row = next(p for p in service.get_project("demo")["parts"]
               if p["id"] == "flange")
    assert row["configs"] == {} and row["active_config"] is None
    assert service.store.get_part("demo", "flange").configs is None


# ------------------------------------------------------------- normalization


def test_normalize_params_coerces_the_declared_python_type(service):
    out = service.normalize_params(SPEC, {"n": 3.0, "width": 40})
    assert out == {"n": 3, "width": 40.0}
    assert isinstance(out["n"], int) and not isinstance(out["n"], bool)
    assert isinstance(out["width"], float)


def test_normalize_params_canonicalizes_an_enum_to_the_declared_choice(service):
    """`{"count": 3}` and `{"count": 3.0}` must not be two configurations (and
    two cache keys) for one geometry."""
    assert service.normalize_params(SPEC, {"count": 3.0}) == {"count": 3}
    assert isinstance(service.normalize_params(SPEC, {"count": 3.0})["count"],
                      int)
    assert service.normalize_params(SPEC, {"grade": "wide"}) == {"grade": "wide"}
    with pytest.raises(ValidationError):
        service.normalize_params(SPEC, {"grade": "narrow"})


def test_normalize_params_refuses_unknown_names_before_coercing_anything(service):
    with pytest.raises(ValidationError) as exc:
        service.normalize_params(SPEC, {"widht": 40.0, "n": 3})
    assert exc.value.details["unknown"] == ["widht"]
    assert exc.value.details["known"] == ["count", "grade", "n", "width"]


def test_normalize_params_does_not_range_check(service):
    """Ranges are the validator's job (`validate_configurations` refuses an
    out-of-range configuration); the normalizer only canonicalizes types, the
    way `set_params` does — the worker clamps a raw override with a warning."""
    assert service.normalize_params(SPEC, {"width": 4000.0}) == {"width": 4000.0}
    with pytest.raises(ValidationError):
        service.normalize_params(SPEC, {"width": "wide"})


def test_normalize_params_of_nothing_is_nothing(service):
    assert service.normalize_params(SPEC, {}) == {}


# ---------------------------------------------------------------- cache keys


def test_the_key_of_an_active_configuration_is_the_key_of_its_params(service):
    """Config-awareness is `record.effective_params` and nothing else: an
    active configuration and a hand-written override map of the same values
    are one cache entry (AC5)."""
    store = service.store
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS,
                            active_config="l")
    configured = store.get_part("demo", "flange")
    plain = dataclasses.replace(
        configured, params=dict(THREE_SIZE_CONFIGS["l"]["params"]),
        configs=None, active_config=None)
    assert service._cache_key_for("demo", plain) == \
        service._cache_key_for("demo", configured)

    # ...and a different member is a different entry.
    store.update_part_entry("demo", "flange", active_config="s")
    assert service._cache_key_for("demo", store.get_part("demo", "flange")) != \
        service._cache_key_for("demo", configured)


def test_a_part_without_configurations_keeps_the_pinned_cache_key(service):
    """Nothing new enters the hashed payload, so every on-disk cache entry
    written before PRD-012 stays valid."""
    rec = service.store.get_part("demo", "flange")
    payload = json.dumps(
        {
            "content": service._content_signature("demo", rec),
            "params": {},
            "density": service.material_density("demo", rec.material),
            "tolerance": MESH_TOLERANCE,
            "format": "acm1",
        },
        sort_keys=True,
    )
    expected = hashlib.sha256(payload.encode()).hexdigest()[:32]
    assert service._cache_key_for("demo", rec) == expected
    # A declared family nobody activated changes no key either.
    service.store.update_part_entry("demo", "flange",
                                    configs=THREE_SIZE_CONFIGS)
    assert service._cache_key_for(
        "demo", service.store.get_part("demo", "flange")) == expected


# ------------------------------------------------------------ derived records


def test_record_for_returns_the_stored_record_or_a_pure_derived_one(service):
    store = service.store
    store.update_part_entry("demo", "flange", params={"thick": 20.0},
                            configs=THREE_SIZE_CONFIGS, active_config="m")

    stored = service._record_for("demo", "flange")
    assert stored.params == {"thick": 20.0} and stored.active_config == "m"

    derived = service._record_for("demo", "flange", "l")
    assert derived.params == THREE_SIZE_CONFIGS["l"]["params"]
    assert derived.active_config is None       # pure: no working-state layer
    assert derived.effective_params == THREE_SIZE_CONFIGS["l"]["params"]
    assert derived.configs == THREE_SIZE_CONFIGS   # the family travels along
    assert derived.id == "flange" and derived.material == stored.material
    # ...and the stored record is untouched by the derivation.
    assert store.get_part("demo", "flange").params == {"thick": 20.0}


def test_record_for_refuses_an_undeclared_name_and_a_reference_part(service):
    store = service.store
    store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS)
    with pytest.raises(ValidationError) as exc:
        service._record_for("demo", "flange", "xl")
    assert "xl" in exc.value.message
    assert exc.value.details["declared"] == ["l", "m", "s"]

    store.add_part("demo", "vendor", "Vendor", DEFAULT_MATERIAL, "",
                   kind="reference", source="imports/bracket.step")
    with pytest.raises(ValidationError) as exc:
        service._record_for("demo", "vendor", "m")
    assert "reference part" in exc.value.message


# ------------------------------------------------------- the build path (S2)


def _counting(service, monkeypatch) -> dict:
    """Count kernel requests by method (the `tests/test_specs_api.py` pattern)."""
    calls: dict = {}
    original = service.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        calls[method] = calls.get(method, 0) + 1
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", counting)
    return calls


#: A script that refuses to build above 20 mm — the deterministic failure a
#: configuration can carry (an out-of-range value would be clamped by the
#: worker with a warning and build fine).
FRAGILE_SCRIPT = '''\
from build123d import *

PARAMS = {
    "size": {"default": 10.0, "min": 1.0, "max": 50.0, "unit": "mm",
             "description": "cube edge"},
}

def build(p):
    if p.size > 20:
        raise ValueError("size above 20 mm is not buildable")
    return Box(p.size, p.size, p.size)
'''

#: One buildable member, one that raises — same script, same part.
FRAGILE_CONFIGS = {
    "ok": {"params": {"size": 10.0}, "label": "Buildable"},
    "bad": {"params": {"size": 30.0}, "label": "Refuses"},
}


def _drain(q: queue.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


@pytest.mark.timeout(600)
class TestFlangeFamily:
    """One three-member flange family, built for real.

    The template project (script + the family, no builds) is made once per
    class and cloned per test, so every test mutates its own manifest and its
    own cache directory while the cache entries the kernel produces are still
    only paid for once per test.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def family_projects(cls, kernel, tmp_path_factory):
        projects = tmp_path_factory.mktemp("configs_projects")
        svc = make_test_service(projects, kernel)
        svc.create_project("demo")
        svc.store.add_part("demo", "flange", "Flange", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
        svc.store.update_part_entry("demo", "flange",
                                    configs=THREE_SIZE_CONFIGS)
        # The same script with no family: the "unchanged without
        # configurations" half of the state assertions (and a free build — the
        # cache key is the script's content, so it shares flange's base entry).
        svc.store.add_part("demo", "plate", "Plate", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
        # A configured part with one member that cannot build: the failure path.
        svc.store.add_part("demo", "fragile", "Fragile", DEFAULT_MATERIAL,
                           FRAGILE_SCRIPT)
        svc.store.update_part_entry("demo", "fragile", configs=FRAGILE_CONFIGS)
        return projects

    @pytest.fixture
    def demo(self, kernel, tmp_path, family_projects):
        return clone_test_service(family_projects, tmp_path / "projects",
                                  kernel)

    # ------------------------------------------------ _ensure_config_built

    def test_every_configuration_builds_its_own_geometry(self, demo):
        """AC1/FR4: three declared sizes, three distinct built shapes — each
        resolved purely, none of them touching the working state."""
        masses = {}
        keys = {}
        for name in ("s", "m", "l"):
            result = demo._ensure_config_built("demo", "flange", name)
            assert result["ok"], result.get("error")
            masses[name] = result["metrics"]["mass_g"]
            keys[name] = result["cache_key"]
            assert (demo.store.cache_dir("demo") / f"{keys[name]}.acm").is_file()
        assert masses["s"] < masses["m"] < masses["l"]
        assert len(set(keys.values())) == 3
        # The working state was never activated by a config build.
        assert demo.store.get_part("demo", "flange").active_config is None
        assert demo.store.get_part("demo", "flange").params == {}

    def test_a_repeated_config_build_is_a_silent_memo_hit(self, demo):
        """Decision 4: a hit publishes nothing. Two instances of one part
        bound to different configurations would otherwise republish
        `rebuild_finished` on alternate `get_assembly` calls and drive the
        browser's refresh loop forever."""
        first = demo._ensure_config_built("demo", "flange", "m")
        assert first["ok"]
        events = demo.bus.subscribe()
        second = demo._ensure_config_built("demo", "flange", "m")
        assert second == first
        assert _drain(events) == []

    def test_a_config_build_never_writes_the_two_tuple_status(self, demo):
        """`get_project.parts[].state`, `get_part.status` and the tree badge
        keep meaning *the working state*, so `_status` stays 2-tuple keyed."""
        demo._ensure_config_built("demo", "flange", "l")
        assert demo._status == {}
        lock_key = demo.store.lock_key("demo")
        assert (lock_key, "flange", "l") in demo._config_status
        # ...and the working-state rebuild still writes exactly the 2-tuple.
        demo._rebuild("demo", "flange")
        assert set(demo._status) == {(lock_key, "flange")}
        assert set(demo._config_status) == {(lock_key, "flange", "l")}
        # Both dicts are swept together.
        demo._forget_status(lock_key)
        assert demo._status == {} and demo._config_status == {}

    def test_deleting_a_part_forgets_its_configuration_builds(self, demo):
        demo._ensure_config_built("demo", "flange", "s")
        demo._ensure_config_built("demo", "flange", "m")
        assert len(demo._config_status) == 2
        demo.delete_part("demo", "flange")
        assert demo._config_status == {}

    def test_rebuild_events_name_the_config_only_for_a_config_build(self, demo):
        """G5: a base rebuild's payload stays byte-identical (the frontend
        keys on `ev.part`); a pure-config build tags every event."""
        events = demo.bus.subscribe()
        demo._ensure_config_built("demo", "flange", "m")
        built = ["rebuild_started", "rebuild_finished"]
        tagged = [e for e in _drain(events) if e["type"].startswith("rebuild_")]
        assert [e["type"] for e in tagged] == built
        assert all(e["config"] == "m" for e in tagged)

        demo._rebuild("demo", "flange")
        base = [e for e in _drain(events) if e["type"].startswith("rebuild_")]
        assert [e["type"] for e in base] == built
        assert all("config" not in e for e in base)
        assert base[0] == {"type": "rebuild_started", "project": "demo",
                           "part": "flange"}

    def test_two_configurations_with_the_same_params_share_one_build(
            self, demo, monkeypatch):
        """AC5: identical override maps are one cache entry — the second
        configuration costs a sidecar read, not a kernel build."""
        first = demo._ensure_config_built("demo", "flange", "m")
        configs = dict(demo.store.get_part("demo", "flange").configs)
        configs["m2"] = {"params": dict(configs["m"]["params"]),
                         "label": "Medium (again)"}
        demo.store.update_part_entry("demo", "flange", configs=configs)

        calls = _counting(demo, monkeypatch)
        second = demo._ensure_config_built("demo", "flange", "m2")
        assert second["ok"]
        assert second["cache_key"] == first["cache_key"]
        assert second["metrics"] == first["metrics"]
        assert "build" not in calls
        assert demo._status == {}      # the sidecar hit writes no badge either

    def test_a_failing_configuration_build_is_memoized_and_stays_quiet(
            self, demo, monkeypatch):
        """A red configuration is a result, not an exception — and it is still
        not the part's badge (`_status` stays empty), it tags its
        `rebuild_failed`, and the second ask costs no kernel call and publishes
        nothing."""
        events = demo.bus.subscribe()
        first = demo._ensure_config_built("demo", "fragile", "bad")
        assert first["ok"] is False
        assert "not buildable" in json.dumps(first["error"])
        failed = [e for e in _drain(events) if e["type"] == "rebuild_failed"]
        assert len(failed) == 1 and failed[0]["config"] == "bad"
        assert demo._status == {}
        memo = demo._config_status[(demo.store.lock_key("demo"), "fragile",
                                    "bad")]
        assert memo["state"] == "error" and memo["metrics"] is None

        calls = _counting(demo, monkeypatch)
        second = demo._ensure_config_built("demo", "fragile", "bad")
        assert second == {"ok": False, "error": first["error"]}
        assert calls == {} and _drain(events) == []
        # ...and the sibling that builds is unaffected.
        assert demo._ensure_config_built("demo", "fragile", "ok")["ok"] is True

    # ------------------------------------------------------------- meshes

    def test_mesh_info_keys_the_mesh_per_configuration(self, demo):
        info = {name: demo.mesh_info("demo", "flange", config=name)
                for name in ("s", "l")}
        assert info["s"]["key"] != info["l"]["key"]
        for name, got in info.items():
            assert got["key"] == demo._ensure_config_built(
                "demo", "flange", name)["cache_key"]
            assert got["path"].name == f"{got['key']}.acm"
            assert got["path"].is_file() and got["lod"] is None
            assert demo.ensure_mesh("demo", "flange", config=name) == \
                got["path"]
        base = demo.mesh_info("demo", "flange")
        assert base["key"] not in {got["key"] for got in info.values()}

    def test_mesh_info_refuses_an_undeclared_configuration(self, demo):
        """Never a silent fall back to the base geometry: a caller asking for
        a size that does not exist must not be handed another one."""
        with pytest.raises(ValidationError):
            demo.mesh_info("demo", "flange", config="xl")
        with pytest.raises(ValidationError) as exc:
            demo.mesh_info("demo", "plate", config="m")
        assert exc.value.details["declared"] == []

    # ------------------------------------------------------------ exports

    def test_the_base_export_is_unchanged(self, demo):
        result = demo.export_part("demo", "flange", "step")
        assert Path(result["path"]).name == "flange.step"
        assert result["size_bytes"] > 500
        assert "config" not in result

    def test_a_configuration_export_names_the_file_and_echoes_the_config(
            self, demo):
        result = demo.export_part("demo", "flange", "step", config="l")
        assert Path(result["path"]).name == "flange_l.step"
        assert result["config"] == "l"
        assert result["size_bytes"] > 500
        assert (demo.store.exports_dir("demo") / "flange_l.step").is_file()
        # Pure resolution: the working state is still base, unexported.
        assert not (demo.store.exports_dir("demo") / "flange.step").exists()

    def test_an_export_of_an_undeclared_configuration_refuses(self, demo):
        with pytest.raises(ValidationError):
            demo.export_part("demo", "flange", "step", config="xl")
        assert list(demo.store.exports_dir("demo").glob("flange*")) == []

    def test_the_export_tool_and_route_forward_the_configuration(self, demo):
        registry = build_registry(demo)
        schema = registry.get("export_part").input_schema
        assert "config" in schema["properties"]
        assert schema["properties"]["config"]["type"] == "string"
        result = registry.call("export_part", {
            "project": "demo", "part_id": "flange", "format": "stl",
            "config": "s"})
        assert Path(result["path"]).name == "flange_s.stl"
        assert result["config"] == "s"

        app = create_app(demo, registry, extra_allowed_hosts={"testserver"})
        client = TestClient(app, base_url="http://127.0.0.1")
        response = client.post(
            "/api/projects/demo/parts/flange/export",
            json={"format": "stl", "config": "m"})
        assert response.status_code == 200, response.text
        assert Path(response.json()["path"]).name == "flange_m.stl"
        assert response.json()["config"] == "m"

    def test_a_non_string_config_from_the_route_is_a_refusal(self, demo):
        """`app.py` forwards `body.get("config")` unvalidated, so an unhashable
        value must be refused by `_record_for` (422) rather than raising a
        TypeError out of the membership test (500)."""
        registry = build_registry(demo)
        app = create_app(demo, registry, extra_allowed_hosts={"testserver"})
        client = TestClient(app, base_url="http://127.0.0.1")
        for bad in ({}, [], 7):
            response = client.post(
                "/api/projects/demo/parts/flange/export",
                json={"format": "stl", "config": bad})
            assert response.status_code == 422, (bad, response.text)
            assert response.json()["error"]["type"] == "ValidationError"
        with pytest.raises(ValidationError):
            demo.mesh_info("demo", "flange", config=["l"])

    # ------------------------------------------------------- exposed state

    def test_get_part_and_get_project_carry_the_configuration_state(self, demo):
        plain = demo.get_part("demo", "plate")
        assert plain["configs"] == {} and plain["active_config"] is None
        assert plain["status"]["diverged"] is False
        assert plain["status"]["diverged_params"] == []

        demo.store.update_part_entry("demo", "flange", active_config="l")
        detail = demo.get_part("demo", "flange")
        assert list(detail["configs"]) == ["s", "m", "l"]   # family order
        assert detail["active_config"] == "l"
        assert detail["status"]["diverged"] is False

        rows = {p["id"]: p for p in demo.get_project("demo")["parts"]}
        assert list(rows["flange"]["configs"]) == ["s", "m", "l"]
        assert rows["flange"]["active_config"] == "l"
        assert rows["plate"]["configs"] == {}
        assert rows["plate"]["active_config"] is None

    def test_an_override_on_top_of_the_active_configuration_diverges(self, demo):
        demo.store.update_part_entry("demo", "flange", active_config="m")
        demo.set_params("demo", "flange", {"thick": 20.0})
        detail = demo.get_part("demo", "flange")
        assert detail["params"] == {"thick": 20.0}      # overrides, as stored
        assert detail["active_config"] == "m"
        assert detail["status"]["diverged"] is True
        assert detail["status"]["diverged_params"] == ["thick"]

    def test_an_override_equal_to_the_configuration_is_not_divergence(
            self, demo):
        """Divergence is semantic: the geometry — and the cache key — are the
        pure configuration's, so the chip must stay off."""
        demo.store.update_part_entry("demo", "flange", active_config="m")
        demo.set_params("demo", "flange",
                        {"outer_d": THREE_SIZE_CONFIGS["m"]["params"]["outer_d"]})
        status = demo.get_part("demo", "flange")["status"]
        assert status["diverged"] is False and status["diverged_params"] == []
        assert demo.mesh_info("demo", "flange")["key"] == \
            demo._ensure_config_built("demo", "flange", "m")["cache_key"]

    def test_clearing_the_override_returns_to_the_configurations_key(self, demo):
        demo.store.update_part_entry("demo", "flange", active_config="m")
        demo.set_params("demo", "flange", {"thick": 20.0})
        diverged_key = demo.mesh_info("demo", "flange")["key"]

        demo.set_params("demo", "flange", {"thick": None})
        detail = demo.get_part("demo", "flange")
        assert detail["status"]["diverged"] is False
        assert detail["status"]["diverged_params"] == []
        pure = demo._ensure_config_built("demo", "flange", "m")["cache_key"]
        assert demo.mesh_info("demo", "flange")["key"] == pure != diverged_key
