"""Integration tests over every bundled example project.

Parametrized over examples/*/project.json found on disk; each example must:
rebuild all parts (valid, positive volume) at defaults and at every param's
min and max, pass an interference check, and export the assembly as STEP.
"""

from pathlib import Path

import pytest

from agentcad.core.service import AgentCADService, EventBus

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

EXAMPLE_DIRS = sorted(
    child for child in (EXAMPLES_DIR.iterdir() if EXAMPLES_DIR.is_dir() else [])
    if (child / "project.json").is_file()
)

pytestmark = pytest.mark.skipif(
    not EXAMPLE_DIRS, reason="no example projects present yet"
)


@pytest.fixture(scope="module")
def service(kernel, tmp_path_factory):
    return AgentCADService(tmp_path_factory.mktemp("projects"), kernel, EventBus())


@pytest.fixture(scope="module", params=EXAMPLE_DIRS, ids=lambda p: p.name)
def example(request, service, tmp_path_factory):
    # Copy the example into a temp dir first: the tests mutate params and write
    # caches, and we must never touch the committed example on disk.
    import shutil

    src = request.param
    dest = tmp_path_factory.mktemp("ex") / src.name
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".cache", "exports"))
    detail = service.open_project(str(dest))
    return service, detail["name"]


def test_all_parts_build_valid_at_defaults(example):
    service, name = example
    project = service.get_project(name)
    assert project["parts"], f"example {name} has no parts"
    for part in project["parts"]:
        detail = service.get_part(name, part["id"])
        assert detail["status"]["state"] == "ok", (
            f"{name}/{part['id']}: {detail['status']['error']}"
        )
        assert detail["metrics"]["volume_mm3"] > 0
        assert detail["metrics"]["is_valid"] is True
        assert detail["params_spec"], f"{name}/{part['id']} has no PARAMS"
        for pname, spec in detail["params_spec"].items():
            assert spec["min"] is not None, f"{name}/{part['id']}.{pname} missing min"
            assert spec["max"] is not None, f"{name}/{part['id']}.{pname} missing max"
            assert spec["unit"], f"{name}/{part['id']}.{pname} missing unit"
            assert spec["description"], f"{name}/{part['id']}.{pname} missing description"


def test_parts_build_at_param_extremes(example):
    service, name = example
    project = service.get_project(name)
    for part in project["parts"]:
        detail = service.get_part(name, part["id"])
        baseline = dict(part["params"])
        for pname, spec in detail["params_spec"].items():
            for value in (spec["min"], spec["max"]):
                result = service.set_params(name, part["id"], {pname: value})
                assert result["ok"], (
                    f"{name}/{part['id']}.{pname}={value}: {result.get('error')}"
                )
                assert result["metrics"]["volume_mm3"] > 0
            service.set_params(
                name, part["id"], {pname: baseline.get(pname, spec["default"])}
            )


def test_assembly_present_and_interference_clean(example):
    service, name = example
    assembly = service.get_assembly(name)
    assert len(assembly["instances"]) >= 2, f"{name}: assembly needs >= 2 instances"
    assert all(i["state"] == "ok" for i in assembly["instances"])
    result = service.check_interference(name)
    assert result["pairs"] == [], f"{name}: interference {result['pairs']}"


def test_assembly_exports_step(example):
    service, name = example
    result = service.export_assembly(name, "step")
    assert result["size_bytes"] > 1000
