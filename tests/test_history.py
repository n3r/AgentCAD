"""Git-backed project history: automatic snapshots on every persistent
mutation (via the EventBus ``on_publish`` hook), the ``project_history`` /
``project_restore`` tools, and the cache-consistency invariant — after a
restore, geometry rebuilds from the restored content, never from stale
in-memory state.

The whole module skips when git is not on PATH; the git-missing degradation
path is tested explicitly with a monkeypatched ``shutil.which``.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from agentcad.core import history as history_mod
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not found on PATH"
)

# Same PARAMS, doubled height: default size 10 -> volume 2000 instead of 1000.
BOX_V2_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 2)"
)
assert BOX_V2_SCRIPT != BOX_SCRIPT


@pytest.fixture
def stack(kernel, tmp_path):
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    return service, registry, bus


@pytest.fixture
def demo(stack):
    service, registry, bus = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    created = registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT}
    )
    assert "error" not in created
    return service, registry, bus


def _history(registry, limit=20):
    payload = registry.call("project_history", {"project": "demo", "limit": limit})
    assert "error" not in payload, payload
    return payload


def _ls_files(project_path):
    return subprocess.run(
        ["git", "--git-dir", str(project_path / ".history"), "ls-files"],
        capture_output=True, text=True, cwd=project_path, check=True,
    ).stdout.splitlines()


# --------------------------------------------- 1. mutations append snapshots


def test_mutations_append_history_newest_first(demo):
    _service, registry, _bus = demo
    edited = registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
    )
    assert edited["ok"] is True

    payload = _history(registry)
    assert payload["available"] is True
    entries = payload["history"]
    assert len(entries) >= 2
    assert all(e["id"] and e["message"] and e["ts"] for e in entries)
    assert len({e["id"] for e in entries}) == len(entries)
    # Newest first: ISO timestamps must be non-increasing down the list.
    stamps = [e["ts"] for e in entries]
    assert stamps == sorted(stamps, reverse=True)


# ------------------------------- 2. restore reverts content AND geometry


def test_restore_reverts_script_and_rebuilds_old_geometry(demo):
    service, registry, _bus = demo
    assert service.get_metrics("demo", "box")["volume_mm3"] == pytest.approx(
        1000.0, rel=1e-6
    )
    assert registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
    )["ok"] is True
    assert service.get_metrics("demo", "box")["volume_mm3"] == pytest.approx(
        2000.0, rel=1e-6
    )

    oldest = _history(registry)["history"][-1]["id"]  # state after create_part
    restored = registry.call(
        "project_restore", {"project": "demo", "commit": oldest}
    )
    assert "error" not in restored, restored
    assert restored["restored"] == oldest

    # Script text on disk reverted...
    assert service.store.read_script("demo", "box") == BOX_SCRIPT
    # ...and the cache-consistency invariant: metrics re-derive from the
    # restored content, not from the stale in-memory status of the edit.
    part = service.get_part("demo", "box")
    assert part["status"]["state"] == "ok"
    assert part["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)


# ------------------------------------------ 3. restore keeps history linear


def test_restore_appends_a_restore_commit(demo):
    _service, registry, _bus = demo
    registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
    )
    before = _history(registry)["history"]
    oldest = before[-1]["id"]

    restored = registry.call(
        "project_restore", {"project": "demo", "commit": oldest}
    )
    assert "error" not in restored

    after = restored["history"]  # fresh history comes back with the result
    assert len(after) == len(before) + 1
    assert after[0]["message"] == f"restore {oldest[:8]}"
    # Linear: every previous entry is still there, in order (no rewind).
    assert [e["id"] for e in after[1:]] == [e["id"] for e in before]


# ------------------------------------- 4. pack mutations snapshot too


def test_pack_mutation_snapshots_via_bus_hook(demo):
    _service, registry, _bus = demo
    before = len(_history(registry)["history"])
    result = registry.call(
        "set_part_pmi",
        {
            "project": "demo",
            "part_id": "box",
            "pmi": {"datums": [{"id": "A", "face": "top"}]},
        },
    )
    assert "error" not in result, result
    after = _history(registry)["history"]
    assert len(after) == before + 1


# ------------------------------------------- 5. derived data stays untracked


def test_cache_and_exports_are_not_tracked(demo):
    service, registry, _bus = demo
    service.ensure_mesh("demo", "box")  # writes .cache/<key>.acm (+ sidecar)
    service.export_part("demo", "box", "step")  # writes exports/box.step
    # A mutation after the derived files exist: add -A must not pick them up.
    assert registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_SCRIPT + "\n# v2\n"},
    )["ok"] is True

    tracked = _ls_files(service.store.path_of("demo"))
    assert "project.json" in tracked
    assert "parts/box.py" in tracked
    assert not [f for f in tracked if f.startswith((".cache/", "exports/"))]


# ------------------------------------------------ 6. git-missing degradation


def test_git_missing_degrades_gracefully(demo, monkeypatch):
    service, registry, _bus = demo
    monkeypatch.setattr(history_mod.shutil, "which", lambda _cmd: None)
    fresh = history_mod.ProjectHistory()  # fresh instance: no cached git path
    path = service.store.path_of("demo")

    assert fresh.available() is False
    assert fresh.snapshot(path, "x") is None
    assert fresh.log(path) == []
    monkeypatch.setattr(service, "history", fresh)

    payload = registry.call("project_history", {"project": "demo"})
    assert payload == {
        "available": False, "history": [], "note": "git not found on PATH"
    }
    denied = registry.call(
        "project_restore", {"project": "demo", "commit": "0" * 40}
    )
    assert denied["error"]["type"] == "validation_error"

    # Service mutations still work without git — snapshots just no-op.
    ok = registry.call(
        "set_params", {"project": "demo", "part_id": "box", "values": {"size": 12.0}}
    )
    assert ok["ok"] is True


# ---------------------------------- 7. restore does not double-snapshot


def test_restore_makes_exactly_one_history_entry(demo):
    _service, registry, _bus = demo
    registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
    )
    before = _history(registry)["history"]
    oldest = before[-1]["id"]

    registry.call("project_restore", {"project": "demo", "commit": oldest})

    after = _history(registry)["history"]
    # Exactly ONE new entry: the internal restore commit. The project_changed
    # published by project_restore must be suppressed by the reentrancy flag
    # (and would find a clean tree anyway).
    assert len(after) == len(before) + 1
    restores = [e for e in after if e["message"].startswith("restore ")]
    assert len(restores) == 1


# -------------------------------------------- 8. bad commit ids are rejected


def test_restore_unknown_commit_is_a_validation_error(demo):
    _service, registry, _bus = demo
    bad = registry.call(
        "project_restore", {"project": "demo", "commit": "deadbeef"}
    )
    assert bad["error"]["type"] == "validation_error"
    weird = registry.call(
        "project_restore", {"project": "demo", "commit": "--help"}
    )
    assert weird["error"]["type"] == "validation_error"
