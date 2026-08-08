import pytest

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

EXPECTED_TOOLS = {
    "list_projects", "create_project", "open_project", "get_project",
    "create_part", "get_part", "update_part_script", "set_params",
    "delete_part", "get_metrics", "get_mesh_summary", "export_part",
    "get_assembly", "set_assembly", "check_interference", "export_assembly",
    "part_template",
}


@pytest.fixture
def registry(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    return build_registry(service)


def test_seventeen_tools_with_valid_schemas(registry):
    tools = registry.list()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert len(tools) == 17
    for tool in tools:
        assert tool.description
        assert tool.input_schema["type"] == "object"
        assert isinstance(tool.input_schema["properties"], dict)
        assert isinstance(tool.input_schema["required"], list)


def test_tool_flow_create_and_break(registry):
    assert registry.call("create_project", {"name": "demo"})["name"] == "demo"
    part = registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT}
    )
    assert part["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)

    broken = registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": "PARAMS = {}\ndef build(p):\n    return None\n"},
    )
    assert broken["ok"] is False
    assert broken["error"]["type"] == "contract_error"
    assert "hint" in broken

    metrics = registry.call("get_metrics", {"project": "demo", "part_id": "box"})
    assert metrics["error"]["type"] == "contract_error"  # propagated as payload


def test_error_payloads_not_exceptions(registry):
    result = registry.call("get_project", {"project": "ghost"})
    assert result["error"]["type"] == "notfound_error"

    result = registry.call("create_project", {})
    assert result["error"]["type"] == "invalid_arguments"

    result = registry.call("create_project", {"name": 42})
    assert result["error"]["type"] == "invalid_arguments"

    result = registry.call("no_such_tool", {})
    assert result["error"]["type"] == "unknown_tool"


def test_part_template_contains_contract(registry):
    result = registry.call("part_template", {})
    assert "PARAMS" in result["template"]
    assert "CONTRACT" in result["cheatsheet"].upper()
