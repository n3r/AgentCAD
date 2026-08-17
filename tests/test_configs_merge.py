"""PRD-012 slice 5, the orchestrator half: a dangling selection through a REAL merge.

`tests/test_manifest_merge.py` proves the driver reaches
`parts.<id>.configs.<name>.params.<param>` and that
`manifest_merge.config_problems` names the damage the key-wise merge cannot
see. What it cannot prove is that the damage *reaches a human*, so these tests
drive two real branches through `merge_branch` and read `validation` — the
surface PRD-001 already uses for structural merge damage — rather than
grepping the orchestrator's source. The shape is
`test_packages_index.py::test_a_real_merge_blocks_on_the_package_hybrid`.

The two kinds are deliberately asymmetric (Decision 9): an instance bound to a
configuration the merged part no longer declares resolves to *nothing*, so it
blocks; a part whose `active_config` is gone resolves as base, so it is a
warning that must not block.
"""

from __future__ import annotations

import shutil

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
    # Two real kernel builds and four snapshots per test, against a global
    # 120 s default that a loaded machine can trip.
    pytest.mark.timeout(600),
]

pytestmark = _GIT

BOX_V2_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 2)"
)
assert BOX_V2_SCRIPT != BOX_SCRIPT


@pytest.fixture(autouse=True)
def _reset_context():
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def rig(kernel, tmp_path):
    """A service with history, branches and merges live — `make_test_service`
    nulls `bus.on_publish`, and without those snapshots there is no branch."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "merges", None) is not None
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT}
    )
    return service, registry


def _edit_manifest(service, edit) -> None:
    """Write the manifest and snapshot it, the way a tool call would.

    Slice 5 owns the merge, not the tool pack, so the family is authored
    directly — the merge reads project.json, not the writer that produced it.
    """
    manifest = service.store.manifest("demo")
    edit(manifest)
    service.store.save_manifest("demo", manifest)
    service.bus.publish({"type": "project_changed", "project": "demo"})


def _declare_big(manifest) -> None:
    manifest["parts"][0]["configs"] = {
        "big": {"params": {"size": 20.0}, "label": "Big"}
    }


def _drop_big(manifest) -> None:
    manifest["parts"][0].pop("configs", None)


def _fork_and_shrink_the_family(service, registry, prepare) -> dict:
    """master declares `big` (plus whatever `prepare` selects); `feat` removes
    the configuration; master edits a script so the merge is real work and not
    a fast-forward. Returns `merge_branch`'s payload."""
    _edit_manifest(service, prepare)
    service.branches.create("demo", "feat")

    locks.set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    _edit_manifest(service, _drop_big)

    locks.set_client_id("agent_b")
    service.branches.switch("demo", "master")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
    )
    return registry.call("merge_branch", {"project": "demo", "source": "feat"})


def test_a_real_merge_blocks_on_an_instance_bound_to_a_removed_configuration(rig):
    service, registry = rig

    def prepare(manifest):
        _declare_big(manifest)
        manifest["assembly"]["instances"] = [
            {"id": "box_1", "part": "box", "position": [0.0, 0.0, 0.0],
             "rotation_deg": [0.0, 0.0, 0.0], "config": "big"}
        ]

    out = _fork_and_shrink_the_family(service, registry, prepare)

    assert "error" in out, out
    assert out["error"]["type"] == "validation_error"
    validation = (out["error"]["details"] or {})["validation"]
    assert validation["ok"] is False and validation["blocked"] is True
    row = next(r for r in validation["integrity"]
               if r["kind"] == "dangling_instance_config")
    assert row["instance"] == "box_1"
    assert row["part"] == "box" and row["config"] == "big"
    assert "no longer declares" in row["message"]
    # and the merge did not land: master still has its own head
    assert service.branches.current("demo") == "master"
    assert service.store.manifest("demo")["parts"][0]["configs"]["big"]


def test_a_real_merge_only_warns_when_the_removed_configuration_was_active(rig):
    service, registry = rig

    def prepare(manifest):
        _declare_big(manifest)
        manifest["parts"][0]["active_config"] = "big"

    out = _fork_and_shrink_the_family(service, registry, prepare)

    assert "error" not in out, out
    validation = out["validation"]
    assert validation["ok"] is True
    assert validation["integrity"] == []
    assert any("active_config 'big'" in w for w in validation["warnings"]), (
        validation["warnings"]
    )
    # the selection survived the merge and resolves as base, exactly as
    # Decision 3 says — the warning is the only thing that changed.
    entry = service.store.manifest("demo")["parts"][0]
    assert entry["active_config"] == "big" and entry.get("configs") in ({}, None)
