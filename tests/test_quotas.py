"""PRD-006 slice 1 — the layered quota resolver (`agentcad/kernel/quotas.py`).

Quotas are *configuration*, resolved once in the server process and handed to
the kernel client at construction, so every worker (and every respawn of one)
is capped identically. The layering is FR12's: built-in defaults < the
instance config file < `AGENTCAD_QUOTA_*` env < per-caller overrides (the slot
PRD-005's per-tenant limits plug into without a signature change).

These are pure unit tests: no kernel, no subprocess, no OS call.
"""

from __future__ import annotations

import dataclasses

import pytest

from agentcad.kernel.quotas import DEFAULTS, ENV_PREFIX, resolve


def _resolve(overrides=None, **kwargs):
    """`resolve` with both ambient layers switched off unless a test asks."""
    kwargs.setdefault("env", {})
    kwargs.setdefault("config", {})
    return resolve(overrides, **kwargs)


# ------------------------------------------------------------------ defaults

def test_defaults_are_the_measured_table():
    q = _resolve()
    assert (q.memory_mb, q.pids, q.pids_headroom) == (2048, 128, 64)
    assert q.cpu_percent == 400
    assert q.sample_interval_s == 0.25
    assert q.disk_mb == 2048
    # address_space_mb is stored as 0 ("auto") and resolves to 3x memory.
    assert DEFAULTS["address_space_mb"] == 0
    assert q.address_space_mb == 3 * 2048


def test_the_resolved_object_is_frozen():
    """`FrozenInstanceError`, not a bare `Exception`: a typo in the attribute
    name would raise too, and would pass a test that accepts anything."""
    q = _resolve()
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.memory_mb = 1  # type: ignore[misc]


# ------------------------------------------------------------------ layering

def test_env_wins_over_the_config_file():
    q = _resolve(env={f"{ENV_PREFIX}MEMORY_MB": "4096"},
                 config={"quotas": {"memory_mb": 1024}})
    assert q.memory_mb == 4096


def test_config_applies_when_the_env_is_silent():
    q = _resolve(config={"quotas": {"memory_mb": 1024}})
    assert q.memory_mb == 1024
    assert q.address_space_mb == 3 * 1024  # auto follows the resolved memory


def test_overrides_win_over_env_and_config():
    q = _resolve({"pids": 32},
                 env={f"{ENV_PREFIX}PIDS": "64"},
                 config={"quotas": {"pids": 256}})
    assert q.pids == 32


def test_unknown_keys_are_ignored_everywhere():
    q = _resolve({"nonsense": 1},
                 env={f"{ENV_PREFIX}NONSENSE": "1", "PATH": "/usr/bin"},
                 config={"quotas": {"nonsense": 1}})
    assert q.memory_mb == DEFAULTS["memory_mb"]


def test_an_empty_env_value_is_not_a_setting():
    """`AGENTCAD_QUOTA_MEMORY_MB=` (a compose file with a blank value) must
    read as "unset", not as a parse error or a zero cap."""
    q = _resolve(env={f"{ENV_PREFIX}MEMORY_MB": "   "})
    assert q.memory_mb == DEFAULTS["memory_mb"]


def test_resolve_with_no_arguments_reads_the_real_layers(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"quotas": {"disk_mb": 77}}', encoding="utf-8")
    monkeypatch.setenv("AGENTCAD_CONFIG", str(cfg))
    monkeypatch.setenv(f"{ENV_PREFIX}PIDS", "16")
    q = resolve()
    assert (q.disk_mb, q.pids) == (77, 16)


# --------------------------------------------------------------- disabling

def test_cpu_percent_off_is_none():
    assert _resolve({"cpu_percent": "off"}).cpu_percent is None
    assert _resolve({"cpu_percent": 0}).cpu_percent is None


def test_address_space_zero_is_auto_but_off_is_disabled():
    assert _resolve({"address_space_mb": 0, "memory_mb": 512}).address_space_mb == 1536
    assert _resolve({"address_space_mb": "off"}).address_space_mb == 0
    assert _resolve({"address_space_mb": 4096}).address_space_mb == 4096


def test_off_disables_a_plain_knob():
    assert _resolve({"pids": "off"}).pids == 0


# ----------------------------------------------------------------- refusals

def test_a_non_numeric_value_names_the_key_and_the_layer():
    with pytest.raises(ValueError) as exc_info:
        _resolve({"memory_mb": "lots"})
    message = str(exc_info.value)
    assert "memory_mb" in message and "overrides" in message

    with pytest.raises(ValueError) as exc_info:
        _resolve(env={f"{ENV_PREFIX}MEMORY_MB": "lots"})
    assert f"{ENV_PREFIX}MEMORY_MB" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        _resolve(config={"quotas": {"memory_mb": "lots"}})
    message = str(exc_info.value)
    assert "memory_mb" in message and "config" in message


def test_a_negative_cap_is_refused_rather_than_silently_breaching():
    with pytest.raises(ValueError) as exc_info:
        _resolve({"memory_mb": -1})
    assert "memory_mb" in str(exc_info.value)


def test_a_boolean_is_not_a_number():
    """`{"quotas": {"cpu_percent": true}}` in JSON is a mistake, and `True` is
    an `int` in Python — accepting it would cap CPU at 1%."""
    with pytest.raises(ValueError):
        _resolve({"cpu_percent": True})


def test_a_fractional_count_is_refused_but_the_interval_is_a_float():
    with pytest.raises(ValueError):
        _resolve({"pids": 12.5})
    assert _resolve({"sample_interval_s": "0.5"}).sample_interval_s == 0.5


def test_a_malformed_quotas_block_is_ignored():
    assert _resolve(config={"quotas": "2048"}).memory_mb == DEFAULTS["memory_mb"]


# -------------------------------------------------------------------- limits

def test_limits_reports_every_cap_but_not_the_sample_interval():
    """`limits()` is what health publishes: the caps a script can breach. The
    supervisor's sampling interval is an implementation detail of the
    enforcer, not a limit anyone is subject to."""
    limits = _resolve().limits()
    assert "sample_interval_s" not in limits
    assert set(limits) == set(DEFAULTS) - {"sample_interval_s"}
    assert limits["memory_mb"] == 2048
    assert limits["address_space_mb"] == 6144  # the resolved value, not 0


def test_enforcement_is_off_when_no_tier_can_apply_anything():
    """Honesty (design spec, Decision 8): a backend with no tier behind the
    caps reports `off` and still publishes the limits, so health never names
    a cap nothing is watching."""
    from agentcad.kernel.quotas import enforcement

    q = _resolve()
    assert enforcement(q, ["rlimit", "supervisor"]) == {
        "status": "active", "mechanism": "rlimit+supervisor",
        "limits": q.limits()}
    off = enforcement(q, [])
    assert off["status"] == "off" and off["mechanism"] is None
    assert off["limits"] == q.limits()


def test_limits_is_json_serializable_with_a_disabled_knob():
    import json

    limits = _resolve({"cpu_percent": "off"}).limits()
    assert limits["cpu_percent"] is None
    assert json.loads(json.dumps(limits))["cpu_percent"] is None
