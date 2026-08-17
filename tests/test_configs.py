"""PRD-012 slice 1 — the configuration model, the store, resolution.

Everything here is manifest and dataclass work: a configuration never reaches
the kernel (the service resolves it into an override map on the way *into* a
request), so these tests run without a geometry worker — the service is built
with ``kernel=None`` on purpose, and any call that would need one would fail
loudly rather than pass on a mock.
"""

import dataclasses
import hashlib
import json

import pytest

from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.model import InstanceSpec, PartRecord, ValidationError
from agentcad.core.service import MESH_TOLERANCE

from .conftest import FLANGE_SCRIPT, THREE_SIZE_CONFIGS, make_test_service

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
