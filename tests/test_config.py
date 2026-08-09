import json

from agentcad import config


def test_get_port_allocates_and_persists(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setenv("AGENTCAD_CONFIG", str(cfg_file))

    assert config.get_port() == 8630
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["port"] == 8630


def test_get_port_reads_persisted_value(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setenv("AGENTCAD_CONFIG", str(cfg_file))
    cfg_file.write_text(json.dumps({"port": 4222}), encoding="utf-8")

    assert config.get_port() == 4222


def test_corrupt_config_recovers(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setenv("AGENTCAD_CONFIG", str(cfg_file))
    cfg_file.write_text("{not json", encoding="utf-8")

    assert config.get_port() == 8630
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["port"] == 8630
